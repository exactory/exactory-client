"""Deterministic, recoverable Markdown projections of controller state."""

import json
import os
from pathlib import Path
import tempfile

from .proof import coverage, obligation_support
from .storage import safe_path, Store
from .errors import SearchError


def replace_text(path, text):
    if path.is_symlink():
        raise SearchError("unsafe_path", "Generated views cannot be symlinks")
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
        Store._fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render(root, state):
    root = Path(root)
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
        lineage = ["# Investigation lineage", "", "Generated from .search/tree.json; do not edit.", "",
                   "Node: " + node["id"], "Attack: " + node["attack_slug"],
                   "Claim: " + node["claim"]["statement"], "Status: " + node["status"],
                   "Obligation: " + str(node["obligation_id"]), "Relationship: " + node["relationship"],
                   "Logical predecessor: " + str(node["logical_predecessor"]),
                   "Predecessor checkpoint: " + str(node["checkpoint_id"]),
                   "Native parent: " + str(node["native_parent"]), "Account: " + node["account_id"],
                   "", "Local finish does not imply objective completion.", "", "## Checkpoints", ""]
        for cp in state["checkpoints"].values():
            if cp["origin"].get("node_id") == node["id"]:
                lineage.extend(["- {}: {}".format(cp["id"], cp["what_changed"]),
                                "  Remaining obligations: " + json.dumps(cp["remaining_obligation_ids"]),
                                "  Next hypothesis: " + cp["next_hypothesis"]])
        if workspace.is_dir():
            replace_text(safe_path(workspace, "LINEAGE.md"), "\n".join(lineage) + "\n")
    replace_text(safe_path(root, "SEARCH_TREE.md"), "\n".join(lines) + "\n")
