"""Deferral changes research selection without changing claims or authority."""

import copy
import json
import shutil
import unittest

import attack
from search_controller.errors import SearchError
from search_controller.journal_io import journal_ownership
from search_controller.scheduler import next_action
from search_controller.service import Controller
from tests.search_execution_support import admit_workspace, begin_spec, invoke
from tests.search_fixtures import proposal, provenance, review
from tests.support import FIXTURE_STRATEGIES, WorkspaceTest, make_move
from tests.test_obligation_order import direct_alternatives
from tests.test_search_proof import accepted, admitted, event, route_state
from tests.test_search_scheduler import facts_record


def deferral_spec():
    return {
        "route_orders": [],
        "deferred_node_ids": ["node-000001"],
        "progress_acceptance_ids": [],
        "reason": "The unstarted method lacks its required operation; retain its open claim.",
    }


def retreated_alternatives():
    criterion = {"kind": "strategy_failure", "strategy_id": "induction"}
    alternative = copy.deepcopy(direct_alternatives()["proposals"]["proposal-000002"]["record"])
    alternative["retreat_criteria"] = [criterion]
    state = admitted(route_state(), alternative)
    candidate = proposal()
    candidate.update(attack_slug="tail", target_obligation="obligation-000003",
                     claim=copy.deepcopy(state["obligations"]["obligation-000003"]["claim"]))
    candidate["contribution"]["route"] = "route-000001"
    candidate["checkpoint_criteria"][0]["obligation_id"] = "obligation-000003"
    state = admitted(state, candidate)
    state = event(state, "node_facts_recorded", {"facts": facts_record(
        node_id="node-000002", failed_strategies=["induction"])})
    return event(state, "node_retreated", {"retreat": {
        "node_id": "node-000002", "criterion": criterion,
        "failed_hypothesis": "The fixture induction closes the obligation",
        "observation": "The fixture induction does not close the obligation",
        "last_checkpoint_id": None, "remaining_assumption_ids": [],
        "reconsideration": "A verified induction step becomes available",
        "abandoned_route_ids": [], "abandoned_assumption_ids": [],
    }})


