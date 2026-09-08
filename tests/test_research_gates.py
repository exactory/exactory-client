"""Actual entrypoint gates with evidence created by the domain services."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

from development_fixtures import DevelopmentCase


PLUGIN = Path(__file__).resolve().parents[1]


class ResearchGateTests(DevelopmentCase):
    def test_experiment_stage_requires_an_admission_for_current_preparation(self):
        from integration_fixtures import admit_lab
        from research_harness.gates import gate_report
        self.prepared_study()
        admit_lab(self, body="print('{\"metric\": 7}')\n")
        self.assertTrue(gate_report(self.store, "execution")["ready"])
        self.refresh_synthesis("new-preparation")
        self.assertTrue(gate_report(self.store, "preparation")["ready"])
        report = gate_report(self.store, "execution")
        self.assertFalse(report["ready"])
        self.assertIn("plan_dependencies_stale", [item["code"] for item in report["obligations"]])

    def test_bare_draft_marker_is_not_publication_readiness(self):
        metadata = self.root / ".exactory"
        (metadata / "draft.json").write_text(json.dumps({"version": 1, "title": "Fixture"}))
        (self.root / "paper.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        (self.root / "abstract.txt").write_text("An authored bounded fixture.")
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-draft"), "deposit",
            "--pdf", "paper.pdf", "--abstract-file", "abstract.txt", "--creator", "Example, Author"],
            cwd=self.root, capture_output=True, text=True, env=dict(os.environ, ZENODO_SANDBOX_TOKEN=""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("readiness", result.stderr)
        self.assertNotIn("ZENODO_SANDBOX_TOKEN is not set", result.stderr)

    def test_gate_recomputes_whole_candidate_after_source_change(self):
        from integration_fixtures import observed_candidate
        self.prepared_study()
        observed_candidate(self)
        self.assertTrue((PLUGIN / "research_harness/gates.py").is_file(), "Shared gate module is missing")
        gates = importlib.import_module("research_harness.gates")
        self.assertTrue(gates.gate_report(self.store, "readiness")["ready"])
        source = self.root / self.links[0]["artifact"]["path"]
        source.chmod(0o600)
        source.write_bytes(b"Changed original extraction")
        before = self.store.snapshot()
        self.assertFalse(gates.gate_report(self.store, "readiness")["ready"])
        self.assertEqual(self.store.snapshot(), before)

    def test_modeled_managed_history_keeps_its_receipt_but_cannot_authorize_publication(self):
        from research_harness.gates import gate_report
        from research_harness.publication import publication_report, prepare_publication
        self.prepared_candidate()
        execution = self.store.snapshot()["records"]["execution"]["execution-run-1"]["payload"]
        payload = self.review(execution)
        revision = self.store.revision
        receipt = self.mutate(self.development().record_readiness_review, payload)
        self.assertTrue(self.development().readiness_report(self.store)["ready"])
        before = self.store.snapshot()
        report = gate_report(self.store, "readiness")
        self.assertFalse(report["ready"])
        self.assertIn("execution_observation_required", [item["code"] for item in report["obligations"]])
        self.assertFalse(publication_report(self.store)["ready"])
        self.assert_error("readiness_required", lambda: prepare_publication(self.store,
            {"id": "modeled-paper", "files": {"pdf": "paper.pdf", "abstract": "abstract.txt", "bibliography": "references.bib",
                "claims": "claims.json", "sources": None}, "claim_evidence": []},
            expected_revision=self.store.revision, request_id="modeled-publication"))
        self.assertEqual(self.development().record_readiness_review(self.store, payload,
            expected_revision=revision, request_id=receipt["request_id"]), receipt)
        self.assertEqual(self.store.snapshot(), before)
