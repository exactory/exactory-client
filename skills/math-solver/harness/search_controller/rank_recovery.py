"""Reviewed operator abandonment of a narrowly identified legacy rank interruption.

This is not native receipt recovery. It preserves subsequent input edits and
records an operator decision without asserting the original process outcome.
The operator must establish a period without concurrent native-file writers;
filesystem locks do not serialize arbitrary editors or the activity hook.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path

from . import schema as s
from .storage import canonical_bytes, safe_path, _strict_json
from .integration import native_snapshot


def require(condition, message):
    s.require(condition, message, "rank_recovery_refused")


def validate_subject(controller, state, repair):
    s.closed(repair, "subject review")
    subject, review = repair["subject"], repair["review"]
    s.closed(subject, "schema_version root objective_id contract_digest revision node_id intent current_snapshot operator source_evidence incident authorization quiescence")
    s.integer(subject["schema_version"], 1, 1)
    s.integer(subject["revision"])
    require(subject["root"] == str(controller.root), "Recovery is bound to another root")
    for key in ["objective_id", "contract_digest", "revision"]:
        require(subject[key] == state[key], "Recovery binding changed: " + key)
    s.validate_provenance(subject["operator"])
    s.closed(review, "schema_version subject_digest reviewer decision findings unresolved_objections")
    s.integer(review["schema_version"], 1, 1)
    s.validate_provenance(review["reviewer"])
    require(review["subject_digest"] == s.digest(subject), "Independent review does not bind this recovery")
    require(review["decision"] == "approve" and review["unresolved_objections"] == [],
            "Recovery requires an approving review without unresolved objections")
    for key in ["actor_id", "attestation_id"]:
        require(subject["operator"][key] != review["reviewer"][key], "The operator cannot review its own recovery")
    s.closed(review["findings"], "read_only_origin delta authority preservation quiescence")
    for value in review["findings"].values():
        s.text(value)
    return subject


def validate_eligibility(state, subject):
    intents = state["service"]["native_intents"]
    require(len(intents) == 1, "Recovery requires exactly one pending native intent")
    intent = next(iter(intents.values()))
    require(subject["intent"] == intent and subject["node_id"] == intent["node_id"],
            "Recovery does not bind the original intent and node")
    require(intent["command"] == "rank" and intent["output_paths"] == [],
            "Only an interrupted read-only rank is eligible")
    require(len(state["nodes"]) == 1 and state["proof_status"] == "open"
            and not state["acceptances"] and not state["checkpoints"] and not state["runs"],
            "Recovery is limited to an unexecuted initial research node")
    require(not state["control"]["pending_moves"] and not state["service"]["moves"]
            and not state["service"]["legacy_intents"], "Research execution is pending or recorded")
    for account in state["accounts"].values():
        require(account["historical_usage"] == "known", "Historical usage must be known")
        for key in ["used_moves", "used_runs", "reserved_moves", "reserved_runs", "historical_moves", "historical_runs"]:
            require(account[key] == 0, "Recovery cannot discharge or refund research usage")
    for key in ["used_moves", "used_runs", "reserved_moves", "reserved_runs"]:
        require(state["totals"][key] == 0, "Recovery cannot change research accounting")
    return intent, state["nodes"][intent["node_id"]]


@contextmanager
def original_owner(controller, intent, content):
    owner = content.get_blob(intent["ownership_digest"])
    path = safe_path(controller.store.root, "native/" + intent["id"] + ".lock")
    require(path.is_file() and not path.is_symlink(), "Original native ownership evidence is missing")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        identity = os.fstat(handle.fileno())
        current = path.lstat()
        require(owner["id"] == intent["id"] and (owner["device"], owner["inode"])
                == (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino),
                "Original native ownership evidence was replaced")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            require(False, "The original native invocation still owns its lock")
        yield handle


def pin_evidence(item, content):
    s.closed(item, "path digest")
    s.text(item["path"])
    s.digest_string(item["digest"])
    path = Path(item["path"])
    require(path.is_absolute() and str(path.resolve()) == str(path)
            and path.is_file() and not path.is_symlink(), "Evidence needs a canonical absolute regular-file path")
    require(path.stat().st_size <= 2 * 1024 * 1024, "Recovery evidence exceeds the bounded input size")
    raw = path.read_bytes()
    require(bool(raw.strip()) and len(raw) <= 2 * 1024 * 1024, "Recovery evidence is empty or too large")
    require(hashlib.sha256(raw).hexdigest() == item["digest"], "Recovery evidence changed after review")
    content.put_artifact(raw)


def validate_delta(before, after, content):
    old = {item["path"]: item["digest"] for item in before["files"]}
    new = {item["path"]: item["digest"] for item in after["files"]}
    require("journal.jsonl" in old and "journal.jsonl" in new
            and not content.get_artifact(old["journal.jsonl"]).strip()
            and not content.get_artifact(new["journal.jsonl"]).strip(),
            "Both original and current native journals must contain no moves")
    changed = {path for path in set(old) | set(new) if old.get(path) != new.get(path)}
    require(changed == {"ranking.json", "tasks.json", "activity.jsonl"},
            "Recovery requires precisely the reviewed ranking, task and activity changes")
    require("tasks.json" not in old and all(name in old and name in new for name in ["ranking.json", "activity.jsonl"])
            and "tasks.json" in new, "The original task file must be absent and all other inputs retained")
    original = _strict_json(content.get_artifact(old["ranking.json"]), "rank_recovery_refused")
    corrected = _strict_json(content.get_artifact(new["ranking.json"]), "rank_recovery_refused")
    require(isinstance(original, list) and bool(original)
            and canonical_bytes(corrected) == canonical_bytes({"order": original}),
            "Ranking recovery may only wrap the unchanged original list in an order object")
    previous = content.get_artifact(old["activity.jsonl"])
    current = content.get_artifact(new["activity.jsonl"])
    require(len(previous.splitlines()) == 6 and previous.endswith(b"\n") and current.startswith(previous),
            "The complete original six-line activity prefix must be preserved")
    suffix = current[len(previous):]
    lines = suffix.splitlines()
    require(len(lines) in {1, 3, 4} and suffix.endswith(b"\n"),
            "Only the ranking correction and its reviewed inspections may be appended")
    activity = _strict_json(lines[0], "rank_recovery_refused")
    s.closed(activity, "at tool target")
    s.text(activity["at"])
    require(activity["tool"] == "Edit" and activity["target"] == "ranking.json", "The appended activity must record the ranking correction")
    if len(lines) >= 3:
        inspections = [_strict_json(raw, "rank_recovery_refused") for raw in lines[1:]]
        for item, target in zip(inspections, ["ranking.json", "activity.jsonl", "activity.jsonl"]):
            s.closed(item, "at tool target")
            s.text(item["at"])
            require(item["tool"] == "Bash" and item["target"] == target,
                    "Only the exact reviewed ranking and activity inspections are eligible")
        require(inspections[0]["at"] == inspections[1]["at"],
                "The reviewed inspection pair must belong to the same recorded inspection")
    tasks = _strict_json(content.get_artifact(new["tasks.json"]), "rank_recovery_refused")
    s.closed(tasks, "tasks")
    require(len(s.records(tasks["tasks"])) == 4, "Exactly four new, unexecuted tasks are eligible")
    for number, task in enumerate(tasks["tasks"], 1):
        s.closed(task, "id text status added_at added_after_move done_at done_after_move")
        s.integer(task["id"], number, number)
        s.text(task["text"])
        s.text(task["added_at"])
        require(task["status"] == "open" and type(task["added_after_move"]) is int and task["added_after_move"] == 0
                and task["done_at"] is None and task["done_after_move"] is None,
                "The added tasks must have no execution or completion claims")


def build_abandonment(controller, state, spec, content, emit, locks):
    s.closed(spec, "rank_recovery")
    subject = validate_subject(controller, state, spec["rank_recovery"])
    intent, node = validate_eligibility(state, subject)
    require(locks is not None, "Recovery must own its locks through durable commitment")
    locks.enter_context(original_owner(controller, intent, content))
    marker = safe_path(controller.store.root, "native/" + intent["id"] + ".json")
    require(not marker.exists() and not marker.is_symlink(), "An existing native receipt requires ordinary recovery")
    before = content.get_blob(intent["pre_digest"])
    content.get_blob(intent["args_digest"])
    actual = native_snapshot(controller, node, content)
    require(actual == subject["current_snapshot"], "The complete native snapshot changed after review")
    validate_delta(before, actual, content)
    sources = s.records(subject["source_evidence"])
    require(2 <= len(sources) <= 8, "Pin the original rank implementation and its integration source")
    for item in sources + [subject["incident"], subject["authorization"], subject["quiescence"]]:
        pin_evidence(item, content)
    evidence_digest = content.put_blob(spec)
    emit("native_acknowledged", {
        "id": intent["id"], "outcome": "failed", "post_digest": content.put_blob(actual),
        "diagnostics": [
            "Operator abandonment of an interrupted read-only rank; the original process outcome is unknown.",
            "No original native terminal receipt exists. This is not successful ranking validation or mathematical evidence.",
            "Subsequent ranking, task and activity edits are preserved exactly. No research usage is refunded.",
            "Immutable recovery evidence: " + evidence_digest,
        ],
    })


class DigestSink:
    """Hash a native snapshot without writing content or reacquiring a store lock."""

    @staticmethod
    def put_artifact(raw):
        return hashlib.sha256(raw).hexdigest()


def audit_commit(controller, spec):
    """Detect drift during evidence persistence while both operation locks remain held."""
    state = controller.status()
    subject = validate_subject(controller, state, spec["rank_recovery"])
    intent, node = validate_eligibility(state, subject)
    owner = controller.store.get_blob(intent["ownership_digest"])
    lock = safe_path(controller.store.root, "native/" + intent["id"] + ".lock")
    require(lock.is_file() and not lock.is_symlink(), "Original ownership evidence disappeared before commitment")
    current = lock.lstat()
    require((current.st_dev, current.st_ino) == (owner["device"], owner["inode"]),
            "Original ownership evidence changed before commitment")
    marker = lock.with_suffix(".json")
    require(not marker.exists() and not marker.is_symlink(), "A native receipt appeared before commitment")
    digests = DigestSink()
    require(native_snapshot(controller, node, digests) == subject["current_snapshot"],
            "Native files changed during recovery; preserve the intervening edits")
    for item in subject["source_evidence"] + [subject["incident"], subject["authorization"], subject["quiescence"]]:
        pin_evidence(item, digests)
