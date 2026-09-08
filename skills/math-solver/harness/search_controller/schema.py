"""Closed JSON records used by the deterministic admission reducer."""

import copy
import hashlib
import re

from .errors import SearchError
from .storage import canonical_bytes


POLICIES = {"reviewed", "certificate", "lean-kernel"}
CATEGORIES = {"main", "coverage", "standalone"}
RELATIONSHIPS = CATEGORIES | {"prerequisite", "alternative", "continuation"}


def require(condition, message, code="invalid_record"):
    if not condition:
        raise SearchError(code, message)


def closed(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields.split()),
            "Record must contain exactly: " + fields)


def text(value):
    require(isinstance(value, str) and bool(value.strip()), "Expected nonempty text")


def integer(value, minimum=0, maximum=None):
    require(type(value) is int and value >= minimum and (maximum is None or value <= maximum),
            "Integer is outside the permitted range")


def choice(value, choices):
    require(isinstance(value, str) and value in choices, "Unknown enum value")


def strings(value, nonempty=False):
    require(isinstance(value, list) and (value or not nonempty), "Expected a list")
    for item in value:
        text(item)
    require(len(value) == len(set(value)), "Duplicate list values")


def records(value):
    require(isinstance(value, list), "Expected a record list")
    return value


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_string(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "Expected a SHA-256 digest")


def optional_text(value):
    if value is not None:
        text(value)


def validate_provenance(value):
    closed(value, "source actor_id attestation_id")
    choice(value["source"], {"host", "operator"})
    text(value["actor_id"])
    text(value["attestation_id"])


def validate_scope(value):
    require(isinstance(value, dict), "Scope must be a record")
    kind = value.get("kind")
    choice(kind, {"named", "case_ids", "integer_interval"})
    if kind == "named":
        closed(value, "kind name")
        text(value["name"])
    elif kind == "case_ids":
        closed(value, "kind case_ids")
        strings(value["case_ids"], nonempty=True)
    else:
        closed(value, "kind lower upper lower_inclusive upper_inclusive")
        require(type(value["lower"]) is int and type(value["upper"]) is int,
                "Interval bounds must be finite integers")
        require(type(value["lower_inclusive"]) is bool and type(value["upper_inclusive"]) is bool,
                "Interval endpoint flags must be boolean")
        lower, upper = interval_bounds(value)
        require(lower <= upper, "Integer interval must be nonempty and ordered")


def interval_bounds(scope):
    return (scope["lower"] + (0 if scope["lower_inclusive"] else 1),
            scope["upper"] - (0 if scope["upper_inclusive"] else 1))


def validate_claim(value):
    closed(value, "statement quantifiers assumption_ids proof_policy scope")
    text(value["statement"])
    text(value["quantifiers"])
    strings(value["assumption_ids"])
    choice(value["proof_policy"], POLICIES)
    validate_scope(value["scope"])


def claim_identity(value):
    """Normalize only structural equivalences; semantic equivalence needs review."""
    normalized = copy.deepcopy(value)
    normalized["assumption_ids"].sort()
    scope = normalized["scope"]
    if scope["kind"] == "case_ids":
        scope["case_ids"].sort()
    elif scope["kind"] == "integer_interval":
        lower, upper = interval_bounds(scope)
        scope.update(lower=lower, upper=upper, lower_inclusive=True, upper_inclusive=True)
    return digest(normalized)


def validate_contract(value):
    canonical_bytes(value)
    closed(value, "schema_version original_claim assumption_ids root_obligation root_attack_slug requested_outcome proof_policy required_deliverables resource_policy")
    integer(value["schema_version"], 1, 1)
    validate_claim(value["original_claim"])
    strings(value["assumption_ids"])
    require(value["original_claim"]["assumption_ids"] == value["assumption_ids"],
            "Root assumptions must equal the frozen contract assumptions")
    require(value["root_obligation"] == "obligation-000001", "Root uses the first stable obligation ID")
    slug(value["root_attack_slug"])
    choice(value["requested_outcome"], {"proof", "decision"})
    choice(value["proof_policy"], POLICIES)
    require(value["proof_policy"] == value["original_claim"]["proof_policy"], "Root proof policy mismatch")
    strings(value["required_deliverables"])
    policy = value["resource_policy"]
    closed(policy, "max_total_moves max_total_runs max_workers")
    for field in ["max_total_moves", "max_total_runs"]:
        if policy[field] is not None:
            integer(policy[field], 1)
    integer(policy["max_workers"], 1)


