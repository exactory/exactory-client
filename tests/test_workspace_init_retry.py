"""Public initialization retries preserve their original request and later work."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from research_harness.storage import Store


PLUGIN = Path(__file__).resolve().parents[1]


class InitializationRetryCases:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / (self.kind + " workspace")
        self.marker = self.workspace / (".exactory/" + self.kind + ".json")
        self.environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.environment.pop("PYTHONPATH", None)
        self.arguments = ["init", "--dir", str(self.workspace), *self.options,
                          "--expected-revision", "0", "--request-id", "original-init"]
        self.observations = {"kind": self.kind, "commands": []}

    def command(self, program, arguments, *, cwd=None):
        result = subprocess.run([sys.executable, "-B", str(PLUGIN / "bin" / program), *arguments],
            cwd=cwd or self.root, env=self.environment, capture_output=True, text=True, timeout=20)
        self.observations["commands"].append({"program": program, "arguments": arguments,
            "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        return result

    def initialize(self):
        result = self.command(self.program, self.arguments)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return json.loads(self.marker.read_text())

    def history(self):
        database = self.workspace / ".exactory/research.sqlite3"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            events = connection.execute("SELECT * FROM events ORDER BY revision").fetchall()
            receipts = connection.execute("SELECT * FROM receipts ORDER BY revision").fetchall()
        return {"snapshot": Store(self.workspace).snapshot(), "events": events, "receipts": receipts,
                "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest()}

    def changed_arguments(self, **changes):
        values = list(self.arguments)
        for key, value in changes.items():
            values[values.index("--" + key.replace("_", "-")) + 1] = value
        return values

    def wait_for_a_different_timestamp(self, created):
        deadline = time.monotonic() + 3
        while time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) == created:
            self.assertLess(time.monotonic(), deadline, "The actual initialization clock did not advance")
            time.sleep(0.02)

    def advance_study(self):
        if self.kind == "draft":
            result = self.command("exactory-lab", ["init", "--dir", str(self.workspace),
                "--slug", "later-study", "--expected-revision", str(Store(self.workspace).revision),
                "--request-id", "later-study"])
            self.assertEqual((result.returncode, result.stderr), (0, ""))
        result = self.command("exactory-lab", ["state", "set", "--stage", "cohort",
            "--status", "pending", "--waiting", "reading", "--loop-budget", "7",
            "--expected-revision", str(Store(self.workspace).revision), "--request-id", "later-state"],
            cwd=self.workspace)
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def preserved_files(self):
        files = {}
        for path in self.workspace.rglob("*"):
            relative = path.relative_to(self.workspace).as_posix()
            if path.is_file() and (relative.startswith(".git/") or relative in {
                    ".gitignore", "context/README.md", "research/literature.md", "research/user-notes.md"}):
                files[relative] = (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        return files

    def save_user_files(self):
        for relative in ("context/README.md", "research/literature.md", ".gitignore", "research/user-notes.md"):
            path = self.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("User-maintained content in " + relative + "\n")

    def test_identical_request_replays_after_the_actual_generated_timestamp_changes(self):
        original = self.initialize()
        before = self.history()
        files = self.preserved_files()
        self.wait_for_a_different_timestamp(original["created"])
        retry = self.command(self.program, self.arguments)
        self.assertEqual((retry.returncode, retry.stderr), (0, ""), json.dumps(self.observations))
        self.assertEqual(self.history(), before)
        self.assertEqual(json.loads(self.marker.read_text()), original)
        self.assertEqual(self.preserved_files(), files)

    def test_replay_preserves_later_state_user_files_and_existing_git(self):
        self.initialize()
        self.advance_study()
        self.save_user_files()
        self.assertTrue((self.workspace / ".git").is_dir())
        before = self.history()
        files = self.preserved_files()
        retry = self.command(self.program, self.arguments)
        self.assertEqual((retry.returncode, retry.stderr), (0, ""), json.dumps(self.observations))
        self.assertEqual(self.history(), before)
        self.assertEqual(self.preserved_files(), files)
        study = json.loads((self.workspace / ".exactory/study.json").read_text())
        self.assertEqual((study["stage"], study["waiting"], study["loop"]["budget"]), ("cohort", "reading", 7))

    def test_same_request_with_different_user_arguments_is_a_conflict(self):
        self.initialize()
        before = self.history()
        for key, value in self.conflicts.items():
            changed = self.command(self.program, self.changed_arguments(**{key: value}))
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("request_id_conflict", changed.stderr, json.dumps(self.observations))
            self.assertEqual(self.history(), before)

    def test_a_new_request_cannot_reinitialize_an_existing_workspace(self):
        self.initialize()
        before = self.history()
        files = self.preserved_files()
        changed = self.command(self.program, self.changed_arguments(request_id="another-init", expected_revision="1"))
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("already", changed.stderr)
        self.assertEqual(self.history(), before)
        self.assertEqual(self.preserved_files(), files)

    def test_replay_repairs_a_missing_projection_from_the_current_state(self):
        self.initialize()
        self.advance_study()
        self.save_user_files()
        before = self.history()
        files = self.preserved_files()
        self.marker.unlink()
        retry = self.command(self.program, self.arguments)
        self.assertEqual((retry.returncode, retry.stderr), (0, ""), json.dumps(self.observations))
        self.assertEqual(json.loads(self.marker.read_text()), before["snapshot"]["records"]["workspace"][self.kind])
        self.assertEqual(self.history(), before)
        self.assertEqual(self.preserved_files(), files)

    def test_retry_completes_layout_after_the_real_commit_precedes_an_interruption(self):
        self.save_user_files()
        files = self.preserved_files()
        source = """
