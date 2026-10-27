"""Retain exploratory artifact identities through execution imports and references."""

import errno
import hashlib
import json
import os
import re
import stat

from .artifacts import ArtifactStore, describe_artifact, is_sealed_artifact
from .errors import ResearchError
from .operations import _read_preparation, fields, immutable_record, prepared_mutation, text
from .storage import Store
from .workspace import checked_parent, read_file, strict_json


def check_normalized_identity(digest, raw):
    """Authenticate the two lossless JSON encodings used by native exports."""
    try:
        value = strict_json(raw.decode("utf-8"))
        encodings = {hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=ascii_only, allow_nan=False).encode("utf-8")).hexdigest()
            for ascii_only in (False, True)}
    except (UnicodeError, ValueError, RecursionError, ResearchError) as error:
        raise ResearchError("invalid_import_identity", "Original import bytes must be finite, unambiguous UTF-8 JSON") from error
    if digest not in encodings:
        raise ResearchError("invalid_import_identity", "Original import bytes do not establish their canonical identity")
    return encodings


def read_import_identity(identity, artifacts):
    """Check an append-only common alias against its immutable original bytes."""
    fields(identity, ("schema_version", "canonical_digest", "raw"), code="invalid_import_identity")
    if type(identity["schema_version"]) is not int or identity["schema_version"] != 1:
        raise ResearchError("invalid_import_identity", "Expected import identity schema version 1")
    if artifacts is None:
        raise ResearchError("invalid_import_identity", "Resolving normalized identities requires their original artifacts")
    raw = artifacts.read(identity["raw"])
    check_normalized_identity(identity["canonical_digest"], raw)
    return raw


def record_probe_artifact_origin(root, probe_id, sha256, role):
    """Commit an exploratory identity before its captured bytes become importable."""
    text(probe_id, "Probe ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", probe_id):
        raise ResearchError("invalid_input", "Probe artifact origin needs a safe probe ID")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ResearchError("invalid_input", "Probe artifact origin needs a lowercase SHA-256 identity")
    if role not in ("outputs", "stdout", "stderr"):
        raise ResearchError("invalid_input", "Probe artifact role must be outputs, stdout or stderr")
    store = Store(root)
    payload = {"probe_id": probe_id, "sha256": sha256, "roles": [role]}
    identifier = probe_id + ":" + sha256 + ":" + role
    def prepare(records, value):
        if probe_id not in records.get("probe_intent", {}):
            raise ResearchError("probe_intent_missing", "Pin exploratory output only after its immutable probe intent")
        return [immutable_record(records, "probe_artifact", identifier, value)], value
    while True:
        try:
            return prepared_mutation(store, "research.probe_artifact", payload, prepare,
                expected_revision=store.revision, request_id="probe-artifact:" + identifier)
        except ResearchError as error:
            if error.code != "stale_revision":
                raise


def pin_probe_artifact(root, probe_id, data, media_type, role, *, sealed=False):
    """Register provenance durably before publishing a content-addressed capture."""
    descriptor = describe_artifact(data, media_type)
    record_probe_artifact_origin(root, probe_id, descriptor["sha256"], role)
    artifacts = ArtifactStore(root)
    return (artifacts.put_sealed if sealed else artifacts.put)(data, media_type)


def find_probe_path_bindings(records, path):
    """Locate captured paths without reading bytes or classifying copied inputs."""
    bindings = []
    if not isinstance(path, str):
        return bindings
    for launch in records.get("probe_launch", {}).values():
        work = launch["directory"] + "/work/"
        role = next((name for name in ("stdout", "stderr") if path == launch["directory"] + "/" + name), None)
        original = None
        original_mtime = None
        if role is None and path.startswith(work):
            name = path[len(work):]
            if launch["output_dir"] == "." or name.startswith(launch["output_dir"] + "/"):
                role = "outputs"
                initial = launch.get("initial", {}).get(name, {})
                original, original_mtime = initial.get("sha256"), initial.get("mtime_ns")
        if role is not None:
            bindings.append({"probe_id": launch["id"], "role": role, "original_sha256": original,
                             "original_mtime_ns": original_mtime})
    return bindings


