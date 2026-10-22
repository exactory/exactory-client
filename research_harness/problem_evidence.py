"""Validate and deliver the exact readings and empirical support a post cites."""

from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, strings, text
from .problem_claims import find_current_statement, find_latest, find_value, find_assessment, compute_statement_digest
from .reading import fulltext_coverage, validate_read_evidence

# The reviewer reads this note with the problem evidence of a result packet.
INLINE_DELIVERY_NOTE = (
    "Each passage is the exact text of one inspected span of a reading, with the reading id, the inspection index and "
    "the offsets of the span in the source text. An inspection that covers the complete text is listed in its reading "
    "with whole_text true and is not repeated as a passage. Full texts, including those that a material objection "
    "names by reference, are not attached in this turn; request a held full text with source_requests by its "
    "version_id. Cite a delivered record by copying its cite object, which names the record by kind and id. "
    "A source link is citable only for a full text that a source request delivered to you. "
    "This review is verified only when it calls no tool, so a held text is obtained only through a source request.")


def collect_value_items(value):
    return [item for section in ("motivation", "current_state") for paragraph in (value or {}).get(section, [])
            for item in paragraph["evidence"]]


def validate_reading(records, artifacts, item):
    fields(item, ("kind", "version_id", "reading_id"))
    reading = records.get("reading", {}).get(item["reading_id"])
    from .source_deferrals import assess_deferrals
    deferred = any(row["version_id"] == item["version_id"] and row["status"] in {"active", "stale"}
                   for row in assess_deferrals(records, artifacts))
    if (reading is None or reading["version_id"] != item["version_id"] or reading["depth"] != "fulltext" or deferred):
        raise ResearchError("problem_value_reading_missing", "Read the exact cited work in full; a deferral gives no evidence credit", item)
    bundle = records.get("source_bundle", {}).get(reading.get("bundle_id"))
    if bundle is None or not fulltext_coverage(records, artifacts, item["version_id"])["complete"]:
        raise ResearchError("problem_value_reading_missing", "Complete the current work's required source units", item)
    target = {"id": item["version_id"], "sha256": bundle["original_sha256"]}
    pinned = dict(records, reading={reading["id"]: reading})
    if not fulltext_coverage(pinned, artifacts, item["version_id"], target=target)["complete"]:
        raise ResearchError("problem_value_reading_missing", "Complete the cited reading's required source units", item)
    for inspection in reading["inspections"]:
        validate_read_evidence(pinned, artifacts, inspection["link"], target=target)
    return reading


def validate_formal_request(value):
    if value is None:
        return
    fields(value, ("claim", "required_deliverables"))
    claim = value["claim"]
    fields(claim, ("statement", "quantifiers", "assumption_ids", "proof_policy", "scope"))
    for key in ("statement", "quantifiers"):
        text(claim[key], "Native claim " + key)
    strings(claim["assumption_ids"], "Native assumption IDs")
    if claim["proof_policy"] not in {"certificate", "lean-kernel"}:
        raise ResearchError("invalid_input", "Requested formal support requires certificate or lean-kernel policy")
    scope = claim["scope"]
    if not isinstance(scope, dict):
        raise ResearchError("invalid_input", "Native scope must be an object")
    if scope.get("kind") == "named":
        fields(scope, ("kind", "name"))
        text(scope["name"], "Native scope name")
    elif scope.get("kind") == "case_ids":
        fields(scope, ("kind", "case_ids"))
        strings(scope["case_ids"], "Native cases", nonempty=True)
    elif scope.get("kind") == "integer_interval":
        fields(scope, ("kind", "lower", "upper", "lower_inclusive", "upper_inclusive"))
        if (type(scope["lower"]) is not int or type(scope["upper"]) is not int
                or type(scope["lower_inclusive"]) is not bool or type(scope["upper_inclusive"]) is not bool
                or scope["lower"] + (not scope["lower_inclusive"]) > scope["upper"] - (not scope["upper_inclusive"])):
            raise ResearchError("invalid_input", "Native integer scope must be nonempty with explicit endpoint flags")
    else:
        raise ResearchError("invalid_input", "Use a valid native claim scope")
    strings(value["required_deliverables"], "Requested native deliverables")


