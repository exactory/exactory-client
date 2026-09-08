"""Pure eligibility checks for explicit unstarted-investigation deferral."""

from . import schema as s
from .admission import reference


def validate_deferrals(state, node_ids):
    s.strings(node_ids)
    control = state["control"]
    s.require(not control["pending_moves"] and not any(
        run["status"] in {"reserved", "launched", "indeterminate"}
        for run in state["runs"].values()),
        "Reconcile pending execution before changing deferrals", "execution_pending")
    records = list(state["service"]["moves"].values()) + list(state["runs"].values())
    for nid in node_ids:
        node = reference(state["nodes"], nid, "deferred investigation")
        s.require(node["proposal_id"] is not None and node["category"] != "standalone",
                  "Deferral requires reviewed main-work admission", "admission_required")
        s.require(node["status"] == "admitted" and control["active_node_id"] != nid,
                  "Only unstarted investigations may be deferred", "deferral_unavailable")
        s.require(not any(record["node_id"] == nid for record in records),
                  "An investigation with execution history cannot be deferred",
                  "deferral_unavailable")
        facts = control["node_facts"].get(nid, {})
        s.require(not facts.get("result_action") and not facts.get("cashout_action"),
                  "Result and cash-out work cannot be deferred", "deferral_unavailable")
    return list(node_ids)
