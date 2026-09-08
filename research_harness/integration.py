"""Atomic workspace contracts and supported legacy adoption.

SQLite is the research authority. Human-readable files are projections. Native
mathematical budgets and proof acceptance remain owned by the native controller.
"""

import copy
from datetime import datetime, timezone
from pathlib import Path
import uuid

from .artifacts import ArtifactStore
from .errors import ResearchError
from .evidence import digest
from .gates import STAGES, gate_state, validate_transition
from .operations import fields, immutable_record, prepared_mutation, strings, text
from .principles import prepare_initialization
from .storage import Store
from .workspace import json_projection, read_file, strict_json, write_projection


def now():
    return datetime.now(timezone.utc).isoformat()


def current_store(root):
    try:
        store = Store(root)
    except ResearchError as error:
        if error.code == "store_missing":
            raise ResearchError("migration_required", "Research readiness requires explicit exactory-research adopt") from error
        raise
    if "research" not in store.snapshot()["records"].get("configuration", {}):
        raise ResearchError("migration_required", "Research readiness requires initialization or explicit adoption")
    return store


def pin_artifact(store, payload, *, expected_revision, request_id):
    """Save authored program, review or provenance bytes without result credit."""
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, ("id", "path", "media_type"))
        text(value["id"], "Local artifact ID")
        artifact = artifacts.put(read_file(store.root, value["path"]), value["media_type"])
        record = dict(value, artifact=artifact, scientific_validation=False)
        return [immutable_record(records, "local_artifact", value["id"], record)], record
    return prepared_mutation(store, "workspace.artifact", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def initialize_workspace(store, payload, *, expected_revision, request_id):
    """{kind: study|draft, state: initial marker}; attach the current contract."""
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, ("kind", "state"))
        kind = value["kind"]
        if kind not in ("study", "draft") or not isinstance(value["state"], dict):
            raise ResearchError("invalid_workspace", "Expected study or draft initial state")
        if kind in records.get("workspace", {}):
            raise ResearchError("workspace_exists", "Workspace already exists")
        changes = []
        config = records.get("configuration", {}).get("research")
        if config is None:
            changes, config = prepare_initialization(records, artifacts, {"profile": "research", "target": None})
        if config["profile"] != "research":
            raise ResearchError("profile_mismatch", "Author layout cannot replace an independent verification contract")
        state = dict(value["state"], version=2, research={"store": ".exactory/research.sqlite3", "profile": "research"})
        changes.append(("workspace", kind, state))
        return changes, state
    result = prepared_mutation(store, "workspace.initialize", payload, prepare,
                               expected_revision=expected_revision, request_id=request_id)
    export_workspace(store)
    return result


def export_workspace(store):
    """Repair projections from the current authoritative state, including replay."""
    snapshot = store.snapshot()
    for kind, state in snapshot["records"].get("workspace", {}).items():
        if kind in ("study", "draft", "deposit"):
            json_projection(store.root, ".exactory/" + kind + ".json", state)
    if snapshot["records"].get("workspace_decision"):
        write_projection(store.root, ".exactory/decisions.jsonl", _decisions(snapshot["records"], ArtifactStore(store.root)))
    return {"revision": snapshot["revision"], "projection_only": True}


def _decisions(records, artifacts):
    from .storage import _canonical
    legacy = records.get("workspace_decision_history", {}).get("legacy")
    result = artifacts.read(legacy["artifact"]) if legacy else b""
    if result and not result.endswith(b"\n"):
        result += b"\n"
    values = sorted(records.get("workspace_decision", {}).values(), key=lambda item: item["revision"])
    return result + b"".join((_canonical(item["entry"]) + "\n").encode() for item in values)


def record_decision(store, payload, *, expected_revision, request_id):
    """Append an operational decision with CAS, retaining any legacy log bytes."""
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, ("stage", "decision", "why", "evidence"))
        if value["stage"] not in STAGES:
            raise ResearchError("invalid_stage", "Choose a supported study stage")
        text(value["decision"], "Decision")
        text(value["why"], "Decision reason")
        if not isinstance(value["evidence"], str):
            raise ResearchError("invalid_state", "Evidence reference must be text")
        changes = []
        path = ".exactory/decisions.jsonl"
        try:
            previous = read_file(store.root, path)
        except ResearchError as error:
            if error.code != "artifact_missing":
                raise
            previous = b""
        if not records.get("workspace_decision"):
            if previous:
                legacy = {"artifact": artifacts.put(previous, "application/octet-stream"), "scientific_validation": False}
                changes.append(immutable_record(records, "workspace_decision_history", "legacy", legacy))
        elif previous != _decisions(records, artifacts):
            raise ResearchError("projection_changed", "The decision log differs from authority; run exactory-research export")
        entry = dict(value, ts=now())
        changes.append(immutable_record(records, "workspace_decision", request_id,
                                       {"entry": entry, "revision": expected_revision + 1}))
        return changes, entry
    result = prepared_mutation(store, "workspace.decide", payload, prepare,
                               expected_revision=expected_revision, request_id=request_id)
    export_workspace(store)
    return result


