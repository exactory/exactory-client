"""Reviewed proposal admission and stable, cumulative resource accounts."""

import copy

from . import schema as s


def allocate(state, kind):
    number = state["next_ids"][kind]
    state["next_ids"][kind] += 1
    return "{}-{:06d}".format(kind, number)


def reference(mapping, key, label):
    s.require(isinstance(key, str) and key in mapping, "Unknown " + label + " reference", "dangling_reference")
    return mapping[key]


def obligation_record(identity, claim, proposal_id=None):
    return {"id": identity, "schema_version": 1, "claim": copy.deepcopy(claim),
            "claim_digest": s.digest(claim), "proposal_id": proposal_id, "status": "open"}


def prepare_decomposition(state, proposal, proposal_id):
    """Resolve proposal-local keys without modifying the graph or ID counters."""
    obligations = copy.deepcopy(state["obligations"])
    routes = copy.deepcopy(state["routes"])
    obligation_refs = {key: key for key in obligations}
    route_refs = {key: key for key in routes}
    new_obligations, new_routes = [], []
    for item in proposal["decomposition"]["obligations"]:
        # Repeating an identical obligation cannot create another budget identity.
        identity = next((key for key, value in obligations.items()
                         if s.claim_identity(value["claim"]) == s.claim_identity(item["claim"])), None)
        if identity is None:
            identity = "obligation-{:06d}".format(state["next_ids"]["obligation"] + len(new_obligations))
            obligations[identity] = obligation_record(identity, item["claim"], proposal_id)
            new_obligations.append(identity)
        obligation_refs["new:" + item["key"]] = identity
    for item in proposal["decomposition"]["routes"]:
        identity = "route-{:06d}".format(state["next_ids"]["route"] + len(new_routes))
        route_refs["new:" + item["key"]] = identity
        conclusion = reference(obligation_refs, item["conclusion"], "conclusion")
        premises = [reference(obligation_refs, key, "premise") for key in item["premises"]]
        bridge = reference(obligation_refs, item["bridge"], "bridge")
        s.require(len(set(premises + [bridge])) == len(premises) + 1, "Route premises and bridge must be distinct")
        for node_id in item["alternative_order"]:
            node = reference(state["nodes"], node_id, "alternative node")
            s.require(node["obligation_id"] == conclusion, "Alternative targets another conclusion")
        routes[identity] = {"id": identity, "schema_version": 1, "conclusion": conclusion,
                            "premises": premises, "bridge": bridge, "proposal_id": proposal_id,
                            "proposal_digest": s.digest(proposal), "review_ids": [],
                            "alternative_order": list(item["alternative_order"]), "status": "hypothesis"}
        new_routes.append(identity)
    edges = {key: [] for key in obligations}
    for route in routes.values():
        edges[route["conclusion"]].extend(route["premises"] + [route["bridge"]])
    visiting, visited = set(), set()

    def visit(key):
        s.require(key not in visiting, "Proof routes must be acyclic", "dependency_cycle")
        if key in visited:
            return
        visiting.add(key)
        for child in edges[key]:
            visit(child)
        visiting.remove(key)
        visited.add(key)

    for key in edges:
        visit(key)
    reachable = set()

    def reach(key):
        if key not in reachable:
            reachable.add(key)
            for child in edges[key]:
                reach(child)

    reach(state["contract"]["root_obligation"])
    return obligations, routes, obligation_refs, route_refs, new_obligations, new_routes, reachable


