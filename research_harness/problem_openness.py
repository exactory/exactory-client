"""Observed native openness findings over this problem's common evidence."""

import copy
import json
import re
from collections import Counter

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .native_review import ATTESTATION_PREFIX, PROTOCOL, GROUND_NEXT_STEPS, load_packet, uses_finding_contract
from .operations import fields, immutable_record, iso_date, strings, text
from .problem_claims import find_current_statement, find_latest, require_problem, compute_statement_digest
from .reading import fulltext_coverage

GAPPED_EXPORT_MESSAGE = ("The gaps of this minimum block both supported_open and already_solved, so a review "
                         "over this packet can record only an unresolved finding. details.gaps lists the "
                         "blocking conditions with the records that they concern; a gap that a command repairs "
                         "names it under details.command. Continue for an unresolved finding, or repair the "
                         "gaps and export again.")
OUTDATED_PACKET_MESSAGE = ("The records changed after this packet was exported: a binding check of its minimum now "
                           "fails, or the gaps that the harness computes now differ from the gaps of the packet. "
                           "added and removed list the changes: in each item, row names the kind of dependency "
                           "that changed and entry holds the error or the gap. Export "
                           "a new packet with export --kind problem-openness and start a new chain over it. The "
                           "verified reading turns of this chain stay verified.")


FAMILY_READING_REPORT_FIELD = "derived_family_readings"
FAMILY_READING_LINK_FIELD = "link"
FAMILY_READING_AUTHOR_COMPARISON_FIELD = "authors_compared"
FAMILY_READING_V1_REFUSAL = "Family readings require a version 2 openness minimum delivered with protocol v6."
FAMILY_READING_LIST_REFUSAL = ("minimum.family_readings is not a list. Give a list of "
                               "{search_digest, work_id, version_id} entries, or leave the field out.")


def describe_gap_fact(gap, packet, family_failures):
    """Name the R10 dependency exposed by a changed forecast entry."""
    condition, details = gap["condition"], gap["details"]
    if condition == "reading_deferred":
        return "source_deferrals"
    if condition == "reading_missing":
        error = details.get("assessment_error", {})
        if (gap["search_digest"], gap.get("work_id")) in family_failures:
            return "family_reading"
        if error.get("code") == "invalid_reading":
            return "required_bundle"
        if error.get("code") in {"unknown_work", "missing_version"}:
            return "decisive_captures"
        if error:
            return "inspection_bytes"
        missing = details.get("missing", [])
        delivered = {row["digest"] for row in packet["evidence"]}
        if any(row.get("bundle_id") is not None or row.get("original_sha256") in delivered for row in missing):
            return "required_bundle"
        return "decisive_captures"
    return "bound_records"


def assess_packet_freshness(records, artifacts, packet):
    """Compare a packet's forecast with R10 facts, without changing its evidence."""
    from .openness_minimum import FAMILY_READING_SPEC_REFUSAL, list_minimum_gaps, validate_minimum_bindings

    context = packet["scientific_context"]
    if "minimum_gaps" not in context:
        return dict(current=True, added=[], removed=[])
    minimum = context["openness_minimum"]
    statement = find_current_statement(records)
    try:
        if (statement is None or packet["subject"] != statement
                or packet["claim_digest"] != compute_statement_digest(statement)
                or minimum["neighbours"] != statement["neighbours"]):
            raise ResearchError("openness_statement_mismatch", "The current statement or its neighbours changed.")
        validate_minimum_bindings(minimum, statement["statement"], records)
    except ResearchError as error:
        row = ("current_statement" if error.code == "openness_statement_mismatch" else
               "material_searches" if error.code == "openness_search_stale"
               else "bound_records")
        return dict(current=False, added=[dict(row=row, entry=error.as_dict())], removed=[])
    gaps = list_minimum_gaps(minimum, statement["statement"], records, artifacts)
    family_failures = {(gap["search_digest"], gap.get("work_id")) for gap in context["minimum_gaps"] + gaps
                       if gap["details"].get("assessment_error", {}).get("message") == FAMILY_READING_SPEC_REFUSAL}
    # Canonical entry bytes make both dictionary and list enumeration irrelevant.
    entries = {serialize_record(gap): gap for gap in context["minimum_gaps"] + gaps}
    previous = Counter(serialize_record(gap) for gap in context["minimum_gaps"])
    current = Counter(serialize_record(gap) for gap in gaps)
    added = [dict(row=describe_gap_fact(entries[key], packet, family_failures), entry=entries[key])
             for key in sorted((current - previous).elements())]
    removed = [dict(row=describe_gap_fact(entries[key], packet, family_failures), entry=entries[key])
               for key in sorted((previous - current).elements())]
    return dict(current=not added and not removed, added=added, removed=removed)


