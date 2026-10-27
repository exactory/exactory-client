"""Run the registered commands even when the loaded plugin version has disappeared."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
UPDATE_MESSAGE = (
    "[exactory] The Exactory plugin was updated during this session; start a new session.\n"
)
CONFIGS = {"claude": "hooks/hooks.json", "codex": "codex/hooks.json"}
MISSING_EXIT = {"PreToolUse": 2, "PostToolUse": 1, "SessionStart": 1, "Stop": 0}
SCRIPT_PATH = re.compile(r'\$\{(?:CLAUDE_PLUGIN_ROOT|PLUGIN_ROOT)\}/([^\"]+\.py)')
PROBE = """import json, os, sys
print(json.dumps({"argv": sys.argv[1:], "stdin": json.load(sys.stdin),
                  "cwd": os.getcwd(), "python": sys.version_info[:2]}))
sys.stderr.write(os.environ.get("HOOK_TEST_STDERR", ""))
sys.exit(int(os.environ.get("HOOK_TEST_EXIT", "0")))
"""


class TestHookStartup(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="exactory hook startup ")
        self.addCleanup(scratch.cleanup)
        self.workspace = Path(scratch.name)
        self.version = self.workspace / "plugin cache's A"
        self.launchers = self.workspace / "bin"
        self.launchers.mkdir()
        (self.launchers / "python3").symlink_to(sys.executable)

    def find_handlers(self):
        for host, config_path in CONFIGS.items():
            config = json.loads((ROOT / config_path).read_text())
            for event, groups in config["hooks"].items():
                for group in groups:
                    for handler in group["hooks"]:
                        yield host, event, handler

    def invoke(self, handler, version=None, payload=None, extra_env=None):
        version = self.version if version is None else version
        env = {
            **os.environ,
            "PLUGIN_ROOT": str(version),
            "CLAUDE_PLUGIN_ROOT": str(version),
            "EXACTORY_API_KEY": "",
            "PATH": str(self.launchers) + os.pathsep + os.environ["PATH"],
            **(extra_env or {}),
        }
        return subprocess.run(
            ["/bin/sh", "-c", handler["command"]], cwd=self.workspace, env=env,
            input=json.dumps(payload or {"fixture": "unchanged input"}),
            capture_output=True, text=True, timeout=10,
        )

    def assert_update_diagnostic(self, process, code):
        self.assertEqual(process.returncode, code, process.stderr)
        self.assertEqual(process.stderr, UPDATE_MESSAGE)
        self.assertEqual(process.stdout, "")

    def assert_missing_event(self, event, code):
        handlers = [(host, handler) for host, name, handler in self.find_handlers() if name == event]
        self.assertEqual({host for host, _ in handlers}, {"claude", "codex"})
        for host, handler in handlers:
            with self.subTest(host=host, event=event, command=handler["command"]):
                self.assert_update_diagnostic(self.invoke(handler), code)

    def test_missing_stop_scripts_allow_session_to_stop(self):
        self.assert_missing_event("Stop", 0)

    def test_missing_pre_tool_use_scripts_refuse_tools(self):
        self.assert_missing_event("PreToolUse", 2)

    def test_missing_post_tool_use_scripts_report_nonblocking_errors(self):
        self.assert_missing_event("PostToolUse", 1)

    def test_missing_session_start_scripts_report_nonblocking_errors(self):
        self.assert_missing_event("SessionStart", 1)

    def test_missing_version_reports_update_for_every_entrypoint(self):
        for host, event, handler in self.find_handlers():
            with self.subTest(host=host, event=event, command=handler["command"]):
                self.assert_update_diagnostic(self.invoke(handler), MISSING_EXIT[event])

    def test_missing_entrypoint_in_existing_root_reports_update(self):
        (self.version / "hooks").mkdir(parents=True)
        (self.version / "codex").mkdir()
        for host, event, handler in self.find_handlers():
            with self.subTest(host=host, event=event, command=handler["command"]):
                self.assert_update_diagnostic(self.invoke(handler), MISSING_EXIT[event])

    def test_entrypoints_preserve_script_execution(self):
        payload = {"hook_event_name": "PreToolUse", "tool_input": {"command": "echo 'sample'"}}
        for host, event, handler in self.find_handlers():
            with self.subTest(host=host, event=event, command=handler["command"]):
                relative = SCRIPT_PATH.search(handler["command"]).group(1)
                script = self.version / relative
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(PROBE)
                process = self.invoke(handler, payload=payload)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(process.stderr, "")
                result = json.loads(process.stdout)
                expected_args = []
                if host == "codex" and relative == "codex/hook.py":
                    expected_args = [shlex.split(handler["command"])[-1]]
                self.assertEqual(result, {
                    "argv": expected_args, "stdin": payload,
                    "cwd": str(self.workspace.resolve()), "python": list(sys.version_info[:2]),
                })

    def test_script_errors_are_not_reported_as_plugin_updates(self):
        for host, event, handler in self.find_handlers():
            relative = SCRIPT_PATH.search(handler["command"]).group(1)
            script = self.version / relative
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(PROBE)
            for code in (2, 17):
                with self.subTest(host=host, event=event, script=relative, exit=code):
                    process = self.invoke(handler, extra_env={
                        "HOOK_TEST_STDERR": "original hook failure\n", "HOOK_TEST_EXIT": str(code),
                    })
                    self.assertEqual(process.returncode, code)
                    self.assertEqual(process.stderr, "original hook failure\n")
                    self.assertEqual(json.loads(process.stdout)["stdin"], {"fixture": "unchanged input"})

    def test_real_guard_still_allows_and_refuses_tools(self):
        attack = self.workspace / "attack/sample"
        attack.mkdir(parents=True)
        (attack / "problem.json").write_text('{"claim": "sample"}')
        (attack / "journal.jsonl").write_text("")
        for host, event, handler in self.find_handlers():
            if event != "PreToolUse" or "guard_attack_files.py" not in handler["command"]:
                continue
            for filename, denied in (("notes.md", False), ("attack/sample/tasks.json", True)):
                with self.subTest(host=host, filename=filename):
                    process = self.invoke(handler, version=ROOT, payload={
                        "hook_event_name": event, "tool_name": "Write", "session_id": "startup-test",
                        "cwd": str(self.workspace), "tool_input": {"file_path": filename},
                    })
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertEqual(process.stderr, "")
                    if denied:
                        decision = json.loads(process.stdout)["hookSpecificOutput"]
                        self.assertEqual(decision["permissionDecision"], "deny")
                        self.assertIn("harness", decision["permissionDecisionReason"])
                    else:
                        self.assertEqual(process.stdout, "")

    def test_generated_hook_files_are_byte_identical(self):
        namespace = runpy.run_path(str(ROOT / "codex/generate.py"))
        files = namespace["build_files"]()
        self.assertIn(ROOT / "hooks/hooks.json", files)
        self.assertIn(ROOT / "codex/hooks.json", files)
        for path, contents in files.items():
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertEqual(path.read_bytes(), contents.encode("utf-8"))

    def test_check_rejects_unwrapped_claude_command(self):
        namespace = runpy.run_path(str(ROOT / "codex/generate.py"))
        root = self.workspace / "generator"
        (root / "skills").mkdir(parents=True)
        (root / "hooks").mkdir()
        config_path = root / "hooks/hooks.json"
        config_path.write_bytes((ROOT / "hooks/hooks.json").read_bytes())
        with patch.dict(namespace["main"].__globals__, {"ROOT": root}):
            with patch.object(sys, "argv", ["generate.py"]):
                self.assertEqual(namespace["main"](), 0)
            config = json.loads(config_path.read_text())
            config["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = (
                'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/guard_attack_files.py"'
            )
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            before = config_path.read_bytes()
            errors = io.StringIO()
            with patch.object(sys, "argv", ["generate.py", "--check"]):
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(namespace["main"](), 1)
            self.assertIn("hooks/hooks.json", errors.getvalue())
            self.assertEqual(config_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
