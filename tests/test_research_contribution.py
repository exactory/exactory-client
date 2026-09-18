"""The Grand Challenge search, the contribution analysis, the next pin and the round decision (design 7.3 to 7.6)."""

from research_harness import publication
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
