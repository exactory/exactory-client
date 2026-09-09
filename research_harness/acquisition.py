"""Durable literature acquisition without creating reading or readiness records.

Public APIs (Store is already initialized):
collect_cohort(store, definition, *, request_id, expected_revision,
               max_requests=None, http=None, page_size=100)
resume_cohort(store, collection_id, *, request_id, expected_revision,
              max_requests=None, http=None)
collection_status(store, collection_id=None)
acquire_work(store, identifier, *, request_id, expected_revision, http=None,
             provider=None, max_requests=None)
acquire_fulltext(store, identifier, url, *, request_id, expected_revision,
                 http=None, max_requests=None, extractor=None)
import_response(store, provider, response: bytes, *, source_url, captured_at,
                request_id, expected_revision, media_type=None, mappings=None)

Collection definitions are exactly freeze's four fields. collection/{id} holds
definition, page_size, active_operation, partitions [{id,start,end,offset,total,
seen_count,epoch,status,restart}], sequence, source_count, returned_count,
extraction_failures, pending, last_attempt_at and next_eligible_at. The latter
retains the latest observed Retry-After deadline as a UTC ISO timestamp, even
after expiry. Every resume restores it in a fresh client; a future deadline
beyond the fetch wait allowance produces pending without a new HTTP attempt.
Inclusive minute partitions
are disjoint; a query above 30000 is bisected until enumerable or explicitly
pending at a single minute. cohort_member/cohort_exclusion/cohort_seen records
are keyed by a hash of collection/family and hold collection_id, work_id,
version_ids and source_ids. Family counts never count versions twice.
cohort_partition_member binds family membership to an enumeration epoch.
collection_page records every response/failed attempt, requested cursor,
reported counts, disposition and exact source_ids. Historical pages never change.

acquisition_operation/{request_id} holds operation, payload, state, target,
admission_revision and original result. Admission uses the supplied revision;
every later commit uses a consistent snapshot and explicit revision CAS.
Deterministic derived request IDs identify pages and finalization. A replay
returns the original finalized result (or committed admission after a crash)
without further network I/O. Explicit resume with a NEW request id can supersede
an interrupted collector. Its predecessor cannot subsequently commit a page.

max_requests counts all HTTP attempts, including retry/redirect/resolution
failures, across this invocation. Exhaustion is resumable and never complete.
Collection completion means enumeration and saved abstracts only. Status gives
actual source paths and separate reading obligations; it does not assert they
have been read. Registry acquisition's scope is metadata, not full-text reading
or verified bibliography completeness. Fulltext availability is source-specific.
Fulltext capture values contain source_id, requested_version_id, observed version,
url, original/text artifacts, availability (available|pending), extraction_status,
includes_abstract, and visual_inspection_required. Missing bytes, extraction or
exact-version agreement remain pending. All PDF/HTML captures retain a separate
visual inspection obligation for non-text content.

source_import/{request_id} holds provider, source_url, captured_at,
response_sha256, media_type, mappings, source_id, work_ids and parser failures.
Web/MCP source_url identifies the supplied result's provenance, not a verified
origin article download. Their partial excerpts cannot replace full sources.
"""

import copy
import re
from datetime import date, datetime, timedelta, timezone

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import derived_id, digest, media_type as response_media_type, prepare_sources, prepare_work, put_sources, put_work
from .fulltext import extract
from .http import HttpClient, HttpFailure, RequestBudget, safe_url
from .identities import family_id, normalize_identifier, version_of
from .imports import parse_mapped
from .providers import Arxiv, Crossref, OpenAlex


