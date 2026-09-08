"""Research must use an assessment of the latest recorded evidence and failures."""

import copy
import json
import unittest

from search_controller.errors import SearchError
from tests.search_execution_support import admit_workspace, begin_spec, invoke
from tests.support import WorkspaceTest, make_move
from tests.search_fixtures import digest, provenance, proposal
from tests.strategy_refresh_support import assessment_spec, sign_assessment


class StrategyRefreshExecutionTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)

    def record_problem_progress(self):
        invoke(self.controller, "begin", begin_spec())
        problem = self.read_json("problem.json")
        problem["shape"]["missing_input"] = "The first reduction is established; only its uniform bound remains"
        self.write_json("problem.json", problem)
        status, _, error = self.run_cli("journal", "add", self.slug, "--json",
                                      json.dumps(make_move(1, problem_changed=True)))
        self.assertEqual((status, error), (0, ""))

    def spec(self, continued=None):
        description = self.controller.command("strategy-context", {}, None, None)
        value = assessment_spec(description, continued)
        value["review"]["claim_digest"] = digest(self.controller.status()["contract"]["original_claim"])
        return value

    def refresh(self):
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        value = self.spec()
        invoke(self.controller, "reassess", value, None)
        return value

    def test_progress_blocks_the_next_move_in_the_same_pass(self):
        self.record_problem_progress()
        before = self.controller.status()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", begin_spec())
        self.assertEqual(caught.exception.code, "strategy_reassessment_required")
        self.assertEqual(self.controller.status(), before)

    def test_next_reports_reassessment_instead_of_reusing_the_active_strategy(self):
        self.record_problem_progress()
        action = self.controller.command("next", {}, None, None)
        self.assertEqual(action["kind"], "reassess_strategies")

    def test_strategy_context_is_read_only_and_exposes_the_current_basis(self):
        self.record_problem_progress()
        before = self.controller.store.tree_path.read_bytes()
        try:
            description = self.controller.command("strategy-context", {}, None, None)
        except SearchError as error:
            self.fail("The read-only strategy-context command is required: " + error.message)
        self.assertEqual(len(description["context_digest"]), 64)
        self.assertTrue(description["required"])
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_reassessment_requires_a_plan_for_the_changed_problem(self):
        self.record_problem_progress()
        before = self.controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reassess", self.spec(), None)
        self.assertEqual(caught.exception.code, "strategy_plan_stale")
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_reviewed_reassessment_allows_work_without_resetting_accounts(self):
        self.record_problem_progress()
        before = self.controller.status()
        self.refresh()
        after = self.controller.status()
        self.assertEqual(after["accounts"], before["accounts"])
        self.assertEqual(after["totals"], before["totals"])
        self.assertFalse(after["strategy_assessment"]["required"])
        self.assertEqual(self.controller.command("next", {}, None, None),
                         {"kind": "execute_node", "node_id": "node-000001", "strategy": begin_spec()["strategy"]})
        invoke(self.controller, "begin", begin_spec())
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(2)))[0], 0)
        self.assertFalse(self.controller.status()["strategy_assessment"]["required"])

    def test_replan_cannot_substitute_for_strategy_reassessment(self):
        self.record_problem_progress()
        invoke(self.controller, "replan", {"route_orders": [], "progress_acceptance_ids": [],
                                          "reason": "Record an alternative ordering without an assessment"}, None)
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "reassess_strategies")

    def test_native_plan_changes_after_review_block_execution(self):
        self.record_problem_progress()
        self.refresh()
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["reason"] = "A different unreviewed ranking rationale"
        self.write_json("ranking.json", ranking)
        self.assertTrue(self.controller.command("strategy-context", {}, None, None)["required"])
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "reassess_strategies")
        before = self.controller.status()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", begin_spec())
        self.assertEqual(caught.exception.code, "strategy_reassessment_stale")
        self.assertEqual(self.controller.status(), before)

    def test_invalid_assessments_commit_no_event_or_artifact_prefix(self):
        self.record_problem_progress()
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        mutations = [
            lambda value: value.update(context_digest="0" * 64),
            lambda value: value.update(assessments=[]),
            lambda value: value["assessments"][0].update(evidence_ids=[]),
            lambda value: value["assessments"][0].update(reason=""),
            lambda value: value["assessments"][0].update(node_id=[]),
            lambda value: value.update(remaining_obligation_ids=[]),
            lambda value: value["strategy_order"].append(value["strategy_order"][0].copy()),
            lambda value: value.update(next_hypotheses=[]),
            lambda value: value["plan_bindings"]["node-000001"].update(problem_digest="1" * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                spec = self.spec()
                mutate(spec["assessment"])
                spec["review"]["subject_digest"] = digest(spec["assessment"])
                before = self.controller.status()
                paths = sorted(str(path) for path in self.controller.store.root.rglob("*"))
                with self.assertRaises(SearchError):
                    invoke(self.controller, "reassess", spec, None)
                self.assertEqual(self.controller.status(), before)
                self.assertEqual(sorted(str(path) for path in self.controller.store.root.rglob("*")), paths)

    def test_assessment_author_cannot_approve_their_own_decisions(self):
        self.record_problem_progress()
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        spec = self.spec()
        spec["review"]["reviewer"] = spec["assessment"]["author"].copy()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reassess", spec, None)
        self.assertEqual(caught.exception.code, "review_not_independent")

    def test_old_assessment_request_replay_does_not_clear_new_progress(self):
        self.record_problem_progress()
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        spec = self.spec()
        revision = self.controller.status()["revision"]
        original = self.controller.command("reassess", spec, revision, "first-assessment")
        invoke(self.controller, "begin", begin_spec())
        problem = self.read_json("problem.json")
        problem["shape"]["missing_input"] = "The uniform bound is reduced to one exact inequality"
        self.write_json("problem.json", problem)
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json",
                                      json.dumps(make_move(2, problem_changed=True)))[0], 0)
        before = self.controller.store.tree_path.read_bytes()
        self.assertEqual(self.controller.command("reassess", spec, 0, "first-assessment"), original)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
        self.assertTrue(self.controller.status()["strategy_assessment"]["required"])

    def test_administrative_rendering_does_not_invalidate_an_assessment(self):
        self.record_problem_progress()
        self.refresh()
        before = self.controller.status()["strategy_assessment"]
        invoke(self.controller, "render", {}, None)
        self.assertEqual(self.controller.status()["strategy_assessment"], before)

    def test_generated_views_keep_assessment_lineage_and_existing_obligations(self):
        self.record_problem_progress()
        self.refresh()
        invoke(self.controller, "reassess", self.spec(), None)
        for path in [self.attack_root / "SEARCH_TREE.md", self.workspace / "LINEAGE.md"]:
            view = path.read_text()
            for expected in ["assessment-000001", "assessment-000002", "Predecessor: assessment-000001",
                             "obligation-000001", "Independent review:", "continue", "Failure signal:"]:
                self.assertIn(expected, view)
        self.assertIn("## Residual obligations", (self.attack_root / "SEARCH_TREE.md").read_text())
        self.assertIn("## Checkpoints", (self.workspace / "LINEAGE.md").read_text())

    def test_failed_strategy_survives_clearing_its_mutable_precondition_note(self):
        invoke(self.controller, "begin", begin_spec())
        line = make_move(1, failed=True)
        line["output"] = "The proposed estimate fails on the exact boundary example"
        self.assertEqual(self.run_cli("journal", "add", self.slug, "--json", json.dumps(line))[0], 0)
        original = self.read_json("preconditions.json")
        strategy = begin_spec()["strategy"]
        self.assertEqual(self.run_cli("fail", self.slug, strategy)[0], 0)
        state = self.controller.status()
        failures = state["control"]["strategy_refresh"]["failures"]
        self.assertEqual(len(failures), 1)
        failure = next(iter(failures.values()))
        self.assertEqual(failure["observation"], line["output"])
        self.assertEqual(failure["source"]["kind"], "native_receipt")
        self.write_json("preconditions.json", original)
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        self.assertEqual(self.controller.status()["control"]["strategy_refresh"]["failures"], failures)
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "reassess", self.spec(), None)
        self.assertEqual(caught.exception.code, "failed_strategy")
        invoke(self.controller, "reassess", self.spec(continued=set()), None)
        self.assertEqual(self.controller.command("next", {}, None, None)["kind"], "replan")


