"""Authoritative study configuration and explicit constitution adoption.

configuration/research contains {profile, target, constitution,
preparation_policy}. Policy bytes are archived in constitution/{sha256};
research_objective/{id} fixes the full objective independently of branches;
objective_lineage/{id} records a widening admitted by a round, with the
predecessor objective id, so the earlier objective stays retained.
The preparation policy (exhaustive-v1 by default, or screened-v1) decides which
population members owe structured reading; a configuration without the field is
exhaustive-v1. A policy adoption or change does not relabel old decisions: their
bound dependencies require new current assessments.
"""

import hashlib
import re
from pathlib import Path

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .graph import main_captures, obligation, validate_target
from .operations import fields, immutable_record, prepared_mutation, profile_name, text
from .source_links import captured_source, exact_work


CONSTITUTION_PATH = Path(__file__).resolve().parent.parent / "RESEARCH_CONSTITUTION.md"
POLICIES = ("exhaustive-v1", "screened-v1")
DEFAULT_POLICY = "exhaustive-v1"


def preparation_policy(records):
    """The study's recorded preparation policy; studies from earlier releases are exhaustive."""
    config = records.get("configuration", {}).get("research") or {}
    return (config.get("preparation_policy") or {}).get("id", DEFAULT_POLICY)


def _policy(value):
    if value not in POLICIES:
        raise ResearchError("invalid_input", "Preparation policy must be one of: " + ", ".join(POLICIES))
    return {"id": value}


def _constitution():
    try:
        data = CONSTITUTION_PATH.read_bytes()
        versions = re.findall(r"^Version: ([^\r\n]+)$", data.decode("utf-8"), re.MULTILINE)
        if len(versions) != 1 or not versions[0].strip():
            raise ValueError("The policy requires one version declaration")
    except (OSError, UnicodeError, ValueError) as error:
        raise ResearchError("constitution_unavailable", "Read the distributed versioned research constitution before proceeding") from error
    return {"path": "RESEARCH_CONSTITUTION.md", "version": versions[0],
            "sha256": hashlib.sha256(data).hexdigest()}, data


def constitution_contract():
    """Return the distributed policy's stable relative path, version and SHA-256."""
    return _constitution()[0]


def _validate_objective(target):
    fields(target, ("kind", "id", "statement"), code="invalid_target")
    if target["kind"] != "objective":
        raise ResearchError("invalid_target", "A research target must identify the complete objective")
    for key in ("id", "statement"):
        text(target[key], "Objective " + key, code="invalid_target")


def _validate_target(records, artifacts, profile, target):
    if profile == "research":
        if target is not None:
            _validate_objective(target)
        return
    fields(target, ("kind", "id", "source_id", "sha256"), code="invalid_target")
    work = exact_work(records, target["id"])
    if work["id"] != target["id"]:
        raise ResearchError("invalid_target", "Use the canonical exact work ID")
    validate_target(records, target, [work["id"]])
    if target["source_id"] is not None:
        captured_source(records, artifacts, target["source_id"])
        if not main_captures(records, work, target):
            raise ResearchError("invalid_target", "The verification pin must name the original main document, not a supplement")


def _archive_policy(records, artifacts, contract, data):
    return immutable_record(records, "constitution", contract["sha256"],
                            dict(contract, artifact=artifacts.put(data, "text/markdown; charset=utf-8")))


def prepare_initialization(records, artifacts, value):
    """Prepare configuration for an atomic workspace initialization or adoption."""
    fields(value, ("profile", "target"), ("preparation_policy",))
    if records.get("configuration", {}).get("research") is not None:
        raise ResearchError("configuration_exists", "Use explicit target or constitution operations for the existing study")
    profile_name(value["profile"])
    _validate_target(records, artifacts, value["profile"], value["target"])
    contract, data = _constitution()
    config = {"profile": value["profile"], "target": value["target"], "constitution": contract,
              "preparation_policy": _policy(value.get("preparation_policy", DEFAULT_POLICY))}
    changes = [_archive_policy(records, artifacts, contract, data), ("configuration", "research", config)]
    if value["profile"] == "research" and value["target"] is not None:
        changes.append(immutable_record(records, "research_objective", value["target"]["id"], value["target"]))
    return changes, config


