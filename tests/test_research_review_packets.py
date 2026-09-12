"""Neutral reviewer packets and one review per assessor per bundle."""

import importlib
import json
import unittest

from research_harness.errors import ResearchError
from test_research_publication import ResearchPublicationTests


class ScrubTests(unittest.TestCase):
    def test_scrub_removes_labels_at_every_depth(self):
        from research_harness.review_packets import scrub
        value = {"request_id": "r", "claimed_revision": 3, "token": "t", "keep": 1,
                 "nested": [{"admitted_revision": 2, "inner": {"token": "x", "value": "y"}}],
                 "manifest": {"request_id": "z", "path": "p", "revision_label": "kept-because-it-is-not-a-suffix"}}
        self.assertEqual(scrub(value), {"keep": 1, "nested": [{"inner": {"value": "y"}}],
                                        "manifest": {"path": "p", "revision_label": "kept-because-it-is-not-a-suffix"}})


class PacketTests(ResearchPublicationTests):
    def test_manuscript_delivery_is_a_neutral_packet_with_the_claim_evidence_closure(self):
        api = self.publication()
        bundle = self.mutate(api.prepare_publication, self.bundle_payload())["result"]
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-a"))
        delivery = importlib.import_module("research_harness.review_delivery")
        directory = self.root / "blind-manuscript"
        result = delivery.deliver_manuscript(self.store, directory)
        text = (directory / "inputs.json").read_text()
        manifest = json.loads(text)
        self.assertEqual(manifest["kind"], "manuscript")
        self.assertEqual(manifest["bundle_digest"], bundle["digest"])
        for forbidden in ('"request_id"', '"token"', '_revision"', '"candidate"', '"plan"', '"strategy_accounts"',
                          '"next_hypothesis"', '"hypothesis"', '"history"', '"readiness_review"', '"overall"', '"review_inputs"'):
            self.assertNotIn(forbidden, text)
        execution_id = self.execution_payload["id"]
        self.assertIn(execution_id, manifest["results"])
        self.assertEqual(manifest["results"][execution_id]["execution"]["id"], execution_id)
        self.assertIsNotNone(manifest["results"][execution_id]["observation"])
        self.assertEqual(set(manifest["evidence"]), {"work", "reading", "source_bundle", "source"})
        self.assertIn("field", manifest["standards"])
        for item in result["artifacts"]:
            self.assertEqual((directory / item["path"]).read_bytes(), self.artifacts.read(item))

    def test_readiness_delivery_keeps_check_evidence_and_drops_labels(self):
        delivery = importlib.import_module("research_harness.review_delivery")
        directory = self.root / "blind-readiness"
        delivery.deliver_readiness(self.store, directory)
        text = (directory / "inputs.json").read_text()
        manifest = json.loads(text)
        self.assertEqual(manifest["kind"], "readiness")
        inputs = manifest["inputs"]
        self.assertEqual(set(inputs) >= {"candidate", "branches", "sources", "synthesis", "strategy_accounts", "plan", "assessment", "checkpoint"}, True)
        self.assertNotIn("authors", inputs["candidate"])
        self.assertNotIn("history", inputs["synthesis"])
        for forbidden in ('"request_id"', '"token"', '_revision"', '"readiness_review"'):
            self.assertNotIn(forbidden, text)
        self.assertIn("sections", inputs["synthesis"])

    def test_one_review_per_assessor_per_bundle_and_a_rejection_stands(self):
        api = self.publication()
        bundle = self.mutate(api.prepare_publication, self.bundle_payload())["result"]
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-a", "reject"))
        self.assert_error("manuscript_review_duplicate", lambda: self.mutate(api.record_manuscript_review,
                                                                             self.manuscript_review(bundle, "Reviewer-A", "accept")))
        self.mutate(api.record_manuscript_review, self.manuscript_review(bundle, "reviewer-b"))
        report = api.publication_report(self.store)
        self.assertFalse(report["ready"])
        self.assertEqual({r["assessor"]["id"] for r in report["reviews"]}, {"reviewer-a", "reviewer-b"})
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Revised after the rejection.\n%%EOF")
        revised = self.mutate(api.prepare_publication, dict(self.bundle_payload(), id="paper-2"))["result"]
        self.assertNotEqual(revised["digest"], bundle["digest"])
        self.mutate(api.record_manuscript_review, dict(self.manuscript_review(revised, "reviewer-a"), id="reviewer-a-2"))
        self.mutate(api.record_manuscript_review, dict(self.manuscript_review(revised, "reviewer-b"), id="reviewer-b-2"))
        self.assertTrue(api.publication_report(self.store)["ready"])


if __name__ == "__main__":
    unittest.main()
