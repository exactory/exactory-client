"""Prospective development, retained results and exact-candidate readiness.

Mutations prepare against one snapshot and publish through revision CAS. Plans,
admissions, executions, assessments, checkpoints and reviews are immutable;
cycle/account/selection records are projections with preserved Store events.

An admission reserves resources and binds a program and run identity. It does
not run a process. Replaying its receipt is not permission to launch again.
The managed launcher must launch the admitted bytes once and reconcile an
unknown outcome against that same run identity before doing any further work.
Imported evidence retains its earlier origin and earns no prospective credit.

These empirical research labels neither accept mathematical proofs nor change
the native math controller's budgets, reservations or recovery authority.
"""

import json
import math

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .execution_accounting import require_accounted_usage
from .graph import obligation
from .operations import fields, immutable_record, prepared_mutation, strings, text, timestamp
from .reading import validate_read_evidence
from .source_links import captured_source, original_identity, read_locator, validate_link
from .synthesis import synthesis_state


STRATEGIES = ("generalization", "weaker_assumptions", "mechanism", "tightness_limits", "unification",
              "representation_change", "transfer", "practical_usefulness", "other")
DISPOSITIONS = ("pursue", "not_useful", "resolved", "budget_paused", "next_round")
_REVIEW_CHECKS = ("validity", "scope", "novelty", "contribution", "development", "branches")
_LIMITS = ("This is mechanical evidence anchoring and documented research assessment, not scientific truth or native "
           "mathematical proof acceptance. The launcher must execute the admitted artifact and preserve the actual run "
           "identity. Admission replay, caller-supplied timestamps and file timestamps do not prove execution or authorize "
           "another launch. Imported results retain their original provenance. A different assessor name and a local "
           "receipt do not prove comprehension, independence or impartiality. Scope, failure signals, estimands, source "
           "support and scientific judgments require substantive assessment. Strategy identity detects the same "
           "normalized method, test and scope, not every semantic paraphrase. Native proof and budget authority remain "
           "with the existing math controller.")


def _text(value, name):
    return text(value, name, code="invalid_development")


def _items(value, name, nonempty=False):
    if not isinstance(value, list) or nonempty and not value:
        raise ResearchError("invalid_development", name + " must be an array" + (" with at least one entry" if nonempty else ""))
    return value


def _strings(value, name, nonempty=False):
    return strings(value, name, nonempty=nonempty, code="invalid_development")


def _fields(value, required, optional=()):
    fields(value, required, optional, code="invalid_development")


def _choice(value, choices, name):
    if value not in choices:
        raise ResearchError("invalid_development", name + " must be one of: " + ", ".join(choices))


def _number(value, name, positive=False, integer=False):
    try:
        valid = (type(value) in ((int,) if integer else (int, float)) and math.isfinite(value)
                 and value >= 0 and (not positive or value > 0))
    except OverflowError:
        valid = False
    if not valid:
        raise ResearchError("invalid_development", name + " must be a finite " + ("positive" if positive else "nonnegative") + " number")


def _normalized(value):
    return " ".join(value.casefold().split())


def _unique(values):
    return sorted({digest(x): x for x in values}.values(), key=lambda x: (x["code"], digest(x)))


def _get(records, kind, identifier, code):
    _text(identifier, kind + " ID")
    value = records.get(kind, {}).get(identifier)
    if value is None:
        raise ResearchError(code, "Record the referenced " + kind + " before using it", {"id": identifier})
    return value


def _scope(value, objective):
    _fields(value, ("id", "kind", "statement", "assumptions", "remaining_obligations"))
    _text(value["id"], "Scope ID")
    _text(value["statement"], "Exact scope")
    _choice(value["kind"], ("full", "partial"), "Scope relationship")
    _strings(value["assumptions"], "Scope assumptions")
    _strings(value["remaining_obligations"], "Remaining complete-objective obligations", value["kind"] == "partial")
    if value["kind"] == "full" and value["statement"] != objective["statement"]:
        raise ResearchError("objective_scope_mismatch", "A full scope must retain the original complete objective statement")
    if value["kind"] == "partial" and value["id"] == objective["id"]:
        raise ResearchError("objective_scope_mismatch", "Give the special case its own scope identity; keep the full objective")


class _Context:
    def __init__(self, records, artifacts):
        self.records, self.artifacts = records, Evaluation.of(records, artifacts)
        artifacts = self.artifacts
        config = records.get("configuration", {}).get("research")
        if config and config["profile"] != "research":
            raise ResearchError("profile_inapplicable", "Author development does not apply to the verification profile")
        # This shared helper assesses configuration and foundation on the same
        # supplied records/artifacts snapshot and returns both current reports.
        self.synthesis = synthesis_state(records, artifacts, "research")
        self.configuration = self.synthesis["configuration"]
        self.foundation = self.synthesis["foundation"]
        self.objective = self.configuration["target"]
        self.assessments = {}
        self.assessing = set()

    def require_objective(self):
        if self.objective is None or not self.configuration["ready"]:
            raise ResearchError("configuration_not_ready", "Fix the full objective and adopt the current constitution first",
                                {"obligations": self.configuration["obligations"]})

    def require_ready(self):
        self.require_objective()
        if not self.synthesis["ready"]:
            raise ResearchError("research_not_ready", "Complete the current foundation and synthesis before admitting prospective work",
                                {"obligations": self.synthesis["obligations"]})

    def dependencies(self):
        return {"configuration": self.configuration["digest"], "constitution": self.configuration["constitution"],
                "objective": self.objective, "preparation": self.synthesis["preparation_digest"]}

    def assessment(self, identifier):
        if identifier not in self.assessments:
            record = _get(self.records, "cycle_assessment", identifier, "unknown_assessment")
            if identifier in self.assessing:
                raise ResearchError("lineage_cycle", "Development inheritance must follow earlier checkpoints")
            self.assessing.add(identifier)
            try:
                report = _assess(self, record["payload"])
                if report["dependencies"] != record["dependencies"]:
                    report["obligations"].append(obligation("development_dependencies_stale",
                        "Reassess the result against current policy, literature, sources and executions.", assessment_id=identifier))
                    report["validated_result"] = False
                    report["complete"] = False
                report["obligations"] = _unique(report["obligations"])
                self.assessments[identifier] = report
            finally:
                self.assessing.remove(identifier)
        return self.assessments[identifier]


class _Evidence:
    def __init__(self, context):
        self.context = context
        self.items = {}

    def one(self, value):
        key = digest(value)
        if key in self.items:
            return self.items[key]
        context = self.context
        if isinstance(value, dict) and value.get("kind") == "source":
            _fields(value, ("kind", "link"))
            linked = context.artifacts.link(value["link"])
            reading = validate_read_evidence(context.records, context.artifacts, value["link"], depth="fulltext")
            work = linked["work"]
            # Common source validation also checks source-backed metadata and
            # aliases for evidence outside the citation graph.
            aliases = [a for a in context.records.get("alias", {}).values()
                       if any(x["work_id"] == work["work_id"] for x in a["assertions"])]
            source_ids = {context.records["work_assertion"][i]["source_id"] for i in work["assertion_ids"]}
            source_ids.update(x["source_id"] for a in aliases for x in a["assertions"])
            sources = [captured_source(context.records, context.artifacts, i) for i in sorted(source_ids)]
            item = {"reference": value, "reading": reading, "work": digest(work),
                    "metadata": digest(sources), "aliases": digest(aliases),
                    "original_sha256": original_identity(context.records, value["link"])}
        elif isinstance(value, dict) and value.get("kind") == "result":
            _fields(value, ("kind", "execution_id", "output_id", "artifact", "locator"))
            execution = _get(context.records, "execution", value["execution_id"], "unknown_execution")
            payload = execution["payload"]
            output = next((o for o in payload["outputs"] if o["id"] == value["output_id"]), None)
            if output is None or output["artifact"] != value["artifact"]:
                raise ResearchError("result_evidence_mismatch", "The result locator must identify an actual output of this exact execution")
            read_locator(context.artifacts, value["artifact"], value["locator"])
            _execution_artifacts(context.artifacts, payload)
            plan = _get(context.records, "cycle_plan", payload["cycle_id"], "unknown_cycle")
            requirement = next(r for r in plan["payload"]["evidence_requirements"] if r["id"] == output["requirement_id"])
            item = {"reference": value, "execution_digest": execution["digest"], "cycle_id": payload["cycle_id"],
                    "origin": payload["origin"], "status": payload["status"],
                    "requirement_id": output["requirement_id"], "requirement_kind": requirement["kind"]}
        else:
            raise ResearchError("invalid_development", "Evidence must identify an actual source Link or an execution output and locator")
        self.items[key] = item
        return item

    def many(self, values, name, nonempty=True):
        return [self.one(v) for v in _items(values, name, nonempty)]

    def summary(self):
        return [self.items[k] for k in sorted(self.items)]


