"""exactory verify, exactory submit and exactory reconcile while a step of a saved intent is pending.

A step is pending after the store refused the record of the answer, after the response was lost, and after a
request that did not arrive. The stand-in service of these tests applies the rules of the web app: one current
verdict of an account on a verification, and one open verification request for a paper."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import uuid
from unittest import mock

import test_transport
from test_transport import _VERIFICATION_ID, _FakeResponse, _transport, _write_verdict_file
from research_harness import execution, finishing, remote
from research_harness.errors import ResearchError
from research_harness.storage import Store

_VERDICTS_PATH = f"/api/v1/verifications/{_VERIFICATION_ID}/verdicts"
_TASK_PATH = f"/api/v1/tasks/{_VERIFICATION_ID}"
# The service keeps one open request for a paper under its concept DOI, which the published fixture record has.
_CONCEPT_DOI_TASK_PATH = "/api/v1/tasks/10.5281/zenodo.0"
_BUSY_MSG = "Research store is busy; retry the same request after the writer finishes"


class _ClockBeyondEveryWait:
    """A clock on which every wait of a finishing write has ended at its next reading, and no sleep takes time."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += 10_000.0
        return self.now

    def sleep(self, seconds: float) -> None:
        pass


class _StandInService:
    """The verdict and submission rules of the service, behind the signature of _send_request."""

    def __init__(self, task: dict) -> None:
        self.task = task
        self.verdicts: list[dict] = []
        self.request: dict | None = None
        self.is_ingested = False
        self.fault: str | None = None
        self.posts: list[tuple[str, int | None]] = []
        self.bodies: list[dict] = []
        self.reads: list[str] = []
        # Answers to the next reads of the verdict task that the service gave before the latest request was applied.
        self.earlier_task_reads: list[dict] = []

    def read_current_verdict_id(self) -> str | None:
        return next((row["id"] for row in self.verdicts if row["status"] == "current"), None)

    def send(self, method: str, path: str, body: dict | None = None, *, allow_missing: bool = False,
             **options: object) -> tuple[dict, int]:
        if method == "GET":
            self.reads.append(path)
            return self._read_task(path, allow_missing)
        self.bodies.append(body)
        fault, self.fault = self.fault, None
        if fault == "never_arrives":
            self.posts.append((path, None))
            _transport._exit_with_error("The client cannot reach the server.")
        answer, status = self._file_verdict(body) if path == _VERDICTS_PATH else self.open_request()
        self.posts.append((path, status))
        if fault == "response_lost":
            _transport._exit_with_error("The client cannot read the answer of the server.")
        if status >= 400:
            _transport._exit_with_error(json.dumps(answer))
        return answer, status

    def _read_task(self, path: str, allow_missing: bool) -> tuple[dict, int]:
        if path == _TASK_PATH and self.earlier_task_reads:
            return self.earlier_task_reads.pop(0), 200
        if path == _TASK_PATH:
            return dict(self.task, viewerVerdictId=self.read_current_verdict_id()), 200
        if path == _CONCEPT_DOI_TASK_PATH and self.request is not None and self.is_ingested:
            return {"verificationId": _VERIFICATION_ID, "doi": "10.5281/zenodo.0", "source": "zenodo", "sourceId": "1",
                    "sourceVersion": None, "url": "https://zenodo.org/records/1"}, 200
        if allow_missing:
            return {}, 404
        _transport._exit_with_error("The server did not find this resource.")
        raise AssertionError("unreachable")

    def _file_verdict(self, body: dict) -> tuple[dict, int]:
        # The service compares UUID values, so the letter case of an ID in the body does not matter.
        predecessor, current = body.get("supersedesVerdictId"), self.read_current_verdict_id()
        predecessor = predecessor.lower() if isinstance(predecessor, str) else predecessor
        if predecessor is None and current is not None:
            return {"error": "conflict"}, 409
        if predecessor is not None and current != predecessor:
            return {"error": "invalid_request"}, 422
        for row in self.verdicts:
            if row["id"] == predecessor:
                row["status"] = "superseded"
        identifier = str(uuid.uuid4())
        self.verdicts.append({"id": identifier, "status": "current"})
        return {"id": identifier, "status": "current", "stance": body["stance"], "supersedesVerdictId": predecessor,
                "createdAt": "2026-11-20T00:00:01.000Z"}, 201

    def open_request(self) -> tuple[dict, int]:
        # The answer of the service: verificationSummarySchema, which names the request by its id.
        status = 201 if self.request is None else 200
        self.request = {"id": _VERIFICATION_ID, "status": "open", "doi": "10.5281/zenodo.0",
                        "createdAt": "2026-11-20T00:00:00.000Z", "webUrl": "https://www.exactory.ai/verifications/x",
                        "supersedes": None, "supersededBy": None}
        return dict(self.request), status


