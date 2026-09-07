"""Exact native problem snapshots and validated in-move transitions."""

import json

from . import schema as s
from .errors import SearchError
from .storage import safe_path, _strict_json


def problem_bytes(problem):
    """Use the native digest encoding, including its ASCII escaping."""
    return json.dumps(problem, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_problem(problem, claim):
    import attack
    s.require(isinstance(problem, dict), "Problem must be a record")
    for field in ["quadruple", "shape"]:
        s.require(isinstance(problem.get(field), dict), "Problem lacks its " + field)
    for field in ["direction", "mode"]:
        s.text(problem["quadruple"].get(field))
    defects = list(attack.find_problem_defects(problem))
    s.require(not defects, "Invalid problem snapshot: " + "; ".join(defects))
    s.require(problem["claim"] == claim, "Problem claim differs from its admission", "claim_mismatch")


def validate_journal_problem(state, move, payload):
    """Replay checks both sides without changing the original reservation."""
    import attack
    if "problem_transition" not in payload:
        s.require(payload["problem_digest"] == move["problem_digest"],
                  "Journal intent changed the reservation without a problem transition")
        return
    transition = payload["problem_transition"]
    s.closed(transition, "before after line")
    claim = state["nodes"][move["node_id"]]["claim"]["statement"]
    for field in ["before", "after"]:
        validate_problem(transition[field], claim)
    s.require(attack.compute_problem_digest(transition["before"]) == move["problem_digest"],
              "Problem transition does not start at the reserved problem", "digest_mismatch")
    s.require(attack.compute_problem_digest(transition["after"]) == payload["problem_digest"],
              "Problem transition does not end at the journal problem", "digest_mismatch")
    validate_journal_line(transition["line"], move, payload["problem_digest"])
    defects = (attack.find_move_cost_defects(transition["line"], transition["after"]["quadruple"])
               or list(attack.find_closing_defects(transition["line"], transition["after"]["quadruple"]))
               or list(attack.find_trigger_defects(transition["line"]["trigger_features"], transition["after"])))
    s.require(not defects, "Invalid transition journal line: " + "; ".join(defects))


def validate_journal_line(line, move, problem_digest):
    """A snapshot transition cannot be paired with a different reserved entry."""
    import attack
    s.require(isinstance(line, dict), "Journal line must be a record")
    body = {key: value for key, value in line.items() if key != "problem_digest"}
    defects = list(attack.find_move_defects(body))
    s.require(not defects, "Invalid journal line: " + "; ".join(defects))
    for field in ["move", "pass", "strategy", "entry", "walk", "trigger_features", "step_cites"]:
        s.require(line[field] == move[field], "Journal line differs from reserved entry: " + field,
                  "reservation_mismatch")
    s.require(line.get("problem_digest") == problem_digest,
              "Journal line differs from the recorded problem", "digest_mismatch")


def pin_problem(content, problem):
    """Preserve canonical native bytes under the digest already used by moves."""
    return content.put_artifact(problem_bytes(problem))


def load_problem_before(controller, node, reservation, content, source=None):
    """An old reservation needs an explicit preimage, never an inferred reset."""
    import attack
    before = None
    try:
        before = _strict_json(content.get_artifact(reservation["problem_digest"]), "corrupt_artifact")
    except SearchError as error:
        if error.code != "corrupt_artifact" or not isinstance(error.__cause__, FileNotFoundError):
            raise
    if source is not None:
        path = safe_path(controller.root, source)
        s.require(path.is_file() and not path.is_symlink(), "Original problem snapshot is missing", "missing_evidence")
        supplied = _strict_json(path.read_bytes(), "invalid_input")
        validate_problem(supplied, node["claim"]["statement"])
        s.require(attack.compute_problem_digest(supplied) == reservation["problem_digest"],
                  "Original problem snapshot differs from the reservation", "digest_mismatch")
        s.require(before is None or supplied == before,
                  "Original problem snapshot differs from stored evidence", "digest_mismatch")
        before = supplied
    s.require(before is not None,
              "The reserved problem snapshot is unavailable; supply its exact original problem value with journal add --problem-before",
              "problem_snapshot_required")
    validate_problem(before, node["claim"]["statement"])
    s.require(pin_problem(content, before) == reservation["problem_digest"],
              "Stored problem snapshot differs from the reservation", "digest_mismatch")
    return before


def audit_journal_problem(controller, node, move, intent, content):
    """A durable append cannot be reconciled against a different working problem."""
    import attack
    problem = attack.read_json(safe_path(controller.root, node["attack_slug"] + "/problem.json"))
    s.require(attack.compute_problem_digest(problem) == intent["problem_digest"],
              "Problem conflicts with its durable journal intent", "recovery_conflict")
    validate_problem(problem, node["claim"]["statement"])
    transition = intent.get("problem_transition")
    if transition is not None:
        for field, digest in [("before", move["problem_digest"]), ("after", intent["problem_digest"])]:
            s.require(content.get_artifact(digest) == problem_bytes(transition[field]),
                      "Problem transition snapshot differs from stored evidence", "digest_mismatch")


def audit_journal_append(controller, node, move, intent, content):
    """Return only the reserved prefix followed by its one validated frozen line."""
    audit_journal_problem(controller, node, move, intent, content)
    before = content.get_artifact(intent["before_digest"])
    after = content.get_artifact(intent["after_digest"])
    s.require(not before or before.endswith(b"\n"), "Reserved journal prefix is incomplete", "recovery_conflict")
    s.require(after.startswith(before), "Journal intent does not extend its reserved prefix", "recovery_conflict")
    suffix = after[len(before):]
    line = _strict_json(suffix, "recovery_conflict")
    validate_journal_line(line, move, intent["problem_digest"])
    s.require(suffix == (json.dumps(line) + "\n").encode("utf-8"),
              "Journal intent must append exactly one native line", "recovery_conflict")
    transition = intent.get("problem_transition")
    if transition is not None:
        s.require(line == transition["line"], "Journal bytes differ from their problem transition", "recovery_conflict")
    return before, after
