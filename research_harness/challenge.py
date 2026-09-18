"""The challenges ahead of a study (design 7.1).

A research study records, before ideation, the ultimate goal its field is trying to reach and,
where useful, nearer large goals on the way, each with criteria that would show it reached and
the evidence behind it. The latest record is current for the whole study, across manuscript
iterations and rounds; a new record with a reason replaces it only when the direction changes.
The record is study-level: it is not a synthesis section and it stays outside the preparation
digest, so recording it changes neither the foundation nor a pinned bundle.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import fields, immutable_record, prepared_mutation, strings, text

HORIZONS = ("ultimate", "near_term")
_ERROR = "invalid_grand_challenge"


def find_current_challenge(records):
    """The study's current Grand Challenge record, or None before one is recorded."""
    selected = records.get("grand_challenge_selection", {}).get("current")
    return records["grand_challenge"][selected["id"]] if selected else None


def resolve_criterion_ids(records):
    """The criterion IDs of the current Grand Challenge record; empty before one is recorded."""
    current = find_current_challenge(records)
    if current is None:
        return set()
    return {criterion["id"] for item in current["payload"]["challenges"] for criterion in item["criteria"]}


def validate_criterion_ids(records, values, name, code):
    """Nonempty IDs of criteria of the current Grand Challenge record (design 7.4 and 7.6)."""
    identifiers = strings(values, name, nonempty=True, code=code)
    unknown = sorted(set(identifiers) - resolve_criterion_ids(records))
    if unknown:
        raise ResearchError(code, "Name criteria of the study's current Grand Challenge record", {"criterion_ids": unknown})
    return identifiers


def _check_items(value, name):
    if not isinstance(value, list) or not value:
        raise ResearchError(_ERROR, name + " must be an array with at least one entry")
    return value


def record_grand_challenge(store, payload, *, expected_revision, request_id):
    """Record the challenges ahead of the study; the new record becomes the current one."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        # development imports synthesis, which reads this module for the preparation obligation.
        from .development import _Context, _Evidence
        fields(value, ("id", "reason", "challenges"), code=_ERROR)
        text(value["id"], "Grand Challenge record ID", code=_ERROR)
        text(value["reason"], "Grand Challenge record reason", code=_ERROR)
        evidence = _Evidence(_Context(records, Evaluation(records, artifacts)))
        challenge_ids, criterion_ids, linked = set(), set(), []
        for item in _check_items(value["challenges"], "Challenges"):
            fields(item, ("id", "horizon", "statement", "state", "criteria", "evidence"), code=_ERROR)
            for key in ("id", "statement", "state"):
                text(item[key], "Challenge " + key, code=_ERROR)
            if item["id"] in challenge_ids:
                raise ResearchError(_ERROR, "Challenge IDs must be unique")
            challenge_ids.add(item["id"])
            if item["horizon"] not in HORIZONS:
                raise ResearchError(_ERROR, "A challenge's horizon is ultimate or near_term")
            for criterion in _check_items(item["criteria"], "Challenge criteria"):
                fields(criterion, ("id", "statement"), code=_ERROR)
                text(criterion["id"], "Criterion ID", code=_ERROR)
                text(criterion["statement"], "Criterion", code=_ERROR)
                if criterion["id"] in criterion_ids:
                    raise ResearchError(_ERROR, "Criterion IDs must be unique across the record")
                criterion_ids.add(criterion["id"])
            linked.extend(evidence.one(reference) for reference in _check_items(item["evidence"], "Challenge evidence"))
        if not any(item["horizon"] == "ultimate" for item in value["challenges"]):
            raise ResearchError(_ERROR, "Name the ultimate goal the study is directed at")
        current = find_current_challenge(records)
        record = {"id": value["id"], "payload": value, "previous_id": current["id"] if current else None,
                  "evidence": linked, "recorded_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "grand_challenge", value["id"], record),
                ("grand_challenge_selection", "current", {"id": value["id"]})], record

    return prepared_mutation(store, "grand_challenge.record", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)
