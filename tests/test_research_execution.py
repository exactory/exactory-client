"""Real pinned launches, one-time claims, timeout and interrupted-owner recovery."""

import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from development_fixtures import DevelopmentCase, PROGRAM
from integration_fixtures import admit_lab, make_venv


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

    def wall_time_overrun(self, body, timeout, status):
        admission = admit_lab(self, body=body, timeout=timeout, usage_unit="wall_seconds",
                              reserved_units=0.01, max_units=0.1)
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="wall-time")
        records = self.store.snapshot()["records"]
        outcome = records["execution"][records["execution_outcome"][admission["id"]]["execution_id"]]["payload"]
        account = records["strategy_account"][admission["strategy_key"]]
        self.assertEqual(outcome["status"], status)
        self.assertGreater(result["duration_s"], 0.1)
        self.assertEqual(outcome["usage"]["units"], result["duration_s"])
        self.assertAlmostEqual(account["charged_units"], result["duration_s"])
        payload = {key: admission[key] for key in ("cycle_id", "plan_digest", "command", "reserved_units")}
        payload["id"] = "after-overrun"
        before = self.store.snapshot()
        self.assert_error("strategy_budget_exhausted", lambda: self.mutate(self.development().admit_execution, payload))
        self.assertEqual(self.store.snapshot(), before)

    def test_failed_wall_time_is_measured_and_exhausts_the_account(self):
        self.wall_time_overrun("import time\ntime.sleep(0.2)\nraise SystemExit(7)\n", 5, "failed")

    def test_timed_out_wall_time_is_measured_and_exhausts_the_account(self):
        self.wall_time_overrun("import time\ntime.sleep(30)\n", 0.15, "timed_out")

    def legacy_wall_failure(self, *, reserve_following=False, interrupt_before_observation=False):
        from legacy_execution_fixtures import legacy_execution_writers
        admission = admit_lab(self, body="import time\ntime.sleep(0.2)\nraise SystemExit(7)\n",
                              usage_unit="wall_seconds", reserved_units=0.01, max_units=0.1)
        api = importlib.import_module("research_harness.execution")
        original_record = api.record_execution
        original_mutation = api.prepared_mutation
        legacy = legacy_execution_writers()
        old = {}

        def retain_pre_fix_outcome(store, payload, **identity):
            # Reconstruct the former producer's null-usage payload through the
            # real historical API. The process and pinned terminal are actual.
            payload["usage"]["units"] = None
            old.update(payload=payload, identity=identity,
                       receipt=original_record(store, payload, **identity))
            if reserve_following:
                following = {key: admission[key] for key in ("cycle_id", "plan_digest", "command", "reserved_units")}
                following["id"] = "historically-admitted"
                self.mutate(legacy.admit_execution, following)
                self.mutate(legacy.bind_execution, {"admission_id": following["id"], "script": "code/program.py",
                    "backend": "local", "timeout_seconds": 5, "inputs": [], "usage_unit": "wall_seconds",
                    "outputs": [{"id": "result", "requirement_id": "measurements", "path": "stdout", "media_type": "text/plain"}]})
            return old["receipt"]

        def retain_missing_observation(store, operation, *args, **kwargs):
            if operation == "execution.observe" and interrupt_before_observation:
                raise OSError("Historical interruption after outcome and before observation")
            return original_mutation(store, operation, *args, **kwargs)

        with mock.patch.object(api, "record_execution", side_effect=retain_pre_fix_outcome), \
                mock.patch.object(api, "prepared_mutation", side_effect=retain_missing_observation):
            if interrupt_before_observation:
                with self.assertRaisesRegex(OSError, "Historical interruption after outcome and before observation"):
                    api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                         request_id="historical-failure")
            else:
                api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                     request_id="historical-failure")
        terminal = json.loads((self.root / api._directory(admission["id"]) / "terminal.json").read_bytes())
        self.assertGreater(terminal["duration_s"], 0.1)
        before = self.store.snapshot()
        if interrupt_before_observation:
            self.assertNotIn(admission["id"], before["records"].get("execution_observation", {}))
        self.assertEqual(original_record(self.store, old["payload"], **old["identity"]), old["receipt"])
        self.assertEqual(self.store.snapshot(), before)
        return admission

    def test_retained_null_usage_blocks_new_admission_without_rewriting_history(self):
        admission = self.legacy_wall_failure()
        before = self.store.snapshot()
        self.assertEqual(before["records"]["strategy_account"][admission["strategy_key"]]["charged_units"], 0.01)
        following = {key: admission[key] for key in ("cycle_id", "plan_digest", "command", "reserved_units")}
        following["id"] = "new-after-known-usage"
        self.assert_error("execution_usage_reconciliation_required", lambda: self.mutate(self.development().admit_execution, following))
        self.assertEqual(self.store.snapshot(), before)

    def test_retained_null_usage_blocks_an_unstarted_historical_admission(self):
        admission = self.legacy_wall_failure(reserve_following=True)
        api = importlib.import_module("research_harness.execution")
        before = self.store.snapshot()
        self.assertEqual(before["records"]["strategy_account"][admission["strategy_key"]]["charged_units"], 0.02)
        self.assert_error("execution_usage_reconciliation_required", lambda: api.launch_execution(self.store,
            "historically-admitted", expected_revision=self.store.revision, request_id="new-launch-after-known-usage"))
        self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn("historically-admitted", before["records"]["execution_claim"])

    def test_unobserved_legacy_outcome_blocks_new_admission(self):
        admission = self.legacy_wall_failure(interrupt_before_observation=True)
        before = self.store.snapshot()
        self.assertEqual(before["records"]["strategy_account"][admission["strategy_key"]]["charged_units"], 0.01)
        following = {key: admission[key] for key in ("cycle_id", "plan_digest", "command", "reserved_units")}
        following["id"] = "new-after-unobserved-outcome"
        self.assert_error("execution_usage_reconciliation_required", lambda: self.mutate(self.development().admit_execution, following))
        self.assertEqual(self.store.snapshot(), before)

    def test_unobserved_legacy_outcome_blocks_an_unstarted_historical_admission(self):
        from research_harness.errors import ResearchError
        admission = self.legacy_wall_failure(reserve_following=True, interrupt_before_observation=True)
        api = importlib.import_module("research_harness.execution")
        before = self.store.snapshot()
        self.assertEqual(before["records"]["strategy_account"][admission["strategy_key"]]["charged_units"], 0.02)
        try:
            summary = api.launch_execution(self.store, "historically-admitted",
                expected_revision=self.store.revision, request_id="new-launch-after-unobserved-outcome")
        except ResearchError as error:
            self.assertEqual(error.code, "execution_usage_reconciliation_required")
        else:
            self.fail("An unstarted historical admission actually executed: " + json.dumps(summary, sort_keys=True))
        self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn("historically-admitted", before["records"]["execution_claim"])
        self.assertFalse((self.root / api._directory("historically-admitted") / "terminal.json").exists())

    def test_accounted_wall_outcome_recovers_observation_before_new_permission(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n", usage_unit="wall_seconds",
                              reserved_units=0.01, max_units=1)
        api = importlib.import_module("research_harness.execution")
        original_mutation = api.prepared_mutation

        def stop_before_observation(store, operation, *args, **kwargs):
            if operation == "execution.observe":
                raise OSError("Current interruption after accounted outcome")
            return original_mutation(store, operation, *args, **kwargs)

        with mock.patch.object(api, "prepared_mutation", side_effect=stop_before_observation):
            with self.assertRaisesRegex(OSError, "Current interruption after accounted outcome"):
                api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                     request_id="accounted-before-observation")
        before = self.store.snapshot()["records"]
        self.assertNotIn(admission["id"], before.get("execution_observation", {}))
        terminal = json.loads((self.root / api._directory(admission["id"]) / "terminal.json").read_bytes())
        outcome = before["execution"][before["execution_outcome"][admission["id"]]["execution_id"]]["payload"]
        self.assertEqual(outcome["usage"]["units"], terminal["duration_s"])
        self.assertEqual(before["strategy_account"][admission["strategy_key"]]["charged_units"],
                         max(admission["reserved_units"], terminal["duration_s"]))
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"]},
            expected_revision=self.store.revision, request_id="recover-accounted-observation")
        self.assertTrue(result["ok"])
        after = self.store.snapshot()["records"]
        self.assertEqual(after["execution"], before["execution"])
        self.assertEqual(after["strategy_account"], before["strategy_account"])
        self.assertIn(admission["id"], after["execution_observation"])
        following = {key: admission[key] for key in ("cycle_id", "plan_digest", "command", "reserved_units")}
        following["id"] = "following-accounted-recovery"
        receipt = self.mutate(self.development().admit_execution, following)
        self.assertEqual(receipt["result"]["id"], following["id"])

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

    def test_local_terminal_recovery_rejects_changed_and_inserted_output_bytes(self):
        from research_harness.storage import Store
        api = importlib.import_module("research_harness.execution")
        admission = admit_lab(self, body="from pathlib import Path\nPath('results/value.json').write_text('{\"value\": 9}')\nprint('{\"metric\": 9}')\n",
            outputs=[{"id": "result", "requirement_id": "measurements", "path": "results/value.json", "media_type": "application/json"},
                     {"id": "late", "requirement_id": "checks", "path": "results/late.json", "media_type": "application/json"}])
        with mock.patch.object(api, "reconcile_execution", side_effect=OSError("Interrupted before the first outcome observation")):
            with self.assertRaises(OSError):
                api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="terminal-before-recovery")
        directory = api._directory(admission["id"])
        terminal = (self.root / directory / "terminal.json").read_bytes()
        original = self.store.snapshot()
        self.assertNotIn("execution_outcome", original["records"])
        changes = (("work/results/value.json", b'{"value":123456}'), ("stdout", b'{"metric":123456}\n'),
                   ("stderr", b"Injected diagnostics"), ("work/results/late.json", b'{"passed":true}'),
                   ("work/results/program.json", b'{"metric":123456}'),
                   ("work/results/value.json", None), ("stdout", None))
        for relative, changed in changes:
            with self.subTest(path=relative, removed=changed is None), tempfile.TemporaryDirectory(dir=self.root.parent) as temporary:
                copy_root = Path(temporary)
                shutil.copytree(self.root, copy_root, dirs_exist_ok=True)
                copied = Store(copy_root)
                if changed is None:
                    (copy_root / directory / relative).unlink()
                else:
                    (copy_root / directory / relative).write_bytes(changed)
                self.assertEqual((copy_root / directory / "terminal.json").read_bytes(), terminal)
                self.assert_error("execution_output_mismatch", lambda: api.reconcile_execution(copied,
                    {"admission_id": admission["id"]}, expected_revision=copied.revision, request_id="changed-output"))
                self.assertEqual(copied.snapshot(), original)
        with tempfile.TemporaryDirectory(dir=self.root.parent) as temporary:
            copy_root = Path(temporary)
            shutil.copytree(self.root, copy_root, dirs_exist_ok=True)
            copied = Store(copy_root)
            legacy = json.loads(terminal)
            legacy.pop("output_seal")
            (copy_root / directory / "terminal.json").write_text(json.dumps(legacy))
            self.assert_error("execution_output_seal_required", lambda: api.reconcile_execution(copied,
                {"admission_id": admission["id"]}, expected_revision=copied.revision, request_id="unsealed-terminal"))
            self.assertEqual(copied.snapshot(), original)
        # The unchanged original remains recoverable without another process or
        # reservation, even after the failed recovery attempts on exact copies.
        identity = {"expected_revision": self.store.revision, "request_id": "unchanged-output"}
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"]}, **identity)
        self.assertEqual(result["metric"], {"metric": 9})
        after = self.store.snapshot()
        self.assertEqual(after["records"]["strategy_account"], original["records"]["strategy_account"])
        self.assertEqual(len(after["records"]["execution"]), 1)
        self.assertEqual(api.reconcile_execution(self.store, {"admission_id": admission["id"]}, **identity), result)
        self.assertEqual(self.store.snapshot(), after)

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

    def start_worker_after(self, command):
        """Start the launcher's worker through a shell script that runs `command` and then execs the worker's argv.

        The patch replaces subprocess.Popen, which a local launch calls only to start its worker."""
        wrapper = Path(self.temporary.name) / "start-worker"
        wrapper.write_text('#!/bin/sh\n' + command + '\nexec "$@"\n')
        wrapper.chmod(0o755)
        popen = subprocess.Popen

        def start_through_wrapper(argv, **options):
            worker = popen([str(wrapper)] + argv, **options)
            # A worker that outlives its test stops with it.
            self.addCleanup(worker.wait)
            self.addCleanup(worker.kill)
            return worker

        return mock.patch.object(subprocess, "Popen", side_effect=start_through_wrapper)

    def test_worker_that_starts_within_the_run_timeout_completes_its_run(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n", timeout=30)
        api = importlib.import_module("research_harness.execution")
        started = time.monotonic()
        # A loaded machine starts the worker slowly: it writes ready.json after the 10 seconds that the wait adds to
        # the run's timeout, so only a wait that grows with that timeout sees it.
        with self.start_worker_after("sleep 12"):
            result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                          request_id="slow-worker-start")
        self.assertGreaterEqual(time.monotonic() - started, 12)
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 7})

    def test_worker_that_starts_after_a_run_timeout_of_five_seconds_completes_its_run(self):
        # 5 seconds is the default run timeout of admit_lab, which most launches of the suite bind.
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n", timeout=5)
        api = importlib.import_module("research_harness.execution")
        # The worker starts later than the run's timeout. The timeout counts from the program's start.
        with self.start_worker_after("sleep 7"):
            result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                          request_id="slow-worker-start-short-run")
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 7})

    def test_worker_that_exits_before_it_is_ready_leaves_the_claimed_run_to_reconcile(self):
        from research_harness.errors import ResearchError
        admission = admit_lab(self, body="raise RuntimeError('must never launch')\n", timeout=60)
        api = importlib.import_module("research_harness.execution")
        started = time.monotonic()
        # The script exits before it starts the worker, so ready.json is never written.
        with self.start_worker_after("exit 3"), self.assertRaises(ResearchError) as raised:
            api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                 request_id="worker-exits")
        # The wait ends when the worker exits, long before the run's timeout.
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("execution_recovery_required", "The launcher outcome is unknown; reconcile the claimed identity"))
        records = self.store.snapshot()["records"]
        self.assertIn(admission["id"], records["execution_claim"])
        self.assertNotIn("execution_outcome", records)
        # The documented recovery records the claimed run as interrupted.
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "interrupted",
            "reason": "The worker exited before it was ready."}, expected_revision=self.store.revision, request_id="recover-exited")
        self.assertFalse(result["ok"])
        records = self.store.snapshot()["records"]
        execution_id = records["execution_outcome"][admission["id"]]["execution_id"]
        self.assertEqual(records["execution"][execution_id]["payload"]["status"], "interrupted")

    def test_worker_that_stays_alive_and_not_ready_ends_the_wait_at_its_bound(self):
        from research_harness.errors import ResearchError
        admission = admit_lab(self, body="raise RuntimeError('must never launch')\n", timeout=0.15)
        api = importlib.import_module("research_harness.execution")
        started = time.monotonic()
        # The worker process stays alive for 60 seconds and never writes ready.json.
        with self.start_worker_after("exec sleep 60"), self.assertRaises(ResearchError) as raised:
            api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                 request_id="worker-never-ready")
        # The wait ends at the run's timeout plus 10 seconds, and the exit wait after it at 6 seconds more, both while
        # the worker is alive.
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("execution_recovery_required", "The worker is still live or its outcome is unknown"))
        records = self.store.snapshot()["records"]
        self.assertIn(admission["id"], records["execution_claim"])
        self.assertNotIn("execution_outcome", records)

    def test_worker_that_ends_after_the_wait_for_its_run_gives_the_run_outcome(self):
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n")
        api = importlib.import_module("research_harness.execution")
        wait = subprocess.Popen.wait

        def expire_the_wait_for_the_run(worker, timeout):
            # Only the wait for the run is longer than the 6-second exit wait. It expires at once, as when hashing
            # large outputs takes longer than its bound, and the worker then ends within the exit wait.
            if timeout > 6:
                raise subprocess.TimeoutExpired(worker.args, timeout)
            return wait(worker)

        with mock.patch.object(subprocess.Popen, "wait", autospec=True, side_effect=expire_the_wait_for_the_run):
            result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                          request_id="late-worker-end")
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 7})

    def test_worker_still_live_after_the_wait_for_its_run_leaves_the_outcome_to_reconcile(self):
        from research_harness.errors import ResearchError
        admission = admit_lab(self, body="import time\ntime.sleep(5)\nprint('{\"metric\": 7}')\n", timeout=30)
        api = importlib.import_module("research_harness.execution")
        workers = []

        def expire_each_wait(worker, timeout):
            # The wait for the run and the exit wait both expire while the program still sleeps.
            workers.append(worker)
            raise subprocess.TimeoutExpired(worker.args, timeout)

        with mock.patch.object(subprocess.Popen, "wait", autospec=True, side_effect=expire_each_wait), \
                self.assertRaises(ResearchError) as raised:
            api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                 request_id="live-worker")
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("execution_recovery_required", "The worker is still live or its outcome is unknown"))
        self.assertNotIn("execution_outcome", self.store.snapshot()["records"])
        # Once the worker ends, reconciliation records the outcome of its run.
        workers[0].wait()
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"]},
                                         expected_revision=self.store.revision, request_id="reconcile-live-worker")
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 7})

    def test_worker_that_ends_before_its_token_leaves_the_claimed_run_to_reconcile(self):
        from research_harness.errors import ResearchError
        admission = admit_lab(self, body="raise RuntimeError('must never launch')\n")
        api = importlib.import_module("research_harness.execution")
        popen = subprocess.Popen
        prelaunch = api._prelaunch
        workers = []

        def start_and_keep(argv, **options):
            # A local launch calls subprocess.Popen only to start its worker.
            workers.append(popen(argv, **options))
            return workers[-1]

        def end_the_worker_then_prelaunch(*args):
            # The worker is ready and waits for its token when it ends, as when it is killed.
            workers[0].kill()
            workers[0].wait()
            return prelaunch(*args)

        with mock.patch.object(subprocess, "Popen", side_effect=start_and_keep), \
                mock.patch.object(api, "_prelaunch", side_effect=end_the_worker_then_prelaunch), \
                self.assertRaises(ResearchError) as raised:
            api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                 request_id="worker-ends-before-token")
        self.assertEqual((raised.exception.code, raised.exception.message), ("execution_recovery_required",
            "No terminal outcome is available; preserve the claim and reconcile. A dead local owner may be recorded "
            "interrupted with a reason."))
        records = self.store.snapshot()["records"]
        self.assertIn(admission["id"], records["execution_claim"])
        self.assertNotIn("execution_outcome", records)
        # The documented recovery records the claimed run as interrupted.
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "interrupted",
            "reason": "The worker ended before it received its token."}, expected_revision=self.store.revision,
            request_id="recover-ended-before-token")
        self.assertFalse(result["ok"])
        records = self.store.snapshot()["records"]
        execution_id = records["execution_outcome"][admission["id"]]["execution_id"]
        self.assertEqual(records["execution"][execution_id]["payload"]["status"], "interrupted")

    def test_run_timeout_shorter_than_the_worker_start_still_gives_an_observed_run(self):
        admission = admit_lab(self, body="import time\ntime.sleep(30)\n", timeout=0.15)
        api = importlib.import_module("research_harness.execution")
        # The worker starts later than the whole run may take, and within the 10 seconds that the wait adds to it.
        with self.start_worker_after("sleep 1"):
            result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                          request_id="short-run")
        self.assertTrue(result["timed_out"])

    def test_run_timeout_of_thirty_days_completes_its_run(self):
        # bind-run accepts any finite positive timeout, and the launch waits for the run's timeout plus 10 seconds.
        # Thirty days is longer than the 2**31 - 1 milliseconds (about 24.86 days) that a select.poll timeout holds.
        admission = admit_lab(self, body="print('{\"metric\": 7}')\n", timeout=2592000)
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision,
                                      request_id="thirty-day-run")
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 7})

    def test_symlinked_venv_interpreter_runs_the_program_with_its_own_site_packages(self):
        interpreter = make_venv(self)
        admission = admit_lab(self, interpreter=str(interpreter),
                              body="import json\nimport venv_only_probe\nprint(json.dumps({'metric': venv_only_probe.VALUE}))\n")
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="venv-run")
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 11})
        runtime = self.store.snapshot()["records"]["execution_binding"][admission["id"]]["runtime"]
        self.assertEqual(runtime, {"path": str(interpreter), "resolved_path": str(interpreter.resolve()),
                                   "python": sys.version.split()[0],
                                   "sha256": hashlib.sha256(interpreter.resolve().read_bytes()).hexdigest()})

    def test_real_interpreter_file_keeps_the_runtime_record_of_earlier_releases(self):
        interpreter = Path(sys.executable).resolve()
        admission = admit_lab(self, interpreter=str(interpreter), body="print('{\"metric\": 7}')\n")
        runtime = self.store.snapshot()["records"]["execution_binding"][admission["id"]]["runtime"]
        # The record that earlier releases wrote for every admitted interpreter.
        self.assertEqual(runtime, {"path": str(Path(sys.executable).resolve()), "python": sys.version.split()[0],
                                   "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()})
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="real-file")
        self.assertTrue(result["ok"], result["stderr_tail"])

    def test_real_interpreter_file_named_through_a_linked_directory_keeps_the_record_of_earlier_releases(self):
        interpreter = Path(sys.executable).resolve()
        linked = Path(self.temporary.name) / "linked-bin"
        linked.symlink_to(interpreter.parent, target_is_directory=True)
        admitted = linked / interpreter.name
        self.assertFalse(admitted.is_symlink())
        admission = admit_lab(self, interpreter=str(admitted), body="print('{\"metric\": 3}')\n")
        runtime = self.store.snapshot()["records"]["execution_binding"][admission["id"]]["runtime"]
        # Earlier releases recorded the resolved file of every admitted interpreter.
        self.assertEqual(runtime, {"path": str(interpreter), "python": sys.version.split()[0],
                                   "sha256": hashlib.sha256(interpreter.read_bytes()).hexdigest()})
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="linked-directory")
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 3})

    def admit_as_earlier_release(self, body):
        """Admit a link to this interpreter, bound as exactory-client 0.47.0 did: the resolved file is the path."""
        api = importlib.import_module("research_harness.execution")
        link = Path(self.temporary.name) / "bin/python3"
        link.parent.mkdir()
        link.symlink_to(Path(sys.executable).resolve())
        earlier = {"path": str(Path(sys.executable).resolve()), "python": sys.version.split()[0],
                   "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()}
        with mock.patch.object(api, "_runtime", return_value=earlier):
            return admit_lab(self, interpreter=str(link), body=body)

    def test_binding_that_pinned_the_resolved_interpreter_path_still_launches(self):
        admission = self.admit_as_earlier_release("print('{\"metric\": 5}')\n")
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="earlier-binding")
        self.assertTrue(result["ok"], result["stderr_tail"])
        self.assertEqual(result["metric"], {"metric": 5})

    def build_bind_run_payload(self, admission_id):
        """The bind-run payload of the recorded binding, as a retry with a new request ID sends it again."""
        binding = self.store.snapshot()["records"]["execution_binding"][admission_id]
        return {key: binding[key] for key in ("admission_id", "script", "backend", "timeout_seconds", "inputs", "outputs", "usage_unit")}

    def test_identical_bind_run_again_returns_the_binding_of_an_earlier_release(self):
        admission = self.admit_as_earlier_release("print('{\"metric\": 5}')\n")
        api = importlib.import_module("research_harness.execution")
        bindings = self.store.snapshot()["records"]["execution_binding"]
        payload = self.build_bind_run_payload(admission["id"])
        # A retry with a new request ID, as exactory-client 0.47.0 accepted it.
        self.assertEqual(self.mutate(api.bind_execution, payload)["result"], bindings[admission["id"]])
        self.assertEqual(self.store.snapshot()["records"]["execution_binding"], bindings)
        self.assert_error("record_conflict", lambda: self.mutate(api.bind_execution, dict(payload, timeout_seconds=6)))
        self.assertEqual(self.store.snapshot()["records"]["execution_binding"], bindings)

    def test_identical_bind_run_again_returns_an_earlier_release_binding_of_a_figure_as_result_evidence(self):
        api = importlib.import_module("research_harness.execution")
        outputs = [{"id": "result", "requirement_id": "measurements", "path": "results/result.json", "media_type": "application/json"},
                   {"id": "plot", "requirement_id": "measurements", "path": "plots/figure.png", "media_type": "image/png"}]
        # exactory-client 0.47.0 bound an output of any media type to a result requirement.
        with mock.patch.object(api, "is_json_media_type", return_value=True):
            admission = admit_lab(self, body="print('{\"metric\": 7}')\n", outputs=outputs)
        bindings = self.store.snapshot()["records"]["execution_binding"]
        self.assertEqual(bindings[admission["id"]]["outputs"], outputs)
        payload = self.build_bind_run_payload(admission["id"])
        # A retry with a new request ID returns the recorded binding, as exactory-client 0.47.0 did.
        self.assertEqual(self.mutate(api.bind_execution, payload)["result"], bindings[admission["id"]])
        self.assertEqual(self.store.snapshot()["records"]["execution_binding"], bindings)
        # The binding is immutable, so a changed payload is still refused.
        self.assert_error("record_conflict", lambda: self.mutate(api.bind_execution, dict(payload, timeout_seconds=6)))
        self.assertEqual(self.store.snapshot()["records"]["execution_binding"], bindings)

    def admit_linked_interpreter(self):
        """Admit a link to one of two byte-identical interpreter files of separate installations.

        Both files are shell scripts that write a marker when they start, so a test sees whether an
        interpreter behind the link ever ran. Returns the admission, the link, the other file and the marker."""
        temporary = Path(self.temporary.name)
        marker = temporary / "interpreter-started"
        data = ("#!/bin/sh\necho started > '" + str(marker) + "'\n").encode()
        installations = []
        for name in ("admitted", "other"):
            path = temporary / name / "bin/python3"
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            path.chmod(0o755)
            installations.append(path)
        link = temporary / "venv/bin/python3"
        link.parent.mkdir(parents=True)
        link.symlink_to(installations[0])
        with mock.patch.object(sys, "executable", str(link)):
            admission = admit_lab(self, interpreter=str(link), body="raise RuntimeError('must never launch')\n")
        return admission, link, installations[1], marker

    def claim_under(self, link):
        """Claim as a command line started by the link would; the worker still starts under this interpreter."""
        api = importlib.import_module("research_harness.execution")
        claim = api._claim

        def claim_through_link(*args):
            with mock.patch.object(sys, "executable", str(link)):
                return claim(*args)

        return mock.patch.object(api, "_claim", side_effect=claim_through_link)

    def test_link_retargeted_to_an_identical_file_of_another_installation_stops_the_claim(self):
        api = importlib.import_module("research_harness.execution")
        admission, link, other, marker = self.admit_linked_interpreter()
        link.unlink()
        link.symlink_to(other)
        before = self.store.snapshot()
        with self.claim_under(link):
            self.assert_error("execution_runtime_changed", lambda: api.launch_execution(self.store, admission["id"],
                expected_revision=self.store.revision, request_id="retargeted-link"))
        self.assertEqual(self.store.snapshot(), before)
        self.assertFalse(marker.exists())

    def test_link_retargeted_after_the_claim_never_starts_the_other_installation(self):
        api = importlib.import_module("research_harness.execution")
        admission, link, other, marker = self.admit_linked_interpreter()
        prelaunch = api._prelaunch

        def retarget_then_prelaunch(*args):
            # The worker waits for its token and checks the interpreter only after this step.
            link.unlink()
            link.symlink_to(other)
            return prelaunch(*args)

        with self.claim_under(link), mock.patch.object(api, "_prelaunch", side_effect=retarget_then_prelaunch):
            self.assert_error("execution_recovery_required", lambda: api.launch_execution(self.store, admission["id"],
                expected_revision=self.store.revision, request_id="retargeted-after-claim"))
        directory = self.root / api._directory(admission["id"])
        # The retarget happened, and the worker itself refused the file behind the link.
        self.assertEqual(link.resolve(), other.resolve())
        self.assertIn(b"Worker interpreter differs from admission", (directory / "launcher.log").read_bytes())
        self.assertFalse(marker.exists())
        self.assertFalse((directory / "terminal.json").exists())
        # The documented recovery records the claimed run as interrupted, so it no longer stays pending.
        result = api.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "interrupted",
            "reason": "The worker refused an interpreter that changed after the claim."},
            expected_revision=self.store.revision, request_id="recover-retargeted")
        self.assertFalse(result["ok"])
        records = self.store.snapshot()["records"]
        execution_id = records["execution_outcome"][admission["id"]]["execution_id"]
        self.assertEqual(records["execution"][execution_id]["payload"]["status"], "interrupted")

    def test_changed_bytes_or_version_behind_a_linked_interpreter_stop_the_claim(self):
        api = importlib.import_module("research_harness.execution")
        base = Path(self.temporary.name) / "base/python3"
        link = Path(self.temporary.name) / "venv/bin/python3"
        for path in (base, link):
            path.parent.mkdir(parents=True)
        base.write_bytes(b"admitted interpreter bytes")
        link.symlink_to(base)
        # The command line runs under the linked interpreter; the claim is refused before any process starts.
        with mock.patch.object(sys, "executable", str(link)):
            admission = admit_lab(self, interpreter=str(link), body="raise RuntimeError('must never launch')\n")
            before = self.store.snapshot()

            def launch():
                api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="changed-runtime")

            base.write_bytes(b"replaced interpreter bytes")
            self.assert_error("execution_runtime_changed", launch)
            base.write_bytes(b"admitted interpreter bytes")
            with mock.patch.object(sys, "version", "3.0.0 (another release)"):
                self.assert_error("execution_runtime_changed", launch)
        self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn("execution_claim", before["records"])

    def bind_outputs(self, outputs, admission_id="lab-run"):
        api = importlib.import_module("research_harness.execution")
        return self.mutate(api.bind_execution, {"admission_id": admission_id, "script": "code/program.py", "backend": "local",
            "timeout_seconds": 5, "inputs": [], "outputs": outputs, "usage_unit": "execution"})

    def test_output_that_no_locator_can_cite_is_refused_for_result_and_validation_evidence(self):
        from research_harness.errors import ResearchError
        plan = self.plan()
        plan["evidence_requirements"].append({"id": "figures", "kind": "log", "description": "The rendered figures."})
        json_result = {"id": "result", "requirement_id": "measurements", "path": "results/result.json", "media_type": "application/json"}
        refused = [
            [{"id": "rendered-plot", "requirement_id": "measurements", "path": "plots/figure.png", "media_type": "image/png"}],
            [{"id": "paper-pdf", "requirement_id": "measurements", "path": "plots/figure.pdf", "media_type": "application/pdf"}],
            [json_result, {"id": "raw-array", "requirement_id": "measurements", "path": "results/values.npy",
                           "media_type": "application/octet-stream"}],
            [json_result, {"id": "check-plot", "requirement_id": "checks", "path": "plots/check.png", "media_type": "image/png"}]]
        # The admission is recorded; its first binding, a PNG as the only result output, is refused.
        with self.assertRaises(ResearchError) as first:
            admit_lab(self, plan=plan, body="print('{\"metric\": 7}')\n", outputs=refused[0])
        self.assertEqual(first.exception.code, "invalid_execution")
        for outputs in refused:
            output = outputs[-1]
            with self.subTest(output=output["id"]):
                before = self.store.snapshot()
                with self.assertRaises(ResearchError) as raised:
                    self.bind_outputs(outputs)
                self.assertEqual(raised.exception.code, "invalid_execution")
                self.assertEqual(raised.exception.details, {"output_id": output["id"], "requirement_id": output["requirement_id"],
                                                            "media_type": output["media_type"]})
                for named in (output["id"], output["media_type"], "text or JSON", "log requirement"):
                    self.assertIn(named, raised.exception.message)
                self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn("execution_binding", self.store.snapshot()["records"])
        accepted = [json_result,
                    {"id": "summary", "requirement_id": "measurements", "path": "results/summary.json", "media_type": "application/ld+json"},
                    {"id": "checks", "requirement_id": "checks", "path": "results/checks.txt", "media_type": "text/plain; charset=utf-8"},
                    {"id": "rendered-plot", "requirement_id": "figures", "path": "plots/figure.png", "media_type": "image/png"},
                    {"id": "paper-pdf", "requirement_id": "figures", "path": "plots/figure.pdf", "media_type": "application/pdf"}]
        self.bind_outputs(accepted)
        self.assertEqual(self.store.snapshot()["records"]["execution_binding"]["lab-run"]["outputs"], accepted)

    def test_declared_json_validation_output_supplies_the_metric_of_a_differently_named_program(self):
        from research_harness.execution_evidence import _observed
        body = ("import json\nfrom pathlib import Path\nvalues = [n * n for n in range(4)]\n"
                "Path('results/result.json').write_text(json.dumps({'values': values}))\n"
                "Path('results/notes.txt').write_text('Every square is at most 9.')\n"
                "Path('results/run_cycle.json').write_text(json.dumps({'passed': max(values) == 9, 'maximum': max(values)}))\n")
        # The first JSON output of a validation requirement follows a JSON result output and a text validation output.
        admission = admit_lab(self, "code/refine_cycle.py", body=body, outputs=[
            {"id": "result", "requirement_id": "measurements", "path": "results/result.json", "media_type": "application/json"},
            {"id": "notes", "requirement_id": "checks", "path": "results/notes.txt", "media_type": "text/plain"},
            {"id": "validation", "requirement_id": "checks", "path": "results/run_cycle.json", "media_type": "application/json"}])
        api = importlib.import_module("research_harness.execution")
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="refine")
        self.assertEqual((result["ok"], result["is_buggy"], result["metric"]), (True, False, {"passed": True, "maximum": 9}))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["execution_claim"][admission["id"]]["config"]["metric_output"], "work/results/run_cycle.json")
        # Author readiness recomputes the metric from the recorded config and the sealed bytes.
        _observed(records, self.artifacts, records["execution_outcome"][admission["id"]]["execution_id"])

    def test_launched_run_records_no_metric_that_a_later_python_cannot_read_back(self):
        from research_harness.execution_evidence import _observed
        # The program writes an integer of 4301 digits as text, so it runs under any Python.
        body = ("from pathlib import Path\n"
                "Path('results/result.json').write_text('{\"values\": [0, 1, 4, 9]}')\n"
                "Path('results/run_cycle.json').write_text('{\"count\": 1' + '0' * 4300 + '}')\n")
        admission = admit_lab(self, "code/refine_cycle.py", body=body, outputs=[
            {"id": "result", "requirement_id": "measurements", "path": "results/result.json", "media_type": "application/json"},
            {"id": "validation", "requirement_id": "checks", "path": "results/run_cycle.json", "media_type": "application/json"}])
        api = importlib.import_module("research_harness.execution")
        limit = sys.get_int_max_str_digits() if hasattr(sys, "get_int_max_str_digits") else None
        if limit is not None:
            # Reconcile as Python 3.9.6 does: it reads an integer of any length.
            self.addCleanup(sys.set_int_max_str_digits, limit)
            sys.set_int_max_str_digits(0)
        result = api.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="long-integer")
        self.assertEqual((result["ok"], result["is_buggy"], result["metric"]), (False, True, None))
        if limit is not None:
            # Read the store as Python 3.11 and later do.
            sys.set_int_max_str_digits(limit)
        records = self.store.snapshot()["records"]
        _observed(records, self.artifacts, records["execution_outcome"][admission["id"]]["execution_id"])