def collect_reading_artifacts(records, artifacts, reading):
    """Collect inspected originals and visuals, using the bound main extraction for a chosen original."""
    from .source_links import validate_link
    found, originals = {}, set()
    for inspection in reading["inspections"]:
        link = inspection["link"]
        context = validate_link(records, artifacts, link)
        original = (context["capture"] or {}).get("original")
        if original is not None:
            originals.add(original["sha256"])
        references = [link["artifact"], original]
        references.extend(asset["artifact"] for asset in link["locator"].get("assets", []))
        for reference in references:
            if reference is not None:
                artifacts.read(reference)
                found[reference["sha256"]] = reference
    from .capture_selection import find_capture_selections, find_replaced_texts
    if find_capture_selections(records, reading["version_id"]):
        from .graph import main_captures
        work = records["work"][reading["version_id"]]
        inspected = set(found)
        visuals = {asset["artifact"]["sha256"] for inspection in reading["inspections"]
                   for asset in inspection["link"]["locator"].get("assets", [])}
        for sha256 in find_replaced_texts(records, work) - visuals - originals:
            found.pop(sha256, None)
        for capture in main_captures(records, work):
            if capture["original"]["sha256"] in inspected and capture.get("text") is not None:
                reference = capture["text"]
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
    from .review_composite import read_final_output
    output, composite = read_final_output(records, artifacts, assignment)
    from .openness_reading import PROTOCOL_V5, PROTOCOL_V6, PROTOCOL_V7, find_protocol, find_stage, verify_chain, verify_problem_coverage
    supplied = json.loads(artifacts.read(assignment["packet"]))
    stage = find_stage(supplied)
    if stage == "reading" or (stage == "final" and output.get("stage") == "reading"):
        raise ResearchError("openness_reading_incomplete", "This turn of the reading chain returned no native result; "
                            "continue the chain to a final turn that returns it", {"assignment_id": assignment["id"]})
    finding_contract = uses_finding_contract(assignment)
    if finding_contract:
        # The observed answer was checked against this assignment's immutable contract at ingestion.
        finding = copy.deepcopy(output["findings"]["openness"])
        context = packet["scientific_context"]
        finding.update(statement_digest=packet["claim_digest"],
                       searched_at=context["audit_forecast"]["searched_through"],
                       minimum=context["openness_minimum"], minimum_gaps=context["minimum_gaps"])
    else:
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
    verified = None
    if stage == "final" and find_protocol(supplied) in (PROTOCOL_V5, PROTOCOL_V6, PROTOCOL_V7):
        verified = verify_problem_coverage(records, artifacts, assignment, final=True)
        delivered.difference_update(row["search_digest"] for row in
                                    packet["scientific_context"]["openness_minimum"]["searches"])
        delivered.update(verified["searches"])
    if (verified is not None and find_protocol(supplied) == PROTOCOL_V7
            and 'unread' in verified['state']):
        reference = verified['state']['unread']['artifact']
        delivered.intersection_update(row['artifact']['sha256'] for row in verified['units'])
        finding['unread'] = dict(artifact=reference, digest=reference['sha256'],
                                **json.loads(artifacts.read(reference)))
    if finding_contract:
        # Whole-body receipt credit covers split reading units and reconstructed original searches.
        # The packet's remaining artifacts are delivered whole by the ordinary native evidence boundary.
        finding["evidence"] = sorted(delivered)
    if not isinstance(finding["evidence"], list) or not set(finding["evidence"]) <= delivered:
        raise ResearchError("openness_evidence_mismatch", "Use evidence delivered to the observed reviewer")
    from .problem_openness_reviews import validate_bound_results
    validate_bound_results(packet["scientific_context"].get("openness_minimum", {}), records, artifacts)
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
        from .capture_selection import bind_packet_selections
        bound_records = bind_packet_selections(records, packet["scientific_context"], artifacts=artifacts, review=assignment)
        required_artifacts = {reference["sha256"] for reading in bound_records.get("reading", {}).values()
                              if digest(reading) in reading_digests
                              for reference in collect_reading_artifacts(bound_records, artifacts, reading)}
        if not required_artifacts <= delivered:
            raise ResearchError("openness_evidence_mismatch", "Deliver every decisive reading's inspected original and visual bytes",
                                {"artifact_digests": sorted(required_artifacts - delivered)})
    record = dict(finding, id=value["assignment_id"], assignment_id=value["assignment_id"],
                  statement_id=statement["id"], review=output, recorded_revision=revision)
    if composite is not None:
        record['composite'] = dict(id=composite['id'], digest=digest(composite))
    if stage == "final":
        record["reading_turns"] = verified["turns"] if verified is not None else verify_chain(records, artifacts, assignment)
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
    verified = None
    minimum = finding.get("minimum")
    if minimum and minimum.get("results"):
        from .problem_openness_reviews import has_result_changed, validate_bound_results
        try:
            bodies = validate_bound_results(minimum, records, artifacts)
            changed = any(has_result_changed(records, binding, body)
                          for binding, body in zip(minimum["results"], bodies))
        except ResearchError as error:
            return [obligation("problem_openness_stale", "Reassess changed or unavailable observed openness evidence.",
                               cause=error.code, **affected)]
        if changed:
            from .problem_openness_reviews import RESULT_CHANGED_MESSAGE
            return [obligation("problem_openness_stale", RESULT_CHANGED_MESSAGE,
                               cause="openness_result_changed", **affected)]
    if any(key in finding for key in ("native_reviews", "source_evidence", "reading_turns", "composite")):
        from .problem_openness_reviews import observed_review, validate_native_reviews
        try:
            assignment, state, packet = observed_review(records, artifacts, finding["assignment_id"])
            if "reading_turns" in finding:
                from .openness_reading import PROTOCOL_V5, PROTOCOL_V6, PROTOCOL_V7, find_protocol, verify_chain, verify_problem_coverage
                if find_protocol(json.loads(artifacts.read(assignment["packet"]))) in (PROTOCOL_V5, PROTOCOL_V6, PROTOCOL_V7):
                    verified = verify_problem_coverage(records, artifacts, assignment, final=True)
                turns = verified["turns"] if verified is not None else verify_chain(records, artifacts, assignment)
                if turns != finding["reading_turns"]:
                    raise ResearchError("openness_reading_incomplete", "The recorded reading turns changed")
            observed = state['output']
            composite_matches = True
            if finding.get('composite') is not None:
                from .review_composite import read_final_output
                observed, composite = read_final_output(records, artifacts, assignment)
                composite_matches = (composite is not None and finding['composite'] ==
                                     dict(id=composite['id'], digest=digest(composite)))
            current = validate_native_reviews(records, artifacts, packet, state)
            if current != finding.get("native_reviews") or observed != finding["review"] or not composite_matches:
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
    grounds = [dict(ground, next=GROUND_NEXT_STEPS[ground["kind"]]) for ground in finding.get("grounds", [])]
    minimum = finding.get("minimum")
    if minimum is None:
        assignment = records.get("review_assignment", {}).get(finding.get("assignment_id"))
        if assignment is not None:
            packet = load_packet(artifacts, assignment["context"]["native_packet"], assignment["reviewer_id"])
            minimum = packet["scientific_context"].get("openness_minimum")
    if minimum is None:
        return [obligation("problem_openness_unresolved", "Resolve the literature and reading gaps before claiming openness.", gaps=[], grounds=grounds, **affected)]
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
        unbound = [row["id"] for row in find_material_searches(records, expected, bound_searches) if digest(row) not in bound_searches]
        if (unbound or not bound_searches <= searches or not set(minimum.get("reading_digests", [])) <= readings):
            return [obligation("problem_openness_stale", "Reassess changed or newly material search and reading evidence.",
                               search_ids=unbound, **affected)]
        from .openness_minimum import list_minimum_gaps
        gaps = list_minimum_gaps(minimum, statement["statement"], records, artifacts)
        return [obligation("problem_openness_unresolved", "Resolve the literature and reading gaps before claiming openness.", gaps=gaps, grounds=grounds, **affected)]
    from .openness_minimum import audit_minimum
    try:
        delivered = set(finding["evidence"])
        if verified is not None:
            delivered.difference_update(row["search_digest"] for row in minimum["searches"])
            delivered.update(verified["searches"])
        audit_minimum(finding, statement["statement"], records, artifacts, delivered)
    except ResearchError as error:
        return [obligation("problem_openness_stale", "Reassess changed or newly material search and reading evidence.",
                           cause=error.code, **affected)]
    if finding["outcome"] == "already_solved":
        return [obligation("problem_already_solved", "Redirect to the established result or record a new statement.",
                           settling_work_id=minimum["settling_work_id"], **affected)]
    return []


