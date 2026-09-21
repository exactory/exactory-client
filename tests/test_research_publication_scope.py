"""An explicitly scoped paper cannot close or hide the wider objective."""

import copy
import json

from source_limited_fixtures import SourceLimitedCase
from research_harness.cli import OPERATIONS


class PublicationScopeTests(SourceLimitedCase):
    def test_supported_manuscript_keeps_full_objective_and_checkpoint_incomplete(self):
        self.assertIn("publication-scope", OPERATIONS)
        payload = self.prepare_source_limited()
        historical = copy.deepcopy(self.store.snapshot()["records"])
        self.record_scope(payload)
        self.accept_scope()
        report = self.manuscript_readiness()
        self.assertTrue(report["manuscript_ready"], report["manuscript_obligations"])
        self.assertFalse(report["objective_complete"])
        self.assertFalse(self.development().readiness_report(self.store)["ready"])
        self.assertIn(self.debt, report["remaining_obligations"])
        for kind in ("cycle_assessment", "checkpoint", "strategy_account", "cycle_plan"):
            self.assertEqual(historical[kind], self.store.snapshot()["records"][kind])
        self.assertFalse(historical["cycle_assessment"]["assessment-1"]["assessment"]["complete"])

    def test_debt_mapping_must_match_every_current_binding(self):
        self.assertIn("publication-scope", OPERATIONS)
        payload = self.prepare_source_limited()
        variants = []
        for key, value in (("obligation", "An unperformed available comparison."), ("deferral_id", "forged"),
                           ("version_id", self.links[0]["version_id"]), ("dependency_digest", "0" * 64),
                           ("dependent_claim", "An unrelated claim.")):
            invalid = copy.deepcopy(payload)
            invalid["deferred_objective_obligations"][0][key] = value
            variants.append(invalid)
        variants.extend([dict(payload, deferred_objective_obligations=[]),
                         dict(payload, deferred_objective_obligations=payload["deferred_objective_obligations"] * 2)])
        for invalid in variants:
            with self.subTest(mapping=invalid["deferred_objective_obligations"]):
                self.assert_error("publication_scope_debt_mismatch", lambda: self.record_scope(invalid))

    def test_later_acceptance_cannot_erase_adverse_same_target(self):
        self.assertIn("scoped-review", OPERATIONS)
        self.prepare_source_limited()
        self.record_scope()
        api = self.scope_api()
        self.mutate(api.record_scoped_readiness_review, self.scope_review(verdict="not_ready"))
        rejected = copy.deepcopy(self.store.snapshot()["records"]["scoped_readiness_review"])
        self.mutate(api.record_scoped_readiness_review, self.scope_review("later", assessor="other-reviewer"))
        report = self.manuscript_readiness()
        self.assertFalse(report["manuscript_ready"])
        self.assertIn("scoped_target_rejected", {o["code"] for o in report["manuscript_obligations"]})
        self.assertEqual(rejected["scope-review"], self.store.snapshot()["records"]["scoped_readiness_review"]["scope-review"])

    def test_preparer_cannot_review_under_normalized_alias(self):
        self.assertIn("scoped-review", OPERATIONS)
        self.prepare_source_limited()
        self.record_scope()
        review = self.scope_review(assessor="  SCOPE-PREPARER  ")
        self.assert_error("review_not_independent", lambda: self.mutate(self.scope_api().record_scoped_readiness_review, review))

    def test_available_work_remains_blocking(self):
        self.assertIn("publication-scope", OPERATIONS)
        self.prepare_source_limited()
        changed = copy.deepcopy(self.assessment_payload)
        changed["id"] = "assessment-2"
        changed["development"]["alternatives"][0]["disposition"] = "pursue"
        self.mutate(self.development().assess_cycle, changed)
        self.save_checkpoint(assessment_id="assessment-2", identifier="checkpoint-2")
        self.record_scope()
        self.accept_scope()
        report = self.manuscript_readiness()
        self.assertFalse(report["manuscript_ready"])
        self.assertIn("useful_development_remaining", {o["code"] for o in report["manuscript_obligations"]})

    def test_publication_uses_exact_scoped_claims_and_excludes_scientific_preparer(self):
        from research_harness import publication
        from integration_fixtures import build_manuscript_review
        self.prepare_source_limited()
        self.record_scope()
        self.accept_scope()
        bundle = self.scoped_manuscript()
        self.assertEqual(bundle["publication_scope"]["contract_id"], "scope-1")
        review = build_manuscript_review(self, bundle, " SCOPE-PREPARER ")
        self.assert_error("review_not_independent", lambda: self.mutate(publication.record_manuscript_review, review))

    def test_scoped_gate_checks_both_sides_of_experiment_to_write(self):
        from research_harness.gates import gate_report, validate_transition
        self.prepare_source_limited()
        self.record_scope()
        self.accept_scope()
        self.assertTrue(gate_report(self.store, "manuscript-readiness")["ready"])
        self.assertFalse(gate_report(self.store, "readiness")["ready"])
        validate_transition(self.store.snapshot()["records"], self.artifacts,
                            {"stage": "experiment", "status": "active"}, {"stage": "write", "status": "active"})
        self.mutate(self.scope_api().select_publication_scope, {"id": None})
        self.assert_error("readiness_required", lambda: validate_transition(self.store.snapshot()["records"], self.artifacts,
                         {"stage": "experiment", "status": "active"}, {"stage": "write", "status": "active"}))

    def test_cosmetic_ids_and_whitespace_keep_scientific_target(self):
        from research_harness.development import _Context
        self.prepare_source_limited()
        original = self.record_scope()
        payload = copy.deepcopy(original["payload"])
        payload["id"] = "renamed"
        payload["scope"]["id"] = "renamed-scope"
        payload["scope"]["statement"] = "  " + payload["scope"]["statement"].replace(" ", "\n ")
        payload["supported_claims"][0]["id"] = "new-claim-id"
        payload["public_limitations"][0]["id"] = "renamed-limit"
        payload["supported_claims"][0]["limitation_ids"] = ["renamed-limit"]
        target = self.scope_api().build_scientific_target(_Context(self.store.snapshot()["records"], self.artifacts), payload)
        self.assertEqual(target, original["scientific_projection"])
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review(verdict="not_ready"))
        self.assert_error("publication_scope_correction_required", lambda: self.record_scope(payload))

    def test_ready_verdict_with_failed_check_is_permanently_adverse(self):
        self.prepare_source_limited()
        self.record_scope()
        review = self.scope_review()
        review["checks"][0]["status"] = "failed"
        self.mutate(self.scope_api().record_scoped_readiness_review, review)
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review("later", assessor="later-reviewer"))
        self.assertIn("scoped_target_rejected", {o["code"] for o in self.manuscript_readiness()["obligations"]})

    def test_duplicate_propositions_limitations_and_evidence_do_not_change_scientific_target(self):
        from research_harness.development import _Context
        self.prepare_source_limited()
        original = self.record_scope()
        context = _Context(self.store.snapshot()["records"], self.artifacts)
        payload = copy.deepcopy(original["payload"])
        duplicate = copy.deepcopy(payload["supported_claims"][0])
        duplicate["id"] = "duplicate-proposition-alias"
        payload["supported_claims"].append(duplicate)
        limitation = copy.deepcopy(payload["public_limitations"][0])
        limitation["id"] = "duplicate-limitation-alias"
        payload["public_limitations"].append(limitation)
        for claim in payload["supported_claims"]:
            claim["evidence"] += copy.deepcopy(claim["evidence"])
            claim["limitation_ids"].append(limitation["id"])
        self.assertEqual(self.scope_api().build_scientific_target(context, payload), original["scientific_projection"])
        payload["supported_claims"][1]["polarity"] = "negative"
        self.assertNotEqual(self.scope_api().build_scientific_target(context, payload), original["scientific_projection"])

    def corrected_scope(self):
        from research_harness.development import _Context
        payload = self.scope_payload("corrected")
        payload["supported_claims"][0]["statement"] = "The authored enumeration reports a maximum of 9 for four tested integers."
        api = self.scope_api()
        required = api.required_corrections(self.store.snapshot()["records"])
        projection = api.build_scientific_target(_Context(self.store.snapshot()["records"], self.artifacts), payload)
        references = [self.result_evidence(self.execution_payload)]
        predecessors, changes = [], []
        for required_review in required:
            previous = self.store.snapshot()["records"][required_review["kind"]][required_review["review_id"]]
            predecessor = dict(required_review, findings=[{"check": check, "response": "The claim now reports exactly the finite enumeration.",
                "disposition": "addressed", "evidence": references, "deferred_obligation": None} for check in required_review["findings"]])
            predecessors.append(predecessor)
            changes.append({"predecessor_target": required_review["scientific_target_digest"], "field": "claims",
                "before": previous.get("scientific_projection", {}).get("claims"), "after": projection["claims"], "evidence": references})
        payload["correction"] = {"response": self.artifacts.put(b"PRIVATE-CORRECTION-RESPONSE", "text/plain"),
                                 "predecessors": predecessors, "changes": changes}
        return payload

    def test_corrected_target_requires_all_findings_and_fresh_corrective_review(self):
        self.prepare_source_limited()
        original = self.record_scope()
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review(verdict="not_ready"))
        payload = self.corrected_scope()
        incomplete = copy.deepcopy(payload)
        incomplete["correction"]["predecessors"][0]["findings"].pop()
        self.assert_error("publication_scope_correction_mismatch", lambda: self.record_scope(incomplete))
        corrected = self.record_scope(payload)
        self.assertNotEqual(original["scientific_target_digest"], corrected["scientific_target_digest"])
        self.assertFalse(self.manuscript_readiness()["ready"])
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review("corrective-review", assessor="corrective-reviewer"))
        self.assertTrue(self.manuscript_readiness()["ready"], self.manuscript_readiness()["obligations"])
        self.mutate(self.scope_api().select_publication_scope, {"id": original["id"]})
        self.assertFalse(self.manuscript_readiness()["ready"])

    def test_original_full_rejection_needs_transition_response_without_changing_old_verdict(self):
        self.prepare_source_limited()
        self.mutate(self.development().record_readiness_review, self.review(self.execution_payload, verdict="not_ready"))
        old = copy.deepcopy(self.store.snapshot()["records"]["readiness_review"])
        self.assert_error("publication_scope_correction_required", lambda: self.record_scope())
        self.record_scope(self.corrected_scope())
        self.accept_scope()
        self.assertTrue(self.manuscript_readiness()["ready"], self.manuscript_readiness()["obligations"])
        self.assertEqual(old, self.store.snapshot()["records"]["readiness_review"])

    def test_current_corrective_readiness_rechecks_response_and_predecessor_bytes(self):
        self.prepare_source_limited()
        self.record_scope()
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review(verdict="not_ready"))
        payload = self.corrected_scope()
        self.record_scope(payload)
        self.mutate(self.scope_api().record_scoped_readiness_review, self.scope_review("fresh-corrective", assessor="corrective-reviewer"))
        self.assertTrue(self.manuscript_readiness()["ready"])
        previous = self.store.snapshot()["records"]["scoped_readiness_review"]["scope-review"]
        for ref in (payload["correction"]["response"], previous["artifact"]):
            with self.subTest(artifact=ref["sha256"]):
                path = self.root / ref["path"]
                original = path.read_bytes()
                path.chmod(0o600)
                path.write_bytes(b"corrupt correction lineage")
                try:
                    report = self.manuscript_readiness()
                    self.assertFalse(report["ready"])
                    self.assertIn("artifact_corrupt", {item["code"] for item in report["obligations"]})
                finally:
                    path.write_bytes(original)
                    path.chmod(0o444)

    def test_resume_invalidates_current_scope_without_mutating_it_or_historical_replay(self):
        self.prepare_source_limited()
        payload, revision = self.scope_payload(), self.store.revision
        operation = self.scope_api().record_publication_scope
        saved = operation(self.store, payload, expected_revision=revision, request_id="scope-request")
        self.accept_scope()
        self.mutate(self.get_operation("resume-source"), {"id": "resumed", "profile": "research", "version_id": self.gap,
            "deferral_id": self.deferral["id"], "reason": "The comparison is now available."})
        self.assertFalse(self.manuscript_readiness()["ready"])
        replay = operation(self.store, payload, expected_revision=revision, request_id="scope-request")
        self.assertEqual(saved["result"], replay["result"])
        self.assertFalse(self.manuscript_readiness()["ready"])

    def test_corruption_is_still_checked_after_objective_source_debt(self):
        self.prepare_source_limited()
        self.record_scope()
        self.accept_scope()
        artifact = self.execution_payload["outputs"][0]["artifact"]
        (self.root / artifact["path"]).chmod(0o600)
        (self.root / artifact["path"]).write_bytes(b"corrupt")
        report = self.manuscript_readiness()
        self.assertFalse(report["ready"])
        self.assertTrue(any("artifact" in o["code"] for o in report["obligations"]))

    def test_exact_manuscript_text_polarity_assumptions_and_limitations_are_bound(self):
        from research_harness import publication
        self.prepare_source_limited()
        self.record_scope()
        self.accept_scope()
        bundle = self.scoped_manuscript()
        path = self.root / bundle["files"]["claims"]["path"]
        original = json.loads(path.read_text())
        for field, value in (("id", "renamed"), ("claim", "Every integer is bounded by 9."), ("polarity", "negative"),
                             ("assumptions", []), ("limitation_ids", []), ("public_limitations", [])):
            with self.subTest(field=field):
                claims = copy.deepcopy(original)
                claims[0][field] = value
                path.write_text(json.dumps(claims))
                payload = {"id": "changed-" + field, "files": {k: v["path"] if v else None for k, v in bundle["files"].items()},
                           "claim_evidence": copy.deepcopy(bundle["claim_evidence"])}
                if field == "id":
                    payload["claim_evidence"][0]["claim_id"] = value
                self.assert_error("publication_scope_claim_mismatch", lambda: self.mutate(publication.prepare_publication, payload))
        path.write_text(json.dumps(original))
