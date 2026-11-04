"""Bounded advisory views of fully evaluated research reports.

These functions consume an already evaluated report. They never read the store,
never evaluate a gate, and never authorize a mutation: a hint that omits a field
is a pointer to the detailed command, not an executable instruction.
"""

from collections import Counter
import copy
import json

from .errors import ResearchError
from .evidence import digest


PRIORITY = ("migration_required", "profile_mismatch", "configuration_missing", "constitution_revalidation_required",
            "constitution_archive_missing", "collection_pending", "cohort_missing", "screening_missing",
            "cohort_abstract_reading_missing", "sample_missing", "sample_stale", "sample_reading_missing", "placement_missing",
            "screening_audit_reading_missing", "screening_audit_failed", "doctrine_coverage_missing",
            "roots_missing", "root_missing",
            "target_source_pin_missing", "target_source_pin_invalid", "target_mismatch", "critical_source_unavailable",
            "fulltext_reading_missing",
            "source_bundle_missing", "source_bundle_incomplete", "required_unit_missing", "required_unit_incomplete",
            "required_unit_uninspected", "reading_bundle_stale", "bibliography_incomplete", "reference_identity_ambiguous",
            "reference_unresolved", "search_purpose_missing", "search_scope_stale", "search_frontier_stale",
            "search_evidence_stale", "search_dispositions_missing", "search_pending", "loop_closure_missing",
            "loop_closure_stale", "abstract_reading_missing",
            "historical_version_unresolved", "research_intent_missing",
            "direction_search_missing", "direction_search_incomplete", "direction_root_reading_missing",
            "direction_reading_missing", "direction_standards_missing", "direction_standards_unresolved",
            "grand_challenge_missing", "objective_missing", "objective_mismatch",
            "standards_missing", "rationale_missing", "innovation_missing",
            "innovation_candidates_missing", "innovation_case_not_candidate", "context_missing",
            "review_worker_reconciliation_required", "review_observation_acceptance_pending", "review_capacity_wait",
            "review_task_dependency_changed", "review_epoch_stale", "review_task_pending", "review_parts_pending",
            "review_composition_conflict", "review_composition_pending", "review_assurance_dependency_pending",
            "assessment_incomplete", "problem_assurance_pending",
            "problem_rubric_below_threshold", "research_objection_unresolved",
            "review_quality_measurement_pending", "problem_review_pair_required",
            "synthesis_dependencies_stale", "resource_budget_exhausted",
            "publication_bundle_missing", "publication_readiness_stale", "publication_artifact_changed",
            "manuscript_reviews_required", "lineage_citation_missing",
            "round_assessment_missing", "round_search_missing", "round_exemplar_missing", "round_cycle_missing", "round_claims_dropped",
            "round_claim_missing", "manuscript_measurement_missing", "contribution_analysis_missing", "round_decision_missing",
            "round_review_missing", "round_review_pending", "round_admission_missing")
_HINT_LIMITS = {"code": 48, "version_id": 64, "work_id": 64, "collection_id": 64, "unit_id": 64, "explanation": 120}
_MAX_PAGE = 500
# The status summary names this many criterion ids of the Grand Challenge record, cut at this length, and counts
# the rest. A control character costs six bytes in JSON, so seven ids of 32 characters stay inside the room the
# 16 KiB bound leaves; `status` reports the whole record.
_SUMMARY_CRITERION_IDS = 6
_SUMMARY_ID_LENGTH = 32


