"""Immutable input loading and filesystem audits; never launches processes."""

import hashlib
import os
from pathlib import Path
import shutil

from . import schema as s
from .admission import reference, resolve_proposal_account, validate_proposal_context
from .errors import SearchError
from .storage import safe_path, _strict_json


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_lean_inspection(output, declaration):
    """Capture the complete delimited type and both exact axiom records."""
    import re
    blocks = re.findall(r"^EXACTORY_TYPE_BEGIN\n(.*?)^EXACTORY_TYPE_END\s*$", output, re.MULTILINE | re.DOTALL)
    s.require(output.count("EXACTORY_TYPE_BEGIN") == 1 and output.count("EXACTORY_TYPE_END") == 1 and len(blocks) == 1,
              "Inspection must contain one complete printed type block", "verification_failed")
    printed = blocks[0].rstrip("\n")
    s.require(re.match(re.escape(declaration) + r"(?:\s|\.\{)", printed) is not None and ":" in printed,
              "Printed type must identify exactly the requested declaration", "verification_failed")
    records = {}
    for key, name in [("declaration_axioms", declaration), ("correspondence_axioms", "exactory_correspondence")]:
        pattern = r"^\s*(?:'|`)?" + re.escape(name) + r"(?:'|`)? (?:depends on axioms: \[([^\]]*)\]|(does not depend on any axioms))\s*$"
        matches = re.findall(pattern, output, re.MULTILINE)
        s.require(len(matches) == 1, "Inspection must identify exactly the requested declaration and its correspondence theorem", "verification_failed")
        records[key] = [item.strip() for item in matches[0][0].split(",") if item.strip()]
    return dict(records, printed_type=printed)


def installed_lean(project, search_path):
    """Resolve an existing installation without invoking elan or installing tools."""
    selected = shutil.which("lake", path=search_path)
    s.require(selected is not None, "Lake is not installed", "missing_toolchain")
    executable = Path(selected).absolute()
    elan = executable.parent / "elan"
    if elan.exists() and os.path.samefile(str(executable), str(elan)):
        version = (project / "lean-toolchain").read_text().strip()
        s.require(version.startswith("leanprover/lean4:") and ".." not in version,
                  "The exact installed Lean toolchain must be named", "missing_toolchain")
        installation = safe_path(executable.parent.parent / "toolchains", version.replace("/", "--").replace(":", "---"))
        executable = installation / "bin/lake"
        s.require(executable.is_file() and (installation / "bin/lean").is_file(),
                  "The requested Lean toolchain is unavailable; no download was started", "missing_toolchain")
    else:
        installation = executable.parent.parent if executable.parent.name == "bin" and (executable.parent / "lean").exists() else executable.parent
    return executable.resolve(), installation.resolve()


def capture_toolchain(root):
    root = Path(root).resolve()
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            s.require(not path.is_dir(), "Symlinked toolchain directories need explicit supported resolution", "incomplete_dependencies")
            try:
                path.resolve().relative_to(root)
            except ValueError as error:
                raise SearchError("incomplete_dependencies", "Toolchain symlink escapes its installation") from error
        if path.is_file():
            files.append({"path": str(path.relative_to(root)), "digest": file_digest(path)})
    s.require(bool(files), "Toolchain inventory is empty", "incomplete_dependencies")
    return {"root": str(root), "files": files}


def audit_toolchain(inventory):
    s.closed(inventory, "root files")
    s.require(Path(inventory["root"]).is_absolute(), "Toolchain root must be absolute")
    for item in s.records(inventory["files"]):
        s.closed(item, "path digest")
        safe_path(Path(inventory["root"]), item["path"])
        s.digest_string(item["digest"])
    s.require(capture_toolchain(Path(inventory["root"])) == inventory,
              "Installed toolchain membership or bytes changed", "digest_mismatch")


