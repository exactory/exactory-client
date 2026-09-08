"""One source-link boundary for reading, bibliography and later claim evidence.

A Link is {version_id, source_id, artifact: ArtifactRef, locator}. A text locator
is {kind: text, start, end, quote}, in Unicode character offsets. JSON locators
are {kind: json, pointer, value}. PDF visual locators are {kind: pdf, page_index,
printed_page: str|null, region: [x, y, width, height]}, using zero-based PDF pages
and normalized page coordinates. HTML visuals use {kind: html, anchor: TEXT,
assets?: [{url, source_id, artifact}]}. Assets are acquired original image bytes,
bound to actual resources of the selected saved HTML visual.

PDF bounds use the acquired extraction's form-feed page map, not printed page
numbers. No extraction/page map means no mechanically admitted PDF inspection.
Printed pagination is a retained assertion. Neither valid coordinates nor a
quoted passage prove comprehension or scientific truth.
"""

import math

from .errors import ResearchError
from .identities import normalize_identifier, version_of
from .imports import _pointer
from .operations import fields, text
from .providers import _json
from .storage import _canonical


def exact_work(records, identifier):
    identifier = normalize_identifier(identifier)
    work = records.get("work", {}).get(identifier)
    if work is None:
        raise ResearchError("unknown_work", "Acquire the exact work before linking its evidence", {"version_id": identifier})
    if identifier.startswith("arxiv:") and version_of(identifier) is None:
        raise ResearchError("missing_version", "A versionless arXiv observation is not an exact source version", {"version_id": identifier})
    return work


def captured_source(records, artifacts, source_id):
    text(source_id, "Source ID", code="source_mismatch")
    source = records.get("source", {}).get(source_id)
    if source is None:
        raise ResearchError("unknown_source", "Acquire or import the original source response first", {"source_id": source_id})
    if source.get("response") is not None:
        artifacts.read(source["response"])
    if source.get("status") != "captured" or source.get("response_complete") is not True or source.get("response") is None:
        raise ResearchError("source_pending", "A failed, partial or absent response cannot establish a reading",
                            {"source_id": source_id, "next_eligible_at": source.get("next_eligible_at")})
    return source


def fulltext_capture(work, source_id):
    return next((c for c in work.get("fulltexts", []) if c["source_id"] == source_id), None)


def read_locator(artifacts, artifact, locator, *, capture=None):
    data = artifacts.read(artifact)
    if not isinstance(locator, dict):
        raise ResearchError("invalid_locator", "A source locator is required")
    kind = locator.get("kind")
    if kind in ("text", "json"):
        try:
            content = data.decode("utf-8")
        except UnicodeError as error:
            raise ResearchError("invalid_locator", "Text locators require saved UTF-8 text") from error
        if kind == "text":
            fields(locator, ("kind", "start", "end", "quote"), code="invalid_locator")
            if (type(locator["start"]) is not int or type(locator["end"]) is not int
                    or not 0 <= locator["start"] < locator["end"] <= len(content)
                    or not isinstance(locator["quote"], str) or not locator["quote"].strip()
                    or content[locator["start"]:locator["end"]] != locator["quote"]):
                raise ResearchError("invalid_locator", "The quoted fragment must match its exact saved character span")
            return locator["quote"]
        fields(locator, ("kind", "pointer", "value"), code="invalid_locator")
        try:
            value = _pointer(_json(data, require_object=False), locator["pointer"])
        except ResearchError as error:
            raise ResearchError("invalid_locator", "The JSON locator does not identify a saved response value") from error
        if _canonical(value) != _canonical(locator["value"], "invalid_locator"):
            raise ResearchError("invalid_locator", "The JSON value differs from its saved response")
        return value
    if kind == "pdf":
        fields(locator, ("kind", "page_index", "printed_page", "region"), code="invalid_locator")
        if (artifact["media_type"] != "application/pdf" or not data.startswith(b"%PDF-")
                or capture is None or capture.get("original") != artifact or capture.get("text") is None):
            raise ResearchError("invalid_locator", "PDF visual locators require the original and an acquired page map")
        pages = artifacts.read(capture["text"]).decode("utf-8").split("\f")
        if not pages[-1].strip():
            pages.pop()
        region = locator["region"]
        if (type(locator["page_index"]) is not int or not 0 <= locator["page_index"] < len(pages)
                or not isinstance(region, list) or len(region) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in region)
                or not 0 <= region[0] < region[0] + region[2] <= 1
                or not 0 <= region[1] < region[1] + region[3] <= 1):
            raise ResearchError("invalid_locator", "PDF page and region must be within the saved document")
        if locator["printed_page"] is not None:
            text(locator["printed_page"], "Printed page", code="invalid_locator")
        return None
    if kind == "html":
        fields(locator, ("kind", "anchor"), ("assets",), code="invalid_locator")
        if (artifact["media_type"] not in ("text/html", "application/xhtml+xml")
                or not isinstance(locator["anchor"], dict) or locator["anchor"].get("kind") != "text"):
            raise ResearchError("invalid_locator", "HTML visual inspections require an anchor in the original HTML")
        return read_locator(artifacts, artifact, locator["anchor"])
    raise ResearchError("invalid_locator", "Unsupported source locator kind")


