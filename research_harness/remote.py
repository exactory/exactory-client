"""Durable remote-write intent, confirmed step receipts and honest unknown outcomes.

The owner lock precedes short Store transactions. No network request runs inside
a SQLite transaction. A lost response is reconciled with remote reads; absence of
a response never authorizes repeating an uncertain remote mutation.
"""

from .errors import ResearchError
from .evidence import digest
from .operations import immutable_record, prepared_mutation, text


def get_intent(store, identifier):
    record = store.snapshot()["records"].get("remote_intent", {}).get(identifier)
    if record is None:
        raise ResearchError("remote_intent_missing", "No saved remote intent has this request ID")
    return record


def _compute_request_fingerprint(kind, binding, deduplication_key):
    """What makes two remote requests one durable intent: the whole request, or
    the caller's deduplication key when equivalent requests name one operation."""
    if deduplication_key is not None:
        return digest({"kind": kind, "key": deduplication_key})
    return digest({"kind": kind, "binding": binding})


def find_intent(records, kind, binding, deduplication_key=None):
    """The saved intent that begin_intent reuses for this exact request, or None."""
    fingerprint = _compute_request_fingerprint(kind, binding, deduplication_key)
    return next((r for r in records.get("remote_intent", {}).values() if r["fingerprint"] == fingerprint), None)


def begin_intent(store, kind, binding, *, expected_revision, request_id, deduplication_key=None):
    payload = {"kind": kind, "binding": binding}
    if deduplication_key is not None:
        payload["deduplication_key"] = deduplication_key
    def prepare(records, value):
        text(kind, "Remote operation")
        existing = find_intent(records, kind, value["binding"], deduplication_key)
        if existing is not None:
            return [], {"id": existing["id"]}
        record = {"id": request_id, "kind": kind, "binding": binding,
                  "fingerprint": _compute_request_fingerprint(kind, value["binding"], deduplication_key),
                  "responses": {}, "pending": None, "status": "prepared", "created_revision": expected_revision}
        if deduplication_key is not None:
            record["deduplication_key"] = deduplication_key
        return [immutable_record(records, "remote_intent", request_id, record)], {"id": request_id}
    receipt = prepared_mutation(store, "remote.begin", payload, prepare,
                                expected_revision=expected_revision, request_id=request_id)
    return get_intent(store, receipt["result"]["id"])


def _change(store, identifier, action, data, transform, *, expected_revision=None):
    expected = store.revision if expected_revision is None else expected_revision
    def prepare(records, value):
        current = records["remote_intent"][identifier]
        record = transform(current)
        return [("remote_intent", identifier, record)], record
    return prepared_mutation(store, "remote." + action, {"id": identifier, "data": data}, prepare,
                             expected_revision=expected, request_id=identifier + ":" + action + ":" + str(expected))["result"]


def remote_step(store, identifier, name, request, perform, *, expected_revision=None):
    current = get_intent(store, identifier)
    if name in current["responses"]:
        return current["responses"][name]["response"]
    if current["pending"] is not None:
        raise ResearchError("remote_reconciliation_required", "A prior remote mutation has an unknown outcome; reconcile it before another write",
                            {"request_id": identifier, "pending": current["pending"]})
    _change(store, identifier, "claim", {"name": name, "request": request},
            lambda r: dict(r, pending={"name": name, "request": request}, status="in_flight"),
            expected_revision=expected_revision)
    # Exceptions and process death intentionally leave the claimed operation.
    response = perform()
    return resolve_step(store, identifier, name, response, {"kind": "direct_response"})


def _read_pending_step(record, name):
    pending = record["pending"]
    if pending is None or pending["name"] != name:
        raise ResearchError("remote_reconciliation_conflict", "Remote observation does not match the pending step")
    return pending


def resolve_step(store, identifier, name, response, observation):
    """Only a concrete transport response or checked remote observation resolves it."""
    def transform(record):
        pending = _read_pending_step(record, name)
        replies = dict(record["responses"])
        replies[name] = {"request": pending["request"], "response": response, "observation": observation}
        return dict(record, pending=None, responses=replies, status="confirmed")
    _change(store, identifier, "resolve", {"name": name, "response": response, "observation": observation}, transform)
    return response


def discard_step(store, identifier, name, observation):
    """Clear a claimed step so that the caller sends it itself.

    A discard invents no response. The caller establishes that the step reaches
    the same remote state when it is sent again: checked remote reads hold no
    trace of the request, or the service documents the request as an action that
    has no effect when it is repeated. The intent keeps that observation."""
    def transform(record):
        _read_pending_step(record, name)
        return dict(record, pending=None, status="confirmed" if record["responses"] else "prepared",
                    discarded=record.get("discarded", []) + [{"name": name, "observation": observation}])
    _change(store, identifier, "discard", {"name": name, "observation": observation}, transform)


def finish_intent(store, identifier, kind, receipt, *, workspace=None):
    expected = store.revision
    def prepare(records, value):
        current = records["remote_intent"][identifier]
        if current["pending"] is not None:
            raise ResearchError("remote_reconciliation_required", "An uncertain remote operation cannot be marked complete")
        changes = [immutable_record(records, kind, identifier, receipt),
                   ("remote_intent", identifier, dict(current, status="complete"))]
        if workspace is not None:
            changes.append(("workspace", "deposit", workspace))
        return changes, receipt
    return prepared_mutation(store, "remote.finish", {"id": identifier, "kind": kind, "receipt": receipt, "workspace": workspace}, prepare,
                             expected_revision=expected, request_id=identifier + ":finish:" + digest(receipt))["result"]