def freeze_execution_inputs(root, node, spec, content):
    """Capture the enumerated local source boundary before reserving a process."""
    step = safe_path(root, node["attack_slug"] + "/deterministic/" + spec["step_dir"])
    s.require(step.is_dir(), "Execution step directory is missing", "missing_evidence")
    s.text(spec["dependency_enumeration"])
    paths = set()
    for item in s.records(spec["artifacts"]):
        s.closed(item, "path digest role")
        path = safe_path(root, item["path"])
        s.require(item["path"] not in paths and path.is_file(), "Input is missing or duplicated", "missing_evidence")
        paths.add(item["path"])
        s.require(content.put_artifact(path.read_bytes()) == item["digest"], "Execution input changed", "digest_mismatch")
        s.choice(item["role"], {"proof", "source", "study", "certificate", "checker", "theorem", "toolchain", "input"})
    required = set()
    for path in step.rglob("*"):
        relative = path.relative_to(step)
        if any(part in {".lake", ".git", "__pycache__"} for part in relative.parts):
            continue
        s.require(not path.is_symlink(), "Execution source boundary contains a symlink", "unsafe_path")
        if path.is_file() and relative.as_posix() not in {"result.json", "inspection.log", "axioms-check.lean", "verification-review.json"}:
            required.add(str(path.relative_to(root)))
    s.require(required <= paths and bool(required), "Execution manifest omits local source dependencies", "incomplete_dependencies")
    external = []
    for item in s.records(spec["external_dependencies"]):
        s.closed(item, "path digest")
        path = Path(item["path"])
        s.require(path.is_absolute() and path.is_file() and not path.is_symlink(), "External dependency is not a regular absolute file")
        s.require(file_digest(path) == item["digest"], "External dependency changed", "digest_mismatch")
        external.append(item)
    frozen = {"schema_version": 1, "claim_digest": node["claim_digest"],
              "artifacts": spec["artifacts"], "external_dependencies": external}
    return content.put_blob(frozen)


def import_inputs(root, inputs, content):
    seen = set()
    for item in s.records(inputs):
        s.closed(item, "path digest kind")
        s.choice(item["kind"], {"artifact", "blob"})
        s.digest_string(item["digest"])
        path = safe_path(root, item["path"])
        key = (item["kind"], item["digest"])
        s.require(key not in seen, "Input is duplicated")
        seen.add(key)
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise SearchError("missing_evidence", "Cannot read input", {"path": item["path"]}) from error
        if item["kind"] == "blob":
            value = _strict_json(raw, "invalid_input")
            s.require(s.digest(value) == item["digest"], "Input digest differs", "digest_mismatch")
            content.put_blob(value)
        else:
            s.require(hashlib.sha256(raw).hexdigest() == item["digest"], "Input digest differs", "digest_mismatch")
            content.put_artifact(raw)


def proposal_inputs(proposal, content):
    if proposal.get("computation") is not None:
        from .computation_io import computation_inputs
        computation_inputs(proposal["computation"], content)
    studies = proposal["studies"]
    required = [studies["problem"], studies["novelty"]] + [v["digest"] for v in studies["strategies"]]
    standalone = proposal["contribution"]["standalone"]
    if standalone is not None:
        required += standalone["primary_sources"]
    for digest in required:
        s.require(bool(content.get_artifact(digest).strip()), "Study or source is empty", "missing_evidence")
    for digest in proposal["inherited_evidence"]:
        content.get_blob(digest)


def manifest_closure(digests, content):
    """Load the exact bounded manifest graph, rejecting cycles."""
    manifests = {}
    def visit(digest, visiting):
        s.require(digest not in visiting, "Evidence dependency cycle", "dependency_cycle")
        if digest in manifests:
            return
        s.require(len(manifests) < 256, "Evidence dependency closure is excessive", "dependency_cycle")
        manifest = content.get_blob(digest)
        manifests[digest] = manifest
        s.strings(manifest["dependencies"])
        for child in manifest["dependencies"]:
            visit(child, visiting | {digest})
    for digest in digests:
        visit(digest, set())
    return manifests


