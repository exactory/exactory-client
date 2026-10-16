"""Exploratory probes retain pinned artifacts and actual costs without result credit."""

from development_fixtures import DevelopmentCase
from literature_fixtures import LiteratureCase
from research_harness import cli, resources
from research_harness.principles import initialize_research
from test_research_probe_lifecycle import build_probe_intent, build_probe_outcome


class ProbeTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.mutate(initialize_research, {"profile": "research", "target": None})

    def record_intent(self, value=None):
        return self.mutate(cli.OPERATIONS["probe-intent"], value or build_probe_intent(self.artifacts))

    def record_outcome(self, value=None):
        return self.mutate(cli.OPERATIONS["probe"], value or build_probe_outcome(self.artifacts))

    def test_record_before_any_target_or_cycle_and_charge_once(self):
        self.record_intent()
        payload = build_probe_outcome(self.artifacts)
        revision = self.store.revision
        receipt = self.record_outcome(payload)
        request_id = "task3-" + str(self.sequence)
        records = self.store.snapshot()["records"]
        self.assertIsNone(records["configuration"]["research"]["target"])
        self.assertFalse(records.get("cycle"))
        self.assertEqual(records["probe"][payload["id"]], receipt["result"])
        self.assertEqual({key: receipt["result"][key] for key in payload}, payload)
        self.assertEqual(cli.OPERATIONS["probe"](self.store, payload, expected_revision=revision, request_id=request_id), receipt)
        self.assertEqual(resources.account_report(records, "research")["experiment"]["wall_seconds"]["charged"], 0.25)
        self.record_intent(build_probe_intent(self.artifacts, "probe-2"))
        self.record_outcome(build_probe_outcome(self.artifacts, "probe-2", elapsed=0.5))
        self.assertEqual(cli.status_report(self.store)["probes"], {"count": 2, "elapsed_seconds": 0.75})

    def test_invalid_intent_text_descriptors_and_timeout_are_atomic(self):
        original = build_probe_intent(self.artifacts)
        for key in ("id", "question", "interpreter"):
            for invalid in ("", None, 3):
                self.assert_error("invalid_input", lambda: self.record_intent(dict(original, **{key: invalid})))
        for invalid in (0, -1, True, "1", None):
            self.assert_error("invalid_input", lambda: self.record_intent(dict(original, timeout_seconds=invalid)))
        for key in ("program", "inputs"):
            bad = dict(original["program"], sha256="0" * 64, path="research/sources/objects/" + "0" * 64)
            self.assert_error("artifact_missing", lambda: self.record_intent(dict(original, **{key: [bad] if key == "inputs" else bad})))
            bad = dict(original["program"], extra="unsupported")
            self.assert_error("invalid_input", lambda: self.record_intent(dict(original, **{key: [bad] if key == "inputs" else bad})))
        self.assertFalse(self.store.snapshot()["records"].get("probe_intent"))
        self.assertFalse(resources.account_report(self.store.snapshot()["records"], "research"))

    def test_invalid_outcome_artifacts_text_status_and_usage_keep_the_reservation(self):
        self.record_intent()
        original = build_probe_outcome(self.artifacts)
        for key in ("id", "command", "observation"):
            for invalid in ("", None, 3):
                self.assert_error("invalid_input", lambda: self.record_outcome(dict(original, **{key: invalid})))
        for key in ("elapsed_seconds", "exit_status"):
            for invalid in ((-1, True, "1", None) if key == "elapsed_seconds" else (True, 1.5, "unknown", None)):
                self.assert_error("invalid_input", lambda: self.record_outcome(dict(original, **{key: invalid})))
        for key in ("stdout", "stderr", "outputs"):
            bad = dict(original["stdout"], sha256="0" * 64, path="research/sources/objects/" + "0" * 64)
            self.assert_error("artifact_missing", lambda: self.record_outcome(dict(original, **{key: [bad] if key == "outputs" else bad})))
            bad = dict(original["stdout"], extra="unsupported")
            self.assert_error("invalid_input", lambda: self.record_outcome(dict(original, **{key: [bad] if key == "outputs" else bad})))
        self.assertFalse(self.store.snapshot()["records"].get("probe"))
        usage = resources.account_report(self.store.snapshot()["records"], "research")["experiment"]["wall_seconds"]
        self.assertEqual((usage["charged"], usage["reserved"]), (0, 2))

    def test_legacy_status_and_immutable_outcome_ids(self):
        self.assertNotIn("probes", cli.status_report(self.store))
        self.record_intent()
        self.record_outcome()
        before = self.store.snapshot()
        self.assert_error("record_conflict", lambda: self.record_outcome(dict(build_probe_outcome(self.artifacts), observation="Changed.")))
        self.assertEqual(self.store.snapshot(), before)

    def test_budget_refuses_intent_without_recording_or_charging(self):
        self.mutate(resources.set_budget, {"profile": "research", "purpose": "experiment", "reason": "Bound exploratory time.",
                    "limits": {unit: 0.1 if unit == "wall_seconds" else None for unit in resources.UNITS}})
        self.assert_error("resource_budget_exhausted", self.record_intent)
        self.assertFalse(self.store.snapshot()["records"].get("probe_intent"))
        self.assertFalse(self.store.snapshot()["records"].get("probe"))


class ProbeCandidateTests(DevelopmentCase):
    def test_probes_without_admitted_cycles_never_form_a_result_candidate(self):
        self.prepared_study()
        self.mutate(cli.OPERATIONS["probe-intent"], build_probe_intent(self.artifacts))
        self.mutate(cli.OPERATIONS["probe"], build_probe_outcome(self.artifacts))
        report = self.development().readiness_report(self.store)
        self.assertIsNone(report["candidate"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["counts"]["cycles"], 0)
        self.assertFalse(self.store.snapshot()["records"].get("execution_admission"))
