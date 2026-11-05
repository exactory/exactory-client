"""Store contention delays polling without replacing each waiter's deadline."""

import os
import time
from itertools import chain, repeat
from unittest.mock import patch

from development_fixtures import DevelopmentCase
from integration_fixtures import admit_lab
from research_harness import execution, remote_execution, review_staged_protocol, review_worker
from research_harness.errors import ResearchError
from test_review_task_execution import TaskExecutionFixture
import test_review_task_execution_worker as worker_tests


class PollClock:
    def __init__(self):
        self.elapsed = 0
        self.intervals = []

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.intervals.append(seconds)
        self.elapsed += seconds


class WorkerPollingFixture(TaskExecutionFixture):
    entry = worker_tests.WorkerExecutionTests.entry
    stop = worker_tests.WorkerExecutionTests.stop

    def launch(self, reservation, **options):
        def spawn(*args, **kwargs):
            process = review_worker._SYSTEM_POPEN(*args, **kwargs)
            self.addCleanup(self.stop, process)
            return process

        with patch.object(review_worker, '_entry_command', return_value=self.entry('normal')), \
                patch.object(review_worker.subprocess, 'Popen', side_effect=spawn):
            return review_worker.launch_worker(self.store, reservation['id'], **options)


class WorkerPollingTests(WorkerPollingFixture):
    def test_handshake_retries_busy_reads_and_authorizes_the_original_worker(self):
        reservation = self.reserve()['result']
        snapshot = self.store.snapshot
        reads = []

        def read():
            reads.append(None)
            if len(reads) in (2, 3):
                raise ResearchError('store_busy', 'Synthetic concurrent writer')
            return snapshot()

        with patch.object(self.store, 'snapshot', side_effect=read):
            process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=60), 0)
        current = snapshot()['records']['review_reservation'][reservation['id']]
        self.assertTrue(current['launch']['authorized'])
        self.assertEqual(current['worker']['pid'], process.pid)
        self.assertEqual(current['state'], 'captured')
        self.assertEqual((self.root / 'calls').read_text().splitlines(), ['call'])

    def test_persistent_busy_handshake_keeps_its_deadline_and_unauthorized_claim(self):
        reservation = self.reserve()['result']
        initial = self.store.snapshot()
        clock = PollClock()
        with patch.object(self.store, 'snapshot', side_effect=[initial] + [
                ResearchError('store_busy', 'Synthetic concurrent writer') for _ in range(20)]), \
                patch.object(review_worker, 'time', clock):
            with self.assertRaises(ResearchError) as caught:
                self.launch(reservation, handshake_timeout=.05)
        self.assertEqual(caught.exception.code, 'review_worker_reconciliation_required')
        self.assertEqual(caught.exception.message, 'The worker launch handshake did not complete')
        self.assertAlmostEqual(clock.elapsed, .05)
        self.assertTrue(all(interval == .01 for interval in clock.intervals))
        current = self.store.snapshot()['records']['review_reservation'][reservation['id']]
        self.assertEqual(current['launch'], {'authorized': False})
        self.assertFalse((self.root / 'calls').exists())

    def test_other_handshake_read_refusals_surface_without_sleeping(self):
        reservation = self.reserve()['result']
        initial = self.store.snapshot()
        error = ResearchError('stale_revision', 'Synthetic refusal')
        clock = PollClock()
        with patch.object(self.store, 'snapshot', side_effect=[initial, error]), \
                patch.object(review_worker, 'time', clock):
            with self.assertRaises(ResearchError) as caught:
                self.launch(reservation)
        self.assertIs(caught.exception, error)
        self.assertEqual(clock.intervals, [])

    def test_busy_read_before_handshake_has_a_separate_retry_bound(self):
        reservation = self.reserve()['result']
        error = ResearchError('store_busy', 'Synthetic concurrent writer')
        clock = PollClock()
        with patch.object(self.store, 'snapshot', side_effect=error), \
                patch('research_harness.finishing.time', clock):
            with self.assertRaises(ResearchError) as caught:
                self.launch(reservation)
        self.assertIs(caught.exception, error)
        self.assertEqual(clock.intervals, [1.0] * 7)
        current = self.store.snapshot()['records']['review_reservation'][reservation['id']]
        self.assertIsNone(current['launch'])