def audit_admission(root, state, proposal, content):
    """Revalidate the proposal's evidence and accepted budget basis under lock."""
    s.validate_proposal(proposal)
    _, target = validate_proposal_context(state, proposal, None)
    if proposal.get("computation") is not None:
        from .computation_io import audit_computation
        audit_computation(root, state, proposal, proposal["computation"], content)
    account = resolve_proposal_account(state, proposal, target)
    checkpoint_ids = set()
    if proposal["anchor"]["kind"] == "checkpoint":
        checkpoint_ids.add(proposal["anchor"]["checkpoint_id"])
    if proposal["budget"]["mode"] == "renew":
        checkpoint_ids.add(proposal["budget"]["basis_checkpoint_id"])
    elif account is not None and account["renewal_basis"] is not None:
        checkpoint_ids.add(account["renewal_basis"])
    manifests = list(proposal["inherited_evidence"])
    for identity in checkpoint_ids:
        checkpoint = reference(state["checkpoints"], identity, "admission checkpoint")
        audit_checkpoint(root, state, checkpoint, content, verify=False)
        manifests.extend(checkpoint["evidence_digests"])
    for digest in proposal["inherited_evidence"]:
        audit_manifest(root, state, digest, content, verify=False)
    pending = [identity for identity, accepted in state["acceptances"].items()
               if accepted["status"] == "accepted" and accepted["checkpoint_id"] in checkpoint_ids]
    checked = set()
    while manifests or pending:
        for manifest in manifest_closure(manifests, content).values():
            pending.extend(manifest["conclusion"]["dependency_ids"])
        manifests = []
        if not pending:
            break
        identity = pending.pop()
        if identity in checked:
            continue
        accepted = state["acceptances"].get(identity)
        s.require(accepted is not None and accepted["status"] == "accepted", "Inherited acceptance is unavailable", "audit_failed")
        s.require(content.get_blob(s.digest(accepted["review"])) == accepted["review"], "Pinned review differs", "digest_mismatch")
        checkpoint = reference(state["checkpoints"], accepted["checkpoint_id"], "accepted checkpoint")
        audit_checkpoint(root, state, checkpoint, content)
        checked.add(identity)
        pending.extend(accepted["dependency_ids"])
        manifests.extend(checkpoint["evidence_digests"])


def audit_state(root, state, content):
    """Return failed accepted identities without changing recorded history."""
    failures = {}
    for identity, accepted in state["acceptances"].items():
        if accepted["status"] != "accepted":
            continue
        try:
            content.get_blob(s.digest(accepted["review"]))
            checkpoint = state["checkpoints"][accepted["checkpoint_id"]]
            audit_checkpoint(root, state, checkpoint, content, verify=True)
            s.require(content.get_blob(s.digest(accepted["review"])) == accepted["review"], "Pinned review differs", "digest_mismatch")
        except SearchError as error:
            failures[identity] = {"code": error.code, "message": error.message}
    closure = state["control"]["closure"]
    if closure is not None and state["proof_status"] in {"proved", "disproved"}:
        try:
            s.require(content.get_blob(s.digest(closure["review"])) == closure["review"], "Final review differs", "digest_mismatch")
            for item in closure["subject"]["deliverables"]:
                content.get_artifact(item["digest"])
            for delivery in closure["subject"]["local_deliveries"]:
                audit_local_delivery(root, state, delivery, content)
        except SearchError as error:
            for identity in closure["subject"]["acceptance_ids"]:
                failures[identity] = {"code": error.code, "message": "Final package audit: " + error.message}
    return failures


def checked_file(workspace, relative, digest, content):
    expected = content.get_artifact(digest)
    s.require(bool(expected.strip()), "Required delivery artifact is empty", "delivery_required")
    try:
        actual = safe_path(workspace, relative).read_bytes()
    except OSError as error:
        raise SearchError("delivery_required", "Required delivery artifact is missing", {"path": relative}) from error
    s.require(actual == expected, "Delivery artifact changed: " + relative, "digest_mismatch")
    return expected


