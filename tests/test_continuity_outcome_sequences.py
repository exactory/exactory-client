"""G1: each non-size outcome enters v7 reading/final rows from the policy table."""

import os
import unittest
from unittest.mock import patch

import continuity_stage3_fixtures as stage3
import continuity_guard_fixtures as matrix
from research_harness import review_failure_class as failures, review_protocol as protocol
from research_harness import stop_classes
from research_harness.review_continuation import plan_next
from test_series_code_reach import record_reachable_errors

# These rows have their own multi-step command journeys in the Stage 3 matrix.
SPECIAL_OUTCOMES = frozenset(('provider_refused', 'scientific_refused', 'output_over_cap',
    'input_over_limit', 'response_over_limit', 'timeout', 'stopped'))


class OutcomeSequenceTests(unittest.TestCase):
    def test_g1_v7_reading_runs_every_other_outcome_through_its_last_sequence(self):
        self.execute_rows('reading')

    def test_g1_v7_final_runs_every_other_outcome_through_its_last_sequence(self):
        self.execute_rows('final')

    def execute_rows(self, stage):
        for outcome in failures.OUTCOME_ROWS:
            if outcome in SPECIAL_OUTCOMES:
                continue
            for scheduled in ((True, False) if outcome == 'unknown' else (True,)):
                with self.subTest(stage=stage, outcome=outcome, scheduled=scheduled):
                    try:
                        with record_reachable_errors('plan_next'):
                            self.execute_row(stage, outcome, scheduled)
                    finally:
                        self.doCleanups()

    def execute_row(self, stage, outcome, scheduled):
        case, native, head = stage3.prepare_turn(self, stage)
        counts = {}
        limit = failures.OUTCOME_ROWS['provider_unavailable' if outcome == 'unknown' and scheduled else outcome]['attempt_limit']
        for number in range(failures.TURN_FAILURE_LIMIT * failures.SEQUENCE_LIMIT + 12):
            action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
            if action['state'] == 'wait':
                self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                clock = failures.datetime
                deadline = clock.fromisoformat(action['not_before'])
                class DueClock(clock):
                    @classmethod
                    def now(cls, tz=None):
                        return deadline
                with patch.object(failures, 'datetime', DueClock):
                    action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
            if action['state'] == 'stop':
                self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                self.assertEqual(action['details']['outcome'], outcome)
                if failures.OUTCOME_ROWS[outcome]['exhausted'] == 'sequence':
                    self.assertEqual(action['details']['sequence'], failures.SEQUENCE_LIMIT)
                expected = limit
                self.assertEqual(counts, {sequence: expected for sequence in range(1, failures.SEQUENCE_LIMIT + 1)}
                    if failures.OUTCOME_ROWS[outcome]['exhausted'] == 'sequence' else {1: expected})
                stage3.execute_stop_answers(self, case, head, action)
                return
            if action['state'] == 'handoff':
                self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                self.assertIn(action['command'], ('review-route', 'review-probe'))
                self.assertEqual(counts, {1: limit})
                step = action['details']['step']
                self.assertEqual(step['command'], action['command'])
                route_id, commands = stage3.execute_route_step(self, case, step, 'recovery-route')
                # The role's new export/assignment completes the route handoff.
                identifier = 'recovery'
                reference = case.export(name='recovery-export', assignment_id=identifier)
                assigned = matrix.execute(case, 'review-assignment', dict(id=identifier,
                    route_id=route_id, dossier_id=None,
                    role='native_math', reviewer_id='openness-reviewer', author_id='problem-author',
                    context=dict(native_packet=reference)))
                self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, assigned['id'])['state'], 'run')
                case.finish_from(reference, assigned['id'])
                return
            self.assertEqual(action['state'], 'run', action)
            with patch.dict(os.environ, {'CODEX_HOME': str(case.root / 'synthetic-codex')}), patch.object(
                    protocol.transport, 'send_request', side_effect=stage3.fail_outcome_request(case, outcome, scheduled=scheduled)), patch.object(protocol.time, 'sleep'):
                attempt = matrix.execute(case, action['command'], action['payload'])
            classified = failures.classify_attempt(case.store.snapshot()['records'], case.artifacts,
                case.store.snapshot()['records']['review_attempt'][attempt['id']])
            self.assertEqual(classified['outcome'], outcome)
            position = failures.count_position(case.store.snapshot()['records'], case.artifacts,
                case.store.snapshot()['records']['review_assignment'][head])
            sequence = position['sequence']
            counts[sequence] = counts.get(sequence, 0) + 1
            self.assertEqual(position['failures_by_outcome'][outcome], counts[sequence])
            self.assertLessEqual(counts[sequence], limit)
        self.fail('Every outcome row must reach its declared sequence end.')

    def test_g1_runtime_handoff_executes_declared_step_and_nested_commands(self):
        for stage in ('reading', 'final'):
            for outcome in ('runtime_unavailable', 'runtime_stale'):
                with self.subTest(stage=stage, outcome=outcome):
                    try:
                        with record_reachable_errors('plan_next'):
                            self.execute_row(stage, outcome, True)
                    finally:
                        self.doCleanups()

    @record_reachable_errors('plan_next')
    def test_g1_v7_stopped_reading_and_final_release_reaches_recorded_finding(self):
        for stage in ('reading', 'final'):
            with self.subTest(stage=stage):
                case, native, head = stage3.prepare_turn(self, stage)
                matrix.execute(case, 'review-stop', dict(id='stop', action='stop', assignment_id=head,
                    mode='now', reason='The owner stops this reading position.'))
                action = plan_next(case.store.snapshot()['records'], case.artifacts, head)
                self.assertEqual((action['state'], action['command']), ('handoff', 'review-stop'))
                self.assertIn(action['code'], stop_classes.STOP_REGISTRY)
                step = action['details']['step']
                matrix.execute(case, step['command'], dict(step['payload'], id='release',
                    reason='The owner resumes this reading position.'))
                self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, head)['state'], 'run')
                case.finish_from(native, head)
                case.doCleanups()