class CapturePollingTests(WorkerPollingFixture):
    def test_capture_retries_busy_reads_then_returns_the_durable_observation(self):
        reservation = self.reserve()['result']
        pending = self.store.snapshot()
        process = self.launch(reservation)
        self.assertEqual(process.wait(timeout=60), 0)
        snapshot = self.store.snapshot()
        current = snapshot['records']['review_reservation'][reservation['id']]
        observation = snapshot['records']['review_observation'][current['observation_id']]
        with patch.object(self.store, 'snapshot', side_effect=[snapshot,
                ResearchError('store_busy', 'Synthetic concurrent writer'),
                ResearchError('store_busy', 'Synthetic concurrent writer'), pending, snapshot]):
            captured, launched, returned = review_staged_protocol.wait_for_capture(
                self.store, {'configuration': {'timeout_seconds': 5}}, current, 'wait')
        self.assertEqual(captured, observation)
        self.assertEqual(returned, current)
        self.assertIsNone(launched)

    def test_persistent_busy_capture_keeps_its_pending_outcome_deadline(self):
        reservation = self.reserve()['result']
        initial = self.store.snapshot()
        clock = PollClock()
        with patch.object(review_worker, 'launch_worker', return_value=None), \
                patch.object(self.store, 'snapshot', side_effect=chain(
                    [initial], repeat(ResearchError('store_busy', 'Synthetic writer')))), \
                patch.object(time, 'monotonic', side_effect=clock.monotonic), \
                patch.object(time, 'sleep', side_effect=clock.sleep):
            with self.assertRaises(ResearchError) as caught:
                review_staged_protocol.wait_for_capture(
                    self.store, {'configuration': {'timeout_seconds': 1}}, reservation, 'wait')
        self.assertEqual(caught.exception.code, 'review_worker_reconciliation_required')
        self.assertEqual(caught.exception.message, 'Resume the owned reservation; its outcome is still pending')
        self.assertLess(abs(clock.elapsed - 16), .05)
        self.assertTrue(all(interval == .05 for interval in clock.intervals))

    def test_other_capture_read_errors_surface_without_sleeping(self):
        reservation = self.reserve()['result']
        for error in (ResearchError('stale_revision', 'Synthetic refusal'), ValueError('Synthetic defect')):
            with self.subTest(error=type(error).__name__):
                clock = PollClock()
                with patch.object(review_worker, 'launch_worker', return_value=None), \
                        patch.object(self.store, 'snapshot', side_effect=error), \
                        patch.object(time, 'sleep', side_effect=clock.sleep):
                    with self.assertRaises(type(error)) as caught:
                        review_staged_protocol.wait_for_capture(
                            self.store, {'configuration': {'timeout_seconds': 1}}, reservation, 'wait')
                self.assertIs(caught.exception, error)
                self.assertEqual(clock.intervals, [])