def audit_local_delivery(root, state, delivery, content):
    """Validate native cash-out without writing stamps or launching proof jobs."""
    import attack
    s.closed(delivery, "node_id inventory_digest checked_unit_digests consolidation_digest draft_digests evaluation_digests finish handoff_digest")
    node = state["nodes"].get(delivery["node_id"])
    s.require(node is not None and node["proposal_id"] is not None, "Historical imports cannot supply new local delivery", "admission_required")
    workspace = safe_path(root, node["attack_slug"])
    try:
        moves = attack.read_journal(workspace)
        s.require(attack.find_cash_out_rule(workspace, moves) is not None,
                  "No native stage-seven cash-out trigger permits this delivery", "delivery_required")
        checked_file(workspace, "units/INVENTORY.md", delivery["inventory_digest"], content)
        checked_file(workspace, "units/consolidation.md", delivery["consolidation_digest"], content)
        checked_file(workspace, "HANDOFF.md", delivery["handoff_digest"], content)
        numbers = attack.list_unit_numbers(workspace)
        packages = [content.get_blob(digest) for digest in delivery["checked_unit_digests"]]
        s.require(len(packages) == len(numbers), "Every applicable publication unit needs an exact checked package", "delivery_required")
        drafts, evaluations, package_evidence = [], [], {}
        for number, package in zip(numbers, packages):
            s.closed(package, "schema_version node_id unit_number files")
            s.integer(package["schema_version"], 1, 1)
            s.require(package["node_id"] == node["id"] and package["unit_number"] == number, "Unit package identity differs")
            prefix = "units/{}/".format(number)
            files = {}
            for artifact in s.records(package["files"]):
                s.closed(artifact, "path digest")
                s.require(artifact["path"] not in files, "Unit package has duplicate files")
                files[artifact["path"]] = artifact["digest"]
                checked_file(workspace, artifact["path"], artifact["digest"], content)
            required = {prefix + name for name in ["unit.json", "check-unit.json", "draft.md", "evaluation.md"]}
            s.require(required <= set(files), "Unit package omits its check, draft or evaluation", "delivery_required")
            unit = _strict_json(content.get_artifact(files[prefix + "unit.json"]), "invalid_input")
            evidence = safe_path(workspace, unit["evidence"])
            if evidence.is_dir():
                required_evidence = set()
                for path in evidence.rglob("*"):
                    if path.is_file() and not any(part.startswith(".") for part in path.relative_to(evidence).parts):
                        required_evidence.add(str(path.relative_to(workspace)))
            else:
                required_evidence = {unit["evidence"]}
            s.require(required_evidence <= set(files), "Unit package omits proof evidence inputs", "delivery_required")
            defects = (list(attack.find_unit_defects(unit, workspace, moves))
                       + list(attack.find_form_defects(unit, workspace))
                       + list(attack.find_finished_unit_defects(workspace, number)))
            s.require(not defects, "Local unit checks failed: " + "; ".join(defects), "delivery_required")
            drafts.append(files[prefix + "draft.md"])
            evaluations.append(files[prefix + "evaluation.md"])
            package_evidence.update(files)
        s.require(drafts == delivery["draft_digests"] and evaluations == delivery["evaluation_digests"],
                  "Delivery lists differ from their exact checked unit packages", "digest_mismatch")
        required_versions = {}
        for acceptance in state["acceptances"].values():
            if acceptance["status"] != "accepted":
                continue
            cp = state["checkpoints"][acceptance["checkpoint_id"]]
            if cp["origin"].get("node_id") != node["id"]:
                continue
            for manifest in manifest_closure(cp["evidence_digests"], content).values():
                required_artifacts = list(manifest["artifacts"])
                if manifest.get("verification") is not None:
                    run = reference(state["runs"], manifest["verification"]["run_id"], "accepted workload")
                    s.require(run.get("legacy_result") is not None, "Accepted native result publication is missing", "delivery_required")
                    required_artifacts.extend(run[key] for key in ["legacy_result", "legacy_inspection"] if run.get(key) is not None)
                for artifact in required_artifacts:
                    source = safe_path(root, artifact["path"]).resolve()
                    try:
                        local = str(source.relative_to(workspace.resolve()))
                    except ValueError:
                        continue
                    s.require(local not in required_versions or required_versions[local] == artifact["digest"],
                              "Accepted evidence requires conflicting local versions: " + local, "conflicting_evidence_versions")
                    required_versions[local] = artifact["digest"]
        for local, digest in required_versions.items():
            s.require(package_evidence.get(local) == digest,
                      "Local delivery uses a different accepted evidence version: " + local, "digest_mismatch")
        unfinished = []
        for child in attack.list_children(root, node["attack_slug"]):
            if not (child / "units" / "FINISHED.json").exists():
                child_node = next((n for n in state["nodes"].values() if n["attack_slug"] == child.name), None)
                s.require(child_node is not None, "Unmapped native child prevents exact local delivery", "delivery_required")
                unfinished.append(child_node["id"])
        finish = delivery["finish"]
        s.closed(finish, "kind unused_child_ids")
        if finish["kind"] == "finished":
            s.require(not unfinished and not finish["unused_child_ids"], "Native children still prevent local finish", "delivery_required")
            actual = attack.read_json(workspace / "units" / "FINISHED.json")
            s.require(actual == {"outcome": "cashed-out", "units": numbers}, "Actual local finish differs", "delivery_required")
        else:
            s.require(finish["kind"] == "local_finish_pending_unused_children" and bool(unfinished)
                      and sorted(finish["unused_child_ids"]) == sorted(unfinished),
                      "Unused native children must be the sole remaining local finish refusal", "delivery_required")
            s.require(not (workspace / "units" / "FINISHED.json").exists(), "Pending-finish label conflicts with an existing finish")
    except (attack.ValidationError, KeyError, ValueError, OSError) as error:
        raise SearchError("delivery_required", "Local delivery validation failed", {"reason": str(error)}) from error


