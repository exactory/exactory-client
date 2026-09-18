"""Validate original, externally captured arXivRaw ListRecords traversals.

The first supported set is physics:math-ph. Category order follows arXiv's
current abs_categories field, with its primary category first. Completion
means an observed traversal, not historical category membership or a
transactionally consistent provider snapshot. Original responses remain
unverified external imports, even when their supplied URL uses HTTPS.
"""

import re
import xml.etree.ElementTree as ET
from datetime import timezone
from urllib.parse import parse_qsl, urlsplit

from .errors import ResearchError
from .oai_records import OAI, RAW, child, parse_record, value
from .operations import fields, timestamp
from .providers import _MetadataTreeBuilder

ENDPOINT = "https://oaipmh.arxiv.org/oai"
PARSER = {"name": "arxiv-oai-cohort", "version": 1,
          "oai_commit": "27ad99e3e37ab7919869bd3d4cd24449dea78135",
          "base_commit": "f835347408851239ba23d2171f7f10916799cf62",
          "category_rule": "First current abs_categories token is primary; preserve the full sequence."}
LIMITS = ("Enumeration of the supplied observed OAI traversal, projected by v1 submission dates and current primary categories. "
          "External origin is unverified; this is neither historical category membership nor a transactional provider snapshot. "
          "Acquisition grants no reading, preparation, admission or scientific validity credit.")


def _invalid(message, **details):
    raise ResearchError("invalid_oai_chain", message, details)


def _clean(text):
    return isinstance(text, str) and bool(text.strip()) and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in text)


def _url_parameters(url):
    if not _clean(url) or any(c.isspace() for c in url) or re.search(r"%(?![0-9a-fA-F]{2})", url):
        _invalid("OAI source URL contains invalid text")
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise ResearchError("invalid_oai_chain", "Malformed OAI source URL") from error
    if parsed.scheme != "https" or parsed.netloc != "oaipmh.arxiv.org" or parsed.path != "/oai" or parsed.fragment or "#" in url:
        _invalid("OAI source URL must use the exact official HTTPS endpoint")
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, errors="strict")
    except (ValueError, UnicodeError) as error:
        raise ResearchError("invalid_oai_chain", "Invalid OAI query encoding") from error
    if len(dict(pairs)) != len(pairs) or any(not _clean(k) or not _clean(v) for k, v in pairs):
        _invalid("OAI query keys must be unique and values nonempty without controls")
    return dict(pairs)


def _utc(value):
    return timestamp(value).astimezone(timezone.utc)


def _record(node, response_date, parsed_response):
    if any(n.tag not in {OAI + "header", OAI + "metadata"} for n in node):
        _invalid("Unexpected OAI record structure")
    header = child(node, OAI + "header")
    if any(n.tag not in {OAI + "identifier", OAI + "datestamp", OAI + "setSpec"} for n in header):
        _invalid("Unexpected OAI header structure")
    sets = [n.text for n in header.findall(OAI + "setSpec")]
    if "physics:math-ph" not in sets or any(len(n) or not _clean(n.text) for n in header.findall(OAI + "setSpec")):
        _invalid("OAI record does not assert the requested subject set")
    article = child(child(node, OAI + "metadata"), RAW + "arXivRaw")
    # All optional fields must remain simple, unique original fields. Their
    # absence does not authorize fabricated abstracts or author structure.
    for tag in {n.tag for n in article if n.tag != RAW + "version"}:
        value(article, tag, empty=True)
    for version in article.findall(RAW + "version"):
        for tag in {n.tag for n in version}:
            value(version, tag, empty=True)
    parsed = parse_record(node, response_date, parsed_response)
    work = parsed.works[0]
    if not work["categories"]:
        _invalid("The original category sequence is required for every record")
    abstract = child(article, RAW + "abstract", optional=True)
    if abstract is not None and not (abstract.text or "").strip():
        _invalid("An original abstract field must contain nonempty simple text")
    work["category"] = work["categories"][0]
    return work


