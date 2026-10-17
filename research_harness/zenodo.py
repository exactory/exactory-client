"""Deposit reviewed immutable bytes with persisted remote-step reconciliation."""

from contextlib import contextmanager
import hashlib

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .execution import _owner
from .evaluation import Evaluation
from .gates import require_ready
from .integration import export_workspace
from .operations import immutable_record, prepared_mutation
from .publication import publication_state, validate_upload
from .remote import begin_intent, discard_step, finish_intent, get_intent, remote_step, resolve_step
from .rounds import round_state
from .publication_scope import validate_scope_binding


# The paper always lands on a record under this name, whatever the local build
# called it, so every record presents the same face and every step that names
# the paper to Zenodo names the same file.
PAPER_UPLOAD_NAME = "paper.pdf"
# The record's previewed file is set on its RDM draft document: the file-sort
# endpoint that https://developers.zenodo.org still documents answers HTTP 405
# on zenodo.org (verified 2026-08-30).
RDM_JSON_MEDIA_TYPE = "application/vnd.inveniordm.v1+json"


def build_draft_document_url(base_url, record_id):
    """The URL of one record's RDM draft document."""
    return base_url + "/records/" + str(record_id) + "/draft"


def build_previewed_document(document):
    """Return the fetched RDM draft document with the paper as its previewed file.

    The RDM PUT replaces the whole draft document, so every other field the
    fetched document carries goes back unchanged."""
    return dict(document, files=dict(document.get("files", {}), default_preview=PAPER_UPLOAD_NAME))


def _current(store, binding):
    snapshot = store.snapshot()
    records = snapshot["records"]
    evaluation = Evaluation(records, ArtifactStore(store.root))
    report = publication_state(records, evaluation)
    if not report["ready"] or report["bundle"]["digest"] != binding["bundle_digest"]:
        raise ResearchError("readiness_required", "The current manuscript and dual reviews must match the saved publication intent",
                            {"obligations": report["obligations"]})
    validate_scope_binding(report["bundle"], binding.get("publication_scope"))
    require_ready(round_state(records, evaluation), "depositing the final development round")
    return snapshot["revision"]


def _deposition(intent):
    return intent["responses"].get("create", {}).get("response")


def _is_published_deposition(answer, record_id):
    return (isinstance(answer, dict) and str(answer.get("id")) == str(record_id)
            and answer.get("submitted") is True and isinstance(answer.get("doi"), str) and bool(answer["doi"].strip()))


def _require_published_deposition(answer, record_id):
    """Validate before confirming a publish claim or recording its publication."""
    if _is_published_deposition(answer, record_id):
        return answer
    raise ResearchError("remote_reconciliation_required",
                        f"Zenodo has not confirmed that deposition {record_id} is published: the answer must name"
                        f" deposition {record_id} and carry submitted true and a DOI. The publish step stays"
                        f" unresolved, and the next run reads deposition {record_id} back from Zenodo before any"
                        " other write.")


def _recover_published_deposition(store, intent, client, *, reconcile):
    """Return the publication of an intent whose publish step is confirmed. An earlier version
    confirmed the step before it checked the answer, so a saved answer that does not show the
    publication is read back from the same deposition, and the saved answer stays as it is."""
    published = intent["responses"].get("publish", {}).get("response")
    record_id = _deposition(intent)["id"]
    if not _is_published_deposition(published, record_id):
        if reconcile:
            published = client("GET", intent["binding"]["base_url"] + "/deposit/depositions/" + str(record_id))
        _require_published_deposition(published, record_id)
        if not _files_match(published, intent["binding"], ArtifactStore(store.root)):
            raise ResearchError("remote_reconciliation_required",
                                f"Deposition {record_id} is published, but its files on Zenodo do not match the"
                                " uploads of this deposit. The deposit stays unresolved, and no further write was sent.")
    return published


def _record_read_back_publication(store, identifier, receipt, published):
    """Record a publication that a read of the same deposition established for a receipt without a
    DOI. An earlier version completed such a receipt from a publish answer of JSON null. That receipt
    stays as it was written. A read-back receipt of the same intent holds the read publication for
    the same bundle, so the publication gate and the submission find it, and the workspace record
    takes the publication while it still names this deposition without a DOI."""
    state = dict({key: receipt[key] for key in ("environment", "deposition_id", "draft_url")},
                 doi=published["doi"], concept_doi=published.get("conceptdoi", ""),
                 record_url=published.get("links", {}).get("record_html", ""))
    read_back = dict(receipt, **state, published_response=published)

    def prepare(records, value):
        changes = [immutable_record(records, "publication_read_back", identifier, value["receipt"])]
        current = records.get("workspace", {}).get("deposit", {})
        # A newer deposit's workspace record stays as it is.
        if (current.get("environment") == state["environment"] and current.get("deposition_id") == state["deposition_id"]
                and not current.get("doi")):
            changes.append(("workspace", "deposit", value["deposit"]))
        return changes, value["receipt"]

    prepared_mutation(store, "publication.read_back", {"receipt": read_back, "deposit": state}, prepare,
                      expected_revision=store.revision, request_id=identifier + ":read-back:" + digest(read_back))
    return read_back


