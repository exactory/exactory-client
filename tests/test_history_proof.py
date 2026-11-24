"""A use of the store replays the history before its lock and proves the rows under the lock (record
20261120143200-every-transaction-replays-the-whole-history).

WorkThatLeavesTheWriteLockTests, ReplayWorkTests and NoLockWhileDecodingTests check the work that leaves a lock and
its results. WhatStaysRefusedTests checks the decisions after the history changes. PreliminaryReplayTests checks that
the replay before the transaction decides no refusal, recovers no less and ends at the revision of its first read.
ChangesComparedInPlaceTests checks the comparison of the changes text in place.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

from research_harness import operations, storage
from research_harness.errors import ResearchError
from research_harness.storage import Store


class _Decodes:
    """JSON decodes and event replays observed during one use."""

    def __init__(self):
        self.under_write_lock, self.outside = [], []
        self.replays = []


class HistoryProofCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.database = self.workspace / ".exactory" / "research.sqlite3"
        self.store = Store(self.workspace, create=True)
        self.add(self.store, "paper", "Original", 0, "add-1")
        self.add(self.store, "second", "Second", 1, "add-2")

    def add(self, store, key, title, revision, request):
        def apply(tx):
            tx.put("work", key, {"title": title})
            tx.put("note", key, {"text": "about " + title})
            return {"id": key}
        return store.mutate("add", {"key": key, "title": title}, apply, expected_revision=revision, request_id=request)

    def assert_error(self, code, action):
        with self.assertRaises(ResearchError) as caught:
            action()
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception

    @contextmanager
    def database_connection(self):
        with closing(sqlite3.connect(self.database)) as connection:
            with connection:
                yield connection

    def read_stored_texts(self):
        """(the payload, changes and result texts of every event by revision, the value text of every record)."""
        with closing(sqlite3.connect(self.database)) as connection:
            events = {revision: (payload, changes, result) for revision, payload, changes, result in
                      connection.execute("SELECT revision, payload, changes, result FROM events")}
            records = [value for (value,) in connection.execute("SELECT value FROM records")]
        return events, records

    @contextmanager
    def watch_decodes(self):
        """Record each stored text that storage decodes, and whether its write lock was held at that time."""
        decodes, held, opened_transactions = _Decodes(), [], []
        original_loads, original_connection = storage.json.loads, Store._connection
        original_add_event = storage._Replay.add_event

        def add_event(replay, row, read_receipt):
            phase = opened_transactions[-1] if opened_transactions else "outside"
            decodes.replays.append((phase, row["revision"]))
            return original_add_event(replay, row, read_receipt)

        def loads(text, *args, **kwargs):
            (decodes.under_write_lock if held else decodes.outside).append(text)
            return original_loads(text, *args, **kwargs)

        @contextmanager
        def connection(store, *, writable=False, **options):
            # Store._connection takes the write lock for a connection that can write, unless write_lock is False.
            write_lock = writable and options.get("write_lock", True)
            with original_connection(store, writable=writable, **options) as opened:
                opened_transactions.append("write" if write_lock else "read")
                if write_lock:
                    held.append(True)
                try:
                    yield opened
                finally:
                    opened_transactions.pop()
                    if write_lock:
                        held.pop()

        with mock.patch.object(storage.json, "loads", loads), mock.patch.object(Store, "_connection", connection), \
                mock.patch.object(storage._Replay, "add_event", add_event):
            yield decodes

    def write_with_a_preparation(self, store, request="prepared-1", revision=2, callback=None):
        def prepare(records, payload):
            if callback is not None:
                callback()
            return [("work", "third", {"title": "Third"})], {"recorded": True}
        return operations.prepared_mutation(store, "test.prepared", {"n": 1}, prepare,
                                            expected_revision=revision, request_id=request)

    def write_scoped(self, store, request="scoped-1", callback=None):
        def prepare(records, payload):
            if callback is not None:
                callback()
            records.get("work", {}).get("paper")
            return [("work", "fourth", {"title": "Fourth"})], {"recorded": True}
        return operations.prepared_task_mutation(store, "test.scoped", {"n": 1}, prepare, request_id=request)


class WorkThatLeavesTheWriteLockTests(HistoryProofCase):
    def assert_no_stored_text_decoded_under_the_write_lock(self, decodes, events, records):
        stored = {text for texts in events.values() for text in texts} | set(records)
        self.assertEqual([text for text in decodes.under_write_lock if text in stored], [])

    def test_a_write_with_a_preparation_decodes_no_stored_row_under_its_write_lock(self):
        events, records = self.read_stored_texts()
        with self.watch_decodes() as decodes:
            self.assertEqual(self.write_with_a_preparation(self.store)["revision"], 3)
        self.assert_no_stored_text_decoded_under_the_write_lock(decodes, events, records)
        # The preparation read replayed the history: it decoded the changes of every event.
        self.assertTrue({texts[1] for texts in events.values()} <= set(decodes.outside))

    def test_a_scoped_write_decodes_no_stored_row_under_its_write_lock(self):
        events, records = self.read_stored_texts()
        with self.watch_decodes() as decodes:
            self.assertEqual(self.write_scoped(self.store)["revision"], 3)
        self.assert_no_stored_text_decoded_under_the_write_lock(decodes, events, records)
        self.assertTrue({texts[1] for texts in events.values()} <= set(decodes.outside))

    def test_a_write_without_a_preparation_replays_the_history_before_its_write_lock(self):
        events, records = self.read_stored_texts()
        with self.watch_decodes() as decodes:
            self.add(self.store, "direct", "Direct", 2, "add-3")
        self.assert_no_stored_text_decoded_under_the_write_lock(decodes, events, records)
        self.assertTrue({texts[1] for texts in events.values()} <= set(decodes.outside))

    def test_a_write_at_the_revision_of_its_lock_replays_the_history_before_its_write_lock(self):
        events, records = self.read_stored_texts()
        with self.watch_decodes() as decodes:
            self.store.mutate_serialized("test.serialized", {"n": 1},
                                         lambda tx, revision: tx.put("work", "fifth", {"title": "Fifth"}),
                                         request_id="serialized-1")
        self.assert_no_stored_text_decoded_under_the_write_lock(decodes, events, records)
        self.assertTrue({texts[1] for texts in events.values()} <= set(decodes.outside))

    def test_a_scoped_write_after_the_commit_of_another_writer_replays_only_the_added_event(self):
        other = Store(self.workspace)
        before, _ = self.read_stored_texts()
        with self.watch_decodes() as decodes:
            response = self.write_scoped(self.store, callback=lambda: self.add(other, "other", "Other", 2, "add-other"))
        self.assertEqual(response["revision"], 4)
        after, _ = self.read_stored_texts()
        added = set(after[3])
        earlier = {text for texts in before.values() for text in texts} - added
        self.assertEqual([text for text in decodes.under_write_lock if text in earlier], [])
        # The event of the other writer is replayed under the write lock of the scoped write.
        self.assertTrue(after[3][1] in decodes.under_write_lock)
        self.assertEqual(Store(self.workspace).snapshot()["records"]["work"]["fourth"], {"title": "Fourth"})


class ReplayWorkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name), create=True)
        self.value = {"title": "Original"}
        self.store.mutate("add", {}, lambda tx: tx.put("work", "paper", self.value),
                          expected_revision=0, request_id="add-1")

    def test_a_replay_encodes_the_changes_of_an_event_once(self):
        encoded = []
        original = storage._encode_json

        def encode(value, code):
            encoded.append(value)
            return original(value, code)

        with mock.patch.object(storage, "_encode_json", encode):
            self.assertEqual(self.store.revision, 1)
        # The replay encodes the change value once for its record.
        self.assertEqual([value for value in encoded if isinstance(value, list)], [])
        self.assertEqual(encoded.count(self.value), 1)

    def test_a_replay_without_a_snapshot_decodes_no_record_row(self):
        value = storage._encode_json(self.value, "corrupt_state")
        with mock.patch.object(storage.json, "loads", wraps=storage.json.loads) as loads:
            self.assertEqual(self.store.revision, 1)
        self.assertNotIn(mock.call(value), loads.call_args_list)

    def test_the_replay_before_a_snapshot_read_keeps_no_current_value(self):
        """Each snapshot read decodes the current values in its own transaction: the replay before it keeps none."""
        original_verify, original_init = storage._verify_without_lock, storage._Replay.__init__
        preliminary, retained = [], []

        def verify(store, **options):
            preliminary.append(True)
            try:
                return original_verify(store, **options)
            finally:
                preliminary.pop()

        def init(replay, *args, **options):
            original_init(replay, *args, **options)
            if preliminary:
                retained.append(replay.values is not None)

        def read_guarded():
            with self.store.guarded_snapshot() as guard:
                return guard.snapshot()["records"]

        reads = (("snapshot", lambda: self.store.snapshot()["records"]), ("guarded snapshot", read_guarded),
                 ("preparation", lambda: self.store._read_preparation().records),
                 ("scoped preparation", lambda: self.store._read_scoped_preparation().records))
        for name, read in reads:
            with self.subTest(read=name), mock.patch.object(storage, "_verify_without_lock", verify), \
                    mock.patch.object(storage._Replay, "__init__", init):
                retained.clear()
                self.assertEqual(read()["work"]["paper"], self.value)
                self.assertEqual(retained, [False])


class NoLockWhileDecodingTests(HistoryProofCase):
    def assert_replayed_without_a_lock(self, decodes, revisions):
        self.assertEqual(decodes.replays, [("outside", revision) for revision in revisions])

    def test_a_read_decodes_the_history_with_no_transaction_open(self):
        uses = (("revision", lambda: self.store.revision), ("snapshot", self.store.snapshot),
                ("receipt lookup", lambda: self.store.committed_request("add-1")))
        for name, use in uses:
            with self.subTest(use=name), self.watch_decodes() as decodes:
                result = use()
                self.assert_replayed_without_a_lock(decodes, (1, 2))
                if name == "receipt lookup":
                    self.assertEqual(result["payload"]["key"], "paper")
                    self.assertEqual(result["response"]["revision"], 1)

    def test_a_second_replay_inside_the_transaction_fails_the_phase_assertion(self):
        original = storage._validate

        def validate_twice(connection, **options):
            original(connection)
            return original(connection, **options)

        with mock.patch.object(storage, "_validate", validate_twice), self.watch_decodes() as decodes:
            self.assertEqual(self.store.revision, 2)
        self.assertEqual(decodes.replays, [("outside", 1), ("outside", 2), ("read", 1), ("read", 2)])
        with self.assertRaises(AssertionError):
            self.assert_replayed_without_a_lock(decodes, (1, 2))

    def test_a_snapshot_after_the_replay_holds_the_committed_records(self):
        """A compatibility control: release 0.92.0 passes it too."""
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["revision"], 2)
        self.assertEqual(snapshot["records"], {
            "note": {"paper": {"text": "about Original"}, "second": {"text": "about Second"}},
            "work": {"paper": {"title": "Original"}, "second": {"title": "Second"}}})
        self.assertEqual(list(snapshot["records"]), ["note", "work"])
        snapshot["records"]["work"]["paper"]["title"] = "Changed by the caller"
        self.assertEqual(self.store.snapshot()["records"]["work"]["paper"], {"title": "Original"})

    def test_a_write_decodes_the_history_with_no_transaction_open(self):
        writes = (("write with a preparation", lambda: self.write_with_a_preparation(self.store)),
                  ("scoped write", lambda: self.write_scoped(self.store)),
                  ("write without a preparation", lambda: self.add(self.store, "direct", "Direct", 4, "add-3")),
                  ("write at the revision of its lock", lambda: self.store.mutate_serialized(
                      "test.serialized", {"n": 1}, lambda tx, revision: tx.put("work", "fifth", {"title": "Fifth"}),
                      request_id="serialized-1")))
        for revision, (name, write) in enumerate(writes, 2):
            with self.subTest(write=name), self.watch_decodes() as decodes:
                write()
                self.assert_replayed_without_a_lock(decodes, range(1, revision + 1))

    def test_a_committed_request_is_answered_after_a_replay_with_no_transaction_open(self):
        writes = (("write with a preparation", lambda: self.write_with_a_preparation(self.store)),
                  ("scoped write", lambda: self.write_scoped(self.store)),
                  ("write without a preparation", lambda: self.add(self.store, "direct", "Direct", 4, "add-3")),
                  ("write at the revision of its lock", lambda: self.store.mutate_serialized(
                      "test.serialized", {"n": 1}, lambda tx, revision: tx.put("work", "fifth", {"title": "Fifth"}),
                      request_id="serialized-1")))
        for name, write in writes:
            with self.subTest(write=name):
                first = write()
                with self.watch_decodes() as decodes:
                    self.assertEqual(write(), first)
                self.assert_replayed_without_a_lock(decodes, range(1, first["revision"] + 1))
                self.assertEqual(decodes.under_write_lock, [])

    def test_a_replay_before_the_lock_takes_the_events_in_short_transactions(self):
        expected = self.store.snapshot()
        reads, original = [], Store._connection

        @contextmanager
        def connection(store, *, writable=False, **options):
            reads.append(writable)
            with original(store, writable=writable, **options) as opened:
                yield opened

        with mock.patch.object(storage, "_UNLOCKED_BATCH_CHARACTERS", 1), \
                mock.patch.object(Store, "_connection", connection):
            snapshot = self.store.snapshot()
        # One read transaction for each of the two events, then the authoritative read.
        self.assertEqual(reads, [False, False, False])
        self.assertEqual(snapshot, expected)

    def test_a_read_replays_under_its_lock_the_event_that_another_writer_added_meanwhile(self):
        other, original, added = Store(self.workspace), storage._verify_without_lock, []

        def verify_then_add(store, **options):
            verified = original(store, **options)
            if store is self.store and not added:
                added.append(True)
                self.add(other, "other", "Other", 2, "add-other")
            return verified

        with mock.patch.object(storage, "_verify_without_lock", verify_then_add):
            snapshot = self.store.snapshot()
        self.assertEqual(snapshot["revision"], 3)
        self.assertEqual(snapshot["records"]["work"]["other"], {"title": "Other"})


class WhatStaysRefusedTests(HistoryProofCase):
    CORRUPTIONS = (
        ("event digest", "UPDATE events SET digest = 'bad' WHERE revision = 1"),
        ("event noncanonical JSON", "UPDATE events SET payload = '{ }' WHERE revision = 1"),
        ("event bytes of the same length", "UPDATE events SET payload = replace(payload, 'Original', 'Forgered') "
                                           "WHERE revision = 1"),
        ("receipt digest", "UPDATE receipts SET digest = 'bad' WHERE revision = 1"),
        ("receipt bytes", "UPDATE receipts SET response = '{}' WHERE revision = 1"),
        ("record digest", "UPDATE records SET digest = 'bad' WHERE key = 'paper'"),
        ("record bytes", "UPDATE records SET value = '{\"title\":\"Forged\"}' WHERE kind = 'work' AND key = 'paper'"),
        ("record noncanonical JSON", "UPDATE records SET value = '{\"title\": \"Original\"}' "
                                     "WHERE kind = 'work' AND key = 'paper'"),
        ("record wrong JSON type", "UPDATE records SET value = '[]' WHERE kind = 'work' AND key = 'paper'"),
        ("missing record", "DELETE FROM records WHERE kind = 'note' AND key = 'paper'"),
        ("added record", "INSERT INTO records (kind, key, value, digest) VALUES ('work', 'forged', '{}', 'bad')"),
        ("schema guard", "DROP TRIGGER events_no_update"),
        ("event at revision 0", "INSERT INTO events (revision, request_id, operation, payload, changes, result, digest) "
                                "VALUES (0, 'forged-0', 'forged', '{}', '[]', '{}', 'x')"),
        ("event at a negative revision", "INSERT INTO events (revision, request_id, operation, payload, changes, result, "
                                         "digest) VALUES (-3, 'forged-n', 'forged', '{}', '[]', '{}', 'x')"),
    )

    def corrupt(self, name, statement):
        with self.database_connection() as connection:
            guards = connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
            for guard, _ in guards:
                connection.execute("DROP TRIGGER " + guard)
            if name != "schema guard":
                connection.execute(statement)
            for _, sql in guards:
                connection.execute(sql)
            if name == "schema guard":
                connection.execute(statement)

    def rebuild(self):
        self.database.unlink()
        self.store = Store(self.workspace, create=True)
        self.add(self.store, "paper", "Original", 0, "add-1")
        self.add(self.store, "second", "Second", 1, "add-2")

    def writes(self):
        """The two forms of a prepared write; each takes the action that runs between its read and its lock."""
        return (("write with a preparation", lambda action: self.write_with_a_preparation(self.store, callback=action)),
                ("scoped write", lambda action: self.write_scoped(self.store, callback=action)))

    def read_revision_without_a_check(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT revision FROM metadata").fetchone()[0]

    def test_tampering_between_the_read_and_the_write_lock_is_refused(self):
        for form, write in self.writes():
            for name, statement in self.CORRUPTIONS:
                with self.subTest(write=form, corruption=name):
                    self.rebuild()
                    self.assert_error("corrupt_state", lambda: write(lambda: self.corrupt(name, statement)))
                    self.assertEqual(self.read_revision_without_a_check(), 2)

    def test_tampering_before_a_write_without_a_preparation_is_refused(self):
        for name, statement in self.CORRUPTIONS:
            with self.subTest(corruption=name):
                self.rebuild()
                self.corrupt(name, statement)
                self.assert_error("corrupt_state", lambda: self.add(self.store, "direct", "Direct", 2, "add-3"))
                self.assertEqual(self.read_revision_without_a_check(), 2)

    def write_bytes_outside_sqlite(self, old, new):
        content = self.database.read_bytes()
        self.assertEqual(len(old), len(new))
        self.assertGreaterEqual(content.count(old), 1)
        with open(self.database, "r+b") as raw:
            raw.seek(content.index(old))
            raw.write(new)

    def test_a_byte_written_outside_sqlite_between_the_read_and_the_write_lock_is_refused(self):
        cases = (("record row", b'workpaper{"title":"Original"}', b'workpaper{"title":"Forgered"}'),
                 ("event row", b'{"key":"paper","title":"Original"}', b'{"key":"paper","title":"Forgered"}'))
        for form, write in self.writes():
            for name, old, new in cases:
                with self.subTest(write=form, row=name):
                    self.rebuild()
                    self.assert_error("corrupt_state", lambda: write(lambda: self.write_bytes_outside_sqlite(old, new)))
                    self.assertEqual(self.read_revision_without_a_check(), 2)

    def truncate(self, *, whole):
        """Remove the last event; whole also removes its receipt and its records and steps the revision back."""
        with self.database_connection() as connection:
            guards = connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
            for guard, _ in guards:
                connection.execute("DROP TRIGGER " + guard)
            if whole:
                connection.execute("DELETE FROM receipts WHERE revision = 2")
            connection.execute("DELETE FROM events WHERE revision = 2")
            if whole:
                connection.execute("DELETE FROM records WHERE key = 'second'")
                connection.execute("UPDATE metadata SET revision = 1")
            for _, sql in guards:
                connection.execute(sql)

    def test_a_history_truncated_between_the_read_and_the_write_lock_is_refused(self):
        for form, write in self.writes():
            with self.subTest(write=form, truncation="the last event with its receipt and records"):
                self.rebuild()
                self.assert_error("stale_revision", lambda: write(lambda: self.truncate(whole=True)))
                self.assertEqual(self.read_revision_without_a_check(), 1)
            with self.subTest(write=form, truncation="the last event alone"):
                self.rebuild()
                self.assert_error("corrupt_state", lambda: write(lambda: self.truncate(whole=False)))
                self.assertEqual(self.read_revision_without_a_check(), 2)

    def build_other_store_bytes(self):
        other_root = Path(self.temporary.name) / "other"
        other = Store(other_root, create=True)
        self.add(other, "paper", "Another", 0, "add-1")
        self.add(other, "second", "Second", 1, "add-2")
        return (other_root / ".exactory" / "research.sqlite3")

    def test_a_store_replaced_between_the_read_and_the_write_lock_is_refused(self):
        for form, write in self.writes():
            with self.subTest(write=form, replacement="another file"):
                self.rebuild()
                other = self.build_other_store_bytes()
                self.assert_error("stale_revision", lambda: write(lambda: os.replace(other, self.database)))
                self.assertEqual(Store(self.workspace).snapshot()["records"]["work"]["paper"], {"title": "Another"})
                self.assertNotIn("third", Store(self.workspace).snapshot()["records"]["work"])
            with self.subTest(write=form, replacement="the bytes of another store in the same file"):
                self.rebuild()
                content = self.build_other_store_bytes().read_bytes()
                self.assert_error("stale_revision", lambda: write(lambda: self.database.write_bytes(content)))
                self.assertEqual(self.read_revision_without_a_check(), 2)

    def test_a_complete_replay_accepts_the_history_after_each_form_of_write(self):
        self.write_with_a_preparation(self.store)
        self.write_scoped(self.store)
        self.add(self.store, "direct", "Direct", 4, "add-3")
        other = Store(self.workspace)
        self.write_scoped(self.store, request="scoped-2",
                          callback=lambda: self.add(other, "other", "Other", 5, "add-other"))
        snapshot = Store(self.workspace).snapshot()
        self.assertEqual(snapshot["revision"], 7)
        self.assertEqual(sorted(snapshot["records"]["work"]),
                         ["direct", "fourth", "other", "paper", "second", "third"])
        self.assertEqual(Store(self.workspace).committed_request("scoped-2")["response"]["revision"], 7)

    def test_a_committed_request_is_answered_with_its_recorded_response(self):
        first = self.write_with_a_preparation(self.store)
        again = self.write_with_a_preparation(self.store, revision=2)
        self.assertEqual(first, again)
        self.assert_error("request_id_conflict", lambda: operations.prepared_mutation(
            self.store, "test.prepared", {"n": 2}, lambda records, payload: ([], {}),
            expected_revision=3, request_id="prepared-1"))

    def test_tampering_between_the_replay_and_the_transaction_of_a_read_is_refused(self):
        original = storage._verify_without_lock
        uses = (("revision", lambda: self.store.revision), ("snapshot", lambda: self.store.snapshot()),
                ("receipt lookup", lambda: self.store.committed_request("add-1")))
        for use_name, use in uses:
            for name, statement in self.CORRUPTIONS:
                with self.subTest(use=use_name, corruption=name):
                    self.rebuild()

                    def verify_then_corrupt(store, **options):
                        verified = original(store, **options)
                        self.corrupt(name, statement)
                        return verified

                    with mock.patch.object(storage, "_verify_without_lock", verify_then_corrupt):
                        self.assert_error("corrupt_state", use)

    def test_the_record_of_a_check_holds_digests_and_no_decoded_value(self):
        verified = self.store._read_preparation().release_proof().verified
        self.assertEqual(verified.revision, 2)
        self.assertEqual(sorted(verified.identities), [("note", "paper"), ("note", "second"),
                                                        ("work", "paper"), ("work", "second")])
        for identity in verified.identities.values():
            self.assertRegex(identity, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(verified.creation_revisions, {})
        self.assertIsInstance(verified.rows, bytes)
        self.assertIsNone(verified.history)
        with self.assertRaises(TypeError):
            verified.identities[("work", "paper")] = "0" * 64


class PreliminaryReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def add(self, store, revision):
        return store.mutate("add", {}, lambda tx: tx.put("work", str(revision + 1), {"title": "Committed"}),
                            expected_revision=revision, request_id=str(revision + 1))

    def test_a_missing_request_can_recover_a_hot_journal_before_each_write(self):
        interrupt = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA cache_size = 8")
connection.execute("PRAGMA cache_spill = 8")
connection.execute("BEGIN IMMEDIATE")
connection.execute("UPDATE records SET value = ?", ("x" * (256 * 1024),))
os._exit(23)
"""
        original = Store._read_committed_response
        for method in ("mutate", "mutate_serialized", "mutate_scoped"):
            with self.subTest(method=method):
                store = Store(self.root / method, create=True)
                self.add(store, 0)
                database = store.root / ".exactory" / "research.sqlite3"
                journal = database.with_name(database.name + "-journal")

                def read_then_interrupt(target, *args):
                    response = original(target, *args)
                    self.assertIsNone(response)
                    process = subprocess.run([sys.executable, "-c", interrupt, str(database)],
                                             capture_output=True, timeout=10)
                    self.assertEqual(process.returncode, 23, process.stderr)
                    with self.assertRaises(ResearchError) as caught:
                        store.snapshot()
                    self.assertEqual(caught.exception.code, "store_recovery_required")
                    return response

                with mock.patch.object(Store, "_read_committed_response", read_then_interrupt):
                    if method == "mutate":
                        response = self.add(store, 1)
                    elif method == "mutate_serialized":
                        response = store.mutate_serialized(
                            "add", {}, lambda tx, revision: tx.put("work", str(revision), {"title": "Committed"}),
                            request_id="2")
                    else:
                        # A preparation that the store does not own is refused after the recovery, as by 0.92.0.
                        with self.assertRaises(ResearchError) as caught:
                            store.mutate_scoped("add", {}, lambda tx, revision: None, request_id="2",
                                                _preparation={})
                        self.assertEqual(caught.exception.code, "invalid_preparation")
                        response = None
                self.assertFalse(journal.exists())
                expected = {"1": {"title": "Committed"}}
                if response is not None:
                    self.assertEqual(response["revision"], 2)
                    expected["2"] = {"title": "Committed"}
                self.assertEqual(store.snapshot()["records"]["work"], expected)

    def test_snapshot_precheck_decides_before_an_unsupported_schema(self):
        def guarded(store):
            with store.guarded_snapshot():
                pass

        cases = (("configuration", "research", "{"), ("review_assignment", "a", '{"reading_chain":'))
        for kind, key, malformed in cases:
            store = Store(self.root / kind, create=True)
            store.mutate("add", {}, lambda tx: tx.put(kind, key, {}), expected_revision=0, request_id="1")
            database = store.root / ".exactory" / "research.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE metadata SET schema_version = 2")
                connection.execute("UPDATE records SET value = ?", (malformed,))
            for use in (Store.snapshot, guarded, Store._read_preparation, Store._read_scoped_preparation):
                with self.subTest(kind=kind, use=use.__name__):
                    with self.assertRaises(ResearchError) as caught:
                        use(store)
                    self.assertEqual((caught.exception.code, caught.exception.message),
                                     ("corrupt_state", "Stored JSON is malformed"))
            with self.assertRaises(ResearchError) as caught:
                store.revision
            self.assertEqual(caught.exception.code, "unsupported_schema")

    def test_a_writer_cannot_extend_preliminary_replay_past_its_first_revision(self):
        original_verify, original_add = storage._verify_without_lock, storage._Replay.add_event
        for malformed_suffix in (False, True):
            with self.subTest(malformed_suffix=malformed_suffix):
                store = Store(self.root / str(malformed_suffix), create=True)
                writer = Store(store.root)
                self.add(writer, 0)
                self.add(writer, 1)
                phases, preliminary = [], []

                def append_after_replay_event(replay, row, receipt):
                    result = original_add(replay, row, receipt)
                    if phases == [store]:
                        preliminary.append(row["revision"])
                        self.assertLessEqual(row["revision"], 2)
                        self.add(writer, len(preliminary) + 1)
                    return result

                def verify(target, **options):
                    phases.append(target)
                    try:
                        verified = original_verify(target, **options)
                    finally:
                        phases.pop()
                    if target is store and malformed_suffix:
                        database = store.root / ".exactory" / "research.sqlite3"
                        with closing(sqlite3.connect(database)) as connection, connection:
                            sql = connection.execute("SELECT sql FROM sqlite_master "
                                                     "WHERE name = 'events_no_update'").fetchone()[0]
                            connection.execute("DROP TRIGGER events_no_update")
                            connection.execute("UPDATE events SET digest = 'bad' WHERE revision = 4")
                            connection.execute(sql)
                    return verified

                with mock.patch.object(storage, "_UNLOCKED_BATCH_CHARACTERS", 1), \
                        mock.patch.object(storage, "_verify_without_lock", verify), \
                        mock.patch.object(storage._Replay, "add_event", append_after_replay_event):
                    if malformed_suffix:
                        with self.assertRaises(ResearchError) as caught:
                            store.snapshot()
                        self.assertEqual(caught.exception.code, "corrupt_state")
                        self.assertIn("event digest", caught.exception.message)
                    else:
                        snapshot = store.snapshot()
                        self.assertEqual(snapshot["revision"], 4)
                        self.assertIn("4", snapshot["records"]["work"])
                self.assertEqual(preliminary, [1, 2])


