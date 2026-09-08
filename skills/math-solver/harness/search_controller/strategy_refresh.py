"""Versioned strategy assessments over immutable research observations."""

import copy

from . import schema as s


def initial_policy():
    return {"enabled": False, "assessments": [], "failures": {}, "evidence_ordinals": {}}


def note_evidence(state, identity, revision=None):
    state["control"]["strategy_refresh"]["evidence_ordinals"][identity] = (
        state["revision"] + 1 if revision is None else revision)


def remember_failure(state, node_id, strategy, source):
    policy = state["control"]["strategy_refresh"]
    identity = "strategy-failure:" + node_id + ":" + strategy
    if identity in policy["failures"]:
        return
    observations = state["service"]["journal_observations"]
    related = [line for mid, line in observations.items()
               if state["service"]["moves"][mid]["node_id"] == node_id and line["strategy"] == strategy]
    last = max(related, key=lambda line: line["move"], default=None)
    policy["failures"][identity] = {
        "kind": "strategy_failure", "node_id": node_id, "strategy": strategy,
        "observation": last["output"] if last else "The native harness recorded this strategy as failed",
        "problem_digest": last["problem_digest"] if last else None,
        "source": copy.deepcopy(source), "revision": state["revision"] + 1}
    note_evidence(state, identity)


def evidence(state):
    """Return planning evidence; its presence grants no mathematical credit."""
    result = {}
    for identity, value in state["checkpoints"].items():
        result["checkpoint:" + identity] = {"kind": "checkpoint", "checkpoint": copy.deepcopy(value)}
    for identity, value in state["acceptances"].items():
        result["acceptance:" + identity] = {"kind": "acceptance", "acceptance": copy.deepcopy(value)}
    for mid, move in state["service"]["moves"].items():
        receipt = state["service"]["journal_receipts"].get("{}:{}".format(move["node_id"], move["move"]))
        if receipt is None:
            continue
        line = state["service"]["journal_observations"].get(mid)
        changed = receipt["problem_digest"] != move["problem_digest"]
        if changed or line is None or line["failure_signal_fired"]:
            result["journal:" + mid] = {
                "kind": "journal", "node_id": move["node_id"], "strategy": move["strategy"],
                "problem_changed": changed, "problem_digest": receipt["problem_digest"],
                "journal_prefix_digest": receipt["journal_prefix_digest"],
                "observation": copy.deepcopy(line)}
    for identity, run in state["runs"].items():
        if run["status"] == "terminal":
            result["run:" + identity] = {"kind": "run", "node_id": run["node_id"],
                "result_digest": run["result_digest"], "termination": run["termination"],
                "interpretation": copy.deepcopy(state["service"]["run_interpretations"].get(identity))}
    result.update(copy.deepcopy(state["control"]["strategy_refresh"]["failures"]))
    for index, retreat in enumerate(state["control"]["retreats"]):
        result["retreat:" + str(index + 1)] = {"kind": "retreat", "retreat": copy.deepcopy(retreat)}
    ordinals = state["control"]["strategy_refresh"]["evidence_ordinals"]
    for identity, value in result.items():
        value.setdefault("revision", ordinals.get(identity, 0))
    return result


def inventory(state):
    rows = []
    for node in state["nodes"].values():
        if node["proposal_id"] is None or node["status"] in {"finished", "retreated", "imported"}:
            continue
        for item in node["admission"]["studies"]["strategies"]:
            rows.append({"node_id": node["id"], "strategy": item["method"],
                         "study_digest": item["digest"], "claim_digest": node["claim_digest"],
                         "proposal_id": node["proposal_id"]})
    return sorted(rows, key=lambda row: (row["node_id"], row["strategy"]))


