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
from .operations import fields, immutable_record, prepared_mutation, strings, text
from . import development, principles, publication, resources


DECISIONS = ("continue", "stop")
DIRECTIONS = ("vertical", "horizontal")
CANDIDATE_DISPOSITIONS = ("pursue", "rejected", "deferred")
CRITERION_KINDS = ("claim", "scope")
CHECKS_CONTINUE = ("impact", "demand", "novelty_risk", "feasibility", "distinctness", "continuity")
CHECKS_STOP = ("stop", "demand")
VERDICTS = ("approved", "not_approved", "unresolved")
CHECK_STATUSES = ("passed", "failed", "unresolved")
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
    return next((a for a in records.get("round_assessment", {}).values() if a["round_id"] == admission_id), None)


def current_number(records):
    latest = latest_admission(records)
    return latest["number"] if latest else 1


def active_round(records):
    """The admitted round without an assessment, or None."""
    latest = latest_admission(records)
    return latest if latest is not None and assessment_for(records, latest["id"]) is None else None


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


def _carried(records, latest, values):
    """The developments carried by the closing round: those its cycle assessments recorded since the round was admitted."""
    admitted_revision = latest["admitted_revision"] if latest else 0
    expected = {_carried_key(item): item for item in development.carried_developments(records)
                if records["cycle_assessment"][item["assessment_id"]]["assessed_revision"] > admitted_revision}
    seen = {}
    for item in _items(values, "Carried developments", nonempty=False):
        _fields(item, ("assessment_id", "kind", "disposition", "reason"), ("question", "cycle_id"))
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
    exhausted = [o for o in resources.obligations(records, "research") if o.get("purpose") == "development"]
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
        evidence = _RoundEvidence(context, bundle)
        pursued = _candidates(value["candidates"], evidence)
        carried = _carried(records, latest, value["carried"])
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
