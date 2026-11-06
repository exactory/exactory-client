"""Stage, verify and atomically approve complete immutable plugin releases."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .state import (RuntimeRefusal, build_binding_path, lock_registry,
                    lock_sessions, read_binding, read_json, read_registry,
                    require_directory, select_session_state, sync_directory,
                    validate_digest, write_json)


MANIFEST_NAME = ".exactory-release.json"
SOURCE_EXCLUSIONS = {".git", "__pycache__", ".lane-scratch"}


def raise_walk_error(error):
    raise error


def collect_entries(root):
    """Hash bytes, relative names, directory presence and executable bits."""
    require_directory(root)
    entries = []
    for directory, directories, filenames in os.walk(root, followlinks=False, onerror=raise_walk_error):
        for name in sorted(directories + filenames):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == MANIFEST_NAME:
                continue
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                entries.append({"path": relative, "kind": "directory"})
            elif stat.S_ISREG(mode):
                entries.append({"path": relative, "kind": "file",
                                "executable": bool(mode & 0o111),
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            else:
                raise RuntimeRefusal("Bundle contains a symlink or special file: " + relative)
    return sorted(entries, key=lambda entry: entry["path"])


def digest_entries(entries):
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_immutable(path):
    if path.lstat().st_mode & 0o222:
        raise RuntimeRefusal("Published release is writable: " + str(path))


def read_manifest(root, digest):
    require_directory(root)
    require_immutable(root)
    manifest_path = root / MANIFEST_NAME
    require_immutable(manifest_path)
    manifest = read_json(manifest_path)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "digest", "entries"}
            or manifest["schema"] != 1 or manifest["digest"] != digest
            or not isinstance(manifest["entries"], list)):
        raise RuntimeRefusal("Invalid release manifest")
    if digest_entries(manifest["entries"]) != digest:
        raise RuntimeRefusal("Release manifest does not match its approved identity")
    return manifest


def check_entrypoints(root):
    require_directory(root / "codex")
    require_immutable(root / "codex")
    for entrypoint in ("codex/hook.py", "codex/session_start.py"):
        path = root / entrypoint
        if not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeRefusal("Missing release entrypoint")
        require_immutable(path)


def verify_bundle(root, digest):
    manifest = read_manifest(root, digest)
    if collect_entries(root) != manifest["entries"]:
        raise RuntimeRefusal("Release digest verification failed: " + digest)
    check_entrypoints(root)
    return root


def verify_release(prefix, digest):
    validate_digest(digest)
    require_directory(prefix / "releases")
    return verify_bundle(prefix / "releases" / digest, digest)


def check_release(prefix, digest):
    """Authenticate the manifest and check entrypoints without reading payloads."""
    validate_digest(digest)
    require_directory(prefix / "releases")
    root = prefix / "releases" / digest
    read_manifest(root, digest)
    check_entrypoints(root)
    return root


def copy_plugin(source, destination):
    """Copy package contents only; reject links instead of retaining cache links."""
    require_directory(source)
    for directory, directories, files in os.walk(source, followlinks=False, onerror=raise_walk_error):
        directories[:] = sorted(name for name in directories if name not in SOURCE_EXCLUSIONS)
        target_directory = destination / Path(directory).relative_to(source)
        target_directory.mkdir(parents=True, exist_ok=True)
        for name in directories + sorted(files):
            if name in SOURCE_EXCLUSIONS or name.endswith((".pyc", ".pyo")):
                continue
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode) or name == MANIFEST_NAME:
                raise RuntimeRefusal("Source contains a link, special file or reserved manifest: " + str(path))
            target = target_directory / name
            shutil.copyfile(path, target)
            os.chmod(target, 0o755 if mode & 0o111 else 0o644)


def freeze_release(root):
    for directory, _, filenames in os.walk(root, topdown=False, onerror=raise_walk_error):
        for name in filenames:
            path = Path(directory) / name
            os.chmod(path, 0o555 if path.stat().st_mode & 0o111 else 0o444)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        os.chmod(directory, 0o555)
        sync_directory(Path(directory))


def discard_stage(root):
    for directory, _, filenames in os.walk(root, onerror=raise_walk_error):
        os.chmod(directory, 0o755)
        for name in filenames:
            os.chmod(Path(directory) / name, 0o644)
    shutil.rmtree(root)


def publish_release(prefix, source):
    """Only this administrator operation approves bytes and moves the default."""
    prefix = Path(prefix).resolve()
    source = Path(source).resolve()
    if source == prefix or source in prefix.parents:
        raise RuntimeRefusal("The managed prefix must be outside the plugin source tree")
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(prefix / "releases")))
    try:
        copy_plugin(source, stage)
        entries = collect_entries(stage)
        digest = digest_entries(entries)
        # Detect a source changed during copying, before approving any bytes.
        comparison = Path(tempfile.mkdtemp(prefix=".compare-", dir=str(prefix / "releases")))
        try:
            copy_plugin(source, comparison)
            if collect_entries(comparison) != entries:
                raise RuntimeRefusal("Plugin source changed during publication")
        finally:
            discard_stage(comparison)
        write_json(stage / MANIFEST_NAME, {"schema": 1, "digest": digest, "entries": entries})
        freeze_release(stage)
        verify_bundle(stage, digest)
        with lock_registry(prefix):
            registry = read_registry(prefix)
            destination = prefix / "releases" / digest
            try:
                destination.lstat()
            except FileNotFoundError:
                os.replace(stage, destination)
                sync_directory(destination.parent)
            verify_release(prefix, digest)
            if digest not in registry["approved"]:
                registry["approved"].append(digest)
            registry["default"] = digest
            write_json(prefix / "state/registry.json", registry)
        return digest
    finally:
        try:
            stage.lstat()
        except FileNotFoundError:
            pass
        else:
            discard_stage(stage)


def resolve_release(prefix, payload, event, state_dir=None):
    """Enroll startup once, or resolve an existing binding without upgrading it."""
    session_id = payload.get("session_id")
    directory = select_session_state(prefix, state_dir)
    with lock_sessions(directory):
        registry = read_registry(prefix)
        try:
            binding = read_binding(directory, session_id)
        except FileNotFoundError:
            if event != "SessionStart" or payload.get("source") != "startup":
                raise RuntimeRefusal("Session has no binding; a fresh startup is required")
            digest = registry["default"]
            if digest is None:
                raise RuntimeRefusal("No default release is approved")
            root = verify_release(prefix, digest)
            binding = {"schema": 1, "session_id": session_id, "release": digest}
            write_json(build_binding_path(directory, session_id), binding, mode=0o600)
            return root
        digest = binding["release"]
        if digest not in registry["approved"]:
            raise RuntimeRefusal("Session release is not approved")
        if event == "SessionStart" and payload.get("source") == "startup":
            return verify_release(prefix, digest)
        return check_release(prefix, digest)


def remove_release(prefix, digest):
    """There is deliberately no garbage collection in runtime protocol v1."""
    validate_digest(digest)
    registry = read_registry(prefix)
    if digest in registry["approved"]:
        raise RuntimeRefusal("Approved releases are retained; v1 does not remove them")
    raise RuntimeRefusal("Unapproved releases require administrator inspection")
