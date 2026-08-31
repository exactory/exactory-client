"""Tests for bin/exactory-draft: the init layout and the Zenodo deposit flow."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

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
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        if expected_exit_code is None:
            args.handler(args)
        else:
            with test_case.assertRaises(SystemExit) as caught:
                args.handler(args)
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
                "doi": "10.5281/zenodo.4242",
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
                "doi": "10.5281/zenodo.4343",
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
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["title"], "Cohort Percentiles")
        self.assertEqual(state["corpus"], "arxiv")
        self.assertEqual(state["category"], "cs.MA")
        self.assertRegex(state["created"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(set(state), {"version", "title", "corpus", "category", "created"})
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
        self.assertIn("AI assistance", metadata["description"])
        self.assertIn("Shiroshita, Ryosuke", metadata["description"])
        self.assertIn("responsible", metadata["description"])

    def test_description_opens_with_the_abstract_and_ends_with_the_disclosure(self) -> None:
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        description = self._read_sent_metadata()["description"]
        self.assertTrue(description.startswith("<p>"))
        self.assertIn("cohort percentiles &amp; bound their error", description)
        self.assertIn("second paragraph", description)
        self.assertLess(
            description.find("cohort percentiles"), description.find("AI assistance")
        )

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


class TestProductionDepositGate(_DepositTestCase):
    def test_production_deposit_is_refused_when_the_gate_fails(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        stderr_text = self._deposit(
            ["--production", "--creator", "Shiroshita, Ryosuke"], expected_exit_code=1
        )
        self.assertIn("exactory-check lookup", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_sandbox_deposit_is_not_gated(self) -> None:
        (self.workspace_dir / ".exactory" / "citation-check.json").unlink()
        (self.workspace_dir / "draft" / "references.bib").unlink()
        self._deposit(["--creator", "Shiroshita, Ryosuke"])
        self.assertTrue(self.fake_api.requests)


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
        (self.workspace_dir / ".exactory" / "deposit.json").write_text(json.dumps({
            "environment": environment,
            "deposition_id": 4242,
            "draft_url": "https://sandbox.zenodo.org/deposit/4242",
        }))

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

    def test_new_version_refuses_an_environment_mismatch(self) -> None:
        self.record_prior_deposit("production")
        stderr_text = self._deposit(
            ["--new-version", "--creator", "Shiroshita, Ryosuke"],
            expected_exit_code=1,
        )
        self.assertIn("production", stderr_text)
        self.assertEqual(self.fake_api.requests, [])

    def test_new_version_without_a_stored_deposit_is_an_error(self) -> None:
        stderr_text = self._deposit(
            ["--new-version", "--creator", "Shiroshita, Ryosuke"],
            expected_exit_code=1,
        )
        self.assertIn("deposit.json", stderr_text)


if __name__ == "__main__":
    unittest.main()