class NodeDeferralTests(unittest.TestCase):
    def test_retreat_criterion_matches_recorded_admission(self):
        state = retreated_alternatives()
        node = state["nodes"]["node-000002"]
        recorded = state["proposals"][node["proposal_id"]]["record"]
        self.assertEqual(recorded["retreat_criteria"], node["retreat_criteria"])
        self.assertEqual(node["retreat_criteria"], [{"kind": "strategy_failure", "strategy_id": "induction"}])
        self.assertEqual(state["control"]["retreats"][0]["criterion"],
                         {"kind": "strategy_failure", "strategy_id": "induction"})

    def test_deferral_reaches_another_obligation_after_retreat(self):
        state = retreated_alternatives()
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000001"})
        try:
            updated = event(state, "replan_recorded", deferral_spec())
        except SearchError as error:
            self.fail("An unstarted alternative must be deferrable: " + error.code)
        self.assertEqual(next_action(updated), {"kind": "execute_node", "node_id": "node-000003"})
        for field in ["contract", "nodes", "obligations", "routes", "accounts", "totals",
                      "acceptances", "proof_status"]:
            self.assertEqual(updated[field], state[field], field)
        self.assertEqual(updated["control"]["retreats"], state["control"]["retreats"])
        self.assertEqual(updated["control"]["nonprogress_replans"], 1)
        for field in state["control"]:
            if field not in {"deferred_node_ids", "nonprogress_replans"}:
                self.assertEqual(updated["control"][field], state["control"][field], field)

    def test_omission_preserves_deferral_and_empty_list_restores_nearest_alternative(self):
        initial = retreated_alternatives()
        legacy = deferral_spec()
        del legacy["deferred_node_ids"]
        self.assertEqual(event(initial, "replan_recorded", legacy)["control"]["deferred_node_ids"], [])
        state = event(initial, "replan_recorded", deferral_spec())
        kept = event(state, "replan_recorded", legacy)
        self.assertEqual(next_action(kept), {"kind": "execute_node", "node_id": "node-000003"})
        cleared = event(state, "replan_recorded", dict(deferral_spec(), deferred_node_ids=[]))
        self.assertEqual(next_action(cleared), {"kind": "execute_node", "node_id": "node-000001"})

    def test_invalid_shapes_references_and_extra_fields_are_atomic(self):
        for ids in [None, {}, "node-000001", [False], ["missing"], ["node-000001", "node-000001"]]:
            with self.subTest(ids=ids):
                state = retreated_alternatives()
                before = copy.deepcopy(state)
                with self.assertRaises(SearchError):
                    event(state, "replan_recorded", dict(deferral_spec(), deferred_node_ids=ids))
                self.assertEqual(state, before)
        with self.assertRaises(SearchError):
            event(state, "replan_recorded", dict(deferral_spec(), force=True))
        self.assertEqual(state, before)

    def test_validator_is_pure_and_does_not_alias_the_requested_list(self):
        from search_controller.deferrals import validate_deferrals
        state = retreated_alternatives()
        before = copy.deepcopy(state)
        ids = ["node-000001"]
        result = validate_deferrals(state, ids)
        ids.append("node-000003")
        self.assertEqual(result, ["node-000001"])
        self.assertEqual(state, before)

    def assert_refused(self, state, code):
        before = copy.deepcopy(state)
        with self.assertRaises(SearchError) as caught:
            event(state, "replan_recorded", deferral_spec())
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(state, before)

    def test_unadmitted_and_standalone_nodes_are_ineligible(self):
        for field, value in [("proposal_id", None), ("category", "standalone")]:
            with self.subTest(field=field):
                state = retreated_alternatives()
                state["nodes"]["node-000001"][field] = value
                self.assert_refused(state, "admission_required")

    def test_started_lifecycle_and_active_pointer_are_ineligible(self):
        for status in ["active", "waiting", "result_ready", "finished", "retreated"]:
            with self.subTest(status=status):
                state = retreated_alternatives()
                state["nodes"]["node-000001"]["status"] = status
                self.assert_refused(state, "deferral_unavailable")
        state = retreated_alternatives()
        state["control"]["active_node_id"] = "node-000001"
        self.assert_refused(state, "deferral_unavailable")

    def test_historical_move_or_run_prevents_deferral_despite_admitted_status(self):
        for kind in ["move", "run"]:
            with self.subTest(kind=kind):
                state = retreated_alternatives()
                records = state["service"]["moves"] if kind == "move" else state["runs"]
                records[kind + "-000001"] = {"id": kind + "-000001", "node_id": "node-000001",
                                               "status": "terminal"}
                self.assert_refused(state, "deferral_unavailable")

    def test_pending_execution_blocks_setting_and_clearing_deferrals(self):
        for status in ["move", "reserved", "launched", "indeterminate"]:
            with self.subTest(status=status):
                state = retreated_alternatives()
                if status == "move":
                    state["control"]["pending_moves"] = ["move-000001"]
                else:
                    state["runs"]["run-000001"] = {"id": "run-000001", "node_id": "node-000003", "status": status}
                self.assert_refused(state, "execution_pending")
                with self.assertRaises(SearchError) as caught:
                    event(state, "replan_recorded", dict(deferral_spec(), deferred_node_ids=[]))
                self.assertEqual(caught.exception.code, "execution_pending")

    def test_result_and_cashout_facts_prevent_deferral(self):
        for field, value in [("result_action", "snapshot"), ("cashout_action", "inventory")]:
            with self.subTest(field=field):
                state = retreated_alternatives()
                state = event(state, "node_facts_recorded", {"facts": facts_record(status="admitted", **{field: value})})
                self.assert_refused(state, "deferral_unavailable")

    def test_pause_focus_and_state_error_remain_authoritative(self):
        state = retreated_alternatives()
        paused = event(state, "control_recorded", {"action": "pause", "reason": "User pause"})
        self.assertEqual(next_action(event(paused, "replan_recorded", deferral_spec())),
                         {"kind": "paused", "reason": "User pause"})
        ambiguous = event(state, "control_recorded", {"action": "focus", "focus": "ambiguous",
                                                     "provenance": provenance("operator")})
        self.assertEqual(next_action(event(ambiguous, "replan_recorded", deferral_spec())),
                         {"kind": "handoff", "reason": "Objective focus is ambiguous"})
        state["control"]["state_error"] = "Unresolved audit error"
        self.assertEqual(next_action(event(state, "replan_recorded", deferral_spec())),
                         {"kind": "blocked", "reason": "Unresolved audit error"})

    def test_prerequisite_and_account_or_total_exhaustion_never_gain_authority(self):
        for blocker in ["prerequisite", "account", "total"]:
            with self.subTest(blocker=blocker):
                state = retreated_alternatives()
                if blocker == "prerequisite":
                    state = event(state, "node_facts_recorded", {"facts": facts_record(
                        node_id="node-000003", status="waiting", waiting_on=["obligation-000004"])})
                elif blocker == "account":
                    account_id = state["nodes"]["node-000003"]["account_id"]
                    state["accounts"][account_id]["used_moves"] = 24
                updated = event(state, "replan_recorded", deferral_spec())
                if blocker == "total":
                    updated["contract"]["resource_policy"]["max_total_moves"] = 1
                    updated["totals"]["used_moves"] = 1
                    state["totals"]["used_moves"] = 1
                self.assertEqual(next_action(updated)["kind"], "replan")
                self.assertEqual(updated["accounts"], state["accounts"])
                self.assertEqual(updated["totals"], state["totals"])

    def test_due_result_root_closure_and_cashout_precede_deferred_research(self):
        for field, value, expected in [
            ("result_action", "snapshot", {"kind": "prepare_result", "node_id": "node-000003", "step": "snapshot"}),
            ("cashout_action", "inventory", {"kind": "local_cashout", "node_id": "node-000003", "step": "inventory"}),
        ]:
            with self.subTest(field=field):
                state = event(retreated_alternatives(), "node_facts_recorded", {"facts": facts_record(
                    node_id="node-000003", **{field: value})})
                self.assertEqual(next_action(event(state, "replan_recorded", deferral_spec())), expected)
        state = accepted(retreated_alternatives(), "obligation-000001")
        self.assertEqual(next_action(event(state, "replan_recorded", deferral_spec())),
                         {"kind": "finalize_root", "outcome": "proof", "acceptance_ids": ["acceptance-000001"]})

    def test_three_replans_pause_without_credit_or_refund(self):
        initial = retreated_alternatives()
        state = initial
        for ids in [["node-000001"], [], ["node-000001"]]:
            state = event(state, "replan_recorded", dict(deferral_spec(), deferred_node_ids=ids))
        self.assertEqual(next_action(state), {"kind": "paused", "reason": "Three replans without new verified progress"})
        self.assertEqual(state["control"]["nonprogress_replans"], 3)
        self.assertEqual(state["control"]["progress_fingerprints"], [])
        self.assertEqual(state["accounts"], initial["accounts"])
        self.assertEqual(state["totals"], initial["totals"])

    def test_deferral_composes_with_orders_and_credits_verified_progress_only_once(self):
        state = accepted(direct_alternatives(), "obligation-000003")
        spec = dict(deferral_spec(), progress_acceptance_ids=["acceptance-000001"],
                    obligation_orders=[{"obligation_id": "obligation-000002",
                                        "alternative_order": ["node-000001", "node-000002"]}],
                    route_orders=[{"route_id": "route-000001", "alternative_order": [], "selected": True}])
        state = event(state, "replan_recorded", spec)
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000002"})
        self.assertEqual(state["control"]["nonprogress_replans"], 0)
        state = event(state, "replan_recorded", dict(spec, deferred_node_ids=[]))
        self.assertEqual(next_action(state), {"kind": "execute_node", "node_id": "node-000001"})
        self.assertEqual(state["control"]["nonprogress_replans"], 1)
        self.assertEqual(len(state["control"]["progress_fingerprints"]), 1)

    def test_later_due_result_or_cashout_on_deferred_node_is_not_hidden(self):
        state = event(retreated_alternatives(), "replan_recorded", deferral_spec())
        for field, value, expected in [
            ("result_action", "snapshot", {"kind": "prepare_result", "node_id": "node-000001", "step": "snapshot"}),
            ("cashout_action", "inventory", {"kind": "local_cashout", "node_id": "node-000001", "step": "inventory"}),
        ]:
            with self.subTest(field=field):
                observed = event(state, "node_facts_recorded", {"facts": facts_record(status="admitted", **{field: value})})
                self.assertEqual(next_action(observed), expected)


