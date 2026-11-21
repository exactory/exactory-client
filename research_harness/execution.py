"""Pinned one-time local/remote execution and recovery over domain admissions.

Lock order is execution-owner lock, then a short research Store operation. The
worker owns a different run lock and never opens Store. The program of an
admission runs at most once: the same request starts a claimed local run whose
program never started (_is_unstarted_local_run), and every other claimed run is
reconciled, not launched again. A lost launcher response stays pending until its
actual outcome is reconciled, retaining every reservation.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .artifacts import ArtifactStore, _relative_parts
from .development import record_execution, validate_admitted_execution
from .errors import ResearchError
from .finishing import retry_finishing_write
from .evidence import digest
from .execution_outputs import log_bytes, output_metric, output_path, read_sealed_outputs, seal_outputs
from .operations import (_read_preparation, fields, immutable_record, is_finite_number, prepared_mutation,
                         prepared_task_mutation, text)
from .scientific_json import is_json_media_type
from .storage import _canonical
from .workspace import checked_parent, json_projection, read_file, strict_json, write_projection


# The messages of a record of an admitted run that the store refused beyond the wait: the record that releases the
# program, a record of a run with a terminal outcome, and a record of a resolution that reconcile-run gave.
RUN_START_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. The start of this '
    'admitted run was not recorded. The program did not run, and the admission keeps its claim. Run the same '
    'exactory-lab run command again with the request ID in details.request_id. It starts the program of this '
    'admission. If it is refused with execution_live, the earlier worker is still ending. Run it again after a minute.')
RUN_RECORD_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. A record of this run was '
    'not written. Nothing false was recorded. The program ran once, and its terminal outcome, its streams and its '
    'output files are kept under research/runs. Run exactory-research reconcile-run for the admission ID in '
    'details.admission_id, or the same exactory-lab run command with the request ID in details.request_id. Each of '
    'them records the run from the kept files and does not run the program again.')
RUN_RESOLUTION_STORE_BUSY_MSG = (
    'The research store stayed busy, or kept changing the records that this write read. A record of this '
    'resolution was not written. Nothing false was recorded. Run exactory-research reconcile-run for the admission ID '
    'in details.admission_id, with the same resolution and reason and with the current revision in '
    '--expected-revision. It records the resolution.')
# The messages of a claimed local run whose program did not start: its worker did not start, its worker ended while
# the record that releases the program waited, or reconcile-run finds no record that released it.
RUN_WORKER_NOT_STARTED_MSG = (
    'The worker process did not start, so the program did not run, and the admission keeps its claim. Run the same '
    'exactory-lab run command again with the request ID in details.request_id. It starts a worker for this admission. '
    'To end the run instead, record it with exactory-research reconcile-run, with resolution "interrupted" and a '
    'reason.')
RUN_WORKER_ENDED_MSG = (
    'The worker process ended before the start of this admitted run was recorded, so the program did not run, and the '
    'admission keeps its claim. Run the same exactory-lab run command again with the request ID in details.request_id. '
    'It starts a new worker for this admission.')
RUN_NOT_RELEASED_MSG = (
    'No terminal outcome is available, and no record released the program of this claimed run, so the program did not '
    'run. Run the exactory-lab run command of this admission again with the request ID in details.request_id. It '
    'starts the program. To end the run instead, record it with exactory-research reconcile-run, with resolution '
    '"interrupted" and a reason.')


def _directory(admission_id):
    return "research/runs/" + digest({"admission_id": admission_id})


def record_imported_execution(store, payload, *, expected_revision, request_id):
    """The public result command imports history; managed results are observed."""
    snapshot = _read_preparation(store)
    if not snapshot.has_request(request_id):
        origin = payload.get("origin") if isinstance(payload, dict) else None
        if not isinstance(origin, dict) or origin.get("kind") != "imported":
            raise ResearchError("managed_result_requires_reconciliation", "Use exactory-lab run or exactory-research "
                                "reconcile-run for a managed outcome; result imports explicitly attributed "
                                "execution history")
    return record_execution(store, payload, expected_revision=expected_revision, request_id=request_id,
                            _snapshot=snapshot)


@contextmanager
def _owner(root, admission_id, *, worker=False):
    relative = _directory(admission_id) + ("/worker.lock" if worker else "/client.lock")
    with checked_parent(root, relative, create=True) as (directory, name):
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    # The checked-directory context translates filesystem errors only while
    # opening the lock, not transport exceptions from an owned operation.
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ResearchError("execution_live", "The admitted execution still has a live owner; wait or reconcile later") from error
        yield
    finally:
        os.close(descriptor)


def _describe_interpreter(interpreter_path):
    """The runtime record of the running Python, started as the absolute `interpreter_path`."""
    resolved_file = Path(interpreter_path).resolve()
    runtime = {"path": str(resolved_file), "sha256": hashlib.sha256(resolved_file.read_bytes()).hexdigest(),
               "python": sys.version.split()[0]}
    if Path(interpreter_path).is_symlink():
        # A worker starts a link itself: a venv's bin/python3 is a symlink, and only that path gives the
        # program the venv's site-packages. The file it resolves to stays pinned by its path and bytes.
        runtime.update(path=interpreter_path, resolved_path=str(resolved_file))
    return runtime


def _runtime(command, backend="local"):
    argv = command["argv"]
    if backend == "colab":
        if len(argv) < 2 or not Path(argv[0]).is_absolute() or not command["versions"].get("python"):
            raise ResearchError("unsupported_execution", "Declare an absolute Python interpreter and its remote version")
        return {"path": argv[0], "python": command["versions"]["python"], "mapping": "runner_python"}
    if len(argv) < 2 or not Path(argv[0]).is_absolute() or Path(argv[0]).resolve() != Path(sys.executable).resolve():
        raise ResearchError("unsupported_execution", "The lab launcher supports an explicit current Python interpreter and script")
    if command["versions"].get("python") != sys.version.split()[0]:
        raise ResearchError("execution_runtime_changed", "Run with the exact admitted Python version")
    return _describe_interpreter(argv[0])


def _build_earlier_runtime(runtime):
    """The record that exactory-client 0.47.0 and earlier wrote for the same interpreter: the resolved file only."""
    earlier_runtime = dict(runtime, path=runtime.get("resolved_path", runtime["path"]))
    earlier_runtime.pop("resolved_path", None)
    return earlier_runtime


def _files(store, admission, binding, *, current=True):
    artifacts = store.build_artifact_store()
    pairs = [(binding["script"], admission["command"]["program"])] + [(v["path"], v["artifact"]) for v in binding["inputs"]]
    for path, artifact in pairs:
        data = artifacts.read(artifact)
        if current and read_file(store.root, "experiment/" + path) != data:
            raise ResearchError("execution_input_changed", "The declared program or input changed after admission", {"path": path})
    return pairs


def bind_execution(store, payload, *, expected_revision, request_id):
    """Bind admitted bytes to backend, paths, timeout and observed output names."""
    artifacts = store.build_artifact_store()
    def prepare(records, value):
        fields(value, ("admission_id", "script", "backend", "timeout_seconds", "inputs", "outputs", "usage_unit"))
        admission = validate_admitted_execution(records, artifacts, value["admission_id"])
        _relative_parts(value["script"])
        if value["backend"] not in ("local", "colab"):
            raise ResearchError("invalid_execution", "Backend must be local or colab")
        timeout = value["timeout_seconds"]
        if not is_finite_number(timeout) or timeout <= 0:
            raise ResearchError("invalid_execution", "A finite positive timeout is required")
        runtime = _runtime(admission["command"], value["backend"])
        recorded_binding = records.get("execution_binding", {}).get(admission["id"])
        if recorded_binding is not None and recorded_binding["runtime"] == _build_earlier_runtime(runtime):
            # The same binding sent again keeps the record that an earlier release wrote for this interpreter.
            runtime = recorded_binding["runtime"]
        expected_script = str(store.root / "experiment" / value["script"])
        if Path(admission["command"]["argv"][1]).resolve() != Path(expected_script).resolve():
            raise ResearchError("execution_identity_mismatch", "The admitted argv must name the exact declared script")
        for key in ("inputs", "outputs"):
            if not isinstance(value[key], list):
                raise ResearchError("invalid_execution", key + " must be an array")
        for item in value["inputs"]:
            fields(item, ("path", "artifact"))
            _relative_parts(item["path"])
        paths = [value["script"]] + [item["path"] for item in value["inputs"]]
        if len(set(paths)) != len(paths) or [v["artifact"] for v in value["inputs"]] != admission["command"]["inputs"]:
            raise ResearchError("execution_identity_mismatch", "Bind each admitted input in order to one distinct relative path")
        plan = records["cycle_plan"][admission["cycle_id"]]["payload"]
        requirement_kinds = {item["id"]: item["kind"] for item in plan["evidence_requirements"]}
        output_ids = set()
        output_paths = set()
        for output in value["outputs"]:
            fields(output, ("id", "requirement_id", "path", "media_type"))
            text(output["id"], "Output ID")
            _relative_parts(output["path"])
            if output["id"] in output_ids or output["path"] in output_paths or output["requirement_id"] not in requirement_kinds:
                raise ResearchError("invalid_execution", "Each unique output must name a planned evidence requirement")
            if output["path"] in paths:
                raise ResearchError("invalid_execution", "An output cannot replace a frozen input")
            output_ids.add(output["id"])
            output_paths.add(output["path"])
            artifacts.put(b"", output["media_type"])
            # Whole-artifact locators admit result outputs of any media type; validation still needs text or JSON.
            # The rule applies to a new binding only: a recorded one is immutable, and the same payload returns it.
            if (recorded_binding is None and requirement_kinds[output["requirement_id"]] == "validation"
                    and not (output["media_type"].lower().startswith("text/") or is_json_media_type(output["media_type"]))):
                raise ResearchError("invalid_execution", "Validation evidence needs a text or JSON output, but output "
                                    + output["id"] + " is " + output["media_type"]
                                    + "; bind figures and other binary files to a result or log requirement",
                                    {"output_id": output["id"], "requirement_id": output["requirement_id"],
                                     "media_type": output["media_type"]})
        if value["usage_unit"] not in ("execution", "wall_seconds") or plan["resource_limits"]["unit"] != value["usage_unit"]:
            raise ResearchError("unsupported_usage", "Actual lab accounting supports planned execution or wall_seconds units")
        record = dict(value, admission_digest=admission["digest"], runtime=runtime)
        if value["backend"] == "colab":
            from .remote_execution import transport_root
            record["transport"] = {"sync_root": str(transport_root()), "protocol": 2}
        _files(store, admission, record)
        record["digest"] = digest(record)
        return [immutable_record(records, "execution_binding", admission["id"], record)], record
    return prepared_mutation(store, "execution.bind", payload, prepare, expected_revision=expected_revision, request_id=request_id)


def _binding(records, admission_id):
    value = records.get("execution_binding", {}).get(admission_id)
    if value is None:
        raise ResearchError("execution_binding_required", "Bind the admitted backend, timeout, input paths and outputs with exactory-research bind-run")
    return value


def _materialize(store, admission, binding, plan):
    directory = _directory(admission["id"])
    pairs = _files(store, admission, binding)
    for relative, artifact in pairs:
        path = directory + "/work/" + relative
        write_projection(store.root, path, store.build_artifact_store().read(artifact))
        (store.root / path).chmod(0o444)
    for name in ("logs", "results", "plots"):
        with checked_parent(store.root, directory + "/work/" + name + "/.keep", create=True):
            pass
    replacements = {str(store.root / "experiment" / p): str(store.root / directory / "work" / p) for p, _ in pairs}
    argv = [replacements.get(str(Path(arg).resolve()), arg) if Path(arg).is_absolute() else arg
            for arg in admission["command"]["argv"]]
    argv[1] = str(store.root / directory / "work" / binding["script"])
    argv[0] = binding["runtime"]["path"]
    requirement_kinds = {item["id"]: item["kind"] for item in plan["evidence_requirements"]}
    config = {"admission_id": admission["id"], "binding_digest": binding["digest"], "argv": argv,
              "script": binding["script"], "files": [{"path": p, "sha256": a["sha256"]} for p, a in pairs],
              "timeout_seconds": binding["timeout_seconds"], "seed": admission["command"]["seed"],
              "runtime": binding["runtime"], "backend": binding["backend"],
              "outputs": binding["outputs"],
              # The metric's last source: the first declared JSON output of a validation requirement.
              "metric_output": next((output_path(item["path"]) for item in binding["outputs"]
                                     if requirement_kinds[item["requirement_id"]] == "validation" and is_json_media_type(item["media_type"])), None)}
    if binding["backend"] == "colab":
        config["transport"] = binding["transport"]
    return config


def _claim(store, admission_id, expected_revision, request_id):
    artifacts = store.build_artifact_store()
    def prepare(records, value):
        if admission_id in records.get("execution_claim", {}):
            raise ResearchError("execution_recovery_required", "This admitted run was already claimed; reconcile it instead of relaunching")
        admission = validate_admitted_execution(records, artifacts, admission_id)
        binding = _binding(records, admission_id)
        runtime = _runtime(admission["command"], binding["backend"])
        # A binding written by an earlier release names the resolved file as its path; its run keeps that path.
        if binding["runtime"] not in (runtime, _build_earlier_runtime(runtime)):
            raise ResearchError("execution_runtime_changed", "The admitted interpreter file or its bytes changed")
        config = _materialize(store, admission, binding, records["cycle_plan"][admission["cycle_id"]]["payload"])
        claim = {"admission_id": admission_id, "binding_digest": binding["digest"], "config": config,
                 "config_artifact": artifacts.put(_canonical(config).encode(), "application/json"),
                 "request_id": request_id, "claimed_revision": expected_revision + 1, "token": uuid.uuid4().hex}
        return [immutable_record(records, "execution_claim", admission_id, claim)], claim
    return prepared_mutation(store, "execution.claim", {"admission_id": admission_id}, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def _name_launch(claim):
    """What a refusal of a record of a run carries, so that its message can name the command that goes on."""
    return {"admission_id": claim["admission_id"], "request_id": claim["request_id"]}


def _prelaunch(store, admission_id, claim, worker):
    artifacts = store.build_artifact_store()
    def prepare(records, value):
        admission = validate_admitted_execution(records, artifacts, admission_id)
        binding = _binding(records, admission_id)
        if binding["digest"] != claim["binding_digest"]:
            raise ResearchError("execution_identity_mismatch", "The run binding differs from the durable claim")
        _files(store, admission, binding)
        result = {"admission_id": admission_id, "binding_digest": binding["digest"], "revision": None}
        return [("execution_prelaunch", admission_id, result)], result
    def stamp_checked_revision(changes, result, revision):
        # The check held on the store as it stood before this commit: the revision that this record has always named.
        result = dict(result, revision=revision - 1)
        return [("execution_prelaunch", admission_id, result)], result
    def commit():
        # A worker that ended cannot read its token, so a record that released its program would stand for a run that
        # never happens, and the same request could not start the program again.
        if worker.poll() is not None:
            raise ResearchError("execution_recovery_required", RUN_WORKER_ENDED_MSG, _name_launch(claim))
        return prepared_task_mutation(store, "execution.prelaunch",
            {"admission_id": admission_id, "claim": claim["config_artifact"]["sha256"]}, prepare,
            request_id="prelaunch-" + digest(claim), finalize=stamp_checked_revision)
    # The commit depends on the records that the check read. The worker waits for its token meanwhile.
    return retry_finishing_write(commit, RUN_START_STORE_BUSY_MSG, kept=_name_launch(claim))


def _is_unstarted_local_run(store, records, claim):
    """Whether the program of a claimed local run never started: no record released it, the run has no outcome, and
    its folder holds no stream and no terminal outcome. The caller holds the worker lock, so no worker of this claim
    reads its token now. A worker starts the program only after it has read its token, which its launcher sends only
    after the record that releases the program, and it creates both streams before the program; the launcher holds
    the client lock from the claim to the token, so no other launch of the admission runs meanwhile."""
    admission_id = claim["admission_id"]
    directory = store.root / _directory(admission_id)
    return (claim["config"]["backend"] == "local" and admission_id not in records.get("execution_prelaunch", {})
            and admission_id not in records.get("execution_outcome", {})
            and not any((directory / name).exists() for name in ("terminal.json", "stdout", "stderr")))


def launch_execution(store, admission_id, *, expected_revision, request_id):
    with _owner(store.root, admission_id):
        before = store.snapshot()["records"]
        old = before.get("execution_claim", {}).get(admission_id)
        if old is not None:
            if old["request_id"] != request_id:
                raise ResearchError("execution_recovery_required", "An admitted identity is launched once; reconcile the original run")
            store.mutate("execution.claim", {"admission_id": admission_id}, lambda tx: None,
                         expected_revision=expected_revision, request_id=request_id)
            with _owner(store.root, admission_id, worker=True):
                unstarted = _is_unstarted_local_run(store, before, old)
                if unstarted:
                    # The notice of a worker of the earlier launch names no worker of this one.
                    (store.root / _directory(admission_id) / "ready.json").unlink(missing_ok=True)
            if not unstarted:
                reconciliation_id = ("remote-observe-" + digest(old) if old["config"]["backend"] == "colab"
                                     else "reconcile-" + request_id)
                return reconcile_execution(store, {"admission_id": admission_id}, expected_revision=None,
                                           request_id=reconciliation_id, owned=True)
            # The launch that made this claim ended before the program was released, so this launch releases it.
            claim = old
        else:
            claim = _claim(store, admission_id, expected_revision, request_id)["result"]
        if claim["config"]["backend"] == "colab":
            from .remote_execution import launch_colab
            return launch_colab(store, claim)
        directory = store.root / _directory(admission_id)
        json_projection(store.root, _directory(admission_id) + "/config.json", claim["config"])
        try:
            # Appended, so that a second start of the same claim keeps what the earlier worker wrote.
            with (directory / "launcher.log").open("ab") as diagnostics:
                worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name("launcher.py")),
                    str(directory), claim["config_artifact"]["sha256"], claim["token"]],
                    stdin=subprocess.PIPE, bufsize=0, stdout=diagnostics, stderr=diagnostics, start_new_session=True)
        except OSError as error:
            # At a limit on processes or open files, no worker holds the claimed token, so the program never runs.
            raise ResearchError("execution_recovery_required", RUN_WORKER_NOT_STARTED_MSG,
                                dict(_name_launch(claim), errno=error.errno)) from error
        is_worker_ready = token_sent = False
        try:
            # A loaded machine starts the worker slowly. While the worker is alive, the launch waits for it to become
            # ready as long as it waits below for the run to end: the run's timeout plus 10 seconds.
            worker_wait_seconds = claim["config"]["timeout_seconds"] + 10
            deadline = time.monotonic() + worker_wait_seconds
            while not (directory / "ready.json").exists() and worker.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            if not (directory / "ready.json").exists():
                raise ResearchError("execution_recovery_required", "The launcher outcome is unknown; reconcile the claimed identity")
            ready = strict_json(read_file(store.root, _directory(admission_id) + "/ready.json"))
            if ready != {"pid": worker.pid, "token": claim["token"], "config_sha256": claim["config_artifact"]["sha256"]}:
                raise ResearchError("execution_identity_mismatch", "The launcher did not establish the claimed identity")
            is_worker_ready = True
            _prelaunch(store, admission_id, claim, worker)
            try:
                # The worker's input is unbuffered (bufsize=0), so this one write sends the token and leaves nothing for
                # the close to flush.
                worker.stdin.write((claim["token"] + "\n").encode())
                token_sent = True
            except BrokenPipeError:
                # A worker that ended before it read its token never ran the program, and reconciliation reports that.
                pass
            worker.stdin.close()
            try:
                # Popen.wait accepts a timeout of any length. Popen.communicate does not: with a timeout it waits in
                # poll(), which refuses more than 2**31 - 1 milliseconds (about 24.86 days).
                worker.wait(timeout=worker_wait_seconds)
            except subprocess.TimeoutExpired:
                # The exit wait below gives the worker 6 more seconds. A worker that ends in them is reconciled, and a
                # worker that is still live is reported.
                pass
        finally:
            if not worker.stdin.closed:
                worker.stdin.close()
            if worker.poll() is None:
                try:
                    worker.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    # A ready worker that never received its token ends on its closed input without the program, so the
                    # refusal that ended this launch stands. Do not relabel a running or unobserved producer as failed.
                    if token_sent or not is_worker_ready:
                        raise ResearchError("execution_recovery_required", "The worker is still live or its outcome is unknown")
        return reconcile_execution(store, {"admission_id": admission_id}, expected_revision=None,
                                   request_id="reconcile-" + request_id, owned=True)


def record_reconciliation(store, payload, summary, *, expected_revision, request_id):
    """Replay the receipt of a reconciliation for a check of the reconcile-run command."""
    return store.mutate("execution.reconcile", payload, lambda tx: summary,
                        expected_revision=expected_revision, request_id=request_id)


def _select_record_busy_msg(store, admission_id):
    """The message of a record of a claimed run that the store refused beyond the wait. A run with a terminal outcome
    is recorded from its kept files, and a claim without one from the resolution that reconcile-run gave."""
    if (store.root / _directory(admission_id) / "terminal.json").exists():
        return RUN_RECORD_STORE_BUSY_MSG
    return RUN_RESOLUTION_STORE_BUSY_MSG


def _finish_reconciliation(store, payload, observation, request_id, claim, busy_msg):
    """Record the receipt of a reconciliation. The write reads nothing, so it depends on no revision."""
    receipt = retry_finishing_write(lambda: store.mutate_serialized(
        "execution.reconcile", payload, lambda transaction, revision: observation["summary"],
        request_id=request_id), busy_msg, kept=_name_launch(claim))
    summary = observation["summary"]
    directory = _directory(payload["admission_id"])
    json_projection(store.root, directory + "/summary.json", summary)
    return receipt["result"]


def _reconcile(store, payload, expected_revision, request_id):
    admission_id = payload["admission_id"]
    records = store.snapshot()["records"]
    claim = records.get("execution_claim", {}).get(admission_id)
    if claim is None:
        raise ResearchError("execution_not_claimed", "No launcher claimed this admitted run")
    prior = records.get("execution_observation", {}).get(admission_id)
    if prior is not None:
        return _finish_reconciliation(store, payload, prior, request_id, claim,
                                      _select_record_busy_msg(store, admission_id))
    # The revision is the input of the reconcile-run command. The launcher reconciles its own run without one.
    if expected_revision is not None and store.revision != expected_revision:
        raise ResearchError("stale_revision", "Read the current revision before reconciling a new execution observation")
    binding = _binding(records, admission_id)
    admission = records["execution_admission"][admission_id]
    directory = _directory(admission_id)
    unreleased = payload.get("resolution") == "not_released"
    if unreleased and (binding["backend"] != "colab" or not payload.get("reason")
                       or admission_id in records.get("execution_remote_release", {})):
        raise ResearchError("execution_recovery_required", "Only a remote claim with no durable execution release can be resolved as not_released")
    if binding["backend"] == "colab":
        if not unreleased:
            from .remote_execution import collect_colab
            collect_colab(store, claim)
    with _owner(store.root, admission_id, worker=True):
        terminal = store.root / directory / "terminal.json"
        if not terminal.exists():
            if not unreleased and (payload.get("resolution") != "interrupted" or not payload.get("reason") or binding["backend"] != "local"):
                if _is_unstarted_local_run(store, records, claim):
                    raise ResearchError("execution_recovery_required", RUN_NOT_RELEASED_MSG, _name_launch(claim))
                raise ResearchError("execution_recovery_required", "No terminal outcome is available; preserve the claim and reconcile. A dead local owner may be recorded interrupted with a reason.")
            observed = {"status": "interrupted", "exit_code": None, "duration_s": None, "binding_digest": binding["digest"],
                        "config_sha256": claim["config_artifact"]["sha256"], "reason": payload["reason"]}
            observed["output_seal"] = seal_outputs(store.root, directory, claim["config"], origin="recovery")
        else:
            observed = strict_json(read_file(store.root, directory + "/terminal.json"))
        if observed.get("binding_digest") != binding["digest"] or observed.get("config_sha256") != claim["config_artifact"]["sha256"]:
            raise ResearchError("execution_identity_mismatch", "The observed worker output belongs to a different frozen run")
        artifacts = store.build_artifact_store()
        files = read_sealed_outputs(store.root, directory, claim["config"], observed)
        if terminal.exists() and observed["output_seal"]["origin"] != "worker":
            raise ResearchError("execution_output_seal_required", "A producer terminal requires its original worker output seal")
        outputs = []
        output_media_types = {output_path(item["path"]): item["media_type"] for item in binding["outputs"]}
        for item in binding["outputs"]:
            path = output_path(item["path"])
            if path in files:
                outputs.append({"id": item["id"], "requirement_id": item["requirement_id"], "artifact": artifacts.put(files[path], item["media_type"])})
        stderr = files.get("stderr", b"")
        metric = output_metric(claim["config"], files)
        log_ref = artifacts.put(log_bytes(files), "text/plain; charset=utf-8")
        summary = {"admission_id": admission_id, "cycle_id": admission["cycle_id"], "node": Path(binding["script"]).stem,
                   "ok": observed["status"] == "completed" and metric is not None,
                   "is_buggy": observed["status"] != "completed" or metric is None,
                   "returncode": observed["exit_code"] if observed["status"] != "timed_out" else -1,
                   "timed_out": observed["status"] == "timed_out", "duration_s": observed["duration_s"],
                   "metric": metric, "seed": admission["command"]["seed"], "log": log_ref["path"],
                   "stderr_tail": stderr.decode("utf-8", "replace")[-800:], "backend": binding["backend"],
                   "program_sha256": admission["command"]["program"]["sha256"], "timeout_seconds": binding["timeout_seconds"],
                   "scientific_validation": False}
        usage = observed["duration_s"]
        if binding["usage_unit"] == "execution":
            usage = 1 if observed["exit_code"] is not None else None
        execution = {"id": "launched-" + digest({"admission_id": admission_id}), "cycle_id": admission["cycle_id"],
                     "origin": {"kind": "managed", "admission_id": admission_id}, "command": admission["command"],
                     "status": observed["status"], "exit_code": observed["exit_code"],
                     "usage": {"units": usage, "reason": "Observed foreground execution; unknown usage retains the full admitted reservation."},
                     "outputs": outputs, "notes": "Observed by the pinned managed launcher. Process completion supplies no scientific result validation."}
        observation = {"admission_id": admission_id, "claim_digest": digest(claim), "binding_digest": binding["digest"],
                       "execution": execution,
                       "terminal": artifacts.put(_canonical(observed).encode(), "application/json"), "log": log_ref, "summary": summary,
                       "files": [{"path": path, "artifact": artifacts.put(data, output_media_types.get(path, "application/octet-stream"))}
                                 for path, data in files.items()]}
        busy_msg = _select_record_busy_msg(store, admission_id)
        outcome = records.get("execution_outcome", {}).get(admission_id)
        if outcome is not None:
            if records["execution"][outcome["execution_id"]]["payload"] != execution:
                raise ResearchError("execution_identity_mismatch", "Recovered bytes differ from the already recorded actual outcome")
        else:
            from .probe_provenance import find_probe_artifact_origins
            observed_origin = {"_observation": observation} if find_probe_artifact_origins(
                records, [item["artifact"] for item in outputs], artifacts=artifacts) else {}
            retry_finishing_write(lambda: record_execution(store, execution, expected_revision=None,
                request_id="launcher-outcome-" + digest(claim), **observed_origin), busy_msg, kept=_name_launch(claim))
        retry_finishing_write(lambda: prepared_task_mutation(store, "execution.observe", {"admission_id": admission_id},
            lambda current, value: ([immutable_record(current, "execution_observation", admission_id, observation)], observation),
            request_id=request_id + ":observation"), busy_msg, kept=_name_launch(claim))
        # Rebuild familiar operational views even after a lost projection write.
        # Their bytes never supply execution authority.
        return _finish_reconciliation(store, payload, observation, request_id, claim, busy_msg)


def reconcile_execution(store, payload, *, expected_revision, request_id, owned=False):
    """Record the outcome of a claimed run: from its terminal outcome and its kept files, or, for a claim without a
    terminal outcome, from the resolution of the payload. The reconcile-run command gives the revision that it read;
    the launcher, which reconciles its own run, gives None."""
    fields(payload, ("admission_id",), ("resolution", "reason"))
    text(payload["admission_id"], "Admission ID")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise ResearchError("invalid_input", "Expected revision must be a nonnegative integer")
    if owned:
        return _reconcile(store, payload, expected_revision, request_id)
    with _owner(store.root, payload["admission_id"]):
        return _reconcile(store, payload, expected_revision, request_id)


def execution_status(store, admission_id):
    records = store.snapshot()["records"]
    return {"admission": records.get("execution_admission", {}).get(admission_id),
            "binding": records.get("execution_binding", {}).get(admission_id),
            "claim": records.get("execution_claim", {}).get(admission_id),
            "observation": records.get("execution_observation", {}).get(admission_id),
            "scientific_validation": False}


def find_recorded_execution(store, admission_id):
    """The id and outputs of the execution that the store records for the admitted run, or null for both."""
    records = store.snapshot()["records"]
    outcome = records.get("execution_outcome", {}).get(admission_id)
    if outcome is None:
        return {"execution_id": None, "outputs": None}
    execution = records["execution"][outcome["execution_id"]]["payload"]
    return {"execution_id": execution["id"], "outputs": execution["outputs"]}