def study_state(store, *, verify_projection=True):
    state = store.snapshot()["records"].get("workspace", {}).get("study")
    if state is None:
        raise ResearchError("migration_required", "Adopt the legacy study before research advancement")
    if verify_projection and strict_json(read_file(store.root, ".exactory/study.json")) != state:
        raise ResearchError("projection_changed", "The study projection differs from authoritative history; run exactory-research export")
    return state


def update_study(store, payload, *, expected_revision, request_id):
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, (), ("stage", "status", "autopilot", "waiting", "loop"))
        previous = records.get("workspace", {}).get("study")
        if previous is None:
            raise ResearchError("migration_required", "Adopt the legacy study before research advancement")
        if strict_json(read_file(store.root, ".exactory/study.json")) != previous:
            raise ResearchError("projection_changed", "The study projection differs from authoritative history; run exactory-research export")
        proposed = copy.deepcopy(previous)
        if "loop" in value:
            fields(value["loop"], (), ("target", "budget", "notes"))
            proposed["loop"].update(value["loop"])
        proposed.update({k: v for k, v in value.items() if k != "loop"})
        text(proposed["status"], "Status")
        if type(proposed["autopilot"]) is not bool or not (proposed["waiting"] is None or isinstance(proposed["waiting"], str)):
            raise ResearchError("invalid_state", "Invalid operational study state")
        budget = proposed["loop"]["budget"]
        if budget is not None and (type(budget) is not int or budget < 0):
            raise ResearchError("invalid_state", "Loop budget must be nonnegative")
        validate_transition(records, artifacts, previous, proposed)
        proposed["updated"] = now()
        return [("workspace", "study", proposed)], proposed
    result = prepared_mutation(store, "workspace.state", payload, prepare,
                               expected_revision=expected_revision, request_id=request_id)
    export_workspace(store)
    return result


def adopt_workspace(store, payload, *, expected_revision, request_id):
    """Archive selected legacy bytes without converting assertions into readings."""
    artifacts = ArtifactStore(store.root)
    def prepare(records, value):
        fields(value, ("id", "profile", "target", "files", "reason"))
        text(value["id"], "Adoption ID")
        text(value["reason"], "Adoption reason")
        strings(value["files"], "Legacy paths", nonempty=True)
        changes = []
        config = records.get("configuration", {}).get("research")
        if config is None:
            changes, config = prepare_initialization(records, artifacts,
                {"profile": value["profile"], "target": value["target"]})
        elif config["profile"] != value["profile"] or config["target"] != value["target"]:
            raise ResearchError("configuration_conflict", "Adoption cannot change the current profile or complete objective")
        snapshots = []
        for path in value["files"]:
            raw = read_file(store.root, path)
            snapshots.append({"path": path, "artifact": artifacts.put(raw, "application/octet-stream")})
            for kind in ("study", "draft", "deposit"):
                if path == ".exactory/" + kind + ".json" and kind not in records.get("workspace", {}):
                    marker = strict_json(raw)
                    if not isinstance(marker, dict) or marker.get("version", 1) not in (1, 2):
                        raise ResearchError("migration_required", "Unsupported legacy workspace marker")
                    changes.append(("workspace", kind, marker))
        adoption = dict(value, snapshots=snapshots, imported_at=now(), scientific_validation=False,
                        obligations=[{"code": "legacy_evidence_pending", "message": "Historical notes do not establish source reading, prospective execution or current readiness."}])
        changes.append(immutable_record(records, "workspace_adoption", value["id"], adoption))
        return changes, adoption
    return prepared_mutation(store, "workspace.adopt", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def command_identity(store, args):
    """Legacy commands support explicit CAS and idempotency without inventing gates."""
    revision = getattr(args, "expected_revision", None)
    request = getattr(args, "request_id", None)
    return {"expected_revision": store.revision if revision is None else revision,
            "request_id": request or ("cli-" + uuid.uuid4().hex)}
