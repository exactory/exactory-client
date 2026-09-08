"""Acquisition keeps versioned evidence and honest extraction/import states."""

import json
import tempfile
import unittest
from pathlib import Path

from research_harness.acquisition import acquire_work, acquire_fulltext, import_response
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.identities import family_versions, resolve_family
from research_harness.storage import Store
from research_fixtures import atom, entry, client, xml_response, json_response, openalex, crossref


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root, create=True)

    def metadata(self, version="v1", **kwargs):
        http, _, _ = client([xml_response(atom([entry("2601.00001" + version, **kwargs)], total=1))])
        return acquire_work(self.store, "arxiv:2601.00001" + version, request_id="metadata-" + version,
                            expected_revision=self.store.revision, http=http)

    def test_acquisition_preserves_versions_alias_evidence_and_replay(self):
        original = self.metadata(doi="10.1234/example")
        self.metadata("v2", abstract="A revised abstract.", doi="10.1234/example")
        records = self.store.snapshot()["records"]
        self.assertEqual(family_versions(records, "10.1234/example"), ["arxiv:2601.00001v1", "arxiv:2601.00001v2"])
        self.assertEqual(resolve_family(records, "10.1234/example"), "arxiv:2601.00001")
        self.assertEqual(ArtifactStore(self.root).read(records["work"]["arxiv:2601.00001v1"]["abstract"]), b"A short original abstract.")
        http, wire, _ = client([])
        replay = acquire_work(self.store, "arxiv:2601.00001v1", request_id="metadata-v1", expected_revision=0, http=http)
        self.assertEqual(replay, original)
        self.assertEqual(wire.requests, [])
        self.assertNotIn("reading", records)

    def test_conflicting_aliases_are_preserved_and_require_resolution(self):
        self.metadata(doi="10.1234/conflict")
        http, _, _ = client([xml_response(atom([entry("2601.99999v1", doi="10.1234/conflict")], total=1))])
        acquire_work(self.store, "2601.99999v1", request_id="other", expected_revision=self.store.revision, http=http)
        records = self.store.snapshot()["records"]
        with self.assertRaises(ResearchError) as error:
            resolve_family(records, "10.1234/conflict")
        self.assertEqual(error.exception.code, "ambiguous_alias")
        self.assertEqual(len(records["alias"]["doi:10.1234/conflict"]["assertions"]), 2)

    def test_requested_version_mismatch_does_not_accept_the_wrong_work(self):
        http, _, _ = client([xml_response(atom([entry("2601.00001v2")], total=1))])
        result = acquire_work(self.store, "2601.00001v1", request_id="wrong-version", expected_revision=0, http=http)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["pending"][0]["code"], "identifier_mismatch")
        self.assertNotIn("work", self.store.snapshot()["records"])
        self.assertEqual(len(self.store.snapshot()["records"]["source"]), 1)
        source = next(iter(self.store.snapshot()["records"]["source"].values()))
        self.assertEqual(source["exact_version"], "v2")
        self.assertEqual(source["requested_identifier"], "arxiv:2601.00001v1")

    def test_registry_metadata_saves_reconstructed_and_original_abstracts_and_references(self):
        http, _, _ = client([json_response(openalex()), json_response(crossref())])
        acquire_work(self.store, "W123", request_id="oa", expected_revision=0, http=http)
        acquire_work(self.store, "10.1234/example", request_id="cr", expected_revision=self.store.revision, http=http)
        records = self.store.snapshot()["records"]
        self.assertEqual(ArtifactStore(self.root).read(records["work"]["openalex:W123"]["abstract"]), b"An original abstract.")
        self.assertEqual(len(records["reference_occurrence"]), 4)
        self.assertFalse(records["work"]["doi:10.1234/example"]["reference_metadata"]["bibliography_complete"])

    def test_failed_acquisition_cannot_erase_existing_abstract(self):
        self.metadata()
        before = self.store.snapshot()["records"]["work"]
        http, _, _ = client([(503, {"Content-Type": "text/plain"}, b"Temporarily unavailable")], max_retries=0)
        result = acquire_work(self.store, "2601.00001v1", request_id="failed", expected_revision=self.store.revision, http=http)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(self.store.snapshot()["records"]["work"], before)

    def test_fulltext_challenge_and_abstract_landing_pages_are_not_fulltext(self):
        self.metadata()
        bodies = [b"<html><title>Checking your browser</title><p>Enable JavaScript and cookies to continue.</p></html>",
                  b"<article><h1>An example</h1><section class='abstract'><h2>Abstract</h2><p>Only an abstract.</p></section></article>"]
        for i, body in enumerate(bodies):
            http, _, _ = client([(200, {"Content-Type": "text/html"}, body)])
            result = acquire_fulltext(self.store, "arxiv:2601.00001v1", "https://arxiv.org/html/2601.00001v1",
                request_id="html-" + str(i), expected_revision=self.store.revision, http=http)
            self.assertEqual(result["status"], "pending")
            capture = self.store.snapshot()["records"]["work"]["arxiv:2601.00001v1"]["fulltexts"][-1]
            self.assertIsNone(capture["text"])
            self.assertEqual(capture["extraction_status"], "challenge" if i == 0 else "landing_page")

    def test_fulltext_html_body_is_saved_without_marking_read(self):
        self.metadata()
        raw = b"<article class='ltx_document'><section class='ltx_abstract'>A summary.</section><section class='ltx_section'><h2>1 Results</h2><p>The exact body result.</p></section></article>"
        http, _, _ = client([(200, {"Content-Type": "text/html"}, raw)])
        result = acquire_fulltext(self.store, "2601.00001v1", "https://arxiv.org/html/2601.00001v1",
                                 request_id="body", expected_revision=self.store.revision, http=http)
        self.assertEqual(result["status"], "complete")
        records = self.store.snapshot()["records"]
        text = records["work"]["arxiv:2601.00001v1"]["fulltexts"][0]["text"]
        self.assertIn(b"The exact body result.", ArtifactStore(self.root).read(text))
        self.assertNotIn("reading", records)

    def test_pdf_missing_malformed_scanned_and_timeout_extraction_stay_pending(self):
        self.metadata()
        for i, (body, extraction, expected) in enumerate([
            (b"not a PDF", None, "malformed_pdf"),
            (b"%PDF-1.4\nAuthored placeholder\n%%EOF", {"status": "extractor_missing", "text": None}, "extractor_missing"),
            (b"%PDF-1.4\nAuthored placeholder\n%%EOF", {"status": "empty_text", "text": None}, "empty_text"),
            (b"%PDF-1.4\nAuthored placeholder\n%%EOF", {"status": "extraction_timeout", "text": None}, "extraction_timeout")]):
            http, _, _ = client([(200, {"Content-Type": "application/pdf"}, body)])
            result = acquire_fulltext(self.store, "2601.00001v1", "https://arxiv.org/pdf/2601.00001v1",
                request_id="pdf-" + str(i), expected_revision=self.store.revision, http=http,
                extractor=lambda data: extraction)
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["pending"][0]["code"], expected)
        self.assertEqual(len(self.store.snapshot()["records"]["work"]["arxiv:2601.00001v1"]["fulltexts"]), 4)

    def test_native_response_import_and_generic_mcp_mapping_preserve_exact_evidence(self):
        raw = json.dumps(openalex()).encode()
        imported = import_response(self.store, "openalex", raw, source_url="https://api.openalex.org/works/W123",
            captured_at="2026-09-07T00:00:00+00:00", request_id="import-oa", expected_revision=0)
        self.assertEqual(imported["status"], "complete")
        generic = json.dumps({"results": [{"identifier": "https://example.org/notebook", "name": "An authored notebook",
                                           "summary": "A non-paper source summary.", "authors": ["A. Researcher"]}]}).encode()
        mappings = [{"id": "/results/0/identifier", "title": "/results/0/name", "abstract": "/results/0/summary",
                     "authors": "/results/0/authors"}]
        result = import_response(self.store, "mcp", generic, source_url="https://example.org/search",
            captured_at="2026-09-07T00:00:00+00:00", request_id="import-mcp", expected_revision=self.store.revision,
            mappings=mappings, media_type="application/json")
        self.assertEqual(result["status"], "pending")
        records = self.store.snapshot()["records"]
        work = records["work"]["url:https://example.org/notebook"]
        self.assertIsNone(work["abstract"])
        self.assertEqual(ArtifactStore(self.root).read(work["abstracts"][0]["artifact"]), b"A non-paper source summary.")
        self.assertEqual(work["abstracts"][0]["completeness"], "partial")
        source = records["source"][work["source_ids"][0]]
        self.assertEqual(ArtifactStore(self.root).read(source["response"]), generic)
        self.assertEqual(source["capture_method"], "external_import")
        self.assertEqual(source["content_scope"], "tool_response")
        self.assertFalse(source["origin_verified"])
        self.assertNotIn("reading", records)

    def test_bad_import_mapping_and_credential_provenance_never_write_state(self):
        for source_url, mappings in [("https://example.org/?token=secret", []),
                                     ("https://example.org/", [{"id": "/missing", "title": "/title"}])]:
            revision = self.store.revision
            with self.assertRaises(ResearchError):
                import_response(self.store, "web", b'{"title":"A title"}', source_url=source_url,
                    captured_at="2026-09-07T00:00:00+00:00", request_id="invalid", expected_revision=revision,
                    mappings=mappings, media_type="application/json")
            self.assertEqual(self.store.revision, revision)

    def test_different_date_assertions_and_reference_sets_survive_refresh(self):
        http, _, _ = client([json_response(crossref())])
        acquire_work(self.store, "10.1234/example", request_id="date-original", expected_revision=0, http=http)
        updated = crossref()
        updated["message"]["published"] = {"date-parts": [[2024, 6, 2]]}
        updated["message"]["reference"].append({"unstructured": "Another authored reference."})
        http, _, _ = client([json_response(updated)])
        acquire_work(self.store, "10.1234/example", request_id="date-conflict", expected_revision=self.store.revision, http=http)
        work = self.store.snapshot()["records"]["work"]["doi:10.1234/example"]
        self.assertEqual(work["publication_date"], "2026-01")
        self.assertEqual(len(work["date_assertions"]), 2)
        self.assertEqual([len(r["occurrence_ids"]) for r in work["reference_sets"]], [2, 3])

    def test_partial_http_response_is_saved_as_partial_source(self):
        self.metadata()
        http, _, _ = client([(206, {"Content-Type": "application/pdf", "Content-Range": "bytes 0-4/20"}, b"%PDF-")])
        result = acquire_fulltext(self.store, "2601.00001v1", "https://arxiv.org/pdf/2601.00001v1",
                                 request_id="partial-pdf", expected_revision=self.store.revision, http=http)
        self.assertEqual(result["status"], "pending")
        source = self.store.snapshot()["records"]["source"][result["capture"]["source_id"]]
        self.assertEqual(source["status"], "partial")
        self.assertEqual(ArtifactStore(self.root).read(source["response"]), b"%PDF-")

    def test_redirected_fulltext_version_is_recorded_as_observed_not_requested(self):
        self.metadata()
        raw = b"<article class='article-body'>A different version's body.</article>"
        http, _, _ = client([(302, {"Location": "https://arxiv.org/html/2601.00001v2"}, b""),
                             (200, {"Content-Type": "text/html"}, raw)])
        result = acquire_fulltext(self.store, "2601.00001v1", "https://arxiv.org/html/2601.00001v1",
                                 request_id="redirect-version", expected_revision=self.store.revision, http=http)
        self.assertEqual(result["pending"][0]["code"], "version_mismatch")
        source = self.store.snapshot()["records"]["source"][result["capture"]["source_id"]]
        self.assertEqual(source["exact_version"], "v2")
        self.assertEqual(result["capture"]["version"], "v2")
        self.assertIsNone(result["capture"]["text"])

    def test_ambiguous_json_and_invalid_mapped_date_are_rejected_before_admission(self):
        cases = [(b'{"id":"W123","id":"W456","title":"A title"}', {"id": "/id", "title": "/title"}),
                 (b'{"id":"W123","title":"A title","date":"yesterday"}', {"id": "/id", "title": "/title", "publication_date": "/date"})]
        for index, (raw, mapping) in enumerate(cases):
            store = Store(self.root / str(index), create=True)
            with self.subTest(raw=raw):
                with self.assertRaises(ResearchError):
                    import_response(store, "web", raw, source_url="https://example.org/search",
                        captured_at="2026-09-07T00:00:00Z", request_id="bad-json-import", expected_revision=0,
                        media_type="application/json", mappings=[mapping])
                self.assertEqual(store.revision, 0)


if __name__ == "__main__":
    unittest.main()
