"""Current approval consumes the actual dossier and manuscript, not claimed IDs."""

import copy
from unittest.mock import patch

from research_harness import publication, research_decisions, review_protocol, strategy
from strategy_fixtures import StrategyCase


class CurrentApprovalBindingTests(StrategyCase):
    def test_material_successor_dossier_invalidates_default_and_explicit_old_decisions(self):
        self.reviewed_dossier()
        self.mutate(research_decisions.record_research_decision, self.decision())
        original = self.store.snapshot()["records"]["research_decision"]["decision-1"]
        newer = self.dossier("dossier-2")
        evidence = self.artifacts.put(b"A new comparator changes the target inference.", "text/plain")
        newer.update(previous="dossier-1", material_change={"kind": "new_comparison",
            "reason": "The new comparator requires assessment before further commitment.",
            "evidence": [evidence], "prior_objections": []})
        self.mutate(strategy.record_strategy, newer)
        records = self.store.snapshot()["records"]
        for boundary in ("cycle", "target", "round"):
            for identifier in (None, "decision-1"):
                with self.subTest(boundary=boundary, decision_id=identifier):
                    state = research_decisions.decision_state(records, self.artifacts, boundary, decision_id=identifier)
                    self.assertFalse(state["ready"])
                    self.assertIn("research_dossier_stale", {item["code"] for item in state["obligations"]})
        self.assertEqual(records["research_decision"]["decision-1"], original)

    def reconsideration(self, identifier, previous, reason):
        newer = self.dossier(identifier)
        newer.update(previous=previous, material_change={"kind": "reconsideration", "reason": reason,
            "evidence": [self.evidence], "prior_objections": []},
            reconsideration={"decision_id": "decision-1", "reason": "The finite witness excludes a universal claim.",
                "alternatives": ["candidate-a", "candidate-b"], "remaining_obligations": ["The mechanism remains open."]})
        newer["candidates"][0]["addition"] = "The finite witness excludes the stronger universal bound."
        return newer

    def test_reconsideration_does_not_repeat_unchanged_grounds_under_new_labels(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        self.mutate(research_decisions.record_research_decision, self.decision())
        first = self.reconsideration("reconsidered-1", "dossier-1", "Assess a previously unrecognized consequence.")
        self.mutate(strategy.record_strategy, first)
        before = self.store.snapshot()
        repeated = self.reconsideration("reconsidered-2", "reconsidered-1", "Please assess the same consequence again.")
        self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, repeated))
        self.assertEqual(self.store.snapshot(), before)

    def test_distinct_reconsideration_can_reuse_the_same_observed_results(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        self.mutate(research_decisions.record_research_decision, self.decision())
        first = self.reconsideration("reconsidered-1", "dossier-1", "Assess the first unrecognized consequence.")
        self.mutate(strategy.record_strategy, first)
        second = self.reconsideration("reconsidered-2", "reconsidered-1", "Assess a distinct logical inference.")
        second["candidates"][0]["addition"] = "The same witness also rules out the asserted uniqueness of the response."
        self.mutate(strategy.record_strategy, second)
        self.assertEqual(len(self.store.snapshot()["records"]["strategy_dossier"]), 3)

    def test_transfer_evidence_relabeling_cannot_reopen_an_unchanged_consequence(self):
        self.reviewed_dossier(response_b=self.review_response(status="insufficient"))
        self.mutate(research_decisions.record_research_decision, self.decision())
        before = self.store.snapshot()
        for serial, media_type in enumerate(("text/markdown", "text/x-rst")):
            repeated = self.reconsideration("descriptor-" + str(serial), "dossier-1", "The transfer descriptor changed.")
            repeated["candidates"] = copy.deepcopy(self.dossier()["candidates"])
            repeated["candidates"][0]["transfer"]["evidence"][0]["media_type"] = media_type
            with self.subTest(media_type=media_type):
                self.error("strategy_material_change_unsubstantiated", lambda: self.mutate(strategy.record_strategy, repeated))
                self.assertEqual(self.store.snapshot(), before)

    def paper(self):
        self.route()
        self.put("collection", "cohort", {"definition": {"corpus": "arxiv", "primaryCategory": "cs.LG",
            "windowStart": "2026-01-01", "windowEnd": "2026-01-31"}})
        self.put("literature_scope", "research", {"collection_ids": ["cohort"]})
        paper = self.artifacts.put(b"The exact supported manuscript.", "text/plain")
        bundle = {"id": "paper", "digest": "pinned-paper-digest", "files": {"pdf": {"artifact": paper}},
            "claim_evidence": [], "execution_observations": {},
            "review_inputs": {"synthesis": {"sections": {}, "foundation": {}}}}
        self.put("publication_bundle", "paper", bundle)
        return bundle

    def assign_paper(self, bundle):
        return self.mutate(review_protocol.record_assignment, {"id": "paper-assignment", "route_id": "route",
            "dossier_id": None, "role": "manuscript", "reviewer_id": "paper-reviewer", "author_id": "author",
            "context": {"bundle": bundle}})

    def test_claimed_bundle_digest_cannot_substitute_different_reviewer_input_bytes(self):
        bundle = self.paper()
        forged = copy.deepcopy(bundle)
        forged["files"]["pdf"]["artifact"] = self.artifacts.put(b"An unrelated easier manuscript.", "text/plain")
        before = self.store.snapshot()
        self.error("review_bundle_mismatch", lambda: self.assign_paper(forged))
        self.assertEqual(self.store.snapshot(), before)

    def test_current_consumption_rechecks_actual_bundle_content_against_observed_assignment(self):
        bundle = self.paper()
        self.assign_paper(bundle)
        core = {"decision": "accept"}
        with patch("research_harness.review_transport.send_request", return_value=self.provider_response({"review": core})):
            self.mutate(review_protocol.invoke_assignment, {"id": "paper-attempt", "assignment_id": "paper-assignment"}, credential="fixture")
        review = {"assignment_id": "paper-assignment", "assessor": {"id": "paper-reviewer"}}
        self.assertTrue(publication.require_manuscript_assignment(self.store.snapshot()["records"], self.artifacts,
            review, bundle, core=core)["ready"])
        changed = copy.deepcopy(bundle)
        changed["files"]["pdf"]["artifact"] = self.artifacts.put(b"A different manuscript with the same claimed digest.", "text/plain")
        self.error("publication_review_stale", lambda: publication.require_manuscript_assignment(
            self.store.snapshot()["records"], self.artifacts, review, changed, core=core))