def context(state):
    from .proof import obligation_support
    return {"objective_id": state["objective_id"], "contract_digest": state["contract_digest"],
            "evidence": evidence(state), "strategies": inventory(state),
            "planning_priorities": {
                key: copy.deepcopy(state["control"][key]) for key in
                ["selected_routes", "obligation_orders", "deferred_node_ids"]} | {
                    "route_alternative_orders": {rid: route["alternative_order"][:]
                                                 for rid, route in state["routes"].items()}},
            "remaining_obligation_ids": sorted(oid for oid in state["obligations"]
                                               if obligation_support(state, oid) is None)}


def assessment_status(state):
    current = context(state)
    records = state["control"]["strategy_refresh"]["assessments"]
    latest = records[-1] if records else None
    digest = s.digest(current)
    required = bool(current["evidence"] or latest) and (
        latest is None or latest["assessment"]["context_digest"] != digest)
    return {"context_digest": digest, "required": required,
            "policy_enabled": state["control"]["strategy_refresh"]["enabled"],
            "upgrade_required": not state["control"]["strategy_refresh"]["enabled"],
            "assessment_id": latest["id"] if latest else None,
            "assessment_digest": latest["digest"] if latest else None}


def enable_policy(state, payload):
    from .problem_records import validate_journal_line
    s.closed(payload, "version journal_observations")
    s.integer(payload["version"], 1, 1)
    policy = state["control"]["strategy_refresh"]
    s.require(not policy["enabled"], "Strategy reassessment policy is already enabled")
    seen = set()
    for item in s.records(payload["journal_observations"]):
        s.closed(item, "reservation_id line")
        mid = item["reservation_id"]
        s.require(mid not in seen and mid in state["service"]["moves"], "Unknown or duplicate historical move")
        seen.add(mid)
        move = state["service"]["moves"][mid]
        receipt = state["service"]["journal_receipts"].get("{}:{}".format(move["node_id"], move["move"]))
        s.require(receipt is not None, "Historical strategy context requires an acknowledged journal")
        validate_journal_line(item["line"], move, receipt["problem_digest"])
        old = state["service"]["journal_observations"].get(mid)
        s.require(old is None or old == item["line"], "Historical journal observation changed", "digest_mismatch")
        state["service"]["journal_observations"][mid] = copy.deepcopy(item["line"])
    policy["enabled"] = True


def _pair(value):
    s.text(value["node_id"])
    s.text(value["strategy"])
    return value["node_id"], value["strategy"]


def _related_failures(state, node, strategy):
    ancestors = {node["id"]}
    predecessor = node["logical_predecessor"]
    while predecessor is not None and predecessor not in ancestors:
        ancestors.add(predecessor)
        predecessor = state["nodes"][predecessor]["logical_predecessor"]
    account = state["accounts"][node["account_id"]]
    result = {}
    for identity, failure in state["control"]["strategy_refresh"]["failures"].items():
        source = state["nodes"][failure["node_id"]]
        same_account = state["accounts"][source["account_id"]]["lineage_owner"] == account["lineage_owner"]
        if failure["strategy"] == strategy and (source["id"] in ancestors or same_account
                or s.claim_identity(source["claim"]) == s.claim_identity(node["claim"])):
            result[identity] = failure
    return result


def _references(values, available, nonempty=False):
    s.strings(values, nonempty=nonempty)
    s.require(set(values) <= set(available), "Assessment refers to evidence outside its research context")