def validate_proposal_context(state, proposal, proposal_id):
    graph = prepare_decomposition(state, proposal, proposal_id)
    obligations, routes, refs, route_refs, new_obligations, _, reachable = graph
    category = proposal["category"]
    relation = proposal["relationship"]
    if relation in s.CATEGORIES:
        s.require(relation == category, "Relationship conflicts with admission category")
    if relation == "prerequisite":
        s.require(category == "main", "Prerequisite must be a main proposal")
    s.require(proposal["limits"]["workers"] <= state["contract"]["resource_policy"]["max_workers"], "Worker limit exceeds the contract")
    allowed_assumptions = set(state["contract"]["assumption_ids"]) | set(state["obligations"])
    for claim in [proposal["claim"]] + [obligations[key]["claim"] for key in new_obligations]:
        s.require(set(claim["assumption_ids"]) <= allowed_assumptions, "Claim names undeclared assumptions", "dangling_reference")
    s.require(set(proposal["inherited_assumption_ids"]) <= set(proposal["claim"]["assumption_ids"]), "Inherited assumptions must be explicit in the claim")
    predecessor_id = proposal["logical_predecessor"]
    if predecessor_id is not None:
        predecessor = reference(state["nodes"], predecessor_id, "predecessor")
        if relation == "continuation":
            s.require(category == predecessor["category"], "Continuation must retain its original category")
    else:
        s.require(relation != "continuation", "Continuation requires a predecessor")
    if proposal["native_parent"] is not None:
        parent = reference(state["nodes"], proposal["native_parent"], "native parent")
        s.require(parent["native_parent"] is None and parent["status"] != "finished", "Native parent must be unfinished and not a native child")
    for node_id in proposal["equivalent_node_ids"]:
        reference(state["nodes"], node_id, "equivalent node")
    anchor = proposal["anchor"]
    if anchor["kind"] == "objective":
        s.require(anchor["digest"] == state["contract_digest"], "Initial objective snapshot differs", "digest_mismatch")
        s.require(predecessor_id is None and not proposal["inherited_evidence"],
                  "A successor or inherited result requires a recorded checkpoint")
    else:
        checkpoint = reference(state["checkpoints"], anchor["checkpoint_id"], "checkpoint")
        s.require(anchor["digest"] == s.digest(checkpoint), "Predecessor checkpoint differs", "digest_mismatch")
        s.require(set(proposal["inherited_evidence"]) <= set(checkpoint["evidence_digests"]), "Inherited evidence is absent from the checkpoint")
    if category == "standalone":
        s.require(proposal["target_obligation"] is None, "Standalone proposal cannot claim root coverage")
        s.require(not new_obligations and not proposal["decomposition"]["routes"], "Standalone admission cannot install a root decomposition")
        s.require(proposal["contribution"]["route"] is None, "Standalone contribution has no root route")
        target = None
    else:
        target = reference(refs, proposal["target_obligation"], "target obligation")
        s.require(s.digest(proposal["claim"]) == obligations[target]["claim_digest"], "Claim or scope differs from the frozen obligation", "claim_mismatch")
        s.require(target in reachable, "Target has no proposed deduction to the root", "unreachable_obligation")
        route_ref = proposal["contribution"]["route"]
        if target != state["contract"]["root_obligation"] or route_ref is not None:
            route_id = reference(route_refs, route_ref, "contribution route")
            route = routes[route_id]
            s.require(target in route["premises"] + [route["bridge"], route["conclusion"]]
                      and route["conclusion"] in reachable, "Contribution route does not connect the target")
    necessity = proposal["contribution"]["necessity"]
    if necessity is not None:
        necessary_target = (None if category == "standalone" and necessity["obligation_id"] is None
                            else reference(refs, necessity["obligation_id"], "necessary obligation"))
        s.require(necessary_target == target, "Finite decision serves a different obligation")
    if category == "coverage":
        coverage = proposal["contribution"]["coverage"]
        parent = reference(refs, coverage["parent_obligation"], "coverage parent")
        route = routes[reference(route_refs, coverage["partition_route"], "partition route")]
        s.require(route["conclusion"] == parent and target in route["premises"], "Coverage is not a premise of its declared parent route")
        child_scope, parent_scope = coverage["scope"], obligations[parent]["claim"]["scope"]
        s.require(child_scope == proposal["claim"]["scope"], "Coverage scope differs from the exact claim")
        s.require(child_scope["kind"] == parent_scope["kind"], "Coverage scope kinds differ")
        if child_scope["kind"] == "integer_interval":
            a, b = s.interval_bounds(child_scope)
            c, d = s.interval_bounds(parent_scope)
            s.require(c <= a <= b <= d, "Coverage exceeds the parent interval")
        elif child_scope["kind"] == "case_ids":
            s.require(set(child_scope["case_ids"]) <= set(parent_scope["case_ids"]), "Coverage exceeds the parent case set")
        else:
            s.require(child_scope == parent_scope, "Named coverage requires exact scope identity")
    for criterion in proposal["checkpoint_criteria"] + proposal["retreat_criteria"]:
        if "obligation_id" in criterion:
            reference(refs, criterion["obligation_id"], "criterion obligation")
        if "route_id" in criterion:
            reference(route_refs, criterion["route_id"], "criterion route")
        if "strategy_id" in criterion:
            s.require(criterion["strategy_id"] in [item["method"] for item in proposal["studies"]["strategies"]], "Criterion names an unstudied strategy")
    return graph, target


