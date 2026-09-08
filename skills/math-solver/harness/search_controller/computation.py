"""Closed computation contracts and interpretation facts in the existing log."""

import copy

from . import schema as s
from .admission import reference
from .proof import acceptance_closure, obligation_support, route_identity, scope_subset, validate_review


FINITE_TASKS = {"finite_decision", "finite_proof", "counterexample_search"}
FINITE_SCOPES = {"case_ids", "integer_interval"}


def validate_contract(value):
    s.closed(value, "schema_version domain basis preflight verification_plan")
    s.integer(value["schema_version"], 1, 1)
    domain = value["domain"]
    s.require(isinstance(domain, dict), "Computation domain must be a record")
    if domain.get("kind") == "bounded_encoding":
        s.closed(domain, "kind encoding_digest max_instances")
        s.digest_string(domain["encoding_digest"])
        s.integer(domain["max_instances"], 1)
    else:
        s.validate_scope(domain)
        s.choice(domain["kind"], FINITE_SCOPES)
    basis = value["basis"]
    s.require(isinstance(basis, dict), "Computation basis must be a record")
    s.choice(basis.get("kind"), {"root_finite_scope", "finite_residue", "diagnostic", "standalone", "counterexample"})
    s.closed(basis, "kind deduction_digest dependencies completeness_acceptance_id bound_acceptance_id" +
             (" reduction_acceptance_id" if basis["kind"] == "finite_residue" else ""))
    s.digest_string(basis["deduction_digest"])
    for dependency in s.records(basis["dependencies"]):
        s.closed(dependency, "acceptance_id acceptance_digest claim_digest")
        s.text(dependency["acceptance_id"])
        s.digest_string(dependency["acceptance_digest"])
        s.digest_string(dependency["claim_digest"])
    ids = [item["acceptance_id"] for item in basis["dependencies"]]
    s.strings(ids)
    for key in ["completeness_acceptance_id", "bound_acceptance_id"]:
        s.optional_text(basis[key])
        s.require(basis[key] is None or basis[key] in ids, "Bound and completeness evidence must be pinned dependencies")
    if basis["kind"] == "finite_residue":
        s.require(basis["reduction_acceptance_id"] in ids, "Finite residue needs its accepted reduction dependency")
    preflight = value["preflight"]
    s.closed(preflight, "uncertainty inspected_evidence already_determined cheapest_sufficient_check failure_signal")
    for key in ["uncertainty", "cheapest_sufficient_check", "failure_signal"]:
        s.text(preflight[key])
    s.strings(preflight["inspected_evidence"])
    for item in preflight["inspected_evidence"]:
        s.digest_string(item)
    s.require(type(preflight["already_determined"]) is bool, "already_determined must be boolean")
    plan = value["verification_plan"]
    s.closed(plan, "certificate_shape checker_method producer_seconds checker_seconds checker_cap_seconds fallback max_input_bytes")
    for key in ["certificate_shape", "checker_method", "fallback"]:
        s.text(plan[key])
    for key in ["producer_seconds", "checker_seconds", "checker_cap_seconds", "max_input_bytes"]:
        s.integer(plan[key], 1)
    s.require(plan["checker_seconds"] <= plan["checker_cap_seconds"],
              "Expected checking exceeds its cap; redesign the verification plan", "computation_required")


