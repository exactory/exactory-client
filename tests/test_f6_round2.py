"""Offline regressions for request uncertainty and direct-deposit recovery."""

from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import json
import os
import socket
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.error
import urllib.request

from research_harness.integration import record_direct_deposit
from research_harness.storage import Store
from test_draft import _FakeZenodoApi, _load_bin_module


_cli = _load_bin_module("exactory", "f6_cli")
_draft = _load_bin_module("exactory-draft", "f6_draft")
_BASE = "http://zenodo.test/api"
_DRAFT_URL = _BASE + "/deposit/4242"


class _AcceptThenFailSocket:
    """Accept the entire HTTP request, then fail inside real HTTPConnection.send."""

    def __init__(self, error_type):
        self.error_type = error_type
        self.accepted = b""
        self.complete_requests = []

    def sendall(self, data):
        self.accepted += bytes(data)
        if b"\r\n\r\n" not in self.accepted:
            return
        headers, body = self.accepted.split(b"\r\n\r\n", 1)
        length = next((int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                       if line.lower().startswith(b"content-length:")), 0)
        if len(body) == length:
            self.complete_requests.append(self.accepted)
            raise self.error_type("failed after accepting the whole request")

    def setsockopt(self, *args):
        pass

    def close(self):
        pass


class _OfflineTestCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(scratch.name)
        Path(".exactory").mkdir()
        Path("paper.pdf").write_bytes(b"%PDF-1.4 test")
        env = patch.dict(os.environ, {"EXACTORY_API_URL": "http://api.test", "EXACTORY_API_KEY": "test-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.enterContext(patch.object(_cli, "_API_BASE_URL", "http://api.test", create=True))
        self.enterContext(patch.object(_draft, "_ZENODO_API_URLS", {"sandbox": _BASE}))
        self.enterContext(patch("urllib.request.getproxies", return_value={}))

    # Python 3.9's TestCase has no enterContext.
    def enterContext(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def capture_exit(self, call, code=1):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                call()
        self.assertEqual(caught.exception.code, code)
        return out.getvalue(), err.getvalue()

    def deposit(self, *, new_version=False, store=None, problem=None):
        args = argparse.Namespace(new_version=new_version, publish=True)
        return _draft._deposit_directly(store, None, args, _BASE, "sandbox", {}, Path("paper.pdf"), None,
                                        "test-token", problem)

    def write_prior(self, **changes):
        state = dict(environment="sandbox", deposition_id=4242, draft_url=_DRAFT_URL, **changes)
        Path(".exactory/deposit.json").write_text(json.dumps(state), encoding="utf-8")
        return state


class TestUncertainSend(_OfflineTestCase):
    def test_real_urllib_wraps_a_complete_send_failure_for_a_write_and_a_read(self):
        for error_type in (ConnectionResetError, BrokenPipeError):
            for method in ("POST", "GET"):
                with self.subTest(error=error_type.__name__, method=method):
                    sock = _AcceptThenFailSocket(error_type)
                    with patch("socket.create_connection", return_value=sock):
                        _, err = self.capture_exit(lambda: _cli._send_request(method, "/api/v1/posts", {"body": "x"}
                                                                             if method == "POST" else None))
                    self.assertEqual(len(sock.complete_requests), 1)
                    self.assertIn("failed after accepting the whole request", err)
                    if method == "POST":
                        self.assertIn("may have applied the request", err)
                        self.assertIn("Before you run the command again, check", err)
                    else:
                        self.assertIn("Run the command again.", err)
                        self.assertNotIn("may have applied", err)
                    self.assertNotIn("Then send the request again", err)

    def test_real_urllib_send_failure_uses_creation_and_publication_recovery(self):
        real_open = _draft._open_url
        for error_type in (ConnectionResetError, BrokenPipeError):
            for step in ("creation", "publication"):
                with self.subTest(error=error_type.__name__, step=step):
                    sock = _AcceptThenFailSocket(error_type)
                    fake = _FakeZenodoApi()
                    def open_request(request):
                        if (step == "creation" and request.full_url.endswith("/deposit/depositions")
                                or step == "publication" and request.full_url.endswith("/actions/publish")):
                            return real_open(request)
                        return fake(request)
                    with patch.object(_draft, "_open_url", open_request), patch("socket.create_connection", return_value=sock):
                        _, err = self.capture_exit(self.deposit)
                    self.assertEqual(len(sock.complete_requests), 1)
                    self.assertIn("may have created a draft deposition" if step == "creation" else
                                  "Deposition 4242 may be published already", err)
                    self.assertNotIn("stays a draft", err)
                    self.assertNotIn("Then run the command again", err)

    def test_known_unsent_controls_through_real_urllib(self):
        for failure in ("refused", "dns"):
            for operation in ("write", "read", "creation", "publication"):
                with self.subTest(failure=failure, operation=operation):
                    manager = (patch("socket.create_connection", side_effect=ConnectionRefusedError(61, "refused"))
                               if failure == "refused" else patch("socket.getaddrinfo", side_effect=socket.gaierror("no name")))
                    with manager:
                        if operation in ("write", "read"):
                            _, err = self.capture_exit(lambda: _cli._send_request(
                                "POST" if operation == "write" else "GET", "/test"))
                        else:
                            advice = (_draft._FailureAdvice(uncertain_msg="uncertain creation") if operation == "creation"
                                      else _draft._build_publish_request_advice_set(4242, _DRAFT_URL, None, is_new_version=False))
                            _, err = self.capture_exit(lambda: _draft._call_zenodo(
                                "POST", _BASE + "/test", "test-token", environment="sandbox", failure_advice=advice))
                    self.assertNotIn("may have applied", err)
                    self.assertNotIn("may be published", err)
                    self.assertNotIn("uncertain creation", err)
                    self.assertIn("stays a draft" if operation == "publication" else "again", err)

    def test_only_proven_pre_send_reasons_get_unsent_advice(self):
        known = (socket.gaierror("dns"), ConnectionRefusedError("refused"),
                 ssl.SSLCertVerificationError("certificate"), http.client.InvalidURL("address"))
        unknown = (TimeoutError("timeout"), ConnectionResetError("reset"), BrokenPipeError("pipe"),
                   ssl.SSLError("tls"), OSError("other"), "untyped reason")
        for reason in known + unknown:
            with self.subTest(reason=type(reason).__name__):
                with patch.object(_cli, "open_url_within_origin", side_effect=urllib.error.URLError(reason)):
                    _, err = self.capture_exit(lambda: _cli._send_request("POST", "/test"))
                self.assertEqual("may have applied" in err, reason in unknown)
                with patch.object(_draft, "_open_url", side_effect=urllib.error.URLError(reason)):
                    _, err = self.capture_exit(lambda: _draft._call_zenodo("POST", _BASE + "/test", "test-token",
                        environment="sandbox", failure_advice=_draft._FailureAdvice(uncertain_msg="uncertain", unsent_msg="unsent")))
                self.assertIn("uncertain" if reason in unknown else "unsent", err)


class TestJavascriptTrim(_OfflineTestCase):
    def test_discussion_and_report_note_match_server_trim(self):
        for command, option, field in ((["post-discussion", "p"], "--body", "body"),
                                       (["report-challenge", "c"], "--note", "note")):
            for value in ("\x85", "\ufeff", "x\x85"):
                with self.subTest(command=command, value=repr(value)):
                    sent = []
                    def send(method, path, body=None, **kwargs):
                        sent.append((method, body))
                        return {"orcidId": "linked"}, 201
                    args = _cli._build_parser().parse_args([*command, option, value])
                    with patch.object(_cli, "_send_request", side_effect=send), contextlib.redirect_stdout(io.StringIO()):
                        if value == "\ufeff":
                            self.capture_exit(lambda: args.handler(args))
                            self.assertEqual(sent, [])
                        else:
                            args.handler(args)
                            self.assertEqual(sent[-1][1][field], value)

    def test_every_ecmascript_trim_character_and_no_python_extras(self):
        whitespace = "\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200b))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"
        for char in whitespace:
            with self.subTest(code=ord(char)):
                self.assertEqual(_cli._require_utf16_bounds("field", char + "x" + char, 1, 1, trim=True), "x")
        self.assertEqual(_cli._require_utf16_bounds("field", "\x85x\x85", 3, 3), "\x85x\x85")

    def test_untrimmed_problem_and_challenge_fields_are_sent_as_given(self):
        citation = " \ufeffA citation of the source.\ufeff "
        Path("citations.json").write_text(json.dumps([{"citation": citation, "locator": "10.1234/source"}]))
        for command, statement_option, statement_field, minimum in (
                ("post-problem", "--statement", "statement", 50),
                ("post-challenge", "--problem-statement", "problemStatement", 200)):
            with self.subTest(command=command):
                title, statement = " \ufeffA padded title\ufeff ", " \ufeff" + "s" * minimum + "\ufeff "
                argv = [command, "--title", title, statement_option, statement,
                        "--current-state", "c" * 100, "--resolution-criteria", "r" * 50,
                        "--citations-file", "citations.json"]
                argv += ["--area", "math.CO", "--motivation", "m" * 100] if command == "post-problem" else ["--field", "math"]
                sent = []
                def send(method, path, body=None, **kwargs):
                    if body is not None:
                        sent.append(body)
                    return {"orcidId": "linked"}, 201
                args = _cli._build_parser().parse_args(argv)
                with patch.object(_cli, "_send_request", side_effect=send), contextlib.redirect_stdout(io.StringIO()):
                    args.handler(args)
                self.assertEqual(sent[-1]["title"], title)
                self.assertEqual(sent[-1][statement_field], statement)
                self.assertEqual(sent[-1]["citations"][0]["citation"], citation)

    def test_citation_locators_are_not_trimmed_and_use_javascript_whitespace(self):
        for locator in ("10.1234/source\x85", "10.1234/source\ufeff", " 10.1234/source "):
            with self.subTest(locator=repr(locator)):
                Path("citations.json").write_text(json.dumps([{"citation": "A citation of the source.", "locator": locator}]))
                if locator.endswith("\x85"):
                    result = _cli._read_citations_file("citations.json")
                    self.assertEqual(result[0]["locator"], locator)
                else:
                    self.capture_exit(lambda: _cli._read_citations_file("citations.json"))


class TestDirectReadBack(_OfflineTestCase):
    def test_the_command_reads_recorded_publication_before_problem_preflight(self):
        self.write_prior()
        Path(".exactory/draft.json").write_text(json.dumps({"title": "A paper"}))
        Path("abstract.txt").write_text("An abstract.")
        args = _draft._build_parser().parse_args(["deposit", "--new-version", "--publish", "--pdf", "paper.pdf",
            "--abstract-file", "abstract.txt", "--creator", "Ada", "--math-problem", "656c336e-e892-4c47-80d2-a71d022f4116"])
        calls = []
        def answer(request):
            calls.append("zenodo GET")
            return {"id": 4242, "doi": "10.5072/zenodo.4242", "links": {"record_html": "http://zenodo.test/records/4242"}}
        def resolve(problem_id):
            calls.append("problem preflight")
            return {"problem_id": problem_id, "api_url": "http://api.test"}
        with patch.object(_draft, "_open_url", answer), patch.object(_draft, "_resolve_problem_target", resolve), \
                patch.object(_draft, "_read_zenodo_token", return_value="test-token"):
            self.capture_exit(lambda: _draft._run_deposit(args), 0)
        self.assertEqual(calls, ["zenodo GET"])

    def test_published_read_back_saves_the_record_and_stops_successfully(self):
        self.write_prior()
        answer = {"id": 4242, "doi": "10.5072/zenodo.4242", "conceptdoi": "10.5072/zenodo.42",
                  "links": {"record_html": "http://zenodo.test/records/4242"}}
        with patch.object(_draft, "_open_url", return_value=answer) as opened:
            out, err = self.capture_exit(lambda: self.deposit(new_version=True), 0)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args[0][0].get_method(), "GET")
        self.assertEqual(opened.call_args[0][0].full_url, _BASE + "/deposit/depositions/4242")
        state = json.loads(Path(".exactory/deposit.json").read_text())
        self.assertEqual(state["doi"], answer["doi"])
        self.assertEqual(state["concept_doi"], answer["conceptdoi"])
        self.assertEqual(state["record_url"], answer["links"]["record_html"])
        self.assertIn(answer["doi"], out)
        self.assertIn("earlier run is published", err)
        self.assertIn("second run with --new-version revises it", err)

    def test_draft_read_back_continues_the_same_draft_and_prints_before_upload(self):
        self.write_prior()
        fake = _FakeZenodoApi()
        first = True
        def answer(request):
            nonlocal first
            if first:
                first = False
                self.assertEqual((request.get_method(), request.full_url), ("GET", _BASE + "/deposit/depositions/4242"))
                return {"id": 4242, "submitted": False,
                        "links": {"bucket": _BASE + "/files/bucket-1", "html": _DRAFT_URL}}
            if request.get_method() == "PUT":
                self.assertIn("Continuing with draft deposition 4242", sys.stdout.getvalue())
                self.assertIn(_DRAFT_URL, sys.stdout.getvalue())
            return fake(request)
        out = io.StringIO()
        with patch.object(_draft, "_open_url", answer), contextlib.redirect_stdout(out):
            self.deposit(new_version=True)
        self.assertFalse(any(r.full_url.endswith("/actions/newversion") for r in fake.requests))
        self.assertFalse(any(r.full_url.endswith("/deposit/depositions") for r in fake.requests))
        self.assertEqual(json.loads(Path(".exactory/deposit.json").read_text())["doi"], "10.5281/zenodo.4242")

    def test_missing_draft_uses_newest_published_direct_history_of_same_environment(self):
        store = Store(Path.cwd(), create=True)
        for key, dep_id, env, doi in (("z-first", 10, "sandbox", "10.5072/zenodo.10"),
                                      ("a-latest", 20, "sandbox", "10.5072/zenodo.20"),
                                      ("other-env", 30, "production", "10.5281/zenodo.30"),
                                      ("pending", 40, "sandbox", "")):
            state = dict(environment=env, deposition_id=dep_id, draft_url="http://zenodo.test/deposit/" + str(dep_id))
            if doi:
                state["doi"] = doi
            record_direct_deposit(store, state, expected_revision=store.revision, request_id=key)
        self.write_prior()
        seen = []
        fake = _FakeZenodoApi()
        def answer(request):
            seen.append((request.get_method(), request.full_url))
            if request.full_url == _BASE + "/deposit/depositions/4242":
                raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, io.BytesIO(b"missing"))
            if request.full_url.endswith("/20/actions/newversion"):
                self.assertIn("20", sys.stdout.getvalue())
                self.assertIn("10.5072/zenodo.20", sys.stdout.getvalue())
                return {"links": {"latest_draft": _BASE + "/deposit/depositions/4343"}}
            return fake(request)
        with patch.object(_draft, "_open_url", answer), contextlib.redirect_stdout(io.StringIO()):
            self.deposit(new_version=True, store=store)
        self.assertEqual(seen[:2], [("GET", _BASE + "/deposit/depositions/4242"),
                                   ("POST", _BASE + "/deposit/depositions/20/actions/newversion")])

    def test_missing_draft_without_published_history_names_both_recovery_choices(self):
        for has_store in (False, True):
            with self.subTest(has_store=has_store):
                self.write_prior()
                store = Store(Path.cwd(), create=True) if has_store else None
                with patch.object(_draft, "_open_url", side_effect=urllib.error.HTTPError(
                        _BASE, 404, "missing", {}, io.BytesIO(b"missing"))) as opened:
                    _, err = self.capture_exit(lambda: self.deposit(new_version=True, store=store))
                self.assertEqual(opened.call_count, 1)
                for text in ("4242", "deleted", "without --new-version", "New version", "workspace's record"):
                    self.assertIn(text, err)

    def test_failed_or_unsettled_read_back_never_writes(self):
        for answer in (urllib.error.URLError(TimeoutError("timeout")),
                       urllib.error.HTTPError(_BASE, 410, "gone", {}, io.BytesIO(b"gone")),
                       {"id": 4242, "submitted": True}):
            with self.subTest(answer=type(answer).__name__):
                prior = self.write_prior()
                with patch.object(_draft, "_open_url", **({"side_effect": answer} if isinstance(answer, Exception)
                                                       else {"return_value": answer})) as opened:
                    _, err = self.capture_exit(lambda: self.deposit(new_version=True))
                self.assertEqual(opened.call_count, 1)
                self.assertEqual(opened.call_args[0][0].get_method(), "GET")
                self.assertEqual(json.loads(Path(".exactory/deposit.json").read_text()), prior)
                self.assertIn("4242", err)
                self.assertIn("reads deposition 4242 back first", err)

    def test_missing_deposition_with_an_unreadable_store_does_not_claim_empty_history(self):
        self.write_prior()
        error = _draft.ResearchError("store_busy", "The workspace store is busy")
        with patch.object(_draft, "_open_url", side_effect=urllib.error.HTTPError(
                _BASE, 404, "missing", {}, io.BytesIO(b"missing"))) as opened:
            _, err = self.capture_exit(lambda: _draft._open_new_version_draft(
                _BASE, "sandbox", "test-token", None, error))
        self.assertEqual(opened.call_count, 1)
        self.assertIn("history could not be read", err)
        self.assertIn("Resolve the workspace store error", err)
        self.assertIn("4242", err)
        self.assertNotIn("has no published direct deposit", err)

    def test_a_null_successful_read_back_never_means_a_404(self):
        store = Store(Path.cwd(), create=True)
        prior = self.write_prior(doi="10.5072/zenodo.4242")
        record_direct_deposit(store, prior, expected_revision=store.revision, request_id="published-prior")
        self.write_prior()
        with patch.object(_draft, "_open_url", return_value=None) as opened:
            _, err = self.capture_exit(lambda: self.deposit(new_version=True, store=store))
        self.assertEqual(opened.call_count, 1)
        self.assertIn("does not describe a deposition", err)
        self.assertIn("reads deposition 4242 back first", err)
        self.assertNotIn("was deleted", err)

    def test_a_read_back_cannot_switch_the_recorded_deposition(self):
        prior = self.write_prior()
        fake = _FakeZenodoApi()
        def answer(request):
            if request.full_url == _BASE + "/deposit/depositions/4242":
                fake.requests.append(request)
                return {"id": 4343, "submitted": False,
                        "links": {"bucket": _BASE + "/files/bucket-2", "html": _BASE + "/deposit/4343"}}
            return fake(request)
        with patch.object(_draft, "_open_url", answer):
            _, err = self.capture_exit(lambda: self.deposit(new_version=True))
        self.assertEqual(len(fake.requests), 1)
        self.assertIn("another deposition", err)
        self.assertEqual(json.loads(Path(".exactory/deposit.json").read_text()), prior)

    def test_refused_new_version_publish_then_rerun_publishes_that_draft_once(self):
        self.write_prior(doi="10.5072/zenodo.4242")
        fake = _FakeZenodoApi()
        def refuse_publish(request):
            if request.full_url.endswith("/actions/publish"):
                raise urllib.error.HTTPError(request.full_url, 400, "refused", {}, io.BytesIO(b"refused"))
            return fake(request)
        with patch.object(_draft, "_open_url", refuse_publish):
            _, err = self.capture_exit(lambda: self.deposit(new_version=True))
        self.assertIn("reads deposition 4343 back first", err)
        self.assertIn("never publishes a second record", err)
        fake.requests.clear()
        def answer(request):
            result = fake(request)
            if request.full_url.endswith("/deposit/depositions/4343") and request.get_method() == "GET":
                result["submitted"] = False
            return result
        with patch.object(_draft, "_open_url", answer), contextlib.redirect_stdout(io.StringIO()):
            self.deposit(new_version=True)
        self.assertEqual(sum(r.full_url.endswith("/actions/publish") for r in fake.requests), 1)
        self.assertFalse(any(r.full_url.endswith("/actions/newversion") for r in fake.requests))


class TestFailureAdvice(_OfflineTestCase):
    def test_fresh_draft_recovery_only_deletes_then_retries(self):
        fake = _FakeZenodoApi()
        def fail_upload(request):
            if "/files/" in request.full_url:
                raise OSError("upload failed")
            return fake(request)
        for problem in (None, {"problem_id": "p"}):
            with patch.object(_draft, "_open_url", fail_upload):
                _, err = self.capture_exit(lambda: self.deposit(problem=problem))
            self.assertIn("Delete button", err)
            self.assertNotIn("finish this draft", err)
            self.assertNotIn("publish it there", err)
            self.assertNotIn("link-problem-paper", err)

    def test_plain_uncertain_publish_names_published_and_draft_recovery(self):
        fake = _FakeZenodoApi()
        def fail_publish(request):
            if request.full_url.endswith("/actions/publish"):
                raise OSError("lost")
            return fake(request)
        with patch.object(_draft, "_open_url", fail_publish):
            _, err = self.capture_exit(self.deposit)
        for text in ("If it is published", "do not run this command again", "--new-version", "records its DOI",
                     "If it is still a draft", "Delete button"):
            self.assertIn(text, err)

    def test_a_resumed_draft_upload_failure_names_the_read_back_on_the_next_run(self):
        self.write_prior()
        def answer(request):
            if request.get_method() == "GET":
                return {"id": 4242, "submitted": False,
                        "links": {"bucket": _BASE + "/files/bucket-1", "html": _DRAFT_URL}}
            raise OSError("upload failed")
        with patch.object(_draft, "_open_url", answer):
            _, err = self.capture_exit(lambda: self.deposit(new_version=True))
        self.assertIn("reads deposition 4242 back first", err)
        self.assertIn("never publishes a second record", err)

    def test_an_unpublished_local_copy_does_not_claim_a_second_permanent_record(self):
        state = self.write_prior()
        _, err = self.capture_exit(lambda: _draft._stop_with_only_the_local_deposit_copy(Path.cwd(), state))
        self.assertIn("A second plain run creates another draft", err)
        self.assertNotIn("second permanent record", err)

    def test_only_4xx_errors_are_refusals_in_both_commands(self):
        for status in (300, 304, 399, 400, 499, 500, 599, 600, 999):
            for method in ("POST", "GET"):
                with self.subTest(status=status, method=method):
                    def error():
                        return urllib.error.HTTPError(_BASE, status, "error", {}, io.BytesIO(b"detail"))
                    with patch.object(_cli, "open_url_within_origin", side_effect=error()):
                        _, err = self.capture_exit(lambda: _cli._send_request(method, "/test"))
                    self.assertEqual("may have applied" in err, method == "POST" and not 400 <= status <= 499)
                    with patch.object(_draft, "_open_url", side_effect=error()):
                        _, err = self.capture_exit(lambda: _draft._call_zenodo(method, _BASE + "/test", "test-token",
                            environment="sandbox", failure_advice=_draft._FailureAdvice(uncertain_msg="uncertain", refused_msg="refusal")))
                    self.assertIn("refusal" if 400 <= status <= 499 else "uncertain", err)

    def test_invalid_zenodo_answer_address_names_the_cause_without_network_advice(self):
        _, err = self.capture_exit(lambda: _draft._call_zenodo("PUT", _BASE + "/files/bad path/paper.pdf",
            "test-token", file_bytes=b"pdf", environment="sandbox"))
        self.assertIn("address from a Zenodo answer", err)
        self.assertIn("control characters", err)
        self.assertNotIn("network", err)

    def test_a_non_refusal_status_keeps_uncertainty_even_with_a_known_error_body(self):
        for status in (300, 503, 600):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(_BASE, status, "error", {}, io.BytesIO(
                    b'{"error":"verification_superseded","successorId":"next"}'))
                with patch.object(_cli, "open_url_within_origin", side_effect=error):
                    _, err = self.capture_exit(lambda: _cli._send_request("POST", "/test"))
                self.assertIn("may have applied", err)

    def test_publish_request_advice_set_has_an_explicit_name(self):
        advice = _draft._build_publish_request_advice_set(4242, _DRAFT_URL, None, is_new_version=False)
        self.assertIsInstance(advice, _draft._FailureAdvice)

    def test_unwritten_journal_and_unrecorded_store_say_what_the_next_run_does(self):
        fake = _FakeZenodoApi()
        real_projection = _draft.json_projection
        def fail_journal(root, path, state):
            if path == ".exactory/problem-deposit.json":
                raise OSError("disk full")
            return real_projection(root, path, state)
        with patch.object(_draft, "_open_url", fake), patch.object(_draft, "json_projection", fail_journal):
            _, err = self.capture_exit(lambda: self.deposit(problem={"problem_id": "p"}))
        self.assertIn("A second run creates another draft", err)
        self.assertIn("Delete button", err)
        _, err = self.capture_exit(lambda: _draft._stop_with_only_the_local_deposit_copy(Path.cwd(),
            self.write_prior(doi="10.5072/zenodo.4242")))
        self.assertIn("A second plain run publishes a second permanent record", err)
        self.assertIn("--new-version", err)

    def test_a_deposit_projection_that_cannot_be_written_names_next_run_and_record(self):
        state = self.write_prior(doi="10.5072/zenodo.4242")
        for call in (lambda: _draft._save_deposit_state(None, None, state),
                     lambda: _draft._stop_with_only_the_local_deposit_copy(Path.cwd(), state),
                     lambda: _draft._record_pending_deposit(None, state)):
            with self.subTest(call=call):
                with patch.object(_draft, "json_projection", side_effect=OSError("disk full")):
                    _, err = self.capture_exit(call)
                self.assertIn("4242", err)
                self.assertIn("A second plain run", err)
                self.assertIn("--new-version", err)

    def test_unwritten_managed_journal_says_what_a_second_run_does(self):
        report = {"bundle": {"files": {"pdf": {"artifact": {}}}, "digest": "bundle", "prepared_revision": 0},
                  "revision": 0}
        args = argparse.Namespace(new_version=False, publish=True, expected_revision=None)
        from unittest.mock import MagicMock
        with patch.object(_draft, "command_identity", return_value={}), \
                patch.object(_draft, "begin_deposit", return_value={"id": "intent-1"}), \
                patch("research_harness.publication_scope.build_publication_scope_binding", return_value=None), \
                patch("research_harness.publication_scope.validate_caller_scope"), \
                patch.object(_draft, "json_projection", side_effect=OSError("disk full")):
            _, err = self.capture_exit(lambda: _draft._deposit_managed(MagicMock(), report, args, _BASE, "sandbox", {},
                                                                      None, "test-token", {"problem_id": "p"}))
        self.assertIn("A second run", err)
        self.assertIn("recovery record", err)

    def test_store_record_failures_distinguish_a_refusal_from_a_failed_export(self):
        store = Store(Path.cwd(), create=True)
        state = self.write_prior(doi="10.5072/zenodo.4242")
        for after_commit in (False, True):
            for error in (OSError("disk full"), _draft.ResearchError("storage_io", "export failed")):
                with self.subTest(after_commit=after_commit, error=type(error).__name__):
                    manager = (patch("research_harness.integration.json_projection", side_effect=error)
                               if after_commit else patch.object(_draft, "record_direct_deposit", side_effect=error))
                    with manager:
                        _, err = self.capture_exit(lambda: _draft._save_deposit_state(store, None, state))
                    self.assertIn("A second plain run", err)
                    self.assertIn("--new-version", err)
                    self.assertEqual(json.loads(Path(".exactory/deposit.json").read_text()), state)
                    if after_commit:
                        self.assertEqual(store.snapshot()["records"]["workspace"]["deposit"], state)
                        self.assertNotIn("store did not record", err)
                        self.assertNotIn("only local copy", err)
        with patch.object(_draft, "record_direct_deposit", side_effect=OSError("disk full")), \
                patch.object(store, "committed_request", side_effect=_draft.ResearchError("storage_io", "unreadable")):
            _, err = self.capture_exit(lambda: _draft._save_deposit_state(store, None, state))
        self.assertNotIn("store did not record", err)
        self.assertNotIn("only local copy", err)
        self.assertIn("A second plain run", err)

    def test_pending_store_io_failure_keeps_the_file_before_publication(self):
        store = Store(Path.cwd(), create=True)
        state = self.write_prior()
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                manager = (patch("research_harness.integration.json_projection", side_effect=OSError("disk full"))
                           if after_commit else patch.object(_draft, "record_direct_deposit", side_effect=OSError("disk full")))
                with manager:
                    _draft._record_pending_deposit(store, state)
                self.assertEqual(json.loads(Path(".exactory/deposit.json").read_text()), state)


class TestConfiguration(_OfflineTestCase):
    def test_origin_is_read_and_checked_once_at_command_start(self):
        with patch("research_harness.exactory_api.read_api_base_url", return_value="http://api.test") as read:
            module = _load_bin_module("exactory", "f6_boot")
            class Response(io.BytesIO):
                def getcode(self):
                    return 200
            with patch.object(module, "open_url_within_origin", side_effect=lambda *a, **kw: Response(b'{}')):
                module._send_request("GET", "/first")
                module._send_request("GET", "/second")
                module._build_code_check_advice("a@test.local", "label")
            self.assertEqual(read.call_count, 1)

    def test_default_origin_in_address_advice_comes_from_shared_constant(self):
        with patch("research_harness.exactory_api._DEFAULT_API_URL", "https://default.test"):
            module = _load_bin_module("exactory", "f6_default")
        self.assertIn("for example https://default.test", module._UNUSABLE_API_URL_ERR_MSG)
        self.assertIn("unset EXACTORY_API_URL to use https://default.test", module._UNUSABLE_API_URL_ERR_MSG)

    def test_logout_names_keys_page_of_configured_origin(self):
        with patch.object(_cli, "_credentials_path", return_value=Path("credentials.json")):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                _cli._run_logout(argparse.Namespace())
        self.assertIn("http://api.test/keys", err.getvalue())
        self.assertNotIn("https://www.exactory.ai/keys", err.getvalue())