def validate_chain(pages, definition):
    """Pure validation of ordered {response: bytes, source_url, captured_at} pages.

    Return projected native works per page and a conservation ledger for every
    original record. No Store, network request or caller-supplied count is used.
    """
    from .acquisition import _definition, digest_bytes
    definition = _definition(definition)
    if definition["primaryCategory"] != "math-ph":
        _invalid("Only the frozen math-ph primary category is supported")
    if not isinstance(pages, list) or not pages:
        _invalid("A complete ordered original response chain is required")
    expected = {"verb": "ListRecords", "metadataPrefix": "arXivRaw", "set": "physics:math-ph"}
    seen_ids, seen_tokens, observed_days = set(), set(), set()
    result, ledger = [], []
    previous_capture = previous_response = None
    members = 0
    for index, supplied in enumerate(pages):
        fields(supplied, ("response", "source_url", "captured_at"))
        raw = supplied["response"]
        if not isinstance(raw, bytes):
            _invalid("OAI response must be original bytes", page=index)
        args = _url_parameters(supplied["source_url"])
        if args != expected:
            _invalid("OAI source request does not continue the exact ordered token chain", page=index)
        try:
            root = ET.fromstring(raw, parser=ET.XMLParser(target=_MetadataTreeBuilder()))
        except ET.ParseError as error:
            raise ResearchError("invalid_oai_chain", "Malformed original OAI XML", {"page": index}) from error
        if root.tag != OAI + "OAI-PMH" or any(n.tag not in {OAI + "responseDate", OAI + "request", OAI + "ListRecords"} for n in root):
            _invalid("OAI envelope contains a protocol error or unexpected element", page=index)
        for container in root.iter():
            if len(container) and ((container.text or "").strip() or any((n.tail or "").strip() for n in container)):
                _invalid("OAI containers cannot contain undeclared mixed text", page=index)
        request = child(root, OAI + "request")
        if request.attrib != args or value(root, OAI + "request") not in {ENDPOINT, "http://oaipmh.arxiv.org/oai"}:
            _invalid("XML request must agree with the supplied official request", page=index)
        response_date = value(root, OAI + "responseDate")
        response, captured = _utc(response_date), _utc(supplied["captured_at"])
        if response > captured or (previous_capture is not None and (captured < previous_capture or response < previous_response)):
            _invalid("OAI capture and response chronology is inconsistent", page=index)
        previous_capture, previous_response = captured, response
        observed_days.update((response.date(), captured.date()))
        if len(observed_days) != 1:
            _invalid("The observed chain must fit within one UTC day", page=index)
        listing = child(root, OAI + "ListRecords")
        if any(n.tag not in {OAI + "record", OAI + "resumptionToken"} for n in listing):
            _invalid("Unexpected ListRecords element", page=index)
        token_node = child(listing, OAI + "resumptionToken", optional=True)
        token = value(listing, OAI + "resumptionToken", optional=True, empty=True)
        attrs = dict(token_node.attrib) if token_node is not None else {}
        if not attrs.keys() <= {"expirationDate", "completeListSize", "cursor"}:
            _invalid("Unexpected resumption-token attributes", page=index)
        if "expirationDate" in attrs:
            _utc(attrs["expirationDate"])
        for key in ("completeListSize", "cursor"):
            if key in attrs and not re.fullmatch(r"[0-9]+", attrs[key]):
                _invalid("Invalid resumption-token count metadata", page=index)
        if token:
            if not _clean(token) or token in seen_tokens or index == len(pages) - 1:
                _invalid("The token chain is repeated, malformed or incomplete", page=index)
            seen_tokens.add(token)
            expected = {"verb": "ListRecords", "resumptionToken": token}
        elif index != len(pages) - 1:
            _invalid("Unexpected pages after termination", page=index)
        nodes = listing.findall(OAI + "record")
        if not nodes:
            _invalid("A ListRecords response must contain records", page=index)
        works = []
        response_hash = digest_bytes(raw)
        for record_index, node in enumerate(nodes):
            try:
                work = _record(node, response_date, response)
            except ResearchError as error:
                raise ResearchError(error.code, error.message, {**(error.details or {}), "page": index, "record_index": record_index,
                    "identifier": node.findtext(OAI + "header/" + OAI + "identifier")}) from error
            if work["work_id"] in seen_ids:
                _invalid("Duplicate article family in OAI traversal", page=index, identifier=work["work_id"])
            seen_ids.add(work["work_id"])
            selected = definition["windowStart"] <= work["publication_date"][:10] <= definition["windowEnd"]
            disposition = "member" if work["category"] == "math-ph" else "other_primary_category"
            if not selected:
                disposition = "out_of_scope_date"
            work["source_locator"].update(page_index=index, record_index=record_index)
            ledger.append({"id": work["id"], "work_id": work["work_id"], "publication_date": work["publication_date"],
                           "category": work["category"], "categories": work["categories"], "page_index": index,
                           "record_index": record_index, "disposition": disposition,
                           "date_diagnostics": work["date_assertions"]["diagnostics"],
                           "chronology_status": work["date_assertions"]["chronology_status"],
                           "version_history": work["date_assertions"]["version_history"],
                           "response_sha256": response_hash, "source_url": supplied["source_url"]})
            if selected:
                works.append(work)
                members += disposition == "member"
        result.append(dict(supplied, works=works, returned_count=len(nodes), response_date=response_date,
                           xml_request_url=request.text, token=token, token_attributes=attrs))
    projected = sum(len(p["works"]) for p in result)
    return {"pages": result, "ledger": ledger, "returned_count": len(ledger), "projected_count": projected,
            "member_count": members, "out_of_window_count": len(ledger) - projected, "definition": definition,
            "date_anomaly_count": sum(bool(item["date_diagnostics"]) for item in ledger)}


