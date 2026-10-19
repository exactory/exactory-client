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


def attempt_summary(controller, state):
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
                                   if node["admission"] is not None), default=0)},
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
        summary = attempt_summary(controller, state)
        identity = s.digest(summary)
        common = common_root(controller.root, state)
        value = dict(summary, provenance=controller.provenance("deliver-attempt"))
        result = {"objective_id": state["objective_id"], "attack_root": str(controller.root),
                  "native_attempt_digest": identity, "workspace": str(common), "state": summary["state"]}
        try:
            store = CommonStore(common)

            def prepare(records, payload):
                # Audit the exact common snapshot protected by the mutation's revision.
                snapshot = SimpleNamespace(root=common, snapshot=lambda: {"records": records})
                content = ContentSink(controller.store)
                failures = audit_state(controller.root, state, content, common_guard=snapshot, fresh=True)
                s.require(not failures, "Accepted native attempt audit failed", "audit_failed")
                return [immutable_record(records, "native_attempt", identity, payload)], result

            while True:
                snapshot = store.snapshot()
                # An identical summary re-audits without adding a record or receipt.
                existing = snapshot["records"].get("native_attempt", {}).get(identity)
                if existing is not None:
                    prepare(snapshot["records"], value)
                    return result
                try:
                    prepared_mutation(store, "native_math.deliver_attempt", value, prepare,
                                      expected_revision=snapshot["revision"],
                                      request_id="native-attempt:" + identity)
                    return result
                except ResearchError as error:
                    if error.code != "stale_revision":
                        raise
        except ResearchError as error:
            raise SearchError(error.code, error.message, error.details) from error
