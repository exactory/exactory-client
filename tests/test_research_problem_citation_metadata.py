"""URL citation corrections bind exact retained bytes without changing work records."""

import importlib
import json
import os

from test_research_problem_claims import ProblemCase
from research_fixtures import client
from research_harness.acquisition import acquire_fulltext, import_response
from research_harness import cli
from research_harness.check_mode import check_mutation
from research_harness.evidence import digest
from research_harness.literature import import_bundle
from research_harness.problem_claims import compute_problem_evidence_digest
from research_harness.problem_evidence import describe_binding, describe_evidence, project_citations
from research_harness.reading import record_reading


class CitationMetadataCase(ProblemCase):
    def setUp(self):
        super().setUp()
        self.initialize_problem()
        self.select_statement()
        self.version = "url:https://example.org/repository/paper.pdf"
        self.title = "A finite bound with exact evidence"
        self.names = ["Ada Lovelace", "Grace Hopper"]
        self.pdf_text = ("A finite bound with exact evidence\nAda Lovelace and Grace Hopper\n"
                         "The finite bound holds for the inspected inputs.\nReferences: none.")
        self.imported = self.import_json({"id": self.version, "title": "/tmp/repository/paper.pdf"}, "paper")
        http, _, _ = client([(200, {"Content-Type": "application/pdf"}, b"%PDF-1.4\nfixture\n%%EOF")])
        self.capture_record = acquire_fulltext(self.store, self.version, self.version[4:], http=http,
            extractor=lambda data: {"status": "extracted", "text": self.pdf_text},
            expected_revision=self.store.revision, request_id="citation-pdf")["capture"]
        bundle = self.bundle(self.version, self.capture_record)
        bundle["units"][0]["url"] = self.version[4:]
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "citation-reading"))
        self.item = {"kind": "reading", "version_id": self.version, "reading_id": "citation-reading"}
        self.mutate(self.load_problem_module().record_value, self.build_value([self.item]))
        self.response_source = self.import_json({"id": "https://example.org/repository/README", "title": "README",
            "content": "Context " + "\U0001f4d6" * 300 + self.title + " by Ada Lovelace and Grace Hopper (2026). "
                       + "\U0001f4d6" * 300 + self.title,
            "escaped": "A title\nwith a newline"}, "readme")

    def import_json(self, document, request):
        result = import_response(self.store, "mcp", json.dumps(document, ensure_ascii=False).encode(),
            source_url="https://example.org/" + request, captured_at="2026-09-07T12:00:00Z",
            media_type="application/json", mappings=[{"id": "/id", "title": "/title"}],
            expected_revision=self.store.revision, request_id="citation-" + request)
        return self.store.snapshot()["records"]["source"][result["source_ids"][0]]

    def metadata_module(self):
        return importlib.import_module("research_harness.problem_citation_metadata")

    def evidence_span(self, value, *, fulltext=False, last=False):
        source = self.capture_record["source_id"] if fulltext else self.response_source["id"]
        artifact = self.capture_record["text"] if fulltext else self.response_source["response"]
        data = self.artifacts.read(artifact)
        start = data.rindex(value.encode()) if last else data.index(value.encode())
        return {"kind": "fulltext" if fulltext else "response", "source_id": source,
                "sha256": artifact["sha256"], "start": start, "end": start + len(value.encode())}

    def payload(self, identifier="metadata-1", year=False, fulltext=False):
        value = {"id": identifier, "version_id": self.version, "title": self.title, "authors": self.names[:],
                 "evidence": {"title": self.evidence_span(self.title, fulltext=fulltext),
                              "authors": [self.evidence_span(name) for name in self.names]}}
        if year:
            value["year"] = "2026"
            value["evidence"]["year"] = self.evidence_span("2026")
        return value

    def record_metadata(self, payload=None):
        self.assertIn("problem-citation-metadata", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["problem-citation-metadata"], payload or self.payload())["result"]