def record_proposal(state, payload):
    s.closed(payload, "proposal digest")
    proposal = payload["proposal"]
    s.validate_proposal(proposal)
    s.require(payload["digest"] == s.digest(proposal), "Proposal digest differs", "digest_mismatch")
    identity = allocate(state, "proposal")
    validate_proposal_context(state, proposal, identity)
    state["proposals"][identity] = {"id": identity, "schema_version": 1, "record": proposal,
                                    "digest": payload["digest"], "status": "proposed"}


def record_review(state, payload):
    s.closed(payload, "proposal_id review digest")
    proposal = reference(state["proposals"], payload["proposal_id"], "proposal")
    s.require(proposal["status"] == "proposed", "Admission reviews are immutable after admission")
    review = payload["review"]
    s.validate_review(review)
    s.require(payload["digest"] == s.digest(review), "Review digest differs", "digest_mismatch")
    s.require(review["subject_digest"] == proposal["digest"] and
              review["claim_digest"] == s.digest(proposal["record"]["claim"]), "Review inputs differ", "digest_mismatch")
    author, reviewer = proposal["record"]["author"], review["reviewer"]
    s.require(author["actor_id"] != reviewer["actor_id"] and
              author["attestation_id"] != reviewer["attestation_id"], "Proposal author cannot supply its independent review", "review_not_independent")
    for old in state["reviews"].values():
        if old["proposal_id"] == payload["proposal_id"]:
            previous = old["record"]["reviewer"]
            s.require(previous["actor_id"] != reviewer["actor_id"] and
                      previous["attestation_id"] != reviewer["attestation_id"], "Duplicate reviewer provenance", "review_not_independent")
    identity = allocate(state, "review")
    state["reviews"][identity] = {"id": identity, "schema_version": 1, "proposal_id": payload["proposal_id"],
                                  "record": review, "digest": payload["digest"]}


def approving_reviews(state, proposal_id, proposal):
    reviews = [item for item in state["reviews"].values() if item["proposal_id"] == proposal_id]
    required = {"mathematical_substance", "assumptions", "scope", "equivalence"}
    required |= {"novelty", "significance"} if proposal["category"] == "standalone" else {"root_connection"}
    if proposal["task"]["kind"] != "proof":
        required.add("necessity")
    if proposal["budget"]["mode"] == "renew":
        required.add("renewal_basis")
    for item in reviews:
        review = item["record"]
        s.require(review["decision"] == "approve" and not any(objection["blocking"] for objection in review["unresolved_objections"]),
                  "Every supplied review must approve without blocking objections", "admission_required")
        s.require(all(review["findings"][key].strip() for key in required), "Review omits a required substantive finding", "admission_required")
    s.require(len(reviews) >= (2 if proposal["category"] == "standalone" else 1), "Independent proposal approval is required", "admission_required")
    return [item["id"] for item in reviews]


def qualifying_progress(state, checkpoint_id, expected_digest):
    checkpoint = reference(state["checkpoints"], checkpoint_id, "renewal checkpoint")
    s.require(s.digest(checkpoint) == expected_digest, "Renewal checkpoint differs", "digest_mismatch")
    return checkpoint["kind"] in {"proof", "reduction", "obstruction"} and any(
        item["checkpoint_id"] == checkpoint_id and item["checkpoint_digest"] == expected_digest
        and item["status"] == "accepted" for item in state["acceptances"].values())


def progress_identity(checkpoint):
    """Renaming a checkpoint does not change its recorded mathematical progress."""
    return s.digest({key: value for key, value in checkpoint.items() if key != "id"})


def remaining_allowance(account):
    """Unknown historical use is a blocked account, never a zero estimate."""
    s.require(account["historical_usage"] == "known" or account["renewal_basis"] is not None,
              "Historical usage is unknown; reviewed renewal is required", "usage_unknown")
    return {"moves": account["max_moves"] - account["used_moves"] - account["reserved_moves"],
            "runs": account["max_runs"] - account["used_runs"] - account["reserved_runs"]}


