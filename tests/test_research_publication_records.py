"""Memory-exposure provenance and content-addressed manuscript input snapshots."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from development_fixtures import DevelopmentCase
from integration_fixtures import account_fixture_citations, build_manuscript_review, observed_candidate
from research_harness import publication, review_packets
from research_harness.evidence import digest
import test_research_publication_readiness_binding as binding_fixtures


PLUGIN = Path(__file__).resolve().parents[1]
INPUTS = ("review_inputs", "execution_observations")


class PublicationRecordTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.execution_payload = observed_candidate(self)
        (self.root / "draft").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Publication record fixture.\n%%EOF")
        (self.root / "draft/abstract.txt").write_text("The finite maximum is nine.")
        (self.root / "draft/references.bib").write_text("@article{bounded,title={Authored bound}}\n")
        (self.root / "draft/claims.json").write_text(json.dumps([{"id": "bound", "claim": "The maximum is nine."}]))

    def build_payload(self, identifier="paper-1"):
        return {"id": identifier,
                "files": {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt",
                          "bibliography": "draft/references.bib", "claims": "draft/claims.json", "sources": None},
                "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(self.execution_payload)]}],
                "citation_accounting": account_fixture_citations(self)}

    def pin(self, identifier="paper-1"):
        return self.mutate(publication.prepare_publication, self.build_payload(identifier))["result"]

    def read_readiness(self):
        return publication._ready(self.store.snapshot()["records"], self.artifacts)

    def export(self, destination):
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "export",
                                 "--workspace", str(self.root), "--kind", "manuscript",
                                 "--destination", str(self.root / destination)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.root / destination / "inputs.json").read_text())

    def build_inline_view(self, bundle, report):
        result = copy.deepcopy(bundle)
        for key in INPUTS:
            result.pop(key + "_artifact", None)
            result[key] = copy.deepcopy(report[key])
        return result

    def build_reference_view(self, bundle, report):
        """Build the reference shape independently so reader tests do not depend on the new writer."""
        result = copy.deepcopy(bundle)
        for key in INPUTS:
            data = json.dumps(report[key], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            result.pop(key, None)
            result[key + "_artifact"] = self.artifacts.put(data, "application/json")
        result["digest"] = digest({key: value for key, value in result.items() if key != "digest"})
        return result

    def pin_reference_fixture(self):
        report = self.read_readiness()
        bundle = self.build_reference_view(self.pin(), report)
        self.store.mutate("fixture.reference-pin", {}, lambda tx: tx.put("publication_bundle", bundle["id"], bundle),
                          expected_revision=self.store.revision, request_id="reference-pin")
        return report, bundle

    def pin_legacy(self):
        """Write the pre-0.62.0 inline shape, including its original event and replay result."""
        records = self.store.snapshot()["records"]
        report, payload = self.read_readiness(), self.build_payload("legacy-paper")
        files = {key: None if path is None else {
            "path": path, "artifact": self.artifacts.put((self.root / path).read_bytes(), publication.FILE_TYPES[key])}
            for key, path in payload["files"].items()}
        review_id = records["development_selection"]["review"]["review_id"]
        bundle = dict(payload, files=files, candidate=report["candidate"], readiness_digest=digest(report),
                      citation_accounting=publication.account_citations(
                          records, (self.root / payload["files"]["bibliography"]).read_bytes(),
                          payload["citation_accounting"]),
                      readiness_review=records["readiness_review"][review_id],
                      readiness_binding=publication._scientific_readiness(report, records),
                      review_inputs=report["review_inputs"], execution_observations=report["execution_observations"],
                      prepared_revision=self.store.revision, mechanical_only=True)
        bundle["digest"] = digest(bundle)

        def save(transaction):
            transaction.put("publication_bundle", bundle["id"], bundle)
            transaction.put("publication_selection", "bundle", {"id": bundle["id"]})
            return bundle

        receipt = self.store.mutate("fixture.legacy-pin", {}, save, expected_revision=self.store.revision,
                                    request_id="legacy-pin")
        return bundle, receipt

    def test_manuscript_review_command_retains_true_and_false_memory_disclosures(self):
        # Dropping the optional provenance field, or coercing false to an omission, loses the disclosure.
        bundle = self.pin()
        for present in (False, True):
            with self.subTest(present=present):
                identifier = "reviewer-" + str(present)
                payload = build_manuscript_review(self, bundle, identifier)
                payload["assessor"]["author_memory_notes_present"] = present
                path = self.root / (identifier + ".json")
                path.write_text(json.dumps(payload))
                result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "manuscript-review",
                    "--workspace", str(self.root), "--file", str(path), "--expected-revision", str(self.store.revision),
                    "--request-id", identifier], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                saved = self.store.snapshot()["records"]["manuscript_review"][identifier]
                self.assertIs(saved["assessor"]["author_memory_notes_present"], present)
                self.assertEqual(saved["digest"], digest(payload))
                self.assertEqual(json.loads(result.stdout)["result"], saved)
        report = publication.publication_report(self.store)
        self.assertTrue(report["ready"], report["obligations"])

    def test_memory_disclosure_accepts_only_booleans_and_does_not_default_legacy_absence(self):
        # Truthy strings, integers and null must not become evidence that memory was absent or present.
        assessor = {"id": "independent", "kind": "agent", "relationship": "Separate reviewer.",
                    "independence_basis": "Observed independent assignment.",
                    "provenance": self.artifacts.put(b"Reviewer context receipt.", "text/plain")}
        for kind in ("human", "agent"):
            for present in (False, True):
                disclosed = dict(assessor, kind=kind, author_memory_notes_present=present)
                self.assertEqual(publication.validate_assessor(self.artifacts, disclosed, []), disclosed)
        for invalid in (None, 0, 1, "false", "true", [], {}):
            with self.subTest(invalid=invalid):
                self.assert_error("invalid_input", lambda: publication.validate_assessor(
                    self.artifacts, dict(assessor, author_memory_notes_present=invalid), []))
        self.assertEqual(publication.validate_assessor(self.artifacts, assessor, []), assessor)
        self.assertNotIn("author_memory_notes_present", assessor)

    def test_legacy_review_without_memory_metadata_keeps_its_recorded_result(self):
        # Reading an old review must not add a default disclosure or change the gate because it is missing.
        bundle = self.pin()
        old_payload = build_manuscript_review(self, bundle, "legacy-reviewer")
        old = self.mutate(publication.record_manuscript_review, old_payload)["result"]
        before = publication.publication_report(self.store)
        self.assertNotIn("author_memory_notes_present", before["reviews"][0]["assessor"])
        payload = build_manuscript_review(self, bundle, "disclosed-reviewer")
        payload["assessor"]["author_memory_notes_present"] = False
        self.mutate(publication.record_manuscript_review, payload)
        after = publication.publication_report(self.store)
        self.assertTrue(after["ready"], after["obligations"])
        self.assertIn(old, after["reviews"])
        self.assertEqual(self.store.snapshot()["records"]["manuscript_review"][old["id"]], old)
        self.assertNotIn("author_memory_notes_present", old["assessor"])

    def test_repeated_pins_store_canonical_exports_once_by_digest(self):
        # Repeating an export inline, pretty-printing its bytes, or using a non-content address grows the store.
        report = self.read_readiness()
        report["review_inputs"]["fixture_note"] = {"z": "The bound is ≤ nine.", "a": "A UTF-8 export."}
        with patch.object(publication, "_ready", return_value=report):
            first = self.pin()
            objects = set((self.root / "research/sources/objects").iterdir())
            second = self.pin("paper-2")
        self.assertEqual(set((self.root / "research/sources/objects").iterdir()), objects)
        for key in INPUTS:
            with self.subTest(key=key):
                self.assertFalse(key in first, "The new pin must not copy " + key + " inline")
                reference = first[key + "_artifact"]
                self.assertEqual(reference, second[key + "_artifact"])
                self.assertEqual(reference["sha256"], digest(report[key]))
                self.assertEqual(reference["media_type"], "application/json")
                self.assertEqual(json.loads(self.artifacts.read(reference)), report[key])
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertEqual(first["digest"], digest({k: v for k, v in first.items() if k != "digest"}))

    def test_pin_size_does_not_grow_with_the_review_input_or_observation_exports(self):
        # Merely adding digests while keeping either inline value would retain the original size problem.
        report = self.read_readiness()
        with patch.object(publication, "_ready", return_value=report):
            small = self.pin()
        expanded = copy.deepcopy(report)
        for key in INPUTS:
            expanded[key]["fixture_padding"] = "recorded packet evidence " * 20000
        with patch.object(publication, "_ready", return_value=expanded):
            first_large = self.pin("paper-2")
            objects = set((self.root / "research/sources/objects").iterdir())
            second_large = self.pin("paper-3")
        for bundle in (first_large, second_large):
            self.assertLess(abs(len(json.dumps(bundle)) - len(json.dumps(small))), 1024)
            for key in INPUTS:
                self.assertFalse(key in bundle, "The new pin must not copy " + key + " inline")
                self.assertEqual(json.loads(self.artifacts.read(bundle[key + "_artifact"])), expanded[key])
        self.assertEqual(set((self.root / "research/sources/objects").iterdir()), objects)

    def test_changing_one_export_only_stores_the_changed_object(self):
        # Reusing the prior digest loses changed inputs; storing both again loses deduplication.
        report = self.read_readiness()
        with patch.object(publication, "_ready", return_value=report):
            previous = self.pin()
        self.assertTrue("review_inputs_artifact" in previous, "The pin must reference its review inputs")
        for number, changed in enumerate(INPUTS, 2):
            report = copy.deepcopy(report)
            report[changed]["fixture_change"] = "A distinct export."
            objects = set((self.root / "research/sources/objects").iterdir())
            with patch.object(publication, "_ready", return_value=report):
                current = self.pin("paper-" + str(number))
            for key in INPUTS:
                with self.subTest(changed=changed, key=key):
                    if key == changed:
                        self.assertNotEqual(current[key + "_artifact"], previous[key + "_artifact"])
                    else:
                        self.assertEqual(current[key + "_artifact"], previous[key + "_artifact"])
            new_objects = set((self.root / "research/sources/objects").iterdir()) - objects
            self.assertEqual(new_objects, {self.root / current[changed + "_artifact"]["path"]})
            previous = current

    def test_manuscript_export_resolves_pinned_objects_without_delivering_author_context(self):
        # Export must resolve the pinned standards and observations while keeping private context out of the packet.
        report, bundle = self.pin_reference_fixture()
        expected = review_packets.manuscript_packet(self.store.snapshot()["records"],
                                                    self.build_inline_view(bundle, report))
        before = self.store.snapshot()
        packet = self.export("reference-export")
        self.assertEqual(packet, expected)
        self.assertEqual(self.store.snapshot(), before)
        for key in INPUTS:
            reference = bundle[key + "_artifact"]
            self.assertFalse((self.root / "reference-export" / reference["path"]).exists())
        self.assertNotIn("review_inputs", json.dumps(packet))
        self.assertNotIn('"authors"', json.dumps(packet))
        execution = self.execution_payload["id"]
        self.assertEqual(packet["results"][execution]["observation"]["observation"]["terminal"],
                         report["execution_observations"][execution]["observation"]["terminal"])

    def test_legacy_inline_bundle_exports_reviews_and_replays_without_migration_in_a_mixed_store(self):
        # Upgrading must not rewrite an old bundle digest, its review, or its original request result.
        legacy, receipt = self.pin_legacy()
        old_packet = self.export("legacy-export-before")
        old_bytes = (self.root / "legacy-export-before/inputs.json").read_bytes()
        for identifier in ("legacy-reviewer-a", "legacy-reviewer-b"):
            self.mutate(publication.record_manuscript_review, build_manuscript_review(self, legacy, identifier))
        old_report = publication.publication_report(self.store)
        self.assertTrue(old_report["ready"], old_report["obligations"])
        old_reviews = copy.deepcopy(self.store.snapshot()["records"]["manuscript_review"])
        current = self.pin("paper-2")
        self.assertTrue("review_inputs_artifact" in current, "The later pin must use content-addressed inputs")
        self.store.mutate("fixture.select-legacy", {}, lambda tx: tx.put(
            "publication_selection", "bundle", {"id": legacy["id"]}),
            expected_revision=self.store.revision, request_id="select-legacy")
        self.assertEqual(self.export("legacy-export-after"), old_packet)
        self.assertEqual((self.root / "legacy-export-after/inputs.json").read_bytes(), old_bytes)
        self.assertEqual(publication.publication_report(self.store), old_report | {"revision": self.store.revision})
        records = self.store.snapshot()["records"]
        self.assertEqual(records["publication_bundle"][legacy["id"]], legacy)
        self.assertEqual(records["manuscript_review"], old_reviews)
        self.assertIn("review_inputs", legacy)
        self.assertNotIn("review_inputs_artifact", legacy)
        replay = self.store.mutate("fixture.legacy-pin", {}, lambda tx: self.fail("Replay invoked the writer"),
                                   expected_revision=0, request_id="legacy-pin")
        self.assertEqual(replay, receipt)

    def test_missing_or_corrupt_pinned_exports_fail_closed_without_changing_records(self):
        # Neither the live readiness export nor a competing inline copy can hide a broken pinned object.
        unused, bundle = self.pin_reference_fixture()
        before = self.store.snapshot()
        for key in INPUTS:
            reference = bundle[key + "_artifact"]
            path, data = self.root / reference["path"], self.artifacts.read(reference)
            for code in ("artifact_missing", "artifact_corrupt"):
                with self.subTest(key=key, code=code):
                    path.unlink()
                    if code == "artifact_corrupt":
                        path.write_bytes(b"{}")
                    try:
                        records = self.store.snapshot()["records"]
                        records["publication_bundle"][bundle["id"]][key] = self.read_readiness()[key]
                        self.assert_error(code, lambda: publication._bundle(records, self.artifacts))
                    finally:
                        if path.exists():
                            path.unlink()
                        path.write_bytes(data)
                        os.chmod(path, 0o444)
        self.assertEqual(self.store.snapshot(), before)


class HistoricalBundleInputTests(DevelopmentCase):
    def test_historical_readiness_rebuild_resolves_the_pinned_export(self):
        # A current report is not a substitute for the authenticated pin-time snapshot used by managed currency.
        bundle = binding_fixtures.ManagedPublicationCurrencyTests.prepare_managed_bundle(self)
        records = self.store.snapshot()["records"]
        report = publication._ready(records, self.artifacts)
        inline = PublicationRecordTests.inline_view(self, bundle, report)
        expected = publication.rebuild_pinned_readiness(records, self.artifacts, inline)
        bundle = PublicationRecordTests.reference_view(self, bundle, report)
        self.assertEqual(publication.rebuild_pinned_readiness(records, self.artifacts, bundle), expected)
        altered = copy.deepcopy(bundle)
        inputs = json.loads(self.artifacts.read(altered["review_inputs_artifact"]))
        inputs["candidate"]["source_records_digest"] = "unrecorded-input"
        altered["review_inputs_artifact"] = self.artifacts.put(json.dumps(inputs).encode(), "application/json")
        self.assert_error("claim_reassessment_required", lambda: publication.rebuild_pinned_readiness(
            records, self.artifacts, altered))