def import_cohort(store, collection_id, pages, *, request_id, expected_revision):
    """Read checked workspace files, validate, then publish one atomic CAS receipt.

    File paths are local inputs only. Replay identity instead binds ordered
    original hashes/sizes, source URLs and capture times to the collection.
    """
    import copy
    from .acquisition import _collection_summary, _member, _partition
    from .artifacts import ArtifactStore, describe_artifact
    from .evidence import derived_id, digest, prepare_work, put_sources, put_work
    from .provenance import runtime_provenance
    from .storage import _canonical
    from .workspace import read_file
    from . import resources

    if not isinstance(collection_id, str) or not collection_id:
        _invalid("Name an existing collection")
    if not isinstance(pages, list) or not pages:
        _invalid("Name a complete ordered array of original response files")
    supplied, identities = [], []
    for page in pages:
        fields(page, ("response_file", "source_url", "captured_at"))
        raw = read_file(store.root, page["response_file"])
        reference = describe_artifact(raw, "text/xml")
        supplied.append({"response": raw, "source_url": page["source_url"], "captured_at": page["captured_at"]})
        identities.append({"response_sha256": reference["sha256"], "response_size": reference["size"],
                           "source_url": page["source_url"], "captured_at": page["captured_at"]})
    payload = {"collection_id": collection_id, "pages": identities}
    operation = "cohort.import_oai"
    # Replay/conflict checking must precede validation against today's records.
    if store.committed_request(request_id) is not None:
        return store.mutate(operation, payload, lambda tx: None, expected_revision=expected_revision, request_id=request_id)["result"]
    snapshot = store.snapshot()
    if type(expected_revision) is not int or expected_revision < 0:
        _invalid("Expected revision must be a nonnegative integer")
    if snapshot["revision"] != expected_revision:
        raise ResearchError("stale_revision", "Read the current revision before importing a new OAI chain")
    collection = snapshot["records"].get("collection", {}).get(collection_id)
    if collection is None:
        raise ResearchError("unknown_collection", "No collection has this identifier")
    _check_active(snapshot["records"].get("acquisition_operation", {}), collection_id)
    chain = validate_chain(supplied, collection["definition"])
    imported_bytes = sum(p["response_size"] for p in identities)
    # The known byte charge is refused before writing artifacts and rechecked
    # inside the final CAS. No native request is reserved for an external import.
    amounts = {"source_bytes": imported_bytes}
    resources.charge(snapshot["records"], "literature", amounts)
    artifacts = ArtifactStore(store.root)
    sources, prepared, page_records = [], [], []
    enumeration_id = derived_id(request_id, "oai-enumeration", digest(payload))
    for index, page in enumerate(chain["pages"]):
        source_id = derived_id(request_id, "source", index)
        source = {"id": source_id, "provider": "arxiv", "operation_id": request_id, "attempt_ordinal": index,
                  "requested_identifier": None, "url": page["source_url"], "captured_at": page["captured_at"],
                  "response_received_at": None, "next_eligible_at": None, "exact_version": None,
                  "capture_method": "external_import", "content_scope": "provider_response", "origin_verified": False,
                  "http_status": None, "headers": {}, "imported": True, "status": "captured", "response_complete": True,
                  "response": artifacts.put(page["response"], "text/xml"), "error": None,
                  "oai_response_date": page["response_date"]}
        sources.append(source)
        prepared.extend(prepare_work(artifacts, work, source_id) for work in page["works"])
        page_records.append({"source_id": source_id, "source_digest": digest(source), "response": source["response"],
                             "source_url": page["source_url"], "captured_at": page["captured_at"],
                             "response_date": page["response_date"], "xml_request_url": page["xml_request_url"],
                             "token": page["token"], "token_attributes": page["token_attributes"],
                             "returned_count": page["returned_count"], "projected_count": len(page["works"]),
                             "reported_total": None})
    history = artifacts.put(_canonical(collection).encode(), "application/json")
    projection = artifacts.put(_canonical(chain["ledger"]).encode(), "application/json")
    counts = {"returned": chain["returned_count"], "projected": chain["projected_count"],
              "members": chain["member_count"], "out_of_window": chain["out_of_window_count"]}
    epoch = {"id": enumeration_id, "collection_id": collection_id, "definition": chain["definition"], "parser": PARSER,
             "pages": page_records, "projection": projection, "history": history, "counts": counts,
             "assertions": [{"id": item[0]["assertion_id"], "digest": digest(item[0])} for item in prepared],
             "chain_digest": digest(identities), "captured_from": chain["pages"][0]["captured_at"],
             "captured_through": chain["pages"][-1]["captured_at"], "limits": LIMITS,
             "origin_verified": False, "provider_reported_total": None, "date_anomaly_count": chain["date_anomaly_count"]}
    runtime = runtime_provenance()
    epoch["runtime"] = runtime
    epoch["manifest"] = artifacts.put(_canonical(epoch).encode(), "application/json")

    def commit(transaction):
        _check_active(transaction.records("acquisition_operation"), collection_id)
        current = transaction.get("collection", collection_id)
        if current != collection:
            raise ResearchError("stale_revision", "The collection changed while preparing the OAI chain")
        charge = resources.charge(transaction, "literature", amounts)
        updated = copy.deepcopy(current)
        partition = _partition(enumeration_id, current["definition"]["windowStart"].replace("-", "") + "0000",
                                current["definition"]["windowEnd"].replace("-", "") + "2359")
        partition.update(offset=counts["projected"], total=counts["projected"], seen_count=counts["projected"],
                         entries_seen=counts["projected"], status="complete", enumeration_id=enumeration_id,
                         total_basis="local_submission_date_projection", provider_reported_total=None)
        updated.update(partitions=[partition], active_operation=None, pending=[], extraction_failures=0,
                       source_count=current["source_count"] + len(sources),
                       returned_count=current["returned_count"] + counts["returned"],
                       last_attempt_at=epoch["captured_through"], next_eligible_at=None,
                       sequence=current["sequence"] + len(sources), current_enumeration=enumeration_id)
        updated["enumeration_evidence"] = current.get("enumeration_evidence", []) + [{"id": enumeration_id, "digest": digest(epoch)}]
        put_sources(transaction, sources)
        for item in prepared:
            assertion = item[0]
            work = put_work(transaction, item)
            _member(transaction, collection_id, work, assertion["source_id"], "cohort_seen")
            member = assertion["category"] == "math-ph"
            _member(transaction, collection_id, work, assertion["source_id"], "cohort_member" if member else "cohort_exclusion",
                    None if member else "other_primary_category")
            entry = {"collection_id": collection_id, "partition_id": enumeration_id, "epoch": 0}
            transaction.put("cohort_partition_entry", digest([collection_id, enumeration_id, "entry", work["id"]]),
                            dict(entry, entry=work["id"]))
            transaction.put("cohort_partition_member", digest([collection_id, enumeration_id, work["work_id"]]),
                            dict(entry, work_id=work["work_id"]))
        for index, page in enumerate(page_records):
            transaction.put("collection_page", derived_id(request_id, "oai-page", index),
                {"collection_id": collection_id, "partition_id": enumeration_id, "epoch": 0,
                 "operation_id": request_id, "sequence": current["sequence"] + index + 1,
                 "source_ids": [page["source_id"]], "returned_count": page["returned_count"],
                 "reported_total": None, "projected_count": page["projected_count"], "disposition": "accepted",
                 "enumeration_id": enumeration_id, "page_index": index})
        transaction.put("cohort_enumeration", enumeration_id, epoch)
        transaction.put("cohort_enumeration_history", enumeration_id,
                        {"collection_id": collection_id, "previous": history, "enumeration_id": enumeration_id})
        transaction.put("collection", collection_id, updated)
        # Keep the ordinary retained-population and abstract obligations intact.
        records = {kind: transaction.records(kind) for kind in ("work", "source", "work_assertion", "cohort_member", "cohort_exclusion",
                   "cohort_seen", "cohort_partition_member", "cohort_abstract_selection", "alias")}
        summary = _collection_summary(records, updated)
        result = dict(summary, request_id=request_id, revision=expected_revision + 1,
                      enumeration_id=enumeration_id, scope="cohort_enumeration_import", attempts_used=0,
                      external_response_count=len(sources), imported_bytes=imported_bytes,
                      source_ids=[s["id"] for s in sources], enumeration_counts=counts,
                      origin_verified=False, limits=LIMITS, date_anomaly_count=chain["date_anomaly_count"])
        transaction.put("acquisition_operation", request_id, {"operation": operation, "payload": payload,
            "state": "finished", "target": collection_id, "admission_revision": None,
            "result": result, "runtime": runtime, "network_requests": 0,
            "external_response_count": len(sources), "imported_bytes": imported_bytes})
        if charge is not None:
            transaction.put(*charge)
        return result

    return store.mutate(operation, payload, commit, expected_revision=expected_revision, request_id=request_id)["result"]


