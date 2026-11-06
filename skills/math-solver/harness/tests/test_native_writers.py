"""Accidental direct writes fail on files published by the real native CLI."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest import mock

from tests.support import AdmittedWorkspaceTest, make_move, prepare_plan


class NativeWritersTest(AdmittedWorkspaceTest):
    def setUp(self):
        super().setUp()
        prepare_plan(self)

    def test_cli_journal_rejects_bare_name_cd_and_python_direct_writes(self):
        journal = self.workspace / "journal.jsonl"
        before = journal.read_bytes()
        commands = [
            (["/bin/sh", "-c", "echo x > journal.jsonl"], self.workspace),
            (["/bin/sh", "-c", "echo x >> journal.jsonl"], self.workspace),
            (["/bin/sh", "-c", "cd " + shlex.quote(str(self.workspace))
              + " && echo x > journal.jsonl"], self.attack_root.parent),
            (["/bin/sh", "-c", "cd " + shlex.quote(str(self.workspace))
              + " && echo x >> journal.jsonl"], self.attack_root.parent),
            ([sys.executable, "-c", "open('journal.jsonl', 'a').write('x')"], self.workspace),
        ]
        for command, cwd in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result)
                self.assertIn("Permission denied", result.stderr)
                self.assertEqual(journal.read_bytes(), before)

    def test_native_updates_owned_files_and_leaves_author_files_writable(self):
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1)))[0], 0)
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(2, closes=True)))[0], 0)
        for text in ("first task", "second task"):
            self.assertEqual(self.run_cli("task", "add", self.slug, text)[0], 0)
        self.assertEqual(self.run_cli("stall", self.slug)[0], 0)
        self.assertEqual(len((self.workspace / "journal.jsonl").read_text().splitlines()), 2)
        self.assertEqual(len(self.read_json("tasks.json")["tasks"]), 2)
        owned = [self.workspace / name for name in ("journal.jsonl", "openings.json", "tasks.json", "LINEAGE.md")]
        owned += [self.attack_root / "SEARCH_TREE.md"]
        owned += [path for path in (self.attack_root / ".search").rglob("*")
                  if path.is_file() and path.suffix != ".lock" and ".sqlite3" not in path.name]
        for path in owned:
            with self.subTest(path=path):
                self.assertEqual(path.stat().st_mode & 0o222, 0)
        for name in ("problem.json", "preconditions.json", "ranking.json", "novelty.md", "study/problem.md",
                     "units/INVENTORY.md"):
            with self.subTest(name=name):
                with (self.workspace / name).open("a"):
                    pass

    def test_rename_interruption_preserves_previous_cli_journal(self):
        from search_controller.render import replace_bytes
        journal = self.workspace / "journal.jsonl"
        before = journal.read_bytes()
        entries = set(self.workspace.iterdir())

        def interrupt(source, target, **kwargs):
            temporary = Path(source)
            self.assertEqual(temporary.parent, journal.parent)
            self.assertEqual(temporary.read_bytes(), b"complete replacement\n")
            self.assertEqual(temporary.stat().st_mode & 0o222, 0)
            self.assertEqual(journal.read_bytes(), before)
            raise InterruptedError("before rename")

        with mock.patch.object(os, "replace", side_effect=interrupt):
            with self.assertRaises(InterruptedError):
                replace_bytes(journal, b"complete replacement\n")
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(set(self.workspace.iterdir()), entries)

    def test_writable_old_record_replays_and_plans_without_a_mode_requirement(self):
        files = [path for path in self.attack_root.rglob("*") if path.is_file()]
        for path in files:
            path.chmod(path.stat().st_mode | 0o200)
        before = {path: path.read_bytes() for path in files}
        state = self.controller.status()
        self.assertGreater(state["revision"], 0)
        self.assertEqual(self.run_cli("search", "next", "--json")[0], 0)
        self.assertEqual({path: path.read_bytes() for path in files}, before)
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        self.assertEqual((self.workspace / "openings.json").stat().st_mode & 0o222, 0)
        self.assertEqual((self.workspace / "problem.json").read_bytes(), before[self.workspace / "problem.json"])

    def test_writable_math_record_passes_record_check_on_an_unchanged_source(self):
        from research_harness.record_check import check_record
        root = self.attack_root.parent
        plugin = Path(__file__).resolve().parents[4]
        payload = root / "init-research.json"
        payload.write_text(json.dumps({"profile": "research", "target": None}))
        result = subprocess.run([sys.executable, str(plugin / "bin/exactory-research"), "init",
                                 "--workspace", str(root), "--file", str(payload),
                                 "--expected-revision", "0", "--request-id", "init"],
                                cwd=plugin, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            path.chmod(path.stat().st_mode | 0o200)
        before = {path.relative_to(root): (path.read_bytes(), path.stat().st_mode) for path in files}
        report = check_record(root, isolate=False)
        self.assertTrue(report["ready"], report)
        after = {path.relative_to(root): (path.read_bytes(), path.stat().st_mode)
                 for path in root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
