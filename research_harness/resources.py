"""Resource budgets and accounts for preparation work.

set_budget records resource_budget/<profile>:<purpose> with per-unit limits
(null means unlimited) and a reason. Work that charges a purpose keeps a
resource_account/<profile>:<purpose> projection with charged, reserved and
unknown amounts per unit. Acquisition reserves its request allowance at
admission and reconciles it at finish; batches charge their counts and any
reported model usage. New work that would exceed a limit is refused; usage
already spent is always recorded; exhaustion is an obligation, never readiness.
"""

from .errors import ResearchError
from .graph import obligation
from .operations import fields, prepared_mutation, profile_name, text


UNITS = ("network_requests", "source_bytes", "readings", "screenings", "model_input_tokens", "model_output_tokens", "wall_seconds")
PURPOSES = ("literature", "screening", "experiment")
_ACCOUNT = "resource_account"
_BUDGET = "resource_budget"


def _key(profile, purpose):
    if purpose not in PURPOSES:
        raise ResearchError("invalid_input", "Resource purpose must be one of: " + ", ".join(PURPOSES))
    return profile_name(profile) + ":" + purpose


def _empty(key):
    return {"key": key, "charged": {unit: 0 for unit in UNITS}, "reserved": {unit: 0 for unit in UNITS},
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


def _configured_profile(source):
    config = _lookup(source, "configuration", "research")
    return config["profile"] if config else None


def _check(budget, account, unit, amount):
    limit = (budget or {}).get("limits", {}).get(unit)
    if limit is not None and account["charged"][unit] + account["reserved"][unit] + amount > limit:
        raise ResearchError("resource_budget_exhausted", "The " + unit + " budget would be exceeded; raise the budget with a reason or stop",
                            {"unit": unit, "limit": limit, "charged": account["charged"][unit], "reserved": account["reserved"][unit],
                             "requested": amount})


def reserve(source, purpose, amounts):
    """Return the account change that reserves amounts, or None when no study is configured."""
    profile = _configured_profile(source)
    if profile is None:
        return None
    key = _key(profile, purpose)
    account = dict(_lookup(source, _ACCOUNT, key) or _empty(key))
    budget = _lookup(source, _BUDGET, key)
    reserved = dict(account["reserved"])
    for unit, amount in _amounts(amounts, "Reservation").items():
        if amount:
            _check(budget, account, unit, amount)
            reserved[unit] += amount
    return _ACCOUNT, key, dict(account, reserved=reserved)


def charge(source, purpose, amounts, *, unknown=(), reserved=None, refuse=True):
    """Return the account change that charges amounts; with refuse, new work over a limit is refused."""
    profile = _configured_profile(source)
    if profile is None:
        return None
    key = _key(profile, purpose)
    account = dict(_lookup(source, _ACCOUNT, key) or _empty(key))
    budget = _lookup(source, _BUDGET, key)
    charged, held, unknowns = dict(account["charged"]), dict(account["reserved"]), dict(account["unknown"])
    for unit, amount in _amounts(reserved or {}, "Reservation").items():
        held[unit] = max(0, held[unit] - (amount or 0))
    for unit, amount in _amounts(amounts, "Usage").items():
        if amount is None:
            unknowns[unit] += 1
            continue
        if refuse:
            _check(budget, dict(account, reserved=held), unit, amount)
        charged[unit] += amount
    for unit in unknown:
        unknowns[unit] += 1
    return _ACCOUNT, key, dict(account, charged=charged, reserved=held, unknown=unknowns)


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
        report[purpose] = {unit: {"limit": (budget or {}).get("limits", {}).get(unit), "charged": account["charged"][unit],
                                  "reserved": account["reserved"][unit], "unknown": account["unknown"][unit]} for unit in UNITS}
    return report


def obligations(records, profile):
    """A charged amount at or above its limit is a checkpoint obligation, never readiness."""
    found = []
    for purpose, units in account_report(records, profile).items():
        for unit, value in units.items():
            if value["limit"] is not None and value["charged"] >= value["limit"]:
                found.append(obligation("resource_budget_exhausted", "Record a checkpoint, then raise the budget with a reason, narrow the claim, or pause.",
                                        purpose=purpose, unit=unit, limit=value["limit"], charged=value["charged"]))
    return found