class _PendingStepTestCase(test_transport._TransportTestCase):
    def install_service(self, task: dict) -> None:
        self.service = _StandInService(task)
        _transport._send_request = self.service.send

    def run_with_a_refused_intent_write(self, argv: list[str], action: str) -> ResearchError:
        """Run the command while the store refuses one write of the intent beyond the wait of that write: "claim" is
        the start of a step, and "resolve" is the record of its answer."""
        change = remote._change

        def refuse_the_write(store, identifier, attempted_action, *args, **kwargs):
            if attempted_action == action:
                raise ResearchError("store_busy", _BUSY_MSG)
            return change(store, identifier, attempted_action, *args, **kwargs)

        with mock.patch.object(remote, "_change", refuse_the_write), \
                mock.patch.object(finishing, "time", _ClockBeyondEveryWait()), \
                self.assertRaises(ResearchError) as refused:
            self._run(argv)
        self.assertEqual(refused.exception.code, "store_busy")
        return refused.exception

    def run_with_a_refused_answer_record(self, argv: list[str]) -> ResearchError:
        return self.run_with_a_refused_intent_write(argv, "resolve")

    def run_done(self, argv: list[str]) -> tuple[str, str]:
        """The two streams of a command that ends without a refusal. A refusal fails the test with its requests."""
        try:
            return self._run(argv)
        except ResearchError as error:
            self.fail(f"The command was refused with {error.code} after these POST requests: {self.service.posts!r}")
        except SystemExit as ended:
            self.fail(f"The command ended with exit status {ended.code!r} after these POST requests: {self.service.posts!r}")

    def run_refused(self, argv: list[str]) -> ResearchError:
        """The refusal that ends the command. A command that ends another way fails the test with its requests."""
        try:
            self._run(argv)
        except ResearchError as error:
            return error
        except SystemExit as ended:
            self.fail(f"The command ended with exit status {ended.code!r} after these POST requests: {self.service.posts!r}")
        self.fail(f"The command was not refused. Its POST requests: {self.service.posts!r}")

    def read_records(self) -> dict:
        return Store(self.scratch_dir).snapshot()["records"]

    def read_intent(self, kind: str) -> dict:
        intents = [row for row in self.read_records()["remote_intent"].values() if row["kind"] == kind]
        self.assertEqual(len(intents), 1)
        return intents[0]


