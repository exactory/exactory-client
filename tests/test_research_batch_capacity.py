"""Capacity, incremental publication and recovery of abstract batch exports."""

import json
from pathlib import Path
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_fixtures import atom, entry
from research_harness import batches
from research_harness.acquisition import import_response
from research_harness.errors import ResearchError
from research_harness.principles import initialize_research


class BatchCapacityTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.mutate(initialize_research, {"profile": "research", "target": None})

    def register_abstracts(self, texts):
        versions = ["arxiv:2601.%05dv1" % n for n in range(1, len(texts) + 1)]
        self.sequence += 1
        import_response(self.store, "arxiv", atom([entry(version[6:], abstract=text)
            for version, text in zip(versions, texts)], total=len(versions), size=len(versions)),
            source_url="https://export.arxiv.org/api/query", captured_at="2026-09-07T12:00:00Z",
            expected_revision=self.store.revision, request_id="capacity-" + str(self.sequence))
        return versions

    def export(self, versions, name="batches", **kwargs):
        return batches.export_batches(self.store, loop=True, candidates=versions,
                                      destination=self.root / name, **kwargs)

    def read_manifest(self, name="batches"):
        return json.loads((self.root / name / "manifest.json").read_text(encoding="utf-8"))

    def read_batches(self, result):
        return [json.loads(Path(filename).read_text(encoding="utf-8")) for filename in result["files"]]

    def test_unicode_complete_encoded_file_at_the_byte_boundary(self):
        text = "雪🐾" * 2000
        versions = self.register_abstracts([text])
        probe = self.export(versions, "probe")
        original = Path(probe["files"][0]).read_bytes()
        bound = len(original)
        self.assertGreater(bound, len(original.decode("utf-8")))
        exact = self.export(versions, "exact", max_input_bytes=bound)
        self.assertEqual(Path(exact["files"][0]).read_bytes(), original)
        self.assertEqual(self.read_batches(exact)[0]["items"][0]["text"], text)
        with self.assertRaises(ResearchError) as raised:
            self.export(versions, "one-byte-short", max_input_bytes=bound - 1)
        self.assertEqual(raised.exception.code, "batch_item_too_large")
        self.assertEqual(raised.exception.details["input_bytes"], bound)

    def test_default_byte_bound_rejects_an_oversized_singleton_with_retry_parameters(self):
        versions = self.register_abstracts(["雪" * 90000])
        with self.assertRaises(ResearchError) as raised:
            self.export(versions)
        self.assertEqual(raised.exception.code, "batch_item_too_large")
        details = raised.exception.details
        self.assertEqual(details["version_id"], versions[0])
        self.assertEqual(details["max_input_bytes"], 262144)
        self.assertGreater(details["input_bytes"], 262144)
        self.assertEqual(details["parameters"]["max_input_bytes"], details["input_bytes"])
        retry = self.export(versions, "retry", **details["parameters"])
        self.assertEqual(Path(retry["files"][0]).stat().st_size, details["input_bytes"])
        self.assertFalse(self.read_manifest()["complete"])
        self.assertEqual(self.read_manifest()["files"], [])

    def test_output_estimate_adds_a_greedy_item_ceiling(self):
        versions = self.register_abstracts(["Complete abstract."] * 5)
        result = self.export(versions, max_output_tokens=25, estimated_output_tokens_per_item=10)
        self.assertEqual([len(batch["items"]) for batch in self.read_batches(result)], [2, 2, 1])
        capacity = self.read_manifest()["capacity"]
        self.assertEqual(capacity["output_capacity"], "estimated")
        self.assertEqual(capacity["effective_max_items"], 2)
        self.assertEqual([item["estimated_output_tokens"] for item in self.read_manifest()["files"]], [20, 20, 10])

    def test_output_allowance_below_one_item_refuses_before_creating_destination(self):
        versions = self.register_abstracts(["Complete abstract."])
        with self.assertRaises(ResearchError) as raised:
            self.export(versions, max_output_tokens=9, estimated_output_tokens_per_item=10)
        self.assertEqual(raised.exception.code, "batch_output_budget_too_small")
        self.assertEqual(raised.exception.details["max_output_tokens"], 9)
        self.assertEqual(raised.exception.details["estimated_output_tokens_per_item"], 10)
        self.assertFalse((self.root / "batches").exists())

    def test_capacity_values_are_positive_integers_and_output_values_come_together(self):
        invalid = [{"max_output_tokens": 10}, {"estimated_output_tokens_per_item": 10}]
        for field in ("max_input_bytes", "max_output_tokens", "estimated_output_tokens_per_item"):
            for value in (0, -1, True, 1.5, "10"):
                values = {"max_output_tokens": 20, "estimated_output_tokens_per_item": 10} if field != "max_input_bytes" else {}
                invalid.append(dict(values, **{field: value}))
        for index, values in enumerate(invalid):
            with self.subTest(values=values):
                self.assert_error("invalid_input", lambda: self.export([], "invalid-%d" % index, **values))
                self.assertFalse((self.root / ("invalid-%d" % index)).exists())

    def test_a_batch_is_published_before_all_entries_are_constructed(self):
        versions = self.register_abstracts(["Complete abstract."] * 3)
        construct = batches._entry

        def inspect_construction(records, evaluation, version):
            if version == versions[0]:
                self.assertFalse(self.read_manifest()["complete"])
                self.assertEqual(self.read_manifest()["files"], [])
            if version == versions[1]:
                earlier = json.loads((self.root / "batches/batch-001.json").read_text(encoding="utf-8"))
                self.assertEqual(earlier["items"][0]["version_id"], versions[0])
                self.assertFalse(self.read_manifest()["complete"])
                self.assertEqual(self.read_manifest()["counts"]["exported"], 1)
            return construct(records, evaluation, version)

        with patch.object(batches, "_entry", side_effect=inspect_construction):
            result = self.export(versions, size=1)
        self.assertEqual(result["entries"], 3)
        self.assertTrue(self.read_manifest()["complete"])

    def test_later_construction_failure_keeps_an_incomplete_manifest_and_completed_file(self):
        versions = self.register_abstracts(["Complete abstract."] * 3)
        construct = batches._entry

        def fail_later(records, evaluation, version):
            if version == versions[1]:
                raise ResearchError("artifact_missing", "Injected missing artifact")
            return construct(records, evaluation, version)

        with patch.object(batches, "_entry", side_effect=fail_later):
            self.assert_error("artifact_missing", lambda: self.export(versions, size=1))
        manifest = self.read_manifest()
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["counts"], {"candidate": 3, "exported": 1, "missing": 0, "skipped": 0})
        completed = self.root / "batches/batch-001.json"
        self.assertEqual(json.loads(completed.read_bytes())["items"][0]["version_id"], versions[0])
        self.assertEqual(manifest["files"][0]["path"], completed.name)
        self.assertEqual(sorted(path.name for path in completed.parent.iterdir()), ["batch-001.json", "manifest.json"])
        self.assert_error("review_destination_exists", lambda: self.export(versions, size=1))
        retry = self.export(versions, "retry", size=1)
        self.assertEqual(retry["entries"], 3)

    def test_later_oversized_entry_leaves_the_earlier_file_usable(self):
        versions = self.register_abstracts(["Complete abstract.", "雪" * 90000])
        self.assert_error("batch_item_too_large", lambda: self.export(versions))
        manifest = self.read_manifest()
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["counts"]["exported"], 1)
        self.assertEqual(json.loads((self.root / "batches/batch-001.json").read_bytes())["items"][0]["text"], "Complete abstract.")
        self.assertFalse((self.root / "batches/batch-002.json").exists())

    def test_every_emitted_file_fits_all_declared_bounds_and_preserves_order(self):
        texts = [("雪🐾 %d \"quoted\"\n" % number) * 1800 for number in range(7)]
        versions = self.register_abstracts(texts)
        result = self.export(versions, size=4, max_input_bytes=85000,
                             max_output_tokens=31, estimated_output_tokens_per_item=10)
        manifest = self.read_manifest()
        capacity = manifest["capacity"]
        self.assertEqual(capacity["max_input_bytes"], 85000)
        exported = []
        for path, file_info, batch in zip(result["files"], manifest["files"], self.read_batches(result)):
            raw = Path(path).read_bytes()
            self.assertEqual(file_info["input_bytes"], len(raw))
            self.assertLessEqual(len(raw), capacity["max_input_bytes"])
            self.assertLessEqual(len(batch["items"]), capacity["max_items"])
            self.assertLessEqual(len(batch["items"]) * capacity["estimated_output_tokens_per_item"], capacity["max_output_tokens"])
            exported.extend(batch["items"])
        self.assertEqual([item["version_id"] for item in exported], versions)
        self.assertEqual([item["text"] for item in exported], texts)
        self.assertEqual(len(result["files"]), 4)

    def test_default_byte_bound_splits_large_abstracts_without_truncation(self):
        texts = [("Complete abstract %d. " % number) * 4000 for number in range(5)]
        versions = self.register_abstracts(texts)
        result = self.export(versions)
        self.assertEqual([len(batch["items"]) for batch in self.read_batches(result)], [3, 2])
        self.assertTrue(all(Path(filename).stat().st_size <= 262144 for filename in result["files"]))
        self.assertEqual([item["text"] for batch in self.read_batches(result) for item in batch["items"]], texts)

    def test_defaults_preserve_the_existing_sixty_item_batches_for_inputs_that_fit(self):
        versions = self.register_abstracts(["Complete abstract."] * 121)
        before = self.store.snapshot()
        result = self.export(versions)
        content = self.read_batches(result)
        self.assertEqual([len(batch["items"]) for batch in content], [60, 60, 1])
        self.assertEqual([item["version_id"] for batch in content for item in batch["items"]], versions)
        self.assertEqual(self.store.snapshot(), before)
        manifest = self.read_manifest()
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["counts"], {"candidate": 121, "exported": 121, "missing": 0, "skipped": 0})
        self.assertEqual(manifest["capacity"], {"max_items": 60, "effective_max_items": 60, "max_input_bytes": 262144,
            "max_output_tokens": None, "estimated_output_tokens_per_item": None, "output_capacity": "unknown"})
        self.assertTrue(all(item["estimated_output_tokens"] is None for item in manifest["files"]))

    def test_missing_abstract_and_unknown_candidate_are_disclosed_in_selection_coverage(self):
        versions = self.register_abstracts(["Complete abstract.", ""])
        unknown = "arxiv:2601.99999v1"
        result = self.export(versions + [unknown])
        self.assertEqual(result["entries"], 1)
        manifest = self.read_manifest()
        self.assertEqual(manifest["counts"], {"candidate": 3, "exported": 1, "missing": 1, "skipped": 1})
        self.assertEqual(manifest["selection_coverage"]["version_ids"], versions)
        self.assertEqual(manifest["selection_coverage"]["missing_version_ids"], versions[1:])
        self.assertEqual(manifest["selection_coverage"]["skipped_candidate_ids"], [unknown])

    def test_completed_batches_are_published_atomically(self):
        import os

        versions = self.register_abstracts(["Complete abstract."] * 2)
        replace = os.replace
        published = []

        def inspect_replace(source, target, *args, **kwargs):
            if str(target).startswith("batch-"):
                descriptor = os.open(source, os.O_RDONLY, dir_fd=kwargs.get("src_dir_fd"))
                with os.fdopen(descriptor, "rb") as stream:
                    value = json.load(stream)
                self.assertEqual(value["items"][0]["version_id"], versions[len(published)])
                self.assertFalse((self.root / "batches" / target).exists())
                published.append(str(target))
            return replace(source, target, *args, **kwargs)

        with patch("os.replace", side_effect=inspect_replace):
            self.export(versions, size=1)
        self.assertEqual(published, ["batch-001.json", "batch-002.json"])

    def test_failed_manifest_update_preserves_initial_incomplete_state(self):
        import os

        versions = self.register_abstracts(["Complete abstract."])
        replace = os.replace

        def fail_manifest_update(source, target, *args, **kwargs):
            if str(target) == "manifest.json" and (self.root / "batches/batch-001.json").exists():
                raise OSError("Injected manifest replacement failure")
            return replace(source, target, *args, **kwargs)

        with patch("os.replace", side_effect=fail_manifest_update):
            with self.assertRaises(OSError):
                self.export(versions, size=1)
        self.assertFalse(self.read_manifest()["complete"])
        self.assertEqual(json.loads((self.root / "batches/batch-001.json").read_bytes())["items"][0]["text"], "Complete abstract.")
        self.assertEqual(sorted(path.name for path in (self.root / "batches").iterdir()), ["batch-001.json", "manifest.json"])


