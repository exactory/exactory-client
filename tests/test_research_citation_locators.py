"""Late HTTPS metadata binds an existing exact reading without changing its units."""

import copy
import json

from test_research_problem_claims import ProblemCase
from research_fixtures import client
from research_harness.acquisition import acquire_fulltext, import_response
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.problem_evidence import describe_binding, project_citations, record_citation_locator
from research_harness.reading import record_reading, unit_digest


class CitationLocatorTests(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.url = "https://github.com/fixture/math/raw/main/bound.pdf"
        self.version = "url:" + self.url
        import_response(self.store, "web", json.dumps({"id": self.version, "title": "Finite bound"}).encode(),
            source_url="https://example.org/catalog", captured_at="2026-09-07T12:00:00Z", media_type="application/json",
            mappings=[{"id": "/id", "title": "/title"}], expected_revision=self.store.revision, request_id="url-work")
        http, _, _ = client([(200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\nfinite fixture\n%%EOF")])
        self.capture_record = acquire_fulltext(self.store, self.version, self.url, http=http,
            extractor=lambda data: {"status": "extracted", "text": "A finite bound. References: none."},
            expected_revision=self.store.revision, request_id="github-pdf")["capture"]
        self.bundle_record = self.bundle(self.version, self.capture_record)
        self.mutate(import_bundle, self.bundle_record)
        self.mutate(record_reading, self.full_note(self.bundle_record, "github-reading"))
        item = {"kind": "reading", "version_id": self.version, "reading_id": "github-reading"}
        self.mutate(self.load_problem_module().record_value, self.build_value([item]))

    def build_payload(self):
        return {"id": "late-github-url", "version_id": self.version,
                "reading_id": "github-reading", "original_sha256": self.capture_record["original"]["sha256"],
                "url": self.url, "source_id": self.capture_record["source_id"]}

    def test_unchanged_github_pdf_gains_verified_locator_without_rereading(self):
        before = self.store.snapshot()["records"]
        self.assert_error("problem_citation_locator_missing", lambda: project_citations(before, self.artifacts))
        saved = self.mutate(record_citation_locator, self.build_payload())["result"]
        records = self.store.snapshot()["records"]
        for kind in ("reading", "work", "source_bundle", "problem_value"):
            self.assertEqual(records[kind], before[kind])
        self.assertEqual(project_citations(records, self.artifacts)[0]["locator"], self.url)
        self.assertEqual(saved["reading_digest"], digest(records["reading"]["github-reading"]))
        self.assertEqual(saved["source_digest"], digest(records["source"][saved["source_id"]]))
        self.assertEqual(describe_binding(records, self.artifacts)["citation_locators"],
                         [{"id": saved["id"], "digest": digest(saved)}])

    def test_locator_guards_hash_version_identity_and_https_without_mutation(self):
        other_version = self.metadata(17)
        for change in ({"original_sha256": "0" * 64}, {"version_id": "url:https://example.org/other.pdf"},
                       {"version_id": other_version},
                       {"url": "https://example.org/different.pdf"}, {"url": "http://example.org/file.pdf"},
                       {"url": "https://"}, {"url": "https://user:secret@github.com/x"}):
            with self.subTest(change=change):
                before = self.store.snapshot()
                self.assert_error("problem_citation_locator_unbound", lambda: self.mutate(
                    record_citation_locator, dict(self.build_payload(), **change)))
                self.assertEqual(self.store.snapshot(), before)

    def test_locator_does_not_repair_real_reading_staleness(self):
        self.mutate(record_citation_locator, self.build_payload())
        changed = copy.deepcopy(self.bundle_record)
        changed["id"] = "changed-unit-inventory"
        changed["units"][0]["reason"] = "A changed required-unit dependency."
        self.mutate(import_bundle, changed)
        records = self.store.snapshot()["records"]
        self.assert_error("problem_value_reading_missing", lambda: project_citations(records, self.artifacts))

    def test_historical_urls_and_exports_keep_their_dependencies(self):
        records = self.store.snapshot()["records"]
        bundle = records["source_bundle"][self.bundle_record["id"]]
        old_digest = unit_digest(bundle, records)
        with_url = copy.deepcopy(bundle)
        with_url["units"][0]["url"] = self.url
        self.assertNotEqual(unit_digest(with_url, records), old_digest)
        self.mutate(record_citation_locator, self.build_payload())
        self.assertEqual(unit_digest(bundle, self.store.snapshot()["records"]), old_digest)
        self.assert_error("problem_citation_locator_missing", lambda: project_citations(records, self.artifacts))

    def test_changed_retained_bytes_and_provenance_are_refused(self):
        saved = self.mutate(record_citation_locator, self.build_payload())["result"]
        records = self.store.snapshot()["records"]
        records["citation_locator"][saved["id"]]["source_digest"] = "0" * 64
        self.assert_error("problem_citation_locator_unbound", lambda: project_citations(records, self.artifacts))
        path = self.root / self.capture_record["original"]["path"]
        path.chmod(0o600)
        path.write_bytes(b"Changed original PDF bytes")
        self.assert_error("artifact_corrupt", lambda: self.mutate(record_citation_locator, dict(self.build_payload(), id="changed-bytes")))

    def test_locator_check_mode_authenticates_without_writing(self):
        from research_harness.check_mode import check_mutation
        before = self.store.snapshot()
        payload = self.build_payload()
        checked = check_mutation(self.store, record_citation_locator, payload,
            expected_revision=self.store.revision, request_id="check-late-url", bounded_payload=payload)
        self.assertEqual(checked["check"], "accepted")
        self.assertFalse(checked["recorded"])
        self.assertEqual(self.store.snapshot(), before)
