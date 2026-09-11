"""Typed reading batches and the read-only batch export."""

import json

from literature_fixtures import FIELDS, LiteratureCase
from research_harness.errors import ResearchError


def item(version_id, **extra):
    return dict({"version_id": version_id, "note": "Read the complete abstract.",
                 "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS}}, **extra)


class ReadBatchTests(LiteratureCase):
    def test_batch_records_expanded_abstract_readings_in_one_event(self):
        from research_harness.reading import record_reading_batch
        ids = [self.metadata(n) for n in (1, 2, 3)]
        before = self.store.revision
        result = self.mutate(record_reading_batch, {"id": "batch-1", "depth": "abstract", "items": [item(i) for i in ids]})
        self.assertEqual(self.store.revision, before + 1)
        self.assertEqual(result["result"]["count"], 3)
        self.assertEqual(result["result"]["usage"]["input_tokens"], None)
        records = self.store.snapshot()["records"]
        self.assertEqual(len(records["reading"]), 3)
        reading = records["reading"][result["result"]["items"][0]["reading_id"]]
        self.assertEqual(reading["inspections"][0]["link"]["locator"]["kind"], "span")
        self.assertEqual(reading["assessment"]["status"], "complete")
        self.assertTrue(reading["assessment"]["includes_abstract"])
        self.assertNotIn("abstract_reading_missing", self.codes())

    def test_invalid_item_rejects_the_whole_batch_with_indices(self):
        from research_harness.reading import record_reading_batch
        ids = [self.metadata(n) for n in (1, 2)]
        bad = item(ids[1])
        bad["notes"]["problem"]["status"] = "maybe"
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as raised:
            self.mutate(record_reading_batch, {"id": "batch-2", "depth": "abstract", "items": [item(ids[0]), bad]})
        self.assertEqual(raised.exception.code, "invalid_batch")
        self.assertEqual([x["index"] for x in raised.exception.details["items"]], [1])
        self.assertEqual(self.store.snapshot(), before)

    def test_duplicate_versions_unknown_works_and_oversized_batches_are_refused(self):
        from research_harness.reading import record_reading_batch
        a = self.metadata()
        for payload in ({"id": "dup", "depth": "abstract", "items": [item(a), item(a)]},
                        {"id": "unknown", "depth": "abstract", "items": [item("arxiv:2699.00001v1")]},
                        {"id": "big", "depth": "abstract", "items": [item(a)] * 101},
                        {"id": "full", "depth": "fulltext", "items": [item(a)]},
                        {"id": "usage", "depth": "abstract", "items": [item(a)], "usage": {"model": None, "input_tokens": -1, "output_tokens": None, "wall_seconds": None}}):
            with self.subTest(payload=payload["id"]):
                self.assert_error("invalid_batch", lambda: self.mutate(record_reading_batch, payload))
        self.assertNotIn("reading", self.store.snapshot()["records"])

    def test_replay_and_conflict_follow_request_identity(self):
        from research_harness.reading import record_reading_batch
        a = self.metadata()
        payload = {"id": "batch-3", "depth": "abstract", "items": [item(a)]}
        first = record_reading_batch(self.store, payload, expected_revision=self.store.revision, request_id="batch-request")
        again = record_reading_batch(self.store, payload, expected_revision=self.store.revision - 1, request_id="batch-request")
        self.assertEqual(first, again)
        changed = dict(payload, items=[item(a, note="Another note.")])
        self.assert_error("request_id_conflict", lambda: record_reading_batch(
            self.store, changed, expected_revision=self.store.revision, request_id="batch-request"))

    def test_screening_audit_and_consequential_are_retained_on_the_reading(self):
        from research_harness.reading import record_reading_batch
        a = self.metadata()
        extras = {"screening": {"relevance": "weak", "reason": "Peripheral.", "conventions": ["Reports a bound."]},
                  "audit": {"relevance": "none", "reason": "Confirmed peripheral."}, "consequential": False}
        result = self.mutate(record_reading_batch, {"id": "batch-4", "depth": "abstract", "items": [item(a, **extras)]})
        reading = self.store.snapshot()["records"]["reading"][result["result"]["items"][0]["reading_id"]]
        self.assertEqual(reading["batch"], dict(extras, batch_id="batch-4"))
        bad = item(a, screening={"relevance": "high", "reason": "x", "conventions": []})
        self.assert_error("invalid_batch", lambda: self.mutate(record_reading_batch, {"id": "batch-5", "depth": "abstract", "items": [bad]}))


class BatchExportTests(LiteratureCase):
    def test_export_writes_unread_abstracts_without_touching_the_store(self):
        from research_harness.batches import export_batches
        from research_harness.principles import initialize_research
        from research_harness.reading import record_reading
        self.mutate(initialize_research, {"profile": "research", "target": None})
        self.cohort((1, 2, 3))
        self.mutate(record_reading, self.abstract_note("arxiv:2601.00002v1", "read-2"))
        before = self.store.snapshot()
        result = export_batches(self.store, size=1, destination=self.root / "cohort/batches")
        self.assertEqual(result["entries"], 2)
        self.assertEqual(len(result["files"]), 2)
        first = json.loads((self.root / "cohort/batches/batch-001.json").read_text())
        self.assertEqual(first["id"], "batch-001")
        self.assertEqual(set(first["items"][0]), {"version_id", "title", "authors", "published", "categories", "text", "link", "extraction"})
        self.assertEqual({json.loads((self.root / "cohort/batches" / name).read_text())["items"][0]["version_id"]
                          for name in ("batch-001.json", "batch-002.json")}, {"arxiv:2601.00001v1", "arxiv:2601.00003v1"})
        readme = json.loads((self.root / "cohort/batches/README.json").read_text())
        self.assertEqual(set(readme["notes_shape"]["items"][0]["notes"]), set(FIELDS))
        self.assertEqual(self.store.snapshot(), before)
        self.assert_error("review_destination_exists", lambda: export_batches(self.store, destination=self.root / "cohort/batches"))

    def test_export_includes_tier_3_abstracts_after_the_cohort(self):
        from research_harness.batches import export_batches
        from research_harness.principles import initialize_research
        from research_harness.reading import record_reading
        self.mutate(initialize_research, {"profile": "research", "target": None})
        collection = self.cohort((1,))
        root = "arxiv:2601.00001v1"
        self.mutate(record_reading, self.abstract_note(root))
        self.metadata(1, references=[{"id": "arxiv:2601.00009v1"}])
        self.metadata(9)
        self.scope([root], [collection])
        result = export_batches(self.store, destination=self.root / "batches")
        self.assertEqual(result["entries"], 1)
        content = json.loads((self.root / "batches/batch-001.json").read_text())
        self.assertEqual(content["items"][0]["version_id"], "arxiv:2601.00009v1")
