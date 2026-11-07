"""Stop policy shared by the research store and the math controller.

Only explicit, evidenced codes leave the default decision class. Material,
unknown causes and exhausted budgets must never enter the defect allowlist.
"""


WAITING_CODES = frozenset((
    "rate_limited", "review_capacity_wait", "review_invocation_pending", "provider_state_capacity",
    "execution_live", "remote_operation_owned", "verification_ingest_pending",
))
HARNESS_DEFECT_CODES = frozenset(("review_series_no_progress",))


def classify_stop(code):
    if code in WAITING_CODES:
        return "waiting"
    if code in HARNESS_DEFECT_CODES:
        return "harness_defect"
    return "decision"
