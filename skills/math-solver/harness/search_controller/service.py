"""Filesystem and operator boundary over the pure search controller."""

import copy
from pathlib import Path

from . import schema as s
from .admission import reference
from .errors import SearchError
from .evidence import import_inputs, proposal_inputs, audit_admission, audit_state, audit_checkpoint, audit_local_delivery
from .model import apply_event, initial_state, replay
from .render import render, replace_text
from .scheduler import next_action
from .storage import Store, canonical_bytes, safe_path, _strict_json


class Controller:
    def __init__(self, root: Path, strategies_dir=None):
        self.root = Path(root).resolve()
        self.strategies_dir = strategies_dir
        self.store = Store(safe_path(self.root, ".search"))

    def status(self, full_audit=False):
        state = replay(self.store.read())
        pending = [s.digest(effect) for effect in state["service"]["effects"]
                   if not safe_path(self.store.root, "effect-" + s.digest(effect) + ".json").exists()]
        state["service"]["pending_effect_ids"] = pending
        if pending:
            state["control"]["state_error"] = "Filesystem initialization is pending; replay its original command or run search render"
        state["freshness"] = {"status": "unchecked", "failures": {}}
        from .execution import process_observations
        state["process_observations"] = process_observations(state)
        if full_audit:
            failures = audit_state(self.root, state, self.store)
            state["freshness"] = {"status": "failed" if failures else "fresh", "failures": failures}
            if failures:
                from .proof import invalidate_evidence
                invalidate_evidence(state, {"acceptance_ids": sorted(failures), "reason": "Evidence audit failed",
                                            "provenance": self.provenance("audit")})
        return state

    @staticmethod
    def provenance(operation):
        return {"source": "operator", "actor_id": "search-controller",
                "attestation_id": "filesystem-audit:" + operation}

    def command(self, name, spec, expected_revision, request_id, target=None, *, workspace_root=None, hook_session=None):
        s.require(isinstance(spec, dict), "Command spec must be an object")
        if name in {"status", "next"}:
            s.require(set(spec) <= {"session_id", "focus_request_id", "objective_id", "contract_digest"}, "Unknown read-only validation field")
            state = self.status()
            self._validate_routing_identity(state, spec)
            if spec.get("session_id") is not None:
                from .discovery import validate_session
                if not validate_session(state, spec["session_id"], spec.get("focus_request_id")):
                    return {"kind": "handoff", "reason": "Explicit matching session focus is required; run search focus"}
            return state if name == "status" else next_action(state)
        s.integer(expected_revision)
        s.text(request_id)
        if name not in {"admit", "accept", "retreat", "checkpoint", "begin", "run", "amend-computation", "interpret"}:
            s.require(target is None, "This command has no positional target")
        elif name != "checkpoint":
            s.text(target)
        routing = None
        if name in {"init", "focus", "resume"}:
            from .discovery import prepare
            if name == "init":
                s.closed(spec, "contract")
                initial_state(spec["contract"], "objective-000001")
            elif name == "focus":
                s.closed(spec, "focus provenance session_id")
            routing = prepare(self, name, spec, request_id, workspace_root)
        else:
            s.require(workspace_root is None, "Workspace routing is only available for init/focus/resume")
        identity_spec = {"spec": spec, "routing": routing} if routing is not None else spec
        if hook_session is not None:
            s.require(name == "hook-stop", "Session mutation validation is only available for hook-stop")
            identity_spec = {"spec": identity_spec, "hook_session": hook_session}
        identity = {"command": name, "target": target, "spec_digest": s.digest(identity_spec)}
        built = []
        def build(document, content):
            built.append(True)
            return self._build(name, spec, target, identity, document, content, routing, hook_session)
        if name == "init":
            s.closed(spec, "contract")
            s.require(target is None and expected_revision == 0, "Initialization requires revision zero")
            contract = spec["contract"]
            initial_state(contract, "objective-000001")
            payload = dict(identity, operations=[{"kind": "discovery_recorded", "payload": routing}], effects=[])
            event = {"sequence": 1, "request_id": request_id, "kind": "service_operation", "payload": payload}
            apply_event(initial_state(contract, "objective-000001"), event)
            self.store.initialize(contract, "objective-000001", event)
        else:
            event = self.store.append_operation(identity, expected_revision, request_id,
                build,
                lambda document, candidate: apply_event(replay(document), candidate))
        if name == "run" and built:
            from .execution import launch
            state = replay(self.store.read())
            run_id = event["payload"]["operations"][0]["payload"]["run"]["id"]
            launch(self, state["runs"][run_id])
        elif name == "reconcile":
            from .execution import reconcile_runs
            from .integration import recover_journal_effects
            reconcile_runs(self)
            recover_journal_effects(self)
        with self.store._writer_lock():
            document = self.store.read()
            state = replay(document)
            self._recover_effects(state)
            manual = safe_path(self.root, "SEARCH_TREE.md")
            if name != "init" or not manual.exists():
                render(self.root, state)
            if routing is not None:
                from .discovery import publish
                try:
                    publish(self, routing)
                except (OSError, SearchError) as error:
                    raise SearchError("discovery_unpublished", "Controller command committed but discovery was not published; "
                        "inspect the conflict and use a fresh explicit focus if superseded", {"request_id": request_id,
                        "revision": event["sequence"], "reason": str(error)}) from error
        old = replay(dict(document, events=document["events"][:event["sequence"]]))
        response = {"objective_id": old["objective_id"], "revision": old["revision"],
                    "proof_status": old["proof_status"], "command": name}
        if name == "hook-stop":
            from .discovery import validate_session
            prior = old["control"]["stop_decision"]
            action = next_action(state)
            response["decision"] = ({"kind": "continue", "action": action}
                if prior["kind"] == "continue" and action["kind"] not in {"paused", "resolved", "handoff", "blocked", "execution_pending"}
                else prior if built and event["sequence"] == state["revision"] else {"kind": "allow_stop"})
            if not validate_session(state, spec.get("session_id"), (hook_session or {}).get("focus_request_id")):
                response["decision"] = {"kind": "allow_stop"}
            response["current_revision"] = state["revision"]
        return response

    @staticmethod
    def _validate_routing_identity(state, validation):
        for key in ("session_id", "focus_request_id", "objective_id", "contract_digest"):
            s.optional_text(validation.get(key))
        for key in ("objective_id", "contract_digest"):
            s.require(validation.get(key) is None or validation[key] == state[key],
                      "Registered objective identity changed; recover discovery", "discovery_conflict")

    def _build(self, name, spec, target, identity, document, content, routing=None, hook_session=None):
        state = replay(document)
        s.require(not state["service"]["native_intents"] or name in {"reconcile", "pause", "focus", "hook-stop", "audit", "render"},
                  "Reconcile the original native intent before another controller mutation", "recovery_required")
        if name != "render":
            s.require(all(safe_path(self.store.root, "effect-" + s.digest(effect) + ".json").exists()
                          for effect in state["service"]["effects"]),
                      "Replay the interrupted command or run search render before new work", "recovery_required")
        operations, effects = [], []
        def emit(kind, value):
            operations.append({"kind": kind, "payload": value})
        if name == "begin":
            from .integration import build_begin
            emit("move_reserved", {"reservation": build_begin(self, state, spec, target, content)})
        elif name == "run":
            from .execution import build_run
            emit("run_reserved", {"run": build_run(self, state, spec, target, content)})
        elif name == "interpret":
            from .computation_io import audit_run_result
            s.closed(spec, "result_digest computation_digest outcome inconclusive_reason classification root_decision remaining_obligation_ids next_action")
            run = reference(state["runs"], target, "interpreted run")
            result = audit_run_result(state, run, content)
            if spec["classification"] in {"proof_candidate", "counterexample_candidate"}:
                s.require(len(result["commands"]) == len(run["commands"])
                          and all(command["exit_code"] == 0 for command in result["commands"]),
                          "Unsuccessful execution cannot decide a theorem")
            value = dict(spec, run_id=target)
            emit("run_interpreted", {"interpretation": value, "digest": content.put_blob(value)})
        elif name == "amend-computation":
            from .computation_io import audit_computation
            s.closed(spec, "proposal_digest computation review inputs")
            node = reference(state["nodes"], target, "amendment node")
            proposal = reference(state["proposals"], node["proposal_id"], "admitted proposal")["record"]
            import_inputs(self.root, spec["inputs"], content)
            audit_computation(self.root, state, proposal, spec["computation"], content)
            subject = {"node_id": target, "proposal_digest": spec["proposal_digest"], "computation": spec["computation"]}
            content.put_blob(spec["review"])
            emit("computation_amended", {"subject": subject, "review": spec["review"], "digest": content.put_blob(subject)})
        elif name == "reconcile":
            from .integration import reconcile_journals, reconcile_native_intents
            s.closed(spec, "")
            reconcile_journals(self, state, content, emit)
            reconcile_native_intents(self, state, content, emit)
        elif name == "adopt":
            from .adoption import build_import, record_import, record_import_version, record_allowance
            s.closed(spec, "mappings inputs")
            import_inputs(self.root, spec["inputs"], content)
            s.require(0 < len(s.records(spec["mappings"])) <= 64, "Adoption needs a bounded explicit mapping")
            for mapping in spec["mappings"]:
                record = build_import(self.root, state, mapping, content)
                if record is not None:
                    version = "import_id" in record
                    emit("legacy_import_version_recorded" if version else "legacy_imported", record)
                    (record_import_version if version else record_import)(state, record)
                if mapping["verification"] is not None:
                    verification = mapping["verification"]
                    s.closed(verification, "proposal review allowance_review")
                    proposal_inputs(verification["proposal"], content)
                    from .computation import require_new_proposal
                    require_new_proposal(verification["proposal"])
                    audit_admission(self.root, state, verification["proposal"], content)
                    for key in ["proposal", "review", "allowance_review"]:
                        content.put_blob(verification[key])
                    imported = next(v for v in state["service"]["imports"].values() if v["subject"]["attack_slug"] == mapping["attack_slug"])
                    allowance = dict(verification, import_id=imported["id"])
                    existing = any(s.digest(verification["proposal"]) == s.digest(v["proposal"]) for v in state["service"]["adoption_allowances"])
                    if existing:
                        previous_versions = {v["snapshot_digest"] for v in state["service"]["adoption_allowances"]
                                             if s.digest(v["proposal"]) == s.digest(verification["proposal"])}
                        if mapping["snapshot_digest"] not in previous_versions:
                            finished = safe_path(self.root, verification["proposal"]["attack_slug"] + "/units/FINISHED.json")
                            s.require(not finished.exists(), "Finished verification requires a new reviewed node sharing its existing account", "terminal_attack")
                    if not existing:
                        effect = self._workspace_effect(state, verification["proposal"], content)
                        if effect is not None:
                            effects.append(effect)
                    emit("adoption_allowance_recorded", allowance)
                    record_allowance(state, allowance)
        elif name == "propose":
            s.closed(spec, "proposal inputs")
            proposal = spec["proposal"]
            s.validate_proposal(proposal)
            from .computation import require_new_proposal
            require_new_proposal(proposal)
            safe_path(self.root, proposal["attack_slug"])
            import_inputs(self.root, spec["inputs"], content)
            proposal_inputs(proposal, content)
            emit("proposal_recorded", {"proposal": proposal, "digest": content.put_blob(proposal)})
        elif name == "review":
            s.closed(spec, "proposal_id review inputs")
            import_inputs(self.root, spec["inputs"], content)
            proposal = reference(state["proposals"], spec["proposal_id"], "proposal")["record"]
            proposal_inputs(proposal, content)
            emit("review_recorded", {"proposal_id": spec["proposal_id"], "review": spec["review"],
                                     "digest": content.put_blob(spec["review"])})
        elif name == "admit":
            s.closed(spec, "")
            proposal = reference(state["proposals"], target, "proposal")["record"]
            from .computation import require_new_proposal
            require_new_proposal(proposal)
            proposal_inputs(proposal, content)
            audit_admission(self.root, state, proposal, content)
            for review in state["reviews"].values():
                if review["proposal_id"] == target:
                    s.require(content.get_blob(review["digest"]) == review["record"], "Review changed", "digest_mismatch")
            effect = self._workspace_effect(state, proposal, content)
            if effect is not None:
                effects.append(effect)
            emit("proposal_admitted", {"proposal_id": target})
        elif name in {"pause", "resume", "focus", "hook-stop"}:
            s.require("action" not in spec, "Control action is selected by the command")
            if name == "hook-stop":
                s.require(not state["service"]["native_intents"],
                          "Reconcile the original native operation before automatic continuation", "recovery_required")
                from .discovery import validate_session
                validation = hook_session or {}
                self._validate_routing_identity(state, validation)
                s.require(validate_session(state, spec.get("session_id"), validation.get("focus_request_id")),
                          "Explicit matching session focus is required; run search focus", "focus_required")
                if not state["service"]["legacy_intents"] and not state["control"]["pending_moves"]:
                    from .integration import observe_node
                    from .model import EVENT_HANDLERS
                    prospective = copy.deepcopy(state)
                    for node in list(prospective["nodes"].values()):
                        if node["proposal_id"] is None:
                            continue
                        facts = observe_node(self, prospective, node, content)
                        old_facts = prospective["control"]["node_facts"].get(node["id"], {})
                        if facts != {key: old_facts.get(key) for key in facts}:
                            s.require(len(operations) < 255,
                                      "Too many changed local observations; recover through bounded native reconciliation", "recovery_required")
                            emit("node_facts_recorded", {"facts": facts})
                            EVENT_HANDLERS["node_facts_recorded"](prospective, {"facts": facts})
            control_spec = {key: value for key, value in spec.items() if name != "focus" or key != "session_id"}
            emit("control_recorded", dict(control_spec, action=name.replace("-", "_")))
            if routing is not None:
                emit("discovery_recorded", routing)
        elif name == "checkpoint":
            s.closed(spec, "checkpoint inputs")
            checkpoint = copy.deepcopy(spec["checkpoint"])
            s.closed(checkpoint, "schema_version kind claim origin evidence_digests what_changed remaining_obligation_ids next_hypothesis milestone_id")
            checkpoint["verification_status"] = "pending"
            import_inputs(self.root, spec["inputs"], content)
            origin = checkpoint["origin"]
            s.require(isinstance(origin, dict), "Checkpoint origin must be a record")
            if origin.get("kind") == "external_result":
                s.closed(origin, "kind source source_digest statement statement_digest study_digest")
            else:
                s.closed(origin, "kind node_id move journal_prefix_digest")
            s.require((origin["kind"] == "external_result" and target is None) or
                      (origin["kind"] == "journal_move" and origin.get("node_id") == target and target is not None),
                      "Checkpoint command must identify its exact local producer")
            audit_checkpoint(self.root, state, checkpoint, content, verify=False)
            emit("checkpoint_recorded", {"checkpoint": checkpoint, "digest": content.put_blob(checkpoint)})
        elif name == "accept":
            s.closed(spec, "obligation_id outcome dependency_ids route_bindings review inputs")
            import_inputs(self.root, spec["inputs"], content)
            s.require(not audit_state(self.root, state, content), "Accepted dependency audit failed", "audit_failed")
            checkpoint = reference(state["checkpoints"], target, "checkpoint")
            classification, standard = audit_checkpoint(self.root, state, checkpoint, content)
            for evidence_digest in checkpoint["evidence_digests"]:
                conclusion = content.get_blob(evidence_digest)["conclusion"]
                s.require(conclusion == {key: spec[key] for key in ["outcome", "dependency_ids", "route_bindings"]},
                          "Acceptance interpretation differs from the reviewed evidence conclusion", "claim_mismatch")
            content.put_blob(spec["review"])
            value = {key: copy.deepcopy(spec[key]) for key in ["obligation_id", "outcome", "dependency_ids", "route_bindings", "review"]}
            value.update(schema_version=1, checkpoint_id=target, checkpoint_digest=s.digest(checkpoint),
                         classification=classification, standard=standard,
                         audit={"subject_digest": s.digest(checkpoint), "dependency_ids": spec["dependency_ids"],
                                "evidence_digests": checkpoint["evidence_digests"], "provenance": self.provenance("accept")})
            emit("result_accepted", {"acceptance": value, "digest": content.put_blob(value)})
        elif name == "complete":
            s.closed(spec, "subject review inputs")
            import_inputs(self.root, spec["inputs"], content)
            s.require(not audit_state(self.root, state, content), "Accepted proof audit failed", "audit_failed")
            subject = spec["subject"]
            s.closed(subject, "schema_version objective_id contract_digest outcome acceptance_ids evidence_digests local_deliveries deliverables")
            for delivery in subject["deliverables"]:
                s.require(bool(content.get_artifact(delivery["digest"]).strip()), "Required deliverable is empty")
            for delivery in subject["local_deliveries"]:
                audit_local_delivery(self.root, state, delivery, content)
            content.put_blob(spec["review"])
            closure = {"subject": subject, "review": spec["review"],
                       "audit": {"subject_digest": s.digest(subject), "acceptance_ids": subject["acceptance_ids"],
                                 "evidence_digests": subject["evidence_digests"], "provenance": self.provenance("complete")}}
            emit("objective_completed", {"closure": closure, "digest": content.put_blob(closure)})
        elif name == "replan":
            emit("replan_recorded", spec)
        elif name == "retreat":
            s.require("node_id" not in spec, "Node is selected by the command")
            emit("node_retreated", {"retreat": dict(spec, node_id=target)})
        elif name == "render":
            s.closed(spec, "")
        elif name == "audit":
            s.closed(spec, "")
            failures = audit_state(self.root, state, content)
            if failures:
                emit("evidence_invalidated", {"acceptance_ids": sorted(failures), "reason": "Evidence audit failed",
                                              "provenance": self.provenance("audit")})
        else:
            raise SearchError("invalid_command", "Unknown search command: " + name)
        if name in {"checkpoint", "accept", "reconcile"}:
            from .model import EVENT_HANDLERS
            from .integration import observe_node
            prospective = copy.deepcopy(state)
            for operation in operations:
                EVENT_HANDLERS[operation["kind"]](prospective, operation["payload"])
            for node in prospective["nodes"].values():
                if node["proposal_id"] is not None:
                    emit("node_facts_recorded", {"facts": observe_node(self, prospective, node, content)})
        paths = {"SEARCH_TREE.md"} | {n["attack_slug"] + "/LINEAGE.md" for n in state["nodes"].values()}
        if name == "admit":
            paths.add(proposal["attack_slug"] + "/LINEAGE.md")
        preserved = set()
        for effect in state["service"]["effects"]:
            if effect["kind"] == "preserve_manual_views":
                preserved.update(item["path"] for item in content.get_blob(effect["snapshot_digest"])["files"])
        files = []
        for relative in sorted(paths - preserved):
            path = safe_path(self.root, relative)
            if path.exists():
                files.append({"path": relative, "digest": content.put_artifact(path.read_bytes())})
        if files:
            effects.append({"kind": "preserve_manual_views", "slug": ".",
                            "snapshot_digest": content.put_blob({"directories": [], "files": files})})
        return dict(identity, operations=operations, effects=effects)

    def _workspace_effect(self, state, proposal, content):
        import attack
        slug = proposal["attack_slug"]
        workspace = safe_path(self.root, slug)
        parent = proposal["native_parent"]
        link = None
        if parent is not None:
            parent_slug = state["nodes"][parent]["attack_slug"]
            defects = list(attack.find_parent_defects(self.root, parent_slug))
            s.require(not defects, "Native parent is unavailable: " + "; ".join(defects))
            link = {"parent": parent_slug, "opened_after_move": len(attack.read_journal(safe_path(self.root, parent_slug)))}
        if workspace.exists():
            s.require(workspace.is_dir(), "Attack path is not a directory", "unsafe_path")
            s.require(not (workspace / "units" / "FINISHED.json").exists(), "Historical finished attacks require adoption", "adoption_required")
            journal = workspace / "journal.jsonl"
            s.require(not journal.exists() or not journal.read_bytes().strip(), "Existing research requires explicit adoption", "adoption_required")
            actual_parent = attack.read_parent(workspace)
            s.require(actual_parent == link, "Native parent differs from proposal")
            return None
        files = {"problem.json": canonical_bytes(attack.build_problem_skeleton()),
                 "novelty.md": b"", "journal.jsonl": b""}
        if link is not None:
            files["parent.json"] = canonical_bytes(link)
        snapshot = {"directories": ["deterministic", "units", "study"],
                    "files": [{"path": key, "digest": content.put_artifact(value)} for key, value in files.items()]}
        return {"kind": "initialize_workspace", "slug": slug, "snapshot_digest": content.put_blob(snapshot)}

    def _recover_effects(self, state):
        for effect in state["service"]["effects"]:
            digest = s.digest(effect)
            marker = safe_path(self.store.root, "effect-" + digest + ".json")
            if marker.exists():
                s.require(_strict_json(marker.read_bytes(), "corrupt_state") == effect, "Effect acknowledgement differs", "corrupt_state")
                continue
            snapshot = self.store.get_blob(effect["snapshot_digest"])
            if effect["kind"] == "preserve_manual_views":
                for item in snapshot["files"]:
                    self.store.get_artifact(item["digest"])
                replace_text(marker, canonical_bytes(effect).decode("utf-8"))
                continue
            workspace = safe_path(self.root, effect["slug"])
            workspace.mkdir(exist_ok=True)
            for directory in snapshot["directories"]:
                safe_path(workspace, directory).mkdir(exist_ok=True)
            for item in snapshot["files"]:
                path = safe_path(workspace, item["path"])
                raw = self.store.get_artifact(item["digest"])
                if path.exists():
                    s.require(path.read_bytes() == raw, "Interrupted workspace initialization conflicts with existing bytes", "recovery_conflict")
                else:
                    replace_text(path, raw.decode("utf-8"))
            replace_text(marker, canonical_bytes(effect).decode("utf-8"))

    def guard_legacy(self, command, slug, details):
        from .integration import guard_legacy
        return guard_legacy(self, command, slug, details)
