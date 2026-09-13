"""Development rounds: the paper-level loop and its gate.

A round decision (`round`) closes the current round on the exact publication bundle and
either proposes the next round's goal (`continue`) or stops. An independent round review
judges it (`round-review`). `round-admit` opens the proposed round, applies a widened
objective and records the round's opening state; `round-assess` judges a finished round
against its goal. `round_state` is the gate between `evaluate` and either a new round or
`deposit`. Blind review scores and predictions are derived and reported; no rule here
reads them.
"""

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .workspace import strict_json
from . import development, literature, predictions, principles, publication, resources


DECISIONS = ("continue", "stop")
DIRECTIONS = ("vertical", "horizontal")
CANDIDATE_DISPOSITIONS = ("pursue", "rejected", "deferred")
CRITERION_KINDS = ("claim", "scope")
CHECKS_CONTINUE = ("impact", "demand", "novelty_risk", "feasibility", "distinctness", "continuity")
CHECKS_STOP = ("stop", "demand")
VERDICTS = ("approved", "not_approved", "unresolved")
CHECK_STATUSES = ("passed", "failed", "unresolved")
OPENING_PURPOSES = literature.SEARCH_PURPOSES + literature.DEVELOPMENT_PURPOSES
_ERROR = "invalid_round"


def _text(value, name):
    return text(value, name, code=_ERROR)


def _fields(value, required, optional=()):
    fields(value, required, optional, code=_ERROR)


def _strings(value, name, nonempty=False):
    return strings(value, name, nonempty=nonempty, code=_ERROR)


def _choice(value, choices, name):
    if value not in choices:
        raise ResearchError(_ERROR, name + " must be one of: " + ", ".join(choices))


def _items(value, name, nonempty=True):
    if not isinstance(value, list) or nonempty and not value:
        raise ResearchError(_ERROR, name + " must be an array" + (" with at least one entry" if nonempty else ""))
    return value


def _normalized(value):
    return " ".join(value.casefold().split())


def admissions(records):
    return sorted(records.get("round_admission", {}).values(), key=lambda a: a["number"])


def latest_admission(records):
    items = admissions(records)
    return items[-1] if items else None


def assessment_for(records, admission_id):
    """The round's latest assessment, or None; a round assessed on a superseded bundle is assessed again on the current one."""
    return max((a for a in records.get("round_assessment", {}).values() if a["round_id"] == admission_id),
               key=lambda a: a["assessed_revision"], default=None)


def closing_round_assessments(records):
    """The cycle assessments recorded after the latest admission (every assessment before any round was admitted): the closing round's."""
    latest = latest_admission(records)
    admitted_revision = latest["admitted_revision"] if latest is not None else 0
    return {identifier: assessment for identifier, assessment in records.get("cycle_assessment", {}).items()
            if assessment["assessed_revision"] > admitted_revision}


def current_number(records):
    latest = latest_admission(records)
    return latest["number"] if latest else 1


def active_round(records):
    """The admitted round without an assessment, or None."""
    latest = latest_admission(records)
    return latest if latest is not None and assessment_for(records, latest["id"]) is None else None


def fresh_searches(records, admission):
    """The development purposes whose selected search is not the one the round opened with: {purpose: search_id}."""
    selection = records.get("search_selection", {})
    fresh = {}
    for purpose in literature.DEVELOPMENT_PURPOSES:
        selected = selection.get("research:" + purpose, {}).get("search_id")
        if selected is not None and selected != admission["opening"]["search_selection"][purpose]:
            fresh[purpose] = selected
    return fresh


def count_full_readings(records):
    """How many readings of full depth the store holds; abstract and passage readings are not counted."""
    return sum(1 for r in records.get("reading", {}).values() if r["depth"] == "fulltext")


def has_round_exemplar(records, admission):
    """Whether a research full-text requirement with the exemplar purpose was recorded after the round opened."""
    return any(r["profile"] == "research" and r["purpose"] == "exemplar" and r["id"] not in admission["opening"]["requirement_ids"]
               for r in records.get("fulltext_requirement", {}).values())


