"""Runtime probes retry only pre-generation missing-runtime failures."""

from datetime import datetime, timedelta
import time
from unittest.mock import call, patch

from problem_parts_fixtures import ResultPartsCase
import test_review_staged_frontier as frontier
from research_harness import review_task_execution as execution
from literature_fixtures import LiteratureCase
from research_harness import review_failure_class as failures
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.review_task_execution import identify_call
import test_research_review_protocol as fixtures


class RuntimeRetryTests(LiteratureCase):
    protocol = fixtures.ReviewProtocolTests.protocol
    seed = fixtures.ReviewProtocolTests.seed
    route = fixtures.ReviewProtocolTests.route
    response = fixtures.ReviewProtocolTests.response
    run_probe = fixtures.ReviewProtocolTests.run_probe
    assign = fixtures.ReviewProtocolTests.assign
    records = fixtures.ReviewProtocolTests.records

    def setUp(self):
        super().setUp()
        self.route()
        fingerprint = patch('research_harness.review_state_evaluation._runtime_fingerprint', return_value=None)
        fingerprint.start()
        self.addCleanup(fingerprint.stop)
        self.sleep = patch.object(time, 'sleep')
        self.sleeper = self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def missing(self):
        return ResearchError('review_runtime_missing', 'Synthetic runtime check failure')

    def invoke(self):
        return self.mutate(protocol.invoke_assignment, dict(id='attempt', assignment_id='assignment'),
                           credential='fixture')

    def test_invocation_start_retries_then_calls_once(self):
        self.run_probe()
        self.assign()
        runtime = self.records()['review_route']['route']['runtime']
        with patch.object(transport, 'runtime', side_effect=[self.missing(), runtime, runtime, runtime]), \
                patch.object(transport, 'send_request', return_value=self.response({})) as send:
            self.assertEqual(self.invoke()['result']['status'], 'completed')
        self.assertEqual(self.sleeper.call_args_list, [call(1)])
        send.assert_called_once()

    def test_probe_start_retries_then_success(self):
        runtime = self.records()['review_route']['route']['runtime']
        with patch.object(transport, 'runtime', side_effect=[self.missing(), runtime, runtime, runtime]):
            self.run_probe()
        self.assertEqual(self.sleeper.call_args_list, [call(1)])
        self.assertEqual(self.records()['review_route_probe']['probe']['status'], 'completed')

    def test_predispatch_persistent_failure_creates_no_invocation(self):
        for start in ('probe', 'invoke'):
            with self.subTest(start=start):
                if start == 'invoke':
                    self.run_probe()
                    self.assign()
                self.sleeper.reset_mock()
                with patch.object(transport, 'runtime', side_effect=self.missing()) as runtime:
                    with self.assertRaises(ResearchError) as raised:
                        self.run_probe() if start == 'probe' else self.invoke()
                self.assertEqual(raised.exception.code, 'review_runtime_missing')
                self.assertEqual(runtime.call_count, 6)
                self.assertEqual(self.sleeper.call_args_list, [call(n) for n in (1, 2, 4, 8, 16)])
                self.assertFalse(self.records().get('review_invocation'))

    def test_transport_runtime_failure_finishes_admitted_invocation(self):
        self.run_probe()
        self.assign()
        with patch.object(transport, 'send_request', side_effect=self.missing()) as send:
            attempt = self.invoke()['result']
        self.assertEqual(send.call_count, 6)
        self.assertEqual(attempt['status'], 'failed')
        membership = self.records()['review_pool_membership'][identify_call('review.invoke', 'attempt')]
        self.assertTrue(membership['released'])
        self.assertEqual(self.sleeper.call_args_list, [call(n) for n in (1, 2, 4, 8, 16)])

    def test_observed_runtime_failure_finishes_admitted_invocation(self):
        self.run_probe()
        self.assign()
        runtime = self.records()['review_route']['route']['runtime']
        with patch.object(transport, 'runtime', side_effect=[runtime] + [self.missing()] * 6), \
                patch.object(transport, 'send_request', side_effect=AssertionError('No model call')):
            attempt = self.invoke()['result']
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['error'], dict(kind='ResearchError', code='review_runtime_missing'))
        self.assertIn('observed_at', attempt)
        records = self.records()
        self.assertIn('attempt', records['review_attempt'])
        self.assertIn(identify_call('review.invoke', 'attempt'), records['review_call_observation'])
        self.assertTrue(records['review_pool_membership'][identify_call('review.invoke', 'attempt')]['released'])
        self.assertEqual(self.sleeper.call_args_list, [call(n) for n in (1, 2, 4, 8, 16)])
        classification = failures.classify_attempt(records, self.artifacts, attempt)
        observed_at = datetime.fromisoformat(attempt['observed_at'])
        with patch.object(failures, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = observed_at
            plan = failures.plan_after_failure(records, self.artifacts, 'assignment')
        deadline = (observed_at + timedelta(seconds=60)).isoformat(timespec='seconds')
        self.assertEqual((classification['outcome'], plan['state'], plan['code'], plan['not_before']),
                         ('runtime_unavailable', 'wait', 'review_runtime_missing', deadline))

    def test_observed_stale_runtime_stops_with_stale_code(self):
        self.run_probe()
        self.assign()
        runtime = self.records()['review_route']['route']['runtime']
        changed_runtime = dict(runtime, implementation_sha256='0' * 64)
        with patch.object(transport, 'runtime', side_effect=[runtime, changed_runtime]), \
                patch.object(transport, 'send_request', side_effect=AssertionError('No model call')):
            attempt = self.invoke()['result']
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['error'], dict(kind='ResearchError', code='review_route_runtime_stale'))
        records = self.records()
        self.assertIn('attempt', records['review_attempt'])
        self.assertIn(identify_call('review.invoke', 'attempt'), records['review_call_observation'])
        self.assertTrue(records['review_pool_membership'][identify_call('review.invoke', 'attempt')]['released'])
        self.sleeper.assert_not_called()
        classification = failures.classify_attempt(records, self.artifacts, attempt)
        plan = failures.plan_after_failure(records, self.artifacts, 'assignment')
        self.assertEqual((classification['outcome'], plan['state'], plan['code'], plan['not_before']),
                         ('runtime_stale', 'stop', 'review_route_runtime_stale', None))

    def test_observed_transient_runtime_check_then_success(self):
        route = self.records()['review_route']['route']
        with patch.object(transport, 'runtime', side_effect=[self.missing(), route['runtime'], route['runtime']]), \
                patch.object(transport, 'send_request', return_value=self.response({})) as send:
            self.assertEqual(protocol._observed(self.artifacts, route, {}, 'fixture')['status'], 'completed')
        send.assert_called_once()
        self.assertEqual(self.sleeper.call_args_list, [call(1)])

    def test_runtime_missing_inside_transport_retries_but_other_observations_do_not(self):
        route = self.records()['review_route']['route']
        for persistent in (False, True):
            self.sleeper.reset_mock()
            outcomes = [self.missing()] * 6 if persistent else [self.missing(), self.response({})]
            with patch.object(transport, 'send_request', side_effect=outcomes) as send:
                result = protocol._observed(self.artifacts, route, {}, 'fixture')
            self.assertEqual(result['status'], 'failed' if persistent else 'completed')
            self.assertEqual(send.call_count, 6 if persistent else 2)
            delays = (1, 2, 4, 8, 16) if persistent else (1,)
            self.assertEqual(self.sleeper.call_args_list, [call(n) for n in delays])
        self.sleeper.reset_mock()
        failure = ResearchError('review_input_too_large', 'Size')
        with patch.object(transport, 'send_request', side_effect=failure) as send:
            result = protocol._observed(self.artifacts, route, {}, 'fixture')
        self.assertEqual(result['error']['code'], 'review_input_too_large')
        send.assert_called_once()
        self.sleeper.assert_not_called()

    def test_no_sleep_on_success_and_no_retry_for_changed_runtime(self):
        route = self.records()['review_route']['route']
        with patch.object(transport, 'send_request', return_value=self.response({})):
            protocol._observed(self.artifacts, route, {}, 'fixture')
        self.sleeper.assert_not_called()
        failure = ResearchError('review_route_runtime_stale', 'Changed')
        with patch.object(transport, 'runtime', side_effect=failure) as runtime:
            result = protocol._observed(self.artifacts, route, {}, 'fixture')
        self.assertEqual(result['error']['code'], 'review_route_runtime_stale')
        runtime.assert_called_once()
        self.sleeper.assert_not_called()


