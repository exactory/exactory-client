"""A new typed-record revision does not manufacture new scientific grounds."""

from research_harness import research_decisions, strategy
from research_harness.evidence import digest
from strategy_fixtures import StrategyCase


class TypedRecordIdentityTests(StrategyCase):
    def record_reference(self, record):
        return {"kind": "record", "record_kind": "research_work_item",
                "id": record["id"], "digest": digest(record)}

    def work_dossier(self, reviewed=False, listed_only=False):
        self.mutate(strategy.record_intent, self.intent())
        original = self.mutate(strategy.record_work_item, self.work_item())["result"]
        value = self.dossier()
        if listed_only:
            value["work_items"] = [original["id"]]
        else:
            value["candidates"][0]["transfer"]["evidence"] = [self.record_reference(original)]
        if reviewed:
            self.reviewed_dossier(dossier_payload=value,
                                  response_b=self.review_response(status="insufficient"))
            self.mutate(research_decisions.record_research_decision, self.decision())
        else:
            self.mutate(strategy.record_strategy, value)
        renamed = self.work_item("work-2")
        renamed["previous"] = original["id"]
        repeated = self.mutate(strategy.record_work_item, renamed)["result"]
        self.assertEqual({k: v for k, v in original["payload"].items() if k not in ("id", "previous")},
                         {k: v for k, v in repeated["payload"].items() if k not in ("id", "previous")})
        return value, self.record_reference(repeated)

    def test_identical_work_revision_cannot_supply_new_scientific_evidence(self):
        value, renamed = self.work_dossier()
        value.update(id="work-relabel-successor", previous="dossier-1", material_change={
            "kind": "new_evidence", "reason": "The same work request now has another revision ID.",
            "evidence": [renamed], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, value))

    def test_identical_listed_work_revision_cannot_supply_new_scientific_evidence(self):
        value, renamed = self.work_dossier(listed_only=True)
        value.update(id="listed-work-relabel-successor", previous="dossier-1", material_change={
            "kind": "new_evidence", "reason": "The already supplied work request has another revision ID.",
            "evidence": [renamed], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, value))

    def test_changed_work_state_with_new_observed_evidence_remains_eligible(self):
        value, repeated = self.work_dossier(listed_only=True)
        observation = self.artifacts.put(b"Exact control observations: mechanism A gives 0; mechanism B gives 1.", "text/plain")
        changed = self.work_item("work-3")
        changed.update(previous=repeated["id"], execution_state="validated", disposition="resolved",
                       evidence=[observation], reason="The exact control observations separate the mechanisms.")
        recorded = self.mutate(strategy.record_work_item, changed)["result"]
        value.update(id="observed-work-successor", previous="dossier-1", material_change={
            "kind": "new_evidence", "reason": "A validated control observation is now available.",
            "evidence": [self.record_reference(recorded)], "prior_objections": []})
        saved = self.mutate(strategy.record_strategy, value)["result"]
        self.assertEqual(saved["payload"]["material_change"]["evidence"], [self.record_reference(recorded)])

    def test_identical_work_revision_cannot_change_the_reconsidered_consequence(self):
        value, renamed = self.work_dossier(reviewed=True)
        value.update(id="work-relabel-reconsidered", previous="dossier-1", material_change={
            "kind": "reconsideration", "reason": "Assess the unchanged requested consequence again.",
            "evidence": [self.evidence], "prior_objections": []},
            reconsideration={"decision_id": "decision-1", "reason": "Reconsider the same consequence.",
                             "alternatives": ["candidate-a", "candidate-b"],
                             "remaining_obligations": ["The complete objective remains unresolved."]})
        value["candidates"][0]["transfer"]["evidence"] = [renamed]
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, value))

    def test_lead_self_id_change_is_already_rejected(self):
        self.mutate(strategy.record_intent, self.intent())
        lead = {"id": "lead-1", "intent_id": "intent-1", "origin": "hypothesis", "origin_id": "same-origin",
                "discrepancy": "The two mechanisms may predict different outcomes.",
                "contradicts": "They have not yet been distinguished.", "verification_status": "suspected",
                "input_knowledge": False, "evidence": [self.evidence],
                "possible_consequence": "A discriminator would separate the mechanisms.",
                "next_check": "Observe both controls.", "disposition": "candidate",
                "reason": "The controlled observation remains unattempted.", "reopen_trigger": None}
        self.mutate(strategy.record_lead, lead)
        self.error("lead_duplicate", lambda: self.mutate(strategy.record_lead, dict(lead, id="lead-2")))
