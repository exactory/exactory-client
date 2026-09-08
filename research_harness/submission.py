"""Managed author submission and verifier writes over the existing server wire API."""

from urllib.parse import quote

from .errors import ResearchError
from .evidence import digest
from .execution import _owner
from .operations import immutable_record, prepared_mutation
from .publication import publication_report
from .remote import begin_intent, finish_intent, get_intent, remote_step, resolve_step
from .verification import task_identity, validate_verdict


def _observation(store, intent_id, status, response):
    payload = {"intent_id": intent_id, "status": status, "response": response}
    identifier = intent_id + ":observation:" + digest(payload)
    return prepared_mutation(store, "submission.observe", payload,
        lambda records, value: ([immutable_record(records, "remote_observation", identifier, value)], value),
        expected_revision=store.revision, request_id=identifier)


def validate_submission(store, body):
    report = publication_report(store, "deposited")
    if not report["ready"]:
        raise ResearchError("readiness_required", "Submit the concrete published DOI of the current reviewed manuscript", {"obligations": report["obligations"]})
    records = store.snapshot()["records"]
    current = records.get("workspace", {}).get("deposit", {})
    candidates = [r for r in records.get("publication_receipt", {}).values()
                  if r.get("bundle_digest") == report["bundle"]["digest"] and r.get("doi") == current.get("doi") and r.get("environment") == "production"]
    if len(candidates) != 1:
        raise ResearchError("publication_receipt_required", "Select the exact current production publication receipt")
    receipt = candidates[0]
    identifier = body.get("doi")
    url = body.get("url")
    if not (isinstance(identifier, str) and identifier.lower() == receipt["doi"].lower()
            or isinstance(url, str) and url.rstrip("/") in (receipt["record_url"].rstrip("/"), "https://doi.org/" + receipt["doi"])):
        raise ResearchError("publication_identifier_mismatch", "Managed author submission must name the current concrete record DOI or URL")
    return report, receipt


def submit_managed(store, body, client, *, expected_revision, request_id):
    report, publication = validate_submission(store, body)
    binding = {"bundle_digest": report["bundle"]["digest"], "publication": publication, "body": body}
    # Equivalent record DOI/URL inputs retain their exact request payloads, but
    # refer to one durable submission for this concrete publication receipt.
    intent = begin_intent(store, "submit", binding, expected_revision=expected_revision, request_id=request_id,
        deduplication_key={"publication_receipt": publication["intent_id"], "record_doi": publication["doi"]})
    return continue_submission(store, intent["id"], client)


def continue_submission(store, identifier, client):
    with _owner(store.root, "remote:" + identifier):
        intent = get_intent(store, identifier)
        if intent["status"] == "complete":
            return store.snapshot()["records"]["submission_receipt"][identifier]
        binding = intent["binding"]
        response = intent["responses"].get("submit", {}).get("response")
        if response is None and intent["pending"] is None:
            validate_submission(store, binding["body"])
            response = remote_step(store, identifier, "submit", binding["body"],
                lambda: client("POST", "/api/v1/verifications", binding["body"])[0])
        locator = response.get("verificationId") if response else None
        locator = locator or binding["publication"].get("concept_doi") or binding["publication"]["doi"]
        task, status = client("GET", "/api/v1/tasks/" + quote(locator, safe="/"), allow_missing=True)
        if status == 404:
            _observation(store, identifier, "ingest_pending", {"locator": locator})
            raise ResearchError("verification_ingest_pending", "The remote request is recorded, but its task is not yet ingested; resume this intent with task-only reads",
                                {"intent_id": identifier})
        identity = task_identity(task)
        publication = binding["publication"]
        if identity["source"] != "zenodo" or identity["source_id"] != str(publication["deposition_id"]):
            _observation(store, identifier, "different_record", task)
            raise ResearchError("verification_target_mismatch", "The existing open task names a different published version. Waiting or repeating the request does not retarget that task",
                                {"intent_id": identifier, "actual_task": identity, "deposition_id": publication["deposition_id"]})
        if get_intent(store, identifier)["pending"] is not None:
            response = resolve_step(store, identifier, "submit", {"verificationId": task["verificationId"], "doi": task["doi"]},
                                    {"kind": "task_only_read", "task": task})
        receipt = {"intent_id": identifier, "bundle_digest": binding["bundle_digest"], "publication": publication,
                   "request_body": binding["body"], "request_response": response, "task": task,
                   "task_identity": identity, "record_doi": publication["doi"], "canonical_doi": task["doi"], "matched": True}
        return finish_intent(store, identifier, "submission_receipt", receipt)


