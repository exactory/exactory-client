"""A probe owner survives a lost launcher and leaves durable terminal evidence."""

import hashlib
import mimetypes
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

from .artifacts import describe_artifact
from .errors import ResearchError
from .probe_provenance import pin_probe_artifact, record_probe_artifact_origin
from .workspace import checked_parent, json_projection, read_file, strict_json


STREAM_LIMIT = 32 * 1024 * 1024


def read_probe_log(root, relative):
    with checked_parent(root, relative) as (directory, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ResearchError("unsafe_path", "Probe logs must be regular files")
            data = stream.read(STREAM_LIMIT + 1)
    return data[:STREAM_LIMIT], len(data) > STREAM_LIMIT


def find_probe_stream_origins(root, launch):
    origins = {}
    for role in ("stdout", "stderr"):
        try:
            with checked_parent(root, launch["directory"] + "/" + role) as (directory, name):
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ResearchError("unsafe_path", "Probe logs must be regular files")
                    sha256 = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        sha256.update(block)
        except FileNotFoundError:
            continue
        origins.setdefault(sha256.hexdigest(), []).append(role)
    return [{"probe_id": launch["id"], "sha256": sha256, "roles": roles} for sha256, roles in sorted(origins.items())]


def run_probe_process(root, config, owner):
    directory = root / config["directory"]
    started = time.monotonic()
    exit_status = "failed"
    with (directory / "stdout").open("xb") as stdout, (directory / "stderr").open("xb") as stderr:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        process = subprocess.Popen(config["argv"], cwd=directory / "work", env=env, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True, pass_fds=(owner,))
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                json_projection(root, config["directory"] + "/usage.json", {"elapsed_seconds": elapsed})
                if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > STREAM_LIMIT:
                    exit_status = "output_bound"
                    break
                if elapsed >= config["timeout_seconds"]:
                    exit_status = "timed_out"
                    break
                time.sleep(0.01)
            else:
                exit_status = process.returncode
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                if process.poll() is None:
                    process.kill()
            process.wait()
        if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > STREAM_LIMIT:
            exit_status = "output_bound"
    return {"exit_status": exit_status, "elapsed_seconds": time.monotonic() - started}


def capture_probe_outcome(root, launch, terminal):
    for origin in find_probe_stream_origins(root, launch):
        for role in origin["roles"]:
            record_probe_artifact_origin(root, launch["id"], origin["sha256"], role)
    status = terminal["exit_status"]
    observation = launch.get("observation") or ("Local probe exited with status " + str(status) + ".")
    logs = {}
    for name in ("stdout", "stderr"):
        try:
            data, exceeded = read_probe_log(root, launch["directory"] + "/" + name)
        except FileNotFoundError:
            data, exceeded = b"", False
        logs[name] = pin_probe_artifact(root, launch["id"], data, "text/plain", name)
        if exceeded:
            status = "output_bound"
            observation = "Probe output exceeded 32 MiB; the raw file and a bounded artifact prefix are retained."
    outputs = []
    relative = launch["directory"] + "/work"
    suffix = "" if launch["output_dir"] == "." else "/" + launch["output_dir"]
    try:
        with checked_parent(root, relative + suffix + "/.probe-directory"):
            pass
        for path in sorted((root / relative / launch["output_dir"]).rglob("*")):
            if path.is_symlink():
                raise ResearchError("unsafe_path", "Probe outputs must be regular files under their output directory")
            if path.is_dir():
                continue
            name = path.relative_to(root / relative).as_posix()
            data = read_file(root, relative + "/" + name)
            media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            artifact = describe_artifact(data, media_type)
            initial = launch["initial"].get(name)
            if initial is None or (path.stat().st_mtime_ns, artifact["sha256"]) != (initial["mtime_ns"], initial["sha256"]):
                outputs.append(pin_probe_artifact(root, launch["id"], data, media_type, "outputs"))
    except (ResearchError, OSError) as error:
        status = "failed" if status != "output_bound" else status
        observation = "Probe output capture failed: " + (error.code if isinstance(error, ResearchError) else str(error))
    return {"id": launch["id"], "command": launch["command"], "outputs": outputs, **logs,
            "exit_status": status, "elapsed_seconds": terminal["elapsed_seconds"], "observation": observation}


def run_probe_worker(root, directory, config_sha256, owner):
    data = read_file(root, directory + "/config.json")
    if hashlib.sha256(data).hexdigest() != config_sha256:
        raise ResearchError("probe_evidence_mismatch", "Probe configuration differs from its immutable launch")
    config = strict_json(data)
    json_projection(root, directory + "/worker.json", {"pid": os.getpid(), "config_sha256": config_sha256})
    started = time.monotonic()
    try:
        terminal = run_probe_process(root, config, owner)
    except (ResearchError, OSError) as error:
        terminal = {"exit_status": "failed", "elapsed_seconds": time.monotonic() - started,
                    "error": error.as_dict() if isinstance(error, ResearchError) else {"message": str(error)}}
    terminal["config_sha256"] = config_sha256
    json_projection(root, directory + "/terminal.json", terminal)
    outcome = capture_probe_outcome(root, config, terminal)
    json_projection(root, directory + "/outcome.json", {"config_sha256": config_sha256, "outcome": outcome})


if __name__ == "__main__":
    run_probe_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]))
