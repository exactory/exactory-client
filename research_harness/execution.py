"""Pinned one-time local/remote execution and recovery over domain admissions.

Lock order is execution-owner lock, then a short research Store operation. The
worker owns a different run lock and never opens Store. A claimed run is never
relaunched. A lost launcher response stays pending until its actual outcome is
reconciled, retaining every reservation.
"""

from contextlib import contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .artifacts import ArtifactStore, _relative_parts
from .development import record_execution, validate_admitted_execution
from .errors import ResearchError
from .evidence import digest
from .execution_outputs import log_bytes, output_metric, output_path, read_sealed_outputs, seal_outputs
from .operations import fields, immutable_record, prepared_mutation, text
from .storage import _canonical
from .workspace import checked_parent, json_projection, read_file, strict_json, write_projection


def _directory(admission_id):
    return "research/runs/" + digest({"admission_id": admission_id})


def record_imported_execution(store, payload, *, expected_revision, request_id):
    """The public result command imports history; managed results are observed."""
    origin = payload.get("origin") if isinstance(payload, dict) else None
    if not isinstance(origin, dict) or origin.get("kind") != "imported":
        raise ResearchError("managed_result_requires_reconciliation", "Use exactory-lab run or exactory-research reconcile-run for a managed outcome; result imports explicitly attributed execution history")
    return record_execution(store, payload, expected_revision=expected_revision, request_id=request_id)


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
    return {"path": str(Path(sys.executable).resolve()), "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "python": sys.version.split()[0]}


def _files(store, admission, binding, *, current=True):
    artifacts = ArtifactStore(store.root)
    pairs = [(binding["script"], admission["command"]["program"])] + [(v["path"], v["artifact"]) for v in binding["inputs"]]
    for path, artifact in pairs:
        data = artifacts.read(artifact)
        if current and read_file(store.root, "experiment/" + path) != data:
            raise ResearchError("execution_input_changed", "The declared program or input changed after admission", {"path": path})
    return pairs


def bind_execution(store, payload, *, expected_revision, request_id):
    """Bind admitted bytes to backend, paths, timeout and observed output names."""
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, ("admission_id", "script", "backend", "timeout_seconds", "inputs", "outputs", "usage_unit"))
        admission = validate_admitted_execution(records, artifacts, value["admission_id"])
        _relative_parts(value["script"])
        if value["backend"] not in ("local", "colab"):
            raise ResearchError("invalid_execution", "Backend must be local or colab")
        timeout = value["timeout_seconds"]
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ResearchError("invalid_execution", "A finite positive timeout is required")
        runtime = _runtime(admission["command"], value["backend"])
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
        requirements = {item["id"] for item in plan["evidence_requirements"]}
        output_ids = set()
        output_paths = set()
        for output in value["outputs"]:
            fields(output, ("id", "requirement_id", "path", "media_type"))
            text(output["id"], "Output ID")
            _relative_parts(output["path"])
            if output["id"] in output_ids or output["path"] in output_paths or output["requirement_id"] not in requirements:
                raise ResearchError("invalid_execution", "Each unique output must name a planned evidence requirement")
            if output["path"] in paths:
                raise ResearchError("invalid_execution", "An output cannot replace a frozen input")
            output_ids.add(output["id"])
            output_paths.add(output["path"])
            artifacts.put(b"", output["media_type"])
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


def _materialize(store, admission, binding):
    directory = _directory(admission["id"])
    pairs = _files(store, admission, binding)
    for relative, artifact in pairs:
        path = directory + "/work/" + relative
        write_projection(store.root, path, ArtifactStore(store.root).read(artifact))
        (store.root / path).chmod(0o444)
    for name in ("logs", "results", "plots"):
        with checked_parent(store.root, directory + "/work/" + name + "/.keep", create=True):
            pass
    replacements = {str(store.root / "experiment" / p): str(store.root / directory / "work" / p) for p, _ in pairs}
    argv = [replacements.get(str(Path(arg).resolve()), arg) if Path(arg).is_absolute() else arg
            for arg in admission["command"]["argv"]]
    argv[1] = str(store.root / directory / "work" / binding["script"])
    argv[0] = binding["runtime"]["path"]
    config = {"admission_id": admission["id"], "binding_digest": binding["digest"], "argv": argv,
              "script": binding["script"], "files": [{"path": p, "sha256": a["sha256"]} for p, a in pairs],
              "timeout_seconds": binding["timeout_seconds"], "seed": admission["command"]["seed"],
              "runtime": binding["runtime"], "backend": binding["backend"],
              "outputs": binding["outputs"]}
    if binding["backend"] == "colab":
        config["transport"] = binding["transport"]
    return config