def validate_computation(records, artifacts, item, *, recorded_revision=None):
    fields(item, ("kind", "execution_id", "output_id", "formal_check"))
    validate_formal_request(item["formal_check"])
    affected = {"execution_id": item["execution_id"], "output_id": item["output_id"]}

    def raise_missing_computation(part):
        raise ResearchError("problem_value_computation_missing", "Cited empirical support requires its " + part, affected)

    execution = records.get("execution", {}).get(item["execution_id"])
    if execution is None:
        raise_missing_computation("execution")
    payload = execution["payload"]
    origin = payload["origin"]
    admission = records.get("execution_admission", {}).get(origin.get("admission_id"))
    outcome = records.get("execution_outcome", {}).get(origin.get("admission_id"))
    if admission is None or outcome is None or outcome["execution_id"] != item["execution_id"]:
        raise_missing_computation("admission and reconciled outcome")
    output = next((row for row in payload["outputs"] if row["id"] == item["output_id"]), None)
    if output is None or payload["status"] != "completed":
        raise_missing_computation("completed output")
    from .probe_provenance import find_probe_artifact_origins
    if find_probe_artifact_origins(records, [output["artifact"]], artifacts=artifacts):
        raise ResearchError("exploratory_evidence", "Probe-origin bytes cannot support a problem claim", affected)
    artifacts.read(output["artifact"])
    assessments = sorted((row for row in records.get("cycle_assessment", {}).values()
                          if recorded_revision is None or row["assessed_revision"] <= recorded_revision),
                         key=lambda row: row["assessed_revision"], reverse=True)
    for assessment in assessments:
        report = assessment["assessment"]
        result_references = {digest(reference) for reference in assessment["payload"]["result"]["evidence"]}
        own = [row for row in report["evidence"] if row.get("cycle_id") == payload["cycle_id"]
               and row.get("requirement_kind") == "result" and row.get("reference", {}).get("execution_id") == item["execution_id"]
               and row["reference"].get("output_id") == item["output_id"] and digest(row["reference"]) in result_references]
        if not own or item["execution_id"] not in assessment["payload"]["execution_ids"]:
            continue
        # A stored assessment can contain several results. Its aggregate
        # unbound-validation finding does not identify the affected output.
        # Recover that binding from the assessment's own delivered references
        # and immutable execution inputs, without reassessing current science.
        evidence = {digest(row["reference"]): row for row in report["evidence"]}
        validated_requirements = set()
        validated = False
        for check in assessment["payload"]["validity_checks"]:
            if check["status"] != "passed":
                continue
            for reference in check["evidence"]:
                validation = evidence.get(digest(reference), {})
                if validation.get("requirement_kind") != "validation" or validation.get("status") != "completed":
                    continue
                checked_run = records["execution"][reference["execution_id"]]["payload"]
                inputs = (checked_run["command"] or {}).get("inputs", [])
                if reference["execution_id"] == item["execution_id"] or output["artifact"] in inputs:
                    validated = True
                    if validation["cycle_id"] == payload["cycle_id"]:
                        validated_requirements.add(validation["requirement_id"])
        blocked = []
        validity_codes = {row["code"] for row in report.get("prerequisites", {}).get("validity", [])}
        validity_codes.update({"validity_evidence_unbound", "required_validation_unverified", "validity_unresolved"})
        for pending in report["obligations"]:
            code = pending["code"]
            relevant_result = (pending.get("execution_id", item["execution_id"]) == item["execution_id"]
                               and pending.get("output_id", item["output_id"]) == item["output_id"])
            if code == "validity_evidence_unbound" and "execution_id" not in pending and "output_id" not in pending:
                relevant_result = not validated
            if code == "required_validation_unverified" and relevant_result:
                relevant_result = not set(pending.get("requirement_ids", [])) <= validated_requirements
            if code in {"required_result_unverified", "required_evidence_missing"} and relevant_result:
                relevant_result = bool(set(pending.get("requirement_ids", [])) & {row["requirement_id"] for row in own})
            if code == "result_execution_incomplete" and relevant_result:
                relevant_result = any(row["status"] != "completed" for row in own)
            if code == "execution_coverage_missing" and relevant_result:
                relevant_result = item["execution_id"] not in assessment["payload"]["execution_ids"]
            if code == "validity_unresolved" and relevant_result:
                check = next((row for row in assessment["payload"]["validity_checks"]
                              if row["id"] == pending.get("check_id")), None)
                result_references = [] if check is None else [reference for reference in check["evidence"]
                                                              if reference.get("kind") == "result"]
                relevant_result = not result_references
                for reference in result_references:
                    checked_run = records["execution"][reference["execution_id"]]["payload"]
                    inputs = (checked_run["command"] or {}).get("inputs", [])
                    if reference["execution_id"] == item["execution_id"] or output["artifact"] in inputs:
                        relevant_result = True
            if ((code in validity_codes and relevant_result)
                    or (code in {"resource_limit_exceeded", "execution_pending"}
                        and pending.get("admission_id", admission["id"]) == admission["id"])):
                blocked.append(pending)
        if blocked:
            raise ResearchError("problem_value_computation_missing", "Resolve this execution's recorded validity or resource obligations",
                                dict(affected, assessment_id=assessment["id"], obligations=blocked))
        for row in payload["outputs"]:
            artifacts.read(row["artifact"])
        return {"execution_id": item["execution_id"], "output_id": item["output_id"], "admission": admission,
                "execution": execution, "assessment": assessment, "outputs": payload["outputs"],
                "validations": assessment["payload"]["validity_checks"]}
    raise_missing_computation("assessment listing this result")