def describe_parts_turn(assignment, state, series, *, ended_without_output=False):
    """Describe one evaluated parts turn without copying its inventory or output."""
    from . import review_parts

    header = assignment[review_parts.KEY]
    plan = review_parts.describe_plan(assignment)
    result = {key: header[key] for key in
              ('protocol', 'adapter', 'series_assignment_id', 'kind', 'turn')}
    result['recorded_revision'] = assignment['recorded_revision']
    result.update({key: copy.deepcopy(plan[key]) for key in
                   ('unit_allowance', 'card_allowance', 'remaining_turns')})
    result['unit_allowance_floor'] = plan.get('unit_allowance_floor')
    total = plan['units_carded'] + plan['units_remaining']
    result.update(units_carded=0, units_remaining=total)
    packets = series['packets']
    inventory = (series.get('delivery') or {}).get('inventory')
    if inventory is not None:
        reads = [packet for packet in packets if review_parts.find_kind(packet) == 'read']
        originals = [original for packet in reads
                     for original in packet[review_parts.KEY]['originals']]
        carded = 0
        for row in inventory['units'][:total]:
            item = dict(unit=row['handle'], item=True, start=0, end=row['item_characters'])
            if (review_parts.covers_original(originals, item, inventory)
                    and all(review_parts.has_series_attachment(None, reads, body['artifact'])
                            for body in row['bodies'])):
                carded += 1
        result.update(units_carded=carded, units_remaining=total - carded)
    result['complete'] = series['complete']
    result['issues'] = copy.deepcopy(series['issues'])
    result['next'] = []
    observed = any(packet[review_parts.KEY]['turn'] == header['turn'] for packet in packets)
    blocking_code = next((item['code'] for item in state.get('obligations', [])
                          if item['code'] not in ('review_output_pending', 'review_output_refused')), None)
    invalid_code = next((item['reason'] for item in series['issues']['examples']
                         if item['code'] == 'series_invalid'), None)
    if not series['complete']:
        if invalid_code is not None:
            result['blocked'] = invalid_code
        elif not state['ready'] and len(state.get('refused_attempts', [])) >= 2:
            result['blocked'] = 'review_slot_exhausted'
        elif not state['ready'] and blocking_code is not None:
            result['blocked'] = blocking_code
        elif not state['ready'] and state.get('output') is not None:
            result['blocked'] = next((item['code'] for item in state.get('obligations', [])),
                                     'review_invocation_unverified')
        elif (state['ready'] and observed) or ended_without_output:
            scope = {key: assignment[key] for key in
                     ('role', 'route_id', 'reviewer_id', 'author_id', 'dossier_id')}
            prior = (assignment['id'] if state['ready'] and observed
                     else assignment.get('context', {}).get('prior_assignment_id'))
            if prior is not None:
                scope['prior_assignment_id'] = prior
            result['next'] = [dict(kind='review', workflow='review-assignment', scope=scope)]
        else:
            result['next'] = [dict(kind='review', workflow='review-run',
                                   scope=dict(assignment_id=assignment['id']))]
    return result


def annotate_parts_obligations(report, review):
    """Keep pending-series actions consistent with the evaluated progress view."""
    assignments = review.get('assignments', {})
    obligations = []
    for item in report.get('obligations', []):
        progress = assignments.get(item.get('assignment_id'), {}).get('review_parts')
        if item.get('code') == 'review_parts_pending' and progress is not None:
            details = {key: progress[key] for key in
                       ('adapter', 'series_assignment_id', 'kind', 'turn',
                        'units_carded', 'units_remaining', 'next')}
            if 'blocked' in progress:
                details['blocked'] = progress['blocked']
            item = dict(item, **copy.deepcopy(details))
        obligations.append(item)
    return dict(report, obligations=obligations)


def priority(item):
    code = item.get("code")
    if code == "review_task_pending" and not item.get("next"):
        return PRIORITY.index("assessment_incomplete") + 0.5
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
    if value.get('code') == 'review_parts_pending':
        result['review_parts'] = {key: value.get(key) for key in
                                  ('kind', 'turn', 'units_carded', 'units_remaining')}
        result['review_parts'].update({key: _short(value.get(key), 64)
                                       for key in ('adapter', 'series_assignment_id', 'blocked')})
    if "next" in value:
        actions = value["next"] or []
        # An executable payload is exact or absent. A clipped id could target
        # different work, so large actions stay on the detailed obligation page.
        first = actions[0] if actions else None
        result["next_step"] = (copy.deepcopy(first) if first is not None and
            len(json.dumps(first, ensure_ascii=False, indent=2).encode("utf-8")) <= 1024 else None)
        result["omitted_next_steps"] = len(actions) - (result["next_step"] is not None)
    return result