def _labelled(values, name, kinds=None):
    seen = set()
    for value in _items(values, name, True):
        _fields(value, ("id", "statement") if kinds is None else ("id", "kind", "description"))
        _text(value["id"], name + " ID")
        _text(value["statement" if kinds is None else "description"], name)
        if value["id"] in seen:
            raise ResearchError("invalid_development", name + " IDs must be unique")
        seen.add(value["id"])
        if kinds is not None:
            _choice(value["kind"], kinds, name + " kind")


def _literature(context, evidence, value, scope):
    # A comparison recorded under the foundation digest (before 0.38.0) is stale, not malformed.
    _fields(value, ("scope", "comparison", "sources", "gaps"), ("literature_digest", "foundation_digest"))
    _text(value["comparison"], "Claim-specific literature comparison")
    _strings(value["gaps"], "Literature gaps")
    evidence.many([{"kind": "source", "link": link} for link in _items(value["sources"], "Related full-read sources", True)], "Literature evidence")
    if value["scope"] != scope or value.get("literature_digest") != context.synthesis["literature_digest"] or value["gaps"]:
        raise ResearchError("literature_comparison_stale", "Record the literature comparison for this exact claim/scope against the current literature_digest")


def _plan_evidence(evidence, value):
    # The comparison's original foundation digest stays historical. Its exact
    # source bytes and reading coverage must still be valid for current use.
    evidence.many([{"kind": "source", "link": link} for link in value["literature"]["sources"]],
                  "Retained prospective plan sources")
    for item in value["inheritance"]:
        evidence.many(item["evidence"], "Retained plan inheritance evidence")
    for item in value["reopening"]:
        evidence.many(item["evidence"], "Retained plan reopening evidence")
    return evidence.summary()


def _unresolved_plan_sources(context, checkpoint, value):
    """Authenticate plan inheritance when no recorded execution output exists."""
    if value["use"] != "unresolved" or value["assessment_id"] is not None:
        return False
    cycle = context.records["cycle"][checkpoint["cycle_id"]]
    # A later assessment may truthfully retain the absence of results. It does
    # not turn this historical unresolved inheritance into result credit.
    if checkpoint["execution_ids"] != cycle["execution_ids"]:
        return False
    for admission in context.records.get("execution_admission", {}).values():
        if admission["cycle_id"] == cycle["id"] and admission["id"] not in context.records.get("execution_outcome", {}):
            return False
    for identifier in cycle["execution_ids"]:
        execution = context.records["execution"][identifier]
        if execution["payload"]["outputs"] or checkpoint["execution_digests"].get(identifier) != execution["digest"]:
            return False
        _execution_artifacts(context.artifacts, execution["payload"])
    plan = context.records["cycle_plan"][cycle["id"]]
    if checkpoint["plan_digest"] != plan["digest"]:
        return False
    sources = {digest({"kind": "source", "link": link}) for link in plan["payload"]["literature"]["sources"]}
    if not {digest(e) for e in value["evidence"]} <= sources:
        return False
    if not set(plan["payload"]["scope"]["assumptions"]) <= set(value["assumptions"]):
        raise ResearchError("inheritance_assumptions_missing", "Retain the original plan's assumptions without claiming an unestablished result")
    return True


def _inheritance(context, evidence, value):
    dependencies = []
    predecessor = value["predecessor"]
    if predecessor is not None:
        parent = _get(context.records, "checkpoint", predecessor, "unknown_checkpoint")
        context.artifacts.read(parent["artifact"])
        if parent["objective"] != context.objective:
            raise ResearchError("objective_mismatch", "A successor checkpoint must retain the same complete objective")
        if _normalized(value["question"]) == _normalized(context.records["cycle_plan"][parent["cycle_id"]]["payload"]["question"]):
            raise ResearchError("development_question_repeated", "A successor must pose a distinct development question")
    inherited = set()
    for item in _items(value["inheritance"], "Inherited results and failures"):
        _fields(item, ("checkpoint_id", "assessment_id", "use", "evidence", "assumptions", "deduction"))
        checkpoint = _get(context.records, "checkpoint", item["checkpoint_id"], "unknown_checkpoint")
        context.artifacts.read(checkpoint["artifact"])
        if checkpoint["objective"] != context.objective or checkpoint["assessment_id"] != item["assessment_id"]:
            raise ResearchError("inheritance_mismatch", "Inherit the assessment actually preserved by the named checkpoint")
        _choice(item["use"], ("validated_result", "failure", "unresolved"), "Inherited evidence use")
        _strings(item["assumptions"], "Inherited assumptions")
        _text(item["deduction"], "Deduction contributing to the complete objective")
        linked = evidence.many(item["evidence"], "Inherited evidence")
        if not any(x.get("cycle_id") == checkpoint["cycle_id"] for x in linked) and not _unresolved_plan_sources(context, checkpoint, item):
            raise ResearchError("inheritance_mismatch", "Cite retained execution evidence, or authenticated unresolved plan sources when no output exists and no run is pending")
        assessment = context.assessment(item["assessment_id"]) if item["assessment_id"] is not None else None
        if item["use"] == "validated_result" and (assessment is None or not assessment["validated_result"]):
            raise ResearchError("inherited_result_stale", "Reassess inherited evidence before assigning validated-result credit")
        if item["use"] == "validated_result":
            assessed_results = {digest(e) for e in assessment["payload"]["result"]["evidence"] if e["kind"] == "result"}
            inherited_results = {digest(e) for e in item["evidence"] if e["kind"] == "result"}
            if not inherited_results or not inherited_results <= assessed_results:
                raise ResearchError("inheritance_result_mismatch", "Inherit the exact assessed result, not another locator or output from the same run")
        if assessment is not None and not set(assessment["payload"]["assumptions"]) <= set(item["assumptions"]):
            raise ResearchError("inheritance_assumptions_missing", "Retain the assumptions under which the inherited result was assessed")
        dependencies.append({"checkpoint": checkpoint["digest"], "assessment": assessment["digest"] if assessment else None,
                             "use": item["use"]})
        inherited.add(item["checkpoint_id"])
    if predecessor is not None and predecessor not in inherited:
        raise ResearchError("inheritance_missing", "Explain the predecessor's inherited evidence, assumptions and remaining contribution")
    return dependencies


def _strategy_key(value):
    scope = value["scope"]
    return digest({"objective": value["objective"], "scope": _normalized(scope["statement"]),
                   "assumptions": sorted(_normalized(s) for s in scope["assumptions"]),
                   "mechanism": _normalized(value["strategy"]["mechanism"]),
                   "test": _normalized(value["distinguishing_test"])})


def _plan_dependencies(context, value):
    evidence = _Evidence(context)
    _literature(context, evidence, value["literature"], value["scope"])
    inheritance = _inheritance(context, evidence, value)
    for reopening in value["reopening"]:
        evidence.many(reopening["evidence"], "Changed evidence addressing failure")
    account = context.records.get("strategy_account", {}).get(_strategy_key(value), {})
    return dict(context.dependencies(), evidence=digest(evidence.summary()), inheritance=inheritance,
                strategy_failures=digest(account.get("failures", [])))


def _exhausted(account):
    return (account["executions"] >= account["limits"]["max_executions"]
            or account["charged_units"] >= account["limits"]["max_units"])