def record_assessment(state, payload):
    from .proof import validate_review
    s.closed(payload, "assessment review digest")
    value = payload["assessment"]
    s.closed(value, "context_digest author what_changed remaining_obligation_ids considered_failure_ids "
             "assessments plan_bindings strategy_order next_hypotheses no_new_hypothesis_reason")
    s.require(not state["control"]["pending_moves"] and not state["service"]["native_intents"]
              and not state["service"]["legacy_intents"]
              and all(run["status"] == "terminal" for run in state["runs"].values()),
              "Reconcile and journal pending work before strategy reassessment", "recovery_required")
    from .computation import require_interpreted
    require_interpreted(state)
    current = context(state)
    s.require(value["context_digest"] == s.digest(current),
              "Research changed after the strategy assessment was prepared", "strategy_reassessment_stale")
    s.require(payload["digest"] == s.digest(value), "Strategy assessment digest differs", "digest_mismatch")
    s.validate_provenance(value["author"])
    s.text(value["what_changed"])
    s.strings(value["remaining_obligation_ids"])
    s.require(set(value["remaining_obligation_ids"]) == set(current["remaining_obligation_ids"]),
              "Strategy assessment must preserve every residual obligation")
    failures = state["control"]["strategy_refresh"]["failures"]
    _references(value["considered_failure_ids"], failures)
    s.require(set(value["considered_failure_ids"]) == set(failures), "Assessment omits a retained strategy failure")
    expected = {_pair(row) for row in current["strategies"]}
    seen, continuing = set(), set()
    for row in s.records(value["assessments"]):
        s.closed(row, "node_id strategy disposition reason next_action evidence_ids failure_resolutions")
        pair = _pair(row)
        s.require(pair in expected and pair not in seen, "Assessment has an unknown or duplicate strategy")
        seen.add(pair)
        s.choice(row["disposition"], {"continue", "revise", "defer", "retire"})
        s.text(row["reason"])
        s.text(row["next_action"])
        _references(row["evidence_ids"], current["evidence"], nonempty=bool(current["evidence"]))
        node = state["nodes"][row["node_id"]]
        related = _related_failures(state, node, row["strategy"])
        resolutions = {}
        for resolution in s.records(row["failure_resolutions"]):
            s.closed(resolution, "failure_id changed_input evidence_ids")
            fid = resolution["failure_id"]
            s.require(fid in related and fid not in resolutions, "Failure resolution does not match this strategy")
            s.text(resolution["changed_input"])
            _references(resolution["evidence_ids"], current["evidence"], nonempty=True)
            newer = [current["evidence"][eid] for eid in resolution["evidence_ids"]
                     if current["evidence"][eid]["revision"] > related[fid]["revision"]]
            s.require(any(item["kind"] in {"checkpoint", "acceptance", "journal", "run"} for item in newer),
                      "Retry requires evidence recorded after the failed strategy", "failed_strategy")
            resolutions[fid] = resolution
        if row["disposition"] == "continue":
            s.require(not any(failure["node_id"] == node["id"] for failure in related.values()),
                      "A natively failed strategy requires a reviewed successor", "failed_strategy")
            s.require(set(resolutions) == set(related), "An inherited failed strategy needs changed-input evidence", "failed_strategy")
            continuing.add(pair)
    s.require(seen == expected, "Assessment must classify every current admitted strategy")
    order = []
    for row in s.records(value["strategy_order"]):
        s.closed(row, "node_id strategy")
        order.append(_pair(row))
    s.require(len(order) == len(set(order)) and set(order) == continuing,
              "Strategy order must contain exactly the continuing strategies")
    bindings = value["plan_bindings"]
    s.require(isinstance(bindings, dict) and set(bindings) == {nid for nid, _ in continuing},
              "Every continuing node must bind its current native plan")
    for binding in bindings.values():
        s.closed(binding, "problem_digest preconditions_digest openings_digest ranking_digest")
        for digest in binding.values():
            s.digest_string(digest)
    hypotheses = s.records(value["next_hypotheses"])
    s.optional_text(value["no_new_hypothesis_reason"])
    s.require(bool(hypotheses) != (value["no_new_hypothesis_reason"] is not None),
              "Record new hypotheses or explain why the existing hypotheses still suffice")
    for hypothesis in hypotheses:
        s.closed(hypothesis, "statement evidence_ids success_criterion failure_signal")
        for field in ["statement", "success_criterion", "failure_signal"]:
            s.text(hypothesis[field])
        _references(hypothesis["evidence_ids"], current["evidence"], nonempty=bool(current["evidence"]))
    validate_review(payload["review"], payload["digest"], s.digest(state["contract"]["original_claim"]))
    reviewer = payload["review"]["reviewer"]
    authors = [value["author"]] + [state["proposals"][row["proposal_id"]]["record"]["author"]
                                    for row in current["strategies"]]
    s.require(all(reviewer["actor_id"] != author["actor_id"] and
                  reviewer["attestation_id"] != author["attestation_id"] for author in authors),
              "Strategy assessment requires an independent reviewer", "review_not_independent")
    policy = state["control"]["strategy_refresh"]
    s.require(policy["enabled"], "Strategy reassessment policy must be enabled")
    previous = policy["assessments"][-1]["id"] if policy["assessments"] else None
    policy["assessments"].append(dict(copy.deepcopy(payload),
        id="assessment-{:06d}".format(len(policy["assessments"]) + 1), predecessor_id=previous,
        context=copy.deepcopy(current), recorded_revision=state["revision"] + 1))


