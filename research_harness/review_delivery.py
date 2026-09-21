"""Copy exact candidate/source bytes into a new independent reviewer directory.

The manifest is a neutral packet (review_packets): no revision labels, request
identities, launcher tokens, author names, assessment history or author plans
reach a manuscript reviewer, and a readiness reviewer sees the six checks'
evidence without labels."""

import json
from pathlib import Path

from .artifacts import ArtifactStore, describe_artifact
from .execution_evidence import author_readiness_state
from .errors import ResearchError
from .evaluation import Evaluation
from .publication import _bundle, publication_state
from .review_packets import manuscript_packet, readiness_packet, round_packet, find_round_investigation_responses
from .scientific_json import scientific_json
from .scientific_delivery import encode_delivery
from .workspace import strict_json, write_projection


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


def _deliver(store, destination, manifest, *, derived=None, transitive=False, empty_captures=()):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ResearchError("review_destination_exists", "Deliver into a new independent directory")
    artifacts = ArtifactStore(store.root)
    derived = derived or {}
    values, pending, seen = [], references(manifest), set()
    while pending:
        ref = pending.pop()
        if ref["path"] in seen:
            continue
        seen.add(ref["path"])
        data = derived[ref["path"]] if ref["path"] in derived else artifacts.read(ref)
        values.append((ref, data))
        if transitive and (data or ref not in empty_captures):
            is_json, structured = scientific_json(data, ref["media_type"])
            if is_json:
                pending.extend(references(structured))
    destination.mkdir(parents=True, mode=0o700)
    for ref, data in values:
        write_projection(destination, ref["path"], data)
    # Transitive scientific packets use the same bytes as their bounded count.
    data = encode_delivery(manifest) if transitive else (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    write_projection(destination, "inputs.json", data)
    return {"destination": str(destination), "artifacts": [ref for ref, _ in values],
            "revision": store.revision, "mechanical_only": True}


def deliver_readiness(store, destination):
    snapshot = store.snapshot()
    evaluation = Evaluation(snapshot["records"], ArtifactStore(store.root))
    from .publication_scope import has_publication_scope, assess_manuscript_readiness
    scoped = has_publication_scope(snapshot["records"])
    report = dict((assess_manuscript_readiness if scoped else author_readiness_state)(snapshot["records"], evaluation), revision=snapshot["revision"])
    if report["review_inputs"] is None:
        raise ResearchError("candidate_checkpoint_missing", "Select an actual assessed candidate before independent delivery")
    packet = readiness_packet(report)
    if scoped:
        from .scientific_delivery import project_delivery
        from .review_packets import scrub
        contract = report["contract"]
        if contract is None:
            raise ResearchError("publication_scope_stale", "Deliver the explicit current scoped candidate", {"obligations": report["obligations"]})
        packet = scrub(packet, ("author", "authors", "request_id", "token"))
        packet["scientific_scope"] = contract["public_projection"]
        packet["review_target"] = {"kind": "source_limited_manuscript", "contract_id": contract["id"],
                                   "scientific_target_digest": report["scientific_target_digest"],
                                   "candidate_digest": report["candidate_digest"]}
        correction = contract["payload"]["correction"]
        if correction is not None:
            packet["scientific_correction"] = correction
            packet["predecessor_findings"] = [{"review_id": r["review_id"], "kind": r["kind"],
                "scientific_target_digest": r["scientific_target_digest"],
                "verdict": snapshot["records"][r["kind"]][r["review_id"]]["payload"]["verdict"],
                "checks": snapshot["records"][r["kind"]][r["review_id"]]["payload"]["checks"]}
                for r in contract["required_corrections"]]
        packet, derived = project_delivery(snapshot["records"], evaluation, contract, packet, corrective=correction is not None)
        return _deliver(store, destination, packet, derived=derived, transitive=True)
    return _deliver(store, destination, packet)


def _project_current_claims(bundle, artifacts):
    """Separate current claims from the study's internal continuity ledger."""
    claims = strict_json(artifacts.read(bundle["files"]["claims"]["artifact"]))
    if not any("revised" in claim or "superseded" in claim for claim in claims):
        return bundle, {}
    current = [{key: value for key, value in claim.items() if key != "revised"}
               for claim in claims if "superseded" not in claim]
    data = (json.dumps(current, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    reference = describe_artifact(data, "application/json")
    files = dict(bundle["files"], claims=dict(bundle["files"]["claims"], artifact=reference))
    identifiers = {claim["id"] for claim in current}
    evidence = [item for item in bundle["claim_evidence"] if item["claim_id"] in identifiers]
    return dict(bundle, files=files, claim_evidence=evidence), {reference["path"]: data}


def deliver_manuscript(store, destination):
    snapshot = store.snapshot()
    report = publication_state(snapshot["records"], Evaluation(snapshot["records"], ArtifactStore(store.root)), "manuscript")
    if not report["ready"]:
        raise ResearchError("readiness_required", "Prepare a current manuscript bundle before independent delivery", {"obligations": report["obligations"]})
    bundle, derived = _project_current_claims(report["bundle"], ArtifactStore(store.root))
    packet = manuscript_packet(snapshot["records"], bundle)
    if bundle.get("publication_scope") is not None:
        from .scientific_delivery import project_delivery
        from .publication_scope import find_publication_scope
        packet, projected = project_delivery(snapshot["records"], ArtifactStore(store.root), find_publication_scope(snapshot["records"]), packet,
            manuscript_files=[bundle["files"][kind]["artifact"] for kind in ("pdf", "abstract", "bibliography", "claims")])
        derived.update(projected)
        return _deliver(store, destination, packet, derived=derived, transitive=True)
    return _deliver(store, destination, packet, derived=derived)


def deliver_round(store, destination):
    """The latest round decision on the exact current bundle, delivered to the independent round assessor."""
    records = store.snapshot()["records"]
    bundle = _bundle(records, Evaluation(records, ArtifactStore(store.root)))
    decisions = [d for d in records.get("round_decision", {}).values() if d["bundle_digest"] == bundle["digest"]]
    if not decisions:
        raise ResearchError("round_decision_missing", "Record the round decision before delivering it for review")
    packet = round_packet(records, bundle, max(decisions, key=lambda d: d["decided_revision"]))
    if bundle.get("publication_scope") is not None:
        from .scientific_delivery import project_delivery
        from .publication_scope import find_publication_scope
        captures = find_round_investigation_responses(records, packet)
        packet, derived = project_delivery(records, ArtifactStore(store.root), find_publication_scope(records), packet,
            manuscript_files=[bundle["files"][kind]["artifact"] for kind in ("pdf", "abstract", "bibliography", "claims")])
        return _deliver(store, destination, packet, derived=derived, transitive=True, empty_captures=captures)
    return _deliver(store, destination, packet)
