"""Proof facts close exact obligations, never an unfinished route."""

import copy
import importlib.util
import unittest

from search_controller.errors import SearchError
from search_controller.model import apply_event, initial_state
from tests.search_fixtures import contract, decomposition_proposal, digest, provenance, review


def event(state, kind, payload):
    seq = state["revision"] + 1
    return apply_event(state, {"sequence": seq, "request_id": "event-" + str(seq),
                               "kind": kind, "payload": payload})


def admitted(state, proposal):
    state = event(state, "proposal_recorded", {"proposal": proposal, "digest": digest(proposal)})
    pid = list(state["proposals"])[-1]
    state = event(state, "review_recorded", {"proposal_id": pid, "review": review(proposal),
                                            "digest": digest(review(proposal))})
    return event(state, "proposal_admitted", {"proposal_id": pid})


def route_state():
    return admitted(initial_state(contract(), "objective-000001"), decomposition_proposal())


def checkpoint_record(state, oid, kind="proof"):
    claim = copy.deepcopy(state["obligations"][oid]["claim"])
    return {"schema_version": 1, "kind": kind, "claim": claim,
            "origin": {"kind": "external_result", "source": "Pinned source for " + oid,
                       "source_digest": "a" * 64, "statement": claim["statement"],
                       "statement_digest": digest(claim), "study_digest": "b" * 64},
            "evidence_digests": [digest(claim)], "verification_status": "pending",
            "what_changed": "An exact proof is now available", "remaining_obligation_ids": [],
            "next_hypothesis": "Use the proved result in its admitted route",
            "milestone_id": None}


def checkpointed(state, oid, kind="proof"):
    cp = checkpoint_record(state, oid, kind)
    return event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})


def acceptance_record(state, oid, classification="analytical", standard="reviewed", outcome="proof"):
    cp = list(state["checkpoints"].values())[-1]
    return {"schema_version": 1, "checkpoint_id": cp["id"], "checkpoint_digest": digest(cp),
            "obligation_id": oid, "outcome": outcome, "classification": classification,
            "standard": standard, "dependency_ids": [], "route_bindings": [],
            "review": {"subject_digest": digest(cp), "claim_digest": digest(cp["claim"]),
                       "reviewer": provenance("result-reviewer"), "decision": "approve",
                       "findings": {key: "Exact checked statement" for key in
                                    ["statement", "assumptions", "scope", "dependencies", "policy"]}},
            "audit": {"subject_digest": digest(cp), "dependency_ids": [],
                      "evidence_digests": cp["evidence_digests"],
                      "provenance": provenance("audit-service")}}


def accepted(state, oid, **kwargs):
    state = checkpointed(state, oid)
    value = acceptance_record(state, oid, **kwargs)
    return event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})


def bridge_binding(state, cases=None):
    route = state["routes"]["route-000001"]
    return {"route_id": route["id"], "route_digest": digest(route),
            "case_obligation_ids": cases or [], "shared_prerequisite_ids": [],
            "discharged_assumption_ids": []}


def bridge_accepted(state, cases=None):
    oid = state["routes"]["route-000001"]["bridge"]
    state = checkpointed(state, oid)
    value = acceptance_record(state, oid)
    value["route_bindings"] = [bridge_binding(state, cases)]
    return event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})


