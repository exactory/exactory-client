"""Behavioral tests for the math-solver hooks, run as subprocesses on synthetic payloads.

Three hooks guard an attack workspace (`attack/<slug>/`): the files the harness
writes are refused to every other tool, a unit is written only after the
cash-out started and drafted only after its check, and the session does not
stop while an attack is open.
"""

from __future__ import annotations

import errno
import hashlib
import importlib.util
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _overlong_paths(root: Path) -> dict[str, Path]:
    return {
        "component": root / ("x" * 300) / "notes.md",
        "whole_path": root.joinpath(*(["segment"] * (os.pathconf(root, "PC_PATH_MAX") // 8 + 1)), "notes.md"),
    }


def _assert_overlong_path(test: unittest.TestCase, path: Path) -> None:
    with test.assertRaises(OSError) as raised:
        os.stat(path)
    test.assertEqual(raised.exception.errno, errno.ENAMETOOLONG)


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

    def test_allows_a_heredoc_with_an_overlong_quote_swallowed_word(self) -> None:
        body = "Jordan's characterization: " + "x" * 320 + "/Jordan's result.\n"
        completed = self._run_bash("cat > out.md <<'EOF'\n" + body + "EOF\n")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_an_overlong_shell_word_does_not_hide_a_later_owned_file(self) -> None:
        body = "Jordan's characterization: " + "x" * 320 + "/Jordan's result.\n"
        command = "cat > out.md <<'EOF'\n" + body + "EOF\nprintf '{}' > attack/sample/journal.jsonl\n"
        self.assertIn("journal add", _read_denial_reason(self, self._run_bash(command)))

    def test_allows_an_overlong_whole_path_in_a_single_line_shell_write(self) -> None:
        path = _overlong_paths(self.root)["whole_path"]
        _assert_overlong_path(self, path)
        completed = self._run_bash("printf x > " + str(path))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_allows_overlong_parent_paths_in_write_and_edit(self) -> None:
        for kind, path in _overlong_paths(self.root).items():
            _assert_overlong_path(self, path)
            for tool in ("Write", "Edit"):
                with self.subTest(kind=kind, tool=tool):
                    completed = self._run_file_tool(str(path), tool=tool)
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, "")

    def test_allows_overlong_paths_beneath_a_managed_tree(self) -> None:
        from test_math_search_hooks import managed_objective

        managed_root = self.root / "managed"
        managed_objective(managed_root)
        for kind, path in _overlong_paths(managed_root / "attempt").items():
            _assert_overlong_path(self, path)
            for tool in ("Bash", "Write", "Edit"):
                with self.subTest(kind=kind, tool=tool):
                    completed = (self._run_bash("printf x > " + str(path)) if tool == "Bash"
                                 else self._run_file_tool(str(path), tool=tool))
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, "")

    def test_an_overlong_single_line_word_does_not_hide_a_later_owned_file(self) -> None:
        for kind, path in _overlong_paths(self.root).items():
            with self.subTest(kind=kind):
                _assert_overlong_path(self, path)
                command = "printf '%s' " + str(path) + " > attack/sample/journal.jsonl"
                completed = self._run_bash(command)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stderr, "")
                self.assertIn("journal add", _read_denial_reason(self, completed))

    def test_an_overlong_batch_target_does_not_hide_a_later_owned_file(self) -> None:
        for kind, path in _overlong_paths(self.root).items():
            with self.subTest(kind=kind):
                _assert_overlong_path(self, path)
                completed = _run_hook(_GUARD_SCRIPT_PATH, {
                    "cwd": str(self.root),
                    "_math_guard_batch": [
                        {"tool_name": "Write", "tool_input": {"file_path": str(path)}, "cwd": str(self.root)},
                        {"tool_name": "Write", "tool_input": {"file_path": "attack/sample/journal.jsonl"},
                         "cwd": str(self.root)},
                    ],
                })
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stderr, "")
                self.assertIn("journal add", _read_denial_reason(self, completed))

    def test_other_tools_pass(self) -> None:
        completed = _run_hook(_GUARD_SCRIPT_PATH, {
            "tool_name": "Read",
            "tool_input": {"file_path": str(self.workspace / "journal.jsonl")},
            "cwd": str(self.root),
        })
        self.assertEqual(completed.stdout, "")