class JsonLocatorCapacityTests(LiteratureCase):
    def test_multiple_large_json_locator_sources_keep_complete_abstracts_and_emit_early(self):
        from research_harness.reading import record_reading

        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "exhaustive-v1"})
        self.cohort((1, 2, 3, 4, 5))
        versions = []
        texts = []
        sources = []
        for number in range(1, 6):
            version = "arxiv:2601.%05dv1" % number
            text = ("Complete JSON abstract %d. " % number) * 3000
            raw = {"id": version, "title": "JSON source %d" % number, "abstract": text,
                   "padding": "Registry metadata." * 40000}
            self.sequence += 1
            result = import_response(self.store, "mcp", json.dumps(raw).encode("utf-8"),
                source_url="https://example.org/metadata/%d" % number, captured_at="2026-09-07T12:00:00Z",
                media_type="application/json", mappings=[{"id": "/id", "title": "/title", "abstract": "/abstract"}],
                expected_revision=self.store.revision, request_id="json-capacity-" + str(self.sequence))
            versions.append(version)
            texts.append(text)
            sources.append(result["source_ids"][0])
        for version, text, source_id in zip(versions[:3], texts, sources):
            note = self.abstract_note(version, "read-" + version)
            note["inspections"].append({"unit_id": None, "link": {"version_id": version, "source_id": source_id,
                "artifact": self.store.snapshot()["records"]["source"][source_id]["response"],
                "locator": {"kind": "json", "pointer": "/abstract", "value": text}}, "note": "Inspected saved JSON."})
            self.mutate(record_reading, note)
        construct = batches._entry

        def inspect_construction(records, evaluation, version):
            if version == versions[-1]:
                self.assertTrue((self.root / "batches/batch-001.json").is_file())
            return construct(records, evaluation, version)

        with patch.object(batches, "_entry", side_effect=inspect_construction):
            result = batches.export_batches(self.store, destination=self.root / "batches", size=1)
        exported = [item for filename in result["files"] for item in json.loads(Path(filename).read_bytes())["items"]]
        self.assertEqual([item["version_id"] for item in exported], versions[3:])
        # Cohort imports select the original abstracts; JSON passages remain independently assessed evidence.
        self.assertEqual([item["text"] for item in exported], [
            "Source 4 studies bounded sequences and reports a finite example.",
            "Source 5 studies bounded sequences and reports a finite example."])
        self.assertTrue(json.loads((self.root / "batches/manifest.json").read_bytes())["complete"])
