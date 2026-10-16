"""Tests for bin/exactory-lab: the study workspace, its state machine, and execution."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from integration_fixtures import prepare_research, admit_lab
from research_harness.errors import ResearchError

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _load_bin_module(command_name: str, module_name: str):
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(_PLUGIN_ROOT / "bin" / command_name)
    )
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_lab = _load_bin_module("exactory-lab", "exactory_lab")

_STUDY_DIR_NAMES = (
    ".exactory",
    "context",
    "cohort/notes",
    "idea",
    "experiment/code",
    "experiment/logs",
    "experiment/results",
    "experiment/plots",
    "evidence",
    "research",
    "reviews",
    "learnings",
)


def _run_lab_command(argv: list[str], expected_exit_code: int | None,
                     test_case: unittest.TestCase) -> str:
    args = _lab._build_parser().parse_args(argv)
    def invoke():
        try:
            if argv[0] == "run" and not args.admission:
                script = test_case.workspace / "experiment" / args.script
                if script.is_file() and (test_case.workspace / "experiment").resolve() in script.resolve().parents:
                    case = prepare_research(test_case.workspace)
                    admission = admit_lab(case, args.script, backend=args.backend,
                                          timeout=args.timeout, seed=args.seed)
                    args.admission = admission["id"]
            args.handler(args)
        except ResearchError as error:
            _lab._exit_with_error(json.dumps({"error": error.as_dict()}))
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        if expected_exit_code is None:
            invoke()
        else:
            with test_case.assertRaises(SystemExit) as caught:
                invoke()
            test_case.assertEqual(caught.exception.code, expected_exit_code)
    return sink.getvalue()


class _WorkspaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.workspace = Path(self._temp_dir.name) / "study"

    def init_workspace(self) -> str:
        return _run_lab_command(
            ["init", "--dir", str(self.workspace), "--slug", "curvature"], None, self
        )

    def read_study_state(self) -> dict:
        return json.loads(
            (self.workspace / ".exactory" / "study.json").read_text(encoding="utf-8")
        )


class _InsideWorkspaceTestCase(_WorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.init_workspace()
        original_dir = os.getcwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, original_dir)


class TestProbe(_InsideWorkspaceTestCase):
    def run_probe(self, script, *, identifier="probe-1", timeout="2", extra=()):
        path = self.workspace / "source.py"
        path.write_text(script, encoding="utf-8")
        result = _run_lab_command(["probe", "--id", identifier, "--question", "What does this calculation show?",
                                  "--script", str(path), "--timeout", timeout, *extra], None, self)
        return json.loads(result)

    def test_probe_pins_program_inputs_outputs_and_closed_stdin_before_target(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.storage import Store
        input_path = self.workspace / "input.txt"
        input_path.write_text("7")
        script = ('from pathlib import Path\nimport sys\n'
                  'assert sys.stdin.read() == ""\n'
                  'value = int(Path(sys.argv[1]).read_text()) * 2\n'
                  'Path("results/nested").mkdir(parents=True)\n'
                  'Path("results/nested/result.txt").write_text(str(value))\n'
                  'print(value)\nprint("observed", file=sys.stderr)\n')
        receipt = self.run_probe(script, extra=("--input", str(input_path), "--output-dir", "results"))
        records = Store(self.workspace).snapshot()["records"]
        saved = records["probe"]["probe-1"]
        intent = records["probe_intent"]["probe-1"]
        self.assertEqual(receipt["result"], saved)
        artifacts = ArtifactStore(self.workspace)
        self.assertEqual(artifacts.read(intent["program"]), script.encode())
        self.assertEqual([artifacts.read(ref) for ref in intent["inputs"]], [b"7"])
        self.assertEqual([artifacts.read(ref) for ref in saved["outputs"]], [b"14"])
        self.assertEqual(artifacts.read(saved["stdout"]), b"14\n")
        self.assertEqual(artifacts.read(saved["stderr"]), b"observed\n")
        self.assertEqual(saved["exit_status"], 0)
        self.assertGreater(saved["elapsed_seconds"], 0)
        self.assertTrue(list((self.workspace / "research/probes/probe-1").iterdir()))
        self.assertEqual(Store(self.workspace).committed_request("probe:probe-1")["operation"], "research.probe")

    def test_nonzero_exit_retains_outputs_and_logs(self):
        from research_harness.artifacts import ArtifactStore
        receipt = self.run_probe('from pathlib import Path\nimport sys\nPath("partial.txt").write_text("partial")\n'
                                 'print("failed", file=sys.stderr)\nsys.exit(7)\n')
        saved = receipt["result"]
        self.assertEqual(saved["exit_status"], 7)
        self.assertIn(b"partial", [ArtifactStore(self.workspace).read(ref) for ref in saved["outputs"]])
        self.assertEqual(ArtifactStore(self.workspace).read(saved["stderr"]), b"failed\n")

    def test_timeout_retains_partial_output_and_records_timed_out(self):
        from research_harness.artifacts import ArtifactStore
        receipt = self.run_probe('import time\nprint("started", flush=True)\ntime.sleep(10)\n', timeout="0.2")
        saved = receipt["result"]
        self.assertEqual(saved["exit_status"], "timed_out")
        self.assertEqual(ArtifactStore(self.workspace).read(saved["stdout"]), b"started\n")
        self.assertLess(saved["elapsed_seconds"], 5)

    def test_large_finite_timeout_does_not_overflow_supervisor_readiness(self):
        saved = self.run_probe('print("finished")\n', timeout="1e100")["result"]
        self.assertEqual(saved["exit_status"], 0)

    def test_probe_id_and_output_directory_cannot_escape_the_run(self):
        for extra in (("--id", "../escape"), ("--output-dir", "../escape"), ("--output-dir", str(self.workspace / "escape"))):
            with self.subTest(extra=extra):
                path = self.workspace / "source.py"
                path.write_text("print(1)\n")
                result = _run_lab_command(["probe", "--id", "safe", "--question", "Finite check?", "--script", str(path),
                                           "--timeout", "2", *extra], 1, self)
                self.assertIn("unsafe_path", result)

    def test_probe_records_and_charges_output_bound_stdout_and_stderr(self):
        from research_harness.integration import current_store
        from research_harness.resources import account_report
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                path = self.workspace / "source.py"
                path.write_text('import sys\nsys.' + stream + '.write(' + repr("x" if stream == "stdout" else "y")
                                + ' * (32 * 1024 * 1024 + 1))\n')
                result = _run_lab_command(["probe", "--id", stream, "--question", "Output bound?", "--script", str(path),
                                           "--timeout", "2"], None, self)
                records = current_store(self.workspace).snapshot()["records"]
                outcome = json.loads(result)["result"]
                self.assertEqual(outcome["exit_status"], "output_bound")
                self.assertLessEqual(outcome[stream]["size"], 32 * 1024 * 1024)
                self.assertGreater(outcome["elapsed_seconds"], 0)
                self.assertEqual(account_report(records, "research")["experiment"]["wall_seconds"]["charged"],
                                 sum(item["elapsed_seconds"] for item in records["probe"].values()))
                self.assertEqual(account_report(records, "research")["experiment"]["wall_seconds"]["reserved"], 0)
                from research_harness.artifacts import describe_artifact
                from research_harness.probe_provenance import find_probe_artifact_origins
                launch = records["probe_launch"][stream]
                raw = (self.workspace / launch["directory"] / stream).read_bytes()
                descriptor = describe_artifact(raw, "application/octet-stream")
                self.assertEqual(find_probe_artifact_origins(records, [descriptor]),
                                 [{"probe_id": stream, "sha256": descriptor["sha256"], "roles": [stream]}])

    def test_repeated_probe_id_replays_without_rerunning(self):
        receipt = self.run_probe('print("once")\n')
        replay = self.run_probe('print("once")\n')
        self.assertEqual(receipt, replay)

    def test_terminal_probe_retry_replays_even_with_changed_program_or_timeout(self):
        receipt = self.run_probe('print("once")\n')
        for script, timeout in (('print("changed")\n', "2"), ('print("once")\n', "3")):
            path = self.workspace / "source.py"
            path.write_text(script)
            result = _run_lab_command(["probe", "--id", "probe-1", "--question", "What does this calculation show?",
                                       "--script", str(path), "--timeout", timeout], None, self)
            self.assertEqual(json.loads(result), receipt)

    def test_modified_program_and_inputs_remain_pinned_as_originals_and_outputs(self):
        from research_harness.artifacts import ArtifactStore
        input_path = self.workspace / "input.txt"
        input_path.write_text("before")
        script = ('from pathlib import Path\nimport sys\nPath(__file__).write_text("changed program")\n'
                  'Path(sys.argv[1]).write_text("changed input")\n')
        receipt = self.run_probe(script, extra=("--input", str(input_path)))
        from research_harness.storage import Store
        intent = Store(self.workspace).snapshot()["records"]["probe_intent"]["probe-1"]
        artifacts = ArtifactStore(self.workspace)
        self.assertEqual(artifacts.read(intent["program"]), script.encode())
        self.assertEqual(artifacts.read(intent["inputs"][0]), b"before")
        self.assertEqual({artifacts.read(ref) for ref in receipt["result"]["outputs"]}, {b"changed program", b"changed input"})

    def test_symlink_output_directory_is_refused(self):
        from research_harness.integration import current_store
        path = self.workspace / "source.py"
        path.write_text('from pathlib import Path\nPath("results").rmdir()\nPath("results").symlink_to("..", target_is_directory=True)\n')
        result = _run_lab_command(["probe", "--id", "unsafe", "--question", "Path check?", "--script", str(path),
                                   "--output-dir", "results", "--timeout", "2"], None, self)
        self.assertIn("unsafe_path", result)
        records = current_store(self.workspace).snapshot()["records"]
        self.assertEqual(records["probe"]["unsafe"]["exit_status"], "failed")

    def set_probe_budget(self, seconds):
        from research_harness.integration import current_store
        from research_harness.resources import set_budget, UNITS
        store = current_store(self.workspace)
        set_budget(store, {"profile": "research", "purpose": "experiment", "limits": {
            unit: seconds if unit == "wall_seconds" else None for unit in UNITS}, "reason": "Bound local exploration."},
            expected_revision=store.revision, request_id="probe-budget")

    def test_probe_budget_is_checked_before_running(self):
        self.set_probe_budget(0)
        path = self.workspace / "source.py"
        marker = self.workspace / "ran.txt"
        path.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ran")\n')
        result = _run_lab_command(["probe", "--id", "no-budget", "--question", "Budget check?", "--script", str(path),
                                   "--timeout", "1"], 1, self)
        self.assertIn("resource_budget_exhausted", result)
        self.assertFalse(marker.exists())

    def test_timeout_cleanup_overhead_is_recorded_and_charged(self):
        from research_harness.integration import current_store
        from research_harness.resources import account_report, obligations
        self.set_probe_budget(0.1)
        saved = self.run_probe('import time\ntime.sleep(10)\n', timeout="0.1")["result"]
        self.assertEqual(saved["exit_status"], "timed_out")
        records = current_store(self.workspace).snapshot()["records"]
        self.assertEqual(account_report(records, "research")["experiment"]["wall_seconds"]["charged"], saved["elapsed_seconds"])
        self.assertIn("resource_budget_exhausted", {item["code"] for item in obligations(records, "research")})

    def test_another_launcher_of_the_owned_probe_returns_pending(self):
        from research_harness.probe import hold_probe_owner
        with hold_probe_owner(self.workspace, "owned"):
            path = self.workspace / "source.py"
            path.write_text('print("must not run")\n')
            result = _run_lab_command(["probe", "--id", "owned", "--question", "Ownership check?", "--script", str(path),
                                       "--timeout", "1"], None, self)
        self.assertEqual(json.loads(result)["status"], "pending")

    def test_probe_ids_do_not_collide_with_launcher_metadata(self):
        saved = self.run_probe('print("safe identity")\n', identifier="launcher.lock")["result"]
        self.assertEqual(saved["id"], "launcher.lock")
        self.assertEqual(saved["exit_status"], 0)

    def test_changed_input_bytes_are_outputs_even_when_timestamps_are_restored(self):
        from research_harness.artifacts import ArtifactStore
        input_path = self.workspace / "input.txt"
        input_path.write_text("before")
        script = ('from pathlib import Path\nimport os, sys\npath = Path(sys.argv[1])\n'
                  'original = path.stat()\npath.write_text("after")\n'
                  'os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))\n')
        saved = self.run_probe(script, extra=("--input", str(input_path)))["result"]
        self.assertEqual([ArtifactStore(self.workspace).read(ref) for ref in saved["outputs"]], [b"after"])

    def test_incomplete_probe_id_cannot_silently_run_again(self):
        from research_harness.probe import record_probe_intent
        from research_harness.integration import current_store
        from research_harness.artifacts import ArtifactStore
        store = current_store(self.workspace)
        value = {"id": "interrupted", "question": "A previous launch?", "interpreter": sys.executable,
                 "program": ArtifactStore(self.workspace).put(b'print("original")\n', "text/x-python"), "inputs": [], "timeout_seconds": 2}
        record_probe_intent(store, value, expected_revision=store.revision, request_id="interrupted-intent")
        path = self.workspace / "source.py"
        path.write_text('print("must not rerun")\n')
        result = _run_lab_command(["probe", "--id", "interrupted", "--question", "Retry?", "--script", str(path),
                                   "--timeout", "1"], None, self)
        self.assertEqual(json.loads(result)["result"]["exit_status"], "interrupted")


class TestProbeLifecycle(_InsideWorkspaceTestCase):
    def build_command(self, identifier, script, timeout="5"):
        path = self.workspace / "probe-script.py"
        path.write_text(script)
        return [sys.executable, str(_PLUGIN_ROOT / "bin/exactory-lab"), "probe", "--id", identifier,
                "--question", "Lifecycle check?", "--script", str(path), "--timeout", timeout]

    def wait_for_file(self, path):
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), str(path))

    def test_failed_worker_start_records_terminal_cost_and_releases_reservation(self):
        from research_harness.integration import current_store
        from research_harness.probe import launch_probe
        from research_harness.resources import account_report
        command = self.build_command("cannot-start", 'print("must not run")\n')
        store = current_store(self.workspace)
        with unittest.mock.patch("research_harness.probe.subprocess.Popen", side_effect=OSError("worker unavailable")):
            receipt = launch_probe(store, identifier="cannot-start", question="Lifecycle check?", script=command[-3], timeout=5)
        self.assertEqual(receipt["result"]["exit_status"], "failed")
        usage = account_report(store.snapshot()["records"], "research")["experiment"]["wall_seconds"]
        self.assertEqual(usage["reserved"], 0)
        self.assertGreaterEqual(usage["charged"], 0)

    def test_outcome_committed_before_launch_claim_prevents_unreserved_execution(self):
        from research_harness.integration import current_store
        from research_harness import probe
        from test_research_probe_lifecycle import build_probe_outcome
        from research_harness.artifacts import ArtifactStore
        store = current_store(self.workspace)
        command = self.build_command("claimed-outcome", 'print("must not run")\n')
        original = probe.record_probe_launch
        saved = []
        def record_then_claim(store, payload, **identity):
            saved.append(probe.record_probe(store, build_probe_outcome(ArtifactStore(store.root), "claimed-outcome"),
                expected_revision=store.revision, request_id="external-outcome"))
            return original(store, payload, expected_revision=store.revision, request_id=identity["request_id"])
        with unittest.mock.patch.object(probe, "record_probe_launch", side_effect=record_then_claim):
            with unittest.mock.patch.object(probe.subprocess, "Popen") as spawn:
                result = probe.launch_probe(store, identifier="claimed-outcome", question="Lifecycle check?", script=command[-3], timeout=5)
        spawn.assert_not_called()
        self.assertEqual(result, saved[0])

    def test_terminal_files_reconcile_after_lost_outcome_response_without_rerun(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.integration import current_store
        from research_harness.probe import launch_probe
        from research_harness.resources import account_report
        marker = self.workspace / "once"
        command = self.build_command("sealed", 'from pathlib import Path\nPath(' + repr(str(marker))
            + ').write_text("ran")\nPath("result.txt").write_text("result")\nprint("observed")\n')
        store = current_store(self.workspace)
        with unittest.mock.patch("research_harness.probe.record_probe", side_effect=ResearchError("response_lost", "Lost response")):
            with self.assertRaises(ResearchError):
                launch_probe(store, identifier="sealed", question="Lifecycle check?", script=command[-3], timeout=5)
        records = store.snapshot()["records"]
        self.assertNotIn("sealed", records.get("probe", {}))
        self.assertEqual(account_report(records, "research")["experiment"]["wall_seconds"]["reserved"], 5)
        directory = self.workspace / records["probe_launch"]["sealed"]["directory"]
        (directory / "outcome.json").write_text("{", encoding="utf-8")
        marker.unlink()
        receipt = launch_probe(store, identifier="sealed", question="Lifecycle check?", script="missing.py", timeout=5)
        self.assertEqual(receipt["result"]["exit_status"], 0)
        self.assertEqual(ArtifactStore(self.workspace).read(receipt["result"]["stdout"]), b"observed\n")
        self.assertIn(b"result", [ArtifactStore(self.workspace).read(item) for item in receipt["result"]["outputs"]])
        self.assertFalse(marker.exists())

    def test_invalid_outcome_values_fall_back_to_complete_original_terminal_files(self):
        from research_harness.integration import current_store
        from research_harness.probe import launch_probe
        command = self.build_command("invalid-outcome", 'print("original")\n')
        store = current_store(self.workspace)
        with unittest.mock.patch("research_harness.probe.record_probe", side_effect=ResearchError("response_lost", "Lost response")):
            with self.assertRaises(ResearchError):
                launch_probe(store, identifier="invalid-outcome", question="Lifecycle check?", script=command[-3], timeout=5)
        launch = store.snapshot()["records"]["probe_launch"]["invalid-outcome"]
        path = self.workspace / launch["directory"] / "outcome.json"
        original = json.loads(path.read_text())
        for changes in ({"exit_status": True}, {"elapsed_seconds": -1}, {"id": "another-intent"}, {"command": "different"},
                        {"stdout": dict(original["outcome"]["stdout"], sha256="0" * 64, path="research/sources/objects/" + "0" * 64)}):
            path.write_text(json.dumps(dict(original, outcome=dict(original["outcome"], **changes))))
            with unittest.mock.patch("research_harness.probe.record_probe", side_effect=ResearchError("response_lost", "Lost response")) as record:
                with self.assertRaises(ResearchError):
                    launch_probe(store, identifier="invalid-outcome", question="Lifecycle check?", script="missing.py", timeout=5)
            self.assertEqual(record.call_args.args[1], original["outcome"])

    def test_incomplete_execution_with_nonobject_usage_reconciles_as_interrupted(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.integration import current_store
        from research_harness.probe import launch_probe
        from research_harness.resources import account_report
        command = self.build_command("incomplete", 'print("partial")\n')
        store = current_store(self.workspace)
        with unittest.mock.patch("research_harness.probe.record_probe", side_effect=ResearchError("response_lost", "Lost response")):
            with self.assertRaises(ResearchError):
                launch_probe(store, identifier="incomplete", question="Lifecycle check?", script=command[-3], timeout=5)
        launch = store.snapshot()["records"]["probe_launch"]["incomplete"]
        directory = self.workspace / launch["directory"]
        (directory / "outcome.json").unlink()
        (directory / "terminal.json").write_text('{"exit_status":true,"elapsed_seconds":0,"config_sha256":'
            + json.dumps(launch["config_artifact"]["sha256"]) + '}')
        (directory / "usage.json").write_text("42")
        saved = launch_probe(store, identifier="incomplete", question="Lifecycle check?", script="missing.py", timeout=5)["result"]
        self.assertEqual(saved["exit_status"], "interrupted")
        self.assertEqual(ArtifactStore(self.workspace).read(saved["stdout"]), b"partial\n")
        usage = account_report(store.snapshot()["records"], "research")["experiment"]["wall_seconds"]
        self.assertEqual((usage["reserved"], usage["unknown"]), (0, 1))

    def test_concurrent_retry_returns_pending_and_preserves_reservation_and_revision(self):
        from research_harness.storage import Store
        from research_harness.resources import account_report
        marker, release = self.workspace / "started", self.workspace / "release"
        command = self.build_command("live", 'from pathlib import Path\nimport time\nPath(' + repr(str(marker))
            + ').write_text("started")\ndeadline = time.monotonic() + 50\nwhile not Path(' + repr(str(release)) + ').exists() and time.monotonic() < deadline: time.sleep(0.01)\n')
        owner = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: owner.kill() if owner.poll() is None else None)
        self.wait_for_file(marker)
        before = Store(self.workspace).snapshot()
        retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(json.loads(retry.stdout)["status"], "pending")
        self.assertEqual(Store(self.workspace).snapshot(), before)
        self.assertEqual(account_report(before["records"], "research")["experiment"]["wall_seconds"]["reserved"], 5)
        release.write_text("finish")
        stdout, stderr = owner.communicate(timeout=10)
        self.assertEqual(owner.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"]["exit_status"], 0)
        self.assertEqual(account_report(Store(self.workspace).snapshot()["records"], "research")["experiment"]["wall_seconds"]["reserved"], 0)

    def test_lost_launcher_still_has_live_worker_then_reconciles_without_relaunch(self):
        from research_harness.storage import Store
        marker, release = self.workspace / "started", self.workspace / "release"
        command = self.build_command("lost", 'from pathlib import Path\nimport time\nPath(' + repr(str(marker))
            + ').write_text("started")\ndeadline = time.monotonic() + 50\nwhile not Path(' + repr(str(release)) + ').exists() and time.monotonic() < deadline: time.sleep(0.01)\nprint("finished")\n')
        owner = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: owner.kill() if owner.poll() is None else None)
        self.wait_for_file(marker)
        owner.kill()
        owner.communicate(timeout=5)
        before = Store(self.workspace).snapshot()
        retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(json.loads(retry.stdout)["status"], "pending")
        self.assertEqual(Store(self.workspace).snapshot(), before)
        release.write_text("finish")
        launch = Store(self.workspace).snapshot()["records"]["probe_launch"]["lost"]
        self.wait_for_file(self.workspace / launch["directory"] / "outcome.json")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            reconciled = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
            result = json.loads(reconciled.stdout)
            if result.get("status") != "pending":
                break
            time.sleep(0.01)
        self.assertEqual(result["result"]["exit_status"], 0)
        marker.unlink()
        replay = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(json.loads(replay.stdout), result)
        self.assertFalse(marker.exists(), "A replay must not execute the script")

    def test_unlaunched_intent_reconciles_interrupted_only_after_owner_lock_is_free(self):
        from research_harness.artifacts import ArtifactStore
        from research_harness.integration import current_store
        from research_harness.probe import record_probe_intent
        from research_harness.resources import account_report
        store = current_store(self.workspace)
        marker = self.workspace / "must-not-run"
        script = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ran")\n'
        command = self.build_command("interrupted", script)
        intent = {"id": "interrupted", "question": "Lifecycle check?", "interpreter": sys.executable,
                  "program": ArtifactStore(self.workspace).put(script.encode(), "text/x-python"), "inputs": [], "timeout_seconds": 5}
        record_probe_intent(store, intent, expected_revision=store.revision, request_id="intent:interrupted")
        before = store.snapshot()
        self.assertEqual(account_report(before["records"], "research")["experiment"]["wall_seconds"]["reserved"], 5)
        result = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"]["exit_status"], "interrupted")
        self.assertFalse(marker.exists())
        self.assertEqual(account_report(store.snapshot()["records"], "research")["experiment"]["wall_seconds"]["reserved"], 0)

    def test_lost_worker_does_not_release_while_the_program_retains_ownership(self):
        from research_harness.storage import Store
        from research_harness.resources import account_report
        marker, release = self.workspace / "started", self.workspace / "release"
        command = self.build_command("lost-worker", 'from pathlib import Path\nimport time\nPath(' + repr(str(marker))
            + ').write_text("started")\nprint("partial", flush=True)\ndeadline = time.monotonic() + 50\nwhile not Path(' + repr(str(release)) + ').exists() and time.monotonic() < deadline: time.sleep(0.01)\n')
        owner = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: owner.kill() if owner.poll() is None else None)
        self.wait_for_file(marker)
        launch = Store(self.workspace).snapshot()["records"]["probe_launch"]["lost-worker"]
        directory = self.workspace / launch["directory"]
        self.wait_for_file(directory / "worker.json")
        os.kill(json.loads((directory / "worker.json").read_text())["pid"], signal.SIGKILL)
        stdout, stderr = owner.communicate(timeout=5)
        self.assertEqual(owner.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "pending")
        before = Store(self.workspace).snapshot()
        self.assertNotIn("lost-worker", before["records"].get("probe", {}))
        self.assertEqual(account_report(before["records"], "research")["experiment"]["wall_seconds"]["reserved"], 5)
        retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(json.loads(retry.stdout)["status"], "pending")
        self.assertEqual(Store(self.workspace).snapshot(), before)
        release.write_text("finish")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
            result = json.loads(response.stdout)
            if result.get("status") != "pending":
                break
            time.sleep(0.01)
        self.assertEqual(result["result"]["exit_status"], "interrupted")
        from research_harness.artifacts import ArtifactStore
        self.assertEqual(ArtifactStore(self.workspace).read(result["result"]["stdout"]), b"partial\n")

    def test_lost_worker_timeout_terminates_surviving_program_and_charges_before_replay(self):
        self.check_lost_worker_timeout()

    def test_lost_worker_deadline_stops_program_and_descendants_without_retry(self):
        from research_harness.probe import hold_probe_owner
        from research_harness.probe_supervision import find_probe_process_identity, read_probe_clock
        from research_harness.resources import account_report
        from research_harness.storage import Store
        marker = self.workspace / "autonomous-program"
        command = self.build_command("autonomous-timeout", 'from pathlib import Path\nimport os, time\n'
            'child = os.fork()\n'
            'if child:\n'
            '    Path(' + repr(str(marker)) + ').write_text(str(os.getpid()) + "," + str(child))\n'
            '    print("started", flush=True)\n'
            'deadline = time.monotonic() + 50\n'
            'while time.monotonic() < deadline: time.sleep(0.01)\n', timeout="2")
        launcher = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: launcher.kill() if launcher.poll() is None else None)
        self.wait_for_file(marker)
        program, child = map(int, marker.read_text().split(","))
        group = os.getpgid(program)
        def terminate_group():
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(terminate_group)
        store = Store(self.workspace)
        launch = store.snapshot()["records"]["probe_launch"]["autonomous-timeout"]
        directory = self.workspace / launch["directory"]
        supervision = json.loads((directory / "supervision.json").read_text())
        os.kill(json.loads((directory / "worker.json").read_text())["pid"], signal.SIGKILL)
        stdout, stderr = launcher.communicate(timeout=5)
        self.assertEqual(launcher.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "pending")
        before = store.snapshot()
        deadline = supervision["deadline"] + 3
        while read_probe_clock() < deadline and any(find_probe_process_identity(pid) is not None for pid in (group, program, child)):
            time.sleep(0.01)
        self.assertTrue(all(find_probe_process_identity(pid) is None for pid in (group, program, child)),
                        "The frozen deadline must terminate the complete group without another CLI call")
        self.assertEqual(store.snapshot(), before)
        self.assertNotIn("autonomous-timeout", before["records"].get("probe", {}))
        self.assertEqual(account_report(before["records"], "research")["experiment"]["wall_seconds"]["reserved"], 2)
        with hold_probe_owner(self.workspace, "autonomous-timeout") as owner:
            self.assertIsNotNone(owner)
        self.wait_for_file(directory / "supervisor-terminal.json")
        terminal = json.loads((directory / "supervisor-terminal.json").read_text())
        self.assertEqual(terminal["deadline"], supervision["deadline"])
        self.assertEqual(terminal["program"], supervision["program"])
        from research_harness import probe
        with unittest.mock.patch.object(probe, "find_probe_group_members", return_value=[{"pid": child}]):
            pending = probe.launch_probe(store, identifier="autonomous-timeout", question="Lifecycle check?",
                                         script="missing.py", timeout=2)
        self.assertEqual(pending["status"], "pending", "An unverified group must retain its reservation")
        self.assertEqual(store.snapshot(), before)
        time.sleep(0.2)
        retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        receipt = json.loads(retry.stdout)
        self.assertEqual(receipt["result"]["exit_status"], "timed_out")
        self.assertEqual(receipt["result"]["elapsed_seconds"], terminal["elapsed_seconds"])
        usage = account_report(store.snapshot()["records"], "research")["experiment"]["wall_seconds"]
        self.assertEqual((usage["reserved"], usage["unknown"]), (0, 0))
        self.assertEqual(usage["charged"], terminal["elapsed_seconds"])
        replay = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(json.loads(replay.stdout), receipt)
        self.assertEqual(marker.read_text(), str(program) + "," + str(child))

    def test_lost_timeout_recovery_preserves_measured_usage_without_charging_replay_delay(self):
        self.check_lost_worker_timeout(interrupt_recovery=True)

    def check_lost_worker_timeout(self, interrupt_recovery=False):
        from research_harness.artifacts import ArtifactStore
        from research_harness.probe import hold_probe_owner
        from research_harness.resources import account_report
        from research_harness.storage import Store
        marker = self.workspace / "surviving-program"
        command = self.build_command("worker-timeout", 'from pathlib import Path\nimport os, time\nos.closerange(3, 256)\nPath('
            + repr(str(marker)) + ').write_text(str(os.getpid()))\nprint("partial", flush=True)\n'
              'deadline = time.monotonic() + 50\nwhile time.monotonic() < deadline: time.sleep(0.01)\n', timeout="2")
        owner = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: owner.kill() if owner.poll() is None else None)
        self.wait_for_file(marker)
        program_pid = int(marker.read_text())
        program_group = os.getpgid(program_pid)
        def terminate_program():
            try:
                os.killpg(program_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(terminate_program)
        launch = Store(self.workspace).snapshot()["records"]["probe_launch"]["worker-timeout"]
        directory = self.workspace / launch["directory"]
        self.wait_for_file(directory / "watchdog.json")
        # Also lose the autonomous watchdog so these tests retain coverage of retry cleanup and its crash windows.
        os.kill(json.loads((directory / "watchdog.json").read_text())["identity"]["pid"], signal.SIGKILL)
        os.kill(json.loads((directory / "worker.json").read_text())["pid"], signal.SIGKILL)
        stdout, stderr = owner.communicate(timeout=5)
        self.assertEqual(owner.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "pending")
        before = Store(self.workspace).snapshot()
        retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(json.loads(retry.stdout)["status"], "pending")
        self.assertEqual(Store(self.workspace).snapshot(), before)
        self.assertEqual(account_report(before["records"], "research")["experiment"]["wall_seconds"]["reserved"], 2)
        time.sleep(2)
        from research_harness import probe
        supervision = json.loads((directory / "supervision.json").read_text())
        changed_identity = dict(supervision["program"], start_identity=["reused-pid"])
        with unittest.mock.patch.object(probe, "find_probe_process_identity", side_effect=(None, changed_identity)):
            with unittest.mock.patch.object(probe.os, "killpg") as terminate:
                pending = probe.launch_probe(Store(self.workspace), identifier="worker-timeout", question="Lifecycle check?",
                                             script="missing.py", timeout=2)
        terminate.assert_not_called()
        self.assertEqual(pending["status"], "pending", "A reused process ID cannot authorize termination")
        self.assertEqual(Store(self.workspace).snapshot(), before)
        if interrupt_recovery:
            original_kill = os.killpg
            def terminate_then_lose_recovery(group, sig):
                original_kill(group, sig)
                concurrent = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
                self.assertEqual(concurrent.returncode, 0, concurrent.stderr)
                self.assertEqual(json.loads(concurrent.stdout)["status"], "pending")
                self.assertEqual(Store(self.workspace).snapshot(), before)
                raise ResearchError("response_lost", "Recovery disappeared after signalling the original group")
            with unittest.mock.patch.object(probe.os, "killpg", side_effect=terminate_then_lose_recovery):
                with self.assertRaises(ResearchError) as lost:
                    probe.launch_probe(Store(self.workspace), identifier="worker-timeout", question="Lifecycle check?",
                                       script="missing.py", timeout=2)
            self.assertEqual(lost.exception.code, "response_lost")
            time.sleep(0.2)
        retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        receipt = json.loads(retry.stdout)
        self.assertIn("result", receipt, "An expired surviving program must be terminated and reconciled")
        saved = receipt["result"]
        self.assertEqual(saved["exit_status"], "timed_out")
        self.assertGreaterEqual(saved["elapsed_seconds"], 2)
        self.assertEqual(ArtifactStore(self.workspace).read(saved["stdout"]), b"partial\n")
        with hold_probe_owner(self.workspace, "worker-timeout") as descriptor:
            self.assertIsNotNone(descriptor, "The original process group must release ownership before its reservation")
        usage = account_report(Store(self.workspace).snapshot()["records"], "research")["experiment"]["wall_seconds"]
        self.assertEqual((usage["reserved"], usage["unknown"]), (0, int(interrupt_recovery)))
        self.assertEqual(usage["charged"], saved["elapsed_seconds"])
        if interrupt_recovery:
            termination = json.loads((directory / "termination.json").read_text())
            self.assertEqual(saved["elapsed_seconds"], termination["elapsed_seconds"])
        replay = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(json.loads(replay.stdout), receipt)
        self.assertEqual(int(marker.read_text()), program_pid, "A retry must never relaunch the program")

    def test_program_starts_only_after_process_identity_and_deadline_are_durable(self):
        from research_harness.integration import current_store
        from research_harness.probe import launch_probe
        command = self.build_command("supervised", 'from pathlib import Path\nimport json, os, time\n'
            'directory = Path.cwd().parent\n'
            'supervision = json.loads((directory / "supervision.json").read_text())\n'
            'worker = json.loads((directory / "worker.json").read_text())\n'
            'assert supervision["program"]["pid"] == os.getpgrp()\n'
            'assert supervision["program"]["process_group"] == os.getpgrp()\n'
            'assert supervision["program"]["start_identity"]\n'
            'assert supervision["worker"] == worker["identity"]\n'
            'assert supervision["deadline"] == supervision["execution_started"] + 5\n'
            'assert supervision["started"] <= supervision["execution_started"] <= time.clock_gettime(time.CLOCK_MONOTONIC) < supervision["deadline"]\n'
            'assert supervision["config_sha256"] == worker["config_sha256"]\n')
        receipt = launch_probe(current_store(self.workspace), identifier="supervised", question="Lifecycle check?",
                               script=command[-3], timeout=5)
        self.assertEqual(receipt["result"]["exit_status"], 0)

    def test_retry_after_deadline_keeps_a_live_worker_pending(self):
        from research_harness.storage import Store
        marker = self.workspace / "live-worker"
        command = self.build_command("stopped-worker", 'from pathlib import Path\nimport time\nPath('
            + repr(str(marker)) + ').write_text("started")\ntime.sleep(10)\n', timeout="1")
        owner = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: owner.kill() if owner.poll() is None else None)
        self.wait_for_file(marker)
        launch = Store(self.workspace).snapshot()["records"]["probe_launch"]["stopped-worker"]
        directory = self.workspace / launch["directory"]
        worker_pid = json.loads((directory / "worker.json").read_text())["pid"]
        os.kill(worker_pid, signal.SIGSTOP)
        try:
            owner.kill()
            owner.communicate(timeout=5)
            time.sleep(1)
            before = Store(self.workspace).snapshot()
            retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            self.assertEqual(json.loads(retry.stdout)["status"], "pending")
            self.assertEqual(Store(self.workspace).snapshot(), before)
        finally:
            os.kill(worker_pid, signal.SIGCONT)
        self.wait_for_file(directory / "outcome.json")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
            self.assertEqual(retry.returncode, 0, retry.stderr)
            result = json.loads(retry.stdout)
            if result.get("status") != "pending":
                break
            time.sleep(0.01)
        self.assertEqual(result["result"]["exit_status"], "timed_out")

    def test_lost_worker_cleans_up_descendants_when_the_script_exits(self):
        from research_harness.probe import hold_probe_owner
        from research_harness.probe_supervision import find_probe_process_identity
        from research_harness.resources import account_report
        from research_harness.storage import Store
        marker, release = self.workspace / "descendant", self.workspace / "finish-parent"
        command = self.build_command("lost-parent", 'from pathlib import Path\nimport os, time\n'
            'if os.fork() == 0:\n'
            '    Path(' + repr(str(marker)) + ').write_text(str(os.getpid()))\n'
            '    deadline = time.monotonic() + 50\n'
            '    while time.monotonic() < deadline: time.sleep(0.01)\n'
            '    os._exit(0)\n'
            'deadline = time.monotonic() + 50\nwhile not Path(' + repr(str(release)) + ').exists() and time.monotonic() < deadline: time.sleep(0.01)\n', timeout="5")
        owner = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: owner.kill() if owner.poll() is None else None)
        self.wait_for_file(marker)
        child_pid = int(marker.read_text())
        group = os.getpgid(child_pid)
        def terminate_group():
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(terminate_group)
        launch = Store(self.workspace).snapshot()["records"]["probe_launch"]["lost-parent"]
        directory = self.workspace / launch["directory"]
        os.kill(json.loads((directory / "worker.json").read_text())["pid"], signal.SIGKILL)
        stdout, stderr = owner.communicate(timeout=5)
        self.assertEqual(owner.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["status"], "pending")
        release.write_text("finish")
        deadline = time.monotonic() + 2
        while find_probe_process_identity(child_pid) is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(find_probe_process_identity(child_pid), "A script exit must not strand its same-group descendants")
        with hold_probe_owner(self.workspace, "lost-parent") as descriptor:
            self.assertIsNotNone(descriptor)
        retry = subprocess.run(command, cwd=self.workspace, capture_output=True, timeout=5)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(json.loads(retry.stdout)["result"]["exit_status"], "interrupted")
        self.assertEqual(account_report(Store(self.workspace).snapshot()["records"], "research")["experiment"]["wall_seconds"]["reserved"], 0)


class TestInit(_WorkspaceTestCase):
    def test_init_creates_the_study_layout(self) -> None:
        self.init_workspace()
        for dir_name in _STUDY_DIR_NAMES:
            self.assertTrue((self.workspace / dir_name).is_dir(), dir_name)
        self.assertFalse((self.workspace / "draft").exists())

    def test_init_writes_the_study_state(self) -> None:
        self.init_workspace()
        state = self.read_study_state()
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["slug"], "curvature")
        self.assertEqual(state["stage"], "initiate")
        self.assertEqual(state["status"], "pending")
        self.assertTrue(state["autopilot"])
        self.assertEqual(state["waiting"], "context")
        self.assertEqual(state["loop"], {"target": None, "budget": None, "notes": ""})
        self.assertIn("created", state)
        self.assertIn("updated", state)

    def test_init_writes_the_context_readme(self) -> None:
        self.init_workspace()
        readme_text = (self.workspace / "context" / "README.md").read_text(encoding="utf-8")
        self.assertIn("Context intake", readme_text)

    def test_init_seeds_the_literature_log_only_when_absent(self) -> None:
        preserved_line = "## 2026-08-01T00:00Z - earlier pass\n"
        (self.workspace / "research").mkdir(parents=True)
        (self.workspace / "research" / "literature.md").write_text(
            preserved_line, encoding="utf-8"
        )
        self.init_workspace()
        self.assertEqual(
            (self.workspace / "research" / "literature.md").read_text(encoding="utf-8"),
            preserved_line,
        )

    def test_init_creates_a_git_repository_with_a_latex_gitignore(self) -> None:
        self.init_workspace()
        self.assertTrue((self.workspace / ".git").is_dir())
        gitignore_text = (self.workspace / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.aux", gitignore_text)

    def test_init_skips_git_init_inside_an_existing_repository(self) -> None:
        parent_dir = self.workspace.parent
        subprocess.run(["git", "init", "-q", str(parent_dir)], check=True)
        self.init_workspace()
        self.assertFalse((self.workspace / ".git").exists())

    def test_init_refuses_an_existing_study_workspace(self) -> None:
        self.init_workspace()
        output = _run_lab_command(
            ["init", "--dir", str(self.workspace), "--slug", "curvature"], 1, self
        )
        self.assertIn("already", output)


class TestState(_InsideWorkspaceTestCase):
    def test_show_prints_the_study_state(self) -> None:
        output = _run_lab_command(["state", "show"], None, self)
        self.assertEqual(json.loads(output)["slug"], "curvature")

    def test_set_mutates_only_the_given_keys(self) -> None:
        before = self.read_study_state()
        _run_lab_command(["state", "set", "--stage", "cohort", "--status", "running"],
                         None, self)
        state = self.read_study_state()
        self.assertEqual(state["stage"], "cohort")
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["slug"], before["slug"])
        self.assertTrue(state["autopilot"])

    def test_set_refuses_an_unknown_stage(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            _lab._build_parser().parse_args(["state", "set", "--stage", "escape"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(stderr.getvalue(),
            "usage: exactory-lab state set [-h]\n"
            "                              [--stage {initiate,cohort,literature,ideate,experiment,write,evaluate,deposit,submit,complete}]\n"
            "                              [--status STATUS] [--autopilot {on,off}]\n"
            "                              [--waiting WAITING] [--loop-target LOOP_TARGET]\n"
            "                              [--loop-budget LOOP_BUDGET]\n"
            "                              [--loop-notes LOOP_NOTES]\n"
            "                              [--expected-revision EXPECTED_REVISION]\n"
            "                              [--request-id REQUEST_ID]\n"
            "exactory-lab state set: error: argument --stage: invalid choice: 'escape' "
            "(choose from initiate, cohort, literature, ideate, experiment, write, evaluate, deposit, submit, complete)\n")

    def test_set_switches_autopilot(self) -> None:
        _run_lab_command(["state", "set", "--autopilot", "off"], None, self)
        self.assertFalse(self.read_study_state()["autopilot"])
        _run_lab_command(["state", "set", "--autopilot", "on"], None, self)
        self.assertTrue(self.read_study_state()["autopilot"])

    def test_set_parks_and_releases_the_waiting_marker(self) -> None:
        _run_lab_command(["state", "set", "--waiting", "production-deposit"], None, self)
        self.assertEqual(self.read_study_state()["waiting"], "production-deposit")
        _run_lab_command(["state", "set", "--waiting", "none"], None, self)
        self.assertIsNone(self.read_study_state()["waiting"])

    def test_set_records_the_loop_policy(self) -> None:
        _run_lab_command(
            ["state", "set", "--loop-target", "8", "--loop-budget", "12",
             "--loop-notes", "stop after experiments"], None, self,
        )
        self.assertEqual(
            self.read_study_state()["loop"],
            {"target": 8.0, "budget": 12, "notes": "stop after experiments"},
        )

    def test_state_outside_a_workspace_is_refused(self) -> None:
        os.chdir(self._temp_dir.name)
        _run_lab_command(["state", "show"], 2, self)


class TestDecide(_InsideWorkspaceTestCase):
    def read_decision_lines(self) -> list[dict]:
        log_path = self.workspace / ".exactory" / "decisions.jsonl"
        return [json.loads(line) for line in
                log_path.read_text(encoding="utf-8").splitlines()]

    def test_decide_refuses_an_unknown_stage(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            _lab._build_parser().parse_args(
                ["decide", "--decision", "adopt node n3", "--why", "best metric",
                 "--stage", "escape"]
            )
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(stderr.getvalue().splitlines()[-1],
            "exactory-lab decide: error: argument --stage: invalid choice: 'escape' "
            "(choose from initiate, cohort, literature, ideate, experiment, write, evaluate, deposit, submit, complete)")
        self.assertFalse((self.workspace / ".exactory" / "decisions.jsonl").exists())

    def test_decide_appends_an_entry_with_the_current_stage(self) -> None:
        _run_lab_command(["decide", "--decision", "cs.LG is the category",
                          "--why", "the context names deep learning"], None, self)
        entries = self.read_decision_lines()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["stage"], "initiate")
        self.assertEqual(entries[0]["decision"], "cs.LG is the category")
        self.assertEqual(entries[0]["why"], "the context names deep learning")
        self.assertIn("ts", entries[0])

    def test_decide_takes_an_explicit_stage_and_evidence(self) -> None:
        _run_lab_command(
            ["decide", "--decision", "adopt node n3", "--why", "best metric",
             "--stage", "experiment", "--evidence", "experiment/results/n3.json"],
            None, self,
        )
        entry = self.read_decision_lines()[0]
        self.assertEqual(entry["stage"], "experiment")
        self.assertEqual(entry["evidence"], "experiment/results/n3.json")

    def test_decide_appends_rather_than_overwrites(self) -> None:
        for decision_number in range(2):
            _run_lab_command(["decide", "--decision", f"decision {decision_number}",
                              "--why", "because"], None, self)
        self.assertEqual(len(self.read_decision_lines()), 2)


class TestRunLocal(_InsideWorkspaceTestCase):
    def write_script(self, name: str, body: str) -> None:
        (self.workspace / "experiment" / "code" / name).write_text(
            body, encoding="utf-8"
        )

    def read_record(self, node: str) -> dict:
        return json.loads(
            (self.workspace / "experiment" / "results" / f"{node}.json")
            .read_text(encoding="utf-8")
        )

    def test_run_captures_the_metrics_line(self) -> None:
        self.write_script("n1.py", "import json\n"
                                   "print(json.dumps({'metric': 0.83, 'loss': 0.1}))\n")
        output = _run_lab_command(["run", "code/n1.py"], None, self)
        record = json.loads(output.splitlines()[-1])
        self.assertTrue(record["ok"])
        self.assertFalse(record["is_buggy"])
        self.assertEqual(record["returncode"], 0)
        self.assertEqual(record["metric"], {"metric": 0.83, "loss": 0.1})
        self.assertEqual(record["backend"], "local")
        self.assertEqual(self.read_record("n1"), record)

    def test_run_falls_back_to_the_results_file(self) -> None:
        self.write_script("n2.py", "import json, pathlib\n"
                                   "pathlib.Path('results/n2.json').write_text("
                                   "json.dumps({'metric': 0.5}))\n")
        output = _run_lab_command(["run", "code/n2.py"], None, self)
        record = json.loads(output.splitlines()[-1])
        self.assertTrue(record["ok"])
        self.assertEqual(record["metric"], {"metric": 0.5})

    def test_run_marks_a_timeout_as_buggy(self) -> None:
        self.write_script("n3.py", "import time\ntime.sleep(5)\n")
        output = _run_lab_command(["run", "code/n3.py", "--timeout", "0.2"], 1, self)
        record = json.loads(output.splitlines()[-1])
        self.assertTrue(record["timed_out"])
        self.assertTrue(record["is_buggy"])
        self.assertEqual(record["returncode"], -1)

    def test_run_marks_a_crash_as_buggy_and_keeps_the_stderr_tail(self) -> None:
        self.write_script("n4.py", "raise RuntimeError('boom')\n")
        output = _run_lab_command(["run", "code/n4.py"], 1, self)
        record = json.loads(output.splitlines()[-1])
        self.assertTrue(record["is_buggy"])
        self.assertIn("boom", record["stderr_tail"])

    def test_run_writes_the_log_file(self) -> None:
        self.write_script("n5.py", "print('hello')\n")
        _run_lab_command(["run", "code/n5.py"], 1, self)
        log_text = (self.workspace / "experiment" / "logs" / "n5.log").read_text(
            encoding="utf-8"
        )
        self.assertIn("STDOUT", log_text)
        self.assertIn("hello", log_text)

    def test_run_passes_the_seed_through_the_environment(self) -> None:
        self.write_script("n6.py", "import json, os\n"
                                   "print(json.dumps({'metric': "
                                   "int(os.environ['EXACTORY_LAB_SEED'])}))\n")
        output = _run_lab_command(["run", "code/n6.py", "--seed", "7"], None, self)
        record = json.loads(output.splitlines()[-1])
        self.assertEqual(record["seed"], 7)
        self.assertEqual(record["metric"], {"metric": 7})

    def test_run_refuses_a_script_outside_the_experiment_directory(self) -> None:
        (self.workspace / "outside.py").write_text("print('no')\n", encoding="utf-8")
        output = _run_lab_command(["run", "../outside.py"], 2, self)
        self.assertIn("experiment", output)


class TestColabBackend(_InsideWorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sync_root = Path(self._temp_dir.name) / "sync"
        self.sync_root.mkdir()
        patcher = unittest.mock.patch.dict(os.environ, {
            "EXACTORY_LAB_COLAB_DIR": str(self.sync_root),
            "EXACTORY_LAB_COLAB_SYNC_WAIT": "0",
            "EXACTORY_LAB_COLAB_POLL": "0.05",
            "EXACTORY_LAB_COLAB_WAIT": "0",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_script(self, name: str, body: str) -> None:
        (self.workspace / "experiment" / "code" / name).write_text(
            body, encoding="utf-8"
        )

    def mark_runner_alive(self) -> None:
        (self.sync_root / "RUNNER_ALIVE").write_text(str(time.time()),
                                                     encoding="utf-8")

    def test_missing_sync_root_yields_an_error_record(self) -> None:
        self.write_script("n1.py", "print('x')\n")
        del os.environ["EXACTORY_LAB_COLAB_DIR"]
        output = _run_lab_command(["run", "code/n1.py", "--backend", "colab"], 1, self)
        record = json.loads(output.splitlines()[-1])
        self.assertEqual(record["error"]["code"], "execution_transport_required")
        self.assertIn("EXACTORY_LAB_COLAB_DIR", record["error"]["message"])
        self.assertFalse((self.sync_root / "jobs").exists())

    def test_timeout_without_a_runner_reports_the_dead_heartbeat(self) -> None:
        self.write_script("n2.py", "print('x')\n")
        output = _run_lab_command(
            ["run", "code/n2.py", "--backend", "colab", "--timeout", "0.2"], 1, self
        )
        record = json.loads(output.splitlines()[-1])
        self.assertEqual(record["error"]["code"], "execution_recovery_required")
        self.assertIn("runner", record["error"]["message"].lower())
        self.assertIn("heartbeat", record["error"]["message"].lower())
        job_dirs = list((self.sync_root / "jobs").iterdir())
        self.assertEqual(len(job_dirs), 1)
        job = json.loads((job_dirs[0] / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["node"], "n2")
        self.assertEqual(job["script"], "code/n2.py")
        self.assertTrue((job_dirs[0] / "READY").is_file())
        self.assertTrue((job_dirs[0] / "code" / "n2.py").is_file())

    def test_serve_scan_processes_one_job_and_writes_done_last(self) -> None:
        from research_harness import execution, remote_execution
        self.write_script("n3.py", "import json\nprint(json.dumps({'metric': 0.9}))\n")
        case = prepare_research(self.workspace)
        admission = admit_lab(case, "code/n3.py", backend="colab", timeout=30)
        claim = execution._claim(case.store, admission["id"], case.store.revision, "runner-once")["result"]
        root, job = remote_execution._publish_job(case.store, claim)
        job_dir = self.sync_root / "jobs" / job["job_id"]
        self.assertFalse(_lab._serve_scan_once(self.sync_root))
        self.assertTrue((job_dir / "PROCESSED").is_file())
        result_dir = self.sync_root / "results" / job["job_id"]
        self.assertFalse((result_dir / "DONE").exists())
        remote_execution._release(case.store, claim, root, job)
        self.assertTrue(_lab._serve_scan_once(self.sync_root))
        self.assertTrue((result_dir / "DONE").is_file())
        record = execution.reconcile_execution(case.store, {"admission_id": admission["id"]},
            expected_revision=case.store.revision, request_id="collect-once")
        self.assertTrue(record["ok"])
        self.assertEqual(record["metric"], {"metric": 0.9})
        self.assertFalse(_lab._serve_scan_once(self.sync_root))

    def test_round_trip_through_a_threaded_runner(self) -> None:
        import threading

        self.write_script("n4.py", "import json\nprint(json.dumps({'metric': 1.5}))\n")
        case = prepare_research(self.workspace)
        admission = admit_lab(case, 'code/n4.py', backend='colab', timeout=10)
        self.mark_runner_alive()

        def run_runner() -> None:
            deadline = time.time() + 10
            while time.time() < deadline:
                if _lab._serve_scan_once(self.sync_root):
                    return
                time.sleep(0.02)

        runner_thread = threading.Thread(target=run_runner, daemon=True)
        runner_thread.start()
        output = _run_lab_command(
            ["run", "code/n4.py", "--admission", admission['id'], "--backend", "colab", "--timeout", "10"], None, self
        )
        runner_thread.join(timeout=10)
        record = json.loads(output.splitlines()[-1])
        self.assertTrue(record["ok"])
        self.assertEqual(record["backend"], "colab")
        self.assertEqual(record["metric"], {"metric": 1.5})
        pulled_record = json.loads(
            (self.workspace / "experiment" / "results" / "n4.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(pulled_record, record)
        self.assertTrue(
            (self.workspace / "experiment" / "logs" / "n4.log").is_file()
        )

    def test_colab_status_reports_liveness(self) -> None:
        _run_lab_command(["colab-status"], 1, self)
        self.mark_runner_alive()
        output = _run_lab_command(["colab-status"], None, self)
        self.assertTrue(json.loads(output)["runner_alive"])


class TestKeys(unittest.TestCase):
    """`keys` reports which credentials are present, so a stage can say what it can run."""

    def setUp(self) -> None:
        # An empty config home, so a credentials file on the developer's machine never
        # leaks into a test that means "nothing is set".
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        self.config_home = Path(config_home.name)
        self.empty_env = {"XDG_CONFIG_HOME": str(self.config_home)}

    def read_keys_report(self) -> dict:
        return json.loads(_run_lab_command(["keys"], None, self))

    def test_keys_reports_every_credential_as_absent_when_none_is_set(self) -> None:
        with unittest.mock.patch.dict(os.environ, self.empty_env, clear=True):
            report = self.read_keys_report()
        self.assertEqual(
            sorted(report),
            ["EXACTORY_API_KEY", "ZENODO_SANDBOX_TOKEN", "ZENODO_TOKEN"],
        )
        for name, credential in report.items():
            with self.subTest(credential=name):
                self.assertFalse(credential["set"])

    def test_keys_reports_the_key_that_exactory_login_stored(self) -> None:
        secret_value = "sk-from-the-file"
        credentials_path = self.config_home / "exactory" / "credentials.json"
        credentials_path.parent.mkdir(parents=True)
        credentials_path.write_text(json.dumps({"api_key": secret_value}))
        with unittest.mock.patch.dict(os.environ, self.empty_env, clear=True):
            output = _run_lab_command(["keys"], None, self)
        self.assertNotIn(secret_value, output)
        self.assertTrue(json.loads(output)["EXACTORY_API_KEY"]["set"])

    def test_keys_names_the_login_command_for_the_exactory_key(self) -> None:
        with unittest.mock.patch.dict(os.environ, self.empty_env, clear=True):
            report = self.read_keys_report()
        self.assertEqual(report["EXACTORY_API_KEY"]["login_command"],
                         "exactory login --email <your email>")
        self.assertNotIn("login_command", report["ZENODO_TOKEN"])

    def test_keys_reports_a_present_credential_without_printing_its_value(self) -> None:
        secret_value = "sk-do-not-print-this"
        with unittest.mock.patch.dict(
            os.environ, {"EXACTORY_API_KEY": secret_value}, clear=True
        ):
            output = _run_lab_command(["keys"], None, self)
        self.assertNotIn(secret_value, output)
        report = json.loads(output)
        self.assertTrue(report["EXACTORY_API_KEY"]["set"])
        self.assertFalse(report["ZENODO_TOKEN"]["set"])

    def test_keys_treats_a_blank_credential_as_absent(self) -> None:
        with unittest.mock.patch.dict(
            os.environ, {"EXACTORY_API_KEY": "   "}, clear=True
        ):
            report = self.read_keys_report()
        self.assertFalse(report["EXACTORY_API_KEY"]["set"])

    def test_keys_names_what_each_credential_unlocks_and_what_runs_without_it(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            report = self.read_keys_report()
        for name, credential in report.items():
            with self.subTest(credential=name):
                self.assertTrue(credential["unlocks"])
                self.assertTrue(credential["without_it"])
                self.assertTrue(credential["create_at"].startswith("https://"))

    def test_keys_runs_outside_a_study_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as scratch_dir:
            original_dir = os.getcwd()
            os.chdir(scratch_dir)
            self.addCleanup(os.chdir, original_dir)
            report = self.read_keys_report()
        self.assertIn("EXACTORY_API_KEY", report)


if __name__ == "__main__":
    unittest.main()
