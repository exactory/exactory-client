"""Copy exact candidate/source bytes into a new independent reviewer directory."""

import json
from pathlib import Path

from .artifacts import ArtifactStore
from .execution_evidence import author_readiness_state
from .errors import ResearchError
from .evaluation import Evaluation
from .publication import publication_report
from .workspace import write_projection


def references(value):
    found = {}
    def visit(item):
        if isinstance(item, dict):
            if {"path", "sha256", "size", "media_type"} <= item.keys():
                found[item["path"]] = item
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    return [found[key] for key in sorted(found)]


def _deliver(store, destination, manifest):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ResearchError("review_destination_exists", "Deliver into a new independent directory")
    artifacts = ArtifactStore(store.root)
    values = [(ref, artifacts.read(ref)) for ref in references(manifest)]
    destination.mkdir(parents=True, mode=0o700)
    for ref, data in values:
        write_projection(destination, ref["path"], data)
    write_projection(destination, "inputs.json", (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
    return {"destination": str(destination), "artifacts": [ref for ref, _ in values],
            "revision": store.revision, "mechanical_only": True}


def deliver_readiness(store, destination):
    snapshot = store.snapshot()
    evaluation = Evaluation(snapshot["records"], ArtifactStore(store.root))
    report = dict(author_readiness_state(snapshot["records"], evaluation), revision=snapshot["revision"])
    if report["review_inputs"] is None:
        raise ResearchError("candidate_checkpoint_missing", "Select an actual assessed candidate before independent delivery")
    return _deliver(store, destination, {"kind": "readiness", "revision": report["revision"], "inputs": report["review_inputs"],
        "execution_observations": report["execution_observations"]})


def deliver_manuscript(store, destination):
    report = publication_report(store, "manuscript")
    if not report["ready"]:
        raise ResearchError("readiness_required", "Prepare a current manuscript bundle before independent delivery", {"obligations": report["obligations"]})
    bundle = report["bundle"]
    manifest = {"kind": "manuscript", "revision": report["revision"], "bundle_digest": bundle["digest"],
                "files": bundle["files"], "claim_evidence": bundle["claim_evidence"], "candidate": bundle["candidate"],
                "execution_observations": bundle["execution_observations"],
                "inputs": bundle["review_inputs"]}
    return _deliver(store, destination, manifest)
