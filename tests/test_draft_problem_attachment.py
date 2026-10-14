"""Tests for deposits for a math problem: exactory-draft deposit --math-problem, its
recovery journal, and attach-problem (design 2026-10-12 §17)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

from research_harness.integration import export_workspace, record_direct_deposit
from research_harness.storage import Store
from test_draft import (
    _DepositTestCase,
    _FakeZenodoApi,
    _LostResponseZenodoApi,
    _PlainWorkspaceDepositTestCase,
    _PublishedRecordZenodoApi,
    _UnpublishedRecordZenodoApi,
    _draft,
    _read_request,
    _refuse_the_direct_deposit_record,
    _run_draft_command,
)

_PROBLEM_ID = "656c336e-e892-4c47-80d2-a71d022f4116"
_PROBLEM = {"problem_id": _PROBLEM_ID, "api_url": "https://api.test"}
_PRODUCTION_PUBLISH = ["--production", "--publish", "--confirm-publish", "--creator", "Shiroshita, Ryosuke"]
_PROBLEM_DEPOSIT = [*_PRODUCTION_PUBLISH, "--math-problem", _PROBLEM_ID]
# The journal of an operation this run did not start: the state a writer outside the run
# leaves behind, which no write of this run replaces.
_OTHER_JOURNAL = {"operation_id": "0" * 32, "path": "direct", "environment": "production",
                  "base_url": "https://zenodo.org/api", "deposition_id": 4000,
                  "draft_url": "https://zenodo.org/api/deposit/4000", "math_problem": _PROBLEM, "settled": False}


class _ExactoryRecorder:
    """Stands in for the exactory command: records each call with its origin and answers
    the way the API would."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.orcid_id: str | None = "0000-0002-1825-0097"
        self.failing_links = 0

    def __call__(self, argv: list[str], api_url: str) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), api_url))
        if argv[0] == "me":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"displayName": "Ada", "orcidId": self.orcid_id}), "")
        if argv[0] == "problem":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"id": argv[1], "removed": False}), "")
        if argv[0] == "link-problem-paper":
            if self.failing_links > 0:
                self.failing_links -= 1
                return subprocess.CompletedProcess(argv, 1, "", "The server rejected the API key.")
            return subprocess.CompletedProcess(argv, 0, json.dumps({"papers": []}), "")
        raise AssertionError(f"unexpected exactory call: {argv}")

    def links(self) -> list[tuple[list[str], str]]:
        return [(argv, url) for argv, url in self.calls if argv[0] == "link-problem-paper"]


class _StateAtPublishZenodoApi(_FakeZenodoApi):
    """Answers like the fake, and keeps the named workspace files as they stood when the
    publish request went out."""

    def __init__(self, paths) -> None:
        super().__init__()
        self.paths = paths
        self.files_at_publish: dict = {}

    def __call__(self, request):
        if request.full_url.endswith("/actions/publish"):
            self.files_at_publish = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in self.paths}
        return super().__call__(request)


class _InterleavingZenodoApi(_PublishedRecordZenodoApi):
    """Answers like the published-record fake, and runs `interleave` once, when the run sends
    the named request: what a second command or another writer does at that moment. The
    overlap is fixed by the request, so the test is deterministic."""

    def __init__(self, uploaded: bytes, method: str, url_suffix: str, interleave) -> None:
        super().__init__(uploaded)
        self.method, self.url_suffix, self.interleave = method, url_suffix, interleave
        self.interleaved = None

    def __call__(self, request):
        if self.interleave is not None and request.get_method() == self.method \
                and request.full_url.endswith(self.url_suffix):
            interleave, self.interleave = self.interleave, None
            self.interleaved = interleave()
        return super().__call__(request)


def _install_exactory(test_case: unittest.TestCase) -> _ExactoryRecorder:
    recorder = _ExactoryRecorder()
    test_case.addCleanup(setattr, _draft, "_call_exactory", _draft._call_exactory)
    _draft._call_exactory = recorder
    os.environ["EXACTORY_API_URL"] = "https://api.test"
    return recorder


def _count_publishes(fake_api) -> int:
    return sum(1 for request in fake_api.requests if request.full_url.endswith("/actions/publish"))


