"""Revisioned SQLite records, immutable event patches, and request receipts.

Every mutation holds a BEGIN IMMEDIATE transaction, checks its request receipt
before its expected revision, and commits records, original changes, the result,
and the next revision together. Connections are short-lived. Reads open in
SQLite read-only mode and never initialize or migrate storage.

The SQL metadata row holds schema_version and revision. Scientific configuration
is the ordinary configuration/research record, owned by domain services.
"""

import copy
from bisect import bisect_right
from collections.abc import Mapping
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import threading
import uuid
import weakref
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple, Optional

from .artifacts import ArtifactStore, _Workspace, _filesystem_error, _regular_file
from .errors import ResearchError


_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_SECONDS = 1.0
_DATABASE = "research.sqlite3"
_MAX_REVISION = (1 << 63) - 1
# Every supported Python (3.9 to 3.14) must read back what the store writes. Only writes check these bounds,
# so a store that an earlier release wrote reads as before. Python 3.9.6 converts an integer of any length,
# and Python 3.11 and later refuse to parse one of more than 4300 digits (sys.int_info.default_max_str_digits).
# The default recursion limit of 1000 frames bounds the nesting that a read handles. Measured on 2026-09-30 at
# the top level of a script under Python 3.9.6, 3.11.11, 3.12.8 and 3.13.8: _load reads back 990 to 993 levels,
# Store.snapshot a record value of 982 to 985 levels, and Store.guarded_snapshot, whose copy.deepcopy takes
# two frames for each level, a record value of 492 levels. The depth bound keeps a wide margin below 492.
_MAX_STORED_INTEGER_DIGITS = 4300
_MAX_STORED_INTEGER = 10 ** _MAX_STORED_INTEGER_DIGITS - 1
_MAX_STORED_DEPTH = 100
# A value that the harness takes in nests at most _MAX_INPUT_DEPTH levels: a payload file of exactory-research, a
# verdict body of bind-verdict, and the metric of a run (execution_outputs), which has had this bound since 0.48.0. A
# paper review assignment repeats the stored bundle of manuscript, which the command bound does not count
# (review_protocol.drop_repeated_bundle). Later records and reviewer exports hold copies of the values of an input
# deeper. A reviewer export reads JSON of at most 40 levels (scientific_delivery._MAX_DEPTH,
# scientific_json._check_nesting_bound). The deepest export copy found is 9 levels deeper: the packet of a review
# assignment with role result in a source-limited study holds the readiness export at readiness, which holds each full
# reading at inputs.synthesis.foundation.inventory[i].body_coverage.readings[id], so a read payload of 32 levels reaches
# 41 levels there. A read payload nests deeply only inside the locators of its links, and ScientificDelivery.walk copies
# a locator without adding the levels inside it to the depth. The deepest copy found whose levels the walk counts is 8
# levels deeper: the same packet holds, at readiness.inputs.sources.work, the component of a fulltext payload, which
# acquire_fulltext records as given while the fetch is pending. So an input of 32 levels reaches the 40 levels of an
# export. The deepest record copy found is 12 levels deeper, at context.bundle of a paper review assignment, whose
# bundle holds the same component at
# review_inputs.synthesis.foundation.source_deferrals[i].dependencies.work.fulltexts[k].component.spec. So an input of
# 32 levels reaches 44 of the store's 100 levels in a record.
# Since 0.65.0 a new bundle holds its review inputs and execution observations as artifacts, and the pin checks
# both values against the store bounds before it writes them (publication._check_stored_bounds). The inline
# copies of earlier bundles stay at context.bundle.
_MAX_INPUT_DEPTH = 32
_UTF8_CHUNK_CHARACTERS = 65536
_PUBLICATION_NAME = re.compile(r"\.research-[0-9a-f]{32}\.sqlite3\Z")
_WORKSPACE_LOCKS = weakref.WeakValueDictionary()
_LOCK_REGISTRY_GUARD = threading.Lock()
_SCHEMA = {
    ("table", "metadata"): """CREATE TABLE metadata (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0))""",
    ("table", "records"): """CREATE TABLE records (
        kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
        digest TEXT NOT NULL, PRIMARY KEY (kind, key))""",
    ("table", "events"): """CREATE TABLE events (
        revision INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        operation TEXT NOT NULL, payload TEXT NOT NULL, changes TEXT NOT NULL,
        result TEXT NOT NULL, digest TEXT NOT NULL)""",
    ("table", "receipts"): """CREATE TABLE receipts (
        request_id TEXT PRIMARY KEY NOT NULL,
        revision INTEGER NOT NULL UNIQUE REFERENCES events(revision),
        response TEXT NOT NULL, digest TEXT NOT NULL)""",
}
for _table in ("events", "receipts"):
    for _action in ("UPDATE", "DELETE"):
        _name = _table + "_no_" + _action.lower()
        _SCHEMA[("trigger", _name)] = (
            "CREATE TRIGGER " + _name + " BEFORE " + _action + " ON " + _table
            + " BEGIN SELECT RAISE(ABORT, 'Research history is append-only'); END")


