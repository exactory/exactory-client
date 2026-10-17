"""Tests for deposits for a math problem: exactory-draft deposit --math-problem, its
recovery journal, and attach-problem (design 2026-10-12 §17)."""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import socket
import subprocess
import sys
import threading
import unittest
import unittest.mock
import urllib.request
from pathlib import Path

from integration_fixtures import build_unconfirmed_write_msg
from research_harness.errors import ResearchError
from research_harness.exactory_api import UUID_RE, build_orcid_required_msg, read_api_base_url
from research_harness.integration import export_workspace, record_direct_deposit
from research_harness.remote import begin_intent
from research_harness.same_origin import (RefusedRedirectError, SameOriginRedirectHandler, get_origin,
                                          open_url_within_origin)
from research_harness.storage import Store
from research_harness.zenodo import has_published_record, require_published_prior
from test_draft import (
    _DepositTestCase,
    _FailingRequestZenodoApi,
    _FakeZenodoApi,
    _LostResponseZenodoApi,
    _PlainWorkspaceDepositTestCase,
    _PublishedRecordZenodoApi,
    _StatusErrorZenodoApi,
    _UnpublishedRecordZenodoApi,
    _build_uncertain_creation_advice,
    _draft,
    _load_bin_module,
    _read_request,
    _refuse_the_direct_deposit_record,
    _run_draft_command,
    _run_draft_command_on_split_streams,
)

# The deposit test cases replace _open_url in their setUp. This is the function of the command, for the tests that go
# through the real transport.
_OPEN_URL_OF_THE_COMMAND = _draft._open_url
_PROBLEM_ID = "656c336e-e892-4c47-80d2-a71d022f4116"
_OTHER_PROBLEM_ID = "11111111-2222-4333-8444-555555555555"
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
_SANDBOX_JOURNAL = dict(_UNATTACHED_JOURNAL, environment="sandbox", base_url="https://sandbox.zenodo.org/api",
                        doi="10.5072/zenodo.4000")
# Settled with no DOI: the deposit never published a record.
_RELEASED_JOURNAL = dict(_OTHER_JOURNAL, settled=True)
_JOURNAL_PATH_TEXT = ".exactory/problem-deposit.json"
# The sentence that every message leaving an unsettled journal ends with.
_REMOVAL_RULE_MSG = f"Remove {_JOURNAL_PATH_TEXT} only after you check on Zenodo that"
# The instruction that a refusal gives for a record that waits for its attachment, and how that refusal ends
# when it sent nothing.
_ATTACH_INSTRUCTION_MSG = "Run exactory-draft attach-problem to finish that attachment."
_NOTHING_SENT_CLOSING_MSG = "Nothing was sent, and the journal stays as it is."


def _build_settled_removal_rule(doi: str) -> str:
    """What a message says when the settled journal that it leaves holds a published record: only
    the attachment is given up by removing the file."""
    return (f"If you decide not to attach the record, remove {_JOURNAL_PATH_TEXT}. Removing it is safe,"
            f" because the record is published as {doi}. The command never removes it.")


def _build_pending_refusal(doi: str, recorded_problem_id: str, recorded_origin: str, named_problem_id: str,
                           attach_instruction: str, closing_msg: str) -> str:
    """The refusal of a deposit for another problem while a record waits for its attachment."""
    return (f"{_JOURNAL_PATH_TEXT} holds a published record that is not attached yet. The record {doi} belongs to"
            f" problem {recorded_problem_id} at the exactory origin {recorded_origin}. This deposit names the problem"
            f" {named_problem_id}. {attach_instruction} {_build_settled_removal_rule(doi)} {closing_msg}")


# The three commands that read the journal, each with the sentence that ends its refusal of a file it cannot trust.
_JOURNAL_READING_COMMANDS = (
    (["deposit", "--abstract-file", "draft/abstract.txt", *_PROBLEM_DEPOSIT], "Nothing was deposited."),
    (["attach-problem"], "Nothing was attached."),
    (["reconcile", "no-such-intent"], "Nothing was reconciled."))


def _build_intent_binding(**changes) -> dict:
    """The binding of a managed deposit intent for the problem of `_PROBLEM_TARGET` in production,
    with `changes` applied."""
    binding = {"bundle_digest": "0" * 64, "prepared_revision": 1, "base_url": "https://zenodo.org/api",
               "environment": "production", "new_version": False, "publish": True, "uploads": [], "prior": None,
               "metadata": {"related_identifiers": [{"identifier": f"https://api.test/math/{_PROBLEM_ID}",
                                                     "relation": "references"}]}}
    return dict(binding, **changes)


def _build_managed_journal(operation_id: str, **changes) -> dict:
    """An unsettled journal of a managed deposit for the problem of `_PROBLEM_TARGET` in production."""
    journal = {"operation_id": operation_id, "path": "managed", "environment": "production",
               "base_url": "https://zenodo.org/api", "math_problem": _PROBLEM_TARGET, "settled": False,
               "attached": False}
    return dict(journal, **changes)


