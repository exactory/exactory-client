"""Publication boundaries and blind measurements across development rounds."""

import copy
import json
import unittest

from research_harness import predictions, publication, rounds
from research_harness.gates import gate_report
from research_harness.review_delivery import deliver_manuscript, deliver_round
from research_harness.storage import Store
from rounds_fixtures import RoundsCase
from integration_fixtures import approve_publication_stop, prepare_manuscript
from test_draft import _DepositTestCase, _draft


class DepositRoundGateTests(_DepositTestCase):
    prepare_stop = False

    def test_a_first_deposit_without_an_approved_stop_is_noted_and_deposited_directly(self):
        output = self._deposit(["--production", "--publish", "--confirm-publish",
                                "--creator", "Example, Researcher"])
        self.assertIn("Managed record skipped (readiness_required): ", output)
        self.assertIn("round_decision_missing", output)
        self.assertEqual(self.fake_api.requests[0].full_url, "https://zenodo.org/api/deposit/depositions")
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertNotIn("publication_receipt", records)
        self.assertEqual(records["workspace"]["deposit"]["doi"], "10.5281/zenodo.4242")

    def test_a_legacy_pending_deposit_requires_the_stop_before_resuming(self):
        from research_harness.remote import begin_intent
        from research_harness.zenodo import continue_deposit
        store = self.research.store
        bundle = publication.publication_report(store)["bundle"]
        binding = {"bundle_digest": bundle["digest"], "prepared_revision": bundle["prepared_revision"],
                   "base_url": "https://zenodo.org/api", "environment": "production", "new_version": False,
                   "publish": True, "metadata": {"title": "Fixture", "description": "Fixture"},
                   "uploads": [{"name": "paper.pdf", "artifact": bundle["files"]["pdf"]["artifact"]}], "prior": None}
        intent = begin_intent(store, "deposit", binding, expected_revision=store.revision, request_id="legacy-deposit")
        before = store.snapshot()
        def remote(*args, **kwargs):
            self.fail("A pending deposit reached a remote operation before its stop was approved.")
        self.research.assert_error("readiness_required", lambda: continue_deposit(store, intent["id"], remote))
        self.assertEqual(store.snapshot(), before)

    def test_a_changed_bundle_stops_before_the_remote_preview(self):
        self.assert_preview_refuses_change("PUT", "/deposit/depositions/4242")

    def test_a_change_during_the_preview_read_stops_before_a_pending_write(self):
        self.assert_preview_refuses_change("GET", "/records/4242/draft")

    def assert_preview_refuses_change(self, method, suffix):
        store = self.research.store
        approve_publication_stop(self.research, publication.publication_report(store)["bundle"])
        def remote(request):
            response = self.fake_api(request)
            if request.get_method() == method and request.full_url.endswith(suffix):
                (self.workspace_dir / "draft/paper.pdf").write_bytes(b"%PDF-1.4 changed during deposit")
            return response
        _draft._open_url = remote
        output = self._deposit(["--creator", "Example, Researcher"], expected_exit_code=1)
        self.assertIn("publication_artifact_changed", output)
        self.assertFalse(any(request.get_method() == "PUT" and "/records/4242/draft" in request.full_url
                             for request in self.fake_api.requests))
        self.assertFalse(store.snapshot()["records"].get("publication_receipt"))
        self.assertIsNone(next(iter(store.snapshot()["records"]["remote_intent"].values()))["pending"])

    def test_a_new_version_without_its_own_stop_is_deposited_directly_and_survives_export(self):
        from research_harness.integration import export_workspace
        store = self.research.store
        approve_publication_stop(self.research, publication.publication_report(store)["bundle"])
        self._deposit(["--publish", "--creator", "Example, Researcher"])
        prepare_manuscript(self.research)
        self.assertFalse(gate_report(store, "round")["ready"])
        self.fake_api.requests.clear()
        output = self._deposit(["--new-version", "--creator", "Example, Researcher"])
        self.assertIn("Managed record skipped (readiness_required): ", output)
        self.assertIn("round_decision_missing", output)
        self.assertEqual(self.fake_api.requests[0].full_url,
                         "https://sandbox.zenodo.org/api/deposit/depositions/4242/actions/newversion")
        self.assertEqual(json.loads((self.workspace_dir / ".exactory/deposit.json").read_text())["deposition_id"], 4343)
        current = Store(self.workspace_dir)
        self.assertEqual(len(current.snapshot()["records"]["publication_receipt"]), 1)
        export_workspace(current)
        self.assertEqual(json.loads((self.workspace_dir / ".exactory/deposit.json").read_text())["deposition_id"], 4343)

    def test_a_new_version_with_its_own_stop_keeps_the_prior_record(self):
        store = self.research.store
        approve_publication_stop(self.research, publication.publication_report(store)["bundle"])
        self._deposit(["--publish", "--creator", "Example, Researcher"])
        bundle = prepare_manuscript(self.research)
        approve_publication_stop(self.research, bundle)
        self.fake_api.requests.clear()
        output = self._deposit(["--new-version", "--creator", "Example, Researcher"])
        self.assertNotIn("Managed record skipped", output)
        receipt = json.loads((self.workspace_dir / ".exactory/deposit.json").read_text())
        self.assertEqual(receipt["deposition_id"], 4343)
        self.assertEqual(len(Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]), 2)


