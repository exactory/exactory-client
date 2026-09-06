"""Exact immutable artifact and accepted-basis audits for computation records."""

from . import schema as s
from .computation import validate_basis
from .evidence import audit_checkpoint
from .proof import acceptance_closure


def computation_inputs(value, content):
    required = [value["basis"]["deduction_digest"]] + value["preflight"]["inspected_evidence"]
    if value["domain"]["kind"] == "bounded_encoding":
        required.append(value["domain"]["encoding_digest"])
    for item in required:
        s.require(bool(content.get_artifact(item).strip()), "Computation evidence is empty", "missing_evidence")


def audit_computation(root, state, proposal, value, content):
    validate_basis(state, proposal, value)
    computation_inputs(value, content)
    checked = set()
    for item in value["basis"]["dependencies"]:
        closure = acceptance_closure(state, item["acceptance_id"], proposal["claim"]["proof_policy"])
        for identity in sorted(closure - checked):
            accepted = state["acceptances"][identity]
            s.require(content.get_blob(s.digest(accepted["review"])) == accepted["review"],
                      "Computation basis review changed", "digest_mismatch")
            audit_checkpoint(root, state, state["checkpoints"][accepted["checkpoint_id"]], content)
            checked.add(identity)


def audit_amendment(root, state, node, content):
    amendment = state["service"]["computation_amendments"].get(node["id"])
    if amendment is None:
        return
    s.require(content.get_blob(amendment["digest"]) == amendment["subject"]
              and content.get_blob(s.digest(amendment["review"])) == amendment["review"],
              "Pinned computation amendment or review changed", "digest_mismatch")
    audit_computation(root, state, state["proposals"][node["proposal_id"]]["record"],
                      amendment["subject"]["computation"], content)


def audit_run_result(state, run, content):
    result = content.get_blob(run["result_digest"])
    s.require(result["run_id"] == run["id"] and result["input_digest"] == run["input_digest"]
              and result["claim_digest"] == state["nodes"][run["node_id"]]["claim_digest"],
              "Actual result differs from the reserved computation", "digest_mismatch")
    s.require(len(result["commands"]) <= len(run["commands"]), "Result command inventory differs")
    for number, command in enumerate(result["commands"]):
        s.require(command["argv"] == run["commands"][number], "Result command changed", "digest_mismatch")
        content.get_artifact(command["stdout_digest"])
        content.get_artifact(command["stderr_digest"])
    for output in run["outputs"]:
        content.get_artifact(output["digest"])
    return result
