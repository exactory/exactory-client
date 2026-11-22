"""Citation fields obey the post's rules when a problem records them."""

import json
from unittest.mock import patch

from test_research_problem_claims import ProblemCase
from research_fixtures import client
from research_harness import cli
from research_harness.acquisition import acquire_fulltext, import_response
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.problem_citation_metadata import validate_citation_metadata
from research_harness.problem_claims import compute_problem_evidence_digest
from research_harness.problem_delivery import build_post_files
from research_harness.problem_evidence import project_citations
from research_harness.reading import record_reading
from research_harness.stop_classes import describe_terminal
from research_harness.storage import Store
from test_research_problem_assessment import build_assessment
from test_research_problem_attempt import build_attempt
from test_research_problem_delivery import ProblemDeliveryCase


class PostCitationRulesTests(ProblemCase):
    def read_paper(self, url, *, registry_work=False, bundle_locator=False):
        self.initialize_problem()
        if registry_work:
            version = self.metadata()
            capture = self.capture(version)
        else:
            version = "url:" + url
            import_response(self.store, "web", json.dumps({"id": version, "title": "Fixture paper"}).encode(),
                source_url="https://example.org/catalog", captured_at="2026-09-07T12:00:00Z",
                media_type="application/json", mappings=[{"id": "/id", "title": "/title"}],
                expected_revision=self.store.revision, request_id="citation-work")
            http, _, _ = client([(200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\nfixture\n%%EOF")])
            capture = acquire_fulltext(self.store, version, url, http=http,
                extractor=lambda data: {"status": "extracted", "text": "Bounded sequences. References: none."},
                expected_revision=self.store.revision, request_id="citation-pdf")["capture"]
        bundle = self.bundle(version, capture)
        if bundle_locator:
            bundle["units"][0]["url"] = url
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "citation-reading"))
        return version, capture

    def refuse(self, command, payload, code):
        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.mutate(cli.OPERATIONS[command], payload)
        self.assertEqual(caught.exception.code, code, caught.exception.as_dict())
        self.assertEqual(self.store.revision, revision)
        return caught.exception.details

    def build_metadata(self, title):
        version, capture = self.read_paper("https://example.org/paper.pdf", bundle_locator=True)
        content = (title + "\nA").encode()
        records = self.store.snapshot()["records"]
        source = records["source"][capture["source_id"]]
        artifact = self.artifacts.put(content, "text/plain")
        source = dict(source, id="metadata-source", response=artifact)
        self.put("source", source["id"], source)
        span = {"kind": "response", "source_id": source["id"], "sha256": artifact["sha256"]}
        end = len(title.encode())
        return {"id": "metadata", "version_id": version, "title": title, "authors": ["A"],
                "evidence": {"title": dict(span, start=0, end=end),
                             "authors": [dict(span, start=end + 1, end=end + 2)]}}

    def build_locator(self, url, *, capture_url=None):
        version, capture = self.read_paper(capture_url or url, registry_work=capture_url is not None)
        if capture_url is not None:
            # The earlier locator provenance check used urlsplit and exact capture identity.
            source = self.store.snapshot()["records"]["source"][capture["source_id"]]
            self.put("source", source["id"], dict(source, url=url))
        return {"id": "locator", "version_id": version, "reading_id": "citation-reading",
                "original_sha256": capture["original"]["sha256"], "url": url, "source_id": capture["source_id"]}

    def test_metadata_refuses_a_post_citation_character(self):
        payload = self.build_metadata("A title\rwith a \x1bcharacter")
        details = self.refuse("problem-citation-metadata", payload, "problem_citation_text_character")
        self.assertEqual((details["character"], details["line"], details["column"]), ("U+001B", 2, 8))

    def test_metadata_refuses_a_post_citation_below_the_minimum_length(self):
        payload = self.build_metadata("123456\ufeff")
        details = self.refuse("problem-citation-metadata", payload, "problem_citation_text_length")
        self.assertEqual((details["characters"], details["minimum"]), (9, 10))

        records = self.store.snapshot()["records"]
        provenance, _ = validate_citation_metadata(records, self.artifacts, payload)
        record = dict(payload, provenance=provenance, recorded_revision=self.store.revision + 1)
        self.put("problem_citation_metadata", record["id"], record)
        self.assert_stored_citation(payload, {"citation": "A. 123456\ufeff", "locator": "https://example.org/paper.pdf"})

    def test_metadata_refuses_a_post_citation_above_the_maximum_length(self):
        payload = self.build_metadata("\U0001f4d6" * 499)
        details = self.refuse("problem-citation-metadata", payload, "problem_citation_text_length")
        self.assertEqual((details["characters"], details["maximum"]), (1001, 1000))

    def test_locator_refuses_a_post_locator_character(self):
        payload = self.build_locator("https://example.org/a\x1bb.pdf", capture_url="https://example.org/paper.pdf")
        details = self.refuse("problem-citation-locator", payload, "problem_citation_locator_character")
        self.assertEqual(details["character"], "U+001B")

    def test_locator_refuses_a_post_locator_above_the_maximum_length(self):
        url = "https://example.org/" + "x" * 481
        payload = self.build_locator(url)
        details = self.refuse("problem-citation-locator", payload, "problem_citation_locator_length")
        self.assertEqual((details["characters"], details["maximum"]), (501, 500))

        # Seed the stored form that an earlier locator command would have written.
        records = self.store.snapshot()["records"]
        record = dict(payload, reading_digest=digest(records["reading"][payload["reading_id"]]),
                      source_digest=digest(records["source"][payload["source_id"]]),
                      recorded_revision=self.store.revision + 1)
        self.put("citation_locator", payload["id"], record)
        self.assert_stored_citation(payload, {"citation": "Fixture paper", "locator": url})

    def assert_stored_citation(self, payload, expected):
        self.select_statement()
        item = {"kind": "reading", "version_id": payload["version_id"], "reading_id": "citation-reading"}
        value = dict(self.build_value([item]), accounts_version=2, recorded_revision=self.store.revision + 1)
        self.put("problem_value", value["id"], value)
        before = self.store.snapshot()["records"]
        reopened = Store(self.root)
        records = reopened.snapshot()["records"]
        self.assertEqual(records, before)
        self.assertEqual(project_citations(records, self.artifacts), [expected])
        self.assertEqual(json.loads(build_post_files(records, self.artifacts)["citations.json"]), [expected])
        self.assertEqual(compute_problem_evidence_digest(records, self.artifacts),
                         compute_problem_evidence_digest(before, self.artifacts))

    def test_locator_refuses_a_post_locator_with_an_invalid_host(self):
        payload = self.build_locator("https://example%2f.org/paper.pdf", capture_url="https://example.org/paper.pdf")
        self.refuse("problem-citation-locator", payload, "problem_citation_locator_format")

    def read_short_title(self):
        version, _ = self.read_paper("https://example.org/paper.pdf")
        statement = self.select_statement()
        work = self.store.snapshot()["records"]["work"][version]
        self.put("work", version, dict(work, title="Short"))
        item = {"kind": "reading", "version_id": version, "reading_id": "citation-reading"}
        return statement, item

    def test_value_refuses_a_new_citation_with_unpostable_work_metadata(self):
        _, item = self.read_short_title()
        details = self.refuse("problem-value", self.build_value([item]), "problem_citation_text_length")
        self.assertEqual(details["reading_id"], item["reading_id"])

    def test_attempt_refuses_a_new_citation_with_unpostable_work_metadata(self):
        _, item = self.read_short_title()
        details = self.refuse("problem-attempt", build_attempt(reading=item), "problem_citation_text_length")
        self.assertEqual(details["reading_id"], item["reading_id"])

    def test_assessment_refuses_a_new_citation_with_unpostable_work_metadata(self):
        statement, item = self.read_short_title()
        assessment = build_assessment(statement)
        reading = self.store.snapshot()["records"]["reading"][item["reading_id"]]
        next(iter(assessment["items"].values()))["evidence"].append(
            {"kind": "source", "link": reading["inspections"][0]["link"]})
        details = self.refuse("problem-assessment", assessment, "problem_citation_text_length")
        self.assertEqual(details["reading_id"], item["reading_id"])


class OpennessCitationRulesTests(ProblemDeliveryCase):
    def test_openness_refuses_an_unpostable_locator_of_a_decisive_reading(self):
        self.initialize_problem()
        self.put_selecting_slate()
        self.selected = self.select_statement(candidate_id="candidate-1")
        item = self.record_full_reading()
        version = item["version_id"]
        work = self.store.snapshot()["records"]["work"][version]
        self.put("work", version, dict(work, aliases=["doi:10.1234/" + "x" * 493]))
        search = self.capture_search(found=[version])
        with patch.object(self, "capture_search", return_value=search):
            output = self.observe_openness()
        reading = self.store.snapshot()["records"]["reading"][item["reading_id"]]
        self.assertIn(digest(reading), output["findings"]["openness"]["minimum"]["reading_digests"])

        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.mutate(cli.OPERATIONS["problem-openness"], {"assignment_id": "openness-assignment"})
        self.assertEqual(caught.exception.code, "problem_citation_locator_length")
        self.assertEqual(caught.exception.details["reading_id"], item["reading_id"])
        terminal = describe_terminal(caught.exception.code)
        self.assertEqual(terminal["state"], "stop")
        self.assertEqual(terminal["details"]["next"], {"kind": "outcome", "outcome": "scientific_refused"})
        self.assertEqual(self.store.revision, revision)