def audit_manifest(root, state, digest, content, claim_digest=None, verify=True, visiting=None):
    visiting = set() if visiting is None else visiting
    s.require(digest not in visiting and len(visiting) < 256, "Evidence dependency cycle or excessive depth", "dependency_cycle")
    manifest = content.get_blob(digest)
    s.closed(manifest, "schema_version kind claim_digest conclusion artifacts dependencies external_dependencies verification")
    s.integer(manifest["schema_version"], 1, 1)
    s.choice(manifest["kind"], {"analytical", "certificate", "lean"})
    s.digest_string(manifest["claim_digest"])
    conclusion = manifest["conclusion"]
    s.closed(conclusion, "outcome dependency_ids route_bindings")
    s.choice(conclusion["outcome"], {"proof", "counterexample", "reduction", "obstruction", "hypothesis"})
    s.strings(conclusion["dependency_ids"])
    s.records(conclusion["route_bindings"])
    if claim_digest is not None:
        s.require(manifest["claim_digest"] == claim_digest, "Evidence binds a different claim", "claim_mismatch")
    roles = set()
    paths = set()
    for artifact in s.records(manifest["artifacts"]):
        s.closed(artifact, "path digest role")
        safe_path(root, artifact["path"])
        s.require(artifact["path"] not in paths, "Manifest paths must be unique")
        paths.add(artifact["path"])
        s.choice(artifact["role"], {"proof", "source", "study", "certificate", "checker", "theorem", "toolchain", "input"})
        roles.add(artifact["role"])
        s.require(bool(content.get_artifact(artifact["digest"])), "Evidence artifact is empty", "missing_evidence")
    s.require(bool(paths), "Evidence manifest is empty")
    for dependency in s.records(manifest["external_dependencies"]):
        s.closed(dependency, "path digest")
        path = Path(dependency["path"])
        s.require(path.is_absolute(), "Shared read-only dependencies use explicit absolute paths")
        s.digest_string(dependency["digest"])
        try:
            actual = file_digest(path)
        except OSError as error:
            raise SearchError("missing_evidence", "Shared dependency is unavailable", {"path": str(path)}) from error
        s.require(actual == dependency["digest"], "Shared dependency changed", "digest_mismatch")
    s.strings(manifest["dependencies"])
    child_standards = []
    for child in manifest["dependencies"]:
        child_standards.append(audit_manifest(root, state, child, content, verify=verify, visiting=visiting | {digest}))
    if manifest["kind"] == "analytical":
        s.require(manifest["verification"] is None and "proof" in roles, "Analytical evidence requires its proof and no executable verification record")
        s.require(roles <= {"proof", "source", "study", "input"}, "Computational artifacts cannot be relabelled analytical")
        classification, standard = "analytical", "reviewed"
    else:
        required = {"certificate", "checker"} if manifest["kind"] == "certificate" else {"theorem", "toolchain"}
        s.require(required <= roles, "Computational manifest omits required evidence")
        classification, standard = "computational", "certificate" if manifest["kind"] == "certificate" else "lean-kernel"
        if verify:
            audit_verification(state, manifest, content)
    if any(kind == "computational" for kind, _ in child_standards):
        classification = "computational"
        s.require(manifest["kind"] != "analytical", "An analytical wrapper cannot certify a computational dependency")
    if verify and standard == "lean-kernel":
        s.require(all(policy == "lean-kernel" for _, policy in child_standards), "Kernel proof has a non-kernel dependency")
    return classification, standard


