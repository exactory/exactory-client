"""Deposit reviewed immutable bytes with persisted remote-step reconciliation."""

import hashlib

from .artifacts import ArtifactStore
from .errors import ResearchError
from .execution import _owner
from .integration import export_workspace
from .publication import publication_report
from .remote import begin_intent, finish_intent, get_intent, remote_step, resolve_step


def _current(store, binding):
    report = publication_report(store)
    if not report["ready"] or report["bundle"]["digest"] != binding["bundle_digest"]:
        raise ResearchError("readiness_required", "The current manuscript and dual reviews must match the saved publication intent",
                            {"obligations": report["obligations"]})
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
        candidates = []
        for page in range(1, 4):
            rows = client("GET", base + "/deposit/depositions?page=" + str(page) + "&size=100")
            if not isinstance(rows, list):
                raise ResearchError("remote_reconciliation_required", "The deposition listing did not supply a complete checked page")
            candidates.extend(r for r in rows if r.get("metadata", {}).get("notes") == token)
            if len(rows) < 100:
                break
        if len(candidates) == 1:
            observed = candidates[0]
    elif deposition is not None:
        record_id = deposition["id"]
        if name == "preview":
            candidate = client("GET", base + "/records/" + str(record_id) + "/draft")
            if candidate.get("files", {}).get("default_preview") == "paper.pdf":
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
    if observed is None:
        raise ResearchError("remote_reconciliation_required", "Remote reads have not established the pending mutation's exact outcome; no duplicate write was sent",
                            {"request_id": identifier, "pending": pending})
    resolve_step(store, identifier, name, observed, {"kind": "remote_read", "pending_request": pending["request"]})
    return get_intent(store, identifier)


def continue_deposit(store, identifier, client, preview, *, reconcile=False):
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
        remote_step(store, identifier, "preview", {"deposition_id": deposition["id"], "filename": "paper.pdf"},
                    lambda: preview(base, deposition["id"]))
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


def deposit(store, binding, client, preview, *, expected_revision, request_id):
    _current(store, binding)
    if binding["new_version"] and (binding["prior"] is None or binding["prior"]["environment"] != binding["environment"]):
        raise ResearchError("publication_prior_required", "A revised deposit must use the prior concrete record in the same environment")
    intent = begin_intent(store, "deposit", binding, expected_revision=expected_revision, request_id=request_id)
    return continue_deposit(store, intent["id"], client, preview)
