"""Immutable problem claims and the evidence their open-problem post uses.

The problem orchestration makes an empirical support claim, never accepts a
proof. Requested formal support is bound to a delivered native completion.
"""

from datetime import datetime, timezone

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .principles import prepare_initialization
from .strategy import find_orchestration
from . import problem_rubric, strategy


def find_current_statement(records):
    selection = records.get("problem_selection", {}).get("current")
    return None if selection is None else records.get("problem_statement", {}).get(selection["statement_id"])


def compute_statement_digest(statement):
    return digest({key: statement[key] for key in ("statement", "resolution_criteria")})


def find_latest(records, kind):
    return max(records.get(kind, {}).values(), key=lambda item: item.get("recorded_revision", 0), default=None)


def require_problem(records):
    if find_orchestration(records) != "problem_creation":
        raise ResearchError("orchestration_conflict", "Initialize a problem record for this operation")
    return records["workspace"]["problem"]


def validate_area(area):
    text(area, "Mathematics subject class")
    if not area.startswith("math."):
        raise ResearchError("invalid_input", "The subject class must have the math. prefix")


def initialize_problem(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("slug", "area", "field", "authors"))
        if find_orchestration(records) is not None:
            raise ResearchError("orchestration_conflict", "Keep each orchestration in its own research record")
        for key in ("slug", "field"):
            text(value[key], "Problem " + key)
        validate_area(value["area"])
        strings(value["authors"], "Problem authors", nonempty=True)
        config = records.get("configuration", {}).get("research")
        changes = []
        if config is None:
            changes, config = prepare_initialization(records, artifacts, {"profile": "research", "target": None})
        if config["profile"] != "research":
            raise ResearchError("profile_mismatch", "A problem requires the research profile")
        if config["target"] is not None:
            # A research target is the objective of ai-science; a problem record keeps a null target.
            raise ResearchError("orchestration_conflict", "Keep each orchestration in its own research record")
        now = datetime.now(timezone.utc).isoformat()
        marker = dict(value, version=1, orchestration="problem_creation", created=now, updated=now,
                      research={"store": ".exactory/research.sqlite3", "profile": "research"})
        return changes + [("workspace", "problem", marker)], marker

    return prepared_mutation(store, "problem.initialize", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def record_statement(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        marker = require_problem(records)
        fields(value, ("id", "title", "area", "statement", "resolution_criteria", "neighbours"), ("formal_statement",))
        value = dict(value, formal_statement=value.get("formal_statement"))
        for key in ("id", "title", "statement", "resolution_criteria"):
            text(value[key], "Problem " + key)
        validate_area(value["area"])
        if value["area"] != marker["area"]:
            raise ResearchError("problem_area_mismatch", "Use the record's subject class; another area requires a new problem record",
                                {"statement_id": value["id"], "statement_area": value["area"], "record_area": marker["area"]})
        strings(value["neighbours"], "Neighbouring formulations")
        formal = value["formal_statement"]
        if formal is not None:
            fields(formal, ("language", "text"))
            text(formal["language"], "Formal statement language")
            text(formal["text"], "Formal statement")
        saved = records.get("problem_statement", {}).get(value["id"])
        revision = saved["recorded_revision"] if saved else expected_revision + 1
        record = dict(value, recorded_revision=revision)
        return [immutable_record(records, "problem_statement", value["id"], record),
                ("problem_selection", "current", {"statement_id": value["id"]})], record

    return prepared_mutation(store, "problem.statement", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def find_value(records):
    return find_latest(records, "problem_value")


def find_assessment(records):
    return find_latest(records, "problem_assessment")


def record_assessment(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "statement_id", "grades", "items", "primary"))
        text(value["id"], "Problem assessment ID")
        statement = find_current_statement(records)
        if statement is None or value["statement_id"] != statement["id"]:
            raise ResearchError("problem_statement_missing", "Select the exact current problem statement")
        problem_rubric.validate_grades(value["grades"])
        for item in problem_rubric.ITEMS:
            entry = value["items"].get(item) if isinstance(value["items"], dict) else None
            if not isinstance(entry, dict) or not entry.get("evidence"):
                raise ResearchError("problem_assessment_evidence_missing", "Give evidence for every rubric item", {"item": item})
            fields(entry, ("evidence", "note"))
            strategy.evidence(records, artifacts, entry["evidence"])
            text(entry["note"], "Rubric item note")
        fields(value["items"], problem_rubric.ITEMS)
        fields(value["primary"], ("ground", "criterion"))
        primary = value["primary"]
        if (primary["ground"] not in problem_rubric.GROUNDS or primary["criterion"] not in problem_rubric.CRITERIA
                or value["grades"]["significance"][primary["ground"]] == 0):
            raise ResearchError("invalid_input", "Name a nonzero significance ground and a criterion V1 to V8")
        record = dict(value, recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_assessment", value["id"], record)], record

    return prepared_mutation(store, "problem.assessment", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def assess_assessment(records, artifacts):
    assessment, statement = find_assessment(records), find_current_statement(records)
    statement_id = None if statement is None else statement["id"]
    if assessment is None:
        return [obligation("problem_assessment_missing", "Record an evidence-bound rubric self-assessment.", statement_id=statement_id)]
    pending = []
    if assessment["statement_id"] != statement_id:
        pending.append(obligation("problem_assessment_stale", "Assess the current problem statement.",
                                  assessment_id=assessment["id"], statement_id=statement_id))
    shortfalls = problem_rubric.assess_grades(assessment["grades"])
    if shortfalls:
        pending.append(obligation("problem_rubric_below_threshold", "Repair the rubric self-assessment shortfalls.",
                                  source="self", assessment_id=assessment["id"], shortfalls=shortfalls))
    return pending


def record_value(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "statement_id", "motivation", "current_state"))
        text(value["id"], "Problem value ID")
        statement = find_current_statement(records)
        if statement is None or value["statement_id"] != statement["id"]:
            raise ResearchError("problem_statement_missing", "Select the exact current problem statement")
        from .problem_evidence import validate_value
        validate_value(records, artifacts, value)
        record = dict(value, recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_value", value["id"], record)], record

    return prepared_mutation(store, "problem.value", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def record_formal_check(store, payload, *, expected_revision, request_id):
    from .problem_evidence import bind_formal_check
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        require_problem(records)
        fields(value, ("evidence", "native_closure_digest"))
        record = bind_formal_check(records, artifacts, value)
        identifier = digest(record)
        saved = records.get("problem_formal_check", {}).get(identifier)
        if saved is not None:
            return [], saved
        record = dict(record, id=identifier, recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_formal_check", identifier, record)], record

    return prepared_mutation(store, "problem.formal_check", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def describe_problem_evidence(records, artifacts):
    from .problem_evidence import describe_evidence
    return describe_evidence(records, artifacts)


def compute_problem_evidence_digest(records, artifacts):
    from .problem_evidence import describe_binding
    return digest(describe_binding(records, artifacts))


def record_openness(store, payload, *, expected_revision, request_id):
    from .problem_openness import prepare_openness
    artifacts = ArtifactStore(store.root)
    return prepared_mutation(store, "problem.openness", payload,
        lambda records, value: prepare_openness(records, artifacts, value, expected_revision + 1),
        expected_revision=expected_revision, request_id=request_id)


def record_decision(store, payload, *, expected_revision, request_id):
    from .problem_delivery import prepare_decision
    artifacts = ArtifactStore(store.root)
    return prepared_mutation(store, "problem.decision", payload,
        lambda records, value: prepare_decision(records, artifacts, value, expected_revision + 1),
        expected_revision=expected_revision, request_id=request_id)


def record_posted(store, payload, *, expected_revision, request_id):
    from .problem_delivery import prepare_posted
    artifacts = ArtifactStore(store.root)
    return prepared_mutation(store, "problem.posted", payload,
        lambda records, value: prepare_posted(records, artifacts, value),
        expected_revision=expected_revision, request_id=request_id)
