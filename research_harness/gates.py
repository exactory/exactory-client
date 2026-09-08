"""Current snapshot gates, separate from historical receipts and proof authority."""

from .artifacts import ArtifactStore
from .cohort_evidence import cohort_report
from .development import validate_admitted_execution
from .execution_evidence import author_readiness_state
from .errors import ResearchError
from .evidence import digest
from .graph import obligation
from .literature import foundation_state
from .synthesis import synthesis_state


STAGES = ("initiate", "cohort", "literature", "ideate", "experiment", "write",
          "evaluate", "deposit", "submit", "complete")
BACKWARD = {(stage, "literature") for stage in STAGES[3:-1]} | {
    ("experiment", "ideate"), ("evaluate", "experiment"), ("evaluate", "write")}


def _report(obligations, **values):
    return dict(ready=not obligations, obligations=obligations, digest=digest(values),
                counts={"obligations": len(obligations)}, **values)


def require_ready(report, action):
    if not report["ready"]:
        raise ResearchError("readiness_required", "Current research readiness is required for " + action,
                            {"action": action, "obligations": report["obligations"],
                             "next": "exactory-research next"})
    return report


def gate_state(records, artifacts, action, *, profile=None):
    config = records.get("configuration", {}).get("research")
    if config is None:
        return _report([obligation("migration_required", "Initialize or explicitly adopt the current research contract.")])
    profile = profile or config["profile"]
    if profile != config["profile"]:
        return _report([obligation("profile_mismatch", "Use a separate workspace with the applicable profile.")])
    if action == "cohort":
        scope = records.get("literature_scope", {}).get(profile, {})
        selected = records.get("cohort_selection", {}).get(profile)
        ids = selected["collection_ids"] if selected else scope.get("collection_ids", sorted(records.get("collection", {})))
        return cohort_report(records, artifacts, ids)
    if action == "foundation":
        return foundation_state(records, artifacts, profile)
    if action in {"preparation", "verification"}:
        if action == "verification" and profile != "verification":
            return _report([obligation("profile_mismatch", "Verdicts require an independent verification workspace.")])
        return synthesis_state(records, artifacts, profile)
    if action in {"readiness", "write"}:
        return author_readiness_state(records, artifacts)
    if action == "execution":
        preparation = synthesis_state(records, artifacts, profile)
        pending = [a for key, a in records.get("execution_admission", {}).items()
                   if key not in records.get("execution_outcome", {})]
        obligations = list(preparation["obligations"])
        for admission in pending:
            try:
                validate_admitted_execution(records, artifacts, admission["id"])
            except ResearchError as error:
                obligations.append(obligation(error.code, error.message, admission_id=admission["id"]))
        if not pending:
            obligations.append(obligation("execution_admission_required", "Plan and admit a prospective cycle before entering experiment."))
        return _report(obligations, preparation=preparation, admissions=pending)
    if action in {"manuscript", "publication", "deposited", "submitted"}:
        from .publication import publication_state
        return publication_state(records, artifacts, action)
    if action == "initiate":
        # Fixing the objective is a supported preparation operation before
        # literature -> ideate. Intake itself does not require that objective.
        return _report([], configuration=config)
    raise ResearchError("invalid_gate", "Unknown research gate", {"action": action})


def gate_report(store, action, *, profile=None):
    snapshot = store.snapshot()
    report = gate_state(snapshot["records"], ArtifactStore(store.root), action, profile=profile)
    return dict(report, revision=snapshot["revision"])


def validate_transition(records, artifacts, previous, proposed):
    source, target = previous["stage"], proposed["stage"]
    if source not in STAGES or target not in STAGES:
        raise ResearchError("invalid_stage", "Unsupported study stage")
    if target != source and (source, target) not in BACKWARD and STAGES.index(target) != STAGES.index(source) + 1:
        raise ResearchError("readiness_transition", "Research readiness does not permit a direct stage jump",
                            {"from": source, "to": target, "next": "exactory-research next"})
    if (source, target) in BACKWARD:
        if (source, target) == ("experiment", "ideate") and not records.get("cycle_assessment"):
            raise ResearchError("readiness_required", "Assess the experiment before returning to ideate")
        if proposed["status"] == "done":
            raise ResearchError("readiness_required", "A return for more work cannot assert that work is done")
        return
    if target == source and proposed["status"] != "done":
        return
    completed = {
        "initiate": "initiate", "cohort": "cohort", "literature": "preparation",
        "ideate": "execution", "experiment": "readiness", "write": "manuscript",
        "evaluate": "publication", "deposit": "deposited", "submit": "submitted", "complete": "submitted",
    }
    require_ready(gate_state(records, artifacts, completed[source]), source + " completion")
    prerequisites = {"ideate": "preparation", "experiment": "execution", "write": "readiness",
                     "evaluate": "manuscript", "deposit": "publication", "submit": "deposited", "complete": "submitted"}
    if target in prerequisites:
        require_ready(gate_state(records, artifacts, prerequisites[target]), "entering " + target)
