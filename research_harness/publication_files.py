"""Restore pinned manuscript files and report drift during their recorded measurement."""

import hashlib
import os
import unicodedata
import uuid

from .artifacts import ArtifactStore, _relative_parts, _regular_file, _filesystem_error, validate_reference
from .errors import ResearchError
from .predictions import select_measurement_reviews
from .publication import find_selected_bundle
from .workspace import checked_parent, read_file


_HASH_BUFFER_SIZE = 65536


def _has_working_drift(artifacts, item):
    reference = item["artifact"]
    validate_reference(reference)
    with checked_parent(artifacts.root, item["path"]) as (directory, name):
        descriptor = _regular_file(directory, name)
        try:
            before = os.fstat(descriptor)
            if before.st_size != reference["size"]:
                return True
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                hashed = hashlib.sha256()
                remaining = reference["size"] + 1
                while remaining:
                    block = source.read(min(_HASH_BUFFER_SIZE, remaining))
                    if not block:
                        break
                    hashed.update(block)
                    remaining -= len(block)
                    if hasattr(artifacts, "counters"):
                        artifacts.counters["working_bytes_hashed"] += len(block)
            after = os.fstat(descriptor)
            return (hashed.hexdigest() != reference["sha256"]
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        finally:
            os.close(descriptor)


def _read_working_file(root, path):
    try:
        return read_file(root, path)
    except ResearchError as error:
        if error.code != "artifact_missing":
            raise
        return None


def find_bundle_drift(records, artifacts):
    """Identify every differing path of the selected bundle until its measurement is complete."""
    bundle = find_selected_bundle(records)
    if bundle is None or select_measurement_reviews(records, bundle) is not None:
        return None
    differing = []
    for item in bundle["files"].values():
        if item is None:
            continue
        code = "publication_artifact_changed"
        try:
            if not _has_working_drift(artifacts, item):
                continue
        except FileNotFoundError:
            code = "artifact_missing"
        except OSError as error:
            code = _filesystem_error(error).code
        except ResearchError as error:
            code = error.code
        differing.append({"path": item["path"], "code": code})
    return {"bundle_id": bundle["id"], "bundle_digest": bundle["digest"], "files": differing} if differing else None


def _validate_restore_path(path):
    parts = [part.casefold() for part in _relative_parts(path)]
    if parts[0] in (".exactory", ".git") or parts[:3] == ["research", "sources", "objects"]:
        raise ResearchError("unsafe_path",
                            "Restore destinations must be outside .exactory, .git and research/sources/objects",
                            {"path": path})


def _resolve_restore_keys(root, path):
    entry = ("entry", tuple(unicodedata.normalize("NFC", part).casefold() for part in _relative_parts(path)))
    keys = {entry}
    try:
        with checked_parent(root, path) as (directory, name):
            identity = os.stat(name, dir_fd=directory, follow_symlinks=False)
            keys.add(("inode", identity.st_dev, identity.st_ino))
    except FileNotFoundError:
        pass
    return entry, keys


def _move_restore_file(root, item):
    path = item["path"]
    if _read_working_file(root, path) != item["current"]:
        raise ResearchError("publication_artifact_changed", "The working file changed while the restore ran",
                            {"path": path})
    try:
        with checked_parent(root, path) as (directory, name):
            displaced = ".restore-backup-" + uuid.uuid4().hex
            try:
                os.rename(name, displaced, src_dir_fd=directory, dst_dir_fd=directory)
            except FileNotFoundError:
                if item["current"] is not None:
                    raise ResearchError("publication_artifact_changed",
                                        "The working file changed while the restore ran", {"path": path})
                return
            item["backup_path"] = path.rsplit("/", 1)[0] + "/" + displaced if "/" in path else displaced
            os.fsync(directory)
    except FileNotFoundError:
        if item["current"] is not None:
            raise


def _install_restore_group(root, group, installed):
    path = group["items"][0]["path"]
    with checked_parent(root, path, create=True) as (directory, _):
        temporary = ".restore-pinned-" + uuid.uuid4().hex
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(group["pinned"])
                target.flush()
                os.fsync(target.fileno())
            for item in group["items"]:
                with checked_parent(root, item["path"], create=True) as (destination, name):
                    try:
                        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=destination,
                                follow_symlinks=False)
                    except FileExistsError as error:
                        raise ResearchError("publication_artifact_changed",
                                            "The working file changed while the restore ran",
                                            {"path": item["path"]}) from error
                    installed.append(item["path"])
                    os.fsync(destination)
        finally:
            os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)


