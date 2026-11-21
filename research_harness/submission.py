"""Managed author submission and verifier writes over the existing server wire API."""

from contextlib import contextmanager
from urllib.parse import quote

from .errors import ResearchError
from .finishing import retry_finishing_write
from .evidence import digest
from .execution import _owner
from .operations import immutable_record, prepared_task_mutation
from .publication import list_publication_receipts, publication_report
from .publication_scope import build_publication_scope_binding, validate_scope_binding, validate_caller_scope
from .remote import (ANSWER_MEMBERS, REMOTE_READ_STORE_BUSY_MSG, begin_intent, discard_step, find_intent, finish_intent,
                     get_intent, remote_step, resolve_step)
from .verification import task_identity, validate_verdict


# The message of an observation of a submission or a verdict that the store refused beyond the wait.
SUBMISSION_OBSERVATION_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. What the task read showed '
    'about this remote operation was not recorded. The read sent no write to the service. Run the same command '
    'again. It reads the task again.')
# The refusal of a submission whose published record has a saved submission with other declarations.
SUBMISSION_DECLARATIONS_DIFFER_MSG = (
    'This workspace saved a submission of the same published record with other grandChallengeIds or '
    'mathProblemIds. details.intent_id names that intent, and details.request holds its request. The workspace '
    'keeps one submission for a published record, so it does not record this request. To continue the saved '
    'submission, run exactory reconcile with that intent ID.')
# The refusal of a verdict file that does not name the current verdict of the account, which the server refuses.
VERDICT_PREDECESSOR_NOT_CURRENT_MSG = (
    'The command sent nothing. The server files a verdict only when supersedesVerdictId names the current verdict '
    'of this account on this verification. While the account has no verdict, the file names none. '
    'details.viewerVerdictId holds the current verdict that the task shows, and details.supersedesVerdictId holds the '
    'verdict that the file names. In both, null means none. An earlier run of this command can have sent this file. '
    'To find out, compare the current verdict with this file on the page of the verification. '
    'details.verificationId holds its ID. If they match, nothing remains to send. Do not set supersedesVerdictId to '
    'send a verdict that is already current: the server files it as a second verdict. To revise the current verdict, '
    'set supersedesVerdictId in the file to its ID. Then run the command again.')
# The refusal for a pending step of this verdict while the task shows a newer verdict of the account.
VERDICT_ANSWER_NOT_RECORDED_MSG = (
    'An earlier run of this workspace started the request of this verdict, and no record holds its answer. The '
    'intent in details.intent_id keeps the step as pending. The task shows a newer verdict of this account, in '
    'details.viewerVerdictId. The server accepts only a verdict that names the current one, so the command sent '
    'nothing. First find out whether the newer verdict is the verdict of this file. A store_busy refusal of an '
    'earlier run can hold this intent ID and response in its details. Then the id in details.response is the verdict '
    'of this file. Otherwise, compare the newer verdict with this file on the page of the verification. '
    'details.verificationId holds its ID. If the newer verdict is the verdict of this file, run exactory reconcile '
    'with the intent ID, and give its ID to --verdict-id. That command records the step and the receipt, and it sends '
    'no request. If it is another verdict, the step stays pending, and no command sends this file again. To replace '
    'the newer verdict, send a verdict file with its ID in supersedesVerdictId.')
# The refusal of --verdict-id for an ID that the task does not show as a newer current verdict, or for an intent
# that waits for no answer.
VERDICT_ID_NOT_CURRENT_MSG = (
    'The command recorded nothing and sent no request. --verdict-id records the answer of a pending verdict step. '
    'Its value must be the current verdict of this account on this verification, as the task shows it. It must also '
    'be newer than the verdict that the request supersedes. details.viewerVerdictId holds the current verdict that the '
    'task shows. details.pending says whether the intent has a pending step. If the task shows the verdict that the '
    'request supersedes, the server holds no verdict from the request. If the intent has no pending step, it waits '
    'for no answer. In both cases, run exactory reconcile with the intent ID alone. It sends the request, or it '
    'records or returns the receipt.')
# The members of a submission that declare links of the paper; the members that name the record are not among them.
_SUBMISSION_DECLARATION_MEMBERS = ("grandChallengeIds", "mathProblemIds")
# What check_verdict_preconditions returns for a pending step of this verdict while the task shows a newer verdict:
# the managed path owns that request, and _send_verdict ends the command with VERDICT_ANSWER_NOT_RECORDED_MSG.
_UNRECORDED_ANSWER_CHECK_RESULT = (None, None)


