"""Intent authority, scientific continuity and immutable strategy dependencies."""

import copy

from research_harness.evidence import digest
from strategy_fixtures import StrategyCase


class IntentContractTests(StrategyCase):
    def test_bare_shared_research_profile_does_not_create_strategic_obligations(self):
        strategy = self.module("strategy")
        self.assertFalse(strategy.managed_research({"configuration": {"research": {"profile": "research"}}}))
        self.assertTrue(strategy.managed_research({"workspace": {"study": {"stage": "literature"}}}))

    def test_original_instruction_is_bound_and_replay_does_not_create_a_revision(self):
        strategy = self.module("strategy")
        payload, revision = self.intent(), self.store.revision
        first = self.mutate(strategy.record_intent, payload, request="intent-request", revision=revision)
        again = self.mutate(strategy.record_intent, payload, request="intent-request", revision=revision)
        self.assertEqual(first, again)
        self.assertEqual(self.store.revision, revision + 1)
        saved = first["result"]
        self.assertEqual(saved["instruction_provenance"]["text"], "Investigate the complete problem and submit the supported result.")
        self.assertIsNone(saved["payload"]["resources"][0]["limit"])

    def test_unpinned_or_noncontext_instruction_cannot_authorize_delivery(self):
        strategy = self.module("strategy")
        for instruction in ("missing", "result-note"):
            if instruction == "result-note":
                self.put("local_artifact", instruction, {"id": instruction, "path": "results/claim.md", "artifact": self.evidence})
            payload = self.intent()
            payload["instruction"] = instruction
            self.error("intent_instruction_missing", lambda: self.mutate(strategy.record_intent, payload))

    def test_later_intent_cannot_silently_replace_the_full_objective(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        revised = self.intent("intent-2")
        revised.update(previous="intent-1", full_objective="Only produce the easy special case.")
        self.error("intent_authority_required", lambda: self.mutate(strategy.record_intent, revised))
        self.assertEqual(self.store.snapshot()["records"]["research_intent"]["intent-1"]["payload"]["full_objective"], self.intent()["full_objective"])

    def test_new_quality_intent_retains_earlier_versions_and_actual_time(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        revised = self.intent("intent-2")
        revised.update(previous="intent-1", recorded_at="2026-10-01T18:00:00Z", user_standard="Resolve the decisive inference.")
        self.put("local_artifact", "quality-instruction", {"id": "quality-instruction", "path": "context/quality.md",
                 "artifact": self.artifacts.put(b"Resolve the decisive inference before publishing.", "text/plain")})
        revised["instruction"] = "quality-instruction"
        self.mutate(strategy.record_intent, revised)
        records = self.store.snapshot()["records"]
        self.assertEqual(len(records["research_intent"]), 2)
        self.assertEqual(records["strategy_selection"]["intent"]["id"], "intent-2")
        self.assertEqual(records["research_intent"]["intent-1"]["payload"]["user_standard"], "unspecified")

    def test_duplicate_ids_unknown_fields_and_stale_revision_are_atomic(self):
        strategy = self.module("strategy")
        revision = self.store.revision
        self.mutate(strategy.record_intent, self.intent())
        changed = self.intent()
        changed["user_standard"] = "Changed"
        self.error("record_conflict", lambda: self.mutate(strategy.record_intent, changed))
        self.error("stale_revision", lambda: self.mutate(strategy.record_intent, self.intent("intent-2"), revision=revision))
        self.error("invalid_intent", lambda: self.mutate(strategy.record_intent, dict(self.intent("intent-2"), secret="field")))


class StrategyRecordTests(StrategyCase):
    def test_prospective_dossier_does_not_require_a_native_objective(self):
        saved = self.setup_dossier()
        self.assertEqual(saved["objective_binding"]["kind"], "proposed")
        self.assertNotIn("research_objective", self.store.snapshot()["records"])
        self.assertEqual(saved["source_snapshot"], {})

    def test_dossier_cannot_rewrite_its_intent_objective(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        payload = self.dossier()
        payload["objective"]["statement"] = "An easier target."
        self.error("strategy_objective_mismatch", lambda: self.mutate(strategy.record_strategy, payload))

    def test_cosmetic_alternative_and_unexplained_single_candidate_fail(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        payload = self.dossier()
        payload["candidates"][1] = dict(payload["candidates"][0], id="renamed")
        self.error("strategy_alternative_duplicate", lambda: self.mutate(strategy.record_strategy, payload))
        payload["candidates"] = payload["candidates"][:1]
        self.error("strategy_alternatives_missing", lambda: self.mutate(strategy.record_strategy, payload))

    def test_single_feasible_candidate_with_evidenced_unavailability_is_accepted(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        payload = self.dossier()
        payload["candidates"] = payload["candidates"][:1]
        payload["single_candidate"] = {"reason": "The alternative data cannot be obtained.", "evidence": [self.evidence]}
        self.mutate(strategy.record_strategy, payload)

    def test_new_id_does_not_reset_the_same_scientific_dossier(self):
        self.setup_dossier()
        strategy = self.module("strategy")
        self.error("strategy_duplicate", lambda: self.mutate(strategy.record_strategy, self.dossier("renamed")))

    def test_unknown_evidence_does_not_create_a_dossier(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        payload = self.dossier()
        payload["candidates"][0]["evidence"] = [dict(self.evidence, sha256="0" * 64, path="research/sources/objects/" + "0" * 64)]
        self.error("artifact_missing", lambda: self.mutate(strategy.record_strategy, payload))

    def test_snapshot_ignores_unrelated_source_headers_but_detects_new_source_bytes(self):
        strategy = self.module("strategy")
        source = {"id": "source-1", "status": "captured", "response_complete": True, "response": self.evidence,
                  "headers": {"Date": "then"}}
        self.put("source", "source-1", source)
        saved = self.setup_dossier()
        self.put("source", "source-1", dict(source, headers={"Date": "now"}))
        self.assertEqual(strategy.source_delta(self.store.snapshot()["records"], saved), [])
        self.put("source", "source-2", dict(source, id="source-2"))
        self.assertEqual(strategy.source_delta(self.store.snapshot()["records"], saved), ["source-2"])

    def test_a_repeated_failed_tranche_requires_material_change(self):
        self.setup_dossier()
        strategy = self.module("strategy")
        next_dossier = self.dossier("dossier-2")
        next_dossier.update(previous="dossier-1")
        next_dossier["tranche"].update(id="renamed-tranche", predecessor="tranche-1", previous_outcome="failed")
        self.error("strategy_material_change_required", lambda: self.mutate(strategy.record_strategy, next_dossier))

    def test_rewording_a_new_root_dossier_cannot_evade_material_change(self):
        self.setup_dossier()
        changed = self.dossier("dossier-2")
        changed["candidates"][0]["addition"] = "The same result described more attractively."
        self.error("strategy_material_change_required", lambda: self.mutate(self.module("strategy").record_strategy, changed))

    def test_new_evidence_means_new_exact_evidence_not_a_new_reason(self):
        self.setup_dossier()
        changed = self.dossier("dossier-2")
        changed.update(previous="dossier-1", material_change={"kind": "new_evidence", "reason": "The evidence is now more persuasive.",
                       "evidence": [self.evidence], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(self.module("strategy").record_strategy, changed))

    def test_unchanged_intent_label_cannot_resample_rejected_science(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        self.mutate(self.module("research_decisions").record_research_decision, self.decision())
        changed = self.dossier("dossier-2")
        changed.update(previous="dossier-1", material_change={"kind": "intent_change", "reason": "Ask another pair to assess the unchanged intent.",
                       "evidence": [self.evidence], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(self.module("strategy").record_strategy, changed))

    def test_renamed_identical_intent_is_not_a_material_intent_change(self):
        self.setup_dossier()
        intent = self.intent("intent-2")
        intent.update(previous="intent-1", recorded_at="2026-10-01T18:00:00Z")
        self.mutate(self.module("strategy").record_intent, intent)
        changed = self.dossier("dossier-2")
        changed.update(previous="dossier-1", intent_id="intent-2", material_change={"kind": "intent_change", "reason": "A new intent record exists.",
                       "evidence": [self.evidence], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(self.module("strategy").record_strategy, changed))

    def test_actual_new_user_quality_condition_allows_intent_reassessment(self):
        self.setup_dossier()
        self.put("local_artifact", "quality-instruction", {"id": "quality-instruction", "path": "context/quality.md",
            "artifact": self.artifacts.put(b"Require a test of the decisive inference before publication.", "text/plain")})
        intent = self.intent("intent-2")
        intent.update(previous="intent-1", instruction="quality-instruction", user_standard="Test the decisive inference.")
        self.mutate(self.module("strategy").record_intent, intent)
        changed = self.dossier("dossier-2")
        changed.update(previous="dossier-1", intent_id="intent-2", material_change={"kind": "intent_change", "reason": "The user imposed a new scientific quality condition.",
                       "evidence": [self.evidence], "prior_objections": []})
        self.mutate(self.module("strategy").record_strategy, changed)
        self.assertEqual(self.store.snapshot()["records"]["strategy_dossier"]["dossier-2"]["payload"]["intent_id"], "intent-2")

    def test_unadjudicated_review_correction_cannot_create_another_review_slate(self):
        self.setup_dossier()
        changed = self.dossier("dossier-2")
        changed.update(previous="dossier-1", material_change={"kind": "review_correction", "reason": "The author prefers a different reading.",
                       "evidence": [self.evidence], "prior_objections": []})
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(self.module("strategy").record_strategy, changed))

    def test_an_unreviewed_intermediate_dossier_cannot_erase_an_earlier_objection(self):
        objection = {"id": "counterexample", "claim": "The proposed inference", "reason": "The same observation fits two mechanisms.",
                     "evidence": [self.evidence], "resolution_condition": "Supply a deciding observation."}
        self.reviewed_dossier(response_b=self.review_response(objections=[objection]))
        evidence = self.artifacts.put(b"A new control result with its actual unchanged ambiguity.", "text/plain")
        changed = self.dossier("dossier-2")
        changed.update(previous="dossier-1", material_change={"kind": "new_evidence", "reason": "Record the new control.", "evidence": [evidence],
            "prior_objections": [{"id": "counterexample", "status": "continuing", "reason": "The control does not yet decide the inference.", "evidence": []}]})
        self.mutate(self.module("strategy").record_strategy, changed)
        next_evidence = self.artifacts.put(b"A second control result before a new scientific assessment.", "text/plain")
        successor = self.dossier("dossier-3")
        successor.update(previous="dossier-2", material_change={"kind": "new_evidence", "reason": "Record the second control.",
            "evidence": [next_evidence], "prior_objections": []})
        self.error("strategy_objection_history_missing", lambda: self.mutate(self.module("strategy").record_strategy, successor))

    def test_reconsideration_can_reuse_results_but_preserves_original_alternatives(self):
        dossier = self.setup_dossier()
        self.put("research_decision", "original", {"id": "original", "payload": {"dossier_id": dossier["id"]}})
        changed = self.dossier("reconsidered")
        changed.update(previous=dossier["id"], material_change={"kind": "reconsideration", "reason": "Assess the valuable restricted inference on its own terms.",
                       "evidence": [self.evidence], "prior_objections": []},
                       reconsideration={"decision_id": "original", "reason": "The same evidence supports a narrower result.",
                                        "alternatives": ["candidate-a", "candidate-b"], "remaining_obligations": ["The wider mechanism remains unresolved."]})
        changed["candidates"][0]["addition"] = "The restricted witness excludes the stronger universal statement."
        self.mutate(self.module("strategy").record_strategy, changed)
        self.assertEqual(len(self.store.snapshot()["records"]["strategy_dossier"]), 2)

    def test_new_result_dossier_binds_the_selected_publication_bundle(self):
        strategy = self.module("strategy")
        self.put("research_objective", "native", {"kind": "objective", "id": "native", "statement": "A supported finite theorem."})
        intent = self.intent()
        intent["objective"] = {"kind": "native", "id": "native"}
        self.mutate(strategy.record_intent, intent)
        self.put("publication_bundle", "paper", {"id": "paper", "digest": "a" * 64})
        self.put("publication_selection", "bundle", {"id": "paper"})
        dossier = self.dossier()
        dossier.update(objective=intent["objective"], phase="post_measurement", bundle_digest="a" * 64)
        self.mutate(strategy.record_strategy, dossier)

    def test_a_renamed_work_item_cannot_erase_a_repeated_original_request(self):
        strategy = self.module("strategy")
        self.mutate(strategy.record_intent, self.intent())
        self.mutate(strategy.record_work_item, self.work_item())
        renamed = self.work_item("renamed-record")
        renamed["item_id"] = "renamed-gap"
        self.error("work_item_request_reassigned", lambda: self.mutate(strategy.record_work_item, renamed))


class WorkAndLeadTests(StrategyCase):
    def setUp(self):
        super().setUp()
        self.strategy = self.module("strategy")
        self.mutate(self.strategy.record_intent, self.intent())

    def test_adopting_a_request_does_not_make_it_executed(self):
        saved = self.mutate(self.strategy.record_work_item, self.work_item())["result"]
        self.assertEqual(saved["payload"]["execution_state"], "planned")
        invalid = self.work_item("work-2")
        invalid.update(previous="work-1", disposition="resolved")
        self.error("work_item_unresolved", lambda: self.mutate(self.strategy.record_work_item, invalid))

    def test_rejection_or_deferral_retains_the_execution_state_and_reopening_trigger(self):
        payload = self.work_item()
        payload.update(disposition="deferred", reason="The required data are unavailable.", evidence=[self.evidence])
        self.error("work_item_reopening_required", lambda: self.mutate(self.strategy.record_work_item, payload))
        payload["reopen_trigger"] = "The data become available."
        saved = self.mutate(self.strategy.record_work_item, payload)["result"]
        self.assertEqual(saved["payload"]["execution_state"], "planned")

    def test_a_successor_preserves_every_original_request_and_stable_identity(self):
        self.mutate(self.strategy.record_work_item, self.work_item())
        changed = self.work_item("work-2")
        changed.update(previous="work-1", requests=[])
        self.error("work_item_history_missing", lambda: self.mutate(self.strategy.record_work_item, changed))

    def test_prior_verification_finding_is_retained_as_input_knowledge(self):
        payload = {"id": "lead-1", "intent_id": "intent-1", "origin": "prior_verification", "origin_id": "old-verdict",
                   "discrepancy": "The exact supplied value differs from the paper.", "contradicts": "Its stated formula.",
                   "verification_status": "verified_in_origin", "input_knowledge": True, "evidence": [self.evidence],
                   "possible_consequence": "An omitted assumption might matter.", "next_check": "Check conventions and units.",
                   "disposition": "candidate", "reason": "The discrepancy affects the deciding claim.", "reopen_trigger": None}
        saved = self.mutate(self.strategy.record_lead, payload)["result"]
        self.assertTrue(saved["payload"]["input_knowledge"])
        payload = dict(payload, id="lead-2", input_knowledge=False)
        self.error("lead_origin_mismatch", lambda: self.mutate(self.strategy.record_lead, payload))
