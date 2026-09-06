"""The scheduler progresses the root while honoring durable user control."""

import copy
import importlib.util
import unittest

from search_controller.errors import SearchError
from search_controller.model import initial_state
from tests.search_fixtures import contract, decomposition_proposal, proposal, provenance
from tests.test_search_proof import accepted, admitted, event, route_state, checkpoint_record, acceptance_record, digest


def facts_record(node_id="node-000001", **updates):
    value = {"node_id": node_id, "status": "active", "result_action": None,
             "cashout_action": None, "waiting_on": [], "failed_strategies": [],
             "stagnation_moves": 0, "external_block": None, "dependency_route_ids": [],
             "dependency_assumption_ids": []}
    value.update(updates)
    return value


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("search_controller.scheduler"),
                             "The pure scheduler must be implemented")

    def test_b_success_selects_ready_c_without_running_parent(self):
        from search_controller.scheduler import next_action
        state = route_state()
        p = proposal()
        p.update(attack_slug="tail", target_obligation="obligation-000003",
                 claim=copy.deepcopy(state["obligations"]["obligation-000003"]["claim"]))
        p["contribution"]["route"] = "route-000001"
        p["checkpoint_criteria"][0]["obligation_id"] = "obligation-000003"
        state = admitted(state, p)
        state = accepted(state, "obligation-000002")
        action = next_action(state)
        self.assertEqual(action, {"kind": "execute_node", "node_id": "node-000002"})
        self.assertEqual(state["proof_status"], "open")

    def test_all_local_nodes_finished_root_remains_open(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state["nodes"]["node-000001"]["status"] = "finished"
        self.assertEqual(next_action(state)["kind"], "replan")
        self.assertEqual(state["proof_status"], "open")

    def test_root_ready_precedes_research_and_cashout(self):
        from search_controller.scheduler import next_action
        state = accepted(route_state(), "obligation-000001")
        self.assertEqual(next_action(state)["kind"], "finalize_root")

    def test_three_nonprogress_replans_pause_without_resetting_accounts(self):
        from search_controller.scheduler import next_action
        state = route_state()
        before = copy.deepcopy(state["accounts"])
        for _ in range(3):
            state = event(state, "replan_recorded", {"route_orders": [], "progress_acceptance_ids": [],
                                                     "reason": "No usable route remains"})
        self.assertEqual(next_action(state)["kind"], "paused")
        self.assertEqual(state["accounts"], before)

    def test_stop_limit_is_objective_wide_and_false_flag_never_rearms(self):
        state = route_state()
        for index in range(40):
            state = event(state, "control_recorded", {"action": "hook_stop", "delivery_id": str(index),
                       "session_id": None, "turn_id": None, "stop_hook_active": False})
        self.assertEqual(state["execution_status"], "paused")
        self.assertEqual(state["control"]["stop_decision"]["kind"], "summary_then_stop")
        state = event(state, "control_recorded", {"action": "hook_stop", "delivery_id": None,
                   "session_id": None, "turn_id": None, "stop_hook_active": False})
        self.assertEqual(state["control"]["stop_decision"], {"kind": "allow_stop"})
        self.assertEqual(state["control"]["stop_count"], 40)

    def test_delivery_id_deduplicates_and_resume_requires_provenance(self):
        state = route_state()
        payload = {"action": "hook_stop", "delivery_id": "one", "session_id": "s",
                   "turn_id": "t", "stop_hook_active": True}
        state = event(event(state, "control_recorded", payload), "control_recorded", payload)
        self.assertEqual(state["control"]["stop_count"], 1)
        state = event(state, "control_recorded", {"action": "pause", "reason": "User stopped work"})
        bad = {"action": "resume", "objective_id": state["objective_id"], "message_id": "message",
               "session_id": "s", "instruction": "Continue this objective", "provenance": provenance("user-host")}
        bad["provenance"]["source"] = "model"
        with self.assertRaises(SearchError):
            event(state, "control_recorded", bad)
        bad["provenance"]["source"] = "operator"
        state = event(state, "control_recorded", bad)
        self.assertEqual(state["control"]["stop_count"], 0)

    def test_pending_run_and_result_priority_is_pure(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state["runs"]["run-000001"] = {"id": "run-000001", "status": "indeterminate"}
        before = copy.deepcopy(state)
        self.assertEqual(next_action(state)["kind"], "execution_pending")
        self.assertEqual(state, before)

    def test_cashout_due_even_when_research_allowance_exhausted(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = event(state, "node_facts_recorded", {"facts": facts_record(cashout_action="inventory")})
        self.assertEqual(next_action(state), {"kind": "local_cashout", "node_id": "node-000001", "step": "inventory"})

    def test_retreat_preserves_accepted_independent_facts(self):
        from search_controller.scheduler import next_action
        state = accepted(route_state(), "obligation-000003")
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = event(state, "node_facts_recorded", {"facts": facts_record()})
        value = {"node_id": "node-000001", "criterion": {"kind": "move_limit", "threshold": 24},
                 "failed_hypothesis": "The planned estimate suffices", "observation": "Budget exhausted",
                 "last_checkpoint_id": "checkpoint-000001", "remaining_assumption_ids": [],
                 "reconsideration": "A proved sharper estimate", "abandoned_route_ids": ["route-000001"],
                 "abandoned_assumption_ids": []}
        state = event(state, "node_retreated", {"retreat": value})
        self.assertEqual(state["obligations"]["obligation-000003"]["status"], "accepted")
        self.assertEqual(state["nodes"]["node-000001"]["status"], "retreated")
        self.assertEqual(next_action(state)["kind"], "replan")

    def test_repeated_stop_delivery_after_pause_does_not_request_more_work(self):
        state = route_state()
        payload = {"action": "hook_stop", "delivery_id": "first", "session_id": None,
                   "turn_id": None, "stop_hook_active": False}
        state = event(state, "control_recorded", payload)
        state = event(state, "control_recorded", {"action": "pause", "reason": "User stopped"})
        state = event(state, "control_recorded", payload)
        self.assertEqual(state["control"]["stop_decision"], {"kind": "allow_stop"})

    def test_resume_cannot_replay_old_authorization_to_rearm(self):
        state = route_state()
        resume = {"action": "resume", "objective_id": state["objective_id"], "message_id": "message",
                  "session_id": "s", "instruction": "Continue", "provenance": provenance("operator")}
        state = event(state, "control_recorded", resume)
        state = event(state, "control_recorded", {"action": "pause", "reason": "User stopped"})
        with self.assertRaises(SearchError):
            event(state, "control_recorded", resume)

    def test_reaccepted_evidence_cannot_reset_replanning(self):
        state = accepted(route_state(), "obligation-000002")
        replan = {"route_orders": [], "progress_acceptance_ids": ["acceptance-000001"], "reason": "Use proved lemma"}
        state = event(state, "replan_recorded", replan)
        state = event(state, "replan_recorded", dict(replan, progress_acceptance_ids=[]))
        cp = checkpoint_record(state, "obligation-000002")
        cp["evidence_digests"] = ["e" * 64]
        state = event(state, "checkpoint_recorded", {"checkpoint": cp, "digest": digest(cp)})
        value = acceptance_record(state, "obligation-000002")
        state = event(state, "result_accepted", {"acceptance": value, "digest": digest(value)})
        state = event(state, "replan_recorded", dict(replan, progress_acceptance_ids=["acceptance-000002"]))
        self.assertEqual(state["control"]["nonprogress_replans"], 2)

    def test_unconditional_consumer_cannot_run_before_its_route_premises(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state["nodes"]["node-000001"]["status"] = "finished"
        p = proposal()
        p["attack_slug"] = "consumer"
        p["contribution"]["route"] = "route-000001"
        state = admitted(state, p)
        self.assertEqual(next_action(state)["kind"], "replan")

    def test_ambiguous_focus_hands_off_and_does_not_spend_stop_allowance(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state = event(state, "control_recorded", {"action": "focus", "focus": "ambiguous",
                                                  "provenance": provenance("operator")})
        self.assertEqual(next_action(state)["kind"], "handoff")
        state = event(state, "control_recorded", {"action": "hook_stop", "delivery_id": None,
                    "session_id": None, "turn_id": None, "stop_hook_active": None})
        self.assertEqual(state["control"]["stop_count"], 0)

    def test_exhausted_verification_allowance_requests_handoff(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state["accounts"]["account-000001"]["used_runs"] = 24
        state = event(state, "node_facts_recorded", {"facts": facts_record(status="result_ready", result_action="verification")})
        self.assertEqual(next_action(state)["kind"], "blocked")

    def test_explicit_side_interval_is_bounded_and_main_still_wins(self):
        from search_controller.scheduler import next_action
        state = route_state()
        p = proposal("standalone")
        p["attack_slug"] = "side"
        state = event(state, "proposal_recorded", {"proposal": p, "digest": digest(p)})
        from tests.search_fixtures import review
        for actor in ["one", "two"]:
            r = review(p, actor)
            state = event(state, "review_recorded", {"proposal_id": "proposal-000002", "review": r, "digest": digest(r)})
        state = event(state, "proposal_admitted", {"proposal_id": "proposal-000002"})
        state = event(state, "control_recorded", {"action": "side_interval", "node_id": "node-000002",
                    "max_moves": 2, "message_id": "side-allocation", "provenance": provenance("operator")})
        self.assertEqual(next_action(state)["node_id"], "node-000001")
        state["nodes"]["node-000001"]["status"] = "finished"
        self.assertEqual(next_action(state)["node_id"], "node-000002")
        account = state["accounts"][state["nodes"]["node-000002"]["account_id"]]
        account["used_moves"] += 2
        self.assertEqual(next_action(state)["kind"], "replan")

    def test_ready_alternative_at_retreat_node_precedes_root_restart(self):
        from search_controller.scheduler import next_action
        state = route_state()
        p = proposal()
        p.update(attack_slug="alternative", target_obligation="obligation-000002",
                 claim=copy.deepcopy(state["obligations"]["obligation-000002"]["claim"]))
        p["contribution"]["route"] = "route-000001"
        p["checkpoint_criteria"][0]["obligation_id"] = "obligation-000002"
        p["retreat_criteria"] = [{"kind": "strategy_failure", "strategy_id": "induction"}]
        state = admitted(state, p)
        state["nodes"]["node-000001"]["retreat_criteria"] = copy.deepcopy(p["retreat_criteria"])
        state = event(state, "node_facts_recorded", {"facts": facts_record(failed_strategies=["induction"])})
        value = {"node_id": "node-000001", "criterion": p["retreat_criteria"][0],
                 "failed_hypothesis": "First approach", "observation": "Coefficient has wrong sign",
                 "last_checkpoint_id": None, "remaining_assumption_ids": [],
                 "reconsideration": "A stronger bound", "abandoned_route_ids": [], "abandoned_assumption_ids": []}
        state = event(state, "node_retreated", {"retreat": value})
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000002"})

    def test_pending_result_snapshot_precedes_root_review(self):
        from search_controller.scheduler import next_action
        state = accepted(route_state(), "obligation-000001")
        state = event(state, "node_facts_recorded", {"facts": facts_record(status="result_ready", result_action="snapshot")})
        self.assertEqual(next_action(state), {"kind": "prepare_result", "node_id": "node-000001", "step": "snapshot"})

    def test_active_node_continues_before_initial_route_traversal(self):
        from search_controller.scheduler import next_action
        state = route_state()
        p = proposal()
        p.update(attack_slug="tail", target_obligation="obligation-000003",
                 claim=copy.deepcopy(state["obligations"]["obligation-000003"]["claim"]))
        p["contribution"]["route"] = "route-000001"
        p["checkpoint_criteria"][0]["obligation_id"] = "obligation-000003"
        state = admitted(state, p)
        state = event(state, "node_facts_recorded", {"facts": facts_record(node_id="node-000002")})
        self.assertEqual(next_action(state)["node_id"], "node-000002")

    def test_pending_move_reconciliation_precedes_result_acceptance(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state["control"]["pending_moves"] = ["move-000001"]
        state = event(state, "node_facts_recorded", {"facts": facts_record(status="result_ready", result_action="acceptance")})
        self.assertEqual(next_action(state), {"kind": "reconcile_move", "move_id": "move-000001"})

    def test_verified_continuation_precedes_root_restart(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state = accepted(state, "obligation-000003")
        cp = state["checkpoints"]["checkpoint-000001"]
        p = proposal()
        p.update(attack_slug="continuation", relationship="continuation", logical_predecessor="node-000001",
                 category="coverage", target_obligation="obligation-000002",
                 claim=copy.deepcopy(state["obligations"]["obligation-000002"]["claim"]),
                 anchor={"kind": "checkpoint", "checkpoint_id": cp["id"], "digest": digest(cp)},
                 inherited_evidence=cp["evidence_digests"][:])
        p["contribution"].update(route="route-000001", coverage={"parent_obligation": "obligation-000001",
              "scope": p["claim"]["scope"], "partition_route": "route-000001", "subset_deduction": "Same base cases"})
        p["checkpoint_criteria"][0]["obligation_id"] = "obligation-000002"
        state = admitted(state, p)
        state = event(state, "node_facts_recorded", {"facts": facts_record()})
        state = event(state, "node_facts_recorded", {"facts": facts_record(status="finished")})
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000002"})

    def test_real_blocker_allows_host_stop(self):
        state = route_state()
        state["control"]["state_error"] = "Unresolved audit error"
        state = event(state, "control_recorded", {"action": "hook_stop", "delivery_id": None,
                    "session_id": None, "turn_id": None, "stop_hook_active": None})
        self.assertEqual(state["control"]["stop_decision"], {"kind": "allow_stop"})

    def test_finished_exhausted_active_node_replans_instead_of_retreating_again(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state = event(state, "node_facts_recorded", {"facts": facts_record()})
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = event(state, "node_facts_recorded", {"facts": facts_record(status="finished")})
        self.assertEqual(next_action(state)["kind"], "replan")

    def test_waiting_descendant_of_abandoned_route_stays_suspended(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state = accepted(state, "obligation-000003")
        cp = state["checkpoints"]["checkpoint-000001"]
        p = proposal()
        p.update(attack_slug="dependent", logical_predecessor="node-000001",
                 target_obligation="obligation-000004", claim=copy.deepcopy(state["obligations"]["obligation-000004"]["claim"]),
                 anchor={"kind": "checkpoint", "checkpoint_id": cp["id"], "digest": digest(cp)},
                 inherited_evidence=cp["evidence_digests"][:])
        p["contribution"]["route"] = "route-000001"
        p["checkpoint_criteria"][0]["obligation_id"] = "obligation-000004"
        state = admitted(state, p)
        state["accounts"]["account-000001"]["used_moves"] = 24
        value = {"node_id": "node-000001", "criterion": {"kind": "move_limit", "threshold": 24},
                 "failed_hypothesis": "A usable reduction", "observation": "Method exhausted",
                 "last_checkpoint_id": cp["id"], "remaining_assumption_ids": [], "reconsideration": "New reduction",
                 "abandoned_route_ids": ["route-000001"], "abandoned_assumption_ids": []}
        state = event(state, "node_retreated", {"retreat": value})
        self.assertTrue(state["control"]["node_facts"]["node-000002"]["suspended"])
        self.assertEqual(next_action(state)["kind"], "replan")

    def test_repeated_delivery_after_state_error_allows_stop(self):
        state = route_state()
        payload = {"action": "hook_stop", "delivery_id": "first", "session_id": None,
                   "turn_id": None, "stop_hook_active": None}
        state = event(state, "control_recorded", payload)
        state["control"]["state_error"] = "Corrupt state"
        state = event(state, "control_recorded", payload)
        self.assertEqual(state["control"]["stop_decision"], {"kind": "allow_stop"})

    def test_retreated_node_can_complete_cashout_without_restarting_research(self):
        from search_controller.scheduler import next_action
        state = route_state()
        state["accounts"]["account-000001"]["used_moves"] = 24
        retreat = {"node_id": "node-000001", "criterion": {"kind": "move_limit", "threshold": 24},
                   "failed_hypothesis": "The estimate suffices", "observation": "Move limit reached",
                   "last_checkpoint_id": None, "remaining_assumption_ids": [],
                   "reconsideration": "A verified stronger estimate", "abandoned_route_ids": [],
                   "abandoned_assumption_ids": []}
        state = event(state, "node_retreated", {"retreat": retreat})
        accounts = copy.deepcopy(state["accounts"])
        for step in ["inventory", "unit_checks", "consolidation", "draft", "evaluation", "handoff", "finish"]:
            state = event(state, "node_facts_recorded", {"facts": facts_record(status="retreated", cashout_action=step)})
            self.assertEqual(next_action(state), {"kind": "local_cashout", "node_id": "node-000001", "step": step})
            self.assertEqual(state["nodes"]["node-000001"]["status"], "retreated")
        for status in ["admitted", "active", "waiting", "result_ready"]:
            with self.subTest(status=status), self.assertRaises(SearchError):
                event(state, "node_facts_recorded", {"facts": facts_record(status=status)})
        state = event(state, "node_facts_recorded", {"facts": facts_record(status="finished")})
        self.assertEqual(next_action(state)["kind"], "replan")
        self.assertEqual(state["nodes"]["node-000001"]["status"], "finished")
        self.assertEqual(state["accounts"], accounts)
        self.assertEqual(state["control"]["retreats"], [retreat])
        with self.assertRaises(SearchError):
            event(state, "node_facts_recorded", {"facts": facts_record(status="active")})

    def test_node_facts_cannot_retreat_without_declared_predicate_transition(self):
        with self.assertRaises(SearchError):
            event(route_state(), "node_facts_recorded", {"facts": facts_record(status="retreated")})

    def test_repeated_stop_delivery_uses_current_root_finalization_without_recounting(self):
        state = route_state()
        payload = {"action": "hook_stop", "delivery_id": "frontier-event", "session_id": None,
                   "turn_id": None, "stop_hook_active": False}
        state = event(state, "control_recorded", payload)
        self.assertEqual(state["control"]["stop_decision"]["action"]["kind"], "execute_node")
        state = accepted(state, "obligation-000001")
        state = event(state, "control_recorded", payload)
        self.assertEqual(state["control"]["stop_decision"], {"kind": "continue", "action": {
            "kind": "finalize_root", "outcome": "proof", "acceptance_ids": ["acceptance-000001"]}})
        self.assertEqual(state["control"]["stop_count"], 1)
        self.assertEqual(len(state["control"]["stop_deliveries"]), 1)

    def test_repeated_stop_delivery_uses_current_reconciliation_without_recounting(self):
        state = route_state()
        payload = {"action": "hook_stop", "delivery_id": "frontier-event", "session_id": None,
                   "turn_id": None, "stop_hook_active": None}
        state = event(state, "control_recorded", payload)
        state["control"]["pending_moves"] = ["move-000001"]
        state = event(state, "control_recorded", payload)
        self.assertEqual(state["control"]["stop_decision"], {"kind": "continue", "action": {
            "kind": "reconcile_move", "move_id": "move-000001"}})
        self.assertEqual(state["control"]["stop_count"], 1)

    def test_repeated_previously_allowed_stop_does_not_grant_free_continuation(self):
        state = route_state()
        payload = {"action": "hook_stop", "delivery_id": "paused-event", "session_id": None,
                   "turn_id": None, "stop_hook_active": False}
        state = event(state, "control_recorded", {"action": "pause", "reason": "User stopped"})
        state = event(state, "control_recorded", payload)
        state = event(state, "control_recorded", {"action": "resume", "objective_id": state["objective_id"],
                    "message_id": "resume-message", "session_id": "session", "instruction": "Resume this objective",
                    "provenance": provenance("operator")})
        state = event(state, "control_recorded", payload)
        self.assertEqual(state["control"]["stop_decision"], {"kind": "allow_stop"})
        self.assertEqual(state["control"]["stop_count"], 0)

    def test_unused_node_observations_preserve_completed_objective(self):
        from search_controller.scheduler import next_action
        from tests.test_search_proof import closure_record
        state = route_state()
        state = event(state, "node_facts_recorded", {"facts": facts_record()})
        state = accepted(state, "obligation-000001")
        closure = closure_record(state)
        state = event(state, "objective_completed", {"closure": closure, "digest": digest(closure)})
        accounts = copy.deepcopy(state["accounts"])
        for status in ["active", "finished"]:
            state = event(state, "node_facts_recorded", {"facts": facts_record(status=status)})
            self.assertEqual(state["nodes"]["node-000001"]["status"], status)
            self.assertEqual(state["execution_status"], "resolved")
            self.assertEqual(state["proof_status"], "proved")
            self.assertEqual(state["control"]["closure"], closure)
            self.assertEqual(next_action(state), {"kind": "resolved", "proof_status": "proved"})
        self.assertEqual(state["accounts"], accounts)


if __name__ == "__main__":
    unittest.main()