def _text(value, field: str, code: str = "invalid_input"):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ResearchError(code, field + " must be a nonempty string without NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ResearchError(code, field + " must contain valid Unicode") from error


def _json_types(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _json_types(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_types(item)
        return
    raise ValueError("Value is not representable in JSON")


def _check_bounds(value, name, max_depth, depth_reason):
    """Refuse a value that nests deeper than max_depth levels or holds an integer that some supported Python cannot
    read back; the top container is level 1. The refusal calls the value name, and depth_reason ends a depth refusal."""
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list, tuple)):
            if depth > max_depth:
                raise ResearchError("invalid_input", name + " must nest at most " + str(max_depth) + " levels"
                                    + depth_reason)
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
        elif type(item) is int and abs(item) > _MAX_STORED_INTEGER:
            raise ResearchError("invalid_input", name + " must hold no integer of more than "
                                + str(_MAX_STORED_INTEGER_DIGITS)
                                + " digits so that every supported Python reads it back")


def _check_stored_bounds(value, max_depth=_MAX_STORED_DEPTH):
    """Refuse a value to store beyond the bounds that every supported Python reads back."""
    _check_bounds(value, "JSON to store", max_depth, " so that every supported Python reads it back")


def check_command_bounds(value, input_name="A command input"):
    """Refuse a command input that nests deeper than the input bound or holds an integer beyond the store's bound. The
    refusal calls the input input_name."""
    _check_bounds(value, input_name, _MAX_INPUT_DEPTH,
                  ", which leaves room for deeper copies of its values in later records and reviewer exports")


def _canonical(value, code: str = "invalid_input") -> str:
    try:
        _json_types(value)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ResearchError(code, "Expected finite JSON data with string object keys") from error
    return _encode_json(value, code)


def _encode_json(value, code: str) -> str:
    # Stored values are decoded JSON plus checked SQL scalars; caller values first pass _json_types in _canonical.
    try:
        encoded = json.JSONEncoder(sort_keys=True, ensure_ascii=False,
                                   allow_nan=False, separators=(",", ":")).encode(value)
        # Bound UTF-8 scratch buffers independently of the stored value's size.
        for offset in range(0, len(encoded), _UTF8_CHUNK_CHARACTERS):
            encoded[offset:offset + _UTF8_CHUNK_CHARACTERS].encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ResearchError(code, "Expected finite JSON data with string object keys") from error


def _digest(encoded: str) -> str:
    hasher = hashlib.sha256()
    for offset in range(0, len(encoded), _UTF8_CHUNK_CHARACTERS):
        hasher.update(encoded[offset:offset + _UTF8_CHUNK_CHARACTERS].encode("utf-8"))
    return hasher.hexdigest()


def _load(encoded, expected_type=None):
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as error:
        raise ResearchError("corrupt_state", "Stored JSON is malformed") from error
    if (expected_type is not None and not isinstance(value, expected_type)
            or _encode_json(value, "corrupt_state") != encoded):
        raise ResearchError("corrupt_state", "Stored JSON is not canonical or has the wrong type")
    return value


def _sql_error(error: sqlite3.Error, *, readonly: bool = False) -> ResearchError:
    message = str(error).lower()
    if "locked" in message or "busy" in message:
        return ResearchError("store_busy", "Research store is busy; retry the same request after the writer finishes")
    if readonly and ("readonly" in message or "read-only" in message):
        return ResearchError("store_recovery_required", "SQLite needs to recover an interrupted transaction; "
                             "explicitly reopen Store(root, create=True) to recover without migrating the schema")
    if any(fragment in message for fragment in ("readonly", "read-only", "disk", "unable to open")):
        return ResearchError("storage_io", "SQLite could not access the research store")
    return ResearchError("corrupt_state", "Research SQLite state is malformed or inconsistent")


def _event_value(row):
    return {"revision": row["revision"], "request_id": row["request_id"],
            "operation": row["operation"], "payload": _load(row["payload"], dict),
            "changes": _load(row["changes"], list), "result": _load(row["result"])}


def _request_row(connection: sqlite3.Connection, request_id: str):
    return connection.execute(
        "SELECT events.operation, events.payload, receipts.response FROM receipts "
        "JOIN events ON events.revision = receipts.revision WHERE receipts.request_id = ?",
        (request_id,)).fetchone()


def _read_original_response(original, operation, encoded_payload, request_id):
    if original is None:
        return None
    if original["operation"] != operation or original["payload"] != encoded_payload:
        raise ResearchError("request_id_conflict", "Request ID was already committed with different inputs",
                            {"request_id": request_id})
    return _load(original["response"], dict)


def _encode_mutation_input(operation, payload, apply, expected_revision, request_id):
    _text(operation, "Operation")
    _text(request_id, "Request ID")
    if not isinstance(payload, dict) or not callable(apply):
        raise ResearchError("invalid_input", "Mutation needs a JSON payload object and a callable transaction")
    if type(expected_revision) is not int or not 0 <= expected_revision < _MAX_REVISION:
        raise ResearchError("invalid_input", "Expected revision must be a nonnegative SQLite integer")
    return _canonical(payload)


def _commit_mutation(connection, transaction, operation, encoded_payload, encoded_result, revision, request_id):
    """Append the event and receipt, then advance the revision in the same transaction."""
    next_revision = revision + 1
    response = {"revision": next_revision, "request_id": request_id, "result": json.loads(encoded_result)}
    event = {"revision": next_revision, "request_id": request_id, "operation": operation,
             "payload": json.loads(encoded_payload), "changes": transaction._changes,
             "result": response["result"]}
    connection.execute(
        "INSERT INTO events (revision, request_id, operation, payload, changes, result, digest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (next_revision, request_id, operation, encoded_payload, _canonical(transaction._changes),
         encoded_result, _digest(_canonical(event))))
    encoded_response = _canonical(response)
    connection.execute("INSERT INTO receipts (request_id, revision, response, digest) VALUES (?, ?, ?, ?)",
                       (request_id, next_revision, encoded_response, _digest(encoded_response)))
    changed = connection.execute("UPDATE metadata SET revision = ? WHERE id = 1 AND revision = ?",
                                 (next_revision, revision)).rowcount
    if changed != 1:
        raise ResearchError("corrupt_state", "Research revision changed during the transaction")
    return response


def _encode_record_change(key, value):
    _text(key, "Record key")
    if not isinstance(value, dict):
        raise ResearchError("invalid_input", "Record value must be a JSON object")
    _check_stored_bounds(value)
    return _canonical(value)