def _observation(store, intent_id, status, response):
    payload = {"intent_id": intent_id, "status": status, "response": response}
    identifier = intent_id + ":observation:" + digest(payload)
    return retry_finishing_write(lambda: prepared_task_mutation(store, "submission.observe", payload,
        lambda records, value: ([immutable_record(records, "remote_observation", identifier, value)], value),
        request_id=identifier), SUBMISSION_OBSERVATION_STORE_BUSY_MSG)


def validate_submission(store, body):
    report = publication_report(store, "deposited")
    if not report["ready"]:
        raise ResearchError("readiness_required", "Submit the concrete published DOI of the current reviewed manuscript", {"obligations": report["obligations"]})
    records = store.snapshot()["records"]
    current = records.get("workspace", {}).get("deposit", {})
    # An unpublished deposit has no DOI to name, so its receipt is no candidate.
    candidates = [r for r in list_publication_receipts(records)
                  if r.get("bundle_digest") == report["bundle"]["digest"] and r.get("environment") == "production"
                  and r.get("doi") and r.get("doi") == current.get("doi")]
    if len(candidates) != 1:
        raise ResearchError("publication_receipt_required", "Select the exact current production publication receipt")
    receipt = candidates[0]
    if not _is_publication_named(body, receipt):
        raise ResearchError("publication_identifier_mismatch", "Managed author submission must name the current concrete record DOI or URL")
    return report, receipt


def _is_publication_named(body, receipt):
    """Whether a submission body names the record of a publication receipt, by its DOI or by one of its two URLs."""
    identifier, url = body.get("doi"), body.get("url")
    urls = (receipt["record_url"].rstrip("/"), "https://doi.org/" + receipt["doi"])
    return (isinstance(identifier, str) and identifier.lower() == receipt["doi"].lower()
            or isinstance(url, str) and url.rstrip("/") in urls)


def check_submission_preconditions(store, body):
    """Every check the managed path runs before its remote write.

    Returns the intent binding and the deduplication key of the submission this
    workspace records. Raises ResearchError when the workspace cannot record
    this submission as it stands. Only the ordinary non-strict CLI may select
    its established direct path after a refusal. A submission whose record a
    pending step of this workspace names passes without the other checks: the
    request can be on the service, so the managed path continues that intent."""
    records = store.snapshot()["records"]
    pending = next((r for r in records.get("remote_intent", {}).values()
                    if r["kind"] == "submit" and r["pending"] is not None
                    and _is_publication_named(body, r["binding"]["publication"])), None)
    if pending is not None:
        _require_same_declarations(pending, body)
        return pending["binding"], pending.get("deduplication_key")
    report, publication = validate_submission(store, body)
    binding = {"bundle_digest": report["bundle"]["digest"], "publication": publication, "body": body}
    scope = build_publication_scope_binding(report["bundle"])
    if scope is not None:
        binding["publication_scope"] = scope
    # Equivalent record DOI/URL inputs retain their exact request payloads, but
    # refer to one durable submission for this concrete publication receipt.
    deduplication_key = {"publication_receipt": publication["intent_id"], "record_doi": publication["doi"]}
    saved = find_intent(records, "submit", binding, deduplication_key)
    if saved is not None:
        _require_same_declarations(saved, body)
    return binding, deduplication_key


def _require_same_declarations(intent, body):
    """Refuse a body whose declarations differ from the request of the saved submission of its record: the workspace
    continues or returns that saved request, and the declarations of this body are not in it."""
    saved = intent["binding"]["body"]
    if any(body.get(name) != saved.get(name) for name in _SUBMISSION_DECLARATION_MEMBERS):
        raise ResearchError("submission_declarations_mismatch", SUBMISSION_DECLARATIONS_DIFFER_MSG,
                            {"intent_id": intent["id"], "request": saved})


def submit_managed(store, body, client, *, expected_revision=None, request_id, scope_contract_id=None,
                   scope_target_digest=None):
    binding, deduplication_key = check_submission_preconditions(store, body)
    validate_caller_scope(binding.get("publication_scope"), scope_contract_id, scope_target_digest)
    intent = begin_intent(store, "submit", binding, expected_revision=expected_revision, request_id=request_id,
                          deduplication_key=deduplication_key)
    return continue_submission(store, intent["id"], client)


