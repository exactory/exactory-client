"""Crash-safe storage for search-controller events and immutable evidence."""

import contextlib
import copy
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Dict, Iterator, Optional

from .errors import SearchError

try:
    import fcntl
except ImportError:  # pragma: no cover - fcntl is present on supported POSIX hosts
    fcntl = None  # type: ignore


SCHEMA_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_RETRY_SECONDS = 0.05
ENVELOPE_FIELDS = {"schema_version", "objective_id", "contract", "events"}
EVENT_FIELDS = {"sequence", "request_id", "kind", "payload"}
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def canonical_bytes(value: Any) -> bytes:
    """Serialize a JSON value deterministically as UTF-8 bytes."""

    try:
        _validate_json_shape(value, set())
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return encoded.encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError, RecursionError) as error:
        raise SearchError(
            "invalid_json", "value is not finite canonical JSON", {"reason": str(error)}
        ) from error


def _validate_json_shape(value: Any, active_containers: set) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in active_containers:
            raise ValueError("circular JSON value")
        active_containers.add(identity)
        try:
            for item in value:
                _validate_json_shape(item, active_containers)
        finally:
            active_containers.remove(identity)
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in active_containers:
            raise ValueError("circular JSON value")
        active_containers.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                _validate_json_shape(item, active_containers)
        finally:
            active_containers.remove(identity)
        return
    raise TypeError("value contains a non-JSON type")


def safe_path(root: Path, relative: str) -> Path:
    """Return a path below root, rejecting absolute paths and symlink escapes."""

    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise SearchError("unsafe_path", "path must be a non-empty relative string")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise SearchError(
            "unsafe_path", "absolute paths and traversal components are not allowed"
        )
    root_path = Path(root)
    try:
        resolved_root = root_path.resolve(strict=False)
        candidate = root_path / relative_path
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as error:
        raise SearchError(
            "unsafe_path", "path does not remain inside the registered root"
        ) from error
    return candidate


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number: " + value)


def _unique_object(pairs: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _strict_json(raw: bytes, error_code: str) -> Any:
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as error:
        raise SearchError(
            error_code, "stored JSON is malformed", {"reason": str(error)}
        ) from error


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _validate_event(event: Any, expected_sequence: int) -> None:
    if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
        raise SearchError("corrupt_state", "event fields are malformed")
    sequence = event["sequence"]
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence != expected_sequence
    ):
        raise SearchError("corrupt_state", "event sequence is malformed")
    if not _is_nonempty_string(event["request_id"]):
        raise SearchError("corrupt_state", "event request_id is malformed")
    if not _is_nonempty_string(event["kind"]):
        raise SearchError("corrupt_state", "event kind is malformed")
    if not isinstance(event["payload"], dict):
        raise SearchError("corrupt_state", "event payload is malformed")
    try:
        canonical_bytes(event["payload"])
    except SearchError as error:
        raise SearchError("corrupt_state", "event payload is not valid JSON") from error


def _validate_document(document: Any) -> Dict[str, Any]:
    if not isinstance(document, dict) or set(document) != ENVELOPE_FIELDS:
        raise SearchError("corrupt_state", "controller envelope fields are malformed")
    version = document["schema_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != SCHEMA_VERSION
    ):
        raise SearchError("corrupt_state", "schema_version is unsupported")
    if not _is_nonempty_string(document["objective_id"]):
        raise SearchError("corrupt_state", "objective_id is malformed")
    if not isinstance(document["contract"], dict):
        raise SearchError("corrupt_state", "contract is malformed")
    events = document["events"]
    if not isinstance(events, list):
        raise SearchError("corrupt_state", "events is malformed")
    request_ids = set()
    for sequence, event in enumerate(events, start=1):
        _validate_event(event, sequence)
        request_id = event["request_id"]
        if request_id in request_ids:
            raise SearchError("corrupt_state", "event request_id is not unique")
        request_ids.add(request_id)
    try:
        canonical_bytes(document)
    except SearchError as error:
        raise SearchError("corrupt_state", "document is not valid JSON") from error
    return document


