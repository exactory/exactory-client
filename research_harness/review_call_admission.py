"""Transactional admission and launch checks shared by reviewer call writers."""

from .errors import ResearchError
from .review_budget import check_admission


def admit_review_call(transaction, operation, invocation_id, route_id):
    """Check the allowance, then shared store and route capacity, before a start."""
    from .review_task_execution import _TransactionRecords, _active_legacy, _get, _policy
    records = _TransactionRecords(transaction)
    check_admission(records, operation, invocation_id)
    policy = _policy(records)
    route = _get(records, "review_route", route_id)
    if route.get("adapter") not in policy["covered_adapters"]:
        return
    memberships = records.get("review_pool_membership", {})
    active = [row for row in records.get("review_reservation", {}).values()
              if row["id"] in memberships and not row["released"]]
    active.extend(_active_legacy(records, policy))
    route_active = [row for row in active if row["route_id"] == route_id]
    if len(active) >= policy["store_limit"] or len(route_active) >= policy["route_limits"].get(
            route_id, policy["store_limit"]):
        raise ResearchError("review_capacity_wait", "Shared reviewer capacity is occupied; reconcile its reservations",
                            {"reservations": [row["id"] for row in active], "scope": "store"})


def check_launch(records, transition, lifecycle, *, token=None, read_identity=None):
    """Check a launch claim, worker start or child registration in its write transaction.

    The writer retains authority and lock verification. Child registrations supply
    the process identity reader so live identity checks stay inside the transaction.
    Launches consume the existing admission without checking budget or capacity again.
    """
    from .review_task_execution import _check_references, _get
    if transition == "review.worker_claim":
        if lifecycle["released"] or lifecycle["launch"] is not None or lifecycle["state"] != "reserved":
            raise ResearchError("review_invocation_pending", "Reconcile the original worker instead of launching twice")
        _check_references(records, _get(records, "review_execution_plan", lifecycle["plan_id"]))
    elif transition == "review.worker_running":
        if lifecycle["released"] or lifecycle["state"] != "reserved" or not lifecycle["launch"]["authorized"]:
            raise ResearchError("review_worker_reconciliation_required", "The worker's launch was not authorized")
        _check_references(records, _get(records, "review_execution_plan", lifecycle["plan_id"]))
    elif transition == "review.call_child":
        if (lifecycle["released"] or lifecycle["token"] != token
                or read_identity(lifecycle["owner"]["pid"]) != lifecycle["owner"]):
            raise ResearchError("review_worker_reconciliation_required", "The call's owner is no longer live")
    elif transition == "review.worker_child":
        if (lifecycle["released"] or lifecycle["state"] != "running" or lifecycle["token"] != token
                or read_identity(lifecycle["worker"]["pid"]) != lifecycle["worker"]):
            raise ResearchError("review_worker_reconciliation_required", "The transport child's worker is no longer live")
