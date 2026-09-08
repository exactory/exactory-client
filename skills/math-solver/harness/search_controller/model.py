"""Pure replay of validated controller events. This module performs no I/O."""

import copy

from . import schema as s
from .admission import EVENT_HANDLERS as ADMISSION_HANDLERS, obligation_record
from .proof import EVENT_HANDLERS as PROOF_HANDLERS
from .scheduler import EVENT_HANDLERS as SCHEDULER_HANDLERS, initial_control
from .adoption import EVENT_HANDLERS as ADOPTION_HANDLERS
from .execution_state import EVENT_HANDLERS as EXECUTION_HANDLERS
from .computation import EVENT_HANDLERS as COMPUTATION_HANDLERS
from .research import EVENT_HANDLERS as RESEARCH_HANDLERS
from .discovery import record as record_discovery


EVENT_HANDLERS = dict(ADMISSION_HANDLERS, **PROOF_HANDLERS, **SCHEDULER_HANDLERS, **ADOPTION_HANDLERS, **EXECUTION_HANDLERS, **COMPUTATION_HANDLERS, **RESEARCH_HANDLERS)
EVENT_HANDLERS["discovery_recorded"] = record_discovery


def service_operation(state, payload):
    """Replay only typed facts emitted by the filesystem service boundary."""
    s.closed(payload, "command target spec_digest operations effects")
    s.require(not state["service"]["native_intents"] or payload["command"] in {"reconcile", "pause", "focus", "hook-stop", "audit", "render"},
              "Native intent requires reconciliation", "recovery_required")
    allowed = {
        "init": {"discovery_recorded"}, "render": set(), "propose": {"proposal_recorded"},
        "adopt": {"legacy_imported", "legacy_import_version_recorded", "adoption_allowance_recorded"},
        "review": {"review_recorded"}, "admit": {"proposal_admitted"},
        "checkpoint": {"checkpoint_recorded", "node_facts_recorded"}, "accept": {"result_accepted", "node_facts_recorded"},
        "complete": {"objective_completed"}, "audit": {"evidence_invalidated"},
        "retreat": {"node_retreated"}, "replan": {"replan_recorded"},
        "focus": {"control_recorded", "discovery_recorded"}, "pause": {"control_recorded"},
        "resume": {"control_recorded", "discovery_recorded"}, "hook-stop": {"control_recorded", "node_facts_recorded"},
        "begin": {"move_reserved"},
        "amend-computation": {"computation_amended"}, "amend-foundation": {"foundation_amended"}, "interpret": {"run_interpreted"},
        "run": {"run_reserved"}, "execution-launch": {"run_launched"},
        "legacy-journal": {"journal_intended"},
        "legacy-native": {"native_intended"},
        "reconcile": {"journal_acknowledged", "node_facts_recorded", "run_finished", "native_acknowledged"},
    }
    s.choice(payload["command"], allowed)
    s.optional_text(payload["target"])
    s.digest_string(payload["spec_digest"])
    s.require(len(s.records(payload["operations"])) <= 256, "Too many internal operations")
    for operation in payload["operations"]:
        s.closed(operation, "kind payload")
        s.choice(operation["kind"], allowed[payload["command"]])
        EVENT_HANDLERS[operation["kind"]](state, operation["payload"])
    for effect in s.records(payload["effects"]):
        s.closed(effect, "kind slug snapshot_digest")
        s.choice(effect["kind"], {"initialize_workspace", "preserve_manual_views"})
        if effect["kind"] == "initialize_workspace":
            s.slug(effect["slug"])
        else:
            s.require(effect["slug"] == ".", "Manual root view uses the registered root")
        s.digest_string(effect["snapshot_digest"])
        state["service"]["effects"].append(copy.deepcopy(effect))


EVENT_HANDLERS["service_operation"] = service_operation


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
        "service": {"effects": [], "imports": {}, "import_versions": [], "journal_receipts": {}, "adoption_allowances": [],
                    "moves": {}, "legacy_intents": {}, "native_intents": {}, "native_receipts": {}, "discovery_intents": {},
                    "computation_amendments": {}, "run_interpretations": {}, "foundation_amendments": {}, "foundation_selection": {}},
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
