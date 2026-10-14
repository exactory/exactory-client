"""Tests for the math section subcommands of bin/exactory (design 2026-10-12 §17)."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_SHA = "3f2a9c1e4b5d6a7f8091a2b3c4d5e6f708192a3b"
_PROBLEM_ID = "656c336e-e892-4c47-80d2-a71d022f4116"
_PROOF_ID = "1f0d5a52-6f5a-4a3e-9c9d-0d3f6f4b2a10"
_ORCID_ID = "0000-0002-1825-0097"
# The reason the link checks give for a control character, an invisible character, or an
# unpaired surrogate. The tests build those characters with chr(), so no source line holds one.
_NON_TEXT_REASON = "a control character, an invisible character, or an unpaired surrogate"
# The reason the link checks give for a space, or another character that Unicode counts as whitespace and
# that is not a control character. A tab and a line feed are control characters and get the reason above.
_WHITESPACE_REASON = "contains a space or a line break"


def _load_exactory():
    loader = importlib.machinery.SourceFileLoader("exactory_math_cli", str(_PLUGIN_ROOT / "bin" / "exactory"))
    spec = importlib.util.spec_from_loader("exactory_math_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


_cli = _load_exactory()


class _FakeResponse(io.BytesIO):
    """The slice of http.client.HTTPResponse that _send_request touches."""

    def __init__(self, body: bytes, status: int) -> None:
        super().__init__(body)
        self._status = status

    def getcode(self) -> int:
        return self._status


class _MathCliTestCase(unittest.TestCase):
    """A scratch cwd, a test key and origin, and a urlopen stub that answers by method and
    path, so the preflight read and the write are told apart on the real request objects."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch_dir = Path(scratch.name)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.scratch_dir)

        saved = {key: os.environ.get(key) for key in ("EXACTORY_API_KEY", "EXACTORY_API_URL")}

        def restore_env() -> None:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore_env)
        os.environ["EXACTORY_API_KEY"] = "test-key"
        os.environ["EXACTORY_API_URL"] = "https://api.test"

        self.sent: list[urllib.request.Request] = []
        self.me = {"displayName": "Ada", "orcidId": _ORCID_ID}
        self.answers: dict[tuple[str, str], tuple[int, dict]] = {}
        self.addCleanup(setattr, urllib.request, "urlopen", urllib.request.urlopen)

        def answer_request(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent.append(request)
            path = urllib.parse.urlparse(request.full_url).path
            if (request.get_method(), path) == ("GET", "/api/v1/me"):
                return _FakeResponse(json.dumps(self.me).encode(), 200)
            status, body = self.answers.get((request.get_method(), path), (200, {}))
            if status >= 400:
                raise urllib.error.HTTPError(request.full_url, status, "refused", {}, io.BytesIO(json.dumps(body).encode()))
            return _FakeResponse(json.dumps(body).encode(), status)

        urllib.request.urlopen = answer_request

    def run_cli(self, argv: list[str], expected_exit_code: int | None = None) -> tuple[str, str]:
        args = _cli._build_parser().parse_args(argv)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if expected_exit_code is None:
                args.handler(args)
            else:
                with self.assertRaises(SystemExit) as caught:
                    args.handler(args)
                self.assertEqual(caught.exception.code, expected_exit_code)
        return out.getvalue(), err.getvalue()

    def list_sent_requests(self) -> list[tuple[str, str]]:
        return [(request.get_method(), urllib.parse.urlparse(request.full_url).path) for request in self.sent]

    def read_sent_body(self, method: str, path: str) -> dict:
        for request in self.sent:
            if request.get_method() == method and urllib.parse.urlparse(request.full_url).path == path:
                return json.loads(request.data.decode())
        raise AssertionError(f"no {method} {path} was sent")

    def write_citations(self) -> str:
        path = self.scratch_dir / "citations.json"
        path.write_text(json.dumps([{"citation": "H. Minc, Permanents, 1978, chapter 2.", "locator": "10.1017/cbo9781107340688"}]))
        return str(path)

    def build_post_problem_argv(self, *extra: str) -> list[str]:
        return [
            "post-problem",
            "--title", "Dittert conjecture for n = 7",
            "--area", "math.CO",
            "--statement", "s" * 60,
            "--motivation", "m" * 120,
            "--current-state", "c" * 120,
            "--resolution-criteria", "r" * 60,
            "--citations-file", self.write_citations(),
            *extra,
        ]


class TestCallerState(_MathCliTestCase):
    def test_me_prints_the_caller_state(self) -> None:
        stdout_text, _ = self.run_cli(["me"])
        self.assertEqual(json.loads(stdout_text), self.me)


class TestProblemReads(_MathCliTestCase):
    def test_problems_builds_the_search_and_filter_query(self) -> None:
        self.run_cli(["problems", "--query", "permanent", "--status", "all", "--area", "math.CO",
                      "--has-proof", "--has-passing-lean-check", "--sort", "top", "--cursor", "c1", "--limit", "10"])
        parsed = urllib.parse.urlparse(self.sent[0].full_url)
        self.assertEqual(parsed.path, "/api/v1/math-problems")
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {
            "q": ["permanent"], "status": ["all"], "area": ["math.CO"], "hasProof": ["true"],
            "hasPassingLeanCheck": ["true"], "sort": ["top"], "cursor": ["c1"], "limit": ["10"],
        })

    def test_problem_and_history_read_by_id(self) -> None:
        self.run_cli(["problem", _PROBLEM_ID])
        self.run_cli(["problem-history", _PROBLEM_ID, "--limit", "5"])
        self.assertEqual(self.list_sent_requests(), [
            ("GET", f"/api/v1/math-problems/{_PROBLEM_ID}"),
            ("GET", f"/api/v1/math-problems/{_PROBLEM_ID}/status-events"),
        ])


