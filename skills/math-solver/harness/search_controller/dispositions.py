"""Durable user dispositions, separate from acceptance and closure evidence."""

import copy

from . import schema as s
from .errors import SearchError


_INSTRUCTION_LIMIT_BYTES = 64 * 1024
_OBJECTIVE_ENDED_MESSAGE = (
    "This objective has a recorded end and permits no further research. Deliver any due attempt, report what "
    "stands and what stays open, and stop. Start another objective for the same statement only when the user's "
    "instruction authorizes that further work."
)


def objective_ended(state):
    return next((row for row in state["control"].get("dispositions", [])
                 if row["kind"] == "objective_ended"), None)


def require_objective_open(state):
    s.require(objective_ended(state) is None, _OBJECTIVE_ENDED_MESSAGE, "control_objective_ended")


def is_set_aside(state, checkpoint_id):
    return any(row["kind"] == "result_set_aside" and row["checkpoint_id"] == checkpoint_id
               and "ended_by_acceptance_id" not in row
               for row in state["control"].get("dispositions", []))


def validate_control_provenance(value):
    """Check the reported or host/operator form; authorship is not checked."""
    try:
        if value != {"source": "reported_by_agent"}:
            s.validate_provenance(value)
    except SearchError as error:
        raise SearchError("control_provenance_required",
                          "If the user gave this instruction in this session, provenance is exactly "
                          "{\"source\": \"reported_by_agent\"}; a host or operator that authenticated it gives "
                          "{source, actor_id, attestation_id}. Without the user's instruction, report the stop.") from error


def validate_instruction(state, payload, *, checkpoint=False):
    """Validate the bounded instruction, provenance form and resume's consumed identity."""
    fields = "objective_id message_id session_id instruction provenance" + (" checkpoint_id" if checkpoint else "")
    try:
        s.closed(payload, fields)
        for key in ("objective_id", "message_id", "session_id", "instruction"):
            s.text(payload[key])
        if checkpoint:
            s.text(payload["checkpoint_id"])
    except SearchError as error:
        raise SearchError("control_invalid", "If the user gave this instruction, the spec holds exactly these "
                          "fields: " + fields + ". All but provenance are nonempty text; provenance is an object. "
                          "Without the user's instruction, report the stop.") from error
    s.require(len(payload["instruction"].encode("utf-8")) <= _INSTRUCTION_LIMIT_BYTES,
              "The instruction exceeds 64 KiB of UTF-8 bytes and was not recorded. Do not truncate, paraphrase or "
              "translate it. Report the limit; a shorter instruction comes from the user.", "control_invalid")
    validate_control_provenance(payload["provenance"])
    s.require(payload["objective_id"] == state["objective_id"],
              "objective_id is not this objective's; take it from search status --json.", "control_objective_mismatch")
    identity = s.digest({key: payload[key] for key in ("objective_id", "message_id", "session_id")})
    s.require(identity not in state["control"]["resume_ids"]
              and payload["message_id"] not in state["control"]["side_instruction_ids"],
              "This message_id was used by an earlier action. If the user gave a new instruction, label that message "
              "with a message_id that no action used; without one, report the stop.", "control_instruction_consumed")
    return identity


def set_aside(state, payload):
    identity = validate_instruction(state, payload, checkpoint=True)
    checkpoint_id = payload["checkpoint_id"]
    s.require(checkpoint_id in state["checkpoints"],
              "No checkpoint has this id; search status --json lists the checkpoints.", "control_checkpoint_unavailable")
    cp = state["checkpoints"][checkpoint_id]
    s.require(cp["kind"] != "hypothesis" and not is_set_aside(state, checkpoint_id),
              "This checkpoint is a hypothesis or is already set aside; set-aside takes a result checkpoint that is "
              "not set aside.", "control_checkpoint_unavailable")
    s.require(not any(row["checkpoint_id"] == checkpoint_id and row["status"] == "accepted"
                      for row in state["acceptances"].values()),
              "This checkpoint is accepted, and an accepted result is not set aside.", "control_result_accepted")
    state["control"]["resume_ids"].append(identity)
    state["control"].setdefault("dispositions", []).append(dict(copy.deepcopy(payload), kind="result_set_aside"))


def end_objective(state, payload):
    identity = validate_instruction(state, payload)
    s.require(objective_ended(state) is None, "This objective has already ended on the user's instruction.",
              "control_objective_ended")
    s.require(state["execution_status"] != "resolved",
              "This objective is resolved by a closure, so it does not end; an end is for an objective without a "
              "closure.", "control_objective_resolved")
    state["control"]["resume_ids"].append(identity)
    state["control"].setdefault("dispositions", []).append(dict(copy.deepcopy(payload), kind="objective_ended"))
    state["execution_status"] = "ended"


EVENT_HANDLERS = {"result_set_aside": set_aside, "objective_ended": end_objective}
