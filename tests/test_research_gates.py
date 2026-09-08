"""Actual entrypoint gates with evidence created by the domain services."""

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

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

    def test_unsealed_historical_observation_keeps_receipts_but_is_pending_for_readiness(self):
        from integration_fixtures import observed_candidate
        from research_harness import execution
        from research_harness.gates import gate_report
        from research_harness.storage import _canonical
        from research_harness.workspace import strict_json
        self.prepared_study()
        actual = execution.prepared_mutation

        def retain_original_observation(store, operation, payload, prepare, **identity):
            if operation == "execution.observe":
                def original_envelope(records, value):
                    changes, observation = prepare(records, value)
                    terminal = strict_json(self.artifacts.read(observation["terminal"]))
                    terminal.pop("output_seal", None)
                    observation["terminal"] = self.artifacts.put(_canonical(terminal).encode(), "application/json")
                    observation.pop("files", None)
                    return changes, observation
                return actual(store, operation, payload, original_envelope, **identity)
            return actual(store, operation, payload, prepare, **identity)

        with mock.patch.object(execution, "prepared_mutation", side_effect=retain_original_observation):
            payload = observed_candidate(self)
        self.assertTrue(self.development().readiness_report(self.store)["ready"])
        saved = self.store.snapshot()["records"]["execution"][payload["id"]]
        identity = {"expected_revision": saved["recorded_revision"] - 1, "request_id": saved["request_id"]}
        receipt = self.development().record_execution(self.store, payload, **identity)
        before = self.store.snapshot()
        report = gate_report(self.store, "readiness")
        self.assertFalse(report["ready"])
        self.assertIn("execution_output_seal_required", [item["code"] for item in report["obligations"]])
        self.assertEqual(self.development().record_execution(self.store, payload, **identity), receipt)
        self.assertEqual(self.store.snapshot(), before)

    def test_current_readiness_checks_streams_against_the_worker_seal(self):
        from integration_fixtures import observed_candidate
        from research_harness.gates import gate_state
        self.prepared_study()
        payload = observed_candidate(self)
        original = self.store.snapshot()
        self.assertTrue(gate_state(original["records"], self.artifacts, "readiness")["ready"])
        records = copy.deepcopy(original["records"])
        observation = records["execution_observation"][payload["origin"]["admission_id"]]
        observation["log"] = self.artifacts.put(b"Injected output and diagnostics", "text/plain; charset=utf-8")
        report = gate_state(records, self.artifacts, "readiness")
        self.assertFalse(report["ready"])
        self.assertIn("execution_output_mismatch", [item["code"] for item in report["obligations"]])
        self.assertEqual(self.store.snapshot(), original)

    def test_worker_seal_consumer_rejects_an_otherwise_matching_outcome(self):
        from integration_fixtures import observed_candidate
        from research_harness.execution_evidence import _observed
        self.prepared_study()
        payload = observed_candidate(self)
        original = self.store.snapshot()
        accepted = _observed(original["records"], self.artifacts, payload["id"])
        self.assertEqual(accepted["observation"]["execution"], payload)
        records = copy.deepcopy(original["records"])
        execution = records["execution"][payload["id"]]["payload"]
        execution["outputs"][0]["artifact"] = self.artifacts.put(b'{"value":123456}', "application/json")
        observation = records["execution_observation"][payload["origin"]["admission_id"]]
        observation["execution"] = copy.deepcopy(execution)
        self.assertEqual(observation["execution"], execution)
        self.assertEqual(observation["terminal"], accepted["observation"]["terminal"])
        self.assert_error("execution_output_mismatch", lambda: _observed(records, self.artifacts, payload["id"]))
        self.assertEqual(self.store.snapshot(), original)
