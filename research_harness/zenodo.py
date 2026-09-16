"""Deposit reviewed immutable bytes with persisted remote-step reconciliation."""

import hashlib

from .artifacts import ArtifactStore
from .errors import ResearchError
from .execution import _owner
from .evaluation import Evaluation
from .gates import require_ready
from .integration import export_workspace
from .publication import publication_state, validate_upload
from .remote import begin_intent, discard_step, finish_intent, get_intent, remote_step, resolve_step
from .rounds import round_state


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
    records = store.snapshot()["records"]
    evaluation = Evaluation(records, ArtifactStore(store.root))
    report = publication_state(records, evaluation)
    if not report["ready"] or report["bundle"]["digest"] != binding["bundle_digest"]:
        raise ResearchError("readiness_required", "The current manuscript and dual reviews must match the saved publication intent",
                            {"obligations": report["obligations"]})
    require_ready(round_state(records, evaluation), "depositing the final development round")
    return report["bundle"]


def _deposition(intent):
    return intent["responses"].get("create", {}).get("response")


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
            raise ResearchError("remote_reconciliation_required", "The new-version response was lost before its record ID was captured; no repeat creation was sent",
                                {"request_id": identifier, "prior_deposition_id": binding["prior"]["deposition_id"]})
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
            if name.startswith("upload:"):
                selected = dict(binding, uploads=[u for u in binding["uploads"] if u["name"] == name[7:]])
                if _files_match(candidate, selected, ArtifactStore(store.root)):
                    observed = candidate
            elif name == "metadata" and all(candidate.get("metadata", {}).get(k) == v for k, v in pending["request"]["metadata"].items()):
                observed = candidate
            elif name == "publish" and candidate.get("submitted") is True and candidate.get("doi") and _files_match(candidate, binding, ArtifactStore(store.root)):
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
    _current(store, binding)
    return remote_step(store, identifier, "preview", {"deposition_id": record_id, "filename": PAPER_UPLOAD_NAME},
                       lambda: client("PUT", url, json_body=updated))


def continue_deposit(store, identifier, client, *, reconcile=False):
    with _owner(store.root, "remote:" + identifier):
        intent = get_intent(store, identifier)
        if intent["status"] == "complete":
            export_workspace(store)
            return store.snapshot()["records"]["publication_receipt"][identifier]
        if reconcile:
            intent = reconcile_pending(store, identifier, client)
        binding = intent["binding"]
        _current(store, binding)
        base = binding["base_url"]
        metadata = dict(binding["metadata"], notes="Exactory publication intent " + identifier)
        if binding["new_version"]:
            prior = binding["prior"]
            if prior is None or prior["environment"] != binding["environment"]:
                raise ResearchError("publication_prior_required", "A revised deposit must use the prior concrete record in the same environment")
            def create():
                response = client("POST", base + "/deposit/depositions/" + str(prior["deposition_id"]) + "/actions/newversion")
                draft = response.get("links", {}).get("latest_draft")
                if not draft:
                    raise ResearchError("remote_reconciliation_required", "New-version response omitted its latest_draft link")
                return client("GET", draft)
        else:
            def create():
                return client("POST", base + "/deposit/depositions", json_body={"metadata": metadata})
        deposition = remote_step(store, identifier, "create", {"metadata": metadata, "new_version": binding["new_version"], "prior": binding["prior"]}, create)
        bucket = deposition["links"]["bucket"]
        artifacts = ArtifactStore(store.root)
        for item in binding["uploads"]:
            _current(store, binding)
            data = artifacts.read(item["artifact"])
            remote_step(store, identifier, "upload:" + item["name"], item,
                        lambda item=item, data=data: client("PUT", bucket + "/" + item["name"], file_bytes=data))
        _current(store, binding)
        remote_step(store, identifier, "metadata", {"metadata": metadata},
                    lambda: client("PUT", base + "/deposit/depositions/" + str(deposition["id"]), json_body={"metadata": metadata}))
        _current(store, binding)
        _set_preview(store, identifier, binding, client, deposition["id"])
        published = None
        if binding["publish"]:
            _current(store, binding)
            published = remote_step(store, identifier, "publish", {"deposition_id": deposition["id"]},
                lambda: client("POST", base + "/deposit/depositions/" + str(deposition["id"]) + "/actions/publish"))
        state = {"environment": binding["environment"], "deposition_id": deposition["id"], "draft_url": deposition.get("links", {}).get("html", "")}
        if published is not None:
            if not published.get("doi") or str(published.get("id")) != str(deposition["id"]):
                raise ResearchError("remote_reconciliation_required", "The publish response has not established the concrete version's DOI")
            state.update(doi=published["doi"], concept_doi=published.get("conceptdoi", ""),
                         record_url=published.get("links", {}).get("record_html", ""))
        receipt = dict(state, intent_id=identifier, bundle_digest=binding["bundle_digest"], uploads=binding["uploads"],
                       published_response=published, prepared_revision=binding["prepared_revision"])
        result = finish_intent(store, identifier, "publication_receipt", receipt, workspace=state)
        export_workspace(store)
        return result


def validate_deposit(store, pdf, abstract, sources, *, new_version, environment):
    """Every check the managed deposit runs before its first remote write.

    Raises ResearchError when the workspace cannot record this deposit; the
    CLI then deposits directly."""
    report = validate_upload(store, pdf, abstract, sources)
    records = store.snapshot()["records"]
    require_ready(round_state(records, Evaluation(records, ArtifactStore(store.root))),
                  "depositing the final development round")
    prior = records.get("workspace", {}).get("deposit")
    if new_version and (prior is None or prior["environment"] != environment):
        raise ResearchError("publication_prior_required", "A revised deposit must use the prior concrete record in the same environment")
    return report


def deposit(store, binding, client, *, expected_revision, request_id):
    _current(store, binding)
    if binding["new_version"] and (binding["prior"] is None or binding["prior"]["environment"] != binding["environment"]):
        raise ResearchError("publication_prior_required", "A revised deposit must use the prior concrete record in the same environment")
    intent = begin_intent(store, "deposit", binding, expected_revision=expected_revision, request_id=request_id)
    # An interrupted deposit continues through remote reads: a fresh intent has
    # no claimed step, so reconciliation returns at once and nothing changes.
    return continue_deposit(store, intent["id"], client, reconcile=True)
