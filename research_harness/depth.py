"""Prospective research tool amounts, deliberately outside evidence dependencies."""

from .errors import ResearchError
from .operations import fields, immutable_record, prepared_mutation, profile_name, text


VERIFICATION_DEPTH_KEYS = ("hits_per_query", "search_readings", "core_fulltexts")


def build_default_depth_values():
    from . import lineage, sampling
    return {"loop_abstracts": lineage.LOOP_LIMIT, "round_loop_abstracts": lineage.ROUND_LOOP_LIMIT,
            "hits_per_query": lineage.HITS_PER_QUERY, "innovation_candidates": lineage.INNOVATION_CANDIDATES,
            "innovation_cases": lineage.INNOVATION_CASES, "sample_size": sampling.SAMPLE_LIMIT,
            "search_readings": sampling.SEARCH_READING_LIMIT, "core_fulltexts": sampling.CORE_LIMIT}


def resolve_depth_profile(records, *, profile=None):
    return profile_name(profile if profile is not None else
                        (records.get("configuration", {}).get("research") or {}).get("profile", "research"))


def find_current_depth_plan(records, *, profile=None):
    profile = resolve_depth_profile(records, profile=profile)
    selections = records.get("depth_plan_selection", {})
    selection = selections.get(profile)
    if selection is None and profile == "research":
        selection = selections.get("current")
    plan = records.get("depth_plan", {}).get(selection["id"]) if selection else None
    return plan if plan is not None and plan.get("profile", "research") == profile else None


def resolve_depth_value(records, key, default, *, profile=None):
    profile = resolve_depth_profile(records, profile=profile)
    if profile == "verification" and key not in VERIFICATION_DEPTH_KEYS:
        return default
    plan = find_current_depth_plan(records, profile=profile)
    return plan["values"].get(key, default) if plan else default


def resolve_depth_values(records, overrides=None, keys=None, *, profile=None):
    """Resolve prospective amounts without changing a plan or any historical record."""
    defaults = build_default_depth_values()
    profile = resolve_depth_profile(records, profile=profile)
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or not set(overrides) <= defaults.keys():
        raise ResearchError("invalid_input", "Depth values must map supported tool amounts to integers", {"keys": sorted(defaults)})
    if profile == "verification" and not set(overrides) <= set(VERIFICATION_DEPTH_KEYS):
        raise ResearchError("profile_inapplicable", "Verification depth applies only to " + ", ".join(VERIFICATION_DEPTH_KEYS))
    for key, amount in overrides.items():
        if type(amount) is not int:
            raise ResearchError("invalid_input", "The " + key + " depth must be a positive integer")
        if profile == "verification" and amount < defaults[key]:
            raise ResearchError("invalid_input", "The " + key + " depth " + str(amount)
                                + " must be at least the verification default " + str(defaults[key]))
        if amount < 1:
            raise ResearchError("invalid_input", "The " + key + " depth must be a positive integer")
    values = {key: resolve_depth_value(records, key, default, profile=profile) for key, default in defaults.items()}
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
        fields(value, ("id", "values", "reason"), ("profile", "stopping_criterion"))
        text(value["id"], "Depth plan ID")
        text(value["reason"], "Depth change reason")
        profile = resolve_depth_profile(records, profile=value.get("profile"))
        if profile == "verification":
            if value.get("profile") != "verification":
                raise ResearchError("profile_inapplicable", "A verification depth plan requires profile: verification")
            text(value.get("stopping_criterion"), "Verification stopping criterion")
        elif "stopping_criterion" in value:
            text(value["stopping_criterion"], "Depth stopping criterion")
        if not isinstance(value["values"], dict):
            raise ResearchError("invalid_input", "Depth values must map supported tool amounts to integers")
        values = resolve_depth_values(records, value["values"],
                                      keys=VERIFICATION_DEPTH_KEYS if profile == "verification" else None, profile=profile)
        record = {"id": value["id"], "profile": profile, "values": values,
                  "reason": value["reason"], "revision": expected_revision + 1}
        if "stopping_criterion" in value:
            record["stopping_criterion"] = value["stopping_criterion"]
        return [immutable_record(records, "depth_plan", value["id"], record),
                ("depth_plan_selection", profile, {"id": value["id"]})], record

    return prepared_mutation(store, "research.depth", payload, prepare, expected_revision=expected_revision, request_id=request_id)
