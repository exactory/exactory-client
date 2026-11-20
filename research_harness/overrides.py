"""Pinned user instructions that advance work without changing gate findings."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from .errors import ResearchError
from .evidence import digest
from .gates import STAGES
from .native_writes import write_atomic
from .operations import fields, immutable_record, prepared_mutation, text, timestamp
from .storage import overlay_kinds
from .workspace import checked_parent, find_workspace, open_file, read_file, strict_json


_INSTRUCTION_LIMIT_BYTES = 64 * 1024
_UNRECORDED_OVERRIDES_PATH = ".exactory/unrecorded-overrides.jsonl"
_OVERRIDE_REQUIRED_FIELDS = ("id", "gate", "instruction", "reason")
_OVERRIDE_OPTIONAL_FIELDS = ("stop_code", "transition", "public_action", "condition", "lifts")
CONDITION_STATES = ("met", "unmet", "user_decides", "lifted", "unknown")


def check_openness_supported_open(records, artifacts):
    """Use the ordinary assessment of the current statement and pin a met finding."""
    from .problem_claims import find_latest
    from .problem_openness import assess_openness
    obligations = assess_openness(records, artifacts)
    finding = find_latest(records, "problem_openness")
    evidence = [] if obligations else [dict(id=finding["id"], digest=digest(finding))]
    return obligations, evidence


_CONDITION_CHECKS = {"openness_supported_open": (("post",), check_openness_supported_open)}


def _valid_public_action(action):
    return (isinstance(action, str) and action.startswith(("POST /", "PUT /", "PATCH /", "DELETE /"))
            and "\n" not in action and "\r" not in action)


def validate_override_payload(value):
    """Validate the instruction's scope independently of store availability."""
    from .cli import GATES
    code = "gate_override_invalid"
    fields(value, _OVERRIDE_REQUIRED_FIELDS, _OVERRIDE_OPTIONAL_FIELDS, code=code)
    for key in ("id", "gate", "instruction", "reason", "stop_code"):
        if key in value:
            text(value[key], "Gate override " + key, code=code)
    if value["gate"] not in GATES:
        raise ResearchError("gate_override_gate", "Give gate as a name that exactory-research gate accepts")
    if "public_action" in value and not _valid_public_action(value["public_action"]):
        raise ResearchError("gate_override_invalid", "Need public_action as an exact METHOD /path for this write.")
    if "condition" in value:
        condition = value["condition"]
        fields(condition, ("text", "part", "check"), code=code)
        text(condition["text"], "Instruction condition", code=code)
        check = condition["check"]
        if check is not None and (not isinstance(check, str) or check not in _CONDITION_CHECKS
                                  or value["gate"] not in _CONDITION_CHECKS[check][0]):
            raise ResearchError(code, "Name a condition check that serves this gate, or use null for a user decision")
    if "lifts" in value:
        if not isinstance(value["lifts"], list):
            raise ResearchError(code, "Give lifts as an array of the conditions that this instruction lifts")
        targets = set()
        for lift in value["lifts"]:
            fields(lift, ("override_id", "basis", "part", "quote"), code=code)
            text(lift["override_id"], "Lifted override id", code=code)
            text(lift["quote"], "Words that lift the condition", code=code)
            if lift["basis"] not in ("named", "after_report") or lift["override_id"] in targets:
                raise ResearchError(code, "Give each lifted override once, with basis named or after_report")
            targets.add(lift["override_id"])
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
    return data, parse_override_instruction(path, instruction)