def serialize_record(record):
    """The exact JSON bytes exported as evidence for a bound store record."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def build_minimum(statement, bound, readings, spec, *, records, artifacts, schema_version=2):
    """Build the declaration and bind complete readings of linked work records."""
    minimum = {"schema_version": schema_version, "statement": statement["statement"], "neighbours": statement["neighbours"],
               "searches": [{"formulation": formulation, "search_digest": digest(search)}
                            for formulation, search in bound],
               "reading_digests": sorted(digest(row) for row in readings.values()),
               "settling_work_id": spec["settling_work_id"], "duplicate_resolutions": spec["duplicate_resolutions"]}
    if schema_version == 2:
        minimum.update(family_readings=spec.get("family_readings", []), supporting_reading_digests=[], results=[])
        from .openness_minimum import validate_minimum
        validate_minimum(minimum)
        bind_family_readings(minimum, records, artifacts)
        decisive = set(minimum["reading_digests"])
        explicit = set(spec.get("supporting_reading_ids", []))
        minimum["supporting_reading_digests"] = sorted(
            digest(row) for row in records.get("reading", {}).values()
            if digest(row) not in decisive and (row["id"] in explicit
                or list_reading_citations(row["id"], [search for _, search in bound])))
    elif spec.get("family_readings"):
        raise ResearchError("invalid_input", FAMILY_READING_V1_REFUSAL, {"field": "family_readings"})
    return minimum


def bind_family_readings(minimum, records, artifacts):
    """Select the latest completely read linked version, or the spec's choice."""
    from .evaluation import Evaluation
    from .identities import find_work_family_link, version_of
    from .openness_minimum import FAMILY_READING_SPEC_REFUSAL

    artifacts = Evaluation.of(records, artifacts)
    searches = {digest(search): search for search in records.get("literature_search", {}).values()}
    required = dict.fromkeys((binding["search_digest"], row["work_id"]) for binding in minimum["searches"]
                             for row in searches[binding["search_digest"]]["dispositions"]
                             if row["disposition"] in {"relevant", "contradictory"})
    choices = {(row["search_digest"], row["work_id"]): row["version_id"] for row in minimum["family_readings"]}
    for key, version in choices.items():
        if key not in required or version not in records.get("work", {}) or version == key[1]:
            raise ResearchError("openness_reading_gap", FAMILY_READING_SPEC_REFUSAL,
                                {"search_digest": key[0], "work_id": key[1], "version_id": version})
    candidates = {row["version_id"] for row in records.get("reading", {}).values() if row["depth"] == "fulltext"}
    reading_digests = set(minimum["reading_digests"])
    bindings = []
    for identity, work in required:
        try:
            own = fulltext_coverage(records, artifacts, work)
        except ResearchError as error:
            if error.code != "missing_version":
                raise
            own = {"complete": False, "readings": {}}
        if own["complete"]:
            reading_digests.update(digest(row) for row in own["readings"].values())
        selected = choices.get((identity, work))
        available = []
        if not own["complete"]:
            for version in sorted(candidates if selected is None else [selected]):
                if version == work or find_work_family_link(records, work, version) is None:
                    continue
                coverage = fulltext_coverage(records, artifacts, version)
                if coverage["complete"]:
                    number = version_of(version)
                    available.append((int(number[1:]) if number else 0, version, coverage))
        if selected is not None and not available:
            raise ResearchError("openness_reading_gap", FAMILY_READING_SPEC_REFUSAL,
                                {"search_digest": identity, "work_id": work, "version_id": selected})
        if available:
            _, version, coverage = max(available, key=lambda row: (row[0], row[1]))
            bindings.append({"search_digest": identity, "work_id": work, "version_id": version})
            reading_digests.update(digest(row) for row in coverage["readings"].values())
    minimum["family_readings"] = bindings
    minimum["reading_digests"] = sorted(reading_digests)


