"""One phase-specific scientific decision state for managed research boundaries.

Support, consequence, context isolation and publication authority are distinct.
The records bind actual independent outputs and exact evidence; no numerical
rating or a nonempty author justification establishes scientific approval.
"""

import copy
import json

from .artifacts import ArtifactStore
from .claim_dependencies import verify_record_dependency
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, normalized_text, prepared_mutation, strings, text
from . import strategy
from . import problem_rubric


CONTINUE_CHECKS_V1 = ("impact", "demand", "novelty_risk", "feasibility", "distinctness", "continuity", "grand_challenge")
CONTINUE_CHECKS = CONTINUE_CHECKS_V1 + ("q1_venue",)
STOP_CHECKS = ("stop", "demand")
ALL_CHECKS = CONTINUE_CHECKS + ("stop",)
PROBLEM_CHECKS = ("problem_statement", "problem_openness", "problem_value", "problem_citations", "stop")
BOUNDARIES = ("target", "cycle", "write", "publication", "round")
ARTIFACT_DISPOSITIONS = ("none", "result_report", "prepare_manuscript", "deliver_under_instruction")
_LIMITS = ("These are mechanical evidence, attribution and transition checks. Scientific truth, importance, "
           "semantic materiality and the meaning of a user instruction require substantive review. "
           "No ordinal score establishes sufficient contribution or native mathematical proof acceptance.")


def _obligations(values):
    return sorted({digest(value): value for value in values}.values(), key=lambda value: (value["code"], digest(value)))


def _state(required, obligations, decision=None, *, legacy_status=None):
    obligations = _obligations(obligations)
    content = {"required": required, "ready": not obligations, "obligations": obligations, "decision": decision,
               "legacy_status": legacy_status, "mechanical_only": True, "scientific_truth_certified": False,
               "limits": _LIMITS}
    return dict(content, digest=digest(content))


def _response(payload):
    return {key: value for key, value in payload.items() if key not in ("id", "dossier_id", "assignment_id")}


def _validate_checks(records, artifacts, values, expected):
    checks = strategy.items(values, "Independent decision assurances")
    seen = set()
    for check in checks:
        fields(check, ("kind", "status", "reason", "evidence"), code="invalid_value_review")
        problem = set(expected) == set(PROBLEM_CHECKS)
        strategy.choice(check["kind"], PROBLEM_CHECKS if problem else ALL_CHECKS, "Independent assurance", "invalid_value_review")
        strategy.choice(check["status"], ("passed", "failed", "not_assessed") if problem else ("passed", "failed", "unresolved"), "Assurance status", "invalid_value_review")
        text(check["reason"], "Independent assurance reasoning")
        strategy.evidence(records, artifacts, check["evidence"])
        if check["kind"] in seen:
            raise ResearchError("invalid_value_review", "Assess each independent assurance once")
        seen.add(check["kind"])
    if seen != set(expected):
        raise ResearchError("invalid_value_review", "Address all phase-required independent assurances", {"required": list(expected)})


def _validate_slate_potential(value, packet):
    """A problem slate review assesses no statement and grades the potential of exactly the packet's candidates."""
    if set(value["bar_ids"]) != set(packet["bar_ids"]):
        raise ResearchError("invalid_value_review", "Assess both bars of the slate packet", {"bar_ids": packet["bar_ids"]})
    if any(check["status"] != "not_assessed" for check in value["assurances"]):
        raise ResearchError("invalid_value_review", "This role grades candidate potential; leave every realized-problem assurance not_assessed.")
    candidate_ids = sorted(candidate["id"] for candidate in packet["candidates"])
    potential = value["rubric_potential"]
    if not isinstance(potential, dict) or sorted(potential) != candidate_ids:
        raise ResearchError("invalid_value_review", "Grade the potential of exactly the packet's candidates",
                            {"candidate_ids": candidate_ids})
    for candidate_id, grades in sorted(potential.items()):
        fields(grades, ("significance", "priority"), code="invalid_value_review")
        try:
            problem_rubric.validate_grades(grades, potential=True)
        except ResearchError as error:
            raise ResearchError("invalid_value_review", error.message, {"candidate_id": candidate_id}) from error


def _validate_value(records, artifacts, value, stage, dossier, historical=(), *, expected_checks=ALL_CHECKS, problem=None,
                    problem_objections=(), problem_rubric_required=True, slate_packet=None):
    rubric_required = problem is not None and stage == "result" and problem_rubric_required
    potential_required = problem is not None and stage == "slate"
    fields(value, ("status", "consequence", "bar_ids", "objective_adequacy", "method_adequacy", "reason", "evidence",
                   "work_items", "assurances", "objection_findings") +
           (("rubric", "rubric_evidence") if rubric_required else ()) +
           (("rubric_potential",) if potential_required else ()), code="invalid_value_review")
    if rubric_required:
        problem_rubric.validate_grades(value["rubric"])
        fields(value["rubric_evidence"], problem_rubric.ITEMS, code="invalid_value_review")
        for item in problem_rubric.ITEMS:
            text(value["rubric_evidence"][item], "Rubric item evidence", code="invalid_value_review")
    strategy.choice(value["status"], ("sufficient", "insufficient", "unresolved"), "Scientific consequence", "invalid_value_review")
    for key in ("consequence", "reason"):
        text(value[key], "Independent " + key)
    for key in ("objective_adequacy", "method_adequacy"):
        strategy.choice(value[key], ("adequate", "inadequate", "unresolved", "not_assessed"), key, "invalid_value_review")
    strings(value["bar_ids"], "Independently recorded bar reviews")
    strategy.evidence(records, artifacts, value["evidence"])
    if stage == "bar":
        if value["bar_ids"] or any(value[key] != "not_assessed" for key in ("objective_adequacy", "method_adequacy")):
            raise ResearchError("invalid_value_review", "The initial bar cannot assess the withheld author proposal")
        expected = ()
    else:
        expected = expected_checks
        if len(value["bar_ids"]) < 2:
            raise ResearchError("review_bar_pending", "Assess the recorded independent bars before accepting the slate or result")
        for identifier in value["bar_ids"]:
            bar = strategy.get_record(records, "value_review", identifier)
            if problem is not None:
                from .review_protocol import validate_problem_bars
                if bar.get("subject", {}).get("kind") != "problem" or bar["payload"]["stage"] != "bar":
                    raise ResearchError("review_bar_mismatch", "Use independent bars for this problem field")
                validate_problem_bars(records, artifacts, [bar])
                continue
            bar_dossier = strategy.get_record(records, "strategy_dossier", bar["dossier_id"])
            if bar["payload"]["stage"] != "bar" or bar_dossier["payload"]["intent_id"] != dossier["payload"]["intent_id"]:
                raise ResearchError("review_bar_mismatch", "Use bar findings for the current actual intent")
    _validate_checks(records, artifacts, value["assurances"], expected)
    if potential_required:
        _validate_slate_potential(value, slate_packet)
    work_ids = set()
    for finding in strategy.items(value["work_items"], "Independent work-item classifications"):
        fields(finding, ("id", "classification", "status", "reason", "evidence"), code="invalid_value_review")
        strategy.get_record(records, "research_work_item", finding["id"])
        strategy.choice(finding["classification"], strategy.CLASSIFICATIONS, "Independent obligation class", "invalid_value_review")
        strategy.choice(finding["status"], ("accepted", "rejected", "unresolved"), "Work finding", "invalid_value_review")
        text(finding["reason"], "Work-item reasoning")
        strategy.evidence(records, artifacts, finding["evidence"])
        if finding["id"] in work_ids:
            raise ResearchError("invalid_value_review", "Assess each work item once")
        work_ids.add(finding["id"])
    if work_ids != (set() if stage == "bar" or problem is not None else set(dossier["payload"]["work_items"])):
        raise ResearchError("review_work_items_incomplete", "Independently assess every dossier work item")
    prior_ids = set()
    for finding in strategy.items(value["objection_findings"], "Independent prior-objection findings"):
        fields(finding, ("id", "status", "reason", "evidence"), code="invalid_value_review")
        text(finding["id"], "Prior objection ID")
        strategy.choice(finding["status"], ("resolved", "continuing", "contested"), "Prior objection status", "invalid_value_review")
        text(finding["reason"], "Prior objection reasoning")
        strategy.evidence(records, artifacts, finding["evidence"])
        if finding["id"] in prior_ids:
            raise ResearchError("invalid_value_review", "Assess each prior objection once")
        prior_ids.add(finding["id"])
    material = None if problem is not None else dossier["payload"]["material_change"]
    expected_ids = set() if stage == "bar" or material is None else {item["id"] for item in material["prior_objections"]}
    expected_ids.update(objection["id"] for review in historical for objection in review["payload"]["objections"])
    expected_ids.update(objection["id"] for objection in problem_objections)
    if prior_ids != expected_ids:
        raise ResearchError("review_objection_history_incomplete", "Assess each recorded predecessor objection")


