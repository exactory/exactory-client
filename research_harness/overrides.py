"""Pinned user instructions that advance work without changing gate findings."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from .errors import ResearchError
from .gates import STAGES
from .native_writes import write_atomic
from .operations import fields, immutable_record, prepared_mutation, text
from .workspace import checked_parent, find_workspace, open_file, read_file, strict_json


_INSTRUCTION_LIMIT_BYTES = 64 * 1024
_UNRECORDED_OVERRIDES_PATH = ".exactory/unrecorded-overrides.jsonl"


def validate_override_payload(value):
    """Validate the instruction's scope independently of store availability."""
    from .cli import GATES
    code = "gate_override_invalid"
    fields(value, ("id", "gate", "instruction", "reason"), ("stop_code", "transition", "public_action"), code=code)
    for key in ("id", "gate", "instruction", "reason", "stop_code"):
        if key in value:
            text(value[key], "Gate override " + key, code=code)
    if value["gate"] not in GATES:
        raise ResearchError("gate_override_gate", "Give gate as a name that exactory-research gate accepts")
    if "public_action" in value:
        action = value["public_action"]
        if (not isinstance(action, str) or not action.startswith(("POST /", "PUT /", "PATCH /", "DELETE /"))
                or "\n" in action or "\r" in action):
            raise ResearchError("gate_override_invalid", "Need public_action as an exact METHOD /path for this write.")
    if "transition" in value:
        transition = value["transition"]
        fields(transition, ("from", "to"), code=code)
        if any(transition[key] not in STAGES for key in ("from", "to")):
            raise ResearchError("gate_override_transition", "Give from and to as study stages, initiate through complete")
        if STAGES.index(transition["to"]) < STAGES.index(transition["from"]):
            raise ResearchError("gate_override_transition", "An override cannot move a study to an earlier stage; "
                                "give the current stage as from and the same or a later stage as to")


def read_override_instruction(root, path):
    """Read at most 64 KiB from a regular UTF-8 file under context/, without following links."""
    if (not isinstance(path, str) or not path.startswith("context/")
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise ResearchError("gate_override_instruction", "Give instruction as a relative path under context/ of the "
                            "workspace, without . or .. parts, to the file that holds the user's own words")
    try:
        with open_file(root, path) as source:
            data = source.read(_INSTRUCTION_LIMIT_BYTES + 1)
        if len(data) > _INSTRUCTION_LIMIT_BYTES:
            raise ResearchError("gate_override_instruction", "The instruction file must be at most 64 KiB")
        instruction = data.decode("utf-8")
        if not instruction.strip():
            raise ResearchError("gate_override_instruction", "The instruction file is empty. It must hold the user's own words; "
                                "without an instruction, report the stop")
    except (ResearchError, UnicodeError, OSError) as error:
        if isinstance(error, ResearchError) and error.code.startswith("gate_override_"):
            raise
        raise ResearchError("gate_override_instruction", "The instruction file must be a regular UTF-8 file under context/; "
                            "it is missing, a link, a directory, unreadable or not UTF-8") from error
    return data, instruction


def record_override(store, payload, *, expected_revision, request_id):
    """Record a reported user instruction and the gate's current unmet obligations."""
    artifacts = store.build_artifact_store()

    def prepare(records, value):
        from .cli import build_gate_report
        from .problem_claims import compute_statement_digest, find_current_statement
        validate_override_payload(value)
        transition = value.get("transition")
        if transition is not None:
            study = records.get("workspace", {}).get("study")
            if study is None or study["stage"] != transition["from"]:
                raise ResearchError("gate_override_transition", "transition.from must be the current stage of this study; "
                                    "a record without a study, such as a problem record, takes no transition")
        binding = {}
        if value["gate"] == "post":
            statement = find_current_statement(records)
            if statement is None:
                raise ResearchError("problem_statement_missing", "Select the exact current problem statement")
            binding = dict(statement_id=statement["id"], statement_digest=compute_statement_digest(statement))
        data, instruction = read_override_instruction(store.root, value["instruction"])
        try:
            report = build_gate_report(records, artifacts, value["gate"], expected_revision)
            obligations = [{key: row[key] for key in ("code", "explanation")} for row in report["obligations"]]
        except ResearchError as error:
            obligations = [dict(code=error.code, explanation=error.message)]
        existing = records.get("gate_override", {}).get(value["id"])
        record = dict(value, instruction=dict(path=value["instruction"], text=instruction,
                                              artifact=artifacts.put(data, "text/plain; charset=utf-8")),
                      authority="user", evidence="reported_by_agent", obligations=obligations,
                      recorded_revision=existing["recorded_revision"] if existing else expected_revision + 1, **binding)
        try:
            change = immutable_record(records, "gate_override", value["id"], record)
        except ResearchError as error:
            raise ResearchError("gate_override_conflict", "An override with this id is already recorded with another payload; "
                                "use a new id, or repeat the original payload with its original request id",
                                error.details) from error
        return [change], record

    return prepared_mutation(store, "research.gate_override", payload, prepare,
                             expected_revision=expected_revision, request_id=request_id)


def append_unrecorded_override(root, payload, *, expected_revision, request_id):
    """Append and sync a local instruction when the authoritative store cannot take it."""
    validate_override_payload(payload)
    text(request_id, "Gate override request ID", code="gate_override_invalid")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ResearchError("gate_override_invalid", "Expected revision must be a nonnegative integer")
    data, instruction = read_override_instruction(root, payload["instruction"])
    record = dict(payload, instruction=dict(path=payload["instruction"], text=instruction,
                                           sha256=hashlib.sha256(data).hexdigest()),
                  authority="user", evidence="reported_by_agent", request_id=request_id,
                  expected_revision=expected_revision)
    try:
        with checked_parent(root, _UNRECORDED_OVERRIDES_PATH, create=True) as (directory, name):
            descriptor = os.open(name + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=directory)
            with os.fdopen(descriptor, "wb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ResearchError("gate_override_storage", "The lock file of the local override log, "
                                        + _UNRECORDED_OVERRIDES_PATH + ".lock, must be a regular file")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    previous = read_file(root, _UNRECORDED_OVERRIDES_PATH)
                except ResearchError as error:
                    if error.code != "artifact_missing":
                        raise
                    previous = b""
                data = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                write_atomic(name, previous + data, directory_fd=directory)
    except (OSError, ResearchError) as error:
        raise ResearchError("gate_override_storage", "The store refused the override, and the local log "
                            + _UNRECORDED_OVERRIDES_PATH + " could not be written; nothing is recorded. "
                            "Repair the log or its directory, then record the override again") from error
    return dict(recorded=False, path=str(root / _UNRECORDED_OVERRIDES_PATH))


def build_unreadable_override(line_number):
    return dict(id="unreadable-line-" + str(line_number), gate="unknown",
                instruction=dict(text="An entry of " + _UNRECORDED_OVERRIDES_PATH + " could not be read. "
                                 "Its gate and the user's words are unknown."))


def read_unrecorded_overrides(root):
    """Read local fallback entries without importing them into the authoritative store."""
    try:
        data = read_file(root, _UNRECORDED_OVERRIDES_PATH)
    except (ResearchError, OSError) as error:
        if isinstance(error, ResearchError) and error.code == "artifact_missing":
            return None
        return [build_unreadable_override(1)]
    rows = []
    for line_number, line in enumerate(data.splitlines(), 1):
        try:
            row = strict_json(line)
        except ResearchError:
            row = None
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("gate"), str) or not isinstance(row.get("instruction"), dict)
                or not isinstance(row["instruction"].get("text"), str)):
            row = build_unreadable_override(line_number)
        rows.append(row)
    return rows


