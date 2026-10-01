"""Combined actual-candidate support, consequence and retained critical work."""

import copy
import json
from unittest.mock import patch

from development_fixtures import DevelopmentCase
from strategy_fixtures import StrategyCase
from research_harness import strategy, research_decisions, review_protocol


class ResultDecisionTests(DevelopmentCase):
    module = StrategyCase.module
    put = StrategyCase.put
    error = StrategyCase.error
    dossier = StrategyCase.dossier
    work_item = StrategyCase.work_item
    provider_response = StrategyCase.provider_response
    route = StrategyCase.route
    review_response = StrategyCase.review_response
    record_response = StrategyCase.record_response
    decision = StrategyCase.decision

    def setUp(self):
        super().setUp()
        self.prepared_candidate()
        self.execution = self.store.snapshot()["records"]["execution"]["execution-run-1"]["payload"]
        self.evidence = self.execution["outputs"][0]["artifact"]
        self.put("local_artifact", "instruction", {"id": "instruction", "path": "context/instruction.md",
            "artifact": self.artifacts.put(b"Investigate the bound and submit the supported result.", "text/plain")})

    def intent(self, identifier="intent-1"):
        value = StrategyCase.intent(self, identifier)
        value["objective"] = {"kind": "native", "id": self.objective["id"]}
        value["authors"] = ["cycle-author", "author"]
        return value

    def result_response(self, identifier, reviewer, response, *, dossier_id="dossier-1"):
        assignment_id = "assignment-" + identifier
        self.mutate(review_protocol.record_assignment, {"id": assignment_id, "route_id": "route", "dossier_id": dossier_id,
                    "role": "result", "reviewer_id": reviewer, "author_id": "author", "context": {}})
        assignment = self.store.snapshot()["records"]["review_assignment"][assignment_id]
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        support = self.review(self.execution, identifier="support-" + identifier)
        support["assessor"] = packet["assessor"]
        response = copy.deepcopy(response)
        if response["support"] == "invalid":
            support["checks"][0]["status"] = "failed"
            support["verdict"] = "not_ready"
        response["support"] = support
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(response)):
            self.mutate(review_protocol.invoke_assignment, {"id": "attempt-" + identifier, "assignment_id": assignment_id}, credential="fixture-only")
        return self.mutate(research_decisions.record_value_review, dict(response, id=identifier, dossier_id=dossier_id, assignment_id=assignment_id))["result"]

    def prepare_result(self, *, sufficient=True, invalid=False, work=None, condition=None, consequence=None, claim=None, relation="direct", intent_payload=None):
        intent = self.intent() if intent_payload is None else copy.deepcopy(intent_payload)
        intent["publication"]["quality_condition"] = condition
        self.mutate(strategy.record_intent, intent)
        dossier = self.dossier()
        dossier.update(phase="result", claims=[{"id": "claim-a", "statement": "The finite maximum is nine.", "scope": "The finite enumerated range.",
                                                "evidence": [self.evidence]}])
        if claim is not None:
            dossier["claims"][0]["statement"] = claim
        if consequence is not None:
            dossier["candidates"][0]["addition"] = consequence
        dossier["candidates"][0]["transfer"]["kind"] = relation
        if work is not None:
            self.mutate(strategy.record_work_item, work)
            dossier["work_items"] = [work["id"]]
        self.mutate(strategy.record_strategy, dossier)
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar"))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))
        for suffix in ("a", "b"):
            response = self.review_response("result", status="sufficient" if sufficient else "insufficient")
            if consequence is not None:
                response["value"].update(consequence=consequence, reason="The complete finite enumeration supports this exact consequence; the broader objective remains open.")
            response["support"] = "invalid" if invalid and suffix == "b" else None
            if work is not None:
                response["value"]["work_items"] = [{"id": work["id"], "classification": work["classification"], "status": "accepted",
                                                     "reason": "The missing discriminator is critical to the inference.", "evidence": [self.evidence]}]
            self.result_response("result-" + suffix, "reviewer-" + suffix, response)
        payload = self.decision(action="develop_manuscript")
        payload.update(phase="result", artifact_disposition="prepare_manuscript", review_ids=["result-a", "result-b"],
                       support_candidate_digest=self.store.snapshot()["records"]["value_review"]["result-a"]["payload"]["support"]["candidate_digest"])
        if work is not None:
            payload["work_items"] = [{"id": work["id"], "disposition": "test", "reason": "Perform the deciding test.", "evidence": []}]
        return payload

    def state(self):
        return research_decisions.decision_state(self.store.snapshot()["records"], self.artifacts, "write")

    def codes(self):
        return {item["code"] for item in self.state()["obligations"]}

    def test_expected_theorem_can_remove_an_assumption_without_an_ordinal_score(self):
        consequence = "The expected bound holds by exhaustive enumeration, removing the formerly unproved monotonicity assumption on this finite domain."
        payload = self.prepare_result(consequence=consequence)
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        records = self.store.snapshot()["records"]
        self.assertEqual(set(records["research_support"]), {"result-a", "result-b"})
        self.assertEqual(records["research_support"]["result-b"]["readiness_review_id"], "support-result-b")
        self.assertEqual(records["value_review"]["result-b"]["payload"]["value"]["consequence"], consequence)
        self.assertEqual(payload["goal_status"], "open")

    def test_decisive_negative_result_excludes_the_proposed_stronger_bound(self):
        consequence = "The attained value 9 excludes every method requiring a uniform upper bound of 8 on this same domain."
        payload = self.prepare_result(consequence=consequence, claim="At n = 3, the value 9 refutes the proposed bound 8.", relation="logical")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        records = self.store.snapshot()["records"]
        self.assertEqual(records["value_review"]["result-a"]["payload"]["value"]["consequence"], consequence)
        self.assertEqual(records["strategy_dossier"]["dossier-1"]["payload"]["claims"][0]["evidence"], [self.evidence])

    def test_restricted_witness_can_refute_a_universal_claim_without_closing_the_larger_goal(self):
        consequence = "A single admissible n = 3 witness disproves the universal bound n squared at most 8; its finite scope suffices for that logical refutation."
        payload = self.prepare_result(consequence=consequence, claim="The n = 3 witness has square 9.", relation="logical")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        self.assertEqual(self.state()["decision"]["payload"]["goal_status"], "open")
        self.assertTrue(self.state()["decision"]["payload"]["remaining_objective"])

    def test_supported_true_claim_does_not_establish_sufficient_consequence(self):
        payload = self.prepare_result(sufficient=False)
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertFalse(self.state()["ready"])
        self.assertIn("research_consequence_insufficient", self.codes())

    def test_favorable_consequence_does_not_override_failed_validity(self):
        payload = self.prepare_result(invalid=True)
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertFalse(self.state()["ready"])
        self.assertIn("independent_review_pending", self.codes())

    def test_adopted_critical_request_blocks_unsupported_consequence(self):
        payload = self.prepare_result(work=self.work_item())
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertIn("research_consequence_work_unresolved", self.codes())

    def test_unconditional_supported_delivery_retains_unmet_scientific_goal(self):
        payload = self.prepare_result(sufficient=False, work=self.work_item())
        payload.update(action="deliver_requested", artifact_disposition="deliver_under_instruction")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        self.assertEqual(self.state()["decision"]["payload"]["goal_status"], "open")

    def test_quality_condition_is_not_waived_by_publication_authority(self):
        payload = self.prepare_result(sufficient=False, condition="The result must resolve the decisive mechanism.")
        payload.update(action="deliver_requested", artifact_disposition="deliver_under_instruction")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertIn("research_publication_condition_unmet", self.codes())

    def test_delivery_authority_does_not_waive_an_unresolved_validity_request(self):
        work = self.work_item()
        work["classification"] = "validity"
        payload = self.prepare_result(sufficient=False, work=work)
        payload.update(action="deliver_requested", artifact_disposition="deliver_under_instruction")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertIn("research_validity_work_unresolved", self.codes())

    def test_optional_polish_does_not_block_a_supported_sufficient_result(self):
        work = self.work_item()
        work["classification"] = "optional"
        payload = self.prepare_result(work=work)
        payload["work_items"][0]["disposition"] = "optional"
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])

    def test_new_critical_work_invalidates_prior_development_approval(self):
        payload = self.prepare_result()
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        self.mutate(strategy.record_work_item, self.work_item())
        self.assertIn("research_work_item_stale", self.codes())

    def test_prewrite_result_approval_cannot_replace_the_post_measurement_publication_decision(self):
        payload = self.prepare_result()
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        publication = research_decisions.decision_state(self.store.snapshot()["records"], self.artifacts, "publication")
        self.assertIn("research_post_measurement_decision_required", {item["code"] for item in publication["obligations"]})

    def test_user_requested_narrow_report_can_be_delivered_without_full_goal_success(self):
        intent = self.intent()
        intent["task_kind"] = "specified_delivery"
        intent["publication"].update(endpoint="artifact", required=True)
        self.put("local_artifact", "narrow-instruction", {"id": "narrow-instruction", "path": "context/narrow.md",
            "artifact": self.artifacts.put(b"Deliver a report of the finite verified result, retaining the unresolved general problem.", "text/plain")})
        intent["instruction"] = "narrow-instruction"
        intent["publication"]["instruction"] = "narrow-instruction"
        payload = self.prepare_result(sufficient=False, intent_payload=intent)
        payload.update(action="deliver_requested", artifact_disposition="deliver_under_instruction")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        self.assertEqual(self.state()["decision"]["payload"]["goal_status"], "open")
        self.assertTrue(self.state()["decision"]["payload"]["remaining_objective"])

    def test_unanswered_scope_choice_does_not_authorize_author_selected_delivery(self):
        intent = self.intent()
        intent["publication"].update(endpoint="none", required=False, instruction=None)
        payload = self.prepare_result(sufficient=False, intent_payload=intent)
        payload.update(action="deliver_requested", artifact_disposition="deliver_under_instruction")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertIn("research_delivery_authority_missing", self.codes())
        before = self.store.snapshot()
        for unused in range(2):
            self.assertFalse(self.state()["ready"])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(strategy.current_intent(before["records"])["payload"]["full_objective"], intent["full_objective"])

    def test_permission_to_publish_is_not_an_instruction_to_deliver_a_smaller_result(self):
        intent = self.intent()
        intent["publication"]["required"] = False
        payload = self.prepare_result(sufficient=False, intent_payload=intent)
        payload.update(action="deliver_requested", artifact_disposition="deliver_under_instruction")
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertIn("research_delivery_authority_missing", self.codes())

    def reconsider_result(self, sufficient):
        original = self.prepare_result(sufficient=False)
        self.mutate(research_decisions.record_research_decision, original)
        records = self.store.snapshot()["records"]
        dossier = copy.deepcopy(records["strategy_dossier"]["dossier-1"]["payload"])
        consequence = "The finite witness refutes the stronger universal bound, although it does not distinguish the intended mechanisms."
        dossier.update(id="reconsidered", previous="dossier-1", tranche=None,
            material_change={"kind": "reconsideration", "reason": "Assess the unforeseen logical refutation without credit for effort.",
                             "evidence": [self.evidence], "prior_objections": []},
            reconsideration={"decision_id": original["id"], "reason": consequence,
                             "alternatives": ["candidate-a", "candidate-b"],
                             "remaining_obligations": ["The mechanism distinction remains unresolved."]},
            continuity=[{"claim_ids": ["claim-a"], "disposition": "retained", "reason": "The same finite result is retained.", "evidence": [self.evidence]}])
        dossier["candidates"][0]["addition"] = consequence
        self.mutate(strategy.record_strategy, dossier)
        for suffix in ("a", "b"):
            response = self.review_response("result", status="sufficient" if sufficient else "insufficient")
            response["value"].update(consequence=consequence, reason="The comparison considers the original alternatives and the no-branch option without sunk-cost credit.")
            self.result_response("reconsidered-" + suffix, "reviewer-" + suffix, response, dossier_id="reconsidered")
        payload = copy.deepcopy(original)
        payload.update(id="reconsidered-decision", dossier_id="reconsidered", review_ids=["reconsidered-a", "reconsidered-b"])
        return original, payload, consequence

    def test_unforeseen_consequence_gets_explicit_reconsideration_and_keeps_original_obligation(self):
        original, payload, consequence = self.reconsider_result(True)
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertTrue(self.state()["ready"], self.state()["obligations"])
        records = self.store.snapshot()["records"]
        self.assertEqual(records["value_review"]["result-a"]["payload"]["value"]["status"], "insufficient")
        self.assertEqual(records["value_review"]["reconsidered-a"]["payload"]["value"]["consequence"], consequence)
        self.assertEqual(records["strategy_dossier"]["reconsidered"]["payload"]["reconsideration"]["decision_id"], original["id"])
        self.assertEqual(self.state()["decision"]["payload"]["goal_status"], "open")
        self.assertTrue(self.state()["decision"]["payload"]["remaining_objective"])

    def test_failed_reconsideration_retains_rejection_and_permits_justified_branch_closure(self):
        unused, payload, unused_consequence = self.reconsider_result(False)
        self.mutate(research_decisions.record_research_decision, payload)
        self.assertIn("research_consequence_insufficient", self.codes())
        closure = dict(payload, id="reconsidered-closure", action="close_branch", artifact_disposition="result_report")
        self.mutate(research_decisions.record_research_decision, closure)
        state = research_decisions.decision_state(self.store.snapshot()["records"], self.artifacts, "round")
        self.assertTrue(state["ready"], state["obligations"])
        self.assertFalse(self.state()["ready"])
        self.assertEqual(state["decision"]["payload"]["goal_status"], "open")
        self.assertIn(payload["id"], self.store.snapshot()["records"]["research_decision"])
