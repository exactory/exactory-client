"""Revisioned SQLite records, immutable event patches, and request receipts.

Every mutation holds a BEGIN IMMEDIATE transaction, checks its request receipt
before its expected revision, and commits records, original changes, the result,
and the next revision together. Connections are short-lived. Reads open in
SQLite read-only mode and never initialize or migrate storage.

The SQL metadata row holds schema_version and revision. Scientific configuration
is the ordinary configuration/research record, owned by domain services.
"""

import copy
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
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .artifacts import _Workspace, _regular_file
from .errors import ResearchError


_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_SECONDS = 1.0
_DATABASE = "research.sqlite3"
_MAX_REVISION = (1 << 63) - 1
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


def _canonical(value, code: str = "invalid_input") -> str:
    try:
        _json_types(value)
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":"))
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ResearchError(code, "Expected finite JSON data with string object keys") from error


def _digest(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load(encoded, expected_type=None):
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as error:
        raise ResearchError("corrupt_state", "Stored JSON is malformed") from error
    if (expected_type is not None and not isinstance(value, expected_type)) or _canonical(value, "corrupt_state") != encoded:
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


def _validate(connection: sqlite3.Connection) -> int:
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
    if [row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]:
        raise ResearchError("corrupt_state", "Research SQLite integrity check failed")
    if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
        raise ResearchError("corrupt_state", "Research store must use rollback journaling")

    projections = {}
    event_count = 0
    for row in connection.execute("SELECT * FROM events ORDER BY revision"):
        event_count += 1
        if row["revision"] != event_count:
            raise ResearchError("corrupt_state", "Research event revisions are not contiguous")
        _text(row["request_id"], "Stored request ID", "corrupt_state")
        _text(row["operation"], "Stored operation", "corrupt_state")
        event = _event_value(row)
        if _digest(_canonical(event, "corrupt_state")) != row["digest"]:
            raise ResearchError("corrupt_state", "Research event digest does not match its original values")
        for change in event["changes"]:
            if not isinstance(change, dict) or set(change) != {"kind", "key", "value"} or not isinstance(change["value"], dict):
                raise ResearchError("corrupt_state", "Research event change is malformed")
            _text(change["kind"], "Stored record kind", "corrupt_state")
            _text(change["key"], "Stored record key", "corrupt_state")
            encoded = _canonical(change["value"], "corrupt_state")
            projections[(change["kind"], change["key"])] = (encoded, _digest(encoded))
        receipt = connection.execute("SELECT * FROM receipts WHERE request_id = ?",
                                     (row["request_id"],)).fetchone()
        expected_response = {"revision": row["revision"], "request_id": row["request_id"],
                             "result": event["result"]}
        if (receipt is None or receipt["revision"] != row["revision"]
                or receipt["response"] != _canonical(expected_response, "corrupt_state")
                or _digest(receipt["response"]) != receipt["digest"]):
            raise ResearchError("corrupt_state", "Research request receipt does not match its original event")
    if event_count != revision or connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] != revision:
        raise ResearchError("corrupt_state", "Research revision, events, and receipts disagree")
    record_count = 0
    for row in connection.execute("SELECT kind, key, value, digest FROM records"):
        record_count += 1
        _load(row["value"], dict)
        if projections.get((row["kind"], row["key"])) != (row["value"], row["digest"]):
            raise ResearchError("corrupt_state", "Research record differs from its committed history")
    if record_count != len(projections):
        raise ResearchError("corrupt_state", "Research records are missing committed values")
    return revision


def _snapshot(connection: sqlite3.Connection) -> dict:
    revision = _validate(connection)
    records = {}
    for row in connection.execute("SELECT kind, key, value FROM records ORDER BY kind, key"):
        records.setdefault(row["kind"], {})[row["key"]] = _load(row["value"], dict)
    return {"revision": revision, "records": records}


class _GuardedSnapshot:
    """An isolated snapshot usable only while its read transaction is held."""

    def __init__(self, root: Path, value: dict):
        self.root = root
        self._value = value

    def snapshot(self) -> dict:
        if self._value is None:
            raise ResearchError("invalid_snapshot_guard", "The guarded research read has finished")
        return copy.deepcopy(self._value)


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
        _text(key, "Record key")
        if not isinstance(value, dict):
            raise ResearchError("invalid_input", "Record value must be a JSON object")
        encoded = _canonical(value)
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
            _validate(connection)

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
                            yield connection
                            connection.commit()
                        except BaseException:
                            connection.rollback()
                            raise
                    finally:
                        if connection is not None:
                            connection.close()
        except FileNotFoundError as error:
            raise ResearchError("store_missing", "Research store does not exist; initialize or adopt the workspace") from error
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

    @property
    def revision(self) -> int:
        with self._connection() as connection:
            return _validate(connection)

    def snapshot(self) -> dict:
        with self._connection() as connection:
            return _snapshot(connection)

    @contextmanager
    def guarded_snapshot(self):
        """Prevent common commits until the consumer releases this snapshot."""
        with self._connection() as connection:
            guard = _GuardedSnapshot(self.root, _snapshot(connection))
            try:
                yield guard
            finally:
                guard._value = None

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

    def mutate(self, operation: str, payload: dict, apply, *, expected_revision: int, request_id: str) -> dict:
        _text(operation, "Operation")
        _text(request_id, "Request ID")
        if not isinstance(payload, dict) or not callable(apply):
            raise ResearchError("invalid_input", "Mutation needs a JSON payload object and a callable transaction")
        if type(expected_revision) is not int or not 0 <= expected_revision < _MAX_REVISION:
            raise ResearchError("invalid_input", "Expected revision must be a nonnegative SQLite integer")
        encoded_payload = _canonical(payload)
        with self._connection(writable=True) as connection:
            revision = _validate(connection)
            original = _request_row(connection, request_id)
            if original is not None:
                if original["operation"] != operation or original["payload"] != encoded_payload:
                    raise ResearchError("request_id_conflict", "Request ID was already committed with different inputs",
                                        {"request_id": request_id})
                return _load(original["response"], dict)
            if revision != expected_revision:
                raise ResearchError("stale_revision", "Research state changed; read the current revision before a new mutation",
                                    {"expected_revision": expected_revision, "revision": revision})
            transaction = Transaction(connection)
            try:
                result = _canonical(apply(transaction))
            finally:
                transaction._active = False
            next_revision = revision + 1
            response = {"revision": next_revision, "request_id": request_id, "result": json.loads(result)}
            event = {"revision": next_revision, "request_id": request_id, "operation": operation,
                     "payload": json.loads(encoded_payload), "changes": transaction._changes,
                     "result": response["result"]}
            connection.execute(
                "INSERT INTO events (revision, request_id, operation, payload, changes, result, digest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (next_revision, request_id, operation, encoded_payload, _canonical(transaction._changes),
                 result, _digest(_canonical(event))))
            encoded_response = _canonical(response)
            connection.execute("INSERT INTO receipts (request_id, revision, response, digest) VALUES (?, ?, ?, ?)",
                               (request_id, next_revision, encoded_response, _digest(encoded_response)))
            changed = connection.execute("UPDATE metadata SET revision = ? WHERE id = 1 AND revision = ?",
                                         (next_revision, expected_revision)).rowcount
            if changed != 1:
                raise ResearchError("corrupt_state", "Research revision changed during the transaction")
            return response
