"""The challenges ahead of a study: one current record for the whole study (design 7.1)."""

import copy

from research_harness import challenge
from research_harness.cli import status_report
from research_harness.report_views import status_summary
from test_research_synthesis import SynthesisCase


class GrandChallengeRecordTests(SynthesisCase):
    def prepared(self):
        self.configured()
        return self.read_source()

    def record(self, payload):
        return self.mutate(challenge.record_grand_challenge, payload)

    def test_a_study_owes_its_grand_challenge_before_ideation(self):
        link = self.prepared()
        self.assertIn("grand_challenge_missing", self.synthesis_codes())
        recorded = self.record(self.grand_challenge(link))["result"]
        self.assertIsNone(recorded["previous_id"])
        self.assertNotIn("grand_challenge_missing", self.synthesis_codes())
        self.assertEqual(challenge.resolve_criterion_ids(self.store.snapshot()["records"]), {"rc-general", "rc-finite"})

    def test_the_record_names_an_ultimate_goal_with_unique_criteria_and_evidence(self):
        link = self.prepared()
        payload = self.grand_challenge(link)
        near_only = copy.deepcopy(payload)
        near_only["challenges"][0]["horizon"] = "near_term"
        horizon = copy.deepcopy(payload)
        horizon["challenges"][1]["horizon"] = "someday"
        repeated = copy.deepcopy(payload)
        repeated["challenges"][1]["criteria"][0]["id"] = "rc-general"
        no_criteria = copy.deepcopy(payload)
        no_criteria["challenges"][0]["criteria"] = []
        no_evidence = copy.deepcopy(payload)
        no_evidence["challenges"][0]["evidence"] = []
        empty = dict(copy.deepcopy(payload), challenges=[])
        for name, wrong in (("near only", near_only), ("horizon", horizon), ("repeated", repeated),
                            ("no criteria", no_criteria), ("no evidence", no_evidence), ("empty", empty)):
            with self.subTest(case=name):
                self.assert_error("invalid_grand_challenge", lambda: self.record(wrong))
        unread = copy.deepcopy(payload)
        unread["challenges"][0]["evidence"] = [{"kind": "source", "link": self.read_source(2, complete=False)}]
        self.assert_error("reading_missing", lambda: self.record(unread))
        self.record(payload)

    def test_the_record_belongs_to_a_configured_research_study(self):
        unconfigured = self.grand_challenge(self.read_source())
        self.assert_error("configuration_missing", lambda: self.record(unconfigured))
        self.assertNotIn("grand_challenge", self.store.snapshot()["records"])

    def test_a_verification_workspace_takes_no_grand_challenge_record(self):
        self.configured("verification")
        payload = self.grand_challenge(self.read_source(2))
        self.assert_error("profile_inapplicable", lambda: self.record(payload))

    def test_a_source_that_two_challenges_cite_is_stored_once(self):
        link = self.prepared()
        payload = self.grand_challenge(link)
        self.assertEqual(payload["challenges"][0]["evidence"], payload["challenges"][1]["evidence"])
        recorded = self.record(payload)["result"]
        self.assertEqual([item["reference"] for item in recorded["evidence"]], payload["challenges"][0]["evidence"])

    def test_a_new_record_replaces_the_current_one_and_keeps_the_history(self):
        link = self.prepared()
        self.record(self.grand_challenge(link))
        changed = self.grand_challenge(link, "grand-challenge-2")
        changed["reason"] = "The near-term goal was reached; the unbounded case is next."
        changed["challenges"] = changed["challenges"][:1]
        recorded = self.record(changed)["result"]
        records = self.store.snapshot()["records"]
        self.assertEqual(recorded["previous_id"], "grand-challenge")
        self.assertEqual(challenge.find_current_challenge(records)["id"], "grand-challenge-2")
        self.assertEqual(challenge.resolve_criterion_ids(records), {"rc-general"})
        self.assertEqual(sorted(records["grand_challenge"]), ["grand-challenge", "grand-challenge-2"])

    def test_the_record_leaves_the_preparation_digest_unchanged(self):
        link = self.prepared()
        before = self.api().synthesis_report(self.store, "research")["preparation_digest"]
        self.record(self.grand_challenge(link))
        self.assertEqual(self.api().synthesis_report(self.store, "research")["preparation_digest"], before)

    def test_status_reports_the_current_record(self):
        link = self.prepared()
        self.record(self.grand_challenge(link))
        report = status_report(self.store)
        self.assertEqual(report["grand_challenge"]["id"], "grand-challenge")
        summary = status_summary(report)["grand_challenge"]
        self.assertEqual(summary["challenges"][0], {"id": "general-bound", "horizon": "ultimate",
                                                   "criterion_ids": ["rc-general"], "omitted_criteria": 0})
        self.assertEqual(summary["omitted_challenges"], 0)
