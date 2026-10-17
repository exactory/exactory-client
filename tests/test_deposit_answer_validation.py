"""A Zenodo answer cannot change the deposition of a recorded operation."""

import io
import contextlib
import json
import unittest
from email.message import Message
from unittest.mock import patch
import urllib.request
import urllib.response

from research_harness import zenodo
from research_harness.artifacts import ArtifactStore
from research_harness.remote import begin_intent, finish_intent, resolve_step
from research_harness.storage import Store
from test_draft import (_FakeZenodoApi, _LostResponseZenodoApi, _PlainWorkspaceDepositTestCase,
                        _PublishedRecordZenodoApi, _draft, _read_request, _run_draft_command)
from test_draft_problem_attachment import (_PROBLEM_DEPOSIT, _install_exactory, _read_journal,
                                           _OPEN_URL_OF_THE_COMMAND, _build_intent_binding, _build_managed_journal)


def _published(record_id=4242, **changes):
    return dict({"id": record_id, "submitted": True, "doi": "10.5281/zenodo." + str(record_id),
                 "conceptdoi": "10.5281/zenodo.4241",
                 "links": {"record_html": "https://zenodo.org/records/" + str(record_id)}}, **changes)


class _OverrideAnswer(_FakeZenodoApi):
    def __init__(self, method, suffix, answer):
        super().__init__()
        self.method, self.suffix, self.answer = method, suffix, answer

    def __call__(self, request):
        if request.get_method() == self.method and request.full_url.endswith(self.suffix):
            self.requests.append(request)
            return self.answer
        return super().__call__(request)


class TestDirectDepositionAnswers(_PlainWorkspaceDepositTestCase):
    def setUp(self):
        super().setUp()
        self.exactory = _install_exactory(self)

    def install_answer(self, method, suffix, answer):
        self.fake_api = _OverrideAnswer(method, suffix, answer)
        _draft._open_url = self.fake_api

    def assert_unresolved(self):
        journal = _read_journal(self.workspace_dir)
        self.assertEqual(journal["deposition_id"], 4242)
        self.assertFalse(journal["settled"])
        self.assertFalse(journal["attached"])
        self.assertEqual(self.exactory.links(), [])
        self.assertNotIn("doi", self.read_deposit_state())
        records = Store(self.workspace_dir).snapshot()["records"].get("direct_deposit", {})
        self.assertTrue(all(not record.get("doi") for record in records.values()))

    def assert_original_retry(self, message):
        self.assertIn("4343", message)
        self.assertIn("4242", message)
        self.assertIn("reads deposition 4242 back", message)

    def test_a_mismatched_publish_answer_keeps_the_original_operation_unresolved(self):
        self.install_answer("POST", "/actions/publish", _published(4343))
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assert_original_retry(output)
        self.assert_unresolved()

    def test_a_publish_answer_without_publication_evidence_stays_unresolved(self):
        self.install_answer("POST", "/actions/publish", _published(submitted=False))
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assert_unresolved()

    def test_a_publish_answer_without_a_doi_stays_unresolved(self):
        self.install_answer("POST", "/actions/publish", _published(doi=""))
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assert_unresolved()

    def test_an_empty_publish_answer_stays_unresolved(self):
        self.install_answer("POST", "/actions/publish", {})
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assert_unresolved()

    def test_a_non_string_doi_does_not_make_a_plain_publish_final(self):
        self.install_answer("POST", "/actions/publish", _published(doi=42))
        self._deposit(["--production", "--publish", "--confirm-publish", "--creator", "Example, Ada"],
                      expected_exit_code=1)
        self.assertNotIn("doi", self.read_deposit_state())

    def test_a_mismatched_plain_publish_keeps_its_pending_record_for_new_version_read_back(self):
        self.install_answer("POST", "/actions/publish", _published(4343))
        output = self._deposit(["--production", "--publish", "--confirm-publish", "--creator", "Example, Ada"],
                               expected_exit_code=1)
        self.assertIn("--new-version. It reads deposition 4242 back", output)
        self.assertNotIn("doi", self.read_deposit_state())
        self.assertFalse((self.workspace_dir / ".exactory" / "problem-deposit.json").exists())

    def test_a_matching_published_answer_records_and_attaches_the_original_deposition(self):
        self.install_answer("POST", "/actions/publish", _published())
        self._deposit(_PROBLEM_DEPOSIT)
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

    def lose_publish(self):
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

    def test_a_mismatched_unpublished_read_never_publishes_either_deposition(self):
        self.lose_publish()
        self.install_answer("GET", "/deposit/depositions/4242", _published(4343, submitted=False, doi=""))
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assert_original_retry(output)
        self.assertEqual([r.get_method() for r in self.fake_api.requests], ["GET"])
        self.assert_unresolved()

    def test_a_mismatched_recovery_publish_answer_stays_unresolved(self):
        self.lose_publish()
        self.install_answer("POST", "/actions/publish", _published(4343))
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assert_original_retry(output)
        self.assert_unresolved()

    def redirect_read(self, answer, expected_exit_code):
        requested = []
        def https_open(handler, request):
            requested.append(request.full_url)
            headers = Message()
            if len(requested) == 1:
                headers["Location"] = "/api/deposit/depositions/4343"
                status, body = 302, b""
            else:
                status, body = 200, json.dumps(answer).encode()
            response = urllib.response.addinfourl(io.BytesIO(body), headers, request.full_url, status)
            response.msg = "Found" if status == 302 else "OK"
            return response
        with patch.object(_draft, "_open_url", _OPEN_URL_OF_THE_COMMAND), \
                patch.object(urllib.request, "getproxies", return_value={}), \
                patch.object(urllib.request.HTTPSHandler, "https_open", https_open):
            output = _run_draft_command(["attach-problem"], expected_exit_code, self)
        self.assertEqual(requested, ["https://zenodo.org/api/deposit/depositions/4242",
                                     "https://zenodo.org/api/deposit/depositions/4343"])
        return output

    def test_a_real_urllib_redirect_to_another_deposition_cannot_settle_or_attach(self):
        self.lose_publish()
        output = self.redirect_read(_published(4343), 1)
        self.assert_original_retry(output)
        self.assert_unresolved()

    def test_a_redirected_matching_answer_settles_and_attaches(self):
        self.lose_publish()
        self.redirect_read(_published(), None)
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4242)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

    def test_a_read_with_a_doi_but_no_submitted_evidence_cannot_settle(self):
        self.lose_publish()
        answer = _published()
        del answer["submitted"]
        self.install_answer("GET", "/deposit/depositions/4242", answer)
        _run_draft_command(["attach-problem"], 1, self)
        self.assert_unresolved()