class CitationMetadataTests(CitationMetadataCase):
    def test_response_title_and_two_authors_replace_path_title_with_unchanged_pdf_locator(self):
        before = self.store.snapshot()["records"]
        record = self.record_metadata(self.payload(year=True))
        records = self.store.snapshot()["records"]
        self.assertEqual(record["recorded_revision"], self.store.revision)
        self.assertEqual(project_citations(records, self.artifacts), [{"citation":
            "Ada Lovelace, Grace Hopper. A finite bound with exact evidence. 2026", "locator": self.version[4:]}])
        for kind in ("work", "reading", "source_bundle"):
            self.assertEqual(records[kind], before[kind])
        self.assertFalse(self.response_source["origin_verified"])
        projected = describe_evidence(records, self.artifacts)["readings"][0]["citation_metadata"]
        self.assertEqual(projected["id"], record["id"])
        self.assertEqual(projected["digest"], digest(record))
        self.assertEqual(projected["cite"], {"kind": "record", "record_kind": "problem_citation_metadata", "id": record["id"]})
        self.assertEqual(projected["evidence"], record["evidence"])
        self.assertFalse(projected["provenance"]["title"]["source"]["origin_verified"])
        excerpt = projected["excerpts"]["title"]
        raw = self.artifacts.read(self.response_source["response"])
        self.assertEqual(raw[excerpt["start"]:excerpt["end"]].decode(), excerpt["text"])
        self.assertEqual(excerpt["text"], "\U0001f4d6" * 256 + self.title +
                         " by Ada Lovelace and Grace Hopper (2026). " + "\U0001f4d6" * 214)
        self.assertEqual(describe_binding(records, self.artifacts)["citation_metadata"],
            [{"version_id": self.version, "id": record["id"], "digest": digest(record)}])

    def test_fulltext_title_is_supported_and_omitted_year_has_no_fallback(self):
        work = self.store.snapshot()["records"]["work"][self.version]
        self.put("work", self.version, dict(work, year=1999))
        self.record_metadata(self.payload(fulltext=True))
        citation = project_citations(self.store.snapshot()["records"], self.artifacts)[0]
        self.assertEqual(citation["citation"], "Ada Lovelace, Grace Hopper. " + self.title)

    def test_replacement_is_complete_immutable_and_latest_revision_wins(self):
        first = self.record_metadata(self.payload("z-first", year=True))
        second = self.record_metadata(self.payload("a-second"))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["problem_citation_metadata"][first["id"]], first)
        self.assertEqual(self.metadata_module().find_citation_metadata(records, self.version), second)
        tied = dict(second, id="z-tie")
        records["problem_citation_metadata"]["z-tie"] = tied
        records["problem_citation_metadata"]["other-version"] = dict(tied, id="other-version", version_id="url:https://example.org/other", recorded_revision=100000)
        self.assertEqual(self.metadata_module().find_citation_metadata(records, self.version), tied)
        self.assert_error("record_conflict", lambda: self.record_metadata(self.payload("z-first")))

    def test_same_id_and_payload_preserve_the_original_recorded_revision(self):
        first = self.record_metadata()
        self.assertEqual(self.record_metadata(), first)
        self.assertEqual(first["recorded_revision"], self.store.revision - 1)

    def test_replay_precedes_revalidation_and_request_conflict_and_stale_revision_are_preserved(self):
        operation = cli.OPERATIONS.get("problem-citation-metadata")
        self.assertIsNotNone(operation)
        value, revision = self.payload(), self.store.revision
        first = operation(self.store, value, expected_revision=revision, request_id="metadata-replay")
        source = dict(self.response_source, response_complete=False)
        self.put("source", source["id"], source)
        replay = operation(self.store, value, expected_revision=revision, request_id="metadata-replay")
        self.assertEqual(replay, first)
        self.assert_error("request_id_conflict", lambda: operation(self.store, dict(value, title="Other"),
            expected_revision=revision, request_id="metadata-replay"))
        self.assert_error("stale_revision", lambda: operation(self.store, value,
            expected_revision=revision, request_id="metadata-stale"))

    def test_check_verifies_evidence_without_writes(self):
        self.assertIn("problem-citation-metadata", cli.OPERATIONS)
        before = self.store.snapshot()
        files = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file())
        result = check_mutation(self.store, cli.OPERATIONS["problem-citation-metadata"], self.payload(),
            expected_revision=self.store.revision, request_id="metadata-check", bounded_payload=self.payload())
        self.assertEqual(result["check"], "accepted")
        self.assertFalse(result["recorded"])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file()), files)
        invalid = self.payload()
        invalid["title"] = "Unsupported title"
        self.assert_error("problem_citation_span_mismatch", lambda: check_mutation(self.store,
            cli.OPERATIONS["problem-citation-metadata"], invalid, expected_revision=self.store.revision,
            request_id="metadata-check-refused", bounded_payload=invalid))
        self.assertEqual(self.store.snapshot(), before)

    def test_evidence_only_replacement_changes_digest_but_not_citation(self):
        self.record_metadata()
        before = self.store.snapshot()["records"]
        replacement = self.payload("metadata-2")
        replacement["evidence"]["title"] = self.evidence_span(self.title, last=True)
        self.record_metadata(replacement)
        after = self.store.snapshot()["records"]
        self.assertEqual(project_citations(before, self.artifacts), project_citations(after, self.artifacts))
        self.assertNotEqual(compute_problem_evidence_digest(before, self.artifacts), compute_problem_evidence_digest(after, self.artifacts))

    def test_metadata_outside_reading_collection_is_omitted(self):
        self.record_metadata()
        records = self.store.snapshot()["records"]
        records.pop("problem_value")
        self.assertNotIn("citation_metadata", describe_binding(records, self.artifacts))
        self.assertEqual(describe_evidence(records, self.artifacts)["readings"], [])

    def test_schema_cardinality_strings_year_and_offsets_are_strict(self):
        changes = [lambda p: p.update(path="/tmp/caller"), lambda p: p.update(title=" "),
            lambda p: p.update(authors=[]), lambda p: p.update(authors=[True]),
            lambda p: p.update(year=2026), lambda p: p.update(year="２０２６"),
            lambda p: p.update(year="202"), lambda p: p.update(year="2026"),
            lambda p: p["evidence"].update(year=p["evidence"]["title"]),
            lambda p: p["evidence"].update(authors=p["evidence"]["authors"][:1]),
            lambda p: p["evidence"].update(extra={}), lambda p: p["evidence"].pop("title"),
            lambda p: p["evidence"]["title"].update(kind="file"),
            lambda p: p["evidence"]["title"].update(path="/tmp/caller"),
            lambda p: p["evidence"]["title"].update(sha256="bad")]
        for offset in (True, -1, 0.5, "1", None):
            changes.append(lambda p, offset=offset: p["evidence"]["title"].update(start=offset))
        changes += [lambda p: p["evidence"]["title"].update(end=True),
                    lambda p: p["evidence"]["title"].update(end=999999),
                    lambda p: p["evidence"]["title"].update(end=p["evidence"]["title"]["start"])]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                payload = self.payload()
                change(payload)
                self.assert_error("invalid_input", lambda: self.record_metadata(payload))

    def test_orchestration_conflict(self):
        records = self.store.snapshot()["records"]
        records.pop("workspace")
        self.assert_error("orchestration_conflict", lambda: self.metadata_module().validate_citation_metadata(records, self.artifacts, self.payload()))

    def test_missing_non_url_and_registry_identity_are_ineligible(self):
        registry_version = self.metadata()
        for version in ("url:https://example.org/missing", registry_version):
            with self.subTest(version=version):
                value = self.payload()
                value["version_id"] = version
                self.assert_error("problem_citation_work_ineligible", lambda: self.record_metadata(value))
        work = self.store.snapshot()["records"]["work"][self.version]
        for alias in ("doi:10.1000/paper", "arxiv:2601.00001v1"):
            with self.subTest(alias=alias):
                self.put("work", self.version, dict(work, aliases=[alias]))
                self.assert_error("problem_citation_work_ineligible", self.record_metadata)

    def test_only_a_full_reading_of_the_exact_version_grants_eligibility(self):
        version = self.metadata()
        bundle = self.bundle(version, bundle_id="other-bundle")
        self.mutate(import_bundle, bundle)
        self.mutate(record_reading, self.full_note(bundle, "other-reading"))
        reading = self.store.snapshot()["records"]["reading"]["citation-reading"]
        self.put("reading", reading["id"], dict(reading, depth="abstract"))
        self.assert_error("problem_value_reading_missing", self.record_metadata)

    def test_unrelated_available_fulltext_with_identical_text_is_unbound(self):
        version = self.metadata()
        unrelated = self.capture(version, pdf_text=self.pdf_text)
        value = self.payload(fulltext=True)
        self.assertEqual(unrelated["text"]["sha256"], value["evidence"]["title"]["sha256"])
        value["evidence"]["title"]["source_id"] = unrelated["source_id"]
        self.assert_error("problem_citation_evidence_unbound", lambda: self.record_metadata(value))

    def test_missing_extracted_text_is_unbound(self):
        value = self.payload(fulltext=True)
        (self.root / self.capture_record["text"]["path"]).unlink()
        self.assert_error("problem_citation_evidence_unbound", lambda: self.record_metadata(value))

    def test_no_valid_full_reading(self):
        reading = self.store.snapshot()["records"]["reading"]["citation-reading"]
        self.put("reading", reading["id"], dict(reading, depth="abstract"))
        self.assert_error("problem_value_reading_missing", self.record_metadata)

    def test_unknown_and_incomplete_source(self):
        value = self.payload()
        value["evidence"]["title"]["source_id"] = "missing"
        self.assert_error("unknown_source", lambda: self.record_metadata(value))
        for changes in ({"response_complete": False}, {"status": "pending"}, {"response": None}):
            self.put("source", self.response_source["id"], dict(self.response_source, **changes))
            self.assert_error("source_pending", self.record_metadata)

    def test_wrong_hash_unrelated_fulltext_and_missing_object_are_unbound(self):
        for changes in ({"sha256": "0" * 64}, {"kind": "fulltext"}):
            value = self.payload()
            value["evidence"]["title"].update(changes)
            self.assert_error("problem_citation_evidence_unbound", lambda: self.record_metadata(value))
        value = self.payload()
        path = self.root / self.response_source["response"]["path"]
        path.unlink()
        self.assert_error("problem_citation_evidence_unbound", lambda: self.record_metadata(value))

    def test_pending_fulltext_cannot_supply_metadata(self):
        for changes in ({"availability": "pending"}, {"extraction_status": "pending"}):
            with self.subTest(changes=changes):
                records = self.store.snapshot()["records"]
                records["work"][self.version]["fulltexts"] = [dict(self.capture_record, **changes)]
                self.assert_error("source_pending", lambda: self.metadata_module().validate_citation_metadata(records, self.artifacts, self.payload(fulltext=True)))

    def test_corrupt_artifact_is_refused_during_recording_and_projection(self):
        self.record_metadata()
        value = self.payload("second")
        path = self.root / self.response_source["response"]["path"]
        os.chmod(path, 0o600)
        path.write_bytes(b"corrupt")
        self.assert_error("artifact_corrupt", lambda: self.record_metadata(value))
        self.assert_error("artifact_corrupt", lambda: project_citations(self.store.snapshot()["records"], self.artifacts))

    def test_byte_boundaries_invalid_utf8_escape_and_whitespace_mismatches(self):
        value = self.payload()
        value["evidence"]["title"]["start"] -= 1
        self.assert_error("invalid_input", lambda: self.record_metadata(value))
        value = self.payload()
        value["title"] = "\U0001f4d6"
        value["evidence"]["title"] = self.evidence_span("\U0001f4d6")
        value["evidence"]["title"]["end"] -= 1
        self.assert_error("invalid_input", lambda: self.record_metadata(value))
        for title in ("A title\nwith a newline", self.title.replace(" ", "  "), "Clean title"):
            value = self.payload()
            value["title"] = title
            if title.startswith("A title"):
                value["evidence"]["title"] = self.evidence_span("A title\\nwith a newline")
            self.assert_error("problem_citation_span_mismatch", lambda: self.record_metadata(value))
        value = self.payload(year=True)
        value["year"] = "2025"
        self.assert_error("problem_citation_span_mismatch", lambda: self.record_metadata(value))
        artifact = self.artifacts.put(b"\xff" + self.title.encode(), "text/plain")
        self.put("source", self.response_source["id"], dict(self.response_source, response=artifact))
        value = self.payload()
        value["evidence"]["title"].update(sha256=artifact["sha256"], start=1, end=1 + len(self.title))
        self.assert_error("invalid_input", lambda: self.record_metadata(value))

    def test_non_ascii_match_uses_utf8_byte_offsets(self):
        value = self.payload()
        value["title"] = "\U0001f4d6"
        value["evidence"]["title"] = self.evidence_span("\U0001f4d6")
        self.assertEqual(value["evidence"]["title"]["end"] - value["evidence"]["title"]["start"], 4)
        self.record_metadata(value)
        self.assertEqual(project_citations(self.store.snapshot()["records"], self.artifacts)[0]["citation"],
                         "Ada Lovelace, Grace Hopper. \U0001f4d6")

    def test_changed_source_provenance_cannot_silently_rebind_metadata(self):
        self.record_metadata()
        self.put("source", self.response_source["id"], dict(self.response_source, origin_verified=True))
        self.assert_error("problem_citation_evidence_unbound", lambda: project_citations(self.store.snapshot()["records"], self.artifacts))

    def test_selected_record_is_revalidated_without_fallback(self):
        record = self.record_metadata()
        records = self.store.snapshot()["records"]
        records["problem_citation_metadata"][record["id"]]["title"] = "Unsupported correction"
        for projection in (project_citations, describe_binding, describe_evidence):
            self.assert_error("problem_citation_span_mismatch", lambda: projection(records, self.artifacts))
