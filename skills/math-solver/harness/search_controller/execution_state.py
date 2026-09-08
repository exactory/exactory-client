"""Pure move reservations and execution lifecycle transitions."""

import copy

from . import schema as s
from .admission import reference, remaining_allowance, require_current_account, qualifying_progress
from .scheduler import next_action
from .problem_records import validate_journal_problem, validate_journal_line


def account_for_work(state, node_id, resource, units=1):
    node = reference(state["nodes"], node_id, "execution node")
    s.require(node["proposal_id"] is not None and node["status"] in {"admitted", "active", "waiting", "result_ready"},
              "Execution requires a nonterminal admitted node", "admission_required")
    account = require_current_account(state, node["account_id"])
    if account["renewal_basis"] is not None:
        s.require(qualifying_progress(state, account["renewal_basis"], account["renewal_basis_digest"]),
                  "Renewal basis is no longer accepted", "budget_exhausted")
    s.require(remaining_allowance(account)[resource] >= units, "Execution allowance is exhausted", "budget_exhausted")
    cap = state["contract"]["resource_policy"]["max_total_" + resource]
    if cap is not None:
        s.require(state["totals"]["historical_usage"] == "known", "Objective historical usage is unknown", "usage_unknown")
        s.require(state["totals"]["used_" + resource] + state["totals"]["reserved_" + resource] + units <= cap,
                  "Objective execution allowance is exhausted", "budget_exhausted")
    s.require(state["execution_status"] not in {"paused", "resolved"} and state["control"]["focus"] == "focused",
              "Execution is paused or outside the active objective", "execution_paused")
    return node, account


def reserve_move(state, payload):
    s.closed(payload, "reservation")
    value = payload["reservation"]
    modern = "purpose" in value
    s.closed(value, "id node_id account_id move pass strategy entry walk trigger_features step_cites problem_digest journal_prefix_digest" +
             (" purpose strategy_context_digest assessment_digest planning_digest" if modern else ""))
    node, account = account_for_work(state, value["node_id"], "moves")
    s.require(not state["control"]["pending_moves"] and not state["service"]["legacy_intents"] and not state["service"]["native_intents"],
              "A prior move or legacy operation needs reconciliation", "recovery_required")
    s.require(not any(run["status"] != "terminal" for run in state["runs"].values()), "A workload remains unresolved", "recovery_required")
    action = next_action(state)
    verification = action == {"kind": "prepare_result", "node_id": node["id"], "step": "verification"}
    s.require(verification or (action.get("kind") == "execute_node" and action.get("node_id") == node["id"]
                              and action.get("strategy", value["strategy"]) == value["strategy"]),
              "This node is not the admitted execution frontier", "frontier_required")
    s.require(modern or not state["control"]["strategy_refresh"]["enabled"],
              "New moves must bind the strategy assessment", "strategy_reassessment_required")
    if modern:
        from .strategy_refresh import assessment_status, require_research
        status = assessment_status(state)
        s.require(value["purpose"] == ("verification" if verification else "research"),
                  "Move purpose differs from the execution frontier")
        s.require(value["strategy_context_digest"] == status["context_digest"]
                  and value["assessment_digest"] == status["assessment_digest"],
                  "Move strategy context changed", "strategy_reassessment_stale")
        s.digest_string(value["planning_digest"])
        if not verification:
            require_research(state, node["id"], value["strategy"], value["problem_digest"], value["planning_digest"])
    s.require(value["account_id"] == account["id"], "Reservation account differs", "account_superseded")
    s.integer(value["move"], 1, 24)
    s.integer(value["pass"], 1, 3)
    s.require(value["id"] == "move-{}-{}".format(node["id"], value["move"]), "Move identity differs")
    s.require(value["id"] not in state["service"]["moves"], "Move is already reserved")
    for key in ["strategy", "entry", "walk"]:
        s.text(value[key])
    for key in ["trigger_features", "step_cites"]:
        s.strings(value[key])
    for key in ["problem_digest", "journal_prefix_digest"]:
        s.digest_string(value[key])
    state["service"]["moves"][value["id"]] = dict(copy.deepcopy(value), status="reserved")
    state["control"]["pending_moves"].append(value["id"])
    account["reserved_moves"] += 1
    state["totals"]["reserved_moves"] += 1
    previous = state["control"]["active_node_id"]
    if previous is not None and previous != node["id"] and state["nodes"][previous]["status"] == "active":
        state["nodes"][previous]["status"] = "waiting"
    node["status"] = "active"
    state["control"]["active_node_id"] = node["id"]
    state["execution_status"] = "running"


