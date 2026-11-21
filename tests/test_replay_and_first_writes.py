"""A committed request is answered beside a reader and beside a writer, and a command waits for a busy store: its
reads wait for a COMMIT in progress and its COMMIT for the readers of the store (record
20261120104814-replay-and-first-writes-wait-for-readers-on-a-large-store)."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from finishing_write_fixtures import OtherWriters
from research_fixtures import atom, client, entry, xml_response
from research_harness import acquisition, cli, finishing, output_views, review_worker, storage
from research_harness.errors import ResearchError
from research_harness.operations import prepared_mutation, prepared_task_mutation
from research_harness.storage import Store

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFINITION = {"corpus": "arxiv", "primaryCategory": "cs.LG", "windowStart": "2026-01-01", "windowEnd": "2026-06-30"}


def load_bin_module(command_name, module_name):
    loader = importlib.machinery.SourceFileLoader(module_name, str(PLUGIN_ROOT / "bin" / command_name))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def run_command_main(main, argv, cwd):
    """Run the main of a bin command with argv as its command line, in cwd. Returns the exit status, stdout and
    stderr."""
    output, errors = io.StringIO(), io.StringIO()
    directory = os.getcwd()
    os.chdir(cwd)
    try:
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(errors):
            try:
                main(render_views=lambda start: None)
                status = 0
            except SystemExit as stop:
                status = stop.code
    finally:
        os.chdir(directory)
    return status, output.getvalue(), errors.getvalue()


def write_row(transaction, *revision):
    transaction.put("fixture_row", "row", {"n": 1})
    return {"written": True}


def prepare_row(records, value):
    return [("fixture_row", "row", {"n": 1})], {"written": True}


# A command whose write waits at its COMMIT for a reader of the store, until it is ended. Its first refused COMMIT
# creates the file sys.argv[2].
WAITING_WRITER_SCRIPT = """
import sqlite3
import sys
from pathlib import Path
from research_harness import finishing
from research_harness.storage import Store


class MarkedConnection(sqlite3.Connection):
    def commit(self):
        try:
            return super().commit()
        except sqlite3.OperationalError:
            Path(sys.argv[2]).touch()
            raise


connect = sqlite3.connect
sqlite3.connect = lambda *args, **kwargs: connect(*args, factory=MarkedConnection, **kwargs)
store = Store(Path(sys.argv[1]))
with finishing.wait_for_store_in_command():
    store.mutate("fixture.ended", {}, lambda tx: tx.put("fixture_row", "ended", {"n": 2}), expected_revision=1,
                 request_id="ended")
"""


# One request through each commit entry of the store and through the two prepared forms: name -> call(store, revision).
REQUESTS = {
    "mutate": lambda store, revision: store.mutate(
        "fixture.write", {"n": 1}, write_row, expected_revision=revision, request_id="request"),
    "mutate_serialized": lambda store, revision: store.mutate_serialized(
        "fixture.write", {"n": 1}, write_row, request_id="request"),
    "prepared_mutation": lambda store, revision: prepared_mutation(
        store, "fixture.write", {"n": 1}, prepare_row, expected_revision=revision, request_id="request"),
    "prepared_task_mutation": lambda store, revision: prepared_task_mutation(
        store, "fixture.write", {"n": 1}, prepare_row, request_id="request"),
}


class LockedStoreCase(OtherWriters, unittest.TestCase):
    """A new store beside a reader, a writer, or a COMMIT that waits for a reader. Each SQLite call of the store waits
    0.05 seconds for a lock, in place of the busy timeout of one second."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root, create=True)
        self.start_other_writers()
        timeout = mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.05)
        timeout.start()
        self.addCleanup(timeout.stop)

    def records(self):
        return self.store.snapshot()["records"]

    def open_connection(self):
        """A connection of another command, which a timer thread can end."""
        connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None,
                                     timeout=0.01, check_same_thread=False)
        self.addCleanup(connection.close)
        return connection

    def hold_reader(self):
        """A reader in the middle of a read. Returns the function that ends it."""
        reader = self.open_connection()
        reader.execute("BEGIN")
        reader.execute("SELECT revision FROM metadata").fetchone()
        return lambda: reader.execute("ROLLBACK")

    def hold_waiting_commit(self):
        """A writer whose COMMIT waits for a reader: it holds the PENDING lock, which admits no new reader. Returns
        the function that ends the reader and then the writer."""
        end_reader = self.hold_reader()
        writer = self.open_connection()
        writer.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.OperationalError):
            writer.commit()

        def end():
            end_reader()
            writer.execute("ROLLBACK")
        return end

    @contextlib.contextmanager
    def ended_after(self, end, seconds):
        """Within this block, another thread calls end after seconds; the block leaves when that call has returned."""
        timer = threading.Timer(seconds, end)
        timer.start()
        try:
            yield
        finally:
            timer.join()


