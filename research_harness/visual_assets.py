"""Acquire original static HTML figure assets through the shared transport.

acquire_visual_asset(store, link, url, *, request_id, expected_revision,
                     http=None, max_requests=None)
The link locates the corresponding acquired original HTML. url must be an actual
resolved resource in that visual's saved markup. The result retains all redirect
and failure sources plus capture and asset_link: {url, source_id, artifact}|None.
Put available asset links in the HTML locator's assets array, then import the
extended bundle and record inspections that include those exact saved bytes.

visual_asset/{request_id} is immutable capture history. visual_asset_selection
selects the latest attempt for (exact work, original HTML SHA, resolved URL).
New bytes or a failed current attempt leave earlier readings partial; identical
bytes from a new receipt may be reused. Images and self-contained SVG are
supported. This performs no browsing, OCR, rendering or execution.
"""

import copy

from .acquisition import _begin, _finish
from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest, media_type, prepare_sources, put_sources
from .html_visuals import VISUAL_TYPES, resource_inventory, validate_visual
from .http import HttpClient, HttpFailure, RequestBudget, safe_url
from .operations import fields, text


def asset_key(version_id, original_sha256, url):
    return digest([version_id, original_sha256, url])


def current_asset(records, version_id, original_sha256, url):
    selection = records.get("visual_asset_selection", {}).get(asset_key(version_id, original_sha256, url))
    return records.get("visual_asset", {}).get(selection["asset_id"]) if selection else None


def asset_dependencies(records, version_id, original_sha256, resources):
    result = []
    for resource in resources:
        asset = current_asset(records, version_id, original_sha256, resource["url"])
        result.append({"url": resource["url"], "availability": asset["availability"] if asset else "missing",
                       "sha256": asset["artifact"]["sha256"] if asset and asset["artifact"] else None,
                       "validation_status": asset["validation_status"] if asset else None})
    return result


def visual_context(records, artifacts, link, source, capture):
    from .source_links import captured_source
    if capture is None or link["artifact"] != capture["original"]:
        raise ResearchError("invalid_locator", "An HTML visual must locate its acquired original document")
    inventory = resource_inventory(artifacts.read(link["artifact"]).decode("utf-8"), source["url"], link["locator"]["anchor"])
    urls = {r["url"] for r in inventory["resources"]}
    assets = link["locator"].get("assets", [])
    if not isinstance(assets, list):
        raise ResearchError("invalid_visual_asset", "Visual assets must be an array of captured byte links")
    linked = {}
    for asset in assets:
        fields(asset, ("url", "source_id", "artifact"), code="invalid_visual_asset")
        url = safe_url(asset["url"])
        if url not in urls or url in linked:
            raise ResearchError("invalid_visual_asset", "Each visual asset must match a distinct resource in the selected original markup")
        saved = captured_source(records, artifacts, asset["source_id"])
        captured = records.get("visual_asset", {}).get(saved["operation_id"])
        if (captured is None or captured["source_id"] != saved["id"] or captured["version_id"] != link["version_id"]
                or captured["html_sha256"] != capture["original"]["sha256"] or captured["url"] != url
                or captured["artifact"] != asset["artifact"] or asset["artifact"] != saved["response"]
                or captured["availability"] != "available" or saved["capture_method"] != "http" or saved["origin_verified"] is not True):
            raise ResearchError("invalid_visual_asset", "Visual bytes must be acquired for this exact work and original HTML resource")
        artifacts.read(asset["artifact"])
        linked[url] = asset
    pending = list(inventory["pending"])
    for url in sorted(urls):
        current = current_asset(records, link["version_id"], capture["original"]["sha256"], url)
        if current is None:
            pending.append({"code": "visual_asset_missing", "url": url})
        elif current["availability"] != "available":
            pending.append({"code": "visual_asset_pending", "url": url, "reason": current["validation_status"],
                            "source_ids": current["source_ids"]})
        elif url not in linked:
            pending.append({"code": "visual_asset_unlinked", "url": url, "path": current["artifact"]["path"]})
        elif linked[url]["artifact"]["sha256"] != current["artifact"]["sha256"]:
            pending.append({"code": "visual_asset_stale", "url": url, "path": current["artifact"]["path"]})
    return dict(inventory, pending=pending)


def acquire_visual_asset(store, link, url, *, request_id, expected_revision, http=None, max_requests=None):
    from .source_links import complete_original, validate_link
    text(request_id, "Request ID")
    url, budget = safe_url(url), RequestBudget(max_requests)
    payload = {"link": copy.deepcopy(link), "url": url, "max_requests": max_requests}
    link = payload["link"]
    snapshot = store.snapshot()
    if request_id in snapshot["records"].get("acquisition_operation", {}):
        return _begin(store, "visual_asset.acquire", payload, request_id, expected_revision)
    artifacts = ArtifactStore(store.root)
    context = validate_link(snapshot["records"], artifacts, link)
    if not complete_original(context) or link["locator"]["kind"] != "html":
        raise ResearchError("invalid_visual_asset", "An asset must belong to an acquired original HTML visual")
    inventory = context["visual"]
    if url not in {r["url"] for r in inventory["resources"]}:
        raise ResearchError("invalid_visual_asset", "The requested URL is not a saved resource of the selected HTML visual")
    replay = _begin(store, "visual_asset.acquire", payload, request_id, expected_revision, target=link["version_id"])
    if replay is not None:
        return replay
    snapshot = store.snapshot()
    attempts, pending, final_url = [], [], None
    try:
        response = (http or HttpClient()).get(url, accept=VISUAL_TYPES, budget=budget)
        attempts, final_url = response.attempts, response.url
        validate_visual(response.body, media_type(response.headers))
    except HttpFailure as error:
        attempts, pending = error.attempts, [{"code": error.code}]
    except ResearchError as error:
        pending = [{"code": error.code}]
    sources = prepare_sources(artifacts, attempts, "visual_asset", request_id, requested_identifier=link["version_id"])
    capture = {"id": request_id, "version_id": link["version_id"], "html_source_id": link["source_id"],
               "html_sha256": context["capture"]["original"]["sha256"], "url": url, "final_url": final_url,
               "source_id": sources[-1]["id"] if sources else None, "source_ids": [s["id"] for s in sources],
               "artifact": sources[-1]["response"] if sources else None, "availability": "pending" if pending else "available",
               "validation_status": pending[0]["code"] if pending else "verified_container", "pending": pending}

    def commit(transaction):
        put_sources(transaction, sources)
        transaction.put("visual_asset", request_id, capture)
        transaction.put("visual_asset_selection", asset_key(link["version_id"], capture["html_sha256"], url), {"asset_id": request_id})
        work = transaction.get("work", link["version_id"])
        work.setdefault("visual_asset_ids", []).append(request_id)
        transaction.put("work", link["version_id"], work)

    return _finish(store, request_id, snapshot["revision"], {"status": "pending" if pending else "complete",
        "scope": "visual_asset_acquisition", "pending": pending, "capture": capture, "attempts_used": budget.used,
        "asset_link": {"url": url, "source_id": capture["source_id"], "artifact": capture["artifact"]} if not pending else None}, apply=commit)