def _load_math_search():
    spec = importlib.util.spec_from_file_location("math_search", _PLUGIN_ROOT / "hooks/math_search.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFileStatusChecks(unittest.TestCase):
    """The discovery checks answer False for an absent path and raise for a path they cannot check, on every
    Python version; pathlib's own checks return False on every OSError from 3.14 on."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.module = _load_math_search()
        self.checks = (self.module.path_exists, self.module.is_regular_file,
                       self.module.is_directory, self.module.is_symlink)

    def test_absent_and_present_paths(self) -> None:
        file_path = self.root / "problem.json"
        file_path.write_text("{}")
        directory = self.root / "attack"
        directory.mkdir()
        link = self.root / "link.json"
        link.symlink_to(file_path)
        broken = self.root / "broken.json"
        broken.symlink_to(self.root / "missing.json")
        loop = self.root / "loop"
        loop.symlink_to(loop)
        # (path_exists, is_regular_file, is_directory, is_symlink)
        expectations = (
            (self.root / "missing.json", (False, False, False, False)),
            (file_path, (True, True, False, False)),
            (directory, (True, False, True, False)),
            (link, (True, True, False, True)),
            (broken, (False, False, False, True)),
            (loop, (False, False, False, True)),
            (file_path / "child", (False, False, False, False)),
            (Path("nul\0l"), (False, False, False, False)),
        )
        for path, expected in expectations:
            with self.subTest(path=str(path)):
                self.assertEqual(tuple(check(path) for check in self.checks), expected)

    def test_an_overlong_component_is_absent(self) -> None:
        over_long = self.root / ("x" * 300) / "problem.json"
        _assert_overlong_path(self, over_long)
        for check in self.checks:
            with self.subTest(check=check.__name__):
                self.assertIs(check(over_long), False)

    def test_an_overlong_whole_path_is_absent(self) -> None:
        over_long = _overlong_paths(self.root)["whole_path"]
        _assert_overlong_path(self, over_long)
        for check in self.checks:
            with self.subTest(check=check.__name__):
                self.assertIs(check(over_long), False)

    def test_a_mocked_permission_error_propagates_and_denies_the_hook(self) -> None:
        with patch.dict(sys.modules, {"math_search": self.module}):
            gate = runpy.run_path(str(_GUARD_SCRIPT_PATH))
        probe = self.root / ".search/tree.json"
        permission_error = PermissionError(errno.EACCES, "Permission denied", str(probe))
        original_stat = os.stat

        def denied_stat(path, *args, **kwargs):
            if os.path.realpath(path) == os.path.realpath(probe):
                raise permission_error
            return original_stat(path, *args, **kwargs)

        payload = {"tool_name": "Write", "tool_input": {"file_path": "notes.md"}, "cwd": str(self.root)}
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(os, "stat", side_effect=denied_stat):
            for check in self.checks:
                with self.subTest(check=check.__name__):
                    with self.assertRaises(PermissionError) as raised:
                        check(probe)
                    self.assertIs(raised.exception, permission_error)
            with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                    patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors):
                with self.assertRaises(SystemExit) as stopped:
                    gate["main"]()
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(errors.getvalue(), "")
        reason = _read_denial_reason(self, subprocess.CompletedProcess([], 0, output.getvalue(), errors.getvalue()))
        self.assertIn("Recovery required before this managed operation", reason)

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permission bits")
    def test_a_directory_without_permissions_raises(self) -> None:
        blocked = self.root / "blocked"
        blocked.mkdir()
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o700)
        for check in self.checks:
            with self.subTest(check=check.__name__):
                with self.assertRaises(PermissionError):
                    check(blocked / "problem.json")


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
    def setUp(self):
        from test_math_search_hooks import managed_objective
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.controller = managed_objective(self.root / "attack")
        self.workspace = self.root / "attack/attempt"

    def _run_stop(self, cwd=None):
        return _run_hook(_CONTINUE_SCRIPT_PATH, {
            "cwd": str(cwd or self.root), "session_id": "session",
            "hook_event_name": "Stop", "stop_hook_active": False})

    def test_admitted_objective_blocks_with_current_controller_action(self):
        result = json.loads(self._run_stop().stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("execute_node", result["reason"])
        self.assertEqual(self.controller.status()["control"]["stop_count"], 1)

    def test_unmanaged_skeleton_is_not_persistent_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            unrelated = Path(directory)
            _build_attack(unrelated)
            self.assertEqual(self._run_stop(unrelated / "attack/sample").stdout, "")

    def test_finds_the_workspace_from_a_subdirectory(self):
        result = json.loads(self._run_stop(self.workspace / "deterministic").stdout)
        self.assertEqual(result["decision"], "block")

    def test_local_finished_file_does_not_close_objective(self):
        (self.workspace / "units/FINISHED.json").write_text('{"units":[]}')
        self.assertEqual(json.loads(self._run_stop().stdout)["decision"], "block")
        self.assertEqual(self.controller.status()["proof_status"], "open")

    def test_legacy_counter_never_overrides_objective_allowance(self):
        (self.workspace / ".continue_count").write_text("-1")
        self.assertEqual(json.loads(self._run_stop().stdout)["decision"], "block")
        self.assertEqual(self.controller.status()["control"]["stop_count"], 1)


class TestRecordAttackActivity(unittest.TestCase):
    """The autosave: every tool call that touched an attack workspace lands in its
    activity.jsonl, so a resumed session sees what the previous one was doing."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "work"
        self.workspace = _build_attack(self.root)

    def _run(self, tool_name: str, tool_input: dict, session: str | None = "session",
             host: str = "claude") -> subprocess.CompletedProcess:
        payload = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_response": {"success": True},
            "cwd": str(self.root),
            "session_id": session,
        }
        if host == "codex":
            return subprocess.run(
                [sys.executable, str(_PLUGIN_ROOT / "codex/hook.py"), _ACTIVITY_SCRIPT_PATH.name],
                input=json.dumps(payload), text=True, capture_output=True, check=True,
            )
        return _run_hook(_ACTIVITY_SCRIPT_PATH, payload)

    def _read_activity_status(self) -> list[str]:
        completed = subprocess.run(
            [sys.executable, str(_PLUGIN_ROOT / "bin/exactory-math"), "--attack-root",
             str(self.root / "attack"), "status", "sample"],
            text=True, capture_output=True, check=True,
        )
        lines = completed.stdout.splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith("activity:"))
        end = next((index for index in range(start + 1, len(lines)) if not lines[index].startswith("  ")),
                   len(lines))
        return lines[start:end]

    def _assert_inspection_keeps_the_harness_session(self, command: str) -> None:
        log_path = self.workspace / "activity.jsonl"
        for old_count in (0, 198):
            log_path.unlink(missing_ok=True)
            log_path.write_text('{"at":"old","tool":"Write","target":"old.md"}\n' * old_count)
            self._run("Bash", {"command": "exactory-math status sample"}, session="solver")
            self._run("Write", {"file_path": "attack/sample/notes.md"}, session="solver")
            before = log_path.read_bytes()
            for host in ("claude", "codex"):
                with self.subTest(entries=old_count + 2, host=host):
                    self._run("Bash", {"command": command}, session="reviewer", host=host)
                    self.assertEqual(log_path.read_bytes(), before)
                    lines = self._read_activity_status()
                    self.assertEqual(lines[0], "activity: last 2 of 2")
                    self.assertTrue(lines[-1].endswith(" Write notes.md"), lines)
                    self.assertEqual(len(self.read_log()), old_count + 2)

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
        self._run("Bash", {"command": "exactory-math status sample"})
        self._run("Bash", {"command": "cat attack/sample/study/problem.md"})
        self.assertEqual(self.read_log()[-1]["target"], "cat attack/sample/study/problem.md")

    def test_a_long_command_is_cut(self) -> None:
        self._run("Bash", {"command": "exactory-math status sample"})
        self._run("Bash", {"command": "python3 attack/sample/deterministic/run-1/search.py " + "x" * 300})
        self.assertEqual(len(self.read_log()[-1]["target"]), 160)

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

    def test_cat_from_another_session_keeps_the_harness_session_in_status(self) -> None:
        self._assert_inspection_keeps_the_harness_session("cat attack/sample/journal.jsonl")

    def test_wc_from_another_session_keeps_the_harness_session_in_status(self) -> None:
        self._assert_inspection_keeps_the_harness_session("wc -l attack/sample/journal.jsonl")

    def test_python_from_another_session_keeps_the_harness_session_in_status(self) -> None:
        self._assert_inspection_keeps_the_harness_session("python3 script.py attack/sample/journal.jsonl")

    def test_writes_and_edits_record_sessions_without_selecting_them(self) -> None:
        self._run("Bash", {"command": "exactory-math status sample"}, session="solver")
        for tool_name in ("Write", "Edit"):
            self._run(tool_name, {"file_path": "attack/sample/review.md"}, session="reviewer")
        entries = self.read_log()
        self.assertEqual([entry["session_id"] for entry in entries],
                         ["claude:solver", "claude:reviewer", "claude:reviewer"])
        self.assertEqual([entry["harness_command"] for entry in entries], [True, False, False])
        self.assertEqual([entry["tool"] for entry in entries], ["Bash", "Write", "Edit"])
        self.assertEqual(self._read_activity_status()[0], "activity: last 1 of 1")

    def test_the_latest_harness_session_selects_its_calls_before_and_after_the_command(self) -> None:
        self._run("Bash", {"command": "exactory-math status sample"}, session="previous")
        self._run("Write", {"file_path": "attack/sample/current.md"}, session="current")
        self._run("Bash", {"command": "exactory-math budget sample"}, session="current")
        self._run("Bash", {"command": "cat attack/sample/problem.json"}, session="current")
        self._run("Edit", {"file_path": "attack/sample/previous.md"}, session="previous")
        lines = self._read_activity_status()
        self.assertEqual(lines[0], "activity: last 3 of 3")
        self.assertTrue(lines[1].endswith(" Write current.md"), lines)
        self.assertTrue(lines[2].endswith(" Bash exactory-math budget sample"), lines)
        self.assertTrue(lines[3].endswith(" Bash cat attack/sample/problem.json"), lines)

    def test_harness_invocations_are_tagged_for_both_hosts(self) -> None:
        for host, tool_name, command_key in (("claude", "Bash", "command"),
                                              ("codex", "functions.exec_command", "cmd")):
            for command in (
                "exactory-math status sample",
                "/plugin/bin/exactory-math status sample",
                "python3 /plugin/harness/attack.py status sample",
                "python3.12 -u /plugin/harness/attack.py status sample",
                "env EXACTORY_API_KEY= exactory-math status sample",
                "NAME=value env -u SECRET -C . EXACTORY_API_KEY= python3 -W ignore -X dev "
                "--check-hash-based-pycs default -- /plugin/harness/attack.py status sample",
                "export PATH=/plugin/bin:$PATH; exactory-math status sample",
            ):
                with self.subTest(host=host, command=command):
                    count = len(self.read_log())
                    self._run(tool_name, {command_key: command}, host=host)
                    self.assertEqual(len(self.read_log()), count + 1)
                    entry = self.read_log()[-1]
                    self.assertEqual(entry["session_id"], host + ":session")
                    self.assertIs(entry["harness_command"], True)

    def test_search_commands_tag_the_session_in_a_managed_custom_root(self) -> None:
        attack_root = self.root / "custom"
        workspace = attack_root / "attempt"
        workspace.mkdir(parents=True)
        (workspace / "problem.json").write_text('{"claim":""}')
        (attack_root / ".search").mkdir()
        (attack_root / ".search/tree.json").write_text("{}")
        spec_path = workspace / "begin.json"
        command = "exactory-math --attack-root %s search begin node-000001 --spec %s" % (attack_root, spec_path)
        self._run("exec_command", {"cmd": command, "workdir": str(self.root)}, session="searcher", host="codex")
        entry = json.loads((workspace / "activity.jsonl").read_text())
        self.assertEqual(entry["session_id"], "codex:searcher")
        self.assertIs(entry["harness_command"], True)
        self.assertEqual(entry["target"], "begin.json")

    def test_command_arguments_do_not_select_a_harness_session(self) -> None:
        self._run("Bash", {"command": "exactory-math status sample"}, session="solver")
        log_path = self.workspace / "activity.jsonl"
        for command in (
            "cat attack/sample/exactory-math",
            "python3 script.py attack/sample/attack.py",
            "python3 -c 'exactory-math' attack/sample/problem.json",
            "printf '%s' exactory-math attack/sample/problem.json",
        ):
            with self.subTest(command=command):
                before = log_path.read_bytes()
                self._run("Bash", {"command": command}, session="reviewer")
                self.assertEqual(log_path.read_bytes(), before)
                count = len(self.read_log())
                self._run("Bash", {"command": command}, session="solver")
                self.assertEqual(len(self.read_log()), count + 1)
                self.assertIs(self.read_log()[-1]["harness_command"], False)
        self.assertEqual({entry["session_id"] for entry in self.read_log()}, {"claude:solver"})

    def test_quoted_math_and_shell_characters_do_not_hide_harness_calls(self) -> None:
        for command in (
            "exactory-math task add sample '$n/2$'",
            "exactory-math task add sample 'a; b | c'",
            "exactory-math task add sample 'two\nlines'",
            "exactory-math task add sample '" + "state the reduction step by step " * 12 + "proofs/reduction.md'",
            "exactory-math task add sample '" + "proofs/" * 700 + "reduction.md'",
        ):
            with self.subTest(command=command):
                count = len(self.read_log())
                self._run("Bash", {"command": command}, session="solver")
                self.assertEqual(len(self.read_log()), count + 1)
                entry = self.read_log()[-1]
                self.assertEqual(entry["session_id"], "claude:solver")
                self.assertIs(entry["harness_command"], True)

    def test_a_harness_command_on_another_attack_does_not_select_the_inspection_session(self) -> None:
        _build_attack(self.root, "other")
        self._run("Bash", {"command": "exactory-math status sample"}, session="solver")
        log_path = self.workspace / "activity.jsonl"
        before = log_path.read_bytes()
        self._run("Bash", {"command": "cat attack/sample/problem.json; exactory-math status other"},
                  session="reviewer")
        self.assertEqual(log_path.read_bytes(), before)
        self.assertEqual(self._read_activity_status()[0], "activity: last 1 of 1")
        self._run("Bash", {"command": "cat attack/sample/problem.json; exactory-math status sample"},
                  session="reviewer")
        self.assertIs(self.read_log()[-1]["harness_command"], True)
        self.assertEqual(self._read_activity_status()[0], "activity: last 1 of 1")

    def test_interpreter_option_values_do_not_select_a_harness_session(self) -> None:
        self._run("Bash", {"command": "exactory-math status sample"}, session="solver")
        log_path = self.workspace / "activity.jsonl"
        for command in (
            "python3 -W exactory-math script.py attack/sample/problem.json",
            "python3 -X exactory-math script.py attack/sample/problem.json",
        ):
            with self.subTest(command=command):
                before = log_path.read_bytes()
                self._run("Bash", {"command": command}, session="reviewer")
                self.assertEqual(log_path.read_bytes(), before)
                count = len(self.read_log())
                self._run("Bash", {"command": command}, session="solver")
                self.assertEqual(len(self.read_log()), count + 1)
                self.assertIs(self.read_log()[-1]["harness_command"], False)
        self.assertEqual({entry["session_id"] for entry in self.read_log()}, {"claude:solver"})

    def test_sessions_with_the_same_id_on_different_hosts_stay_separate(self) -> None:
        self._run("Bash", {"command": "exactory-math status sample"}, session="shared")
        log_path = self.workspace / "activity.jsonl"
        before = log_path.read_bytes()
        self._run("exec_command", {"cmd": "cat attack/sample/problem.json"}, session="shared", host="codex")
        self.assertEqual(log_path.read_bytes(), before)
        self.assertEqual(self._read_activity_status()[0], "activity: last 1 of 1")
        self._run("exec_command", {"cmd": "exactory-math budget sample"}, session="shared", host="codex")
        self._run("exec_command", {"cmd": "cat attack/sample/problem.json"}, session="shared", host="codex")
        self.assertEqual([entry["session_id"] for entry in self.read_log()],
                         ["claude:shared", "codex:shared", "codex:shared"])
        lines = self._read_activity_status()
        self.assertEqual(lines[0], "activity: last 2 of 2")
        self.assertTrue(lines[-1].endswith(" Bash cat attack/sample/problem.json"), lines)

    def test_anonymous_calls_cannot_replace_the_last_identified_harness_session(self) -> None:
        self._run("Bash", {"command": "exactory-math status sample"}, session="solver")
        for session in (None, "", " "):
            self._run("Bash", {"command": "exactory-math budget sample"}, session=session)
            self.assertIsNone(self.read_log()[-1]["session_id"])
            self.assertIs(self.read_log()[-1]["harness_command"], True)
        self.assertEqual(self._read_activity_status()[0], "activity: last 1 of 1")

    def test_the_cap_counts_all_sessions_and_preserves_retained_legacy_lines(self) -> None:
        legacy_lines = [json.dumps({"at": "old", "tool": "Write", "target": "old-%d.md" % number}) + "\n"
                        for number in range(198)]
        log_path = self.workspace / "activity.jsonl"
        log_path.write_text("".join(legacy_lines))
        self._run("Bash", {"command": "exactory-math status sample"}, session="solver")
        for tool_name in ("Write", "Edit", "Write"):
            self._run(tool_name, {"file_path": "attack/sample/review.md"}, session="reviewer")
        entries = self.read_log()
        self.assertEqual(len(entries), 200)
        self.assertTrue(log_path.read_bytes().startswith("".join(legacy_lines[2:]).encode()))
        self.assertEqual([entry["session_id"] for entry in entries[-4:]],
                         ["claude:solver", "claude:reviewer", "claude:reviewer", "claude:reviewer"])
        self.assertEqual(self._read_activity_status()[0], "activity: last 1 of 1")

    def test_logs_without_an_identified_harness_command_keep_the_legacy_view(self) -> None:
        log_path = self.workspace / "activity.jsonl"
        log_path.write_text('{"at":"old","tool":"Write","target":"old.md"}\ninvalid\n[]\n')
        self._run("Write", {"file_path": "attack/sample/notes.md"}, session=None)
        self._run("Edit", {"file_path": "attack/sample/review.md"}, session="reviewer")
        before = log_path.read_bytes()
        self._run("Bash", {"command": "cat attack/sample/problem.json"}, session="reviewer")
        self.assertEqual(log_path.read_bytes(), before)
        entries = [json.loads(line) for line in log_path.read_text().splitlines()[-2:]]
        self.assertEqual([entry["session_id"] for entry in entries], [None, "claude:reviewer"])
        lines = self._read_activity_status()
        self.assertEqual(lines[0], "activity: last 3 of 3")
        self.assertEqual(lines[1], "  old Write old.md")
        self.assertTrue(lines[-1].endswith(" Edit review.md"), lines)

    def test_a_session_records_ordinary_calls_only_after_its_first_harness_command(self) -> None:
        log_path = self.workspace / "activity.jsonl"
        log_path.write_text('{"at":"old","tool":"Write","target":"old.md"}\n')
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                before = log_path.read_bytes()
                self._run("Bash", {"command": "cat attack/sample/problem.json"}, host=host)
                self.assertEqual(log_path.read_bytes(), before)
                self._run("Bash", {"command": "exactory-math status sample"}, host=host)
                count = len(self.read_log())
                for command in ("cat", "wc -l", "python3 script.py", "printf x >"):
                    self._run("Bash", {"command": command + " attack/sample/notes.md"}, host=host)
                entries = self.read_log()
                self.assertEqual(len(entries), count + 4)
                self.assertEqual([entry["session_id"] for entry in entries[-4:]], [host + ":session"] * 4)
                self.assertEqual([entry["harness_command"] for entry in entries[-4:]], [False] * 4)

    def test_an_anonymous_session_cannot_record_ordinary_calls(self) -> None:
        log_path = self.workspace / "activity.jsonl"
        for host in ("claude", "codex"):
            for session in (None, "", " "):
                with self.subTest(host=host, session=session):
                    self._run("Bash", {"command": "exactory-math status sample"}, host=host, session=session)
                    before = log_path.read_bytes()
                    self._run("Bash", {"command": "cat attack/sample/problem.json"}, host=host, session=session)
                    self.assertEqual(log_path.read_bytes(), before)

    def test_writes_and_edits_do_not_establish_a_shell_session(self) -> None:
        log_path = self.workspace / "activity.jsonl"
        for host in ("claude", "codex"):
            for tool_name in ("Write", "Edit"):
                self._run(tool_name, {"file_path": "attack/sample/notes.md"}, host=host)
            before = log_path.read_bytes()
            self._run("Bash", {"command": "printf x > attack/sample/notes.md"}, host=host)
            self.assertEqual(log_path.read_bytes(), before)
        self.assertEqual([entry["tool"] for entry in self.read_log()], ["Write", "Edit", "Write", "Edit"])

    def test_a_session_without_a_harness_entry_does_not_create_an_activity_log(self) -> None:
        for host in ("claude", "codex"):
            for command in ("cat", "wc -l", "python3 script.py"):
                with self.subTest(host=host, command=command):
                    self._run("Bash", {"command": command + " attack/sample/problem.json"}, host=host)
                    self.assertFalse((self.workspace / "activity.jsonl").exists())

    def test_retention_keeps_the_harness_entry_of_a_working_session(self) -> None:
        log_path = self.workspace / "activity.jsonl"
        self._run("Bash", {"command": "exactory-math status sample"})
        retained = json.dumps({"at": "old", "tool": "Write", "target": "notes.md",
                               "session_id": "claude:session", "harness_command": False})
        log_path.chmod(0o644)
        log_path.write_text(log_path.read_text() + (retained + "\n") * 199)
        self._run("Write", {"file_path": "attack/sample/notes.md"})
        self.assertEqual(len(self.read_log()), 201)
        self.assertIs(self.read_log()[0]["harness_command"], True)
        self._run("Bash", {"command": "cat attack/sample/problem.json"})
        self.assertEqual(len(self.read_log()), 201)
        self.assertEqual(self.read_log()[-1]["target"], "cat attack/sample/problem.json")

    def test_a_checked_harness_command_leaves_no_activity(self) -> None:
        # A check writes nothing, so it leaves no activity entry either; another call of the same shell command still
        # records its own.
        self._run("Bash", {"command": "exactory-math status sample"})
        log_path = self.workspace / "activity.jsonl"
        before = log_path.read_bytes()
        for command in ("exactory-math search pause sample --expected-revision 3 --request-id check-pause --check",
                        "exactory-math journal add sample --check --json '{\"move\": 1}'"):
            with self.subTest(command=command):
                self._run("Bash", {"command": command})
                self.assertEqual(log_path.read_bytes(), before)
        self._run("Bash", {"command": "exactory-math journal add sample --check;exactory-math status sample"})
        self.assertEqual(len(self.read_log()), 2)
        self.assertIs(self.read_log()[-1]["harness_command"], True)

    def test_compound_harness_commands_use_shell_token_boundaries(self) -> None:
        for command in ("exactory-math status sample;printf done", "NAME=value exactory-math status sample|cat"):
            with self.subTest(command=command):
                count = len(self.read_log())
                self._run("Bash", {"command": command})
                self.assertEqual(len(self.read_log()), count + 1)
                self.assertIs(self.read_log()[-1]["harness_command"], True)
        log_path = self.workspace / "activity.jsonl"
        before = log_path.read_bytes()
        for command in ("exactory-math status sample '", 'cat "attack/sample/problem.json'):
            self._run("Bash", {"command": command})
            self.assertEqual(log_path.read_bytes(), before)

    def test_quoted_and_escaped_separators_do_not_establish_a_harness_session(self) -> None:
        log_path = self.workspace / "activity.jsonl"
        for entry_count in (1, 200):
            log_path.write_text('{"at":"old","tool":"Write","target":"old.md"}\n' * entry_count)
            before = log_path.read_bytes()
            for host in ("claude", "codex"):
                for separator in ("';'", '"|"', "\\;", "'&&'", "'||'", "'&'"):
                    with self.subTest(entries=entry_count, host=host, separator=separator):
                        command = "printf '%s' " + separator + " exactory-math status sample attack/sample/problem.json"
                        self._run("Bash", {"command": command}, host=host)
                        self.assertEqual(log_path.read_bytes(), before)
        self._run("Bash", {"command": "printf '%s' ';';exactory-math status sample"})
        self.assertIs(self.read_log()[-1]["harness_command"], True)

    def test_a_workspace_inspection_does_not_hide_another_attacks_harness_command(self) -> None:
        other_workspace = _build_attack(self.root, "other")
        log_path = self.workspace / "activity.jsonl"
        log_path.write_text('{"at":"old","tool":"Write","target":"old.md"}\n')
        before = log_path.read_bytes()
        self._run("Bash", {"command": "cat attack/sample/problem.json;exactory-math status other"})
        self.assertEqual(log_path.read_bytes(), before)
        other_log_path = other_workspace / "activity.jsonl"
        self.assertTrue(other_log_path.exists())
        other_entries = [json.loads(line) for line in other_log_path.read_text().splitlines()]
        self.assertEqual(len(other_entries), 1)
        self.assertEqual(other_entries[0]["session_id"], "claude:session")
        self.assertIs(other_entries[0]["harness_command"], True)
        self._run("Bash", {"command": "cat attack/other/problem.json"})
        self.assertEqual(len(other_log_path.read_text().splitlines()), 2)
        self.assertEqual(log_path.read_bytes(), before)
        self._run("Bash", {"command": "exactory-math status sample;exactory-math status other"})
        self.assertEqual(len(self.read_log()), 2)
        self.assertEqual(len(other_log_path.read_text().splitlines()), 3)

    def test_missing_lexer_state_keeps_guard_public_and_skips_activity(self) -> None:
        spec = importlib.util.spec_from_file_location("math_search", _PLUGIN_ROOT / "hooks/math_search.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        command = "exactory-math status sample"
        tokens = ["exactory-math", "status", "sample"]
        lexer = MagicMock(spec=["__iter__", "instream", "get_token", "whitespace_split", "commenters"])
        lexer.__iter__.side_effect = lambda: iter(tokens)
        lexer.instream = io.StringIO(command)
        self.assertFalse(hasattr(lexer, "_pushback_chars"))
        log_path = self.workspace / "activity.jsonl"
        before = b'{"at":"old","tool":"Write","target":"old.md"}\r\n' * 200
        log_path.write_bytes(before)
        payload = {"tool_name": "Bash", "tool_input": {"command": command},
                   "tool_response": {"success": True}, "cwd": str(self.root), "session_id": "session"}
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(module.shlex, "shlex", return_value=lexer):
            with self.subTest(path="guard"):
                self.assertEqual(module.shell_tokens(command), tokens)
            with patch.dict(sys.modules, {"math_search": module}), patch.object(sys, "stdin", io.StringIO(
                    json.dumps(payload))), patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(_ACTIVITY_SCRIPT_PATH), run_name="__main__")
            self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(log_path.read_bytes(), before)