def validate_link(records, artifacts, link):
    """Verify bytes, exact-work membership and locator; return its source context."""
    fields(link, ("version_id", "source_id", "artifact", "locator"), code="source_mismatch")
    if not isinstance(link["locator"], dict):
        raise ResearchError("invalid_locator", "A source locator object is required")
    artifacts.read(link["artifact"])
    work = exact_work(records, link["version_id"])
    source = captured_source(records, artifacts, link["source_id"])
    capture = fulltext_capture(work, source["id"])
    abstract = next((a for a in work["abstracts"] if a["source_id"] == source["id"] and a["artifact"] == link["artifact"]), None)
    member = (abstract is not None or capture is not None and link["artifact"] in (capture["original"], capture["text"])
              or source["id"] in work["source_ids"] and link["artifact"] == source["response"])
    if not member:
        raise ResearchError("source_mismatch", "Evidence must belong to this exact captured work version")
    if abstract is None and capture is None and not _within_metadata_assertion(records, work, source, link["locator"]):
        raise ResearchError("source_mismatch", "A response passage must stay within this work's actual mapped fields or record")
    if capture is not None and capture["availability"] != "available":
        raise ResearchError("source_pending", "An incomplete full-text capture cannot establish a reading")
    value = read_locator(artifacts, link["artifact"], link["locator"], capture=capture)
    visual = None
    if link["locator"]["kind"] == "html":
        from .visual_assets import visual_context
        visual = visual_context(records, artifacts, link, source, capture)
    return {"work": work, "source": source, "capture": capture, "abstract": abstract, "value": value, "visual": visual}


def _within_metadata_assertion(records, work, source, locator):
    assertions = [records["work_assertion"][a] for a in work["assertion_ids"]
                  if records["work_assertion"][a]["source_id"] == source["id"]]
    for assertion in assertions:
        boundary = assertion["source_locator"]
        if boundary["type"] == "mapped":
            for field in boundary["fields"].values():
                if isinstance(field, str) and locator.get("kind") == "json":
                    pointer = locator.get("pointer")
                    if isinstance(pointer, str) and (pointer == field or pointer.startswith(field + "/")):
                        return True
                if isinstance(field, dict) and locator.get("kind") == "text":
                    if type(locator.get("start")) is int and type(locator.get("end")) is int and field["start"] <= locator["start"] < locator["end"] <= field["end"]:
                        return True
        elif boundary["type"] == "json_pointer" and locator.get("kind") == "json":
            pointer = locator.get("pointer")
            if isinstance(pointer, str) and (pointer == boundary["pointer"] or pointer.startswith(boundary["pointer"] + "/")):
                return True
    # Atom metadata should use its prepared source-specific abstract artifact;
    # arbitrary offsets into a multi-entry feed do not delimit an article.
    return False


def original_identity(records, link):
    work = exact_work(records, link["version_id"])
    capture = fulltext_capture(work, link["source_id"])
    source = records.get("source", {}).get(link["source_id"])
    original = capture.get("original") if capture else source.get("response") if source else None
    if original is None:
        raise ResearchError("source_pending", "The corresponding original capture is missing")
    return original["sha256"]


def complete_original(context):
    """Only acquired complete originals can establish article-unit scope."""
    source, capture = context["source"], context["capture"]
    return (capture is not None and capture["availability"] == "available"
            and source["capture_method"] == "http" and source["origin_verified"] is True
            and source["response_complete"] is True and capture["original"] == source["response"])


def link_identity(link, records):
    """Content identity permits explicit reuse across identical capture receipts."""
    locator = link["locator"]
    if locator["kind"] == "html":
        locator = {"kind": "html", "anchor": locator["anchor"],
                   "assets": sorted(({"url": a["url"], "sha256": a["artifact"]["sha256"]} for a in locator.get("assets", [])),
                                    key=lambda a: a["url"])}
    return {"version_id": link["version_id"], "original_sha256": original_identity(records, link),
            "sha256": link["artifact"]["sha256"], "locator": locator}


def contains(outer, inner, records):
    if (outer["version_id"] != inner["version_id"] or outer["artifact"]["sha256"] != inner["artifact"]["sha256"]
            or original_identity(records, outer) != original_identity(records, inner)):
        return False
    a, b = outer["locator"], inner["locator"]
    if a["kind"] != b["kind"]:
        return False
    if a["kind"] == "text":
        return a["start"] <= b["start"] < b["end"] <= a["end"]
    if a["kind"] == "pdf":
        x, y, width, height = a["region"]
        u, v, w, h = b["region"]
        return a["page_index"] == b["page_index"] and x <= u and y <= v and u + w <= x + width and v + h <= y + height
    if a["kind"] == "html":
        assets_a = {(x["url"], x["artifact"]["sha256"]) for x in a.get("assets", [])}
        assets_b = {(x["url"], x["artifact"]["sha256"]) for x in b.get("assets", [])}
        return (a["anchor"]["start"] <= b["anchor"]["start"] < b["anchor"]["end"] <= a["anchor"]["end"]
                and assets_b <= assets_a)
    return a == b


def covers_text(artifacts, link):
    if link["locator"]["kind"] != "text":
        return False
    content = artifacts.read(link["artifact"]).decode("utf-8")
    locator = link["locator"]
    return not content[:locator["start"]].strip() and not content[locator["end"]:].strip()
