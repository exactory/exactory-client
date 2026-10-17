"""Tests for the math section subcommands of bin/exactory (design 2026-10-12 §17)."""

from __future__ import annotations

import contextlib
import errno
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from integration_fixtures import build_unconfirmed_write_msg

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
# What argv holds for the byte 0xFF, which is not UTF-8 text: Python decodes it to a lone surrogate.
_ARGUMENT_FROM_BYTE_FF = chr(0xDCFF)
# The message for a command argument that has no UTF-8 encoding.
_NOT_UTF8_ERR_MSG = "A command argument is not valid UTF-8 text. Give every argument as UTF-8 text."


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
    """A scratch cwd, a test key and origin, and a stub of open_url_within_origin, the one function
    that bin/exactory sends every request through. The stub answers by method and path, so the
    preflight read and the write are told apart on the real request objects."""

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
        self.addCleanup(setattr, _cli, "open_url_within_origin", _cli.open_url_within_origin)

        def answer_request(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent.append(request)
            path = urllib.parse.urlparse(request.full_url).path
            if (request.get_method(), path) == ("GET", "/api/v1/me"):
                return _FakeResponse(json.dumps(self.me).encode(), 200)
            status, body = self.answers.get((request.get_method(), path), (200, {}))
            if status >= 400:
                raise urllib.error.HTTPError(request.full_url, status, "refused", {}, io.BytesIO(json.dumps(body).encode()))
            return _FakeResponse(json.dumps(body).encode(), status)

        _cli.open_url_within_origin = answer_request

    def run_cli(self, argv: list[str], expected_exit_code: int | None = None) -> tuple[str, str]:
        args = _cli._build_parser().parse_args(argv)
        _cli._API_BASE_URL = _cli._read_checked_api_base_url()
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


class TestRemovedProblemLinks(_MathCliTestCase):
    def test_a_removed_paper_or_context_link_has_its_own_exit_code_and_names_the_link(self) -> None:
        for command, option, identifier, resource, extra in (
                ("link-problem-paper", "--identifier", "2609.97001v3", "papers", []),
                ("link-problem-github", "--url", "https://github.com/o/r/issues/1", "github-links",
                 ["--label", "Context"])):
            with self.subTest(command=command):
                self.sent.clear()
                path = f"/api/v1/math-problems/{_PROBLEM_ID}/{resource}"
                self.answers[("POST", path)] = (409, {"error": "conflict"})
                out, err = self.run_cli([command, _PROBLEM_ID, option, identifier, *extra], expected_exit_code=3)
                self.assertEqual(out, "")
                self.assertIn(_PROBLEM_ID, err)
                self.assertIn(identifier, err)
                self.assertIn("A moderator removed", err)
                self.assertIn("cannot be attached again", err)
                self.assertIn("Nothing changed.", err)
                self.assertNotIn("run the command again", err)
                self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me"), ("POST", path)])

    def test_a_conflict_on_another_math_write_keeps_the_general_failure_code(self) -> None:
        self.answers[("POST", "/api/v1/math-problems")] = (409, {"error": "conflict"})
        _, err = self.run_cli(self.build_post_problem_argv(), expected_exit_code=1)
        self.assertNotIn("moderator removed", err)


class TestPlainTextLineBreaks(_MathCliTestCase):
    def test_a_discussion_at_the_normalized_limit_is_sent_with_lf(self) -> None:
        body = "xx\r\n" * 3333 + "x"
        self.run_cli(["post-discussion", _PROBLEM_ID, "--body", body])
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/posts")["body"],
                         "xx\n" * 3333 + "x")

    def test_a_lone_carriage_return_is_sent_as_lf(self) -> None:
        self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "first\rsecond"])
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/posts")["body"],
                         "first\nsecond")

    def test_line_breaks_alone_are_empty_after_trimming_and_send_nothing(self) -> None:
        _, err = self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "\r\n"], expected_exit_code=1)
        self.assertIn("0 characters long", err)
        self.assertEqual(self.sent, [])

    def test_citations_and_search_also_normalize_line_breaks(self) -> None:
        argv = self.build_post_problem_argv()
        citations = self.write_citations()
        Path(citations).write_text(json.dumps([{"citation": "A citation\r\nwith\rlines", "locator": "https://example.org/a\rb"}]))
        self.run_cli(argv)
        self.assertEqual(self.read_sent_body("POST", "/api/v1/math-problems")["citations"],
                         [{"citation": "A citation\nwith\nlines", "locator": "https://example.org/a\nb"}])
        self.run_cli(["problems", "--query", "first\r\nsecond"])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(self.sent[-1].full_url).query)["q"], ["first\nsecond"])

    def test_challenge_text_keeps_inline_and_file_line_breaks(self) -> None:
        statement = "p" * 200 + "\r\ninside\rend"
        path = self.scratch_dir / "statement.txt"
        path.write_bytes(statement.encode())
        for flag, value in (("--problem-statement", statement), ("--problem-statement-file", str(path))):
            with self.subTest(flag=flag):
                self.sent.clear()
                self.run_cli(["post-challenge", "--title", "A title\r\ninside\rend", "--field", "math",
                              flag, value, "--current-state", "c" * 100, "--resolution-criteria", "r" * 50,
                              "--citations-file", self.write_citations()])
                body = self.read_sent_body("POST", "/api/v1/grand-challenges")
                self.assertEqual(body["title"], "A title\r\ninside\rend")
                self.assertEqual(body["problemStatement"], statement)


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
                         ["vote-problem", unroutable_id, "--value", "0"],
                         ["link-problem-paper", unroutable_id, "--identifier", "10.5281/zenodo.4242"],
                         ["link-problem-github", unroutable_id, "--url", "https://github.com/o/r", "--label", "Context"]):
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
    def test_each_problem_text_field_counts_and_sends_trimmed_text(self) -> None:
        for flag, field, minimum, maximum in (("--title", "title", 8, 200), ("--statement", "statement", 50, 20000),
                ("--motivation", "motivation", 100, 10000), ("--current-state", "currentState", 100, 10000),
                ("--resolution-criteria", "resolutionCriteria", 50, 5000)):
            for length in (minimum - 1, minimum, maximum):
                with self.subTest(flag=flag, length=length):
                    self.sent.clear()
                    argv = self.build_post_problem_argv()
                    argv[argv.index(flag) + 1] = " \ufeff\r\n" + "x" * length + "\r\ufeff "
                    if length < minimum:
                        self.run_cli(argv, expected_exit_code=1)
                        self.assertEqual(self.sent, [])
                    else:
                        self.run_cli(argv)
                        self.assertEqual(self.read_sent_body("POST", "/api/v1/math-problems")[field], "x" * length)

    def test_a_title_of_spaces_is_refused_before_any_request(self) -> None:
        argv = self.build_post_problem_argv()
        argv[argv.index("--title") + 1] = " " * 20
        self.run_cli(argv, expected_exit_code=1)
        self.assertEqual(self.sent, [])

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
            {"statement": "s" * 60, "motivation": "m" * 120,
             "currentState": "c" * 120, "resolutionCriteria": "r" * 60})
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
# The two kinds of check evidence link: a GitHub Actions run, and a file at a full commit.
_CHECK_RUN_URL = "https://github.com/checker/runs/actions/runs/123456789"
_CHECK_FILE_URL = f"https://github.com/checker/runs/blob/{_SHA}/logs/check.txt"

# One valid command line for each command that writes to a problem by its id and needs a linked ORCID iD:
# the command, the HTTP method of its write, the route after the problem id, and its other options.
_PROBLEM_WRITE_COMMANDS = (
    ("add-formal-statement", "POST", "/formal-statements",
     ("--lean-url", _LEAN_SOURCE_URL, "--proof-target", "statement")),
    ("declare-status", "PATCH", "", ("--status", "open", "--note", "Reopened after the correction.")),
    ("link-problem-paper", "POST", "/papers", ("--identifier", "2609.97001v3")),
    ("link-problem-github", "POST", "/github-links", ("--url", "https://github.com/o/r", "--label", "Code")),
)


def _build_repeated_options(flag: str, count: int) -> list[str]:
    return [arg for i in range(count) for arg in (flag, f"value_{i}")]


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
            *_build_repeated_options("--proof-target", 20), *_build_repeated_options("--disproof-target", 20)))
        body = self.read_formal_statement_body()
        self.assertEqual((len(body["proofTargets"]), len(body["disproofTargets"])), (20, 20))

    def test_twenty_one_targets_of_either_kind_are_refused_with_the_count_and_one_remedy_per_sentence(self) -> None:
        for extra_args, counted in (
                (_build_repeated_options("--proof-target", 21), "21 proof targets"),
                ([*_build_repeated_options("--proof-target", 1), *_build_repeated_options("--disproof-target", 21)],
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
                 "An open declaration names no resolving proof. Remove --resolving-proof."),
                (["declare-status", _PROBLEM_ID, "--status", "open", "--resolving-proof", "", "--note", "x"],
                 "An open declaration names no resolving proof. Remove --resolving-proof."),
                (["declare-status", _PROBLEM_ID, "--status", "proved", "--resolving-proof", "", "--note", "x"],
                 "A proved or disproved declaration names its resolving proof. Add --resolving-proof <proof-id>.")):
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

    def build_check_evidence_argv(self, url: str) -> list[str]:
        return ["post-lean-check", _PROOF_ID, "--outcome", "pass", "--tool", "t", "--lean-toolchain", "v",
                "--evidence-url", url]

    def list_link_kinds(self) -> tuple:
        """For each kind of link: its name in the messages, a valid link, and the command line that posts a link."""
        return (
            ("Lean source link", _LEAN_SOURCE_URL, self.build_lean_source_argv),
            ("context link", "https://github.com/o/r/issues/1", self.build_context_link_argv),
            ("check evidence link", _CHECK_RUN_URL, self.build_check_evidence_argv),
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
                ("context link", "http://github.com/o/r tracking", self.build_context_link_argv),
                ("check evidence link", "https://example.org/log txt", self.build_check_evidence_argv)):
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


class TestLinkPatternsOnAHostilePath(unittest.TestCase):
    def test_both_link_checks_refuse_a_long_hostile_path_in_linear_time(self) -> None:
        # A segment class that admitted "/" would split this path in 2^26 ways and try each
        # one before the trailing "?" fails the match.
        hostile = f"https://github.com/o/r/blob/{_SHA}" + "/a" * 26 + "?"
        started = time.perf_counter()
        self.assertIsNone(_cli._LEAN_SOURCE_URL_RE.fullmatch(hostile))
        self.assertIsNone(_cli._CHECK_EVIDENCE_URL_RE.fullmatch(hostile))
        self.assertLess(time.perf_counter() - started, 0.1)