class StagedRuntimeRetryTests(ResultPartsCase):
    staged_entry = True
    output_tokens = 4000
    timeout_seconds = 30
    create_controller = frontier.FrontierTests.create_controller
    command = frontier.FrontierTests.command

    def test_staged_start_transient_runtime_then_dispatch(self):
        controller = self.create_controller()
        planned = self.command('review-assignment', controller['next'][0]['payload'])
        route = self.case.read_records()['review_route']['route']
        missing = ResearchError('review_runtime_missing', 'Synthetic runtime failure')
        with patch.object(transport, 'runtime', side_effect=[missing, route['runtime']]), \
                patch.object(execution, 'reserve_task', side_effect=RuntimeError('Reached admission')) as reserve, \
                patch.object(time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'Reached admission'):
                self.command('review-run', planned['next'][0]['payload'])
        reserve.assert_called_once()
        self.assertEqual(sleep.call_args_list, [call(1)])

    def test_staged_start_persistent_runtime_never_dispatches(self):
        controller = self.create_controller()
        planned = self.command('review-assignment', controller['next'][0]['payload'])
        failure = ResearchError('review_runtime_missing', 'Runtime')
        with patch.object(transport, 'runtime', side_effect=failure) as runtime, \
                patch.object(execution, 'reserve_task', side_effect=AssertionError('No dispatch')), \
                patch.object(time, 'sleep') as sleep:
            with self.assertRaises(ResearchError) as raised:
                self.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(raised.exception.code, 'review_runtime_missing')
        self.assertEqual(runtime.call_count, 6)
        self.assertEqual(sleep.call_args_list, [call(n) for n in (1, 2, 4, 8, 16)])
        self.assertFalse(self.case.read_records().get('review_reservation'))

    def test_staged_worker_runtime_failure_is_captured_and_released(self):
        import sys
        from research_harness import review_worker
        controller = self.create_controller()
        planned = self.command('review-assignment', controller['next'][0]['payload'])
        failure_setup = '''
from research_harness.errors import ResearchError
import time
runtime_checks = []
def unavailable(route):
    runtime_checks.append(True)
    raise ResearchError('review_runtime_missing', 'Synthetic missing worker runtime')
review_transport.runtime = unavailable
time.sleep = lambda seconds: None
'''
        script = frontier.WORKER.replace('review_transport.send_request = send', failure_setup)
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', script]):
            attempt = self.command('review-run', planned['next'][0]['payload'])
        records = self.case.read_records()
        captured = records['review_observation'][attempt['reservation_id']]
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['error']['code'], 'review_runtime_missing')
        self.assertEqual(captured['observation']['observed_at'], attempt['observed_at'])
        self.assertTrue(records['review_reservation'][attempt['reservation_id']]['released'])
        self.assertNotIn('calls', [path.name for path in self.case.root.iterdir()])