def parse_override_instruction(path, instruction):
    """Keep the original text and validate the attributed parts of a JSON instruction."""
    result = dict(path=path, form="text", text=instruction)
    if not path.endswith(".json"):
        return result
    code = "gate_override_instruction"
    try:
        value = strict_json(instruction)
        fields(value, ("schema", "parts"), code=code)
        if value["schema"] != "exactory-instruction-v1" or not isinstance(value["parts"], list):
            raise ResearchError(code, "Use exactory-instruction-v1 with an array of instruction parts")
        has_instruction = False
        for part in value["parts"]:
            if not isinstance(part, dict) or part.get("kind") not in ("message", "dialog", "note"):
                raise ResearchError(code, "Give each instruction part as a message, dialog or note")
            kind = part["kind"]
            keys = ("kind", "asked_at", "answered_at", "question", "options", "selected", "typed") if kind == "dialog" else (
                ("kind", "at", "text", "reports") if kind == "note" else ("kind", "at", "text"))
            fields(part, keys, code=code)
            for key in (("asked_at", "answered_at") if kind == "dialog" else ("at",)):
                if part[key] is not None:
                    timestamp(part[key])
            if kind != "dialog":
                text(part["text"], "Instruction part text", code=code)
                if kind == "note" and part["reports"] is not None:
                    text(part["reports"], "Reported override id", code=code)
            else:
                text(part["question"], "Dialog question", code=code)
                if not isinstance(part["options"], list) or len(part["options"]) < 2:
                    raise ResearchError(code, "Keep at least two options in a dialog part")
                for option in part["options"]:
                    fields(option, ("label", "text"), code=code)
                    for key in ("label", "text"):
                        text(option[key], "Dialog option " + key, code=code)
                selected = part["selected"]
                if (not isinstance(selected, list) or any(type(index) is not int or not 0 <= index < len(part["options"])
                                                         for index in selected)
                        or len(set(selected)) != len(selected)):
                    raise ResearchError(code, "Give selected as distinct zero-based indices of the offered options")
                if part["typed"] is not None:
                    text(part["typed"], "User words typed in the dialog", code=code)
                if not selected and part["typed"] is None:
                    raise ResearchError(code, "A dialog instruction needs a selected option or the user's typed words")
            has_instruction = has_instruction or kind in ("message", "dialog")
        if not has_instruction:
            raise ResearchError(code, "Keep at least one message or dialog in an instruction")
    except ResearchError as error:
        if error.code == code:
            raise
        raise ResearchError(code, error.message, error.details) from error
    return dict(result, form="parts", parts=value["parts"])


def describe_instruction(instruction):
    """Separate user-typed words from session-authored choices without changing text-form views."""
    if instruction.get("form") != "parts":
        return {}
    words, selections = [], []
    for part in instruction["parts"]:
        if part["kind"] == "message":
            words.append(dict(at=part["at"], text=part["text"]))
        elif part["kind"] == "dialog":
            if part["typed"] is not None:
                words.append(dict(at=part["answered_at"], text=part["typed"]))
            selections.append(dict(asked_at=part["asked_at"], answered_at=part["answered_at"],
                                   question=part["question"], author="session",
                                   selected=[part["options"][index] for index in part["selected"]],
                                   unselected=[option for index, option in enumerate(part["options"])
                                               if index not in part["selected"]]))
    return dict(user_words=words, selections=selections)


def read_instruction_words(instruction, index):
    """Read only user words or a selected option as the verbatim basis of authority."""
    parts = instruction.get("parts", []) if instruction.get("form") == "parts" else [
        dict(kind="message", text=instruction["text"])]
    if type(index) is not int or not 0 <= index < len(parts):
        raise ResearchError("gate_override_invalid", "Give part as the zero-based index of an instruction part")
    part = parts[index]
    if part["kind"] == "message":
        return [part["text"]]
    if part["kind"] == "dialog":
        words = [] if part["typed"] is None else [part["typed"]]
        return words + [part["options"][selected][key] for selected in part["selected"] for key in ("label", "text")]
    return []


def validate_override_bindings(value, instruction, prior_overrides):
    """Bind each condition and lift to the instruction and, when readable, an earlier condition."""
    condition = value.get("condition")
    if condition is not None and not any(condition["text"] in words for words in
                                         read_instruction_words(instruction, condition["part"])):
        raise ResearchError("gate_override_invalid", "The condition text must occur verbatim in user words or a selected option")
    for lift in value.get("lifts", []):
        if not any(lift["quote"] in words for words in read_instruction_words(instruction, lift["part"])):
            raise ResearchError("gate_override_invalid", "The lift quote must occur verbatim in user words or a selected option")
        if lift["override_id"] == value["id"] or (prior_overrides is not None and not any(
                row["id"] == lift["override_id"] and row.get("authority") == "user" and "condition" in row
                for row in prior_overrides)):
            raise ResearchError("gate_override_invalid", "A lift must name an earlier user override with a condition")
        if lift["basis"] == "after_report" and not any(part["kind"] == "note" and part["reports"] == lift["override_id"]
                for part in instruction.get("parts", [])[:lift["part"]]):
            raise ResearchError("gate_override_invalid", "An after_report lift needs a prior note reporting that override's unmet condition")


def covers_public_write(row, gate, public_action):
    """Keep exact-action coverage independent of the gate, and gate coverage for an unscoped action."""
    return row.get("authority") == "user" and (
        row.get("public_action") == public_action if row.get("public_action") is not None else
        gate is not None and row["gate"] == gate)


