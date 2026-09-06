"""Pure checkpoint and proof joins over service-validated immutable evidence.

The service authenticates reviews and audits before emitting these internal events.
This module checks their exact bindings; it never opens or verifies an artifact.
"""

import copy

from . import schema as s
from .admission import allocate, reference, progress_identity


def record_checkpoint(state, payload):
    s.closed(payload, "checkpoint digest")
    cp = payload["checkpoint"]
    s.closed(cp, "schema_version kind claim origin evidence_digests verification_status what_changed remaining_obligation_ids next_hypothesis milestone_id")
    s.require(s.digest(cp) == payload["digest"], "Checkpoint digest differs", "digest_mismatch")
    s.integer(cp["schema_version"], 1, 1)
    s.choice(cp["kind"], {"proof", "reduction", "obstruction", "hypothesis"})
    s.validate_claim(cp["claim"])
    s.choice(cp["verification_status"], {"pending"})
    s.strings(cp["evidence_digests"], nonempty=cp["kind"] != "hypothesis")
    for value in cp["evidence_digests"]:
        s.digest_string(value)
    s.strings(cp["remaining_obligation_ids"])
    for oid in cp["remaining_obligation_ids"] + cp["claim"]["assumption_ids"]:
        s.require(oid in state["obligations"] or oid in state["contract"]["assumption_ids"], "Unknown obligation or assumption")
    s.text(cp["what_changed"])
    s.text(cp["next_hypothesis"])
    s.optional_text(cp["milestone_id"])
    origin = cp["origin"]
    s.require(isinstance(origin, dict), "Origin must be a record")
    s.choice(origin.get("kind"), {"journal_move", "external_result"})
    if origin["kind"] == "journal_move":
        s.closed(origin, "kind node_id move journal_prefix_digest")
        node = reference(state["nodes"], origin["node_id"], "producer node")
        s.integer(origin["move"], 1, 24)
        s.digest_string(origin["journal_prefix_digest"])
        s.require(cp["milestone_id"] in {x["criterion_id"] for x in node["checkpoint_criteria"]}, "Checkpoint must name a declared milestone")
        criterion = next(x for x in node["checkpoint_criteria"] if x["criterion_id"] == cp["milestone_id"])
        expected = (state["obligations"][criterion["obligation_id"]]["claim"]
                    if "obligation_id" in criterion else node["claim"])
        s.require(s.claim_identity(cp["claim"]) == s.claim_identity(expected), "Checkpoint claim differs from its producer's admitted milestone")
        permitted = {"accepted_obligation": {"proof"}, "accepted_case_set": {"proof"},
                     "verified_reduction": {"reduction"}, "verified_obstruction": {"obstruction"},
                     "hypothesis_recorded": {"hypothesis"}}
        s.require(cp["kind"] in permitted[criterion["kind"]], "Checkpoint kind differs from its declared milestone")
    else:
        s.closed(origin, "kind source source_digest statement statement_digest study_digest")
        for key in ["source", "statement"]:
            s.text(origin[key])
        for key in ["source_digest", "statement_digest", "study_digest"]:
            s.digest_string(origin[key])
        s.require(origin["statement"] == cp["claim"]["statement"] and origin["statement_digest"] == s.digest(cp["claim"]), "External result must pin the exact claim")
        s.require(cp["milestone_id"] is None, "External result has no local milestone")
    identity = allocate(state, "checkpoint")
    state["checkpoints"][identity] = dict(copy.deepcopy(cp), id=identity)


def policy_allows(acceptance, policy):
    if policy == "lean-kernel":
        return acceptance["standard"] == "lean-kernel"
    if policy == "certificate" and acceptance["classification"] == "computational":
        return acceptance["standard"] in {"certificate", "lean-kernel"}
    return True