class _RoundEvidence:
    """Source and result evidence as the development layer validates it, plus manuscript-review evidence."""

    def __init__(self, context, bundle):
        self.records, self.bundle = context.records, bundle
        self.development = development._Evidence(context)
        self.items = {}

    def many(self, values, name):
        linked = []
        for value in _items(values, name):
            if isinstance(value, dict) and value.get("kind") == "review":
                _fields(value, ("kind", "review_id"))
                _text(value["review_id"], "Review evidence ID")
                review = self.records.get("manuscript_review", {}).get(value["review_id"])
                if review is None or review["bundle_digest"] != self.bundle["digest"]:
                    raise ResearchError("round_evidence_mismatch", "Review evidence names a manuscript review of the current bundle")
                item = {"reference": value, "review_digest": review["digest"]}
            else:
                item = self.development.one(value)
            self.items[digest(value)] = item
            linked.append(item)
        return linked

    def summary(self):
        return [self.items[key] for key in sorted(self.items)]


def _prepare_context(records, artifacts):
    evaluation = Evaluation.of(records, artifacts)
    context = development._Context(records, evaluation)
    context.require_objective()
    return context, publication._bundle(records, evaluation)


def _candidates(values, evidence):
    seen, pursued = set(), []
    for candidate in _items(values, "Candidates"):
        _fields(candidate, ("id", "direction", "statement", "disposition", "reason", "evidence"))
        for key in ("id", "statement", "reason"):
            _text(candidate[key], "Candidate " + key)
        if candidate["id"] in seen:
            raise ResearchError(_ERROR, "Candidate IDs must be unique")
        seen.add(candidate["id"])
        _choice(candidate["direction"], DIRECTIONS, "Candidate direction")
        _choice(candidate["disposition"], CANDIDATE_DISPOSITIONS, "Candidate disposition")
        evidence.many(candidate["evidence"], "Candidate evidence")
        if candidate["disposition"] == "pursue":
            pursued.append(candidate)
    return pursued


def _carried_key(item):
    return (item["assessment_id"], item["kind"], item.get("question") or item.get("cycle_id"))


def _carried(records, values):
    """The developments carried by the closing round: those its cycle assessments recorded since the round was admitted."""
    closing = closing_round_assessments(records)
    expected = {_carried_key(item): item for item in development.carried_developments(records) if item["assessment_id"] in closing}
    seen = {}
    for item in _items(values, "Carried developments", nonempty=False):
        _fields(item, ("assessment_id", "kind", "disposition", "reason"), ("question", "cycle_id"))
        _text(item["assessment_id"], "Carried development assessment ID")
        _text(item["kind"], "Carried development kind")
        for key in ("question", "cycle_id"):
            if key in item:
                _text(item[key], "Carried development " + key)
        _text(item["reason"], "Carried development reason")
        _choice(item["disposition"], CANDIDATE_DISPOSITIONS, "Carried development disposition")
        key = _carried_key(item)
        if key not in expected or key in seen:
            raise ResearchError(_ERROR, "Dispose each carried development of the closing round exactly once")
        seen[key] = item
    missing = [expected[key] for key in expected if key not in seen]
    if missing:
        raise ResearchError("carried_development_missing", "Dispose of every development the closing round carried",
                            {"missing": missing})
    return list(seen.values())


def _goal(value, evidence, pursued):
    _fields(value, ("direction", "field_change", "statement", "contribution_delta", "beneficiaries", "success_criteria",
                    "stop_conditions", "continuity", "route", "risks", "evidence"))
    for key in ("statement", "contribution_delta", "continuity", "route"):
        _text(value[key], "Goal " + key)
    _choice(value["direction"], DIRECTIONS, "Goal direction")
    if value["direction"] != pursued["direction"] or _normalized(value["statement"]) != _normalized(pursued["statement"]):
        raise ResearchError(_ERROR, "The goal states the pursued candidate")
    if value["field_change"] is not None:
        _fields(value["field_change"], ("corpus", "primaryCategory"))
        for key in ("corpus", "primaryCategory"):
            _text(value["field_change"][key], "Field change " + key)
        if value["direction"] != "horizontal":
            raise ResearchError(_ERROR, "A field change is a horizontal goal")
    for beneficiary in _items(value["beneficiaries"], "Goal beneficiaries"):
        _fields(beneficiary, ("who", "bottleneck", "evidence"))
        _text(beneficiary["who"], "Beneficiary")
        _text(beneficiary["bottleneck"], "Beneficiary bottleneck")
        evidence.many(beneficiary["evidence"], "Beneficiary evidence")
    for name, items, kinds in (("Success criteria", value["success_criteria"], CRITERION_KINDS),
                               ("Stop conditions", value["stop_conditions"], None)):
        seen = set()
        for item in _items(items, name):
            _fields(item, ("id", "kind", "statement") if kinds else ("id", "statement"))
            _text(item["id"], name + " ID")
            _text(item["statement"], name)
            if kinds:
                _choice(item["kind"], kinds, name + " kind")
            if item["id"] in seen:
                raise ResearchError(_ERROR, name + " IDs must be unique")
            seen.add(item["id"])
    _strings(value["risks"], "Goal risks")
    evidence.many(value["evidence"], "Goal evidence")
    return value