def _claim(store, admission_id, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        if admission_id in records.get("execution_claim", {}):
            raise ResearchError("execution_recovery_required", "This admitted run was already claimed; reconcile it instead of relaunching")
        admission = validate_admitted_execution(records, artifacts, admission_id)
        binding = _binding(records, admission_id)
        if _runtime(admission["command"], binding["backend"]) != binding["runtime"]:
            raise ResearchError("execution_runtime_changed", "The admitted interpreter bytes changed")
        config = _materialize(store, admission, binding)
        claim = {"admission_id": admission_id, "binding_digest": binding["digest"], "config": config,
                 "config_artifact": artifacts.put(_canonical(config).encode(), "application/json"),
                 "request_id": request_id, "claimed_revision": expected_revision + 1, "token": uuid.uuid4().hex}
        return [immutable_record(records, "execution_claim", admission_id, claim)], claim
    return prepared_mutation(store, "execution.claim", {"admission_id": admission_id}, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def _prelaunch(store, admission_id, claim):
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        admission = validate_admitted_execution(records, artifacts, admission_id)
        binding = _binding(records, admission_id)
        if binding["digest"] != claim["binding_digest"]:
            raise ResearchError("execution_identity_mismatch", "The run binding differs from the durable claim")
        _files(store, admission, binding)
        result = {"admission_id": admission_id, "binding_digest": binding["digest"], "revision": store_revision}
        return [("execution_prelaunch", admission_id, result)], result
    store_revision = store.revision
    return prepared_mutation(store, "execution.prelaunch", {"admission_id": admission_id, "claim": claim["config_artifact"]["sha256"]},
        prepare, expected_revision=store_revision, request_id="prelaunch-" + digest(claim))


def launch_execution(store, admission_id, *, expected_revision, request_id):
    with _owner(store.root, admission_id):
        before = store.snapshot()["records"]
        old = before.get("execution_claim", {}).get(admission_id)
        if old is not None:
            if old["request_id"] != request_id:
                raise ResearchError("execution_recovery_required", "An admitted identity is launched once; reconcile the original run")
            store.mutate("execution.claim", {"admission_id": admission_id}, lambda tx: None,
                         expected_revision=expected_revision, request_id=request_id)
            reconciliation_id = ("remote-observe-" + digest(old) if old["config"]["backend"] == "colab"
                                 else "reconcile-" + request_id)
            return reconcile_execution(store, {"admission_id": admission_id}, expected_revision=store.revision,
                                       request_id=reconciliation_id, owned=True)
        claim = _claim(store, admission_id, expected_revision, request_id)["result"]
        if claim["config"]["backend"] == "colab":
            from .remote_execution import launch_colab
            return launch_colab(store, claim)
        directory = store.root / _directory(admission_id)
        json_projection(store.root, _directory(admission_id) + "/config.json", claim["config"])
        with (directory / "launcher.log").open("wb") as diagnostics:
            worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name("launcher.py")),
                str(directory), claim["config_artifact"]["sha256"], claim["token"]],
                stdin=subprocess.PIPE, stdout=diagnostics, stderr=diagnostics, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not (directory / "ready.json").exists() and worker.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            if not (directory / "ready.json").exists():
                raise ResearchError("execution_recovery_required", "The launcher outcome is unknown; reconcile the claimed identity")
            ready = strict_json(read_file(store.root, _directory(admission_id) + "/ready.json"))
            if ready != {"pid": worker.pid, "token": claim["token"], "config_sha256": claim["config_artifact"]["sha256"]}:
                raise ResearchError("execution_identity_mismatch", "The launcher did not establish the claimed identity")
            _prelaunch(store, admission_id, claim)
            worker.stdin.write((claim["token"] + "\n").encode())
            worker.stdin.flush()
            worker.stdin.close()
            worker.wait(timeout=claim["config"]["timeout_seconds"] + 10)
        finally:
            if not worker.stdin.closed:
                worker.stdin.close()
            if worker.poll() is None:
                try:
                    worker.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    # Do not relabel a running or unobserved producer as failed.
                    raise ResearchError("execution_recovery_required", "The worker is still live or its outcome is unknown")
        return reconcile_execution(store, {"admission_id": admission_id}, expected_revision=store.revision,
                                   request_id="reconcile-" + request_id, owned=True)


