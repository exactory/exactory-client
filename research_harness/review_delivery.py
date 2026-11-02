"""Copy exact candidate/source bytes into a new independent reviewer directory.

The manifest is a neutral packet (review_packets): no revision labels, request
identities, launcher tokens, author names, assessment history or author plans
reach a manuscript reviewer, and a readiness reviewer sees the six checks'
evidence without labels."""

import codecs
from contextlib import contextmanager
import io
import json
import os
import shutil
import uuid
from pathlib import Path

from .artifacts import ArtifactStore, _filesystem_error, describe_artifact, is_sealed_artifact
from .execution_evidence import author_readiness_state
from .errors import ResearchError
from .evidence import digest
from .evaluation import Evaluation
from .file_streams import _copy_stream, freeze_file
from .output_views import PACKETS_FOLDER, _format_file_name, find_workspace_root
from .publication import _bundle, publication_state
from .review_packets import manuscript_packet, readiness_packet, round_packet, find_round_investigation_responses
from .scientific_json import is_json_media_type, parse_scientific_json
from .scientific_delivery import encode_delivery
from .workspace import checked_parent, strict_json, write_projection


def references(value):
    found = {}
    def visit(item):
        if isinstance(item, dict):
            if {"path", "sha256", "size", "media_type"} <= item.keys() and not is_sealed_artifact(item):
                found[(item["path"], digest(item))] = item
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    return [found[key] for key in sorted(found)]


def find_packet_destination(store, name):
    """The default directory of a reviewer packet: intermediate/reviews/packets/<name>/ of the workspace root, or
    <name>-2, <name>-3, ... when that directory exists. A name that is not a portable file name is replaced by its
    digest, as the output views name records."""
    folder, name = find_workspace_root(store.root) / PACKETS_FOLDER, _format_file_name(name)
    destination, number = folder / name, 1
    while destination.exists() or destination.is_symlink():
        number += 1
        destination = folder / (name + "-" + str(number))
    return destination