class Store:
    """One authoritative event document and its immutable content stores."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def tree_path(self) -> Path:
        return self.root / "tree.json"

    @property
    def lock_path(self) -> Path:
        return self.root / "tree.lock"

    def initialize(self, contract: Any, objective_id: str) -> Dict[str, Any]:
        if not _is_nonempty_string(objective_id) or not isinstance(contract, dict):
            raise SearchError(
                "invalid_input", "objective_id and contract must be non-empty ID and object"
            )
        canonical_bytes(contract)
        self._prepare_root()
        candidate = {
            "schema_version": SCHEMA_VERSION,
            "objective_id": objective_id,
            "contract": contract,
            "events": [],
        }
        with self._writer_lock():
            if self.tree_path.exists() or self.tree_path.is_symlink():
                current = self._read_document()
                if canonical_bytes(current) == canonical_bytes(candidate):
                    return current
                raise SearchError(
                    "already_initialized",
                    "controller root is already initialized with different data",
                )
            self._atomic_replace(self.tree_path, canonical_bytes(candidate))
        return self._read_document()

    def read(self) -> Dict[str, Any]:
        self._check_root()
        return self._read_document()

    def append(
        self,
        kind: str,
        payload: Dict[str, Any],
        expected_revision: int,
        request_id: str,
        validate: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        if (
            not _is_nonempty_string(kind)
            or not _is_nonempty_string(request_id)
            or not isinstance(payload, dict)
            or not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
            or (validate is not None and not callable(validate))
        ):
            raise SearchError("invalid_input", "append inputs are malformed")
        payload_bytes = canonical_bytes(payload)
        self._check_root()
        with self._writer_lock():
            document = self._read_document()
            for existing in document["events"]:
                if existing["request_id"] != request_id:
                    continue
                identical = existing["kind"] == kind and canonical_bytes(
                    existing["payload"]
                ) == payload_bytes
                if identical:
                    return existing
                raise SearchError(
                    "request_id_conflict",
                    "request_id was already used with different inputs",
                    {"request_id": request_id},
                )
            revision = len(document["events"])
            if expected_revision != revision:
                raise SearchError(
                    "revision_conflict",
                    "expected revision does not match committed revision",
                    {"expected": expected_revision, "actual": revision},
                )
            candidate = {
                "sequence": revision + 1,
                "request_id": request_id,
                "kind": kind,
                "payload": _strict_json(payload_bytes, "invalid_json"),
            }
            if validate is not None:
                validate(copy.deepcopy(document), copy.deepcopy(candidate))
            _validate_event(candidate, revision + 1)
            new_document = {
                "schema_version": document["schema_version"],
                "objective_id": document["objective_id"],
                "contract": document["contract"],
                "events": document["events"] + [candidate],
            }
            _validate_document(new_document)
            self._atomic_replace(self.tree_path, canonical_bytes(new_document))
            return candidate

    def put_blob(self, value: Any) -> str:
        data = canonical_bytes(value)
        digest = hashlib.sha256(data).hexdigest()
        self._check_root()
        with self._writer_lock():
            self._read_document()
            path = self._content_path("blobs", digest + ".json")
            self._write_immutable(path, data)
        return digest

    def get_blob(self, digest: str) -> Any:
        self._check_root()
        self._validate_digest(digest)
        path = self._content_path("blobs", digest + ".json")
        raw = self._read_content(path, "corrupt_blob")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise SearchError("corrupt_blob", "blob digest does not match its name")
        value = _strict_json(raw, "corrupt_blob")
        try:
            canonical = canonical_bytes(value)
        except SearchError as error:
            raise SearchError("corrupt_blob", "blob is not canonical JSON") from error
        if canonical != raw:
            raise SearchError("corrupt_blob", "blob is not canonical JSON")
        return value

    def put_artifact(self, content: bytes) -> str:
        if not isinstance(content, bytes):
            raise SearchError("invalid_input", "artifact content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        self._check_root()
        with self._writer_lock():
            self._read_document()
            path = self._content_path("artifacts", digest)
            self._write_immutable(path, content)
        return digest

    def get_artifact(self, digest: str) -> bytes:
        self._check_root()
        self._validate_digest(digest)
        path = self._content_path("artifacts", digest)
        raw = self._read_content(path, "corrupt_artifact")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise SearchError(
                "corrupt_artifact", "artifact digest does not match its name"
            )
        return raw

    def _prepare_root(self) -> None:
        if self.root.is_symlink():
            raise SearchError("unsafe_path", "controller directory cannot be a symlink")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SearchError(
                "storage_error", "cannot create controller directory", {"reason": str(error)}
            ) from error
        self._check_root()
        self._ensure_directory("blobs")
        self._ensure_directory("artifacts")

    def _check_root(self) -> None:
        if self.root.is_symlink() or not self.root.is_dir():
            raise SearchError(
                "unsafe_path", "controller directory must be a real directory"
            )

    def _ensure_directory(self, relative: str) -> Path:
        path = safe_path(self.root, relative)
        if path.is_symlink():
            raise SearchError("unsafe_path", "controller subdirectory cannot be a symlink")
        path.mkdir(exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise SearchError("unsafe_path", "controller subdirectory is not safe")
        return path

    def _content_path(self, directory: str, filename: str) -> Path:
        parent = self._ensure_directory(directory)
        path = safe_path(self.root, directory + "/" + filename)
        if path.is_symlink():
            raise SearchError("unsafe_path", "content path cannot be a symlink")
        if path.parent != parent:
            raise SearchError("unsafe_path", "content path is outside its namespace")
        return path

    def _read_document(self) -> Dict[str, Any]:
        path = safe_path(self.root, "tree.json")
        if path.is_symlink():
            raise SearchError("unsafe_path", "tree.json cannot be a symlink")
        try:
            raw = path.read_bytes()
        except FileNotFoundError as error:
            raise SearchError("not_initialized", "controller is not initialized") from error
        except OSError as error:
            raise SearchError(
                "corrupt_state", "controller document cannot be read", {"reason": str(error)}
            ) from error
        document = _strict_json(raw, "corrupt_state")
        return _validate_document(document)

    @contextlib.contextmanager
    def _writer_lock(self) -> Iterator[None]:
        self._check_root()
        lock_path = safe_path(self.root, "tree.lock")
        if lock_path.is_symlink():
            raise SearchError("unsafe_path", "tree.lock cannot be a symlink")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(str(lock_path), flags, 0o600)
        except OSError as error:
            raise SearchError(
                "storage_error", "cannot open writer lock", {"reason": str(error)}
            ) from error
        try:
            if fcntl is not None:
                deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN):
                            raise
                        if time.monotonic() >= deadline:
                            raise SearchError(
                                "lock_timeout", "writer lock acquisition timed out"
                            ) from error
                        time.sleep(LOCK_RETRY_SECONDS)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)

    def _atomic_replace(self, target: Path, content: bytes) -> None:
        if target.is_symlink():
            raise SearchError("unsafe_path", "controller file cannot be a symlink")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary_path), str(target))
            self._fsync_directory(target.parent)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def _write_immutable(self, target: Path, content: bytes) -> None:
        if target.is_symlink():
            raise SearchError("unsafe_path", "immutable content cannot be a symlink")
        if target.exists():
            existing = self._read_content(target, "immutable_collision")
            if existing == content:
                return
            raise SearchError(
                "immutable_collision", "digest path already contains different bytes"
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(str(temporary_path), str(target))
            except FileExistsError:
                existing = self._read_content(target, "immutable_collision")
                if existing != content:
                    raise SearchError(
                        "immutable_collision",
                        "digest path already contains different bytes",
                    )
            self._fsync_directory(target.parent)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _read_content(path: Path, error_code: str) -> bytes:
        if path.is_symlink():
            raise SearchError("unsafe_path", "content path cannot be a symlink")
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError) as error:
            raise SearchError(
                error_code, "stored content cannot be read", {"reason": str(error)}
            ) from error

    @staticmethod
    def _validate_digest(digest: str) -> None:
        if not isinstance(digest, str) or DIGEST_PATTERN.fullmatch(digest) is None:
            raise SearchError("invalid_digest", "digest must be 64 lowercase hex digits")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(str(directory), flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
