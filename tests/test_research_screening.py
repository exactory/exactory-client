"""Screened preparation policy: screenings, the cohort gate, audit, doctrine, saturation, Tier 3, reports."""

import json

from literature_fixtures import FIELDS, LiteratureCase
from research_harness.errors import ResearchError
from research_harness.reading import record_reading_batch
from research_harness.screening import record_screening_batch, record_screening_checkpoint, policy_report


def note(version_id, **extra):
    return dict({"version_id": version_id, "note": "Read the complete abstract.",
                 "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS}}, **extra)


def version(number):
    return "arxiv:2601.%05dv1" % number


class ScreenedCase(LiteratureCase):
    members = 10

    def setUp(self):
        super().setUp()
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "screened-v1"})
        self.collection = self.cohort(tuple(range(1, self.members + 1)))

    def item(self, number, disposition, relevance, **extra):
        value = {"collection_id": self.collection, "work_id": version(number)[:-2], "version_id": version(number),
                 "disposition": disposition, "relevance": relevance, "reason": "Authored screening.", "conventions": []}
        value.update(extra)
        return value

    def screen(self, items, batch_id="screen-1", round_number=None):
        payload = {"id": batch_id, "screener": {"kind": "agent", "model": None}, "items": items}
        if round_number is not None:
            payload["round"] = round_number
        return self.mutate(record_screening_batch, payload)

    def read(self, numbers, batch_id, **extra):
        return self.mutate(record_reading_batch, {"id": batch_id, "depth": "abstract", "items": [note(version(n), **extra) for n in numbers]})

    def cohort_codes(self):
        from research_harness.cohort_evidence import cohort_reading_report
        return [o["code"] for o in cohort_reading_report(self.store, [self.collection])["obligations"]]


class ScreeningRuleTests(ScreenedCase):
    def test_every_member_needs_a_screening_and_item_rules_are_enforced(self):
        self.assertEqual(self.cohort_codes().count("screening_missing"), 10)
        for bad in (self.item(1, "exclude", "strong"), self.item(1, "pending", "strong"), self.item(1, "exclude", "weak"),
                    self.item(1, "promote", "strong"), self.item(1, "doctrine", "weak", promotion_reasons=["prior_art"]),
                    self.item(1, "promote", "strong", promotion_reasons=["famous"]),
                    dict(self.item(1, "doctrine", "weak"), collection_id="cohort:unknown"),
                    dict(self.item(1, "doctrine", "weak"), work_id="arxiv:2601.00002")):
            with self.subTest(bad=bad):
                with self.assertRaises(ResearchError) as raised:
                    self.screen([bad])
                self.assertEqual(raised.exception.code, "invalid_screening")
                self.assertEqual(raised.exception.details["items"][0]["index"], 0)
        self.assertNotIn("screening", self.store.snapshot()["records"])
        self.screen([self.item(1, "promote", "strong", promotion_reasons=["prior_art"])])
        self.assertEqual(self.cohort_codes().count("screening_missing"), 9)

    def test_screening_needs_the_screened_policy(self):
        from research_harness.principles import change_policy
        self.mutate(change_policy, {"previous": "screened-v1", "policy": "exhaustive-v1", "reason": "Back to exhaustive."})
        self.assert_error("policy_inapplicable", lambda: self.screen([self.item(1, "doctrine", "weak")]))
        self.assertEqual(self.cohort_codes().count("cohort_abstract_reading_missing"), 10)

    def test_dispositions_drive_readings_audit_and_doctrine_coverage(self):
        self.screen([self.item(n, "doctrine", "weak") for n in range(1, 9)]
                    + [self.item(9, "promote", "strong", promotion_reasons=["contradiction"]), self.item(10, "exclude", "none")])
        codes = self.cohort_codes()
        self.assertEqual(codes.count("cohort_abstract_reading_missing"), 9)
        self.assertEqual(codes.count("screening_audit_reading_missing"), 1)
        self.assertIn("doctrine_coverage_missing", codes)
        self.read(range(1, 10), "batch-doctrine")
        codes = self.cohort_codes()
        self.assertEqual(codes, ["screening_audit_reading_missing"])
        self.read([10], "batch-audit-strong", audit={"relevance": "strong", "reason": "It addresses the objective directly."})
        self.assertEqual(self.cohort_codes(), ["screening_audit_failed"])
        self.screen([self.item(10, "promote", "strong", promotion_reasons=["prior_art"])], "screen-2", round_number=2)
        from research_harness.cohort_evidence import cohort_reading_report
        report = cohort_reading_report(self.store, [self.collection])
        self.assertTrue(report["ready"])
        self.assertEqual(report["counts"]["screening"][self.collection]["promote"], 2)
        self.assertEqual(report["counts"]["screening"][self.collection]["audit_sample"], 0)

    def test_audit_none_discharges_the_sample_and_gate_reads_with_the_policy(self):
        self.screen([self.item(n, "doctrine", "weak") for n in range(1, 9)] + [self.item(n, "exclude", "none") for n in (9, 10)])
        self.read(range(1, 9), "batch-doctrine")
        self.assertEqual(set(self.cohort_codes()), {"screening_audit_reading_missing"})
        self.read([9, 10], "batch-audit", audit={"relevance": "none", "reason": "Confirmed irrelevant."})
        self.assertEqual(self.cohort_codes(), [])


