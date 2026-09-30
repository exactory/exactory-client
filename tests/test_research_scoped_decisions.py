"""A combined scoped delivery assessment preserves unpaid source obligations."""

import json
from unittest.mock import patch

from source_limited_fixtures import SourceLimitedCase
from strategy_fixtures import StrategyCase
from research_harness import strategy, research_decisions, review_protocol
from research_harness.errors import ResearchError


class ScopedDecisionTests(SourceLimitedCase):
    module = StrategyCase.module
    put = StrategyCase.put
    error = StrategyCase.error
    dossier = StrategyCase.dossier
    provider_response = StrategyCase.provider_response
    route = StrategyCase.route
    review_response = StrategyCase.review_response
    record_response = StrategyCase.record_response
    decision = StrategyCase.decision

    def intent(self, identifier="intent-1"):
        value = StrategyCase.intent(self, identifier)
        value["objective"] = {"kind": "native", "id": self.objective["id"]}
        value["full_objective"] = self.objective["statement"]
        value["authors"] = ["author", "cycle-author", "scope-preparer"]
        value["branch"]["remaining_obligations"] = [self.debt]
        return value

    def prepare_scoped_strategy(self):
        self.prepare_delivery(accept=False)
        self.evidence = self.execution_payload["outputs"][0]["artifact"]
        self.put("local_artifact", "instruction", {"id": "instruction", "path": "context/instruction.md",
            "artifact": self.artifacts.put(b"Investigate the complete objective and submit the supported result with source limits disclosed.", "text/plain")})
        self.mutate(strategy.record_intent, self.intent())
        dossier = self.dossier()
        dossier.update(phase="result", claims=[{"id": "finite-bound", "statement": "The finite maximum is nine.",
            "scope": "The supported finite domain without the unavailable dataset comparison.", "evidence": [self.evidence]}])
        self.mutate(strategy.record_strategy, dossier)
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar"))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))

    def scoped_response(self, suffix, *, native=False):
        identifier, reviewer = "scoped-" + suffix, "reviewer-" + suffix
        assignment_id = "assignment-" + identifier
        self.mutate(review_protocol.record_assignment, {"id": assignment_id, "route_id": "route", "dossier_id": "dossier-1",
                    "role": "result", "reviewer_id": reviewer, "author_id": "author", "context": {}})
        assignment = self.store.snapshot()["records"]["review_assignment"][assignment_id]
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        support = self.review(self.execution_payload, identifier) if native else self.scope_review(identifier, assessor=reviewer)
        support["assessor"] = packet["assessor"]
        response = self.review_response("result", status="insufficient")
        response["support"] = support
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(response)):
            self.mutate(review_protocol.invoke_assignment, {"id": "attempt-" + identifier, "assignment_id": assignment_id}, credential="fixture-only")
        return self.mutate(research_decisions.record_value_review,
            dict(response, id=identifier, dossier_id="dossier-1", assignment_id=assignment_id))["result"]

    def test_supported_scoped_delivery_does_not_require_completion_of_unavailable_comparison(self):
        self.prepare_scoped_strategy()
        for suffix in ("a", "b"):
            self.scoped_response(suffix)
        readiness = self.manuscript_readiness()
        payload = self.decision(action="deliver_requested")
        payload.update(phase="result", artifact_disposition="deliver_under_instruction", review_ids=["scoped-a", "scoped-b"],
                       remaining_objective=[self.debt], support_candidate_digest=readiness["candidate_digest"])
        self.mutate(research_decisions.record_research_decision, payload)
        state = research_decisions.decision_state(self.store.snapshot()["records"], self.artifacts, "write")
        self.assertTrue(state["ready"], state["obligations"])
        self.assertFalse(self.manuscript_readiness()["objective_complete"])
        self.assertEqual(state["decision"]["payload"]["goal_status"], "open")
        self.assertEqual(state["decision"]["payload"]["remaining_objective"], [self.debt])
        support = self.store.snapshot()["records"]["research_support"]["scoped-a"]
        self.assertEqual(support["scientific_target_digest"], readiness["scientific_target_digest"])

    def test_selected_scope_cannot_be_reviewed_as_the_complete_native_target(self):
        self.prepare_scoped_strategy()
        with self.assertRaises(ResearchError) as caught:
            self.scoped_response("a", native=True)
        self.assertEqual(caught.exception.code, "research_support_scope_mismatch")
        records = self.store.snapshot()["records"]
        self.assertNotIn("scoped-a", records.get("research_support", {}))
        self.assertIn("attempt-scoped-a", records["review_attempt"])
