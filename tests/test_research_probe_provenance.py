"""Probe bytes remain exploratory through every result import and later reference."""

import sys
import unittest
from unittest import mock

from development_fixtures import DevelopmentCase
from integration_fixtures import admit_lab
from research_harness.evidence import digest
from research_harness.errors import ResearchError


RESULT = b'{"result":{"values":[0,1,4,9],"bound":9},"validation":{"passed":true}}'


class ResearchProbeProvenanceTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()

    def save_probe(self, artifact, *, role="outputs", identifier="probe-1"):
        revision = self.store.revision + 1
        program = self.artifacts.put(b"print('exploratory fixture')\n", "text/x-python")
        probe = {"id": identifier, "command": "python probe.py", "outputs": [],
            "stdout": self.artifacts.put(b"fixture probe stdout\n", "text/plain"),
            "stderr": self.artifacts.put(b"fixture probe stderr\n", "text/plain"), "exit_status": 0,
            "elapsed_seconds": 0.01, "observation": "Exploratory fixture observation.", "revision": revision}
        if role == "outputs":
            probe[role] = [artifact]
        else:
            probe[role] = artifact
        intent = {"id": identifier, "question": "What does the exploratory fixture show?", "interpreter": sys.executable,
            "program": program, "inputs": [], "timeout_seconds": 1, "revision": revision}
        def prepare(transaction):
            transaction.put("probe_intent", identifier, intent)
            transaction.put("probe", identifier, probe)
            return probe
        self.store.mutate("fixture.probe", probe, prepare, expected_revision=self.store.revision,
                          request_id="fixture-" + identifier)
        return probe

    def build_import(self, artifact, identifier="imported-probe"):
        return {"id": identifier, "cycle_id": "cycle-1",
            "origin": {"kind": "imported", "original": {"run_id": "external-run", "executed_at": None,
                "provenance": self.artifacts.put(b"External import attribution; chronology unknown.\n", "text/plain")},
                "reason": "Retain earlier observations for independent assessment.",
                "deduction": "The import is history and grants no prospective execution credit."},
            "command": None, "status": "completed", "exit_code": 0,
            "usage": {"units": None, "reason": "Original usage is unknown."},
            "outputs": [{"id": "result", "requirement_id": "measurements", "artifact": artifact},
                        {"id": "validation", "requirement_id": "checks", "artifact": artifact}],
            "notes": "Retain the original bytes and chronology."}

    def save_pending_launch(self, identifier="pending-probe", output_dir="results", initial=None):
        from research_harness.probe import record_probe_intent, record_probe_launch
        from test_research_probe_lifecycle import build_probe_intent
        self.mutate(record_probe_intent, build_probe_intent(self.artifacts, identifier))
        launch = {"id": identifier, "directory": "research/probes/" + identifier + "/run-fixture",
            "output_dir": output_dir, "initial": initial or {}}
        (self.root / launch["directory"] / "work" / output_dir).mkdir(parents=True)
        self.mutate(record_probe_launch, launch)
        return launch

    def test_real_worker_artifacts_cannot_be_laundered_before_outcome_commit(self):
        from research_harness import execution, probe
        plan = self.plan()
        self.mutate(self.development().plan_cycle, plan)
        admission = self.admit()
        script = self.root / "gap-probe.py"
        script.write_text("from pathlib import Path\nvalue = " + repr(RESULT.decode())
                          + "\nPath('result.json').write_text(value + '\\n')\nprint(value)\n")
        original = probe.record_probe
        imported = []
        def import_before_outcome(store, payload, **identity):
            if not imported:
                self.assertNotIn(payload["id"], store.snapshot()["records"].get("probe", {}))
                value = self.build_import(dict(payload["stdout"], media_type="application/json"), "gap-import")
                saved = self.mutate(execution.record_imported_execution, value)["result"]
                self.assertEqual(saved["probe_origins"], [{"probe_id": payload["id"],
                    "sha256": value["outputs"][0]["artifact"]["sha256"], "roles": ["outputs", "stdout"]}])
                managed = dict(value, id="gap-managed", origin={"kind": "managed", "admission_id": admission["id"]},
                    command=admission["command"], usage={"units": 1, "reason": "A declarative managed label."})
                self.assert_error("probe_exploratory_origin", lambda: self.mutate(self.development().record_execution, managed))
                assessed = self.mutate(self.development().assess_cycle, self.assessment(plan, value))["result"]
                self.assertIn("prospective_execution_missing", {item["code"] for item in assessed["obligations"]})
                imported.append(saved)
            return original(store, payload, **identity)
        with mock.patch.object(probe, "record_probe", side_effect=import_before_outcome):
            probe.launch_probe(self.store, identifier="gap-probe", question="What do the exploratory bytes show?",
                               script=str(script), timeout=2)
        records = self.store.snapshot()["records"]
        self.assertEqual(len(imported), 1)
        self.assertNotIn(admission["id"], records.get("execution_outcome", {}))
        self.assertIn("gap-probe", records["probe"])

    def test_same_bytes_input_rewrite_with_changed_timestamp_is_retained_as_output(self):
        from research_harness.probe import launch_probe
        from research_harness.probe_provenance import find_probe_artifact_origins
        path = self.root / "rewrite-input.json"
        path.write_bytes(RESULT)
        script = self.root / "rewrite-input.py"
        script.write_text("from pathlib import Path\nimport os, sys\npath = Path(sys.argv[1])\n"
            "before = path.stat()\npath.write_bytes(path.read_bytes())\n"
            "os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2000000000))\n")
        saved = launch_probe(self.store, identifier="same-bytes-rewrite", question="Was the copied input rewritten?",
            script=str(script), inputs=[str(path)], timeout=2)["result"]
        self.assertEqual([self.artifacts.read(artifact) for artifact in saved["outputs"]], [RESULT])
        self.assertEqual(find_probe_artifact_origins(self.store.snapshot()["records"], saved["outputs"]),
            [{"probe_id": saved["id"], "sha256": saved["outputs"][0]["sha256"], "roles": ["outputs"]}])

    def test_pending_launch_local_pins_retain_output_and_raw_stream_origins(self):
        from research_harness.execution import record_imported_execution
        from research_harness.integration import pin_artifact
        launch = self.save_pending_launch()
        self.mutate(self.development().plan_cycle, self.plan())
        for role, suffix in (("outputs", "/work/results/result.json"), ("stdout", "/stdout"), ("stderr", "/stderr")):
            relative = launch["directory"] + suffix
            (self.root / relative).write_bytes(RESULT + role.encode())
            pinned = self.mutate(pin_artifact, {"id": "pending-" + role, "path": relative,
                "media_type": "application/octet-stream"})["result"]
            saved = self.mutate(record_imported_execution, self.build_import(pinned["artifact"], "pending-import-" + role))["result"]
            self.assertEqual(saved["probe_origins"], [{"probe_id": launch["id"],
                "sha256": pinned["artifact"]["sha256"], "roles": [role]}])
        self.assertNotIn("probe", self.store.snapshot()["records"])

    def test_prior_imports_stay_exact_after_matching_pending_probe_local_pin(self):
        from research_harness.integration import pin_artifact
        self.mutate(self.development().plan_cycle, self.plan())
        payload = self.build_import(self.artifacts.put(RESULT, "application/json"), "prior-import")
        saved = self.mutate(self.development().record_execution, payload)["result"]
        launch = self.save_pending_launch()
        relative = launch["directory"] + "/stdout"
        (self.root / relative).write_bytes(RESULT)
        self.mutate(pin_artifact, {"id": "later-probe-pin", "path": relative, "media_type": "application/json"})
        self.assertEqual(self.store.snapshot()["records"]["execution"][payload["id"]], saved)
        evidence = self.development()._Evidence(self.development()._Context(self.store.snapshot()["records"], self.artifacts))
        self.assertNotIn("probe_origins", evidence.one(self.result_evidence(payload)))

    def test_pinning_registers_origin_before_publishing_bytes_and_retries_only_stale(self):
        from research_harness import probe_provenance
        from research_harness.artifacts import ArtifactStore, describe_artifact
        self.save_pending_launch()
        data = b"Unpublished exploratory output bytes."
        descriptor = describe_artifact(data, "text/plain")
        original = ArtifactStore.put
        def inspect_publication(artifacts, value, media_type):
            self.assertEqual(probe_provenance.find_probe_artifact_origins(self.store.snapshot()["records"], [descriptor]),
                [{"probe_id": "pending-probe", "sha256": descriptor["sha256"], "roles": ["outputs"]}])
            self.assertFalse((self.root / descriptor["path"]).exists())
            return original(artifacts, value, media_type)
        prepare = probe_provenance.prepared_mutation
        attempts = []
        def race_once(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise ResearchError("stale_revision", "Fixture concurrent mutation")
            return prepare(*args, **kwargs)
        with mock.patch.object(probe_provenance, "prepared_mutation", side_effect=race_once):
            with mock.patch.object(ArtifactStore, "put", new=inspect_publication):
                result = probe_provenance.pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "outputs")
        self.assertEqual(result, descriptor)
        self.assertEqual(len(attempts), 2)
        with mock.patch.object(probe_provenance, "prepared_mutation", side_effect=ResearchError("storage_busy", "Fixture lock")) as failed:
            self.assert_error("storage_busy", lambda: probe_provenance.pin_probe_artifact(
                self.root, "pending-probe", b"Never published", "text/plain", "stdout"))
        self.assertEqual(failed.call_count, 1)

    def test_raw_stream_identity_is_registered_before_oversized_prefix_publication(self):
        from research_harness import probe_provenance, probe_worker
        from research_harness.artifacts import ArtifactStore, describe_artifact
        launch = self.save_pending_launch()
        data = b"A full raw stream that exceeds the fixture bound."
        (self.root / launch["directory"] / "stdout").write_bytes(data)
        full = describe_artifact(data, "text/plain")
        original = ArtifactStore.put
        def inspect_prefix(artifacts, value, media_type):
            self.assertEqual(probe_provenance.find_probe_artifact_origins(self.store.snapshot()["records"], [full]),
                [{"probe_id": launch["id"], "sha256": full["sha256"], "roles": ["stdout"]}])
            return original(artifacts, value, media_type)
        with mock.patch.object(probe_worker, "STREAM_LIMIT", 8), mock.patch.object(ArtifactStore, "put", new=inspect_prefix):
            outcome = probe_worker.capture_probe_outcome(self.root, dict(launch, command="python fixture.py"),
                {"exit_status": 0, "elapsed_seconds": 0.1})
        self.assertEqual(self.artifacts.read(outcome["stdout"]), data[:8])
        self.assertEqual(outcome["exit_status"], "output_bound")

    def test_origin_registration_validates_intent_identity_role_and_replays_per_role(self):
        from research_harness.probe_provenance import find_probe_artifact_origins, pin_probe_artifact, record_probe_artifact_origin
        from research_harness.artifacts import describe_artifact
        data = b"An output whose origin must precede publication."
        descriptor = describe_artifact(data, "text/plain")
        self.assert_error("probe_intent_missing", lambda: pin_probe_artifact(self.root, "missing", data, "text/plain", "stdout"))
        self.assertFalse((self.root / descriptor["path"]).exists())
        self.save_pending_launch()
        for probe_id, sha256, role in (("../unsafe", descriptor["sha256"], "stdout"),
                                      ("pending-probe", "A" * 64, "stdout"),
                                      ("pending-probe", descriptor["sha256"], "inputs")):
            self.assert_error("invalid_input", lambda: record_probe_artifact_origin(self.root, probe_id, sha256, role))
        pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "stdout")
        before = self.store.snapshot()
        pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "stdout")
        self.assertEqual(self.store.snapshot(), before)
        pin_probe_artifact(self.root, "pending-probe", data, "text/plain", "stderr")
        self.assertEqual(find_probe_artifact_origins(self.store.snapshot()["records"], [descriptor]),
            [{"probe_id": "pending-probe", "sha256": descriptor["sha256"], "roles": ["stderr", "stdout"]}])

    def test_both_import_paths_pin_probe_origin_by_bytes_despite_media_relabeling(self):
        from research_harness import execution
        artifact = self.artifacts.put(RESULT, "application/json")
        self.save_probe(self.artifacts.put(RESULT, "text/plain"))
        self.mutate(self.development().plan_cycle, self.plan())
        for index, operation in enumerate((self.development().record_execution, execution.record_imported_execution)):
            payload = self.build_import(artifact, "import-" + str(index))
            record = self.mutate(operation, payload)["result"]
            self.assertEqual(record["payload"], payload)
            self.assertEqual(record["probe_origins"], [{"probe_id": "probe-1", "sha256": artifact["sha256"], "roles": ["outputs"]}])
            self.assertIsNone(record["admitted_revision"])
        self.assertNotIn("execution_outcome", self.store.snapshot()["records"])

    def test_imports_of_each_probe_stream_as_outputs_keep_the_stream_origin(self):
        from research_harness.execution import record_imported_execution
        self.mutate(self.development().plan_cycle, self.plan())
        for index, role in enumerate(("stdout", "stderr")):
            artifact = self.artifacts.put(RESULT + str(index).encode(), "application/octet-stream")
            self.save_probe(artifact, role=role, identifier="probe-" + role)
            record = self.mutate(record_imported_execution, self.build_import(artifact, "import-" + role))["result"]
            self.assertEqual(record["probe_origins"], [{"probe_id": "probe-" + role,
                "sha256": artifact["sha256"], "roles": [role]}])

    def test_import_of_full_raw_stream_uses_catalog_when_outcome_retains_only_a_prefix(self):
        from research_harness.execution import record_imported_execution
        artifact = self.artifacts.put(RESULT + b"\n", "application/json")
        self.save_probe(self.artifacts.put(RESULT[:12], "text/plain"), role="stdout")
        origin = {"probe_id": "probe-1", "sha256": artifact["sha256"], "roles": ["stdout"]}
        def prepare(transaction):
            transaction.put("probe_artifact", "probe-1:" + artifact["sha256"], origin)
            return origin
        self.store.mutate("fixture.probe_artifact", origin, prepare, expected_revision=self.store.revision,
                          request_id="fixture-full-stream-origin")
        self.mutate(self.development().plan_cycle, self.plan())
        record = self.mutate(record_imported_execution, self.build_import(artifact))["result"]
        self.assertEqual(record["probe_origins"], [origin])

    def test_managed_label_cannot_launder_probe_bytes_through_low_level_recording(self):
        self.mutate(self.development().plan_cycle, self.plan())
        admission = self.admit()
        payload = self.execution(admission)
        self.save_probe(payload["outputs"][0]["artifact"])
        before = self.store.snapshot()
        self.assert_error("probe_exploratory_origin", lambda: self.mutate(self.development().record_execution, payload))
        self.assertEqual(self.store.snapshot(), before)

    def test_probe_origin_survives_assessment_and_result_references_without_prospective_credit(self):
        artifact = self.artifacts.put(RESULT, "application/json")
        self.save_probe(artifact)
        plan = self.plan()
        self.mutate(self.development().plan_cycle, plan)
        payload = self.build_import(artifact)
        record = self.mutate(self.development().record_execution, payload)["result"]
        assessed = self.mutate(self.development().assess_cycle, self.assessment(plan, payload))["result"]
        self.assertTrue(assessed["validated_result"])
        self.assertIn("prospective_execution_missing", {item["code"] for item in assessed["obligations"]})
        self.save_checkpoint()
        candidate = self.candidate()
        evidence = self.development()._Evidence(self.development()._Context(self.store.snapshot()["records"], self.artifacts))
        referenced = evidence.one(self.result_evidence(payload))
        self.assertEqual(referenced["probe_origins"], record["probe_origins"])
        self.assertIn("prospective_execution_missing", self.readiness_codes())
        self.assertFalse(self.development().readiness_report(self.store)["ready"])
        self.assertIsNotNone(candidate)

    def test_ordinary_import_preserves_exact_record_and_report_with_unrelated_probe(self):
        artifact = self.artifacts.put(RESULT, "application/json")
        self.save_probe(self.artifacts.put(b"unrelated exploratory bytes", "text/plain"))
        plan = self.plan()
        pinned = self.mutate(self.development().plan_cycle, plan)["result"]
        payload = self.build_import(artifact, "ordinary-import")
        revision, request_id = self.store.revision, "ordinary-import-request"
        record = self.development().record_execution(self.store, payload, expected_revision=revision, request_id=request_id)["result"]
        expected = {"id": payload["id"], "payload": payload, "plan_digest": pinned["digest"],
            "recorded_revision": revision + 1, "request_id": request_id, "admitted_revision": None}
        expected["digest"] = digest(expected)
        self.assertEqual(record, expected)
        self.assertTrue(self.mutate(self.development().assess_cycle, self.assessment(plan, payload))["result"]["validated_result"])
        self.save_checkpoint()
        before = self.development().readiness_state(self.store.snapshot()["records"], self.artifacts)
        self.save_probe(self.artifacts.put(b"second unrelated exploratory bytes", "text/plain"), identifier="probe-2")
        self.assertEqual(self.development().readiness_state(self.store.snapshot()["records"], self.artifacts), before)
        evidence = self.development()._Evidence(self.development()._Context(self.store.snapshot()["records"], self.artifacts))
        self.assertNotIn("probe_origins", evidence.one(self.result_evidence(payload)))

    def test_real_observed_rerun_can_validate_the_same_bytes_as_an_earlier_probe(self):
        from research_harness.execution import launch_execution
        from research_harness.execution_evidence import validate_observed_execution
        artifact = self.artifacts.put(b'{"metric": 7}\n', "text/plain")
        self.save_probe(artifact)
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        result = launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="observe-rerun")
        self.assertTrue(result["ok"])
        records = self.store.snapshot()["records"]
        identifier = records["execution_outcome"][admission["id"]]["execution_id"]
        record = records["execution"][identifier]
        self.assertEqual(record["payload"]["outputs"][0]["artifact"]["sha256"], artifact["sha256"])
        self.assertNotIn("probe_origins", record)
        self.assertIsNotNone(validate_observed_execution(records, self.artifacts, identifier)["observation"])

    def test_observed_identical_rerun_recovers_once_after_losing_observation_publication(self):
        from research_harness import execution
        self.save_probe(self.artifacts.put(b'{"metric": 7}\n', "text/plain"))
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        prepare = execution.prepared_mutation
        def interrupt_observation(store, operation, *args, **kwargs):
            if operation == "execution.observe":
                raise OSError("Fixture interruption before observation publication")
            return prepare(store, operation, *args, **kwargs)
        with mock.patch.object(execution, "prepared_mutation", side_effect=interrupt_observation):
            with self.assertRaisesRegex(OSError, "Fixture interruption"):
                execution.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                           request_id="interrupted-identical-rerun")
        before = self.store.snapshot()["records"]
        self.assertEqual(len(before["execution"]), 1)
        self.assertNotIn("execution_observation", before)
        result = execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
            expected_revision=self.store.revision, request_id="recover-identical-rerun")
        self.assertTrue(result["ok"])
        after = self.store.snapshot()["records"]
        self.assertEqual(after["execution"], before["execution"])
        self.assertEqual(after["strategy_account"], before["strategy_account"])
        self.assertIn(admission["id"], after["execution_observation"])

    def test_interrupted_recovery_retains_matching_probe_bytes_without_prospective_credit(self):
        from research_harness import execution
        artifact = self.artifacts.put(RESULT, "application/json")
        self.save_probe(artifact)
        admission = admit_lab(self, body="raise RuntimeError('must never launch')\n",
            outputs=[{"id": "result", "requirement_id": "measurements", "path": "stdout", "media_type": "application/json"},
                     {"id": "validation", "requirement_id": "checks", "path": "stderr", "media_type": "application/json"}])
        execution._claim(self.store, admission["id"], self.store.revision, "interrupted-probe-reuse")
        directory = self.root / execution._directory(admission["id"])
        (directory / "stdout").write_bytes(RESULT)
        (directory / "stderr").write_bytes(RESULT)
        result = execution.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "interrupted",
            "reason": "The producer has no live owner and retained only partial bytes."},
            expected_revision=self.store.revision, request_id="recover-partial-probe-reuse")
        self.assertFalse(result["ok"])
        records = self.store.snapshot()["records"]
        identifier = records["execution_outcome"][admission["id"]]["execution_id"]
        record = records["execution"][identifier]
        self.assertEqual(record["payload"]["status"], "interrupted")
        self.assertEqual(record["probe_origins"], [{"probe_id": "probe-1", "sha256": artifact["sha256"], "roles": ["outputs"]}])
        reference = {"kind": "result", "execution_id": identifier, "output_id": "result", "artifact": artifact,
            "locator": {"kind": "json", "pointer": "/result", "value": {"values": [0, 1, 4, 9], "bound": 9}}}
        evidence = self.development()._Evidence(self.development()._Context(records, self.artifacts)).one(reference)
        self.assertEqual(evidence["origin"]["kind"], "exploratory")
        self.assertEqual(evidence["probe_origins"], record["probe_origins"])
        self.assertEqual(records["strategy_account"][admission["strategy_key"]]["charged_units"], admission["reserved_units"])
        assessment = self.assessment(records["cycle_plan"][admission["cycle_id"]]["payload"], record["payload"])
        assessment["validity_checks"][0]["status"] = "unresolved"
        assessed = self.mutate(self.development().assess_cycle, assessment)["result"]
        self.assertFalse(assessed["validated_result"])
        self.assertTrue({"prospective_execution_missing", "result_execution_incomplete"}
                        <= {item["code"] for item in assessed["obligations"]})


