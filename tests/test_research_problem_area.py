"""Problem statements retain the immutable subject area of their field bars."""

from research_harness import research_decisions, review_protocol
from research_harness.errors import ResearchError
from test_research_problem_claims import ProblemCase
from test_research_problem_delivery import ProblemDeliveryCase


class ProblemStatementAreaTests(ProblemCase):
    def test_initial_statement_in_another_area_is_refused_without_writing(self):
        self.initialize_problem()
        before = self.store.snapshot()
        self.assert_error("problem_area_mismatch", lambda: self.select_statement(area="math.NT"))
        self.assertEqual(self.store.snapshot(), before)

    def test_rejected_area_change_preserves_the_selected_statement(self):
        self.initialize_problem()
        self.select_statement()
        before = self.store.snapshot()
        self.assert_error("problem_area_mismatch", lambda: self.select_statement(
            identifier="different-area", area="math.NT"))
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(before["records"]["problem_selection"]["current"], {"statement_id": "statement-1"})

    def test_revised_statement_in_the_record_area_keeps_its_established_scope(self):
        marker = self.initialize_problem()
        self.select_statement()
        changed = self.select_statement(identifier="statement-2", statement="A revised exact combinatorial problem.")
        records = self.store.snapshot()["records"]
        self.assertEqual(changed["area"], "math.CO")
        self.assertEqual(records["problem_selection"]["current"], {"statement_id": "statement-2"})
        self.assertEqual(records["workspace"]["problem"], marker)


class ProblemBarAreaTests(ProblemDeliveryCase):
    def test_legacy_foreign_area_statement_cannot_use_the_record_bars(self):
        self.prepare_ready()
        records = self.store.snapshot()["records"]
        # Reproduce an already recorded malformed statement; the bar packets
        # still match the immutable marker and must not authorize this area.
        records["problem_statement"]["statement-1"]["area"] = "math.NT"
        bars = [records["value_review"]["review-bar-" + reviewer] for reviewer in ("first", "second")]
        with self.assertRaises(ResearchError) as caught:
            review_protocol.validate_problem_bars(records, self.artifacts, bars)
        self.assertEqual(caught.exception.code, "review_bar_mismatch")
        self.assertEqual(caught.exception.details["statement_id"], "statement-1")
        assessed = research_decisions.assess_problem_reviews(records, self.artifacts, check_evidence=False)
        self.assertFalse(assessed["ready"])
        self.assertIn("review_bar_mismatch", {item["code"] for item in assessed["obligations"]})

    def test_area_guard_preserves_bars_before_and_after_a_matching_statement(self):
        self.prepare_ready()
        records = self.store.snapshot()["records"]
        bars = [records["value_review"]["review-bar-" + reviewer] for reviewer in ("first", "second")]
        review_protocol.validate_problem_bars(records, self.artifacts, bars)
        records.pop("problem_selection")
        review_protocol.validate_problem_bars(records, self.artifacts, bars)
