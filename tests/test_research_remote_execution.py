"""Real shared-folder launches and conservative recovery of unknown outcomes."""

import os
import threading
import time
from unittest import mock

from development_fixtures import DevelopmentCase
from integration_fixtures import admit_lab
from research_harness import execution, remote_execution


class RemoteExecutionTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.sync = self.root / "transport"
        self.sync.mkdir()
        patch = mock.patch.dict(os.environ, {"EXACTORY_LAB_COLAB_DIR": str(self.sync),
            "EXACTORY_LAB_COLAB_POLL": "0.01", "EXACTORY_LAB_COLAB_WAIT": "0"})
        patch.start()
        self.addCleanup(patch.stop)

    def runner(self):
        errors = []
        stop = threading.Event()
        def run():
            try:
                while not stop.is_set():
                    if remote_execution.serve_scan_once(self.sync):
                        return
                    stop.wait(0.01)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(10)))
        return thread, errors

    def test_roundtrip_pins_seed_timeout_runtime_and_replays_without_new_job(self):
        admission = admit_lab(self, backend="colab", seed=83, timeout=5,
            body="import os, json\nprint(json.dumps({'metric': int(os.environ['EXACTORY_LAB_SEED'])}))\n")
        thread, errors = self.runner()
        identity = {"expected_revision": self.store.revision, "request_id": "remote-run"}
        result = execution.launch_execution(self.store, admission["id"], **identity)
        thread.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(result["ok"])
        self.assertEqual((result["backend"], result["metric"], result["seed"], result["timeout_seconds"]),
                         ("colab", {"metric": 83}, 83, 5))
        before = self.store.snapshot()
        self.assertEqual(execution.launch_execution(self.store, admission["id"], **identity), result)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(len(list((self.sync / "jobs").iterdir())), 1)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        self.assertFalse(execution.execution_status(self.store, admission["id"])["scientific_validation"])

    def test_no_runner_stays_pending_and_reconciliation_reuses_the_only_job(self):
        admission = admit_lab(self, backend="colab", timeout=0.1, body="print('{\"metric\": 3}')\n")
        identity = {"expected_revision": self.store.revision, "request_id": "pending-run"}
        self.assert_error("execution_recovery_required", lambda: execution.launch_execution(self.store, admission["id"], **identity))
        before = self.store.snapshot()["records"]
        self.assertNotIn("execution_outcome", before)
        self.assert_error("execution_recovery_required", lambda: execution.reconcile_execution(self.store,
            {"admission_id": admission["id"], "resolution": "interrupted", "reason": "Transport waiting ended."},
            expected_revision=self.store.revision, request_id="cannot-infer-interruption"))
        self.assertEqual(len(list((self.sync / "jobs").iterdir())), 1)
        thread, errors = self.runner()
        for _ in range(100):
            try:
                result = execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
                    expected_revision=self.store.revision, request_id="collect-original")
                break
            except execution.ResearchError as error:
                self.assertEqual(error.code, "execution_recovery_required")
                time.sleep(0.02)
        else:
            self.fail("The released original remote job was not observed")
        thread.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.store.snapshot()["records"]["execution"]), 1)
        self.assertEqual(before["strategy_account"], self.store.snapshot()["records"]["strategy_account"])

    def test_source_change_between_ready_and_release_prevents_real_remote_execution(self):
        admission = admit_lab(self, backend="colab", body="raise RuntimeError('must never run')\n")
        claim = execution._claim(self.store, admission["id"], self.store.revision, "claim-before-change")["result"]
        root, job = remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        self.refresh_synthesis("changed-before-release")
        self.assert_error("plan_dependencies_stale", lambda: remote_execution._release(self.store, claim, root, job))
        self.assertFalse(list((self.sync / "jobs" / job["job_id"]).glob("GO-*")))
        self.assertFalse(list((self.sync / "jobs" / job["job_id"]).glob("STARTED-*")))
        self.assertNotIn("execution_outcome", self.store.snapshot()["records"])
        accounts = self.store.snapshot()["records"]["strategy_account"]
        result = execution.reconcile_execution(self.store, {"admission_id": admission["id"], "resolution": "not_released",
            "reason": "The complete preparation changed before any durable remote execution authorization."},
            expected_revision=self.store.revision, request_id="close-unreleased")
        self.assertFalse(result["ok"])
        self.assertEqual(self.store.snapshot()["records"]["strategy_account"], accounts)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))

    def test_remote_byte_tampering_refuses_observation_after_the_actual_run(self):
        admission = admit_lab(self, backend="colab", timeout=5, body="print('{\"metric\": 9}')\n")
        thread, errors = self.runner()
        with mock.patch.object(remote_execution, "collect_colab", side_effect=OSError("Lost collection response")):
            with self.assertRaises(OSError):
                execution.launch_execution(self.store, admission["id"], expected_revision=self.store.revision, request_id="lost-result")
        thread.join(10)
        self.assertEqual(errors, [])
        self.assert_error("execution_recovery_required", lambda: execution.reconcile_execution(self.store,
            {"admission_id": admission["id"], "resolution": "not_released", "reason": "A released worker may already have run."},
            expected_revision=self.store.revision, request_id="cannot-cancel-released"))
        result_directory = next((self.sync / "results").iterdir())
        (result_directory / "stdout").write_text("tampered")
        self.assert_error("execution_identity_mismatch", lambda: execution.reconcile_execution(self.store,
            {"admission_id": admission["id"]}, expected_revision=self.store.revision, request_id="tampered-result"))
        self.assertNotIn("execution_outcome", self.store.snapshot()["records"])
        self.assertFalse(remote_execution.serve_scan_once(self.sync))

    def test_lost_first_go_cannot_be_dispatched_after_preparation_changes(self):
        from research_harness.gates import gate_report
        admission = admit_lab(self, backend="colab", body="print('{\"metric\": 42}')\n")
        claim = execution._claim(self.store, admission["id"], self.store.revision, "claim-before-lost-go")["result"]
        root, job = remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        write = remote_execution._immutable

        def interrupt_first_go(directory, path, data):
            if "/GO-" in path:
                raise OSError("Interrupted after the release committed and before the first signal")
            return write(directory, path, data)

        with mock.patch.object(remote_execution, "_immutable", side_effect=interrupt_first_go):
            with self.assertRaises(OSError):
                remote_execution._release(self.store, claim, root, job)
        folder = self.sync / "jobs" / job["job_id"]
        self.assertIn(admission["id"], self.store.snapshot()["records"]["execution_remote_release"])
        self.assertFalse(list(folder.glob("GO-*")))
        self.assertFalse(list(folder.glob("STARTED-*")))
        self.refresh_synthesis("after-lost-go")
        self.assertFalse(gate_report(self.store, "execution")["ready"])
        before = self.store.snapshot()
        refused = None
        try:
            execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
                expected_revision=self.store.revision, request_id="recover-stale-first-go")
        except execution.ResearchError as error:
            refused = error
        self.assertFalse(remote_execution.serve_scan_once(self.sync), "A previously unreleased stale job actually executed")
        self.assertIsNotNone(refused)
        self.assertEqual(refused.code, "plan_dependencies_stale")
        self.assertFalse(list(folder.glob("GO-*")))
        self.assertFalse(list(folder.glob("STARTED-*")))
        self.assertEqual(self.store.snapshot(), before)

    def test_completed_remote_job_is_collected_after_preparation_change_without_recreating_go(self):
        admission = admit_lab(self, backend="colab", body="print('{\"metric\": 42}')\n")
        claim = execution._claim(self.store, admission["id"], self.store.revision, "completed-original")["result"]
        root, job = remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        remote_execution._release(self.store, claim, root, job)
        self.assertTrue(remote_execution.serve_scan_once(self.sync))
        folder = self.sync / "jobs" / job["job_id"]
        go = next(folder.glob("GO-*"))
        go.unlink()
        self.refresh_synthesis("after-remote-completion")
        accounts = self.store.snapshot()["records"]["strategy_account"]
        result = execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
            expected_revision=self.store.revision, request_id="collect-completed-original")
        self.assertEqual(result["metric"], {"metric": 42})
        self.assertFalse(go.exists())
        self.assertEqual(self.store.snapshot()["records"]["strategy_account"], accounts)
        self.assertEqual(len(self.store.snapshot()["records"]["execution"]), 1)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))

    def test_lost_first_go_recovery_keeps_the_original_claim_and_reservation(self):
        admission = admit_lab(self, backend="colab", body="print('{\"metric\": 11}')\n")
        claim = execution._claim(self.store, admission["id"], self.store.revision, "current-original")["result"]
        root, job = remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        with mock.patch.object(remote_execution, "_immutable", side_effect=OSError("Lost first GO write")):
            with self.assertRaises(OSError):
                remote_execution._release(self.store, claim, root, job)
        before = self.store.snapshot()
        remote_execution._release(self.store, claim, root, job)
        self.assertEqual(self.store.snapshot(), before)
        self.assertTrue(remote_execution.serve_scan_once(self.sync))
        result = execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
            expected_revision=self.store.revision, request_id="recover-current-original")
        self.assertEqual(result["metric"], {"metric": 11})
        self.assertEqual(self.store.snapshot()["records"]["strategy_account"], before["records"]["strategy_account"])
        self.assertEqual(len(list((self.sync / "jobs").iterdir())), 1)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))

    def test_preparation_change_during_missing_go_check_prevents_dispatch(self):
        admission = admit_lab(self, backend="colab", body="print('{\"metric\": 17}')\n")
        claim = execution._claim(self.store, admission["id"], self.store.revision, "changing-original")["result"]
        root, job = remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        with mock.patch.object(remote_execution, "_immutable", side_effect=OSError("Lost first GO write")):
            with self.assertRaises(OSError):
                remote_execution._release(self.store, claim, root, job)
        before = self.store.snapshot()["records"]
        actual = execution._files

        def change_during_validation(store, admitted, binding):
            result = actual(store, admitted, binding)
            self.refresh_synthesis("during-current-go-check")
            return result

        refused = None
        with mock.patch.object(execution, "_files", side_effect=change_during_validation):
            try:
                remote_execution._release(self.store, claim, root, job)
            except execution.ResearchError as error:
                refused = error
        self.assertFalse(remote_execution.serve_scan_once(self.sync), "A revision changed during authorization but the job executed")
        self.assertIsNotNone(refused)
        self.assertEqual(refused.code, "stale_revision")
        after = self.store.snapshot()["records"]
        self.assertEqual(after["strategy_account"], before["strategy_account"])
        self.assertEqual(after["execution_claim"], before["execution_claim"])
        self.assertEqual(after["execution_remote_release"], before["execution_remote_release"])
        self.assertFalse(list((self.sync / "jobs" / job["job_id"]).glob("GO-*")))
