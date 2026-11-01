"""Detached owned workers capture once and keep capacity until termination."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest import mock

from research_harness import review_task_execution as execution
from research_harness import review_worker
from research_harness.errors import ResearchError
from research_harness.storage import Store
from tests.test_review_task_execution import TaskExecutionFixture


class SharedProcessIdentityTests(TaskExecutionFixture):
    def test_worker_uses_neutral_kernel_identity_and_keeps_its_record_shape(self):
        from research_harness import probe_supervision
        self.assertTrue(hasattr(probe_supervision, 'find_process_identity'))
        with mock.patch.object(probe_supervision, 'find_process_identity', return_value={
                'pid': 123, 'process_group': 120, 'start_identity': [10, 20]}):
            self.assertEqual(review_worker._identity(123), {'pid': 123, 'pgid': 120, 'started': '10:20'})

    def test_group_survivor_is_retained_when_the_original_leader_is_gone(self):
        from research_harness import probe_supervision
        self.assertTrue(hasattr(probe_supervision, 'find_process_group_members'))
        original = {'pid': 123, 'pgid': 120, 'started': '10:20'}
        with mock.patch.object(probe_supervision, 'find_process_identity', return_value=None), \
                mock.patch.object(probe_supervision, 'find_process_group_members', return_value=[
                    {'pid': 124, 'process_group': 120, 'start_identity': [30, 40]}]):
            self.assertTrue(review_worker._has_processes(original, review_worker._process_table([original])))

    def test_unreadable_kernel_identity_requires_reconciliation(self):
        from research_harness import probe_supervision
        self.assertTrue(hasattr(probe_supervision, 'find_process_identity'))
        with mock.patch.object(probe_supervision, 'find_process_identity', side_effect=PermissionError('denied')):
            self.refuse('review_worker_reconciliation_required', lambda: review_worker._identity(123))

    def test_linux_worker_reads_kernel_ticks_without_spawning_ps(self):
        from research_harness import probe_supervision
        data = '123 (a command with ) parentheses) S 1 120 ' + '0 ' * 16 + '98765 0'
        with mock.patch.object(probe_supervision.sys, 'platform', 'linux'), \
                mock.patch.object(Path, 'read_text', return_value=data), \
                mock.patch.object(review_worker, '_SYSTEM_POPEN', side_effect=AssertionError('No ps process')):
            self.assertEqual(review_worker._identity(123), {'pid': 123, 'pgid': 120, 'started': '98765'})


# A real external process supplies output through the same subprocess ownership boundary as the CLI adapters.
_FIXTURE = r'''
import json, os, subprocess, sys, time
from pathlib import Path
from research_harness import review_worker, review_task_execution as execution
from research_harness.storage import _canonical
from research_harness.errors import ResearchError
mode = MODE

def observe(store, route, request):
    command = [sys.executable, '-c', "from pathlib import Path; import sys,time; p=Path(sys.argv[1]); p.open('a').write('call\\n'); time.sleep(float(sys.argv[2])); print('{\\\"answer\\\":42}')", str(store.root / 'calls'), '1.0' if mode in ('slow', 'kill') else '0.01']
    if mode == 'lingering-child':
        command = [sys.executable, '-c', "from pathlib import Path; import subprocess,sys; Path(sys.argv[1]).open('a').write('call\\n'); subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'], close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print('{\\\"answer\\\":42}')", str(store.root / 'calls')]
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    stdout, stderr = child.communicate(timeout=5)
    artifacts = store.build_artifact_store()
    output = {'answer': 42}
    response = artifacts.put(stdout, 'application/json')
    (store.root / 'response-artifact.json').write_text(json.dumps(response))
    return {'status': 'failed' if mode == 'known-failure' else 'completed', 'request': artifacts.put(_canonical(request).encode(), 'application/json'),
            'response': response, 'output': artifacts.put(_canonical(output).encode(), 'application/json'),
            'error': None}
review_worker._observe_live = observe
if mode == 'instrumented':
    from research_harness import review_protocol, review_transport
    review_protocol._require_route_runtime = lambda route: None
    review_transport.runtime = lambda route=None: {}
    def send(route, request, credential):
        from research_harness.storage import Store
        record = observe(Store(Path(sys.argv[1])), route, request)
        return {'id': 'fixture-response', 'status': 'completed', 'model': 'fixture', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': '{"answer":42}'}]}]}
    review_transport.send_request = send
    from research_harness.review_protocol import _observed
    review_worker._observe_live = lambda store, route, request: _observed(store.build_artifact_store(), route, request, None)
original = review_worker.capture_observation
first = True

def capture(*args, **kwargs):
    global first
    if mode == 'before-capture': os._exit(71)
    result = original(*args, **kwargs)
    if mode == 'lost-ack' and first:
        first = False
        raise ResearchError('store_busy', 'Lost commit acknowledgment')
    if mode == 'after-capture': os._exit(72)
    if mode == 'hold-after-capture':
        (Path(sys.argv[1]) / 'captured-live').touch()
        time.sleep(2)
    return result
review_worker.capture_observation = capture
review_worker.run_worker(Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]))
'''


class WorkerExecutionTests(TaskExecutionFixture):
    def entry(self, mode):
        return [sys.executable, "-c", _FIXTURE.replace("MODE", repr(mode))]

    def launch(self, reservation, mode="normal"):
        with mock.patch.object(review_worker, "_entry_command", return_value=self.entry(mode)):
            process = review_worker.launch_worker(self.store, reservation["id"], handshake_timeout=4)
        self.addCleanup(self.stop, process)
        return process

    def stop(self, process):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=4)
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    def wait_for(self, condition, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        self.fail("Timed out waiting for the worker transition")

    def current(self, reservation):
        return self.store.snapshot()["records"]["review_reservation"][reservation["id"]]

    def accept(self, reservation, request_id="accept", validate=None):
        def verify(records, observation):
            output = json.loads(self.store.build_artifact_store().read(observation["observation"]["output"]))
            self.assertEqual(output, {"answer": 42})
            return {"answer": 42}
        return execution.accept_observation(self.store, {"reservation_id": reservation["id"]},
                                            validate or verify, request_id=request_id)

    def reconcile(self, reservation):
        return execution.reconcile_worker(self.store, {"reservation_id": reservation["id"]},
                                           request_id="reconcile-" + str(self.store.revision))

    def test_crash_after_capture_is_resumable_and_does_not_call_again(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "after-capture")
        self.assertEqual(process.wait(timeout=6), 72)
        self.assertEqual(self.current(reservation)["state"], "captured")
        result = self.accept(reservation)
        self.assertEqual(self.accept(reservation), result)
        self.refuse("review_resampling_forbidden", lambda: self.reserve(request_id="another"))
        self.reconcile(reservation)
        self.assertTrue(self.current(reservation)["released"])
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])

    def test_response_artifact_without_capture_is_unknown_and_never_accepted(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "before-capture")
        self.assertEqual(process.wait(timeout=6), 71)
        self.assertTrue((self.root / "response-artifact.json").exists())
        self.reconcile(reservation)
        self.assertEqual(self.current(reservation)["state"], "unknown")
        self.refuse("review_observation_acceptance_pending", lambda: self.accept(reservation))
        retry = self.reserve(request_id="bounded-retry")["result"]
        self.assertEqual(retry["attempt"], 2)
        self.assertNotEqual(retry["id"], reservation["id"])

    def test_worker_death_before_capture_retains_child_capacity_until_child_exits(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "kill")
        self.wait_for(lambda: (self.root / "calls").exists())
        process.kill()
        process.wait(timeout=4)
        self.reconcile(reservation)
        self.assertFalse(self.current(reservation)["released"])
        self.refuse("review_capacity_wait", lambda: self.reserve("b"))
        self.wait_for(lambda: review_worker.confirm_termination(self.store, reservation["id"]))
        self.reconcile(reservation)
        self.assertEqual(self.current(reservation)["state"], "unknown")
        self.assertTrue(self.current(reservation)["released"])

    def test_capture_receipt_recovers_a_lost_commit_acknowledgment(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "lost-ack")
        self.assertEqual(process.wait(timeout=7), 0)
        self.assertEqual(self.current(reservation)["state"], "captured")
        receipt = self.store.committed_request("review-capture:" + reservation["id"])["response"]
        self.assertEqual(receipt["result"]["recorded_revision"], receipt["revision"])
        self.accept(reservation)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])

    def test_acceptance_can_commit_while_worker_live_but_release_waits(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "hold-after-capture")
        self.wait_for(lambda: (self.root / "captured-live").exists())
        self.accept(reservation)
        self.assertEqual(self.current(reservation)["state"], "accepted")
        self.reconcile(reservation)
        self.assertFalse(self.current(reservation)["released"])
        self.refuse("review_worker_reconciliation_required", lambda: execution.release_reservation(
            self.store, {"reservation_id": reservation["id"]}, request_id="too-early"))
        process.wait(timeout=5)
        self.reconcile(reservation)
        self.assertTrue(self.current(reservation)["released"])

    def test_late_capture_is_historical_and_changed_own_dependencies_block_acceptance(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "slow")
        self.wait_for(lambda: (self.root / "calls").exists())
        self.put("review_epoch", "epoch", {"id": "epoch", "generation": 2, "context": "successor"})
        self.assertEqual(process.wait(timeout=6), 0)
        self.assertEqual(self.current(reservation)["state"], "captured")
        self.refuse("review_task_dependency_changed", lambda: self.accept(reservation))
        self.assertIn(reservation["id"], self.store.snapshot()["records"]["review_observation"])

    def test_sibling_commit_during_acceptance_keeps_preparation(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=6), 0)
        def validate(records, observation):
            self.put("review_task", "b", {"id": "b", "generation": 2})
            return {"answer": 42}
        result = self.accept(reservation, validate=validate)
        self.assertEqual(result["revision"], result["result"]["recorded_revision"])

    def test_descendant_process_group_blocks_release_after_worker_and_group_leader_exit(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "lingering-child")
        self.assertEqual(process.wait(timeout=6), 0)
        self.accept(reservation)
        self.assertFalse(review_worker.confirm_termination(self.store, reservation["id"]))
        self.reconcile(reservation)
        self.assertFalse(self.current(reservation)["released"])
        self.wait_for(lambda: review_worker.confirm_termination(self.store, reservation["id"]))
        self.reconcile(reservation)
        self.assertTrue(self.current(reservation)["released"])

    def test_existing_instrumented_observer_keeps_raw_provenance(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "instrumented")
        self.assertEqual(process.wait(timeout=6), 0)
        captured = self.store.snapshot()["records"]["review_observation"][reservation["id"]]
        self.assertEqual(captured["origin"], "instrumented")
        self.assertEqual(captured["observation"]["request_digest"],
                         self.store.snapshot()["records"]["review_execution_plan"][reservation["plan_id"]]["request_digest"])
        self.accept(reservation)

    def test_own_selector_and_absence_changes_during_acceptance_invalidate_preparation(self):
        payload = self.payload()
        payload["absence"] = [{"kind": "terminal_selection", "key": "slot"}]
        reservation = execution.reserve_task(self.store, payload, request_id="with-absence")["result"]
        process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=6), 0)
        def validate(records, observation):
            self.put("terminal_selection", "slot", {"frozen": True})
            return {"answer": 42}
        self.refuse("review_task_dependency_changed", lambda: self.accept(reservation, validate=validate))
        self.assertNotIn("review_task_acceptance", self.store.snapshot()["records"])
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])

    def test_duplicate_launch_never_starts_another_worker(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "slow")
        self.refuse("review_invocation_pending", lambda: review_worker.launch_worker(self.store, reservation["id"]))
        self.assertEqual(process.wait(timeout=6), 0)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])

    def test_observed_known_failure_is_captured_and_retry_waits_for_termination(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation, "known-failure")
        self.assertEqual(process.wait(timeout=6), 0)
        self.assertEqual(self.current(reservation)["state"], "known_failure")
        self.refuse("review_task_failed", lambda: self.accept(reservation))
        self.refuse("review_invocation_pending", lambda: self.reserve(request_id="early-retry"))
        self.reconcile(reservation)
        self.assertEqual(self.reserve(request_id="eligible-retry")["result"]["attempt"], 2)

    def test_selector_change_during_acceptance_does_not_credit_stale_science(self):
        reservation = self.reserve()["result"]
        process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=6), 0)
        def validate(records, observation):
            self.put("context", "slot", {"version": 2})
            return {"answer": 42}
        self.refuse("review_task_dependency_changed", lambda: self.accept(reservation, validate=validate))
        self.assertNotIn("review_task_acceptance", self.store.snapshot()["records"])

    def test_controller_death_before_token_release_never_calls_reviewer(self):
        reservation = self.reserve()["result"]
        program = '''import sys, time
from pathlib import Path
from unittest import mock
from research_harness.storage import Store
from research_harness import review_worker
original = review_worker._authorize_launch
def authorize(*args):
    result = original(*args)
    Path(sys.argv[1], 'controller-ready').touch()
    time.sleep(20)
    return result
with mock.patch.object(review_worker, '_entry_command', return_value=ENTRY), mock.patch.object(review_worker, '_authorize_launch', side_effect=authorize):
    review_worker.launch_worker(Store(Path(sys.argv[1])), sys.argv[2], handshake_timeout=4)
'''.replace("ENTRY", repr(self.entry("normal")))
        controller = subprocess.Popen([sys.executable, "-c", program, str(self.root), reservation["id"]],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, controller)
        self.wait_for(lambda: (self.root / "controller-ready").exists())
        controller.kill()
        controller.wait(timeout=4)
        self.wait_for(lambda: review_worker.confirm_termination(self.store, reservation["id"]))
        self.reconcile(reservation)
        self.assertEqual(self.current(reservation)["state"], "known_failure")
        self.assertFalse((self.root / "calls").exists())

    def test_controller_death_after_handshake_does_not_repeat_external_call(self):
        reservation = self.reserve()["result"]
        program = '''import sys, time
from pathlib import Path
from unittest import mock
from research_harness.storage import Store
from research_harness import review_worker
with mock.patch.object(review_worker, '_entry_command', return_value=ENTRY):
    process = review_worker.launch_worker(Store(Path(sys.argv[1])), sys.argv[2], handshake_timeout=4)
Path(sys.argv[1], 'controller-ready').touch()
time.sleep(20)
'''.replace("ENTRY", repr(self.entry("slow")))
        controller = subprocess.Popen([sys.executable, "-c", program, str(self.root), reservation["id"]],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, controller)
        self.wait_for(lambda: (self.root / "controller-ready").exists())
        controller.kill()
        controller.wait(timeout=4)
        self.refuse("review_invocation_pending", lambda: self.reserve(request_id="resume"))
        self.wait_for(lambda: self.current(reservation)["state"] == "captured")
        self.accept(reservation)
        self.wait_for(lambda: review_worker.confirm_termination(self.store, reservation["id"]))
        self.reconcile(reservation)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])

    def test_known_launch_failure_and_retry_budget(self):
        for index in range(2):
            reservation = self.reserve(request_id="attempt-" + str(index))["result"]
            with mock.patch.object(review_worker.subprocess, "Popen", side_effect=OSError("No worker")):
                self.refuse("review_worker_reconciliation_required", lambda: review_worker.launch_worker(
                    self.store, reservation["id"], handshake_timeout=.2))
            self.reconcile(reservation)
            self.assertEqual(self.current(reservation)["state"], "known_failure")
        self.refuse("review_task_exhausted", lambda: self.reserve(request_id="exhausted"))
