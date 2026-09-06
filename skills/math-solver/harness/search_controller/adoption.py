"""Explicit preserved legacy imports and reviewed verification allowances."""

import copy
import os
from pathlib import Path

from . import schema as s
from .admission import allocate, reference, record_proposal, record_review, admit_proposal, _current_account
from .proof import validate_review
from .storage import safe_path, _strict_json


def snapshot_workspace(root, slug, paths, content):
    workspace = safe_path(root, slug)
    s.require(workspace.is_dir(), "Legacy attack directory is missing", "adoption_required")
    s.strings(paths, True)
    relevant, omitted = set(), []
    for directory, folders, filenames in os.walk(str(workspace), followlinks=False):
        parent = safe_path(workspace, str(Path(directory).relative_to(workspace)) or ".")
        retained = []
        for name in folders:
            relative = str((parent / name).relative_to(workspace))
            if name.startswith(".") or name in {"__pycache__", "node_modules", "build", "dist"}:
                omitted.append(relative)
            else:
                s.require(not (parent / name).is_symlink(), "Snapshot directory cannot be a symlink", "unsafe_path")
                retained.append(name)
        folders[:] = retained
        for name in filenames:
            relative = str((parent / name).relative_to(workspace))
            if name.startswith("."):
                omitted.append(relative)
            else:
                relevant.add(relative)
    s.require(set(paths) == relevant, "Snapshot must enumerate every relevant research file exactly; caches and hidden files are excluded", "snapshot_incomplete")
    files = []
    for relative in sorted(paths):
        path = safe_path(workspace, relative)
        s.require(not path.is_symlink(), "Legacy snapshots require real files", "unsafe_path")
        files.append({"path": relative, "digest": content.put_artifact(path.read_bytes())})
    return {"files": files, "omitted_paths": sorted(omitted)}


def build_import(root, state, mapping, content):
    s.closed(mapping, "attack_slug claim target_obligation logical_predecessor snapshot_digest snapshot_paths usage review verification")
    subject = {key: copy.deepcopy(value) for key, value in mapping.items() if key not in {"review", "verification"}}
    s.slug(subject["attack_slug"])
    s.validate_claim(subject["claim"])
    old = next((v for v in state["service"]["imports"].values()
                if v["subject"]["attack_slug"] == subject["attack_slug"]), None)
    if old is not None:
        versions = [v for v in state["service"]["import_versions"] if v["import_id"] == old["id"]]
        previous = versions[-1] if versions else old
        if previous["subject"] == subject:
            return None
        for field in ["attack_slug", "claim", "target_obligation", "logical_predecessor"]:
            s.require(subject[field] == old["subject"][field], "An import amendment cannot change its immutable mapping", "adoption_conflict")
    snapshot = snapshot_workspace(root, subject["attack_slug"], subject["snapshot_paths"], content)
    s.require(content.put_blob(snapshot) == subject["snapshot_digest"], "Adoption review snapshot differs", "digest_mismatch")
    validate_review(mapping["review"], s.digest(subject), s.digest(subject["claim"]))
    content.put_blob(mapping["review"])
    files = {v["path"]: v["digest"] for v in snapshot["files"]}
    s.require("problem.json" in files and "journal.jsonl" in files, "Legacy problem and journal are required")
    journal = content.get_artifact(files["journal.jsonl"])
    if old is not None:
        original = content.get_blob(previous["subject"]["snapshot_digest"])
        previous_files = {v["path"]: v["digest"] for v in original["files"]}
        s.require(journal.startswith(content.get_artifact(previous_files["journal.jsonl"])),
                  "An import amendment cannot remove or rewrite preserved journal history", "adoption_conflict")
        for path in ["parent.json", "units/FINISHED.json"]:
            s.require(files.get(path) == previous_files.get(path), "Native parent and historical FINISHED bytes are immutable", "adoption_conflict")
    moves = [_strict_json(line, "invalid_input") for line in journal.splitlines() if line.strip()]
    usage = subject["usage"]
    s.closed(usage, "moves runs")
    for resource in ["moves", "runs"]:
        if usage[resource] is not None:
            s.integer(usage[resource])
    s.require(usage["moves"] is None or usage["moves"] >= len(moves), "Known usage omits journalled moves")
    parent = None
    if "parent.json" in files:
        native = _strict_json(content.get_artifact(files["parent.json"]), "invalid_input")
        s.closed(native, "parent opened_after_move")
        s.slug(native["parent"])
        s.integer(native["opened_after_move"])
        parent = next((n["id"] for n in state["nodes"].values() if n["attack_slug"] == native["parent"]), None)
        s.require(parent is not None and state["nodes"][parent]["native_parent"] is None,
                  "Import native parents before their depth-one children")
    record = {"subject": subject, "review": mapping["review"], "native_parent": parent,
              "finished": "units/FINISHED.json" in files, "journal_moves": len(moves)}
    return dict(record, import_id=old["id"]) if old is not None else record


