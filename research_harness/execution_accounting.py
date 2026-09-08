"""Current execution permission over immutable measured resource history."""

import math

from .errors import ResearchError
from .evidence import digest
from .workspace import strict_json


def require_accounted_usage(records, artifacts, strategy_key):
    """Keep known uncharged wall time from becoming permission for another run.

    Older execution payloads and receipts are never rewritten. An unresolved
    overrun needs an explicit accounting correction before further execution.
    This guard neither estimates unknown time nor changes any resource limit.
    """
    for identifier, admission in records.get("execution_admission", {}).items():
        if admission["strategy_key"] != strategy_key:
            continue
        observation = records.get("execution_observation", {}).get(identifier)
        binding = records.get("execution_binding", {}).get(identifier)
        claim = records.get("execution_claim", {}).get(identifier)
        outcome = records.get("execution_outcome", {}).get(identifier)
        if claim is not None and outcome is not None and (observation is None or binding is None):
            raise ResearchError("execution_usage_reconciliation_required", "A claimed outcome has incomplete observation or binding evidence. Reconcile the original execution before admitting or launching more work; historical accounting is not rewritten",
                                {"admission_id": identifier, "strategy_key": strategy_key})
        if observation is None or binding is None or binding["usage_unit"] != "wall_seconds":
            continue
        execution = records.get("execution", {}).get(outcome["execution_id"]) if outcome else None
        terminal = strict_json(artifacts.read(observation["terminal"]))
        details = {"admission_id": identifier, "strategy_key": strategy_key, "terminal": observation["terminal"]}
        if (claim is None or execution is None or observation.get("claim_digest") != digest(claim)
                or observation.get("execution") != execution["payload"]
                or binding["admission_digest"] != admission["digest"]
                or terminal.get("binding_digest") != binding["digest"]
                or terminal.get("config_sha256") != claim["config_artifact"]["sha256"]):
            raise ResearchError("execution_usage_reconciliation_required", "The retained measured usage lacks its exact execution identity", details)
        measured = terminal.get("duration_s")
        if measured is None:
            continue
        if type(measured) not in (int, float) or not math.isfinite(measured) or measured < 0:
            raise ResearchError("execution_usage_reconciliation_required", "The retained terminal has no valid measured duration", details)
        units = execution["payload"]["usage"]["units"]
        accounted = max(admission["reserved_units"], units if units is not None else 0)
        if measured > accounted:
            details.update(measured_units=measured, accounted_units=accounted, unaccounted_units=measured - accounted)
            raise ResearchError("execution_usage_reconciliation_required", "Known measured wall time remains uncharged in historical execution. Preserve the original receipt and resolve accounting before admitting or launching more work", details)
