"""Durable exploratory intents, reservations and once-only local outcomes."""

from contextlib import contextmanager
import fcntl
import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

from . import resources
from .artifacts import ArtifactStore, is_sealed_artifact
from .errors import ResearchError
from .operations import fields, immutable_record, is_finite_number, prepared_mutation, text
from .probe_worker import capture_probe_outcome, find_probe_stream_origins
from .probe_provenance import pin_probe_artifact
from .probe_supervision import find_probe_group_members, find_probe_process_identity, read_probe_clock
from .program_probes import build_program_run, format_utc_now, validate_program_run, validate_program_run_context
from .workspace import checked_parent, json_projection, read_file, strict_json, write_projection


def validate_probe_descriptor(artifacts, value, *, sealed=False):
    fields(value, ("path", "sha256", "size", "media_type"))
    if sealed and is_sealed_artifact(value):
        artifacts.verify_sealed(value)
    else:
        artifacts.read(value)


def validate_probe_descriptors(artifacts, values, *, sealed=False):
    if not isinstance(values, list):
        raise ResearchError("invalid_input", "Probe artifacts must be an array of descriptors")
    for value in values:
        validate_probe_descriptor(artifacts, value, sealed=sealed)


def validate_probe_outcome(artifacts, value):
    fields(value, ("id", "command", "outputs", "stdout", "stderr", "exit_status", "elapsed_seconds", "observation"),
           ("program_run",))
    for key in ("id", "command", "observation"):
        text(value[key], "Probe " + key)
    sealed = isinstance(value.get("program_run"), dict) and value["program_run"].get("outputs_unread") is True
    validate_probe_descriptors(artifacts, value["outputs"], sealed=sealed)
    for key in ("stdout", "stderr"):
        validate_probe_descriptor(artifacts, value[key], sealed=sealed)
    if not is_probe_exit_status(value["exit_status"]):
        raise ResearchError("invalid_input", "Probe exit_status must be an integer, timed_out, failed, output_bound or interrupted")
    if not is_finite_number(value["elapsed_seconds"]) or value["elapsed_seconds"] < 0:
        raise ResearchError("invalid_input", "Probe elapsed_seconds must be a finite nonnegative number")


def is_probe_exit_status(value):
    return type(value) is int or isinstance(value, str) and value in ("timed_out", "failed", "output_bound", "interrupted")