def _read_journal(workspace_dir) -> dict:
    return json.loads((workspace_dir / ".exactory" / "problem-deposit.json").read_text(encoding="utf-8"))


def _refuse_the_journal_write(write_projection):
    """Stand in for json_projection when the journal cannot be written; every other file is
    written as before."""
    def write(root, relative, value):
        if relative == str(_draft._PROBLEM_DEPOSIT_JOURNAL_PATH):
            raise OSError("No space left on device")
        return write_projection(root, relative, value)
    return write


def _interleave_before_the_journal_release(write_projection, interleave):
    """Stand in for json_projection, and run `interleave` once, just before the run marks the
    journal settled: the moment a release acts on the intent state the run read earlier. Every
    write goes on as before."""
    def write(root, relative, value):
        nonlocal interleave
        if interleave is not None and relative == str(_draft._PROBLEM_DEPOSIT_JOURNAL_PATH) and value["settled"]:
            run, interleave = interleave, None
            run()
        return write_projection(root, relative, value)
    return write


class TestManagedDepositForAProblem(_DepositTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def _lose_the_publish_response(self) -> bytes:
        """Run the deposit with its publish response lost, and return the uploaded paper."""
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        return _read_request(self.fake_api.requests, "PUT", "/files/bucket-1/paper.pdf").data

    def _answer_with_the_published_record(self, uploaded: bytes) -> None:
        self.fake_api = _PublishedRecordZenodoApi(uploaded)
        _draft._open_url = self.fake_api

    def _requested(self) -> list[tuple[str, str]]:
        return [(request.get_method(), request.full_url) for request in self.fake_api.requests]

    def test_a_production_publish_names_the_problem_journals_it_and_attaches(self) -> None:
        self._deposit(_PROBLEM_DEPOSIT)

        metadata = json.loads(_read_request(self.fake_api.requests, "PUT", "/deposit/depositions/4242").data)["metadata"]
        self.assertEqual(metadata["related_identifiers"],
                         [{"identifier": f"https://api.test/math/{_PROBLEM_ID}", "relation": "references"}])
        intent_id = next(iter(Store(self.workspace_dir).snapshot()["records"]["remote_intent"]))
        self.assertEqual(_read_journal(self.workspace_dir),
                         {"operation_id": intent_id, "path": "managed", "environment": "production",
                          "base_url": "https://zenodo.org/api", "math_problem": _PROBLEM, "settled": True})
        self.assertEqual(self.exactory.links(),
                         [(["link-problem-paper", _PROBLEM_ID, "--identifier", "10.5281/zenodo.4242"], "https://api.test")])

    def test_an_attachment_failure_leaves_a_journal_that_attach_problem_retries_alone(self) -> None:
        self.exactory.failing_links = 1
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertIn("is published", output)
        self.assertIn("exactory-draft attach-problem", output)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        publishes = _count_publishes(self.fake_api)

        # A changed environment does not move the attachment: the recorded origin decides.
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(_count_publishes(self.fake_api), publishes)
        self.assertEqual([url for _, url in self.exactory.links()], ["https://api.test", "https://api.test"])
        self.assertTrue(_read_journal(self.workspace_dir)["settled"])

    def test_a_rerun_after_an_attachment_failure_attaches_alone_and_sends_zenodo_nothing(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        sent = len(self.fake_api.requests)

        output = self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(len(self.fake_api.requests), sent)
        self.assertEqual(len(self.exactory.links()), 2)
        self.assertIn("deposited nothing new", output)

    def test_a_lost_publish_response_is_read_back_by_the_same_command_and_then_attached(self) -> None:
        uploaded = self._lose_the_publish_response()
        self.assertEqual(self.exactory.links(), [])
        self._answer_with_the_published_record(uploaded)

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self._requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_rerun_settles_the_recorded_intent_whatever_the_draft_and_the_origin_say(self) -> None:
        uploaded = self._lose_the_publish_response()
        # The draft changes and the environment names another application. Without the
        # journal, the changed draft sends the rerun down the direct path, which creates and
        # publishes a second record.
        (self.workspace_dir / "draft" / "references.bib").unlink()
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        self._answer_with_the_published_record(uploaded)

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self._requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual([argv[0] for argv, _ in self.exactory.calls], ["me", "problem", "link-problem-paper"])
        self.assertEqual([url for _, url in self.exactory.links()], ["https://api.test"])
        receipts = Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]
        self.assertEqual([receipt["doi"] for receipt in receipts.values()], ["10.5281/zenodo.4242"])

    def test_attach_problem_settles_a_lost_publish_without_a_deposit_projection(self) -> None:
        uploaded = self._lose_the_publish_response()
        # The managed path writes the deposit projection only when its intent completes.
        self.assertFalse((self.workspace_dir / ".exactory" / "deposit.json").exists())
        self._answer_with_the_published_record(uploaded)

        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(self._requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(len(self.exactory.links()), 1)
        deposit_state = json.loads((self.workspace_dir / ".exactory" / "deposit.json").read_text(encoding="utf-8"))
        self.assertEqual(deposit_state["doi"], "10.5281/zenodo.4242")

    def _find_the_deposition_unpublished_with_attach_problem(self, uploaded: bytes) -> str:
        """Run attach-problem while the deposition reads back unpublished, and return the recorded
        intent's id. The read discards the publish claim, and attach-problem publishes nothing."""
        self.fake_api = _UnpublishedRecordZenodoApi(uploaded)
        _draft._open_url = self.fake_api
        self.assertIn("publication_unconfirmed", _run_draft_command(["attach-problem"], 1, self))
        intent_id = _read_journal(self.workspace_dir)["operation_id"]
        intent = Store(self.workspace_dir).snapshot()["records"]["remote_intent"][intent_id]
        self.assertEqual([entry["name"] for entry in intent["discarded"]], ["publish"])
        self.fake_api = _FakeZenodoApi()
        _draft._open_url = self.fake_api
        return intent_id

    def test_a_rerun_publishes_the_deposition_attach_problem_found_unpublished(self) -> None:
        intent_id = self._find_the_deposition_unpublished_with_attach_problem(self._lose_the_publish_response())

        output = self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self._requested(), [("POST", "https://zenodo.org/api/deposit/depositions/4242/actions/publish")])
        self.assertIn("deposited nothing new", output)
        self.assertEqual([argv[0] for argv, _ in self.exactory.calls], ["me", "problem", "link-problem-paper"])
        self.assertEqual(list(Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]), [intent_id])
        self.assertTrue(_read_journal(self.workspace_dir)["settled"])

    def test_changed_inputs_after_attach_problem_found_the_deposition_unpublished_select_no_new_operation(self) -> None:
        intent_id = self._find_the_deposition_unpublished_with_attach_problem(self._lose_the_publish_response())
        # The draft changes and the environment names another application. A released journal
        # would let this run select another operation: the changed draft takes the direct path,
        # which creates and publishes a second record.
        (self.workspace_dir / "draft" / "references.bib").unlink()
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        # The recorded intent still binds the run, and its manuscript check stops the publish.
        self.assertIn("readiness_required", output)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual([argv[0] for argv, _ in self.exactory.calls], ["me", "problem"])
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertEqual(list(records["remote_intent"]), [intent_id])
        self.assertNotIn("direct_deposit", records)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["operation_id"], journal["settled"]), (intent_id, False))

    def _stop_before_the_publish_claim(self) -> str:
        """Run the deposit until the read of its record's draft document is lost, before any
        publish claim, and return the recorded intent's id."""
        self.fake_api = _LostResponseZenodoApi("/records/4242/draft")
        _draft._open_url = self.fake_api
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.fake_api = _FakeZenodoApi()
        _draft._open_url = self.fake_api
        return _read_journal(self.workspace_dir)["operation_id"]

    def test_a_reconcile_during_the_release_of_an_unclaimed_journal_is_refused_and_one_record_is_published(self) -> None:
        intent_id = self._stop_before_the_publish_claim()
        # The environment names another application, so the released run selects another
        # operation. A reconcile that published the recorded intent while this run released its
        # journal left two published records.
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        reconcile_outputs = []
        with unittest.mock.patch.object(_draft, "json_projection", _interleave_before_the_journal_release(
                _draft.json_projection,
                lambda: reconcile_outputs.append(_run_draft_command(["reconcile", intent_id], 1, self)))):
            self._deposit(_PROBLEM_DEPOSIT)

        self.assertIn("remote_operation_owned", reconcile_outputs[0])
        self.assertEqual(_count_publishes(self.fake_api), 1)
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertNotEqual(records["remote_intent"][intent_id]["status"], "complete")
        self.assertNotIn(intent_id, records["publication_receipt"])
        self.assertEqual([url for _, url in self.exactory.links()], ["https://elsewhere.test"])

    def test_reconcile_from_a_subdirectory_finishes_the_recorded_intent(self) -> None:
        intent_id = self._stop_before_the_publish_claim()
        os.chdir(self.workspace_dir / "draft")

        _run_draft_command(["reconcile", intent_id], None, self)

        self.assertEqual(_count_publishes(self.fake_api), 1)
        intent = Store(self.workspace_dir).snapshot()["records"]["remote_intent"][intent_id]
        self.assertEqual(intent["status"], "complete")

    def test_a_journal_that_cannot_be_written_stops_before_any_zenodo_request(self) -> None:
        with unittest.mock.patch.object(_draft, "json_projection", _refuse_the_journal_write(_draft.json_projection)):
            output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertEqual(self.fake_api.requests, [])
        self.assertIn("nothing was sent to Zenodo", output)

    def test_a_sandbox_publish_names_the_problem_and_attaches_nothing(self) -> None:
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])
        metadata = json.loads(_read_request(self.fake_api.requests, "PUT", "/deposit/depositions/4242").data)["metadata"]
        self.assertEqual(metadata["related_identifiers"][0]["relation"], "references")
        self.assertEqual(self.exactory.links(), [])
        self.assertIn("Only a published production record is attached", output)
        self.assertTrue(_read_journal(self.workspace_dir)["settled"])

    def test_an_unlinked_orcid_stops_before_any_zenodo_request(self) -> None:
        self.exactory.orcid_id = None
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertEqual(self.fake_api.requests, [])
        self.assertIn("https://api.test/account", output)

    def test_a_problem_id_that_is_not_an_id_is_refused_before_any_request(self) -> None:
        self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", "not-an-id"], expected_exit_code=2)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])


