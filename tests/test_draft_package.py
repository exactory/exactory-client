"""Offline deposit archives built from authored records and frozen execution bytes."""

import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness.operations import prepared_mutation
from research_harness.storage import Store


PLUGIN = Path(__file__).resolve().parents[1]
PROGRAM = b'''import json
from pathlib import Path
from helper import OFFSET
value = json.loads(Path("data/input.json").read_text())["value"] + OFFSET
Path("results").mkdir(exist_ok=True)
Path("results/result.json").write_text(json.dumps({"value": value}))
'''


class DraftPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.records = {}
        self.first = self.record_cited_run("run-1", 1)
        self.second = self.record_cited_run("run-2", 2)
        self.record_cited_run("uncited", 99)
        self.claims = [{"id": "claim", "claim": "The two historical versions give 11 and 12."}]
        self.links = [{"claim_id": "claim",
                       "evidence": [self.build_result_ref(self.first), self.build_result_ref(self.second)]}]
        claim_ref = self.put(json.dumps(self.claims).encode(), "application/json")
        self.bundle = {"id": "paper-1", "files": {"claims": {"path": "evidence/claims.json", "artifact": claim_ref}},
                       "claim_evidence": self.links, "review_inputs": {"historical": True},
                       "execution_observations": {}, "prepared_revision": 0}
        self.bundle["digest"] = digest(self.bundle)
        self.records["publication_bundle"] = {"paper-1": self.bundle}
        self.records["publication_selection"] = {"bundle": {"id": "paper-1"}}
        for kind in ("cycle_assessment", "checkpoint", "readiness_review", "manuscript_review",
                     "manuscript_prediction", "contribution_analysis", "round_decision", "round_review",
                     "source_deferral", "workspace_decision", "strategy_account", "development_scope"):
            self.records[kind] = {"record/a:b": {"id": "record/a:b", "historical_result": "retained"},
                                  "record:a/b": {"id": "record:a/b", "historical_result": "also retained"}}
        self.save()

    def put(self, data, media_type="application/octet-stream"):
        return self.artifacts.put(data, media_type)

    def record_cited_run(self, identifier, offset):
        program = self.put(PROGRAM + ("# Recorded program version %d.\n" % offset).encode(), "text/x-python")
        module = self.put(("OFFSET = %d\n" % offset).encode(), "text/x-python")
        data = self.put(b'{"value": 10}', "application/json")
        command = {"argv": [sys.executable, str(self.root / "experiment/code/main.py")],
                   "program": program, "inputs": [module, data], "versions": {"python": sys.version.split()[0]},
                   "seed": None, "seed_reason": "Deterministic authored fixture."}
        admission = {"id": identifier, "cycle_id": "cycle-" + identifier, "command": command}
        admission["digest"] = digest(admission)
        output = self.put(json.dumps({"value": 10 + offset}).encode(), "application/json")
        image = self.put(b"\x89PNG\r\n\x1a\nfixture-" + str(offset).encode(), "image/png")
        execution = {"id": "execution-" + identifier, "cycle_id": admission["cycle_id"],
                     "origin": {"kind": "managed", "admission_id": identifier}, "command": command,
                     "status": "completed", "exit_code": 0,
                     "outputs": [{"id": "result", "requirement_id": "measurement", "artifact": output},
                                 {"id": "figure", "requirement_id": "figure", "artifact": image}]}
        binding = {"admission_id": identifier, "admission_digest": admission["digest"], "script": "code/main.py",
                   "backend": "local", "runtime": {"path": sys.executable},
                   "inputs": [{"path": "code/helper.py", "artifact": module},
                              {"path": "data/input.json", "artifact": data}],
                   "outputs": [{"id": "result", "path": "results/result.json", "media_type": "application/json"},
                               {"id": "figure", "path": "plots/figure.png", "media_type": "image/png"}]}
        for kind, key, value in (("execution_admission", identifier, admission),
                                 ("execution_binding", identifier, binding),
                                 ("execution", execution["id"], {"payload": execution, "digest": digest(execution)}),
                                 ("cycle_plan", admission["cycle_id"], {"payload": {"id": admission["cycle_id"]}})):
            self.records.setdefault(kind, {})[key] = value
        return execution

    def build_result_ref(self, execution):
        output = execution["outputs"][0]
        value = json.loads(self.artifacts.read(output["artifact"]))["value"]
        return {"kind": "result", "execution_id": execution["id"], "output_id": output["id"],
                "artifact": output["artifact"], "locator": {"kind": "json", "pointer": "/value", "value": value}}

    def save(self):
        prepared_mutation(self.store, "test.package-fixture", {"revision": self.store.revision},
                          lambda records, value: ([(kind, key, item) for kind, group in self.records.items()
                                                   for key, item in group.items()], {}),
                          expected_revision=self.store.revision, request_id="fixture-" + str(self.store.revision))

    def invoke(self, *args, cwd=None):
        environment = dict(os.environ, EXACTORY_API_KEY="")
        return subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-draft"), "package", *args],
                              cwd=cwd or self.root, env=environment, capture_output=True, text=True)

    def package(self, name="sources.zip", *args):
        result = self.invoke("--output", name, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.root / name
        self.assertTrue(path.is_file(), "Package must create the requested ZIP.")
        return path

    def read_manifest(self, archive):
        return json.loads(archive.read("package.json"))

    def test_cited_runs_replay_with_their_own_program_and_all_input_versions(self):
        current = self.root / "experiment/code"
        current.mkdir(parents=True)
        (current / "main.py").write_text("raise RuntimeError('working copy must not be archived as a run')")
        (current / "helper.py").write_text("OFFSET = 999")
        with zipfile.ZipFile(self.package()) as archive:
            manifest = self.read_manifest(archive)
            self.assertEqual({run["execution_id"] for run in manifest["runs"]},
                             {self.first["id"], self.second["id"]})
            target = self.root / "unpacked"
            archive.extractall(target)
            for run in manifest["runs"]:
                directory = target / run["directory"]
                saved = self.records["execution_admission"][run["admission_id"]]["command"]["program"]
                self.assertEqual((directory / "code/main.py").read_bytes(), self.artifacts.read(saved))
                for name in ("logs", "results", "plots"):
                    self.assertTrue((directory / name).is_dir())
                result = subprocess.run(run["argv"], cwd=directory, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = 11 if run["admission_id"] == "run-1" else 12
                self.assertEqual(json.loads((directory / "results/result.json").read_bytes()), {"value": expected})

    def test_every_claim_artifact_and_every_sealed_output_is_indexed_with_exact_bytes(self):
        with zipfile.ZipFile(self.package()) as archive:
            self.assertIn("README.txt", archive.namelist())
            index = json.loads(archive.read("data/INDEX.json"))
            for execution in (self.first, self.second):
                for output in execution["outputs"]:
                    ref = output["artifact"]
                    self.assertIn(ref["path"], index)
                    self.assertEqual(archive.read(index[ref["path"]]), self.artifacts.read(ref))
            self.assertEqual(json.loads(archive.read("claims.json")), self.claims)
            self.assertEqual(json.loads(archive.read("claim-evidence.json")), self.links)

    def test_records_keep_superseded_results_and_distinct_record_names(self):
        before = self.store.snapshot()
        with zipfile.ZipFile(self.package()) as archive:
            records = self.read_manifest(archive)["records"]
            for kind in ("cycle_plan", "execution_admission", "execution_binding", "execution", "cycle_assessment",
                         "checkpoint", "readiness_review", "manuscript_review", "manuscript_prediction",
                         "contribution_analysis", "round_decision", "round_review", "publication_bundle"):
                for key, value in self.records[kind].items():
                    self.assertEqual(json.loads(archive.read(records[kind][key])), value)
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
        self.assertEqual(self.store.snapshot(), before, "Export must leave historical records and results unchanged.")

    def test_packet_objects_are_followed_without_assuming_inline_review_inputs(self):
        nested = self.put(b"Recorded packet evidence.", "text/plain")
        packet = self.put(json.dumps({"evidence": nested}).encode(), "application/json")
        self.bundle.pop("review_inputs")
        self.bundle.pop("execution_observations")
        self.bundle["review_inputs_artifact"] = packet
        self.bundle["execution_observations_artifact"] = self.put(b"{}", "application/json")
        self.save()
        with zipfile.ZipFile(self.package()) as archive:
            index = json.loads(archive.read("data/INDEX.json"))
            for ref in (packet, nested):
                self.assertEqual(archive.read(index[ref["path"]]), self.artifacts.read(ref))
            saved = self.read_manifest(archive)["records"]["publication_bundle"][self.bundle["id"]]
            self.assertEqual(json.loads(archive.read(saved)), self.bundle)

    def test_json_file_descriptions_are_data_without_becoming_store_references(self):
        data = json.dumps({"value": 10, "file": {"path": "external.csv", "sha256": "0" * 64,
                                               "size": 123, "media_type": "text/csv"}}).encode()
        ref = self.put(data, "application/json")
        admission = self.records["execution_admission"]["run-1"]
        admission["command"]["inputs"][1] = ref
        admission["digest"] = digest({key: value for key, value in admission.items() if key != "digest"})
        binding = self.records["execution_binding"]["run-1"]
        binding["inputs"][1]["artifact"] = ref
        binding["admission_digest"] = admission["digest"]
        self.save()
        with zipfile.ZipFile(self.package()) as archive:
            run = next(item for item in self.read_manifest(archive)["runs"] if item["admission_id"] == "run-1")
            self.assertEqual(archive.read(run["directory"] + "/data/input.json"), data)
            index = json.loads(archive.read("data/INDEX.json"))
            self.assertIn(ref["path"], index)
            self.assertNotIn("external.csv", index)

    def test_a_frozen_input_can_be_an_uncited_runs_output_without_selecting_its_producer(self):
        ref = self.records["execution"]["execution-uncited"]["payload"]["outputs"][0]["artifact"]
        admission = self.records["execution_admission"]["run-2"]
        admission["command"]["inputs"][1] = ref
        binding = self.records["execution_binding"]["run-2"]
        binding["inputs"][1]["artifact"] = ref
        admission["digest"] = digest({key: value for key, value in admission.items() if key != "digest"})
        binding["admission_digest"] = admission["digest"]
        self.save()
        with zipfile.ZipFile(self.package()) as archive:
            manifest = self.read_manifest(archive)
            self.assertEqual(len(manifest["runs"]), 2)
            run = next(item for item in manifest["runs"] if item["admission_id"] == "run-2")
            self.assertEqual(archive.read(run["directory"] + "/data/input.json"), self.artifacts.read(ref))

    def test_imported_history_keeps_its_recorded_outputs_without_inventing_a_binding(self):
        imported = copy.deepcopy(self.first)
        imported.update(id="imported-history", origin={"kind": "imported", "attribution": "Earlier recorded work."})
        self.records["execution"][imported["id"]] = {"payload": imported, "digest": digest(imported)}
        self.bundle["claim_evidence"] = [{"claim_id": "claim", "evidence": [self.build_result_ref(imported)]}]
        self.save()
        with zipfile.ZipFile(self.package()) as archive:
            run, = self.read_manifest(archive)["runs"]
            self.assertEqual(run["execution_id"], imported["id"])
            self.assertIsNone(run["directory"])
            self.assertEqual(run["origin"], imported["origin"])
            index = json.loads(archive.read("data/INDEX.json"))
            self.assertIn(imported["outputs"][0]["artifact"]["path"], index)

    def test_bundle_id_or_digest_selects_the_historical_ledger(self):
        alternate = dict(self.bundle, id="paper-2", digest="b" * 64, claim_evidence=[])
        self.records["publication_bundle"]["paper-2"] = alternate
        self.records["publication_selection"]["bundle"] = {"id": "paper-2"}
        self.save()
        for selector in ("paper-1", self.bundle["digest"]):
            with self.subTest(selector=selector):
                name = "bundle-" + selector + ".zip"
                with zipfile.ZipFile(self.package(name, "--bundle", selector)) as archive:
                    self.assertEqual(self.read_manifest(archive)["bundle"]["id"], "paper-1")
                    self.assertEqual(json.loads(archive.read("claim-evidence.json")), self.links)

    def test_repeated_packages_are_identical_and_work_from_a_study_subdirectory(self):
        first = self.package().read_bytes()
        subdirectory = self.root / "research"
        subdirectory.mkdir(exist_ok=True)
        result = self.invoke("--output", "../second.zip", cwd=subdirectory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "second.zip").read_bytes(), first)

    def test_supplementary_code_and_disclosures_are_explicitly_included(self):
        (self.root / "tools").mkdir()
        (self.root / "tools/calibrate.py").write_bytes(b"# An unadmitted calibration helper.\n")
        (self.root / "disclosures").mkdir()
        (self.root / "disclosures/earlier.json").write_text('{"outputs_read": true}')
        with zipfile.ZipFile(self.package("sources.zip", "--include", "tools", "--include", "disclosures")) as archive:
            self.assertEqual(archive.read("tools/calibrate.py"), b"# An unadmitted calibration helper.\n")
            supplements = self.read_manifest(archive)["supplements"]
            self.assertEqual({item["path"] for item in supplements},
                             {"tools/calibrate.py", "disclosures/earlier.json"})

    def test_corrupt_objects_are_refused_without_leaving_an_archive(self):
        ref = self.first["outputs"][0]["artifact"]
        path = self.root / ref["path"]
        path.chmod(0o600)
        path.write_bytes(b"changed sealed result")
        result = self.invoke("--output", "refused.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifact_corrupt", result.stderr)
        self.assertFalse((self.root / "refused.zip").exists())
        self.assertFalse(list(self.root.glob(".package-*")))

    def test_missing_managed_binding_is_refused_without_guessing_current_paths(self):
        admission = copy.deepcopy(self.records["execution_admission"]["run-1"])
        admission["id"] = "unbound-run"
        self.records["execution_admission"]["unbound-run"] = admission
        self.first["origin"]["admission_id"] = "unbound-run"
        self.save()
        result = self.invoke("--output", "refused.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("package_binding_missing", result.stderr)
        self.assertFalse((self.root / "refused.zip").exists())

    def test_binding_inputs_must_match_the_admitted_input_versions(self):
        self.records["execution_binding"]["run-1"]["inputs"][0]["artifact"] = self.put(b"OFFSET = 999\n")
        self.save()
        result = self.invoke("--output", "refused.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution_identity_mismatch", result.stderr)
        self.assertFalse((self.root / "refused.zip").exists())

    def test_claim_output_mismatch_is_refused(self):
        self.bundle["claim_evidence"][0]["evidence"][0]["artifact"] = self.put(b"other output", "text/plain")
        self.save()
        result = self.invoke("--output", "refused.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("result_evidence_mismatch", result.stderr)
        self.assertFalse((self.root / "refused.zip").exists())

    def test_unsafe_bound_paths_are_refused(self):
        self.records["execution_binding"]["run-1"]["inputs"][0]["path"] = "../../outside.py"
        self.save()
        result = self.invoke("--output", "refused.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe_path", result.stderr)
        self.assertFalse((self.root / "refused.zip").exists())

    def test_existing_archives_and_symlink_destinations_are_never_replaced(self):
        existing = self.root / "existing.zip"
        existing.write_bytes(b"keep this archive")
        (self.root / "link.zip").symlink_to(existing)
        for name in ("existing.zip", "link.zip"):
            with self.subTest(name=name):
                result = self.invoke("--output", name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("package_destination_exists", result.stderr)
        self.assertEqual(existing.read_bytes(), b"keep this archive")

    def test_supplement_symlinks_and_reserved_member_collisions_are_refused(self):
        (self.root / "link.py").symlink_to(self.root / self.first["command"]["program"]["path"])
        (self.root / "claims.json").write_text("not the pinned claims")
        extra = "runs/run-run-1/code/extra.py"
        (self.root / extra).parent.mkdir(parents=True)
        (self.root / extra).write_text("# An unadmitted module must stay outside frozen run trees.\n")
        for name, code in (("link.py", "unsafe_path"), ("claims.json", "package_member_conflict"),
                           (extra, "package_member_conflict")):
            with self.subTest(name=name):
                result = self.invoke("--output", "refused.zip", "--include", name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(code, result.stderr)
                self.assertFalse((self.root / "refused.zip").exists())

    def test_missing_bundle_is_reported_without_creating_a_destination(self):
        result = self.invoke("--output", "refused.zip", "--bundle", "unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("publication_bundle_missing", result.stderr)
        self.assertFalse((self.root / "refused.zip").exists())

    def test_supplementary_files_cannot_replace_archive_directories(self):
        for name in ("data", "runs"):
            with self.subTest(name=name):
                (self.root / name).write_bytes(b"A file cannot also be an archive directory.")
                destination = "refused-" + name + ".zip"
                result = self.invoke("--output", destination, "--include", name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("package_member_conflict", result.stderr)
                self.assertFalse((self.root / destination).exists())

    def test_generated_zip_is_uploaded_unchanged_by_deposit_sources(self):
        path = self.package()
        loader = importlib.machinery.SourceFileLoader("draft_package_upload_test", str(PLUGIN / "bin/exactory-draft"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        draft = importlib.util.module_from_spec(spec)
        loader.exec_module(draft)
        from test_draft import _FakeZenodoApi
        fake = _FakeZenodoApi()
        (self.root / ".exactory/draft.json").write_text(json.dumps({"title": "An authored archive fixture."}))
        (self.root / "draft").mkdir()
        (self.root / "draft/paper.pdf").write_bytes(b"%PDF-1.4 fixture")
        (self.root / "draft/abstract.txt").write_text("An authored package upload fixture.")
        original = Path.cwd()
        try:
            os.chdir(self.root)
            args = draft._build_parser().parse_args(["deposit", "--pdf", "draft/paper.pdf", "--sources", str(path),
                                                    "--abstract-file", "draft/abstract.txt",
                                                    "--creator", "Fixture, A."])
            with mock.patch.object(draft, "_open_url", fake), mock.patch.dict(
                    os.environ, {"ZENODO_SANDBOX_TOKEN": "fixture-token"}), mock.patch(
                        "sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
                args.handler(args)
        finally:
            os.chdir(original)
        upload, = [request for request in fake.requests if request.full_url.endswith("/supplementary-sources.zip")]
        self.assertEqual(upload.data, path.read_bytes())
        self.assertEqual(hashlib.sha256(upload.data).hexdigest(), hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
