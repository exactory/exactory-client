"""Pointer publication with immutable routing intents and ordered filesystem locks."""

import copy
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile

from . import schema as s
from .storage import canonical_bytes, safe_path, _strict_json


def session_key(value):
    s.text(value)
    host, separator, identity = value.partition(":")
    s.require(host in {"codex", "claude"} and separator and identity.strip(),
              "A host-qualified session identity is required", "focus_required")
    return value


def root_identity(value):
    s.closed(value, "path objective_id contract_digest")
    s.text(value["path"])
    s.require(Path(value["path"]).is_absolute(), "Registered root must be absolute")
    s.text(value["objective_id"])
    s.digest_string(value["contract_digest"])


def entry(value):
    if value is None:
        return
    s.closed(value, "generation target")
    s.digest_string(value["generation"])
    if value["target"] is not None:
        s.closed(value["target"], "root focus_request_id")
        root_identity(value["target"]["root"])
        s.text(value["target"]["focus_request_id"])


def validate_registry(value):
    s.closed(value, "schema_version roots sessions")
    s.integer(value["schema_version"], 1, 1)
    seen = set()
    for root in s.records(value["roots"]):
        root_identity(root)
        s.require(root["path"] not in seen, "Duplicate registered root")
        seen.add(root["path"])
    s.require(isinstance(value["sessions"], dict), "Sessions must be a mapping")
    for session, pointer in value["sessions"].items():
        session_key(session)
        s.require(pointer is not None, "Cleared sessions retain a tombstone")
        entry(pointer)
        if pointer["target"] is not None:
            s.require(pointer["target"]["root"] in value["roots"], "Session root is not registered")
    return value


def registry_path(workspace):
    workspace = Path(workspace)
    s.require(workspace.is_dir() and not workspace.is_symlink(),
              "Workspace registration requires an existing directory without symlinks", "unsafe_path")
    s.require(not (workspace / ".exactory").is_symlink()
              and not (workspace / ".exactory/math-search.json").is_symlink()
              and not (workspace / ".exactory/math-search.lock").is_symlink(),
              "Registration paths cannot be symlinks", "unsafe_path")
    return safe_path(workspace, ".exactory/math-search.json")


def read_registry(workspace):
    path = registry_path(workspace)
    return validate_registry(_strict_json(path.read_bytes(), "corrupt_discovery")) if path.exists() else {
        "schema_version": 1, "roots": [], "sessions": {}}


@contextmanager
def registry_lock(workspace):
    path = registry_path(workspace)
    path.parent.mkdir(exist_ok=True)
    lock = safe_path(Path(workspace), ".exactory/math-search.lock")
    descriptor = os.open(str(lock), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        registry_path(workspace)
        yield
    finally:
        os.close(descriptor)


def prepare(controller, name, spec, request_id, workspace_root):
    """Read a prior pointer before the controller lock; replays use the pinned intent."""
    workspace = Path(workspace_root).absolute() if workspace_root is not None else controller.root.parent
    s.require(not workspace.is_symlink(), "Workspace registration cannot follow a symlink", "unsafe_path")
    workspace = workspace.resolve()
    registry_path(workspace)
    command_digest = s.digest({"command": name, "spec": spec, "workspace_root": str(workspace)})
    state = controller.status() if controller.store.tree_path.exists() else None
    if state is not None and request_id in state["requests"]:
        original = state["service"]["discovery_intents"].get(request_id)
        s.require(original is not None and original["command_digest"] == command_digest,
                  "Request ID has different routing or command inputs", "request_id_conflict")
        return copy.deepcopy(original)
    identity = {"path": str(controller.root), "objective_id": state["objective_id"] if state else "objective-000001",
                "contract_digest": state["contract_digest"] if state else s.digest(spec["contract"])}
    session = session_key(spec["session_id"]) if name != "init" else None
    with registry_lock(workspace):
        expected = read_registry(workspace)["sessions"].get(session) if session else None
    target = None if name == "focus" and spec["focus"] != "focused" else {
        "root": identity, "focus_request_id": request_id}
    desired = {"generation": s.digest({"root": identity, "request_id": request_id}), "target": target} if session else None
    return {"workspace_root": str(workspace), "root": identity, "request_id": request_id,
            "command_digest": command_digest, "session_id": session,
            "expected": expected, "desired": desired}


def record(state, value):
    """Pure validation/replay of the closed routing operation."""
    s.closed(value, "workspace_root root request_id command_digest session_id expected desired")
    s.text(value["workspace_root"])
    s.require(Path(value["workspace_root"]).is_absolute(), "Workspace must be absolute")
    root_identity(value["root"])
    s.require(value["root"]["objective_id"] == state["objective_id"]
              and value["root"]["contract_digest"] == state["contract_digest"], "Routing identifies another objective")
    s.text(value["request_id"])
    s.digest_string(value["command_digest"])
    s.require(value["request_id"] not in state["service"]["discovery_intents"], "Duplicate routing intent")
    entry(value["expected"])
    entry(value["desired"])
    if value["session_id"] is None:
        s.require(value["expected"] is None and value["desired"] is None, "Root registration cannot set session focus")
    else:
        session_key(value["session_id"])
        s.require(value["desired"] is not None, "Focus requires a pointer or tombstone")
        target = value["desired"]["target"]
        s.require(target is None or target == {"root": value["root"], "focus_request_id": value["request_id"]},
                  "Focus target differs from its routing intent")
        state["control"]["focus_record"] = {"session_id": value["session_id"], "request_id": value["request_id"]}
    state["service"]["discovery_intents"][value["request_id"]] = copy.deepcopy(value)


def publish(controller, intent):
    """Caller holds this controller's lock; acquire only the one registry lock."""
    from .model import replay
    state = replay(controller.store.read())
    s.require(state["service"]["discovery_intents"].get(intent["request_id"]) == intent,
              "Routing intent is not committed", "discovery_conflict")
    session = intent["session_id"]
    if session:
        s.require(state["control"]["focus_record"] == {"session_id": session, "request_id": intent["request_id"]},
                  "A later focus superseded this command; issue a fresh explicit focus", "discovery_conflict")
    workspace = Path(intent["workspace_root"])
    with registry_lock(workspace):
        value = read_registry(workspace)
        previous_root = next((root for root in value["roots"] if root["path"] == intent["root"]["path"]), None)
        s.require(previous_root is None or previous_root == intent["root"],
                  "Registered objective identity differs; preserve and inspect the registry", "discovery_conflict")
        if session:
            current = value["sessions"].get(session)
            s.require(current in (intent["expected"], intent["desired"]),
                      "Session pointer changed; issue a fresh explicit focus", "discovery_conflict")
            value["sessions"][session] = intent["desired"]
        if previous_root is None:
            value["roots"].append(intent["root"])
        value["roots"].sort(key=lambda root: root["path"])
        path = registry_path(workspace)
        raw = canonical_bytes(validate_registry(value))
        if path.exists() and path.read_bytes() == raw:
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".math-search-", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            registry_path(workspace)
            os.replace(temporary, path)
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def validate_session(state, session, request=None):
    owner = state["control"]["focus_record"]
    if not (session is not None and owner is not None and owner["session_id"] == session
            and (request is None or request == owner["request_id"]) and state["control"]["focus"] == "focused"):
        return False
    intent = state["service"]["discovery_intents"].get(owner["request_id"])
    if intent is None:
        return False
    # Atomic pointer reads create no lock file and grant no independent authority.
    # This also checks an external workspace when called from the root directly.
    return read_registry(Path(intent["workspace_root"]))["sessions"].get(session) == intent["desired"]
