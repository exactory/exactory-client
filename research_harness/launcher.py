"""Detached foreground worker. It writes observations and never edits Store."""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

# Direct execution must not make this package's http.py shadow stdlib http.
sys.path[0] = str(Path(__file__).resolve().parents[1])
from research_harness.execution import _owner
from research_harness.storage import _canonical
from research_harness.workspace import json_projection, read_file, strict_json


def run_worker(directory, config_sha256, token, *, handshake=True):
    directory = Path(directory)
    root = directory.parents[2]
    relative = str(directory.relative_to(root))
    config = strict_json(read_file(root, relative + "/config.json"))
    if hashlib.sha256(_canonical(config).encode()).hexdigest() != config_sha256:
        raise ValueError("Frozen launcher configuration changed")
    with _owner(root, config["admission_id"], worker=True):
        json_projection(root, relative + "/ready.json", {"pid": os.getpid(), "token": token, "config_sha256": config_sha256})
        if handshake and sys.stdin.readline().strip() != token:
            return
        if config["runtime"]["python"] != sys.version.split()[0]:
            raise ValueError("Worker Python version differs from admission")
        if config["backend"] == "local" and hashlib.sha256(Path(config["runtime"]["path"]).read_bytes()).hexdigest() != config["runtime"]["sha256"]:
            raise ValueError("Worker interpreter differs from admission")
        for item in config["files"]:
            if hashlib.sha256(read_file(root, relative + "/work/" + item["path"])).hexdigest() != item["sha256"]:
                raise ValueError("Frozen program/input bytes changed")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        if config["seed"] is not None:
            env["EXACTORY_LAB_SEED"] = str(config["seed"])
        else:
            env.pop("EXACTORY_LAB_SEED", None)
        start = time.monotonic()
        status = "completed"
        with (directory / "stdout").open("wb") as stdout, (directory / "stderr").open("wb") as stderr:
            process = subprocess.Popen(config["argv"], cwd=directory / "work", env=env,
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                while process.poll() is None:
                    if time.monotonic() - start >= config["timeout_seconds"]:
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
        observed = {"status": status, "exit_code": process.returncode, "duration_s": time.monotonic() - start,
                    "binding_digest": config["binding_digest"], "config_sha256": config_sha256,
                    "actual_argv": config["argv"], "runtime": config["runtime"], "backend": config["backend"]}
        json_projection(root, relative + "/terminal.json", observed)


if __name__ == "__main__":
    run_worker(sys.argv[1], sys.argv[2], sys.argv[3])
