"""Probe intent, reservation, ownership and reconciliation contracts."""

from literature_fixtures import LiteratureCase
from research_harness import cli, resources
from research_harness.principles import initialize_research


def build_probe_intent(artifacts, identifier="probe-1", timeout=2):
    return {"id": identifier, "question": "What does the finite example establish?", "interpreter": "python",
            "program": artifacts.put(b"print(4)\n", "text/x-python"), "inputs": [], "timeout_seconds": timeout}


def build_probe_outcome(artifacts, identifier="probe-1", elapsed=0.25, exit_status=0):
    return {"id": identifier, "command": "python example.py", "outputs": [],
            "stdout": artifacts.put(b"4\n", "text/plain"), "stderr": artifacts.put(b"", "text/plain"),
            "exit_status": exit_status, "elapsed_seconds": elapsed, "observation": "The finite result was 4."}


class ProbeLifecycleTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        self.mutate(initialize_research, {"profile": "research", "target": None})

    def record_intent(self, value=None):
        self.assertIn("probe-intent", cli.OPERATIONS)
        return self.mutate(cli.OPERATIONS["probe-intent"], value or build_probe_intent(self.artifacts))

    def record_outcome(self, value=None):
        return self.mutate(cli.OPERATIONS["probe"], value or build_probe_outcome(self.artifacts))

    def find_usage(self):
        return resources.account_report(self.store.snapshot()["records"], "research")["experiment"]["wall_seconds"]

    def test_intent_before_target_reserves_without_charging_and_outcome_releases(self):
        receipt = self.record_intent()
        records = self.store.snapshot()["records"]
        self.assertIsNone(records["configuration"]["research"]["target"])
        self.assertFalse(records.get("cycle"))
        self.assertEqual(records["probe_intent"]["probe-1"], receipt["result"])
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (2, 0))
        self.record_outcome()
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (0, 0.25))

    def test_budget_counts_other_intents_and_refuses_before_launch(self):
        self.mutate(resources.set_budget, {"profile": "research", "purpose": "experiment", "reason": "Bound exploratory time.",
                    "limits": {unit: 3 if unit == "wall_seconds" else None for unit in resources.UNITS}})
        self.record_intent()
        before = self.store.snapshot()
        self.assert_error("resource_budget_exhausted", lambda: self.record_intent(build_probe_intent(self.artifacts, "second")))
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.find_usage()["reserved"], 2)

    def test_duplicate_intent_id_refuses_changed_payload_and_never_reserves_twice(self):
        value = build_probe_intent(self.artifacts)
        original = self.record_intent(value)
        self.assertEqual(self.record_intent(value)["result"], original["result"])
        self.assertEqual(self.find_usage()["reserved"], 2)
        for changed in (dict(value, question="Another question?"), dict(value, timeout_seconds=3)):
            self.assert_error("record_conflict", lambda: self.record_intent(changed))

    def test_outcome_requires_intent_and_replays_without_a_second_charge(self):
        self.assert_error("probe_intent_missing", self.record_outcome)
        self.record_intent()
        value = build_probe_outcome(self.artifacts)
        revision = self.store.revision
        receipt = self.record_outcome(value)
        request_id = "task3-" + str(self.sequence)
        self.assertEqual(cli.OPERATIONS["probe"](self.store, value, expected_revision=revision, request_id=request_id), receipt)
        self.assertEqual(self.find_usage()["charged"], 0.25)
        self.assert_error("record_conflict", lambda: self.record_outcome(dict(value, observation="Changed.")))

    def test_timed_out_failed_output_bound_and_nonzero_outcomes_charge_actual_usage(self):
        for index, status in enumerate(("timed_out", "failed", "output_bound", 7)):
            identifier = "outcome-" + str(index)
            self.record_intent(build_probe_intent(self.artifacts, identifier, timeout=0.1))
            self.record_outcome(build_probe_outcome(self.artifacts, identifier, exit_status=status))
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (0, 1))
        self.assertEqual(cli.status_report(self.store)["probes"], {"count": 4, "elapsed_seconds": 1})

    def test_invalid_outcome_does_not_release_reservation(self):
        self.record_intent()
        value = build_probe_outcome(self.artifacts)
        for key, invalids in (("elapsed_seconds", (-1, True, "1", None)), ("exit_status", (True, 1.5, "unknown", None))):
            for invalid in invalids:
                self.assert_error("invalid_input", lambda: self.record_outcome(dict(value, **{key: invalid})))
        bad = dict(value["stdout"], extra="unsupported")
        self.assert_error("invalid_input", lambda: self.record_outcome(dict(value, stdout=bad)))
        self.assertEqual((self.find_usage()["reserved"], self.find_usage()["charged"]), (2, 0))

    def test_legacy_status_omits_new_probe_keys(self):
        self.assertNotIn("probes", cli.status_report(self.store))

    def test_legacy_acquisition_reservations_do_not_add_account_report_fields(self):
        records = {"acquisition_operation": {"held": {"state": "admitted", "payload": {"max_requests": 3}}}}
        self.assertEqual(resources.held(records, "literature")["network_requests"], 3)
        self.assertEqual(resources.account_report(records, "research"), {})

    def test_interrupted_empty_stream_origins_precede_artifact_publication(self):
        from unittest import mock
        from research_harness.artifacts import ArtifactStore, describe_artifact
        from research_harness.probe import hold_probe_owner, reconcile_probe
        from research_harness.probe_provenance import find_probe_artifact_origins
        self.record_intent()
        intent = self.store.snapshot()["records"]["probe_intent"]["probe-1"]
        original = ArtifactStore.put
        roles = iter(("stdout", "stderr"))
        def check_origin_before_put(artifacts, data, media_type):
            if data == b"":
                role = next(roles)
                origins = find_probe_artifact_origins(self.store.snapshot()["records"], [describe_artifact(data, media_type)])
                self.assertTrue(any(origin["probe_id"] == "probe-1" and role in origin["roles"] for origin in origins))
            return original(artifacts, data, media_type)
        with hold_probe_owner(self.root, "probe-1"), mock.patch.object(ArtifactStore, "put", check_origin_before_put):
            result = reconcile_probe(self.store, intent, None)
        self.assertEqual(result["result"]["exit_status"], "interrupted")


class ProbeConfigurationTests(LiteratureCase):
    def test_intent_requires_configuration(self):
        self.assertIn("probe-intent", cli.OPERATIONS)
        self.assert_error("configuration_missing", lambda: self.mutate(cli.OPERATIONS["probe-intent"], build_probe_intent(self.artifacts)))
