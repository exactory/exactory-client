"""Failure-based pivots, complete claim groups and scientific source stability."""

import copy

from research_harness.evidence import digest
from strategy_fixtures import StrategyCase


class StrategyContinuityTests(StrategyCase):
    def initial(self, *, claims=()):
        value = self.dossier()
        value["claims"] = [{"id": claim, "statement": "The calibrated control has its stated finite bound.",
                            "scope": "The corresponding control only.", "evidence": [self.evidence]}
                           for claim in claims]
        value["failures"] = [{"id": "nonidentifying-fit", "reason": "Both mechanisms produce the same original observable.",
                              "evidence": [self.evidence]}]
        return value

    def successor(self, initial):
        comparison = self.artifacts.put(
            b"An independent observable separates the two mechanisms while the original observable cannot.", "text/plain")
        value = copy.deepcopy(initial)
        value.update(id="dossier-pivot", previous=initial["id"],
                     material_change={"kind": "new_comparison", "reason": "The second observable has distinct mechanism predictions.",
                                      "evidence": [comparison], "prior_objections": []})
        value["candidates"][0].update(method="Orthogonal observable", addition="The independent response distinguishes mechanisms.")
        value["candidates"][0]["next_test"].update(method="Orthogonal observable")
        value["candidates"][0]["evidence"].append(comparison)
        value["failures"][0].update(reason="The first observable remains nonidentifying; the distinct second observable avoids that obstruction.")
        value["tranche"].update(id="tranche-pivot", question="Does the orthogonal observable separate the mechanisms?",
                                 method="Orthogonal observable", predecessor="tranche-1", previous_outcome="failed")
        value["recommendation"] = {"action": "pivot", "reason": "Test the independent response using the retained failure."}
        if initial["claims"]:
            value["continuity"] = [{"claim_ids": [claim["id"] for claim in initial["claims"]], "disposition": "retained",
                                    "reason": "The bounded controls remain valid under the unchanged calibration assumptions.",
                                    "evidence": [self.evidence]}]
        return value

    def test_failure_only_pivot_needs_no_fabricated_positive_claim(self):
        initial = self.initial()
        self.reviewed_dossier(dossier_payload=initial)
        decisions, strategy = self.module("research_decisions"), self.module("strategy")
        self.mutate(decisions.record_research_decision, self.decision())
        successor = self.successor(initial)
        saved = self.mutate(strategy.record_strategy, successor)["result"]
        for suffix in ("a", "b"):
            self.record_response("pivot-" + suffix, "reviewer-" + suffix, self.review_response(), dossier_id=successor["id"])
        payload = self.decision("decision-pivot", action="pivot")
        payload.update(dossier_id=successor["id"], review_ids=["pivot-a", "pivot-b"],
                       reason="The failed fit rules out precision-only work; the independent observable is adequate.")
        self.mutate(decisions.record_research_decision, payload)
        state = decisions.decision_state(self.store.snapshot()["records"], self.artifacts, "cycle")
        self.assertTrue(state["ready"], state["obligations"])
        self.assertEqual(saved["payload"]["claims"], [])
        self.assertEqual(saved["payload"]["continuity"], [])
        self.assertEqual(saved["payload"]["failures"][0]["id"], "nonidentifying-fit")
        self.assertEqual(state["decision"]["payload"]["goal_status"], "open")
        self.assertEqual(self.store.snapshot()["records"]["research_intent"]["intent-1"]["payload"]["full_objective"],
                         self.intent()["full_objective"])

    def test_explicit_complete_claim_group_is_retained_during_a_pivot(self):
        initial = self.initial(claims=("control-a", "control-b", "control-c"))
        self.mutate(self.module("strategy").record_intent, self.intent())
        self.mutate(self.module("strategy").record_strategy, initial)
        saved = self.mutate(self.module("strategy").record_strategy, self.successor(initial))["result"]
        self.assertEqual(saved["payload"]["continuity"][0]["claim_ids"], ["control-a", "control-b", "control-c"])
        self.assertEqual(len(saved["payload"]["continuity"]), 1)
        self.assertEqual(len(saved["payload"]["claims"]), 3)

    def test_incomplete_or_duplicate_group_membership_is_rejected_atomically(self):
        initial = self.initial(claims=("control-a", "control-b", "control-c"))
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        self.mutate(strategy.record_strategy, initial)
        successor = self.successor(initial)
        incomplete = copy.deepcopy(successor)
        incomplete["continuity"][0]["claim_ids"].pop()
        self.error("strategy_claim_history_missing", lambda: self.mutate(strategy.record_strategy, incomplete))
        duplicate = copy.deepcopy(successor)
        duplicate["continuity"].append(copy.deepcopy(duplicate["continuity"][0]))
        self.error("strategy_claim_history_missing", lambda: self.mutate(strategy.record_strategy, duplicate))

    def test_grouping_old_claims_cannot_erase_a_relevant_failed_route(self):
        initial = self.initial(claims=("control-a", "control-b"))
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        self.mutate(strategy.record_strategy, initial)
        successor = self.successor(initial)
        successor["failures"] = []
        self.error("strategy_failure_history_missing", lambda: self.mutate(strategy.record_strategy, successor))

    def source_decision(self):
        source = {"id": "decisive-source", "status": "captured", "response_complete": True,
                  "response": self.evidence, "headers": {"Date": "original-fetch"}}
        self.put("source", source["id"], source)
        dossier = self.dossier()
        dossier["dependencies"] = [{"kind": "source", "id": source["id"], "digest": digest(source)}]
        self.reviewed_dossier(dossier_payload=dossier)
        decisions = self.module("research_decisions")
        self.mutate(decisions.record_research_decision, self.decision())
        state = decisions.decision_state(self.store.snapshot()["records"], self.artifacts, "cycle")
        self.assertTrue(state["ready"], state["obligations"])
        return source

    def test_bound_source_metadata_change_preserves_current_approval(self):
        source = self.source_decision()
        self.put("source", source["id"], dict(source, headers={"Date": "later-fetch", "ETag": "transport-only"}))
        state = self.module("research_decisions").decision_state(self.store.snapshot()["records"], self.artifacts, "cycle")
        self.assertTrue(state["ready"], state["obligations"])

    def test_decisive_source_content_change_requires_current_reassessment(self):
        source = self.source_decision()
        changed = self.artifacts.put(b"The newly captured theorem already supplies the proposed discriminator.", "text/plain")
        self.put("source", source["id"], dict(source, response=changed))
        state = self.module("research_decisions").decision_state(self.store.snapshot()["records"], self.artifacts, "cycle")
        self.assertFalse(state["ready"])
        self.assertIn("research_source_impact_required", {item["code"] for item in state["obligations"]})
        self.assertIn("decision-1", self.store.snapshot()["records"]["research_decision"])
