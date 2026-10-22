"""Observed native openness findings over this problem's common evidence."""

import json

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .native_review import ATTESTATION_PREFIX, PROTOCOL, load_packet
from .operations import fields, immutable_record, iso_date, strings, text
from .problem_claims import find_current_statement, find_latest, require_problem, compute_statement_digest
from .reading import fulltext_coverage


def collect_reading_artifacts(records, artifacts, reading):
    """Collect inspected originals, extracted text and explicitly named visuals."""
    from .source_links import validate_link
    found = {}
    for inspection in reading["inspections"]:
        link = inspection["link"]
        context = validate_link(records, artifacts, link)
        references = [link["artifact"], (context["capture"] or {}).get("original")]
        references.extend(asset["artifact"] for asset in link["locator"].get("assets", []))
        for reference in references:
            if reference is not None:
                artifacts.read(reference)
                found[reference["sha256"]] = reference
    return list(found.values())


def prepare_openness(records, artifacts, value, revision):
    require_problem(records)
    fields(value, ("assignment_id",))
    from .review_protocol import assignment_state
    state = assignment_state(records, artifacts, value["assignment_id"])
    assignment = records.get("review_assignment", {}).get(value["assignment_id"])
    if not state["ready"] or assignment is None or assignment["role"] != "native_math":
        raise ResearchError("review_context_unverified", "Obtain the observed native_math openness result", {"obligations": state["obligations"]})
    packet = load_packet(artifacts, assignment["context"]["native_packet"], assignment["reviewer_id"])
    output = state["output"]
    fields(output, ("schema_version", "subject_digest", "claim_digest", "reviewer", "decision", "findings"), code="invalid_record")
    fields(output["findings"], ("statement", "assumptions", "scope", "dependencies", "policy", "openness"), code="invalid_record")
    for key in ("statement", "assumptions", "scope", "dependencies", "policy"):
        text(output["findings"][key], "Native result finding " + key, code="invalid_record")
    if (output.get("schema_version") != 2 or output.get("decision") != "approve"
            or output.get("subject_digest") != packet["subject_digest"] or output.get("claim_digest") != packet["claim_digest"]
            or output.get("reviewer") != packet["reviewer"] or packet["kind"] != "result"):
        raise ResearchError("review_packet_mismatch", "Use the observed version 2 native result bound to the problem packet")
    finding = output.get("findings", {}).get("openness")
    fields(finding, ("outcome", "statement_digest", "evidence", "searched_at", "coverage", "reason"), ("minimum",))
    if "minimum" in finding:
        from .openness_minimum import validate_minimum
        validate_minimum(finding["minimum"])
    if finding["outcome"] not in {"supported_open", "already_solved", "unresolved"}:
        raise ResearchError("invalid_input", "Use a native openness outcome")
    iso_date(finding["searched_at"])
    text(finding["reason"], "Openness finding reason")
    # The observed finding is recorded as given; a reviewer that reports the dated coverage per formulation as a
    # nonempty structure is accepted alongside plain text.
    if isinstance(finding["coverage"], (list, dict)):
        if not finding["coverage"]:
            raise ResearchError("invalid_record", "Openness coverage must be nonempty")
    else:
        text(finding["coverage"], "Openness coverage", code="invalid_record")
    strings(finding["evidence"], "Openness evidence", nonempty=True, code="invalid_record")
    statement = find_current_statement(records)
    if (statement is None or finding["statement_digest"] != compute_statement_digest(statement)
            or finding["statement_digest"] != packet["claim_digest"]
            or packet["subject"] != statement or packet["subject_digest"] != digest(statement)):
        raise ResearchError("openness_statement_mismatch", "The finding must name the exact current problem claim")
    delivered = {entry["digest"] for entry in packet["evidence"]}
    if not isinstance(finding["evidence"], list) or not set(finding["evidence"]) <= delivered:
        raise ResearchError("openness_evidence_mismatch", "Use evidence delivered to the observed reviewer")
    if finding["outcome"] != "unresolved":
        from .openness_minimum import audit_minimum
        audit_minimum(finding, statement["statement"], records, artifacts, delivered)
        expected = {statement["statement"], *statement["neighbours"]}
        actual = {finding["minimum"]["statement"], *finding["minimum"]["neighbours"]}
        if actual != expected:
            raise ResearchError("openness_statement_mismatch", "Search the current declared neighbours", {"formulations": sorted(actual ^ expected)})
        if finding["minimum"] != packet["scientific_context"].get("openness_minimum"):
            raise ResearchError("openness_evidence_mismatch", "Return the exact minimum supplied to the reviewer")
        reading_digests = set(finding["minimum"]["reading_digests"])
        required_artifacts = {reference["sha256"] for reading in records.get("reading", {}).values()
                              if digest(reading) in reading_digests
                              for reference in collect_reading_artifacts(records, artifacts, reading)}
        if not required_artifacts <= delivered:
            raise ResearchError("openness_evidence_mismatch", "Deliver every decisive reading's inspected original and visual bytes",
                                {"artifact_digests": sorted(required_artifacts - delivered)})
    record = dict(finding, id=value["assignment_id"], assignment_id=value["assignment_id"],
                  statement_id=statement["id"], review=output, recorded_revision=revision)
    from .problem_openness_reviews import validate_native_reviews
    native_reviews = validate_native_reviews(records, artifacts, packet, state)
    if native_reviews is not None:
        record["native_reviews"] = native_reviews
    from .problem_openness_sources import validate_delivery
    delivery = validate_delivery(records, artifacts, packet, state)
    if delivery is not None:
        record["openness_delivery"] = delivery
    from .problem_openness_screening import validate_source_evidence
    source_evidence = validate_source_evidence(records, artifacts, packet, state)
    if source_evidence is not None:
        record["source_evidence"] = source_evidence
    return [immutable_record(records, "problem_openness", record["id"], record)], record


