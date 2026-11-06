"""Large cohort lookups retain compact bundle IDs and exact view selection."""

from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_harness import graph, reading
from research_harness.acquisition import collect_cohort
from research_fixtures import atom, client, entry, xml_response
from research_harness.evaluation import Evaluation, _weigh_value
from research_harness.evidence import digest
from research_harness.literature import import_bundle


class BundleIndexTests(LiteratureCase):
    def prepare_oversized_cohort(self):
        http, _, _ = client([xml_response(atom([
            entry("2601.%05dv1" % number) for number in range(start + 1, min(start + 2001, 2527))],
            total=2526, start=start, size=2000)) for start in (0, 2000)])
        collect_cohort(self.store, {"corpus": "arxiv", "primaryCategory": "cs.LG",
            "windowStart": "2026-01-01", "windowEnd": "2026-01-31"}, http=http,
            page_size=2000, expected_revision=self.store.revision, request_id="large-cohort")
        version = "arxiv:2601.00001v1"
        bundle = self.bundle(version)
        self.mutate(import_bundle, bundle)
        records = self.store.snapshot()["records"]
        # An unbounded authored inventory is part of the stored bundle shape.
        # It exceeds the arena without needing private article or review text.
        records["source_bundle"][bundle["id"]]["inventory"]["text"] = (
            "Synthetic required-unit inventory explanation. " * 1600000)
        self.assertEqual(len(records["work"]), 2526)
        self.assertGreater(_weigh_value(records["source_bundle"]), 64 * 1024 * 1024)
        return records

    def prepare_exact_readings(self):
        self.store.mutate("fixture", {}, lambda tx: (
            tx.put("configuration", "research", {"profile": "research"}),
            tx.put("workspace", "study", {"stage": "evaluate"})),
            expected_revision=0, request_id="managed-study")
        version = self.metadata()
        first = self.bundle(version, bundle_id="first")
        self.mutate(import_bundle, first)
        self.mutate(reading.record_reading, self.full_note(first, "read-first"))
        revision = self.store.revision
        second = self.bundle(version, self.capture(version,
            body="A different exact original. References: none."), "second")
        self.mutate(import_bundle, second)
        self.mutate(reading.record_reading, self.full_note(second, "read-second"))
        return version, revision

    def test_oversized_bundle_is_not_indexed_for_2526_untargeted_readings(self):
        records = self.prepare_oversized_cohort()
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(reading, "index_bundles", wraps=graph.index_bundles) as build:
            for version in records["work"]:
                self.assertEqual(reading.current_readings(records, evaluation, version), ([], []))
        self.assertEqual(build.call_count, 0)

    def test_coverage_without_available_main_bodies_does_not_build_an_index(self):
        records = self.prepare_oversized_cohort()
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(reading, "index_bundles", wraps=graph.index_bundles) as build:
            for version in list(records["work"])[1:]:
                result = reading.fulltext_coverage(records, evaluation, version)
                self.assertEqual(result, {"complete": False, "readings": {}, "missing": [
                    {"source_id": None, "original_sha256": None, "paths": [],
                     "bundle_id": None, "pending": []}]})
        self.assertEqual(build.call_count, 0)

    def test_oversized_payload_is_not_retained_in_the_targeted_index(self):
        records = self.prepare_oversized_cohort()
        version = "arxiv:2601.00001v1"
        bundle = records["source_bundle"]["bundle"]
        target = {"id": version, "sha256": bundle["original_sha256"]}
        index = graph.index_bundles(records)
        self.assertTrue(isinstance(index[version]["originals"][target["sha256"]], str))
        self.assertEqual(index[version]["originals"], {target["sha256"]: "bundle"})
        self.assertLess(_weigh_value(index), 4096)
        self.assertIs(graph.selected_bundle(records, version, target, bundle_index=index), bundle)
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(reading, "index_bundles", wraps=graph.index_bundles) as build:
            self.assertEqual(reading.current_readings(records, evaluation, version, target=target), ([], []))
            self.assertEqual(reading.fulltext_coverage(records, evaluation, version, target=target)[
                "missing"][0]["bundle_id"], "bundle")
        self.assertEqual(build.call_count, 1)

    def test_nonexact_targets_and_unavailable_bodies_do_not_build_an_index(self):
        version = self.metadata()
        self.capture(version, status=403)
        records = self.store.snapshot()["records"]
        evaluation = Evaluation(records, self.artifacts)
        with patch.object(reading, "index_bundles", wraps=graph.index_bundles) as build:
            for target in (None, {"id": "other", "sha256": "body"},
                           {"id": version, "sha256": None}):
                self.assertEqual(reading.current_readings(records, evaluation, version, target=target), ([], []))
                self.assertFalse(reading.fulltext_coverage(records, evaluation, version, target=target)["complete"])
        self.assertEqual(build.call_count, 0)

    def test_selection_preserves_sequence_ties_and_explicit_selection(self):
        version = self.metadata()
        bundle = self.bundle(version, bundle_id="first")
        self.mutate(import_bundle, bundle)
        records = self.store.snapshot()["records"]
        first = records["source_bundle"]["first"]
        for identifier, sequence in (("tie", 7), ("earlier", 2), ("equal", 7)):
            records["source_bundle"][identifier] = dict(first, id=identifier, sequence=sequence)
        target = {"id": version, "sha256": first["original_sha256"]}
        for selection, expected in (({}, "tie"), ({version: {"bundle_id": "earlier"}}, "earlier")):
            records["bundle_selection"] = selection
            index = graph.index_bundles(records)
            self.assertEqual(graph.selected_bundle(records, version, target)["id"], expected)
            self.assertEqual(graph.selected_bundle(records, version, target, bundle_index=index)["id"], expected)
            for pin in (None, {"id": "other", "sha256": target["sha256"]},
                        {"id": version, "sha256": None}, {"id": version, "sha256": "missing"}):
                self.assertEqual(graph.selected_bundle(records, version, pin, bundle_index=index),
                                 graph.selected_bundle(records, version, pin))

    def test_supplement_ids_preserve_main_captures_and_graph(self):
        version = self.metadata()
        main = self.capture(version)
        supplement = self.capture(version, body="Supplementary proof. References: none.")
        bundle = self.bundle(version, main)
        bundle["units"].append({"id": "supplement", "kind": "supplement", "required": True,
            "link": self.link(version, supplement["source_id"], supplement["text"])})
        self.mutate(import_bundle, bundle)
        self.mutate(reading.record_reading, self.full_note(bundle))
        self.scope([version])
        records = self.store.snapshot()["records"]
        index = graph.index_bundles(records)
        self.assertEqual(index[version]["supplements"], {supplement["source_id"]})
        for target in (None, {"id": version, "sha256": main["original"]["sha256"]},
                       {"id": version, "sha256": supplement["original"]["sha256"]}):
            self.assertEqual(graph.main_captures(records, records["work"][version], target, bundle_index=index),
                             graph.main_captures(records, records["work"][version], target))
        self.assertEqual([capture["source_id"] for capture in graph.main_captures(
            records, records["work"][version], bundle_index=index)], [main["source_id"]])
        report = {"graph": graph.citation_graph(records, "research"),
                  "coverage": reading.fulltext_coverage(records, self.artifacts, version)}
        self.assertTrue(report["coverage"]["complete"])
        # Frozen against the unchanged 0.73.6 implementation.
        self.assertEqual(digest(report), "43d08c67d6f2dac1f6b5f205cceac1a5965a340ef4fb3c4e594e03b5d28aecee")

    def test_targeted_readings_match_the_released_outputs_in_historical_views(self):
        version, revision = self.prepare_exact_readings()
        records = self.store.snapshot()["records"]
        reports = []
        for budget in (0, 1, 64 * 1024 * 1024):
            evaluation = Evaluation(records, self.artifacts, retention_budget_bytes=budget)
            old = evaluation.read_snapshot(revision)
            views = (evaluation, old, evaluation, old)
            results = []
            for view in views:
                for name in ("first", "second", "missing"):
                    bundle = records["source_bundle"].get(name)
                    target = {"id": version, "sha256": bundle["original_sha256"] if bundle else name}
                    results.append({"current": reading.current_readings(view.records, view, version, target=target),
                        "coverage": reading.fulltext_coverage(view.records, view, version, target=target)})
            self.assertEqual([row["id"] for row in results[0]["current"][0]], ["read-first"])
            self.assertEqual([row["id"] for row in results[1]["current"][0]], ["read-second"])
            self.assertEqual(results[4]["current"][0], [])
            self.assertEqual(results[4]["coverage"]["missing"][0]["bundle_id"], None)
            reports.append(results)
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[1], reports[2])
        # Frozen against the unchanged 0.73.6 implementation.
        self.assertEqual(digest(reports), "f03fd78274e1e2782e20f8cc8230db0bae33637842a7bfc6aa8cbb03ebcf8671")

    def test_historical_index_does_not_select_a_later_bundle_of_the_same_original(self):
        version, _ = self.prepare_exact_readings()
        before = self.store.snapshot()
        capture = before["records"]["work"][version]["fulltexts"][0]
        replacement = self.bundle(version, capture, "replacement")
        replacement["units"].append(dict(replacement["units"][0], id="new-unit"))
        self.mutate(import_bundle, replacement)
        records = self.store.snapshot()["records"]
        target = {"id": version, "sha256": capture["original"]["sha256"]}
        reports = []
        for budget in (0, 1, 64 * 1024 * 1024):
            root = Evaluation(records, self.artifacts, retention_budget_bytes=budget)
            old = root.read_snapshot(before["revision"])
            results = []
            for view, bundle_id, reading_ids in (
                    (root, "replacement", []), (old, "first", ["read-first"]),
                    (root, "replacement", []), (old, "first", ["read-first"])):
                index = graph.index_bundles(view.records)
                self.assertEqual(graph.selected_bundle(view.records, version, target, bundle_index=index)["id"], bundle_id)
                current = reading.current_readings(view.records, view, version, target=target)
                self.assertEqual([row["id"] for row in current[0]], reading_ids)
                coverage = reading.fulltext_coverage(view.records, view, version, target=target)
                self.assertEqual(coverage["complete"], bool(reading_ids))
                results.append({"current": current, "coverage": coverage})
            reports.append(results)
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[1], reports[2])
        # Frozen against the unchanged 0.73.6 implementation.
        self.assertEqual(digest(reports), "94ba72a3c82317fab1034210bb54a8fe1956c63e8c47eb9f87b27872b8a1c425")
