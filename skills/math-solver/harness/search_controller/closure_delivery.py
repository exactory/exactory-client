"""Deliver actual native completion as immutable evidence in its common store."""

import copy
from types import SimpleNamespace

from research_harness.errors import ResearchError
from research_harness.operations import immutable_record, prepared_mutation
from research_harness.storage import Store as CommonStore

from . import schema as s
from .errors import SearchError
from .evidence import audit_state
from .reviewer import common_root, PREFIX
from .storage import ContentSink


def deliver_closure(controller, spec, expected_revision, request_id):
    """Publish only the currently completed objective, never caller-supplied bytes."""
    s.closed(spec, "")
    s.integer(expected_revision)
    s.text(request_id)
    with controller.store._writer_lock():
        state = controller.status()
        if expected_revision != state["revision"]:
            raise SearchError("revision_conflict", "Expected revision differs",
                              {"expected": expected_revision, "actual": state["revision"]})
        closure = state["control"]["closure"]
        s.require(closure is not None and state["proof_status"] in {"proved", "disproved"}
                  and state["execution_status"] == "resolved" and state["control"]["state_error"] is None,
                  "Complete the native objective before delivering its closure", "native_completion_required")
        s.require(closure["review"]["reviewer"]["attestation_id"].startswith(PREFIX),
                  "Closure delivery requires an observed native review", "review_assignment_required")
        identity = s.digest(closure)
        content = ContentSink(controller.store)
        s.require(content.get_blob(identity) == closure, "Native closure differs from its pinned bytes", "digest_mismatch")
        common = common_root(controller.root, state)
        value = {"schema_version": 1, "objective_id": state["objective_id"], "attack_root": str(controller.root),
                 "contract": copy.deepcopy(state["contract"]), "closure": copy.deepcopy(closure),
                 "provenance": controller.provenance("deliver-closure")}
        result = {"objective_id": state["objective_id"], "attack_root": str(controller.root),
                  "native_closure_digest": identity, "workspace": str(common)}
        try:
            store = CommonStore(common)

            def prepare(records, payload):
                # Audit the exact common snapshot that the mutation's revision
                # check protects, without holding a read lock across its commit.
                snapshot = SimpleNamespace(root=common, snapshot=lambda: {"records": records})
                audit_content = ContentSink(controller.store)
                failures = audit_state(controller.root, state, audit_content, common_guard=snapshot, fresh=True)
                s.require(not failures, "Accepted native completion audit failed", "audit_failed")
                return [immutable_record(records, "native_closure", identity, payload)], result

            while True:
                snapshot = store.snapshot()
                # An exact repeat publishes neither another record nor a receipt.
                existing = snapshot["records"].get("native_closure", {}).get(identity)
                if existing is not None:
                    prepare(snapshot["records"], value)
                    return result
                try:
                    prepared_mutation(store, "native_math.deliver_closure", value, prepare,
                                      expected_revision=snapshot["revision"],
                                      request_id="native-closure:" + identity)
                    return result
                except ResearchError as error:
                    if error.code != "stale_revision":
                        raise
        except ResearchError as error:
            raise SearchError(error.code, error.message, error.details) from error
