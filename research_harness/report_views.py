"""Bounded advisory views of fully evaluated research reports.

These functions consume an already evaluated report. They never read the store,
never evaluate a gate, and never authorize a mutation: a hint that omits a field
is a pointer to the detailed command, not an executable instruction.
"""

from collections import Counter

from .errors import ResearchError
from .evidence import digest


PRIORITY = ("migration_required", "profile_mismatch", "configuration_missing", "constitution_revalidation_required",
            "constitution_archive_missing", "collection_pending", "cohort_missing", "screening_missing",
            "cohort_abstract_reading_missing", "screening_audit_reading_missing", "screening_audit_failed", "doctrine_coverage_missing",
            "objective_missing", "objective_mismatch", "roots_missing", "root_missing", "target_source_pin_missing",
            "target_source_pin_invalid", "target_mismatch", "critical_source_unavailable", "fulltext_reading_missing",
            "source_bundle_missing", "source_bundle_incomplete", "required_unit_missing", "required_unit_incomplete",
            "required_unit_uninspected", "reading_bundle_stale", "bibliography_incomplete", "reference_identity_ambiguous",
            "reference_unresolved", "search_purpose_missing", "search_scope_stale", "search_frontier_stale",
            "search_evidence_stale", "search_dispositions_missing", "search_pending", "abstract_reading_missing",
            "historical_version_unresolved", "standards_missing", "rationale_missing", "innovation_missing",
            "context_missing", "synthesis_dependencies_stale", "resource_budget_exhausted",
            "publication_bundle_missing", "publication_readiness_stale", "publication_artifact_changed", "manuscript_reviews_required",
            "round_assessment_missing", "round_search_missing", "round_exemplar_missing", "round_cycle_missing", "round_claims_dropped",
            "round_claim_missing", "round_decision_missing", "round_review_missing", "round_review_pending", "round_admission_missing")
_HINT_LIMITS = {"code": 48, "version_id": 64, "work_id": 64, "collection_id": 64, "unit_id": 64, "explanation": 120}
_MAX_PAGE = 500


def priority(item):
    code = item.get("code")
    return PRIORITY.index(code) if code in PRIORITY else len(PRIORITY)


def order_obligations(obligations):
    """Preparation order first, then exact version, then a stable digest."""
    return sorted(obligations, key=lambda o: (priority(o), o.get("version_id") or "", digest(o)))


def _short(value, limit):
    return value[:limit] if isinstance(value, str) else None


def _hint(value):
    if not isinstance(value, dict):
        return None
    result = {key: _short(value.get(key), limit) for key, limit in _HINT_LIMITS.items()}
    result["truncated_fields"] = [key for key, limit in _HINT_LIMITS.items()
                                  if isinstance(value.get(key), str) and len(value[key]) > limit]
    result["details_required"] = True
    return result


def _obligations(values):
    counts = Counter(value["code"] for value in values)
    selected = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6]
    shown = sum(count for _, count in selected)
    return {"total": len(values), "shown_obligations": shown, "omitted_obligations": len(values) - shown,
            "omitted_codes": len(counts) - len(selected),
            "by_code": [{"code": _short(code, 48), "count": count, "code_truncated": len(code) > 48} for code, count in selected]}


def next_summary(report):
    """Return a navigation hint, never a stage-transition authorization."""
    preparation = report.get("preparation") or {}
    return {"schema": "research-next-summary-v1", "revision": report["revision"], "ready": report["ready"],
            "preparation_ready": preparation.get("ready"), "advisory_only": True, "detail_available": True,
            "next_hint": _hint(report.get("next")),
            "details": "exactory-research obligations --code CODE [--limit N] [--cursor CURSOR]"}


def status_summary(report):
    """Summarize obligations without embedding evidence or author notes."""
    result = next_summary(report)
    preparation = report.get("preparation") or {}
    study = report.get("study") or {}
    result.update({"schema": "research-status-summary-v1", "runtime": report.get("runtime"),
                   "profile": _short(report.get("profile"), 24), "stage": _short(study.get("stage"), 32),
                   "status": _short(study.get("status"), 32), "waiting": _short(study.get("waiting"), 160),
                   "display_strings_may_be_truncated": True,
                   "obligations": _obligations(report.get("obligations", [])),
                   "preparation_obligations": _obligations(preparation.get("obligations", [])),
                   "counts": report.get("counts"), "resources": report.get("resources", {}),
                   "round": report.get("round"), "evaluation": report.get("evaluation")})
    return result


def current_obligations(report):
    """The obligations `next` follows: the preparation stage's while it has any, else readiness."""
    return report.get("preparation", {}).get("obligations") or report["obligations"]


def obligations_page(report, code, *, limit, cursor):
    """One revision-bound page of the current obligations that carry this code."""
    if type(limit) is not int or not 1 <= limit <= _MAX_PAGE:
        raise ResearchError("invalid_input", "Page limit must be between 1 and " + str(_MAX_PAGE))
    offset = 0
    if cursor is not None:
        try:
            revision, offset = (int(part) for part in cursor.split(":"))
        except (AttributeError, ValueError) as error:
            raise ResearchError("invalid_input", "Cursor must be REVISION:OFFSET") from error
        if revision != report["revision"] or offset < 0:
            raise ResearchError("stale_cursor", "The store changed; restart the listing", {"revision": report["revision"]})
    items = [o for o in order_obligations(current_obligations(report)) if o.get("code") == code]
    page = items[offset:offset + limit]
    end = offset + len(page)
    return {"schema": "research-obligations-page-v1", "revision": report["revision"], "code": code, "total": len(items),
            "offset": offset, "returned": len(page),
            "next_cursor": None if end >= len(items) else str(report["revision"]) + ":" + str(end),
            "obligations": page, "advisory_only": True}
