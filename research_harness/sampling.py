"""Sampled verification preparation (sampled-v1): a stratified random sample of the population,
placement judgments, the percentile prediction, and the verification limits."""

import math
import random

from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, text
from .principles import preparation_policy

SAMPLED = "sampled-v1"
SAMPLE_LIMIT = 100
SEARCH_READING_LIMIT = 20
CORE_LIMIT = 10
UNPLACED_WIDEN = 20
POSITIONS = ("above", "below", "unplaced")


def core_requirements(records):
    """Fulltext requirements with purpose core under the verification profile."""
    return sorted((r for r in records.get("fulltext_requirement", {}).values()
                   if r["profile"] == "verification" and r["purpose"] == "core"), key=lambda r: r["id"])


def _require_sampled(records):
    if preparation_policy(records) != SAMPLED:
        raise ResearchError("policy_inapplicable", "Sample records belong to the sampled-v1 preparation policy",
                            {"policy": preparation_policy(records)})


def draw(members, size, seed):
    """A deterministic stratified sample: equal allocation per month, a short month's shortfall
    passed to the next months, and the whole population when it fits within size."""
    by_month = {}
    for item in sorted(members, key=lambda m: (m["month"], m["work_id"])):
        by_month.setdefault(item["month"], []).append(item)
    if len(members) <= size:
        return [dict(m) for month in sorted(by_month) for m in by_month[month]]
    rng = random.Random(seed)
    for month in sorted(by_month):
        rng.shuffle(by_month[month])
    months = sorted(by_month)
    taken = {month: 0 for month in months}
    remaining = size
    while remaining > 0:
        open_months = [m for m in months if taken[m] < len(by_month[m])]
        share = max(1, remaining // len(open_months))
        for month in open_months:
            allowance = min(share, len(by_month[month]) - taken[month], remaining)
            taken[month] += allowance
            remaining -= allowance
            if remaining == 0:
                break
    return [dict(m) for month in months for m in by_month[month][:taken[month]]]


def _members(records, collection):
    from .acquisition import _collection_summary
    summary = _collection_summary(records, collection)
    if summary["pending"]:
        raise ResearchError("collection_pending", "Complete the population enumeration before drawing the sample",
                            {"pending": summary["pending"]})
    # One entry per work: a work with several reading obligations keeps the last of them,
    # which is the version the rest of the harness reads as version_ids[-1].
    members = {}
    for item in summary["reading_obligations"]:
        work = records.get("work", {}).get(item["version_id"]) or {}
        members[item["work_id"]] = {"work_id": item["work_id"], "version_id": item["version_id"],
                                    "month": (work.get("publication_date") or "")[:7]}
    return list(members.values())


def record_sample(store, payload, *, expected_revision, request_id):
    """Draw and record {id, collection_id, size, seed} as the verification's sample."""
    def prepare(records, value):
        fields(value, ("id", "collection_id", "size", "seed"))
        text(value["id"], "Sample ID")
        text(value["seed"], "Sample seed")
        _require_sampled(records)
        if type(value["size"]) is not int or not 1 <= value["size"] <= SAMPLE_LIMIT:
            raise ResearchError("invalid_input", "The sample size is between 1 and " + str(SAMPLE_LIMIT))
        collection = records.get("collection", {}).get(value["collection_id"])
        if collection is None:
            raise ResearchError("cohort_missing", "Select the frozen population collection", {"collection_id": value["collection_id"]})
        members = _members(records, collection)
        population_digest = digest(sorted(m["work_id"] for m in members))
        existing = current_sample(records)
        if (existing is not None and existing["collection_id"] == value["collection_id"]
                and existing["population_digest"] == population_digest):
            raise ResearchError("sample_exists", "This verification already drew its sample; draw again only after the population changed",
                                {"sample_id": existing["id"]})
        drawn = draw(members, value["size"], value["seed"])
        months = {}
        for item in members:
            months.setdefault(item["month"], {"population": 0, "sampled": 0})["population"] += 1
        for item in drawn:
            months[item["month"]]["sampled"] += 1
        record = {"id": value["id"], "collection_id": value["collection_id"], "seed": value["seed"], "size": value["size"],
                  "population": len(members), "population_digest": population_digest,
                  "months": months, "members": drawn, "revision": expected_revision + 1}
        return [immutable_record(records, "cohort_sample", value["id"], record),
                ("cohort_sample_selection", "current", {"id": value["id"]})], record

    return prepared_mutation(store, "cohort.sample", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def current_sample(records):
    selection = records.get("cohort_sample_selection", {}).get("current")
    return records.get("cohort_sample", {}).get(selection["id"]) if selection else None


def placements(records, sample):
    """The placement judgment recorded on each sampled member's abstract reading, by version.
    Readings are taken in batch order, so the latest batch's placement is the one that stands."""
    batches = records.get("reading_batch", {})
    wanted = {m["version_id"] for m in sample["members"]}
    judged = [r for r in records.get("reading", {}).values()
              if (r.get("batch") or {}).get("placement") and r["version_id"] in wanted
              and r["assessment"]["status"] == "complete"]
    judged.sort(key=lambda r: (batches.get(r["batch"]["batch_id"], {}).get("revision", 0), r["id"]))
    return {r["version_id"]: r["batch"]["placement"] for r in judged}


def prediction(records):
    """The percentile estimate and one-standard-error band the sample supports."""
    sample = current_sample(records)
    if sample is None:
        raise ResearchError("sample_missing", "Draw the verification sample before predicting")
    judged = placements(records, sample)
    above = sum(p["position"] == "above" for p in judged.values())
    below = sum(p["position"] == "below" for p in judged.values())
    unplaced = sum(p["position"] == "unplaced" for p in judged.values())
    placed = above + below
    if placed == 0:
        return {"sample_id": sample["id"], "size": len(sample["members"]), "n": len(judged), "placed": 0,
                "above": above, "below": below, "unplaced": unplaced,
                "percentile": None, "standard_error": None, "band": None, "widen_required": unplaced > UNPLACED_WIDEN}
    share = above / placed
    error = math.sqrt(share * (1 - share) / placed) * 100
    # The platform states a percentile and a band as integers from 1 to 100, with best <= percentile <= worst.
    percentile = min(100, max(1, round((1 - share) * 100)))
    band = {"best": max(1, round(percentile - error)), "worst": min(100, round(percentile + error))}
    return {"sample_id": sample["id"], "size": len(sample["members"]), "n": len(judged), "placed": placed,
            "above": above, "below": below, "unplaced": unplaced,
            "percentile": percentile, "standard_error": round(error, 2), "band": band, "widen_required": unplaced > UNPLACED_WIDEN}
