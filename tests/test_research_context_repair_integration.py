"""Public context repair retains adverse science without opening a new review slot."""

import json
from unittest.mock import patch

from strategy_fixtures import StrategyCase
from research_harness import research_decisions, review_context_repair, review_protocol


class ContextRepairIntegrationTests(StrategyCase):
    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.objection = {"id": "counterexample", "claim": "The broader inference is unsupported.",
            "reason": "The same observation fits both mechanisms.", "evidence": [self.evidence],
            "resolution_condition": "Supply a deciding observation for the two mechanisms."}
        self.original = self.review_response("bar", status="insufficient", objections=[self.objection])
        self.record_response("bar-a", "reviewer-a", self.original)
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))

    def expose(self, identifier="exposure", assignment="assignment-bar-a"):
        return self.mutate(review_protocol.record_context_event, {"id": identifier, "assignment_id": assignment,
            "kind": "contamination", "source": "author_history", "reason": "An actual inherited context exposed author advocacy.",
            "evidence": [self.evidence]})

    def probe_repair(self, identifier="repair-probe"):
        def provider(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=provider):
            return review_protocol.probe_route(self.store, {"id": identifier, "route_id": "route"},
                expected_revision=self.store.revision, request_id=identifier + "-call", credential="fixture-only")

    def repair_payload(self):
        return {"id": "repair-a", "route_id": "route", "dossier_id": "dossier-1", "role": "bar",
            "reviewer_id": "repair-reviewer", "author_id": "author", "context": {"context_repair": {
                "assignment_id": "assignment-bar-a", "event_ids": ["exposure"], "probe_id": "repair-probe",
                "reason": "A fresh verified context reassesses the retained scientific findings.", "evidence": [self.evidence]}}}

    def prepare_repair(self):
        self.expose()
        self.probe_repair()
        return self.mutate(review_protocol.record_assignment, self.repair_payload())["result"]

    def packet(self, assignment="repair-a"):
        saved = self.store.snapshot()["records"]["review_assignment"][assignment]
        return json.loads(self.artifacts.read(saved["packet"]))

    def invoke(self, response, assignment="repair-a"):
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(response)):
            return review_protocol.invoke_assignment(self.store,
                {"id": "attempt-" + assignment, "assignment_id": assignment}, expected_revision=self.store.revision,
                request_id="invoke-" + assignment, credential="fixture-only")

    def final_response(self):
        response = self.review_response("bar")
        response["reassessment"] = {"assignment_id": "assignment-bar-a", "findings": [{
            "review_id": "bar-a", "disposition": "revised", "reason": "The finite consequence is worthwhile; the broader objection remains open.",
            "evidence": [self.evidence]}]}
        response["value"]["objection_findings"] = [{"id": "counterexample", "status": "continuing",
            "reason": "The broader inference still needs the deciding observation.", "evidence": [self.evidence]}]
        return response

    def record_final(self, response, assignment="repair-a"):
        return self.mutate(research_decisions.record_value_review,
            dict(response, id="repair-final", dossier_id="dossier-1", assignment_id=assignment))

    def test_contamination_does_not_release_a_completed_slot_for_a_third_favorable_sample(self):
        self.expose()
        payload = self.repair_payload()
        payload["context"] = {}
        self.error("review_slots_fixed", lambda: self.mutate(review_protocol.record_assignment, payload))

    def test_repair_uses_the_original_slot_and_retains_adverse_scientific_content(self):
        saved = self.prepare_repair()
        packet = self.packet()
        self.assertEqual(saved["history"], [])
        self.assertEqual(packet["context_repair"]["slot_id"], "assignment-bar-a")
        self.assertEqual(review_context_repair.slot_id(self.store.snapshot()["records"], saved), "assignment-bar-a")
        self.assertIn("explicit reassessment", saved["prompt"]["text"])
        self.assertEqual(packet["historical_reviews"][0]["id"], "bar-a")
        self.assertEqual(packet["historical_reviews"][0]["payload"]["value"]["status"], "insufficient")
        self.assertIn(self.objection["reason"], json.dumps(packet["scientific_history"]))
        self.assertFalse(review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts,
            "assignment-bar-a")["ready"])

    def test_public_repair_requires_exact_events_current_probe_and_original_source_boundary(self):
        self.expose()
        self.probe_repair()
        for change, code in (({"event_ids": []}, "review_context_repair_mismatch"),
                             ({"probe_id": "probe"}, "review_context_repair_unverified"),
                             ({"evidence": [self.artifacts.put(b"An author replacement account.", "text/plain")]}, "review_context_repair_mismatch")):
            with self.subTest(change=change):
                payload = self.repair_payload()
                payload["context"]["context_repair"].update(change)
                self.error(code, lambda: self.mutate(review_protocol.record_assignment, payload))
        payload = self.repair_payload()
        payload["context"]["source_links"] = []
        self.error("invalid_review_context", lambda: self.mutate(review_protocol.record_assignment, payload))

    def test_reassessment_must_be_in_the_actual_observed_output(self):
        self.prepare_repair()
        response = self.review_response("bar")
        self.invoke(response)
        self.error("review_reassessment_missing", lambda: self.record_final(response))
        self.error("value_review_output_mismatch", lambda: self.record_final(self.final_response()))

    def test_historical_objection_cannot_disappear_from_the_repaired_finding(self):
        self.prepare_repair()
        response = self.final_response()
        response["value"]["objection_findings"] = []
        self.invoke(response)
        self.error("review_objection_history_incomplete", lambda: self.record_final(response))

    def test_final_repair_preserves_old_objections_in_the_later_slate_packet(self):
        self.prepare_repair()
        response = self.final_response()
        self.invoke(response)
        self.record_final(response)
        payload = {"id": "slate-after-repair", "route_id": "route", "dossier_id": "dossier-1", "role": "slate",
            "reviewer_id": "repair-reviewer", "author_id": "author", "context": {}}
        self.mutate(review_protocol.record_assignment, payload)
        packet = self.packet("slate-after-repair")
        self.assertEqual(set(packet["bar_ids"]), {"bar-b", "repair-final"})
        self.assertIn(self.objection["reason"], json.dumps(packet["historical_bars"]))
        self.assertIn('"insufficient"', json.dumps(packet["historical_bars"]))
        extra = self.repair_payload()
        extra["id"] = "another-favorable-sample"
        self.error("review_resampling_forbidden", lambda: self.mutate(review_protocol.record_assignment, extra))

    def test_later_ancestor_exposure_invalidates_repair_without_erasing_its_output(self):
        self.prepare_repair()
        response = self.final_response()
        self.invoke(response)
        self.record_final(response)
        self.assertTrue(review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, "repair-a")["ready"])
        self.expose("new-exposure")
        state = review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, "repair-a")
        self.assertFalse(state["ready"])
        self.assertEqual(state["output"], response)
        self.assertIn("review_context_repair_stale", [entry["code"] for entry in state["obligations"]])

    def test_source_continuation_after_repair_preserves_the_slot_and_reassessment_obligation(self):
        original = self.prepare_repair()
        request = self.review_response("bar", status="unresolved")
        request["source_requests"] = [{"id": "inventory", "kind": "inventory_page", "page": 0}]
        self.invoke(request)
        payload = self.repair_payload()
        payload.update(id="repair-source", context={"prior_assignment_id": "repair-a"})
        continued = self.mutate(review_protocol.record_assignment, payload)["result"]
        self.assertEqual(continued["session_id"], original["session_id"])
        self.assertEqual(self.packet("repair-source")["context_repair"], self.packet()["context_repair"])
        self.assertEqual(review_context_repair.slot_id(self.store.snapshot()["records"], continued), "assignment-bar-a")
        response = self.final_response()
        self.invoke(response, "repair-source")
        self.record_final(response, "repair-source")
        self.assertEqual(self.store.snapshot()["records"]["value_review"]["repair-final"]["payload"]["reassessment"], response["reassessment"])

    def test_completed_slate_can_repair_actual_contamination_in_its_inherited_bar_context(self):
        self.record_response("slate-a", "reviewer-a", self.review_response("slate", status="insufficient"))
        self.expose()
        self.probe_repair()
        state = review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, "assignment-slate-a")
        self.assertFalse(state["ready"])
        self.assertIn("review_source_parent_unverified", [item["code"] for item in state["obligations"]])
        payload = self.repair_payload()
        payload["role"] = "slate"
        payload["context"]["context_repair"]["assignment_id"] = "assignment-slate-a"
        saved = self.mutate(review_protocol.record_assignment, payload)["result"]
        self.assertEqual(review_context_repair.slot_id(self.store.snapshot()["records"], saved), "assignment-slate-a")
        self.assertEqual(self.packet()["context_repair"]["event_ids"], ["exposure"])
        self.assertIn(self.objection["reason"], json.dumps(self.packet()["scientific_history"]))

    def test_same_reviewer_can_reassess_in_an_explicit_new_verified_context(self):
        self.expose()
        self.probe_repair()
        payload = self.repair_payload()
        payload["reviewer_id"] = "reviewer-a"
        self.mutate(review_protocol.record_assignment, payload)
        response = self.final_response()
        self.invoke(response)
        self.record_final(response)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["value_review"]["repair-final"]["reviewer_id"], "reviewer-a")
        self.assertEqual(records["value_review"]["bar-a"]["payload"]["value"]["status"], "insufficient")
        self.assertTrue(review_protocol.assignment_state(records, self.artifacts, "repair-a")["ready"])

    def test_new_observed_ancestor_exposure_permits_a_bound_successor_repair(self):
        self.prepare_repair()
        first = self.final_response()
        self.invoke(first)
        self.record_final(first)
        self.expose("second-exposure")
        self.probe_repair("repair-probe-2")
        payload = self.repair_payload()
        payload.update(id="repair-b", reviewer_id="successor-reviewer")
        payload["context"]["context_repair"].update(assignment_id="repair-a",
            event_ids=["exposure", "second-exposure"], probe_id="repair-probe-2")
        saved = self.mutate(review_protocol.record_assignment, payload)["result"]
        self.assertEqual(review_context_repair.slot_id(self.store.snapshot()["records"], saved), "assignment-bar-a")
        response = self.final_response()
        response["reassessment"]["assignment_id"] = "repair-a"
        response["reassessment"]["findings"].append({"review_id": "repair-final", "disposition": "confirmed",
            "reason": "The prior finite finding and its unresolved objection remain unchanged.", "evidence": [self.evidence]})
        self.invoke(response, "repair-b")
        self.mutate(research_decisions.record_value_review,
            dict(response, id="repair-final-2", dossier_id="dossier-1", assignment_id="repair-b"))
        records = self.store.snapshot()["records"]
        self.assertTrue(review_protocol.assignment_state(records, self.artifacts, "repair-b")["ready"])
        self.assertFalse(review_protocol.assignment_state(records, self.artifacts, "repair-a")["ready"])
        self.assertEqual(set(self.packet("repair-b")["context_repair"]["historical_review_ids"]), {"bar-a", "repair-final"})

    def test_later_clean_probe_cannot_replace_corrupted_bound_repair_probe_evidence(self):
        self.prepare_repair()
        response = self.final_response()
        self.invoke(response)
        self.record_final(response)
        self.probe_repair("later-clean-probe")
        records = self.store.snapshot()["records"]
        reference = records["review_route_probe"]["repair-probe"]["response"]
        path = self.root / reference["path"]
        path.chmod(0o600)
        path.write_bytes(b"corrupt exact bound prospective repair probe")
        state = review_protocol.assignment_state(records, self.artifacts, "repair-a")
        self.assertFalse(state["ready"])
        self.assertIn("review_context_repair_unverified", [item["code"] for item in state["obligations"]])
        self.assertEqual(state["output"], response)
