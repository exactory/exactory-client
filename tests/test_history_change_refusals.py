"""Each change of a research store that 0.91.11 refuses stays refused with the code and the message of 0.91.11,
wherever the change happens during a use (record 20261120143200-every-transaction-replays-the-whole-history, finding
F1 of its review: an event row at revision 0 or below was accepted).

The fixture holds the outcome of each change and use on the store code of 0.91.11, for the positions that 0.91.11
has: before the use, between the read of a prepared write and its write, and just before the write lock of a write.
A use now replays the history in short read transactions before its own transaction. A change between those
transactions, or between that replay and the transaction, reaches the transaction of the use, as a change before the
use reaches the one transaction of 0.91.11, so its outcome is that of the same change before the use.
"""

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from unittest import mock

from research_harness import operations, storage
from research_harness.errors import ResearchError
from research_harness.storage import Store

FIXTURE_PATH = Path(__file__).with_name("fixtures") / "history_change_refusals_0_91_11.json"
_INSERT_EVENT_SQL = ("INSERT INTO events (revision, request_id, operation, payload, changes, result, digest) "
                     "VALUES (?, ?, 'forged', '{}', ?, '{}', 'x')")

# Each change as SQL statements that run with the history guards dropped (except the added index, which needs no
# guard dropped).
CHANGES = {
    "event at revision 0, no receipt": [(_INSERT_EVENT_SQL, (0, "forged-0", "[]"))],
    "event at revision -7, no receipt": [(_INSERT_EVENT_SQL, (-7, "forged-n", "[]"))],
    "event at revision 0 that writes a record, no receipt": [(_INSERT_EVENT_SQL, (
        0, "forged-w", '[{"key":"v","kind":"value_review","value":{"recorded_revision":0}}]'))],
    "event at revision 0 with a receipt": [
        (_INSERT_EVENT_SQL, (0, "forged-r", "[]")),
        ("INSERT INTO receipts (request_id, revision, response, digest) VALUES ('forged-r', 0, '{}', 'x')", ())],
    "event at revision 4, metadata kept": [(_INSERT_EVENT_SQL, (4, "forged-4", "[]"))],
    "record value as BLOB, same bytes": [("UPDATE records SET value = CAST(value AS BLOB) WHERE key = 'a'", ())],
    "event payload as BLOB, same bytes": [("UPDATE events SET payload = CAST(payload AS BLOB) WHERE revision = 1", ())],
    "event changes as BLOB, same bytes": [("UPDATE events SET changes = CAST(changes AS BLOB) WHERE revision = 2", ())],
    "receipt digest as BLOB": [("UPDATE receipts SET digest = CAST(digest AS BLOB) WHERE revision = 1", ())],
    "extra receipt": [("INSERT INTO receipts (request_id, revision, response, digest) "
                       "VALUES ('extra', 99, '{}', 'x')", ())],
    "added index": [("CREATE INDEX extra ON records (digest)", ())],
}


def add(store, key, revision, request):
    def apply(tx):
        tx.put("work", key, {"title": key})
        return {"id": key}
    return store.mutate("add", {"key": key}, apply, expected_revision=revision, request_id=request)


def write_prepared(store, callback=None):
    def prepare(records, payload):
        if callback is not None:
            callback()
        return [("work", "p", {"title": "p"})], {"ok": True}
    return operations.prepared_mutation(store, "test.prepared", {"n": 1}, prepare, expected_revision=3,
                                        request_id="prepared-1")


def write_scoped(store, callback=None):
    def prepare(records, payload):
        if callback is not None:
            callback()
        records.get("work", {}).get("a")
        return [("work", "s", {"title": "s"})], {"ok": True}
    return operations.prepared_task_mutation(store, "test.scoped", {"n": 1}, prepare, request_id="scoped-1")


USES = {
    "Store.revision": lambda store: store.revision,
    "Store.snapshot": lambda store: store.snapshot(),
    "Store.committed_request": lambda store: store.committed_request("add-a"),
    "Store.mutate": lambda store: add(store, "d", 3, "add-d"),
    "Store.mutate_serialized": lambda store: store.mutate_serialized(
        "test.serialized", {"n": 1}, lambda tx, revision: tx.put("work", "z", {"title": "z"}),
        request_id="serialized-1"),
    "prepared write": write_prepared,
    "scoped write": write_scoped,
}
PREPARED_WRITES = ("prepared write", "scoped write")
WRITES = ("Store.mutate", "Store.mutate_serialized") + PREPARED_WRITES


def change_store(database, name):
    with closing(sqlite3.connect(str(database))) as connection:
        with connection:
            guards = [] if name == "added index" else connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
            for guard, _ in guards:
                connection.execute("DROP TRIGGER " + guard)
            for statement, parameters in CHANGES[name]:
                connection.execute(statement, parameters)
            for _, sql in guards:
                connection.execute(sql)


@contextmanager
def change_before_the_write_lock(action):
    """Run action once, just before a use asks for the write lock."""
    original, done = Store._connection, []

    @contextmanager
    def connection(store, *, writable=False, **options):
        if writable and options.get("write_lock", True) and not done:
            done.append(True)
            action()
        with original(store, writable=writable, **options) as opened:
            yield opened

    with mock.patch.object(Store, "_connection", connection):
        yield


