"""Activity retention and concurrent updates in temporary attack workspaces."""

import fcntl
import json
import os
from pathlib import Path
import runpy
import selectors
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
RECORDER = HOOKS / "record_attack_activity.py"

# Pause each writer after its read. Without serialization, both writers read
# the same prefix before either is allowed to write it back.
APPEND_PROCESS = """
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import record_attack_activity as activity
workspace = Path(sys.argv[2])
read_text = Path.read_text
def read_and_wait(path, *args, **kwargs):
    result = read_text(path, *args, **kwargs)
    if path == workspace / 'activity.jsonl':
        print('read', flush=True)
        sys.stdin.readline()
    return result
print('ready', flush=True)
sys.stdin.readline()
with patch.object(Path, 'read_text', read_and_wait):
    activity._append(workspace, 'Edit', sys.argv[3], 'claude:solver', False)
"""


class AttackActivityEligibilityTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.workspace = self.root / "attack" / "sample"
        self.workspace.mkdir(parents=True)
        (self.workspace / "problem.json").write_text('{"claim":""}')
        self.log = self.workspace / "activity.jsonl"
        with patch.object(sys, "path", [str(HOOKS), *sys.path]):
            self.recorder = runpy.run_path(str(RECORDER))

    def append(self, target, session="claude:solver", tool="Edit", harness=False):
        self.recorder["_append"](self.workspace, tool, target, session, harness)

    def read_entries(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_working_session_keeps_ordinary_shell_activity_after_200_later_entries(self):
        self.append("exactory-math status sample", tool="Bash", harness=True)
        marker = self.log.read_text().splitlines()[0]
        for number in range(205):
            self.append("notes-%d.md" % number)
        result = subprocess.run([sys.executable, str(RECORDER)], input=json.dumps({
            "tool_name": "Bash", "tool_input": {"command": "cat attack/sample/problem.json"},
            "tool_response": {"success": True}, "cwd": str(self.root), "session_id": "solver",
        }), text=True, capture_output=True, check=True)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertEqual(self.read_entries()[-1]["target"], "cat attack/sample/problem.json")
        self.assertEqual(self.log.read_text().splitlines()[0], marker)
        self.assertEqual(len(self.read_entries()), 201)

    def test_bound_allows_only_latest_markers_of_sessions_in_the_recent_200(self):
        entries = [
            {"session_id": "claude:a", "harness_command": True, "target": "old-a"},
            {"session_id": "codex:a", "harness_command": True, "target": "codex-a"},
            {"session_id": "claude:expired", "harness_command": True},
            {"session_id": "claude:a", "harness_command": True, "target": "latest-a"},
            {"session_id": None, "harness_command": True},
        ] + [{"session_id": ("claude:a", "codex:a", "claude:reviewer", None)[number % 4],
              "harness_command": False, "target": "notes-%d.md" % number} for number in range(240)]
        raw = [json.dumps(entry, separators=(",", ":")) for entry in entries]
        self.log.write_text("\n".join(raw) + "\n")
        self.append("new.md", session="claude:reviewer")
        retained = self.log.read_text().splitlines()
        self.assertEqual(retained[:-1], [raw[1], raw[3]] + raw[-199:])
        self.assertEqual(len(retained), 202)
        self.assertEqual(json.loads(retained[-1])["target"], "new.md")

    def test_marker_expires_when_its_session_has_no_recent_entry(self):
        self.append("harness", tool="Bash", harness=True)
        for number in range(200):
            self.append("other-%d.md" % number, session="codex:other")
        self.assertEqual(len(self.read_entries()), 200)
        self.assertFalse(any(entry["harness_command"] for entry in self.read_entries()))
        before = self.log.read_bytes()
        self.append("inspection", tool="Bash")
        self.assertEqual(self.log.read_bytes(), before)

    def test_recent_marker_is_not_duplicated_and_legacy_lines_keep_the_bound(self):
        raw = ['{"at":"old","target":"legacy"}', "malformed", "[]"] * 70
        self.log.write_text("\n".join(raw) + "\n")
        self.append("harness", tool="Bash", harness=True)
        self.assertEqual(self.log.read_text().splitlines()[:-1], raw[-199:])
        self.append("notes.md")
        entries = self.log.read_text().splitlines()
        self.assertEqual(len(entries), 200)
        self.assertEqual(entries[:-2], raw[-198:])
        self.assertEqual(sum(json.loads(line).get("harness_command", False)
                             for line in entries[-2:]), 1)

    def test_two_processes_appending_at_the_same_time_keep_both_entries(self):
        self.append("initial.md")
        processes = [subprocess.Popen(
            [sys.executable, "-c", APPEND_PROCESS, str(HOOKS), str(self.workspace), target],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for target in ("first.md", "second.md")]
        try:
            with selectors.DefaultSelector() as reader:
                for process in processes:
                    self.assertEqual(process.stdout.readline(), "ready\n")
                    reader.register(process.stdout, selectors.EVENT_READ, process)
                for process in processes:
                    process.stdin.write("start\n")
                    process.stdin.flush()
                observed = 0
                while observed < 2:
                    ready = reader.select(timeout=10 if observed == 0 else 1)
                    if not ready:
                        break
                    for key, _ in ready:
                        self.assertEqual(key.fileobj.readline(), "read\n")
                        reader.unregister(key.fileobj)
                        observed += 1
                self.assertGreater(observed, 0)
                for process in processes:
                    process.stdin.write("release\n")
                    process.stdin.flush()
                for process in processes:
                    output, errors = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, errors)
                    self.assertEqual(errors, "")
            self.assertEqual({entry["target"] for entry in self.read_entries()},
                             {"initial.md", "first.md", "second.md"})
            self.assertEqual(len(self.read_entries()), 3)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def test_lock_on_the_attack_directory_is_held_before_reading_the_log(self):
        self.append("initial.md")
        lock = os.open(self.workspace, os.O_RDONLY)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen(
                [sys.executable, "-c", APPEND_PROCESS, str(HOOKS), str(self.workspace), "locked.md"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.assertEqual(process.stdout.readline(), "ready\n")
                process.stdin.write("start\n")
                process.stdin.flush()
                with selectors.DefaultSelector() as reader:
                    reader.register(process.stdout, selectors.EVENT_READ)
                    self.assertEqual(reader.select(timeout=0.25), [])
                self.assertEqual([entry["target"] for entry in self.read_entries()], ["initial.md"])
                fcntl.flock(lock, fcntl.LOCK_UN)
                output, errors = process.communicate("release\n", timeout=10)
                self.assertEqual(process.returncode, 0, errors)
                self.assertEqual((output, errors), ("read\n", ""))
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        finally:
            os.close(lock)
        self.assertEqual([entry["target"] for entry in self.read_entries()], ["initial.md", "locked.md"])

    def test_rewrite_replaces_the_log_atomically(self):
        self.append("initial.md")
        with self.log.open() as previous:
            before = previous.read()
            self.append("new.md")
            self.assertNotEqual(os.fstat(previous.fileno()).st_ino, self.log.stat().st_ino)
            previous.seek(0)
            self.assertEqual(previous.read(), before)
        # No lock or temporary file stays: a workspace snapshot enumerates every file of the attack.
        self.assertEqual({path.name for path in self.workspace.iterdir()}, {"problem.json", "activity.jsonl"})

    def test_failed_replacement_preserves_log_and_removes_temporary_file(self):
        self.append("initial.md")
        before = self.log.read_bytes()
        with patch.object(os, "replace", side_effect=OSError("replacement failed")):
            with self.assertRaisesRegex(OSError, "replacement failed"):
                self.append("new.md")
        self.assertEqual(self.log.read_bytes(), before)
        self.assertEqual({path.name for path in self.workspace.iterdir()}, {"problem.json", "activity.jsonl"})
        self.append("after-failure.md")
        self.assertEqual([entry["target"] for entry in self.read_entries()], ["initial.md", "after-failure.md"])


if __name__ == "__main__":
    unittest.main()
