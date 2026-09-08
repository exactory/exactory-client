"""Task-only server identity, original-body preparation and exact verdict binding.

The server does not supply an original-document hash. Local acquisition supplies
that pin. A request's creator can independently verify an external paper; the
requestedByViewer flag is not an authorship declaration.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .identities import normalize_identifier
from .operations import fields, immutable_record, prepared_mutation, text
from .reading import validate_read_evidence
from .source_links import validate_link
from .synthesis import synthesis_state
from .workspace import strict_json


def task_identity(task):
    if not isinstance(task, dict) or not {"verificationId", "doi", "source", "sourceId", "sourceVersion", "url"} <= task.keys():
        raise ResearchError("verification_target_pending", "The task must supply its concrete source and version identity")
    for key in ("verificationId", "doi", "source", "sourceId", "url"):
        text(task[key], "Task " + key)
    if task["source"] == "arxiv":
        version = task["sourceVersion"]
        if type(version) is not int or version <= 0:
            raise ResearchError("verification_target_mismatch", "A versionless arXiv task cannot certify an exact body")
        work = normalize_identifier("arxiv:" + task["sourceId"] + "v" + str(version))
        if normalize_identifier(task["url"]) != work:
            raise ResearchError("verification_target_mismatch", "The task URL differs from its concrete source version")
        return {"source": "arxiv", "source_id": task["sourceId"], "version": version, "work_ids": [work]}
    if task["source"] == "zenodo":
        identifier = task["sourceId"]
        if not identifier.isdigit() or task["url"].rstrip("/") != "https://zenodo.org/records/" + identifier:
            raise ResearchError("verification_target_mismatch", "A Zenodo task must identify its concrete record")
        return {"source": "zenodo", "source_id": identifier, "version": task["sourceVersion"],
                "work_ids": ["doi:10.5281/zenodo." + identifier, "url:https://zenodo.org/records/" + identifier]}
    raise ResearchError("verification_target_pending", "This source needs a supported exact task identity adapter")


def _preparation(records, artifacts, task):
    try:
        report = synthesis_state(records, artifacts, "verification")
    except ResearchError as error:
        raise ResearchError("readiness_required", "Exact-target preparation contains unavailable or changed source bytes",
                            {"obligations": [error.as_dict()]}) from error
    if not report["ready"]:
        raise ResearchError("readiness_required", "Read the exact target and complete independent verification preparation",
                            {"obligations": report["obligations"]})
    target = report["configuration"]["target"]
    identity = task_identity(task)
    if target["id"] not in identity["work_ids"]:
        raise ResearchError("verification_target_mismatch", "The current task is a different concrete work from the acquired original body",
                            {"target": target, "task_identity": identity})
    return report, target, identity


def record_task(store, payload, *, expected_revision, request_id):
    """Capture a task-only response after acquiring its configured exact body."""
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, ("task",))
        report, target, identity = _preparation(records, artifacts, value["task"])
        record = {"task": value["task"], "target": target, "identity": identity,
                  "preparation_digest": report["digest"], "observed_revision": expected_revision}
        record["digest"] = digest(record)
        return [immutable_record(records, "verification_task", record["digest"], record),
                ("verification_selection", "task", {"digest": record["digest"]})], record
    return prepared_mutation(store, "verification.task", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def _body(artifacts, reference):
    body = strict_json(artifacts.read(reference))
    fields(body, ("stance", "summary", "prediction"),
           ("rationaleSections", "wouldChange", "suggestions", "findings", "supersedesVerdictId"))
    text(body["stance"], "Verdict stance")
    text(body["summary"], "Verdict summary")
    if not isinstance(body["prediction"], dict) or "percentile" not in body["prediction"]:
        raise ResearchError("prediction_required", "Record the separate cohort impact prediction")
    return body


def bind_verdict(store, payload, *, expected_revision, request_id):
    """Bind {id, task_digest, body:ArtifactRef, assessment} without changing the wire schema."""
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, ("id", "task_digest", "body", "assessment"))
        text(value["id"], "Verdict assessment ID")
        task = records.get("verification_task", {}).get(value["task_digest"])
        if task is None:
            raise ResearchError("verification_task_required", "Acquire and bind the task-only server target first")
        report, target, identity = _preparation(records, artifacts, task["task"])
        _body(artifacts, value["body"])
        assessment = value["assessment"]
        fields(assessment, ("assessor", "provenance", "independence_basis", "blind", "checks"))
        text(assessment["assessor"], "Verifier identity")
        text(assessment["independence_basis"], "Independent verification basis")
        artifacts.read(assessment["provenance"])
        if assessment["blind"] is not True:
            raise ResearchError("review_not_independent", "The verifier must assess the paper before reading other verdicts")
        if not isinstance(assessment["checks"], list):
            raise ResearchError("invalid_verdict_assessment", "Assess soundness, novelty and impact separately")
        evidence, dimensions = [], set()
        for check in assessment["checks"]:
            fields(check, ("dimension", "reason", "evidence"))
            if check["dimension"] not in ("soundness", "novelty", "impact") or check["dimension"] in dimensions:
                raise ResearchError("invalid_verdict_assessment", "Assess soundness, novelty and impact exactly once each")
            dimensions.add(check["dimension"])
            text(check["reason"], "Verification reasoning")
            if not isinstance(check["evidence"], list) or not check["evidence"]:
                raise ResearchError("invalid_verdict_assessment", "Support each distinct assessment with actual full-read source links")
            for link in check["evidence"]:
                validate_link(records, artifacts, link)
                evidence.append(validate_read_evidence(records, artifacts, link, depth="fulltext", target=target))
        if dimensions != {"soundness", "novelty", "impact"}:
            raise ResearchError("invalid_verdict_assessment", "Keep validity independent of novelty and impact prediction")
        if not any(link["version_id"] == target["id"] for c in assessment["checks"] if c["dimension"] == "soundness" for link in c["evidence"]):
            raise ResearchError("invalid_verdict_assessment", "The soundness reasoning must inspect the exact target itself")
        record = dict(value, target=target, task_identity=identity, verification_id=task["task"]["verificationId"],
                      preparation_digest=report["digest"], evidence=evidence, reviewed_revision=expected_revision)
        record["digest"] = digest(record)
        return [immutable_record(records, "verdict_assessment", value["id"], record),
                ("verification_selection", "verdict", {"id": value["id"]})], record
    return prepared_mutation(store, "verification.verdict", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def validate_verdict(store, task, body):
    snapshot = store.snapshot()
    records, artifacts = snapshot["records"], ArtifactStore(store.root)
    report, target, identity = _preparation(records, artifacts, task)
    selection = records.get("verification_selection", {}).get("verdict")
    if selection is None:
        raise ResearchError("verdict_assessment_required", "Bind an evidence-linked assessment of this exact verdict body")
    saved = records["verdict_assessment"][selection["id"]]
    if saved["verification_id"] != task["verificationId"] or saved["task_identity"] != identity or saved["target"] != target:
        raise ResearchError("verification_target_mismatch", "The current server task differs from the assessed exact target")
    if saved["preparation_digest"] != report["digest"]:
        raise ResearchError("verdict_assessment_stale", "Reassess the verdict against current exact-target preparation")
    if _body(artifacts, saved["body"]) != body:
        raise ResearchError("verdict_body_mismatch", "The transmitted verdict body must equal the bound assessment, including revisions")
    for check in saved["assessment"]["checks"]:
        for link in check["evidence"]:
            validate_read_evidence(records, artifacts, link, depth="fulltext", target=target)
    return {"ready": True, "revision": snapshot["revision"], "assessment": saved, "body": body, "task": task,
            "preparation_digest": report["digest"]}
