"""Lineage preparation (lineage-v1): the bounded five-purpose loop, innovation candidates, the population query."""

from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .literature import SEARCH_PURPOSES  # literature imports lineage inside functions only, so this stays acyclic.
from .operations import fields, immutable_record, prepared_mutation, text
from .principles import preparation_policy

LINEAGE = "lineage-v1"
LOOP_LIMIT = 100
ROUND_LOOP_LIMIT = 40
HITS_PER_QUERY = 10
INNOVATION_CANDIDATES = 10
INNOVATION_CASES = 5
LOOP_SOURCES = ("search", "population", "citing", "author")
COVERING = ("relevant", "contradictory")


def loop_readings(records):
    """Abstract readings registered as loop entries, in id order."""
    return sorted((r for r in records.get("reading", {}).values() if (r.get("batch") or {}).get("loop")), key=lambda r: r["id"])


def candidate_readings(records):
    """Abstract readings registered as innovation candidates, in id order."""
    return sorted((r for r in records.get("reading", {}).values() if (r.get("batch") or {}).get("innovation_candidate")),
                  key=lambda r: r["id"])


def candidate_families(records):
    return {records["work"][r["version_id"]]["work_id"] for r in candidate_readings(records) if r["version_id"] in records.get("work", {})}


def loop_state(records, profile="research"):
    """Per purpose: loop readings, covering readings, the distinct queries tried, and whether the purpose is covered."""
    readings = loop_readings(records)
    searches = [s for s in records.get("literature_search", {}).values()
                if s["profile"] == profile and s["purpose"] in SEARCH_PURPOSES]
    purposes = {}
    for purpose in SEARCH_PURPOSES:
        hits = [r for r in readings if purpose in r["batch"]["loop"]["purposes"]]
        relevant = [r for r in hits if r["batch"]["loop"]["disposition"] in COVERING]
        own = [s for s in searches if s["purpose"] == purpose]
        queries = {q for s in own for q in s["queries"]}
        empty = len(queries) >= 2 and not any(d["disposition"] in COVERING for s in own for d in s.get("dispositions", []))
        purposes[purpose] = {"readings": len(hits), "relevant": len(relevant), "queries": len(queries),
                             "queries_tried": sorted(queries), "covered": bool(relevant) or empty}
    return {"readings": len(readings), "limit": LOOP_LIMIT, "purposes": purposes,
            "digest": digest([sorted(r["id"] for r in readings), sorted(s["id"] for s in searches)])}


def record_loop_closure(store, payload, *, expected_revision, request_id):
    """Close the loop with {id, purposes: {purpose: {status: covered|gap, note}}} against the current loop state.

    Each closed purpose keeps the queries tried, so a recorded gap names the searches it rests on."""
    def prepare(records, value):
        fields(value, ("id", "purposes"))
        text(value["id"], "Closure ID")
        if preparation_policy(records) != LINEAGE:
            raise ResearchError("policy_inapplicable", "Loop closures belong to the lineage-v1 policy", {"policy": preparation_policy(records)})
        state = loop_state(records)
        if not isinstance(value["purposes"], dict) or set(value["purposes"]) != set(SEARCH_PURPOSES):
            raise ResearchError("invalid_input", "Close every one of the five purposes exactly once")
        closed = {}
        for purpose, item in value["purposes"].items():
            fields(item, ("status", "note"))
            text(item["note"], "Closure note for " + purpose)
            current = state["purposes"][purpose]
            if item["status"] not in ("covered", "gap"):
                raise ResearchError("invalid_input", "Closure status is covered or gap", {"purpose": purpose})
            if item["status"] == "covered" and not current["covered"]:
                raise ResearchError("invalid_input", "A covered purpose needs a covering reading or two empty queries", {"purpose": purpose})
            if item["status"] == "gap" and current["covered"]:
                raise ResearchError("invalid_input", "A covered purpose is not a gap", {"purpose": purpose})
            if item["status"] == "gap" and current["queries"] < 2 and state["readings"] < LOOP_LIMIT:
                raise ResearchError("invalid_input", "A gap needs two distinct queries tried or the loop at its limit", {"purpose": purpose})
            closed[purpose] = dict(item, queries_tried=current["queries_tried"])
        record = {"id": value["id"], "purposes": closed, "loop_digest": state["digest"], "readings": state["readings"],
                  "revision": expected_revision + 1}
        return [immutable_record(records, "loop_closure", value["id"], record), ("loop_closure_selection", "current", {"id": value["id"]})], record

    return prepared_mutation(store, "literature.loop_closure", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def current_closure(records):
    selection = records.get("loop_closure_selection", {}).get("current")
    return records.get("loop_closure", {}).get(selection["id"]) if selection else None


def loop_obligations(records, profile="research"):
    closure = current_closure(records)
    if closure is None:
        return [obligation("loop_closure_missing", "Run the five-purpose loop and close it with each purpose covered or recorded as a gap.")]
    if closure["loop_digest"] != loop_state(records, profile)["digest"]:
        return [obligation("loop_closure_stale", "Loop readings or searches changed after the closure; close the loop again.", closure_id=closure["id"])]
    return []
