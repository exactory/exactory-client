"""Tests for bin/exactory-draft: the init layout and the Zenodo deposit flow."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import urllib.error
import zipfile
from pathlib import Path

from integration_fixtures import prepare_research, prepare_manuscript
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.storage import Store

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _load_bin_module(command_name: str, module_name: str):
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(_PLUGIN_ROOT / "bin" / command_name)
    )
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_draft = _load_bin_module("exactory-draft", "exactory_draft")

_WORKSPACE_DIR_NAMES = (".exactory", "draft", "evidence", "research", "reviews", "learnings")


def _invoke_draft_command(argv: list[str], expected_exit_code: int | None,
                          test_case: unittest.TestCase,
                          out_sink: io.StringIO, err_sink: io.StringIO) -> None:
    args = _draft._build_parser().parse_args(argv)
    def invoke():
        try:
            args.handler(args)
        except ResearchError as error:
            _draft._exit_with_error(json.dumps({"error": error.as_dict()}))
    with contextlib.redirect_stdout(out_sink), contextlib.redirect_stderr(err_sink):
        if expected_exit_code is None:
            invoke()
        else:
            with test_case.assertRaises(SystemExit) as caught:
                invoke()
            test_case.assertEqual(caught.exception.code, expected_exit_code)


def _run_draft_command(argv: list[str], expected_exit_code: int | None,
                       test_case: unittest.TestCase) -> str:
    sink = io.StringIO()
    _invoke_draft_command(argv, expected_exit_code, test_case, sink, sink)
    return sink.getvalue()


def _run_draft_command_on_split_streams(argv: list[str], expected_exit_code: int | None,
                                        test_case: unittest.TestCase) -> tuple[str, str]:
    """Run the command with stdout and stderr apart, and return the two texts.

    An agent captures the two streams separately, so every instruction on stderr
    carries what it names instead of pointing at a line on the other stream."""
    out_sink, err_sink = io.StringIO(), io.StringIO()
    _invoke_draft_command(argv, expected_exit_code, test_case, out_sink, err_sink)
    return out_sink.getvalue(), err_sink.getvalue()


class _FakeZenodoApi:
    """Answer Zenodo API requests in memory and record every request."""

    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        url = request.full_url
        method = request.get_method()
        base_url = url.split("/api/")[0] + "/api"
        if method == "POST" and url.endswith("/deposit/depositions"):
            return {
                "id": 4242,
                "links": {
                    "bucket": f"{base_url}/files/bucket-1",
                    "html": f"{base_url}/deposit/4242",
                },
            }
        if method == "PUT" and "/files/bucket-1/" in url:
            return {}
        if method == "GET" and url.endswith("/records/4242/draft"):
            return {"metadata": {"title": "Cohort Percentiles"},
                    "files": {"enabled": True}}
        if method == "PUT" and url.endswith("/records/4242/draft"):
            return {}
        if method == "PUT" and url.endswith("/deposit/depositions/4242"):
            return {"metadata": {"prereserve_doi": {"doi": "10.5281/zenodo.4242"}}}
        if method == "POST" and url.endswith("/deposit/depositions/4242/actions/publish"):
            return {
                "id": 4242, "doi": "10.5281/zenodo.4242",
                "conceptdoi": "10.5281/zenodo.4241",
                "links": {"record_html": f"{base_url}/records/4242"},
            }
        if method == "POST" and url.endswith("/deposit/depositions/4242/actions/newversion"):
            return {"links": {"latest_draft": f"{base_url}/deposit/depositions/4343"}}
        if method == "GET" and url.endswith("/deposit/depositions/4343"):
            return {
                "id": 4343,
                "links": {
                    "bucket": f"{base_url}/files/bucket-2",
                    "html": f"{base_url}/deposit/4343",
                },
            }
        if method == "PUT" and "/files/bucket-2/" in url:
            return {}
        if method == "GET" and url.endswith("/records/4343/draft"):
            return {"metadata": {"title": "Cohort Percentiles"},
                    "files": {"enabled": True}}
        if method == "PUT" and url.endswith("/records/4343/draft"):
            return {}
        if method == "PUT" and url.endswith("/deposit/depositions/4343"):
            return {"metadata": {"prereserve_doi": {"doi": "10.5281/zenodo.4343"}}}
        if method == "POST" and url.endswith("/deposit/depositions/4343/actions/publish"):
            return {
                "id": 4343, "doi": "10.5281/zenodo.4343",
                "conceptdoi": "10.5281/zenodo.4241",
                "links": {"record_html": f"{base_url}/records/4343"},
            }
        raise AssertionError(f"unexpected Zenodo request in test: {method} {url}")


class _StdoutRecordingZenodoApi(_FakeZenodoApi):
    """Answer like the fake above, and keep what the command had printed when
    the publish request went out. A published record is permanent, so the test
    measures what the user holds before the response that can be lost."""

    def __init__(self) -> None:
        super().__init__()
        self.printed_before_publish = ""

    def __call__(self, request):
        if request.full_url.endswith("/actions/publish"):
            self.printed_before_publish = sys.stdout.getvalue()
        return super().__call__(request)


class _LostResponseZenodoApi(_FakeZenodoApi):
    """Answer like the fake above until one named request, whose response is
    lost on the way back. Zenodo may have acted on it already and the client
    cannot tell, which is the state the message on the screen has to answer."""

    def __init__(self, lost_url_suffix: str) -> None:
        super().__init__()
        self.lost_url_suffix = lost_url_suffix

    def __call__(self, request):
        if request.full_url.endswith(self.lost_url_suffix):
            self.requests.append(request)
            raise OSError("connection reset by peer")
        return super().__call__(request)


class _StatusErrorZenodoApi(_FakeZenodoApi):
    """Answer like the fake above until one named request, which the API answers
    with a status instead of a body. A 5xx states no outcome for the request,
    exactly as a lost response states none; a 4xx is the server's decision."""

    def __init__(self, failing_url_suffix: str, status_code: int) -> None:
        super().__init__()
        self.failing_url_suffix = failing_url_suffix
        self.status_code = status_code

    def __call__(self, request):
        if request.full_url.endswith(self.failing_url_suffix):
            self.requests.append(request)
            raise urllib.error.HTTPError(request.full_url, self.status_code, "Zenodo error",
                                         {}, io.BytesIO(b"the gateway answered"))
        return super().__call__(request)


class _PublishedRecordZenodoApi(_FakeZenodoApi):
    """Answer the reconciliation read of a deposition that is published: the
    record carries its DOI and the file the interrupted run uploaded."""

    def __init__(self, uploaded_bytes: bytes) -> None:
        super().__init__()
        self.uploaded_bytes = uploaded_bytes

    def __call__(self, request):
        if request.get_method() == "GET" and request.full_url.endswith("/deposit/depositions/4242"):
            self.requests.append(request)
            return {
                "id": 4242, "submitted": True, "doi": "10.5281/zenodo.4242",
                "conceptdoi": "10.5281/zenodo.4241",
                "links": {"record_html": "https://sandbox.zenodo.org/api/records/4242"},
                "files": [{"filename": "paper.pdf", "checksum": "md5:" + hashlib.md5(
                    self.uploaded_bytes, usedforsecurity=False).hexdigest()}],
            }
        return super().__call__(request)


