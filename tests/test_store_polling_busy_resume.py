"""Initial read retry and handshake contention preserve one resumable launch."""

import sys
import time
import unittest
from unittest.mock import patch

from research_harness import cli, review_worker
from research_harness import review_staged_protocol as producer
from research_harness.errors import ResearchError
import test_review_staged_frontier as frontier
import test_store_polling_busy as polling


class InitialReadTests(polling.WorkerPollingFixture):
    def test_initial_handshake_read_retries_before_claiming_or_launching(self):
        reservation = self.reserve()['result']
        snapshot = self.store.snapshot
        reads = []

        def read():
            reads.append(None)
            if len(reads) <= 2:
                self.assertIsNone(snapshot()['records']['review_reservation'][reservation['id']]['launch'])
                raise ResearchError('store_busy', 'Synthetic concurrent writer')
            return snapshot()

        with patch.object(self.store, 'snapshot', side_effect=read), \
                patch('research_harness.storage._BUSY_TIMEOUT_SECONDS', .001):
            process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=60), 0)
        current = snapshot()['records']['review_reservation'][reservation['id']]
        self.assertEqual(current['worker']['pid'], process.pid)
        self.assertEqual(current['state'], 'captured')
        self.assertEqual((self.root / 'calls').read_text().splitlines(), ['call'])

    def test_initial_capture_read_retries_and_refreshes_a_replayed_reservation(self):
        reservation = self.reserve()['result']
        process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=60), 0)
        snapshot = self.store.snapshot()
        current = snapshot['records']['review_reservation'][reservation['id']]
        error = ResearchError('store_busy', 'Synthetic concurrent writer')
        with patch.object(self.store, 'snapshot', side_effect=[error, error, snapshot, snapshot]), \
                patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No second worker')), \
                patch('research_harness.storage._BUSY_TIMEOUT_SECONDS', .001):
            captured, launched, returned = producer.wait_for_capture(
                self.store, {'configuration': {'timeout_seconds': 5}}, reservation, 'resume')
        self.assertEqual(captured['id'], current['observation_id'])
        self.assertEqual(returned, current)
        self.assertIsNone(launched)
        self.assertEqual((self.root / 'calls').read_text().splitlines(), ['call'])

    def test_persistent_initial_capture_contention_surfaces_busy_without_launching(self):
        reservation = self.reserve()['result']
        initial = self.store.snapshot()
        error = ResearchError('store_busy', 'Synthetic concurrent writer')
        clock = polling.PollClock()
        with patch.object(self.store, 'snapshot', side_effect=error), \
                patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No worker before a read')), \
                patch('research_harness.finishing.time', clock):
            with self.assertRaises(ResearchError) as caught:
                producer.wait_for_capture(
                    self.store, {'configuration': {'timeout_seconds': 5}}, reservation, 'resume')
        self.assertIs(caught.exception, error)
        self.assertEqual(clock.intervals, [1.0] * 7)
        self.assertEqual(self.store.snapshot(), initial)