class TestVersionDepositionAnswers(_PlainWorkspaceDepositTestCase):
    def setUp(self):
        super().setUp()
        self.prior = {"environment": "sandbox", "deposition_id": 4242,
                      "draft_url": "https://sandbox.zenodo.org/deposit/4242", "concept_doi": "10.5072/zenodo.4000"}
        self.path = self.workspace_dir / ".exactory" / "deposit.json"
        self.path.write_text(json.dumps(self.prior))

    def test_a_version_read_names_both_ids_and_keeps_the_original_record(self):
        _draft._open_url = _OverrideAnswer("GET", "/deposit/depositions/4242", _published(4343))
        output = self._deposit(["--new-version", "--creator", "Example, Ada"], expected_exit_code=1)
        self.assertIn("4343", output)
        self.assertIn("reads deposition 4242 back", output)
        self.assertEqual(self.read_deposit_state(), self.prior)

    def test_a_version_read_with_a_doi_but_not_submitted_keeps_the_original_record(self):
        _draft._open_url = _OverrideAnswer("GET", "/deposit/depositions/4242", _published(submitted=False))
        self._deposit(["--new-version", "--creator", "Example, Ada"], expected_exit_code=1)
        self.assertEqual(self.read_deposit_state(), self.prior)

    def test_a_matching_published_version_read_records_the_original_deposition(self):
        _draft._open_url = _OverrideAnswer("GET", "/deposit/depositions/4242", _published())
        self._deposit(["--new-version", "--creator", "Example, Ada"], expected_exit_code=0)
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4242)
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")

    def test_a_new_version_draft_read_cannot_change_the_id_of_its_link(self):
        self.prior["doi"] = "10.5072/zenodo.4242"
        self.path.write_text(json.dumps(self.prior))
        _draft._open_url = _OverrideAnswer("GET", "/deposit/depositions/4343",
                                          {"id": 4444, "submitted": False, "links": {"bucket": "https://sandbox.zenodo.org/api/files/bucket-2"}})
        output = self._deposit(["--new-version", "--creator", "Example, Ada"], expected_exit_code=1)
        self.assertIn("4343", output)
        self.assertIn("4444", output)
        self.assertEqual(self.read_deposit_state(), self.prior)

    def test_the_managed_client_rejects_a_draft_answer_that_changes_the_id_of_its_link(self):
        _draft._open_url = _OverrideAnswer("GET", "/deposit/depositions/4343", {"id": 4444, "submitted": False})
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit):
            _draft._build_zenodo_client("sandbox", "test-token")(
                "GET", "https://sandbox.zenodo.org/api/deposit/depositions/4343")
        self.assertIn("4343", sink.getvalue())
        self.assertIn("4444", sink.getvalue())

    def test_the_managed_client_checks_a_draft_link_with_a_trailing_slash(self):
        _draft._open_url = _OverrideAnswer("GET", "/deposit/depositions/4343/", {"id": 4444, "submitted": False})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _draft._build_zenodo_client("sandbox", "test-token")(
                "GET", "https://sandbox.zenodo.org/api/deposit/depositions/4343/")