class ReplayTests(LockedStoreCase):
    """The replay of a committed request returns its original response and takes no write lock."""

    def commit_request(self, name):
        revision = self.store.revision
        return REQUESTS[name], revision, REQUESTS[name](self.store, revision)

    def test_replay_returns_the_original_response_beside_a_reader(self):
        for name in REQUESTS:
            with self.subTest(entry=name):
                self.setUp()
                request, revision, first = self.commit_request(name)
                self.hold_read_lock()
                self.assertEqual(request(Store(self.root), revision), first)

    def test_replay_returns_the_original_response_beside_a_writer(self):
        for name in REQUESTS:
            with self.subTest(entry=name):
                self.setUp()
                request, revision, first = self.commit_request(name)
                self.hold_write_lock()
                self.assertEqual(request(Store(self.root), revision), first)

    def test_replay_found_inside_the_write_transaction_frees_the_write_lock_beside_a_reader(self):
        # Another process commits the request between the read before the write and the write lock.
        for name in REQUESTS:
            with self.subTest(entry=name):
                self.setUp()
                request, revision, first = self.commit_request(name)
                self.hold_read_lock()
                with mock.patch.object(Store, "_read_committed_response", return_value=None, create=True):
                    self.assertEqual(request(Store(self.root), revision), first)
                # The write lock is free again: another writer takes it while the reader still reads.
                self.hold_write_lock().execute("ROLLBACK")

    def test_conflict_found_inside_the_write_transaction_frees_the_write_lock_beside_a_reader(self):
        self.commit_request("mutate")
        self.hold_read_lock()
        with mock.patch.object(Store, "_read_committed_response", return_value=None, create=True), \
                self.assertRaises(ResearchError) as caught:
            self.store.mutate("fixture.write", {"n": 2}, write_row, expected_revision=1, request_id="request")
        self.assertEqual(caught.exception.code, "request_id_conflict")
        self.hold_write_lock().execute("ROLLBACK")

    def test_open_of_an_existing_store_for_creation_takes_no_write_lock(self):
        # init, adopt, problem-init, recover and exactory-cohort collect open the store with create.
        for hold in (self.hold_read_lock, self.hold_write_lock):
            with self.subTest(beside=hold.__name__):
                other = hold()
                self.assertEqual(Store(self.root, create=True).read_records([])[0], 0)
                other.execute("ROLLBACK")

    def test_replay_of_an_acquisition_returns_its_result_beside_a_reader_and_a_writer(self):
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        first = acquisition.acquire_work(self.store, "2601.00001v1", request_id="acquire", expected_revision=0,
                                         http=http)
        self.hold_read_lock()
        self.hold_write_lock()
        unused, unused_wire, _ = client([])
        again = acquisition.acquire_work(Store(self.root), "2601.00001v1", request_id="acquire", expected_revision=0,
                                         http=unused)
        self.assertEqual((again, len(wire.requests), len(unused_wire.requests)), (first, 1, 0))

    def test_replay_with_other_inputs_is_a_conflict_beside_a_writer(self):
        self.commit_request("mutate")
        self.hold_write_lock()
        with self.assertRaises(ResearchError) as caught:
            self.store.mutate("fixture.write", {"n": 2}, write_row, expected_revision=0, request_id="request")
        self.assertEqual(caught.exception.code, "request_id_conflict")

    def test_replay_is_answered_from_an_authenticated_history(self):
        self.commit_request("mutate")
        database = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None)
        database.execute("DROP TRIGGER events_no_update")
        database.execute("UPDATE events SET digest = 'changed'")
        database.execute(storage._SCHEMA[("trigger", "events_no_update")])
        database.close()
        self.assertEqual(Store(self.root).read_records([])[0], 1)
        with self.assertRaises(ResearchError) as caught:
            self.store.mutate("fixture.write", {"n": 1}, write_row, expected_revision=0, request_id="request")
        self.assertEqual(caught.exception.code, "corrupt_state")

    def test_new_request_commits_and_a_later_revision_refuses_a_compare_and_swap(self):
        first = self.store.mutate("fixture.write", {"n": 1}, write_row, expected_revision=0, request_id="request")
        self.assertEqual((first["revision"], self.store.revision), (1, 1))
        with self.assertRaises(ResearchError) as caught:
            self.store.mutate("fixture.write", {"n": 2}, write_row, expected_revision=0, request_id="another")
        self.assertEqual(caught.exception.code, "stale_revision")