def _definition(value):
    if not isinstance(value, dict) or set(value) != {"corpus", "primaryCategory", "windowStart", "windowEnd"}:
        raise ResearchError("invalid_input", "Collection needs the exact four-field frozen cohort definition")
    if value["corpus"] != "arxiv":
        raise ResearchError("unsupported_corpus", "Managed cohort collection currently supports the arXiv corpus")
    if not isinstance(value["primaryCategory"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", value["primaryCategory"]):
        raise ResearchError("invalid_input", "Cohort primary category is invalid")
    try:
        start, end = date.fromisoformat(value["windowStart"]), date.fromisoformat(value["windowEnd"])
        if start > end or start.isoformat() != value["windowStart"] or end.isoformat() != value["windowEnd"]:
            raise ValueError()
    except (TypeError, ValueError) as error:
        raise ResearchError("invalid_input", "Cohort window must contain ordered ISO dates") from error
    return dict(value)


def _partition(identifier, start, end):
    return {"id": identifier, "start": start, "end": end, "offset": 0, "total": None,
            "seen_count": 0, "epoch": 0, "status": "pending", "restart": False}


def _begin(store, operation, payload, request_id, expected_revision, *, target=None, apply=None):
    admitted = []

    def admission(transaction):
        if apply:
            apply(transaction)
        result = {"status": "admitted", "request_id": request_id, "revision": expected_revision + 1,
                  "target": target, "pending": [{"code": "operation_incomplete"}]}
        transaction.put("acquisition_operation", request_id,
                        {"operation": operation, "payload": payload, "state": "admitted", "target": target,
                         "admission_revision": expected_revision + 1, "result": result})
        admitted.append(True)
        return result

    receipt = store.mutate(operation, payload, admission, expected_revision=expected_revision, request_id=request_id)
    if admitted:
        return None
    record = store.snapshot()["records"].get("acquisition_operation", {}).get(request_id)
    return receipt["result"] if record is None else record["result"]


def _finish(store, request_id, revision, result, *, apply=None):
    final = dict(result, request_id=request_id, revision=revision + 1)

    def commit(transaction):
        if apply:
            apply(transaction)
        operation = transaction.get("acquisition_operation", request_id)
        if operation is None or operation["state"] != "admitted":
            raise ResearchError("operation_conflict", "Acquisition operation is no longer admitted")
        operation.update({"state": "finished", "result": final})
        transaction.put("acquisition_operation", request_id, operation)
        return final

    return store.mutate("acquisition.finish", {"operation_id": request_id, "result": final}, commit,
                        expected_revision=revision, request_id=derived_id(request_id, "finish"))["result"]


def _collection_members(records, collection_id, kind):
    return [record for record in records.get(kind, {}).values() if record["collection_id"] == collection_id]


def _collection_summary(records, collection):
    from .cohort_evidence import current_selection
    collection_id = collection["id"]
    members = _collection_members(records, collection_id, "cohort_member")
    exclusions = _collection_members(records, collection_id, "cohort_exclusion")
    seen = _collection_members(records, collection_id, "cohort_seen")
    pending = copy.deepcopy(collection["pending"])
    # A family observed as a member on one capture and as an exclusion on another (the provider
    # changed its primary category between captures) is an explicit, retained conflict. Both
    # observations stay recorded; the member obligations remain; the collection is not paused.
    category_conflicts = sorted({m["work_id"] for m in members} & {m["work_id"] for m in exclusions})
    conflicts = [{"code": "primary_category_conflict", "work_ids": category_conflicts}] if category_conflicts else []
    obligations, historical = [], []
    for member in sorted(members, key=lambda m: m["work_id"]):
        for identifier in member["version_ids"]:
            work = records.get("work", {}).get(identifier)
            abstract = work.get("abstract") if work else None
            selection = current_selection(records, collection_id, member, identifier)
            if selection is not None:
                historical.append({"work_id": member["work_id"], "version_id": identifier,
                    "unresolved_assertion_id": selection["unresolved_assertion_id"], "artifact": abstract,
                    "source_ids": work["source_ids"], "selected_assertion_id": selection["selected_assertion_id"],
                    "reason": selection["reason"], "historical_version_resolved": False})
                obligations.append({"work_id": member["work_id"], "version_id": selection["version_id"], "depth": "abstract",
                                    "artifact": selection["artifact"], "source_ids": [selection["source_id"]],
                                    "selection": selection})
                continue
            if abstract is None:
                pending.append({"code": "missing_abstract", "work_id": member["work_id"], "version_id": identifier})
            if work and work["id"].startswith("arxiv:") and work["version"] is None:
                pending.append({"code": "missing_version", "work_id": member["work_id"]})
            obligations.append({"work_id": member["work_id"], "version_id": identifier, "depth": "abstract",
                                "artifact": abstract, "source_ids": [a["source_id"] for a in work["abstracts"]
                                    if a["artifact"] == abstract and a["completeness"] == "complete"] if work else []})
    unenumerated = [p["id"] for p in collection["partitions"] if p["status"] != "complete"]
    if not unenumerated:
        epochs = {(p["id"], p["epoch"]) for p in collection["partitions"]}
        current = {m["work_id"] for m in records.get("cohort_partition_member", {}).values()
                   if m["collection_id"] == collection_id and (m["partition_id"], m["epoch"]) in epochs}
        lost = sorted({m["work_id"] for m in seen} - current)
        if lost:
            pending.append({"code": "population_changed", "retained_work_ids": lost})
    if unenumerated and not pending:
        pending.append({"code": "collection_incomplete", "partitions": unenumerated})
    return {"collection_id": collection_id, "definition": collection["definition"],
            "status": "paused" if pending else "complete", "pending": pending,
            "source_count": collection["source_count"], "returned_count": collection["returned_count"],
            "unique_count": len(seen), "member_count": len(members), "exclusion_count": len(exclusions),
            "extraction_failures": collection["extraction_failures"], "pending_partitions": unenumerated,
            "next_eligible_at": collection.get("next_eligible_at"),
            "historical_unresolved": historical, "conflicts": conflicts,
            "reading_obligations": obligations, "next_abstract": next((o for o in obligations if o["artifact"]), None)}


def collection_status(store, collection_id=None):
    snapshot = store.snapshot()
    collections = snapshot["records"].get("collection", {})
    if collection_id is not None and collection_id not in collections:
        raise ResearchError("unknown_collection", "No collection has this identifier")
    selected = list(collections.values()) if collection_id is None else [collections[collection_id]]
    summaries = [_collection_summary(snapshot["records"], c) for c in selected]
    artifacts = ArtifactStore(store.root)
    for summary in summaries:
        for obligation in summary["reading_obligations"]:
            if obligation["artifact"]:
                artifacts.read(obligation["artifact"])
            if obligation.get("selection"):
                artifacts.read(snapshot["records"]["source"][obligation["selection"]["source_id"]]["response"])
        for historical in summary["historical_unresolved"]:
            for source_id in historical["source_ids"]:
                artifacts.read(snapshot["records"]["source"][source_id]["response"])
    return ({"revision": snapshot["revision"], "collections": summaries} if collection_id is None
            else dict(summaries[0], revision=snapshot["revision"]))


def collect_cohort(store, definition, *, request_id, expected_revision, max_requests=None, http=None, page_size=100):
    definition = _definition(definition)
    budget = RequestBudget(max_requests)
    if type(page_size) is not int or not 1 <= page_size <= 2000:
        raise ResearchError("invalid_input", "arXiv page_size must be between 1 and 2000")
    collection_id = "cohort:" + digest(definition)
    payload = {"definition": definition, "max_requests": max_requests, "page_size": page_size}

    def admission(transaction):
        collection = transaction.get("collection", collection_id)
        if collection is not None:
            raise ResearchError("collection_exists", "Use resume_cohort to continue the existing population", {"collection_id": collection_id})
        collection = {"id": collection_id, "definition": definition, "page_size": page_size,
                      "active_operation": request_id, "sequence": 0, "source_count": 0,
                      "returned_count": 0, "extraction_failures": 0, "pending": [], "last_attempt_at": None,
                      "next_eligible_at": None,
                      "partitions": [_partition("0", definition["windowStart"].replace("-", "") + "0000",
                                                definition["windowEnd"].replace("-", "") + "2359")]}
        transaction.put("collection", collection_id, collection)

    replay = _begin(store, "cohort.collect", payload, request_id, expected_revision, target=collection_id, apply=admission)
    return replay if replay is not None else _run_collection(store, collection_id, request_id, budget, http or HttpClient())


def resume_cohort(store, collection_id, *, request_id, expected_revision, max_requests=None, http=None):
    budget = RequestBudget(max_requests)

    def admission(transaction):
        collection = transaction.get("collection", collection_id)
        if collection is None:
            raise ResearchError("unknown_collection", "No collection has this identifier")
        collection["active_operation"] = request_id
        collection["pending"] = []
        for partition in collection["partitions"]:
            if partition["restart"]:
                partition.update({"offset": 0, "total": None, "seen_count": 0, "epoch": partition["epoch"] + 1,
                                  "status": "pending", "restart": False})
        transaction.put("collection", collection_id, collection)

    replay = _begin(store, "cohort.resume", {"collection_id": collection_id, "max_requests": max_requests},
                    request_id, expected_revision, target=collection_id, apply=admission)
    return replay if replay is not None else _run_collection(store, collection_id, request_id, budget, http or HttpClient())


def _member(transaction, collection_id, work, source_id, kind):
    key = digest([collection_id, work["work_id"]])
    member = transaction.get(kind, key) or {"collection_id": collection_id, "work_id": work["work_id"], "version_ids": [], "source_ids": []}
    if work["id"] not in member["version_ids"]:
        member["version_ids"].append(work["id"])
    if source_id not in member["source_ids"]:
        member["source_ids"].append(source_id)
    transaction.put(kind, key, member)


def _page_update(transaction, collection, partition_id, page, prepared, sources, error, request_id):
    if transaction.get("collection", collection["id"])["active_operation"] != request_id:
        raise ResearchError("operation_superseded", "A newer resume operation owns this collection")
    partition = next(p for p in collection["partitions"] if p["id"] == partition_id)
    requested = copy.deepcopy(partition)
    collection["source_count"] += len(sources)
    if sources:
        collection["last_attempt_at"] = sources[-1]["captured_at"]
    eligible_times = [source["next_eligible_at"] for source in sources if source["next_eligible_at"] is not None]
    if collection.get("next_eligible_at") is not None:
        eligible_times.append(collection["next_eligible_at"])
    collection["next_eligible_at"] = max(eligible_times) if eligible_times else None
    put_sources(transaction, sources)
    failures = [] if page is None else list(page.failures)
    if page is not None:
        collection["returned_count"] += page.returned_count
        collection["extraction_failures"] += len(page.failures)
    for item in prepared:
        work = put_work(transaction, item)
        source_id = item[0]["source_id"]
        _member(transaction, collection["id"], work, source_id, "cohort_seen")
        category = item[0]["category"]
        published = item[0]["publication_date"]
        try:
            published_time = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(timezone.utc)
            published_date = published_time.date().isoformat()
            within_window = collection["definition"]["windowStart"] <= published_date <= collection["definition"]["windowEnd"]
            if not partition["start"] <= published_time.strftime("%Y%m%d%H%M") <= partition["end"]:
                failures.append({"code": "out_of_partition_date", "id": work["id"]})
        except (TypeError, ValueError, AttributeError):
            within_window = False
        if not within_window:
            failures.append({"code": "out_of_scope_date", "id": work["id"]})
        elif category:
            kind = "cohort_member" if category == collection["definition"]["primaryCategory"] else "cohort_exclusion"
            _member(transaction, collection["id"], work, source_id, kind)
    disposition = "accepted"
    if error:
        failures.insert(0, {"code": error.code})
        disposition = "failed"
    elif page.start != partition["offset"]:
        failures.insert(0, {"code": "changed_cursor"})
        partition["restart"] = True
    elif partition["total"] is not None and page.total != partition["total"]:
        failures.insert(0, {"code": "changed_total", "previous": partition["total"], "observed": page.total})
        partition["restart"] = True
    elif page.total > Arxiv.query_ceiling:
        begin, end = (datetime.strptime(partition[key], "%Y%m%d%H%M") for key in ("start", "end"))
        if begin == end:
            failures.insert(0, {"code": "query_ceiling", "partition_id": partition["id"], "reported_total": page.total})
        else:
            middle = begin + timedelta(minutes=int((end - begin).total_seconds() // 120))
            children = [_partition(partition["id"] + ".0", partition["start"], middle.strftime("%Y%m%d%H%M")),
                        _partition(partition["id"] + ".1", (middle + timedelta(minutes=1)).strftime("%Y%m%d%H%M"), partition["end"])]
            index = collection["partitions"].index(partition)
            collection["partitions"][index:index + 1] = children
            disposition = "split"
    elif page.returned_count == 0 and page.total != partition["offset"]:
        failures.insert(0, {"code": "empty_page"})
    elif partition["offset"] + page.returned_count > page.total:
        failures.insert(0, {"code": "inconsistent_count"})
        partition["restart"] = True
    else:
        partition["total"] = page.total
        for item in prepared:
            key = digest([collection["id"], partition["id"], partition["epoch"], item[0]["work_id"]])
            if transaction.get("cohort_partition_member", key) is None:
                partition["seen_count"] += 1
                transaction.put("cohort_partition_member", key,
                    {"collection_id": collection["id"], "partition_id": partition["id"],
                     "epoch": partition["epoch"], "work_id": item[0]["work_id"]})
        partition["offset"] += page.returned_count
        if partition["offset"] == partition["total"]:
            if partition["seen_count"] != partition["total"]:
                failures.append({"code": "duplicate_or_missing_results"})
                partition["restart"] = True
            elif not failures:
                partition["status"] = "complete"
    if failures:
        disposition = "pending"
        collection["pending"] = failures
        if any(f.get("code") in ("missing_primary_category", "invalid_primary_category", "invalid_identifier", "invalid_response",
                                 "out_of_scope_date", "out_of_partition_date", "invalid_alias") for f in failures):
            partition["restart"] = True
    page_record = {"collection_id": collection["id"], "sequence": collection["sequence"], "requested": requested,
                   "source_ids": [s["id"] for s in sources], "reported_total": page.total if page else None,
                   "returned_count": page.returned_count if page else 0, "disposition": disposition, "failures": failures,
                   "warnings": list(page.warnings) if page else []}
    transaction.put("collection_page", derived_id(collection["id"], "page", collection["sequence"]), page_record)
    collection["sequence"] += 1
    transaction.put("collection", collection["id"], collection)
    return {"collection_id": collection["id"], "sequence": collection["sequence"], "disposition": disposition}


def _run_collection(store, collection_id, request_id, budget, http):
    artifacts, provider = ArtifactStore(store.root), Arxiv()
    while True:
        snapshot = store.snapshot()
        collection = snapshot["records"]["collection"][collection_id]
        if collection["active_operation"] != request_id:
            raise ResearchError("operation_superseded", "A newer resume operation owns this collection")
        partition = next((p for p in collection["partitions"] if p["status"] != "complete"), None)
        if partition is None or collection["pending"]:
            break
        try:
            budget.check()
        except ResearchError as error:
            collection["pending"] = [{"code": error.code}]
            break
        if collection["last_attempt_at"]:
            http.pace_from("export.arxiv.org", collection["last_attempt_at"])
        request = provider.collection_request(collection["definition"]["primaryCategory"], partition["start"], partition["end"],
                                                start=partition["offset"], page_size=collection["page_size"])
        page, failure, attempts, prepared = None, None, [], []
        try:
            response = http.get(request.url, headers=request.headers, accept=request.accept, budget=budget,
                                not_before=collection.get("next_eligible_at"))
            attempts = response.attempts
            page = provider.parse(response.body)
        except HttpFailure as error:
            failure, attempts = error, error.attempts
        except ResearchError as error:
            failure = error
        sources = prepare_sources(artifacts, attempts, "arxiv", request_id, start=collection["source_count"])
        if page is not None:
            prepared = [prepare_work(artifacts, w, sources[-1]["id"]) for w in page.works]
        result = store.mutate("cohort.page", {"collection_id": collection_id, "sequence": collection["sequence"],
                              "source_ids": [s["id"] for s in sources], "failure": failure.code if failure else None},
            lambda tx: _page_update(tx, collection, partition["id"], page, prepared, sources, failure, request_id),
            expected_revision=snapshot["revision"], request_id=derived_id(request_id, "page", collection["sequence"]))
        if result["result"]["disposition"] == "pending":
            continue
    result = _collection_summary(snapshot["records"], collection)
    result["attempts_used"] = budget.used

    def finish(transaction):
        current = transaction.get("collection", collection_id)
        if current["active_operation"] != request_id:
            raise ResearchError("operation_superseded", "A newer resume operation owns this collection")
        collection["active_operation"] = None
        transaction.put("collection", collection_id, collection)

    return _finish(store, request_id, snapshot["revision"], result, apply=finish)


def _provider(name):
    providers = {"arxiv": Arxiv, "openalex": OpenAlex, "crossref": Crossref}
    if name not in providers:
        raise ResearchError("unsupported_provider", "Expected arxiv, openalex or crossref")
    return providers[name]()


def _matches(identifier, work):
    if identifier == work["id"]:
        return True
    if identifier.startswith("arxiv:"):
        return version_of(identifier) is None and family_id(identifier) == work["work_id"]
    return identifier in work["aliases"]


def acquire_work(store, identifier, *, request_id, expected_revision, http=None, provider=None, max_requests=None):
    identifier = normalize_identifier(identifier)
    provider = _provider(provider or ("arxiv" if identifier.startswith("arxiv:") else "openalex" if identifier.startswith("openalex:") else "crossref"))
    request = provider.work_request(identifier)
    budget = RequestBudget(max_requests)
    replay = _begin(store, "work.acquire", {"identifier": identifier, "provider": provider.name, "max_requests": max_requests},
                    request_id, expected_revision, target=identifier)
    if replay is not None:
        return replay
    snapshot = store.snapshot()
    http, artifacts = http or HttpClient(), ArtifactStore(store.root)
    attempts, pending, works, observed_identifier, warnings = [], [], [], None, []
    try:
        response = http.get(request.url, headers=request.headers, accept=request.accept, budget=budget)
        attempts = response.attempts
        page = provider.parse(response.body)
        pending.extend(page.failures)
        warnings = list(page.warnings)
        if len(page.works) == 1:
            observed_identifier = page.works[0]["id"]
        if len(page.works) != 1 or not _matches(identifier, page.works[0]):
            pending.insert(0, {"code": "identifier_mismatch", "identifier": identifier})
        else:
            works = page.works
    except HttpFailure as error:
        attempts, pending = error.attempts, [{"code": error.code}]
    except ResearchError as error:
        pending.append({"code": error.code})
    sources = prepare_sources(artifacts, attempts, provider.name, request_id, requested_identifier=identifier)
    if observed_identifier and sources:
        sources[-1].update({"exact_version": version_of(observed_identifier), "observed_identifier": observed_identifier})
    prepared = [prepare_work(artifacts, work, sources[-1]["id"]) for work in works]
    for work in works:
        if work["abstract_text"] is None:
            pending.append({"code": "missing_abstract", "work_id": work["work_id"]})
        if work["id"].startswith("arxiv:") and work["version"] is None:
            pending.append({"code": "missing_version", "work_id": work["work_id"]})

    def commit(transaction):
        put_sources(transaction, sources)
        for item in prepared:
            put_work(transaction, item)

    return _finish(store, request_id, snapshot["revision"],
                   {"status": "pending" if pending else "complete", "scope": "metadata", "pending": pending, "warnings": warnings,
                    "work_ids": [w["id"] for w in works], "source_ids": [s["id"] for s in sources], "attempts_used": budget.used}, apply=commit)


def acquire_fulltext(store, identifier, url, *, request_id, expected_revision, http=None, max_requests=None, extractor=None):
    identifier, url = normalize_identifier(identifier), safe_url(url)
    budget = RequestBudget(max_requests)

    def admission(transaction):
        if transaction.get("work", identifier) is None:
            raise ResearchError("unknown_work", "Acquire metadata for the exact version before acquiring its full text")

    replay = _begin(store, "fulltext.acquire", {"identifier": identifier, "url": url, "max_requests": max_requests},
                    request_id, expected_revision, target=identifier, apply=admission)
    if replay is not None:
        return replay
    snapshot = store.snapshot()
    http, artifacts = http or HttpClient(), ArtifactStore(store.root)
    attempts, pending, extracted, observed_identifier = [], [], None, None
    try:
        response = http.get(url, accept=("application/pdf", "text/html", "application/xhtml+xml"), budget=budget)
        attempts = response.attempts
        if identifier.startswith("arxiv:"):
            observed_identifier = normalize_identifier(response.url)
            if observed_identifier != identifier or version_of(observed_identifier) is None:
                raise ResearchError("version_mismatch", "Full-text destination differs from the requested arXiv version")
        extracted = extract(response.body, response_media_type(response.headers), extractor=extractor)
        if extracted["status"] != "extracted":
            pending.append({"code": extracted["status"]})
    except HttpFailure as error:
        attempts, pending = error.attempts, [{"code": error.code}]
    except ResearchError as error:
        pending.append({"code": error.code})
    sources = prepare_sources(artifacts, attempts, "fulltext", request_id, requested_identifier=identifier)
    if observed_identifier and sources:
        sources[-1].update({"exact_version": version_of(observed_identifier), "observed_identifier": observed_identifier})
    capture = {"source_id": sources[-1]["id"] if sources else None, "version": version_of(observed_identifier) if observed_identifier else None,
               "requested_version_id": identifier,
               "url": sources[-1]["url"] if sources else url, "original": sources[-1]["response"] if sources else None,
               "text": artifacts.put(extracted["text"].encode(), "text/plain; charset=utf-8") if extracted and extracted["text"] is not None else None,
               "availability": "available" if not pending else "pending", "extraction_status": extracted["status"] if extracted else pending[0]["code"],
               "includes_abstract": extracted["includes_abstract"] if extracted else None,
               "visual_inspection_required": extracted["visual_inspection_required"] if extracted else True}

    def commit(transaction):
        put_sources(transaction, sources)
        work = transaction.get("work", identifier)
        work["fulltexts"].append(capture)
        transaction.put("work", identifier, work)

    return _finish(store, request_id, snapshot["revision"],
                   {"status": "pending" if pending else "complete", "scope": "fulltext_acquisition", "pending": pending,
                    "work_id": identifier, "capture": capture, "attempts_used": budget.used}, apply=commit)


def import_response(store, provider, response, *, source_url, captured_at, request_id, expected_revision, media_type=None, mappings=None):
    source_url = safe_url(source_url)
    if not isinstance(response, bytes):
        raise ResearchError("invalid_import", "Import response must be original bytes")
    try:
        timestamp = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError()
    except (TypeError, ValueError, AttributeError) as error:
        raise ResearchError("invalid_import", "Import capture time must contain an explicit timezone") from error
    if provider in ("web", "mcp"):
        if media_type not in ("application/json", "text/plain", "text/html"):
            raise ResearchError("invalid_import", "Web/MCP import must declare JSON, text or HTML media type")
        page = parse_mapped(response, media_type, mappings)
    else:
        adapter = _provider(provider)
        media_type = media_type or ("application/atom+xml" if provider == "arxiv" else "application/json")
        page = adapter.parse(response)
    payload = {"provider": provider, "source_url": source_url, "captured_at": captured_at,
               "response_sha256": digest_bytes(response), "media_type": media_type, "mappings": mappings}
    replay = _begin(store, "response.import", payload, request_id, expected_revision, target=source_url)
    if replay is not None:
        return replay
    snapshot = store.snapshot()
    artifacts = ArtifactStore(store.root)
    attempts = [{"url": source_url, "captured_at": captured_at, "status": 200,
                 "headers": {"content-type": media_type}, "body": response, "complete": True, "error": None}]
    sources = prepare_sources(artifacts, attempts, provider, request_id)
    sources[0]["http_status"] = None
    sources[0]["imported"] = True
    sources[0].update({"capture_method": "external_import", "content_scope": "tool_response" if provider in ("web", "mcp") else "provider_response",
                       "origin_verified": False})
    prepared = [prepare_work(artifacts, work, sources[0]["id"]) for work in page.works]
    pending = list(page.failures)
    for work in page.works:
        if work["abstract_status"] != "available":
            pending.append({"code": "partial_abstract" if work["abstract_status"] == "partial" else "missing_abstract", "work_id": work["work_id"]})
        if work["id"].startswith("arxiv:") and work["version"] is None:
            pending.append({"code": "missing_version", "work_id": work["work_id"]})

    def commit(transaction):
        put_sources(transaction, sources)
        for item in prepared:
            put_work(transaction, item)
        transaction.put("source_import", request_id,
                        dict(payload, source_id=sources[0]["id"], work_ids=[w["id"] for w in page.works], failures=page.failures))

    return _finish(store, request_id, snapshot["revision"],
                   {"status": "pending" if pending else "complete", "scope": "metadata_import", "pending": pending,
                    "work_ids": [w["id"] for w in page.works], "source_ids": [s["id"] for s in sources]}, apply=commit)


def digest_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()
