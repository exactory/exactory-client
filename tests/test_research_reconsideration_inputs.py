"""Reconsideration checks typed evidence before comparing scientific identities."""

from research_harness import integration, research_decisions, strategy
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from strategy_fixtures import StrategyCase


class ReconsiderationInputTests(StrategyCase):
    def unchanged_successor(self, evidence):
        value = self.dossier("reconsidered-input")
        value.update(previous="dossier-1", material_change={
            "kind": "reconsideration", "reason": "Assess the same consequence with a different evidence reference.",
            "evidence": [self.evidence], "prior_objections": []},
            reconsideration={"decision_id": "decision-1", "reason": "Review the recorded consequence again.",
                "alternatives": ["candidate-a", "candidate-b"],
                "remaining_obligations": ["The complete objective remains unresolved."]})
        value["candidates"][0]["transfer"]["evidence"] = evidence
        return value

    def prepare_rejected_decision(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        self.mutate(research_decisions.record_research_decision, self.decision())

    def test_malformed_transfer_evidence_is_an_atomic_structured_rejection(self):
        self.prepare_rejected_decision()
        bad_digest = dict(self.evidence, sha256=[])
        for evidence in (bad_digest, {"kind": "source", "link": {}}):
            with self.subTest(evidence=evidence):
                before = self.store.snapshot()
                with self.assertRaises(ResearchError):
                    self.mutate(strategy.record_strategy, self.unchanged_successor([evidence]))
                self.assertEqual(self.store.snapshot(), before)

    def pinned_alias_reference(self):
        path = self.root / "same-proof-with-another-name.txt"
        path.write_bytes(self.artifacts.read(self.evidence))
        saved = self.mutate(integration.pin_artifact, {"id": "renamed-existing-proof", "path": path.name,
                                                      "media_type": "text/markdown"})["result"]
        self.assertEqual(saved["artifact"]["sha256"], self.evidence["sha256"])
        return {"kind": "record", "record_kind": "local_artifact", "id": saved["id"], "digest": digest(saved)}

    def test_pinned_alias_of_old_bytes_cannot_change_the_reconsidered_consequence(self):
        self.prepare_rejected_decision()
        repeated = self.unchanged_successor([self.pinned_alias_reference()])
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, repeated))

    def test_pinned_alias_of_old_bytes_cannot_supply_new_scientific_evidence(self):
        self.setup_dossier()
        repeated = self.dossier("repinned-evidence")
        repeated.update(previous="dossier-1", material_change={
            "kind": "new_evidence", "reason": "The same proof bytes were pinned with a new local name.",
            "evidence": [self.pinned_alias_reference()], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, repeated))