class _ExactoryRecorder:
    """Stands in for the exactory command: records each call with its origin and answers
    the way the API would."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.orcid_id: str | None = "0000-0002-1825-0097"
        self.failing_links = 0
        self.has_removed_links = False

    def __call__(self, argv: list[str], api_url: str) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), api_url))
        if argv[0] == "me":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"displayName": "Ada", "orcidId": self.orcid_id}), "")
        if argv[0] == "problem":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"id": argv[1], "removed": False}), "")
        if argv[0] == "link-problem-paper":
            if self.has_removed_links:
                return subprocess.CompletedProcess(argv, 3, "", "A moderator removed this link. Nothing changed.")
            if self.failing_links > 0:
                self.failing_links -= 1
                return subprocess.CompletedProcess(argv, 1, "", "The server rejected the API key.")
            return subprocess.CompletedProcess(argv, 0, json.dumps({"papers": []}), "")
        raise AssertionError(f"unexpected exactory call: {argv}")

    def links(self) -> list[tuple[list[str], str]]:
        return [(argv, url) for argv, url in self.calls if argv[0] == "link-problem-paper"]


class _SandboxZenodoApi(_FakeZenodoApi):
    """Answers like the fake, and issues the test DOIs of the sandbox (prefix 10.5072) for the
    requests that go to the sandbox address."""

    def __call__(self, request):
        answer = super().__call__(request)
        if request.full_url.startswith("https://sandbox.zenodo.org/") and str(answer.get("doi", "")).startswith("10.5281/"):
            answer = dict(answer, doi="10.5072/" + answer["doi"].split("/", 1)[1])
        return answer


class _PublishWithDoiZenodoApi(_FakeZenodoApi):
    """Answers like the fake, except that the deposition carries the named DOI once it is published: in the
    answer to the publish request, and in the read of the deposition."""

    def __init__(self, doi: str) -> None:
        super().__init__()
        self.doi = doi

    def __call__(self, request):
        if request.get_method() == "GET" and request.full_url.endswith("/deposit/depositions/4242"):
            self.requests.append(request)
            return {"id": 4242, "submitted": True, "doi": self.doi}
        answer = super().__call__(request)
        if request.get_method() == "POST" and request.full_url.endswith("/actions/publish"):
            return dict(answer, doi=self.doi)
        return answer


class _HostileBucketZenodoApi(_FakeZenodoApi):
    """Answers like the fake, except that the new deposition names a file bucket on another host."""

    def __call__(self, request):
        answer = super().__call__(request)
        if request.get_method() == "POST" and request.full_url.endswith("/deposit/depositions"):
            return dict(answer, links=dict(answer["links"], bucket="https://evil.test/api/files/bucket-1"))
        return answer


class _HostileDraftLinkZenodoApi(_FakeZenodoApi):
    """Answers like the fake, except that a new version names its draft on another host."""

    def __call__(self, request):
        answer = super().__call__(request)
        if request.get_method() == "POST" and request.full_url.endswith("/actions/newversion"):
            return {"links": {"latest_draft": "https://evil.test/api/deposit/depositions/4343"}}
        return answer


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


class _PendingAttachmentTests:
    """What a deposit for a problem does while its settled journal holds a published record that is
    not attached yet (design 2026-10-12 §17). The managed and the direct path share the rule, so both
    deposit test cases mix these tests in. The host class is a deposit test case with `self.exactory`
    and `self.fake_api`, and it says how to change the draft."""

    # What the success message says about publishing another record. The host class sets it.
    _NEXT_DEPOSIT_ADVICE_MSG = ""

    def _change_the_draft(self) -> None:
        raise NotImplementedError

    def _read_the_waiting_journal(self) -> dict:
        """The journal of a record that is published, recorded, and not attached yet."""
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]),
                         (True, False, "10.5281/zenodo.4242"))
        self.assertEqual(_count_publishes(self.fake_api), 1)
        return journal

    def _assert_the_rerun_only_attached(self, output: str, journal: dict, requests_before_the_rerun: int) -> None:
        self.assertIn(f"The record {journal['doi']} of the earlier deposit for this problem is attached now. This run"
                      f" published nothing new. {self._NEXT_DEPOSIT_ADVICE_MSG}", output)
        self.assertEqual(len(self.fake_api.requests), requests_before_the_rerun)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(_read_journal(self.workspace_dir), dict(journal, attached=True))
        # The rerun reads nothing from exactory and links the recorded record again, which changes nothing.
        self.assertEqual([argv[0] for argv, _ in self.exactory.calls],
                         ["me", "problem", "link-problem-paper", "link-problem-paper"])

    def test_an_identical_rerun_attaches_the_recorded_record_and_publishes_nothing_new(self) -> None:
        self.exactory.failing_links = 1
        failed_run_output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        # The failed run says how to finish, and when removing the journal is safe.
        self.assertIn(f"Record 10.5281/zenodo.4242 is published. Attaching it to problem {_PROBLEM_ID} failed:",
                      failed_run_output)
        self.assertIn("Then run exactory-draft attach-problem. It attaches the recorded record and publishes nothing."
                      f" {_build_settled_removal_rule('10.5281/zenodo.4242')}", failed_run_output)
        journal = self._read_the_waiting_journal()
        requests_before_the_rerun = len(self.fake_api.requests)

        output = self._deposit(_PROBLEM_DEPOSIT)

        self._assert_the_rerun_only_attached(output, journal, requests_before_the_rerun)

    def test_a_rerun_with_a_changed_draft_for_the_same_problem_attaches_the_recorded_record_and_publishes_nothing_new(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        journal = self._read_the_waiting_journal()
        requests_before_the_rerun = len(self.fake_api.requests)
        # Without the journal, the changed draft starts another operation, which publishes a second record.
        self._change_the_draft()

        output = self._deposit(_PROBLEM_DEPOSIT)

        self._assert_the_rerun_only_attached(output, journal, requests_before_the_rerun)

    def test_an_interruption_right_after_the_settled_mark_leaves_a_record_that_the_next_run_attaches(self) -> None:
        recorder = self.exactory

        def interrupt_the_attachment(argv: list[str], api_url: str) -> subprocess.CompletedProcess:
            if argv[0] != "link-problem-paper":
                return recorder(argv, api_url)
            recorder.calls.append((list(argv), api_url))
            raise KeyboardInterrupt

        _draft._call_exactory = interrupt_the_attachment
        with self.assertRaises(KeyboardInterrupt):
            self._deposit(_PROBLEM_DEPOSIT)
        _draft._call_exactory = recorder
        journal = self._read_the_waiting_journal()
        requests_before_the_rerun = len(self.fake_api.requests)

        output = self._deposit(_PROBLEM_DEPOSIT)

        self._assert_the_rerun_only_attached(output, journal, requests_before_the_rerun)

    def test_a_rerun_whose_attachment_fails_again_keeps_the_journal_and_publishes_nothing_new(self) -> None:
        self.exactory.failing_links = 2
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        journal = self._read_the_waiting_journal()
        requests_before_the_rerun = len(self.fake_api.requests)
        self._change_the_draft()

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        # The failure of a retry says that the record belongs to the earlier deposit and that this run published nothing.
        self.assertIn(f"Record {journal['doi']} belongs to the earlier deposit for this problem. This run published"
                      f" nothing. Attaching it to problem {_PROBLEM_ID} failed:", output)
        self.assertIn("Then run exactory-draft attach-problem. It attaches the recorded record and publishes nothing."
                      f" {_build_settled_removal_rule(journal['doi'])}", output)
        self.assertNotIn("is attached now", output)
        self.assertEqual(len(self.fake_api.requests), requests_before_the_rerun)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(_read_journal(self.workspace_dir), journal)
        self.assertEqual(len(self.exactory.links()), 2)

    def test_the_rerun_prints_the_attached_record_as_json_on_stdout(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        journal = self._read_the_waiting_journal()

        stdout_text, stderr_text = _run_draft_command_on_split_streams(
            ["deposit", "--abstract-file", "draft/abstract.txt", *_PROBLEM_DEPOSIT], None, self)

        # A caller that reads only stdout gets the record, as it does from a settle.
        self.assertEqual(json.loads(stdout_text), dict(journal, attached=True))
        self.assertIn("is attached now. This run published nothing new.", stderr_text)
        self.assertNotIn("is attached now", stdout_text)

    def test_a_deposit_for_another_problem_is_refused_before_any_request_while_the_attachment_waits(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        journal = self._read_the_waiting_journal()
        deposit_file = self.workspace_dir / ".exactory" / "deposit.json"
        deposit_bytes = deposit_file.read_bytes()
        requests_before, exactory_calls_before = len(self.fake_api.requests), list(self.exactory.calls)

        output = self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", _OTHER_PROBLEM_ID], expected_exit_code=1)

        self.assertIn(_build_pending_refusal("10.5281/zenodo.4242", _PROBLEM_ID, "https://api.test", _OTHER_PROBLEM_ID,
                                             _ATTACH_INSTRUCTION_MSG, _NOTHING_SENT_CLOSING_MSG), output)
        self.assertEqual(len(self.fake_api.requests), requests_before)
        self.assertEqual(self.exactory.calls, exactory_calls_before)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(_read_journal(self.workspace_dir), journal)
        self.assertEqual(deposit_file.read_bytes(), deposit_bytes)


class TestManagedDepositForAProblem(_PendingAttachmentTests, _DepositTestCase):
    # The same manuscript resumes the completed intent, so a new record needs another manuscript or a new version.
    _NEXT_DEPOSIT_ADVICE_MSG = ("To publish another record for this problem, change the manuscript or add --new-version."
                                " The same manuscript resumes the same completed deposit.")

    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def _change_the_draft(self) -> None:
        (self.workspace_dir / "draft" / "references.bib").unlink()

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
        self.assertIn(_build_settled_removal_rule("10.5281/zenodo.4242"), output)
        # The publication is certain and recorded, so only the attachment is open.
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]),
                         (True, False, "10.5281/zenodo.4242"))
        sent = len(self.fake_api.requests)

        # Another environment does not move the attachment: the recorded origin must be the origin of
        # the run, so the command stops before it sends anything, and the journal stays.
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        del os.environ["ZENODO_TOKEN"], os.environ["ZENODO_SANDBOX_TOKEN"]
        stopped_output = _run_draft_command(["attach-problem"], 1, self)
        self.assertIn("names the exactory origin https://api.test. This run uses the exactory origin"
                      " https://elsewhere.test.", stopped_output)
        # The advice for a record that is published: attach it from its own origin, or give the attachment up.
        self.assertIn("If https://api.test is your own exactory origin, run exactory-draft attach-problem with"
                      f" EXACTORY_API_URL set to https://api.test. {_build_settled_removal_rule('10.5281/zenodo.4242')}",
                      stopped_output)
        self.assertEqual(len(self.exactory.links()), 1)
        self.assertFalse(_read_journal(self.workspace_dir)["attached"])

        # On the recorded origin, the journal holds the record, so the retry reads no Zenodo token and
        # sends no Zenodo request.
        os.environ["EXACTORY_API_URL"] = "https://api.test"
        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(len(self.fake_api.requests), sent)
        self.assertEqual([url for _, url in self.exactory.links()], ["https://api.test", "https://api.test"])
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

    def test_a_lost_publish_response_is_read_back_by_the_same_command_and_then_attached(self) -> None:
        uploaded = self._lose_the_publish_response()
        self.assertEqual(self.exactory.links(), [])
        self._answer_with_the_published_record(uploaded)

        output = self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(self._requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(len(self.exactory.links()), 1)
        # The advice says what makes another record, because the same command resumes the completed deposit.
        self.assertIn(f"It deposited nothing new. {self._NEXT_DEPOSIT_ADVICE_MSG}", output)
        self.assertNotIn("to start a new deposit", output)
        requests_before_the_next_run = len(self.fake_api.requests)

        self._deposit(_PROBLEM_DEPOSIT)

        self.assertEqual(len(self.fake_api.requests), requests_before_the_next_run)
        self.assertEqual(_count_publishes(self.fake_api), 0)

    def test_a_removed_link_after_managed_read_back_names_the_earlier_deposit(self) -> None:
        self._answer_with_the_published_record(self._lose_the_publish_response())
        self.exactory.has_removed_links = True
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=3)
        self.assertIn("belongs to the earlier deposit for this problem. This run published nothing.", output)
        self.assertIn(f"A moderator removed the link of record 10.5281/zenodo.4242 from problem {_PROBLEM_ID}.", output)
        self.assertIn(_build_settled_removal_rule("10.5281/zenodo.4242"), output)
        self.assertEqual(self._requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"]), (True, False))

    def test_a_removed_link_after_managed_publication_does_not_claim_nothing_was_published(self) -> None:
        self._find_the_deposition_unpublished_with_attach_problem(self._lose_the_publish_response())
        self.exactory.has_removed_links = True
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=3)
        self.assertNotIn("This run published nothing", output)
        self.assertIn("The record stays published as 10.5281/zenodo.4242.", output)
        self.assertEqual(self._requested(), [("POST", "https://zenodo.org/api/deposit/depositions/4242/actions/publish")])
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"]), (True, False))

    def test_a_rerun_settles_the_recorded_intent_whatever_the_draft_says(self) -> None:
        uploaded = self._lose_the_publish_response()
        # The draft changes. Without the journal, the changed draft sends the rerun down the direct
        # path, which creates and publishes a second record.
        (self.workspace_dir / "draft" / "references.bib").unlink()
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
        # The draft changes. A released journal would let this run select another operation: the
        # changed draft takes the direct path, which creates and publishes a second record.
        (self.workspace_dir / "draft" / "references.bib").unlink()

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        # The recorded intent still binds the run, and its manuscript check stops the publish. The
        # message says that the journal stays, which deposition it names, and when removing it is safe.
        self.assertIn("readiness_required", output)
        self.assertIn(f"must match the saved publication intent. {_JOURNAL_PATH_TEXT} stays as the recovery record of"
                      " this deposit", output)
        self.assertIn(f"{_REMOVAL_RULE_MSG} the deposition whose notes read", output)
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
        # attach-problem never publishes, so it leaves the unclaimed deposit as it is and names the command to run.
        # The note that follows says what that run does: the intent never published, so it releases the journal.
        stopped_output = _run_draft_command(["attach-problem"], 1, self)
        self.assertIn("The recorded deposit never sent its publish request, so there is no record to attach."
                      " Run exactory-draft deposit to deposit the paper.", stopped_output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. The next deposit run"
                      " releases it and deposits as usual.", stopped_output)
        self.assertNotIn("to finish it", stopped_output)
        self.assertNotIn("reads the deposition back", stopped_output)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.fake_api.requests, [])
        # A release that cannot be written leaves the journal as it is, and the message says so.
        refuse = _refuse_the_journal_write_when(_draft.json_projection, lambda value: value["settled"])
        with unittest.mock.patch.object(_draft, "json_projection", refuse):
            refused_output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertIn("The recovery record could not be released. Nothing was deposited.", refused_output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. The next deposit run"
                      " releases it and deposits as usual.", refused_output)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(self.fake_api.requests, [])
        # Another problem makes another deposit, so the released run selects another operation. A
        # reconcile that published the recorded intent while this run released its journal left two
        # published records.
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
            self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", _OTHER_PROBLEM_ID])

        # The release marks the unclaimed operation settled with no DOI: it never published a record.
        self.assertEqual(journal_writes[0], {"operation_id": intent_id, "path": "managed", "environment": "production",
                                             "base_url": "https://zenodo.org/api", "math_problem": _PROBLEM_TARGET,
                                             "settled": True, "attached": False})
        self.assertIn("remote_operation_owned", reconcile_outputs[0])
        self.assertEqual(_count_publishes(self.fake_api), 1)
        records = Store(self.workspace_dir).snapshot()["records"]
        self.assertNotEqual(records["remote_intent"][intent_id]["status"], "complete")
        self.assertNotIn(intent_id, records["publication_receipt"])
        self.assertEqual(self.exactory.links(), [(["link-problem-paper", _OTHER_PROBLEM_ID, "--identifier",
                                                   "10.5281/zenodo.4242"], "https://api.test")])

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
        # The sandbox issues test DOIs with the prefix 10.5072 (https://developers.zenodo.org).
        self.fake_api = _SandboxZenodoApi()
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])
        metadata = json.loads(_read_request(self.fake_api.requests, "PUT", "/deposit/depositions/4242").data)["metadata"]
        self.assertEqual(metadata["related_identifiers"][0]["relation"], "references")
        self.assertEqual(self.exactory.links(), [])
        self.assertIn("Only a published production record is attached to a problem. This sandbox record stays"
                      " unattached.", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, False, "10.5072/zenodo.4242"))

    def test_an_unlinked_orcid_stops_before_any_zenodo_request(self) -> None:
        self.exactory.orcid_id = None
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.assertEqual(self.fake_api.requests, [])
        self.assertIn("https://api.test/account", output)

    def test_a_problem_id_that_is_not_an_id_is_refused_before_any_request(self) -> None:
        self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", "not-an-id"], expected_exit_code=2)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_new_version_refuses_a_fresh_draft_that_has_no_concept_doi(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        prior = json.loads((self.workspace_dir / ".exactory/deposit.json").read_text())
        self.fake_api.requests.clear()

        output = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)

        # The managed check refuses a prior with no DOI; the direct path then reads that prior back.
        self.assertIn("Managed record skipped (publication_prior_required): The prior deposit, deposition 4242,"
                      " has no recorded DOI. Open https://sandbox.zenodo.org/api/deposit/4242 and check its state.",
                      output)
        self.assertNotIn("not a published record", output)
        self.assertIn("fresh draft, not a version of the workspace's published record", output)
        self.assertIn("Delete button", output)
        self.assertEqual((self.fake_api.requests[0].get_method(), self.fake_api.requests[0].full_url),
                         ("GET", "https://sandbox.zenodo.org/api/deposit/depositions/4242"))
        self.assertEqual(len(self.fake_api.requests), 1)
        self.assertEqual(json.loads((self.workspace_dir / ".exactory/deposit.json").read_text()), prior)


    def test_after_the_retry_the_same_manuscript_resumes_the_same_completed_deposit(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self._deposit(_PROBLEM_DEPOSIT)
        requests_before = len(self.fake_api.requests)

        self._deposit(_PROBLEM_DEPOSIT)

        # The advice says so: the same manuscript finds its completed intent, and Zenodo gets nothing.
        self.assertEqual(len(self.fake_api.requests), requests_before)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, True, "10.5281/zenodo.4242"))

    def test_a_removed_link_on_a_completed_managed_deposit_names_the_earlier_publication(self) -> None:
        self._deposit(_PROBLEM_DEPOSIT)
        requests_before = len(self.fake_api.requests)
        self.exactory.has_removed_links = True

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=3)

        self.assertIn("belongs to the earlier deposit for this problem. This run published nothing.", output)
        self.assertIn(f"A moderator removed the link of record 10.5281/zenodo.4242 from problem {_PROBLEM_ID}.", output)
        self.assertIn(_build_settled_removal_rule("10.5281/zenodo.4242"), output)
        self.assertEqual(len(self.fake_api.requests), requests_before)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"]), (True, False))

    def test_after_the_retry_a_new_version_publishes_another_record(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self._deposit(_PROBLEM_DEPOSIT)

        self._deposit([*_PROBLEM_DEPOSIT, "--new-version"])

        self.assertIn(("POST", "https://zenodo.org/api/deposit/depositions/4242/actions/newversion"), self._requested())
        self.assertEqual(_count_publishes(self.fake_api), 2)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, True, "10.5281/zenodo.4343"))
        self.assertEqual(len(self.exactory.links()), 3)

    def test_a_reconcile_error_on_the_intent_of_the_journal_carries_the_note_and_the_removal_rule(self) -> None:
        intent_id = self._stop_before_the_publish_claim()
        # The draft changes, so the manuscript check of the intent stops the reconcile.
        (self.workspace_dir / "draft" / "references.bib").unlink()

        output = _run_draft_command(["reconcile", intent_id], 1, self)

        self.assertIn("readiness_required", output)
        # The intent never sent its publish request, so the next deposit run releases the journal.
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. The next deposit run"
                      " releases it and deposits as usual.", output)
        self.assertIn(f"{_REMOVAL_RULE_MSG} the deposition whose notes read", output)
        self.assertIn(f"Exactory publication intent {intent_id}", output)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])
        self.assertEqual(_count_publishes(self.fake_api), 0)

    def test_a_record_of_another_environment_is_neither_settled_nor_attached(self) -> None:
        # Zenodo issues the test DOIs of the sandbox with the prefix 10.5072 (https://developers.zenodo.org).
        self.fake_api = _PublishWithDoiZenodoApi("10.5072/zenodo.4242")
        _draft._open_url = self.fake_api

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        # The receipt proves the publication, so the refusal gives the way out for a published record.
        self.assertIn(f"{_JOURNAL_PATH_TEXT} is not a journal this command can trust: the DOI 10.5072/zenodo.4242 does"
                      " not start with 10.5281/, the DOI prefix of the production environment. Nothing was attached."
                      f" {_build_settled_removal_rule('10.5072/zenodo.4242')}", output)
        self.assertNotIn("stays as the recovery record", output)
        self.assertEqual(self.exactory.links(), [])
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])

    def test_a_file_bucket_on_another_host_gets_neither_the_paper_nor_the_token(self) -> None:
        self.fake_api = _HostileBucketZenodoApi()
        _draft._open_url = self.fake_api

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("The address https://evil.test/api/files/bucket-1/paper.pdf is not under https://zenodo.org/api,"
                      " the Zenodo API of the production environment. The command sent nothing to it, and the token"
                      " stayed here.", output)
        self.assertEqual(self._requested(), [("POST", "https://zenodo.org/api/deposit/depositions")])
        self.assertNotIn("production-token", output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", output)
        self.assertFalse(_read_journal(self.workspace_dir)["settled"])


class TestDirectDepositForAProblem(_PendingAttachmentTests, _PlainWorkspaceDepositTestCase):
    _NEXT_DEPOSIT_ADVICE_MSG = "To publish another record for this problem, run the command again."

    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def _change_the_draft(self) -> None:
        (self.workspace_dir / "draft" / "paper.pdf").write_bytes(b"%PDF-1.4 a changed paper")
        (self.workspace_dir / "draft" / "abstract.txt").write_text("A changed abstract.\n")

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
        self.assertIn(f"{_REMOVAL_RULE_MSG} deposition 4242 (https://zenodo.org/api/deposit/4242) is not published,"
                      " or decide not to publish it. The command never removes it.", stderr_text)
        return _read_request(self.fake_api.requests, "PUT", "/files/bucket-1/paper.pdf").data

    def _answer_with(self, fake_api) -> None:
        self.fake_api = fake_api
        _draft._open_url = fake_api

    def test_a_failure_before_the_journal_names_the_draft_and_its_deletion_before_retry(self) -> None:
        # Before the publish request the deposit keeps no journal, so the draft is deleted before a second run.
        self._answer_with(_FailingRequestZenodoApi("PUT", "/files/bucket-1/paper.pdf", lambda request: OSError("reset")))

        _, stderr_text = self._deposit_on_split_streams(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertTrue(stderr_text.endswith(
            "Deposition 4242 of this run stays a draft on Zenodo: https://zenodo.org/api/deposit/4242. A second run"
            " creates another draft. Before you run the command again, delete this draft with the Delete button on its"
            " page.\n"),
            stderr_text)
        self.assertFalse((self.workspace_dir / ".exactory" / "problem-deposit.json").exists())
        self.assertEqual(self.exactory.links(), [])

    def test_a_failure_before_the_journal_of_a_sandbox_deposit_names_no_attachment(self) -> None:
        # A sandbox draft is also deleted before a second run, with no attachment step.
        self._answer_with(_FailingRequestZenodoApi("PUT", "/files/bucket-1/paper.pdf", lambda request: OSError("reset")))

        _, stderr_text = self._deposit_on_split_streams(["--publish", "--creator", "Shiroshita, Ryosuke",
                                                         "--math-problem", _PROBLEM_ID], expected_exit_code=1)

        self.assertTrue(stderr_text.endswith(
            "Deposition 4242 of this run stays a draft on Zenodo: https://sandbox.zenodo.org/api/deposit/4242. A second"
            " run creates another draft. Before you run the command again, delete this draft with the Delete button on"
            " its page.\n"), stderr_text)

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
        # A direct deposit creates a record on every run, so the same command publishes another one.
        self.assertIn(f"It deposited nothing new. {self._NEXT_DEPOSIT_ADVICE_MSG}", output)

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
        # The failed run names attach-problem, and says when removing the journal is safe.
        self.assertIn("Resolve that cause. Then run exactory-draft attach-problem. It attaches the recorded record"
                      f" and publishes nothing. {_build_settled_removal_rule('10.5281/zenodo.4242')}", output)
        self.assertNotIn("the same deposit command", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]),
                         (True, False, "10.5281/zenodo.4242"))
        sent = len(self.fake_api.requests)
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

    def _replace_the_journal_when(self, url_suffix: str, journal: dict = _OTHER_JOURNAL) -> None:
        """Write the journal of another operation when the named request goes out: a writer
        outside the run, which the workspace lock does not stop."""
        journal_path = self.workspace_dir / ".exactory" / "problem-deposit.json"
        self._answer_with(_InterleavingZenodoApi(b"%PDF-1.4 fake paper", "POST", url_suffix,
                                                 lambda: journal_path.write_text(json.dumps(journal), encoding="utf-8")))

    def test_the_next_run_deposits_normally_once_the_attachment_is_done(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self._deposit(_PROBLEM_DEPOSIT)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])
        self.assertEqual(_count_publishes(self.fake_api), 1)

        output = self._deposit(_PROBLEM_DEPOSIT)

        # The journal is attached, so this run is a new deposit: it creates, publishes, and attaches another record.
        self.assertNotIn("is attached now", output)
        self.assertEqual(self.requested().count(("POST", "https://zenodo.org/api/deposit/depositions")), 2)
        self.assertEqual(_count_publishes(self.fake_api), 2)
        self.assertEqual(len(self.exactory.links()), 3)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"]), (True, True))

    def test_a_deposit_for_another_problem_goes_ahead_once_attach_problem_has_finished_the_attachment(self) -> None:
        self.exactory.failing_links = 1
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", _OTHER_PROBLEM_ID], expected_exit_code=1)
        self.assertEqual(_count_publishes(self.fake_api), 1)

        _run_draft_command(["attach-problem"], None, self)
        self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", _OTHER_PROBLEM_ID])

        self.assertEqual(_count_publishes(self.fake_api), 2)
        self.assertEqual([argv[1] for argv, _ in self.exactory.links()], [_PROBLEM_ID, _PROBLEM_ID, _OTHER_PROBLEM_ID])
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["math_problem"]["problem_id"], journal["attached"]), (_OTHER_PROBLEM_ID, True))

    def test_a_record_that_waits_for_its_attachment_and_appears_before_the_journal_write_stops_the_run(self) -> None:
        # A writer outside the run leaves a published record for another problem unattached while this
        # run creates its deposition. The run must not replace that journal with its own.
        waiting = dict(_UNATTACHED_JOURNAL, math_problem={"problem_id": _OTHER_PROBLEM_ID, "api_url": "https://api.test"})
        self._replace_the_journal_when("/deposit/depositions", waiting)

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(_build_pending_refusal(_EARLIER_DOI, _OTHER_PROBLEM_ID, "https://api.test", _PROBLEM_ID,
                                             _ATTACH_INSTRUCTION_MSG,
                                             "The journal stays as it is. The recovery record of deposition 4242 could"
                                             " not be written, so it was not published. It stays a draft on Zenodo:"
                                             " https://zenodo.org/api/deposit/4242."), output)
        self.assertEqual(_count_publishes(self.fake_api), 0)
        self.assertEqual(_read_journal(self.workspace_dir), waiting)
        self.assertEqual(self.exactory.links(), [])

    def test_the_journal_of_another_operation_stops_the_run_before_its_publish_call(self) -> None:
        self._replace_the_journal_when("/deposit/depositions")

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"records operation {_OTHER_JOURNAL['operation_id']}", output)
        self.assertIn("so it was not published", output)
        # That journal is unsettled, so the message says when removing it is safe.
        self.assertIn(f"{_REMOVAL_RULE_MSG} deposition 4000 (https://zenodo.org/api/deposit/4000) is not published,"
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


class TestRemovedProblemLinks(_PlainWorkspaceDepositTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)
        attach = _draft._attach_recorded_record

        def attach_without_zenodo(journal, **kwargs):
            with unittest.mock.patch.object(_draft, "_open_url", side_effect=AssertionError("unexpected Zenodo request")), \
                    unittest.mock.patch.object(_draft, "_read_zenodo_token", side_effect=AssertionError("unexpected token read")):
                return attach(journal, **kwargs)

        patcher = unittest.mock.patch.object(_draft, "_attach_recorded_record", side_effect=attach_without_zenodo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _record_journal(self, journal: dict) -> None:
        (self.workspace_dir / _JOURNAL_PATH_TEXT).write_text(json.dumps(journal), encoding="utf-8")

    def _assert_removed(self, output: str, doi: str, *, is_retry: bool) -> None:
        self.assertIn(f"A moderator removed the link of record {doi} from problem {_PROBLEM_ID}.", output)
        self.assertIn(f"The record stays published as {doi}.", output)
        self.assertIn(_build_settled_removal_rule(doi), output)
        self.assertNotIn("Resolve that cause", output)
        self.assertNotIn("It attaches the recorded record", output)
        if is_retry:
            self.assertIn("belongs to the earlier deposit for this problem. This run published nothing.", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, False, doi))

    def test_a_standing_link_marks_the_journal_attached_without_a_zenodo_request(self) -> None:
        self._record_journal(_UNATTACHED_JOURNAL)
        with unittest.mock.patch.object(_draft, "_open_url", side_effect=AssertionError("unexpected Zenodo request")):
            _run_draft_command(["attach-problem"], None, self)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_link_removed_before_the_first_attachment_keeps_the_published_record_unattached(self) -> None:
        self.exactory.has_removed_links = True
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=3)
        self._assert_removed(output, "10.5281/zenodo.4242", is_retry=False)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_lost_attachment_mark_then_removal_is_reported_by_attach_problem(self) -> None:
        refuse = _refuse_the_journal_write_when(_draft.json_projection, lambda value: value["attached"])
        with unittest.mock.patch.object(_draft, "json_projection", refuse):
            self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.exactory.has_removed_links = True
        with unittest.mock.patch.object(_draft, "_open_url", side_effect=AssertionError("unexpected Zenodo request")):
            output = _run_draft_command(["attach-problem"], 3, self)
        self._assert_removed(output, "10.5281/zenodo.4242", is_retry=True)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(len(self.exactory.links()), 2)

    def test_a_deposit_retry_preserves_the_removal_exit_without_publishing(self) -> None:
        self._record_journal(_UNATTACHED_JOURNAL)
        self.exactory.has_removed_links = True
        with unittest.mock.patch.object(_draft, "_open_url", side_effect=AssertionError("unexpected Zenodo request")):
            output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=3)
        self._assert_removed(output, _EARLIER_DOI, is_retry=True)

    def test_settling_a_prior_publish_reports_removal_without_another_publish(self) -> None:
        fake = _LostResponseZenodoApi("/actions/publish")
        self.fake_api = fake
        _draft._open_url = fake
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        self.exactory.has_removed_links = True
        uploaded = _read_request(fake.requests, "PUT", "/files/bucket-1/paper.pdf").data
        published = _PublishedRecordZenodoApi(uploaded)
        _draft._open_url = published
        output = _run_draft_command(["attach-problem"], 3, self)
        self._assert_removed(output, "10.5281/zenodo.4242", is_retry=True)
        self.assertEqual(_count_publishes(fake), 1)
        self.assertEqual([(request.get_method(), request.full_url) for request in published.requests],
                         [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(len(self.exactory.links()), 1)

    def test_a_deposit_that_reads_back_a_published_record_names_the_earlier_deposit_on_removal(self) -> None:
        fake = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = fake
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        uploaded = _read_request(fake.requests, "PUT", "/files/bucket-1/paper.pdf").data
        published = _PublishedRecordZenodoApi(uploaded)
        _draft._open_url = published
        self.exactory.has_removed_links = True
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=3)
        self._assert_removed(output, "10.5281/zenodo.4242", is_retry=True)
        self.assertEqual([(request.get_method(), request.full_url) for request in published.requests],
                         [("GET", "https://zenodo.org/api/deposit/depositions/4242")])

    def test_a_settlement_that_publishes_does_not_claim_this_run_published_nothing(self) -> None:
        fake = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = fake
        self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        uploaded = _read_request(fake.requests, "PUT", "/files/bucket-1/paper.pdf").data
        unpublished = _UnpublishedRecordZenodoApi(uploaded)
        _draft._open_url = unpublished
        self.exactory.has_removed_links = True
        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=3)
        self._assert_removed(output, "10.5281/zenodo.4242", is_retry=False)
        self.assertNotIn("This run published nothing", output)
        self.assertEqual([(request.get_method(), request.full_url) for request in unpublished.requests],
                         [("GET", "https://zenodo.org/api/deposit/depositions/4242"),
                          ("POST", "https://zenodo.org/api/deposit/depositions/4242/actions/publish")])


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
            ("a sandbox DOI under the production environment",
             json.dumps(dict(_UNATTACHED_JOURNAL, doi="10.5072/zenodo.4000")).encode(),
             'the key "doi" must start with 10.5281/, the DOI prefix of the production environment'),
            ("a production DOI under the sandbox environment",
             json.dumps(dict(_SANDBOX_JOURNAL, doi="10.5281/zenodo.4000")).encode(),
             'the key "doi" must start with 10.5072/, the DOI prefix of the sandbox environment'),
            ("a DOI that no Zenodo issues", json.dumps(dict(_UNATTACHED_JOURNAL, doi="10.1234/other")).encode(),
             'the key "doi" must start with 10.5281/, the DOI prefix of the production environment'),
            ("a key that appears twice", b'{"operation_id": "a", "operation_id": "b"}',
             "cannot be read. Cause: Expected finite JSON without duplicate fields."),
            ("nesting that is too deep", b"[" * 200000 + b"]" * 200000,
             "cannot be read. Cause: Expected finite JSON without duplicate fields."),
        )
        for label, data, complaint_msg in cases:
            path = self._write_journal_bytes(data)
            for argv, failure_msg in _JOURNAL_READING_COMMANDS:
                with self.subTest(journal=label, command=argv[0]):
                    output = _run_draft_command(argv, 1, self)
                    self.assertIn(f"{_JOURNAL_PATH_TEXT} ", output)
                    self.assertIn(complaint_msg, output)
                    # The file may hold an unsettled deposit, so the message says when removing it is safe.
                    self.assertIn(f"{_REMOVAL_RULE_MSG} the deposition it names is not published, or decide not to"
                                  " publish it. The command never removes it.", output)
                    self.assertTrue(output.rstrip().endswith(failure_msg), output)
                    self.assertEqual(path.read_bytes(), data)
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_a_journal_that_is_not_a_regular_file_is_refused_by_every_command_and_never_followed(self) -> None:
        path = self.workspace_dir / ".exactory" / "problem-deposit.json"
        elsewhere = self.workspace_dir / "elsewhere.json"
        elsewhere.write_text(json.dumps(_UNATTACHED_JOURNAL), encoding="utf-8")
        symlink_cause_msg = "Research paths must not contain symlinks or non-directories"
        regular_file_cause_msg = "Research content must be a regular file"
        # A symlink would send every command to a file outside the workspace. A named pipe would hold a
        # plain read until a writer came.
        for label, create, remove, cause_msg in (
                ("a symlink", lambda: path.symlink_to(elsewhere), path.unlink, symlink_cause_msg),
                ("a directory", path.mkdir, path.rmdir, regular_file_cause_msg),
                ("a named pipe", lambda: os.mkfifo(path), path.unlink, regular_file_cause_msg)):
            create()
            for argv, failure_msg in _JOURNAL_READING_COMMANDS:
                with self.subTest(file=label, command=argv[0]):
                    output = _run_draft_command(argv, 1, self)
                    self.assertIn(f"{_JOURNAL_PATH_TEXT} cannot be read. Cause: {cause_msg}.", output)
                    self.assertIn(f"{_REMOVAL_RULE_MSG} the deposition it names is not published, or decide not to publish"
                                  " it. The command never removes it.", output)
                    self.assertTrue(output.rstrip().endswith(failure_msg), output)
            remove()
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), json.dumps(_UNATTACHED_JOURNAL))
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])

    def test_the_help_of_math_problem_describes_the_retry_and_the_refusal(self) -> None:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), self.assertRaises(SystemExit) as stopped:
            _draft._build_parser().parse_args(["deposit", "--help"])

        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("While an earlier published record waits for its attachment, a publishing deposit for the same"
                      " problem attaches that record and publishes nothing new, and a publishing deposit for another"
                      " problem is refused. exactory-draft attach-problem attaches the waiting record alone.",
                      " ".join(sink.getvalue().split()))

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


class _PublishedForeignDepositionZenodoApi(_FakeZenodoApi):
    """Answers like the fake, except that deposition 4000 of the foreign journal reads back published."""

    def __call__(self, request):
        if request.get_method() == "GET" and request.full_url.endswith("/deposit/depositions/4000"):
            self.requests.append(request)
            return {"id": 4000, "submitted": True, "doi": "10.5072/zenodo.4000",
                    "links": {"record_html": "https://sandbox.zenodo.org/records/4000"}}
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

    def test_a_deposit_for_the_problem_of_a_waiting_record_attaches_it_and_publishes_nothing(self) -> None:
        # The retry reads no Zenodo token and no draft, so neither one is available to it.
        del os.environ["ZENODO_TOKEN"], os.environ["ZENODO_SANDBOX_TOKEN"]
        for label, problem_id in (("as recorded", _PROBLEM_ID), ("in capitals", _PROBLEM_ID.upper())):
            with self.subTest(problem_id=label):
                self._record_journal(_UNATTACHED_JOURNAL)
                self.exactory.calls.clear()

                output = self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", problem_id])

                self.assertIn(f"The record {_EARLIER_DOI} of the earlier deposit for this problem is attached now. This"
                              " run published nothing new. To publish another record for this problem, run the command"
                              " again.", output)
                self.assertEqual(self.exactory.calls, [(["link-problem-paper", _PROBLEM_ID, "--identifier", _EARLIER_DOI],
                                                        "https://api.test")])
                self.assertEqual(self.fake_api.requests, [])
                self.assertEqual(_read_journal(self.workspace_dir), _ATTACHED_JOURNAL)

    def test_a_deposit_for_another_problem_is_refused_before_any_request_while_a_record_waits_for_its_attachment(self) -> None:
        own_origin = dict(_UNATTACHED_JOURNAL)
        other_origin = dict(_UNATTACHED_JOURNAL, math_problem={"problem_id": _PROBLEM_ID, "api_url": "https://recorded.test"})
        other_origin_instruction_msg = ("If https://recorded.test is your own exactory origin, run exactory-draft"
                                        " attach-problem with EXACTORY_API_URL set to https://recorded.test.")
        for label, journal, instruction_msg in (("at the origin of the run", own_origin, _ATTACH_INSTRUCTION_MSG),
                                                ("at another origin", other_origin, other_origin_instruction_msg)):
            with self.subTest(journal=label):
                self._record_journal(journal)
                problem_target = journal["math_problem"]

                output = self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", _OTHER_PROBLEM_ID], expected_exit_code=1)

                self.assertIn(_build_pending_refusal(_EARLIER_DOI, _PROBLEM_ID, problem_target["api_url"],
                                                     _OTHER_PROBLEM_ID, instruction_msg, _NOTHING_SENT_CLOSING_MSG), output)
                # Nothing leaves, and nothing in the refusal suggests that anything was about to be sent there.
                self.assertNotIn("would receive your API key", output)
                self.assertEqual(self.fake_api.requests, [])
                self.assertEqual(self.exactory.calls, [])
                self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_the_advice_for_another_record_depends_on_the_path_of_the_earlier_deposit(self) -> None:
        # A direct deposit creates a record on every run. A managed deposit with the same manuscript resumes
        # its completed intent, so only a changed manuscript or a new version makes another record.
        self.assertEqual(_draft._build_next_deposit_advice_msg(_UNATTACHED_JOURNAL),
                         "To publish another record for this problem, run the command again.")
        self.assertEqual(_draft._build_next_deposit_advice_msg(_build_managed_journal("a" * 32)),
                         "To publish another record for this problem, change the manuscript or add --new-version."
                         " The same manuscript resumes the same completed deposit.")

    def test_a_bad_problem_id_is_refused_before_any_journal_is_considered(self) -> None:
        for label, journal in (("a record waits for its attachment", _UNATTACHED_JOURNAL),
                               ("an unsettled deposit", _OTHER_JOURNAL)):
            self._record_journal(journal)
            for problem_id in ("", "not-an-id", _PROBLEM_ID + "0", " " + _PROBLEM_ID):
                with self.subTest(journal=label, problem_id=problem_id):
                    output = self._deposit([*_PRODUCTION_PUBLISH, "--math-problem", problem_id], expected_exit_code=2)

                    self.assertIn(f'"{problem_id}" is not a problem id. Copy the id from the problem page.', output)
                    self.assertEqual(self.fake_api.requests, [])
                    self.assertEqual(self.exactory.calls, [])
                    self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_a_deposit_for_a_problem_replaces_a_journal_with_nothing_to_attach(self) -> None:
        for label, journal in (("attached", _ATTACHED_JOURNAL), ("sandbox", _SANDBOX_JOURNAL),
                               ("released", _RELEASED_JOURNAL)):
            with self.subTest(journal=label):
                self._record_journal(journal)

                self._deposit(_PROBLEM_DEPOSIT)

                self.assertEqual(_read_journal(self.workspace_dir)["math_problem"], _PROBLEM_TARGET)
                self.assertEqual(_read_journal(self.workspace_dir)["settled"], True)

    def test_a_deposit_that_only_creates_a_draft_keeps_a_journal_with_an_unattached_record(self) -> None:
        self._record_journal(_UNATTACHED_JOURNAL)

        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])

        # A draft publishes nothing, so it neither retries the attachment nor replaces the journal.
        self.assertIn("This draft stays unattached", output)
        self.assertNotIn("is attached now", output)
        self.assertEqual(_read_journal(self.workspace_dir), _UNATTACHED_JOURNAL)
        self.assertEqual(self.exactory.links(), [])

    def test_attach_problem_attaches_a_settled_record_alone_when_the_run_names_the_recorded_origin(self) -> None:
        earlier = dict(_UNATTACHED_JOURNAL, math_problem={"problem_id": _OTHER_PROBLEM_ID,
                                                          "api_url": "https://earlier.test"})
        self._record_journal(earlier)
        os.environ["EXACTORY_API_URL"] = "https://earlier.test"
        del os.environ["ZENODO_TOKEN"], os.environ["ZENODO_SANDBOX_TOKEN"]

        output = _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(self.exactory.calls, [(["link-problem-paper", _OTHER_PROBLEM_ID,
                                                 "--identifier", _EARLIER_DOI], "https://earlier.test")])
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(_read_journal(self.workspace_dir), dict(earlier, attached=True))
        self.assertIn(f"Record {_EARLIER_DOI} is attached to https://earlier.test/math/{_OTHER_PROBLEM_ID}.", output)

    def test_attach_problem_has_nothing_to_attach_in_the_other_settled_states(self) -> None:
        for journal, reason_msg in (
                (_ATTACHED_JOURNAL, "is already attached."),
                (_SANDBOX_JOURNAL, "is a sandbox record, and only a published production record is attached."),
                (_RELEASED_JOURNAL, "published no record, so there is nothing to attach.")):
            with self.subTest(reason=reason_msg):
                self._record_journal(journal)

                output = _run_draft_command(["attach-problem"], None, self)

                self.assertIn(f"The recorded deposit for problem {_PROBLEM_ID} {reason_msg} Nothing was changed.", output)
                self.assertEqual(_read_journal(self.workspace_dir), journal)
        self.assertEqual(self.exactory.calls, [])
        self.assertEqual(self.fake_api.requests, [])

    def _at_another_origin(self, journal: dict, problem_id: str = _OTHER_PROBLEM_ID) -> dict:
        return dict(journal, math_problem={"problem_id": problem_id, "api_url": "https://recorded.test"})

    def _assert_stopped_before_sending_anything(self, output: str, journal: dict, current_origin: str = "https://api.test") -> None:
        problem_id = journal["math_problem"]["problem_id"]
        stop_msg = (f"The recorded deposit for problem {problem_id} names the exactory origin https://recorded.test. This"
                    f" run uses the exactory origin {current_origin}. The recorded origin would receive your API key."
                    f" Nothing was sent, and {_JOURNAL_PATH_TEXT} stays as it is. If https://recorded.test is your own"
                    " exactory origin, ")
        if journal["settled"]:
            # The record is published: attach it from its own origin, or give the attachment up.
            self.assertIn(f"{stop_msg}run exactory-draft attach-problem with EXACTORY_API_URL set to https://recorded.test."
                          f" {_build_settled_removal_rule(journal['doi'])}", output)
        else:
            self.assertIn(f"{stop_msg}run the command again with EXACTORY_API_URL set to https://recorded.test.", output)
        # An unsettled journal may hold an unpublished deposition, so only its message gives that removal rule.
        self.assertEqual(_REMOVAL_RULE_MSG in output, not journal["settled"])
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(self.exactory.calls, [])
        self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_a_journal_at_another_origin_sends_nothing_from_attach_problem_or_a_deposit_that_uses_it(self) -> None:
        deposit = ["deposit", "--abstract-file", "draft/abstract.txt"]
        unsettled = self._at_another_origin(_OTHER_JOURNAL)
        # A deposit uses a settled journal only when it names the problem of that journal: it is the retry.
        unattached = self._at_another_origin(_UNATTACHED_JOURNAL, _PROBLEM_ID)
        for label, journal, argv in (
                ("unsettled", unsettled, ["attach-problem"]),
                ("unsettled", unsettled, [*deposit, *_PROBLEM_DEPOSIT]),
                ("unsettled", unsettled, [*deposit, *_PRODUCTION_PUBLISH]),
                ("unattached", unattached, ["attach-problem"]),
                ("unattached", unattached, [*deposit, *_PROBLEM_DEPOSIT])):
            with self.subTest(journal=label, command=" ".join(argv[:1] + argv[-2:])):
                self._record_journal(journal)

                output = _run_draft_command(argv, 1, self)

                self._assert_stopped_before_sending_anything(output, journal)

    def _record_unclaimed_managed_journal_elsewhere(self) -> dict:
        """An unsettled managed journal at another origin whose intent never sent its publish request."""
        store = Store(self.workspace_dir)
        metadata = {"related_identifiers": [{"identifier": f"https://recorded.test/math/{_OTHER_PROBLEM_ID}",
                                             "relation": "references"}]}
        intent_id = begin_intent(store, "deposit", _build_intent_binding(metadata=metadata),
                                 expected_revision=store.revision, request_id="unclaimed-elsewhere")["id"]
        journal = _build_managed_journal(intent_id, math_problem={"problem_id": _OTHER_PROBLEM_ID,
                                                                  "api_url": "https://recorded.test"})
        self._record_journal(journal)
        return journal

    def test_a_managed_journal_whose_intent_never_sent_its_publish_request_is_not_stopped_by_its_origin(self) -> None:
        journal = self._record_unclaimed_managed_journal_elsewhere()

        output = _run_draft_command(["attach-problem"], 1, self)

        # The run releases the journal and sends nothing to the recorded origin, so that origin does not stop
        # it, and no message says that the API key would go there. The message names the command to run, and the
        # note says what that run does at this origin: it releases the journal and deposits as usual.
        self.assertIn("The recorded deposit never sent its publish request, so there is no record to attach."
                      " Run exactory-draft deposit to deposit the paper.", output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. The next deposit run"
                      " releases it and deposits as usual.", output)
        self.assertNotIn("to finish it", output)
        self.assertNotIn("would receive your API key", output)
        self.assertEqual((self.fake_api.requests, self.exactory.calls), ([], []))
        self.assertEqual(_read_journal(self.workspace_dir), journal)

        # A deposit releases the journal and deposits as usual.
        self._deposit(_PRODUCTION_PUBLISH)

        released = _read_journal(self.workspace_dir)
        self.assertEqual((released["settled"], released["attached"], "doi" in released), (True, False, False))
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(self.exactory.calls, [])

    def test_a_managed_journal_whose_intent_sent_its_publish_request_is_still_stopped_by_its_origin(self) -> None:
        journal = self._record_unclaimed_managed_journal_elsewhere()

        with unittest.mock.patch.object(_draft, "has_claimed_publish", return_value=True):
            for argv in (["attach-problem"], ["deposit", "--abstract-file", "draft/abstract.txt", *_PRODUCTION_PUBLISH]):
                with self.subTest(command=argv[0]):
                    output = _run_draft_command(argv, 1, self)

                    self._assert_stopped_before_sending_anything(output, journal)

    def test_the_release_exception_of_the_origin_check_covers_an_unsettled_journal_only(self) -> None:
        # A settled journal leads to an attachment, so the same journal, once settled, meets the check whatever its
        # intent says.
        unsettled = self._record_unclaimed_managed_journal_elsewhere()
        settled = dict(unsettled, settled=True, doi=_EARLIER_DOI)

        self.assertIsNone(_draft._require_current_origin(unsettled))
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
            _draft._require_current_origin(settled)

        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("names the exactory origin https://recorded.test. This run uses the exactory origin"
                      " https://api.test. The recorded origin would receive your API key.", sink.getvalue())
        self.assertEqual(self.exactory.calls, [])

    def test_a_managed_journal_whose_intent_the_store_does_not_give_names_the_removal_rule_and_the_way_out(self) -> None:
        # A run that uses the recorded origin stops at that intent too, so the stop names no origin to settle from, and
        # no run gets as far as an exactory request. The removal rule covers a deposition that is not published. For a
        # published one, the stop says to remove the journal and to attach the record by hand from the recorded
        # origin, when that origin is the user's own.
        missing_intent_id = "f" * 32
        journal = _build_managed_journal(missing_intent_id, math_problem={"problem_id": _OTHER_PROBLEM_ID,
                                                                          "api_url": "https://recorded.test"})
        for argv in (["attach-problem"], ["deposit", "--abstract-file", "draft/abstract.txt", *_PRODUCTION_PUBLISH]):
            with self.subTest(command=argv[0]):
                self._record_journal(journal)

                output = _run_draft_command(argv, 1, self)

                self.assertEqual(output, f"The recorded deposit for problem {_OTHER_PROBLEM_ID} names the exactory origin"
                                         " https://recorded.test. This run uses the exactory origin https://api.test."
                                         f" Nothing was sent, and {_JOURNAL_PATH_TEXT} stays as it is. The store of this"
                                         " workspace does not give the saved intent of this deposit, so a run that uses"
                                         f" https://recorded.test stops at that intent too. {_REMOVAL_RULE_MSG} the"
                                         f' deposition whose notes read "Exactory publication intent {missing_intent_id}"'
                                         " is not published, or decide not to publish it. The command never removes"
                                         f" it. If you find the deposition published, remove {_JOURNAL_PATH_TEXT}. The"
                                         " recorded origin would receive your API key. If https://recorded.test is your"
                                         " own exactory origin, attach the record there: run exactory link-problem-paper"
                                         f" {_OTHER_PROBLEM_ID} --identifier <the record's DOI> with EXACTORY_API_URL set"
                                         " to https://recorded.test.\n")
                self.assertEqual((self.fake_api.requests, self.exactory.calls), ([], []))
                self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_a_managed_journal_whose_intent_describes_another_operation_is_refused_at_every_origin(self) -> None:
        # A run that uses the recorded origin refuses such a journal as one that it cannot trust, so a run at another
        # origin gives the same refusal and names no origin to switch to.
        store = Store(self.workspace_dir)
        another_operation_id = begin_intent(store, "deposit", _build_intent_binding(), expected_revision=store.revision,
                                            request_id="another-operation")["id"]
        journal = _build_managed_journal(another_operation_id, math_problem={"problem_id": _OTHER_PROBLEM_ID,
                                                                             "api_url": "https://recorded.test"})
        for argv in (["attach-problem"], ["deposit", "--abstract-file", "draft/abstract.txt", *_PRODUCTION_PUBLISH]):
            for is_claimed in (False, True):
                with self.subTest(command=argv[0], is_claimed=is_claimed), \
                        unittest.mock.patch.object(_draft, "has_claimed_publish", return_value=is_claimed):
                    self._record_journal(journal)

                    output = _run_draft_command(argv, 1, self)

                    self.assertIn(f"{_JOURNAL_PATH_TEXT} is not a journal this command can trust: the intent does not name"
                                  f" the problem of the journal. {_REMOVAL_RULE_MSG} the deposition it names is not"
                                  " published, or decide not to publish it. The command never removes it. Nothing was"
                                  " sent.", output)
                    self.assertNotIn("EXACTORY_API_URL set to", output)
                    self.assertEqual((self.fake_api.requests, self.exactory.calls), ([], []))
                    self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_the_attachment_itself_refuses_a_journal_at_another_origin_whatever_path_led_there(self) -> None:
        # Every attachment passes through this one function, so it checks the origin before the exactory request.
        journal = self._at_another_origin(_UNATTACHED_JOURNAL, _PROBLEM_ID)
        self._record_journal(journal)
        for is_retry in (False, True):
            with self.subTest(is_retry=is_retry):
                sink = io.StringIO()
                with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
                    _draft._attach_recorded_record(journal, is_retry=is_retry)

                self.assertEqual(stopped.exception.code, 1)
                self._assert_stopped_before_sending_anything(sink.getvalue(), journal)

    def test_the_current_origin_is_the_default_origin_when_the_environment_names_none(self) -> None:
        del os.environ["EXACTORY_API_URL"]
        journal = self._at_another_origin(_UNATTACHED_JOURNAL)
        self._record_journal(journal)

        output = _run_draft_command(["attach-problem"], 1, self)

        self._assert_stopped_before_sending_anything(output, journal, "https://www.exactory.ai")

    def test_a_journal_at_another_origin_does_not_stop_what_it_does_not_drive(self) -> None:
        unattached = self._at_another_origin(_UNATTACHED_JOURNAL)
        self._record_journal(unattached)
        # A deposit for no problem never uses a settled journal, and a draft replaces nothing.
        self._deposit(_PRODUCTION_PUBLISH)
        self._deposit(["--production", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(_read_journal(self.workspace_dir), unattached)
        # A journal with nothing left to attach drives no request, whatever origin it records.
        for label, journal in (("attached", self._at_another_origin(_ATTACHED_JOURNAL)),
                               ("sandbox", self._at_another_origin(_SANDBOX_JOURNAL)),
                               ("released", self._at_another_origin(_RELEASED_JOURNAL))):
            with self.subTest(journal=label):
                self._record_journal(journal)
                output = _run_draft_command(["attach-problem"], None, self)
                self.assertIn("Nothing was changed.", output)
                self.assertEqual(_read_journal(self.workspace_dir), journal)
                self._deposit(_PROBLEM_DEPOSIT)
                self.assertEqual(_read_journal(self.workspace_dir)["math_problem"], _PROBLEM_TARGET)
        # An unsettled sandbox deposit is never attached, so its settle sends nothing to the recorded origin.
        self.exactory.calls.clear()
        sandbox = dict(self._at_another_origin(_OTHER_JOURNAL), environment="sandbox",
                       base_url="https://sandbox.zenodo.org/api")
        self._record_journal(sandbox)
        self._answer_with(_PublishedForeignDepositionZenodoApi())
        _run_draft_command(["attach-problem"], None, self)
        self.assertEqual(self.exactory.calls, [])
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"]), (True, False, "10.5072/zenodo.4000"))

    def test_a_sandbox_publish_settles_the_journal_and_attaches_nothing(self) -> None:
        # The sandbox issues test DOIs with the prefix 10.5072 (https://developers.zenodo.org).
        self._answer_with(_SandboxZenodoApi())

        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID])

        self.assertIn("Only a published production record is attached to a problem. This sandbox record stays"
                      " unattached.", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"], journal["doi"], journal["environment"]),
                         (True, False, "10.5072/zenodo.4242", "sandbox"))
        self.assertEqual(self.exactory.links(), [])

    def test_a_mark_of_the_attachment_that_cannot_be_written_leaves_a_settled_journal_and_a_retry_attaches_again(self) -> None:
        refuse = _refuse_the_journal_write_when(_draft.json_projection, lambda value: value["attached"])
        with unittest.mock.patch.object(_draft, "json_projection", refuse):
            output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(f"Record 10.5281/zenodo.4242 is attached, and the command cannot mark its recovery record"
                      " attached. Run exactory-draft attach-problem. It retries only the attachment and publishes"
                      " nothing.", output)
        journal = _read_journal(self.workspace_dir)
        self.assertEqual((journal["settled"], journal["attached"]), (True, False))
        self.assertEqual(len(self.exactory.links()), 1)

        _run_draft_command(["attach-problem"], None, self)

        self.assertEqual(len(self.exactory.links()), 2)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])
        self.assertEqual(_count_publishes(self.fake_api), 1)

    def test_a_mark_of_the_attachment_that_cannot_be_written_is_retried_by_the_next_deposit_for_the_problem(self) -> None:
        refuse = _refuse_the_journal_write_when(_draft.json_projection, lambda value: value["attached"])
        with unittest.mock.patch.object(_draft, "json_projection", refuse):
            self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)
        requests_before_the_rerun = len(self.fake_api.requests)

        output = self._deposit(_PROBLEM_DEPOSIT)

        self.assertIn("The record 10.5281/zenodo.4242 of the earlier deposit for this problem is attached now. This run"
                      " published nothing new.", output)
        self.assertEqual(len(self.fake_api.requests), requests_before_the_rerun)
        self.assertEqual(_count_publishes(self.fake_api), 1)
        self.assertEqual(len(self.exactory.links()), 2)
        self.assertTrue(_read_journal(self.workspace_dir)["attached"])

    def test_a_record_of_another_environment_is_neither_settled_nor_attached(self) -> None:
        journal_path = self.workspace_dir / ".exactory" / "problem-deposit.json"
        sandbox_deposit = ["--publish", "--creator", "Shiroshita, Ryosuke", "--math-problem", _PROBLEM_ID]
        # Zenodo issues the test DOIs of the sandbox with the prefix 10.5072 (https://developers.zenodo.org).
        for label, argv_tail, doi, prefix, environment in (
                ("a sandbox DOI from a production publish", _PROBLEM_DEPOSIT, "10.5072/zenodo.4242", "10.5281/", "production"),
                ("a production DOI from a sandbox publish", sandbox_deposit, "10.5281/zenodo.4242", "10.5072/", "sandbox")):
            with self.subTest(record=label):
                self._answer_with(_PublishWithDoiZenodoApi(doi))

                output = self._deposit(argv_tail, expected_exit_code=1)

                self.assertIn(f"{_JOURNAL_PATH_TEXT} is not a journal this command can trust: the DOI {doi} does not"
                              f" start with {prefix}, the DOI prefix of the {environment} environment. Nothing was"
                              f" attached. {_build_settled_removal_rule(doi)}", output)
                # The refusal carries its own way out, so the recovery note does not follow it.
                self.assertNotIn("stays as the recovery record", output)
                self.assertEqual(self.exactory.links(), [])
                self.assertFalse(_read_journal(self.workspace_dir)["settled"])
                # Every later deposit meets the same refusal until the user removes the file, as the message says.
                self._deposit(_PRODUCTION_PUBLISH, expected_exit_code=1)
                journal_path.unlink()
        # With the file removed, the workspace deposits again.
        self._answer_with(_FakeZenodoApi())
        self._deposit(_PRODUCTION_PUBLISH)
        self.assertEqual(_count_publishes(self.fake_api), 1)

    def test_a_reconcile_of_the_operation_a_journal_names_ends_with_the_note_on_that_journal(self) -> None:
        self._record_journal(_OTHER_JOURNAL)

        # The operation of a direct deposit has no saved intent, so this reconcile ends with an error.
        output = _run_draft_command(["reconcile", _OTHER_JOURNAL["operation_id"]], 1, self)

        self.assertIn("remote_intent_missing", output)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. The next deposit run reads"
                      " the deposition back from Zenodo and settles it.", output)
        self.assertIn(f"{_REMOVAL_RULE_MSG} deposition 4000 (https://zenodo.org/api/deposit/4000) is not published, or"
                      " decide not to publish it. The command never removes it.", output)
        self.assertEqual(_read_journal(self.workspace_dir), _OTHER_JOURNAL)
        # An error on an intent that the journal does not name carries no note.
        unrelated_output = _run_draft_command(["reconcile", "1" * 32], 1, self)
        self.assertIn("remote_intent_missing", unrelated_output)
        self.assertNotIn("stays as the recovery record", unrelated_output)

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
        self.assertIn(f"{_REMOVAL_RULE_MSG} deposition 4000 (https://zenodo.org/api/deposit/4000) is not published, or"
                      " decide not to publish it. The command never removes it.", output)
        self.assertEqual(_read_journal(self.workspace_dir), _OTHER_JOURNAL)
        self.assertEqual(self.exactory.links(), [])

    def test_a_publish_that_zenodo_refuses_leaves_an_unsettled_journal_and_says_when_removing_it_is_safe(self) -> None:
        self._answer_with(_StatusErrorZenodoApi("/actions/publish", 400))
        _, stderr_text = self._deposit_on_split_streams(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("The Zenodo API returned HTTP 400.", stderr_text)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. The next deposit run"
                      " reads the deposition back from Zenodo and settles it.", stderr_text)
        self.assertIn(f"{_REMOVAL_RULE_MSG} deposition 4242 (https://zenodo.org/api/deposit/4242) is not published, or"
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
        self.assertIn(f"{_REMOVAL_RULE_MSG} deposition 4242 (https://zenodo.org/api/deposit/4242) is not published, or"
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

    def test_new_version_refuses_a_fresh_draft_whose_plain_publish_was_refused(self) -> None:
        self._answer_with(_StatusErrorZenodoApi("/actions/publish", 400))
        self._deposit(_PRODUCTION_PUBLISH, expected_exit_code=1)
        # A refused plain publish leaves a fresh draft, without a prior concept DOI.
        prior = self.read_deposit_state()
        self.assertNotIn("doi", prior)
        self.assertNotIn("concept_doi", prior)
        self._answer_with(_FakeZenodoApi())

        output = self._deposit(["--production", "--new-version", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)

        self.assertIn("Deposition 4242 is a fresh draft, not a version of the workspace's published record:"
                      " https://zenodo.org/api/deposit/4242. Delete it with the Delete button on its page."
                      " Then run the same command again with --new-version.", output)
        self.assertEqual(self.requested(), [("GET", "https://zenodo.org/api/deposit/depositions/4242")])
        self.assertEqual(self.read_deposit_state(), prior)


class TestExplainUnsettledJournal(_PlainWorkspaceDepositTestCase):
    """The note that every exit of a command carries while its journal is unsettled."""

    def _record_journal(self, journal: dict) -> None:
        (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text(json.dumps(journal), encoding="utf-8")

    def _note_for(self, journal: dict | None) -> str:
        return _draft._build_unsettled_journal_note(journal)

    def test_a_library_error_keeps_its_code_and_details_and_gains_a_sentence_with_the_note(self) -> None:
        self._record_journal(_OTHER_JOURNAL)
        note_msg = self._note_for(_OTHER_JOURNAL)
        self.assertIn(f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit.", note_msg)

        for message, expected_msg in (("The manuscript changed", f"The manuscript changed. {note_msg}"),
                                      ("The manuscript changed.", f"The manuscript changed. {note_msg}")):
            with self.subTest(message=message):
                with self.assertRaises(ResearchError) as stopped:
                    with _draft._explain_unsettled_journal(_OTHER_JOURNAL):
                        raise ResearchError("readiness_required", message, {"obligations": []})
                self.assertEqual((stopped.exception.code, stopped.exception.message, stopped.exception.details),
                                 ("readiness_required", expected_msg, {"obligations": []}))

    def test_an_exit_passes_through_with_the_note_on_stderr(self) -> None:
        self._record_journal(_OTHER_JOURNAL)
        note_msg = self._note_for(_OTHER_JOURNAL)
        sink = io.StringIO()

        with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
            with _draft._explain_unsettled_journal(_OTHER_JOURNAL):
                _draft._exit_with_error("The Zenodo API returned HTTP 400.", 1)

        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(sink.getvalue(), f"The Zenodo API returned HTTP 400.\n{note_msg}\n")

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

    def test_the_note_says_what_the_next_deposit_run_does_with_the_intent_of_a_managed_journal(self) -> None:
        store = Store(self.workspace_dir)
        intent_id = begin_intent(store, "deposit", _build_intent_binding(), expected_revision=store.revision,
                                 request_id="managed-note")["id"]
        journal = _build_managed_journal(intent_id)
        self._record_journal(journal)
        released_msg = "The next deposit run releases it and deposits as usual."
        read_back_msg = "The next deposit run reads the deposition back from Zenodo and settles it."

        # The intent never claimed its publish step, so no publication is uncertain and the next run releases the journal.
        self.assertIn(released_msg, self._note_for(journal))
        self.assertNotIn(read_back_msg, self._note_for(journal))
        # Once the intent claimed its publish step, the publication is uncertain, and the next run reads it back.
        with unittest.mock.patch.object(_draft, "has_claimed_publish", return_value=True):
            self.assertIn(read_back_msg, self._note_for(journal))
            self.assertNotIn(released_msg, self._note_for(journal))
        # A direct journal has no intent, and the next run reads its deposition back.
        self._record_journal(_OTHER_JOURNAL)
        self.assertIn(read_back_msg, self._note_for(_OTHER_JOURNAL))
        # The next run of a managed journal starts from its intent. When the store does not give that intent, the
        # run stops there before it reads anything back, so the note names neither outcome and keeps the rest.
        missing_intent_id = "f" * 32
        missing = _build_managed_journal(missing_intent_id)
        self._record_journal(missing)
        note_msg = self._note_for(missing)
        self.assertTrue(note_msg.startswith(
            f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. {_REMOVAL_RULE_MSG} the deposition"
            f' whose notes read "Exactory publication intent {missing_intent_id}" is not published, or decide not to'
            " publish it. The command never removes it."), note_msg)
        self.assertNotIn(released_msg, note_msg)
        self.assertNotIn(read_back_msg, note_msg)

    def test_the_note_of_a_journal_whose_intent_the_store_does_not_give_says_what_to_do_with_a_published_record(
            self) -> None:
        # No run can settle such a journal, and the removal rule covers only a deposition that is not published. So
        # the note also says what to do when the user finds the deposition published.
        missing_intent_id = "f" * 32
        removal_msg = (f"{_JOURNAL_PATH_TEXT} stays as the recovery record of this deposit. {_REMOVAL_RULE_MSG} the"
                       f' deposition whose notes read "Exactory publication intent {missing_intent_id}" is not'
                       " published, or decide not to publish it. The command never removes it.")
        production = _build_managed_journal(missing_intent_id)
        self._record_journal(production)
        # This run uses the recorded origin.
        os.environ["EXACTORY_API_URL"] = "https://api.test"
        self.assertEqual(self._note_for(production),
                         f"{removal_msg} If you find the deposition published, remove {_JOURNAL_PATH_TEXT}. Then attach"
                         f" the record to the problem with exactory link-problem-paper {_PROBLEM_ID} --identifier <the"
                         " record's DOI>.")
        # This run uses another origin, so the record is attached from the recorded one, which then receives the key.
        os.environ["EXACTORY_API_URL"] = "https://elsewhere.test"
        self.assertEqual(self._note_for(production),
                         f"{removal_msg} If you find the deposition published, remove {_JOURNAL_PATH_TEXT}. The recorded"
                         " origin would receive your API key. If https://api.test is your own exactory origin, attach"
                         f" the record there: run exactory link-problem-paper {_PROBLEM_ID} --identifier <the record's"
                         " DOI> with EXACTORY_API_URL set to https://api.test.")
        # Only a published production record is attached to a problem.
        sandbox = _build_managed_journal(missing_intent_id, environment="sandbox",
                                         base_url="https://sandbox.zenodo.org/api")
        self._record_journal(sandbox)
        self.assertEqual(self._note_for(sandbox),
                         f"{removal_msg} If you find the deposition published, remove {_JOURNAL_PATH_TEXT}. A sandbox"
                         " record is not attached to a problem.")

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
        # One fact the code knows, and one instruction.
        self.assertEqual(refused.exception.message, "The prior deposit, deposition 4242, has no recorded DOI. Open"
                                                    " https://zenodo.org/deposit/4242 and check its state.")
        self.assertEqual(refused.exception.details, {"deposition_id": 4242})

    def test_a_prior_without_a_doi_and_without_a_page_is_refused_with_a_general_instruction(self) -> None:
        prior = {"environment": "production", "deposition_id": 4242}

        with self.assertRaises(ResearchError) as refused:
            require_published_prior(prior, "production")
        self.assertEqual(refused.exception.message,
                         "The prior deposit, deposition 4242, has no recorded DOI. Check its state on Zenodo.")

    def test_no_prior_and_a_prior_of_another_environment_are_refused(self) -> None:
        sandbox_prior = {"environment": "sandbox", "deposition_id": 4242, "doi": "10.5072/zenodo.4242"}

        for prior in (None, sandbox_prior):
            with self.subTest(prior=prior):
                with self.assertRaises(ResearchError) as refused:
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


class TestZenodoTokenOrigin(unittest.TestCase):
    """The Zenodo token goes only to the Zenodo API of the environment that it belongs to
    (https://developers.zenodo.org), whichever address a journal, an intent, or an answer names."""

    def test_only_an_address_under_the_api_of_the_environment_is_approved(self) -> None:
        for url, environment, is_approved in (
                ("https://zenodo.org/api/deposit/depositions", "production", True),
                ("https://zenodo.org/api", "production", True),
                ("https://zenodo.org/api/files/bucket-1/paper.pdf?x=1#top", "production", True),
                ("https://sandbox.zenodo.org/api/deposit/depositions/4242", "sandbox", True),
                ("https://sandbox.zenodo.org/api/deposit/depositions", "production", False),
                ("https://zenodo.org/api/deposit/depositions", "sandbox", False),
                ("http://zenodo.org/api/deposit/depositions", "production", False),
                ("https://evil.test/api/deposit/depositions", "production", False),
                ("https://zenodo.org.evil.test/api/deposit/depositions", "production", False),
                ("https://zenodo.org@evil.test/api/deposit/depositions", "production", False),
                ("https://evil.test@zenodo.org/api/deposit/depositions", "production", False),
                ("https://zenodo.org:8443/api/deposit/depositions", "production", False),
                ("https://zenodo.org/apix/deposit/depositions", "production", False),
                ("https://zenodo.org/records/4242", "production", False),
                ("//zenodo.org/api/deposit/depositions", "production", False),
                ("/api/deposit/depositions", "production", False),
                ("zenodo.org/api/deposit/depositions", "production", False),
                ("https://[::1/api", "production", False),
                ("", "production", False)):
            with self.subTest(url=url, environment=environment):
                self.assertIs(_draft._is_inside_zenodo_api(url, environment), is_approved)

    def test_an_address_outside_the_api_of_the_environment_gets_no_request_and_no_token(self) -> None:
        for url in ("https://evil.test/api/files/bucket-1/paper.pdf", "https://sandbox.zenodo.org/api/deposit/depositions",
                    "http://zenodo.org/api/deposit/depositions", "https://zenodo.org.evil.test/api/deposit/depositions",
                    "https://zenodo.org/records/4242"):
            with self.subTest(url=url), unittest.mock.patch.object(_draft, "_open_url") as open_url:
                sink = io.StringIO()
                with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
                    _draft._call_zenodo("PUT", url, "secret-token", file_bytes=b"paper", environment="production")

                self.assertEqual(stopped.exception.code, 1)
                open_url.assert_not_called()
                self.assertEqual(sink.getvalue(), f"The address {url} is not under https://zenodo.org/api, the Zenodo API"
                                                  " of the production environment. The command sent nothing to it, and"
                                                  " the token stayed here.\n")

    def test_the_token_goes_with_a_request_to_the_api_of_its_environment(self) -> None:
        for environment, url in (("production", "https://zenodo.org/api/deposit/depositions/1"),
                                 ("sandbox", "https://sandbox.zenodo.org/api/deposit/depositions/1")):
            with self.subTest(environment=environment), \
                    unittest.mock.patch.object(_draft, "_open_url", return_value={"id": 1}) as open_url:
                self.assertEqual(_draft._call_zenodo("GET", url, "secret-token", environment=environment), {"id": 1})

                (request,), _ = open_url.call_args
                self.assertEqual(request.full_url, url)
                self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")

    def test_a_refused_redirect_ends_the_call_with_the_advice_of_its_caller(self) -> None:
        url = "https://zenodo.org/api/deposit/depositions/1/actions/publish"
        for refused, refusal_msg in (
                (RefusedRedirectError(url, "https://evil.test/x", "POST", is_cross_origin=True),
                 "Zenodo answered with a redirect to https://evil.test/x, which is not on the origin of the request."
                 " The command did not follow it, and the token was not sent there."),
                # A redirect of a write within the origin leaves the result of the write open.
                (RefusedRedirectError(url, "https://zenodo.org/api/records/1", "POST", is_cross_origin=False),
                 "Zenodo answered the POST request with a redirect. The command does not follow a redirect of a POST"
                 " request, so the result of the request is not confirmed.")):
            with self.subTest(is_cross_origin=refused.is_cross_origin):
                sink = io.StringIO()
                with unittest.mock.patch.object(_draft, "_open_url", side_effect=refused), \
                        contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
                    _draft._call_zenodo("POST", url, "secret-token",
                                        failure_advice=_draft._FailureAdvice(uncertain_msg="Check the deposition."),
                                        environment="production")

                self.assertEqual(stopped.exception.code, 1)
                self.assertEqual(sink.getvalue(), f"{refusal_msg} Check the deposition.\n")


_JSON_HEADERS = {"Content-Type": "application/json"}


def _serve_on_loopback(test_case: unittest.TestCase, respond) -> tuple[str, list]:
    """Serve on a free loopback port until the test ends. `respond(method, path)` returns the status, the
    headers, and the body of the answer, or None to close the connection without an answer. Returns the
    address and the list that gets one (method, path, Authorization header) for every request that arrives."""
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            received.append((self.command, self.path, self.headers.get("Authorization")))
            answer = respond(self.command, self.path)
            if answer is None:
                # The handler writes nothing, and the server closes the connection after the request.
                self.close_connection = True
                return
            status, headers, body = answer
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = _answer

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    test_case.addCleanup(thread.join)
    test_case.addCleanup(server.server_close)
    test_case.addCleanup(server.shutdown)
    return f"http://127.0.0.1:{server.server_address[1]}", received


def _answer_like_exactory(method: str, path: str) -> tuple[int, dict, bytes]:
    """Answer like the exactory API: an account with an ORCID iD, and success for every other request."""
    if path.endswith("/api/v1/me"):
        return 200, _JSON_HEADERS, json.dumps({"displayName": "Ada", "orcidId": "0000-0002-1825-0097"}).encode()
    return 200, _JSON_HEADERS, b'{"papers": []}'


class _RecordingHandler(urllib.request.BaseHandler):
    """Records the address of every HTTP request that goes through the opener it belongs to."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def http_request(self, request: urllib.request.Request) -> urllib.request.Request:
        self.urls.append(request.full_url)
        return request


class TestSameOriginRedirects(unittest.TestCase):
    """urllib sends the headers of a request, a credential included, on to the target of a redirect, and it
    turns a redirected POST into a GET at the target. A client that sends a credential follows a redirect only
    within the origin of its request (research_harness.same_origin, which the Zenodo client and the exactory
    client share)."""

    def setUp(self) -> None:
        # The requests go to the loopback address, whatever proxy the machine names.
        patcher = unittest.mock.patch.object(urllib.request, "getproxies", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_origin_names_the_scheme_host_and_port_and_leaves_out_the_user_name_and_the_path(self) -> None:
        for url, origin in (("https://zenodo.org/api/deposit/depositions/1?x=1", "https://zenodo.org"),
                            ("http://127.0.0.1:8080/a", "http://127.0.0.1:8080"),
                            ("https://user:secret@api.test:8443/path", "https://api.test:8443"),
                            ("http://[::1]:9000/x", "http://[::1]:9000")):
            with self.subTest(url=url):
                self.assertEqual(get_origin(url), origin)

    def test_the_handler_follows_a_read_within_the_origin_and_refuses_a_redirect_to_another_origin(self) -> None:
        handler = SameOriginRedirectHandler()
        for method in ("GET", "HEAD"):
            request = urllib.request.Request("https://zenodo.org/api/deposit/depositions/1", method=method,
                                             headers={"Authorization": "Bearer secret-token"})
            for target in ("https://zenodo.org/api/deposit/depositions/2", "https://zenodo.org/records/1"):
                with self.subTest(method=method, target=target):
                    followed = handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, target)
                    self.assertEqual(followed.full_url, target)
                    self.assertEqual(followed.get_header("Authorization"), "Bearer secret-token")
        request = urllib.request.Request("https://zenodo.org/api/deposit/depositions/1",
                                         headers={"Authorization": "Bearer secret-token"})
        for target in ("https://evil.test/api/deposit/depositions/1", "http://zenodo.org/api/deposit/depositions/1",
                       "https://zenodo.org:8443/api/deposit/depositions/1", "https://sandbox.zenodo.org/api/x",
                       "https://zenodo.org.evil.test/api/x"):
            with self.subTest(target=target):
                answer = io.BytesIO()
                with self.assertRaises(RefusedRedirectError) as refused:
                    handler.redirect_request(request, answer, 302, "Found", {}, target)
                self.assertEqual((refused.exception.request_url, refused.exception.target_url,
                                  refused.exception.method, refused.exception.is_cross_origin),
                                 (request.full_url, target, "GET", True))
                # The answer that carried the redirect is closed, so nothing waits for the garbage collector.
                self.assertTrue(answer.closed)

    def test_the_handler_refuses_every_redirect_of_a_request_that_is_not_a_read(self) -> None:
        # urllib turns a POST into a GET on a 301, 302, or 303, so the answer to that GET would look like the
        # answer to the write. Within the origin too, no redirect of a write is followed.
        handler = SameOriginRedirectHandler()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(method=method, status=status):
                    request = urllib.request.Request("https://api.test/api/v1/math-problems/x/papers", method=method,
                                                     data=b"{}", headers={"Authorization": "Bearer secret-token"})
                    answer = io.BytesIO()
                    with self.assertRaises(RefusedRedirectError) as refused:
                        handler.redirect_request(request, answer, status, "Moved", {}, "https://api.test/api/v1/moved")
                    self.assertEqual((refused.exception.method, refused.exception.is_cross_origin), (method, False))
                    self.assertTrue(answer.closed)

    def test_a_redirect_to_another_origin_is_refused_and_that_origin_receives_nothing(self) -> None:
        other_url, other_received = _serve_on_loopback(self, lambda method, path: (200, {}, b"{}"))
        url, received = _serve_on_loopback(
            self, lambda method, path: (302, {"Location": f"{other_url}/api/deposit/depositions"}, b""))
        request = urllib.request.Request(f"{url}/api/deposit/depositions", headers={"Authorization": "Bearer secret-token"})

        with self.assertRaises(RefusedRedirectError) as refused:
            _draft._open_url(request)

        self.assertEqual(refused.exception.target_url, f"{other_url}/api/deposit/depositions")
        self.assertEqual(received, [("GET", "/api/deposit/depositions", "Bearer secret-token")])
        self.assertEqual(other_received, [])

    def test_a_redirected_write_is_not_turned_into_a_read_at_another_origin(self) -> None:
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                other_url, other_received = _serve_on_loopback(self, lambda method, path: (200, _JSON_HEADERS, b"{}"))
                url, received = _serve_on_loopback(
                    self, lambda method, path, status=status: (status, {"Location": f"{other_url}{path}"}, b""))
                request = urllib.request.Request(f"{url}/api/v1/papers", method="POST", data=b'{"identifier": "x"}',
                                                 headers={"Authorization": "Bearer secret-token"})

                with self.assertRaises(RefusedRedirectError):
                    open_url_within_origin(request, timeout=10)

                self.assertEqual(received, [("POST", "/api/v1/papers", "Bearer secret-token")])
                self.assertEqual(other_received, [])

    def test_a_redirect_within_the_origin_is_followed(self) -> None:
        def respond(method: str, path: str) -> tuple[int, dict, bytes]:
            if path == "/api/start":
                return 302, {"Location": "/api/moved"}, b""
            return 200, _JSON_HEADERS, b'{"moved": true}'
        url, received = _serve_on_loopback(self, respond)
        request = urllib.request.Request(f"{url}/api/start", headers={"Authorization": "Bearer secret-token"})

        self.assertEqual(_draft._open_url(request), {"moved": True})

        self.assertEqual(received, [("GET", "/api/start", "Bearer secret-token"),
                                    ("GET", "/api/moved", "Bearer secret-token")])

    def test_a_redirect_of_a_write_within_the_origin_is_refused_and_its_target_receives_nothing(self) -> None:
        publish_path = "/api/deposit/depositions/1/actions/publish"
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                # The target answers like a published deposition, so a followed redirect would look like a publish.
                url, received = _serve_on_loopback(self, lambda method, path, status=status: (
                    (status, {"Location": "/api/moved"}, b"") if path == publish_path
                    else (200, _JSON_HEADERS, b'{"doi": "10.5281/zenodo.1"}')))
                request = urllib.request.Request(f"{url}{publish_path}", method="POST",
                                                 headers={"Authorization": "Bearer secret-token"})

                with self.assertRaises(RefusedRedirectError) as refused:
                    _draft._open_url(request)

                self.assertEqual((refused.exception.method, refused.exception.is_cross_origin), ("POST", False))
                self.assertEqual(received, [("POST", publish_path, "Bearer secret-token")])

    def test_an_opener_that_the_caller_installed_stays_installed_and_the_call_does_not_use_it(self) -> None:
        url, received = _serve_on_loopback(self, lambda method, path: (200, _JSON_HEADERS, b"{}"))
        recorder = _RecordingHandler()
        self.addCleanup(urllib.request.install_opener, None)
        urllib.request.install_opener(urllib.request.build_opener(recorder))

        with open_url_within_origin(urllib.request.Request(f"{url}/call"), timeout=10) as response:
            response.read()
        with urllib.request.urlopen(f"{url}/after", timeout=10) as response:
            response.read()

        # The call sends its request through an opener of its own, and the opener of the process stays as it was.
        self.assertEqual(recorder.urls, [f"{url}/after"])
        self.assertEqual([path for _, path, _ in received], ["/call", "/after"])

    def test_a_caller_that_resets_the_opener_of_the_process_during_the_call_does_not_lift_the_rule(self) -> None:
        other_url, other_received = _serve_on_loopback(self, lambda method, path: (200, _JSON_HEADERS, b"{}"))
        url, _ = _serve_on_loopback(self, lambda method, path: (302, {"Location": f"{other_url}/x"}, b""))
        open_with_the_opener_of_the_process = urllib.request.urlopen

        def open_after_another_caller_resets_the_opener(*args, **kwargs):
            # Another caller ends its own call at this moment and resets the opener of the process.
            urllib.request.install_opener(None)
            return open_with_the_opener_of_the_process(*args, **kwargs)

        request = urllib.request.Request(f"{url}/start", headers={"Authorization": "Bearer secret-token"})
        with unittest.mock.patch.object(urllib.request, "urlopen", open_after_another_caller_resets_the_opener), \
                self.assertRaises(RefusedRedirectError):
            open_url_within_origin(request, timeout=10)

        self.assertEqual(other_received, [])


class TestDirectCreationThatZenodoAppliedBeforeARedirect(_PlainWorkspaceDepositTestCase):
    """Codex P22 through the real transport: a loopback stand-in for the Zenodo API creates draft 4242 and answers the
    creation request with 303. The redirect handler refuses a redirect of a POST, and the command says that a draft may
    exist, so that the user looks at the drafts before a second run creates another one."""

    def setUp(self) -> None:
        super().setUp()
        # The requests go to the loopback address, whatever proxy the machine names.
        proxies = unittest.mock.patch.object(urllib.request, "getproxies", return_value={})
        proxies.start()
        self.addCleanup(proxies.stop)

    def test_the_command_refuses_the_redirect_and_says_that_a_draft_may_exist(self) -> None:
        created_deposition_ids: list[int] = []

        def respond(method: str, path: str) -> tuple[int, dict, bytes]:
            if method == "POST" and path == "/api/deposit/depositions":
                # The stand-in creates the draft, and only then answers the creation request.
                created_deposition_ids.append(4242)
                return 303, {"Location": "/api/deposit/depositions/4242"}, b""
            return 404, _JSON_HEADERS, b'{"status": 404}'
        url, received = _serve_on_loopback(self, respond)
        _draft._open_url = _OPEN_URL_OF_THE_COMMAND

        with unittest.mock.patch.dict(_draft._ZENODO_API_URLS, {"sandbox": f"{url}/api"}):
            stdout_text, stderr_text = self._deposit_on_split_streams(["--publish", "--creator", "Shiroshita, Ryosuke"],
                                                                      expected_exit_code=1)

        self.assertTrue(stderr_text.endswith(
            "Zenodo answered the POST request with a redirect. The command does not follow a redirect of a POST request,"
            f" so the result of the request is not confirmed. {_build_uncertain_creation_advice(url)}\n"), stderr_text)
        self.assertEqual(stdout_text, "")
        self.assertEqual(received, [("POST", "/api/deposit/depositions", "Bearer sandbox-token")])
        self.assertEqual(created_deposition_ids, [4242])


class TestExactoryRedirects(_PlainWorkspaceDepositTestCase):
    """The exactory client adds the API key, so it follows no redirect to another origin. These tests run the
    real exactory command as a child process against two loopback servers: the exactory server, and the
    other origin that it redirects to. A refused redirect fails the command, so a record stays pending."""

    def setUp(self) -> None:
        super().setUp()
        # The child process reads its key and its proxy rules from the environment.
        os.environ["EXACTORY_API_KEY"] = "test-key"
        os.environ["no_proxy"] = "127.0.0.1"
        patcher = unittest.mock.patch.object(urllib.request, "getproxies", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.other_url, self.other_received = _serve_on_loopback(self, lambda method, path: (200, _JSON_HEADERS, b"{}"))

    def _serve_exactory(self, redirected: tuple[str, str], status: int = 302, *,
                        location: str | None = None) -> tuple[str, list]:
        """An exactory server whose account has an ORCID iD. It redirects the one request named here (a method
        and the end of a path) to `location`, by default the same path at the other origin. A location that is a
        path alone stays within the origin of the server, where every other request succeeds."""
        redirected_method, redirected_path = redirected

        def respond(method: str, path: str) -> tuple[int, dict, bytes]:
            if method == redirected_method and path.endswith(redirected_path):
                return status, {"Location": location or f"{self.other_url}{path}"}, b""
            return _answer_like_exactory(method, path)
        url, received = _serve_on_loopback(self, respond)
        os.environ["EXACTORY_API_URL"] = url
        return url, received

    def _record_pending_journal(self, url: str) -> dict:
        """A published production record for the problem, recorded at `url`, whose attachment waits."""
        journal = dict(_UNATTACHED_JOURNAL, math_problem={"problem_id": _PROBLEM_ID, "api_url": url})
        (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text(json.dumps(journal), encoding="utf-8")
        return journal

    def _build_read_refusal_msg(self, url: str) -> str:
        """The refusal of a redirect of the account check. A read changes nothing, so the advice is to run again."""
        return (f"The exactory server at {url} answered the GET request with a redirect to another origin,"
                f" {self.other_url}. The command refused the redirect and sent nothing there. Make sure that"
                " EXACTORY_API_URL is correct. Then run the command again.")

    def _build_write_refusal_msg(self, url: str) -> str:
        """The refusal of a redirect of the attachment. The server may have applied it, so the user checks first."""
        return (f"The exactory server at {url} answered the POST request with a redirect to another origin,"
                f" {self.other_url}. The command refused the redirect and sent nothing there. Make sure that"
                f" EXACTORY_API_URL is correct. {build_unconfirmed_write_msg(url)}")

    def test_a_redirect_at_the_account_check_stops_attach_problem_and_the_record_stays_pending(self) -> None:
        url, received = self._serve_exactory(("GET", "/api/v1/me"))
        journal = self._record_pending_journal(url)

        output = _run_draft_command(["attach-problem"], 1, self)

        self.assertIn(self._build_read_refusal_msg(url), output)
        # With EXACTORY_API_URL set to the target of the redirect, the journal of this origin would stop the retry.
        self.assertNotIn(f"set EXACTORY_API_URL to {self.other_url}", output)
        self.assertNotIn("set EXACTORY_API_URL to it", output)
        self.assertIn(f"Record {_EARLIER_DOI} belongs to the earlier deposit for this problem. This run published nothing."
                      f" Attaching it to problem {_PROBLEM_ID} failed:", output)
        self.assertEqual([(method, path) for method, path, _ in received], [("GET", "/api/v1/me")])
        self.assertEqual(received[0][2], "Bearer test-key")
        self.assertEqual(self.other_received, [])
        self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_a_redirect_of_the_attachment_within_the_origin_stops_attach_problem_and_the_record_stays_pending(self) -> None:
        papers_path = f"/api/v1/math-problems/{_PROBLEM_ID}/papers"
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                # The target answers with success, so a followed redirect would look like a finished attachment.
                url, received = self._serve_exactory(("POST", papers_path), status, location="/api/v1/moved")
                journal = self._record_pending_journal(url)

                output = _run_draft_command(["attach-problem"], 1, self)

                self.assertIn(f"The exactory server at {url} answered the POST request with a redirect. The command"
                              f" does not follow a redirect of a POST request. {build_unconfirmed_write_msg(url)}",
                              output)
                self.assertIn(f"Record {_EARLIER_DOI} belongs to the earlier deposit for this problem. This run published nothing."
                              f" Attaching it to problem {_PROBLEM_ID} failed:", output)
                self.assertEqual([(method, path) for method, path, _ in received],
                                 [("GET", "/api/v1/me"), ("POST", papers_path)])
                self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_a_redirect_of_the_attachment_stops_attach_problem_and_the_record_stays_pending(self) -> None:
        papers_path = f"/api/v1/math-problems/{_PROBLEM_ID}/papers"
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.other_received.clear()
                url, received = self._serve_exactory(("POST", papers_path), status)
                journal = self._record_pending_journal(url)

                output = _run_draft_command(["attach-problem"], 1, self)

                self.assertIn(self._build_write_refusal_msg(url), output)
                # Attaching again changes nothing, so the draft command keeps its own advice to retry the attachment.
                self.assertIn("Resolve that cause. Then run exactory-draft attach-problem. It attaches the recorded record"
                              " and publishes nothing.", output)
                # The answer of the other origin would have looked like the answer to the write.
                self.assertEqual([(method, path) for method, path, _ in received],
                                 [("GET", "/api/v1/me"), ("POST", papers_path)])
                self.assertEqual(self.other_received, [])
                self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_a_redirect_stops_the_retry_of_a_deposit_for_the_same_problem(self) -> None:
        papers_path = f"/api/v1/math-problems/{_PROBLEM_ID}/papers"
        url, received = self._serve_exactory(("POST", papers_path))
        journal = self._record_pending_journal(url)

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn(self._build_write_refusal_msg(url), output)
        self.assertIn(f"Record {_EARLIER_DOI} belongs to the earlier deposit for this problem. This run published"
                      f" nothing. Attaching it to problem {_PROBLEM_ID} failed:", output)
        self.assertEqual(self.other_received, [])
        self.assertEqual(self.fake_api.requests, [])
        self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_a_redirect_at_the_account_check_of_a_deposit_for_a_problem_creates_nothing(self) -> None:
        url, received = self._serve_exactory(("GET", "/api/v1/me"))

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("exactory me failed, so nothing was deposited:", output)
        self.assertIn(self._build_read_refusal_msg(url), output)
        self.assertEqual(self.other_received, [])
        self.assertEqual(self.fake_api.requests, [])
        self.assertFalse((self.workspace_dir / ".exactory" / "problem-deposit.json").exists())


class TestExactoryRequestRedirects(unittest.TestCase):
    """_send_request of bin/exactory, called in process: the one place that adds the API key."""

    def setUp(self) -> None:
        patcher = unittest.mock.patch.dict(os.environ, {"EXACTORY_API_KEY": "test-key"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        proxies = unittest.mock.patch.object(urllib.request, "getproxies", return_value={})
        proxies.start()
        self.addCleanup(proxies.stop)
        self.exactory = _load_bin_module("exactory", "exactory_for_the_redirect_tests")
        self.other_url, self.other_received = _serve_on_loopback(self, lambda method, path: (200, _JSON_HEADERS, b"{}"))

    def test_a_closed_loopback_port_proves_that_no_request_was_sent(self) -> None:
        # A port that was bound and then closed refuses a connection at once. A bound port that does not listen
        # is not enough: macOS drops the connection attempt, and the client then meets a timeout, which leaves the
        # outcome unknown.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        origin = f"http://127.0.0.1:{closed_port}"
        os.environ["EXACTORY_API_URL"] = origin
        self.exactory._API_BASE_URL = self.exactory._read_checked_api_base_url()
        for method in ("POST", "GET"):
            with self.subTest(client="exactory", method=method):
                sink = io.StringIO()
                with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
                    self.exactory._send_request(method, "/test")
                self.assertEqual(stopped.exception.code, 1)
                self.assertIn("Then send the request again.", sink.getvalue())
                self.assertNotIn("may have applied", sink.getvalue())

        creation_advice = _draft._FailureAdvice(uncertain_msg="An uncertain creation must be checked first.")
        publish_advice = _draft._build_publish_request_advice_set(
            4242, f"{origin}/deposit/4242", None, is_new_version=False)
        with unittest.mock.patch.dict(_draft._ZENODO_API_URLS, {"sandbox": f"{origin}/api"}), \
                unittest.mock.patch.object(_draft, "_open_url", _OPEN_URL_OF_THE_COMMAND):
            for step, advice in (("creation", creation_advice), ("publication", publish_advice)):
                with self.subTest(client="zenodo", step=step):
                    sink = io.StringIO()
                    with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
                        _draft._call_zenodo("POST", f"{origin}/api/test", "sandbox-token", json_body={},
                                            failure_advice=advice, environment="sandbox")
                    self.assertEqual(stopped.exception.code, 1)
                    self.assertIn("cannot reach the Zenodo API", sink.getvalue())
                    self.assertIn("stays a draft" if step == "publication" else "Then run the command again.",
                                  sink.getvalue())
                    self.assertNotIn("uncertain creation", sink.getvalue())
                    self.assertNotIn("may be published already", sink.getvalue())

    def test_a_redirect_to_another_origin_fails_the_request_and_the_other_origin_receives_nothing(self) -> None:
        url, received = _serve_on_loopback(
            self, lambda method, path: (302, {"Location": f"{self.other_url}{path}"}, b""))
        os.environ["EXACTORY_API_URL"] = url
        sink = io.StringIO()

        self.exactory._API_BASE_URL = self.exactory._read_checked_api_base_url()

        with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
            self.exactory._send_request("POST", "/api/v1/reports", {"kind": "x"})

        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(sink.getvalue(), f"The exactory server at {url} answered the POST request with a redirect to"
                                          f" another origin, {self.other_url}. The command refused the redirect and"
                                          " sent nothing there. Make sure that EXACTORY_API_URL is correct."
                                          f" {build_unconfirmed_write_msg(url)}\n")
        self.assertEqual(received, [("POST", "/api/v1/reports", "Bearer test-key")])
        self.assertEqual(self.other_received, [])

    def test_a_post_that_the_server_stored_before_its_redirect_is_reported_as_possibly_applied(self) -> None:
        posts_path = f"/api/v1/math-problems/{_PROBLEM_ID}/posts"
        for label, location in (("within the origin", "/api/v1/math-posts/1"),
                                ("to another origin", f"{self.other_url}/api/v1/math-posts/1")):
            with self.subTest(redirect=label):
                stored_posts = []

                def respond(method: str, path: str, stored_posts=stored_posts, location=location) -> tuple[int, dict, bytes]:
                    if method == "POST" and path == posts_path:
                        # The server applies the write, and only then answers with the redirect.
                        stored_posts.append(path)
                        return 303, {"Location": location}, b""
                    return _answer_like_exactory(method, path)
                url, received = _serve_on_loopback(self, respond)
                os.environ["EXACTORY_API_URL"] = url
                args = self.exactory._build_parser().parse_args(["post-discussion", _PROBLEM_ID, "--body", "A post."])
                self.exactory._API_BASE_URL = self.exactory._read_checked_api_base_url()
                sink = io.StringIO()

                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(sink), \
                        self.assertRaises(SystemExit) as stopped:
                    args.handler(args)

                self.assertEqual(stopped.exception.code, 1)
                # A second run would post the same text again, so the message sends the user to check first.
                self.assertIn(build_unconfirmed_write_msg(url), sink.getvalue())
                self.assertNotIn("Then run the command again.", sink.getvalue())
                self.assertEqual([(method, path) for method, path, _ in received],
                                 [("GET", "/api/v1/me"), ("POST", posts_path)])
                self.assertEqual(stored_posts, [posts_path])
        self.assertEqual(self.other_received, [])

    def test_a_redirect_of_a_write_within_the_origin_fails_the_request_and_is_not_followed(self) -> None:
        for method, body in (("POST", {"identifier": "10.5281/zenodo.1"}), ("PUT", {"value": 1}), ("DELETE", None)):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(method=method, status=status):
                    # The target answers with success, so a followed redirect would look like the answer to the write.
                    url, received = _serve_on_loopback(self, lambda request_method, path, status=status: (
                        (status, {"Location": "/api/v1/moved"}, b"") if path == "/api/v1/target"
                        else (200, _JSON_HEADERS, b"{}")))
                    os.environ["EXACTORY_API_URL"] = url
                    self.exactory._API_BASE_URL = self.exactory._read_checked_api_base_url()
                    sink = io.StringIO()

                    with contextlib.redirect_stderr(sink), self.assertRaises(SystemExit) as stopped:
                        self.exactory._send_request(method, "/api/v1/target", body)

                    self.assertEqual(stopped.exception.code, 1)
                    self.assertEqual(sink.getvalue(), f"The exactory server at {url} answered the {method} request with"
                                                      f" a redirect. The command does not follow a redirect of a {method}"
                                                      f" request. {build_unconfirmed_write_msg(url)}\n")
                    self.assertEqual(received, [(method, "/api/v1/target", "Bearer test-key")])

    def test_a_redirect_that_needs_no_key_is_refused_the_same_way(self) -> None:
        url, received = _serve_on_loopback(
            self, lambda method, path: (302, {"Location": f"{self.other_url}{path}"}, b""))
        os.environ["EXACTORY_API_URL"] = url

        self.exactory._API_BASE_URL = self.exactory._read_checked_api_base_url()

        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            self.exactory._send_request("POST", "/api/v1/email-codes", {"email": "a@test.local"}, authenticated=False)

        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(received, [("POST", "/api/v1/email-codes", None)])
        self.assertEqual(self.other_received, [])

    def test_a_redirect_within_the_origin_is_followed(self) -> None:
        def respond(method: str, path: str) -> tuple[int, dict, bytes]:
            if path == "/api/v1/old":
                return 302, {"Location": "/api/v1/me"}, b""
            return 200, _JSON_HEADERS, b'{"orcidId": "0000-0002-1825-0097"}'
        url, received = _serve_on_loopback(self, respond)
        os.environ["EXACTORY_API_URL"] = url

        self.exactory._API_BASE_URL = self.exactory._read_checked_api_base_url()

        self.assertEqual(self.exactory._send_request("GET", "/api/v1/old"),
                         ({"orcidId": "0000-0002-1825-0097"}, 200))

        self.assertEqual([(method, path) for method, path, _ in received], [("GET", "/api/v1/old"), ("GET", "/api/v1/me")])

    def test_the_message_names_both_origins_and_leaves_out_the_path_and_the_query(self) -> None:
        error = RefusedRedirectError("https://api.test/api/v1/me", "https://user:secret@elsewhere.test:8443/x?token=1",
                                     "GET", is_cross_origin=True)

        # A read changes nothing on the server, so its refusal keeps the advice to run the command again.
        self.assertEqual(self.exactory._build_redirect_refusal_msg(error, build_unconfirmed_write_msg("https://api.test")),
                         "The exactory server at https://api.test answered the GET request with a redirect to another"
                         " origin, https://elsewhere.test:8443. The command refused the redirect and sent nothing there."
                         " Make sure that EXACTORY_API_URL is correct. Then run the command again.")


class TestPlantedSettledJournal(_PlainWorkspaceDepositTestCase):
    """A settled managed journal that records another exactory origin and names an intent that never claimed its
    publish step: a state that a workspace from someone else can hold, and that the journal check accepts. These
    tests run the real exactory command as a child process. One loopback server stands in for the recorded origin
    and one for the origin of the run, so every request that the command sent would arrive at one of them."""

    def setUp(self) -> None:
        super().setUp()
        # The child process reads its key and its proxy rules from the environment.
        os.environ["EXACTORY_API_KEY"] = "test-key"
        os.environ["no_proxy"] = "127.0.0.1"
        patcher = unittest.mock.patch.object(urllib.request, "getproxies", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.recorded_url, self.recorded_received = _serve_on_loopback(self, _answer_like_exactory)
        self.current_url, self.current_received = _serve_on_loopback(self, _answer_like_exactory)
        os.environ["EXACTORY_API_URL"] = self.current_url
        store = Store(self.workspace_dir)
        metadata = {"related_identifiers": [{"identifier": f"{self.recorded_url}/math/{_PROBLEM_ID}",
                                             "relation": "references"}]}
        intent_id = begin_intent(store, "deposit", _build_intent_binding(metadata=metadata),
                                 expected_revision=store.revision, request_id="planted-unclaimed")["id"]
        self.journal = _build_managed_journal(intent_id, settled=True, doi=_EARLIER_DOI,
                                              math_problem={"problem_id": _PROBLEM_ID, "api_url": self.recorded_url})

    def test_attach_problem_and_the_retry_of_a_deposit_for_the_problem_send_the_recorded_origin_nothing(self) -> None:
        journal_path = self.workspace_dir / ".exactory" / "problem-deposit.json"
        for argv in (["attach-problem"], ["deposit", "--abstract-file", "draft/abstract.txt", *_PROBLEM_DEPOSIT]):
            with self.subTest(command=argv[0]):
                journal_path.write_text(json.dumps(self.journal), encoding="utf-8")

                output = _run_draft_command(argv, 1, self)

                self.assertIn(f"The recorded deposit for problem {_PROBLEM_ID} names the exactory origin"
                              f" {self.recorded_url}. This run uses the exactory origin {self.current_url}. The recorded"
                              f" origin would receive your API key. Nothing was sent, and {_JOURNAL_PATH_TEXT} stays as it"
                              f" is. If {self.recorded_url} is your own exactory origin, run exactory-draft attach-problem"
                              f" with EXACTORY_API_URL set to {self.recorded_url}."
                              f" {_build_settled_removal_rule(_EARLIER_DOI)}", output)
                self.assertEqual((self.recorded_received, self.current_received), ([], []))
                self.assertEqual(_read_journal(self.workspace_dir), self.journal)
        self.assertEqual(self.fake_api.requests, [])


class TestAttachmentWhoseAnswerDidNotArriveInFull(_PlainWorkspaceDepositTestCase):
    """The real exactory command, as a child process, attaches a waiting record through a loopback server whose answer
    to the attachment does not arrive in full. The command ends with its message instead of a traceback, and the
    record stays pending for attach-problem, as after any other failure of the attachment."""

    def setUp(self) -> None:
        super().setUp()
        # The child process reads its key and its proxy rules from the environment.
        os.environ["EXACTORY_API_KEY"] = "test-key"
        os.environ["no_proxy"] = "127.0.0.1"
        patcher = unittest.mock.patch.object(urllib.request, "getproxies", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_record_stays_pending_and_the_message_asks_for_a_check_first(self) -> None:
        papers_path = f"/api/v1/math-problems/{_PROBLEM_ID}/papers"
        for label, papers_answer, cause in (
                ("a connection that closes without an answer", None, "Remote end closed connection without response"),
                ("a body that is not JSON", (200, {"Content-Type": "text/html"}, b"<html>not json</html>"),
                 "the answer has HTTP 200 and a body that is not JSON")):
            with self.subTest(answer=label):
                def respond(method: str, path: str, papers_answer=papers_answer):
                    if method == "POST" and path == papers_path:
                        return papers_answer
                    return _answer_like_exactory(method, path)
                url, received = _serve_on_loopback(self, respond)
                os.environ["EXACTORY_API_URL"] = url
                journal = dict(_UNATTACHED_JOURNAL, math_problem={"problem_id": _PROBLEM_ID, "api_url": url})
                (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text(json.dumps(journal),
                                                                                       encoding="utf-8")

                output = _run_draft_command(["attach-problem"], 1, self)

                self.assertIn(f"Record {_EARLIER_DOI} belongs to the earlier deposit for this problem. This run published nothing."
                              f" Attaching it to problem {_PROBLEM_ID} failed:\n"
                              f"The client cannot read the answer of the exactory server at {url} to the POST request."
                              f" Cause: {cause}. {build_unconfirmed_write_msg(url)}\nResolve that cause. Then run"
                              " exactory-draft attach-problem. It attaches the recorded record and publishes nothing.",
                              output)
                self.assertEqual([(method, path) for method, path, _ in received],
                                 [("GET", "/api/v1/me"), ("POST", papers_path)])
                self.assertEqual(_read_journal(self.workspace_dir), journal)
        self.assertEqual(self.fake_api.requests, [])


class TestIntentAndJournalAgreement(_PlainWorkspaceDepositTestCase):
    """A command executes the intent that a journal names only when the two describe one operation, and it
    checks that before it reads a token (design 2026-10-12 §17). A workspace can come from someone else."""

    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)
        # No command in these tests may read a token. A read fails the test.
        reader = unittest.mock.patch.object(_draft, "_read_zenodo_token", side_effect=AssertionError("a token was read"))
        reader.start()
        self.addCleanup(reader.stop)
        self.store = Store(self.workspace_dir)

    def _save_intent(self, request_id: str, **changes) -> str:
        return begin_intent(self.store, "deposit", _build_intent_binding(**changes),
                            expected_revision=self.store.revision, request_id=request_id)["id"]

    def _record_journal(self, journal: dict) -> None:
        (self.workspace_dir / ".exactory" / "problem-deposit.json").write_text(json.dumps(journal), encoding="utf-8")

    def test_the_agreement_check_names_what_differs(self) -> None:
        journal = _build_managed_journal("a" * 32)
        agreeing = {"id": "a" * 32, "kind": "deposit", "binding": _build_intent_binding()}

        self.assertIsNone(_draft._find_intent_flaw(agreeing, journal))
        for label, intent, flaw_msg in (
                ("another kind", dict(agreeing, kind="verdict"), "the intent is not a deposit"),
                ("another identity", dict(agreeing, id="b" * 32),
                 f"the intent has the identity {'b' * 32}, and the journal names {'a' * 32}"),
                ("no binding", {key: value for key, value in agreeing.items() if key != "binding"},
                 "the intent has no binding"),
                ("a binding that is not an object", dict(agreeing, binding=[]), "the intent has no binding"),
                ("a second related identifier", dict(agreeing, binding=_build_intent_binding(metadata={
                    "related_identifiers": [*_build_intent_binding()["metadata"]["related_identifiers"],
                                            {"identifier": "https://evil.test/x", "relation": "references"}]})),
                 "the intent does not name the problem of the journal"),
                ("another relation", dict(agreeing, binding=_build_intent_binding(metadata={
                    "related_identifiers": [{"identifier": f"https://api.test/math/{_PROBLEM_ID}",
                                             "relation": "isSupplementTo"}]})),
                 "the intent does not name the problem of the journal"),
                ("metadata that is not an object", dict(agreeing, binding=_build_intent_binding(metadata="text")),
                 "the intent does not name the problem of the journal")):
            with self.subTest(case=label):
                self.assertEqual(_draft._find_intent_flaw(intent, journal), flaw_msg)

    def test_a_journal_executes_no_intent_that_is_another_operation(self) -> None:
        other_problem = {"related_identifiers": [{"identifier": f"https://api.test/math/{_OTHER_PROBLEM_ID}",
                                                  "relation": "references"}]}
        problem_on_another_origin = {"related_identifiers": [{"identifier": f"https://evil.test/math/{_PROBLEM_ID}",
                                                              "relation": "references"}]}
        problem_flaw_msg = "the intent does not name the problem of the journal"
        for index, (label, changes, flaw_msg) in enumerate((
                ("another host", {"base_url": "https://evil.test/api"},
                 "the intent names the Zenodo address https://evil.test/api, and the journal names"
                 " https://zenodo.org/api"),
                ("the sandbox", {"environment": "sandbox", "base_url": "https://sandbox.zenodo.org/api"},
                 "the intent is for the sandbox environment, and the journal names the production environment"),
                ("an environment that is not Zenodo's", {"environment": "staging"},
                 "the intent is for the staging environment, and the journal names the production environment"),
                ("another problem", {"metadata": other_problem}, problem_flaw_msg),
                ("the problem on another origin", {"metadata": problem_on_another_origin}, problem_flaw_msg),
                ("no problem", {"metadata": {}}, problem_flaw_msg))):
            intent_id = self._save_intent(f"hostile-{index}", **changes)
            journal = _build_managed_journal(intent_id)
            for argv in (["deposit", "--abstract-file", "draft/abstract.txt", *_PROBLEM_DEPOSIT], ["attach-problem"],
                         ["reconcile", intent_id]):
                with self.subTest(intent=label, command=argv[0]):
                    self._record_journal(journal)

                    output = _run_draft_command(argv, 1, self)

                    self.assertIn(f"{_JOURNAL_PATH_TEXT} is not a journal this command can trust: {flaw_msg}.", output)
                    self.assertIn(f"{_REMOVAL_RULE_MSG} the deposition it names is not published, or decide not to publish"
                                  " it. The command never removes it. Nothing was sent.", output)
                    # The refusal is the whole answer: the recovery note does not follow it.
                    self.assertNotIn("stays as the recovery record", output)
                    self.assertEqual(self.fake_api.requests, [])
                    self.assertEqual(self.exactory.calls, [])
                    self.assertEqual(_read_journal(self.workspace_dir), journal)

    def test_reconcile_executes_no_intent_that_names_an_address_or_environment_of_no_zenodo_deposit(self) -> None:
        for index, (label, changes, flaw_msg) in enumerate((
                ("another host", {"base_url": "https://evil.test/api"},
                 "the intent names the Zenodo address https://evil.test/api, which is not https://zenodo.org/api, the"
                 " Zenodo API of the production environment"),
                ("the sandbox environment at the production address",
                 {"environment": "sandbox", "base_url": "https://zenodo.org/api"},
                 "the intent names the Zenodo address https://zenodo.org/api, which is not"
                 " https://sandbox.zenodo.org/api, the Zenodo API of the sandbox environment"),
                ("an environment that is not Zenodo's", {"environment": "staging"},
                 "the intent names no Zenodo environment that this command knows"))):
            intent_id = self._save_intent(f"unnamed-{index}", **changes)
            with self.subTest(intent=label):
                output = _run_draft_command(["reconcile", intent_id], 1, self)

                self.assertIn(f"The command does not execute intent {intent_id}: {flaw_msg}. Nothing was sent, and no token"
                              " was read.", output)
                self.assertEqual(self.fake_api.requests, [])

    def test_a_record_is_the_journals_when_its_environment_and_doi_prefix_agree(self) -> None:
        production = _build_managed_journal("a" * 32)
        sandbox = dict(production, environment="sandbox", base_url="https://sandbox.zenodo.org/api")

        for journal, record, flaw_msg in (
                (production, {"environment": "production", "doi": "10.5281/zenodo.4242"}, None),
                (sandbox, {"environment": "sandbox", "doi": "10.5072/zenodo.4242"}, None),
                (production, {"environment": "sandbox", "doi": "10.5072/zenodo.4242"},
                 "the record is for the sandbox environment, and the journal names the production environment"),
                (production, {"environment": "production", "doi": "10.5072/zenodo.4242"},
                 "the DOI 10.5072/zenodo.4242 does not start with 10.5281/, the DOI prefix of the production environment"),
                (sandbox, {"environment": "sandbox", "doi": "10.5281/zenodo.4242"},
                 "the DOI 10.5281/zenodo.4242 does not start with 10.5072/, the DOI prefix of the sandbox environment")):
            with self.subTest(journal=journal["environment"], record=record):
                self.assertEqual(_draft._find_record_flaw(journal, record), flaw_msg)


class TestHostileZenodoLinks(_PlainWorkspaceDepositTestCase):
    """A link in an answer of Zenodo is an address like any other: the token goes only to the Zenodo API."""

    def setUp(self) -> None:
        super().setUp()
        self.exactory = _install_exactory(self)

    def test_a_file_bucket_on_another_host_gets_neither_the_paper_nor_the_token(self) -> None:
        self.fake_api = _HostileBucketZenodoApi()
        _draft._open_url = self.fake_api

        output = self._deposit(_PROBLEM_DEPOSIT, expected_exit_code=1)

        self.assertIn("The address https://evil.test/api/files/bucket-1/paper.pdf is not under https://zenodo.org/api,"
                      " the Zenodo API of the production environment. The command sent nothing to it, and the token"
                      " stayed here.", output)
        self.assertEqual(self.requested(), [("POST", "https://zenodo.org/api/deposit/depositions")])
        self.assertNotIn("production-token", output)
        self.assertFalse((self.workspace_dir / ".exactory" / "problem-deposit.json").exists())

    def test_a_new_version_draft_on_another_host_gets_no_request_and_no_token(self) -> None:
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        self.fake_api = _HostileDraftLinkZenodoApi()
        _draft._open_url = self.fake_api

        output = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)

        self.assertIn("The address https://evil.test/api/deposit/depositions/4343 is not under"
                      " https://sandbox.zenodo.org/api, the Zenodo API of the sandbox environment. The command sent"
                      " nothing to it, and the token stayed here.", output)
        self.assertEqual(self.requested(), [("POST", "https://sandbox.zenodo.org/api/deposit/depositions/4242/actions/newversion")])
        self.assertNotIn("sandbox-token", output)


if __name__ == "__main__":
    unittest.main()
