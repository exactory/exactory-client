"""Verify direction evidence in actual independent review inputs."""

import json

from .direction_sources import find_delivered_readings
from .evaluation import Evaluation
from .graph import obligation
from .review_protocol import assignment_state, describe_direction_evidence


def collect_observed_packets(records, artifacts, assignment_id):
    """Collect only observed inputs in the review's bound scientific ancestry."""
    pending, seen, packets = [assignment_id], set(), []
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        assignment = records.get("review_assignment", {}).get(identifier)
        if assignment is None:
            continue
        state = assignment_state(records, artifacts, identifier)
        invalid = {"review_invocation_unverified", "review_output_unavailable", "review_output_pending"}
        if state["output"] is not None and not any(item["code"] in invalid for item in state["obligations"]):
            packets.append((assignment, json.loads(artifacts.read(assignment["packet"]))))
        parent = assignment.get("history_assignment_id") or assignment.get("context", {}).get("prior_assignment_id")
        if parent is not None:
            pending.append(parent)
        repair = assignment.get("context", {}).get("context_repair")
        if isinstance(repair, dict) and repair.get("assignment_id") is not None:
            pending.append(repair["assignment_id"])
        elif parent is None and assignment["role"] == "slate" and assignment.get("session_id") != identifier:
            pending.append(assignment.get("session_id"))
    return packets


def assess_direction_reviews(records, artifacts, dossier, reviews, direction):
    """Require both observed reviewers to cover the same decisive evidence and claim."""
    evaluation = Evaluation.of(records, artifacts)
    expected, _ = describe_direction_evidence(records, evaluation, direction, dossier)
    readings = {item["id"]: item for item in expected["readings"]}
    obligations = []
    for review in reviews:
        covered = set()
        packets = collect_observed_packets(records, evaluation, review["assignment_id"])
        delivered = find_delivered_readings(records, evaluation, [records["reading"][identifier] for identifier in readings],
                                            [packet for _, packet in packets])
        missing_readings = set(readings) - delivered
        for assignment, packet in packets:
            supplied = packet.get("direction_evidence", {})
            for key in ("claim", "search", "standards", "grand_challenge"):
                if expected[key] is not None and supplied.get(key) == expected[key]:
                    covered.add(key)
        missing = sorted({"claim", "search", "standards", "grand_challenge"} - covered)
        if missing or missing_readings:
            obligations.append(obligation("direction_evidence_unassessed", "Obtain an observed assessment or bound supplement covering the decisive direction evidence and claim.",
                review_id=review["id"], missing=missing, reading_ids=sorted(missing_readings)))
    return obligations
