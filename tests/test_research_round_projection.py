"""Round reviewers can quote the exact references delivered to them."""

import copy
import json

from integration_fixtures import approve_publication_stop
from rounds_fixtures import RoundsCase
from source_limited_fixtures import SourceLimitedCase
from research_harness import rounds
from research_harness.review_delivery import deliver_round


class RoundProjectionTests(SourceLimitedCase):
    goal = RoundsCase.goal
    round_evidence = RoundsCase.round_evidence
    decision_payload = RoundsCase.decision_payload
    review_payload = RoundsCase.review_payload

    def setUp(self):
        super().setUp()
        self.prepare_delivery()
        self.bundle = self.scoped_manuscript()
        approve_publication_stop(self, self.bundle)

    def export_review(self, action):
        decision = self.mutate(rounds.record_round, self.decision_payload(
            self.bundle, decision=action))["result"]
        directory = self.root / ("round-" + decision["id"])
        deliver_round(self.store, directory)
        packet = json.loads((directory / "inputs.json").read_text())
        from research_harness.evaluation import Evaluation
        from research_harness.publication_scope import find_publication_scope
        from research_harness.review_packets import round_packet
        from research_harness.scientific_delivery import encode_delivery, project_delivery
        records = self.store.snapshot()["records"]
        native = round_packet(records, self.bundle, decision,
                              Evaluation(records, self.artifacts, retention_budget_bytes=0))
        expected, derivatives = project_delivery(
            records, self.artifacts, find_publication_scope(records), native,
            manuscript_files=[self.bundle["files"][kind]["artifact"]
                              for kind in ("pdf", "abstract", "bibliography", "claims")])
        self.assertEqual((directory / "inputs.json").read_bytes(), encode_delivery(expected))
        for path, data in derivatives.items():
            self.assertEqual((directory / path).read_bytes(), data)
        reference = packet["decision"]["payload"]["candidates"][0]["evidence"][1]
        self.assertEqual(reference["original_locator"]["pointer"], "/result")
        self.assertTrue(reference["derivative"])
        review = self.review_payload(decision, verdict="not_approved")
        for check in review["checks"]:
            check["evidence"] = [copy.deepcopy(reference)]
        return decision, review, reference

    def test_exported_result_records_unchanged_for_stop_and_demand(self):
        for action in ("stop", "continue"):
            with self.subTest(action=action):
                decision, review, reference = self.export_review(action)
                saved = self.mutate(rounds.record_round_review, review)["result"]
                self.assertEqual(saved["payload"], review)
                mapping = saved["evidence"][0]
                self.assertEqual(mapping["reference"], reference)
                self.assertEqual(mapping["native_reference"], self.result_evidence(self.execution_payload))
                self.assertEqual(mapping["projection_binding"], {
                    "decision_digest": decision["digest"], "bundle_digest": self.bundle["digest"]})
                if action == "continue":
                    self.assertIn("demand", [check["kind"] for check in review["checks"]])

    def test_entire_projected_reference_is_authenticated_without_mutation(self):
        _, review, reference = self.export_review("continue")
        changes = [
            {"original_sha256": "0" * 64},
            {"artifact": dict(reference["artifact"], sha256="0" * 64)},
            {"original_locator": dict(reference["original_locator"], pointer="/validation")},
            {"locator": dict(reference["locator"], pointer="/entries/99/value")},
            {"locator": dict(reference["locator"], value="invented")},
            {"derivative": False}, {"extra": "unbound"},
        ]
        for change in changes:
            with self.subTest(change=change):
                altered = copy.deepcopy(review)
                altered["checks"][0]["evidence"][0].update(change)
                before = self.store.snapshot()
                self.assert_error("round_evidence_mismatch", lambda: self.mutate(rounds.record_round_review, altered))
                self.assertEqual(self.store.snapshot(), before)

    def test_projection_keeps_independence_and_decision_binding(self):
        _, review, _ = self.export_review("stop")
        stale = copy.deepcopy(review)
        stale["round_digest"] = "0" * 64
        self.assert_error("round_review_stale", lambda: self.mutate(rounds.record_round_review, stale))
        author = copy.deepcopy(review)
        author["assessor"]["id"] = self.assessment_payload["author"]
        self.assert_error("review_not_independent", lambda: self.mutate(rounds.record_round_review, author))
        self.bundle = self.scoped_manuscript("paper-2")
        self.assert_error("round_review_stale", lambda: self.mutate(rounds.record_round_review, review))