def record_value_review(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("id", "dossier_id", "assignment_id", "stage", "support", "value", "objections", "limitations"), ("source_requests", "reassessment"), code="invalid_value_review")
        old = strategy.existing_record(records, "value_review", value)
        if old is not None:
            return [], old
        problem = records["workspace"]["problem"] if strategy.find_orchestration(records) == "problem_creation" else None
        dossier = None if problem is not None else strategy.get_record(records, "strategy_dossier", value["dossier_id"])
        intent = None if problem is not None else strategy.get_record(records, "research_intent", dossier["payload"]["intent_id"])
        strategy.choice(value["stage"], ("bar", "slate", "result"), "Review phase", "invalid_value_review")
        from .review_protocol import assignment_state, validate_assignment_inventories
        validate_assignment_inventories(records, artifacts, value["assignment_id"])
        assignment = assignment_state(records, artifacts, value["assignment_id"])
        if assignment.get("output") is None:
            raise ResearchError("review_output_pending", "Preserve failed attempts; a missing output is neither approval nor a negative scientific result")
        if assignment.get("dossier_id") != value["dossier_id"] or assignment.get("stage") != value["stage"]:
            raise ResearchError("value_review_assignment_mismatch", "The review must concern the assigned dossier and role")
        if assignment["output"] != _response(value):
            raise ResearchError("value_review_output_mismatch", "Record the exact observed independent output without author substitutions")
        if value["stage"] in ("bar", "slate", "result"):
            from .bar_sources import pending
            saved_assignment = records["review_assignment"][value["assignment_id"]]
            packet = json.loads(artifacts.read(saved_assignment["packet"]))
            if pending(packet, assignment["output"]):
                raise ResearchError("review_sources_pending", "Retain the observed request turn; resolve its source deliveries before recording a final phase assessment")
        from . import review_context_repair
        saved_assignment = records["review_assignment"][value["assignment_id"]]
        if problem is not None:
            from .review_protocol import build_problem_subject
            if (value["dossier_id"] is not None or saved_assignment.get("subject", {}).get("kind") != "problem"
                    or (value["stage"] != "bar" and saved_assignment["subject"] != build_problem_subject(records, value["stage"]))):
                raise ResearchError("value_review_assignment_mismatch", "Record the exact assigned problem statement and role")
            if normalized_text(saved_assignment["author_id"]) not in {normalized_text(author) for author in problem["authors"]}:
                raise ResearchError("problem_author_unknown", "Use an assignment by one of the immutable problem authors")
        review_context_repair.validate_repair_origin(records, artifacts, saved_assignment)
        review_context_repair.validate_reassessment(records, artifacts, saved_assignment, assignment["output"])
        inherited = review_context_repair.reassessment_reviews(records, saved_assignment)
        refreshed = (review_context_repair.collect_problem_review_history(records, saved_assignment)
                     if problem is not None else [])
        # A refreshed bar, slate or result review supersedes its earlier review, so it inherits that review's material
        # objections and gives a finding on each; until it is recorded, the earlier objections stay current.
        historical = inherited + refreshed
        if value["stage"] != "bar" and problem is not None:
            historical.extend(review for review in review_context_repair.historical_reviews(records, None, value["stage"])
                              if review.get("subject") == saved_assignment["subject"])
            historical.extend(review_context_repair.historical_reviews(records, None, "bar"))
        elif value["stage"] != "bar":
            for previous_dossier in records.get("strategy_dossier", {}).values():
                if previous_dossier["payload"]["intent_id"] == dossier["payload"]["intent_id"]:
                    historical.extend(review_context_repair.historical_reviews(records, previous_dossier["id"], "bar"))
            historical.extend(review_context_repair.historical_reviews(records, value["dossier_id"], value["stage"]))
        inherited_ids = {review["id"] for review in inherited + refreshed}
        reviewer = normalized_text(assignment["reviewer_id"])
        authors = problem["authors"] if problem is not None else intent["payload"]["authors"]
        if reviewer in {normalized_text(author) for author in authors}:
            raise ResearchError("review_not_independent", "The author cannot supply the independent assessment")
        if any(saved["assignment_id"] == value["assignment_id"] for saved in records.get("value_review", {}).values()):
            raise ResearchError("value_review_duplicate", "One observed output supplies one immutable review record")
        if any(saved["dossier_id"] == value["dossier_id"] and saved["payload"]["stage"] == value["stage"]
               and normalized_text(saved["reviewer_id"]) == reviewer and saved["id"] not in inherited_ids
               and (problem is None or value["stage"] == "bar" or saved.get("subject") == saved_assignment["subject"])
               for saved in records.get("value_review", {}).values()):
            raise ResearchError("value_review_duplicate", "A reviewer cannot replace its initial finding by another sample")
        from .review_protocol import is_previous_prompt
        expected_checks = (PROBLEM_CHECKS if problem is not None else
                           CONTINUE_CHECKS_V1 + ("stop",) if is_previous_prompt(saved_assignment) else ALL_CHECKS)
        _validate_value(records, artifacts, value["value"], value["stage"], dossier, historical,
                        expected_checks=expected_checks, problem=problem,
                        problem_rubric_required=not is_previous_prompt(saved_assignment),
                        problem_objections=(json.loads(artifacts.read(saved_assignment["packet"])).get("material_objections", [])
                                             if problem is not None and value["stage"] == "result" else ()),
                        slate_packet=packet)
        if problem is not None and value["stage"] == "result":
            undelivered = find_undelivered_bar_ids(records, artifacts, value["assignment_id"], value["value"]["bar_ids"])
            if undelivered:
                raise ResearchError("review_bar_mismatch", "Assess only bars delivered to this reviewer", {"bar_ids": undelivered})
        strings(value["limitations"], "Independent assessment limitations", nonempty=True)
        # A slate objection names the candidates it concerns; an empty list concerns the slate as a whole.
        slate_candidate_ids = ({candidate["id"] for candidate in packet["candidates"]}
                               if problem is not None and value["stage"] == "slate" else None)
        objection_ids = set()
        for objection in strategy.items(value["objections"], "Material objections"):
            fields(objection, ("id", "claim", "reason", "evidence", "resolution_condition") +
                   (("candidate_ids",) if slate_candidate_ids is not None else ()), code="invalid_value_review")
            for key in ("id", "claim", "reason", "resolution_condition"):
                text(objection[key], "Material objection " + key)
            strategy.evidence(records, artifacts, objection["evidence"])
            if slate_candidate_ids is not None:
                strings(objection["candidate_ids"], "Candidates of a slate objection", code="invalid_value_review")
                if not set(objection["candidate_ids"]) <= slate_candidate_ids:
                    raise ResearchError("invalid_value_review", "Name only candidates of the slate packet",
                                        {"objection_id": objection["id"], "candidate_ids": sorted(slate_candidate_ids)})
            if objection["id"] in objection_ids:
                raise ResearchError("invalid_value_review", "Material objection identities must be unique")
            objection_ids.add(objection["id"])
            for saved in records.get("value_review", {}).values():
                for earlier in saved["payload"]["objections"]:
                    if earlier["id"] == objection["id"] and earlier["claim"] != objection["claim"]:
                        raise ResearchError("research_objection_identity_conflict", "A stable objection ID cannot name a different contested claim")
        changes, support = [], None
        if problem is not None:
            if value["support"] is not None:
                raise ResearchError("invalid_value_review", "A problem review supplies problem assurances, not native proof acceptance")
        elif value["stage"] == "result":
            if dossier["payload"]["phase"] not in ("result", "post_measurement"):
                raise ResearchError("value_review_phase_mismatch", "A result review requires a result dossier")
            if not isinstance(value["support"], dict):
                raise ResearchError("value_review_support_missing", "A result assessment contains the existing native support checks first")
            recorded_assignment = records["review_assignment"][value["assignment_id"]]
            packet = json.loads(artifacts.read(recorded_assignment["packet"]))
            if value["support"].get("assessor") != packet.get("assessor") or normalized_text(value["support"]["assessor"]["id"]) != reviewer:
                raise ResearchError("value_review_support_identity", "Use the assigned native assessor and its actual provenance")
            from .publication_scope import has_publication_scope, prepare_combined_scoped_support
            scoped = has_publication_scope(records)
            supplied_target = value["support"].get("target")
            if scoped != isinstance(supplied_target, dict) or (scoped and supplied_target.get("kind") != "source_limited_manuscript"):
                raise ResearchError("research_support_scope_mismatch", "Review the currently selected exact manuscript scope without replacing the full scientific objective")
            if scoped:
                changes, support = prepare_combined_scoped_support(records, artifacts, value["support"],
                    revision=expected_revision + 1, request_id=request_id, assignment_id=value["assignment_id"])
            else:
                from .development import prepare_combined_support
                changes, support = prepare_combined_support(records, artifacts, value["support"],
                    revision=expected_revision + 1, request_id=request_id, assignment_id=value["assignment_id"])
        elif value["support"] is not None:
            raise ResearchError("invalid_value_review", "The bar and proposed slate cannot fabricate a completed result review")
        record = {"id": value["id"], "payload": value, "digest": digest(value), "recorded_revision": expected_revision + 1,
                  "dossier_id": value["dossier_id"], "dossier_digest": None if dossier is None else dossier["digest"], "assignment_id": value["assignment_id"],
                  "reviewer_id": assignment["reviewer_id"], "packet_digest": assignment["packet_digest"], "prompt_hash": assignment["prompt_hash"],
                  "output_digest": assignment["output_digest"], "support": support, "independence_at_recording": assignment["context_status"]}
        if problem is not None:
            record["subject"] = saved_assignment["subject"]
        changes.append(immutable_record(records, "value_review", value["id"], record))
        if support is not None:
            changes.append(immutable_record(records, "research_support", value["id"], {
                "review_id": value["id"], "readiness_review_id": value["support"]["id"], "candidate_digest": value["support"]["candidate_digest"],
                "assessment": support, "assignment_id": value["assignment_id"],
                "scientific_target_digest": support.get("scientific_target_digest")}))
        return changes, record

    return prepared_mutation(store, "research.value_review", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _pair(records, identifiers, dossier, stage):
    strings(identifiers, "Independent review pair", code="research_review_pair_required")
    if len(identifiers) != 2 or len(set(identifiers)) != 2:
        raise ResearchError("research_review_pair_required", "A major scientific decision requires two distinct independent assessments")
    reviews = [strategy.get_record(records, "value_review", identifier) for identifier in identifiers]
    if len({normalized_text(review["reviewer_id"]) for review in reviews}) != 2:
        raise ResearchError("research_review_pair_required", "A single reviewer cannot supply both independent findings")
    from .review_context_repair import slot_id
    assignments = [strategy.get_record(records, "review_assignment", review["assignment_id"]) for review in reviews]
    if len({slot_id(records, assignment) for assignment in assignments}) != 2:
        raise ResearchError("research_review_pair_required", "The two independent findings must occupy different original review slots")
    for review in reviews:
        reviewed_dossier = (None if dossier.get("kind") == "problem" else
                            strategy.get_record(records, "strategy_dossier", review["dossier_id"]))
        if review["payload"]["stage"] != stage:
            raise ResearchError("research_review_phase_mismatch", "Use the assessment for the current decision phase")
        if dossier.get("kind") == "problem":
            if (review.get("subject", {}).get("kind") != "problem"
                    or (stage != "bar" and review.get("subject") != dossier)):
                raise ResearchError("research_review_stale", "Use reviews of the exact current problem statement")
            continue
        if ((stage == "bar" and reviewed_dossier["payload"]["intent_id"] != dossier["payload"]["intent_id"])
                or (stage != "bar" and review["dossier_id"] != dossier["id"])):
            raise ResearchError("research_review_stale", "Use exact current dossier findings and bars for the actual intent")
    return reviews


def validate_source_impact(records, artifacts, dossier, impact):
    fields(impact, ("sources", "aspects"), code="invalid_source_impact")
    fields(impact["aspects"], ("nearest_work", "scope", "transfer", "bar"), code="invalid_source_impact")
    for key, value in impact["aspects"].items():
        strategy.choice(value, ("unchanged", "material", "unresolved"), "Source impact on " + key, "invalid_source_impact")
    seen, material = set(), []
    for source in strategy.items(impact["sources"], "New or changed sources"):
        fields(source, ("source_id", "impact", "reason", "evidence"), code="invalid_source_impact")
        text(source["source_id"], "Changed source ID")
        strategy.choice(source["impact"], ("unrelated", "material", "unresolved"), "Scientific source impact", "invalid_source_impact")
        text(source["reason"], "Source impact reason")
        strategy.evidence(records, artifacts, source["evidence"])
        if source["source_id"] in seen:
            raise ResearchError("invalid_source_impact", "Declare each source change once")
        seen.add(source["source_id"])
        if source["impact"] != "unrelated":
            material.append(source["source_id"])
    delta = set(strategy.source_delta(records, dossier))
    if seen != delta:
        raise ResearchError("research_source_impact_required", "Declare the relevance of every source change since prospective assessment",
                            {"required": sorted(delta), "declared": sorted(seen)})
    if material or any(value != "unchanged" for value in impact["aspects"].values()):
        raise ResearchError("research_source_reassessment_required", "Refresh affected comparisons, scope, transfer or bar before using the old approval",
                            {"sources": material})


def record_source_impact(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("id", "decision_id", "source_impact"), code="invalid_source_impact")
        old = strategy.existing_record(records, "research_source_impact", value)
        if old is not None:
            return [], old
        decision = strategy.get_record(records, "research_decision", value["decision_id"])
        dossier = strategy.get_record(records, "strategy_dossier", decision["payload"]["dossier_id"])
        validate_source_impact(records, artifacts, dossier, value["source_impact"])
        saved = {"id": value["id"], "payload": value, "digest": digest(value), "recorded_revision": expected_revision + 1,
                 "source_snapshot": strategy.source_snapshot(records)}
        return [immutable_record(records, "research_source_impact", value["id"], saved)], saved

    return prepared_mutation(store, "research.source_impact", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _current_impact(records, decision):
    snapshot = strategy.source_snapshot(records)
    matching = [(saved["recorded_revision"], saved["payload"]["source_impact"])
                for saved in records.get("research_source_impact", {}).values()
                if saved["payload"]["decision_id"] == decision["id"] and saved["source_snapshot"] == snapshot]
    matching.extend((saved["committed_revision"], saved["source_impact"])
                    for saved in records.get("research_commitment", {}).values()
                    if saved["decision_id"] == decision["id"] and saved["source_snapshot"] == snapshot)
    return max(matching, key=lambda item: item[0])[1] if matching else decision["payload"]["source_impact"]


def _all_objections(records, dossier, reviews):
    found = {}
    for review in reviews:
        for objection in review["payload"]["objections"]:
            found[objection["id"]] = objection
    prior = dossier["payload"]["material_change"]
    if prior is not None:
        ids = {entry["id"] for entry in prior["prior_objections"]}
        for review in records.get("value_review", {}).values():
            for objection in review["payload"]["objections"]:
                if objection["id"] in ids:
                    found[objection["id"]] = objection
    return found


def _accept_support_digests(records, artifacts, values):
    """Recognize old and projected names for the same current support subject."""
    from .claim_dependencies import uses_claim_dependencies
    if not uses_claim_dependencies(records):
        return False
    from .development import _Context
    from .publication_scope import has_publication_scope, _load_scope_snapshot
    if has_publication_scope(records):
        context, candidate, _, current, _, _ = _load_scope_snapshot(records, artifacts)
        return candidate is not None and all(context.verify_candidate_digest(value, current, scoped=True) for value in values)
    context = _Context(records, artifacts)
    candidate, _ = context.build_candidate()
    return candidate is not None and all(context.verify_candidate_digest(value, candidate) for value in values)


def _decision_structure(records, artifacts, value):
    fields(value, ("id", "dossier_id", "phase", "action", "artifact_disposition", "goal_status", "review_ids", "bar_review_ids",
                   "objections", "work_items", "source_impact", "reason", "remaining_objective", "next_action", "goal_evidence",
                   "support_candidate_digest"), code="invalid_research_decision")
    text(value["id"], "Decision ID")
    dossier = strategy.get_record(records, "strategy_dossier", value["dossier_id"])
    intent = strategy.get_record(records, "research_intent", dossier["payload"]["intent_id"])
    strategy.choice(value["phase"], strategy.PHASES, "Decision phase", "invalid_research_decision")
    if value["phase"] != dossier["payload"]["phase"]:
        raise ResearchError("research_decision_phase_mismatch", "The decision must use its dossier's exact phase")
    strategy.choice(value["action"], strategy.ACTIONS, "Scientific action", "invalid_research_decision")
    strategy.choice(value["artifact_disposition"], ARTIFACT_DISPOSITIONS, "Artifact disposition", "invalid_research_decision")
    strategy.choice(value["goal_status"], ("open", "achieved", "resource_limited"), "Full-objective status", "invalid_research_decision")
    text(value["reason"], "Scientific decision reason")
    strings(value["remaining_objective"], "Remaining full objective")
    if value["next_action"] is not None:
        text(value["next_action"], "Next action under the continuing objective")
    if value["goal_status"] != "achieved" and (not value["remaining_objective"] or value["next_action"] is None):
        raise ResearchError("research_goal_status_missing", "An unfinished objective keeps its obligations and next action explicit")
    if value["goal_status"] == "achieved":
        if value["action"] == "close_branch" or value["remaining_objective"] or intent["payload"]["branch"]["relation"] != "full":
            raise ResearchError("research_goal_not_achieved", "Closing a branch or completing a special case does not complete the full objective")
        strategy.evidence(records, artifacts, value["goal_evidence"])
    else:
        strategy.evidence(records, artifacts, value["goal_evidence"], required=False)
    reviews = _pair(records, value["review_ids"], dossier, "slate" if value["phase"] == "prospective" else "result")
    bars = _pair(records, value["bar_review_ids"], dossier, "bar")
    for review in reviews:
        if not set(value["bar_review_ids"]) <= set(review["payload"]["value"]["bar_ids"]):
            raise ResearchError("research_bar_unassessed", "Both reviewers must assess the independently established bar")
    objections, dispositions = _all_objections(records, dossier, reviews + bars), set()
    for item in strategy.items(value["objections"], "Material objection dispositions"):
        fields(item, ("id", "disposition", "reason", "evidence", "adjudication_id"), code="invalid_research_decision")
        text(item["id"], "Objection ID")
        strategy.choice(item["disposition"], ("accepted", "resolved", "unresolved"), "Objection disposition", "invalid_research_decision")
        text(item["reason"], "Objection disposition reason")
        strategy.evidence(records, artifacts, item["evidence"], required=item["disposition"] == "resolved")
        if item["id"] in dispositions:
            raise ResearchError("invalid_research_decision", "Dispose of every objection exactly once")
        dispositions.add(item["id"])
        if item["adjudication_id"] is not None:
            adjudication = strategy.get_record(records, "review_adjudication", item["adjudication_id"])
            if adjudication["objection_id"] != item["id"]:
                raise ResearchError("research_adjudication_mismatch", "Adjudication is bound to the specific objection")
    if set(objections) != dispositions:
        raise ResearchError("research_objection_disposition_missing", "Retain a disposition for every material objection", {"required": sorted(objections)})
    work_ids = set()
    for item in strategy.items(value["work_items"], "Critical-work dispositions"):
        fields(item, ("id", "disposition", "reason", "evidence"), code="invalid_research_decision")
        strategy.get_record(records, "research_work_item", item["id"])
        strategy.choice(item["disposition"], ("test", "obstruction", "alternative", "resolved", "withdrawn", "optional"), "Work disposition", "invalid_research_decision")
        text(item["reason"], "Work disposition reason")
        strategy.evidence(records, artifacts, item["evidence"], required=item["disposition"] not in ("test", "optional"))
        if item["id"] in work_ids:
            raise ResearchError("invalid_research_decision", "Dispose of each persistent work item once")
        work_ids.add(item["id"])
    if work_ids != set(dossier["payload"]["work_items"]):
        raise ResearchError("research_work_disposition_missing", "The decision must carry every dossier work item")
    fields(value["source_impact"], ("sources", "aspects"), code="invalid_source_impact")
    if value["phase"] == "prospective" and value["support_candidate_digest"] is not None:
        raise ResearchError("invalid_research_decision", "A prospective decision cannot fabricate a completed result candidate")
    if value["phase"] != "prospective":
        text(value["support_candidate_digest"], "Exact result candidate digest")
        subjects = [value["support_candidate_digest"]] + [review["payload"]["support"]["candidate_digest"] for review in reviews]
        if len(set(subjects)) != 1 and not _accept_support_digests(records, artifacts, subjects):
            raise ResearchError("research_support_mismatch", "Both support sections must assess the exact same result candidate")
    return dossier, intent, reviews, bars


def _independence(records, artifacts, reviews):
    from .review_protocol import assignment_state
    obligations = []
    for review in reviews:
        state = assignment_state(records, artifacts, review["assignment_id"])
        obligations.extend(state["obligations"])
        if state.get("output") != _response(review["payload"]) or state.get("output_digest") != review["output_digest"]:
            obligations.append(obligation("research_review_binding_stale", "Reassess the exact recorded independent output.", review_id=review["id"]))
    return obligations


def _collect_problem_reviews(records, stage, subject):
    from .review_context_repair import historical_reviews, slot_id
    historical = {review['id'] for review in historical_reviews(records, None, stage)}
    slots = {}
    for review in sorted(records.get('value_review', {}).values(),
                         key=lambda saved: (saved.get('recorded_revision', 0), saved['id'])):
        if (review['payload']['stage'] != stage or review['id'] in historical
                or review.get('subject', {}).get('kind') != 'problem'
                or (stage != 'bar' and review['subject'] != subject)):
            continue
        assignment = records.get('review_assignment', {}).get(review['assignment_id'])
        if assignment is not None:
            slots[slot_id(records, assignment)] = review['id']
    return list(slots.values())


def _assess_problem_coverage(records, artifacts, reviews, expected, evidence_digest):
    from .direction_reviews import collect_observed_packets
    from .direction_sources import find_delivered_readings, has_attachment
    from .evaluation import Evaluation
    from .review_protocol import collect_references
    evaluation = Evaluation.of(records, artifacts)
    readings = [records['reading'][item['id']] for item in expected['readings']]
    obligations = []
    for review in reviews:
        packets = [packet for _, packet in collect_observed_packets(records, evaluation, review['assignment_id'])]
        current = [packet for packet in packets if packet.get('problem_evidence_digest') == evidence_digest]
        current_evidence = [packet.get('problem_evidence', {}) for packet in current]
        missing = [key for key in ('claim', 'openness', 'openness_searches', 'value', 'assessment', 'assessment_evidence', 'attempt', 'results', 'citations', 'readings', 'computations', 'formal_checks')
                   if not any(key in packet_evidence and packet_evidence[key] == expected[key] for packet_evidence in current_evidence)]
        delivered = find_delivered_readings(records, evaluation, readings,
            [dict(packet, direction_evidence=packet_evidence) for packet, packet_evidence in zip(current, current_evidence)])
        missing_readings = sorted(reading['id'] for reading in readings if reading['id'] not in delivered)
        assessment_artifacts = [reference for reference in expected['assessment_evidence'] if 'kind' not in reference]
        assessment_artifacts.extend(reference['record']['artifact'] for reference in expected['assessment_evidence']
                                    if reference.get('kind') == 'record' and reference['record_kind'] == 'local_artifact')
        missing_artifacts = sorted({reference['sha256'] for reference in collect_references(
            [assessment_artifacts, expected['openness_searches'], expected['computations'], expected['formal_checks']])
            if not any(has_attachment(evaluation, packet, reference) for packet in current)})
        if missing or missing_readings or missing_artifacts:
            obligations.append(obligation('problem_evidence_unassessed',
                'Obtain both observed results covering the current problem evidence and every delivered source and output.',
                review_id=review['id'], missing=missing, reading_ids=missing_readings, artifact_digests=missing_artifacts))
    return obligations


def state_objection(review, objection):
    """One material version of an objection: its finding content bound to the review and revision that stated it."""
    finding = {key: objection[key] for key in ('claim', 'reason', 'evidence', 'resolution_condition', 'candidate_ids')
               if key in objection}
    return dict(objection, review_id=review['id'], recorded_revision=review['recorded_revision'],
                material_version=digest({'finding': finding, 'review_id': review['id'],
                                         'recorded_revision': review['recorded_revision']}))


def _collect_superseded_review_ids(records, artifacts):
    """The problem reviews that a later review of the same stage, reviewer, session and slot superseded; a result is
    superseded by a later result given on the same problem evidence. Only the latest review of a lineage holds its
    objection versions current; the statements and findings of a superseded review are history."""
    from .review_context_repair import slot_id
    lineages = {}
    for review in records.get('value_review', {}).values():
        if review.get('subject', {}).get('kind') != 'problem':
            continue
        assignment = records['review_assignment'][review['assignment_id']]
        key = (review['payload']['stage'], normalized_text(assignment['reviewer_id']), assignment['session_id'],
               slot_id(records, assignment))
        if review['payload']['stage'] == 'result':
            key += (read_assignment_packet(records, artifacts, assignment['id']).get('problem_evidence_digest'),)
        lineages.setdefault(key, []).append(review)
    return {review['id'] for members in lineages.values()
            for review in sorted(members, key=lambda row: row['recorded_revision'])[:-1]}


def _fold_objection_versions(reviews, superseded):
    """Fold the reviews' stated objections and continuing or contested findings, in recorded order, into the versions
    of each objection id, and name the versions that a review not superseded holds.

    A stated objection is a new version. A finding compares with the version it continues: the latest held version,
    or the latest version when none is held. A finding with another reason or evidence is a new version that keeps
    the claim, the resolution condition and the candidates of that version; a finding about an objection that no
    review states is a version without a claim. A review holds the versions it states and the versions its findings
    continue, unless a later review of its lineage superseded it."""
    versions, latest, current, held = {}, {}, {}, set()
    for review in sorted(reviews, key=lambda row: (row['recorded_revision'], row['id'])):
        holds = review['id'] not in superseded
        stated = set()
        for objection in review['payload']['objections']:
            version = latest[objection['id']] = state_objection(review, objection)
            versions.setdefault(objection['id'], []).append(version)
            stated.add(objection['id'])
            if holds:
                current[objection['id']] = version
                held.add(version['material_version'])
        for finding in review['payload']['value']['objection_findings']:
            identifier = finding['id']
            if finding['status'] not in ('continuing', 'contested') or identifier in stated:
                continue
            version = current.get(identifier, latest.get(identifier))
            if version is None or (finding['reason'], finding['evidence']) != (version['reason'], version['evidence']):
                kept = {key: version[key] for key in ('claim', 'resolution_condition', 'candidate_ids')
                        if version is not None and key in version}
                version = latest[identifier] = state_objection(review, dict(kept, **{key: finding[key] for key in ('id', 'reason', 'evidence')}))
                versions.setdefault(identifier, []).append(version)
            if holds:
                current[identifier] = version
                held.add(version['material_version'])
    return versions, held


def collect_problem_post_objections(records, artifacts, reviews):
    """The material objections that bind the current statement: the findings of the given bar and result reviews'
    observed ancestries and of the slate reviews bound to candidates. Per objection id, the latest held version that
    concerns the statement, with the other versions in recorded order as its history."""
    from .direction_reviews import collect_observed_packets
    from .problem_candidates import collect_bound_slate_reviews, project_slate_objection
    identifiers = {assignment['id'] for review in reviews
                   for assignment, _ in collect_observed_packets(records, artifacts, review['assignment_id'])}
    sources = {saved['id']: saved for saved in records.get('value_review', {}).values()
               if saved['assignment_id'] in identifiers and saved.get('subject', {}).get('kind') == 'problem'}
    sources.update((review['id'], review) for review in collect_bound_slate_reviews(records, artifacts))
    versions, held = _fold_objection_versions(sources.values(), _collect_superseded_review_ids(records, artifacts))
    selected = []
    for identifier, rows in sorted(versions.items()):
        # A slate version binds the statement only through the candidates it names.
        projected = (project_slate_objection(records, row) if 'candidate_ids' in row else row for row in rows)
        bound = [row for row in projected if row is not None]
        current = [row for row in bound if row['material_version'] in held]
        if current:
            selected.append(dict(current[-1], history=[row for row in bound
                                                       if row['material_version'] != current[-1]['material_version']]))
    return selected


def read_assignment_packet(records, artifacts, assignment_id):
    return json.loads(artifacts.read(records['review_assignment'][assignment_id]['packet']))


def find_undelivered_adjudication_keys(records, artifacts, assignment_id, *, rubric=False, version_evidence=None):
    """The required deliveries that the observed adjudicator packet lacks: the original bar calibrations of its
    adjudicated pair, for a rubric shortfall the item's definition and threshold, and for a material objection the
    given stating review and evidence of the version. A cited reference that the packet lacks, or that cannot be
    delivered, is named as version_evidence:<record_kind>:<id> or version_evidence:<sha256>."""
    from .review_protocol import resolve_original_calibrations
    assignment = records['review_assignment'][assignment_id]
    packet = read_assignment_packet(records, artifacts, assignment_id)
    expected = dict(version_evidence or {},
                    bar_calibrations=resolve_original_calibrations(records, artifacts, assignment['context']['review_ids']))
    entries = expected.pop('version_evidence', None)
    undelivered = [key for key, value in expected.items() if packet.get(key) != value]
    if entries is not None:
        delivered = packet.get('version_evidence') or []
        # An undelivered entry names its reference; a record entry its kind and id; an artifact its sha256.
        missing = ['version_evidence:' + (entry['identity'] if entry.get('kind') == 'undelivered' else
                                          entry['record_kind'] + ':' + entry['id'] if entry.get('kind') == 'record' else
                                          entry['sha256'])
                   for entry in entries if entry.get('kind') == 'undelivered' or entry not in delivered]
        undelivered.extend(missing or (['version_evidence'] if delivered != entries else []))
    if rubric and not packet.get('rubric_definition'):
        undelivered.append('rubric_definition')
    return sorted(undelivered)


def find_undelivered_bar_ids(records, artifacts, assignment_id, bar_ids):
    """The named bars whose content no observed packet of the reviewer's lineage delivered."""
    from .direction_reviews import collect_observed_packets
    delivered = {bar['id'] for _, packet in collect_observed_packets(records, artifacts, assignment_id)
                 for bar in packet.get('bars', [])}
    return sorted(set(bar_ids) - delivered)


def assess_problem_objections(records, artifacts, subject, bars, reviews, evidence_digest):
    """Apply the common evidenced-resolution rule without a strategic dossier. A result finding or a focused
    adjudication resolves only the material version that its observed packet delivered."""
    from .review_protocol import describe_version_evidence
    delivered = {review['id']: {row['id']: row.get('material_version') for row in
                                read_assignment_packet(records, artifacts, review['assignment_id']).get('material_objections', [])}
                 for review in reviews}
    dispositions = []
    for objection in collect_problem_post_objections(records, artifacts, bars + reviews):
        version = objection['material_version']
        adjudication = max((saved for saved in records.get('review_adjudication', {}).values()
            if saved['objection_id'] == objection['id'] and saved.get('subject') == subject
            and saved.get('problem_evidence_digest') == evidence_digest
            and read_assignment_packet(records, artifacts, saved['assignment_id'])['objection'].get('material_version') == version
            and not find_undelivered_adjudication_keys(records, artifacts, saved['assignment_id'],
                                                       version_evidence=describe_version_evidence(records, artifacts, objection))),
            key=lambda saved: saved['recorded_revision'], default=None)
        given = len(reviews) == 2 and all(delivered[review['id']].get(objection['id']) == version for review in reviews)
        dispositions.append({'id': objection['id'], 'disposition': 'resolved' if given else 'continuing',
                             'adjudication_id': None if adjudication is None else adjudication['id']})
    return _objection_obligations(records, artifacts, subject, {'payload': {'objections': dispositions}}, reviews,
                                 problem_evidence_digest=evidence_digest)


def assess_problem_reviews(records, artifacts, review_ids=None, bar_review_ids=None, *, check_evidence=True):
    """Assess fixed independent pairs and their observed delivered problem evidence."""
    from .problem_claims import describe_problem_evidence, compute_problem_evidence_digest
    from .review_protocol import build_problem_subject, validate_problem_bars
    subject = build_problem_subject(records)
    review_ids = _collect_problem_reviews(records, 'result', subject) if review_ids is None else review_ids
    bar_review_ids = _collect_problem_reviews(records, 'bar', subject) if bar_review_ids is None else bar_review_ids
    obligations, pairs = [], {}
    for stage, identifiers, code in (('bar', bar_review_ids, 'problem_bar_pending'),
                                      ('result', review_ids, 'problem_review_pair_required')):
        try:
            pairs[stage] = _pair(records, identifiers, subject, stage)
        except ResearchError as error:
            obligations.append(obligation(code, str(error), review_ids=identifiers))
            pairs[stage] = []
        obligations.extend(_independence(records, artifacts, pairs[stage]))
    bars, reviews = pairs['bar'], pairs['result']
    if bars:
        try:
            validate_problem_bars(records, artifacts, bars)
        except ResearchError as error:
            obligations.append(obligation(error.code, str(error), **error.details))
    authors = {normalized_text(author) for author in records['workspace']['problem']['authors']}
    for review in bars + reviews:
        assignment = records['review_assignment'][review['assignment_id']]
        if normalized_text(review['reviewer_id']) in authors:
            obligations.append(obligation('review_not_independent', 'A problem author cannot supply its independent review.', review_id=review['id']))
        if normalized_text(assignment['author_id']) not in authors:
            obligations.append(obligation('problem_author_unknown', 'The assignment author is not an immutable problem author.', assignment_id=assignment['id']))
    for review in reviews:
        findings = review['payload']['value']
        checks = findings.get('assurances', [])
        pending = [kind for kind in PROBLEM_CHECKS
                   if len([check for check in checks if check.get('kind') == kind and check.get('status') == 'passed']) != 1]
        if (pending or len(checks) != len(PROBLEM_CHECKS) or findings.get('status') != 'sufficient'):
            obligations.append(obligation('problem_assurance_pending', 'Every problem assurance must pass against the recorded evidence.',
                review_id=review['id'], assurances=pending))
        if set(findings['bar_ids']) != set(bar_review_ids):
            obligations.append(obligation('review_bar_mismatch', 'Both problem results must assess the selected independent bars.', review_id=review['id']))
        undelivered = find_undelivered_bar_ids(records, artifacts, review['assignment_id'], findings['bar_ids'])
        if undelivered:
            obligations.append(obligation('review_bar_mismatch', 'Each problem result must receive the bars it assesses.',
                                          review_id=review['id'], bar_ids=undelivered))
    expected, evidence_digest = None, None
    if reviews and check_evidence:
        expected = describe_problem_evidence(records, artifacts)
        evidence_digest = compute_problem_evidence_digest(records, artifacts)
        obligations.extend(_assess_problem_coverage(records, artifacts, reviews, expected, evidence_digest))
    obligations.extend(assess_rubric_reviews(records, artifacts, reviews, evidence_digest))
    obligations.extend(assess_problem_objections(records, artifacts, subject, bars, reviews, evidence_digest))
    return {'ready': not obligations, 'obligations': _obligations(obligations),
            'review_ids': review_ids, 'bar_review_ids': bar_review_ids, 'problem_evidence_digest': evidence_digest}


def assess_rubric_reviews(records, artifacts, reviews, evidence_digest):
    """Require current rubric results and independently adjudicate named shortfalls."""
    from .review_protocol import PROBLEM_PROTOCOL, adjudication_state
    obligations = []
    for review in reviews:
        assignment = records['review_assignment'][review['assignment_id']]
        template = assignment['prompt']['template_id']
        findings = review['payload']['value']
        if template not in (PROBLEM_PROTOCOL + '/result', PROBLEM_PROTOCOL + '/result/context-repair'):
            obligations.append(obligation('problem_rubric_review_required', 'Obtain a version 2 problem result with rubric grades.',
                                          review_id=review['id']))
            continue
        pending = []
        for shortfall in problem_rubric.assess_grades(findings['rubric']):
            identifier = problem_rubric.rubric_objection_id(review['id'], shortfall['item'])
            saved = max((row for row in records.get('review_adjudication', {}).values()
                         if row['objection_id'] == identifier and row.get('subject') == review['subject']
                         and evidence_digest is not None and row.get('problem_evidence_digest') == evidence_digest
                         and not find_undelivered_adjudication_keys(records, artifacts, row['assignment_id'], rubric=True)),
                        key=lambda row: row['recorded_revision'], default=None)
            resolved = False
            if saved is not None:
                state = adjudication_state(records, artifacts, saved['id'])
                resolved = state['ready'] and state['disposition'] == 'not_upheld'
                if resolved:
                    try:
                        strategy.evidence(records, artifacts, saved['evidence'])
                    except ResearchError as error:
                        resolved = False
                        obligations.append(obligation(error.code, str(error), review_id=review['id'],
                                                      objection_id=identifier, adjudication_id=saved['id']))
            if not resolved:
                pending.append(shortfall)
        if pending:
            obligations.append(obligation('problem_rubric_below_threshold', 'Repair or independently adjudicate each rubric shortfall.',
                                          source='review', review_id=review['id'], shortfalls=pending))
    return obligations


def _objection_obligations(records, artifacts, dossier, decision, reviews, *, problem_evidence_digest=None):
    from .review_protocol import adjudication_state
    obligations = []
    for item in decision["payload"]["objections"]:
        resolved = False
        adjudication_obligations = []
        if item["adjudication_id"] is not None:
            adjudication = adjudication_state(records, artifacts, item["adjudication_id"])
            if dossier.get("kind") == "problem":
                adjudication_obligations.extend(adjudication["obligations"])
            else:
                obligations.extend(adjudication["obligations"])
            saved = adjudication["adjudication"]
            if saved is None:
                scope_matches = False
            elif dossier.get("kind") == "problem":
                scope_matches = (saved.get("subject") == dossier
                                 and saved.get("problem_evidence_digest") == problem_evidence_digest)
            else:
                scope_matches = saved["dossier_id"] == dossier["id"]
            resolved = adjudication["ready"] and adjudication["disposition"] == "not_upheld" and scope_matches
            if resolved and dossier.get("kind") == "problem":
                try:
                    strategy.evidence(records, artifacts, adjudication["adjudication"]["evidence"])
                except ResearchError as error:
                    resolved = False
                    adjudication_obligations.append(obligation(error.code, str(error), objection_id=item["id"]))
        if not resolved and item["disposition"] == "resolved":
            findings = [{finding["id"]: finding for finding in review["payload"]["value"]["objection_findings"]} for review in reviews]
            resolved = all(item["id"] in finding and finding[item["id"]]["status"] == "resolved" for finding in findings)
            if any(item["id"] in {objection["id"] for objection in review["payload"]["objections"]} for review in reviews):
                resolved = False
            if resolved and dossier.get("kind") == "problem":
                for finding in findings:
                    try:
                        strategy.evidence(records, artifacts, finding[item["id"]]["evidence"])
                    except ResearchError as error:
                        resolved = False
                        obligations.append(obligation(error.code, str(error), objection_id=item["id"]))
        if not resolved:
            obligations.extend(adjudication_obligations)
            obligations.append(obligation("research_objection_unresolved", "Resolve the specific material objection with evidence; another positive opinion cannot waive it.", objection_id=item["id"]))
    return obligations


def _intent_lineage(records, intent_id):
    lineage = set()
    while intent_id is not None and intent_id not in lineage:
        lineage.add(intent_id)
        intent = records.get("research_intent", {}).get(intent_id)
        intent_id = intent["payload"]["previous"] if intent is not None else None
    return lineage


def _work_obligations(records, dossier, intent, action, reviews):
    obligations = []
    current = [records["research_work_item"][selection["id"]] for selection in records.get("work_item_selection", {}).values()]
    current = [item for item in current if item["payload"]["intent_id"] in _intent_lineage(records, intent["id"])]
    dossier_ids = set(dossier["payload"]["work_items"])
    for item in current:
        value = item["payload"]
        if item["id"] not in dossier_ids:
            obligations.append(obligation("research_work_item_stale", "Assess the current revision of every retained work item.", work_item_id=item["id"]))
            continue
        findings = [next((finding for finding in review["payload"]["value"]["work_items"] if finding["id"] == item["id"]), None) for review in reviews]
        if any(finding is None or finding["status"] != "accepted" for finding in findings) or len({finding["classification"] for finding in findings if finding}) != 1:
            obligations.append(obligation("research_work_classification_unresolved", "Resolve the independent classification and treatment of this work item.", work_item_id=item["id"]))
            continue
        classification = findings[0]["classification"]
        unresolved = value["execution_state"] != "validated" or value["disposition"] != "resolved"
        retained = set(value["claim_ids"]) & {claim["id"] for claim in dossier["payload"]["claims"]}
        withdrawn = value["disposition"] == "withdrawn" and not retained
        if classification == "validity" and unresolved and not withdrawn and action in ("develop_manuscript", "deliver_requested"):
            obligations.append(obligation("research_validity_work_unresolved", "Resolve the validity obligation or withdraw the claim and downstream uses.", work_item_id=item["id"]))
        if classification == "consequence_critical" and unresolved and action == "develop_manuscript" and dossier["payload"]["reconsideration"] is None:
            obligations.append(obligation("research_consequence_work_unresolved", "Planned or deferred work does not establish the promised consequence.", work_item_id=item["id"]))
    return obligations


def _support_obligations(records, artifacts, value, reviews):
    obligations = []
    if value["phase"] == "prospective":
        return obligations
    from .development import _Context, _review
    try:
        from .publication_scope import has_publication_scope, assess_manuscript_readiness, _load_scope_snapshot, _validate_scoped_review
        if has_publication_scope(records):
            report = assess_manuscript_readiness(records, artifacts)
            obligations.extend(report["obligations"])
            context, candidate, contract, exact, _, _ = _load_scope_snapshot(records, artifacts)
            if not context.verify_candidate_digest(value["support_candidate_digest"], exact, scoped=True, record_id=value["id"]):
                obligations.append(obligation("research_support_stale", "The exact supported manuscript scope changed after its combined assessment."))
            for review in reviews:
                _validate_scoped_review(context, review["payload"]["support"], candidate, contract, exact)
            if value["goal_status"] == "achieved" and not report["objective_complete"]:
                obligations.append(obligation("research_goal_not_achieved", "A supported scoped delivery retains the incomplete full scientific objective."))
            return obligations
        context = _Context(records, artifacts)
        candidate, pending = context.build_candidate()
        obligations.extend(pending)
        if candidate is None:
            return obligations
        if not context.verify_candidate_digest(value["support_candidate_digest"], candidate, record_id=value["id"]):
            obligations.append(obligation("research_support_stale", "The result changed after its combined independent support/value assessment."))
        for review in reviews:
            report = _review(context, review["payload"]["support"], candidate)
            obligations.extend(report["obligations"])
    except ResearchError as error:
        obligations.append(obligation(error.code, error.message, **(error.details or {})))
    return obligations


def _decision_obligations(records, artifacts, decision, boundary, *, source_impact=None):
    value = decision["payload"]
    dossier, intent, reviews, bars = _decision_structure(records, artifacts, value)
    obligations = []
    if strategy.current_intent(records) is None or strategy.current_intent(records)["id"] != intent["id"]:
        obligations.append(obligation("research_intent_stale", "Assess the user's current actual instruction."))
    current = strategy.current_dossier(records)
    if (decision["dossier_digest"] != dossier["digest"]
            or current is None or current["id"] != dossier["id"]):
        obligations.append(obligation("research_dossier_stale", "Assess the current strategy dossier before a new commitment; earlier findings remain historical evidence."))
    for dependency in dossier["payload"]["dependencies"]:
        if dependency["kind"] == "source":
            changed = strategy.source_snapshot(records).get(dependency["id"]) != dossier["source_snapshot"].get(dependency["id"])
        else:
            try:
                changed = not verify_record_dependency(records, dependency["kind"], dependency["id"], dependency["digest"])
            except ResearchError as error:
                obligations.append(obligation(error.code, error.message, **(error.details or {})))
                continue
        if changed:
            obligations.append(obligation("research_dependency_stale", "A decisive bound source, comparison or result changed.", dependency=dependency))
    try:
        validate_source_impact(records, artifacts, dossier, _current_impact(records, decision) if source_impact is None else source_impact)
    except ResearchError as error:
        obligations.append(obligation(error.code, error.message, **(error.details or {})))
    obligations.extend(_independence(records, artifacts, reviews + bars))
    action = value["action"]
    permitted = {"target": ("investigate", "pivot"), "cycle": ("investigate", "pivot"),
                 "write": ("develop_manuscript", "deliver_requested"), "publication": ("develop_manuscript", "deliver_requested"),
                 "round": strategy.ACTIONS}
    if action not in permitted[boundary]:
        obligations.append(obligation("research_action_incompatible", "The recorded scientific action does not authorize this boundary.", action=action, boundary=boundary))
    if boundary == "publication" and value["phase"] != "post_measurement":
        obligations.append(obligation("research_post_measurement_decision_required", "Assess the measured current manuscript and its reviewer demands before publication planning."))
    values = [review["payload"]["value"] for review in reviews]
    sufficient = all(finding["status"] == "sufficient" for finding in values)
    if action in ("investigate", "pivot", "develop_manuscript"):
        if not sufficient:
            obligations.append(obligation("research_consequence_insufficient", "Both independent assessments must support a worthwhile consequence for this intent."))
        if any(finding["objective_adequacy"] != "adequate" for finding in values):
            obligations.append(obligation("research_objective_unresolved", "Independently assess whether the objective addresses the user's aim."))
        if any(finding["method_adequacy"] != "adequate" for finding in values):
            obligations.append(obligation("research_method_unresolved", "Establish the method's ability to resolve the stated question or bounded adequacy probe."))
    version_two = decision.get("gate_version") == 2
    if version_two and action in ("investigate", "pivot"):
        from .claim_minimums import describe_direction
        direction = describe_direction(records, artifacts)
        obligations.extend(direction["obligations"])
        if direction["obligations"] or decision.get("direction_evidence_digest") != direction["digest"]:
            obligations.append(obligation("direction_evidence_unassessed", "Reassess the changed decisive evidence before authorizing this direction.",
                decision_id=decision["id"], assessed_digest=decision.get("direction_evidence_digest"), current_digest=direction["digest"]))
        else:
            from .direction_reviews import assess_direction_reviews
            obligations.extend(assess_direction_reviews(records, artifacts, dossier, reviews, direction))
    continuing = action in ("investigate", "pivot", "develop_manuscript")
    checks = (CONTINUE_CHECKS if version_two else CONTINUE_CHECKS_V1) if continuing else STOP_CHECKS
    for review in reviews:
        assurances = review["payload"]["value"]["assurances"]
        if version_two and continuing and not any(check["kind"] == "q1_venue" for check in assurances):
            obligations.append(obligation("q1_venue_missing", "Assess Q1 venue standards in both independent reviews.", review_id=review["id"]))
        for check in assurances:
            if check["kind"] in checks and check["status"] != "passed":
                obligations.append(obligation("research_assurance_pending", "Resolve the independent scientific assurance.", review_id=review["id"], check=check["kind"], status=check["status"]))
    objection_pending = _objection_obligations(records, artifacts, dossier, decision, reviews)
    # Investigation and closure can retain a material scientific dispute. An
    # author-only claim of resolving that dispute cannot authorize a test either.
    if action in ("develop_manuscript",) or any(item["disposition"] == "resolved" for item in value["objections"]):
        obligations.extend(objection_pending)
    obligations.extend(_work_obligations(records, dossier, intent, action, reviews))
    candidate = next(candidate for candidate in dossier["payload"]["candidates"] if candidate["id"] == dossier["payload"]["selected_candidate"])
    if action in ("investigate", "pivot"):
        if dossier["payload"]["tranche"] is None:
            obligations.append(obligation("research_tranche_missing", "Bound the deciding question, resources and stopping signals before further work."))
        if candidate["transfer"]["kind"] != "direct" and candidate["transfer"]["plan"] is None:
            obligations.append(obligation("research_transfer_plan_missing", "A target-level commitment needs a transfer plan or an explicit adequacy probe."))
    if action in ("develop_manuscript", "deliver_requested"):
        if value["phase"] == "prospective":
            obligations.append(obligation("research_result_required", "A proposed consequence does not establish a supported result."))
        else:
            obligations.extend(_support_obligations(records, artifacts, value, reviews))
        if action == "develop_manuscript" and value["artifact_disposition"] != "prepare_manuscript":
            obligations.append(obligation("research_artifact_disposition_mismatch", "Manuscript development must identify its exact artifact disposition."))
        if action == "deliver_requested":
            publication = intent["payload"]["publication"]
            covered = intent["payload"]["task_kind"] == "specified_delivery" or (publication["required"] and publication["endpoint"] != "none")
            if not covered:
                obligations.append(obligation("research_delivery_authority_missing", "Identify an existing instruction covering this supported delivery; mere permission is not a requirement."))
            if publication["quality_condition"] is not None and not sufficient:
                obligations.append(obligation("research_publication_condition_unmet", "Continue authorized work toward the explicit quality condition."))
            if value["artifact_disposition"] != "deliver_under_instruction":
                obligations.append(obligation("research_artifact_disposition_mismatch", "Requested delivery retains its actual instruction and limited scientific standing."))
    if value["goal_status"] == "achieved" and (not sufficient or any(review["support"] is None or not review["support"]["ready"] for review in reviews)):
        obligations.append(obligation("research_goal_not_achieved", "Full-objective success requires supported results and the independently assessed consequence."))
    if value["phase"] == "post_measurement":
        from .publication import find_selected_bundle
        from .predictions import measurement_state
        bundle = find_selected_bundle(records)
        if boundary == "write":
            # Revising the manuscript can reuse the assessed scientific result.
            # Recheck its original measurement; publication still binds the new bundle.
            bundle = next((saved for saved in records.get("publication_bundle", {}).values()
                           if saved["digest"] == dossier["payload"]["bundle_digest"]), None)
        if bundle is None or bundle["digest"] != dossier["payload"]["bundle_digest"]:
            obligations.append(obligation("research_bundle_stale", "The post-measurement decision binds the exact current bundle."))
        else:
            measurement = measurement_state(records, artifacts, bundle)
            obligations.extend(measurement["independence_obligations"])
            if not measurement.get("complete"):
                obligations.append(obligation("manuscript_measurement_missing", "Complete the current bundle measurement before its research decision."))
            from .contribution import find_analysis
            if find_analysis(records, bundle["digest"]) is None:
                obligations.append(obligation("contribution_analysis_missing", "Retain all current reviewer-demand dispositions before the post-measurement decision."))
    return obligations


def record_research_decision(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        old = strategy.existing_record(records, "research_decision", value)
        if old is not None:
            return [], old
        dossier, intent, reviews, bars = _decision_structure(records, artifacts, value)
        fingerprint = digest({key: val for key, val in value.items() if key not in ("id", "reason")})
        if any(saved["fingerprint"] == fingerprint for saved in records.get("research_decision", {}).values()):
            raise ResearchError("research_decision_duplicate", "Reuse the existing decision findings for unchanged evidence and intent")
        record = {"id": value["id"], "payload": value, "digest": digest(value), "fingerprint": fingerprint,
                  "recorded_revision": expected_revision + 1, "dossier_digest": dossier["digest"], "intent_digest": intent["digest"], "gate_version": 2}
        if value["action"] in ("investigate", "pivot"):
            from .claim_minimums import describe_direction
            from .direction_reviews import assess_direction_reviews
            direction = describe_direction(records, artifacts)
            if direction["obligations"]:
                raise ResearchError("direction_minimum_unmet", "The direction evidence must exist before the reviews that judge it", {"obligations": direction["obligations"]})
            coverage = assess_direction_reviews(records, artifacts, dossier, reviews, direction)
            if coverage:
                raise ResearchError("direction_evidence_unassessed", "Both observed reviews must cover the decisive evidence and claim before accepting the direction", {"obligations": coverage})
            record["direction_evidence_digest"] = direction["digest"]
        # Other rejected and unresolved findings remain inspectable. Only the
        # current derived state, never this historical report, permits work.
        record["assessment"] = _state(True, _decision_obligations(records, artifacts, record, "round"))
        return [immutable_record(records, "research_decision", value["id"], record),
                ("strategy_selection", "decision", {"id": value["id"]})], record

    return prepared_mutation(store, "research.decision", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def decision_state(records, artifacts, boundary, *, decision_id=None):
    strategy.choice(boundary, BOUNDARIES, "Research decision boundary", "invalid_research_decision")
    if not strategy.managed_research(records):
        return _state(False, [])
    intent = strategy.current_intent(records)
    legacy = "legacy_unassessed" if not records.get("research_intent") else None
    if intent is None:
        return _state(True, [obligation("research_intent_missing", "Record the actual full instruction and its delivery, quality and resource authority.")], legacy_status=legacy)
    dossier = strategy.current_dossier(records)
    if dossier is None:
        return _state(True, [obligation("strategy_dossier_missing", "Compare the objective, candidate consequences and deciding methods before commitment.")])
    decision = strategy.selected(records, "decision", "research_decision") if decision_id is None else records.get("research_decision", {}).get(decision_id)
    if decision is None:
        return _state(True, [obligation("research_decision_missing", "Record the canonical phase-specific decision after both independent assessments.")])
    try:
        obligations = _decision_obligations(records, artifacts, decision, boundary)
    except ResearchError as error:
        obligations = [obligation(error.code, error.message, **(error.details or {}))]
    return _state(True, obligations, decision)


def prepare_objective_commitment(records, artifacts, target, *, decision_id, source_impact, revision):
    """Return commitment changes to append to the native target transaction."""
    if not strategy.managed_research(records):
        return [], None
    decision = (strategy.selected(records, "decision", "research_decision") if decision_id is None
                else records.get("research_decision", {}).get(decision_id))
    if decision is None:
        raise ResearchError("research_decision_required", "Assess the exact proposed objective before committing it",
                            {"obligations": decision_state(records, artifacts, "target")["obligations"]})
    dossier = strategy.get_record(records, "strategy_dossier", decision["payload"]["dossier_id"])
    proposal = dossier["objective_binding"]
    if (not isinstance(target, dict) or proposal["kind"] != "proposed" or target.get("statement") != proposal["statement"]):
        raise ResearchError("research_objective_commitment_mismatch", "Commit exactly the approved proposed statement and scope; a new native ID alone is harmless")
    from .claim_minimums import assess_direction
    minimum = assess_direction(records, artifacts)
    if minimum:
        raise ResearchError("direction_minimum_unmet", "Complete the direction evidence before objective commitment", {"obligations": minimum})
    impact = _current_impact(records, decision) if source_impact is None else source_impact
    validate_source_impact(records, artifacts, dossier, impact)
    if decision.get("gate_version") != 2:
        # Historical tranche authority is unchanged. A new target commitment
        # still needs observed coverage of its current decisive evidence.
        from .claim_minimums import describe_direction
        from .direction_reviews import assess_direction_reviews
        reviews = [strategy.get_record(records, "value_review", identifier) for identifier in decision["payload"]["review_ids"]]
        coverage = assess_direction_reviews(records, artifacts, dossier, reviews, describe_direction(records, artifacts))
        if coverage:
            raise ResearchError("direction_evidence_unassessed", "Both observed reviews must cover the decisive evidence and claim before objective commitment", {"obligations": coverage})
    obligations = _decision_obligations(records, artifacts, decision, "target", source_impact=impact)
    if obligations:
        code = "direction_evidence_unassessed" if any(item["code"] == "direction_evidence_unassessed" for item in obligations) else "research_decision_required"
        raise ResearchError(code, "Resolve the current prospective decision findings before objective commitment", {"obligations": obligations})
    earlier = [saved for saved in records.get("research_commitment", {}).values() if saved["proposal"]["id"] == proposal["id"]]
    if earlier:
        if earlier[0]["objective"] != target:
            raise ResearchError("research_objective_commitment_mismatch", "A proposal is consumed by one exact native objective")
        return [], earlier[0]
    commitment = {"id": target["id"], "objective": target, "proposal": proposal, "decision_id": decision["id"],
                  "decision_digest": decision["digest"], "dossier_id": dossier["id"], "source_snapshot": strategy.source_snapshot(records),
                  "prospective_source_snapshot": dossier["source_snapshot"], "source_impact": impact, "committed_revision": revision}
    return [immutable_record(records, "research_commitment", target["id"], commitment)], commitment


def _intent_budget(records, dossier, unit, requested):
    intent = strategy.get_record(records, "research_intent", dossier["payload"]["intent_id"])
    constraints = [resource for resource in intent["payload"]["resources"] if resource["unit"] == unit and resource["limit"] is not None]
    if not constraints:
        return None
    lineage, consumed = _intent_lineage(records, intent["id"]), 0
    for admission in records.get("execution_admission", {}).values():
        link = admission.get("research_decision") or {}
        prior_decision = records.get("research_decision", {}).get(link.get("id"))
        if prior_decision is None:
            continue
        prior_dossier = records["strategy_dossier"][prior_decision["payload"]["dossier_id"]]
        tranche = prior_dossier["payload"]["tranche"]
        if prior_dossier["payload"]["intent_id"] in lineage and tranche is not None and tranche["limit"]["unit"] == unit:
            consumed += admission["reserved_units"]
    limit = min(resource["limit"] for resource in constraints)
    if consumed + requested > limit:
        raise ResearchError("research_intent_budget_exceeded", "The planned work exceeds the actual authorized resource contract in this same unit",
                            {"unit": unit, "limit": limit, "reserved": consumed, "requested": requested})
    return {"unit": unit, "limit": limit, "reserved": consumed, "requested": requested}


def validate_cycle_decision(records, artifacts, plan_payload, decision_record, *, reserved_units=None):
    """Bind each cycle and resource reservation to the reviewed bounded question."""
    if decision_record is None:
        if strategy.managed_research(records):
            raise ResearchError("research_decision_required", "A managed cycle requires its reviewed decision")
        return None
    dossier = strategy.get_record(records, "strategy_dossier", decision_record["payload"]["dossier_id"])
    candidate = next(candidate for candidate in dossier["payload"]["candidates"] if candidate["id"] == dossier["payload"]["selected_candidate"])
    tranche = dossier["payload"]["tranche"]
    if (decision_record["payload"]["action"] not in ("investigate", "pivot") or tranche is None
            or plan_payload.get("candidate_id") != candidate["id"] or plan_payload.get("decision_id") != decision_record["id"]):
        raise ResearchError("research_cycle_mismatch", "The cycle must identify the actual reviewed candidate and investigation decision")
    question = normalized_text(plan_payload.get("question", ""))
    methods = {normalized_text(plan_payload.get("distinguishing_test", "")), normalized_text(plan_payload.get("strategy", {}).get("mechanism", ""))}
    test = candidate["next_test"]
    direct = question == normalized_text(test["question"]) and normalized_text(test["method"]) in methods
    prerequisite = any(question == normalized_text(item["description"]) and normalized_text(item["exit_condition"]) in methods
                       for item in test["prerequisites"])
    if not direct and not prerequisite:
        raise ResearchError("research_cycle_mismatch", "Execute the deciding test or its explicitly bounded prerequisite, not an unrelated convenient cycle")
    limits = plan_payload.get("resource_limits", {})
    if limits.get("unit") != tranche["limit"]["unit"] or limits.get("max_units", 0) > tranche["limit"]["amount"]:
        raise ResearchError("research_cycle_resource_mismatch", "The cycle must fit the reviewed tranche in the same resource unit")
    tranche_decisions = set()
    for prior in records.get("research_decision", {}).values():
        prior_dossier = records.get("strategy_dossier", {}).get(prior["payload"]["dossier_id"])
        prior_tranche = prior_dossier["payload"]["tranche"] if prior_dossier else None
        if prior_tranche is not None and prior_tranche["id"] == tranche["id"]:
            tranche_decisions.add(prior["id"])
    units = sum(admission["reserved_units"] for admission in records.get("execution_admission", {}).values()
                if (admission.get("research_decision") or {}).get("id") in tranche_decisions)
    if reserved_units is not None:
        strategy.number(reserved_units, "Reserved tranche units", positive=True)
    budget = _intent_budget(records, dossier, limits["unit"], limits.get("max_units", 0) if reserved_units is None else reserved_units)
    if units >= tranche["limit"]["amount"] or (reserved_units is not None and units + reserved_units > tranche["limit"]["amount"]):
        raise ResearchError("research_tranche_exhausted", "The same authorized tranche cannot be reset by a new cycle or plan ID")
    for plan in records.get("cycle_plan", {}).values():
        if (plan.get("research_decision") or {}).get("id") not in tranche_decisions:
            continue
        cycle = records.get("cycle", {}).get(plan["id"], {})
        if cycle.get("status") in ("complete", "failed", "budget_paused"):
            prior_payload = plan.get("payload", {})
            prior_question = normalized_text(prior_payload.get("question", ""))
            prior_methods = {normalized_text(prior_payload.get("distinguishing_test", "")),
                             normalized_text(prior_payload.get("strategy", {}).get("mechanism", ""))}
            bounded_prerequisite = any(prior_question == normalized_text(item["description"])
                                       and normalized_text(item["exit_condition"]) in prior_methods
                                       for item in test["prerequisites"])
            assessment = records.get("cycle_assessment", {}).get(cycle.get("assessment_id"), {})
            completed_prerequisite = (cycle.get("status") == "complete" and bounded_prerequisite
                                      and assessment.get("assessment", {}).get("complete") is True
                                      and assessment.get("payload", {}).get("cycle_id") == plan["id"]
                                      and all(finding.get("status") == "not_observed"
                                              for finding in assessment.get("payload", {}).get("failures", [])))
            if completed_prerequisite and question != prior_question:
                continue
            raise ResearchError("research_tranche_reassessment_required", "Compare the preceding result or failure with the tranche purpose before another commitment",
                                {"cycle_id": plan["id"], "status": cycle["status"]})
    return {"decision_id": decision_record["id"], "decision_digest": decision_record["digest"], "dossier_id": dossier["id"],
            "candidate_id": candidate["id"], "tranche_id": tranche["id"], "reserved_units": units,
            "limit": tranche["limit"], "prerequisite": prerequisite, "intent_budget": budget}