class TestPostProblem(_MathCliTestCase):
    def test_it_posts_the_fields_the_links_and_the_citations(self) -> None:
        self.answers[("POST", "/api/v1/math-problems")] = (201, {"id": _PROBLEM_ID})
        self.run_cli(self.build_post_problem_argv(
            "--paper", "2609.92001", "--github-link", "https://github.com/o/r/issues/1", "Tracking issue"))
        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me"), ("POST", "/api/v1/math-problems")])
        self.assertEqual(self.read_sent_body("POST", "/api/v1/math-problems"), {
            "title": "Dittert conjecture for n = 7",
            "area": "math.CO",
            "statement": "s" * 60,
            "motivation": "m" * 120,
            "currentState": "c" * 120,
            "resolutionCriteria": "r" * 60,
            "citations": [{"citation": "H. Minc, Permanents, 1978, chapter 2.", "locator": "10.1017/cbo9781107340688"}],
            "paperIdentifiers": ["2609.92001"],
            "githubLinks": [{"url": "https://github.com/o/r/issues/1", "label": "Tracking issue"}],
        })

    def test_without_an_orcid_it_stops_before_the_write_and_names_the_account_page(self) -> None:
        self.me = {"displayName": "Ada", "orcidId": None}
        _, stderr_text = self.run_cli(self.build_post_problem_argv(), expected_exit_code=1)
        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me")])
        self.assertIn("https://api.test/account", stderr_text)

    def test_local_bounds_refuse_before_any_request(self) -> None:
        papers = [arg for i in range(11) for arg in ("--paper", f"2609.9{i:04d}")]
        self.run_cli(self.build_post_problem_argv(*papers), expected_exit_code=1)
        self.run_cli([*self.build_post_problem_argv()[:3], "--area", "cs.LG", *self.build_post_problem_argv()[5:]], expected_exit_code=1)
        self.assertEqual(self.sent, [])

    def test_context_links_meet_the_link_rules_before_any_request(self) -> None:
        page = "https://github.com/o/r/"
        unprinted = [
            (page + "a" * 478, "is 501 characters long"),
            # The length is checked first, so a long link with a control character gets that reason.
            (page + chr(0x07) + "a" * 477, "is 501 characters long"),
            (page + chr(0x07), _NON_TEXT_REASON),
            (page + chr(0x200B) + "issues", _NON_TEXT_REASON),
            (page + chr(0xD800), _NON_TEXT_REASON),
            ("https://github.com/o/r tracking", _WHITESPACE_REASON),
        ]
        for url, reason in unprinted:
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(self.build_post_problem_argv("--github-link", url, "Label"), expected_exit_code=1)
                self.assertIn(reason, stderr_text)
                self.assertNotIn(url, stderr_text)
        for url in ("HTTPS://github.com/o/r", "https://GitHub.com/o/r", "http://github.com/o/r", "https://github.com",
                    "https://github.com/", "https://github.com/?tab=repositories", "https://github.com/#start"):
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(self.build_post_problem_argv("--github-link", url, "Label"), expected_exit_code=1)
                self.assertIn(f"{url} is not a context link", stderr_text)
        self.assertEqual(self.sent, [])


class TestVotesAndReportsNeedNoOrcid(_MathCliTestCase):
    def test_a_vote_a_cleared_vote_and_a_report_run_without_a_linked_id(self) -> None:
        self.me = {"displayName": "Ada", "orcidId": None}
        self.answers[("POST", "/api/v1/reports")] = (201, {"id": "r1", "status": "pending"})
        self.run_cli(["vote-problem", _PROBLEM_ID, "--value", "1"])
        self.run_cli(["vote-problem", _PROBLEM_ID, "--value", "0"])
        self.run_cli(["report-math", "math_post", _PROOF_ID, "--note", "Advertising."])
        self.assertEqual(self.list_sent_requests(), [
            ("PUT", f"/api/v1/math-problems/{_PROBLEM_ID}/vote"),
            ("DELETE", f"/api/v1/math-problems/{_PROBLEM_ID}/vote"),
            ("POST", "/api/v1/reports"),
        ])
        self.assertEqual(self.read_sent_body("PUT", f"/api/v1/math-problems/{_PROBLEM_ID}/vote"), {"value": 1})
        self.assertEqual(self.read_sent_body("POST", "/api/v1/reports"),
                         {"subjectKind": "math_post", "subjectId": _PROOF_ID, "note": "Advertising."})

    def test_an_unknown_report_kind_is_refused_by_the_parser(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _cli._build_parser().parse_args(["report-math", "grand_challenge", _PROBLEM_ID])
        self.assertEqual(caught.exception.code, 2)


# The classes below cover the branches of the new handlers that the cases above do not reach: the
# query keys, the encoding of an id, the file form and the bounds of the text fields, the limits at
# 10 and 11, the length of a link, the bounds of a label and a note, and the vote and report bodies.


def _build_paper_options(count: int) -> list[str]:
    return [arg for i in range(count) for arg in ("--paper", f"2609.9{i:04d}")]


def _build_github_link_options(count: int) -> list[str]:
    return [arg for i in range(count)
            for arg in ("--github-link", f"https://github.com/o/r/issues/{i + 1}", f"Issue {i + 1}")]


class TestProblemReadRequests(_MathCliTestCase):
    def read_query(self, request_index: int) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.sent[request_index].full_url).query)

    def test_problems_without_options_asks_for_the_first_page_only(self) -> None:
        self.run_cli(["problems"])
        self.assertEqual(self.read_query(0), {"limit": ["25"]})

    def test_problems_sends_the_formal_statement_filter(self) -> None:
        self.run_cli(["problems", "--has-formal-statement"])
        self.assertEqual(self.read_query(0), {"hasFormalStatement": ["true"], "limit": ["25"]})

    def test_problem_history_sends_the_cursor_and_the_limit(self) -> None:
        self.run_cli(["problem-history", _PROBLEM_ID, "--cursor", "c2", "--limit", "5"])
        self.run_cli(["problem-history", _PROBLEM_ID])
        self.assertEqual(self.read_query(0), {"cursor": ["c2"], "limit": ["5"]})
        self.assertEqual(self.read_query(1), {"limit": ["50"]})


