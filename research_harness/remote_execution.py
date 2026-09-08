"""Shared-folder compute transport with one durable claim and explicit release.

READY carries immutable bytes. A runner publishes its unique nonce and actual
interpreter before the client validates current admission and records GO. Only
that nonce may run. An unknown outcome is never resubmitted or called a timeout.
The folder mirror is transport, not an atomic distributed transaction.
"""

import hashlib
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .artifacts import _relative_parts
from .development import validate_admitted_execution
from .errors import ResearchError
from .evidence import digest
from .execution_outputs import output_paths, read_sealed_outputs, seal_outputs
from .operations import immutable_record, prepared_mutation
from .storage import _canonical
from .workspace import checked_parent, json_projection, read_file, strict_json, write_projection


_RUNNER_CLAIMS = {}


def transport_root():
    value = os.environ.get("EXACTORY_LAB_COLAB_DIR", "").strip()
    root = Path(value).expanduser().absolute() if value else None
    if root is None or not root.is_dir():
        raise ResearchError("execution_transport_required", "EXACTORY_LAB_COLAB_DIR must name an existing shared folder")
    with checked_parent(root, "RUNNER_ALIVE"):
        pass
    return root


def _interval(name, default):
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _exclusive(root, path, value):
    with checked_parent(root, path, create=True) as (directory, name):
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    return True


def _immutable(root, path, data):
    if (root / path).exists():
        if read_file(root, path) != data:
            raise ResearchError("execution_identity_mismatch", "A remote transport object changed", {"path": path})
    else:
        write_projection(root, path, data)


def _job(claim):
    return {"schema_version": 2, "job_id": digest(claim), "admission_id": claim["admission_id"],
            "config": claim["config"], "config_sha256": claim["config_artifact"]["sha256"],
            "node": Path(claim["config"]["script"]).stem, "script": claim["config"]["script"]}


def _publish_job(store, claim):
    from .execution import _directory
    root = Path(claim["config"]["transport"]["sync_root"])
    job = _job(claim)
    prefix = "jobs/" + job["job_id"]
    for item in job["config"]["files"]:
        data = read_file(store.root, _directory(claim["admission_id"]) + "/work/" + item["path"])
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ResearchError("execution_input_changed", "Frozen Colab input bytes changed")
        _immutable(root, prefix + "/" + item["path"], data)
    _immutable(root, prefix + "/job.json", _canonical(job).encode())
    _immutable(root, prefix + "/READY", digest(job).encode())
    return root, job


def _release(store, claim, root, job):
    from .artifacts import ArtifactStore
    from .execution import _binding, _files

    def authorize(current):
        admission = validate_admitted_execution(current, ArtifactStore(store.root), claim["admission_id"])
        binding = _binding(current, claim["admission_id"])
        if binding["digest"] != claim["binding_digest"]:
            raise ResearchError("execution_identity_mismatch", "The remote release differs from its durable binding")
        _files(store, admission, binding)

    records = store.snapshot()["records"]
    released = records.get("execution_remote_release", {}).get(claim["admission_id"])
    if released is None:
        claims_dir = root / "jobs" / job["job_id"] / "claims"
        if not claims_dir.is_dir():
            return None
        candidates = sorted(claims_dir.glob("*.json"))
        if not candidates:
            return None
        relative = str(candidates[0].relative_to(root))
        accepted = strict_json(read_file(root, relative))
        if (accepted.get("job_sha256") != digest(job) or accepted.get("runtime", {}).get("python") != job["config"]["runtime"]["python"]
                or accepted.get("worker_nonce") != candidates[0].stem):
            raise ResearchError("execution_runtime_changed", "The runner did not confirm the admitted job and exact Python version")
        value = {"admission_id": claim["admission_id"], "job_sha256": digest(job), "worker": accepted}
        def prepare(current, payload):
            authorize(current)
            return [immutable_record(current, "execution_remote_release", claim["admission_id"], value)], value
        released = prepared_mutation(store, "execution.remote-release", value, prepare,
            expected_revision=store.revision, request_id="remote-release-" + digest(claim))["result"]
    nonce = released["worker"]["worker_nonce"]
    prefix = "jobs/" + job["job_id"]
    go = prefix + "/GO-" + nonce
    if not (root / go).exists():
        started = prefix + "/STARTED-" + nonce
        if (root / started).exists():
            if strict_json(read_file(root, started)) != released["worker"]:
                raise ResearchError("execution_identity_mismatch", "The original start marker belongs to a different remote worker")
            return released
        if (root / "results" / job["job_id"] / "DONE").exists():
            # Collection validates the complete original result. It does not
            # need to issue another execution signal to recover known history.
            return released
        # A retained release is an immutable identity, not current permission
        # to emit a signal that may never have reached the original runner.
        current = store.snapshot()
        authorize(current["records"])
        revision = store.revision
        if revision != current["revision"]:
            raise ResearchError("stale_revision", "Research changed while checking a missing remote execution signal; retain the release and recheck before dispatch", {"expected_revision": current["revision"], "revision": revision})
    _immutable(root, go, _canonical(released).encode())
    return released


