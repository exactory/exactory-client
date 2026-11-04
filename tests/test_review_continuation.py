"""Continuation contracts over synthetic records and real command boundaries."""

import copy
import io
import json
import unittest
from unittest.mock import patch

from research_harness import cli, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.review_task_execution import identify_call
import test_research_problem_reviews as fixtures


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.prepare_review_route()
        self.case.supporting = self.case.artifacts.put(b'A field standard.', 'text/plain')
        self.assignment = self.case.assign('bar', 'first', **self.case.deliver_supporting('first'))

    def plan(self, records=None, head=None):
        from research_harness.review_continuation import plan_next
        return plan_next(records or self.case.read_records(), self.case.artifacts,
                         head or self.assignment['id'])

    def execute(self, plan, request='execute'):
        return cli.OPERATIONS[plan['command']](self.case.store, plan['payload'],
            expected_revision=self.case.store.revision,
            request_id=plan.get('request_id', request))

    def invoke(self, output):
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(output)):
            return self.case.mutate(protocol.invoke_assignment,
                dict(id='observed', assignment_id=self.assignment['id']), credential='fixture')

    def test_fresh_head_has_complete_accepted_run_payload(self):
        plan = self.plan()
        self.assertEqual((plan['state'], plan['chain'], plan['head_id']),
                         ('run', 'session', 'bar-first'))
        self.assertEqual(plan['payload'], dict(id='bar-first-run-1', assignment_id='bar-first'))
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(self.case.build_review_output('bar'))):
            self.execute(plan)
        self.assertEqual(self.plan()['state'], 'record')

    def test_final_is_recorded_exactly_then_done(self):
        output = self.case.build_review_output('bar')
        self.invoke(output)
        plan = self.plan()
        self.assertEqual(plan['state'], 'record')
        self.assertEqual(plan['command'], 'value-review')
        self.assertEqual(plan['payload'], dict(output, id='review:bar-first',
                                             assignment_id='bar-first', dossier_id=None))
        self.execute(plan)
        self.assertEqual(self.plan()['state'], 'done')

    def test_source_request_continues_with_complete_identity(self):
        output = self.case.build_review_output('bar')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        self.invoke(output)
        plan = self.plan()
        self.assertEqual(plan['state'], 'continue')
        self.assertEqual(plan['payload'], dict(id='bar-first-t2', route_id='route',
            dossier_id=None, role='bar', reviewer_id='first', author_id='author',
            context=dict(prior_assignment_id='bar-first')))
        self.execute(plan)
        self.assertEqual(self.plan()['head_id'], 'bar-first-t2')

    def test_captured_unfinished_call_uses_original_request(self):
        from research_harness import review_call_capture
        real_capture = review_call_capture.capture_call
        def captured(*args, **kwargs):
            real_capture(*args, **kwargs)
            raise RuntimeError('killed after capture')
        with patch.object(review_call_capture, 'capture_call', side_effect=captured):
            with self.assertRaises(RuntimeError):
                self.invoke(self.case.build_review_output('bar'))
        records = self.case.read_records()
        capture = records['review_call_observation'][identify_call('review.invoke', 'observed')]
        plan = self.plan()
        self.assertEqual(plan['state'], 'resume')
        self.assertEqual(plan['request_id'], capture['request_id'])
        self.assertEqual(plan['payload'], dict(id='observed', assignment_id='bar-first'))
        with patch('research_harness.review_transport.send_request',
                   side_effect=AssertionError('duplicate reviewer call')):
            self.execute(plan)
        self.assertEqual(self.plan()['state'], 'record')

    def test_pending_invocation_is_waiting_for_reconciliation(self):
        with patch('research_harness.review_protocol._observed', side_effect=RuntimeError('killed')):
            with self.assertRaises(RuntimeError):
                self.invoke(self.case.build_review_output('bar'))
        plan = self.plan()
        self.assertEqual(plan['state'], 'wait')
        self.assertEqual(plan['command'], 'review-run')
        self.assertEqual(plan['payload'], dict(action='reconcile', operation='review.invoke',
                                             invocation_id='observed'))
        self.assertIn('not_before', plan)

    def test_failure_policy_controls_retry_and_replacement_parent(self):
        records = self.case.read_records()
        parent = records['review_assignment']['bar-first']
        failed = dict(parent, id='failed', context=dict(prior_assignment_id='bar-first'),
                      history_assignment_id='bar-first', recorded_revision=100)
        records['review_assignment']['failed'] = failed
        records['review_attempt'] = dict(failure=dict(id='failure', assignment_id='failed',
            status='failed', recorded_revision=101))
        for state in ('run', 'replace', 'wait', 'stop'):
            with self.subTest(state=state), patch(
                    'research_harness.review_failure_class.plan_after_failure',
                    return_value=dict(state=state, not_before='2030-01-01T00:00:00+00:00',
                                      code='fixture_failure', details={'bounded': True})):
                plan = self.plan(records)
                self.assertEqual(plan['state'], state)
                if state == 'replace':
                    self.assertEqual(plan['payload']['context'], dict(prior_assignment_id='bar-first'))
                    self.assertEqual(plan['payload']['id'], 'bar-first-t3')
                elif state in ('run', 'wait'):
                    self.assertEqual(plan['payload'], dict(id='failed-run-2', assignment_id='failed'))

    def test_completed_call_with_unverified_context_uses_failure_policy(self):
        response = self.case.build_provider_response(self.case.build_review_output('bar'))
        response['context_evidence'] = dict(inspected=True, input_verified=False)
        with patch('research_harness.review_transport.send_request', return_value=response):
            self.execute(self.plan())
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='run', code='context_unverified', details={})) as policy:
            plan = self.plan()
        self.assertEqual(plan['state'], 'run')
        self.assertEqual(plan['payload'], dict(id='bar-first-run-2', assignment_id='bar-first'))
        self.assertEqual(policy.call_count, 1)
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(self.case.build_review_output('bar'))):
            self.execute(plan, request='retry-unverified')

    def test_all_registered_roles_and_parts_adapters_keep_their_context(self):
        from research_harness.review_parts_roles import ADAPTERS
        records = self.case.read_records()
        original = records['review_assignment']['bar-first']
        ready = dict(ready=True, output={'kind': 'requests', 'unit_requests': [{'unit': 'u1'}]},
                     refused_attempts=[], obligations=[], pending_source_requests=[])
        for adapter in ADAPTERS:
            role = dict(problem_result='result', problem_adjudicator='adjudicator',
                        context_repair='result', study_readiness='result',
                        study_verification='verification', native_math='native_math')[adapter]
            assignment = dict(original, role=role, context={},
                              review_parts=dict(adapter=adapter, kind='final', turn=1,
                                                series_assignment_id='bar-first'))
            if role == 'verification':
                assignment['context']['verification_task_id'] = 'task'
            elif role == 'native_math':
                assignment['context']['native_packet'] = {'fixture': 'packet'}
            elif role == 'adjudicator':
                assignment['context'].update(review_ids=['r1', 'r2'], objection_id='o', correction=None)
            records['review_assignment']['bar-first'] = assignment
            with self.subTest(adapter=adapter), patch.object(protocol, 'assignment_state', return_value=ready):
                plan = self.plan(records)
                self.assertEqual((plan['state'], plan['chain']), ('continue', 'parts_v2:' + adapter))
                context = dict(assignment['context'], prior_assignment_id='bar-first')
                self.assertEqual(plan['payload']['context'], context)

    def test_new_ids_check_all_record_keys(self):
        records = self.case.read_records()
        records['artifact'] = {'bar-first-run-1': {}, 'bar-first-run-1-2': {}}
        self.assertEqual(self.plan(records)['payload']['id'], 'bar-first-run-1-3')

    def test_revision_alone_does_not_change_progress(self):
        records = self.case.read_records()
        before = self.plan(records)['progress']
        records['unrelated'] = {'other': dict(recorded_revision=999)}
        self.assertEqual(self.plan(records)['progress'], before)

    def test_progress_binds_delivered_units_to_the_actual_request_digest(self):
        self.invoke(self.case.build_review_output('bar'))
        records = self.case.read_records()
        self.assertEqual(self.plan(records)['progress']['deliveries'], [dict(
            assignment_id='bar-first', request_digest=records['review_attempt']['observed']['request_digest'])])

    def test_source_obligation_exposes_complete_action(self):
        from research_harness.problem_delivery import assess_source_requests
        output = self.case.build_review_output('bar')
        output.update(source_requests=[dict(id='page', kind='inventory_page', page=0)])
        output['value']['status'] = 'unresolved'
        self.invoke(output)
        rows = assess_source_requests(self.case.read_records(), self.case.artifacts)
        row = next(row for row in rows if row['code'] == 'review_sources_pending')
        self.assertEqual(row['next'][0]['command'], self.plan()['command'])
        self.assertEqual(row['next'][0]['payload'], self.plan()['payload'])

    def test_gate_annotation_keeps_navigation_fields_and_does_not_mutate_report(self):
        from research_harness.review_continuation import annotate_review_obligations
        navigation = dict(kind='review', workflow='review-run', scope=dict(assignment_id='bar-first'))
        item = dict(code='review_output_pending', assignment_id='bar-first', next=[navigation])
        report = dict(obligations=[item], next=item,
                      remaining_obligations=['A retained scientific objective statement.'])
        before = copy.deepcopy(report)
        annotated = annotate_review_obligations(self.case.read_records(), self.case.artifacts, report)
        action = annotated['obligations'][0]['next'][0]
        self.assertEqual({key: action[key] for key in ('kind', 'workflow', 'scope')}, navigation)
        self.assertEqual(action['command'], 'review-run')
        self.assertEqual(action['payload'], dict(id='bar-first-run-1', assignment_id='bar-first'))
        self.assertEqual(annotated['remaining_obligations'], report['remaining_obligations'])
        self.assertEqual(report, before)

    def test_summary_keeps_large_payload_whole_or_omits_it(self):
        from research_harness.report_views import _hint
        payload = dict(id='a', context=dict(native_packet='λ' * 1024))
        action = dict(kind='review', workflow='review-assignment', scope={},
                      command='review-assignment', payload=payload)
        hint = _hint(dict(code='review_parts_pending', next=[action]))
        self.assertIsNone(hint['next_step'])
        self.assertEqual(hint['omitted_next_steps'], 1)

    def test_context_loss_hands_off_without_inventing_repair_prerequisites(self):
        self.case.mutate(protocol.record_context_event, dict(id='exposure',
            assignment_id='bar-first', kind='contamination', source='author_history',
            reason='An observed exposure.', evidence=[self.case.supporting]))
        plan = self.plan()
        self.assertEqual(plan['state'], 'handoff')
        self.assertEqual(plan['command'], 'review-assignment')
        self.assertNotIn('payload', plan)
        self.assertEqual(plan['code'], 'review_context_contaminated')

    def test_native_first_turn_replacement_needs_new_attestation(self):
        records = self.case.read_records()
        records['review_assignment']['bar-first'].update(role='native_math',
            context=dict(native_packet={'fixture': 'native'}))
        records['review_attempt'] = dict(failed=dict(id='failed', assignment_id='bar-first',
            status='failed', recorded_revision=200))
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='replace', code='fixture', details={})):
            plan = self.plan(records)
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'search review-packet'))
        self.assertEqual(plan['code'], 'native_review_attestation_required')

    def test_capacity_wait_names_existing_call_reconciliation(self):
        records = self.case.read_records()
        records['review_route']['route']['adapter'] = 'codex_cli_v1'
        records['review_invocation'] = dict(other=dict(id='other', assignment_id='other-chain', route_id='route'))
        records['review_pool_membership'] = {identify_call('review.invoke', 'other'):
            dict(released=False, invocation_id='other', operation='review.invoke')}
        with patch.object(protocol, 'assignment_state', return_value=dict(output=None, obligations=[])):
            plan = self.plan(records)
        self.assertEqual(plan['state'], 'wait')
        self.assertEqual(plan['code'], 'review_capacity_wait')
        self.assertEqual(plan['payload'], dict(action='reconcile', operation='review.invoke', invocation_id='other'))

    def test_verification_final_needs_body_and_envelope(self):
        records = self.case.read_records()
        records['review_assignment']['bar-first'].update(role='verification',
            context=dict(verification_task_id='task'))
        with patch.object(protocol, 'assignment_state', return_value=dict(ready=True,
                output=dict(verdict={'stance': 'accept'}, checks=[]), obligations=[])):
            plan = self.plan(records)
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'bind-verdict'))
        self.assertEqual(plan['code'], 'verification_verdict_body_required')

    def test_paper_roles_hand_off_their_actual_recording_boundary(self):
        records = self.case.read_records()
        for role, command in [('manuscript', 'manuscript-review'), ('standalone', 'export')]:
            records['review_assignment']['bar-first']['role'] = role
            with self.subTest(role=role), patch.object(protocol, 'assignment_state',
                    return_value=dict(ready=True, output=dict(review={}), obligations=[])):
                plan = self.plan(records)
                self.assertEqual((plan['state'], plan['command']), ('handoff', command))
                self.assertNotIn('payload', plan)

    def test_two_refusals_hand_off_correction(self):
        for number in range(2):
            output = self.case.build_review_output('bar')
            output['value']['status'] = 'invented'
            plan = self.plan()
            with patch('research_harness.review_transport.send_request',
                       return_value=self.case.build_provider_response(output)):
                self.execute(plan, request='refused-' + str(number))
            plan = self.plan()
            with self.assertRaises(ResearchError):
                self.execute(plan, request='record-refused-' + str(number))
        plan = self.plan()
        self.assertEqual(plan['state'], 'handoff')
        self.assertEqual(plan['code'], 'review_slot_exhausted')
        self.assertEqual(plan['command'], 'review-output-correction')

if __name__ == '__main__':
    unittest.main()