def collect_applicable_overrides(records, local_overrides, write):
    """Collect all covering user instructions in recorded order, retaining local rows separately."""
    rows = list((records or {}).get("gate_override", {}).values())
    rows.sort(key=lambda row: (row.get("recorded_revision", 0), row["id"]))
    rows.extend(local_overrides)
    if write.get("kind") == "problem-override":
        return [row for row in rows if row.get("authority") == "user" and row["gate"] == "post"]
    return [row for row in rows if covers_public_write(row, write.get("gate"), write.get("public_action"))]


def precedes_override(earlier, later, local_order):
    """Use store revisions or local append order to require a condition before its lift."""
    first, second = local_order.get(id(earlier)), local_order.get(id(later))
    if first is not None and second is not None:
        return first < second
    if first is not None:
        start, end = earlier.get("expected_revision"), later.get("recorded_revision")
    elif second is not None:
        start, end = earlier.get("recorded_revision"), later.get("expected_revision")
        return type(start) is int and type(end) is int and start <= end
    else:
        start, end = earlier.get("recorded_revision"), later.get("recorded_revision")
    return type(start) is int and type(end) is int and start < end


def assess_write_authority(records, local_overrides, write, *, artifacts=None):
    """Assess every applicable condition; None records or unavailable check inputs yield unknown authority.

    write holds gate and public_action, with kind=problem-override for composition.
    artifacts supplies the verified reader needed by named checks; checks of lifted conditions never run.
    """
    from .problem_claims import compute_statement_digest, find_current_statement
    applicable = collect_applicable_overrides(records, local_overrides, write)
    local_order = {id(row): index for index, row in enumerate(local_overrides)}
    is_export = write.get("kind") == "problem-override"
    if is_export:
        statement = find_current_statement(records or {})
        identity = None if statement is None else compute_statement_digest(statement)
        covered = identity is not None and any(row.get("statement_digest") == identity for row in applicable)
    else:
        covered = bool(applicable)
    conditions, checked = [], {}
    for row in applicable:
        if "condition" not in row:
            continue
        condition = row["condition"]
        lifted_by = list(dict.fromkeys(other["id"] for other in applicable for lift in other.get("lifts", [])
                                      if lift["override_id"] == row["id"] and precedes_override(row, other, local_order)))
        state, evidence, obligations = "user_decides", [], []
        check = condition["check"]
        if lifted_by:
            state = "lifted"
        elif check is not None:
            if records is None or artifacts is None:
                state = "unknown"
            else:
                if check not in checked:
                    try:
                        pending, support = _CONDITION_CHECKS[check][1](records, artifacts)
                        checked[check] = ("unmet" if pending else "met", support, pending)
                    except ResearchError as error:
                        checked[check] = ("unknown", [], [dict(code=error.code, explanation=error.message)])
                state, evidence, obligations = checked[check]
        conditions.append(dict(override_id=row["id"], text=condition["text"], check=check, state=state,
                               evidence=evidence, obligations=obligations, lifted_by=lifted_by))
    return dict(covered=covered, override_ids=list(dict.fromkeys(row["id"] for row in applicable)),
                conditions=conditions, authorized=covered and all(row["state"] in ("met", "lifted") for row in conditions))


def require_met_override_conditions(authority):
    """Refuse a write while an applicable condition stands without met evidence."""
    if any(row["state"] not in ("met", "lifted") for row in authority["conditions"]):
        raise ResearchError("gate_override_condition_unmet",
                            "A recorded instruction's condition still stands and has not been established as met. "
                            "Report the condition and its obligations; continue independent work, or record the owner's explicit lift.",
                            authority)


def describe_override_write_authorities(records, local_overrides, row, artifacts):
    """Show current authority for the writes of a recorded or proposed instruction.

    The proposed view keeps the history of the store, which the verification of a carried selection reads.
    """
    proposed = records
    if records is not None and "recorded_revision" in row:
        proposed = overlay_kinds(records, dict(gate_override={**records.get("gate_override", {}), row["id"]: row}))
    writes = [dict(gate=row["gate"], public_action=row.get("public_action"))]
    if row["gate"] == "post":
        writes.append(dict(kind="problem-override"))
    assessments = [dict(write=write, **assess_write_authority(proposed, local_overrides, write, artifacts=artifacts))
                   for write in writes]
    return assessments if any(item["conditions"] for item in assessments) or row.get("lifts") else []


