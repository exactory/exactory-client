"""Durable remote-write intent, confirmed step receipts and honest unknown outcomes.

The owner lock precedes short Store transactions. No network request runs inside
a SQLite transaction. A lost response is reconciled with remote reads; absence of
a response never authorizes repeating an uncertain remote mutation.
"""

from .errors import ResearchError
from .finishing import retry_finishing_write
from .evidence import digest
from .operations import immutable_record, prepared_mutation, prepared_task_mutation, text


# The messages of a write of a remote intent that the store refused: before a request is sent, after its answer
# arrived, after a read of the service, for a cleared step, and for the receipt.
REMOTE_CLAIM_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. The step of this remote '
    'operation was not started. No request of this step was sent. The intent keeps every step that was confirmed '
    'before. Run the same command again. It continues from the confirmed steps.')
REMOTE_CLAIM_STORE_CHANGED_MSG = (
    'The research store changed between the check of this remote step and its start, so the step was not started. No '
    'request of this step was sent. The intent keeps every step that was confirmed before. Run the same command '
    'again. It checks the store again and continues from the confirmed steps.')
REMOTE_ANSWER_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. The answer of the remote '
    'service to this step was not recorded. The request was sent once, and the service answered. details.response '
    'holds the members of the answer that the step needs, and no record holds them. The intent keeps the step as '
    'pending. While it is pending, exactory submit and exactory verify do not take their managed path. Without '
    '--managed-only, they then send the request directly, a second time. Do not run them again for this request, and '
    'do not send the request another way. For a deposit, run exactory-draft reconcile with the intent ID in '
    'details.intent_id. For a submission, run exactory reconcile with that intent ID. Each of them reads the service '
    'and records the step. A verdict has no such read: give details.response to the user.')
REMOTE_READ_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. What a read of the '
    'service showed about the pending step was not recorded. This command sent no write to the service. The intent '
    'keeps the step as pending. Run the same command again. It reads the service again and records the step.')
REMOTE_DISCARD_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. The release of a pending '
    'step of this remote operation was not recorded. This command sent no request of this step. The intent keeps the '
    'step as pending. For a deposit, run the same command again. It checks the state of the step again before it '
    'sends a request. For a submission or a verdict, report this refusal to the user.')
REMOTE_RECEIPT_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. The receipt of this '
    'remote operation was not recorded. Every step of the operation is confirmed and recorded in its intent, and no '
    'request is pending. Run the same command again. It records the receipt from the confirmed steps and sends no step '
    'a second time.')

_DEPOSITION_ANSWER_MEMBERS = ("id", "record_id", "conceptrecid", "state", "submitted", "doi", "conceptdoi",
                              "links.html", "links.record_html", "metadata.prereserve_doi.doi")
# The members of a service answer that its step needs, by intent kind and step; a dotted name is a member of a member.
# The refusal of an answer that the store did not take carries these members and never the raw answer, which can
# identify an account; a kind or a step that is not here carries none. Two steps are resolved by a read of the
# service: a deposition read (zenodo.reconcile_pending, for every step but the preview) and the task read of a
# submission (submission.continue_submission).
ANSWER_MEMBERS = {
    ("deposit", "create"): _DEPOSITION_ANSWER_MEMBERS,
    ("deposit", "upload"): ("key", "checksum", "size"),
    ("deposit", "metadata"): _DEPOSITION_ANSWER_MEMBERS,
    ("deposit", "preview"): ("id",),
    ("deposit", "publish"): _DEPOSITION_ANSWER_MEMBERS,
    ("deposit", "deposition_read"): _DEPOSITION_ANSWER_MEMBERS,
    ("submit", "submit"): ("id", "verificationId", "status", "doi", "webUrl", "createdAt", "supersedes",
                           "supersededBy"),
    ("submit", "task_read"): ("verificationId", "doi"),
    ("verdict", "verdict"): ("id", "status", "stance", "supersedesVerdictId", "createdAt"),
}


def _select_answer_members(answer, members):
    """The members of a JSON answer that members names, in the structure of the answer; absent members are left out."""
    selected = {}
    for path in members:
        *parents, name = path.split(".")
        value = answer
        for part in parents:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, dict) and name in value:
            target = selected
            for part in parents:
                target = target.setdefault(part, {})
            target[name] = value[name]
    return selected


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
    """Change the intent. With an expected revision, which a caller gives when a check of the whole store stands
    behind the step, the commit is the compare-and-swap on that revision. Without one the commit depends on the intent
    alone. The request ID ends with the revision at which the change was prepared, in both forms."""
    def prepare(records, value):
        current = records["remote_intent"][identifier]
        record = transform(current)
        return [("remote_intent", identifier, record)], record
    payload = {"id": identifier, "data": data}
    if expected_revision is not None:
        return prepared_mutation(store, "remote." + action, payload, prepare, expected_revision=expected_revision,
                                 request_id=identifier + ":" + action + ":" + str(expected_revision))["result"]
    preparation = store._read_scoped_preparation()
    return prepared_task_mutation(store, "remote." + action, payload, prepare, _snapshot=preparation,
                                  request_id=identifier + ":" + action + ":" + str(preparation.revision))["result"]