class StoreWaitTests(LockedStoreCase):
    """Inside a command, a read waits for a COMMIT in progress and a COMMIT waits for the readers of the store."""

    def run_command(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                cli.main([*arguments, "--workspace", str(self.root)], render_views=lambda workspace: None)
                status = 0
            except SystemExit as stop:
                status = stop.code
        return status, output.getvalue(), errors.getvalue()

    def test_reading_commands_wait_for_a_commit_in_progress(self):
        for arguments in (("status", "--summary"), ("next", "--summary"),
                          ("obligations", "--code", "abstract_reading_missing")):
            with self.subTest(command=arguments[0]):
                with self.ended_after(self.hold_waiting_commit(), 0.4):
                    status, output, errors = self.run_command(*arguments)
                self.assertEqual((status, errors), (0, ""))
                self.assertIn("revision", json.loads(output))

    def test_mutation_of_a_command_commits_when_the_reader_has_ended(self):
        (self.root / "authored.txt").write_bytes(b"A local authored artifact.\n")
        payload = self.root.parent / (self.root.name + "-payload.json")
        payload.write_text(json.dumps({"id": "authored", "path": "authored.txt", "media_type": "text/plain"}))
        self.addCleanup(payload.unlink)
        with self.ended_after(self.hold_reader(), 0.4):
            status, output, errors = self.run_command("artifact", "--file", str(payload), "--expected-revision", "0",
                                                      "--request-id", "pin")
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(json.loads(output)["revision"], 1)

    def test_writer_ended_while_its_commit_waits_leaves_the_store_unchanged_and_readable(self):
        self.store.mutate("fixture.write", {"n": 1}, write_row, expected_revision=0, request_id="request")
        before = self.store.snapshot()
        end_reader = self.hold_reader()
        waiting = self.root.parent / (self.root.name + "-commit-waits")
        self.addCleanup(waiting.unlink, missing_ok=True)
        writer = subprocess.Popen([sys.executable, "-B", "-c", WAITING_WRITER_SCRIPT, str(self.root), str(waiting)],
                                  cwd=PLUGIN_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(writer.wait)
        self.addCleanup(writer.kill)
        deadline = time.monotonic() + 120
        while not waiting.exists():
            self.assertIsNone(writer.poll())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        writer.kill()
        writer.wait()
        end_reader()
        journal = self.root / ".exactory" / "research.sqlite3-journal"
        self.assertTrue(journal.exists())
        # The ended write left a journal that SQLite does not roll back: a read-only open reads the committed records.
        self.assertEqual(Store(self.root).snapshot(), before)
        self.store.mutate("fixture.after", {}, lambda tx: tx.put("fixture_row", "after", {"n": 3}), expected_revision=1,
                          request_id="after")
        self.assertFalse(journal.exists())
        self.assertEqual(self.records()["fixture_row"], {"row": {"n": 1}, "after": {"n": 3}})

    def test_read_inside_the_store_wait_is_admitted_when_the_commit_has_ended(self):
        with self.ended_after(self.hold_waiting_commit(), 0.4), storage.wait_for_store_locks(30.0):
            self.assertEqual(self.store.snapshot()["revision"], 0)

    def test_read_is_refused_when_the_commit_outlasts_the_store_wait(self):
        self.hold_waiting_commit()
        with storage.wait_for_store_locks(0.2), self.assertRaises(ResearchError) as caught:
            self.store.snapshot()
        self.assertEqual(caught.exception.code, "store_busy")

    def test_polling_read_inside_the_store_wait_keeps_the_busy_timeout(self):
        self.hold_waiting_commit()
        begun = time.monotonic()
        with storage.wait_for_store_locks(30.0):
            self.assertIsNone(review_worker.read_polling_store(self.store.snapshot))
        self.assertLess(time.monotonic() - begun, 5.0)

    def test_render_inside_a_command_keeps_the_busy_timeout(self):
        self.hold_waiting_commit()
        begun, errors = time.monotonic(), io.StringIO()
        with finishing.wait_for_store_in_command(), contextlib.redirect_stderr(errors):
            self.assertIsNone(output_views.render_workspace_views(self.root))
        self.assertLess(time.monotonic() - begun, 5.0)
        self.assertIn("Output views were not rendered (store_busy", errors.getvalue())

    def test_read_outside_a_command_keeps_the_busy_timeout(self):
        self.hold_waiting_commit()
        with self.assertRaises(ResearchError) as caught:
            self.store.snapshot()
        self.assertEqual(caught.exception.code, "store_busy")

    def test_first_write_with_a_revision_commits_when_the_reader_has_ended(self):
        with self.ended_after(self.hold_reader(), 0.4), storage.wait_for_store_locks(30.0):
            receipt = prepared_mutation(self.store, "fixture.write", {"n": 1}, prepare_row, expected_revision=0,
                                        request_id="request")
        self.assertEqual((receipt["revision"], self.records()["fixture_row"]["row"]), (1, {"n": 1}))

    def test_first_write_outside_a_command_is_refused_beside_a_reader(self):
        self.hold_read_lock()
        with self.assertRaises(ResearchError) as caught:
            prepared_mutation(self.store, "fixture.write", {"n": 1}, prepare_row, expected_revision=0,
                              request_id="request")
        self.assertEqual(caught.exception.code, "store_busy")


class CommandEntryTests(LockedStoreCase):
    """Each command entry sets the store wait for its whole command, so its first read waits for a COMMIT in
    progress."""

    def assert_error_code(self, errors, code):
        self.assertEqual(json.loads(errors.strip().splitlines()[-1])["error"]["code"], code, errors)

    def test_exactory_command_reads_after_a_commit_in_progress(self):
        exactory = load_bin_module("exactory", "replay_first_writes_exactory")
        with self.ended_after(self.hold_waiting_commit(), 0.4):
            status, _, errors = run_command_main(exactory.main, ["exactory", "reconcile", "intent"], self.root)
        # The store holds no research configuration: the command read the store and ends there.
        self.assertEqual(status, 1)
        self.assert_error_code(errors, "migration_required")

    def test_draft_command_reads_after_a_commit_in_progress(self):
        draft = load_bin_module("exactory-draft", "replay_first_writes_draft")
        with self.ended_after(self.hold_waiting_commit(), 0.4):
            status, _, errors = run_command_main(draft.main, ["exactory-draft", "reconcile", "intent"], self.root)
        self.assertEqual(status, 1)
        self.assert_error_code(errors, "migration_required")

    def test_lab_command_reads_after_a_commit_in_progress(self):
        lab = load_bin_module("exactory-lab", "replay_first_writes_lab")
        (self.root / ".exactory" / "study.json").write_text(json.dumps({"stage": "direction"}))
        with self.ended_after(self.hold_waiting_commit(), 0.4):
            status, _, errors = run_command_main(lab.main, ["exactory-lab", "state", "show"], self.root)
        self.assertEqual(status, 1)
        self.assert_error_code(errors, "migration_required")

    def test_cohort_command_reads_after_a_commit_in_progress(self):
        cohort = load_bin_module("exactory-cohort", "replay_first_writes_cohort")
        with self.ended_after(self.hold_waiting_commit(), 0.4):
            status, output, errors = run_command_main(
                cohort.main, ["exactory-cohort", "status", "--workspace", str(self.root)], self.root)
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(json.loads(output), {"revision": 0, "collections": []})

    def test_cohort_collection_opens_an_existing_workspace_beside_a_reader(self):
        cohort = load_bin_module("exactory-cohort", "replay_first_writes_cohort_collect")
        definition = self.root.parent / (self.root.name + "-definition.json")
        definition.write_text(json.dumps(DEFINITION))
        self.addCleanup(definition.unlink)
        with self.ended_after(self.hold_reader(), 0.4):
            status, output, errors = run_command_main(cohort.main, [
                "exactory-cohort", "collect", "--workspace", str(self.root), "--definition", str(definition),
                "--request-id", "collect", "--expected-revision", "0", "--max-requests", "0"], self.root)
        self.assertEqual((status, errors), (0, ""))
        self.assertEqual(json.loads(output)["status"], "paused")

    def test_review_worker_reads_after_a_commit_in_progress(self):
        revisions = []
        with mock.patch.object(review_worker, "run_worker",
                               lambda root, *rest: revisions.append(Store(root).snapshot()["revision"])), \
                self.ended_after(self.hold_waiting_commit(), 0.4):
            review_worker.main([str(self.root), "reservation", "token", "3"])
        self.assertEqual(revisions, [0])


if __name__ == "__main__":
    unittest.main()
