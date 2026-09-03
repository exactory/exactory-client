"""Tests for bin/exactory-math: the two things it adds over the math-solver harness."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SKILL_DIR = _PLUGIN_ROOT / "skills" / "math-solver"


def _load_bin_module(command_name: str, module_name: str):
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(_PLUGIN_ROOT / "bin" / command_name)
    )
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_math = _load_bin_module("exactory-math", "exactory_math")


def _run_math_command(argv: list[str]) -> tuple[int, str]:
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        exit_code = _math.main(["exactory-math", *argv])
    return exit_code, sink.getvalue()


class SkillDirTest(unittest.TestCase):
    def test_skill_dir_prints_the_directory_that_holds_the_skill_files(self):
        exit_code, output = _run_math_command(["skill-dir"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.strip(), str(_SKILL_DIR))

    def test_help_lists_the_harness_commands_and_skill_dir(self):
        exit_code, output = _run_math_command(["--help"])

        self.assertEqual(exit_code, 0)
        self.assertIn("check-problem", output)
        self.assertIn("skill-dir", output)


class HarnessDispatchTest(unittest.TestCase):
    def test_a_slug_named_skill_dir_reaches_the_harness(self):
        with tempfile.TemporaryDirectory() as attack_root:
            exit_code, output = _run_math_command(
                ["--attack-root", attack_root, "init", "skill-dir"]
            )

            self.assertEqual(exit_code, 0)
            self.assertNotIn(str(_SKILL_DIR), output)
            self.assertTrue((Path(attack_root) / "skill-dir" / "problem.json").exists())

    def test_a_strategy_named_skill_dir_reaches_the_harness(self):
        with tempfile.TemporaryDirectory() as attack_root:
            _run_math_command(["--attack-root", attack_root, "init", "demo"])

            exit_code, output = _run_math_command(
                ["--attack-root", attack_root, "fail", "demo", "skill-dir"]
            )

            self.assertEqual(exit_code, 1)
            self.assertNotIn(str(_SKILL_DIR), output)


if __name__ == "__main__":
    unittest.main()
