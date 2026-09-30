"""Observed strategic source requests preserve phase, evidence and review identity."""

import copy
import json
from unittest.mock import patch

from development_fixtures import DevelopmentCase
from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
from research_harness import research_decisions, review_protocol, strategy


class StrategicSourceHelpers:
    def assignment(self, identifier="source-a", **changes):
        payload = {"id": identifier, "route_id": "route", "dossier_id": "dossier-1",
                   "role": self.phase, "reviewer_id": "reviewer-a", "author_id": "author", "context": {}}
        payload.update(changes)
        return self.mutate(review_protocol.record_assignment, payload)["result"]

    def packet(self, identifier="source-a"):
        saved = self.store.snapshot()["records"]["review_assignment"][identifier]
        return json.loads(self.artifacts.read(saved["packet"]))

    def invoke(self, output, identifier="source-a"):
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response(output)):
            return review_protocol.invoke_assignment(self.store,
                {"id": "attempt-" + identifier, "assignment_id": identifier}, credential="fixture-only",
                expected_revision=self.store.revision, request_id="invoke-" + identifier)

    def request(self, requests, identifier="source-a"):
        output = self.review_response(self.phase, status="unresolved")
        output["value"] = {"status": "unresolved"}
        output["source_requests"] = requests
        self.invoke(output, identifier)
        return output

    def continue_review(self, identifier="source-b", parent="source-a", **changes):
        return self.assignment(identifier, context={"prior_assignment_id": parent}, **changes)

    def record_final(self, output, assignment="source-b", identifier="final-a"):
        return self.mutate(research_decisions.record_value_review,
            dict(output, id=identifier, dossier_id="dossier-1", assignment_id=assignment))

    def seed_bars(self):
        self.route()
        self.record_response("bar-a", "reviewer-a", self.review_response("bar"))
        self.record_response("bar-b", "reviewer-b", self.review_response("bar"))


class SlateSourceRequestTests(StrategicSourceHelpers, StrategyCase):
    phase = "slate"
    metadata = LiteratureCase.metadata

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.source = self.metadata(1, abstract="The exact requested primary comparator.")
        self.seed_bars()

    def test_requested_slate_source_continues_own_bar_history_then_finalizes_once(self):
        initial = self.assignment()
        output = self.request([{"id": "primary", "kind": "source", "version_id": self.source, "depth": "abstract"}])
        self.error("review_sources_pending", lambda: self.record_final(output, "source-a", "interim"))
        continued = self.continue_review()
        self.assertEqual(continued["session_id"], initial["session_id"])
        self.assertEqual(continued["session_id"], "assignment-bar-a")
        prior = self.store.snapshot()["records"]["review_attempt"]["attempt-source-a"]
        observed_input = json.loads(self.artifacts.read(prior["request"]))["input"]
        self.assertEqual(continued["history"], observed_input + [{"role": "assistant", "content": json.dumps(output, sort_keys=True)}])
        packet = self.packet("source-b")
        self.assertEqual(packet["source_inventory"], self.packet()["source_inventory"])
        self.assertEqual(json.loads(observed_input[-1]["content"])["dossier"], self.packet()["dossier"])
        self.assertIn("The exact requested primary comparator.", json.dumps(packet))
        self.assertEqual(packet["source_deliveries"][0]["status"], "delivered")
        final = self.review_response("slate")
        self.invoke(final, "source-b")
        self.record_final(final)
        self.error("review_resampling_forbidden", lambda: self.continue_review("replacement", "source-b"))
        self.error("review_resampling_forbidden", lambda: self.assignment("new-sample"))

    def test_unavailable_slate_request_survives_inventory_only_turn_and_blocks_final(self):
        self.assignment()
        request = {"id": "body", "kind": "source", "version_id": self.source, "depth": "fulltext"}
        self.request([request])
        self.continue_review()
        self.request([{"id": "inventory", "kind": "inventory_page", "page": 0}], "source-b")
        self.continue_review("source-c", "source-b")
        self.assertEqual(self.packet("source-c")["pending_source_requests"], [request])
        final = self.review_response("slate")
        self.invoke(final, "source-c")
        self.error("review_sources_pending", lambda: self.record_final(final, "source-c"))
        records = self.store.snapshot()["records"]
        self.assertEqual(set(records["value_review"]), {"bar-a", "bar-b"})
        self.assertFalse(records.get("research_support"))

    def test_continuation_cannot_change_phase_reviewer_route_author_or_dossier(self):
        self.assignment()
        self.request([{"id": "inventory", "kind": "inventory_page", "page": 0}])
        self.mutate(review_protocol.record_route, {"id": "other-route", "adapter": "openai_responses_v1", "model": "fixture-model",
            "endpoint": "https://api.openai.com/v1/responses", "configuration": {"max_output_tokens": 1500, "timeout_seconds": 5}})
        other = self.dossier("other-dossier")
        other.update(previous="dossier-1", material_change={"kind": "new_evidence",
            "reason": "A separately captured control requires its own dossier.",
            "evidence": [self.artifacts.put(b"A distinct observed control result.", "text/plain")], "prior_objections": []})
        self.mutate(strategy.record_strategy, other)
        for changed in ({"role": "bar"}, {"reviewer_id": "reviewer-b"}, {"route_id": "other-route"},
                        {"author_id": "other-author"}, {"dossier_id": "other-dossier"}):
            with self.subTest(changed=changed):
                self.error("review_source_continuation_mismatch", lambda: self.continue_review(**changed))

    def test_continuation_cannot_fork_or_create_a_third_reviewer_slot(self):
        self.assignment()
        self.request([{"id": "inventory", "kind": "inventory_page", "page": 0}])
        self.continue_review()
        self.error("review_source_continuation_fork", lambda: self.continue_review("fork"))
        self.assignment("other-slot", reviewer_id="reviewer-b")
        self.error("review_slots_fixed", lambda: self.assignment("third-slot", reviewer_id="reviewer-c"))

    def test_ancestor_contamination_invalidates_completed_slate_continuation(self):
        self.assignment()
        self.request([{"id": "inventory", "kind": "inventory_page", "page": 0}])
        self.continue_review()
        final = self.review_response("slate")
        self.invoke(final, "source-b")
        self.mutate(review_protocol.record_context_event, {"id": "exposure", "assignment_id": "source-a",
            "kind": "contamination", "source": "author_history", "reason": "The parent received excluded prior scores.",
            "evidence": [self.evidence]})
        state = review_protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, "source-b")
        self.assertFalse(state["ready"])
        self.assertEqual(state["output"], final)
        self.assertIn("review_source_parent_unverified", [item["code"] for item in state["obligations"]])


