"""Problem reviewers can resolve an objection independently of an unused adjudication."""

import test_research_problem_delivery as delivery_fixtures
import test_research_problem_objections as objection_fixtures

from research_harness import research_decisions as decisions, review_protocol as protocol


class ProblemAdjudicationResolutionTests(delivery_fixtures.ProblemDeliveryCase):
    make_objection = objection_fixtures.ProblemObjectionTests.make_objection
    make_finding = objection_fixtures.ProblemObjectionTests.make_finding
    prepare_reviews = objection_fixtures.ProblemObjectionTests.prepare_reviews
    observe_adjudication = objection_fixtures.ProblemObjectionTests.observe_adjudication
    assert_delivered = objection_fixtures.ProblemObjectionTests.assert_delivered
    assert_blocked_delivery = objection_fixtures.ProblemObjectionTests.assert_blocked_delivery

    def prepare_bar_adjudication(self):
        self.prepare_reviews(bar_reviewer="second")
        changed = self.build_value([self.item], identifier="value-2")
        changed["motivation"][0]["text"] = "The supplied comparator supports the exact stated implication."
        self.mutate(self.load_problem_module().record_value, changed)
        return self.observe_adjudication(review_ids=["review-bar-first", "review-bar-second"])

    def observe_later_resolutions(self, first_status="resolved"):
        self.resolution_evidence = self.artifacts.put(
            b"The exact implication follows from the comparator's stated hypotheses.", "text/plain")
        for reviewer in ("first", "second"):
            output = self.build_review_output("result")
            finding = self.make_finding(first_status if reviewer == "first" else "resolved")
            finding["evidence"] = [self.resolution_evidence]
            output["value"]["objection_findings"] = [finding]
            self.observe("result", reviewer, output, identifier="resolved-" + reviewer)

    def remove_adjudication_response(self):
        response = self.store.snapshot()["records"]["review_attempt"]["focused-attempt"]["response"]
        (self.root / response["path"]).unlink()

    def test_both_evidenced_result_resolutions_allow_post_without_adjudication_response(self):
        adjudication = self.prepare_bar_adjudication()
        self.observe_later_resolutions()
        self.remove_adjudication_response()
        records = self.store.snapshot()["records"]
        self.assertEqual(records["review_adjudication"][adjudication["id"]], adjudication)
        focused = protocol.adjudication_state(records, self.artifacts, adjudication["id"])
        self.assertFalse(focused["ready"])
        self.assertIn("review_output_unavailable", {item["code"] for item in focused["obligations"]})
        self.assert_delivered()

    def test_unavailable_adjudication_and_continuing_result_block_post(self):
        self.prepare_bar_adjudication()
        self.observe_later_resolutions(first_status="continuing")
        self.remove_adjudication_response()
        assessment = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertIn("review_output_unavailable", {item["code"] for item in assessment["obligations"]})
        self.assert_blocked_delivery()

    def test_available_adjudication_resolves_a_continuing_result(self):
        self.prepare_bar_adjudication()
        self.observe_later_resolutions(first_status="continuing")
        self.assert_delivered()

    def test_missing_resolution_evidence_and_unavailable_adjudication_block_post(self):
        self.prepare_bar_adjudication()
        self.observe_later_resolutions()
        self.remove_adjudication_response()
        (self.root / self.resolution_evidence["path"]).unlink()
        assessment = decisions.assess_problem_reviews(self.store.snapshot()["records"], self.artifacts)
        self.assertTrue({"artifact_missing", "review_output_unavailable", "research_objection_unresolved"}
                        <= {item["code"] for item in assessment["obligations"]})
        self.assert_blocked_delivery("artifact_missing")
