"""A committed request is answered beside a reader and beside a writer, a command waits for a busy store (its reads
wait for a COMMIT in progress and its COMMIT for the readers of the store), and the first write of an acquisition and
of a remote operation commits after another command committed (record
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

from finishing_write_fixtures import ORIGINAL_MUTATE, OtherWriters
from research_fixtures import atom, client, entry, xml_response
from research_harness import (acquisition, cli, finishing, fulltext_jobs, output_views, remote, review_worker, storage,
                              submission, zenodo)
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

    def test_cohort_collection_admits_its_operation_after_a_writer_has_ended(self):
        cohort = load_bin_module("exactory-cohort", "replay_first_writes_cohort_admission")
        definition = self.root.parent / (self.root.name + "-definition.json")
        definition.write_text(json.dumps(DEFINITION))
        self.addCleanup(definition.unlink)
        writer = self.open_connection()
        writer.execute("BEGIN IMMEDIATE")
        with self.ended_after(lambda: writer.execute("ROLLBACK"), 0.4):
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


class FirstWriteTests(LockedStoreCase):
    """The admission of an acquisition and the saved intent of a remote operation commit beside other commands."""

    def acquire(self, http, revision, request_id="acquire"):
        return acquisition.acquire_work(self.store, "2601.00001v1", request_id=request_id, expected_revision=revision,
                                        http=http)

    def hold_then_free_the_write_lock(self, operation):
        """Another command holds the write lock at the first commit of operation and has ended at the second."""
        held = []

        def act():
            if held:
                held.pop().execute("ROLLBACK")
            else:
                held.append(self.hold_write_lock())
        return self.meet_commits((operation,), act=act, limit=2)

    def write_collection(self, collection_id, **members):
        """Another command writes members of the collection without its lock, as an import of an OAI cohort does."""
        other = Store(self.root)
        ORIGINAL_MUTATE(other, "fixture.collection", {"revision": other.revision},
                        lambda transaction: transaction.put("collection", collection_id, dict(
                            transaction.get("collection", collection_id), **members)),
                        expected_revision=other.revision, request_id="collection-" + str(other.revision))

    def acquire_work_record(self):
        http, _, _ = client([xml_response(atom([entry()], total=1))])
        self.acquire(http, self.store.revision, request_id="metadata")
        return "arxiv:2601.00001v1"

    def start_fulltext_job(self, identifier, revision):
        return fulltext_jobs.start_job(self.store, identifier, request_id="start", expected_revision=revision,
                                       url="https://arxiv.org/pdf/2601.00001v1")

    def test_admission_commits_after_another_command_committed(self):
        revision = self.store.revision
        self.commit_unrelated()
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        result = self.acquire(http, revision)
        operation = self.records()["acquisition_operation"]["acquire"]
        self.assertEqual((result["status"], len(wire.requests), operation["admission_revision"]),
                         ("complete", 1, revision + 2))

    def test_admission_names_the_revision_of_its_commit(self):
        http, _, _ = client([xml_response(atom([entry()], total=1))])
        self.meet_commits(("work.acquire",))
        self.acquire(http, 0)
        operation = self.records()["acquisition_operation"]["acquire"]
        admitted = self.store.committed_request("acquire")["response"]
        self.assertEqual((admitted["revision"], admitted["result"]["revision"], operation["admission_revision"]),
                         (2, 2, 2))

    def test_admission_waits_for_a_busy_store(self):
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        self.hold_then_free_the_write_lock("work.acquire")
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            result = self.acquire(http, 0)
        self.assertEqual((result["status"], len(wire.requests), self.attempts["work.acquire"]), ("complete", 1, 2))

    def test_admission_refused_beyond_the_wait_sends_nothing_and_the_same_request_goes_on(self):
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        refusal = self.refuse_beyond_the_wait("work.acquire", lambda: self.acquire(http, 0))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", acquisition.ADMISSION_STORE_BUSY_MSG))
        self.assertEqual(refusal.details["cause"]["code"], "store_busy")
        self.assertEqual((len(wire.requests), self.records().get("acquisition_operation")), (0, None))
        self.assertEqual((self.acquire(http, 0)["status"], len(wire.requests)), ("complete", 1))

    def test_admission_needs_the_store_at_the_callers_revision_or_a_later_one(self):
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        with self.assertRaises(ResearchError) as caught:
            self.acquire(http, 5)
        self.assertEqual((caught.exception.code, caught.exception.details["expected_revision"],
                          caught.exception.details["revision"], len(wire.requests)), ("stale_revision", 5, 0, 0))

    def test_cohort_collection_starts_after_another_command_committed(self):
        revision = self.store.revision
        self.commit_unrelated()
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        result = acquisition.collect_cohort(self.store, DEFINITION, request_id="collect", expected_revision=revision,
                                            http=http)
        self.assertEqual((result["status"], len(wire.requests)), ("complete", 1))

    def test_resume_reserves_requests_by_the_collection_that_its_admission_reads(self):
        http, wire, _ = client([])
        collection_id = acquisition.collect_cohort(self.store, DEFINITION, request_id="collect", expected_revision=0,
                                                   max_requests=0, http=http)["collection_id"]
        partitions = self.records()["collection"][collection_id]["partitions"]
        self.write_collection(collection_id, pending=[acquisition.describe_query_ceiling(partitions[0], 30001)])
        revision = self.store.revision
        # Before the admission of resume, another command completes the enumeration and ends the pause.
        self.meet_commits(("cohort.resume",), act=lambda: self.write_collection(
            collection_id, pending=[], partitions=[dict(partition, status="complete") for partition in partitions]),
            limit=1)
        acquisition.resume_cohort(self.store, collection_id, request_id="resume", expected_revision=revision,
                                  max_requests=3, http=http)
        operation = self.records()["acquisition_operation"]["resume"]
        self.assertEqual((operation["payload"]["max_requests"], operation["admission_revision"], len(wire.requests)),
                         (3, revision + 2, 0))

    def test_same_admission_request_returns_its_recorded_result(self):
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        first = self.acquire(http, 0)
        self.commit_unrelated()
        self.assertEqual((self.acquire(http, 0), len(wire.requests)), (first, 1))

    def test_fulltext_job_starts_after_another_command_committed(self):
        identifier = self.acquire_work_record()
        revision = self.store.revision
        self.commit_unrelated()
        result = self.start_fulltext_job(identifier, revision)
        self.assertEqual((result["status"], result["revision"]), ("pending", revision + 2))
        self.assertIn(result["job_id"], self.records()["fulltext_job"])

    def test_fulltext_job_start_waits_for_a_busy_store(self):
        identifier = self.acquire_work_record()
        self.hold_then_free_the_write_lock("fulltext.start")
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            result = self.start_fulltext_job(identifier, self.store.revision)
        self.assertEqual((result["status"], self.attempts["fulltext.start"]), ("pending", 2))

    def test_fulltext_job_start_refuses_a_revision_later_than_the_store(self):
        identifier = self.acquire_work_record()
        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.start_fulltext_job(identifier, revision + 1)
        self.assertEqual((caught.exception.code, caught.exception.message, caught.exception.details),
                         ("stale_revision", acquisition.ADMISSION_REVISION_AHEAD_MSG,
                          {"expected_revision": revision + 1, "revision": revision}))
        self.assertNotIn("fulltext_job", self.records())

    def test_intent_without_a_revision_is_saved_when_another_command_commits_before_it(self):
        self.meet_commits(("remote.begin",))
        intent = remote.begin_intent(self.store, "fixture", {"id": "item"}, request_id="intent")
        self.assertEqual((intent["id"], intent["status"], intent["created_revision"], self.store.revision),
                         ("intent", "prepared", 1, 2))

    def test_intent_without_a_revision_waits_for_a_busy_store(self):
        self.hold_then_free_the_write_lock("remote.begin")
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            intent = remote.begin_intent(self.store, "fixture", {"id": "item"}, request_id="intent")
        self.assertEqual((intent["id"], self.attempts["remote.begin"]), ("intent", 2))

    def test_intent_refused_beyond_the_wait_says_that_nothing_was_sent(self):
        refusal = self.refuse_beyond_the_wait("remote.begin", lambda: remote.begin_intent(
            self.store, "fixture", {"id": "item"}, request_id="intent"))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_BEGIN_STORE_BUSY_MSG))
        self.assertEqual(self.records().get("remote_intent"), None)

    def test_same_remote_request_of_two_commands_is_one_intent(self):
        def save_the_same_request():
            other = Store(self.root)
            prepared_mutation(other, "fixture.other_command", {}, lambda records, value: (
                [("remote_intent", "other", {
                    "id": "other", "kind": "fixture", "binding": {"id": "item"}, "responses": {}, "pending": None,
                    "fingerprint": remote._compute_request_fingerprint("fixture", {"id": "item"}, None),
                    "status": "prepared", "created_revision": 0})], {}),
                              expected_revision=other.revision, request_id="other-command")
        self.meet_commits(("remote.begin",), act=save_the_same_request, limit=1)
        intent = remote.begin_intent(self.store, "fixture", {"id": "item"}, request_id="intent")
        self.assertEqual((intent["id"], sorted(self.records()["remote_intent"])), ("other", ["other"]))

    def test_intent_without_a_revision_is_saved_when_another_command_changes_another_intent(self):
        remote.begin_intent(self.store, "fixture", {"id": "other"}, expected_revision=self.store.revision,
                            request_id="other")

        def change_the_other_intent():
            other = Store(self.root)
            ORIGINAL_MUTATE(other, "fixture.other_step", {}, lambda transaction: transaction.put(
                "remote_intent", "other", dict(transaction.get("remote_intent", "other"), status="confirmed")),
                expected_revision=other.revision, request_id="other-step")
        self.meet_commits(("remote.begin",), act=change_the_other_intent, limit=1)
        intent = remote.begin_intent(self.store, "fixture", {"id": "item"}, request_id="intent")
        # The intent reads only the saved intents of its own request, so the first attempt commits.
        self.assertEqual((intent["id"], self.attempts["remote.begin"]), ("intent", 1))

    def test_intent_with_the_revision_of_the_user_keeps_the_compare_and_swap(self):
        self.commit_unrelated()
        with self.assertRaises(ResearchError) as caught:
            remote.begin_intent(self.store, "fixture", {"id": "item"}, expected_revision=0, request_id="intent")
        self.assertEqual(caught.exception.code, "stale_revision")

    def test_deposit_and_submission_without_a_revision_save_their_intents_beside_another_command(self):
        # The checks of the manuscript, the reviews and the publication are not what this test is about.
        self.meet_commits(("remote.begin",))
        with mock.patch.object(zenodo, "_current", side_effect=lambda store, binding: store.revision):
            deposit = zenodo.begin_deposit(self.store, {"new_version": False, "fixture": "deposit"},
                                           request_id="deposit")
        binding = {"fixture": "submission"}
        with mock.patch.object(submission, "check_submission_preconditions", return_value=(binding, None)), \
                mock.patch.object(submission, "continue_submission",
                                  side_effect=lambda store, identifier, client: remote.get_intent(store, identifier)):
            submitted = submission.submit_managed(self.store, {"doi": "10.5072/zenodo.1"}, None, request_id="submit")
        self.assertEqual((deposit["status"], submitted["status"], self.attempts["remote.begin"]),
                         ("prepared", "prepared", 2))
        self.assertEqual(sorted(self.records()["remote_intent"]), ["deposit", "submit"])


class ReservationTests(unittest.TestCase):
    """The admission of a supplied full text reserves no request, and the resume of a full-text job reserves its bounded
    invocation, as before the reservation was read inside the admission."""

    def start_case(self, case_class):
        case = case_class()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_import_of_a_supplied_full_text_reserves_no_request(self):
        from test_import_fulltext import ImportFulltextTests
        case = self.start_case(ImportFulltextTests)
        with mock.patch.object(acquisition.resources, "admit", wraps=acquisition.resources.admit) as admit:
            case.import_text()
        operation = case.store.snapshot()["records"]["acquisition_operation"]["supplied"]
        self.assertEqual((operation["payload"]["max_requests"], admit.call_count), (0, 0))

    def test_resume_of_a_full_text_job_reserves_its_bounded_invocation(self):
        from test_research_fulltext_jobs import JobCase
        case = self.start_case(JobCase)
        job = case.start()
        http, _ = case.wire()
        request_id = "resume-" + str(case.store.revision)
        with mock.patch.object(acquisition.resources, "admit", wraps=acquisition.resources.admit) as admit:
            case.resume(job, http=http, max_requests=1)
        # Each charged request lowers the reservation later, so the admission's own event holds it.
        database = sqlite3.connect(str(case.root / ".exactory" / "research.sqlite3"))
        self.addCleanup(database.close)
        changes = json.loads(database.execute("SELECT changes FROM events WHERE request_id = ?", (request_id,))
                             .fetchone()[0])
        admitted = next(change["value"] for change in changes if change["kind"] == "acquisition_operation")
        self.assertEqual((admitted["payload"]["max_requests"], admit.call_args[0][1:]), (1, ("literature", 1)))


class ManagedCommandCase(OtherWriters, unittest.TestCase):
    """A workspace whose managed remote write is ready, and exactory run through its main beside a stand-in service.
    Without --expected-revision the command saves its intent while another command commits; with it the intent is
    the compare-and-swap on that revision, as exactory-draft deposit keeps it."""

    def start_case(self, case_class):
        case = case_class()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.root = case.store.root
        self.start_other_writers()
        return case

    def run_exactory(self, arguments):
        """Run exactory with arguments. Returns the exit status, stderr and the methods of the requests it sent."""
        exactory = load_bin_module("exactory", "replay_first_writes_" + self.id().rsplit(".", 1)[-1])
        sent = []

        def send_request(method, path, body=None, **options):
            sent.append(method)
            return self.answer(method)
        with mock.patch.object(exactory, "_send_request", send_request), \
                mock.patch.object(exactory, "report_citation_gate", lambda *arguments: None):
            status, _, errors = run_command_main(exactory.main, ["exactory", *arguments], self.root)
        return status, errors, sent

    def read_intents(self, kind):
        intents = Store(self.root).snapshot()["records"].get("remote_intent", {}).values()
        return [(intent["status"], intent["created_revision"]) for intent in intents if intent["kind"] == kind]

    def assert_stale_revision(self, errors):
        self.assertEqual(json.loads(errors.strip().splitlines()[-1])["error"]["code"], "stale_revision", errors)


class VerdictCommandTests(ManagedCommandCase):
    """exactory verify on a bound verdict."""

    def setUp(self):
        from test_research_verification import ResearchVerificationTests
        self.case = self.start_case(ResearchVerificationTests)
        self.case.bind(self.case.verifier())
        self.verdict = self.root / "verdict.json"
        self.verdict.write_text(json.dumps(self.case.verdict()))

    def answer(self, method):
        if method == "GET":
            return dict(self.case.task), 200
        return {"id": "22222222-2222-4222-8222-222222222222"}, 201

    def verify(self, *options):
        return self.run_exactory(["verify", self.case.task["verificationId"], "--file", str(self.verdict), *options])

    def test_verify_saves_its_intent_when_another_command_commits_before_it(self):
        self.meet_commits(("remote.begin",), limit=1)
        status, errors, sent = self.verify()
        self.assertEqual((status, errors, sent), (0, "", ["GET", "POST"]))
        self.assertEqual([status for status, _ in self.read_intents("verdict")], ["complete"])

    def test_verify_with_a_stale_revision_is_refused_before_its_request(self):
        status, errors, sent = self.verify("--expected-revision", "0")
        self.assertEqual((status, sent), (1, ["GET"]))
        self.assert_stale_revision(errors)
        self.assertEqual(self.read_intents("verdict"), [])

    def test_verify_with_the_current_revision_sends_its_verdict(self):
        revision = self.case.store.revision
        status, errors, sent = self.verify("--expected-revision", str(revision))
        self.assertEqual((status, errors, sent), (0, "", ["GET", "POST"]))
        self.assertEqual(self.read_intents("verdict"), [("complete", revision)])


class SubmitCommandTests(ManagedCommandCase):
    """exactory submit on a published deposit."""

    TASK = {"verificationId": "11111111-1111-4111-8111-111111111111", "doi": "10.5281/zenodo.90", "source": "zenodo",
            "sourceId": "91", "sourceVersion": None, "url": "https://zenodo.org/records/91"}

    def setUp(self):
        from test_research_publication import ResearchPublicationTests
        self.case = self.start_case(ResearchPublicationTests)
        self.publication = self.case.publish_remote(self.case.remote_binding())[0]

    def answer(self, method):
        if method == "POST":
            # The answer of the service: verificationSummarySchema, which names the request by its id.
            return {"id": self.TASK["verificationId"], "status": "open", "doi": self.TASK["doi"],
                    "createdAt": "2026-11-20T00:00:00.000Z", "webUrl": "https://www.exactory.ai/verifications/x",
                    "supersedes": None, "supersededBy": None}, 201
        return dict(self.TASK), 200

    def submit(self, *options):
        return self.run_exactory(["submit", "--doi", self.publication["doi"], *options])

    def test_submit_saves_its_intent_when_another_command_commits_before_it(self):
        self.meet_commits(("remote.begin",), limit=1)
        status, errors, sent = self.submit()
        self.assertEqual((status, errors, sent), (0, "", ["POST", "GET"]))
        self.assertEqual([status for status, _ in self.read_intents("submit")], ["complete"])

    def test_submit_with_a_stale_revision_is_refused_before_its_request(self):
        status, errors, sent = self.submit("--expected-revision", "0")
        self.assertEqual((status, sent), (1, []))
        self.assert_stale_revision(errors)
        self.assertEqual(self.read_intents("submit"), [])

    def test_submit_with_the_current_revision_sends_its_request(self):
        revision = self.case.store.revision
        status, errors, sent = self.submit("--expected-revision", str(revision))
        self.assertEqual((status, errors, sent), (0, "", ["POST", "GET"]))
        self.assertEqual(self.read_intents("submit"), [("complete", revision)])

if __name__ == "__main__":
    unittest.main()
