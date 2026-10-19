"""Deliver native attempt findings as immutable evidence in their common store."""

import copy
from types import SimpleNamespace

from research_harness.errors import ResearchError
from research_harness.operations import immutable_record, prepared_mutation
from research_harness.storage import Store as CommonStore

from . import schema as s
from .errors import SearchError
from .evidence import audit_state
from .reviewer import common_root
from .storage import ContentSink


def build_attempt_summary(controller, state):
    """Project recorded findings and accounted usage without reading local files."""
    openness = state.get("openness")
    closure = state["control"]["closure"]
    finishes = {}
    for receipt in sorted(state["service"]["native_receipts"].values(),
                          key=lambda item: (item["recorded_revision"], item["id"])):
        if receipt["command"] == "finish" and receipt["outcome"] == "succeeded":
            finishes[receipt["node_id"]] = {key: receipt[key] for key in ("id", "post_digest")}
    summary = {
        "schema_version": 1, "objective_id": state["objective_id"], "attack_root": str(controller.root),
        "contract": state["contract"],
        "state": {"proof_status": state["proof_status"], "execution_status": state["execution_status"],
                  "openness": ({key: openness[key] for key in ("status", "outcome", "statement_digest")}
                               if openness is not None else None),
                  "closure_digest": s.digest(closure) if closure is not None else None},
        "obligations": [{key: item[key] for key in ("id", "status", "claim")}
                        for _, item in sorted(state["obligations"].items())],
        "checkpoints": [dict({key: item[key] for key in ("id", "kind", "claim", "verification_status")},
                             origin_kind=item["origin"]["kind"])
                        for _, item in sorted(state["checkpoints"].items())],
        "acceptances": [dict({key: item[key] for key in ("id", "checkpoint_id", "obligation_id", "outcome",
                                                        "classification", "standard", "status")},
                             claims_openness=item.get("claims_openness", False),
                             evidence_digests=item["audit"]["evidence_digests"])
                        for _, item in sorted(state["acceptances"].items())],
        "retreats": state["control"]["retreats"],
        "failed_strategies": [item for _, item in sorted(state["control"]["strategy_refresh"]["failures"].items())],
        "nodes": [{"id": identity, "status": item["status"],
                   "cashout_action": state["control"]["node_facts"].get(identity, {}).get("cashout_action"),
                   "finish_receipt": finishes.get(identity)}
                  for identity, item in sorted(state["nodes"].items())],
        "totals": {"moves": state["totals"]["used_moves"], "runs": state["totals"]["used_runs"],
                   # Native records do not account elapsed duration. Preserve that gap.
                   "elapsed_seconds": None,
                   "workers": max((node["admission"]["limits"]["workers"] for node in state["nodes"].values()
                                   if node["admission"] is not None), default=0),
                   "timeout_seconds": max((node["admission"]["limits"]["timeout_seconds"]
                                           for node in state["nodes"].values() if node["admission"] is not None), default=0),
                   "historical_usage": state["totals"]["historical_usage"]},
    }
    return copy.deepcopy(summary)


def deliver_attempt(controller, spec, expected_revision, request_id):
    """Publish the current attempt, including unresolved and historical findings."""
    s.closed(spec, "")
    s.integer(expected_revision)
    s.text(request_id)
    with controller.store._writer_lock():
        state = controller.status()
        if expected_revision != state["revision"]:
            raise SearchError("revision_conflict", "Expected revision differs",
                              {"expected": expected_revision, "actual": state["revision"]})
        s.require(state["control"]["state_error"] is None,
                  "Reconcile pending native state before delivering its attempt", "native_state_pending")
        summary = build_attempt_summary(controller, state)
        attempt_digest = s.digest(summary)
        common = common_root(controller.root, state)
        value = dict(summary, provenance=controller.provenance("deliver-attempt"))
        result = {"objective_id": state["objective_id"], "attack_root": str(controller.root),
                  "native_attempt_digest": attempt_digest, "workspace": str(common), "state": summary["state"]}
        return deliver_snapshot(controller, state, common, "native_attempt", attempt_digest, value, result,
                                audit_state, "Accepted native attempt audit failed")


def deliver_snapshot(controller, state, common, kind, identity, value, result, audit, audit_message):
    """Audit a revision-protected common snapshot and retry competing common writes."""
    def audit_snapshot(records):
        snapshot = SimpleNamespace(root=common, snapshot=lambda: {"records": records})
        failures = audit(controller.root, state, ContentSink(controller.store), common_guard=snapshot, fresh=True)
        if failures:
            raise SearchError("audit_failed", audit_message, failures)

    key = {name: value[name] for name in ("attack_root", "objective_id")}
    pointer_id = s.digest(key)
    try:
        store = CommonStore(common)
        while True:
            snapshot = store.snapshot()
            existing = snapshot["records"].get(kind, {}).get(identity)
            current = snapshot["records"].get("native_attempt_current", {}).get(pointer_id)
            pointer_current = current is not None and current["native_attempt_digest"] == identity
            # Exact current repeats freshly audit without adding a record or receipt.
            if existing is not None and (kind != "native_attempt" or pointer_current):
                audit_snapshot(snapshot["records"])
                expected = (dict(value, recorded_revision=existing["recorded_revision"])
                            if kind == "native_attempt" and "recorded_revision" in existing else value)
                immutable_record(snapshot["records"], kind, identity, expected)
                return result

            def prepare(records, payload):
                audit_snapshot(records)
                record = payload
                if kind == "native_attempt":
                    revision = snapshot["revision"] + 1 if existing is None else existing.get("recorded_revision")
                    if revision is not None:
                        record = dict(payload, recorded_revision=revision)
                checked = immutable_record(records, kind, identity, record)
                changes = [checked] if existing is None else []
                if kind == "native_attempt":
                    pointer = dict(key, native_attempt_digest=identity, recorded_revision=snapshot["revision"] + 1)
                    changes.append(("native_attempt_current", pointer_id, pointer))
                return changes, result

            label = kind[len("native_"):]
            request_id = "native-" + label + ":" + identity
            if existing is not None:
                # A prior content receipt cannot replay a later pointer change.
                request_id += ":current:" + str(snapshot["revision"] + 1)
            try:
                prepared_mutation(store, "native_math.deliver_" + label, value, prepare,
                                  expected_revision=snapshot["revision"], request_id=request_id)
                return result
            except ResearchError as error:
                if error.code != "stale_revision":
                    raise
    except ResearchError as error:
        raise SearchError(error.code, error.message, error.details) from error