class TestDirectDepositForAProblem(_PlainWorkspaceDepositTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def _lose_the_publish_response(self) -> bytes:
        """Run the deposit with its publish response lost, and return the uploaded paper."""
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        _, stderr_text = self._deposit_on_split_streams(_PROBLEM_DEPOSIT, expected_exit_code=1)
        # The journal makes a rerun safe, so the advice sends the user back to the same command.
        self.assertIn("Run the same command again", stderr_text)
        self.assertNotIn("a second run publishes a second permanent record", stderr_text)
        return _read_request(self.fake_api.requests, "PUT", "/files/bucket-1/paper.pdf").data

    def _answer_with(self, fake_api) -> None:
        self.fake_api = fake_api
        _draft._open_url = fake_api

    def test_the_journal_and_the_deposit_record_are_kept_before_the_publish_call(self) -> None:
        exactory_dir = self.workspace_dir / ".exactory"
        self._answer_with(_StateAtPublishZenodoApi([exactory_dir / "problem-deposit.json", exactory_dir / "deposit.json"]))

        self._deposit(_PROBLEM_DEPOSIT)

        journal = self.fake_api.files_at_publish["problem-deposit.json"]
        self.assertRegex(journal.pop("operation_id"), r"^[0-9a-f]{32}$")
        self.assertEqual(journal, {"path": "direct", "environment": "production", "base_url": "https://zenodo.org/api",
                                   "deposition_id": 4242, "draft_url": "https://zenodo.org/api/deposit/4242",
                                   "math_problem": _PROBLEM, "settled": False})
        self.assertEqual(self.fake_api.files_at_publish["deposit.json"]["deposition_id"], 4242)
        self.assertNotIn("doi", self.fake_api.files_at_publish["deposit.json"])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertTrue(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_rerun_after_a_lost_publish_reads_the_deposition_back_and_creates_nothing(self) -> None:
        uploaded = self._lose_the_publish_response()
        self._answer_with(_PublishedRecordZenodoApi(uploaded))

        output = self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertEqual(len(self.exactory.links()), 1)
        self.assertIn("deposited nothing new", output)

    def test_attach_problem_reads_a_lost_publish_back_and_never_publishes(self) -> None:
        uploaded = self._lose_the_publish_response()
        self._answer_with(_PublishedRecordZenodoApi(uploaded))

        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_rerun_publishes_the_same_deposition_when_it_reads_back_unpublished(self) -> None:
        uploaded = self._lose_the_publish_response()
        self._answer_with(_UnpublishedRecordZenodoApi(uploaded))

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242"),
                                            ("POST", "https://zenodo.org/api/deposit/depositions/4242/actions/publish")])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertEqual(len(self.exactory.links()), 1)

    def test_attach_problem_leaves_an_unpublished_deposition_unpublished(self) -> None:
        uploaded = self._lose_the_publish_response()
        self._answer_with(_UnpublishedRecordZenodoApi(uploaded))

        output = _run_draft_command(["attach-problem"], 1, self)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertIn("Run exactory-draft deposit to publish it", output)
        self.assertEqual(self.exactory.links(), [])

    def test_a_rerun_after_a_failed_attachment_attaches_the_recorded_record_alone(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self._answer_with(_PublishedRecordZenodoApi(b"%PDF-1.4 fake paper"))
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual([url for _, url in self.exactory.links()], ["https://api.test", "https://api.test"])

    def test_a_refused_store_record_and_a_lost_publish_survive_a_projection_export(self) -> None:
        store = Store(self.workspace_dir)
        earlier = {"environment": "production", "deposition_id": 4000, "draft_url": "https://zenodo.org/deposit/4000",
                   "doi": "10.5281/zenodo.4000", "concept_doi": "10.5281/zenodo.3999",
                   "record_url": "https://zenodo.org/records/4000"}
        record_direct_deposit(store, earlier, expected_revision=store.revision, request_id="direct-deposit-earlier")
        with unittest.mock.patch.object(_draft, "record_direct_deposit", _refuse_the_direct_deposit_record):
            uploaded = self._lose_the_publish_response()
        # An export rewrites the deposit file from the store's earlier record. It never writes the journal.
        export_workspace(Store(self.workspace_dir))
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4000)
        self._answer_with(_PublishedRecordZenodoApi(uploaded))

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_journal_that_cannot_be_written_stops_before_the_publish_call(self) -> None:
        with unittest.mock.patch.object(_draft, "json_projection", _refuse_the_journal_write(_draft.json_projection)):
            output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertNotIn(("POST", "https://zenodo.org/api/deposit/depositions/4242/actions/publish"), self.requested())
        self.assertIn("so it was not published", output)
        self.assertEqual(self.exactory.links(), [])

    def test_a_deposit_that_overlaps_a_running_one_is_refused_and_one_record_is_published(self) -> None:
        # Two runs that both read no journal each created and published a record. The second
        # run starts while the first creates its deposition, and the workspace lock refuses it.
        self._answer_with(_InterleavingZenodoApi(b"%PDF-1.4 fake paper", "POST", "/deposit/depositions",
                                                 lambda: self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)))

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertIn("remote_operation_owned", self.fake_api.interleaved)
        self.assertEqual(self.requested().count(("POST", "https://zenodo.org/api/deposit/depositions")), 1)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual([argv[0] for argv, _ in self.exactory.calls], ["me", "problem", "link-problem-paper"])
        self.assertTrue(_read_journal(self.workspace_dir)["settled"])

    def test_attach_problem_that_overlaps_a_running_deposit_is_refused(self) -> None:
        # attach-problem starts while the publish request is out and the journal is unsettled.
        # The lock refuses it, so the deposit alone records and attaches the record.
        self._answer_with(_InterleavingZenodoApi(b"%PDF-1.4 fake paper", "POST", "/actions/publish",
                                                 lambda: _run_draft_command(["attach-problem"], 1, self)))

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertIn("remote_operation_owned", self.fake_api.interleaved)
        self.assertNotIn(("GET", "https://zenodo.org/api/deposit/depositions/4242"), self.requested())
        self.assertEqual(len(self.exactory.links()), 1)
        self.assertTrue(_read_journal(self.workspace_dir)["settled"])

    def _replace_the_journal_when(self, url_suffix: str) -> None:
        """Write the journal of another operation when the named request goes out: a writer
        outside the run, which the workspace lock does not stop."""
        journal_path = self.workspace_dir / ".exactory" / "problem-deposit.json"
        self._answer_with(_InterleavingZenodoApi(b"%PDF-1.4 fake paper", "POST", url_suffix,
                                                 lambda: journal_path.write_text(json.dumps(_OTHER_JOURNAL), encoding="utf-8")))

    def test_the_journal_of_another_operation_stops_the_run_before_its_publish_call(self) -> None:
        self._replace_the_journal_when("/deposit/depositions")

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"records operation {_OTHER_JOURNAL['operation_id']}", output)
        self.assertIn("so it was not published", output)
        self.assertEqual(_count_publishes(self.fake_api), 0)
        self.assertEqual(_read_journal(self.workspace_dir), _OTHER_JOURNAL)

    def test_a_settled_mark_leaves_the_journal_of_another_operation_as_it_is(self) -> None:
        self._replace_the_journal_when("/actions/publish")

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"records operation {_OTHER_JOURNAL['operation_id']}", output)
        self.assertIn("Run exactory-draft attach-problem", output)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(_read_journal(self.workspace_dir), _OTHER_JOURNAL)

    def test_attach_problem_outside_the_workspace_root_is_refused_and_leaves_no_lock_there(self) -> None:
        os.chdir(self.workspace_dir / "draft")

        output = _run_draft_command(["attach-problem"], 2, self)

        self.assertIn(".exactory/draft.json does not exist here.", output)
        self.assertFalse((self.workspace_dir / "draft" / "research").exists())


