"""Logical UTF-8 row proofs and the public fault order of release 0.91.11."""

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import mock

from research_harness import storage
from research_harness.errors import ResearchError


TOKEN = "Aé中🌱"
LONG_COLUMNS = (("events", "payload"), ("events", "changes"), ("events", "result"), ("receipts", "response"))
UTF16_ENCODINGS = ("UTF-16LE", "UTF-16BE")
SQLITE_MALFORMED_MSG = "Research SQLite state is malformed or inconsistent"
STORED_JSON_MALFORMED_MSG = "Stored JSON is malformed"
RECEIPT_MISMATCH_MSG = "Research request receipt does not match its original event"
EVENT_DIGEST_MISMATCH_MSG = "Research event digest does not match its original values"
NONCANONICAL_JSON_MSG = "Stored JSON is not canonical or has the wrong type"
# Python 3.9 decodes the next SQLite row before giving its caller the current one.
# Keep the public result of 0.91.11 on each interpreter.
NEXT_EVENT_PRECEDENCE_MSG = SQLITE_MALFORMED_MSG if sys.version_info[:2] == (3, 9) else STORED_JSON_MALFORMED_MSG
MIXED_FAULTS = (
    ("undecodable payload and digest", (
        (0, "UPDATE events SET payload = CAST(X'ff' AS TEXT), "
            "digest = CAST(X'62757379ff' AS TEXT) WHERE revision = ?"),
    ), SQLITE_MALFORMED_MSG),
    ("bad JSON before the next event", (
        (0, "UPDATE events SET payload = 'x' WHERE revision = ?"),
        (1, "UPDATE events SET result = CAST(X'ff' AS TEXT) WHERE revision = ?"),
    ), NEXT_EVENT_PRECEDENCE_MSG),
    ("bad JSON before its receipt", (
        (0, "UPDATE events SET payload = 'x' WHERE revision = ?"),
        (0, "UPDATE receipts SET response = CAST(X'ff' AS TEXT) WHERE revision = ?"),
    ), STORED_JSON_MALFORMED_MSG),
    ("bad JSON before its receipt containing busy", (
        (0, "UPDATE events SET payload = 'x' WHERE revision = ?"),
        (0, "UPDATE receipts SET response = CAST(X'62757379ff' AS TEXT) WHERE revision = ?"),
    ), STORED_JSON_MALFORMED_MSG),
)


class HistoryProofBytesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store_count = 0

    def build_store(self, encoding="UTF-8", text=TOKEN):
        self.store_count += 1
        store = storage.Store(self.root / str(self.store_count), create=True)
        database = self.get_database_path(store)
        if encoding != "UTF-8":
            with closing(sqlite3.connect(database)) as connection:
                schema = "\n".join(connection.iterdump())
            database.unlink()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA encoding = '" + encoding + "'")
                connection.executescript(schema)
        self.add_event(store, 1, text)
        return store

    def add_event(self, store, revision, text=TOKEN):
        value = {"v": text}

        def apply(transaction):
            transaction.put("work", "1", value)
            return value

        store.mutate("add", value, apply, expected_revision=revision - 1,
                     request_id="id" if revision == 1 else str(revision))

    def get_database_path(self, store):
        return store.root / ".exactory" / "research.sqlite3"

    def read_long_values(self, store, *, as_blobs=False):
        with closing(sqlite3.connect(self.get_database_path(store))) as connection:
            values = []
            for table, column in LONG_COLUMNS:
                expression = "CAST(" + column + " AS BLOB)" if as_blobs else column
                values.append(connection.execute("SELECT " + expression + " FROM " + table).fetchone()[0])
            return values

    def change_rows(self, store, *changes):
        with closing(sqlite3.connect(self.get_database_path(store))) as connection, connection:
            guards = connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
            for name, _ in guards:
                connection.execute("DROP TRIGGER " + name)
            for statement, parameters in changes:
                connection.execute(statement, parameters)
            for _, sql in guards:
                connection.execute(sql)

    @contextmanager
    def watch_proof(self, factory=None):
        results, prove = [], storage._prove_events

        def watch(connection, verified):
            original = connection.text_factory
            if factory is not None:
                connection.text_factory = factory
            try:
                result = prove(connection, verified)
                results.append(result)
                return result
            finally:
                connection.text_factory = original

        with mock.patch.object(storage, "_prove_events", watch):
            yield results

    @contextmanager
    def change_after_replay(self, change):
        verify = storage._verify_without_lock

        def verify_then_change(store, **options):
            verified = verify(store, **options)
            self.assertIsNotNone(verified)
            with mock.patch.object(storage, "_verify_without_lock", verify):
                change()
            return verified

        with mock.patch.object(storage, "_verify_without_lock", verify_then_change):
            yield

    def assert_refused(self, store, message):
        with self.assertRaises(ResearchError) as caught:
            _ = store.revision
        self.assertEqual((caught.exception.code, caught.exception.message), ("corrupt_state", message))

    def test_proves_multibyte_text_without_decoding_long_values(self):
        store = self.build_store()
        long_values = self.read_long_values(store)
        self.assertTrue(all(TOKEN in value for value in long_values))
        long_bytes = {value.encode("utf-8") for value in long_values}
        decoded = []

        def decode(value):
            if value in long_bytes:
                decoded.append(value)
            return value.decode("utf-8")

        with self.watch_proof(decode) as proofs:
            self.assertEqual(store.revision, 1)
        self.assertEqual(len(proofs), 1)
        self.assertEqual(len(proofs[0]), 1)
        self.assertEqual(decoded, [])

    def test_proves_utf16_and_rejects_equal_native_bytes_of_different_text(self):
        source = self.build_store(text="AB")
        verified = storage._verify_without_lock(source)
        native_bytes = self.read_long_values(source, as_blobs=True)
        for encoding in UTF16_ENCODINGS:
            with self.subTest(encoding=encoding):
                store = self.build_store(encoding)
                with self.watch_proof() as proofs:
                    self.assertEqual(store.revision, 1)
                self.assertEqual(len(proofs), 1)
                self.assertEqual(len(proofs[0]), 1)

                other = self.build_store(encoding, text="AB")
                forged = [value.decode(encoding) for value in native_bytes]
                self.change_rows(other, ("UPDATE events SET payload = ?, changes = ?, result = ?", forged[:3]),
                                 ("UPDATE receipts SET response = ?", (forged[3],)))
                self.assertEqual(self.read_long_values(other, as_blobs=True), native_bytes)
                self.assertNotEqual(self.read_long_values(other), self.read_long_values(source))
                with self.watch_proof() as proofs, mock.patch.object(storage, "_verify_without_lock",
                                                                     return_value=verified):
                    self.assert_refused(other, STORED_JSON_MALFORMED_MSG)
                self.assertEqual(proofs, [None])

    def test_same_length_canonical_edit_declines_proof(self):
        for index, (table, column) in enumerate(LONG_COLUMNS):
            with self.subTest(column=column):
                store = self.build_store()
                original = self.read_long_values(store)[index]
                changed = original.replace(TOKEN, "B" + TOKEN[1:], 1)
                self.assertEqual(len(changed.encode("utf-8")), len(original.encode("utf-8")))
                expected = RECEIPT_MISMATCH_MSG if table == "receipts" else EVENT_DIGEST_MISMATCH_MSG
                with self.watch_proof() as proofs, self.change_after_replay(lambda: self.change_rows(
                        store, ("UPDATE " + table + " SET " + column + " = ?", (changed,)))):
                    self.assert_refused(store, expected)
                self.assertEqual(proofs, [None])

    def test_text_replaced_by_blob_with_same_bytes_declines_proof(self):
        for index, (table, column) in enumerate(LONG_COLUMNS):
            with self.subTest(column=column):
                store = self.build_store()
                original = self.read_long_values(store)[index].encode("utf-8")
                with self.watch_proof() as proofs, self.change_after_replay(lambda: self.change_rows(
                        store, ("UPDATE " + table + " SET " + column + " = ?", (original,)))):
                    self.assert_refused(store, RECEIPT_MISMATCH_MSG if table == "receipts" else NONCANONICAL_JSON_MSG)
                self.assertEqual(proofs, [None])

    def test_invalid_utf8_and_raw_nul_decline_proof(self):
        for byte in (b"\xff", b"\x00"):
            for index, (table, column) in enumerate(LONG_COLUMNS):
                with self.subTest(byte=byte, column=column):
                    store = self.build_store()
                    original = self.read_long_values(store)[index].encode("utf-8")
                    changed = original.replace(b"A", byte, 1)
                    if byte == b"\xff":
                        expected = SQLITE_MALFORMED_MSG
                    else:
                        expected = RECEIPT_MISMATCH_MSG if table == "receipts" else STORED_JSON_MALFORMED_MSG
                    with self.watch_proof() as proofs, self.change_after_replay(lambda: self.change_rows(store, (
                            "UPDATE " + table + " SET " + column + " = CAST(? AS TEXT)", (changed,)))):
                        self.assert_refused(store, expected)
                    self.assertEqual(proofs, [None])

    def test_mixed_faults_follow_release_order(self):
        prove = storage._prove_events
        for name, statements, message in MIXED_FAULTS:
            for position in ("before use", "in prefix", "in suffix"):
                with self.subTest(case=name, position=position):
                    store = self.build_store()
                    first = 2 if position == "in suffix" else 1
                    last = first + statements[-1][0]
                    changes = tuple((statement, (first + offset,)) for offset, statement in statements)
                    if position != "in suffix" and last == 2:
                        self.add_event(store, 2)

                    def damage():
                        if position == "in suffix":
                            with mock.patch.object(storage, "_prove_events", prove):
                                for revision in range(first, last + 1):
                                    self.add_event(store, revision)
                        self.change_rows(store, *changes)

                    if position == "before use":
                        damage()
                        self.assert_refused(store, message)
                    else:
                        with self.watch_proof() as proofs, self.change_after_replay(damage):
                            self.assert_refused(store, message)
                        if position == "in suffix":
                            self.assertEqual(len(proofs), 1)
                            self.assertEqual(len(proofs[0]), 1)
                        else:
                            self.assertEqual(proofs, [None])

    def test_proof_restores_text_factory_after_success_and_errors(self):
        store = self.build_store()
        verified = storage._verify_without_lock(store)
        with store._connection() as connection:
            self.assertEqual(len(storage._prove_events(connection, verified)), 1)
            self.assertIs(connection.text_factory, str)
            self.assertEqual(connection.execute("SELECT 'text'").fetchone()[0], "text")

            for message in ("database is locked", "disk I/O error"):
                with self.subTest(message=message):
                    failure = sqlite3.OperationalError(message)
                    with mock.patch.object(storage, "_read_receipt", side_effect=failure):
                        with self.assertRaises(sqlite3.OperationalError) as caught:
                            storage._prove_events(connection, verified)
                    self.assertIs(caught.exception, failure)
                    self.assertIs(connection.text_factory, str)
                    self.assertEqual(connection.execute("SELECT 'text'").fetchone()[0], "text")

        self.change_rows(store, ("UPDATE events SET digest = CAST(X'ff' AS TEXT)", ()))
        with store._connection() as connection:
            self.assertIsNone(storage._prove_events(connection, verified))
            self.assertIs(connection.text_factory, str)
            self.assertEqual(connection.execute("SELECT 'text'").fetchone()[0], "text")