def continue_submission(store, identifier, client):
    with _owner(store.root, "remote:" + identifier):
        intent = get_intent(store, identifier)
        if intent["status"] == "complete":
            return store.snapshot()["records"]["submission_receipt"][identifier]
        binding = intent["binding"]
        publication = binding["publication"]
        # The service keeps one open request for a paper under its concept DOI, and its answer to a submission names
        # the request by its id. The task is read by that DOI on every path: after an ingest that gave up, a read by
        # the id finds nothing, and the DOI read finds the request that followed.
        locator = publication.get("concept_doi") or publication["doi"]
        response = intent["responses"].get("submit", {}).get("response")
        task = None
        if intent["pending"] is not None:
            task = _find_task(client, locator)
            if task is None:
                # The service shows no task of the record: the request did not arrive, the paper is not ingested, or
                # the ingest gave up on it. The service keeps one open request for a paper and answers a repeat with
                # that request; after an ingest that gave up, a repeat opens a new request (apps/web,
                # repositories/verifications.ts). So the claim is cleared, and the request is sent again below.
                discard_step(store, identifier, "submit", {"kind": "remote_read", "locator": locator, "status": 404})
        if response is None and task is None:
            report, _ = validate_submission(store, binding["body"])
            if report["bundle"]["digest"] != binding["bundle_digest"]:
                raise ResearchError("publication_identifier_mismatch", "The current manuscript differs from the saved submission intent")
            validate_scope_binding(report["bundle"], binding.get("publication_scope"))
            response = remote_step(store, identifier, "submit", binding["body"],
                lambda: client("POST", "/api/v1/verifications", binding["body"])[0], expected_revision=report["revision"])
        if task is None:
            task = _find_task(client, locator)
        if task is None:
            _observation(store, identifier, "ingest_pending", {"locator": locator})
            raise ResearchError("verification_ingest_pending", "The remote request is recorded, but its task is not yet ingested; resume this intent with task-only reads",
                                {"intent_id": identifier})
        identity = task_identity(task)
        if identity["source"] != "zenodo" or identity["source_id"] != str(publication["deposition_id"]):
            _observation(store, identifier, "different_record", task)
            raise ResearchError("verification_target_mismatch", "The existing open task names a different published version. Waiting or repeating the request does not retarget that task",
                                {"intent_id": identifier, "actual_task": identity, "deposition_id": publication["deposition_id"]})
        if get_intent(store, identifier)["pending"] is not None:
            response = resolve_step(store, identifier, "submit", {"verificationId": task["verificationId"], "doi": task["doi"]},
                                    {"kind": "task_only_read", "task": task},
                                    answer_members=ANSWER_MEMBERS[("submit", "task_read")],
                                    busy_msg=REMOTE_READ_STORE_BUSY_MSG)
        receipt = {"intent_id": identifier, "bundle_digest": binding["bundle_digest"], "publication": publication,
                   "request_body": binding["body"], "request_response": response, "task": task,
                   "task_identity": identity, "record_doi": publication["doi"], "canonical_doi": task["doi"], "matched": True}
        if binding.get("publication_scope") is not None:
            receipt["publication_scope"] = binding["publication_scope"]
        return finish_intent(store, identifier, "submission_receipt", receipt)


def _find_task(client, locator):
    """The task that the service shows at a DOI, or None when it shows none. The slash of a DOI stays a separator of the
    path."""
    task, status = client("GET", "/api/v1/tasks/" + quote(locator, safe="/"), allow_missing=True)
    return None if status == 404 else task


@contextmanager
def _hold_verification(store, verification_id):
    """Own the verdict decisions and writes of one verification in this process. Ownership covers the target-level
    decision before any distinct intent can be prepared: per-intent ownership alone permits two concurrent senders."""
    try:
        with _owner(store.root, "verdict:" + verification_id):
            yield
    except ResearchError as error:
        if error.code != "execution_live":
            raise
        raise ResearchError("remote_operation_owned", "Another verdict sender owns this verification; wait for its recorded outcome before retrying",
                            {"verification_id": verification_id}) from error


def _read_verdict_task(client, verification_id):
    return client("GET", "/api/v1/tasks/" + quote(verification_id, safe=""))[0]