def require_research(state, node_id, strategy=None, problem_digest=None, planning_digest=None):
    status = assessment_status(state)
    s.require(not status["required"],
              "Reassess strategies against the current evidence with search strategy-context and search reassess before new research",
              "strategy_reassessment_required")
    records = state["control"]["strategy_refresh"]["assessments"]
    if not records:
        return
    value = records[-1]["assessment"]
    rows = [row for row in value["assessments"] if row["node_id"] == node_id
            and (strategy is None or row["strategy"] == strategy) and row["disposition"] == "continue"]
    s.require(bool(rows), "This strategy is excluded by the current assessment", "strategy_not_selected")
    binding = value["plan_bindings"][node_id]
    s.require(problem_digest is None or binding["problem_digest"] == problem_digest,
              "The local problem changed after strategy reassessment", "strategy_reassessment_stale")
    s.require(planning_digest is None or s.digest(binding) == planning_digest,
              "The native plan changed after strategy reassessment", "strategy_reassessment_stale")


def research_selection(state):
    """Called after recovery, proof work and cash-out, before new research."""
    if not state["control"]["strategy_refresh"]["enabled"]:
        return None
    status = assessment_status(state)
    if status["required"]:
        return {"kind": "reassess_strategies", "context_digest": status["context_digest"],
                "previous_assessment_id": status["assessment_id"]}
    records = state["control"]["strategy_refresh"]["assessments"]
    if not records:
        return None
    from .scheduler import ready
    control = state["control"]
    for row in records[-1]["assessment"]["strategy_order"]:
        node = state["nodes"][row["node_id"]]
        if node["category"] != "standalone" and ready(state, node):
            return dict(kind="execute_node", **row)
    externally_blocked = control["main_external_block"] or any(
        facts.get("external_block") for nid, facts in control["node_facts"].items()
        if state["nodes"][nid]["category"] != "standalone")
    interval = control["side_interval"]
    for row in records[-1]["assessment"]["strategy_order"]:
        node = state["nodes"][row["node_id"]]
        allocated = (interval is not None and interval["node_id"] == node["id"]
                     and node["account_id"] == interval["account_id"]
                     and state["accounts"][node["account_id"]]["used_moves"] - interval["start_used_moves"] < interval["max_moves"])
        if node["category"] == "standalone" and (externally_blocked or allocated) and ready(state, node):
            return dict(kind="execute_node", **row)
    # Do not let the previous active node escape the reviewed dispositions.
    active = control["active_node_id"]
    if active is not None:
        from .scheduler import retreat_due
        node = state["nodes"][active]
        criterion = retreat_due(state, active)
        if node["status"] not in {"finished", "retreated"} and criterion is not None:
            return {"kind": "retreat", "node_id": active, "criterion": criterion}
    return {"kind": "replan", "round": control["nonprogress_replans"] + 1,
            "obligation_ids": context(state)["remaining_obligation_ids"]}


EVENT_HANDLERS = {"strategy_policy_enabled": enable_policy, "strategies_reassessed": record_assessment}