def record_probe_intent(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        fields(value, ("id", "question", "interpreter", "program", "inputs", "timeout_seconds"), ("program_run",))
        for key in ("id", "question", "interpreter"):
            text(value[key], "Probe " + key)
        if records.get("configuration", {}).get("research") is None:
            raise ResearchError("configuration_missing", "Initialize the study profile and constitution first")
        validate_probe_descriptor(artifacts, value["program"])
        validate_probe_descriptors(artifacts, value["inputs"])
        if not is_finite_number(value["timeout_seconds"]) or value["timeout_seconds"] <= 0:
            raise ResearchError("invalid_input", "Probe timeout_seconds must be a finite positive number")
        existing = records.get("probe_intent", {}).get(value["id"])
        if "program_run" in value:
            validate_program_run_context(records, value["program_run"], new=existing is None)
        if existing is not None:
            if {key: existing.get(key) for key in value} != value:
                raise ResearchError("record_conflict", "Probe intent ID already identifies different arguments")
            return [], existing
        # The immutable intent itself holds this allowance until its outcome exists.
        resources.charge(records, "experiment", {"wall_seconds": value["timeout_seconds"]})
        record = dict(value, revision=expected_revision + 1)
        return [immutable_record(records, "probe_intent", value["id"], record)], record

    return prepared_mutation(store, "research.probe_intent", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def record_probe(store, payload, *, expected_revision, request_id):
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        fields(value, ("id", "command", "outputs", "stdout", "stderr", "exit_status", "elapsed_seconds", "observation"),
               ("program_run",))
        for key in ("id", "command", "observation"):
            text(value[key], "Probe " + key)
        if value["id"] not in records.get("probe_intent", {}):
            raise ResearchError("probe_intent_missing", "Record the pinned, reserved probe intent before its outcome")
        validate_probe_outcome(artifacts, value)
        validate_program_run(records["probe_intent"][value["id"]], value)
        existing = records.get("probe", {}).get(value["id"])
        if existing is not None:
            if {key: existing[key] for key in value} != value:
                raise ResearchError("record_conflict", "Probe ID already has a different terminal outcome")
            return [], existing
        launch = records.get("probe_launch", {}).get(value["id"])
        if launch is not None and value != read_probe_outcome(store, launch):
            raise ResearchError("probe_pending", "Reconcile the original launch's terminal evidence before recording its outcome")
        record = dict(value, revision=expected_revision + 1)
        changes = [immutable_record(records, "probe", value["id"], record)]
        if launch:
            changes.extend(immutable_record(records, "probe_artifact", value["id"] + ":" + origin["sha256"], origin)
                           for origin in find_probe_stream_origins(store.root, launch))
        recovery = records.get("probe_recovery", {}).get(value["id"], {})
        charge = resources.charge(records, "experiment", {"wall_seconds": value["elapsed_seconds"]}, refuse=False,
                                  unknown=("wall_seconds",) if recovery.get("usage_incomplete") else ())
        if charge is not None:
            changes.append(charge)
        return changes, record

    return prepared_mutation(store, "research.probe", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def summarize_probes(records):
    probes = records.get("probe", {})
    return {"count": len(probes), "elapsed_seconds": sum(probe["elapsed_seconds"] for probe in probes.values())}


def find_probe_receipt(store, identifier):
    records = store.snapshot()["records"]
    if identifier not in records.get("probe", {}):
        return None
    for request_id in ["probe:" + identifier] + list(records.get("literature_operation", {})):
        previous = store.committed_request(request_id)
        if previous and previous["operation"] == "research.probe" and previous["payload"].get("id") == identifier:
            return record_probe(store, previous["payload"], expected_revision=store.revision, request_id=request_id)
    raise ResearchError("probe_evidence_mismatch", "A recorded probe has no original managed receipt")


def commit_probe_mutation(store, operation, payload, request_id):
    while True:
        try:
            return operation(store, payload, expected_revision=store.revision, request_id=request_id)
        except ResearchError as error:
            if error.code != "stale_revision":
                raise


def build_probe_directory(identifier):
    return "research/probes/" + identifier


@contextmanager
def hold_probe_lock(root, identifier, filename):
    with checked_parent(root, build_probe_directory(identifier) + "/" + filename, create=True) as (directory, name):
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def hold_probe_owner(root, identifier):
    with hold_probe_lock(root, identifier, "owner.lock") as descriptor:
        yield descriptor


def read_probe_file(store, relative):
    try:
        return strict_json(read_file(store.root, relative))
    except ResearchError as error:
        if error.code not in ("artifact_missing", "invalid_json"):
            raise
        return None


def read_probe_outcome(store, launch):
    document = read_probe_file(store, launch["directory"] + "/outcome.json")
    if not isinstance(document, dict):
        return None
    if document.get("config_sha256") != launch["config_artifact"]["sha256"]:
        return None
    outcome = document.get("outcome")
    if not isinstance(outcome, dict):
        return None
    try:
        validate_probe_outcome(store.build_artifact_store(), outcome)
    except ResearchError:
        return None
    if outcome["id"] != launch["id"] or outcome["command"] != launch["command"]:
        return None
    return outcome


def record_probe_launch(store, payload, *, expected_revision, request_id):
    def prepare(records, value):
        if value["id"] not in records.get("probe_intent", {}):
            raise ResearchError("probe_intent_missing", "Record the probe intent before claiming its launch")
        if value["id"] in records.get("probe", {}):
            raise ResearchError("probe_already_recorded", "The probe outcome already exists; replay its receipt without launching")
        return [immutable_record(records, "probe_launch", value["id"], value)], value
    return prepared_mutation(store, "research.probe_launch", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def find_probe_terminal(store, launch):
    for name in ("terminal.json", "supervisor-terminal.json"):
        terminal = read_probe_file(store, launch["directory"] + "/" + name)
        if (not isinstance(terminal, dict) or terminal.get("config_sha256") != launch["config_artifact"]["sha256"]
                or not is_finite_number(terminal.get("elapsed_seconds")) or terminal["elapsed_seconds"] < 0
                or not is_probe_exit_status(terminal.get("exit_status"))):
            continue
        if name == "supervisor-terminal.json":
            supervision = read_probe_file(store, launch["directory"] + "/supervision.json")
            if (not isinstance(supervision, dict) or terminal.get("exit_status") != "timed_out"
                    or any(terminal.get(key) != supervision.get(key) for key in ("program", "started", "deadline"))
                    or not isinstance(terminal.get("program"), dict)
                    or type(terminal["program"].get("process_group")) is not int
                    or find_probe_group_members(terminal["program"]["process_group"])):
                continue
        return terminal
    return None


def find_supervisor_timeout(store, launch, supervision):
    timeout = read_probe_file(store, launch["directory"] + "/supervisor-timeout.json")
    if (not isinstance(timeout, dict)
            or any(timeout.get(key) != supervision.get(key) for key in ("config_sha256", "program", "started", "deadline"))
            or not is_finite_number(timeout.get("elapsed_seconds")) or timeout["elapsed_seconds"] < 0
            or not isinstance(timeout.get("supervisor"), dict) or type(timeout["supervisor"].get("pid")) is not int
            or timeout["supervisor"]["pid"] <= 0 or not timeout["supervisor"].get("start_identity")):
        return None
    return timeout


def record_probe_recovery(store, payload, **identity):
    return prepared_mutation(store, "research.probe_recovery", payload,
        lambda records, value: ([immutable_record(records, "probe_recovery", value["id"], value)], value), **identity)


def reconcile_probe(store, intent, launch):
    """The caller holds the free owner lock; a missing outcome alone is never recovery authority."""
    supervision = read_probe_file(store, launch["directory"] + "/supervision.json") if launch else None
    program = supervision.get("program") if isinstance(supervision, dict) else None
    if (isinstance(program, dict) and type(program.get("process_group")) is int and program["process_group"] > 0
            and find_probe_group_members(program["process_group"])):
        return {"id": intent["id"], "status": "pending", "state": "running"}
    outcome = read_probe_outcome(store, launch) if launch else None
    if outcome is None and launch:
        terminal = find_probe_terminal(store, launch)
        if terminal is not None:
            outcome = capture_probe_outcome(store.root, launch, terminal)
    if outcome is None:
        usage = read_probe_file(store, launch["directory"] + "/usage.json") if launch else None
        elapsed = usage.get("elapsed_seconds", 0) if isinstance(usage, dict) else 0
        if not is_finite_number(elapsed) or elapsed < 0:
            elapsed = 0
        outcome = {"id": intent["id"], "command": (launch or {}).get("command", shlex.join([intent["interpreter"], "<pinned-program>"])),
                   "outputs": [],
                   "exit_status": "interrupted", "elapsed_seconds": elapsed,
                   "observation": "The owner lock is free and complete terminal evidence is unavailable. Recorded usage is the last measured lower bound; remaining usage is unknown."}
        if launch:
            partial = capture_probe_outcome(store.root, launch, {"exit_status": "interrupted", "elapsed_seconds": elapsed})
            outcome.update({key: partial[key] for key in ("outputs", "stdout", "stderr")})
        else:
            sealed = "program_run" in intent and not intent["program_run"]["read_outputs"]
            outcome.update({role: pin_probe_artifact(store.root, intent["id"], b"", "text/plain", role, sealed=sealed)
                            for role in ("stdout", "stderr")})
        if "program_run" in intent:
            if launch:
                outcome["program_run"] = partial["program_run"]
            else:
                outcome["program_run"] = build_program_run(intent, {}, [], outcome)
        recovery = {"id": intent["id"], "usage_incomplete": True, "owner_lost": True}
        commit_probe_mutation(store, record_probe_recovery, recovery, "probe-recovery:" + intent["id"])
    if launch:
        json_projection(store.root, launch["directory"] + "/outcome.json", {"config_sha256": launch["config_artifact"]["sha256"], "outcome": outcome})
    return commit_probe_mutation(store, record_probe, outcome, "probe:" + intent["id"])


def recover_expired_probe(store, intent, launch, owner):
    """The caller serializes launch/recovery; an occupied owner lock retains the reservation."""
    identifier = intent["id"]
    pending = {"id": identifier, "status": "pending", "state": "running"}
    if launch is None or owner is not None and (read_probe_outcome(store, launch) is not None or find_probe_terminal(store, launch) is not None):
        return reconcile_probe(store, intent, launch) if owner is not None else pending
    supervision = read_probe_file(store, launch["directory"] + "/supervision.json")
    if (not isinstance(supervision, dict) or supervision.get("config_sha256") != launch["config_artifact"]["sha256"]
            or not is_finite_number(supervision.get("started")) or not is_finite_number(supervision.get("deadline"))
            or not is_finite_number(supervision.get("execution_started", supervision["started"]))
            or supervision.get("execution_started", supervision["started"]) < supervision["started"]
            or supervision["deadline"] != supervision.get("execution_started", supervision["started"]) + intent["timeout_seconds"]):
        return reconcile_probe(store, intent, launch) if owner is not None else pending
    worker, program = supervision.get("worker"), supervision.get("program")
    if (not isinstance(worker, dict) or type(worker.get("pid")) is not int or worker["pid"] <= 0
            or not isinstance(program, dict) or type(program.get("pid")) is not int or program["pid"] <= 0
            or program.get("process_group") != program["pid"] or not program.get("start_identity")):
        return pending
    if find_probe_process_identity(worker["pid"]) == worker:
        return pending
    supervisor_timeout = find_supervisor_timeout(store, launch, supervision)
    if supervisor_timeout is not None:
        if (owner is None or find_probe_group_members(program["process_group"])
                or find_probe_process_identity(supervisor_timeout["supervisor"]["pid"]) == supervisor_timeout["supervisor"]):
            return pending
        return finish_probe_timeout(store, intent, launch, supervision, supervisor_timeout["elapsed_seconds"])
    termination = {"config_sha256": launch["config_artifact"]["sha256"], "program": program,
                   "started": supervision["started"], "deadline": supervision["deadline"]}
    previous = read_probe_file(store, launch["directory"] + "/termination.json")
    resuming = (isinstance(previous, dict) and {key: previous.get(key) for key in termination} == termination
                and is_finite_number(previous.get("elapsed_seconds")) and previous["elapsed_seconds"] >= intent["timeout_seconds"])
    current = find_probe_process_identity(program["pid"])
    if current != program and not resuming:
        if find_probe_group_members(program["process_group"]):
            return pending
        return reconcile_probe(store, intent, launch) if owner is not None else pending
    if read_probe_clock() < supervision["deadline"]:
        return pending
    elapsed = previous["elapsed_seconds"] if resuming and current != program else None
    if current == program:
        json_projection(store.root, launch["directory"] + "/termination.json",
                        dict(termination, elapsed_seconds=read_probe_clock() - supervision["started"]))
        try:
            os.killpg(program["process_group"], signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2
    while True:
        if find_probe_process_identity(program["pid"]) != program and not find_probe_group_members(program["process_group"]):
            if owner is not None:
                return finish_probe_timeout(store, intent, launch, supervision, elapsed)
            with hold_probe_owner(store.root, identifier) as available:
                if available is not None:
                    return finish_probe_timeout(store, intent, launch, supervision, elapsed)
        if time.monotonic() >= deadline:
            return pending
        time.sleep(0.01)


def finish_probe_timeout(store, intent, launch, supervision, elapsed):
    if elapsed is not None:
        recovery = {"id": intent["id"], "usage_incomplete": True, "owner_lost": True}
        commit_probe_mutation(store, record_probe_recovery, recovery, "probe-recovery:" + intent["id"])
    json_projection(store.root, launch["directory"] + "/terminal.json", {
        "config_sha256": launch["config_artifact"]["sha256"], "exit_status": "timed_out",
        "elapsed_seconds": elapsed if elapsed is not None else read_probe_clock() - supervision["started"]})
    return reconcile_probe(store, intent, launch)


def launch_probe(store, *, identifier, question, script, inputs=(), output_dir=".", timeout, observation=None,
                 program_run=None):
    """Pin and reserve once; retries observe the original owner or reconcile its evidence."""
    text(identifier, "Probe ID")
    text(question, "Probe question")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
        raise ResearchError("unsafe_path", "Probe ID must be a safe directory name starting with a letter or digit")
    if not is_finite_number(timeout) or timeout <= 0:
        raise ResearchError("invalid_input", "Probe timeout must be a finite positive number")
    if observation is not None:
        text(observation, "Probe observation")
    relative_output = Path(output_dir)
    if relative_output.is_absolute() or ".." in relative_output.parts or "\\" in output_dir or "\x00" in output_dir:
        raise ResearchError("unsafe_path", "Probe output directory must stay under its fresh working directory")
    previous = find_probe_receipt(store, identifier)
    if previous is not None:
        return previous
    with hold_probe_lock(store.root, identifier, "recovery.lock") as recovery:
        if recovery is None:
            return {"id": identifier, "status": "pending", "state": "running"}
        return launch_owned_probe(store, identifier, question, script, inputs, relative_output, timeout, observation,
                                  program_run)


def launch_owned_probe(store, identifier, question, script, inputs, relative_output, timeout, observation,
                       program_run=None):
    with hold_probe_owner(store.root, identifier) as owner:
        previous = find_probe_receipt(store, identifier)
        if previous is not None:
            return previous
        records = store.snapshot()["records"]
        existing = records.get("probe_intent", {}).get(identifier)
        if existing is not None:
            return recover_expired_probe(store, existing, records.get("probe_launch", {}).get(identifier), owner)
        if owner is None:
            return {"id": identifier, "status": "pending", "state": "running"}
        artifacts = store.build_artifact_store()
        files = [("program/" + Path(script).name, Path(script).read_bytes(), "text/x-python")]
        files.extend(("inputs/" + str(index) + "/" + Path(path).name, Path(path).read_bytes(),
                      mimetypes.guess_type(str(path))[0] or "application/octet-stream") for index, path in enumerate(inputs))
        pinned = [artifacts.put(data, media_type) for _, data, media_type in files]
        intent = {"id": identifier, "question": question, "interpreter": sys.executable,
                  "program": pinned[0], "inputs": pinned[1:], "timeout_seconds": timeout}
        if program_run is not None:
            intent["program_run"] = program_run
        intent = commit_probe_mutation(store, record_probe_intent, intent, "probe-intent:" + identifier)["result"]
        directory = build_probe_directory(identifier) + "/run-" + uuid.uuid4().hex
        initial = {}
        for (name, data, _), artifact in zip(files, pinned):
            write_projection(store.root, directory + "/work/" + name, data)
            initial[name] = {"mtime_ns": (store.root / directory / "work" / name).stat().st_mtime_ns, "sha256": artifact["sha256"]}
        with checked_parent(store.root, directory + "/work/" + (relative_output.as_posix() + "/" if str(relative_output) != "." else "") + ".probe-directory", create=True):
            pass
        argv = [sys.executable] + [name for name, _, _ in files]
        config = {"id": identifier, "directory": directory, "argv": argv, "command": shlex.join(argv), "output_dir": relative_output.as_posix(),
                  "timeout_seconds": timeout, "observation": observation, "initial": initial, "owner": {"pid": os.getpid(), "token": uuid.uuid4().hex}}
        if "program_run" in intent:
            config.update(program_run=intent["program_run"], started_at=format_utc_now())
        config_artifact = artifacts.put((json.dumps(config, sort_keys=True) + "\n").encode(), "application/json")
        launch = dict(config, config_artifact=config_artifact)
        write_projection(store.root, directory + "/config.json", artifacts.read(config_artifact))
        try:
            commit_probe_mutation(store, record_probe_launch, launch, "probe-launch:" + identifier)
        except ResearchError as error:
            if error.code != "probe_already_recorded":
                raise
            return find_probe_receipt(store, identifier)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        try:
            worker = subprocess.Popen([sys.executable, "-m", "research_harness.probe_worker", str(store.root), directory,
                                       config_artifact["sha256"], str(owner)], cwd=store.root, env=env, pass_fds=(owner,),
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            json_projection(store.root, directory + "/terminal.json", {"config_sha256": config_artifact["sha256"],
                "exit_status": "failed", "elapsed_seconds": 0, "error": {"message": str(error)}})
        else:
            worker.wait()
            # A dead worker can leave a live program holding the inherited owner lock. The launcher still holds
            # its own copy, so absence of terminal evidence cannot establish owner loss here.
            if (read_probe_outcome(store, launch) is None
                    and find_probe_terminal(store, launch) is None):
                return {"id": identifier, "status": "pending", "state": "running"}
        return reconcile_probe(store, intent, launch)