class SaturationTests(ScreenedCase):
    members = 12

    def test_pending_members_need_reading_until_a_saturation_checkpoint_covers_them(self):
        self.screen([self.item(n, "doctrine", "weak") for n in range(1, 9)] + [self.item(n, "pending", "weak") for n in (9, 10, 11, 12)])
        self.read(range(1, 9), "batch-doctrine")
        self.assertEqual(self.cohort_codes().count("cohort_abstract_reading_missing"), 4)
        self.read([9], "batch-a", consequential=False)
        self.read([10], "batch-b", consequential=False)
        bad = {"id": "sat-bad", "batch_ids": ["batch-doctrine", "batch-a"], "reason": "x"}
        self.assert_error("invalid_screening", lambda: self.mutate(record_screening_checkpoint, bad))
        self.mutate(record_screening_checkpoint, {"id": "sat-1", "batch_ids": ["batch-a", "batch-b"], "reason": "Two batches added nothing consequential."})
        from research_harness.cohort_evidence import cohort_reading_report
        report = cohort_reading_report(self.store, [self.collection])
        self.assertTrue(report["ready"])
        self.assertEqual(report["counts"]["screening"][self.collection]["inventoried_unread"], 2)
        self.read([11], "batch-c", consequential=True)
        self.assertEqual(self.cohort_codes().count("cohort_abstract_reading_missing"), 1)
        self.read([12], "batch-d", consequential=False)
        self.assertEqual(self.cohort_codes(), [])

    def test_a_new_screening_round_or_policy_change_removes_the_checkpoint_effect(self):
        self.screen([self.item(n, "doctrine", "weak") for n in range(1, 9)] + [self.item(n, "pending", "weak") for n in (9, 10, 11, 12)])
        self.read(range(1, 9), "batch-doctrine")
        self.read([9], "batch-a", consequential=False)
        self.read([10], "batch-b", consequential=False)
        self.mutate(record_screening_checkpoint, {"id": "sat-1", "batch_ids": ["batch-a", "batch-b"], "reason": "Saturated."})
        self.assertEqual(self.cohort_codes(), [])
        self.screen([self.item(11, "pending", "weak")], "screen-2", round_number=2)
        self.assertEqual(self.cohort_codes().count("cohort_abstract_reading_missing"), 2)


class Tier3ScreeningTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "screened-v1"})

    def test_references_are_screened_by_family_and_critical_requirements_survive(self):
        from research_harness.reading import require_fulltext
        root = self.metadata(1, references=[{"id": "arxiv:2601.00002v1"}])
        reference = self.metadata(2)
        self.scope([root])
        self.assertIn("screening_missing", self.codes())
        item = {"collection_id": None, "work_id": "arxiv:2601.00002", "version_id": reference, "disposition": "exclude",
                "relevance": "none", "reason": "Background only.", "conventions": [], "context": "Cited once as background."}
        self.mutate(record_screening_batch, {"id": "screen-ref", "screener": {"kind": "agent", "model": None}, "items": [item]})
        self.assertNotIn("screening_missing", self.codes())
        self.assertIn("screening_audit_reading_missing", self.codes())
        self.mutate(record_reading_batch, {"id": "audit-ref", "depth": "abstract",
                                           "items": [note(reference, audit={"relevance": "none", "reason": "Confirmed background."})]})
        self.assertNotIn("screening_audit_reading_missing", self.codes())
        self.assertNotIn("abstract_reading_missing", self.codes())
        self.mutate(require_fulltext, {"id": "critical", "profile": "research", "version_id": reference, "purpose": "validity",
                                       "reason": "A central assumption rests on it."})
        self.assertIn("fulltext_reading_missing", self.codes())
        missing_context = dict(item, context=None, work_id="arxiv:2601.00002")
        with self.assertRaises(ResearchError):
            self.mutate(record_screening_batch, {"id": "screen-bad", "screener": {"kind": "agent", "model": None}, "items": [missing_context]})


class ReportTests(ScreenedCase):
    def test_screen_export_and_policy_report_describe_the_preparation_set(self):
        from research_harness.batches import export_batches
        result = export_batches(self.store, destination=self.root / "screen", screen=True)
        self.assertEqual(result["entries"], 10)
        content = json.loads((self.root / "screen/screen-001.json").read_text())
        self.assertTrue(content["screen"])
        self.assertEqual(content["items"][0]["collection_id"], self.collection)
        self.assertEqual(content["items"][0]["work_id"], "arxiv:2601.00001")
        self.screen([self.item(n, "doctrine", "weak") for n in range(1, 6)] + [self.item(6, "exclude", "none")])
        self.assertEqual(export_batches(self.store, destination=self.root / "screen-2", screen=True)["entries"], 4)
        report = policy_report(self.store)
        summary = report["collections"][self.collection]
        self.assertEqual(summary["dispositions"], {"promote": 0, "doctrine": 5, "exclude": 1, "pending": 0})
        self.assertEqual((summary["unscreened"], summary["selected"], summary["audit_sample"]), (4, 5, ["arxiv:2601.00006"]))
        reference = {"prior_art": ["arxiv:2601.00006"], "contradictions": [], "methods": ["arxiv:2601.00001"], "doctrine": []}
        recall = policy_report(self.store, reference=reference)["recall"]
        self.assertEqual((recall["prior_art"]["recall"], recall["prior_art"]["missed"]), (0.0, ["arxiv:2601.00006"]))
        self.assertEqual(recall["methods"]["recall"], 1.0)
        self.assertIsNone(recall["contradictions"]["recall"])
        hypothetical = policy_report(self.store, policy="exhaustive-v1")
        self.assertEqual(hypothetical["collections"][self.collection]["selected"], 10)
        self.assertEqual(hypothetical["recorded_policy"], "screened-v1")