def journal_intended(state, payload):
    s.closed(payload, "reservation_id before_digest after_digest problem_digest" +
             (" problem_transition" if "problem_transition" in payload else "") +
             (" journal_line" if "journal_line" in payload else ""))
    move = reference(state["service"]["moves"], payload["reservation_id"], "reserved move")
    s.require(move["status"] == "reserved" and move["id"] in state["control"]["pending_moves"], "Journal requires its pending move", "reservation_required")
    s.require(not any(run["status"] != "terminal" for run in state["runs"].values()), "Workload must terminate before journalling", "recovery_required")
    s.require(payload["before_digest"] == move["journal_prefix_digest"], "Journal intent changed the reserved prefix")
    validate_journal_problem(state, move, payload)
    if "journal_line" in payload:
        validate_journal_line(payload["journal_line"], move, payload["problem_digest"])
        s.require("problem_transition" not in payload or payload["problem_transition"]["line"] == payload["journal_line"],
                  "Journal observation differs from its problem transition", "digest_mismatch")
    for key in ["before_digest", "after_digest", "problem_digest"]:
        s.digest_string(payload[key])
    s.require(move["id"] not in state["service"]["legacy_intents"], "Journal intent already exists", "recovery_required")
    state["service"]["legacy_intents"][move["id"]] = copy.deepcopy(payload)


def journal_acknowledged(state, payload):
    s.closed(payload, "node_id move reservation_id journal_prefix_digest problem_digest")
    value = reference(state["service"]["moves"], payload["reservation_id"], "reserved move")
    intent = reference(state["service"]["legacy_intents"], value["id"], "journal intent")
    s.require(value["status"] == "reserved" and payload["node_id"] == value["node_id"] and payload["move"] == value["move"]
              and payload["journal_prefix_digest"] == intent["after_digest"] and payload["problem_digest"] == intent["problem_digest"],
              "Journal acknowledgement differs from original reservation")
    account = state["accounts"][value["account_id"]]
    account["reserved_moves"] -= 1
    account["used_moves"] += 1
    state["totals"]["reserved_moves"] -= 1
    state["totals"]["used_moves"] += 1
    value["status"] = "journalled"
    state["control"]["pending_moves"].remove(value["id"])
    line = intent.get("journal_line", intent.get("problem_transition", {}).get("line"))
    if line is not None:
        state["service"]["journal_observations"][value["id"]] = copy.deepcopy(line)
    del state["service"]["legacy_intents"][value["id"]]
    state["service"]["journal_receipts"]["{}:{}".format(payload["node_id"], payload["move"])] = copy.deepcopy(payload)
    from .strategy_refresh import note_evidence
    note_evidence(state, "journal:" + value["id"])


EVENT_HANDLERS = {"move_reserved": reserve_move, "journal_intended": journal_intended,
                  "journal_acknowledged": journal_acknowledged}