class ResultSourceRequestTests(StrategicSourceHelpers, DevelopmentCase):
    phase = "result"
    module = StrategyCase.module
    put = StrategyCase.put
    error = StrategyCase.error
    dossier = StrategyCase.dossier
    provider_response = StrategyCase.provider_response
    route = StrategyCase.route
    review_response = StrategyCase.review_response
    record_response = StrategyCase.record_response

    def intent(self, identifier="intent-1"):
        value = StrategyCase.intent(self, identifier)
        value["objective"] = {"kind": "native", "id": self.objective["id"]}
        value["authors"] = ["author", "cycle-author"]
        return value

    def setUp(self):
        super().setUp()
        self.prepared_candidate()
        self.execution_payload = self.store.snapshot()["records"]["execution"]["execution-run-1"]["payload"]
        self.evidence = self.execution_payload["outputs"][0]["artifact"]
        self.put("local_artifact", "instruction", {"id": "instruction", "path": "context/instruction.md",
            "artifact": self.artifacts.put(b"Investigate the bound and submit the supported result.", "text/plain")})
        self.mutate(strategy.record_intent, self.intent())
        dossier = self.dossier()
        dossier.update(phase="result", claims=[{"id": "bound", "statement": "The finite maximum is nine.",
            "scope": "The finite enumerated range.", "evidence": [self.evidence]}])
        self.mutate(strategy.record_strategy, dossier)
        self.seed_bars()

    def supported_response(self, assignment="source-b"):
        response = self.review_response("result")
        support = self.review(self.execution_payload, identifier="support-final")
        support["assessor"] = copy.deepcopy(self.packet(assignment)["assessor"])
        response["support"] = support
        return response

    def test_result_source_turn_defers_support_until_exact_delivery_and_final_response(self):
        initial = self.assignment()
        output = self.request([{"id": "primary", "kind": "source", "version_id": self.links[0]["version_id"], "depth": "abstract"}])
        self.error("review_sources_pending", lambda: self.record_final(output, "source-a", "interim"))
        self.assertFalse(self.store.snapshot()["records"].get("research_support"))
        self.assertFalse(self.store.snapshot()["records"].get("readiness_review"))
        continued = self.continue_review()
        self.assertEqual(continued["session_id"], initial["session_id"])
        packet = self.packet("source-b")
        self.assertEqual(packet["source_inventory"], self.packet()["source_inventory"])
        self.assertEqual(packet["support_contract"], self.packet()["support_contract"])
        attempt = self.store.snapshot()["records"]["review_attempt"]["attempt-source-a"]
        observed_input = json.loads(self.artifacts.read(attempt["request"]))["input"]
        self.assertEqual(continued["history"], observed_input + [{"role": "assistant", "content": json.dumps(output, sort_keys=True)}])
        self.assertEqual(json.loads(observed_input[-1]["content"])["readiness"], self.packet()["readiness"])
        self.assertEqual(packet["pending_source_requests"], [])
        final = self.supported_response()
        self.invoke(final, "source-b")
        self.record_final(final)
        records = self.store.snapshot()["records"]
        self.assertEqual(set(records["research_support"]), {"final-a"})
        self.assertEqual(set(records["readiness_review"]), {"support-final"})
        self.assertEqual(records["value_review"]["final-a"]["payload"]["support"], final["support"])
        self.error("review_resampling_forbidden", lambda: self.continue_review("resample", "source-b"))

    def test_unavailable_result_source_blocks_even_a_supplied_positive_native_support(self):
        self.assignment()
        missing = {"id": "missing", "kind": "source", "version_id": "arxiv:2601.00999v1", "depth": "fulltext"}
        self.request([missing])
        self.continue_review()
        final = self.supported_response()
        self.invoke(final, "source-b")
        self.error("review_sources_pending", lambda: self.record_final(final))
        records = self.store.snapshot()["records"]
        self.assertEqual(self.packet("source-b")["pending_source_requests"], [missing])
        self.assertFalse(records.get("research_support"))
        self.assertFalse(records.get("readiness_review"))

    def test_result_request_cannot_include_a_final_support_verdict(self):
        self.assignment()
        response = self.supported_response("source-a")
        response["value"]["status"] = "unresolved"
        response["source_requests"] = [{"id": "inventory", "kind": "inventory_page", "page": 0}]
        self.invoke(response)
        self.error("invalid_review_source_request", lambda: self.continue_review())
        self.error("invalid_review_source_request", lambda: self.record_final(response, "source-a"))
