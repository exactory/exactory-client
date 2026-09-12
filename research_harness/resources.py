"""Resource budgets and accounts for preparation work.

set_budget records resource_budget/<profile>:<purpose> with per-unit limits
(null means unlimited) and a reason. Work that charges a purpose keeps a
resource_account/<profile>:<purpose> projection with charged and unknown
amounts per unit. The reserved amount is never stored: it is the request
allowance of every acquisition operation that is still admitted, so an
interrupted or superseded operation holds nothing once it is no longer
admitted. Admission refuses work that has no room for one request; batches
charge their counts and any reported model usage. Usage already spent is
always recorded; exhaustion is an obligation, never readiness. The
`development` purpose counts admitted development rounds in the `rounds`
unit; `round-admit` charges one round.
"""

from .errors import ResearchError
from .graph import obligation
from .operations import fields, prepared_mutation, profile_name, text


UNITS = ("network_requests", "source_bytes", "readings", "screenings", "model_input_tokens", "model_output_tokens", "wall_seconds", "rounds")
PURPOSES = ("literature", "screening", "experiment", "development")
_ACCOUNT = "resource_account"
_BUDGET = "resource_budget"


def _key(profile, purpose):
    if purpose not in PURPOSES:
        raise ResearchError("invalid_input", "Resource purpose must be one of: " + ", ".join(PURPOSES))
    return profile_name(profile) + ":" + purpose


def _empty(key):
    return {"key": key, "charged": {unit: 0 for unit in UNITS},
            "unknown": {unit: 0 for unit in UNITS}}


def _amounts(value, name):
    if not isinstance(value, dict) or not set(value) <= set(UNITS):
        raise ResearchError("invalid_input", name + " must map supported resource units to amounts")
    for unit, amount in value.items():
        if amount is not None and (type(amount) not in (int, float) or isinstance(amount, bool) or amount < 0):
            raise ResearchError("invalid_input", name + " amounts are nonnegative numbers")
    return value


def set_budget(store, payload, *, expected_revision, request_id):
    """Record or raise a budget; a limit never drops below what is already charged."""
    def prepare(records, value):
        fields(value, ("profile", "purpose", "limits", "reason"))
        key = _key(value["profile"], value["purpose"])
        text(value["reason"], "Budget reason")
        fields(value["limits"], UNITS)
        _amounts(value["limits"], "Budget limits")
        account = records.get(_ACCOUNT, {}).get(key, _empty(key))
        for unit, limit in value["limits"].items():
            if limit is not None and account["charged"][unit] > limit:
                raise ResearchError("resource_budget_below_charged", "A budget cannot drop below the amount already charged",
                                    {"unit": unit, "charged": account["charged"][unit], "limit": limit})
        return [(_BUDGET, key, dict(value, key=key))], dict(value, key=key)

    return prepared_mutation(store, "resources.budget", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _lookup(source, kind, key):
    if hasattr(source, "get") and not isinstance(source, dict):
        return source.get(kind, key)
    return source.get(kind, {}).get(key)


def _records(source, kind):
    if hasattr(source, "records") and not isinstance(source, dict):
        return source.records(kind)
    return source.get(kind, {})


def held(source, purpose):
    """Amounts reserved by admitted acquisition operations; only literature acquisition reserves."""
    reserved = {unit: 0 for unit in UNITS}
    if purpose == "literature":
        for operation in _records(source, "acquisition_operation").values():
            if operation["state"] == "admitted":
                reserved["network_requests"] += operation["payload"].get("max_requests") or 0
    return reserved


def _configured_profile(source):
    config = _lookup(source, "configuration", "research")
    return config["profile"] if config else None


def _check(budget, account, reserved, unit, amount):
    limit = (budget or {}).get("limits", {}).get(unit)
    if limit is not None and account["charged"][unit] + reserved[unit] + amount > limit:
        raise ResearchError("resource_budget_exhausted", "The " + unit + " budget would be exceeded; raise the budget with a reason or stop",
                            {"unit": unit, "limit": limit, "charged": account["charged"][unit], "reserved": reserved[unit],
                             "requested": amount})


def admit(source, purpose, max_requests):
    """Refuse an acquisition whose request allowance (at least one request) has no room beside the admitted ones."""
    profile = _configured_profile(source)
    if profile is None:
        return
    key = _key(profile, purpose)
    account = _lookup(source, _ACCOUNT, key) or _empty(key)
    _check(_lookup(source, _BUDGET, key), account, held(source, purpose), "network_requests", max(max_requests or 0, 1))


def charge(source, purpose, amounts, *, unknown=(), refuse=True):
    """Return the account change that charges amounts; with refuse, new work over a limit is refused."""
    profile = _configured_profile(source)
    if profile is None:
        return None
    key = _key(profile, purpose)
    account = dict(_lookup(source, _ACCOUNT, key) or _empty(key))
    budget = _lookup(source, _BUDGET, key)
    charged, unknowns = dict(account["charged"]), dict(account["unknown"])
    reserved = held(source, purpose) if refuse else None
    for unit, amount in _amounts(amounts, "Usage").items():
        if amount is None:
            unknowns[unit] += 1
            continue
        if refuse:
            _check(budget, account, reserved, unit, amount)
        charged[unit] += amount
    for unit in unknown:
        unknowns[unit] += 1
    return _ACCOUNT, key, dict(account, charged=charged, unknown=unknowns)


def account_report(records, profile):
    """Every account of the profile with its budget, for status and gates."""
    report = {}
    for purpose in PURPOSES:
        key = profile + ":" + purpose
        budget = records.get(_BUDGET, {}).get(key)
        account = records.get(_ACCOUNT, {}).get(key)
        if budget is None and account is None:
            continue
        account = account or _empty(key)
        reserved = held(records, purpose)
        report[purpose] = {unit: {"limit": (budget or {}).get("limits", {}).get(unit), "charged": account["charged"][unit],
                                  "reserved": reserved[unit], "unknown": account["unknown"][unit]} for unit in UNITS}
    return report


def obligations(records, profile):
    """Charged plus reserved at or above a limit is a checkpoint obligation, never readiness."""
    found = []
    for purpose, units in account_report(records, profile).items():
        for unit, value in units.items():
            if value["limit"] is not None and value["charged"] + value["reserved"] >= value["limit"]:
                found.append(obligation("resource_budget_exhausted", "Record a checkpoint, then raise the budget with a reason, narrow the claim, or pause.",
                                        purpose=purpose, unit=unit, limit=value["limit"], charged=value["charged"], reserved=value["reserved"]))
    return found