def _current_account(state, account_id):
    """Resolve an account's lineage to its unique current allowance segment."""
    account = reference(state["accounts"], account_id, "budget account")
    lineage = {key: item for key, item in state["accounts"].items()
               if item["lineage_owner"] == account["lineage_owner"]}
    predecessors = {item["predecessor_account_id"] for item in lineage.values()}
    current = set(lineage) - predecessors
    s.require(len(current) == 1, "Budget lineage has no unique current segment", "corrupt_state")
    return lineage[next(iter(current))]


def require_current_account(state, account_id):
    """Reject new work on historical allowance segments, even if unused."""
    account = _current_account(state, account_id)
    s.require(account_id == account["id"], "Budget account has a renewed successor", "account_superseded")
    return account


def choose_account(state, proposal, target):
    budget = proposal["budget"]
    candidates = []
    for node in reversed(list(state["nodes"].values())):
        if (target is not None and node["obligation_id"] == target) or s.claim_identity(node["claim"]) == s.claim_identity(proposal["claim"]) or node["id"] in proposal["equivalent_node_ids"]:
            candidates.append(node["account_id"])
    predecessor = proposal["logical_predecessor"]
    if proposal["relationship"] == "continuation":
        candidates.insert(0, state["nodes"][predecessor]["account_id"])
        s.require(budget["account_id"] in {None, candidates[0]}, "Continuation must inherit its predecessor account")
    if budget["account_id"] is not None:
        selected = reference(state["accounts"], budget["account_id"], "budget account")
        s.require(not candidates or any(state["accounts"][key]["lineage_owner"] == selected["lineage_owner"]
                                       for key in candidates), "Budget account is unrelated to this investigation")
        candidates.insert(0, budget["account_id"])
    account = None
    if candidates:
        if budget["account_id"] is None and proposal["relationship"] != "continuation":
            account = _current_account(state, candidates[0])
        else:
            account = require_current_account(state, candidates[0])
    renewal = budget["mode"] == "renew"
    if renewal:
        s.require(account is not None and qualifying_progress(state, budget["basis_checkpoint_id"], budget["basis_checkpoint_digest"]),
                  "Renewal needs accepted progress or relevant accepted external evidence", "budget_exhausted")
        s.require(proposal["relationship"] != "continuation", "Continuation cannot reset an account", "budget_exhausted")
        lineage = {key: item for key, item in state["accounts"].items()
                   if item["lineage_owner"] == account["lineage_owner"]}
        s.require(all(item["reserved_moves"] == 0 and item["reserved_runs"] == 0
                      for item in lineage.values()),
                  "Pending reservations must be reconciled before renewal", "budget_exhausted")
        basis = progress_identity(state["checkpoints"][budget["basis_checkpoint_id"]])
        for previous in state["accounts"].values():
            if previous["lineage_owner"] == account["lineage_owner"] and previous["renewal_basis"] is not None:
                used_checkpoint = reference(state["checkpoints"], previous["renewal_basis"], "previous renewal")
                s.require(progress_identity(used_checkpoint) != basis,
                          "The same progress cannot renew a lineage twice", "budget_exhausted")
        for node in state["nodes"].values():
            old = state["proposals"][node["proposal_id"]]["record"]
            if node["account_id"] in lineage:
                s.require(old["method"] != proposal["method"] and node["id"] not in proposal["equivalent_node_ids"],
                          "An identical attempt cannot renew through renaming", "budget_exhausted")
    if account is not None and not renewal:
        if account["renewal_basis"] is not None:
            s.require(qualifying_progress(state, account["renewal_basis"], account["renewal_basis_digest"]),
                      "Renewal evidence is no longer accepted", "budget_exhausted")
        remaining = remaining_allowance(account)
        s.require(remaining["moves"] > 0 and remaining["runs"] > 0, "Inherited account is exhausted", "budget_exhausted")
        s.require(proposal["limits"]["max_moves"] <= account["max_moves"] and
                  proposal["limits"]["max_runs"] <= account["max_runs"], "Inherited account limits cannot increase", "budget_exhausted")
        return account["id"]
    identity = allocate(state, "account")
    history_known = account is None or account["historical_usage"] == "known"
    state["accounts"][identity] = {
        "id": identity, "schema_version": 1, "owner_obligation": target,
        "owner_claim_digest": s.digest(proposal["claim"]),
        "lineage_owner": account["lineage_owner"] if account else identity,
        "predecessor_account_id": account["id"] if account else None,
        "max_moves": proposal["limits"]["max_moves"], "max_runs": proposal["limits"]["max_runs"],
        "used_moves": 0, "used_runs": 0, "reserved_moves": 0, "reserved_runs": 0,
        "historical_usage": "known" if history_known else "unknown",
        "historical_moves": (account["historical_moves"] + account["used_moves"] + account["reserved_moves"] if account else 0) if history_known else None,
        "historical_runs": (account["historical_runs"] + account["used_runs"] + account["reserved_runs"] if account else 0) if history_known else None,
        "renewal_basis": budget["basis_checkpoint_id"] if renewal else None,
        "renewal_basis_digest": budget["basis_checkpoint_digest"] if renewal else None,
    }
    return identity