def record_import(state, payload):
    s.closed(payload, "subject review native_parent finished journal_moves")
    subject = payload["subject"]
    s.closed(subject, "attack_slug claim target_obligation logical_predecessor snapshot_digest snapshot_paths usage")
    s.strings(subject["snapshot_paths"], True)
    s.slug(subject["attack_slug"])
    s.validate_claim(subject["claim"])
    s.digest_string(subject["snapshot_digest"])
    validate_review(payload["review"], s.digest(subject), s.digest(subject["claim"]))
    s.require(type(payload["finished"]) is bool, "Historical finish observation must be boolean")
    s.integer(payload["journal_moves"])
    s.closed(subject["usage"], "moves runs")
    for value in subject["usage"].values():
        if value is not None:
            s.integer(value)
    s.require(subject["usage"]["moves"] is None or subject["usage"]["moves"] >= payload["journal_moves"], "Imported move usage is incomplete")
    target = subject["target_obligation"]
    if target is not None:
        obligation = reference(state["obligations"], target, "import obligation")
        s.require(obligation["claim"] == subject["claim"], "Historical claim differs from mapped obligation", "claim_mismatch")
    for parent in [subject["logical_predecessor"], payload["native_parent"]]:
        if parent is not None:
            reference(state["nodes"], parent, "import predecessor")
    s.require(not any(n["attack_slug"] == subject["attack_slug"] for n in state["nodes"].values()), "Attack is already mapped")
    iid = "import-{:06d}".format(len(state["service"]["imports"]) + 1)
    nid = allocate(state, "node")
    equivalent = next((v for v in state["service"]["imports"].values()
                       if s.claim_identity(v["subject"]["claim"]) == s.claim_identity(subject["claim"])), None)
    if equivalent is not None:
        aid = equivalent["account_id"]
        account = state["accounts"][aid]
        for resource in ["moves", "runs"]:
            observed = subject["usage"][resource]
            lower_bound = max(observed or 0, payload["journal_moves"] if resource == "moves" else 0)
            increase = max(0, lower_bound - account["used_" + resource])
            account["used_" + resource] += increase
            state["totals"]["used_" + resource] += increase
            if observed is None:
                account["historical_usage"] = state["totals"]["historical_usage"] = "unknown"
                account["historical_moves"] = account["historical_runs"] = None
    else:
        aid = allocate(state, "account")
        usage = subject["usage"]
        known = all(value is not None for value in usage.values())
        state["accounts"][aid] = {"id": aid, "schema_version": 1, "owner_obligation": target,
            "owner_claim_digest": s.digest(subject["claim"]), "lineage_owner": aid, "predecessor_account_id": None,
            "max_moves": 24, "max_runs": 24, "used_moves": max(usage["moves"] or 0, payload["journal_moves"]), "used_runs": usage["runs"] or 0,
            "reserved_moves": 0, "reserved_runs": 0, "historical_usage": "known" if known else "unknown",
            "historical_moves": 0 if known else None, "historical_runs": 0 if known else None,
            "renewal_basis": None, "renewal_basis_digest": None}
        for resource in ["moves", "runs"]:
            state["totals"]["used_" + resource] += state["accounts"][aid]["used_" + resource]
        if not known:
            state["totals"]["historical_usage"] = "unknown"
    state["nodes"][nid] = {"id": nid, "schema_version": 1, "attack_slug": subject["attack_slug"],
        "obligation_id": target, "claim": copy.deepcopy(subject["claim"]), "claim_digest": s.digest(subject["claim"]),
        "role": "research", "category": "main" if target else "standalone", "relationship": "alternative" if target else "standalone",
        "logical_predecessor": subject["logical_predecessor"], "native_parent": payload["native_parent"],
        "checkpoint_id": None, "proposal_id": None, "account_id": aid, "status": "finished" if payload["finished"] else "imported",
        "route_id": None, "checkpoint_criteria": [], "retreat_criteria": [], "admission": None, "import_id": iid}
    state["service"]["imports"][iid] = dict(copy.deepcopy(payload), id=iid, node_id=nid, account_id=aid)