class TestProofsChecksAndDiscussion(_MathCliTestCase):
    def test_the_plugin_posts_a_formal_statement_a_lean_proof_and_a_check_report(self) -> None:
        statement_url = f"https://github.com/o/r/blob/{_SHA}/Challenge.lean"
        solution_url = f"https://github.com/o/r/tree/{_SHA}/Solution"
        evidence_url = "https://github.com/checker/runs/actions/runs/123456789"
        self.run_cli(["add-formal-statement", _PROBLEM_ID, "--lean-url", statement_url, "--proof-target", "statement"])
        self.run_cli(["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "p" * 120,
                      "--lean-url", solution_url, "--formal-statement-version", "1"])
        self.run_cli(["post-lean-check", _PROOF_ID, "--outcome", "pass",
                      "--tool", "leanprover/comparator at commit 1a2b3c4", "--lean-toolchain", "leanprover/lean4:v4.22.0",
                      "--axiom", "propext", "--axiom", "Classical.choice", "--axiom", "Quot.sound",
                      "--evidence-url", evidence_url])

        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/proofs"), {
            "claim": "proves", "summary": "p" * 120, "lean": {"url": solution_url, "formalStatementVersion": 1},
        })
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-proofs/{_PROOF_ID}/lean-checks"), {
            "outcome": "pass", "tool": "leanprover/comparator at commit 1a2b3c4",
            "leanToolchain": "leanprover/lean4:v4.22.0", "axioms": ["propext", "Classical.choice", "Quot.sound"],
            "evidenceUrl": evidence_url,
        })

    def test_local_checks_refuse_an_unversioned_arxiv_write_up_and_a_non_github_evidence_link(self) -> None:
        _, stderr_text = self.run_cli(["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "p" * 120,
                                       "--writeup", "2601.12345"], expected_exit_code=1)
        self.assertIn("2601.12345v2", stderr_text)
        self.run_cli(["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "p" * 120], expected_exit_code=1)
        self.run_cli(["post-lean-check", _PROOF_ID, "--outcome", "fail", "--tool", "t", "--lean-toolchain", "v",
                      "--evidence-url", "https://example.org/log.txt"], expected_exit_code=1)
        self.assertEqual(self.sent, [])

    def test_check_evidence_and_lean_solution_links_meet_the_link_rules_before_any_request(self) -> None:
        run = "https://github.com/checker/runs/actions/runs/123456789"
        pinned_file = f"https://github.com/checker/runs/blob/{_SHA}/logs/check.txt"
        refused = [
            (run + "/" + "1" * (500 - len(run)), "is 501 characters long"),
            (run + chr(0x7F), _NON_TEXT_REASON),
            (pinned_file + chr(0x2066), _NON_TEXT_REASON),
            (pinned_file + chr(0xD800), _NON_TEXT_REASON),
            (f"https://github.com/checker/runs/blob/{_SHA}/logs/../../../other/repo/blob/main/check.txt",
             _MOVED_PATH_REASON),
            (f"https://github.com/checker/runs/blob/{_SHA}/logs/%2e/check.txt", _MOVED_PATH_REASON),
            (f"https://github.com/checker/runs/blob/{_SHA}/logs%2Fcheck.txt", _MOVED_PATH_REASON),
            (f"https://github.com/checker/runs/blob/{_SHA}/logs\\check.txt", _MOVED_PATH_REASON),
        ]
        for url, reason in refused:
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(["post-lean-check", _PROOF_ID, "--outcome", "pass", "--tool", "t",
                                               "--lean-toolchain", "v", "--evidence-url", url], expected_exit_code=1)
                self.assertIn(reason, stderr_text)
        _, stderr_text = self.run_cli(["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "p" * 120,
                                       "--lean-url", f"https://github.com/o/r/tree/{_SHA}/Solution/..",
                                       "--formal-statement-version", "1"], expected_exit_code=1)
        self.assertIn(_MOVED_PATH_REASON, stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_versioned_arxiv_write_up_and_a_revision_are_forwarded(self) -> None:
        self.run_cli(["post-proof", _PROBLEM_ID, "--claim", "disproves", "--summary", "d" * 120,
                      "--writeup", "2601.12345v2", "--supersedes", _PROOF_ID])
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/proofs"),
                         {"claim": "disproves", "summary": "d" * 120, "writeup": "2601.12345v2", "supersedesProofId": _PROOF_ID})

    def test_reads_and_the_discussion(self) -> None:
        self.run_cli(["proofs", _PROBLEM_ID])
        self.run_cli(["proof", _PROOF_ID])
        self.run_cli(["lean-checks", _PROOF_ID])
        self.run_cli(["lean-check", _PROOF_ID])
        self.run_cli(["discussion", _PROBLEM_ID])
        self.run_cli(["discussion-post", _PROOF_ID])
        self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "Is $n = 7$ within reach?", "--reply-to", _PROOF_ID])
        self.assertEqual(self.list_sent_requests(), [
            ("GET", f"/api/v1/math-problems/{_PROBLEM_ID}/proofs"),
            ("GET", f"/api/v1/math-proofs/{_PROOF_ID}"),
            ("GET", f"/api/v1/math-proofs/{_PROOF_ID}/lean-checks"),
            ("GET", f"/api/v1/math-lean-checks/{_PROOF_ID}"),
            ("GET", f"/api/v1/math-problems/{_PROBLEM_ID}/posts"),
            ("GET", f"/api/v1/math-posts/{_PROOF_ID}"),
            ("GET", "/api/v1/me"),
            ("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/posts"),
        ])
        self.assertEqual(self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/posts"),
                         {"body": "Is $n = 7$ within reach?", "replyToPostId": _PROOF_ID})


class TestSubmitWithMathProblems(_MathCliTestCase):
    def test_a_submission_with_problems_runs_the_preflight_and_sends_the_ids(self) -> None:
        self.answers[("POST", "/api/v1/verifications")] = (201, {"id": "v1"})
        self.run_cli(["submit", "--arxiv-id", "2601.12345v2", "--math-problem", _PROBLEM_ID])
        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me"), ("POST", "/api/v1/verifications")])
        self.assertEqual(self.read_sent_body("POST", "/api/v1/verifications"),
                         {"arxivId": "2601.12345v2", "mathProblemIds": [_PROBLEM_ID]})

    def test_a_submission_without_problems_runs_no_preflight(self) -> None:
        self.me = {"displayName": "Ada", "orcidId": None}
        self.answers[("POST", "/api/v1/verifications")] = (201, {"id": "v1"})
        self.run_cli(["submit", "--arxiv-id", "2601.12345v2"])
        self.assertEqual(self.list_sent_requests(), [("POST", "/api/v1/verifications")])

    def test_four_problems_are_refused_before_any_request(self) -> None:
        argv = ["submit", "--arxiv-id", "2601.12345v2"]
        for _ in range(4):
            argv += ["--math-problem", _PROBLEM_ID]
        self.run_cli(argv, expected_exit_code=1)
        self.assertEqual(self.sent, [])


# The classes below cover the branches of the new commands that the cases above do not reach: the shapes of a check
# evidence link, the forms of a write-up, the bounds of every text field, the file forms, the limit of 20 axioms,
# the query of a list, the ids in the routes, the limit of 3 problems at submission, and the parsers.

_LEAN_SOLUTION_URL = f"https://github.com/o/r/tree/{_SHA}/Solution"