class TestVerifyWithAPendingVerdict(_PendingStepTestCase):
    _task = test_transport.TestVerify._task
    _bind_the_verdict = test_transport.TestVerify._bind_the_verdict_in_a_verification_workspace
    _ARGV = ["verify", _VERIFICATION_ID, "--file", "verdict.json"]

    def setUp(self) -> None:
        super().setUp()
        self._bind_the_verdict()
        self.install_service(self._task())

    def test_verify_after_a_refused_answer_record_sends_no_second_verdict(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        filed = self.service.read_current_verdict_id()
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])

        refusal = self.run_refused(self._ARGV)

        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])
        self.assertEqual(refusal.code, "verdict_reconciliation_pending")
        intent = self.read_intent("verdict")
        self.assertEqual(refusal.details, {"intent_id": intent["id"], "step": "verdict",
                                           "verificationId": _VERIFICATION_ID, "viewerVerdictId": filed})
        self.assertEqual(intent["pending"]["name"], "verdict")
        self.assertNotIn("verdict_receipt", self.read_records())

    def test_verify_after_a_lost_response_sends_no_second_verdict_that_the_task_shows(self) -> None:
        self.service.fault = "response_lost"
        self._run(self._ARGV, expected_exit_code=1)

        refusal = self.run_refused(self._ARGV)

        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])
        self.assertEqual(refusal.code, "verdict_reconciliation_pending")
        self.assertEqual(refusal.details["viewerVerdictId"], self.service.read_current_verdict_id())

    def test_verify_decides_a_pending_step_by_a_task_read_taken_while_it_owns_the_verification(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        # The read of the command came before the earlier request was applied, so it still shows no verdict.
        self.service.earlier_task_reads.append(dict(self._task(), viewerVerdictId=None))
        self.service.reads.clear()

        refusal = self.run_refused(self._ARGV)

        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])
        self.assertEqual(refusal.code, "verdict_reconciliation_pending")
        self.assertEqual(refusal.details["viewerVerdictId"], self.service.read_current_verdict_id())
        self.assertEqual(self.service.reads, [_TASK_PATH, _TASK_PATH])
        self.assertNotIn("discarded", self.read_intent("verdict"))

    def test_verify_after_a_request_that_never_arrived_sends_it_once_and_writes_the_receipt(self) -> None:
        self.service.fault = "never_arrives"
        self._run(self._ARGV, expected_exit_code=1)
        self.assertEqual(self.read_intent("verdict")["pending"]["name"], "verdict")

        _, stderr_text = self.run_done(self._ARGV)

        self.assertEqual(stderr_text, "")
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, None), (_VERDICTS_PATH, 201)])
        intent = self.read_intent("verdict")
        self.assertEqual(intent["status"], "complete")
        self.assertEqual(intent["discarded"], [{"name": "verdict",
                                                "observation": {"kind": "remote_read", "viewerVerdictId": None}}])
        receipt = self.read_records()["verdict_receipt"][intent["id"]]
        self.assertEqual(receipt["response"]["id"], self.service.read_current_verdict_id())

    def test_reconcile_with_the_verdict_id_records_the_answer_and_sends_nothing(self) -> None:
        answered = self.run_with_a_refused_answer_record(self._ARGV)
        filed = self.service.read_current_verdict_id()
        intent_id = self.read_intent("verdict")["id"]
        # The refusal of the first run carries the id of the verdict that the service filed.
        self.assertEqual((answered.details["intent_id"], answered.details["response"]["id"]), (intent_id, filed))

        wrong = self.run_refused(["reconcile", intent_id, "--verdict-id", str(uuid.uuid4())])
        self.assertEqual(wrong.code, "remote_reconciliation_conflict")
        self.assertEqual(wrong.details, {"intent_id": intent_id, "viewerVerdictId": filed, "pending": True})
        self.assertNotIn("verdict_receipt", self.read_records())

        # The ID names the same verdict in either letter case, and the receipt holds it as the task shows it.
        stdout_text, _ = self.run_done(["reconcile", intent_id, "--verdict-id", filed.upper()])

        self.assertEqual(json.loads(stdout_text)["response"], {"id": filed})
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])
        intent = self.read_intent("verdict")
        self.assertEqual(intent["status"], "complete")
        self.assertEqual(intent["responses"]["verdict"]["observation"],
                         {"kind": "reported_answer",
                          "task": {"verificationId": _VERIFICATION_ID, "viewerVerdictId": filed}})
        # The same command then returns the receipt, as after a recorded answer.
        stdout_text, _ = self.run_done(self._ARGV)
        self.assertEqual(json.loads(stdout_text)["response"], {"id": filed})
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])

    def test_reconcile_with_the_verdict_id_after_a_refused_receipt_records_the_receipt(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        filed = self.service.read_current_verdict_id()
        intent_id = self.read_intent("verdict")["id"]
        argv = ["reconcile", intent_id, "--verdict-id", filed]

        def refuse_the_receipt(*args, **kwargs):
            raise ResearchError("store_busy", _BUSY_MSG)

        with mock.patch.object(remote, "_finish_intent_once", refuse_the_receipt), \
                mock.patch.object(finishing, "time", _ClockBeyondEveryWait()):
            refusal = self.run_refused(argv)
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote.REMOTE_RECEIPT_STORE_BUSY_MSG))
        self.assertEqual(self.read_intent("verdict")["status"], "confirmed")

        # The message says to run the same command again, and that command records the receipt.
        stdout_text, _ = self.run_done(argv)

        self.assertEqual(json.loads(stdout_text)["response"], {"id": filed})
        self.assertEqual(self.read_intent("verdict")["status"], "complete")
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])

    def test_reconcile_with_the_verdict_id_reads_nothing_while_another_sender_owns_the_verification(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        intent_id = self.read_intent("verdict")["id"]
        self.service.reads.clear()

        with execution._owner(Store(self.scratch_dir).root, "verdict:" + _VERIFICATION_ID):
            refusal = self.run_refused(["reconcile", intent_id, "--verdict-id", self.service.read_current_verdict_id()])

        # The read that decides the answer is taken while the command owns the verification.
        self.assertEqual(refusal.code, "remote_operation_owned")
        self.assertEqual(self.service.reads, [])
        self.assertEqual(self.read_intent("verdict")["pending"]["name"], "verdict")

    def test_verdict_id_for_a_submission_or_a_deposit_intent_is_refused(self) -> None:
        store = Store(self.scratch_dir)
        for kind in ("submit", "deposit"):
            with self.subTest(kind=kind):
                remote.begin_intent(store, kind, {"id": kind}, expected_revision=store.revision,
                                    request_id=kind + "-intent")

                refusal = self.run_refused(["reconcile", kind + "-intent", "--verdict-id", str(uuid.uuid4())])

                self.assertEqual(refusal.code, "remote_intent_mismatch")
                self.assertEqual(refusal.message, _transport._VERDICT_ID_FOR_A_SUBMISSION_ERR_MSG if kind == "submit"
                                 else "Use exactory-draft reconcile for a Zenodo deposit")
        self.assertEqual((self.service.reads, self.service.posts), ([], []))

    def test_reconcile_without_the_verdict_id_names_the_unrecorded_answer_and_sends_nothing(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        intent_id = self.read_intent("verdict")["id"]

        refusal = self.run_refused(["reconcile", intent_id])

        self.assertEqual(refusal.code, "verdict_reconciliation_pending")
        self.assertEqual(refusal.details["intent_id"], intent_id)
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])

    def test_reconcile_after_a_request_that_never_arrived_sends_it_once(self) -> None:
        self.service.fault = "never_arrives"
        self._run(self._ARGV, expected_exit_code=1)
        intent_id = self.read_intent("verdict")["id"]

        stdout_text, _ = self.run_done(["reconcile", intent_id])

        self.assertEqual(json.loads(stdout_text)["response"]["id"], self.service.read_current_verdict_id())
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, None), (_VERDICTS_PATH, 201)])

    def test_a_refused_observation_of_an_unrecorded_answer_ends_the_command_without_a_request(self) -> None:
        self.service.fault = "response_lost"
        self._run(self._ARGV, expected_exit_code=1)
        from research_harness import submission

        def refuse_the_observation(*args, **kwargs):
            raise ResearchError("store_busy", _BUSY_MSG)

        with mock.patch.object(submission, "_observation", refuse_the_observation):
            refusal = self.run_refused(self._ARGV)

        self.assertEqual(refusal.code, "store_busy")
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])

    def test_a_prepared_verdict_is_not_sent_while_the_task_shows_a_newer_verdict(self) -> None:
        # The start of the step is refused, so the intent is prepared and nothing was sent.
        self.run_with_a_refused_intent_write(self._ARGV, "claim")
        self.assertEqual(self.service.posts, [])
        self.assertIsNone(self.read_intent("verdict")["pending"])
        # The account then files a verdict from another place.
        elsewhere = str(uuid.uuid4())
        self.service.verdicts.append({"id": elsewhere, "status": "current"})

        refusal = self.run_refused(self._ARGV)

        self.assertEqual(self.service.posts, [])
        self.assertEqual(refusal.code, "verdict_revision_required")
        self.assertEqual(refusal.details, {"verificationId": _VERIFICATION_ID, "viewerVerdictId": elsewhere,
                                           "supersedesVerdictId": None})

    def test_another_verdict_file_is_sent_while_an_answer_is_unrecorded(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        filed = self.service.read_current_verdict_id()
        _write_verdict_file(self.scratch_dir / "revision.json", summary="A revised summary.", supersedesVerdictId=filed)

        _, stderr_text = self._run(["verify", _VERIFICATION_ID, "--file", "revision.json"])

        # The revision is not bound, so the workspace records no receipt for it; the pending step stops no other verdict.
        self.assertTrue(stderr_text.startswith("Managed record skipped (verdict_body_mismatch): "), stderr_text)
        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201), (_VERDICTS_PATH, 201)])
        self.assertEqual(self.read_intent("verdict")["pending"]["name"], "verdict")