def launch_colab(store, claim):
    from .execution import reconcile_execution
    root, job = _publish_job(store, claim)
    deadline = time.monotonic() + claim["config"]["timeout_seconds"] + _interval("EXACTORY_LAB_COLAB_WAIT", 1800)
    while True:
        _release(store, claim, root, job)
        if (root / "results" / job["job_id"] / "DONE").exists():
            return reconcile_execution(store, {"admission_id": claim["admission_id"]}, expected_revision=store.revision,
                request_id="remote-observe-" + digest(claim), owned=True)
        if time.monotonic() >= deadline:
            raise ResearchError("execution_recovery_required", "The Colab runner has no confirmed terminal outcome. Check RUNNER_ALIVE heartbeat and reconcile the same admitted identity; its reservation remains charged.", {"job_id": job["job_id"]})
        time.sleep(max(0.01, _interval("EXACTORY_LAB_COLAB_POLL", 10)))


def collect_colab(store, claim):
    from .execution import _directory
    root, job = _publish_job(store, claim)
    released = _release(store, claim, root, job)
    prefix = "results/" + job["job_id"]
    if not (root / prefix / "DONE").exists():
        raise ResearchError("execution_recovery_required", "The Colab outcome is pending. Retain this job and reconcile; a remote transport timeout cannot establish interruption.")
    result = strict_json(read_file(root, prefix + "/manifest.json"))
    if (released is None or read_file(root, prefix + "/DONE").decode() != digest(result)
            or result.get("job_sha256") != digest(job) or result.get("worker") != released["worker"]):
        raise ResearchError("execution_identity_mismatch", "The Colab result differs from the only released worker and job")
    allowed_paths = set(output_paths(job["config"]))
    destination = _directory(claim["admission_id"])
    seen = set()
    for item in result["files"]:
        path = item["path"]
        if path not in allowed_paths or path in seen:
            raise ResearchError("execution_identity_mismatch", "Remote output names differ from the declared observation contract")
        seen.add(path)
        data = read_file(root, prefix + "/" + path)
        if hashlib.sha256(data).hexdigest() != item["sha256"] or len(data) != item["size"]:
            raise ResearchError("execution_identity_mismatch", "Remote output byte hashes do not match the terminal manifest")
        _immutable(store.root, destination + "/" + path, data)
    observed = result["terminal"]
    if observed.get("config_sha256") != job["config_sha256"] or observed.get("runtime") != released["worker"]["runtime"]:
        raise ResearchError("execution_identity_mismatch", "Remote terminal identity differs from admission")
    _immutable(store.root, destination + "/terminal.json", _canonical(observed).encode())


