"""Shared observed reviewer boundary for native mathematics.

Native reviews retain their versioned schemas. Their host
attestation identifies an actual shared assignment. Historical reducers remain
pure; this module is called only by the filesystem service and current audits.
"""

import copy
import hashlib
import json
from pathlib import Path
import re

from . import schema as s
from .errors import SearchError
from .storage import ContentSink, canonical_bytes

PREFIX = "review-assignment:"


def objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def reviews(value):
    """Find native review records, including input, policy and adoption reviews."""
    for item in objects(value):
        if {"subject_digest", "reviewer", "decision", "findings"} <= item.keys():
            yield item


def kind(value):
    return "proposal" if "unresolved_objections" in value and "claim_digest" in value else (
        "result" if "claim_digest" in value else "recovery")


def common_root(root, state, supplied=()):
    """Resolve only the native root or its existing canonical preparation ancestor."""
    from .research import _common_root
    roots = set()
    workspace = state.get("service", {}).get("review_workspace")
    if workspace is not None:
        from .research import validate_workspace
        validate_workspace(workspace)
        roots.add((Path(root) / workspace).resolve())
    for record in objects([supplied, state.get("proposals", {}), state.get("service", {}).get("foundation_amendments", {})]):
        if {"workspace", "profile", "snapshot_digest", "preparation_digest", "claim_binding"} <= record.keys():
            roots.add(_common_root(root, record).resolve())
    s.require(len(roots) <= 1, "Native review references conflicting common workspaces", "review_workspace_mismatch")
    return next(iter(roots), Path(root).resolve())


def initialize_review_workspace(root, operations):
    """Create the review store when a new association is first recorded."""
    from research_harness.errors import ResearchError
    from research_harness.storage import Store
    from .research import validate_workspace
    for operation in operations:
        if operation["kind"] == "review_workspace_recorded":
            workspace = operation["payload"]["workspace"]
            validate_workspace(workspace)
            try:
                Store((Path(root) / workspace).resolve(), create=True)
            except ResearchError as error:
                raise SearchError("review_workspace_unavailable", "The shared review store could not be initialized",
                                  {"cause": error.code}) from error


def scientific_context(state, subject, review_kind):
    from .strategy_refresh import context
    objective_context = (review_kind in {"proposal", "recovery"}
                         or any(key in subject for key in ("context_digest", "acceptance_ids", "snapshot_paths")))
    if not objective_context:
        # Input checks and fixed-result reviews bind the exact subject and its
        # dependency closure. Unrelated later runs do not change those inputs.
        scientific = {"scope": "exact_subject_and_dependencies"}
        origin = subject.get("origin") if isinstance(subject.get("origin"), dict) else {}
        node_id = origin.get("node_id", subject.get("node_id"))
        if node_id is None and isinstance(subject.get("run_id"), str) and subject["run_id"] in state["runs"]:
            node_id = state["runs"][subject["run_id"]]["node_id"]
        if isinstance(node_id, str) and node_id in state["nodes"] and state["nodes"][node_id]["proposal_id"] is not None:
            scientific["studies"] = copy.deepcopy(state["nodes"][node_id]["admission"]["studies"])
        return scientific
    return {"current": context(state), "obligations": copy.deepcopy(state["obligations"]),
            "routes": copy.deepcopy(state["routes"]),
            "studies": {node["id"]: copy.deepcopy(node["admission"]["studies"])
                        for node in state["nodes"].values() if node["proposal_id"] is not None},
            "previous_assessments": [copy.deepcopy(row["assessment"])
                                     for row in state["control"]["strategy_refresh"]["assessments"]],
            "nodes": [{key: copy.deepcopy(node[key]) for key in
                       ("id", "claim", "status", "category", "relationship", "logical_predecessor", "native_parent")}
                      for node in state["nodes"].values()]}