def should_stage_probe_artifact_pin(records, payload, request_id):
    """Resume a reserved pin or freeze a path belonging to an existing launch."""
    return (isinstance(request_id, str) and request_id in records.get("probe_artifact_pin", {})
            or isinstance(payload, dict) and (is_sealed_artifact(payload)
                or bool(find_probe_path_bindings(records, payload.get("path")))))


def read_probe_pin_source(root, relative):
    """Freeze bytes and their observed timestamp through one checked descriptor."""
    try:
        with checked_parent(root, relative) as (directory, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise ResearchError("unsafe_path", "Probe pin sources must be regular files")
            with os.fdopen(descriptor, "rb") as source:
                data = source.read()
                return data, os.fstat(source.fileno()).st_mtime_ns
    except FileNotFoundError as error:
        raise ResearchError("artifact_missing", "Required workspace file is missing", {"path": relative}) from error
    except OSError as error:
        code = "unsafe_path" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "storage_io"
        raise ResearchError(code, "Probe pin source could not be read", {"errno": error.errno}) from error


def pin_probe_local_artifact(store, payload, *, expected_revision, request_id, _snapshot=None):
    """Publish one frozen local pin after its provenance, and recover its receipt."""
    snapshot = _read_preparation(store, _snapshot)
    if snapshot.has_request(request_id):
        return store.mutate("workspace.artifact", payload, lambda transaction: None,
                            expected_revision=expected_revision, request_id=request_id, _preparation=snapshot)
    artifacts = ArtifactStore(store.root)
    frozen = {}

    def prepare_intent(records, value):
        fields(value, ("id", "path", "media_type"))
        text(value["id"], "Local artifact ID")
        if is_sealed_artifact(value):
            raise ResearchError("artifact_sealed", "Completion-check outputs are sealed unread")
        data, source_mtime_ns = read_probe_pin_source(store.root, value["path"])
        artifact = describe_artifact(data, value["media_type"])
        # Validate the eventual record before reserving this request identity.
        immutable_record(records, "local_artifact", value["id"], dict(value, artifact=artifact, scientific_validation=False))
        origins = {}
        for binding in find_probe_path_bindings(records, value["path"]):
            if (binding["original_sha256"] != artifact["sha256"]
                    or binding["original_mtime_ns"] is not None and binding["original_mtime_ns"] != source_mtime_ns):
                if binding["probe_id"] not in records.get("probe_intent", {}):
                    raise ResearchError("probe_intent_missing", "Retain the probe intent before pinning its captured files")
                context = records["probe_intent"][binding["probe_id"]].get("program_run")
                if context is not None and not context["read_outputs"]:
                    raise ResearchError("artifact_sealed", "Completion-check outputs are sealed unread")
                origins.setdefault(binding["probe_id"], set()).add(binding["role"])
        origins = [{"probe_id": identifier, "sha256": artifact["sha256"], "roles": sorted(roles)}
                   for identifier, roles in sorted(origins.items())]
        intent = {"payload": value, "artifact": artifact, "origins": origins, "source_mtime_ns": source_mtime_ns}
        changes = [immutable_record(records, "probe_artifact_pin", request_id, intent)]
        for origin in origins:
            for role in origin["roles"]:
                identifier = origin["probe_id"] + ":" + origin["sha256"] + ":" + role
                changes.append(immutable_record(records, "probe_artifact", identifier,
                    dict(origin, roles=[role])))
        frozen["data"] = data
        return changes, intent

    intent = prepared_mutation(store, "workspace.probe_artifact_intent", payload, prepare_intent,
        expected_revision=expected_revision, request_id=request_id + ":probe-origin", _snapshot=snapshot)["result"]
    if "data" in frozen:
        data = frozen["data"]
    else:
        try:
            data = artifacts.read(intent["artifact"])
        except ResearchError as error:
            if error.code != "artifact_missing":
                raise
            try:
                data = read_file(store.root, intent["payload"]["path"])
            except ResearchError as missing:
                if missing.code != "artifact_missing":
                    raise
                raise ResearchError("probe_artifact_pin_conflict", "The original frozen pin bytes are unavailable") from missing
            if describe_artifact(data, intent["payload"]["media_type"]) != intent["artifact"]:
                raise ResearchError("probe_artifact_pin_conflict", "The source changed before its frozen probe pin was published")
    artifact = artifacts.put(data, intent["payload"]["media_type"])

    def prepare_record(records, value):
        if records.get("probe_artifact_pin", {}).get(request_id) != intent or artifact != intent["artifact"]:
            raise ResearchError("probe_artifact_pin_conflict", "Finish the exact original frozen probe pin")
        record = dict(value, artifact=artifact, scientific_validation=False)
        return [immutable_record(records, "local_artifact", value["id"], record)], record

    while True:
        try:
            return prepared_mutation(store, "workspace.artifact", intent["payload"], prepare_record,
                expected_revision=store.revision, request_id=request_id)
        except ResearchError as error:
            if error.code != "stale_revision":
                raise


def find_probe_artifact_origins(records, descriptors, *, artifacts=None):
    """Resolve checked aliases and bind each origin to the requested output SHA.

    Exact identity queries need no artifact access. Normalization aliases always
    require checked original bytes, including when their probe is recorded later.
    """
    aliases = {}
    for identity in records.get("native_import_identity", {}).values():
        aliases.setdefault(identity["canonical_digest"], []).append(identity)
    requested = {item["sha256"] for item in descriptors}
    references = {identity: {identity} for identity in requested}
    pending = [(identity, identity) for identity in sorted(requested)]
    checked = {}
    count = len(pending)
    while pending:
        output, current = pending.pop()
        if count > 10000:
            raise ResearchError("dependency_cycle", "Probe identity closure is excessive")
        if current not in checked:
            checked[current] = []
            for alias in aliases.get(current, []):
                read_import_identity(alias, artifacts)
                checked[current].append(alias["raw"]["sha256"])
        for original in checked[current]:
            outputs = references.setdefault(original, set())
            if output not in outputs:
                outputs.add(output)
                pending.append((output, original))
                count += 1
    origins = []
    for origin in match_probe_artifact_origins(records, [{"sha256": identity} for identity in references]):
        for output in sorted(references[origin["sha256"]]):
            row = dict(origin, sha256=output)
            if output != origin["sha256"]:
                row["raw_sha256"] = origin["sha256"]
            origins.append(row)
    return sorted(origins, key=lambda row: (row["probe_id"], row["sha256"], row.get("raw_sha256", "")))


def match_probe_artifact_origins(records, descriptors):
    """Match exact identities after the caller has authenticated its closure."""
    identities = {item["sha256"] for item in descriptors}
    origins = {}
    for identifier, probe in sorted(records.get("probe", {}).items()):
        for role in ("outputs", "stdout", "stderr"):
            values = probe.get(role, []) if role == "outputs" else [probe.get(role)]
            for descriptor in values:
                if descriptor is not None and descriptor["sha256"] in identities:
                    origins.setdefault((identifier, descriptor["sha256"]), set()).add(role)
    for origin in records.get("probe_artifact", {}).values():
        if origin["sha256"] in identities:
            origins.setdefault((origin["probe_id"], origin["sha256"]), set()).update(origin["roles"])
    for pinned in records.get("local_artifact", {}).values():
        identity = pinned["artifact"]["sha256"]
        if identity in identities:
            for binding in find_probe_path_bindings(records, pinned["path"]):
                if binding["original_sha256"] != identity:
                    origins.setdefault((binding["probe_id"], identity), set()).add(binding["role"])
    return [{"probe_id": identifier, "sha256": identity, "roles": sorted(roles)}
            for (identifier, identity), roles in sorted(origins.items())]