class RemotePollingTests(DevelopmentCase):
    def setUp(self):
        super().setUp()
        self.prepared_study()
        self.sync = self.root / 'transport'
        self.sync.mkdir()
        environment = patch.dict(os.environ, {'EXACTORY_LAB_COLAB_DIR': str(self.sync),
            'EXACTORY_LAB_COLAB_POLL': '0.01', 'EXACTORY_LAB_COLAB_WAIT': '0'})
        environment.start()
        self.addCleanup(environment.stop)

    def runner(self):
        import test_research_remote_execution
        return test_research_remote_execution.RemoteExecutionTests.runner(self)

    def make_remote_claim(self, request_id='busy-remote'):
        admission = admit_lab(self, backend='colab', timeout=5, body='print(\'{"metric": 7}\')\n')
        return execution._claim(self.store, admission['id'], self.store.revision, request_id)['result']

    def test_remote_release_retries_busy_snapshots_before_collecting_the_original_job(self):
        self.check_remote_retry('snapshot')

    def test_remote_release_retries_busy_reauthorization_snapshots(self):
        self.check_remote_retry('reauthorization')

    def test_remote_release_retries_busy_revision_reads(self):
        self.check_remote_retry('revision')

    def test_remote_release_retries_busy_preparation_reads(self):
        self.check_remote_retry('_read_preparation')

    def check_remote_retry(self, read_kind):
        claim = self.make_remote_claim()
        remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        thread, errors = self.runner()
        snapshot = self.store.snapshot
        read_revision = type(self.store).revision.fget
        read_store = (lambda: read_revision(self.store)) if read_kind == 'revision' else getattr(
            self.store, '_read_preparation' if read_kind == '_read_preparation' else 'snapshot')
        reads = []

        def read():
            reads.append(None)
            if len(reads) in ((2, 4) if read_kind == 'reauthorization' else (1, 2)):
                raise ResearchError('store_busy', 'Synthetic concurrent writer')
            return read_store()

        if read_kind == 'revision':
            patched_read = patch.object(type(self.store), 'revision', property(lambda store: read()))
        else:
            attribute = '_read_preparation' if read_kind == '_read_preparation' else 'snapshot'
            patched_read = patch.object(self.store, attribute, side_effect=read)
        with patched_read:
            result = remote_execution.launch_colab(self.store, claim)
        thread.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(result['ok'])
        self.assertEqual(result['metric'], {'metric': 7})
        self.assertEqual(len(list((self.sync / 'jobs').iterdir())), 1)
        self.assertEqual(len(snapshot()['records']['execution']), 1)

    def test_remote_busy_write_refusals_are_not_treated_as_polling_reads(self):
        claim = self.make_remote_claim()
        remote_execution._publish_job(self.store, claim)
        self.assertFalse(remote_execution.serve_scan_once(self.sync))
        error = ResearchError('store_busy', 'Synthetic refused write')
        clock = PollClock()
        with patch.object(self.store, 'mutate', side_effect=error), \
                patch.object(remote_execution, 'time', clock):
            with self.assertRaises(ResearchError) as caught:
                remote_execution.launch_colab(self.store, claim)
        self.assertIs(caught.exception, error)
        self.assertEqual(clock.intervals, [])

    def test_persistent_busy_remote_read_keeps_its_recovery_deadline(self):
        claim = self.make_remote_claim()
        clock = PollClock()
        with patch.object(self.store, 'snapshot', side_effect=ResearchError('store_busy', 'Synthetic writer')), \
                patch.object(remote_execution, 'time', clock):
            with self.assertRaises(ResearchError) as caught:
                remote_execution.launch_colab(self.store, claim)
        self.assertEqual(caught.exception.code, 'execution_recovery_required')
        self.assertIn('job_id', caught.exception.details)
        self.assertLess(abs(clock.elapsed - claim['config']['timeout_seconds']), .01)
        self.assertTrue(all(interval == .01 for interval in clock.intervals))
        self.assertNotIn('execution_outcome', self.store.snapshot()['records'])

    def test_busy_remote_poll_waits_even_when_a_done_marker_is_visible(self):
        claim = self.make_remote_claim()
        root, job = remote_execution._publish_job(self.store, claim)
        result_directory = root / 'results' / job['job_id']
        result_directory.mkdir(parents=True)
        (result_directory / 'DONE').touch()
        clock = PollClock()
        with patch.object(self.store, 'snapshot', side_effect=ResearchError('store_busy', 'Synthetic writer')), \
                patch.object(remote_execution, 'time', clock):
            with self.assertRaises(ResearchError) as caught:
                remote_execution.launch_colab(self.store, claim)
        self.assertEqual(caught.exception.code, 'execution_recovery_required')
        self.assertEqual(caught.exception.details['job_id'], job['job_id'])
        self.assertGreaterEqual(clock.elapsed, claim['config']['timeout_seconds'])

    def test_other_remote_read_errors_surface_without_sleeping(self):
        claim = self.make_remote_claim()
        for error in (ResearchError('stale_revision', 'Synthetic refusal'), ValueError('Synthetic defect')):
            with self.subTest(error=type(error).__name__):
                clock = PollClock()
                with patch.object(self.store, 'snapshot', side_effect=error), \
                        patch.object(remote_execution, 'time', clock):
                    with self.assertRaises(type(error)) as caught:
                        remote_execution.launch_colab(self.store, claim)
                self.assertIs(caught.exception, error)
                self.assertEqual(clock.intervals, [])

    def test_busy_release_read_outside_polling_is_not_retried(self):
        claim = self.make_remote_claim()
        root, job = remote_execution._publish_job(self.store, claim)
        error = ResearchError('store_busy', 'Synthetic concurrent writer')
        with patch.object(self.store, 'snapshot', side_effect=error):
            with self.assertRaises(ResearchError) as caught:
                remote_execution._release(self.store, claim, root, job)
        self.assertIs(caught.exception, error)
