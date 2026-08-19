"""Tests for bin/exactory-lab: the study workspace, its state machine, and execution."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

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
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        if expected_exit_code is None:
            args.handler(args)
        else:
            with test_case.assertRaises(SystemExit) as caught:
                args.handler(args)
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


class TestInit(_WorkspaceTestCase):
    def test_init_creates_the_study_layout(self) -> None:
        self.init_workspace()
        for dir_name in _STUDY_DIR_NAMES:
            self.assertTrue((self.workspace / dir_name).is_dir(), dir_name)
        self.assertFalse((self.workspace / "draft").exists())

    def test_init_writes_the_study_state(self) -> None:
        self.init_workspace()
        state = self.read_study_state()
        self.assertEqual(state["version"], 1)
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
        with self.assertRaises(SystemExit) as caught:
            _lab._build_parser().parse_args(["state", "set", "--stage", "escape"])
        self.assertEqual(caught.exception.code, 2)

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


if __name__ == "__main__":
    unittest.main()
