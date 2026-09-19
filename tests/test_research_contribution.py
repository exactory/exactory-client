"""The Grand Challenge search, the contribution analysis, the next pin and the round decision (design 7.3 to 7.6)."""

import copy
import json
from unittest.mock import patch

from research_harness import contribution, predictions, publication, rounds
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
        # The summary still says that the selected bundle has its analysis, so the author sees the next pin is open.
        self.assertTrue(rounds.round_state(self.store.snapshot()["records"], self.artifacts)["analysis"])

    def test_a_full_reading_outside_the_citation_graph_serves_a_step_and_keeps_the_bundle_current(self):
        # The evaluate skill tells the author to read in full the sources a step rests on, after the pin.
        bundle = self.measured()
        link = self.read_source(50)
        payload = self.analysis_payload(bundle, "one")
        payload["steps"][0]["evidence"] = [{"kind": "source", "link": copy.deepcopy(link)}]
        self.record(payload)
        records = self.store.snapshot()["records"]
        self.assertEqual(publication._bundle(records, Evaluation(records, self.artifacts))["digest"], bundle["digest"])

    def test_a_malformed_analysis_fails_with_its_own_code(self):
        bundle = self.measured()
        payload = self.analysis_payload(bundle, "one")
        no_position_evidence = copy.deepcopy(payload)
        no_position_evidence["position"]["evidence"] = []
        no_community_evidence = copy.deepcopy(payload)
        no_community_evidence["steps"][0]["community"]["evidence"] = []
        wide_review_item = copy.deepcopy(payload)
        wide_review_item["position"]["evidence"] = [{"kind": "review", "review_id": "measure-one-1", "extra": 1}]
        no_steps = dict(copy.deepcopy(payload), steps=[])
        unknown_position = copy.deepcopy(payload)
        unknown_position["position"]["criterion_ids"] = ["rc-absent"]
        listed_id = copy.deepcopy(payload)
        listed_id["steps"][0]["id"] = ["step-one"]
        for name, wrong in (("position evidence", no_position_evidence), ("community evidence", no_community_evidence),
                            ("review item", wide_review_item), ("steps", no_steps), ("position criterion", unknown_position),
                            ("listed id", listed_id)):
            with self.subTest(case=name):
                self.assert_error("invalid_contribution_analysis", lambda: self.record(wrong))
        self.record(payload)

    def test_a_bundle_pinned_before_the_search_snapshot_takes_any_grand_challenge_search(self):
        self.record_purpose("grand_challenge", "gc-early")
        bundle = self.pin(measure=False)
        legacy = {key: value for key, value in self.store.snapshot()["records"]["publication_bundle"][bundle["id"]].items()
                  if key != "search_ids"}
        self.write_record("publication_bundle", legacy)
        self.measure(bundle, "one")
        payload = self.analysis_payload(bundle, "one")
        payload["searches"] = ["gc-early"]
        self.assertEqual(self.record(payload)["result"]["bundle_digest"], bundle["digest"])

    def test_reviews_recorded_before_the_changes_field_leave_nothing_to_dispose_of(self):
        bundle = self.measured()
        for saved in predictions.select_measurement_reviews(self.store.snapshot()["records"], bundle):
            core = {key: value for key, value in saved["core"].items() if key != "changes_for_maximum"}
            self.write_record("manuscript_review", dict(saved, core=core))
        payload = self.analysis_payload(bundle, "one")
        self.assertEqual(payload["reviewer_changes"], [])
        self.record(payload)

    def test_an_analysis_needs_the_study_grand_challenge(self):
        bundle = self.measured()
        with patch("research_harness.contribution.resolve_criterion_ids", return_value=set()):
            self.assert_error("grand_challenge_missing", lambda: self.record(self.analysis_payload(bundle, "one")))


