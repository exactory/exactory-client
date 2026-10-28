"""Bind a problem's exact statement to a solver or assessed literature attempt."""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, text
from .problem_claims import find_assessment, find_current_statement, find_latest, require_problem

# Normal solver attempt limits from DESIGN.md section 5.
NORMAL_ATTEMPT_LIMITS = {"moves": 24, "runs": 24, "workers": 1, "timeout_seconds": 300}


def is_native_resolved(native):
    if native["state"]["proof_status"] == "invalidated":
        return False
    root = native["contract"]["root_obligation"]
    return (native["state"]["proof_status"] in ("proved", "disproved")
            or any(row["id"] == root and row["status"] == "accepted" for row in native["obligations"])
            or any(row["status"] == "accepted" and row["obligation_id"] == root
                   and row["outcome"] in ("proof", "counterexample") for row in native["acceptances"]))


def find_attempt(records):
    return find_latest(records, "problem_attempt")


def find_current_attempt(records):
    attempt, statement = find_attempt(records), find_current_statement(records)
    return attempt if (attempt is not None and statement is not None
                       and attempt["statement_id"] == statement["id"]) else None


def find_latest_native_attempt(records, bound):
    key = {name: bound[name] for name in ("attack_root", "objective_id")}
    pointer = records.get("native_attempt_current", {}).get(digest(key))
    if pointer is None:
        return None, None
    identifier = pointer["native_attempt_digest"]
    native = records.get("native_attempt", {}).get(identifier)
    if native is None or any(native[name] != value for name, value in key.items()):
        return None, None
    return identifier, native


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
        fields(value["solver"], ("native_attempt_digest", "applicability"))
        text(value["solver"]["applicability"], "Applicability to the statement and resolution criteria")
        identifier = value["solver"]["native_attempt_digest"]
        text(identifier, "Native attempt digest")
        native = records.get("native_attempt", {}).get(identifier)
        if native is None:
            raise ResearchError("problem_attempt_unbound", "Deliver the native attempt into this problem store",
                                {"native_attempt_digest": identifier, "expected_store": str(artifacts.root)})
        latest_digest, _ = find_latest_native_attempt(records, native)
        if latest_digest is None:
            raise ResearchError("problem_attempt_unbound", "Deliver the native attempt into this problem store",
                                {"native_attempt_digest": identifier, "expected_store": str(artifacts.root)})
        if identifier != latest_digest:
            raise ResearchError("problem_attempt_superseded", "Bind the latest delivered snapshot of this objective",
                                {"native_attempt_digest": identifier, "latest_native_attempt_digest": latest_digest})
        scope = native["contract"]["original_claim"]["scope"]
        if scope["kind"] != "named":
            raise ResearchError("problem_attempt_scope_restricted", "Attempt the exact statement with a named scope; a restricted scope is a partial attempt",
                                {"native_attempt_digest": identifier, "scope": scope})
        if native["contract"]["original_claim"]["statement"] != statement["statement"]:
            raise ResearchError("problem_attempt_statement_mismatch", "The native contract must name the exact current statement",
                                {"attempt_id": value["id"], "statement_id": statement["id"], "native_attempt_digest": identifier})
        if outcomes["resolved"] != is_native_resolved(native):
            raise ResearchError("problem_attempt_outcome_mismatch", "Match the native attempt's recorded root resolution",
                                {"attempt_id": value["id"], "native_attempt_digest": identifier})
        totals = native["totals"]
        exceeded = [name for name, limit in NORMAL_ATTEMPT_LIMITS.items() if totals[name] > limit]
        if exceeded and (reason is None or not reason.strip()):
            raise ResearchError("problem_attempt_extension_unexplained", "Record the reason for exceeding the normal limits named in exceeded",
                                {"totals": totals, "exceeded": exceeded})
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
    artifacts = store.build_artifact_store()

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
        pending.append(obligation("problem_attempt_missing", "Attempt the exact statement with math-solver, or assess an existing attempt from full readings, and record it.",
                                  statement_id=statement_id))
    elif not current:
        pending.append(obligation("problem_attempt_stale", "Reassess the attempt for the current statement and criteria.",
                                  attempt_id=attempt["id"], statement_id=statement_id))
    else:
        try:
            validate_attempt(records, artifacts, attempt)
        except ResearchError as error:
            if error.code == "problem_attempt_superseded":
                pending.append(obligation("problem_attempt_stale",
                    "Record a new attempt bound to the latest delivered snapshot of this objective.",
                    attempt_id=attempt["id"], statement_id=statement_id, **error.details))
            else:
                pending.append(obligation(error.code, error.message, attempt_id=attempt["id"],
                                          **{key: value for key, value in (error.details or {}).items() if key != "attempt_id"}))
    resolved = []
    for recorded in records.get("problem_attempt", {}).values():
        if statement is None or recorded["statement_id"] != statement_id:
            continue
        if recorded["kind"] == "literature":
            established = recorded["outcomes"]["resolved"]
        else:
            native = records.get("native_attempt", {}).get(recorded["solver"]["native_attempt_digest"])
            latest = None if native is None else find_latest_native_attempt(records, native)[1]
            established = latest is not None and is_native_resolved(latest)
        if established:
            resolved.append(recorded)
    if resolved:
        concerned = max(resolved, key=lambda row: (row["recorded_revision"], row["id"]))
        explanation = ("The literature resolved this statement: redirect to the established result and its source; the candidate leaves the open-problem path."
                       if concerned["kind"] == "literature" else
                       "The solver resolved this statement: deliver the result through math-solver's own delivery; the candidate leaves the open-problem path.")
        pending.append(obligation("problem_attempt_resolved", explanation, attempt_id=concerned["id"]))
    assessment = find_assessment(records)
    if (assessment is not None and assessment["statement_id"] == statement_id
            and assessment["grades"]["attempt"] == "pass" and not current):
        pending.append(obligation("problem_rubric_attempt_unbound", "Bind the passing attempt grade to a current attempt record.",
                                  assessment_id=assessment["id"], statement_id=statement_id))
    return pending
