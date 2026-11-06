"""Deterministic, recoverable Markdown projections of controller state."""

import json
from pathlib import Path

from research_harness.native_writes import write_atomic

from .proof import coverage, obligation_support
from .storage import safe_path
from .errors import SearchError
from . import schema as s


def strategy_assessment_lines(state, node_id=None, *, status=None, assessments=None, failures=None):
    from .strategy_refresh import assessment_status
    if status is None:
        status = assessment_status(state)
    policy = state["control"]["strategy_refresh"]
    if assessments is None:
        assessments = [(record, [row for row in record["assessment"]["assessments"]
                                 if node_id is None or row["node_id"] == node_id]) for record in policy["assessments"]]
    if failures is None:
        failures = [(identity, failure) for identity, failure in policy["failures"].items()
                    if node_id is None or failure["node_id"] == node_id]
    prefix = ".search/blobs/" if node_id is None else "../.search/blobs/"
    lines = ["", "## Strategy reassessment", "",
             "Recorded context: " + status["context_digest"],
             "Reassessment required by recorded evidence: " + ("yes" if status["required"] else "no"),
             "Policy recorded as enabled: " + ("yes" if policy["enabled"] else "no; the current service checks new work"),
             "Local plan freshness is checked by search strategy-context and at execution."]
    for record, rows in assessments:
        value = record["assessment"]
        if node_id is not None and not rows:
            continue
        lines += ["", "### " + record["id"], "",
                  "Predecessor: " + str(record["predecessor_id"]),
                  "Context: [snapshot](" + prefix + value["context_digest"] + ".json)",
                  "Assessment: [record](" + prefix + record["digest"] + ".json)",
                  "Independent review: [record](" + prefix + s.digest(record["review"]) + ".json)",
                  "What changed: " + value["what_changed"],
                  "Remaining obligations: " + json.dumps(value["remaining_obligation_ids"]), ""]
        for row in rows:
            lines += ["- {} / {}: {}. {}".format(row["node_id"], row["strategy"], row["disposition"], row["reason"]),
                      "  Next action: " + row["next_action"], "  Evidence: " + json.dumps(row["evidence_ids"])]
        lines += ["", "Continuing order: " + json.dumps(value["strategy_order"]),
                  "Considered failures: " + json.dumps(value["considered_failure_ids"]), ""]
        for hypothesis in value["next_hypotheses"]:
            lines += ["- Hypothesis: " + hypothesis["statement"],
                      "  Success criterion: " + hypothesis["success_criterion"],
                      "  Failure signal: " + hypothesis["failure_signal"]]
        if value["no_new_hypothesis_reason"] is not None:
            lines.append("Existing hypotheses retained because: " + value["no_new_hypothesis_reason"])
    if failures:
        lines += ["", "### Retained strategy failures", ""]
        lines += ["- {}: {}".format(identity, failure["observation"]) for identity, failure in failures]
    return lines


def replace_text(path, text, *, writable=False):
    replace_bytes(path, text.encode("utf-8"), writable=writable)


def replace_bytes(path, data, *, writable=False):
    if path.is_symlink():
        raise SearchError("unsafe_path", "Generated views cannot be symlinks")
    try:
        info = path.stat()
    except FileNotFoundError:
        pass
    else:
        if bool(info.st_mode & 0o222) == writable and path.read_bytes() == data:
            return
    write_atomic(path, data, mode=0o600 if writable else 0o400)


