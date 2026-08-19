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


if __name__ == "__main__":
    unittest.main()
