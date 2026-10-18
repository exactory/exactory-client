"""Each post prerequisite remains an explicit, local gate obligation."""

import copy
from unittest.mock import patch

from test_research_problem_delivery import ProblemDeliveryCase
from research_harness.evidence import digest
from research_harness.gates import gate_state
from research_harness.problem_claims import compute_problem_evidence_digest


class ProblemGateTests(ProblemDeliveryCase):
    def setUp(self):
        super().setUp()
        self.prepare_ready()
        self.record_post_decision()

    def collect_gate_codes(self, records):
        return {row["code"] for row in gate_state(records, self.artifacts, "post")["obligations"]}

    def update_decision_evidence_digest(self, records):
        records["problem_decision"]["decision"]["problem_evidence_digest"] = compute_problem_evidence_digest(records, self.artifacts)

    def test_missing_current_statement_is_explicit(self):
        records = self.store.snapshot()["records"]
        records.pop("problem_selection")
        self.assertIn("problem_statement_missing", self.collect_gate_codes(records))

    def test_unresolved_openness_is_the_only_missing_condition(self):
        records = self.store.snapshot()["records"]
        records["problem_openness"]["openness-assignment"]["outcome"] = "unresolved"
        self.update_decision_evidence_digest(records)
        self.assertEqual(self.collect_gate_codes(records), {"problem_openness_unresolved"})

    def test_changed_neighbours_make_openness_stale_at_the_gate(self):
        records = self.store.snapshot()["records"]
        records["problem_statement"]["statement-1"]["neighbours"] = ["A newly declared formulation"]
        self.update_decision_evidence_digest(records)
        self.assertEqual(self.collect_gate_codes(records), {"problem_openness_stale"})

    def test_already_solved_is_an_explicit_settling_work_obligation(self):
        import test_research_problem_openness_minimum as fixtures
        work = self.find_item_version()
        search = fixtures.ProblemOpennessMinimumTests.search(self, [work])
        records = self.store.snapshot()["records"]
        finding = records["problem_openness"]["openness-assignment"]
        finding["outcome"] = "already_solved"
        finding["searched_at"] = "2026-10-17"
        finding["minimum"].update(searches=[{"formulation": self.selected["statement"], "search_digest": digest(search)}],
                                  reading_digests=[digest(records["reading"]["read-full"])], settling_work_id=work)
        finding["evidence"] = [digest(search), digest(records["reading"]["read-full"])]
        self.update_decision_evidence_digest(records)
        self.assertEqual(self.collect_gate_codes(records), {"problem_already_solved"})

    def find_item_version(self):
        return self.store.snapshot()["records"]["reading"]["read-full"]["version_id"]

    def test_missing_openness_and_value_are_explicit(self):
        for kind, code in (("problem_openness", "problem_openness_missing"), ("problem_value", "problem_value_missing")):
            with self.subTest(kind=kind):
                records = self.store.snapshot()["records"]
                records.pop(kind)
                self.update_decision_evidence_digest(records)
                self.assertEqual(self.collect_gate_codes(records), {code})

    def test_missing_full_reading_has_its_own_gate_code_without_missing_review_noise(self):
        records = self.store.snapshot()["records"]
        records["reading"].pop("read-full")
        self.update_decision_evidence_digest(records)
        self.assertEqual(self.collect_gate_codes(records), {"problem_value_reading_missing"})

    def test_missing_computation_has_its_own_gate_code_without_review_noise(self):
        records = self.store.snapshot()["records"]
        for section in ("motivation", "current_state"):
            records["problem_value"]["value-1"][section][0]["evidence"] = [
                {"kind": "computation", "execution_id": "missing-execution", "output_id": "result", "formal_check": None}]
        records["problem_decision"]["decision"]["problem_evidence_digest"] = None
        self.assertEqual(self.collect_gate_codes(records), {"problem_value_computation_missing"})

    def test_missing_bar_pair_and_result_pair_are_independent_gate_conditions(self):
        for identifier, code in (("review-bar-second", "problem_bar_pending"), ("review-result-second", "problem_review_pair_required")):
            records = self.store.snapshot()["records"]
            records["value_review"].pop(identifier)
            self.assertIn(code, self.collect_gate_codes(records))

    def test_bar_mismatch_is_reported_by_the_post_gate(self):
        records = self.store.snapshot()["records"]
        records["workspace"]["problem"]["field"] = "A changed mathematical field"
        self.assertIn("review_bar_mismatch", self.collect_gate_codes(records))

    def test_evidence_unassessed_and_decision_stale_are_separate_conditions(self):
        records = self.store.snapshot()["records"]
        records["problem_value"]["value-1"]["motivation"][0]["text"] = "A changed motivation paragraph."
        self.assertIn("problem_decision_stale", self.collect_gate_codes(records))
        self.update_decision_evidence_digest(records)
        self.assertEqual(self.collect_gate_codes(records), {"problem_evidence_unassessed"})

    def test_decision_missing_is_the_only_missing_condition_after_passed_reviews(self):
        records = self.store.snapshot()["records"]
        records.pop("problem_decision")
        self.assertEqual(self.collect_gate_codes(records), {"problem_decision_missing"})

    def test_recorded_post_is_the_only_post_gate_obligation(self):
        records = self.store.snapshot()["records"]
        records["problem_post"] = {"posted": {"id": "posted", "problem_id": "remote-id",
            "statement_digest": self.load_problem_module().compute_statement_digest(self.selected)}}
        self.assertEqual(self.collect_gate_codes(records), {"problem_already_posted"})

    def test_not_passed_problem_assurance_is_explicit_at_the_gate(self):
        records = self.store.snapshot()["records"]
        # This isolated snapshot models a retained adverse result; its unchanged
        # observed-output binding remains a separate obligation as well.
        records["value_review"]["review-result-first"]["payload"]["value"]["assurances"][0]["status"] = "not_assessed"
        self.assertIn("problem_assurance_pending", self.collect_gate_codes(records))


class ProblemSourceGateTests(ProblemDeliveryCase):
    def test_pending_observed_source_request_blocks_the_post_gate(self):
        from research_harness import review_protocol
        self.initialize_problem()
        self.select_statement()
        item = self.record_full_reading()
        self.prepare_review_route()
        self.supporting = self.artifacts.put(b"Source request evidence.", "text/plain")
        output = self.build_review_output("bar")
        output["value"]["status"] = "unresolved"
        output["source_requests"] = [{"id": "requested-source", "kind": "source", "version_id": item["version_id"], "depth": "fulltext"}]
        assignment = self.assign("bar", "first")
        with patch("research_harness.review_transport.send_request", return_value=self.build_provider_response(output)):
            self.mutate(review_protocol.invoke_assignment, {"id": "request-attempt", "assignment_id": assignment["id"]}, credential="test-only")
        report = gate_state(self.store.snapshot()["records"], self.artifacts, "post")
        pending = [row for row in report["obligations"] if row["code"] == "review_sources_pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["assignment_id"], "bar-first")
        self.assertFalse(report["ready"])