def _validate(connection: sqlite3.Connection, *, replay: bool = True,
              records: Optional[dict] = None, history=None, fingerprint=None, requests=None) -> int:
    """Check the store's structure, and with replay, its complete history.

Construction checks the structure so unsupported, corrupt or hot stores fail
before use. Every read, write and receipt lookup replays the history, so a
process validates it once per use and never trusts a cached replay.
An optional snapshot mapping receives current records only after their checks;
the caller must discard it if any validation fails.
"""
    metadata = connection.execute("SELECT id, schema_version, revision FROM metadata").fetchall()
    if len(metadata) != 1 or metadata[0]["id"] != 1:
        raise ResearchError("corrupt_state", "Research metadata must contain exactly one row")
    version, revision = metadata[0]["schema_version"], metadata[0]["revision"]
    if type(version) is not int or type(revision) is not int or revision < 0:
        raise ResearchError("corrupt_state", "Research schema version or revision is malformed")
    if version != _SCHEMA_VERSION:
        raise ResearchError("unsupported_schema", "Research schema version is unsupported",
                            {"schema_version": version, "supported_version": _SCHEMA_VERSION})
    schema = {(row["type"], row["name"]): row["sql"] for row in connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL")}
    if schema != _SCHEMA:
        raise ResearchError("corrupt_state", "Research database schema or history guards were changed")
    if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
        raise ResearchError("corrupt_state", "Research store must use rollback journaling")
    if not replay:
        return revision
    if [row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]:
        raise ResearchError("corrupt_state", "Research SQLite integrity check failed")
    if fingerprint is not None:
        _fingerprint_entry(fingerprint, ("metadata", version, revision))

    projections = {}
    event_count = 0
    for row in connection.execute("SELECT * FROM events ORDER BY revision"):
        event_count += 1
        if row["revision"] != event_count:
            raise ResearchError("corrupt_state", "Research event revisions are not contiguous")
        _text(row["request_id"], "Stored request ID", "corrupt_state")
        _text(row["operation"], "Stored operation", "corrupt_state")
        event = _event_value(row)
        if _digest(_encode_json(event, "corrupt_state")) != row["digest"]:
            raise ResearchError("corrupt_state", "Research event digest does not match its original values")
        for change in event["changes"]:
            if not isinstance(change, dict) or set(change) != {"kind", "key", "value"} or not isinstance(change["value"], dict):
                raise ResearchError("corrupt_state", "Research event change is malformed")
            _text(change["kind"], "Stored record kind", "corrupt_state")
            _text(change["key"], "Stored record key", "corrupt_state")
            encoded = _encode_json(change["value"], "corrupt_state")
            identity = _digest(encoded)
            projections[(change["kind"], change["key"])] = (encoded, identity)
            if history is not None:
                history.record_version(change["kind"], change["key"], event_count, encoded, identity)
            del change
        receipt = connection.execute("SELECT * FROM receipts WHERE request_id = ?",
                                     (row["request_id"],)).fetchone()
        expected_response = {"revision": row["revision"], "request_id": row["request_id"],
                             "result": event["result"]}
        if (receipt is None or receipt["revision"] != row["revision"]
                or receipt["response"] != _encode_json(expected_response, "corrupt_state")
                or _digest(receipt["response"]) != receipt["digest"]):
            raise ResearchError("corrupt_state", "Research request receipt does not match its original event")
        if fingerprint is not None:
            _fingerprint_entry(fingerprint, ("event", row["revision"], row["digest"], receipt["digest"]))
        if requests is not None:
            requests.add(row["request_id"])
        # Release the completed event before decoding another event or the current records.
        del event, expected_response, receipt, row
    if event_count != revision or connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] != revision:
        raise ResearchError("corrupt_state", "Research revision, events, and receipts disagree")
    record_count = 0
    for row in connection.execute("SELECT kind, key, value, digest FROM records ORDER BY kind, key"):
        record_count += 1
        value = _load(row["value"], dict)
        if projections.get((row["kind"], row["key"])) != (row["value"], row["digest"]):
            raise ResearchError("corrupt_state", "Research record differs from its committed history")
        if fingerprint is not None:
            _fingerprint_entry(fingerprint, ("record", row["kind"], row["key"], row["digest"]))
        if records is not None:
            records.setdefault(row["kind"], {})[row["key"]] = value
    if record_count != len(projections):
        raise ResearchError("corrupt_state", "Research records are missing committed values")
    return revision


def _read_event_digests(connection, revisions):
    """{revision: the digest of its event}, with None for revision 0, which has no event."""
    digests = {revision: None for revision in revisions}
    for row in connection.execute("SELECT revision, digest FROM events WHERE revision IN (" + ", ".join(
            "?" for _ in revisions) + ")", tuple(revisions)):
        digests[row["revision"]] = row["digest"]
    return digests


def _fingerprint_entry(fingerprint, entry):
    """Hash checked metadata only, without traversing expanded JSON values again."""
    fingerprint.update((_canonical(entry) + "\n").encode("utf-8"))


class _RecordHistory:
    """An immutable-after-validation index shared by one snapshot's copies and views."""

    def __init__(self):
        self._versions, self._digests = {}, {}

    def __deepcopy__(self, memo):
        return self

    def record_version(self, kind, key, revision, encoded, identity):
        previous = self._digests.get((kind, key, identity))
        content = previous[2] if previous is not None else zlib.compress(encoded.encode("utf-8"), level=1)
        entry = (revision, identity, content)
        self._versions.setdefault(kind, {}).setdefault(key, []).append(entry)
        self._digests.setdefault((kind, key, identity), entry)

    def resolve_record(self, kind, key, revision, recorded_digest=None):
        if recorded_digest is not None:
            entry = self._digests.get((kind, key, recorded_digest))
            return None if entry is None or entry[0] > revision else entry
        versions = self._versions.get(kind, {}).get(key, ())
        index = bisect_right(versions, (revision + 1,)) - 1
        if index < 0:
            raise KeyError(key)
        return versions[index]

    def read_record(self, kind, key, revision, recorded_digest=None):
        entry = self.resolve_record(kind, key, revision, recorded_digest)
        return None if entry is None else json.loads(zlib.decompress(entry[2]))