def _check_active(operations, collection_id):
    if any(op.get("state") == "admitted" and op.get("target") == collection_id for op in operations.values()):
        raise ResearchError("operation_conflict", "An active acquisition owns this collection; resolve it before importing")


def enumeration_evidence(records, artifacts, collection):
    """Verify every retained original-chain dependency once per Evaluation."""
    from .evaluation import Evaluation
    evaluation = Evaluation.of(records, artifacts)
    return evaluation.once(("oai_enumeration", collection["id"]), lambda: _enumeration_evidence(evaluation, collection))


def _enumeration_evidence(evaluation, collection):
    from .evidence import digest
    from .storage import _canonical
    evidence = collection.get("enumeration_evidence", [])
    current = collection.get("current_enumeration")
    if not evidence and not current and not any("enumeration_id" in p for p in collection["partitions"]):
        return []
    if not current or current not in [e.get("id") for e in evidence]:
        _invalid("Restore the collection's enumeration evidence")
    dependencies = []
    for reference in evidence:
        epoch = evaluation.records.get("cohort_enumeration", {}).get(reference["id"])
        if (epoch is None or digest(epoch) != reference["digest"] or epoch["collection_id"] != collection["id"]
                or epoch["definition"] != collection["definition"]):
            _invalid("Missing or inconsistent native enumeration evidence")
        manifest = {k: v for k, v in epoch.items() if k != "manifest"}
        if evaluation.read(epoch["manifest"]) != _canonical(manifest).encode():
            _invalid("Enumeration manifest does not agree with native provenance")
        evaluation.read(epoch["history"])
        evaluation.read(epoch["projection"])
        for page in epoch["pages"]:
            source = evaluation.records.get("source", {}).get(page["source_id"])
            if source is None or digest(source) != page["source_digest"] or source["response"] != page["response"]:
                _invalid("Missing or inconsistent original OAI source")
            evaluation.read(page["response"])
        for reference in epoch["assertions"]:
            assertion = evaluation.records.get("work_assertion", {}).get(reference["id"])
            if assertion is None or digest(assertion) != reference["digest"]:
                _invalid("Missing or inconsistent projected OAI assertion")
        if epoch["id"] == current:
            partitions = collection["partitions"]
            if (len(partitions) != 1 or partitions[0].get("enumeration_id") != current
                    or partitions[0]["total"] != epoch["counts"]["projected"]):
                _invalid("The active partition no longer matches its OAI projection")
        dependencies.append(epoch)
    return dependencies
