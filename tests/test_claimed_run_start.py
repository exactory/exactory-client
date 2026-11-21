"""A claimed local run whose program never started is started by the same request, and by nothing else."""

import contextlib
import sqlite3
import subprocess
import sys
from unittest import mock

from development_fixtures import DevelopmentCase
from integration_fixtures import admit_lab
from research_harness import execution, finishing, storage
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.storage import Store
from research_harness.workspace import json_projection

# The commit entries of the store. A release before the one that adds mutate_serialized has the first two.
COMMIT_ENTRIES = [name for name in ("mutate", "mutate_scoped", "mutate_serialized") if hasattr(Store, name)]


class WaitlessTime:
    """What research_harness.finishing sees as the time module: it never sleeps, and each reading of its clock passes
    ten seconds, so that the wait of a finishing write ends without real time."""

    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        pass

    def monotonic(self):
        self.now += 10.0
        return self.now


class ClaimedRunStartTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.marker = self.root / "program-ran.txt"
        self.admission = admit_lab(self, body="open(%r, 'a').write('ran\\n')\nprint('{\"metric\": 7}')\n" % str(self.marker))

    def launch(self, request_id="launch"):
        return execution.launch_execution(self.store, self.admission["id"], expected_revision=self.store.revision,
                                          request_id=request_id)

    def run_count(self):
        return len(self.marker.read_text().splitlines()) if self.marker.exists() else 0

    def run_folder(self):
        return self.root / "research/runs" / digest({"admission_id": self.admission["id"]})

    def claim_run(self, request_id="launch"):
        return execution._claim(self.store, self.admission["id"], self.store.revision, request_id)["result"]

    def refuse_the_start(self):
        """Launch while another connection holds the write lock from the first commit of the release record on. Every
        commit entry of the store is wrapped, so the test does not depend on which of them the record uses."""
        held = []

        def wrap(original):
            def commit(store, operation, payload, apply, **kwargs):
                if operation == "execution.prelaunch" and not held:
                    connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None)
                    connection.execute("BEGIN IMMEDIATE")
                    self.addCleanup(connection.close)
                    held.append(connection)
                return original(store, operation, payload, apply, **kwargs)
            return commit
        with contextlib.ExitStack() as patches:
            for name in COMMIT_ENTRIES:
                patches.enter_context(mock.patch.object(Store, name, wrap(getattr(Store, name))))
            patches.enter_context(mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01))
            patches.enter_context(mock.patch.object(finishing, "time", WaitlessTime()))
            with self.assertRaises(ResearchError) as caught:
                self.launch()
        held[0].execute("ROLLBACK")
        return caught.exception

    def test_same_request_starts_a_claimed_run_whose_start_the_store_refused(self):
        refusal = self.refuse_the_start()
        # The message is checked after the second launch, so that a release that spends the claim fails there.
        self.assertEqual(refusal.code, "store_busy")
        self.assertEqual(self.run_count(), 0)
        result = self.launch()
        self.assertTrue(result["ok"])
        self.assertEqual(self.run_count(), 1)
        self.assertEqual(self.launch(), result)
        self.assertEqual(self.run_count(), 1)
        self.assertEqual(refusal.message, execution.RUN_START_STORE_BUSY_MSG)

    def test_same_request_starts_a_claim_whose_launcher_ended_before_the_release(self):
        self.claim_run()
        result = self.launch()
        self.assertTrue(result["ok"])
        self.assertEqual(self.run_count(), 1)

    def test_stale_ready_notice_of_an_earlier_worker_does_not_stop_the_start(self):
        claim = self.claim_run()
        # The notice of a worker of the first launch that ended: its pid names no live process now.
        ended = subprocess.Popen([sys.executable, "-c", "pass"])
        ended.wait()
        json_projection(self.root, "research/runs/" + digest({"admission_id": self.admission["id"]}) + "/ready.json",
                        {"pid": ended.pid, "token": claim["token"], "config_sha256": claim["config_artifact"]["sha256"]})
        self.assertTrue(self.launch()["ok"])
        self.assertEqual(self.run_count(), 1)

    def test_another_request_never_starts_a_claimed_run(self):
        self.claim_run("first")
        self.assert_error("execution_recovery_required", lambda: self.launch("second"))
        self.assertEqual(self.run_count(), 0)

    def test_released_run_without_an_outcome_is_not_started_again(self):
        claim = self.claim_run()
        execution._prelaunch(self.store, self.admission["id"], claim, mock.Mock(poll=mock.Mock(return_value=None)))
        self.assert_error("execution_recovery_required", self.launch)
        self.assertEqual(self.run_count(), 0)

    def test_run_folder_with_a_stream_is_not_started_again(self):
        self.claim_run()
        (self.run_folder() / "stdout").write_bytes(b"")
        self.assert_error("execution_recovery_required", self.launch)
        self.assertEqual(self.run_count(), 0)

    def test_terminal_outcome_without_streams_is_not_started_again(self):
        self.claim_run()
        (self.run_folder() / "terminal.json").write_text("{}")
        # The reconciliation refuses the outcome; no program starts.
        with self.assertRaises(ResearchError):
            self.launch()
        self.assertEqual(self.run_count(), 0)

    def test_live_worker_refuses_a_second_start(self):
        self.claim_run()
        with execution._owner(self.root, self.admission["id"], worker=True):
            self.assert_error("execution_live", self.launch)
        self.assertEqual(self.run_count(), 0)

    def test_claim_recorded_as_interrupted_is_not_started(self):
        self.claim_run()
        summary = execution.reconcile_execution(self.store, {"admission_id": self.admission["id"], "resolution": "interrupted",
            "reason": "The launcher ended before the release."}, expected_revision=self.store.revision, request_id="interrupted")
        self.assertFalse(summary["ok"])
        self.assertFalse(self.launch()["ok"])
        self.assertEqual(self.run_count(), 0)

    def test_worker_that_ends_while_its_start_waits_leaves_the_claim_to_the_same_request(self):
        popen, prelaunch, workers = subprocess.Popen, execution._prelaunch, []

        def start_and_keep(argv, **options):
            # A local launch calls subprocess.Popen only to start its worker.
            workers.append(popen(argv, **options))
            return workers[-1]

        def end_the_worker_then_prelaunch(*args):
            # The worker is ready and waits for its token when it ends, as when it is killed.
            workers[-1].kill()
            workers[-1].wait()
            return prelaunch(*args)
        with mock.patch.object(subprocess, "Popen", side_effect=start_and_keep), \
                mock.patch.object(execution, "_prelaunch", side_effect=end_the_worker_then_prelaunch):
            with self.assertRaises(ResearchError) as caught:
                self.launch()
        refusal = caught.exception
        self.assertEqual((refusal.code, refusal.message), ("execution_recovery_required", execution.RUN_WORKER_ENDED_MSG))
        self.assertEqual(refusal.details, {"admission_id": self.admission["id"], "request_id": "launch"})
        self.assertNotIn(self.admission["id"], self.store.snapshot()["records"].get("execution_prelaunch", {}))
        self.assertEqual(self.run_count(), 0)
        # The way on that the message names.
        self.assertTrue(self.launch()["ok"])
        self.assertEqual(self.run_count(), 1)

    def test_store_refusal_of_the_start_stands_while_the_worker_ends(self):
        wait = subprocess.Popen.wait

        def end_after_the_exit_wait(worker, timeout=None):
            # The worker, which has no token, ends on its closed input after the exit wait of the launch expired.
            if timeout == 6:
                wait(worker)
                raise subprocess.TimeoutExpired(worker.args, timeout)
            return wait(worker, timeout)
        with mock.patch.object(subprocess.Popen, "poll", autospec=True, return_value=None), \
                mock.patch.object(subprocess.Popen, "wait", autospec=True, side_effect=end_after_the_exit_wait):
            refusal = self.refuse_the_start()
        self.assertEqual((refusal.code, refusal.message), ("store_busy", execution.RUN_START_STORE_BUSY_MSG))
        self.assertEqual(self.run_count(), 0)

    def test_second_start_keeps_the_log_of_the_first_worker(self):
        self.refuse_the_start()
        log = self.run_folder() / "launcher.log"
        log.write_bytes(log.read_bytes() + b"Written by the first worker.\n")
        self.assertTrue(self.launch()["ok"])
        self.assertIn(b"Written by the first worker.\n", log.read_bytes())
