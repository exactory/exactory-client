"""Deterministic frontier selection and durable, objective-level user control."""

import copy

from . import schema as s
from .admission import reference, remaining_allowance, require_current_account
from .errors import SearchError
from .proof import acceptance_closure, obligation_support, root_support


def initial_control():
    return {"nonprogress_replans": 0, "progress_fingerprints": [], "stop_count": 0,
            "stop_deliveries": {}, "summary_issued": False, "stop_decision": {"kind": "allow_stop"},
            "pause_reason": None, "focus": "focused", "state_error": None,
            "active_node_id": None, "retreat_node_id": None, "pending_moves": [],
            "node_facts": {}, "selected_routes": {}, "closure": None,
            "main_external_block": None, "side_interval": None, "resume_record": None,
            "resume_ids": [], "side_instruction_ids": [], "retreats": []}


def ready(state, node):
    if node["status"] not in {"admitted", "active", "waiting"}:
        return False
    facts = state["control"]["node_facts"].get(node["id"], {})
    if facts.get("suspended") or facts.get("external_block"):
        return False
    if node["obligation_id"] and obligation_support(state, node["obligation_id"]) is not None:
        return False
    try:
        account = require_current_account(state, node["account_id"])
        if remaining_allowance(account)["moves"] <= 0:
            return False
    except SearchError:
        return False
    for resource in ["moves", "runs"]:
        cap = state["contract"]["resource_policy"]["max_total_" + resource]
        if cap is not None and (state["totals"]["historical_usage"] != "known" or
                               state["totals"]["used_" + resource] + state["totals"]["reserved_" + resource] >= cap):
            return False
    # A claimed implication already carries its hypotheses. An unconditional
    # consumer must first satisfy separately recorded execution prerequisites.
    assumptions = set(node["claim"]["assumption_ids"])
    waiting = list(facts.get("waiting_on", []))
    if node["route_id"]:
        route = state["routes"][node["route_id"]]
        if route["conclusion"] == node["obligation_id"]:
            waiting.extend(route["premises"] + [route["bridge"]])
    return all(oid in assumptions or obligation_support(state, oid) is not None
               for oid in waiting) and not retreat_due(state, node["id"])


def retreat_due(state, node_id):
    node = state["nodes"][node_id]
    account = state["accounts"][node["account_id"]]
    facts = state["control"]["node_facts"].get(node_id, {})
    for criterion in node["retreat_criteria"]:
        kind = criterion["kind"]
        if kind in {"move_limit", "run_limit"}:
            if account["used_moves" if kind == "move_limit" else "used_runs"] >= criterion["threshold"]:
                return criterion
        elif kind == "stagnation_window" and facts.get("stagnation_moves", 0) >= criterion["threshold"]:
            return criterion
        elif kind == "strategy_failure" and criterion["strategy_id"] in facts.get("failed_strategies", []):
            return criterion
        elif kind == "method_prerequisite_failed":
            oid = criterion["obligation_id"]
            for aid, value in state["acceptances"].items():
                if value["obligation_id"] == oid and value["outcome"] == "counterexample" and acceptance_closure(state, aid, node["claim"]["proof_policy"]) is not None:
                    return criterion
    return None


def _ordered_nodes(state, oid):
    order = []
    for route in sorted(state["routes"].values(), key=lambda item: item["id"]):
        if route["conclusion"] == oid:
            order.extend(route["alternative_order"])
    order.extend(sorted(state["nodes"]))
    seen = set()
    for nid in order:
        if nid not in seen:
            seen.add(nid)
            node = state["nodes"][nid]
            if node["obligation_id"] == oid and node["category"] != "standalone":
                yield node


def _frontier(state, oid, visiting=None):
    visiting = set() if visiting is None else visiting
    if oid in visiting or obligation_support(state, oid) is not None:
        return None
    visiting = visiting | {oid}
    routes = [r for r in state["routes"].values() if r["conclusion"] == oid and r["status"] != "abandoned"]
    selected = state["control"]["selected_routes"].get(oid)
    routes.sort(key=lambda r: (r["id"] != selected, r["id"]))
    for route in routes:
        for dep in route["premises"] + [route["bridge"]]:
            action = _frontier(state, dep, visiting)
            if action:
                return action
    for node in _ordered_nodes(state, oid):
        if ready(state, node):
            return {"kind": "execute_node", "node_id": node["id"]}
    return None