def _validate_reopening(context, value, account):
    addressed = {}
    evidence = _Evidence(context)
    for item in _items(value["reopening"], "Reopening conditions"):
        _fields(item, ("assessment_id", "signal_id", "reason", "evidence"))
        _text(item["reason"], "Changed failure condition")
        record = _get(context.records, "cycle_assessment", item["assessment_id"], "unknown_assessment")
        failure = next((f for f in record["payload"]["failures"] if f["signal_id"] == item["signal_id"] and f["status"] == "observed"), None)
        if failure is None:
            raise ResearchError("reopening_mismatch", "Address an actual recorded observed failure signal")
        changed = evidence.many(item["evidence"], "Changed failure evidence")
        historical = record["assessment"]["evidence"]
        def identity(linked):
            reference = linked["reference"]
            return linked["original_sha256"] if reference["kind"] == "source" else reference["artifact"]["sha256"]
        if {identity(x) for x in changed} <= {identity(x) for x in historical}:
            raise ResearchError("reopening_evidence_unchanged", "Changing a label or locator within the same prior evidence does not address a failure")
        addressed[(item["assessment_id"], item["signal_id"])] = item
    for failed in account.get("failures", []):
        if (failed["assessment_id"], failed["signal_id"]) not in addressed:
            raise ResearchError("branch_reopening_required", "Address the strategy's recorded failure conditions with changed evidence before reopening",
                                {"failure": failed})


def plan_cycle(store, payload, *, expected_revision, request_id):
    """Freeze a claim-specific plan, source comparison, lineage and resource limits."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context = _Context(records, artifacts)
        context.require_ready()
        _fields(value, ("id", "author", "objective", "scope", "hypothesis", "question", "strategy", "predecessor", "inheritance",
                        "reopening", "distinguishing_test", "expected_outcomes", "failure_signals", "evidence_requirements", "literature", "resource_limits"))
        for key in ("id", "author", "hypothesis", "question", "distinguishing_test"):
            _text(value[key], "Plan " + key)
        if value["objective"] != context.objective:
            raise ResearchError("objective_mismatch", "Every branch must retain the complete fixed objective")
        _scope(value["scope"], context.objective)
        _fields(value["strategy"], ("kind", "mechanism", "why"))
        _choice(value["strategy"]["kind"], STRATEGIES, "Development strategy")
        for key in ("mechanism", "why"):
            _text(value["strategy"][key], "Strategy " + key)
        _labelled(value["expected_outcomes"], "Expected outcomes")
        _labelled(value["failure_signals"], "Failure signals")
        _labelled(value["evidence_requirements"], "Evidence requirements", ("result", "validation", "log"))
        if not {"result", "validation"} <= {r["kind"] for r in value["evidence_requirements"]}:
            raise ResearchError("invalid_development", "Plan both result evidence and validity evidence before execution")
        limits = value["resource_limits"]
        _fields(limits, ("max_executions", "max_units", "unit"))
        _number(limits["max_executions"], "Execution limit", positive=True, integer=True)
        _number(limits["max_units"], "Resource limit", positive=True)
        _text(limits["unit"], "Resource unit")
        key = _strategy_key(value)
        account = records.get("strategy_account", {}).get(key)
        if account is not None and _exhausted(account):
            raise ResearchError("strategy_budget_exhausted", "A new ID or larger declaration cannot reset the same exhausted strategy", {"strategy_key": key})
        if account is None:
            account = {"key": key, "limits": limits, "executions": 0, "charged_units": 0, "cycles": [], "failures": []}
        elif account["limits"] != limits:
            raise ResearchError("strategy_budget_locked", "The existing strategy retains its original resource limits")
        _validate_reopening(context, value, account)
        if account["cycles"] and value["predecessor"] is None:
            raise ResearchError("strategy_lineage_required", "Continue the existing strategy through a durable checkpoint, not a new unlinked ID")
        dependencies = _plan_dependencies(context, value)
        record = {"id": value["id"], "payload": value, "dependencies": dependencies, "strategy_key": key,
                  "planned_revision": expected_revision + 1, "request_id": request_id}
        record["digest"] = digest(record)
        account = dict(account, cycles=account["cycles"] + [value["id"]])
        cycle = {"id": value["id"], "plan_digest": record["digest"], "status": "planned", "execution_ids": [],
                 "assessment_id": None, "remaining_obligations": value["scope"]["remaining_obligations"] or [context.objective["statement"]]}
        changes = [immutable_record(records, "cycle_plan", value["id"], record),
                   immutable_record(records, "development_scope", value["scope"]["id"], {"objective": context.objective, "scope": value["scope"]}),
                   ("strategy_account", key, account), ("cycle", value["id"], cycle)]
        return changes, record

    return prepared_mutation(store, "development.plan", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _command(artifacts, value):
    _fields(value, ("argv", "program", "inputs", "versions", "seed", "seed_reason"))
    # Repeated argv values and repeated input bytes are legitimate.
    for argument in _items(value["argv"], "Executed argv", True):
        _text(argument, "Command argument")
    artifacts.read(value["program"])
    for item in _items(value["inputs"], "Frozen execution inputs"):
        artifacts.read(item)
    if not isinstance(value["versions"], dict) or not value["versions"]:
        raise ResearchError("invalid_development", "Record actual tool/runtime versions for the admitted command")
    for key, version in value["versions"].items():
        _text(key, "Tool name")
        _text(version, "Tool version")
    if value["seed"] is None:
        _text(value["seed_reason"], "Reason a seed is inapplicable or unavailable")
    else:
        if type(value["seed"]) not in (int, str):
            raise ResearchError("invalid_development", "A seed must be an integer, text, or null with a reason")
        if isinstance(value["seed"], str):
            _text(value["seed"], "Seed")
        if value["seed_reason"] is not None:
            _text(value["seed_reason"], "Seed explanation")


def admit_execution(store, payload, *, expected_revision, request_id):
    """Reserve one run and its resources before launch; never an instruction to relaunch.

