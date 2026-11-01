"""Completed captures and resource refusals survive interrupted controller work."""

import sys
import unittest
from unittest.mock import patch

from research_harness import review_task_execution as execution, review_worker
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
import test_review_staged_frontier as fixtures
import test_review_task_execution_worker as workers
from research_harness.storage import Transaction


class ExecutionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FrontierTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_rejected_capture_persists_attempt_before_acceptance_acknowledgment(self):
        controller = self.fixture.create_controller()
        (self.fixture.case.root / 'refuse').touch()
        planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        accept = execution.accept_observation

        def lose_acknowledgment(*args, **kwargs):
            accept(*args, **kwargs)
            raise RuntimeError('Controller interrupted after the refusal committed')

        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', fixtures.WORKER]), \
                patch.object(execution, 'accept_observation', side_effect=lose_acknowledgment):
            with self.assertRaisesRegex(RuntimeError, 'Controller interrupted'):
                self.fixture.command('review-run', planned['next'][0]['payload'])
        records = self.fixture.case.read_records()
        refusal = next(iter(records['review_task_refusal'].values()))
        attempts = list(records.get('review_attempt', {}).values())
        self.assertEqual(len([row for row in attempts if row.get('reservation_id') == refusal['reservation_id']]), 1)
        self.assertFalse(records.get('review_task_acceptance'))
        self.assertEqual((self.fixture.case.root / 'calls').read_text().splitlines(), ['frame'])

    def test_elapsed_limit_at_dispatch_persists_the_ledger_and_execution_outcome(self):
        controller = self.fixture.create_controller()
        planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        reserve = execution.reserve_task

        def reserve_after_deadline(*args, **kwargs):
            with patch('time.time', return_value=controller['staged_started_at'] + 86401):
                return reserve(*args, **kwargs)

        with patch.object(execution, 'reserve_task', side_effect=reserve_after_deadline):
            result = self.fixture.command('review-run', planned['next'][0]['payload'])
        records = self.fixture.case.read_records()
        outcome = result['outcome']
        self.assertEqual(outcome['origin'], 'execution')
        self.assertEqual(records['review_slot_head'][controller['slot_id']]['state'], 'incomplete')
        resources = records.get('review_resource_ledger', {}).get(outcome['epoch_id'])
        self.assertIsNotNone(resources)
        self.assertGreaterEqual(resources['elapsed_seconds'], 86401)
        self.assertEqual(resources['attempts'], 0)
        self.assertEqual(outcome['resource_ledger']['elapsed_seconds'], resources['elapsed_seconds'])
        for identifier in outcome['origin_binding']['execution_receipts']:
            self.assertIn(identifier, records['review_execution_exhaustion'])
        self.assertFalse(records.get('review_reservation'))
        self.assertEqual(result['next'][0]['command'], 'review-assignment')

    def test_public_ordinary_and_probe_calls_observe_a_live_staged_reservation(self):
        controller = self.fixture.create_controller()
        task_id = controller['staged_review']['task_ids'][0]
        task = self.fixture.case.read_records()['review_task'][task_id]
        execution.reserve_task(self.fixture.case.store, dict(task_id=task_id, task_generation=1,
            epoch_id=task['epoch_id'], route_id=controller['route_id'], request=task['request']),
            request_id='occupy-pool')
        ordinary = self.fixture.assign('ordinary-during-staged', reviewer='second')
        for action in (
                lambda: self.fixture.invoke(ordinary, output=self.fixture.case.build_review_output('result')),
                lambda: protocol.probe_route(self.fixture.case.store, dict(id='blocked-probe', route_id='route'),
                    expected_revision=self.fixture.case.store.revision, request_id='blocked-probe')):
            with self.assertRaises(ResearchError) as failure:
                action()
            self.assertEqual(failure.exception.code, 'review_capacity_wait')


class IndexRecoveryTests(unittest.TestCase):
    def capture_g2(self):
        fixture = workers.WorkerExecutionTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        put = Transaction.put

        def write_g2_record(transaction, kind, key, value):
            if kind != 'review_epoch_discoveries':
                put(transaction, kind, key, value)

        with patch.object(Transaction, 'put', new=write_g2_record):
            reservation = fixture.reserve()['result']
        process = fixture.launch(reservation)
        self.assertEqual(process.wait(timeout=6), 0)
        return fixture, reservation

    def test_a_completed_g2_capture_can_accept_its_first_context_discovery(self):
        fixture, reservation = self.capture_g2()
        result = execution.accept_observation(fixture.store, dict(reservation_id=reservation['id']),
            lambda records, capture:dict(staged_observation=dict(epoch_id='epoch',
                output=dict(new_dependencies=[dict(claim_id='one', depends_on='two')]))),
            expected_revision=fixture.store.revision, request_id='accept-old-capture')
        records = fixture.store.snapshot()['records']
        self.assertEqual(result['result']['task_id'], 'a')
        self.assertEqual(records['review_epoch_discoveries']['epoch']['acceptance_ids'], [reservation['id']])
        self.assertEqual(records['review_task_acceptance_index']['a']['reservation_id'], reservation['id'])

    def test_frame_dependencies_enter_the_same_context_discovery_index(self):
        fixture, reservation = self.capture_g2()
        fixture.accept(reservation, validate=lambda records, capture:dict(staged_observation=dict(
            epoch_id='epoch', output=dict(dependencies=[dict(claim_id='one', depends_on='two')]))))
        records = fixture.store.snapshot()['records']
        self.assertEqual(records['review_epoch_discoveries']['epoch']['acceptance_ids'], [reservation['id']])

    def test_irrelevant_claim_fields_cannot_trap_a_completed_non_frame_capture(self):
        fixture, reservation = self.capture_g2()
        result = fixture.accept(reservation, validate=lambda records, capture:dict(staged_observation=dict(
            epoch_id='epoch', output=dict(cards=[], claims=None, conditions='not a frame output'))))
        self.assertEqual(result['result']['task_id'], 'a')
        self.assertEqual(fixture.current(reservation)['state'], 'accepted')
