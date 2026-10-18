"""The single reading of the bounded policies' counts that both status and the policy report serve."""

from . import lineage, principles, sampling
from .depth import find_current_depth_plan, resolve_depth_values


def describe_depth(records):
    """The current depth plan with its resolved amounts, or None without a plan."""
    plan = find_current_depth_plan(records)
    return None if plan is None else {"plan_id": plan["id"], "values": resolve_depth_values(records)}


def limits_report(records):
    """Counts against every limit of the recorded policy, for status."""
    policy = principles.preparation_policy(records)
    report = {"policy": policy, "loop": None, "sample": None, "innovation_candidates": None, "search_readings": None, "core_papers": None}
    depth = describe_depth(records)
    if depth is not None:
        report["depth"] = depth
    if policy == lineage.LINEAGE:
        state = lineage.loop_state(records)
        report["loop"] = {"readings": state["readings"], "limit": state["limit"],
                          "covered": [p for p, s in state["purposes"].items() if s["covered"]]}
        report["innovation_candidates"] = {"families": len(lineage.candidate_families(records)), "required": lineage.resolve_innovation_candidates(records)}
    if policy == sampling.SAMPLED:
        if sampling.current_sample(records) is not None:
            prediction = sampling.prediction(records)
            report["sample"] = {k: prediction[k] for k in ("n", "placed", "unplaced", "percentile", "band", "widen_required")}
        report["search_readings"] = {"readings": len(sampling.search_readings(records)), "limit": sampling.resolve_search_reading_limit(records)}
        report["core_papers"] = {"requirements": len(sampling.core_requirements(records)), "limit": sampling.resolve_core_limit(records)}
    return report
