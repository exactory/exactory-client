"""Bind terminal output inventory to exact worker bytes before reconciliation."""

import hashlib
import os
from pathlib import Path

from .artifacts import _regular_file
from .errors import ResearchError
from .workspace import checked_parent, read_file, strict_json


def output_path(path):
    return path if path in ("stdout", "stderr") else "work/" + path


def output_paths(config):
    paths = {"stdout", "stderr", "work/results/" + Path(config["script"]).stem + ".json"}
    paths.update(output_path(item["path"]) for item in config["outputs"])
    return sorted(paths)


def seal_outputs(root, directory, config, *, origin="worker"):
    files = []
    for path in output_paths(config):
        try:
            with checked_parent(root, directory + "/" + path) as (parent, name):
                with os.fdopen(_regular_file(parent, name), "rb") as stream:
                    data = stream.read()
                    os.fsync(stream.fileno())
                os.fsync(parent)
        except FileNotFoundError:
            data = None
        except ResearchError as error:
            if error.code != "artifact_missing":
                raise
            data = None
        files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
                      "size": len(data) if data is not None else None})
    return {"schema_version": 1, "origin": origin, "files": files}


def _inventory(config, terminal):
    seal = terminal.get("output_seal")
    if not isinstance(seal, dict) or type(seal.get("schema_version")) is not int or seal["schema_version"] != 1:
        raise ResearchError("execution_output_seal_required", "The original worker did not seal its terminal output bytes. Preserve historical receipts; these outputs cannot establish a new managed observation or current readiness")
    files = seal.get("files")
    if (seal.get("origin") not in ("worker", "recovery") or not isinstance(files, list)
            or any(not isinstance(item, dict) or set(item) != {"path", "sha256", "size"} for item in files)
            or [item["path"] for item in files] != output_paths(config)):
        raise ResearchError("execution_output_mismatch", "The terminal inventory differs from the complete declared output and stream contract")
    for item in files:
        if item["sha256"] is None and item["size"] is None:
            continue
        if (not isinstance(item["sha256"], str) or len(item["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in item["sha256"])
                or type(item["size"]) is not int or item["size"] < 0):
            raise ResearchError("execution_output_mismatch", "A terminal output needs its exact hash and size or an explicit absence")
    return {item["path"]: item for item in files}


def _matches(item, data):
    if data is None:
        return item["sha256"] is None and item["size"] is None
    return item["size"] == len(data) and item["sha256"] == hashlib.sha256(data).hexdigest()


def read_sealed_outputs(root, directory, config, terminal):
    files = {}
    for path, item in _inventory(config, terminal).items():
        try:
            data = read_file(root, directory + "/" + path)
        except ResearchError as error:
            if error.code != "artifact_missing":
                raise
            data = None
        if not _matches(item, data):
            raise ResearchError("execution_output_mismatch", "An output was changed, removed or inserted after the worker sealed its terminal outcome", {"path": path})
        if data is not None:
            files[path] = data
    return files


def log_bytes(files):
    return b"----- STDOUT -----\n" + files.get("stdout", b"") + b"\n----- STDERR -----\n" + files.get("stderr", b"")


def output_metric(config, files):
    result = None
    for line in files.get("stdout", b"").decode("utf-8", "replace").splitlines():
        try:
            value = strict_json(line)
        except ResearchError:
            continue
        if isinstance(value, dict) and "metric" in value:
            result = value
    fallback = "work/results/" + Path(config["script"]).stem + ".json"
    if result is None and fallback in files:
        result = strict_json(files[fallback])
    return result


def validate_observed_outputs(artifacts, config, terminal, observation, execution):
    inventory = _inventory(config, terminal)
    if terminal["output_seal"]["origin"] != "worker":
        raise ResearchError("execution_output_seal_required", "Recovered partial bytes are historical observations, not a worker's completed output seal")
    expected = [path for path, item in inventory.items() if item["sha256"] is not None]
    captured = observation.get("files")
    if (not isinstance(captured, list)
            or any(not isinstance(item, dict) or set(item) != {"path", "artifact"} for item in captured)
            or [item["path"] for item in captured] != expected):
        raise ResearchError("execution_output_mismatch", "The recorded artifact inventory differs from the worker's terminal seal")
    files = {item["path"]: artifacts.read(item["artifact"]) for item in captured}
    if any(not _matches(inventory[path], data) for path, data in files.items()):
        raise ResearchError("execution_output_mismatch", "Recorded output bytes differ from the worker's terminal seal")
    declared = [item for item in config["outputs"] if output_path(item["path"]) in files]
    if len(execution["outputs"]) != len(declared):
        raise ResearchError("execution_output_mismatch", "The managed outcome changed the observed output inventory")
    for output, item in zip(execution["outputs"], declared):
        if (output["id"] != item["id"] or output["requirement_id"] != item["requirement_id"]
                or output["artifact"]["media_type"] != item["media_type"]
                or artifacts.read(output["artifact"]) != files[output_path(item["path"])]):
            raise ResearchError("execution_output_mismatch", "The managed result differs from its declared worker output")
    if (artifacts.read(observation["log"]) != log_bytes(files)
            or observation["summary"]["metric"] != output_metric(config, files)):
        raise ResearchError("execution_output_mismatch", "The recorded log or metric differs from the worker's sealed streams and fallback")