class TestVerifyOnTheDirectPath(_PendingStepTestCase):
    _task = test_transport.TestVerify._task
    _ARGV = ["verify", _VERIFICATION_ID, "--file", "verdict.json"]

    def setUp(self) -> None:
        super().setUp()
        self.install_service(self._task())

    def test_a_first_verdict_is_not_sent_while_the_task_shows_a_verdict_of_the_account(self) -> None:
        _write_verdict_file(self.scratch_dir / "verdict.json")
        self._run(self._ARGV)
        filed = self.service.read_current_verdict_id()

        refusal = self.run_refused(self._ARGV)

        self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201)])
        self.assertEqual(refusal.code, "verdict_revision_required")
        self.assertEqual(refusal.details, {"verificationId": _VERIFICATION_ID, "viewerVerdictId": filed,
                                           "supersedesVerdictId": None})

    def test_a_revision_is_not_sent_while_the_task_shows_another_current_verdict(self) -> None:
        _write_verdict_file(self.scratch_dir / "verdict.json")
        self._run(self._ARGV)
        first = self.service.read_current_verdict_id()
        _write_verdict_file(self.scratch_dir / "verdict.json", supersedesVerdictId=first)
        self._run(self._ARGV)
        second = self.service.read_current_verdict_id()

        refusal = self.run_refused(self._ARGV)

        self.assertEqual(len(self.service.posts), 2)
        self.assertEqual(refusal.details, {"verificationId": _VERIFICATION_ID, "viewerVerdictId": second,
                                           "supersedesVerdictId": first})

    def test_a_verdict_is_not_sent_beside_a_store_that_does_not_open(self) -> None:
        from integration_fixtures import prepare_verification

        prepare_verification(self.scratch_dir)
        (self.scratch_dir / ".exactory" / "research.sqlite3").write_bytes(b"corrupt native state")
        _write_verdict_file(self.scratch_dir / "verdict.json")
        filed = str(uuid.uuid4())
        self.service.verdicts.append({"id": filed, "status": "current"})

        refusal = self.run_refused(self._ARGV)

        self.assertEqual(self.service.posts, [])
        self.assertEqual((refusal.code, refusal.details["viewerVerdictId"]), ("verdict_revision_required", filed))

    def test_a_revision_that_names_the_current_verdict_is_sent(self) -> None:
        for spell in (str.lower, str.upper):
            with self.subTest(letter_case=spell.__name__):
                self.install_service(self._task())
                _write_verdict_file(self.scratch_dir / "verdict.json")
                self._run(self._ARGV)
                current = self.service.read_current_verdict_id()
                _write_verdict_file(self.scratch_dir / "verdict.json", supersedesVerdictId=spell(current))

                self.run_done(self._ARGV)

                self.assertEqual(self.service.posts, [(_VERDICTS_PATH, 201), (_VERDICTS_PATH, 201)])


