"""The single reading of the bounded policies' counts that both status and the policy report serve."""

from . import lineage, principles, sampling


def limits_report(records):
    """Counts against every limit of the recorded policy, for status."""
    policy = principles.preparation_policy(records)
    report = {"policy": policy, "loop": None, "sample": None, "innovation_candidates": None, "search_readings": None, "core_papers": None}
    if policy == lineage.LINEAGE:
        state = lineage.loop_state(records)
        report["loop"] = {"readings": state["readings"], "limit": state["limit"],
                          "covered": [p for p, s in state["purposes"].items() if s["covered"]]}
        report["innovation_candidates"] = {"families": len(lineage.candidate_families(records)), "required": lineage.INNOVATION_CANDIDATES}
    if policy == sampling.SAMPLED:
        if sampling.current_sample(records) is not None:
            prediction = sampling.prediction(records)
            report["sample"] = {k: prediction[k] for k in ("n", "placed", "unplaced", "percentile", "band", "widen_required")}
        report["search_readings"] = {"readings": len(sampling.search_readings(records)), "limit": sampling.SEARCH_READING_LIMIT}
        report["core_papers"] = {"requirements": len(sampling.core_requirements(records)), "limit": sampling.CORE_LIMIT}
    return report
