"""Completed captures and resource refusals survive interrupted controller work."""

import sys
import unittest
from unittest.mock import patch

from research_harness import review_task_execution as execution, review_worker
from research_harness import review_protocol as protocol
from research_harness import review_staged_protocol as producer
from research_harness.errors import ResearchError
import test_review_staged_frontier as fixtures
import test_review_task_execution_worker as workers
from research_harness.storage import Transaction


UNVERIFIED_WORKER = fixtures.WORKER.replace(
    'return build_codex_response(route, request, output)',
    "response = build_codex_response(route, request, output)\n"
    "    response['context_evidence']['input_verified'] = False\n"
    "    return response")


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

    def test_unverified_completed_capture_is_retained_released_and_retryable(self):
        controller = self.fixture.create_controller()
        planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', UNVERIFIED_WORKER]), \
                patch.object(producer, 'finish_worker', wraps=producer.finish_worker) as cleanup:
            failed = self.fixture.command('review-run', planned['next'][0]['payload'])
        cleanup.assert_called_once()
        records = self.fixture.case.read_records()
        capture = next(iter(records['review_observation'].values()))
        attempt = records['review_attempt'][failed['id']]
        self.assertEqual(attempt['status'], 'completed')
        self.assertEqual(attempt['output'], capture['observation']['output'])
        self.assertEqual(failed['refusal']['code'], 'review_invocation_unverified')
        self.assertEqual(failed['refusal']['recovery'], 'retry_after_termination')
        self.assertEqual(failed['refusal']['observation_digest'], producer.digest(capture))
        self.assertFalse(records.get('review_task_acceptance'))
        self.assertTrue(records['review_reservation'][capture['id']]['released'])
        refusal = records['review_task_refusal'][capture['id']]
        self.assertGreater(refusal['recorded_revision'], capture['recorded_revision'])
        raw = protocol.read_raw_observation(records, self.fixture.case.artifacts, attempt['id'])
        self.assertTrue(raw['raw_output']['claims'])
        child = records['review_assignment'][attempt['assignment_id']]
        self.assertTrue(producer.authenticate_capture_provenance(records, self.fixture.case.artifacts, child, attempt))
        self.assertFalse(producer.authenticate_observation(records, self.fixture.case.artifacts, child, attempt))
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', fixtures.WORKER]):
            retry = self.fixture.command('review-assignment', failed['next'][0]['payload'])
            accepted = self.fixture.command('review-run', retry['next'][0]['payload'])
        self.assertNotEqual(accepted['id'], failed['id'])
        records = self.fixture.case.read_records()
        self.assertEqual(len(records['review_observation']), 2)
        self.assertEqual(len(records['review_task_acceptance']), 1)
        self.assertEqual((self.fixture.case.root / 'calls').read_text().splitlines(), ['frame', 'frame'])

    def test_unverified_capture_recovery_survives_an_interrupted_acknowledgment(self):
        from research_harness import operations
        controller = self.fixture.create_controller()
        planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        prepare_task = operations.prepared_task_mutation
        request_id = 'operator-' + str(self.fixture.case.store.revision)

        def interrupt_recovery(store, operation, *args, **kwargs):
            result = prepare_task(store, operation, *args, **kwargs)
            if operation == 'review.task_recovery':
                raise RuntimeError('Recovery acknowledgment interrupted')
            return result

        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', UNVERIFIED_WORKER]), \
                patch.object(operations, 'prepared_task_mutation', side_effect=interrupt_recovery):
            with self.assertRaisesRegex(RuntimeError, 'Recovery acknowledgment interrupted'):
                self.fixture.command('review-run', planned['next'][0]['payload'])
        records = self.fixture.case.read_records()
        capture = next(iter(records['review_observation'].values()))
        self.assertTrue(records['review_reservation'][capture['id']]['released'])
        self.assertEqual(records['review_task_refusal'][capture['id']]['recovery'], 'retry_after_termination')
        self.assertEqual(len([row for row in records['review_attempt'].values()
                              if row.get('reservation_id') == capture['id']]), 1)
        dispatch = records['review_task_dispatch'][request_id]
        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('Resume the recovery receipt')):
            replay = protocol.invoke_assignment(self.fixture.case.store,
                dict(id=dispatch['id'], assignment_id=dispatch['assignment_id']),
                expected_revision=self.fixture.case.store.revision, request_id=request_id)['result']
        self.assertEqual(replay['refusal']['code'], 'review_invocation_unverified')
        self.assertEqual(replay['reservation_id'], capture['id'])
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', fixtures.WORKER]):
            retry = self.fixture.command('review-assignment', controller['next'][0]['payload'])
            accepted = self.fixture.command('review-run', retry['next'][0]['payload'])
        self.assertNotIn('refusal', accepted)
        self.assertNotEqual(accepted['reservation_id'], capture['id'])

    def test_retention_failure_still_cleans_up_and_replay_uses_the_capture(self):
        controller = self.fixture.create_controller()
        planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        # The attempt commit is a separate resumable operation. Interrupt that
        # boundary while allowing the already owned worker to capture normally.
        from research_harness import operations
        prepare_task = operations.prepared_task_mutation

        def interrupt_retention(store, operation, *args, **kwargs):
            if operation == 'review.task_attempt':
                raise RuntimeError('Attempt retention interrupted')
            return prepare_task(store, operation, *args, **kwargs)

        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', fixtures.WORKER]), \
                patch.object(operations, 'prepared_task_mutation', side_effect=interrupt_retention), \
                patch.object(producer, 'finish_worker', wraps=producer.finish_worker) as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'retention interrupted'):
                self.fixture.command('review-run', planned['next'][0]['payload'])
        cleanup.assert_called_once()
        records = self.fixture.case.read_records()
        capture = next(iter(records['review_observation'].values()))
        self.assertTrue(records['review_reservation'][capture['id']]['released'])
        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('Reuse the durable capture')):
            accepted = self.fixture.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(accepted['reservation_id'], capture['id'])
        self.assertEqual((self.fixture.case.root / 'calls').read_text().splitlines(), ['frame'])

    def test_external_verification_failure_does_not_authorize_resampling_a_valid_capture(self):
        controller = self.fixture.create_controller()
        planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', fixtures.WORKER]), \
                patch.object(producer, 'accept_capture', side_effect=ResearchError(
                    'review_invocation_unverified', 'A separate source review is not verified.')):
            with self.assertRaises(ResearchError) as failure:
                self.fixture.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(failure.exception.code, 'review_invocation_unverified')
        records = self.fixture.case.read_records()
        capture = next(iter(records['review_observation'].values()))
        self.assertFalse(records.get('review_task_refusal'))
        self.assertTrue(records['review_reservation'][capture['id']]['released'])
        self.assertEqual(len([row for row in records['review_attempt'].values() if row.get('reservation_id')]), 1)
        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('Resume the valid capture')):
            accepted = self.fixture.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(accepted['reservation_id'], capture['id'])
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
