"""Explicit strategy assessments by synthetic software-test principals."""

from tests.search_fixtures import digest, provenance


def assessment_spec(description, continued=None):
    current = description["context"]
    rows = current["strategies"]
    if continued is None:
        continued = {(row["node_id"], row["strategy"]) for row in rows}
    evidence_ids = list(current["evidence"])
    failures = [identity for identity, item in current["evidence"].items() if item["kind"] == "strategy_failure"]
    value = {
        "context_digest": description["context_digest"], "author": provenance("strategy-author"),
        "what_changed": "Reevaluate every method against the observed reduction and the remaining obligations",
        "remaining_obligation_ids": current["remaining_obligation_ids"][:],
        "considered_failure_ids": failures,
        "assessments": [{"node_id": row["node_id"], "strategy": row["strategy"],
                         "disposition": "continue" if (row["node_id"], row["strategy"]) in continued else "retire",
                         "reason": "The recorded input determines whether this method can address the residual claim",
                         "next_action": "Establish the remaining uniform bound from the recorded reduction",
                         "evidence_ids": evidence_ids[:], "failure_resolutions": []} for row in rows],
        "plan_bindings": {nid: description["plan_bindings"][nid] for nid, _ in continued},
        "strategy_order": [{"node_id": nid, "strategy": strategy} for nid, strategy in sorted(continued)],
        "next_hypotheses": [{"statement": "The residual bound follows from the recorded reduction",
                             "evidence_ids": evidence_ids[:],
                             "success_criterion": "A uniform bound proves the remaining obligation",
                             "failure_signal": "The reduced family admits a counterexample"}],
        "no_new_hypothesis_reason": None}
    return sign_assessment(value)


def sign_assessment(value, reviewer="strategy-reviewer"):
    return {"assessment": value, "review": {
        "subject_digest": digest(value), "claim_digest": "filled-by-caller",
        "reviewer": provenance(reviewer), "decision": "approve",
        "findings": {field: "The exact current context and each disposition have been checked" for field in
                     ["statement", "assumptions", "scope", "dependencies", "policy"]}}}


def reassess_fixture(controller, continued):
    """Record a real assessment; retain all production freshness and review gates."""
    from tests.search_execution_support import invoke
    description = controller.command("strategy-context", {}, None, None)
    spec = assessment_spec(description, continued)
    spec["review"]["claim_digest"] = digest(controller.status()["contract"]["original_claim"])
    invoke(controller, "reassess", spec, None)
    return spec
