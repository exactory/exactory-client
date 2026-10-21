"""A retired route accepts no new work, and a preserved slot continues through a context repair on another route."""

import json
from unittest.mock import patch

from research_harness.errors import ResearchError

from strategy_fixtures import StrategyCase
from research_harness import research_decisions, review_context_repair, review_protocol


class RouteRetirementCase(StrategyCase):
    def retire(self, identifier="retirement", assignment="assignment-bar-a"):
        return self.mutate(review_protocol.record_context_event, {"id": identifier, "assignment_id": assignment,
            "kind": "route_retired", "source": "owner",
            "reason": "The owner withdrew this route on 2026-10-21; no further call on it is permitted.",
            "evidence": [self.evidence]})

    def second_route(self, probe_id="repair-probe"):
        self.mutate(review_protocol.record_route, {"id": "route-2", "adapter": "openai_responses_v1", "model": "fixture-model-2",
                    "endpoint": "https://api.openai.com/v1/responses", "configuration": {"max_output_tokens": 1500, "timeout_seconds": 5}})

        def provider(route, request, credential):
            packet = json.loads(request["input"][0]["content"].removeprefix("The packet below is JSON.\n"))
            return self.provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})

        with patch("research_harness.review_transport.send_request", side_effect=provider):
            return review_protocol.probe_route(self.store, {"id": probe_id, "route_id": "route-2"},
                expected_revision=self.store.revision, request_id=probe_id + "-call", credential="fixture-only")

    def repair_payload(self, predecessor="assignment-bar-a", events=("retirement",)):
        return {"id": "repair-a", "route_id": "route-2", "dossier_id": "dossier-1", "role": "bar",
            "reviewer_id": "repair-reviewer", "author_id": "author", "context": {"context_repair": {
                "assignment_id": predecessor, "event_ids": list(events), "probe_id": "repair-probe",
                "reason": "The route of the slot was retired; a reviewer on another route continues it.", "evidence": [self.evidence]}}}

    def state(self, assignment):
        return review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, assignment)


class RetiredRouteTests(RouteRetirementCase):
    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar", status="insufficient"))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))

    def test_a_retired_route_accepts_no_new_assignment_and_no_run(self):
        slate = {"id": "assignment-slate-a", "route_id": "route", "dossier_id": "dossier-1", "role": "slate",
                 "reviewer_id": "reviewer-a", "author_id": "author", "context": {}}
        self.mutate(review_protocol.record_assignment, slate)
        self.retire()
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, dict(slate, id="assignment-slate-b", reviewer_id="reviewer-b"))
        self.assertEqual(refused.exception.code, "review_route_retired")
        with self.assertRaises(ResearchError) as refused:
            with patch("research_harness.review_transport.send_request", return_value=self.provider_response(self.review_response())):
                review_protocol.invoke_assignment(self.store, {"id": "attempt-slate-a", "assignment_id": "assignment-slate-a"},
                                                  expected_revision=self.store.revision, request_id="invoke-slate-a", credential="fixture-only")
        self.assertEqual(refused.exception.code, "review_route_retired")
        self.assertIn("review_route_retired", [item["code"] for item in self.state("assignment-slate-a")["obligations"]])

    def test_a_retirement_keeps_the_observed_assignments_verified(self):
        self.retire()
        state = self.state("assignment-bar-a")
        self.assertTrue(state["ready"], state["obligations"])
        self.assertEqual(state["context_status"], "verified_for_route")
        records = self.store.snapshot()["records"]
        self.assertEqual(review_protocol.route_state(records, self.artifacts, "route")["context_status"], "verified_for_route")
        self.assertEqual(review_protocol.retired_routes(records), {"route"})

    def test_a_retirement_is_a_cause_for_a_repair_on_another_route(self):
        self.retire()
        self.second_route()
        saved = self.mutate(review_protocol.record_assignment, self.repair_payload())["result"]
        records = self.store.snapshot()["records"]
        self.assertEqual(review_context_repair.slot_id(records, saved), "assignment-bar-a")
        packet = json.loads(self.artifacts.read(saved["packet"]))
        self.assertEqual(packet["context_repair"]["event_ids"], ["retirement"])
        self.assertEqual(packet["context_repair"]["cause"], [{"id": "retirement", "kind": "route_retired", "source": "owner",
            "reason": "The owner withdrew this route on 2026-10-21; no further call on it is permitted."}])
        self.assertEqual(packet["context_repair"]["historical_review_ids"], ["bar-a"])
        # The repair binds the retirement exactly: another event id or other evidence is refused.
        payload = self.repair_payload(events=("other",))
        payload["id"] = "repair-x"
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, payload)
        self.assertEqual(refused.exception.code, "review_context_repair_mismatch")


class RetiredSourceTurnTests(RouteRetirementCase):
    """The shape of the first problem record: the slot's last observed turn requested sources, and its continuation was
    assigned but never run when the route was retired."""

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        request_turn = self.review_response("bar", status="unresolved")
        request_turn["source_requests"] = [{"id": "page-0", "kind": "inventory_page", "page": 0}]
        assignment = {"id": "assignment-bar-a", "route_id": "route", "dossier_id": "dossier-1", "role": "bar",
                      "reviewer_id": "reviewer-a", "author_id": "author", "context": {}}
        self.mutate(review_protocol.record_assignment, assignment)
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(request_turn)):
            review_protocol.invoke_assignment(self.store, {"id": "attempt-bar-a", "assignment_id": "assignment-bar-a"},
                                              expected_revision=self.store.revision, request_id="invoke-bar-a", credential="fixture-only")
        self.mutate(review_protocol.record_assignment, dict(assignment, id="assignment-bar-a-source-1",
                                                            context={"prior_assignment_id": "assignment-bar-a"}))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))

    def test_a_dead_continuation_on_the_retired_route_does_not_block_the_repair(self):
        self.retire()
        dead = self.state("assignment-bar-a-source-1")
        self.assertIn("review_route_retired", [item["code"] for item in dead["obligations"]])
        self.second_route()
        saved = self.mutate(review_protocol.record_assignment, self.repair_payload())["result"]
        records = self.store.snapshot()["records"]
        self.assertEqual(review_context_repair.slot_id(records, saved), "assignment-bar-a")
        packet = json.loads(self.artifacts.read(saved["packet"]))
        self.assertEqual([turn["assignment_id"] for turn in packet["scientific_history"]], ["assignment-bar-a"])
        self.assertEqual(packet["pending_source_requests"], [{"id": "page-0", "kind": "inventory_page", "page": 0}])

    def test_without_a_retirement_the_dead_continuation_still_blocks_a_repair(self):
        self.mutate(review_protocol.record_context_event, {"id": "exposure", "assignment_id": "assignment-bar-a",
            "kind": "contamination", "source": "author_history", "reason": "An inherited context exposed author advocacy.",
            "evidence": [self.evidence]})
        self.second_route()
        with self.assertRaises(ResearchError) as refused:
            self.mutate(review_protocol.record_assignment, self.repair_payload(events=("exposure",)))
        self.assertEqual(refused.exception.code, "review_resampling_forbidden")
