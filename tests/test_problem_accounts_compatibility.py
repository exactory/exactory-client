"""Frozen 0.82.0 value bytes keep validation, projection, rendering and the gate."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from research_harness import output_views, problem_claims, problem_delivery, problem_evidence
from research_harness.evidence import digest
from test_research_problem_claims import ProblemCase


class StoredAccountCompatibilityTests(ProblemCase):
    def test_stored_value_keeps_the_0820_bytes_and_delivery_revalidation(self):
        self.assert_stored_value_unchanged()

    def test_stored_value_without_accounts_uses_no_account_validation_or_derivation(self):
        with patch("research_harness.problem_accounts.validate_accounts") as validate, patch(
                "research_harness.problem_account_derivations.describe_account_derivations") as derive:
            self.assert_stored_value_unchanged()
        validate.assert_not_called()
        derive.assert_not_called()

    def assert_stored_value_unchanged(self):
        directory = Path(__file__).parent / "fixtures"
        fixture = json.loads((directory / "problem_result_0820.json").read_text())
        frozen = json.loads((directory / "problem_accounts_0820.json").read_text())
        records = fixture["records"]
        value = records["problem_value"]["value-1"]
        before = copy.deepcopy(records)
        self.assertIsNone(problem_evidence.validate_value(records, self.artifacts, value))
        self.assertNotIn("accounts", value)
        self.assertEqual(digest(value), fixture["value_digest"])
        for key, project in (("binding", problem_evidence.describe_binding), ("evidence", problem_evidence.describe_evidence)):
            actual = json.dumps(project(records, self.artifacts), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            self.assertEqual(actual.encode(), fixture[key + "_bytes"].encode())
        self.assertEqual(problem_claims.compute_problem_evidence_digest(records, self.artifacts), fixture["evidence_digest"])
        self.assertEqual(records, before)
        records["workspace"] = json.loads((directory / "problem_forming_0820.json").read_text())["records"]["workspace"]
        before = copy.deepcopy(records)
        self.assertEqual(problem_delivery.assess_minimum(records, self.artifacts), frozen["minimum"])
        self.assertEqual(problem_delivery.assess_post(records, self.artifacts), frozen["post"])
        views = output_views.compute_view_model(records, self.artifacts, output_views._ViewInputs({}, {}))
        self.assertEqual({path: {"content": view.content.decode(), "entry": view.entry} for path, view in views.items()}, frozen["views"])
        self.assertEqual(records, before)