class ManuscriptHistoryTests(RoundsCase):
    def test_blind_delivery_keeps_current_claims_and_preserves_the_internal_ledger(self):
        claims = self.claims("withdrawn", revised=("bound",), superseded=("withdrawn",))
        claims[0]["revised"] = {"previous": "EARLIER-CLAIM-SENTINEL", "reason": "REVISION-REASON-SENTINEL"}
        claims[1]["superseded"]["reason"] = "WITHDRAWAL-REASON-SENTINEL"
        bundle = self.pin(claims)
        before = self.store.snapshot()
        original = self.artifacts.read(bundle["files"]["claims"]["artifact"])
        objects = sorted((self.root / "research/sources/objects").iterdir())
        destination = self.root / "blind-history"
        result = deliver_manuscript(self.store, destination)
        manifest = json.loads((destination / "inputs.json").read_text())
        delivered = json.loads((destination / manifest["files"]["claims"]["artifact"]["path"]).read_text())
        self.assertEqual(delivered, [{"id": "bound", "claim": "The maximum is 9."}])
        self.assertEqual([link["claim_id"] for link in manifest["claim_evidence"]], ["bound"])
        for path in destination.rglob("*"):
            if path.is_file():
                contents = path.read_bytes()
                for secret in (b"EARLIER-CLAIM-SENTINEL", b"REVISION-REASON-SENTINEL", b"WITHDRAWAL-REASON-SENTINEL"):
                    self.assertNotIn(secret, contents)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.artifacts.read(bundle["files"]["claims"]["artifact"]), original)
        self.assertEqual(sorted((self.root / "research/sources/objects").iterdir()), objects)
        self.assertEqual(manifest["bundle_digest"], bundle["digest"])
        self.assertEqual((destination / bundle["files"]["pdf"]["artifact"]["path"]).read_bytes(),
                         (self.root / "draft/paper.pdf").read_bytes())
        from research_harness.artifacts import ArtifactStore
        for reference in result["artifacts"]:
            self.assertEqual(ArtifactStore(destination).read(reference), (destination / reference["path"]).read_bytes())

    def test_round_assessor_receives_the_original_claim_history(self):
        bundle = self.pin(self.claims(revised=("bound",)))
        self.mutate(rounds.record_round, self.decision_payload(bundle, decision="stop"))
        destination = self.root / "round-history"
        deliver_round(self.store, destination)
        manifest = json.loads((destination / "inputs.json").read_text())
        reference = manifest["manuscript"]["files"]["claims"]["artifact"]
        self.assertEqual((destination / reference["path"]).read_bytes(), self.artifacts.read(reference))
        self.assertIn("revised", json.loads((destination / reference["path"]).read_text())[0])

    def test_withdrawn_claim_evidence_is_not_in_the_blind_packet_closure(self):
        claims = self.claims("withdrawn", superseded=("withdrawn",))
        source = self.source_evidence()
        result = self.result_evidence(self.execution_payload)
        bundle = self.pin(claims, reviews=0, evidence=[source, result])
        files = {key: None if value is None else value["path"] for key, value in bundle["files"].items()}
        for index, (current, withdrawn) in enumerate(((source, result), (result, source))):
            with self.subTest(current=current["kind"]):
                self.mutate(publication.prepare_publication, {
                    "id": "split-evidence-" + str(index), "files": files,
                    "claim_evidence": [{"claim_id": "bound", "evidence": [current]},
                                       {"claim_id": "withdrawn", "evidence": [withdrawn]}]})
                destination = self.root / ("blind-closure-" + str(index))
                deliver_manuscript(self.store, destination)
                manifest = json.loads((destination / "inputs.json").read_text())
                if current["kind"] == "source":
                    self.assertEqual(manifest["results"], {})
                    self.assertIn(source["link"]["version_id"], manifest["evidence"]["work"])
                    self.assertFalse((destination / result["artifact"]["path"]).exists())
                else:
                    self.assertEqual(manifest["evidence"]["work"], {})
                    self.assertIn(result["execution_id"], manifest["results"])


