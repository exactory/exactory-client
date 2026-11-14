"""Retain the refused series action when a reachable code awaits its registry row."""

import unittest
from unittest.mock import patch

from research_harness import cli, record_check, review_continuation, review_series
from research_harness.errors import ResearchError
import test_review_continuation as fixtures


class UnlistedStopTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ContinuationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        from test_series_code_reach import record_reachable_errors
        recorder = record_reachable_errors('run_series')
        recorder.__enter__()
        self.addCleanup(recorder.__exit__, None, None, None)

    def test_series_retains_unlisted_code_and_refused_action(self):
        rows = []
        with patch.dict(cli.OPERATIONS, {'review-run': self.refuse_stale_search}):
            exit_code = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(exit_code, 3)
        terminal = rows[-1]
        self.assertEqual((terminal['state'], terminal['code'], terminal['stop_class']),
                         ('stop', 'openness_search_stale', 'decision'))
        self.assertTrue(terminal['details']['unlisted'])
        self.assertEqual(terminal['details']['refused_action'], dict(command='review-run', state='run',
            payload=dict(id='bar-first-run-1', assignment_id='bar-first')))
        self.assertEqual(terminal['error']['details'], dict(search_id='synthetic-search'))
        self.assertEqual(self.case.read_records().get('review_attempt', {}), {})

    def refuse_stale_search(self, store, payload, **identity):
        raise ResearchError('openness_search_stale', 'The bound search is stale.', dict(search_id='synthetic-search'))

    def test_planner_marks_an_unlisted_validator_refusal(self):
        with patch.object(review_continuation, '_plan_head', side_effect=ResearchError(
                'openness_search_stale', 'The bound search is stale.', dict(search_id='synthetic-search'))):
            plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, 'bar-first')
        self.assertEqual((plan['state'], plan['code']), ('stop', 'openness_search_stale'))
        self.assertTrue(plan['details']['unlisted'])
        self.assertEqual(plan['details']['refused_action'], dict(command='plan_next', assignment_id='bar-first'))
        self.assertEqual(plan['details']['search_id'], 'synthetic-search')

    def test_record_check_names_the_unlisted_planner_boundary(self):
        plan = dict(state='stop', code='openness_search_stale', chain='session',
                    head_id='bar-first', progress={}, details=dict(unlisted=True))
        with patch.object(record_check, 'plan_next', return_value=plan):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(self.case.store))
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')
        self.assertEqual(row['error']['details']['code'], 'openness_search_stale')

    def test_record_check_names_an_unlisted_prospective_request_refusal(self):
        with patch.object(record_check, 'check_prospective_request', side_effect=ResearchError(
                'openness_search_stale', 'The bound search is stale.')):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(self.case.store))
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')
        self.assertEqual(row['error']['details']['code'], 'openness_search_stale')
        self.assertEqual(row['error']['details']['refused_action']['command'], 'review-run')

    def test_unlisted_handoff_stops_for_a_registry_row(self):
        with patch.object(review_continuation, '_plan_head', return_value=dict(
                state='handoff', code='openness_search_stale', command='export', details={} )):
            plan = review_continuation.plan_next(self.case.read_records(), self.case.artifacts, 'bar-first')
        self.assertEqual(plan['state'], 'stop')
        self.assertTrue(plan['details']['unlisted'])
        self.assertEqual(plan['details']['refused_action'], dict(command='export', state='handoff'))

    def test_native_controller_boundary_reports_an_unlisted_planner_refusal(self):
        self.fixture.doCleanups()
        from test_review_parts_native import NativePartsTests
        case = NativePartsTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        first = case.assign()
        plan = dict(state='stop', code='openness_search_stale', head_id=first['id'], details={})
        with patch.object(record_check, 'plan_next', return_value=plan):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(case.store))
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')

    def test_record_check_names_the_unlisted_recording_refusal(self):
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(self.case.build_review_output('bar'))):
            self.fixture.execute(self.fixture.plan())
        def refuse_recording(store, payload, **identity):
            raise ResearchError('openness_search_stale', 'The bound search is stale.')
        with patch.dict(cli.OPERATIONS, {'value-review': refuse_recording}):
            row = record_check.run_timed_check('continuation', lambda: record_check.check_continuation(self.case.store))
        self.assertEqual(row['error']['code'], 'record_unlisted_stop')
        self.assertEqual(row['error']['details']['code'], 'openness_search_stale')
        self.assertEqual(row['error']['details']['refused_action']['command'], 'value-review')