def _restore_displaced_files(root, items, installed):
    for item in items:
        if item["path"] in installed or not item.get("backup_path"):
            continue
        try:
            with checked_parent(root, item["path"]) as (directory, name):
                os.link(item["backup_path"].rsplit("/", 1)[-1], name,
                        src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.fsync(directory)
        except (OSError, ResearchError):
            # Every displaced path remains available even when a concurrent name prevents rollback.
            pass


def restore_bundle(store, bundle_digest):
    """Keep displaced contents as artifacts before restoring recorded working files.

    Pinned paths with equal components after NFC normalization and casefolding,
    or with the same existing inode, are one destination. Different pinned bytes
    for such aliases are refused before any writes, even on case-sensitive
    filesystems; these bundles are never partly restored.
    """
    if (not isinstance(bundle_digest, str) or len(bundle_digest) != 64
            or any(character not in "0123456789abcdef" for character in bundle_digest)):
        raise ResearchError("invalid_input", "Supply the bundle digest, 64 lowercase hexadecimal digits")
    with store.guarded_snapshot() as guard:
        snapshot = guard.snapshot()
        bundle = next((item for item in snapshot["records"].get("publication_bundle", {}).values()
                       if item["digest"] == bundle_digest), None)
        if bundle is None:
            raise ResearchError("publication_bundle_missing", "No recorded manuscript bundle has this digest",
                                {"bundle_digest": bundle_digest})
        artifacts = ArtifactStore(store.root)
        groups, unchanged = [], []
        for item in bundle["files"].values():
            if item is None:
                continue
            path = item["path"]
            _validate_restore_path(path)
            pinned = artifacts.read(item["artifact"])
            try:
                entry, keys = _resolve_restore_keys(store.root, path)
            except OSError as error:
                raise _filesystem_error(error) from error
            aliases = [group for group in groups if group["keys"] & keys]
            if any(group["pinned"] != pinned for group in aliases):
                raise ResearchError("publication_artifact_invalid",
                                    "Two pinned files name the same destination with different bytes", {"path": path})
            if aliases:
                group = aliases[0]
                for other in aliases[1:]:
                    group["keys"].update(other["keys"])
                    group["items"].extend(other["items"])
                    groups.remove(other)
                group["keys"].update(keys)
            else:
                group = {"keys": keys, "items": [], "pinned": pinned}
                groups.append(group)
            if not any(existing["entry"] == entry for existing in group["items"]):
                group["items"].append(dict(item, entry=entry))
        planned = []
        for group in groups:
            for item in group["items"]:
                item["current"] = _read_working_file(store.root, item["path"])
                item["backup"] = None
            if all(item["current"] == group["pinned"] for item in group["items"]):
                unchanged.append(group["items"][0]["path"])
            else:
                planned.append(group)
        items = [item for group in planned for item in group["items"]]
        restored, installed = [], []
        try:
            for item in items:
                _move_restore_file(store.root, item)
                if item.get("backup_path"):
                    moved = read_file(store.root, item["backup_path"])
                    item["backup"] = artifacts.put(moved, "application/octet-stream")
            for group in planned:
                _install_restore_group(store.root, group, installed)
                first = group["items"][0]
                result = {key: first.get(key) for key in ("path", "artifact", "backup", "backup_path")}
                if len(group["items"]) > 1:
                    result["aliases"] = [item["path"] for item in group["items"][1:]]
                    result["backups"] = [{key: item.get(key) for key in ("path", "backup", "backup_path")}
                                         for item in group["items"]]
                restored.append(result)
        except (ResearchError, OSError) as error:
            _restore_displaced_files(store.root, items, installed)
            if isinstance(error, OSError):
                error = _filesystem_error(error)
            details = dict(error.details or {}, restored=restored, installed_paths=installed,
                           backups=[{key: item.get(key) for key in ("path", "backup", "backup_path")}
                                    for item in items if item.get("backup_path")])
            raise ResearchError(error.code, error.message, details) from error
        return {"revision": snapshot["revision"], "bundle_id": bundle["id"], "bundle_digest": bundle_digest,
                "restored": restored, "unchanged": unchanged}
