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

    def run_stop(self, host, script, active, cwd=None):
        command = [sys.executable, str(ROOT / "hooks" / script)]
        if host == "codex":
            command = [sys.executable, str(ROOT / "codex/hook.py"), script]
        payload = {"hook_event_name": "Stop", "cwd": str(cwd or self.workspace), "session_id": "session"}
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

    def exercise_math_cap(self, host):
        from test_math_search_hooks import managed_objective, focus_objective
        controller = managed_objective(self.workspace / "managed", host=host)
        for number in range(39):
            controller.command("hook-stop", {"delivery_id": None, "session_id": host + ":session",
                "turn_id": None, "stop_hook_active": False}, controller.status()["revision"], "stop-%d" % number)
        summary = self.run_stop(host, "continue_attack.py", False)
        self.assertEqual(summary["decision"], "block")
        self.assertIn("safety cap", summary["reason"])
        from test_math_search_hooks import FIXTURES
        state = controller.status()
        alternative = json.loads(json.dumps(state["proposals"]["proposal-000001"]["record"]))
        alternative.update(attack_slug="alternative", relationship="alternative", equivalent_node_ids=["node-000001"])
        alternative["budget"].update(mode="inherit", account_id="account-000001")
        controller.command("propose", {"proposal": alternative, "inputs": []}, state["revision"], "alternative")
        controller.command("review", {"proposal_id": "proposal-000002", "review": FIXTURES["review"](alternative), "inputs": []},
                           state["revision"] + 1, "alternative-review")
        controller.command("admit", {}, state["revision"] + 2, "alternative-admit", "proposal-000002")
        controller.command("replan", {"route_orders": [], "progress_acceptance_ids": [],
            "reason": "An admitted alternative remains available after explicit operator resume"}, state["revision"] + 3, "alternative-replan")
        self.assertEqual(controller.status()["nodes"]["node-000002"]["account_id"], "account-000001")
        self.assertIsNone(self.run_stop(host, "continue_attack.py", False, controller.root / "alternative"))
        focus_objective(controller, host, "session", request="refocus")
        for flag in (True, False, None, False):
            self.assertIsNone(self.run_stop(host, "continue_attack.py", flag))
        state = controller.status()
        self.assertEqual(state["execution_status"], "paused")
        self.assertEqual(state["control"]["stop_count"], 40)
        controller.command("resume", {"objective_id": state["objective_id"], "session_id": host + ":session",
            "message_id": "explicit-user-resume", "instruction": "Resume this objective for another bounded interval",
            "provenance": {"source": "operator", "actor_id": "fixture-user", "attestation_id": "explicit-user-resume"}},
            state["revision"], "resume")
        continued = self.run_stop(host, "continue_attack.py", False)
        self.assertEqual(continued["decision"], "block")
        self.assertNotIn("safety cap", continued["reason"])
        self.assertEqual(controller.status()["control"]["stop_count"], 1)

    def test_claude_math_cap_requires_explicit_operator_resume(self):
        self.exercise_math_cap("claude")

    def test_codex_math_cap_requires_explicit_operator_resume(self):
        self.exercise_math_cap("codex")

    def test_claude_autopilot_cap_pauses_then_restarts_on_user_turn(self):
        self.exercise_cap("claude", "continue_autopilot.py")

    def test_codex_autopilot_cap_pauses_then_restarts_on_user_turn(self):
        self.exercise_cap("codex", "continue_autopilot.py")


if __name__ == "__main__":
    unittest.main()