class _HistoricalReadCache:
    """Borrow checked current objects and retain only one revision's decoded changes.

    An Evaluation freezes its records for its lifetime. Public mutable storage
    reads do not use this cache; a fresh root Evaluation checks current values
    again even when it shares the same authenticated event index.
    """

    def __init__(self, records):
        self._history = records._history
        self._current = records if records._current_source is None else records._current_source
        self._revision, self._values, self._verified_current = None, {}, {}

    def read_record(self, kind, key, revision, recorded_digest=None):
        entry = self._history.resolve_record(kind, key, revision, recorded_digest)
        if entry is None:
            return None
        if self._revision != revision:
            self._values.clear()
            self._revision = revision
        identity = (kind, key, entry[1])
        latest = self._history._versions[kind][key][-1]
        if entry[1] == latest[1]:
            if identity not in self._verified_current:
                current = self._current.get(kind, {}).get(key)
                self._verified_current[identity] = (current if current is not None
                    and _digest(_canonical(current)) == entry[1] else None)
            current = self._verified_current[identity]
            if current is not None:
                return current
        if identity not in self._values:
            self._values[identity] = json.loads(zlib.decompress(entry[2]))
        return self._values[identity]


class _HistoricalKind(Mapping):
    def __init__(self, snapshot, kind):
        self._snapshot, self._kind = snapshot, kind
        self._history, self._revision = snapshot._history, snapshot._revision

    def __getitem__(self, key):
        read = self._history.read_record if self._snapshot._cache is None else self._snapshot._cache.read_record
        return read(self._kind, key, self._revision)

    def __iter__(self):
        return (key for key in sorted(self._history._versions[self._kind])
                if self._history._versions[self._kind][key][0][0] <= self._revision)

    def __len__(self):
        return sum(1 for _ in self)


class _HistoricalSnapshot(Mapping):
    """A revision view; no complete historical records map is materialized or cached."""

    def __init__(self, history, revision, cache=None, current_source=None):
        self._history, self._revision, self._cache = history, revision, cache
        self._current_source = current_source

    def __getitem__(self, kind):
        versions = self._history._versions.get(kind, {})
        if not any(items[0][0] <= self._revision for items in versions.values()):
            raise KeyError(kind)
        return _HistoricalKind(self, kind)

    def __iter__(self):
        return (kind for kind in sorted(self._history._versions) if self.get(kind))

    def __len__(self):
        return sum(1 for _ in self)

    def copy(self):
        """Create a mutable overlay while sharing the lazy kinds and revision bound."""
        return _RecordedSnapshot(self, history=self._history, revision=self._revision, current_source=self._current_source)

    def read_revision(self):
        return self._revision

    def read_record(self, kind, key, recorded_digest):
        read = self._history.read_record if self._cache is None else self._cache.read_record
        return read(kind, key, self._revision, recorded_digest)

    def read_snapshot(self, revision, *, cache=None):
        if type(revision) is not int or not 0 <= revision <= self._revision:
            raise ResearchError("claim_reassessment_required", "The recorded input snapshot is unavailable; reassess the bound record.",
                                {"record_id": "snapshot:" + str(revision)})
        selected = self._cache if cache is None else cache
        if selected is not None and selected._history is not self._history:
            raise ResearchError("claim_reassessment_required", "The historical cache belongs to another snapshot.",
                                {"record_id": "snapshot:" + str(revision)})
        if selected is not None and selected._revision != revision:
            selected._values.clear()
            selected._revision = revision
        return _HistoricalSnapshot(self._history, revision, selected, self._current_source)

    def verify_record_version(self, kind, key, revision, identity):
        if type(revision) is not int or not 0 <= revision <= self._revision:
            return False
        try:
            return self._history.resolve_record(kind, key, revision)[1] == identity
        except KeyError:
            return False


class _RecordedSnapshot(dict):
    """Current JSON records or a shallow historical overlay with authenticated inputs.

    Only managed research retains indexed history. Copies share its immutable
    compressed values; historical reads require no connection or full replay.
    """

    def __init__(self, records=(), *, history, revision, current_source=None):
        super().__init__(records)
        self._history, self._revision = history, revision
        self._current_source = current_source

    def copy(self):
        return _RecordedSnapshot(self, history=self._history, revision=self._revision, current_source=self._current_source)

    def read_revision(self):
        return self._revision

    def read_record(self, kind, key, recorded_digest):
        return self._history.read_record(kind, key, self._revision, recorded_digest)

    def read_snapshot(self, revision, *, cache=None):
        source = self if self._current_source is None else self._current_source
        return _HistoricalSnapshot(self._history, self._revision, current_source=source).read_snapshot(revision, cache=cache)

    def verify_record_version(self, kind, key, revision, identity):
        return _HistoricalSnapshot(self._history, self._revision).verify_record_version(kind, key, revision, identity)


def _read_snapshot(connection: sqlite3.Connection, *, fingerprint=None, requests=None) -> dict:
    from .claim_dependencies import uses_claim_dependencies
    # This untrusted precheck controls allocation only. _validate authenticates
    # every event and current record before any history or snapshot is returned.
    markers = {}
    config = connection.execute("SELECT value FROM records WHERE kind = 'configuration' AND key = 'research'").fetchone()
    if config is not None:
        markers["configuration"] = {"research": _load(config["value"], dict)}
    for row in connection.execute("SELECT kind, key FROM records WHERE "
            "(kind = 'workspace' AND key IN ('study', 'draft')) OR kind = 'research_intent' LIMIT 1"):
        markers[row["kind"]] = {row["key"]: {}}
    records = {}
    history = _RecordHistory() if uses_claim_dependencies(markers) else None
    revision = _validate(connection, records=records, history=history, fingerprint=fingerprint, requests=requests)
    return {"revision": revision, "records": records if history is None else
            _RecordedSnapshot(records, history=history, revision=revision)}


