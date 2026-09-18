"""The Grand Challenge search, the contribution analysis, the next pin and the round decision (design 7.3 to 7.6)."""

import copy
from unittest.mock import patch

from research_harness import contribution, predictions, publication
from research_harness.evaluation import Evaluation
from rounds_fixtures import RoundsCase


class GrandChallengeSearchTests(RoundsCase):
    def current_bundle(self):
        records = self.store.snapshot()["records"]
        return publication._bundle(records, Evaluation(records, self.artifacts))

    def test_a_grand_challenge_search_keeps_the_pinned_bundle_current(self):
        bundle = self.pin()
        self.record_purpose("grand_challenge", "gc-after-pin")
        self.assertEqual(self.current_bundle()["digest"], bundle["digest"])

    def test_an_ordinary_search_after_the_pin_breaks_readiness(self):
        self.pin()
        self.record_purpose("recent", "recent-after-pin")
        self.assert_error("readiness_required", self.current_bundle)

    def test_a_new_grand_challenge_record_keeps_the_pinned_bundle_current(self):
        from research_harness import challenge
        bundle = self.pin()
        self.mutate(challenge.record_grand_challenge, self.grand_challenge(self.links[0], "grand-challenge-2"))
        self.assertEqual(self.current_bundle()["digest"], bundle["digest"])


class ContributionAnalysisTests(RoundsCase):
    def record(self, payload):
        return self.mutate(contribution.record_contribution_analysis, payload)

    def measured(self, claims=None):
        bundle = self.pin(claims, measure=False)
        self.measure(bundle, "one")
        self.record_purpose("grand_challenge", "gc-one")
        return bundle

    def test_the_measurement_reviews_are_the_three_paired_reviews(self):
        bundle = self.pin(measure=False)
        self.assertIsNone(predictions.select_measurement_reviews(self.store.snapshot()["records"], bundle))
        self.measure(bundle, "one")
        reviews = predictions.select_measurement_reviews(self.store.snapshot()["records"], bundle)
        self.assertEqual([saved["id"] for saved in reviews], ["measure-one-1", "measure-one-2", "measure-one-3"])

    def test_an_analysis_disposes_of_every_reviewer_change_and_binds_the_bundle(self):
        bundle = self.measured()
        payload = self.analysis_payload(bundle, "one")
        self.assertEqual(len(payload["reviewer_changes"]), 3)
        omitted = copy.deepcopy(payload)
        omitted["reviewer_changes"] = omitted["reviewer_changes"][1:]
        self.assert_error("reviewer_change_missing", lambda: self.record(omitted))
        refused = []
        repeated = copy.deepcopy(payload)
        repeated["reviewer_changes"].append(copy.deepcopy(payload["reviewer_changes"][0]))
        refused.append(repeated)
        unknown_step = copy.deepcopy(payload)
        unknown_step["reviewer_changes"][0]["step_id"] = "absent"
        refused.append(unknown_step)
        rejected_with_step = copy.deepcopy(payload)
        rejected_with_step["reviewer_changes"][0]["disposition"] = "rejected"
        refused.append(rejected_with_step)
        for number, wrong in enumerate(refused):
            with self.subTest(case=number):
                self.assert_error("invalid_contribution_analysis", lambda: self.record(wrong))
        recorded = self.record(payload)["result"]
        self.assertEqual((recorded["bundle_id"], recorded["bundle_digest"]), (bundle["id"], bundle["digest"]))
        self.assertEqual(contribution.find_analysis(self.store.snapshot()["records"], bundle["digest"])["id"], "analysis-one")
        self.assert_error("contribution_analysis_duplicate", lambda: self.record(dict(payload, id="analysis-again")))

    def test_an_analysis_needs_a_complete_measurement_of_the_selected_bundle(self):
        bundle = self.pin(measure=False)
        self.record_purpose("grand_challenge", "gc-one")
        self.assert_error("manuscript_measurement_missing", lambda: self.record(self.analysis_payload(bundle, "one")))
        self.measure(bundle, "one")
        stale = self.analysis_payload(bundle, "one")
        stale["bundle_digest"] = "0" * 64
        self.assert_error("contribution_analysis_stale", lambda: self.record(stale))

    def test_the_analysis_needs_its_own_grand_challenge_search_after_the_pin(self):
        self.record_purpose("grand_challenge", "gc-early")
        bundle = self.pin(measure=False)
        self.measure(bundle, "one")
        early = self.analysis_payload(bundle, "one")
        early["searches"] = ["gc-early"]
        self.assert_error("contribution_search_missing", lambda: self.record(early))
        self.record_purpose("grand_challenge", "gc-one")
        ordinary = self.analysis_payload(bundle, "one")
        ordinary["searches"] = ["gc-one", "direct"]
        self.assert_error("invalid_contribution_analysis", lambda: self.record(ordinary))
        self.record(self.analysis_payload(bundle, "one"))

    def test_steps_build_on_current_claims_and_name_the_grand_challenge_criteria(self):
        bundle = self.measured(self.claims("wider", superseded=("bound",)))
        payload = self.analysis_payload(bundle, "one")
        self.assertEqual(payload["steps"][0]["builds_on"], ["wider"])
        superseded = copy.deepcopy(payload)
        superseded["steps"][0]["builds_on"] = ["bound"]
        self.assert_error("contribution_claim_unknown", lambda: self.record(superseded))
        for key, value in (("criterion_ids", ["rc-absent"]), ("reach", "someday"), ("direction", "diagonal"), ("builds_on", [])):
            wrong = copy.deepcopy(payload)
            wrong["steps"][0][key] = value
            with self.subTest(key=key):
                self.assert_error("invalid_contribution_analysis", lambda: self.record(wrong))
        self.record(payload)

    def test_an_analysis_binds_the_selected_bundle_after_later_work_made_it_stale(self):
        bundle = self.measured()
        self.record_purpose("recent", "recent-late")
        recorded = self.record(self.analysis_payload(bundle, "one"))["result"]
        self.assertEqual(recorded["bundle_digest"], bundle["digest"])

    def test_an_analysis_needs_the_study_grand_challenge(self):
        bundle = self.measured()
        with patch("research_harness.contribution.resolve_criterion_ids", return_value=set()):
            self.assert_error("grand_challenge_missing", lambda: self.record(self.analysis_payload(bundle, "one")))