def admit_proposal(state, payload):
    s.closed(payload, "proposal_id")
    record = reference(state["proposals"], payload["proposal_id"], "proposal")
    s.require(record["status"] == "proposed", "Proposal is already admitted")
    proposal = record["record"]
    review_ids = approving_reviews(state, record["id"], proposal)
    graph, target = validate_proposal_context(state, proposal, record["id"])
    s.require(state["control"]["nonprogress_replans"] < 3, "Three replans without verified progress require a pause", "replan_limit")
    for resource in ["moves", "runs"]:
        cap = state["contract"]["resource_policy"]["max_total_" + resource]
        if cap is not None:
            s.require(state["totals"]["historical_usage"] == "known", "Objective usage is unknown under a total cap", "usage_unknown")
            s.require(state["totals"]["used_" + resource] + state["totals"]["reserved_" + resource] < cap,
                      "Objective total allowance is exhausted", "budget_exhausted")
    s.require(not any(node["attack_slug"] == proposal["attack_slug"] for node in state["nodes"].values()), "Attack slug is already bound")
    account_id = choose_account(state, proposal, target)
    obligations, routes, refs, route_refs, new_obligations, new_routes, _ = graph
    state["obligations"], state["routes"] = obligations, routes
    state["next_ids"]["obligation"] += len(new_obligations)
    state["next_ids"]["route"] += len(new_routes)
    for route_id in new_routes:
        routes[route_id]["review_ids"] = review_ids[:]
    criteria = copy.deepcopy(proposal["checkpoint_criteria"])
    retreat = copy.deepcopy(proposal["retreat_criteria"])
    for criterion in criteria + retreat:
        if "obligation_id" in criterion:
            criterion["obligation_id"] = refs[criterion["obligation_id"]]
        if "route_id" in criterion:
            criterion["route_id"] = route_refs[criterion["route_id"]]
    identity = allocate(state, "node")
    state["nodes"][identity] = {
        "id": identity, "schema_version": 1, "attack_slug": proposal["attack_slug"],
        "obligation_id": target, "claim": copy.deepcopy(proposal["claim"]),
        "claim_digest": s.digest(proposal["claim"]), "role": proposal["role"],
        "category": proposal["category"], "relationship": proposal["relationship"],
        "logical_predecessor": proposal["logical_predecessor"], "native_parent": proposal["native_parent"],
        "checkpoint_id": proposal["anchor"].get("checkpoint_id"),
        "proposal_id": record["id"], "account_id": account_id, "status": "admitted",
        "route_id": route_refs.get(proposal["contribution"]["route"]),
        "checkpoint_criteria": criteria, "retreat_criteria": retreat,
        "admission": {"proposal_digest": record["digest"], "review_ids": review_ids,
                      "contribution": copy.deepcopy(proposal["contribution"]),
                      "studies": copy.deepcopy(proposal["studies"]),
                      "task": copy.deepcopy(proposal["task"]), "limits": copy.deepcopy(proposal["limits"])},
    }
    record["status"] = "admitted"


EVENT_HANDLERS = {"proposal_recorded": record_proposal, "review_recorded": record_review,
                  "proposal_admitted": admit_proposal}