class StrategyRefreshModelTests(unittest.TestCase):
    def setUp(self):
        from tests.test_search_proof import event, route_state
        self.event = event
        self.state = event(route_state(), "strategy_policy_enabled", {"version": 1, "journal_observations": []})

    def reviewed(self, state, continued=None):
        from search_controller.strategy_refresh import context
        current = context(state)
        bindings = {row["node_id"]: {name: "a" * 64 for name in
                    ["problem_digest", "preconditions_digest", "openings_digest", "ranking_digest"]}
                    for row in current["strategies"]}
        spec = assessment_spec({"context": current, "context_digest": digest(current), "plan_bindings": bindings}, continued)
        spec["review"]["claim_digest"] = digest(state["contract"]["original_claim"])
        return spec

    def apply_assessment(self, state, spec):
        return self.event(state, "strategies_reassessed", dict(spec, digest=digest(spec["assessment"])))

    def test_accepted_partial_result_requires_reassessment_but_root_proof_closes_first(self):
        from tests.test_search_proof import accepted
        from search_controller.scheduler import next_action
        partial = accepted(self.state, "obligation-000002")
        self.assertEqual(next_action(partial)["kind"], "reassess_strategies")
        complete = accepted(self.state, "obligation-000001")
        self.assertEqual(next_action(complete)["kind"], "finalize_root")

    def test_assessment_order_can_select_an_alternative_to_the_active_node(self):
        from tests.test_search_proof import accepted, admitted
        from tests.test_search_scheduler import facts_record
        from search_controller.scheduler import next_action
        state = accepted(self.state, "obligation-000002")
        candidate = proposal()
        candidate["attack_slug"] = "alternative"
        state = admitted(state, candidate)
        state = self.event(state, "node_facts_recorded", {"facts": facts_record()})
        spec = self.reviewed(state)
        spec["assessment"]["strategy_order"].reverse()
        spec["review"]["subject_digest"] = digest(spec["assessment"])
        result = self.apply_assessment(state, spec)
        self.assertEqual(next_action(result)["node_id"], "node-000002")
        self.assertEqual(result["accounts"], state["accounts"])

    def test_same_account_sibling_cannot_reuse_a_failed_method_without_new_evidence(self):
        from tests.test_search_proof import admitted, checkpointed
        from search_controller.model import initial_state
        from tests.search_fixtures import contract
        from tests.test_search_scheduler import facts_record
        from search_controller.scheduler import next_action
        state = admitted(initial_state(contract(), "objective-000001"), proposal())
        state = self.event(state, "strategy_policy_enabled", {"version": 1, "journal_observations": []})
        state = self.event(state, "node_facts_recorded", {"facts": facts_record(failed_strategies=["induction"])})
        candidate = proposal()
        candidate["attack_slug"] = "sibling"
        state = admitted(state, candidate)
        self.assertIsNone(state["nodes"]["node-000002"]["logical_predecessor"])
        self.assertEqual(state["nodes"]["node-000001"]["account_id"], state["nodes"]["node-000002"]["account_id"])
        continued = {("node-000002", "induction")}
        spec = self.reviewed(state, continued)
        with self.assertRaises(SearchError) as caught:
            self.apply_assessment(state, spec)
        self.assertEqual(caught.exception.code, "failed_strategy")
        state = checkpointed(state, "obligation-000001")
        spec = self.reviewed(state, continued)
        failure_id = next(iter(state["control"]["strategy_refresh"]["failures"]))
        row = next(row for row in spec["assessment"]["assessments"] if row["node_id"] == "node-000002")
        row["failure_resolutions"] = [{"failure_id": failure_id, "changed_input": "The new base-case proof removes the former missing premise",
                                       "evidence_ids": ["checkpoint:checkpoint-000001"]}]
        spec["review"]["subject_digest"] = digest(spec["assessment"])
        result = self.apply_assessment(state, spec)
        self.assertEqual(next_action(result)["node_id"], "node-000002")

    def test_historical_model_events_keep_their_original_execution_behavior(self):
        from tests.test_search_proof import accepted, route_state
        from search_controller.scheduler import next_action
        historical = accepted(route_state(), "obligation-000002")
        self.assertFalse(historical["control"]["strategy_refresh"]["enabled"])
        self.assertEqual(next_action(historical)["kind"], "replan")

    def test_later_obligation_priority_requires_review_against_the_new_order(self):
        from tests.test_obligation_order import direct_alternatives, order_spec
        from search_controller.scheduler import next_action
        from search_controller.strategy_refresh import context
        state = self.event(direct_alternatives(), "strategy_policy_enabled",
                           {"version": 1, "journal_observations": []})
        state = self.apply_assessment(state, self.reviewed(state))
        before = state["accounts"]
        state = self.event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state)["kind"], "reassess_strategies")
        self.assertEqual(context(state)["planning_priorities"]["obligation_orders"]["obligation-000002"],
                         ["node-000002", "node-000001"])
        spec = self.reviewed(state)
        spec["assessment"]["strategy_order"].reverse()
        spec["review"]["subject_digest"] = digest(spec["assessment"])
        state = self.apply_assessment(state, spec)
        self.assertEqual(next_action(state)["node_id"], "node-000002")
        self.assertEqual(state["accounts"], before)

    def test_replan_without_changed_priorities_keeps_the_assessment_current(self):
        from search_controller.strategy_refresh import assessment_status
        state = self.apply_assessment(self.state, self.reviewed(self.state))
        state = self.event(state, "replan_recorded", {"route_orders": [],
                           "progress_acceptance_ids": [], "reason": "Retain the current reviewed priorities"})
        self.assertFalse(assessment_status(state)["required"])
