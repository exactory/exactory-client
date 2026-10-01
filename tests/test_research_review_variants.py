"""Requested assessment variants remain evidence without canonical authority."""

import json

from strategy_fixtures import StrategyCase
from research_harness import review_protocol


class RequestedVariantTests(StrategyCase):
    def test_requested_variant_keeps_its_instruction_prompt_and_output_without_canonical_approval(self):
        self.route()
        self.mutate(review_protocol.record_assignment, {
            "id": "variant-evidence", "route_id": "route", "dossier_id": None,
            "role": "standalone", "reviewer_id": "variant-reviewer", "author_id": "author",
            "context": {"artifact_refs": [self.evidence]}})
        variant = {
            "protocol": "user-requested-accessibility-assessment-v1",
            "instruction": "Assess the explanation for an undergraduate reader.",
            "prompt": "Identify prerequisite definitions and unclear exposition.",
            "output": {"missing_definitions": ["The admissible input domain."]},
            "canonical_scientific_assessment": False}
        artifact = self.artifacts.put(json.dumps(variant).encode(), "application/json")
        self.mutate(review_protocol.record_attempt, {
            "id": "requested-variant", "assignment_id": "variant-evidence", "status": "completed",
            "output": artifact, "reason": "Preserve the separately requested accessibility protocol and its complete response.",
            "usage": {"input_tokens": None, "output_tokens": None, "cost_usd": None, "wall_seconds": None}})
        records = self.store.snapshot()["records"]
        state = review_protocol.assignment_state(records, self.artifacts, "variant-evidence")
        self.assertEqual(state["output"], variant)
        self.assertFalse(state["ready"])
        self.assertIn("review_invocation_unverified", {item["code"] for item in state["obligations"]})
        self.assertEqual(records["review_attempt"]["requested-variant"]["origin"], "imported")
