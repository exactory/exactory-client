"""Adversarial prelaunch, accounting, and recovery boundaries."""

import json
import os
import sys
from unittest import mock

from search_controller.errors import SearchError
from tests.support import WorkspaceTest
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke, review_native_inputs


class NativeReviewBoundaryTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)
        self.step = self.workspace / "deterministic/check"
        self.step.mkdir()
        self.marker = self.attack_root / "checker-ran"
        (self.step / "check.sh").write_text("#!/bin/sh\nprintf checked >> '" + str(self.marker) + "'\n")
        (self.step / "check.sh").chmod(0o755)
        invoke(self.controller, "begin", begin_spec())

    def verify(self):
        return self.run_cli("verify", "certificate", self.slug, "check")

    def test_wrong_claim_review_refuses_before_reservation(self):
        value = review_native_inputs(self.controller, self.slug, "check")
        value["claim_digest"] = "f" * 64
        (self.step / "verification-review.json").write_text(json.dumps(value))
        self.assertNotEqual(self.verify()[0], 0)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.controller.status()["runs"], {})

    def test_wrong_domain_subject_review_refuses_before_reservation(self):
        value = review_native_inputs(self.controller, self.slug, "check")
        value["subject_digest"] = "e" * 64
        (self.step / "verification-review.json").write_text(json.dumps(value))
        self.assertNotEqual(self.verify()[0], 0)
        self.assertFalse(self.marker.exists())

    def test_self_authored_review_does_not_authorize_native_execution(self):
        author = self.controller.status()["proposals"]["proposal-000001"]["record"]["author"]["actor_id"]
        review_native_inputs(self.controller, self.slug, "check", reviewer=author)
        self.assertIn("input_review_required", self.verify()[2])
        self.assertFalse(self.marker.exists())

    def test_changed_input_cannot_reuse_an_unchanged_review(self):
        review_native_inputs(self.controller, self.slug, "check")
        with (self.step / "check.sh").open("a") as stream:
            stream.write("printf undeclared\n")
        self.assertNotEqual(self.verify()[0], 0)
        self.assertFalse(self.marker.exists())

    def test_changed_environment_cannot_reuse_an_unchanged_review(self):
        (self.step / "step.json").write_text(json.dumps({"environment": {"SAMPLE_DOMAIN": "admitted"}}))
        review_native_inputs(self.controller, self.slug, "check")
        (self.step / "step.json").write_text(json.dumps({"environment": {"SAMPLE_DOMAIN": "other"}}))
        self.assertNotEqual(self.verify()[0], 0)
        self.assertFalse(self.marker.exists())

    def test_unchanged_review_can_retry_with_a_fresh_charged_reservation(self):
        review_native_inputs(self.controller, self.slug, "check")
        self.assertEqual(self.verify()[0], 0)
        self.assertEqual(self.verify()[0], 0)
        state = self.controller.status()
        self.assertEqual(self.marker.read_text(), "checkedchecked")
        self.assertEqual(state["totals"]["used_runs"], 2)
        self.assertEqual(len(state["runs"]), 2)
        self.assertEqual(state["proof_status"], "open")

    def test_result_recovery_preserves_intervening_user_edits(self):
        from search_controller import execution
        review_native_inputs(self.controller, self.slug, "check")
        with mock.patch.object(execution, "publish_results", side_effect=OSError("interrupted result publication")):
            with self.assertRaises(OSError):
                self.verify()
        result = self.step / "result.json"
        result.write_text("user recovery notes")
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(caught.exception.code, "recovery_conflict")
        self.assertEqual(result.read_text(), "user recovery notes")

    def test_failed_native_result_enters_only_the_verification_frontier(self):
        from tests.support import make_move
        from search_controller.scheduler import next_action
        (self.step / "check.sh").write_text("#!/bin/sh\nexit 1\n")
        review_native_inputs(self.controller, self.slug, "check")
        self.assertEqual(self.verify()[0], 1)
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1, steps=["check"])))[0], 0)
        self.assertEqual(next_action(self.controller.status()), {"kind": "prepare_result", "node_id": "node-000001", "step": "verification"})
        invoke(self.controller, "begin", begin_spec())
        self.assertEqual(len(self.controller.status()["control"]["pending_moves"]), 1)

    def test_wrong_input_checkpoint_cannot_advance_the_actual_result_frontier(self):
        from tests.support import make_move
        from search_controller.integration import observe_node
        review_native_inputs(self.controller, self.slug, "check")
        self.assertEqual(self.verify()[0], 0)
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(1, steps=["check"])))[0], 0)
        state = self.controller.status()
        node = state["nodes"]["node-000001"]
        receipt = state["service"]["journal_receipts"]["node-000001:1"]
        run = state["runs"]["run-000001"]
        manifest = {"artifacts": [], "external_dependencies": [], "dependencies": [],
                    "verification": {"run_id": run["id"], "result_digest": run["result_digest"]}}
        state["checkpoints"]["checkpoint-false"] = {"id": "checkpoint-false", "kind": "proof", "claim": node["claim"],
            "origin": {"kind": "journal_move", "node_id": node["id"], "move": 1, "journal_prefix_digest": receipt["journal_prefix_digest"]},
            "evidence_digests": [self.controller.store.put_blob(manifest)]}
        self.assertEqual(observe_node(self.controller, state, node)["result_action"], "snapshot")
        state["checkpoints"]["checkpoint-false"]["origin"]["move"] = 2
        self.assertEqual(observe_node(self.controller, state, node)["result_action"], "snapshot")

    def test_full_verification_audit_rechecks_the_pinned_input_review(self):
        from search_controller.evidence import audit_verification
        from tests.search_fixtures import provenance
        review_native_inputs(self.controller, self.slug, "check")
        self.assertEqual(self.verify()[0], 0)
        state = self.controller.status()
        run = state["runs"]["run-000001"]
        frozen = self.controller.store.get_blob(run["input_digest"])
        policy = {"subject_digest": run["result_digest"], "claim_digest": frozen["claim_digest"], "reviewer": provenance("result-reviewer"),
                  "decision": "approve", "findings": {key: "Exact fixture checked" for key in ["statement", "assumptions", "scope", "dependencies", "policy"]}}
        manifest = dict(frozen, kind="certificate", verification={"run_id": run["id"], "result_digest": run["result_digest"],
                        "policy_review": policy, "requested_declaration": None, "requested_type_digest": None})
        audit_verification(state, manifest, self.controller.store)
        spec_path = self.controller.store.root / "blobs" / (run["spec_digest"] + ".json")
        spec_path.write_text('{"input_review":"changed after launch"}')
        with self.assertRaises(SearchError):
            audit_verification(state, manifest, self.controller.store)


class RecoveryBoundaryTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)
        self.step = self.workspace / "deterministic/job"
        self.step.mkdir()
        (self.step / "job.py").write_text("print('bounded fixture')\n")
        invoke(self.controller, "begin", begin_spec())
        self.spec = command_spec(self.workspace, [sys.executable, "job.py"])

    def test_unknown_launch_is_charged_and_never_rerun_or_signalled(self):
        from search_controller import execution
        with mock.patch.object(execution, "launch", side_effect=OSError("interrupted before handshake")):
            with self.assertRaises(OSError):
                invoke(self.controller, "run", self.spec)
        with mock.patch.object(os, "killpg", side_effect=AssertionError("Recovery must not signal an uncertain PID")):
            invoke(self.controller, "reconcile", {}, None)
            invoke(self.controller, "reconcile", {}, None)
        state = self.controller.status()
        self.assertEqual(state["runs"]["run-000001"]["status"], "indeterminate")
        self.assertEqual(state["totals"]["used_runs"], 1)
        self.assertEqual(state["totals"]["reserved_runs"], 0)
        with self.assertRaises(SearchError):
            invoke(self.controller, "run", self.spec)

    def test_dependency_change_after_reservation_prevents_process_start(self):
        import hashlib
        from search_controller import execution
        dependency = self.attack_root / "dependency"
        dependency.write_text("original")
        self.spec["external_dependencies"] = [{"path": str(dependency), "digest": hashlib.sha256(dependency.read_bytes()).hexdigest()}]
        original = execution.materialize
        def change_dependency(controller, run):
            directory = original(controller, run)
            dependency.write_text("changed")
            return directory
        with mock.patch.object(execution, "materialize", side_effect=change_dependency):
            invoke(self.controller, "run", self.spec)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual(run["started_units"], 0)
        self.assertEqual(run["termination"], "dependency_changed")

    def test_superseded_account_refuses_a_new_run_inside_the_builder(self):
        import copy
        from search_controller.execution import build_run
        state = self.controller.status()
        successor = copy.deepcopy(state["accounts"]["account-000001"])
        successor.update(id="account-000002", predecessor_account_id="account-000001")
        state["accounts"]["account-000002"] = successor
        with self.assertRaises(SearchError) as caught:
            build_run(self.controller, state, self.spec, "node-000001", self.controller.store)
        self.assertEqual(caught.exception.code, "account_superseded")


class NativeIntentConflictTests(WorkspaceTest):
    def test_known_failure_with_unpermitted_input_change_is_structured_and_preserved(self):
        import attack
        controller = admit_workspace(self.attack_root)
        problem = self.workspace / "problem.json"
        def corrupt_input(args):
            problem.write_text("intervening input edit")
            raise attack.ValidationError(["The native validation failed"])
        with mock.patch.object(attack, "run_rank", side_effect=corrupt_input):
            status, out, err = self.run_cli("rank", self.slug)
        self.assertEqual(status, 1)
        self.assertIn("recovery_conflict", err)
        self.assertEqual(problem.read_text(), "intervening input edit")
        self.assertEqual(len(controller.status()["service"]["native_intents"]), 1)