def validate_result(records, item):
    from .problem_attempt import find_attempt
    fields(item, ("kind", "attempt_id", "acceptance_id"))
    text(item["attempt_id"], "Result attempt ID")
    text(item["acceptance_id"], "Result acceptance ID")
    attempt, statement = find_attempt(records), find_current_statement(records)
    affected = {key: item[key] for key in ("attempt_id", "acceptance_id")}
    if (attempt is not None and statement is not None and attempt["id"] == item["attempt_id"]
            and attempt["statement_id"] == statement["id"] and attempt["kind"] == "solver"):
        native = records.get("native_attempt", {}).get(attempt["solver"]["native_attempt_digest"])
        if native is not None:
            acceptance = next((row for row in native["acceptances"] if row["id"] == item["acceptance_id"]), None)
            if (acceptance is not None and acceptance["status"] == "accepted"
                    and acceptance["outcome"] in ("proof", "reduction", "obstruction")):
                return acceptance
    raise ResearchError("problem_value_result_missing", "Cite an accepted mathematical result of the current solver attempt", affected)


def collect_results(records):
    results = {}
    for item in collect_value_items(find_value(records)):
        if item["kind"] == "result":
            results[(item["attempt_id"], item["acceptance_id"])] = validate_result(records, item)
    return [(attempt_id, acceptance) for (attempt_id, _), acceptance in sorted(results.items())]


def validate_value(records, artifacts, value):
    for section in ("motivation", "current_state"):
        paragraphs = value[section]
        if not isinstance(paragraphs, list) or not paragraphs:
            raise ResearchError("invalid_input", "Record nonempty paragraphs for " + section)
        for paragraph in paragraphs:
            fields(paragraph, ("text", "evidence"))
            text(paragraph["text"], "Problem value paragraph")
            if not isinstance(paragraph["evidence"], list) or not paragraph["evidence"]:
                raise ResearchError("invalid_input", "Every value paragraph requires evidence")
            for item in paragraph["evidence"]:
                if not isinstance(item, dict) or item.get("kind") not in {"reading", "computation", "result"}:
                    raise ResearchError("invalid_input", "Use a full reading, admitted assessed computation or accepted mathematical result")
                if item["kind"] == "reading":
                    validate_reading(records, artifacts, item)
                elif item["kind"] == "computation":
                    validate_computation(records, artifacts, item, recorded_revision=value.get("recorded_revision"))
                else:
                    validate_result(records, item)