class TestRefusedVerdictMessages(test_transport._OpenUrlTransportTestCase):
    """The text of a verdict that the server refuses, through the real transport."""

    _task = test_transport.TestVerify._task
    _bind_the_verdict = test_transport.TestVerify._bind_the_verdict_in_a_verification_workspace
    _ARGV = ["verify", _VERIFICATION_ID, "--file", "verdict.json"]

    def answer_the_verdict_with(self, status: int, error: str) -> None:
        task = self._task()

        def answer(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
            self.sent_requests.append(request)
            if request.get_method() == "GET":
                return _FakeResponse(json.dumps(task).encode(), 200)
            raise urllib.error.HTTPError(request.full_url, status, "Refused", None,
                                         io.BytesIO(json.dumps({"error": error}).encode()))

        _transport.open_url_within_origin = answer

    def check_that_the_refusal_names_the_command_that_reads_the_task(self, stderr_text: str) -> None:
        self.assertTrue(stderr_text.startswith("The server filed nothing."), stderr_text)
        self.assertIn(" Run the same exactory verify or exactory reconcile command again. ", stderr_text)
        self.assertEqual([request.get_method() for request in self.sent_requests], ["GET", "POST"])

    def test_a_refused_verdict_on_the_direct_path_names_the_command_that_reads_the_task(self) -> None:
        _write_verdict_file(self.scratch_dir / "verdict.json")
        for status, error in ((409, "conflict"), (422, "invalid_request")):
            with self.subTest(status=status):
                self.sent_requests.clear()
                self.answer_the_verdict_with(status, error)

                _, stderr_text = self._run(self._ARGV, expected_exit_code=1)

                self.check_that_the_refusal_names_the_command_that_reads_the_task(stderr_text)

    def test_a_refused_verdict_on_the_managed_path_names_the_command_that_reads_the_task(self) -> None:
        # The binding of the transport tests also sets the answers of their stand-in, which these tests do not use.
        self.responses: dict = {}
        self._bind_the_verdict()
        self.answer_the_verdict_with(409, "conflict")

        _, stderr_text = self._run(self._ARGV, expected_exit_code=1)

        self.check_that_the_refusal_names_the_command_that_reads_the_task(stderr_text)
        intents = Store(self.scratch_dir).snapshot()["records"]["remote_intent"].values()
        self.assertEqual([intent["pending"]["name"] for intent in intents], ["verdict"])


class TestSubmitWithAPendingRequest(_PendingStepTestCase):
    _publish_the_reviewed_bundle = test_transport.TestSubmitDecision._publish_the_reviewed_bundle
    _SUBMISSIONS_PATH = "/api/v1/verifications"
    _ARGV = ["submit", "--doi", "10.5281/zenodo.1"]
    _CHALLENGE_ARGV = _ARGV + ["--challenge", "challenge-one"]

    def setUp(self) -> None:
        super().setUp()
        test_transport._build_draft_workspace(self.scratch_dir)
        self._publish_the_reviewed_bundle()
        self.install_service({})

    def test_submit_after_a_refused_answer_record_reads_the_task_and_writes_the_receipt(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        self.service.is_ingested = True

        _, stderr_text = self.run_done(self._ARGV)

        self.assertEqual(stderr_text, "")
        self.assertEqual(self.service.posts, [(self._SUBMISSIONS_PATH, 201)])
        intent = self.read_intent("submit")
        self.assertEqual(intent["status"], "complete")
        self.assertEqual(intent["responses"]["submit"]["observation"]["kind"], "task_only_read")
        self.assertEqual(len(self.read_records()["submission_receipt"]), 1)

    def test_submit_with_another_spelling_of_the_record_continues_the_same_intent(self) -> None:
        self.run_with_a_refused_answer_record(self._ARGV)
        self.service.is_ingested = True

        self.run_done(["submit", "--url", "https://zenodo.org/records/1"])

        self.assertEqual(self.service.posts, [(self._SUBMISSIONS_PATH, 201)])
        self.assertEqual(self.read_intent("submit")["status"], "complete")

    def _check_that_a_request_that_never_arrived_is_sent_on_the_managed_path(self, argv: list[str]) -> None:
        self.service.fault = "never_arrives"
        self._run(argv, expected_exit_code=1)

        refusal = self.run_refused(argv)

        # The request is recorded with its answer; the task is not ingested yet.
        self.assertEqual(refusal.code, "verification_ingest_pending")
        self.assertEqual(self.service.posts, [(self._SUBMISSIONS_PATH, None), (self._SUBMISSIONS_PATH, 201)])
        intent = self.read_intent("submit")
        self.assertIsNone(intent["pending"])
        self.assertEqual(intent["responses"]["submit"]["observation"]["kind"], "direct_response")
        self.assertEqual(intent["discarded"], [{"name": "submit", "observation": {
            "kind": "remote_read", "locator": "10.5281/zenodo.0", "status": 404}}])

        self.service.is_ingested = True
        self.run_done(argv)

        self.assertEqual(len(self.service.posts), 2)
        self.assertEqual(len(self.read_records()["submission_receipt"]), 1)

    def test_submit_while_the_service_shows_no_task_sends_the_request_on_the_managed_path(self) -> None:
        self._check_that_a_request_that_never_arrived_is_sent_on_the_managed_path(self._ARGV)

    def test_managed_only_submit_while_the_service_shows_no_task_sends_the_request(self) -> None:
        self._check_that_a_request_that_never_arrived_is_sent_on_the_managed_path(self._ARGV + ["--managed-only"])

    def test_submit_after_a_lost_response_joins_the_open_request_and_records_its_answer(self) -> None:
        self.service.fault = "response_lost"
        self._run(self._ARGV, expected_exit_code=1)

        refusal = self.run_refused(self._ARGV)

        # The task is not ingested, so the request is sent again; the service answers with the open request.
        self.assertEqual(refusal.code, "verification_ingest_pending")
        self.assertEqual(self.service.posts, [(self._SUBMISSIONS_PATH, 201), (self._SUBMISSIONS_PATH, 200)])
        self.assertEqual(self.read_intent("submit")["responses"]["submit"]["response"]["id"], _VERIFICATION_ID)

    def _check_that_the_task_is_read_by_the_concept_doi(self, status: int) -> None:
        self.service.is_ingested = True

        self.run_done(self._ARGV)

        self.assertEqual(self.service.posts, [(self._SUBMISSIONS_PATH, status)])
        self.assertEqual(self.service.reads, [_CONCEPT_DOI_TASK_PATH])
        answer = self.read_intent("submit")["responses"]["submit"]["response"]
        self.assertEqual((answer["id"], "verificationId" in answer), (_VERIFICATION_ID, False))
        self.assertEqual(len(self.read_records()["submission_receipt"]), 1)

    def test_submit_reads_the_task_by_the_concept_doi_after_the_answer_of_a_new_request(self) -> None:
        self._check_that_the_task_is_read_by_the_concept_doi(201)

    def test_submit_reads_the_task_by_the_concept_doi_after_the_answer_of_the_open_request(self) -> None:
        # Another account opened the request of the paper, so the service answers with that request.
        self.service.open_request()
        self._check_that_the_task_is_read_by_the_concept_doi(200)

    def test_submit_with_other_declarations_than_a_pending_request_sends_them_directly(self) -> None:
        self.service.fault = "never_arrives"
        self._run(self._ARGV, expected_exit_code=1)

        _, stderr_text = self.run_done(self._CHALLENGE_ARGV)

        self.assertTrue(stderr_text.startswith("Managed record skipped (submission_declarations_mismatch): "),
                        stderr_text)
        self.assertEqual(self.service.bodies, [{"doi": "10.5281/zenodo.1"},
                                               {"doi": "10.5281/zenodo.1", "grandChallengeIds": ["challenge-one"]}])
        self.assertEqual(self.read_intent("submit")["pending"]["request"], {"doi": "10.5281/zenodo.1"})

    def test_managed_only_submit_with_other_declarations_than_a_pending_request_is_refused(self) -> None:
        self.service.fault = "never_arrives"
        self._run(self._ARGV + ["--managed-only"], expected_exit_code=1)
        intent_id = self.read_intent("submit")["id"]

        refusal = self.run_refused(self._CHALLENGE_ARGV + ["--managed-only"])

        self.assertEqual(refusal.code, "submission_declarations_mismatch")
        self.assertEqual(refusal.details, {"intent_id": intent_id, "request": {"doi": "10.5281/zenodo.1"}})
        self.assertEqual(self.service.posts, [(self._SUBMISSIONS_PATH, None)])

    def test_submit_with_other_declarations_than_a_complete_request_sends_them_directly(self) -> None:
        self.service.is_ingested = True
        self.run_done(self._ARGV)
        self.assertEqual(self.read_intent("submit")["status"], "complete")

        _, stderr_text = self.run_done(self._CHALLENGE_ARGV)

        self.assertTrue(stderr_text.startswith("Managed record skipped (submission_declarations_mismatch): "),
                        stderr_text)
        self.assertIn("The server returned the existing open request for this paper.", stderr_text)
        self.assertEqual(self.service.bodies[1:], [{"doi": "10.5281/zenodo.1",
                                                    "grandChallengeIds": ["challenge-one"]}])
        self.assertEqual(len(self.read_records()["submission_receipt"]), 1)
