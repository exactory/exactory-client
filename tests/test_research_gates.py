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
    def build_managed_records(self):
        from managed_strategy_fixtures import ManagedStrategy
        self.prepared_study()
        ManagedStrategy(self).ensure_intent()
        return self.store.snapshot()["records"]

    def test_managed_initiate_can_enter_literature_and_cohort_can_be_left_without_a_gate(self):
        from research_harness.gates import validate_transition
        records = self.build_managed_records()
        records["reading"] = {}
        for source in ("initiate", "cohort"):
            with self.subTest(source=source):
                validate_transition(records, self.artifacts, {"stage": source},
                                    {"stage": "literature", "status": "in_progress"})

    def test_direction_requires_configuration_and_the_cycle_decision(self):
        from managed_strategy_fixtures import bind_plan
        from research_harness.claim_minimums import assess_direction
        from research_harness.gates import gate_state, validate_transition
        from research_harness.research_decisions import decision_state
        records = self.build_managed_records()
        report = gate_state(records, self.artifacts, "direction")
        self.assertFalse(report["ready"])
        self.assertEqual(report["obligations"], decision_state(records, self.artifacts, "cycle")["obligations"]
                         + assess_direction(records, self.artifacts))
        self.assert_error("readiness_required", lambda: validate_transition(records, self.artifacts,
            {"stage": "literature"}, {"stage": "ideate", "status": "in_progress"}))
        bind_plan(self, self.plan())
        records = self.store.snapshot()["records"]
        # The standards section is part of the direction minimum; the other sections are not.
        records["synthesis_selection"] = {key: value for key, value in records["synthesis_selection"].items()
                                          if key == "research:standards"}
        self.assertTrue(gate_state(records, self.artifacts, "direction")["ready"])
        validate_transition(records, self.artifacts, {"stage": "literature"},
                            {"stage": "ideate", "status": "in_progress"})
        records["configuration"]["research"]["target"] = None
        self.assertIn("objective_missing", {o["code"] for o in gate_state(records, self.artifacts, "direction")["obligations"]})

    def test_managed_execution_gate_does_not_add_preparation_obligations(self):
        from managed_strategy_fixtures import bind_plan
        from research_harness.gates import gate_state
        self.build_managed_records()
        self.mutate(self.development().plan_cycle, bind_plan(self, self.plan()))
        self.admit()
        records = self.store.snapshot()["records"]
        records["synthesis_selection"] = {key: value for key, value in records["synthesis_selection"].items()
                                          if key == "research:standards"}
        report = gate_state(records, self.artifacts, "execution")
        self.assertTrue(report["ready"], report["obligations"])

    def test_managed_next_follows_the_current_milestone_with_advisory_preparation(self):
        from research_harness.cli import status_report
        from research_harness.gates import gate_state
        from research_harness.report_views import current_obligations, next_summary, status_summary
        self.build_managed_records()
        for stage in ("initiate", "cohort", "literature", "ideate"):
            self.store.mutate("fixture.stage", {"stage": stage},
                lambda tx: tx.put("workspace", "study", {"stage": stage, "status": "in_progress"}),
                expected_revision=self.store.revision, request_id="stage-" + stage)
            report = status_report(self.store)
            action = "execution" if stage == "ideate" else "direction"
            expected = gate_state(self.store.snapshot()["records"], self.artifacts, action)["obligations"]
            self.assertEqual(report["obligations"], expected)
            self.assertTrue(report["preparation"]["advisory"])
            self.assertIn(report["next"], expected)
            self.assertNotEqual(report["next"]["code"], "candidate_checkpoint_missing")
            self.assertEqual(current_obligations(report), report["obligations"])
            self.assertLess(len(json.dumps(next_summary(report)).encode()), 2048)
            self.assertLess(len(json.dumps(status_summary(report)).encode()), 16384)

    def test_managed_next_names_direction_evidence_before_a_decision_exists(self):
        from research_harness.claim_minimums import assess_direction
        from research_harness.gates import gate_state
        from research_harness.report_views import order_obligations
        records = self.build_managed_records()
        records["search_selection"] = {key: value for key, value in records["search_selection"].items()
                                       if key != "research:direct"}
        self.assertEqual([item["code"] for item in assess_direction(records, self.artifacts)], ["direction_search_missing"])
        codes = [item["code"] for item in order_obligations(gate_state(records, self.artifacts, "direction")["obligations"])]
        self.assertEqual(codes[0], "direction_search_missing")
        self.assertIn("strategy_dossier_missing", codes)

    def test_direction_is_the_unchanged_preparation_report_for_other_stores(self):
        from research_harness.gates import gate_state
        self.prepared_study()
        records = self.store.snapshot()["records"]
        self.assertEqual(gate_state(records, self.artifacts, "direction"),
                         gate_state(records, self.artifacts, "preparation"))

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

    def test_bare_draft_marker_is_no_longer_refused_before_the_token_check(self):
        metadata = self.root / ".exactory"
        (metadata / "draft.json").write_text(json.dumps({"version": 1, "title": "Fixture"}))
        (self.root / "paper.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        (self.root / "abstract.txt").write_text("An authored bounded fixture.")
        result = subprocess.run([sys.executable, str(PLUGIN / "bin/exactory-draft"), "deposit",
            "--pdf", "paper.pdf", "--abstract-file", "abstract.txt", "--creator", "Example, Author"],
            cwd=self.root, capture_output=True, text=True, env=dict(os.environ, ZENODO_SANDBOX_TOKEN=""))
        self.assertEqual(result.returncode, 2)
        self.assertIn("ZENODO_SANDBOX_TOKEN is not set", result.stderr)
        self.assertNotIn("readiness", result.stderr)

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
        from research_harness.execution_evidence import validate_observed_execution
        self.prepared_study()
        payload = observed_candidate(self)
        original = self.store.snapshot()
        accepted = validate_observed_execution(original["records"], self.artifacts, payload["id"])
        self.assertEqual(accepted["observation"]["execution"], payload)
        records = copy.deepcopy(original["records"])
        execution = records["execution"][payload["id"]]["payload"]
        execution["outputs"][0]["artifact"] = self.artifacts.put(b'{"value":123456}', "application/json")
        observation = records["execution_observation"][payload["origin"]["admission_id"]]
        observation["execution"] = copy.deepcopy(execution)
        self.assertEqual(observation["execution"], execution)
        self.assertEqual(observation["terminal"], accepted["observation"]["terminal"])
        self.assert_error("execution_output_mismatch", lambda: validate_observed_execution(records, self.artifacts, payload["id"]))
        self.assertEqual(self.store.snapshot(), original)
