"""Deliver actual native completion as immutable evidence in its common store."""

import copy

from . import schema as s
from .attempt_delivery import deliver_snapshot
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
        return deliver_snapshot(controller, state, common, "native_closure", identity, value, result,
                                audit_state, "Accepted native completion audit failed")