class TestManagedPublishAnswers(_PlainWorkspaceDepositTestCase):
    def setUp(self):
        super().setUp()
        self.exactory = _install_exactory(self)
        # Readiness belongs to the publication-gate tests. Keep actual artifacts, remote
        # claims, receipts, reconciliation, workspace exports and command transport here.
        current = patch.object(zenodo, "_current", side_effect=lambda store, binding: store.revision)
        current.start()
        self.addCleanup(current.stop)
        self.store = Store(self.workspace_dir)
        self.uploaded = b"%PDF-1.4 reviewed bytes"
        artifact = ArtifactStore(self.workspace_dir).put(self.uploaded, "application/pdf")
        binding = _build_intent_binding(uploads=[{"name": "paper.pdf", "artifact": artifact}])
        begin_intent(self.store, "deposit", binding, expected_revision=self.store.revision, request_id="checked-publish")
        _draft._write_problem_journal(_build_managed_journal("checked-publish"), "The test journal could not be written.")

    def run_intent(self, expected_exit_code):
        return _run_draft_command(["reconcile", "checked-publish"], expected_exit_code, self)

    def read_intent(self):
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertNotIn("publication_receipt", records)
        intent, = records["remote_intent"].values()
        return intent

    def assert_read_back_recovers(self, intent, uploaded):
        self.fake_api = _PublishedRecordZenodoApi(uploaded)
        _draft._open_url = self.fake_api
        _run_draft_command(["reconcile", intent["id"]], None, self)
        self.assertEqual([(r.get_method(), r.full_url) for r in self.fake_api.requests],
                         [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertEqual(records["publication_receipt"][intent["id"]]["doi"], "10.5281/zenodo.4242")
        self.assertEqual(set(records["workspace"]["deposit"]),
                         {"environment", "deposition_id", "draft_url", "doi", "concept_doi", "record_url"})
        _run_draft_command(["attach-problem"], None, self)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])
        return records

    def invalid_answer_stays_pending(self, answer):
        self.fake_api = _OverrideAnswer("POST", "/actions/publish", answer)
        _draft._open_url = self.fake_api
        self.run_intent(1)
        intent = self.read_intent()
        self.assertIsNotNone(intent["pending"])
        self.assertEqual(intent["pending"]["name"], "publish")
        self.assertNotIn("publish", intent["responses"])
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.exactory.links(), [])
        self.assertEqual(sum(r.full_url.endswith("/actions/publish") for r in self.fake_api.requests), 1)
        uploaded = _read_request(self.fake_api.requests, "PUT", "/files/bucket-1/paper.pdf").data
        self.assert_read_back_recovers(intent, uploaded)

    def test_a_malformed_success_keeps_the_claim_and_recovers_with_one_publish_request(self):
        self.invalid_answer_stays_pending({})

    def test_a_mismatched_success_keeps_the_claim_and_reads_the_original_deposition(self):
        self.invalid_answer_stays_pending(_published(4343))

    def test_a_doi_without_submitted_true_keeps_the_claim_and_reads_back(self):
        self.invalid_answer_stays_pending(_published(submitted=False))

    def test_a_non_string_doi_keeps_the_claim_and_reads_back(self):
        self.invalid_answer_stays_pending(_published(doi=42))

    def save_old_answer(self, answer):
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        self.run_intent(1)
        uploaded = _read_request(self.fake_api.requests, "PUT", "/files/bucket-1/paper.pdf").data
        store = Store(self.workspace_dir)
        intent = self.read_intent()
        receipt_id = intent["id"] + ":resolve:" + str(store.revision)
        resolve_step(store, intent["id"], "publish", answer, {"kind": "direct_response"})
        return self.read_intent(), uploaded, receipt_id, store.committed_request(receipt_id)

    def test_an_old_saved_invalid_answer_recovers_without_changing_any_prior_receipt(self):
        intent, uploaded, receipt_id, receipt = self.save_old_answer({})
        records = self.assert_read_back_recovers(intent, uploaded)
        self.assertEqual(records["remote_intent"][intent["id"]]["responses"], intent["responses"])
        self.assertEqual(Store(self.workspace_dir).committed_request(receipt_id), receipt)
        self.assertEqual(records["publication_receipt"][intent["id"]]["published_response"]["id"], 4242)

    def test_an_old_saved_invalid_answer_never_republishes_an_unpublished_read_back(self):
        intent, _, receipt_id, receipt = self.save_old_answer({})
        self.fake_api = _FakeZenodoApi()
        _draft._open_url = self.fake_api
        _run_draft_command(["reconcile", intent["id"]], 1, self)
        self.assertEqual([r.get_method() for r in self.fake_api.requests], ["GET"])
        self.assertEqual(Store(self.workspace_dir).committed_request(receipt_id), receipt)
        self.assertEqual(self.read_intent()["responses"], intent["responses"])

    def complete_from_a_null_answer(self):
        """The state that an earlier version left: Zenodo answered the publish request with JSON
        null, and that version completed the intent with a receipt without a DOI."""
        intent, uploaded, _, _ = self.save_old_answer(None)
        state = {"environment": "production", "deposition_id": 4242,
                 "draft_url": "https://zenodo.org/api/deposit/4242"}
        receipt = dict(state, intent_id=intent["id"], published_response=None,
                       uploads=intent["binding"]["uploads"], bundle_digest=intent["binding"]["bundle_digest"],
                       prepared_revision=intent["binding"]["prepared_revision"])
        finish_intent(self.store, intent["id"], "publication_receipt", receipt, workspace=state)
        return intent, uploaded, receipt

    def test_a_receipt_completed_from_a_null_answer_is_read_back_recorded_and_attached(self):
        intent, uploaded, receipt = self.complete_from_a_null_answer()
        self.fake_api = _PublishedRecordZenodoApi(uploaded)
        _draft._open_url = self.fake_api
        _run_draft_command(["attach-problem"], None, self)
        self.assertEqual([(r.get_method(), r.full_url) for r in self.fake_api.requests],
                         [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertEqual(records["publication_receipt"][intent["id"]], receipt)
        self.assertEqual(records["workspace"]["deposit"],
                         {"environment": "production", "deposition_id": 4242,
                          "draft_url": "https://zenodo.org/api/deposit/4242", "doi": "10.5281/zenodo.4242",
                          "concept_doi": "10.5281/zenodo.4241",
                          "record_url": "https://sandbox.zenodo.org/api/records/4242"})
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, True, "10.5281/zenodo.4242"))
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_receipt_completed_from_a_null_answer_stays_unresolved_while_zenodo_shows_no_publication(self):
        intent, _, receipt = self.complete_from_a_null_answer()
        self.fake_api = _FakeZenodoApi()
        _draft._open_url = self.fake_api
        output = _run_draft_command(["attach-problem"], 1, self)
        self.assertIn("Zenodo has not confirmed that deposition 4242 is published", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual([r.get_method() for r in self.fake_api.requests], ["GET"])
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertEqual(records["publication_receipt"][intent["id"]], receipt)
        self.assertNotIn("doi", records["workspace"]["deposit"])
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.exactory.links(), [])
