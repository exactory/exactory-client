"""D2 continuity regressions use recorded calls and public command admission."""

import copy
import json
import random
import unittest
from unittest.mock import patch

import openness_reading_fixtures as reading_fixtures
import test_review_continuation as continuation_fixtures
import test_review_failure_class as failure_fixtures
import test_review_output_correction as correction_fixtures
import test_review_parts_adjudication_journey as focused_fixtures
from research_harness import cli, review_protocol as protocol, review_failure_class as failures
from research_harness import research_decisions as decisions, stop_classes
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series
from research_harness.review_task_execution import reconcile_call


class OutcomeTests(unittest.TestCase):
    def prepare(self, cls):
        fixture = cls()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_three_reconciled_interruptions_continue_the_reading_chain(self):
        case = self.prepare(reading_fixtures.OpennessReadingCase)
        case.limit_input(24000)
        reference = case.export(assignment_id='reading')
        case.assign('reading', reference)
        for number in range(3):
            plan = plan_next(case.store.snapshot()['records'], case.artifacts, 'reading')
            with patch.object(protocol, '_observed', side_effect=RuntimeError('process ended')):
                with self.assertRaises(RuntimeError):
                    case.mutate(protocol.invoke_assignment, plan['payload'])
            reconcile_call(case.store, dict(operation='review.invoke', invocation_id=plan['payload']['id']),
                           request_id='reconcile-' + str(number))
        before = copy.deepcopy(case.store.snapshot()['records'])
        plan = plan_next(before, case.artifacts, 'reading')
        self.assertEqual(plan['state'], 'run')
        self.assertEqual(plan['details']['outcome'], 'interrupted')
        self.assertEqual(plan['details']['failed_attempts'], 3)
        self.assertEqual(digest(before), digest(case.store.snapshot()['records']))
        def provider(route, request, credential):
            from codex_route_fixtures import build_codex_response
            from research_harness.review_excerpts import read_sent_packet
            packet = read_sent_packet(request)
            output = (case.build_reading_response(packet) if 'reading_turn' in packet
                      else case.build_native_result(packet))
            return build_codex_response(route, request, output)
        with patch.object(protocol.transport, 'send_request', side_effect=provider):
            self.assertEqual(run_series(case.store, 'reading', emit=lambda row: None), 0)
        self.assertEqual(case.store.snapshot()['records']['review_assignment']['reading'], before['review_assignment']['reading'])
        for key, attempt in before['review_attempt'].items():
            self.assertEqual(case.store.snapshot()['records']['review_attempt'][key], attempt)

    def test_current_validator_revives_the_head_without_changing_stored_bytes(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        fixture.invoke(fixture.case.build_review_output('bar'))
        records = fixture.case.store.snapshot()['records']
        records['review_output_refusal'] = {'observed': dict(code='old_contract_refusal')}
        before = copy.deepcopy(records)
        self.assertIsNone(failures.classify_attempt(records, fixture.case.artifacts,
                                                    records['review_attempt']['observed']))
        self.assertEqual(fixture.plan(records)['state'], 'record')
        self.assertEqual(records, before)

    def test_over_cap_without_smaller_task_has_one_identical_retry(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        output = fixture.case.build_review_output('bar')
        output.update(source_requests=[dict(id='inventory', kind='inventory_page', page=0)])
        output['value']['status'] = 'unresolved'
        fixture.invoke(output)
        turn = fixture.execute(fixture.plan())['result']
        # Source: 20261112043216-bar-turn-over-output-cap-at-effort-max.
        for index, expected in enumerate(('run', 'stop')):
            response = fixture.case.build_provider_response(fixture.case.build_review_output('bar'))
            response['status'] = 'incomplete'
            response['usage'] = dict(input_tokens=459276, output_tokens=(138371, 179808)[index])
            with patch.object(protocol.transport, 'send_request', return_value=response):
                fixture.case.mutate(protocol.invoke_assignment,
                    dict(id='over-' + str(index), assignment_id=turn['id']), credential='fixture')
            # A measured c of seven never permits seven identical calls.
            with patch.object(failures, 'calculate_over_cap_allowance', return_value=7):
                plan = fixture.plan()
            self.assertEqual((plan['state'], plan['code']), (expected, 'review_output_over_cap'))
        self.assertTrue(plan['details']['decision']['then'])
        self.assertNotIn('effort', plan.get('payload', {}))
        self.assertEqual(failures.calculate_over_cap_allowance(0.138, 66), 5)
        self.assertEqual(failures.calculate_over_cap_allowance(0.375, 66), 7)
        self.assertEqual(failures.calculate_over_cap_allowance(), 2)

    def test_sequences_reset_outcome_counts_and_owner_grants_one_more(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        for sequence in range(1, 4):
            for number in range(3):
                case.attempt('API Error: safeguards flagged this message')
                plan = case.plan()
                self.assertEqual(plan['details']['sequence'], sequence)
                self.assertEqual(plan['state'], 'run' if number < 2 else 'wait' if sequence < 3 else 'stop')
            if sequence < 3:
                self.assertEqual(plan['not_before'], '2026-11-04T08:30:00+00:00')
        self.assertEqual(plan['code'], 'review_turn_failures_exhausted')
        case.records['review_context_event'] = dict(grant=dict(id='grant', assignment_id='head',
            kind='observation', source='owner', recorded_revision=100))
        self.assertEqual(case.plan()['state'], 'wait')
        case.attempt('API Error: safeguards flagged this message')['recorded_revision'] = 101
        self.assertEqual(case.plan()['state'], 'run')
        self.assertEqual(case.plan()['details']['sequence'], 4)
        case.attempt('API Error: safeguards flagged this message')['recorded_revision'] = 102
        case.attempt('API Error: safeguards flagged this message')['recorded_revision'] = 103
        self.assertEqual(case.plan()['code'], 'review_turn_failures_exhausted')

    def test_generated_mixed_sequences_never_exceed_eight_or_renew_a_terminal_outcome(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        rng = random.Random(19)
        for _ in range(30):
            case.records['review_attempt'].clear()
            classifications = {}
            for number in range(40):
                attempt = case.attempt(code='review_invocation_interrupted')
                outcome = rng.choice(('interrupted', 'execution_incomplete', 'answer_malformed',
                                      'provider_refused', 'context_unverified', 'usage_limit', 'stopped'))
                classifications[attempt['id']] = dict(outcome=outcome, signal={},
                    reset_at='2026-11-06T16:00:00+00:00' if rng.randrange(4) == 0 else None)
                with patch.object(failures, 'classify_attempt',
                                  side_effect=lambda records, artifacts, row: classifications[row['id']]):
                    position = failures.count_position(case.records, case.artifacts, case.assignment)
                    plan = case.plan()
                self.assertLessEqual(position['failed_attempts'], 8)
                if position['sequence'] > 3:
                    self.assertEqual(plan['state'], 'stop')
                    break
        self.assertEqual(set(failures.OUTCOME_ROWS), set(failures.OUTCOME_CODES))

    def test_later_provider_reset_controls_sequence_wait(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        for _ in range(7):
            case.attempt("You've hit your weekly limit · resets Nov 6 at 9am (America/Phoenix)")
        self.assertEqual(case.plan()['state'], 'wait')
        self.assertEqual(case.plan()['not_before'], '2026-11-06T16:00:00+00:00')

    def test_registry_preserves_legacy_classes_and_requires_defect_proof(self):
        waiting = ('rate_limited', 'review_capacity_wait', 'review_invocation_pending', 'provider_state_capacity',
                   'execution_live', 'remote_operation_owned', 'verification_ingest_pending',
                   'store_busy', 'lock_timeout', 'review_provider_unavailable')
        for code in waiting:
            self.assertEqual(stop_classes.classify_stop(code), 'waiting')
        for code in ('review_output_over_cap', 'review_slot_exhausted', 'unrecognized'):
            self.assertEqual(stop_classes.classify_stop(code), 'decision')
        self.assertEqual(stop_classes.classify_stop('review_chain_stopped'), 'requested')
        row = copy.deepcopy(stop_classes.STOP_REGISTRY['review_series_no_progress'])
        row.pop('proof')
        with self.assertRaises(ValueError):
            stop_classes.validate_registry({row['code']: row})
        self.assertEqual(stop_classes.describe_terminal('review_source_no_progress')['command'], 'fulltext')
        self.assertEqual(stop_classes.describe_terminal('problem_already_posted')['state'], 'done')

    def test_capacity_refusal_from_executed_action_is_a_wait_without_stop_evidence(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        steps = []
        with patch.dict(cli.OPERATIONS, {'review-run': lambda *args, **kwargs: self.raise_capacity()}):
            self.assertEqual(run_series(fixture.case.store, 'bar-first', emit=steps.append), 4)
        self.assertEqual(steps[-1]['state'], 'wait')
        self.assertEqual(steps[-1]['code'], 'review_capacity_wait')
        self.assertIn('not_before', steps[-1])
        self.assertNotIn('review_series_stop', fixture.case.store.snapshot()['records'])

    def raise_capacity(self):
        raise ResearchError('review_capacity_wait', 'A different call owns this capacity.')

    def test_exhausted_bar_continuation_accepts_a_new_blind_reviewer(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        fixture.case.assign('bar', 'second')
        output = fixture.case.build_review_output('bar')
        output['source_requests'] = [dict(id='inventory', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        fixture.invoke(output)
        turn = fixture.execute(fixture.plan())['result']
        invalid = fixture.case.build_review_output('bar')
        invalid['value']['status'] = 'invented'
        for number in range(2):
            with patch.object(protocol.transport, 'send_request',
                              return_value=fixture.case.build_provider_response(invalid)):
                fixture.execute(fixture.plan(), request='refused-' + str(number))
            with self.assertRaises(ResearchError):
                fixture.execute(fixture.plan(), request='audit-' + str(number))
        steps = []
        self.assertEqual(run_series(fixture.case.store, 'bar-first', emit=steps.append), 3)
        step = steps[-1]['details']['step']
        self.assertEqual(step['id'], 3)
        self.assertEqual(step['excluded_reviewer_ids'], ['first', 'second'])
        before = copy.deepcopy(fixture.case.store.snapshot()['records'])
        for reviewer in ('first', 'second'):
            with self.assertRaises(ResearchError) as caught:
                fixture.case.assign('bar', reviewer, identifier='repeat-' + reviewer)
            self.assertEqual(caught.exception.code, 'review_resampling_forbidden')
        payload = dict(step['payload'], id='new-bar', route_id='route', reviewer_id='third')
        saved = fixture.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(saved['slot_id'], fixture.assignment['id'])
        packet = protocol._read_json(fixture.case.artifacts, saved['packet'])
        self.assertNotIn('context_repair', packet)
        self.assertEqual(protocol.read_history(fixture.case.artifacts, saved), [])
        self.assertNotIn('prior_assignment_id', saved['context'])
        self.assertEqual(fixture.plan()['head_id'], 'new-bar')
        with patch('research_harness.review_refusal.classify_observed_output', return_value=None):
            self.assertEqual(fixture.plan()['head_id'], 'new-bar')
            with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Superseded call')):
                with self.assertRaises(ResearchError) as caught:
                    fixture.case.mutate(protocol.invoke_assignment, dict(id='late-old', assignment_id=turn['id']),
                                        credential='fixture')
            self.assertEqual(caught.exception.code, 'review_resampling_forbidden')
        for kind in ('review_attempt', 'review_output_refusal'):
            self.assertEqual(fixture.case.store.snapshot()['records'][kind], before[kind])
        self.assertEqual(before['review_assignment'][turn['id']], fixture.case.store.snapshot()['records']['review_assignment'][turn['id']])

    def test_focused_exhaustion_accepts_another_reviewer_and_refuses_resampling(self):
        fixture = self.prepare(focused_fixtures.AdjudicationPartsCase)
        assignment = fixture.assign()
        invalid = fixture.final_output()
        invalid['disposition'] = 'invented'
        from codex_route_fixtures import invoke_codex
        for number in range(2):
            invoke_codex(fixture.case, assignment['id'], invalid, attempt_id='refused-' + str(number))
            with self.assertRaises(ResearchError):
                fixture.case.mutate(protocol.record_adjudication,
                    dict(invalid, id='invalid-' + str(number), assignment_id=assignment['id']))
        plan = plan_next(fixture.case.store.snapshot()['records'], fixture.case.artifacts, assignment['id'])
        self.assertEqual(plan['details']['step']['id'], 3)
        with self.assertRaises(ResearchError) as caught:
            fixture.assign(identifier='repeat', reviewer='third')
        self.assertEqual(caught.exception.code, 'review_resampling_forbidden')
        replacement = fixture.assign(identifier='fresh', reviewer='fourth')
        self.assertEqual(replacement['slot_id'], assignment.get('slot_id', assignment['id']))
        self.assertEqual(protocol.read_history(fixture.case.artifacts, replacement), [])

    def test_declined_correction_is_not_offered_twice(self):
        fixture = self.prepare(correction_fixtures.CorrectionTests)
        fixture.prepare_refused()
        assignment = fixture.case.assignment
        records = fixture.fixture.read_records()
        first = plan_next(records, fixture.fixture.artifacts, assignment['id'])
        self.assertEqual(first['command'], 'review-output-correction')
        records['review_output_correction_invocation'] = dict(declined=dict(id='declined', assignment_id=assignment['id']))
        second = plan_next(records, fixture.fixture.artifacts, assignment['id'])
        self.assertEqual(second['state'], 'stop')
        self.assertEqual(second['code'], 'review_slot_exhausted')
        self.assertEqual(second['details']['step']['id'], 8)
        self.assertTrue(second['details']['decision']['then'])


    def test_contaminated_openness_hands_off_to_an_accepted_new_export(self):
        case = self.prepare(reading_fixtures.OpennessReadingCase)
        reference = case.export(assignment_id='old')
        case.assign('old', reference)
        evidence = case.artifacts.put(b'Observed context exposure.', 'text/plain')
        case.mutate(protocol.record_context_event, dict(id='exposure', assignment_id='old',
            kind='contamination', source='author_history', reason='An observed exposure.', evidence=[evidence]))
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, 'old')
        self.assertEqual((plan['state'], plan['command'], plan['details']['step']['id']), ('handoff', 'export', 4))
        new_reference = case.export(name='new-packet', assignment_id='new')
        accepted = case.assign('new', new_reference)
        self.assertEqual(accepted['context']['native_packet'], new_reference)
        self.assertNotIn('context_repair', accepted['context'])

    def test_retired_verification_route_uses_an_accepted_first_turn_context(self):
        import test_review_parts_study as study_fixtures
        fixture = self.prepare(study_fixtures.VerificationPartsTests)
        assignment = fixture.assign()
        evidence = fixture.case.artifacts.put(b'An owner route instruction.', 'text/plain')
        fixture.case.mutate(protocol.record_context_event, dict(id='retire', assignment_id=assignment['id'],
            kind='route_retired', source='owner', reason='Retire this route.', evidence=[evidence]))
        plan = plan_next(fixture.records(), fixture.case.artifacts, assignment['id'])
        self.assertEqual((plan['state'], plan['command'], plan['details']['step']['id']),
                         ('handoff', 'review-assignment', 6))
        from codex_route_fixtures import record_codex_route
        record_codex_route(fixture.case, identifier='fresh-route', output_tokens=128000)
        payload = dict(plan['details']['step']['payload'], id='new-verification',
                       route_id='fresh-route', reviewer_id='new-verifier')
        accepted = fixture.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(accepted['context'], assignment['context'])
        self.assertNotIn('context_repair', accepted['context'])

    def test_event_repair_step_contains_the_complete_ordered_binding(self):
        import test_research_context_repair_integration as repair_fixtures
        fixture = self.prepare(repair_fixtures.ContextRepairIntegrationTests)
        fixture.expose()
        records = fixture.store.snapshot()['records']
        plan = plan_next(records, fixture.artifacts, 'assignment-bar-a')
        step = plan['details']['step']
        self.assertEqual(step['id'], 1)
        self.assertEqual(step['excluded_reviewer_ids'], ['reviewer-b'])
        fixture.probe_repair()
        payload = dict(step['payload'], id='planned-repair', route_id='route', reviewer_id='repair-reviewer')
        binding = payload['context']['context_repair']
        binding.update(probe_id='repair-probe', reason='A new verified context after the cause event.')
        accepted = fixture.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(accepted['context']['context_repair']['event_ids'], ['exposure'])
        # Exhausting this repaired chain still frees a blind bar slot.
        invalid = fixture.final_response()
        invalid['value']['status'] = 'invented'
        for index in range(2):
            with patch.object(protocol.transport, 'send_request', return_value=fixture.provider_response(invalid)):
                fixture.mutate(protocol.invoke_assignment, dict(id='bad-' + str(index),
                    assignment_id=accepted['id']), credential='fixture')
            with self.assertRaises(ResearchError):
                fixture.mutate(decisions.record_value_review, dict(invalid, id='bad-review-' + str(index),
                    dossier_id='dossier-1', assignment_id=accepted['id']))
        next_plan = plan_next(fixture.store.snapshot()['records'], fixture.artifacts, accepted['id'])
        self.assertEqual(next_plan['details']['step']['id'], 3)
        first = dict(next_plan['details']['step']['payload'], id='blind-bar',
                     route_id='route', reviewer_id='new-blind-reviewer')
        fresh = fixture.mutate(protocol.record_assignment, first)['result']
        self.assertEqual(fresh['context'], {})
        self.assertEqual(protocol.read_history(fixture.artifacts, fresh), [])

    def test_slate_and_result_exhaustion_keep_their_reviewer_slots(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        for role in ('slate', 'result'):
            records = fixture.case.read_records()
            assignment = records['review_assignment']['bar-first']
            assignment['role'] = role
            from research_harness.review_plan_steps import describe_role_handoff
            plan = describe_role_handoff(records, fixture.case.artifacts, assignment, 'review_slot_exhausted')
            self.assertEqual(plan['state'], 'stop')
            self.assertEqual([step['id'] for step in plan['details']['steps']], [8, 7, 1])
            self.assertEqual(plan['details']['steps'][-1]['payload']['role'], role)
            self.assertIn('context_repair', plan['details']['steps'][-1]['payload']['context'])
            peer = dict(assignment, id='peer', reviewer_id='second', slot_id='peer', recorded_revision=100)
            records['review_assignment']['peer'] = peer
            records['review_attempt'] = {str(index): dict(id=str(index), assignment_id=identifier,
                status='completed', recorded_revision=200 + index)
                for index, identifier in enumerate(('bar-first', 'bar-first', 'peer', 'peer'))}
            value = dict(assignment, id='third', reviewer_id='third', context={})
            with self.assertRaises(ResearchError) as caught:
                protocol._initial_slot(records, fixture.case.artifacts, value)
            self.assertEqual(caught.exception.code, 'review_slots_fixed')


    def test_stopped_calls_spend_no_allowance_and_captured_completion_keeps_its_meaning(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        attempts = [case.attempt(code='review_invocation_interrupted') for _ in range(10)]
        case.records['review_stop'] = dict(stop=dict(id='stop', mode='now', release=None,
            calls=[dict(operation='review.invoke', invocation_id=row['id'], assignment_id='head')
                   for row in attempts[:8]]))
        before = copy.deepcopy(case.records)
        self.assertEqual(case.plan()['details']['failed_attempts'], 2)
        self.assertEqual(case.plan()['state'], 'run')
        self.assertEqual(case.records, before)
        complete = attempts[0]
        complete.update(status='completed', output=case.put({}))
        self.assertIsNone(case.classify(complete))
        case.records['review_stop']['stop']['mode'] = 'after_turn'
        self.assertEqual(case.classify(attempts[1])['outcome'], 'interrupted')

    def test_owner_grants_before_exhaustion_and_nonowner_events_do_not_extend_the_budget(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        case.records['review_context_event'] = dict(
            early=dict(id='early', assignment_id='head', kind='observation', source='owner', recorded_revision=1),
            other=dict(id='other', assignment_id='head', kind='observation', source='agent', recorded_revision=100))
        for _ in range(24):
            case.attempt(code='review_invocation_interrupted')
        position = failures.count_position(case.records, case.artifacts, case.assignment)
        self.assertEqual((position['sequence'], position['permitted_sequences']), (3, 3))
        self.assertEqual((case.plan()['state'], case.plan()['code']), ('stop', 'review_turn_failures_exhausted'))
        case.attempt(code='review_invocation_interrupted')
        case.records['review_context_event']['late'] = dict(id='late', assignment_id='head',
            kind='observation', source='owner', recorded_revision=101)
        self.assertEqual(case.plan()['state'], 'run')
        self.assertEqual(case.plan()['details']['sequence'], 4)

    def test_recovery_results_have_one_shape_and_keep_the_route(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        recovery = failures.plan_capacity_recovery(case.records, case.artifacts, case.assignment, 'output_over_cap')
        self.assertEqual(recovery['kind'], 'decision')
        self.assertTrue(recovery['then'][0]['steps'])
        replacement = dict(state='replace', code='review_output_over_cap', context={},
                           preflight=dict(allowed=True), unit_characters=1000)
        with patch.object(failures, 'plan_output_replacement', return_value=replacement):
            recovery = failures.plan_capacity_recovery(case.records, case.artifacts, case.assignment, 'output_over_cap')
        self.assertEqual(recovery['kind'], 'replace')
        self.assertEqual(recovery['payload']['route_id'], case.assignment['route_id'])
        self.assertEqual(recovery['reason'], 'capacity')
        self.assertTrue(recovery['preflight']['allowed'])


    def test_native_math_handoff_accepts_a_new_attested_packet(self):
        import test_review_parts_native as native_fixtures
        from research_harness import review_route_capacity as capacity
        from test_review_output_profile import build_row
        # This handoff fixture supplies a complete ordinary native output.
        profile = patch.object(capacity, 'read_profile_priors', return_value=[
            dict(build_row(level=None, kind='single', thinking=1), largest_output_tokens=12)])
        profile.start()
        self.addCleanup(profile.stop)
        fixture = self.prepare(native_fixtures.NativePartsTests)
        assignment = fixture.assign()
        evidence = fixture.artifacts.put(b'Observed context exposure.', 'text/plain')
        protocol.record_context_event(fixture.store, dict(id='exposure', assignment_id=assignment['id'],
            kind='contamination', source='author_history', reason='An observed exposure.', evidence=[evidence]),
            expected_revision=fixture.store.revision, request_id='exposure')
        plan = plan_next(fixture.store.snapshot()['records'], fixture.artifacts, assignment['id'])
        self.assertEqual((plan['state'], plan['command'], plan['details']['step']['id']),
                         ('handoff', 'search review-packet', 5))
        self.assertEqual(plan['details']['step']['objective_id'], 'objective')
        self.assertEqual(set(plan['details']['step']['payload']), {'kind', 'subject', 'claim_digest'})
        self.assertEqual(plan['details']['step']['chooses'], ['reviewer', 'inputs'])
        native = copy.deepcopy(fixture.native)
        native['reviewer']['attestation_id'] = 'review-assignment:fresh'
        reference = fixture.artifacts.put(json.dumps(native).encode(), 'application/json')
        payload = dict(id='fresh', role='native_math', route_id='route', dossier_id=None,
                       reviewer_id='reviewer', author_id='author', context=dict(native_packet=reference))
        accepted = protocol.record_assignment(fixture.store, payload,
            expected_revision=fixture.store.revision, request_id='fresh')['result']
        self.assertEqual(accepted['context']['native_packet'], reference)
        self.assertNotIn('context_repair', accepted['context'])

    def test_two_provider_refusals_at_turn_three_finish_without_rereading(self):
        case = self.prepare(reading_fixtures.OpennessReadingCase)
        case.limit_input(24000)
        reference = case.export(assignment_id='reading')
        case.assign('reading', reference)
        refused = []
        successful = []
        def provider(route, request, credential):
            from codex_route_fixtures import build_codex_response, bind_codex_response
            from research_harness.review_excerpts import read_sent_packet
            packet = read_sent_packet(request)
            if 'reading_turn' in packet:
                turn = packet['reading_turn']['reading_plan']['turn']
                if turn == 3 and len(refused) < 2:
                    refused.append(turn)
                    response = build_codex_response(route, request, {})
                    response['status'] = 'incomplete'
                    response['output'][0]['content'][0]['text'] = 'API Error: safeguards flagged this message'
                    return bind_codex_response(route, request, response)
                successful.append(turn)
                output = case.build_reading_response(packet)
            else:
                output = case.build_native_result(packet)
            return build_codex_response(route, request, output)
        steps = []
        with patch.object(protocol.transport, 'send_request', side_effect=provider):
            self.assertEqual(run_series(case.store, 'reading', emit=steps.append), 0)
        self.assertEqual(refused, [3, 3])
        retries = [step for step in steps if step.get('details', {}).get('outcome') == 'provider_refused']
        self.assertEqual([step['details']['sequence'] for step in retries], [1, 1])
        self.assertEqual(successful, [1, 2, 3])
        self.assertEqual(steps[-1]['state'], 'done')


    def test_permanent_outcomes_at_the_eighth_failure_keep_their_own_step(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        for code, tokens, state, expected in (
                ('review_format_unsupported', 0, 'stop', 'review_format_unsupported'),
                ('review_route_runtime_stale', 0, 'handoff', 'review_route_runtime_stale'),
                ('review_incomplete', 131000, 'stop', 'review_output_over_cap')):
            with self.subTest(code=code):
                case.records['review_attempt'].clear()
                for _ in range(7):
                    case.attempt(code='review_invocation_interrupted')
                case.attempt(code=code, tokens=tokens)
                plan = case.plan()
                self.assertEqual((plan['state'], plan['code']), (state, expected))
                self.assertIsNone(plan['not_before'])


    def test_an_identical_replacement_does_not_renew_the_over_cap_retry(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        old = dict(case.assignment, id='old', recorded_revision=0)
        case.records['review_assignment']['old'] = old
        case.assignment['recorded_revision'] = 100
        case.attempt(tokens=131000, assignment='old')
        self.assertEqual(case.plan()['state'], 'run')
        case.attempt(tokens=131000)['recorded_revision'] = 101
        plan = case.plan()
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_output_over_cap'))
        self.assertEqual(plan['details']['failed_attempts'], 2)

    def test_a_smaller_task_keeps_the_same_sequence_after_the_provider_allowance(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        old = dict(case.assignment, id='old', recorded_revision=0,
                   packet=case.put(dict(review_parts=dict(protocol=protocol.review_parts.PROTOCOL, kind='read', unit_characters=2000))))
        case.records['review_assignment']['old'] = old
        case.assignment.update(recorded_revision=100,
                               packet=case.put(dict(review_parts=dict(protocol=protocol.review_parts.PROTOCOL, kind='read', unit_characters=1000))))
        for _ in range(3):
            case.attempt('API Error: safeguards flagged this message', assignment='old')
        self.assertEqual(case.plan()['state'], 'run')
        case.attempt('API Error: safeguards flagged this message')['recorded_revision'] = 101
        position = failures.count_position(case.records, case.artifacts, case.assignment)
        self.assertEqual((position['sequence'], position['failed_attempts'], position['level']), (1, 4, 1))


    def test_planner_refusals_take_the_registry_handoff(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        with patch('research_harness.review_continuation._plan_head', side_effect=ResearchError(
                'review_source_no_progress', 'Acquire the missing original.', dict(version_id='synthetic:work'))):
            plan = fixture.plan()
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'fulltext'))
        self.assertEqual(plan['details']['step']['payload'], dict(version_id='synthetic:work'))
        self.assertEqual(plan['details']['step']['chooses'], [])

    def test_planner_capacity_refusal_is_a_wait_with_a_complete_action(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        with patch('research_harness.review_continuation._plan_head', side_effect=ResearchError(
                'review_capacity_wait', 'A different call owns capacity.')):
            plan = fixture.plan()
        self.assertEqual((plan['state'], plan['command']), ('wait', 'review-run'))
        self.assertEqual(plan['payload'], dict(id='bar-first-run-1', assignment_id='bar-first'))
        self.assertIn('not_before', plan)
        with patch.object(protocol.transport, 'send_request',
                          return_value=fixture.case.build_provider_response(fixture.case.build_review_output('bar'))):
            fixture.execute(plan)


    def test_openness_route_decision_names_registration_before_the_new_export(self):
        case = self.prepare(reading_fixtures.OpennessReadingCase)
        reference = case.export(assignment_id='reading')
        case.assign('reading', reference)
        with patch.object(protocol.transport, 'send_request', side_effect=OSError('The call ended.')):
            case.mutate(protocol.invoke_assignment, dict(id='failed', assignment_id='reading'), credential='fixture')
        with patch.object(failures, 'plan_after_failure', return_value=dict(
                state='stop', code='review_turn_failures_exhausted', details=dict(failed_attempts=8))):
            plan = plan_next(case.store.snapshot()['records'], case.artifacts, 'reading')
        answers = plan['details']['decision']['then']
        self.assertEqual([step['id'] for step in answers[0]['steps']], [7, 4])
        self.assertEqual(answers[0]['steps'][1]['payload']['kind'], 'problem-openness')
        observation = answers[1]['steps'][0]
        self.assertEqual(observation['id'], 9)
        evidence = case.artifacts.put(b'An evidenced owner instruction.', 'text/plain')
        payload = dict(observation['payload'], id='owner-grant', reason='An evidenced continuation.', evidence=[evidence])
        accepted = case.mutate(protocol.record_context_event, payload)['result']
        self.assertEqual((accepted['kind'], accepted['source']), ('observation', 'owner'))


    def test_a_usable_head_completes_the_position_before_later_failed_attempts(self):
        case = self.prepare(failure_fixtures.FailureClassTests)
        case.attempt(code='review_invocation_interrupted')
        completed = case.attempt('{}', code=None, status='completed', provider_status='completed')
        completed['output'] = case.put({})
        case.attempt(code='review_invocation_interrupted')
        position = failures.count_position(case.records, case.artifacts, case.assignment)
        self.assertTrue(position['complete'])
        self.assertEqual(position['failed_attempts'], 0)
        later = dict(case.assignment, id='later', recorded_revision=100)
        case.records['review_assignment']['later'] = later
        self.assertFalse(failures.count_position(case.records, case.artifacts, later)['complete'])


if __name__ == '__main__':
    unittest.main()