def send_verdict(store, task, body, client, *, expected_revision=None, request_id):
    with _hold_verification(store, task["verificationId"]):
        if _list_pending_verdicts(store.snapshot()["records"], task["verificationId"]):
            # A pending step is decided by a task read that this process takes while it owns the verification. A read
            # taken before can predate an earlier request of this verification that the service applied since.
            task = _read_verdict_task(client, task["verificationId"])
        return _send_verdict(store, task, body, client,
            expected_revision=expected_revision, request_id=request_id)


def _fold_verdict_id(verdict_id):
    """A verdict ID in the letter case that a comparison of two IDs uses: a UUID names one verdict in either case."""
    return verdict_id.lower() if isinstance(verdict_id, str) else verdict_id


def _is_predecessor_current(task, body):
    """Whether the task shows the verdict that the body supersedes as the current verdict of the account (for a first
    verdict: that the account has none). The service keeps one current verdict of an account on a verification and
    files a verdict only when this holds (apps/web, repositories/verdicts.ts, insertVerdict). While it holds for a
    request that was sent, the service holds no verdict from that request."""
    return _fold_verdict_id(task.get("viewerVerdictId")) == _fold_verdict_id(body.get("supersedesVerdictId"))


def require_current_predecessor(task, body):
    """The check of the direct path before its POST: a verdict that the service refuses is not sent."""
    if not _is_predecessor_current(task, body):
        raise ResearchError("verdict_revision_required", VERDICT_PREDECESSOR_NOT_CURRENT_MSG,
                            {"verificationId": task["verificationId"], "viewerVerdictId": task.get("viewerVerdictId"),
                             "supersedesVerdictId": body.get("supersedesVerdictId")})


def check_verdict_preconditions(store, task, body):
    """Every check the managed path runs before its remote write.

    Returns the validation report and the intent binding. Raises ResearchError
    when the workspace cannot record this verdict as it stands; the CLI then
    sends the verdict directly. A pending step of this verdict while the task
    shows a newer verdict of the account passes without the other checks, with
    _UNRECORDED_ANSWER_CHECK_RESULT: the managed path owns that request, and
    _send_verdict ends the command."""
    records = store.snapshot()["records"]
    if _find_unrecorded_answer(records, task, body) is not None:
        return _UNRECORDED_ANSWER_CHECK_RESULT
    report = validate_verdict(store, task, body)
    binding = {"assessment": report["assessment"], "body": report["body"], "task_identity": task_identity(task),
               "verification_id": task["verificationId"],
               "preparation_digest": report["preparation_digest"]}
    prior = [r for r in records.get("remote_intent", {}).values()
             if r["kind"] == "verdict" and r["binding"]["verification_id"] == task["verificationId"]]
    # An intent of this binding that holds its answer is finished without a request. Every other verdict is sent, so it
    # names the latest known verdict of the account.
    if not any(r["binding"] == binding and r["responses"] for r in prior):
        current_id = task.get("viewerVerdictId")
        confirmed = [(r["created_revision"], records["verdict_receipt"][r["id"]]["response"].get("id"))
                     for r in prior if r["status"] == "complete" and r["id"] in records.get("verdict_receipt", {})]
        if confirmed and (current_id is None or current_id in {item[1] for item in confirmed}):
            current_id = max(confirmed, key=lambda item: item[0])[1]
            if not isinstance(current_id, str) or not current_id:
                raise ResearchError("verdict_reconciliation_pending", "The known prior success needs an exact own verdict ID before an explicit revision")
        if _fold_verdict_id(current_id) != _fold_verdict_id(body.get("supersedesVerdictId")):
            raise ResearchError("verdict_revision_required", "A new verdict revision must explicitly name the latest known own verdict ID")
    return report, binding


def _list_pending_verdicts(records, verification_id):
    return [r for r in records.get("remote_intent", {}).values()
            if r["kind"] == "verdict" and r["binding"]["verification_id"] == verification_id
            and r["pending"] is not None]


def _find_unrecorded_answer(records, task, body):
    """The intent whose pending step is this exact verdict while the task shows a newer verdict of the account, or
    None."""
    return next((r for r in _list_pending_verdicts(records, task["verificationId"])
                 if r["pending"]["request"] == body and not _is_predecessor_current(task, body)), None)