# The cases below are the paths the deposit tests above do not reach: the real body of
# _call_exactory, which they replace, and the refusals that stop a deposit for a problem.


class TestCallExactory(unittest.TestCase):
    """The one function that reaches the exactory API runs the exactory command in a child
    process. These tests run its own body against a stand-in for subprocess.run."""

    def test_the_command_runs_against_the_recorded_origin_with_the_caller_environment(self) -> None:
        answer = subprocess.CompletedProcess(["problem"], 0, "{}", "")
        with unittest.mock.patch.dict(os.environ, {"EXACTORY_API_URL": "https://elsewhere.test",
                                                   "EXACTORY_API_KEY": "key-for-the-test"}), \
                unittest.mock.patch.object(subprocess, "run", return_value=answer) as run:
            self.assertIs(_draft._call_exactory(["problem", _PROBLEM_ID], "https://api.test"), answer)

        command = run.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "exactory")
        self.assertTrue(Path(command[1]).is_file())
        self.assertEqual(command[2:], ["problem", _PROBLEM_ID])
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["EXACTORY_API_URL"], "https://api.test")
        self.assertEqual(environment["EXACTORY_API_KEY"], "key-for-the-test")
        self.assertEqual(run.call_args.kwargs["timeout"], _draft._EXACTORY_TIMEOUT_SECONDS)

    def test_a_command_that_does_not_answer_ends_as_a_failed_read(self) -> None:
        with unittest.mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["exactory"], 120)):
            completed = _draft._call_exactory(["me"], "https://api.test")

        self.assertEqual(completed.returncode, 1)
        self.assertIn(f"did not answer within {_draft._EXACTORY_TIMEOUT_SECONDS} seconds", completed.stderr)