class NodeDeferralServiceTests(WorkspaceTest):
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
        self.alternative = self.attack_root / "alternative"
        for name in ["problem.json", "preconditions.json", "ranking.json", "novelty.md"]:
            shutil.copyfile(self.workspace / name, self.alternative / name)
        shutil.copytree(self.workspace / "study", self.alternative / "study", dirs_exist_ok=True)
        self.assertEqual(self.run_cli("plan", "alternative")[0], 0)

    def assert_public_refused(self, code, spec=None):
        before = self.controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            invoke(self.controller, "replan", deferral_spec() if spec is None else spec, None)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_public_replay_commits_one_event_and_one_round(self):
        before = self.controller.status()
        events = self.controller.store.read()["events"]
        result = self.controller.command("replan", deferral_spec(), before["revision"], "defer-once")
        restored = Controller(self.attack_root, FIXTURE_STRATEGIES)
        after = restored.status()
        self.assertEqual(next_action(after), {"kind": "execute_node", "node_id": "node-000002"})
        self.assertEqual(restored.store.read()["events"][:-1], events)
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after["control"]["nonprogress_replans"], 1)
        for field in ["nodes", "obligations", "accounts", "totals", "acceptances", "proof_status"]:
            self.assertEqual(after[field], before[field])
        self.assertEqual(restored.command("replan", deferral_spec(), before["revision"], "defer-once"), result)
        self.assertEqual(restored.status(), after)

    def test_invalid_stale_and_conflicting_requests_commit_no_prefix(self):
        for spec in [dict(deferral_spec(), deferred_node_ids=["node-000001", "missing"]),
                     dict(deferral_spec(), progress_acceptance_ids=["missing"]),
                     dict(deferral_spec(), route_orders=[{"route_id": "missing", "alternative_order": [], "selected": True}]),
                     dict(deferral_spec(), force=True)]:
            with self.subTest(spec=spec):
                before = self.controller.store.tree_path.read_bytes()
                with self.assertRaises(SearchError):
                    invoke(self.controller, "replan", spec, None)
                self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
        revision = self.controller.status()["revision"]
        before = self.controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            self.controller.command("replan", deferral_spec(), revision - 1, "stale")
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
        self.controller.command("replan", deferral_spec(), revision, "unique-request")
        before = self.controller.store.tree_path.read_bytes()
        with self.assertRaises(SearchError) as caught:
            self.controller.command("replan", dict(deferral_spec(), deferred_node_ids=[]), revision, "unique-request")
        self.assertEqual(caught.exception.code, "request_id_conflict")
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)

    def test_nonempty_native_journal_is_refused_without_erasure(self):
        journal = self.workspace / "journal.jsonl"
        journal.write_text(json.dumps(make_move(1)) + "\n")
        before = journal.read_bytes()
        self.assert_public_refused("deferral_unavailable")
        self.assertEqual(journal.read_bytes(), before)

    def test_inconsistent_native_journal_is_refused_without_erasure(self):
        journal = self.workspace / "journal.jsonl"
        journal.write_text("invalid fixture journal\n")
        before = journal.read_bytes()
        self.assert_public_refused("deferral_unavailable")
        self.assertEqual(journal.read_bytes(), before)

    def test_native_finish_is_refused_without_erasure(self):
        finished = self.workspace / "units/FINISHED.json"
        finished.write_text(json.dumps({"status": "inconclusive"}))
        before = finished.read_bytes()
        self.assert_public_refused("deferral_unavailable")
        self.assertEqual(finished.read_bytes(), before)

    def test_current_native_cashout_is_refused_despite_stale_state_facts(self):
        openings = self.workspace / "openings.json"
        value = json.loads(openings.read_text())
        value["openings"] = []
        openings.write_text(json.dumps(value))
        self.assertIsNone(self.controller.status()["control"]["node_facts"]["node-000001"]["cashout_action"])
        self.assert_public_refused("deferral_unavailable")

    def test_every_requested_native_node_is_audited_atomically(self):
        (self.alternative / "journal.jsonl").write_text(json.dumps(make_move(1)) + "\n")
        self.assert_public_refused("deferral_unavailable", dict(
            deferral_spec(), deferred_node_ids=["node-000001", "node-000002"]))
        with journal_ownership(self.controller, "node-000001"):
            self.assertEqual(self.controller.status()["control"]["deferred_node_ids"], [])

    def test_missing_native_journal_is_refused_and_original_is_preserved(self):
        (self.workspace / "journal.jsonl").rename(self.workspace / "saved-journal.jsonl")
        self.assert_public_refused("deferral_unavailable")
        self.assertEqual((self.workspace / "saved-journal.jsonl").read_bytes(), b"")

    def test_three_public_replans_remain_paused_after_reload(self):
        initial = self.controller.status()
        for ids in [["node-000001"], [], ["node-000001"]]:
            invoke(self.controller, "replan", dict(deferral_spec(), deferred_node_ids=ids), None)
        state = Controller(self.attack_root, FIXTURE_STRATEGIES).status()
        self.assertEqual(next_action(state), {"kind": "paused", "reason": "Three replans without new verified progress"})
        self.assertEqual(state["control"]["nonprogress_replans"], 3)
        self.assertEqual(state["control"]["progress_fingerprints"], [])
        self.assertEqual(state["accounts"], initial["accounts"])
        self.assertEqual(state["totals"], initial["totals"])

    def test_live_journal_owner_prevents_deferral(self):
        with journal_ownership(self.controller, "node-000001"):
            self.assert_public_refused("journal_owned")
        invoke(self.controller, "replan", deferral_spec(), None)
        self.assertEqual(next_action(self.controller.status()), {"kind": "execute_node", "node_id": "node-000002"})

    def test_legacy_replan_does_not_add_a_native_deferral_audit(self):
        (self.workspace / "journal.jsonl").write_text(json.dumps(make_move(1)) + "\n")
        spec = deferral_spec()
        del spec["deferred_node_ids"]
        invoke(self.controller, "replan", spec, None)
        self.assertEqual(self.controller.status()["control"]["deferred_node_ids"], [])

    def test_deferred_begin_fails_frontier_and_selected_begin_still_needs_admission(self):
        invoke(self.controller, "replan", deferral_spec(), None)
        for target, spec, code in [
            ("node-000001", begin_spec(), "frontier_required"),
            ("node-000002", dict(begin_spec(), strategy="ladder-the-parameter"), "admission_required"),
        ]:
            with self.subTest(target=target):
                before = self.controller.store.tree_path.read_bytes()
                with self.assertRaises(SearchError) as caught:
                    invoke(self.controller, "begin", spec, target)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
                self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)
        invoke(self.controller, "begin", begin_spec(), "node-000002")
        state = self.controller.status()
        self.assertEqual(state["control"]["active_node_id"], "node-000002")
        self.assertEqual(state["totals"]["reserved_moves"], 1)
        self.assertEqual(state["totals"]["used_moves"], 0)
        self.assertEqual(len(state["service"]["moves"]), 1)
        self.assert_public_refused("execution_pending")

    def test_selected_unknown_trigger_still_fails_native_validation(self):
        problem = json.loads((self.alternative / "problem.json").read_text())
        problem["shape"]["target_quantity"] = "unknown"
        (self.alternative / "problem.json").write_text(json.dumps(problem))
        invoke(self.controller, "replan", deferral_spec(), None)
        before = self.controller.store.tree_path.read_bytes()
        with self.assertRaises(attack.ValidationError) as caught:
            invoke(self.controller, "begin", begin_spec(), "node-000002")
        self.assertIn("unknown", str(caught.exception))
        self.assertEqual(self.controller.store.tree_path.read_bytes(), before)
        self.assertEqual(self.controller.status()["totals"]["reserved_moves"], 0)

    def test_generated_views_show_deferral_and_preserve_open_claim_and_status(self):
        before_tree = (self.attack_root / "SEARCH_TREE.md").read_text().splitlines()
        before_lineage = (self.workspace / "LINEAGE.md").read_text().splitlines()
        invoke(self.controller, "replan", deferral_spec(), None)
        tree = (self.attack_root / "SEARCH_TREE.md").read_text()
        lineage = (self.workspace / "LINEAGE.md").read_text()
        for line in before_tree:
            if line.startswith("Recorded context:"):
                self.assertNotIn(line, tree)
            else:
                self.assertIn(line, tree)
        for line in before_lineage:
            if line.startswith("Recorded context:"):
                self.assertNotIn(line, lineage)
            elif not line.startswith("Deferred from new research selection:"):
                self.assertIn(line, lineage)
        current_context = "Recorded context: " + self.controller.status()["strategy_assessment"]["context_digest"]
        self.assertIn(current_context, tree)
        self.assertIn(current_context, lineage)
        self.assertIn("node-000001: deferred from new research selection", tree)
        self.assertIn("Deferred from new research selection: yes", lineage)
        self.assertIn("Status: admitted", lineage)
        self.assertIn("Claim: True holds.", lineage)
        self.assertIn("obligation-000001: True holds.", tree)
        self.assertIn("Recorded proof status: open", tree)
        invoke(self.controller, "replan", dict(deferral_spec(), deferred_node_ids=[]), None)
        self.assertIn("Deferred from new research selection: no", (self.workspace / "LINEAGE.md").read_text())
        self.assertNotIn("node-000001: deferred from new research selection", (self.attack_root / "SEARCH_TREE.md").read_text())


if __name__ == "__main__":
    unittest.main()
