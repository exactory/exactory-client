"""Replay real repeated payloads without serializing validated components again."""

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import tracemalloc
import unittest
from unittest import mock

from research_harness import storage
from research_harness.errors import ResearchError


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_replay_store(root, event_count=40, record_count=48, text_size=4096):
    """Write synthetic event/receipt SQL in the released format, without quadratic setup replay."""
    store = storage.Store(root, create=True)
    snapshots = [{}]
    change_count = 0
    value = {"text": ('β🙂"\\\n\t' * (text_size // 6 + 1))[:text_size],
             "scalars": [None, False, True, 0, -0.0, 1.0, 1e-20, 1e20], "nested": {"z": [], "a": {}}}
    with sqlite3.connect(str(store._path)) as connection:
        for revision in range(1, event_count + 1):
            changes = []
            if revision == 1:
                changes.extend((dict(kind="configuration", key="research", value={"profile": "research"}),
                                dict(kind="workspace", key="study", value={"stage": "literature"})))
            for index in range(record_count):
                changes.append(dict(kind="work", key="item-" + str(index),
                                    value=dict(value, generation=revision % 3)))
            # A key can be written more than once in an event; the last change wins.
            changes.append(dict(kind="work", key="item-0", value=dict(value, generation=revision)))
            result = {"copies": [value] * 12, "revision": revision}
            event = dict(revision=revision, request_id='request-β"\\\n' + str(revision),
                         operation='replay-🙂"\\\t', payload={"copies": [value] * 12},
                         changes=changes, result=result)
            response = {key: event[key] for key in ("revision", "request_id", "result")}
            connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (revision, event["request_id"], event["operation"], encode(event["payload"]),
                                encode(changes), encode(result), digest(encode(event))))
            connection.execute("INSERT INTO receipts VALUES (?, ?, ?, ?)",
                               (event["request_id"], revision, encode(response), digest(encode(response))))
            current = copy.deepcopy(snapshots[-1])
            for change in changes:
                encoded = encode(change["value"])
                connection.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?)",
                                   (change["kind"], change["key"], encoded, digest(encoded)))
                current.setdefault(change["kind"], {})[change["key"]] = change["value"]
            snapshots.append(current)
            change_count += len(changes)
        connection.execute("UPDATE metadata SET revision = ?", (event_count,))
    return store, snapshots, change_count


def read_replay_oracle(store):
    snapshot = store.snapshot()
    records = snapshot["records"]
    histories = []
    for revision in range(snapshot["revision"] + 1):
        view = records.read_snapshot(revision)
        histories.append(digest(encode({kind: dict(view[kind]) for kind in view})))
    with store._connection() as connection:
        fingerprint = hashlib.sha256()
        requests = set()
        storage._validate(connection, fingerprint=fingerprint, requests=requests)
    return dict(snapshot=digest(encode(snapshot)), histories=histories,
                fingerprint=fingerprint.hexdigest(), requests=sorted(requests))


class ReplayEncodingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_large_repeated_replay_encodes_each_component_and_change_once(self):
        store, snapshots, changes = write_replay_store(self.root)
        with mock.patch.object(storage, "_encode_json", wraps=storage._encode_json) as encode_json:
            snapshot = store.snapshot()
        self.assertEqual(snapshot, {"revision": 40, "records": snapshots[-1]})
        # Three event components, one encode per changed value, and the configuration precheck.
        self.assertEqual(encode_json.call_count, 3 * 40 + changes + 1)

    def test_snapshots_histories_and_fingerprint_equal_released_0736(self):
        store, snapshots, _ = write_replay_store(self.root)
        oracle_path = Path(__file__).with_name("fixtures") / "replay_encoding_0736.json"
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        self.assertEqual(read_replay_oracle(store), oracle)
        records = store.snapshot()["records"]
        for revision, expected in enumerate(snapshots):
            with self.subTest(revision=revision):
                view = records.read_snapshot(revision)
                self.assertEqual({kind: dict(view[kind]) for kind in view}, expected)
                for kind, values in expected.items():
                    for key, value in values.items():
                        self.assertEqual(view.read_record(kind, key, digest(encode(value))), value)

    def test_all_scalar_results_and_escaped_identifiers_keep_their_receipts(self):
        store = storage.Store(self.root, create=True)
        for revision, result in enumerate((None, False, True, 0, -0.0, 1.0, 1e-20, 1e20,
                                           'β🙂"\\\n\t\u2028', [], [1, None], {}), 1):
            request_id = 'β🙂"\\\n\t-' + str(revision)
            response = store.mutate('operation-β🙂"\\\n\t', {}, lambda tx: result,
                                    expected_revision=revision - 1, request_id=request_id)
            self.assertEqual(response, {"revision": revision, "request_id": request_id, "result": result})
            self.assertEqual(store.snapshot(), {"revision": revision, "records": {}})
            self.assertEqual(store.committed_request(request_id)["response"], response)

    def test_corruption_keeps_the_released_error_and_diagnostic_precedence(self):
        malformed = "Stored JSON is malformed"
        noncanonical = "Stored JSON is not canonical or has the wrong type"
        event_error = "Research event digest does not match its original values"
        receipt_error = "Research request receipt does not match its original event"
        row_error = "Research record differs from its committed history"
        cases = []
        for field in ("payload", "changes", "result"):
            cases.extend((("UPDATE events SET " + field + " = ? WHERE revision = 1", ("{",), malformed),
                          ("UPDATE events SET " + field + " = ? WHERE revision = 1", (" {}",), noncanonical)))
        cases.extend((
            ("UPDATE events SET payload = '{}' WHERE revision = 1", (), event_error),
            ("UPDATE events SET digest = 'changed' WHERE revision = 1", (), event_error),
            ("UPDATE receipts SET response = '{}' WHERE revision = 1", (), receipt_error),
            ("UPDATE receipts SET response = '{' WHERE revision = 1", (), receipt_error),
            ("UPDATE receipts SET response = ? WHERE revision = 1", (b"{}",), receipt_error),
            ("UPDATE receipts SET response = CAST(response AS BLOB) WHERE revision = 1", (), receipt_error),
            ("UPDATE receipts SET response = zeroblob(length(response)) WHERE revision = 1", (), receipt_error),
            ("UPDATE receipts SET digest = 'changed' WHERE revision = 1", (), receipt_error),
            ("UPDATE records SET value = ? WHERE kind = 'work'", ("{",), malformed),
            ("UPDATE records SET value = ? WHERE kind = 'work'", (" {}",), noncanonical),
            ("UPDATE records SET value = '[]' WHERE kind = 'work'", (), noncanonical),
            ("UPDATE records SET value = '{}' WHERE kind = 'work'", (), row_error),
            ("UPDATE records SET digest = 'changed' WHERE kind = 'work'", (), row_error),
            ("DELETE FROM receipts WHERE revision = 1", (), receipt_error),
            ("DELETE FROM records WHERE kind = 'work'", (), "Research records are missing committed values"),
            ("DELETE FROM events WHERE revision = 1", (), "Research event revisions are not contiguous"),
        ))
        for index, (statement, parameters, message) in enumerate(cases):
            with self.subTest(statement=statement, parameters=parameters):
                store, _, _ = write_replay_store(self.root / str(index), 2, 2, 12)
                self.assertEqual(store.snapshot()["revision"], 2)
                with sqlite3.connect(str(store._path)) as connection:
                    guards = connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
                    for name, _ in guards:
                        connection.execute("DROP TRIGGER " + name)
                    connection.execute(statement, parameters)
                    for _, sql in guards:
                        connection.execute(sql)
                for action in (store.snapshot, lambda: store.revision,
                               lambda: store.committed_request('request-β"\\\n1')):
                    with self.assertRaises(ResearchError) as caught:
                        action()
                    self.assertEqual(caught.exception.code, "corrupt_state")
                    self.assertEqual(caught.exception.message, message)

    def test_digest_keeps_utf8_scratch_bounded(self):
        text = "🙂" * (storage._UTF8_CHUNK_CHARACTERS * 8 + 1)
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        tracemalloc.start()
        try:
            actual = storage._digest(text)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(actual, expected)
        self.assertLess(peak, storage._UTF8_CHUNK_CHARACTERS * 12)


if __name__ == "__main__":
    unittest.main()