def bind_formal_check(records, artifacts, payload):
    fields(payload["evidence"], ("execution_id", "output_id"), code="problem_formal_check_unbound")
    evidence = payload["evidence"]
    native = records.get("native_closure", {}).get(payload["native_closure_digest"])
    if native is None:
        raise ResearchError("problem_formal_check_unbound", "Bind the current requested support to a closure delivered in this store", evidence)
    closure, contract = native["closure"], native["contract"]
    subject, audit = closure["subject"], closure["audit"]
    delivered = {row["requirement"] for row in subject["deliverables"]}
    candidates = [item for item in collect_value_items(find_value(records))
                  if item["kind"] == "computation" and all(item[key] == evidence[key] for key in evidence)
                  and item["formal_check"] is not None and item["formal_check"]["claim"] == contract["original_claim"]
                  and set(item["formal_check"]["required_deliverables"]) <= set(contract["required_deliverables"]) & delivered]
    if not candidates:
        raise ResearchError("problem_formal_check_unbound", "The native completion must establish a current requested support claim", evidence)
    unbound = [item for item in candidates if not any(record["evidence"] == evidence and record["formal_check"] == item["formal_check"]
               for record in records.get("problem_formal_check", {}).values())]
    item = (unbound or candidates)[0]
    request = item["formal_check"]
    if (digest(closure) != payload["native_closure_digest"] or subject["contract_digest"] != digest(contract)
            or contract["original_claim"] != request["claim"] or contract["proof_policy"] != request["claim"]["proof_policy"]
            or not set(request["required_deliverables"]) <= set(contract["required_deliverables"]) & delivered
            or subject["outcome"] != "proof" or audit["subject_digest"] != digest(subject)
            or audit["acceptance_ids"] != subject["acceptance_ids"] or audit["evidence_digests"] != subject["evidence_digests"]):
        raise ResearchError("problem_formal_check_unbound", "The native completion must establish the exact requested claim, policy and deliverables", evidence)
    return {"evidence": evidence, "formal_check": request, "native_closure_digest": payload["native_closure_digest"],
            "contract_digest": digest(contract)}


def find_formal_checks(records):
    items = collect_value_items(find_value(records))
    return [record for record in records.get("problem_formal_check", {}).values()
            if any(item["kind"] == "computation" and item["formal_check"] == record["formal_check"]
                   and all(item[key] == record["evidence"][key] for key in record["evidence"]) for item in items)]


def assess_formal_checks(records):
    current = find_formal_checks(records)
    pending = []
    for item in collect_value_items(find_value(records)):
        if item["kind"] != "computation" or item["formal_check"] is None:
            continue
        evidence = {key: item[key] for key in ("execution_id", "output_id")}
        if not any(record["evidence"] == evidence and record["formal_check"] == item["formal_check"] for record in current):
            earlier = [r for r in records.get("problem_formal_check", {}).values() if r["evidence"] == evidence and r not in current]
            pending.append(obligation("problem_formal_check_stale" if earlier else "problem_formal_check_missing",
                "Bind and deliver native completion of this exact requested support.", formal_check_digest=digest(item["formal_check"]), **evidence))
    # A changed request on current cited support needs rebinding. Historical
    # support that the current value no longer cites creates no post obligation.
    for record in records.get("problem_formal_check", {}).values():
        cited = any(item["kind"] == "computation" and all(item[key] == record["evidence"][key] for key in record["evidence"])
                    for item in collect_value_items(find_value(records)))
        if (cited and record not in current and not any(row["evidence"] == record["evidence"] for row in current)
                and not any(p.get("execution_id") == record["evidence"]["execution_id"]
                                             and p.get("output_id") == record["evidence"]["output_id"] for p in pending)):
            pending.append(obligation("problem_formal_check_stale", "Reassess the removed or changed formal support.",
                                      formal_check_id=record["id"], **record["evidence"]))
    return pending


def resolve_assessment_record(records, item, reference):
    fields(reference, ("kind", "record_kind", "id", "digest"), code="invalid_strategy")
    text(reference["record_kind"], "Evidence record kind", code="invalid_strategy")
    text(reference["id"], reference["record_kind"] + " ID", code="strategy_reference_missing")
    affected = {key: reference[key] for key in ("record_kind", "id")}
    record = records.get(reference["record_kind"], {}).get(reference["id"])
    if record is None or digest(record) != reference["digest"]:
        raise ResearchError("problem_assessment_evidence_missing",
                            "Deliver the exact record cited by this assessment", dict(item=item, **affected))
    return record


