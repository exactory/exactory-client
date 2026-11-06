"""Posted claims retain their evidence without inviting another review call."""

import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from problem_parts_fixtures import ResultPartsCase
from codex_route_fixtures import build_codex_response
import test_problem_post_terminal as terminal
import test_review_staged_frontier as frontier
import test_review_output_correction as corrections
import review_parts_fixtures as fresh_parts
import test_research_check_mode as checks
from research_harness import cli, problem_claims, problem_delivery, review_protocol as protocol
from research_harness import review_output_correction as correction, review_staged_protocol as producer
from research_harness import review_task_execution as execution, review_transport as transport, review_worker
from research_harness import output_views, review_continuation, review_parts
from research_harness import record_check, review_series
from research_harness.check_mode import check_mutation
from research_harness.errors import ResearchError
from research_harness.evidence import digest


PREPARE_PARTS_PACKET = review_parts.prepare_packet
ROOT = Path(__file__).resolve().parents[1]


def record_external_post(case, statement_id='statement-1'):
    statement = case.store.snapshot()['records']['problem_statement'][statement_id]
    return case.mutate(problem_claims.record_post_external, dict(
        id='external-post', problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
        statement_digest=problem_claims.compute_statement_digest(statement),
        reason='The owner authorized publication of this synthetic claim.',
        evidence='Synthetic publication instruction.'))['result']