def slug(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value) is not None,
            "Attack slug must be a simple directory name")


def validate_limits(value):
    closed(value, "max_moves max_runs timeout_seconds workers")
    integer(value["max_moves"], 1, 24)
    for field in ["max_runs", "timeout_seconds", "workers"]:
        integer(value[field], 1)


def validate_checkpoint_criterion(value):
    fields = {
        "accepted_obligation": "obligation_id",
        "accepted_case_set": "obligation_id case_ids",
        "verified_reduction": "obligation_id",
        "verified_obstruction": "route_id",
        "hypothesis_recorded": "",
    }
    require(isinstance(value, dict), "Criterion must be a record")
    choice(value.get("kind"), fields)
    closed(value, "kind criterion_id explanation " + fields[value["kind"]])
    text(value["criterion_id"])
    text(value["explanation"])
    for key in fields[value["kind"]].split():
        strings(value[key], True) if key == "case_ids" else text(value[key])


def validate_retreat_criterion(value):
    fields = {"strategy_failure": "strategy_id", "move_limit": "threshold",
              "run_limit": "threshold", "stagnation_window": "threshold",
              "method_prerequisite_failed": "obligation_id"}
    require(isinstance(value, dict), "Retreat criterion must be a record")
    choice(value.get("kind"), fields)
    field = fields[value["kind"]]
    closed(value, "kind " + field)
    integer(value[field], 1) if field == "threshold" else text(value[field])


def validate_necessity(value):
    closed(value, "obligation_id omission_consequence domain_justification outcomes stopping_condition")
    optional_text(value["obligation_id"])
    for field in ["omission_consequence", "domain_justification", "stopping_condition"]:
        text(value[field])
    outcomes = records(value["outcomes"])
    require(len(outcomes) >= 2, "Decision must distinguish relevant outcomes")
    for outcome in outcomes:
        closed(outcome, "outcome next_action")
        text(outcome["outcome"])
        text(outcome["next_action"])
    strings([item["outcome"] for item in outcomes], True)


def validate_decomposition(value):
    closed(value, "obligations routes")
    for obligation in records(value["obligations"]):
        closed(obligation, "key claim")
        slug(obligation["key"])
        validate_claim(obligation["claim"])
    for route in records(value["routes"]):
        closed(route, "key conclusion premises bridge alternative_order")
        slug(route["key"])
        text(route["conclusion"])
        text(route["bridge"])
        strings(route["premises"], True)
        strings(route["alternative_order"])
    for field in ["obligations", "routes"]:
        strings([item["key"] for item in value[field]])