def has_published_record(deposit_state):
    """Whether a recorded deposit is a published record. A deposition has a DOI only once it is
    published (the `doi` field of https://developers.zenodo.org, checked 2026-10-14), so a record
    without one is a draft, or a direct deposit whose publish has not been confirmed."""
    return bool(deposit_state.get("doi"))


def require_published_prior(prior, environment):
    """The record that a new version revises: the prior concrete record, which is a published
    deposit in the same environment. A prior that is not published is refused with its
    deposition named, and the managed and the direct path read it through this one rule."""
    if prior is None or prior["environment"] != environment:
        raise ResearchError("publication_prior_required", "A revised deposit must use the prior concrete record in the same environment")
    if not has_published_record(prior):
        instruction_msg = (f"Open {prior['draft_url']} and check its state." if prior.get("draft_url")
                           else "Check its state on Zenodo.")
        raise ResearchError("publication_prior_required",
                            f"The prior deposit, deposition {prior['deposition_id']}, has no recorded DOI. {instruction_msg}",
                            {"deposition_id": prior["deposition_id"]})


def has_claimed_publish(intent):
    """Whether a deposit intent ever claimed its publish step: the claim is pending, its response
    is confirmed, or a remote read found the record unpublished and discarded the claim. From
    that claim on, the deposit stays bound to its own record until that record is published."""
    return ("publish" in intent["responses"] or (intent["pending"] or {}).get("name") == "publish"
            or any(entry["name"] == "publish" for entry in intent.get("discarded", [])))


def _files_match(document, binding, artifacts):
    saved = {item.get("filename", item.get("key")): item for item in document.get("files", [])}
    for item in binding["uploads"]:
        remote = saved.get(item["name"])
        expected = hashlib.md5(artifacts.read(item["artifact"]), usedforsecurity=False).hexdigest()
        if remote is None or remote.get("checksum", "").removeprefix("md5:") != expected:
            return False
    return True


def reconcile_pending(store, identifier, client):
    intent = get_intent(store, identifier)
    pending, binding = intent["pending"], intent["binding"]
    if pending is None:
        return intent
    base, name = binding["base_url"], pending["name"]
    deposition = _deposition(intent)
    observed = None
    if name == "create":
        if binding["new_version"]:
            # The lost response carried the new version's record ID, so no read
            # names that record. https://developers.zenodo.org states that the
            # new-version action has no effect while the draft of the first call
            # is unpublished, so the claim is cleared and the caller sends that
            # same action again: it answers with the draft the first call left,
            # and the published record it revises stays as it is.
            discard_step(store, identifier, name, {"kind": "repeat_without_effect", "action": "newversion",
                                                   "prior_deposition_id": binding["prior"]["deposition_id"]})
            return get_intent(store, identifier)
        token = "Exactory publication intent " + identifier
        candidates, is_listing_complete = [], False
        for page in range(1, 4):
            rows = client("GET", base + "/deposit/depositions?page=" + str(page) + "&size=100")
            if not isinstance(rows, list):
                raise ResearchError("remote_reconciliation_required", "The deposition listing did not supply a complete checked page")
            candidates.extend(r for r in rows if r.get("metadata", {}).get("notes") == token)
            if len(rows) < 100:
                is_listing_complete = True
                break
        if len(candidates) == 1:
            observed = candidates[0]
        elif not candidates and is_listing_complete:
            # Every deposition of this account was read and none carries the
            # intent's token, so the creation never landed. The claim is cleared
            # and the caller creates the record.
            discard_step(store, identifier, name, {"kind": "remote_read", "listing": "complete", "notes": token})
            return get_intent(store, identifier)
    elif deposition is not None:
        record_id = deposition["id"]
        if name == "preview":
            candidate = client("GET", build_draft_document_url(base, record_id))
            if candidate.get("files", {}).get("default_preview") == PAPER_UPLOAD_NAME:
                observed = candidate
        else:
            candidate = client("GET", base + "/deposit/depositions/" + str(record_id))
            # A read that names another deposition says nothing about this publish.
            if name == "publish" and (not isinstance(candidate, dict) or str(candidate.get("id")) != str(record_id)):
                _require_published_deposition(candidate, record_id)
            if name.startswith("upload:"):
                selected = dict(binding, uploads=[u for u in binding["uploads"] if u["name"] == name[7:]])
                if _files_match(candidate, selected, ArtifactStore(store.root)):
                    observed = candidate
            elif name == "metadata" and all(candidate.get("metadata", {}).get(k) == v for k, v in pending["request"]["metadata"].items()):
                observed = candidate
            elif name == "publish" and _is_published_deposition(candidate, record_id) and _files_match(candidate, binding, ArtifactStore(store.root)):
                observed = candidate
        # Each step above writes to the record this read returned, so a read
        # with no trace of the step establishes that it never landed: the claim
        # is cleared and the caller sends that one step to that same record.
        # The publish reads its own evidence of absence, the `submitted` status
        # of https://developers.zenodo.org, because a submitted record carrying
        # another DOI or other files is an anomaly that no repeat settles.
        if observed is None and (name != "publish" or candidate.get("submitted") is False):
            discard_step(store, identifier, name, {"kind": "remote_read", "deposition_id": record_id})
            return get_intent(store, identifier)
    if observed is None:
        raise ResearchError("remote_reconciliation_required", "Remote reads have not established the pending mutation's exact outcome; no duplicate write was sent",
                            {"request_id": identifier, "pending": pending})
    resolve_step(store, identifier, name, observed, {"kind": "remote_read", "pending_request": pending["request"]})
    return get_intent(store, identifier)