class TestPostProofInputs(_MathCliTestCase):
    def build_post_proof_argv(self, *extra: str) -> list[str]:
        return ["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "p" * 120, *extra]

    def read_proof_body(self) -> dict:
        return self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/proofs")

    def test_a_proof_sends_only_the_parts_it_was_given(self) -> None:
        lean = ["--lean-url", _LEAN_SOLUTION_URL, "--formal-statement-version", "2"]
        lean_part = {"url": _LEAN_SOLUTION_URL, "formalStatementVersion": 2}
        for extra_args, sent_parts in (
                (["--writeup", "2601.12345v2"], {"writeup": "2601.12345v2"}),
                (lean, {"lean": lean_part}),
                ([*lean, "--writeup", "10.5281/zenodo.123456"], {"writeup": "10.5281/zenodo.123456", "lean": lean_part})):
            with self.subTest(extra_args=extra_args):
                self.sent.clear()
                self.run_cli(self.build_post_proof_argv(*extra_args))
                self.assertEqual(self.read_proof_body(), {"claim": "proves", "summary": "p" * 120, **sent_parts})

    def test_a_proof_without_a_writeup_and_without_a_lean_solution_is_refused_with_its_remedy(self) -> None:
        for extra_args in ([], ["--formal-statement-version", "1"]):
            with self.subTest(extra_args=extra_args):
                _, stderr_text = self.run_cli(self.build_post_proof_argv(*extra_args), expected_exit_code=1)
                self.assertIn("A proof carries a write-up, a Lean solution, or both. Add --writeup,"
                              " or --lean-url with --formal-statement-version.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_lean_solution_and_its_formal_statement_version_come_together(self) -> None:
        for extra_args in (["--writeup", "2601.12345v2", "--lean-url", _LEAN_SOLUTION_URL],
                           ["--writeup", "2601.12345v2", "--formal-statement-version", "1"]):
            with self.subTest(extra_args=extra_args):
                _, stderr_text = self.run_cli(self.build_post_proof_argv(*extra_args), expected_exit_code=1)
                self.assertIn("A Lean solution names the formal statement version it targets."
                              " Give --lean-url and --formal-statement-version together.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_writeup_that_names_a_version_or_a_record_is_sent_trimmed(self) -> None:
        for writeup in ("2601.12345v2", "2601.12345V2", "https://arxiv.org/abs/2601.12345v3", "10.48550/arXiv.2601.12345v2",
                        "hep-th/9901001v3", "math.GT/0309136v1", "10.5281/zenodo.123456", "https://zenodo.org/records/123456"):
            for padding in ("", "  "):
                with self.subTest(writeup=writeup, padding=padding):
                    self.sent.clear()
                    self.run_cli(self.build_post_proof_argv("--writeup", padding + writeup + padding))
                    self.assertEqual(self.read_proof_body()["writeup"], writeup)

    def test_an_arxiv_id_in_any_form_without_a_version_is_refused_with_the_version_in_the_remedy(self) -> None:
        for writeup in ("2601.12345", "2601.1234", "https://arxiv.org/abs/2601.12345", "http://www.arxiv.org/abs/2601.12345",
                        "10.48550/arXiv.2601.12345", "10.48550/ARXIV.2601.12345", "HTTPS://ARXIV.ORG/ABS/2601.12345",
                        "hep-th/9901001", "math.GT/0309136", "HEP-TH/9901001",
                        "https://arxiv.org/abs/hep-th/9901001", "10.48550/arXiv.hep-th/9901001"):
            with self.subTest(writeup=writeup):
                _, stderr_text = self.run_cli(self.build_post_proof_argv("--writeup", f"  {writeup}  "), expected_exit_code=1)
                self.assertIn(f"The write-up {writeup} is an arXiv id without a version."
                              f" Name the exact version, for example {writeup}v2.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_an_identifier_that_is_not_an_arxiv_id_is_left_to_the_server(self) -> None:
        # Only an arXiv id without a version is known to be refused. Any other identifier goes out as given.
        for writeup in ("2601.123456", "hep-th/990100", "10.1234/other-doi"):
            with self.subTest(writeup=writeup):
                self.sent.clear()
                self.run_cli(self.build_post_proof_argv("--writeup", writeup))
                self.assertEqual(self.read_proof_body()["writeup"], writeup)

    def test_the_summary_has_the_bounds_of_the_server_schema(self) -> None:
        for summary, expected in (("s" * 99, None), ("s" * 100, "s" * 100), ("s" * 20000, "s" * 20000),
                                   ("s" * 20001, None), (" \ufeff" + "s" * 100 + "\ufeff ", "s" * 100),
                                   (" \ufeff" + "s" * 20000 + "\ufeff ", "s" * 20000), (" " * 120, None)):
            with self.subTest(summary_length=len(summary)):
                self.sent.clear()
                argv = ["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", summary, "--writeup", "2601.12345v2"]
                if expected is not None:
                    self.run_cli(argv)
                    self.assertEqual(self.read_proof_body()["summary"], expected)
                else:
                    self.run_cli(argv, expected_exit_code=1)
                    self.assertEqual(self.sent, [])

    def test_the_summary_can_come_from_a_file(self) -> None:
        summary_path = self.scratch_dir / "summary.tex"
        summary_path.write_text("s" * 100 + "\n", encoding="utf-8")
        self.run_cli(["post-proof", _PROBLEM_ID, "--claim", "disproves", "--summary-file", str(summary_path),
                      "--writeup", "2601.12345v2"])
        self.assertEqual(self.read_proof_body()["summary"], "s" * 100)

    def test_a_missing_summary_file_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(["post-proof", _PROBLEM_ID, "--claim", "proves",
                                       "--summary-file", str(self.scratch_dir / "absent.tex"),
                                       "--writeup", "2601.12345v2"], expected_exit_code=1)
        self.assertIn("does not exist", stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_lean_solution_at_a_branch_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(self.build_post_proof_argv(
            "--lean-url", "https://github.com/o/r/blob/main/Solution.lean", "--formal-statement-version", "1"),
            expected_exit_code=1)
        self.assertIn("full 40-character commit SHA", stderr_text)
        self.assertEqual(self.sent, [])

    def test_an_empty_revision_id_is_sent_for_the_server_to_judge(self) -> None:
        self.run_cli(self.build_post_proof_argv("--writeup", "2601.12345v2", "--supersedes", ""))
        self.assertEqual(self.read_proof_body()["supersedesProofId"], "")


class TestLeanCheckInputs(_MathCliTestCase):
    def build_post_lean_check_argv(self, *extra: str, outcome: str = "pass", evidence_url: str = _CHECK_RUN_URL) -> list[str]:
        return ["post-lean-check", _PROOF_ID, "--outcome", outcome, "--tool", "t", "--lean-toolchain", "v",
                "--evidence-url", evidence_url, *extra]

    def read_report_body(self) -> dict:
        return self.read_sent_body("POST", f"/api/v1/math-proofs/{_PROOF_ID}/lean-checks")

    def test_a_report_without_axioms_or_an_excerpt_sends_an_empty_axiom_list_and_no_excerpt(self) -> None:
        self.run_cli(self.build_post_lean_check_argv())
        self.assertEqual(self.read_report_body(),
                         {"outcome": "pass", "tool": "t", "leanToolchain": "v", "axioms": [], "evidenceUrl": _CHECK_RUN_URL})

    def test_a_failing_outcome_is_forwarded(self) -> None:
        self.run_cli(self.build_post_lean_check_argv(outcome="fail"))
        self.assertEqual(self.read_report_body()["outcome"], "fail")

    def test_the_axioms_keep_the_order_they_were_given_in(self) -> None:
        self.run_cli(self.build_post_lean_check_argv("--axiom", "Quot.sound", "--axiom", "propext"))
        self.assertEqual(self.read_report_body()["axioms"], ["Quot.sound", "propext"])

    def test_twenty_axioms_are_accepted(self) -> None:
        self.run_cli(self.build_post_lean_check_argv(*_build_repeated_options("--axiom", 20)))
        self.assertEqual(len(self.read_report_body()["axioms"]), 20)

    def test_twenty_one_axioms_are_refused_with_the_count_and_one_remedy_per_sentence(self) -> None:
        _, stderr_text = self.run_cli(self.build_post_lean_check_argv(*_build_repeated_options("--axiom", 21)), expected_exit_code=1)
        self.assertIn("The command names 21 axioms. The limit is 20. Remove some.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_the_tool_and_the_toolchain_have_the_bounds_of_the_server_schema(self) -> None:
        for flag, field, maximum in (("--tool", "tool", 200), ("--lean-toolchain", "leanToolchain", 100)):
            for value, is_accepted in (("", False), ("   ", False), ("x", True), ("x" * maximum, True), (" x ", True),
                                       ("x" * (maximum + 1), False)):
                with self.subTest(flag=flag, value_length=len(value)):
                    self.sent.clear()
                    argv = self.build_post_lean_check_argv()
                    argv[argv.index(flag) + 1] = value
                    if is_accepted:
                        self.run_cli(argv)
                        self.assertEqual(self.read_report_body()[field], value.strip())
                    else:
                        self.run_cli(argv, expected_exit_code=1)
                        self.assertEqual(self.sent, [])

    def test_the_output_excerpt_comes_from_a_file_with_the_bounds_of_the_server_schema(self) -> None:
        excerpt_path = self.scratch_dir / "excerpt.txt"
        for text, is_accepted in (("  \n", True), ("o\n", True), ("o" * 20000, True), ("o" * 20001, False)):
            with self.subTest(text_length=len(text)):
                self.sent.clear()
                excerpt_path.write_text(text, encoding="utf-8")
                argv = self.build_post_lean_check_argv("--output-excerpt-file", str(excerpt_path))
                if is_accepted:
                    self.run_cli(argv)
                    self.assertEqual(self.read_report_body()["outputExcerpt"], text)
                else:
                    self.run_cli(argv, expected_exit_code=1)
                    self.assertEqual(self.sent, [])

    def test_an_empty_output_excerpt_file_sends_no_excerpt(self) -> None:
        path = self.scratch_dir / "empty-excerpt.txt"
        path.write_text("", encoding="utf-8")
        self.run_cli(self.build_post_lean_check_argv("--output-excerpt-file", str(path)))
        self.assertNotIn("outputExcerpt", self.read_report_body())

    def test_an_excerpt_normalizes_line_breaks_and_keeps_its_leading_spaces(self) -> None:
        path = self.scratch_dir / "excerpt.txt"
        path.write_bytes(b"  first\r\n  second\r")
        self.run_cli(self.build_post_lean_check_argv("--output-excerpt-file", str(path)))
        self.assertEqual(self.read_report_body()["outputExcerpt"], "  first\n  second\n")

    def test_a_missing_excerpt_file_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(
            self.build_post_lean_check_argv("--output-excerpt-file", str(self.scratch_dir / "absent.txt")),
            expected_exit_code=1)
        self.assertIn("does not exist", stderr_text)
        self.assertEqual(self.sent, [])

    def test_an_empty_excerpt_file_name_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(self.build_post_lean_check_argv("--output-excerpt-file", ""), expected_exit_code=1)
        self.assertIn("does not exist", stderr_text)
        self.assertEqual(self.sent, [])

    def test_the_refusal_of_a_link_gives_an_example_of_each_form_and_names_the_links_it_refuses(self) -> None:
        for url in ("https://example.org/log.txt", "https://github.com/o/r/blob/main/check.txt",
                    f"https://github.com/o/r/tree/{_SHA}/logs"):
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(self.build_post_lean_check_argv(evidence_url=url), expected_exit_code=1)
                self.assertIn("Give a GitHub Actions run, for example https://github.com/<owner>/<repo>/actions/runs/<id>."
                              " Or give a file at a full 40-character commit SHA, for example"
                              " https://github.com/<owner>/<repo>/blob/<sha>/<path>.", stderr_text)
                self.assertIn("A branch link or a tree link is refused.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_links_in_every_shape_the_server_accepts_are_sent_as_given(self) -> None:
        pinned = f"https://github.com/o/r/blob/{_SHA}"
        accepted = [
            _CHECK_RUN_URL,
            f"{_CHECK_RUN_URL}/job/987654321",
            "https://github.com/o/r/actions/runs/0",
            _CHECK_FILE_URL,
            f"{pinned}/check.txt",
            f"{pinned}/check.txt#L3",
            f"{pinned}/check.txt#L3-L9",
            f"{pinned}/logs/2026/10/check.txt",
            f"https://github.com/my-org/r.github.io/blob/{_SHA}/check.txt",
            f"https://github.com/{'o' * 39}/{'r' * 100}/blob/{_SHA}/check.txt",
            f"{pinned}/" + "a" * (499 - len(pinned)),
            f"{pinned}/%E2%88%80.txt",
            f"{pinned}/" + chr(0xDC) + "ber.txt",
            f"{pinned}/" + chr(0x1F600) + ".txt",
        ]
        for url in accepted:
            with self.subTest(url=url):
                self.sent.clear()
                self.run_cli(self.build_post_lean_check_argv(evidence_url=url))
                self.assertEqual(self.read_report_body()["evidenceUrl"], url)

    def test_a_link_that_is_not_a_run_or_a_file_at_a_full_commit_is_refused_before_any_request(self) -> None:
        pinned = f"https://github.com/o/r/blob/{_SHA}"
        run = "https://github.com/o/r/actions/runs/123"
        refused = [
            "",
            "https://example.org/log.txt",
            "https://github.com/o/r",
            "https://github.com/o/r/actions",
            "https://github.com/o/r/actions/runs",
            "https://github.com/o/r/actions/runs/",
            "https://github.com/o/r/actions/runs/latest",
            f"{run}x",
            f"x{run}",
            f"{run}/",
            f"{run}/job",
            f"{run}/job/",
            f"{run}/job/abc",
            f"{run}/job/1/job/2",
            f"{run}/attempts/1",
            f"{run}?check_suite_focus=true",
            f"{run}#step:1:1",
            f"{run}#L3",
            "https://github.com/o/r/actions/workflows/ci.yml",
            "https://github.com/o/r/runs/123",
            "https://github.com/o/r/actions/runs/" + chr(0x663),
            f"{run}/job/" + chr(0xFF13),
            f"https://github.com/o/r/tree/{_SHA}/check.txt",
            f"https://github.com/o/r/tree/{_SHA}",
            pinned,
            f"{pinned}/",
            f"{pinned}//check.txt",
            f"{pinned}/check.txt/",
            f"https://github.com/o/r/blob/{_SHA[:7]}/check.txt",
            f"https://github.com/o/r/blob/{_SHA[:39]}/check.txt",
            f"https://github.com/o/r/blob/{_SHA}0/check.txt",
            f"https://github.com/o/r/blob/{_SHA.upper()}/check.txt",
            f"https://github.com/o/r/blob/{_SHA[:39]}g/check.txt",
            "https://github.com/o/r/blob/main/check.txt",
            f"{pinned}/check.txt?plain=1",
            f"{pinned}/check.txt#L3-9",
            f"{pinned}/check.txt#L",
            f"{pinned}/check.txt#L3-L9-L12",
            f"{pinned}/check.txt#section",
            f"{pinned}/check.txt#L" + chr(0x663),
            f"{pinned}/check.txt#L3-L" + chr(0x663),
            f"https://github.com/o/r/commit/{_SHA}",
            f"https://github.com/o/r/raw/{_SHA}/check.txt",
            f"https://github.com/o/r/blame/{_SHA}/check.txt",
            "http://github.com/o/r/actions/runs/123",
            "HTTPS://github.com/o/r/actions/runs/123",
            "https://GitHub.com/o/r/actions/runs/123",
            "https://www.github.com/o/r/actions/runs/123",
            "https://github.com.evil.test/o/r/actions/runs/123",
            "https://githubxcom/o/r/actions/runs/123",
            "https://github.com/actions/runs/123",
            "https://github.com/extra/o/r/actions/runs/123",
            f"https://github.com/{'o' * 40}/r/actions/runs/123",
            "https://github.com/-o/r/actions/runs/123",
            "https://github.com/o-/r/actions/runs/123",
            "https://github.com/o.o/r/actions/runs/123",
            "https://github.com/_o/r/actions/runs/123",
            "https://github.com/o_o/r/actions/runs/123",
            "https://github.com/o_/r/actions/runs/123",
            f"https://github.com/o{chr(0xE9)}o/r/actions/runs/123",
            "https://github.com/o/r~/actions/runs/123",
            f"https://github.com/o/r{chr(0xE9)}/actions/runs/123",
            f"https://github.com/o/{'r' * 101}/actions/runs/123",
        ]
        for url in refused:
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(self.build_post_lean_check_argv(evidence_url=url), expected_exit_code=1)
                self.assertIn(f"{url} is not a check evidence link", stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_path_step_that_moves_the_link_is_refused_in_a_file_and_in_the_repository(self) -> None:
        pinned = f"https://github.com/o/r/blob/{_SHA}"
        refused = [
            f"{pinned}/./check.txt",
            f"{pinned}/%2E%2e/check.txt",
            f"{pinned}/lib/.%2E#L3",
            f"{pinned}/lib/..",
            f"{pinned}/lib%2fcheck.txt",
            f"{pinned}/lib\\check.txt",
            "https://github.com/o/../actions/runs/123",
            f"https://github.com/o/../blob/{_SHA}/check.txt",
        ]
        for url in refused:
            with self.subTest(url=url):
                _, stderr_text = self.run_cli(self.build_post_lean_check_argv(evidence_url=url), expected_exit_code=1)
                self.assertIn(_MOVED_PATH_REASON, stderr_text)
        self.assertEqual(self.sent, [])

    def test_names_made_of_dots_stay_valid_in_a_check_evidence_link(self) -> None:
        pinned = f"https://github.com/o/r/blob/{_SHA}"
        for url in (f"{pinned}/.github/check.txt", f"{pinned}/a..b/...txt", f"{pinned}/%2e%2e%2e.txt"):
            with self.subTest(url=url):
                self.run_cli(self.build_post_lean_check_argv(evidence_url=url))
                self.assertEqual(json.loads(self.sent[-1].data.decode())["evidenceUrl"], url)

    def test_the_shape_pattern_refuses_whitespace_by_itself_like_the_server_pattern(self) -> None:
        # The commands refuse whitespace before the pattern runs, so the pattern is checked on its own.
        for link in (_CHECK_RUN_URL, _CHECK_FILE_URL):
            self.assertIsNotNone(_cli._CHECK_EVIDENCE_URL_RE.fullmatch(link))
            for url in (*_build_links_with_a_space(link), link[:-3] + chr(0xA0) + link[-3:], link + "\n"):
                with self.subTest(url=url):
                    self.assertIsNone(_cli._CHECK_EVIDENCE_URL_RE.fullmatch(url))


class TestPostDiscussionInputs(_MathCliTestCase):
    def read_post_body(self) -> dict:
        return self.read_sent_body("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/posts")

    def test_a_post_that_replies_to_nothing_sends_only_its_body(self) -> None:
        self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "Is $n = 7$ within reach?"])
        self.assertEqual(self.read_post_body(), {"body": "Is $n = 7$ within reach?"})

    def test_the_post_has_the_bounds_of_the_server_schema(self) -> None:
        for body, is_accepted in (("", False), ("   ", False), ("n", True), ("n" * 10000, True), (" n ", True),
                                  ("n" * 10001, False)):
            with self.subTest(body_length=len(body)):
                self.sent.clear()
                argv = ["post-discussion", _PROBLEM_ID, "--body", body]
                if is_accepted:
                    self.run_cli(argv)
                    self.assertEqual(self.read_post_body()["body"], body.strip())
                else:
                    self.run_cli(argv, expected_exit_code=1)
                    self.assertEqual(self.sent, [])

    def test_the_post_can_come_from_a_file(self) -> None:
        body_path = self.scratch_dir / "post.txt"
        body_path.write_text("Is $n = 7$ within reach?\n", encoding="utf-8")
        self.run_cli(["post-discussion", _PROBLEM_ID, "--body-file", str(body_path), "--reply-to", _PROOF_ID])
        self.assertEqual(self.read_post_body(), {"body": "Is $n = 7$ within reach?", "replyToPostId": _PROOF_ID})

    def test_a_missing_post_file_is_refused_before_any_request(self) -> None:
        _, stderr_text = self.run_cli(["post-discussion", _PROBLEM_ID, "--body-file", str(self.scratch_dir / "absent.txt")],
                                      expected_exit_code=1)
        self.assertIn("does not exist", stderr_text)
        self.assertEqual(self.sent, [])

    def test_an_empty_reply_target_is_sent_for_the_server_to_judge(self) -> None:
        self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "Is $n = 7$ within reach?", "--reply-to", ""])
        self.assertEqual(self.read_post_body()["replyToPostId"], "")


# The lists that page: the command, the id its route holds, and the route.
_PAGED_READ_COMMANDS = (
    ("proofs", _PROBLEM_ID, f"/api/v1/math-problems/{_PROBLEM_ID}/proofs"),
    ("lean-checks", _PROOF_ID, f"/api/v1/math-proofs/{_PROOF_ID}/lean-checks"),
    ("discussion", _PROBLEM_ID, f"/api/v1/math-problems/{_PROBLEM_ID}/posts"),
)


class TestPagedReads(_MathCliTestCase):
    def read_query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.sent[-1].full_url).query)

    def test_without_options_each_list_asks_for_the_first_page_of_the_default_size(self) -> None:
        for command, path_id, route in _PAGED_READ_COMMANDS:
            with self.subTest(command=command):
                self.run_cli([command, path_id])
                self.assertEqual(self.sent[-1].full_url, "https://api.test" + route)

    def test_the_limit_and_the_cursor_join_the_query(self) -> None:
        for command, path_id, route in _PAGED_READ_COMMANDS:
            for options, query in ((["--limit", "5"], {"limit": ["5"]}),
                                   (["--cursor", "c1"], {"cursor": ["c1"]}),
                                   (["--limit", "5", "--cursor", "c1"], {"limit": ["5"], "cursor": ["c1"]})):
                with self.subTest(command=command, options=options):
                    self.run_cli([command, path_id, *options])
                    self.assertEqual(urllib.parse.urlparse(self.sent[-1].full_url).path, route)
                    self.assertEqual(self.read_query(), query)

    def test_a_limit_of_zero_is_sent_for_the_server_to_judge(self) -> None:
        for command, path_id, _ in _PAGED_READ_COMMANDS:
            with self.subTest(command=command):
                self.run_cli([command, path_id, "--limit", "0"])
                self.assertEqual(self.read_query(), {"limit": ["0"]})

    def test_an_empty_cursor_is_left_out(self) -> None:
        for command, path_id, route in _PAGED_READ_COMMANDS:
            with self.subTest(command=command):
                self.run_cli([command, path_id, "--cursor", ""])
                self.assertEqual(self.sent[-1].full_url, "https://api.test" + route)

    def test_a_cursor_with_reserved_characters_is_encoded(self) -> None:
        cursor = "a+b/c=d&e f?g#h"
        for command, path_id, _ in _PAGED_READ_COMMANDS:
            with self.subTest(command=command):
                self.run_cli([command, path_id, "--cursor", cursor])
                self.assertEqual(self.read_query(), {"cursor": [cursor]})


# One valid command line for each new command that takes one id: the command, the HTTP method of its request, the
# route with the id in {id}, and its other options. The id is only a path segment here, so one UUID serves every command.
_ID_COMMANDS = (
    ("post-proof", "POST", "/api/v1/math-problems/{id}/proofs",
     ("--claim", "proves", "--summary", "p" * 120, "--writeup", "2601.12345v2")),
    ("post-lean-check", "POST", "/api/v1/math-proofs/{id}/lean-checks",
     ("--outcome", "pass", "--tool", "t", "--lean-toolchain", "v", "--evidence-url", _CHECK_RUN_URL)),
    ("post-discussion", "POST", "/api/v1/math-problems/{id}/posts", ("--body", "Is $n = 7$ within reach?")),
    ("proofs", "GET", "/api/v1/math-problems/{id}/proofs", ()),
    ("proof", "GET", "/api/v1/math-proofs/{id}", ()),
    ("lean-checks", "GET", "/api/v1/math-proofs/{id}/lean-checks", ()),
    ("lean-check", "GET", "/api/v1/math-lean-checks/{id}", ()),
    ("discussion", "GET", "/api/v1/math-problems/{id}/posts", ()),
    ("discussion-post", "GET", "/api/v1/math-posts/{id}", ()),
)


class TestCommandsOnAnId(_MathCliTestCase):
    def list_commands(self, method: str) -> list[tuple]:
        return [row for row in _ID_COMMANDS if row[1] == method]

    def test_each_write_reads_the_caller_state_once_and_then_posts(self) -> None:
        for command, method, route, options in self.list_commands("POST"):
            with self.subTest(command=command):
                self.sent.clear()
                self.run_cli([command, _PROBLEM_ID, *options])
                self.assertEqual(self.list_sent_requests(),
                                 [("GET", "/api/v1/me"), (method, route.format(id=_PROBLEM_ID))])

    def test_without_an_orcid_each_write_stops_before_the_request_and_names_the_account_page(self) -> None:
        self.me = {"displayName": "Ada", "orcidId": None}
        for command, _, _, options in self.list_commands("POST"):
            with self.subTest(command=command):
                self.sent.clear()
                _, stderr_text = self.run_cli([command, _PROBLEM_ID, *options], expected_exit_code=1)
                self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me")])
                self.assertIn("https://api.test/account", stderr_text)

    def test_each_read_sends_one_request_and_needs_no_linked_id(self) -> None:
        self.me = {"displayName": "Ada", "orcidId": None}
        for command, method, route, options in self.list_commands("GET"):
            with self.subTest(command=command):
                self.sent.clear()
                self.run_cli([command, _PROBLEM_ID, *options])
                self.assertEqual(self.list_sent_requests(), [(method, route.format(id=_PROBLEM_ID))])

    def test_each_command_prints_the_answer_of_the_server(self) -> None:
        for command, method, route, options in _ID_COMMANDS:
            with self.subTest(command=command):
                answer = {"id": f"answer of {command}"}
                self.answers[(method, route.format(id=_PROBLEM_ID))] = (201 if method == "POST" else 200, answer)
                stdout_text, _ = self.run_cli([command, _PROBLEM_ID, *options])
                self.assertEqual(json.loads(stdout_text), answer)

    def test_an_id_with_path_characters_stays_one_encoded_segment(self) -> None:
        for command, method, route, options in _ID_COMMANDS:
            with self.subTest(command=command):
                self.sent.clear()
                self.run_cli([command, "../x?y", *options])
                self.assertEqual(self.list_sent_requests()[-1], (method, route.format(id="..%2Fx%3Fy")))

    def test_an_empty_or_dot_id_is_refused_before_any_request(self) -> None:
        for unroutable_id in ("", ".", ".."):
            for command, _, _, options in _ID_COMMANDS:
                with self.subTest(command=command, unroutable_id=unroutable_id):
                    _, stderr_text = self.run_cli([command, unroutable_id, *options], expected_exit_code=1)
                    self.assertIn("is not an identifier exactory can read", stderr_text)
        self.assertEqual(self.sent, [])


class TestSubmitMathProblemInputs(_MathCliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.answers[("POST", "/api/v1/verifications")] = (201, {"id": "v1"})

    def test_three_problems_are_sent_in_the_order_given(self) -> None:
        problem_ids = ["p-3", "p-1", "p-2"]
        argv = ["submit", "--arxiv-id", "2601.12345v2"]
        for problem_id in problem_ids:
            argv += ["--math-problem", problem_id]
        self.run_cli(argv)
        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me"), ("POST", "/api/v1/verifications")])
        self.assertEqual(self.read_sent_body("POST", "/api/v1/verifications")["mathProblemIds"], problem_ids)

    def test_four_problems_are_refused_with_the_count_and_one_remedy_per_sentence(self) -> None:
        argv = ["submit", "--doi", "10.5281/zenodo.1"]
        for problem_id in ("p-1", "p-2", "p-3", "p-4"):
            argv += ["--math-problem", problem_id]
        _, stderr_text = self.run_cli(argv, expected_exit_code=1)
        self.assertIn("The command names 4 math problems. The limit is 3. Remove some.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_without_an_orcid_the_submission_stops_before_the_request_and_names_the_account_page(self) -> None:
        self.me = {"displayName": "Ada", "orcidId": None}
        _, stderr_text = self.run_cli(["submit", "--arxiv-id", "2601.12345v2", "--math-problem", _PROBLEM_ID],
                                      expected_exit_code=1)
        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me")])
        self.assertIn("https://api.test/account", stderr_text)

    def test_problems_and_challenges_join_the_same_body(self) -> None:
        self.run_cli(["submit", "--doi", "10.5281/zenodo.1", "--challenge", "c-1", "--math-problem", "p-1"])
        self.assertEqual(self.read_sent_body("POST", "/api/v1/verifications"),
                         {"doi": "10.5281/zenodo.1", "grandChallengeIds": ["c-1"], "mathProblemIds": ["p-1"]})


class TestNewCommandParsers(unittest.TestCase):
    def test_the_parser_refuses_a_command_line_that_misses_a_required_option(self) -> None:
        valid = [
            ["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "s"],
            ["post-proof", _PROBLEM_ID, "--claim", "disproves", "--summary-file", "summary.tex"],
            ["post-lean-check", _PROOF_ID, "--outcome", "fail", "--tool", "t", "--lean-toolchain", "v",
             "--evidence-url", _CHECK_RUN_URL],
            ["post-discussion", _PROBLEM_ID, "--body", "b"],
            ["post-discussion", _PROBLEM_ID, "--body-file", "post.txt"],
            ["proofs", _PROBLEM_ID, "--limit", "5", "--cursor", "c"],
            ["proof", _PROOF_ID],
            ["lean-checks", _PROOF_ID],
            ["lean-check", _PROOF_ID],
            ["discussion", _PROBLEM_ID],
            ["discussion-post", _PROOF_ID],
            ["submit", "--arxiv-id", "2601.12345v2", "--math-problem", _PROBLEM_ID],
        ]
        for argv in valid:
            _cli._build_parser().parse_args(argv)
        check_options = ["--outcome", "pass", "--tool", "t", "--lean-toolchain", "v", "--evidence-url", _CHECK_RUN_URL]
        refused = [
            ["post-proof", _PROBLEM_ID, "--summary", "s"],
            ["post-proof", _PROBLEM_ID, "--claim", "proves"],
            ["post-proof", _PROBLEM_ID, "--claim", "shows", "--summary", "s"],
            ["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "s", "--summary-file", "summary.tex"],
            ["post-proof", _PROBLEM_ID, "--claim", "proves", "--summary", "s", "--formal-statement-version", "one"],
            ["post-proof", "--claim", "proves", "--summary", "s"],
            ["post-lean-check", _PROOF_ID, *check_options[2:]],
            ["post-lean-check", _PROOF_ID, "--outcome", "maybe", *check_options[2:]],
            ["post-lean-check", _PROOF_ID, *check_options[:2], *check_options[4:]],
            ["post-lean-check", _PROOF_ID, *check_options[:4], *check_options[6:]],
            ["post-lean-check", _PROOF_ID, *check_options[:6]],
            ["post-lean-check", *check_options],
            ["post-discussion", _PROBLEM_ID],
            ["post-discussion", _PROBLEM_ID, "--body", "b", "--body-file", "post.txt"],
            ["post-discussion", "--body", "b"],
            ["proofs", _PROBLEM_ID, "--limit", "many"],
            ["lean-checks", _PROOF_ID, "--limit", "many"],
            ["discussion", _PROBLEM_ID, "--limit", "many"],
            ["proofs"],
            ["proof"],
            ["lean-checks"],
            ["lean-check"],
            ["discussion"],
            ["discussion-post"],
        ]
        for argv in refused:
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        _cli._build_parser().parse_args(argv)
                self.assertEqual(caught.exception.code, 2)

    def test_the_help_of_every_new_command_and_of_submit_renders(self) -> None:
        for command in ("post-proof", "proofs", "proof", "post-lean-check", "lean-checks", "lean-check",
                        "post-discussion", "discussion", "discussion-post", "submit"):
            with self.subTest(command=command):
                help_text = io.StringIO()
                with contextlib.redirect_stdout(help_text):
                    with self.assertRaises(SystemExit) as caught:
                        _cli._build_parser().parse_args([command, "--help"])
                self.assertEqual(caught.exception.code, 0)
                self.assertIn(f"exactory {command}", help_text.getvalue())

    def test_the_help_description_says_that_the_client_also_serves_the_math_section(self) -> None:
        self.assertIn("math section", _cli._build_parser().description.splitlines()[0])

    def test_the_limit_of_every_math_list_command_has_a_help_text(self) -> None:
        server_default = "How many entries one page holds. The server sets the default."
        for command, expected in (("proofs", server_default), ("lean-checks", server_default),
                                  ("discussion", server_default),
                                  ("problems", "How many entries one page holds. Default: 25."),
                                  ("problem-history", "How many entries one page holds. Default: 50.")):
            with self.subTest(command=command):
                help_text = io.StringIO()
                with contextlib.redirect_stdout(help_text):
                    with self.assertRaises(SystemExit):
                        _cli._build_parser().parse_args([command, "--help"])
                self.assertIn(expected, " ".join(help_text.getvalue().split()))


# The classes below cover the fixes of the first review round: a file or an argument that cannot be read ends the
# command with one message and never with a traceback, every over-limit message has one shape, and the patterns
# that mirror the server read ASCII digits only.

# One valid command line for each option that reads a text file: the option, and the command line before it. The
# first three are the file options of the proof, check, and discussion commands, which share one reader. The
# citations file and the verdict file of verify have a reader each.
_TEXT_FILE_OPTION_COMMANDS = (
    ("--summary-file", ["post-proof", _PROBLEM_ID, "--claim", "proves", "--writeup", "2601.12345v2"]),
    ("--output-excerpt-file", ["post-lean-check", _PROOF_ID, "--outcome", "pass", "--tool", "t",
                               "--lean-toolchain", "v", "--evidence-url", _CHECK_RUN_URL]),
    ("--body-file", ["post-discussion", _PROBLEM_ID]),
    ("--citations-file", ["post-problem", "--title", "Dittert conjecture for n = 7", "--area", "math.CO",
                          "--statement", "s" * 60, "--motivation", "m" * 120, "--current-state", "c" * 120,
                          "--resolution-criteria", "r" * 60]),
    ("--file", ["verify", _PROBLEM_ID]),
)


class TestTextFilesThatCannotBeRead(_MathCliTestCase):
    def check_each_option_refuses(self, path: str, expected_message: str) -> None:
        for option, command_line in _TEXT_FILE_OPTION_COMMANDS:
            with self.subTest(option=option):
                _, stderr_text = self.run_cli([*command_line, option, path], expected_exit_code=1)
                self.assertEqual(stderr_text, expected_message + "\n")
        self.assertEqual(self.sent, [])

    def test_a_missing_file_is_refused_with_its_remedy(self) -> None:
        path = str(self.scratch_dir / "absent.txt")
        self.check_each_option_refuses(path, f"The file {path} does not exist. Give the path of an existing file.")

    def test_a_directory_is_refused_with_its_remedy(self) -> None:
        directory = self.scratch_dir / "a-directory"
        directory.mkdir()
        self.check_each_option_refuses(str(directory), f"The file {directory} is a directory. Give the path of a file.")

    def test_a_file_that_is_not_utf8_text_is_refused_with_its_remedy(self) -> None:
        binary_file = self.scratch_dir / "binary.dat"
        binary_file.write_bytes(b"\xff\xfe\x80 not text")
        self.check_each_option_refuses(str(binary_file), f"The file {binary_file} is not UTF-8 text. Save it as UTF-8.")

    def test_a_path_through_a_file_is_refused_with_the_cause_from_the_system(self) -> None:
        plain_file = self.scratch_dir / "plain.txt"
        plain_file.write_text("plain", encoding="utf-8")
        path = str(plain_file / "child")
        self.check_each_option_refuses(path, f"The file {path} cannot be read: {os.strerror(errno.ENOTDIR)}."
                                             " Check the path and the permissions of the file.")


# The commands that put an id in a path, with the options that follow the id: the commands of the math section,
# and the older commands whose paths come from the same two builders.
_ID_IN_PATH_COMMAND_LINES = (
    ("problem",), ("problem-history",), ("vote-problem", "--value", "1"),
    *((command, *options) for command, _, _, options in _PROBLEM_WRITE_COMMANDS),
    *((command, *options) for command, _, _, options in _ID_COMMANDS),
    ("status",), ("challenge",), ("task",), ("paper",),
)

# The commands that put a value in a query, with the option that carries it. The search text of problems is free text,
# so the plain text check refuses it first, with a message that names it (TestPlainTextOfFreeTextFields).
_VALUE_IN_QUERY_COMMAND_LINES = (
    ("proofs", _PROBLEM_ID, "--cursor"), ("lean-checks", _PROOF_ID, "--cursor"),
    ("discussion", _PROBLEM_ID, "--cursor"), ("problem-history", _PROBLEM_ID, "--cursor"),
    ("problems", "--cursor"), ("problems", "--area"),
    ("tasks", "--cursor"), ("tasks", "--query"), ("challenges", "--field"), ("challenges", "--cursor"),
)


class TestArgumentsThatAreNotUtf8Text(_MathCliTestCase):
    """argv decodes a byte that is not UTF-8 text to a lone surrogate, which has no percent-encoding. Such an
    argument in a path or in a query ends the command with one message and no request."""

    def test_an_id_in_a_path_is_refused_before_any_request(self) -> None:
        for command, *options in _ID_IN_PATH_COMMAND_LINES:
            with self.subTest(command=command):
                _, stderr_text = self.run_cli([command, _ARGUMENT_FROM_BYTE_FF, *options], expected_exit_code=1)
                self.assertEqual(stderr_text, _NOT_UTF8_ERR_MSG + "\n")
        self.assertEqual(self.sent, [])

    def test_a_value_in_a_query_is_refused_before_any_request(self) -> None:
        for command_line in _VALUE_IN_QUERY_COMMAND_LINES:
            with self.subTest(command_line=command_line):
                _, stderr_text = self.run_cli([*command_line, _ARGUMENT_FROM_BYTE_FF], expected_exit_code=1)
                self.assertEqual(stderr_text, _NOT_UTF8_ERR_MSG + "\n")
        self.assertEqual(self.sent, [])

    def test_valid_text_outside_ascii_is_sent_encoded(self) -> None:
        text = chr(0xE9) + chr(0x1F600)
        self.run_cli(["proof", text])
        self.run_cli(["proofs", _PROBLEM_ID, "--cursor", text])
        self.assertEqual(self.list_sent_requests()[0], ("GET", "/api/v1/math-proofs/%C3%A9%F0%9F%98%80"))
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(self.sent[1].full_url).query), {"cursor": [text]})


class TestOverLimitMessages(_MathCliTestCase):
    """Every command that names more items than its limit allows ends with one message of one shape. The cases pin
    the whole text of each command, so the code that builds the message cannot drift from them."""

    def build_post_challenge_argv(self, *extra: str) -> list[str]:
        return ["post-challenge", "--title", "An open question", "--field", "cs.LG", "--problem-statement", "p" * 200,
                "--current-state", "c" * 100, "--resolution-criteria", "r" * 50,
                "--citations-file", self.write_citations(), *extra]

    def test_each_command_names_the_count_the_limit_and_the_remedy(self) -> None:
        add_formal_statement = ["add-formal-statement", _PROBLEM_ID, "--lean-url", _LEAN_SOURCE_URL]
        cases = (
            (["submit", "--doi", "10.5281/zenodo.1", *_build_repeated_options("--challenge", 4)],
             "The command names 4 challenges. The limit is 3. Remove some."),
            (["submit", "--doi", "10.5281/zenodo.1", *_build_repeated_options("--math-problem", 4)],
             "The command names 4 math problems. The limit is 3. Remove some."),
            (self.build_post_challenge_argv(*_build_repeated_options("--paper-doi", 21)),
             "The command names 21 papers. The limit is 20. Remove some."),
            (self.build_post_problem_argv(*_build_paper_options(11)),
             "The command names 11 papers. The limit is 10. Remove some. Attach the rest with link-problem-paper."),
            (self.build_post_problem_argv(*_build_github_link_options(11)),
             "The command names 11 context links. The limit is 10. Remove some."
             " Attach the rest with link-problem-github."),
            ([*add_formal_statement, *_build_repeated_options("--proof-target", 21)],
             "The command names 21 proof targets. The limit is 20. Remove some."),
            ([*add_formal_statement, "--proof-target", "t", *_build_repeated_options("--disproof-target", 21)],
             "The command names 21 disproof targets. The limit is 20. Remove some."),
            (["post-lean-check", _PROOF_ID, "--outcome", "pass", "--tool", "t", "--lean-toolchain", "v",
              "--evidence-url", _CHECK_RUN_URL, *_build_repeated_options("--axiom", 21)],
             "The command names 21 axioms. The limit is 20. Remove some."),
        )
        for argv, message in cases:
            with self.subTest(command=argv[0], message=message):
                _, stderr_text = self.run_cli(argv, expected_exit_code=1)
                self.assertEqual(stderr_text, message + "\n")
        self.assertEqual(self.sent, [])

    def read_option_help(self, command: str, option: str) -> str:
        """The help of one option of a command: its metavar and its text, up to the next option."""
        help_text = io.StringIO()
        with contextlib.redirect_stdout(help_text):
            with self.assertRaises(SystemExit):
                _cli._build_parser().parse_args([command, "--help"])
        return " ".join(help_text.getvalue().split()).rsplit(f" {option} ", 1)[1].split(" --", 1)[0]

    def test_the_help_of_each_option_gives_the_limit_that_the_command_enforces(self) -> None:
        for command, option, limit_text in (
                ("submit", "--challenge", "at most 3."), ("submit", "--math-problem", "at most 3."),
                ("post-challenge", "--paper-doi", "at most 20."), ("post-problem", "--paper", "at most 10."),
                ("post-problem", "--github-link", "at most 10."), ("post-lean-check", "--axiom", "at most 20."),
                ("add-formal-statement", "--proof-target", "1-20."),
                ("add-formal-statement", "--disproof-target", "0-20.")):
            with self.subTest(command=command, option=option):
                self.assertIn(limit_text, self.read_option_help(command, option))

    def test_a_command_that_names_exactly_the_limit_is_accepted(self) -> None:
        self.answers[("POST", "/api/v1/verifications")] = (201, {"id": "v1"})
        self.run_cli(["submit", "--doi", "10.5281/zenodo.1", *_build_repeated_options("--challenge", 3)])
        self.assertEqual(len(self.read_sent_body("POST", "/api/v1/verifications")["grandChallengeIds"]), 3)
        self.run_cli(self.build_post_challenge_argv(*_build_repeated_options("--paper-doi", 20)))
        self.assertEqual(len(self.read_sent_body("POST", "/api/v1/grand-challenges")["paperDois"]), 20)


class TestAsciiDigitsInIdentifierPatterns(_MathCliTestCase):
    """The server's patterns read ASCII digits only, and Python's \\d also reads the digits of other scripts."""

    def build_post_problem_argv_with_locator(self, locator: str) -> list[str]:
        citations_path = self.scratch_dir / "locator-citations.json"
        citations_path.write_text(json.dumps([{"citation": "H. Minc, Permanents, 1978, chapter 2.", "locator": locator}]),
                                  encoding="utf-8")
        argv = self.build_post_problem_argv()
        argv[argv.index("--citations-file") + 1] = str(citations_path)
        return argv

    def assert_url_locator_refused(self, locator: str) -> None:
        argv = self.build_post_problem_argv_with_locator(locator)
        citations_path = argv[argv.index("--citations-file") + 1]
        commands = [argv, ["post-challenge", "--title", "A test challenge", "--field", "math.CO",
                           "--problem-statement", "s" * 200, "--current-state", "c" * 100,
                           "--resolution-criteria", "r" * 50, "--citations-file", citations_path]]
        for command in commands:
            with self.subTest(command=command[0], locator=locator):
                self.sent.clear()
                _, message = self.run_cli(command, expected_exit_code=1)
                self.assertIn("not a DOI, an arXiv id, or an https URL", message)
                self.assertEqual(self.sent, [])

    def test_a_url_locator_with_an_empty_host_is_refused(self) -> None:
        for locator in ("https://", "https://?x", "https://#x", "https://:443/x", "https://user@/x"):
            self.assert_url_locator_refused(locator)

    def test_a_url_locator_with_an_unclosed_or_invalid_ipv6_bracket_is_refused(self) -> None:
        for locator in ("https://[", "https://[::1", "https://[invalid]/x", "https://[127.0.0.1]/x",
                        "https://[::1]extra/x", "https://[fe80::1%25eth0]/x"):
            self.assert_url_locator_refused(locator)

    def test_a_url_locator_with_forbidden_host_code_points_is_refused(self) -> None:
        for host in ("exa mple.org", "exa<mple.org", "exa>mple.org", "exa^mple.org", "exa|mple.org",
                     "example%00.org", "example%2f.org", "example%23.org", "example%25.org", "example%ZZ.org"):
            self.assert_url_locator_refused("https://" + host + "/x")

    def test_a_url_locator_with_an_invalid_port_is_refused(self) -> None:
        for port in ("bad", "-1", "65536", "12.3", "443x", "１２"):
            self.assert_url_locator_refused("https://example.org:" + port + "/x")

    def test_accepted_url_locators_are_sent_exactly_as_given(self) -> None:
        for locator in ("https://[::1]:443/x", "https://[::ffff:192.0.2.1]/x", "https://EXAMPLE.org:00443/a/../b?x=%2e#y",
                        "https://example.org:/x", "https:///example.org/x", "https://user:pass@example.org/x",
                        "https://127.1/x", "https://0x7f.1/x", "https://example.org/é", "https://例え.テスト/x",
                        "https://xn--bcher-kva.example/x", "https://256.1.1.1../x", "https://4294967296../x",
                        # International hosts that the server accepts (URL.canParse in Node 24, 2026-10-16).
                        # The server decides international and numeric hosts, so the local check passes them.
                        "https://\U0001fae8.com/x", "https://\u0915\u094d\u200d.in/x", "https://\u0bb8\u0bcd\u200c.in/x",
                        "https://a\u05d0.com/x"):
            with self.subTest(locator=locator):
                self.sent.clear()
                self.run_cli(self.build_post_problem_argv_with_locator(locator))
                body = self.read_sent_body("POST", "/api/v1/math-problems")
                self.assertEqual(body["citations"][0]["locator"], locator)

    def test_a_locator_is_a_doi_or_an_arxiv_id_in_ascii_digits_or_an_https_url(self) -> None:
        for locator in ("10.1017/cbo9781107340688", "arXiv:2301.00001", "arxiv:2301.00001v12", "hep-th/9901001v3",
                        "math.GT/0309136", "https://example.org/x"):
            with self.subTest(locator=locator):
                self.sent.clear()
                self.run_cli(self.build_post_problem_argv_with_locator(locator))
                self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me"), ("POST", "/api/v1/math-problems")])

    def test_a_locator_with_digits_of_another_script_is_refused_before_any_request(self) -> None:
        arabic_three, fullwidth_two = chr(0x663), chr(0xFF12)
        for locator in (f"10.{arabic_three * 4}/x", f"2301.0000{arabic_three}", f"arXiv:{fullwidth_two}301.00001",
                        f"hep-th/990100{arabic_three}", f"2301.00001v{arabic_three}"):
            with self.subTest(locator=locator):
                _, stderr_text = self.run_cli(self.build_post_problem_argv_with_locator(locator), expected_exit_code=1)
                self.assertIn("The locator of entry 1 is not a DOI, an arXiv id, or an https URL. Correct it.", stderr_text)
        self.assertEqual(self.sent, [])

    def test_a_bare_arxiv_id_in_digits_of_another_script_is_not_mapped_to_a_doi(self) -> None:
        arabic_three = chr(0x663)
        self.run_cli(["paper", "2301.00001v2"])
        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/papers/10.48550/arxiv.2301.00001")])
        for identifier in (arabic_three * 4 + ".00001", "2301." + arabic_three * 5, "2301.00001v" + arabic_three):
            with self.subTest(identifier=identifier):
                self.sent.clear()
                self.run_cli(["paper", identifier])
                self.assertEqual(self.list_sent_requests(),
                                 [("GET", "/api/v1/papers/" + urllib.parse.quote(identifier, safe=""))])


# The classes below cover the character rule that the server applies to every free-text field (plainTextSchema, design
# 2026-10-12 §26.24), the message for a refused list query, and the advice after an answer of 500 to 599 to a write.

_BELL_CHARACTER = chr(0x07)
_ESCAPE_CHARACTER = chr(0x1B)
# What argv holds for the byte 0xFF, which is not UTF-8 text.
_LONE_SURROGATE = chr(0xDCFF)


def _build_control_character_refusal(field: str, code_point: int, place: str) -> str:
    """The refusal of a free-text field that holds a control character other than tab, line feed, and carriage return,
    at `place`: "position 8" in a text of one line, "line 2, column 5" in a text of more lines."""
    return (f"The {field} contains the control character U+{code_point:04X} at {place}. The server accepts no"
            " control character except tab, line feed, and carriage return. Remove the character.")


def _build_color_code_refusal(field: str, place: str) -> str:
    """The refusal of a free-text field that holds U+001B, the character that starts a terminal color code."""
    return (f"The {field} contains the control character U+001B at {place}. Terminal color codes start with this"
            " character. Copy the text without colors.")


def _build_not_utf8_text_refusal(field: str, code_point: int, place: str) -> str:
    """The refusal of a free-text field that holds an unpaired surrogate, which argv makes of bytes that are not UTF-8."""
    return f"The {field} is not valid UTF-8 text. It contains U+{code_point:04X} at {place}. Give the text as UTF-8."


def _build_escaped_surrogate_refusal(field: str, code_point: int, place: str) -> str:
    """The refusal of a field of a citations file that holds an unpaired surrogate, which the json module makes of an
    escape of one half of a surrogate pair."""
    return (f"The {field} contains U+{code_point:04X} at {place}. A \\u{code_point:04X} escape in the citations file"
            " gives it, and it is half of a surrogate pair. Write the character as UTF-8 text in the file, or write"
            " both escapes of the pair.")


class TestGithubContextParsedPaths(_MathCliTestCase):
    def context_commands(self, url):
        return [self.build_post_problem_argv("--github-link", url, "Context"),
                ["link-problem-github", _PROBLEM_ID, "--url", url, "--label", "Context"]]

    def test_a_context_path_that_resolves_to_root_is_refused(self):
        paths = [".", "a/..", "%2e", "%2E", "..", ".%2e", "%2E.", "%2e%2E",
                 "a/.%2E", "a/%2e.", "a/%2E%2e", "a/b/../..", "a/../?tab=readme", "a/..#readme",
                 "a\\..", "a/%2e%2e/."]
        for path in paths:
            url = "https://github.com/" + path
            for command in self.context_commands(url):
                with self.subTest(command=command[0], url=url):
                    self.sent.clear()
                    _, message = self.run_cli(command, expected_exit_code=1)
                    self.assertIn("URL that names a page", message)
                    self.assertEqual(self.sent, [])

    def test_a_context_path_that_keeps_a_page_is_sent_exactly_as_given(self):
        for path in ("a/b/..", "a/b/%2E.", "a/b/.%2e", "a/b/%2e%2E", "a/b/../?tab=readme#top",
                     "a/../b", "a/%252e", "a/...", "/", "a\\b\\.."):
            url = "https://github.com/" + path
            for command in self.context_commands(url):
                with self.subTest(command=command[0], url=url):
                    self.sent.clear()
                    self.run_cli(command)
                    body = json.loads(self.sent[-1].data)
                    self.assertEqual(body["githubLinks"][0]["url"] if command[0] == "post-problem" else body["url"], url)


class TestPlainTextOfFreeTextFields(_MathCliTestCase):
    """The server refuses a control character other than tab, line feed, and carriage return, and an unpaired
    surrogate, in every field that it builds on plainTextSchema (design 2026-10-12 §26.24). The command refuses the
    same characters first, before the ORCID preflight and before any request, and names the field, the character,
    and its position."""

    def write_text_file(self, text: str) -> str:
        path = self.scratch_dir / "text-field.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def write_citations_file(self, citation: str, locator: str = "10.1017/cbo9781107340688") -> str:
        path = self.scratch_dir / "text-field-citations.json"
        path.write_text(json.dumps([{"citation": citation, "locator": locator}]), encoding="utf-8")
        return str(path)

    def build_post_challenge_argv(self, citations_path: str) -> list[str]:
        return ["post-challenge", "--title", "An open question", "--field", "cs.LG", "--problem-statement", "p" * 200,
                "--current-state", "c" * 100, "--resolution-criteria", "r" * 50, "--citations-file", citations_path]

    def build_inline_post_argv(self, text: str) -> list[str]:
        return ["post-discussion", _PROBLEM_ID, "--body", text]

    def build_file_post_argv(self, text: str) -> list[str]:
        return ["post-discussion", _PROBLEM_ID, "--body-file", self.write_text_file(text)]

    def build_challenge_citation_argv(self, text: str) -> list[str]:
        return self.build_post_challenge_argv(self.write_citations_file(text))

    def list_free_text_fields(self) -> list[tuple]:
        """Every option whose server field is built on plainTextSchema: the option, the name of its field in the
        messages, the fewest characters that the field takes, and a function that builds a valid command line with
        the given text in that field."""
        def replace_option(argv: list[str], option: str, value: str, new_option: str | None = None) -> list[str]:
            index = argv.index(option)
            argv[index:index + 2] = [new_option or option, value]
            return argv

        add_formal_statement = ["add-formal-statement", _PROBLEM_ID, "--lean-url", _LEAN_SOURCE_URL, "--proof-target",
                                "statement"]
        declare_status = ["declare-status", _PROBLEM_ID, "--status", "open"]
        post_proof = ["post-proof", _PROBLEM_ID, "--claim", "proves", "--writeup", "2601.12345v2"]
        post_lean_check = ["post-lean-check", _PROOF_ID, "--outcome", "pass", "--tool", "t", "--lean-toolchain", "v",
                           "--evidence-url", _CHECK_RUN_URL]
        citation = "H. Minc, Permanents, 1978, chapter 2."
        fields = [
            ("post-problem --title", "title", 8,
             lambda text: replace_option(self.build_post_problem_argv(), "--title", text)),
            ("post-problem --citations-file, the citation", "citation of entry 1", 10,
             lambda text: replace_option(self.build_post_problem_argv(), "--citations-file",
                                         self.write_citations_file(text))),
            ("post-problem --citations-file, the locator", "locator of entry 1", 1,
             lambda text: replace_option(self.build_post_problem_argv(), "--citations-file",
                                         self.write_citations_file(citation, text))),
            ("post-problem --github-link, the label", "link label", 1,
             lambda text: self.build_post_problem_argv("--github-link", "https://github.com/o/r", text)),
            ("problems --query", "search text", 1, lambda text: ["problems", "--query", text]),
            ("add-formal-statement --note", "note", 0, lambda text: [*add_formal_statement, "--note", text]),
            ("add-formal-statement --note-file", "note", 0,
             lambda text: [*add_formal_statement, "--note-file", self.write_text_file(text)]),
            ("declare-status --note", "note", 1, lambda text: [*declare_status, "--note", text]),
            ("declare-status --note-file", "note", 1,
             lambda text: [*declare_status, "--note-file", self.write_text_file(text)]),
            ("link-problem-github --label", "label", 1,
             lambda text: ["link-problem-github", _PROBLEM_ID, "--url", "https://github.com/o/r", "--label", text]),
            ("post-proof --summary", "summary", 100, lambda text: [*post_proof, "--summary", text]),
            ("post-proof --summary-file", "summary", 100,
             lambda text: [*post_proof, "--summary-file", self.write_text_file(text)]),
            ("post-lean-check --tool", "tool", 1, lambda text: replace_option(list(post_lean_check), "--tool", text)),
            ("post-lean-check --lean-toolchain", "Lean toolchain", 1,
             lambda text: replace_option(list(post_lean_check), "--lean-toolchain", text)),
            ("post-lean-check --output-excerpt-file", "output excerpt", 1,
             lambda text: [*post_lean_check, "--output-excerpt-file", self.write_text_file(text)]),
            ("post-discussion --body", "post", 1, self.build_inline_post_argv),
            ("post-discussion --body-file", "post", 1, self.build_file_post_argv),
            ("report-math --note", "note", 1, lambda text: ["report-math", "math_post", _PROOF_ID, "--note", text]),
            ("report-challenge --note", "note", 1, lambda text: ["report-challenge", _PROBLEM_ID, "--note", text]),
            ("post-challenge --citations-file, the citation", "citation of entry 1", 10,
             self.build_challenge_citation_argv),
            ("post-challenge --citations-file, the locator", "locator of entry 1", 1,
             lambda text: self.build_post_challenge_argv(self.write_citations_file(citation, text))),
        ]
        for name, minimum in (("statement", 50), ("motivation", 100), ("current-state", 100),
                              ("resolution-criteria", 50)):
            field = name.replace("-", " ")
            fields.append((f"post-problem --{name}", field, minimum,
                           lambda text, name=name: replace_option(self.build_post_problem_argv(), f"--{name}", text)))
            fields.append((f"post-problem --{name}-file", field, minimum,
                           lambda text, name=name: replace_option(self.build_post_problem_argv(), f"--{name}",
                                                                  self.write_text_file(text), f"--{name}-file")))
        return fields

    def test_each_kind_of_input_names_the_field_the_character_and_its_position_and_sends_nothing(self) -> None:
        tail = ", Permanents, 1978, chapter 2."
        for kind, field, build_argv in (("inline option", "post", self.build_inline_post_argv),
                                        ("file option", "post", self.build_file_post_argv),
                                        ("Grand Challenge citation", "citation of entry 1",
                                         self.build_challenge_citation_argv)):
            for text, message in (
                    ("H. Minc" + _ESCAPE_CHARACTER + "[31m" + tail, _build_color_code_refusal(field, "position 8")),
                    ("H. Minc" + _BELL_CHARACTER + tail, _build_control_character_refusal(field, 0x07, "position 8")),
                    ("H. Minc" + chr(0x7F) + tail, _build_control_character_refusal(field, 0x7F, "position 8"))):
                with self.subTest(kind=kind, character=f"U+{ord(text[7]):04X}"):
                    _, stderr_text = self.run_cli(build_argv(text), expected_exit_code=1)
                    self.assertEqual(stderr_text, message + "\n")
        # The ORCID preflight is a request too, and none went out.
        self.assertEqual(self.sent, [])

    def test_a_lone_surrogate_is_named_as_text_that_is_not_utf8(self) -> None:
        text = "H. Minc" + _LONE_SURROGATE + ", Permanents, 1978, chapter 2."
        _, stderr_text = self.run_cli(self.build_inline_post_argv(text), expected_exit_code=1)
        self.assertEqual(stderr_text, _build_not_utf8_text_refusal("post", 0xDCFF, "position 8") + "\n")
        # The json module turns the escape \udcff into a lone surrogate, so the message names the escape.
        _, stderr_text = self.run_cli(self.build_challenge_citation_argv(text), expected_exit_code=1)
        self.assertEqual(stderr_text, _build_escaped_surrogate_refusal("citation of entry 1", 0xDCFF, "position 8") + "\n")
        _, stderr_text = self.run_cli(self.build_post_challenge_argv(self.write_citations_file(
            "H. Minc, Permanents, 1978.", "10.1017/cbo" + chr(0xD800))), expected_exit_code=1)
        self.assertEqual(stderr_text, _build_escaped_surrogate_refusal("locator of entry 1", 0xD800, "position 12") + "\n")
        # A file is read as UTF-8 text, so the reader refuses the byte that argv turns into a lone surrogate.
        path = self.scratch_dir / "post.txt"
        path.write_bytes(b"H. Minc\xff, Permanents, 1978, chapter 2.")
        _, stderr_text = self.run_cli(["post-discussion", _PROBLEM_ID, "--body-file", str(path)], expected_exit_code=1)
        self.assertEqual(stderr_text, f"The file {path} is not UTF-8 text. Save it as UTF-8.\n")
        _, stderr_text = self.run_cli(["problems", "--query", "perm" + _LONE_SURROGATE], expected_exit_code=1)
        self.assertEqual(stderr_text, _build_not_utf8_text_refusal("search text", 0xDCFF, "position 5") + "\n")
        self.assertEqual(self.sent, [])

    def test_every_free_text_field_that_the_server_builds_on_plain_text_refuses_a_control_character(self) -> None:
        for option, field, minimum, build_argv in self.list_free_text_fields():
            with self.subTest(option=option):
                text = "ab" + _BELL_CHARACTER + "c" * max(1, minimum)
                _, stderr_text = self.run_cli(build_argv(text), expected_exit_code=1)
                self.assertEqual(stderr_text, _build_control_character_refusal(field, 0x07, "position 3") + "\n")
        self.assertEqual(self.sent, [])

    def test_the_refusal_names_the_position_in_a_line_and_the_line_and_the_column_in_more_lines(self) -> None:
        # Both count characters from 1. A line ends at a line feed, a carriage return, or both together.
        for text, place in ((_BELL_CHARACTER, "position 1"), ("  " + _BELL_CHARACTER, "position 3"),
                            (chr(0x1F600) + _BELL_CHARACTER, "position 2"),
                            ("one\ntwo\t" + _BELL_CHARACTER + _ESCAPE_CHARACTER, "line 2, column 5"),
                            ("one\r\ntwo" + _BELL_CHARACTER, "line 2, column 4"),
                            ("one\rtwo" + _BELL_CHARACTER, "line 2, column 4"),
                            (_BELL_CHARACTER + "\nsecond line", "line 1, column 1"),
                            ("first\n\n" + chr(0x1F600) + _BELL_CHARACTER, "line 3, column 2")):
            with self.subTest(text=text):
                _, stderr_text = self.run_cli(self.build_inline_post_argv(text), expected_exit_code=1)
                self.assertEqual(stderr_text, _build_control_character_refusal("post", 0x07, place) + "\n")
        # A file of more lines names the line too.
        _, stderr_text = self.run_cli(self.build_file_post_argv("The first line.\nA " + _ESCAPE_CHARACTER + "[31mred word."),
                                      expected_exit_code=1)
        self.assertEqual(stderr_text, _build_color_code_refusal("post", "line 2, column 3") + "\n")
        self.assertEqual(self.sent, [])

    def test_the_characters_that_the_server_keeps_are_sent_with_lf_line_breaks(self) -> None:
        # Tab, line feed, carriage return, the C1 controls, an invisible format character, the line separator, and a
        # character beyond the basic plane, each between two letters.
        kept = ("a\tb\nc\rd" + chr(0x80) + "e" + chr(0x85) + "f" + chr(0x9F) + "g" + chr(0x200B) + "h" + chr(0x2028)
                + "i" + chr(0x1F600) + "j")
        normalized = ("a\tb\nc\nd" + chr(0x80) + "e" + chr(0x85) + "f" + chr(0x9F) + "g" + chr(0x200B) + "h"
                      + chr(0x2028) + "i" + chr(0x1F600) + "j")
        posts_path = f"/api/v1/math-problems/{_PROBLEM_ID}/posts"
        self.run_cli(self.build_inline_post_argv(kept))
        self.assertEqual(self.read_sent_body("POST", posts_path)["body"], normalized)
        # The plain-text boundary normalizes file input by the same rule as inline text.
        self.sent.clear()
        self.run_cli(self.build_file_post_argv(kept))
        self.assertEqual(self.read_sent_body("POST", posts_path)["body"], normalized)
        self.run_cli(self.build_challenge_citation_argv("H. Minc " + kept))
        self.assertEqual(self.read_sent_body("POST", "/api/v1/grand-challenges")["citations"],
                         [{"citation": "H. Minc " + normalized, "locator": "10.1017/cbo9781107340688"}])
        self.run_cli(["problems", "--query", kept])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(self.sent[-1].full_url).query)["q"], [normalized])

    def test_the_post_normalization_character_rule_matches_the_server(self) -> None:
        refused_code_points = [code_point for code_point in range(0x10000)
                               if _cli._NON_PLAIN_TEXT_CHARACTER_RE.search(chr(code_point))]
        self.assertEqual(refused_code_points,
                         [*range(0x00, 0x09), *range(0x0B, 0x20), 0x7F, *range(0xD800, 0xE000)])
        self.assertIsNone(_cli._NON_PLAIN_TEXT_CHARACTER_RE.search(chr(0x1F600)))

    def test_the_grand_challenge_fields_that_the_server_does_not_build_on_plain_text_keep_their_checks(self) -> None:
        # The server builds the title, the field, the problem statement, the current state, the resolution criteria,
        # and the resolution note of a Grand Challenge on z.string(), so their text goes out as before.
        argv = self.build_post_challenge_argv(self.write_citations_file("H. Minc, Permanents, 1978, chapter 2."))
        argv[argv.index("--title") + 1] = "An open" + _BELL_CHARACTER + " question"
        self.run_cli(argv)
        self.assertEqual(self.read_sent_body("POST", "/api/v1/grand-challenges")["title"],
                         "An open" + _BELL_CHARACTER + " question")
        self.run_cli(["solve-challenge", _PROBLEM_ID, "--note", "Solved" + _BELL_CHARACTER + " by the cited proof."])
        self.assertEqual(self.read_sent_body("PATCH", f"/api/v1/grand-challenges/{_PROBLEM_ID}")["resolutionNote"],
                         "Solved" + _BELL_CHARACTER + " by the cited proof.")


class TestRefusedListQueries(_MathCliTestCase):
    """A read sends no body, so a 422 to a list is the server's refusal of the query. The message names the query and
    asks for a correction, and the server's detail follows. The server judges every limit, so no bound is stated."""

    _DETAIL_TEXT = '{"error": "invalid_request"}'

    def test_a_refused_page_query_names_the_limit_and_the_cursor_and_keeps_the_detail(self) -> None:
        history_route = f"/api/v1/math-problems/{_PROBLEM_ID}/status-events"
        for command, path_id, route in (*_PAGED_READ_COMMANDS, ("problem-history", _PROBLEM_ID, history_route)):
            with self.subTest(command=command):
                self.sent.clear()
                self.answers[("GET", route)] = (422, {"error": "invalid_request"})
                _, stderr_text = self.run_cli([command, path_id, "--limit", "60"], expected_exit_code=1)
                self.assertEqual(stderr_text, "The server rejected the query (the limit or the cursor). Correct it."
                                              f" Then send the request again.\n{self._DETAIL_TEXT}\n")
                self.assertEqual(self.list_sent_requests(), [("GET", route)])

    def test_a_refused_problem_list_query_names_its_search_text_and_its_filters_too(self) -> None:
        self.answers[("GET", "/api/v1/math-problems")] = (422, {"error": "invalid_request"})
        _, stderr_text = self.run_cli(["problems", "--sort", "relevance", "--limit", "101"], expected_exit_code=1)
        self.assertEqual(stderr_text, "The server rejected the query (the search text, a filter, the sort, the limit,"
                                      " or the cursor). Correct it. Then send the request again."
                                      f"\n{self._DETAIL_TEXT}\n")

    def test_a_refused_write_keeps_the_message_about_its_body(self) -> None:
        self.answers[("POST", f"/api/v1/math-problems/{_PROBLEM_ID}/posts")] = (422, {"error": "invalid_request"})
        _, stderr_text = self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "A post."], expected_exit_code=1)
        self.assertEqual(stderr_text, "The server rejected the request body. Correct the body. Then send it again."
                                      f"\n{self._DETAIL_TEXT}\n")