class ChangesComparedInPlaceTests(unittest.TestCase):
    """The comparison of the changes text in place accepts exactly the texts that the complete encoding accepts."""

    TEXTS = ("plain", "", "quote\" and backslash\\", "line\nbreak\ttab", "\u0000control\u001f", "λ🙂é ",
             '{"key":"a","kind":"b","value":{}}', "],[", "a" * 70000)

    def build_changes(self, texts):
        changes = []
        for index, text in enumerate(texts):
            changes.append({"kind": "kind " + text, "key": text + str(index),
                            "value": {"text": text, "numbers": [0, -1, 1.5, 1e-20, 1e20, True, None],
                                      "nested": {"z": [], "a": {text: [text]}}}})
        return changes

    def accepts_as_today(self, text):
        try:
            storage._load(text, list)
        except ResearchError:
            return False
        return True

    def test_canonical_changes_are_accepted_with_the_encoding_of_each_value(self):
        for changes in ([], self.build_changes(self.TEXTS[:1]), self.build_changes(self.TEXTS)):
            text = storage._encode_json(changes, "corrupt_state")
            pairs = storage._encode_changes(text)
            self.assertEqual([change for change, _ in pairs], changes)
            self.assertEqual([value for _, value in pairs],
                             [storage._encode_json(change["value"], "corrupt_state") for change in changes])

    def test_a_text_that_the_complete_encoding_refuses_is_never_accepted(self):
        canonical = storage._encode_json(self.build_changes(self.TEXTS[:2]), "corrupt_state")
        first = storage._encode_json(self.build_changes(self.TEXTS[:1]), "corrupt_state")
        variants = [
            " " + canonical, canonical + " ", canonical[:-1], canonical + "]", canonical.replace(",", ", ", 1),
            canonical.replace(":", ": ", 1), canonical.replace('{"key":', '{ "key":', 1),
            first.replace('"value":{', '"value":{"aaa":1,', 1).replace('"aaa":1,"nested"', '"nested"'),
            '[{"kind":"k","key":"a","value":{}}]', '[{"key":"a","kind":"k","value":{"b":1,"a":2}}]',
            '[{"key":"a","kind":"k","value":{"a":1,"a":1}}]', '[{"key":"a","key":"a","kind":"k","value":{}}]',
            '[{"key":"a","kind":"k","value":{"a":1.0e0}}]', '[{"key":"a","kind":"k","value":{"a":"\\u0041"}}]',
            '[{"key":"\\u0041","kind":"k","value":{}}]', '[{"key":"a","kind":"k","value":{}},]', "[", "{}", "null",
            '[{"key":"a","kind":"k","value":[]}]', '[{"key":"a","kind":"k"}]',
            '[{"extra":1,"key":"a","kind":"k","value":{}}]', '[{"key":1,"kind":"k","value":{}}]',
            b'[{"key":"a","kind":"k","value":{}}]',
        ]
        refused = 0
        for text in variants:
            with self.subTest(text=text[:60]):
                if not self.accepts_as_today(text):
                    refused += 1
                    self.assertIsNone(storage._encode_changes(text))
        self.assertGreaterEqual(refused, 19)


if __name__ == "__main__":
    unittest.main()