def assess_openness(records, artifacts):
    finding, statement = find_latest(records, "problem_openness"), find_current_statement(records)
    if finding is None:
        return [obligation("problem_openness_missing", "Obtain an observed openness finding for the selected statement.")]
    affected = {"openness_id": finding["id"], "statement_id": None if statement is None else statement["id"]}
    if statement is None or finding["statement_digest"] != compute_statement_digest(statement):
        return [obligation("problem_openness_stale", "Reassess openness of the current exact claim.", **affected)]
    if "native_reviews" in finding or "source_evidence" in finding:
        from .problem_openness_reviews import observed_review, validate_native_reviews
        try:
            _, state, packet = observed_review(records, artifacts, finding["assignment_id"])
            current = validate_native_reviews(records, artifacts, packet, state)
            if current != finding.get("native_reviews") or state["output"] != finding["review"]:
                raise ResearchError("openness_native_review_mismatch", "The recorded native review binding changed")
            from .problem_openness_sources import validate_delivery
            if validate_delivery(records, artifacts, packet, state) != finding.get("openness_delivery"):
                raise ResearchError("openness_source_delivery_mismatch", "The recorded source representation changed")
            from .problem_openness_screening import validate_source_evidence
            if validate_source_evidence(records, artifacts, packet, state) != finding.get("source_evidence"):
                raise ResearchError("openness_screening_evidence_mismatch", "The recorded screening source evidence changed")
        except ResearchError as error:
            return [obligation("problem_openness_stale", "Reassess changed or unavailable observed openness evidence.",
                               cause=error.code, **affected)]
    minimum = finding.get("minimum")
    if minimum is None:
        assignment = records.get("review_assignment", {}).get(finding.get("assignment_id"))
        if assignment is not None:
            packet = load_packet(artifacts, assignment["context"]["native_packet"], assignment["reviewer_id"])
            minimum = packet["scientific_context"].get("openness_minimum")
    if minimum is None:
        return [obligation("problem_openness_unresolved", "Resolve the literature and reading gaps before claiming openness.", **affected)]
    actual = {minimum.get("statement"), *minimum.get("neighbours", [])}
    expected = {statement["statement"], *statement["neighbours"]}
    if actual != expected:
        return [obligation("problem_openness_stale", "Reassess every current neighbouring formulation.",
                           formulations=sorted(item for item in actual ^ expected if item is not None), **affected)]
    if finding["outcome"] == "unresolved":
        from .openness_minimum import find_material_searches
        bound_searches = {row["search_digest"] for row in minimum.get("searches", [])}
        searches = {digest(row) for row in records.get("literature_search", {}).values()}
        readings = {digest(row) for row in records.get("reading", {}).values()}
        unbound = [row["id"] for row in find_material_searches(records, expected) if digest(row) not in bound_searches]
        if (unbound or not bound_searches <= searches or not set(minimum.get("reading_digests", [])) <= readings):
            return [obligation("problem_openness_stale", "Reassess changed or newly material search and reading evidence.",
                               search_ids=unbound, **affected)]
        return [obligation("problem_openness_unresolved", "Resolve the literature and reading gaps before claiming openness.", **affected)]
    from .openness_minimum import audit_minimum
    try:
        audit_minimum(finding, statement["statement"], records, artifacts, set(finding["evidence"]))
    except ResearchError as error:
        return [obligation("problem_openness_stale", "Reassess changed or newly material search and reading evidence.",
                           cause=error.code, **affected)]
    if finding["outcome"] == "already_solved":
        return [obligation("problem_already_solved", "Redirect to the established result or record a new statement.",
                           settling_work_id=minimum["settling_work_id"], **affected)]
    return []