import runpy,sys
from unittest.mock import patch
module = runpy.run_path(sys.argv[1])
args = module["_build_parser"]().parse_args(sys.argv[2:])
with patch("research_harness.integration.export_workspace",
           side_effect=RuntimeError("interrupted projection publication")):
    args.handler(args)
"""
        interrupted = subprocess.run([sys.executable, "-B", "-c", source,
            str(PLUGIN / "bin" / self.program), *self.arguments], cwd=self.root,
            env=self.environment, capture_output=True, text=True, timeout=20)
        self.observations["interruption"] = {"exit_code": interrupted.returncode,
            "stdout": interrupted.stdout, "stderr": interrupted.stderr}
        self.assertNotEqual(interrupted.returncode, 0)
        self.assertIn("interrupted projection publication", interrupted.stderr)
        self.assertFalse(self.marker.exists())
        before = self.history()
        self.assertEqual(before["snapshot"]["revision"], 1)
        self.wait_for_a_different_timestamp(before["snapshot"]["records"]["workspace"][self.kind]["created"])
        retry = self.command(self.program, self.arguments)
        self.assertEqual((retry.returncode, retry.stderr), (0, ""), json.dumps(self.observations))
        self.assertEqual(self.history(), before)
        self.assertEqual(json.loads(self.marker.read_text()), before["snapshot"]["records"]["workspace"][self.kind])
        after_files = self.preserved_files()
        for path, value in files.items():
            self.assertEqual(after_files[path], value)
        if self.kind == "study":
            self.assertTrue((self.workspace / ".git").is_dir())
        again = self.command(self.program, self.arguments)
        self.assertEqual((again.returncode, again.stderr), (0, ""))
        self.assertEqual(self.history(), before)
        self.assertEqual(self.preserved_files(), after_files)

    def test_an_unmanaged_legacy_marker_requires_adoption_without_creating_a_store(self):
        self.marker.parent.mkdir(parents=True)
        original = b'{"version":1,"user_history":"Retain these exact bytes"}\n'
        self.marker.write_bytes(original)
        refused = self.command(self.program, self.arguments)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertFalse((self.marker.parent / "research.sqlite3").exists())

    def test_a_new_stale_request_commits_no_initialization_or_seed_files(self):
        payload = self.root / "configuration.json"
        payload.write_text(json.dumps({"profile": "research", "target": None}))
        configured = self.command("exactory-research", ["init", "--workspace", str(self.workspace),
            "--file", str(payload), "--expected-revision", "0", "--request-id", "configuration"])
        self.assertEqual((configured.returncode, configured.stderr), (0, ""))
        before = self.history()
        refused = self.command(self.program, self.arguments)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("stale_revision", refused.stderr)
        self.assertEqual(self.history(), before)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.preserved_files(), {})


class DraftInitializationRetryTests(InitializationRetryCases, unittest.TestCase):
    kind = "draft"
    program = "exactory-draft"
    options = ["--title", "Preserved draft", "--category", "cs.MA", "--corpus", "arxiv"]
    conflicts = {"title": "Different draft", "category": "math.CO", "corpus": "openalex"}


class StudyInitializationRetryTests(InitializationRetryCases, unittest.TestCase):
    kind = "study"
    program = "exactory-lab"
    options = ["--slug", "preserved-study"]
    conflicts = {"slug": "different-study"}


if __name__ == "__main__":
    unittest.main()