def remote_step(store, identifier, name, request, perform, *, expected_revision=None):
    current = get_intent(store, identifier)
    if name in current["responses"]:
        return current["responses"][name]["response"]
    if current["pending"] is not None:
        raise ResearchError("remote_reconciliation_required", "A prior remote mutation has an unknown outcome; reconcile it before another write",
                            {"request_id": identifier, "pending": current["pending"]})
    def claim():
        return _change(store, identifier, "claim", {"name": name, "request": request},
                       lambda r: dict(r, pending={"name": name, "request": request}, status="in_flight"),
                       expected_revision=expected_revision)
    if expected_revision is None:
        retry_finishing_write(claim, REMOTE_CLAIM_STORE_BUSY_MSG)
    else:
        # The caller checked the whole store at this revision, so a later revision refuses the claim at once.
        try:
            claim()
        except ResearchError as error:
            if error.code == "stale_revision":
                raise ResearchError("stale_revision", REMOTE_CLAIM_STORE_CHANGED_MSG, {"cause": error.as_dict()}) from error
            if error.code == "store_busy":
                raise ResearchError("store_busy", REMOTE_CLAIM_STORE_BUSY_MSG, {"cause": error.as_dict()}) from error
            raise
    # Exceptions and process death intentionally leave the claimed operation.
    try:
        response = perform()
    except ResearchError as error:
        if error.code in ("gate_override_public_write", "gate_override_condition_unmet"):
            # The local transport guard refuses before sending anything. Keep
            # that fact rather than forcing remote reconciliation of no write.
            discard_step(store, identifier, name, {"kind": "local_authority_refusal", "code": error.code})
        raise
    return resolve_step(store, identifier, name, response, {"kind": "direct_response"},
                        answer_members=ANSWER_MEMBERS.get((current["kind"], name.partition(":")[0]), ()),
                        busy_msg=REMOTE_ANSWER_STORE_BUSY_MSG)


def _read_pending_step(record, name):
    pending = record["pending"]
    if pending is None or pending["name"] != name:
        raise ResearchError("remote_reconciliation_conflict", "Remote observation does not match the pending step")
    return pending


def resolve_step(store, identifier, name, response, observation, *, answer_members, busy_msg):
    """Only a concrete transport response or checked remote observation resolves it. busy_msg is the message of the
    refusal after the wait, and answer_members the members of response that the step needs (ANSWER_MEMBERS)."""
    def transform(record):
        pending = _read_pending_step(record, name)
        replies = dict(record["responses"])
        replies[name] = {"request": pending["request"], "response": response, "observation": observation}
        return dict(record, pending=None, responses=replies, status="confirmed")
    # The answer exists only in this process, so a refusal after the wait carries the members that the step needs.
    kept = {"intent_id": identifier, "step": name}
    if answer_members:
        kept["response"] = _select_answer_members(response, answer_members)
    retry_finishing_write(lambda: _change(store, identifier, "resolve",
        {"name": name, "response": response, "observation": observation}, transform), busy_msg, kept=kept)
    return response


def discard_step(store, identifier, name, observation):
    """Clear a claimed step so that the caller sends it itself.

    A discard invents no response. The caller establishes that the step reaches
    the same remote state when it is sent again: checked remote reads hold no
    trace of the request, the service documents the request as an action that
    has no effect when it is repeated, or the local authority guard refused
    before sending the request. The intent keeps that observation."""
    def transform(record):
        _read_pending_step(record, name)
        return dict(record, pending=None, status="confirmed" if record["responses"] else "prepared",
                    discarded=record.get("discarded", []) + [{"name": name, "observation": observation}])
    retry_finishing_write(lambda: _change(store, identifier, "discard", {"name": name, "observation": observation}, transform),
                          REMOTE_DISCARD_STORE_BUSY_MSG)


def finish_intent(store, identifier, kind, receipt, *, workspace=None):
    return retry_finishing_write(lambda: _finish_intent_once(store, identifier, kind, receipt, workspace=workspace),
                                 REMOTE_RECEIPT_STORE_BUSY_MSG)


def _finish_intent_once(store, identifier, kind, receipt, *, workspace=None):
    def prepare(records, value):
        current = records["remote_intent"][identifier]
        if current["pending"] is not None:
            raise ResearchError("remote_reconciliation_required", "An uncertain remote operation cannot be marked complete")
        changes = [immutable_record(records, kind, identifier, receipt),
                   ("remote_intent", identifier, dict(current, status="complete"))]
        if workspace is not None:
            changes.append(("workspace", "deposit", workspace))
        return changes, receipt
    return prepared_task_mutation(store, "remote.finish", {"id": identifier, "kind": kind, "receipt": receipt, "workspace": workspace},
                                  prepare, request_id=identifier + ":finish:" + digest(receipt))["result"]
