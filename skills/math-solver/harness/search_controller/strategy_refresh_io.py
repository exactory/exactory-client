"""Filesystem audits and read-only context for reviewed strategy reassessment."""

import copy
from pathlib import Path

from . import schema as s
from .errors import SearchError
from .storage import safe_path, _strict_json
from .strategy_refresh import context, assessment_status, enable_policy, record_assessment


def policy_payload(controller, state, content):
    observations = []
    for mid, move in state["service"]["moves"].items():
        if mid in state["service"]["journal_observations"]:
            continue
        receipt = state["service"]["journal_receipts"].get("{}:{}".format(move["node_id"], move["move"]))
        if receipt is None:
            continue
        raw = content.get_artifact(receipt["journal_prefix_digest"])
        lines = raw.splitlines()
        s.require(raw.endswith(b"\n") and len(lines) == move["move"],
                  "Historical journal prefix is incomplete", "recovery_conflict")
        line = _strict_json(lines[-1], "recovery_conflict")
        observations.append({"reservation_id": mid, "line": line})
    return {"version": 1, "journal_observations": observations}


def effective_state(controller, state, content=None):
    if state["control"]["strategy_refresh"]["enabled"]:
        return state
    preview = copy.deepcopy(state)
    enable_policy(preview, policy_payload(controller, state, content or controller.store))
    return preview


def effective_next_action(controller, state):
    from .scheduler import next_action
    current = effective_state(controller, state)
    action = next_action(current)
    if action["kind"] == "execute_node" and stale_plan_nodes(controller, current):
        status = assessment_status(current)
        return {"kind": "reassess_strategies", "context_digest": status["context_digest"],
                "previous_assessment_id": status["assessment_id"]}
    return action


def _plan_files(controller, node):
    result = {}
    for name in ["problem", "preconditions", "openings", "ranking"]:
        path = safe_path(controller.root, node["attack_slug"] + "/" + name + ".json")
        s.require(path.is_file() and not path.is_symlink(),
                  "Prepare the native " + name + ".json before strategy reassessment", "strategy_plan_stale")
        result[name] = _strict_json(path.read_bytes(), "invalid_input")
    return result


def plan_binding(controller, node):
    import attack
    files = _plan_files(controller, node)
    return {"problem_digest": attack.compute_problem_digest(files["problem"]),
            "preconditions_digest": s.digest(files["preconditions"]),
            "openings_digest": s.digest(files["openings"]),
            "ranking_digest": s.digest(files["ranking"])}


def stale_plan_nodes(controller, state):
    records = state["control"]["strategy_refresh"]["assessments"]
    result = []
    if records:
        for node_id, expected in records[-1]["assessment"]["plan_bindings"].items():
            try:
                changed = plan_binding(controller, state["nodes"][node_id]) != expected
            except (SearchError, OSError):
                changed = True
            if changed:
                result.append(node_id)
    return sorted(result)


def audit_research_plans(controller, state, move=None):
    s.require(not stale_plan_nodes(controller, state),
              "Native planning files changed after strategy reassessment", "strategy_reassessment_stale")
    if move is not None and "planning_digest" in move:
        current = plan_binding(controller, state["nodes"][move["node_id"]])
        s.require(s.digest(current) == move["planning_digest"],
                  "The native plan changed after move reservation", "strategy_reassessment_stale")


def plan_defects(controller, node, strategy):
    """Check native eligibility without fabricating or reserving a research move."""
    import attack
    files = _plan_files(controller, node)
    problem = files["problem"]
    defects = list(attack.find_problem_defects(problem))
    if problem.get("claim") != node["claim"]["statement"]:
        defects.append("Native problem differs from its admitted claim")
    if files["openings"].get("problem_digest") != attack.compute_problem_digest(problem):
        defects.append("Run plan for the current problem before reassessment")
    if defects:
        return defects
    workspace = safe_path(controller.root, node["attack_slug"])
    moves = attack.read_journal(workspace)
    directory = controller.strategies_dir or Path(attack.__file__).resolve().parent.parent / "strategies"
    methods = attack.load_strategies(directory)
    s.require(strategy in methods, "The admitted strategy file is unavailable", "strategy_plan_stale")
    walk = strategy if not moves else moves[-1]["walk"]
    transition = bool(moves and moves[-1]["strategy"] != strategy)
    if transition:
        walk += "+" + strategy
    entry = methods[strategy]["entries"][0]
    candidate = {"strategy": strategy, "entry": entry, "walk": walk,
                 "trigger_features": ["claim"], "step_cites": ["claim"] if transition else []}
    return list(attack.find_move_flow_defects(candidate, moves, workspace, methods, problem))


def describe_context(controller, state):
    current = effective_state(controller, state)
    value = context(current)
    result = dict(assessment_status(current), context=value, plan_bindings={}, native_eligibility=[])
    for row in value["strategies"]:
        node = current["nodes"][row["node_id"]]
        try:
            result["plan_bindings"][node["id"]] = plan_binding(controller, node)
            defects = plan_defects(controller, node, row["strategy"])
        except (SearchError, OSError) as error:
            defects = [str(error)]
        result["native_eligibility"].append({"node_id": node["id"], "strategy": row["strategy"],
                                             "eligible": not defects, "defects": defects})
    result["policy_enabled"] = state["control"]["strategy_refresh"]["enabled"]
    result["upgrade_required"] = not result["policy_enabled"]
    result["stale_plan_node_ids"] = stale_plan_nodes(controller, current)
    result["required"] = result["required"] or bool(result["stale_plan_node_ids"])
    return result


def build_assessment(controller, state, spec, content):
    from .evidence import audit_state
    s.closed(spec, "assessment review")
    value = {"assessment": copy.deepcopy(spec["assessment"]), "review": copy.deepcopy(spec["review"]),
             "digest": s.digest(spec["assessment"])}
    # Structural and review checks precede any persisted artifacts or local reads.
    record_assessment(copy.deepcopy(state), value)
    s.require(not audit_state(controller.root, state, content),
              "Audit and invalidate stale accepted evidence before strategy reassessment", "audit_failed")
    for node_id, expected in value["assessment"]["plan_bindings"].items():
        node = state["nodes"][node_id]
        s.require(plan_binding(controller, node) == expected,
                  "Native planning files changed after the assessment review", "strategy_reassessment_stale")
        files = _plan_files(controller, node)
        for name, data in files.items():
            if name == "problem":
                from .problem_records import pin_problem
                pin_problem(content, data)
            else:
                content.put_blob(data)
    for row in value["assessment"]["assessments"]:
        if row["disposition"] == "continue":
            defects = plan_defects(controller, state["nodes"][row["node_id"]], row["strategy"])
            s.require(not defects, "Strategy cannot execute under its native plan: " + "; ".join(defects),
                      "strategy_plan_stale")
    content.put_blob(context(state))
    content.put_blob(value["assessment"])
    content.put_blob(value["review"])
    return value