def _set_preview(store, identifier, binding, client, record_id):
    """Fetch the draft before claiming the preview write, then validate its bundle."""
    intent = get_intent(store, identifier)
    if "preview" in intent["responses"]:
        return intent["responses"]["preview"]["response"]
    url = build_draft_document_url(binding["base_url"], record_id)
    updated = build_previewed_document(client("GET", url, accept=RDM_JSON_MEDIA_TYPE))
    checked_revision = _current(store, binding)
    return remote_step(store, identifier, "preview", {"deposition_id": record_id, "filename": PAPER_UPLOAD_NAME},
                       lambda: client("PUT", url, json_body=updated), expected_revision=checked_revision)


# The owner key of a workspace's deposit commands. Intents own "remote:<id>", verdict targets
# "verdict:<id>", and admissions their own ids, so this key names no other owner.
_WORKSPACE_DEPOSITS_OWNER_ID = "deposit:workspace"


@contextmanager
def own_workspace_deposits(root):
    """Own the deposit commands of the workspace at `root` for the caller's whole command.
    Per-intent ownership alone lets two runs act on one recorded deposit from different
    observations, so every command that reads a deposit's journal or intent, or starts a
    deposit, takes this first; continue_deposit takes the intent's own lock inside it, never
    before it. A second owner is refused with remote_operation_owned and sends nothing."""
    try:
        with _owner(root, _WORKSPACE_DEPOSITS_OWNER_ID):
            yield
    except ResearchError as error:
        if error.code != "execution_live":
            raise
        raise ResearchError("remote_operation_owned", "Another exactory-draft run owns the deposits of this workspace."
                            " Wait for that run to end. Then run this command again.") from error


