"""The records around a Colab release, around the start of a reviewer program and around a refused reviewer output
commit while other commands use the same store."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from unittest import mock

import test_research_problem_adjudication_resolution as resolution_tests
import test_research_problem_delivery as delivery_fixtures
from codex_route_fixtures import build_codex_response
from development_fixtures import DevelopmentCase
from finishing_write_fixtures import OtherWriters, WaitlessTime
from integration_fixtures import admit_lab
from openness_reading_fixtures import OpennessReadingCase
from research_harness import (cli, execution, finishing, remote_execution, review_call_capture,
                              review_protocol as protocol, review_refusal, review_series, storage)
from research_harness import review_claude_transport, review_codex_transport, review_task_execution, review_worker
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.operations import immutable_record, prepared_mutation
from research_harness.storage import Store, _canonical
from test_research_codex_timeout import PROCESS_FIXTURE
from test_review_task_execution import TaskExecutionFixture

REFUSAL_MEMBERS_0_91_7 = ["assignment_id", "attempt_id", "check", "code", "details", "id", "output_digest", "protocol",
                          "recorded_revision", "request_digest"]
# The stand-in for a reviewer program: it appends a line to the file that it is given and prints an answer.
PROGRAM = "from pathlib import Path; import sys; Path(sys.argv[1]).open('a').write('call\\n'); print('{\"answer\":42}')"
# The entry of an owned review worker whose store refuses the first BUSY_COUNT commits of BUSY_OPERATION with store_busy.
# The worker never sleeps between its attempts; with TICKING its clock passes ten seconds per reading, so that a wait
# ends without real time. Its reviewer call starts PROGRAM as a CLI transport starts its program.
WORKER_ENTRY = r'''
import json, os, subprocess, sys, time
from pathlib import Path
from research_harness import finishing, review_worker
from research_harness.errors import ResearchError
from research_harness.storage import Store, _canonical

busy_operation, remaining = os.environ["BUSY_OPERATION"], [int(os.environ["BUSY_COUNT"])]
def refuse_first(original):
    def commit(store, operation, payload, apply, **kwargs):
        if operation == busy_operation and remaining[0] > 0:
            remaining[0] -= 1
            raise ResearchError("store_busy", "Synthetic busy store")
        return original(store, operation, payload, apply, **kwargs)
    return commit
for name in ("mutate", "mutate_scoped", "mutate_serialized"):
    if hasattr(Store, name):
        setattr(Store, name, refuse_first(getattr(Store, name)))

class Clock:
    now = 0.0
    def sleep(self, seconds):
        pass
    def monotonic(self):
        if os.environ.get("TICKING") != "1":
            return time.monotonic()
        Clock.now += 10.0
        return Clock.now
finishing.time = Clock()

def observe(store, route, request):
    child = subprocess.Popen([sys.executable, "-c", PROGRAM, str(store.root / "calls")],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    stdout, stderr = child.communicate(timeout=60)
    artifacts = store.build_artifact_store()
    completed = child.returncode == 0
    return {"status": "completed" if completed else "failed",
            "request": artifacts.put(_canonical(request).encode(), "application/json"),
            "response": artifacts.put(stdout, "application/json"),
            "output": artifacts.put(_canonical({"answer": 42}).encode(), "application/json") if completed else None,
            "error": None if completed else {"code": "review_transport_failure", "message": stderr.decode()[-300:]}}
review_worker._observe_live = observe
review_worker.run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]))
'''


class ReviewerProgramStartTests(TaskExecutionFixture):
    """The records before a reviewer program starts: the worker of a staged task takes and starts its reservation, and
    the owner of a call records the process that starts the program."""

    def launch(self, reservation, operation="none", count=0, ticking=False):
        entry = [sys.executable, "-c", WORKER_ENTRY.replace("PROGRAM", repr(PROGRAM))]
        environment = dict(os.environ, BUSY_OPERATION=operation, BUSY_COUNT=str(count), TICKING="1" if ticking else "0")
        with mock.patch.object(review_worker, "_entry_command", return_value=entry), \
                mock.patch.dict(os.environ, environment):
            process = review_worker.launch_worker(self.store, reservation["id"])
        self.addCleanup(self.stop, process)
        return process

    def stop(self, process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=60)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def current(self, reservation):
        return self.store.snapshot()["records"]["review_reservation"][reservation["id"]]

    def worker_log(self, reservation):
        return (self.root / review_worker._directory(reservation["id"]) / "worker.log").read_text()

    def calls(self):
        return (self.root / "calls").read_text().splitlines() if (self.root / "calls").exists() else []

    def assert_captured_after(self, operation):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, operation, 9)
        self.assertEqual(process.wait(timeout=120), 0, self.worker_log(reservation))
        current = self.current(reservation)
        self.assertEqual((current["state"], len(current["children"])), ("captured", 1))
        self.assertEqual(self.calls(), ["call"])

    def test_worker_takes_its_reservation_when_the_store_is_busy_for_nine_attempts(self):
        self.assert_captured_after("review.worker_ready")

    def test_worker_starts_its_reservation_when_the_store_is_busy_for_nine_attempts(self):
        self.assert_captured_after("review.worker_running")

    def test_child_of_a_worker_is_recorded_when_the_store_is_busy_for_nine_attempts(self):
        self.assert_captured_after("review.worker_child")

    def test_worker_refused_beyond_the_wait_starts_no_program_and_its_log_says_so(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "review.worker_running", 10 ** 6, ticking=True)
        self.assertNotEqual(process.wait(timeout=120), 0)
        self.assertIn(review_worker.WORKER_RUNNING_STORE_BUSY_MSG, " ".join(self.worker_log(reservation).split()))
        self.assertEqual(self.calls(), [])
        review_task_execution.reconcile_worker(self.store, {"reservation_id": reservation["id"]},
                                               request_id="reconcile-after-busy")
        current = self.current(reservation)
        self.assertEqual((current["state"], current["released"], current["children"]), ("known_failure", True, []))
        # The task keeps its second attempt.
        self.assertEqual(self.reserve(request_id="second")["result"]["attempt"], 2)

    def test_worker_captures_a_child_record_refused_beyond_the_wait_as_a_failed_observation(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "review.worker_child", 10 ** 6, ticking=True)
        self.assertEqual(process.wait(timeout=120), 0, self.worker_log(reservation))
        current = self.current(reservation)
        self.assertEqual((current["state"], current["children"]), ("known_failure", []))
        observation = self.store.snapshot()["records"]["review_observation"][reservation["id"]]["observation"]
        self.assertEqual((observation["status"], observation["error"]),
                         ("failed", {"code": "store_busy", "message": review_worker.WORKER_CHILD_STORE_BUSY_MSG}))
        self.assertEqual(self.calls(), [])

    def test_capture_of_a_worker_refused_beyond_the_wait_ends_the_worker_and_its_log_says_so(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "review.task_capture", 10 ** 6, ticking=True)
        self.assertNotEqual(process.wait(timeout=120), 0)
        self.assertIn(review_worker.WORKER_CAPTURE_STORE_BUSY_MSG, " ".join(self.worker_log(reservation).split()))
        # The reviewer program ran once, and the reconciliation records an unknown outcome.
        self.assertEqual(self.calls(), ["call"])
        review_task_execution.reconcile_worker(self.store, {"reservation_id": reservation["id"]},
                                               request_id="reconcile-after-capture")
        current = self.current(reservation)
        self.assertEqual((current["state"], current["released"]), ("unknown", True))
        self.assertEqual(self.reserve(request_id="second")["result"]["attempt"], 2)

    def refuse_commits(self, operation, count):
        """The store of this process refuses the first count commits of operation with store_busy. Returns a function
        that ends the refusals."""
        remaining = [count]

        def wrap(original):
            def commit(store, name, payload, apply, **kwargs):
                if name == operation and remaining[0] > 0:
                    remaining[0] -= 1
                    raise ResearchError("store_busy", "Synthetic busy store")
                return original(store, name, payload, apply, **kwargs)
            return commit
        for entry in ("mutate", "mutate_scoped", "mutate_serialized"):
            patch = mock.patch.object(Store, entry, wrap(getattr(Store, entry)))
            patch.start()
            self.addCleanup(patch.stop)
        return lambda: remaining.__setitem__(0, 0)

    def reconcile_until_released(self, reservation):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            review_task_execution.reconcile_worker(self.store, {"reservation_id": reservation["id"]},
                                                   request_id="reconcile-" + str(self.store.revision))
            current = self.current(reservation)
            if current["released"]:
                return current
            time.sleep(.05)
        self.fail("The worker of the reservation did not end")

    def test_launch_is_claimed_when_the_store_is_busy_for_nine_attempts(self):
        reservation = self.reserve()["result"]
        self.refuse_commits("review.worker_claim", 9)
        with mock.patch.object(finishing, "time", WaitlessTime()):
            process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=120), 0, self.worker_log(reservation))
        self.assertEqual((self.current(reservation)["state"], self.calls()), ("captured", ["call"]))

    def test_claim_refused_beyond_the_wait_starts_no_worker_and_the_same_launch_runs_later(self):
        reservation = self.reserve()["result"]
        end_refusals = self.refuse_commits("review.worker_claim", 10 ** 6)
        with mock.patch.object(finishing, "time", WaitlessTime(ticking=True)):
            with self.assertRaises(ResearchError) as caught:
                self.launch(reservation)
        end_refusals()
        refusal = caught.exception
        self.assertEqual((refusal.code, refusal.message), ("store_busy", review_worker.WORKER_CLAIM_STORE_BUSY_MSG))
        self.assertIsNone(self.current(reservation)["launch"])
        # The way on that the message names: the same launch.
        process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=120), 0, self.worker_log(reservation))
        self.assertEqual((self.current(reservation)["state"], self.calls()), ("captured", ["call"]))

    def test_changed_dependency_of_the_task_refuses_the_launch_at_once(self):
        reservation = self.reserve()["result"]
        self.put("context", "slot", {"version": 2})
        with mock.patch.object(finishing, "time", WaitlessTime()):
            with self.assertRaises(ResearchError) as caught:
                self.launch(reservation)
        # The refusal of the launch check is no refusal of the store: it is not waited for.
        self.assertEqual(caught.exception.code, "review_task_dependency_changed")
        self.assertIsNone(self.current(reservation)["launch"])

    def test_launch_is_authorized_when_the_store_is_busy_for_nine_attempts(self):
        reservation = self.reserve()["result"]
        self.refuse_commits("review.worker_authorize", 9)
        with mock.patch.object(finishing, "time", WaitlessTime()):
            process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=120), 0, self.worker_log(reservation))
        self.assertEqual(self.current(reservation)["state"], "captured")
        self.assertEqual(self.calls(), ["call"])

    def test_authorization_refused_beyond_the_wait_starts_no_program_and_names_the_way_on(self):
        reservation = self.reserve()["result"]
        self.refuse_commits("review.worker_authorize", 10 ** 6)
        with mock.patch.object(finishing, "time", WaitlessTime(ticking=True)):
            with self.assertRaises(ResearchError) as caught:
                self.launch(reservation)
        refusal = caught.exception
        self.assertEqual((refusal.code, refusal.message), ("store_busy", review_worker.WORKER_AUTHORIZE_STORE_BUSY_MSG))
        self.assertEqual((refusal.details["reservation_id"], refusal.details["cause"]["code"]),
                         (reservation["id"], "store_busy"))
        # The way on that the message names: the reconciliation releases the reservation, and the task runs again.
        current = self.reconcile_until_released(reservation)
        self.assertEqual((current["state"], current["launch"], current["children"]),
                         ("known_failure", {"authorized": False}, []))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.reserve(request_id="second")["result"]["attempt"], 2)

    def test_worker_that_cannot_take_its_reservation_within_the_wait_ends_and_its_log_says_so(self):
        reservation = self.reserve()["result"]
        with self.assertRaises(ResearchError) as caught:
            self.launch(reservation, "review.worker_ready", 10 ** 6, ticking=True)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("review_worker_reconciliation_required", "The worker launch handshake did not complete"))
        self.assertIn(review_worker.WORKER_READY_STORE_BUSY_MSG, " ".join(self.worker_log(reservation).split()))
        self.assertEqual(self.calls(), [])

    def start_inline_call(self, stack, identifier="call-1"):
        """An owned inline call that is started, as review_protocol._invoke_once starts it, and its ownership of the
        processes of its transport."""
        authority = stack.enter_context(review_worker.own_call(self.store, "review.invoke", identifier))
        invocation = {"id": identifier, "route_id": "route", "assignment_id": "assignment", "request": self.request}

        def apply(tx, value, revision):
            review_task_execution.start_call(tx, "review.invoke", invocation, authority, revision)
            tx.put("review_invocation", identifier, invocation)
            return invocation
        review_task_execution._mutate_current(self.store, "review.invoke", invocation, apply,
                                              request_id=identifier + "-start")
        stack.enter_context(review_worker._own_transport_processes(authority))
        return authority

    def start_program(self):
        process = subprocess.Popen([sys.executable, "-c", PROGRAM, str(self.root / "calls")],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.addCleanup(self.stop, process)
        return process

    def children(self, authority):
        return self.store.snapshot()["records"]["review_pool_membership"][authority.reservation_id]["children"]

    def lock_store(self, seconds=None):
        """Another command holds the write lock of the store, for seconds or until the returned function is called."""
        connection = sqlite3.connect(str(self.root / ".exactory" / "research.sqlite3"), isolation_level=None,
                                     check_same_thread=False)
        connection.execute("BEGIN IMMEDIATE")
        released = threading.Event()

        def release():
            if not released.is_set():
                released.set()
                connection.execute("ROLLBACK")
                connection.close()
        self.addCleanup(release)
        if seconds is not None:
            timer = threading.Timer(seconds, release)
            timer.start()
            self.addCleanup(timer.cancel)
        return release

    def test_time_limit_of_a_reviewer_program_starts_after_its_record(self):
        with ExitStack() as stack:
            authority = self.start_inline_call(stack)
            # Another command holds the write lock for four seconds. A transport gives its program three seconds.
            self.lock_store(4)
            process = self.start_program()
            stdout, stderr = process.communicate(timeout=3)
        self.assertEqual((process.returncode, self.calls()), (0, ["call"]), stderr)
        self.assertEqual(len(self.children(authority)), 1)

    def test_reviewer_program_starts_after_the_store_was_locked_for_eleven_seconds(self):
        with ExitStack() as stack:
            authority = self.start_inline_call(stack)
            self.lock_store(11)
            process = self.start_program()
            stdout, stderr = process.communicate(timeout=60)
        self.assertEqual((process.returncode, self.calls()), (0, ["call"]), stderr)
        self.assertEqual(len(self.children(authority)), 1)

    def test_child_record_refused_beyond_the_wait_starts_no_program(self):
        with self.assertRaises(ResearchError) as caught:
            with ExitStack() as stack:
                authority = self.start_inline_call(stack)
                release = self.lock_store()
                stack.enter_context(mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01))
                stack.enter_context(mock.patch.object(finishing, "time", WaitlessTime(ticking=True)))
                self.start_program()
        release()
        refusal = caught.exception
        self.assertEqual((refusal.code, refusal.message), ("store_busy", review_worker.CALL_CHILD_STORE_BUSY_MSG))
        self.assertEqual(refusal.details["cause"]["code"], "store_busy")
        self.assertEqual((self.children(authority), self.calls()), ([], []))


class OwnedCallTransportTests(TaskExecutionFixture):
    """The record of each process that the Codex or the Claude transport starts for a call that a command owns, on the
    path of review-run: _invoke_once, _observed and the transport, with stand-in programs."""

    def setUp(self):
        super().setUp()
        self.programs = self.root.parent
        (self.programs / "mode").write_text("complete")
        for name in ("codex", "claude"):
            program = self.programs / name
            program.write_text("#!" + sys.executable + "\n" + PROCESS_FIXTURE)
            program.chmod(0o700)
        which = shutil.which
        located = mock.patch.object(shutil, "which", side_effect=lambda name, *args, **kwargs: str(self.programs / name)
                                    if name in ("codex", "claude") else which(name, *args, **kwargs))
        located.start()
        self.addCleanup(located.stop)
        request = {"instructions": "Return the stand-in output.", "input": [{"role": "user", "content": "No model call."}]}
        self.call_request = self.store.build_artifact_store().put(_canonical(request).encode(), "application/json")
        self.put("review_assignment", "assignment", {"id": "assignment", "packet": self.request})

    def record_route(self, adapter):
        if adapter == "codex_cli_v1":
            route = {"id": "route", "adapter": adapter, "model": "stand-in", "endpoint": "codex://local",
                     "configuration": {"max_output_tokens": 100, "timeout_seconds": 30},
                     "runtime": review_codex_transport.runtime()}
        else:
            route = {"id": "route", "adapter": adapter, "model": "stand-in", "endpoint": "claude://local",
                     "configuration": {"effort": "high", "max_output_tokens": 100, "timeout_seconds": 30},
                     "runtime": review_claude_transport.runtime()}
        self.put("review_route", "route", route)

    def run_call(self, identifier):
        """Run the call as review-run runs it, through _invoke_once."""
        def start(records, value):
            row = dict(value, route_id="route", request=self.call_request)
            return [immutable_record(records, "review_invocation", value["id"], row)], row

        def finish(records, value, invocation, observation, revision):
            row = dict(invocation, **observation, recorded_revision=revision)
            return [immutable_record(records, "review_attempt", value["id"], row)], row
        return protocol._invoke_once(self.store, {"id": identifier, "assignment_id": "assignment"}, "review.invoke",
                                     start, finish, expected_revision=self.store.revision,
                                     request_id="invoke-" + identifier, credential=None)

    def refuse_child_record(self, stack, position):
        """Within stack, the store refuses with store_busy every commit of the child record of the process at position
        (counted from 0) that the call starts."""
        requests = []

        def wrap(original):
            def commit(store, operation, payload, apply, **kwargs):
                if operation == "review.call_child":
                    if kwargs["request_id"] not in requests:
                        requests.append(kwargs["request_id"])
                    if requests.index(kwargs["request_id"]) == position:
                        raise ResearchError("store_busy", "Synthetic busy store")
                return original(store, operation, payload, apply, **kwargs)
            return commit
        for entry in ("mutate", "mutate_scoped", "mutate_serialized"):
            stack.enter_context(mock.patch.object(Store, entry, wrap(getattr(Store, entry))))

    def has_called(self):
        """The stand-in program writes wrapper.json only when it runs as the reviewer call, not for its version."""
        return (self.programs / "wrapper.json").exists()

    def membership(self, identifier):
        lifecycle_id = review_task_execution.identify_call("review.invoke", identifier)
        return self.store.snapshot()["records"]["review_pool_membership"][lifecycle_id]

    def assert_refused_before_the_program(self, adapter, position):
        self.record_route(adapter)
        with ExitStack() as stack:
            self.refuse_child_record(stack, position)
            stack.enter_context(mock.patch.object(finishing, "time", WaitlessTime(ticking=True)))
            with self.assertRaises(ResearchError) as caught:
                self.run_call("call")
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("store_busy", review_worker.CALL_CHILD_STORE_BUSY_MSG))
        self.assertEqual(
            {key: caught.exception.details.get(key) for key in ("reservation_id", "operation", "invocation_id")},
            {"reservation_id": review_task_execution.identify_call("review.invoke", "call"),
             "operation": "review.invoke", "invocation_id": "call"})
        self.assertFalse(self.has_called())
        records = self.store.snapshot()["records"]
        self.assertNotIn("call", records.get("review_attempt", {}))
        membership = self.membership("call")
        self.assertEqual((len(membership["children"]), membership["released"]), (position, False))

    def test_codex_call_starts_four_recorded_processes_and_runs_its_program_once(self):
        self.record_route("codex_cli_v1")
        result = self.run_call("call")
        self.assertEqual(result["result"]["status"], "completed")
        self.assertTrue(self.has_called())
        self.assertEqual(len(self.membership("call")["children"]), 4)

    # A Codex call starts four processes: the version check of the route's runtime, the version check of the sender,
    # the version check inside send_request, and the call. A Claude call starts two inside send_request.
    def test_refused_record_of_the_runtime_check_of_a_codex_call_starts_no_program(self):
        self.assert_refused_before_the_program("codex_cli_v1", 0)

    def test_refused_record_of_the_sender_check_of_a_codex_call_starts_no_program(self):
        self.assert_refused_before_the_program("codex_cli_v1", 1)

    def test_refused_record_of_the_version_check_inside_a_codex_send_starts_no_program(self):
        self.assert_refused_before_the_program("codex_cli_v1", 2)

    def test_refused_record_of_the_codex_call_itself_starts_no_program(self):
        self.assert_refused_before_the_program("codex_cli_v1", 3)

    def test_refused_record_of_the_version_check_inside_a_claude_send_starts_no_program(self):
        self.assert_refused_before_the_program("claude_cli_v1", 0)

    def test_refused_record_of_the_claude_call_itself_starts_no_program(self):
        self.assert_refused_before_the_program("claude_cli_v1", 1)

    def test_call_refused_before_its_program_is_reconciled_as_interrupted_and_the_next_run_calls_the_reviewer(self):
        self.assert_refused_before_the_program("codex_cli_v1", 3)
        # The way on that the message names: the reconciliation records the call as interrupted.
        review_task_execution.reconcile_call(self.store, {"operation": "review.invoke", "invocation_id": "call"},
                                             request_id="reconcile-call")
        attempt = self.store.snapshot()["records"]["review_attempt"]["call"]
        self.assertEqual((attempt["status"], attempt["error"]["code"]), ("failed", "review_invocation_interrupted"))
        self.assertTrue(self.membership("call")["released"])
        self.assertEqual(self.run_call("next")["result"]["status"], "completed")
        self.assertTrue(self.has_called())


class ColabReleaseTests(OtherWriters, DevelopmentCase):
    """The record that releases a Colab job, and the signal that follows it."""

    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.sync = self.root / "transport"
        self.sync.mkdir()
        environment = mock.patch.dict(os.environ, {"EXACTORY_LAB_COLAB_DIR": str(self.sync),
            "EXACTORY_LAB_COLAB_POLL": "0.01", "EXACTORY_LAB_COLAB_WAIT": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.start_other_writers()

    def claim_job(self, request_id="colab-launch"):
        """An admitted Colab run with its claim, its published job and the claim of a runner."""
        admission = admit_lab(self, backend="colab", timeout=5, body="print('{\"metric\": 7}')\n")
        claim = execution._claim(self.store, admission["id"], self.store.revision, request_id)["result"]
        root, job = remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        return admission, claim, root, job

    def signals(self, job):
        return sorted(path.name[:3] for path in (self.sync / "jobs" / job["job_id"]).glob("GO-*"))

    def releases(self):
        return self.store.snapshot()["records"].get("execution_remote_release", {})

    def reconcile(self, admission, request_id):
        return execution.reconcile_execution(self.store, {"admission_id": admission["id"]},
                                             expected_revision=self.store.revision, request_id=request_id)

    def lose_first_signal(self, claim, root, job):
        """The release is recorded and its signal is not written, as after a launcher that ended between the two."""
        with mock.patch.object(remote_execution, "_immutable", side_effect=OSError("Lost first GO write")):
            with self.assertRaises(OSError):
                remote_execution._release(self.store, claim, root, job)
        self.assertEqual(self.signals(job), [])

    def test_release_is_recorded_and_signalled_when_a_commit_meets_every_attempt(self):
        admission, claim, root, job = self.claim_job()
        self.meet_commits(("execution.remote-release",))
        released = remote_execution._release(self.store, claim, root, job)
        self.assertEqual(self.attempts, {"execution.remote-release": 1})
        self.assertEqual(self.releases()[admission["id"]], released)
        self.assertEqual(self.signals(job), ["GO-"])
        self.assertTrue(remote_execution.serve_scan_once(self.sync))
        self.assertTrue(self.reconcile(admission, "collect")["ok"])

    def test_release_is_prepared_again_when_a_checked_record_changes(self):
        admission, claim, root, job = self.claim_job()

        def change_checked_record():
            # A new budget of the profile is one of the records that the admission check reads.
            other = Store(self.root)
            from research_harness.resources import UNITS, set_budget
            set_budget(other, {"profile": "research", "purpose": "literature", "limits": {unit: None for unit in UNITS},
                               "reason": "Another command changes a record of the check."},
                       expected_revision=other.revision, request_id="other-budget")
        self.meet_commits(("execution.remote-release",), act=change_checked_record, limit=1)
        remote_execution._release(self.store, claim, root, job)
        self.assertEqual(self.attempts["execution.remote-release"], 2)
        self.assertIn(admission["id"], self.releases())
        self.assertEqual(self.signals(job), ["GO-"])

    def test_signal_follows_the_release_when_a_commit_lands_after_it(self):
        admission, claim, root, job = self.claim_job()
        check = remote_execution.validate_admitted_execution

        def check_beside_another_writer(records, artifacts, admission_id):
            result = check(records, artifacts, admission_id)
            if admission["id"] in self.releases():
                self.commit_unrelated()
            return result
        with mock.patch.object(remote_execution, "validate_admitted_execution", side_effect=check_beside_another_writer):
            remote_execution._release(self.store, claim, root, job)
        self.assertIn(admission["id"], self.releases())
        self.assertEqual(self.signals(job), ["GO-"])

    def test_release_that_another_launcher_committed_meanwhile_gets_no_signal_after_the_preparation_changed(self):
        admission, claim, root, job = self.claim_job()
        snapshot, met = Store.snapshot, []

        def read_before_another_launcher(store):
            # After this call read the store, another launcher of the same admission commits the release and ends
            # before its signal, and then the preparation changes.
            result = snapshot(store)
            if not met:
                met.append(True)
                with mock.patch.object(remote_execution, "_immutable", side_effect=OSError("The other launcher ended")):
                    with self.assertRaises(OSError):
                        remote_execution._release(Store(self.root), claim, root, job)
                self.refresh_synthesis("after-other-release")
            return result
        with mock.patch.object(Store, "snapshot", read_before_another_launcher):
            with self.assertRaises(ResearchError) as refused:
                remote_execution._release(self.store, claim, root, job)
        # The commit of the other launcher held the check on the store of its revision, not on the current one.
        self.assertEqual(refused.exception.code, "plan_dependencies_stale")
        self.assertIn(admission["id"], self.releases())
        self.assertEqual(self.signals(job), [])
        self.assertFalse(remote_execution.serve_scan_once(self.sync))

    def test_launch_ends_with_the_run_when_commits_meet_the_release(self):
        admission, claim, root, job = self.claim_job()
        errors, stop = [], threading.Event()

        def run_runner():
            try:
                while not stop.is_set() and not remote_execution.serve_scan_once(self.sync):
                    stop.wait(0.01)
            except BaseException as error:
                errors.append(error)
        runner = threading.Thread(target=run_runner)
        self.addCleanup(lambda: (stop.set(), runner.join(10)))
        self.meet_commits(("execution.remote-release",))
        runner.start()
        result = remote_execution.launch_colab(self.store, claim)
        runner.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(result["ok"])
        self.assertEqual(result["metric"], {"metric": 7})

    def test_store_busy_beyond_the_wait_sends_no_signal_and_the_same_admission_releases_later(self):
        admission, claim, root, job = self.claim_job()
        refusal = self.refuse_beyond_the_wait("execution.remote-release",
                                              lambda: remote_execution._release(self.store, claim, root, job))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", remote_execution.REMOTE_RELEASE_STORE_BUSY_MSG))
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual((refusal.details["admission_id"], refusal.details["request_id"], refusal.details["job_id"]),
                         (admission["id"], "colab-launch", job["job_id"]))
        self.assertEqual((dict(self.releases()), self.signals(job)), ({}, []))
        self.assertFalse(remote_execution.serve_scan_once(self.sync), "No program runs without the release")
        # The way on that the message names: the same admission records the release and signals the runner, is refused
        # while the runner has not finished, and records the run after that.
        with self.assertRaises(ResearchError) as pending:
            self.reconcile(admission, "after-busy")
        self.assertEqual(pending.exception.code, "execution_recovery_required")
        self.assertIn(admission["id"], self.releases())
        self.assertEqual(self.signals(job), ["GO-"])
        self.assertTrue(remote_execution.serve_scan_once(self.sync))
        self.assertTrue(self.reconcile(admission, "after-run")["ok"])

    def test_retained_release_is_signalled_while_another_writer_waits_for_its_check(self):
        admission, claim, root, job = self.claim_job()
        self.lose_first_signal(claim, root, job)
        files, outcomes = execution._files, []

        def write_during_the_check(store, admitted, binding):
            result = files(store, admitted, binding)

            def write():
                try:
                    self.commit_unrelated()
                    outcomes.append("committed")
                except ResearchError as error:
                    outcomes.append(error.code)
            writer = threading.Thread(target=write)
            writer.start()
            writer.join(30)
            return result
        with mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.05), \
                mock.patch.object(execution, "_files", side_effect=write_during_the_check):
            remote_execution._release(self.store, claim, root, job)
        # No commit lands between the check of a retained release and the end of that check.
        self.assertEqual(outcomes, ["store_busy"])
        self.assertEqual(self.signals(job), ["GO-"])
        self.assertTrue(remote_execution.serve_scan_once(self.sync))
        self.assertTrue(self.reconcile(admission, "collect")["ok"])

    def test_retained_release_gets_no_signal_after_the_preparation_changed(self):
        admission, claim, root, job = self.claim_job()
        self.lose_first_signal(claim, root, job)
        self.refresh_synthesis("after-lost-go")
        with self.assertRaises(ResearchError) as refused:
            remote_execution._release(self.store, claim, root, job)
        self.assertEqual(refused.exception.code, "plan_dependencies_stale")
        self.assertEqual(self.signals(job), [])
        self.assertFalse(remote_execution.serve_scan_once(self.sync))

    def test_release_record_has_the_members_and_the_request_of_0_91_7(self):
        admission, claim, root, job = self.claim_job()
        released = remote_execution._release(self.store, claim, root, job)
        self.assertEqual(sorted(released), ["admission_id", "job_sha256", "worker"])
        request = self.store.committed_request("remote-release-" + execution.digest(claim))
        self.assertEqual((request["operation"], request["payload"]), ("execution.remote-release", released))
        self.assertEqual(self.store.snapshot()["records"]["literature_operation"][
            "remote-release-" + execution.digest(claim)]["operation"], "execution.remote-release")


class OutputRefusalTests(OtherWriters, OpennessReadingCase):
    """The record of a reviewer output that fails a check of its contract (review.refusal), after a run, and the
    capture and the finish of the call of that run."""

    def setUp(self):
        super().setUp()
        self.limit_input()
        self.native_packet = self.export()
        self.assign("turn-1", self.native_packet)
        self.packet = self.read_packet("turn-1")
        self.refused_output = dict(self.build_reading_response(self.packet), notes="")
        self.start_other_writers()

    def refusals(self):
        return self.store.snapshot()["records"].get("review_output_refusal", {})

    def test_refusal_of_a_run_output_is_recorded_when_a_commit_meets_every_attempt(self):
        self.meet_commits(("review.refusal",))
        receipt = self.invoke("turn-1", self.refused_output)
        self.assertEqual(self.attempts, {"review.refusal": 1})
        refusal = self.refusals()["attempt-turn-1"]
        self.assertEqual((refusal["code"], refusal["check"]), ("invalid_reading_response", "native.output"))
        self.assertEqual(receipt["output_refusal"], {key: refusal[key] for key in ("code", "check", "details")})
        # The refusal admits the second run of the turn.
        self.invoke("turn-1", self.build_reading_response(self.packet), attempt="attempt-turn-1-retry")
        state = protocol.assignment_state(self.store.snapshot()["records"], self.artifacts, "turn-1")
        self.assertTrue(state["ready"], state["obligations"])

    def test_refusal_names_the_revision_of_its_commit(self):
        self.meet_commits(("review.refusal",))
        receipt = self.invoke("turn-1", self.refused_output)
        refusal = self.refusals()["attempt-turn-1"]
        committed = self.store.committed_request("invoke-attempt-turn-1:refusal")
        self.assertEqual(refusal["recorded_revision"], committed["response"]["revision"])
        self.assertEqual(receipt["revision"], refusal["recorded_revision"])
        attempt = self.store.snapshot()["records"]["review_attempt"]["attempt-turn-1"]
        self.assertGreater(refusal["recorded_revision"], attempt["recorded_revision"])

    def test_refusal_record_has_the_members_and_the_request_of_0_91_7(self):
        self.invoke("turn-1", self.refused_output)
        refusal = self.refusals()["attempt-turn-1"]
        self.assertEqual(sorted(refusal), REFUSAL_MEMBERS_0_91_7)
        committed = self.store.committed_request("invoke-attempt-turn-1:refusal")
        self.assertEqual(committed["operation"], "review.refusal")
        self.assertEqual(committed["payload"], {key: value for key, value in refusal.items() if key != "recorded_revision"})
        self.assertEqual(committed["response"]["result"], refusal)

    def test_refusal_that_0_91_7_recorded_is_named_by_the_same_run_without_a_new_write(self):
        with mock.patch.object(review_refusal, "record_native_refusal", return_value=None):
            self.invoke("turn-1", self.refused_output)
        records = self.store.snapshot()["records"]
        failure = review_refusal.classify_observed_output(records, self.artifacts, "attempt-turn-1")
        attempt = records["review_attempt"]["attempt-turn-1"]
        value = dict(id="attempt-turn-1", assignment_id="turn-1", attempt_id="attempt-turn-1",
                     output_digest=digest(json.loads(self.artifacts.read(attempt["output"]))),
                     request_digest=attempt["request_digest"], protocol=review_refusal.PROTOCOL, **failure)
        revision = self.store.revision

        def prepare(current, submitted):
            # The refusal write of 0.91.7: a compare-and-swap at the revision that the run read.
            record = dict(submitted, recorded_revision=revision + 1)
            return [immutable_record(current, "review_output_refusal", "attempt-turn-1", record)], record
        prepared_mutation(self.store, "review.refusal", value, prepare, expected_revision=revision,
                          request_id="invoke-attempt-turn-1:refusal")
        recorded, revision = self.refusals()["attempt-turn-1"], self.store.revision
        receipt = self.invoke("turn-1", self.refused_output)
        self.assertEqual((receipt["revision"], receipt["output_refusal"]),
                         (recorded["recorded_revision"], {key: recorded[key] for key in ("code", "check", "details")}))
        self.assertEqual(self.store.revision, revision)

    def test_refusal_refused_beyond_the_wait_refuses_the_run_and_the_same_request_records_it(self):
        refusal = self.refuse_beyond_the_wait("review.refusal", lambda: self.invoke("turn-1", self.refused_output))
        self.assertEqual((refusal.code, refusal.message),
                         ("store_busy", review_refusal.OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG))
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual((refusal.details["request_id"], refusal.details["assignment_id"], refusal.details["attempt_id"]),
                         ("invoke-attempt-turn-1", "turn-1", "attempt-turn-1"))
        self.assertEqual(refusal.details["refusal"]["code"], "invalid_reading_response")
        records = self.store.snapshot()["records"]
        self.assertIn("attempt-turn-1", records["review_attempt"])
        self.assertEqual(records.get("review_output_refusal", {}), {})
        # The way on that the message names: the same request returns the recorded run and records the refusal.
        receipt = self.invoke("turn-1", self.refused_output)
        recorded = self.refusals()["attempt-turn-1"]
        self.assertEqual(receipt["output_refusal"], {key: recorded[key] for key in ("code", "check", "details")})
        self.assertEqual(len(self.store.snapshot()["records"]["review_attempt"]), 1)
        self.invoke("turn-1", self.build_reading_response(self.packet), attempt="attempt-turn-1-retry")

    def test_refusal_is_recorded_when_the_read_after_the_run_meets_a_busy_store(self):
        invoke_once, snapshot, pending = protocol._invoke_once, Store.snapshot, []

        def run_then_busy(*args, **kwargs):
            result = invoke_once(*args, **kwargs)
            pending.append(True)
            return result

        def read(store):
            if pending:
                pending.pop()
                raise ResearchError("store_busy", "Research store is busy; retry the same request after the writer finishes")
            return snapshot(store)
        with mock.patch.object(protocol, "_invoke_once", run_then_busy), mock.patch.object(Store, "snapshot", read):
            receipt = self.invoke("turn-1", self.refused_output)
        recorded = self.refusals()["attempt-turn-1"]
        self.assertEqual(receipt["output_refusal"], {key: recorded[key] for key in ("code", "check", "details")})

    def test_series_runs_the_same_request_again_after_the_refusal_of_its_output_was_refused(self):
        held, rows, requests = [], [], []
        self.meet_commits(("review.refusal",), act=lambda: held.append(self.hold_write_lock()), limit=1)

        def observe(row):
            rows.append(row)
            if (row.get("error") or {}).get("message") == review_refusal.OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG:
                # The other command ends its write after the series reported the refusal.
                held[0].execute("ROLLBACK")
        run = cli.OPERATIONS["review-run"]

        def run_once(store, payload, **identity):
            requests.append((identity["request_id"], payload))
            return run(store, payload, **identity)
        # Every answer of the turn fails its contract, so the series ends when the second refused output exhausts it.
        with mock.patch.dict(cli.OPERATIONS, {"review-run": run_once}), \
                mock.patch.object(transport, "send_request", side_effect=lambda route, request, credential:
                                  build_codex_response(route, request, self.refused_output)), \
                mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(finishing, "time", WaitlessTime(ticking=True)):
            code = review_series.run_series(self.store, "turn-1", emit=observe)
        self.assertEqual((code, rows[-1]["code"]), (3, "review_slot_exhausted"), rows[-1])
        # The second run is the same request with the same payload. It returned the recorded run and recorded the
        # refusal, which admits the next run of the turn under a new request and a new attempt.
        self.assertEqual(requests[0], requests[1])
        first_attempt_id = requests[0][1]["id"]
        self.assertEqual(self.refusals()[first_attempt_id]["code"], "invalid_reading_response")
        self.assertEqual(len(requests), 3)
        self.assertNotEqual((requests[2][0], requests[2][1]["id"]), (requests[0][0], first_attempt_id))

    def test_series_keeps_the_request_whose_refusal_is_pending_after_a_busy_read(self):
        requests = []
        run = cli.OPERATIONS["review-run"]

        def run_after_two_refusals(store, payload, **identity):
            requests.append((identity["request_id"], payload))
            if len(requests) == 1:
                raise ResearchError("store_busy", review_refusal.OUTPUT_REFUSAL_AFTER_RUN_STORE_BUSY_MSG,
                                    {"request_id": identity["request_id"], "attempt_id": payload["id"]})
            if len(requests) == 2:
                # The first read of the repeated run meets a busy store before the run is returned.
                raise ResearchError("store_busy", "Research store is busy; retry the same request after the writer finishes")
            return run(store, payload, **identity)
        with mock.patch.dict(cli.OPERATIONS, {"review-run": run_after_two_refusals}), \
                mock.patch.object(transport, "send_request", side_effect=lambda route, request, credential:
                                  build_codex_response(route, request, self.refused_output)):
            review_series.run_series(self.store, "turn-1", emit=lambda row: None)
        # The series runs the same request until a step succeeds; only then does it plan the next step.
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[1], requests[2])
        self.assertNotEqual(requests[3][0], requests[0][0])

    def test_accepted_output_writes_no_refusal_and_meets_no_wait(self):
        self.meet_commits(("review.refusal",))
        receipt = self.invoke("turn-1", self.build_reading_response(self.packet))
        self.assertNotIn("output_refusal", receipt)
        self.assertEqual((self.attempts, dict(self.refusals())), ({}, {}))

    def test_capture_and_finish_of_a_call_keep_their_transaction_when_a_reader_holds_their_commit(self):
        readers, refused = [], []
        self.meet_commits(("review.call_capture", "review.invoke"), act=lambda: readers.append(self.hold_read_lock()),
                          limit=1)

        def end_reads():
            for reader in readers:
                if reader.in_transaction:
                    reader.execute("COMMIT")
        with self.observe_commits(refused, end_reads), mock.patch.object(storage, "_BUSY_TIMEOUT_SECONDS", 0.01):
            receipt = self.invoke("turn-1", self.build_reading_response(self.packet))
        self.assertEqual(receipt["result"]["status"], "completed")
        self.assertEqual((self.attempts, len(refused)), ({"review.call_capture": 1, "review.invoke": 1}, 2))

    def test_capture_refused_beyond_the_wait_leaves_the_call_to_its_reconciliation(self):
        answer = self.build_reading_response(self.packet)
        refusal = self.refuse_beyond_the_wait("review.call_capture", lambda: self.invoke("turn-1", answer))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", review_call_capture.CALL_CAPTURE_STORE_BUSY_MSG))
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        # The way on that the message names: the reconciliation records the call as interrupted, and the next run
        # calls the reviewer.
        reconcile_payload = {key: refusal.details.get(key) for key in ("operation", "invocation_id")}
        self.assertEqual(reconcile_payload, {"operation": "review.invoke", "invocation_id": "attempt-turn-1"})
        review_task_execution.reconcile_call(self.store, reconcile_payload, request_id="reconcile-capture")
        attempt = self.store.snapshot()["records"]["review_attempt"]["attempt-turn-1"]
        self.assertEqual((attempt["status"], attempt["error"]["code"]), ("failed", "review_invocation_interrupted"))
        receipt = self.invoke("turn-1", answer, attempt="attempt-turn-1-retry")
        self.assertEqual(receipt["result"]["status"], "completed")

    def test_finish_refused_beyond_the_wait_is_recorded_by_the_same_request_without_a_call(self):
        answer = self.build_reading_response(self.packet)
        refusal = self.refuse_beyond_the_wait("review.invoke", lambda: self.invoke("turn-1", answer))
        self.assertEqual((refusal.code, refusal.message), ("store_busy", protocol.CALL_FINISH_STORE_BUSY_MSG))
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertNotIn("attempt-turn-1", self.store.snapshot()["records"].get("review_attempt", {}))
        # The way on that the message names: the same payload and request ID record the result from the capture.
        with mock.patch.object(transport, "send_request", side_effect=AssertionError("A second reviewer call")):
            receipt = protocol.invoke_assignment(self.store, {"id": "attempt-turn-1", "assignment_id": "turn-1"},
                                                 credential="test-only", expected_revision=self.store.revision,
                                                 request_id="invoke-attempt-turn-1")
        self.assertEqual(receipt["result"]["status"], "completed")


class RecordingRefusalTests(OtherWriters, delivery_fixtures.ProblemDeliveryCase):
    """The record of a reviewer output that fails a check of its contract (review.refusal), after a recording command
    that was refused."""

    prepare_reviews = resolution_tests.ReviewOutputRefusalTests.prepare_reviews
    prepare_problem_evidence = resolution_tests.ReviewOutputRefusalTests.prepare_problem_evidence
    make_objection = resolution_tests.ReviewOutputRefusalTests.make_objection
    make_finding = resolution_tests.ReviewOutputRefusalTests.make_finding
    build_slate_output = resolution_tests.ReviewOutputRefusalTests.build_slate_output
    records = resolution_tests.ReviewOutputRefusalTests.records
    prepare_bar = resolution_tests.ReviewOutputRefusalTests.prepare_bar
    invoke_output = resolution_tests.ReviewOutputRefusalTests.invoke_output

    def setUp(self):
        super().setUp()
        self.output = self.prepare_bar()
        self.invoke_output(self.output)
        self.start_other_writers()

    def record(self, request, *options):
        payload = dict(self.output, id="finding-bar-first", assignment_id="bar-first", dossier_id=None)
        path = self.root / "refusal-payload.json"
        path.write_text(json.dumps(payload))
        return cli.run(cli.build_parser().parse_args(["value-review", "--workspace", str(self.root), "--file", str(path),
            "--expected-revision", str(self.store.revision), "--request-id", request, *options]))

    def refusals(self):
        return self.records().get("review_output_refusal", {})

    def test_recording_command_records_the_refusal_when_a_commit_meets_every_attempt(self):
        self.meet_commits(("review.refusal",))
        with self.assertRaises(ResearchError) as caught:
            self.record("record-refused")
        self.assertEqual(caught.exception.code, "review_evidence_undelivered")
        self.assertEqual(self.attempts, {"review.refusal": 1})
        refusal = self.refusals()["refused-1"]
        self.assertEqual((refusal["code"], sorted(refusal)), ("review_evidence_undelivered", REFUSAL_MEMBERS_0_91_7))
        committed = self.store.committed_request("record-refused:refusal")
        self.assertEqual((committed["operation"], committed["response"]["revision"]),
                         ("review.refusal", refusal["recorded_revision"]))

    def test_recording_with_a_stale_revision_ends_at_once_and_records_no_refusal(self):
        from research_harness import research_decisions
        payload = dict(self.output, id="finding-bar-first", assignment_id="bar-first", dossier_id=None)
        with self.assertRaises(ResearchError) as caught:
            research_decisions.record_value_review(self.store, payload, expected_revision=self.store.revision - 1,
                                                   request_id="stale-recording")
        self.assertEqual(caught.exception.code, "stale_revision")
        self.assertEqual(self.refusals(), {})

    def test_check_of_a_refused_recording_records_no_refusal(self):
        revision = self.store.revision
        with self.assertRaises(ResearchError) as caught:
            self.record("checked-recording", "--check")
        self.assertEqual(caught.exception.code, "review_evidence_undelivered")
        self.assertEqual((self.refusals(), self.store.revision), ({}, revision))

    def test_refusal_refused_beyond_the_wait_is_reported_and_the_next_recording_records_it(self):
        refusal = self.refuse_beyond_the_wait("review.refusal", lambda: self.record("busy-recording"))
        self.assertEqual((refusal.code, refusal.message),
                         ("store_busy", review_refusal.OUTPUT_REFUSAL_AFTER_RECORDING_STORE_BUSY_MSG))
        self.assertIn(refusal.details["cause"]["code"], finishing.SCOPED_FINISH_RETRY_CODES)
        self.assertEqual(refusal.details["recording_refusal"]["code"], "review_evidence_undelivered")
        self.assertEqual((refusal.details["assignment_id"], refusal.details["attempt_id"], refusal.details["refusal"]["code"]),
                         ("bar-first", "refused-1", "review_evidence_undelivered"))
        self.assertEqual(self.refusals(), {})
        # The way on that the message names: the same recording command again is refused as before and records it.
        with self.assertRaises(ResearchError) as caught:
            self.record("after-busy")
        self.assertEqual(caught.exception.code, "review_evidence_undelivered")
        self.assertIn("refused-1", self.refusals())