def resolve_assessment_evidence(records):
    from .problem_rubric import ITEMS
    assessment, statement = find_assessment(records), find_current_statement(records)
    if assessment is None or statement is None or assessment["statement_id"] != statement["id"]:
        return []
    seen, resolved = set(), []
    for item in ITEMS:
        for reference in assessment["items"][item]["evidence"]:
            identity = digest(reference)
            if identity in seen:
                continue
            seen.add(identity)
            if reference.get("kind") == "record":
                record = resolve_assessment_record(records, item, reference)
                resolved.append(dict(reference, record=record))
            elif reference.get("kind") == "source":
                link = reference["link"]
                readings = [identifier for identifier, reading in sorted(records.get("reading", {}).items())
                            if reading["depth"] == "fulltext" and (reading["version_id"] == link["version_id"]
                                or any(inspection["link"] == link for inspection in reading["inspections"]))]
                if not readings:
                    raise ResearchError("problem_assessment_evidence_missing",
                                        "Deliver full readings of the source cited by this assessment", {"item": item, "link": link})
                resolved.append(dict(reference, reading_ids=readings))
            else:
                resolved.append(reference)
    return resolved


def collect_readings(records):
    from .problem_attempt import find_attempt
    openness = find_latest(records, "problem_openness")
    minimum = (openness or {}).get("minimum") or {}
    digests = set(minimum.get("reading_digests", []))
    identifiers = {item["reading_id"] for item in collect_value_items(find_value(records)) if item["kind"] == "reading"}
    for reference in resolve_assessment_evidence(records):
        if reference.get("kind") == "source":
            identifiers.update(reference["reading_ids"])
    attempt, statement = find_attempt(records), find_current_statement(records)
    if (attempt is not None and statement is not None and attempt["statement_id"] == statement["id"]
            and attempt["kind"] == "literature"):
        identifiers.update(row["reading_id"] for row in attempt["literature"]["readings"])
    return [row for identifier, row in sorted(records.get("reading", {}).items())
            if identifier in identifiers or digest(row) in digests]


def find_citation_locator(work, units):
    """The locator of a cited work: its DOI, else its arXiv id, else the first HTTPS url of a unit of the bundle that
    its reading binds. None when the work has none of them."""
    identifiers = [work["id"], *work.get("aliases", [])]
    locator = next((item[4:] for item in identifiers if item.startswith("doi:")), None)
    locator = locator or next((item[6:] for item in identifiers if item.startswith("arxiv:")), None)
    return locator or next((unit["url"] for unit in units
                            if isinstance(unit.get("url"), str) and unit["url"].startswith("https://")), None)


def project_citations(records, artifacts):
    from .identities import family_id
    seen, citations = set(), []
    for reading in collect_readings(records):
        version = reading["version_id"]
        work = records["work"][version]
        family = family_id(version)
        if family in seen:
            continue
        seen.add(family)
        bundle = records.get("source_bundle", {}).get(reading.get("bundle_id"))
        locator = find_citation_locator(work, (bundle or {}).get("units", []))
        if locator is None:
            raise ResearchError("problem_citation_locator_missing", "Record a DOI, arXiv id or HTTPS locator for the read source",
                                {"version_id": version, "reading_id": reading["id"]})
        authors = ", ".join(a if isinstance(a, str) else a.get("name", "") for a in work.get("authors", []))
        citation = ". ".join(part for part in (authors, work["title"], str(work.get("year") or "")) if part)
        citations.append({"citation": citation, "locator": locator})
    return citations


def describe_openness_searches(records, openness):
    """Each search that the openness minimum binds, with its formulation and its captured source records. An
    unresolved finding is recorded without an audit, so a search it names can be absent from the store."""
    if openness is None or not openness.get("minimum"):
        return []
    searches = {digest(row): row for row in records.get("literature_search", {}).values()}
    bound = []
    for item in openness["minimum"]["searches"]:
        search = searches.get(item["search_digest"])
        if search is None:
            continue
        bound.append({"formulation": item["formulation"], "search_digest": item["search_digest"], "search": search,
                      "sources": [records["source"][response["source_id"]] for response in search["responses"]]})
    return bound