def closure_record(state):
    from search_controller.proof import root_support
    support = root_support(state)
    subject = {"schema_version": 1, "objective_id": state["objective_id"],
               "contract_digest": state["contract_digest"], "outcome": support["outcome"],
               "acceptance_ids": support["acceptance_ids"],
               "evidence_digests": sorted({d for aid in support["acceptance_ids"] for d in
                                          state["checkpoints"][state["acceptances"][aid]["checkpoint_id"]]["evidence_digests"]}),
               "local_deliveries": [],
               "deliverables": [{"requirement": r, "digest": "d" * 64}
                                for r in state["contract"]["required_deliverables"]]}
    result_review = {"subject_digest": digest(subject), "claim_digest": digest(state["contract"]["original_claim"]),
                     "reviewer": provenance("final-reviewer"), "decision": "approve",
                     "findings": {key: "Checked the complete original statement" for key in
                                  ["statement", "assumptions", "scope", "dependencies", "policy"]}}
    audit = {"subject_digest": digest(subject), "acceptance_ids": subject["acceptance_ids"],
             "evidence_digests": subject["evidence_digests"], "provenance": provenance("closure-auditor")}
    return {"subject": subject, "review": result_review, "audit": audit}


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("search_controller.proof"),
                             "The pure proof reducer must be implemented")

    def test_all_premises_and_explicit_bridge_are_required(self):
        from search_controller.proof import root_support
        state = accepted(route_state(), "obligation-000002")
        self.assertIsNone(root_support(state))
        state = accepted(state, "obligation-000003")
        self.assertIsNone(root_support(state))
        state = bridge_accepted(state, ["obligation-000002", "obligation-000003"])
        self.assertEqual(root_support(state)["outcome"], "proof")
        self.assertEqual(state["proof_status"], "open")

    def test_direct_root_requires_no_artificial_bridge(self):
        from search_controller.proof import root_support
        state = accepted(initial_state(contract(), "objective-000001"), "obligation-000001")
        self.assertEqual(root_support(state)["acceptance_ids"], ["acceptance-000001"])
        self.assertEqual(state["proof_status"], "open")

    def test_conditional_claim_cannot_be_imported_as_unconditional(self):
        state = route_state()
        cp = checkpoint_record(state, "obligation-000002")
        cp["claim"]["assumption_ids"] = ["obligation-000003"]
        cp["origin"]["statement_digest"] = digest(cp["claim"])
        state = event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})
        value = acceptance_record(state, "obligation-000002")
        with self.assertRaises(SearchError):
            event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})

    def test_gap_and_endpoint_exclusion_prevent_root_closure(self):
        from search_controller.proof import root_support, coverage
        for defect in ["gap", "endpoint"]:
            p = decomposition_proposal()
            p["decomposition"]["obligations"][1]["claim"]["scope"].update(
                lower=8 if defect == "gap" else 7, upper_inclusive=defect != "endpoint")
            state = admitted(initial_state(contract(), "objective-000001"), p)
            for oid in ["obligation-000002", "obligation-000003"]:
                state = accepted(state, oid)
            state = bridge_accepted(state, ["obligation-000002", "obligation-000003"])
            self.assertIsNone(root_support(state))
            self.assertEqual(coverage(state, "route-000001")["accepted"], 10)

    def test_large_overlapping_intervals_count_once(self):
        from search_controller.proof import coverage
        p = decomposition_proposal()
        c = contract()
        c["original_claim"]["scope"]["upper"] = 10 ** 15
        p["anchor"]["digest"] = digest(c)
        p["decomposition"]["obligations"][1]["claim"]["scope"].update(lower=6, upper=10 ** 15)
        state = admitted(initial_state(c, "objective-000001"), p)
        for oid in ["obligation-000002", "obligation-000003"]:
            state = accepted(state, oid)
        state = bridge_accepted(state, ["obligation-000002", "obligation-000003"])
        self.assertEqual(coverage(state, "route-000001")["accepted"], 10 ** 15 - 4)
        self.assertEqual(coverage(state, "route-000001")["remaining"], [])

    def test_self_support_and_dropped_dependencies_rejected(self):
        state = checkpointed(route_state(), "obligation-000002")
        value = acceptance_record(state, "obligation-000002")
        value["dependency_ids"] = ["acceptance-000001"]
        value["audit"]["dependency_ids"] = value["dependency_ids"][:]
        with self.assertRaises(SearchError):
            event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})

    def test_disproved_premise_does_not_disprove_root(self):
        from search_controller.proof import root_support
        state = accepted(route_state(), "obligation-000002", outcome="counterexample")
        self.assertIsNone(root_support(state))
        self.assertEqual(state["proof_status"], "open")

    def test_hypothesis_and_no_hit_do_not_create_proof_credit(self):
        for kind in ["hypothesis", "proof"]:
            state = checkpointed(route_state(), "obligation-000002", kind)
            value = acceptance_record(state, "obligation-000002", outcome="no_hit" if kind == "proof" else "proof")
            with self.assertRaises(SearchError):
                event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})

    def test_policy_distinguishes_analytical_bridge_from_computation(self):
        for standard, classification, policy, succeeds in [
                ("reviewed", "analytical", "certificate", True),
                ("reviewed", "computational", "certificate", False),
                ("certificate", "computational", "lean-kernel", False)]:
            c = contract()
            c["proof_policy"] = c["original_claim"]["proof_policy"] = policy
            state = checkpointed(initial_state(c, "objective-000001"), "obligation-000001")
            value = acceptance_record(state, "obligation-000001", classification, standard)
            if succeeds:
                state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
                self.assertEqual(len(state["acceptances"]), 1)
            else:
                with self.assertRaises(SearchError):
                    event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})

    def test_invalidated_dependency_reopens_root_and_preserves_history(self):
        from search_controller.proof import root_support
        state = accepted(initial_state(contract(), "objective-000001"), "obligation-000001")
        state = event(state, "evidence_invalidated", {"acceptance_ids": ["acceptance-000001"],
                                                      "reason": "Snapshot missing", "provenance": provenance("auditor")})
        self.assertIsNone(root_support(state))
        self.assertEqual(len(state["acceptances"]), 1)

    def test_complete_requires_final_bound_review_and_deliverables(self):
        state = accepted(initial_state(contract(), "objective-000001"), "obligation-000001")
        value = closure_record(state)
        bad = copy.deepcopy(value)
        bad["subject"]["deliverables"] = []
        with self.assertRaises(SearchError):
            event(state, "objective_completed", {"closure": bad, "digest": digest(bad)})
        result = event(state, "objective_completed", {"closure": value, "digest": digest(value)})
        self.assertEqual(result["proof_status"], "proved")
        self.assertEqual(result["execution_status"], "resolved")
        self.assertEqual(state["proof_status"], "open")
        result = event(result, "evidence_invalidated", {"acceptance_ids": ["acceptance-000001"],
                       "reason": "Accepted snapshot was lost", "provenance": provenance("auditor")})
        self.assertEqual(result["proof_status"], "invalidated")
        self.assertEqual(result["execution_status"], "needs_replan")

    def test_local_origin_and_milestone_requires_accepted_fact(self):
        from search_controller.proof import checkpoint_milestone
        state = route_state()
        cp = checkpoint_record(state, "obligation-000002")
        cp["origin"] = {"kind": "journal_move", "node_id": "node-000001", "move": 1,
                        "journal_prefix_digest": "f" * 64}
        cp["milestone_id"] = "root-proof"
        state = event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})
        self.assertFalse(checkpoint_milestone(state, "checkpoint-000001"))
        value = acceptance_record(state, "obligation-000002")
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        self.assertTrue(checkpoint_milestone(state, "checkpoint-000001"))

    def test_case_coverage_requires_shared_prerequisites_and_policy(self):
        from search_controller.proof import coverage
        p = decomposition_proposal()
        shared = copy.deepcopy(p["decomposition"]["obligations"][2]["claim"])
        shared["statement"] = "Shared equality characterization"
        p["decomposition"]["obligations"].append({"key": "shared", "claim": shared})
        p["decomposition"]["routes"][0]["premises"].append("new:shared")
        state = admitted(initial_state(contract(), "objective-000001"), p)
        state = accepted(accepted(state, "obligation-000002"), "obligation-000003")
        state = checkpointed(state, "obligation-000004")
        value = acceptance_record(state, "obligation-000004")
        binding = bridge_binding(state, ["obligation-000002", "obligation-000003"])
        binding["shared_prerequisite_ids"] = ["obligation-000005"]
        value["route_bindings"] = [binding]
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        self.assertEqual(coverage(state, "route-000001")["accepted"], 0)
        state = accepted(state, "obligation-000005")
        self.assertEqual(coverage(state, "route-000001")["accepted"], 11)

    def test_alternative_route_does_not_require_abandoned_premises(self):
        from search_controller.proof import root_support
        p = decomposition_proposal()
        alternative = copy.deepcopy(p["decomposition"]["obligations"][2]["claim"])
        alternative["statement"] = "Alternative global lemma"
        bridge = copy.deepcopy(alternative)
        bridge["statement"] = "Alternative lemma implies root"
        p["decomposition"]["obligations"].extend([{"key": "alternative", "claim": alternative},
                                                       {"key": "second-bridge", "claim": bridge}])
        p["decomposition"]["routes"].append({"key": "second", "conclusion": "obligation-000001",
                     "premises": ["new:alternative"], "bridge": "new:second-bridge", "alternative_order": []})
        state = admitted(initial_state(contract(), "objective-000001"), p)
        state = accepted(state, "obligation-000005")
        state = checkpointed(state, "obligation-000006")
        value = acceptance_record(state, "obligation-000006")
        binding = bridge_binding(state)
        binding.update(route_id="route-000002", route_digest=digest(state["routes"]["route-000002"]))
        value["route_bindings"] = [binding]
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        self.assertEqual(root_support(state)["outcome"], "proof")
        self.assertEqual(state["obligations"]["obligation-000002"]["status"], "open")

    def test_obstruction_is_progress_not_a_proof(self):
        from search_controller.proof import root_support
        state = checkpointed(initial_state(contract(), "objective-000001"), "obligation-000001", "obstruction")
        value = acceptance_record(state, "obligation-000001", outcome="obstruction")
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        self.assertIsNone(root_support(state))

    def test_rerunning_accepted_claim_cannot_renew_budget(self):
        from search_controller.admission import qualifying_progress
        state = accepted(route_state(), "obligation-000002")
        cp = checkpoint_record(state, "obligation-000002")
        cp["evidence_digests"] = ["c" * 64]
        cp["next_hypothesis"] = "A renamed next step"
        state = event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})
        value = acceptance_record(state, "obligation-000002")
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        cp = state["checkpoints"]["checkpoint-000002"]
        self.assertFalse(qualifying_progress(state, cp["id"], digest(cp)))

    def test_closure_requires_checked_local_artifacts_and_exact_unused_children(self):
        from tests.search_fixtures import proposal
        state = admitted(initial_state(contract(), "objective-000001"), proposal())
        cp = checkpoint_record(state, "obligation-000001")
        cp.update(origin={"kind": "journal_move", "node_id": "node-000001", "move": 1,
                          "journal_prefix_digest": "f" * 64}, milestone_id="root-proof")
        state = event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})
        value = acceptance_record(state, "obligation-000001")
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        p = proposal()
        p.update(attack_slug="unused", native_parent="node-000001")
        state = admitted(state, p)
        value = closure_record(state)
        with self.assertRaises(SearchError):
            event(state, "objective_completed", {"closure": value, "digest": digest(value)})
        delivery = {"node_id": "node-000001", "inventory_digest": "1" * 64,
                    "checked_unit_digests": ["2" * 64], "consolidation_digest": "3" * 64,
                    "draft_digests": ["4" * 64], "evaluation_digests": ["5" * 64],
                    "finish": {"kind": "local_finish_pending_unused_children", "unused_child_ids": ["node-000002"]},
                    "handoff_digest": "6" * 64}
        value["subject"]["local_deliveries"] = [delivery]
        value["review"]["subject_digest"] = value["audit"]["subject_digest"] = digest(value["subject"])
        result = event(state, "objective_completed", {"closure": value, "digest": digest(value)})
        self.assertEqual(result["proof_status"], "proved")
        self.assertEqual(result["nodes"]["node-000002"]["status"], "admitted")

    def test_lean_root_cannot_count_case_with_prose_partition_bridge(self):
        from search_controller.proof import coverage, root_support
        c = contract()
        c["proof_policy"] = c["original_claim"]["proof_policy"] = "lean-kernel"
        p = decomposition_proposal()
        p["anchor"]["digest"] = digest(c)
        state = admitted(initial_state(c, "objective-000001"), p)
        state = accepted(accepted(state, "obligation-000002", standard="lean-kernel"),
                         "obligation-000003", standard="lean-kernel")
        state = bridge_accepted(state, ["obligation-000002", "obligation-000003"])
        self.assertEqual(coverage(state, "route-000001")["accepted"], 0)
        self.assertIsNone(root_support(state))

    def test_assumption_cannot_discharge_itself_through_a_bridge(self):
        from search_controller.proof import root_support
        p = decomposition_proposal()
        p["decomposition"]["obligations"][1]["claim"]["assumption_ids"] = ["new:tail"]
        # Assumption IDs must already exist at admission. This fixture sets up the
        # equivalent imported graph to exercise the proof join's own cycle guard.
        state = route_state()
        claim = state["obligations"]["obligation-000003"]["claim"]
        claim["assumption_ids"] = ["obligation-000003"]
        state["obligations"]["obligation-000003"]["claim_digest"] = digest(claim)
        state = accepted(accepted(state, "obligation-000002"), "obligation-000003")
        state = checkpointed(state, "obligation-000004")
        value = acceptance_record(state, "obligation-000004")
        binding = bridge_binding(state, ["obligation-000002", "obligation-000003"])
        binding["discharged_assumption_ids"] = ["obligation-000003"]
        value["route_bindings"] = [binding]
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        self.assertIsNone(root_support(state))

    def test_case_under_extra_assumption_does_not_count_as_parent_coverage(self):
        from search_controller.proof import coverage
        state = route_state()
        claim = state["obligations"]["obligation-000002"]["claim"]
        claim["assumption_ids"] = ["obligation-000003"]
        state["obligations"]["obligation-000002"]["claim_digest"] = digest(claim)
        state = accepted(state, "obligation-000002")
        state = bridge_accepted(state, ["obligation-000002", "obligation-000003"])
        self.assertEqual(coverage(state, "route-000001")["accepted"], 0)

    def test_computational_dependency_cannot_hide_under_analytical_certificate(self):
        c = contract()
        c["proof_policy"] = c["original_claim"]["proof_policy"] = "certificate"
        p = decomposition_proposal()
        p["anchor"]["digest"] = digest(c)
        state = admitted(initial_state(c, "objective-000001"), p)
        state = accepted(state, "obligation-000002", classification="computational")
        state = checkpointed(state, "obligation-000001")
        value = acceptance_record(state, "obligation-000001")
        value["dependency_ids"] = value["audit"]["dependency_ids"] = ["acceptance-000001"]
        with self.assertRaises(SearchError):
            event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})

    def test_local_checkpoint_cannot_capture_another_nodes_claim(self):
        state = route_state()
        cp = checkpoint_record(state, "obligation-000003")
        cp.update(origin={"kind": "journal_move", "node_id": "node-000001", "move": 1,
                          "journal_prefix_digest": "a" * 64}, milestone_id="root-proof")
        with self.assertRaises(SearchError):
            event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})

    def test_conditional_obligation_is_accepted_without_closing_consequent(self):
        from search_controller.proof import obligation_support, root_support
        state = route_state()
        conditional = state["obligations"]["obligation-000002"]["claim"]
        conditional["assumption_ids"] = ["obligation-000003"]
        state["obligations"]["obligation-000002"]["claim_digest"] = digest(conditional)
        state = accepted(state, "obligation-000002")
        self.assertIsNotNone(obligation_support(state, "obligation-000002"))
        self.assertIsNone(obligation_support(state, "obligation-000003"))
        self.assertIsNone(root_support(state))

    def test_explicit_case_partition_counts_overlap_once(self):
        from search_controller.proof import coverage
        c = contract()
        c["original_claim"]["scope"] = {"kind": "case_ids", "case_ids": ["a", "b", "c"]}
        p = decomposition_proposal()
        p["anchor"]["digest"] = digest(c)
        base = {"kind": "case_ids", "case_ids": ["a", "b"]}
        tail = {"kind": "case_ids", "case_ids": ["b", "c"]}
        p["claim"]["scope"] = base
        p["contribution"]["coverage"]["scope"] = base
        p["decomposition"]["obligations"][0]["claim"]["scope"] = base
        p["decomposition"]["obligations"][1]["claim"]["scope"] = tail
        state = admitted(initial_state(c, "objective-000001"), p)
        state = accepted(accepted(state, "obligation-000002"), "obligation-000003")
        state = bridge_accepted(state, ["obligation-000002", "obligation-000003"])
        self.assertEqual(coverage(state, "route-000001"), {"accepted": 3, "total": 3, "remaining": []})

    def test_pinned_snapshot_mutation_removes_proof_support(self):
        from search_controller.proof import root_support
        state = accepted(initial_state(contract(), "objective-000001"), "obligation-000001")
        state["checkpoints"]["checkpoint-000001"]["what_changed"] = "Changed preserved snapshot"
        self.assertIsNone(root_support(state))

    def test_reordering_alternatives_does_not_change_accepted_bridge(self):
        from search_controller.proof import root_support
        state = accepted(accepted(route_state(), "obligation-000002"), "obligation-000003")
        state = bridge_accepted(state, ["obligation-000002", "obligation-000003"])
        state = event(state, "replan_recorded", {"route_orders": [{"route_id": "route-000001",
                      "alternative_order": [], "selected": True}], "progress_acceptance_ids": [], "reason": "Keep route order"})
        self.assertEqual(root_support(state)["outcome"], "proof")

    def test_proved_reduction_can_close_bridge_but_progress_only_cannot(self):
        from search_controller.proof import root_support
        state = accepted(accepted(route_state(), "obligation-000002"), "obligation-000003")
        state = checkpointed(state, "obligation-000004", "reduction")
        progress = acceptance_record(state, "obligation-000004", outcome="reduction")
        nonclosing = event(state, "result_accepted", {"acceptance": progress, "digest": digest(progress)})
        self.assertIsNone(root_support(nonclosing))
        proof = acceptance_record(state, "obligation-000004", outcome="proof")
        proof["route_bindings"] = [bridge_binding(state, ["obligation-000002", "obligation-000003"])]
        proved = event(state, "result_accepted", {"acceptance": proof, "digest": digest(proof)})
        self.assertEqual(root_support(proved)["outcome"], "proof")

    def test_completion_waits_for_pending_execution_reconciliation(self):
        state = accepted(initial_state(contract(), "objective-000001"), "obligation-000001")
        state["runs"]["run-000001"] = {"id": "run-000001", "status": "indeterminate"}
        value = closure_record(state)
        with self.assertRaises(SearchError):
            event(state, "objective_completed", {"closure": value, "digest": digest(value)})


if __name__ == "__main__":
    unittest.main()
