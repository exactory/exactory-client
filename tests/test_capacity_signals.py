"""Capacity signals retain the evidence and use D2's existing recovery vocabulary."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from research_harness import review_failure_class as failures, review_route_capacity as capacity
from research_harness import review_protocol as protocol, stop_classes
from research_harness import review_claude_transport as claude_transport
from research_harness.review_plan_steps import complete_plan
import test_review_failure_class as failure_fixtures
from test_review_output_profile import build_request, build_response
from test_review_route_capacity import build_route
import test_research_claude_transport as stream_fixtures
import test_review_continuation as continuation_fixtures
import test_review_parts_study as study_fixtures
import openness_reading_fixtures as reading_fixtures
from codex_route_fixtures import record_codex_route, build_codex_response
from research_harness import cli, review_excerpts
from research_harness.errors import ResearchError
from research_harness.review_continuation import plan_next
from research_harness.review_series import run_series


class CapacitySignalTests(unittest.TestCase):
    def setUp(self):
        self.case = failure_fixtures.FailureClassTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.records['review_route']['route'] = build_route()

    def test_claude_output_maximum_reduced_and_fallback_have_distinct_details(self):
        for message, outcome, detail in (
            ("API Error: Claude's response exceeded the 128000 output token maximum. To configure this behavior, ...", 'output_over_cap', None),
            ("API Error: Claude's response exceeded the 32000 output token maximum.", 'output_over_cap', 'reduced_by_context'),
            ("API Error: Claude's response exceeded the 64000 output token maximum for a request sent without streaming.", 'execution_incomplete', 'non_streaming_fallback'),
            ('Prompt is too long', 'input_over_limit', None),
        ):
            with self.subTest(message=message):
                before = copy.deepcopy(self.case.records['review_route'])
                result = self.case.classify(self.case.attempt(message, tokens=179808))
                self.assertEqual(result['outcome'], outcome)
                self.assertEqual(result['signal'].get('detail'), detail)
                self.assertEqual(self.case.records['review_route'], before)

    def test_codex_context_window_and_sole_compaction_issue_are_input_events(self):
        self.case.records['review_route']['route']['adapter'] = 'codex_cli_v1'
        attempt = self.case.attempt(diagnostic='context_window_exceeded')
        self.assertEqual(self.case.classify(attempt)['outcome'], 'input_over_limit')
        for issues, outcome in ((['uninspected_compaction'], 'input_over_limit'),
                                (['uninspected_compaction', 'unknown_input'], 'context_unverified')):
            attempt = self.case.attempt('{}', status='completed', provider_status='completed')
            response = json.loads(self.case.artifacts.read(attempt['response']))
            response['context_evidence'] = dict(inspected=True, input_verified=False, issues=issues)
            attempt['response'] = self.case.put(response)
            result = self.case.classify(attempt)
            self.assertEqual(result['outcome'], outcome)
            if outcome == 'input_over_limit':
                self.assertEqual(result['signal']['detail'], 'compacted')

    def test_claude_compaction_stream_is_input_limit_and_unknown_event_is_unverified(self):
        route = build_route()
        request = build_request(route, {})
        for subtype, outcome in (('compact_boundary', 'input_over_limit'), ('future_context_event', 'context_unverified')):
            with self.subTest(subtype=subtype):
                events = stream_fixtures.clean_stream(request, '{}', model=route['model'])
                event = dict(type='system', subtype=subtype, compact_metadata=dict(trigger='auto', pre_tokens=960000))
                events.insert(-1, event)
                with patch.object(claude_transport, '_cli', return_value=('/fixture/claude', '2.1.synthetic')), patch.object(
                        claude_transport, '_run_owned', return_value=stream_fixtures.completed(events)):
                    response = claude_transport.send_request(route, request)
                self.assertFalse(response['context_evidence']['input_verified'])
                self.assertTrue(response['execution']['system_events']['events'])
                attempt = self.case.attempt('{}', status='completed', provider_status='completed', tokens=900)
                attempt.update(response=self.case.put(response), output=self.case.put({}))
                before = copy.deepcopy(attempt)
                result = self.case.classify(attempt)
                self.assertEqual(result['outcome'], outcome)
                self.assertEqual(attempt, before)
                if subtype == 'compact_boundary':
                    self.assertEqual(result['signal']['detail'], 'compacted')
                    self.assertEqual(self.case.plan()['state'], 'stop')

    def test_response_limit_has_one_retry_then_role_steps_and_no_profile_row(self):
        for expected in ('run', 'stop'):
            attempt = self.case.attempt(code='review_response_too_large', tokens=200000)
            result = self.case.classify(attempt)
            self.assertEqual(result['outcome'], 'response_over_limit')
            plan = self.case.plan()
            self.assertEqual((plan['state'], plan['code']), (expected, 'review_response_too_large'))
        plan = complete_plan(self.case.records, self.case.artifacts, self.case.assignment, plan)
        self.assertEqual([step['id'] for step in plan['details']['decision']['then'][0]['steps']], [8, 7, 1])
        self.assertIn('serving_routes', plan['details'])
        self.assertEqual(stop_classes.STOP_REGISTRY['review_response_too_large']['next']['outcome'], 'response_over_limit')
        measured = capacity.describe_attempt(build_route(), 'result', {}, build_request(build_route(), {}),
                                             {}, attempt_error=dict(code='review_response_too_large'))
        self.assertEqual(capacity.build_profile_rows([dict(measured, output_limit=128000)]), [])

    def test_fallback_enters_no_output_row_and_error_numbers_change_no_measure(self):
        route = build_route()
        packet = {}
        request = build_request(route, packet)
        before = capacity.measure_request(route, request)
        response = build_response(output=179808, completed=False)
        response['output'][0]['content'][0]['text'] = "API Error: Claude's response exceeded the 64000 output token maximum for a request sent without streaming."
        measured = capacity.describe_attempt(route, 'bar', packet, request, response)
        self.assertEqual(capacity.build_profile_rows([dict(measured, output_limit=128000)]), [])
        self.assertEqual(capacity.measure_request(route, request), before)
        self.case.attempt(response['output'][0]['content'][0]['text'])
        self.assertEqual((self.case.plan()['state'], self.case.plan()['details']['outcome']),
                         ('run', 'execution_incomplete'))

    def test_effective_model_output_limit_decides_a_numeric_signal(self):
        self.case.records['review_route']['route']['configuration']['max_output_tokens'] = 600000
        attempt = self.case.attempt(tokens=128000)
        result = self.case.classify(attempt)
        self.assertEqual(result['outcome'], 'output_over_cap')
        self.assertEqual(result['signal']['max_output_tokens'], 128000)
        self.assertEqual(self.case.plan()['details']['max_output_tokens'], 128000)

    def test_excluded_samples_never_displace_or_raise_a_profile(self):
        route = build_route()
        measured = capacity.describe_attempt(route, 'bar', {}, build_request(route, {}), build_response())
        excluded = dict(measured, id='fallback', origin='instrumented', assignment_id='head',
            output_profile_eligible=False, recorded_revision=10, verified_input_tokens=900000)
        records = dict(review_route={'route': route}, review_assignment={'head': dict(route_id='route')},
                       review_attempt={'fallback': excluded})
        result = capacity.read_output_profile(records, route, priors=())
        self.assertEqual((result['rows'], result['store_attempts'], result['verified_input_tokens']), ([], 0, 0))

    def test_retained_error_status_precedes_response_size(self):
        attempt = self.case.attempt(code='review_response_too_large')
        # Future transport evidence uses the existing provider status field.
        attempt['error']['details'] = dict(status=503)
        self.assertEqual(self.case.classify(attempt)['outcome'], 'provider_unavailable')
        attempt['error']['details']['status'] = 200
        self.assertEqual(self.case.classify(attempt)['outcome'], 'response_over_limit')


class ResponseLimitJourneyTests(unittest.TestCase):
    def test_math_review_moves_by_new_attestation_to_recorded_acceptance(self):
        result = subprocess.run([sys.executable, str(Path(__file__).parent / 'fixtures/capacity_math_journey.py')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('recorded acceptance', result.stdout)

    def test_response_choices_exclude_unverified_retired_and_unsupported_routes(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        case = fixture.case
        record_codex_route(case, 'serving', output_tokens=128000)
        self.execute(case, 'review-route', dict(id='unverified', adapter='codex_cli_v1', model='fixture-model',
            endpoint='codex://local', configuration=dict(max_output_tokens=128000, timeout_seconds=5)))
        records = case.read_records()
        def describe(records):
            return complete_plan(records, case.artifacts, fixture.assignment,
                dict(state='stop', code='review_response_too_large', details={}))['details']['serving_routes']
        self.assertEqual(describe(records), ['serving'])
        packet = protocol._read_json(case.artifacts, fixture.assignment['packet'])
        unsupported = dict(packet, evidence=[dict(encoding='base64', content='AA==',
            artifact=case.artifacts.put(b'\x00', 'application/octet-stream'))])
        self.assertEqual(capacity.find_serving_routes(records, 'bar', unsupported, artifacts=case.artifacts,
            assignment=fixture.assignment, prompt=fixture.assignment['prompt']['text']), [])
        oversized = dict(packet, synthetic_material='x' * 700000)
        self.assertEqual(capacity.find_serving_routes(records, 'bar', oversized, artifacts=case.artifacts,
            assignment=fixture.assignment, prompt=fixture.assignment['prompt']['text']), [])
        second = self.execute(case, 'review-assignment', dict(id='second', role='bar', dossier_id=None,
            route_id='serving', reviewer_id='second', author_id='author', context={}))['result']
        evidence = case.artifacts.put(b'Withdraw this fixture route.', 'text/plain')
        self.execute(case, 'review-context', dict(id='withdraw', assignment_id=second['id'], source='owner',
            kind='route_retired', reason='Withdraw this fixture route.', evidence=[evidence]))
        self.assertEqual(describe(case.read_records()), [])

    def prepare(self, cls):
        fixture = cls()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def execute(self, case, command, payload, **options):
        return cli.OPERATIONS[command](case.store, payload, expected_revision=case.store.revision,
            request_id='capacity-step-' + str(case.store.revision), **options)

    def register_https(self, case):
        self.execute(case, 'review-route', dict(id='https', adapter='openai_responses_v1',
            endpoint='https://example.test/responses', model='fixture-model',
            configuration=dict(max_output_tokens=1000, timeout_seconds=5)))
        def respond_to_probe(route, request, credential):
            packet = review_excerpts.read_sent_packet(request)
            return dict(id='positive', status='completed', usage=dict(input_tokens=37, output_tokens=12),
                output=[dict(type='message', content=[dict(type='output_text', text=json.dumps(dict(
                    allowed_control=packet['allowed_control'], evidence_control=packet['evidence_control'], excluded_controls=[])))])])
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture'}), patch.object(
                protocol.transport, 'send_request', side_effect=respond_to_probe):
            self.execute(case, 'review-probe', dict(id='https-probe', route_id='https'))

    def stop_response(self, case, identifier):
        records = case.store.snapshot()['records']
        route = records['review_route'][records['review_assignment'][identifier]['route_id']]
        before = capacity.read_output_profile(records, route, priors=())['rows']
        # This is the code, with no status, that the 0.88 HTTPS transport actually raises.
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture'}), patch.object(protocol.transport, 'send_request', side_effect=ResearchError(
                'review_response_too_large', 'The provider response exceeds 16 MiB')) as sender:
            run_series(case.store, identifier, emit=lambda row: None)
        self.assertEqual(sender.call_count, 2)
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, identifier)
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_response_too_large'))
        self.assertEqual(capacity.read_output_profile(case.store.snapshot()['records'], route, priors=())['rows'], before)
        return plan['details']['decision']['then'][0]['steps']

    def drive(self, case, identifier, output):
        with patch.object(protocol.transport, 'send_request', side_effect=lambda route, request, credential:
                build_codex_response(route, request, output(review_excerpts.read_sent_packet(request)))):
            return run_series(case.store, identifier, emit=lambda row: None)

    def test_existing_bar_session_head_moves_by_retirement_probe_and_repair_to_recording(self):
        fixture = self.prepare(continuation_fixtures.ContinuationTests)
        case = fixture.case
        output = case.build_review_output('bar')
        output.update(source_requests=[dict(id='inventory', kind='inventory_page', page=0)])
        output['value']['status'] = 'unresolved'
        fixture.invoke(output)
        head = fixture.execute(fixture.plan())['result']
        steps = self.stop_response(case, head['id'])
        self.assertEqual([step['id'] for step in steps], [8, 7, 1])
        evidence = case.artifacts.put(b'Move this stopped review to the text route.', 'text/plain')
        retired = self.execute(case, steps[0]['command'], dict(steps[0]['payload'], id='retire',
            reason='Move the slot to a serving route.', evidence=[evidence]))['result']
        record_codex_route(case, 'serving', output_tokens=128000)
        payload = copy.deepcopy(steps[2]['payload'])
        payload.update(id='repaired', route_id='serving', reviewer_id='first')
        payload['context']['context_repair'].update(event_ids=[retired['id']], evidence=[evidence],
            probe_id='probe-serving', reason='Reassess the same slot after route retirement.')
        repaired = self.execute(case, steps[2]['command'], payload)['result']
        self.assertEqual(protocol.context_repair.slot_id(case.read_records(), repaired), 'bar-first')
        def respond(packet):
            if packet.get('review_parts', {}).get('kind') not in (None, 'final'):
                from review_parts_fixtures import make_output
                return make_output(packet)
            output = case.build_review_output('bar')
            output['reassessment'] = dict(assignment_id=head['id'], findings=[])
            return output
        result = self.drive(case, repaired['id'], respond)
        self.assertEqual(result, 0, plan_next(case.read_records(), case.artifacts, repaired['id']))
        self.assertTrue(case.read_records().get('value_review'))

    def test_problem_openness_moves_by_probe_and_new_export_to_recorded_finding(self):
        case = self.prepare(reading_fixtures.OpennessReadingCase)
        self.register_https(case)
        reference = case.export(assignment_id='old')
        self.execute(case, 'review-assignment', dict(id='old', route_id='https', role='native_math',
            dossier_id=None, reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=reference)))
        steps = self.stop_response(case, 'old')
        self.assertEqual([step['id'] for step in steps], [7, 4])
        record_codex_route(case, 'serving', output_tokens=128000)
        exported = case.export(name='new-openness', assignment_id='new')
        payload = dict(steps[1]['then'][0]['payload'], id='new', route_id='serving',
            reviewer_id='openness-reviewer', context=dict(native_packet=exported))
        self.execute(case, 'review-assignment', payload)
        result = self.drive(case, 'new', lambda packet: case.build_reading_response(packet)
                            if 'reading_turn' in packet else case.build_native_result(packet))
        self.assertEqual(result, 0)
        self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, 'new')['state'], 'done')

    def test_verification_moves_by_probe_and_first_assignment_to_recorded_verdict(self):
        fixture = self.prepare(study_fixtures.VerificationPartsTests)
        case = fixture.case
        self.register_https(case)
        assignment = fixture.assign(route_id='https')
        steps = self.stop_response(case, assignment['id'])
        self.assertEqual([step['id'] for step in steps], [7, 6])
        record_codex_route(case, 'serving', output_tokens=128000)
        following = self.execute(case, steps[1]['command'], dict(steps[1]['payload'], id='new',
            route_id='serving', reviewer_id=assignment['reviewer_id']))['result']
        output = dict(verdict=case.verdict(), checks=[dict(dimension=kind, reason='Checked the exact evidence.',
            evidence=fixture.references) for kind in ('soundness', 'novelty', 'impact')])
        self.drive(case, 'new', lambda packet: output)
        # The role's terminal handoff requires the owner's supplied verdict body.
        payload = case.build_bind_payload(fixture.task['digest'], case.verdict(), observed=False)
        payload['assessment'].update(assignment_id=following['id'], checks=output['checks'])
        bound = self.execute(case, 'bind-verdict', payload)
        self.assertIn('result', bound)
