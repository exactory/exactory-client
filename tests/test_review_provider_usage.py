"""Retained Claude usage events inform status and planned reading-chain calls."""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.evidence import digest
from research_harness import cli, review_series
from test_research_claude_transport import clean_stream, module, request_for
import test_research_claude_transport as claude_fixtures


def make_rate_event(utilization, window='seven_day', reset=1795053600):
    return {'type': 'rate_limit_event', 'rate_limit_info': {
        'status': 'allowed_warning', 'rateLimitType': window,
        'utilization': utilization, 'resetsAt': reset}, 'uuid': 'rate-event'}


class _UsageClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 13, tzinfo=timezone.utc)


class UsageRetentionTests(unittest.TestCase):
    invoke = claude_fixtures.ClaudeInvocationTests.invoke

    def test_new_calls_retain_each_rate_event_and_still_verify_the_stream(self):
        request = request_for()
        events = clean_stream(request)
        events[3:4] = [make_rate_event(.7), make_rate_event(.8, 'five_hour')]
        response, _ = self.invoke(events, request=request)
        self.assertEqual(response['execution']['rate_limit_events'], [
            {'event_index': index, 'event': events[index]} for index in (3, 4)])
        self.assertTrue(module().verify_context(self.route, request, response))
        without_events, _ = self.invoke([event for event in clean_stream(request)
            if event.get('type') != 'rate_limit_event'], request=request)
        self.assertEqual(without_events['execution']['rate_limit_events'], [])
        before = copy.deepcopy(response)
        from research_harness.review_provider_usage import describe_usage
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            reference = artifacts.put(json.dumps(response).encode(), 'application/json')
            describe_usage({'review_attempt': {'a': dict(id='a', response=reference,
                recorded_revision=1, observed_at='2026-11-13T00:00:00Z')}}, artifacts)
            self.assertEqual(json.loads(artifacts.read(reference)), before)


