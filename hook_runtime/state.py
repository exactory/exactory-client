"""Atomic on-disk registry and session bindings, serialized across processes."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


class RuntimeRefusal(ValueError):
    """A managed runtime identity cannot be used safely."""


def validate_digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RuntimeRefusal("Invalid release identity")
    return value


def require_directory(path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RuntimeRefusal("Expected a real directory: " + str(path))


def read_json(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeRefusal("Expected a regular state file: " + str(path))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise RuntimeRefusal("Invalid JSON state: " + str(path)) from error


def write_json(path, value, mode=0o644):
    """Publish one complete JSON file; keep the previous file on interruption."""
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def lock_file(path):
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode) or not mode & 0o222:
            raise RuntimeRefusal("Invalid runtime lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def lock_registry(prefix):
    """Only administrator installation and publication acquire this lock."""
    require_directory(prefix / "state")
    with lock_file(prefix / "state/runtime.lock"):
        yield


def select_session_state(prefix, state_dir=None):
    if state_dir is None:
        state_dir = os.environ.get("EXACTORY_RUNTIME_STATE_DIR")
    if state_dir is None:
        state_dir = Path.home() / ".local/state/exactory-runtime"
    if not str(state_dir):
        raise RuntimeRefusal("Empty session state directory")
    directory = Path(state_dir).expanduser().absolute()
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(mode):
            raise RuntimeRefusal("Session state must not be a symlink")
    try:
        canonical = directory.resolve()
    except RuntimeError as error:
        # Python 3.9/3.12 report a symlink loop as RuntimeError, not OSError.
        raise RuntimeRefusal("Session state path cannot be resolved") from error
    prefix = Path(prefix).resolve()
    if canonical == prefix or prefix in canonical.parents:
        raise RuntimeRefusal("Session state must be outside the managed prefix")
    return directory


def require_writable_directory(path):
    require_directory(path)
    if not path.lstat().st_mode & 0o222 or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeRefusal("Session state directory is not writable: " + str(path))


def initialize_session_state(directory):
    """Explicit user setup; a hook never recreates missing session storage."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_writable_directory(directory)
    (directory / "sessions").mkdir(mode=0o700, exist_ok=True)
    require_writable_directory(directory / "sessions")
    descriptor = os.open(directory / "runtime.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)


@contextmanager
def lock_sessions(directory):
    require_writable_directory(directory)
    require_writable_directory(directory / "sessions")
    with lock_file(directory / "runtime.lock"):
        yield


def read_registry(prefix):
    registry = read_json(prefix / "state/registry.json")
    if (not isinstance(registry, dict) or set(registry) != {"schema", "approved", "default"}
            or registry["schema"] != 1 or not isinstance(registry["approved"], list)):
        raise RuntimeRefusal("Invalid approved-release registry")
    for digest in registry["approved"]:
        validate_digest(digest)
    if len(registry["approved"]) != len(set(registry["approved"])):
        raise RuntimeRefusal("Duplicate approved release")
    if registry["default"] is not None:
        validate_digest(registry["default"])
        if registry["default"] not in registry["approved"]:
            raise RuntimeRefusal("Default release is not approved")
    return registry


def build_binding_path(directory, session_id):
    if not isinstance(session_id, str) or not session_id or len(session_id) > 4096:
        raise RuntimeRefusal("Missing or invalid session identity")
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json"
    require_directory(directory / "sessions")
    return directory / "sessions" / name


def read_binding(directory, session_id):
    binding = read_json(build_binding_path(directory, session_id))
    if (not isinstance(binding, dict) or set(binding) != {"schema", "session_id", "release"}
            or binding["schema"] != 1 or binding["session_id"] != session_id):
        raise RuntimeRefusal("Invalid session binding")
    validate_digest(binding["release"])
    return binding