def record_allowance(state, payload):
    s.closed(payload, "import_id proposal review allowance_review")
    imported = reference(state["service"]["imports"], payload["import_id"], "legacy import")
    proposal = payload["proposal"]
    s.validate_proposal(proposal)
    s.require(proposal["role"] == "verification" and proposal["claim"] == imported["subject"]["claim"],
              "Adoption allowance is only for verification of the preserved exact claim")
    s.require(proposal["budget"]["mode"] != "renew", "First verification is not a progress renewal")
    versions = [v for v in state["service"]["import_versions"] if v["import_id"] == imported["id"]]
    source = versions[-1] if versions else imported
    subject = {"import_id": imported["id"], "snapshot_digest": source["subject"]["snapshot_digest"],
               "claim_digest": s.digest(proposal["claim"]), "proposal_digest": s.digest(proposal), "limits": proposal["limits"]}
    validate_review(payload["allowance_review"], s.digest(subject), s.digest(proposal["claim"]))
    author = proposal["author"]
    reviewer = payload["allowance_review"]["reviewer"]
    s.require(author["actor_id"] != reviewer["actor_id"] and author["attestation_id"] != reviewer["attestation_id"],
              "Verification author cannot approve its allowance", "review_not_independent")
    current = _current_account(state, imported["account_id"])
    if current.get("adoption_basis") is None:
        aid = allocate(state, "account")
        known = current["historical_usage"] == "known"
        account = {"id": aid, "schema_version": 1, "owner_obligation": imported["subject"]["target_obligation"],
            "owner_claim_digest": s.digest(proposal["claim"]), "lineage_owner": current["lineage_owner"],
            "predecessor_account_id": current["id"], "max_moves": proposal["limits"]["max_moves"], "max_runs": proposal["limits"]["max_runs"],
            "used_moves": 0, "used_runs": 0, "reserved_moves": 0, "reserved_runs": 0,
            "historical_usage": current["historical_usage"],
            "historical_moves": current["historical_moves"] + current["used_moves"] if known else None,
            "historical_runs": current["historical_runs"] + current["used_runs"] if known else None,
            "renewal_basis": None, "renewal_basis_digest": None, "role": "verification",
            "adoption_basis": {"import_id": imported["id"], "snapshot_digest": subject["snapshot_digest"],
                               "review_digest": s.digest(payload["allowance_review"]), "claim_digest": subject["claim_digest"]},
            "adoption_proposal_digests": []}
        state["accounts"][aid] = account
    else:
        account = current
        s.require(proposal["limits"]["max_moves"] <= account["max_moves"] and proposal["limits"]["max_runs"] <= account["max_runs"],
                  "Reimport cannot increase the verification allowance", "budget_exhausted")
    proposal_digest = s.digest(proposal)
    if proposal_digest in account["adoption_proposal_digests"]:
        record = dict(copy.deepcopy(payload), snapshot_digest=subject["snapshot_digest"])
        if record not in state["service"]["adoption_allowances"]:
            node = next(n for n in state["nodes"].values() if n["proposal_id"] is not None
                        and state["proposals"][n["proposal_id"]]["digest"] == proposal_digest)
            s.require(node["status"] != "finished", "Finished verification needs a new reviewed node on the same account", "terminal_attack")
            state["service"]["adoption_allowances"].append(record)
        return
    account["adoption_proposal_digests"].append(proposal_digest)
    record_proposal(state, {"proposal": proposal, "digest": proposal_digest})
    pid = list(state["proposals"])[-1]
    record_review(state, {"proposal_id": pid, "review": payload["review"], "digest": s.digest(payload["review"])})
    admit_proposal(state, {"proposal_id": pid})
    state["service"]["adoption_allowances"].append(dict(copy.deepcopy(payload), snapshot_digest=subject["snapshot_digest"]))


def record_import_version(state, payload):
    s.closed(payload, "import_id subject review native_parent finished journal_moves")
    imported = reference(state["service"]["imports"], payload["import_id"], "legacy import")
    subject = payload["subject"]
    s.closed(subject, "attack_slug claim target_obligation logical_predecessor snapshot_digest snapshot_paths usage")
    for field in ["attack_slug", "claim", "target_obligation", "logical_predecessor"]:
        s.require(subject[field] == imported["subject"][field], "An import version cannot change its immutable mapping")
    s.require(payload["native_parent"] == imported["native_parent"] and payload["finished"] == imported["finished"],
              "An import version cannot alter native lifecycle history")
    s.digest_string(subject["snapshot_digest"])
    s.strings(subject["snapshot_paths"], True)
    s.integer(payload["journal_moves"])
    s.require(payload["journal_moves"] >= imported["journal_moves"], "Import versions cannot remove journal history")
    validate_review(payload["review"], s.digest(subject), s.digest(subject["claim"]))
    account = state["accounts"][imported["account_id"]]
    s.closed(subject["usage"], "moves runs")
    for resource in ["moves", "runs"]:
        observed = subject["usage"][resource]
        if observed is not None:
            s.integer(observed)
            s.require(observed >= account["used_" + resource], "Import versions cannot reduce recorded resource usage")
            state["totals"]["used_" + resource] += observed - account["used_" + resource]
            account["used_" + resource] = observed
        else:
            account["historical_usage"] = state["totals"]["historical_usage"] = "unknown"
            account["historical_moves"] = account["historical_runs"] = None
    increment = max(0, payload["journal_moves"] - account["used_moves"])
    account["used_moves"] += increment
    state["totals"]["used_moves"] += increment
    state["service"]["import_versions"].append(copy.deepcopy(payload))


EVENT_HANDLERS = {"legacy_imported": record_import, "adoption_allowance_recorded": record_allowance,
                  "legacy_import_version_recorded": record_import_version}