def record_override(store, payload, *, expected_revision, request_id):
    """Record a reported user instruction and the gate's current unmet obligations."""
    artifacts = store.build_artifact_store()
    known_authority_records = None

    def prepare(records, value):
        nonlocal known_authority_records
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
        prior = list(records.get("gate_override", {}).values())
        if value.get("lifts"):
            prior += read_unrecorded_overrides(store.root) or []
        validate_override_bindings(value, instruction, prior)
        try:
            report = build_gate_report(records, artifacts, value["gate"], expected_revision)
            obligations = [{key: row[key] for key in ("code", "explanation")} for row in report["obligations"]]
        except ResearchError as error:
            obligations = [dict(code=error.code, explanation=error.message)]
        existing = records.get("gate_override", {}).get(value["id"])
        record = dict(value, instruction=dict(instruction, artifact=artifacts.put(data, "text/plain; charset=utf-8")),
                      authority="user", evidence="reported_by_agent", obligations=obligations,
                      recorded_revision=existing["recorded_revision"] if existing else expected_revision + 1, **binding)
        try:
            change = immutable_record(records, "gate_override", value["id"], record)
        except ResearchError as error:
            raise ResearchError("gate_override_conflict", "An override with this id is already recorded with another payload; "
                                "use a new id, or repeat the original payload with its original request id",
                                error.details) from error
        known_authority_records = {kind: dict(records.get(kind, {}))
                                   for kind in ("gate_override", "problem_statement", "problem_selection")}
        return [change], record

    receipt = prepared_mutation(store, "research.gate_override", payload, prepare,
                                expected_revision=expected_revision, request_id=request_id)
    local = read_unrecorded_overrides(store.root) or []
    available = True
    try:
        records = store.snapshot()["records"]
    except ResearchError:
        records, artifacts, available = known_authority_records, None, False
        if records is None:
            # A replay bypasses preparation. Without a current read, its old
            # lift alone cannot establish the other conditions of this write.
            local = [receipt["result"]] if "condition" in receipt["result"] else []
    authorities = describe_override_write_authorities(records, local, receipt["result"], artifacts)
    if not available:
        authorities = [dict(authority, available=False) for authority in authorities]
    return dict(receipt, write_authorities=authorities) if authorities else receipt


def append_unrecorded_override(root, payload, *, expected_revision, request_id):
    """Append and sync a local instruction when the authoritative store cannot take it."""
    validate_override_payload(payload)
    text(request_id, "Gate override request ID", code="gate_override_invalid")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ResearchError("gate_override_invalid", "Expected revision must be a nonnegative integer")
    data, instruction = read_override_instruction(root, payload["instruction"])
    prior = None
    if payload.get("lifts"):
        from .storage import Store
        try:
            prior = list(Store(root).snapshot()["records"].get("gate_override", {}).values())
            prior += read_unrecorded_overrides(root) or []
        except ResearchError:
            # Quote and after_report form still bind even while the store cannot be read.
            pass
    validate_override_bindings(payload, instruction, prior)
    record = dict(payload, instruction=dict(instruction, sha256=hashlib.sha256(data).hexdigest()),
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
    result = dict(recorded=False, path=str(root / _UNRECORDED_OVERRIDES_PATH))
    from .storage import Store
    try:
        store = Store(root)
        records, artifacts = store.snapshot()["records"], store.build_artifact_store()
    except ResearchError:
        records, artifacts = None, None
    authorities = describe_override_write_authorities(records, read_unrecorded_overrides(root) or [], record, artifacts)
    return dict(result, write_authorities=authorities) if authorities else result


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
                or not isinstance(row["instruction"].get("text"), str)
                or ("public_action" in row and not _valid_public_action(row["public_action"]))):
            row = build_unreadable_override(line_number)
        elif "form" in row["instruction"] or "condition" in row or "lifts" in row:
            try:
                instruction = row["instruction"]
                data = instruction["text"].encode("utf-8")
                text(instruction["path"], "Instruction path", code="gate_override_instruction")
                parsed = parse_override_instruction(instruction["path"], instruction["text"])
                if (len(data) > _INSTRUCTION_LIMIT_BYTES or instruction != dict(parsed, sha256=hashlib.sha256(data).hexdigest())):
                    raise ResearchError("gate_override_instruction", "The local instruction must match its pinned text and parts")
                payload = build_override_payload(row)
                validate_override_payload(payload)
                validate_override_bindings(payload, instruction, None)
            except (ResearchError, KeyError, TypeError, UnicodeError):
                row = build_unreadable_override(line_number)
        rows.append(row)
    return rows


def build_override_payload(row):
    """Recover command fields for checking a stored override without its derived fields."""
    payload = {key: row[key] for key in _OVERRIDE_REQUIRED_FIELDS + _OVERRIDE_OPTIONAL_FIELDS if key in row}
    payload["instruction"] = row["instruction"]["path"]
    return payload


