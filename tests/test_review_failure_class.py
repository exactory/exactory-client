"""Retained failure shapes and durable retry allowances for a turn position."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from review_parts_fixtures import PartsResultCase
from codex_route_fixtures import build_codex_response
from research_harness import review_parts as parts
from research_harness.artifacts import ArtifactStore
from research_harness import review_failure_class as failures
from research_harness import review_protocol as protocol


class _ObservationClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 4, 8, tzinfo=timezone.utc)


class FailureClassTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(failures, 'datetime', _ObservationClock)
        clock.start()
        self.addCleanup(clock.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.assignment = dict(id='head', role='result', route_id='route', context={},
                               reviewer_id='reviewer', author_id='author', dossier_id=None,
                               packet=self.put({}), recorded_revision=1)
        self.records = dict(review_assignment={'head': self.assignment}, review_attempt={},
                            review_route={'route': dict(id='route', adapter='codex_cli_v1',
                                configuration=dict(max_output_tokens=128000))})

    def put(self, value):
        return self.artifacts.put(json.dumps(value).encode(), 'application/json')

    def attempt(self, answer='', *, code='review_transport_failure', status='incomplete',
                provider_status='incomplete', tokens=0, wall=20, diagnostic=None,
                observed_at='2026-11-04T08:00:00+00:00', assignment='head'):
        response = dict(id='provider', status=provider_status, output=[dict(type='message',
            content=[dict(type='output_text', text=answer)])], context_evidence={}, execution={})
        attempt = dict(id='a' + str(len(self.records['review_attempt'])), assignment_id=assignment,
            origin='instrumented', status=status, request=None, response=self.put(response), output=None,
            usage=dict(output_tokens=tokens, wall_seconds=wall),
            error=None if code is None else dict(kind='ResearchError', code=code),
            recorded_revision=len(self.records['review_attempt']) + 10)
        if observed_at is not None:
            attempt['observed_at'] = observed_at
        if diagnostic is not None:
            attempt['provider_diagnostic'] = dict(source='codex_rollout', thread_id='thread',
                codex_error_info=diagnostic, message='Synthetic provider condition.', rollout_sha256='a' * 64)
        self.records['review_attempt'][attempt['id']] = attempt
        return attempt

    def classify(self, attempt):
        return failures.classify_attempt(self.records, self.artifacts, attempt)

    def plan(self):
        return failures.plan_after_failure(self.records, self.artifacts, 'head')

    def test_measured_failure_shapes(self):
        cases = [
            ('Codex empty failure', '', {}, 'unknown'),
            ('capacity diagnostic', '', dict(diagnostic='server_overloaded'), 'model_at_capacity'),
            ('quota diagnostic', '', dict(diagnostic='usage_limit_exceeded'), 'usage_limit'),
            ('early root close', '{"one":1},"two":2}', dict(provider_status='completed'), 'answer_malformed'),
            ('early duplicate', '{"one":1},"one":2}', dict(provider_status='completed'), 'answer_malformed'),
            ('surplus braces', '{"one":1}}}', dict(provider_status='completed'), 'answer_malformed'),
            ('raw control', '{"one":"a\nb"}', dict(provider_status='completed'), 'answer_malformed'),
            ('missing comma', '{"one":1 "two":2}', dict(provider_status='completed'), 'answer_malformed'),
            ('fence and prose', '{}\n```\nexplanation', dict(provider_status='completed'), 'answer_malformed'),
            ('Claude quota', "You've hit your weekly limit · resets Nov 6 at 9am (America/Phoenix)",
             {}, 'usage_limit'),
            ('Claude reached', 'You’ve reached your usage limit', {}, 'usage_limit'),
            ('API unavailable', "API Error: Can't reach the API server (ENOTFOUND)", {}, 'provider_unavailable'),
            ('safeguards', "API Error: The model's safeguards flagged this message", {}, 'provider_refused'),
            ('cap object', '{}', dict(code='review_incomplete', tokens=131000), 'output_over_cap'),
            ('cap mid sentence', 'continued answer', dict(tokens=218000), 'output_over_cap'),
            ('input limit', '', dict(code='review_input_too_large'), 'input_over_limit'),
            ('runtime check', '', dict(code='review_runtime_missing'), 'runtime_unavailable'),
            ('format', '', dict(code='review_format_unsupported'), 'format_unsupported'),
            ('missing error', '', dict(code=None), 'unknown'),
        ]
        for name, answer, changes, outcome in cases:
            with self.subTest(shape=name):
                result = self.classify(self.attempt(answer, **changes))
                self.assertEqual(result['outcome'], outcome)
                self.assertTrue(result['signal'])

    def test_layer_precedence_and_incomplete_json_are_not_syntax_failures(self):
        attempt = self.attempt('API Error: unavailable', code='review_input_too_large', tokens=200000)
        self.assertEqual(self.classify(attempt)['outcome'], 'input_over_limit')
        attempt = self.attempt("You've hit your limit", tokens=200000)
        self.assertEqual(self.classify(attempt)['outcome'], 'usage_limit')
        for evidence, expected in [({'issues': ['incomplete_turn', 'tool_or_injected_input']}, 'execution_incomplete'),
                                   ({'inspected': True, 'input_verified': False}, 'context_unverified')]:
            provider_status = 'completed' if expected == 'context_unverified' else 'incomplete'
            attempt = self.attempt('{', code='review_incomplete', provider_status=provider_status,
                                   status='completed' if expected == 'context_unverified' else 'failed')
            response = json.loads(self.artifacts.read(attempt['response']))
            response['context_evidence'] = evidence
            if expected == 'context_unverified':
                response['output'][0]['content'][0]['text'] = '{}'
                attempt['output'] = self.put({})
            attempt['response'] = self.put(response)
            self.assertEqual(self.classify(attempt)['outcome'], expected)
        attempt = self.attempt('{', code='review_transport_failure')
        response['execution'] = dict(timeout=True)
        response['status'] = 'incomplete'
        attempt['response'] = self.put(response)
        self.assertEqual(self.classify(attempt)['outcome'], 'timeout')
        attempt = self.attempt('[]', provider_status='completed', code='review_incomplete')
        self.assertEqual(self.classify(attempt)['outcome'], 'answer_malformed')

    def test_reset_times_zones_local_codex_and_year_boundary(self):
        for text, observed, expected in [
            ("You've hit your weekly limit · resets Nov 6 at 9am (America/Phoenix)",
             '2026-11-04T08:00:00+00:00', '2026-11-06T16:00:00+00:00'),
            ('You’ve hit your weekly limit · resets Jan 1 at 12:30am (Asia/Tokyo)',
             '2026-12-31T18:00:00+00:00', '2027-12-31T15:30:00+00:00'),
            ("You've hit your weekly limit · resets Jan 1 at 1am (America/Los_Angeles)",
             '2026-12-31T23:00:00+00:00', '2027-01-01T09:00:00+00:00'),
            ("You've hit your limit · resets someday", '2026-11-04T08:00:00+00:00', None),
            ("You've hit your limit · resets Nov 6 at 9am (Invalid/Zone)", '2026-11-04T08:00:00+00:00', None),
        ]:
            with self.subTest(text=text):
                self.records['review_attempt'].clear()
                attempt = self.attempt(text, observed_at=observed)
                self.assertEqual(self.classify(attempt)['reset_at'], expected)
                self.assertEqual(self.plan()['not_before'], expected or '2026-11-04T08:01:00+00:00')
        with patch.dict(os.environ, {'TZ': 'America/Los_Angeles'}):
            time.tzset()
            try:
                attempt = self.attempt(diagnostic='usage_limit_exceeded', observed_at='2026-12-31T20:00:00+00:00')
                attempt['provider_diagnostic']['message'] = 'Try again at Jan 1st, 2027 1:15 AM.'
                self.assertEqual(self.classify(attempt)['reset_at'], '2027-01-01T09:15:00+00:00')
            finally:
                self.addCleanup(time.tzset)
        time.tzset()

    def test_reset_uses_first_ambiguous_clock_time_after_observation(self):
        attempt = self.attempt(
            "You've hit your limit · resets Nov 1 at 1:30am (America/New_York)",
            observed_at='2026-11-01T05:45:00+00:00')
        self.assertEqual(self.classify(attempt)['reset_at'], '2026-11-01T06:30:00+00:00')

    def test_provider_schedule_every_wait_and_stop(self):
        for changes, answer in [(dict(diagnostic='usage_limit_exceeded'), ''),
                                (dict(diagnostic='server_overloaded'), ''),
                                ({}, 'API Error: service unavailable'), ({}, '')]:
            self.records['review_attempt'].clear()
            for index, minute in enumerate((1, 2, 4, 8, 16, 30)):
                self.attempt(answer, **changes)
                plan = self.plan()
                self.assertEqual(plan['state'], 'wait')
                self.assertEqual(plan['not_before'], '2026-11-04T08:%02d:00+00:00' % minute)
            self.attempt(answer, **changes)
            self.assertEqual(self.plan()['state'], 'stop')
            self.assertEqual(self.plan()['code'], 'review_provider_unavailable')

    def test_usable_completed_output_is_not_a_failure(self):
        self.attempt(diagnostic='server_overloaded')
        attempt = self.attempt('{}', status='completed', provider_status='completed', code=None)
        attempt['output'] = self.put({})
        self.assertIsNone(self.classify(attempt))
        self.assertEqual(self.plan(), dict(state='run', not_before=None, code=None,
                                           details=dict(failed_attempts=0)))

    def test_single_retry_allowances_count_their_own_outcome_across_replacements(self):
        self.records['review_assignment']['old'] = dict(self.assignment, id='old', recorded_revision=0)
        for answer, changes, outcome, code in [
            ('API Error: safeguards flagged this message', {}, 'provider_refused', 'review_provider_refused'),
            ('{}', dict(code='review_incomplete'), 'execution_incomplete', 'review_execution_incomplete'),
            ('{', dict(provider_status='completed'), 'answer_malformed', 'review_answer_malformed'),
            ('{}', dict(status='completed', provider_status='completed', code=None),
             'context_unverified', 'review_context_unverified'),
        ]:
            with self.subTest(outcome=outcome):
                self.records['review_attempt'].clear()
                self.attempt(diagnostic='usage_limit_exceeded', assignment='old')
                if outcome == 'answer_malformed':
                    self.attempt('{}', code='review_incomplete')
                else:
                    self.attempt('{', provider_status='completed')
                for assignment, state in [('head', 'run'), ('old', 'stop')]:
                    attempt = self.attempt(answer, assignment=assignment, **changes)
                    if outcome == 'context_unverified':
                        attempt['output'] = self.put({})
                        response = json.loads(self.artifacts.read(attempt['response']))
                        response['context_evidence'] = dict(inspected=True, input_verified=False)
                        attempt['response'] = self.put(response)
                    plan = self.plan()
                    self.assertEqual((plan['state'], plan['code']), (state, code))
                    self.attempt(diagnostic='server_overloaded')

    def test_provider_schedule_ignores_malformed_answer_count(self):
        self.attempt('{', provider_status='completed')
        self.attempt(diagnostic='server_overloaded', observed_at='2026-11-04T08:01:00+00:00')
        plan = self.plan()
        self.assertEqual((plan['state'], plan['not_before']), ('wait', '2026-11-04T08:02:00+00:00'))
        self.attempt(diagnostic='server_overloaded', observed_at='2026-11-04T08:03:00+00:00')
        plan = self.plan()
        self.assertEqual((plan['state'], plan['not_before']), ('wait', '2026-11-04T08:05:00+00:00'))

    def test_provider_group_counts_mixed_conditions_and_eligible_unknown(self):
        for answer, changes, deadline in [
            ('', {}, '2026-11-04T08:01:00+00:00'),
            ('', dict(diagnostic='usage_limit_exceeded'), '2026-11-04T08:02:00+00:00'),
            ('', dict(diagnostic='server_overloaded'), '2026-11-04T08:04:00+00:00'),
            ('API Error: service unavailable', {}, '2026-11-04T08:08:00+00:00'),
            ('', dict(diagnostic='server_overloaded'), '2026-11-04T08:16:00+00:00'),
            ('', dict(diagnostic='usage_limit_exceeded'), '2026-11-04T08:30:00+00:00'),
            ('API Error: service unavailable', {}, None),
        ]:
            with self.subTest(deadline=deadline):
                self.attempt(answer, **changes)
                plan = self.plan()
                self.assertEqual(plan['state'], 'wait' if deadline else 'stop')
                self.assertEqual(plan['not_before'], deadline)
                expected_code = 'review_failure_unknown' if not changes and not answer else 'review_provider_unavailable'
                self.assertEqual(plan['code'], expected_code)

    def test_ineligible_unknown_does_not_spend_provider_schedule(self):
        self.attempt(wall=121)
        self.assertEqual(self.plan()['state'], 'stop')
        self.attempt(diagnostic='server_overloaded')
        plan = self.plan()
        self.assertEqual((plan['state'], plan['not_before']), ('wait', '2026-11-04T08:01:00+00:00'))

    def test_total_allowance_counts_provider_and_malformed_failures(self):
        for _ in range(6):
            self.attempt(diagnostic='server_overloaded')
        self.attempt('{', provider_status='completed')
        self.assertEqual(self.plan()['state'], 'run')
        self.attempt(diagnostic='server_overloaded')
        plan = self.plan()
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_turn_failures_exhausted'))

    def test_provider_group_seventh_failure_stops_even_with_reset_time(self):
        for _ in range(7):
            self.attempt("You've hit your weekly limit · resets Nov 6 at 9am (America/Phoenix)")
        plan = self.plan()
        self.assertEqual((plan['state'], plan['code'], plan['not_before']),
                         ('stop', 'review_provider_unavailable', None))

    def test_usable_output_restarts_each_outcome_allowance(self):
        self.attempt('{', provider_status='completed')
        complete = self.attempt('{}', status='completed', provider_status='completed', code=None)
        complete['output'] = self.put({})
        self.attempt('{', provider_status='completed')
        self.assertEqual(self.plan()['state'], 'run')
        self.attempt('{', provider_status='completed')
        self.assertEqual((self.plan()['state'], self.plan()['code']), ('stop', 'review_answer_malformed'))

    def test_partial_codex_json_below_cap_is_execution_incomplete(self):
        attempt = self.attempt('{"finding":', code='review_transport_failure', tokens=1)
        attempt['error']['kind'] = 'JSONDecodeError'
        self.assertEqual(self.classify(attempt)['outcome'], 'execution_incomplete')
        self.assertEqual(self.classify(attempt)['signal']['source'], 'response.status')
        self.assertEqual(self.plan()['state'], 'run')

    def test_signal_names_the_retained_staged_refusal_and_timeout_status(self):
        attempt = self.attempt('{}', status='completed', provider_status='completed', code=None)
        attempt.update(output=self.put({}), reservation_id='reservation')
        self.records['review_task_refusal'] = {'reservation': dict(code='invalid_review_staged')}
        self.assertEqual(self.classify(attempt)['signal']['source'], 'review_task_refusal.code')
        attempt = self.attempt(status='timed_out')
        self.assertEqual(self.classify(attempt)['signal']['source'], 'attempt.status')

    def test_first_scientific_refusal_after_provider_failure_can_retry(self):
        self.attempt(diagnostic='server_overloaded')
        attempt = self.attempt('{}', status='completed', provider_status='completed', code=None)
        attempt['output'] = self.put({})
        self.records['review_output_refusal'] = {attempt['id']: dict(code='invalid_value_review')}
        self.assertEqual(self.plan()['state'], 'run')
        second = self.attempt('{}', status='completed', provider_status='completed', code=None)
        second['output'] = self.put({})
        self.records['review_output_refusal'][second['id']] = dict(code='invalid_value_review')
        self.assertEqual(self.plan()['code'], 'review_slot_exhausted')
        self.assertEqual(self.plan()['state'], 'stop')

    def test_single_retry_outcomes_and_scientific_refusal(self):
        for answer, changes, code in [
            ('API Error: safeguards flagged this message', {}, 'review_provider_refused'),
            ('{}', dict(code='review_incomplete'), 'review_execution_incomplete'),
            ('{', dict(provider_status='completed'), 'review_answer_malformed'),
        ]:
            self.records['review_attempt'].clear()
            self.attempt(answer, **changes)
            self.assertEqual(self.plan()['state'], 'run')
            self.attempt(answer, **changes)
            self.assertEqual((self.plan()['state'], self.plan()['code']), ('stop', code))
        self.records['review_attempt'].clear()
        for count in (1, 2):
            attempt = self.attempt('{}', status='completed', provider_status='completed', code=None)
            attempt['output'] = self.put({})
            self.records.setdefault('review_output_refusal', {})[attempt['id']] = dict(
                attempt_id=attempt['id'], assignment_id='head', code='invalid_value_review')
            self.assertEqual(self.classify(attempt)['outcome'], 'scientific_refused')
            self.assertEqual(self.plan()['state'], 'run' if count == 1 else 'stop')

    def test_total_allowance_class_changes_completed_output_and_replacement(self):
        for index in range(8):
            self.attempt(diagnostic='server_overloaded' if index % 2 else 'usage_limit_exceeded')
        self.assertEqual(self.plan()['code'], 'review_turn_failures_exhausted')
        complete = self.attempt('{}', status='completed', provider_status='completed', code=None, tokens=128000)
        complete['output'] = self.put({})
        self.attempt(diagnostic='server_overloaded')
        self.assertEqual(self.plan()['not_before'], '2026-11-04T08:01:00+00:00')
        self.records['review_assignment']['old'] = dict(self.assignment, id='old', recorded_revision=0)
        # Initial replacements repeat the context; unrelated reviewers do not count.
        self.records['review_assignment']['other'] = dict(self.assignment, id='other', reviewer_id='other')
        self.attempt(assignment='other', diagnostic='server_overloaded')
        self.assertEqual(self.plan()['not_before'], '2026-11-04T08:01:00+00:00')
        self.attempt(assignment='old', diagnostic='server_overloaded')
        self.assertEqual(self.plan()['not_before'], '2026-11-04T08:02:00+00:00')
        # Continuation replacements retain the original parent, never the failed sibling.
        self.assignment['context'] = dict(prior_assignment_id='parent')
        self.records['review_assignment']['old']['context'] = dict(prior_assignment_id='parent')
        self.records['review_assignment']['other']['context'] = dict(prior_assignment_id='elsewhere')
        self.assertEqual(self.plan()['not_before'], '2026-11-04T08:02:00+00:00')

    def test_continuation_replacement_on_visual_route_keeps_failure_count(self):
        self.assignment['context'] = dict(prior_assignment_id='parent')
        self.records['review_assignment']['old'] = dict(self.assignment, id='old', recorded_revision=0)
        self.attempt(assignment='old', diagnostic='server_overloaded')
        self.attempt(assignment='old', diagnostic='server_overloaded')
        self.records['review_route']['visual'] = dict(self.records['review_route']['route'],
            id='visual', adapter='codex_cli_visual_v1')
        self.assignment['route_id'] = 'visual'
        self.attempt(diagnostic='server_overloaded')
        self.assertEqual(self.plan()['details']['failed_attempts'], 3)
        self.assertEqual(self.plan()['not_before'], '2026-11-04T08:04:00+00:00')

    def test_elapsed_deadline_allows_run_without_restarting_wait(self):
        self.attempt(diagnostic='server_overloaded', observed_at='2026-11-04T07:58:00+00:00')
        self.assertEqual(self.plan()['state'], 'run')
        self.assertEqual(self.plan()['not_before'], '2026-11-04T07:59:00+00:00')

    def test_missing_timestamps_and_expensive_unknown_stop(self):
        self.attempt(observed_at=None)
        self.assertEqual((self.plan()['state'], self.plan()['not_before']), ('run', None))
        self.attempt(wall=121)
        self.assertEqual(self.plan()['state'], 'stop')
        self.attempt(wall=1)
        self.assertEqual(self.plan()['state'], 'stop')
        self.records['review_attempt'].clear()
        self.attempt(wall=None)
        self.assertEqual(self.plan()['state'], 'stop')
        self.records['review_attempt'].clear()
        self.records['review_route']['route']['adapter'] = 'claude_cli_v1'
        self.attempt()
        self.assertEqual(self.plan()['state'], 'stop')

    def test_dispatch_and_unhandled_output_caps_stop_with_measurement(self):
        for code in ('review_runtime_missing', 'review_format_unsupported'):
            self.records['review_attempt'].clear()
            self.attempt(code=code)
            self.assertEqual(self.plan()['state'], 'stop')
        self.records['review_attempt'].clear()
        self.attempt(tokens=131000)
        self.assertEqual(self.plan()['code'], 'review_output_over_cap')
        self.assertEqual(self.plan()['details']['output_tokens'], 131000)
        self.assertEqual(self.plan()['state'], 'stop')

    def test_initial_input_size_replacement_uses_existing_rule(self):
        self.attempt(code='review_input_too_large', status='failed')
        # The measured no-call shape has neither response nor output.
        next(iter(self.records['review_attempt'].values()))['response'] = None
        self.assertTrue(protocol._initial_size_failure(self.records, self.artifacts, self.assignment, self.assignment))
        self.assertEqual(self.plan()['state'], 'replace')
        self.assignment['context'] = dict(prior_assignment_id='parent')
        self.assertFalse(protocol._initial_size_failure(
            self.records, self.artifacts, self.assignment, self.assignment))
        self.assertEqual(self.plan()['state'], 'stop')

    def test_context_unverified_retry(self):
        for index in range(2):
            attempt = self.attempt('{}', status='completed', provider_status='completed', code=None)
            attempt['output'] = self.put({})
            response = json.loads(self.artifacts.read(attempt['response']))
            response['context_evidence'] = dict(inspected=True, input_verified=False)
            attempt['response'] = self.put(response)
            self.assertEqual(self.plan()['state'], 'run' if index == 0 else 'stop')
        self.assertEqual(self.plan()['code'], 'review_context_unverified')

    def test_measured_output_refusals_retry_once_without_calling_provider(self):
        for code in ('invalid_reading_response', 'invalid_value_review', 'review_reassessment_mismatch',
                     'research_objection_identity_conflict', 'invalid_native_review',
                     'review_evidence_undelivered', 'invalid_strategy'):
            with self.subTest(code=code):
                self.records['review_attempt'].clear()
                self.records['review_output_refusal'] = {}
                with patch.object(protocol.transport, 'observe', side_effect=AssertionError('Unexpected call')), \
                     patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Unexpected call')):
                    for state in ('run', 'stop'):
                        attempt = self.attempt('{}', status='completed', provider_status='completed', code=None)
                        attempt['output'] = self.put({})
                        self.records['review_output_refusal'][attempt['id']] = dict(
                            attempt_id=attempt['id'], assignment_id='head', code=code)
                        self.assertEqual(self.classify(attempt)['outcome'], 'scientific_refused')
                        plan = self.plan()
                        self.assertEqual((plan['state'], plan['code']), (state, 'review_slot_exhausted'))

    def test_unknown_answer_and_timeout_stop_and_reset_does_not_evade_total(self):
        self.attempt('Some unclassified failure.', provider_status='failed')
        self.assertEqual(self.plan()['state'], 'stop')
        self.records['review_attempt'].clear()
        for index in range(8):
            self.attempt("You've hit your limit · resets Nov 6 at 9am (America/Phoenix)")
        self.assertEqual(self.plan()['code'], 'review_turn_failures_exhausted')


class OutputReplacementTests(PartsResultCase):
    def test_real_read_handler_halves_units_and_does_not_write(self):
        self.oversized(count=12, characters=10000, room=80000)
        assignment = self.assign()
        def incomplete(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            # The handler also recognizes review_incomplete below the token cap.
            response['usage']['output_tokens'] = 1
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=incomplete):
            result = protocol.invoke_assignment(self.case.store, dict(id='overflow', assignment_id=assignment['id']),
                credential='fixture', expected_revision=self.case.store.revision, request_id='overflow')['result']
        records = self.case.read_records()
        before = sorted(str(path) for path in self.case.root.rglob('*'))
        classified = failures.classify_attempt(records, self.case.artifacts, result)
        self.assertEqual(classified['outcome'], 'output_over_cap')
        plan = failures.plan_after_failure(records, self.case.artifacts, assignment['id'])
        self.assertEqual(plan['state'], 'replace', plan)
        self.assertLessEqual(plan['details']['unit_characters'],
                             self.packet(assignment)[parts.KEY]['unit_characters'] // 2)
        self.assertEqual(before, sorted(str(path) for path in self.case.root.rglob('*')))

    def test_problem_repair_replacement_preserves_subject(self):
        import test_review_parts_repair_journey as journeys
        self.oversized(count=12, characters=10000, room=80000)
        first = self.assign()
        self.invoke(first)
        binding = journeys.ResultRepairJourneyTests.contaminate(self, first)
        assignment = journeys.ResultRepairJourneyTests.repair_assignment(self, first, binding)
        self.assertEqual(self.packet(assignment)[parts.KEY]['adapter'], 'context_repair')
        def incomplete(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = route['configuration']['max_output_tokens']
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=incomplete):
            protocol.invoke_assignment(self.case.store, dict(id='overflow', assignment_id=assignment['id']),
                credential='fixture', expected_revision=self.case.store.revision, request_id='overflow')
        plan = failures.plan_after_failure(self.case.read_records(), self.case.artifacts, assignment['id'])
        self.assertEqual(plan['state'], 'replace', plan)

    def test_read_handler_that_cannot_reduce_stops(self):
        self.oversized(count=12, characters=10000, room=80000)
        assignment = self.assign()
        records = self.case.read_records()
        attempt = dict(id='overflow', assignment_id=assignment['id'], status='failed', output=None, response=None,
                       error=dict(code='review_incomplete'), usage=dict(output_tokens=128000), recorded_revision=999)
        records['review_attempt']['overflow'] = attempt
        with patch.object(parts, 'prepare_packet', return_value=self.packet(assignment)):
            plan = failures.plan_after_failure(records, self.case.artifacts, assignment['id'])
        self.assertEqual(plan['state'], 'stop')
        self.assertEqual(plan['code'], 'review_output_over_cap')


class RepairReplacementTests(unittest.TestCase):
    def prepare_overflow(self, characters=0):
        import test_review_parts_repair_journey as journeys
        from strategy_fixtures import StrategyCase
        fixture = journeys.BarRepairJourneyTests()
        self.addCleanup(fixture.doCleanups)
        original = StrategyCase.review_response
        def response(case, *args, **kwargs):
            result = original(case, *args, **kwargs)
            if characters:
                result['value']['reason'] = 'A' * characters
            return result
        with patch.object(StrategyCase, 'review_response', response):
            fixture.setUp()
        assignment = fixture.assign_repair()
        self.assertNotIn(parts.KEY, fixture.packet(assignment))
        def incomplete(route, request, credential):
            response = build_codex_response(route, request, {})
            response['status'] = 'incomplete'
            response['usage']['output_tokens'] = 1
            return response
        with patch.object(protocol.transport, 'send_request', side_effect=incomplete):
            attempt = protocol.invoke_assignment(fixture.store, dict(id='overflow', assignment_id=assignment['id']),
                credential='fixture', expected_revision=fixture.store.revision, request_id='overflow')['result']
        records = fixture.store.snapshot()['records']
        self.assertEqual(failures.classify_attempt(records, fixture.artifacts, attempt)['outcome'], 'output_over_cap')
        return fixture, assignment, records

    def test_ordinary_repair_overflow_enters_smaller_parts_request(self):
        fixture, assignment, records = self.prepare_overflow(120000)
        with patch.object(parts, 'READ_CHARACTERS', 20000):
            plan = failures.plan_after_failure(records, fixture.artifacts, assignment['id'])
        self.assertEqual(plan['state'], 'replace', plan)
        self.assertEqual(plan['details']['handler'], 'context_repair')

    def test_repair_that_produces_a_larger_request_stops(self):
        fixture, assignment, records = self.prepare_overflow()
        plan = failures.plan_after_failure(records, fixture.artifacts, assignment['id'])
        self.assertEqual(plan['state'], 'stop', plan)
        self.assertEqual(plan['code'], 'review_output_over_cap')