class PostedReviewTests(ResultPartsCase):
    def make_case(self, case_type):
        case = case_type()
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def read_fingerprint(self, case):
        completed = subprocess.run([sys.executable, str(ROOT / 'ci/record_fingerprint.py'),
            '--harness', str(ROOT), '--workspace', str(case.root)],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=120)
        return json.loads(completed.stdout)

    def assignment_payload(self, role='result', **context):
        return dict(id='new-' + role, role=role, route_id='route', reviewer_id='first',
                    author_id='author', dossier_id=None, context=context)

    def assert_post_refusal(self, case, payload, *, operation=protocol.record_assignment,
                            check=False, statement_id='statement-1'):
        before = case.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            if check:
                check_mutation(case.store, operation, payload, expected_revision=case.store.revision,
                               request_id='refused-check', bounded_payload=payload)
            else:
                operation(case.store, payload, expected_revision=case.store.revision,
                          request_id='refused-request')
        self.assertEqual(caught.exception.code, 'problem_already_posted')
        self.assertEqual(caught.exception.details,
                         dict(post_id='external-post', statement_id=statement_id))
        self.assertEqual(case.store.snapshot(), before)

    def test_posted_subject_assignment_matrix(self):
        record_external_post(self.case)
        for role, context in (
                ('bar', {}), ('result', {}), ('adjudicator', {'review_ids': ['left', 'right']}),
                ('result', {'prior_assignment_id': 'bar-first'}),
                ('result', {'context_repair': dict(assignment_id='bar-first', event_ids=['exposure'],
                    probe_id='probe', reason='Repair the observed exposure.', evidence=[self.case.supporting])})):
            for check in (False, True):
                with self.subTest(role=role, context=context, check=check):
                    self.assert_post_refusal(self.case, self.assignment_payload(role, **context), check=check)

    def test_alias_is_posted_but_changed_statement_or_criteria_can_be_assigned(self):
        record_external_post(self.case)
        original = self.case.statement
        alias = dict(original, id='renamed', title='Renamed synthetic bound')
        self.case.put('problem_statement', alias['id'], alias)
        self.case.put('problem_selection', 'current', dict(statement_id=alias['id']))
        self.assert_post_refusal(self.case, self.assignment_payload(), statement_id='renamed')
        for key in ('statement', 'resolution_criteria'):
            changed = dict(original, id='changed-' + key, **{key: 'A different synthetic ' + key})
            self.case.put('problem_statement', changed['id'], changed)
            self.case.put('problem_selection', 'current', dict(statement_id=changed['id']))
            result = self.case.assign('result', 'first', identifier='assignment-' + key)
            self.assertEqual(result['subject']['statement_id'], changed['id'])

    def test_committed_assignment_replays_and_changed_payload_conflicts(self):
        payload = self.assignment_payload()
        revision = self.case.store.revision
        first = protocol.record_assignment(self.case.store, payload, expected_revision=revision,
                                           request_id='replayed-assignment')
        record_external_post(self.case)
        for check in (False, True):
            if check:
                replay = check_mutation(self.case.store, protocol.record_assignment, payload,
                    expected_revision=revision, request_id='replayed-assignment', bounded_payload=payload)
            else:
                replay = protocol.record_assignment(self.case.store, payload, expected_revision=revision,
                                                     request_id='replayed-assignment')
            self.assertEqual(replay, first)
            changed = dict(payload, id='different')
            with self.assertRaises(ResearchError) as caught:
                if check:
                    check_mutation(self.case.store, protocol.record_assignment, changed,
                        expected_revision=revision, request_id='replayed-assignment', bounded_payload=changed)
                else:
                    protocol.record_assignment(self.case.store, changed, expected_revision=revision,
                                               request_id='replayed-assignment')
            self.assertEqual(caught.exception.code, 'request_id_conflict')

    def test_posted_controller_frontier_and_child_refusal(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        task_id = controller['staged_review']['task_ids'][0]
        record_external_post(staged.case)
        changed = dict(staged.case.statement, id='different', statement='A different synthetic claim.')
        staged.case.put('problem_statement', changed['id'], changed)
        staged.case.put('problem_selection', 'current', dict(statement_id=changed['id']))
        child = {key: controller[key] for key in
                 ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        child.update(id='direct-child', context=dict(controller_assignment_id=controller['id'], task_id=task_id))
        for check in (False, True):
            for payload in (child, dict(controller_assignment_id=controller['id'])):
                with self.subTest(check=check, payload=payload):
                    self.assert_post_refusal(staged.case, payload, check=check)

    def prepare_unbound_staged(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        planned = staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        controller = dict(controller, subject=None)
        staged.case.put('review_assignment', controller['id'], controller)
        return staged, controller, planned

    def test_posted_unbound_controller_refuses_frontier_child_call_and_correction(self):
        staged, controller, planned = self.prepare_unbound_staged()
        record_external_post(staged.case)
        child = {key: controller[key] for key in
                 ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        child.update(id='closed-child', context=dict(controller_assignment_id=controller['id'],
            task_id=controller['staged_review']['task_ids'][0]))
        for payload in (child, dict(controller_assignment_id=controller['id'])):
            for check in (False, True):
                with self.subTest(payload=payload, check=check):
                    before = staged.case.store.snapshot()
                    with self.assertRaises(ResearchError) as caught:
                        if check:
                            check_mutation(staged.case.store, protocol.record_assignment, payload,
                                expected_revision=staged.case.store.revision, request_id='closed-child-check',
                                bounded_payload=payload)
                        else:
                            staged.case.mutate(protocol.record_assignment, payload)
                    self.assertEqual(caught.exception.code, 'problem_already_posted')
                    self.assertEqual(staged.case.store.snapshot(), before)
        for operation, payload in (
                (cli.OPERATIONS['review-run'], planned['next'][0]['payload']),
                (correction.invoke_correction, dict(id='closed-correction', assignment_id=controller['id'], route_id='route'))):
            with self.subTest(operation=operation.__name__), \
                    patch.object(transport, 'send_request', side_effect=AssertionError('No unbound post call')):
                before = staged.case.store.snapshot()
                with self.assertRaises(ResearchError) as caught:
                    staged.case.mutate(operation, payload)
                self.assertEqual(caught.exception.code, 'problem_already_posted')
                self.assertEqual(staged.case.store.snapshot(), before)

    def test_unbound_post_races_refuse_reservation_and_launch(self):
        for phase in ('reservation', 'launch'):
            with self.subTest(phase=phase):
                staged, controller, planned = self.prepare_unbound_staged()
                original = staged.case.store.mutate_scoped if phase == 'reservation' else review_worker._claim_launch
                posted = []

                def reserve(operation, *args, **kwargs):
                    if operation == 'review.task_reserve' and not posted:
                        posted.append(record_external_post(staged.case))
                    return original(operation, *args, **kwargs)

                def claim(store, reservation_id):
                    posted.append(record_external_post(staged.case))
                    return original(store, reservation_id)

                target, name, replacement = ((staged.case.store, 'mutate_scoped', reserve) if phase == 'reservation'
                                             else (review_worker, '_claim_launch', claim))
                with patch.object(target, name, side_effect=replacement), \
                        patch.object(review_worker.subprocess, 'Popen', side_effect=AssertionError('No closed launch')):
                    with self.assertRaises(ResearchError) as caught:
                        staged.command('review-run', planned['next'][0]['payload'])
                self.assertEqual(caught.exception.code, 'problem_already_posted')
                records = staged.case.read_records()
                if phase == 'reservation':
                    self.assertFalse(records.get('review_reservation'))
                    self.assertFalse(records.get('review_resource_ledger'))
                else:
                    reservation = next(iter(records['review_reservation'].values()))
                    self.assertIsNone(reservation['launch'])

    def test_child_of_unposted_controller_uses_frozen_subject_despite_posted_selection(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        changed = dict(staged.case.statement, id='different', statement='A different synthetic claim.')
        staged.case.put('problem_statement', changed['id'], changed)
        staged.case.put('problem_selection', 'current', dict(statement_id=changed['id']))
        record_external_post(staged.case, changed['id'])
        payload = {key: controller[key] for key in
                   ('role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id')}
        payload.update(id='direct-child', context=dict(controller_assignment_id=controller['id'],
            task_id=controller['staged_review']['task_ids'][0]))
        child = staged.case.mutate(protocol.record_assignment, payload)['result']
        self.assertEqual(child['subject'], controller['subject'])

    def test_posted_calls_stop_without_blocking_capture_recovery(self):
        assignment = self.assign('pending-result')
        payload = dict(id='captured-run', assignment_id=assignment['id'])
        revision = self.case.store.revision
        output = self.case.build_review_output('result')
        with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                          build_codex_response(route, request, output)), \
                patch.object(protocol, '_finish_call', side_effect=RuntimeError('Lost after capture')):
            with self.assertRaisesRegex(RuntimeError, 'Lost after capture'):
                protocol.invoke_assignment(self.case.store, payload, expected_revision=revision,
                                           request_id='captured-request')
        post = record_external_post(self.case)
        with patch.object(transport, 'send_request', side_effect=AssertionError('No provider call')), \
                patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No worker launch')):
            self.assert_post_refusal(self.case, dict(payload, id='fresh-run'), operation=protocol.invoke_assignment)
            execution.reconcile_call(self.case.store, dict(operation='review.invoke', invocation_id=payload['id']),
                                     request_id='reconcile-captured')
            captured = protocol.invoke_assignment(self.case.store, payload, expected_revision=revision,
                                                 request_id='captured-request')
            self.assertEqual(protocol.invoke_assignment(self.case.store, payload, expected_revision=revision,
                                                       request_id='captured-request'), captured)
        self.assertEqual(captured['result']['status'], 'completed')
        saved = self.case.mutate(terminal.research_decisions.record_value_review,
            dict(output, id='observed-after-post', dossier_id=None, assignment_id=assignment['id']))
        self.assertEqual(saved['result']['subject'], assignment['subject'])
        self.assertEqual(self.case.read_records()['problem_post'][post['id']], post)

    def test_fresh_staged_run_refuses_before_dispatch_reservation_or_launch(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        child = next(row for row in staged.case.read_records()['review_assignment'].values()
                     if row.get('staged_review', {}).get('kind') == 'task')
        record_external_post(staged.case)
        payload = dict(id='fresh-staged-call', assignment_id=child['id'])
        with patch.object(execution, 'reserve_task', side_effect=AssertionError('No reservation')), \
                patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No launch')), \
                patch.object(transport, 'send_request', side_effect=AssertionError('No provider call')):
            self.assert_post_refusal(staged.case, payload, operation=protocol.invoke_assignment)
            self.assert_post_refusal(staged.case, payload, operation=producer.invoke_task)
            self.assert_post_refusal(staged.case, dict(id='controller-call', assignment_id=controller['id']),
                                     operation=protocol.invoke_assignment)

    def test_posted_correction_refusal_preserves_history(self):
        case = self.make_case(corrections.CorrectionTests)
        case.prepare_refused()
        packet = correction.prepare_packet(case.fixture.read_records(), case.fixture.artifacts,
                                           case.case.assignment['id'])
        record_external_post(case.fixture)
        payload = dict(id='new-correction', assignment_id=case.case.assignment['id'], route_id='route')
        with patch.object(transport, 'send_request', side_effect=AssertionError('No correction call')):
            self.assert_post_refusal(case.fixture, payload, operation=correction.invoke_correction)
        self.assertEqual(correction.prepare_packet(case.fixture.read_records(), case.fixture.artifacts,
                                                  case.case.assignment['id']), packet)

    def test_unlaunched_reservation_cannot_launch_but_can_release_capacity(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        planned = staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        payload = planned['next'][0]['payload']
        with patch.object(producer, 'wait_for_capture', side_effect=RuntimeError('Before worker launch')):
            with self.assertRaisesRegex(RuntimeError, 'Before worker launch'):
                staged.command('review-run', payload)
        records = staged.case.read_records()
        reservation = next(iter(records['review_reservation'].values()))
        self.assertIsNone(reservation['launch'])
        record_external_post(staged.case)
        child = next(row for row in records['review_assignment'].values()
                     if row.get('staged_review', {}).get('kind') == 'task')
        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No launch')), \
                patch.object(transport, 'send_request', side_effect=AssertionError('No provider call')):
            self.assert_post_refusal(staged.case, dict(id='resume', assignment_id=child['id']),
                                     operation=protocol.invoke_assignment)
            with self.assertRaises(ResearchError) as caught:
                producer.wait_for_capture(staged.case.store, records['review_route']['route'], reservation, 'wait')
            self.assertEqual(caught.exception.code, 'problem_already_posted')
            released = execution.reconcile_worker(staged.case.store, dict(reservation_id=reservation['id']),
                                                  request_id='release-unlaunched')
        self.assertTrue(released['result']['released'])
        self.assertFalse(staged.case.read_records().get('review_observation'))

    def test_staged_capture_acceptance_after_post_needs_no_transport(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        planned = staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        payload = planned['next'][0]['payload']
        request_id = 'operator-' + str(staged.case.store.revision)
        revision = staged.case.store.revision
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]), \
                patch.object(execution, 'accept_observation', side_effect=RuntimeError('Captured before acceptance')):
            with self.assertRaisesRegex(RuntimeError, 'Captured before acceptance'):
                staged.command('review-run', payload)
        self.assertEqual(len(staged.case.read_records()['review_observation']), 1)
        record_external_post(staged.case)
        with patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No new launch')), \
                patch.object(transport, 'send_request', side_effect=AssertionError('No new provider call')):
            result = cli.OPERATIONS['review-run'](staged.case.store, payload, expected_revision=revision,
                                                request_id=request_id)
        self.assertEqual(result['result']['status'], 'completed')
        self.assertEqual(len(staged.case.read_records()['review_task_acceptance']), 1)
        self.assertEqual((staged.case.root / 'calls').read_text().splitlines(), ['frame'])
        child = next(row for row in staged.case.read_records()['review_assignment'].values()
                     if row.get('staged_review', {}).get('kind') == 'task')
        self.assert_post_refusal(staged.case, dict(id='different-new-call', assignment_id=child['id']),
                                 operation=producer.invoke_task)
        self.assert_post_refusal(staged.case, dict(payload, id='different-cli-call'), operation=cli.OPERATIONS['review-run'])

    def test_posted_status_and_series_views_have_no_review_actions(self):
        patcher = patch.object(review_parts, 'prepare_packet', PREPARE_PARTS_PACKET)
        patcher.start()
        self.addCleanup(patcher.stop)
        parts = self.make_case(fresh_parts.PartsResultCase)
        parts.oversized()
        for entry in parts.case.expected['openness_searches']:
            search = entry['search']
            search.update(profile='research', purpose='openness', cited_work_ids=[], found_work_ids=[], responses=[])
            parts.case.put('literature_search', search['id'], search)
        assignment = parts.assign('fresh-parts')
        parts.invoke(assignment)
        records = parts.case.read_records()
        before_state = protocol.assignment_state(records, parts.case.artifacts, assignment['id'])
        before = cli.status_report(parts.case.store)['review']['assignments'][assignment['id']]['review_parts']
        self.assertTrue(before['next'])
        record_external_post(parts.case)
        records = parts.case.read_records()
        after = cli.status_report(parts.case.store)['review']['assignments'][assignment['id']]['review_parts']
        self.assertEqual(after['next'], [])
        self.assertEqual({key: value for key, value in after.items() if key != 'next'},
                         {key: value for key, value in before.items() if key != 'next'})
        self.assertEqual(protocol.assignment_state(records, parts.case.artifacts, assignment['id']), before_state)
        files = output_views._build_series(records, parts.case.artifacts, output_views._ViewInputs({}, {}))
        series = [json.loads(view.content) for name, view in files.items() if name.endswith('series.json')]
        progress = [turn['review_parts'] for document in series for turn in document['turns']
                    if turn['assignment_id'] == assignment['id']]
        self.assertEqual(progress[0]['next'], [])
        actions = review_continuation.describe_next_actions(records, parts.case.artifacts, assignment['id'],
            [dict(kind='review', workflow='review-assignment', scope=dict(prior_assignment_id=assignment['id']))])
        self.assertEqual(actions, [dict(kind='review', state='done')])

    def test_staged_status_preserves_tasks_and_incomplete_recovery_evidence(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        before = cli.status_report(staged.case.store)['review']['assignments'][controller['id']]
        self.assertTrue(any(row['next'] for row in before['tasks']))
        record_external_post(staged.case)
        after = cli.status_report(staged.case.store)['review']['assignments'][controller['id']]
        self.assertTrue(all(not row.get('next') for row in after['tasks']))
        self.assertEqual([{key: value for key, value in row.items() if key != 'next'} for row in after['tasks']],
                         [{key: value for key, value in row.items() if key != 'next'} for row in before['tasks']])

    def test_incomplete_staged_outcome_keeps_recovery_work_and_scientific_flags(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        records = staged.case.read_records()
        epoch = records['review_epoch'][controller['staged_review']['epoch_id']]
        task = records['review_task'][controller['staged_review']['task_ids'][0]]['task']
        from research_harness.operations import prepared_task_mutation
        result = prepared_task_mutation(staged.case.store, 'fixture.execution_incomplete', {},
            lambda current, value: producer.prepare_execution_incomplete(current, staged.case.artifacts,
                epoch, task, 'exhausted', ['synthetic-planning-failure']), request_id='incomplete-outcome')['result']
        outcome = result['outcome']
        self.assertTrue(outcome['recovery_work'])
        records = staged.case.read_records()
        before_state = protocol.assignment_state(records, staged.case.artifacts, controller['id'])
        before = problem_delivery.assess_post(records, staged.case.artifacts)
        self.assertIn('assessment_incomplete', {row['code'] for row in before['obligations']})
        fingerprint = self.read_fingerprint(staged.case)
        record_external_post(staged.case)
        records = staged.case.read_records()
        report = cli.status_report(staged.case.store)
        shown = report['review']['assignments'][controller['id']]
        self.assertEqual(shown['incomplete_outcome'], outcome)
        self.assertTrue(all(not task['next'] for task in shown['tasks']))
        self.assertEqual(records['assessment_incomplete'][outcome['id']], outcome)
        self.assertEqual(protocol.assignment_state(records, staged.case.artifacts, controller['id']), before_state)
        self.assertEqual(report['after_post'], [dict(
            {key: value for key, value in row.items() if key != 'next'},
            reason=row.get('reason', row['explanation'])) for row in before['obligations']])
        self.assertEqual(self.read_fingerprint(staged.case), fingerprint)

    def test_completed_unrecorded_final_is_advisory_but_can_still_be_recorded(self):
        patcher = patch.object(review_parts, 'prepare_packet', PREPARE_PARTS_PACKET)
        patcher.start()
        self.addCleanup(patcher.stop)
        parts = self.make_case(fresh_parts.PartsResultCase)
        parts.oversized()
        _, assignment = parts.finish_series()
        output = parts.final_output()
        parts.invoke(assignment, output)
        before = problem_delivery.assess_post(parts.case.read_records(), parts.case.artifacts)
        pending = next(row for row in before['obligations'] if row['code'] == 'review_result_pending')
        self.assertTrue(pending['next'])
        record_external_post(parts.case)
        after = problem_delivery.assess_post(parts.case.read_records(), parts.case.artifacts)
        self.assertEqual(after['after_post'], [dict(
            {key: value for key, value in row.items() if key != 'next'},
            reason=row.get('reason', row['explanation'])) for row in before['obligations']])
        parts.case.mutate(terminal.research_decisions.record_value_review,
            dict(output, id='late-value-review', dossier_id=None, assignment_id=assignment['id']))
        self.assertTrue(protocol.assignment_state(parts.case.read_records(), parts.case.artifacts, assignment['id'])['ready'])

    def test_posted_series_and_record_check_stop_before_any_operation(self):
        assignment = self.assign('ordinary-head')
        record_external_post(self.case)
        before = self.case.store.snapshot()
        operations = {name: lambda *args, **kwargs: self.fail('No operation after done') for name in cli.OPERATIONS}
        with patch.dict(cli.OPERATIONS, operations):
            emitted = []
            self.assertEqual(review_series.run_series(self.case.store, assignment['id'], emit=emitted.append), 0)
            continuation = record_check.check_continuation(self.case.store)
        self.assertEqual(emitted[-1]['state'], 'done')
        self.assertTrue(continuation['chains'])
        self.assertTrue(all(chain['boundary'] == 'done' for chain in continuation['chains']))
        self.assertNotIn('problem_already_posted', record_check.EXPECTED_STOP_CODES)
        self.assertEqual(self.case.store.snapshot(), before)
        changed = dict(self.case.statement, id='second-statement', statement='A distinct synthetic bound.')
        self.case.put('problem_statement', changed['id'], changed)
        self.case.put('problem_selection', 'current', dict(statement_id=changed['id']))
        second = self.assign('second-head')
        self.assertEqual(review_continuation.plan_next(self.case.read_records(), self.case.artifacts, second['id'])['state'], 'run')

    def test_post_between_preparation_and_assignment_admission_invalidates_tracked_read(self):
        original = self.case.store.mutate_scoped
        posted = []
        def admit(operation, *args, **kwargs):
            if operation == 'review.assignment' and not posted:
                posted.append(record_external_post(self.case))
            return original(operation, *args, **kwargs)
        with patch.object(self.case.store, 'mutate_scoped', side_effect=admit):
            with self.assertRaises(ResearchError) as caught:
                protocol.record_assignment(self.case.store, self.assignment_payload(),
                    expected_revision=self.case.store.revision, request_id='racing-assignment')
        self.assertEqual(caught.exception.code, 'review_task_dependency_changed')
        self.assertNotIn('new-result', self.case.read_records()['review_assignment'])
        self.assert_post_refusal(self.case, self.assignment_payload())

    def test_post_between_preparation_and_call_admission_prevents_capacity_and_transport(self):
        assignment = self.assign('racing-head')
        original = self.case.store.mutate_scoped
        posted = []
        def admit(operation, *args, **kwargs):
            if operation == 'review.invoke_start' and not posted:
                posted.append(record_external_post(self.case))
            return original(operation, *args, **kwargs)
        with patch.object(self.case.store, 'mutate_scoped', side_effect=admit), \
                patch.object(transport, 'send_request', side_effect=AssertionError('No call admitted after post')):
            with self.assertRaises(ResearchError) as caught:
                protocol.invoke_assignment(self.case.store, dict(id='racing-call', assignment_id=assignment['id']),
                    expected_revision=self.case.store.revision, request_id='racing-call')
        self.assertEqual(caught.exception.code, 'review_task_dependency_changed')
        records = self.case.read_records()
        self.assertNotIn('racing-call', records.get('review_invocation', {}))
        self.assertNotIn(execution.identify_call('review.invoke', 'racing-call'), records.get('review_pool_membership', {}))

    def test_post_between_reservation_preparation_and_admission_prevents_charge(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        planned = staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        memberships = staged.case.read_records().get('review_pool_membership', {})
        original = staged.case.store.mutate_scoped
        posted = []
        def admit(operation, *args, **kwargs):
            if operation == 'review.task_reserve' and not posted:
                posted.append(record_external_post(staged.case))
            return original(operation, *args, **kwargs)
        with patch.object(staged.case.store, 'mutate_scoped', side_effect=admit), \
                patch.object(review_worker, 'launch_worker', side_effect=AssertionError('No worker after post')):
            with self.assertRaises(ResearchError) as caught:
                staged.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(caught.exception.code, 'problem_already_posted')
        records = staged.case.read_records()
        self.assertFalse(records.get('review_reservation'))
        self.assertEqual(records.get('review_pool_membership', {}), memberships)
        self.assertFalse(records.get('review_resource_ledger'))

    def test_post_between_launch_read_and_claim_prevents_worker_launch(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        planned = staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        original = review_worker._claim_launch
        def claim(store, reservation_id):
            record_external_post(staged.case)
            return original(store, reservation_id)
        with patch.object(review_worker, '_claim_launch', side_effect=claim), \
                patch.object(review_worker.subprocess, 'Popen', side_effect=AssertionError('No worker launch')):
            with self.assertRaises(ResearchError) as caught:
                staged.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(caught.exception.code, 'problem_already_posted')
        records = staged.case.read_records()
        reservation = next(iter(records['review_reservation'].values()))
        self.assertIsNone(reservation['launch'])
        self.assertFalse(records.get('review_observation'))
        execution.reconcile_worker(staged.case.store, dict(reservation_id=reservation['id']), request_id='release-raced-launch')

    def test_interrupted_dispatch_resumes_after_unrelated_write(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        planned = staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        payload = planned['next'][0]['payload']
        revision = staged.case.store.revision
        request_id = 'operator-' + str(revision)
        with patch.object(execution, 'reserve_task', side_effect=RuntimeError('Before reservation')):
            with self.assertRaisesRegex(RuntimeError, 'Before reservation'):
                staged.command('review-run', payload)
        staged.case.store.mutate('fixture.unrelated', {}, lambda tx: None,
                                expected_revision=staged.case.store.revision, request_id='unrelated-write')
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]):
            resumed = cli.OPERATIONS['review-run'](staged.case.store, payload,
                expected_revision=staged.case.store.revision, request_id=request_id)
        self.assertEqual(resumed['result']['status'], 'completed')
        self.assertEqual((staged.case.root / 'calls').read_text().splitlines(), ['frame'])

    def test_post_before_launch_authorization_stops_provider_and_allows_reconciliation(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        planned = staged.command('review-assignment', dict(controller_assignment_id=controller['id']))
        processes = []
        original = review_worker._authorize_launch
        def spawn(*args, **kwargs):
            process = review_worker._SYSTEM_POPEN(*args, **kwargs)
            processes.append(process)
            self.addCleanup(process.wait, timeout=30)
            self.addCleanup(process.kill)
            return process
        def authorize(store, reservation_id, identity):
            record_external_post(staged.case)
            return original(store, reservation_id, identity)
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', frontier.WORKER]), \
                patch.object(review_worker.subprocess, 'Popen', side_effect=spawn), \
                patch.object(review_worker, '_authorize_launch', side_effect=authorize):
            with self.assertRaises(ResearchError) as caught:
                staged.command('review-run', planned['next'][0]['payload'])
        self.assertEqual(caught.exception.code, 'problem_already_posted')
        self.assertEqual(len(processes), 1)
        processes[0].wait(timeout=30)
        self.assertFalse((staged.case.root / 'calls').exists())
        records = staged.case.read_records()
        reservation = next(iter(records['review_reservation'].values()))
        self.assertFalse(reservation['launch']['authorized'])
        released = execution.reconcile_worker(staged.case.store, dict(reservation_id=reservation['id']),
                                               request_id='release-unauthorized-worker')
        self.assertTrue(released['result']['released'])

    def test_post_preserves_authentication_reconstruction_and_fingerprint(self):
        assignment = self.assign('historical-result')
        self.invoke(assignment, self.case.build_review_output('result'))
        unposted = self.read_fingerprint(self.case)
        record_external_post(self.case)
        posted = self.read_fingerprint(self.case)
        self.assertEqual(posted, unposted)

    def test_historical_correction_replay_and_fingerprints_survive_post(self):
        case = self.make_case(corrections.CorrectionTests)
        case.prepare_refused()
        payload = dict(id='historical-correction', assignment_id=case.case.assignment['id'], route_id='route')
        revision = case.fixture.store.revision
        with patch.object(transport, 'send_request', return_value=case.fixture.build_provider_response(case.delta())):
            receipt = correction.invoke_correction(case.fixture.store, payload, expected_revision=revision,
                                                   request_id='historical-correction')
        before_state = protocol.assignment_state(case.fixture.read_records(), case.fixture.artifacts, payload['assignment_id'])
        fingerprint = self.read_fingerprint(case.fixture)
        record_external_post(case.fixture)
        with patch.object(transport, 'send_request', side_effect=AssertionError('No correction replay transport')):
            self.assertEqual(correction.invoke_correction(case.fixture.store, payload, expected_revision=revision,
                                                        request_id='historical-correction'), receipt)
        self.assertEqual(protocol.assignment_state(case.fixture.read_records(), case.fixture.artifacts,
                                                   payload['assignment_id']), before_state)
        self.assertEqual(self.read_fingerprint(case.fixture), fingerprint)

    def test_0733_assignment_committed_after_post_still_replays_and_authenticates(self):
        record_external_post(self.case)
        payload = self.assignment_payload()
        revision = self.case.store.revision
        # Reproduce 0.73.3's admission policy; request, packet and history
        # construction are unchanged. Cross-version qualification also runs
        # the actual released code, outside ordinary shallow-checkout tests.
        with patch.object(problem_delivery, 'require_unposted_review', return_value=None):
            receipt = protocol.record_assignment(self.case.store, payload, expected_revision=revision,
                                                 request_id='legacy-after-post')
        before = self.case.store.snapshot()
        fingerprint = self.read_fingerprint(self.case)
        for check in (False, True):
            if check:
                replay = check_mutation(self.case.store, protocol.record_assignment, payload,
                    expected_revision=revision, request_id='legacy-after-post', bounded_payload=payload)
            else:
                replay = protocol.record_assignment(self.case.store, payload, expected_revision=revision,
                                                     request_id='legacy-after-post')
            self.assertEqual(replay, receipt)
        self.assertEqual(self.case.store.snapshot(), before)
        with self.assertRaises(ResearchError) as caught:
            protocol.record_assignment(self.case.store, dict(payload, id='conflicting'),
                                       expected_revision=revision, request_id='legacy-after-post')
        self.assertEqual(caught.exception.code, 'request_id_conflict')
        assignment = before['records']['review_assignment'][payload['id']]
        protocol.build_assignment_request(before['records'], self.case.artifacts, assignment)
        self.assertEqual(self.read_fingerprint(self.case), fingerprint)

    def test_excluded_roles_and_null_subjects_do_not_inherit_current_statement(self):
        record_external_post(self.case)
        records = self.case.read_records()
        for role in ('readiness', 'verification', 'manuscript', 'standalone'):
            assignment = dict(role=role, subject=dict(kind='problem', statement_id='statement-1'))
            self.assertIsNone(problem_delivery.find_review_post(records, assignment))
            problem_delivery.require_unposted_review(records, assignment)
        for subject in (None, dict(kind='problem', statement_id=None)):
            assignment = dict(id='unbound', role='bar', subject=subject, context={}, recorded_revision=1)
            self.assertEqual(problem_delivery.find_review_post(records, assignment)['id'], 'external-post')
        assignment = dict(role='bar', subject=dict(kind='study', statement_id='statement-1'))
        self.assertIsNone(problem_delivery.find_review_post(records, assignment))
        study = dict(records, workspace={'study': dict(orchestration='ai_science')})
        self.assertIsNone(problem_delivery.find_review_post(study,
            dict(role='result', subject=dict(kind='problem', statement_id='statement-1'))))
        self.case.put('problem_selection', 'current', dict(statement_id=None))
        self.assertIsNone(protocol.build_problem_subject(self.case.read_records(), 'bar')['statement_id'])
        self.assert_code('review_slots_fixed', lambda: self.case.assign('bar', 'third', identifier='null-statement-bar'))

    def test_posted_frontier_check_changes_no_workspace_bytes(self):
        staged = self.make_case(frontier.FrontierTests)
        controller = staged.create_controller()
        record_external_post(staged.case)
        from types import SimpleNamespace
        reader = SimpleNamespace(root=staged.case.root)
        before = checks.CheckCommandMixin.read_workspace(reader)
        self.assert_post_refusal(staged.case, dict(controller_assignment_id=controller['id']), check=True)
        self.assertEqual(checks.CheckCommandMixin.read_workspace(reader), before)

    def test_new_retry_after_post_is_refused_and_committed_failed_run_replays(self):
        assignment = self.assign('retry-head')
        receipt = self.invoke(assignment, {}, attempt='refused-before-post')
        record_external_post(self.case)
        payload = dict(id='refused-before-post', assignment_id=assignment['id'])
        with patch.object(transport, 'send_request', side_effect=AssertionError('No retry call')):
            replay = protocol.invoke_assignment(self.case.store, payload,
                expected_revision=0, request_id='invoke-refused-before-post')
            self.assertEqual(replay, receipt)
            self.assert_post_refusal(self.case, dict(payload, id='new-retry'), operation=protocol.invoke_assignment)
            with self.assertRaises(ResearchError) as caught:
                protocol.invoke_assignment(self.case.store, dict(payload, id='changed'),
                    expected_revision=0, request_id='invoke-refused-before-post')
            self.assertEqual(caught.exception.code, 'request_id_conflict')

    def test_two_posted_part_slots_have_advisory_findings_only(self):
        for reviewer in ('first', 'second'):
            self.record_final(self.assign('original-' + reviewer, reviewer=reviewer),
                              self.case.build_review_output('result'))
        self.oversized(count=22, characters=50000, limit=110000)
        for entry in self.case.expected['openness_searches']:
            search = entry['search']
            search.update(profile='research', purpose='openness', cited_work_ids=[], found_work_ids=[], responses=[])
            self.case.put('literature_search', search['id'], search)
        for reviewer in ('first', 'second'):
            assignment = self.assign('long-' + reviewer, reviewer=reviewer)
            for turn in range(1, 21):
                self.invoke(assignment)
                assignment = self.assign('long-' + reviewer + '-' + str(turn + 1),
                                         assignment['id'], reviewer)
            self.assertEqual(self.packet(assignment)['problem_evidence_parts']['turn'], 21)
            self.invoke(assignment)
        records = self.case.read_records()
        findings = problem_delivery.assess_post(records, self.case.artifacts)['obligations']
        self.assertEqual(cli.status_report(self.case.store)['slot_outcomes']['occupied'], 2)
        self.assertTrue(any(row.get('next') for row in findings))
        scopes = [action['scope'] for row in findings for action in row.get('next', [])
                  if action.get('workflow') == 'review-assignment']
        self.assertEqual({scope['reviewer_id'] for scope in scopes}, {'first', 'second'})
        record_external_post(self.case)
        report = cli.status_report(self.case.store)
        self.assertEqual(report['after_post'], [dict(
            {key: value for key, value in row.items() if key != 'next'},
            reason=row.get('reason', row['explanation'])) for row in findings])
        self.assertEqual(report['next']['code'], 'problem_already_posted')
        for index, scope in enumerate(scopes):
            payload = {key: value for key, value in scope.items() if key != 'prior_assignment_id'}
            payload.update(id='post-hint-' + str(index), context={})
            if 'prior_assignment_id' in scope:
                payload['context']['prior_assignment_id'] = scope['prior_assignment_id']
            for check in (False, True):
                with self.subTest(scope=scope, check=check):
                    self.assert_post_refusal(self.case, payload, check=check)


class AdvisoryProjectionTests(checks.CheckCommandMixin, terminal.delivery.ProblemDeliveryCase):
    post_current_statement = terminal.PostedProblemTests.post_current_statement

    def test_passed_post_refuses_assignments_in_both_modes_without_workspace_writes(self):
        self.prepare_ready()
        post = self.post_current_statement()
        for role in ('bar', 'result', 'adjudicator'):
            payload = dict(id='after-passed-' + role, role=role, route_id='route', dossier_id=None,
                           reviewer_id='new-reviewer', author_id='problem-author', context={})
            before = self.read_workspace()
            code, _, error = self.invoke_command('review-assignment', payload)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(error)['error']['code'], 'problem_already_posted')
            self.assertEqual(json.loads(error)['error']['details'], dict(post_id=post['id'], statement_id=self.selected['id']))
            self.assertEqual(self.read_workspace(), before)

        for command in ('review-run', 'review-output-correction'):
            payload = dict(id='unsupported', assignment_id='result-first')
            if command == 'review-output-correction':
                payload['route_id'] = 'route'
            before = self.read_workspace()
            code, _, error = self.invoke_command(command, payload)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(error)['error']['code'], 'check_unsupported')
            self.assertEqual(self.read_workspace(), before)

    def test_context_events_remain_recordable_but_repair_cannot_reopen_post(self):
        self.prepare_ready()
        post = self.post_current_statement()
        self.mutate(protocol.record_context_event, dict(id='exposure-after-post', assignment_id='result-first',
            kind='contamination', source='author_history', reason='Synthetic retained exposure evidence.',
            evidence=[self.supporting]))
        context = dict(context_repair=dict(assignment_id='result-first', event_ids=['exposure-after-post'],
            probe_id='probe', reason='A fresh assessment of the exposed subject.', evidence=[self.supporting]))
        payload = dict(id='repair-after-post', role='result', route_id='route', dossier_id=None,
                       reviewer_id='repair-reviewer', author_id='problem-author', context=context)
        self.assert_refusal_parity('review-assignment', payload, 'problem_already_posted')
        self.assertIn('exposure-after-post', self.store.snapshot()['records']['review_context_event'])
        self.assertEqual(self.store.snapshot()['records']['problem_post'][post['id']], post)

    def test_focused_adjudication_cannot_reopen_posted_results(self):
        case = terminal.PostedProblemTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_reviews(result_reviewer='first')
        record_external_post(case)
        before = case.store.snapshot()
        with patch.object(transport, 'send_request', side_effect=AssertionError('No adjudicator call')):
            with self.assertRaises(ResearchError) as caught:
                case.observe_adjudication()
        self.assertEqual(caught.exception.code, 'problem_already_posted')
        self.assertEqual(case.store.snapshot(), before)

    def test_observed_adjudication_can_be_recorded_after_post_without_transport(self):
        case = terminal.PostedProblemTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_reviews(result_reviewer='first')
        with patch.object(protocol, 'record_adjudication', side_effect=RuntimeError('Before adjudication recording')):
            with self.assertRaisesRegex(RuntimeError, 'Before adjudication recording'):
                case.observe_adjudication()
        state = protocol.assignment_state(case.store.snapshot()['records'], case.artifacts, 'focused')
        self.assertTrue(state['ready'])
        post = record_external_post(case)
        with patch.object(transport, 'send_request', side_effect=AssertionError('No adjudication transport')):
            recorded = case.mutate(protocol.record_adjudication,
                dict(state['output'], id='late-finding', assignment_id='focused'))['result']
        self.assertEqual(recorded['disposition'], state['output']['disposition'])
        self.assertEqual(case.store.snapshot()['records']['problem_post'][post['id']], post)

    def test_regression_ledger_covers_all_posted_review_boundaries(self):
        ledger = json.loads((ROOT / 'tests/incidents/ledger.json').read_text())
        entries = {row['id']: row for row in ledger['incidents']}
        for identifier in ('posted-advisories-retain-review-actions', 'posted-statement-accepts-review-assignment',
                           'posted-statement-accepts-review-call', 'posted-controller-plans-new-frontier',
                           'posted-statement-accepts-output-correction', 'posted-review-views-invite-continuation'):
            self.assertTrue(identifier in entries, identifier)
            self.assertEqual(entries[identifier]['fixed_in'], '0.73.6')
            self.assertEqual(entries[identifier]['test_kind'], 'real_shape')

    def test_projection_preserves_all_other_fields_order_count_and_source(self):
        self.prepare_ready()
        self.post_current_statement()
        # Distinct source rows can become equal after the projection. Both stay.
        findings = [dict(code=code, explanation='Synthetic finding.', details={'index': index},
                         next=[dict(workflow='review-assignment', scope={'turn': index})])
                    for index, code in enumerate((
                        'problem_evidence_part_pending', 'review_parts_pending', 'review_result_pending',
                        'assessment_incomplete', 'review_task_pending', 'problem_assurance_pending',
                        'research_objection_unresolved', 'future_hint'))]
        findings += [dict(code='same', explanation='Retained twice.', next=[{'turn': turn}])
                     for turn in (1, 2)]
        findings[0]['reason'] = 'Retain the existing reason.'
        original = copy.deepcopy(findings)
        records = self.store.snapshot()['records']
        baseline = problem_delivery.assess_post(records, self.artifacts)
        with patch.object(problem_delivery, 'assess_source_requests', return_value=findings):
            report = problem_delivery.assess_post(records, self.artifacts)
        self.assertEqual(findings, original)
        self.assertEqual(report['after_post'], [dict(
            {key: value for key, value in row.items() if key != 'next'},
            reason=row.get('reason', row['explanation'])) for row in findings])
        self.assertEqual(len(report['after_post']), len(findings))
        for key in ('ready', 'obligations', 'digest'):
            self.assertEqual(report[key], baseline[key])
        self.assertEqual(report['digest'], digest(dict(
            problem_evidence_digest=report['problem_evidence_digest'],
            decision=records['problem_decision'][report['decision_id']],
            obligations=report['obligations'])))