class _UnpublishedRecordZenodoApi(_FakeZenodoApi):
    """Answer the reconciliation read of a deposition that is still a draft:
    the publish request never reached Zenodo, so the record holds the file the
    interrupted run uploaded and reads back unpublished."""

    def __init__(self, uploaded_bytes: bytes) -> None:
        super().__init__()
        self.uploaded_bytes = uploaded_bytes

    def __call__(self, request):
        if request.get_method() == "GET" and request.full_url.endswith("/deposit/depositions/4242"):
            self.requests.append(request)
            return {
                "id": 4242, "submitted": False,
                "links": {"bucket": "https://sandbox.zenodo.org/api/files/bucket-1",
                          "html": "https://sandbox.zenodo.org/api/deposit/4242"},
                "files": [{"filename": "paper.pdf", "checksum": "md5:" + hashlib.md5(
                    self.uploaded_bytes, usedforsecurity=False).hexdigest()}],
            }
        return super().__call__(request)


class _FirstRequestRecordingZenodoApi(_FakeZenodoApi):
    """Answer like the fake above, and keep what the command had printed when
    the first request went out. The citation report informs the user about the
    record that is about to exist, so it reaches the screen before that record
    does."""

    def __init__(self) -> None:
        super().__init__()
        self.printed_before_the_first_request = ""

    def __call__(self, request):
        if not self.requests:
            self.printed_before_the_first_request = sys.stderr.getvalue()
        return super().__call__(request)


def _read_pinned_abstract_bytes(workspace_dir: Path) -> bytes:
    """The abstract bytes the publication bundle pinned. The managed deposit of
    0.39.x built the record's description from these bytes, so they are the
    reference the description keeps matching."""
    store = Store(workspace_dir)
    records = store.snapshot()["records"]
    bundle = records["publication_bundle"][records["publication_selection"]["bundle"]["id"]]
    return ArtifactStore(store.root).read(bundle["files"]["abstract"]["artifact"])


def _write_passing_citation_report(workspace_dir: Path) -> None:
    """Write a references file and a fresh, clean citation report for it."""
    bib_path = workspace_dir / "draft" / "references.bib"
    bib_path.write_text(
        "@article{instance2023predicting,\n"
        "  title={Predicting Citation Impact with Cohort Percentiles},\n"
        "  author={Carol Instance and Dana Case},\n"
        "  year={2023},\n"
        "  doi={10.1234/exact.5678}\n"
        "}\n"
    )
    report = {
        "version": 1,
        "bib_sha256": hashlib.sha256(bib_path.read_bytes()).hexdigest(),
        "checked_at": "2026-08-08T00:00:00Z",
        "entries": [{
            "key": "instance2023predicting", "doi": "10.1234/exact.5678", "arxiv_id": "",
            "title": "Predicting Citation Impact with Cohort Percentiles",
            "status": "verified", "detail": "",
        }],
        "counts": {"verified": 1, "blocking": 0, "warning": 0},
        "blocking": 0,
        "nothing_verified": False,
        "ok": True,
        # What lookup records for a draft whose manuscript is paper.tex alone, or none.
        "manuscript": {
            "main": "paper.tex" if (bib_path.parent / "paper.tex").is_file() else None,
            "tex_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in [bib_path.parent / "paper.tex"] if path.is_file()},
            "uncited_keys": [], "prior_art_without_citation": [],
        },
    }
    (workspace_dir / ".exactory" / "citation-check.json").write_text(
        json.dumps(report, indent=2)
    )


# The two disclosures, spelled out here so a change to either wording fails a
# test instead of reaching a permanent Zenodo record. Every deposit test names
# "Shiroshita, Ryosuke" first, so that is the author each sentence carries.
_WRITTEN_BY_EXACTORY_SENTENCE = (
    "This preprint was written by exactory.ai (https://www.exactory.ai), an AI"
    " research system. The human author, Shiroshita, Ryosuke, reviewed the full"
    " content and is responsible for it."
)
_DEPOSITED_THROUGH_EXACTORY_SENTENCE = (
    "This preprint was prepared with AI assistance and deposited through"
    " exactory.ai (https://www.exactory.ai). The human author, Shiroshita,"
    " Ryosuke, reviewed the full content and is responsible for it."
)

# The keyword the record carries with the first disclosure, spelled out here
# for the same reason.
_WRITTEN_BY_EXACTORY_KEYWORD = "Written by exactory.ai"

_AGENT_WROTE_THE_PAPER_RECORD_TEXT = json.dumps({"written_by_exactory": True})
_AUTHORSHIP_RECORDER_SCRIPT_PATH = _PLUGIN_ROOT / "hooks" / "record_paper_authorship.py"


def _write_authorship_record(workspace_dir: Path, record_text: str) -> None:
    """Write .exactory/authorship.json, the record the record_paper_authorship
    hook writes when an agent writes a LaTeX source under draft/. It takes the
    file's text, so a malformed record is as easy to set up as a valid one."""
    (workspace_dir / ".exactory" / "authorship.json").write_text(
        record_text, encoding="utf-8"
    )


def _link_deposit_state_elsewhere(workspace_dir: Path) -> Path:
    """Make .exactory/deposit.json a symlink and return the file it names.

    A writer that follows the symlink overwrites the named file and leaves the
    link in place; the projection primitive replaces the link itself."""
    elsewhere_path = workspace_dir / "draft" / "elsewhere.json"
    elsewhere_path.write_text("untouched\n", encoding="utf-8")
    (workspace_dir / ".exactory" / "deposit.json").symlink_to(elsewhere_path)
    return elsewhere_path


def _read_request(requests, method: str, url_suffix: str):
    """Read the first recorded request a test names.

    The match is checked here, so a test that names a request the run never sent
    fails with that name instead of ending in StopIteration."""
    for request in requests:
        if request.get_method() == method and request.full_url.endswith(url_suffix):
            return request
    raise AssertionError(f"The run sent no {method} to a URL ending {url_suffix}.")


def _refuse_the_direct_deposit_record(store, state, *, expected_revision, request_id):
    """Stand in for record_direct_deposit when the store cannot take the write."""
    raise ResearchError("store_busy", "Research store is busy; retry the same"
                                      " request after the writer finishes")


