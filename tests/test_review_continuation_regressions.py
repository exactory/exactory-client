"""Lifecycle regressions found by the independent continuation review."""

import threading
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from research_harness import cli, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.review_continuation import list_chain_heads, plan_next
from research_harness.review_series import run_series
import test_review_continuation as fixtures


class ContinuationRegressionTests(unittest.TestCase):
    def fixture(self, kind=fixtures.ContinuationTests):
        case = kind()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_live_inline_owner_is_waited_for_without_another_call(self):
        fixture = self.fixture()
        entered, release = threading.Event(), threading.Event()
        failures, steps = [], []

        def provider(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=30):
                raise RuntimeError('fixture provider timed out')
            return fixture.case.build_provider_response(fixture.case.build_review_output('bar'))

        def invoke():
            try:
                protocol.invoke_assignment(fixture.case.store,
                    dict(id='owned', assignment_id='bar-first'),
                    expected_revision=fixture.case.store.revision,
                    request_id='owned-request', credential='fixture')
            except BaseException as error:
                failures.append(error)

        with patch('research_harness.review_transport.send_request', side_effect=provider) as send:
            owner = threading.Thread(target=invoke)
            owner.start()
            self.addCleanup(release.set)
            self.assertTrue(entered.wait(timeout=30))
            sleeps = []

            def sleep(delay):
                sleeps.append(delay)
                # Exercise two live reconciliation refusals, then let the
                # original owner finish. Neither refusal spends a reviewer call.
                if len(sleeps) == 3:
                    release.set()
                    owner.join(timeout=30)

            with patch('research_harness.review_series.time.sleep', side_effect=sleep):
                code = run_series(fixture.case.store, 'bar-first', emit=steps.append)
            release.set()
            owner.join(timeout=30)
        self.assertFalse(owner.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(code, 0, steps)
        self.assertGreaterEqual(len(sleeps), 3)
        self.assertEqual(send.call_count, 1)

    def test_failed_source_head_has_an_executable_status_obligation(self):
        fixture = self.fixture()
        output = fixture.case.build_review_output('bar')
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        fixture.invoke(output)
        fixture.execute(fixture.plan())
        with patch('research_harness.review_transport.send_request', side_effect=OSError('failure')):
            fixture.execute(fixture.plan(), request='failed-source')
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='run', code='fixture', details={})):
            report = cli.status_report(fixture.case.store)
            plan = fixture.plan()
        rows = [row for row in report['obligations'] if row.get('assignment_id') == 'bar-first-t2']
        self.assertTrue(rows)
        self.assertEqual(rows[0]['next'][0]['command'], plan['command'])
        self.assertEqual(rows[0]['next'][0]['payload'], plan['payload'])

    def test_unverified_context_head_exposes_the_policy_action_in_status(self):
        fixture = self.fixture()
        response = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
        response['context_evidence'] = dict(inspected=True, input_verified=False)
        with patch('research_harness.review_transport.send_request', return_value=response):
            fixture.execute(fixture.plan(), request='unverified-context')
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='wait', code='review_context_unverified', details={},
                                     not_before='2030-01-01T00:00:00+00:00')):
            plan = fixture.plan()
            report = cli.status_report(fixture.case.store)
        rows = [row for row in report['obligations'] if row.get('assignment_id') == 'bar-first']
        self.assertTrue(rows)
        self.assertEqual(rows[0]['next'][0]['state'], 'wait')
        self.assertEqual(rows[0]['next'][0]['command'], plan['command'])
        self.assertEqual(rows[0]['next'][0]['payload'], plan['payload'])

    def test_live_captured_owner_is_polled_without_another_reviewer_call(self):
        from research_harness import review_call_capture
        fixture = self.fixture()
        captured, release = threading.Event(), threading.Event()
        original_capture = review_call_capture.capture_call
        failures, steps = [], []

        def capture(*args, **kwargs):
            result = original_capture(*args, **kwargs)
            captured.set()
            if not release.wait(timeout=120):
                raise RuntimeError('fixture captured owner timed out')
            return result

        def invoke():
            try:
                protocol.invoke_assignment(fixture.case.store,
                    dict(id='live-captured', assignment_id='bar-first'),
                    expected_revision=fixture.case.store.revision,
                    request_id='live-captured-request', credential='fixture')
            except BaseException as error:
                failures.append(error)

        def sleep(delay):
            release.set()
            owner.join(timeout=120)

        response = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
        with patch('research_harness.review_transport.send_request', return_value=response) as send:
            with patch.object(review_call_capture, 'capture_call', side_effect=capture):
                owner = threading.Thread(target=invoke)
                owner.start()
                self.addCleanup(release.set)
                self.assertTrue(captured.wait(timeout=120))
                try:
                    with patch('research_harness.review_series.time.sleep', side_effect=sleep) as slept:
                        code = run_series(fixture.case.store, 'bar-first', emit=steps.append)
                finally:
                    release.set()
                    owner.join(timeout=120)
        self.assertFalse(owner.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(code, 0, steps)
        self.assertEqual(slept.call_count, 1)
        self.assertEqual(send.call_count, 1)
        refused = next(row for row in steps if row.get('error'))
        self.assertEqual(refused['request_id'], 'live-captured-request')
        self.assertEqual(refused['error']['code'], 'review_worker_reconciliation_required')

    def test_failed_direction_head_has_an_executable_status_obligation(self):
        from research_harness.principles import initialize_research
        from test_research_repair_slot_replacement import OrdinarySlotReplacementTests
        case = self.fixture(OrdinarySlotReplacementTests)
        case.mutate(initialize_research, dict(profile='research', target=None))
        case.assign('failed-a', 'reviewer-a')
        with patch('research_harness.review_transport.send_request', side_effect=OSError('failure')):
            protocol.invoke_assignment(case.store, dict(id='failure', assignment_id='failed-a'),
                expected_revision=case.store.revision, request_id='failure-call', credential='fixture')
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='run', code='fixture', details={})):
            report = cli.status_report(case.store)
        row = next(row for row in report['obligations'] if row.get('assignment_id') == 'failed-a')
        self.assertEqual(row['next'][0]['payload'], dict(id='failed-a-run-2', assignment_id='failed-a'))

    def test_a_recorded_problem_post_finishes_every_chain(self):
        fixture = self.fixture()
        records = fixture.case.read_records()
        records['problem_post'] = dict(post=dict(id='post', gate='bypassed'))
        plan = fixture.plan(records)
        self.assertEqual(plan['state'], 'done')
        self.assertNotIn('command', plan)
        self.assertNotIn('payload', plan)

    def test_failed_initial_bar_replacement_needs_the_new_reviewer_identity(self):
        fixture = self.fixture()
        with patch('research_harness.review_transport.send_request', side_effect=OSError('failure')):
            fixture.execute(fixture.plan(), request='initial-failure')
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='replace', code='review_context_lost', details={})):
            plan = fixture.plan()
        self.assertEqual(plan['state'], 'handoff')
        self.assertEqual(plan['command'], 'review-assignment')
        self.assertNotIn('payload', plan)

    def test_provider_failure_after_output_refusal_uses_the_failure_policy(self):
        fixture = self.fixture()
        output = fixture.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        fixture.invoke(output)
        with self.assertRaises(ResearchError):
            fixture.execute(fixture.plan(), request='refused-output')
        with patch('research_harness.review_transport.send_request', side_effect=OSError('provider failure')):
            fixture.execute(fixture.plan(), request='provider-failure')
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='wait', code='review_provider_unavailable', details={},
                                     not_before='2030-01-01T00:00:00+00:00')) as policy:
            plan = fixture.plan()
        self.assertEqual(plan['state'], 'wait')
        self.assertEqual(plan['payload'], dict(id='bar-first-run-3', assignment_id='bar-first'))
        policy.assert_called_once()

    def test_unverified_context_after_refusal_uses_policy_instead_of_recording(self):
        fixture = self.fixture()
        output = fixture.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        fixture.invoke(output)
        with self.assertRaises(ResearchError):
            fixture.execute(fixture.plan(), request='refused-output')
        response = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
        response['context_evidence'] = dict(inspected=True, input_verified=False)
        with patch('research_harness.review_transport.send_request', return_value=response):
            fixture.execute(fixture.plan(), request='unverified-context')
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='run', code='review_context_unverified', details={})) as policy:
            plan = fixture.plan()
        self.assertEqual(plan['state'], 'run')
        self.assertEqual(plan['payload'], dict(id='bar-first-run-3', assignment_id='bar-first'))
        policy.assert_called_once()

    def test_refusal_after_unverified_context_keeps_the_existing_refusal_retry(self):
        fixture = self.fixture()
        response = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
        response['context_evidence'] = dict(inspected=True, input_verified=False)
        with patch('research_harness.review_transport.send_request', return_value=response):
            fixture.execute(fixture.plan(), request='unverified-context')
        output = fixture.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='run', code='review_context_unverified', details={})), patch(
                'research_harness.review_transport.send_request',
                return_value=fixture.case.build_provider_response(output)):
            fixture.execute(fixture.plan(), request='refused-retry')
        recording = fixture.plan()
        self.assertEqual(recording['state'], 'record')
        with self.assertRaises(ResearchError):
            fixture.execute(recording, request='refuse-observed-retry')
        plan = fixture.plan()
        self.assertEqual(plan['state'], 'run')
        self.assertEqual(plan['payload'], dict(id='bar-first-run-3', assignment_id='bar-first'))
        with patch('research_harness.review_transport.send_request', return_value=
                   fixture.case.build_provider_response(fixture.case.build_review_output('bar'))) as send:
            fixture.execute(plan, request='observed-retry')
            fixture.execute(fixture.plan(), request='record-observed')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(fixture.plan()['state'], 'done')

    def test_initial_slot_successor_can_change_reviewer(self):
        from test_research_repair_slot_replacement import OrdinarySlotReplacementTests
        case = self.fixture(OrdinarySlotReplacementTests)
        case.assign('failed-a', 'reviewer-a')
        with patch('research_harness.review_transport.send_request', side_effect=OSError('failure')):
            protocol.invoke_assignment(case.store, dict(id='failure', assignment_id='failed-a'),
                expected_revision=case.store.revision, request_id='failure-call', credential='fixture')
        case.assign('replacement-c', 'reviewer-c')
        records = case.store.snapshot()['records']
        self.assertEqual(list_chain_heads(records, case.artifacts), ['replacement-c'])
        plan = plan_next(records, case.artifacts, 'failed-a')
        self.assertEqual(plan['head_id'], 'replacement-c')
        self.assertEqual(plan['payload'], dict(id='replacement-c-run-1', assignment_id='replacement-c'))

    def test_busy_read_after_durable_refusal_replans(self):
        fixture = self.fixture()
        original = cli.OPERATIONS['value-review']
        snapshot = fixture.case.store.snapshot
        busy = []

        def read():
            if busy:
                busy.pop()
                raise ResearchError('store_busy', 'fixture post-refusal contention')
            return snapshot()

        def record(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except ResearchError:
                busy.append(True)
                raise

        output = fixture.case.build_review_output('bar')
        output['value']['status'] = 'invalid'
        steps = []
        with patch.object(fixture.case.store, 'snapshot', side_effect=read), patch.dict(
                cli.OPERATIONS, {'value-review': record}), patch(
                'research_harness.review_transport.send_request',
                return_value=fixture.case.build_provider_response(output)) as send:
            code = run_series(fixture.case.store, 'bar-first', emit=steps.append)
        self.assertEqual(code, 3)
        self.assertEqual(steps[-1]['code'], 'review_slot_exhausted')
        self.assertEqual(send.call_count, 2)

    def test_unrun_initial_size_failure_plans_accepted_replacement(self):
        from test_problem_initial_slot_replacement import ProblemInitialSlotReplacementTests
        from codex_route_fixtures import limit_codex_input
        fixture = self.fixture(ProblemInitialSlotReplacementTests)
        fixture.oversized(limit=10 ** 6)
        original = fixture.assign('refused-initial')
        limit_codex_input(fixture, 110000)
        records = fixture.case.read_records()
        plan = plan_next(records, fixture.case.artifacts, original['id'])
        self.assertEqual(plan['state'], 'replace')
        self.assertEqual(plan['payload'], dict(id='refused-initial-t2', role='result',
            route_id=original['route_id'], dossier_id=None, reviewer_id=original['reviewer_id'],
            author_id=original['author_id'], context={}))
        result = cli.OPERATIONS[plan['command']](fixture.case.store, plan['payload'],
            expected_revision=fixture.case.store.revision, request_id='replacement')
        self.assertEqual(result['result']['slot_id'], original['slot_id'])

    def test_staged_reconciling_action_waits_with_a_fresh_poll_identity(self):
        from research_harness.review_continuation import _plan_staged_action
        action = dict(command='review-run', payload=dict(assignment_id='controller',
            epoch_id='epoch', task_id='task', action='reconcile'))
        task = dict(task_id='task', state='reconciling', code='review_worker_reconciliation_required')
        plan = _plan_staged_action({}, {}, task, action)
        self.assertEqual(plan['state'], 'wait')
        self.assertEqual(plan['payload'], action['payload'])
        self.assertNotIn('request_id', plan)

    def test_successful_live_reconciliation_polls_do_not_spend_progress(self):
        fixture = self.fixture()
        real_plan = plan_next
        polls, requests, steps = [], [], []

        def plan(records, artifacts, head):
            current = real_plan(records, artifacts, head)
            if len(polls) < 2:
                return dict(current, state='wait', command='review-run',
                    payload=dict(action='reconcile', operation='review.invoke', invocation_id='other'),
                    code='review_capacity_wait', not_before=(
                        datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
            return current

        original = cli.OPERATIONS['review-run']

        def execute(store, payload, **identity):
            if payload.get('action') == 'reconcile':
                polls.append(payload)
                requests.append(identity['request_id'])
                return store.mutate('fixture.poll', {}, lambda tx: None, **identity)
            return original(store, payload, **identity)

        with patch('research_harness.review_series.plan_next', side_effect=plan), patch.dict(
                cli.OPERATIONS, {'review-run': execute}), patch(
                'research_harness.review_transport.send_request', return_value=
                fixture.case.build_provider_response(fixture.case.build_review_output('bar'))) as send:
            code = run_series(fixture.case.store, 'bar-first', emit=steps.append)
        self.assertEqual(code, 0, steps)
        self.assertEqual(len(set(requests)), 2)
        self.assertEqual(send.call_count, 1)

    def test_wait_refreshes_a_chain_finished_by_another_driver(self):
        fixture = self.fixture()
        output = fixture.case.build_review_output('bar')
        policy = dict(state='wait', code='review_provider_unavailable', details={},
            not_before=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())

        def finish(delay):
            cli.OPERATIONS['review-run'](fixture.case.store,
                dict(id='other-driver-run', assignment_id='bar-first'),
                expected_revision=fixture.case.store.revision, request_id='other-driver-call')
            fixture.execute(fixture.plan(), request='other-driver-record')

        steps = []
        with patch('research_harness.review_transport.send_request', side_effect=[OSError('provider failure'),
                fixture.case.build_provider_response(output)]) as send, patch(
                'research_harness.review_failure_class.plan_after_failure', return_value=policy), patch(
                'research_harness.review_series.time.sleep', side_effect=finish) as sleep:
            code = run_series(fixture.case.store, 'bar-first', emit=steps.append)
        self.assertEqual(code, 0, steps)
        self.assertEqual(steps[-1]['state'], 'done')
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once()
        self.assertIn('other-driver-run', fixture.case.read_records()['review_invocation'])


if __name__ == '__main__':
    unittest.main()
