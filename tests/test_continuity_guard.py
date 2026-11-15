"""Executed Stage 1 boundaries and retained protocol compatibility."""

import copy
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ci import capacity_guard, continuity_guard as guard
from research_harness import cli, review_protocol as protocol, review_route_capacity as capacity, stop_classes
from research_harness import review_failure_class as failures, review_transport as transport
from research_harness.evidence import digest
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series
import test_capacity_signals as response_fixtures
import test_d2_outcomes as outcome_fixtures
import test_review_failure_class as failure_fixtures
import test_review_continuation as session_fixtures
import problem_parts_fixtures as parts_fixtures
import test_record_reading_handoff as reading_fixtures
import test_research_problem_reviews as problem_fixtures
import test_review_parts_adjudication_journey as focused_fixtures
import test_research_review_protocol as paper_fixtures
import test_research_publication as publication_fixtures
from test_review_route_capacity import build_route
from test_review_output_profile import build_request
from codex_route_fixtures import record_codex_route, invoke_codex, build_codex_response
import review_parts_fixtures
import continuity_guard_fixtures as matrix_fixtures


class StagedPartsCase(parts_fixtures.ResultPartsCase):
    staged_entry = True
    output_tokens = 1000


class ContinuityGuardTests(unittest.TestCase):
    def prepare(self, cls):
        case = cls()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def test_registration_alone_cannot_finish_an_existing_head_stop(self):
        stop = dict(code='review_response_too_large', details=dict(decision=dict(
            then=[dict(answer='Move', steps=[dict(command='review-route')])])), head_id='head')
        journey = dict(answer='Move', accepted_commands=['review-route'], end=dict(kind='route', id='new'))
        self.assertIn(guard.STOP_END_MESSAGE, guard.validate_stop_journeys(stop, [journey], stop_classes.STOP_REGISTRY))
        journey['end'] = dict(kind='assignment', id='new-head', prior_head='head', eligible=True)
        journey['accepted_commands'].append('review-assignment')
        self.assertEqual(guard.validate_stop_journeys(stop, [journey], stop_classes.STOP_REGISTRY), [])
        self.assertTrue(guard.validate_stop_journeys(stop, [], stop_classes.STOP_REGISTRY))

    def test_current_packet_kinds_and_size_outcomes_classify_without_execution_credit(self):
        kinds = capacity.TURN_KINDS
        executed = []
        for kind, outcome, detail in guard.list_size_cases(kinds):
            with self.subTest(kind=kind, outcome=outcome, detail=detail):
                case = self.prepare(failure_fixtures.FailureClassTests)
                case.records['review_route']['route'] = build_route()
                packet = guard.build_kind_packet(kind)
                case.assignment['packet'] = case.put(packet)
                operation = 'review.route_probe' if kind == 'probe' else 'review.output_correction' if kind == 'correction' else 'review.invoke'
                measured = capacity.describe_task('result', packet, route=build_route(),
                    request=build_request(build_route(), packet), operation=operation)
                self.assertEqual(measured['kind'], kind)
                if kind == 'probe':
                    # A failed probe has no reviewer head to replace and gives no capacity credit.
                    self.assertEqual(guard.describe_probe_boundary(outcome), 'unverified')
                    executed.append((kind, outcome, detail))
                    continue
                if outcome == 'output_over_cap':
                    options = dict(answer="API Error: Claude's response exceeded the 32000 output token maximum."
                        if detail else '{}', tokens=128000)
                elif outcome == 'input_over_limit':
                    options = dict(answer='Prompt is too long')
                    if detail:
                        case.records['review_route']['route']['adapter'] = 'codex_cli_v1'
                        options = dict(answer='{}')
                elif outcome == 'response_over_limit':
                    options = dict(code='review_response_too_large')
                else:
                    options = dict(status='timed_out')
                before = copy.deepcopy(case.assignment)
                for index in range(2):
                    attempt = case.attempt(**options)
                    if outcome == 'input_over_limit' and detail:
                        response = json.loads(case.artifacts.read(attempt['response']))
                        response['context_evidence'] = dict(issues=['uninspected_compaction'])
                        attempt['response'] = case.put(response)
                    self.assertEqual(case.classify(attempt)['outcome'], outcome)
                    plan = case.plan()
                    if plan['state'] in ('stop', 'handoff'):
                        break
                    self.assertEqual(plan['state'], 'run')
                # Smaller legacy parts tasks can replace; other kinds use the finite route decision.
                self.assertIn(plan['state'], ('stop', 'handoff', 'continue'))
                from research_harness.review_plan_steps import complete_plan
                plan = complete_plan(case.records, case.artifacts, case.assignment, plan)
                self.assertIn(plan.get('code'), stop_classes.STOP_REGISTRY)
                self.assertTrue(plan['details'].get('decision') or plan['details'].get('step') or plan.get('payload'))
                self.assertEqual(case.assignment, before)
                executed.append((kind, outcome, detail))
        self.assertEqual(set(executed), set(guard.list_size_cases(kinds)))
        self.assertTrue(guard.validate_size_matrix(executed, kinds))

    def test_size_kinds_execute_real_commands_to_preflight_or_all_stop_answers(self):
        kinds = capacity.TURN_KINDS
        executed = []
        for kind in kinds:
            with self.subTest(kind=kind):
                try:
                    if kind == 'probe':
                        for outcome, detail in guard.SIZE_OUTCOMES:
                            executed.append(matrix_fixtures.execute_probe_size(self, outcome, detail))
                    else:
                        executed.extend(matrix_fixtures.execute_assignment_sizes(self, kind, guard.SIZE_OUTCOMES))
                finally:
                    self.doCleanups()
        self.assertEqual(guard.validate_size_matrix(executed, kinds), [])
        self.assertTrue(guard.validate_size_matrix(executed[:-1], kinds))
        missing_preflight = copy.deepcopy(executed)
        assignment = next(row for row in missing_preflight if row['boundary']['kind'] == 'assignment')
        assignment['boundary'].pop('preflight')
        self.assertTrue(guard.validate_size_matrix(missing_preflight, kinds))
        route_only = copy.deepcopy(executed)
        route_only[0]['boundary'] = dict(kind='route', id='registered-only')
        self.assertTrue(guard.validate_size_matrix(route_only, kinds))
        missing_call = copy.deepcopy(executed)
        missing_call[0].pop('call')
        self.assertTrue(guard.validate_size_matrix(missing_call, kinds))

    def test_size_failures_of_real_probes_give_no_verified_capacity(self):
        executed = []
        for outcome, detail in guard.SIZE_OUTCOMES:
            with self.subTest(outcome=outcome, detail=detail):
                executed.append(matrix_fixtures.execute_probe_size(self, outcome, detail))
        self.assertEqual(guard.validate_size_matrix(executed, ('probe',)), [])

    def test_size_stops_execute_all_answers_to_a_recorded_review(self):
        for outcome, detail in guard.SIZE_OUTCOMES:
            with self.subTest(outcome=outcome, detail=detail):
                fixture = self.prepare(response_fixtures.ResponseLimitJourneyTests)
                case = self.prepare(problem_fixtures.ProblemReviewTests)
                case.supporting = case.artifacts.put(b'A synthetic field standard.', 'text/plain')
                record_codex_route(case, 'route', output_tokens=128000)
                initial = case.assign('bar', 'first', **case.deliver_supporting('first'))
                output = case.build_review_output('bar')
                output.update(source_requests=[dict(id='inventory', kind='inventory_page', page=0)])
                output['value']['status'] = 'unresolved'
                invoke_codex(case, initial['id'], output)
                continuing = plan_next(case.read_records(), case.artifacts, initial['id'])
                head = fixture.execute(case, continuing['command'], continuing['payload'])['result']
                def fail_request(route, request, credential):
                    if outcome == 'response_over_limit':
                        raise ResearchError('review_response_too_large', 'Synthetic response body limit.')
                    if outcome == 'input_over_limit' and detail is None:
                        raise ResearchError('review_input_too_large', 'Synthetic input limit.')
                    response = build_codex_response(route, request, {})
                    response['status'] = 'incomplete'
                    if outcome == 'output_over_cap':
                        response['usage']['output_tokens'] = 128000
                        if detail:
                            response['output'][0]['content'][0]['text'] = "API Error: Claude's response exceeded the 32000 output token maximum."
                    elif outcome == 'timeout':
                        response.setdefault('execution', {})['timeout'] = True
                    else:
                        response['context_evidence'] = dict(issues=['uninspected_compaction'])
                    return response
                emitted = []
                with patch.object(transport, 'send_request', side_effect=fail_request) as sender:
                    self.assertEqual(run_series(case.store, head['id'], emit=emitted.append), 3)
                stop = plan_next(case.read_records(), case.artifacts, head['id'])
                self.assertIn(stop['state'], ('stop', 'handoff'), stop)
                self.assertEqual(stop['details']['outcome'], outcome)
                if detail:
                    self.assertEqual(stop['details']['signal']['detail'], detail)
                self.assertLessEqual(sender.call_count, 2)
                answers = stop['details']['decision']['then']
                self.assertEqual(len(answers), 1)
                steps = answers[0]['steps']
                self.assertEqual([step['id'] for step in steps], [8, 7, 1])
                evidence = case.artifacts.put(b'Continue this review on a verified serving route.', 'text/plain')
                event = fixture.execute(case, steps[0]['command'], dict(steps[0]['payload'], id='retire',
                    reason='The earlier request reached a size boundary.', evidence=[evidence]))['result']
                record_codex_route(case, 'serving', output_tokens=128000)
                payload = copy.deepcopy(steps[-1]['payload'])
                payload.update(id='repaired', route_id='serving', reviewer_id='first')
                payload['context']['context_repair'].update(event_ids=[event['id']], evidence=[evidence],
                    probe_id='probe-serving', reason='Continue the same review after the size stop.')
                following = fixture.execute(case, steps[-1]['command'], payload)['result']
                def answer(packet):
                    if packet.get('review_parts', {}).get('kind') not in (None, 'final'):
                        return review_parts_fixtures.make_output(packet)
                    final = case.build_review_output('bar')
                    final['reassessment'] = dict(assignment_id=head['id'], findings=[])
                    return final
                self.assertEqual(fixture.drive(case, following['id'], answer), 0)
                self.assertTrue(any(row['assignment_id'] == following['id'] for row in case.read_records()['value_review'].values()))
                self.assertEqual(guard.validate_stop_journeys(stop, [dict(answer=answers[0]['answer'],
                    accepted_commands=['review-context', 'review-route', 'review-probe', 'review-assignment'],
                    end=dict(kind='assignment', id=following['id'], prior_head=head['id'],
                        eligible=following.get('slot_id') == initial['id']))], stop_classes.STOP_REGISTRY), [])

    def test_budget_stop_grants_calls_then_records_without_an_admission_send(self):
        fixture = self.prepare(session_fixtures.ContinuationTests)
        case = fixture.case
        driver = self.prepare(response_fixtures.ResponseLimitJourneyTests)
        from research_harness.review_budget import read_budget_usage
        maximum = read_budget_usage(case.read_records())['charged_calls']
        driver.execute(case, 'review-budget', dict(max_calls=maximum, reason='Bound the fixture calls.'))
        with patch.object(transport, 'send_request', side_effect=AssertionError('Budget admission sent a request')):
            emitted = []
            self.assertEqual(run_series(case.store, fixture.assignment['id'], emit=emitted.append), 3)
        stop = emitted[-1]
        self.assertEqual(stop['code'], 'review_budget_exhausted')
        step = stop['details']['decision']['then'][0]['steps'][0]
        driver.execute(case, step['command'], dict(step['payload'], max_calls=maximum + 3,
            reason='Grant the calls required for this fixture review.'))
        with patch.object(transport, 'send_request', return_value=case.build_provider_response(case.build_review_output('bar'))):
            self.assertEqual(run_series(case.store, fixture.assignment['id'], emit=lambda row: None), 0)
        self.assertEqual(fixture.plan()['state'], 'done')

    def test_response_limit_group_rows_execute_to_recorded_reviews(self):
        identifiers = [
            'test_capacity_signals.ResponseLimitJourneyTests.test_existing_bar_session_head_moves_by_retirement_probe_and_repair_to_recording',
            'test_capacity_signals.ResponseLimitJourneyTests.test_problem_openness_moves_by_probe_and_new_export_to_recorded_finding',
            'test_capacity_signals.ResponseLimitJourneyTests.test_math_review_moves_by_new_attestation_to_recorded_acceptance',
            'test_capacity_signals.ResponseLimitJourneyTests.test_verification_moves_by_probe_and_first_assignment_to_recorded_verdict',
        ]
        self.assertEqual(set(capacity_guard.run_evidence_tests(identifiers).values()), {'passed'})

    def test_response_limit_slate_and_result_rows_preserve_the_slot_to_recording(self):
        for role in ('slate', 'result'):
            with self.subTest(role=role):
                fixture = self.prepare(response_fixtures.ResponseLimitJourneyTests)
                case = self.prepare(problem_fixtures.ProblemReviewTests)
                case.observe_bar_reviews()
                fixture.register_https(case)
                assignment = fixture.execute(case, 'review-assignment', dict(id='old', role=role,
                    route_id='https', dossier_id=None, reviewer_id='first', author_id='author', context={}))['result']
                steps = fixture.stop_response(case, assignment['id'])
                stop = plan_next(case.read_records(), case.artifacts, assignment['id'])
                evidence = case.artifacts.put(b'Move the stopped slot to a verified route.', 'text/plain')
                retired = fixture.execute(case, steps[0]['command'], dict(steps[0]['payload'], id='retire',
                    reason='The response body exceeded this transport limit.', evidence=[evidence]))['result']
                record_codex_route(case, 'serving', output_tokens=128000)
                payload = copy.deepcopy(steps[2]['payload'])
                payload.update(id='new', route_id='serving', reviewer_id='first')
                payload['context']['context_repair'].update(event_ids=[retired['id']], evidence=[evidence],
                    probe_id='probe-serving', reason='Reassess the preserved slot on a serving route.')
                following = fixture.execute(case, steps[2]['command'], payload)['result']
                output = case.build_review_output(role)
                if role == 'slate':
                    output['value']['rubric_potential'] = {}
                    for check in output['value']['assurances']:
                        check['status'] = 'not_assessed'
                output['reassessment'] = dict(assignment_id=assignment['id'], findings=[])
                self.assertEqual(fixture.drive(case, following['id'], lambda packet: output), 0)
                self.assertTrue(any(row['assignment_id'] == following['id'] for row in case.read_records()['value_review'].values()))
                journey = dict(answer=stop['details']['decision']['then'][0]['answer'],
                    accepted_commands=['review-context', 'review-route', 'review-probe', 'review-assignment'],
                    end=dict(kind='assignment', id=following['id'], prior_head=stop['head_id'],
                        eligible=following.get('slot_id') == assignment.get('slot_id', assignment['id'])))
                self.assertEqual(guard.validate_stop_journeys(stop, [journey], stop_classes.STOP_REGISTRY), [])

    def test_response_limit_adjudicator_row_reaches_a_recorded_disposition(self):
        fixture = self.prepare(response_fixtures.ResponseLimitJourneyTests)
        focused = self.prepare(focused_fixtures.AdjudicationPartsCase)
        case = focused.case
        fixture.register_https(case)
        assignment = fixture.execute(case, 'review-assignment', dict(id='old', role='adjudicator', route_id='https',
            dossier_id=None, reviewer_id='old-third', author_id='author', context=focused.context))['result']
        steps = fixture.stop_response(case, assignment['id'])
        record_codex_route(case, 'serving', output_tokens=128000)
        following = fixture.execute(case, steps[-1]['command'], dict(steps[-1]['payload'], id='new',
            route_id='serving', reviewer_id='third'))['result']
        self.assertEqual(fixture.drive(case, following['id'], lambda packet: focused.final_output()), 0)
        self.assertTrue(any(row['assignment_id'] == following['id'] for row in case.read_records()['review_adjudication'].values()))

    def test_response_limit_paper_rows_reach_the_recording_and_export_actions(self):
        for role in ('manuscript', 'standalone'):
            with self.subTest(role=role):
                fixture = self.prepare(response_fixtures.ResponseLimitJourneyTests)
                if role == 'manuscript':
                    case = self.prepare(publication_fixtures.ResearchPublicationTests)
                    bundle = case.mutate(case.publication().prepare_publication, case.bundle_payload())['result']
                    context = dict(bundle=bundle)
                    author = bundle['candidate']['authors'][0]
                    output = dict(review=case.core(), prediction=dict(corpus='arxiv', category='cs.LG',
                        windowStart='2026-01-01', windowEnd='2026-01-31', percentile=30, band=dict(best=20, worst=40)),
                        reasons=['The supplied work establishes a bounded result.'])
                else:
                    case = self.prepare(paper_fixtures.ReviewProtocolTests)
                    context = dict(artifact_refs=[case.artifacts.put(b'A supplied paper.', 'text/plain')])
                    author = 'author'
                    output = dict(review='A bounded review of the supplied evidence.')
                fixture.register_https(case)
                assignment = fixture.execute(case, 'review-assignment', dict(id='old', role=role, route_id='https',
                    dossier_id=None, reviewer_id='paper-reviewer', author_id=author, context=context))['result']
                steps = fixture.stop_response(case, assignment['id'])
                record_codex_route(case, 'serving', output_tokens=128000)
                following = fixture.execute(case, steps[-1]['command'], dict(steps[-1]['payload'], id='new',
                    route_id='serving', reviewer_id='paper-reviewer'))['result']
                fixture.drive(case, following['id'], lambda packet: output)
                boundary = plan_next(case.store.snapshot()['records'], case.artifacts, following['id'])
                self.assertEqual(boundary['command'], 'manuscript-review' if role == 'manuscript' else 'export')
                if role == 'manuscript':
                    reference = case.artifacts.put(json.dumps(output['review']).encode(), 'application/json')
                    payload = dict(id='recorded-paper', bundle_digest=bundle['digest'], assignment_id=following['id'],
                        assessor=dict(id='paper-reviewer', kind='agent', provenance=case.artifacts.put(b'Independent fixture context.', 'text/plain'),
                            relationship='An independent assessor.', independence_basis='A fresh blind context.'),
                        review=reference, blind=True)
                    saved = fixture.execute(case, boundary['command'], payload)['result']
                    self.assertEqual(saved['assignment_id'], following['id'])
                else:
                    rendered = io.StringIO()
                    with contextlib.redirect_stdout(rendered):
                        self.assertIsNone(cli.main(['export', '--kind', 'outputs', '--workspace', str(case.root)]))
                    report = json.loads(rendered.getvalue())
                    self.assertEqual(report['revision'], case.store.revision)
                    self.assertEqual(report['errors'], 0)
                    self.assertTrue((case.root / '.exactory/output-views.json').is_file())

    def test_c12_fulltext_abstract_and_mixed_rows_execute_to_their_end(self):
        identifiers = [
            'test_record_reading_handoff.ReadingHandoffTests.test_delivered_fulltext_hands_off_reading_then_records_the_same_output_without_a_call',
            'test_record_reading_handoff.ReadingHandoffTests.test_second_output_citing_descriptor_hands_off_only_the_fulltext_reading',
            'test_record_reading_handoff.ReadingHandoffTests.test_abstract_refusal_precedes_every_missing_reading_and_rechecks_from_frozen_output',
            'test_record_reading_handoff.FocusedReadingHandoffTests.test_adjudication_final_hands_off_a_stale_reading_then_records_the_same_output',
        ]
        self.assertEqual(set(capacity_guard.run_evidence_tests(identifiers).values()), {'passed'})
        case = self.prepare(reading_fixtures.ReadingHandoffTests)
        case.build_final(count=0, abstract=True)
        case.case.assign('bar', 'second', identifier='other-slot')
        with self.assertRaises(ResearchError):
            case.execute(case.plan())
        corrected = case.case.build_review_output('bar')
        corrected['value']['evidence'] = [case.abstract_link['artifact']]
        with patch.object(protocol.transport, 'send_request', return_value=case.case.build_provider_response(corrected)):
            self.assertEqual(run_series(case.case.store, 'initial', emit=lambda row: None), 0, case.plan())
        self.assertEqual(case.plan()['state'], 'done')

    def test_staged_refusal_preserves_the_slot_and_series_records_the_review(self):
        fixture = self.prepare(StagedPartsCase)
        fixture.oversized()
        assignment = fixture.assign('ordinary')
        records = fixture.case.read_records()
        with patch.object(transport, 'send_request', side_effect=AssertionError('admission called reviewer')):
            with self.assertRaises(ResearchError) as caught:
                fixture.case.mutate(protocol.record_assignment, dict(id='controller', role='result', dossier_id=None,
                    route_id=assignment['route_id'], reviewer_id=assignment['reviewer_id'], author_id=assignment['author_id'],
                    context=dict(staged_upgrade=dict(assignment_id=assignment['id']))))
        self.assertEqual(caught.exception.code, 'review_staged_scale_unsupported')
        self.assertEqual(fixture.case.read_records(), records)
        _, final = fixture.finish_parts(assignment)
        fixture.record_final(final)
        self.assertTrue(protocol.assignment_state(fixture.case.read_records(), fixture.case.artifacts, final['id'])['ready'])
        self.assertEqual(fixture.case.read_records()['review_assignment'][assignment['id']], records['review_assignment'][assignment['id']])

    def test_external_task_loss_has_no_plugin_coverage_claim(self):
        entry = next(row for row in capacity_guard.read_matrix()['entries'] if row['id'] == 'T3')
        self.assertEqual(entry['scope'], 'external')
        self.assertEqual(entry['units']['construction'], [])
        self.assertEqual(entry['evidence']['reports'], [])
        self.assertNotIn('external_task', capacity.TURN_KINDS)

    def test_generated_counts_and_stored_requests_keep_their_meaning(self):
        identifiers = [
            'test_d2_outcomes.OutcomeTests.test_generated_mixed_sequences_never_exceed_eight_or_renew_a_terminal_outcome',
            'test_d2_outcomes.OutcomeTests.test_current_validator_revives_the_head_without_changing_stored_bytes',
            'test_d2_outcomes.OutcomeTests.test_three_reconciled_interruptions_continue_the_reading_chain',
            'test_record_reading_handoff.ReadingHandoffTests.test_stored_reviews_and_refusal_certificates_keep_their_identities_through_the_handoff',
        ]
        self.assertEqual(set(capacity_guard.run_evidence_tests(identifiers).values()), {'passed'})