def audit_verification(state, manifest, content):
    """Consume Task 5 terminal execution records without executing a checker."""
    verification = manifest["verification"]
    s.closed(verification, "run_id result_digest policy_review requested_declaration requested_type_digest")
    run = state["runs"].get(verification["run_id"])
    s.require(run is not None and run.get("status") == "terminal", "Evidence needs its controlled terminal verification run", "verification_required")
    s.require(run.get("termination") == "exit" and run.get("kind") == manifest["kind"],
              "Execution failed or was not this verifier kind", "verification_failed")
    s.require(run.get("result_digest") == verification["result_digest"], "Terminal execution result differs", "digest_mismatch")
    result = content.get_blob(verification["result_digest"])
    s.closed(result, "schema_version run_id claim_digest input_digest commands declaration theorem_type_digest toolchain_digest")
    s.integer(result["schema_version"], 1, 1)
    s.require(result["run_id"] == verification["run_id"] and result["claim_digest"] == manifest["claim_digest"], "Verification targets different inputs", "claim_mismatch")
    s.require(run.get("input_digest") == result["input_digest"], "Run input snapshot differs", "digest_mismatch")
    inputs = content.get_blob(result["input_digest"])
    s.require(inputs == {"schema_version": 1, "claim_digest": manifest["claim_digest"],
                        "artifacts": manifest["artifacts"], "external_dependencies": manifest["external_dependencies"]},
              "Controlled run did not inspect these exact evidence artifacts", "digest_mismatch")
    from .proof import validate_review
    spec = content.get_blob(run["spec_digest"])
    s.require(spec["kind"] == run["kind"], "Run kind differs from its pinned spec", "digest_mismatch")
    s.require(spec["input_modes"] == run["input_modes"]
              and [item["path"] for item in run["input_modes"]] == [item["path"] for item in inputs["artifacts"]],
              "Run permissions differ from reviewed input provenance", "digest_mismatch")
    subject = {"node_id": run["node_id"], "task": run["task"],
               "spec_without_input_review": {key: value for key, value in spec.items() if key != "input_review"}}
    validate_review(spec["input_review"], s.digest(subject), manifest["claim_digest"])
    s.require(content.get_blob(s.digest(subject)) == subject and content.get_blob(s.digest(spec["input_review"])) == spec["input_review"],
              "Pinned input review differs", "digest_mismatch")
    validate_review(verification["policy_review"], verification["result_digest"], manifest["claim_digest"])
    commands = s.records(result["commands"])
    s.require(len(commands) == (2 if manifest["kind"] == "lean" else 1), "Verification command record is incomplete")
    s.require([command["argv"] for command in commands] == run["commands"],
              "Result commands differ from the reserved workload", "digest_mismatch")
    for command in commands:
        s.closed(command, "argv exit_code stdout_digest stderr_digest")
        s.require(bool(s.records(command["argv"])), "Verification argv is empty")
        for argument in command["argv"]:
            s.text(argument)
        s.require(type(command["exit_code"]) is int and command["exit_code"] == 0, "Verification process failed", "verification_failed")
        content.get_artifact(command["stdout_digest"])
        content.get_artifact(command["stderr_digest"])
    if manifest["kind"] == "lean":
        audit_toolchain(content.get_blob(run["toolchain_inventory_digest"]))
        import re
        import attack
        s.text(result["declaration"])
        s.require(result["declaration"] == verification["requested_declaration"] and
                  result["theorem_type_digest"] == verification["requested_type_digest"],
                  "Formal declaration or requested type differs", "claim_mismatch")
        content.get_artifact(result["toolchain_digest"])
        content.get_artifact(result["theorem_type_digest"])
        output = content.get_artifact(commands[1]["stdout_digest"]).decode("utf-8", errors="strict")
        captured = parse_lean_inspection(output, result["declaration"])
        inspection = run.get("inspection")
        s.require(inspection is not None and inspection["source_digest"] == run["inspection_source_digest"], "Missing bound inspection provenance", "verification_failed")
        s.require(content.get_artifact(inspection["printed_type_digest"]) == captured["printed_type"].encode()
                  and all(inspection[key] == captured[key] for key in ["declaration_axioms", "correspondence_axioms"]),
                  "Captured inspection facts differ", "digest_mismatch")
        from .execution import lean_inspection_source
        spec = content.get_blob(run["spec_digest"])
        step = next(item for item in inputs["artifacts"] if item["path"].endswith("/step.json"))
        description = _strict_json(content.get_artifact(step["digest"]), "corrupt_artifact")
        s.require(content.get_artifact(inspection["source_digest"]) == lean_inspection_source(description, spec["requested_type"]).encode(),
                  "Inspection source is not the exact native protocol", "digest_mismatch")
        axioms = set(captured["declaration_axioms"] + captured["correspondence_axioms"])
        s.require(axioms <= set(attack.STANDARD_AXIOMS), "Inspection includes forbidden axioms", "verification_failed")
    else:
        s.require(verification["requested_declaration"] is None and verification["requested_type_digest"] is None,
                  "Certificate verification cannot request a Lean declaration")
        s.require(result["declaration"] is None and result["theorem_type_digest"] is None and result["toolchain_digest"] is None,
                  "Certificate records cannot fabricate Lean declaration metadata")