def _runner_runtime():
    path = Path(sys.executable).resolve()
    return {"path": str(path), "python": sys.version.split()[0], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _execute_job(root, job, worker):
    config = job["config"]
    nonce = worker["worker_nonce"]
    relative = "_work/" + job["job_id"] + "/" + nonce
    for item in config["files"]:
        _relative_parts(item["path"])
        data = read_file(root, "jobs/" + job["job_id"] + "/" + item["path"])
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ResearchError("execution_input_changed", "The remote program/input differs from the immutable claim")
        _immutable(root, relative + "/work/" + item["path"], data)
    for name in ("logs", "results", "plots"):
        with checked_parent(root, relative + "/work/" + name + "/.keep", create=True):
            pass
    # Map declared absolute file operands and the interpreter, retaining both
    # the original configuration digest and the actual remote argv.
    local_work = Path(config["argv"][1])
    for _ in Path(config["script"]).parts:
        local_work = local_work.parent
    replacements = {str(local_work / item["path"]): str(root / relative / "work" / item["path"]) for item in config["files"]}
    argv = [replacements.get(arg, arg) for arg in config["argv"]]
    argv[0] = worker["runtime"]["path"]
    if _runner_runtime() != worker["runtime"]:
        raise ResearchError("execution_runtime_changed", "The runner interpreter changed before launch")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    if config["seed"] is None:
        env.pop("EXACTORY_LAB_SEED", None)
    else:
        env["EXACTORY_LAB_SEED"] = str(config["seed"])
    status = "completed"
    started = time.monotonic()
    with (root / relative / "stdout").open("wb") as stdout, (root / relative / "stderr").open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=root / relative / "work", env=env, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while process.poll() is None:
                if time.monotonic() - started >= config["timeout_seconds"]:
                    status = "timed_out"
                    break
                if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > 32 * 1024 * 1024:
                    status = "partial"
                    break
                time.sleep(0.01)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                if status == "completed":
                    status = "partial"
            except ProcessLookupError:
                pass
            process.wait()
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
    if status == "completed" and process.returncode != 0:
        status = "failed"
    terminal = {"status": status, "exit_code": process.returncode, "duration_s": time.monotonic() - started,
                "binding_digest": config["binding_digest"], "config_sha256": job["config_sha256"],
                "actual_argv": argv, "runtime": worker["runtime"], "backend": "colab"}
    terminal["output_seal"] = seal_outputs(root, relative, config)
    contents = read_sealed_outputs(root, relative, config, terminal)
    files = []
    prefix = "results/" + job["job_id"]
    for path, data in contents.items():
        _immutable(root, prefix + "/" + path, data)
        files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    result = {"job_sha256": digest(job), "worker": worker, "terminal": terminal, "files": files}
    _immutable(root, prefix + "/manifest.json", _canonical(result).encode())
    _immutable(root, prefix + "/DONE", digest(result).encode())


def serve_scan_once(root):
    """Claim eligible jobs, then execute at most one locally owned released job."""
    root = Path(root).absolute()
    write_projection(root, "RUNNER_ALIVE", str(time.time()).encode())
    for ready in sorted((root / "jobs").glob("*/READY")):
        prefix = str(ready.parent.relative_to(root))
        job = strict_json(read_file(root, prefix + "/job.json"))
        if (job.get("schema_version") != 2 or job.get("job_id") != ready.parent.name
                or digest(job) != read_file(root, prefix + "/READY").decode()
                or hashlib.sha256(_canonical(job["config"]).encode()).hexdigest() != job["config_sha256"]):
            raise ResearchError("execution_binding_required", "Legacy or altered Colab jobs need a current immutable admission; they cannot execute")
        key = (str(root), job["job_id"])
        worker = _RUNNER_CLAIMS.get(key)
        if worker is None:
            worker = {"worker_nonce": uuid.uuid4().hex, "runtime": _runner_runtime(), "job_sha256": digest(job)}
            if not _exclusive(root, prefix + "/PROCESSED", worker):
                continue
            _RUNNER_CLAIMS[key] = worker
            _immutable(root, prefix + "/claims/" + worker["worker_nonce"] + ".json", _canonical(worker).encode())
        nonce = worker["worker_nonce"]
        go = root / prefix / ("GO-" + nonce)
        if not go.exists():
            continue
        release = strict_json(read_file(root, prefix + "/GO-" + nonce))
        if release != {"admission_id": job["admission_id"], "job_sha256": digest(job), "worker": worker}:
            raise ResearchError("execution_identity_mismatch", "The remote release does not authorize this runner")
        if not _exclusive(root, prefix + "/STARTED-" + nonce, worker):
            continue
        _execute_job(root, job, worker)
        return True
    return False