class MeasurementPopulationTests(RoundsCase):
    def test_measurement_uses_the_three_reviewers_with_predictions(self):
        bundle = self.pin(reviews=0)
        for name, score in (("gate-a", 5), ("gate-b", 5), ("measure-a", 7), ("measure-b", 8), ("measure-c", 9)):
            payload = self.manuscript_review(bundle, name)
            payload["review"] = self.artifacts.put(json.dumps(dict(self.core(), overall=score)).encode(), "application/json")
            self.mutate(publication.record_manuscript_review, payload)
            if name.startswith("measure-"):
                self.mutate(predictions.record_prediction, self.prediction_payload(bundle, name.upper()))
        summary = predictions.measurement_summary(self.store.snapshot()["records"], bundle)
        self.assertEqual(summary["reviews"]["overall"], {"median": 8, "spread": [7, 9]})
        self.assertEqual(summary["reviews"]["count"], 3)
        self.assertIs(summary.get("complete"), True)
        self.assertEqual(len(publication.publication_report(self.store)["reviews"]), 5)

    def test_incomplete_measurement_has_counts_without_a_measurement_value(self):
        bundle = self.pin()
        self.mutate(predictions.record_prediction, self.prediction_payload(bundle, "measurement-a"))
        self.mutate(publication.record_manuscript_review, self.manuscript_review(bundle, "measurement-a"))
        summary = predictions.measurement_summary(self.store.snapshot()["records"], bundle)
        self.assertIs(summary.get("complete"), False)
        self.assertEqual(summary["reviews"]["count"], 1)
        self.assertEqual(summary["predictions"]["count"], 1)
        self.assertEqual(summary["reviews"]["overall"], {"median": None, "spread": None})
        self.assertEqual(summary["predictions"]["percentile"], {"median": None, "spread": None})

    def test_extra_or_duplicate_predictions_do_not_define_a_three_reviewer_measurement(self):
        bundle = self.pin()
        self.measure(bundle, "complete")
        records = self.store.snapshot()["records"]
        self.assertTrue(predictions.measurement_summary(records, bundle)["complete"])
        saved = next(iter(records["manuscript_prediction"].values()))
        for assessor in ("fourth-predictor", saved["payload"]["assessor"]["id"].upper()):
            with self.subTest(assessor=assessor):
                legacy = copy.deepcopy(records)
                extra = copy.deepcopy(saved)
                extra["id"] = "extra-prediction"
                extra["payload"]["assessor"]["id"] = assessor
                legacy["manuscript_prediction"][extra["id"]] = extra
                summary = predictions.measurement_summary(legacy, bundle)
                self.assertFalse(summary["complete"])
                self.assertEqual(summary["reviews"]["count"], 3)
                self.assertEqual(summary["predictions"]["count"], 4)
                self.assertIsNone(summary["reviews"]["overall"]["median"])
                self.assertIsNone(summary["predictions"]["percentile"]["median"])


if __name__ == "__main__":
    unittest.main()
