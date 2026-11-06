"""Recorded shell and passive-reader shapes at the real controller boundary."""

import errno
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from research_harness.native_writes import write_atomic
from test_math_search_hooks import managed_objective

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import math_search


class ManagedHookBoundaryTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="guard boundaries ")
        self.addCleanup(scratch.cleanup)
        self.workspace = Path(scratch.name).resolve()
        self.root = self.workspace / "objective"
        self.controller = managed_objective(self.root)
        self.pointer = self.workspace / ".exactory/math-search.json"

    def build_payload(self, command=None, cwd=None, session="session", host="claude"):
        payload = {"cwd": str(cwd or self.workspace), "session_id": session,
                   "_math_host": host}
        if command is not None:
            payload.update(tool_name="exec_command", tool_input={"cmd": command})
        return payload

    def assert_guard_allows(self, command, cwd=None):
        before = self.pointer.read_bytes(), self.controller.store.tree_path.read_bytes()
        with patch.object(math_search, "controller_call", wraps=math_search.controller_call) as calls:
            self.assertIsNone(math_search.guard(self.build_payload(command, cwd)))
        self.assertLessEqual(calls.call_count, 1)
        self.assertEqual((self.pointer.read_bytes(), self.controller.store.tree_path.read_bytes()), before)


class MathSearchShellGuardTests(ManagedHookBoundaryTests):
    def test_literal_environment_prefixed_read_is_allowed(self):
        for prefix in ("EXACTORY_API_KEY= ", "MODE='read only' env EXACTORY_API_KEY= "):
            with self.subTest(prefix=prefix):
                self.assert_guard_allows(prefix + "exactory-math --attack-root " +
                                         shlex.quote(str(self.root)) + " search status --json")

    def test_compound_reads_and_du_are_allowed(self):
        for separator in (";", "&&", "||", "|"):
            with self.subTest(separator=separator):
                self.assert_guard_allows("du -sh .search " + separator + " cat .search/tree.json", self.root)

    def test_discovery_pointer_copy_uses_only_the_destination_as_a_write(self):
        self.assert_guard_allows("cp .exactory/math-search.json pointer-copy.json")
        self.assert_guard_allows("env MODE=backup cp -- .exactory/math-search.json pointer-copy.json")

    def test_reverse_copy_move_delete_and_mixed_chains_remain_denied(self):
        for command in (
            "cp notes.json .exactory/math-search.json",
            "mv .exactory/math-search.json notes.json",
            "rm .exactory/math-search.json",
            "cat notes.json && cp notes.json .exactory/math-search.json",
            "du -sh objective ; rm objective/.search/tree.json",
            "cat objective/.search/tree.json | tee objective/.search/tree.json",
            "cp .exactory/math-search.json backup.json && rm .exactory/math-search.json",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(math_search.guard(self.build_payload(command)))

    def test_ambiguous_shell_forms_are_denied(self):
        for command in (
            "env -C .. cat .search/tree.json",
            "env --split-string='cat .search/tree.json'",
            "cp -t .search notes.json", "cp --target-directory=.search notes.json",
            "cp --parents .search/tree.json notes", "cp .search/tree.json",
            "cat .search/tree.json &", "cat .search/tree.json ;; pwd",
            "cat .search/tree.json > notes.json", "cat .search/tree.json < notes.json",
            "cat .search/*.json", 'cat "$TARGET"', "cat notes\nrm .search/tree.json",
            "cat .search/tree.json && python3 -c 'print(1)'",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(math_search.guard(self.build_payload(command, self.root)))

    def test_copy_sources_are_readable_but_symlink_destinations_are_protected(self):
        alias = self.workspace / "pointer-alias.json"
        alias.symlink_to(self.pointer)
        self.assert_guard_allows("cp pointer-alias.json backup.json")
        self.assertIsNotNone(math_search.guard(self.build_payload("cp notes.json pointer-alias.json")))
        directory_alias = self.workspace / "controller-alias"
        directory_alias.symlink_to(self.root / ".search", target_is_directory=True)
        self.assertIsNotNone(math_search.guard(self.build_payload("cp notes.json controller-alias/tree.json")))

    def test_overlong_text_does_not_hide_a_protected_write(self):
        text = shlex.quote("synthetic prose " * 30 + "proofs/example.md")
        self.assert_guard_allows("printf '%s' " + text, self.root)
        self.assertIsNotNone(math_search.guard(self.build_payload(
            "printf '%s' " + text + " ; rm .search/tree.json", self.root)))

    def test_real_permission_error_remains_an_error(self):
        blocked = self.root / "blocked"
        blocked.mkdir()
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o700)
        for command in ("cat blocked/input.json", "cp notes.json blocked/output.json"):
            with self.subTest(command=command), self.assertRaises(PermissionError):
                math_search.guard(self.build_payload(command, self.root))

    def test_unexpected_stat_failure_on_a_copy_is_not_treated_as_absence(self):
        original_stat = os.stat

        def check_stat(path, *args, **kwargs):
            if str(path).endswith("/blocked/.search/tree.json"):
                raise PermissionError(errno.EACCES, "Synthetic access failure", str(path))
            return original_stat(path, *args, **kwargs)

        with patch.object(os, "stat", side_effect=check_stat), self.assertRaises(PermissionError):
            math_search.guard(self.build_payload("cp notes.json blocked/output.json", self.root))

    def test_recorded_shell_shapes_pass_both_host_adapters(self):
        commands = (
            "EXACTORY_API_KEY= exactory-math --attack-root objective search status --json",
            "du -sh objective && cat objective/.search/tree.json",
            "cp .exactory/math-search.json pointer-copy.json",
        )
        for host in ("claude", "codex"):
            argv = [sys.executable, str(ROOT / "hooks/guard_attack_files.py")]
            if host == "codex":
                argv = [sys.executable, str(ROOT / "codex/hook.py"), "guard_attack_files.py"]
            for command in commands:
                with self.subTest(host=host, command=command):
                    payload = self.build_payload(command, host=host)
                    payload["hook_event_name"] = "PreToolUse"
                    result = subprocess.run(argv, input=json.dumps(payload), capture_output=True,
                                            text=True, timeout=30)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


class MathSearchStopFocusTests(ManagedHookBoundaryTests):
    def test_repeated_passive_parent_stops_have_no_output_calls_or_mutation(self):
        before = self.pointer.read_bytes(), self.controller.store.tree_path.read_bytes()
        for host in ("claude", "codex"):
            for session in ("passive-reader", None):
                payload = self.build_payload(session=session, host=host)
                with patch.object(math_search, "selected_root", wraps=math_search.selected_root) as selection, \
                        patch.object(math_search, "controller_call", wraps=math_search.controller_call) as calls:
                    outputs = [math_search.lifecycle(payload, stop=True) for _ in range(3)]
                self.assertEqual(outputs, [None, None, None])
                self.assertEqual(selection.call_count, 0)
                self.assertEqual(calls.call_count, 0)
        self.assertEqual((self.pointer.read_bytes(), self.controller.store.tree_path.read_bytes()), before)

    def test_passive_stop_is_silent_through_both_host_adapters(self):
        before = self.pointer.read_bytes(), self.controller.store.tree_path.read_bytes()
        for host in ("claude", "codex"):
            argv = [sys.executable, str(ROOT / "hooks/continue_attack.py")]
            if host == "codex":
                argv = [sys.executable, str(ROOT / "codex/hook.py"), "continue_attack.py"]
            payload = self.build_payload(session="passive-reader", host=host)
            payload["hook_event_name"] = "Stop"
            for _ in range(3):
                result = subprocess.run(argv, input=json.dumps(payload), capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual((self.pointer.read_bytes(), self.controller.store.tree_path.read_bytes()), before)

    def test_focused_parent_and_direct_root_stops_still_continue(self):
        for cwd in (self.workspace, self.root / "attempt"):
            with self.subTest(cwd=cwd):
                result = math_search.lifecycle(self.build_payload(cwd=cwd), stop=True)
                self.assertEqual(result["decision"], "block")
        registry = json.loads(self.pointer.read_text())
        del registry["sessions"]["claude:session"]
        write_atomic(self.pointer, json.dumps(registry).encode("utf-8"))
        with patch.object(math_search, "controller_call", wraps=math_search.controller_call) as calls:
            result = math_search.lifecycle(self.build_payload(cwd=self.root), stop=True)
        self.assertGreater(calls.call_count, 0)
        self.assertIn("focus", json.dumps(result).lower())
        self.assertEqual(self.controller.status()["control"]["stop_count"], 2)

    def test_explicit_cleared_ambiguous_and_conflicting_focus_still_reports_recovery(self):
        registry = json.loads(self.pointer.read_text())
        cleared = json.loads(json.dumps(registry))
        cleared["sessions"]["claude:session"]["target"] = None
        write_atomic(self.pointer, json.dumps(cleared).encode("utf-8"))
        result = math_search.lifecycle(self.build_payload(), stop=True)
        self.assertIn("cleared", json.dumps(result))
        write_atomic(self.pointer, json.dumps(registry).encode("utf-8"))

        nested = self.root / ".exactory/math-search.json"
        nested.parent.mkdir(exist_ok=True)
        write_atomic(nested, json.dumps(cleared).encode("utf-8"))
        result = math_search.lifecycle(self.build_payload(cwd=self.root), stop=True)
        self.assertIn("ambiguous", json.dumps(result))
        nested.unlink()

        other = self.root / "other"
        (other / ".search").mkdir(parents=True)
        (other / ".search/tree.json").write_text("{}")
        result = math_search.lifecycle(self.build_payload(cwd=other), stop=True)
        self.assertIn("conflicts", json.dumps(result))
        self.assertEqual(self.controller.status()["control"]["stop_count"], 0)

    def test_session_start_and_unbound_direct_root_keep_focus_diagnostics(self):
        for stop, cwd in ((False, self.workspace), (True, self.root)):
            result = math_search.lifecycle(self.build_payload(cwd=cwd, session="passive-reader"), stop=stop)
            self.assertIsNotNone(result)
            self.assertIn("focus", json.dumps(result).lower())
        self.assertEqual(self.controller.status()["control"]["stop_count"], 0)