class ProviderUsageTests(unittest.TestCase):
    def setUp(self):
        clock = patch('research_harness.review_provider_usage.datetime', _UsageClock)
        clock.start()
        self.addCleanup(clock.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.records = {'review_attempt': {}, 'review_assignment': {}}

    def add_call(self, utilization=None, *, window='seven_day', reset=1795053600, events=None):
        index = len(self.records['review_attempt']) + 1
        response = {'execution': {}}
        if events is not None or utilization is not None:
            response['execution']['rate_limit_events'] = [dict(event_index=i, event=event)
                for i, event in enumerate(events if events is not None else [make_rate_event(utilization, window, reset)])]
        reference = self.artifacts.put(json.dumps(response).encode(), 'application/json')
        attempt = dict(id='call-' + str(index), status='failed', response=reference, recorded_revision=index,
                       observed_at='2026-11-13T00:%02d:00Z' % index)
        self.records['review_attempt'][attempt['id']] = attempt
        return reference

    def describe(self, **kwargs):
        from research_harness.review_provider_usage import describe_usage
        return describe_usage(self.records, self.artifacts, **kwargs)

    def plan(self, characters, *, state='run'):
        packet = {'reading_turn': {'reading_plan': {
            'delivered_now': [{'characters': characters[0]}],
            'remaining': [{'characters': size} for size in characters[1:]]}}}
        self.records['review_assignment']['head'] = dict(id='head', packet=self.artifacts.put(
            json.dumps(packet).encode(), 'application/json'))
        return dict(head_id='head', state=state)

    def test_latest_window_uses_the_last_event_of_each_call_and_reports_its_ground(self):
        self.add_call(.7)
        self.add_call(events=[make_rate_event(.72), make_rate_event(.75)])
        self.add_call(.8, window='five_hour')
        usage = self.describe()
        windows = {row['window']: row for row in usage['windows']}
        weekly = windows['seven_day']
        self.assertEqual(weekly['utilization'], .75)
        self.assertEqual(weekly['reset_at'], datetime.fromtimestamp(1795053600, timezone.utc).isoformat())
        self.assertEqual(weekly['attempt_id'], 'call-2')
        self.assertEqual(weekly['observed_at'], '2026-11-13T00:02:00Z')
        self.assertAlmostEqual(weekly['utilization_per_call'], .05)
        self.assertEqual(weekly['sample_calls'], 2)
        self.assertEqual(weekly['sample_intervals'], 1)
        self.assertEqual(windows['five_hour']['utilization_per_call'], None)

    def test_remaining_chain_warning_counts_greedy_packing_and_the_final_turn(self):
        self.add_call(.75)
        self.add_call(.85)
        plan = self.plan([70000, 60000, 10000])
        usage = self.describe(plan=plan)
        self.assertEqual(usage['planned_remaining_turns'], 3)
        warning = usage['warnings'][0]
        self.assertEqual(warning['code'], 'review_usage_window_insufficient')
        details = warning['details']
        self.assertEqual(details['planned_remaining_turns'], 3)
        self.assertAlmostEqual(details['estimated_utilization'], .3)
        self.assertAlmostEqual(details['remaining_utilization'], .15)
        self.assertAlmostEqual(details['utilization_per_call'], .1)
        self.assertEqual(details['sample_calls'], 2)
        self.assertIn('retained', details['ground'])
        self.assertEqual(self.describe(plan=self.plan([70000, 60000, 10000], state='continue'))[
            'planned_remaining_turns'], 2)

    def test_insufficient_flat_reset_and_decreasing_samples_do_not_invent_a_forecast(self):
        for values in ([.7], [.7, .7], [.8, .7]):
            with self.subTest(values=values):
                self.records['review_attempt'].clear()
                for value in values:
                    self.add_call(value)
                usage = self.describe(plan=self.plan([1000]))
                self.assertIsNone(usage['windows'][0]['utilization_per_call'])
                self.assertEqual(usage['warnings'], [])
        self.records['review_attempt'].clear()
        self.add_call(.7, reset=1795053600)
        self.add_call(.8, reset=1795658400)
        self.assertIsNone(self.describe()['windows'][0]['utilization_per_call'])

    def test_missing_events_break_per_call_samples_and_legacy_artifacts_stay_unchanged(self):
        references = [self.add_call(.7), self.add_call(), self.add_call(.9)]
        before = [self.artifacts.read(ref) for ref in references]
        usage = self.describe(plan=self.plan([1000]))
        self.assertIsNone(usage['windows'][0]['utilization_per_call'])
        self.assertEqual([self.artifacts.read(ref) for ref in references], before)
        self.assertEqual(self.describe()['warnings'], [])
        self.records['review_attempt'].clear()
        self.add_call()
        self.assertEqual(self.describe()['windows'], [])

    def test_status_and_every_series_step_show_latest_retained_usage(self):
        self.add_call(.7)
        self.add_call(.8)
        status = cli._review_status(self.records, self.artifacts)
        self.assertEqual(status['provider_usage']['windows'][0]['utilization'], .8)
        snapshot = dict(records=self.records, revision=2)
        store = type('UsageStore', (), dict(root=self.artifacts.root, snapshot=lambda unused: snapshot))()
        with patch.object(review_series, 'plan_next', return_value=dict(state='done', head_id='head')):
            rows = []
            self.assertEqual(review_series.run_series(store, 'head', emit=rows.append), 0)
        self.assertEqual(rows[0]['provider_usage']['windows'][0]['utilization'], .8)

    def test_terminal_usage_does_not_change_the_recorded_stop_plan(self):
        self.add_call(.8)
        snapshot = dict(records=self.records, revision=1)
        store = type('UsageStore', (), dict(root=self.artifacts.root, snapshot=lambda unused: snapshot))()
        plan = dict(state='stop', code='review_provider_refused', head_id='head', progress={}, details={})
        original = copy.deepcopy(plan)
        with patch.object(review_series, 'plan_next', return_value=plan), patch(
                'research_harness.automatic_overrides.record_series_stop',
                return_value=dict(id='stop', recorded_revision=2)) as record:
            rows = []
            self.assertEqual(review_series.run_series(store, 'head', emit=rows.append), 3)
        self.assertEqual(record.call_args.args[1], original)
        self.assertEqual(plan, original)
        self.assertEqual(rows[0]['provider_usage']['windows'][0]['utilization'], .8)
        self.assertEqual(rows[0]['stop_id'], 'stop')

    def test_call_observations_are_deduplicated_and_unaccepted_calls_are_visible(self):
        ref = self.add_call(.7)
        self.records['review_call_observation'] = {
            'accepted': dict(id='accepted', operation='review.invoke', invocation_id='call-1', recorded_revision=2, observation=dict(
                response=ref, observed_at='2026-11-13T00:01:00Z'))}
        ref2 = self.add_call(.8)
        attempt = self.records['review_attempt'].pop('call-2')
        self.records['review_call_observation']['unaccepted'] = dict(id='unaccepted', operation='review.invoke', invocation_id='call-2', recorded_revision=3,
            observation=dict(response=ref2, observed_at=attempt['observed_at']))
        usage = self.describe()['windows'][0]
        self.assertEqual(usage['attempt_id'], 'unaccepted')
        self.assertEqual(usage['sample_calls'], 2)
        self.assertAlmostEqual(usage['utilization_per_call'], .1)

    def test_run_step_immediately_shows_the_usage_event_of_the_call_it_just_executed(self):
        self.add_call(.7)
        snapshot = dict(records=self.records, revision=1)
        store = type('UsageStore', (), dict(root=self.artifacts.root, snapshot=lambda unused: snapshot))()
        running = dict(state='run', head_id='head', command='review-run', progress={}, payload={'id': 'call-new'})
        done = dict(state='done', head_id='head', progress={})
        def invoke(*args, **kwargs):
            self.add_call(.8)
            snapshot['revision'] = 2
        with patch.object(review_series, 'plan_next', side_effect=[running, done]), \
                patch.dict(cli.OPERATIONS, {'review-run': invoke}):
            rows = []
            self.assertEqual(review_series.run_series(store, 'head', emit=rows.append), 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['provider_usage']['windows'][0]['utilization'], .8)
        self.assertEqual(rows[1]['provider_usage']['windows'][0]['utilization'], .8)

    def test_recording_needs_no_call_and_dynamic_staged_plans_remain_unknown(self):
        plan = self.plan([70000, 60000])
        self.assertEqual(self.describe(plan=dict(plan, state='record'))['planned_remaining_turns'], 0)
        self.records['review_assignment']['head']['staged_review'] = dict(epoch_id='epoch')
        self.assertIsNone(self.describe(plan=plan)['planned_remaining_turns'])

    def test_parts_phase_estimates_do_not_claim_a_complete_chain_size(self):
        from research_harness import review_parts
        packet = {review_parts.KEY: {'plan': {'remaining_turns': {
            'read': {'estimate': 3}, 'combine': {'estimate': 2}, 'final': {'estimate': 1}}}}}
        self.records['review_assignment']['head'] = dict(id='head', packet=self.artifacts.put(
            json.dumps(packet).encode(), 'application/json'))
        self.assertIsNone(self.describe(plan=dict(head_id='head', state='run'))['planned_remaining_turns'])
        self.assertIsNone(self.describe(plan=dict(head_id='head', state='continue'))['planned_remaining_turns'])

    def test_same_response_and_timestamp_still_count_distinct_invocations(self):
        for value in (.7, .8, .8, .9):
            self.add_call(value)
        self.records['review_attempt']['call-3']['observed_at'] = self.records['review_attempt']['call-2']['observed_at']
        window = self.describe()['windows'][0]
        self.assertEqual(window['sample_calls'], 4)
        self.assertAlmostEqual(window['utilization_per_call'], .2 / 3)

    def test_native_capture_order_survives_delayed_acceptance(self):
        first, second = self.add_call(.7), self.add_call(.8)
        self.records['review_call_observation'] = {
            'capture-1': dict(id='capture-1', operation='review.invoke', invocation_id='call-1', recorded_revision=1,
                observation=dict(response=first, observed_at='2026-11-13T00:01:00Z')),
            'capture-2': dict(id='capture-2', operation='review.invoke', invocation_id='call-2', recorded_revision=2,
                observation=dict(response=second, observed_at='2026-11-13T00:02:00Z'))}
        self.records['review_attempt']['call-1']['recorded_revision'] = 3
        window = self.describe()['windows'][0]
        self.assertEqual(window['utilization'], .8)
        self.assertEqual(window['attempt_id'], 'call-2')
        self.assertAlmostEqual(window['utilization_per_call'], .1)

    def test_staged_captures_and_probes_contribute_usage_without_an_ordinary_attempt(self):
        first, second = self.add_call(.7), self.add_call(.8)
        self.records['review_attempt'].clear()
        self.records['review_route_probe'] = {'probe': dict(id='probe', response=first, recorded_revision=1)}
        self.records['review_observation'] = {'reservation': dict(id='reservation', reservation_id='reservation',
            recorded_revision=2, observation=dict(response=second))}
        window = self.describe()['windows'][0]
        self.assertEqual(window['utilization'], .8)
        self.assertEqual(window['sample_calls'], 2)
        self.assertAlmostEqual(window['utilization_per_call'], .1)

    def test_unbounded_provider_numbers_do_not_stop_status(self):
        self.add_call(events=[make_rate_event(10 ** 400)])
        self.assertEqual(self.describe()['windows'], [])
        self.records['review_attempt'].clear()
        self.add_call(events=[make_rate_event(.7, reset=10 ** 400)])
        self.assertIsNone(self.describe()['windows'][0]['reset_at'])

    def test_released_0853_response_verifies_and_keeps_its_bytes_without_usage_events(self):
        from claude_review_fixtures import build_recorded_response, build_recorded_runtime
        implementation = 'ee183e027eaad81de13cfab1149000c027b4504842070537a1068c93dfb1a7bf'
        request = request_for()
        route = dict(claude_fixtures.ROUTE, runtime=build_recorded_runtime(implementation))
        context = ('released-0.85.3', implementation, True, [], [])
        response = build_recorded_response(route, request, {'ok': True}, context)
        reference = self.artifacts.put(json.dumps(response).encode(), 'application/json')
        before = self.artifacts.read(reference)
        self.records['review_attempt']['old-call'] = dict(id='old-call', response=reference, recorded_revision=1)
        self.assertTrue(module().verify_context(route, request, response))
        self.assertEqual(self.describe()['windows'], [])
        self.assertEqual(self.artifacts.read(reference), before)
        self.assertEqual(digest(json.loads(before)), digest(response))

    def test_estimate_equal_to_remaining_allowance_does_not_warn_on_float_roundoff(self):
        self.add_call(.7)
        self.add_call(.8)
        usage = self.describe(plan=self.plan([1000]))
        self.assertEqual(usage['planned_remaining_turns'], 2)
        self.assertEqual(usage['warnings'], [])

    def test_a_codex_chain_does_not_spend_the_retained_claude_allowance(self):
        self.add_call(.75)
        self.add_call(.85)
        plan = self.plan([70000, 60000, 10000])
        self.records['review_assignment']['head']['route_id'] = 'codex'
        self.records['review_route'] = {'codex': dict(adapter='codex_cli_v1')}
        usage = self.describe(plan=plan)
        self.assertEqual(usage['windows'][0]['utilization'], .85)
        self.assertEqual(usage['warnings'], [])

    def test_an_expired_window_does_not_forecast_the_new_allowance(self):
        self.add_call(.7, reset=1794448800)
        self.add_call(.8, reset=1794448800)
        usage = self.describe(plan=self.plan([70000, 60000, 10000]))
        self.assertEqual(usage['windows'][0]['utilization'], .8)
        self.assertEqual(usage['warnings'], [])

    def test_an_error_step_shows_usage_captured_before_acceptance_became_pending(self):
        from research_harness.errors import ResearchError
        self.add_call(.7)
        snapshot = dict(records=self.records, revision=1)
        store = type('UsageStore', (), dict(root=self.artifacts.root, snapshot=lambda unused: snapshot))()
        running = dict(state='run', head_id='head', command='review-run', progress={}, payload={'id': 'call-new'})
        done = dict(state='done', head_id='head', progress={})
        def invoke(*args, **kwargs):
            self.add_call(.8)
            snapshot['revision'] = 2
            attempt = self.records['review_attempt'].pop('call-2')
            self.records['review_call_observation'] = {'capture-2': dict(id='capture-2',
                operation='review.invoke', invocation_id='call-2', recorded_revision=2,
                observation=dict(response=attempt['response'], observed_at=attempt['observed_at']))}
            raise ResearchError('review_invocation_pending', 'The captured call awaits acceptance.')
        with patch.object(review_series, 'plan_next', side_effect=[running, done]), \
                patch.dict(cli.OPERATIONS, {'review-run': invoke}):
            rows = []
            self.assertEqual(review_series.run_series(store, 'head', emit=rows.append), 0)
        self.assertEqual(rows[0]['error']['code'], 'review_invocation_pending')
        self.assertEqual(rows[0]['provider_usage']['windows'][0]['utilization'], .8)

    def test_a_replan_error_step_shows_the_latest_capture(self):
        from research_harness.errors import ResearchError
        self.add_call(.7)
        snapshot = dict(records=self.records, revision=1)
        store = type('UsageStore', (), dict(root=self.artifacts.root, snapshot=lambda unused: snapshot))()
        running = dict(state='run', head_id='head', command='review-run', progress={}, payload={'id': 'call-new'})
        done = dict(state='done', head_id='head', progress={})
        def invoke(*args, **kwargs):
            self.add_call(.8)
            snapshot['revision'] = 2
            raise ResearchError('review_task_dependency_changed', 'The captured task dependency changed.')
        with patch.object(review_series, 'plan_next', side_effect=[running, done]), \
                patch.dict(cli.OPERATIONS, {'review-run': invoke}):
            rows = []
            self.assertEqual(review_series.run_series(store, 'head', emit=rows.append), 0)
        self.assertEqual(rows[0]['provider_usage']['windows'][0]['utilization'], .8)