def acceptance_closure(state, identity, policy, visiting=None):
    """Return usable dependency IDs, or None for stale, cyclic or weak evidence."""
    visiting = set() if visiting is None else visiting
    if identity in visiting:
        return None
    value = state["acceptances"].get(identity)
    if value is None or value["status"] != "accepted" or not policy_allows(value, policy):
        return None
    cp = state["checkpoints"].get(value["checkpoint_id"])
    if cp is None or s.digest(cp) != value["checkpoint_digest"]:
        return None
    result = {identity}
    for dep in value["dependency_ids"]:
        closure = acceptance_closure(state, dep, policy, visiting | {identity})
        if closure is None:
            return None
        result.update(closure)
    return result


def validate_review(value, subject_digest, claim_digest):
    s.closed(value, "subject_digest claim_digest reviewer decision findings")
    s.require(value["subject_digest"] == subject_digest and value["claim_digest"] == claim_digest,
              "Result review is bound to different inputs", "digest_mismatch")
    s.validate_provenance(value["reviewer"])
    s.choice(value["decision"], {"approve"})
    s.closed(value["findings"], "statement assumptions scope dependencies policy")
    for finding in value["findings"].values():
        s.text(finding)


def route_identity(route):
    """Proof content excludes mutable scheduling order and derived status."""
    return s.digest({key: value for key, value in route.items() if key not in {"status", "alternative_order"}})


def binding_valid(state, binding, oid):
    s.closed(binding, "route_id route_digest case_obligation_ids shared_prerequisite_ids discharged_assumption_ids")
    route = reference(state["routes"], binding["route_id"], "route")
    s.require(route["bridge"] == oid, "Only the exact bridge can bind this route")
    # The recorded full route digest is pinned at acceptance. The immutable content
    # digest is stored separately so later scheduling changes cannot alter a theorem.
    s.require(binding["route_digest"] == s.digest(route), "Bridge route differs", "digest_mismatch")
    for key in ["case_obligation_ids", "shared_prerequisite_ids", "discharged_assumption_ids"]:
        s.strings(binding[key])
        s.require(set(binding[key]) <= set(route["premises"]), "Bridge names a non-premise")
    s.require(not set(binding["case_obligation_ids"]) & set(binding["shared_prerequisite_ids"]), "Cases and shared prerequisites must be distinct")
    if binding["case_obligation_ids"]:
        s.require(set(binding["case_obligation_ids"] + binding["shared_prerequisite_ids"]) == set(route["premises"]), "Finite partition must name every required premise")
        parent_scope = state["obligations"][route["conclusion"]]["claim"]["scope"]
        s.require(parent_scope["kind"] != "named", "Named domains require a quantified bridge, not finite coverage")
        for case in binding["case_obligation_ids"]:
            scope = state["obligations"][case]["claim"]["scope"]
            s.require(scope_subset(scope, parent_scope), "Case scope exceeds its parent")
    required_cases = {node["obligation_id"] for node in state["nodes"].values()
                      if node["category"] == "coverage" and node["route_id"] == route["id"]}
    s.require(required_cases <= set(binding["case_obligation_ids"]), "Admitted case coverage must be explicit in its bridge")
    return route_identity(route)


def scope_subset(child, parent):
    if child["kind"] != parent["kind"]:
        return False
    if child["kind"] == "case_ids":
        return set(child["case_ids"]) <= set(parent["case_ids"])
    if child["kind"] == "integer_interval":
        lo, hi = s.interval_bounds(child)
        lower, upper = s.interval_bounds(parent)
        return lower <= lo <= hi <= upper
    return child == parent


