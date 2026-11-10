"""The fixture was captured from 0.82.0 (645a791), before lane A3 changed the code."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from research_harness import output_views, problem_candidates, problem_evidence
from research_harness.cli import status_report
from research_harness.evidence import digest
from research_harness.record_check import check_integrity
from test_research_problem_claims import ProblemCase


class ProblemStartCompatibilityTests(ProblemCase):
    def setUp(self):
        super().setUp()
        path = Path(__file__).parent / "fixtures/problem_start_0_82_0.json"
        self.fixture = json.loads(path.read_text(encoding="utf-8"))

    def test_legacy_initialization_keeps_the_0_82_0_marker_and_digest(self):
        expected = self.fixture["initial_records"]["workspace"]["problem"]
        with patch("research_harness.problem_claims.datetime") as clock:
            clock.now.return_value = datetime.fromisoformat(expected["created"])
            marker = self.initialize_problem()
        self.assertEqual(marker, expected)
        self.assertEqual(digest(marker), self.fixture["expected"]["problem_digest"])
        self.assertNotIn("start", marker)

    def test_stored_records_keep_validation_digests_projections_status_and_rendered_bytes(self):
        for archive in self.fixture["archives"]:
            reference = archive["reference"]
            self.assertEqual(self.artifacts.put(archive["text"].encode("utf-8"), reference["media_type"]), reference)

        def restore_records(tx):
            for kind, rows in self.fixture["initial_records"].items():
                for key, value in rows.items():
                    tx.put(kind, key, value)

        self.store.mutate("test.restore", {}, restore_records, expected_revision=0, request_id="restore-0.82.0")
        self.assertEqual(self.store.snapshot()["records"], self.fixture["initial_records"])
        for candidate in self.fixture["candidates"]:
            payload = {key: value for key, value in candidate.items() if key != "recorded_revision"}
            saved = self.mutate(problem_candidates.record_candidate, payload)["result"]
            self.assertEqual(saved, candidate)
            self.assertEqual(digest(saved), self.fixture["expected"]["candidate_digests"][saved["id"]])
        records = self.store.snapshot()["records"]
        marker = self.load_problem_module().require_problem(records)
        self.assertEqual(marker, self.fixture["initial_records"]["workspace"]["problem"])
        self.assertIsNone(marker.get("start"))
        expected = self.fixture["expected"]
        self.assertEqual(digest(marker), expected["problem_digest"])
        self.assertEqual(problem_candidates.compute_candidate_set_digest(records), expected["candidate_set_digest"])
        self.assertEqual(problem_candidates.describe_candidates(records), expected["candidate_projection"])
        self.assertEqual(problem_evidence.describe_binding(records, self.artifacts), expected["binding"])
        self.assertEqual(problem_evidence.describe_evidence(records, self.artifacts), expected["evidence"])
        report = status_report(self.store)
        report.pop("runtime")  # Build identity changes when code changes; record interpretation must not.
        self.assertEqual(report, expected["status"])
        self.assertEqual(check_integrity(self.store), expected["integrity"])
        model = output_views.compute_view_model(records, self.artifacts, output_views._ViewInputs({}, {}))
        self.assertEqual({path: {"content": view.content.decode("utf-8"), "entry": view.entry}
                          for path, view in model.items()}, expected["views"])
