"""Behavioral tests for durable, revisioned research records."""

import errno
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

from research_harness.errors import ResearchError
from research_harness.storage import Store


class ResearchStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.database = self.workspace / ".exactory" / "research.sqlite3"

    def assert_error(self, code, action):
        with self.assertRaises(ResearchError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertIsInstance(caught.exception.message, str)
        return caught.exception

    @contextmanager
    def database_connection(self):
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                yield connection

    def add(self, store, key="paper", title="Original", revision=0, request="add-1"):
        def apply(tx):
            tx.put("work", key, {"title": title})
            return {"id": key}
        return store.mutate("add", {"key": key, "title": title}, apply,
                            expected_revision=revision, request_id=request)

    def test_callback_contract_returns_the_original_committed_envelope(self):
        store = Store(self.workspace, create=True)
        calls = []

        def add(tx):
            calls.append(True)
            tx.put("work", "arxiv:1706.03762", {"title": "Attention Is All You Need"})
            return {"id": "arxiv:1706.03762"}

        first = store.mutate("test-add", {"id": "arxiv:1706.03762"}, add,
                             expected_revision=0, request_id="request-1")
        second = store.mutate("test-add", {"id": "arxiv:1706.03762"}, add,
                              expected_revision=0, request_id="request-1")
        self.assertEqual(first, {"revision": 1, "request_id": "request-1",
                                 "result": {"id": "arxiv:1706.03762"}})
        self.assertEqual(first, second)
        self.assertEqual(calls, [True])
        self.assertEqual(store.revision, 1)
        self.assertEqual(store.snapshot()["records"]["work"]["arxiv:1706.03762"]["title"],
                         "Attention Is All You Need")
        self.assertEqual(Store(self.workspace).snapshot(), store.snapshot())

    def test_replay_after_later_revisions_preserves_result_and_uses_canonical_payload(self):
        store = Store(self.workspace, create=True)
        first = self.add(store)
        self.add(store, title="Revised", revision=1, request="add-2")
        first["result"]["id"] = "caller changed its response"

        def must_not_run(tx):
            self.fail("A committed replay ran the callback")

        replay = Store(self.workspace).mutate(
            "add", {"title": "Original", "key": "paper"}, must_not_run,
            expected_revision=0, request_id="add-1")
        self.assertEqual(replay, {"revision": 1, "request_id": "add-1",
                                  "result": {"id": "paper"}})
        self.assertEqual(store.revision, 2)

    def test_conflicting_replays_and_stale_requests_never_run_callback(self):
        store = Store(self.workspace, create=True)
        self.add(store)

        def must_not_run(tx):
            self.fail("Rejected request ran its callback")

        for operation, payload in (("different", {"key": "paper", "title": "Original"}),
                                   ("add", {"key": "paper", "title": "Changed"})):
            self.assert_error("request_id_conflict", lambda: store.mutate(
                operation, payload, must_not_run, expected_revision=0, request_id="add-1"))
        error = self.assert_error("stale_revision", lambda: store.mutate(
            "add", {}, must_not_run, expected_revision=0, request_id="new"))
        self.assertEqual(error.details, {"expected_revision": 0, "revision": 1})
        self.assertEqual(store.snapshot(), {"revision": 1, "records": {
            "work": {"paper": {"title": "Original"}}}})

    def test_callback_failure_rolls_back_records_revision_and_request_receipt(self):
        store = Store(self.workspace, create=True)

        def fail(tx):
            tx.put("work", "paper", {"title": "Uncommitted"})
            tx.put("reading", "note", {"read": True})
            raise RuntimeError("Assessment failed")

        with self.assertRaisesRegex(RuntimeError, "Assessment failed"):
            store.mutate("add", {}, fail, expected_revision=0, request_id="retry")
        self.assertEqual(store.snapshot(), {"revision": 0, "records": {}})
        result = store.mutate("add", {}, lambda tx: {"retried": True},
                              expected_revision=0, request_id="retry")
        self.assertEqual(result["revision"], 1)
        with self.database_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)

    def test_invalid_json_result_rolls_back_a_successful_callback_write(self):
        store = Store(self.workspace, create=True)

        def invalid(tx):
            tx.put("work", "paper", {"title": "Uncommitted"})
            return {"score": float("nan")}

        self.assert_error("invalid_input", lambda: store.mutate(
            "add", {}, invalid, expected_revision=0, request_id="bad"))
        self.assertEqual(store.snapshot(), {"revision": 0, "records": {}})

    def test_transaction_reads_its_writes_and_copies_all_caller_owned_values(self):
        store = Store(self.workspace, create=True)
        escaped = []

        def apply(tx):
            escaped.append(tx)
            self.assertIsNone(tx.get("work", "missing"))
            self.assertEqual(tx.records("work"), {})
            original = {"title": "Original", "authors": ["One"]}
            tx.put("work", "paper", original)
            original["authors"].append("Not committed")
            read = tx.get("work", "paper")
            read["authors"].append("Also not committed")
            records = tx.records("work")
            records["paper"]["title"] = "Not committed"
            self.assertEqual(tx.get("work", "paper"), {"title": "Original", "authors": ["One"]})
            return ["accepted", None, 3]

        result = store.mutate("add", {}, apply, expected_revision=0, request_id="add")
        self.assertEqual(result["result"], ["accepted", None, 3])
        for action in (lambda: escaped[0].get("work", "paper"),
                       lambda: escaped[0].records("work"),
                       lambda: escaped[0].put("work", "paper", {})):
            self.assert_error("transaction_closed", action)

    def test_event_changes_preserve_each_original_value_without_corpus_snapshots(self):
        store = Store(self.workspace, create=True)
        self.add(store, key="untouched", title="Unrelated evidence")

        def update(tx):
            value = {"title": "First original", "authors": ["One"]}
            tx.put("work", "paper", value)
            value["title"] = "Second original"
            tx.put("work", "paper", value)
            value["title"] = "Caller mutation"
            return {"saved": True}

        store.mutate("update", {"source": "fixture"}, update,
                     expected_revision=1, request_id="update-1")
        self.add(store, title="Latest", revision=2, request="update-2")
        with self.database_connection() as connection:
            row = connection.execute("SELECT payload, changes FROM events WHERE revision = 2").fetchone()
        self.assertEqual(json.loads(row[0]), {"source": "fixture"})
        self.assertEqual(json.loads(row[1]), [
            {"kind": "work", "key": "paper", "value": {"title": "First original", "authors": ["One"]}},
            {"kind": "work", "key": "paper", "value": {"title": "Second original", "authors": ["One"]}},
        ])
        self.assertNotIn("Unrelated evidence", row[1])

    def test_record_json_is_canonical_and_has_an_independent_content_digest(self):
        store = Store(self.workspace, create=True)
        store.mutate("add", {}, lambda tx: tx.put("work", "paper", {"z": 1, "a": 2}),
                     expected_revision=0, request_id="add")
        with self.database_connection() as connection:
            row = connection.execute("SELECT value, digest FROM records").fetchone()
        self.assertEqual(row, ('{"a":2,"z":1}',
                              "c2985c5ba6f7d2a55e768f92490ca09388e95bc4cccb9fdf11b15f4d42f93e73"))

    def test_read_only_missing_store_and_failed_mutation_do_not_create_files(self):
        self.assert_error("store_missing", lambda: Store(self.workspace))
        self.assertFalse(self.workspace.exists())
        store = Store(self.workspace, create=True)
        self.database.unlink()
        before = sorted(path.relative_to(self.workspace).as_posix() for path in self.workspace.rglob("*"))
        for action in (lambda: store.snapshot(), lambda: store.revision,
                       lambda: store.mutate("add", {}, lambda tx: None,
                                            expected_revision=0, request_id="missing")):
            self.assert_error("store_missing", action)
            self.assertEqual(sorted(path.relative_to(self.workspace).as_posix()
                                    for path in self.workspace.rglob("*")), before)

    def test_read_only_open_and_reads_do_not_write_a_valid_database(self):
        store = Store(self.workspace, create=True)
        self.add(store)
        before = {path.relative_to(self.workspace): (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in self.workspace.rglob("*") if path.is_file()}
        opened = Store(self.workspace)
        self.assertEqual(opened.revision, 1)
        self.assertEqual(opened.snapshot()["records"]["work"]["paper"], {"title": "Original"})
        after = {path.relative_to(self.workspace): (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in self.workspace.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_two_connections_serialize_and_second_stale_writer_is_rejected(self):
        first = Store(self.workspace, create=True)
        second = Store(self.workspace)
        entered = threading.Event()
        release = threading.Event()
        outcomes = []

        def first_apply(tx):
            tx.put("work", "first", {"title": "First"})
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release writer")
            return "first"

        def run_first():
            try:
                outcomes.append(first.mutate("first", {}, first_apply,
                                             expected_revision=0, request_id="first"))
            except BaseException as error:
                outcomes.append(error)

        def run_second():
            try:
                outcomes.append(self.add(second, key="second", request="second"))
            except BaseException as error:
                outcomes.append(error)

        writer = threading.Thread(target=run_first)
        contender = threading.Thread(target=run_second)
        writer.start()
        self.assertTrue(entered.wait(5))
        contender.start()
        release.set()
        writer.join(5)
        contender.join(5)
        self.assertFalse(writer.is_alive())
        self.assertFalse(contender.is_alive())
        errors = [value for value in outcomes if isinstance(value, BaseException)]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ResearchError)
        self.assertEqual(errors[0].code, "stale_revision")
        self.assertEqual(first.snapshot(), {"revision": 1, "records": {
            "work": {"first": {"title": "First"}}}})

    def test_writer_lock_wait_is_finite_and_failed_attempt_is_retryable(self):
        store = Store(self.workspace, create=True)
        with self.database_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            self.assert_error("store_busy", lambda: self.add(store))
            self.assertLess(time.monotonic() - started, 6)
            connection.rollback()
        self.assertEqual(self.add(store)["revision"], 1)

    def test_nested_store_access_fails_promptly_without_damaging_outer_transaction(self):
        store = Store(self.workspace, create=True)

        def apply(tx):
            tx.put("work", "paper", {"title": "Outer transaction"})
            for action in (store.snapshot, lambda: Store(self.workspace, create=True),
                           lambda: self.add(store, request="nested")):
                started = time.monotonic()
                self.assert_error("store_busy", action)
                self.assertLess(time.monotonic() - started, 3)
            return tx.get("work", "paper")

        store.mutate("outer", {}, apply, expected_revision=0, request_id="outer")
        self.assertEqual(store.snapshot(), {"revision": 1, "records": {
            "work": {"paper": {"title": "Outer transaction"}}}})

    def test_directory_alias_access_uses_the_active_directory_identity(self):
        store = Store(self.workspace, create=True)
        alias = self.workspace.with_name("WORKSPACE")
        same_spelling_alias = alias.exists()
        if same_spelling_alias:
            self.assertTrue(os.path.samefile(alias, self.workspace))
        probe = """
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1], timeout=0.1)
try:
    connection.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as error:
    print(str(error))
else:
    print("Unexpectedly acquired the writer lock")
finally:
    connection.close()
"""

        def apply(tx):
            # A rename supplies the same-inode alias on case-sensitive filesystems.
            if not same_spelling_alias:
                self.workspace.rename(alias)
            try:
                started = time.monotonic()
                self.assert_error("store_busy", lambda: Store(alias))
                self.assertLess(time.monotonic() - started, 3)
                result = subprocess.run(
                    [sys.executable, "-c", probe, str(alias / ".exactory" / "research.sqlite3")],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("locked", result.stdout)
            finally:
                if not same_spelling_alias:
                    alias.rename(self.workspace)
            tx.put("work", "paper", {"title": "Original writer"})
            return "committed"

        response = store.mutate("add", {}, apply, expected_revision=0, request_id="original")
        self.assertEqual(response["result"], "committed")
        self.assertEqual(store.snapshot(), {"revision": 1, "records": {
            "work": {"paper": {"title": "Original writer"}}}})

    def test_existing_store_rebinds_coordination_after_metadata_directory_replacement(self):
        previous = Store(self.workspace, create=True)
        self.database.parent.rename(self.workspace / "previous-metadata")
        replacement = Store(self.workspace, create=True)

        def apply(tx):
            self.assert_error("store_busy", previous.snapshot)
            tx.put("work", "replacement", {"title": "Current directory"})
            return None

        replacement.mutate("replace", {}, apply, expected_revision=0, request_id="replacement")
        self.assertEqual(previous.snapshot(), {"revision": 1, "records": {
            "work": {"replacement": {"title": "Current directory"}}}})

    def test_replaced_regular_metadata_directory_is_rejected_before_sqlite_mutation(self):
        store = Store(self.workspace, create=True)
        replacement_workspace = Path(self.temporary.name) / "replacement"
        Store(replacement_workspace, create=True)
        replacement_directory = replacement_workspace / ".exactory"
        replacement_bytes = (replacement_directory / "research.sqlite3").read_bytes()
        original_open = store._open

        def replace_directory_then_open(path, *, writable):
            self.database.parent.rename(self.workspace / "previous-metadata")
            replacement_directory.rename(self.database.parent)
            return original_open(path, writable=writable)

        with mock.patch.object(store, "_open", side_effect=replace_directory_then_open):
            self.assert_error("unsafe_path", lambda: self.add(store))
        self.assertEqual(self.database.read_bytes(), replacement_bytes)

    def test_initialization_propagates_workspace_and_metadata_parent_sync_failures(self):
        original_fsync = os.fsync
        for target_name in ("workspace-parent", "metadata-parent"):
            with self.subTest(target=target_name):
                workspace = Path(self.temporary.name) / target_name
                target = workspace.parent if target_name == "workspace-parent" else workspace

                def fail_target_sync(descriptor):
                    info = os.fstat(descriptor)
                    if target.exists():
                        expected = target.stat()
                        if (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino):
                            raise OSError(errno.EIO, "Injected directory synchronization failure")
                    original_fsync(descriptor)

                with mock.patch("research_harness.artifacts.os.fsync", side_effect=fail_target_sync):
                    self.assert_error("storage_io", lambda: Store(workspace, create=True))
                self.assertFalse((workspace / ".exactory" / "research.sqlite3").exists())
                self.assertEqual(Store(workspace, create=True).snapshot(), {"revision": 0, "records": {}})

    def test_existing_initialization_establishes_database_directory_durability(self):
        Store(self.workspace, create=True)
        directory = self.database.parent.stat()
        original_fsync = os.fsync

        def fail_database_directory_sync(descriptor):
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                raise OSError(errno.EIO, "Injected database publication synchronization failure")
            original_fsync(descriptor)

        with mock.patch("research_harness.storage.os.fsync", side_effect=fail_database_directory_sync):
            self.assert_error("storage_io", lambda: Store(self.workspace, create=True))
            self.assertEqual(Store(self.workspace).snapshot(), {"revision": 0, "records": {}})

    def test_concurrent_read_cannot_release_another_managed_writers_process_lock(self):
        store = Store(self.workspace, create=True)
        entered = threading.Event()
        release = threading.Event()
        results = []

        def apply(tx):
            tx.put("work", "paper", {"title": "Reserved writer"})
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Test did not release writer")
            return None

        def writer():
            try:
                store.mutate("write", {}, apply, expected_revision=0, request_id="write")
                results.append("committed")
            except BaseException as error:
                results.append(error)

        probe = """
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1], timeout=0.1)
try:
    connection.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as error:
    print(str(error))
else:
    print("Unexpectedly acquired the writer lock")
finally:
    connection.close()
"""

        def assert_external_writer_blocked():
            result = subprocess.run([sys.executable, "-c", probe, str(self.database)],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("locked", result.stdout)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            assert_external_writer_blocked()
            try:
                Store(self.workspace).snapshot()
            except ResearchError as error:
                self.assertEqual(error.code, "store_busy")
            assert_external_writer_blocked()
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(results, ["committed"])

    def test_process_exit_during_mutation_leaves_no_partial_commit(self):
        Store(self.workspace, create=True)
        script = """
import os
import sys
from pathlib import Path
from research_harness.storage import Store
store = Store(Path(sys.argv[1]))
def crash(tx):
    tx.put("work", "lost", {"title": "Not committed"})
    os._exit(23)
store.mutate("crash", {}, crash, expected_revision=0, request_id="crash")
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.workspace)],
                                cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr.decode())
        store = Store(self.workspace)
        self.assertEqual(store.snapshot(), {"revision": 0, "records": {}})
        self.assertEqual(self.add(store, request="crash")["revision"], 1)

    def test_hot_journal_needs_explicit_recovery_and_preserves_committed_state(self):
        store = Store(self.workspace, create=True)
        committed = {"title": "Committed", "text": "a" * (4 * 1024 * 1024)}
        store.mutate("add", {}, lambda tx: tx.put("work", "paper", committed),
                     expected_revision=0, request_id="committed")
        script = """
import json
import os
import sqlite3
import sys
from pathlib import Path
connection = sqlite3.connect(Path(sys.argv[1]) / ".exactory" / "research.sqlite3")
connection.execute("PRAGMA cache_size = 8")
connection.execute("PRAGMA cache_spill = 8")
connection.execute("BEGIN IMMEDIATE")
connection.execute("UPDATE records SET value = ?", (
    json.dumps({"title": "Uncommitted", "text": "b" * (4 * 1024 * 1024)}),))
os._exit(23)
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.workspace)],
                                cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr.decode())
        journal = Path(str(self.database) + "-journal")
        self.assertTrue(journal.is_file())
        self.assertNotEqual(journal.read_bytes()[:8], b"\x00" * 8)
        before = (self.database.read_bytes(), journal.read_bytes())
        self.assert_error("store_recovery_required", lambda: Store(self.workspace))
        self.assertEqual((self.database.read_bytes(), journal.read_bytes()), before)
        recovered = Store(self.workspace, create=True)
        self.assertEqual(recovered.snapshot(), {"revision": 1, "records": {"work": {"paper": committed}}})
        replay = recovered.mutate("add", {}, lambda tx: self.fail("Replayed recovery callback"),
                                  expected_revision=0, request_id="committed")
        self.assertEqual(replay, {"revision": 1, "request_id": "committed", "result": None})
        self.assertEqual(self.add(recovered, key="after", revision=1, request="crash")["revision"], 2)

    def test_concurrent_store_creation_produces_one_usable_schema(self):
        start = threading.Barrier(6)
        results = []

        def create():
            start.wait()
            try:
                results.append(Store(self.workspace, create=True).snapshot())
            except BaseException as error:
                results.append(error)

        threads = [threading.Thread(target=create) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results, [{"revision": 0, "records": {}}] * 6)
        self.assertEqual(list(self.database.parent.iterdir()), [self.database])

    def test_published_initial_schema_is_readable_before_temporary_link_cleanup(self):
        import os

        published = threading.Event()
        release = threading.Event()
        original_link = os.link
        results = []

        def link_then_pause(*args, **kwargs):
            original_link(*args, **kwargs)
            published.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release schema publisher")

        def create():
            try:
                results.append(Store(self.workspace, create=True).revision)
            except BaseException as error:
                results.append(error)

        with mock.patch("research_harness.storage.os.link", side_effect=link_then_pause):
            writer = threading.Thread(target=create)
            writer.start()
            try:
                self.assertTrue(published.wait(5))
                script = """
import json
import sys
from pathlib import Path
from research_harness.storage import Store
print(json.dumps(Store(Path(sys.argv[1])).snapshot()))
"""
                result = subprocess.run([sys.executable, "-c", script, str(self.workspace)],
                                        cwd=Path(__file__).resolve().parent.parent,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"revision": 0, "records": {}})
            finally:
                release.set()
                writer.join(5)
        self.assertEqual(results, [0])

    def test_concurrent_same_request_returns_one_result_and_calls_once(self):
        store = Store(self.workspace, create=True)
        start = threading.Barrier(2)
        results = []
        calls = []

        def apply(tx):
            calls.append(True)
            tx.put("work", "paper", {"title": "Original"})
            return {"id": "paper"}

        def mutate():
            start.wait()
            try:
                results.append(store.mutate("add", {}, apply, expected_revision=0, request_id="same"))
            except BaseException as error:
                results.append(error)

        threads = [threading.Thread(target=mutate) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(calls, [True])
        self.assertEqual(results, [{"revision": 1, "request_id": "same", "result": {"id": "paper"}}] * 2)
        self.assertEqual(store.revision, 1)

    def test_future_schema_is_rejected_without_upgrading_or_modifying_it(self):
        Store(self.workspace, create=True)
        with self.database_connection() as connection:
            connection.execute("UPDATE metadata SET schema_version = 999")
        before = self.database.read_bytes()
        for create in (False, True):
            self.assert_error("unsupported_schema", lambda: Store(self.workspace, create=create))
        self.assertEqual(self.database.read_bytes(), before)

    def test_corrupt_database_and_schema_fail_explicitly(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b"This is not SQLite")
        self.assert_error("corrupt_state", lambda: Store(self.workspace))
        self.database.unlink()
        Store(self.workspace, create=True)
        with self.database_connection() as connection:
            connection.execute("DROP TABLE records")
        self.assert_error("corrupt_state", lambda: Store(self.workspace))

    def test_corrupt_projection_digest_and_revision_are_detected(self):
        store = Store(self.workspace, create=True)
        self.add(store)
        with self.database_connection() as connection:
            connection.execute("UPDATE records SET value = ?", ('{"title":"Forged"}',))
        self.assert_error("corrupt_state", store.snapshot)
        with self.database_connection() as connection:
            connection.execute("UPDATE records SET value = ?", ('{"title":"Original"}',))
            connection.execute("UPDATE metadata SET revision = 2")
        self.assert_error("corrupt_state", store.snapshot)

    def test_receipt_json_types_must_match_the_immutable_event(self):
        store = Store(self.workspace, create=True)
        store.mutate("typed", {}, lambda tx: 1, expected_revision=0, request_id="typed")
        original_response = '{"request_id":"typed","result":1,"revision":1}'
        original_digest = hashlib.sha256(original_response.encode("utf-8")).hexdigest()
        with self.database_connection() as connection:
            original_dump = "\n".join(connection.iterdump())
            original_guards = connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name").fetchall()
        self.assertEqual(original_dump.count(original_response), 1)
        self.assertEqual(original_dump.count(original_digest), 1)
        for changed_response in (
                '{"request_id":"typed","result":true,"revision":1}',
                '{"request_id":"typed","result":1.0,"revision":1}',
                '{"request_id":"typed","result":1,"revision":true}',
                '{"request_id":"typed","result":1,"revision":1.0}'):
            with self.subTest(response=changed_response):
                changed_digest = hashlib.sha256(changed_response.encode("utf-8")).hexdigest()
                changed_dump = original_dump.replace(original_response, changed_response).replace(
                    original_digest, changed_digest)
                corrupted = self.database.parent / "corrupted.sqlite3"
                # Rebuild an offline fixture from SQL, retaining all normal guards.
                with closing(sqlite3.connect(corrupted)) as connection:
                    connection.executescript(changed_dump)
                    guards = connection.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name").fetchall()
                    self.assertEqual(guards, original_guards)
                    receipt = connection.execute("SELECT response, digest FROM receipts").fetchone()
                    self.assertEqual(receipt, (changed_response, changed_digest))
                os.replace(corrupted, self.database)
                self.assert_error("corrupt_state", lambda: Store(self.workspace).snapshot())

    def test_construction_checks_structure_and_first_read_replays_history(self):
        from unittest import mock
        from research_harness import storage
        Store(self.workspace, create=True)
        calls = []
        original = storage._validate

        def counting(connection, **kwargs):
            calls.append(kwargs.get("replay", True))
            return original(connection, **kwargs)

        with mock.patch.object(storage, "_validate", counting):
            store = storage.Store(self.workspace)
            store.snapshot()
        self.assertEqual(calls, [False, True])

    def test_record_tampering_is_caught_at_first_use_not_at_construction(self):
        store = Store(self.workspace, create=True)
        self.add(store)
        with self.database_connection() as connection:
            connection.execute("UPDATE records SET value = ?, digest = ?", ('{"title":"Forged"}', "0" * 64))
        reopened = Store(self.workspace)
        self.assert_error("corrupt_state", reopened.snapshot)
        self.assert_error("corrupt_state", lambda: reopened.revision)
        self.assert_error("corrupt_state", lambda: reopened.mutate("add", {}, lambda tx: None, expected_revision=1, request_id="later"))

    def test_events_and_receipts_reject_updates_and_deletions(self):
        store = Store(self.workspace, create=True)
        self.add(store)
        with self.database_connection() as connection:
            for statement in ("UPDATE events SET operation = 'forged'", "DELETE FROM events",
                              "UPDATE receipts SET request_id = 'forged'", "DELETE FROM receipts"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
                connection.rollback()
        self.assertEqual(store.revision, 1)

    def test_invalid_mutation_inputs_fail_without_running_callback(self):
        store = Store(self.workspace, create=True)
        cases = [dict(operation=""), dict(operation="\ud800"), dict(request_id="\ud800"),
                 dict(payload=[]), dict(payload={"value": float("inf")}),
                 dict(payload={1: "not a JSON object key"}), dict(request_id=""),
                 dict(expected_revision=True), dict(expected_revision=-1), dict(apply=None)]
        for changes in cases:
            arguments = dict(operation="add", payload={}, apply=lambda tx: self.fail("invalid callback"),
                             expected_revision=0, request_id="add")
            arguments.update(changes)
            self.assert_error("invalid_input", lambda: store.mutate(**arguments))
        self.assertEqual(store.revision, 0)

    def test_invalid_record_values_roll_back_prior_writes(self):
        store = Store(self.workspace, create=True)
        for kind, key, value in (("", "paper", {}), ("work", "", {}), ("work", None, {}),
                                  ("work", "\ud800", {}),
                                  ("work", "paper", []), ("work", "paper", {"bad": object()})):
            def apply(tx):
                tx.put("work", "valid", {})
                tx.put(kind, key, value)
            self.assert_error("invalid_input", lambda: store.mutate(
                "add", {}, apply, expected_revision=0, request_id="add"))
        self.assertEqual(store.snapshot(), {"revision": 0, "records": {}})

    def test_transaction_rejects_missing_or_invalid_record_lookup_keys(self):
        store = Store(self.workspace, create=True)
        for key in (None, "", "\ud800"):
            self.assert_error("invalid_input", lambda: store.mutate(
                "read", {}, lambda tx: tx.get("work", key), expected_revision=0, request_id="read"))
        self.assertEqual(store.revision, 0)

    def test_existing_store_object_rechecks_directory_boundaries_before_mutation(self):
        store = Store(self.workspace, create=True)
        outside = Path(self.temporary.name) / "outside"
        self.database.parent.rename(outside)
        (self.workspace / ".exactory").symlink_to(outside, target_is_directory=True)
        before = (outside / "research.sqlite3").read_bytes()
        self.assert_error("unsafe_path", lambda: self.add(store))
        self.assertEqual((outside / "research.sqlite3").read_bytes(), before)

    def test_sqlite_rejects_a_parent_symlink_replaced_during_connection_open(self):
        store = Store(self.workspace, create=True)
        outside = Path(self.temporary.name) / "outside"
        Store(outside, create=True)
        outside_database = outside / ".exactory" / "research.sqlite3"
        before = outside_database.read_bytes()
        original_open = store._open

        def replace_parent_then_open(path, *, writable):
            self.database.parent.rename(self.workspace / "saved-metadata")
            self.database.parent.symlink_to(outside / ".exactory", target_is_directory=True)
            return original_open(path, writable=writable)

        with mock.patch.object(store, "_open", side_effect=replace_parent_then_open):
            self.assert_error("unsafe_path", lambda: self.add(store))
        self.assertEqual(outside_database.read_bytes(), before)

    def test_symlinked_workspace_is_rejected(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.workspace.symlink_to(outside, target_is_directory=True)
        self.assert_error("unsafe_path", lambda: Store(self.workspace, create=True))
        self.assertEqual(list(outside.iterdir()), [])

    def test_database_and_sqlite_sidecar_symlinks_never_touch_external_files(self):
        Store(self.workspace, create=True)
        original = self.database.read_bytes()
        outside = Path(self.temporary.name) / "outside.sqlite3"
        outside.write_bytes(original)
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(self.database) + suffix)
            if suffix == "":
                candidate.unlink()
            candidate.symlink_to(outside)
            before = outside.read_bytes()
            for create in (False, True):
                self.assert_error("unsafe_path", lambda: Store(self.workspace, create=create))
            self.assertEqual(outside.read_bytes(), before)
            candidate.unlink()
            if suffix == "":
                candidate.write_bytes(original)

    def test_hardlinked_database_and_sidecars_are_rejected_without_changing_aliases(self):
        import os

        Store(self.workspace, create=True)
        outside = Path(self.temporary.name) / "outside"
        os.link(self.database, outside)
        before = outside.read_bytes()
        self.assert_error("unsafe_path", lambda: Store(self.workspace))
        self.assert_error("unsafe_path", lambda: Store(self.workspace, create=True))
        self.assertEqual(outside.read_bytes(), before)
        outside.unlink()
        outside.write_bytes(b"journal data")
        for suffix in ("-journal", "-wal", "-shm"):
            candidate = Path(str(self.database) + suffix)
            os.link(outside, candidate)
            self.assert_error("unsafe_path", lambda: Store(self.workspace, create=True))
            self.assertEqual(outside.read_bytes(), b"journal data")
            candidate.unlink()

    def test_symlinked_metadata_directory_is_rejected_before_store_creation(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.workspace.mkdir()
        (self.workspace / ".exactory").symlink_to(outside, target_is_directory=True)
        self.assert_error("unsafe_path", lambda: Store(self.workspace, create=True))
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