def _limits(value, goal):
    if not isinstance(value, dict) or not set(value) <= {"literature", "experiment"}:
        raise ResearchError(_ERROR, "Resource limits name the literature and experiment purposes")
    for purpose, amounts in value.items():
        resources._amounts(amounts, "Round " + purpose + " limits")
    if goal["field_change"] is not None:
        literature = value.get("literature", {})
        if not literature.get("network_requests") or not literature.get("readings"):
            raise ResearchError("round_field_change_refused", "A field change needs literature allowances for the collected difference")


def _reopening(records, value, evidence):
    """A reopening names an earlier round assessed unsuccessful and carries evidence its decision did not; returns that round's id."""
    if value is None:
        return None
    _fields(value, ("round_id", "reason", "evidence"))
    _text(value["reason"], "Reopening reason")
    _text(value["round_id"], "Reopened round ID")
    admission = records.get("round_admission", {}).get(value["round_id"])
    assessment = assessment_for(records, admission["id"]) if admission else None
    if assessment is None or assessment["successful"]:
        raise ResearchError(_ERROR, "Reopen an earlier round that was assessed unsuccessful")
    known = {digest(e["reference"]) for e in records["round_decision"][admission["decision_id"]]["evidence"]}
    changed = evidence.many(value["evidence"], "Reopening evidence")
    if all(digest(item["reference"]) in known for item in changed):
        raise ResearchError("round_reopening_unchanged", "Reopening needs evidence the earlier decision did not carry")
    return admission["id"]


def _distinct(records, goal, reopened_round_id):
    """The goal repeats no rejected candidate, and repeats an earlier round's goal only by reopening that round."""
    statement = _normalized(goal["statement"])
    for decision in records.get("round_decision", {}).values():
        if any(_normalized(c["statement"]) == statement for c in decision["payload"]["candidates"] if c["disposition"] == "rejected"):
            raise ResearchError("round_goal_repeated", "A goal cannot repeat a rejected candidate")
    repeated = [a["id"] for a in admissions(records) if _normalized(a["goal"]["statement"]) == statement]
    if repeated and reopened_round_id not in repeated:
        raise ResearchError("round_goal_repeated", "A goal that repeats an earlier round's goal reopens that round with changed evidence",
                            {"round_ids": repeated})


def _direction_open(records, goal, reopened_round_id):
    """After two consecutive unsuccessful rounds, their direction continues only by reopening one of them."""
    unsuccessful = []
    for admission in reversed(admissions(records)):
        assessment = assessment_for(records, admission["id"])
        if assessment is None or assessment["successful"]:
            break
        unsuccessful.append(admission)
    exhausted = unsuccessful[:2]
    if len(exhausted) == 2 and goal["direction"] in {a["goal"]["direction"] for a in exhausted} and reopened_round_id not in {a["id"] for a in exhausted}:
        raise ResearchError("round_direction_exhausted", "Two consecutive rounds in this direction were unsuccessful; change direction or reopen with changed evidence",
                            {"round_ids": [a["id"] for a in exhausted]})


def _development_exhausted(records):
    """The development budget's exhausted obligations (spec section 12): read by the decision and the gate."""
    return [o for o in resources.obligations(records, "research") if o["purpose"] == "development"]


