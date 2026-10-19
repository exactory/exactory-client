"""Validate and deliver the exact readings and empirical support a post cites."""

from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, strings, text
from .problem_claims import find_current_statement, find_latest, find_value, find_assessment, compute_statement_digest
from .reading import fulltext_coverage, validate_read_evidence


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
                if not isinstance(item, dict) or item.get("kind") not in {"reading", "computation"}:
                    raise ResearchError("invalid_input", "Use a full reading or admitted assessed computation")
                if item["kind"] == "reading":
                    validate_reading(records, artifacts, item)
                else:
                    validate_computation(records, artifacts, item, recorded_revision=value.get("recorded_revision"))


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


def collect_readings(records):
    openness = find_latest(records, "problem_openness")
    minimum = (openness or {}).get("minimum") or {}
    digests = set(minimum.get("reading_digests", []))
    identifiers = {item["reading_id"] for item in collect_value_items(find_value(records)) if item["kind"] == "reading"}
    return [row for identifier, row in sorted(records.get("reading", {}).items())
            if identifier in identifiers or digest(row) in digests]


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
        identifiers = [work["id"], *work.get("aliases", [])]
        locator = next((item[4:] for item in identifiers if item.startswith("doi:")), None)
        locator = locator or next((item[6:] for item in identifiers if item.startswith("arxiv:")), None)
        if locator is None:
            bundle = records.get("source_bundle", {}).get(reading.get("bundle_id"))
            locator = next((unit["url"] for unit in (bundle or {}).get("units", [])
                            if isinstance(unit.get("url"), str) and unit["url"].startswith("https://")), None)
        if locator is None:
            raise ResearchError("problem_citation_locator_missing", "Record a DOI, arXiv id or HTTPS locator for the read source",
                                {"version_id": version, "reading_id": reading["id"]})
        authors = ", ".join(a if isinstance(a, str) else a.get("name", "") for a in work.get("authors", []))
        citation = ". ".join(part for part in (authors, work["title"], str(work.get("year") or "")) if part)
        citations.append({"citation": citation, "locator": locator})
    return citations


def describe_binding(records, artifacts):
    statement = find_current_statement(records)
    openness, value = find_latest(records, "problem_openness"), find_value(records)
    computations = [validate_computation(records, artifacts, item, recorded_revision=value.get("recorded_revision")) for item in collect_value_items(value)
                    if item["kind"] == "computation"]
    return {"claim": statement, "openness": openness, "value": value, "assessment": find_assessment(records),
            "readings": [{"id": row["id"], "digest": digest(row)} for row in collect_readings(records)],
            "computations": [{"execution_id": row["execution_id"], "output_id": row["output_id"],
                              **{key: digest(row[key]) for key in ("admission", "execution", "assessment")}} for row in computations],
            "formal_checks": [{"id": row["id"], "digest": digest(row),
                               "native_closure": digest(records["native_closure"][row["native_closure_digest"]])}
                              for row in find_formal_checks(records)], "citations": project_citations(records, artifacts)}


def describe_evidence(records, artifacts):
    from .direction_sources import build_reading_passages
    statement, value = find_current_statement(records), find_value(records)
    openness = find_latest(records, "problem_openness")
    assessment = find_assessment(records)
    readings = collect_readings(records)
    prepared = build_reading_passages(records, artifacts, readings)
    computations = [validate_computation(records, artifacts, item, recorded_revision=value.get("recorded_revision")) for item in collect_value_items(value)
                    if item["kind"] == "computation"]
    return {"claim": None if statement is None else dict(
                {key: statement[key] for key in ("title", "area", "statement", "resolution_criteria", "formal_statement")},
                statement_id=statement["id"], statement_digest=compute_statement_digest(statement)),
            "openness": None if openness is None else {key: openness.get(key) for key in ("id", "outcome", "statement_digest", "minimum")},
            "value": None if value is None else {key: value[key] for key in ("id", "motivation", "current_state")},
            "assessment": None if assessment is None else {key: assessment[key] for key in ("id", "grades", "items", "primary")},
            "citations": project_citations(records, artifacts),
            "readings": [{"id": row["id"], "version_id": row["version_id"], "digest": digest(row),
                          "depth": row["depth"], "reading": row} for row in readings],
            "passages": [passage for passage, _ in prepared], "computations": computations,
            "formal_checks": [dict(row, native_closure=records["native_closure"][row["native_closure_digest"]])
                              for row in find_formal_checks(records)]}
