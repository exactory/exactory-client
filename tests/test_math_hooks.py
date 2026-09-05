"""Behavioral tests for the math-solver hooks, run as subprocesses on synthetic payloads.

Three hooks guard an attack workspace (`attack/<slug>/`): the files the harness
writes are refused to every other tool, a unit is written only after the
cash-out started and drafted only after its check, and the session does not
stop while an attack is open.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_GUARD_SCRIPT_PATH = _PLUGIN_ROOT / "hooks" / "guard_attack_files.py"
_UNIT_FLOW_SCRIPT_PATH = _PLUGIN_ROOT / "hooks" / "enforce_unit_flow.py"
_CONTINUE_SCRIPT_PATH = _PLUGIN_ROOT / "hooks" / "continue_attack.py"
_ACTIVITY_SCRIPT_PATH = _PLUGIN_ROOT / "hooks" / "record_attack_activity.py"
_RESUME_SCRIPT_PATH = _PLUGIN_ROOT / "hooks" / "resume_attack.py"

_UNIT_RECORD = {
    "statement": "For every n the bound holds.",
    "form": "quantitative-improvement",
    "evidence": "units/1/proof.md",
    "novelty": "searched by statement; no hit",
    "moves": [1],
    "costs": [],
}


def _run_hook(script_path: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script_path)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )


def _build_attack(root: Path, slug: str = "sample") -> Path:
    """An attack workspace as `exactory-math init` lays it out."""
    workspace = root / "attack" / slug
    for name in ("study", "deterministic", "units"):
        (workspace / name).mkdir(parents=True)
    (workspace / "problem.json").write_text(json.dumps({"claim": ""}))
    (workspace / "novelty.md").write_text("")
    (workspace / "journal.jsonl").write_text("")
    return workspace


def _read_denial_reason(test: unittest.TestCase, proc: subprocess.CompletedProcess) -> str:
    decision = json.loads(proc.stdout)["hookSpecificOutput"]
    test.assertEqual(decision["hookEventName"], "PreToolUse")
    test.assertEqual(decision["permissionDecision"], "deny")
    return decision["permissionDecisionReason"]


class TestGuardAttackFiles(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "work"
        self.workspace = _build_attack(self.root)
        self.elsewhere = Path(scratch.name) / "elsewhere"
        self.elsewhere.mkdir()

    def _run_file_tool(self, path: str, tool: str = "Write") -> subprocess.CompletedProcess:
        return _run_hook(_GUARD_SCRIPT_PATH, {
            "tool_name": tool,
            "tool_input": {"file_path": path, "content": "{}"},
            "cwd": str(self.root),
        })

    def _run_bash(self, command: str) -> subprocess.CompletedProcess:
        return _run_hook(_GUARD_SCRIPT_PATH, {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(self.root),
        })

    def test_denies_a_write_to_the_journal(self) -> None:
        reason = _read_denial_reason(self, self._run_file_tool(str(self.workspace / "journal.jsonl")))
        self.assertIn("journal add", reason)

    def test_denies_an_edit_to_the_openings(self) -> None:
        reason = _read_denial_reason(
            self, self._run_file_tool(str(self.workspace / "openings.json"), tool="Edit")
        )
        self.assertIn("plan", reason)

    def test_denies_a_write_to_the_parent_link(self) -> None:
        reason = _read_denial_reason(self, self._run_file_tool(str(self.workspace / "parent.json")))
        self.assertIn("init", reason)

    def test_denies_a_write_to_the_tasks_and_to_the_activity_log(self) -> None:
        tasks_reason = _read_denial_reason(self, self._run_file_tool(str(self.workspace / "tasks.json")))
        self.assertIn("task", tasks_reason)
        activity_reason = _read_denial_reason(
            self, self._run_file_tool(str(self.workspace / "activity.jsonl"))
        )
        self.assertIn("activity", activity_reason)

    def test_denies_a_write_to_a_result_file(self) -> None:
        path = self.workspace / "deterministic" / "enumeration-run-1" / "result.json"
        reason = _read_denial_reason(self, self._run_file_tool(str(path)))
        self.assertIn("verify", reason)

    def test_denies_a_write_to_a_unit_stamp_and_to_the_finished_record(self) -> None:
        stamp_reason = _read_denial_reason(
            self, self._run_file_tool(str(self.workspace / "units" / "1" / "check-unit.json"))
        )
        self.assertIn("check-unit", stamp_reason)
        finished_reason = _read_denial_reason(
            self, self._run_file_tool(str(self.workspace / "units" / "FINISHED.json"))
        )
        self.assertIn("finish", finished_reason)

    def test_allows_the_files_the_solver_owns(self) -> None:
        for relative in (
            "problem.json",
            "novelty.md",
            "study/problem.md",
            "preconditions.json",
            "ranking.json",
            "units/INVENTORY.md",
            "units/1/unit.json",
            "deterministic/enumeration-run-1/check.sh",
        ):
            with self.subTest(file=relative):
                self.assertEqual(self._run_file_tool(str(self.workspace / relative)).stdout, "")

    def test_resolves_a_relative_path_against_cwd(self) -> None:
        self.assertEqual(self._run_file_tool("attack/sample/problem.json").stdout, "")
        _read_denial_reason(self, self._run_file_tool("attack/sample/journal.jsonl"))

    def test_ignores_a_journal_outside_an_attack_workspace(self) -> None:
        self.assertEqual(self._run_file_tool(str(self.elsewhere / "journal.jsonl")).stdout, "")
        stray = self.root / "attack" / "stray"
        stray.mkdir()
        self.assertEqual(self._run_file_tool(str(stray / "journal.jsonl")).stdout, "")

    def test_denies_a_shell_redirect_into_the_journal(self) -> None:
        for command in (
            "echo '{}' >> attack/sample/journal.jsonl",
            "echo '{}' > attack/sample/journal.jsonl",
            "printf '{}' >attack/sample/journal.jsonl",
        ):
            with self.subTest(command=command):
                self.assertIn("journal add", _read_denial_reason(self, self._run_bash(command)))

    def test_denies_tee_copy_move_and_sed_in_place(self) -> None:
        for command in (
            "cat new.json | tee attack/sample/openings.json",
            "cp new.json attack/sample/journal.jsonl",
            "mv new.json %s" % (self.workspace / "units" / "FINISHED.json"),
            "sed -i '' 's/false/true/' attack/sample/journal.jsonl",
            "rm attack/sample/deterministic/enumeration-run-1/result.json",
        ):
            with self.subTest(command=command):
                _read_denial_reason(self, self._run_bash(command))

    def test_allows_reading_the_harness_files_from_the_shell(self) -> None:
        for command in (
            "cat attack/sample/journal.jsonl",
            "grep -c closes attack/sample/journal.jsonl",
            "python3 -m json.tool attack/sample/openings.json",
            "exactory-math journal add sample --json '{\"move\": 1}'",
            "ls attack/sample/units",
        ):
            with self.subTest(command=command):
                self.assertEqual(self._run_bash(command).stdout, "")

    def test_other_tools_pass(self) -> None:
        completed = _run_hook(_GUARD_SCRIPT_PATH, {
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.workspace / "journal.jsonl")},
            "cwd": str(self.root),
        })
        self.assertEqual(completed.stdout, "")


class TestEnforceUnitFlow(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "work"
        self.workspace = _build_attack(self.root)
        self.unit_dir = self.workspace / "units" / "1"
        self.unit_dir.mkdir()
        (self.unit_dir / "unit.json").write_text(json.dumps(_UNIT_RECORD))

    def _run_write(self, path: Path) -> subprocess.CompletedProcess:
        return _run_hook(_UNIT_FLOW_SCRIPT_PATH, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(path), "content": "text"},
            "cwd": str(self.root),
        })

    def _write_inventory(self) -> None:
        (self.workspace / "units" / "INVENTORY.md").write_text("# Inventory: sample\n")

    def _write_stamp(self, digest: str | None = None) -> None:
        if digest is None:
            digest = hashlib.sha256((self.unit_dir / "unit.json").read_bytes()).hexdigest()
        (self.unit_dir / "check-unit.json").write_text(json.dumps({"unit_sha256": digest}))

    def test_denies_a_unit_write_before_the_inventory(self) -> None:
        reason = _read_denial_reason(self, self._run_write(self.unit_dir / "unit.json"))
        self.assertIn("stall", reason)

    def test_allows_a_unit_write_after_the_inventory(self) -> None:
        self._write_inventory()
        self.assertEqual(self._run_write(self.unit_dir / "unit.json").stdout, "")
        self.assertEqual(self._run_write(self.unit_dir / "proof.md").stdout, "")

    def test_denies_the_draft_before_check_unit(self) -> None:
        self._write_inventory()
        reason = _read_denial_reason(self, self._run_write(self.unit_dir / "draft.md"))
        self.assertIn("check-unit sample 1", reason)

    def test_denies_the_draft_when_the_stamp_is_stale(self) -> None:
        self._write_inventory()
        self._write_stamp("0" * 64)
        reason = _read_denial_reason(self, self._run_write(self.unit_dir / "evaluation.md"))
        self.assertIn("check-unit sample 1", reason)

    def test_allows_the_draft_and_the_evaluation_when_the_stamp_matches(self) -> None:
        self._write_inventory()
        self._write_stamp()
        self.assertEqual(self._run_write(self.unit_dir / "draft.md").stdout, "")
        self.assertEqual(self._run_write(self.unit_dir / "evaluation.md").stdout, "")

    def test_allows_the_consolidation_record_before_the_inventory(self) -> None:
        self.assertEqual(self._run_write(self.workspace / "units" / "consolidation.md").stdout, "")

    def test_ignores_files_outside_units(self) -> None:
        self.assertEqual(self._run_write(self.workspace / "problem.json").stdout, "")
        self.assertEqual(self._run_write(self.root / "units" / "1" / "draft.md").stdout, "")


class TestContinueAttack(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "work"
        self.workspace = _build_attack(self.root)
        self.elsewhere = Path(scratch.name) / "elsewhere"
        self.elsewhere.mkdir()

    def _run_stop(self, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return _run_hook(_CONTINUE_SCRIPT_PATH, {
            "cwd": str(cwd or self.root),
            "stop_hook_active": False,
        })

    def read_counter(self) -> int:
        return int((self.workspace / ".continue_count").read_text())

    def test_an_open_attack_blocks_the_stop(self) -> None:
        decision = json.loads(self._run_stop().stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("attack/sample", decision["reason"])
        self.assertIn("exactory-math finish sample", decision["reason"])
        self.assertEqual(self.read_counter(), 1)

    def test_a_finished_attack_allows_the_stop(self) -> None:
        (self.workspace / "units" / "FINISHED.json").write_text(json.dumps({"outcome": "cashed-out", "units": []}))
        self.assertEqual(self._run_stop().stdout, "")

    def test_no_workspace_allows_the_stop(self) -> None:
        self.assertEqual(self._run_stop(self.elsewhere).stdout, "")

    def test_the_reason_reports_where_the_attack_stands(self) -> None:
        (self.workspace / "journal.jsonl").write_text('{"move": 1}\n{"move": 2}\n')
        (self.workspace / "units" / "INVENTORY.md").write_text("# Inventory\n")
        reason = json.loads(self._run_stop().stdout)["reason"]
        self.assertIn("2 moves", reason)
        self.assertIn("inventory written", reason)

    def test_the_reason_carries_the_next_step_the_harness_reports(self) -> None:
        reason = json.loads(self._run_stop().stdout)["reason"]
        self.assertIn("next: fill problem.json and run check-problem", reason)

    def test_finds_the_workspace_from_a_subdirectory(self) -> None:
        decision = json.loads(self._run_stop(self.workspace / "deterministic").stdout)
        self.assertEqual(decision["decision"], "block")

    def test_names_every_open_attack(self) -> None:
        _build_attack(self.root, "second")
        reason = json.loads(self._run_stop().stdout)["reason"]
        self.assertIn("attack/sample", reason)
        self.assertIn("attack/second", reason)

    def test_the_safety_cap_requests_a_summary(self) -> None:
        (self.workspace / ".continue_count").write_text("40")
        decision = json.loads(self._run_stop().stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("cap", decision["reason"].lower())


class TestRecordAttackActivity(unittest.TestCase):
    """The autosave: every tool call that touched an attack workspace lands in its
    activity.jsonl, so a resumed session sees what the previous one was doing."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "work"
        self.workspace = _build_attack(self.root)

    def _run(self, tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
        return _run_hook(_ACTIVITY_SCRIPT_PATH, {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_response": {"success": True},
            "cwd": str(self.root),
        })

    def read_log(self) -> list:
        path = self.workspace / "activity.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_a_write_inside_the_workspace_is_recorded(self) -> None:
        completed = self._run("Write", {"file_path": str(self.workspace / "deterministic" / "run-1" / "check.sh")})
        self.assertEqual(completed.stdout, "")
        entries = self.read_log()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool"], "Write")
        self.assertEqual(entries[0]["target"], "deterministic/run-1/check.sh")
        self.assertRegex(entries[0]["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_an_edit_with_a_relative_path_is_recorded(self) -> None:
        self._run("Edit", {"file_path": "attack/sample/problem.json"})
        self.assertEqual(self.read_log()[0]["target"], "problem.json")

    def test_a_harness_command_is_recorded_under_its_slug(self) -> None:
        self._run("Bash", {"command": "exactory-math journal add sample --json '{\"move\": 1}'"})
        entries = self.read_log()
        self.assertEqual(entries[0]["tool"], "Bash")
        self.assertEqual(entries[0]["target"], "exactory-math journal add sample")

    def test_a_shell_command_naming_a_workspace_path_is_recorded(self) -> None:
        self._run("Bash", {"command": "cat attack/sample/study/problem.md"})
        self.assertEqual(self.read_log()[0]["target"], "cat attack/sample/study/problem.md")

    def test_a_long_command_is_cut(self) -> None:
        self._run("Bash", {"command": "python3 attack/sample/deterministic/run-1/search.py " + "x" * 300})
        self.assertLessEqual(len(self.read_log()[0]["target"]), 160)

    def test_a_tool_call_outside_the_workspace_is_not_recorded(self) -> None:
        self._run("Write", {"file_path": str(self.root / "notes.md")})
        self._run("Bash", {"command": "ls"})
        self.assertEqual(self.read_log(), [])

    def test_the_log_keeps_the_last_two_hundred_entries(self) -> None:
        for number in range(205):
            self._run("Write", {"file_path": str(self.workspace / ("file-%d.md" % number))})
        entries = self.read_log()
        self.assertEqual(len(entries), 200)
        self.assertEqual(entries[-1]["target"], "file-204.md")
        self.assertEqual(entries[0]["target"], "file-5.md")


class TestResumeAttack(unittest.TestCase):
    """At session start, an open attack under the working directory is reported with
    the harness's status, so the session resumes it instead of starting over."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "work"
        self.workspace = _build_attack(self.root)
        self.elsewhere = Path(scratch.name) / "elsewhere"
        self.elsewhere.mkdir()

    def _run_start(self, cwd: Path | None = None, source: str = "startup") -> subprocess.CompletedProcess:
        return _run_hook(_RESUME_SCRIPT_PATH, {
            "cwd": str(cwd or self.root),
            "source": source,
            "hook_event_name": "SessionStart",
        })

    def _read_context(self, proc: subprocess.CompletedProcess) -> str:
        output = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        return output["additionalContext"]

    def test_an_open_attack_is_reported_with_its_status(self) -> None:
        context = self._read_context(self._run_start())
        self.assertIn("attack/sample", context)
        self.assertIn("stage 2 (set the problem)", context)
        self.assertIn("next: fill problem.json and run check-problem", context)
        self.assertIn("/exactory:math-solver", context)

    def test_the_context_says_to_resume_and_not_to_restart(self) -> None:
        context = self._read_context(self._run_start())
        self.assertIn("resume", context.lower())
        self.assertIn("stage 0", context)

    def test_a_finished_attack_is_silent(self) -> None:
        (self.workspace / "units" / "FINISHED.json").write_text(json.dumps({"outcome": "cashed-out", "units": []}))
        self.assertEqual(self._run_start().stdout, "")

    def test_no_workspace_is_silent(self) -> None:
        self.assertEqual(self._run_start(self.elsewhere).stdout, "")

    def test_every_open_attack_is_reported(self) -> None:
        _build_attack(self.root, "second")
        context = self._read_context(self._run_start())
        self.assertIn("attack/sample", context)
        self.assertIn("attack/second", context)

    def test_a_compaction_reports_it_too(self) -> None:
        context = self._read_context(self._run_start(source="compact"))
        self.assertIn("attack/sample", context)


if __name__ == "__main__":
    unittest.main()
