"""Pure replay of validated controller events. This module performs no I/O."""

import copy

from . import schema as s
from .admission import EVENT_HANDLERS as ADMISSION_HANDLERS, obligation_record
from .proof import EVENT_HANDLERS as PROOF_HANDLERS
from .scheduler import EVENT_HANDLERS as SCHEDULER_HANDLERS, initial_control


EVENT_HANDLERS = dict(ADMISSION_HANDLERS, **PROOF_HANDLERS, **SCHEDULER_HANDLERS)


def initial_state(contract, objective_id):
    s.validate_contract(contract)
    s.text(objective_id)
    root = contract["root_obligation"]
    kinds = ["obligation", "route", "node", "proposal", "review", "account", "checkpoint", "acceptance", "run"]
    return {
        "schema_version": 1, "objective_id": objective_id,
        "contract": copy.deepcopy(contract), "contract_digest": s.digest(contract), "revision": 0,
        "obligations": {root: obligation_record(root, contract["original_claim"])},
        "routes": {}, "nodes": {}, "proposals": {}, "reviews": {}, "accounts": {},
        "checkpoints": {}, "acceptances": {}, "runs": {},
        "control": initial_control(),
        "totals": {"used_moves": 0, "used_runs": 0, "reserved_moves": 0, "reserved_runs": 0,
                   "historical_usage": "known"},
        "next_ids": {kind: 2 if kind == "obligation" else 1 for kind in kinds},
        "proof_status": "open", "execution_status": "needs_replan", "publication_status": [],
        "requests": {},
    }


def apply_event(state, event):
    s.canonical_bytes(event)
    s.closed(event, "sequence request_id kind payload")
    s.integer(event["sequence"], 1)
    s.text(event["request_id"])
    s.text(event["kind"])
    s.require(isinstance(event["payload"], dict), "Event payload must be a record")
    s.require(state["contract_digest"] == s.digest(state["contract"]), "Frozen contract was mutated", "contract_changed")
    root = state["obligations"][state["contract"]["root_obligation"]]
    s.require(root["claim"] == state["contract"]["original_claim"], "Frozen root claim was mutated", "contract_changed")
    s.require(event["sequence"] == state["revision"] + 1, "Events must have contiguous sequence numbers", "revision_conflict")
    s.require(event["request_id"] not in state["requests"], "Event request ID is already recorded", "request_conflict")
    s.require(event["kind"] in EVENT_HANDLERS, "Unknown or unavailable event kind", "unknown_event")
    result = copy.deepcopy(state)
    EVENT_HANDLERS[event["kind"]](result, copy.deepcopy(event["payload"]))
    result["revision"] = event["sequence"]
    result["requests"][event["request_id"]] = s.digest(event)
    return result


def replay(document):
    s.canonical_bytes(document)
    s.closed(document, "schema_version objective_id contract events")
    s.integer(document["schema_version"], 1, 1)
    s.records(document["events"])
    state = initial_state(document["contract"], document["objective_id"])
    for event in document["events"]:
        state = apply_event(state, event)
    return state
