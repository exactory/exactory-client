"""Probe intent, reservation, ownership and reconciliation contracts."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from literature_fixtures import LiteratureCase
from research_harness import cli, resources
from research_harness.principles import initialize_research


def build_probe_intent(artifacts, identifier="probe-1", timeout=2):
    return {"id": identifier, "question": "What does the finite example establish?", "interpreter": "python",
            "program": artifacts.put(b"print(4)\n", "text/x-python"), "inputs": [], "timeout_seconds": timeout}


def build_probe_outcome(artifacts, identifier="probe-1", elapsed=0.25, exit_status=0):
    return {"id": identifier, "command": "python example.py", "outputs": [],
            "stdout": artifacts.put(b"4\n", "text/plain"), "stderr": artifacts.put(b"", "text/plain"),
            "exit_status": exit_status, "elapsed_seconds": elapsed, "observation": "The finite result was 4."}


class ProbeLifecycleTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.mutate(initialize_research, {"profile": "research", "target": None})

    def record_intent(self, value=None):
        self.assertIn("probe-intent", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["probe-intent"], value or build_probe_intent(self.artifacts))

    def record_outcome(self, value=None):
        return self.mutate(cli.OPERATIONS["probe"], value or build_probe_outcome(self.artifacts))

    def find_usage(self):
        return resources.account_report(self.store.snapshot()["records"], "research")["experiment"]["wall_seconds"]

    def test_intent_before_target_reserves_without_charging_and_outcome_releases(self):
        receipt = self.record_intent()
        records = self.store.snapshot()["records"]
        self.assertIsNone(records["configuration"]["research"]["target"])
        self.assertFalse(records.get("cycle"))
        self.assertEqual(records["probe_intent"]["probe-1"], receipt["result"])
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (2, 0))
        self.record_outcome()
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (0, 0.25))

    def test_budget_counts_other_intents_and_refuses_before_launch(self):
        self.mutate(resources.set_budget, {"profile": "research", "purpose": "experiment", "reason": "Bound exploratory time.",
                    "limits": {unit: 3 if unit == "wall_seconds" else None for unit in resources.UNITS}})
        self.record_intent()
        before = self.store.snapshot()
        self.assert_error("resource_budget_exhausted", lambda: self.record_intent(build_probe_intent(self.artifacts, "second")))
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.find_usage()["reserved"], 2)

    def test_duplicate_intent_id_refuses_changed_payload_and_never_reserves_twice(self):
        value = build_probe_intent(self.artifacts)
        original = self.record_intent(value)
        self.assertEqual(self.record_intent(value)["result"], original["result"])
        self.assertEqual(self.find_usage()["reserved"], 2)
        for changed in (dict(value, question="Another question?"), dict(value, timeout_seconds=3)):
            self.assert_error("record_conflict", lambda: self.record_intent(changed))

    def test_outcome_requires_intent_and_replays_without_a_second_charge(self):
        self.assert_error("probe_intent_missing", self.record_outcome)
        self.record_intent()
        value = build_probe_outcome(self.artifacts)
        revision = self.store.revision
        receipt = self.record_outcome(value)
        request_id = "task3-" + str(self.sequence)
        self.assertEqual(cli.OPERATIONS["probe"](self.store, value, expected_revision=revision, request_id=request_id), receipt)
        self.assertEqual(self.find_usage()["charged"], 0.25)
        self.assert_error("record_conflict", lambda: self.record_outcome(dict(value, observation="Changed.")))

    def test_timed_out_failed_output_bound_and_nonzero_outcomes_charge_actual_usage(self):
        for index, status in enumerate(("timed_out", "failed", "output_bound", 7)):
            identifier = "outcome-" + str(index)
            self.record_intent(build_probe_intent(self.artifacts, identifier, timeout=0.1))
            self.record_outcome(build_probe_outcome(self.artifacts, identifier, exit_status=status))
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (0, 1))
        self.assertEqual(cli.status_report(self.store)["probes"], {"count": 4, "elapsed_seconds": 1})

    def test_invalid_outcome_does_not_release_reservation(self):
        self.record_intent()
        value = build_probe_outcome(self.artifacts)
        for key, invalids in (("elapsed_seconds", (-1, True, "1", None)), ("exit_status", (True, 1.5, "unknown", None))):
            for invalid in invalids:
                self.assert_error("invalid_input", lambda: self.record_outcome(dict(value, **{key: invalid})))
        bad = dict(value["stdout"], extra="unsupported")
        self.assert_error("invalid_input", lambda: self.record_outcome(dict(value, stdout=bad)))
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (2, 0))

    def test_legacy_status_omits_new_probe_keys(self):
        self.assertNotIn("probes", cli.status_report(self.store))

    def test_legacy_acquisition_reservations_do_not_add_account_report_fields(self):
        records = {"acquisition_operation": {"held": {"state": "admitted", "payload": {"max_requests": 3}}}}
        self.assertEqual(resources.held(records, "literature")["network_requests"], 3)
        self.assertEqual(resources.account_report(records, "research"), {})

    def test_interrupted_empty_stream_origins_precede_artifact_publication(self):
        from unittest import mock
        from research_harness.artifacts import ArtifactStore, describe_artifact
        from research_harness.probe import hold_probe_owner, reconcile_probe
        from research_harness.probe_provenance import find_probe_artifact_origins
        self.record_intent()
        intent = self.store.snapshot()["records"]["probe_intent"]["probe-1"]
        original = ArtifactStore.put
        roles = iter(("stdout", "stderr"))
        def check_origin_before_put(artifacts, data, media_type):
            if data == b"":
                role = next(roles)
                origins = find_probe_artifact_origins(self.store.snapshot()["records"], [describe_artifact(data, media_type)])
                self.assertTrue(any(origin["probe_id"] == "probe-1" and role in origin["roles"] for origin in origins))
            return original(artifacts, data, media_type)
        with hold_probe_owner(self.root, "probe-1"), mock.patch.object(ArtifactStore, "put", check_origin_before_put):
            result = reconcile_probe(self.store, intent, None)
        self.assertEqual(result["result"]["exit_status"], "interrupted")


class ProbeConfigurationTests(LiteratureCase):
    def test_intent_requires_configuration(self):
        self.assertIn("probe-intent", cli.OPERATIONS)
        self.assert_error("configuration_missing", lambda: self.mutate(cli.OPERATIONS["probe-intent"], build_probe_intent(self.artifacts)))


class ProbeSupervisionTests(unittest.TestCase):
    def patch_darwin_libproc(self, **functions):
        import ctypes
        from types import SimpleNamespace
        from unittest import mock
        from research_harness import probe_supervision
        self.addCleanup(ctypes.set_errno, ctypes.get_errno())
        for patcher in (mock.patch.object(probe_supervision.sys, "platform", "darwin"),
                        mock.patch.object(ctypes, "CDLL", return_value=SimpleNamespace(**functions))):
            patcher.start()
            self.addCleanup(patcher.stop)
        return probe_supervision

    def test_supervisor_startup_leaves_the_requested_time_for_the_program(self):
        from unittest import mock
        from research_harness import probe_worker
        from research_harness.probe_supervision import find_probe_process_identity
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "run/work"
            work.mkdir(parents=True)
            (work / "program.py").write_text('import time\nprint("started", flush=True)\ntime.sleep(10)\n')
            config = {"directory": "run", "argv": [sys.executable, "program.py"], "timeout_seconds": 0.2}
            spawn = subprocess.Popen
            def launch_after_preparation(*args, **kwargs):
                time.sleep(0.3)
                return spawn(*args, **kwargs)
            with (root / "owner.lock").open("wb") as owner:
                with mock.patch.object(probe_worker.subprocess, "Popen", side_effect=launch_after_preparation):
                    terminal = probe_worker.run_probe_process(root, config, owner.fileno(), find_probe_process_identity(os.getpid()), "fixture")
            self.assertEqual(terminal["exit_status"], "timed_out")
            self.assertEqual((root / "run/stdout").read_bytes(), b"started\n")
            self.assertGreaterEqual(terminal["elapsed_seconds"], 0.5)

    def test_deadline_clock_uses_the_shared_kernel_origin_on_python_39(self):
        from research_harness.probe_supervision import read_probe_clock
        before = time.clock_gettime(time.CLOCK_MONOTONIC)
        observed = read_probe_clock()
        self.assertLessEqual(before, observed)
        self.assertLessEqual(observed, time.clock_gettime(time.CLOCK_MONOTONIC))

    def test_process_identity_is_stable_and_names_the_current_group(self):
        from research_harness.probe_supervision import find_probe_process_identity
        identity = find_probe_process_identity(os.getpid())
        self.assertEqual(identity, find_probe_process_identity(os.getpid()))
        self.assertEqual(identity["pid"], os.getpid())
        self.assertEqual(identity["process_group"], os.getpgrp())
        self.assertTrue(identity["start_identity"])

    def test_group_members_include_the_current_process_identity(self):
        from research_harness.probe_supervision import find_probe_group_members, find_probe_process_identity
        self.assertIn(find_probe_process_identity(os.getpid()), find_probe_group_members(os.getpgrp()))

    def test_darwin_group_enumeration_raises_when_libproc_sets_errno(self):
        import ctypes
        import errno
        def list_group(group, buffer, size):
            ctypes.set_errno(errno.EPERM)
            return 0
        probe_supervision = self.patch_darwin_libproc(proc_listpgrppids=list_group)
        with self.assertRaises(OSError) as raised:
            probe_supervision.find_probe_group_members(10)
        self.assertEqual(raised.exception.errno, errno.EPERM)
        self.assertEqual(raised.exception.strerror, "Cannot enumerate the probe process group")
        self.assertEqual(raised.exception.filename, 10)

    def test_darwin_empty_group_returns_no_members_despite_stale_errno(self):
        import ctypes
        import errno
        def list_group(group, buffer, size):
            return 0
        probe_supervision = self.patch_darwin_libproc(proc_listpgrppids=list_group)
        for error in (0, errno.EPERM):
            with self.subTest(errno=error):
                ctypes.set_errno(error)
                self.assertEqual(probe_supervision.find_probe_group_members(10), [])

    def test_darwin_group_enumeration_grows_full_buffers_and_returns_every_member(self):
        import ctypes
        from unittest import mock
        members = [{"pid": pid, "process_group": 10, "start_identity": [pid]} for pid in range(100, 170)]
        identities = {member["pid"]: member for member in members}
        capacities = []
        def list_group(group, buffer, size):
            self.assertEqual(group, 10)
            self.assertEqual(size, ctypes.sizeof(buffer))
            capacities.append(len(buffer))
            for index, member in enumerate(members[:len(buffer)]):
                buffer[index] = member["pid"]
            return min(len(members), len(buffer))
        probe_supervision = self.patch_darwin_libproc(proc_listpgrppids=list_group)
        with mock.patch.object(probe_supervision, "find_probe_process_identity", side_effect=identities.__getitem__):
            self.assertEqual(probe_supervision.find_probe_group_members(10), members)
        self.assertEqual(capacities, [32, 64, 128])

    def test_darwin_process_identity_raises_permission_error_despite_stale_esrch(self):
        import ctypes
        import errno
        def read_identity(pid, flavor, arg, buffer, size):
            ctypes.set_errno(errno.EPERM)
            return 0
        probe_supervision = self.patch_darwin_libproc(proc_pidinfo=read_identity)
        ctypes.set_errno(errno.ESRCH)
        with self.assertRaises(OSError) as raised:
            probe_supervision.find_probe_process_identity(10)
        self.assertEqual(raised.exception.errno, errno.EPERM)

    def test_darwin_process_identity_raises_io_error_for_short_result_despite_stale_esrch(self):
        import ctypes
        import errno
        def read_identity(pid, flavor, arg, buffer, size):
            return size - 1
        probe_supervision = self.patch_darwin_libproc(proc_pidinfo=read_identity)
        ctypes.set_errno(errno.ESRCH)
        with self.assertRaises(OSError) as raised:
            probe_supervision.find_probe_process_identity(10)
        self.assertEqual(raised.exception.errno, errno.EIO)

    def test_retry_cleanup_retains_reservation_until_all_group_members_exit(self):
        from types import SimpleNamespace
        from unittest import mock
        from research_harness import probe
        program = {"pid": 10, "process_group": 10, "start_identity": [1]}
        supervision = {"config_sha256": "frozen", "program": program,
                       "worker": {"pid": 11, "start_identity": [2]}, "started": 0, "deadline": 1}
        termination = {key: supervision[key] for key in ("config_sha256", "program", "started", "deadline")}
        termination["elapsed_seconds"] = 2
        documents = {"supervision.json": supervision, "termination.json": termination}
        launch = {"directory": "run", "config_artifact": {"sha256": "frozen"}}
        store = SimpleNamespace(root=Path("."))
        with mock.patch.object(probe, "read_probe_file", side_effect=lambda store, path: documents.get(Path(path).name)):
            with mock.patch.object(probe, "find_probe_process_identity", return_value=None):
                with mock.patch.object(probe, "find_probe_group_members", return_value=[{"pid": 12}]):
                    with mock.patch.object(probe, "read_probe_clock", return_value=3), mock.patch.object(probe.time, "monotonic", side_effect=(0, 3)):
                        with mock.patch.object(probe, "finish_probe_timeout") as finish:
                            result = probe.recover_expired_probe(store, {"id": "held", "timeout_seconds": 1}, launch, 3)
        self.assertEqual(result["status"], "pending")
        finish.assert_not_called()

    def test_program_gate_does_not_execute_when_worker_disappears_before_release(self):
        descriptor, release = os.pipe()
        ready, announce = os.pipe()
        try:
            bootstrap = Path(__file__).resolve().parents[1] / "research_harness/probe_supervision.py"
            child = subprocess.Popen([sys.executable, str(bootstrap), str(descriptor), str(announce), "0", ".", "unused", "unused", sys.executable,
                                      "-c", 'print("must not execute")'],
                                     pass_fds=(descriptor, announce), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        finally:
            os.close(descriptor)
            os.close(announce)
        try:
            self.assertEqual(os.read(ready, 1), b"1")
        finally:
            os.close(ready)
            os.close(release)
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual(stdout, b"")