def describe_overrides(records, root, *, artifacts=None):
    """Quote instructions and list the obligation codes while retaining the ordinary report shape when absent."""
    result = {}
    rows = sorted(records.get("gate_override", {}).values(), key=lambda row: (row["recorded_revision"], row["id"]))
    if rows:
        result["overrides"] = [dict(id=row["id"], gate=row["gate"], transition=row.get("transition"),
                                    stop_code=row.get("stop_code"),
                                    **(dict(authority="harness", stop_id=row["stop_id"], cited_stops=row["cited_stops"],
                                            report_path=row["report_path"]) if row.get("authority") == "harness" else
                                       dict(instruction=row["instruction"]["text"], **describe_instruction(row["instruction"]))),
                                    **({"public_action": row["public_action"]} if "public_action" in row else {}),
                                    obligations=[item["code"] for item in row["obligations"]])
                               for row in rows]
    local = read_unrecorded_overrides(root)
    unrecorded = None if local is None else [dict(row, **describe_instruction(row["instruction"])) for row in local]
    automatic = read_unrecorded_automatic_overrides(root, records.get("gate_override", {}))
    if automatic:
        unrecorded = (unrecorded or []) + automatic
    if unrecorded is not None:
        result["unrecorded_overrides"] = unrecorded
    sources = rows + (local or []) + automatic
    for source, row in zip(sources, result.get("overrides", []) + result.get("unrecorded_overrides", [])):
        if "condition" not in source and "lifts" not in source:
            continue
        write = dict(gate=source["gate"], public_action=source.get("public_action"))
        authority = assess_write_authority(records, local or [], write, artifacts=artifacts)
        condition_sources = [item for item in collect_applicable_overrides(records, local or [], write) if "condition" in item]
        condition = next((item for origin, item in zip(condition_sources, authority["conditions"]) if origin is source), None)
        if condition is not None:
            row["condition"] = condition
            row["lifted_by"] = condition["lifted_by"]
        row["lifts"] = source.get("lifts", [])
    return result


def verify_override_instructions(records, artifacts, local_overrides=()):
    """Check new stored instruction forms against their pinned bytes and earlier conditions."""
    count, prior = 0, list(local_overrides)
    for row in sorted(records.get("gate_override", {}).values(), key=lambda row: (row["recorded_revision"], row["id"])):
        if row.get("authority") != "user":
            continue
        instruction = row["instruction"]
        if "form" in instruction:
            raw = artifacts.read(instruction["artifact"])
            try:
                original = raw.decode("utf-8")
            except UnicodeError as error:
                raise ResearchError("gate_override_instruction", "The pinned instruction must be UTF-8") from error
            parsed = parse_override_instruction(instruction["path"], original)
            if instruction != dict(parsed, artifact=instruction["artifact"]):
                raise ResearchError("gate_override_instruction", "The recorded instruction must match its pinned text and parts")
            payload = build_override_payload(row)
            validate_override_payload(payload)
            validate_override_bindings(payload, instruction, prior)
            count += 1
        prior.append(row)
    return count


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
    gates = {row["gate"] for row in records.get("gate_override", {}).values() if "public_action" not in row}
    gates.update(row["gate"] for row in read_unrecorded_overrides(root) or [] if "public_action" not in row)
    return sorted(gates)


def describe_deposit_overrides(store):
    """Disclose user gate instructions, exact public writes and automatic entries separately."""
    records = {}
    root = store.root if store is not None else find_workspace(required=False) or Path.cwd()
    if store is not None:
        try:
            records = store.snapshot()["records"]
        except ResearchError:
            pass
    rows = records.get("gate_override", {})
    unrecorded = read_unrecorded_overrides(root) or []
    gates = {row["gate"] for row in rows.values()
             if row.get("authority") != "harness" and "public_action" not in row}
    gates.update(row["gate"] for row in unrecorded if "public_action" not in row)
    automatic = [dict(gate=row["gate"], stop_code=row["stop_code"], stop_id=row["stop_id"], recorded=True)
                 for row in rows.values() if row.get("authority") == "harness"]
    automatic.extend(dict(gate=row["gate"], stop_code=row["stop_code"], stop_id=row["stop_id"], recorded=False)
                     for row in read_unrecorded_automatic_overrides(root, rows))
    result = dict(passed_gates=sorted(gates),
                  automatic_passes=sorted(automatic, key=lambda row: (row["gate"], row["stop_id"])))
    actions = [dict(id=row["id"], public_action=row["public_action"], recorded=True)
               for row in rows.values() if row.get("authority") != "harness" and "public_action" in row]
    actions.extend(dict(id=row["id"], public_action=row["public_action"], recorded=False)
                   for row in unrecorded if "public_action" in row)
    if actions:
        result["public_action_overrides"] = sorted(actions, key=lambda row: (row["public_action"], row["id"]))
    return result
