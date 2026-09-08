"""Cohort acquisition preserves all responses, family membership and resumability."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from research_harness.acquisition import collect_cohort, resume_cohort, collection_status, acquire_work
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.storage import Store
from research_fixtures import atom, entry, client, xml_response


DEFINITION = {"corpus": "arxiv", "primaryCategory": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-06-30"}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root, create=True)

    def test_paginated_capture_actual_abstracts_crosslists_replay_and_resume(self):
        original = "  An original\n  two-line abstract.  "
        page1 = atom([entry(abstract=original), entry("2601.00002v1", category="math.PR")], total=3, size=2)
        page2 = atom([entry("2601.00003v1", abstract="A memorial.")], total=3, start=2, size=2)
        http, wire, _ = client([xml_response(page1), xml_response(page2)])
        result = collect_cohort(self.store, DEFINITION, request_id="collect-1", expected_revision=0,
                                max_requests=1, http=http, page_size=2)
        self.assertEqual(result["status"], "paused")
        self.assertTrue(result["pending"])
        records = self.store.snapshot()["records"]
        work = records["work"]["arxiv:2601.00001v1"]
        self.assertEqual(ArtifactStore(self.root).read(work["abstract"]).decode(), original)
        self.assertNotIn("reading", records)
        self.assertEqual(len(records["cohort_member"]), 1)
        self.assertEqual(len(records["cohort_exclusion"]), 1)
        self.assertEqual(ArtifactStore(self.root).read(next(iter(records["source"].values()))["response"]), page1)
        replay = collect_cohort(self.store, DEFINITION, request_id="collect-1", expected_revision=0,
                                max_requests=1, http=http, page_size=2)
        self.assertEqual(replay, result)
        self.assertEqual(len(wire.requests), 1)
        done = resume_cohort(self.store, result["collection_id"], request_id="resume-1",
                             expected_revision=self.store.revision, http=http, max_requests=1)
        self.assertEqual(done["status"], "complete")
        self.assertEqual(done["member_count"], 2)
        self.assertEqual(done["exclusion_count"], 1)
        self.assertEqual(parse_qs(urlsplit(wire.requests[1][0]).query)["start"], ["2"])
        self.assertIsNotNone(collection_status(self.store, result["collection_id"])["next_abstract"]["artifact"])
        self.assertEqual(collect_cohort(self.store, DEFINITION, request_id="collect-1", expected_revision=0,
                                      max_requests=1, http=http, page_size=2), result)

    def test_empty_intermediate_page_pauses_without_losing_cursor(self):
        http, wire, _ = client([xml_response(atom([entry()], total=2, size=1)),
                                xml_response(atom([], total=2, start=1, size=1)),
                                xml_response(atom([entry("2601.00002v1")], total=2, start=1, size=1))])
        result = collect_cohort(self.store, DEFINITION, request_id="empty", expected_revision=0, http=http, page_size=1)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["pending"][0]["code"], "empty_page")
        done = resume_cohort(self.store, result["collection_id"], request_id="retry-empty", expected_revision=self.store.revision, http=http)
        self.assertEqual(done["status"], "complete")
        self.assertEqual(parse_qs(urlsplit(wire.requests[-1][0]).query)["start"], ["1"])

    def test_changed_total_or_start_is_never_completion(self):
        for page2, code in [(atom([], total=0, start=1, size=1), "changed_total"),
                            (atom([entry("2601.00002v1")], total=2, start=0, size=1), "changed_cursor")]:
            with self.subTest(code=code):
                store = Store(self.root / code, create=True)
                http, _, _ = client([xml_response(atom([entry()], total=2, size=1)), xml_response(page2)])
                result = collect_cohort(store, DEFINITION, request_id=code, expected_revision=0, http=http, page_size=1)
                self.assertEqual(result["status"], "paused")
                self.assertEqual(result["pending"][0]["code"], code)
                self.assertEqual(len(store.snapshot()["records"]["source"]), 2)

    def test_query_ceiling_creates_disjoint_pending_partitions(self):
        http, _, _ = client([xml_response(atom([], total=30001))])
        result = collect_cohort(self.store, DEFINITION, request_id="large", expected_revision=0, max_requests=1, http=http)
        collection = self.store.snapshot()["records"]["collection"][result["collection_id"]]
        partitions = collection["partitions"]
        self.assertEqual(len(partitions), 2)
        self.assertEqual(partitions[0]["start"], "202601010000")
        self.assertEqual(partitions[1]["end"], "202606302359")
        from datetime import datetime, timedelta
        self.assertEqual(datetime.strptime(partitions[0]["end"], "%Y%m%d%H%M") + timedelta(minutes=1),
                         datetime.strptime(partitions[1]["start"], "%Y%m%d%H%M"))
        self.assertEqual(result["status"], "paused")
        self.assertEqual(collection["definition"], DEFINITION)

    def test_rate_limit_budget_keeps_failed_response_and_resume_progresses(self):
        http, wire, _ = client([(429, {"Retry-After": "3", "Content-Type": "text/plain"}, b"Please wait."),
                                xml_response(atom([entry()], total=1))])
        result = collect_cohort(self.store, DEFINITION, request_id="limited", expected_revision=0, max_requests=1, http=http)
        self.assertEqual(result["attempts_used"], 1)
        self.assertEqual(result["status"], "paused")
        source = next(iter(self.store.snapshot()["records"]["source"].values()))
        self.assertEqual(source["status"], "failed")
        self.assertEqual(source["http_status"], 429)
        done = resume_cohort(self.store, result["collection_id"], request_id="later", expected_revision=self.store.revision, http=http)
        self.assertEqual(done["status"], "complete")
        self.assertEqual(len(wire.requests), 2)

    def test_missing_abstract_and_category_are_pending_with_saved_originals(self):
        http, _, _ = client([xml_response(atom([entry(abstract=None), entry("2601.00002v1", category=None)], total=2))])
        result = collect_cohort(self.store, DEFINITION, request_id="missing", expected_revision=0, http=http)
        self.assertNotEqual(result["status"], "complete")
        self.assertIn("missing_abstract", {p["code"] for p in result["pending"]})
        self.assertIn("missing_primary_category", {p["code"] for p in result["pending"]})
        self.assertEqual(len(self.store.snapshot()["records"]["work"]), 2)

    def test_duplicate_results_do_not_inflate_members_or_certify_coverage(self):
        http, _, _ = client([xml_response(atom([entry(), entry("2601.00001v2")], total=2))])
        result = collect_cohort(self.store, DEFINITION, request_id="duplicate", expected_revision=0, http=http)
        self.assertEqual(result["member_count"], 1)
        self.assertEqual(len(self.store.snapshot()["records"]["work"]), 2)
        self.assertNotEqual(result["status"], "complete")

    def test_stale_admission_and_conflicting_replay_do_not_fetch(self):
        http, wire, _ = client([])
        first = collect_cohort(self.store, DEFINITION, request_id="admit", expected_revision=0, max_requests=0, http=http)
        with self.assertRaises(ResearchError) as error:
            collect_cohort(self.store, DEFINITION, request_id="stale", expected_revision=0, http=http)
        self.assertEqual(error.exception.code, "stale_revision")
        with self.assertRaises(ResearchError):
            collect_cohort(self.store, DEFINITION, request_id="admit", expected_revision=0, max_requests=1, http=http)
        self.assertEqual(wire.requests, [])
        self.assertEqual(first["status"], "paused")

    def test_valid_empty_response_is_distinct_from_missing_response(self):
        http, _, _ = client([xml_response(atom())])
        result = collect_cohort(self.store, DEFINITION, request_id="zero", expected_revision=0, http=http)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["member_count"], 0)
        self.assertEqual(result["source_count"], 1)

    def test_interrupted_collection_replays_admission_and_explicit_resume_uses_next_page(self):
        http, wire, _ = client([xml_response(atom([entry()], total=2, size=1)),
                                xml_response(atom([entry("2601.00002v1")], total=2, start=1, size=1))])
        original_transport = http.transport
        def interrupt(url, headers, **kwargs):
            if len(wire.requests) == 1:
                raise KeyboardInterrupt()
            return original_transport(url, headers, **kwargs)
        http.transport = interrupt
        with self.assertRaises(KeyboardInterrupt):
            collect_cohort(self.store, DEFINITION, request_id="crash", expected_revision=0, http=http, page_size=1)
        record = next(iter(self.store.snapshot()["records"]["collection"].values()))
        self.assertEqual(record["partitions"][0]["offset"], 1)
        replay = collect_cohort(self.store, DEFINITION, request_id="crash", expected_revision=0, http=http, page_size=1)
        self.assertEqual(replay["status"], "admitted")
        self.assertEqual(len(wire.requests), 1)
        http.transport = original_transport
        done = resume_cohort(self.store, record["id"], request_id="after-crash", expected_revision=self.store.revision, http=http)
        self.assertEqual(done["status"], "complete")
        self.assertEqual(parse_qs(urlsplit(wire.requests[-1][0]).query)["start"], ["1"])

    def test_concurrent_revision_change_rejects_page_commit_and_resume_keeps_prior_evidence(self):
        http, wire, _ = client([xml_response(atom([entry()], total=1)), xml_response(atom([entry()], total=1))])
        original_transport = http.transport
        def concurrent(url, headers, **kwargs):
            self.store.mutate("unrelated", {}, lambda tx: tx.put("unrelated", "value", {"x": 1}),
                              request_id="other-writer", expected_revision=self.store.revision)
            return original_transport(url, headers, **kwargs)
        http.transport = concurrent
        with self.assertRaises(ResearchError) as error:
            collect_cohort(self.store, DEFINITION, request_id="cas", expected_revision=0, http=http)
        self.assertEqual(error.exception.code, "stale_revision")
        records = self.store.snapshot()["records"]
        self.assertNotIn("work", records)
        self.assertEqual(records["unrelated"]["value"], {"x": 1})
        http.transport = original_transport
        collection_id = next(iter(records["collection"]))
        result = resume_cohort(self.store, collection_id, request_id="cas-resume", expected_revision=self.store.revision, http=http)
        self.assertEqual(result["status"], "complete")

    def test_population_loss_after_changed_total_remains_pending(self):
        http, _, _ = client([xml_response(atom([entry()], total=3, size=1)),
                             xml_response(atom([entry("2601.00002v1")], total=2, start=1, size=1)),
                             xml_response(atom([entry()], total=1, size=1))])
        first = collect_cohort(self.store, DEFINITION, request_id="unstable", expected_revision=0, http=http, page_size=1)
        later = resume_cohort(self.store, first["collection_id"], request_id="changed-population", expected_revision=self.store.revision, http=http)
        self.assertNotEqual(later["status"], "complete")
        self.assertIn("population_changed", {p["code"] for p in later["pending"]})
        self.assertEqual(later["member_count"], 2)

    def test_single_minute_over_query_ceiling_is_explicitly_pending(self):
        definition = dict(DEFINITION, windowEnd="2026-01-01")
        http, _, _ = client([xml_response(atom([], total=30001)) for _ in range(20)])
        result = collect_cohort(self.store, definition, request_id="minute-limit", expected_revision=0, http=http, max_requests=20)
        self.assertEqual(result["pending"][0]["code"], "query_ceiling")
        self.assertLess(result["attempts_used"], 20)
        self.assertGreater(len(result["pending_partitions"]), 1)

    def test_partial_import_cannot_fill_missing_original_cohort_abstract(self):
        from research_harness.acquisition import import_response
        http, _, _ = client([xml_response(atom([entry(abstract=None)], total=1))])
        result = collect_cohort(self.store, DEFINITION, request_id="missing-original", expected_revision=0, http=http)
        raw = b'{"id":"arxiv:2601.00001v1","title":"An authored example","snippet":"A search excerpt."}'
        import_response(self.store, "web", raw, source_url="https://example.org/search",
            captured_at="2026-09-07T00:00:00Z", request_id="partial-import", expected_revision=self.store.revision,
            media_type="application/json", mappings=[{"id": "/id", "title": "/title", "abstract": "/snippet"}])
        status = collection_status(self.store, result["collection_id"])
        self.assertIn("missing_abstract", {p["code"] for p in status["pending"]})
        self.assertIsNone(self.store.snapshot()["records"]["work"]["arxiv:2601.00001v1"]["abstract"])

    def test_category_conflict_on_resume_remains_explicit(self):
        http, _, _ = client([xml_response(atom([entry()], total=2, size=1)),
                             xml_response(atom([], total=1, start=1, size=1)),
                             xml_response(atom([entry(category="math.PR")], total=1, size=1))])
        first = collect_cohort(self.store, DEFINITION, request_id="category-old", expected_revision=0, http=http, page_size=1)
        later = resume_cohort(self.store, first["collection_id"], request_id="category-new", expected_revision=self.store.revision, http=http)
        self.assertIn("primary_category_conflict", {p["code"] for p in later["pending"]})
        self.assertEqual(later["member_count"], 1)
        self.assertEqual(later["exclusion_count"], 1)

    def test_split_response_outside_its_partition_does_not_certify_enumeration(self):
        http, _, _ = client([xml_response(atom([], total=30001)),
                             xml_response(atom([entry()], total=1)),
                             xml_response(atom([entry("2601.00002v1")], total=1))])
        result = collect_cohort(self.store, DEFINITION, request_id="bad-partition", expected_revision=0, http=http)
        self.assertIn("out_of_partition_date", {p["code"] for p in result["pending"]})

    def test_versionless_arxiv_response_cannot_certify_exact_source_capture(self):
        http, _, _ = client([xml_response(atom([entry("2601.00001")], total=1))])
        result = collect_cohort(self.store, DEFINITION, request_id="versionless", expected_revision=0, http=http)
        self.assertIn("missing_version", {p["code"] for p in result["pending"]})

    def test_duplicate_exact_ids_keep_distinct_original_entry_assertions(self):
        http, _, _ = client([xml_response(atom([entry(abstract="First assertion."), entry(abstract="Second assertion.")], total=2))])
        collect_cohort(self.store, DEFINITION, request_id="two-assertions", expected_revision=0, http=http)
        records = self.store.snapshot()["records"]
        self.assertEqual(len(records["work_assertion"]), 2)
        self.assertEqual({a["source_locator"]["index"] for a in records["work_assertion"].values()}, {0, 1})
        work = records["work"]["arxiv:2601.00001v1"]
        self.assertEqual(len(work["abstracts"]), 2)
        self.assertEqual(len({a["assertion_id"] for a in work["abstracts"]}), 2)

    def test_status_for_all_collections_checks_saved_abstract_bytes(self):
        http, _, _ = client([xml_response(atom([entry()], total=1))])
        collect_cohort(self.store, DEFINITION, request_id="artifact-check", expected_revision=0, http=http)
        abstract = self.store.snapshot()["records"]["work"]["arxiv:2601.00001v1"]["abstract"]
        (self.root / abstract["path"]).unlink()
        with self.assertRaises(ResearchError) as error:
            collection_status(self.store)
        self.assertEqual(error.exception.code, "artifact_missing")


class CollectionCliTests(unittest.TestCase):
    def test_collect_zero_budget_status_resume_and_unchanged_freeze_contract(self):
        command = str(Path(__file__).resolve().parent.parent / "bin" / "exactory-cohort")
        with tempfile.TemporaryDirectory() as directory:
            definition = Path(directory) / "definition.json"
            definition.write_text(json.dumps(DEFINITION))
            workspace = Path(directory) / "workspace"
            def run(*arguments):
                return subprocess.run([sys.executable, command, *arguments], capture_output=True, text=True)
            result = run("collect", "--workspace", str(workspace), "--definition", str(definition),
                         "--request-id", "cli-1", "--expected-revision", "0", "--max-requests", "0")
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "paused")
            status = run("status", "--workspace", str(workspace), "--collection-id", output["collection_id"])
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertTrue(json.loads(status.stdout)["pending"])
            resumed = run("collect", "--workspace", str(workspace), "--resume", output["collection_id"],
                          "--request-id", "cli-resume", "--expected-revision", str(json.loads(status.stdout)["revision"]),
                          "--max-requests", "0")
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(json.loads(resumed.stdout)["status"], "paused")
            frozen = run("freeze", "--published", "2026-07-15", "--category", "cs.LG", "--corpus", "arxiv")
            self.assertEqual(json.loads(frozen.stdout), DEFINITION)

    def test_status_missing_store_fails_without_creating_workspace(self):
        command = str(Path(__file__).resolve().parent.parent / "bin" / "exactory-cohort")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "absent"
            result = subprocess.run([sys.executable, command, "status", "--workspace", str(workspace)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(workspace.exists())
            self.assertIn("error", json.loads(result.stderr))


if __name__ == "__main__":
    unittest.main()
