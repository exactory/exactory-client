"""Apply one strategic state at managed entrypoints without recursive readiness."""

from .errors import ResearchError
from .evidence import digest


def decision_report(records, artifacts, boundary, *, decision_id=None):
    from .research_decisions import decision_state
    return decision_state(records, artifacts, boundary, decision_id=decision_id)


def require_decision(records, artifacts, boundary, *, decision_id=None):
    report = decision_report(records, artifacts, boundary, decision_id=decision_id)
    if not report["ready"]:
        raise ResearchError("research_decision_required", "Resolve the current scientific decision before " + boundary,
                            {"boundary": boundary, "obligations": report["obligations"],
                             "next": "exactory-research research-decision-assess --boundary " + boundary})
    return report


def with_decision(report, records, artifacts, boundary):
    """Keep the scientific report readable while exposing the same strategic obligations."""
    state = decision_report(records, artifacts, boundary)
    if not state.get("required", state.get("decision") is not None or bool(state["obligations"])):
        return report
    obligations = list({digest(item): item for item in report["obligations"] + state["obligations"]}.values())
    values = {"ready": not obligations, "obligations": obligations, "research_decision": state}
    if "manuscript_ready" in report:
        values.update(manuscript_ready=not obligations, manuscript_obligations=obligations)
    if "counts" in report:
        values["counts"] = dict(report["counts"], obligations=len(obligations))
    return dict(report, **values)