def accept_result(state, payload):
    s.closed(payload, "acceptance digest")
    value = payload["acceptance"]
    s.closed(value, "schema_version checkpoint_id checkpoint_digest obligation_id outcome classification standard dependency_ids route_bindings review audit")
    s.require(s.digest(value) == payload["digest"], "Acceptance digest differs", "digest_mismatch")
    s.integer(value["schema_version"], 1, 1)
    cp = reference(state["checkpoints"], value["checkpoint_id"], "checkpoint")
    s.require(s.digest(cp) == value["checkpoint_digest"], "Checkpoint differs", "digest_mismatch")
    s.require(cp["kind"] != "hypothesis", "Hypotheses grant no accepted progress")
    s.choice(value["outcome"], {"proof", "counterexample", "reduction", "obstruction"})
    expected_kind = "proof" if value["outcome"] == "counterexample" else value["outcome"]
    s.require(cp["kind"] == expected_kind or (cp["kind"] == "reduction" and value["outcome"] == "proof"), "Checkpoint kind and accepted outcome differ")
    s.choice(value["classification"], {"analytical", "computational"})
    s.choice(value["standard"], s.POLICIES)
    oid = value["obligation_id"]
    if oid is not None:
        claim = reference(state["obligations"], oid, "obligation")["claim"]
    else:
        origin = cp["origin"]
        s.require(origin["kind"] == "journal_move", "Standalone evidence needs its admitted producer")
        node = state["nodes"][origin["node_id"]]
        s.require(node["category"] == "standalone", "Null target requires standalone admission")
        claim = node["claim"]
    s.require(s.claim_identity(cp["claim"]) == s.claim_identity(claim), "Acceptance must target the exact claim, scope and assumptions")
    validate_review(value["review"], s.digest(cp), s.digest(cp["claim"]))
    if cp["origin"]["kind"] == "journal_move":
        node = state["nodes"][cp["origin"]["node_id"]]
        author = state["proposals"][node["proposal_id"]]["record"]["author"]
        reviewer = value["review"]["reviewer"]
        s.require(author["actor_id"] != reviewer["actor_id"] and author["attestation_id"] != reviewer["attestation_id"], "Producer cannot review its own result")
    s.strings(value["dependency_ids"])
    s.records(value["route_bindings"])
    policy = claim["proof_policy"]
    s.require(policy_allows(value, policy), "Evidence does not meet the required proof policy")
    dependencies = set()
    for dep in value["dependency_ids"]:
        closure = acceptance_closure(state, dep, policy)
        s.require(closure is not None, "Dependency is missing, stale, circular or below policy")
        dependencies.update(closure)
    s.require(all(state["acceptances"][dep]["checkpoint_id"] != cp["id"] for dep in dependencies), "Checkpoint cannot support itself")
    s.require(all(state["acceptances"][dep]["outcome"] == "proof" for dep in dependencies), "A proof dependency must be a proof")
    allowed_assumptions = set(claim["assumption_ids"])
    for dep in dependencies:
        inherited = state["checkpoints"][state["acceptances"][dep]["checkpoint_id"]]["claim"]
        s.require(set(inherited["assumption_ids"]) <= allowed_assumptions, "Dependency drops an assumption; use an explicit bridge")
    audit = value["audit"]
    s.closed(audit, "subject_digest dependency_ids evidence_digests provenance")
    s.validate_provenance(audit["provenance"])
    s.require(audit["subject_digest"] == s.digest(cp) and audit["dependency_ids"] == value["dependency_ids"]
              and audit["evidence_digests"] == cp["evidence_digests"], "Audit is bound to different evidence", "digest_mismatch")
    bindings = {}
    for binding in value["route_bindings"]:
        s.require(value["outcome"] == "proof", "Only proof can establish a bridge")
        content_digest = binding_valid(state, binding, oid)
        s.require(binding["route_id"] not in bindings, "Duplicate route binding")
        bindings[binding["route_id"]] = content_digest
    identity = allocate(state, "acceptance")
    progress_key = s.digest({"progress": progress_identity(cp), "outcome": value["outcome"]})
    progress_eligible = oid is not None and value["outcome"] != "counterexample" and not any(
        item.get("progress_key") == progress_key for item in state["acceptances"].values())
    state["acceptances"][identity] = dict(copy.deepcopy(value), id=identity, status="accepted",
                                         route_content_digests=bindings, progress_key=progress_key,
                                         progress_eligible=progress_eligible)
    refresh_obligations(state)


def _route_binding(state, route, policy):
    for aid, value in sorted(state["acceptances"].items()):
        if value["obligation_id"] != route["bridge"] or value["outcome"] != "proof":
            continue
        if value["route_content_digests"].get(route["id"]) != route_identity(route):
            continue
        closure = acceptance_closure(state, aid, policy)
        if closure is not None:
            for binding in value["route_bindings"]:
                if binding["route_id"] == route["id"]:
                    yield binding, closure


