"""Owned transport groups, signals and default queue waiting across processes."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from research_harness import cli, review_task_execution as execution, review_worker as worker
import test_review_stop_queue as stops


PROGRAM = r'''
import json, subprocess, sys, time
from pathlib import Path
from unittest.mock import patch
from research_harness import cli, review_protocol as protocol, review_series as series
from research_harness import review_call_capture
from research_harness.operations import immutable_record
from research_harness.storage import Store
root, mode, identifier, assignment = sys.argv[1:]
root, store = Path(root), Store(Path(root))
request = store.build_artifact_store().put(b'{"history":[],"packet":{}}','application/json')
def operate(store, payload, **identity):
    identifier = payload['id']
    def start(records, value):
        row = dict(value, route_id='route', request=request)
        return [immutable_record(records,'review_invocation',identifier,row)], row
    def observe(*args):
        (root / (identifier + '-started')).write_text('started')
        if mode == 'series-http':
            while True: time.sleep(.05)
        if mode == 'signal-after-capture':
            return dict(status='completed',output=request,response=None,request=request,usage={})
        if mode in ('queue', 'series-queue'):
            while not (root / (identifier + '-finish')).exists(): time.sleep(.02)
        else:
            program = 'import time; time.sleep(300)'
            if mode == 'ignore-term':
                program = ('import signal, sys, time; from pathlib import Path; '
                    'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                    'Path(sys.argv[1]).write_text("ready"); time.sleep(300)')
            process = subprocess.Popen([sys.executable,'-c',program,str(root / 'ignore-ready')])
            (root / (identifier + '-child')).write_text(str(process.pid))
            process.wait(timeout=60)
        return dict(status='failed',output=None,response=None,request=request,usage={})
    def finish(records, value, invocation, observation, revision):
        row=dict(invocation,**observation,recorded_revision=revision)
        return [immutable_record(records,'review_attempt',identifier,row)],row
    with patch.object(protocol,'_observed',side_effect=observe):
        return protocol._invoke_once(store,payload,'review.invoke',start,finish,
            credential=None,**identity)
payload=dict(id=identifier,assignment_id=assignment)
if mode in ('series', 'signal-after-capture', 'signal-during-start', 'series-queue', 'series-wait', 'series-http'):
    if mode == 'signal-during-start':
        from research_harness import review_task_execution as execution
        original_start = execution.start_call
        def start_call(*args, **kwargs):
            original_start(*args, **kwargs)
            import os, signal
            os.kill(os.getpid(), signal.SIGTERM)
        execution.start_call = start_call
    if mode == 'signal-after-capture':
        original = review_call_capture.capture_call
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            import os, signal
            os.kill(os.getpid(), signal.SIGTERM)
            return result
        review_call_capture.capture_call = capture
    def plan(*args):
        snapshot=store.snapshot()
        if mode=='series-wait':
            from datetime import datetime, timedelta, timezone
            (root / 'waiting-started').write_text('waiting')
            return snapshot,dict(state='wait',chain='fixture',head_id=assignment,progress={},
                command='review-run',payload=payload,not_before=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(),
                provider_usage=dict(windows=[],warnings=[]))
        count=len(snapshot['records'].get('review_attempt',{}))
        done=mode=='series-queue' and count>=3
        current=dict(payload,id='next') if mode=='series-queue' and 'call' in snapshot['records'].get('review_attempt',{}) else payload
        return snapshot,dict(state='done' if done else 'run',chain='fixture',head_id=assignment,progress=dict(attempts=count),
            command='review-run',payload=current,provider_usage=dict(windows=[],warnings=[]))
    with patch.dict(cli.OPERATIONS,{'review-run':operate}),patch.object(series,'_read_plan',side_effect=plan), \
         patch.object(series,'render_workspace_views',return_value=None):
        sys.exit(series.run_series(store,assignment))
else:
    operate(store,payload,expected_revision=store.revision,request_id='invoke-'+identifier)
'''


class StopProcessTests(unittest.TestCase):
    def setUp(self):
        self.case = stops.StopQueueTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.store = self.case.root, self.case.store

    def launch(self, mode, identifier='call', assignment='first'):
        process = subprocess.Popen([sys.executable, '-c', PROGRAM, str(self.root), mode, identifier, assignment],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        def cleanup():
            records = self.store.snapshot()['records']
            for member in records.get('review_pool_membership', {}).values():
                if (member.get('owner') or {}).get('pid') != process.pid:
                    continue
                for child in member.get('children', []):
                    table = worker._process_table([child])
                    if child['pgid'] != os.getpgrp() and worker._has_processes(child, table):
                        try:
                            os.killpg(child['pgid'], signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=30)
        self.addCleanup(cleanup)
        return process

    def await_file(self, name, process=None):
        deadline = time.monotonic() + 20
        while not (self.root / name).exists():
            if process is not None and process.poll() is not None:
                out, err = process.communicate()
                self.fail('Process exited before ' + name + ': ' + out + err)
            self.assertLess(time.monotonic(), deadline, 'Process did not reach ' + name)
            time.sleep(.02)
        return (self.root / name).read_text()

    def await_registration(self):
        deadline = time.monotonic() + 20
        key = execution.identify_call('review.invoke', 'call')
        while not self.store.snapshot()['records']['review_pool_membership'][key]['children']:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)

    def test_now_ends_registered_transport_and_owner_records_attempt_before_return(self):
        process = self.launch('single')
        pid = int(self.await_file('call-child'))
        self.await_registration()
        result = self.case.stop()
        out, err = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, out + err)
        self.assertEqual(result['call_states'][0]['state'], 'settled')
        self.assertIsNone(worker._process_table([dict(pid=pid, pgid=pid, started='unused')]).get(pid))
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_attempt']['call']['status'], 'failed')
        self.assertTrue(records['review_pool_membership'][execution.identify_call('review.invoke','call')]['released'])

    def test_now_kills_an_owned_transport_after_ten_seconds_when_it_ignores_sigterm(self):
        process = self.launch('ignore-term')
        self.await_file('ignore-ready')
        self.await_registration()
        started = time.monotonic()
        stopped = self.case.stop()
        out, err = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, out + err)
        self.assertGreaterEqual(time.monotonic() - started, 10)
        self.assertEqual(stopped['call_states'][0]['state'], 'settled')

    def test_now_ends_a_staged_worker_and_reconciles_its_reservation(self):
        import test_review_task_execution_worker as workers
        reservation = self.case.reserve()['result']
        program = workers._FIXTURE.replace('MODE', repr('slow')).replace(
            'review_worker.run_worker(Path(sys.argv[1])',
            'review_worker.capture_observation = lambda *args, **kwargs: time.sleep(300)\n'
            'review_worker.run_worker(Path(sys.argv[1])')
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', program]):
            process = worker.launch_worker(self.store, reservation['id'])
        def cleanup():
            (self.root / 'release-child').touch()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=20)
        self.addCleanup(cleanup)
        self.await_file('calls')
        stopped = self.case.stop()
        process.wait(timeout=20)
        current = self.store.snapshot()['records']['review_reservation'][reservation['id']]
        self.assertTrue(current['released'])
        self.assertEqual(current['state'], 'unknown')
        self.assertEqual(stopped['call_states'][0]['state'], 'settled')

    def assert_driver_signal(self, number):
        process = self.launch('series')
        self.await_file('call-child')
        self.await_registration()
        process.send_signal(number)
        out, err = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 3, out + err)
        last = json.loads(out.splitlines()[-1])
        self.assertEqual((last['state'], last['code'], last['stop_class']), ('stop','review_chain_stopped','requested'))
        records = self.store.snapshot()['records']
        self.assertEqual(next(iter(records['review_stop'].values()))['origin'], 'signal')
        self.assertIn('call', records['review_attempt'])
        self.assertNotIn('review_series_stop', records)
        driver = next(iter(records['review_series_run'].values()))
        self.assertEqual(driver['ended']['stop_id'], last['stop_id'])

    def test_sigterm_driver_records_signal_stop_attempt_and_acknowledgement(self):
        self.assert_driver_signal(signal.SIGTERM)

    def test_sigint_driver_records_signal_stop_attempt_and_acknowledgement(self):
        self.assert_driver_signal(signal.SIGINT)

    def test_sigkill_leaves_no_boundary_and_the_call_remains_interrupted(self):
        process = self.launch('series')
        self.await_file('call-child')
        self.await_registration()
        process.kill()
        process.wait(timeout=20)
        records = self.store.snapshot()['records']
        self.assertNotIn('review_stop', records)
        key = execution.identify_call('review.invoke', 'call')
        children = records['review_pool_membership'][key]['children']
        for child in children:
            if worker._has_processes(child, worker._process_table([child])):
                try:
                    os.killpg(child['pgid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 20
        while any(worker._has_processes(child, worker._process_table([child])) for child in children):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        process.communicate(timeout=20)
        execution.reconcile_call(self.store, dict(operation='review.invoke', invocation_id='call'), request_id='reconcile')
        from research_harness import review_failure_class, review_stop
        records = self.store.snapshot()['records']
        self.assertEqual(review_failure_class.classify_attempt(records, self.store.build_artifact_store(),
                         records['review_attempt']['call'])['outcome'], 'interrupted')
        next_driver = review_stop.start_driver(self.store, 'first')
        records = self.store.snapshot()['records']
        predecessor = next(row for row in records['review_series_run'].values() if row['id'] != next_driver['id'])
        self.assertEqual(predecessor['ended']['code'], 'process_gone')
        review_stop.end_driver(self.store, next_driver['id'], dict(state='done'))

    def test_store_stop_lets_two_signalled_drivers_finish_their_own_calls(self):
        route = self.store.snapshot()['records']['review_route']['route']
        self.case.put('review_route', 'route', dict(route, adapter='openai_responses_v1'))
        execution.record_capacity(self.store, dict(store_limit=2, route_limits={}), request_id='two-calls')
        processes = [self.launch('series-http', 'one', 'first'), self.launch('series-http', 'two', 'other')]
        self.await_file('one-started', processes[0])
        self.await_file('two-started', processes[1])
        stopped = self.case.stop(scope='store')
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 3, out + err)
            self.assertEqual(json.loads(out.splitlines()[-1])['stop_id'], stopped['id'])
        self.assertEqual(len(stopped['call_states']), 2)
        self.assertTrue(all(row['state'] == 'settled' for row in stopped['call_states']))
        self.assertNotIn('review_series_stop', self.store.snapshot()['records'])

    def test_twenty_minute_waiting_driver_acknowledges_within_ten_seconds(self):
        process = self.launch('series-wait')
        self.await_file('waiting-started')
        started = time.monotonic()
        stopped = self.case.stop('after_turn')
        out, err = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 3, out + err)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(json.loads(out.splitlines()[-1])['stop_id'], stopped['id'])
        self.case.release()

    def test_signal_during_start_transaction_records_boundary_after_rollback(self):
        process = self.launch('signal-during-start')
        out, err = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 3, out + err)
        records = self.store.snapshot()['records']
        self.assertNotIn('call', records.get('review_invocation', {}))
        self.assertEqual(next(iter(records['review_stop'].values()))['origin'], 'signal')
        self.assertEqual(json.loads(out.splitlines()[-1])['code'], 'review_chain_stopped')

    def test_signal_after_capture_finishes_the_attempt_before_acknowledgement(self):
        process = self.launch('signal-after-capture')
        out, err = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 3, out + err)
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_attempt']['call']['status'], 'completed')
        self.assertTrue(records['review_pool_membership'][execution.identify_call('review.invoke','call')]['released'])
        self.assertNotIn('review_series_stop', records)

    def test_single_waits_then_starts_before_a_chains_next_call(self):
        first = self.launch('series-queue')
        self.await_file('call-started')
        single = self.launch('queue', 'single', 'other')
        deadline = time.monotonic() + 20
        while not self.store.snapshot()['records'].get('review_call_wait'):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        self.assertFalse((self.root / 'single-started').exists())
        (self.root / 'call-finish').write_text('release')
        self.await_file('single-started')
        self.assertFalse((self.root / 'next-started').exists())
        (self.root / 'single-finish').write_text('release')
        out, err = single.communicate(timeout=20)
        self.assertEqual(single.returncode, 0, out + err)
        self.await_file('next-started')
        (self.root / 'next-finish').write_text('release')
        out, err = first.communicate(timeout=20)
        self.assertEqual(first.returncode, 0, out + err)
        waits = list(self.store.snapshot()['records']['review_call_wait'].values())
        self.assertTrue(waits)
        self.assertTrue(all(row['state'] == 'admitted' for row in waits))