class TestResumeAttack(unittest.TestCase):
    def setUp(self):
        from test_math_search_hooks import managed_objective
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.controller = managed_objective(self.root / "attack")

    def _start(self, source="startup"):
        return _run_hook(_RESUME_SCRIPT_PATH, {
            "cwd": str(self.root), "session_id": "session", "source": source,
            "hook_event_name": "SessionStart"})

    def test_reports_one_controller_action_without_writing_state(self):
        before = self.controller.store.tree_path.read_bytes()
        output = json.loads(self._start().stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        self.assertIn("execute_node", output["additionalContext"])
        self.assertIn("stage 0", output["additionalContext"])
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_compaction_preserves_a_durable_pause(self):
        self.controller.command("pause", {"reason": "Explicit user pause"}, 5, "pause")
        before = self.controller.store.tree_path.read_bytes()
        for source in ("compact", "resume", "startup"):
            output = json.loads(self._start(source).stdout)
            self.assertIn("paused", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_unmanaged_attack_does_not_create_session_focus(self):
        unrelated = self.root / "unmanaged"
        _build_attack(unrelated)
        output = _run_hook(_RESUME_SCRIPT_PATH, {
            "cwd": str(unrelated / "attack/sample"), "hook_event_name": "SessionStart"})
        self.assertEqual(output.stdout, "")


if __name__ == "__main__":
    unittest.main()