def _obligations(values):
    counts = Counter(value["code"] for value in values)
    selected = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6]
    shown = sum(count for _, count in selected)
    return {"total": len(values), "shown_obligations": shown, "omitted_obligations": len(values) - shown,
            "omitted_codes": len(counts) - len(selected),
            "by_code": [{"code": _short(code, 48), "count": count, "code_truncated": len(code) > 48} for code, count in selected]}


def next_summary(report, *, counters=False):
    """Return a navigation hint, never a stage-transition authorization."""
    preparation = report.get("preparation") or {}
    result = {"schema": "research-next-summary-v1", "revision": report["revision"], "ready": report["ready"],
            "preparation_ready": preparation.get("ready"), "advisory_only": True, "detail_available": True,
            "next_hint": _hint(report.get("next")),
            "details": "exactory-research obligations --code CODE [--limit N] [--cursor CURSOR]"}
    if report.get("orchestration") == "problem_creation":
        result.update(orchestration=report["orchestration"], milestone=report["milestone"],
                      post_obligations=_obligations(report["obligations"]))
    if report.get("advisories"):
        # Stale output views are advisory and never replace the next hint.
        result["advisories"] = [{"code": item["code"], "reason": item["reason"]} for item in report["advisories"]]
    if "manuscript_ready" in report:
        result.update({"manuscript_ready": report["manuscript_ready"], "objective_complete": report["objective_complete"],
                       "manuscript_obligations": _obligations(report["manuscript_obligations"]),
                       "objective_obligations": _obligations(report["objective_obligations"]),
                       "scope_contract_id": _short(report.get("scope_contract_id"), 64),
                       "scientific_target_digest": report["scientific_target_digest"]})
    hint = result.get("next_hint") or {}
    if hint.get("next_step") is not None and len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")) > 4096:
        hint["next_step"] = None
        hint["omitted_next_steps"] += 1
    if counters:
        result["evaluation"] = report.get("evaluation")
    hint = result.get("next_hint")
    while hint and len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")) > 4096:
        fields = [key for key, value in hint.items() if isinstance(value, str) and value]
        if not fields:
            break
        key = max(fields, key=lambda item: len(hint[item]))
        hint[key] = hint[key][:-16]
        if key not in hint["truncated_fields"]:
            hint["truncated_fields"].append(key)
    return result


def _summarize_grand_challenge(challenge):
    """The record id, how many challenges each horizon has, and the first criterion ids, with a count of the rest."""
    if challenge is None:
        return None
    horizons = Counter(item["horizon"] for item in challenge["challenges"])
    criterion_ids = [criterion["id"] for item in challenge["challenges"] for criterion in item["criteria"]]
    return {"id": _short(challenge["id"], _SUMMARY_ID_LENGTH), "ultimate": horizons["ultimate"], "near_term": horizons["near_term"],
            "criterion_ids": [_short(identifier, _SUMMARY_ID_LENGTH) for identifier in criterion_ids[:_SUMMARY_CRITERION_IDS]],
            "omitted_criterion_ids": max(0, len(criterion_ids) - _SUMMARY_CRITERION_IDS)}


def _summarize_bundle_drift(drift):
    if drift is None:
        return None
    return {"bundle_id": _short(drift["bundle_id"], 64), "bundle_digest": drift["bundle_digest"],
            "bundle_id_truncated": len(drift["bundle_id"]) > 64,
            "files": [{"path": _short(item["path"], 128), "path_truncated": len(item["path"]) > 128,
                       "code": item["code"]} for item in drift["files"]]}


