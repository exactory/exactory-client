"""A stopped or unreadable driver keeps the series ending after the release merge."""

import json
import unittest
from unittest.mock import patch

from research_harness import cli, review_series, review_stop
from research_harness.errors import ResearchError
import test_review_continuation as fixtures


class MergedDriverEndingTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        self.fixture = fixtures.ContinuationTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.case = self.fixture.case

    def test_startup_refusal_gets_a_retained_ending(self):
        rows = []
        with patch.object(review_stop, 'start_driver', side_effect=ResearchError('artifact_missing', 'Synthetic startup refusal.')):
            result = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assertEqual((rows[-1]['head_id'], rows[-1]['code']), ('bar-first', 'artifact_missing'))
        self.assertIn(rows[-1]['details']['receipt']['id'], self.case.read_records()['review_series_stop'])

    def test_persistent_snapshot_failure_does_not_replace_the_workspace_ending(self):
        driver = review_stop.start_driver(self.case.store, 'bar-first')
        rows = []
        with patch.object(review_stop, 'start_driver', return_value=driver), \
                patch.object(self.case.store, 'snapshot', side_effect=ResearchError('artifact_missing', 'Synthetic unreadable snapshot.')):
            result = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assertEqual(rows[-1]['code'], 'artifact_missing')
        receipt = rows[-1]['details']['receipt']
        self.assertEqual(receipt['place'], 'workspace')
        retained = json.loads((self.case.root / receipt['path']).read_text())
        self.assertEqual(retained['code'], 'artifact_missing')

    def test_stop_during_the_selected_action_keeps_its_head_and_payload(self):
        plan = self.advance_head()
        rows = []

        def stop(store, payload, **options):
            receipt = review_stop.request_stop(store, dict(id='requested', action='stop', mode='now',
                assignment_id=plan['head_id'], reason='Synthetic action boundary.'), request_id='requested')
            raise ResearchError('review_chain_stopped', 'Synthetic requested boundary.',
                                dict(stop_id=receipt['result']['id']))

        with patch.dict(cli.OPERATIONS, {'review-run': stop}):
            result = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assertEqual(rows[-1]['head_id'], plan['head_id'])
        self.assertEqual(rows[-1]['error']['code'], 'review_chain_stopped')
        self.assertEqual(rows[-1]['details']['refused_action'],
                         {key: plan[key] for key in ('command', 'state', 'payload')})
        self.assertIn(rows[-1]['details']['receipt']['id'], self.case.read_records()['review_series_stop'])

    def advance_head(self):
        output = self.case.build_review_output('bar')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        self.fixture.invoke(output)
        self.fixture.execute(self.fixture.plan())
        plan = self.fixture.plan()
        self.assertNotEqual(plan['head_id'], 'bar-first')
        return plan

    def test_signal_during_progress_keeps_the_advanced_head_and_raised_error(self):
        plan = self.advance_head()
        waiting = dict(plan, state='wait', not_before='2000-01-01T00:00:00Z')
        rows = []

        def emit(row):
            if row['state'] == 'wait':
                raise review_stop.ReviewStopSignal(dict(stop_request=dict(id='signal-progress',
                    action='stop', mode='now', assignment_id=plan['head_id'], reason='Synthetic signal.')))
            rows.append(row)

        with patch.object(review_series, '_read_plan', return_value=(self.case.store.snapshot(), waiting)):
            result = review_series.run_series(self.case.store, 'bar-first', emit=emit)
        self.assertEqual(result, 3)
        self.assertEqual(rows[-1]['head_id'], plan['head_id'])
        self.assertEqual(rows[-1]['error']['code'], 'review_chain_stopped')
        self.assertIn(rows[-1]['details']['receipt']['id'], self.case.read_records()['review_series_stop'])

    def test_unrecorded_signal_stop_uses_an_executable_inspection_fallback(self):
        rows = []

        def stop(*args, **kwargs):
            raise review_stop.ReviewStopSignal(dict(stop_request=dict(id='unrecorded-signal',
                action='stop', mode='now', assignment_id='bar-first', reason='Synthetic signal.')))

        with patch.dict(cli.OPERATIONS, {'review-run': stop}), \
                patch.object(review_stop, 'record_signal_boundary', side_effect=ResearchError('store_busy', 'Synthetic busy stop.')):
            result = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assertEqual(rows[-1]['error']['code'], 'review_chain_stopped')
        self.assertEqual(rows[-1]['command'], 'record-check')
        self.assertEqual(rows[-1]['details']['step']['command'], 'record-check')
        self.assertEqual(rows[-1]['details']['omitted']['stop_settlement']['code'], 'store_busy')

    def test_repeated_requested_stops_keep_each_release_id_and_ending_receipt(self):
        endings = []
        for identifier in ('first-stop', 'second-stop'):
            review_stop.request_stop(self.case.store, dict(id=identifier, action='stop', mode='after_turn',
                assignment_id='bar-first', reason='Synthetic requested stop.'), request_id=identifier)
            rows = []
            self.assertEqual(review_series.run_series(self.case.store, 'bar-first', emit=rows.append), 3)
            line = rows[-1]
            self.assertEqual(line['stop_id'], identifier)
            receipt_id = line['details']['receipt']['id']
            self.assertEqual(line['series_stop_id'], receipt_id)
            retained = self.case.read_records()['review_series_stop'][receipt_id]['plan']
            self.assertEqual((retained['stop_id'], retained['series_stop_id']), (identifier, receipt_id))
            recovered = review_series._recover_ending_line(line)
            self.assertEqual((recovered['stop_id'], recovered['series_stop_id']), (identifier, receipt_id))
            endings.append(receipt_id)
            review_stop.release_stop(self.case.store, dict(id='release-' + identifier, action='release',
                stop_id=identifier, reason='Continue the same synthetic chain.'), request_id='release-' + identifier)
        self.assertNotEqual(endings[0], endings[1])

    def test_failed_boundary_read_keeps_the_operation_refusal_and_inspection_step(self):
        self.assert_failed_boundary_keeps_refusal(
            ResearchError('corrupt_state', 'The later diagnostic boundary is unavailable.'), 'corrupt_state')

    def test_boundary_exit_keeps_the_already_selected_operation_refusal(self):
        self.assert_failed_boundary_keeps_refusal(SystemExit('The later diagnostic read exited.'), 'SystemExit')

    def assert_failed_boundary_keeps_refusal(self, read_error, read_code):
        failed = []
        rows = []

        def refuse(*args, **kwargs):
            failed.append(True)
            raise ResearchError('artifact_missing', 'The selected operation refused its artifact.')

        def read_boundary(*args, **kwargs):
            if failed:
                raise read_error
            return None

        with patch.dict(cli.OPERATIONS, {'review-run': refuse}), \
                patch.object(review_stop, 'read_driver_boundary', side_effect=read_boundary):
            result = review_series.run_series(self.case.store, 'bar-first', emit=rows.append)
        self.assertEqual(result, 3)
        self.assertEqual((rows[-1]['code'], rows[-1]['error']['code']), ('artifact_missing', 'artifact_missing'))
        self.assertEqual(rows[-1]['details']['omitted']['boundary']['code'], read_code)
        self.assertEqual((rows[-1]['state'], rows[-1]['command']), ('handoff', 'record-check'))

    def test_an_unexpected_operation_error_propagates_and_ends_the_driver(self):
        def fail(*args, **kwargs):
            raise ValueError('Synthetic operation error.')

        with patch.dict(cli.OPERATIONS, {'review-run': fail}):
            with self.assertRaisesRegex(ValueError, 'Synthetic operation error'):
                review_series.run_series(self.case.store, 'bar-first', emit=lambda row: None)
        drivers = self.case.read_records()['review_series_run'].values()
        self.assertTrue(all(row['ended'] is not None for row in drivers))
