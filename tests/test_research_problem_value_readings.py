"""Exact full readings remain usable when a version has multiple originals."""

from test_research_problem_claims import ProblemCase
from research_harness.literature import import_bundle
from research_harness.reading import fulltext_coverage, record_reading
import json


class ProblemValueReadingTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.first = self.record_full_reading()

    def capture_second_original(self, read=True):
        version = self.first["version_id"]
        capture = self.capture(version, body="A second complete rendering records the same bounded example. References: none.")
        bundle = self.bundle(version, capture=capture, bundle_id="second-body")
        self.mutate(import_bundle, bundle)
        if read:
            self.mutate(record_reading, self.full_note(bundle, "read-second-body"))
        return {"kind": "reading", "version_id": version, "reading_id": "read-second-body"}

    def test_two_fully_read_main_originals_can_supply_the_named_value_readings(self):
        second = self.capture_second_original()
        coverage = fulltext_coverage(self.store.snapshot()["records"], self.artifacts, self.first["version_id"])
        self.assertTrue(coverage["complete"])
        self.assertEqual(set(coverage["readings"]), {"read-full", "read-second-body"})
        saved = self.mutate(self.load_problem_module().record_value, self.build_value([self.first, second]))["result"]
        self.assertEqual(saved["motivation"][0]["evidence"], [self.first, second])

    def test_named_reading_stays_exact_while_current_work_coverage_is_complete(self):
        self.capture_second_original()
        saved = self.mutate(self.load_problem_module().record_value, self.build_value([self.first]))["result"]
        self.assertEqual(saved["motivation"][0]["evidence"], [self.first])

    def test_an_unread_second_original_retains_the_common_full_reading_gap(self):
        self.capture_second_original(read=False)
        self.assert_error("problem_value_reading_missing", lambda: self.mutate(self.load_problem_module().record_value, self.build_value([self.first])))

    def test_a_read_https_work_without_a_bundle_locator_has_an_explicit_post_obligation(self):
        from research_harness.acquisition import import_response, acquire_fulltext
        from research_harness.gates import gate_state
        from research_fixtures import client
        url = "https://example.org/finite-bound"
        version = "url:" + url
        import_response(self.store, "web", json.dumps({"id": version, "title": "A recorded finite bound"}).encode(),
            source_url="https://example.org/catalog", captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/id", "title": "/title"}], expected_revision=self.store.revision, request_id="https-work")
        http, _, _ = client([(200, {"Content-Type": "text/html"},
            b'<article><section class="article-body"><p>A bounded result. References: none.</p></section></article>')])
        capture = acquire_fulltext(self.store, version, url, http=http, expected_revision=self.store.revision, request_id="https-body")["capture"]
        bundle = self.bundle(version, capture=capture, bundle_id="https-bundle")
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "https-reading"))
        self.mutate(self.load_problem_module().record_value, self.build_value([{"kind": "reading", "version_id": version, "reading_id": "https-reading"}]))
        report = gate_state(self.store.snapshot()["records"], self.artifacts, "post")
        codes = {row["code"] for row in report["obligations"]}
        self.assertIn("problem_citation_locator_missing", codes)
        self.assertNotIn("problem_value_reading_missing", codes)