class _PreparationKind(Mapping):
    def __init__(self, records):
        self._records = records

    def __getitem__(self, key):
        return copy.deepcopy(self._records[key])

    def __contains__(self, key):
        return key in self._records

    def __iter__(self):
        return iter(self._records)

    def __len__(self):
        return len(self._records)


class _PreparationRecords(Mapping):
    """Read-only kinds with defensive record reads, retaining checked history."""

    def __init__(self, records):
        self._records = records

    def __getitem__(self, kind):
        return _PreparationKind(self._records[kind])

    def __contains__(self, kind):
        return kind in self._records

    def __iter__(self):
        return iter(self._records)

    def __len__(self):
        return len(self._records)

    def __getattr__(self, name):
        if name in ("read_revision", "read_record", "read_snapshot", "verify_record_version",
                    "_history", "_current_source", "_cache"):
            return getattr(self._records, name)
        raise AttributeError(name)

    def copy(self):
        if isinstance(self._records, _RecordedSnapshot):
            return _RecordedSnapshot(self, history=self._records._history, revision=self._records.read_revision(),
                                     current_source=self._records)
        return dict(self)


class _PreparationProof(NamedTuple):
    """Immutable commit proof with no decoded records or Store reference."""

    identity: tuple
    revision: int
    fingerprint: bytes

    def matches_snapshot(self, identity, revision, fingerprint):
        return (self.identity == identity and self.revision == revision
                and self.fingerprint == fingerprint.digest())


class _PreparedSnapshot:
    """Own one freshly authenticated read and its nonpersistent content proof."""

    def __init__(self, store, request_id=None):
        self._store = store
        fingerprint, requests = hashlib.sha256(), set()
        with store._connection() as connection:
            identity = store._read_database_identity()
            snapshot = _read_snapshot(connection, fingerprint=fingerprint, requests=requests)
            self._original_request = _request_row(connection, request_id) if request_id is not None else None
            if identity != store._read_database_identity():
                raise _refuse_snapshot_identity(snapshot["revision"], snapshot["revision"])
        self._identity = identity
        self._fingerprint = fingerprint.digest()
        self._revision = snapshot["revision"]
        self._records = _PreparationRecords(snapshot["records"])
        self._requests = frozenset(requests)

    def read_revision(self):
        return self._revision

    def read_records(self):
        return self._records

    revision = property(read_revision)
    records = property(read_records)

    def has_request(self, request_id):
        return isinstance(request_id, str) and request_id in self._requests

    def release_proof(self):
        """Detach the decoded view from every holder before independent commit validation."""
        proof = _PreparationProof(self._identity, self.revision, self._fingerprint)
        self._records = None
        return proof


def _refuse_snapshot_identity(expected_revision, revision):
    return ResearchError("stale_revision", "Research snapshot identity changed; prepare the mutation again",
                         {"expected_revision": expected_revision, "revision": revision,
                          "reason": "snapshot_identity_mismatch"})


def _scoped_prefix(connection, revision):
    fingerprint = hashlib.sha256()
    for row in connection.execute("SELECT revision, digest FROM events WHERE revision <= ? ORDER BY revision",
                                  (revision,)):
        _fingerprint_entry(fingerprint, (row["revision"], row["digest"]))
    return fingerprint.digest()


class _ScopedProof(NamedTuple):
    """An evaluation's internally issued read and absence predicates."""

    store: object
    identity: tuple
    revision: int
    prefix: bytes
    reads: tuple
    memberships: tuple
    epoch_tasks: tuple
    global_revision: bool


class _ScopedKind(Mapping):
    def __init__(self, scope, kind):
        self._scope, self._kind = scope, kind

    def __getitem__(self, key):
        self._scope.bind_record(self._kind, key)
        return copy.deepcopy(self._scope._source.get(self._kind, {})[key])

    def __contains__(self, key):
        self._scope.bind_record(self._kind, key)
        return key in self._scope._source.get(self._kind, {})

    def __iter__(self):
        return iter(self._scope.bind_membership(self._kind))

    def __len__(self):
        return len(self._scope.bind_membership(self._kind))


class _ScopedRecords(Mapping):
    def __init__(self, scope):
        self._scope = scope

    def __getitem__(self, kind):
        self._scope.check_open()
        if kind not in self._scope._source:
            self._scope.bind_membership(kind)
            raise KeyError(kind)
        return _ScopedKind(self._scope, kind)

    def get(self, kind, default=None):
        self._scope.check_open()
        # The ordinary records.get(kind, {}).get(key) idiom must bind a key's
        # absence even when no sibling has created its kind yet.
        if kind in self._scope._source or isinstance(default, Mapping) and not default:
            return _ScopedKind(self._scope, kind)
        self._scope.bind_membership(kind)
        return default

    def __contains__(self, kind):
        self._scope.bind_membership(kind)
        return kind in self._scope._source

    def __iter__(self):
        self._scope.check_open()
        self._scope._global_revision = True
        return iter(self._scope._source)

    def __len__(self):
        return sum(1 for _ in self)

    def copy(self):
        return {kind: dict(self[kind]) for kind in self}

    def read_revision(self):
        self._scope.check_open()
        self._scope._global_revision = True
        return self._scope.revision

    def read_epoch_tasks(self, epoch_id):
        keys = self._scope.bind_epoch_tasks(epoch_id)
        return {key: self['review_task'][key] for key in keys}