def render(root, state):
    from .strategy_refresh import assessment_status
    root = Path(root)
    status = assessment_status(state)
    checkpoints_by_node, assessments_by_node, failures_by_node = {}, {}, {}
    for checkpoint in state["checkpoints"].values():
        checkpoints_by_node.setdefault(checkpoint["origin"].get("node_id"), []).append(checkpoint)
    policy = state["control"]["strategy_refresh"]
    assessments = []
    for record in policy["assessments"]:
        rows, rows_by_node = [], {}
        for row in record["assessment"]["assessments"]:
            rows.append(row)
            rows_by_node.setdefault(row["node_id"], []).append(row)
        assessments.append((record, rows))
        for node_id, node_rows in rows_by_node.items():
            assessments_by_node.setdefault(node_id, []).append((record, node_rows))
    failures = list(policy["failures"].items())
    for identity, failure in failures:
        failures_by_node.setdefault(failure["node_id"], []).append((identity, failure))
    deferred_node_ids = set(state["control"]["deferred_node_ids"])
    claim = state["contract"]["original_claim"]
    lines = ["# Mathematical search tree", "", "Generated from .search/tree.json; do not edit.", "",
             "Objective: " + state["objective_id"], "", claim["statement"], "",
             "Quantifiers: " + claim["quantifiers"],
             "Assumptions: " + json.dumps(claim["assumption_ids"]),
             "Proof policy: " + state["contract"]["proof_policy"],
             "Recorded proof status: " + state["proof_status"],
             "Execution status: " + state["execution_status"],
             "Freshness: unchecked by this generated view.", "", "## Residual obligations", ""]
    remaining = [o for oid, o in state["obligations"].items() if obligation_support(state, oid) is None]
    lines += ["- {}: {}".format(o["id"], o["claim"]["statement"]) for o in remaining]
    if not remaining:
        lines.append("No recorded residual obligations. Completion still requires its audited closure.")
    for rid, route in state["routes"].items():
        parent = state["obligations"][route["conclusion"]]["claim"]
        if parent["scope"]["kind"] != "named":
            count = coverage(state, rid)
            lines.extend(["", "{}: {} of {} cases accepted; remaining {}.".format(
                rid, count["accepted"], count["total"], json.dumps(count["remaining"]))])
    lines += ["", "## Investigations", ""]
    for node in state["nodes"].values():
        workspace = safe_path(root, node["attack_slug"])
        lines.append("- {} ({}): {}; obligation {}; account {}.".format(
            node["id"], node["attack_slug"], node["status"], node["obligation_id"], node["account_id"]))
        deferred = node["id"] in deferred_node_ids
        if deferred:
            lines.append("  {}: deferred from new research selection; its obligation is retained.".format(node["id"]))
        lineage = ["# Investigation lineage", "", "Generated from .search/tree.json; do not edit.", "",
                   "Node: " + node["id"], "Attack: " + node["attack_slug"],
                   "Claim: " + node["claim"]["statement"], "Status: " + node["status"],
                   "Deferred from new research selection: " + ("yes" if deferred else "no"),
                   "Obligation: " + str(node["obligation_id"]), "Relationship: " + node["relationship"],
                   "Logical predecessor: " + str(node["logical_predecessor"]),
                   "Predecessor checkpoint: " + str(node["checkpoint_id"]),
                   "Native parent: " + str(node["native_parent"]), "Account: " + node["account_id"],
                   "", "Local finish does not imply objective completion.", "", "## Checkpoints", ""]
        for cp in checkpoints_by_node.get(node["id"], []):
            lineage.extend(["- {}: {}".format(cp["id"], cp["what_changed"]),
                            "  Remaining obligations: " + json.dumps(cp["remaining_obligation_ids"]),
                            "  Next hypothesis: " + cp["next_hypothesis"]])
        lineage.extend(strategy_assessment_lines(state, node["id"], status=status,
                                                assessments=assessments_by_node.get(node["id"], []),
                                                failures=failures_by_node.get(node["id"], [])))
        if workspace.is_dir():
            replace_text(safe_path(workspace, "LINEAGE.md"), "\n".join(lineage) + "\n")
    lines.extend(strategy_assessment_lines(state, status=status, assessments=assessments, failures=failures))
    replace_text(safe_path(root, "SEARCH_TREE.md"), "\n".join(lines) + "\n")
