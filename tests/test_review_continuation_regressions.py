"""Lifecycle regressions found by the independent continuation review."""

import io
import json
from contextlib import redirect_stdout
import threading
import time
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

    def assert_exhausted_slot_stops(self, case, identifier):
        before = case.store.snapshot()
        plan = plan_next(before['records'], case.artifacts, identifier)
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_slot_exhausted'))
        self.assertEqual(plan['details'], dict(assignment_id=identifier, refused_attempts=[
            dict(attempt_id='refused-1', code='review_evidence_undelivered'),
            dict(attempt_id='refused-2', code='review_evidence_undelivered')]))
        self.assertNotIn('command', plan)
        self.assertNotIn('payload', plan)
        self.assertEqual(case.store.snapshot(), before)
        return plan

    def test_two_bar_continuation_refusals_stop_the_fixed_slot(self):
        fixture = self.fixture()
        case = fixture.case
        case.assign('bar', 'second')
        output = case.build_review_output('bar')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        fixture.invoke(output)
        fixture.execute(fixture.plan(), request='continue-bar')
        identifier = 'bar-first-t2'
        output = case.build_review_output('bar')
        output['value']['evidence'] = [case.artifacts.put(b'Undelivered evidence.', 'text/plain')]
        for attempt in ('refused-1', 'refused-2'):
            with patch('research_harness.review_transport.send_request',
                       return_value=case.build_provider_response(output)):
                case.mutate(protocol.invoke_assignment, dict(id=attempt, assignment_id=identifier),
                            credential='fixture')
            with self.assertRaises(ResearchError) as caught:
                case.mutate(cli.OPERATIONS['value-review'], dict(
                    output, id='finding', dossier_id=None, assignment_id=identifier))
            self.assertEqual(caught.exception.code, 'review_evidence_undelivered')
        before = case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            case.assign('bar', 'third', identifier='replacement')
        self.assertEqual(caught.exception.code, 'review_slots_fixed')
        with self.assertRaises(ResearchError) as caught:
            case.assign('bar', 'third', identifier='replacement', prior_assignment_id='bar-first')
        self.assertEqual(caught.exception.code, 'review_source_continuation_mismatch')
        self.assertEqual(case.store.snapshot(), before)
        self.assert_exhausted_slot_stops(case, identifier)

    def test_two_slate_refusals_stop_the_fixed_slot(self):
        from test_research_problem_adjudication_resolution import ReviewOutputRefusalTests

        case = self.fixture(ReviewOutputRefusalTests)
        # This fixture also proves that a fresh reviewer is refused with review_bar_continuity.
        case.exhaust_later_role('slate')
        self.assert_exhausted_slot_stops(case, 'slate-first')

    def test_two_problem_result_refusals_stop_the_fixed_slot(self):
        from test_research_problem_adjudication_resolution import ReviewOutputRefusalTests

        case = self.fixture(ReviewOutputRefusalTests)
        case.exhaust_later_role('result')
        self.assert_exhausted_slot_stops(case, 'result-first')

    def test_two_adjudicator_refusals_stop_the_fixed_slot(self):
        from test_research_problem_adjudication_resolution import ReviewOutputRefusalTests

        case = self.fixture(ReviewOutputRefusalTests)
        case.prepare_reviews(result_reviewer='first')
        context = dict(review_ids=['review-result-first', 'review-result-second'],
                       objection_id='unsupported-implication')
        case.assign('adjudicator', 'third', identifier='focused', **context)
        output = dict(objection_id=context['objection_id'], disposition='not_upheld',
            reason='Exact evidence resolves it.', correction_admissible=None,
            evidence=[case.artifacts.put(b'Undelivered evidence.', 'text/plain')])
        for attempt in ('refused-1', 'refused-2'):
            error = case.refuse(output, 'focused', attempt, command='review-adjudication')
            self.assertEqual(error.code, 'review_evidence_undelivered')
        before = case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            case.assign('adjudicator', 'fourth', identifier='replacement', **context)
        self.assertEqual(caught.exception.code, 'review_adjudicator_fixed')
        self.assertEqual(case.store.snapshot(), before)
        self.assert_exhausted_slot_stops(case, 'focused')

    def test_two_verification_refusals_keep_the_assignment_handoff(self):
        from test_review_parts_study import VerificationPartsTests
        from review_parts_fixtures import make_output

        case = self.fixture(VerificationPartsTests)
        case.enlarge_verification()
        case.install_packet()
        assignment = case.assign()
        output = make_output(case.packet(assignment))
        output['cards'] = []
        for attempt in ('refused-1', 'refused-2'):
            result = case.invoke(assignment, output, attempt)
            self.assertEqual(result['output_refusal']['code'], 'invalid_value_review')
        before = case.case.store.snapshot()
        plan = plan_next(before['records'], case.case.artifacts, assignment['id'])
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         ('handoff', 'review_slot_exhausted', 'review-assignment'))
        self.assertEqual(plan['details'], dict(assignment_id=assignment['id'], refused_attempts=[
            dict(attempt_id='refused-1', code='invalid_value_review'),
            dict(attempt_id='refused-2', code='invalid_value_review')]))
        self.assertNotIn('payload', plan)
        self.assertEqual(case.case.store.snapshot(), before)

    def test_series_ends_with_the_exhausted_fixed_slot_stop(self):
        from test_research_problem_adjudication_resolution import ReviewOutputRefusalTests

        case = self.fixture(ReviewOutputRefusalTests)
        case.exhaust_later_role('result')
        output = io.StringIO()
        before = case.store.snapshot()
        plan = self.assert_exhausted_slot_stops(case, 'result-first')
        with patch('research_harness.review_transport.send_request',
                   side_effect=AssertionError('An exhausted slot must not call a reviewer')), \
                redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            cli.main(['review-series', '--workspace', str(case.root), '--assignment', 'result-first'],
                     render_views=lambda workspace: None)
        self.assertEqual(exited.exception.code, 3)
        after = case.store.snapshot()
        recorded_stop, = after['records']['review_series_stop'].values()
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]),
                         dict(plan, stop_id=recorded_stop['id'], stop_class='decision',
                              revision_after_stop=after['revision']))
        self.assertEqual(recorded_stop['plan'], plan)
        self.assertEqual(recorded_stop['recorded_revision'], after['revision'])
        operations = dict(before['records']['literature_operation'])
        operations[recorded_stop['request_id']] = dict(operation='review.series_stop',
                                                      runtime=case.store.describe_runtime())
        self.assertEqual(after, dict(before, revision=before['revision'] + 1,
            records=dict(before['records'], review_series_stop={recorded_stop['id']: recorded_stop},
                         literature_operation=operations)))

    def test_two_reassessment_mismatches_hand_off_eligible_correction(self):
        from research_harness import review_output_correction
        from test_review_output_correction import CorrectionTests

        case = self.fixture(CorrectionTests)
        case.prepare_refused()
        before = case.fixture.store.snapshot()
        identifier = case.case.assignment['id']
        packet = review_output_correction.prepare_packet(
            before['records'], case.fixture.artifacts, identifier)
        self.assertEqual(packet['diagnostic']['code'], 'review_reassessment_mismatch')
        plan = plan_next(before['records'], case.fixture.artifacts, identifier)
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         ('handoff', 'review_slot_exhausted', 'review-output-correction'))
        self.assertEqual(plan['details'], dict(assignment_id='repair-result', refused_attempts=[
            dict(attempt_id='refused-one', code='review_reassessment_mismatch'),
            dict(attempt_id='refused-two', code='review_reassessment_mismatch')]))
        self.assertNotIn('payload', plan)
        self.assertEqual(case.fixture.store.snapshot(), before)

    def test_two_openness_refusals_hand_off_a_new_export(self):
        from openness_reading_fixtures import OpennessReadingCase

        case = self.fixture(OpennessReadingCase)
        case.limit_input()
        reference = case.export(assignment_id='openness-first')
        case.assign('openness-first', reference)
        packet = case.read_packet('openness-first')
        output = dict(case.build_reading_response(packet), notes='')
        for attempt in ('refused-one', 'refused-two'):
            result = case.invoke('openness-first', output, attempt=attempt)
            self.assertEqual(result['output_refusal']['code'], 'invalid_reading_response')
        before = case.store.snapshot()
        plan = plan_next(before['records'], case.artifacts, 'openness-first')
        self.assertEqual((plan['state'], plan['code'], plan['command']),
                         ('handoff', 'review_slot_exhausted', 'export'))
        self.assertEqual(plan['details'], dict(assignment_id='openness-first', kind='problem-openness',
            refused_attempts=[dict(attempt_id='refused-one', code='invalid_reading_response'),
                              dict(attempt_id='refused-two', code='invalid_reading_response')]))
        self.assertNotIn('payload', plan)
        self.assertEqual(case.store.snapshot(), before)

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

    def test_wait_actions_do_not_change_gate_or_status_digests(self):
        from research_harness.gates import gate_report
        from review_parts_fixtures import PartsResultCase

        fixture = self.fixture(PartsResultCase)
        fixture.oversized()
        for row in fixture.case.expected['openness_searches']:
            row['search'].update(profile='research', purpose='openness',
                                 found_work_ids=[], cited_work_ids=[], responses=[])
            fixture.case.put('literature_search', row['search']['id'], row['search'])
        assignment = fixture.assign()
        with patch('research_harness.review_protocol._observed', side_effect=RuntimeError('killed')):
            with self.assertRaises(RuntimeError):
                fixture.invoke(assignment)
        before = fixture.case.store.snapshot()
        args = cli.build_parser().parse_args([
            'gate', 'post', '--workspace', str(fixture.case.root)])
        with patch('research_harness.cli.require_ready', side_effect=lambda report, action: report):
            first = cli.run(args)
            first_status = cli.status_report(fixture.case.store)
            time.sleep(1.05)
            second = cli.run(args)
            second_status = cli.status_report(fixture.case.store)
            with patch('research_harness.review_continuation.annotate_review_obligations',
                       side_effect=lambda records, artifacts, report: report):
                baseline = cli.run(args)
                baseline_status = cli.status_report(fixture.case.store)
        self.assertEqual(first['digest'], second['digest'])
        self.assertEqual(first_status['digest'], second_status['digest'])
        self.assertEqual(first['digest'], baseline['digest'])
        self.assertEqual(first_status['digest'], baseline_status['digest'])
        self.assertEqual(first['digest'], gate_report(fixture.case.store, 'post')['digest'])
        for report in (first, second, first_status, second_status):
            row = next(row for row in report['obligations']
                       if row['code'] == 'review_parts_pending')
            action = row['next'][0]
            self.assertEqual(action['state'], 'wait')
            self.assertEqual(action['command'], 'review-run')
            self.assertEqual(action['payload'], dict(action='reconcile', operation='review.invoke',
                invocation_id='attempt-' + assignment['id']))
        first_wait = next(row for row in first['obligations'] if row['code'] == 'review_parts_pending')
        second_wait = next(row for row in second['obligations'] if row['code'] == 'review_parts_pending')
        self.assertNotEqual(first_wait['next'][0]['not_before'], second_wait['next'][0]['not_before'])
        self.assertEqual(fixture.case.store.snapshot(), before)

    def test_blocked_gate_exposes_the_executable_source_action(self):
        fixture = self.fixture()
        output = fixture.case.build_review_output('bar')
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        fixture.invoke(output)
        args = cli.build_parser().parse_args([
            'gate', 'post', '--workspace', str(fixture.case.root)])
        with self.assertRaises(ResearchError) as raised:
            cli.run(args)
        self.assertEqual(raised.exception.code, 'readiness_required')
        row = next(row for row in raised.exception.details['obligations']
                   if row['code'] == 'review_sources_pending')
        self.assertEqual(row['next'][0]['command'], 'review-assignment')
        self.assertEqual(row['next'][0]['payload'], dict(id='bar-first-t2', route_id='route',
            dossier_id=None, role='bar', reviewer_id='first', author_id='author',
            context=dict(prior_assignment_id='bar-first')))

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

    def test_status_lists_failed_openness_but_not_controller_native_heads(self):
        from openness_reading_fixtures import OpennessReadingCase
        case = self.fixture(OpennessReadingCase)
        reference = case.export(assignment_id='openness-failed')
        case.assign('openness-failed', reference)
        native = json.loads(case.artifacts.read(reference))
        native['contract'].pop('orchestration')
        native['scientific_context'].pop('openness_minimum')
        native['reviewer'] = dict(source='host', actor_id='math-reviewer',
                                 attestation_id='review-assignment:math-failed')
        reference = case.artifacts.put(json.dumps(native).encode(), 'application/json')
        case.mutate(protocol.record_assignment, dict(id='math-failed', route_id='route',
            dossier_id=None, role='native_math', reviewer_id='math-reviewer', author_id='problem-author',
            context=dict(native_packet=reference)))
        for identifier in ('math-failed', 'openness-failed'):
            with patch('research_harness.review_transport.send_request', side_effect=OSError('failure')):
                protocol.invoke_assignment(case.store,
                    dict(id=identifier + '-run-1', assignment_id=identifier),
                    expected_revision=case.store.revision, request_id=identifier + '-call',
                    credential='fixture')
        with patch('research_harness.review_failure_class.plan_after_failure',
                   return_value=dict(state='run', code='fixture_failure', details={})):
            report = cli.status_report(case.store)
            records = case.store.snapshot()['records']
            self.assertEqual(list_chain_heads(records, case.artifacts),
                             ['math-failed', 'openness-failed'])
            for identifier in ('math-failed', 'openness-failed'):
                plan = plan_next(records, case.artifacts, identifier)
                self.assertEqual(plan['state'], 'run')
                self.assertEqual(plan['payload'], dict(id=identifier + '-run-2',
                                                      assignment_id=identifier))
        identifiers = {row.get('assignment_id') for row in report['obligations']}
        self.assertIn('openness-failed', identifiers)
        self.assertNotIn('math-failed', identifiers)
        packet_path = case.root / records['review_assignment']['math-failed']['packet']['path']
        original = packet_path.read_bytes()
        for corruption in ('missing', 'corrupt'):
            with self.subTest(packet=corruption):
                try:
                    if corruption == 'missing':
                        packet_path.unlink()
                    else:
                        packet_path.write_bytes(b'corrupt packet')
                    with patch('research_harness.review_failure_class.plan_after_failure',
                               return_value=dict(state='run', code='fixture_failure', details={})):
                        report = cli.status_report(case.store)
                    identifiers = {row.get('assignment_id') for row in report['obligations']}
                    self.assertIn('openness-failed', identifiers)
                    self.assertNotIn('math-failed', identifiers)
                finally:
                    packet_path.write_bytes(original)

    def test_a_recorded_problem_post_finishes_its_statement_chain(self):
        fixture = self.fixture()
        records = fixture.case.read_records()
        from research_harness.problem_claims import compute_statement_digest
        records['problem_post'] = dict(post=dict(id='post', gate='bypassed',
            statement_digest=compute_statement_digest(fixture.case.statement)))
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