def describe_binding(records, artifacts):
    from .problem_attempt import find_current_attempt
    statement = find_current_statement(records)
    attempt = find_current_attempt(records)
    openness, value = find_latest(records, "problem_openness"), find_value(records)
    computations = [validate_computation(records, artifacts, item, recorded_revision=value.get("recorded_revision")) for item in collect_value_items(value)
                    if item["kind"] == "computation"]
    return {"claim": statement, "openness": openness,
            "openness_searches": [{"formulation": row["formulation"], "search_digest": row["search_digest"],
                                   "source_digests": [digest(source) for source in row["sources"]]}
                                  for row in describe_openness_searches(records, openness)],
            "value": value, "assessment": find_assessment(records),
            "assessment_evidence": [{key: value for key, value in row.items() if key != "record"}
                                    for row in resolve_assessment_evidence(records)],
            "attempt": None if attempt is None else {"id": attempt["id"], "digest": digest(attempt),
                "native_attempt_digest": None if attempt["solver"] is None else attempt["solver"]["native_attempt_digest"]},
            "results": [{"attempt_id": attempt_id, "acceptance_id": acceptance["id"], "digest": digest(acceptance)}
                        for attempt_id, acceptance in ([] if attempt is None else collect_results(records))],
            "readings": [{"id": row["id"], "digest": digest(row)} for row in collect_readings(records)],
            "computations": [{"execution_id": row["execution_id"], "output_id": row["output_id"],
                              **{key: digest(row[key]) for key in ("admission", "execution", "assessment")}} for row in computations],
            "formal_checks": [{"id": row["id"], "digest": digest(row),
                               "native_closure": digest(records["native_closure"][row["native_closure_digest"]])}
                              for row in find_formal_checks(records)], "citations": project_citations(records, artifacts)}