def read_epoch_tasks(records, epoch_id):
    """Read one epoch's task index, binding only its membership in scoped views."""
    _text(epoch_id, "Epoch ID")
    read = getattr(records, 'read_epoch_tasks', None)
    if read is not None:
        return read(epoch_id)
    return {key: row for key, row in records.get('review_task', {}).items()
            if row.get('epoch_id') == epoch_id}


class _ScopedPreparation:
    """Track reads from a fresh authenticated snapshot; never trust caller maps."""

    def __init__(self, store):
        self._store = store
        requests = set()
        with store._connection() as connection:
            self._identity = store._read_database_identity()
            snapshot = _read_snapshot(connection, requests=requests)
            self._revision = snapshot["revision"]
            self._prefix = _scoped_prefix(connection, self._revision)
            if self._identity != store._read_database_identity():
                raise _refuse_snapshot_identity(self._revision, self._revision)
        self._source = snapshot["records"]
        self._records = _ScopedRecords(self)
        self._reads, self._memberships, self._epoch_tasks = {}, {}, {}
        self._requests = frozenset(requests)
        self._global_revision = False

    revision = property(lambda self: self._revision)
    records = property(lambda self: self._records)

    def check_open(self):
        if self._records is None:
            raise ResearchError("invalid_preparation", "Task preparation has already been released")

    def has_request(self, request_id):
        return request_id in self._requests

    def bind_record(self, kind, key):
        self.check_open()
        _text(kind, "Record kind")
        _text(key, "Record key")
        identity = (kind, key)
        if identity not in self._reads:
            value = self._source.get(kind, {}).get(key)
            self._reads[identity] = None if value is None else _digest(_canonical(value))

    def bind_membership(self, kind):
        self.check_open()
        _text(kind, "Record kind")
        if kind not in self._memberships:
            self._memberships[kind] = tuple(sorted(self._source.get(kind, {})))
        return self._memberships[kind]

    def bind_epoch_tasks(self, epoch_id):
        self.check_open()
        _text(epoch_id, "Epoch ID")
        if epoch_id not in self._epoch_tasks:
            self._epoch_tasks[epoch_id] = tuple(sorted(read_epoch_tasks(self._source, epoch_id)))
        return self._epoch_tasks[epoch_id]

    def release_proof(self):
        self.check_open()
        proof = _ScopedProof(self._store, self._identity, self.revision, self._prefix,
                             tuple((kind, key, value) for (kind, key), value in sorted(self._reads.items())),
                             tuple(sorted(self._memberships.items())), tuple(sorted(self._epoch_tasks.items())),
                             self._global_revision)
        self._records = self._source = None
        return proof


def _revalidate_scoped(store, connection, proof, revision):
    if not isinstance(proof, _ScopedProof) or proof.store is not store:
        raise ResearchError("invalid_preparation", "Scoped writes require this Store's internally owned preparation")
    if (proof.identity != store._read_database_identity() or revision < proof.revision
            or proof.prefix != _scoped_prefix(connection, proof.revision)):
        raise _refuse_snapshot_identity(proof.revision, revision)
    changed, task_epochs = set(), {}
    for row in connection.execute("SELECT changes FROM events WHERE revision > ? ORDER BY revision", (proof.revision,)):
        for item in _load(row["changes"], list):
            changed.add((item["kind"], item["key"]))
            if item['kind'] == 'review_task':
                task_epochs[item['key']] = item['value'].get('epoch_id')
    invalid = []
    for kind, key, identity in proof.reads:
        row = connection.execute("SELECT digest FROM records WHERE kind = ? AND key = ?", (kind, key)).fetchone()
        if (kind, key) in changed or (None if row is None else row["digest"]) != identity:
            invalid.append({"kind": kind, "key": key})
    for kind, keys in proof.memberships:
        current = tuple(row["key"] for row in connection.execute(
            "SELECT key FROM records WHERE kind = ? ORDER BY key", (kind,)))
        if current != keys:
            invalid.append({"kind": kind, "predicate": "membership"})
    for epoch_id, keys in proof.epoch_tasks:
        # The authenticated append-only suffix contains every membership change.
        # Apply its task moves without requiring optional SQLite JSON functions.
        current = set(keys)
        for key, task_epoch in task_epochs.items():
            if task_epoch == epoch_id:
                current.add(key)
            else:
                current.discard(key)
        if current != set(keys):
            invalid.append({'kind': 'review_task', 'epoch_id': epoch_id, 'predicate': 'membership'})
    if invalid or proof.global_revision and proof.revision != revision:
        raise ResearchError("review_task_dependency_changed", "Task dependencies changed; prepare acceptance again",
                            {"dependencies": invalid, "prepared_revision": proof.revision, "revision": revision})


class _GuardedSnapshot:
    """An isolated snapshot usable only while its read transaction is held."""

    def __init__(self, root: Path, value: dict):
        self.root = root
        self._value = value

    def snapshot(self) -> dict:
        if self._value is None:
            raise ResearchError("invalid_snapshot_guard", "The guarded research read has finished")
        return copy.deepcopy(self._value)

    def build_artifact_store(self):
        return ArtifactStore(self.root)