class TestServerErrorsAfterAWrite(_MathCliTestCase):
    """An answer of 500 to 599 does not show that the server applied nothing. A server that stores a post and then
    answers 503 leaves a post that a second run would post again, so the advice to a write is to check first. A read
    changes nothing, and it keeps the advice to send it again."""

    _DETAIL_TEXT = '{"error": "source_unavailable"}'

    def setUp(self) -> None:
        super().setUp()
        self.stored_posts: list[str] = []
        posts_path = f"/api/v1/math-problems/{_PROBLEM_ID}/posts"
        answer_other_requests = _cli.open_url_within_origin

        def store_the_post_then_answer_503(request: urllib.request.Request,
                                           timeout: float | None = None) -> _FakeResponse:
            route = (request.get_method(), urllib.parse.urlparse(request.full_url).path)
            if route == ("POST", posts_path):
                self.sent.append(request)
                # The server applies the write, and only then answers with the error.
                self.stored_posts.append(json.loads(request.data.decode())["body"])
                raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {},
                                             io.BytesIO(self._DETAIL_TEXT.encode()))
            if route == ("GET", posts_path):
                self.sent.append(request)
                page = {"posts": [{"body": body} for body in self.stored_posts], "nextCursor": None}
                return _FakeResponse(json.dumps(page).encode(), 200)
            return answer_other_requests(request, timeout)

        _cli.open_url_within_origin = store_the_post_then_answer_503

    def test_a_post_that_the_server_stored_before_its_503_is_reported_as_possibly_applied(self) -> None:
        _, stderr_text = self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "A post."], expected_exit_code=1)

        self.assertEqual(stderr_text, "The paper source is not reachable now."
                                      f" {build_unconfirmed_write_msg('https://api.test')}\n{self._DETAIL_TEXT}\n")
        # A second run would post the same text again, so no sentence asks the user to send the request again.
        self.assertNotIn("again later", stderr_text)
        self.assertEqual(self.stored_posts, ["A post."])
        # The check that the advice asks for finds the post.
        stdout_text, _ = self.run_cli(["discussion", _PROBLEM_ID])
        self.assertEqual(json.loads(stdout_text)["posts"], [{"body": "A post."}])

    def test_every_write_method_gets_the_check_first_advice_for_every_server_error(self) -> None:
        vote_route = f"/api/v1/math-problems/{_PROBLEM_ID}/vote"
        writes = ((["vote-problem", _PROBLEM_ID, "--value", "1"], "PUT", vote_route),
                  (["vote-problem", _PROBLEM_ID, "--value", "0"], "DELETE", vote_route),
                  (["declare-status", _PROBLEM_ID, "--status", "open", "--note", "Reopened."], "PATCH",
                   f"/api/v1/math-problems/{_PROBLEM_ID}"),
                  (["link-problem-paper", _PROBLEM_ID, "--identifier", "2609.97001v3"], "POST",
                   f"/api/v1/math-problems/{_PROBLEM_ID}/papers"))
        for argv, method, route in writes:
            for status in (500, 502, 503, 504, 599):
                with self.subTest(command=argv[0], method=method, status=status):
                    self.answers[(method, route)] = (status, {"error": "source_unavailable"})
                    description = ("The paper source is not reachable now." if status == 503
                                   else f"The server returned HTTP {status}.")
                    _, stderr_text = self.run_cli(argv, expected_exit_code=1)
                    self.assertEqual(stderr_text, f"{description} {build_unconfirmed_write_msg('https://api.test')}"
                                                  f"\n{self._DETAIL_TEXT}\n")

    def test_a_read_keeps_the_advice_to_send_the_request_again(self) -> None:
        for status in (500, 503, 504):
            with self.subTest(status=status):
                self.answers[("GET", f"/api/v1/math-problems/{_PROBLEM_ID}")] = (status,
                                                                                 {"error": "source_unavailable"})
                description = ("The paper source is not reachable now." if status == 503
                               else f"The server returned HTTP {status}.")
                _, stderr_text = self.run_cli(["problem", _PROBLEM_ID], expected_exit_code=1)
                self.assertEqual(stderr_text,
                                 f"{description} Send the same request again later.\n{self._DETAIL_TEXT}\n")

    def test_the_preflight_read_of_a_write_keeps_the_advice_of_a_read(self) -> None:
        def answer_503_to_the_preflight(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent.append(request)
            raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {},
                                         io.BytesIO(self._DETAIL_TEXT.encode()))
        _cli.open_url_within_origin = answer_503_to_the_preflight

        _, stderr_text = self.run_cli(["post-discussion", _PROBLEM_ID, "--body", "A post."], expected_exit_code=1)

        self.assertEqual(stderr_text, f"The paper source is not reachable now. Send the same request again later."
                                      f"\n{self._DETAIL_TEXT}\n")
        self.assertEqual(self.list_sent_requests(), [("GET", "/api/v1/me")])

    def test_a_conflict_on_another_write_keeps_its_advice(self) -> None:
        self.answers[("POST", "/api/v1/math-problems")] = (409, {"error": "conflict"})
        _, stderr_text = self.run_cli(self.build_post_problem_argv(), expected_exit_code=1)
        self.assertEqual(stderr_text, "The resource is in a state that does not accept this operation. Run exactory"
                                      ' status <verification-id> to see the current state.\n{"error": "conflict"}\n')


if __name__ == "__main__":
    unittest.main()