@contextmanager
def change_between_the_short_reads(action):
    """Run action once, after the first short read transaction of the replay before the transaction of a use has
    ended and before the next one; each short read takes one event."""
    original_connection, original_add_event = Store._connection, storage._Replay.add_event
    open_transactions, done = [], []

    @contextmanager
    def connection(store, **options):
        with original_connection(store, **options) as opened:
            open_transactions.append(True)
            try:
                yield opened
            finally:
                open_transactions.pop()

    def add_event(replay, row, receipt):
        if not open_transactions and not done:
            done.append(True)
            action()
        return original_add_event(replay, row, receipt)

    with mock.patch.object(storage, "_UNLOCKED_BATCH_CHARACTERS", 1), \
            mock.patch.object(Store, "_connection", connection), \
            mock.patch.object(storage._Replay, "add_event", add_event):
        yield


@contextmanager
def change_after_the_replay(action):
    """Run action once, after the replay before the transaction of a use and before that transaction."""
    original, done = storage._verify_without_lock, []

    def verify_then_change(store, **options):
        verified = original(store, **options)
        if not done:
            done.append(True)
            action()
        return verified

    with mock.patch.object(storage, "_verify_without_lock", verify_then_change):
        yield


def read_outcome(action):
    """{"code", "message"} of the refusal of action, or {"code": "accepted"} when action returns."""
    try:
        action()
    except ResearchError as error:
        return {"code": error.code, "message": error.message}
    return {"code": "accepted"}


class _Stores:
    """For each case, a copy of one template store with three events, in one temporary folder."""

    def __init__(self, folder, template):
        self.root = Path(folder) / "workspace"
        self.database = self.root / ".exactory" / "research.sqlite3"
        self.template = template

    @staticmethod
    def build_template(path):
        store = Store(path, create=True)
        for revision, key in enumerate(("a", "b", "c")):
            add(store, key, revision, "add-" + key)

    def build(self):
        if self.root.exists():
            shutil.rmtree(self.root)
        shutil.copytree(self.template, self.root)
        return Store(self.root)

    def read_outcome_before_the_use(self, name, use):
        self.build()
        change_store(self.database, name)
        return read_outcome(lambda: USES[use](Store(self.root)))

    def read_outcome_between_the_read_and_the_write(self, name, use):
        store = self.build()
        return read_outcome(lambda: USES[use](store, lambda: change_store(self.database, name)))

    def read_outcome_before_the_write_lock(self, name, use):
        store = self.build()
        with change_before_the_write_lock(lambda: change_store(self.database, name)):
            return read_outcome(lambda: USES[use](store))

    def read_outcome_between_the_short_reads(self, name, use):
        store = self.build()
        with change_between_the_short_reads(lambda: change_store(self.database, name)):
            return read_outcome(lambda: USES[use](store))

    def read_outcome_after_the_replay(self, name, use):
        store = self.build()
        with change_after_the_replay(lambda: change_store(self.database, name)):
            return read_outcome(lambda: USES[use](store))


def read_outcomes_of_0_91_11_positions(folder):
    """{position: {change: {use: outcome}}} for the positions that 0.91.11 has; the fixture holds this on 0.91.11."""
    template = Path(folder) / "template"
    _Stores.build_template(template)
    stores = _Stores(folder, template)
    positions = {"before the use": (stores.read_outcome_before_the_use, tuple(USES)),
                 "between the read and the write": (stores.read_outcome_between_the_read_and_the_write,
                                                    PREPARED_WRITES),
                 "just before the write lock": (stores.read_outcome_before_the_write_lock, WRITES)}
    return {position: {name: {use: read(name, use) for use in uses} for name in CHANGES}
            for position, (read, uses) in positions.items()}


class HistoryChangeRefusalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        template = Path(temporary.name) / "template"
        _Stores.build_template(template)
        self.stores = _Stores(temporary.name, template)
        self.expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["outcomes"]

    def test_history_changes_are_refused_as_by_0_91_11(self):
        """All 297 change, use and position combinations share one template, including across CI shards."""
        matrix = (
            ("before use", "before the use", self.stores.read_outcome_before_the_use, USES),
            ("after preparation", "between the read and the write",
             self.stores.read_outcome_between_the_read_and_the_write, PREPARED_WRITES),
            ("before write lock", "just before the write lock", self.stores.read_outcome_before_the_write_lock, WRITES),
            ("between short reads", "before the use", self.stores.read_outcome_between_the_short_reads, USES),
            ("after replay", "before the use", self.stores.read_outcome_after_the_replay, USES),
        )
        for position, oracle_position, read, uses in matrix:
            for name in CHANGES:
                for use in uses:
                    with self.subTest(position=position, change=name, use=use):
                        outcome = read(name, use)
                        self.assertNotEqual(outcome["code"], "accepted")
                        self.assertEqual(outcome, self.expected[oracle_position][name][use])


if __name__ == "__main__":
    unittest.main()