def next_action(state):
    """Return one closed tagged action; never mutate state or spend resources."""
    control = state["control"]
    if state["execution_status"] == "paused":
        return {"kind": "paused", "reason": control["pause_reason"] or "Execution is paused"}
    if control["state_error"] is not None:
        return {"kind": "blocked", "reason": control["state_error"]}
    if control["focus"] != "focused":
        return {"kind": "handoff", "reason": "Objective focus is " + control["focus"]}
    if state["execution_status"] == "resolved":
        return {"kind": "resolved", "proof_status": state["proof_status"]}
    for run_id, run in sorted(state["runs"].items()):
        if run["status"] in {"launched", "indeterminate"}:
            return {"kind": "execution_pending", "run_id": run_id, "status": run["status"]}
    if control["pending_moves"]:
        return {"kind": "reconcile_move", "move_id": control["pending_moves"][0]}
    for run_id, run in sorted(state["runs"].items()):
        if run["status"] == "reserved":
            return {"kind": "reconcile_run", "run_id": run_id}
    for nid, facts in sorted(control["node_facts"].items()):
        if facts.get("result_action"):
            if facts["result_action"] == "verification":
                try:
                    account = require_current_account(state, state["nodes"][nid]["account_id"])
                    allowance = remaining_allowance(account)
                    s.require(allowance["runs"] > 0 and allowance["moves"] > 0, "Verification allowance is exhausted", "budget_exhausted")
                except SearchError as exc:
                    return {"kind": "blocked", "reason": exc.code}
            return {"kind": "prepare_result", "node_id": nid, "step": facts["result_action"]}
    try:
        support = root_support(state)
    except SearchError as exc:
        return {"kind": "blocked", "reason": exc.code}
    if support is not None:
        return {"kind": "finalize_root", "outcome": support["outcome"], "acceptance_ids": support["acceptance_ids"]}
    for nid, facts in sorted(control["node_facts"].items()):
        if facts.get("cashout_action"):
            return {"kind": "local_cashout", "node_id": nid, "step": facts["cashout_action"]}
    active = control["active_node_id"]
    if active is not None:
        node = state["nodes"][active]
        if node["category"] != "standalone":
            if ready(state, node):
                return {"kind": "execute_node", "node_id": active}
            if node["status"] not in {"finished", "retreated"} and retreat_due(state, active) is not None:
                return {"kind": "retreat", "node_id": active, "criterion": retreat_due(state, active)}
            for child in sorted(state["nodes"].values(), key=lambda item: item["id"]):
                if child["logical_predecessor"] == active and child["relationship"] == "continuation" and ready(state, child):
                    return {"kind": "execute_node", "node_id": child["id"]}
            facts = control["node_facts"].get(active, {})
            for oid in facts.get("waiting_on", []):
                action = _frontier(state, oid)
                if action:
                    return action
    ancestor = control["retreat_node_id"]
    seen = set()
    while ancestor is not None and ancestor not in seen:
        seen.add(ancestor)
        node = state["nodes"][ancestor]
        for candidate in _ordered_nodes(state, node["obligation_id"]):
            if candidate["id"] not in seen and ready(state, candidate):
                return {"kind": "execute_node", "node_id": candidate["id"]}
        ancestor = node["logical_predecessor"]
    action = _frontier(state, state["contract"]["root_obligation"])
    if action:
        return action
    externally_blocked = control["main_external_block"] or any(
        facts.get("external_block") for nid, facts in control["node_facts"].items()
        if state["nodes"][nid]["category"] != "standalone")
    if externally_blocked or control["side_interval"]:
        for node in sorted(state["nodes"].values(), key=lambda item: item["id"]):
            interval = control["side_interval"]
            allocated = (interval is not None and interval["node_id"] == node["id"]
                         and node["account_id"] == interval["account_id"] and
                         state["accounts"][node["account_id"]]["used_moves"] - interval["start_used_moves"] < interval["max_moves"])
            if node["category"] == "standalone" and (externally_blocked or allocated) and ready(state, node):
                return {"kind": "execute_node", "node_id": node["id"]}
    return {"kind": "replan", "round": control["nonprogress_replans"] + 1,
            "obligation_ids": sorted(oid for oid in state["obligations"] if obligation_support(state, oid) is None)}


