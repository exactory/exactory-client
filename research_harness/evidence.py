"""Materialize acquired evidence before short Store transaction callbacks.

source/{id}: {id, provider, operation_id, attempt_ordinal, requested_identifier,
url, captured_at, response_received_at, next_eligible_at, exact_version,
http_status, headers, capture_method,
content_scope, origin_verified, response_complete,
status: captured|failed|partial, response: ArtifactRef|None, error}.
Source ids are deterministic hashes of operation id and attempt ordinal. A
partial response artifact is retained with status partial, never captured.
Observed identifiers/versions describe a parsed response or final source URL;
requested_identifier never substitutes for an observed source version. Imported
sources also contain imported=True, have http_status=None and origin_verified
False. Their content_scope is tool_response (web/MCP) or provider_response.

work/{exact_id} retains first known scalar metadata plus all assertion_ids,
source_ids, date_assertions, abstracts, fulltexts and reference_sets. abstract
is the first available abstract artifact; abstracts lists every source-bound
representation. A later assertion fills missing fields but does not erase a
conflict. work_assertion/{id} is a source-bound normalized metadata assertion.
An assertion id includes its source locator, so duplicate ids within one response
remain separate assertions. Each abstracts item holds artifact, source_id,
assertion_id, version, representation and completeness (complete|partial).
Mapped excerpts remain partial and never fill the preferred abstract field.

reference_occurrence/{id}: {id, source_id, source_work_id: exact_id, work_id:
family, ordinal, target: identifier|None, raw, kind}. Every returned occurrence
is kept, including duplicates and unresolved non-paper citations. Each
reference_set binds the occurrence ids and provider coverage to its source.
"""

import copy
import hashlib
import json
import re

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def derived_id(operation_id, purpose, ordinal=None):
    return "acq:" + digest([operation_id, purpose, ordinal])


def media_type(headers):
    value = headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
    return value if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", value) else "application/octet-stream"


def prepare_sources(artifacts, attempts, provider, operation_id, *, start=0, version=None, requested_identifier=None):
    sources = []
    for index, attempt in enumerate(attempts, start):
        successful = attempt["complete"] and attempt["error"] is None and attempt["status"] is not None and 200 <= attempt["status"] < 300
        source = {"id": derived_id(operation_id, "source", index), "provider": provider,
                  "operation_id": operation_id, "attempt_ordinal": index, "requested_identifier": requested_identifier,
                  "url": attempt["url"], "captured_at": attempt["captured_at"], "exact_version": version,
                  "response_received_at": attempt.get("response_received_at"),
                  "next_eligible_at": attempt.get("next_eligible_at"),
                  "capture_method": "http", "content_scope": "response", "origin_verified": attempt["status"] is not None,
                  "http_status": attempt["status"], "headers": attempt["headers"],
                  "status": "captured" if successful else "partial" if attempt["status"] == 206 or (not attempt["complete"] and attempt["body"]) else "failed",
                  "response_complete": attempt["complete"],
                  "response": artifacts.put(attempt["body"], media_type(attempt["headers"])) if attempt["body"] or attempt["complete"] else None,
                  "error": attempt["error"] or (None if successful else "http_status" if attempt["status"] is not None else "network_error")}
        sources.append(source)
    return sources


def prepare_work(artifacts, work, source_id):
    assertion = copy.deepcopy(work)
    abstract_text = assertion.pop("abstract_text")
    assertion["abstract"] = artifacts.put(abstract_text.encode("utf-8"), "text/plain; charset=utf-8") if abstract_text is not None else None
    assertion["source_id"] = source_id
    assertion["assertion_id"] = derived_id(source_id, "work", {"id": work["id"], "locator": work["source_locator"]})
    occurrences = []
    for reference in assertion.pop("references"):
        occurrence = dict(reference, id=derived_id(assertion["assertion_id"], "reference", reference["ordinal"]),
                          source_id=source_id, source_work_id=work["id"], work_id=work["work_id"])
        occurrences.append(occurrence)
    assertion["references"] = [r["id"] for r in occurrences]
    return assertion, occurrences


def _append_unique(target, values):
    for value in values:
        if value not in target:
            target.append(value)


def put_work(transaction, prepared):
    assertion, occurrences = prepared
    source_id, identifier = assertion["source_id"], assertion["id"]
    transaction.put("work_assertion", assertion["assertion_id"], assertion)
    for occurrence in occurrences:
        transaction.put("reference_occurrence", occurrence["id"], occurrence)
    work = transaction.get("work", identifier)
    if work is None:
        work = {k: copy.deepcopy(assertion[k]) for k in ("id", "work_id", "version", "title", "authors", "publication_date",
                "category", "type", "abstract", "abstract_status", "reference_metadata")}
        work.update({"aliases": [], "categories": [], "fulltext_urls": [], "source_ids": [],
                     "assertion_ids": [], "abstracts": [], "date_assertions": [], "fulltexts": [],
                     "references": [], "reference_sets": []})
        if assertion["abstract_status"] != "available":
            work["abstract"] = None
    for key in ("title", "authors", "publication_date", "category"):
        if not work[key] and assertion[key]:
            work[key] = copy.deepcopy(assertion[key])
    if work["abstract"] is None and assertion["abstract_status"] == "available":
        work["abstract"] = assertion["abstract"]
    if work["abstract"]:
        work["abstract_status"] = "available"
    for key in ("aliases", "categories", "fulltext_urls", "references"):
        _append_unique(work[key], assertion[key])
    _append_unique(work["source_ids"], [source_id])
    _append_unique(work["assertion_ids"], [assertion["assertion_id"]])
    _append_unique(work["date_assertions"], [{"source_id": source_id, "values": assertion["date_assertions"]}])
    if assertion["abstract"] is not None:
        _append_unique(work["abstracts"], [{"artifact": assertion["abstract"], "source_id": source_id,
                                          "assertion_id": assertion["assertion_id"],
                                          "version": assertion["version"], "representation": assertion["abstract_representation"],
                                          "completeness": "complete" if assertion["abstract_status"] == "available" else "partial"}])
    _append_unique(work["reference_sets"], [{"source_id": source_id, "assertion_id": assertion["assertion_id"], "occurrence_ids": assertion["references"],
                                            "metadata": assertion["reference_metadata"]}])
    if work["reference_metadata"]["status"] == "missing" and assertion["reference_metadata"]["status"] != "missing":
        work["reference_metadata"] = assertion["reference_metadata"]
    transaction.put("work", identifier, work)
    family = transaction.get("work_family", work["work_id"]) or {"work_id": work["work_id"], "version_ids": []}
    _append_unique(family["version_ids"], [identifier])
    transaction.put("work_family", family["work_id"], family)
    for identifier in assertion["aliases"]:
        alias = transaction.get("alias", identifier) or {"identifier": identifier, "assertions": []}
        _append_unique(alias["assertions"], [{"work_id": work["work_id"], "source_id": source_id,
                                            "locator": assertion["source_locator"], "relation": "same_work"}])
        transaction.put("alias", identifier, alias)
    return work


def put_sources(transaction, sources):
    for source in sources:
        transaction.put("source", source["id"], source)