def send_verdict(store, task, body, client, *, expected_revision, request_id):
    # Ownership covers the target-level decision before any distinct intent can
    # be prepared. Per-intent ownership alone permits two concurrent senders.
    try:
        with _owner(store.root, "verdict:" + task["verificationId"]):
            return _send_verdict(store, task, body, client,
                expected_revision=expected_revision, request_id=request_id)
    except ResearchError as error:
        if error.code != "execution_live":
            raise
        raise ResearchError("remote_operation_owned", "Another verdict sender owns this verification; wait for its recorded outcome before retrying",
                            {"verification_id": task["verificationId"]}) from error


def _send_verdict(store, task, body, client, *, expected_revision, request_id):
    report = validate_verdict(store, task, body)
    binding = {"assessment": report["assessment"], "body": report["body"], "task_identity": task_identity(task),
               "verification_id": task["verificationId"],
               "preparation_digest": report["preparation_digest"]}
    records = store.snapshot()["records"]
    prior = [r for r in records.get("remote_intent", {}).values()
             if r["kind"] == "verdict" and r["binding"]["verification_id"] == task["verificationId"]]
    uncertain = next((r for r in prior if r["pending"] is not None), None)
    if uncertain is not None:
        _observation(store, uncertain["id"], "verdict_response_unknown", {"verificationId": task["verificationId"], "viewerVerdictId": task.get("viewerVerdictId")})
        raise ResearchError("verdict_reconciliation_pending", "The previous POST has an unknown outcome. The task exposes only your verdict ID, so the existing API cannot confirm the exact body without exposing other verdicts; no duplicate POST was sent",
                            {"intent_id": uncertain["id"], "viewerVerdictId": task.get("viewerVerdictId")})
    if not any(r["binding"] == binding for r in prior):
        current_id = task.get("viewerVerdictId")
        confirmed = [(r["created_revision"], records["verdict_receipt"][r["id"]]["response"].get("id"))
                     for r in prior if r["status"] == "complete" and r["id"] in records.get("verdict_receipt", {})]
        if confirmed and (current_id is None or current_id in {item[1] for item in confirmed}):
            current_id = max(confirmed, key=lambda item: item[0])[1]
            if not isinstance(current_id, str) or not current_id:
                raise ResearchError("verdict_reconciliation_pending", "The known prior success needs an exact own verdict ID before an explicit revision")
        if current_id != body.get("supersedesVerdictId"):
            raise ResearchError("verdict_revision_required", "A new verdict revision must explicitly name the latest known own verdict ID")
    intent = begin_intent(store, "verdict", binding, expected_revision=expected_revision, request_id=request_id)
    with _owner(store.root, "remote:" + intent["id"]):
        current = get_intent(store, intent["id"])
        if current["status"] == "complete":
            return store.snapshot()["records"]["verdict_receipt"][intent["id"]]
        validate_verdict(store, task, body)
        response = remote_step(store, intent["id"], "verdict", body,
            lambda: client("POST", "/api/v1/verifications/" + quote(task["verificationId"], safe="") + "/verdicts", body)[0])
        return finish_intent(store, intent["id"], "verdict_receipt", {"intent_id": intent["id"], "body": body,
            "assessment_digest": report["assessment"]["digest"], "task": task, "response": response})
