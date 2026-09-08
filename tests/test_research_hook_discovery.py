"""Interpreter source is data; explicit paths and managed CWD remain guarded."""

import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


HOOK = Path(__file__).resolve().parents[1] / "hooks/guard_attack_files.py"


class HookOperandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cwd = Path(temporary.name)
        self.managed = self.cwd / "managed"
        (self.managed / ".search").mkdir(parents=True)
        (self.managed / ".search/tree.json").write_text("{}")

    def invoke(self, command, cwd=None):
        process = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": "Bash",
            "cwd": str(cwd or self.cwd), "tool_input": {"command": command}}), capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout) if process.stdout else None

    def program(self):
        return "padding = '" + "x" * 400 + "'; value = 'ordinary/name'; print('example')"

    def test_long_inline_source_is_not_a_managed_filesystem_operand(self):
        for executable in ("python3", sys.executable, "env python3"):
            with self.subTest(executable=executable):
                self.assertIsNone(self.invoke(executable + " -c " + shlex.quote(self.program())))

    def test_managed_cwd_and_explicit_script_paths_still_require_authority(self):
        command = "python3 -c " + shlex.quote(self.program())
        denied = self.invoke(command, self.managed)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("Unsupported managed shell", denied)
        self.assertNotIn("File name too long", denied)
        script = self.managed / "program.py"
        script.write_text("print('authored fixture')")
        result = self.invoke("python3 " + shlex.quote(str(script)))
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_owned_target_after_data_and_broken_registration_are_not_hidden(self):
        command = "python3 -c " + shlex.quote(self.program()) + " " + shlex.quote(str(self.managed / ".search/tree.json"))
        self.assertEqual(self.invoke(command)["hookSpecificOutput"]["permissionDecision"], "deny")
        (self.cwd / ".exactory").mkdir()
        (self.cwd / ".exactory/math-search.json").write_text("{}")
        result = self.invoke("python3 -c " + shlex.quote(self.program()))
        self.assertIn("Malformed discovery registry", result["hookSpecificOutput"]["permissionDecisionReason"])
