"""Candidate problems, the compared slate and its selection decision.

A candidate is a sourced question worth comparing, not a claim. The slate binds
the selection to two blind problem bars, the comparison and the provisional
potential grades. Selection authorizes further research expenditure; it
establishes neither openness nor significance.
"""

import json

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, normalized_text, prepared_mutation, strings, text
from .problem_claims import find_current_statement, find_latest, require_problem
from .research_decisions import _independence, _pair
from .review_context_repair import slot_id
from .review_protocol import build_problem_subject, is_previous_prompt, validate_problem_bars
from . import problem_rubric, strategy

SOURCE_KINDS = ("own_record", "literature", "computation", "section", "user")
DISPOSITIONS = ("open", "selected", "deferred", "rejected")


def find_current_candidates(records):
    """Every candidate that no later candidate names as its previous version."""
    candidates = records.get("problem_candidate", {})
    superseded = {row["previous"] for row in candidates.values() if row["previous"] is not None}
    return {identifier: row for identifier, row in candidates.items() if identifier not in superseded}


def record_candidate(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "previous", "statement", "source", "observation", "neighbours", "potential", "notes",
                       "disposition"))
        for key in ("id", "statement", "observation"):
            text(value[key], "Candidate " + key)
        if value["previous"] is not None:
            text(value["previous"], "Previous candidate ID")
            if value["previous"] not in records.get("problem_candidate", {}):
                raise ResearchError("problem_candidate_missing", "Name an existing candidate as the previous version",
                                    {"candidate_id": value["previous"]})
        source = value["source"]
        fields(source, ("kind", "reference", "evidence"))
        strategy.choice(source["kind"], SOURCE_KINDS, "Candidate source kind", "invalid_input")
        text(source["reference"], "Candidate source reference")
        strategy.evidence(records, artifacts, source["evidence"], required=False)
        strings(value["neighbours"], "Candidate neighbours")
        problem_rubric.validate_grades(value["potential"], potential=True)
        if not isinstance(value["notes"], str):
            raise ResearchError("invalid_input", "Candidate notes are a string, possibly empty")
        disposition = value["disposition"]
        fields(disposition, ("status", "reason", "evidence_needed"))
        strategy.choice(disposition["status"], DISPOSITIONS, "Candidate disposition", "invalid_input")
        text(disposition["reason"], "Candidate disposition reason")
        if disposition["evidence_needed"] is not None:
            text(disposition["evidence_needed"], "Evidence that would raise the candidate")
        record = dict(value, recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_candidate", value["id"], record)], record

    return prepared_mutation(store, "problem.candidate", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def compute_candidate_set_digest(records):
    """Bind a slate review to the exact current candidates: their ids and record digests."""
    return digest([{"id": identifier, "digest": digest(row)}
                   for identifier, row in sorted(find_current_candidates(records).items())])


def find_slate(records):
    return find_latest(records, "problem_slate")


def find_current_slate(records):
    """The latest slate while it disposes of exactly the current candidates; a stale slate counts as none."""
    slate = find_slate(records)
    if slate is None or slate["candidate_ids"] != sorted(find_current_candidates(records)):
        return None
    return slate


def collect_applicable_slate_objections(records, artifacts):
    """Each material objection of an observed slate review that still concerns the current statement, with its review.

    An objection that names candidates applies while every named candidate is current with the record digest that the
    review's packet carried, and concerns the statement that formulates one of them. An objection that names no
    candidate concerns the slate as a whole and applies while the reviewed candidate set is current. A change of the
    bars erases no objection."""
    statement = find_current_statement(records)
    candidate_id = None if statement is None else statement.get("candidate_id")
    current = {identifier: digest(row) for identifier, row in find_current_candidates(records).items()}
    candidate_set_digest = compute_candidate_set_digest(records)
    applicable = []
    for review in sorted(records.get("value_review", {}).values(), key=lambda row: (row["recorded_revision"], row["id"])):
        if review["payload"]["stage"] != "slate" or review.get("subject", {}).get("kind") != "problem":
            continue
        packet = json.loads(artifacts.read(records["review_assignment"][review["assignment_id"]]["packet"]))
        reviewed = {candidate["id"]: candidate["digest"] for candidate in packet["candidates"]}
        for objection in review["payload"]["objections"]:
            named = objection["candidate_ids"]
            if named:
                applies = candidate_id in named and all(current.get(identifier) == reviewed[identifier] for identifier in named)
            else:
                applies = packet["candidate_set_digest"] == candidate_set_digest
            if applies:
                applicable.append((review, objection))
    return applicable


def describe_candidates(records):
    """A slate reviewer's view of every current candidate, without the author's notes and disposition."""
    return [{"id": identifier, "digest": digest(row), "statement": row["statement"],
             "source": {"kind": row["source"]["kind"], "reference": row["source"]["reference"]},
             "observation": row["observation"], "neighbours": row["neighbours"], "potential": row["potential"]}
            for identifier, row in sorted(find_current_candidates(records).items())]


def count_candidates(records):
    """The number of current candidates and of the candidates in each list of the latest slate."""
    slate = find_slate(records)
    counts = {"current": len(find_current_candidates(records))}
    for key in ("selected", "deferred", "rejected"):
        counts[key] = 0 if slate is None else len(slate[key])
    return counts


def _verify_bars(records, artifacts, bar_review_ids):
    try:
        bars = _pair(records, bar_review_ids, build_problem_subject(records, "slate"), "bar")
    except ResearchError as error:
        raise ResearchError("problem_bar_pending", "Cite two observed independent problem bars",
                            {"bar_review_ids": bar_review_ids}) from error
    for bar in bars:
        if is_previous_prompt(records["review_assignment"][bar["assignment_id"]]):
            raise ResearchError("problem_bar_calibration_required",
                                "Obtain a version 2 bar from this reviewer: assign the bar again to refresh it",
                                {"review_id": bar["id"]})
    validate_problem_bars(records, artifacts, bars)
    pending = _independence(records, artifacts, bars)
    if pending:
        raise ResearchError(pending[0]["code"], pending[0]["explanation"], {"obligations": pending})


def _is_current_review(records, artifacts, review, bar_review_ids, candidate_ids):
    """An observed, still independent slate review of exactly these bars and candidates."""
    if (review is None or review["payload"]["stage"] != "slate"
            or review.get("subject") != build_problem_subject(records, "slate")):
        return False
    findings = review["payload"]["value"]
    packet = json.loads(artifacts.read(records["review_assignment"][review["assignment_id"]]["packet"]))
    return (set(findings["bar_ids"]) == set(bar_review_ids) and set(findings["rubric_potential"]) == set(candidate_ids)
            and packet.get("candidate_set_digest") == compute_candidate_set_digest(records)
            and not _independence(records, artifacts, [review]))


def _verify_reviews(records, artifacts, review_ids, bar_review_ids, candidate_ids):
    """Zero to two slate reviews from distinct reviewers and review slots."""
    strings(review_ids, "Slate review IDs")
    if len(review_ids) > 2:
        raise ResearchError("invalid_input", "Cite at most two observed slate reviews")
    reviewers, slots = set(), set()
    for identifier in review_ids:
        review = records.get("value_review", {}).get(identifier)
        if not _is_current_review(records, artifacts, review, bar_review_ids, candidate_ids):
            raise ResearchError("problem_slate_review_unverified",
                                "Cite observed independent slate reviews of these bars and current candidates",
                                {"review_id": identifier})
        reviewer = normalized_text(review["reviewer_id"])
        slot = slot_id(records, records["review_assignment"][review["assignment_id"]])
        if reviewer in reviewers or slot in slots:
            raise ResearchError("problem_slate_review_unverified", "Cite slate reviews from distinct reviewers and slots",
                                {"review_id": identifier})
        reviewers.add(reviewer)
        slots.add(slot)


def record_slate(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        require_problem(records)
        fields(value, ("id", "selected", "deferred", "rejected", "bar_review_ids", "review_ids", "comparison", "reason"))
        text(value["id"], "Slate ID")
        text(value["comparison"], "Candidate comparison")
        for identifier in strategy.items(value["selected"], "Selected candidates", code="invalid_input"):
            text(identifier, "Selected candidate ID")
        for entry in strategy.items(value["deferred"], "Deferred candidates", code="invalid_input"):
            fields(entry, ("candidate_id", "evidence_needed"))
            text(entry["candidate_id"], "Deferred candidate ID")
            text(entry["evidence_needed"], "Evidence that would raise the deferred candidate")
        for entry in strategy.items(value["rejected"], "Rejected candidates", code="invalid_input"):
            fields(entry, ("candidate_id", "reason"))
            text(entry["candidate_id"], "Rejected candidate ID")
            text(entry["reason"], "Reason for rejecting the candidate")
        candidates = find_current_candidates(records)
        named = value["selected"] + [entry["candidate_id"] for entry in value["deferred"] + value["rejected"]]
        missing = sorted(set(candidates) - set(named))
        extra = sorted({identifier for identifier in named if identifier not in candidates or named.count(identifier) > 1})
        if missing or extra:
            raise ResearchError("problem_slate_incomplete", "Give every current candidate exactly one disposition",
                                {"missing": missing, "extra": extra})
        if not value["selected"]:
            text(value["reason"], "Reason for selecting no candidate", code="problem_slate_reason_missing")
        elif value["reason"] != "":
            text(value["reason"], "Slate reason")
        for identifier in value["selected"]:
            shortfalls = problem_rubric.assess_potential(candidates[identifier]["potential"])
            if shortfalls:
                raise ResearchError("problem_slate_candidate_below_threshold",
                                    "Defer a candidate below the potential threshold with the evidence that would raise it",
                                    {"candidate_id": identifier, "shortfalls": shortfalls})
        _verify_bars(records, artifacts, value["bar_review_ids"])
        _verify_reviews(records, artifacts, value["review_ids"], value["bar_review_ids"], sorted(candidates))
        record = dict(value, candidate_ids=sorted(candidates), recorded_revision=expected_revision + 1)
        return [immutable_record(records, "problem_slate", value["id"], record)], record

    return prepared_mutation(store, "problem.slate", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def assess_slate(records, artifacts):
    slate = find_slate(records)
    if slate is None:
        return [obligation("problem_slate_missing", "Compare the current candidates and record the selection decision.")]
    current = sorted(find_current_candidates(records))
    if slate["candidate_ids"] != current:
        return [obligation("problem_slate_stale", "Record a new slate that disposes of every current candidate.",
                           slate_id=slate["id"], missing=sorted(set(current) - set(slate["candidate_ids"])),
                           extra=sorted(set(slate["candidate_ids"]) - set(current)))]
    statement = find_current_statement(records)
    if statement is None or statement.get("candidate_id") in slate["selected"]:
        return []
    return [obligation("problem_candidate_unselected", "Formulate a candidate that the latest slate selects.",
                       statement_id=statement["id"], candidate_id=statement.get("candidate_id"))]
