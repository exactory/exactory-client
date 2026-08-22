"""Tests for bin/exactory: identifier mapping, path encoding, the citation gate,
and the grand-challenge subcommands."""

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
import urllib.request
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


def _invoke_cli(test_case: unittest.TestCase, argv: list[str],
                expected_exit_code: int | None = None) -> tuple[str, str]:
    """Parse argv with the real parser and run its handler, capturing both streams."""
    args = _transport._build_parser().parse_args(argv)
    stdout_sink, stderr_sink = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout_sink), contextlib.redirect_stderr(stderr_sink):
        if expected_exit_code is None:
            args.handler(args)
        else:
            with test_case.assertRaises(SystemExit) as caught:
                args.handler(args)
            test_case.assertEqual(caught.exception.code, expected_exit_code)
    return stdout_sink.getvalue(), stderr_sink.getvalue()


class _TransportTestCase(unittest.TestCase):
    """Shared plumbing: a scratch cwd and a recording _send_request patch."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch_dir = Path(scratch.name)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch_dir)

        self.requested_paths: list[str] = []
        self.requested_methods: list[str] = []
        self.request_bodies: list[dict | None] = []
        self.response_status = 201
        self.addCleanup(setattr, _transport, "_send_request", _transport._send_request)

        def _record_request(method: str, path: str, body: dict | None = None) -> tuple[dict, int]:
            self.requested_paths.append(path)
            self.requested_methods.append(method)
            self.request_bodies.append(body)
            return {}, self.response_status

        _transport._send_request = _record_request

    def _run(self, argv: list[str], expected_exit_code: int | None = None) -> tuple[str, str]:
        return _invoke_cli(self, argv, expected_exit_code)


class _FakeResponse(io.BytesIO):
    """The slice of http.client.HTTPResponse that _send_request touches."""

    def __init__(self, body: bytes, status: int) -> None:
        super().__init__(body)
        self._status = status

    def getcode(self) -> int:
        return self._status


class _UrlopenTransportTestCase(unittest.TestCase):
    """Shared plumbing for the grand-challenge subcommands: a scratch cwd and a
    stubbed urllib.request.urlopen, so the method, the full URL, the JSON body,
    and the Authorization header are all asserted on the real request object."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch_dir = Path(scratch.name)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch_dir)

        saved_env = {key: os.environ.get(key)
                     for key in ("EXACTORY_API_KEY", "EXACTORY_API_URL")}

        def _restore_env() -> None:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(_restore_env)
        os.environ["EXACTORY_API_KEY"] = "test-key"
        os.environ["EXACTORY_API_URL"] = "https://api.test"

        self.sent_requests: list[urllib.request.Request] = []
        self.response_status = 200
        self.response_body = b"{}"
        self.addCleanup(setattr, urllib.request, "urlopen", urllib.request.urlopen)

        def _fake_urlopen(request: urllib.request.Request,
                          timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            return _FakeResponse(self.response_body, self.response_status)

        urllib.request.urlopen = _fake_urlopen

    def _run(self, argv: list[str], expected_exit_code: int | None = None) -> tuple[str, str]:
        return _invoke_cli(self, argv, expected_exit_code)

    def _single_request(self) -> urllib.request.Request:
        self.assertEqual(len(self.sent_requests), 1)
        return self.sent_requests[0]


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


class TestVoteSubcommand(_TransportTestCase):
    def test_an_upvote_puts_the_value(self) -> None:
        self._run(["vote", "656c336e-e892-4c47-80d2-a71d022f4116", "--value", "1"])
        self.assertEqual(
            self.requested_paths,
            ["/api/v1/verifications/656c336e-e892-4c47-80d2-a71d022f4116/vote"],
        )
        self.assertEqual(self.requested_methods, ["PUT"])
        self.assertEqual(self.request_bodies, [{"value": 1}])

    def test_a_downvote_puts_the_negative_value(self) -> None:
        self._run(["vote", "656c336e-e892-4c47-80d2-a71d022f4116", "--value", "-1"])
        self.assertEqual(self.request_bodies, [{"value": -1}])

    def test_a_zero_deletes_the_vote_with_no_body(self) -> None:
        self._run(["vote", "656c336e-e892-4c47-80d2-a71d022f4116", "--value", "0"])
        self.assertEqual(self.requested_methods, ["DELETE"])
        self.assertEqual(self.request_bodies, [None])

    def test_a_value_outside_the_three_choices_never_reaches_the_server(self) -> None:
        parser = _transport._build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["vote", "656c336e-e892-4c47-80d2-a71d022f4116",
                                   "--value", "2"])
        self.assertEqual(self.requested_paths, [])


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

    def test_vote_url_encodes_the_verification_id(self) -> None:
        self._run(["vote", "../tasks?x=1", "--value", "1"])
        self.assertEqual(
            self.requested_paths, ["/api/v1/verifications/..%2Ftasks%3Fx%3D1/vote"]
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


class TestChallengesSubcommand(_UrlopenTransportTestCase):
    def test_it_lists_challenges_with_the_default_limit(self) -> None:
        self._run(["challenges"])
        request = self._single_request()
        self.assertEqual(request.get_method(), "GET")
        parsed = urllib.parse.urlparse(request.full_url)
        self.assertEqual(parsed.path, "/api/v1/grand-challenges")
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {"limit": ["25"]})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_it_builds_the_filter_query_string(self) -> None:
        self._run(["challenges", "--status", "all", "--field", "cs.LG",
                   "--parent-id", "parent-1", "--paper-doi", "10.5281/zenodo.1",
                   "--sort", "top", "--cursor", "abc", "--limit", "50"])
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(self._single_request().full_url).query)
        self.assertEqual(query, {
            "status": ["all"], "field": ["cs.LG"], "parentId": ["parent-1"],
            "paperDoi": ["10.5281/zenodo.1"], "sort": ["top"],
            "cursor": ["abc"], "limit": ["50"],
        })

    def test_a_bare_arxiv_paper_doi_maps_to_its_datacite_doi(self) -> None:
        self._run(["challenges", "--paper-doi", "2301.00001"])
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(self._single_request().full_url).query)
        self.assertEqual(query["paperDoi"], ["10.48550/arxiv.2301.00001"])

    def test_a_null_author_list_item_prints_the_server_json_unchanged(self) -> None:
        # A deleted account leaves its challenges on record with null authorship.
        listing = {"items": [{"id": "chal-1", "title": "Solve cohort percentile"
                              " drift", "author": None, "score": 3}],
                   "nextCursor": None}
        self.response_body = json.dumps(listing).encode()
        stdout_text, _ = self._run(["challenges"])
        self.assertEqual(json.loads(stdout_text), listing)

    def test_an_unknown_status_value_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _transport._build_parser().parse_args(["challenges", "--status", "closed"])
        self.assertEqual(caught.exception.code, 2)

    def test_an_unknown_sort_value_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _transport._build_parser().parse_args(["challenges", "--sort", "best"])
        self.assertEqual(caught.exception.code, 2)


