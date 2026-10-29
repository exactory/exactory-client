"""Stored JSON keeps canonical checks without repeating the caller type walk."""

from collections import OrderedDict
from contextlib import closing, contextmanager
from decimal import Decimal
from fractions import Fraction
import gc
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import tracemalloc
import unittest

from research_harness import storage
from research_harness.errors import ResearchError


@contextmanager
def count_json_type_calls():
    counts = {"calls": 0}
    previous_profiler = sys.getprofile()

    def count_call(frame, event, argument):
        if event == "call" and frame.f_code is storage._json_types.__code__:
            counts["calls"] += 1

    sys.setprofile(count_call)
    try:
        yield counts
    finally:
        sys.setprofile(previous_profiler)


def check_legacy_json_types(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            check_legacy_json_types(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            check_legacy_json_types(item)
        return
    raise ValueError("Value is not representable in JSON")

def encode_legacy_json(value, code="invalid_input"):
    try:
        check_legacy_json_types(value)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"))
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ResearchError(code, "Expected finite JSON data with string object keys") from error

def build_nested_value(depth, leaf=None):
    value = leaf
    for level in range(depth):
        value = {"value": value} if level % 3 == 0 else ([value] if level % 3 == 1 else (value,))
    return value

def build_value_corpus():
    integer_type = type("IntegerSubclass", (int,), {})
    float_type = type("FloatSubclass", (float,), {})
    string_type = type("StringSubclass", (str,), {})
    list_type = type("ListSubclass", (list,), {})
    tuple_type = type("TupleSubclass", (tuple,), {})
    dict_type = type("DictSubclass", (dict,), {})
    values = [None, False, True, "", "\x00\b\n\r\t\"\\", "\u00e9", "e\u0301", "\u03bb\u4e2d\U0001f52c",
              "\ud800", "\udfff", "\ud83d\ude00", 0, -1, 1, -(2 ** 63), 2 ** 63 - 1,
              10 ** 4299, -(10 ** 4299), 10 ** 4300 - 1, -(10 ** 4300 - 1), 10 ** 4300,
              0.0, -0.0, sys.float_info.max, -sys.float_info.max, sys.float_info.min,
              float.fromhex("0x0.0000000000001p-1022"), 1e-7, 1e20, float("nan"), float("inf"), -float("inf"),
              [], (), {}, {"z": 1, "a": 2}, {"\U0001f52c": 1, "\u00e9": 2, "e\u0301": 3},
              {"\ud800": 1}, {1: "integer key"}, {None: "null key"}, {(1,): "tuple key"},
              {string_type("key"): "allowed key"}, string_type("value"), integer_type(1), float_type(1),
              list_type([True]), tuple_type((False,)), dict_type(a=1), OrderedDict((("z", 1), ("a", 2))),
              b"bytes", bytearray(b"bytes"), {1, 2}, frozenset((1, 2)), Decimal("1.25"), Fraction(1, 2),
              complex(1, 2), object()]
    randomizer = random.Random(20261028)
    leaves = values[:]

    def build_random_value(depth):
        if depth == 0 or randomizer.randrange(4) == 0:
            return randomizer.choice(leaves)
        children = [build_random_value(depth - 1) for _ in range(randomizer.randrange(5))]
        container = randomizer.randrange(3)
        if container == 0:
            return children
        if container == 1:
            return tuple(children)
        return {str(index) + "\u03bb": child for index, child in enumerate(children)}

    values.extend(build_random_value(6) for _ in range(256))
    values.extend(build_nested_value(depth) for depth in (32, 100, 101, 400, 2000))
    for container in (list, tuple, dict):
        for depth in range(sys.getrecursionlimit() - 40, sys.getrecursionlimit() + 5):
            value = 0
            for _ in range(depth):
                value = {"value": value} if container is dict else container((value,))
            values.append(value)
    shared = {"shared": [1.0, True, None]}
    values.append([shared, shared])
    circular = []
    circular.append(circular)
    values.append(circular)
    circular_object = {}
    circular_object["self"] = circular_object
    values.append(circular_object)
    return values

def read_encoding_result(encode, value, code):
    try:
        encoded = encode(value, code)
    except ResearchError as error:
        return error.as_dict()
    content = encoded.encode("utf-8")
    return content, hashlib.sha256(content).hexdigest()

def read_native_json(encoded, expected_type=None):
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as error:
        raise ResearchError("corrupt_state", "Stored JSON is malformed") from error
    if expected_type is not None and not isinstance(value, expected_type):
        raise ResearchError("corrupt_state", "Stored JSON is not canonical or has the wrong type")
    try:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
                               allow_nan=False, separators=(",", ":"))
        canonical.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ResearchError("corrupt_state", "Expected finite JSON data with string object keys") from error
    if canonical != encoded:
        raise ResearchError("corrupt_state", "Stored JSON is not canonical or has the wrong type")
    return value


def read_load_result(load, encoded, expected_type):
    try:
        value = load(encoded, expected_type)
    except ResearchError as error:
        return error.as_dict()
    content = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return content, hashlib.sha256(content).hexdigest()


def build_stored_json_corpus():
    texts = ['null', 'true', 'false', '0', '-0.0', '1.0', '[]', '{}', '{"z":0,"a":1}',
             '{"a":1,"a":2}', '{"a":1,"a":1}', '{"outer":{"a":1,"a":2}}', '{"a":1, "b":2}',
             '"\\ud800"', '"\\udfff"', '"\\ud83d\\ude00"', '"\\u03bb"', '"\u03bb"',
             '{"\u03bb":[true,null,1.0]}', 'NaN', 'Infinity', '-Infinity', '1e999', '[NaN]',
             '{"a":-Infinity}', '[1,]', '[', '1' * 4301, '[' * 2000 + '0' + ']' * 2000, None, b'{}']
    leaves = [None, True, False, "", "\x00\b\n\r\t\"\\", "\u00e9", "e\u0301", "\u03bb\u4e2d\U0001f52c",
              0, -1, -(2 ** 63), 2 ** 63 - 1, 10 ** 4300 - 1, -(10 ** 4300 - 1),
              0.0, -0.0, sys.float_info.max, -sys.float_info.max, sys.float_info.min,
              float.fromhex("0x0.0000000000001p-1022"), 1e-7, 1e20]
    randomizer = random.Random(20261028)

    def build_random_value(depth):
        if depth == 0 or randomizer.randrange(4) == 0:
            return randomizer.choice(leaves)
        children = [build_random_value(depth - 1) for _ in range(randomizer.randrange(5))]
        return children if randomizer.randrange(2) else {str(index): child for index, child in enumerate(children)}

    values = leaves + [build_random_value(6) for _ in range(128)]
    for depth in (32, 100, 200):
        value = None
        for level in range(depth):
            value = {"value": value} if level % 2 else [value]
        values.append(value)
    texts.extend(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                            separators=(",", ":")) for value in values)
    return texts


class ResearchStoredJsonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def build_store(self, name):
        store = storage.Store(self.root / name, create=True)

        def put_records(transaction):
            transaction.put("configuration", "research", {"profile": "research"})
            transaction.put("workspace", "study", {"stage": "literature"})
            transaction.put("work", "paper", {"title": "Original \u03bb", "values": [1.0, True, None]})
            return {"nested": [{"value": 1.0}], "unicode": "\U0001f52c"}

        store.mutate("add", {"value": [1.0, True, None]}, put_records, expected_revision=0, request_id="add")
        return store

    def replace_stored_text(self, store, table, column, encoded):
        with closing(sqlite3.connect(store._path)) as connection:
            with connection:
                if table != "records":
                    connection.execute("DROP TRIGGER " + table + "_no_update")
                connection.execute("UPDATE " + table + " SET " + column + " = ? "
                                   + ("WHERE kind = 'work'" if table == "records" else "WHERE revision = 1"),
                                   (encoded,))
                if table != "records":
                    connection.execute(storage._SCHEMA[("trigger", table + "_no_update")])

    def test_validation_bounds_utf8_buffers_for_large_nested_records(self):
        for character in ("λ", "🔬", "e\u0301"):
            for offset in (65535, 65536, 65537, 131071):
                text_prefix = "a" * (offset - len('{"text":"'))
                value = {"text": text_prefix + character * 4}
                with self.subTest(character=character, offset=offset):
                    encoded = encode_legacy_json(value)
                    self.assertEqual(storage._canonical(value), encoded)
                    self.assertEqual(storage._digest(encoded), hashlib.sha256(encoded.encode("utf-8")).hexdigest())
                    for surrogate in ("\ud800", "\udfff"):
                        invalid = {"text": text_prefix + surrogate}
                        for code in ("invalid_input", "corrupt_state"):
                            self.assertEqual(read_encoding_result(storage._canonical, invalid, code),
                                             read_encoding_result(encode_legacy_json, invalid, code))
        store = storage.Store(self.root / "memory", create=True)
        text = "λ" * 1048576

        def put_records(transaction):
            for index in range(3):
                transaction.put("bundle", str(index), {"sections": [{"text": text,
                                "values": [0.0, 1, -1, None, True]}]})
            for index in range(512):
                transaction.put("reference", str(index), {"id": index, "evidence": {"values": [index / 8]}})
            return {"bundles": 3, "references": 512}

        store.mutate("seed", {}, put_records, expected_revision=0, request_id="seed")
        gc.collect()
        records = {}
        history = storage._RecordHistory()
        with store._connection() as connection:
            tracemalloc.start()
            try:
                revision = storage._validate(connection, records=records, history=history)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertEqual(revision, 1)
        self.assertEqual(len(records["reference"]), 512)
        self.assertEqual(records["bundle"]["2"]["sections"][0]["text"], text)
        self.assertEqual(history.read_record("bundle", "0", 1), records["bundle"]["0"])
        # Three 1 Mi-character records plus 512 small records must authenticate within 26 MiB on both Pythons.
        self.assertLess(peak, 26 * 1048576)

    def test_caller_refusals_match_legacy_and_stored_values_follow_json_codecs(self):
        for index, value in enumerate(build_value_corpus()):
            for code in ("invalid_input", "corrupt_state"):
                with self.subTest(caller_index=index, code=code):
                    self.assertEqual(read_encoding_result(storage._canonical, value, code),
                                     read_encoding_result(encode_legacy_json, value, code))
        total_calls = 0
        for index, encoded in enumerate(build_stored_json_corpus()):
            for expected_type in (None, dict, list):
                with self.subTest(index=index, expected_type=expected_type):
                    expected = read_load_result(read_native_json, encoded, expected_type)
                    with count_json_type_calls() as counts:
                        actual = read_load_result(storage._load, encoded, expected_type)
                    self.assertEqual(actual, expected)
                    total_calls += counts["calls"]
        self.assertEqual(total_calls, 0, "Decoded stored values must not repeat the caller type walk")

    def test_validation_refuses_stored_nonfinite_numbers_without_type_walk(self):
        for table, column in (("records", "value"), ("events", "payload"),
                              ("events", "changes"), ("events", "result")):
            for number in ("NaN", "Infinity", "-Infinity", "1e999"):
                with self.subTest(table=table, column=column, number=number):
                    store = self.build_store(table + column + number)
                    encoded = '{"value":' + number + '}'
                    if column == "changes":
                        encoded = '[{"kind":"work","key":"paper","value":' + encoded + '}]'
                    self.replace_stored_text(store, table, column, encoded)
                    with count_json_type_calls() as counts:
                        with self.assertRaises(ResearchError) as caught:
                            store.snapshot()
                    self.assertEqual(caught.exception.as_dict(), {
                        "code": "corrupt_state", "message": "Expected finite JSON data with string object keys"})
                    self.assertEqual(counts["calls"], 0, "Stored nonfinite numbers must be refused by the encoder")

    def test_validation_refuses_noncanonical_stored_text_without_type_walk(self):
        for table, column in (("records", "value"), ("events", "payload"),
                              ("events", "changes"), ("events", "result"), ("receipts", "response")):
            for encoded in ('{"value": 1}', '{"value":1,"value":1}', '{"z":0,"a":1}', '"\\u03bb"'):
                with self.subTest(table=table, column=column, encoded=encoded):
                    store = self.build_store(table + column + str(len(list(self.root.iterdir()))))
                    if column == "changes":
                        encoded = '[' + encoded + ']'
                    self.replace_stored_text(store, table, column, encoded)
                    with count_json_type_calls() as counts:
                        with self.assertRaises(ResearchError) as caught:
                            store.snapshot()
                    self.assertEqual(caught.exception.code, "corrupt_state")
                    message = ("Research request receipt does not match its original event" if table == "receipts"
                               else "Stored JSON is not canonical or has the wrong type")
                    self.assertEqual(caught.exception.message, message)
                    self.assertEqual(counts["calls"], 0, "Canonical comparisons must not repeat the caller type walk")

    def test_history_replay_avoids_type_walk_and_caller_values_keep_strict_checks(self):
        store = self.build_store("history")
        original_request = store.committed_request("add")
        store.mutate("revise", {}, lambda tx: tx.put("work", "paper", {"title": "Revised"}),
                     expected_revision=1, request_id="revise")
        with count_json_type_calls() as counts:
            records = store.snapshot()["records"]
            replayed_request = store.committed_request("add")
        self.assertEqual(replayed_request, original_request)
        self.assertEqual(records.read_snapshot(1)["work"]["paper"], {
            "title": "Original \u03bb", "values": [1.0, True, None]})
        self.assertEqual(records.read_snapshot(2)["work"]["paper"], {"title": "Revised"})
        integer_type = type("CallerInteger", (int,), {})
        value = {"value": integer_type(1)}
        for payload, apply in ((value, lambda tx: None), ({}, lambda tx: value),
                               ({}, lambda tx: tx.put("work", "invalid", value))):
            with self.assertRaises(ResearchError) as caught:
                store.mutate("invalid", payload, apply, expected_revision=2, request_id="invalid")
            self.assertEqual(caught.exception.code, "invalid_input")
        for depth in (101, 2000):
            deep_value = None
            for _ in range(depth):
                deep_value = {"value": deep_value}
            for caller_field in ("payload", "result", "record"):
                with self.subTest(depth=depth, caller_field=caller_field):
                    payload = deep_value if caller_field == "payload" else {}
                    apply = (lambda tx: tx.put("work", "deep", deep_value) if caller_field == "record"
                             else deep_value if caller_field == "result" else None)
                    with self.assertRaises(ResearchError) as caught:
                        store.mutate("deep", payload, apply, expected_revision=2, request_id="deep")
                    message = ("Expected finite JSON data with string object keys"
                               if depth == 2000 and caller_field == "payload" else
                               "JSON to store must nest at most 100 levels "
                               "so that every supported Python reads it back")
                    self.assertEqual(caught.exception.as_dict(), {"code": "invalid_input", "message": message})
        records["work"]["paper"]["value"] = integer_type(1)
        with self.assertRaises(ResearchError) as caught:
            storage._HistoricalReadCache(records).read_record("work", "paper", 2)
        self.assertEqual(caught.exception.code, "invalid_input")
        with self.assertRaises(ResearchError) as caught:
            storage._fingerprint_entry(hashlib.sha256(), ("metadata", integer_type(1), 2))
        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(store.revision, 2)
        self.assertEqual(counts["calls"], 0, "Validated history must be encoded without the caller type walk")


if __name__ == "__main__":
    unittest.main()