def _refuse_unrecorded_answer(store, task, body):
    """End the command for a pending step of this exact verdict while the task shows a newer verdict of the account:
    the service files the verdict no second time, and the task cannot show which body the newer verdict has. The store
    keeps what the task showed."""
    unrecorded = _find_unrecorded_answer(store.snapshot()["records"], task, body)
    if unrecorded is not None:
        _observation(store, unrecorded["id"], "verdict_response_unknown",
                     {"verificationId": task["verificationId"], "viewerVerdictId": task.get("viewerVerdictId")})
        raise ResearchError("verdict_reconciliation_pending", VERDICT_ANSWER_NOT_RECORDED_MSG,
                            {"intent_id": unrecorded["id"], "step": unrecorded["pending"]["name"],
                             "verificationId": task["verificationId"], "viewerVerdictId": task.get("viewerVerdictId")})


def _release_unapplied_verdicts(store, task):
    """Clear each pending verdict step of this verification whose predecessor the task still shows as current: the
    service holds no verdict from such a request, so the next send of its verdict starts the step again. The caller
    owns the verification, so no other process of this workspace sends such a request now."""
    for intent in _list_pending_verdicts(store.snapshot()["records"], task["verificationId"]):
        if _is_predecessor_current(task, intent["pending"]["request"]):
            discard_step(store, intent["id"], intent["pending"]["name"],
                         {"kind": "remote_read", "viewerVerdictId": task.get("viewerVerdictId")})


def record_verdict_answer(store, identifier, verdict_id, client):
    """Record the answer of a pending verdict step from the verdict ID that the caller reports, and the receipt, with no
    request. The ID is the id of the answer that an earlier refusal carried, or the verdict that the caller compared on
    the page of the verification. The task, read while this process owns the verification, shows that ID as the current
    verdict of the account and as a newer one than the verdict that the pending request supersedes. The intent keeps
    that the ID was reported. An intent whose recorded answer has this ID gets its receipt."""
    verification_id = get_intent(store, identifier)["binding"]["verification_id"]
    with _hold_verification(store, verification_id), _owner(store.root, "remote:" + identifier):
        records = store.snapshot()["records"]
        receipt = records.get("verdict_receipt", {}).get(identifier)
        if receipt is not None and _fold_verdict_id(receipt["response"].get("id")) == _fold_verdict_id(verdict_id):
            return receipt
        intent = records["remote_intent"][identifier]
        pending, binding = intent["pending"], intent["binding"]
        response = intent["responses"].get("verdict", {}).get("response")
        task = _read_verdict_task(client, verification_id)
        if (pending is not None and _fold_verdict_id(task.get("viewerVerdictId")) == _fold_verdict_id(verdict_id)
                and not _is_predecessor_current(task, pending["request"])):
            response = resolve_step(store, identifier, pending["name"], {"id": task["viewerVerdictId"]},
                                    {"kind": "reported_answer",
                                     "task": {"verificationId": verification_id,
                                              "viewerVerdictId": task["viewerVerdictId"]}},
                                    answer_members=("id",), busy_msg=REMOTE_READ_STORE_BUSY_MSG)
        elif (pending is not None or response is None
              or _fold_verdict_id(response.get("id")) != _fold_verdict_id(verdict_id)):
            raise ResearchError("remote_reconciliation_conflict", VERDICT_ID_NOT_CURRENT_MSG,
                                {"intent_id": identifier, "viewerVerdictId": task.get("viewerVerdictId"),
                                 "pending": pending is not None})
        return finish_intent(store, identifier, "verdict_receipt", {"intent_id": identifier, "body": binding["body"],
            "assessment_digest": binding["assessment"]["digest"], "task": task, "response": response})


def _send_verdict(store, task, body, client, *, expected_revision=None, request_id):
    _refuse_unrecorded_answer(store, task, body)
    report, binding = check_verdict_preconditions(store, task, body)
    intent = begin_intent(store, "verdict", binding, expected_revision=expected_revision, request_id=request_id)
    with _owner(store.root, "remote:" + intent["id"]):
        current = get_intent(store, intent["id"])
        if current["status"] == "complete":
            return store.snapshot()["records"]["verdict_receipt"][intent["id"]]
        _release_unapplied_verdicts(store, task)
        validate_verdict(store, task, body)
        response = remote_step(store, intent["id"], "verdict", body,
            lambda: client("POST", "/api/v1/verifications/" + quote(task["verificationId"], safe="") + "/verdicts", body)[0])
        return finish_intent(store, intent["id"], "verdict_receipt", {"intent_id": intent["id"], "body": body,
            "assessment_digest": report["assessment"]["digest"], "task": task, "response": response})
