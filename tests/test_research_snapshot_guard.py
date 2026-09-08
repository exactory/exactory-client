"""A scoped common snapshot retains ownership until its consumer finishes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from research_harness.errors import ResearchError
from research_harness.storage import Store


WRITER = """
import json
from pathlib import Path
import sys
from research_harness.errors import ResearchError
from research_harness.storage import Store
store = Store(Path(sys.argv[1]))
try:
    value = store.mutate("fixture", {}, lambda tx: tx.put("note", "entry", {"text": "Preserved"}),
                         expected_revision=0, request_id="writer")
except ResearchError as error:
    value = {"error": error.code}
print(json.dumps(value))
"""


class SnapshotGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "study"
        self.store = Store(self.root, create=True)
        self.environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.environment.pop("PYTHONPATH", None)
        self.plugin = Path(__file__).resolve().parents[1]

    def writer(self):
        result = subprocess.run([sys.executable, "-B", "-c", WRITER, str(self.root)],
            cwd=self.plugin, env=self.environment, capture_output=True, text=True, timeout=8)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return json.loads(result.stdout)

    def test_guard_blocks_a_real_writer_commit_and_identical_retry_later_succeeds(self):
        before = self.store.snapshot()
        with self.store.guarded_snapshot() as guard:
            self.assertEqual(guard.root, self.root)
            self.assertEqual(guard.snapshot(), before)
            self.assertEqual(self.writer(), {"error": "store_busy"})
            self.assertEqual(guard.snapshot(), before)
            reader = subprocess.run([sys.executable, "-B", "-c",
                "import json,sys; from pathlib import Path; from research_harness.storage import Store; "
                "print(json.dumps(Store(Path(sys.argv[1])).snapshot()))", str(self.root)],
                cwd=self.plugin, env=self.environment, capture_output=True, text=True, timeout=8)
            self.assertEqual((reader.returncode, reader.stderr), (0, ""))
            self.assertEqual(json.loads(reader.stdout), before)
        self.assertEqual(self.store.snapshot(), before)
        receipt = self.writer()
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(self.writer(), receipt)
        self.assertEqual(self.store.revision, 1)

    def test_guard_returns_isolated_data_and_cannot_be_used_after_release(self):
        with self.store.guarded_snapshot() as guard:
            returned = guard.snapshot()
            returned["records"]["caller"] = {"changed": True}
            self.assertEqual(guard.snapshot(), {"revision": 0, "records": {}})
        with self.assertRaises(ResearchError) as caught:
            guard.snapshot()
        self.assertEqual(caught.exception.code, "invalid_snapshot_guard")
        self.assertEqual(self.store.snapshot(), {"revision": 0, "records": {}})

    def test_exception_releases_ownership_after_bounded_nested_access_refusal(self):
        with self.assertRaisesRegex(RuntimeError, "consumer interrupted"):
            with self.store.guarded_snapshot() as guard:
                with self.assertRaises(ResearchError) as caught:
                    self.store.snapshot()
                self.assertEqual(caught.exception.code, "store_busy")
                self.assertEqual(guard.snapshot(), {"revision": 0, "records": {}})
                raise RuntimeError("consumer interrupted")
        self.assertEqual(self.writer()["revision"], 1)
        with self.assertRaises(ResearchError) as caught:
            guard.snapshot()
        self.assertEqual(caught.exception.code, "invalid_snapshot_guard")

    def test_busy_guard_entry_does_not_retain_ownership_or_change_history(self):
        source = """
import sqlite3,sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("BEGIN EXCLUSIVE")
print("held", flush=True)
sys.stdin.readline()
connection.rollback()
connection.close()
"""
        owner = subprocess.Popen([sys.executable, "-B", "-c", source,
            str(self.root / ".exactory/research.sqlite3")], cwd=self.plugin, env=self.environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(owner.stdout.readline().strip(), "held")
            with self.assertRaises(ResearchError) as caught:
                with self.store.guarded_snapshot():
                    self.fail("An exclusive database writer allowed a guarded read")
            self.assertEqual(caught.exception.code, "store_busy")
        finally:
            output, error = owner.communicate("release\n", timeout=8)
        self.assertEqual((owner.returncode, output, error), (0, "", ""))
        with self.store.guarded_snapshot() as guard:
            self.assertEqual(guard.snapshot(), {"revision": 0, "records": {}})
        self.assertEqual(self.writer()["revision"], 1)

    def test_terminated_guard_owner_releases_the_database_for_a_writer(self):
        source = """
from pathlib import Path
import sys
from research_harness.storage import Store
with Store(Path(sys.argv[1])).guarded_snapshot() as guard:
    print(guard.snapshot()["revision"], flush=True)
    sys.stdin.readline()
"""
        owner = subprocess.Popen([sys.executable, "-B", "-c", source, str(self.root)],
            cwd=self.plugin, env=self.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(owner.stdout.readline().strip(), "0")
            self.assertEqual(self.writer(), {"error": "store_busy"})
        finally:
            owner.terminate()
            output, error = owner.communicate(timeout=8)
        self.assertEqual((output, error), ("", ""))
        self.assertLess(owner.returncode, 0)
        self.assertEqual(self.writer()["revision"], 1)


if __name__ == "__main__":
    unittest.main()