def record_control(state, payload):
    action = payload.get("action")
    control = state["control"]
    s.choice(action, {"pause", "resume", "focus", "hook_stop", "side_interval"})
    if action == "pause":
        s.closed(payload, "action reason")
        s.text(payload["reason"])
        state["execution_status"] = "paused"
        control["pause_reason"] = payload["reason"]
    elif action == "resume":
        s.closed(payload, "action objective_id message_id session_id instruction provenance")
        s.require(payload["objective_id"] == state["objective_id"], "Resume refers to another objective")
        for key in ["message_id", "session_id", "instruction"]:
            s.text(payload[key])
        s.validate_provenance(payload["provenance"])
        resume_id = s.digest({key: payload[key] for key in ["objective_id", "message_id", "session_id"]})
        s.require(resume_id not in control["resume_ids"], "Resume instruction was already consumed")
        control["resume_ids"].append(resume_id)
        control.update(stop_count=0, summary_issued=False, pause_reason=None,
                       nonprogress_replans=0, resume_record=copy.deepcopy(payload), focus="focused")
        state["execution_status"] = "needs_replan"
    elif action == "focus":
        s.closed(payload, "action focus provenance")
        s.choice(payload["focus"], {"focused", "ambiguous", "unrelated"})
        s.validate_provenance(payload["provenance"])
        control["focus"] = payload["focus"]
    elif action == "side_interval":
        s.closed(payload, "action node_id max_moves message_id provenance")
        node = reference(state["nodes"], payload["node_id"], "side node")
        s.require(node["category"] == "standalone", "Side interval requires standalone admission")
        s.integer(payload["max_moves"], 1, 24)
        s.text(payload["message_id"])
        s.validate_provenance(payload["provenance"])
        s.require(payload["message_id"] not in control["side_instruction_ids"], "Side allocation instruction already consumed")
        account = require_current_account(state, node["account_id"])
        s.require(payload["max_moves"] <= remaining_allowance(account)["moves"], "Side interval exceeds remaining allowance")
        control["side_instruction_ids"].append(payload["message_id"])
        control["side_interval"] = {"node_id": node["id"], "account_id": node["account_id"],
                                    "start_used_moves": account["used_moves"], "max_moves": payload["max_moves"]}
    else:
        s.closed(payload, "action delivery_id session_id turn_id stop_hook_active")
        for key in ["delivery_id", "session_id", "turn_id"]:
            s.optional_text(payload[key])
        s.require(payload["stop_hook_active"] is None or type(payload["stop_hook_active"]) is bool, "Stop flag must be boolean or null")
        delivery = payload["delivery_id"]
        upcoming = next_action(state)
        if delivery is not None and delivery in control["stop_deliveries"]:
            prior = control["stop_deliveries"][delivery]
            control["stop_decision"] = ({"kind": "continue", "action": upcoming}
                                        if prior["kind"] == "continue" and upcoming["kind"] not in {"paused", "resolved", "handoff", "blocked"}
                                        else {"kind": "allow_stop"})
            return
        if upcoming["kind"] in {"paused", "resolved", "handoff", "blocked"}:
            decision = {"kind": "allow_stop"}
        else:
            control["stop_count"] += 1
            if control["stop_count"] >= 40:
                state["execution_status"] = "paused"
                control["pause_reason"] = "Objective continuation limit reached"
                control["summary_issued"] = True
                decision = {"kind": "summary_then_stop", "reason": control["pause_reason"]}
            else:
                decision = {"kind": "continue", "action": upcoming}
        control["stop_decision"] = decision
        if delivery is not None:
            control["stop_deliveries"][delivery] = copy.deepcopy(decision)


def record_replan(state, payload):
    s.closed(payload, "route_orders progress_acceptance_ids reason")
    s.text(payload["reason"])
    s.records(payload["route_orders"])
    s.strings(payload["progress_acceptance_ids"])
    control = state["control"]
    new = set()
    for aid in payload["progress_acceptance_ids"]:
        value = reference(state["acceptances"], aid, "progress acceptance")
        s.require(acceptance_closure(state, aid, state["contract"]["proof_policy"]) is not None, "Replan progress is not usable")
        s.require(value["outcome"] in {"proof", "reduction", "obstruction"}, "Result is not qualifying progress")
        fingerprint = value["progress_key"]
        if value["progress_eligible"] and fingerprint not in control["progress_fingerprints"]:
            new.add(fingerprint)
    for order in payload["route_orders"]:
        s.closed(order, "route_id alternative_order selected")
        route = reference(state["routes"], order["route_id"], "route")
        s.strings(order["alternative_order"])
        s.require(type(order["selected"]) is bool, "Selected must be boolean")
        for nid in order["alternative_order"]:
            s.require(reference(state["nodes"], nid, "alternative")["obligation_id"] == route["conclusion"], "Alternative targets another conclusion")
        route["alternative_order"] = list(order["alternative_order"])
        if order["selected"]:
            control["selected_routes"][route["conclusion"]] = route["id"]
    control["progress_fingerprints"].extend(sorted(new))
    control["nonprogress_replans"] = 0 if new else control["nonprogress_replans"] + 1
    if control["nonprogress_replans"] >= 3:
        state["execution_status"] = "paused"
        control["pause_reason"] = "Three replans without new verified progress"


