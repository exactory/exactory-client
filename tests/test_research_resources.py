"""Resource budgets and accounts: reservation, reconciliation, refusal and obligations."""

import json
from unittest import mock

from literature_fixtures import FIELDS, LiteratureCase
from research_harness.errors import ResearchError
from research_fixtures import atom, client, entry, xml_response


def item(version_id):
    return {"version_id": version_id, "note": "Read the complete abstract.",
            "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS}}


def limits(**values):
    base = {"network_requests": None, "source_bytes": None, "readings": None, "screenings": None,
            "model_input_tokens": None, "model_output_tokens": None, "wall_seconds": None, "rounds": None}
    base.update(values)
    return base


def build_usage(wall_seconds=None, input_tokens=None):
    return {"model": "fixture", "input_tokens": input_tokens, "output_tokens": None, "wall_seconds": wall_seconds}


class ResourceTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "research", "target": None, "preparation_policy": "exhaustive-v1"})

    def budget(self, **values):
        from research_harness.resources import set_budget
        return self.mutate(set_budget, {"profile": "research", "purpose": "literature", "limits": limits(**values),
                                        "reason": "Approved by the user."})

    def account(self):
        return self.store.snapshot()["records"].get("resource_account", {}).get("research:literature")

    def report(self):
        from research_harness.resources import account_report
        return account_report(self.store.snapshot()["records"], "research")["literature"]

    def test_budget_records_raises_and_never_drops_below_charged(self):
        from research_harness.reading import record_reading_batch
        from research_harness.resources import set_budget
        self.budget(readings=2)
        a = self.metadata()
        self.mutate(record_reading_batch, {"id": "b1", "depth": "abstract", "items": [item(a)]})
        self.assertEqual(self.account()["charged"]["readings"], 1)
        self.budget(readings=5)
        self.assert_error("resource_budget_below_charged", lambda: self.budget(readings=0))
        bad = {"profile": "research", "purpose": "shopping", "limits": limits(), "reason": "x"}
        self.assert_error("invalid_input", lambda: self.mutate(set_budget, bad))
        bad = {"profile": "research", "purpose": "literature", "limits": {"readings": 1}, "reason": "x"}
        self.assert_error("invalid_input", lambda: self.mutate(set_budget, bad))

    def test_batches_charge_counts_and_unknown_usage_and_refuse_over_the_limit(self):
        from research_harness.reading import record_reading_batch
        self.budget(readings=1)
        a, b = self.metadata(1), self.metadata(2)
        self.mutate(record_reading_batch, {"id": "b1", "depth": "abstract", "items": [item(a)],
                                           "usage": {"model": "fixture", "input_tokens": 120, "output_tokens": None, "wall_seconds": 1.5}})
        account = self.account()
        self.assertEqual((account["charged"]["readings"], account["charged"]["model_input_tokens"], account["charged"]["wall_seconds"]), (1, 120, 1.5))
        self.assertEqual(account["unknown"]["model_output_tokens"], 1)
        before = self.store.snapshot()
        self.assert_error("resource_budget_exhausted", lambda: self.mutate(record_reading_batch, {"id": "b2", "depth": "abstract", "items": [item(b)]}))
        self.assertEqual(self.store.snapshot(), before)
        self.assertIn("resource_budget_exhausted", self.codes()) if self.store.snapshot()["records"].get("literature_scope") else None

    def test_wall_seconds_beyond_the_float_range_are_refused_before_the_charge(self):
        # An integer beyond the float range (about 1.8e308) has no float value, so adding it to a float account total
        # raised OverflowError, and read-batch printed a traceback instead of a JSON error.
        from research_harness.reading import record_reading_batch
        a, b = self.metadata(1), self.metadata(2)
        self.mutate(record_reading_batch, {"id": "b1", "depth": "abstract", "items": [item(a)], "usage": build_usage(1.5)})
        before = self.store.snapshot()
        self.assert_error("invalid_batch", lambda: self.mutate(
            record_reading_batch, {"id": "b2", "depth": "abstract", "items": [item(b)], "usage": build_usage(10 ** 400)}))
        self.assertEqual(self.store.snapshot(), before)

    def test_integer_charges_sum_beyond_the_float_range_as_in_0_49_0(self):
        # Python adds two integers exactly at any size, so token counts and integer wall seconds keep their totals
        # beyond the float range (about 1.8e308), as 0.49.0 kept them.
        from research_harness.reading import record_reading_batch
        charges = (build_usage(input_tokens=10 ** 400), build_usage(10 ** 308, input_tokens=5), build_usage(10 ** 308),
                   build_usage(5))
        for number, usage in enumerate(charges, 1):
            self.mutate(record_reading_batch, {"id": "b" + str(number), "depth": "abstract",
                                               "items": [item(self.metadata(number))], "usage": usage})
        charged = self.account()["charged"]
        self.assertEqual((charged["model_input_tokens"], charged["wall_seconds"]), (10 ** 400 + 5, 2 * 10 ** 308 + 5))

    def test_a_float_charge_to_an_integer_total_beyond_the_float_range_is_refused(self):
        # Integer charges within the float range can sum beyond it. Python cannot add a float to such an integer: it
        # raised OverflowError, and read-batch printed a traceback instead of a JSON error.
        from research_harness.reading import record_reading_batch
        for number in (1, 2):
            self.mutate(record_reading_batch, {"id": "b" + str(number), "depth": "abstract",
                                               "items": [item(self.metadata(number))], "usage": build_usage(10 ** 308)})
        float_charge = {"id": "b3", "depth": "abstract", "items": [item(self.metadata(3))], "usage": build_usage(1.5)}
        before = self.store.snapshot()
        self.assert_error("invalid_input", lambda: self.mutate(record_reading_batch, float_charge))
        self.assertEqual(self.store.snapshot(), before)
        self.mutate(record_reading_batch, dict(float_charge, usage=build_usage(1)))
        self.assertEqual(self.account()["charged"]["wall_seconds"], 2 * 10 ** 308 + 1)

    def test_float_charges_that_sum_beyond_the_float_range_are_refused(self):
        # Two floats sum to infinity beyond the float range, and the store writes only finite numbers.
        from research_harness.reading import record_reading_batch
        self.mutate(record_reading_batch, {"id": "b1", "depth": "abstract", "items": [item(self.metadata(1))],
                                           "usage": build_usage(1e308)})
        second = {"id": "b2", "depth": "abstract", "items": [item(self.metadata(2))], "usage": build_usage(1e308)}
        before = self.store.snapshot()
        self.assert_error("invalid_input", lambda: self.mutate(record_reading_batch, second))
        self.assertEqual(self.store.snapshot(), before)

    def test_a_charge_to_a_total_that_an_earlier_release_stored_beyond_the_float_range_is_refused(self):
        # 0.49.0 and earlier stored an integer charge of any size, so an account can hold a total beyond the float
        # range. A float charge raised OverflowError there, in the budget check when the unit has a limit.
        from research_harness.reading import record_reading_batch
        a, b = self.metadata(1), self.metadata(2)
        with mock.patch("research_harness.reading.is_finite_number", return_value=True, create=True), \
                mock.patch("research_harness.resources.is_finite_number", return_value=True, create=True):
            self.mutate(record_reading_batch, {"id": "b1", "depth": "abstract", "items": [item(a)], "usage": build_usage(10 ** 400)})
        float_charge = {"id": "b2", "depth": "abstract", "items": [item(b)], "usage": build_usage(1.5)}
        before = self.store.snapshot()
        self.assert_error("invalid_input", lambda: self.mutate(record_reading_batch, float_charge))
        self.assertEqual(self.store.snapshot(), before)
        self.budget(wall_seconds=10 ** 500)
        before = self.store.snapshot()
        self.assert_error("invalid_input", lambda: self.mutate(record_reading_batch, float_charge))
        self.assertEqual(self.store.snapshot(), before)

    def test_acquisition_reserves_then_reconciles_and_refuses_over_the_limit(self):
        from research_harness.acquisition import acquire_work
        self.budget(network_requests=3)
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        acquire_work(self.store, "arxiv:2601.00001v1", request_id="acq-1", expected_revision=self.store.revision, http=http, max_requests=2)
        account = self.account()
        self.assertEqual(self.report()["network_requests"]["reserved"], 0)
        self.assertEqual(account["charged"]["network_requests"], 1)
        self.assertGreater(account["charged"]["source_bytes"], 0)
        http2, wire2, _ = client([xml_response(atom([entry("2601.00002v1")], total=1))])
        self.assert_error("resource_budget_exhausted", lambda: acquire_work(self.store, "arxiv:2601.00002v1", request_id="acq-2",
                                                                            expected_revision=self.store.revision, http=http2, max_requests=3))
        self.assertEqual(wire2.requests, [])
        acquire_work(self.store, "arxiv:2601.00002v1", request_id="acq-3", expected_revision=self.store.revision, http=http2, max_requests=2)
        self.assertEqual(self.account()["charged"]["network_requests"], 2)

    def test_an_interrupted_acquisition_holds_its_reservation_until_superseded(self):
        from research_harness.acquisition import acquire_work
        from research_harness.errors import ResearchError
        from research_harness.resources import obligations
        self.budget(network_requests=3)

        class Crash(Exception):
            pass

        class Interrupted:
            def get(self, *args, **kwargs):
                raise Crash()
        with self.assertRaises(Crash):
            acquire_work(self.store, "arxiv:2601.00001v1", request_id="acq-crash", expected_revision=self.store.revision,
                         http=Interrupted(), max_requests=3)
        self.assertEqual(self.report()["network_requests"]["reserved"], 3)
        self.assertEqual([o["code"] for o in obligations(self.store.snapshot()["records"], "research")], ["resource_budget_exhausted"])
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        with self.assertRaises(ResearchError) as raised:
            acquire_work(self.store, "arxiv:2601.00002v1", request_id="acq-other", expected_revision=self.store.revision, http=http, max_requests=1)
        self.assertEqual(raised.exception.code, "resource_budget_exhausted")
        acquire_work(self.store, "arxiv:2601.00001v1", request_id="acq-again", expected_revision=self.store.revision, http=http, max_requests=2)
        records = self.store.snapshot()["records"]
        self.assertEqual(records["acquisition_operation"]["acq-crash"]["state"], "superseded")
        self.assertEqual(self.report()["network_requests"], {"limit": 3, "charged": 1, "reserved": 0, "unknown": 0})

    def test_an_acquisition_without_an_allowance_needs_room_for_one_request(self):
        from research_harness.acquisition import acquire_work
        self.budget(network_requests=1)
        http, wire, _ = client([xml_response(atom([entry()], total=1)), xml_response(atom([entry("2601.00002v1")], total=1))])
        acquire_work(self.store, "arxiv:2601.00001v1", request_id="acq-1", expected_revision=self.store.revision, http=http)
        self.assertEqual(self.account()["charged"]["network_requests"], 1)
        self.assert_error("resource_budget_exhausted", lambda: acquire_work(self.store, "arxiv:2601.00002v1", request_id="acq-2",
                                                                            expected_revision=self.store.revision, http=http))
        self.assertEqual(len(wire.requests), 1)

    def test_exhaustion_is_an_obligation_shown_in_status(self):
        from research_harness.cli import status_report
        from research_harness.reading import record_reading_batch
        self.budget(readings=1)
        a = self.metadata()
        self.scope([a])
        self.mutate(record_reading_batch, {"id": "b1", "depth": "abstract", "items": [item(a)]})
        self.assertIn("resource_budget_exhausted", self.codes())
        report = status_report(self.store)
        self.assertEqual(report["resources"]["literature"]["readings"], {"limit": 1, "charged": 1, "reserved": 0, "unknown": 0})
        self.assertIn("resource_budget_exhausted", {o["code"] for o in report["obligations"]})

    def test_development_rounds_are_a_budgeted_unit(self):
        from research_harness.resources import PURPOSES, UNITS, charge, obligations, set_budget
        self.assertIn("rounds", UNITS)
        self.assertIn("development", PURPOSES)
        self.mutate(set_budget, {"profile": "research", "purpose": "development", "limits": limits(rounds=1),
                                 "reason": "The user wants at most one development round."})
        records = self.store.snapshot()["records"]
        kind, key, account = charge(records, "development", {"rounds": 1})
        self.assertEqual((kind, key, account["charged"]["rounds"]), ("resource_account", "research:development", 1))
        charged = dict(records, resource_account={key: account})
        self.assert_error("resource_budget_exhausted", lambda: charge(charged, "development", {"rounds": 1}))
        self.assertEqual([o["code"] for o in obligations(charged, "research")], ["resource_budget_exhausted"])

    def test_a_budget_payload_names_the_rounds_unit(self):
        from research_harness.resources import set_budget
        incomplete = limits()
        del incomplete["rounds"]
        self.assert_error("invalid_input", lambda: self.mutate(set_budget, {"profile": "research", "purpose": "literature",
                                                                             "limits": incomplete, "reason": "Incomplete."}))

    def test_an_account_stored_before_the_rounds_unit_keeps_its_credit(self):
        from research_harness.cli import status_report
        from research_harness.operations import prepared_mutation
        from research_harness.resources import account_report, charge, obligations, set_budget
        legacy = {"key": "research:literature", "charged": {u: 0 for u in limits() if u != "rounds"},
                  "unknown": {u: 0 for u in limits() if u != "rounds"}}
        legacy["charged"]["readings"] = 1
        records = dict(self.store.snapshot()["records"], resource_account={"research:literature": legacy})
        self.assertEqual(account_report(records, "research")["literature"]["rounds"],
                         {"limit": None, "charged": 0, "reserved": 0, "unknown": 0})
        self.assertEqual(account_report(records, "research")["literature"]["readings"]["charged"], 1)
        self.assertEqual(obligations(records, "research"), [])
        self.assertEqual(charge(records, "literature", {"readings": 1})[2]["charged"], dict(legacy["charged"], readings=2, rounds=0))
        prepared_mutation(self.store, "test.legacy-account", {}, lambda stored, value: ([("resource_account", "research:literature", legacy)], None),
                          expected_revision=self.store.revision, request_id="legacy-account")
        self.assertEqual(status_report(self.store)["resources"]["literature"]["readings"]["charged"], 1)
        self.budget(rounds=2)
        self.assertEqual(self.account(), legacy)