def _next(records, context, evidence, value, number, pursued):
    _fields(value, ("number", "objective", "objective_lineage", "goal", "resource_limits", "reopening"))
    if value["number"] != number + 1:
        raise ResearchError(_ERROR, "The next round follows the closing round")
    if value["objective"] == context.objective:
        if value["objective_lineage"] is not None:
            raise ResearchError(_ERROR, "An unchanged objective has no lineage")
    else:
        principles.widen_objective(records, value["objective"], value["objective_lineage"], "proposed")
    goal = _goal(value["goal"], evidence, pursued)
    _limits(value["resource_limits"], goal)
    reopened_round_id = _reopening(records, value["reopening"], evidence)
    _distinct(records, goal, reopened_round_id)
    _direction_open(records, goal, reopened_round_id)
    exhausted = _development_exhausted(records)
    if exhausted:
        raise ResearchError("resource_budget_exhausted", "The development budget has no room for another round", exhausted[0])
    return value


def record_round(store, payload, *, expected_revision, request_id):
    """Close the current round on the exact bundle and decide: continue with a goal, or stop."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context, bundle = _prepare_context(records, artifacts)
        _fields(value, ("id", "closes", "decision", "bundle_digest", "candidates", "carried", "next", "reason"))
        _text(value["id"], "Round decision ID")
        _text(value["reason"], "Decision reason")
        _choice(value["decision"], DECISIONS, "Round decision")
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_bundle_mismatch", "Decide on the exact current manuscript bundle")
        number = current_number(records)
        if value["closes"] != number:
            raise ResearchError("round_number_mismatch", "Close the current round", {"current": number})
        latest = latest_admission(records)
        if latest is not None:
            assessment = assessment_for(records, latest["id"])
            if assessment is None or assessment["bundle_digest"] != bundle["digest"]:
                raise ResearchError("round_assessment_missing", "Assess the current round against its goal on this bundle before deciding")
            unkept = _unkept_claim_ids(derive_progress(records, context.artifacts, latest, bundle))
            if unkept:
                raise ResearchError("round_claims_dropped", "Keep, revise or supersede every claim of the round's opening bundle", {"claim_ids": unkept})
        evidence = _RoundEvidence(context, bundle)
        pursued = _candidates(value["candidates"], evidence)
        carried = _carried(records, value["carried"])
        if value["decision"] == "continue":
            if len(pursued) != 1:
                raise ResearchError(_ERROR, "A continue decision pursues exactly one candidate")
            _next(records, context, evidence, value["next"], number, pursued[0])
        elif pursued or value["next"] is not None or any(item["disposition"] == "pursue" for item in carried):
            raise ResearchError(_ERROR, "A stop decision pursues no candidate or carried development and proposes no round")
        record = {"id": value["id"], "payload": value, "closes": number, "decision": value["decision"],
                  "bundle_id": bundle["id"], "bundle_digest": bundle["digest"], "evidence": evidence.summary(),
                  "decided_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "round_decision", value["id"], record)], record

    return prepared_mutation(store, "round.decide", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def record_round_review(store, payload, *, expected_revision, request_id):
    """An independent assessor's judgment of one round decision."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context, bundle = _prepare_context(records, artifacts)
        _fields(value, ("id", "round_id", "round_digest", "assessor", "verdict", "checks", "limitations"))
        _text(value["id"], "Round review ID")
        _text(value["round_id"], "Reviewed decision ID")
        decision = records.get("round_decision", {}).get(value["round_id"])
        if decision is None:
            raise ResearchError("unknown_round_decision", "Review a recorded round decision", {"id": value["round_id"]})
        if value["round_digest"] != decision["digest"] or decision["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_review_stale", "Review the exact current decision on the current bundle")
        publication.validate_assessor(context.artifacts, value["assessor"], development.cycle_authors(records))
        _choice(value["verdict"], VERDICTS, "Round verdict")
        _strings(value["limitations"], "Review limitations", nonempty=True)
        kinds = CHECKS_CONTINUE if decision["decision"] == "continue" else CHECKS_STOP
        evidence = _RoundEvidence(context, bundle)
        seen = set()
        for check in _items(value["checks"], "Round checks"):
            _fields(check, ("kind", "status", "reason", "evidence"))
            _choice(check["kind"], kinds, "Round check")
            if check["kind"] in seen:
                raise ResearchError(_ERROR, "Address each round check once")
            seen.add(check["kind"])
            _choice(check["status"], CHECK_STATUSES, "Round check status")
            _text(check["reason"], "Round check reason")
            evidence.many(check["evidence"], "Round check evidence")
        if seen != set(kinds):
            raise ResearchError(_ERROR, "Address every round check: " + ", ".join(kinds))
        key = publication._assessor_key(value["assessor"]["id"])
        for saved in records.get("round_review", {}).values():
            if saved["round_id"] == decision["id"]:
                if publication._assessor_key(saved["payload"]["assessor"]["id"]) == key:
                    raise ResearchError("round_review_duplicate", "This assessor already reviewed this decision", {"review_id": saved["id"]})
            elif (value["verdict"] == "approved" and saved["verdict"] == "approved" and saved["closes"] == decision["closes"]
                  and records["round_decision"][saved["round_id"]]["bundle_digest"] == bundle["digest"]):
                raise ResearchError("round_decision_duplicate", "This closing round already has an approved decision on this bundle",
                                    {"review_id": saved["id"]})
        record = {"id": value["id"], "payload": value, "round_id": decision["id"], "round_digest": decision["digest"],
                  "closes": decision["closes"], "decision": decision["decision"], "verdict": value["verdict"],
                  "reviewed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "round_review", value["id"], record)], record

    return prepared_mutation(store, "round.review", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _opening(records, evaluation, bundle):
    """What the round starts from; freshness inside the round is judged against it."""
    claims = strict_json(evaluation.read(bundle["files"]["claims"]["artifact"]))
    selection = records.get("search_selection", {})
    return {"bundle_id": bundle["id"], "bundle_digest": bundle["digest"], "claim_ids": sorted(c["id"] for c in claims),
            "search_selection": {p: selection.get("research:" + p, {}).get("search_id") for p in OPENING_PURPOSES},
            "requirement_ids": sorted(records.get("fulltext_requirement", {})),
            "cycle_ids": sorted(records.get("cycle", {})), "fulltext_reading_count": count_full_readings(records),
            "accounts": resources.account_report(records, "research")}


def _field_change(records, change):
    """A field change adds a category inside the study's corpus; a new corpus is a migration, not a round."""
    definitions = [c["definition"] for c in records.get("collection", {}).values()]
    if change["corpus"] not in {d["corpus"] for d in definitions}:
        raise ResearchError("round_field_change_refused", "A field change stays in the study's corpus; a new corpus is not a development round")
    if change["primaryCategory"] in {d["primaryCategory"] for d in definitions}:
        raise ResearchError("round_field_change_refused", "The category is already part of the study's cohort")


def admit_round(store, payload, *, expected_revision, request_id):
    """Open the approved next round: charge the development budget, widen the objective, record the opening state."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        _fields(value, ("id", "round_id", "review_id", "reason"))
        _text(value["id"], "Round admission ID")
        _text(value["round_id"], "Admitted decision ID")
        _text(value["review_id"], "Admitting review ID")
        _text(value["reason"], "Admission reason")
        # Checked before readiness: an active round's own literature obligations keep readiness
        # from passing, and the reason to refuse is the open round, not the work it still owes.
        if active_round(records) is not None:
            raise ResearchError("round_active", "Assess the current round before opening another")
        context, bundle = _prepare_context(records, artifacts)
        decision = records.get("round_decision", {}).get(value["round_id"])
        if decision is None:
            raise ResearchError("unknown_round_decision", "Admit a recorded round decision", {"id": value["round_id"]})
        review = records.get("round_review", {}).get(value["review_id"])
        if review is None:
            raise ResearchError("unknown_round_review", "Admit a reviewed round decision", {"id": value["review_id"]})
        if review["round_id"] != decision["id"] or review["verdict"] != "approved":
            raise ResearchError("round_review_required", "An approved independent review of this decision is required")
        if decision["decision"] != "continue":
            raise ResearchError(_ERROR, "Admit a continue decision")
        if decision["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_review_stale", "The decision's bundle is no longer current")
        number = current_number(records)
        if decision["closes"] != number:
            raise ResearchError("round_number_mismatch", "Admit a decision that closes the current round", {"current": number})
        proposal = decision["payload"]["next"]
        if proposal["goal"]["field_change"] is not None:
            _field_change(records, proposal["goal"]["field_change"])
        changes = [resources.charge(records, "development", {"rounds": 1})]
        if proposal["objective_lineage"] is not None:
            changes.extend(principles.widen_objective(records, proposal["objective"], proposal["objective_lineage"], value["id"]))
        record = {"id": value["id"], "number": proposal["number"], "decision_id": decision["id"], "review_id": review["id"],
                  "goal": proposal["goal"], "objective": proposal["objective"], "objective_lineage": proposal["objective_lineage"],
                  "resource_limits": proposal["resource_limits"], "reopening": proposal["reopening"],
                  "opening": _opening(records, context.artifacts, bundle), "reason": value["reason"],
                  "admitted_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        changes.append(immutable_record(records, "round_admission", value["id"], record))
        return changes, record

    return prepared_mutation(store, "round.admit", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _compute_usage(records, opening_accounts):
    """What each purpose charged since the round opened, per unit."""
    usage = {}
    for purpose, units in resources.account_report(records, "research").items():
        before = opening_accounts.get(purpose, {})
        usage[purpose] = {unit: value["charged"] - before.get(unit, {}).get("charged", 0) for unit, value in units.items()}
    return usage


def derive_progress(records, evaluation, admission, bundle):
    """What the round did since its admission, judged against the opening state it recorded.

    Information about the round for its assessment, the gate report and the round packet; no
    rule reads the measurement. `bundle` is None when no bundle is pinned: the claim lists are
    then empty and there is no measurement."""
    opening = admission["opening"]
    fresh = sorted(fresh_searches(records, admission))
    exemplar = has_round_exemplar(records, admission)
    cycles = sorted(c["id"] for c in records.get("cycle", {}).values()
                    if c["id"] not in opening["cycle_ids"] and c["assessment_id"] is not None)
    claims, opening_claims = {}, {}
    if bundle is not None:
        claims = {c["id"]: c for c in strict_json(evaluation.read(bundle["files"]["claims"]["artifact"]))}
        opening_bundle = records["publication_bundle"][opening["bundle_id"]]
        opening_claims = {c["id"]: c["claim"] for c in strict_json(evaluation.read(opening_bundle["files"]["claims"]["artifact"]))}
    new = sorted(i for i, c in claims.items() if i not in opening["claim_ids"] and "superseded" not in c)
    # An opening claim keeps its text, or carries `revised` or `superseded`; a rewritten text without a marker is a change.
    changed = sorted(i for i, c in claims.items() if i in opening_claims and c["claim"] != opening_claims[i]
                     and "revised" not in c and "superseded" not in c)
    return {"fresh_purposes": fresh, "exemplar": exemplar, "cycles": cycles,
            "readings": count_full_readings(records) - opening["fulltext_reading_count"],
            "new_claim_ids": new,
            "revised_claim_ids": sorted(i for i, c in claims.items() if "revised" in c),
            "superseded_claim_ids": sorted(i for i, c in claims.items() if "superseded" in c),
            "changed_claim_ids": changed,
            "dropped_claim_ids": sorted(i for i in opening["claim_ids"] if i not in claims) if bundle else [],
            "usage": _compute_usage(records, opening["accounts"]),
            "measurement": predictions.measurement_summary(records, bundle) if bundle else None,
            "unproductive": not (new and len(fresh) == len(literature.DEVELOPMENT_PURPOSES) and exemplar and cycles)}


def _validate_judgments(values, expected, name, evidence):
    """Every item of the goal (`expected` ids) judged exactly once with a status, an explanation and evidence."""
    seen = []
    for item in _items(values, name):
        _fields(item, ("id", "status", "explanation", "evidence"))
        _text(item["id"], name + " ID")
        if item["id"] not in expected or item["id"] in seen:
            raise ResearchError(_ERROR, "Judge each of the goal's " + name.lower() + " exactly once")
        seen.append(item["id"])
        _choice(item["status"], development.OBSERVATION_STATUSES, name + " status")
        _text(item["explanation"], name + " explanation")
        evidence.many(item["evidence"], name + " evidence")
    if set(seen) != set(expected):
        raise ResearchError(_ERROR, "Judge every one of the goal's " + name.lower())
    return values


def assess_round(store, payload, *, expected_revision, request_id):
    """Judge the current round against its goal on the exact current bundle, and derive what the round did."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context, bundle = _prepare_context(records, artifacts)
        _fields(value, ("id", "round_id", "bundle_digest", "criteria", "stop_conditions", "summary"))
        _text(value["id"], "Round assessment ID")
        _text(value["summary"], "Round summary")
        _text(value["round_id"], "Assessed round ID")
        admission = records.get("round_admission", {}).get(value["round_id"])
        if admission is None:
            raise ResearchError("unknown_round", "Assess an admitted round", {"id": value["round_id"]})
        if value["bundle_digest"] != bundle["digest"]:
            raise ResearchError("round_bundle_mismatch", "Assess the round on the exact current manuscript bundle")
        if latest_admission(records)["id"] != admission["id"]:
            raise ResearchError(_ERROR, "Assess the current round", {"round_id": admission["id"]})
        existing = assessment_for(records, admission["id"])
        if existing is not None and existing["bundle_digest"] == bundle["digest"]:
            raise ResearchError("round_already_assessed", "This round already has its assessment on this bundle", {"round_id": admission["id"]})
        evidence = _RoundEvidence(context, bundle)
        goal = admission["goal"]
        criteria = _validate_judgments(value["criteria"], {c["id"] for c in goal["success_criteria"]}, "Success criteria", evidence)
        _validate_judgments(value["stop_conditions"], {s["id"] for s in goal["stop_conditions"]}, "Stop conditions", evidence)
        derived = derive_progress(records, context.artifacts, admission, bundle)
        successful = any(c["status"] == "observed" for c in criteria) and not derived["unproductive"]
        record = {"id": value["id"], "payload": value, "round_id": admission["id"], "bundle_digest": bundle["digest"],
                  "derived": derived, "successful": successful, "unproductive": derived["unproductive"],
                  "assessed_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        return [immutable_record(records, "round_assessment", value["id"], record)], record

    return prepared_mutation(store, "round.assess", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def literature_obligations(fresh_purposes, exemplar):
    """The active round's literature obligations (spec section 7): one home for the foundation report and the round gate."""
    obligations = [obligation("round_search_missing", "Record this development round's captured search for the purpose.", purpose=p)
                   for p in literature.DEVELOPMENT_PURPOSES if p not in fresh_purposes]
    if not exemplar:
        obligations.append(obligation("round_exemplar_missing", "Select a development exemplar with require-fulltext (purpose exemplar) and read it in full."))
    return obligations


def _unkept_claim_ids(progress):
    """Opening claims that disappeared, or whose text changed without a marker: both break the continuity rule the same way."""
    return sorted(progress["dropped_claim_ids"] + progress["changed_claim_ids"])


def _continuity_obligations(progress):
    """Claims continuity (spec section 9) holds for the whole round, assessed or not, until its closing decision."""
    unkept = _unkept_claim_ids(progress)
    if unkept:
        return [obligation("round_claims_dropped", "Keep, revise or supersede every claim of the round's opening bundle.", claim_ids=unkept)]
    return []


def _progress_obligations(progress, admission, bundle):
    """What the active round still owes before its assessment (spec sections 7, 8 and 9)."""
    obligations = literature_obligations(progress["fresh_purposes"], progress["exemplar"])
    if not progress["cycles"]:
        obligations.append(obligation("round_cycle_missing", "Plan, execute and assess at least one cycle in this round."))
    if bundle is not None:
        obligations.extend(_continuity_obligations(progress))
        if not progress["new_claim_ids"]:
            obligations.append(obligation("round_claim_missing", "The round's manuscript needs at least one new evidenced claim."))
    obligations.append(obligation("round_assessment_missing", "Assess the current round against its goal before deciding.", round_id=admission["id"]))
    return obligations


def _decision_obligations(records, bundle, number):
    """The approved decision closing round `number` on the bundle, and what is missing for it.

    An approved continue is never admitted here: its admission raises the current round number, after
    which the gate looks for the decision closing the new number. An exhausted development budget
    stands beside every pending step but an approved stop (spec sections 6 and 10)."""
    reviews = records.get("round_review", {}).values()
    candidates = [d for d in records.get("round_decision", {}).values() if d["bundle_digest"] == bundle["digest"] and d["closes"] == number]
    approved = next((d for d in candidates if any(r["round_id"] == d["id"] and r["verdict"] == "approved" for r in reviews)), None)
    exhausted = _development_exhausted(records)
    if approved is not None:
        if approved["decision"] == "stop":
            return approved, []
        return approved, exhausted + [obligation("round_admission_missing", "Admit the approved next round with round-admit.", decision_id=approved["id"])]
    if candidates:
        latest = max(candidates, key=lambda d: d["decided_revision"])
        pending = any(r["round_id"] == latest["id"] for r in reviews)
        code = "round_review_pending" if pending else "round_review_missing"
        return None, exhausted + [obligation(code, "Obtain an approving independent review of the round decision.", decision_id=latest["id"])]
    return None, exhausted + [obligation("round_decision_missing", "Decide on this exact manuscript: continue with a goal, or stop.", round=number)]


def round_state(records, artifacts):
    """The gate between evaluate and either a new development round or deposit (spec section 6).

    In order: the current bundle; for an admitted round, its assessment on this bundle, what it still
    owes while it is active, and its claims continuity until its closing decision; then the decision
    closing the current round, its approving review and, for a continue, its admission. `decision` is
    set only when an approved decision closing the current round binds the current bundle; an admitted
    decision is never reported, because its admission opened the round that the gate now closes."""
    evaluation = Evaluation.of(records, artifacts)
    latest = latest_admission(records)
    number = current_number(records)
    active = active_round(records)
    assessment = assessment_for(records, latest["id"]) if latest is not None else None
    decision_obligations, progress_obligations, progress = [], [], None
    bundle = None
    try:
        bundle = publication._bundle(records, evaluation)
    except ResearchError as error:
        decision_obligations.append(obligation(error.code, error.message, **(error.details or {})))
    assessed = bundle is not None and assessment is not None and assessment["bundle_digest"] == bundle["digest"]
    if latest is not None:
        # The round's counts are reported whenever a round was admitted, with or without a current bundle (spec section 14).
        progress = derive_progress(records, evaluation, latest, bundle)
        if active is not None:
            progress_obligations = _progress_obligations(progress, active, bundle)
        elif bundle is not None:
            if not assessed:
                decision_obligations.append(obligation("round_assessment_missing", "Assess the current round against its goal on this bundle before deciding.",
                                                       round_id=latest["id"]))
            progress_obligations = _continuity_obligations(progress)
    decision = None
    if bundle is not None and not decision_obligations and not progress_obligations:
        decision, pending = _decision_obligations(records, bundle, number)
        decision_obligations.extend(pending)
    obligations = decision_obligations + progress_obligations
    return {"ready": not obligations, "obligations": obligations, "decision_obligations": decision_obligations,
            "progress_obligations": progress_obligations, "round": number, "active": active is not None, "assessed": assessed,
            "decision": decision["decision"] if decision else None, "decision_id": decision["id"] if decision else None,
            "next": decision["payload"]["next"] if decision and decision["decision"] == "continue" else None,
            "progress": progress,
            "measurement": predictions.measurement_summary(records, bundle) if bundle else None,
            "limits": latest["resource_limits"] if latest is not None else None,
            "budget": resources.account_report(records, "research").get("development"),
            "digest": digest({"round": number, "decision": decision["digest"] if decision else None, "obligations": obligations}),
            "counts": {"obligations": len(obligations)}, "mechanical_only": True}


def round_summary(report):
    """A bounded view of the round gate for status reports: numbers, booleans, purpose names, the measurement,
    the admitted round's limits, the development budget line and the usage since the admission (spec sections 12 and 14)."""
    progress = report["progress"]
    return {"number": report["round"], "active": report["active"], "assessed": report["assessed"],
            "decision": report["decision"], "obligations": len(report["obligations"]),
            "progress": None if progress is None else {
                "fresh_purposes": progress["fresh_purposes"], "exemplar": progress["exemplar"], "cycles": len(progress["cycles"]),
                "new_claims": len(progress["new_claim_ids"]), "dropped_claims": len(progress["dropped_claim_ids"]),
                "readings": progress["readings"]},
            "measurement": report["measurement"], "limits": report["limits"], "budget": report["budget"],
            "usage": None if progress is None else {p: progress["usage"][p] for p in ("literature", "experiment") if p in progress["usage"]}}