class TestChallengeSubcommand(_UrlopenTransportTestCase):
    def test_it_reads_one_challenge_by_id(self) -> None:
        self._run(["challenge", "656c336e-e892-4c47-80d2-a71d022f4116"])
        request = self._single_request()
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(
            urllib.parse.urlparse(request.full_url).path,
            "/api/v1/grand-challenges/656c336e-e892-4c47-80d2-a71d022f4116",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_it_url_encodes_the_challenge_id(self) -> None:
        self._run(["challenge", "../verifications?limit=1"])
        self.assertEqual(
            urllib.parse.urlparse(self._single_request().full_url).path,
            "/api/v1/grand-challenges/..%2Fverifications%3Flimit%3D1",
        )

    def test_a_tombstone_detail_prints_the_server_json_unchanged(self) -> None:
        # A removed challenge resolves to a tombstone: no title, sections,
        # author, or score. The client prints the server JSON as is.
        tombstone = {"id": "chal-1", "removed": True,
                     "createdAt": "2026-08-01T00:00:00Z", "parentId": None}
        self.response_body = json.dumps(tombstone).encode()
        stdout_text, stderr_text = self._run(["challenge", "chal-1"])
        self.assertEqual(json.loads(stdout_text), tombstone)
        self.assertEqual(stderr_text, "")

    def test_a_null_author_detail_prints_the_server_json_unchanged(self) -> None:
        detail = {"id": "chal-1", "title": "Solve cohort percentile drift",
                  "author": None, "score": 3, "children": [], "papers": []}
        self.response_body = json.dumps(detail).encode()
        stdout_text, _ = self._run(["challenge", "chal-1"])
        self.assertEqual(json.loads(stdout_text), detail)


class TestPostChallengeSubcommand(_UrlopenTransportTestCase):
    _CITATION_TEXT = ("Carol Instance and Dana Case. Predicting Citation Impact"
                      " with Cohort Percentiles. 2023.")

    def setUp(self) -> None:
        super().setUp()
        self.citations_path = self.scratch_dir / "citations.json"
        self.citations_path.write_text(json.dumps([
            {"citation": self._CITATION_TEXT, "locator": "10.1234/exact.5678"},
        ]))

    def _post_argv(self, *, title: str = "Solve cohort percentile drift",
                   problem_statement: str | None = None) -> list[str]:
        return [
            "post-challenge",
            "--title", title,
            "--field", "cs.LG",
            "--problem-statement", problem_statement or ("p" * 200),
            "--current-state", "c" * 100,
            "--resolution-criteria", "r" * 50,
            "--citations-file", str(self.citations_path),
        ]

    def test_it_posts_the_six_required_fields(self) -> None:
        self.response_status = 201
        self._run(self._post_argv())
        request = self._single_request()
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(urllib.parse.urlparse(request.full_url).path,
                         "/api/v1/grand-challenges")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        body = json.loads(request.data.decode())
        self.assertEqual(body, {
            "title": "Solve cohort percentile drift",
            "field": "cs.LG",
            "problemStatement": "p" * 200,
            "currentState": "c" * 100,
            "resolutionCriteria": "r" * 50,
            "citations": [{"citation": self._CITATION_TEXT,
                           "locator": "10.1234/exact.5678"}],
        })

    def test_the_field_files_feed_the_body(self) -> None:
        (self.scratch_dir / "problem.md").write_text("p" * 200 + "\n")
        (self.scratch_dir / "state.md").write_text("c" * 100 + "\n")
        (self.scratch_dir / "criteria.md").write_text("r" * 50 + "\n")
        self._run([
            "post-challenge",
            "--title", "Solve cohort percentile drift",
            "--field", "cs.LG",
            "--problem-statement-file", str(self.scratch_dir / "problem.md"),
            "--current-state-file", str(self.scratch_dir / "state.md"),
            "--resolution-criteria-file", str(self.scratch_dir / "criteria.md"),
            "--citations-file", str(self.citations_path),
        ])
        body = json.loads(self._single_request().data.decode())
        self.assertEqual(body["problemStatement"], "p" * 200)
        self.assertEqual(body["currentState"], "c" * 100)
        self.assertEqual(body["resolutionCriteria"], "r" * 50)

    def test_parent_and_paper_links_are_forwarded(self) -> None:
        self._run(self._post_argv() + [
            "--parent-id", "parent-1",
            "--paper-doi", "10.5281/zenodo.1", "--paper-doi", "2301.00001",
        ])
        body = json.loads(self._single_request().data.decode())
        self.assertEqual(body["parentId"], "parent-1")
        self.assertEqual(body["paperDois"],
                         ["10.5281/zenodo.1", "10.48550/arxiv.2301.00001"])

    def test_a_short_title_is_refused_before_any_request(self) -> None:
        self._run(self._post_argv(title="short"), expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_title_bounds_count_utf16_units(self) -> None:
        # 101 non-BMP characters are 202 UTF-16 code units, over the 200 limit.
        self._run(self._post_argv(title="\U0001f9ea" * 101), expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_a_short_problem_statement_is_refused(self) -> None:
        self._run(self._post_argv(problem_statement="p" * 199), expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_a_bad_locator_is_refused(self) -> None:
        self.citations_path.write_text(json.dumps([
            {"citation": self._CITATION_TEXT, "locator": "not-a-locator"},
        ]))
        self._run(self._post_argv(), expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_an_https_locator_is_accepted(self) -> None:
        self.citations_path.write_text(json.dumps([
            {"citation": self._CITATION_TEXT,
             "locator": "https://example.org/paper"},
        ]))
        self._run(self._post_argv())
        body = json.loads(self._single_request().data.decode())
        self.assertEqual(body["citations"][0]["locator"], "https://example.org/paper")

    def test_every_arxiv_locator_style_the_server_accepts_is_accepted(self) -> None:
        # The server accepts new-format ids, old-style ids, and the arXiv: prefix
        # (apps/web schemas/grand-challenge.ts); the client mirror must not refuse
        # a locator the contract allows.
        for locator in ("2301.00001", "2301.00001v2", "arXiv:2301.00001",
                        "hep-th/9901001", "hep-th/9901001v3", "math.GT/0309136"):
            with self.subTest(locator=locator):
                self.sent_requests.clear()
                self.citations_path.write_text(json.dumps([
                    {"citation": self._CITATION_TEXT, "locator": locator},
                ]))
                self._run(self._post_argv())
                body = json.loads(self._single_request().data.decode())
                self.assertEqual(body["citations"][0]["locator"], locator)

    def test_a_lone_surrogate_in_a_citation_counts_as_one_utf16_unit(self) -> None:
        # json.load turns a "\ud800" escape into a lone surrogate, and the
        # server's zod bounds count it as one UTF-16 unit; the client mirror
        # must count it the same way instead of raising UnicodeEncodeError.
        self.citations_path.write_text(
            '[{"citation": "0123456789\\ud800", "locator": "10.1234/exact.5678"}]')
        self._run(self._post_argv())
        body = json.loads(self._single_request().data.decode())
        self.assertEqual(body["citations"][0]["citation"], "0123456789\ud800")

    def test_an_empty_citations_list_is_refused(self) -> None:
        self.citations_path.write_text("[]")
        self._run(self._post_argv(), expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_a_citation_with_an_unknown_key_is_refused(self) -> None:
        self.citations_path.write_text(json.dumps([
            {"citation": self._CITATION_TEXT, "locator": "10.1234/exact.5678",
             "note": "extra"},
        ]))
        self._run(self._post_argv(), expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_more_than_twenty_paper_dois_are_refused(self) -> None:
        paper_flags = [flag for index in range(21)
                       for flag in ("--paper-doi", f"10.5281/zenodo.{index}")]
        self._run(self._post_argv() + paper_flags, expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])


class TestVoteChallengeSubcommand(_UrlopenTransportTestCase):
    def test_an_upvote_puts_value_one(self) -> None:
        self._run(["vote-challenge", "chal-1", "--value", "1"])
        request = self._single_request()
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(urllib.parse.urlparse(request.full_url).path,
                         "/api/v1/grand-challenges/chal-1/vote")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(json.loads(request.data.decode()), {"value": 1})

    def test_a_downvote_puts_value_minus_one(self) -> None:
        self._run(["vote-challenge", "chal-1", "--value", "-1"])
        request = self._single_request()
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(json.loads(request.data.decode()), {"value": -1})

    def test_value_zero_deletes_the_vote(self) -> None:
        self._run(["vote-challenge", "chal-1", "--value", "0"])
        request = self._single_request()
        self.assertEqual(request.get_method(), "DELETE")
        self.assertEqual(urllib.parse.urlparse(request.full_url).path,
                         "/api/v1/grand-challenges/chal-1/vote")
        self.assertIsNone(request.data)

    def test_an_out_of_range_value_is_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _transport._build_parser().parse_args(
                    ["vote-challenge", "chal-1", "--value", "2"])
        self.assertEqual(caught.exception.code, 2)


class TestSolveChallengeSubcommand(_UrlopenTransportTestCase):
    def test_solving_sends_the_resolution_note(self) -> None:
        note = "The linked paper meets every criterion."
        self._run(["solve-challenge", "chal-1", "--note", note])
        request = self._single_request()
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(urllib.parse.urlparse(request.full_url).path,
                         "/api/v1/grand-challenges/chal-1")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(json.loads(request.data.decode()),
                         {"status": "solved", "resolutionNote": note})

    def test_reopening_sends_status_open_without_a_note(self) -> None:
        self._run(["solve-challenge", "chal-1", "--reopen"])
        self.assertEqual(json.loads(self._single_request().data.decode()),
                         {"status": "open"})

    def test_a_short_note_is_refused_before_any_request(self) -> None:
        self._run(["solve-challenge", "chal-1", "--note", "too short"],
                  expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_note_and_reopen_together_are_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _transport._build_parser().parse_args(
                    ["solve-challenge", "chal-1", "--note", "long enough note",
                     "--reopen"])
        self.assertEqual(caught.exception.code, 2)


class TestReportChallengeSubcommand(_UrlopenTransportTestCase):
    def test_it_posts_the_subject_without_a_note(self) -> None:
        self.response_status = 201
        self._run(["report-challenge", "chal-1"])
        request = self._single_request()
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(urllib.parse.urlparse(request.full_url).path,
                         "/api/v1/reports")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(json.loads(request.data.decode()),
                         {"subjectKind": "grand_challenge", "subjectId": "chal-1"})

    def test_a_note_joins_the_body(self) -> None:
        self.response_status = 201
        self._run(["report-challenge", "chal-1",
                   "--note", "The post is advertising, not a research problem."])
        self.assertEqual(json.loads(self._single_request().data.decode()), {
            "subjectKind": "grand_challenge",
            "subjectId": "chal-1",
            "note": "The post is advertising, not a research problem.",
        })

    def test_note_bounds_count_utf16_units(self) -> None:
        # 501 non-BMP characters are 1002 UTF-16 code units, over the 1000 limit.
        self._run(["report-challenge", "chal-1", "--note", "\U0001f9ea" * 501],
                  expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_a_note_of_exactly_one_thousand_units_is_accepted(self) -> None:
        self.response_status = 201
        self._run(["report-challenge", "chal-1", "--note", "n" * 1000])
        self.assertEqual(
            json.loads(self._single_request().data.decode())["note"], "n" * 1000)

    def test_a_whitespace_only_note_is_refused_before_any_request(self) -> None:
        self._run(["report-challenge", "chal-1", "--note", "   "],
                  expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])

    def test_a_201_response_prints_no_stderr_notice(self) -> None:
        self.response_status = 201
        stdout_text, stderr_text = self._run(["report-challenge", "chal-1"])
        self.assertEqual(stderr_text, "")
        json.loads(stdout_text)  # stdout stays pure JSON

    def test_a_200_response_notes_the_existing_report_on_stderr(self) -> None:
        self.response_status = 200
        stdout_text, stderr_text = self._run(["report-challenge", "chal-1"])
        self.assertIn("existing report", stderr_text)
        json.loads(stdout_text)


class TestSubmitChallengeDeclaration(_UrlopenTransportTestCase):
    """The scratch cwd holds no draft workspace, so the citation gate is skipped."""

    def test_challenges_join_the_submit_body(self) -> None:
        self.response_status = 201
        self._run(["submit", "--doi", "10.5281/zenodo.1",
                   "--challenge", "id-1", "--challenge", "id-2"])
        request = self._single_request()
        self.assertEqual(urllib.parse.urlparse(request.full_url).path,
                         "/api/v1/verifications")
        self.assertEqual(json.loads(request.data.decode()),
                         {"doi": "10.5281/zenodo.1",
                          "grandChallengeIds": ["id-1", "id-2"]})

    def test_submit_without_challenges_sends_no_challenge_key(self) -> None:
        self.response_status = 201
        self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(json.loads(self._single_request().data.decode()),
                         {"doi": "10.5281/zenodo.1"})

    def test_more_than_five_challenges_are_refused(self) -> None:
        challenge_flags = [flag for index in range(6)
                           for flag in ("--challenge", f"id-{index}")]
        self._run(["submit", "--doi", "10.5281/zenodo.1"] + challenge_flags,
                  expected_exit_code=1)
        self.assertEqual(self.sent_requests, [])


if __name__ == "__main__":
    unittest.main()
