"""Immutable problem claims and the evidence their open-problem post uses.

The problem orchestration makes an empirical support claim, never accepts a
proof. Requested formal support is bound to a delivered native completion.
"""

from datetime import datetime, timezone
from contextlib import ExitStack

from .artifacts import ArtifactStore, validate_reference
from .errors import ResearchError
from .evidence import digest
from .exactory_api import UUID_RE
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, strings, text, timestamp
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
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        fields(value, ("slug", "area", "field", "authors"), ("start",))
        if find_orchestration(records) is not None:
            raise ResearchError("orchestration_conflict", "Keep each orchestration in its own research record")
        for key in ("slug", "field"):
            text(value[key], "Problem " + key)
        validate_area(value["area"])
        strings(value["authors"], "Problem authors", nonempty=True)
        if "start" in value:
            fields(value["start"], ("kind", "description"))
            for key in ("kind", "description"):
                text(value["start"][key], "Problem start " + key)
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
        fields(value, ("id", "title", "area", "statement", "resolution_criteria", "neighbours"),
               ("formal_statement", "candidate_id"))
        value = dict(value, formal_statement=value.get("formal_statement"), candidate_id=value.get("candidate_id"))
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
        if value["candidate_id"] is not None:
            from .problem_candidates import find_current_candidates
            text(value["candidate_id"], "Formulated candidate ID")
            if value["candidate_id"] not in find_current_candidates(records):
                raise ResearchError("problem_candidate_missing", "Formulate a current candidate",
                                    {"candidate_id": value["candidate_id"]})
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
    from .problem_evidence import resolve_assessment_record
    artifacts = store.build_artifact_store()

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
            for reference in strategy.items(entry["evidence"], "Evidence", nonempty=True):
                if isinstance(reference, dict) and reference.get("kind") == "record":
                    resolve_assessment_record(records, item, reference)
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
    from .problem_delivery import find_post
    assessment, statement = find_assessment(records), find_current_statement(records)
    statement_id = None if statement is None else statement["id"]
    if assessment is None:
        return [obligation("problem_assessment_missing", "Record the rubric self-assessment with an evidence reference for every item.", statement_id=statement_id)]
    pending = []
    if assessment["statement_id"] != statement_id:
        pending.append(obligation("problem_assessment_stale", "Record a new self-assessment for the current statement.",
                                  assessment_id=assessment["id"], statement_id=statement_id))
    problem_rubric.validate_grades(assessment["grades"], allow_previous=True)
    if problem_rubric.was_graded_before_a7(assessment["grades"]) and find_post(records, statement) is None:
        return pending
    shortfalls = problem_rubric.assess_grades(assessment["grades"])
    if shortfalls:
        pending.append(obligation("problem_rubric_below_threshold", "Repair each named shortfall with evidence, or revise or withdraw the candidate.",
                                  source="self", assessment_id=assessment["id"], shortfalls=shortfalls))
    return pending


def record_value(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

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
    artifacts = store.build_artifact_store()

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
    artifacts = store.build_artifact_store()
    return prepared_mutation(store, "problem.openness", payload,
        lambda records, value: prepare_openness(records, artifacts, value, expected_revision + 1),
        expected_revision=expected_revision, request_id=request_id)


def record_decision(store, payload, *, expected_revision, request_id):
    from .problem_delivery import prepare_decision
    artifacts = store.build_artifact_store()
    with ExitStack() as guards:
        return prepared_mutation(store, "problem.decision", payload,
            lambda records, value: prepare_decision(records, artifacts, value, expected_revision + 1, source_guards=guards),
            expected_revision=expected_revision, request_id=request_id)


def record_posted(store, payload, *, expected_revision, request_id):
    from .problem_delivery import prepare_posted
    artifacts = store.build_artifact_store()
    with ExitStack() as guards:
        return prepared_mutation(store, "problem.posted", payload,
            lambda records, value: prepare_posted(records, artifacts, value, expected_revision + 1, source_guards=guards),
            expected_revision=expected_revision, request_id=request_id)


def record_post_external(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        from .problem_delivery import find_post
        require_problem(records)
        fields(value, ("id", "problem_id", "statement_digest", "reason", "evidence"),
               optional=("posted_at",))
        if "posted_at" in value:
            timestamp(value["posted_at"])
        for key in ("id", "problem_id", "statement_digest", "reason"):
            text(value[key], "External post " + key)
        if not UUID_RE.fullmatch(value["problem_id"]):
            raise ResearchError("invalid_input", "Posted problem ID must be a UUID")
        if isinstance(value["evidence"], dict):
            validate_reference(value["evidence"])
            artifacts.verify(value["evidence"])
        else:
            text(value["evidence"], "External post evidence")
        statement = find_current_statement(records)
        if statement is None:
            raise ResearchError("problem_statement_missing",
                                "Select the exact current problem statement")
        statement_digest = compute_statement_digest(statement)
        if value["statement_digest"] != statement_digest:
            raise ResearchError("problem_statement_mismatch",
                                "The posted statement digest must match the current statement",
                                {"statement_digest": statement_digest})
        earlier = find_post(records, statement)
        if earlier is not None:
            raise ResearchError("problem_already_posted",
                                "This exact statement already has a recorded post",
                                {"post_id": earlier["id"]})
        record = dict(value, statement_id=statement["id"], gate="bypassed",
                      posted_at=value.get("posted_at"),
                      recorded_at=datetime.now(timezone.utc).isoformat(),
                      recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_post", value["id"], record)], record

    return prepared_mutation(store, "problem.post_external", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def record_post_time(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        require_problem(records)
        fields(value, ("post_id", "posted_at", "evidence"))
        text(value["post_id"], "Time statement post ID")
        timestamp(value["posted_at"])
        if isinstance(value["evidence"], dict):
            validate_reference(value["evidence"])
            artifacts.verify(value["evidence"])
        else:
            text(value["evidence"], "Time statement evidence")
        post = records.get("problem_post", {}).get(value["post_id"])
        if post is None:
            raise ResearchError("problem_post_missing", "No recorded problem post has this post ID",
                                {"post_id": value["post_id"]})
        if post.get("gate") != "bypassed" or "recorded_at" in post:
            raise ResearchError("problem_post_time_inapplicable",
                                "Only posts with gate bypassed and no recorded_at accept a time statement",
                                {"post_id": value["post_id"], "gate": post.get("gate")})
        if value["post_id"] in records.get("problem_post_time", {}):
            raise ResearchError("problem_post_time_already_recorded",
                                "This post already has a time statement",
                                {"post_id": value["post_id"]})
        record = dict(value, recorded_at=datetime.now(timezone.utc).isoformat(),
                      recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_post_time", value["post_id"], record)], record

    return prepared_mutation(store, "problem.post_time", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
