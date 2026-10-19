"""Bind a problem's exact statement to a solver or assessed literature attempt."""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, text
from .problem_claims import find_assessment, find_current_statement, find_latest, require_problem


def find_attempt(records):
    return find_latest(records, "problem_attempt")


def validate_attempt(records, artifacts, value):
    from .problem_evidence import validate_reading
    fields(value, ("id", "statement_id", "kind", "solver", "literature", "outcomes", "proportion"),
           ("recorded_revision",))
    text(value["id"], "Problem attempt ID")
    statement = find_current_statement(records)
    if statement is None or value["statement_id"] != statement["id"]:
        raise ResearchError("problem_statement_missing", "Select the exact current problem statement",
                            {"statement_id": value["statement_id"], "attempt_id": value["id"]})
    kind = value["kind"]
    if (kind not in ("solver", "literature") or value[kind] is None
            or value["literature" if kind == "solver" else "solver"] is not None):
        raise ResearchError("invalid_input", "Supply exactly the solver or literature binding matching the attempt kind")
    outcomes = value["outcomes"]
    fields(outcomes, ("resolved", "footholds", "unresolved", "obstructions"))
    if type(outcomes["resolved"]) is not bool:
        raise ResearchError("invalid_input", "The resolved outcome must be a boolean")
    for section in ("footholds", "unresolved", "obstructions"):
        if not isinstance(outcomes[section], list):
            raise ResearchError("invalid_input", "Attempt " + section + " must be an array")
        for row in outcomes[section]:
            required = {"unresolved": ("description",), "footholds": ("claim", "evidence"),
                        "obstructions": ("claim", "scope", "evidence")}[section]
            fields(row, required)
            for key in required:
                if key != "evidence":
                    text(row[key], "Attempt " + key)
    fields(value["proportion"], ("extension_reason",))
    reason = value["proportion"]["extension_reason"]
    if reason is not None and not isinstance(reason, str):
        raise ResearchError("invalid_input", "The extension reason must be text or null")
    native, readings = None, []
    if kind == "solver":
        fields(value["solver"], ("native_attempt_digest",))
        identifier = value["solver"]["native_attempt_digest"]
        text(identifier, "Native attempt digest")
        native = records.get("native_attempt", {}).get(identifier)
        if native is None:
            raise ResearchError("problem_attempt_unbound", "Deliver the native attempt into this problem store",
                                {"native_attempt_digest": identifier, "expected_store": str(artifacts.root)})
        if native["contract"]["original_claim"]["statement"] != statement["statement"]:
            raise ResearchError("problem_attempt_statement_mismatch", "The native contract must name the exact current statement",
                                {"attempt_id": value["id"], "statement_id": statement["id"], "native_attempt_digest": identifier})
        if outcomes["resolved"] != (native["state"]["proof_status"] in ("proved", "disproved")):
            raise ResearchError("problem_attempt_outcome_mismatch", "Match the native attempt's recorded proof status",
                                {"attempt_id": value["id"], "native_attempt_digest": identifier})
        totals = native["totals"]
        if (totals["moves"] > 24 or totals["runs"] > 24) and (reason is None or not reason.strip()):
            raise ResearchError("problem_attempt_extension_unexplained", "Explain extending the normal attempt limits",
                                {"totals": totals})
    else:
        fields(value["literature"], ("readings", "description"))
        text(value["literature"]["description"], "Critical assessment of the literature attempt")
        readings = value["literature"]["readings"]
        if not isinstance(readings, list) or not readings:
            raise ResearchError("invalid_input", "A literature attempt requires full readings")
        for reading in readings:
            fields(reading, ("version_id", "reading_id"))
            validate_reading(records, artifacts, dict(reading, kind="reading"))
    for section in ("footholds", "obstructions"):
        for row in outcomes[section]:
            evidence = row["evidence"]
            if not isinstance(evidence, dict) or evidence.get("kind") not in ("acceptance", "reading"):
                raise ResearchError("invalid_input", "Use acceptance or reading evidence for attempt findings")
            if evidence["kind"] == "acceptance":
                fields(evidence, ("kind", "acceptance_id"))
                text(evidence["acceptance_id"], "Acceptance ID")
                acceptance = next((item for item in (native or {}).get("acceptances", [])
                                   if item["id"] == evidence["acceptance_id"]), None)
                allowed = ("proof", "reduction") if section == "footholds" else ("obstruction", "counterexample")
                if (acceptance is None or acceptance["status"] != "accepted" or acceptance["outcome"] not in allowed
                        or (section == "obstructions" and acceptance["obligation_id"] == native["contract"]["root_obligation"])):
                    raise ResearchError("problem_attempt_evidence_missing", "Name an accepted result of the appropriate outcome and scope",
                                        {"acceptance_id": evidence["acceptance_id"]})
            else:
                fields(evidence, ("kind", "version_id", "reading_id"))
                if {key: evidence[key] for key in ("version_id", "reading_id")} not in readings:
                    raise ResearchError("problem_attempt_evidence_missing", "Name a full reading of this literature attempt",
                                        {key: evidence[key] for key in ("version_id", "reading_id")})


def record_attempt(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "statement_id", "kind", "solver", "literature", "outcomes", "proportion"))
        validate_attempt(records, artifacts, value)
        record = dict(value, recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_attempt", value["id"], record)], record

    return prepared_mutation(store, "problem.attempt", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def assess_attempt(records, artifacts):
    attempt, statement = find_attempt(records), find_current_statement(records)
    statement_id = None if statement is None else statement["id"]
    current = attempt is not None and statement is not None and attempt["statement_id"] == statement_id
    pending = []
    if attempt is None:
        pending.append(obligation("problem_attempt_missing", "Record a proportionate solver or assessed literature attempt.",
                                  statement_id=statement_id))
    elif not current:
        pending.append(obligation("problem_attempt_stale", "Reassess the attempt for the current statement and criteria.",
                                  attempt_id=attempt["id"], statement_id=statement_id))
    else:
        try:
            validate_attempt(records, artifacts, attempt)
        except ResearchError as error:
            pending.append(obligation(error.code, error.message, attempt_id=attempt["id"],
                                      **{key: value for key, value in (error.details or {}).items() if key != "attempt_id"}))
        if attempt["outcomes"]["resolved"]:
            pending.append(obligation("problem_attempt_resolved", "Deliver the resolved candidate as a result.", attempt_id=attempt["id"]))
    assessment = find_assessment(records)
    if (assessment is not None and assessment["statement_id"] == statement_id
            and assessment["grades"]["attempt"] == "pass" and not current):
        pending.append(obligation("problem_rubric_attempt_unbound", "Bind the passing attempt grade to a current attempt record.",
                                  assessment_id=assessment["id"], statement_id=statement_id))
    return pending
