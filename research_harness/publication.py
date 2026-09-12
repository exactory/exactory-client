"""Exact manuscript snapshots, unchanged rubric reviews and publication receipts."""

import hashlib
from pathlib import Path

from .artifacts import ArtifactStore
from .execution_evidence import author_readiness_state
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .workspace import read_file, strict_json


FILE_TYPES = {"pdf": "application/pdf", "abstract": "text/plain", "bibliography": "text/plain",
              "claims": "application/json", "sources": "application/octet-stream"}


def _ready(records, artifacts):
    report = author_readiness_state(records, artifacts)
    if not report["ready"]:
        raise ResearchError("readiness_required", "Current whole-candidate readiness is required for the manuscript",
                            {"obligations": report["obligations"]})
    return report


def prepare_publication(store, payload, *, expected_revision, request_id):
    """Pin {id, files:{pdf,abstract,bibliography,claims,sources}, claim_evidence}."""
    def prepare(records, value):
        artifacts = Evaluation(records, ArtifactStore(store.root))
        fields(value, ("id", "files", "claim_evidence"))
        text(value["id"], "Publication bundle ID")
        fields(value["files"], tuple(FILE_TYPES))
        report = _ready(records, artifacts)
        saved = {}
        for kind, path in value["files"].items():
            if path is None and kind == "sources":
                saved[kind] = None
                continue
            data = read_file(store.root, path)
            if not data or kind == "pdf" and not data.startswith(b"%PDF-"):
                raise ResearchError("publication_artifact_invalid", "Publication files must contain their declared content")
            if kind == "abstract":
                try:
                    nonempty = bool(data.decode("utf-8").strip())
                except UnicodeError as error:
                    raise ResearchError("publication_artifact_invalid", "The abstract must be UTF-8 text") from error
                if not nonempty:
                    raise ResearchError("publication_artifact_invalid", "The abstract must contain nonblank text")
            saved[kind] = {"path": path, "artifact": artifacts.put(data, FILE_TYPES[kind])}
        claims = strict_json(artifacts.read(saved["claims"]["artifact"]))
        if not isinstance(claims, list) or not claims:
            raise ResearchError("publication_claims_missing", "Provide the actual manuscript claim registry")
        claim_ids = []
        for claim in claims:
            if not isinstance(claim, dict):
                raise ResearchError("publication_claims_missing", "Claim records must have stable IDs and claim text")
            claim_ids.append(text(claim.get("id"), "Claim ID"))
            text(claim.get("claim"), "Claim text")
        if len(set(claim_ids)) != len(claim_ids):
            raise ResearchError("publication_claims_missing", "Claim IDs must be unique")
        if not isinstance(value["claim_evidence"], list):
            raise ResearchError("publication_claims_missing", "Map each claim to current candidate evidence")
        allowed = {digest(e) for e in report["candidate"]["evidence"]}
        seen = set()
        for link in value["claim_evidence"]:
            fields(link, ("claim_id", "evidence"))
            if link["claim_id"] not in claim_ids or link["claim_id"] in seen:
                raise ResearchError("publication_claims_missing", "Map each manuscript claim exactly once")
            seen.add(link["claim_id"])
            if not isinstance(link["evidence"], list) or not link["evidence"] or any(digest(e) not in allowed for e in link["evidence"]):
                raise ResearchError("publication_evidence_mismatch", "Every manuscript claim must link actual current candidate evidence")
        if seen != set(claim_ids):
            raise ResearchError("publication_claims_missing", "Map every manuscript claim to evidence")
        bundle = dict(value, files=saved, candidate=report["candidate"], readiness_digest=digest(report),
                      readiness_review=records["readiness_review"][records["development_selection"]["review"]["review_id"]],
                      review_inputs=report["review_inputs"], prepared_revision=expected_revision,
                      execution_observations=report["execution_observations"],
                      mechanical_only=True)
        bundle["digest"] = digest(bundle)
        return [immutable_record(records, "publication_bundle", value["id"], bundle),
                ("publication_selection", "bundle", {"id": value["id"]})], bundle
    return prepared_mutation(store, "publication.prepare", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def _bundle(records, artifacts):
    selected = records.get("publication_selection", {}).get("bundle")
    if selected is None:
        raise ResearchError("publication_bundle_missing", "Prepare the exact PDF, abstract, bibliography and claims")
    bundle = records["publication_bundle"][selected["id"]]
    report = _ready(records, artifacts)
    if digest(report) != bundle["readiness_digest"]:
        raise ResearchError("publication_readiness_stale", "Prepare and review the manuscript against current whole-candidate readiness")
    for item in bundle["files"].values():
        if item is not None and read_file(artifacts.root, item["path"]) != artifacts.read(item["artifact"]):
            raise ResearchError("publication_artifact_changed", "The manuscript or its supporting files changed after preparation",
                                {"path": item["path"]})
    return bundle


def _assessor_key(assessor_id):
    return " ".join(assessor_id.casefold().split())


def latest_reviews(records, bundle_digest):
    """The latest manuscript review per assessor on the bundle, keyed by assessor.

    Recording refuses a second review per assessor per exact bundle; for reviews recorded
    before that rule the assessor's latest stands. The gate and the measurement summary
    both read this selection.
    """
    latest = {}
    for saved in records.get("manuscript_review", {}).values():
        if saved["bundle_digest"] == bundle_digest:
            key = _assessor_key(saved["assessor"]["id"])
            if key not in latest or saved["reviewed_revision"] > latest[key]["reviewed_revision"]:
                latest[key] = saved
    return latest


def validate_assessor(artifacts, assessor, authors):
    """An identified human or agent assessor with pinned provenance who is not an author."""
    fields(assessor, ("id", "kind", "provenance", "relationship", "independence_basis"))
    for key in ("id", "relationship", "independence_basis"):
        text(assessor[key], "Independent assessor " + key)
    if assessor["kind"] not in ("human", "agent"):
        raise ResearchError("review_not_independent", "Supply an identified independent reviewer")
    artifacts.read(assessor["provenance"])
    if _assessor_key(assessor["id"]) in {_assessor_key(a) for a in authors}:
        raise ResearchError("review_not_independent", "An author cannot provide an independent assessment")
    return assessor


def _review(records, artifacts, value, bundle):
    fields(value, ("id", "bundle_digest", "assessor", "review", "blind"))
    text(value["id"], "Manuscript review ID")
    if value["bundle_digest"] != bundle["digest"]:
        raise ResearchError("publication_review_stale", "Review the exact current manuscript bundle")
    if value["blind"] is not True:
        raise ResearchError("review_not_independent", "Supply an identified independent blind reviewer")
    validate_assessor(artifacts, value["assessor"], bundle["candidate"]["authors"])
    core = strict_json(artifacts.read(value["review"]))
    fields(core, ("summary", "strengths", "weaknesses", "soundness", "presentation", "contribution", "overall", "decision"))
    text(core["summary"], "Review summary")
    for key in ("strengths", "weaknesses"):
        strings(core[key], key, nonempty=True)
    for key, maximum in (("soundness", 4), ("presentation", 4), ("contribution", 4), ("overall", 10)):
        if type(core[key]) not in (int, float) or not 1 <= core[key] <= maximum:
            raise ResearchError("invalid_review", "Review scores must use the unchanged rubric scales")
    if core["decision"] not in ("accept", "reject"):
        raise ResearchError("invalid_review", "The rubric decision must be accept or reject")
    return core


def record_manuscript_review(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        artifacts = Evaluation(records, ArtifactStore(store.root))
        bundle = _bundle(records, artifacts)
        core = _review(records, artifacts, value, bundle)
        key = _assessor_key(value["assessor"]["id"])
        for saved in records.get("manuscript_review", {}).values():
            if saved["bundle_digest"] == bundle["digest"] and _assessor_key(saved["assessor"]["id"]) == key:
                raise ResearchError("manuscript_review_duplicate", "This assessor already reviewed this exact bundle; a rejection stands until the manuscript changes",
                                    {"review_id": saved["id"]})
        record = dict(value, core=core, reviewed_revision=expected_revision, digest=digest(value))
        return [immutable_record(records, "manuscript_review", value["id"], record)], record
    return prepared_mutation(store, "publication.review", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def publication_state(records, artifacts, action="publication"):
    artifacts = Evaluation.of(records, artifacts)
    bundle, reviews, obligations = None, [], []
    try:
        bundle = _bundle(records, artifacts)
        cores = {}
        for saved in records.get("manuscript_review", {}).values():
            if saved["bundle_digest"] == bundle["digest"]:
                cores[saved["id"]] = _review(records, artifacts, {k: saved[k] for k in ("id", "bundle_digest", "assessor", "review", "blind")}, bundle)
        reviews = list(latest_reviews(records, bundle["digest"]).values())
        if action != "manuscript" and (len(reviews) < 2 or any(cores[saved["id"]]["decision"] != "accept" for saved in reviews)):
            obligations.append(obligation("manuscript_reviews_required", "Obtain two independent accepting reviews of this exact manuscript and resolve current rejections."))
        if action == "deposited":
            if not any(r.get("bundle_digest") == bundle["digest"] and r.get("doi") and r.get("environment") == "production"
                       for r in records.get("publication_receipt", {}).values()):
                obligations.append(obligation("publication_receipt_required", "Publish this exact reviewed bundle and reconcile its concrete record DOI."))
        if action == "submitted":
            if not any(r.get("bundle_digest") == bundle["digest"] and r.get("matched") is True
                       for r in records.get("submission_receipt", {}).values()):
                obligations.append(obligation("submission_receipt_required", "Associate the current publication with the server's exact task target."))
    except ResearchError as error:
        obligations.append(obligation(error.code, error.message, **(error.details or {})))
    return {"ready": not obligations, "obligations": obligations, "bundle": bundle, "reviews": reviews,
            "digest": digest({"bundle": bundle["digest"] if bundle else None, "reviews": reviews, "obligations": obligations}),
            "mechanical_only": True}


def publication_report(store, action="publication"):
    snapshot = store.snapshot()
    evaluation = Evaluation(snapshot["records"], ArtifactStore(store.root))
    return dict(publication_state(snapshot["records"], evaluation, action), revision=snapshot["revision"])


def validate_upload(store, pdf, abstract, sources):
    snapshot = store.snapshot()
    artifacts = Evaluation(snapshot["records"], ArtifactStore(store.root))
    report = publication_state(snapshot["records"], artifacts)
    if not report["ready"]:
        raise ResearchError("readiness_required", "Publication requires the exact manuscript and dual review gate", {"obligations": report["obligations"]})
    bundle = report["bundle"]
    for kind, supplied in (("pdf", pdf), ("abstract", abstract), ("sources", sources)):
        pinned = bundle["files"][kind]
        if (supplied is None) != (pinned is None):
            raise ResearchError("publication_artifact_mismatch", "Upload arguments must identify the reviewed bundle")
        if supplied is not None:
            selected = Path(supplied).absolute()
            # Normalize a caller's workspace alias, retaining each path component
            # inside it so checked reads still reject internal symlinks.
            ancestors = [parent for parent in selected.parents if parent.resolve() == store.root]
            if not ancestors:
                raise ResearchError("publication_artifact_mismatch", "Upload files must belong to the reviewed workspace")
            relative = selected.relative_to(ancestors[0]).as_posix()
            if read_file(store.root, relative) != artifacts.read(pinned["artifact"]):
                raise ResearchError("publication_artifact_mismatch", "The selected upload differs from the reviewed manuscript", {"kind": kind})
    return dict(report, revision=snapshot["revision"])
