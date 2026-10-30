"""Later DOI aliases need advisory import diagnostics and occurrence-level family choices."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from literature_fixtures import LiteratureCase
from research_fixtures import atom, crossref, entry
from research_harness import acquisition
from research_harness.errors import ResearchError
from research_harness.gates import gate_report, require_ready
from research_harness.graph import citation_graph
from research_harness.identities import resolve_family
from research_harness.literature import import_bundle
from research_harness.principles import initialize_research
from research_harness.reading import bibliography_digest, record_reading, unit_digest


DOI = "doi:10.1234/example"
ARXIV = "arxiv:2601.00002"
IMPORT_URL = "https://export.arxiv.org/api/query?id_list=2601.00002v1"
CAPTURED_AT = "2026-09-07T12:00:00Z"
PLUGIN = Path(__file__).resolve().parents[1]


class ReferenceFamilyTests(LiteratureCase):
    def import_bytes(self, provider, raw, request_id, **kwargs):
        return acquisition.import_response(
            self.store, provider, raw, source_url=IMPORT_URL, captured_at=CAPTURED_AT,
            expected_revision=self.store.revision, request_id=request_id, **kwargs)

    def import_doi_metadata(self):
        data = crossref()
        data["message"]["reference"] = []
        data["message"]["reference-count"] = 0
        return self.import_bytes("crossref", json.dumps(data).encode(), "doi-metadata")

    def build_arxiv_bytes(self, identifier="2601.00002v1"):
        return atom([entry(identifier, doi="10.1234/example")], total=1)

    def build_conflict(self, existing=(DOI,), incoming=(ARXIV,)):
        return {"identifier": DOI, "existing_families": list(existing), "incoming_families": list(incoming),
                "candidates": sorted(set(existing) | set(incoming))}

    def preview(self, raw):
        preview = getattr(acquisition, "preview_import_response", None)
        self.assertTrue(callable(preview), "A read-only import preview API is missing")
        return preview(self.store, "arxiv", raw, source_url=IMPORT_URL, captured_at=CAPTURED_AT)

    def read_files(self):
        return {str(path.relative_to(self.root)): (path.stat().st_mode, path.read_bytes())
                for path in self.root.rglob("*") if path.is_file()}

    def build_cited_bundle(self, *, selected=False):
        root = self.metadata(references=[{"id": DOI}])
        self.import_doi_metadata()
        self.scope([root])
        capture = self.capture(root, "The proof cites the journal article. References: doi:10.1234/example.")
        bundle = self.bundle(root, capture, "before-alias")
        reference = {"target": DOI, "kind": "paper", "reason": "The bibliography identifies the journal article.",
                     "link": self.link(root, capture["source_id"], capture["text"], "doi:10.1234/example")}
        if selected:
            reference["target_family"] = DOI
        bundle["bibliography"]["entries"] = [reference]
        return root, bundle

    def test_an_import_refuses_a_concurrent_write_instead_of_reporting_conflicts_of_an_older_snapshot(self):
        # The family conflicts come from the snapshot that the import read, so its finishing write keeps that revision.
        self.import_doi_metadata()
        real_prepare_work, written = acquisition.prepare_work, []

        def prepare_after_a_concurrent_write(*args, **kwargs):
            if not written:
                written.append(True)
                self.store.mutate("fixture.concurrent", {}, lambda tx: tx.put("fixture_concurrency", "writer", {}),
                                  expected_revision=self.store.revision, request_id="concurrent-writer")
            return real_prepare_work(*args, **kwargs)

        with patch.object(acquisition, "prepare_work", side_effect=prepare_after_a_concurrent_write):
            with self.assertRaises(ResearchError) as raised:
                self.import_bytes("arxiv", self.build_arxiv_bytes(), "raced-arxiv")
        self.assertEqual(raised.exception.code, "stale_revision")
        self.assertNotIn("raced-arxiv", self.store.snapshot()["records"].get("source_import", {}))

    def test_import_reports_new_doi_family_conflicts_and_replays_its_recorded_report(self):
        self.import_doi_metadata()
        before = self.store.snapshot()["records"]
        raw = self.build_arxiv_bytes()
        revision = self.store.revision
        result = self.import_bytes("arxiv", raw, "later-arxiv")
        self.assertEqual(result.get("identity_conflicts"), [self.build_conflict()])
        self.assertEqual(result["status"], "complete")
        records = self.store.snapshot()["records"]
        self.assertEqual(records["work"][DOI], before["work"][DOI])
        self.assertEqual(set(records["work_family"]), {DOI, ARXIV})
        self.assertEqual(records["alias"][DOI]["assertions"][0]["source_id"], result["source_ids"][0])
        with self.assertRaises(ResearchError) as raised:
            resolve_family(records, DOI)
        self.assertEqual(raised.exception.code, "ambiguous_alias")
        self.import_bytes("arxiv", self.build_arxiv_bytes("2601.00003v1"), "another-alias")
        original = acquisition.import_response(
            self.store, "arxiv", raw, source_url=IMPORT_URL, captured_at=CAPTURED_AT,
            expected_revision=revision, request_id="later-arxiv")
        self.assertEqual(original, result)
        self.assertEqual(self.import_bytes("arxiv", raw, "repeat-alias")["identity_conflicts"], [])

        # An old final result has no advisory field; replay must never backfill one.
        legacy_result = {key: value for key, value in result.items() if key != "identity_conflicts"}

        def keep_legacy_result(transaction):
            operation = transaction.get("acquisition_operation", "later-arxiv")
            operation["result"] = legacy_result
            transaction.put("acquisition_operation", "later-arxiv", operation)

        self.store.mutate("fixture.legacy-result", {}, keep_legacy_result,
                          expected_revision=self.store.revision, request_id="legacy-result")
        self.assertEqual(acquisition.import_response(
            self.store, "arxiv", raw, source_url=IMPORT_URL, captured_at=CAPTURED_AT,
            expected_revision=revision, request_id="later-arxiv"), legacy_result)

    def test_preview_predicts_the_import_without_writing_records_artifacts_or_receipts(self):
        self.import_doi_metadata()
        before, files = self.store.snapshot(), self.read_files()
        raw = self.build_arxiv_bytes()
        preview = self.preview(raw)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["revision"], before["revision"])
        self.assertEqual(preview["work_ids"], [ARXIV + "v1"])
        self.assertEqual(preview["identity_conflicts"], [self.build_conflict()])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.read_files(), files)
        imported = self.import_bytes("arxiv", raw, "previewed-import")
        self.assertEqual(imported["identity_conflicts"], preview["identity_conflicts"])

    def test_preview_checks_the_whole_batch_and_distinguishes_repeated_family_assertions(self):
        self.import_doi_metadata()
        rows = [entry("2601.00003v1", doi="10.1234/example"), entry("2601.00002v1", doi="10.1234/example")]
        expected = [self.build_conflict(incoming=(ARXIV, "arxiv:2601.00003"))]
        for batch in (rows, list(reversed(rows))):
            self.assertEqual(self.preview(atom(batch, total=2))["identity_conflicts"], expected)
        self.import_bytes("arxiv", self.build_arxiv_bytes(), "first-alias")
        self.assertEqual(self.preview(self.build_arxiv_bytes("2601.00002v2"))["identity_conflicts"], [])
        other_doi = "doi:10.1234/batch"
        batch = atom([entry("2601.00004v1", doi="10.1234/batch"),
                      entry("2601.00005v1", doi="10.1234/batch")], total=2)
        self.assertEqual(self.preview(batch)["identity_conflicts"], [{
            "identifier": other_doi, "existing_families": [],
            "incoming_families": ["arxiv:2601.00004", "arxiv:2601.00005"],
            "candidates": ["arxiv:2601.00004", "arxiv:2601.00005"]}])
        self.assertEqual(self.preview(atom([entry("2601.00006v1")], total=1))["identity_conflicts"], [])

    def test_cli_preview_requires_no_mutation_identity_and_never_creates_a_store(self):
        self.import_doi_metadata()
        raw_file = self.root / "incoming.xml"
        raw_file.write_bytes(self.build_arxiv_bytes())
        payload_file = self.root / "import.json"
        payload_file.write_text(json.dumps({"provider": "arxiv", "response_file": str(raw_file),
                                            "source_url": IMPORT_URL, "captured_at": CAPTURED_AT}))
        before, files = self.store.snapshot(), self.read_files()

        def invoke(workspace, *extra):
            return subprocess.run(
                [sys.executable, str(PLUGIN / "bin/exactory-research"), "import-response",
                 "--workspace", str(workspace), "--file", str(payload_file), *extra],
                cwd=PLUGIN, capture_output=True, text=True,
                env=dict(os.environ, EXACTORY_API_KEY="", PYTHONDONTWRITEBYTECODE="1"))

        result = invoke(self.root, "--preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["identity_conflicts"], [self.build_conflict()])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.read_files(), files)
        self.assertNotEqual(invoke(self.root).returncode, 0)
        self.assertEqual(self.store.snapshot(), before)
        empty = self.root.parent / "empty"
        empty.mkdir()
        self.assertNotEqual(invoke(empty, "--preview").returncode, 0)
        self.assertEqual(list(empty.iterdir()), [])
        imported = invoke(self.root, "--expected-revision", str(before["revision"]), "--request-id", "cli-import")
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)["identity_conflicts"], [self.build_conflict()])

    def test_evidence_backed_doi_choice_resolves_old_and_current_occurrences_in_preparation_gate(self):
        root, first = self.build_cited_bundle()
        self.mutate(initialize_research, {"profile": "research", "target": None})
        self.mutate(import_bundle, first)
        self.mutate(record_reading, self.full_note(first, "earlier-full-reading"))
        original = copy.deepcopy(self.store.snapshot()["records"]["reference_occurrence"])
        self.assertFalse(any(o["code"] == "reference_identity_ambiguous"
                             for o in gate_report(self.store, "preparation")["obligations"]))
        self.import_bytes("arxiv", self.build_arxiv_bytes(), "late-identity")
        before = self.store.snapshot()
        blocked = gate_report(self.store, "preparation")
        ambiguous = {o["reference_id"] for o in blocked["obligations"] if o["code"] == "reference_identity_ambiguous"}
        self.assertEqual(ambiguous, set(original))
        with self.assertRaises(ResearchError):
            require_ready(blocked, "preparation")
        second = copy.deepcopy(first)
        second["id"] = "after-alias"
        second["bibliography"]["entries"][0]["target_family"] = DOI
        second["resolutions"] = [dict(second["bibliography"]["entries"][0], reference_id=identifier)
                                 for identifier in sorted(original)]
        self.mutate(import_bundle, second)
        records = self.store.snapshot()["records"]
        self.assertEqual({key: records["reference_occurrence"][key] for key in original}, original)
        self.assertEqual(records["alias"], before["records"]["alias"])
        self.assertEqual(records["reading"], before["records"]["reading"])
        current = gate_report(self.store, "preparation")
        self.assertNotIn("reference_identity_ambiguous", {o["code"] for o in current["obligations"]})
        self.assertNotIn("fulltext_reading_missing", {o["code"] for o in current["obligations"]
                                                    if o.get("version_id") == root})
        graph = citation_graph(records, "research")
        self.assertEqual({r["target_family"] for r in graph["references"]}, {DOI})
        self.assertEqual({node["work_id"] for node in graph["nodes"]}, {root[:-2], DOI})
        self.assertEqual(self.store.snapshot()["records"], records)
        with self.assertRaises(ResearchError) as raised:
            resolve_family(records, DOI)
        self.assertEqual(raised.exception.code, "ambiguous_alias")

    def test_graph_honors_only_explicit_choices_and_keeps_exact_arxiv_versions(self):
        root, bundle = self.build_cited_bundle()
        self.mutate(import_bundle, bundle)
        self.import_bytes("arxiv", self.build_arxiv_bytes(), "late-identity")
        self.import_bytes("arxiv", self.build_arxiv_bytes("2601.00002v2"), "later-version")
        records = self.store.snapshot()["records"]
        ids = sorted(records["reference_occurrence"])
        selected, untouched = ids
        evidence = dict(bundle["bibliography"]["entries"][0], reference_id=selected, bundle_id=bundle["id"])
        records["reference_resolution"] = {selected: dict(evidence, target_family=DOI)}

        def read_rows():
            return {row["occurrence_id"]: row for row in citation_graph(records, "research")["references"]}

        self.assertEqual(read_rows()[selected]["status"], "resolved")
        self.assertEqual(read_rows()[selected]["version_ids"], [DOI])
        self.assertEqual(read_rows()[untouched]["status"], "ambiguous_alias")
        records["reference_resolution"][selected] = dict(evidence, target_family=ARXIV)
        self.assertEqual(read_rows()[selected]["version_ids"], [ARXIV + "v1", ARXIV + "v2"])
        records["reference_resolution"][selected] = dict(evidence, target=ARXIV + "v1", target_family=ARXIV)
        self.assertEqual(read_rows()[selected]["version_ids"], [ARXIV + "v1"])
        records["reference_resolution"][selected] = evidence
        self.assertEqual(read_rows()[selected]["status"], "ambiguous_alias")
        self.assertEqual(read_rows()[untouched]["status"], "ambiguous_alias")

    def test_family_choices_reject_unknown_unrelated_versioned_and_noncanonical_families(self):
        _, bundle = self.build_cited_bundle(selected=True)
        self.import_bytes("arxiv", self.build_arxiv_bytes(), "late-identity")
        self.metadata(3)
        bad_choices = ("doi:10.1234/missing", "arxiv:2601.00003", ARXIV + "v1",
                       "10.1234/example", "doi:10.1234/EXAMPLE", None, [])
        for choice in bad_choices:
            with self.subTest(choice=choice):
                invalid = copy.deepcopy(bundle)
                invalid["bibliography"]["entries"][0]["target_family"] = choice
                before = self.store.snapshot()
                with self.assertRaises(ResearchError) as raised:
                    self.mutate(import_bundle, invalid)
                self.assertEqual(raised.exception.code, "invalid_family_selection")
                self.assertEqual(self.store.snapshot(), before)

    def test_family_choice_still_requires_bibliography_evidence_and_paper_classification(self):
        _, bundle = self.build_cited_bundle(selected=True)
        self.mutate(import_bundle, bundle)
        cases = (("reason", "", "invalid_bibliography"), ("link", None, "source_mismatch"),
                 ("kind", "nonpaper", "invalid_bibliography"), ("target", None, "invalid_bibliography"))
        for field, value, code in cases:
            with self.subTest(field=field):
                invalid = copy.deepcopy(bundle)
                invalid["id"] = "invalid-" + field
                invalid["bibliography"]["entries"][0][field] = value
                before = self.store.snapshot()
                with self.assertRaises(ResearchError) as raised:
                    self.mutate(import_bundle, invalid)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self.store.snapshot(), before)
        invalid = copy.deepcopy(bundle)
        invalid["id"] = "invalid-span"
        invalid["bibliography"]["entries"][0]["link"]["locator"]["quote"] = "Not in the captured article"
        self.assert_error("invalid_locator", lambda: self.mutate(import_bundle, invalid))

    def test_family_choice_changes_bibliography_identity_without_staling_reading_units(self):
        _, bundle = self.build_cited_bundle()
        records = self.store.snapshot()["records"]
        original = bibliography_digest(bundle, records)
        self.assertEqual(original, "99017c4dbd95514d0a1f3866dff859426b4d38aa0d49949794cee66795fe47b3")
        selected = copy.deepcopy(bundle)
        selected["bibliography"]["entries"][0]["target_family"] = DOI
        self.assertNotEqual(bibliography_digest(selected, records), original)
        self.assertEqual(unit_digest(selected, records), unit_digest(bundle, records))
        selected["bibliography"]["entries"][0]["target_family"] = ARXIV
        self.assertNotEqual(bibliography_digest(selected, records), bibliography_digest(
            dict(bundle, bibliography=dict(bundle["bibliography"], entries=[
                dict(bundle["bibliography"]["entries"][0], target_family=DOI)])), records))
        self.assertEqual(bibliography_digest(bundle, records), original)