class Transaction:
    """A callback's isolated record view, usable only during Store.mutate."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._changes = []
        self._active = True

    def _check(self, kind):
        if not self._active:
            raise ResearchError("transaction_closed", "Research transaction callback has finished")
        _text(kind, "Record kind")

    def get(self, kind: str, key: str) -> Optional[dict]:
        self._check(kind)
        _text(key, "Record key")
        row = self._connection.execute("SELECT value FROM records WHERE kind = ? AND key = ?",
                                       (kind, key)).fetchone()
        return None if row is None else _load(row["value"], dict)

    def records(self, kind: str) -> dict:
        self._check(kind)
        return {row["key"]: _load(row["value"], dict) for row in self._connection.execute(
            "SELECT key, value FROM records WHERE kind = ? ORDER BY key", (kind,))}

    def put(self, kind: str, key: str, value: dict) -> None:
        self._check(kind)
        encoded = _encode_record_change(key, value)
        self._connection.execute("INSERT OR REPLACE INTO records (kind, key, value, digest) VALUES (?, ?, ?, ?)",
                                 (kind, key, encoded, _digest(encoded)))
        self._changes.append({"kind": kind, "key": key, "value": json.loads(encoded)})


class Store:
    """A durable research store at root/.exactory/research.sqlite3."""

    def __init__(self, root: Path, create: bool = False):
        self._workspace = _Workspace(root)
        self.root = self._workspace.root
        self._path = self.root / ".exactory" / _DATABASE
        if create:
            with self._workspace.directory(".exactory", create=True) as directory:
                with self._locked(directory):
                    self._create(directory)
        with self._connection(writable=create) as connection:
            _validate(connection, replay=False)

    @contextmanager
    def _locked(self, directory: int):
        # A raw descriptor close can release another connection's POSIX locks.
        # Identify the checked directory on every access, so aliases coordinate
        # and a replaced directory never inherits a stale cached lock identity.
        info = os.fstat(directory)
        identity = (info.st_dev, info.st_ino)
        with _LOCK_REGISTRY_GUARD:
            mutex = _WORKSPACE_LOCKS.get(identity)
            if mutex is None:
                mutex = threading.Lock()
                _WORKSPACE_LOCKS[identity] = mutex
        if not mutex.acquire(timeout=_BUSY_TIMEOUT_SECONDS):
            raise ResearchError("store_busy", "Research store is active in this process; "
                                "retry after it finishes and use Transaction methods inside callbacks")
        try:
            yield
        finally:
            mutex.release()

    def _check_links(self, directory: int, info, *, database: bool):
        if info.st_nlink == 1:
            return
        if database and info.st_nlink == 2:
            # Atomic publication briefly gives the initialized database two names.
            # Both links must be in this directory and identify the same inode.
            for name in os.listdir(directory):
                if _PUBLICATION_NAME.fullmatch(name):
                    try:
                        temporary = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if (stat.S_ISREG(temporary.st_mode)
                            and (temporary.st_dev, temporary.st_ino) == (info.st_dev, info.st_ino)):
                        return
            # The publisher may have removed its temporary link during inspection.
            current = os.stat(_DATABASE, dir_fd=directory, follow_symlinks=False)
            if (current.st_nlink == 1
                    and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)):
                return
        raise ResearchError("unsafe_path", "SQLite files must not have aliases outside atomic workspace publication")

    def _check_files(self, directory: int, *, required: bool = True):
        self._check_path(self._path.parent)
        opened = os.fstat(directory)
        current = os.stat(self._path.parent, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ResearchError("unsafe_path", "Research metadata directory changed during access")
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                info = os.stat(_DATABASE + suffix, dir_fd=directory, follow_symlinks=False)
                self._check_links(directory, info, database=not suffix)
                descriptor = _regular_file(directory, _DATABASE + suffix)
            except FileNotFoundError:
                if not suffix and required:
                    raise ResearchError("store_missing", "Research store does not exist; initialize or adopt the workspace")
                continue
            with os.fdopen(descriptor, "rb") as source:
                if not suffix:
                    header = source.read(100)
                    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
                        raise ResearchError("corrupt_state", "Research database has an invalid SQLite header")
                    if header[18:20] != b"\x01\x01":
                        raise ResearchError("corrupt_state", "Research store must use rollback journaling")

    def _check_path(self, path: Path):
        try:
            resolved = path.resolve(strict=True)
        except RuntimeError as error:
            raise ResearchError("unsafe_path", "SQLite paths must not contain symlink loops") from error
        if resolved != path:
            raise ResearchError("unsafe_path", "SQLite paths must not traverse symlinks")

    def _open(self, path: Path, *, writable: bool):
        self._check_path(path)
        connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if writable else "?mode=ro"),
                                     uri=True, isolation_level=None, timeout=_BUSY_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        try:
            self._check_path(path)
            actual_path = connection.execute("PRAGMA database_list").fetchone()["file"]
            if actual_path != str(path):
                raise ResearchError("unsafe_path", "SQLite opened a different workspace path")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA foreign_keys = ON")
            if not writable:
                connection.execute("PRAGMA query_only = ON")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _connection(self, *, writable: bool = False):
        caller_failure = None
        try:
            with self._workspace.directory(".exactory") as directory:
                with self._locked(directory):
                    connection = None
                    try:
                        self._check_files(directory)
                        connection = self._open(self._path, writable=writable)
                        # Recheck managed names before SQLite can mutate.
                        self._check_files(directory)
                        connection.execute("BEGIN IMMEDIATE" if writable else "BEGIN")
                        try:
                            try:
                                yield connection
                            except BaseException as error:
                                caller_failure = error
                                raise
                            connection.commit()
                        except BaseException:
                            connection.rollback()
                            raise
                    finally:
                        if connection is not None:
                            connection.close()
        except FileNotFoundError as error:
            if error is caller_failure:
                raise
            raise ResearchError("store_missing", "Research store does not exist; initialize or adopt the workspace") from error
        except OSError as error:
            if error is caller_failure:
                raise
            raise _filesystem_error(error) from error
        except sqlite3.Error as error:
            raise _sql_error(error, readonly=not writable) from error

    def _create(self, directory: int):
        try:
            self._check_files(directory, required=False)
            try:
                os.stat(_DATABASE, dir_fd=directory, follow_symlinks=False)
                os.fsync(directory)
                return
            except FileNotFoundError:
                pass
            temporary = ".research-" + uuid.uuid4().hex + ".sqlite3"
            descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            os.close(descriptor)
            connection = None
            try:
                connection = self._open(self._path.parent / temporary, writable=True)
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA.values():
                    connection.execute(statement)
                connection.execute("INSERT INTO metadata (id, schema_version, revision) VALUES (1, ?, 0)",
                                   (_SCHEMA_VERSION,))
                connection.commit()
                connection.close()
                connection = None
                descriptor = _regular_file(directory, temporary)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                try:
                    os.link(temporary, _DATABASE, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                except FileExistsError:
                    pass
            finally:
                if connection is not None:
                    connection.close()
                os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        except sqlite3.Error as error:
            raise _sql_error(error) from error
        except OSError as error:
            raise _filesystem_error(error) from error

    @property
    def revision(self) -> int:
        with self._connection() as connection:
            return _validate(connection)

    def snapshot(self) -> dict:
        with self._connection() as connection:
            return _read_snapshot(connection)

    def _read_database_identity(self):
        identities = []
        for path in (self.root, self._path.parent, self._path):
            info = os.stat(path, follow_symlinks=False)
            identities.append((info.st_dev, info.st_ino))
        return (str(self.root), *identities)

    def _read_preparation(self, request_id=None):
        return _PreparedSnapshot(self, request_id)

    def _read_scoped_preparation(self):
        return _ScopedPreparation(self)

    def build_artifact_store(self):
        return ArtifactStore(self.root)

    @contextmanager
    def guarded_snapshot(self):
        """Prevent common commits until the consumer releases this snapshot."""
        with self._connection() as connection:
            guard = _GuardedSnapshot(self.root, _read_snapshot(connection))
            try:
                yield guard
            finally:
                guard._value = None

    def read_changes(self, after_revision: int):
        """(revision, {after_revision: its event digest, revision: its event digest}, the kinds that the events after
        after_revision wrote), for the generated output views; revision 0 has no event and the digest None. These reads
        check the store's structure and skip the replay of its history: a view decides nothing, and every
        authoritative read still replays the history."""
        with self._connection() as connection:
            revision = _validate(connection, replay=False)
            kinds = set()
            for row in connection.execute("SELECT changes FROM events WHERE revision > ? ORDER BY revision",
                                          (after_revision,)):
                kinds.update(change["kind"] for change in _load(row["changes"], list))
            return revision, _read_event_digests(connection, (after_revision, revision)), kinds

    def read_kinds(self, kinds):
        """(revision, the digest of its event, {kind: {key: value}}) for the current records of kinds, from one read,
        with the checks of read_changes."""
        with self._connection() as connection:
            revision = _validate(connection, replay=False)
            records = {kind: {row["key"]: _load(row["value"], dict) for row in connection.execute(
                "SELECT key, value FROM records WHERE kind = ? ORDER BY key", (kind,))} for kind in kinds}
            return revision, _read_event_digests(connection, (revision,))[revision], records

    def committed_request(self, request_id: str) -> Optional[dict]:
        """Read a validated original request without retrying or changing it."""
        _text(request_id, "Request ID")
        with self._connection() as connection:
            _validate(connection)
            original = _request_row(connection, request_id)
            if original is None:
                return None
            return {"operation": original["operation"], "payload": _load(original["payload"], dict),
                    "response": _load(original["response"], dict)}

    def mutate(self, operation: str, payload: dict, apply, *, expected_revision: int, request_id: str,
               _preparation=None) -> dict:
        encoded_payload = _encode_mutation_input(operation, payload, apply, expected_revision, request_id)
        with self._connection(writable=True) as connection:
            fingerprint = hashlib.sha256() if _preparation is not None else None
            identity = self._read_database_identity() if _preparation is not None else None
            revision = _validate(connection, fingerprint=fingerprint)
            original = _read_original_response(_request_row(connection, request_id), operation, encoded_payload,
                                               request_id)
            if original is not None:
                return original
            if revision != expected_revision:
                raise ResearchError("stale_revision", "Research state changed; read the current revision before a new mutation",
                                    {"expected_revision": expected_revision, "revision": revision})
            if _preparation is not None and (not isinstance(_preparation, _PreparationProof)
                    or identity != self._read_database_identity()
                    or not _preparation.matches_snapshot(identity, revision, fingerprint)):
                raise _refuse_snapshot_identity(expected_revision, revision)
            # A committed request returns above without this check, so a retry of a request that an earlier
            # release stored beyond the bounds still gets its original receipt.
            _check_stored_bounds(payload)
            transaction = Transaction(connection)
            try:
                result = apply(transaction)
                _check_stored_bounds(result)
                encoded_result = _canonical(result)
            finally:
                transaction._active = False
            return _commit_mutation(connection, transaction, operation, encoded_payload, encoded_result,
                                    revision, request_id)

    def mutate_scoped(self, operation, payload, apply, *, request_id, _preparation, expected_revision=None):
        """Revalidate an owned task proof, then apply(tx, actual_commit_revision)."""
        encoded_payload = _encode_mutation_input(operation, payload, apply,
                                                 0 if expected_revision is None else expected_revision, request_id)
        with self._connection(writable=True) as connection:
            revision = _validate(connection)
            original = _read_original_response(_request_row(connection, request_id), operation, encoded_payload, request_id)
            if original is not None:
                return original
            _revalidate_scoped(self, connection, _preparation, revision)
            if expected_revision is not None and expected_revision != _preparation.revision:
                raise ResearchError("stale_revision", "Prepare against the expected research revision",
                                    {"expected_revision": expected_revision, "revision": _preparation.revision})
            if revision >= _MAX_REVISION:
                raise ResearchError("invalid_input", "Research revision exhausted its SQLite integer range")
            _check_stored_bounds(payload)
            next_revision = revision + 1
            transaction = Transaction(connection)
            try:
                result = apply(transaction, next_revision)
                _check_stored_bounds(result)
                encoded_result = _canonical(result)
            finally:
                transaction._active = False
            return _commit_mutation(connection, transaction, operation, encoded_payload, encoded_result,
                                    revision, request_id)
