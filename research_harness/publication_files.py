"""Restore pinned manuscript files and report drift during their recorded measurement."""

from .artifacts import ArtifactStore, _relative_parts
from .errors import ResearchError
from .predictions import select_measurement_reviews
from .publication import find_selected_bundle
from .workspace import read_file, write_projection


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
        pinned = artifacts.read(item["artifact"])
        code = "publication_artifact_changed"
        try:
            if read_file(artifacts.root, item["path"]) == pinned:
                continue
        except ResearchError as error:
            if error.code not in ("artifact_missing", "unsafe_path"):
                raise
            code = error.code
        differing.append({"path": item["path"], "code": code})
    return {"bundle_id": bundle["id"], "bundle_digest": bundle["digest"], "files": differing} if differing else None


def _validate_restore_path(path):
    parts = [part.casefold() for part in _relative_parts(path)]
    if parts[0] in (".exactory", ".git") or parts[:3] == ["research", "sources", "objects"]:
        raise ResearchError("unsafe_path",
                            "Restore destinations must be outside .exactory, .git and research/sources/objects",
                            {"path": path})


def restore_bundle(store, bundle_digest):
    """Keep displaced contents as artifacts before restoring an exact recorded bundle's working files."""
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
        planned, unchanged, paths = [], [], {}
        for item in bundle["files"].values():
            if item is None:
                continue
            path = item["path"]
            _validate_restore_path(path)
            pinned = artifacts.read(item["artifact"])
            if path in paths:
                if paths[path] != pinned:
                    raise ResearchError("publication_artifact_invalid",
                                        "Two pinned files of the bundle name this path with different bytes",
                                        {"path": path})
                continue
            paths[path] = pinned
            current = _read_working_file(store.root, path)
            if current == pinned:
                unchanged.append(path)
            else:
                planned.append((item, pinned, current))
        # Finish all validation and durable backups before the first working-file replacement.
        backups = [artifacts.put(current, "application/octet-stream") if current is not None else None
                   for _, _, current in planned]
        restored = []
        for (item, pinned, current), backup in zip(planned, backups):
            if _read_working_file(store.root, item["path"]) != current:
                raise ResearchError("publication_artifact_changed", "The working file changed while the restore ran",
                                    {"path": item["path"], "restored": restored})
            write_projection(store.root, item["path"], pinned)
            restored.append({"path": item["path"], "artifact": item["artifact"], "backup": backup})
        return {"revision": snapshot["revision"], "bundle_id": bundle["id"], "bundle_digest": bundle_digest,
                "restored": restored, "unchanged": unchanged}
