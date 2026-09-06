"""Explicit, small mathematical records for controller behavior tests."""

import hashlib

from search_controller.storage import canonical_bytes


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def claim(statement="For every integer n from 5 to 15, P(n)"):
    return {
        "statement": statement,
        "quantifiers": "For every integer n with 5 <= n <= 15",
        "assumption_ids": [],
        "proof_policy": "reviewed",
        "scope": {"kind": "integer_interval", "lower": 5, "upper": 15,
                  "lower_inclusive": True, "upper_inclusive": True},
    }


def provenance(actor):
    return {"source": "host", "actor_id": actor, "attestation_id": "host-" + actor}


def contract():
    return {
        "schema_version": 1,
        "original_claim": claim(), "assumption_ids": [],
        "root_obligation": "obligation-000001", "root_attack_slug": "root",
        "requested_outcome": "proof", "proof_policy": "reviewed",
        "required_deliverables": ["Conventional proof with all dimension cases"],
        "resource_policy": {"max_total_moves": None, "max_total_runs": None,
                            "max_workers": 1},
    }


def proposal(category="main"):
    return {
        "schema_version": 1, "author": provenance("author"),
        "category": category, "relationship": category,
        "attack_slug": "attempt", "role": "research", "claim": claim(),
        "target_obligation": "obligation-000001" if category != "standalone" else None,
        "logical_predecessor": None, "native_parent": None,
        "anchor": {"kind": "objective", "digest": digest(contract())},
        "inherited_evidence": [], "inherited_assumption_ids": [],
        "hypothesis": "Induction with an exact recurrence closes every dimension",
        "method": "induction", "applicability": "The recurrence is valid for n >= 5",
        "success_criterion": "Prove base case and recurrence for the full interval",
        "failure_criterion": "A recurrence coefficient can be negative",
        "parent_effect": "Discharges the original quantified proposition",
        "studies": {"problem": "1" * 64, "novelty": "2" * 64,
                    "strategies": [{"method": "induction", "digest": "3" * 64}]},
        "contribution": {"route": None, "deduction": "Exact original claim",
                         "necessity": None, "coverage": None,
                         "standalone": ({"prospective_theorem": "A sharp recurrence bound",
                                         "primary_sources": ["4" * 64],
                                         "strongest_known_result": "The known bound loses a factor n",
                                         "mathematical_contribution": "Remove that factor for every n",
                                         "significance": "A sharp uniform bound changes the asymptotic order"}
                                        if category == "standalone" else None)},
        "task": {"kind": "proof", "purpose": "Prove the original assertion",
                 "input_domain": "Integers 5 through 15"},
        "limits": {"max_moves": 24, "max_runs": 24, "timeout_seconds": 300, "workers": 1},
        "budget": {"mode": "new", "account_id": None,
                   "basis_checkpoint_id": None, "basis_checkpoint_digest": None,
                   "justification": "First investigation of the root obligation"},
        "equivalent_node_ids": [],
        "checkpoint_criteria": [{"kind": "accepted_obligation", "criterion_id": "root-proof",
                                  "obligation_id": "obligation-000001",
                                  "explanation": "An accepted proof resolves the root"}],
        "retreat_criteria": [{"kind": "move_limit", "threshold": 24}],
        "decomposition": {"obligations": [], "routes": []},
    }


def review(subject, reviewer="reviewer-one", decision="approve"):
    return {
        "schema_version": 1, "subject_digest": digest(subject),
        "claim_digest": digest(subject["claim"]), "reviewer": provenance(reviewer),
        "decision": decision,
        "findings": {"root_connection": "The exact claim matches the full root domain",
                     "mathematical_substance": "The recurrence would establish the required uniform statement",
                     "assumptions": "The proposal introduces no unstated assumptions",
                     "scope": "Both interval endpoints and every intermediate integer are included",
                     "equivalence": "No earlier equivalent attempt has been omitted",
                     "necessity": "The stated finite domain covers the necessary decision completely",
                     "novelty": "Primary-source comparison identifies the missing sharp factor",
                     "significance": "The proposed uniform improvement is mathematically substantial",
                     "renewal_basis": "The verified reduction addresses the earlier coefficient obstruction"},
        "unresolved_objections": [],
    }


def decomposition_proposal():
    value = proposal()
    base = claim("P(5) and P(6)")
    base["quantifiers"] = "For n in {5, 6}"
    base["scope"].update(upper=6)
    tail = claim("P(n) for every integer 7 <= n <= 15")
    tail["quantifiers"] = "For every integer 7 <= n <= 15"
    tail["scope"].update(lower=7)
    bridge = claim("The base cases and remaining cases imply the full objective")
    bridge["scope"] = {"kind": "named", "name": "partition-deduction"}
    value["decomposition"] = {
        "obligations": [{"key": "base", "claim": base}, {"key": "tail", "claim": tail},
                        {"key": "bridge", "claim": bridge}],
        "routes": [{"key": "partition", "conclusion": "obligation-000001",
                    "premises": ["new:base", "new:tail"], "bridge": "new:bridge",
                    "alternative_order": []}],
    }
    value.update(category="coverage", relationship="coverage", claim=base,
                 target_obligation="new:base")
    value["contribution"].update(
        route="new:partition",
        deduction="The two base cases are an exact subset of the eleven required cases",
        coverage={"parent_obligation": "obligation-000001", "scope": base["scope"],
                  "partition_route": "new:partition", "subset_deduction": "5 and 6 lie within 5 through 15"})
    value["checkpoint_criteria"][0]["obligation_id"] = "new:base"
    return value


def checkpoint(state, kind="reduction"):
    """An internal accepted-result fixture, not a public verification input."""
    value = {"id": "checkpoint-000001", "kind": kind, "evidence_digests": ["a" * 64],
             "origin": {"kind": "external_result", "source": "Pinned exact reduction"}}
    state["checkpoints"][value["id"]] = value
    state["acceptances"]["acceptance-000001"] = {
        "checkpoint_id": value["id"], "checkpoint_digest": digest(value), "status": "accepted"}
    return value


def successor(state, method="induction", category="main"):
    value = proposal(category)
    anchor = checkpoint(state)
    value.update(attack_slug="next", relationship="continuation", logical_predecessor="node-000001",
                 anchor={"kind": "checkpoint", "checkpoint_id": anchor["id"], "digest": digest(anchor)},
                 inherited_evidence=anchor["evidence_digests"][:], method=method)
    value["studies"]["strategies"] = [{"method": method, "digest": "5" * 64}]
    return value