def describe_family_readings(minimum, records, spec):
    """Report automatic bindings with the link and whether authors were compared."""
    from .identities import ARXIV_VERSION_LINK_KIND, find_work_family_link

    explicit = {(row["search_digest"], row["work_id"]) for row in spec.get("family_readings", [])}
    report = []
    for row in minimum.get("family_readings", []):
        if (row["search_digest"], row["work_id"]) in explicit:
            continue
        link = find_work_family_link(records, row["work_id"], row["version_id"])
        compared = link["kind"] != ARXIV_VERSION_LINK_KIND and all(
            records["work"][identifier].get("authors") for identifier in (row["work_id"], row["version_id"]))
        report.append(dict(row, **{FAMILY_READING_LINK_FIELD:
            dict(link, **{FAMILY_READING_AUTHOR_COMPARISON_FIELD: bool(compared)})}))
    return report


def list_reading_citations(reading_id, searches):
    """Name the bound searches and text fields containing an exact reading id."""
    # Unicode alphanumeric characters exclude underscore; reject boundaries as part of the match so an
    # invalid occurrence cannot consume a later, overlapping valid occurrence of the same id.
    pattern = re.compile(r'(?<![^\W_])' + re.escape(reading_id) + r'(?![^\W_])')
    citations = {}
    for search in searches:
        texts = [("dispositions.reason", [row["reason"] for row in search["dispositions"]]),
                 ("resolved.reason", [row["reason"] for row in search.get("resolved", [])]),
                 ("impact", [search["impact"]]), ("scope", [search["scope"]]), ("gaps", search["gaps"]),
                 ("row_dispositions.reason", [row["reason"] for row in search.get("row_dispositions", [])])]
        fields = []
        for field, bodies in texts:
            if any(pattern.search(body) for body in bodies):
                fields.append(field)
        if fields:
            identity = digest(search)
            citations.setdefault(identity, dict(search_id=search["id"], search_digest=identity, fields=fields))
    return list(citations.values())