def audit_checkpoint(root, state, checkpoint, content, verify=True):
    origin = checkpoint["origin"]
    if origin["kind"] == "external_result":
        content.get_artifact(origin["source_digest"])
        content.get_artifact(origin["study_digest"])
    else:
        key = "{}:{}".format(origin["node_id"], origin["move"])
        receipt = state["service"].get("journal_receipts", {}).get(key)
        s.require(receipt is not None, "Local checkpoint requires an acknowledged reserved journal move", "reservation_required")
        s.closed(receipt, "node_id move reservation_id journal_prefix_digest problem_digest")
        s.require(receipt["node_id"] == origin["node_id"] and receipt["move"] == origin["move"], "Journal receipt names another producer or move")
        s.text(receipt["reservation_id"])
        s.digest_string(receipt["problem_digest"])
        s.require(receipt["journal_prefix_digest"] == origin["journal_prefix_digest"], "Journal receipt differs", "digest_mismatch")
        expected = content.get_artifact(origin["journal_prefix_digest"])
        node = state["nodes"][origin["node_id"]]
        s.require(node["proposal_id"] is not None, "Imported history has no controlled local producer", "admission_required")
        proposal = state["proposals"][node["proposal_id"]]
        s.require(content.get_blob(proposal["digest"]) == proposal["record"], "Producer admission proposal differs", "digest_mismatch")
        proposal_inputs(proposal["record"], content)
        for review_id in node["admission"]["review_ids"]:
            review = state["reviews"][review_id]
            s.require(content.get_blob(review["digest"]) == review["record"], "Producer admission review differs", "digest_mismatch")
        try:
            raw = safe_path(root, node["attack_slug"] + "/journal.jsonl").read_bytes()
        except OSError as error:
            raise SearchError("missing_evidence", "Producer journal is unavailable") from error
        prefix = b"".join(raw.splitlines(keepends=True)[:origin["move"]])
        s.require(prefix == expected, "Accepted journal prefix changed", "digest_mismatch")
        lines = prefix.splitlines()
        s.require(len(lines) == origin["move"], "Pinned journal does not include the reserved move")
        last = _strict_json(lines[-1], "invalid_input")
        s.require(last.get("move") == origin["move"] and last.get("problem_digest") == receipt["problem_digest"],
                  "Journalled problem version differs from the acknowledged reservation", "digest_mismatch")
    standards = [audit_manifest(root, state, digest, content, s.digest(checkpoint["claim"]), verify=verify)
                 for digest in checkpoint["evidence_digests"]]
    if not standards:
        return "analytical", "reviewed"
    classification = "computational" if any(c == "computational" for c, _ in standards) else "analytical"
    standard = min((p for _, p in standards), key={"reviewed": 0, "certificate": 1, "lean-kernel": 2}.get)
    return classification, standard
