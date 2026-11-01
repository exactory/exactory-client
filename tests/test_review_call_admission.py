"""Every local call uses the same atomic store and route capacity pools."""

import json
import subprocess
import sys
import time
from unittest.mock import patch

from research_harness import review_protocol as protocol
from research_harness.operations import immutable_record
from test_review_task_execution import TaskExecutionFixture


CALLS = (('review.invoke', 'review_invocation', 'review_attempt'),
         ('review.route_probe', 'review_probe_invocation', 'review_route_probe'),
         ('review.output_correction', 'review_output_correction_invocation', 'review_output_correction'))


class CallAdmissionTests(TaskExecutionFixture):
    def invoke(self, operation, kind, completed, observe=None, identifier='ordinary'):
        payload = dict(id=identifier, route_id='route')

        def start(records, value):
            record = dict(value, request=self.request)
            return [immutable_record(records, kind, identifier, record)], record

        def finish(records, value, invocation, observation, revision):
            record = dict(invocation, **observation, recorded_revision=revision)
            return [immutable_record(records, completed, identifier, record)], record

        with patch.object(protocol, '_observed', side_effect=observe or (
                lambda *args: dict(status='failed', request=self.request, response=None, output=None))):
            return protocol._invoke_once(self.store, payload, operation, start, finish,
                expected_revision=self.store.revision, request_id=identifier, credential=None)

    def test_staged_reservation_blocks_every_covered_call_before_model_io(self):
        self.reserve()
        for index, (operation, kind, completed) in enumerate(CALLS):
            with self.subTest(operation=operation):
                self.refuse('review_capacity_wait', lambda: self.invoke(operation, kind, completed,
                    observe=lambda *args: self.fail('Occupied capacity must prevent external I/O'),
                    identifier='blocked-' + str(index)))
                self.assertNotIn('blocked-' + str(index), self.store.snapshot()['records'].get(kind, {}))

    def test_each_live_covered_call_blocks_staged_admission_and_releases_on_finish(self):
        for index, (operation, kind, completed) in enumerate(CALLS):
            with self.subTest(operation=operation):
                def observe(*args):
                    self.refuse('review_capacity_wait', lambda: self.reserve(request_id='during-call'))
                    return dict(status='failed', request=self.request, response=None, output=None)
                self.invoke(operation, kind, completed, observe, identifier='call-' + str(index))
        self.assertEqual(self.reserve()['result']['state'], 'reserved')

    def test_preexisting_probe_and_correction_invocations_without_membership_do_not_occupy_capacity(self):
        for index, (_, kind, completed) in enumerate(CALLS[1:]):
            identifier = 'pending-' + str(index)
            self.put(kind, identifier, dict(id=identifier, route_id='route'))
        self.assertEqual(self.reserve()['result']['state'], 'reserved')

    def test_ordinary_dispatch_and_replay_use_one_preparation_read(self):
        read = self.store._read_preparation
        reads_before_start = []

        def prepare():
            snapshot = read()
            if not snapshot.has_request('ordinary:start'):
                reads_before_start.append(snapshot.revision)
            return snapshot

        with patch.object(self.store, '_read_preparation', side_effect=prepare):
            first = self.invoke(*CALLS[0])
        self.assertEqual(len(reads_before_start), 1)
        with patch.object(self.store, '_read_preparation', wraps=read) as replay:
            self.assertEqual(self.invoke(*CALLS[0]), first)
        self.assertEqual(replay.call_count, 1)

    def test_route_limit_is_shared_even_when_the_store_has_room(self):
        from research_harness import review_task_execution as execution
        execution.record_capacity(self.store, dict(store_limit=3, route_limits={'route':1}), request_id='route-cap')
        self.reserve()
        self.refuse('review_capacity_wait', lambda: self.invoke(*CALLS[1]))

    def test_explicit_capacity_allows_a_staged_and_ordinary_call_together(self):
        from research_harness import review_task_execution as execution
        execution.record_capacity(self.store, dict(store_limit=2, route_limits={}), request_id='two')
        self.reserve()
        result = self.invoke(*CALLS[0])
        self.assertEqual(result['result']['status'], 'failed')

    def test_separate_processes_cannot_admit_an_ordinary_call_and_staged_call_to_one_slot(self):
        program = '''import json, sys, time
from pathlib import Path
from research_harness import review_protocol as protocol, review_task_execution as execution
from research_harness.errors import ResearchError
from research_harness.operations import immutable_record
from research_harness.storage import Store
root, mode, raw = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
store = Store(root)
def report(value): (root / ('result-' + mode)).write_text(json.dumps(value))
while not (root / 'race-gate').exists(): time.sleep(.005)
try:
    if mode == 'staged':
        execution.reserve_task(store, raw, request_id='race-staged')
        report({'admitted': True})
    else:
        def start(records, value):
            row = dict(id='ordinary', route_id='route', request=raw['request'])
            return [immutable_record(records, 'review_invocation', 'ordinary', row)], row
        def observe(*args):
            report({'admitted': True})
            while not (root / 'race-release').exists(): time.sleep(.005)
            return {'status': 'failed'}
        def finish(records, value, invocation, observation, revision):
            row = dict(invocation, **observation)
            return [immutable_record(records, 'review_attempt', 'ordinary', row)], row
        protocol._observed = observe
        protocol._invoke_once(store, {'id':'ordinary'}, 'review.invoke', start, finish,
            expected_revision=store.revision, request_id='race-ordinary', credential=None)
except ResearchError as error: report({'error':error.code})
'''
        processes = [subprocess.Popen([sys.executable, '-c', program, str(self.root), mode,
            json.dumps(self.payload())], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for mode in ('ordinary', 'staged')]
        try:
            (self.root / 'race-gate').touch()
            deadline = time.monotonic() + 60
            paths = [self.root / ('result-' + mode) for mode in ('ordinary', 'staged')]
            while not all(path.exists() for path in paths) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(all(path.exists() for path in paths), 'Both contenders must report admission')
            results = [json.loads(path.read_text()) for path in paths]
            self.assertEqual(sum(row.get('admitted', False) for row in results), 1, results)
            self.assertIn(next(row['error'] for row in results if 'error' in row),
                          ('review_capacity_wait', 'stale_revision'))
        finally:
            (self.root / 'race-release').touch()
            for process in processes:
                try:
                    _, errors = process.communicate(timeout=60)
                    self.assertEqual(process.returncode, 0, errors)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=60)