def _scope_coverage(parent, scopes):
    if parent["kind"] == "case_ids":
        wanted = set(parent["case_ids"])
        covered = set().union(*(set(x["case_ids"]) for x in scopes)) & wanted
        return {"accepted": len(covered), "total": len(wanted), "remaining": sorted(wanted - covered)}
    lower, upper = s.interval_bounds(parent)
    intervals = sorted(s.interval_bounds(scope) for scope in scopes)
    merged = []
    for lo, hi in intervals:
        lo, hi = max(lo, lower), min(hi, upper)
        if lo > hi:
            continue
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    remaining, cursor = [], lower
    for lo, hi in merged:
        if cursor < lo:
            remaining.append([cursor, lo - 1])
        cursor = hi + 1
    if cursor <= upper:
        remaining.append([cursor, upper])
    return {"accepted": sum(hi - lo + 1 for lo, hi in merged), "total": upper - lower + 1, "remaining": remaining}


def obligation_support(state, oid, policy=None, visiting=None):
    claim = state["obligations"][oid]["claim"]
    policies = {policy, claim["proof_policy"]}
    policy = "lean-kernel" if "lean-kernel" in policies else "certificate" if "certificate" in policies else "reviewed"
    visiting = set() if visiting is None else visiting
    if oid in visiting:
        return None
    visiting = visiting | {oid}
    for aid, value in sorted(state["acceptances"].items()):
        if value["obligation_id"] == oid and value["outcome"] == "proof":
            closure = acceptance_closure(state, aid, policy)
            if closure is not None:
                return closure
    for route in sorted(state["routes"].values(), key=lambda item: item["id"]):
        if route["conclusion"] != oid or route["status"] == "abandoned":
            continue
        for binding, closure in _route_binding(state, route, policy):
            if not _discharges_valid(state, claim, binding, policy):
                continue
            allowed = set(claim["assumption_ids"]) | set(binding["discharged_assumption_ids"])
            required = route["premises"] + [route["bridge"]]
            if any(not set(state["obligations"][dep]["claim"]["assumption_ids"]) <= allowed for dep in required):
                continue
            supports = [obligation_support(state, dep, policy, visiting) for dep in route["premises"]]
            if any(value is None for value in supports):
                continue
            cases = binding["case_obligation_ids"]
            if cases:
                scopes = [state["obligations"][case]["claim"]["scope"] for case in cases]
                if _scope_coverage(claim["scope"], scopes)["remaining"]:
                    continue
            return set(closure).union(*supports)
    return None


def _discharges_valid(state, parent_claim, binding, policy):
    for oid in binding["discharged_assumption_ids"]:
        claim = state["obligations"][oid]["claim"]
        if not set(claim["assumption_ids"]) <= set(parent_claim["assumption_ids"]):
            return False
        if obligation_support(state, oid, policy) is None:
            return False
    return True


def coverage(state, route_id):
    route = reference(state["routes"], route_id, "route")
    parent = state["obligations"][route["conclusion"]]["claim"]
    s.require(parent["scope"]["kind"] != "named", "Named obligations have no finite fraction")
    policy = state["contract"]["proof_policy"]
    best = _scope_coverage(parent["scope"], [])
    for binding, _ in _route_binding(state, route, policy):
        if not _discharges_valid(state, parent, binding, policy):
            continue
        allowed = set(parent["assumption_ids"]) | set(binding["discharged_assumption_ids"])
        common = binding["shared_prerequisite_ids"] + [route["bridge"]]
        if any(not set(state["obligations"][oid]["claim"]["assumption_ids"]) <= allowed for oid in common):
            continue
        if any(obligation_support(state, oid, policy) is None for oid in binding["shared_prerequisite_ids"]):
            continue
        scopes = [state["obligations"][oid]["claim"]["scope"] for oid in binding["case_obligation_ids"]
                  if set(state["obligations"][oid]["claim"]["assumption_ids"]) <= allowed
                  and obligation_support(state, oid, policy) is not None]
        result = _scope_coverage(parent["scope"], scopes)
        if result["accepted"] > best["accepted"]:
            best = result
    return best