def find_subject(value, state, content, supplied=()):
    expected = value["subject_digest"]
    for record in objects([supplied, state]):
        if s.digest(record) == expected:
            return record
    try:
        return content.get_blob(expected)
    except SearchError as error:
        raise SearchError("review_subject_missing", "The exact native review subject is unavailable") from error


def _digests(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _digests(item)
    elif isinstance(value, list):
        for item in value:
            yield from _digests(item)


def _read_optional(content, digest, kind):
    try:
        return content.get_blob(digest) if kind == "blob" else content.get_artifact(digest)
    except SearchError as error:
        # A content digest may instead identify a derived native record. Only a
        # missing file permits that fallback; corrupt present bytes always fail.
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise


def _media(raw, blob=False):
    if blob:
        return "application/json"
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    for magic, media in ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
                         (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif")):
        if raw.startswith(magic):
            return media
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SearchError("review_evidence_unreadable", "Native review evidence requires a supported exact-byte representation") from error
    return "text/plain"


def _native_executables(seed, state, content):
    """Identify only the executable selected by a typed native verifier spec."""
    candidates = [seed, state]
    for run in state.get("runs", {}).values():
        candidates.append(content.get_blob(run["spec_digest"]))
    executables, inventories, scientific = {}, set(), set()
    for record in objects(candidates):
        if not {"kind", "step_dir", "artifacts", "external_dependencies"} <= record.keys():
            continue
        scientific.update(item["digest"] for item in record["artifacts"])
        if record["kind"] == "certificate":
            allowed = {str(Path("/bin/sh").resolve()): None}
        elif record["kind"] == "lean" and "toolchain_inventory_digest" in record:
            inventory_digest = record["toolchain_inventory_digest"]
            inventory = content.get_blob(inventory_digest)
            inventories.add(inventory_digest)
            allowed = {str(Path(inventory["root"]) / item["path"]): item["digest"]
                       for item in inventory["files"] if item["path"] in {"lake", "bin/lake"}}
        else:
            continue
        for item in record["external_dependencies"]:
            if item["path"] not in allowed or allowed[item["path"]] not in {None, item["digest"]}:
                continue
            raw = _read_optional(content, item["digest"], "artifact")
            if raw is not None:
                executables[item["digest"]] = {"path": item["path"], "digest": item["digest"],
                    "size": len(raw), "role": "native_verifier_executable"}
    return {key: value for key, value in executables.items() if key not in scientific}, inventories


def _closure(seed, state, content, artifacts, *, write, common_records=None):
    from research_harness.artifacts import describe_artifact
    index = {s.digest(record): record for record in objects([seed, state, common_records or {}])}
    from research_harness.review_delivery import references
    common_artifacts = {row["sha256"]: row for row in references(common_records or {})}
    pending, seen, found, unavailable = list(_digests(seed)), set(), {}, []
    executables, inventories = _native_executables(seed, state, content)
    provenance, materialized = {}, {}
    while pending:
        digest = pending.pop()
        if digest in seen:
            continue
        seen.add(digest)
        s.require(len(seen) <= 10000, "Native review evidence exceeds the explicit closure bound", "review_packet_too_large")
        if digest in executables:
            provenance[digest] = executables[digest]
            continue
        value = _read_optional(content, digest, "blob")
        if value is None:
            raw = _read_optional(content, digest, "artifact")
            if raw is None and digest in index:
                value = index[digest]
            if raw is None and value is None and digest in common_artifacts:
                raw = artifacts.read(common_artifacts[digest])
        if value is not None:
            raw, category = canonical_bytes(value), "blob"
            # The complete typed toolchain inventory is delivered as evidence;
            # its installed binaries are bound by native execution validation.
            if digest not in inventories:
                pending.extend(_digests(value))
            structured = value
        elif raw is not None:
            category = "artifact"
            try:
                structured = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                structured = None
            if structured is not None:
                pending.extend(_digests(structured))
        else:
            unavailable.append(digest)
            continue
        for row in objects(structured):
            for field, namespace in (("source_id", "source"), ("bundle_id", "source_bundle"), ("version_id", "work")):
                identity = row.get(field)
                if isinstance(identity, str) and identity in (common_records or {}).get(namespace, {}):
                    pending.append(s.digest(common_records[namespace][identity]))
        media = _media(raw, category == "blob")
        from .provenance import collect_import_sources, publish_import_sources
        sources = collect_import_sources(digest, state, content, artifacts, common_records or {})
        reference = describe_artifact(raw, media)
        if sources:
            reference["raw_identities"] = [item for item, original in sources]
        found[digest] = {"digest": digest, "kind": category, "artifact": reference}
        materialized[digest] = (raw, media, sources)
    if write:
        # An original can itself be normalized. Commit its provenance before
        # exposing it, then publish the requested artifacts after their aliases.
        for digest, (raw, media, sources) in sorted(materialized.items()):
            publish_import_sources(artifacts, digest, sources, state=state, content=content, records=common_records or {})
        for raw, media, sources in materialized.values():
            artifacts.put(raw, media)
    return ([found[key] for key in sorted(found)], sorted(unavailable),
            [provenance[key] for key in sorted(provenance)])


def packet(root, state, content, subject, review_kind, claim_digest, reviewer, artifacts, *, write=False,
           openness=None, common_records=None):
    from research_harness.native_review import PROTOCOL
    context = scientific_context(state, subject, review_kind)
    if openness is not None:
        from .openness import validate_minimum
        validate_minimum(openness)
        context["openness_minimum"] = copy.deepcopy(openness)
    seed = {"subject": subject, "contract": state["contract"], "scientific_context": context,
            "claim_digest": claim_digest}
    evidence, unavailable, provenance = _closure(seed, state, content, artifacts, write=write, common_records=common_records)
    return {"protocol": PROTOCOL, "kind": review_kind, "root": str(Path(root).resolve()),
            "objective_id": state["objective_id"], "contract": copy.deepcopy(state["contract"]),
            "subject": copy.deepcopy(subject), "subject_digest": s.digest(subject), "claim_digest": claim_digest,
            "reviewer": copy.deepcopy(reviewer), "scientific_context": context,
            "evidence": evidence, "execution_provenance": provenance, "unmaterialized_digests": unavailable}


def prepare_subject(controller, state, content, subject):
    """Freeze only files explicitly enumerated by supported native subjects."""
    from .storage import safe_path
    def pin(item):
        s.digest_string(item["digest"])
        selected = Path(item["path"])
        path = selected if selected.is_absolute() else safe_path(controller.root, item["path"])
        s.require(path.is_file() and not path.is_symlink(), "Native review input must be a regular exact file", "missing_evidence")
        s.require(content.put_artifact(path.read_bytes()) == item["digest"],
                  "Native review input changed after its subject was prepared", "digest_mismatch")
    if "spec_without_input_review" in subject:
        spec = subject["spec_without_input_review"]
        for item in spec["artifacts"] + spec["external_dependencies"]:
            pin(item)
    if "snapshot_paths" in subject and "attack_slug" in subject:
        from .adoption import snapshot_workspace
        captured = snapshot_workspace(controller.root, subject["attack_slug"], subject["snapshot_paths"], content)
        s.require(content.put_blob(captured) == subject["snapshot_digest"],
                  "Historical snapshot changed before independent review", "digest_mismatch")
    if "current_snapshot" in subject and "source_evidence" in subject:
        from .integration import native_snapshot
        node = state["nodes"][subject["node_id"]]
        s.require(native_snapshot(controller, node, content) == subject["current_snapshot"],
                  "Recovery snapshot changed before independent review", "digest_mismatch")
        for item in subject["source_evidence"] + [subject["incident"], subject["authorization"], subject["quiescence"]]:
            pin(item)
    if "context_digest" in subject and "plan_bindings" in subject:
        from .strategy_refresh import context
        from .strategy_refresh_io import _plan_files, effective_state, plan_binding
        current = effective_state(controller, state, content)
        s.require(content.put_blob(context(current)) == subject["context_digest"],
                  "Strategy context changed before independent review", "strategy_reassessment_stale")
        for node_id, expected in subject["plan_bindings"].items():
            node = state["nodes"][node_id]
            s.require(plan_binding(controller, node) == expected,
                      "Native plans changed before independent review", "strategy_plan_stale")
            _plan_files(controller, node, content)


def export_packet(controller, spec):
    """Write immutable common evidence objects, leaving native events unchanged."""
    from research_harness.artifacts import ArtifactStore
    from research_harness.native_review import KINDS, load_packet
    from research_harness.storage import Store
    from .evidence import import_inputs
    s.closed(spec, "kind subject claim_digest reviewer inputs" + (" openness" if "openness" in spec else ""))
    s.choice(spec["kind"], KINDS)
    s.require(isinstance(spec["subject"], dict), "Native review subject must be a record")
    s.validate_provenance(spec["reviewer"])
    with controller.store._writer_lock():
        state = controller.status()
        content = ContentSink(controller.store)
        common = common_root(controller.root, state, spec["subject"])
        from .provenance import record_probe_origins
        bindings = import_inputs(controller.root, spec["inputs"], content, state=state, common_workspace=common)
        if bindings:
            record_probe_origins(state, {"bindings": bindings})
        prepare_subject(controller, state, content, spec["subject"])
        content.put_blob(spec["subject"])
        records = Store(common, create=True).snapshot()["records"]
        artifacts = ArtifactStore(common)
        value = packet(controller.root, state, content, spec["subject"], spec["kind"],
                       spec["claim_digest"], spec["reviewer"], artifacts, write=True,
                       openness=spec.get("openness"), common_records=records)
        reference = artifacts.put(canonical_bytes(value), "application/json")
        load_packet(artifacts, reference, spec["reviewer"]["actor_id"])
        return {"workspace": str(common), "native_packet": reference,
                "subject_digest": value["subject_digest"], "claim_digest": value["claim_digest"]}


def require_review(root, state, content, value, *, subject=None, new=False, common_guard=None):
    """Check actual invocation and exact native evidence, not self-attestation."""
    from research_harness.artifacts import ArtifactStore
    from research_harness.errors import ResearchError
    from research_harness.native_review import load_packet
    from research_harness.operations import normalized_text
    from research_harness.review_protocol import assignment_state
    from research_harness.storage import Store
    identifier = value.get("reviewer", {}).get("attestation_id", "")
    if not isinstance(identifier, str) or not identifier.startswith(PREFIX):
        s.require(not new, "New independent native reviews require an observed shared assignment", "review_assignment_required")
        return {"context_status": "historical_unknown"}
    identifier = identifier[len(PREFIX):]
    common = common_root(root, state, subject or ())
    try:
        records = (common_guard.snapshot() if common_guard is not None else Store(common).snapshot())["records"]
        if common_guard is not None:
            s.require(Path(common_guard.root).resolve() == common, "Native reviewer guard names another workspace", "review_workspace_mismatch")
        artifacts = ArtifactStore(common)
        current = assignment_state(records, artifacts, identifier)
        s.require(current["ready"], "The current reviewer route or invocation does not establish independence", "review_context_unverified")
        assignment = records["review_assignment"][identifier]
        s.require(assignment["role"] == "native_math" and assignment["dossier_id"] is None,
                  "Native approval requires its native mathematical assignment", "review_assignment_mismatch")
        s.require(current["output"] == value, "Use the observed native review without author changes", "review_output_mismatch")
        supplied = load_packet(artifacts, assignment["context"]["native_packet"], assignment["reviewer_id"])
        s.require(supplied["root"] == str(Path(root).resolve()) and supplied["objective_id"] == state["objective_id"]
                  and supplied["contract"] == state["contract"] and supplied["reviewer"] == value["reviewer"]
                  and supplied["kind"] == kind(value) and supplied["subject_digest"] == value["subject_digest"]
                  and supplied["claim_digest"] == value.get("claim_digest"),
                  "The observed reviewer assessed different native inputs", "review_packet_mismatch")
        if subject is not None:
            s.require(supplied["subject"] == subject, "The exact native subject changed", "review_packet_mismatch")
        if supplied["kind"] == "proposal":
            s.validate_proposal(supplied["subject"])
            s.require(normalized_text(supplied["subject"]["author"]["actor_id"])
                      != normalized_text(value["reviewer"]["actor_id"]),
                      "The native proposal author cannot supply its independent review", "review_not_independent")
        if new:
            minimum = value.get("findings", {}).get("openness", {}).get("minimum")
            expected = packet(root, state, content, subject, kind(value), value.get("claim_digest"), value["reviewer"], artifacts,
                              openness=minimum, common_records=records)
            s.require(supplied == expected, "The reviewer packet omits or changes required native scientific evidence", "review_packet_mismatch")
        for entry in supplied["evidence"]:
            artifacts.read(entry["artifact"])
        if supplied["kind"] == "result" and value.get("schema_version") == 2:
            delivered = {entry["digest"] for entry in supplied["evidence"]}
            s.require(set(value["findings"]["openness"]["evidence"]) <= delivered,
                      "Openness evidence must be delivered in the observed review packet", "review_packet_mismatch")
            if new and value["findings"]["openness"]["outcome"] != "unresolved":
                from .openness import audit_minimum
                claim = next((row for row in objects([supplied["subject"], state])
                              if s.digest(row) == value["claim_digest"]), None)
                s.require(claim is not None, "The exact reviewed claim is unavailable", "claim_mismatch")
                audit_minimum(value["findings"]["openness"], claim, records, artifacts, delivered)
        return current
    except ResearchError as error:
        raise SearchError("review_context_unverified", "Shared native review evidence could not be verified",
                          {"cause": error.code}) from error


def admission_reviews(root, state, content, proposal_id, *, common_guard=None):
    """Only observed approvals count toward a new native admission."""
    proposal = state["proposals"][proposal_id]["record"]
    observed = 0
    for saved in state["reviews"].values():
        if saved["proposal_id"] != proposal_id:
            continue
        result = require_review(root, state, content, saved["record"], subject=proposal, common_guard=common_guard)
        observed += result["context_status"] != "historical_unknown"
    s.require(observed >= (2 if proposal["category"] == "standalone" else 1),
              "A new native admission requires its observed independent approvals; retain old reviews as history",
              "review_assignment_required")


def node_reviews(root, state, content, node, *, common_guard=None):
    """Recheck the reviews the current native node actually relies upon."""
    values = [state["reviews"][identity]["record"] for identity in node["admission"]["review_ids"]]
    selected = state["service"]["foundation_selection"].get(node["id"])
    if selected is not None:
        values.append(state["service"]["foundation_amendments"][selected]["review"])
    amendment = state["service"]["computation_amendments"].get(node["id"])
    if amendment is not None:
        values.append(amendment["review"])
    assessments = state["control"]["strategy_refresh"]["assessments"]
    if assessments and node["id"] in assessments[-1]["assessment"]["plan_bindings"]:
        values.append(assessments[-1]["review"])
        from .provenance import audit_strategy_inputs
        audit_strategy_inputs(root, state, assessments[-1]["assessment"], content, common_guard=common_guard)
    for allowance in state["service"]["adoption_allowances"]:
        if s.digest(allowance["proposal"]) == node["admission"]["proposal_digest"]:
            values.extend([allowance["review"], allowance["allowance_review"]])
    for value in values:
        require_review(root, state, content, value, common_guard=common_guard)


def validate_submitted(controller, state, spec, candidate, content, locks):
    """Hold the common snapshot through native commit for ingress and use."""
    from .evidence import audit_state, manifest_closure
    state = copy.deepcopy(state)
    from .provenance import record_probe_origins
    for operation in candidate["operations"]:
        if operation["kind"] == "probe_origins_recorded":
            record_probe_origins(state, operation["payload"])
    incoming = list(reviews(spec))
    for operation in candidate["operations"]:
        if operation["kind"] == "checkpoint_recorded":
            checkpoint = operation["payload"]["checkpoint"]
            for manifest in manifest_closure(checkpoint["evidence_digests"], content).values():
                if manifest.get("verification") is not None:
                    incoming.extend(reviews(manifest["verification"].get("policy_review", {})))
    command, target = candidate["command"], candidate["target"]
    consuming = command in {"admit", "begin", "run", "checkpoint", "accept", "complete", "reassess"}
    if not incoming and not consuming:
        return
    for value in incoming:
        s.require(value.get("reviewer", {}).get("attestation_id", "").startswith(PREFIX),
                  "New independent native reviews require an observed shared assignment", "review_assignment_required")
    from research_harness.storage import Store
    common = common_root(controller.root, state, spec)
    # Preserve native-then-common lock order until the native commit completes.
    guard = locks.enter_context(Store(common).guarded_snapshot())
    recorded_reviews = {s.digest(value) for value in reviews(state)}
    for value in incoming:
        subject = find_subject(value, state, content, [spec, candidate])
        new_review = s.digest(value) not in recorded_reviews
        if new_review:
            prepare_subject(controller, state, content, subject)
        require_review(controller.root, state, content, value, subject=subject,
                       new=new_review or command in {"accept", "complete"}, common_guard=guard)
    if command == "admit":
        from .evidence import audit_admission
        audit_admission(controller.root, state, state["proposals"][target]["record"], content, common_guard=guard)
        admission_reviews(controller.root, state, content, target, common_guard=guard)
    elif command in {"begin", "run"}:
        from .integration import audit_work
        audit_work(controller, state, state["nodes"][target], content, common_guard=guard)
    elif command == "checkpoint":
        from .evidence import audit_checkpoint
        checkpoint = spec["checkpoint"]
        audit_checkpoint(controller.root, state, checkpoint, content, verify=False, common_guard=guard,
                         fresh=checkpoint["kind"] != "hypothesis")
    elif command == "adopt":
        from .adoption import record_import, record_import_version, record_allowance
        from .evidence import audit_admission
        prospective = copy.deepcopy(state)
        for operation in candidate["operations"]:
            payload = operation["payload"]
            if operation["kind"] == "legacy_imported":
                record_import(prospective, payload)
            elif operation["kind"] == "legacy_import_version_recorded":
                record_import_version(prospective, payload)
            elif operation["kind"] == "adoption_allowance_recorded":
                audit_admission(controller.root, prospective, payload["proposal"], content, common_guard=guard)
                record_allowance(prospective, payload)
    elif command in {"amend-foundation", "amend-computation"}:
        node = state["nodes"][target]
        proposal = state["proposals"][node["proposal_id"]]["record"]
        if command == "amend-foundation":
            from .research import audit_foundation
            audit_foundation(controller.root, state, proposal, spec["subject"]["foundation"], content, common_guard=guard)
        else:
            from .computation_io import audit_computation
            audit_computation(controller.root, state, proposal, spec["computation"], content, common_guard=guard)
    elif command in {"accept", "complete", "reassess"}:
        if command == "accept":
            from .evidence import audit_checkpoint
            audit_checkpoint(controller.root, state, state["checkpoints"][target], content, common_guard=guard, fresh=True)
        if command == "reassess":
            from .provenance import audit_strategy_inputs
            audit_strategy_inputs(controller.root, state, spec["assessment"], content, common_guard=guard)
        s.require(not audit_state(controller.root, state, content, common_guard=guard, fresh=True),
                  "A currently relied-on independent review or proof dependency is unavailable", "audit_failed")
