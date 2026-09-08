"""Prepare domain mutations outside short, nonreentrant Store callbacks.

The domain receipt permits replay before validating today's source selection.
Store remains authoritative for original-result replay, request conflicts and
CAS. Preparation may verify/create immutable artifacts but cannot publish state.
Callbacks only apply the prepared record patch and receipt through Transaction.
"""

import json
from datetime import date, datetime

from .errors import ResearchError
from .storage import _canonical, _text


def fields(value, required, optional=(), *, code="invalid_input"):
    if not isinstance(value, dict) or not set(required) <= value.keys() or not value.keys() <= set(required) | set(optional):
        raise ResearchError(code, "Expected fields: " + ", ".join(sorted(required)) +
                            ("; optional: " + ", ".join(sorted(optional)) if optional else ""))


def text(value, name, *, code="invalid_input"):
    _text(value, name, code)
    return value


def strings(value, name, *, nonempty=False, code="invalid_input"):
    if not isinstance(value, list) or nonempty and not value:
        raise ResearchError(code, name + " must be an array" + (" with at least one entry" if nonempty else ""))
    for item in value:
        text(item, name, code=code)
    if len(set(value)) != len(value):
        raise ResearchError(code, name + " cannot contain duplicates")
    return value


def profile_name(value):
    if value not in ("research", "verification"):
        raise ResearchError("invalid_profile", "Profile must be research or verification")
    return value


def iso_date(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (TypeError, ValueError) as error:
        raise ResearchError("invalid_input", "Expected an ISO calendar date") from error


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed
    except (TypeError, AttributeError, ValueError) as error:
        raise ResearchError("invalid_input", "Expected a timestamp with an explicit timezone") from error


def prepared_mutation(store, operation, payload, prepare, *, expected_revision, request_id):
    """prepare(records, payload) returns ([(kind, key, value), ...], result)."""
    text(request_id, "Request ID")
    if not isinstance(payload, dict):
        raise ResearchError("invalid_input", "A domain mutation requires a JSON object")
    payload = json.loads(_canonical(payload))
    snapshot = store.snapshot()
    receipt = snapshot["records"].get("literature_operation", {}).get(request_id)
    if receipt is not None:
        # The Store checks the original operation and payload before invoking a
        # callback, including when the caller supplies the historical revision.
        return store.mutate(operation, payload, lambda tx: None,
                            expected_revision=expected_revision, request_id=request_id)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ResearchError("invalid_input", "Expected revision must be a nonnegative integer")
    if snapshot["revision"] != expected_revision:
        raise ResearchError("stale_revision", "Research state changed; read the current revision before a new mutation",
                            {"expected_revision": expected_revision, "revision": snapshot["revision"]})
    changes, result = prepare(snapshot["records"], payload)

    def apply(transaction):
        for kind, key, value in changes:
            transaction.put(kind, key, value)
        transaction.put("literature_operation", request_id, {"operation": operation})
        return result

    return store.mutate(operation, payload, apply, expected_revision=expected_revision, request_id=request_id)


def immutable_record(records, kind, key, value):
    text(key, "Record ID")
    existing = records.get(kind, {}).get(key)
    if existing is not None and existing != value:
        raise ResearchError("record_conflict", "An evidence record ID cannot replace historical content",
                            {"kind": kind, "id": key})
    return kind, key, value