def describe_evidence(records, artifacts):
    from .direction_sources import build_inline_passages
    from .evaluation import Evaluation
    from .problem_attempt import find_current_attempt
    from .review_protocol import _reference_templates
    from .source_links import TEXT_KINDS, covers_text
    statement, value = find_current_statement(records), find_value(records)
    openness = find_latest(records, "problem_openness")
    assessment = find_assessment(records)
    attempt = find_current_attempt(records)
    attempt_evidence = None
    if attempt is not None:
        attempt_evidence = dict({key: attempt[key] for key in ("id", "kind", "outcomes", "proportion")},
                                digest=digest(attempt), literature=attempt["literature"], acceptances=[])
        if attempt["kind"] == "solver":
            native = records["native_attempt"][attempt["solver"]["native_attempt_digest"]]
            attempt_evidence["record_references"] = [
                cite_record("native_attempt", attempt["solver"]["native_attempt_digest"], native)]
            attempt_evidence["applicability"] = attempt["solver"]["applicability"]
            attempt_evidence.update({key: native[key] for key in (
                "state", "totals", "contract", "obligations", "failed_strategies", "retreats", "nodes")})
            cited = [row["evidence"]["acceptance_id"] for section in ("footholds", "obstructions")
                     for row in attempt["outcomes"][section]]
            for identifier in dict.fromkeys(cited):
                acceptance = next(row for row in native["acceptances"] if row["id"] == identifier)
                checkpoint = next(row for row in native["checkpoints"] if row["id"] == acceptance["checkpoint_id"])
                attempt_evidence["acceptances"].append({"acceptance_id": identifier, "claim": checkpoint["claim"],
                    "outcome": acceptance["outcome"], "standard": acceptance["standard"]})
    readings = collect_readings(records)
    evaluation = Evaluation.of(records, artifacts)
    prepared = build_inline_passages(records, evaluation, readings)
    # A reading arrives with its notes and, for each inspection, its unit, note and offsets; the text of its inspected
    # spans arrives once, as inline passages, so its source links are not attached again.
    delivered_readings = []
    for row in readings:
        inspections = []
        for index, inspection in enumerate(row["inspections"]):
            link = inspection["link"]
            entry = dict({key: value for key, value in inspection.items() if key != "link"},
                         index=index, version_id=link["version_id"])
            if link["locator"]["kind"] in TEXT_KINDS:
                entry.update(start=link["locator"]["start"], end=link["locator"]["end"],
                             whole_text=covers_text(evaluation, link))
            else:
                entry["link"] = _reference_templates(link)
            inspections.append(entry)
        delivered_readings.append({"id": row["id"], "version_id": row["version_id"], "digest": digest(row),
                                   "depth": row["depth"], "reading": dict(
                                       {key: value for key, value in row.items() if key != "inspections"},
                                       inspections=inspections)})
    held = {(row["id"], row["digest"]) for row in delivered_readings}
    computations = [validate_computation(records, artifacts, item, recorded_revision=value.get("recorded_revision")) for item in collect_value_items(value)
                    if item["kind"] == "computation"]
    results = []
    for attempt_id, acceptance in ([] if attempt is None else collect_results(records)):
        native = records["native_attempt"][records["problem_attempt"][attempt_id]["solver"]["native_attempt_digest"]]
        checkpoint = next(row for row in native["checkpoints"] if row["id"] == acceptance["checkpoint_id"])
        results.append({"attempt_id": attempt_id, "acceptance_id": acceptance["id"], "claim": checkpoint["claim"],
                        "outcome": acceptance["outcome"], "standard": acceptance["standard"]})
    # Each projected record carries the stored record's id and digest, so that a reviewer can cite it exactly.
    delivered = {"claim": None if statement is None else dict(
                {key: statement[key] for key in ("title", "area", "statement", "resolution_criteria", "formal_statement")},
                id=statement["id"], digest=digest(statement),
                statement_id=statement["id"], statement_digest=compute_statement_digest(statement)),
            "openness": None if openness is None else dict({key: openness.get(key) for key in (
                "id", "outcome", "statement_digest", "reason", "coverage", "searched_at", "evidence", "minimum")},
                digest=digest(openness)),
            "openness_searches": describe_openness_searches(records, openness),
            "value": None if value is None else dict({key: value[key] for key in ("id", "motivation", "current_state")},
                                                     digest=digest(value)),
            "assessment": None if assessment is None else dict(
                {key: assessment[key] for key in ("id", "grades", "items", "primary")}, digest=digest(assessment)),
            "assessment_evidence": [
                dict({key: value for key, value in reference.items() if key != "record"}, delivered_under="readings")
                if reference.get("kind") == "record" and reference.get("record_kind") == "reading"
                and (reference["id"], reference["digest"]) in held else reference
                for reference in resolve_assessment_evidence(records)],
            "attempt": attempt_evidence,
            "results": results,
            "citations": project_citations(records, artifacts),
            "readings": delivered_readings,
            "passages": [passage for passage, _ in prepared],
            "delivery": {"protocol": "inline-passages-v1", "note": INLINE_DELIVERY_NOTE},
            "computations": [dict(row, record_references=[cite_record(kind, row[key]["id"], row[key]) for key, kind in (
                ("admission", "execution_admission"), ("execution", "execution"), ("assessment", "cycle_assessment"))])
                for row in computations],
            "formal_checks": [dict(row, digest=digest(row), native_closure=records["native_closure"][row["native_closure_digest"]],
                                   record_references=[cite_record("native_closure", row["native_closure_digest"],
                                                                  records["native_closure"][row["native_closure_digest"]])])
                              for row in find_formal_checks(records)]}
    # Each projected record names how a reviewer cites it, by kind and id.
    for key, kind, field in (("claim", "problem_statement", "statement_id"), ("openness", "problem_openness", "id"),
                             ("value", "problem_value", "id"), ("assessment", "problem_assessment", "id"),
                             ("attempt", "problem_attempt", "id")):
        entry = delivered[key]
        if entry is not None and entry.get(field) is not None:
            entry["cite"] = {"kind": "record", "record_kind": kind, "id": entry[field]}
    for entry in delivered["readings"]:
        entry["cite"] = {"kind": "record", "record_kind": "reading", "id": entry["id"]}
    for row in delivered["openness_searches"]:
        row["cite"] = {"kind": "record", "record_kind": "literature_search", "id": row["search"]["id"]}
    return delivered


def cite_record(kind, identifier, record):
    """The exact reference by which a reviewer cites a stored record that a packet carries."""
    return {"kind": "record", "record_kind": kind, "id": identifier, "digest": digest(record)}
