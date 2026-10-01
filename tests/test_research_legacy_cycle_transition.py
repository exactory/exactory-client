"""Managed adoption preserves admitted work while closing fresh commitments."""

import copy
import json

from development_fixtures import DevelopmentCase
from integration_fixtures import OBSERVED_PROGRAM, admit_lab
from research_harness import development, execution, integration
from research_harness.errors import ResearchError
from research_harness.gates import gate_report


class LegacyCycleTransitionTests(DevelopmentCase):
    def mark_managed(self):
        self.mutate(integration.initialize_workspace, {
            "kind": "study", "state": {"name": "legacy-admitted-study", "stage": "experiment",
                                        "status": "pending", "waiting": None}})
        self.assertFalse(self.store.snapshot()["records"].get("research_intent"))

    def assert_missing_intent_blocks(self, function, payload):
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.mutate(function, payload)
        self.assertEqual(caught.exception.code, "research_decision_required")
        self.assertIn("research_intent_missing", {
            item["code"] for item in caught.exception.details["obligations"]})
        self.assertEqual(self.store.snapshot(), before)

    def test_admitted_execution_and_assessment_finish_without_inventing_a_new_intent(self):
        self.prepared_study()
        admission = admit_lab(self, body=OBSERVED_PROGRAM, outputs=[
            {"id": "result", "requirement_id": "measurements", "path": "results/result.json", "media_type": "application/json"},
            {"id": "validation", "requirement_id": "checks", "path": "results/validation.json", "media_type": "application/json"}])
        before = self.store.snapshot()["records"]
        plan = before["cycle_plan"][admission["cycle_id"]]
        account = copy.deepcopy(before["strategy_account"][plan["strategy_key"]])
        self.assertNotIn("research_decision", admission)
        self.assertNotIn("research_decision", plan)
        self.mark_managed()

        identity = {"expected_revision": self.store.revision, "request_id": "finish-legacy-admission"}
        result = execution.launch_execution(self.store, admission["id"], **identity)
        self.assertTrue(result["ok"])
        self.assertEqual(result["metric"], {"metric": 9})
        records = self.store.snapshot()["records"]
        outcome = records["execution_outcome"][admission["id"]]
        observed = records["execution"][outcome["execution_id"]]["payload"]
        raw = json.loads(self.artifacts.read(observed["outputs"][0]["artifact"]))
        self.assertEqual(raw["result"], {"values": [0, 1, 4, 9], "bound": 9})
        self.assertEqual(records["execution_admission"][admission["id"]], admission)
        self.assertEqual(records["cycle_plan"][plan["id"]], plan)
        self.assertEqual(records["strategy_account"][plan["strategy_key"]], account)

        saved = self.store.snapshot()
        self.assertEqual(execution.launch_execution(self.store, admission["id"], **identity), result)
        self.assertEqual(self.store.snapshot(), saved)
        fresh = {key: admission[key] for key in ("id", "cycle_id", "plan_digest", "command", "reserved_units")}
        fresh["id"] = "fresh-run-after-legacy-completion"
        self.assert_missing_intent_blocks(development.admit_execution, fresh)

        assessment = self.assessment(plan["payload"], observed)
        report = self.mutate(development.assess_cycle, assessment)["result"]
        self.assertTrue(report["validated_result"])
        self.assertTrue(report["complete"])
        checkpoint = self.save_checkpoint()
        self.assertEqual(checkpoint["assessment_id"], assessment["id"])
        final = self.store.snapshot()["records"]
        self.assertEqual(final["cycle"][plan["id"]]["status"], "complete")
        self.assertEqual(len(final["execution"]), 1)
        self.assertEqual(final["execution_admission"][admission["id"]], admission)
        self.assertFalse(final.get("research_intent"))
        self.assertFalse(final.get("research_decision"))
        readiness = gate_report(self.store, "readiness")
        self.assertFalse(readiness["ready"])
        self.assertIn("research_intent_missing", {item["code"] for item in readiness["obligations"]})

    def test_legacy_plan_cannot_authorize_new_admission_or_cycle_after_managed_adoption(self):
        self.prepared_study()
        planned = self.mutate(development.plan_cycle, self.plan())["result"]
        self.mark_managed()
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            self.admit(identifier="fresh-admission")
        self.assertEqual(caught.exception.code, "research_decision_required")
        self.assertIn("research_intent_missing", {
            item["code"] for item in caught.exception.details["obligations"]})
        self.assertEqual(self.store.snapshot(), before)
        self.assert_missing_intent_blocks(development.plan_cycle, self.plan("fresh-cycle"))
        records = self.store.snapshot()["records"]
        self.assertEqual(records["cycle_plan"][planned["id"]], planned)
        self.assertEqual(set(records["cycle"]), {planned["id"]})
        self.assertFalse(records.get("execution_admission"))
        account = records["strategy_account"][planned["strategy_key"]]
        self.assertEqual((account["executions"], account["charged_units"]), (0, 0))
