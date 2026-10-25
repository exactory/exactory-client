"""Recover pinned manuscript bytes without losing edits or changing recorded measurements."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from development_fixtures import DevelopmentCase
from integration_fixtures import account_fixture_citations, build_manuscript_review, observed_candidate
from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.report_views import status_summary
from research_harness.storage import Store
from test_research_report_views import encoded_size, grand_challenge_view, report_fixture, round_view


PLUGIN = Path(__file__).resolve().parents[1]


def run_cli(root, *arguments):
    return subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), *arguments,
                           "--workspace", str(root)], capture_output=True, text=True,
                          env=dict(os.environ, EXACTORY_API_KEY=""))


class StoredBundleFilesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "study"
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.originals = {"draft/paper.pdf": b"%PDF-1.4 pinned\n", "draft/text/abstract.txt": b"Pinned abstract.\n",
                          "draft/references.bib": b"@article{pinned,title={Pinned paper}}\n",
                          "evidence/claims.json": b'[{"id":"bound","claim":"The bound is 9."}]',
                          "draft/sources.zip": b"PK\x03\x04pinned sources"}
        files = {}
        for kind, path in zip(("pdf", "abstract", "bibliography", "claims", "sources"), self.originals):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.originals[path])
            files[kind] = {"path": path, "artifact": self.artifacts.put(self.originals[path],
                                                                      "application/octet-stream")}
        self.bundle = {"id": "legacy-paper", "files": files, "prepared_revision": 0,
                       "review_inputs": {"candidate": {"recorded": "pin-time inputs"}},
                       "execution_observations": [{"recorded": "pin-time output"}]}
        self.bundle["digest"] = digest(self.bundle)
        self.save_bundle(self.bundle)

    def save_records(self, changes):
        revision = self.store.revision

        def apply(transaction):
            for kind, key, value in changes:
                transaction.put(kind, key, value)
            return {"historical_result": "retained", "scores": [3, 3, 4, 7]}

        return self.store.mutate("test.legacy", {}, apply, expected_revision=revision,
                                 request_id="fixture-" + str(revision))

    def save_bundle(self, bundle):
        self.save_records([("publication_bundle", bundle["id"], bundle),
                           ("publication_selection", "bundle", {"id": bundle["id"]})])

    def restore(self, bundle=None):
        result = run_cli(self.root, "restore", "--bundle", (bundle or self.bundle)["digest"])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def status(self, summary=True):
        result = run_cli(self.root, "status", *(("--summary",) if summary else ()))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue("bundle_drift" in report, "Status must identify drift during the selected measurement")
        return report

    def assert_refused(self, code):
        result = run_cli(self.root, "restore", "--bundle", self.bundle["digest"])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], code)

    def test_restore_keeps_every_displaced_file_and_preserves_legacy_records_and_receipts(self):
        self.save_records([("manuscript_review", "legacy-review", {
            "bundle_digest": self.bundle["digest"], "assessor": {"id": "legacy-assessor"},
            "reviewed_revision": 1, "core": {"overall": 7, "decision": "accept"}})])
        before = self.store.snapshot()
        database = (self.root / ".exactory/research.sqlite3").read_bytes()
        receipt = self.store.committed_request("fixture-1")
        changed = {path: b"later author bytes: " + data for path, data in self.originals.items()}
        for path, data in changed.items():
            (self.root / path).write_bytes(data)
        result = self.restore()
        self.assertEqual(result["bundle_digest"], self.bundle["digest"])
        self.assertEqual(result["bundle_id"], "legacy-paper")
        self.assertEqual(result["revision"], before["revision"])
        restored = {item["path"]: item for item in result["restored"]}
        self.assertEqual(set(restored), set(self.originals))
        self.assertEqual(result["unchanged"], [])
        for path, data in changed.items():
            self.assertEqual((self.root / path).read_bytes(), self.originals[path])
            self.assertEqual(restored[path]["backup"]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(self.artifacts.read(restored[path]["backup"]), data)
            self.assertEqual(self.artifacts.read(restored[path]["artifact"]), self.originals[path])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.committed_request("fixture-1"), receipt)
        self.assertEqual((self.root / ".exactory/research.sqlite3").read_bytes(), database)

    def test_restore_recreates_missing_parents_and_records_no_backup_for_absent_files(self):
        shutil.rmtree(self.root / "draft/text")
        (self.root / "draft/paper.pdf").unlink()
        result = self.restore()
        self.assertEqual({item["path"] for item in result["restored"]},
                         {"draft/paper.pdf", "draft/text/abstract.txt"})
        for item in result["restored"]:
            self.assertIsNone(item["backup"])
            self.assertEqual((self.root / item["path"]).read_bytes(), self.originals[item["path"]])

    def test_restore_is_a_noop_for_unchanged_files_and_safe_to_repeat(self):
        original_stat = (self.root / "draft/paper.pdf").stat()
        objects = set((self.root / "research/sources/objects").iterdir())
        result = self.restore()
        self.assertEqual(result["restored"], [])
        self.assertEqual(set(result["unchanged"]), set(self.originals))
        restored_stat = (self.root / "draft/paper.pdf").stat()
        self.assertEqual((restored_stat.st_ino, restored_stat.st_mtime_ns, restored_stat.st_mode),
                         (original_stat.st_ino, original_stat.st_mtime_ns, original_stat.st_mode))
        self.assertEqual(set((self.root / "research/sources/objects").iterdir()), objects)
        (self.root / "draft/paper.pdf").write_bytes(b"later PDF")
        first = self.restore()
        second = self.restore()
        self.assertEqual(second["restored"], [])
        self.assertEqual(self.artifacts.read(first["restored"][0]["backup"]), b"later PDF")

    def test_restore_uses_the_requested_historical_digest_without_changing_selection(self):
        later = copy.deepcopy(self.bundle)
        later["id"] = "later-paper"
        later["files"]["pdf"]["artifact"] = self.artifacts.put(b"%PDF-1.4 later", "application/pdf")
        later["digest"] = digest({key: value for key, value in later.items() if key != "digest"})
        self.save_bundle(later)
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 later")
        before = self.store.snapshot()
        result = self.restore()
        self.assertEqual(result["bundle_digest"], self.bundle["digest"])
        self.assertEqual((self.root / "draft/paper.pdf").read_bytes(), self.originals["draft/paper.pdf"])
        self.assertEqual(self.store.snapshot(), before)

    def test_unknown_and_malformed_digests_refuse_before_writing(self):
        before = self.store.snapshot()
        for supplied, code in (("f" * 64, "publication_bundle_missing"), ("short", "invalid_input"),
                               ("A" * 64, "invalid_input")):
            with self.subTest(digest=supplied):
                result = run_cli(self.root, "restore", "--bundle", supplied)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads(result.stderr)["error"]["code"], code)
        self.assertEqual(self.store.snapshot(), before)
        for path, data in self.originals.items():
            self.assertEqual((self.root / path).read_bytes(), data)

    def test_all_pinned_objects_are_verified_before_any_working_file_is_replaced(self):
        (self.root / "draft/paper.pdf").write_bytes(b"later PDF")
        corrupted = self.root / self.bundle["files"]["sources"]["artifact"]["path"]
        corrupted.chmod(0o600)
        corrupted.write_bytes(b"corrupt")
        self.assert_refused("artifact_corrupt")
        self.assertEqual((self.root / "draft/paper.pdf").read_bytes(), b"later PDF")

    def test_backup_storage_failure_leaves_all_working_files_untouched(self):
        (self.root / "draft/paper.pdf").write_bytes(b"later PDF")
        (self.root / "draft/references.bib").write_bytes(b"later bibliography")
        backup = self.artifacts.put(b"later PDF", "application/octet-stream")
        corrupted = self.root / backup["path"]
        corrupted.chmod(0o600)
        corrupted.write_bytes(b"corrupt backup")
        self.assert_refused("artifact_corrupt")
        self.assertEqual((self.root / "draft/paper.pdf").read_bytes(), b"later PDF")
        self.assertEqual((self.root / "draft/references.bib").read_bytes(), b"later bibliography")

    def test_symlink_file_destinations_are_refused_without_touching_the_target(self):
        target = self.root.parent / "outside.pdf"
        target.write_bytes(b"outside author file")
        (self.root / "draft/paper.pdf").unlink()
        (self.root / "draft/paper.pdf").symlink_to(target)
        self.assert_refused("unsafe_path")
        self.assertEqual(target.read_bytes(), b"outside author file")
        self.assertTrue((self.root / "draft/paper.pdf").is_symlink())

    def test_symlink_parent_destinations_are_refused_before_other_files_are_restored(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        shutil.rmtree(self.root / "draft/text")
        (self.root / "draft/text").symlink_to(outside, target_is_directory=True)
        (self.root / "draft/paper.pdf").write_bytes(b"later PDF")
        self.assert_refused("unsafe_path")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.root / "draft/paper.pdf").read_bytes(), b"later PDF")

    def test_directory_destinations_are_refused(self):
        (self.root / "draft/paper.pdf").unlink()
        (self.root / "draft/paper.pdf").mkdir()
        self.assert_refused("unsafe_path")
        self.assertTrue((self.root / "draft/paper.pdf").is_dir())

    def test_restore_refuses_paths_into_the_store_objects_or_outside_the_workspace(self):
        for path in (".exactory/research.sqlite3", ".git/config", "../outside.pdf", "/tmp/outside.pdf",
                     self.bundle["files"]["pdf"]["artifact"]["path"]):
            with self.subTest(path=path):
                invalid = copy.deepcopy(self.bundle)
                invalid["files"]["pdf"]["path"] = path
                self.save_bundle(invalid)
                database = (self.root / ".exactory/research.sqlite3").read_bytes()
                self.assert_refused("unsafe_path")
                self.assertEqual((self.root / ".exactory/research.sqlite3").read_bytes(), database)
        self.assertEqual(self.store.snapshot()["records"]["publication_bundle"]["legacy-paper"], invalid)

    def test_metadata_directory_case_alias_is_refused(self):
        database = self.root / ".exactory/research.sqlite3"
        bundle = copy.deepcopy(self.bundle)
        bundle["id"] = "metadata-alias-paper"
        bundle["files"]["sources"] = {
            "path": ".EXACTORY/research.sqlite3",
            "artifact": self.artifacts.put(database.read_bytes(), "application/octet-stream")}
        bundle["digest"] = digest({key: value for key, value in bundle.items() if key != "digest"})
        self.bundle = bundle
        self.save_bundle(bundle)
        before = database.read_bytes()
        self.assert_refused("unsafe_path")
        self.assertEqual(database.read_bytes(), before)

    def test_status_names_every_changed_or_missing_file_in_the_incomplete_bundle(self):
        (self.root / "draft/paper.pdf").write_bytes(b"later PDF")
        (self.root / "evidence/claims.json").unlink()
        before = self.store.snapshot()
        objects = set((self.root / "research/sources/objects").iterdir())
        for summary in (True, False):
            with self.subTest(summary=summary):
                drift = self.status(summary)["bundle_drift"]
                self.assertEqual(drift["bundle_id"], "legacy-paper")
                self.assertEqual(drift["bundle_digest"], self.bundle["digest"])
                self.assertEqual({item["path"] for item in drift["files"]},
                                 {"draft/paper.pdf", "evidence/claims.json"})
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(set((self.root / "research/sources/objects").iterdir()), objects)
        self.restore()
        self.assertIsNone(self.status()["bundle_drift"])

    def test_status_retains_drift_until_all_three_reviews_and_predictions_are_paired(self):
        (self.root / "draft/paper.pdf").write_bytes(b"later PDF")
        for number in range(3):
            assessor = "historical-assessor-" + str(number)
            self.save_records([("manuscript_review", assessor, {
                "bundle_digest": self.bundle["digest"], "assessor": {"id": assessor},
                "reviewed_revision": self.store.revision, "core": {"overall": 7, "decision": "accept"}})])
            self.assertIsNotNone(self.status()["bundle_drift"])
            self.save_records([("manuscript_prediction", assessor, {
                "bundle_digest": self.bundle["digest"], "payload": {"assessor": {"id": assessor}}})])
        self.assertIsNone(self.status()["bundle_drift"])

    def test_status_ignores_drift_in_a_superseded_incomplete_bundle(self):
        later = copy.deepcopy(self.bundle)
        later["id"] = "later-paper"
        later["files"]["pdf"]["artifact"] = self.artifacts.put(b"%PDF-1.4 later", "application/pdf")
        later["digest"] = digest({key: value for key, value in later.items() if key != "digest"})
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 later")
        self.save_bundle(later)
        self.assertIsNone(self.status()["bundle_drift"])

    def test_status_names_unsafe_files_without_following_symlinks(self):
        target = self.root.parent / "outside.pdf"
        target.write_bytes(b"outside author file")
        (self.root / "draft/paper.pdf").unlink()
        (self.root / "draft/paper.pdf").symlink_to(target)
        drift = self.status()["bundle_drift"]
        self.assertEqual(drift["files"][0]["path"], "draft/paper.pdf")
        self.assertEqual(drift["files"][0]["code"], "unsafe_path")
        self.assertEqual(target.read_bytes(), b"outside author file")


class BundleDriftViewTests(unittest.TestCase):
    def test_summary_keeps_all_file_identities_bounded_and_marks_truncated_names(self):
        report = report_fixture()
        long_text = "\x01" * 10000
        report["profile"] = long_text
        report["study"] = {"stage": long_text, "status": long_text, "waiting": long_text}
        report["next"] = {key: long_text for key in
                          ("code", "version_id", "work_id", "collection_id", "unit_id", "explanation")}
        report["obligations"] = [{"code": long_text + str(number)} for number in range(100)]
        report["preparation"]["obligations"] = report["obligations"]
        report["round"] = round_view()
        report["grand_challenge"] = grand_challenge_view()
        report["runtime"] = {"plugin_version": "0.39.0", "source_commit": "a" * 40, "dirty": True,
                             "executable": "/x" * 100, "package_digest": "b" * 64, "schema_version": 1,
                             "constitution": {"version": "3", "sha256": "c" * 64}}
        report["evaluation"] = {"reads": 10 ** 9, "artifacts_verified": 10 ** 9, "bytes_verified": 10 ** 12,
                                "computed": 10 ** 6, "readings_assessed": 10 ** 6, "links_validated": 10 ** 6,
                                "graph_builds": 3, "cohort_reports": 1, "elapsed_seconds": 123456.789}
        report["limits"] = {"policy": "lineage-v1", "loop": {"readings": 1000, "limit": 1000,
                            "covered": ["direct", "originals", "theory", "adjacent", "recent"]}, "sample": None,
                            "innovation_candidates": {"families": 100, "required": 100}, "search_readings": None,
                            "core_papers": None, "depth": {"plan_id": long_text, "values": {
                                "loop_abstracts": 1000, "round_loop_abstracts": 400, "hits_per_query": 100,
                                "innovation_candidates": 100, "innovation_cases": 50, "sample_size": 1000,
                                "search_readings": 200, "core_fulltexts": 100}}}
        report["probes"] = {"count": 10 ** 9, "elapsed_seconds": 10 ** 9 + 0.5}
        report["bundle_drift"] = {"bundle_id": long_text, "bundle_digest": "a" * 64,
                                  "files": [{"path": long_text + str(number), "code": "publication_artifact_changed"}
                                            for number in range(5)]}
        before = copy.deepcopy(report)
        summary = status_summary(report)
        self.assertTrue("bundle_drift" in summary, "The summary must retain the selected bundle's differing paths")
        drift = summary["bundle_drift"]
        self.assertEqual(drift["bundle_digest"], "a" * 64)
        self.assertEqual(len(drift["files"]), 5)
        self.assertTrue(drift["bundle_id_truncated"])
        self.assertTrue(all(item["path_truncated"] for item in drift["files"]))
        self.assertLessEqual(encoded_size(summary), 16 * 1024)
        self.assertEqual(summary["limits"]["depth"]["values"], report["limits"]["depth"]["values"])
        self.assertEqual(report, before)


class BundleRestoreReviewTests(DevelopmentCase):
    def test_a_review_of_the_pinned_bundle_is_accepted_after_restoring_nullable_source_files(self):
        from research_harness import publication
        self.prepared_study()
        execution = observed_candidate(self)
        (self.root / "draft").mkdir()
        files = {"pdf": "draft/paper.pdf", "abstract": "draft/abstract.txt", "bibliography": "draft/references.bib",
                 "claims": "draft/claims.json", "sources": None}
        contents = {"pdf": b"%PDF-1.4 pinned", "abstract": b"A finite bound was enumerated.",
                    "bibliography": b"@article{bound,title={Finite bound}}",
                    "claims": b'[{"id":"bound","claim":"The maximum is 9."}]'}
        for kind, data in contents.items():
            (self.root / files[kind]).write_bytes(data)
        payload = {"id": "paper-1", "files": files,
                   "claim_evidence": [{"claim_id": "bound", "evidence": [self.result_evidence(execution)]}],
                   "citation_accounting": account_fixture_citations(self)}
        bundle = self.mutate(publication.prepare_publication, payload)["result"]
        self.mutate(publication.record_manuscript_review, build_manuscript_review(self, bundle, "assessor-a"))
        review = build_manuscript_review(self, bundle, "assessor-b")
        before = self.store.snapshot()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 next revision")
        self.assert_error("publication_artifact_changed", lambda: self.mutate(publication.record_manuscript_review,
                                                                            review))
        restored = run_cli(self.root, "restore", "--bundle", bundle["digest"])
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.store.snapshot(), before)
        result = self.mutate(publication.record_manuscript_review, review)["result"]
        self.assertEqual(result["bundle_digest"], bundle["digest"])
        self.assertEqual(result["core"], json.loads(self.artifacts.read(review["review"])))
        self.assertEqual(self.store.snapshot()["records"]["publication_bundle"][bundle["id"]], bundle)


if __name__ == "__main__":
    unittest.main()