def initialize_research(store, payload, *, expected_revision, request_id):
    """Initialize once with {profile, target}; null research targets are pending."""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        return prepare_initialization(records, artifacts, value)

    return prepared_mutation(store, "research.initialize", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def _configuration(records):
    config = records.get("configuration", {}).get("research")
    if config is None:
        raise ResearchError("configuration_missing", "Initialize the study profile and constitution first")
    return config


def set_target(store, payload, *, expected_revision, request_id):
    """Set {target, reason}. A fixed complete research objective cannot narrow.

Verification repinning is explicit and leaves mismatching literature scope
pending until set_roots selects the same target. It preserves earlier events.
"""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("target", "reason"))
        text(value["reason"], "Target decision reason")
        config = _configuration(records)
        if config["profile"] == "research" and config["target"] is not None and config["target"] != value["target"]:
            raise ResearchError("objective_locked", "Keep the complete original objective; record special cases as separate branches")
        _validate_target(records, artifacts, config["profile"], value["target"])
        updated = dict(config, target=value["target"])
        changes = [("configuration", "research", updated)]
        if config["profile"] == "research" and value["target"] is not None:
            changes.append(immutable_record(records, "research_objective", value["target"]["id"], value["target"]))
        return changes, updated

    return prepared_mutation(store, "research.target", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def widen_objective(records, target, lineage, round_id):
    """Changes that widen the research objective through an admitted round; the old objective stays retained.

    `target` is the wider objective, `lineage` is {previous_id, containment}
    naming the current objective and why the wider one contains it. The
    configuration target becomes `target`; research_objective/{target id} and
    objective_lineage/{target id} record the widening. Containment is the
    author's recorded assertion, judged by the round reviewer: the statement
    text gives no mechanical containment check.
    """
    config = _configuration(records)
    current = config["target"]
    fields(lineage, ("previous_id", "containment"), code="invalid_target")
    text(lineage["containment"], "Objective containment", code="invalid_target")
    _validate_objective(target)
    if config["profile"] != "research" or current is None or lineage["previous_id"] != current["id"]:
        raise ResearchError("objective_locked", "Widen the current complete objective through its recorded predecessor")
    if target["statement"] == current["statement"] or target["id"] in records.get("research_objective", {}):
        raise ResearchError("objective_locked", "A widened objective needs a new identity and a wider statement")
    record = {"id": target["id"], "predecessor": current["id"], "containment": lineage["containment"], "round_id": round_id}
    return [("configuration", "research", dict(config, target=target)),
            immutable_record(records, "research_objective", target["id"], target),
            immutable_record(records, "objective_lineage", target["id"], record)]


def revalidate_constitution(store, payload, *, expected_revision, request_id):
    """Adopt current policy with {previous_sha256, reason}, without migrating decisions.

Every active synthesis/development decision must separately be reassessed under
the current contract. Re-recording an assessment preserves its predecessor.
"""
    artifacts = ArtifactStore(store.root)

    def prepare(records, value):
        fields(value, ("previous_sha256", "reason"))
        text(value["reason"], "Current policy assessment")
        config = _configuration(records)
        if value["previous_sha256"] != config["constitution"]["sha256"]:
            raise ResearchError("constitution_conflict", "Revalidate against the policy currently bound to this study")
        contract, data = _constitution()
        updated = dict(config, constitution=contract)
        return [_archive_policy(records, artifacts, contract, data), ("configuration", "research", updated)], dict(
            updated, decisions_revalidated=False)

    return prepared_mutation(store, "research.constitution", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def change_policy(store, payload, *, expected_revision, request_id):
    """Change the preparation policy with {previous, policy, reason}; dependent assessments become stale."""
    def prepare(records, value):
        fields(value, ("previous", "policy", "reason"))
        text(value["reason"], "Policy change reason")
        config = _configuration(records)
        if value["previous"] != preparation_policy(records):
            raise ResearchError("policy_conflict", "Name the policy currently recorded for this study",
                                {"current": preparation_policy(records)})
        updated = dict(config, preparation_policy=_policy(value["policy"]))
        return [("configuration", "research", updated)], dict(updated, decisions_revalidated=False)

    return prepared_mutation(store, "research.policy", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def configuration_state(records, artifacts, profile):
    """Snapshot-level configuration check shared by downstream managed gates."""
    profile_name(profile)
    evaluation = Evaluation.of(records, artifacts)
    return evaluation.once(("configuration", profile), lambda: _configuration_state(evaluation, profile))


def _configuration_state(evaluation, profile):
    records, artifacts = evaluation.records, evaluation
    contract = constitution_contract()
    config = records.get("configuration", {}).get("research")
    obligations = []
    if config is None:
        obligations.append(obligation("configuration_missing", "Initialize the study profile, target and constitution."))
    elif config["profile"] != profile:
        obligations.append(obligation("profile_mismatch", "Use the study's configured research or verification profile."))
    else:
        if config["constitution"] != contract:
            obligations.append(obligation("constitution_revalidation_required", "Explicitly review the current policy and reassess dependent decisions.",
                                          previous=config["constitution"], current=contract))
        archived = records.get("constitution", {}).get(config["constitution"]["sha256"])
        if archived is None:
            obligations.append(obligation("constitution_archive_missing", "Restore the archived policy that the study actually adopted."))
        else:
            artifacts.read(archived["artifact"])
        target = config["target"]
        _validate_target(records, artifacts, profile, target)
        if profile == "research":
            if target is None:
                obligations.append(obligation("objective_missing", "Fix the complete research objective before judging readiness."))
            elif records.get("research_objective", {}).get(target["id"]) != target:
                obligations.append(obligation("objective_mismatch", "The configuration must retain the original complete objective."))
        else:
            if target["source_id"] is None:
                obligations.append(obligation("target_source_pin_missing", "Acquire and pin the exact original main target body."))
            scope = records.get("literature_scope", {}).get(profile)
            if scope is None or scope.get("target") != target:
                obligations.append(obligation("target_mismatch", "Set the literature roots and target to the configured exact source pin."))
    return {"ready": not obligations, "digest": digest({"configuration": config, "current_constitution": contract}),
            "obligations": obligations, "counts": {"obligations": len(obligations)},
            "preparation_policy": preparation_policy(records),
            "profile": profile, "target": config["target"] if config and config["profile"] == profile else None,
            "constitution": contract, "configuration": config if config and config["profile"] == profile else None,
            "next": obligations[0] if obligations else None,
            "limits": "A current policy and target do not establish scientific readiness or revalidate historical decisions."}


def configuration_report(store, profile):
    records = store.snapshot()["records"]
    return configuration_state(records, Evaluation(records, ArtifactStore(store.root)), profile)
