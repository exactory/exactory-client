"""Native harness admission guards and journal reconciliation."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import uuid

from . import schema as s
from .errors import SearchError
from .storage import safe_path
from .admission import reference
from .evidence import audit_admission, proposal_inputs
from .execution_state import account_for_work
from .problem_records import audit_journal_append, load_problem_before, pin_problem, validate_problem


MUTATIONS = {"plan", "rank", "journal", "verify", "fail", "stall", "check-unit", "finish"}


@contextmanager
def task_write_guard(args):
    """Serialize task maintenance with native snapshot creation and reconciliation."""
    from .service import Controller
    if args.command != "task" or args.task_command not in {"add", "done"}:
        yield
        return
    controller = Controller(args.attack_root, args.strategies)
    if not controller.store.tree_path.exists():
        yield
        return
    # Hold the transaction lock through the read, edit, and write of tasks.json.
    # A check outside this lock could race with a newly recorded native intent.
    with controller.store._writer_lock():
        state = controller.status()
        s.require(not state["service"]["native_intents"],
                  "A native operation requires reconciliation of its original intent", "recovery_required")
        s.require(not state["service"]["pending_effect_ids"],
                  "Recover pending filesystem initialization before task maintenance", "recovery_required")
        yield


def guard_legacy(controller, command, slug, details):
    s.slug(slug)
    safe_path(controller.root, slug)
    parent = details.get("parent")
    if parent is not None:
        s.slug(parent)
        safe_path(controller.root, parent)
    s.require(not (command == "init" and parent is not None),
              "Native children require search propose, independent review, and search admit with native_parent; init --from does not create a child",
              "admission_required")
    for field in ["step_dir", "unit"]:
        if details.get(field) is not None:
            safe_path(controller.root, slug + "/" + str(details[field]))
    if command not in MUTATIONS and not (command == "init" and parent is not None):
        return None
    s.require(controller.store.tree_path.exists(), "Initialize the controller and admit this attack before research", "admission_required")
    state = controller.status()
    s.require(not state["service"]["native_intents"], "A native operation requires reconciliation of its original intent", "recovery_required")
    s.require(not state["service"]["pending_effect_ids"], "Recover pending filesystem initialization before legacy work", "recovery_required")
    node = next((n for n in state["nodes"].values() if n["attack_slug"] == slug), None)
    s.require(node is not None and node["proposal_id"] is not None,
              "Research requires a reviewed controller admission", "admission_required")
    forbidden = {"finished", "imported"} | ({"retreated"} if command not in {"stall", "check-unit", "finish"} else set())
    s.require(node["status"] not in forbidden
              and not safe_path(controller.root, slug + "/units/FINISHED.json").exists(),
              "A terminal attack cannot receive new research mutations", "terminal_attack")
    if command not in {"journal", "verify"}:
        s.require(not state["control"]["pending_moves"] and not state["service"]["legacy_intents"]
                  and not any(run["status"] != "terminal" for run in state["runs"].values()),
                  "Reconcile pending moves and workloads before another native mutation", "recovery_required")
    if command in {"journal", "verify"}:
        pending = state["control"]["pending_moves"]
        s.require(len(pending) == 1 and state["service"]["moves"][pending[0]]["node_id"] == node["id"],
                  "Begin a controller move before execution or journalling", "reservation_required")
    if command in {"plan", "rank", "verify"}:
        with controller.store._writer_lock():
            current = controller.status()
            audit_work(controller, current, current["nodes"][node["id"]], controller.store)
    return node


def audit_work(controller, state, node, content):
    from .research import audit_amendment as audit_foundation_amendment, effective_foundation
    audit_foundation_amendment(state, node, content)
    from .computation_io import audit_amendment
    audit_amendment(controller.root, state, node, content)
    proposal = state["proposals"][node["proposal_id"]]
    s.require(content.get_blob(proposal["digest"]) == proposal["record"], "Admission proposal changed", "digest_mismatch")
    proposal_inputs(proposal["record"], content)
    audit_admission(controller.root, state, proposal["record"], content, effective_foundation(state, node))
    for identity in node["admission"]["review_ids"]:
        review = state["reviews"][identity]
        s.require(content.get_blob(review["digest"]) == review["record"], "Admission review changed", "digest_mismatch")


def build_begin(controller, state, spec, target, content):
    import attack
    s.closed(spec, "strategy entry pass trigger_features step_cites")
    node, account = account_for_work(state, target, "moves")
    s.require(not state["control"]["pending_moves"], "Journal the previous entry before another begins", "recovery_required")
    audit_work(controller, state, node, content)
    workspace = safe_path(controller.root, node["attack_slug"])
    s.require(not (workspace / "units/FINISHED.json").exists(), "Attack is locally finished", "terminal_attack")
    studied = {item["method"] for item in node["admission"]["studies"]["strategies"]}
    s.require(spec["strategy"] in studied, "The selected strategy needs reviewed admission and its study", "admission_required")
    problem = attack.read_json(workspace / "problem.json")
    s.require(problem["claim"] == node["claim"]["statement"], "Problem claim differs from its admission", "claim_mismatch")
    moves = attack.read_journal(workspace)
    raw = (workspace / "journal.jsonl").read_bytes()
    if moves:
        receipt = state["service"]["journal_receipts"].get("{}:{}".format(target, len(moves)))
        s.require(receipt is not None and content.get_artifact(receipt["journal_prefix_digest"]) == raw,
                  "Existing journal needs exact controlled receipts or explicit adoption", "recovery_required")
    budget = attack.compute_budget(moves, attack.read_failure_window_start(workspace))
    walk = spec["strategy"] if not moves else moves[-1]["walk"]
    if moves and moves[-1]["strategy"] != spec["strategy"]:
        walk += "+" + spec["strategy"]
    planned = dict(spec, move=len(moves) + 1, walk=walk)
    strategies = controller.strategies_dir or Path(attack.__file__).resolve().parent.parent / "strategies"
    defects = (list(attack.find_problem_defects(problem))
        or attack.find_study_defects(workspace, "problem", "the problem-level study", "begin")
        or attack.find_study_defects(workspace, spec["strategy"], "the strategy study", "begin")
        or list(attack.find_move_flow_defects(planned, moves, workspace, attack.load_strategies(strategies), problem))
        or list(attack.find_budget_defects(planned, budget)))
    if defects:
        raise attack.ValidationError(defects)
    digest = attack.compute_problem_digest(problem)
    baseline = moves[-1]["problem_digest"] if moves else attack.read_json(workspace / "openings.json")["problem_digest"]
    defects = list(attack.find_problem_change_defects(dict(planned, problem_changed=digest != baseline), moves, budget, workspace, problem))
    if defects:
        raise attack.ValidationError(defects)
    return dict(planned, id="move-{}-{}".format(target, len(moves) + 1), node_id=target,
                account_id=account["id"], problem_digest=pin_problem(content, problem),
                journal_prefix_digest=content.put_artifact(raw))


def internal_operation(controller, command, request_id, build):
    """Record internal facts under the same validated service transaction boundary."""
    from .model import apply_event, replay
    identity = {"command": command, "target": None, "spec_digest": s.digest({"request_id": request_id})}
    revision = controller.status()["revision"]
    def builder(document, content):
        operations = build(replay(document), content)
        return dict(identity, operations=operations, effects=[])
    return controller.store.append_operation(identity, revision, request_id, builder,
        lambda document, event: apply_event(replay(document), event))


def before_legacy(args):
    from .service import Controller
    if not hasattr(args, "slug"):
        return None
    controller = Controller(args.attack_root, args.strategies)
    node = controller.guard_legacy(args.command, args.slug, vars(args))
    if args.command != "journal" or args.journal_command != "add":
        context = {"controller": controller, "node": node, "command": args.command} if node else None
        if node and args.command in {"plan", "rank", "fail", "stall", "check-unit", "finish"}:
            context["native_intent"], context["native_ownership"] = begin_native_intent(controller, node, args)
        return context
    return begin_journal_intent(controller, node, args)


def begin_journal_intent(controller, node, args):
    from .journal_io import acquire_journal_ownership
    ownership = acquire_journal_ownership(controller, node["id"])
    context = {"controller": controller, "node": node, "command": "journal", "journal_ownership": ownership}
    try:
        record_journal_intent(context, args)
        args._journal_context = context
        return context
    except BaseException:
        ownership.close()
        raise


def record_journal_intent(context, args):
    import attack
    controller, node = context["controller"], context["node"]
    workspace = safe_path(controller.root, args.slug)
    move = attack.parse_move_json(args.json)
    line = attack.validated_journal_line(args)
    request = "journal-intent-" + uuid.uuid4().hex
    def build(state, content):
        pending = state["control"]["pending_moves"]
        s.require(len(pending) == 1, "No unique reserved move", "reservation_required")
        reservation = state["service"]["moves"][pending[0]]
        s.require(reservation["node_id"] == node["id"], "Reserved move belongs to another journal", "reservation_mismatch")
        context["reservation_id"] = reservation["id"]
        for field in ["move", "pass", "strategy", "entry", "walk", "trigger_features", "step_cites"]:
            s.require(move[field] == reservation[field], "Journal differs from reserved entry: " + field, "reservation_mismatch")
        problem = attack.read_json(workspace / "problem.json")
        validate_problem(problem, node["claim"]["statement"])
        s.require(attack.compute_problem_digest(problem) == line["problem_digest"],
                  "Problem changed while preparing the journal", "digest_mismatch")
        problem_digest = pin_problem(content, problem)
        raw = (workspace / "journal.jsonl").read_bytes()
        s.require(content.put_artifact(raw) == reservation["journal_prefix_digest"], "Journal changed during reserved move", "digest_mismatch")
        after = raw + (json.dumps(line) + "\n").encode("utf-8")
        payload = {"reservation_id": reservation["id"],
            "before_digest": reservation["journal_prefix_digest"], "after_digest": content.put_artifact(after),
            "problem_digest": problem_digest}
        source = getattr(args, "problem_before", None)
        before = None
        if source is not None or problem_digest != reservation["problem_digest"]:
            before = load_problem_before(controller, node, reservation, content, source)
        if problem_digest != reservation["problem_digest"]:
            payload["problem_transition"] = {"before": before, "after": problem, "line": line}
        return [{"kind": "journal_intended", "payload": payload}]
    internal_operation(controller, "legacy-journal", request, build)


def reconcile_journals(controller, state, content, emit):
    for reservation_id, intent in state["service"]["legacy_intents"].items():
        move = state["service"]["moves"][reservation_id]
        node = state["nodes"][move["node_id"]]
        before, expected = audit_journal_append(controller, node, move, intent, content)
        path = safe_path(controller.root, node["attack_slug"] + "/journal.jsonl")
        raw = path.read_bytes()
        s.require(raw == expected or raw == before,
                  "Journal conflicts with its durable intent", "recovery_conflict")
        if raw == expected:
            emit("journal_acknowledged", {"node_id": node["id"], "move": move["move"],
                "reservation_id": reservation_id, "journal_prefix_digest": intent["after_digest"],
                "problem_digest": intent["problem_digest"]})


def after_legacy(context, outcome, diagnostics=None):
    if context is None:
        return
    controller = context["controller"]
    if outcome != 0:
        identity = context.get("native_intent")
        if identity is None:
            return
        state = controller.status()
        intent = state["service"]["native_intents"][identity]
        current = native_snapshot(controller, state["nodes"][intent["node_id"]], controller.store)
        if current != controller.store.get_blob(intent["pre_digest"]):
            if diagnostics is not None:
                record_native_success(controller, identity, "failed", diagnostics)
                internal_operation(controller, "reconcile", "native-failed-" + identity,
                    lambda state, content: [{"kind": "native_acknowledged", "payload": {
                        "id": identity, "outcome": "failed", "post_digest": content.put_blob(current),
                        "diagnostics": diagnostics}}])
            return
        internal_operation(controller, "reconcile", "native-unchanged-" + identity,
            lambda state, content: [{"kind": "native_acknowledged", "payload": {
                "id": identity, "outcome": "unchanged", "post_digest": intent["pre_digest"]}}])
        return
    if context.get("native_intent") is not None:
        record_native_success(controller, context["native_intent"])
    def build(state, content):
        operations = []
        reconcile_native_intents(controller, state, content, lambda kind, payload: operations.append({"kind": kind, "payload": payload}), context)
        if context["command"] == "journal":
            reconcile_journals(controller, state, content, lambda kind, payload: operations.append({"kind": kind, "payload": payload}))
        import copy
        from .model import EVENT_HANDLERS
        state = copy.deepcopy(state)
        for operation in operations:
            EVENT_HANDLERS[operation["kind"]](state, operation["payload"])
        for node in state["nodes"].values():
            if node["proposal_id"] is not None:
                operations.append({"kind": "node_facts_recorded", "payload": {"facts": observe_node(controller, state, node)}})
        return operations
    internal_operation(controller, "reconcile", "journal-ack-" + uuid.uuid4().hex, build)


def native_snapshot(controller, node, content):
    """Pin native inputs and outputs; caches and controller-generated views are excluded."""
    workspace = safe_path(controller.root, node["attack_slug"])
    files = []
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if any(part in {".lake", ".git", "__pycache__"} for part in relative.parts) or relative.as_posix() == "LINEAGE.md":
            continue
        s.require(not path.is_symlink(), "Native operation inputs cannot contain symlinks", "unsafe_path")
        if path.is_file():
            files.append({"path": str(relative), "digest": content.put_artifact(path.read_bytes())})
    return {"files": files}


def begin_native_intent(controller, node, args):
    from .execution import start_identity
    identity = "native-" + uuid.uuid4().hex
    directory = safe_path(controller.store.root, "native")
    directory.mkdir(exist_ok=True)
    ownership = (directory / (identity + ".lock")).open("x+b")
    arguments = {key: value for key, value in vars(args).items() if key != "run"}
    arguments = {key: str(value) if isinstance(value, Path) else value for key, value in arguments.items()}
    outputs = {"plan": ["openings.json"], "rank": [], "fail": ["preconditions.json", "openings.json"],
               "stall": ["units/INVENTORY.md"], "finish": ["units/FINISHED.json"],
               "check-unit": ["units/{}/check-unit.json".format(getattr(args, "unit", ""))]}[args.command]
    def build(state, content):
        current = reference(state["nodes"], node["id"], "native producer")
        if args.command in {"plan", "rank"}:
            audit_work(controller, state, current, content)
        return [{"kind": "native_intended", "payload": {"id": identity, "node_id": current["id"],
            "command": args.command, "args_digest": content.put_blob(arguments),
            "pre_digest": content.put_blob(native_snapshot(controller, current, content)), "output_paths": outputs,
            "ownership_digest": content.put_blob(owner)}}]
    try:
        fcntl.flock(ownership.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(ownership.fileno())
        owner = {"id": identity, "pid": os.getpid(), "start_identity": start_identity(),
                 "device": info.st_dev, "inode": info.st_ino}
        ownership.flush()
        os.fsync(ownership.fileno())
        internal_operation(controller, "legacy-native", identity, build)
        return identity, ownership
    except BaseException:
        ownership.close()
        raise


def release_native_ownership(context):
    """Release only after the synchronous invocation and its acknowledgement end."""
    if context is not None:
        for key in ["native_ownership", "journal_ownership"]:
            if context.get(key) is not None:
                context[key].close()


def record_native_success(controller, identity, outcome="succeeded", diagnostics=None):
    from .execution import atomic_record
    state = controller.status()
    intent = state["service"]["native_intents"][identity]
    node = state["nodes"][intent["node_id"]]
    before = controller.store.get_blob(intent["pre_digest"])
    after = native_snapshot(controller, node, controller.store)
    check_native_effect(intent, before, after)
    record = {"id": identity, "outcome": outcome, "post_digest": controller.store.put_blob(after)}
    if outcome == "failed":
        record["diagnostics"] = diagnostics
    directory = safe_path(controller.store.root, "native")
    directory.mkdir(exist_ok=True)
    atomic_record(directory / (identity + ".json"), record)


def check_native_effect(intent, before, after):
    old = {item["path"]: item["digest"] for item in before["files"]}
    new = {item["path"]: item["digest"] for item in after["files"]}
    changed = {path for path in set(old) | set(new) if old.get(path) != new.get(path)}
    s.require(changed <= set(intent["output_paths"]),
              "Native intent " + intent["id"] + " (" + intent["command"] + ") changed unpermitted inputs: "
              + ", ".join(sorted(changed - set(intent["output_paths"]))), "recovery_conflict")


def reconcile_native_intents(controller, state, content, emit, owned_context=None):
    from .execution import read_record
    for identity, intent in state["service"]["native_intents"].items():
        owner = content.get_blob(intent["ownership_digest"])
        lock = safe_path(controller.store.root, "native/" + identity + ".lock")
        s.require(lock.is_file() and not lock.is_symlink(), "Native ownership evidence is missing", "recovery_required")
        current_owner = owned_context is not None and owned_context.get("native_intent") == identity
        ownership = owned_context["native_ownership"] if current_owner else lock.open("rb")
        try:
            info = os.fstat(ownership.fileno())
            path_info = lock.stat()
            s.require(owner["id"] == identity and (owner["device"], owner["inode"]) == (info.st_dev, info.st_ino)
                      == (path_info.st_dev, path_info.st_ino), "Native ownership evidence changed", "recovery_required")
            if not current_owner:
                try:
                    fcntl.flock(ownership.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    s.require(False, "Native invocation still owns intent " + identity, "recovery_required")
            node = state["nodes"][intent["node_id"]]
            before = content.get_blob(intent["pre_digest"])
            actual = native_snapshot(controller, node, content)
            marker = safe_path(controller.store.root, "native/" + identity + ".json")
            if marker.exists():
                receipt = read_record(marker)
                s.require(receipt["id"] == identity and actual == content.get_blob(receipt["post_digest"]),
                          "Native output changed after its success receipt", "recovery_conflict")
                check_native_effect(intent, before, actual)
            else:
                s.require(not current_owner, "Live native invocation has no durable outcome", "recovery_required")
                if actual != before:
                    old = {item["path"]: item["digest"] for item in before["files"]}
                    new = {item["path"]: item["digest"] for item in actual["files"]}
                    paths = [str(controller.root / node["attack_slug"] / path)
                             for path in sorted(set(old) | set(new)) if old.get(path) != new.get(path)]
                    raise SearchError("recovery_conflict",
                        "Native effect is ambiguous for original command " + intent["command"]
                        + " (intent " + identity + "); conflicting paths: " + ", ".join(paths)
                        + ". Operator handoff required: preserve these files and the pinned intent, arguments, "
                        "snapshots and ownership records. There is no supported replay or automatic overwrite; "
                        "matching output bytes do not establish success.",
                        {"intent_id": identity, "original_command": intent["command"],
                         "original_args": content.get_blob(intent["args_digest"]), "conflicting_paths": paths})
                receipt = {"id": identity, "outcome": "unchanged", "post_digest": intent["pre_digest"]}
            emit("native_acknowledged", receipt)
        finally:
            if not current_owner:
                ownership.close()


def observe_node(controller, state, node, content=None):
    """Derive local stage observations without treating local finish as proof."""
    import attack
    workspace = safe_path(controller.root, node["attack_slug"])
    moves = attack.read_journal(workspace)
    previous = state["control"]["node_facts"].get(node["id"], {})
    status = node["status"]
    cashout = None
    if (workspace / "units/FINISHED.json").exists():
        status = "finished"
    elif attack.find_cash_out_rule(workspace, moves) is not None:
        if not (workspace / "units/INVENTORY.md").exists():
            cashout = "inventory"
        else:
            cashout = "finish"
            for number in attack.list_unit_numbers(workspace):
                directory = workspace / "units" / str(number)
                unit = attack.describe_unit(directory, number)
                if unit["state"] != "complete":
                    cashout = {"unchecked": "unit_checks", "undrafted": "draft", "unevaluated": "evaluation"}[unit["state"]]
                    break
    failed = []
    if (workspace / "preconditions.json").exists():
        preconditions = attack.read_json(workspace / "preconditions.json")
        studied = {item["method"] for item in node["admission"]["studies"]["strategies"]}
        failed = sorted(name for name, value in preconditions.items() if name in studied and value.get("note") == attack.FAIL_NOTE)
    stagnant = 0
    for move in reversed(moves):
        if not move["failure_signal_fired"]:
            break
        stagnant += 1
    result_action = None if status == "finished" else observe_result(controller, state, node, moves, content or controller.store)
    return {"node_id": node["id"], "status": status, "result_action": result_action, "cashout_action": cashout,
        "waiting_on": previous.get("waiting_on", []), "failed_strategies": failed, "stagnation_moves": stagnant,
        "external_block": previous.get("external_block"), "dependency_route_ids": previous.get("dependency_route_ids", []),
        "dependency_assumption_ids": previous.get("dependency_assumption_ids", [])}


def observe_result(controller, state, node, moves, content):
    """Derive a result task only from this exact controlled producer history."""
    from .evidence import manifest_closure
    if not moves or node["status"] in {"finished", "retreated"}:
        return None
    move = moves[-1]
    receipt = state["service"]["journal_receipts"].get("{}:{}".format(node["id"], move["move"]))
    if receipt is None:
        return None
    raw = safe_path(controller.root, node["attack_slug"] + "/journal.jsonl").read_bytes()
    s.require(content.get_artifact(receipt["journal_prefix_digest"]) == raw, "Observed journal differs from its controlled receipt", "recovery_conflict")
    runs = [run for run in state["runs"].values() if run["reservation_id"] == receipt["reservation_id"] and run["kind"] != "command"]
    run = sorted(runs, key=lambda item: item["id"])[-1] if runs else None
    matching = []
    for checkpoint in state["checkpoints"].values():
        origin = checkpoint["origin"]
        if (origin != {"kind": "journal_move", "node_id": node["id"], "move": move["move"], "journal_prefix_digest": receipt["journal_prefix_digest"]}
                or checkpoint["claim"] != node["claim"] or checkpoint["kind"] == "hypothesis"):
            continue
        if run is not None:
            manifests = manifest_closure(checkpoint["evidence_digests"], content).values()
            frozen = content.get_blob(run["input_digest"])
            if not any(manifest.get("verification") is not None
                       and manifest["verification"]["run_id"] == run["id"]
                       and manifest["verification"]["result_digest"] == run["result_digest"]
                       and all(manifest.get(key) == frozen[key] for key in ["claim_digest", "artifacts", "external_dependencies"])
                       for manifest in manifests):
                continue
        matching.append(checkpoint["id"])
    if any(value["status"] == "accepted" and value["checkpoint_id"] in matching for value in state["acceptances"].values()):
        return None
    if matching:
        return "acceptance"
    if run is not None and run["status"] == "terminal" and run.get("legacy_result") is not None:
        verdict = json.loads(content.get_artifact(run["legacy_result"]["digest"]))
        return "snapshot" if run["termination"] == "exit" and verdict["status"] == "pass" else "verification"
    if move["closes"]:
        return "verification" if move["steps"] else "snapshot"
    return None


def recover_journal_effects(controller):
    """Complete only already recorded append intents, then acknowledge once."""
    from .journal_io import apply_journal_intent, journal_ownership
    state = controller.status()
    for identity in state["service"]["legacy_intents"]:
        move = state["service"]["moves"][identity]
        node = state["nodes"][move["node_id"]]
        with journal_ownership(controller, node["id"]) as ownership:
            current = controller.status()
            if identity not in current["service"]["legacy_intents"]:
                continue
            apply_journal_intent(controller, current, identity, controller.store)
            after_legacy({"controller": controller, "node": node, "command": "journal",
                          "journal_ownership": ownership}, 0)