class TestIdsStayInTheirRoute(_MathCliTestCase):
    """An id is one segment of its route: percent-encoding keeps a slash or a question mark inside
    it, and the empty id and the two dot ids are refused, because a URL parser would resolve them."""

    def test_an_id_with_path_characters_stays_one_encoded_segment(self) -> None:
        hostile_id = "../x?y"
        self.run_cli(["problem", hostile_id])
        self.run_cli(["problem-history", hostile_id])
        self.run_cli(["vote-problem", hostile_id, "--value", "1"])
        self.assertEqual(self.list_sent_requests(), [
            ("GET", "/api/v1/math-problems/..%2Fx%3Fy"),
            ("GET", "/api/v1/math-problems/..%2Fx%3Fy/status-events"),
            ("PUT", "/api/v1/math-problems/..%2Fx%3Fy/vote"),
        ])

    def test_an_empty_or_dot_id_is_refused_before_any_request(self) -> None:
        for unroutable_id in ("", ".", ".."):
            for argv in (["problem", unroutable_id], ["problem-history", unroutable_id],
                         ["vote-problem", unroutable_id, "--value", "1"],
                         ["vote-problem", unroutable_id, "--value", "0"]):
                with self.subTest(argv=argv):
                    _, stderr_text = self.run_cli(argv, expected_exit_code=1)
                    self.assertIn("is not an identifier exactory can read", stderr_text)
        self.assertEqual(self.list_sent_requests(), [])

    def test_the_refusal_reads_like_the_refusal_of_the_released_task_command(self) -> None:
        for unroutable_id in ("", ".", ".."):
            with self.subTest(unroutable_id=unroutable_id):
                _, math_stderr_text = self.run_cli(["problem", unroutable_id], expected_exit_code=1)
                _, released_stderr_text = self.run_cli(["task", unroutable_id], expected_exit_code=1)
                self.assertEqual(math_stderr_text, released_stderr_text)
        self.assertEqual(self.list_sent_requests(), [])

    def test_an_id_that_holds_dots_among_other_characters_is_kept(self) -> None:
        kept_ids = ("...", "a..b", ".x", "x.")
        for kept_id in kept_ids:
            self.run_cli(["problem", kept_id])
        self.assertEqual(self.list_sent_requests(), [("GET", f"/api/v1/math-problems/{kept_id}") for kept_id in kept_ids])