def describe_overrides(records, root):
    """Quote instructions and list the obligation codes while retaining the ordinary report shape when absent."""
    result = {}
    rows = records.get("gate_override", {}).values()
    if rows:
        result["overrides"] = [dict(id=row["id"], gate=row["gate"], transition=row.get("transition"),
                                    stop_code=row.get("stop_code"),
                                    **(dict(authority="harness", stop_id=row["stop_id"], cited_stops=row["cited_stops"],
                                            report_path=row["report_path"]) if row.get("authority") == "harness" else
                                       dict(instruction=row["instruction"]["text"])),
                                    **({"public_action": row["public_action"]} if "public_action" in row else {}),
                                    obligations=[item["code"] for item in row["obligations"]])
                               for row in sorted(rows, key=lambda row: (row["recorded_revision"], row["id"]))]
    unrecorded = read_unrecorded_overrides(root)
    automatic = read_unrecorded_automatic_overrides(root, records.get("gate_override", {}))
    if automatic:
        unrecorded = (unrecorded or []) + automatic
    if unrecorded is not None:
        result["unrecorded_overrides"] = unrecorded
    return result


def read_unrecorded_automatic_overrides(root, recorded):
    """Show pending local reports without importing or applying their proposals."""
    directory = root / ".exactory/automatic-passes"
    if not directory.exists():
        return []
    try:
        names = sorted(path.name for path in directory.iterdir() if path.suffix == ".json")
    except OSError:
        names = ["unreadable.json"]
    result = []
    for name in names:
        path = ".exactory/automatic-passes/" + name
        try:
            row = strict_json(read_file(root, path))
            if (not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in
                    ("override_id", "gate", "stop_id", "stop_code"))):
                raise ValueError()
        except (OSError, ResearchError, ValueError):
            row = dict(override_id="unreadable-" + name, gate="unknown", stop_id="unknown", stop_code="unknown",
                       cited_stops=[])
        if row["override_id"] not in recorded:
            result.append(dict(id=row["override_id"], authority="harness", gate=row["gate"], stop_id=row["stop_id"],
                               stop_code=row["stop_code"], cited_stops=row.get("cited_stops", []), report_path=path,
                               recorded=False))
    return result


def list_passed_gates(store):
    """Collect recorded and local fallback gate names for the deposit's public disclosure."""
    records = {}
    if store is None:
        root = find_workspace(required=False) or Path.cwd()
    else:
        root = store.root
        try:
            records = store.snapshot()["records"]
        except ResearchError:
            pass
    gates = {row["gate"] for row in records.get("gate_override", {}).values()}
    gates.update(row["gate"] for row in read_unrecorded_overrides(root) or [])
    return sorted(gates)


def describe_deposit_overrides(store):
    """Keep automatic entries out of the disclosure that attributes user instructions."""
    records = {}
    root = store.root if store is not None else find_workspace(required=False) or Path.cwd()
    if store is not None:
        try:
            records = store.snapshot()["records"]
        except ResearchError:
            pass
    rows = records.get("gate_override", {})
    gates = {row["gate"] for row in rows.values() if row.get("authority") != "harness"}
    gates.update(row["gate"] for row in read_unrecorded_overrides(root) or [])
    automatic = [dict(gate=row["gate"], stop_code=row["stop_code"], stop_id=row["stop_id"], recorded=True)
                 for row in rows.values() if row.get("authority") == "harness"]
    automatic.extend(dict(gate=row["gate"], stop_code=row["stop_code"], stop_id=row["stop_id"], recorded=False)
                     for row in read_unrecorded_automatic_overrides(root, rows))
    return dict(passed_gates=sorted(gates), automatic_passes=sorted(automatic, key=lambda row: (row["gate"], row["stop_id"])))
