"""Prospective research tool amounts, deliberately outside evidence dependencies."""

from .errors import ResearchError
from .operations import fields, immutable_record, prepared_mutation, text


def build_default_depth_values():
    from . import lineage, sampling
    return {"loop_abstracts": lineage.LOOP_LIMIT, "round_loop_abstracts": lineage.ROUND_LOOP_LIMIT,
            "hits_per_query": lineage.HITS_PER_QUERY, "innovation_candidates": lineage.INNOVATION_CANDIDATES,
            "innovation_cases": lineage.INNOVATION_CASES, "sample_size": sampling.SAMPLE_LIMIT,
            "search_readings": sampling.SEARCH_READING_LIMIT, "core_fulltexts": sampling.CORE_LIMIT}


def find_current_depth_plan(records):
    if (records.get("configuration", {}).get("research") or {}).get("profile") == "verification":
        return None
    selection = records.get("depth_plan_selection", {}).get("current")
    return records.get("depth_plan", {}).get(selection["id"]) if selection else None


def resolve_depth_value(records, key, default):
    plan = find_current_depth_plan(records)
    return plan["values"].get(key, default) if plan else default


def resolve_depth_values(records, overrides=None, keys=None):
    """Resolve prospective amounts without changing a plan or any historical record."""
    defaults = build_default_depth_values()
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or not set(overrides) <= defaults.keys():
        raise ResearchError("invalid_input", "Depth values must map supported tool amounts to integers", {"keys": sorted(defaults)})
    if overrides and (records.get("configuration", {}).get("research") or {}).get("profile") == "verification":
        raise ResearchError("profile_inapplicable", "Verification retains its fixed depth contract")
    for key, amount in overrides.items():
        if type(amount) is not int or amount < 1:
            raise ResearchError("invalid_input", "The " + key + " depth must be a positive integer")
    values = {key: resolve_depth_value(records, key, default) for key, default in defaults.items()}
    values.update(overrides)
    if values["innovation_cases"] > values["innovation_candidates"]:
        raise ResearchError("invalid_input", "Innovation cases cannot exceed innovation candidates")
    if keys is None:
        return values
    if not set(keys) <= defaults.keys():
        raise ResearchError("invalid_input", "Select supported depth keys", {"keys": sorted(defaults)})
    return {key: values[key] for key in keys}


def record_depth(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        fields(value, ("id", "values", "reason"))
        text(value["id"], "Depth plan ID")
        text(value["reason"], "Depth change reason")
        if (records.get("configuration", {}).get("research") or {}).get("profile") == "verification":
            raise ResearchError("profile_inapplicable", "Depth plans apply to the research profile")
        if not isinstance(value["values"], dict):
            raise ResearchError("invalid_input", "Depth values must map supported tool amounts to integers")
        values = resolve_depth_values(records, value["values"])
        record = {"id": value["id"], "values": values, "reason": value["reason"], "revision": expected_revision + 1}
        return [immutable_record(records, "depth_plan", value["id"], record),
                ("depth_plan_selection", "current", {"id": value["id"]})], record

    return prepared_mutation(store, "research.depth", payload, prepare, expected_revision=expected_revision, request_id=request_id)
