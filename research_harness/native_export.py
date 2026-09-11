"""Export current complete preparation and all original bytes for native review."""

import hashlib
import os
from pathlib import Path

from .artifacts import ArtifactStore, _Workspace
from .errors import ResearchError
from .review_delivery import references
from .storage import _canonical
from .synthesis import synthesis_state
from .workspace import write_projection


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
    artifacts = Evaluation(snapshot["records"], ArtifactStore(store.root))
    config = snapshot["records"].get("configuration", {}).get("research")
    if config is None:
        raise ResearchError("migration_required", "Initialize or adopt the common preparation contract first")
    report = synthesis_state(snapshot["records"], artifacts, config["profile"])
    if not report["ready"]:
        raise ResearchError("readiness_required", "Native admission requires current complete preparation", {"obligations": report["obligations"]})
    value = {"schema_version": 1, "profile": config["profile"], "research_revision": snapshot["revision"],
             "preparation": report, "records": snapshot["records"]}
    raw = _canonical(value).encode()
    reference = {"schema_version": 1, "workspace": os.path.relpath(store.root, attack_root),
                 "profile": config["profile"], "revision": snapshot["revision"],
                 "snapshot_digest": hashlib.sha256(raw).hexdigest(), "preparation_digest": report["digest"],
                 "claim_binding": claim_binding}
    inputs = [{"path": relative + "/snapshot.json", "kind": "blob", "digest": reference["snapshot_digest"]}]
    originals = [(ref, artifacts.read(ref)) for ref in references(value)]
    for ref, data in originals:
        path = relative + "/artifacts/" + ref["sha256"]
        write_projection(attack_root, path, data)
        inputs.append({"path": path, "kind": "artifact", "digest": ref["sha256"]})
    write_projection(attack_root, inputs[0]["path"], raw)
    result = {"foundation": reference, "inputs": inputs, "revision": snapshot["revision"],
              "mechanical_only": True, "native_proof_acceptance": False}
    write_projection(attack_root, relative + "/manifest.json", (_canonical(result) + "\n").encode())
    return result