def reserve_run(state, payload):
    s.closed(payload, "run")
    run = payload["run"]
    s.closed(run, "id node_id account_id reservation_id kind input_digest spec_digest task cwd snapshot_root output_root commands timeout_seconds environment threads expected_outputs dependency_enumeration executable_bindings reserved_units token requested_declaration requested_type_digest toolchain_digest toolchain_inventory_digest inspection_source_digest publication_prestate input_modes" +
             (" computation_digest" if "computation_digest" in run else "") +
             (" strategy_context_digest" if "strategy_context_digest" in run else ""))
    s.choice(run["kind"], {"command", "certificate", "lean"})
    units = 2 if run["kind"] == "lean" else 1
    s.require(run["reserved_units"] == units and len(run["commands"]) == units, "Command reservation count differs")
    node, account = account_for_work(state, run["node_id"], "runs", units)
    if "computation_digest" in run:
        from .computation import validate_run
        validate_run(state, node, run["kind"], run["computation_digest"], run["timeout_seconds"])
    else:
        s.require(state["proposals"][node["proposal_id"]]["record"]["schema_version"] == 1
                  and node["id"] not in state["service"]["computation_amendments"],
                  "New run reservations must pin their computation digest", "computation_required")
    if run["kind"] == "command":
        s.require(node["admission"]["task"]["kind"] in {"finite_decision", "finite_proof", "counterexample_search"}
                  and node["admission"]["contribution"]["necessity"] is not None,
                  "Generic computation requires reviewed finite-task admission", "admission_required")
    s.require(not any(value["status"] != "terminal" for value in state["runs"].values()), "Another workload is unresolved", "recovery_required")
    s.require(not state["service"]["legacy_intents"] and not state["service"]["native_intents"], "Native intent needs reconciliation", "recovery_required")
    move = reference(state["service"]["moves"], run["reservation_id"], "reserved move")
    s.require(move["status"] == "reserved" and move["node_id"] == node["id"] and move["account_id"] == account["id"] == run["account_id"],
              "Run requires the original current reserved move", "reservation_required")
    if run["kind"] == "command" and ("strategy_context_digest" in run or state["control"]["strategy_refresh"]["enabled"]):
        require_producer_context(state, run, move)
    s.require(run["task"] == node["admission"]["task"], "Run purpose or input domain differs from admission", "admission_required")
    s.integer(run["timeout_seconds"], 1, node["admission"]["limits"]["timeout_seconds"])
    s.require(run["id"] == "run-{:06d}".format(state["next_ids"]["run"]), "Run identity is not next")
    for key in ["input_digest", "spec_digest"]:
        s.digest_string(run[key])
    for key in ["token", "cwd", "snapshot_root", "output_root", "dependency_enumeration"]:
        s.text(run[key])
    for argv in s.records(run["commands"]):
        s.strings(argv, nonempty=True)
    for item in s.records(run["input_modes"]):
        s.closed(item, "path mode")
        s.text(item["path"])
        s.integer(item["mode"], 0, 0o777)
    for item in s.records(run["publication_prestate"]):
        s.closed(item, "path digest")
        s.text(item["path"])
        if item["digest"] is not None:
            s.digest_string(item["digest"])
    s.require(run["threads"] == node["admission"]["limits"]["workers"], "Thread limit differs from admission")
    state["next_ids"]["run"] += 1
    state["runs"][run["id"]] = dict(copy.deepcopy(run), status="reserved", identity=None,
        started_units=0, charged_units=0, result_digest=None, termination=None)
    account["reserved_runs"] += units
    state["totals"]["reserved_runs"] += units


def launch_run(state, payload):
    s.closed(payload, "run_id token identity")
    run = reference(state["runs"], payload["run_id"], "run")
    s.require(run["status"] == "reserved" and payload["token"] == run["token"], "Launch identity differs")
    if run["kind"] == "command" and "strategy_context_digest" in run:
        require_producer_context(state, run, state["service"]["moves"][run["reservation_id"]])
    identity = payload["identity"]
    s.closed(identity, "pid process_group start_identity token")
    s.integer(identity["pid"], 1)
    s.integer(identity["process_group"], 1)
    s.text(identity["start_identity"])
    s.require(identity["token"] == run["token"], "Launcher token differs")
    run.update(status="launched", identity=copy.deepcopy(identity))


def finish_run(state, payload):
    s.closed(payload, "run_id token status started_units charged_units result_digest termination legacy_result legacy_inspection outputs inspection")
    run = reference(state["runs"], payload["run_id"], "run")
    s.require(run["status"] != "terminal" and payload["token"] == run["token"], "Result is not for an unresolved original run")
    s.choice(payload["status"], {"terminal", "indeterminate"})
    s.integer(payload["started_units"], 0, run["reserved_units"])
    s.integer(payload["charged_units"], payload["started_units"], run["reserved_units"])
    s.optional_text(payload["result_digest"])
    if payload["result_digest"] is not None:
        s.digest_string(payload["result_digest"])
    s.text(payload["termination"])
    if payload["inspection"] is not None:
        inspection = payload["inspection"]
        s.closed(inspection, "printed_type_digest source_digest declaration_axioms correspondence_axioms")
        s.require(run["kind"] == "lean" and inspection["source_digest"] == run["inspection_source_digest"], "Inspection source differs")
        s.digest_string(inspection["printed_type_digest"])
        s.strings(inspection["declaration_axioms"])
        s.strings(inspection["correspondence_axioms"])
    for item in s.records(payload["outputs"]):
        s.closed(item, "path digest")
        s.require(item["path"] in run["expected_outputs"], "Result path was not declared")
        s.digest_string(item["digest"])
    for field in ["legacy_result", "legacy_inspection"]:
        if payload[field] is not None:
            s.closed(payload[field], "path digest")
            s.text(payload[field]["path"])
            s.digest_string(payload[field]["digest"])
    s.require(payload["status"] != "indeterminate" or payload["charged_units"] == run["reserved_units"], "Uncertain crashes retain all charges")
    account = state["accounts"][run["account_id"]]
    if run["status"] != "indeterminate":
        account["reserved_runs"] -= run["reserved_units"]
        state["totals"]["reserved_runs"] -= run["reserved_units"]
    charge = payload["charged_units"] - run["charged_units"]
    s.require(charge >= 0, "Uncertain previously charged work cannot be refunded")
    account["used_runs"] += charge
    state["totals"]["used_runs"] += charge
    run.update({key: copy.deepcopy(payload[key]) for key in payload if key not in {"run_id", "token"}})
    if run["status"] == "terminal":
        from .strategy_refresh import note_evidence
        note_evidence(state, "run:" + run["id"])