class ReviewRunResumptionTests(unittest.TestCase):
    stop = polling.WorkerPollingFixture.stop

    def setUp(self):
        self.fixture = frontier.FrontierTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_busy_handshake_deadline_and_repeated_review_run_keep_one_reservation(self):
        controller = self.fixture.create_controller()
        planned = self.fixture.command('review-assignment', controller['next'][0]['payload'])
        payload = planned['next'][0]['payload']
        store = self.fixture.case.store
        revision = store.revision
        request_id = 'busy-handshake-run'
        snapshot = store.snapshot
        claim_launch = review_worker._claim_launch
        launch_worker = review_worker.launch_worker
        claimed = []
        bound_reservations = []
        processes = []
        release = self.fixture.case.root / 'release-handshake'
        bound = self.fixture.case.root / 'worker-bound'
        clock = polling.PollClock()
        gate = '''
import time
original_bind = review_worker._bind_worker
def bind(store, *args):
    result = original_bind(store, *args)
    (store.root / 'worker-bound').touch()
    deadline = time.monotonic() + 120
    while not (store.root / 'release-handshake').exists() and time.monotonic() < deadline:
        time.sleep(.01)
    return result
review_worker._bind_worker = bind
'''
        worker = frontier.WORKER.replace('review_worker.run_worker(', gate + '\nreview_worker.run_worker(')

        def claim(*args):
            result = claim_launch(*args)
            claimed.append(snapshot()['records']['review_reservation'][result['id']])
            return result

        def spawn(*args, **kwargs):
            process = review_worker._SYSTEM_POPEN(*args, **kwargs)
            processes.append(process)
            self.addCleanup(self.stop, process)
            self.addCleanup(release.touch)
            return process

        def read():
            if processes:
                deadline = time.monotonic() + 60
                while not bound.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(bound.exists(), 'The original worker did not bind its identity')
                if not bound_reservations:
                    bound_reservations.append(snapshot()['records']['review_reservation'][claimed[0]['id']])
                raise ResearchError('store_busy', 'Synthetic concurrent writer')
            return snapshot()

        def launch(current_store, reservation_id):
            return launch_worker(current_store, reservation_id, handshake_timeout=.05)

        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', worker]), \
                patch.object(review_worker, '_claim_launch', side_effect=claim), \
                patch.object(review_worker, 'launch_worker', side_effect=launch), \
                patch.object(review_worker.subprocess, 'Popen', side_effect=spawn), \
                patch.object(review_worker, 'time', clock), \
                patch.object(store, 'snapshot', side_effect=read):
            with self.assertRaises(ResearchError) as caught:
                cli.OPERATIONS['review-run'](store, payload, expected_revision=revision, request_id=request_id)
        self.assertEqual(caught.exception.code, 'review_worker_reconciliation_required')
        self.assertEqual(caught.exception.message, 'The worker launch handshake did not complete')
        self.assertAlmostEqual(clock.elapsed, .05)
        self.assertEqual(len(processes), 1)
        reservation = bound_reservations[0]
        self.assertEqual(snapshot()['records']['review_reservation'][reservation['id']], reservation)
        self.assertEqual(reservation['launch'], {'authorized': False})
        self.assertEqual(reservation['worker']['pid'], processes[0].pid)
        self.assertFalse(reservation['released'])

        # A live worker retains its reservation. Replaying the same operation reconciles it and waits.
        capture_clock = polling.PollClock()

        def advance_capture(seconds):
            capture_clock.sleep(45)

        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No second launch')), \
                patch.object(time, 'monotonic', side_effect=capture_clock.monotonic), \
                patch.object(time, 'sleep', side_effect=advance_capture):
            with self.assertRaises(ResearchError) as caught:
                cli.OPERATIONS['review-run'](store, payload, expected_revision=revision, request_id=request_id)
        self.assertEqual(caught.exception.code, 'review_worker_reconciliation_required')
        self.assertEqual(caught.exception.message, 'Resume the owned reservation; its outcome is still pending')
        self.assertEqual(snapshot()['records']['review_reservation'][reservation['id']], reservation)

        release.touch()
        self.assertEqual(processes[0].wait(timeout=60), 0)
        # After its EOF exit, every replay must use the same reconciliation path, including after release.
        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No second launch')), \
                patch.object(time, 'monotonic', side_effect=capture_clock.monotonic), \
                patch.object(time, 'sleep', side_effect=advance_capture):
            for _ in range(2):
                with self.assertRaises(ResearchError) as caught:
                    cli.OPERATIONS['review-run'](store, payload, expected_revision=revision, request_id=request_id)
                self.assertEqual(caught.exception.code, 'review_worker_reconciliation_required')
                self.assertEqual(caught.exception.message, 'The worker ended without a committed observation')
        records = snapshot()['records']
        current = records['review_reservation'][reservation['id']]
        self.assertEqual(len(records['review_reservation']), 1)
        self.assertEqual(current['attempt'], 1)
        self.assertEqual(current['state'], 'known_failure')
        self.assertTrue(current['released'])
        self.assertFalse(records['review_pool_membership'][reservation['id']]['active'])
        self.assertFalse((self.fixture.case.root / 'calls').exists())