class RunMetricTests(unittest.TestCase):
    def test_metric_sources_keep_their_order_and_an_earlier_run_config_keeps_its_metric(self):
        from research_harness.execution_outputs import output_metric
        config = {"script": "code/refine_cycle.py", "metric_output": "work/results/run_cycle.json"}
        declared = {"work/results/run_cycle.json": b'{"passed": true}'}
        stem = {"work/results/refine_cycle.json": b'{"metric": 2}'}
        stdout = {"stdout": b'started\n{"metric": 1}\n'}
        self.assertEqual(output_metric(config, declared), {"passed": True})
        self.assertEqual(output_metric(config, {**declared, **stem}), {"metric": 2})
        self.assertEqual(output_metric(config, {**declared, **stem, **stdout}), {"metric": 1})
        self.assertIsNone(output_metric(dict(config, metric_output=None), declared))
        # A declared output that is not finite JSON supplies no metric, so its run still reconciles.
        for data in (b'{"passed": tr', b'{"ratio": NaN}'):
            self.assertIsNone(output_metric(config, {"work/results/run_cycle.json": data}))
        # A run config recorded before metric_output existed yields the metric it yielded before.
        earlier = {"script": "code/refine_cycle.py"}
        self.assertIsNone(output_metric(earlier, declared))
        self.assertEqual(output_metric(earlier, {**declared, **stem}), {"metric": 2})

    def test_declared_validation_output_of_any_size_supplies_the_metric(self):
        from research_harness.execution_outputs import output_metric
        config = {"script": "code/refine_cycle.py", "metric_output": "work/results/run_cycle.json"}

        def build_validation_json(size):
            # A JSON object of exactly `size` bytes: {"checks": "xx...x"}.
            return b'{"checks": "' + b"x" * (size - 14) + b'"}'

        # The declared validation output has no size bound, as the fallback file and a stdout line have none.
        for size in (16 * 1024 + 1, 575793):
            data = build_validation_json(size)
            self.assertEqual(len(data), size)
            for path in ("work/results/refine_cycle.json", "work/results/run_cycle.json"):
                with self.subTest(size=size, path=path):
                    self.assertEqual(output_metric(config, {path: data}), {"checks": "x" * (size - 14)})

    def test_each_metric_source_gives_only_json_that_every_supported_python_reads_back(self):
        from research_harness.execution_outputs import output_metric
        # Python 3.9.6 reads an integer of any length, and Python 3.11 and later refuse one of more than
        # 4300 digits. Read as the earlier Python does, so that the metric bound itself must refuse it.
        if hasattr(sys, "set_int_max_str_digits"):
            self.addCleanup(sys.set_int_max_str_digits, sys.get_int_max_str_digits())
            sys.set_int_max_str_digits(0)
        config = {"script": "code/refine_cycle.py", "metric_output": "work/results/run_cycle.json"}
        earlier = {"script": "code/refine_cycle.py"}
        fallback, declared = "work/results/refine_cycle.json", "work/results/run_cycle.json"

        def build_nested_json(levels):
            # An object and levels - 1 lists inside it: {"metric": [[...[0]...]]}.
            return b'{"metric": ' + b"[" * (levels - 1) + b"0" + b"]" * (levels - 1) + b"}"

        def build_integer_json(digits):
            return b'{"metric": ' + b"9" * digits + b"}"

        for dimension, within, beyond in (("nesting", build_nested_json(32), build_nested_json(33)),
                                          ("integer digits", build_integer_json(4300), build_integer_json(4301))):
            with self.subTest(dimension=dimension):
                for path in ("stdout", fallback, declared):
                    self.assertEqual(output_metric(config, {path: within}), json.loads(within))
                # A stdout line beyond the bound is skipped, so the earlier metric line stays the metric.
                self.assertEqual(output_metric(config, {"stdout": b'{"metric": 1}\n' + beyond + b"\n"}), {"metric": 1})
                # A fallback file beyond the bound gives no metric, so the declared validation output supplies it.
                self.assertEqual(output_metric(config, {fallback: beyond, declared: b'{"passed": true}'}), {"passed": True})
                self.assertIsNone(output_metric(config, {declared: beyond}))
                # A run config of exactory-client 0.47.0 or earlier keeps the unbounded metric it recorded.
                for path in ("stdout", fallback):
                    self.assertEqual(output_metric(earlier, {path: beyond}), json.loads(beyond))

    def test_unreadable_fallback_file_gives_no_metric_instead_of_stopping_reconciliation(self):
        from research_harness.execution_outputs import output_metric
        fallback, declared = "work/results/refine_cycle.json", "work/results/run_cycle.json"
        # A fallback file that is not finite JSON, as a program stopped while writing it leaves, gives no metric.
        # Python 3.11 and later read an integer of more than 4300 digits the same way. The declared validation
        # output still supplies the metric.
        truncated = {fallback: b'{"metric": tr', declared: b'{"passed": true}'}
        self.assertEqual(output_metric({"script": "code/refine_cycle.py", "metric_output": declared}, truncated), {"passed": True})
        # A run config of exactory-client 0.47.0 or earlier has no third source; its run reconciles without a metric.
        self.assertIsNone(output_metric({"script": "code/refine_cycle.py"}, truncated))
