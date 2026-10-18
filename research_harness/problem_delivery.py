"""Post gate, decisions and deterministic record-authoritative delivery."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, strings, text
from .problem_claims import current_statement, find_latest, find_value, problem_evidence_digest, require_problem, statement_digest
from .problem_evidence import assess_formal_checks, project_citations, validate_value
from .problem_openness import assess_openness


def assess_minimum(records, artifacts):
    pending = []
    statement, value = current_statement(records), find_value(records)
    if statement is None:
        pending.append(obligation("problem_statement_missing", "Record the exact statement and resolution criteria."))
    pending.extend(assess_openness(records, artifacts))
    if value is None or statement is None or value["statement_id"] != statement["id"]:
        pending.append(obligation("problem_value_missing", "Bind motivation and current-state paragraphs to this statement's evidence.",
                                  statement_id=None if statement is None else statement["id"]))
    else:
        try:
            validate_value(records, artifacts, value)
        except ResearchError as error:
            pending.append(obligation(error.code, error.message, **(error.details or {})))
    pending.extend(assess_formal_checks(records))
    try:
        project_citations(records, artifacts)
    except ResearchError as error:
        pending.append(obligation(error.code, error.message, **(error.details or {})))
    return pending


def assess_source_requests(records, artifacts):
    from .review_protocol import assignment_state
    parents = {parent for row in records.get("review_assignment", {}).values()
               for parent in (row.get("history_assignment_id"), row.get("context", {}).get("prior_assignment_id"),
                              (row.get("context", {}).get("context_repair") or {}).get("assignment_id"))
               if parent is not None}
    pending = []
    for identifier in records.get("review_assignment", {}):
        if identifier in parents:
            continue
        state = assignment_state(records, artifacts, identifier)
        if state.get("pending_source_requests"):
            pending.append(obligation("review_sources_pending", "Deliver the sources requested by the observed reviewer.",
                                      assignment_id=identifier, requests=state["pending_source_requests"]))
    return pending


def find_post(records, statement):
    if statement is None:
        return None
    return next((row for row in records.get("problem_post", {}).values()
                 if row["statement_digest"] == statement_digest(statement)), None)


def post_state(records, artifacts):
    require_problem(records)
    from .research_decisions import assess_problem_reviews
    statement = current_statement(records)
    minimum = assess_minimum(records, artifacts)
    # A packet is judged only when its own evidence is available. Missing claim
    # evidence is already reported with the more useful minimum obligations.
    reviews = assess_problem_reviews(records, artifacts, check_evidence=not minimum)
    pending = list(minimum)
    pending.extend(reviews["obligations"])
    pending.extend(assess_source_requests(records, artifacts))
    decision = find_latest(records, "problem_decision")
    evidence_digest = None
    # Currency is a digest of explicitly named immutable records. Do not run
    # historical execution assessment under today's preparation snapshot.
    try:
        evidence_digest = problem_evidence_digest(records, artifacts)
    except ResearchError as error:
        if not minimum:
            pending.append(obligation(error.code, error.message, **(error.details or {})))
    if decision is None or decision["action"] != "post":
        pending.append(obligation("problem_decision_missing", "Record a post decision after both observed result reviews."))
    elif decision["problem_evidence_digest"] != evidence_digest:
        pending.append(obligation("problem_decision_stale", "Reassess the changed evidence and record a current post decision.", decision_id=decision["id"]))
    elif not minimum:
        selected = assess_problem_reviews(records, artifacts, decision["review_ids"], decision["bar_review_ids"])
        pending.extend(selected["obligations"])
    posted = find_post(records, statement)
    if posted is not None:
        pending.append(obligation("problem_already_posted", "This exact statement already has a recorded post.", post_id=posted["id"], problem_id=posted["problem_id"]))
    unique = list({digest(row): row for row in pending}.values())
    milestone = "exploration"
    if statement is not None:
        milestone = "formulation"
        if not assess_openness(records, artifacts):
            milestone = "openness"
    if not minimum and reviews is not None and reviews["ready"] and decision is not None and decision["action"] == "post" and decision["problem_evidence_digest"] == evidence_digest:
        milestone = "value"
    if posted is not None:
        milestone = "post"
    return {"ready": not unique, "obligations": unique, "counts": {"obligations": len(unique)},
            "digest": digest({"problem_evidence_digest": evidence_digest, "decision": decision, "obligations": unique}),
            "orchestration": "problem_creation", "milestone": milestone,
            "problem_evidence_digest": evidence_digest, "statement_id": None if statement is None else statement["id"],
            "decision_id": None if decision is None else decision["id"]}


def prepare_decision(records, artifacts, value, revision):
    require_problem(records)
    fields(value, ("id", "action", "review_ids", "bar_review_ids", "reason"))
    text(value["id"], "Problem decision ID")
    text(value["reason"], "Problem decision reason")
    if value["action"] not in {"post", "revise", "withdraw"}:
        raise ResearchError("invalid_input", "Problem decision action must be post, revise or withdraw")
    strings(value["review_ids"], "Problem result review IDs")
    strings(value["bar_review_ids"], "Problem bar review IDs")
    evidence_digest = None
    if value["action"] == "post":
        pending = assess_minimum(records, artifacts)
        if pending:
            raise ResearchError("problem_minimum_unmet", "Complete the current statement, openness and value evidence first", {"obligations": pending})
        from .research_decisions import assess_problem_reviews
        review = assess_problem_reviews(records, artifacts, value["review_ids"], value["bar_review_ids"])
        pending = review["obligations"] + assess_source_requests(records, artifacts)
        if pending:
            raise ResearchError(pending[0]["code"], pending[0]["explanation"], {"obligations": pending})
        evidence_digest = problem_evidence_digest(records, artifacts)
    record = dict(value, problem_evidence_digest=evidence_digest, recorded_revision=revision)
    return [immutable_record(records, "problem_decision", value["id"], record)], record


def build_projection(records, artifacts):
    statement, value = current_statement(records), find_value(records)
    decision = find_latest(records, "problem_decision")
    files = {"title.txt": statement["title"], "statement.md": statement["statement"],
             "criteria.md": statement["resolution_criteria"],
             "motivation.md": "\n\n".join(row["text"] for row in value["motivation"]),
             "state.md": "\n\n".join(row["text"] for row in value["current_state"]),
             "citations.json": json.dumps(project_citations(records, artifacts), ensure_ascii=False, sort_keys=True, indent=2)}
    formal = statement["formal_statement"]
    if formal is not None:
        files["formal-statement.txt"] = formal["text"]
    files = {name: (content + "\n").encode("utf-8") for name, content in files.items()}
    manifest = {"statement_id": statement["id"], "statement_digest": statement_digest(statement),
                "problem_evidence_digest": problem_evidence_digest(records, artifacts), "decision_id": decision["id"],
                "area": statement["area"], "formal_statement_language": None if formal is None else formal["language"],
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}}
    files["problem-export.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return files, manifest


def export_problem(store, destination):
    from .gates import require_ready
    records = store.snapshot()["records"]
    artifacts = ArtifactStore(store.root)
    require_ready(post_state(records, artifacts), "post")
    directory = Path(destination).absolute()
    if directory.exists() or directory.is_symlink():
        raise ResearchError("review_destination_exists", "Export into a new directory")
    files, manifest = build_projection(records, artifacts)
    from .workspace import write_projection
    directory.mkdir(mode=0o700, parents=True)
    for name, content in files.items():
        write_projection(directory, name, content)
    return dict(manifest, destination=str(directory))


def prepare_posted(records, artifacts, value):
    require_problem(records)
    fields(value, ("problem_id", "export_directory"))
    text(value["problem_id"], "Posted problem ID")
    text(value["export_directory"], "Problem export directory")
    statement = current_statement(records)
    earlier = find_post(records, statement)
    if earlier is not None:
        raise ResearchError("problem_already_posted", "This exact statement already has a recorded post", {"post_id": earlier["id"]})
    from .gates import require_ready
    require_ready(post_state(records, artifacts), "post")
    files, manifest = build_projection(records, artifacts)
    directory = Path(value["export_directory"])
    if not directory.is_absolute():
        directory = artifacts.root / directory
    try:
        valid = (directory.is_dir() and not directory.is_symlink() and {path.name for path in directory.iterdir()} == set(files)
                 and all(not (directory / name).is_symlink() and (directory / name).is_file()
                         and (directory / name).read_bytes() == content for name, content in files.items()))
    except OSError as error:
        raise ResearchError("problem_export_mismatch", "Read the complete approved problem projection", {"export_directory": str(directory)}) from error
    if not valid:
        raise ResearchError("problem_export_mismatch", "The exported files must equal the current reviewed records byte for byte", {"export_directory": str(directory)})
    export_digest = digest({name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())})
    identifier = digest({"problem_id": value["problem_id"], "export_digest": export_digest})
    record = {"id": identifier, "problem_id": value["problem_id"], "statement_id": statement["id"],
              "statement_digest": manifest["statement_digest"], "problem_evidence_digest": manifest["problem_evidence_digest"],
              "export_digest": export_digest, "posted_at": datetime.now(timezone.utc).isoformat()}
    return [immutable_record(records, "problem_post", identifier, record)], record
