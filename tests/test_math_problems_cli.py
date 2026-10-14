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
        ]
        for url, reason in unprinted:
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(self.build_post_problem_argv("--github-link", url, "Label"), expected_exit_code=1)
                self.assertIn(reason, stderr_text)
                self.assertNotIn(url, stderr_text)
        for url in ("HTTPS://github.com/o/r", "https://GitHub.com/o/r", "http://github.com/o/r", "https://github.com",
                    "https://github.com/", "https://github.com/?tab=repositories", "https://github.com/#start",
                    "https://github.com/o/r tracking"):
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


if __name__ == "__main__":
    unittest.main()