def validate_basis(state, proposal, value):
    """Check exact graph bindings; semantic relevance belongs to recorded review."""
    validate_contract(value)
    basis, domain = value["basis"], value["domain"]
    claim = proposal["claim"]
    policy = claim["proof_policy"]
    reviewed_dependencies = set()
    for item in basis["dependencies"]:
        accepted = reference(state["acceptances"], item["acceptance_id"], "computation acceptance")
        s.require(s.digest(accepted) == item["acceptance_digest"], "Bound acceptance changed", "digest_mismatch")
        closure = acceptance_closure(state, accepted["id"], policy)
        s.require(closure is not None, "Computation basis is stale or below proof policy", "audit_failed")
        reviewed_dependencies.update(closure)
        cp = state["checkpoints"][accepted["checkpoint_id"]]
        s.require(s.digest(cp["claim"]) == item["claim_digest"], "Bound claim differs", "claim_mismatch")
        for identity in closure:
            dependency = state["acceptances"][identity]
            inherited = state["checkpoints"][dependency["checkpoint_id"]]["claim"]
            s.require(dependency["outcome"] == "proof" and set(inherited["assumption_ids"]) <= set(claim["assumption_ids"]),
                      "Computation dependency must prove its exact statement without extra assumptions", "claim_mismatch")
    root_scope = state["contract"]["original_claim"]["scope"]
    if basis["kind"] == "root_finite_scope":
        represented_scope = claim["scope"] if domain["kind"] == "bounded_encoding" else domain
        s.require(root_scope["kind"] in FINITE_SCOPES and scope_subset(represented_scope, root_scope),
                  "Root finite work must cover an actual finite root subdomain", "computation_required")
    elif basis["kind"] == "finite_residue":
        reduction = state["acceptances"][basis["reduction_acceptance_id"]]
        target = proposal["target_obligation"]
        routes = [route for route in state["routes"].values()
                  if route["conclusion"] == state["contract"]["root_obligation"]
                  and target in route["premises"] and route["bridge"] == reduction["obligation_id"]
                  and reduction["route_content_digests"].get(route["id"]) == route_identity(route)]
        s.require(bool(routes), "Finite residue requires an accepted exact root reduction bridge", "computation_required")
        # Resolve nonfinite premises only through the reviewed dependency closure,
        # whose claims, policies, assumptions, artifacts and reviews are audited.
        reviewed_state = dict(state, acceptances={identity: state["acceptances"][identity]
                                                 for identity in reviewed_dependencies})
        s.require(any(all(state["obligations"][oid]["claim"]["scope"]["kind"] in FINITE_SCOPES
                              or obligation_support(reviewed_state, oid, policy) is not None
                          for oid in route["premises"]) for route in routes),
                  "An unresolved or unpinned unbounded tail is not a finite residue", "computation_required")
        s.require(claim["scope"]["kind"] in FINITE_SCOPES,
                  "The reduction target must be an explicitly finite obligation", "computation_required")
        if domain["kind"] in FINITE_SCOPES:
            s.require(domain == claim["scope"], "Finite residue domain must cover its exact target", "claim_mismatch")
    elif basis["kind"] == "standalone":
        s.require(proposal["category"] == "standalone" and proposal["contribution"]["standalone"] is not None,
                  "Standalone computation requires independently reviewed significance", "computation_required")
    elif basis["kind"] == "counterexample":
        s.require(proposal["task"]["kind"] == "counterexample_search",
                  "Counterexample basis requires the exact counterexample task", "computation_required")
    else:
        s.require(proposal["contribution"]["necessity"] is not None,
                  "Diagnostic requires reviewed necessity and outcomes", "computation_required")
    if domain["kind"] == "bounded_encoding" and (proposal["task"]["kind"] == "finite_proof"
                                                     or basis["kind"] in {"root_finite_scope", "finite_residue"}):
        s.require(basis["completeness_acceptance_id"] is not None and basis["bound_acceptance_id"] is not None,
                  "Proof encoding needs accepted completeness and bound evidence", "computation_required")
    s.require(not value["preflight"]["already_determined"] or proposal["role"] == "verification",
              "The declared outcome is already determined; no producer is necessary", "already_determined")
    for key in ["producer_seconds", "checker_seconds", "checker_cap_seconds"]:
        s.require(value["verification_plan"][key] <= proposal["limits"]["timeout_seconds"],
                  "Verification plan exceeds the admitted allowance", "budget_exhausted")


def require_new_proposal(proposal):
    s.require(proposal["task"]["kind"] not in FINITE_TASKS or proposal["schema_version"] == 2,
              "New finite proposals require schema_version 2 and a computation contract", "computation_required")


def effective_contract(state, node):
    proposal = state["proposals"][node["proposal_id"]]["record"]
    if proposal["task"]["kind"] not in FINITE_TASKS:
        return None
    amendment = state["service"]["computation_amendments"].get(node["id"])
    value = proposal.get("computation") if amendment is None else amendment["subject"]["computation"]
    s.require(value is not None,
              "Historical finite admission requires search amend-computation NODE --spec FILE before execution",
              "computation_amendment_required")
    return value


def pending_interpretation(state):
    return next((run for _, run in sorted(state["runs"].items())
                 if run.get("computation_digest") is not None and run["status"] == "terminal"
                 and run["id"] not in state["service"]["run_interpretations"]), None)


def require_interpreted(state):
    s.require(pending_interpretation(state) is None,
              "Interpret the reconciled finite run with search interpret RUN --spec FILE before another mathematical launch",
              "interpretation_required")


def validate_run(state, node, kind, computation_digest, timeout):
    require_interpreted(state)
    value = effective_contract(state, node)
    if value is None:
        s.require(computation_digest is None, "Analytical run cannot bind a computation contract")
        return
    s.require(computation_digest == s.digest(value), "Run computation contract differs", "digest_mismatch")
    proposal = state["proposals"][node["proposal_id"]]["record"]
    validate_basis(state, proposal, value)
    s.require(not value["preflight"]["already_determined"] or kind != "command",
              "Already determined outcomes permit only independent input-reviewed verification", "already_determined")
    cap = value["verification_plan"]["checker_cap_seconds"] if kind != "command" else proposal["limits"]["timeout_seconds"]
    s.integer(timeout, 1, cap)