def require_producer_context(state, run, move):
    from .strategy_refresh import assessment_status, require_research
    s.require(move.get("purpose", "research") == "research", "A verification move cannot launch a producer", "verification_only")
    require_research(state, move["node_id"], move["strategy"], move["problem_digest"], move.get("planning_digest"))
    s.require(run.get("strategy_context_digest") == assessment_status(state)["context_digest"],
              "Research changed after the producer was reserved", "strategy_reassessment_stale")


EVENT_HANDLERS.update(run_reserved=reserve_run, run_launched=launch_run, run_finished=finish_run)


def native_intended(state, payload):
    s.closed(payload, "id node_id command args_digest pre_digest output_paths ownership_digest" +
             (" strategy" if "strategy" in payload else ""))
    s.require(not state["service"]["native_intents"] and not state["service"]["legacy_intents"]
              and not state["control"]["pending_moves"] and not any(run["status"] != "terminal" for run in state["runs"].values()),
              "Another native mutation or execution requires recovery", "recovery_required")
    node = reference(state["nodes"], payload["node_id"], "native producer")
    s.require(node["proposal_id"] is not None and node["status"] != "finished", "Native mutation requires a nonterminal admission", "admission_required")
    s.choice(payload["command"], {"plan", "rank", "fail", "stall", "check-unit", "finish"})
    if "strategy" in payload:
        s.optional_text(payload["strategy"])
        s.require((payload["command"] == "fail") == (payload["strategy"] is not None), "Failure intent must identify its strategy")
    s.text(payload["id"])
    s.strings(payload["output_paths"])
    s.digest_string(payload["args_digest"])
    s.digest_string(payload["pre_digest"])
    s.digest_string(payload["ownership_digest"])
    state["service"]["native_intents"][payload["id"]] = copy.deepcopy(payload)
    state["control"]["state_error"] = "Native intent {} ({}) requires reconciliation".format(payload["id"], payload["command"])


def native_acknowledged(state, payload):
    s.closed(payload, "id outcome post_digest diagnostics" if payload.get("outcome") == "failed" else "id outcome post_digest")
    intent = reference(state["service"]["native_intents"], payload["id"], "native intent")
    s.choice(payload["outcome"], {"succeeded", "unchanged", "failed"})
    if payload["outcome"] == "failed":
        s.strings(payload["diagnostics"], nonempty=True)
    s.digest_string(payload["post_digest"])
    del state["service"]["native_intents"][payload["id"]]
    state["service"]["native_receipts"][payload["id"]] = dict(copy.deepcopy(payload),
        node_id=intent["node_id"], command=intent["command"], args_digest=intent["args_digest"],
        pre_digest=intent["pre_digest"], recorded_revision=state["revision"] + 1)
    if payload["outcome"] == "succeeded" and intent.get("strategy") is not None:
        from .strategy_refresh import remember_failure
        remember_failure(state, intent["node_id"], intent["strategy"],
                         dict(kind="native_receipt", receipt=state["service"]["native_receipts"][payload["id"]]))
    if (state["control"]["state_error"] or "").startswith("Native intent " + payload["id"] + " "):
        state["control"]["state_error"] = None


EVENT_HANDLERS.update(native_intended=native_intended, native_acknowledged=native_acknowledged)