class TestPostProblemInputs(_MathCliTestCase):
    def test_the_text_fields_can_come_from_files_and_the_optional_lists_are_left_out(self) -> None:
        argv = ["post-problem", "--title", "Dittert conjecture for n = 7", "--area", "math.CO",
                "--citations-file", self.write_citations()]
        for flag, text in (("statement", "s" * 60), ("motivation", "m" * 120),
                           ("current-state", "c" * 120), ("resolution-criteria", "r" * 60)):
            path = self.scratch_dir / f"{flag}.txt"
            path.write_text(text + "\n", encoding="utf-8")
            argv += [f"--{flag}-file", str(path)]
        self.run_cli(argv)
        body = self.read_sent_body("POST", "/api/v1/math-problems")
        self.assertEqual(
            {key: body[key] for key in ("statement", "motivation", "currentState", "resolutionCriteria")},
            {"statement": "s" * 60, "motivation": "m" * 120, "currentState": "c" * 120, "resolutionCriteria": "r" * 60})
        self.assertNotIn("paperIdentifiers", body)
        self.assertNotIn("githubLinks", body)

    def test_every_text_field_has_the_bounds_of_the_server_schema(self) -> None:
        bounds = {"--title": (8, 200), "--statement": (50, 20000), "--motivation": (100, 10000),
                  "--current-state": (100, 10000), "--resolution-criteria": (50, 5000)}
        for flag, (minimum, maximum) in bounds.items():
            for length, is_accepted in ((minimum - 1, False), (minimum, True), (maximum, True), (maximum + 1, False)):
                with self.subTest(flag=flag, length=length):
                    self.sent.clear()
                    argv = self.build_post_problem_argv()
                    argv[argv.index(flag) + 1] = "x" * length
                    if is_accepted:
                        self.run_cli(argv)
                        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me"), ("POST", "/api/v1/math-problems")])
                    else:
                        self.run_cli(argv, expected_exit_code=1)
                        self.assertEqual(self.sent, [])

    def test_a_post_may_carry_ten_papers_and_ten_context_links(self) -> None:
        self.run_cli(self.build_post_problem_argv(*_build_paper_options(10), *_build_github_link_options(10)))
        body = self.read_sent_body("POST", "/api/v1/math-problems")
        self.assertEqual(len(body["paperIdentifiers"]), 10)
        self.assertEqual(len(body["githubLinks"]), 10)

    def test_eleven_papers_or_eleven_context_links_are_refused_with_the_count_and_one_remedy_per_sentence(self) -> None:
        for extra_args, counted, remedy in (
                (_build_paper_options(11), "11 papers", "Remove some. Attach the rest with link-problem-paper."),
                (_build_github_link_options(11), "11 context links", "Remove some. Attach the rest with link-problem-github.")):
            with self.subTest(counted=counted):
                _, stderr_text = self.run_cli(self.build_post_problem_argv(*extra_args), expected_exit_code=1)
                self.assertIn(counted, stderr_text)
                self.assertIn(remedy, stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_context_link_of_exactly_five_hundred_code_units_is_accepted(self) -> None:
        url = "https://github.com/o/r/" + "a" * 477
        self.run_cli(self.build_post_problem_argv("--github-link", url, "Label"))
        self.assertEqual(self.read_sent_body("POST", "/api/v1/math-problems")["githubLinks"], [{"url": url, "label": "Label"}])

    def test_the_link_length_counts_utf16_code_units_not_code_points(self) -> None:
        # 239 characters beyond the basic plane are 478 code units, so this link has 501 units
        # although it holds only 262 code points.
        url = "https://github.com/o/r/" + chr(0x1F600) * 239
        _, stderr_text = self.run_cli(self.build_post_problem_argv("--github-link", url, "Label"), expected_exit_code=1)
        self.assertIn("is 501 characters long", stderr_text)
        self.assertNotIn(url, stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_link_label_of_no_characters_or_over_one_hundred_is_refused(self) -> None:
        for label in ("", "   ", "l" * 101):
            with self.subTest(label_length=len(label)):
                self.run_cli(self.build_post_problem_argv("--github-link", "https://github.com/o/r", label),
                             expected_exit_code=1)
        self.assertEqual(self.sent, [])

    def test_an_area_is_sent_as_given_because_the_server_trims_it_and_ignores_its_case(self) -> None:
        for area in ("MATH.co", " math.CO", "math.CO ", "  Math.nt  "):
            with self.subTest(area=area):
                self.sent.clear()
                argv = self.build_post_problem_argv()
                argv[argv.index("--area") + 1] = area
                self.run_cli(argv)
                self.assertEqual(self.read_sent_body("POST", "/api/v1/math-problems")["area"], area)

    def test_an_area_that_is_not_a_math_subject_class_is_refused_before_any_request(self) -> None:
        for area in ("cs.LG", "math", "mathematics", "math-ph", "", "  "):
            with self.subTest(area=area):
                argv = self.build_post_problem_argv()
                argv[argv.index("--area") + 1] = area
                _, stderr_text = self.run_cli(argv, expected_exit_code=1)
                self.assertIn("is not a math.* subject class", stderr_text)
        self.assertEqual(self.sent, [])


class TestVoteProblem(_MathCliTestCase):
    def test_a_downvote_sends_the_negative_value(self) -> None:
        self.run_cli(["vote-problem", _PROBLEM_ID, "--value", "-1"])
        self.assertEqual(self.read_sent_body("PUT", f"/api/v1/math-problems/{_PROBLEM_ID}/vote"), {"value": -1})

    def test_the_parser_refuses_a_vote_value_outside_the_three_choices(self) -> None:
        for value in ("1", "-1", "0"):
            self.assertEqual(_cli._build_parser().parse_args(["vote-problem", _PROBLEM_ID, "--value", value]).value, int(value))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _cli._build_parser().parse_args(["vote-problem", _PROBLEM_ID, "--value", "2"])
        self.assertEqual(caught.exception.code, 2)


class TestReportMath(_MathCliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.answers[("POST", "/api/v1/reports")] = (201, {"id": "r1", "status": "pending"})

    def test_a_new_report_without_a_note_sends_no_note_and_prints_no_notice(self) -> None:
        stdout_text, stderr_text = self.run_cli(["report-math", "math_proof", _PROOF_ID])
        self.assertEqual(self.read_sent_body("POST", "/api/v1/reports"), {"subjectKind": "math_proof", "subjectId": _PROOF_ID})
        self.assertEqual(json.loads(stdout_text), {"id": "r1", "status": "pending"})
        self.assertEqual(stderr_text, "")

    def test_every_math_report_kind_is_accepted_and_sent_as_the_subject_kind(self) -> None:
        # The eight report kinds of the math section (design 2026-10-12 §19). The server owns the
        # vocabulary. The list is written out here, so a kind that the parser drops fails this test.
        for kind in ("math_problem", "math_status_event", "math_formal_statement", "math_proof",
                     "math_lean_check", "math_post", "math_problem_paper", "math_github_link"):
            with self.subTest(kind=kind):
                self.sent.clear()
                self.run_cli(["report-math", kind, _PROOF_ID])
                self.assertEqual(self.list_sent_requests(), [("POST", "/api/v1/reports")])
                self.assertEqual(self.read_sent_body("POST", "/api/v1/reports")["subjectKind"], kind)

    def test_an_existing_report_is_named_on_stderr_and_printed(self) -> None:
        self.answers[("POST", "/api/v1/reports")] = (200, {"id": "r1", "status": "pending"})
        stdout_text, stderr_text = self.run_cli(["report-math", "math_proof", _PROOF_ID])
        self.assertEqual(json.loads(stdout_text), {"id": "r1", "status": "pending"})
        self.assertEqual(stderr_text, "The server returned the existing report for this content.\n")

    def test_a_note_of_exactly_one_thousand_characters_joins_the_body(self) -> None:
        self.run_cli(["report-math", "math_proof", _PROOF_ID, "--note", "n" * 1000])
        self.assertEqual(self.read_sent_body("POST", "/api/v1/reports")["note"], "n" * 1000)

    def test_a_note_with_no_text_or_over_one_thousand_characters_is_refused_before_any_request(self) -> None:
        for note in ("", "   ", "n" * 1001):
            with self.subTest(note_length=len(note)):
                self.run_cli(["report-math", "math_proof", _PROOF_ID, "--note", note], expected_exit_code=1)
        self.assertEqual(self.sent, [])


_MOVED_PATH_REASON = 'has a "." or ".." path step, a backslash, or an encoded slash'


class TestFormalStatementsStatusAndLinks(_MathCliTestCase):
    def test_add_formal_statement_sends_the_exact_link_and_the_targets(self) -> None:
        url = f"https://github.com/o/r/blob/{_SHA}/Challenge.lean#L3-L9"
        self.run_cli(["add-formal-statement", _PROBLEM_ID, "--lean-url", url, "--proof-target", "statement",
                      "--disproof-target", "statement_false", "--note", "Lean v4.22.0, Mathlib at 1a2b3c4."])
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/formal-statements"), {
            "leanUrl": url, "proofTargets": ["statement"], "disproofTargets": ["statement_false"],
            "note": "Lean v4.22.0, Mathlib at 1a2b3c4.",
        })

    def test_a_branch_link_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(["add-formal-statement", _PROBLEM_ID, "--lean-url",
                                       "https://github.com/o/r/blob/main/Challenge.lean", "--proof-target", "statement"],
                                      expected_exit_code=1)
        self.assertIn("full 40-character commit SHA", stderr_text)
        self.assertEqual(self.sent, [])

    def test_declare_status_names_the_resolving_proof_for_a_resolution(self) -> None:
        self.run_cli(["declare-status", _PROBLEM_ID, "--status", "proved", "--resolving-proof", _PROOF_ID,
                      "--note", "Proved by the cited proof."])
        self.assertEqual(self.read_sent_body("PATCH", f"/api/v1/math-problems/{_PROBLEM_ID}"),
                         {"status": "proved", "resolvingProofId": _PROOF_ID, "note": "Proved by the cited proof."})
        self.run_cli(["declare-status", _PROBLEM_ID, "--status", "proved", "--note", "x"], expected_exit_code=1)
        self.run_cli(["declare-status", _PROBLEM_ID, "--status", "open", "--resolving-proof", _PROOF_ID, "--note", "x"],
                     expected_exit_code=1)

    def test_links_go_to_their_routes_after_the_preflight(self) -> None:
        self.run_cli(["link-problem-paper", _PROBLEM_ID, "--identifier", "2609.97001v3"])
        self.run_cli(["link-problem-github", _PROBLEM_ID, "--url", "https://github.com/o/r/blob/main/README.md#usage",
                      "--label", "Usage"])
        self.assertEqual(self.list_sent_requests(), [
            ("GET", "/api/v1/me"), ("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/papers"),
            ("GET", "/api/v1/me"), ("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/github-links"),
        ])
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/papers"), {"identifier": "2609.97001v3"})
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/github-links"),
                         {"url": "https://github.com/o/r/blob/main/README.md#usage", "label": "Usage"})

    def test_lean_source_links_meet_the_link_rules_before_any_request(self) -> None:
        pinned = f"https://github.com/o/r/blob/{_SHA}"
        refused = [
            (f"{pinned}/" + "a" * (500 - len(pinned)), "is 501 characters long"),
            (f"{pinned}/Challenge.lean" + chr(0x1B), _NON_TEXT_REASON),
            (f"{pinned}/Chall" + chr(0x202E) + "enge.lean", _NON_TEXT_REASON),
            (f"{pinned}/Challenge.lean" + chr(0xDFFF), _NON_TEXT_REASON),
            # A URL parser resolves each of these, so the commit-pinned link could name a branch.
            (f"{pinned}/../../../../other/repo/blob/main/Challenge.lean", _MOVED_PATH_REASON),
            (f"{pinned}/./Challenge.lean", _MOVED_PATH_REASON),
            (f"{pinned}/%2E%2e/Challenge.lean", _MOVED_PATH_REASON),
            (f"{pinned}/lib/.%2E#L3", _MOVED_PATH_REASON),
            (f"{pinned}/lib/..", _MOVED_PATH_REASON),
            (f"{pinned}/lib%2FChallenge.lean", _MOVED_PATH_REASON),
            (f"{pinned}/lib%2fChallenge.lean", _MOVED_PATH_REASON),
            (f"{pinned}/lib\\Challenge.lean", _MOVED_PATH_REASON),
            (f"https://github.com/o/../blob/{_SHA}/Challenge.lean", _MOVED_PATH_REASON),
        ]
        for url, reason in refused:
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(["add-formal-statement", _PROBLEM_ID, "--lean-url", url,
                                               "--proof-target", "statement"], expected_exit_code=1)
                self.assertIn(reason, stderr_text)
        self.assertEqual(self.sent, [])

    def test_names_made_of_dots_stay_valid_in_a_lean_source_link(self) -> None:
        pinned = f"https://github.com/o/r/blob/{_SHA}"
        for url in (f"{pinned}/.github/Challenge.lean", f"{pinned}/a..b/...lean", f"{pinned}/%2e%2e%2e.lean"):
            with self.subTest(url=url):
                self.run_cli(["add-formal-statement", _PROBLEM_ID, "--lean-url", url, "--proof-target", "statement"])
                self.assertEqual(json.loads(self.sent[-1].data.decode())["leanUrl"], url)

    def test_a_context_link_keeps_a_dot_step_and_refuses_an_invisible_character(self) -> None:
        url = "https://github.com/o/r/tree/main/../docs"
        self.run_cli(["link-problem-github", _PROBLEM_ID, "--url", url, "--label", "Docs"])
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/github-links")["url"], url)
        _, stderr_text = self.run_cli(["link-problem-github", _PROBLEM_ID, "--url", url + chr(0x200B), "--label", "Docs"],
                                      expected_exit_code=1)
        self.assertIn(_NON_TEXT_REASON, stderr_text)


