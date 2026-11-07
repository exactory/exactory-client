"""User control changes scheduling while preserving the mathematical record."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from search_controller.attempt_delivery import build_attempt_summary
from research_harness.storage import Store as CommonStore
from search_controller.errors import SearchError
from search_controller.model import apply_event, replay
from search_controller.proof import obligation_support, root_support
from search_controller.scheduler import next_action
from search_controller.service import Controller
from search_controller.storage import canonical_bytes
from tests import test_search_evidence as evidence
from tests import support
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke, review_native_inputs
from tests.search_fixtures import digest, provenance, review
from tests.native_reviewer_support import attest_spec, observe_review


PLUGIN = Path(__file__).resolve().parents[4]


class DispositionTests(support.WorkspaceTest):
    result_review = evidence.EvidenceTests.result_review

    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)
        self.root = self.attack_root
        self.serial = 0

    def command(self, name, spec, target=None, **kwargs):
        self.serial += 1
        return self.controller.command(name, spec, self.controller.status()["revision"],
                                       "disposition-" + str(self.serial), target, **kwargs)

    def instruction(self, message="user-1", **changes):
        return dict(objective_id="objective-000001", message_id=message, session_id="claude:session",
                    instruction="Set this result aside and work on the other approaches.",
                    provenance=provenance("fixture-operator"), **changes)

    def pending_result(self, verified=False):
        invoke(self.controller, "begin", begin_spec())
        if verified:
            step = self.workspace / "deterministic/check-1"
            step.mkdir()
            (step / "check.sh").write_text("#!/bin/sh\nprintf checked\n")
            (step / "check.sh").chmod(0o755)
            (step / "certificate.txt").write_text("True.intro\n")
            review_native_inputs(self.controller, self.slug, "check-1")
            result = self.run_cli("verify", "certificate", self.slug, "check-1")
            self.assertEqual(result[0], 0, result)
        result = self.run_cli("journal", "add", self.slug, "--json", json.dumps(support.make_move(1)))
        self.assertEqual(result[0], 0, result)
        state = self.controller.status()
        claim = state["nodes"]["node-000001"]["claim"]
        proof = b"The proposition True holds by its constructor."
        (self.root / "proof.txt").write_bytes(proof)
        proof_digest = hashlib.sha256(proof).hexdigest()
        manifest = {"schema_version": 1, "kind": "analytical", "claim_digest": digest(claim),
                    "conclusion": {"outcome": "proof", "dependency_ids": [], "route_bindings": []},
                    "artifacts": [{"path": "proof.txt", "digest": proof_digest, "role": "proof"}],
                    "dependencies": [], "external_dependencies": [], "verification": None}
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        receipt = state["service"]["journal_receipts"]["node-000001:1"]
        checkpoint = {"schema_version": 1, "kind": "proof", "claim": claim,
                      "origin": {"kind": "journal_move", "node_id": "node-000001", "move": 1,
                                 "journal_prefix_digest": receipt["journal_prefix_digest"]},
                      "evidence_digests": [digest(manifest)], "what_changed": "An exact proof is available",
                      "remaining_obligation_ids": [], "next_hypothesis": "Review this deduction",
                      "milestone_id": "root-proof"}
        self.command("checkpoint", {"checkpoint": checkpoint, "inputs": [
            {"path": "proof.txt", "digest": proof_digest, "kind": "artifact"},
            {"path": "manifest.json", "digest": digest(manifest), "kind": "blob"}]}, "node-000001")
        if verified:
            run = self.controller.status()["runs"]["run-000001"]
            frozen = self.controller.store.get_blob(run["input_digest"])
            manifest = dict({key: frozen[key] for key in ("claim_digest", "artifacts", "external_dependencies")},
                schema_version=1, kind="certificate", dependencies=[],
                conclusion={"outcome": "proof", "dependency_ids": [], "route_bindings": []},
                verification={"run_id": run["id"], "result_digest": run["result_digest"],
                    "requested_declaration": None, "requested_type_digest": None,
                    "policy_review": {"subject_digest": run["result_digest"], "claim_digest": digest(claim),
                        "reviewer": provenance("fixture-policy-reviewer"), "decision": "approve",
                        "findings": {key: "The exact fixture verifier is in scope" for key in
                                     ("statement", "assumptions", "scope", "dependencies", "policy")}}})
            manifest["verification"]["policy_review"] = observe_review(self.controller,
                manifest["verification"]["policy_review"], self.controller.store.get_blob(run["result_digest"]))
            (self.root / "verified-manifest.json").write_text(json.dumps(manifest))
            checkpoint.update(evidence_digests=[digest(manifest)], what_changed="The exact controlled run verified it")
            self.command("checkpoint", {"checkpoint": checkpoint, "inputs": [
                {"path": "verified-manifest.json", "digest": digest(manifest), "kind": "blob"}]}, "node-000001")
        self.assertEqual(next_action(self.controller.status()),
                         {"kind": "prepare_result", "node_id": "node-000001", "step": "acceptance"})
        return list(self.controller.status()["checkpoints"].values())[-1]

    def assert_code(self, code, name, spec, target=None):
        before = self.controller.store.read()
        with self.assertRaises(SearchError) as caught:
            self.command(name, spec, target)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.controller.store.read(), before)

    def assert_provenance_is_retained(self, reported):
        cp = self.pending_result()
        for name, target, message in (("set-aside", cp["id"], "set-aside-report"),
                                      ("end-objective", None, "end-report")):
            spec = self.instruction(message)
            spec["provenance"] = reported
            self.command(name, spec, target)
        state = self.controller.command("status", {}, None, None)
        rows = state["control"]["dispositions"]
        self.assertEqual([row["provenance"] for row in rows], [reported, reported])
        objective = json.loads((self.root.parent / "final/math/attack/objective.json").read_text())
        self.assertEqual(objective["dispositions"], rows)
        summary = build_attempt_summary(self.controller, state)
        self.assertEqual(summary["dispositions"], rows)
        delivered = self.command("deliver-attempt", {})
        records = CommonStore(self.root).snapshot()["records"]
        self.assertEqual(records["native_attempt"][delivered["native_attempt_digest"]]["dispositions"], rows)

    def test_reported_provenance_is_retained_without_claiming_authentication(self):
        self.assert_provenance_is_retained({"source": "reported_by_agent"})

    def test_authenticated_host_provenance_is_still_accepted(self):
        self.assert_provenance_is_retained({"source": "host", "actor_id": "fixture-host",
                                           "attestation_id": "authenticated-user-message"})

    def test_reported_provenance_with_extra_fields_is_refused_for_both_actions(self):
        cp = self.pending_result()
        for name, target in (("set-aside", cp["id"]), ("end-objective", None)):
            for bad in ({"source": "reported_by_agent", "actor_id": "x"},
                        {"source": "reported_by_agent", "attestation_id": "x"},
                        {"source": "unknown"}, {}, None):
                with self.subTest(command=name, provenance=bad):
                    spec = self.instruction()
                    spec["provenance"] = bad
                    self.assert_code("control_provenance_required", name, spec, target)

    def test_instruction_limit_accepts_exactly_64_kib_of_utf8_for_both_actions(self):
        cp = self.pending_result()
        for name, target in (("set-aside", cp["id"]), ("end-objective", None)):
            spec = self.instruction("bounded-" + name)
            spec["instruction"] = "é" * 32768
            self.command(name, spec, target)
            row = self.controller.status()["control"]["dispositions"][-1]
            self.assertEqual(row["instruction"], "é" * 32768)
            self.assertEqual(len(row["instruction"].encode("utf-8")), 65536)

    def test_instruction_one_utf8_byte_over_the_limit_is_refused_for_both_actions(self):
        cp = self.pending_result()
        for name, target in (("set-aside", cp["id"]), ("end-objective", None)):
            spec = self.instruction("too-large-" + name)
            spec["instruction"] = "é" * 32768 + "a"
            self.assert_code("control_invalid", name, spec, target)

    def test_end_with_a_pending_native_intent_preserves_reconciliation(self):
        import attack
        cp = self.pending_result()
        with patch.object(attack, "run_plan", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli("plan", self.slug)
        pending = self.controller.status()["service"]["native_intents"]
        self.assertTrue(pending)
        self.assert_code("recovery_required", "set-aside", self.instruction("aside-pending"), cp["id"])
        self.command("end-objective", self.instruction("end-pending"))
        state = self.controller.status()
        self.assertEqual(state["service"]["native_intents"], pending)
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "ended")
        self.assert_code("native_state_pending", "deliver-attempt", {})
        self.command("reconcile", {})
        state = self.controller.status()
        self.assertEqual(state["service"]["native_intents"], {})
        self.assertIsNone(state["control"]["state_error"])
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "ended")
        delivered = self.command("deliver-attempt", {})
        self.assertEqual(delivered["state"]["execution_status"], "ended")
        self.assertEqual(replay(self.controller.store.read())["execution_status"], "ended")

    def test_end_with_a_pending_move_delivers_attempt_without_closure(self):
        invoke(self.controller, "begin", begin_spec())
        pending = self.controller.status()["control"]["pending_moves"]
        self.assertTrue(pending)
        self.command("end-objective", self.instruction())
        self.assertEqual(self.controller.status()["control"]["pending_moves"], pending)
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "ended")
        delivered = self.command("deliver-attempt", {})
        self.assertEqual(delivered["state"]["execution_status"], "ended")
        self.assert_code("native_completion_required", "deliver-closure", {})

    def test_accept_after_end_keeps_execution_ended_and_refuses_completion(self):
        cp = self.pending_result()
        self.command("end-objective", self.instruction())
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(cp, cp["claim"]), "inputs": []}
        attest_spec(self.controller, spec)
        self.command("accept", spec, cp["id"])
        state = self.controller.status()
        self.assertEqual(state["acceptances"]["acceptance-000001"]["status"], "accepted")
        self.assertEqual(state["execution_status"], "ended")
        self.assert_code("control_objective_ended", "complete", {})

    def test_set_aside_allows_reassessment_and_replanning(self):
        from tests.strategy_refresh_support import reassess_fixture
        cp = self.pending_result()
        self.command("set-aside", self.instruction(), cp["id"])
        reassess_fixture(self.controller, set())
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "replan")
        before = self.controller.status()["revision"]
        spec = {"route_orders": [], "progress_acceptance_ids": [],
                "obligation_orders": [{"obligation_id": "obligation-000001", "alternative_order": ["node-000001"]}],
                "reason": "Return to this open obligation after setting the result aside."}
        self.command("replan", spec)
        state = self.controller.status()
        self.assertGreater(state["revision"], before)
        event = self.controller.store.read()["events"][-1]
        self.assertEqual(event["payload"]["command"], "replan")
        self.assertEqual(event["payload"]["operations"][0], {"kind": "replan_recorded", "payload": spec})
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "reassess_strategies")
        reassess_fixture(self.controller, {("node-000001", support.OPENING)})
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "execute_node")

    def test_new_work_commands_refuse_after_end_including_receipt_retries(self):
        state = self.controller.status()
        candidate = copy.deepcopy(state["proposals"]["proposal-000001"]["record"])
        candidate["attack_slug"] = "later"
        proposed = {"proposal": candidate, "inputs": []}
        reviewed = {"proposal_id": "proposal-000002", "review": review(candidate, "later-reviewer"), "inputs": []}
        requests = []
        def record(name, spec, target=None):
            attest_spec(self.controller, spec)
            revision = self.controller.status()["revision"]
            request_id = "disposition-" + str(self.serial + 1)
            saved = copy.deepcopy(spec)
            self.command(name, spec, target)
            requests.append((name, saved, target, revision, request_id))
        record("propose", proposed)
        record("review", reviewed)
        record("admit", {}, "proposal-000002")
        legacy = self.root / "legacy"
        (legacy / "units").mkdir(parents=True)
        (legacy / "problem.json").write_text(json.dumps({"claim": candidate["claim"]["statement"]}))
        (legacy / "journal.jsonl").write_bytes(b"")
        (legacy / "units/FINISHED.json").write_text(json.dumps({"outcome": "solved-in-literature", "units": []}))
        snapshot = {"files": [{"path": path.relative_to(legacy).as_posix(),
                                "digest": hashlib.sha256(path.read_bytes()).hexdigest()}
                               for path in sorted(legacy.rglob("*")) if path.is_file()], "omitted_paths": []}
        subject = {"attack_slug": "legacy", "claim": candidate["claim"], "target_obligation": "obligation-000001",
                   "logical_predecessor": None, "snapshot_digest": digest(snapshot),
                   "snapshot_paths": [row["path"] for row in snapshot["files"]],
                   "usage": {"moves": None, "runs": None}}
        adoption_review = {"subject_digest": digest(subject), "claim_digest": digest(candidate["claim"]),
                           "reviewer": provenance("adoption-reviewer"), "decision": "approve",
                           "findings": {key: "Inspected the preserved historical fixture with unknown usage."
                                        for key in ("statement", "assumptions", "scope", "dependencies", "policy")}}
        record("adopt", {"mappings": [dict(subject, review=adoption_review, verification=None)], "inputs": []})
        self.command("end-objective", self.instruction())
        before = {path.relative_to(self.controller.store.root).as_posix(): path.read_bytes()
                  for path in self.controller.store.root.rglob("*") if path.is_file()}
        for name, spec, target, revision, request_id in requests:
            for retry in (False, True):
                with self.subTest(command=name, retry=retry):
                    with self.assertRaises(SearchError) as caught:
                        if retry:
                            self.controller.command(name, spec, revision, request_id, target)
                        else:
                            self.command(name, spec, target)
                    self.assertEqual(caught.exception.code, "control_objective_ended")
                    after = {path.relative_to(self.controller.store.root).as_posix(): path.read_bytes()
                             for path in self.controller.store.root.rglob("*") if path.is_file()}
                    self.assertEqual(after, before)

    def test_ended_objective_is_silent_for_unfocused_sessions_on_both_hosts(self):
        self.command("focus", {"focus": "focused", "session_id": "claude:session-A",
                               "provenance": provenance("fixture-operator")})
        instruction = self.instruction()
        instruction["session_id"] = "claude:session-A"
        self.command("end-objective", instruction)
        before = self.controller.status()
        for host in ("claude", "codex"):
            session = host + ":session-B"
            for command in ("status", "next", "strategy-context"):
                with self.subTest(host=host, command=command):
                    result = self.controller.command(command, {"session_id": session}, None, None)
                    self.assertNotEqual(result.get("kind"), "handoff")
                    if command == "next":
                        self.assertEqual(result["kind"], "ended")
            for script, event in (("resume_attack.py", "SessionStart"), ("continue_attack.py", "Stop")):
                with self.subTest(host=host, event=event):
                    argv = [sys.executable, str(PLUGIN / "hooks" / script)] if host == "claude" else [
                        sys.executable, str(PLUGIN / "codex/hook.py"), script]
                    result = subprocess.run(argv, input=json.dumps({"hook_event_name": event, "session_id": "session-B",
                        "delivery_id": "ended-other-" + host, "cwd": str(self.workspace)}),
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "")
            stopped = self.command("hook-stop", {"session_id": session, "delivery_id": "unfocused-" + host,
                                                 "turn_id": "other-turn", "stop_hook_active": False})
            self.assertEqual(stopped["decision"], {"kind": "allow_stop"})
        state = self.controller.status()
        self.assertEqual(state["control"]["stop_count"], before["control"]["stop_count"])
        self.assertEqual(state["control"]["focus_record"], before["control"]["focus_record"])

    def test_reported_resume_recovers_a_replan_pause_without_a_new_size_bound(self):
        for index in range(3):
            self.command("replan", {"route_orders": [], "progress_acceptance_ids": [], "reason": "No progress yet."})
        self.assertEqual(self.controller.status()["execution_status"], "paused")
        spec = self.instruction("resume-after-cap")
        spec["provenance"] = {"source": "reported_by_agent"}
        spec["instruction"] = "é" * 32769
        self.command("resume", spec)
        state = self.controller.status()
        self.assertEqual(state["execution_status"], "needs_replan")
        self.assertEqual(state["control"]["resume_record"]["provenance"], {"source": "reported_by_agent"})
        self.assertEqual(state["control"]["resume_record"]["instruction"], spec["instruction"])

    def test_set_aside_releases_acceptance_and_preserves_all_proof_credit(self):
        cp = self.pending_result()
        before = self.controller.status()
        spec = self.instruction()
        self.command("set-aside", spec, cp["id"])
        state = self.controller.command("status", {}, None, None)
        self.assertEqual(next_action(state)["kind"], "reassess_strategies")
        for key in ("checkpoints", "acceptances", "obligations", "proof_status", "totals"):
            self.assertEqual(state[key], before[key], key)
        self.assertIsNone(root_support(state))
        self.assertIsNone(obligation_support(state, "obligation-000001"))
        self.assertEqual(state["checkpoints"][cp["id"]]["verification_status"], "pending")
        row = state["control"]["dispositions"][0]
        self.assertEqual(row, dict(spec, kind="result_set_aside", checkpoint_id=cp["id"]))
        self.command("refresh", {"node_ids": ["node-000001"]})
        self.assertNotEqual(next_action(self.controller.status()).get("step"), "acceptance")
        self.assertEqual(replay(self.controller.store.read())["control"]["dispositions"], [row])
        objective = self.root.parent / "final/math/attack/objective.json"
        self.assertEqual(json.loads(objective.read_text())["dispositions"], [row])

    def test_accepting_a_set_aside_checkpoint_later_keeps_normal_acceptance(self):
        cp = self.pending_result()
        self.command("set-aside", self.instruction(), cp["id"])
        spec = {"obligation_id": "obligation-000001", "outcome": "proof", "dependency_ids": [],
                "route_bindings": [], "review": self.result_review(cp, cp["claim"]), "inputs": []}
        attest_spec(self.controller, spec)
        self.command("accept", spec, cp["id"])
        state = self.controller.status()
        self.assertEqual(state["acceptances"]["acceptance-000001"]["status"], "accepted")
        self.assertIsNotNone(root_support(state))
        self.assertEqual(state["obligations"]["obligation-000001"]["status"], "accepted")
        from search_controller.dispositions import is_set_aside
        self.assertFalse(is_set_aside(state, cp["id"]))
        self.assertEqual(len(state["control"]["dispositions"]), 1)

    def test_invalidation_does_not_reactivate_a_consumed_set_aside(self):
        self.test_accepting_a_set_aside_checkpoint_later_keeps_normal_acceptance()
        state = self.controller.status()
        self.controller.store.append("evidence_invalidated", {
            "acceptance_ids": ["acceptance-000001"], "reason": "A later audit invalidated this finding",
            "provenance": provenance("fixture-audit")}, state["revision"], "later-audit",
            validate=lambda document, event: apply_event(replay(document), event))
        self.command("refresh", {"node_ids": ["node-000001"]})
        state = self.controller.status()
        self.assertEqual(next_action(state).get("step"), "acceptance")
        from search_controller.dispositions import is_set_aside
        self.assertFalse(is_set_aside(state, "checkpoint-000001"))
        self.assertIsNone(root_support(state))
        self.assertEqual(state["control"]["dispositions"][0]["ended_by_acceptance_id"], "acceptance-000001")
        self.command("set-aside", self.instruction("user-after-invalidation"), "checkpoint-000001")
        self.assertNotEqual(next_action(self.controller.status()).get("step"), "acceptance")

    def test_setting_aside_one_checkpoint_does_not_hide_another(self):
        cp = self.pending_result()
        checkpoint = {key: value for key, value in cp.items() if key not in {"id", "verification_status"}}
        checkpoint["what_changed"] = "A distinct deduction for the same journal move"
        self.command("checkpoint", {"checkpoint": checkpoint, "inputs": []}, "node-000001")
        self.command("set-aside", self.instruction(), cp["id"])
        self.assertEqual(next_action(self.controller.status()).get("step"), "acceptance")
        self.command("set-aside", self.instruction("user-2"), "checkpoint-000002")
        self.assertNotEqual(next_action(self.controller.status()).get("step"), "acceptance")

    def test_set_aside_immediately_uses_the_current_verification_match(self):
        cp = self.pending_result(verified=True)
        self.assertEqual(cp["id"], "checkpoint-000002")
        self.command("set-aside", self.instruction(), cp["id"])
        state = self.controller.status()
        self.assertNotEqual(next_action(state).get("step"), "acceptance")
        self.assertIsNone(state["control"]["node_facts"]["node-000001"]["result_action"])
        self.assertEqual(state["acceptances"], {})

    def test_model_provenance_and_missing_instruction_are_refused_for_both_actions(self):
        cp = self.pending_result()
        for name, target in (("set-aside", cp["id"]), ("end-objective", None)):
            for invalid in ("provenance", "instruction", "objective"):
                with self.subTest(name=name, invalid=invalid):
                    spec = self.instruction()
                    if invalid == "provenance":
                        spec["provenance"]["source"] = "model"
                    elif invalid == "instruction":
                        del spec["instruction"]
                    else:
                        spec["objective_id"] = "another-objective"
                    self.assert_code({"provenance": "control_provenance_required", "instruction": "control_invalid",
                                      "objective": "control_objective_mismatch"}[invalid], name, spec, target)

    def test_instruction_identity_cannot_be_reused_across_controls(self):
        cp = self.pending_result()
        spec = self.instruction()
        self.command("set-aside", spec, cp["id"])
        self.assert_code("control_instruction_consumed", "end-objective", spec)
        self.assert_code("invalid_record", "resume", spec)
        self.assert_code("control_instruction_consumed", "set-aside", spec, cp["id"])

    def test_resume_identity_is_also_unavailable_to_new_controls(self):
        cp = self.pending_result()
        spec = self.instruction()
        self.command("resume", spec)
        for name, target in (("set-aside", cp["id"]), ("end-objective", None)):
            self.assert_code("control_instruction_consumed", name, spec, target)

    def test_end_instruction_cannot_be_reused_to_set_aside_a_result(self):
        cp = self.pending_result()
        spec = self.instruction()
        self.command("end-objective", spec)
        self.assert_code("control_instruction_consumed", "set-aside", spec, cp["id"])
        self.assert_code("control_instruction_consumed", "end-objective", spec)

    def test_public_cli_checks_and_records_both_commands(self):
        cp = self.pending_result()
        for name, target, spec in (("set-aside", [cp["id"]], self.instruction()),
                                   ("end-objective", [], self.instruction("user-end"))):
            path = Path(self._tmp.name) / (name + ".json")
            path.write_text(json.dumps(spec))
            revision = self.controller.status()["revision"]
            args = ["search", name, *target, "--spec", str(path), "--expected-revision", str(revision),
                    "--request-id", "public-" + name, "--json"]
            before = self.controller.store.tree_path.read_bytes()
            status, out, err = self.run_cli(*args, "--check")
            self.assertEqual((status, err), (0, ""), out)
            self.assertEqual(json.loads(out)["check"], "accepted")
            self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
            status, out, err = self.run_cli(*args)
            self.assertEqual((status, err), (0, ""), out)
            self.assertEqual(json.loads(out)["command"], name)

    def test_end_objective_delivers_attempt_without_closure_and_hooks_allow_stop(self):
        cp = self.pending_result()
        self.command("focus", {"focus": "focused", "session_id": "claude:session",
                               "provenance": provenance("fixture-operator")})
        self.command("set-aside", self.instruction(), cp["id"])
        decision = self.command("hook-stop", {"delivery_id": "ended-stop", "session_id": "claude:session",
                                              "turn_id": "old-turn", "stop_hook_active": False})
        self.assertEqual(decision["decision"]["kind"], "continue")
        end = self.instruction("user-end")
        end["instruction"] = "End this objective now and report the findings without closure."
        before = self.controller.status()
        self.command("end-objective", end)
        state = self.controller.status()
        self.assertEqual(state["execution_status"], "ended")
        self.assertEqual(state["proof_status"], before["proof_status"])
        self.assertIsNone(state["control"]["closure"])
        self.assertEqual(next_action(state)["kind"], "ended")
        self.assert_code("native_completion_required", "deliver-closure", {})
        delivered = self.command("deliver-attempt", {})
        records = CommonStore(self.root).snapshot()["records"]
        summary = records["native_attempt"][delivered["native_attempt_digest"]]
        self.assertEqual(summary["dispositions"], state["control"]["dispositions"])
        self.assertEqual(summary["dispositions"][-1], dict(end, kind="objective_ended"))
        self.assertNotIn("native_closure", records)
        for host in ("claude", "codex"):
            self.command("focus", {"focus": "focused", "session_id": host + ":session",
                                   "provenance": provenance("fixture-operator")})
            for script, event in (("continue_attack.py", "Stop"), ("resume_attack.py", "SessionStart")):
                with self.subTest(host=host, event=event):
                    argv = [sys.executable, str(PLUGIN / "hooks" / script)] if host == "claude" else [
                        sys.executable, str(PLUGIN / "codex/hook.py"), script]
                    result = subprocess.run(argv, input=json.dumps({"hook_event_name": event, "session_id": "session",
                        "delivery_id": "ended-stop", "cwd": str(self.root)}), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(self.controller.status()["control"]["stop_count"], before["control"]["stop_count"])
        self.assertEqual(self.controller.status()["execution_status"], "ended")
        self.assertEqual(replay(self.controller.store.read())["execution_status"], "ended")
        self.assert_code("control_objective_ended", "resume", self.instruction("user-new"))
        self.assert_code("control_objective_ended", "begin", begin_spec(), "node-000001")

    def test_ended_objective_refuses_new_native_research_mutations(self):
        self.command("end-objective", self.instruction())
        before = self.controller.store.tree_path.read_bytes()
        for args in (("plan", self.slug), ("rank", self.slug), ("fail", self.slug, support.OPENING)):
            with self.subTest(command=args[0]):
                status, out, err = self.run_cli(*args)
                self.assertEqual(status, 1, out)
                self.assertIn("control_objective_ended", err)
                self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_check_and_retry_preserve_the_same_durable_instruction(self):
        cp = self.pending_result()
        for name, target, spec in (("set-aside", cp["id"], self.instruction()),
                                   ("end-objective", None, self.instruction("user-end"))):
            state = self.controller.status()
            before = self.controller.store.tree_path.read_bytes()
            revision, request = state["revision"], "checked-" + name
            checked = self.controller.command(name, spec, revision, request, target, check=True)
            self.assertEqual(checked["check"], "accepted")
            self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
            committed = self.controller.command(name, spec, revision, request, target)
            self.assertEqual(self.controller.command(name, spec, revision, request, target), committed)


class HistoricalDispositionTests(unittest.TestCase):
    def test_reported_provenance_authorizes_resume_and_side_interval_without_a_size_bound(self):
        from tests.test_search_standalone_proof import admit_standalone
        state = admit_standalone()
        resume = {"action": "resume", "objective_id": state["objective_id"], "message_id": "legacy-resume",
                  "session_id": "claude:session", "instruction": "Resume this objective.",
                  "provenance": {"source": "reported_by_agent"}}
        side = {"action": "side_interval", "node_id": "node-000001", "max_moves": 1,
                "message_id": "legacy-side", "instruction": "Allocate one side move.",
                "provenance": {"source": "reported_by_agent"}}
        for payload in (resume, side):
            for instruction in (payload["instruction"], "é" * 32769):
                with self.subTest(action=payload["action"], instruction_bytes=len(instruction.encode("utf-8"))):
                    event = {"sequence": state["revision"] + 1, "request_id": payload["message_id"],
                             "kind": "control_recorded", "payload": dict(payload, instruction=instruction)}
                    accepted = apply_event(state, event)
                    self.assertEqual(accepted["revision"], state["revision"] + 1)
                    value = accepted["control"]["resume_record" if payload["action"] == "resume" else "side_interval"]
                    self.assertEqual(value["provenance"], {"source": "reported_by_agent"})
                    self.assertEqual(value["instruction"], instruction)

    def test_focus_still_refuses_reported_provenance(self):
        from tests.test_search_standalone_proof import admit_standalone
        state = admit_standalone()
        with self.assertRaises(SearchError) as caught:
            apply_event(state, {"sequence": state["revision"] + 1, "request_id": "reported-focus",
                "kind": "control_recorded", "payload": {"action": "focus", "focus": "focused",
                    "provenance": {"source": "reported_by_agent"}}})
        self.assertEqual(caught.exception.code, "invalid_record")

    def test_malformed_legacy_control_keeps_its_structured_refusal(self):
        fixture = Path(__file__).parent / "fixtures/standalone-release-replay.json"
        state = replay(json.loads(fixture.read_text())["document"])
        with self.assertRaises(SearchError) as caught:
            apply_event(state, {"sequence": state["revision"] + 1, "request_id": "malformed-control",
                                "kind": "control_recorded", "payload": {"action": []}})
        self.assertEqual(caught.exception.code, "invalid_record")

    def test_existing_release_fixture_and_summary_remain_byte_identical(self):
        path = Path(__file__).parent / "fixtures/standalone-release-replay.json"
        before = path.read_bytes()
        state = replay(json.loads(before)["document"])
        self.assertEqual(hashlib.sha256(canonical_bytes(state)).hexdigest(),
                         "fbc5037bc4c0cc85ad7ff2073806476d42b00c625958c16ddaf18c712b943f65")
        summary = build_attempt_summary(SimpleNamespace(root=Path("/fixture/attack")), state)
        self.assertNotIn("dispositions", summary)
        self.assertEqual(hashlib.sha256(canonical_bytes(summary)).hexdigest(),
                         "b39b98e0807c54eca04b1b6788210a6541ae0bd7b3b066801d7a9f1f22887755")
        self.assertEqual(path.read_bytes(), before)


class LaunchDispositionTests(support.WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)

    def end_objective(self):
        self.controller.command("end-objective", {"objective_id": "objective-000001", "message_id": "end-launch",
            "session_id": "claude:session", "instruction": "End the objective before starting another computation.",
            "provenance": provenance("fixture-operator")}, self.controller.status()["revision"], "end-launch")

    def assert_unstarted(self, timing):
        from search_controller import execution, integration
        marker = Path(self._tmp.name) / "producer-ran"
        step = self.workspace / "deterministic/job"
        step.mkdir()
        (step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('ran')\n")
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, [sys.executable, "job.py"])
        if timing == "before-launch":
            original = execution.materialize
            def boundary(controller, run):
                directory = original(controller, run)
                self.end_objective()
                return directory
            interception = patch.object(execution, "materialize", side_effect=boundary)
        else:
            original = integration.internal_operation
            def boundary(controller, command, request_id, build):
                result = original(controller, command, request_id, build)
                if command == "execution-launch":
                    self.end_objective()
                return result
            interception = patch.object(integration, "internal_operation", side_effect=boundary)
        with interception:
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "run", spec)
        self.assertEqual(caught.exception.code, "control_objective_ended")
        self.assertFalse(marker.exists())
        state = self.controller.status()
        self.assertEqual(state["execution_status"], "ended")
        self.assertEqual(state["runs"]["run-000001"]["termination"], "never_started")
        self.assertEqual(state["totals"]["used_runs"], 0)
        self.assertEqual(state["totals"]["reserved_runs"], 0)

    def test_end_before_launch_prevents_a_reserved_run_starting(self):
        self.assert_unstarted("before-launch")

    def test_end_after_launch_record_prevents_token_release(self):
        self.assert_unstarted("before-token")
