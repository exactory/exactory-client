"""Lineage preparation (lineage-v1): the bounded five-purpose loop, innovation candidates, the population query."""

from .errors import ResearchError
from .evidence import digest
from .graph import obligation
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