# The classes below cover the branches of the four commands that the cases above do not reach: the optional
# fields, the file form and the bounds of the note, the limit of 20 targets, the shapes of a Lean source link
# that the server accepts or refuses, the label bounds, the preflight, and the ids in the routes.

_LEAN_SOURCE_URL = f"https://github.com/o/r/blob/{_SHA}/Challenge.lean"

# One valid command line for each command that writes to a problem by its id and needs a linked ORCID iD:
# the command, the HTTP method of its write, the route after the problem id, and its other options.
_PROBLEM_WRITE_COMMANDS = (
    ("add-formal-statement", "POST", "/formal-statements",
     ("--lean-url", _LEAN_SOURCE_URL, "--proof-target", "statement")),
    ("declare-status", "PATCH", "", ("--status", "open", "--note", "Reopened after the correction.")),
    ("link-problem-paper", "POST", "/papers", ("--identifier", "2609.97001v3")),
    ("link-problem-github", "POST", "/github-links", ("--url", "https://github.com/o/r", "--label", "Code")),
)


def _build_target_options(flag: str, count: int) -> list[str]:
    return [arg for i in range(count) for arg in (flag, f"target_{i}")]


class TestWritesToAProblem(_MathCliTestCase):
    def test_each_write_reads_the_caller_state_once_and_then_writes(self) -> None:
        for command, method, route_after_id, options in _PROBLEM_WRITE_COMMANDS:
            with self.subTest(command=command):
                self.sent.clear()
                self.run_cli([command, _PROBLEM_ID, *options])
                write_path = f"/api/v1/math-problems/{_PROBLEM_ID}{route_after_id}"
                self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me"), (method, write_path)])

    def test_each_write_prints_the_answer_of_the_server(self) -> None:
        for command, method, route_after_id, options in _PROBLEM_WRITE_COMMANDS:
            with self.subTest(command=command):
                answer = {"id": f"answer of {command}"}
                self.answers[(method, f"/api/v1/math-problems/{_PROBLEM_ID}{route_after_id}")] = (201, answer)
                stdout_text, _ = self.run_cli([command, _PROBLEM_ID, *options])
                self.assertEqual(json.loads(stdout_text), answer)

    def test_without_an_orcid_each_write_stops_before_the_request_and_names_the_account_page(self) -> None:
        self.me = {"displayName": "Ada", "orcidId": None}
        for command, _, _, options in _PROBLEM_WRITE_COMMANDS:
            with self.subTest(command=command):
                self.sent.clear()
                _, stderr_text = self.run_cli([command, _PROBLEM_ID, *options], expected_exit_code=1)
                self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me")])
                self.assertIn("https://api.test/account", stderr_text)

    def test_an_id_with_path_characters_stays_one_encoded_segment(self) -> None:
        for command, method, route_after_id, options in _PROBLEM_WRITE_COMMANDS:
            with self.subTest(command=command):
                self.sent.clear()
                self.run_cli([command, "../x?y", *options])
                write_path = f"/api/v1/math-problems/..%2Fx%3Fy{route_after_id}"
                self.assertEqual(self.list_sent_requests()[-1], (method, write_path))

    def test_an_empty_or_dot_id_is_refused_before_any_request(self) -> None:
        for unroutable_id in ("", ".", ".."):
            for command, _, _, options in _PROBLEM_WRITE_COMMANDS:
                with self.subTest(command=command, unroutable_id=unroutable_id):
                    _, stderr_text = self.run_cli([command, unroutable_id, *options], expected_exit_code=1)
                    self.assertIn("is not an identifier exactory can read", stderr_text)
        self.assertEqual(self.sent, [])


