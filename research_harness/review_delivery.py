"""Copy exact candidate/source bytes into a new independent reviewer directory.

The manifest is a neutral packet (review_packets): no revision labels, request
identities, launcher tokens, author names, assessment history or author plans
reach a manuscript reviewer, and a readiness reviewer sees the six checks'
evidence without labels."""

import json
from pathlib import Path

from .artifacts import ArtifactStore
from .execution_evidence import author_readiness_state
from .errors import ResearchError
from .evaluation import Evaluation
from .publication import _bundle, publication_state
from .review_packets import manuscript_packet, readiness_packet, round_packet
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
    return _deliver(store, destination, readiness_packet(report))


def deliver_manuscript(store, destination):
    snapshot = store.snapshot()
    report = publication_state(snapshot["records"], Evaluation(snapshot["records"], ArtifactStore(store.root)), "manuscript")
    if not report["ready"]:
        raise ResearchError("readiness_required", "Prepare a current manuscript bundle before independent delivery", {"obligations": report["obligations"]})
    return _deliver(store, destination, manuscript_packet(snapshot["records"], report["bundle"]))


def deliver_round(store, destination):
    """The latest round decision on the exact current bundle, delivered to the independent round assessor."""
    records = store.snapshot()["records"]
    bundle = _bundle(records, Evaluation(records, ArtifactStore(store.root)))
    decisions = [d for d in records.get("round_decision", {}).values() if d["bundle_digest"] == bundle["digest"]]
    if not decisions:
        raise ResearchError("round_decision_missing", "Record the round decision before delivering it for review")
    return _deliver(store, destination, round_packet(records, bundle, max(decisions, key=lambda d: d["decided_revision"])))