def root_support(state):
    root = state["contract"]["root_obligation"]
    policy = state["contract"]["proof_policy"]
    proof = obligation_support(state, root, policy)
    counters = []
    for aid, value in sorted(state["acceptances"].items()):
        if value["obligation_id"] == root and value["outcome"] == "counterexample":
            closure = acceptance_closure(state, aid, policy)
            if closure is not None:
                counters.append(closure)
    s.require(not (proof is not None and counters), "Conflicting root proof and counterexample require audit", "evidence_conflict")
    if proof is not None or counters:
        return {"outcome": "proof" if proof is not None else "counterexample",
                "acceptance_ids": sorted(proof if proof is not None else counters[0])}
    return None


def refresh_obligations(state):
    for oid, obligation in state["obligations"].items():
        obligation["status"] = "accepted" if obligation_support(state, oid) is not None else "open"


def invalidate_evidence(state, payload):
    s.closed(payload, "acceptance_ids reason provenance")
    s.strings(payload["acceptance_ids"], nonempty=True)
    s.text(payload["reason"])
    s.validate_provenance(payload["provenance"])
    invalid = set(payload["acceptance_ids"])
    for aid in invalid:
        reference(state["acceptances"], aid, "acceptance")
    changed = True
    while changed:
        more = {aid for aid, value in state["acceptances"].items() if set(value["dependency_ids"]) & invalid}
        changed = not more <= invalid
        invalid.update(more)
    for aid in invalid:
        state["acceptances"][aid]["status"] = "invalidated"
    refresh_obligations(state)
    closure = state["control"]["closure"]
    if state["proof_status"] in {"proved", "disproved"} and closure is not None and invalid & set(closure["subject"]["acceptance_ids"]):
        state["proof_status"] = "invalidated"
        if state["execution_status"] != "paused":
            state["execution_status"] = "needs_replan"


def checkpoint_milestone(state, checkpoint_id):
    cp = reference(state["checkpoints"], checkpoint_id, "checkpoint")
    if cp["origin"]["kind"] != "journal_move":
        return False
    node = state["nodes"][cp["origin"]["node_id"]]
    criterion = next(x for x in node["checkpoint_criteria"] if x["criterion_id"] == cp["milestone_id"])
    kind = criterion["kind"]
    if kind == "hypothesis_recorded":
        return cp["kind"] == "hypothesis"
    for aid, value in state["acceptances"].items():
        if value["checkpoint_id"] != checkpoint_id or acceptance_closure(state, aid, node["claim"]["proof_policy"]) is None:
            continue
        if kind == "accepted_obligation" and value["outcome"] == "proof" and value["obligation_id"] == criterion["obligation_id"]:
            return True
        if kind == "accepted_case_set" and value["outcome"] == "proof" and value["obligation_id"] == criterion["obligation_id"]:
            scope = cp["claim"]["scope"]
            if scope["kind"] == "case_ids" and set(criterion["case_ids"]) <= set(scope["case_ids"]):
                return True
        if kind == "verified_reduction" and value["outcome"] in {"reduction", "proof"} and value["obligation_id"] == criterion["obligation_id"]:
            return True
        if kind == "verified_obstruction" and value["outcome"] == "obstruction" and node["route_id"] == criterion["route_id"]:
            return True
    return False