class TestWriteCommandParsers(unittest.TestCase):
    def test_the_parser_refuses_a_command_line_that_misses_a_required_option(self) -> None:
        for command, _, _, options in _PROBLEM_WRITE_COMMANDS:
            _cli._build_parser().parse_args([command, _PROBLEM_ID, *options])
        refused = [
            ["add-formal-statement", _PROBLEM_ID, "--proof-target", "statement"],
            ["add-formal-statement", _PROBLEM_ID, "--lean-url", _LEAN_SOURCE_URL],
            ["add-formal-statement", _PROBLEM_ID, "--lean-url", _LEAN_SOURCE_URL, "--proof-target", "statement",
             "--note", "n", "--note-file", "note.txt"],
            ["declare-status", _PROBLEM_ID, "--note", "x"],
            ["declare-status", _PROBLEM_ID, "--status", "open"],
            ["declare-status", _PROBLEM_ID, "--status", "reopened", "--note", "x"],
            ["declare-status", _PROBLEM_ID, "--status", "open", "--note", "x", "--note-file", "note.txt"],
            ["link-problem-paper", _PROBLEM_ID],
            ["link-problem-github", _PROBLEM_ID, "--url", "https://github.com/o/r"],
            ["link-problem-github", _PROBLEM_ID, "--label", "Code"],
        ]
        for argv in refused:
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        _cli._build_parser().parse_args(argv)
                self.assertEqual(caught.exception.code, 2)

    def test_declare_status_and_problems_take_the_same_three_statuses_and_only_problems_takes_all(self) -> None:
        for status in ("open", "proved", "disproved"):
            _cli._build_parser().parse_args(["problems", "--status", status])
            _cli._build_parser().parse_args(["declare-status", _PROBLEM_ID, "--status", status, "--note", "x"])
        _cli._build_parser().parse_args(["problems", "--status", "all"])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _cli._build_parser().parse_args(["declare-status", _PROBLEM_ID, "--status", "all", "--note", "x"])
        self.assertEqual(caught.exception.code, 2)