def export_openness(store, spec, destination=None, *, accept_gaps=False):
    records = store.snapshot()["records"]
    marker = require_problem(records)
    fields(spec, ("assignment_id", "reviewer_id", "minimum"), ("native_reviews", "source_evidence"))
    text(spec["assignment_id"], "Openness assignment ID")
    text(spec["reviewer_id"], "Openness reviewer ID")
    fields(spec["minimum"], ("searches", "settling_work_id", "duplicate_resolutions"),
           ("text_priority", "family_readings", "supporting_reading_ids", "results"))
    if "family_readings" in spec["minimum"] and not isinstance(spec["minimum"]["family_readings"], list):
        raise ResearchError("invalid_input", FAMILY_READING_LIST_REFUSAL, {"field": "family_readings"})
    strings(spec["minimum"].get("supporting_reading_ids", []), "Supporting reading IDs")
    missing = sorted(set(spec["minimum"].get("supporting_reading_ids", [])) - set(records.get("reading", {})))
    if missing:
        raise ResearchError("openness_reading_gap", "minimum.supporting_reading_ids names a reading that this store "
                            "does not hold. Give the ids of reading records of this store.",
                            {"reading_ids": missing})
    from .problem_openness_reviews import bind_results, validate_result_specs
    validate_result_specs(spec["minimum"].get("results", []))
    statement = find_current_statement(records)
    if statement is None:
        raise ResearchError("problem_statement_missing", "Record the exact statement before openness review")
    artifacts = ArtifactStore(store.root)
    bound, required, evidence = [], set(), {}

    def include_record(record):
        data = serialize_record(record)
        artifact = artifacts.put(data, "application/json")
        evidence[artifact["sha256"]] = {"digest": artifact["sha256"], "kind": "blob", "artifact": artifact}

    for binding in spec["minimum"]["searches"]:
        fields(binding, ("formulation", "search_id"))
        search = records.get("literature_search", {}).get(binding["search_id"])
        if search is None:
            raise ResearchError("openness_search_missing", "Bind this store's exact search", binding)
        bound.append((binding["formulation"], search))
        required.update(row["work_id"] for row in search["dispositions"] if row["disposition"] in {"relevant", "contradictory"})
    decisive = set(required)
    if spec["minimum"]["settling_work_id"] is not None:
        required.add(spec["minimum"]["settling_work_id"])
    readings = {}
    for work in sorted(required):
        try:
            coverage = fulltext_coverage(records, artifacts, work)
        except ResearchError as error:
            if error.code != "missing_version" and not (error.code == "unknown_work" and work not in decisive):
                raise
            # The assessment reports a versionless source or an unacquired
            # declared settling work. Stored artifact failures keep their code.
            continue
        for reading_id in coverage["readings"]:
            readings[reading_id] = records["reading"][reading_id]
    minimum = build_minimum(statement, bound, readings, spec["minimum"], records=records, artifacts=artifacts)
    minimum["results"], result_evidence = bind_results(records, artifacts, spec["minimum"].get("results", []))
    evidence.update(result_evidence)
    # Check the declaration against the store before writing the packet.
    from .openness_minimum import validate_minimum_bindings
    validate_minimum_bindings(minimum, statement["statement"], records)
    if minimum["schema_version"] == 2:
        reading_digests = set(minimum["reading_digests"])
        readings = {identifier: row for identifier, row in records.get("reading", {}).items()
                    if digest(row) in reading_digests}
    from .openness_minimum import (find_searched_through, forecast_outcomes, list_minimum_gaps, list_minimum_notices)
    gaps = list_minimum_gaps(minimum, statement["statement"], records, artifacts)
    forecast = {"outcomes": forecast_outcomes(gaps), "gaps": gaps,
                "notices": list_minimum_notices(minimum, records, artifacts),
                "searched_through": find_searched_through(minimum, records)}
    blocked = all(outcome == "blocked" for outcome in forecast["outcomes"].values())
    if blocked and not accept_gaps:
        raise ResearchError("openness_minimum_gaps", "The bound minimum blocks both resolved outcomes. Repair the "
                            "reported gaps, or use --accept-gaps to export for an unresolved finding.", forecast)
    for _, search in bound:
        include_record(search)
    for reading in readings.values():
        include_record(reading)
        for artifact in collect_reading_artifacts(records, artifacts, reading):
            evidence[artifact["sha256"]] = {"digest": artifact["sha256"], "kind": "artifact", "artifact": artifact}
    supporting = [row for row in records.get("reading", {}).values()
                  if digest(row) in minimum.get("supporting_reading_digests", [])]
    for reading in supporting:
        include_record(reading)
    include_record(statement)
    from .capture_selection import describe_capture_selections
    read_decisive_work_ids = {reading["version_id"] for reading in readings.values()
                              if reading["version_id"] in decisive}
    read_decisive_work_ids.update(row["work_id"] for row in minimum.get("family_readings", []))
    scientific_context = {"openness_minimum": minimum, "minimum_gaps": gaps, "audit_forecast": forecast,
                          "decisive_work_ids": sorted(read_decisive_work_ids),
                          **describe_capture_selections(records)}
    if minimum.get("family_readings"):
        from .openness_reading import FAMILY_READING_PLAN_VERSION
        scientific_context["family_reading_plan_version"] = FAMILY_READING_PLAN_VERSION
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
    packet = {"protocol": PROTOCOL, "kind": "result", "root": str(store.root.resolve()), "objective_id": statement["id"],
              "contract": {"orchestration": "problem_creation", "area": marker["area"]}, "subject": statement,
              "subject_digest": digest(statement), "claim_digest": compute_statement_digest(statement),
              "reviewer": {"source": "host", "actor_id": spec["reviewer_id"], "attestation_id": ATTESTATION_PREFIX + spec["assignment_id"]},
              "scientific_context": scientific_context, "evidence": [evidence[key] for key in sorted(evidence)],
              "execution_provenance": [], "unmaterialized_digests": []}
    if spec["minimum"].get("text_priority"):
        # The research record declares which decisive full texts a text-limited route delivers first; the transport
        # applies the route limit to the rest.
        strings(spec["minimum"]["text_priority"], "Text delivery priority")
        from .openness_reading import list_reading_texts
        reading_texts = list_reading_texts(records, packet)
        priority = []
        for version in spec["minimum"]["text_priority"]:
            texts = sorted(row["text_sha256"] for row in reading_texts if row["version_id"] == version)
            if not texts:
                raise ResearchError("openness_reading_gap", "Text delivery priority requires an extracted main text "
                                    "carried by this packet for a bound reading", {"version_id": version})
            priority.extend(texts)
        scientific_context["text_delivery_priority"] = priority
        scientific_context["text_delivery_rule"] = (
            "On a text-limited reviewer route the bodies of the listed full texts are delivered in this order "
            "while the route input limit allows; a body that does not fit is replaced by an explicit elision "
            "note, and the reading record of that work (its notes and inspected excerpts) is delivered in full.")
    if "native_reviews" in scientific_context:
        from .problem_openness_sources import build_delivery
        scientific_context["openness_delivery"] = build_delivery(records, artifacts, packet)
    reference = artifacts.put(json.dumps(packet, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(), "application/json")
    load_packet(artifacts, reference, spec["reviewer_id"])
    from .review_delivery import deliver_packet, find_packet_destination
    if destination is None:
        destination = find_packet_destination(store, spec["assignment_id"], packet=packet)
    delivered = deliver_packet(store, destination, packet, transitive=True, reuse=True)
    result = dict(delivered, native_packet=reference, subject_digest=packet["subject_digest"], claim_digest=packet["claim_digest"],
                  audit_forecast=forecast)
    if minimum["schema_version"] == 2:
        result[FAMILY_READING_REPORT_FIELD] = describe_family_readings(minimum, records, spec["minimum"])
        result["supporting_readings"] = [dict(reading_id=row["id"], reading_digest=digest(row),
            cited_by=list_reading_citations(row["id"], [search for _, search in bound]))
            for row in sorted(supporting, key=lambda row: row["id"])]
    if blocked:
        result["warnings"] = [{"code": "openness_minimum_gaps", "message": GAPPED_EXPORT_MESSAGE, "details": forecast}]
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
            result.setdefault("warnings", []).append({"code": "review_input_too_large", "message":
                "The estimated size of this packet exceeds the input limit of the text routes in route_ids, and "
                "without minimum.text_priority a route withholds no full text by priority. List the version ids of the "
                "read works in minimum.text_priority, the decisive ones first, so that a route withholds full-text bodies "
                "from the end of the list. The estimate counts the packet and its UTF-8 evidence as JSON strings, so a "
                "delivery projection or a framed route can send less.",
                "details": {"route_ids": routes, "estimated_characters": estimate}})
    return result


def _is_utf8(data):
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
