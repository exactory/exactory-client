"""Composite local pinning cannot publish probe bytes before durable origins."""

from unittest import mock
from concurrent.futures import ThreadPoolExecutor
import os
import time

from development_fixtures import DevelopmentCase
import test_research_probe_provenance as probe_fixtures
from research_harness.artifacts import ArtifactStore, describe_artifact
from research_harness.errors import ResearchError
from research_harness.integration import pin_artifact


RESULT = probe_fixtures.RESULT


class ResearchProbePinTests(DevelopmentCase):
    build_import = probe_fixtures.ResearchProbeProvenanceTests.build_import
    save_pending_launch = probe_fixtures.ResearchProbeProvenanceTests.save_pending_launch

    def setUp(self):
        super().setUp()
        self.prepared_study()

    def build_pin(self, *, data=RESULT, identifier="pending-pin"):
        launch = self.save_pending_launch()
        relative = launch["directory"] + "/stdout"
        (self.root / relative).write_bytes(data)
        return {"id": identifier, "path": relative, "media_type": "application/json"}

    def test_generic_publication_interleaving_keeps_origin_blocks_managed_label_and_finishes_pin(self):
        from research_harness.execution import record_imported_execution
        from research_harness.probe import launch_probe
        self.mutate(self.development().plan_cycle, self.plan())
        admission = self.admit()
        script = self.root / "live-output.py"
        script.write_text("from pathlib import Path\nimport time\nPath('results').mkdir(exist_ok=True)\n"
            "Path('results/live.json').write_bytes(" + repr(RESULT) + ")\n"
            "while not Path('release.txt').exists():\n    time.sleep(0.01)\n")
        owner = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(owner.shutdown, wait=True)
        pending = owner.submit(launch_probe, self.store, identifier="pending-probe", question="What do the live output bytes show?",
                               script=str(script), output_dir="results", timeout=5)
        descriptor = describe_artifact(RESULT, "application/json")
        original = ArtifactStore.put
        imported = []
        def interleave_import(artifacts, data, media_type):
            artifact = original(artifacts, data, media_type)
            if data == RESULT and not imported:
                payload = self.build_import(artifact, "publication-import")
                saved = self.mutate(record_imported_execution, payload)["result"]
                self.assertEqual(saved["probe_origins"], [{"probe_id": "pending-probe", "sha256": descriptor["sha256"], "roles": ["outputs"]}])
                imported.append(saved)
                managed = dict(payload, id="publication-managed", origin={"kind": "managed", "admission_id": admission["id"]},
                    command=admission["command"], usage={"units": 1, "reason": "A declarative managed label."})
                self.assert_error("probe_exploratory_origin", lambda: self.mutate(self.development().record_execution, managed))
            return artifact
        launch = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                launch = self.store.snapshot()["records"].get("probe_launch", {}).get("pending-probe")
                if launch and (self.root / launch["directory"] / "work/results/live.json").is_file():
                    break
                time.sleep(0.01)
            self.assertIsNotNone(launch)
            self.assertNotIn("pending-probe", self.store.snapshot()["records"].get("probe", {}))
            value = {"id": "live-pin", "path": launch["directory"] + "/work/results/live.json", "media_type": "application/json"}
            with mock.patch.object(ArtifactStore, "put", new=interleave_import):
                receipt = pin_artifact(self.store, value, expected_revision=self.store.revision, request_id="publication-pin")
        finally:
            if launch:
                (self.root / launch["directory"] / "work/release.txt").write_text("release")
            pending.result(timeout=7)
        self.assertEqual(receipt["result"], dict(value, artifact=descriptor, scientific_validation=False))
        self.assertEqual(len(imported), 1)
        self.assertEqual(self.store.committed_request("publication-pin")["operation"], "workspace.artifact")
        self.assertNotIn(admission["id"], self.store.snapshot()["records"].get("execution_outcome", {}))

    def test_stale_initial_caller_publishes_no_blob_and_creates_no_origin_intent(self):
        value = self.build_pin(data=RESULT + b" stale")
        before = self.store.snapshot()
        with mock.patch.object(ArtifactStore, "put") as publish:
            self.assert_error("stale_revision", lambda: pin_artifact(self.store, value,
                expected_revision=self.store.revision - 1, request_id="stale-pin"))
        publish.assert_not_called()
        self.assertEqual(self.store.snapshot(), before)

    def test_stage_crash_retry_reuses_original_cas_then_replays_without_source(self):
        value = self.build_pin()
        revision = self.store.revision
        with mock.patch.object(ArtifactStore, "put", side_effect=OSError("Crash before publication")):
            with self.assertRaisesRegex(OSError, "Crash before publication"):
                pin_artifact(self.store, value, expected_revision=revision, request_id="recover-pin")
        records = self.store.snapshot()["records"]
        self.assertIn("recover-pin", records["probe_artifact_pin"])
        self.assertTrue(records["probe_artifact"])
        self.assertIsNone(self.store.committed_request("recover-pin"))
        receipt = pin_artifact(self.store, value, expected_revision=revision, request_id="recover-pin")
        self.assertEqual(self.artifacts.read(receipt["result"]["artifact"]), RESULT)
        before = self.store.snapshot()
        (self.root / value["path"]).unlink()
        self.assertEqual(pin_artifact(self.store, value, expected_revision=revision, request_id="recover-pin"), receipt)
        self.assertEqual(self.store.snapshot(), before)

    def test_unpublished_stage_refuses_changed_source_and_changed_retry_payload(self):
        value = self.build_pin()
        revision = self.store.revision
        with mock.patch.object(ArtifactStore, "put", side_effect=OSError("Crash before publication")):
            with self.assertRaises(OSError):
                pin_artifact(self.store, value, expected_revision=revision, request_id="changed-pin")
        (self.root / value["path"]).write_bytes(RESULT + b" changed")
        self.assert_error("probe_artifact_pin_conflict", lambda: pin_artifact(self.store, value,
            expected_revision=revision, request_id="changed-pin"))
        self.assert_error("request_id_conflict", lambda: pin_artifact(self.store, dict(value, path="other.json"),
            expected_revision=revision, request_id="changed-pin"))
        self.assertIsNone(self.store.committed_request("changed-pin"))
        self.assertFalse((self.root / describe_artifact(RESULT, "application/json")["path"]).exists())
        self.assertFalse((self.root / describe_artifact(RESULT + b" changed", "application/json")["path"]).exists())

    def test_published_stage_recovers_held_bytes_after_source_changes(self):
        from research_harness import probe_provenance
        value = self.build_pin()
        revision = self.store.revision
        original = probe_provenance.prepared_mutation
        def interrupt_final(store, operation, *args, **kwargs):
            if operation == "workspace.artifact":
                raise OSError("Crash after publication")
            return original(store, operation, *args, **kwargs)
        with mock.patch.object(probe_provenance, "prepared_mutation", side_effect=interrupt_final):
            with self.assertRaisesRegex(OSError, "Crash after publication"):
                pin_artifact(self.store, value, expected_revision=revision, request_id="held-pin")
        (self.root / value["path"]).write_bytes(RESULT + b" later")
        receipt = pin_artifact(self.store, value, expected_revision=revision, request_id="held-pin")
        self.assertEqual(self.artifacts.read(receipt["result"]["artifact"]), RESULT)
        self.assertFalse((self.root / describe_artifact(RESULT + b" later", "application/json")["path"]).exists())

    def test_publication_cannot_change_the_frozen_request_by_mutating_caller_payload(self):
        value = self.build_pin()
        frozen = dict(value)
        original = ArtifactStore.put
        def mutate_caller(artifacts, data, media_type):
            value.update(id="different-id", path="different.json")
            return original(artifacts, data, media_type)
        with mock.patch.object(ArtifactStore, "put", new=mutate_caller):
            receipt = pin_artifact(self.store, value, expected_revision=self.store.revision, request_id="frozen-request")
        self.assertEqual(self.store.committed_request("frozen-request")["payload"], frozen)
        self.assertEqual(receipt["result"], dict(frozen, artifact=describe_artifact(RESULT, "application/json"), scientific_validation=False))
        self.assertNotIn("different-id", self.store.snapshot()["records"].get("local_artifact", {}))

    def test_ordinary_pin_retains_exact_record_single_revision_and_source_free_replay(self):
        self.save_pending_launch()
        value = {"id": "ordinary-pin", "path": "ordinary.json", "media_type": "application/json"}
        (self.root / value["path"]).write_bytes(RESULT)
        revision = self.store.revision
        receipt = pin_artifact(self.store, value, expected_revision=revision, request_id="ordinary-pin")
        self.assertEqual(receipt, {"revision": revision + 1, "request_id": "ordinary-pin",
            "result": dict(value, artifact=describe_artifact(RESULT, "application/json"), scientific_validation=False)})
        records = self.store.snapshot()["records"]
        self.assertNotIn("probe_artifact_pin", records)
        self.assertNotIn("probe_artifact", records)
        (self.root / value["path"]).unlink()
        self.assertEqual(pin_artifact(self.store, value, expected_revision=revision, request_id="ordinary-pin"), receipt)

    def test_unchanged_copied_input_freezes_bytes_without_exploratory_taint(self):
        descriptor = describe_artifact(RESULT, "application/json")
        for suffix, original in (("legacy", {"sha256": descriptor["sha256"]}),
                                 ("timed", {"sha256": descriptor["sha256"], "mtime_ns": 1000000000})):
            launch = self.save_pending_launch(identifier="copied-" + suffix, output_dir=".", initial={"inputs/original.json": original})
            relative = launch["directory"] + "/work/inputs/original.json"
            (self.root / relative).parent.mkdir(parents=True)
            (self.root / relative).write_bytes(RESULT)
            if "mtime_ns" in original:
                os.utime(self.root / relative, ns=(original["mtime_ns"], original["mtime_ns"]))
            value = {"id": "original-input-" + suffix, "path": relative, "media_type": "application/json"}
            request_id = "original-input-pin-" + suffix
            receipt = pin_artifact(self.store, value, expected_revision=self.store.revision, request_id=request_id)
            records = self.store.snapshot()["records"]
            self.assertIn(request_id, records["probe_artifact_pin"])
            self.assertNotIn("probe_artifact", records)
            self.assertEqual(receipt["result"], dict(value, artifact=descriptor, scientific_validation=False))

    def test_pending_same_byte_input_rewrite_has_origin_before_generic_publication(self):
        from research_harness.execution import record_imported_execution
        descriptor = describe_artifact(RESULT, "application/json")
        launch = self.save_pending_launch(output_dir=".", initial={"inputs/rewritten.json":
            {"sha256": descriptor["sha256"], "mtime_ns": 1000000000}})
        relative = launch["directory"] + "/work/inputs/rewritten.json"
        (self.root / relative).parent.mkdir(parents=True)
        (self.root / relative).write_bytes(RESULT)
        os.utime(self.root / relative, ns=(2000000000, 2000000000))
        self.mutate(self.development().plan_cycle, self.plan())
        original = ArtifactStore.put
        imported = []
        def import_rewritten_bytes(artifacts, data, media_type):
            artifact = original(artifacts, data, media_type)
            if data == RESULT and not imported:
                saved = self.mutate(record_imported_execution, self.build_import(artifact))["result"]
                self.assertEqual(saved["probe_origins"], [{"probe_id": launch["id"], "sha256": descriptor["sha256"], "roles": ["outputs"]}])
                imported.append(saved)
            return artifact
        value = {"id": "rewritten-input", "path": relative, "media_type": "application/json"}
        with mock.patch.object(ArtifactStore, "put", new=import_rewritten_bytes):
            receipt = pin_artifact(self.store, value, expected_revision=self.store.revision, request_id="rewritten-pin")
        self.assertEqual(self.artifacts.read(receipt["result"]["artifact"]), RESULT)
        self.assertEqual(len(imported), 1)
