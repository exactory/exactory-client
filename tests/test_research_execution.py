"""Real pinned launches, one-time claims, timeout and interrupted-owner recovery."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
from unittest import mock

from development_fixtures import DevelopmentCase, PROGRAM
from integration_fixtures import admit_lab


PLUGIN = Path(__file__).resolve().parents[1]


class ResearchExecutionTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()

    def require_launcher(self):
        self.assertTrue((PLUGIN / "research_harness/execution.py").is_file(), "The admitted launch boundary is missing")

    def test_real_execution_uses_frozen_bytes_and_cannot_replay_a_launch(self):
        self.require_launcher()
        admission = admit_lab(self, body="import json\nprint(json.dumps({'metric': 7}))\n")
        api = importlib.import_module("research_harness.execution")
        identity = {"expected_revision": self.store.revision, "request_id": "launch-1"}
        first = api.launch_execution(self.store, admission["id"], **identity)
        self.assertTrue(first["ok"])
        self.assertEqual(first["metric"]["metric"], 7)
        before = self.store.snapshot()
        second = api.launch_execution(self.store, admission["id"], **identity)
        self.assertEqual(second, first)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(len(before["records"]["execution"]), 1)
        self.assertFalse(api.execution_status(self.store, admission["id"])["scientific_validation"])

    def test_changed_script_after_binding_is_refused_before_process_claim(self):
        self.require_launcher()
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        (self.root / "experiment/code/program.py").write_text("raise RuntimeError('mutable bytes')\n")
        api = importlib.import_module("research_harness.execution")
        before = self.store.snapshot()
        self.assert_error("execution_input_changed", lambda: api.launch_execution(self.store, admission["id"],
            expected_revision=self.store.revision, request_id="changed"))
        self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn("execution_claim", before["records"])

    def test_stale_foundation_after_admission_prevents_claim_without_refunding_reservation(self):
        self.require_launcher()
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.refresh_synthesis("new")
        before = self.store.snapshot()
        api = importlib.import_module("research_harness.execution")
        self.assert_error("plan_dependencies_stale", lambda: api.launch_execution(self.store, admission["id"],
            expected_revision=self.store.revision, request_id="stale"))
        self.assertEqual(self.store.snapshot(), before)
        account = next(iter(before["records"]["strategy_account"].values()))
        self.assertEqual((account["executions"], account["charged_units"]), (1, 1))

    def test_public_result_cannot_replace_an_actual_managed_observation_with_json(self):
        admission = admit_lab(self, body="raise RuntimeError('must never launch')\n")
        before = self.store.snapshot()
        fabricated = {"id": "fabricated-run", "cycle_id": admission["cycle_id"], "command": admission["command"],
            "origin": {"kind": "managed", "admission_id": admission["id"]}, "status": "completed", "exit_code": 0,
            "usage": {"units": 1, "reason": "A declarative claim without an observed producer."},
            "outputs": [{"id": "result", "requirement_id": "measurements",
                "artifact": self.artifacts.put(b'{"metric": 7}', "application/json")}],
            "notes": "This JSON must not establish a managed result."}
        path = self.root / "fabricated.json"
        path.write_text(json.dumps(fabricated))
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "result", "--file", str(path),
            "--expected-revision", str(self.store.revision), "--request-id", "fabricated"], cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("managed_result_requires_reconciliation", result.stderr)
        self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn("execution_claim", before["records"])

    def test_public_result_preserves_attributed_external_history_without_prospective_credit(self):
        self.mutate(self.development().plan_cycle, self.plan())
        path = self.root / "external.py"
        path.write_bytes(PROGRAM)
        completed = subprocess.run([sys.executable, str(path)], capture_output=True, check=True)
        payload = {"id": "imported-external", "cycle_id": "cycle-1",
            "origin": {"kind": "imported", "original": {"run_id": "external-1", "executed_at": None,
                "provenance": self.artifacts.put(b"Actual test subprocess outside the managed launcher; no original timestamp was retained.", "text/plain")},
                "reason": "Retain the observed external output.", "deduction": "This history supplies no prospective managed execution credit."},
            "command": {"argv": [sys.executable, str(path)], "program": self.artifacts.put(PROGRAM, "text/x-python"),
                "inputs": [], "versions": {"python": sys.version.split()[0]}, "seed": None, "seed_reason": "Deterministic enumeration."},
            "status": "completed", "exit_code": 0, "usage": {"units": 1, "reason": "One externally observed fixture execution."},
            "outputs": [{"id": "result", "requirement_id": "measurements", "artifact": self.artifacts.put(completed.stdout, "application/json")}],
            "notes": "Imported actual output is retained as historical evidence."}
        path = self.root / "import.json"
        path.write_text(json.dumps(payload))
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-research"), "result", "--file", str(path),
            "--expected-revision", str(self.store.revision), "--request-id", "import-external"], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["execution"][payload["id"]]["payload"], payload)
        self.assertNotIn("execution_claim", records)
        self.assertFalse(self.development().readiness_report(self.store)["ready"])

    def test_actual_timeout_is_retained_without_result_validation(self):
        self.require_launcher()
        admission = admit_lab(self, body="import time\nprint('started', flush=True)\ntime.sleep(30)\n", timeout=0.15)
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="timeout")
        self.assertTrue(result["timed_out"])
        records = self.store.snapshot()["records"]
        outcome = records["execution"][records["execution_outcome"][admission["id"]]["execution_id"]]["payload"]
        self.assertEqual(outcome["status"], "timed_out")
        self.assertIn(b"started", self.artifacts.read(outcome["outputs"][0]["artifact"]))
        self.assertFalse(self.development().readiness_report(self.store)["ready"])

    def test_lost_observation_response_reconciles_existing_outcome_once(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        api = importlib.import_module("research_harness.execution")
        actual = api.prepared_mutation
        def crash_before_observation(store, operation, *args, **kwargs):
            if operation == "execution.observe":
                raise OSError("Simulated interruption after recording the actual outcome")
            return actual(store, operation, *args, **kwargs)
        with mock.patch.object(api, "prepared_mutation", side_effect=crash_before_observation):
            with self.assertRaises(OSError):
                api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="lost")
        before = self.store.snapshot()["records"]
        self.assertEqual(len(before["execution"]), 1)
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"]},
            expected_revision=self.store.revision, request_id="recover-observation")
        self.assertTrue(result["ok"])
        after = self.store.snapshot()["records"]
        self.assertEqual(before["execution"], after["execution"])
        self.assertEqual(before["strategy_account"], after["strategy_account"])
        # An acknowledged observation can outlive a failed projection write.
        # Its exact retry repairs views without new execution or accounting.
        summary = self.root / "experiment/results/program.json"
        log = self.root / "experiment/logs/program.log"
        expected_summary, expected_log = summary.read_bytes(), log.read_bytes()
        summary.unlink()
        log.unlink()
        completed = self.store.snapshot()
        replay = api.reconcile_execution(self.store, {"admission_id": admission["id"]},
            expected_revision=before["execution_admission"][admission["id"]]["admitted_revision"],
            request_id="recover-observation")
        self.assertEqual(replay, result)
        self.assertEqual(self.store.snapshot(), completed)
        self.assertEqual(json.loads(summary.read_bytes()), json.loads(expected_summary))
        self.assertEqual(log.read_bytes(), expected_log)

    def test_dead_claim_can_record_interruption_but_never_launch_again(self):
        admission = admit_lab(self, body="raise RuntimeError('must never launch')\n")
        api = importlib.import_module("research_harness.execution")
        api._claim(self.store, admission["id"], self.store.revision, "dead-client")
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "interrupted",
            "reason": "The client exited before releasing the worker, and the worker lock is unowned."},
            expected_revision=self.store.revision, request_id="recover-dead")
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.store.snapshot()["records"]["execution"]), 1)
        self.assert_error("execution_recovery_required", lambda: api.launch_execution(self.store, admission["id"],
            expected_revision=self.store.revision, request_id="second-launch"))