class ResearchProbeOriginLookupTests(unittest.TestCase):
    def test_pending_local_paths_exclude_unchanged_copied_inputs_and_sibling_directories(self):
        from research_harness.probe_provenance import find_probe_artifact_origins
        first = {"sha256": "a" * 64}
        changed = {"sha256": "b" * 64}
        root = "research/probes/pending/run-fixture"
        launch = {"id": "pending", "directory": root, "output_dir": ".",
            "initial": {"program/program.py": {"sha256": first["sha256"]}, "inputs/0/input.txt": {"sha256": first["sha256"]}}}
        local = {"program": {"path": root + "/work/program/program.py", "artifact": first},
            "input": {"path": root + "/work/inputs/0/input.txt", "artifact": first},
            "sibling": {"path": root + "-other/work/result.txt", "artifact": first},
            "changed": {"path": root + "/work/inputs/0/input.txt", "artifact": changed}}
        records = {"probe_launch": {"pending": launch}, "local_artifact": local}
        self.assertEqual(find_probe_artifact_origins(records, [first, changed]), [
            {"probe_id": "pending", "sha256": changed["sha256"], "roles": ["outputs"]}])
        scoped = dict(launch, output_dir="results")
        self.assertEqual(find_probe_artifact_origins(dict(records, probe_launch={"pending": scoped}), [changed]), [])

    def test_lookup_collects_all_probe_roles_by_sha256_and_ignores_inputs(self):
        from research_harness.probe_provenance import find_probe_artifact_origins
        first = {"sha256": "a" * 64, "media_type": "text/plain"}
        relabeled = dict(first, media_type="application/json")
        second = {"sha256": "b" * 64, "media_type": "text/plain"}
        records = {"probe_intent": {"probe-2": {"program": second, "inputs": [second]}},
            "probe": {"probe-2": {"outputs": [first, first], "stdout": first, "stderr": second},
                      "probe-1": {"outputs": [], "stdout": first, "stderr": second}}}
        self.assertEqual(find_probe_artifact_origins(records, [relabeled, first]), [
            {"probe_id": "probe-1", "sha256": first["sha256"], "roles": ["stdout"]},
            {"probe_id": "probe-2", "sha256": first["sha256"], "roles": ["outputs", "stdout"]}])
        self.assertEqual(find_probe_artifact_origins({"probe_intent": records["probe_intent"]}, [second]), [])
        self.assertEqual(find_probe_artifact_origins({}, [first]), [])
