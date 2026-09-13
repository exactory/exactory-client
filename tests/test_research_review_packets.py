"""Neutral reviewer packets and one review per assessor per bundle."""

import importlib
import json
import unittest

from research_harness.errors import ResearchError
from rounds_fixtures import RoundsCase
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
        self.assertNotIn('"authors"', text)
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
        # A second review of the same bundle by reviewer-a recorded before the duplicate rule
        # existed: the assessor's latest review stands.
        records = self.store.snapshot()["records"]
        legacy = dict(records["manuscript_review"]["reviewer-a"], id="reviewer-a-legacy", reviewed_revision=self.store.revision,
                      review=self.artifacts.put(json.dumps(self.core("accept")).encode(), "application/json"))
        self.store.mutate("legacy", {}, lambda tx: tx.put("manuscript_review", "reviewer-a-legacy", legacy),
                          expected_revision=self.store.revision, request_id="legacy-review")
        self.assertTrue(api.publication_report(self.store)["ready"])
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Revised after the rejection.\n%%EOF")
        revised = self.mutate(api.prepare_publication, dict(self.bundle_payload(), id="paper-2"))["result"]
        self.assertNotEqual(revised["digest"], bundle["digest"])
        self.mutate(api.record_manuscript_review, dict(self.manuscript_review(revised, "reviewer-a"), id="reviewer-a-2"))
        self.mutate(api.record_manuscript_review, dict(self.manuscript_review(revised, "reviewer-b"), id="reviewer-b-2"))
        self.assertTrue(api.publication_report(self.store)["ready"])


class RoundPacketTests(RoundsCase):
    def test_the_round_packet_carries_history_and_reviews_but_no_labels_or_authors(self):
        from research_harness import predictions, rounds
        from research_harness.review_delivery import deliver_round
        directory = self.root / "reviews" / "round-1"
        self.assert_error("publication_bundle_missing", lambda: deliver_round(self.store, directory))
        # A source closure enters the packet through the claim's source evidence: its work carries the author names.
        bundle = self.pin(evidence=[self.result_evidence(self.execution_payload), self.source_evidence()])
        self.assert_error("round_decision_missing", lambda: deliver_round(self.store, directory))
        self.measure(bundle, "one")
        decision = self.mutate(rounds.record_round, self.decision_payload(bundle))["result"]
        deliver_round(self.store, directory)
        text = (directory / "inputs.json").read_text()
        manifest = json.loads(text)
        self.assertEqual(manifest["kind"], "round")
        self.assertEqual(manifest["manuscript"]["bundle_digest"], bundle["digest"])
        self.assertEqual(manifest["decision"]["digest"], decision["digest"])
        self.assertEqual(manifest["decision"]["closes"], 1)
        self.assertEqual(manifest["decision"]["payload"]["id"], decision["id"])
        self.assertEqual(len(manifest["reviews"]), 5)
        self.assertEqual({r["kind"] for r in manifest["reviews"]}, {"agent"})
        # Review ids stay: a candidate's review evidence names one of them.
        self.assertEqual({r["id"] for r in manifest["reviews"]},
                         {bundle["id"] + "-gate-1", bundle["id"] + "-gate-2", "measure-one-1", "measure-one-2", "measure-one-3"})
        self.assertEqual(len(manifest["predictions"]), 3)
        self.assertEqual(sorted(p["percentile"] for p in manifest["predictions"]), [25, 30, 40])
        records = self.store.snapshot()["records"]
        self.assertEqual(manifest["measurement"], predictions.measurement_summary(records, bundle))
        self.assertIn("overall", text)
        self.assertIn("percentile", text)
        self.assertIn("candidates", text)
        work_id = self.links[0]["version_id"]
        self.assertIn(work_id, manifest["manuscript"]["evidence"]["work"])
        self.assertEqual(records["work"][work_id]["authors"], ["A. Researcher"])
        self.assertNotIn("A. Researcher", text)
        for forbidden in ('"request_id"', '"token"', '_revision"', '"authors"', '"author"'):
            self.assertNotIn(forbidden, text)
        # Before any admission the closing round is the study so far: every cycle assessment, no round history.
        self.assertEqual(list(manifest["development"]), ["assessment-1"])
        self.assertIn("alternatives", manifest["development"]["assessment-1"])
        self.assertEqual(manifest["rounds"], [])
        self.assertEqual(sorted(manifest["synthesis"]), ["context", "innovation"])
        self.assertEqual(manifest["searches"], {})
        self.assertIn("literature", manifest["resources"])

    def test_the_manuscript_packet_stays_blind_to_rounds_and_predictions(self):
        from research_harness import rounds
        from research_harness.review_delivery import deliver_manuscript, deliver_round
        decision, review, admission = self.open_round()
        self.run_round_work("r2")
        bundle = self.pin(self.claims("wider"), identifier="paper-r2")
        self.measure(bundle, "r2")
        directory = self.root / "reviews" / "manuscript-r2"
        deliver_manuscript(self.store, directory)
        text = (directory / "inputs.json").read_text()
        for forbidden in ('"round', '"percentile"', '"core"', '"goal"', '"overall"'):
            self.assertNotIn(forbidden, text)
        # The round packet after the round: its history, the closing round's assessments and its consequence searches.
        assessed = self.mutate(rounds.assess_round, self.assess_payload(admission, bundle))["result"]
        stop = self.mutate(rounds.record_round, self.decision_payload(bundle, closes=2, decision="stop"))["result"]
        deliver_round(self.store, self.root / "reviews" / "round-2")
        manifest = json.loads((self.root / "reviews" / "round-2" / "inputs.json").read_text())
        self.assertEqual(manifest["decision"]["digest"], stop["digest"])
        self.assertEqual(manifest["decision"]["closes"], 2)
        self.assertEqual(len(manifest["rounds"]), 1)
        history = manifest["rounds"][0]
        self.assertEqual(sorted(history), ["assessment", "decision", "goal", "number", "objective", "resource_limits"])
        self.assertEqual((history["number"], history["goal"], history["objective"]), (2, admission["goal"], admission["objective"]))
        self.assertEqual(history["resource_limits"], admission["resource_limits"])
        self.assertEqual(history["decision"], {"id": decision["id"], "payload": decision["payload"], "digest": decision["digest"]})
        self.assertEqual(sorted(history["assessment"]), ["derived", "payload", "successful", "unproductive"])
        self.assertEqual(history["assessment"]["successful"], assessed["successful"])
        self.assertEqual(history["assessment"]["derived"]["cycles"], ["cycle-r2"])
        self.assertEqual(sorted(manifest["development"]), ["assessment-cycle-1-r2", "assessment-r2"])
        self.assertEqual(sorted(manifest["searches"]), ["downstream", "next_step"])
        self.assertEqual(sorted(manifest["searches"]["downstream"]), ["dispositions", "found_work_ids", "gaps", "impact", "purpose"])
        self.assertEqual(manifest["searches"]["next_step"]["purpose"], "next_step")
        self.assertEqual(manifest["resources"]["development"]["rounds"]["charged"], 1)


if __name__ == "__main__":
    unittest.main()