class TestInit(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace_dir = Path(scratch.name)

    def _init(self, expected_exit_code: int | None = None) -> str:
        return _run_draft_command(
            ["init", "--dir", str(self.workspace_dir),
             "--title", "Cohort Percentiles", "--category", "cs.MA"],
            expected_exit_code,
            self,
        )

    def test_init_creates_the_workspace_layout_and_the_seed_files(self) -> None:
        self._init()
        for dir_name in _WORKSPACE_DIR_NAMES:
            self.assertTrue((self.workspace_dir / dir_name).is_dir(), dir_name)
        state = json.loads((self.workspace_dir / ".exactory" / "draft.json").read_text())
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["title"], "Cohort Percentiles")
        self.assertEqual(state["corpus"], "arxiv")
        self.assertEqual(state["category"], "cs.MA")
        self.assertRegex(state["created"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(set(state), {"version", "title", "corpus", "category", "created", "research"})
        literature_lines = (
            (self.workspace_dir / "research" / "literature.md").read_text().splitlines()
        )
        self.assertEqual(len(literature_lines), 2)
        self.assertTrue(literature_lines[0].startswith("#"))

    def test_init_refuses_an_existing_workspace(self) -> None:
        self._init()
        stderr_text = self._init(expected_exit_code=1)
        self.assertIn("draft.json", stderr_text)


class _DepositTestCase(unittest.TestCase):
    prepare_stop = True

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace_dir = Path(scratch.name)
        _run_draft_command(
            ["init", "--dir", str(self.workspace_dir),
             "--title", "Cohort Percentiles", "--category", "cs.MA"],
            None,
            self,
        )
        (self.workspace_dir / "draft" / "paper.pdf").write_bytes(b"%PDF-1.4 fake paper")
        (self.workspace_dir / "draft" / "abstract.txt").write_text(
            "We predict cohort percentiles & bound their error.\n\n"
            "A second paragraph states the limits.\n"
        )
        _write_passing_citation_report(self.workspace_dir)
        self.research = prepare_research(self.workspace_dir, candidate=True)
        prepare_manuscript(self.research, stop=self.prepare_stop)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.workspace_dir)

        self.fake_api = _FakeZenodoApi()
        self.addCleanup(setattr, _draft, "_open_url", _draft._open_url)
        _draft._open_url = self.fake_api

        env_patcher = unittest.mock.patch.dict(
            os.environ,
            {"ZENODO_SANDBOX_TOKEN": "sandbox-token", "ZENODO_TOKEN": "production-token"},
            clear=True,
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _deposit(self, argv_tail: list[str], expected_exit_code: int | None = None) -> str:
        return _run_draft_command(
            ["deposit", "--abstract-file", "draft/abstract.txt", *argv_tail],
            expected_exit_code, self,
        )


class _PlainWorkspaceDepositTestCase(unittest.TestCase):
    """A draft workspace as `exactory-draft init` leaves it: a store, no research."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.workspace_dir = Path(scratch.name)
        _run_draft_command(
            ["init", "--dir", str(self.workspace_dir),
             "--title", "Cohort Percentiles", "--category", "cs.MA"],
            None,
            self,
        )
        (self.workspace_dir / "draft" / "paper.pdf").write_bytes(b"%PDF-1.4 fake paper")
        (self.workspace_dir / "draft" / "abstract.txt").write_text(
            "We predict cohort percentiles & bound their error.\n"
        )
        _write_passing_citation_report(self.workspace_dir)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.workspace_dir)
        self.fake_api = _FakeZenodoApi()
        self.addCleanup(setattr, _draft, "_open_url", _draft._open_url)
        _draft._open_url = self.fake_api
        env_patcher = unittest.mock.patch.dict(
            os.environ,
            {"ZENODO_SANDBOX_TOKEN": "sandbox-token", "ZENODO_TOKEN": "production-token"},
            clear=True,
        )
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _deposit(self, argv_tail: list[str], expected_exit_code: int | None = None) -> str:
        return _run_draft_command(
            ["deposit", "--abstract-file", "draft/abstract.txt", *argv_tail],
            expected_exit_code, self,
        )

    def _deposit_on_split_streams(self, argv_tail: list[str],
                                  expected_exit_code: int | None = None) -> tuple[str, str]:
        return _run_draft_command_on_split_streams(
            ["deposit", "--abstract-file", "draft/abstract.txt", *argv_tail],
            expected_exit_code, self,
        )

    def read_deposit_state(self) -> dict:
        return json.loads((self.workspace_dir / ".exactory" / "deposit.json").read_text())

    def requested(self) -> list[tuple[str, str]]:
        return [(request.get_method(), request.full_url) for request in self.fake_api.requests]


class _LegacyWorkspaceDepositTestCase(_PlainWorkspaceDepositTestCase):
    """A draft workspace from before 0.38.0: draft.json and no store."""

    def setUp(self) -> None:
        super().setUp()
        # The store and its journal files, which Store() looks for together.
        for path in (self.workspace_dir / ".exactory").glob("research.sqlite3*"):
            path.unlink()


class TestDeposit(_DepositTestCase):
    def _read_sent_metadata(self) -> dict:
        metadata_request = _read_request(self.fake_api.requests, "PUT",
                                         "/deposit/depositions/4242")
        return json.loads(metadata_request.data.decode())["metadata"]

    def test_deposit_targets_the_sandbox_by_default(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        first_request = self.fake_api.requests[0]
        self.assertTrue(first_request.full_url.startswith("https://sandbox.zenodo.org/api/"))
        self.assertEqual(first_request.get_header("Authorization"), "Bearer sandbox-token")

    def test_production_flag_targets_zenodo_org_with_the_production_token(self) -> None:
        self._deposit(["--production", "--creator", "Shiroshita, Ryosuke"])
        first_request = self.fake_api.requests[0]
        self.assertTrue(first_request.full_url.startswith("https://zenodo.org/api/"))
        self.assertEqual(first_request.get_header("Authorization"), "Bearer production-token")

    def test_missing_token_error_names_the_variable(self) -> None:
        del os.environ["ZENODO_SANDBOX_TOKEN"]
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn("ZENODO_SANDBOX_TOKEN", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_metadata_carries_the_contract_fields_and_the_disclosure(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke", "--creator", "Example, Alice"])
        metadata = self._read_sent_metadata()
        self.assertEqual(metadata["upload_type"], "publication")
        self.assertEqual(metadata["publication_type"], "preprint")
        self.assertEqual(metadata["title"], "Cohort Percentiles")
        self.assertEqual(
            metadata["creators"],
            [{"name": "Shiroshita, Ryosuke"}, {"name": "Example, Alice"}],
        )
        # This workspace holds no authorship record, so the disclosure states
        # only that the deposit went through exactory.ai.
        self.assertIn(_DEPOSITED_THROUGH_EXACTORY_SENTENCE, metadata["description"])

    def test_description_opens_with_the_abstract_and_ends_with_the_disclosure(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        description = self._read_sent_metadata()["description"]
        self.assertTrue(description.startswith("<p>"))
        self.assertIn("cohort percentiles &amp; bound their error", description)
        self.assertIn("second paragraph", description)
        self.assertLess(
            description.find("second paragraph"),
            description.find(_DEPOSITED_THROUGH_EXACTORY_SENTENCE),
        )
        self.assertTrue(description.endswith("responsible for it.</p>"))

    def test_an_abstract_with_crlf_line_endings_describes_the_record_as_the_pinned_bytes_do(self) -> None:
        """A Windows abstract reaches Zenodo with the description the pinned
        bytes produce, the one 0.39.x sent. The description is part of the
        deposit intent's fingerprint, so a description that differs by the line
        endings alone stops an interrupted 0.39.x deposit from resuming and
        creates a second record."""
        abstract_path = self.workspace_dir / "draft" / "abstract.txt"
        abstract_path.write_bytes(b"We predict cohort percentiles & bound their error.\r\n\r\n"
                                  b"A second paragraph states the limits.\r\n")
        prepare_manuscript(self.research, stop=True)
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        description = self._read_sent_metadata()["description"]
        pinned_text = _read_pinned_abstract_bytes(self.workspace_dir).decode("utf-8").strip()
        self.assertEqual(description,
                         _draft._build_deposit_metadata("Cohort Percentiles", ["Shiroshita, Ryosuke"],
                                                        pinned_text, False)["description"])
        # Both paragraphs reach the record. A CRLF file holds no "\n\n", which
        # is the paragraph separator, so they arrive as one paragraph, exactly
        # as they did when the bundle's bytes fed the description.
        self.assertIn("<p>We predict cohort percentiles &amp; bound their error."
                      " A second paragraph states the limits.</p>", description)

    def _assert_claims_only_the_deposit(self, metadata: dict) -> None:
        """Assert the record says nothing about who wrote the paper. The gate
        selects the disclosure and the keyword together, so every doubtful
        input is checked on both."""
        self.assertIn(_DEPOSITED_THROUGH_EXACTORY_SENTENCE, metadata["description"])
        self.assertNotIn("written by exactory.ai", metadata["description"])
        self.assertNotIn("keywords", metadata)

    def test_a_recorded_agent_write_names_exactory_as_the_writer(self) -> None:
        _write_authorship_record(self.workspace_dir, _AGENT_WROTE_THE_PAPER_RECORD_TEXT)
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        metadata = self._read_sent_metadata()
        description = metadata["description"]
        self.assertIn(_WRITTEN_BY_EXACTORY_SENTENCE, description)
        self.assertNotIn("AI assistance", description)
        # assertEqual rather than assertIn, so a second keyword added to the
        # record fails this test.
        self.assertEqual(metadata["keywords"], [_WRITTEN_BY_EXACTORY_KEYWORD])
        # The disclosure still closes the description, after the abstract.
        self.assertLess(
            description.find("second paragraph"),
            description.find(_WRITTEN_BY_EXACTORY_SENTENCE),
        )
        self.assertTrue(description.endswith("responsible for it.</p>"))

    def test_the_record_the_hook_writes_is_the_record_this_command_reads(self) -> None:
        """Run the real hook on a paper source, then deposit. The hook and this
        command name the same file and the same key from two files, so a rename
        on one side alone lands here instead of in a Zenodo record."""
        paper_source_path = self.workspace_dir / "draft" / "paper.tex"
        paper_source_path.write_text("\\section{Results}\n")
        subprocess.run(
            [sys.executable, str(_AUTHORSHIP_RECORDER_SCRIPT_PATH)],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": str(paper_source_path)},
                "cwd": str(self.workspace_dir),
            }),
            text=True, capture_output=True, check=True,
        )
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        metadata = self._read_sent_metadata()
        self.assertIn(_WRITTEN_BY_EXACTORY_SENTENCE, metadata["description"])
        self.assertEqual(metadata["keywords"], [_WRITTEN_BY_EXACTORY_KEYWORD])

    def test_a_workspace_without_an_authorship_record_claims_only_the_deposit(self) -> None:
        self.assertFalse((self.workspace_dir / ".exactory" / "authorship.json").exists())
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self._assert_claims_only_the_deposit(self._read_sent_metadata())

    def test_an_authorship_record_turned_off_claims_only_the_deposit(self) -> None:
        # The value decides, not the file's presence, so a person who sets the
        # record to false turns the claim off.
        _write_authorship_record(self.workspace_dir,
                                 json.dumps({"written_by_exactory": False}))
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self._assert_claims_only_the_deposit(self._read_sent_metadata())

    def test_an_authorship_record_without_the_key_claims_only_the_deposit(self) -> None:
        _write_authorship_record(self.workspace_dir, json.dumps({"version": 1}))
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self._assert_claims_only_the_deposit(self._read_sent_metadata())

    def test_a_truthy_non_boolean_authorship_value_claims_only_the_deposit(self) -> None:
        # 1 is truthy and it also equals True, so this value fails both a
        # truthiness read and an == True read. Only "is True" answers False.
        _write_authorship_record(self.workspace_dir, json.dumps({"written_by_exactory": 1}))
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self._assert_claims_only_the_deposit(self._read_sent_metadata())

    def test_an_authorship_record_of_the_wrong_type_claims_only_the_deposit(self) -> None:
        _write_authorship_record(self.workspace_dir, json.dumps(["written_by_exactory"]))
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self._assert_claims_only_the_deposit(self._read_sent_metadata())

    def test_a_malformed_authorship_record_claims_only_the_deposit(self) -> None:
        _write_authorship_record(self.workspace_dir, "this file is not JSON at all")
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self._assert_claims_only_the_deposit(self._read_sent_metadata())

    def test_an_unreadable_authorship_record_claims_only_the_deposit(self) -> None:
        # A directory on the record's path makes the read raise, and a read
        # that raises answers the same way every other doubtful input does.
        (self.workspace_dir / ".exactory" / "authorship.json").mkdir()
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self._assert_claims_only_the_deposit(self._read_sent_metadata())

    def _write_pdf_outside_the_workspace(self) -> Path:
        """Write a PDF in a directory of its own, outside this workspace."""
        outside_scratch = tempfile.TemporaryDirectory()
        self.addCleanup(outside_scratch.cleanup)
        outside_pdf_path = Path(outside_scratch.name) / "paper.pdf"
        outside_pdf_path.write_bytes(b"%PDF-1.4 fake paper from elsewhere")
        return outside_pdf_path

    def test_a_pdf_from_outside_the_draft_tree_moves_the_deposit_to_the_direct_path(self) -> None:
        _write_authorship_record(self.workspace_dir, _AGENT_WROTE_THE_PAPER_RECORD_TEXT)
        outside = self._write_pdf_outside_the_workspace()
        self.assertFalse(_draft._has_exactory_authorship_evidence(outside))
        output = self._deposit(["--creator", "Shiroshita, Ryosuke", "--pdf", str(outside)])
        self.assertIn("Managed record skipped (publication_artifact_mismatch): ", output)
        self.assertEqual(self.fake_api.requests[0].get_method(), "POST")
        self.assertNotIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])

    def test_a_pdf_symlinked_out_of_the_draft_tree_moves_the_deposit_to_the_direct_path(self) -> None:
        _write_authorship_record(self.workspace_dir, _AGENT_WROTE_THE_PAPER_RECORD_TEXT)
        paper_path = self.workspace_dir / "draft" / "paper.pdf"
        paper_path.unlink()
        paper_path.symlink_to(self._write_pdf_outside_the_workspace())
        self.assertFalse(_draft._has_exactory_authorship_evidence(paper_path))
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Managed record skipped (", output)
        self.assertEqual(self.fake_api.requests[0].get_method(), "POST")
        self.assertNotIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])

    def test_a_blank_abstract_file_is_refused_before_any_request(self) -> None:
        (self.workspace_dir / "draft" / "abstract.txt").write_text(" \n\n")
        stderr_text = self._deposit(
            ["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2
        )
        self.assertIn("abstract", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_missing_abstract_file_is_refused_before_any_request(self) -> None:
        stderr_text = _run_draft_command(
            ["deposit", "--abstract-file", "draft/nothing-here.txt",
             "--creator", "Shiroshita, Ryosuke"],
            2, self,
        )
        self.assertIn("nothing-here.txt", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_the_pdf_uploads_under_the_fixed_name_paper_pdf(self) -> None:
        (self.workspace_dir / "draft" / "paper.pdf").rename(
            self.workspace_dir / "draft" / "main.pdf"
        )
        prepare_manuscript(self.research, pdf="draft/main.pdf", stop=True)
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        upload_urls = [
            request.full_url for request in self.fake_api.requests
            if request.get_method() == "PUT" and "/files/bucket-1/" in request.full_url
        ]
        self.assertEqual(upload_urls, ["https://sandbox.zenodo.org/api/files/bucket-1/paper.pdf"])

    def test_every_deposit_marks_the_paper_as_the_default_preview(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        read_request = _read_request(self.fake_api.requests, "GET", "/records/4242/draft")
        self.assertEqual(read_request.get_header("Accept"),
                         "application/vnd.inveniordm.v1+json")
        write_request = _read_request(self.fake_api.requests, "PUT", "/records/4242/draft")
        document = json.loads(write_request.data.decode())
        self.assertEqual(document["files"]["default_preview"], "paper.pdf")
        # The whole draft document goes back, so the PUT replaces nothing else.
        self.assertEqual(document["metadata"], {"title": "Cohort Percentiles"})

    def test_a_tarball_keeps_its_archive_suffix_in_the_supplementary_name(self) -> None:
        sources_path = self.workspace_dir / "code.tar.gz"
        sources_path.write_bytes(b"fake tarball")
        prepare_manuscript(self.research, sources="code.tar.gz", stop=True)
        self._deposit(["--creator", "Shiroshita, Ryosuke", "--sources", str(sources_path)])
        upload_urls = [
            request.full_url for request in self.fake_api.requests
            if request.get_method() == "PUT" and "/files/bucket-1/" in request.full_url
        ]
        self.assertIn(
            "https://sandbox.zenodo.org/api/files/bucket-1/supplementary-sources.tar.gz",
            upload_urls,
        )

    def test_deposit_uploads_the_newest_pdf(self) -> None:
        older_pdf = self.workspace_dir / "draft" / "old.pdf"
        older_pdf.write_bytes(b"%PDF-1.4 stale")
        stale_time = time.time() - 1000
        os.utime(older_pdf, (stale_time, stale_time))
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        upload_urls = [
            request.full_url for request in self.fake_api.requests
            if request.get_method() == "PUT" and "/files/bucket-1/" in request.full_url
        ]
        self.assertEqual(upload_urls, ["https://sandbox.zenodo.org/api/files/bucket-1/paper.pdf"])

    def test_sources_archive_uploads_under_the_supplementary_name(self) -> None:
        sources_path = self.workspace_dir / "sources.zip"
        sources_path.write_bytes(b"PK fake zip")
        prepare_manuscript(self.research, sources="sources.zip", stop=True)
        self._deposit(["--creator", "Shiroshita, Ryosuke", "--sources", str(sources_path)])
        upload_urls = [
            request.full_url for request in self.fake_api.requests
            if request.get_method() == "PUT" and "/files/bucket-1/" in request.full_url
        ]
        # 'supplementary' sorts after 'paper', so the paper stays first in the
        # record's alphabetical file list.
        self.assertIn(
            "https://sandbox.zenodo.org/api/files/bucket-1/supplementary-sources.zip",
            upload_urls,
        )

    def test_deposit_stays_a_draft_by_default_and_prints_the_deposition_url(self) -> None:
        stdout_text = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        publish_urls = [
            request.full_url for request in self.fake_api.requests
            if request.full_url.endswith("/actions/publish")
        ]
        self.assertEqual(publish_urls, [])
        self.assertIn("deposit/4242", stdout_text)
        # DOIs do not exist yet: the output says they arrive on publish.
        self.assertIn("publish", stdout_text)
        self.assertNotIn("10.5281/zenodo.4242", stdout_text)

    def test_a_ready_study_writes_the_publication_receipt(self) -> None:
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        self.assertNotIn("Managed record skipped", output)
        receipts = Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(next(iter(receipts.values()))["doi"], "10.5281/zenodo.4242")


class TestProductionPublishConfirmation(_DepositTestCase):
    def test_refuses_without_the_confirm_publish_flag(self) -> None:
        stderr_text = self._deposit(
            ["--production", "--publish", "--creator", "Shiroshita, Ryosuke"],
            expected_exit_code=1,
        )
        self.assertIn("--confirm-publish", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_publishes_with_the_confirm_publish_flag_and_prints_both_dois(self) -> None:
        stdout_text = self._deposit(
            ["--production", "--publish", "--confirm-publish",
             "--creator", "Shiroshita, Ryosuke"]
        )
        publish_urls = [
            request.full_url for request in self.fake_api.requests
            if request.full_url.endswith("/actions/publish")
        ]
        self.assertEqual(len(publish_urls), 1)
        self.assertIn("10.5281/zenodo.4242", stdout_text)  # record DOI
        self.assertIn("10.5281/zenodo.4241", stdout_text)  # concept DOI
        self.assertIn("records/4242", stdout_text)

    def test_sandbox_publish_needs_no_confirmation_flag(self) -> None:
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        publish_urls = [
            request.full_url for request in self.fake_api.requests
            if request.full_url.endswith("/actions/publish")
        ]
        self.assertEqual(len(publish_urls), 1)


class TestManagedDepositAfterALostPublishResponse(_DepositTestCase):
    """The managed path holds the whole deposit as one saved intent, so the
    same command finishes it through remote reads. This is what the deposit
    skill sends an agent to do, and the record it protects is permanent."""

    def test_the_same_command_reads_the_record_and_sends_no_second_publish(self) -> None:
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        interrupted_output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                                           expected_exit_code=1)
        # The managed publish is resumable, so it keeps the plain advice, and
        # the deposition line belongs to the direct path alone.
        self.assertIn("Then run the command again.", interrupted_output)
        self.assertNotIn("is open on Zenodo", interrupted_output)
        uploaded_bytes = _read_request(self.fake_api.requests, "PUT",
                                       "/files/bucket-1/paper.pdf").data

        self.fake_api = _PublishedRecordZenodoApi(uploaded_bytes)
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        self.assertEqual([(request.get_method(), request.full_url) for request in self.fake_api.requests],
                         [("GET", "https://sandbox.zenodo.org/api/deposit/depositions/4242")])
        self.assertIn('"doi": "10.5281/zenodo.4242"', output)
        intents = Store(self.workspace_dir).snapshot()["records"]["remote_intent"]
        self.assertEqual(len(intents), 1)
        self.assertEqual(next(iter(intents.values()))["status"], "complete")
        receipts = Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(next(iter(receipts.values()))["doi"], "10.5281/zenodo.4242")

    def test_a_publish_that_never_landed_is_sent_to_the_same_record_and_finishes(self) -> None:
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        uploaded_bytes = _read_request(self.fake_api.requests, "PUT",
                                       "/files/bucket-1/paper.pdf").data

        self.fake_api = _UnpublishedRecordZenodoApi(uploaded_bytes)
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        # The record reads back as a draft, so the publish never landed. The
        # same deposition is published, which Zenodo publishes once, so the
        # deposit finishes on the record the interrupted run created.
        self.assertEqual([(request.get_method(), request.full_url) for request in self.fake_api.requests],
                         [("GET", "https://sandbox.zenodo.org/api/deposit/depositions/4242"),
                          ("POST", "https://sandbox.zenodo.org/api/deposit/depositions/4242/actions/publish")])
        self.assertIn('"doi": "10.5281/zenodo.4242"', output)
        intents = Store(self.workspace_dir).snapshot()["records"]["remote_intent"]
        self.assertEqual(len(intents), 1)
        intent = next(iter(intents.values()))
        self.assertEqual(intent["status"], "complete")
        self.assertEqual([entry["name"] for entry in intent["discarded"]], ["publish"])
        receipts = Store(self.workspace_dir).snapshot()["records"]["publication_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(next(iter(receipts.values()))["doi"], "10.5281/zenodo.4242")


class TestProductionDepositCitationReport(_DepositTestCase):
    def test_a_failing_report_is_printed_and_the_managed_deposit_continues(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Citation report: ", output)
        self.assertIn("exactory-check lookup", output)
        self.assertTrue(self.fake_api.requests[0].full_url.startswith("https://zenodo.org/api/"))
        self.assertIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])

    def test_the_report_reaches_the_user_before_the_first_remote_write(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        self.fake_api = _FirstRequestRecordingZenodoApi()
        _draft._open_url = self.fake_api
        self._deposit(["--production", "--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Citation report: ", self.fake_api.printed_before_the_first_request)

    def test_a_changed_bibliography_moves_the_deposit_to_the_direct_path(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        (self.workspace_dir / "draft" / "references.bib").unlink()
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Managed record skipped (", output)
        self.assertEqual(self.fake_api.requests[0].get_method(), "POST")
        self.assertNotIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])


class TestParserStrictness(unittest.TestCase):
    def test_an_abbreviated_flag_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _draft._build_parser().parse_args(
                    ["deposit", "--prod", "--creator", "Shiroshita, Ryosuke"]
                )
        self.assertEqual(caught.exception.code, 2)


class TestDepositPreconditions(_DepositTestCase):
    def test_deposit_outside_a_workspace_points_at_init(self) -> None:
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        os.chdir(outside_dir.name)
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn("init", stderr_text)

    def test_deposit_from_a_subdirectory_names_the_directory_that_holds_the_marker(self) -> None:
        # The command reads the title from .exactory/draft.json under the
        # current directory, so a run from a subdirectory finds no marker. The
        # message sends the user to the workspace root instead of to init,
        # which would make a second workspace inside this one.
        os.chdir(self.workspace_dir / "draft")
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn(".exactory/draft.json does not exist here.", stderr_text)
        self.assertIn("The deposit runs in the directory that holds it.", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_deposit_without_a_pdf_is_an_error(self) -> None:
        (self.workspace_dir / "draft" / "paper.pdf").unlink()
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn("--pdf", stderr_text)


class TestInitLiteraturePreservation(unittest.TestCase):
    def test_init_seeds_the_literature_log_only_when_absent(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        workspace_dir = Path(scratch.name)
        preserved_line = "## 2026-08-01T00:00Z - earlier pass\n"
        (workspace_dir / "research").mkdir()
        (workspace_dir / "research" / "literature.md").write_text(preserved_line)
        _run_draft_command(
            ["init", "--dir", str(workspace_dir),
             "--title", "Cohort Percentiles", "--category", "cs.MA"],
            None, self,
        )
        self.assertEqual(
            (workspace_dir / "research" / "literature.md").read_text(),
            preserved_line,
        )


class TestDepositState(_DepositTestCase):
    def read_deposit_state(self) -> dict:
        return json.loads(
            (self.workspace_dir / ".exactory" / "deposit.json").read_text()
        )

    def test_deposit_records_the_deposition_in_the_workspace(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        state = self.read_deposit_state()
        self.assertEqual(state["environment"], "sandbox")
        self.assertEqual(state["deposition_id"], 4242)
        self.assertIn("deposit/4242", state["draft_url"])
        self.assertNotIn("doi", state)

    def test_publish_adds_the_dois_to_the_deposit_state(self) -> None:
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        state = self.read_deposit_state()
        self.assertEqual(state["doi"], "10.5281/zenodo.4242")
        self.assertEqual(state["concept_doi"], "10.5281/zenodo.4241")
        self.assertIn("records/4242", state["record_url"])


class TestNewVersion(_DepositTestCase):
    def record_prior_deposit(self, environment: str = "sandbox") -> None:
        args = ["--creator", "Shiroshita, Ryosuke", "--publish"]
        if environment == "production":
            args.extend(["--production", "--confirm-publish"])
        self._deposit(args)
        self.fake_api.requests.clear()

    def test_new_version_reuses_the_stored_deposition(self) -> None:
        self.record_prior_deposit()
        self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"])
        requested = [(request.get_method(), request.full_url)
                     for request in self.fake_api.requests]
        self.assertEqual(
            requested[0],
            ("POST", "https://sandbox.zenodo.org/api/deposit/depositions/4242"
                     "/actions/newversion"),
        )
        self.assertEqual(
            requested[1],
            ("GET", "https://sandbox.zenodo.org/api/deposit/depositions/4343"),
        )
        upload_urls = [url for method, url in requested
                       if method == "PUT" and "/files/" in url]
        self.assertEqual(
            upload_urls,
            ["https://sandbox.zenodo.org/api/files/bucket-2/paper.pdf"],
        )
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4343)

    def read_deposit_state(self) -> dict:
        return json.loads(
            (self.workspace_dir / ".exactory" / "deposit.json").read_text()
        )

    def test_a_lost_new_version_response_finishes_on_the_next_run(self) -> None:
        # https://developers.zenodo.org states that the new-version action has
        # no effect while the draft of the first call is unpublished. The next
        # run sends that same action again, so the deposit finishes on the draft
        # the lost call left, and it creates no second record.
        self.record_prior_deposit()
        self.fake_api = _LostResponseZenodoApi("/actions/newversion")
        _draft._open_url = self.fake_api
        interrupted_output = self._deposit(
            ["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("Then run the command again.", interrupted_output)

        self.fake_api = _FakeZenodoApi()
        _draft._open_url = self.fake_api
        self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"])
        requested = [(request.get_method(), request.full_url)
                     for request in self.fake_api.requests]
        self.assertEqual(
            requested[:2],
            [("POST", "https://sandbox.zenodo.org/api/deposit/depositions"
                      "/4242/actions/newversion"),
             ("GET", "https://sandbox.zenodo.org/api/deposit/depositions/4343")],
        )
        self.assertEqual([url for _, url in requested
                          if url.endswith("/deposit/depositions")], [])
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4343)
        intents = [record for record
                   in Store(self.workspace_dir).snapshot()["records"]["remote_intent"].values()
                   if record["binding"]["new_version"]]
        self.assertEqual(len(intents), 1)
        intent = intents[0]
        self.assertEqual(intent["status"], "complete")
        self.assertEqual([entry["name"] for entry in intent["discarded"]], ["create"])

    def test_new_version_states_the_same_authorship_as_a_first_deposit(self) -> None:
        self.record_prior_deposit()
        _write_authorship_record(self.workspace_dir, _AGENT_WROTE_THE_PAPER_RECORD_TEXT)
        self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"])
        metadata_request = _read_request(self.fake_api.requests, "PUT",
                                         "/deposit/depositions/4343")
        metadata = json.loads(metadata_request.data.decode())["metadata"]
        self.assertIn(_WRITTEN_BY_EXACTORY_SENTENCE, metadata["description"])
        self.assertEqual(metadata["keywords"], [_WRITTEN_BY_EXACTORY_KEYWORD])

    def test_new_version_refuses_an_environment_mismatch(self) -> None:
        self.record_prior_deposit("production")
        stderr_text = self._deposit(
            ["--new-version", "--creator", "Shiroshita, Ryosuke"],
            expected_exit_code=1,
        )
        self.assertIn("same environment", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_new_version_without_a_stored_deposit_is_an_error(self) -> None:
        stderr_text = self._deposit(
            ["--new-version", "--creator", "Shiroshita, Ryosuke"],
            expected_exit_code=1,
        )
        self.assertIn("prior concrete record", stderr_text)


class TestDirectDeposit(_PlainWorkspaceDepositTestCase):
    def test_an_unready_workspace_notes_the_skip_and_uploads(self) -> None:
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Managed record skipped (readiness_required): ", output)
        self.assertEqual(self.requested()[0], ("POST", "https://sandbox.zenodo.org/api/deposit/depositions"))
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/files/bucket-1/paper.pdf"), self.requested())
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/deposit/depositions/4242"), self.requested())
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/records/4242/draft"), self.requested())
        state = self.read_deposit_state()
        self.assertEqual(state["environment"], "sandbox")
        self.assertEqual(state["deposition_id"], 4242)
        self.assertIn("deposit/4242", state["draft_url"])
        self.assertNotIn("doi", state)

    def test_a_published_direct_deposit_writes_the_dois(self) -> None:
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        state = self.read_deposit_state()
        self.assertEqual(state["doi"], "10.5281/zenodo.4242")
        self.assertEqual(state["concept_doi"], "10.5281/zenodo.4241")
        self.assertIn("records/4242", state["record_url"])
        # The combined stream holds the skip note, the publish line, then the state JSON.
        self.assertIn("The record is published: ", output)
        self.assertIn('"doi": "10.5281/zenodo.4242"', output)

    def test_the_direct_metadata_carries_the_disclosure(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        metadata_request = _read_request(self.fake_api.requests, "PUT",
                                         "/deposit/depositions/4242")
        metadata = json.loads(metadata_request.data.decode())["metadata"]
        self.assertEqual(metadata["title"], "Cohort Percentiles")
        self.assertEqual(metadata["creators"], [{"name": "Shiroshita, Ryosuke"}])
        self.assertIn(_DEPOSITED_THROUGH_EXACTORY_SENTENCE, metadata["description"])
        self.assertNotIn("keywords", metadata)

    def _read_upload_body(self, upload_name: str) -> bytes:
        return _read_request(self.fake_api.requests, "PUT",
                             "/files/bucket-1/" + upload_name).data

    def test_the_paper_upload_carries_the_bytes_of_the_pdf(self) -> None:
        """The upload body is the file on disk. A PUT that names paper.pdf and
        sends nothing leaves an empty paper on a permanent record."""
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertEqual(self._read_upload_body("paper.pdf"),
                         (self.workspace_dir / "draft" / "paper.pdf").read_bytes())

    def test_the_sources_upload_carries_the_bytes_of_the_archive(self) -> None:
        sources_path = self.workspace_dir / "sources.zip"
        with zipfile.ZipFile(sources_path, "w") as archive:
            archive.writestr("main.tex", "\\documentclass{article}\n")
        self._deposit(["--creator", "Shiroshita, Ryosuke", "--sources", str(sources_path)])
        self.assertEqual(self._read_upload_body("supplementary-sources.zip"),
                         sources_path.read_bytes())

    def test_a_tarball_keeps_its_archive_suffix_in_the_supplementary_name(self) -> None:
        sources_path = self.workspace_dir / "code.tar.gz"
        sources_path.write_bytes(b"\x1f\x8b\x08\x00fake tarball")
        self._deposit(["--creator", "Shiroshita, Ryosuke", "--sources", str(sources_path)])
        self.assertEqual(self._read_upload_body("supplementary-sources.tar.gz"),
                         sources_path.read_bytes())

    def test_a_production_deposit_prints_a_failing_citation_report_and_continues(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Citation report: ", output)
        self.assertIn("exactory-check lookup", output)
        self.assertEqual(self.requested()[0], ("POST", "https://zenodo.org/api/deposit/depositions"))

    def test_a_production_publish_still_needs_confirmation(self) -> None:
        stderr_text = self._deposit(["--production", "--publish", "--creator", "Shiroshita, Ryosuke"],
                                    expected_exit_code=1)
        self.assertIn("--confirm-publish", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_new_version_reuses_the_stored_deposition(self) -> None:
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        self.fake_api.requests.clear()
        self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"])
        self.assertEqual(self.requested()[0],
                         ("POST", "https://sandbox.zenodo.org/api/deposit/depositions/4242/actions/newversion"))
        self.assertEqual(self.requested()[1], ("GET", "https://sandbox.zenodo.org/api/deposit/depositions/4343"))
        self.assertIn(("PUT", "https://sandbox.zenodo.org/api/files/bucket-2/paper.pdf"), self.requested())
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4343)

    def test_a_new_version_refuses_an_environment_mismatch(self) -> None:
        self._deposit(["--production", "--publish", "--confirm-publish", "--creator", "Shiroshita, Ryosuke"])
        self.fake_api.requests.clear()
        stderr_text = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("same environment", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_new_version_without_a_prior_record_is_an_error(self) -> None:
        stderr_text = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("Run a plain deposit first", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_blank_abstract_file_is_refused_before_any_request(self) -> None:
        (self.workspace_dir / "draft" / "abstract.txt").write_text(" \n\n")
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn("abstract", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_an_abstract_file_that_is_not_utf8_is_refused_before_any_request(self) -> None:
        (self.workspace_dir / "draft" / "abstract.txt").write_bytes(b"R\xe9sum\xe9 of the paper\n")
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn("as UTF-8 text", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_an_abstract_path_that_is_a_directory_is_refused_before_any_request(self) -> None:
        (self.workspace_dir / "draft" / "abstract-directory").mkdir()
        stderr_text = _run_draft_command(
            ["deposit", "--abstract-file", "draft/abstract-directory",
             "--creator", "Shiroshita, Ryosuke"],
            2, self,
        )
        self.assertIn("as UTF-8 text", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_pdf_path_that_is_a_directory_is_refused_before_any_request(self) -> None:
        directory_path = self.workspace_dir / "draft" / "paper-directory"
        directory_path.mkdir()
        stderr_text = self._deposit(["--pdf", str(directory_path), "--creator", "Shiroshita, Ryosuke"],
                                    expected_exit_code=2)
        self.assertIn("cannot read the file", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_sources_path_that_is_a_directory_is_refused_before_any_request(self) -> None:
        directory_path = self.workspace_dir / "draft" / "sources-directory"
        directory_path.mkdir()
        stderr_text = self._deposit(["--sources", str(directory_path), "--creator", "Shiroshita, Ryosuke"],
                                    expected_exit_code=2)
        self.assertIn("cannot read the file", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_draft_state_without_a_title_is_refused_before_any_request(self) -> None:
        (self.workspace_dir / ".exactory" / "draft.json").write_text(
            json.dumps({"version": 1, "category": "cs.MA"}) + "\n", encoding="utf-8"
        )
        stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
        self.assertIn("names no title", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_draft_state_that_is_not_an_object_is_refused_before_any_request(self) -> None:
        # A JSON scalar is valid JSON and names no title, so it lands on the
        # same refusal a state without a title lands on.
        for state_text in ("null", "7"):
            with self.subTest(state=state_text):
                (self.workspace_dir / ".exactory" / "draft.json").write_text(
                    state_text + "\n", encoding="utf-8"
                )
                stderr_text = self._deposit(["--creator", "Shiroshita, Ryosuke"], expected_exit_code=2)
                self.assertIn("names no title", stderr_text)
                self.assertEqual(self.fake_api.requests, [])

    def test_the_deposition_id_and_its_draft_url_reach_the_user_before_the_publish(self) -> None:
        # The publish response can be lost, and a second run publishes a second
        # permanent record. The user reads the record's id first, so the
        # created record stays findable.
        self.fake_api = _StdoutRecordingZenodoApi()
        _draft._open_url = self.fake_api
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Deposition 4242 is open on Zenodo:"
                      " https://sandbox.zenodo.org/api/deposit/4242",
                      self.fake_api.printed_before_publish)

    def test_a_lost_publish_response_names_the_record_instead_of_a_second_run(self) -> None:
        # A direct deposit saves no intent, so there is nothing to resume: the
        # advice names the record to open, and the advice to send the request
        # again stays off this one request. The advice stands on stderr alone,
        # because the caller that reads it there holds no stdout line.
        self.fake_api = _LostResponseZenodoApi("/actions/publish")
        _draft._open_url = self.fake_api
        stdout_text, stderr_text = self._deposit_on_split_streams(
            ["--publish", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("Deposition 4242 is open on Zenodo:", stdout_text)
        self.assertIn("The client cannot reach the Zenodo API.", stderr_text)
        self.assertIn("Deposition 4242 may be published already. Open"
                      " https://sandbox.zenodo.org/api/deposit/4242 and read its state.",
                      stderr_text)
        self.assertIn("a second run publishes a second permanent record", stderr_text)
        self.assertNotIn("Then run the command again", stderr_text)

    def test_a_lost_create_response_keeps_the_advice_to_run_the_command_again(self) -> None:
        # The publish is the last request of the deposit, so a run that ends at
        # the create published nothing: the next run publishes the record for
        # the first time, and the plain advice holds.
        self.fake_api = _LostResponseZenodoApi("/deposit/depositions")
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)
        self.assertIn("The client cannot reach the Zenodo API.", output)
        self.assertIn("Then run the command again.", output)
        self.assertNotIn("second permanent record", output)
        self.assertEqual(self.requested(),
                         [("POST", "https://sandbox.zenodo.org/api/deposit/depositions")])

    def test_a_lost_upload_response_keeps_the_advice_to_run_the_command_again(self) -> None:
        # Every other request of a direct deposit can be sent again, and the
        # upload is the one before the publish.
        self.fake_api = _LostResponseZenodoApi("/files/bucket-1/paper.pdf")
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)
        self.assertIn("Then run the command again.", output)
        self.assertNotIn("second permanent record", output)

    def test_a_server_error_on_the_publish_names_the_record_instead_of_a_second_run(self) -> None:
        # A 5xx settles the publish no more than a lost response does: Zenodo
        # can have published the record behind the gateway that answered.
        self.fake_api = _StatusErrorZenodoApi("/actions/publish", 502)
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)
        self.assertIn("Deposition 4242 is open on Zenodo:", output)
        self.assertIn("The Zenodo API returned HTTP 502.", output)
        self.assertIn("The server states no outcome for this request.", output)
        self.assertIn("Deposition 4242 may be published already.", output)
        self.assertNotIn("Then run the command again", output)

    def test_a_refused_publish_states_the_status_without_advice_to_send_it_again(self) -> None:
        # A 4xx is the server's decision on the request it read, so the same
        # request earns no advice at all.
        self.fake_api = _StatusErrorZenodoApi("/actions/publish", 400)
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)
        self.assertIn("The Zenodo API returned HTTP 400.", output)
        self.assertNotIn("The server states no outcome for this request.", output)
        self.assertNotIn("may be published already", output)
        self.assertNotIn("Then run the command again", output)

    def test_a_server_error_on_the_upload_keeps_the_advice_to_run_the_command_again(self) -> None:
        self.fake_api = _StatusErrorZenodoApi("/files/bucket-1/paper.pdf", 503)
        _draft._open_url = self.fake_api
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)
        self.assertIn("The server states no outcome for this request."
                      " Then run the command again.", output)
        self.assertNotIn("second permanent record", output)

    def test_a_new_version_without_a_stored_deposition_id_is_an_error(self) -> None:
        (self.workspace_dir / ".exactory" / "deposit.json").write_text(
            json.dumps({"environment": "sandbox"}) + "\n", encoding="utf-8"
        )
        stderr_text = self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("names no deposition_id", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_a_store_that_refuses_the_record_names_the_only_local_copy_and_stops(self) -> None:
        with unittest.mock.patch.object(_draft, "record_direct_deposit",
                                        _refuse_the_direct_deposit_record):
            output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                                   expected_exit_code=1)
        # The DOI reaches the user before the command reports the refused record.
        self.assertIn("The record is published: ", output)
        self.assertIn('"doi": "10.5281/zenodo.4242"', output)
        self.assertIn("Managed record skipped (store_busy): ", output)
        self.assertIn(".exactory/deposit.json holds the only local copy of it", output)
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")
        # The store kept no deposit, which is why the file is not durable.
        self.assertNotIn("deposit", Store(self.workspace_dir).snapshot()["records"].get("workspace", {}))

    def test_a_refused_record_replaces_a_deposit_state_symlink_instead_of_following_it(self) -> None:
        elsewhere_path = _link_deposit_state_elsewhere(self.workspace_dir)
        with unittest.mock.patch.object(_draft, "record_direct_deposit",
                                        _refuse_the_direct_deposit_record):
            self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertFalse((self.workspace_dir / ".exactory" / "deposit.json").is_symlink())
        self.assertEqual(elsewhere_path.read_text(encoding="utf-8"), "untouched\n")
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")

    def test_the_store_records_the_direct_deposit_so_an_export_keeps_it(self) -> None:
        from research_harness.integration import export_workspace
        self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"])
        written = self.read_deposit_state()
        store = Store(self.workspace_dir)
        self.assertEqual(store.snapshot()["records"]["workspace"]["deposit"], written)
        self.assertNotIn("publication_receipt", store.snapshot()["records"])
        export_workspace(store)
        self.assertEqual(self.read_deposit_state(), written)


class TestCorruptStoreDeposit(_PlainWorkspaceDepositTestCase):
    """A draft workspace whose store exists and does not open."""

    def setUp(self) -> None:
        super().setUp()
        for path in (self.workspace_dir / ".exactory").glob("research.sqlite3-*"):
            path.unlink()
        (self.workspace_dir / ".exactory" / "research.sqlite3").write_bytes(b"not a database")

    def test_the_corrupt_store_is_noted_once_and_the_deposit_is_direct(self) -> None:
        # A store that exists and does not open can open again later, and the
        # projection export then rewrites the file from a store that never saw
        # this deposit. The command says so on stderr, where it names the file
        # that holds the state instead of a line on the other stream, and exits
        # nonzero.
        stdout_text, stderr_text = self._deposit_on_split_streams(
            ["--creator", "Shiroshita, Ryosuke"], expected_exit_code=1)
        self.assertIn("Managed record skipped (corrupt_state): ", stderr_text)
        # One decision point, so one note: nothing re-opens the store to say it again.
        self.assertEqual(stderr_text.count("Managed record skipped"), 1)
        self.assertEqual(self.requested()[0], ("POST", "https://sandbox.zenodo.org/api/deposit/depositions"))
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4242)
        self.assertIn('"deposition_id": 4242', stdout_text)
        self.assertIn(".exactory/deposit.json holds the only local copy of it", stderr_text)
        self.assertIn("a projection export can replace that file", stderr_text)
        self.assertIn("Copy the state out of that file", stderr_text)
        self.assertNotIn("printed above", stderr_text)

    def test_a_published_deposit_prints_the_doi_before_the_corrupt_store_stops_it(self) -> None:
        output = self._deposit(["--publish", "--creator", "Shiroshita, Ryosuke"],
                               expected_exit_code=1)
        self.assertIn("The record is published: ", output)
        self.assertIn('"doi": "10.5281/zenodo.4242"', output)
        self.assertIn(".exactory/deposit.json holds the only local copy of it", output)
        self.assertEqual(self.read_deposit_state()["doi"], "10.5281/zenodo.4242")


class TestLegacyWorkspaceDeposit(_LegacyWorkspaceDepositTestCase):
    def test_a_legacy_workspace_uploads_without_a_note(self) -> None:
        # No store means no projection export, so the file this command writes
        # is the record. The command says nothing and succeeds.
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertNotIn("Managed record skipped", output)
        self.assertNotIn("only local copy", output)
        self.assertEqual(self.requested()[0], ("POST", "https://sandbox.zenodo.org/api/deposit/depositions"))
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4242)

    def test_a_legacy_workspace_replaces_a_deposit_state_symlink_instead_of_following_it(self) -> None:
        elsewhere_path = _link_deposit_state_elsewhere(self.workspace_dir)
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertFalse((self.workspace_dir / ".exactory" / "deposit.json").is_symlink())
        self.assertEqual(elsewhere_path.read_text(encoding="utf-8"), "untouched\n")
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4242)


if __name__ == "__main__":
    unittest.main()