def status_summary(report):
    """Summarize obligations without embedding evidence or author notes."""
    result = next_summary(report)
    preparation = report.get("preparation") or {}
    study = report.get("study") or {}
    limits = report.get("limits")
    if limits is not None and limits.get("depth") is not None:
        limits = dict(limits, depth=dict(limits["depth"], plan_id=_short(limits["depth"].get("plan_id"), 64)))
    result.update({"schema": "research-status-summary-v1", "runtime": report.get("runtime"),
                   "profile": _short(report.get("profile"), 24), "stage": _short(study.get("stage"), 32),
                   "status": _short(study.get("status"), 32), "waiting": _short(study.get("waiting"), 160),
                   "display_strings_may_be_truncated": True,
                   "obligations": _obligations(report.get("obligations", [])),
                   "preparation_obligations": _obligations(preparation.get("obligations", [])),
                   "counts": report.get("counts"), "resources": report.get("resources", {}),
                   "limits": limits,
                   "round": report.get("round"), "evaluation": report.get("evaluation"),
                   "bundle_drift": _summarize_bundle_drift(report.get("bundle_drift")),
                   "grand_challenge": _summarize_grand_challenge(report.get("grand_challenge"))})
    if report.get("orchestration") == "problem_creation":
        result.update(candidates=report["candidates"], slate_id=_short(report["slate_id"], 64))
    slots = report.get("slot_outcomes") or report.get("review", {}).get("slot_outcomes")
    if slots:
        result["review_slots"] = dict(occupied=slots["occupied"],
            **{key: len(slots[key]) for key in ("complete", "incomplete", "pending")})
    assignments = report.get('review', {}).get('assignments', {})
    series = {}
    for identifier, assignment in assignments.items():
        progress = assignment.get('review_parts')
        if progress is not None:
            series_id = progress['series_assignment_id']
            previous = series.get(series_id)
            if previous is None or progress['recorded_revision'] > previous['recorded_revision']:
                series[series_id] = dict(progress, assignment_id=identifier)
    if series:
        examples = []
        for progress in list(series.values())[:20]:
            example = {key: progress[key] for key in
                       ('kind', 'turn', 'units_carded', 'units_remaining', 'complete')}
            example.update({key: _short(progress[key], 64) for key in
                            ('assignment_id', 'series_assignment_id', 'adapter')})
            if 'blocked' in progress:
                example['blocked'] = _short(progress['blocked'], 64)
            examples.append(example)
        result['review_parts'] = dict(count=len(series), examples=examples,
                                      omitted=max(0, len(series) - len(examples)))
    result["source_deferrals"] = dict(Counter(item["status"] for item in report.get("source_deferrals", [])))
    result["deferred_obligations"] = _obligations(report.get("deferred_obligations", []))
    if "probes" in report:
        result["probes"] = report["probes"]
    if "provisional_searches" in report:
        result["provisional_searches"] = {"count": len(report["provisional_searches"])}
    # Keep depth amounts and counts visible when they take room previously available to optional criterion previews.
    challenge = result["grand_challenge"]
    while ((limits or {}).get("depth") or "probes" in report or "provisional_searches" in report
           or result["bundle_drift"]) and (challenge and challenge["criterion_ids"]
           and len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")) > 16 * 1024):
        challenge["criterion_ids"].pop()
        challenge["omitted_criterion_ids"] += 1
    drift = result["bundle_drift"]
    if drift:
        while len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")) > 16 * 1024:
            longest = max(drift["files"], key=lambda item: len(item["path"]))
            if len(longest["path"]) <= 32:
                break
            longest["path"] = longest["path"][:-16]
            longest["path_truncated"] = True
        hint = result.get("next_hint") or {}
        displays = [(result, "waiting", 32), (hint, "explanation", 32)]
        displays.extend((hint, key, 16) for key in ("version_id", "work_id", "collection_id", "unit_id"))
        for container, key, minimum in displays:
            while (isinstance(container.get(key), str) and len(container[key]) > minimum
                   and len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")) > 16 * 1024):
                container[key] = container[key][:max(minimum, len(container[key]) - 16)]
                if container is hint and key not in hint["truncated_fields"]:
                    hint["truncated_fields"].append(key)
    progress = result.get('review_parts')
    while (progress and progress['examples']
           and len(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')) > 16 * 1024):
        progress['examples'].pop()
        progress['omitted'] += 1
    return result


def current_obligations(report):
    """The obligations `next` follows: the preparation stage's while it has any, else readiness."""
    preparation = report.get("preparation", {})
    if preparation.get("advisory"):
        return report["obligations"]
    return preparation.get("obligations") or report["obligations"]


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
