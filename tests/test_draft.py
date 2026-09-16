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
from pathlib import Path

from integration_fixtures import prepare_research, prepare_manuscript
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


def _run_draft_command(argv: list[str], expected_exit_code: int | None,
                       test_case: unittest.TestCase) -> str:
    args = _draft._build_parser().parse_args(argv)
    def invoke():
        try:
            args.handler(args)
        except ResearchError as error:
            _draft._exit_with_error(json.dumps({"error": error.as_dict()}))
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        if expected_exit_code is None:
            invoke()
        else:
            with test_case.assertRaises(SystemExit) as caught:
                invoke()
            test_case.assertEqual(caught.exception.code, expected_exit_code)
    return sink.getvalue()


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
        metadata_request = next(
            request for request in self.fake_api.requests
            if request.get_method() == "PUT"
            and request.full_url.endswith("/deposit/depositions/4242")
        )
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
        read_request = next(
            request for request in self.fake_api.requests
            if request.get_method() == "GET"
            and request.full_url.endswith("/records/4242/draft")
        )
        self.assertEqual(read_request.get_header("Accept"),
                         "application/vnd.inveniordm.v1+json")
        write_request = next(
            request for request in self.fake_api.requests
            if request.get_method() == "PUT"
            and request.full_url.endswith("/records/4242/draft")
        )
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


class TestProductionDepositCitationReport(_DepositTestCase):
    def test_a_failing_report_is_printed_and_the_managed_deposit_continues(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        output = self._deposit(["--production", "--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Citation report: ", output)
        self.assertIn("exactory-check lookup", output)
        self.assertTrue(self.fake_api.requests[0].full_url.startswith("https://zenodo.org/api/"))
        self.assertIn("publication_receipt", Store(self.workspace_dir).snapshot()["records"])

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

    def test_new_version_states_the_same_authorship_as_a_first_deposit(self) -> None:
        self.record_prior_deposit()
        _write_authorship_record(self.workspace_dir, _AGENT_WROTE_THE_PAPER_RECORD_TEXT)
        self._deposit(["--new-version", "--creator", "Shiroshita, Ryosuke"])
        metadata_request = next(
            request for request in self.fake_api.requests
            if request.get_method() == "PUT"
            and request.full_url.endswith("/deposit/depositions/4343")
        )
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
        metadata_request = next(request for request in self.fake_api.requests
                                if request.get_method() == "PUT"
                                and request.full_url.endswith("/deposit/depositions/4242"))
        metadata = json.loads(metadata_request.data.decode())["metadata"]
        self.assertEqual(metadata["title"], "Cohort Percentiles")
        self.assertEqual(metadata["creators"], [{"name": "Shiroshita, Ryosuke"}])
        self.assertIn(_DEPOSITED_THROUGH_EXACTORY_SENTENCE, metadata["description"])
        self.assertNotIn("keywords", metadata)

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
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertIn("Managed record skipped (corrupt_state): ", output)
        # One decision point, so one note: nothing re-opens the store to say it again.
        self.assertEqual(output.count("Managed record skipped"), 1)
        self.assertEqual(self.requested()[0], ("POST", "https://sandbox.zenodo.org/api/deposit/depositions"))
        self.assertEqual(self.read_deposit_state()["deposition_id"], 4242)


class TestLegacyWorkspaceDeposit(_LegacyWorkspaceDepositTestCase):
    def test_a_legacy_workspace_uploads_without_a_note(self) -> None:
        output = self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertNotIn("Managed record skipped", output)
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
