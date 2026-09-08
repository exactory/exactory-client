"""Direct alternatives can be ordered without fabricating a route or failure."""

import copy
import json
import shutil
import unittest

import attack
from search_controller.errors import SearchError
from search_controller.scheduler import next_action
from search_controller.service import Controller
from tests.search_execution_support import admit_workspace, begin_spec, invoke
from tests.search_fixtures import proposal, review
from tests.support import FIXTURE_STRATEGIES, WorkspaceTest
from tests.test_search_proof import accepted, admitted, event, route_state
from tests.test_search_scheduler import facts_record


def order_spec(obligation="obligation-000002", nodes=None):
    return {
        "route_orders": [],
        "obligation_orders": [{
            "obligation_id": obligation,
            "alternative_order": ["node-000002", "node-000001"] if nodes is None else nodes,
        }],
        "progress_acceptance_ids": [],
        "reason": "Prefer the reviewed alternative without declaring a failed proof.",
    }


def direct_alternatives():
    state = route_state()
    candidate = proposal()
    candidate.update(
        attack_slug="alternative",
        relationship="alternative",
        target_obligation="obligation-000002",
        claim=copy.deepcopy(state["obligations"]["obligation-000002"]["claim"]),
        equivalent_node_ids=["node-000001"],
    )
    candidate["contribution"]["route"] = "route-000001"
    candidate["checkpoint_criteria"][0]["obligation_id"] = "obligation-000002"
    candidate["budget"].update(mode="inherit", account_id="account-000001")
    return admitted(state, candidate)


class ObligationOrderTests(unittest.TestCase):
    def test_direct_preference_changes_frontier_without_route_or_new_authority(self):
        state = direct_alternatives()
        self.assertFalse(any(r["conclusion"] == "obligation-000002" for r in state["routes"].values()))
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000001"})
        try:
            updated = event(state, "replan_recorded", order_spec())
        except SearchError as error:
            self.fail("A reviewed direct alternative must be orderable: " + error.code)
        self.assertEqual(next_action(updated), {"kind": "execute_node", "node_id": "node-000002"})
        for field in ["contract", "nodes", "routes", "accounts", "totals", "acceptances", "proof_status"]:
            self.assertEqual(updated[field], state[field], field)
        self.assertEqual(updated["control"]["retreats"], [])
        self.assertEqual(updated["control"]["nonprogress_replans"], 1)

    def test_omitting_optional_order_preserves_legacy_behavior(self):
        state = direct_alternatives()
        spec = order_spec()
        del spec["obligation_orders"]
        updated = event(state, "replan_recorded", spec)
        self.assertEqual(next_action(updated), {"kind": "execute_node", "node_id": "node-000001"})

    def test_later_legacy_replan_keeps_committed_preference(self):
        state = event(direct_alternatives(), "replan_recorded", order_spec())
        spec = order_spec()
        del spec["obligation_orders"]
        state = event(state, "replan_recorded", spec)
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000002"})

    def test_empty_order_restores_stable_fallback(self):
        state = event(direct_alternatives(), "replan_recorded", order_spec())
        state = event(state, "replan_recorded", order_spec(nodes=[]))
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000001"})

    def test_invalid_references_shapes_and_duplicates_preserve_state(self):
        bad_orders = [
            None,
            {},
            [{"obligation_id": "missing", "alternative_order": ["node-000002"]}],
            [{"obligation_id": "obligation-000002", "alternative_order": ["missing"]}],
            [{"obligation_id": "obligation-000001", "alternative_order": ["node-000002"]}],
            [{"obligation_id": "obligation-000002", "alternative_order": ["node-000002", "node-000002"]}],
            [{"obligation_id": "obligation-000002", "alternative_order": ["node-000002"], "force": True}],
            [{"obligation_id": "obligation-000002", "alternative_order": ["node-000002"]},
             {"obligation_id": "obligation-000002", "alternative_order": ["node-000001"]}],
        ]
        for orders in bad_orders:
            with self.subTest(orders=orders):
                state = direct_alternatives()
                before = copy.deepcopy(state)
                spec = order_spec()
                spec["obligation_orders"] = orders
                with self.assertRaises(SearchError):
                    event(state, "replan_recorded", spec)
                self.assertEqual(state, before)

    def test_imported_unadmitted_node_cannot_be_selected(self):
        state = direct_alternatives()
        state["nodes"]["node-000002"]["proposal_id"] = None
        with self.assertRaises(SearchError):
            event(state, "replan_recorded", order_spec())

    def test_preference_does_not_preempt_active_work(self):
        state = direct_alternatives()
        state = event(state, "node_facts_recorded", {"facts": facts_record()})
        state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000001"})

    def test_unmet_prerequisite_skips_preferred_node(self):
        state = direct_alternatives()
        state = event(state, "node_facts_recorded", {"facts": facts_record(
            node_id="node-000002", status="waiting", waiting_on=["obligation-000003"])})
        state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000001"})

    def test_terminal_preferred_node_is_not_reopened(self):
        state = direct_alternatives()
        state = event(state, "node_facts_recorded", {"facts": facts_record(
            node_id="node-000002", status="finished")})
        state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000001"})
        self.assertEqual(state["nodes"]["node-000002"]["status"], "finished")

    def test_pending_move_precedes_preferred_work(self):
        state = direct_alternatives()
        state["control"]["pending_moves"] = ["pending-original"]
        state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state), {"kind": "reconcile_move", "move_id": "pending-original"})

    def test_pause_is_not_cleared_by_preference(self):
        state = event(direct_alternatives(), "control_recorded", {"action": "pause", "reason": "User pause"})
        state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state), {"kind": "paused", "reason": "User pause"})

    def test_exhausted_account_gains_no_execution_allowance(self):
        state = direct_alternatives()
        state["accounts"]["account-000001"]["used_moves"] = 24
        state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state)["kind"], "replan")
        self.assertEqual(state["accounts"]["account-000001"]["used_moves"], 24)

    def test_root_closure_keeps_priority(self):
        state = accepted(direct_alternatives(), "obligation-000001")
        state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state)["kind"], "finalize_root")

    def test_three_order_only_replans_still_pause(self):
        state = direct_alternatives()
        for _ in range(3):
            state = event(state, "replan_recorded", order_spec())
        self.assertEqual(next_action(state)["kind"], "paused")
        self.assertEqual(state["control"]["nonprogress_replans"], 3)
        self.assertEqual(state["control"]["progress_fingerprints"], [])


class ObligationOrderServiceTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)
        state = self.controller.status()
        candidate = copy.deepcopy(state["proposals"]["proposal-000001"]["record"])
        candidate.update(attack_slug="alternative", relationship="alternative",
                         equivalent_node_ids=["node-000001"])
        candidate["budget"].update(mode="inherit", account_id="account-000001")
        invoke(self.controller, "propose", {"proposal": candidate, "inputs": []}, None)
        invoke(self.controller, "review", {"proposal_id": "proposal-000002",
                                          "review": review(candidate), "inputs": []}, None)
        invoke(self.controller, "admit", {}, "proposal-000002")
        alternative = self.attack_root / "alternative"
        for name in ["problem.json", "preconditions.json", "ranking.json", "novelty.md"]:
            shutil.copyfile(self.workspace / name, alternative / name)
        shutil.copytree(self.workspace / "study", alternative / "study", dirs_exist_ok=True)
        self.assertEqual(self.run_cli("plan", "alternative")[0], 0)
        self.spec = order_spec("obligation-000001")

    def test_public_replan_replays_once_and_preserves_original_events(self):
        before = self.controller.status()
        original_events = self.controller.store.read()["events"]
        result = self.controller.command("replan", self.spec, before["revision"], "choose-alternative")
        restored = Controller(self.attack_root, FIXTURE_STRATEGIES)
        after = restored.status()
        self.assertEqual(next_action(after), {"kind": "execute_node", "node_id": "node-000002"})
        self.assertEqual(restored.store.read()["events"][:-1], original_events)
        for field in ["accounts", "totals", "nodes", "acceptances", "proof_status"]:
            self.assertEqual(after[field], before[field], field)
        repeated = restored.command("replan", self.spec, before["revision"], "choose-alternative")
        self.assertEqual(repeated, result)
        self.assertEqual(restored.status()["control"]["nonprogress_replans"], 1)
        self.assertEqual(restored.status()["revision"], before["revision"] + 1)

    def test_ordered_frontier_still_requires_reviewed_method_and_exact_reservation(self):
        invoke(self.controller, "replan", self.spec, None)
        unstudied = dict(begin_spec(), strategy="ladder-the-parameter")
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", unstudied, "node-000002")
        self.assertEqual(caught.exception.code, "admission_required")
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "begin", begin_spec(), "node-000001")
        self.assertEqual(caught.exception.code, "frontier_required")
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)
        invoke(self.controller, "begin", begin_spec(), "node-000002")
        state = self.controller.status()
        self.assertEqual(state["control"]["active_node_id"], "node-000002")
        self.assertEqual(state["totals"]["reserved_moves"], 1)
        self.assertEqual(state["totals"]["used_moves"], 0)

    def test_preferred_node_with_unknown_trigger_still_cannot_begin(self):
        alternative = self.attack_root / "alternative"
        problem = json.loads((alternative / "problem.json").read_text())
        problem["shape"]["target_quantity"] = "unknown"
        (alternative / "problem.json").write_text(json.dumps(problem))
        invoke(self.controller, "replan", self.spec, None)
        with self.assertRaises(attack.ValidationError) as caught:
            invoke(self.controller, "begin", begin_spec(), "node-000002")
        self.assertIn("unknown", str(caught.exception))
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)

    def test_invalid_and_stale_orders_commit_no_prefix(self):
        before = self.controller.store.tree_path.read_bytes()
        invalid = order_spec(nodes=["node-000002"])
        with self.assertRaises(SearchError):
            invoke(self.controller, "replan", invalid, None)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
        current = self.controller.status()["revision"]
        with self.assertRaises(SearchError) as caught:
            self.controller.command("replan", self.spec, current - 1, "stale-order")
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
