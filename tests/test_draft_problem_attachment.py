"""Tests for deposits for a math problem: exactory-draft deposit --math-problem, its
recovery journal, and attach-problem (design 2026-10-12 §17)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

from research_harness.errors import ResearchError
from research_harness.exactory_api import UUID_RE, build_orcid_required_msg, read_api_base_url
from research_harness.integration import export_workspace, record_direct_deposit
from research_harness.storage import Store
from research_harness.zenodo import has_published_record, require_published_prior
from test_draft import (
    _DepositTestCase,
    _FakeZenodoApi,
    _LostResponseZenodoApi,
    _PlainWorkspaceDepositTestCase,
    _PublishedRecordZenodoApi,
    _StatusErrorZenodoApi,
    _UnpublishedRecordZenodoApi,
    _draft,
    _load_bin_module,
    _read_request,
    _refuse_the_direct_deposit_record,
    _run_draft_command,
)

_PROBLEM_ID = "656c336e-e892-4c47-80d2-a71d022f4116"
_PROBLEM_TARGET = {"problem_id": _PROBLEM_ID, "api_url": "https://api.test"}
_PRODUCTION_PUBLISH = ["--production", "--publish", "--confirm-publish", "--creator", "Shiroshita, Ryosuke"]
_PROBLEM_DEPOSIT = [*_PRODUCTION_PUBLISH, "--math-problem", _PROBLEM_ID]
# The journal of an operation this run did not start: the state a writer outside the run
# leaves behind, which no write of this run replaces. It is unsettled, so its publication is
# uncertain. The journals below are the other states of the same operation.
_OTHER_JOURNAL = {"operation_id": "0" * 32, "path": "direct", "environment": "production",
                  "base_url": "https://zenodo.org/api", "deposition_id": 4000,
                  "draft_url": "https://zenodo.org/api/deposit/4000", "math_problem": _PROBLEM_TARGET,
                  "settled": False, "attached": False}
_EARLIER_DOI = "10.5281/zenodo.4000"
# Settled with a DOI: the publication is certain, and the record waits for its attachment.
_UNATTACHED_JOURNAL = dict(_OTHER_JOURNAL, settled=True, doi=_EARLIER_DOI)
_ATTACHED_JOURNAL = dict(_UNATTACHED_JOURNAL, attached=True)
_SANDBOX_JOURNAL = dict(_UNATTACHED_JOURNAL, environment="sandbox", base_url="https://sandbox.zenodo.org/api")
# Settled with no DOI: the deposit never published a record.
_RELEASED_JOURNAL = dict(_OTHER_JOURNAL, settled=True)
_JOURNAL_PATH_TEXT = ".exactory/problem-deposit.json"
# The sentence that every message leaving an unsettled journal ends with.
_REMOVAL_RULE = f"Remove {_JOURNAL_PATH_TEXT} only after you check on Zenodo that"


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


def _refuse_the_journal_write_when(write_projection, is_refused):
    """Stand in for json_projection when the journal cannot be written for the values that
    `is_refused` names; every other write goes on as before."""
    def write(root, relative, value):
        if relative == str(_draft._PROBLEM_DEPOSIT_JOURNAL_PATH) and is_refused(value):
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
                          "base_url": "https://zenodo.org/api", "math_problem": _PROBLEM_TARGET,
                          "settled": True, "attached": True, "doi": "10.5281/zenodo.4242"})
        self.assertEqual(self.exactory.links(),
                         [(["link-problem-paper", _PROBLEM_ID, "--identifier", "10.5281/zenodo.4242"], "https://api.test")])

    def test_an_attachment_failure_leaves_a_settled_journal_that_attach_problem_retries_alone(self) -> None:
        self.exactory.failing_links = 1
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertIn("is published", output)
        self.assertIn("run exactory-draft attach-problem", output)
        # The publication is certain and recorded, so only the attachment is open.
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]),
                         (True, False, "10.5281/zenodo.4242"))
        sent = len(self.fake_api.requests)

        # A changed environment does not move the attachment: the recorded origin decides. The journal
        # holds the record, so the retry reads no Zenodo token and sends no Zenodo request.
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        del os.environ["ZENODO_TOKEN"], os.environ["ZENODO_SANDBOX_TOKEN"]
        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(len(self.fake_api.requests), sent)
        self.assertEqual([url for _, url in self.exactory.links()], ["https://api.test", "https://api.test"])
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

    def test_a_rerun_after_an_attachment_failure_resumes_its_own_intent_and_attaches(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        sent = len(self.fake_api.requests)

        self._deposit(_PROBLEM_DEPOSIT)

        # The unchanged deposit finds its own complete intent, so Zenodo gets nothing and the record attaches.
        self.assertEqual(len(self.fake_api.requests), sent)
        self.assertEqual(len(self.exactory.links()), 2)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

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
        output = _run_draft_command(["attach-problem"], 1, self)
        self.assertIn("publication_unconfirmed", output)
        self.assertIn("The record of this deposit is not published. Run exactory-draft deposit to finish it.", output)
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
        # The run completes the operation that the earlier run approved with its own flags.
        self.assertIn("completed the earlier deposit for problem", output)
        self.assertIn("which the earlier run started with its own approval", output)
        self.assertIn("deposited nothing new", output)
        self.assertEqual([argv[0] for argv, _ in self.exactory.calls], ["me", "problem", "link-problem-paper"])
        self.assertEqual(list(Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]), [intent_id])
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, True, "10.5281/zenodo.4242"))

    def test_changed_inputs_after_attach_problem_found_the_deposition_unpublished_select_no_new_operation(self) -> None:
        intent_id = self._find_the_deposition_unpublished_with_attach_problem(self._lose_the_publish_response())
        # The draft changes and the environment names another application. A released journal
        # would let this run select another operation: the changed draft takes the direct path,
        # which creates and publishes a second record.
        (self.workspace_dir / "draft" / "references.bib").unlink()
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        # The recorded intent still binds the run, and its manuscript check stops the publish. The
        # message says that the journal stays, which deposition it names, and when removing it is safe.
        self.assertIn("readiness_required", output)
        self.assertIn(f"must match the saved publication intent. {_JOURNAL_PATH_TEXT} stays as the recovery record of"
                      " this deposit", output)
        self.assertIn(f"{_REMOVAL_RULE} the deposition whose notes read", output)
        self.assertIn(f"Exactory publication intent {intent_id}", output)
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
        # attach-problem never publishes, so it leaves the unclaimed deposit as it is and says what to run.
        stopped = _run_draft_command(["attach-problem"], 1, self)
        self.assertIn("The recorded deposit never sent its publish request, so there is no record to attach."
                      " Run exactory-draft deposit to finish it.", stopped)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", stopped)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.fake_api.requests, [])
        # A release that cannot be written leaves the journal as it is, and the message says so.
        refuse = _refuse_the_journal_write_when(_draft.json_projection, lambda value: value["settled"])
        with unittest.mock.patch.object(_draft, "json_projection", refuse):
            refused = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertIn("The recovery record could not be released. Nothing was deposited.", refused)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", refused)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.fake_api.requests, [])
        # The environment names another application, so the released run selects another
        # operation. A reconcile that published the recorded intent while this run released its
        # journal left two published records.
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        reconcile_outputs, journal_writes = [], []

        def spy_on_the_journal_writes(write_projection):
            def write(root, relative, value):
                if relative == str(_draft._PROBLEM_DEPOSIT_JOURNAL_PATH):
                    journal_writes.append(dict(value))
                return write_projection(root, relative, value)
            return write
        with unittest.mock.patch.object(_draft, "json_projection", _interleave_before_the_journal_release(
                spy_on_the_journal_writes(_draft.json_projection),
                lambda: reconcile_outputs.append(_run_draft_command(["reconcile", intent_id], 1, self)))):
            self._deposit(_PROBLEM_DEPOSIT)

        # The release marks the unclaimed operation settled with no DOI: it never published a record.
        self.assertEqual(journal_writes[0], {"operation_id": intent_id, "path": "managed", "environment": "production",
                                             "base_url": "https://zenodo.org/api", "math_problem": _PROBLEM_TARGET,
                                             "settled": True, "attached": False})
        self.assertIn("remote_operation_owned", reconcile_outputs[0])
        self.assertEqual(_count_publishes(self.fake_api), 1)
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertNotEqual(records["remote_intent"][intent_id]["status"], "complete")
        self.assertNotIn(intent_id, records["publication_receipt"])
        self.assertEqual([url for _, url in self.exactory.links()], ["https://elsewhere.test"])

    def test_reconcile_from_a_subdirectory_finishes_the_recorded_intent(self) -> None:
        intent_id = self._stop_before_the_publish_claim()
        os.chdir(self.workspace_dir / "draft")

        output = _run_draft_command(["reconcile", intent_id], None, self)

        self.assertEqual(_count_publishes(self.fake_api), 1)
        intent = Store(self.workspace_dir).snapshot()["records"]["remote_intent"][intent_id]
        self.assertEqual(intent["status"], "complete")
        # The deposit belongs to the recorded deposit for the problem, and its record is not attached yet.
        self.assertIn(f"This deposit belongs to the recorded deposit for problem {_PROBLEM_ID}, and its record"
                      f" is not attached yet. Run exactory-draft attach-problem from {self.workspace_dir.resolve()}.",
                      output)
        self.assertEqual(self.exactory.links(), [])

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
        self.assertIn("Only a published production record is attached to a problem. This sandbox record stays"
                      " unattached.", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, False, "10.5281/zenodo.4242"))

    def test_an_unlinked_orcid_stops_before_any_zenodo_request(self) -> None:
        self.exactory.orcid_id = None
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertEqual(self.fake_api.requests, [])
        self.assertIn("https://api.test/account", output)

    def test_a_problem_id_that_is_not_an_id_is_refused_before_any_request(self) -> None:
        self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", "not-an-id"], expected_exit_code=2)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_new_version_refuses_a_prior_draft_that_has_no_doi_and_names_it(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.fake_api.requests.clear()

        output = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)

        # The managed check refuses first, and the direct path meets the same rule in its file.
        self.assertIn("Managed record skipped (publication_prior_required): The prior deposit, deposition 4242",
                      output)
        self.assertIn("has no DOI, so it is not a published record.", output)
        self.assertEqual(self.fake_api.requests, [])


class TestDirectDepositForAProblem(_PlainWorkspaceDepositTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def _lose_the_publish_response(self) -> bytes:
        """Run the deposit with its publish response lost, and return the uploaded paper."""
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        _, stderr_text = self._deposit_on_split_streams(_PROBLEM_DEPOSIT, expected_exit_code=1)
        # The journal makes a rerun safe, so the advice sends the user back to the same command. It
        # promises an attachment of a production record only, and it says when removing the journal is safe.
        self.assertIn("Run the same command again", stderr_text)
        self.assertIn("A production record is also attached to the problem.", stderr_text)
        self.assertNotIn("a second run publishes a second permanent record", stderr_text)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", stderr_text)
        self.assertIn(f"{_REMOVAL_RULE} deposition 4242 (https://zenodo.org/api/deposit/4242) is not published,"
                      " or decide not to publish it. The command never removes it.", stderr_text)
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
                                   "math_problem": _PROBLEM_TARGET, "settled": False, "attached": False})
        self.assertEqual(self.fake_api.files_at_publish["deposit.json"]["deposition_id"], 4242)
        self.assertNotIn("doi", self.fake_api.files_at_publish["deposit.json"])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        # Recorded and attached: the journal is settled with the DOI, and nothing is left to do.
        final_journal = _read_journal(self.workspace_dir)
        self.assertEqual((final_journal["settled"], final_journal["attached"], final_journal["doi"]),
                         (True, True, "10.5281/zenodo.4242"))
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
        self.assertIn("Deposition 4242 is not published. Run exactory-draft deposit to publish it. That command"
                      " publishes this deposition and creates no other.", output)
        self.assertEqual(self.exactory.links(), [])

    def test_attach_problem_retries_a_failed_attachment_without_zenodo_and_without_a_token(self) -> None:
        self.exactory.failing_links = 1
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        # Only attach-problem retries the attachment: a rerun of the deposit would create a new record.
        self.assertIn("Resolve that cause. Then run exactory-draft attach-problem. It attaches the recorded record"
                      " and publishes nothing.", output)
        self.assertNotIn("the same deposit command", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]),
                         (True, False, "10.5281/zenodo.4242"))
        sent = len(self.fake_api.requests)
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        del os.environ["ZENODO_TOKEN"], os.environ["ZENODO_SANDBOX_TOKEN"]

        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(len(self.fake_api.requests), sent)
        self.assertEqual([url for _, url in self.exactory.links()], ["https://api.test", "https://api.test"])
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

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
        self.assertIn("Another exactory-draft run owns the deposits of this workspace. Wait for that run to end."
                      " Then run this command again.", self.fake_api.interleaved)
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
        # That journal is unsettled, so the message says when removing it is safe.
        self.assertIn(f"{_REMOVAL_RULE} deposition 4000 (https://zenodo.org/api/deposit/4000) is not published,"
                      " or decide not to publish it. The command never removes it.", output)
        self.assertEqual(_count_publishes(self.fake_api), 0)
        self.assertEqual(_read_journal(self.workspace_dir), _OTHER_JOURNAL)

    def test_a_settled_mark_leaves_the_journal_of_another_operation_as_it_is(self) -> None:
        self._replace_the_journal_when("/actions/publish")

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"records operation {_OTHER_JOURNAL['operation_id']}", output)
        self.assertIn("Run exactory-draft attach-problem", output)
        # The file records another operation, so the note on this run's journal stays silent.
        self.assertNotIn("stays as the recovery record of this deposit", output)
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

    def _write_journal_bytes(self, data: bytes) -> Path:
        path = self.workspace_dir / ".exactory" / "problem-deposit.json"
        path.write_bytes(data)
        return path

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

    def test_attach_problem_on_an_attached_journal_changes_nothing(self) -> None:
        self._deposit(_PROBLEM_DEPOSIT)
        sent = len(self.fake_api.requests)

        output = _run_draft_command(["attach-problem"], None, self)

        self.assertIn("is already attached. Nothing was changed.", output)
        self.assertEqual(len(self.fake_api.requests), sent)
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_journal_that_cannot_be_trusted_stops_every_command_before_it_sends_anything(self) -> None:
        unsettled = dict(_OTHER_JOURNAL)
        cases = (
            ("not JSON", b"{not json", "cannot be read. Cause: "),
            ("not UTF-8", b"\xff\xfe\x00{", "cannot be read. Cause: "),
            ("a list", b"[]", "it is not a JSON object"),
            ("a path of no command", json.dumps(dict(unsettled, path="elsewhere")).encode(),
             'the key "path" must be "managed" or "direct"'),
            ("a path alone", json.dumps({"path": "direct"}).encode(),
             'the key "operation_id" must be a text that is not empty'),
            ("an empty operation id", json.dumps(dict(unsettled, operation_id="")).encode(),
             'the key "operation_id" must be a text that is not empty'),
            ("an environment that is not Zenodo's", json.dumps(dict(unsettled, environment="staging")).encode(),
             'the key "environment" must be "production" or "sandbox"'),
            ("a host that is not Zenodo's", json.dumps(dict(unsettled, base_url="https://zenodo.example.test/api")).encode(),
             'the key "base_url" must be https://zenodo.org/api, the Zenodo API of the production environment'),
            ("the host of the other environment", json.dumps(dict(unsettled, environment="sandbox")).encode(),
             'the key "base_url" must be https://sandbox.zenodo.org/api, the Zenodo API of the sandbox environment'),
            ("settled as text", json.dumps(dict(unsettled, settled="false")).encode(),
             'the key "settled" must be true or false'),
            ("attached as a number", json.dumps(dict(unsettled, attached=0)).encode(),
             'the key "attached" must be true or false'),
            ("no problem", json.dumps({key: value for key, value in unsettled.items() if key != "math_problem"}).encode(),
             'the key "math_problem" must hold the texts "problem_id" and "api_url"'),
            ("no problem id", json.dumps(dict(unsettled, math_problem={"api_url": "https://api.test"})).encode(),
             'the key "math_problem" must hold the texts "problem_id" and "api_url"'),
            ("a deposition id as text", json.dumps(dict(unsettled, deposition_id="4000")).encode(),
             'the key "deposition_id" must be a whole number'),
            ("a deposition id as a flag", json.dumps(dict(unsettled, deposition_id=True)).encode(),
             'the key "deposition_id" must be a whole number'),
            ("no draft url", json.dumps({key: value for key, value in unsettled.items() if key != "draft_url"}).encode(),
             'the key "draft_url" must be a text'),
            ("an empty DOI", json.dumps(dict(_UNATTACHED_JOURNAL, doi="")).encode(),
             'the key "doi" must be a text that is not empty'),
            ("attached while unsettled", json.dumps(dict(unsettled, attached=True)).encode(),
             'the key "attached" is true, but the journal is not settled with a DOI'),
        )
        commands = ((["deposit", "--abstract-file", "draft/abstract.txt", *_PROBLEM_DEPOSIT], "Nothing was deposited."),
                    (["attach-problem"], "Nothing was attached."),
                    (["reconcile", "no-such-intent"], "Nothing was reconciled."))
        for label, data, complaint in cases:
            path = self._write_journal_bytes(data)
            for argv, failure in commands:
                with self.subTest(journal=label, command=argv[0]):
                    output = _run_draft_command(argv, 1, self)
                    self.assertIn(f"{_JOURNAL_PATH_TEXT} ", output)
                    self.assertIn(complaint, output)
                    # The file may hold an unsettled deposit, so the message says when removing it is safe.
                    self.assertIn(f"{_REMOVAL_RULE} the deposition it names is not published, or decide not to"
                                  " publish it. The command never removes it.", output)
                    self.assertTrue(output.rstrip().endswith(failure), output)
                    self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_an_empty_problem_id_is_refused_and_never_dropped(self) -> None:
        output = self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", ""], expected_exit_code=2)

        self.assertIn('"" is not a problem id. Copy the id from the problem page.', output)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_an_exactory_answer_that_is_not_an_object_stops_before_any_zenodo_request(self) -> None:
        self._answer_exactory_command("problem", subprocess.CompletedProcess(["problem"], 0, "[true]", ""))

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"exactory problem {_PROBLEM_ID} printed JSON that is not an object, so nothing was"
                      " deposited.", output)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_deposit_for_no_problem_that_overlaps_a_running_one_is_refused(self) -> None:
        # Every deposit reads the journal first, so every deposit takes the workspace lock.
        self.fake_api = _InterleavingZenodoApi(b"%PDF-1.4 fake paper", "POST", "/deposit/depositions",
                                               lambda: self._deposit(_PRODUCTION_PUBLISH, expected_exit_code=1))
        _draft._open_url = self.fake_api

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertIn("remote_operation_owned", self.fake_api.interleaved)
        self.assertEqual(self.requested().count(("POST", "https://zenodo.org/api/deposit/depositions")), 1)
        self.assertEqual(_count_publishes(self.fake_api), 1)


class _PublishWithoutDoiZenodoApi(_FakeZenodoApi):
    """Answers like the fake, except that the answer to the publish request carries no DOI."""

    def __call__(self, request):
        if request.get_method() == "POST" and request.full_url.endswith("/actions/publish"):
            self.requests.append(request)
            return {"id": 4242, "links": {"record_html": "https://zenodo.org/api/records/4242"}}
        return super().__call__(request)


class _PublishedWithoutDoiZenodoApi(_FakeZenodoApi):
    """Answers like the fake, except that the deposition of the foreign journal reads back published
    with no DOI."""

    def __call__(self, request):
        if request.get_method() == "GET" and request.full_url.endswith("/deposit/depositions/4000"):
            self.requests.append(request)
            return {"id": 4000, "submitted": True}
        return super().__call__(request)


class _ScreenAtPublishZenodoApi(_FakeZenodoApi):
    """Answers like the fake, and keeps what the command had printed on stderr when the publish
    request went out: what the user has read before a record becomes permanent."""

    def __init__(self) -> None:
        super().__init__()
        self.read_before_publish = ""

    def __call__(self, request):
        if request.full_url.endswith("/actions/publish"):
            self.read_before_publish = sys.stderr.getvalue()
        return super().__call__(request)


class _StoreAtPublishZenodoApi(_FakeZenodoApi):
    """Answers like the fake, and keeps the deposit that the workspace store held when the publish
    request went out: the record that survives a projection export."""

    def __init__(self, workspace_dir: Path) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self.deposit_at_publish: dict | None = None

    def __call__(self, request):
        if request.full_url.endswith("/actions/publish"):
            self.deposit_at_publish = Store(self.workspace_dir).snapshot()["records"].get("workspace", {}).get("deposit")
        return super().__call__(request)


class TestProblemDepositJournalStates(_PlainWorkspaceDepositTestCase):
    """What each state of the journal lets a command do (design 2026-10-12 §17). Unsettled: the
    publication is uncertain, and every deposit settles it first. Settled with a DOI and not
    attached: the publication is certain, and attach-problem attaches the record. The other settled
    states have nothing left to attach."""

    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def _record_journal(self, journal: dict) -> None:
        (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text(json.dumps(journal), encoding="utf-8")

    def _answer_with(self, fake_api) -> None:
        self.fake_api = fake_api
        _draft._open_url = fake_api

    def test_a_settled_journal_does_not_block_a_deposit_that_names_no_problem(self) -> None:
        self._record_journal(_UNATTACHED_JOURNAL)

        output = self._deposit(_PRODUCTION_PUBLISH)

        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(_read_journal(self.workspace_dir), _UNATTACHED_JOURNAL)
        self.assertEqual(self.exactory.calls, [])
        self.assertNotIn("link-problem-paper", output)

    def test_a_deposit_for_a_problem_first_names_the_command_that_attaches_the_earlier_record(self) -> None:
        earlier_problem = {"problem_id": "11111111-2222-4333-8444-555555555555", "api_url": "https://earlier.test"}
        self._record_journal(dict(_UNATTACHED_JOURNAL, math_problem=earlier_problem))
        self._answer_with(_ScreenAtPublishZenodoApi())

        self._deposit(_PROBLEM_DEPOSIT)

        # The user has read the command before the new record is published, and the journal moved on.
        command = (f"EXACTORY_API_URL=https://earlier.test exactory link-problem-paper"
                   f" 11111111-2222-4333-8444-555555555555 --identifier {_EARLIER_DOI}")
        self.assertIn("The earlier deposit for problem 11111111-2222-4333-8444-555555555555 is published as"
                      f" {_EARLIER_DOI}, and its record is not attached yet. To attach it, run this command:\n{command}",
                      self.fake_api.read_before_publish)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["math_problem"], journal["settled"], journal["attached"]), (_PROBLEM_TARGET, True, True))
        self.assertEqual(_count_publishes(self.fake_api), 1)

    def test_a_deposit_for_a_problem_replaces_a_journal_with_nothing_to_attach_without_a_command(self) -> None:
        for label, journal in (("attached", _ATTACHED_JOURNAL), ("sandbox", _SANDBOX_JOURNAL),
                               ("released", _RELEASED_JOURNAL)):
            with self.subTest(journal=label):
                self._record_journal(journal)
                self._answer_with(_ScreenAtPublishZenodoApi())

                self._deposit(_PROBLEM_DEPOSIT)

                self.assertNotIn("To attach it, run this command", self.fake_api.read_before_publish)
                self.assertEqual(_read_journal(self.workspace_dir)["math_problem"], _PROBLEM_TARGET)

    def test_a_deposit_that_only_creates_a_draft_keeps_a_journal_with_an_unattached_record(self) -> None:
        self._record_journal(_UNATTACHED_JOURNAL)

        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])

        self.assertNotIn("To attach it, run this command", output)
        self.assertEqual(_read_journal(self.workspace_dir), _UNATTACHED_JOURNAL)

    def test_attach_problem_attaches_a_settled_record_alone_through_the_recorded_origin(self) -> None:
        earlier = dict(_UNATTACHED_JOURNAL, math_problem={"problem_id": "11111111-2222-4333-8444-555555555555",
                                                          "api_url": "https://earlier.test"})
        self._record_journal(earlier)
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        del os.environ["ZENODO_TOKEN"], os.environ["ZENODO_SANDBOX_TOKEN"]

        output = _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(self.exactory.calls, [(["link-problem-paper", "11111111-2222-4333-8444-555555555555",
                                                 "--identifier", _EARLIER_DOI], "https://earlier.test")])
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(_read_journal(self.workspace_dir), dict(earlier, attached=True))
        self.assertIn(f"Record {_EARLIER_DOI} is attached to https://earlier.test/math/11111111-2222-4333-8444-555555555555.",
                      output)

    def test_attach_problem_has_nothing_to_attach_in_the_other_settled_states(self) -> None:
        for journal, reason in (
                (_ATTACHED_JOURNAL, "is already attached."),
                (_SANDBOX_JOURNAL, "is a sandbox record, and only a published production record is attached."),
                (_RELEASED_JOURNAL, "published no record, so there is nothing to attach.")):
            with self.subTest(reason=reason):
                self._record_journal(journal)

                output = _run_draft_command(["attach-problem"], None, self)

                self.assertIn(f"The recorded deposit for problem {_PROBLEM_ID} {reason} Nothing was changed.", output)
                self.assertEqual(_read_journal(self.workspace_dir), journal)
        self.assertEqual(self.exactory.calls, [])
        self.assertEqual(self.fake_api.requests, [])

    def test_a_sandbox_publish_settles_the_journal_and_attaches_nothing(self) -> None:
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])

        self.assertIn("Only a published production record is attached to a problem. This sandbox record stays"
                      " unattached.", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"], journal["environment"]),
                         (True, False, "10.5281/zenodo.4242", "sandbox"))
        self.assertEqual(self.exactory.links(), [])

    def test_a_mark_of_the_attachment_that_cannot_be_written_leaves_a_settled_journal_and_a_retry_attaches_again(self) -> None:
        refuse = _refuse_the_journal_write_when(_draft.json_projection, lambda value: value["attached"])
        with unittest.mock.patch.object(_draft, "json_projection", refuse):
            output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"Record 10.5281/zenodo.4242 is attached, and the command cannot mark its recovery record"
                      " attached. Run exactory-draft attach-problem. It attaches the record again, which changes"
                      " nothing.", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"]), (True, False))
        self.assertEqual(len(self.exactory.links()), 1)

        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(len(self.exactory.links()), 2)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])
        self.assertEqual(_count_publishes(self.fake_api), 1)

    def test_a_mark_of_the_settled_state_that_cannot_be_written_leaves_a_journal_that_attach_problem_settles(self) -> None:
        refuse = _refuse_the_journal_write_when(_draft.json_projection, lambda value: value["settled"])
        with unittest.mock.patch.object(_draft, "json_projection", refuse):
            output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        # Published and recorded locally, attachment skipped, and the journal still says the publication is uncertain.
        self.assertIn("Record 10.5281/zenodo.4242 is published and recorded, and the command cannot mark its"
                      " recovery record settled. Run exactory-draft attach-problem. It settles the recorded deposit and"
                      " publishes nothing.", output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", output)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertEqual(self.exactory.links(), [])
        publishes = _count_publishes(self.fake_api)
        self._answer_with(_PublishedRecordZenodoApi(b"%PDF-1.4 fake paper"))

        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(publishes, 1)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, True, "10.5281/zenodo.4242"))
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_publish_answer_without_a_doi_leaves_an_unsettled_journal_that_attach_problem_settles(self) -> None:
        self._answer_with(_PublishWithoutDoiZenodoApi())

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("Deposition 4242 is published, but Zenodo answered without a DOI. Open"
                      " https://zenodo.org/api/deposit/4242 and read its state. Then run exactory-draft"
                      " attach-problem. It reads the deposition back and attaches the record.", output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", output)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.exactory.links(), [])
        self._answer_with(_PublishedRecordZenodoApi(b"%PDF-1.4 fake paper"))

        _run_draft_command(["attach-problem"], None, self)

        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, True, "10.5281/zenodo.4242"))
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_deposition_that_reads_back_published_without_a_doi_leaves_the_journal_unsettled(self) -> None:
        self._record_journal(_OTHER_JOURNAL)
        self._answer_with(_PublishedWithoutDoiZenodoApi())

        output = _run_draft_command(["attach-problem"], 1, self)

        self.assertIn("Deposition 4000 answers without a DOI. Open https://zenodo.org/api/deposit/4000 and read its"
                      " state. Then run the command again. It reads the deposition back and publishes no second"
                      " record.", output)
        self.assertIn(f"{_REMOVAL_RULE} deposition 4000 (https://zenodo.org/api/deposit/4000) is not published, or"
                      " decide not to publish it. The command never removes it.", output)
        self.assertEqual(_read_journal(self.workspace_dir), _OTHER_JOURNAL)
        self.assertEqual(self.exactory.links(), [])

    def test_a_publish_that_zenodo_refuses_leaves_an_unsettled_journal_and_says_when_removing_it_is_safe(self) -> None:
        self._answer_with(_StatusErrorZenodoApi("/actions/publish", 400))
        _, stderr_text = self._deposit_on_split_streams(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("The Zenodo API returned HTTP 400.", stderr_text)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. The next deposit or"
                      " attach-problem run reads the deposition back from Zenodo and settles it.", stderr_text)
        self.assertIn(f"{_REMOVAL_RULE} deposition 4242 (https://zenodo.org/api/deposit/4242) is not published, or"
                      " decide not to publish it. The command never removes it.", stderr_text)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        uploaded = _read_request(self.fake_api.requests, "PUT", "/files/bucket-1/paper.pdf").data

        # The user fixed the cause. The rerun reads the deposition back and publishes that same one.
        self._answer_with(_UnpublishedRecordZenodoApi(uploaded))
        self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242"),
                                            ("POST", "https://zenodo.org/api/deposit/depositions/4242/actions/publish")])
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

    def test_a_store_that_refuses_the_record_after_the_publish_leaves_an_unsettled_journal_that_attach_problem_settles(self) -> None:
        # The crash point: published, record refused, attachment skipped.
        with unittest.mock.patch.object(_draft, "record_direct_deposit", _refuse_the_direct_deposit_record):
            output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertIn("The record is published: ", output)
        self.assertEqual(output.count("Managed record skipped (store_busy)"), 1)
        self.assertIn(".exactory/deposit.json holds the only local copy of it", output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", output)
        self.assertIn(f"{_REMOVAL_RULE} deposition 4242 (https://zenodo.org/api/deposit/4242) is not published, or"
                      " decide not to publish it. The command never removes it.", output)
        self.assertEqual(self.exactory.links(), [])
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertNotIn("deposit", Store(self.workspace_dir).snapshot()["records"].get("workspace", {}))

        # The store takes records again. attach-problem reads the deposition back, records it, and attaches it.
        self._answer_with(_PublishedRecordZenodoApi(b"%PDF-1.4 fake paper"))
        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(Store(self.workspace_dir).snapshot()["records"]["workspace"]["deposit"]["doi"],
                         "10.5281/zenodo.4242")
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"]), (True, True))
        self.assertEqual(len(self.exactory.links()), 1)

    def test_the_record_exists_at_publish_time_for_a_deposit_that_names_no_problem(self) -> None:
        # Every publishing direct deposit keeps its local record before the publish call (design §26.11).
        deposit_path = self.workspace_dir / ".exactory" / "deposit.json"
        self._answer_with(_StateAtPublishZenodoApi([deposit_path]))

        self._deposit(_PRODUCTION_PUBLISH)

        state = self.fake_api.files_at_publish["deposit.json"]
        self.assertEqual((state["deposition_id"], "doi" in state), (4242, False))
        # The store holds the record too, so a projection export keeps it.
        self._answer_with(_StoreAtPublishZenodoApi(self.workspace_dir))
        self._deposit(_PRODUCTION_PUBLISH)
        self.assertEqual(self.fake_api.deposit_at_publish,
                         {"environment": "production", "deposition_id": 4242,
                          "draft_url": "https://zenodo.org/api/deposit/4242"})
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        self.assertFalse((self.workspace_dir / ".exactory" / "problem-deposit.json").exists())

    def test_a_store_that_refuses_the_record_is_reported_once_on_the_publishing_path(self) -> None:
        with unittest.mock.patch.object(_draft, "record_direct_deposit", _refuse_the_direct_deposit_record):
            output = self._deposit(_PRODUCTION_PUBLISH, expected_exit_code=1)

        self.assertEqual(output.count("Managed record skipped (store_busy)"), 1)
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")

    def test_new_version_refuses_a_prior_deposit_whose_publish_was_refused_and_names_it(self) -> None:
        self._answer_with(_StatusErrorZenodoApi("/actions/publish", 400))
        self._deposit(_PRODUCTION_PUBLISH, expected_exit_code=1)
        # The refused publish left the deposition in the record of the workspace, with no DOI.
        self.assertNotIn("doi", self.read_deposit_state())
        self._answer_with(_FakeZenodoApi())

        output = self._deposit(["--production", "--new-version", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)

        self.assertIn("publication_prior_required", output)
        self.assertIn("The prior deposit, deposition 4242 (https://zenodo.org/api/deposit/4242), has no DOI, so it"
                      " is not a published record.", output)
        self.assertEqual(self.fake_api.requests, [])


class TestExplainUnsettledJournal(_PlainWorkspaceDepositTestCase):
    """The note that every exit of a command carries while its journal is unsettled."""

    def _record_journal(self, journal: dict) -> None:
        (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text(json.dumps(journal), encoding="utf-8")

    def _note_for(self, journal: dict | None) -> str:
        return _draft._build_unsettled_journal_note(journal)

    def test_a_library_error_keeps_its_code_and_details_and_gains_a_sentence_with_the_note(self) -> None:
        self._record_journal(_OTHER_JOURNAL)
        note = self._note_for(_OTHER_JOURNAL)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", note)

        for message, expected in (("The manuscript changed", f"The manuscript changed. {note}"),
                                  ("The manuscript changed.", f"The manuscript changed. {note}")):
            with self.subTest(message=message), self.assertRaises(ResearchError) as stopped:
                with _draft._explain_unsettled_journal(_OTHER_JOURNAL):
                    raise ResearchError("readiness_required", message, {"obligations": []})
            self.assertEqual((stopped.exception.code, stopped.exception.message, stopped.exception.details),
                             ("readiness_required", expected, {"obligations": []}))

    def test_an_exit_passes_through_with_the_note_on_stderr(self) -> None:
        self._record_journal(_OTHER_JOURNAL)
        note = self._note_for(_OTHER_JOURNAL)
        sink = io.StringIO()

        with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
            with _draft._explain_unsettled_journal(_OTHER_JOURNAL):
                _draft._exit_with_error("The Zenodo API returned HTTP 400.", 1)

        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(sink.getvalue(), f"The Zenodo API returned HTTP 400.\n{note}\n")

    def test_the_note_is_empty_when_the_file_does_not_record_this_operation_as_unsettled(self) -> None:
        self.assertEqual(self._note_for(None), "")
        self.assertEqual(self._note_for(_OTHER_JOURNAL), "")
        for recorded in (dict(_OTHER_JOURNAL, operation_id="1" * 32), _UNATTACHED_JOURNAL, _ATTACHED_JOURNAL):
            with self.subTest(recorded=recorded["operation_id"], settled=recorded["settled"]):
                self._record_journal(recorded)
                self.assertEqual(self._note_for(_OTHER_JOURNAL), "")
        self._record_journal(_OTHER_JOURNAL)
        (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self._note_for(_OTHER_JOURNAL), "")

    def test_a_deposit_that_names_no_problem_has_no_journal_and_nothing_changes(self) -> None:
        with self.assertRaises(ResearchError) as stopped:
            with _draft._explain_unsettled_journal(None):
                raise ResearchError("readiness_required", "The manuscript changed")

        self.assertEqual(stopped.exception.message, "The manuscript changed")


class TestPublishedPriorRule(unittest.TestCase):
    """The prior concrete record that a new version revises (research_harness.zenodo)."""

    def test_a_published_prior_in_the_same_environment_is_accepted(self) -> None:
        prior = {"environment": "production", "deposition_id": 4242, "draft_url": "https://zenodo.org/deposit/4242",
                 "doi": "10.5281/zenodo.4242"}

        self.assertTrue(has_published_record(prior))
        self.assertIsNone(require_published_prior(prior, "production"))

    def test_a_prior_without_a_doi_is_refused_with_its_deposition_named(self) -> None:
        prior = {"environment": "production", "deposition_id": 4242, "draft_url": "https://zenodo.org/deposit/4242"}

        self.assertFalse(has_published_record(prior))
        with self.assertRaises(ResearchError) as refused:
            require_published_prior(prior, "production")
        self.assertEqual(refused.exception.code, "publication_prior_required")
        self.assertIn("deposition 4242 (https://zenodo.org/deposit/4242), has no DOI", refused.exception.message)
        self.assertEqual(refused.exception.details, {"deposition_id": 4242})

    def test_no_prior_and_a_prior_of_another_environment_are_refused(self) -> None:
        sandbox_prior = {"environment": "sandbox", "deposition_id": 4242, "doi": "10.5072/zenodo.4242"}

        for prior in (None, sandbox_prior):
            with self.subTest(prior=prior), self.assertRaises(ResearchError) as refused:
                require_published_prior(prior, "production")
            self.assertEqual(refused.exception.code, "publication_prior_required")
            self.assertIn("in the same environment", refused.exception.message)


class TestSharedExactoryApi(unittest.TestCase):
    """The origin rule, the id pattern, and the ORCID message have one home that bin/exactory and
    bin/exactory-draft both import (research_harness.exactory_api)."""

    def test_both_commands_import_the_shared_origin_rule_and_the_shared_orcid_message(self) -> None:
        exactory = _load_bin_module("exactory", "exactory_for_the_shared_rules")

        self.assertIs(exactory.read_api_base_url, read_api_base_url)
        self.assertIs(_draft.read_api_base_url, read_api_base_url)
        self.assertIs(exactory.build_orcid_required_msg, build_orcid_required_msg)
        self.assertIs(_draft.build_orcid_required_msg, build_orcid_required_msg)
        self.assertIs(_draft.UUID_RE, UUID_RE)

    def test_the_origin_defaults_to_the_application_and_loses_trailing_slashes(self) -> None:
        for configured, origin in ((None, "https://www.exactory.ai"), ("https://api.test", "https://api.test"),
                                   ("https://api.test/", "https://api.test"), ("https://api.test//", "https://api.test")):
            with self.subTest(configured=configured):
                environment = {} if configured is None else {"EXACTORY_API_URL": configured}
                with unittest.mock.patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(read_api_base_url(), origin)

    def test_the_id_pattern_accepts_an_id_in_either_case_and_nothing_else(self) -> None:
        self.assertTrue(UUID_RE.fullmatch(_PROBLEM_ID))
        self.assertTrue(UUID_RE.fullmatch(_PROBLEM_ID.upper()))
        for text in ("", " " + _PROBLEM_ID, _PROBLEM_ID + "\n", _PROBLEM_ID[:-1], "not-an-id"):
            with self.subTest(text=text):
                self.assertIsNone(UUID_RE.fullmatch(text))

    def test_the_orcid_message_names_the_page_that_links_an_id(self) -> None:
        self.assertEqual(build_orcid_required_msg("This operation", "https://api.test"),
                         "This operation needs an ORCID iD linked to your exactory account."
                         " Link one at https://api.test/account. Then run the command again.")


if __name__ == "__main__":
    unittest.main()