class NextBundleTests(RoundsCase):
    def test_a_fourth_predicting_assessor_is_refused_so_a_complete_measurement_stays_complete(self):
        bundle = self.pin(identifier="paper-first", measure=False)
        self.measure(bundle, "one")
        fourth = self.prediction_payload(bundle, "fourth-predictor")
        self.assert_error("manuscript_prediction_excess", lambda: self.mutate(predictions.record_prediction, fourth))
        records = self.store.snapshot()["records"]
        self.assertIsNotNone(predictions.select_measurement_reviews(records, bundle))
        # The measured bundle still owes its analysis, so the next pin still waits for it.
        self.assertEqual(contribution.find_bundle_owing_analysis(records)["id"], "paper-first")
        self.assert_error("contribution_analysis_missing", lambda: self.pin(identifier="paper-second", measure=False))

    def test_the_next_bundle_waits_for_the_analysis_of_a_measured_bundle(self):
        self.pin(identifier="paper-first", measure=False)
        second = self.pin(identifier="paper-second", measure=False)
        self.measure(second, "two")
        self.assert_error("contribution_analysis_missing", lambda: self.pin(identifier="paper-third", measure=False))
        self.analyze(second, "two")
        third = self.pin(identifier="paper-third", measure=False)
        self.assertEqual(publication.publication_report(self.store)["bundle"]["digest"], third["digest"])


class RoundContributionTests(RoundsCase):
    def round_codes(self):
        records = self.store.snapshot()["records"]
        return {item["code"] for item in rounds.round_state(records, self.artifacts)["obligations"]}

    def test_a_decision_needs_the_measurement_and_the_analysis_of_its_bundle(self):
        bundle = self.pin(measure=False)
        self.assert_error("manuscript_measurement_missing", lambda: self.mutate(rounds.record_round, self.decision_payload(bundle)))
        self.assertIn("manuscript_measurement_missing", self.round_codes())
        self.measure(bundle, "one")
        self.assert_error("contribution_analysis_missing", lambda: self.mutate(rounds.record_round, self.decision_payload(bundle)))
        self.assertIn("contribution_analysis_missing", self.round_codes())
        analysis = self.analyze(bundle, "one")
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.assertIn(analysis["payload"]["steps"][0]["id"], [c["id"] for c in decision["payload"]["candidates"]])

    def test_the_candidates_list_every_step_of_the_analysis(self):
        bundle = self.pin()
        step_id = "step-" + bundle["id"]
        omitted = self.decision_payload(bundle)
        omitted["candidates"] = [c for c in omitted["candidates"] if c["id"] != step_id]
        self.assert_error("contribution_step_missing", lambda: self.mutate(rounds.record_round, omitted))
        turned = self.decision_payload(bundle)
        next(c for c in turned["candidates"] if c["id"] == step_id)["direction"] = "horizontal"
        self.assert_error("contribution_step_missing", lambda: self.mutate(rounds.record_round, turned))
        reworded = self.decision_payload(bundle)
        next(c for c in reworded["candidates"] if c["id"] == step_id)["statement"] = "Another statement than the step's."
        self.assert_error("contribution_step_missing", lambda: self.mutate(rounds.record_round, reworded))
        self.mutate(rounds.record_round, self.decision_payload(bundle))

    def test_a_continue_goal_names_the_grand_challenge_criteria_it_advances(self):
        bundle = self.pin()
        for identifiers in ([], ["rc-absent"]):
            payload = self.decision_payload(bundle)
            payload["next"]["goal"]["criterion_ids"] = identifiers
            with self.subTest(criterion_ids=identifiers):
                self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round, payload))
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        self.assertEqual(decision["payload"]["next"]["goal"]["criterion_ids"], ["rc-general"])

    def test_a_continue_review_answers_the_grand_challenge_check(self):
        bundle = self.pin()
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        review = self.review_payload(decision)
        self.assertIn("grand_challenge", [check["kind"] for check in review["checks"]])
        partial = copy.deepcopy(review)
        partial["checks"] = [check for check in partial["checks"] if check["kind"] != "grand_challenge"]
        self.assert_error("invalid_round", lambda: self.mutate(rounds.record_round_review, partial))
        self.mutate(rounds.record_round_review, review)

    def test_the_round_packet_and_the_summary_carry_the_grand_challenge_and_the_analysis(self):
        from research_harness.review_delivery import deliver_round
        bundle = self.pin()
        self.mutate(rounds.record_round, self.decision_payload(bundle))
        deliver_round(self.store, self.root / "round-packet")
        manifest = json.loads((self.root / "round-packet" / "inputs.json").read_text())
        self.assertEqual(manifest["grand_challenge"]["challenges"][0]["criteria"][0]["id"], "rc-general")
        self.assertEqual(manifest["contribution_analysis"]["steps"][0]["id"], "step-" + bundle["id"])
        records = self.store.snapshot()["records"]
        self.assertTrue(rounds.round_summary(rounds.round_state(records, self.artifacts))["analysis"])