def validate_proposal(value):
    canonical_bytes(value)
    require(isinstance(value, dict), "Proposal must be a record")
    closed(value, "schema_version author category relationship attack_slug role claim target_obligation logical_predecessor native_parent anchor inherited_evidence inherited_assumption_ids hypothesis method applicability success_criterion failure_criterion parent_effect studies contribution task limits budget equivalent_node_ids checkpoint_criteria retreat_criteria decomposition" +
           (" computation" if value.get("schema_version") in (2, 3) else "") +
           (" foundation" if value.get("schema_version") == 3 else ""))
    integer(value["schema_version"], 1, 3)
    if value["schema_version"] == 3:
        from .research import validate_reference
        validate_reference(value["foundation"])
    validate_provenance(value["author"])
    choice(value["category"], CATEGORIES)
    choice(value["relationship"], RELATIONSHIPS)
    slug(value["attack_slug"])
    choice(value["role"], {"research", "verification"})
    validate_claim(value["claim"])
    for field in ["target_obligation", "logical_predecessor", "native_parent"]:
        optional_text(value[field])
    for field in ["hypothesis", "method", "applicability", "success_criterion", "failure_criterion", "parent_effect"]:
        text(value[field])
    anchor = value["anchor"]
    require(isinstance(anchor, dict), "Anchor must be a record")
    choice(anchor.get("kind"), {"objective", "checkpoint"})
    closed(anchor, "kind digest" + (" checkpoint_id" if anchor["kind"] == "checkpoint" else ""))
    digest_string(anchor["digest"])
    if anchor["kind"] == "checkpoint":
        text(anchor["checkpoint_id"])
    strings(value["inherited_evidence"])
    for item in value["inherited_evidence"]:
        digest_string(item)
    strings(value["inherited_assumption_ids"])
    studies = value["studies"]
    closed(studies, "problem novelty strategies")
    digest_string(studies["problem"])
    digest_string(studies["novelty"])
    for strategy in records(studies["strategies"]):
        closed(strategy, "method digest")
        text(strategy["method"])
        digest_string(strategy["digest"])
    require(any(item["method"] == value["method"] for item in studies["strategies"]), "Selected method needs a strategy study")
    closed(value["task"], "kind purpose input_domain")
    choice(value["task"]["kind"], {"proof", "finite_decision", "finite_proof", "counterexample_search"})
    text(value["task"]["purpose"])
    text(value["task"]["input_domain"])
    if value["schema_version"] in (2, 3):
        if value["task"]["kind"] == "proof":
            require(value["computation"] is None, "Analytical proof tasks use a null computation contract")
        else:
            from .computation import validate_contract as validate_computation
            validate_computation(value["computation"])
    contribution = value["contribution"]
    closed(contribution, "route deduction necessity coverage standalone")
    optional_text(contribution["route"])
    text(contribution["deduction"])
    if value["task"]["kind"] != "proof" or contribution["necessity"] is not None:
        validate_necessity(contribution["necessity"])
    if value["category"] == "coverage":
        coverage = contribution["coverage"]
        closed(coverage, "parent_obligation scope partition_route subset_deduction")
        for key in ["parent_obligation", "partition_route", "subset_deduction"]:
            text(coverage[key])
        validate_scope(coverage["scope"])
    else:
        require(contribution["coverage"] is None, "Only coverage proposals carry coverage bindings")
    if value["category"] == "standalone":
        standalone = contribution["standalone"]
        closed(standalone, "prospective_theorem primary_sources strongest_known_result mathematical_contribution significance")
        strings(standalone["primary_sources"], True)
        for source in standalone["primary_sources"]:
            digest_string(source)
        for key in ["prospective_theorem", "strongest_known_result", "mathematical_contribution", "significance"]:
            text(standalone[key])
    else:
        require(contribution["standalone"] is None, "Only standalone proposals carry significance records")
    validate_limits(value["limits"])
    budget = value["budget"]
    closed(budget, "mode account_id basis_checkpoint_id basis_checkpoint_digest justification")
    choice(budget["mode"], {"new", "inherit", "renew"})
    for key in ["account_id", "basis_checkpoint_id"]:
        optional_text(budget[key])
    if budget["basis_checkpoint_digest"] is not None:
        digest_string(budget["basis_checkpoint_digest"])
    text(budget["justification"])
    if budget["mode"] == "new":
        require(all(budget[key] is None for key in ["account_id", "basis_checkpoint_id", "basis_checkpoint_digest"]), "New account cannot carry renewal bindings")
    else:
        text(budget["account_id"])
        if budget["mode"] == "renew":
            text(budget["basis_checkpoint_id"])
            digest_string(budget["basis_checkpoint_digest"])
        else:
            require(budget["basis_checkpoint_id"] is None and budget["basis_checkpoint_digest"] is None, "Inheritance cannot carry renewal bindings")
    strings(value["equivalent_node_ids"])
    require(bool(records(value["checkpoint_criteria"])), "Checkpoint criteria are required")
    for criterion in value["checkpoint_criteria"]:
        validate_checkpoint_criterion(criterion)
    strings([item["criterion_id"] for item in value["checkpoint_criteria"]])
    require(bool(records(value["retreat_criteria"])), "Retreat criteria are required")
    for criterion in value["retreat_criteria"]:
        validate_retreat_criterion(criterion)
    validate_decomposition(value["decomposition"])


def validate_review(value):
    canonical_bytes(value)
    closed(value, "schema_version subject_digest claim_digest reviewer decision findings unresolved_objections")
    integer(value["schema_version"], 1, 1)
    digest_string(value["subject_digest"])
    digest_string(value["claim_digest"])
    validate_provenance(value["reviewer"])
    choice(value["decision"], {"approve", "revise", "reject"})
    closed(value["findings"], "root_connection mathematical_substance assumptions scope equivalence necessity novelty significance renewal_basis")
    for finding in value["findings"].values():
        require(isinstance(finding, str), "Findings must be text; inapplicable findings use an empty string")
    for objection in records(value["unresolved_objections"]):
        closed(objection, "blocking description")
        require(type(objection["blocking"]) is bool, "Objection blocking flag must be boolean")
        text(objection["description"])
