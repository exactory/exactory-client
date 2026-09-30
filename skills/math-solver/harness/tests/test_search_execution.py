"""Metered entry reservations and real bounded process execution."""

import contextlib
import errno
import json
import sys
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

from search_controller.errors import SearchError
from tests.support import WorkspaceTest, make_move
from tests.search_execution_support import admit_workspace, begin_spec, invoke, command_spec, review_native_inputs


NEVER_STARTED_RUN_RECORDED_ERR_MSG = ("The launcher did not start, so no command ran. The run is recorded as never "
                                      "started. Remove the cause given in the details, then run search next.")


class SearchExecutionTests(WorkspaceTest):
    def test_problem_progress_requires_reassessment_before_the_next_pass(self):
        invoke(self.controller, "begin", begin_spec())
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))[0], 0)
        invoke(self.controller, "begin", begin_spec())
        problem = self.read_json("problem.json")
        problem["shape"]["objects"] = "The same exact claim with a refined object description"
        self.write_json("problem.json", problem)
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(2, problem_changed=True)))[0], 0)
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", dict(begin_spec(), **{"pass": 2}))
        self.assertEqual(caught.exception.code, "strategy_reassessment_required")
        self.assertEqual(self.controller.status()["control"]["pending_moves"], [])

    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)

    def test_begin_reserves_move_and_blocks_an_unjournalled_next_entry(self):
        invoke(self.controller, "begin", begin_spec())
        state = self.controller.status()
        self.assertEqual(state["totals"]["reserved_moves"], 1)
        self.assertEqual(state["totals"]["used_moves"], 0)
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", begin_spec())
        self.assertEqual(caught.exception.code, "recovery_required")

    def test_journal_acknowledges_exact_reserved_legacy_bytes_once(self):
        invoke(self.controller, "begin", begin_spec())
        status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual((status, err), (0, ""))
        state = self.controller.status()
        self.assertEqual(state["totals"]["used_moves"], 1)
        self.assertEqual(state["totals"]["reserved_moves"], 0)
        receipt = state["service"]["journal_receipts"]["node-000001:1"]
        self.assertEqual(self.controller.store.get_artifact(receipt["journal_prefix_digest"]),
                         (self.workspace / "journal.jsonl").read_bytes())
        self.assertEqual(json.loads((self.workspace / "journal.jsonl").read_text())["move"], 1)
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)

    def test_begin_rejects_an_unstudied_strategy_before_reserving(self):
        spec = begin_spec()
        spec["strategy"] = "ladder-the-parameter"
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", spec)
        self.assertEqual(caught.exception.code, "admission_required")
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)

    def test_real_command_runs_frozen_bytes_and_replay_never_reruns(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import os\nprint(os.getcwd())\nprint('frozen input')\n")
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        revision = self.controller.status()["revision"]
        try:
            receipt = self.controller.command("run", spec, revision, "one-job", "node-000001")
        except SearchError as error:
            self.fail("A reserved command must execute: " + error.code)
        state = self.controller.status()
        run = state["runs"]["run-000001"]
        self.assertEqual(run["status"], "terminal")
        self.assertEqual(state["totals"]["used_runs"], 1)
        self.assertEqual(state["totals"]["reserved_runs"], 0)
        self.assertNotEqual(run["cwd"], str(step.resolve()))
        result = self.controller.store.get_blob(run["result_digest"])
        output = self.controller.store.get_artifact(result["commands"][0]["stdout_digest"]).decode()
        self.assertEqual(output.splitlines(), [run["cwd"], "frozen input"])
        self.assertEqual(result["kind"], "command")
        (step / "job.py").write_text("raise RuntimeError('must never rerun')\n")
        self.assertEqual(self.controller.command("run", spec, revision, "one-job", "node-000001"), receipt)
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 1)

    def test_timeout_is_charged_and_bounded(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import time\ntime.sleep(20)\n")
        invoke(self.controller, "begin", begin_spec())
        try:
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 1))
        except SearchError as error:
            self.fail("A timeout must be recorded: " + error.code)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual(run["status"], "terminal")
        self.assertEqual(run["termination"], "timeout")
        self.assertEqual(run["charged_units"], 1)

    @contextlib.contextmanager
    def start_launcher_after(self, command):
        """Start the run's launcher through a shell script that runs `command` and then execs the launcher's argv.

        The patch replaces subprocess.Popen and changes only the call that starts the launcher. It yields the list of the
        launchers it started."""
        wrapper = Path(self._tmp.name) / "start-launcher"
        wrapper.write_text('#!/bin/sh\n' + command + '\nexec "$@"\n')
        wrapper.chmod(0o755)
        popen = subprocess.Popen
        launchers = []

        def start_through_wrapper(argv, **options):
            if "--launcher" not in argv:
                return popen(argv, **options)
            launchers.append(popen([str(wrapper)] + argv, **options))
            return launchers[-1]

        try:
            with patch.object(subprocess, "Popen", side_effect=start_through_wrapper):
                yield launchers
        finally:
            # A launcher that outlives its test stops before tearDown removes the workspace.
            for launcher in launchers:
                launcher.kill()
                launcher.wait()

    def test_launcher_that_starts_after_the_run_timeout_runs_its_command(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('late launcher')\n")
        invoke(self.controller, "begin", begin_spec())
        # A loaded machine starts the launcher slowly: it becomes ready after the run's timeout of 10 seconds and after
        # the 5 seconds that earlier releases waited, and within the 10 seconds that the wait adds to the run's timeout.
        with self.start_launcher_after("sleep 11"):
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 10))
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"]), ("terminal", "exit"))
        result = self.controller.store.get_blob(run["result_digest"])
        self.assertEqual(self.controller.store.get_artifact(result["commands"][0]["stdout_digest"]), b"late launcher\n")

    def test_launcher_that_exits_before_it_is_ready_leaves_its_run_to_recovery(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('never runs')\n")
        invoke(self.controller, "begin", begin_spec())
        started = time.monotonic()
        # The script exits before it starts the launcher, so ready.json is never written.
        with self.start_launcher_after("exit 3"), self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 60))
        # The wait ends when the launcher exits, long before the run's timeout.
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("recovery_required", "Launcher did not establish its identity"))
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["started_units"], 0)

    def test_launcher_that_stays_alive_and_not_ready_ends_the_wait_at_its_bound(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('never runs')\n")
        invoke(self.controller, "begin", begin_spec())
        started = time.monotonic()
        # The launcher process stays alive for 60 seconds and never writes ready.json.
        with self.start_launcher_after("exec sleep 60"), self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 1))
        # The wait ends at the run's timeout plus 10 seconds, and the exit waits after it end while the launcher lives.
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual(caught.exception.code, "recovery_required")
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["started_units"], 0)

    def test_launcher_that_ends_after_the_wait_for_its_run_gives_the_run_outcome(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('ended after the wait')\n")
        invoke(self.controller, "begin", begin_spec())
        wait = subprocess.Popen.wait

        def expire_the_wait_for_the_run(launcher, timeout):
            # Only the wait for the run is longer than the 5-second exit wait. It expires at once, as when the launcher's
            # checks after its commands take longer than the 10 seconds that the wait adds, and the launcher then ends
            # within the exit wait.
            if timeout > 5:
                raise subprocess.TimeoutExpired(launcher.args, timeout)
            return wait(launcher)

        # The run's timeout is long enough for a loaded machine to start the command. The command ends at once.
        with patch.object(subprocess.Popen, "wait", autospec=True, side_effect=expire_the_wait_for_the_run):
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 30))
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"]), ("terminal", "exit"))
        result = self.controller.store.get_blob(run["result_digest"])
        self.assertEqual(self.controller.store.get_artifact(result["commands"][0]["stdout_digest"]), b"ended after the wait\n")

    def test_launcher_still_live_after_the_wait_for_its_run_leaves_the_outcome_to_reconcile(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import time\ntime.sleep(5)\nprint('ended after the launch')\n")
        invoke(self.controller, "begin", begin_spec())
        launchers = []

        def expire_each_wait(launcher, timeout):
            # The wait for the run and the exit wait both expire while the command still sleeps.
            launchers.append(launcher)
            raise subprocess.TimeoutExpired(launcher.args, timeout)

        with patch.object(subprocess.Popen, "wait", autospec=True, side_effect=expire_each_wait), \
                self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 30))
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("recovery_required", "The launcher is still live. Run search reconcile after the launcher ends."))
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["status"], "launched")
        # Once the launcher ends, reconciliation records the outcome of its run.
        launchers[0].wait()
        invoke(self.controller, "reconcile", {}, None)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"]), ("terminal", "exit"))
        result = self.controller.store.get_blob(run["result_digest"])
        self.assertEqual(self.controller.store.get_artifact(result["commands"][0]["stdout_digest"]), b"ended after the launch\n")

    def test_launcher_still_live_after_its_launch_is_refused_leaves_its_run_to_reconcile(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('never runs')\n")
        invoke(self.controller, "begin", begin_spec())
        wait = subprocess.Popen.wait
        wait_timeouts = []

        def expire_the_first_wait(launcher, timeout=None):
            # The refused launch closes the launcher's input and waits 5 seconds for it to end. The launcher is still in
            # its delayed start when that wait expires. A later wait waits for its real end.
            wait_timeouts.append(timeout)
            if len(wait_timeouts) == 1:
                raise subprocess.TimeoutExpired(launcher.args, timeout)
            return wait(launcher)

        # The launcher starts after the readiness wait of the run's timeout plus 10 seconds, so the launch is refused.
        with patch.object(subprocess.Popen, "wait", autospec=True, side_effect=expire_the_first_wait), \
                self.start_launcher_after("sleep 13") as launchers:
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 1))
            # The launch waits once for the launcher to end and reports what that wait observed.
            self.assertEqual(wait_timeouts, [5])
            launchers[0].wait()
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("recovery_required", "The launcher is still live. Run search reconcile after the launcher ends."))
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["started_units"]), ("reserved", 0))
        # The launcher read the end of its input and recorded that it started no command, and reconciliation records that.
        invoke(self.controller, "reconcile", {}, None)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"], run["charged_units"]), ("terminal", "never_started", 0))

    def test_launcher_still_live_after_its_launch_is_refused_names_the_refusal(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('never runs')\n")
        invoke(self.controller, "begin", begin_spec())
        wait = subprocess.Popen.wait
        wait_timeouts = []

        def expire_the_first_wait(launcher, timeout=None):
            # The exit wait after the refusal expires while the launcher still runs. A later wait waits for its real end.
            wait_timeouts.append(timeout)
            if len(wait_timeouts) == 1:
                raise subprocess.TimeoutExpired(launcher.args, timeout)
            return wait(launcher)

        # The script runs the launcher as its child, so the launcher's process ID is not the one that the launch started,
        # and the launch refuses it with "Unexpected launcher identity".
        with patch.object(subprocess.Popen, "wait", autospec=True, side_effect=expire_the_first_wait), \
                self.start_launcher_after('"$@"; exit $?') as launchers:
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 30))
            launchers[0].wait()
        self.assertEqual((caught.exception.code, caught.exception.message, caught.exception.details),
                         ("recovery_required", "The launcher is still live. Run search reconcile after the launcher ends.",
                          {"refusal": {"code": "recovery_conflict", "message": "Unexpected launcher identity"}}))
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["status"], "reserved")
        # The launcher read the end of its input and recorded that it started no command, and reconciliation records that.
        invoke(self.controller, "reconcile", {}, None)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"], run["charged_units"]), ("terminal", "never_started", 0))

    def begin_a_move_for_a_job(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('never runs')\n")
        invoke(self.controller, "begin", begin_spec())
        return command_spec(self.workspace, [sys.executable, "job.py"])

    def test_launcher_that_cannot_start_records_its_run_as_never_started(self):
        spec = self.begin_a_move_for_a_job()
        # At the limit of the user's processes, fork fails with EAGAIN. The run calls subprocess.Popen only for its launcher.
        fork_error = BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        with patch.object(subprocess, "Popen", side_effect=fork_error), self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", spec)
        self.assertEqual((caught.exception.code, caught.exception.message, caught.exception.details),
                         ("recovery_required", NEVER_STARTED_RUN_RECORDED_ERR_MSG, {"start_error": str(fork_error)}))
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"], run["started_units"], run["charged_units"]),
                         ("terminal", "never_started", 0, 0))

    def test_run_directory_that_cannot_be_written_records_its_run_as_never_started(self):
        spec = self.begin_a_move_for_a_job()
        write_bytes = Path.write_bytes
        # A full disk refuses the copies of the run's inputs with ENOSPC, and the launcher never starts.
        disk_full_error = OSError(errno.ENOSPC, "No space left on device")

        def refuse_run_input_copies(path, data):
            if "/.search/runs/" in str(path):
                raise disk_full_error
            return write_bytes(path, data)

        with patch.object(Path, "write_bytes", autospec=True, side_effect=refuse_run_input_copies), \
                self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", spec)
        self.assertEqual((caught.exception.code, caught.exception.message, caught.exception.details),
                         ("recovery_required", NEVER_STARTED_RUN_RECORDED_ERR_MSG, {"start_error": str(disk_full_error)}))
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"], run["started_units"], run["charged_units"]),
                         ("terminal", "never_started", 0, 0))

    def test_run_that_cannot_be_recorded_as_never_started_stays_reserved(self):
        spec = self.begin_a_move_for_a_job()
        path_open = Path.open
        # A full disk refuses every file written in the run's directory, the record of the never started run included.
        disk_full_error = OSError(errno.ENOSPC, "No space left on device")

        def refuse_run_directory_writes(path, mode="r", *args, **kwargs):
            if "/.search/runs/" in str(path) and set(mode) & set("wxa"):
                raise disk_full_error
            return path_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", autospec=True, side_effect=refuse_run_directory_writes), \
                self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", spec)
        self.assertEqual((caught.exception.code, caught.exception.message, caught.exception.details),
                         ("recovery_required", "The launcher did not start, so no command ran. The run stays reserved because "
                          "writing its never-started record failed. Remove the causes given in the details, then run search "
                          "reconcile. It records the run as never started if that record exists, and otherwise as indeterminate "
                          "with its reserved units charged.",
                          {"start_error": str(disk_full_error), "record_error": str(disk_full_error)}))
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["status"], "reserved")
        # As the message states, reconciliation cannot tell that no command ran.
        invoke(self.controller, "reconcile", {}, None)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"], run["charged_units"]), ("indeterminate", "indeterminate", 1))

    def test_run_whose_record_cannot_be_reconciled_is_left_to_search_reconcile(self):
        from search_controller import execution
        spec = self.begin_a_move_for_a_job()
        fork_error = BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        disk_full_error = OSError(errno.ENOSPC, "No space left on device")
        # The record of the never started run is written, and then the disk fills before reconciliation records it.
        with patch.object(subprocess, "Popen", side_effect=fork_error), \
                patch.object(execution, "reconcile_runs", side_effect=disk_full_error), \
                self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", spec)
        self.assertEqual((caught.exception.code, caught.exception.message, caught.exception.details),
                         ("recovery_required", "The launcher did not start, so no command ran, and the reconciliation of its "
                          "never-started run did not complete. Remove the causes given in the details, then run search reconcile.",
                          {"start_error": str(fork_error), "reconcile_error": str(disk_full_error)}))
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["status"], "reserved")
        invoke(self.controller, "reconcile", {}, None)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"], run["charged_units"]), ("terminal", "never_started", 0))

    def test_run_directory_that_already_exists_gets_no_record_from_the_launch(self):
        spec = self.begin_a_move_for_a_job()
        # A run directory that existed before the launch, as after a restored store reuses a run ID, can hold another
        # launcher's records. The launch leaves it unchanged.
        directory = self.attack_root / ".search" / "runs" / "run-000001"
        directory.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            invoke(self.controller, "run", spec)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["status"], "reserved")

    def test_launcher_that_ends_before_its_token_records_its_run_as_indeterminate(self):
        from search_controller import integration
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        marker = self.attack_root / "command-ran"
        (step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        invoke(self.controller, "begin", begin_spec())
        popen = subprocess.Popen
        original = integration.internal_operation
        launchers = []

        def start_and_keep(argv, **options):
            # In the test process, the run calls subprocess.Popen only to start its launcher.
            launchers.append(popen(argv, **options))
            return launchers[-1]

        def end_the_launcher_then_record(controller, command, request_id, build):
            if command == "execution-launch":
                # The launcher is ready and waits for its token when it ends, as when it is killed.
                launchers[0].kill()
                launchers[0].wait()
            return original(controller, command, request_id, build)

        with patch.object(subprocess, "Popen", side_effect=start_and_keep), \
                patch.object(integration, "internal_operation", side_effect=end_the_launcher_then_record):
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"], 30))
        # As for any launcher that ended without its terminal record, reconciliation cannot tell whether a command
        # started, so the run is indeterminate and keeps its reserved unit charged.
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["termination"], run["started_units"], run["charged_units"]),
                         ("indeterminate", "indeterminate", 0, 1))
        self.assertFalse(marker.exists())

    def test_command_that_changes_its_frozen_input_cannot_verify(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("from pathlib import Path\nPath('job.py').write_text('changed')\n")
        invoke(self.controller, "begin", begin_spec())
        try:
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, "job.py"]))
        except SearchError as error:
            self.fail("Input mutation must produce a charged terminal error: " + error.code)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual(run["termination"], "input_changed")
        self.assertEqual(run["charged_units"], 1)
        self.assertIn("Path('job.py')", (step / "job.py").read_text())

    def test_legacy_certificate_uses_the_reserved_frozen_executor(self):
        step = self.workspace / "deterministic" / "check-1"
        step.mkdir()
        (step / "check.sh").write_text("#!/bin/sh\nprintf checked\n")
        (step / "check.sh").chmod(0o755)
        (step / "certificate.txt").write_text("True.intro\n")
        invoke(self.controller, "begin", begin_spec())
        review_native_inputs(self.controller, self.slug, "check-1")
        status, out, err = self.run_cli("verify", "certificate", self.slug, "check-1")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 1)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertNotEqual(run["cwd"], str(step.resolve()))
        self.assertEqual(json.loads((step / "result.json").read_text())["status"], "pass")

    def test_nonzero_axiom_inspection_cannot_pass_with_plausible_output(self):
        step = self.workspace / "deterministic" / "formal"
        step.mkdir()
        (step / "Main.lean").write_text("theorem exact_decl : True := True.intro\n")
        (step / "lakefile.toml").write_text('name = "fixture"\n')
        (step / "lean-toolchain").write_text("fixture-version\n")
        (step / "step.json").write_text(json.dumps({"theorem": "exact_decl", "requested_type": "True"}))
        fake_bin = self.attack_root / "tools"
        fake_bin.mkdir()
        lake = fake_bin / "lake"
        lake.write_text("#!/bin/sh\nif [ \"$1\" = build ]; then exit 0; fi\nprintf \"'exact_decl' depends on axioms: []\\n\"\nexit 1\n")
        lake.chmod(0o755)
        from unittest import mock
        invoke(self.controller, "begin", begin_spec())
        with mock.patch.dict(os.environ, {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}):
            review_native_inputs(self.controller, self.slug, "formal", "lean")
            status, out, err = self.run_cli("verify", "lean", self.slug, "formal")
        self.assertEqual((status, err), (1, ""))
        self.assertEqual(json.loads((step / "result.json").read_text())["status"], "fail")
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 2)

    def test_zero_exit_with_mutated_checker_cannot_gain_certificate_acceptance(self):
        from search_controller.evidence import audit_verification
        from tests.search_fixtures import provenance
        step = self.workspace / "deterministic" / "check-1"
        step.mkdir()
        (step / "check.sh").write_text("#!/bin/sh\nprintf changed > check.sh\n")
        (step / "check.sh").chmod(0o755)
        (step / "certificate.txt").write_text("True.intro\n")
        invoke(self.controller, "begin", begin_spec())
        review_native_inputs(self.controller, self.slug, "check-1")
        self.run_cli("verify", "certificate", self.slug, "check-1")
        state = self.controller.status()
        run = state["runs"]["run-000001"]
        frozen = self.controller.store.get_blob(run["input_digest"])
        review = {"subject_digest": run["result_digest"], "claim_digest": frozen["claim_digest"], "reviewer": provenance("reviewer"),
                  "decision": "approve", "findings": {key: "Reviewed checker and exact proposition" for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}
        manifest = dict(frozen, kind="certificate", verification={"run_id": run["id"], "result_digest": run["result_digest"],
                        "policy_review": review, "requested_declaration": None, "requested_type_digest": None})
        with self.assertRaises(SearchError) as caught:
            audit_verification(state, manifest, self.controller.store)
        self.assertEqual(caught.exception.code, "verification_failed")

    def test_interrupted_journal_intent_recovers_the_exact_append_once(self):
        import attack
        from unittest import mock
        invoke(self.controller, "begin", begin_spec())
        with mock.patch.object(attack, "run_journal_add", side_effect=OSError("interrupted before append")):
            with self.assertRaises(OSError):
                self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), b"")
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["totals"]["used_moves"], 1)
        original = (self.workspace / "journal.jsonl").read_bytes()
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual((self.workspace / "journal.jsonl").read_bytes(), original)

    def test_literature_finish_refuses_an_open_native_child(self):
        from tests.support import admit_native_child
        admit_native_child(self.controller, "child", self.slug)
        status, out, err = self.run_cli("finish", self.slug)
        self.assertNotEqual(status, 0)
        self.assertIn("a parent finishes after its children", err)
        self.assertFalse((self.workspace / "units/FINISHED.json").exists())

    def test_finish_cannot_hide_an_unjournalled_reserved_move(self):
        invoke(self.controller, "begin", begin_spec())
        status, out, err = self.run_cli("finish", self.slug)
        self.assertNotEqual(status, 0)
        self.assertIn("recovery_required", err)
        self.assertFalse((self.workspace / "units/FINISHED.json").exists())

    def test_finished_native_record_updates_controller_local_stage(self):
        status, out, err = self.run_cli("finish", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(self.controller.status()["nodes"]["node-000001"]["status"], "finished")
        self.assertEqual(self.controller.status()["proof_status"], "open")

    def test_declared_result_output_is_captured_with_its_exact_run(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("import os\nfrom pathlib import Path\nPath(os.environ['EXACTORY_OUTPUT_DIR'], 'answer.txt').write_text('the exact output')\n")
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        spec["expected_outputs"] = ["answer.txt"]
        invoke(self.controller, "run", spec)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertIn("outputs", run)
        self.assertEqual(run["outputs"][0]["path"], "answer.txt")
        self.assertEqual(self.controller.store.get_artifact(run["outputs"][0]["digest"]), b"the exact output")

    def test_missing_declared_output_is_a_terminal_execution_failure(self):
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('no certificate was produced')\n")
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        spec["expected_outputs"] = ["answer.txt"]
        invoke(self.controller, "run", spec)
        self.assertEqual(self.controller.status()["runs"]["run-000001"]["termination"], "missing_output")

    def test_native_write_without_acknowledgement_blocks_conflicting_recovery(self):
        from unittest import mock
        from search_controller import integration
        with mock.patch.object(integration, "after_legacy", side_effect=OSError("crash before success acknowledgement")):
            with self.assertRaises(OSError):
                self.run_cli("finish", self.slug)
        finished = (self.workspace / "units/FINISHED.json").read_bytes()
        before = self.controller.status()
        identity, intent = next(iter(before["service"]["native_intents"].items()))
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(caught.exception.code, "recovery_conflict")
        self.assertIsInstance(caught.exception.details, dict)
        self.assertEqual(caught.exception.details["intent_id"], identity)
        self.assertEqual(caught.exception.details["original_command"], "finish")
        self.assertEqual(caught.exception.details["original_args"], self.controller.store.get_blob(intent["args_digest"]))
        self.assertEqual(caught.exception.details["conflicting_paths"], [str(self.workspace / "units/FINISHED.json")])
        self.assertIn("Operator handoff required", caught.exception.message)
        self.assertIn("no supported replay or automatic overwrite", caught.exception.message)
        self.assertEqual(self.controller.status(), before)
        self.assertEqual((self.workspace / "units/FINISHED.json").read_bytes(), finished)

    def test_native_crash_before_any_write_can_reconcile_unchanged_inputs(self):
        import attack
        from unittest import mock
        with mock.patch.object(attack, "run_finish", side_effect=OSError("crash before finish write")):
            with self.assertRaises(OSError):
                self.run_cli("finish", self.slug)
        self.assertIn("native_intents", self.controller.status()["service"])
        self.assertEqual(len(self.controller.status()["service"]["native_intents"]), 1)
        from search_controller.scheduler import next_action
        self.assertEqual(next_action(self.controller.status())["kind"], "blocked")
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "complete", {}, None)
        self.assertEqual(caught.exception.code, "recovery_required")
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["service"]["native_intents"], {})
        self.assertFalse((self.workspace / "units/FINISHED.json").exists())

    def test_failed_native_validation_releases_only_an_unchanged_intent(self):
        (self.workspace / "ranking.json").unlink()
        status, out, err = self.run_cli("rank", self.slug)
        self.assertEqual(status, 1)
        self.assertIn("ranking.json", err)
        self.assertEqual(self.controller.status()["service"]["native_intents"], {})

    def test_known_failed_check_records_stamp_removal_without_claiming_success(self):
        directory = self.workspace / "units/1"
        directory.mkdir()
        (self.workspace / "units/INVENTORY.md").write_text("Inventory\n")
        (directory / "check-unit.json").write_text("stale stamp\n")
        status, out, err = self.run_cli("check-unit", self.slug, "1")
        self.assertEqual(status, 1)
        self.assertIn("unit.json", err)
        self.assertFalse((directory / "check-unit.json").exists())
        state = self.controller.status()
        self.assertEqual(state["service"]["native_intents"], {})
        failed = [receipt for receipt in state["service"]["native_receipts"].values() if receipt["outcome"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("unit.json", "\n".join(failed[0]["diagnostics"]))
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["service"]["native_receipts"], state["service"]["native_receipts"])


class AnalyticalAdmissionExecutionTests(WorkspaceTest):
    def test_plain_proof_admission_does_not_authorize_generic_finite_sampling(self):
        controller = admit_workspace(self.attack_root)
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        marker = self.attack_root / "sampling-ran"
        (step / "sample.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        invoke(controller, "begin", begin_spec())
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", command_spec(self.workspace, [sys.executable, "sample.py"]))
        self.assertEqual(caught.exception.code, "admission_required")
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["totals"]["used_runs"], 0)

    def test_native_tag_without_independent_input_review_never_launches(self):
        controller = admit_workspace(self.attack_root)
        step = self.workspace / "deterministic" / "sampling"
        step.mkdir()
        marker = self.attack_root / "sampling-ran"
        (step / "check.sh").write_text("#!/bin/sh\nprintf sampling > '" + str(marker) + "'\n")
        (step / "check.sh").chmod(0o755)
        invoke(controller, "begin", begin_spec())
        status, out, err = self.run_cli("verify", "certificate", self.slug, "sampling")
        self.assertNotEqual(status, 0)
        self.assertIn("input_review_required", err)
        self.assertFalse(marker.exists())
        self.assertEqual(controller.status()["totals"]["used_runs"], 0)


class ExecutionRecoveryTests(WorkspaceTest):
    def test_executor_death_leaves_live_launcher_owned_and_never_reruns(self):
        from tests.test_search_cli import CLI, INSTALLED_BIN
        controller = admit_workspace(self.attack_root, computational=True)
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        marker = self.attack_root / "launch-count"
        (step / "job.py").write_text("import time\nfrom pathlib import Path\nwith Path(" + repr(str(marker)) + ").open('a') as f: f.write('one\\n')\ntime.sleep(1)\n")
        invoke(controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"], 3)
        spec_path = self.attack_root / "run-spec.json"
        spec_path.write_text(json.dumps(spec))
        revision = controller.status()["revision"]
        environment = dict(os.environ)
        environment["PATH"] = INSTALLED_BIN + os.pathsep + environment["PATH"]
        process = subprocess.Popen([sys.executable, str(CLI), "--attack-root", str(self.attack_root), "search", "run", "node-000001",
            "--spec", str(spec_path), "--expected-revision", str(revision), "--request-id", "crash-run", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        deadline = time.monotonic() + 4
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), process.communicate(timeout=1) if process.poll() is not None else "workload did not start")
        process.kill()
        process.communicate(timeout=2)
        invoke(controller, "reconcile", {}, None)
        self.assertEqual(controller.status()["runs"]["run-000001"]["status"], "launched")
        self.assertEqual(controller.status()["process_observations"]["run-000001"], "live")
        with self.assertRaises(SearchError):
            invoke(controller, "run", spec)
        terminal = self.attack_root / ".search/runs/run-000001/terminal.json"
        deadline = time.monotonic() + 4
        while not terminal.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(terminal.exists())
        invoke(controller, "reconcile", {}, None)
        self.assertEqual(controller.status()["runs"]["run-000001"]["status"], "terminal")
        self.assertEqual(controller.status()["process_observations"]["run-000001"], "terminal")
        self.assertEqual(marker.read_text(), "one\n")
        self.assertEqual(controller.status()["totals"]["used_runs"], 1)

    def test_exhausted_inherited_account_refuses_another_checker_launch(self):
        from tests.search_fixtures import review
        controller = admit_workspace(self.attack_root, max_runs=1, computational=True)
        candidate = controller.status()["proposals"]["proposal-000001"]["record"]
        candidate["attack_slug"] = "second"
        candidate["budget"].update(mode="inherit", account_id="account-000001")
        invoke(controller, "propose", {"proposal": candidate, "inputs": []}, None)
        invoke(controller, "review", {"proposal_id": "proposal-000002", "review": review(candidate), "inputs": []}, None)
        invoke(controller, "admit", {}, "proposal-000002")
        self.assertEqual(controller.status()["nodes"]["node-000002"]["account_id"], "account-000001")
        step = self.workspace / "deterministic" / "job"
        step.mkdir()
        (step / "job.py").write_text("print('one run')\n")
        invoke(controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        invoke(controller, "run", spec)
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "run", spec, "node-000002")
        self.assertEqual(caught.exception.code, "budget_exhausted")
        self.assertEqual(controller.status()["totals"]["used_runs"], 1)
