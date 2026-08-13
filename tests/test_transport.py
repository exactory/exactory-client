"""Tests for bin/exactory: identifier mapping, path encoding, and the citation gate."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import urllib.parse
import unittest
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


_transport = _load_bin_module("exactory", "exactory_transport")


def _build_draft_workspace(workspace_dir: Path) -> None:
    """Lay out a minimal draft workspace with the real draft.json shape."""
    (workspace_dir / ".exactory").mkdir()
    (workspace_dir / "draft").mkdir()
    (workspace_dir / ".exactory" / "draft.json").write_text(json.dumps({
        "version": 1, "title": "Cohort Percentiles", "corpus": "arxiv",
        "category": "cs.MA", "created": "2026-08-08T00:00:00Z",
    }))


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


class _TransportTestCase(unittest.TestCase):
    """Shared plumbing: a scratch cwd and a recording _send_request patch."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch_dir = Path(scratch.name)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch_dir)

        self.requested_paths: list[str] = []
        self.response_status = 201
        self.addCleanup(setattr, _transport, "_send_request", _transport._send_request)

        def _record_request(method: str, path: str, body: dict | None = None) -> tuple[dict, int]:
            self.requested_paths.append(path)
            return {}, self.response_status

        _transport._send_request = _record_request

    def _run(self, argv: list[str], expected_exit_code: int | None = None) -> tuple[str, str]:
        args = _transport._build_parser().parse_args(argv)
        stdout_sink, stderr_sink = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout_sink), contextlib.redirect_stderr(stderr_sink):
            if expected_exit_code is None:
                args.handler(args)
            else:
                with self.assertRaises(SystemExit) as caught:
                    args.handler(args)
                self.assertEqual(caught.exception.code, expected_exit_code)
        return stdout_sink.getvalue(), stderr_sink.getvalue()


class TestPaperSubcommand(_TransportTestCase):
    def test_a_bare_arxiv_id_maps_to_its_datacite_doi(self) -> None:
        self._run(["paper", "2301.00001"])
        self.assertEqual(self.requested_paths, ["/api/v1/papers/10.48550/arxiv.2301.00001"])

    def test_a_versioned_arxiv_id_maps_to_the_concept_doi(self) -> None:
        self._run(["paper", "2301.00001v2"])
        self.assertEqual(self.requested_paths, ["/api/v1/papers/10.48550/arxiv.2301.00001"])

    def test_a_doi_passes_through_unchanged(self) -> None:
        self._run(["paper", "10.5281/zenodo.21381192"])
        self.assertEqual(self.requested_paths, ["/api/v1/papers/10.5281/zenodo.21381192"])


class TestSuggestionsSubcommand(_TransportTestCase):
    def test_a_bare_arxiv_id_maps_to_its_datacite_doi(self) -> None:
        self.response_status = 200
        self._run(["suggestions", "2301.00001"])
        self.assertEqual(self.requested_paths,
                         ["/api/v1/suggestions/10.48550/arxiv.2301.00001"])

    def test_a_doi_passes_through_unchanged(self) -> None:
        self.response_status = 200
        self._run(["suggestions", "10.5281/zenodo.21381192"])
        self.assertEqual(self.requested_paths,
                         ["/api/v1/suggestions/10.5281/zenodo.21381192"])


class TestTaskSubcommand(_TransportTestCase):
    def test_it_reads_one_task_by_verification_id(self) -> None:
        self._run(["task", "656c336e-e892-4c47-80d2-a71d022f4116"])
        self.assertEqual(
            self.requested_paths, ["/api/v1/tasks/656c336e-e892-4c47-80d2-a71d022f4116"]
        )


class TestPathEncoding(_TransportTestCase):
    def test_task_url_encodes_the_verification_id(self) -> None:
        self._run(["task", "../verifications?limit=1"])
        self.assertEqual(
            self.requested_paths, ["/api/v1/tasks/..%2Fverifications%3Flimit%3D1"]
        )

    def test_status_url_encodes_the_verification_id(self) -> None:
        self._run(["status", "../tasks?limit=1"])
        self.assertEqual(
            self.requested_paths, ["/api/v1/verifications/..%2Ftasks%3Flimit%3D1"]
        )

    def test_submit_review_url_encodes_the_verification_id(self) -> None:
        review_path = self.scratch_dir / "review.json"
        review_path.write_text("{}")
        self._run(["submit-review", "../tasks?x=1", "--file", str(review_path)])
        self.assertEqual(
            self.requested_paths, ["/api/v1/verifications/..%2Ftasks%3Fx%3D1/reviews"]
        )


class TestSubmitRepeatNotice(_TransportTestCase):
    def test_a_200_response_notes_the_existing_open_request_on_stderr(self) -> None:
        self.response_status = 200
        stdout_text, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertIn("existing open request", stderr_text)
        json.loads(stdout_text)  # stdout stays pure JSON

    def test_a_201_response_prints_no_stderr_notice(self) -> None:
        stdout_text, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(stderr_text, "")
        json.loads(stdout_text)


class TestSubmitCitationGate(_TransportTestCase):
    def setUp(self) -> None:
        super().setUp()
        _build_draft_workspace(self.scratch_dir)

    def test_submit_inside_a_workspace_is_refused_when_the_gate_fails(self) -> None:
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"],
                                   expected_exit_code=1)
        self.assertIn("references.bib", stderr_text)
        self.assertEqual(self.requested_paths, [])

    def test_submit_inside_a_workspace_proceeds_when_the_gate_passes(self) -> None:
        _write_passing_citation_report(self.scratch_dir)
        self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])

    def test_submit_from_a_workspace_subdirectory_still_runs_the_gate(self) -> None:
        os.chdir(self.scratch_dir / "draft")
        self._run(["submit", "--doi", "10.5281/zenodo.1"], expected_exit_code=1)
        self.assertEqual(self.requested_paths, [])

    def test_submit_outside_a_workspace_skips_the_gate(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        os.chdir(outside.name)
        self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])


class TestTasksSearchFlags(_TransportTestCase):
    def test_tasks_builds_the_search_query_string(self) -> None:
        self._run([
            "tasks", "--query", "skin game", "--sort", "relevance",
            "--source", "arxiv", "--category", "cs.LG",
            "--published-from", "2026-01-01", "--published-to", "2026-06-30",
        ])

        parsed = urllib.parse.urlparse(self.requested_paths[0])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/api/v1/tasks")
        self.assertEqual(query["q"], ["skin game"])
        self.assertEqual(query["sort"], ["relevance"])
        self.assertEqual(query["source"], ["arxiv"])
        self.assertEqual(query["category"], ["cs.LG"])
        self.assertEqual(query["publishedFrom"], ["2026-01-01"])
        self.assertEqual(query["publishedTo"], ["2026-06-30"])

    def test_tasks_without_search_flags_sends_only_the_limit(self) -> None:
        self._run(["tasks"])

        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.requested_paths[0]).query)
        self.assertEqual(query, {"limit": ["25"]})

    def test_an_unknown_sort_value_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _transport._build_parser().parse_args(["tasks", "--sort", "best"])
        self.assertEqual(caught.exception.code, 2)


class TestParserStrictness(unittest.TestCase):
    def test_an_abbreviated_flag_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _transport._build_parser().parse_args(["tasks", "--lim", "5"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