def record_node_facts(state, payload):
    s.closed(payload, "facts")
    facts = payload["facts"]
    s.closed(facts, "node_id status result_action cashout_action waiting_on failed_strategies stagnation_moves external_block dependency_route_ids dependency_assumption_ids")
    node = reference(state["nodes"], facts["node_id"], "node")
    allowed_statuses = {"admitted", "active", "waiting", "result_ready", "finished"}
    if node["status"] == "retreated":
        allowed_statuses = {"retreated", "finished"}
    elif node["status"] == "finished":
        allowed_statuses = {"finished"}
    s.choice(facts["status"], allowed_statuses)
    if facts["result_action"] is not None:
        s.choice(facts["result_action"], {"verification", "snapshot", "acceptance"})
    if facts["cashout_action"] is not None:
        s.choice(facts["cashout_action"], {"inventory", "unit_checks", "consolidation", "draft", "evaluation", "finish", "handoff"})
    s.optional_text(facts["external_block"])
    s.integer(facts["stagnation_moves"])
    for key in ["waiting_on", "failed_strategies", "dependency_route_ids", "dependency_assumption_ids"]:
        s.strings(facts[key])
    for oid in facts["waiting_on"]:
        reference(state["obligations"], oid, "waiting premise")
    for rid in facts["dependency_route_ids"]:
        reference(state["routes"], rid, "dependent route")
    for oid in facts["dependency_assumption_ids"]:
        s.require(oid in state["obligations"] or oid in state["contract"]["assumption_ids"], "Unknown assumption")
    s.require(set(facts["failed_strategies"]) <= {x["method"] for x in node["admission"]["studies"]["strategies"]}, "Failure names an unstudied strategy")
    previous = state["control"]["node_facts"].get(node["id"], {})
    saved = copy.deepcopy(facts)
    saved["suspended"] = previous.get("suspended", False)
    state["control"]["node_facts"][node["id"]] = saved
    node["status"] = facts["status"]
    if facts["status"] == "active":
        active = state["control"]["active_node_id"]
        s.require(active is None or active == node["id"] or state["nodes"][active]["status"] != "active", "Only one node may be active")
        state["control"]["active_node_id"] = node["id"]
        if state["execution_status"] not in {"paused", "resolved"}:
            state["execution_status"] = "running"


def retreat_node(state, payload):
    s.closed(payload, "retreat")
    value = payload["retreat"]
    s.closed(value, "node_id criterion failed_hypothesis observation last_checkpoint_id remaining_assumption_ids reconsideration abandoned_route_ids abandoned_assumption_ids")
    node = reference(state["nodes"], value["node_id"], "retreat node")
    s.require(node["status"] not in {"retreated", "finished"}, "Node already stopped")
    s.require(value["criterion"] in node["retreat_criteria"] and retreat_due(state, node["id"]) == value["criterion"], "Declared retreat predicate has not fired")
    for key in ["failed_hypothesis", "observation", "reconsideration"]:
        s.text(value[key])
    for key in ["remaining_assumption_ids", "abandoned_route_ids", "abandoned_assumption_ids"]:
        s.strings(value[key])
    if value["last_checkpoint_id"] is not None:
        cp = reference(state["checkpoints"], value["last_checkpoint_id"], "last usable checkpoint")
        s.require(cp["kind"] == "hypothesis" or any(a["checkpoint_id"] == cp["id"] and a["status"] == "accepted" for a in state["acceptances"].values()), "Checkpoint has no usable result")
    for rid in value["abandoned_route_ids"]:
        route = reference(state["routes"], rid, "abandoned route")
        s.require(node["obligation_id"] in route["premises"] + [route["bridge"], route["conclusion"]], "Retreat cannot abandon an unrelated route")
        route["status"] = "abandoned"
    assumptions = set(state["contract"]["assumption_ids"]) | set(state["obligations"])
    s.require(set(value["remaining_assumption_ids"] + value["abandoned_assumption_ids"]) <= assumptions, "Unknown retreat assumption")
    descendants = {node["id"]}
    changed = True
    while changed:
        more = {nid for nid, item in state["nodes"].items() if item["logical_predecessor"] in descendants}
        changed = not more <= descendants
        descendants.update(more)
    for nid in descendants - {node["id"]}:
        child = state["nodes"][nid]
        facts = state["control"]["node_facts"].setdefault(nid, {})
        dependent = bool(set(facts.get("dependency_route_ids", []) + [child["route_id"]]) & set(value["abandoned_route_ids"]) or
                         set(facts.get("dependency_assumption_ids", []) + child["claim"]["assumption_ids"]) & set(value["abandoned_assumption_ids"]))
        if dependent:
            facts["suspended"] = True
            if child["status"] not in {"finished", "retreated"}:
                child["status"] = "waiting"
    node["status"] = "retreated"
    state["control"]["retreats"].append(copy.deepcopy(value))
    state["control"]["retreat_node_id"] = node["id"]
    state["control"]["active_node_id"] = None
    if state["execution_status"] != "paused":
        state["execution_status"] = "needs_replan"


EVENT_HANDLERS = {"control_recorded": record_control, "replan_recorded": record_replan,
                  "node_facts_recorded": record_node_facts, "node_retreated": retreat_node}
