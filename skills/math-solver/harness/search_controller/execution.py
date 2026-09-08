"""Bounded subprocess workloads with durable launch and recovery records."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from search_controller import schema as s
from search_controller.errors import SearchError
from search_controller.evidence import freeze_execution_inputs, installed_lean, capture_toolchain, audit_toolchain, file_digest
from search_controller.execution_state import account_for_work
from search_controller.storage import safe_path, canonical_bytes, _strict_json


OUTPUT_LIMIT = 1024 * 1024
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


def atomic_record(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(canonical_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_record(path):
    return _strict_json(path.read_bytes(), "recovery_conflict")


def declared_environment(spec, threads):
    s.require(isinstance(spec["environment"], dict), "Environment must be a declared record")
    environment = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"}
    for key, value in spec["environment"].items():
        s.require(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is not None, "Invalid declared environment key")
        s.require(not re.search(r"SECRET|TOKEN|PASSWORD|CREDENTIAL|API_KEY|PRIVATE_KEY", key), "Secret environment inputs are prohibited")
        s.require(isinstance(value, str) and "\x00" not in value, "Environment values must be NUL-free strings")
        s.require(key not in THREAD_VARIABLES and key not in {"PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}, "Reserved execution environment input")
        environment[key] = value
    environment.update({key: str(threads) for key in THREAD_VARIABLES})
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def build_run(controller, state, spec, target, content):
    from .integration import audit_work
    common = "kind step_dir artifacts external_dependencies environment dependency_enumeration timeout_seconds expected_outputs"
    s.choice(spec.get("kind"), {"command", "certificate", "lean"})
    s.closed(spec, common + (" argv" if spec["kind"] == "command" else
                            " input_review input_modes requested_declaration requested_type toolchain_inventory_digest inspection_source_digest" if spec["kind"] == "lean" else " input_review input_modes"))
    units = 2 if spec["kind"] == "lean" else 1
    node, account = account_for_work(state, target, "runs", units)
    from .computation import effective_contract, validate_run
    from .computation_io import audit_computation
    computation = effective_contract(state, node)
    computation_digest = s.digest(computation) if computation is not None else None
    validate_run(state, node, spec["kind"], computation_digest, spec["timeout_seconds"])
    if computation is not None:
        audit_computation(controller.root, state, state["proposals"][node["proposal_id"]]["record"], computation, content)
    if spec["kind"] == "command":
        s.require(node["admission"]["task"]["kind"] in {"finite_decision", "finite_proof", "counterexample_search"}
                  and node["admission"]["contribution"]["necessity"] is not None,
                  "Generic computation requires a reviewed bounded task and its necessity/outcome mapping", "admission_required")
    audit_work(controller, state, node, content)
    if spec["kind"] != "command":
        from .proof import validate_review
        subject = verification_entry_subject(node, spec)
        validate_review(spec["input_review"], s.digest(subject), node["claim_digest"])
        author = state["proposals"][node["proposal_id"]]["record"]["author"]
        s.require(spec["input_review"]["reviewer"]["actor_id"] != author["actor_id"],
                  "Verification input review must be independent of the proposal author", "input_review_required")
        content.put_blob(subject)
        content.put_blob(spec["input_review"])
        s.require("PATH" in spec["environment"], "Native input review must pin its executable search path", "input_review_required")
    pending = state["control"]["pending_moves"]
    s.require(len(pending) == 1, "Begin the entry before launching a workload", "reservation_required")
    move = state["service"]["moves"][pending[0]]
    s.require(move["node_id"] == target, "The reserved entry belongs to another node", "reservation_required")
    from .strategy_refresh import assessment_status, require_research
    if spec["kind"] == "command":
        s.require(move.get("purpose", "research") == "research", "A verification move cannot launch a producer", "verification_only")
        require_research(state, target, move["strategy"], move["problem_digest"], move.get("planning_digest"))
    workspace = safe_path(controller.root, node["attack_slug"])
    import attack
    s.require(not (workspace / "units/FINISHED.json").exists(), "Attack is locally finished", "terminal_attack")
    s.require(attack.compute_problem_digest(attack.read_json(workspace / "problem.json")) == move["problem_digest"], "Problem changed during the reserved entry", "digest_mismatch")
    if spec["kind"] == "command":
        from .strategy_refresh_io import audit_research_plans
        audit_research_plans(controller, state, move)
    s.integer(spec["timeout_seconds"], 1, node["admission"]["limits"]["timeout_seconds"])
    s.strings(spec["expected_outputs"])
    for path in spec["expected_outputs"]:
        safe_path(controller.root, path)
    run_id = "run-{:06d}".format(state["next_ids"]["run"])
    directory = safe_path(controller.store.root, "runs/" + run_id)
    step = node["attack_slug"] + "/deterministic/" + spec["step_dir"]
    cwd = safe_path(directory, "build/" + step)
    environment = declared_environment(spec, node["admission"]["limits"]["workers"])
    environment["EXACTORY_OUTPUT_DIR"] = str(directory / "output")
    declaration = theorem_type = toolchain = inventory_digest = inspection_source = None
    if spec["kind"] == "command":
        s.strings(spec["argv"], nonempty=True)
        commands = [list(spec["argv"])]
    elif spec["kind"] == "certificate":
        checker = safe_path(controller.root, step + "/check.sh")
        s.require(checker.is_file() and os.access(str(checker), os.X_OK), "check.sh must exist and be executable", "missing_evidence")
        commands = [["/bin/sh", "check.sh"]]
    else:
        declaration = spec["requested_declaration"]
        s.require(isinstance(declaration, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'.]*", declaration), "Invalid Lean declaration")
        s.text(spec["requested_type"])
        s.require("\n" not in spec["requested_type"] and "\r" not in spec["requested_type"], "Lean requested type must be a single expression")
        project = safe_path(controller.root, step)
        description = attack.read_json(project / "step.json")
        defects = list(attack.find_lean_project_defects(project, description))
        if defects:
            raise attack.ValidationError(defects)
        s.require(description["theorem"] == declaration, "Requested declaration differs from step.json", "claim_mismatch")
        inspection_source = content.put_artifact(lean_inspection_source(description, spec["requested_type"]).encode())
        s.require(inspection_source == spec["inspection_source_digest"], "Generated inspection source differs from input review", "digest_mismatch")
        theorem_type = content.put_artifact(spec["requested_type"].encode("utf-8"))
        toolchain = content.put_artifact((project / "lean-toolchain").read_bytes())
        lake, installation = installed_lean(project, environment["PATH"])
        inventory_digest = spec["toolchain_inventory_digest"]
        inventory = content.get_blob(inventory_digest)
        s.require(inventory["root"] == str(installation), "Selected toolchain installation changed", "digest_mismatch")
        audit_toolchain(inventory)
        commands = [[str(lake), "build"], [str(lake), "env", "lean", "axioms-check.lean"]]
    bindings = []
    for command in commands:
        if spec["kind"] == "command":
            for index, argument in enumerate(command):
                candidate = Path(argument)
                if index == 0 and not candidate.is_absolute() and "/" in argument:
                    candidate = safe_path(controller.root, step) / candidate
                if candidate.is_absolute() and candidate.resolve().is_relative_to(controller.root.resolve()):
                    relative = str(candidate.resolve().relative_to(controller.root.resolve()))
                    s.require(relative in {item["path"] for item in spec["artifacts"]},
                              "Local argv input must be a declared frozen artifact: " + relative, "missing_evidence")
                    safe_path(controller.root, relative)
                    command[index] = str(safe_path(directory, "build/" + relative))
        if Path(command[0]).is_relative_to(directory / "build"):
            source = safe_path(controller.root, str(Path(command[0]).relative_to(directory / "build")))
            s.require(os.access(str(source), os.X_OK), "Local executable is not executable", "missing_toolchain")
            continue
        executable = shutil.which(command[0], path=environment["PATH"])
        s.require(executable is not None, "Executable is unavailable; installation is not automatic", "missing_toolchain")
        resolved = Path(executable).resolve()
        if spec["kind"] == "command" and resolved.is_relative_to(controller.root.resolve()):
            relative = str(resolved.relative_to(controller.root.resolve()))
            s.require(relative in {item["path"] for item in spec["artifacts"]},
                      "Local executable must be a declared frozen artifact: " + relative, "missing_evidence")
            command[0] = str(safe_path(directory, "build/" + relative))
            continue
        command[0] = str(Path(executable).absolute())
        binding = {"path": str(resolved), "digest": file_digest(resolved)}
        if binding not in bindings:
            bindings.append(binding)
    if spec["kind"] != "command":
        s.require(all(binding in spec["external_dependencies"] for binding in bindings),
                  "Native input review must pin the selected verifier executables", "input_review_required")
    frozen = freeze_execution_inputs(controller.root, node, spec, content)
    input_modes = capture_input_modes(controller.root, content.get_blob(frozen)["artifacts"])
    if spec["kind"] != "command":
        s.require(input_modes == spec["input_modes"], "Input permissions differ from the reviewed specification", "digest_mismatch")
    publication_prestate = []
    if spec["kind"] != "command":
        for name in ["result.json"] + (["inspection.log"] if spec["kind"] == "lean" else []):
            relative = step + "/" + name
            path = safe_path(controller.root, relative)
            publication_prestate.append({"path": relative, "digest": content.put_artifact(path.read_bytes()) if path.exists() else None})
    content.put_blob(spec)
    return {"id": run_id, "node_id": target, "account_id": account["id"], "reservation_id": move["id"],
        "kind": spec["kind"], "input_digest": frozen, "spec_digest": s.digest(spec), "task": node["admission"]["task"],
        "computation_digest": computation_digest,
        "strategy_context_digest": assessment_status(state)["context_digest"],
        "cwd": str(cwd), "snapshot_root": str(directory / "input"), "output_root": str(directory / "output"),
        "commands": commands, "timeout_seconds": spec["timeout_seconds"], "environment": environment,
        "threads": node["admission"]["limits"]["workers"], "expected_outputs": spec["expected_outputs"],
        "dependency_enumeration": spec["dependency_enumeration"], "executable_bindings": bindings,
        "reserved_units": units, "token": uuid.uuid4().hex, "requested_declaration": declaration,
        "requested_type_digest": theorem_type, "toolchain_digest": toolchain, "toolchain_inventory_digest": inventory_digest,
        "inspection_source_digest": inspection_source, "publication_prestate": publication_prestate, "input_modes": input_modes}


def capture_input_modes(root, artifacts):
    modes = []
    for item in artifacts:
        mode = stat.S_IMODE(safe_path(root, item["path"]).stat().st_mode)
        s.require(mode <= 0o777, "Special executable permission bits are unsupported", "unsafe_path")
        modes.append({"path": item["path"], "mode": mode})
    return modes


def lean_inspection_source(description, requested_type):
    module = ".".join(Path(description.get("file", "Main.lean")).with_suffix("").parts)
    s.require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module), "Invalid Lean module path")
    declaration = description["theorem"]
    return ('import {}\n#eval IO.println "EXACTORY_TYPE_BEGIN"\n#check @{}\n'
            '#eval IO.println "EXACTORY_TYPE_END"\n'
            'theorem exactory_correspondence : {} := @{}\n'
            '#print axioms {}\n#print axioms exactory_correspondence\n').format(
                module, declaration, requested_type, declaration, declaration)


def materialize(controller, run):
    directory = Path(run["snapshot_root"]).parent
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "output").mkdir()
    inputs = controller.store.get_blob(run["input_digest"])
    modes = {item["path"]: item["mode"] for item in run["input_modes"]}
    for item in inputs["artifacts"]:
        raw = controller.store.get_artifact(item["digest"])
        for prefix in ["input", "build"]:
            target = safe_path(directory, prefix + "/" + item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(modes[item["path"]])
    if run["kind"] == "lean":
        project = Path(run["cwd"])
        (project / "axioms-check.lean").write_bytes(controller.store.get_artifact(run["inspection_source_digest"]))
    inventory = None if run["toolchain_inventory_digest"] is None else controller.store.get_blob(run["toolchain_inventory_digest"])
    atomic_record(directory / "config.json", {"run": run, "inputs": inputs, "toolchain_inventory": inventory})
    return directory


def verification_entry_subject(node, spec):
    return {"node_id": node["id"], "task": node["admission"]["task"],
            "spec_without_input_review": {key: value for key, value in spec.items() if key != "input_review"}}


def start_identity():
    proc = Path("/proc/self/stat")
    if proc.exists():
        return "proc:" + proc.read_text().rsplit(")", 1)[1].split()[19]
    result = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=2)
    s.require(result.returncode == 0 and result.stdout.strip(), "Cannot establish launcher process identity", "process_identity_unavailable")
    return "ps:" + result.stdout.strip()


def process_observations(state):
    """Observe launcher ownership without inferring liveness from a stored PID."""
    observations = {}
    for identity, run in state["runs"].items():
        if run["status"] == "terminal":
            observations[identity] = "terminal"
            continue
        directory = Path(run["snapshot_root"]).parent
        observed = "indeterminate" if run["status"] == "indeterminate" else "pending_reconciliation"
        lock = directory / "owner.lock"
        if lock.exists() and not (directory / "terminal.json").exists():
            with lock.open("rb") as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    observed = "live"
        observations[identity] = observed
    return observations


def launch(controller, run):
    from .integration import internal_operation
    directory = materialize(controller, run)
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--launcher", str(directory)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    released = False
    try:
        deadline = time.monotonic() + 5
        while not (directory / "ready.json").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        s.require((directory / "ready.json").exists(), "Launcher did not establish its identity", "recovery_required")
        identity = read_record(directory / "ready.json")
        s.require(identity["pid"] == process.pid and identity["token"] == run["token"], "Unexpected launcher identity", "recovery_conflict")
        def build_launch(state, content):
            if run["kind"] == "command":
                from .strategy_refresh_io import audit_research_plans
                audit_research_plans(controller, state, state["service"]["moves"][run["reservation_id"]])
            return [{"kind": "run_launched", "payload": {"run_id": run["id"], "token": run["token"], "identity": identity}}]
        internal_operation(controller, "execution-launch", "launch-" + run["id"], build_launch)
        # Recheck after the launch record commits, then retain ownership until
        # token delivery so another controller writer cannot change the context.
        with controller.store._writer_lock():
            if run["kind"] == "command":
                from .model import replay
                from .execution_state import require_producer_context
                from .strategy_refresh_io import audit_research_plans
                state = replay(controller.store.read())
                move = state["service"]["moves"][run["reservation_id"]]
                require_producer_context(state, run, move)
                audit_research_plans(controller, state, move)
            process.stdin.write((run["token"] + "\n").encode())
            process.stdin.flush()
            released = True
        process.stdin.close()
        process.wait(timeout=run["timeout_seconds"] + 10)
    except SearchError:
        if not released:
            # The owned launcher has not received authority to execute a producer.
            # Closing its input records a definite unstarted result, which can be
            # reconciled without guessing whether a workload ran or charging it.
            process.stdin.close()
            process.wait(timeout=5)
            reconcile_runs(controller)
        raise
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
    reconcile_runs(controller)


def bounded_command(argv, cwd, environment, deadline, directory, number):
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selection = selectors.DefaultSelector()
    selection.register(process.stdout, selectors.EVENT_READ, "stdout")
    selection.register(process.stderr, selectors.EVENT_READ, "stderr")
    reason = "exit"
    while selection.get_map():
        if time.monotonic() >= deadline:
            reason = "timeout"
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            break
        for key, _ in selection.select(min(0.05, max(0, deadline - time.monotonic()))):
            raw = os.read(key.fd, 65536)
            if not raw:
                selection.unregister(key.fileobj)
                continue
            target = streams[key.data]
            remaining = OUTPUT_LIMIT - len(target)
            target.extend(raw[:remaining])
            if len(raw) > remaining:
                reason = "output_limit"
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
    selection.close()
    process.stdout.close()
    process.stderr.close()
    if process.poll() is None:
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            reason = "timeout"
            os.killpg(process.pid, signal.SIGKILL)
    code = process.wait()
    try:
        os.killpg(process.pid, 0)
        reason = "unresolved_descendants"
    except ProcessLookupError:
        pass
    for stream, raw in streams.items():
        (directory / (str(number) + "." + stream)).write_bytes(raw)
    return {"argv": argv, "exit_code": code,
            "stdout_file": str(number) + ".stdout", "stderr_file": str(number) + ".stderr",
            "stdout_digest": hashlib.sha256(streams["stdout"]).hexdigest(),
            "stderr_digest": hashlib.sha256(streams["stderr"]).hexdigest()}, reason


def launcher(directory):
    config = read_record(directory / "config.json")
    run = config["run"]
    with (directory / "owner.lock").open("a+b") as ownership:
        fcntl.flock(ownership.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = {"pid": os.getpid(), "process_group": os.getpgrp(), "start_identity": start_identity(), "token": run["token"]}
        atomic_record(directory / "ready.json", identity)
        commands, started, reason = [], 0, "never_started"
        if sys.stdin.readline().strip() == run["token"]:
            deadline = time.monotonic() + run["timeout_seconds"]
            for number, argv in enumerate(run["commands"]):
                reason = execution_boundary(config, directory)
                if reason != "exit":
                    break
                started += 1
                atomic_record(directory / "started.json", {"token": run["token"], "started_units": started})
                try:
                    command, reason = bounded_command(argv, run["cwd"], run["environment"], deadline, directory, number)
                except OSError:
                    reason = "launch_failed"
                    break
                commands.append(command)
                if command["exit_code"] != 0 or reason != "exit":
                    break
            boundary = execution_boundary(config, directory)
            if boundary != "exit":
                reason = boundary
        outputs = []
        for relative in run["expected_outputs"]:
            path = safe_path(directory / "output", relative)
            if path.is_file() and path.stat().st_size <= OUTPUT_LIMIT:
                outputs.append({"path": relative, "digest": hashlib.sha256(path.read_bytes()).hexdigest()})
            elif reason == "exit":
                reason = "missing_output"
        atomic_record(directory / "terminal.json", {"token": run["token"], "commands": commands,
            "started_units": started, "termination": reason, "outputs": outputs})


def execution_boundary(config, directory):
    run = config["run"]
    modes = {item["path"]: item["mode"] for item in run["input_modes"]}
    for item in config["inputs"]["artifacts"]:
        source = safe_path(directory, "build/" + item["path"])
        if not source.is_file() or file_digest(source) != item["digest"] or stat.S_IMODE(source.stat().st_mode) != modes[item["path"]]:
            return "input_changed"
    if run["inspection_source_digest"] is not None:
        source = Path(run["cwd"]) / "axioms-check.lean"
        if not source.is_file() or file_digest(source) != run["inspection_source_digest"]:
            return "input_changed"
    for item in config["inputs"]["external_dependencies"] + run["executable_bindings"]:
        source = Path(item["path"])
        if not source.is_file() or file_digest(source) != item["digest"]:
            return "dependency_changed"
    if config["toolchain_inventory"] is not None:
        try:
            audit_toolchain(config["toolchain_inventory"])
        except SearchError:
            return "dependency_changed"
    return "exit"


def reconcile_runs(controller):
    """Observe original run ownership; never spawn or signal during recovery."""
    from .integration import internal_operation
    state = controller.status()
    for run in state["runs"].values():
        if run["status"] == "terminal":
            continue
        directory = Path(run["snapshot_root"]).parent
        terminal = directory / "terminal.json"
        if not terminal.exists():
            lock = directory / "owner.lock"
            if lock.exists():
                with lock.open("rb") as stream:
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
            if run["status"] == "indeterminate":
                continue
            value = {"token": run["token"], "commands": [], "started_units": 0, "termination": "indeterminate", "outputs": []}
        else:
            value = read_record(terminal)
            s.require(value["token"] == run["token"], "Terminal token differs", "recovery_conflict")
        def build(current, content):
            commands = []
            for number, item in enumerate(value["commands"]):
                s.require(item["argv"] == run["commands"][number], "Captured command differs from reservation", "recovery_conflict")
                for stream in ["stdout", "stderr"]:
                    s.require(item[stream + "_file"] == str(number) + "." + stream, "Unexpected output storage path", "recovery_conflict")
                    raw = (directory / item[stream + "_file"]).read_bytes()
                    s.require(hashlib.sha256(raw).hexdigest() == item[stream + "_digest"], "Captured output changed before reconciliation", "digest_mismatch")
                commands.append({"argv": item["argv"], "exit_code": item["exit_code"],
                    "stdout_digest": content.put_artifact((directory / item["stdout_file"]).read_bytes()),
                    "stderr_digest": content.put_artifact((directory / item["stderr_file"]).read_bytes())})
            for item in value["outputs"]:
                s.require(item["path"] in run["expected_outputs"], "Unexpected result artifact", "recovery_conflict")
                s.require(content.put_artifact(safe_path(directory / "output", item["path"]).read_bytes()) == item["digest"],
                          "Result output changed before reconciliation", "digest_mismatch")
            result = {"schema_version": 1, "run_id": run["id"], "claim_digest": current["nodes"][run["node_id"]]["claim_digest"],
                "input_digest": run["input_digest"], "commands": commands}
            if run["kind"] == "command":
                result["kind"] = "command"
            else:
                result.update(declaration=run["requested_declaration"], theorem_type_digest=run["requested_type_digest"], toolchain_digest=run["toolchain_digest"])
            uncertain = value["termination"] in {"indeterminate", "unresolved_descendants"}
            charged = max(run["charged_units"], run["reserved_units"] if uncertain else value["started_units"])
            legacy = None
            legacy_inspection = None
            inspection = None
            if run["kind"] != "command":
                verdict = legacy_verdict(run, result, content, value["termination"])
                relative = str(Path(run["cwd"]).relative_to(directory / "build") / "result.json")
                legacy = {"path": relative, "digest": content.put_artifact((json.dumps(verdict, indent=2) + "\n").encode())}
                if run["kind"] == "lean" and len(commands) == 2:
                    legacy_inspection = {"path": str(Path(relative).with_name("inspection.log")),
                                         "digest": commands[1]["stdout_digest"]}
                    from .evidence import parse_lean_inspection
                    try:
                        captured = parse_lean_inspection(content.get_artifact(commands[1]["stdout_digest"]).decode("utf-8"), run["requested_declaration"])
                        inspection = {"printed_type_digest": content.put_artifact(captured.pop("printed_type").encode()),
                                      "source_digest": run["inspection_source_digest"], **captured}
                    except (SearchError, UnicodeError):
                        pass
            return [{"kind": "run_finished", "payload": {"run_id": run["id"], "token": run["token"],
                "status": "indeterminate" if uncertain else "terminal", "started_units": value["started_units"],
                "charged_units": charged, "result_digest": content.put_blob(result), "termination": value["termination"],
                "legacy_result": legacy, "legacy_inspection": legacy_inspection, "outputs": value["outputs"], "inspection": inspection}}]
        internal_operation(controller, "reconcile", "terminal-" + run["id"] + "-" + s.digest(value), build)
    publish_results(controller)


def publish_results(controller):
    """Replay the original immutable result publication without executing a job."""
    from .render import replace_text
    for run in controller.status()["runs"].values():
        publications = [run[key] for key in ["legacy_result", "legacy_inspection"] if run.get(key) is not None]
        if not publications:
            continue
        directory = Path(run["snapshot_root"]).parent
        marker = directory / "result-published.json"
        if marker.exists():
            s.require(read_record(marker) == publications, "Result publication acknowledgement differs", "recovery_conflict")
            continue
        for publication in publications:
            path = safe_path(controller.root, publication["path"])
            before = next(item["digest"] for item in run["publication_prestate"] if item["path"] == publication["path"])
            actual = controller.store.put_artifact(path.read_bytes()) if path.exists() else None
            s.require(actual in {before, publication["digest"]}, "Result publication conflicts with intervening edits: " + publication["path"], "recovery_conflict")
        for publication in publications:
            replace_text(safe_path(controller.root, publication["path"]), controller.store.get_artifact(publication["digest"]).decode())
        atomic_record(marker, publications)


def legacy_verdict(run, result, content, termination):
    import attack
    commands = result["commands"]
    last = commands[-1] if commands else None
    output = "" if last is None else (content.get_artifact(last["stdout_digest"]) + content.get_artifact(last["stderr_digest"])).decode("utf-8", errors="replace")
    if run["kind"] == "certificate":
        code = last["exit_code"] if last else -1
        return {"status": "pass" if termination == "exit" and code == 0 else "fail", "exit_status": code, "output_head": attack.head_lines(output)}
    declaration = run["requested_declaration"]
    if termination != "exit":
        return attack.build_lean_result(declaration, [], "fail", "execution error: " + termination, output)
    if not commands or commands[0]["exit_code"] != 0:
        return attack.build_lean_result(declaration, [], "fail", "lake build failed", output)
    if len(commands) != 2 or commands[1]["exit_code"] != 0:
        return attack.build_lean_result(declaration, [], "fail", "Lean inspection failed", output)
    from .evidence import parse_lean_inspection
    try:
        captured = parse_lean_inspection(output, declaration)
    except SearchError as error:
        return attack.build_lean_result(declaration, [], "fail", error.message, output)
    axioms = list(dict.fromkeys(captured["declaration_axioms"] + captured["correspondence_axioms"]))
    status, reason = attack.classify_axioms(declaration, axioms)
    return attack.build_lean_result(declaration, axioms, status, reason, output)


def native_spec(controller, node, args):
    """Derive a stable native verification input subject before review."""
    import attack
    step = attack.resolve_step_dir(args).resolve()
    description = attack.read_json(step / "step.json") if (step / "step.json").exists() or args.verify_command == "lean" else {}
    if args.verify_command == "lean":
        defects = list(attack.find_lean_project_defects(step, description))
        if defects:
            raise attack.ValidationError(defects)
        if "requested_type" not in description:
            raise attack.ValidationError(["step.json: missing requested_type"])
    else:
        checker = step / "check.sh"
        if not checker.exists():
            raise attack.ValidationError(["check.sh: missing"])
        if not os.access(str(checker), os.X_OK):
            raise attack.ValidationError(["check.sh: not executable"])
    artifacts = []
    for path in sorted(step.rglob("*")):
        relative = path.relative_to(step)
        if not path.is_file() or any(part in {".lake", ".git", "__pycache__"} for part in relative.parts) or relative.as_posix() in {"result.json", "inspection.log", "axioms-check.lean", "verification-review.json"}:
            continue
        role = {"check.sh": "checker", "certificate.txt": "certificate", "lean-toolchain": "toolchain"}.get(relative.as_posix(),
                "theorem" if path.suffix == ".lean" else "input")
        artifacts.append({"path": str(path.relative_to(controller.root)), "digest": hashlib.sha256(path.read_bytes()).hexdigest(), "role": role})
    environment = dict(description.get("environment", {}))
    environment.setdefault("PATH", os.environ.get("PATH", os.defpath))
    dependencies = list(description.get("external_dependencies", []))
    executable = shutil.which("/bin/sh", path=environment["PATH"])
    inventory_digest = None
    if args.verify_command == "lean":
        executable, installation = installed_lean(step, environment["PATH"])
        inventory_digest = controller.store.put_blob(capture_toolchain(installation))
    s.require(executable is not None, "Verifier executable is unavailable", "missing_toolchain")
    executable_path = Path(executable).resolve()
    binding = {"path": str(executable_path), "digest": file_digest(executable_path)}
    if binding not in dependencies:
        dependencies.append(binding)
    spec = {"kind": args.verify_command, "step_dir": args.step_dir, "artifacts": artifacts,
            "input_modes": capture_input_modes(controller.root, artifacts),
            "external_dependencies": dependencies, "environment": environment,
            "dependency_enumeration": description.get("dependency_enumeration", "Recursive local project sources plus explicitly declared external dependencies; proof policy review must assess completeness"),
            "timeout_seconds": node["admission"]["limits"]["timeout_seconds"], "expected_outputs": []}
    if args.verify_command == "lean":
        spec.update(requested_declaration=description["theorem"], requested_type=description["requested_type"], toolchain_inventory_digest=inventory_digest,
                    inspection_source_digest=controller.store.put_artifact(lean_inspection_source(description, description["requested_type"]).encode()))
    return spec


def native_verify(args):
    """Adapt the native verifier to the same public bounded execution contract."""
    import attack
    from .service import Controller
    controller = Controller(args.attack_root, args.strategies)
    node = controller.guard_legacy("verify", args.slug, vars(args))
    spec = native_spec(controller, node, args)
    step = attack.resolve_step_dir(args).resolve()
    review_path = step / "verification-review.json"
    s.require(review_path.is_file(), "Native execution requires an independent input-bound verification-review.json", "input_review_required")
    spec["input_review"] = read_record(review_path)
    state = controller.status()
    controller.command("run", spec, state["revision"], "native-verify-" + uuid.uuid4().hex, node["id"])
    result = attack.read_json(step / "result.json")
    detail = "check.sh exited %d" % result["exit_status"] if args.verify_command == "certificate" else result["reason"] or attack.describe_axioms(result)
    return attack.print_verdict(result, detail)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--launcher":
        launcher(Path(sys.argv[2]))
    else:
        raise SystemExit("This module is an internal launcher, not a public command.")