def _finish_reconciliation(store, payload, observation, expected_revision, request_id):
    receipt = store.mutate("execution.reconcile", payload, lambda tx: observation["summary"],
                           expected_revision=expected_revision, request_id=request_id)
    summary = observation["summary"]
    directory = _directory(payload["admission_id"])
    json_projection(store.root, directory + "/summary.json", summary)
    json_projection(store.root, "experiment/results/" + summary["node"] + ".json", summary)
    if observation.get("log") is not None:
        write_projection(store.root, "experiment/logs/" + summary["node"] + ".log", ArtifactStore(store.root).read(observation["log"]))
    return receipt["result"]


def _reconcile(store, payload, expected_revision, request_id):
    fields(payload, ("admission_id",), ("resolution", "reason"))
    admission_id = payload["admission_id"]
    records = store.snapshot()["records"]
    claim = records.get("execution_claim", {}).get(admission_id)
    if claim is None:
        raise ResearchError("execution_not_claimed", "No launcher claimed this admitted run")
    prior = records.get("execution_observation", {}).get(admission_id)
    if prior is not None:
        return _finish_reconciliation(store, payload, prior, expected_revision, request_id)
    if store.revision != expected_revision:
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
                raise ResearchError("execution_recovery_required", "No terminal outcome is available; preserve the claim and reconcile. A dead local owner may be recorded interrupted with a reason.")
            observed = {"status": "interrupted", "exit_code": None, "duration_s": None, "binding_digest": binding["digest"],
                        "config_sha256": claim["config_artifact"]["sha256"], "reason": payload["reason"]}
            observed["output_seal"] = seal_outputs(store.root, directory, claim["config"], origin="recovery")
        else:
            observed = strict_json(read_file(store.root, directory + "/terminal.json"))
        if observed.get("binding_digest") != binding["digest"] or observed.get("config_sha256") != claim["config_artifact"]["sha256"]:
            raise ResearchError("execution_identity_mismatch", "The observed worker output belongs to a different frozen run")
        artifacts = ArtifactStore(store.root)
        files = read_sealed_outputs(store.root, directory, claim["config"], observed)
        if terminal.exists() and observed["output_seal"]["origin"] != "worker":
            raise ResearchError("execution_output_seal_required", "A producer terminal requires its original worker output seal")
        outputs = []
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
        outcome = records.get("execution_outcome", {}).get(admission_id)
        if outcome is not None:
            if records["execution"][outcome["execution_id"]]["payload"] != execution:
                raise ResearchError("execution_identity_mismatch", "Recovered bytes differ from the already recorded actual outcome")
        else:
            record_execution(store, execution, expected_revision=store.revision, request_id="launcher-outcome-" + digest(claim))
        observation = {"admission_id": admission_id, "claim_digest": digest(claim), "binding_digest": binding["digest"],
                       "execution": execution,
                       "terminal": artifacts.put(_canonical(observed).encode(), "application/json"), "log": log_ref, "summary": summary,
                       "files": [{"path": path, "artifact": artifacts.put(data, "application/octet-stream")} for path, data in files.items()]}
        prepared_mutation(store, "execution.observe", {"admission_id": admission_id},
            lambda current, value: ([immutable_record(current, "execution_observation", admission_id, observation)], observation),
            expected_revision=store.revision, request_id=request_id + ":observation")
        # Rebuild familiar operational views even after a lost projection write.
        # Their bytes never supply execution authority.
        return _finish_reconciliation(store, payload, observation, store.revision, request_id)


def reconcile_execution(store, payload, *, expected_revision, request_id, owned=False):
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