@contextmanager
def _stage_delivery(root, relative):
    destination = root / relative
    with checked_parent(root, relative, create=True) as (directory, name):
        temporary = ".delivery-" + uuid.uuid4().hex
        os.mkdir(temporary, mode=0o700, dir_fd=directory)
        staging = destination.parent / temporary
        try:
            yield staging
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ResearchError("review_destination_exists", "Deliver into a new independent directory")
            os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def deliver_packet(store, destination, manifest, *, derived=None, transitive=False, empty_captures=()):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ResearchError("review_destination_exists", "Deliver into a new independent directory")
    artifacts = ArtifactStore(store.root)
    derived = derived or {}
    values, pending, seen = [], references(manifest), set()
    try:
        with _stage_delivery(destination.parent.resolve(), destination.name) as staging:
            while pending:
                ref = pending.pop()
                key = digest(ref)
                if key in seen:
                    continue
                seen.add(key)
                source = (freeze_file(io.BytesIO(derived[ref["path"]]), ref) if ref["path"] in derived
                          else artifacts.open_verified(ref))
                with source as frozen:
                    with checked_parent(staging, ref["path"], create=True) as (directory, name):
                        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                                             0o600, dir_fd=directory)
                        with os.fdopen(descriptor, "wb") as target:
                            _copy_stream(frozen.stream, target, ref)
                            target.flush()
                            os.fsync(target.fileno())
                        os.fsync(directory)
                    values.append(ref)
                    if transitive and (frozen.size or ref not in empty_captures):
                        if frozen.size > 64 * 1024 * 1024:
                            raise ResearchError("invalid_scientific_delivery", "Scientific JSON bytes must fit the individual 64 MiB artifact bound")
                        frozen.stream.seek(0)
                        possible_json = is_json_media_type(ref["media_type"])
                        if not possible_json:
                            header = frozen.stream.read(4)
                            decoder = codecs.getincrementaldecoder(json.detect_encoding(header))(errors="surrogatepass")
                            frozen.stream.seek(0)
                            while True:
                                chunk = frozen.stream.read(1024 * 1024)
                                try:
                                    prefix = decoder.decode(chunk, final=not chunk).lstrip()
                                except UnicodeError:
                                    break
                                if prefix:
                                    possible_json = prefix.startswith(("{", "[", '"'))
                                    break
                                if not chunk:
                                    break
                        if possible_json:
                            frozen.stream.seek(0)
                            with io.BytesIO() as encoded:
                                _copy_stream(frozen.stream, encoded, ref)
                                is_json, structured = parse_scientific_json(encoded.getvalue(), ref["media_type"])
                            if is_json:
                                pending.extend(references(structured))
                            del structured
            data = encode_delivery(manifest) if transitive else (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
            write_projection(staging, "inputs.json", data)
    except OSError as error:
        raise _filesystem_error(error) from error
    return {"destination": str(destination), "artifacts": values,
            "revision": store.revision, "mechanical_only": True}


def deliver_readiness(store, destination=None):
    snapshot = store.snapshot()
    if destination is None:
        destination = find_packet_destination(store, "readiness-r" + str(snapshot["revision"]))
    evaluation = Evaluation(snapshot["records"], ArtifactStore(store.root), retention_budget_bytes=0)
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
        return deliver_packet(store, destination, packet, derived=derived, transitive=True)
    return deliver_packet(store, destination, packet)


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


def deliver_manuscript(store, destination=None):
    snapshot = store.snapshot()
    if destination is None:
        destination = find_packet_destination(store, "manuscript-r" + str(snapshot["revision"]))
    artifacts = Evaluation(snapshot["records"], ArtifactStore(store.root), retention_budget_bytes=0)
    report = publication_state(snapshot["records"], artifacts, "manuscript")
    if not report["ready"]:
        raise ResearchError("readiness_required", "Prepare a current manuscript bundle before independent delivery", {"obligations": report["obligations"]})
    bundle, derived = _project_current_claims(report["bundle"], artifacts)
    packet = manuscript_packet(snapshot["records"], bundle, artifacts)
    if bundle.get("publication_scope") is not None:
        from .scientific_delivery import project_delivery
        from .publication_scope import find_publication_scope
        packet, projected = project_delivery(snapshot["records"], ArtifactStore(store.root), find_publication_scope(snapshot["records"]), packet,
            manuscript_files=[bundle["files"][kind]["artifact"] for kind in ("pdf", "abstract", "bibliography", "claims")],
            derived=derived)
        derived.update(projected)
        return deliver_packet(store, destination, packet, derived=derived, transitive=True)
    return deliver_packet(store, destination, packet, derived=derived)


def deliver_round(store, destination=None):
    """The latest round decision on the exact current bundle, delivered to the independent round assessor."""
    snapshot = store.snapshot()
    records = snapshot["records"]
    if destination is None:
        destination = find_packet_destination(store, "round-r" + str(snapshot["revision"]))
    artifacts = Evaluation(records, ArtifactStore(store.root), retention_budget_bytes=0)
    bundle = _bundle(records, artifacts)
    decisions = [d for d in records.get("round_decision", {}).values() if d["bundle_digest"] == bundle["digest"]]
    if not decisions:
        raise ResearchError("round_decision_missing", "Record the round decision before delivering it for review")
    packet = round_packet(records, bundle, max(decisions, key=lambda d: d["decided_revision"]), artifacts)
    if bundle.get("publication_scope") is not None:
        from .scientific_delivery import project_delivery
        from .publication_scope import find_publication_scope
        captures = find_round_investigation_responses(records, packet)
        packet, derived = project_delivery(records, ArtifactStore(store.root), find_publication_scope(records), packet,
            manuscript_files=[bundle["files"][kind]["artifact"] for kind in ("pdf", "abstract", "bibliography", "claims")])
        return deliver_packet(store, destination, packet, derived=derived, transitive=True, empty_captures=captures)
    return deliver_packet(store, destination, packet)
