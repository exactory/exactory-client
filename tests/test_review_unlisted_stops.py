"""Retain the refused series action when a reachable code awaits its registry row."""

import sqlite3
import unittest
from unittest.mock import patch

from research_harness import cli, record_check, review_continuation, review_series
from research_harness.errors import ResearchError
import test_review_continuation as fixtures
from test_series_code_reach import record_reachable_errors


class UnlistedStopTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case

    @record_reachable_errors('run_series')
    def test_driver_refuses_an_executable_state_with_a_command_outside_its_set(self):
        plan = dict(state='run', command='record-check', payload={}, head_id='bar-first', progress={})
        calls = []
        def operate(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError('An operation outside EXECUTABLE_COMMANDS was called')
        with patch.object(review_series, '_read_plan', return_value=(self.case.store.snapshot(), plan)), \
                patch.dict(cli.OPERATIONS, {'record-check': operate}):
            with self.assertRaisesRegex(ValueError, 'record-check'):
                review_series.run_series(self.case.store, 'bar-first', emit=lambda row: None)
        self.assertEqual(calls, [])

    @record_reachable_errors('run_series')
    def test_driver_refuses_an_expired_unchanged_wait_with_a_command_outside_its_set(self):
        plan = dict(state='wait', command='record-check', payload={}, head_id='bar-first', progress={},
                    not_before='2000-01-01T00:00:00+00:00')
        calls = []
        def operate(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError('An operation outside EXECUTABLE_COMMANDS was called')
        with patch.object(review_series, '_read_plan', return_value=(self.case.store.snapshot(), plan)), \
                patch.dict(cli.OPERATIONS, {'record-check': operate}):
            with self.assertRaisesRegex(ValueError, 'record-check'):
                review_series.run_series(self.case.store, 'bar-first', emit=lambda row: None)
        self.assertEqual(calls, [])

    @record_reachable_errors('run_series')
    def test_driver_refuses_a_wait_refreshed_to_an_executable_command_outside_its_set(self):
        waiting = dict(state='wait', command='review-run', payload={}, head_id='bar-first', progress={},
                       not_before='2000-01-01T00:00:00+00:00')
        running = dict(state='run', command='record-check', payload={}, head_id='bar-first', progress={})
        snapshot = self.case.store.snapshot()
        calls = []
        def operate(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError('An operation outside EXECUTABLE_COMMANDS was called')
        with patch.object(review_series, '_read_plan', side_effect=[(snapshot, waiting), (snapshot, running)]), \
                patch.dict(cli.OPERATIONS, {'record-check': operate}):
            with self.assertRaisesRegex(ValueError, 'record-check'):
                review_series.run_series(self.case.store, 'bar-first', emit=lambda row: None)
        self.assertEqual(calls, [])

    @record_reachable_errors('run_series')
    def test_every_executable_command_is_a_public_operation(self):
        self.assertEqual(review_series.EXECUTABLE_COMMANDS, frozenset((
            'review-run', 'review-assignment', 'value-review', 'review-adjudication', 'problem-openness')))
        self.assertTrue(review_series.EXECUTABLE_COMMANDS <= set(cli.OPERATIONS))
        self.assertEqual(record_check.LOCAL_OPERATIONS, review_series.EXECUTABLE_COMMANDS - {'review-run'})

    def test_readable_store_keeps_the_last_line_and_reports_the_unlisted_continuation(self):
        output = self.case.build_review_output('bar')
        self.fixture.invoke(output)
        action = dict(command='value-review', state='record', payload=dict(output,
            id='review:bar-first', assignment_id='bar-first', dossier_id=None))
        rows = []
        with patch.dict(cli.OPERATIONS, {'value-review': self.refuse_stale_search}):
            with record_reachable_errors('run_series'):
                result = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
            with record_reachable_errors('record-check'):
                report = record_check.check_record(self.case.root, isolate=False)
        self.assertEqual(result, 3)
        terminal = rows[-1]
        self.assertEqual((terminal['head_id'], terminal['code']), ('bar-first', 'openness_search_stale'))
        self.assertEqual(terminal['details']['refused_action'], action)
        self.assertEqual([row['step'] for row in report['steps']],
                         ['snapshot', 'integrity', 'reconstruction', 'authentication', 'continuation'])
        self.assertTrue(all(row['status'] == 'passed' for row in report['steps'][:-1]))
        failure = report['steps'][-1]
        self.assertEqual((failure['status'], failure['error']['code']), ('failed', 'record_unlisted_stop'))
        boundary = failure['error']['details']
        self.assertEqual((boundary['head_id'], boundary['code']), ('bar-first', 'openness_search_stale'))
        self.assertEqual(boundary['chain'], terminal['chain'])
        self.assertEqual(boundary['refused_action'], action)

    def test_changed_artifact_keeps_the_last_line_and_fails_record_integrity(self):
        operation = cli.OPERATIONS['review-run']
        packet = self.fixture.assignment['packet']
        def damage_packet(store, payload, **identity):
            path = store.root / packet['path']
            path.chmod(0o600)
            path.write_bytes(b'Damaged retained review packet.')
            return operation(store, payload, **identity)
        rows = []
        with patch.dict(cli.OPERATIONS, {'review-run': damage_packet}):
            with record_reachable_errors('run_series'):
                result = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        terminal = rows[-1]
        self.assertEqual((terminal['head_id'], terminal['code']), ('bar-first', 'artifact_corrupt'))
        self.assertEqual(terminal['details']['refused_action'], dict(command='review-run', state='run',
            payload=dict(id='bar-first-run-1', assignment_id='bar-first')))
        self.assertEqual(terminal['error']['code'], 'artifact_corrupt')
        with record_reachable_errors('record-check'):
            report = record_check.check_record(self.case.root, isolate=False)
        self.assertEqual([row['step'] for row in report['steps']], ['snapshot', 'integrity'])
        self.assertEqual(report['steps'][0]['status'], 'passed')
        self.assertEqual((report['steps'][-1]['status'], report['steps'][-1]['error']['code']),
                         ('failed', 'artifact_corrupt'))

    def test_operation_damaged_store_keeps_the_last_line_when_capacity_reads_fail(self):
        from research_harness import review_protocol, review_route_capacity
        from research_harness.evidence import digest
        from test_review_capacity_plan import build_inputs
        capacity_fixture = fixtures.ContinuationTests()
        original_builder = review_protocol.build_packet
        def build_packet(*args, **kwargs):
            packet = original_builder(*args, **kwargs)
            packet[review_route_capacity.CAPACITY_REQUEST_KEY] = dict(build_inputs(1), kind='reading')
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            return packet
        with patch.object(review_protocol, 'build_packet', side_effect=build_packet):
            capacity_fixture.setUp()
        self.addCleanup(capacity_fixture.doCleanups)
        case = capacity_fixture.case
        assignment = capacity_fixture.assignment
        operation = cli.OPERATIONS['review-run']
        def damage_store(store, payload, **identity):
            # The first plan has already been read. Persist both failed store
            # metadata and an unreadable advisory artifact before the real call.
            with sqlite3.connect(str(store.root / '.exactory/research.sqlite3')) as connection:
                connection.execute('UPDATE metadata SET revision = revision + 1')
            path = store.root / assignment['capacity_plan']['artifact']['path']
            path.chmod(0o600)
            path.write_bytes(b'Damaged retained capacity plan.')
            return operation(store, payload, **identity)
        rows = []
        with patch.dict(cli.OPERATIONS, {'review-run': damage_store}):
            with record_reachable_errors('run_series'):
                result = review_series.run_series(case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        terminal = rows[-1]
        self.assertEqual((terminal['head_id'], terminal['code']), ('bar-first', 'corrupt_state'))
        self.assertEqual(terminal['details']['refused_action'], dict(command='review-run', state='run',
            payload=dict(id='bar-first-run-1', assignment_id='bar-first')))
        self.assertEqual(terminal['error']['code'], 'corrupt_state')
        self.assertEqual(terminal['read_error']['code'], 'corrupt_state')
        self.assertEqual(terminal['stop_record_error']['code'], 'corrupt_state')
        self.assertNotIn('capacity_forecast', terminal)
        with record_reachable_errors('record-check'):
            report = record_check.check_record(case.root, isolate=False)
        self.assertEqual([row['step'] for row in report['steps']], ['snapshot'])
        failure = report['steps'][0]
        self.assertEqual((failure['status'], failure['error']['code']), ('failed', 'record_snapshot_inconsistent'))
        self.assertEqual([attempt['code'] for attempt in failure['error']['details']['attempts']],
                         ['corrupt_state', 'corrupt_state', 'corrupt_state'])

    @record_reachable_errors('run_series')
    def test_series_retains_unlisted_code_and_refused_action(self):
        rows = []
        with patch.dict(cli.OPERATIONS, {'review-run': self.refuse_missing_search}):
            exit_code = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(exit_code, 3)
        terminal = rows[-1]
        self.assertEqual((terminal['state'], terminal['code'], terminal['stop_class']),
                         ('stop', 'openness_search_missing', 'decision'))
        self.assertTrue(terminal['details']['unlisted'])
        self.assertEqual(terminal['details']['refused_action'], dict(command='review-run', state='run',
            payload=dict(id='bar-first-run-1', assignment_id='bar-first')))
        self.assertEqual(terminal['error']['details'], dict(search_id='synthetic-search'))
        self.assertEqual(self.case.read_records().get('review_attempt', {}), {})

    def refuse_missing_search(self, store, payload, **identity):
        raise ResearchError('openness_search_missing', 'The required search is missing.', dict(search_id='synthetic-search'))

    @record_reachable_errors('run_series')
    def test_planner_marks_an_unlisted_validator_refusal(self):
        with patch.object(review_continuation, '_plan_head', side_effect=ResearchError(
                'openness_search_missing', 'The required search is missing.', dict(search_id='synthetic-search'))):
            plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, 'bar-first')
        self.assertEqual((plan['state'], plan['code']), ('stop', 'openness_search_missing'))
        self.assertTrue(plan['details']['unlisted'])
        self.assertEqual(plan['details']['refused_action'], dict(command='plan_next', assignment_id='bar-first'))
        self.assertEqual(plan['details']['search_id'], 'synthetic-search')

    @record_reachable_errors('record-check')
    def test_record_check_names_the_unlisted_planner_boundary(self):
        plan = dict(state='stop', code='openness_search_missing', chain='session',
                    head_id='bar-first', progress={}, details=dict(unlisted=True))
        with patch.object(record_check, 'plan_next', return_value=plan):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(self.case.store))
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')
        self.assertEqual(row['error']['details']['code'], 'openness_search_missing')

    @record_reachable_errors('record-check')
    def test_record_check_names_an_unlisted_prospective_request_refusal(self):
        with patch.object(record_check, 'check_prospective_request', side_effect=ResearchError(
                'openness_search_missing', 'The required search is missing.')):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(self.case.store))
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')
        self.assertEqual(row['error']['details']['code'], 'openness_search_missing')
        self.assertEqual(row['error']['details']['refused_action']['command'], 'review-run')

    @record_reachable_errors('run_series')
    def test_unlisted_handoff_stops_for_a_registry_row(self):
        with patch.object(review_continuation, '_plan_head', return_value=dict(
                state='handoff', code='openness_search_missing', command='export', details={} )):
            plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, 'bar-first')
        self.assertEqual(plan['state'], 'stop')
        self.assertTrue(plan['details']['unlisted'])
        self.assertEqual(plan['details']['refused_action'], dict(command='export', state='handoff'))

    @record_reachable_errors('record-check')
    def test_native_controller_boundary_reports_an_unlisted_planner_refusal(self):
        self.fixture.doCleanups()
        from test_review_parts_native import NativePartsTests
        case = NativePartsTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        first = case.assign()
        plan = dict(state='stop', code='openness_search_missing', head_id=first['id'], details={})
        with patch.object(record_check, 'plan_next', return_value=plan):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(case.store))
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')

    @record_reachable_errors('record-check')
    def test_record_check_names_the_unlisted_recording_refusal(self):
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(self.case.build_review_output('bar'))):
            self.fixture.execute(self.fixture.plan())
        def refuse_recording(store, payload, **identity):
            raise ResearchError('openness_search_missing', 'The required search is missing.')
        with patch.dict(cli.OPERATIONS, {'value-review': refuse_recording}):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(self.case.store))
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')
        self.assertEqual(row['error']['details']['code'], 'openness_search_missing')
        self.assertEqual(row['error']['details']['refused_action']['command'], 'value-review')
