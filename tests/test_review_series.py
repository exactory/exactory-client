"""The series driver dispatches real operations without duplicate reviewer calls."""

import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import threading
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from research_harness import cli
from research_harness.errors import ResearchError
import test_review_continuation as continuation_fixtures


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = continuation_fixtures.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case

    def drive(self):
        from research_harness.review_series import run_series
        output = io.StringIO()
        code = run_series(self.case.store, 'bar-first', emit=lambda row: output.write(json.dumps(row) + '\n'))
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_two_source_turns_then_record_and_done(self):
        outputs = []
        for index in range(3):
            output = self.case.build_review_output('bar')
            if index < 2:
                output['source_requests'] = [dict(id='page-' + str(index), kind='inventory_page', page=0)]
                output['value']['status'] = 'unresolved'
            outputs.append(self.case.build_provider_response(output))
        with patch('research_harness.review_transport.send_request', side_effect=outputs) as send:
            code, steps = self.drive()
        self.assertEqual(code, 0)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(steps[-1]['state'], 'done')
        self.assertEqual(len(self.case.read_records()['value_review']), 1)

    def test_busy_and_dependency_refusals_are_replanned(self):
        original = cli.OPERATIONS['review-run']
        calls = []
        def operate(*args, **kwargs):
            calls.append(kwargs['request_id'])
            if len(calls) <= 2:
                raise ResearchError(('store_busy', 'review_task_dependency_changed')[len(calls) - 1], 'fixture')
            return original(*args, **kwargs)
        with patch.dict(cli.OPERATIONS, {'review-run': operate}), patch(
                'research_harness.review_transport.send_request',
                return_value=self.case.build_provider_response(self.case.build_review_output('bar'))) as send:
            code, steps = self.drive()
        self.assertEqual(code, 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len([row for row in steps if row.get('error')]), 2)

    def test_repeated_action_without_progress_stops(self):
        def unrelated_change(store, payload, **identity):
            return store.mutate('fixture.unrelated', {},
                lambda tx: tx.put('unrelated', 'entry', dict(revision=identity['expected_revision'])), **identity)
        with patch.dict(cli.OPERATIONS, {'review-run': unrelated_change}):
            code, steps = self.drive()
        self.assertEqual(code, 3)
        self.assertEqual(steps[-1]['code'], 'review_series_no_progress')

    def test_cli_emits_one_json_line_per_step_and_uses_terminal_exit(self):
        output = io.StringIO()
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(self.case.build_review_output('bar'))), \
                redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            cli.main(['review-series', '--workspace', str(self.case.root), '--assignment', 'bar-first'],
                     render_views=lambda workspace: None)
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual([json.loads(line)['state'] for line in output.getvalue().splitlines()],
                         ['run', 'record', 'done'])

    def test_eight_refusals_stop_without_call(self):
        def busy(*args, **kwargs):
            raise ResearchError('stale_revision', 'fixture')
        with patch.dict(cli.OPERATIONS, {'review-run': busy}):
            code, steps = self.drive()
        self.assertEqual(code, 3)
        self.assertEqual(steps[-1]['code'], 'stale_revision')
        self.assertEqual(len([row for row in steps if row.get('error')]), 8)

    def test_stale_runtime_refusal_hands_off_recording_a_new_route(self):
        initial = self.fixture.plan()
        operation = Mock(side_effect=ResearchError('review_route_runtime_stale', 'fixture stale runtime'))
        with patch.dict(cli.OPERATIONS, {'review-run': operation}):
            code, steps = self.drive()
        self.assertEqual(code, 3)
        operation.assert_called_once()
        self.assertEqual(steps, [dict(state='handoff', chain=initial['chain'],
            head_id='bar-first', progress=initial['progress'], command='review-route',
            code='review_route_runtime_stale',
            details=dict(assignment_id='bar-first', route_id='route'))])

    def test_planner_refusal_is_replanned_from_fresh_snapshot(self):
        from research_harness.review_continuation import plan_next
        calls = []
        def plan(records, artifacts, head):
            calls.append(head)
            if len(calls) <= 2:
                return dict(state='stop', chain='session', head_id=head, progress=None,
                            code='review_task_dependency_changed', details={})
            return plan_next(records, artifacts, head)
        with patch('research_harness.review_series.plan_next', side_effect=plan), patch(
                'research_harness.review_transport.send_request',
                return_value=self.case.build_provider_response(self.case.build_review_output('bar'))) as send:
            code, steps = self.drive()
        self.assertEqual(code, 0, steps[-1])
        self.assertEqual(send.call_count, 1)

    def test_cli_accepts_series_assignment_and_workspace(self):
        args = cli.build_parser().parse_args(['review-series', '--assignment', 'bar-first',
                                               '--workspace', str(self.case.root)])
        self.assertEqual(args.assignment, 'bar-first')

    def test_check_mode_refuses_series_before_opening_workspace(self):
        args = cli.build_parser().parse_args(['review-series', '--assignment', 'head', '--check',
                                             '--workspace', '/unopened-workspace'])
        with self.assertRaises(ResearchError) as caught:
            cli.run(args)
        self.assertEqual(caught.exception.code, 'check_unsupported')

    def test_provider_wait_then_one_fresh_run(self):
        before = datetime.now(timezone.utc)
        policy = dict(state='wait', not_before=(before + timedelta(minutes=10)).isoformat(),
                      code='review_provider_unavailable', details={})
        outputs = [OSError('provider unavailable'),
                   self.case.build_provider_response(self.case.build_review_output('bar'))]
        with patch('research_harness.review_transport.send_request', side_effect=outputs) as send, patch(
                'research_harness.review_failure_class.plan_after_failure', return_value=policy), patch(
                'research_harness.review_series.time.sleep') as sleep:
            code, steps = self.drive()
        self.assertEqual(code, 0, steps[-1])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertIn('wait', [step['state'] for step in steps])

    def test_wait_over_thirty_minutes_exits_four(self):
        policy = dict(state='wait',
            not_before=(datetime.now(timezone.utc) + timedelta(minutes=31)).isoformat(),
            code='review_provider_unavailable', details={})
        with patch('research_harness.review_transport.send_request', side_effect=OSError('quota')) as send, patch(
                'research_harness.review_failure_class.plan_after_failure', return_value=policy), patch(
                'research_harness.review_series.time.sleep') as sleep:
            code, steps = self.drive()
        self.assertEqual(code, 4)
        self.assertEqual(send.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(steps[-1]['not_before'], policy['not_before'])

    def test_second_refusal_ends_the_series_after_two_calls(self):
        output = self.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(output)) as send:
            code, steps = self.drive()
        self.assertEqual(code, 3)
        self.assertEqual(steps[-1]['code'], 'review_slot_exhausted')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(self.case.read_records()['review_output_refusal']), 2)

    def test_driver_restarts_after_capture_without_a_second_call(self):
        from research_harness import review_call_capture
        real = review_call_capture.capture_call
        def capture(*args, **kwargs):
            real(*args, **kwargs)
            raise RuntimeError('driver killed')
        with patch.object(review_call_capture, 'capture_call', side_effect=capture):
            with self.assertRaises(RuntimeError):
                self.fixture.invoke(self.case.build_review_output('bar'))
        with patch('research_harness.review_transport.send_request',
                   side_effect=AssertionError('duplicate reviewer call')) as send:
            code, steps = self.drive()
        self.assertEqual(code, 0, steps[-1])
        self.assertEqual(send.call_count, 0)
        self.assertEqual(steps[0]['state'], 'resume')

    def test_failed_turn_is_replaced_from_its_original_parent(self):
        output = self.case.build_review_output('bar')
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        self.fixture.invoke(output)
        first_plan = self.fixture.plan()
        self.fixture.execute(first_plan)
        outputs = [OSError('failed turn'),
                   self.case.build_provider_response(self.case.build_review_output('bar'))]
        with patch('research_harness.review_transport.send_request', side_effect=outputs) as send, patch(
                'research_harness.review_failure_class.plan_after_failure',
                return_value=dict(state='replace', code='fixture_failure', details={})):
            code, steps = self.drive()
        self.assertEqual(code, 0, steps[-1])
        self.assertEqual(send.call_count, 2)
        replacement = next(step for step in steps if step['state'] == 'replace')
        self.assertEqual(replacement['payload']['context'], dict(prior_assignment_id='bar-first'))
        self.assertEqual(len(self.case.read_records()['review_assignment']), 3)

    def test_two_drivers_on_two_chains_do_not_duplicate_calls(self):
        from research_harness.review_series import run_series
        self.case.assign('bar', 'second', **self.case.deliver_supporting('second'))
        barrier = threading.Barrier(2)
        results, failures, rows = {}, [], {'bar-first': [], 'bar-second': []}
        def provider(*args, **kwargs):
            barrier.wait(timeout=20)
            return self.case.build_provider_response(self.case.build_review_output('bar'))
        def drive(head):
            try:
                results[head] = run_series(self.case.store, head, emit=rows[head].append)
            except BaseException as error:
                failures.append(error)
        with patch('research_harness.review_transport.send_request', side_effect=provider) as send:
            workers = [threading.Thread(target=drive, args=(head,)) for head in rows]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=30)
            self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        self.assertEqual(results, {'bar-first': 0, 'bar-second': 0}, rows)
        self.assertEqual(send.call_count, 2)

    def test_two_processes_drive_two_chains_of_one_store(self):
        import test_research_review_protocol as protocol_fixtures
        fixture = protocol_fixtures.ReviewProtocolTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.route()
        fixture.run_probe()
        fixture.assign(identifier='one')
        fixture.assign(identifier='two', reviewer='another-reviewer')
        script = '''
import json
import os
from pathlib import Path
import sys
import time
from research_harness import review_transport as transport
from research_harness.review_series import run_series
from research_harness.storage import Store
root = Path(sys.argv[1])
def send(route, request, credential):
    descriptor = os.open(str(root / 'calls'), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, b'call\\n')
    finally:
        os.close(descriptor)
    deadline = time.monotonic() + 60
    while len((root / 'calls').read_text().splitlines()) < 2:
        if time.monotonic() >= deadline:
            raise OSError('The second driver did not reach its call')
        time.sleep(0.01)
    return dict(id='response', model=route['model'], status='completed', output=[dict(
        type='message', role='assistant', content=[dict(type='output_text', text='{}')])])
transport.send_request = send
raise SystemExit(run_series(Store(root), sys.argv[2]))
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(fixture.root), head],
                                     cwd=str(Path(__file__).resolve().parents[1]),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for head in ('one', 'two')]
        for process in processes:
            self.addCleanup(lambda process=process: process.kill() if process.poll() is None else None)
            output, errors = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 3, errors)
            self.assertEqual(json.loads(output.splitlines()[-1])['state'], 'handoff')
        self.assertEqual((fixture.root / 'calls').read_text().splitlines(), ['call', 'call'])
        self.assertEqual(len(fixture.records()['review_attempt']), 2)


if __name__ == '__main__':
    unittest.main()
