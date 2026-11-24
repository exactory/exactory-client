"""Stop and queue interleavings share the transactional admission boundary."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import cli, review_call_admission as admission
from research_harness import review_task_execution as execution, review_worker as worker
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_review_task_execution as fixtures


def assert_requested_ending(test, records, line, requested_id):
    """Keep release identity distinct from the retained series receipt."""
    test.assertEqual((line['state'], line['code'], line['stop_class']),
                     ('stop', 'review_chain_stopped', 'requested'))
    test.assertEqual(line['stop_id'], requested_id)
    test.assertEqual(line['details']['stop_id'], requested_id)
    test.assertIn(requested_id, records['review_stop'])
    reference = line['details']['receipt']
    test.assertIn(reference['place'], ('store', 'workspace'))
    if reference['place'] == 'store':
        identifier = line['series_stop_id']
        test.assertNotEqual(identifier, requested_id)
        test.assertEqual(reference['id'], identifier)
        receipt = records['review_series_stop'][identifier]
        test.assertEqual((receipt['id'], receipt['code'], receipt['assignment_id']),
                         (identifier, 'review_chain_stopped', line['head_id']))
        retained = receipt['plan']
        test.assertEqual(retained['series_stop_id'], identifier)
        test.assertEqual(line['revision_after_stop'], receipt['recorded_revision'])
    else:
        path = test.root / reference['path']
        test.assertTrue(path.is_file(), reference)
        retained = json.loads(path.read_text())
        test.assertIn('stop_record_error', line)
        test.assertEqual(retained['stop_record_error'], line['stop_record_error'])
        test.assertEqual(retained.get('series_stop_id'), line.get('series_stop_id'))
    test.assertEqual((retained['state'], retained['code'], retained['stop_class'], retained['head_id']),
                     ('stop', 'review_chain_stopped', 'requested', line['head_id']))
    test.assertEqual(retained['stop_id'], requested_id)
    test.assertEqual(retained['details']['stop_id'], requested_id)
    test.assertEqual(retained['details']['receipt'], reference)
    drivers = [row for row in records['review_series_run'].values()
               if (row.get('ended') or {}).get('details', {}).get('receipt') == reference]
    test.assertEqual(len(drivers), 1)
    driver = drivers[0]
    test.assertEqual(driver['assignment_id'], line['head_id'])
    test.assertEqual(driver['ended']['stop_id'], requested_id)
    test.assertEqual(driver['ended'].get('series_stop_id'), line.get('series_stop_id'))
    test.assertEqual(driver['ended']['details']['receipt'], reference)
    if reference['place'] == 'store':
        test.assertEqual(line['revision_after_driver'], driver['ended']['revision'])
    else:
        test.assertGreater(driver['ended']['revision'], 0)
    return reference


class StopQueueFixture(fixtures.TaskExecutionFixture):
    def setUp(self):
        super().setUp()
        from test_review_route_capacity import build_route
        self.put('review_route', 'route', build_route('codex_cli_v1', 'fixture-model', output=1000))
        for identifier in ('first', 'other'):
            self.put('review_assignment', identifier, dict(id=identifier, role='result', route_id='route',
                dossier_id=None, slot_id=identifier, context={}, prompt={}, reviewer_id='reviewer', recorded_revision=self.store.revision + 1))
        epoch = self.store.snapshot()['records']['review_epoch']['epoch']
        self.put('review_epoch', 'epoch', dict(epoch, controller_assignment_id='first'))

    def stop(self, mode='now', assignment='first', identifier='stop', scope=None):
        payload = dict(id=identifier, action='stop', mode=mode, reason='Synthetic administrative stop.')
        payload.update(scope=scope) if scope else payload.update(assignment_id=assignment)
        return cli.OPERATIONS['review-stop'](self.store, payload, request_id=identifier)['result']

    def release(self, identifier='stop'):
        return cli.OPERATIONS['review-stop'](self.store, dict(id='release', action='release',
            stop_id=identifier, reason='Continue the recorded work.'), request_id='release')['result']

    def start(self, assignment='first', identifier='call'):
        invocation = dict(id=identifier, assignment_id=assignment, route_id='route', request=self.request)
        identity = worker._identity()
        authority = type('Authority', (), dict(reservation_id=execution.identify_call('review.invoke', identifier),
            token='owned-token', identity=identity))()
        revision = self.store.revision + 1
        def apply(tx):
            execution.start_call(tx, 'review.invoke', invocation, authority, revision)
            tx.put('review_invocation', identifier, invocation)
        self.store.mutate('fixture.start', invocation, apply, expected_revision=self.store.revision,
                          request_id='start-' + identifier)
        return self.store.snapshot()['records']['review_pool_membership'][authority.reservation_id]



class StopQueueTests(StopQueueFixture):
    def test_staged_adjudicator_child_keeps_its_controller_as_the_chain_head(self):
        from research_harness.review_continuation import list_chain_heads, _find_head
        records = self.store.snapshot()['records']
        controller = dict(records['review_assignment']['first'], role='adjudicator',
                          staged_review=dict(kind='controller'))
        child = dict(controller, id='child', recorded_revision=controller['recorded_revision'] + 1,
                     context=dict(controller_assignment_id='first', task_id='task'),
                     staged_review=dict(kind='task', controller_assignment_id='first'))
        records['review_assignment'] = dict(first=controller, child=child)
        before = copy.deepcopy(records)
        self.assertEqual(list_chain_heads(records, None), ['first'])
        self.assertEqual(_find_head(records, None, 'child'), controller)
        self.assertEqual(records, before)
        records['review_assignment']['replacement'] = dict(controller, id='replacement',
            recorded_revision=child['recorded_revision'] + 1)
        self.assertEqual(list_chain_heads(records, None), ['replacement'])

    def test_stop_after_plan_before_start_refuses_without_an_invocation(self):
        stop = self.stop()
        self.assertEqual(stop['mode'], 'now')
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as refused:
            self.start()
        self.assertEqual(refused.exception.code, 'review_chain_stopped')
        self.assertEqual(refused.exception.details['stop_class'], 'requested')
        self.assertEqual(self.store.snapshot(), before)
        self.release()
        self.start()

    def test_scope_resolves_any_turn_and_preserves_other_chain(self):
        self.put('review_assignment', 'turn-2', dict(id='turn-2', role='result', route_id='route',
            slot_id='first', dossier_id=None, context=dict(prior_assignment_id='first'), recorded_revision=10))
        stop = self.stop(assignment='turn-2')
        self.assertEqual(stop['scope']['root_assignment_id'], 'first')
        self.refuse('review_chain_stopped', self.start)
        self.start('other')

    def test_status_keeps_an_older_chain_boundary_visible_after_store_release(self):
        self.stop('after_turn')
        self.stop('after_turn', identifier='store-stop', scope='store')
        self.release('store-stop')
        records = self.store.snapshot()['records']
        records.pop('review_epoch')
        status = cli._review_status(records, self.store.build_artifact_store())
        last = status['assignments']['first']['last_stop']
        self.assertEqual(last['id'], 'stop')
        self.assertTrue(last['boundary_stands'])

    def test_stopped_slot_stays_stopped_when_a_new_head_has_another_root(self):
        self.stop('after_turn')
        self.put('review_assignment', 'replacement', dict(id='replacement', role='result', route_id='route',
            slot_id='first', dossier_id=None, context={}, prompt={}, recorded_revision=self.store.revision + 1))
        self.refuse('review_chain_stopped', lambda: self.start('replacement'))

    def test_after_turn_allows_child_then_now_escalates_the_same_stop(self):
        membership = self.start()
        self.stop('after_turn')
        current = self.store.snapshot()['records']['review_pool_membership'][membership['id']]
        admission.check_launch(self.store.snapshot()['records'], 'review.call_child', current,
                               token=current['token'], read_identity=lambda pid: current['owner'])
        with patch('research_harness.review_stop.settle_stop', return_value=None):
            result = self.stop(identifier='raise-now')
        self.assertEqual(result['id'], 'stop')
        self.assertEqual(result['mode'], 'now')
        current = self.store.snapshot()['records']['review_pool_membership'][membership['id']]
        self.refuse('review_chain_stopped', lambda: admission.check_launch(self.store.snapshot()['records'],
            'review.call_child', current, token=current['token'], read_identity=lambda pid: current['owner']))

    def test_store_after_turn_keeps_an_earlier_now_boundary_on_its_calls(self):
        membership = self.start()
        with patch('research_harness.review_stop.settle_stop', return_value=None):
            self.stop()
        self.stop('after_turn', scope='store', identifier='store-stop')
        records = self.store.snapshot()['records']
        current = records['review_pool_membership'][membership['id']]
        self.assertEqual((current['stop_id'], current['stop_mode']), ('stop', 'now'))
        self.refuse('review_chain_stopped', lambda: admission.check_launch(records,
            'review.call_child', current, token=current['token'], read_identity=lambda pid: current['owner']))

    def test_interrupted_call_reconciles_and_does_not_spend_failure_allowance(self):
        membership = self.start()
        dead = dict(membership['owner'], pid=987654321, pgid=987654321)
        self.put('review_pool_membership', membership['id'], dict(membership, owner=dead))
        result = self.stop()
        call = result['calls'][0]
        self.assertEqual(call['invocation_id'], 'call')
        records = self.store.snapshot()['records']
        self.assertTrue(records['review_pool_membership'][membership['id']]['released'])
        from research_harness.review_failure_class import classify_attempt
        self.assertEqual(classify_attempt(records, self.store.build_artifact_store(),
            records['review_attempt']['call'])['outcome'], 'stopped')
        self.release()

    def test_historical_call_has_import_step_and_does_not_block_release(self):
        invocation = dict(id='historical', assignment_id='first', route_id='route', request=self.request)
        self.put('review_invocation', 'historical', invocation)
        result = self.stop()
        self.assertEqual(result['call_states'][0]['state'], 'historical')
        self.assertEqual(result['call_states'][0]['step'], execution.build_historical_attempt_step(invocation))
        self.release()
        self.assertEqual(self.store.snapshot()['records']['review_invocation']['historical'], invocation)

    def test_staged_reservation_and_launch_obey_the_boundary(self):
        stop = self.stop('after_turn')
        self.refuse('review_chain_stopped', self.reserve)
        self.release(stop['id'])
        reservation = self.reserve()['result']
        self.stop('after_turn', identifier='second-stop')
        self.refuse('review_chain_stopped', lambda: worker._claim_launch(self.store, reservation['id']))

    def test_stop_never_signals_a_reused_transport_process_identity(self):
        membership = self.start()
        child = dict(pid=20, pgid=20, started='original-child')
        self.put('review_pool_membership', membership['id'], dict(membership, children=[child]))
        stop = self.stop('after_turn')
        records = self.store.snapshot()['records']
        from research_harness import review_stop
        reused = dict(child, started='another-process')
        with patch.object(worker, '_process_table', return_value={20:reused}), \
                patch.object(review_stop.time, 'monotonic', side_effect=[0, 11]), \
                patch.object(review_stop.os, 'killpg') as kill:
            review_stop._end_owned_processes(records, stop)
        kill.assert_not_called()

    def test_budget_refusal_precedes_the_stop_and_queue(self):
        self.stop('after_turn')
        self.put('review_budget', 'store', dict(id='store', max_calls=0, reason='No further calls.'))
        before = self.store.snapshot()
        self.refuse('review_budget_exhausted', self.start)
        self.assertEqual(self.store.snapshot(), before)

    def test_accepted_attempt_releases_without_changing_its_record(self):
        membership = self.start()
        attempt = dict(id='call', assignment_id='first', status='failed', output=None,
                       error=dict(code='review_invocation_interrupted'), recorded_revision=self.store.revision)
        self.put('review_attempt', 'call', attempt)
        self.put('review_pool_membership', membership['id'], dict(membership,
                 owner=dict(pid=987654321, pgid=987654321, started='gone')))
        stopped = self.stop()
        self.assertEqual(stopped['call_states'][0]['state'], 'settled')
        self.assertEqual(self.store.snapshot()['records']['review_attempt']['call'], attempt)

    def test_store_stop_covers_probes_and_corrections(self):
        self.stop(scope='store')
        for operation in ('review.route_probe', 'review.output_correction'):
            def apply(tx):
                admission.admit_review_call(tx, operation, 'call', 'route')
            self.refuse('review_chain_stopped', lambda: self.store.mutate('fixture.admit', {}, apply,
                expected_revision=self.store.revision, request_id=operation))

    def test_store_stop_can_end_calls_even_when_their_chain_has_a_fork(self):
        from research_harness import review_stop
        own_scope = review_stop.build_chain_scope(self.store.snapshot()['records'], 'first')
        self.start()
        first = self.store.snapshot()['records']['review_assignment']['first']
        self.put('review_assignment', 'first', dict(first, slot_id=None))
        fork_revision = self.store.revision + 1
        for identifier in ('left', 'right'):
            self.put('review_assignment', identifier, dict(first, id=identifier, slot_id=None,
                context=dict(prior_assignment_id='first'), recorded_revision=fork_revision))
        self.refuse('review_source_continuation_fork', lambda: self.stop('after_turn'))
        stopped = self.stop('after_turn', scope='store')
        self.assertEqual(stopped['calls'][0]['invocation_id'], 'call')
        own = review_stop._select_stop_calls(self.store.snapshot()['records'], stopped, own_scope)
        self.assertEqual(own['calls'][0]['invocation_id'], 'call')
        records = self.store.snapshot()['records']
        records.pop('review_epoch')
        self.assertEqual(cli._review_status(records, self.store.build_artifact_store())['assignments']['first']['last_stop']['id'], 'stop')
        from research_harness.review_continuation import plan_next
        plan = plan_next(records, self.store.build_artifact_store(), 'first')
        self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_chain_stopped'))
        self.assertEqual(plan['details']['step']['id'], 10)
        self.refuse('review_chain_stopped', lambda: self.start(identifier='later'))

    def test_release_refuses_unsettled_call_and_keeps_the_boundary(self):
        self.start()
        self.stop('after_turn')
        self.refuse('review_invocation_pending', self.release)
        self.refuse('review_chain_stopped', lambda: self.start(identifier='new'))

    def test_now_returns_an_unsettled_step_and_a_fresh_request_continues_it(self):
        membership = self.start()
        self.put('review_pool_membership', membership['id'], dict(membership,
            owner=dict(pid=987654321, pgid=987654321, started='gone')))
        def refuse_reconciliation(*args, **kwargs):
            raise ResearchError('review_invocation_unverified', 'Synthetic evidence is temporarily unavailable.')
        with patch.dict(cli.OPERATIONS, {'review-run':refuse_reconciliation}):
            stopped = self.stop()
        self.assertTrue(stopped['boundary_stands'])
        self.assertEqual(stopped['call_states'][0]['state'], 'interrupted')
        self.assertEqual(stopped['call_states'][0]['step']['payload']['action'], 'reconcile')
        self.refuse('review_invocation_pending', self.release)
        finished = self.stop(identifier='finish-stop')
        self.assertEqual(finished['id'], 'stop')
        self.assertEqual(finished['call_states'][0]['state'], 'settled')
        self.release()

    def test_stop_payload_refusals_write_nothing(self):
        for change in (dict(mode='later'), dict(reason=''), dict(scope='store'), dict(action='unknown')):
            payload = dict(id='bad', action='stop', assignment_id='first', mode='now', reason='Synthetic stop.')
            payload.update(change)
            before = self.store.snapshot()
            self.refuse('invalid_input', lambda: cli.OPERATIONS['review-stop'](self.store, payload, request_id='bad'))
            self.assertEqual(self.store.snapshot(), before)
        before = self.store.snapshot()
        self.refuse('review_record_missing', lambda: self.stop(assignment='absent'))
        self.assertEqual(self.store.snapshot(), before)

    def test_release_payload_refusals_write_nothing(self):
        self.stop('after_turn')
        for change in (dict(reason=''), dict(id=''), dict(stop_id=''), dict(extra='unexpected')):
            payload = dict(id='release', action='release', stop_id='stop', reason='Continue the review.')
            payload.update(change)
            before = self.store.snapshot()
            self.refuse('invalid_input', lambda: cli.OPERATIONS['review-stop'](self.store, payload, request_id='bad-release'))
            self.assertEqual(self.store.snapshot(), before)
        before = self.store.snapshot()
        self.refuse('review_task_dependency_changed', lambda: cli.OPERATIONS['review-stop'](self.store,
            dict(id='release', action='release', stop_id='absent', reason='Continue the review.'), request_id='missing-stop'))
        self.assertEqual(self.store.snapshot(), before)


class DriverStopTests(StopQueueFixture):
    # Only the driver scenarios use the minimal store; ordinary series journeys
    # continue to exercise all scientific planners with their original fixtures.
    def test_new_driver_reconciles_a_gone_predecessor_without_claiming_a_last_line(self):
        from research_harness import review_stop
        scope = review_stop.build_chain_scope(self.store.snapshot()['records'], 'first')
        self.put('review_series_run', 'gone', dict(id='gone', assignment_id='first', scope=scope,
            process=dict(pid=987654321, pgid=987654321, started='gone'), started_revision=1, ended=None))
        driver = review_stop.start_driver(self.store, 'first')
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_series_run']['gone']['ended']['code'], 'process_gone')
        self.assertIsNone(records['review_series_run'][driver['id']]['ended'])
        review_stop.end_driver(self.store, driver['id'], dict(state='done'))

    def test_sleeping_driver_acknowledges_and_release_waits_for_it(self):
        from datetime import datetime, timedelta, timezone
        from research_harness import review_series as series
        plan = dict(state='wait', chain='fixture', head_id='first', progress={},
                    command='review-run', payload=dict(id='next', assignment_id='first'),
                    not_before=(datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat())
        rows = []
        def sleep(seconds):
            self.assertLessEqual(seconds, 5)
            self.stop('after_turn')
            self.refuse('review_invocation_pending', self.release)
        with patch.object(series, '_read_plan', return_value=(self.store.snapshot(), plan)), \
                patch.object(series.time, 'sleep', side_effect=sleep):
            code = series.run_series(self.store, 'first', emit=rows.append)
        self.assertEqual(code, 3)
        self.assertEqual(rows[-1]['state'], 'stop')
        self.assertEqual(rows[-1]['code'], 'review_chain_stopped')
        self.assertEqual(rows[-1]['details']['step']['id'], 10)
        records = self.store.snapshot()['records']
        assert_requested_ending(self, records, rows[-1], 'stop')
        driver = next(iter(records['review_series_run'].values()))
        self.assertEqual(driver['ended']['stop_id'], 'stop')
        self.release()

    def test_busy_boundary_poll_keeps_the_atomic_start_guard(self):
        from research_harness import review_series as series, review_stop
        self.stop('after_turn')
        plan = dict(state='run', chain='fixture', head_id='first', progress={},
                    command='review-run', payload=dict(id='new', assignment_id='first'))
        rows = []
        with patch.object(series, '_read_plan', return_value=(self.store.snapshot(), plan)), \
                patch.object(review_stop, 'read_driver_boundary', side_effect=ResearchError('store_busy', 'Busy poll.')), \
                patch.dict(cli.OPERATIONS, {'review-run':lambda *args, **kwargs: self.start(identifier='new')}):
            self.assertEqual(series.run_series(self.store, 'first', emit=rows.append), 3)
        self.assertEqual(rows[-1]['code'], 'review_chain_stopped')
        records = self.store.snapshot()['records']
        assert_requested_ending(self, records, rows[-1], 'stop')
        self.assertNotIn('new', records.get('review_invocation', {}))

    def test_stopped_plan_acknowledges_even_when_the_additional_boundary_read_is_busy(self):
        from research_harness import review_series as series, review_stop, review_continuation
        self.stop('after_turn')
        snapshot = self.store.snapshot()
        plan = review_continuation.plan_next(snapshot['records'], self.store.build_artifact_store(), 'first')
        self.assertEqual(plan['state'], 'handoff')
        rows = []
        with patch.object(series, '_read_plan', return_value=(snapshot, plan)), \
                patch.object(review_stop, 'read_driver_boundary', side_effect=ResearchError('store_busy', 'Busy poll.')):
            self.assertEqual(series.run_series(self.store, 'first', emit=rows.append), 3)
        self.assertEqual((rows[-1]['state'], rows[-1]['stop_id'], rows[-1]['stop_class']), ('stop', 'stop', 'requested'))
        records = self.store.snapshot()['records']
        ended = next(iter(records['review_series_run'].values()))['ended']
        self.assertEqual((ended['state'], ended['stop_id']), ('stop', 'stop'))
        assert_requested_ending(self, records, rows[-1], 'stop')

    def test_driver_refusal_stays_requested_when_release_wins_its_final_read(self):
        from research_harness import review_series as series, review_stop
        self.stop('after_turn')
        plan = dict(state='run', chain='fixture', head_id='first', progress={},
                    command='review-run', payload=dict(id='new', assignment_id='first'))
        def run(*args, **kwargs):
            self.release()
            raise ResearchError('review_chain_stopped', 'The original admission met the stop.', dict(stop_id='stop'))
        rows = []
        with patch.object(series, '_read_plan', return_value=(self.store.snapshot(), plan)), \
                patch.object(review_stop, 'read_driver_boundary', return_value=None), \
                patch.dict(cli.OPERATIONS, {'review-run':run}):
            self.assertEqual(series.run_series(self.store, 'first', emit=rows.append), 3)
        self.assertEqual(rows[-1]['stop_id'], 'stop')
        self.assertEqual(rows[-1]['stop_class'], 'requested')
        assert_requested_ending(self, self.store.snapshot()['records'], rows[-1], 'stop')

    def test_driver_keeps_a_met_boundary_when_release_wins_the_settlement_read(self):
        from research_harness import review_series as series, review_stop
        self.stop()
        records = self.store.snapshot()['records']
        boundary = review_stop.describe_driver_stop(records, records['review_stop']['stop'], 'first')
        plan = dict(state='run', chain='fixture', head_id='first', progress={},
                    command='review-run', payload=dict(id='new', assignment_id='first'))
        def refuse_run(*args, **kwargs):
            raise ResearchError('stale_revision', 'Synthetic concurrent revision.')
        rows = []
        with patch.object(series, '_read_plan', return_value=(self.store.snapshot(), plan)), \
                patch.object(review_stop, 'read_driver_boundary', side_effect=[None, None, boundary, None]), \
                patch.object(review_stop, 'settle_stop', side_effect=lambda *args, **kwargs: self.release()), \
                patch.dict(cli.OPERATIONS, {'review-run':refuse_run}):
            self.assertEqual(series.run_series(self.store, 'first', emit=rows.append), 3)
        self.assertEqual(rows[-1]['stop_id'], 'stop')
        records = self.store.snapshot()['records']
        assert_requested_ending(self, records, rows[-1], 'stop')
        self.assertNotIn('new', records.get('review_invocation', {}))

    def test_stop_between_plan_and_action_acknowledges_without_dispatch(self):
        from research_harness import review_series as series
        def plan(*args):
            self.stop('after_turn')
            return self.store.snapshot(), dict(state='run', chain='fixture', head_id='first',
                progress={}, command='review-run', payload=dict(id='new', assignment_id='first'))
        rows = []
        with patch.object(series, '_read_plan', side_effect=plan), \
                patch.dict(cli.OPERATIONS, {'review-run': lambda *a, **kw: self.fail('No stopped dispatch')}):
            self.assertEqual(series.run_series(self.store, 'first', emit=rows.append), 3)
        self.assertEqual(rows[-1]['stop_id'], 'stop')
        assert_requested_ending(self, self.store.snapshot()['records'], rows[-1], 'stop')


class QueueTests(StopQueueFixture):
    def enqueue(self, identifier='waiting', assignment='first', route='route', owner=None):
        from research_harness.review_stop import build_chain_scope
        scope = build_chain_scope(self.store.snapshot()['records'], assignment)
        call = dict(operation='review.invoke', invocation_id=identifier, route_id=route,
                    assignment_id=assignment, scope=scope)
        return admission.record_wait(self.store, call, owner=owner or worker._identity())

    def test_stop_withdraws_only_its_waits_and_no_call_starts(self):
        first, other = self.enqueue(), self.enqueue('other-wait', 'other')
        self.stop('after_turn')
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_call_wait'][first['id']]['state'], 'withdrawn')
        self.assertEqual(records['review_call_wait'][other['id']]['state'], 'waiting')
        self.refuse('review_chain_stopped', self.start)
        self.assertNotIn('call', records.get('review_invocation', {}))

    def test_status_lists_live_waits_in_request_order(self):
        first, second = self.enqueue(), self.enqueue('other-wait', 'other')
        self.enqueue('gone', owner=dict(pid=987654321, pgid=987654321, started='gone'))
        records = self.store.snapshot()['records']
        records.pop('review_epoch')
        report = cli._review_status(records, self.store.build_artifact_store())
        self.assertEqual([row['id'] for row in report['queue']], [first['id'], second['id']])

    def test_older_wait_runs_before_a_chain_gets_its_next_turn(self):
        wait = self.enqueue('single', 'other')
        self.refuse('review_capacity_wait', self.start)
        self.start('other', 'single')
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_call_wait'][wait['id']]['state'], 'admitted')

    def test_dead_wait_is_abandoned_but_a_live_slow_wait_keeps_its_place(self):
        dead = dict(pid=987654321, pgid=987654321, started='gone')
        wait = self.enqueue(owner=dead)
        self.start()
        self.assertEqual(self.store.snapshot()['records']['review_call_wait'][wait['id']]['state'], 'abandoned')

    def test_planned_wait_names_its_own_run_and_all_calls_before_it(self):
        self.start('other')
        self.enqueue('waiting', 'other')
        from research_harness.review_continuation import _plan_capacity
        records = self.store.snapshot()['records']
        action = dict(state='run', command='review-run', payload=dict(id='new', assignment_id='first'))
        plan = _plan_capacity(records, records['review_assignment']['first'], action)
        self.assertEqual(plan['payload'], action['payload'])
        self.assertEqual(plan['details']['calls_before'], 2)

    def test_planned_wait_releases_a_gone_owners_capture_before_its_own_run(self):
        membership = self.start('other', 'captured')
        self.put('review_pool_membership', membership['id'], dict(membership,
            owner=dict(pid=987654321, pgid=987654321, started='gone')))
        self.put('review_call_observation', membership['id'], dict(id=membership['id'],
            request_id='original-capture', observation=dict(status='completed', output=self.request)))
        from research_harness.review_continuation import _plan_capacity
        records = self.store.snapshot()['records']
        capture = records['review_call_observation'][membership['id']]
        action = dict(state='run', command='review-run', payload=dict(id='new', assignment_id='first'))
        plan = _plan_capacity(records, records['review_assignment']['first'], action)
        self.assertEqual(plan['details']['calls_before'], 1)
        self.assertEqual(plan['payload'], dict(action='reconcile', operation='review.invoke', invocation_id='captured'))
        cli.OPERATIONS[plan['command']](self.store, plan['payload'], request_id='release-captured-capacity')
        records = self.store.snapshot()['records']
        self.assertTrue(records['review_pool_membership'][membership['id']]['released'])
        self.assertEqual(records['review_call_observation'][membership['id']], capture)
        self.assertNotIn('captured', records.get('review_attempt', {}))
        self.assertEqual(_plan_capacity(records, records['review_assignment']['first'], action), action)

    def test_planned_wait_reconciles_the_dead_probe_when_another_operation_shares_its_id(self):
        execution.record_capacity(self.store, dict(store_limit=2, route_limits={}), request_id='two-calls')
        live = self.start(identifier='shared')
        invocation = dict(id='shared', route_id='route', request=self.request)
        authority = type('Authority', (), dict(reservation_id=execution.identify_call('review.route_probe', 'shared'),
            token='probe-token', identity=dict(pid=987654321, pgid=987654321, started='gone')))()
        revision = self.store.revision + 1
        def start_probe(tx):
            execution.start_call(tx, 'review.route_probe', invocation, authority, revision)
            tx.put('review_probe_invocation', 'shared', invocation)
        self.store.mutate('fixture.probe', invocation, start_probe, expected_revision=self.store.revision,
                          request_id='start-probe')
        from research_harness.review_continuation import _plan_capacity
        records = self.store.snapshot()['records']
        plan = _plan_capacity(records, records['review_assignment']['other'], dict(state='run', command='review-run',
            payload=dict(id='new', assignment_id='other')))
        self.assertEqual(plan['payload'], dict(action='reconcile', operation='review.route_probe', invocation_id='shared'))
        cli.OPERATIONS[plan['command']](self.store, plan['payload'], request_id='reconcile-probe')
        records = self.store.snapshot()['records']
        self.assertTrue(records['review_pool_membership'][authority.reservation_id]['released'])
        self.assertFalse(records['review_pool_membership'][live['id']]['released'])

    def test_planned_wait_keeps_its_own_bounded_run_while_an_accepted_calls_transport_lives(self):
        membership = self.start('other')
        child = dict(pid=40, pgid=40, started='live-child')
        self.put('review_pool_membership', membership['id'], dict(membership,
            owner=dict(pid=987654321, pgid=987654321, started='gone'), children=[child]))
        self.put('review_attempt', 'call', dict(id='call', assignment_id='other', status='failed', output=None))
        from research_harness.review_continuation import _plan_capacity
        records = self.store.snapshot()['records']
        action = dict(state='run', command='review-run', payload=dict(id='new', assignment_id='first'))
        with patch.object(worker, '_process_table', return_value={child['pid']:child}):
            plan = _plan_capacity(records, records['review_assignment']['first'], action)
        self.assertEqual(plan['payload'], action['payload'])
        self.assertEqual(plan['details']['calls_before'], 1)

    def test_uncontended_call_writes_no_wait(self):
        self.start()
        self.assertNotIn('review_call_wait', self.store.snapshot()['records'])
        self.enqueue('later', 'other')
        self.assertEqual(len(self.store.snapshot()['records']['review_call_wait']), 1)

    def test_wait_loop_retries_after_capacity_release_and_observes_withdrawal(self):
        from research_harness.review_stop import build_chain_scope
        call = dict(operation='review.invoke', invocation_id='new', route_id='route', assignment_id='first',
                    scope=build_chain_scope(self.store.snapshot()['records'], 'first'))
        attempts = []
        def attempt(waiting):
            attempts.append(waiting)
            if len(attempts) == 1:
                raise ResearchError('review_capacity_wait', 'Busy', dict(call=call, wait_seconds=120))
            self.start(identifier='new')
            return 'started'
        with patch.object(admission.time, 'sleep', return_value=None):
            self.assertEqual(admission.wait_for_admission(self.store, attempt), 'started')
        self.assertEqual(attempts, [False, True])
        self.assertEqual(next(iter(self.store.snapshot()['records']['review_call_wait'].values()))['state'], 'admitted')

    def test_expired_wait_cannot_start_even_when_capacity_becomes_free(self):
        from research_harness.review_stop import build_chain_scope
        call = dict(operation='review.invoke', invocation_id='late', route_id='route', assignment_id='first',
                    scope=build_chain_scope(self.store.snapshot()['records'], 'first'))
        clock = [0]
        def attempt(waiting):
            if waiting:
                self.fail('The deadline passed before capacity became free')
            raise ResearchError('review_capacity_wait', 'Synthetic occupied capacity.', dict(call=call, wait_seconds=1))
        def sleep(seconds):
            clock[0] = 2
        with patch.object(admission.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(admission.time, 'sleep', side_effect=sleep):
            self.refuse('review_capacity_wait', lambda: admission.wait_for_admission(self.store, attempt))
        self.assertNotIn('late', self.store.snapshot()['records'].get('review_invocation', {}))

    def test_wait_timeout_withdraws_and_stop_wins_before_a_retry(self):
        from research_harness.review_stop import build_chain_scope
        call = dict(operation='review.invoke', invocation_id='new', route_id='route', assignment_id='first',
                    scope=build_chain_scope(self.store.snapshot()['records'], 'first'))
        def blocked(waiting):
            if waiting:
                self.fail('Stopped waits cannot attempt admission')
            raise ResearchError('review_capacity_wait', 'Busy', dict(call=call, wait_seconds=120))
        def sleep(seconds):
            self.stop('after_turn')
        with patch.object(admission.time, 'sleep', side_effect=sleep):
            self.refuse('review_chain_stopped', lambda: admission.wait_for_admission(self.store, blocked))
        call['invocation_id'] = 'timeout'
        def timeout(waiting):
            raise ResearchError('review_capacity_wait', 'Busy', dict(call=call, wait_seconds=0))
        self.release()
        self.refuse('review_capacity_wait', lambda: admission.wait_for_admission(self.store, timeout))
        rows = self.store.snapshot()['records']['review_call_wait'].values()
        self.assertTrue(any(row.get('reason') == 'timeout' and row['state'] == 'withdrawn' for row in rows))


class ClosedCallTests(StopQueueFixture):
    def test_after_post_keeps_one_call_when_its_process_exits_during_projection(self):
        from research_harness import review_continuation, problem_delivery
        running = dict(operation='review.invoke', invocation_id='call', state='running')
        report = dict(review={}, obligations=[], after_post=[running], next=None)
        with patch.object(problem_delivery, 'describe_posted_calls',
                          return_value=[dict(running, state='interrupted')]), \
                patch.object(review_continuation, 'list_chain_heads', return_value=[]):
            projected = review_continuation.annotate_review_obligations({}, self.store.build_artifact_store(), report)
        self.assertEqual(projected['after_post'], [running])

    def test_usable_accepted_call_stays_visible_until_capacity_is_released(self):
        membership = self.start()
        attempt = dict(id='call', assignment_id='first', status='completed', output=self.request)
        self.put('review_attempt', 'call', attempt)
        from research_harness import review_stop
        records = self.store.snapshot()['records']
        with patch('research_harness.review_failure_class.classify_attempt', return_value=None):
            rows = review_stop.describe_closed_calls(records, self.store.build_artifact_store(),
                                                     records['review_assignment']['first'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'accepted')
        self.assertEqual(rows[0]['step']['payload']['action'], 'reconcile')
        self.assertEqual(self.store.snapshot()['records']['review_attempt']['call'], attempt)

    def test_posted_calls_keep_each_state_and_only_administrative_steps(self):
        from research_harness import problem_delivery
        invocations = {}
        for identifier in ('historical', 'interrupted', 'captured', 'accepted', 'failed', 'running'):
            invocations[identifier] = dict(id=identifier, route_id='route', assignment_id='first', request=self.request)
            self.put('review_invocation', identifier, invocations[identifier])
            if identifier == 'historical':
                continue
            lifecycle_id = execution.identify_call('review.invoke', identifier)
            self.put('review_pool_membership', lifecycle_id, dict(id=lifecycle_id, operation='review.invoke',
                invocation_id=identifier, owner=(worker._identity() if identifier == 'running' else
                    dict(pid=987654321, pgid=987654321, started='gone')),
                children=[], state='terminated' if identifier == 'failed' else 'running',
                released=identifier == 'failed'))
            if identifier in ('accepted', 'failed'):
                self.put('review_attempt', identifier, dict(id=identifier, assignment_id='first', status='failed',
                    output=None, error=dict(code='review_invocation_interrupted'), recorded_revision=self.store.revision))
            if identifier == 'captured':
                self.put('review_call_observation', lifecycle_id, dict(id=lifecycle_id, operation='review.invoke',
                    invocation_id=identifier, request_id='captured-request', observation=dict(status='completed', output=self.request)))
        records = self.store.snapshot()['records']
        records['problem_post'] = {'post': {'id':'post'}}
        with patch.object(problem_delivery, 'find_review_post', return_value={'id':'post'}):
            rows = problem_delivery.describe_posted_calls(records, self.store.build_artifact_store())
        by_id = {row['invocation_id']: row for row in rows}
        self.assertEqual({key: row['state'] for key, row in by_id.items()},
                         dict(historical='historical', interrupted='interrupted', captured='captured',
                              accepted='accepted', failed='settled', running='running'))
        self.assertEqual(by_id['historical']['step'], execution.build_historical_attempt_step(invocations['historical']))
        self.assertEqual(by_id['captured']['step'], dict(command='review-run', request_id='captured-request',
            payload=dict(id='captured', assignment_id='first')))
        for identifier in ('interrupted', 'accepted'):
            self.assertEqual(by_id[identifier]['step']['payload']['action'], 'reconcile')
        self.assertEqual(by_id['running']['step']['command'], 'review-stop')
        self.assertTrue(by_id['failed']['closed'])
        self.assertNotIn('step', by_id['failed'])


class CapturedStopTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_release_continues_the_same_chain_to_a_recorded_final(self):
        import test_review_continuation as continuations
        from research_harness import review_protocol as protocol, review_series, review_continuation
        fixture = continuations.ContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        store = fixture.case.store
        payload = dict(id='interrupted', assignment_id='bar-first')
        with patch.object(protocol, '_observed', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                fixture.case.mutate(protocol.invoke_assignment, payload, credential='fixture')
        stopped = cli.OPERATIONS['review-stop'](store, dict(id='stop', action='stop', assignment_id='bar-first',
            mode='now', reason='Synthetic stop before continuation.'), request_id='stop')['result']
        self.assertEqual(stopped['call_states'][0]['state'], 'settled')
        plan = review_continuation.plan_next(store.snapshot()['records'], fixture.case.artifacts, 'bar-first')
        self.assertEqual(plan['details']['step']['payload'], dict(action='release', stop_id='stop'))
        self.assertTrue(cli.status_report(store)['review']['assignments']['bar-first']['last_stop']['boundary_stands'])
        cli.OPERATIONS['review-stop'](store, dict(id='release', action='release', stop_id='stop',
            reason='Continue the existing review.'), request_id='release')
        response = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
        rows = []
        with patch('research_harness.review_transport.send_request', return_value=response) as send:
            self.assertEqual(review_series.run_series(store, 'bar-first', emit=rows.append), 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(rows[-1]['state'], 'done')
        self.assertEqual(len(store.snapshot()['records']['value_review']), 1)

    def test_dead_owner_capture_finishes_original_operation_and_preserves_old_bindings(self):
        import test_review_call_capture_recovery as captures
        fixture = captures.CallCaptureRecoveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.run_probe()
        fixture.assign()
        payload, request_id = dict(id='retained', assignment_id='assignment'), 'original-call'
        fixture.run_process('review-run', payload, request_id, fixture.store.revision, 'capture', {'finding':'retained'})
        before = fixture.records()
        invocation = copy.deepcopy(before['review_invocation']['retained'])
        capture = copy.deepcopy(before['review_call_observation'][execution.identify_call('review.invoke', 'retained')])
        stop = cli.OPERATIONS['review-stop'](fixture.store, dict(id='stop', action='stop', scope='store',
            mode='now', reason='Synthetic administrative stop.'), request_id='stop')['result']
        records = fixture.records()
        self.assertEqual(records['review_invocation']['retained'], invocation)
        self.assertEqual(records['review_call_observation'][capture['id']], capture)
        self.assertEqual(records['review_attempt']['retained']['status'], 'completed')
        self.assertTrue(records['review_pool_membership'][capture['id']]['released'])
        self.assertEqual(stop['call_states'][0]['state'], 'settled')
        with patch('research_harness.review_protocol._observed', side_effect=AssertionError('No replay call')):
            replay = cli.OPERATIONS['review-run'](fixture.store, payload,
                expected_revision=0, request_id=request_id)
        self.assertEqual(replay['result']['status'], 'completed')


class StagedStopTests(StopQueueFixture):
    def test_released_failed_capture_still_settles_through_its_original_dispatch(self):
        reservation = self.reserve(request_id='original:reserve')['result']
        self.put('review_task_dispatch', 'original', dict(id='attempt', assignment_id='first', task_id='a'))
        self.put('review_observation', reservation['id'], dict(reservation_id=reservation['id'],
                 observation=dict(status='failed', output=None)))
        self.put('review_reservation', reservation['id'], dict(reservation, released=True, state='captured',
                 observation_id=reservation['id']))
        from research_harness import review_stop
        stop = review_stop.request_stop(self.store, dict(id='stop', action='stop', assignment_id='first',
            mode='now', reason='Synthetic capture settlement.'), request_id='stop')['result']
        self.assertEqual([call['reservation_id'] for call in stop['calls']], [reservation['id']])
        row = review_stop.describe_call(self.store.snapshot()['records'], stop['calls'][0])
        self.assertEqual(row['state'], 'captured')
        self.assertEqual(row['step'], dict(command='review-run', request_id='original',
            payload=dict(id='attempt', assignment_id='first')))

    def test_staged_wait_names_its_task_epoch_and_controller(self):
        self.reserve()
        try:
            self.reserve('b')
        except ResearchError as error:
            self.assertEqual(error.code, 'review_capacity_wait')
            wait = admission.record_wait(self.store, error.details['call'])
        else:
            self.fail('The staged task must wait for capacity')
        self.assertEqual((wait['assignment_id'], wait['epoch_id'], wait['task_id']), ('first', 'epoch', 'b'))

    def test_timeout_counts_all_older_waits_while_capacity_is_full(self):
        membership = self.start()
        from research_harness import review_stop
        scope = review_stop.build_chain_scope(self.store.snapshot()['records'], 'other')
        call = dict(operation='review.invoke', invocation_id='queued', route_id='route', scope=scope,
                    assignment_id='other')
        admission.record_wait(self.store, call)
        try:
            self.start('other', 'third')
        except ResearchError as error:
            self.assertEqual(error.code, 'review_capacity_wait')
            self.assertEqual(error.details['wait_seconds'], 1320)
        else:
            self.fail('Capacity remains occupied')


class StopReceiptTests(StopQueueFixture):
    def test_check_refuses_before_opening_the_workspace_or_payload(self):
        args = cli.build_parser().parse_args(['review-stop', '--workspace', str(self.root / 'absent'),
            '--file', str(self.root / 'absent.json'), '--expected-revision', '0', '--request-id', 'check', '--check'])
        self.refuse('check_unsupported', lambda: cli.run(args))

    def test_stop_receipt_replays_exactly_after_release_and_refuses_changed_payload(self):
        payload = dict(id='stop', action='stop', scope='store', mode='now', reason='Synthetic stop.')
        receipt = cli.OPERATIONS['review-stop'](self.store, payload, request_id='stop')
        self.assertEqual(receipt, self.store.committed_request('stop')['response'])
        self.release()
        replay = cli.OPERATIONS['review-stop'](self.store, payload, request_id='stop', expected_revision=0)
        self.assertEqual(replay, receipt)
        self.refuse('request_id_conflict', lambda: cli.OPERATIONS['review-stop'](
            self.store, dict(payload, reason='Changed'), request_id='stop'))


class StagedCaptureSettlementTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_acceptance_read_conflict_keeps_capture_retryable_without_a_refusal(self):
        import sys
        import test_review_staged_frontier as frontier
        import test_problem_post_review_terminal as posted
        fixture = frontier.FrontierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controller = fixture.create_controller()
        planned = fixture.command('review-assignment', dict(controller_assignment_id=controller['id']))
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]), \
                patch.object(execution, 'accept_observation', side_effect=RuntimeError('Before acceptance')):
            with self.assertRaisesRegex(RuntimeError, 'Before acceptance'):
                fixture.command('review-run', dict(planned['next'][0]['payload'], id='retained-attempt'))
        before = fixture.case.read_records()
        reservation = next(iter(before['review_reservation'].values()))
        posted.record_external_post(fixture.case)
        step = next(row for row in cli.status_report(fixture.case.store)['after_post']
                    if row.get('reservation_id') == reservation['id'])['step']
        accept = execution.accept_observation
        def race_acceptance(store, payload, validate, **identity):
            def change_after_validation(records, captured):
                receipt = validate(records, captured)
                state = store.snapshot()['records']['review_task_execution'][captured['task_id']]
                fixture.case.put('review_task_execution', captured['task_id'],
                                 dict(state, generation=state['generation'] + 1))
                return receipt
            return accept(store, payload, change_after_validation, **identity)
        with patch.object(execution, 'accept_observation', side_effect=race_acceptance):
            with self.assertRaises(ResearchError) as caught:
                cli.OPERATIONS[step['command']](fixture.case.store, step['payload'], request_id=step['request_id'],
                                               expected_revision=fixture.case.store.revision)
        self.assertEqual(caught.exception.code, 'review_task_dependency_changed')
        self.assertIn('prepared_revision', caught.exception.details)
        records = fixture.case.read_records()
        self.assertNotIn(reservation['id'], records.get('review_task_refusal', {}))
        self.assertEqual(records['review_observation'], before['review_observation'])
        with patch.object(worker, 'launch_worker', side_effect=AssertionError('Retry acceptance without a new call')):
            cli.OPERATIONS[step['command']](fixture.case.store, step['payload'], request_id=step['request_id'],
                                           expected_revision=fixture.case.store.revision)
        records = fixture.case.read_records()
        self.assertIn(reservation['id'], records['review_task_acceptance'])
        self.assertNotIn(reservation['id'], records.get('review_task_refusal', {}))
        self.assertEqual((fixture.case.root / 'calls').read_text().splitlines(), ['frame'])

    def test_failed_capture_is_retained_after_release_and_post_without_another_call(self):
        import sys
        import test_review_staged_frontier as frontier
        import test_problem_post_review_terminal as posted
        from research_harness import review_staged_protocol as producer
        fixture = frontier.FrontierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controller = fixture.create_controller()
        planned = fixture.command('review-assignment', dict(controller_assignment_id=controller['id']))
        payload = planned['next'][0]['payload']
        program = frontier.WORKER.replace('return build_codex_response(route, request, output)',
                                         "raise OSError('Synthetic provider interruption')")
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', program]), \
                patch.object(producer, 'build_capture_attempt', side_effect=RuntimeError('Before attempt')):
            for identifier in ('first-failed-attempt', 'next-failed-attempt'):
                with self.assertRaisesRegex(RuntimeError, 'Before attempt'):
                    fixture.command('review-run', dict(payload, id=identifier))
        before = fixture.case.read_records()
        reservations = list(before['review_reservation'].values())
        self.assertEqual(len(reservations), 2)
        for reservation in reservations:
            self.assertTrue(reservation['released'])
            self.assertNotIn(reservation['id'], before.get('review_capture_attempt', {}))
        posted.record_external_post(fixture.case)
        with patch.object(worker, 'launch_worker', side_effect=AssertionError('No new reviewer call')):
            stopped = cli.OPERATIONS['review-stop'](fixture.case.store, dict(id='stop', action='stop', scope='store',
                mode='now', reason='Close the captured call.'), request_id='stop')['result']
        self.assertEqual([row['state'] for row in stopped['call_states']], ['settled', 'settled'])
        records = fixture.case.read_records()
        for reservation in reservations:
            attempt_id = records['review_capture_attempt'][reservation['id']]['attempt_id']
            self.assertEqual(records['review_attempt'][attempt_id]['status'], 'failed')
        self.assertEqual(records['review_task_execution'][reservation['task_id']],
                         before['review_task_execution'][reservation['task_id']])
        self.assertEqual((fixture.case.root / 'calls').read_text().splitlines(), ['frame', 'frame'])

    def test_old_failed_capture_finishes_after_a_retry_completes_the_epoch(self):
        import json
        import sys
        import test_review_staged_frontier as frontier
        import test_problem_post_review_terminal as posted
        from research_harness import review_staged_protocol as producer
        fixture = frontier.FrontierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controller = fixture.create_controller()
        planned = fixture.command('review-assignment', dict(controller_assignment_id=controller['id']))
        payload = planned['next'][0]['payload']
        program = frontier.WORKER.replace('return build_codex_response(route, request, output)',
                                         "raise OSError('Synthetic provider interruption')")
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', program]), \
                patch.object(producer, 'build_capture_attempt', side_effect=RuntimeError('Before attempt')):
            with self.assertRaisesRegex(RuntimeError, 'Before attempt'):
                fixture.command('review-run', dict(payload, id='first-failed-attempt'))
        before = fixture.case.read_records()
        old_reservation = next(iter(before['review_reservation'].values()))
        old_capture = before['review_observation'][old_reservation['id']]
        output = fixture.case.build_review_output('result')
        output['value'].update(objective_adequacy='not_assessed', method_adequacy='not_assessed')
        (fixture.case.root / 'scientific.json').write_text(json.dumps(output))
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            result = fixture.command('review-run', dict(payload, id='successful-retry'))
            for _ in range(20):
                step = result['next'][0]
                if step['command'] == 'value-review':
                    fixture.command(step['command'], step['payload'])
                    break
                result = fixture.command(step['command'], step['payload'])
            else:
                self.fail('The successful retry did not complete its epoch')
        before = fixture.case.read_records()
        self.assertEqual(before['review_slot_head'][controller['slot_id']]['state'], 'complete')
        posted.record_external_post(fixture.case)
        calls = (fixture.case.root / 'calls').read_bytes()
        with patch.object(worker, 'launch_worker', side_effect=AssertionError('No reviewer call during closure')):
            stopped = cli.OPERATIONS['review-stop'](fixture.case.store, dict(id='stop', action='stop', scope='store',
                mode='now', reason='Retain the older failed capture.'), request_id='stop')['result']
        self.assertEqual([row['state'] for row in stopped['call_states']], ['settled'])
        records = fixture.case.read_records()
        self.assertEqual(records['review_attempt']['first-failed-attempt']['status'], 'failed')
        self.assertEqual(records['review_observation'][old_reservation['id']], old_capture)
        self.assertEqual(records['review_slot_head'], before['review_slot_head'])
        self.assertEqual(records['value_review'], before['value_review'])
        self.assertEqual(records['review_task_execution'], before['review_task_execution'])
        self.assertEqual((fixture.case.root / 'calls').read_bytes(), calls)

    def test_retained_completed_attempt_still_finishes_its_scientific_acceptance_after_post(self):
        import sys
        import test_review_staged_frontier as frontier
        import test_problem_post_review_terminal as posted
        fixture = frontier.FrontierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controller = fixture.create_controller()
        planned = fixture.command('review-assignment', dict(controller_assignment_id=controller['id']))
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]), \
                patch.object(execution, 'accept_observation', side_effect=RuntimeError('Before acceptance')):
            with self.assertRaisesRegex(RuntimeError, 'Before acceptance'):
                fixture.command('review-run', dict(planned['next'][0]['payload'], id='retained-attempt'))
        before = fixture.case.read_records()
        reservation = next(iter(before['review_reservation'].values()))
        self.assertTrue(reservation['released'])
        self.assertNotIn(reservation['id'], before.get('review_task_acceptance', {}))
        attempt = before['review_attempt']['retained-attempt']
        from research_harness.review_continuation import list_chain_heads
        self.assertIn(controller['id'], list_chain_heads(before, fixture.case.artifacts))
        posted.record_external_post(fixture.case)
        report = cli.status_report(fixture.case.store)
        call = next(row for row in report['after_post'] if row.get('reservation_id') == reservation['id'])
        self.assertEqual(call['state'], 'captured')
        self.assertIn('request_id', call['step'])
        calls = (fixture.case.root / 'calls').read_bytes()
        with patch.object(worker, 'launch_worker', side_effect=AssertionError('No reviewer call during acceptance')):
            stopped = cli.OPERATIONS['review-stop'](fixture.case.store, dict(id='stop', action='stop', scope='store',
                mode='now', reason='Finish the committed scientific acceptance.'), request_id='stop')['result']
        self.assertEqual([row['state'] for row in stopped['call_states']], ['settled'])
        records = fixture.case.read_records()
        self.assertIn(reservation['id'], records['review_task_acceptance'])
        self.assertEqual(records['review_attempt']['retained-attempt'], attempt)
        self.assertEqual((fixture.case.root / 'calls').read_bytes(), calls)
        cli.OPERATIONS['review-stop'](fixture.case.store, dict(id='release', action='release', stop_id='stop',
            reason='The recorded call is settled.'), request_id='release')

    def test_accepted_staged_call_reconciles_capacity_instead_of_replaying_its_finished_receipt(self):
        import sys
        import test_review_staged_frontier as frontier
        import test_problem_post_review_terminal as posted
        from research_harness import review_staged_protocol as producer
        fixture = frontier.FrontierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controller = fixture.create_controller()
        planned = fixture.command('review-assignment', dict(controller_assignment_id=controller['id']))
        def leave_capacity_held(store, reservation_id, process, request_id):
            process.wait(timeout=20)
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]), \
                patch.object(producer, 'finish_worker', side_effect=leave_capacity_held):
            fixture.command('review-run', dict(planned['next'][0]['payload'], id='accepted-attempt'))
        before = fixture.case.read_records()
        reservation = next(iter(before['review_reservation'].values()))
        self.assertFalse(reservation['released'])
        self.assertIn(reservation['id'], before['review_task_acceptance'])
        posted.record_external_post(fixture.case)
        row = next(item for item in cli.status_report(fixture.case.store)['after_post']
                   if item.get('reservation_id') == reservation['id'])
        self.assertEqual(row['state'], 'accepted')
        self.assertEqual(row['step']['payload']['action'], 'reconcile')
        cli.OPERATIONS[row['step']['command']](fixture.case.store, row['step']['payload'], request_id='release-accepted')
        records = fixture.case.read_records()
        self.assertTrue(records['review_reservation'][reservation['id']]['released'])
        self.assertEqual(records['review_attempt'], before['review_attempt'])
        self.assertEqual(records['review_task_acceptance'], before['review_task_acceptance'])
        from research_harness import review_stop
        legacy = copy.deepcopy(records)
        legacy.pop('review_capture_attempt')
        row = review_stop.describe_call(legacy, dict(operation='review.task_reserve', reservation_id=reservation['id']))
        self.assertEqual(row['state'], 'settled')
        self.assertEqual(legacy['review_attempt'], before['review_attempt'])

    def test_ineligible_completed_capture_keeps_output_and_replays_a_committed_refusal_after_post(self):
        import sys
        import test_review_staged_frontier as frontier
        import test_problem_post_review_terminal as posted
        from research_harness import review_staged_protocol as producer, review_protocol as protocol
        fixture = frontier.FrontierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controller = fixture.create_controller()
        planned = fixture.command('review-assignment', dict(controller_assignment_id=controller['id']))
        with patch.object(worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]), \
                patch.object(producer, 'build_capture_attempt', side_effect=RuntimeError('Before attempt')):
            with self.assertRaisesRegex(RuntimeError, 'Before attempt'):
                fixture.command('review-run', dict(planned['next'][0]['payload'], id='completed-capture'))
        records = fixture.case.read_records()
        reservation = next(iter(records['review_reservation'].values()))
        captured = records['review_observation'][reservation['id']]
        fixture.case.mutate(protocol.record_context_event, dict(id='retire-route', assignment_id=controller['id'],
            source='owner', kind='route_retired', reason='The synthetic route is no longer eligible.',
            evidence=[fixture.case.supporting]))
        posted.record_external_post(fixture.case)
        row = next(item for item in cli.status_report(fixture.case.store)['after_post']
                   if item.get('reservation_id') == reservation['id'])
        step = row['step']
        calls = (fixture.case.root / 'calls').read_bytes()
        with patch.object(producer, 'finish_retained_capture', side_effect=RuntimeError('Before receipt')):
            with self.assertRaisesRegex(RuntimeError, 'Before receipt'):
                cli.OPERATIONS[step['command']](fixture.case.store, step['payload'], request_id=step['request_id'],
                                               expected_revision=fixture.case.store.revision)
        refusal = fixture.case.read_records()['review_task_refusal'][reservation['id']]
        self.assertEqual(refusal['code'], 'review_route_retired')
        self.assertIs(type(refusal['recorded_revision']), int)
        with patch.object(worker, 'launch_worker', side_effect=AssertionError('No reviewer call during refusal replay')):
            receipt = cli.OPERATIONS[step['command']](fixture.case.store, step['payload'], request_id=step['request_id'],
                                                   expected_revision=fixture.case.store.revision)
        self.assertEqual(receipt['result']['refusal']['code'], 'review_route_retired')
        records = fixture.case.read_records()
        self.assertEqual(records['review_task_refusal'][reservation['id']], refusal)
        self.assertEqual(records['review_observation'][reservation['id']], captured)
        self.assertEqual(records['review_attempt']['completed-capture']['status'], 'completed')
        self.assertEqual(records['review_attempt']['completed-capture']['output'], captured['observation']['output'])
        self.assertNotIn(reservation['id'], records.get('review_task_acceptance', {}))
        checked = execution.accept_observation(fixture.case.store, dict(reservation_id=reservation['id']),
            lambda *args: self.fail('A recorded refusal is replayed without acceptance'), request_id='read-refusal')
        self.assertEqual(checked['result']['refusal']['code'], 'review_route_retired')
        closed = next(item for item in cli.status_report(fixture.case.store)['after_post']
                      if item.get('reservation_id') == reservation['id'])
        self.assertEqual((closed['state'], closed['closed']), ('settled', True))
        self.assertNotIn('step', closed)
        self.assertEqual((fixture.case.root / 'calls').read_bytes(), calls)