Task 6 must durably claim the run identity once before the side effect and use
the admitted program/input bytes and argv. Unknown outcomes require recovery of
that run. An idempotent admission response retains its original reservation.
"""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        plan = _get(records, "cycle_plan", value.get("cycle_id"), "unknown_cycle")
        _fields(value, ("id", "cycle_id", "plan_digest", "command", "reserved_units"))
        _text(value["id"], "Run/admission ID")
        context = _Context(records, artifacts)
        context.require_ready()
        if value["plan_digest"] != plan["digest"]:
            raise ResearchError("plan_digest_mismatch", "Admit the exact immutable plan the launcher will execute")
        account = records["strategy_account"][plan["strategy_key"]]
        require_accounted_usage(records, artifacts, plan["strategy_key"])
        _validate_reopening(context, plan["payload"], account)
        if _plan_dependencies(context, plan["payload"]) != plan["dependencies"]:
            raise ResearchError("plan_dependencies_stale", "Plan a current development before running after a source, scope or policy change")
        _command(artifacts, value["command"])
        _number(value["reserved_units"], "Reserved resource units", positive=True)
        if _exhausted(account) or account["charged_units"] + value["reserved_units"] > account["limits"]["max_units"]:
            raise ResearchError("strategy_budget_exhausted", "Keep the incomplete work and its original strategy budget; no new execution is admitted")
        for admission in records.get("execution_admission", {}).values():
            if admission["strategy_key"] == plan["strategy_key"] and admission["id"] not in records.get("execution_outcome", {}):
                raise ResearchError("execution_pending", "Reconcile the existing admitted run before launching another producer", {"admission_id": admission["id"]})
        cycle = records["cycle"][value["cycle_id"]]
        if cycle["status"] in ("complete", "failed", "budget_paused"):
            raise ResearchError("cycle_closed", "Use an explicit successor checkpoint and development question after assessing this branch")
        record = dict(value, strategy_key=plan["strategy_key"], admitted_revision=expected_revision + 1, request_id=request_id,
                      dependencies=plan["dependencies"])
        record["digest"] = digest(record)
        account = dict(account, executions=account["executions"] + 1, charged_units=account["charged_units"] + value["reserved_units"])
        changes = [immutable_record(records, "execution_admission", value["id"], record),
                   ("strategy_account", plan["strategy_key"], account), ("cycle", cycle["id"], dict(cycle, status="running"))]
        return changes, record

    return prepared_mutation(store, "development.admit", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _execution_artifacts(artifacts, value):
    if value["command"] is not None:
        _command(artifacts, value["command"])
    if value["origin"]["kind"] == "imported":
        artifacts.read(value["origin"]["original"]["provenance"])
    for output in value["outputs"]:
        artifacts.read(output["artifact"])


def validate_admitted_execution(records, artifacts, admission_id):
    """Recheck a prospective admission immediately before a real launcher spends it.

    This read-only check adds no reservation and returns no proof credit. Original
    admission replay remains historical; outcomes can still be reconciled when
    this current preparation check fails.
    """
    admission = _get(records, "execution_admission", admission_id, "execution_not_admitted")
    plan = _get(records, "cycle_plan", admission["cycle_id"], "unknown_cycle")
    context = _Context(records, artifacts)
    context.require_ready()
    account = records["strategy_account"][plan["strategy_key"]]
    require_accounted_usage(records, artifacts, plan["strategy_key"])
    _validate_reopening(context, plan["payload"], account)
    if _plan_dependencies(context, plan["payload"]) != plan["dependencies"]:
        raise ResearchError("plan_dependencies_stale", "Plan a current development before launching after a source, scope or policy change")
    if admission["plan_digest"] != plan["digest"] or admission["dependencies"] != plan["dependencies"]:
        raise ResearchError("plan_digest_mismatch", "Admission does not bind the current immutable plan")
    _command(artifacts, admission["command"])
    if admission_id in records.get("execution_outcome", {}):
        raise ResearchError("execution_already_recorded", "A recorded admission cannot launch again")
    return admission


def _checkpoint_record(records, artifacts, dependencies, identifier, cycle, assessment, reason, next_hypothesis, revision, request_id):
    plan = records["cycle_plan"][cycle["id"]]
    content = {"id": identifier, "cycle_id": cycle["id"], "objective": plan["payload"]["objective"],
               "scope": assessment["payload"]["scope"] if assessment else plan["payload"]["scope"],
               "predecessor": plan["payload"]["predecessor"], "inheritance": plan["payload"]["inheritance"],
               "plan_digest": plan["digest"], "assessment_id": assessment["id"] if assessment else None,
               "assessment_digest": assessment["digest"] if assessment else None,
               "execution_ids": cycle["execution_ids"],
               "execution_digests": {i: records["execution"][i]["digest"] for i in cycle["execution_ids"]},
               "dependencies": dependencies, "status": "candidate" if assessment and assessment["complete"] else "incomplete",
               "remaining_obligations": cycle["remaining_obligations"], "reason": reason, "next_hypothesis": next_hypothesis,
               "obligations": assessment["obligations"] if assessment else [obligation("development_assessment_missing", "The retained execution needs a current development assessment.")],
               "created_revision": revision, "request_id": request_id}
    if assessment:
        content["evidence"] = assessment["evidence"]
        content["findings"] = assessment["payload"]["findings"]
        content["failures"] = assessment["payload"]["failures"]
    artifact = artifacts.put(json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                         separators=(",", ":")).encode(), "application/json")
    return dict(content, artifact=artifact, digest=digest(content))


def record_execution(store, payload, *, expected_revision, request_id):
    """Retain the actual outcome of one admitted run, or explicitly imported history."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        plan = _get(records, "cycle_plan", value.get("cycle_id"), "unknown_cycle")
        origin = value.get("origin")
        if not isinstance(origin, dict):
            raise ResearchError("invalid_development", "Retain an explicit managed or imported execution origin")
        _choice(origin.get("kind"), ("managed", "imported"), "Execution origin")
        admission = None
        if origin["kind"] == "managed":
            _text(origin.get("admission_id"), "Existing admission ID")
            admission = records.get("execution_admission", {}).get(origin.get("admission_id"))
            if admission is None:
                raise ResearchError("execution_not_admitted", "Reserve and bind the plan, run and program before managed execution")
            _fields(origin, ("kind", "admission_id"))
            if admission["cycle_id"] != plan["id"] or value.get("command") != admission["command"] or admission["plan_digest"] != plan["digest"]:
                raise ResearchError("execution_identity_mismatch", "The actual outcome must match its admitted plan, program, inputs and command")
            if admission["id"] in records.get("execution_outcome", {}):
                raise ResearchError("execution_already_recorded", "This admitted run already has an immutable outcome; replay its original request")
        else:
            _fields(origin, ("kind", "original", "reason", "deduction"))
            _fields(origin["original"], ("run_id", "executed_at", "provenance"))
            _text(origin["original"]["run_id"], "Original run identity")
            if origin["original"]["executed_at"] is not None:
                timestamp(origin["original"]["executed_at"])
            artifacts.read(origin["original"]["provenance"])
            _text(origin["reason"], "Explicit import/adoption reason")
            _text(origin["deduction"], "Prior evidence contribution and chronological limits")
        _fields(value, ("id", "cycle_id", "origin", "command", "status", "exit_code", "usage", "outputs", "notes"))
        _text(value["id"], "Execution ID")
        _text(value["notes"], "Actual execution notes")
        _choice(value["status"], ("completed", "failed", "timed_out", "interrupted", "partial"), "Process outcome")
        if value["exit_code"] is not None and type(value["exit_code"]) is not int:
            raise ResearchError("invalid_development", "An observed exit code is an integer, or null when unavailable")
        if value["status"] == "completed" and value["exit_code"] != 0:
            raise ResearchError("invalid_development", "A completed process needs its observed zero exit code; this is not result verification")
        _fields(value["usage"], ("units", "reason"))
        _text(value["usage"]["reason"], "Resource observation or uncertainty")
        units = value["usage"]["units"]
        if units is not None:
            _number(units, "Observed resource units")
        elif admission and value["status"] == "completed":
            raise ResearchError("invalid_development", "Record resource use for a completed managed run")
        requirements = {r["id"]: r for r in plan["payload"]["evidence_requirements"]}
        seen = set()
        for output in _items(value["outputs"], "Retained result, validation and failure outputs"):
            _fields(output, ("id", "requirement_id", "artifact"))
            _text(output["id"], "Output ID")
            _text(output["requirement_id"], "Planned evidence requirement ID")
            if output["id"] in seen or output["requirement_id"] not in requirements:
                raise ResearchError("invalid_development", "Each output needs a unique identity and a planned evidence requirement")
            seen.add(output["id"])
        # Completed output recording must work after policy/literature changes:
        # it preserves actual history, which a fresh assessment may revalidate.
        _execution_artifacts(artifacts, value)
        record = {"id": value["id"], "payload": value, "plan_digest": plan["digest"],
                  "recorded_revision": expected_revision + 1, "request_id": request_id,
                  "admitted_revision": admission["admitted_revision"] if admission else None}
        record["digest"] = digest(record)
        cycle = records["cycle"][plan["id"]]
        changes = [immutable_record(records, "execution", value["id"], record)]
        account = records["strategy_account"][plan["strategy_key"]]
        if admission:
            overrun = max(0, units - admission["reserved_units"]) if units is not None else 0
            account = dict(account, charged_units=account["charged_units"] + overrun)
            changes.extend([("strategy_account", plan["strategy_key"], account),
                            ("execution_outcome", admission["id"], {"execution_id": value["id"]})])
        paused = admission is not None and _exhausted(account) and value["status"] != "completed"
        cycle = dict(cycle, execution_ids=cycle["execution_ids"] + [value["id"]], status="budget_paused" if paused else "executed")
        changes.append(("cycle", cycle["id"], cycle))
        if paused:
            projected = dict(records, execution=dict(records.get("execution", {}), **{value["id"]: record}))
            # This is a historical incomplete checkpoint. Its bound admission
            # snapshot must survive later unavailable/corrupt literature. It
            # makes no current eligibility decision and is never selected.
            checkpoint = _checkpoint_record(projected, artifacts, admission["dependencies"], "interrupted:" + value["id"], cycle, None,
                "The incomplete run retains its charged reservation and exhausted strategy budget.", None, expected_revision + 1, request_id)
            changes.append(immutable_record(records, "checkpoint", checkpoint["id"], checkpoint))
        return changes, record

    return prepared_mutation(store, "development.execution", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _assessed_checks(evidence, values, expected, identity, target_claim=None):
    seen = set()
    observed = []
    for value in _items(values, "Planned outcome/failure assessment", True):
        keys = (identity, "status", "explanation", "evidence") + (("target_claim",) if target_claim is not None else ())
        _fields(value, keys)
        _text(value[identity], "Planned outcome/failure ID")
        if value[identity] in seen or value[identity] not in expected:
            raise ResearchError("invalid_development", "Assess each exact planned outcome/failure identity once")
        seen.add(value[identity])
        _choice(value["status"], ("observed", "not_observed", "unresolved"), "Observed outcome/failure")
        _text(value["explanation"], "Outcome/failure interpretation")
        evidence.many(value["evidence"], "Outcome/failure evidence")
        if target_claim is not None and value["target_claim"] != target_claim:
            raise ResearchError("failure_scope_mismatch", "Judge failure against the planned target claim or estimand, not a different component or use")
        if value["status"] != "not_observed":
            observed.append(value)
    if seen != set(expected):
        raise ResearchError("invalid_development", "Retain an assessment for every planned outcome and failure signal")
    return observed


def _development(context, evidence, value, scope, cycle_id):
    if value is None:
        return [obligation("development_assessment_missing", "Assess the bottleneck, novelty, contribution and next useful developments before readiness.")]
    _fields(value, ("bottleneck_change", "next_question", "strategy", "reason", "novelty", "contribution", "alternatives", "branches"))
    for key in ("bottleneck_change", "reason"):
        _text(value[key], "Development " + key)
    _choice(value["strategy"], STRATEGIES + ("none",), "Next development strategy")
    if value["strategy"] == "none":
        if value["next_question"] is not None:
            raise ResearchError("invalid_development", "A proposed next question requires a selected strategy")
    else:
        _text(value["next_question"], "Next useful development question")
    novelty = value["novelty"]
    _fields(novelty, ("scope", "comparison", "evidence", "gaps"), ("literature_digest", "foundation_digest"))
    _text(novelty["comparison"], "Current novelty comparison")
    _strings(novelty["gaps"], "Unresolved novelty comparison")
    linked = evidence.many(novelty["evidence"], "Novelty evidence")
    obligations = []
    if not any(x["reference"]["kind"] == "source" for x in linked):
        obligations.append(obligation("novelty_source_missing", "Compare the candidate with actual current full-read sources."))
    if novelty["scope"] != scope or novelty.get("literature_digest") != context.synthesis["literature_digest"] or novelty["gaps"]:
        obligations.append(obligation("novelty_comparison_stale", "Refresh the novelty decision for this exact result claim/scope and current literature."))
    contribution = value["contribution"]
    _fields(contribution, ("and", "but", "therefore", "evidence"))
    for key in ("and", "but", "therefore"):
        _text(contribution[key], "Evidence-bound contribution " + key)
    contribution_evidence = evidence.many(contribution["evidence"], "Contribution evidence")
    if not {"source", "result"} <= {x["reference"]["kind"] for x in contribution_evidence}:
        obligations.append(obligation("contribution_evidence_missing", "Bind the contribution to its established source context and actual result."))
    for option in _items(value["alternatives"], "Useful alternative developments", True):
        _fields(option, ("strategy", "question", "disposition", "reason", "evidence"))
        _choice(option["strategy"], STRATEGIES, "Alternative strategy")
        _choice(option["disposition"], DISPOSITIONS, "Alternative disposition")
        _text(option["question"], "Alternative question")
        _text(option["reason"], "Alternative scientific value or limitation")
        evidence.many(option["evidence"], "Alternative decision evidence")
        if option["disposition"] in ("pursue", "budget_paused"):
            obligations.append(obligation("useful_development_remaining", "Complete or substantively resolve the remaining useful development.", question=option["question"]))
    seen = set()
    for branch in _items(value["branches"], "Actual branch dispositions", True):
        _fields(branch, ("cycle_id", "disposition", "reason", "evidence"))
        _get(context.records, "cycle", branch["cycle_id"], "unknown_cycle")
        if branch["cycle_id"] in seen:
            raise ResearchError("invalid_development", "Assess each actual branch once")
        seen.add(branch["cycle_id"])
        _choice(branch["disposition"], DISPOSITIONS, "Branch disposition")
        _text(branch["reason"], "Branch disposition reason")
        evidence.many(branch["evidence"], "Branch disposition evidence")
        if branch["disposition"] in ("pursue", "budget_paused"):
            obligations.append(obligation("useful_branch_remaining", "Retain unfinished useful branches and their obligations.", cycle_id=branch["cycle_id"]))
    if cycle_id not in seen:
        obligations.append(obligation("branch_disposition_missing", "Include the candidate's own branch in the development assessment.", cycle_id=cycle_id))
    if value["strategy"] != "none":
        obligations.append(obligation("useful_development_remaining", "Develop the stated next useful question before concluding this candidate.", question=value["next_question"]))
    return obligations


def carried_developments(records, cycle_ids=None):
    """The `next_round` alternatives and branches of each cycle's current assessment."""
    carried = []
    for cycle in records.get("cycle", {}).values():
        if cycle["assessment_id"] is None or cycle_ids is not None and cycle["id"] not in cycle_ids:
            continue
        development = records["cycle_assessment"][cycle["assessment_id"]]["payload"]["development"]
        if development is None:
            continue
        for option in development["alternatives"]:
            if option["disposition"] == "next_round":
                carried.append({"assessment_id": cycle["assessment_id"], "kind": "alternative", "question": option["question"],
                                "strategy": option["strategy"], "reason": option["reason"]})
        for branch in development["branches"]:
            if branch["disposition"] == "next_round":
                carried.append({"assessment_id": cycle["assessment_id"], "kind": "branch", "cycle_id": branch["cycle_id"],
                                "reason": branch["reason"]})
    return sorted(carried, key=lambda item: (item["assessment_id"], item["kind"], item.get("question") or item.get("cycle_id")))


def _assess(context, value):
    _fields(value, ("id", "cycle_id", "author", "scope", "execution_ids", "result", "validity_checks", "outcomes", "failures",
                    "findings", "assumptions", "remaining_obligations", "objective_status", "disposition", "development"))
    for key in ("id", "author"):
        _text(value[key], "Assessment " + key)
    plan = _get(context.records, "cycle_plan", value["cycle_id"], "unknown_cycle")
    cycle = context.records["cycle"][value["cycle_id"]]
    _scope(value["scope"], context.objective)
    _strings(value["assumptions"], "Result assumptions")
    if not set(value["scope"]["assumptions"]) <= set(value["assumptions"]):
        raise ResearchError("scope_assumptions_missing", "Retain the declared scope assumptions in the assessed result")
    _strings(value["remaining_obligations"], "Remaining objective obligations")
    _strings(value["execution_ids"], "Assessed executions")
    _choice(value["objective_status"], ("open", "achieved"), "Complete-objective status")
    _choice(value["disposition"], ("continue", "complete", "failed", "budget_paused"), "Branch disposition")
    evidence = _Evidence(context)
    _plan_evidence(evidence, plan["payload"])
    _fields(value["result"], ("statement", "evidence"))
    _text(value["result"]["statement"], "What the evidence establishes")
    result_evidence = evidence.many(value["result"]["evidence"], "Actual result evidence")
    valid_obligations = []
    if value["scope"]["kind"] == "full" and value["scope"] != plan["payload"]["scope"]:
        valid_obligations.append(obligation("result_scope_unplanned", "Use a distinct prospective plan for a changed full scope or weaker assumptions; retain the original execution scope."))
    if set(value["execution_ids"]) != set(cycle["execution_ids"]) or not value["execution_ids"]:
        valid_obligations.append(obligation("execution_coverage_missing", "Assess all actual executions, retaining failed and negative outcomes."))
    executions = []
    for identifier in value["execution_ids"]:
        execution = _get(context.records, "execution", identifier, "unknown_execution")
        if execution["payload"]["cycle_id"] != cycle["id"]:
            raise ResearchError("result_evidence_mismatch", "List this cycle's executions; inherit earlier branches through checkpoints")
        _execution_artifacts(context.artifacts, execution["payload"])
        executions.append(execution)
    supplied_requirements = {o["requirement_id"] for execution in executions for o in execution["payload"]["outputs"]}
    missing_requirements = sorted({r["id"] for r in plan["payload"]["evidence_requirements"]} - supplied_requirements)
    if missing_requirements:
        valid_obligations.append(obligation("required_evidence_missing", "Retain the actual output for every declared evidence requirement.",
                                            requirement_ids=missing_requirements))
    inherited_refs = {digest(e) for i in plan["payload"]["inheritance"] for e in i["evidence"]}
    for item in result_evidence:
        if item["reference"]["kind"] == "result" and item["cycle_id"] != cycle["id"] and digest(item["reference"]) not in inherited_refs:
            raise ResearchError("inheritance_missing", "Explain a prior result's contribution and assumptions through a checkpoint deduction")
    own_results = [x for x in result_evidence if x.get("cycle_id") == cycle["id"] and x["requirement_kind"] == "result"]
    if not own_results:
        valid_obligations.append(obligation("result_evidence_missing", "Bind the claimed result to this cycle's actual planned result outputs."))
    if any(x["status"] != "completed" for x in own_results):
        valid_obligations.append(obligation("result_execution_incomplete", "Preserve the incomplete output without certifying a complete result."))
    required_results = {r["id"] for r in plan["payload"]["evidence_requirements"] if r["kind"] == "result"}
    missing_results = sorted(required_results - {r["requirement_id"] for r in own_results if r["status"] == "completed"})
    if missing_results:
        valid_obligations.append(obligation("required_result_unverified", "Assess completed, referenced result evidence for every declared result requirement.",
                                            requirement_ids=missing_results))
    checks = _items(value["validity_checks"], "Result validity checks")
    if not checks:
        valid_obligations.append(obligation("validity_check_missing", "Process completion alone does not verify a scientific result."))
    seen = set()
    validated_outputs = set()
    validated_requirements = set()
    for check in checks:
        _fields(check, ("id", "question", "method", "status", "explanation", "evidence"))
        for key in ("id", "question", "method", "explanation"):
            _text(check[key], "Validity " + key)
        if check["id"] in seen:
            raise ResearchError("invalid_development", "Validity checks need distinct identities")
        seen.add(check["id"])
        _choice(check["status"], ("passed", "failed", "unresolved"), "Validity outcome")
        checked = evidence.many(check["evidence"], "Validity check evidence")
        if check["status"] != "passed" or not any(x.get("requirement_kind") == "validation" and x.get("status") == "completed" for x in checked):
            valid_obligations.append(obligation("validity_unresolved", "Resolve the actual validity check with planned validation evidence.", check_id=check["id"]))
        else:
            for validation in checked:
                if validation.get("requirement_kind") != "validation" or validation.get("status") != "completed":
                    continue
                validating_run = context.records["execution"][validation["reference"]["execution_id"]]["payload"]
                inputs = validating_run["command"]["inputs"] if validating_run["command"] is not None else []
                bound = False
                for result in own_results:
                    reference = result["reference"]
                    if (reference["execution_id"] == validation["reference"]["execution_id"] or reference["artifact"] in inputs):
                        validated_outputs.add(digest(reference))
                        bound = True
                if bound and validation["cycle_id"] == cycle["id"]:
                    validated_requirements.add(validation["requirement_id"])
    if checks and {digest(x["reference"]) for x in own_results} - validated_outputs:
        valid_obligations.append(obligation("validity_evidence_unbound", "Use validation from the result's own run or a checker with these exact result bytes as admitted inputs."))
    required_validations = {r["id"] for r in plan["payload"]["evidence_requirements"] if r["kind"] == "validation"}
    missing_validations = sorted(required_validations - validated_requirements)
    if missing_validations:
        valid_obligations.append(obligation("required_validation_unverified", "Complete and pass a result-bound assessment of every declared validation requirement.",
                                            requirement_ids=missing_validations))
    outcomes = _assessed_checks(evidence, value["outcomes"], {x["id"] for x in plan["payload"]["expected_outcomes"]}, "outcome_id")
    failed = _assessed_checks(evidence, value["failures"], {x["id"] for x in plan["payload"]["failure_signals"]},
                              "signal_id", plan["payload"]["hypothesis"])
    for finding in _items(value["findings"], "Positive, negative and unresolved findings"):
        _fields(finding, ("statement", "scope", "source_support", "scientific_status", "evidence", "uncertainties"))
        for key in ("statement", "scope"):
            _text(finding[key], "Scoped finding " + key)
        _choice(finding["source_support"], ("supported_as_scoped", "source_not_supported", "source_contradicted", "unresolved"), "Source support")
        _choice(finding["scientific_status"], ("established", "proposed", "unresolved", "refuted"), "Scientific finding")
        _strings(finding["uncertainties"], "Finding uncertainty", finding["source_support"] != "supported_as_scoped")
        evidence.many(finding["evidence"], "Scoped finding evidence")
    inherited = _inheritance(context, evidence, plan["payload"])
    obligations = list(valid_obligations) + list(context.synthesis["obligations"])
    remaining = list(dict.fromkeys(value["scope"]["remaining_obligations"] + value["remaining_obligations"]))
    # A refuted hypothesis can produce a valid negative result. Result
    # validity comes from the separate evidence-linked checks, not whether
    # the expected scientific hypothesis survived its distinguishing test.
    unresolved_outcomes = [o["outcome_id"] for o in outcomes if o["status"] == "unresolved"]
    if unresolved_outcomes:
        obligations.append(obligation("expected_outcome_unresolved", "Resolve the explicitly uncertain planned outcomes before completing this candidate.",
                                      outcomes=unresolved_outcomes))
    unresolved = [f for f in failed if f["status"] == "unresolved"]
    if unresolved:
        obligations.append(obligation("failure_signal_unresolved", "Resolve the uncertainty about the planned failure conditions.",
                                      signals=[f["signal_id"] for f in unresolved]))
    for execution in executions:
        origin = execution["payload"]["origin"]
        if origin["kind"] == "managed":
            admission = context.records["execution_admission"][origin["admission_id"]]
            units = execution["payload"]["usage"]["units"]
            if units is not None and units > admission["reserved_units"]:
                obligations.append(obligation("resource_limit_exceeded", "Retain the result and the actual overrun without satisfying the admitted resource contract.",
                                              admission_id=admission["id"], reserved_units=admission["reserved_units"], used_units=units))
                remaining.append("Resolve the exceeded resource contract for run " + admission["id"] + ".")
    if value["scope"]["kind"] != "full" or plan["payload"]["scope"]["kind"] != "full":
        obligations.append(obligation("objective_scope_incomplete", "A special case contributes to the fixed complete objective but cannot close it."))
        remaining = list(dict.fromkeys(remaining + plan["payload"]["scope"]["remaining_obligations"]))
        if not remaining:
            remaining.append(context.objective["statement"])
    if value["objective_status"] != "achieved" or value["disposition"] != "complete":
        obligations.append(obligation("objective_incomplete", "Retain the complete objective until all of its obligations are established."))
        if not remaining:
            remaining.append(context.objective["statement"])
    if remaining:
        obligations.append(obligation("remaining_obligations", "Complete the retained objective obligations.", remaining=remaining))
    if not any(x["origin"]["kind"] == "managed" for x in own_results):
        obligations.append(obligation("prospective_execution_missing", "Imported evidence can motivate development but is not a new prospective test."))
    for admission in context.records.get("execution_admission", {}).values():
        if admission["cycle_id"] == cycle["id"] and admission["id"] not in context.records.get("execution_outcome", {}):
            obligations.append(obligation("execution_pending", "Reconcile the admitted run's actual outcome.", admission_id=admission["id"]))
    obligations.extend(_development(context, evidence, value["development"], value["scope"], cycle["id"]))
    dependencies = dict(context.dependencies(), plan=plan["digest"], executions=digest(executions),
                        cycle_executions=digest(cycle["execution_ids"]), inheritance=inherited, evidence=digest(evidence.summary()))
    obligations = _unique(obligations)
    return {"id": value["id"], "payload": value, "validated_result": not valid_obligations,
            "complete": not obligations, "obligations": obligations, "remaining_obligations": remaining,
            "evidence": evidence.summary(), "dependencies": dependencies,
            "digest": digest({"payload": value, "dependencies": dependencies}), "mechanical_only": True}


def assess_cycle(store, payload, *, expected_revision, request_id):
    """Retain the result's validity, exact scope, failures and next development."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context = _Context(records, artifacts)
        context.require_objective()
        report = _assess(context, value)
        cycle = records["cycle"][value["cycle_id"]]
        plan = records["cycle_plan"][cycle["id"]]
        account = records["strategy_account"][plan["strategy_key"]]
        paused = not report["complete"] and (value["disposition"] == "budget_paused" or _exhausted(account))
        status = "complete" if report["complete"] else "budget_paused" if paused else "failed" if value["disposition"] == "failed" else "assessed"
        cycle = dict(cycle, status=status, assessment_id=value["id"], remaining_obligations=report["remaining_obligations"])
        record = {"id": value["id"], "payload": value, "dependencies": report["dependencies"], "assessment": report,
                  "assessed_revision": expected_revision + 1, "request_id": request_id}
        failures = account["failures"] + [{"assessment_id": value["id"], "signal_id": f["signal_id"]}
                                             for f in value["failures"] if f["status"] == "observed"]
        changes = [immutable_record(records, "cycle_assessment", value["id"], record),
                   immutable_record(records, "development_scope", value["scope"]["id"], {"objective": context.objective, "scope": value["scope"]}),
                   ("cycle", cycle["id"], cycle), ("strategy_account", plan["strategy_key"], dict(account, failures=failures))]
        if paused:
            checkpoint = _checkpoint_record(records, artifacts, context.dependencies(), "paused:" + value["id"], cycle, report,
                "The branch is incomplete and paused under its retained resource limits.",
                value["development"]["next_question"] if value["development"] else None, expected_revision + 1, request_id)
            changes.append(immutable_record(records, "checkpoint", checkpoint["id"], checkpoint))
        return changes, report

    return prepared_mutation(store, "development.assess", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def checkpoint(store, payload, *, expected_revision, request_id):
    """Archive an immutable development checkpoint; optionally select the candidate."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        _fields(value, ("id", "cycle_id", "assessment_id", "reason", "next_hypothesis", "select_for_readiness"))
        _text(value["id"], "Checkpoint ID")
        _text(value["reason"], "Checkpoint progress or incomplete state")
        if value["next_hypothesis"] is not None:
            _text(value["next_hypothesis"], "Next hypothesis")
        if type(value["select_for_readiness"]) is not bool:
            raise ResearchError("invalid_development", "Select a candidate explicitly with a boolean")
        context = _Context(records, artifacts)
        context.require_objective()
        cycle = _get(records, "cycle", value["cycle_id"], "unknown_cycle")
        if value["assessment_id"] != cycle["assessment_id"]:
            raise ResearchError("checkpoint_assessment_mismatch", "Checkpoint the current assessment without erasing its predecessors")
        assessed = context.assessment(value["assessment_id"]) if value["assessment_id"] is not None else None
        record = _checkpoint_record(records, artifacts, context.dependencies(), value["id"], cycle, assessed,
                                    value["reason"], value["next_hypothesis"], expected_revision + 1, request_id)
        changes = [immutable_record(records, "checkpoint", value["id"], record)]
        if value["select_for_readiness"]:
            changes.append(("development_selection", "candidate", {"checkpoint_id": value["id"]}))
        return changes, record

    return prepared_mutation(store, "development.checkpoint", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _branch_inputs(context):
    branches = {}
    for identifier, cycle in context.records.get("cycle", {}).items():
        plan = context.records["cycle_plan"][identifier]
        executions = {i: context.records["execution"][i] for i in cycle["execution_ids"]}
        for execution in executions.values():
            _execution_artifacts(context.artifacts, execution["payload"])
        branches[identifier] = {"cycle": cycle, "plan": plan,
            "plan_evidence": _plan_evidence(_Evidence(context), plan["payload"]),
            "executions": executions, "assessment": context.assessment(cycle["assessment_id"]) if cycle["assessment_id"] is not None else None,
            "admissions": {i: a for i, a in context.records.get("execution_admission", {}).items() if a["cycle_id"] == identifier},
            "checkpoints": {i: c for i, c in context.records.get("checkpoint", {}).items() if c["cycle_id"] == identifier}}
    return branches


def _source_inputs(context, evidence):
    identifiers = {e["reference"]["link"]["version_id"] for e in evidence if e["reference"]["kind"] == "source"}
    identifiers.update(i["version_id"] for i in context.foundation["inventory"])
    for section in context.synthesis["sections"].values():
        identifiers.update(e["link"]["version_id"] for e in section.get("evidence", []))
    for branch in context.branches.values():
        identifiers.update(link["version_id"] for link in branch["plan"]["payload"]["literature"]["sources"])
    records = context.records
    works = {i: records["work"][i] for i in sorted(identifiers)}
    families = {work["work_id"] for work in works.values()}
    assertions = {i for work in works.values() for i in work["assertion_ids"]}
    selected = {"work": works, "work_assertion": {i: records["work_assertion"][i] for i in sorted(assertions)},
                "alias": {i: a for i, a in records.get("alias", {}).items() if any(x["work_id"] in families for x in a["assertions"])},
                "reading": {i: r for i, r in records.get("reading", {}).items() if r["version_id"] in identifiers},
                "source_bundle": {i: b for i, b in records.get("source_bundle", {}).items() if b["version_id"] in identifiers}}
    source_ids = set()

    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("source_id"), str):
                source_ids.add(value["source_id"])
            source_ids.update(value.get("source_ids", []))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(selected)
    collect(context.foundation)
    selected["source"] = {i: records["source"][i] for i in sorted(source_ids)}
    return selected


def _candidate(context):
    selection = context.records.get("development_selection", {}).get("candidate")
    if selection is None:
        return None, [obligation("candidate_checkpoint_missing", "Select a durable assessed checkpoint for readiness.")]
    checkpoint = _get(context.records, "checkpoint", selection["checkpoint_id"], "unknown_checkpoint")
    context.artifacts.read(checkpoint["artifact"])
    if checkpoint["assessment_id"] is None:
        return None, [obligation("development_assessment_missing", "Assess actual results before selecting a readiness candidate.")]
    assessment = context.assessment(checkpoint["assessment_id"])
    context.branches = _branch_inputs(context)
    obligations = list(assessment["obligations"])
    cycle = context.records["cycle"][checkpoint["cycle_id"]]
    if cycle["assessment_id"] != checkpoint["assessment_id"] or checkpoint["assessment_digest"] != assessment["digest"]:
        obligations.append(obligation("candidate_checkpoint_stale", "Checkpoint the currently assessed candidate and its exact evidence."))
    branches = assessment["payload"]["development"]["branches"] if assessment["payload"]["development"] else []
    branch_ids = {b["cycle_id"] for b in branches}
    for identifier in context.records.get("cycle", {}):
        if identifier not in branch_ids:
            obligations.append(obligation("branch_disposition_missing", "Assess the disposition of each retained branch for this candidate.", cycle_id=identifier))
    for branch in branches:
        assessed_branch = context.branches[branch["cycle_id"]]["assessment"]
        if branch["disposition"] == "resolved" and (assessed_branch is None or not assessed_branch["validated_result"]):
            obligations.append(obligation("branch_resolution_unverified", "A declared resolved branch needs its actual currently validated result.", cycle_id=branch["cycle_id"]))
    for admission in context.records.get("execution_admission", {}).values():
        if admission["id"] not in context.records.get("execution_outcome", {}):
            obligations.append(obligation("execution_pending", "Reconcile every admitted run before independent readiness review.", admission_id=admission["id"]))
    all_evidence = {digest(e["reference"]): e for e in assessment["evidence"]}
    for branch in context.branches.values():
        all_evidence.update({digest(e["reference"]): e for e in branch["plan_evidence"]})
        if branch["assessment"] is not None:
            all_evidence.update({digest(e["reference"]): e for e in branch["assessment"]["evidence"]})
    evidence = [all_evidence[k] for k in sorted(all_evidence)]
    context.sources = _source_inputs(context, evidence)
    result_hashes = sorted({e["reference"]["artifact"]["sha256"] for e in evidence if e["reference"]["kind"] == "result"})
    candidate = {"objective": context.objective, "scope": assessment["payload"]["scope"],
                 "checkpoint_id": checkpoint["id"], "checkpoint_digest": checkpoint["digest"],
                 "assessment_id": assessment["id"], "assessment_digest": assessment["digest"],
                 "preparation_digest": context.synthesis["preparation_digest"],
                 "constitution": context.configuration["constitution"], "result_hashes": result_hashes,
                 "evidence": [e["reference"] for e in evidence], "branches_digest": digest(context.branches),
                 "strategy_accounts_digest": digest(context.records.get("strategy_account", {})),
                 "source_records_digest": digest(context.sources),
                 "authors": sorted({p["payload"]["author"] for p in context.records.get("cycle_plan", {}).values()}
                                   | {a["payload"]["author"] for a in context.records.get("cycle_assessment", {}).values()})}
    candidate["digest"] = digest(candidate)
    return candidate, _unique(obligations)


def _review(context, value, candidate):
    _fields(value, ("id", "candidate_digest", "assessor", "verdict", "checks", "limitations"))
    _text(value["id"], "Readiness review ID")
    if value["candidate_digest"] != candidate["digest"]:
        raise ResearchError("readiness_review_stale", "Assess the exact current candidate, objective, literature and result bytes")
    assessor = value["assessor"]
    _fields(assessor, ("id", "kind", "provenance", "relationship", "independence_basis"))
    for key in ("id", "relationship", "independence_basis"):
        _text(assessor[key], "Independent assessor " + key)
    _choice(assessor["kind"], ("human", "agent"), "Assessor kind")
    context.artifacts.read(assessor["provenance"])
    if _normalized(assessor["id"]) in {_normalized(a) for a in candidate["authors"]}:
        raise ResearchError("review_not_independent", "The cycle author's own assessment cannot supply independent readiness review")
    _choice(value["verdict"], ("ready", "not_ready", "unresolved"), "Independent readiness verdict")
    _strings(value["limitations"], "Independent assessment limits", True)
    evidence = _Evidence(context)
    seen, obligations = set(), []
    for check in _items(value["checks"], "Independent candidate checks", True):
        _fields(check, ("kind", "status", "reason", "evidence"))
        _choice(check["kind"], _REVIEW_CHECKS, "Readiness check")
        if check["kind"] in seen:
            raise ResearchError("invalid_development", "Assess each independent readiness check once")
        seen.add(check["kind"])
        _choice(check["status"], ("passed", "failed", "unresolved"), "Independent check outcome")
        _text(check["reason"], "Independent check reasoning")
        evidence.many(check["evidence"], "Independent review evidence")
        if check["status"] != "passed":
            obligations.append(obligation("independent_review_pending", "Resolve the independent evidence-linked review finding.", check=check["kind"], status=check["status"]))
    if seen != set(_REVIEW_CHECKS):
        raise ResearchError("invalid_development", "Independent review must address validity, scope, novelty, contribution, development and branches")
    referenced = {digest(e["reference"]) for e in evidence.summary()}
    if not {digest(e) for e in candidate["evidence"]} <= referenced:
        raise ResearchError("review_evidence_incomplete", "Independently assess every exact evidence reference supplied by the candidate")
    if value["verdict"] != "ready":
        obligations.append(obligation("independent_review_pending", "The independent assessor has not accepted this candidate.", verdict=value["verdict"]))
    return {"ready": not obligations, "obligations": _unique(obligations), "evidence": evidence.summary(),
            "digest": digest({"payload": value, "candidate": candidate["digest"], "evidence": evidence.summary()}),
            "mechanical_only": True}


def record_readiness_review(store, payload, *, expected_revision, request_id):
    """Archive an identified independent assessment of the exact candidate snapshot."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        context = _Context(records, artifacts)
        context.require_objective()
        candidate, _ = _candidate(context)
        if candidate is None:
            raise ResearchError("candidate_checkpoint_missing", "Provide a current assessed candidate for independent review")
        report = _review(context, value, candidate)
        artifact = artifacts.put(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                              separators=(",", ":")).encode(), "application/json")
        record = {"id": value["id"], "payload": value, "candidate": candidate, "assessment": report,
                  "artifact": artifact, "reviewed_revision": expected_revision + 1, "request_id": request_id}
        return [immutable_record(records, "readiness_review", value["id"], record),
                ("development_selection", "review", {"review_id": value["id"]})], report

    return prepared_mutation(store, "development.readiness_review", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def readiness_state(records, artifacts):
    """Assess current author readiness on a consistent records/artifacts snapshot.

The candidate and review_inputs are the independent review boundary. They bind
source locators/readings, objective/scope, policy, literature, result hashes,
development and retained branch state. No global-revision or cycle-count quota
substitutes for these requirements.
"""
    obligations, candidate, synthesis, reviews = [], None, None, None
    cycles = records.get("cycle", {})
    try:
        context = _Context(records, artifacts)
        synthesis = context.synthesis
        obligations.extend(synthesis["obligations"])
        for identifier, cycle in cycles.items():
            if cycle["assessment_id"] is None:
                obligations.append(obligation("development_assessment_missing", "Assess the actual execution and next development.", cycle_id=identifier))
        candidate, pending = _candidate(context)
        obligations.extend(pending)
        if candidate is not None:
            selection = records.get("development_selection", {}).get("review")
            if selection is None:
                obligations.append(obligation("independent_review_missing", "Obtain an independent evidence-linked assessment of this exact candidate."))
            else:
                saved = _get(records, "readiness_review", selection["review_id"], "unknown_review")
                artifacts.read(saved["artifact"])
                reviews = _review(context, saved["payload"], candidate)
                if reviews["digest"] != saved["assessment"]["digest"]:
                    obligations.append(obligation("readiness_review_stale", "Reassess changed independent source/result evidence."))
                obligations.extend(reviews["obligations"])
    except ResearchError as error:
        obligations.append(obligation(error.code, error.message, **(error.details or {})))
    obligations = _unique(obligations)
    private_profile = records.get("configuration", {}).get("research", {}).get("profile") == "verification"
    return {"ready": not obligations, "candidate": candidate, "synthesis": synthesis,
            "obligations": obligations, "counts": {"cycles": 0 if private_profile else len(cycles), "obligations": len(obligations)},
            "review": reviews, "next": obligations[0] if obligations else None,
            "review_inputs": None if candidate is None else {"candidate": candidate, "branches": context.branches, "sources": context.sources,
                "synthesis": context.synthesis, "strategy_accounts": records.get("strategy_account", {}),
                "plan": records["cycle_plan"][records["checkpoint"][candidate["checkpoint_id"]]["cycle_id"]],
                "assessment": records["cycle_assessment"][candidate["assessment_id"]],
                "checkpoint": records["checkpoint"][candidate["checkpoint_id"]],
                "source_and_result_references": candidate["evidence"]},
            "mechanical_only": True, "native_proof_acceptance": False, "limits": _LIMITS}


def readiness_report(store):
    snapshot = store.snapshot()
    evaluation = Evaluation(snapshot["records"], ArtifactStore(store.root))
    return dict(readiness_state(snapshot["records"], evaluation), revision=snapshot["revision"])
