"""Old scientific bytes cannot become new evidence through dossier bookkeeping."""

import copy

from strategy_fixtures import StrategyCase
from research_harness import research_decisions, strategy


class ScientificEvidenceLineageTests(StrategyCase):
    def test_scientific_scope_kind_is_not_an_evidence_wrapper(self):
        intent = self.intent()
        intent["objective"]["scope"] = {"kind": "source", "domain": "calibrated emission sources"}
        self.mutate(strategy.record_intent, intent)
        original = self.dossier()
        original["objective"] = copy.deepcopy(intent["objective"])
        self.mutate(strategy.record_strategy, original)
        new_evidence = self.artifacts.put(b"A new measured response from the calibrated source.", "text/plain")
        successor = copy.deepcopy(original)
        successor.update(id="new-source-evidence", previous=original["id"], material_change={
            "kind": "new_evidence", "reason": "Assess the newly measured source response.",
            "evidence": [new_evidence], "prior_objections": []})
        saved = self.mutate(strategy.record_strategy, successor)["result"]
        self.assertEqual(saved["payload"]["objective"]["scope"], intent["objective"]["scope"])

    def test_unreviewed_intermediate_dossier_cannot_make_rejected_old_evidence_new(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        self.mutate(research_decisions.record_research_decision, self.decision())
        original = self.evidence
        intervening = self.artifacts.put(b"An intervening control with a distinct observed outcome.", "text/plain")

        def replace_evidence(value):
            if isinstance(value, dict):
                if value == original:
                    return copy.deepcopy(intervening)
                return {key: replace_evidence(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_evidence(item) for item in value]
            return value

        middle = replace_evidence(self.dossier("intervening-dossier"))
        middle.update(previous="dossier-1", material_change={
            "kind": "new_evidence", "reason": "Preserve the distinct intervening control before its assessment.",
            "evidence": [intervening], "prior_objections": []})
        self.mutate(strategy.record_strategy, middle)
        repeated = self.dossier("return-to-old-evidence")
        repeated.update(previous=middle["id"], material_change={
            "kind": "new_evidence", "reason": "Request a new slate for the original unchanged scientific evidence.",
            "evidence": [original], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, repeated))

    def test_changing_artifact_media_type_does_not_supply_new_scientific_bytes(self):
        self.setup_dossier()
        relabeled = dict(self.evidence, media_type="application/octet-stream")
        self.assertEqual(self.artifacts.read(relabeled), self.artifacts.read(self.evidence))
        repeated = self.dossier("media-type-only")
        repeated.update(previous="dossier-1", material_change={
            "kind": "new_evidence", "reason": "The descriptor changed but its scientific bytes did not.",
            "evidence": [relabeled], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, repeated))