class TestProblemDepositRefusals(_PlainWorkspaceDepositTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def _answer_exactory_command(self, command: str, completed: subprocess.CompletedProcess) -> None:
        """Answer the named exactory command with `completed`. The recorder answers every other one."""
        recorder = self.exactory

        def call(argv: list[str], api_url: str) -> subprocess.CompletedProcess:
            if argv[0] != command:
                return recorder(argv, api_url)
            recorder.calls.append((list(argv), api_url))
            return completed
        _draft._call_exactory = call

    def _write_journal_text(self, text: str) -> None:
        (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text(text, encoding="utf-8")

    def test_a_removed_problem_stops_before_any_zenodo_request(self) -> None:
        self._answer_exactory_command("problem", subprocess.CompletedProcess(
            ["problem"], 0, json.dumps({"id": _PROBLEM_ID, "removed": True}), ""))

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"Problem {_PROBLEM_ID} is removed", output)
        self.assertEqual(self.fake_api.requests, [])

    def test_an_exactory_read_that_fails_stops_before_any_zenodo_request(self) -> None:
        self._answer_exactory_command("me", subprocess.CompletedProcess(
            ["me"], 1, "", "The server rejected the API key."))

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("exactory me failed, so nothing was deposited", output)
        self.assertIn("The server rejected the API key.", output)
        self.assertEqual([argv[0] for argv, _ in self.exactory.calls], ["me"])
        self.assertEqual(self.fake_api.requests, [])

    def test_an_exactory_answer_that_is_not_json_stops_before_any_zenodo_request(self) -> None:
        self._answer_exactory_command("me", subprocess.CompletedProcess(["me"], 0, "not json", ""))

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("exactory me printed no JSON, so nothing was deposited", output)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_draft_deposit_names_the_problem_and_keeps_no_journal(self) -> None:
        os.environ["EXACTORY_API_URL"] = "https://api.test/"

        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])

        metadata = json.loads(_read_request(self.fake_api.requests, "PUT", "/deposit/depositions/4242").data)["metadata"]
        self.assertEqual(metadata["related_identifiers"],
                         [{"identifier": f"https://api.test/math/{_PROBLEM_ID}", "relation": "references"}])
        self.assertEqual({url for _, url in self.exactory.calls}, {"https://api.test"})
        self.assertEqual(self.exactory.links(), [])
        self.assertEqual(_count_publishes(self.fake_api), 0)
        self.assertIn("This draft stays unattached", output)
        self.assertFalse((self.workspace_dir / ".exactory" / "problem-deposit.json").exists())

    def test_the_application_origin_defaults_to_the_production_one(self) -> None:
        del os.environ["EXACTORY_API_URL"]

        self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", _PROBLEM_ID])

        origin = "https://www.exactory.ai"
        self.assertEqual({url for _, url in self.exactory.calls}, {origin})
        self.assertEqual(_read_journal(self.workspace_dir)["math_problem"], {"problem_id": _PROBLEM_ID, "api_url": origin})

    def test_attach_problem_without_a_journal_is_refused(self) -> None:
        output = _run_draft_command(["attach-problem"], 1, self)

        self.assertIn(".exactory/problem-deposit.json does not exist here", output)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_attach_problem_on_a_settled_journal_changes_nothing(self) -> None:
        self._deposit(_PROBLEM_DEPOSIT)
        sent = len(self.fake_api.requests)

        output = _run_draft_command(["attach-problem"], None, self)

        self.assertIn("is already settled", output)
        self.assertEqual(len(self.fake_api.requests), sent)
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_journal_that_cannot_be_read_stops_every_command_before_it_sends_anything(self) -> None:
        for text in ("{not json", "[]", json.dumps({"path": "elsewhere"})):
            self._write_journal_text(text)
            for argv, failure in ((["deposit", "--abstract-file", "draft/abstract.txt", *_PROBLEM_DEPOSIT],
                                   "Nothing was deposited."),
                                  (["attach-problem"], "Nothing was attached.")):
                with self.subTest(journal=text, command=argv[0]):
                    output = _run_draft_command(argv, 1, self)
                    self.assertIn("problem-deposit.json", output)
                    self.assertIn(failure, output)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_a_deposit_for_no_problem_that_overlaps_a_running_one_is_refused(self) -> None:
        # Every deposit reads the journal first, so every deposit takes the workspace lock.
        self.fake_api = _InterleavingZenodoApi(b"%PDF-1.4 fake paper", "POST", "/deposit/depositions",
                                               lambda: self._deposit(_PRODUCTION_PUBLISH, expected_exit_code=1))
        _draft._open_url = self.fake_api

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertIn("remote_operation_owned", self.fake_api.interleaved)
        self.assertEqual(self.requested().count(("POST", "https://zenodo.org/api/deposit/depositions")), 1)
        self.assertEqual(_count_publishes(self.fake_api), 1)


if __name__ == "__main__":
    unittest.main()