def continue_deposit(store, identifier, client, *, reconcile=False, finish_only=False):
    with _owner(store.root, "remote:" + identifier):
        intent = get_intent(store, identifier)
        if intent["status"] == "complete":
            records = store.snapshot()["records"]
            receipt = records["publication_receipt"][identifier]
            if intent["binding"]["publish"] and not receipt.get("doi"):
                receipt = records.get("publication_read_back", {}).get(identifier) or _record_read_back_publication(
                    store, identifier, receipt, _recover_published_deposition(store, intent, client, reconcile=reconcile))
            export_workspace(store)
            return receipt
        if reconcile:
            intent = reconcile_pending(store, identifier, client)
        # A publication the intent has confirmed, by its response or by a remote read, is a
        # fact on Zenodo: it is recorded without the manuscript check, so an edited draft
        # cannot keep a published record unrecorded (design 2026-10-12 §17).
        if "publish" in intent["responses"]:
            published = _recover_published_deposition(store, intent, client, reconcile=reconcile)
            return _finish_deposit(store, identifier, intent, published=published)
        if finish_only:
            raise ResearchError("publication_unconfirmed",
                                "The record of this deposit is not published. Run exactory-draft deposit to finish it.",
                                {"request_id": identifier})
        binding = intent["binding"]
        checked_revision = _current(store, binding)
        base = binding["base_url"]
        metadata = dict(binding["metadata"], notes="Exactory publication intent " + identifier)
        if binding["new_version"]:
            prior = binding["prior"]
            require_published_prior(prior, binding["environment"])
            def create():
                response = client("POST", base + "/deposit/depositions/" + str(prior["deposition_id"]) + "/actions/newversion")
                draft = response.get("links", {}).get("latest_draft")
                if not draft:
                    raise ResearchError("remote_reconciliation_required", "New-version response omitted its latest_draft link")
                return client("GET", draft)
        else:
            def create():
                return client("POST", base + "/deposit/depositions", json_body={"metadata": metadata})
        deposition = remote_step(store, identifier, "create", {"metadata": metadata, "new_version": binding["new_version"], "prior": binding["prior"]}, create,
                                 expected_revision=checked_revision)
        bucket = deposition["links"]["bucket"]
        artifacts = ArtifactStore(store.root)
        for item in binding["uploads"]:
            checked_revision = _current(store, binding)
            data = artifacts.read(item["artifact"])
            remote_step(store, identifier, "upload:" + item["name"], item,
                        lambda item=item, data=data: client("PUT", bucket + "/" + item["name"], file_bytes=data), expected_revision=checked_revision)
        checked_revision = _current(store, binding)
        remote_step(store, identifier, "metadata", {"metadata": metadata},
                    lambda: client("PUT", base + "/deposit/depositions/" + str(deposition["id"]), json_body={"metadata": metadata}), expected_revision=checked_revision)
        _current(store, binding)
        _set_preview(store, identifier, binding, client, deposition["id"])
        if binding["publish"]:
            checked_revision = _current(store, binding)
            def publish():
                answer = client("POST", base + "/deposit/depositions/" + str(deposition["id"]) + "/actions/publish")
                return _require_published_deposition(answer, deposition["id"])
            remote_step(store, identifier, "publish", {"deposition_id": deposition["id"]},
                        publish, expected_revision=checked_revision)
        return _finish_deposit(store, identifier, get_intent(store, identifier))


def _finish_deposit(store, identifier, intent, *, published=None):
    """Record the receipt and the workspace deposit from the intent's confirmed responses."""
    binding = intent["binding"]
    deposition = _deposition(intent)
    if published is None:
        published = intent["responses"].get("publish", {}).get("response")
    state = {"environment": binding["environment"], "deposition_id": deposition["id"], "draft_url": deposition.get("links", {}).get("html", "")}
    if "publish" in intent["responses"]:
        _require_published_deposition(published, deposition["id"])
        state.update(doi=published["doi"], concept_doi=published.get("conceptdoi", ""),
                     record_url=published.get("links", {}).get("record_html", ""))
    receipt = dict(state, intent_id=identifier, bundle_digest=binding["bundle_digest"], uploads=binding["uploads"],
                   published_response=published, prepared_revision=binding["prepared_revision"])
    if binding.get("publication_scope") is not None:
        receipt["publication_scope"] = binding["publication_scope"]
    result = finish_intent(store, identifier, "publication_receipt", receipt, workspace=state)
    export_workspace(store)
    return result


def validate_deposit(store, pdf, abstract, sources, *, new_version, environment):
    """Every check the managed deposit runs before its first remote write.

    Raises ResearchError when the workspace cannot record this deposit. The
    managed-only CLI propagates that refusal before any remote write."""
    report = validate_upload(store, pdf, abstract, sources)
    records = store.snapshot()["records"]
    require_ready(round_state(records, Evaluation(records, ArtifactStore(store.root))),
                  "depositing the final development round")
    if new_version:
        require_published_prior(records.get("workspace", {}).get("deposit"), environment)
    return report


def begin_deposit(store, binding, *, expected_revision, request_id):
    """Check a managed deposit and save its intent before any remote write. The same request
    saved earlier returns that intent, so an interrupted deposit resumes on it."""
    _current(store, binding)
    if binding["new_version"]:
        require_published_prior(binding["prior"], binding["environment"])
    return begin_intent(store, "deposit", binding, expected_revision=expected_revision, request_id=request_id)


def deposit(store, binding, client, *, expected_revision, request_id):
    intent = begin_deposit(store, binding, expected_revision=expected_revision, request_id=request_id)
    # An interrupted deposit continues from its saved intent: a fresh intent has
    # no claimed step, so reconciliation returns at once and nothing changes.
    return continue_deposit(store, intent["id"], client, reconcile=True)
