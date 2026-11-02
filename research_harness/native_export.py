"""Export current complete preparation and all original bytes for native review."""

import json
import os
from pathlib import Path
import tempfile

from .artifacts import ArtifactStore, _Workspace, _filesystem_error
from .errors import ResearchError
from .file_streams import _copy_stream, freeze_file
from .review_delivery import _stage_delivery, references
from .storage import _canonical, _json_types
from .synthesis import synthesis_state
from .workspace import checked_parent, write_projection


def export_native(store, attack_root, destination, *, claim_binding=None):
    supplied = Path(attack_root).absolute()
    destination = Path(destination).absolute()
    try:
        relative = destination.relative_to(supplied).as_posix()
        attack_root = _Workspace(supplied).root
        attack_root.relative_to(store.root)
    except ValueError as error:
        raise ResearchError("unsafe_path", "Native delivery must stay inside an attack root beneath the common workspace") from error
    destination = attack_root / relative
    if relative == "." or destination.exists() or destination.is_symlink():
        raise ResearchError("review_destination_exists", "Export the native foundation into a new directory")
    snapshot = store.snapshot()
    from .evaluation import Evaluation
    artifacts = Evaluation(snapshot["records"], ArtifactStore(store.root), retention_budget_bytes=0)
    config = snapshot["records"].get("configuration", {}).get("research")
    if config is None:
        raise ResearchError("migration_required", "Initialize or adopt the common preparation contract first")
    report = synthesis_state(snapshot["records"], artifacts, config["profile"])
    if not report["ready"]:
        raise ResearchError("readiness_required", "Native admission requires current complete preparation", {"obligations": report["obligations"]})
    value = {"schema_version": 1, "profile": config["profile"], "research_revision": snapshot["revision"],
             "preparation": report, "records": snapshot["records"]}
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    try:
        _json_types(value)
        with tempfile.TemporaryFile(mode="w+b") as source:
            for piece in encoder.iterencode(value):
                source.write(piece.encode("utf-8"))
            source.seek(0)
            with freeze_file(source) as snapshot_file, _stage_delivery(attack_root, relative) as staging:
                reference = {"schema_version": 1, "workspace": os.path.relpath(store.root, attack_root),
                             "profile": config["profile"], "revision": snapshot["revision"],
                             "snapshot_digest": snapshot_file.sha256, "preparation_digest": report["digest"],
                             "claim_binding": claim_binding}
                inputs = [{"path": relative + "/snapshot.json", "kind": "blob", "digest": reference["snapshot_digest"]}]
                written = set()
                originals = ArtifactStore(store.root)
                for ref in references(value):
                    if ref["path"] in written:
                        continue
                    with originals.open_verified(ref) as frozen:
                        written.add(ref["path"])
                        path = "artifacts/" + ref["sha256"]
                        with checked_parent(staging, path, create=True) as (directory, name):
                            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                                                 0o600, dir_fd=directory)
                            with os.fdopen(descriptor, "wb") as target:
                                _copy_stream(frozen.stream, target, ref)
                                target.flush()
                                os.fsync(target.fileno())
                            os.fsync(directory)
                        inputs.append({"path": relative + "/" + path, "kind": "artifact", "digest": ref["sha256"]})
                with checked_parent(staging, "snapshot.json") as (directory, name):
                    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=directory)
                    with os.fdopen(descriptor, "wb") as target:
                        _copy_stream(snapshot_file.stream, target,
                                     {"path": "snapshot.json", "sha256": snapshot_file.sha256, "size": snapshot_file.size})
                        target.flush()
                        os.fsync(target.fileno())
                result = {"foundation": reference, "inputs": inputs, "revision": snapshot["revision"],
                          "mechanical_only": True, "native_proof_acceptance": False}
                write_projection(staging, "manifest.json", (_canonical(result) + "\n").encode())
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ResearchError("invalid_input", "Expected finite JSON data with string object keys") from error
    except OSError as error:
        raise _filesystem_error(error) from error
    return result
