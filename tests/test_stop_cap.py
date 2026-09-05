"""Stop caps return control to the user in both plugin hosts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class TestStopCap(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace = Path(scratch.name)
        attack = self.workspace / "attack/sample"
        attack.mkdir(parents=True)
        (attack / "problem.json").write_text("{}")
        (self.workspace / ".exactory").mkdir()
        (self.workspace / ".exactory/study.json").write_text(json.dumps({
            "slug": "sample", "stage": "experiment", "status": "running",
            "autopilot": True, "waiting": None,
        }))

    def run_stop(self, host, script, active):
        command = [sys.executable, str(ROOT / "hooks" / script)]
        if host == "codex":
            command = [sys.executable, str(ROOT / "codex/hook.py"), script]
        payload = {"hook_event_name": "Stop", "cwd": str(self.workspace)}
        if active is not None:
            payload["stop_hook_active"] = active
        result = subprocess.run(
            command, input=json.dumps(payload), text=True, capture_output=True,
            timeout=30, env={**os.environ, "EXACTORY_ATTACK_MAX": "2",
                             "EXACTORY_AUTOPILOT_MAX": "2"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def exercise_cap(self, host, script):
        # A normal stop must not reset progress just because this flag is false.
        for active in (False, False):
            result = self.run_stop(host, script, active)
            self.assertEqual(result["decision"], "block")
            self.assertNotIn("safety cap", result["reason"])
        summary = self.run_stop(host, script, True)
        self.assertEqual(summary["decision"], "block")
        self.assertIn("safety cap", summary["reason"])
        # The summary's completion and repeated deliveries must return control.
        self.assertIsNone(self.run_stop(host, script, True))
        self.assertIsNone(self.run_stop(host, script, True))
        # Missing host metadata cannot be evidence of a new user turn.
        self.assertIsNone(self.run_stop(host, script, None))
        # A new user turn (also the first stop after resume) gets a fresh budget.
        resumed = self.run_stop(host, script, False)
        self.assertEqual(resumed["decision"], "block")
        self.assertNotIn("safety cap", resumed["reason"])
        continued = self.run_stop(host, script, True)
        self.assertNotIn("safety cap", continued["reason"])
        self.assertIn("safety cap", self.run_stop(host, script, True)["reason"])
        self.assertIsNone(self.run_stop(host, script, True))

    def test_claude_math_cap_pauses_then_restarts_on_user_turn(self):
        self.exercise_cap("claude", "continue_attack.py")

    def test_codex_math_cap_pauses_then_restarts_on_user_turn(self):
        self.exercise_cap("codex", "continue_attack.py")

    def test_claude_autopilot_cap_pauses_then_restarts_on_user_turn(self):
        self.exercise_cap("claude", "continue_autopilot.py")

    def test_codex_autopilot_cap_pauses_then_restarts_on_user_turn(self):
        self.exercise_cap("codex", "continue_autopilot.py")


if __name__ == "__main__":
    unittest.main()
