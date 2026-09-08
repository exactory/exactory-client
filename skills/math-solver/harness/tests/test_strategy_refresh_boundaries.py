"""Producer, verifier, CLI and legacy-history boundaries of strategy reassessment."""

import copy
import json
import sys
from unittest import mock

from search_controller.errors import SearchError
from search_controller.service import Controller
from tests.search_execution_support import (
    admit_workspace, begin_spec, command_spec, invoke, review_native_inputs,
)
from tests.search_fixtures import digest, review
from tests.support import FIXTURE_STRATEGIES, WorkspaceTest, make_move
from tests.strategy_refresh_support import assessment_spec


class StrategyRefreshBoundaryTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)
        self.step = self.workspace / "deterministic/job"
        self.step.mkdir()
        (self.step / "job.py").write_text("print('the exact supplied diagnostic result')\n")

    def producer(self):
        return command_spec(self.workspace, [sys.executable, "job.py"])

    def interpret(self, identity="run-000001", outcome="verified"):
        state = self.controller.status()
        run = state["runs"][identity]
        outcomes = state["nodes"][run["node_id"]]["admission"]["contribution"]["necessity"]["outcomes"]
        next_action = next(item["next_action"] for item in outcomes if item["outcome"] == outcome)
        spec = {"result_digest": run["result_digest"], "computation_digest": run["computation_digest"],
                "outcome": outcome, "inconclusive_reason": None, "classification": "observation",
                "root_decision": {"kind": "undecided", "reason": "This diagnostic does not prove the original claim"},
                "remaining_obligation_ids": ["obligation-000001"], "next_action": next_action}
        invoke(self.controller, "interpret", spec, identity)

    def test_repeated_producer_inside_an_old_reservation_requires_reassessment(self):
        invoke(self.controller, "begin", begin_spec())
        invoke(self.controller, "run", self.producer())
        self.interpret()
        before = self.controller.status()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", self.producer())
        self.assertEqual(caught.exception.code, "strategy_reassessment_required")
        self.assertEqual(self.controller.status(), before)
        self.assertEqual(before["totals"]["used_runs"], 1)

    def test_plan_change_after_begin_blocks_the_first_producer_without_reserving_a_run(self):
        invoke(self.controller, "begin", begin_spec())
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["reason"] = "Unreviewed new rationale after reservation"
        self.write_json("ranking.json", ranking)
        before = self.controller.status()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", self.producer())
        self.assertEqual(caught.exception.code, "strategy_reassessment_stale")
        self.assertEqual(self.controller.status(), before)

    def test_plan_change_between_reservation_and_launch_never_starts_the_producer(self):
        from search_controller import execution
        invoke(self.controller, "begin", begin_spec())
        original = execution.launch

        def change_plan_then_launch(controller, run):
            ranking = self.read_json("ranking.json")
            ranking["order"][0]["reason"] = "Unreviewed new rationale after run reservation"
            self.write_json("ranking.json", ranking)
            original(controller, run)

        with mock.patch.object(execution, "launch", side_effect=change_plan_then_launch):
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "run", self.producer())
        self.assertEqual(caught.exception.code, "strategy_reassessment_stale")
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["started_units"], run["charged_units"]), ("terminal", 0, 0))

    def test_reviewed_verification_can_finish_a_move_with_new_evidence(self):
        invoke(self.controller, "begin", begin_spec())
        invoke(self.controller, "run", self.producer())
        self.interpret()
        checker = self.workspace / "deterministic/check"
        checker.mkdir()
        (checker / "check.sh").write_text("#!/bin/sh\nprintf 'independent check'\n")
        (checker / "check.sh").chmod(0o755)
        (checker / "certificate.txt").write_text("True.intro\n")
        review_native_inputs(self.controller, self.slug, "check")
        status, _, error = self.run_cli("verify", "certificate", self.slug, "check")
        self.assertEqual((status, error), (0, ""))
        self.assertEqual(self.controller.status()["totals"]["used_runs"], 2)
        self.assertEqual(self.controller.status()["proof_status"], "open")

    def test_verification_frontier_cannot_be_used_to_launch_a_new_producer(self):
        checker = self.workspace / "deterministic/check"
        checker.mkdir()
        (checker / "check.sh").write_text("#!/bin/sh\nexit 1\n")
        (checker / "check.sh").chmod(0o755)
        invoke(self.controller, "begin", begin_spec())
        review_native_inputs(self.controller, self.slug, "check")
        self.assertEqual(self.run_cli("verify", "certificate", self.slug, "check")[0], 1)
        self.interpret(outcome="rejected")
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json",
                                      json.dumps(make_move(1, steps=["check"])))[0], 0)
        self.assertEqual(self.controller.command("next", {}, None, None),
                         {"kind": "prepare_result", "node_id": "node-000001", "step": "verification"})
        invoke(self.controller, "begin", begin_spec())
        move = self.controller.status()["service"]["moves"]["move-node-000001-2"]
        self.assertEqual(move["purpose"], "verification")
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "run", self.producer())
        self.assertEqual(caught.exception.code, "verification_only")

    def test_context_change_between_reservation_and_launch_never_starts_the_producer(self):
        from search_controller import execution
        marker = self.attack_root / "producer-started"
        (self.step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('started')\n")
        invoke(self.controller, "begin", begin_spec())
        original = execution.launch

        def admit_an_alternative_then_launch(controller, run):
            state = controller.status()
            candidate = copy.deepcopy(state["proposals"]["proposal-000001"]["record"])
            candidate["attack_slug"] = "new-alternative"
            invoke(controller, "propose", {"proposal": candidate, "inputs": []}, None)
            invoke(controller, "review", {"proposal_id": "proposal-000002", "review": review(candidate), "inputs": []}, None)
            invoke(controller, "admit", {}, "proposal-000002")
            original(controller, run)

        with mock.patch.object(execution, "launch", side_effect=admit_an_alternative_then_launch):
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "run", self.producer())
        self.assertEqual(caught.exception.code, "strategy_reassessment_stale")
        self.assertFalse(marker.exists())
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual(run["status"], "terminal")
        self.assertEqual((run["started_units"], run["charged_units"]), (0, 0))

    def test_legacy_journal_failure_is_read_from_its_frozen_receipt_and_upgrade_is_atomic(self):
        invoke(self.controller, "begin", begin_spec())
        line = make_move(1, failed=True)
        line["output"] = "The former construction violates its boundary condition"
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(line))[0], 0)
        document = self.controller.store.read()
        # Archive the legacy event shapes while retaining the actual pinned bytes.
        for event in document["events"]:
            if event["kind"] != "service_operation":
                continue
            operations = event["payload"]["operations"]
            event["payload"]["operations"] = [op for op in operations if op["kind"] != "strategy_policy_enabled"]
            for operation in event["payload"]["operations"]:
                payload = operation["payload"]
                if operation["kind"] == "move_reserved":
                    for field in ["purpose", "strategy_context_digest", "assessment_digest", "planning_digest"]:
                        payload["reservation"].pop(field, None)
                elif operation["kind"] == "journal_intended":
                    payload.pop("journal_line", None)
                elif operation["kind"] == "native_intended":
                    payload.pop("strategy", None)
        self.controller.store.tree_path.write_text(json.dumps(document))
        controller = Controller(self.attack_root, FIXTURE_STRATEGIES)
        old = controller.store.tree_path.read_bytes()
        self.assertFalse(controller.status()["control"]["strategy_refresh"]["enabled"])
        self.assertEqual(controller.status()["service"]["journal_observations"], {})
        description = controller.command("strategy-context", {}, None, None)
        observed = description["context"]["evidence"]["journal:move-node-000001-1"]["observation"]
        self.assertEqual(observed["output"], line["output"])
        self.assertEqual(controller.store.tree_path.read_bytes(), old)
        with self.assertRaises(SearchError) as caught:
            invoke(controller, "begin", begin_spec())
        self.assertEqual(caught.exception.code, "strategy_reassessment_required")
        self.assertEqual(controller.store.tree_path.read_bytes(), old)
        spec = assessment_spec(description)
        spec["review"]["claim_digest"] = digest(controller.status()["contract"]["original_claim"])
        invoke(controller, "reassess", spec, None)
        self.assertTrue(controller.status()["control"]["strategy_refresh"]["enabled"])
        self.assertEqual(controller.store.read()["events"][:-1], document["events"])
        invoke(controller, "begin", begin_spec())

    def assert_change_before_token_prevents_execution(self, change):
        from search_controller import integration
        marker = self.attack_root / "producer-started"
        (self.step / "job.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('started')\n")
        invoke(self.controller, "begin", begin_spec())
        original = integration.internal_operation

        def change_after_launch_record(controller, command, request_id, build):
            result = original(controller, command, request_id, build)
            if command == "execution-launch":
                change(controller)
            return result

        with mock.patch.object(integration, "internal_operation", side_effect=change_after_launch_record):
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "run", self.producer())
        self.assertEqual(caught.exception.code, "strategy_reassessment_stale")
        self.assertFalse(marker.exists())
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual((run["status"], run["started_units"], run["charged_units"]), ("terminal", 0, 0))

    def test_new_admission_after_launch_record_cannot_cross_token_release(self):
        def admit_alternative(controller):
            candidate = copy.deepcopy(controller.status()["proposals"]["proposal-000001"]["record"])
            candidate["attack_slug"] = "alternative-before-token"
            invoke(controller, "propose", {"proposal": candidate, "inputs": []}, None)
            invoke(controller, "review", {"proposal_id": "proposal-000002", "review": review(candidate), "inputs": []}, None)
            invoke(controller, "admit", {}, "proposal-000002")

        self.assert_change_before_token_prevents_execution(admit_alternative)

    def test_plan_edit_after_launch_record_cannot_cross_token_release(self):
        def edit_plan(controller):
            ranking = self.read_json("ranking.json")
            ranking["order"][0]["reason"] = "A new rationale before token delivery"
            self.write_json("ranking.json", ranking)

        self.assert_change_before_token_prevents_execution(edit_plan)

    def test_strategy_context_has_a_read_only_cli_entrypoint(self):
        before = self.controller.store.tree_path.read_bytes()
        status, output, error = self.run_cli("search", "strategy-context", "--json")
        self.assertEqual((status, error), (0, ""))
        self.assertIn("context_digest", json.loads(output))
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