def complete_objective(state, payload):
    s.closed(payload, "closure digest")
    closure = payload["closure"]
    s.closed(closure, "subject review audit")
    s.require(s.digest(closure) == payload["digest"], "Closure digest differs", "digest_mismatch")
    subject = closure["subject"]
    s.closed(subject, "schema_version objective_id contract_digest outcome acceptance_ids evidence_digests local_deliveries deliverables")
    s.integer(subject["schema_version"], 1, 1)
    s.require(subject["objective_id"] == state["objective_id"] and subject["contract_digest"] == state["contract_digest"], "Closure targets another objective")
    s.choice(subject["outcome"], {"proof", "counterexample"})
    support = root_support(state)
    s.require(support is not None and subject["outcome"] == support["outcome"] and subject["acceptance_ids"] == support["acceptance_ids"], "Closure does not bind the full accepted root support")
    s.require(state["execution_status"] != "paused", "Paused objectives require explicit resume before completion")
    s.require(not state["control"]["pending_moves"] and not any(run["status"] != "terminal" for run in state["runs"].values()),
              "Pending execution must be reconciled before completion")
    s.require(state["control"]["state_error"] is None and state["control"]["focus"] == "focused", "Completion requires unambiguous valid objective state")
    s.strings(subject["evidence_digests"], nonempty=True)
    evidence, producers = set(), set()
    for aid in subject["acceptance_ids"]:
        cp = state["checkpoints"][state["acceptances"][aid]["checkpoint_id"]]
        evidence.update(cp["evidence_digests"])
        if cp["origin"]["kind"] == "journal_move":
            producers.add(cp["origin"]["node_id"])
    s.require(set(subject["evidence_digests"]) == evidence, "Closure omits contributing evidence")
    validate_review(closure["review"], s.digest(subject), s.digest(state["contract"]["original_claim"]))
    reviewer = closure["review"]["reviewer"]
    for nid in producers:
        author = state["proposals"][state["nodes"][nid]["proposal_id"]]["record"]["author"]
        s.require(author["actor_id"] != reviewer["actor_id"] and author["attestation_id"] != reviewer["attestation_id"], "Final reviewer must be independent of producers")
    audit = closure["audit"]
    s.closed(audit, "subject_digest acceptance_ids evidence_digests provenance")
    s.validate_provenance(audit["provenance"])
    s.require(audit["subject_digest"] == s.digest(subject) and audit["acceptance_ids"] == subject["acceptance_ids"] and audit["evidence_digests"] == subject["evidence_digests"], "Final audit binds different inputs", "digest_mismatch")
    delivered = set()
    for delivery in s.records(subject["local_deliveries"]):
        s.closed(delivery, "node_id inventory_digest checked_unit_digests consolidation_digest draft_digests evaluation_digests finish handoff_digest")
        nid = delivery["node_id"]
        s.require(nid in producers and nid not in delivered, "Local delivery must name one contributing node exactly once")
        delivered.add(nid)
        for key in ["inventory_digest", "consolidation_digest", "handoff_digest"]:
            s.digest_string(delivery[key])
        for key in ["checked_unit_digests", "draft_digests", "evaluation_digests"]:
            s.strings(delivery[key])
            for value in delivery[key]:
                s.digest_string(value)
        finish = delivery["finish"]
        s.closed(finish, "kind unused_child_ids")
        s.choice(finish["kind"], {"finished", "local_finish_pending_unused_children"})
        s.strings(finish["unused_child_ids"])
        if finish["kind"] == "finished":
            s.require(not finish["unused_child_ids"], "Finished node cannot have pending children")
        else:
            children = {key for key, node in state["nodes"].items() if node["native_parent"] == nid and node["status"] != "finished"}
            s.require(children and children == set(finish["unused_child_ids"]) and not children & producers,
                      "Only the exact unused unfinished native children can defer local finish")
    s.require(delivered == producers, "Every contributing local node needs checked cash-out artifacts")
    requirements = []
    for item in s.records(subject["deliverables"]):
        s.closed(item, "requirement digest")
        s.text(item["requirement"])
        s.digest_string(item["digest"])
        requirements.append(item["requirement"])
    s.require(sorted(requirements) == sorted(state["contract"]["required_deliverables"]), "Required deliverables are incomplete")
    state["control"]["closure"] = copy.deepcopy(closure)
    state["proof_status"] = "proved" if support["outcome"] == "proof" else "disproved"
    state["execution_status"] = "resolved"


EVENT_HANDLERS = {"checkpoint_recorded": record_checkpoint, "result_accepted": accept_result,
                  "evidence_invalidated": invalidate_evidence, "objective_completed": complete_objective}