def export_openness(store, spec, destination):
    records = store.snapshot()["records"]
    marker = require_problem(records)
    fields(spec, ("assignment_id", "reviewer_id", "minimum"), ("native_reviews", "source_evidence"))
    text(spec["assignment_id"], "Openness assignment ID")
    text(spec["reviewer_id"], "Openness reviewer ID")
    fields(spec["minimum"], ("searches", "settling_work_id", "duplicate_resolutions"), ("text_priority",))
    statement = find_current_statement(records)
    if statement is None:
        raise ResearchError("problem_statement_missing", "Record the exact statement before openness review")
    artifacts = ArtifactStore(store.root)
    searches, required, evidence = [], set(), {}

    def include_record(record):
        data = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        artifact = artifacts.put(data, "application/json")
        evidence[artifact["sha256"]] = {"digest": artifact["sha256"], "kind": "blob", "artifact": artifact}

    for binding in spec["minimum"]["searches"]:
        fields(binding, ("formulation", "search_id"))
        search = records.get("literature_search", {}).get(binding["search_id"])
        if search is None:
            raise ResearchError("openness_search_missing", "Bind this store's exact search", binding)
        searches.append({"formulation": binding["formulation"], "search_digest": digest(search)})
        required.update(row["work_id"] for row in search["dispositions"] if row["disposition"] in {"relevant", "contradictory"})
        include_record(search)
    if spec["minimum"]["settling_work_id"] is not None:
        required.add(spec["minimum"]["settling_work_id"])
    readings = {}
    for work in sorted(required):
        coverage = fulltext_coverage(records, artifacts, work)
        for reading_id in coverage["readings"]:
            readings[reading_id] = records["reading"][reading_id]
    for reading in readings.values():
        include_record(reading)
        for artifact in collect_reading_artifacts(records, artifacts, reading):
            evidence[artifact["sha256"]] = {"digest": artifact["sha256"], "kind": "artifact", "artifact": artifact}
    minimum = {"schema_version": 1, "statement": statement["statement"], "neighbours": statement["neighbours"],
               "searches": searches, "reading_digests": sorted(digest(row) for row in readings.values()),
               "settling_work_id": spec["minimum"]["settling_work_id"], "duplicate_resolutions": spec["minimum"]["duplicate_resolutions"]}
    include_record(statement)
    scientific_context = {"openness_minimum": minimum}
    if "source_evidence" in spec:
        from .problem_openness_screening import resolve_source_evidence
        source_evidence, source_artifacts = resolve_source_evidence(records, artifacts, spec["source_evidence"], materialize=True)
        scientific_context["source_evidence"] = source_evidence
        from .review_verbatim_text import MARKER
        scientific_context["review_text_encoding"] = MARKER
        evidence.update(source_artifacts)
    if "native_reviews" in spec:
        from .problem_openness_reviews import resolve_native_reviews
        native_reviews, native_evidence = resolve_native_reviews(records, artifacts, spec["native_reviews"])
        scientific_context["native_reviews"] = native_reviews
        evidence.update(native_evidence)
    if spec["minimum"].get("text_priority"):
        # The research record declares which decisive full texts a text-limited route delivers first; the transport
        # applies the route limit to the rest.
        strings(spec["minimum"]["text_priority"], "Text delivery priority")
        priority = []
        for version in spec["minimum"]["text_priority"]:
            work = records.get("work", {}).get(version)
            if work is None or not work.get("fulltexts"):
                raise ResearchError("openness_reading_gap", "Text delivery priority names a work without a captured full text", {"version_id": version})
            priority.append(work["fulltexts"][0]["text"]["sha256"])
        scientific_context["text_delivery_priority"] = priority
        scientific_context["text_delivery_rule"] = (
            "On a text-limited reviewer route the bodies of the listed full texts are delivered in this order "
            "while the route input limit allows; a body that does not fit is replaced by an explicit elision "
            "note, and the reading record of that work (its notes and inspected excerpts) is delivered in full.")
    packet = {"protocol": PROTOCOL, "kind": "result", "root": str(store.root.resolve()), "objective_id": statement["id"],
              "contract": {"orchestration": "problem_creation", "area": marker["area"]}, "subject": statement,
              "subject_digest": digest(statement), "claim_digest": compute_statement_digest(statement),
              "reviewer": {"source": "host", "actor_id": spec["reviewer_id"], "attestation_id": ATTESTATION_PREFIX + spec["assignment_id"]},
              "scientific_context": scientific_context, "evidence": [evidence[key] for key in sorted(evidence)],
              "execution_provenance": [], "unmaterialized_digests": []}
    if "native_reviews" in scientific_context:
        from .problem_openness_sources import build_delivery
        scientific_context["openness_delivery"] = build_delivery(records, artifacts, packet)
    reference = artifacts.put(json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(), "application/json")
    load_packet(artifacts, reference, spec["reviewer_id"])
    from .review_delivery import deliver_packet
    delivered = deliver_packet(store, destination, packet, transitive=True)
    result = dict(delivered, native_packet=reference, subject_digest=packet["subject_digest"], claim_digest=packet["claim_digest"])
    if not spec["minimum"].get("text_priority"):
        # A lower bound of the reviewer input: the packet and its text evidence bodies as JSON strings. Without a
        # priority no body gives way, so a route whose limit is below it refuses the run.
        from . import review_transport
        estimate = len(json.dumps(packet, ensure_ascii=False)) + sum(
            len(json.dumps(data.decode("utf-8"), ensure_ascii=False))
            for data in (artifacts.read(entry["artifact"]) for entry in packet["evidence"]) if _is_utf8(data))
        routes = sorted(identifier for identifier, route in records.get("review_route", {}).items()
                        if (review_transport.local_input_limit(route) or estimate) < estimate)
        if routes:
            result["warnings"] = [{"code": "review_input_too_large", "message":
                "The estimated size of this packet exceeds the input limit of the text routes in route_ids, and "
                "without minimum.text_priority a route withholds no full text by priority. List the version ids of the "
                "read works in minimum.text_priority, the decisive ones first, so that a route withholds full-text bodies "
                "from the end of the list. The estimate counts the packet and its UTF-8 evidence as JSON strings, so a "
                "delivery projection or a framed route can send less.",
                "details": {"route_ids": routes, "estimated_characters": estimate}}]
    return result


def _is_utf8(data):
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
