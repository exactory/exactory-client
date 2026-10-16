"""Tests for bin/exactory: identifier mapping, path encoding, the citation report,
and the grand-challenge subcommands."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.machinery
import importlib.util
import io
import json
import os
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import unittest
from pathlib import Path

from integration_fixtures import build_unconfirmed_write_msg
from research_harness.same_origin import RefusedRedirectError

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


def _invoke_cli(test_case: unittest.TestCase, argv: list[str],
                expected_exit_code: int | None = None) -> tuple[str, str]:
    """Parse argv with the real parser and run its handler, capturing both streams."""
    args = _transport._build_parser().parse_args(argv)
    def invoke():
        # Each invocation stands for a process, whose configuration is checked once at boot.
        _transport._API_BASE_URL = _transport._read_checked_api_base_url()
        args.handler(args)
    stdout_sink, stderr_sink = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout_sink), contextlib.redirect_stderr(stderr_sink):
        if expected_exit_code is None:
            invoke()
        else:
            with test_case.assertRaises(SystemExit) as caught:
                invoke()
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
        self.responses: dict[tuple[str, str], tuple[dict, int]] = {}
        self.addCleanup(setattr, _transport, "_send_request", _transport._send_request)

        def _record_request(method: str, path: str, body: dict | None = None, *, allow_missing: bool = False,
                            error_messages: dict[int, str] | None = None) -> tuple[dict, int]:
            self.requested_paths.append(path)
            self.requested_methods.append(method)
            self.request_bodies.append(body)
            return self.responses.get((method, path), ({}, self.response_status))

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


class _BodyThatBreaksOff(io.BytesIO):
    """A body that does not arrive: reading it raises `error`, as http.client raises it for a dropped connection."""

    def __init__(self, error: Exception) -> None:
        super().__init__(b"")
        self.error = error

    def read(self, *args) -> bytes:
        raise self.error


class _AnswerThatBreaksOff(_BodyThatBreaksOff):
    """An answer whose status arrived and whose body does not."""

    def __init__(self, error: Exception, status: int) -> None:
        super().__init__(error)
        self._status = status

    def getcode(self) -> int:
        return self._status


class _OpenUrlTransportTestCase(unittest.TestCase):
    """Shared plumbing for the grand-challenge subcommands: a scratch cwd and a
    stubbed open_url_within_origin, the one function that bin/exactory sends
    every request through, so the method, the full URL, the JSON body, and the
    Authorization header are all asserted on the real request object."""

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
        _transport._API_BASE_URL = _transport._read_checked_api_base_url()

        self.sent_requests: list[urllib.request.Request] = []
        self.response_status = 200
        self.response_body = b"{}"
        self.addCleanup(setattr, _transport, "open_url_within_origin", _transport.open_url_within_origin)

        def _fake_open_url(request: urllib.request.Request,
                           timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            return _FakeResponse(self.response_body, self.response_status)

        _transport.open_url_within_origin = _fake_open_url

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
            ["/api/v1/verdicts/656c336e-e892-4c47-80d2-a71d022f4116/vote"],
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
    def test_task_url_keeps_the_slash_a_doi_carries(self) -> None:
        self._run(["task", "10.5281/zenodo.21332924"])
        self.assertEqual(self.requested_paths, ["/api/v1/tasks/10.5281/zenodo.21332924"])

    def test_task_url_encodes_every_character_but_the_slash(self) -> None:
        self._run(["task", "10.5281/zenodo?limit=1"])
        self.assertEqual(
            self.requested_paths, ["/api/v1/tasks/10.5281/zenodo%3Flimit%3D1"]
        )

    def test_task_refuses_a_dot_segment_before_a_request_goes_out(self) -> None:
        _, stderr_text = self._run(["task", "../verifications"], expected_exit_code=1)
        self.assertIn("is not an identifier", stderr_text)
        self.assertEqual(self.requested_paths, [])

    def test_paper_refuses_a_dot_segment_before_a_request_goes_out(self) -> None:
        _, stderr_text = self._run(["paper", "../verifications"], expected_exit_code=1)
        self.assertIn("is not an identifier", stderr_text)
        self.assertEqual(self.requested_paths, [])

    def test_status_url_encodes_the_verification_id(self) -> None:
        self._run(["status", "../tasks?limit=1"])
        self.assertEqual(
            self.requested_paths, ["/api/v1/verifications/..%2Ftasks%3Flimit%3D1"]
        )

    def test_vote_url_encodes_the_verdict_id(self) -> None:
        self._run(["vote", "../tasks?x=1", "--value", "1"])
        self.assertEqual(
            self.requested_paths, ["/api/v1/verdicts/..%2Ftasks%3Fx%3D1/vote"]
        )

    def test_a_command_with_an_id_in_its_path_refuses_an_empty_or_dot_id_before_any_request(self) -> None:
        for unroutable_id in ("", ".", ".."):
            for argv in (["status", unroutable_id], ["vote", unroutable_id, "--value", "1"],
                         ["challenge", unroutable_id], ["vote-challenge", unroutable_id, "--value", "0"],
                         ["solve-challenge", unroutable_id, "--reopen"]):
                with self.subTest(argv=argv):
                    _, stderr_text = self._run(argv, expected_exit_code=1)
                    self.assertIn("is not an identifier", stderr_text)
        self.assertEqual(self.requested_paths, [])


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


class TestSubmitSupersession(_TransportTestCase):
    def test_a_submission_that_replaced_a_verification_names_it_on_stderr(self) -> None:
        self.responses[("POST", "/api/v1/verifications")] = (
            {"id": "new-id", "status": "open", "supersedes": "old-id", "supersededBy": None}, 201)
        stdout_text, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.2"])
        self.assertIn("replaced verification old-id", stderr_text)
        self.assertEqual(json.loads(stdout_text)["supersedes"], "old-id")


class TestSupersededVerification(_OpenUrlTransportTestCase):
    def test_a_superseded_task_names_the_successor_and_not_the_status_command(self) -> None:
        body = json.dumps({"error": "verification_superseded", "successorId": "successor-id"}).encode()

        def _refuse(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            raise urllib.error.HTTPError(request.full_url, 409, "Conflict", None, io.BytesIO(body))

        _transport.open_url_within_origin = _refuse
        _, stderr_text = self._run(["task", "earlier-id"], expected_exit_code=1)
        self.assertIn("moved this verification to a newer version", stderr_text)
        self.assertIn("successor-id", stderr_text)
        self.assertNotIn("exactory status", stderr_text)


class TestSubmitDecision(_TransportTestCase):
    def setUp(self) -> None:
        super().setUp()
        _build_draft_workspace(self.scratch_dir)

    def test_submit_in_a_legacy_workspace_prints_the_report_and_posts(self) -> None:
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertTrue(stderr_text.startswith("Citation report: "))
        self.assertIn("references.bib", stderr_text)
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])
        self.assertEqual(self.request_bodies, [{"doi": "10.5281/zenodo.1"}])

    def test_submit_from_a_workspace_subdirectory_still_prints_the_report(self) -> None:
        os.chdir(self.scratch_dir / "draft")
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertTrue(stderr_text.startswith("Citation report: "))
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])

    def test_submit_outside_a_workspace_prints_nothing_and_posts(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        os.chdir(outside.name)
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(stderr_text, "")
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])

    def test_submit_in_an_unready_study_notes_the_skip_and_posts(self) -> None:
        from integration_fixtures import prepare_research

        prepare_research(self.scratch_dir, candidate=True)
        _write_passing_citation_report(self.scratch_dir)
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertTrue(stderr_text.startswith("Managed record skipped (readiness_required): "))
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])
        from research_harness.storage import Store
        self.assertNotIn("submission_receipt", Store(self.scratch_dir).snapshot()["records"])

    def _publish_the_reviewed_bundle(self) -> None:
        """Publish the reviewed manuscript on Zenodo, as the managed submit requires,
        and answer the submit POST and the task GET that the managed path then reads."""
        from integration_fixtures import prepare_manuscript, prepare_research
        from research_harness.zenodo import deposit

        case = prepare_research(self.scratch_dir, candidate=True)
        _write_passing_citation_report(self.scratch_dir)
        (self.scratch_dir / "draft/paper.pdf").write_bytes(b"%PDF-1.4\n% Authored finite-result fixture.\n%%EOF")
        (self.scratch_dir / "draft/abstract.txt").write_text("The exact finite bound is 9.")
        bundle = prepare_manuscript(case, stop=True)
        binding = {"bundle_digest": bundle["digest"], "prepared_revision": bundle["prepared_revision"],
            "base_url": "https://zenodo.org/api", "environment": "production", "new_version": False,
            "publish": True, "metadata": {"title": "Authored finite result", "description": "The finite bound is 9."},
            "uploads": [{"name": "paper.pdf", "artifact": bundle["files"]["pdf"]["artifact"]}], "prior": None}

        def publisher(method, url, **kwargs):
            if method == "POST" and url.endswith("/deposit/depositions"):
                return {"id": 1, "links": {"bucket": "https://zenodo.org/api/files/fixture"}}
            if url.endswith("/actions/publish"):
                return {"id": 1, "doi": "10.5281/zenodo.1", "conceptdoi": "10.5281/zenodo.0",
                        "links": {"record_html": "https://zenodo.org/records/1"}}
            return {}

        deposit(case.store, binding, publisher,
                expected_revision=case.store.revision, request_id="transport-publication")
        self.responses[("POST", "/api/v1/verifications")] = ({"verificationId": _VERIFICATION_ID}, 201)
        self.responses[("GET", "/api/v1/tasks/" + _VERIFICATION_ID)] = ({"verificationId": _VERIFICATION_ID,
            "doi": "10.5281/zenodo.0", "source": "zenodo", "sourceId": "1", "sourceVersion": None,
            "url": "https://zenodo.org/records/1"}, 200)

    def test_submit_in_a_ready_study_writes_the_receipt(self) -> None:
        self._publish_the_reviewed_bundle()
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(stderr_text, "")
        self.assertEqual(self.requested_paths, ["/api/v1/verifications", "/api/v1/tasks/" + _VERIFICATION_ID])
        self.assertEqual(self.request_bodies, [{"doi": "10.5281/zenodo.1"}, None])
        from research_harness.storage import Store
        receipts = Store(self.scratch_dir).snapshot()["records"]["submission_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertTrue(next(iter(receipts.values()))["matched"])

    def test_submit_after_an_unknown_submission_outcome_notes_the_skip_and_posts(self) -> None:
        self._publish_the_reviewed_bundle()
        record_request = _transport._send_request

        def lose_the_submission_response(method: str, path: str, body: dict | None = None,
                                         *, allow_missing: bool = False) -> tuple[dict, int]:
            response = record_request(method, path, body, allow_missing=allow_missing)
            if method == "POST":
                _transport._exit_with_error("The client cannot reach the server.")
            return response

        _transport._send_request = lose_the_submission_response
        self._run(["submit", "--doi", "10.5281/zenodo.1"], expected_exit_code=1)
        _transport._send_request = record_request
        self.requested_paths.clear()
        self.request_bodies.clear()
        # The concept DOI is the locator a stranded intent reads instead of the
        # lost verification id, and the server has no task under it.
        self.responses[("GET", "/api/v1/tasks/10.5281/zenodo.0")] = ({}, 404)

        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(stderr_text, "Managed record skipped (remote_reconciliation_required):"
                                      " A prior submission POST has an unknown outcome. Reconcile that intent"
                                      " before this study records another submission\n")
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])
        self.assertEqual(self.request_bodies, [{"doi": "10.5281/zenodo.1"}])
        from research_harness.storage import Store
        records = Store(self.scratch_dir).snapshot()["records"]
        self.assertEqual([intent["status"] for intent in records["remote_intent"].values()
                          if intent["kind"] == "submit"], ["in_flight"])
        self.assertNotIn("submission_receipt", records)


class TestSubmitInAStudyWorkspaceWithNoDraftMarker(_TransportTestCase):
    """A study workspace that never ran `exactory-draft init`: `.exactory/study.json`
    and no draft marker. The citation check reads the draft workspace's references
    file, so this workspace runs no check and the submit says nothing."""

    def setUp(self) -> None:
        super().setUp()
        (self.scratch_dir / ".exactory").mkdir()
        (self.scratch_dir / ".exactory" / "study.json").write_text(json.dumps({
            "version": 1, "slug": "cohort-percentiles", "stage": "submit",
            "status": "running",
        }))

    def test_submit_prints_nothing_and_posts(self) -> None:
        _, stderr_text = self._run(["submit", "--doi", "10.5281/zenodo.1"])
        self.assertEqual(stderr_text, "")
        self.assertEqual(self.requested_paths, ["/api/v1/verifications"])
        self.assertEqual(self.request_bodies, [{"doi": "10.5281/zenodo.1"}])


_VERIFICATION_ID = "0e5c2b1a-9d4f-4c3b-8a7e-6f5d4c3b2a19"


def _write_verdict_file(path: Path, **overrides: object) -> None:
    """Write a complete verdict file, then apply overrides (a None value drops the key)."""
    verdict: dict = {
        "stance": "sound",
        "summary": "The claims follow from the evidence.",
        "prediction": {
            "corpus": "arxiv", "category": "cs.LG",
            "windowStart": "2026-01-01", "windowEnd": "2026-06-30",
            "percentile": 15, "band": {"best": 8, "worst": 30},
        },
    }
    for key, value in overrides.items():
        if value is None:
            verdict.pop(key, None)
        else:
            verdict[key] = value
    path.write_text(json.dumps(verdict))


class TestVerify(_TransportTestCase):
    def _run_verify(self, expected_exit_code: int | None = None) -> tuple[str, str]:
        return self._run(["verify", _VERIFICATION_ID, "--file", "verdict.json"],
                         expected_exit_code=expected_exit_code)

    def test_verify_refuses_a_verdict_without_a_prediction(self) -> None:
        _write_verdict_file(self.scratch_dir / "verdict.json", prediction=None)
        _, stderr_text = self._run_verify(expected_exit_code=1)
        self.assertIn("prediction", stderr_text)
        self.assertEqual(self.requested_paths, [])

    def test_verify_refuses_a_null_prediction(self) -> None:
        (self.scratch_dir / "verdict.json").write_text(
            json.dumps({"stance": "sound", "summary": "s", "prediction": None})
        )
        _, stderr_text = self._run_verify(expected_exit_code=1)
        self.assertIn("prediction", stderr_text)
        self.assertEqual(self.requested_paths, [])

    def test_verify_refuses_a_prediction_without_a_percentile(self) -> None:
        _write_verdict_file(
            self.scratch_dir / "verdict.json",
            prediction={"corpus": "arxiv", "category": "cs.LG",
                        "windowStart": "2026-01-01", "windowEnd": "2026-06-30"},
        )
        _, stderr_text = self._run_verify(expected_exit_code=1)
        self.assertIn("percentile", stderr_text)
        self.assertEqual(self.requested_paths, [])

    def _task(self) -> dict:
        return {"verificationId": _VERIFICATION_ID, "doi": "10.48550/arxiv.2601.00001",
                "source": "arxiv", "sourceId": "2601.00001", "sourceVersion": 1,
                "url": "https://arxiv.org/abs/2601.00001v1", "viewerVerdictId": None}

    def test_verify_without_a_workspace_posts_the_verdict(self) -> None:
        _write_verdict_file(self.scratch_dir / "verdict.json")
        self.responses[("GET", f"/api/v1/tasks/{_VERIFICATION_ID}")] = (self._task(), 200)
        self.responses[("POST", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts")] = (
            {"id": "22222222-2222-4222-8222-222222222222"}, 201)
        stdout_text, stderr_text = self._run_verify()
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        self.assertEqual(self.request_bodies[1], json.loads((self.scratch_dir / "verdict.json").read_text()))
        self.assertEqual(json.loads(stdout_text)["id"], "22222222-2222-4222-8222-222222222222")
        self.assertEqual(stderr_text, "")

    def _bind_the_verdict_in_a_verification_workspace(self) -> None:
        """Pin the task and bind an evidence-linked assessment of verdict.json, as the managed path requires."""
        from integration_fixtures import prepare_verification
        from reviewer_fixtures import verdict_assignment
        from research_harness.verification import bind_verdict, record_task

        case = prepare_verification(self.scratch_dir)
        _write_verdict_file(self.scratch_dir / "verdict.json")
        pinned = case.mutate(record_task, {"task": self._task()})["result"]
        payload = {"id": "transport-verdict", "task_digest": pinned["digest"],
            "body": case.artifacts.put((self.scratch_dir / "verdict.json").read_bytes(), "application/json"),
            "assessment": {"assessor": "transport-independent-verifier",
                "provenance": case.artifacts.put(b"Authored independent verification context.", "text/plain"),
                "independence_basis": "Separate context read the exact source and no other verdicts.", "blind": True,
                "checks": [{"dimension": dimension, "reason": "The exact scoped source supports this separate judgment.",
                            "evidence": [case.linked]} for dimension in ("soundness", "novelty", "impact")]}}
        payload["assessment"]["assignment_id"] = verdict_assignment(case, payload)
        case.mutate(bind_verdict, payload)
        self.responses[("GET", f"/api/v1/tasks/{_VERIFICATION_ID}")] = (self._task(), 200)
        self.responses[("POST", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts")] = (
            {"id": "22222222-2222-4222-8222-222222222222"}, 201)

    def test_verify_in_a_bound_verification_workspace_writes_the_receipt(self) -> None:
        self._bind_the_verdict_in_a_verification_workspace()
        _, stderr_text = self._run_verify()
        self.assertEqual(stderr_text, "")
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        self.assertEqual(self.request_bodies[1], json.loads((self.scratch_dir / "verdict.json").read_text()))
        from research_harness.storage import Store
        receipts = Store(self.scratch_dir).snapshot()["records"]["verdict_receipt"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(next(iter(receipts.values()))["response"]["id"], "22222222-2222-4222-8222-222222222222")

    def test_verify_in_an_unbound_workspace_notes_the_skip_and_posts(self) -> None:
        from integration_fixtures import prepare_verification

        prepare_verification(self.scratch_dir)
        _write_verdict_file(self.scratch_dir / "verdict.json")
        self.responses[("GET", f"/api/v1/tasks/{_VERIFICATION_ID}")] = (self._task(), 200)
        self.responses[("POST", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts")] = (
            {"id": "22222222-2222-4222-8222-222222222222"}, 201)
        _, stderr_text = self._run_verify()
        self.assertEqual(stderr_text, "Managed record skipped (verdict_assessment_required):"
                                      " Bind an evidence-linked assessment of this exact verdict body\n")
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        self.assertEqual(self.request_bodies[1], json.loads((self.scratch_dir / "verdict.json").read_text()))
        from research_harness.storage import Store
        self.assertNotIn("verdict_receipt", Store(self.scratch_dir).snapshot()["records"])

    def test_verify_with_a_body_that_differs_from_the_bound_assessment_notes_the_skip_and_posts(self) -> None:
        self._bind_the_verdict_in_a_verification_workspace()
        _write_verdict_file(self.scratch_dir / "verdict.json", summary="A different summary.")
        _, stderr_text = self._run_verify()
        self.assertEqual(stderr_text, "Managed record skipped (verdict_body_mismatch):"
                                      " The transmitted verdict body must equal the bound assessment, including revisions\n")
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        self.assertEqual(self.request_bodies[1], json.loads((self.scratch_dir / "verdict.json").read_text()))
        from research_harness.storage import Store
        self.assertNotIn("verdict_receipt", Store(self.scratch_dir).snapshot()["records"])

    def test_verify_after_an_unknown_verdict_outcome_notes_the_skip_and_posts(self) -> None:
        self._bind_the_verdict_in_a_verification_workspace()
        record_request = _transport._send_request

        def lose_the_verdict_response(method: str, path: str, body: dict | None = None,
                                      *, allow_missing: bool = False) -> tuple[dict, int]:
            response = record_request(method, path, body, allow_missing=allow_missing)
            if method == "POST":
                _transport._exit_with_error("The client cannot reach the server.")
            return response

        _transport._send_request = lose_the_verdict_response
        self._run_verify(expected_exit_code=1)
        _transport._send_request = record_request
        self.requested_paths.clear()
        self.request_bodies.clear()

        _, stderr_text = self._run_verify()
        self.assertEqual(stderr_text, "Managed record skipped (verdict_reconciliation_pending):"
                                      " The previous POST has an unknown outcome. The task exposes only your verdict ID,"
                                      " so the existing API cannot confirm the exact body without exposing other verdicts\n")
        self.assertEqual(self.requested_paths,
                         [f"/api/v1/tasks/{_VERIFICATION_ID}", f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"])
        self.assertEqual(self.request_bodies[1], json.loads((self.scratch_dir / "verdict.json").read_text()))
        from research_harness.storage import Store
        records = Store(self.scratch_dir).snapshot()["records"]
        self.assertEqual([intent["status"] for intent in records["remote_intent"].values()], ["in_flight"])
        self.assertEqual([observation["status"] for observation in records["remote_observation"].values()],
                         ["verdict_response_unknown"])
        self.assertNotIn("verdict_receipt", records)


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


class TestChallengesSubcommand(_OpenUrlTransportTestCase):
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


class TestChallengeSubcommand(_OpenUrlTransportTestCase):
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


class TestPostChallengeSubcommand(_OpenUrlTransportTestCase):
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

    def test_a_lone_surrogate_in_a_citation_is_refused_before_any_request(self) -> None:
        # json.load turns a "\ud800" escape into a lone surrogate. The server
        # builds the citation on plainTextSchema, which refuses an unpaired
        # surrogate, so the client refuses it first.
        self.citations_path.write_text(
            '[{"citation": "0123456789\\ud800", "locator": "10.1234/exact.5678"}]')
        _, stderr_text = self._run(self._post_argv(), expected_exit_code=1)
        self.assertEqual(stderr_text, "The citation of entry 1 contains U+D800 at position 11. A \\uD800 escape in the"
                                      " citations file gives it, and it is half of a surrogate pair. Write the"
                                      " character as UTF-8 text in the file, or write both escapes of the pair.\n")
        self.assertEqual(self.sent_requests, [])

    def test_a_lone_surrogate_in_the_title_counts_as_one_utf16_unit(self) -> None:
        # argv turns a byte that is not UTF-8 into a lone surrogate. The server
        # builds the title on z.string(), whose bounds count it as one UTF-16
        # unit, so the client counts it the same way instead of raising
        # UnicodeEncodeError, and sends a title of exactly eight units.
        self._run(self._post_argv(title="Seven c\udcff"))
        body = json.loads(self._single_request().data.decode())
        self.assertEqual(body["title"], "Seven c\udcff")

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


class TestVoteChallengeSubcommand(_OpenUrlTransportTestCase):
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


class TestSolveChallengeSubcommand(_OpenUrlTransportTestCase):
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


class TestReportChallengeSubcommand(_OpenUrlTransportTestCase):
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

    def test_the_notice_for_an_existing_report_names_the_challenge(self) -> None:
        self.response_status = 200
        _, stderr_text = self._run(["report-challenge", "chal-1"])
        self.assertEqual(stderr_text, "The server returned the existing report for this challenge.\n")


class TestAnswersThatDidNotArriveInFull(_OpenUrlTransportTestCase):
    """The request went out, and then its answer did not arrive in full: a timeout or a dropped connection while the
    command waits for the answer or reads it, or a 2xx answer whose body is not JSON. The command ends with one message
    that names the origin and the cause, and exits with code 1. A read changed nothing on the server, so the user runs
    it again. A write may have been applied, so the user checks on the origin first."""

    # A write and a read, each with the advice that its message ends with.
    _REQUESTS = (("POST", ["report-challenge", "chal-1"], build_unconfirmed_write_msg("https://api.test")),
                 ("GET", ["challenge", "chal-1"], "Run the command again."))

    @staticmethod
    def _build_errors() -> tuple:
        """What http.client raises for an answer that does not arrive in full. On Python 3.10 and later
        socket.timeout is TimeoutError; on 3.9 they are two classes."""
        return (socket.timeout("timed out"), TimeoutError("timed out"),
                ConnectionResetError(54, "Connection reset by peer"),
                http.client.RemoteDisconnected("Remote end closed connection without response"),
                http.client.IncompleteRead(b'{"id"', 20))

    @staticmethod
    def _build_message(method: str, cause: str, advice: str) -> str:
        return (f"The client cannot read the answer of the exactory server at https://api.test to the {method}"
                f" request. Cause: {cause}. {advice}\n")

    def test_an_answer_that_breaks_off_while_its_body_is_read_ends_with_a_message(self) -> None:
        for method, argv, advice in self._REQUESTS:
            for error in self._build_errors():
                with self.subTest(method=method, error=type(error).__name__):
                    def answer_then_break_off(request: urllib.request.Request, timeout: float | None = None,
                                              error: Exception = error) -> _AnswerThatBreaksOff:
                        self.sent_requests.append(request)
                        return _AnswerThatBreaksOff(error, 200)
                    _transport.open_url_within_origin = answer_then_break_off

                    stdout_text, stderr_text = self._run(argv, expected_exit_code=1)

                    self.assertEqual(stderr_text, self._build_message(method, str(error), advice))
                    self.assertEqual(stdout_text, "")

    def test_an_answer_that_does_not_come_while_the_command_waits_ends_with_a_message(self) -> None:
        for method, argv, advice in self._REQUESTS:
            for error in self._build_errors():
                with self.subTest(method=method, error=type(error).__name__):
                    def break_off_before_the_status(request: urllib.request.Request, timeout: float | None = None,
                                                    error: Exception = error) -> _FakeResponse:
                        self.sent_requests.append(request)
                        raise error
                    _transport.open_url_within_origin = break_off_before_the_status

                    _, stderr_text = self._run(argv, expected_exit_code=1)

                    self.assertEqual(stderr_text, self._build_message(method, str(error), advice))

    def test_a_2xx_answer_whose_body_is_not_json_ends_with_a_message(self) -> None:
        for method, argv, advice in self._REQUESTS:
            for status in (200, 201):
                with self.subTest(method=method, status=status):
                    self.response_status, self.response_body = status, b"not json"

                    _, stderr_text = self._run(argv, expected_exit_code=1)

                    self.assertEqual(stderr_text, self._build_message(
                        method, f"the answer has HTTP {status} and a body that is not JSON", advice))

    def answer_with_an_error_whose_body_breaks_off(self, status: int) -> tuple[urllib.error.HTTPError, Exception]:
        """Make every request end with an error answer of `status` whose body breaks off, and return the answer and
        the error that the reading of its body raises."""
        broken_off = http.client.IncompleteRead(b'{"err', 30)
        http_error = urllib.error.HTTPError("https://api.test", status, "error", {}, _BodyThatBreaksOff(broken_off))
        self.addCleanup(http_error.close)

        def answer_with_the_error(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            raise http_error
        _transport.open_url_within_origin = answer_with_the_error
        return http_error, broken_off

    def test_a_server_error_whose_body_breaks_off_keeps_its_message_and_says_that_the_detail_is_missing(self) -> None:
        (post_method, post_argv, write_advice), (get_method, get_argv, _) = self._REQUESTS
        for method, argv, advice in ((post_method, post_argv, write_advice),
                                     (get_method, get_argv, "Send the same request again later.")):
            with self.subTest(method=method):
                http_error, broken_off = self.answer_with_an_error_whose_body_breaks_off(503)

                _, stderr_text = self._run(argv, expected_exit_code=1)

                self.assertEqual(stderr_text, f"The paper source is not reachable now. {advice}\nThe detail of the"
                                              f" answer did not arrive in full. Cause: {broken_off}.\n")
                self.assertTrue(http_error.closed)

    def test_a_refusal_whose_body_breaks_off_keeps_its_own_advice(self) -> None:
        # A 4xx is the server's refusal of the request, which it gives before it writes.
        for method, argv, _ in self._REQUESTS:
            with self.subTest(method=method):
                http_error, broken_off = self.answer_with_an_error_whose_body_breaks_off(409)

                _, stderr_text = self._run(argv, expected_exit_code=1)

                self.assertEqual(stderr_text, "The resource is in a state that does not accept this operation. Run"
                                              " exactory status <verification-id> to see the current state.\nThe"
                                              f" detail of the answer did not arrive in full. Cause: {broken_off}.\n")
                self.assertTrue(http_error.closed)

    def test_a_failure_before_the_request_went_out_keeps_its_message(self) -> None:
        # Connection refusal and InvalidURL both prove that no request byte was sent.
        for cause, error in (("[Errno 61] Connection refused",
                              urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))),
                             ("nonnumeric port: 'abc'", http.client.InvalidURL("nonnumeric port: 'abc'"))):
            def refuse_the_request(request: urllib.request.Request, timeout: float | None = None,
                                   error: Exception = error) -> _FakeResponse:
                raise error
            _transport.open_url_within_origin = refuse_the_request
            for method, argv, _ in self._REQUESTS:
                with self.subTest(method=method, error=type(error).__name__):
                    _, stderr_text = self._run(argv, expected_exit_code=1)

                    self.assertEqual(stderr_text, f"The client cannot reach https://api.test. Cause: {cause}. Make sure"
                                                  " that the network and EXACTORY_API_URL are correct. Then send the"
                                                  " request again.\n")


# What the command says about an EXACTORY_API_URL that it cannot use as the address of the API.
_UNUSABLE_API_URL_ERR_MSG = ("EXACTORY_API_URL is not an address that the client can use. Set it to an http or https"
                             " URL with a host, for example https://www.exactory.ai. Write a port, if any, as a number."
                             " Leave out a user name, a query, a fragment, spaces, control characters, and characters"
                             " outside ASCII. Or unset EXACTORY_API_URL to use https://www.exactory.ai.")


class TestApiBaseUrlCheck(_OpenUrlTransportTestCase):
    """EXACTORY_API_URL is configuration, so the command checks it where it reads it (CLAUDE.md §2). A value that
    urllib or http.client would refuse, or would read as another address, ends the command before any request."""

    _UNUSABLE_URLS = ("http://127.0.0.1:abc", "https://api.test:99999", "http://127.0.0.1/a b", "https://api.test/x\n",
                      "http://api.test/" + chr(0x07), "https://ex" + chr(0xE4) + "mple.test", "ftp://api.test",
                      "file:///etc", "localhost:3000", "https://", "http://user:secret@api.test", "http://user@api.test",
                      "http://[::1", "", "https://api.test?x=1", "https://api.test/?", "https://api.test#top")

    def test_an_address_that_the_client_cannot_use_ends_before_any_request(self) -> None:
        opened_urls: list[str] = []
        self.addCleanup(setattr, _transport.webbrowser, "open", _transport.webbrowser.open)
        _transport.webbrowser.open = lambda url: opened_urls.append(url) or True
        for value in self._UNUSABLE_URLS:
            os.environ["EXACTORY_API_URL"] = value
            for argv in (["report-challenge", "chal-1"], ["challenge", "chal-1"], ["open-signup"],
                         ["login", "--email", "a@test.local"]):
                with self.subTest(value=value, command=argv[0]):
                    stdout_text, stderr_text = self._run(argv, expected_exit_code=2)

                    self.assertEqual(stderr_text, _UNUSABLE_API_URL_ERR_MSG + "\n")
                    self.assertEqual(stdout_text, "")
        self.assertEqual((self.sent_requests, opened_urls), ([], []))

    def test_a_usable_address_is_used_as_given(self) -> None:
        for value, address in (("https://api.test", "https://api.test"), ("https://api.test/", "https://api.test"),
                               ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
                               ("https://api.test/prefix", "https://api.test/prefix"),
                               ("http://[::1]:9000", "http://[::1]:9000"), ("HTTPS://API.test", "HTTPS://API.test")):
            with self.subTest(value=value):
                os.environ["EXACTORY_API_URL"] = value
                self.sent_requests.clear()

                self._run(["challenge", "chal-1"])

                self.assertEqual(self._single_request().full_url, f"{address}/api/v1/grand-challenges/chal-1")


class TestListQueryRefusals(_OpenUrlTransportTestCase):
    """A read sends no body, so a 422 to a list is the server's refusal of its query, and the message names the parts
    of the query that the list sends. The server judges every limit, so the message states no bound."""

    def test_the_task_and_challenge_lists_name_the_parts_of_their_queries(self) -> None:
        def refuse_the_query(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            raise urllib.error.HTTPError(request.full_url, 422, "refused", {}, io.BytesIO(b'{"error": "invalid_request"}'))
        _transport.open_url_within_origin = refuse_the_query
        for argv, query_parts in ((["tasks", "--limit", "500"], "the search text, a filter, the sort, the limit, or the"
                                                                 " cursor"),
                                  (["challenges", "--limit", "500"], "a filter, the sort, the limit, or the cursor")):
            with self.subTest(command=argv[0]):
                _, stderr_text = self._run(argv, expected_exit_code=1)

                self.assertEqual(stderr_text, f"The server rejected the query ({query_parts}). Correct it. Then send the"
                                              ' request again.\n{"error": "invalid_request"}\n')


class TestSubmitChallengeDeclaration(_OpenUrlTransportTestCase):
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


class TestCredentials(unittest.TestCase):
    """The key comes from the environment first, then from the credentials file."""

    def setUp(self) -> None:
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        self.config_home = Path(config_home.name)
        saved_env = {key: os.environ.get(key)
                     for key in ("EXACTORY_API_KEY", "XDG_CONFIG_HOME")}

        def _restore_env() -> None:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(_restore_env)
        os.environ.pop("EXACTORY_API_KEY", None)
        os.environ["XDG_CONFIG_HOME"] = str(self.config_home)

    def test_the_environment_variable_wins(self) -> None:
        _transport._save_api_key("file-key", "a@test.local", "laptop")
        os.environ["EXACTORY_API_KEY"] = "env-key"
        self.assertEqual(_transport._read_api_key(), "env-key")

    def test_the_file_is_read_when_the_variable_is_unset(self) -> None:
        _transport._save_api_key("file-key", "a@test.local", "laptop")
        self.assertEqual(_transport._read_api_key(), "file-key")

    def test_the_file_is_private_to_the_user(self) -> None:
        path = _transport._save_api_key("file-key", "a@test.local", "laptop")
        self.assertEqual(path, self.config_home / "exactory" / "credentials.json")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text()),
                         {"api_key": "file-key", "email": "a@test.local", "label": "laptop"})

    def test_a_second_save_replaces_the_first(self) -> None:
        _transport._save_api_key("first", "a@test.local", "laptop")
        _transport._save_api_key("second", "a@test.local", "desk")
        self.assertEqual(_transport._read_api_key(), "second")

    def test_no_key_anywhere_reads_as_empty(self) -> None:
        self.assertEqual(_transport._read_api_key(), "")

    def test_a_damaged_file_reads_as_empty(self) -> None:
        path = self.config_home / "exactory" / "credentials.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        self.assertEqual(_transport._read_api_key(), "")


class TestLoginSubcommand(_OpenUrlTransportTestCase):
    """`login` is the one path that sends no key: the emailed code is the credential."""

    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("EXACTORY_API_KEY", None)
        saved_config_home = os.environ.get("XDG_CONFIG_HOME")
        self.addCleanup(
            lambda: os.environ.__setitem__("XDG_CONFIG_HOME", saved_config_home)
            if saved_config_home is not None else os.environ.pop("XDG_CONFIG_HOME", None))
        os.environ["XDG_CONFIG_HOME"] = str(self.scratch_dir)

    def _fail_next_request(self, status: int, body: dict) -> urllib.error.HTTPError:
        error = urllib.error.HTTPError("https://api.test", status, "refused", {},
                                       io.BytesIO(json.dumps(body).encode()))
        self.addCleanup(error.close)

        def _raise(request: urllib.request.Request,
                   timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            error.url = request.full_url
            raise error
        _transport.open_url_within_origin = _raise
        return error

    def test_without_a_code_it_requests_one_and_sends_no_key(self) -> None:
        self.response_status = 202
        self.response_body = b'{"email": "a@test.local"}'
        stdout, stderr = self._run(["login", "--email", "a@test.local"])
        request = self._single_request()
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.test/api/v1/email-codes")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(json.loads(request.data), {"email": "a@test.local"})
        self.assertEqual(json.loads(stdout), {"email": "a@test.local", "status": "code_sent"})
        self.assertIn("Terms of Service", stderr)
        self.assertIn("exactory login --email a@test.local --code", stderr)

    def test_with_a_code_it_saves_the_key_and_never_prints_it(self) -> None:
        self.response_status = 201
        self.response_body = json.dumps({
            "apiKey": "secret-key",
            "key": {"id": "k1", "label": "laptop", "createdAt": "2026-08-23T00:00:00Z",
                    "revokedAt": None},
        }).encode()
        stdout, stderr = self._run(["login", "--email", "a@test.local",
                                    "--code", "123456", "--label", "laptop"])
        request = self._single_request()
        self.assertEqual(request.full_url, "https://api.test/api/v1/api-keys")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(json.loads(request.data),
                         {"email": "a@test.local", "code": "123456", "label": "laptop"})
        self.assertNotIn("secret-key", stdout + stderr)
        self.assertEqual(json.loads(stdout)["label"], "laptop")
        self.assertEqual(json.loads(stdout)["saved_to"],
                         str(self.scratch_dir / "exactory" / "credentials.json"))
        self.assertEqual(_transport._read_api_key(), "secret-key")

    def test_the_label_defaults_to_the_host_name(self) -> None:
        self.response_status = 201
        self.response_body = json.dumps({
            "apiKey": "secret-key",
            "key": {"id": "k1", "label": "plugin on box", "createdAt": "x", "revokedAt": None},
        }).encode()
        self._run(["login", "--email", "a@test.local", "--code", "123456"])
        body = json.loads(self._single_request().data)
        self.assertTrue(body["label"].startswith("plugin on "))

    def test_a_wrong_code_names_the_login_command(self) -> None:
        error = self._fail_next_request(401, {"error": "invalid_code"})
        _, stderr = self._run(["login", "--email", "a@test.local", "--code", "000000"],
                              expected_exit_code=1)
        self.assertIn("exactory login --email", stderr)
        self.assertNotIn("EXACTORY_API_KEY", stderr)
        self.assertEqual(_transport._read_api_key(), "")
        self.assertTrue(error.closed)

    def test_a_rate_limit_says_to_wait(self) -> None:
        error = self._fail_next_request(429, {"error": "rate_limited"})
        _, stderr = self._run(["login", "--email", "a@test.local"], expected_exit_code=1)
        self.assertIn("Wait", stderr)
        self.assertTrue(error.closed)

    # What each login request says after an outcome that it cannot confirm. The code request may have sent the email,
    # and the code check may have made a key, whose plaintext the server shows once.
    _CODE_REQUEST_ADVICE = ("If the request worked, an email with a sign-in code is on its way to a@test.local. Then"
                            " run: exactory login --email a@test.local --code <the code>. If no email arrives within a"
                            " few minutes, request a new code: exactory login --email a@test.local")
    _CODE_CHECK_ADVICE = ('The server may have made a key labeled "laptop" for your account. The page'
                          " https://api.test/keys lists your keys, and its Revoke button revokes one. Run the command"
                          " again with the same code. If the server answers that the code is wrong, expired, or already"
                          " used, request a new code: exactory login --email a@test.local")
    _LOGIN_REQUESTS = ((["login", "--email", "a@test.local"], _CODE_REQUEST_ADVICE),
                       (["login", "--email", "a@test.local", "--code", "123456", "--label", "laptop"], _CODE_CHECK_ADVICE))

    def test_a_server_error_keeps_its_description_and_gives_the_advice_of_its_login_request(self) -> None:
        for argv, advice in self._LOGIN_REQUESTS:
            for status, description in ((503, "exactory cannot send or check codes now."),
                                        (500, "The server returned HTTP 500.")):
                with self.subTest(argv=argv, status=status):
                    error = self._fail_next_request(status, {"error": "unavailable"})
                    _, stderr = self._run(argv, expected_exit_code=1)
                    self.assertEqual(stderr, f'{description} {advice}\n{{"error": "unavailable"}}\n')
                    self.assertTrue(error.closed)

    def test_a_refused_redirect_or_a_broken_answer_gives_the_advice_of_its_login_request(self) -> None:
        def refuse_the_redirect(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            raise RefusedRedirectError(request.full_url, "https://api.test/moved", "POST", is_cross_origin=False)

        def break_off_the_answer(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            return _AnswerThatBreaksOff(TimeoutError("timed out"), 201)

        for argv, advice in self._LOGIN_REQUESTS:
            for label, end_the_request, lead in (
                    ("a refused redirect", refuse_the_redirect,
                     "The exactory server at https://api.test answered the POST request with a redirect. The command"
                     " does not follow a redirect of a POST request."),
                    ("an answer that breaks off", break_off_the_answer,
                     "The client cannot read the answer of the exactory server at https://api.test to the POST"
                     " request. Cause: timed out.")):
                with self.subTest(argv=argv, outcome=label):
                    _transport.open_url_within_origin = end_the_request

                    _, stderr = self._run(argv, expected_exit_code=1)

                    self.assertEqual(stderr, f"{lead} {advice}\n")
                    self.assertEqual(_transport._read_api_key(), "")

    def test_an_allowed_missing_response_is_closed(self) -> None:
        error = self._fail_next_request(404, {"error": "not_found"})
        self.assertEqual(_transport._send_request(
            "GET", "/missing", authenticated=False, allow_missing=True), ({}, 404))
        self.assertTrue(error.closed)

    def test_a_missing_key_elsewhere_names_the_login_command(self) -> None:
        _, stderr = self._run(["tasks"], expected_exit_code=2)
        self.assertIn("exactory login", stderr)
        self.assertEqual(self.sent_requests, [])

    def test_logout_removes_the_file_and_nothing_else(self) -> None:
        path = _transport._save_api_key("file-key", "a@test.local", "laptop")
        stdout, stderr = self._run(["logout"])
        self.assertFalse(path.exists())
        self.assertEqual(json.loads(stdout), {"removed": str(path)})
        self.assertIn("https://api.test/keys", stderr)
        self.assertEqual(self.sent_requests, [])

    def test_logout_with_no_file_reports_nothing_removed(self) -> None:
        stdout, _ = self._run(["logout"])
        self.assertEqual(json.loads(stdout), {"removed": None})


class TestWhoamiSubcommand(unittest.TestCase):
    """`whoami` reports where the credential comes from and never prints its value."""

    def setUp(self) -> None:
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        self.config_home = Path(config_home.name)
        saved_env = {key: os.environ.get(key)
                     for key in ("EXACTORY_API_KEY", "XDG_CONFIG_HOME")}

        def _restore_env() -> None:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(_restore_env)
        os.environ.pop("EXACTORY_API_KEY", None)
        os.environ["XDG_CONFIG_HOME"] = str(self.config_home)

    def _run(self, argv: list[str]) -> tuple[str, str]:
        return _invoke_cli(self, argv)

    def test_reports_the_environment_variable_without_its_value(self) -> None:
        os.environ["EXACTORY_API_KEY"] = "sk-never-print-this"
        stdout, _ = self._run(["whoami"])
        self.assertEqual(json.loads(stdout), {"source": "environment"})
        self.assertNotIn("sk-never-print-this", stdout)

    def test_reports_the_credentials_file_with_email_and_label(self) -> None:
        path = _transport._save_api_key("sk-never-print-this", "a@test.local", "laptop")
        stdout, _ = self._run(["whoami"])
        self.assertEqual(json.loads(stdout), {
            "source": "file", "path": str(path),
            "email": "a@test.local", "label": "laptop",
        })
        self.assertNotIn("sk-never-print-this", stdout)

    def test_reports_no_credential_and_names_the_login_command(self) -> None:
        stdout, stderr = self._run(["whoami"])
        self.assertEqual(json.loads(stdout), {"source": "none"})
        self.assertIn("exactory login", stderr)


class TestOpenSignupSubcommand(unittest.TestCase):
    """`open-signup` opens the browser when it can, and always prints the URL."""

    def setUp(self) -> None:
        saved_url = os.environ.get("EXACTORY_API_URL")
        self.addCleanup(
            lambda: os.environ.__setitem__("EXACTORY_API_URL", saved_url)
            if saved_url is not None else os.environ.pop("EXACTORY_API_URL", None))
        os.environ.pop("EXACTORY_API_URL", None)

        self.opened_urls: list[str] = []
        self.browser_available = True
        self.addCleanup(setattr, _transport.webbrowser, "open",
                        _transport.webbrowser.open)

        def _fake_open(url: str) -> bool:
            self.opened_urls.append(url)
            return self.browser_available

        _transport.webbrowser.open = _fake_open

    def _run(self, argv: list[str]) -> tuple[str, str]:
        return _invoke_cli(self, argv)

    def test_opens_the_production_signup_page_by_default(self) -> None:
        stdout, stderr = self._run(["open-signup"])
        self.assertEqual(self.opened_urls, ["https://www.exactory.ai/signup"])
        self.assertEqual(json.loads(stdout),
                         {"url": "https://www.exactory.ai/signup", "opened": True})
        self.assertIn("https://www.exactory.ai/signup", stderr)

    def test_follows_the_configured_base_url(self) -> None:
        os.environ["EXACTORY_API_URL"] = "https://api.test/"
        stdout, _ = self._run(["open-signup"])
        self.assertEqual(json.loads(stdout)["url"], "https://api.test/signup")

    def test_without_a_browser_it_tells_the_user_to_open_the_url(self) -> None:
        self.browser_available = False
        stdout, stderr = self._run(["open-signup"])
        self.assertEqual(json.loads(stdout)["opened"], False)
        self.assertIn("Open this URL", stderr)
        self.assertIn("https://www.exactory.ai/signup", stderr)


if __name__ == "__main__":
    unittest.main()