class TestFormalStatementInputs(_MathCliTestCase):
    def build_add_formal_statement_argv(self, *extra: str, lean_url: str = _LEAN_SOURCE_URL) -> list[str]:
        return ["add-formal-statement", _PROBLEM_ID, "--lean-url", lean_url, *extra]

    def read_formal_statement_body(self) -> dict:
        return self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/formal-statements")

    def test_the_note_and_the_disproof_targets_are_left_out_when_not_given(self) -> None:
        self.run_cli(self.build_add_formal_statement_argv("--proof-target", "statement"))
        self.assertEqual(self.read_formal_statement_body(),
                         {"leanUrl": _LEAN_SOURCE_URL, "proofTargets": ["statement"], "disproofTargets": []})

    def test_the_targets_keep_the_order_they_were_given_in(self) -> None:
        self.run_cli(self.build_add_formal_statement_argv(
            "--proof-target", "b", "--proof-target", "a", "--disproof-target", "d", "--disproof-target", "c"))
        body = self.read_formal_statement_body()
        self.assertEqual((body["proofTargets"], body["disproofTargets"]), (["b", "a"], ["d", "c"]))

    def test_the_note_can_come_from_a_file(self) -> None:
        note_path = self.scratch_dir / "note.txt"
        note_path.write_text("Lean v4.22.0, Mathlib at 1a2b3c4.\n", encoding="utf-8")
        self.run_cli(self.build_add_formal_statement_argv("--proof-target", "statement", "--note-file", str(note_path)))
        self.assertEqual(self.read_formal_statement_body()["note"], "Lean v4.22.0, Mathlib at 1a2b3c4.")

    def test_a_missing_note_file_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(self.build_add_formal_statement_argv(
            "--proof-target", "statement", "--note-file", str(self.scratch_dir / "absent.txt")), expected_exit_code=1)
        self.assertIn("does not exist", stderr_text)
        self.assertEqual(self.sent, [])

    def test_the_note_is_trimmed_may_be_blank_and_holds_at_most_two_thousand_characters(self) -> None:
        # The server trims the note, stores none for a blank one, and counts the 2000 after the trim.
        for note, sent_note in (("", ""), ("   ", ""), ("  Lean v4.22.0  ", "Lean v4.22.0"), ("n", "n"),
                                ("n" * 2000, "n" * 2000), (" " + "n" * 2000 + " ", "n" * 2000)):
            with self.subTest(note_length=len(note)):
                self.sent.clear()
                self.run_cli(self.build_add_formal_statement_argv("--proof-target", "statement", "--note", note))
                self.assertEqual(self.read_formal_statement_body()["note"], sent_note)
        self.sent.clear()
        self.run_cli(self.build_add_formal_statement_argv("--proof-target", "statement", "--note", "n" * 2001),
                     expected_exit_code=1)
        self.assertEqual(self.sent, [])

    def test_twenty_targets_of_each_kind_are_accepted(self) -> None:
        self.run_cli(self.build_add_formal_statement_argv(
            *_build_target_options("--proof-target", 20), *_build_target_options("--disproof-target", 20)))
        body = self.read_formal_statement_body()
        self.assertEqual((len(body["proofTargets"]), len(body["disproofTargets"])), (20, 20))

    def test_twenty_one_targets_of_either_kind_are_refused_with_the_count_and_one_remedy_per_sentence(self) -> None:
        for extra_args, counted in (
                (_build_target_options("--proof-target", 21), "21 proof targets"),
                ([*_build_target_options("--proof-target", 1), *_build_target_options("--disproof-target", 21)],
                 "21 disproof targets")):
            with self.subTest(counted=counted):
                _, stderr_text = self.run_cli(self.build_add_formal_statement_argv(*extra_args), expected_exit_code=1)
                self.assertIn(counted, stderr_text)
                self.assertIn("The limit is 20. Remove some.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_lean_source_links_in_every_shape_the_server_accepts_are_sent_as_given(self) -> None:
        accepted = [
            _LEAN_SOURCE_URL,
            f"https://github.com/o/r/tree/{_SHA}/lib/Challenge",
            f"https://github.com/o/r/blob/{_SHA}",
            f"https://github.com/o/r/tree/{_SHA}",
            f"https://github.com/o/r/blob/{_SHA}/Challenge.lean#L3",
            f"https://github.com/o/r/blob/{_SHA}/Challenge.lean#L3-L9",
            f"https://github.com/my-org/r.github.io/blob/{_SHA}/Statement.lean",
            f"https://github.com/{'o' * 39}/{'r' * 100}/blob/{_SHA}/Statement.lean",
            f"https://github.com/o/r/blob/{_SHA}/" + "a" * (499 - len(f"https://github.com/o/r/blob/{_SHA}")),
            f"https://github.com/o/r/blob/{_SHA}/lib./Challenge.",
            f"https://github.com/o/r/blob/{_SHA}/%E2%88%80.lean",
            f"https://github.com/o/r/blob/{_SHA}/" + chr(0xDC) + "ber.lean",
            f"https://github.com/o/r/blob/{_SHA}/" + chr(0x1F600) + ".lean",
        ]
        for url in accepted:
            with self.subTest(url=url):
                self.sent.clear()
                self.run_cli(self.build_add_formal_statement_argv("--proof-target", "statement", lean_url=url))
                self.assertEqual(self.read_formal_statement_body()["leanUrl"], url)

    def test_a_link_that_is_not_a_lean_source_at_a_full_commit_is_refused_before_any_request(self) -> None:
        pinned = f"https://github.com/o/r/blob/{_SHA}"
        refused = [
            "",
            f"https://github.com/o/r/blob/{_SHA[:7]}/Challenge.lean",
            f"https://github.com/o/r/blob/{_SHA[:39]}/Challenge.lean",
            f"https://github.com/o/r/blob/{_SHA}0/Challenge.lean",
            f"https://github.com/o/r/blob/{_SHA.upper()}/Challenge.lean",
            f"https://github.com/o/r/blob/{_SHA[:39]}g/Challenge.lean",
            "https://github.com/o/r/blob/HEAD/Challenge.lean",
            f"{pinned}/Challenge.lean?plain=1",
            f"{pinned}/Challenge.lean#L3-9",
            f"{pinned}/Challenge.lean#L",
            f"{pinned}/Challenge.lean#L3-L9-L12",
            f"{pinned}/Challenge.lean#section",
            f"{pinned}/Challenge.lean#L" + chr(0x663),
            f"{pinned}/Challenge.lean#L3-L" + chr(0x663),
            f"{pinned}/",
            f"{pinned}//Challenge.lean",
            f"https://github.com/o/r/commit/{_SHA}",
            f"https://github.com/o/r/raw/{_SHA}/Challenge.lean",
            f"https://github.com/o/r/blame/{_SHA}/Challenge.lean",
            f"http://github.com/o/r/blob/{_SHA}/Challenge.lean",
            f"HTTPS://github.com/o/r/blob/{_SHA}/Challenge.lean",
            f"https://GitHub.com/o/r/blob/{_SHA}/Challenge.lean",
            f"https://www.github.com/o/r/blob/{_SHA}/Challenge.lean",
            f"https://gitlab.com/o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com.evil.test/o/r/blob/{_SHA}/Challenge.lean",
            f"https://githubxcom/o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/blob/{_SHA}/Challenge.lean",
            f"https://github.com/extra/o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/{'o' * 40}/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/-o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o-/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o.o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/_o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o_o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o_/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o{chr(0xE9)}o/r/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o/r~/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o/r{chr(0xE9)}/blob/{_SHA}/Challenge.lean",
            f"https://github.com/o/{'r' * 101}/blob/{_SHA}/Challenge.lean",
            "https://github.com/o/r/actions/runs/123456789",
        ]
        for url in refused:
            with self.subTest(url=url):
                argv = self.build_add_formal_statement_argv("--proof-target", "statement", lean_url=url)
                _, stderr_text = self.run_cli(argv, expected_exit_code=1)
                self.assertIn(f"{url} is not a Lean source link", stderr_text)
        self.assertEqual(self.sent, [])


class TestDeclareStatusInputs(_MathCliTestCase):
    def read_declaration_body(self) -> dict:
        return self.read_sent_body("PATCH", f"/api/v1/math-problems/{_PROBLEM_ID}")

    def test_an_open_declaration_sends_the_status_and_the_note_only(self) -> None:
        self.run_cli(["declare-status", _PROBLEM_ID, "--status", "open", "--note", "Reopened after the correction."])
        self.assertEqual(self.read_declaration_body(), {"status": "open", "note": "Reopened after the correction."})

    def test_a_disproof_names_its_resolving_proof(self) -> None:
        self.run_cli(["declare-status", _PROBLEM_ID, "--status", "disproved", "--resolving-proof", _PROOF_ID,
                      "--note", "Disproved by the cited proof."])
        self.assertEqual(self.read_declaration_body(), {
            "status": "disproved", "resolvingProofId": _PROOF_ID, "note": "Disproved by the cited proof.",
        })

    def test_the_note_can_come_from_a_file(self) -> None:
        note_path = self.scratch_dir / "note.txt"
        note_path.write_text("Reopened after the correction.\n", encoding="utf-8")
        self.run_cli(["declare-status", _PROBLEM_ID, "--status", "open", "--note-file", str(note_path)])
        self.assertEqual(self.read_declaration_body()["note"], "Reopened after the correction.")

    def test_a_missing_note_file_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(["declare-status", _PROBLEM_ID, "--status", "open",
                                       "--note-file", str(self.scratch_dir / "absent.txt")], expected_exit_code=1)
        self.assertIn("does not exist", stderr_text)
        self.assertEqual(self.sent, [])

    def test_the_note_has_the_bounds_of_the_server_schema(self) -> None:
        for note, is_accepted in (("", False), ("   ", False), ("n", True), ("n" * 5000, True), ("n" * 5001, False)):
            with self.subTest(note_length=len(note)):
                self.sent.clear()
                argv = ["declare-status", _PROBLEM_ID, "--status", "open", "--note", note]
                if is_accepted:
                    self.run_cli(argv)
                    self.assertEqual(self.read_declaration_body()["note"], note)
                else:
                    self.run_cli(argv, expected_exit_code=1)
                    self.assertEqual(self.sent, [])

    def test_a_missing_or_extra_resolving_proof_is_refused_with_its_remedy_before_any_request(self) -> None:
        for argv, remedy in (
                (["declare-status", _PROBLEM_ID, "--status", "proved", "--note", "x"],
                 "A proved or disproved declaration names its resolving proof. Add --resolving-proof <proof-id>."),
                (["declare-status", _PROBLEM_ID, "--status", "disproved", "--note", "x"],
                 "A proved or disproved declaration names its resolving proof. Add --resolving-proof <proof-id>."),
                (["declare-status", _PROBLEM_ID, "--status", "open", "--resolving-proof", _PROOF_ID, "--note", "x"],
                 "An open declaration names no resolving proof. Remove --resolving-proof.")):
            with self.subTest(argv=argv):
                _, stderr_text = self.run_cli(argv, expected_exit_code=1)
                self.assertIn(remedy, stderr_text)
        self.assertEqual(self.sent, [])


class TestLinkInputs(_MathCliTestCase):
    def test_the_identifier_is_sent_as_given(self) -> None:
        for identifier in ("10.5281/ZENODO.4242", "arXiv:2609.97001V3", "hep-th/9901001"):
            with self.subTest(identifier=identifier):
                self.sent.clear()
                self.run_cli(["link-problem-paper", _PROBLEM_ID, "--identifier", identifier])
                self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/papers"),
                                 {"identifier": identifier})

    def test_a_context_link_that_is_not_a_github_page_is_refused_before_any_request(self) -> None:
        for url in ("http://github.com/o/r", "https://gitlab.com/o/r", "https://github.com/"):
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(["link-problem-github", _PROBLEM_ID, "--url", url, "--label", "Code"],
                                              expected_exit_code=1)
                self.assertIn(f"{url} is not a context link", stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_label_of_no_characters_or_over_one_hundred_is_refused_before_any_request(self) -> None:
        for label in ("", "   ", "l" * 101):
            with self.subTest(label_length=len(label)):
                self.run_cli(["link-problem-github", _PROBLEM_ID, "--url", "https://github.com/o/r", "--label", label],
                             expected_exit_code=1)
        self.assertEqual(self.sent, [])

    def test_a_label_of_one_hundred_characters_is_accepted_and_a_padded_label_is_trimmed(self) -> None:
        for label, sent_label in (("l" * 100, "l" * 100), ("  Usage  ", "Usage")):
            with self.subTest(label=label):
                self.sent.clear()
                self.run_cli(["link-problem-github", _PROBLEM_ID, "--url", "https://github.com/o/r", "--label", label])
                body = self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/github-links")
                self.assertEqual(body["label"], sent_label)


def _build_links_with_a_space(link: str) -> list[str]:
    """The link with a space before it, after it, and inside it."""
    return [" " + link, link + " ", link[:-3] + " " + link[-3:]]


class TestWhitespaceInALink(_MathCliTestCase):
    """Every kind of link refuses whitespace with one message that names it, whatever else is wrong with the link."""

    def build_lean_source_argv(self, url: str) -> list[str]:
        return ["add-formal-statement", _PROBLEM_ID, "--lean-url", url, "--proof-target", "statement"]

    def build_context_link_argv(self, url: str) -> list[str]:
        return ["link-problem-github", _PROBLEM_ID, "--url", url, "--label", "Code"]

    def list_link_kinds(self) -> tuple:
        """For each kind of link: its name in the messages, a valid link, and the command line that posts a link."""
        return (
            ("Lean source link", _LEAN_SOURCE_URL, self.build_lean_source_argv),
            ("context link", "https://github.com/o/r/issues/1", self.build_context_link_argv),
        )

    def test_a_space_before_after_or_inside_a_link_is_named_as_the_cause(self) -> None:
        for kind, link, build_argv in self.list_link_kinds():
            for url in _build_links_with_a_space(link):
                with self.subTest(kind=kind, url=url):
                    _, stderr_text = self.run_cli(build_argv(url), expected_exit_code=1)
                    self.assertIn(f"The {kind} {_WHITESPACE_REASON}. Give the link without them.", stderr_text)
                    self.assertNotIn(url, stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_space_is_named_before_the_shape_of_the_link_is_judged(self) -> None:
        for kind, url, build_argv in (
                ("Lean source link", "https://github.com/o/r/blob/main/Chall enge.lean", self.build_lean_source_argv),
                ("context link", "http://github.com/o/r tracking", self.build_context_link_argv)):
            with self.subTest(kind=kind):
                _, stderr_text = self.run_cli(build_argv(url), expected_exit_code=1)
                self.assertIn(f"The {kind} {_WHITESPACE_REASON}.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_every_other_space_character_and_the_line_and_paragraph_separators_are_named_too(self) -> None:
        for character in (chr(0xA0), chr(0x1680), chr(0x2000), chr(0x2003), chr(0x200A), chr(0x2028), chr(0x2029),
                          chr(0x202F), chr(0x205F), chr(0x3000)):
            for kind, link, build_argv in self.list_link_kinds():
                with self.subTest(kind=kind, character=hex(ord(character))):
                    _, stderr_text = self.run_cli(build_argv(link[:-3] + character + link[-3:]), expected_exit_code=1)
                    self.assertIn(f"The {kind} {_WHITESPACE_REASON}.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_the_shape_pattern_refuses_whitespace_by_itself_like_the_server_pattern(self) -> None:
        # The commands refuse whitespace before the pattern runs, so the pattern is checked on its own.
        self.assertIsNotNone(_cli._LEAN_SOURCE_URL_RE.fullmatch(_LEAN_SOURCE_URL))
        for url in (*_build_links_with_a_space(_LEAN_SOURCE_URL),
                    _LEAN_SOURCE_URL[:-3] + chr(0xA0) + _LEAN_SOURCE_URL[-3:]):
            with self.subTest(url=url):
                self.assertIsNone(_cli._LEAN_SOURCE_URL_RE.fullmatch(url))

    def test_the_length_and_the_characters_are_judged_before_whitespace(self) -> None:
        for kind, link, build_argv in self.list_link_kinds():
            with self.subTest(kind=kind, rule="length"):
                _, stderr_text = self.run_cli(build_argv(link + " " + "a" * (500 - len(link))), expected_exit_code=1)
                self.assertIn(f"The {kind} is 501 characters long.", stderr_text)
            for character in (chr(0x09), chr(0x0A), chr(0x0D)):
                with self.subTest(kind=kind, character=hex(ord(character))):
                    _, stderr_text = self.run_cli(build_argv(link + character), expected_exit_code=1)
                    self.assertIn(f"The {kind} contains {_NON_TEXT_REASON}.", stderr_text)
                    self.assertNotIn(_WHITESPACE_REASON, stderr_text)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