def record_amendment(state, payload):
    s.closed(payload, "subject review digest")
    subject = payload["subject"]
    s.closed(subject, "node_id proposal_digest computation")
    node = reference(state["nodes"], subject["node_id"], "amendment node")
    proposal = reference(state["proposals"], node["proposal_id"], "admitted proposal")
    s.require(node["id"] not in state["service"]["computation_amendments"], "Existing computation amendment is immutable")
    s.require(proposal["record"]["schema_version"] == 1 and proposal["record"]["task"]["kind"] in FINITE_TASKS,
              "Only admitted version-1 finite nodes use computation amendments")
    s.require(subject["proposal_digest"] == proposal["digest"],
              "Material task changes require a reviewed successor on the existing account", "claim_mismatch")
    value = subject["computation"]
    validate_basis(state, proposal["record"], value)
    scope = node["claim"]["scope"]
    if scope["kind"] in FINITE_SCOPES and value["domain"]["kind"] in FINITE_SCOPES:
        s.require(scope == value["domain"], "Scope changes require a reviewed successor", "claim_mismatch")
    validate_review(payload["review"], s.digest(subject), node["claim_digest"])
    author, reviewer = proposal["record"]["author"], payload["review"]["reviewer"]
    s.require(author["actor_id"] != reviewer["actor_id"] and author["attestation_id"] != reviewer["attestation_id"],
              "Computation amendment requires independent review", "review_not_independent")
    s.require(payload["digest"] == s.digest(subject), "Amendment subject changed", "digest_mismatch")
    state["service"]["computation_amendments"][node["id"]] = copy.deepcopy(payload)


def record_interpretation(state, payload):
    s.closed(payload, "interpretation digest")
    value = payload["interpretation"]
    s.closed(value, "run_id result_digest computation_digest outcome inconclusive_reason classification root_decision remaining_obligation_ids next_action")
    run = reference(state["runs"], value["run_id"], "interpreted run")
    s.require(run["status"] == "terminal" and run.get("computation_digest") is not None,
              "Interpretation requires an actual terminal run with its original computation contract")
    s.require(run["id"] not in state["service"]["run_interpretations"], "Run interpretation is immutable")
    for key in ["result_digest", "computation_digest"]:
        s.digest_string(value[key])
        s.require(value[key] == run[key], "Interpretation pins different run evidence", "digest_mismatch")
    s.require(payload["digest"] == s.digest(value), "Interpretation record changed", "digest_mismatch")
    s.optional_text(value["outcome"])
    s.optional_text(value["inconclusive_reason"])
    s.require((value["outcome"] is None) != (value["inconclusive_reason"] is None),
              "Declare one admitted outcome or an explicit inconclusive reason")
    s.choice(value["classification"], {"observation", "audit_only", "undecided", "proof_candidate", "counterexample_candidate"})
    decision = value["root_decision"]
    s.closed(decision, "kind reason")
    s.choice(decision["kind"], {"undecided", "proof_candidate", "counterexample_candidate"})
    s.text(decision["reason"])
    s.text(value["next_action"])
    s.strings(value["remaining_obligation_ids"])
    for oid in value["remaining_obligation_ids"]:
        reference(state["obligations"], oid, "remaining obligation")
    node = state["nodes"][run["node_id"]]
    outcomes = {item["outcome"]: item["next_action"] for item in node["admission"]["contribution"]["necessity"]["outcomes"]}
    if value["outcome"] is not None:
        s.require(value["outcome"] in outcomes and value["next_action"] == outcomes[value["outcome"]],
                  "Interpretation must use the admitted outcome/action map")
    candidate = value["classification"] in {"proof_candidate", "counterexample_candidate"}
    if decision["kind"] != "undecided":
        s.require(candidate and decision["kind"] == value["classification"] and node["category"] != "standalone"
                  and node["obligation_id"] is not None,
                  "A candidate root decision requires matching classification and a root connection")
    if candidate:
        contract = effective_contract(state, node)
        expected_task = "finite_proof" if value["classification"] == "proof_candidate" else "counterexample_search"
        s.require(run["task"]["kind"] == expected_task and contract["basis"]["kind"] != "diagnostic",
                  "Decision and audit work cannot claim mathematical candidacy")
    if (value["inconclusive_reason"] is not None or run["termination"] != "exit"
            or (value["outcome"] or "").upper() == "UNKNOWN"):
        s.require(not candidate and decision["kind"] == "undecided",
                  "Failed or inconclusive execution cannot decide a theorem")
    state["service"]["run_interpretations"][run["id"]] = copy.deepcopy(payload)
    from .strategy_refresh import note_evidence
    note_evidence(state, "run:" + run["id"])


EVENT_HANDLERS = {"computation_amended": record_amendment, "run_interpreted": record_interpretation}
