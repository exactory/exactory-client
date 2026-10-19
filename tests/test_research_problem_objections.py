"""Material problem objections require observed resolution or focused adjudication."""

import json
from unittest.mock import patch

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.problem_delivery import export_problem, assess_post
import test_research_problem_delivery as fixtures


class ProblemObjectionTests(fixtures.ProblemDeliveryCase):
    def make_objection(self):
        return {"id": "unsupported-implication", "claim": "The stated implication exceeds the read comparator.",
            "reason": "The exact quantifier is unsupported.", "evidence": [self.supporting],
            "resolution_condition": "Supply the missing implication or narrow the supported claim."}

    def make_finding(self, status):
        return {"id": "unsupported-implication", "status": status,
            "reason": "The supplied comparator and changed paragraph establish this disposition.",
            "evidence": [self.supporting]}

    def prepare_reviews(self, *, bar_reviewer=None, result_reviewer=None):
        self.initialize_problem()
        self.selected = self.select_statement()
        self.item = self.record_full_reading()
        self.observe_openness()
        self.mutate(self.load_problem_module().record_openness, {"assignment_id": "openness-assignment"})
        self.mutate(self.load_problem_module().record_value, self.build_value([self.item]))
        from test_research_problem_assessment import build_assessment
        self.mutate(self.load_problem_module().record_assessment, build_assessment(self.selected))
        self.supporting = self.artifacts.put(b"The read comparator states an existential bound only.", "text/plain")
        for reviewer in ("first", "second"):
            output = self.build_review_output("bar")
            if reviewer == bar_reviewer:
                output["objections"] = [self.make_objection()]
            self.observe("bar", reviewer, output)
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            if bar_reviewer is not None:
                output["value"]["objection_findings"] = [self.make_finding("continuing")]
            if reviewer == result_reviewer:
                output["objections"] = [self.make_objection()]
            self.observe("result", reviewer, output)

    def refresh_reviews(self, status):
        changed = self.build_value([self.item], identifier="value-2")
        changed["motivation"][0]["text"] = "A narrower implication follows from the supplied comparator."
        self.mutate(self.load_problem_module().record_value, changed)
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            output["value"]["objection_findings"] = [self.make_finding(status if reviewer == "first" else "resolved")]
            self.observe("result", reviewer, output, identifier="refreshed-" + reviewer)

    def prepare_repaired_result(self, *, changed, slot="first"):
        self.prepare_reviews(result_reviewer="second")
        if changed:
            self.mutate(self.load_problem_module().record_value, self.build_value([self.item], identifier="value-copy"))
        self.mutate(protocol.record_context_event, {"id": "exposure", "assignment_id": "result-" + slot,
            "kind": "contamination", "source": "author_history", "reason": "The original assessor received author advocacy.",
            "evidence": [self.supporting]})
        def respond_probe(route, request, credential):
            packet = json.loads(request["input"][0]["content"])
            return self.build_provider_response({"allowed_control": packet["allowed_control"],
                "evidence_control": packet["evidence_control"], "excluded_controls": []})
        with patch("research_harness.review_transport.send_request", side_effect=respond_probe):
            self.mutate(protocol.probe_route, {"id": "repair-probe", "route_id": "route"}, credential="test-only")
        assignment = self.assign("result", "repaired-" + slot, identifier="repaired-result",
            context_repair={"assignment_id": "result-" + slot, "event_ids": ["exposure"], "probe_id": "repair-probe",
                "reason": "Reassess the original science in the independently verified context.", "evidence": [self.supporting]})
        return json.loads(self.artifacts.read(assignment["packet"]))

    def prepare_contested_repair(self):
        self.prepare_repaired_result(changed=False, slot="second")
        output = self.build_review_output("result")
        output["value"]["objection_findings"] = [self.make_finding("contested")]
        output["reassessment"] = {"assignment_id": "result-second", "findings": [{
            "review_id": "review-result-second", "disposition": "revised",
            "reason": "The supplied evidence leaves the historical objection contested.", "evidence": [self.supporting]}]}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "repaired-attempt", "assignment_id": "repaired-result"}, credential="test-only")
        self.mutate(decisions.record_value_review,
            dict(output, id="review-repaired-result", dossier_id=None, assignment_id="repaired-result"))
        self.assert_blocked_delivery()

    def assert_blocked_delivery(self, decision_code="research_objection_unresolved"):
        records = self.store.snapshot()["records"]
        reviews = decisions.assess_problem_reviews(records, self.artifacts)
        self.assertIn("research_objection_unresolved", {item["code"] for item in reviews["obligations"]})
        self.assertFalse(assess_post(records, self.artifacts)["ready"])
        payload = {"id": "decision", "action": "post", "review_ids": reviews["review_ids"],
            "bar_review_ids": reviews["bar_review_ids"], "reason": "All five assurances passed."}
        actions = ((lambda: self.mutate(self.load_problem_module().record_decision, payload), decision_code),
            (lambda: export_problem(self.store, self.root / "blocked-export"), "readiness_required"),
            (lambda: self.mutate(self.load_problem_module().record_posted,
                {"problem_id": "blocked-post", "export_directory": str(self.root / "blocked-export")}), "readiness_required"))
        for action, code in actions:
            with self.assertRaises(ResearchError) as caught:
                action()
            self.assertEqual(caught.exception.code, code)
            self.assertIn("research_objection_unresolved", {item["code"] for item in caught.exception.details["obligations"]})

    def assert_delivered(self):
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue(reviews["ready"], reviews["obligations"])
        self.mutate(self.load_problem_module().record_decision, {"id": "decision", "action": "post", "review_ids": reviews["review_ids"],
            "bar_review_ids": reviews["bar_review_ids"], "reason": "Every material objection has an evidenced disposition."})
        directory = self.root / "approved-export"
        export_problem(self.store, directory)
        saved = self.mutate(self.load_problem_module().record_posted,
            {"problem_id": "approved-post", "export_directory": str(directory)})["result"]
        self.assertEqual(saved["statement_id"], self.selected["id"])

    def observe_adjudication(self, disposition="not_upheld", reviewer="focused-third", review_ids=None, identifier="focused"):
        reviews = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        assignment = self.assign("adjudicator", reviewer, identifier=identifier, review_ids=review_ids or reviews["review_ids"],
            objection_id="unsupported-implication")
        packet = json.loads(self.artifacts.read(assignment["packet"]))
        self.assertEqual(packet["subject"], {"kind": "problem", "statement_id": self.selected["id"]})
        self.assertNotIn("dossier", packet)
        output = {"objection_id": "unsupported-implication", "disposition": disposition,
            "reason": "The exact supplied quantifier resolves the disputed implication.",
            "evidence": [self.supporting], "correction_admissible": None}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": identifier + "-attempt", "assignment_id": identifier}, credential="test-only")
        return self.mutate(protocol.record_adjudication, dict(output, id=identifier + "-finding", assignment_id=identifier))["result"]

    def test_current_first_bar_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(bar_reviewer="first")
        self.assert_blocked_delivery()

    def test_current_second_bar_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(bar_reviewer="second")
        self.assert_blocked_delivery()

    def test_current_first_result_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_blocked_delivery()

    def test_current_second_result_objection_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="second")
        self.assert_blocked_delivery()

    def test_continuing_predecessor_finding_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="first")
        self.refresh_reviews("continuing")
        self.assert_blocked_delivery()

    def test_contested_predecessor_finding_blocks_decision_export_and_posted(self):
        self.prepare_reviews(result_reviewer="second")
        self.refresh_reviews("contested")
        self.assert_blocked_delivery()

    def test_both_observed_evidenced_resolutions_allow_delivery(self):
        self.prepare_reviews(result_reviewer="first")
        self.refresh_reviews("resolved")
        self.assert_delivered()

    def test_resolution_of_a_bar_objection_allows_delivery(self):
        self.prepare_reviews(bar_reviewer="second")
        self.refresh_reviews("resolved")
        self.assert_delivered()

    def test_observed_focused_adjudication_allows_delivery_without_a_dossier(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication()
        self.assert_delivered()

    def test_upheld_adjudication_keeps_the_post_blocked(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication("upheld")
        self.assert_blocked_delivery()

    def test_unresolved_adjudication_keeps_the_post_blocked(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication("unresolved")
        self.assert_blocked_delivery()

    def test_focused_adjudication_can_resolve_a_bar_objection(self):
        self.prepare_reviews(bar_reviewer="second")
        self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])
        self.assert_delivered()

    def test_focused_adjudication_can_resolve_a_contested_result_history(self):
        self.prepare_reviews(result_reviewer="second")
        self.refresh_reviews("contested")
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        self.assert_delivered()

    def test_original_assessor_cannot_adjudicate_the_dispute(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_error("review_not_independent", lambda: self.observe_adjudication(reviewer="first"))

    def test_a_focused_adjudication_requires_two_independent_assessors(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_error("research_review_pair_required", lambda: self.observe_adjudication(
            review_ids=["review-bar-first", "review-result-first"]))
        self.assert_blocked_delivery()

    def test_a_focused_adjudication_requires_one_review_phase(self):
        self.prepare_reviews(result_reviewer="first")
        self.assert_error("research_review_phase_mismatch", lambda: self.observe_adjudication(
            review_ids=["review-bar-second", "review-result-first"]))
        self.assert_blocked_delivery()

    def test_new_value_evidence_cannot_replace_an_upheld_focused_adjudicator(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication("upheld")
        self.mutate(self.load_problem_module().record_value, self.build_value([self.item], identifier="value-copy"))
        self.assert_error("review_adjudicator_fixed", lambda: self.observe_adjudication(
            reviewer="different-fourth", identifier="replacement-focused"))
        self.assert_blocked_delivery("problem_evidence_unassessed")

    def test_new_value_evidence_retains_the_observed_focused_session(self):
        self.prepare_reviews(result_reviewer="first")
        original = self.observe_adjudication("upheld")
        self.refresh_reviews("continuing")
        original_pair = ["review-result-first", "review-result-second"]
        refreshed = self.observe_adjudication(identifier="refreshed-focused", review_ids=original_pair)
        records = self.store.snapshot()["records"]
        previous = records["review_assignment"][original["assignment_id"]]
        assignment = records["review_assignment"][refreshed["assignment_id"]]
        self.assertEqual(assignment["history_assignment_id"], previous["id"])
        self.assertEqual(assignment["session_id"], previous["session_id"])
        attempt = records["review_attempt"]["focused-attempt"]
        request = json.loads(self.artifacts.read(attempt["request"]))
        self.assertEqual(assignment["history"][:-1], request["input"])
        self.assertEqual(json.loads(assignment["history"][-1]["content"])["disposition"], "upheld")
        self.assertNotEqual(refreshed["problem_evidence_digest"], original["problem_evidence_digest"])
        self.assert_error("review_adjudicator_fixed", lambda: self.observe_adjudication(
            identifier="resampled-focused", review_ids=original_pair))
        self.assert_delivered()

    def test_result_context_repair_retains_prior_peer_material_objections(self):
        packet = self.prepare_repaired_result(changed=True)
        self.assertEqual(packet["problem_evidence"]["value"]["id"], "value-copy")
        self.assertEqual(packet["material_objections"], [self.make_objection()])

    def test_result_context_repair_withholds_the_current_peer_assessment(self):
        packet = self.prepare_repaired_result(changed=False)
        self.assertEqual(packet["material_objections"], [])
        self.assertNotIn("unsupported-implication", json.dumps(packet))

    def test_focused_adjudication_can_use_the_observed_original_pair_after_repair(self):
        self.prepare_contested_repair()
        self.observe_adjudication(review_ids=["review-result-first", "review-result-second"])
        self.assert_delivered()

    def test_focused_adjudication_can_use_the_current_repaired_pair_history(self):
        self.prepare_contested_repair()
        self.observe_adjudication()
        self.assert_delivered()

    def test_a_stale_adjudication_cannot_resolve_new_evidence(self):
        self.prepare_reviews(result_reviewer="first")
        self.observe_adjudication()
        self.refresh_reviews("continuing")
        self.assert_blocked_delivery()

    def test_an_unsupported_resolution_cannot_be_recorded(self):
        self.prepare_reviews(result_reviewer="first")
        changed = self.build_value([self.item], identifier="value-2")
        self.mutate(self.load_problem_module().record_value, changed)
        output = self.build_review_output("result")
        finding = self.make_finding("resolved")
        finding["evidence"] = []
        output["value"]["objection_findings"] = [finding]
        payload = self.observe("result", "first", output, record=False, identifier="unsupported-refresh")
        self.assert_error("invalid_strategy", lambda: self.mutate(decisions.record_value_review, payload))
        self.assert_blocked_delivery("problem_evidence_unassessed")

    def test_a_recorded_resolution_requires_its_evidence_to_remain_available(self):
        self.prepare_reviews(result_reviewer="first")
        self.refresh_reviews("resolved")
        (self.root / self.supporting["path"]).unlink()
        self.assert_blocked_delivery("artifact_missing")

    def test_an_adjudication_without_evidence_cannot_clear_the_objection(self):
        self.prepare_reviews(result_reviewer="first")
        self.assign("adjudicator", "focused-third", identifier="focused",
            review_ids=["review-result-first", "review-result-second"], objection_id="unsupported-implication")
        output = {"objection_id": "unsupported-implication", "disposition": "not_upheld",
            "reason": "The objection is said to be resolved without supporting evidence.",
            "evidence": [], "correction_admissible": None}
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(protocol.invoke_assignment, {"id": "focused-attempt", "assignment_id": "focused"}, credential="test-only")
        self.assert_error("invalid_strategy", lambda: self.mutate(protocol.record_adjudication,
            dict(output, id="unsupported-adjudication", assignment_id="focused")))
        self.assert_blocked_delivery()
