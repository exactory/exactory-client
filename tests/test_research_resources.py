"""Resource budgets and accounts: reservation, reconciliation, refusal and obligations."""

import json

from literature_fixtures import FIELDS, LiteratureCase
from research_harness.errors import ResearchError
from research_fixtures import atom, client, entry, xml_response


def item(version_id):
    return {"version_id": version_id, "note": "Read the complete abstract.",
            "notes": {f: {"text": "The abstract discusses " + f + ".", "status": "present"} for f in FIELDS}}


def limits(**values):
    base = {"network_requests": None, "source_bytes": None, "readings": None, "screenings": None,
            "model_input_tokens": None, "model_output_tokens": None, "wall_seconds": None}
    base.update(values)
    return base


class ResourceTests(LiteratureCase):
    def setUp(self):
        super().setUp()
        from research_harness.principles import initialize_research
        self.mutate(initialize_research, {"profile": "research", "target": None})

    def budget(self, **values):
        from research_harness.resources import set_budget
        return self.mutate(set_budget, {"profile": "research", "purpose": "literature", "limits": limits(**values),
                                        "reason": "Approved by the user."})

    def account(self):
        return self.store.snapshot()["records"].get("resource_account", {}).get("research:literature")

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

    def test_acquisition_reserves_then_reconciles_and_refuses_over_the_limit(self):
        from research_harness.acquisition import acquire_work
        self.budget(network_requests=3)
        http, wire, _ = client([xml_response(atom([entry()], total=1))])
        acquire_work(self.store, "arxiv:2601.00001v1", request_id="acq-1", expected_revision=self.store.revision, http=http, max_requests=2)
        account = self.account()
        self.assertEqual(account["reserved"]["network_requests"], 0)
        self.assertEqual(account["charged"]["network_requests"], 1)
        self.assertGreater(account["charged"]["source_bytes"], 0)
        http2, wire2, _ = client([xml_response(atom([entry("2601.00002v1")], total=1))])
        self.assert_error("resource_budget_exhausted", lambda: acquire_work(self.store, "arxiv:2601.00002v1", request_id="acq-2",
                                                                            expected_revision=self.store.revision, http=http2, max_requests=3))
        self.assertEqual(wire2.requests, [])
        acquire_work(self.store, "arxiv:2601.00002v1", request_id="acq-3", expected_revision=self.store.revision, http=http2, max_requests=2)
        self.assertEqual(self.account()["charged"]["network_requests"], 2)

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
