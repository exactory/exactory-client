"""Verify direction evidence in actual independent review inputs."""

import json

from .bar_sources import validate_inventory
from .evaluation import Evaluation
from .graph import obligation
from .review_protocol import assignment_state, build_direction_evidence
from .storage import Store


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
    return packets


def assess_direction_reviews(records, artifacts, dossier, reviews, direction):
    """Require both observed reviewers to cover the same decisive evidence and claim."""
    evaluation = Evaluation.of(records, artifacts)
    expected = build_direction_evidence(records, evaluation, direction, dossier)
    readings = {item["id"]: item for item in expected["readings"]}
    obligations = []
    for review in reviews:
        covered, missing_readings = set(), set(readings)
        packets = collect_observed_packets(records, evaluation, review["assignment_id"])
        explicit = {item["id"] for _, packet in packets for item in packet.get("direction_evidence", {}).get("readings", [])}
        for assignment, packet in packets:
            supplied = packet.get("direction_evidence", {})
            for key in ("claim", "search", "standards", "grand_challenge"):
                if expected[key] is not None and supplied.get(key) == expected[key]:
                    covered.add(key)
            for item in supplied.get("readings", []):
                if readings.get(item.get("id")) == item:
                    missing_readings.discard(item["id"])
            if missing_readings and "source_inventory" in packet:
                versions = {item["id"] for item in validate_inventory(evaluation, packet["source_inventory"])}
                values = {identifier: records["reading"][identifier] for identifier in readings}
                revisions = evaluation.once(("direction_reading_revisions", tuple(sorted(readings))),
                    lambda: Store(evaluation.root).find_record_revisions("reading", values))
                for identifier in list(missing_readings):
                    if (identifier not in explicit and readings[identifier]["version_id"] in versions and identifier in revisions
                            and assignment["recorded_revision"] > revisions[identifier]):
                        missing_readings.discard(identifier)
        missing = sorted({"claim", "search", "standards", "grand_challenge"} - covered)
        if missing or missing_readings:
            obligations.append(obligation("direction_evidence_unassessed", "Obtain an observed assessment or bound supplement covering the decisive direction evidence and claim.",
                review_id=review["id"], missing=missing, reading_ids=sorted(missing_readings)))
    return obligations
