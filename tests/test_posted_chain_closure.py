"""Posted publication chains close by verified binding or authenticated chronology."""

import copy
import json
import unittest
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import cli, native_review, problem_claims, problem_delivery
from research_harness import record_check, review_continuation, review_output_correction
from research_harness import review_protocol as protocol
from research_harness import review_task_execution
from codex_route_fixtures import build_codex_response
from research_harness.check_mode import check_mutation
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class ClosureCase(OpennessReadingCase):
    def setUp(self):
        super().setUp()
        self.reference = self.export(assignment_id='openness-head')
        self.assignment = self.assign('openness-head', self.reference)

    def post(self, *, legacy=False):
        statement = problem_claims.find_current_statement(self.store.snapshot()['records'])
        payload = dict(id='post', problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
            statement_digest=problem_claims.compute_statement_digest(statement),
            reason='The owner published the exact synthetic claim.', evidence='Synthetic server receipt.')
        if legacy:
            record = dict(payload, gate='passed', statement_id=statement['id'],
                          posted_at='2026-11-05T02:53:34.915Z', export_digest='synthetic-export')
            return self.store.mutate('problem.posted', payload,
                lambda transaction: (transaction.put('problem_post', 'post', record), record)[1],
                expected_revision=self.store.revision, request_id='legacy-post')['result']
        return self.mutate(problem_claims.record_post_external, payload)['result']

    def assert_refused(self, operation, payload, *, request='refused'):
        before = self.store.snapshot()
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Closed before transport')):
            with self.assertRaises(ResearchError) as caught:
                operation(self.store, payload, expected_revision=self.store.revision, request_id=request)
        self.assertEqual(caught.exception.code, 'problem_already_posted')
        self.assertEqual(caught.exception.details['post_id'], 'post')
        self.assertEqual(self.store.snapshot(), before)


class PostedOpennessClosureTests(ClosureCase):
    def test_unknotting_five_failed_attempts_close_before_sixth_call_or_continuation(self):
        for number in range(5):
            with patch.object(protocol.transport, 'send_request', side_effect=OSError('Synthetic failed call')):
                self.mutate(protocol.invoke_assignment,
                    dict(id='failed-' + str(number), assignment_id=self.assignment['id']), credential='fixture')
        self.post(legacy=True)
        records = self.store.snapshot()['records']
        plan = review_continuation.plan_next(records, self.artifacts, self.assignment['id'])
        self.assertEqual(plan['state'], 'done')
        self.assertEqual(len(plan['progress']['attempts']), 5)
        self.assertNotIn('command', plan)
        self.assertNotIn('payload', plan)
        self.assert_refused(protocol.invoke_assignment, dict(id='sixth', assignment_id=self.assignment['id']))
        self.assert_refused(protocol.record_assignment, dict(id='continuation', role='native_math', route_id='route',
            dossier_id=None, reviewer_id='openness-reviewer', author_id='problem-author',
            context=dict(native_packet=self.reference, prior_assignment_id=self.assignment['id'])))

    def test_sidorenko_oversized_posted_openness_has_no_prospective_preflight(self):
        self.post()
        request = protocol.build_assignment_request(self.store.snapshot()['records'], self.artifacts, self.assignment)
        self.assertGreater(len(json.dumps(request)), 1000)
        self.limit_input(1000)
        before = self.store.snapshot()
        with patch.object(record_check, 'check_prospective_request',
                          side_effect=AssertionError('Closed input never reaches preflight')), \
                patch.dict(cli.OPERATIONS, {name: lambda *args, **kwargs: self.fail('No closed-chain mutation')
                                           for name in record_check.LOCAL_OPERATIONS}):
            report = record_check.check_continuation(self.store)
        self.assertEqual([chain['boundary'] for chain in report['chains']], ['done'])
        self.assertEqual(self.store.snapshot(), before)
        self.assertNotIn('problem_already_posted', record_check.EXPECTED_STOP_CODES)
        self.assertNotIn('review_input_too_large', record_check.EXPECTED_STOP_CODES)

    def test_kmp_retired_route_and_digest_alias_show_no_retry_or_repair_hint(self):
        alias = self.select_statement(identifier='alias', title='A renamed synthetic problem',
                                      statement=self.selected['statement'],
                                      resolution_criteria=self.selected['resolution_criteria'])
        self.assertNotEqual(alias['id'], self.selected['id'])
        self.post()
        self.put('review_route_retirement', 'retired', dict(id='retired', route_id='route', reason='Fixture retirement'))
        records = self.store.snapshot()['records']
        hints = review_continuation.describe_next_actions(records, self.artifacts, self.assignment['id'],
            [dict(kind='review', workflow='review-assignment', scope=dict(assignment_id=self.assignment['id']))])
        self.assertEqual(hints, [dict(kind='review', state='done')])
        self.assert_refused(protocol.invoke_assignment, dict(id='retry-retired', assignment_id=self.assignment['id']))
        self.assert_refused(review_output_correction.invoke_correction,
                            dict(id='correction', assignment_id=self.assignment['id'], route_id='route'))

    def test_new_root_for_posted_openness_claim_is_refused_in_both_modes(self):
        self.post()
        reference = self.export(assignment_id='new-root', name='new-root-packet')
        payload = dict(id='new-root', role='native_math', route_id='route', dossier_id=None,
                       reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=reference))
        self.assert_refused(protocol.record_assignment, payload)
        before = self.store.snapshot()
        with self.assertRaises(ResearchError) as caught:
            check_mutation(self.store, protocol.record_assignment, payload,
                expected_revision=self.store.revision, request_id='checked-new-root', bounded_payload=payload)
        self.assertEqual(caught.exception.code, 'problem_already_posted')
        self.assertEqual(self.store.snapshot(), before)

    def test_native_publication_race_invalidates_assignment_and_call_admission(self):
        for operation_name in ('review.assignment', 'review.invoke_start'):
            with self.subTest(operation=operation_name):
                case = ClosureCase()
                case.setUp()
                self.addCleanup(case.doCleanups)
                original = case.store.mutate_scoped
                posts = []

                def commit(operation, *args, **kwargs):
                    if operation == operation_name and not posts:
                        posts.append(case.post())
                    return original(operation, *args, **kwargs)

                if operation_name == 'review.assignment':
                    reference = case.export(assignment_id='racing-root', name='racing-root-packet')
                    payload = dict(id='racing-root', role='native_math', route_id='route', dossier_id=None,
                        reviewer_id='openness-reviewer', author_id='problem-author', context=dict(native_packet=reference))
                    operation = protocol.record_assignment
                else:
                    payload = dict(id='racing-call', assignment_id=case.assignment['id'])
                    operation = protocol.invoke_assignment
                with patch.object(case.store, 'mutate_scoped', side_effect=commit), \
                        patch.object(protocol.transport, 'send_request', side_effect=AssertionError('No racing call')):
                    with self.assertRaises(ResearchError) as caught:
                        operation(case.store, payload, expected_revision=case.store.revision, request_id='race')
                self.assertEqual(caught.exception.code, 'review_task_dependency_changed')
                records = case.store.snapshot()['records']
                self.assertNotIn('racing-root', records['review_assignment'])
                self.assertNotIn('racing-call', records.get('review_invocation', {}))
                self.assertNotIn(review_task_execution.identify_call('review.invoke', 'racing-call'),
                                 records.get('review_pool_membership', {}))

    def test_captured_openness_result_replays_after_post_without_new_call(self):
        output = self.build_native_result(self.read_packet(self.assignment['id']))
        payload = dict(id='captured', assignment_id=self.assignment['id'])
        revision = self.store.revision
        with patch.object(protocol.transport, 'send_request',
                side_effect=lambda route, request, credential: build_codex_response(route, request, output)), \
                patch.object(protocol, '_finish_call', side_effect=RuntimeError('Interrupted after capture')):
            with self.assertRaisesRegex(RuntimeError, 'Interrupted after capture'):
                protocol.invoke_assignment(self.store, payload, expected_revision=revision, request_id='capture')
        self.post()
        with patch.object(protocol.transport, 'send_request', side_effect=AssertionError('Replay has captured bytes')):
            receipt = protocol.invoke_assignment(self.store, payload, expected_revision=revision, request_id='capture')
            self.assertEqual(receipt['result']['status'], 'completed')
            self.assertEqual(protocol.invoke_assignment(self.store, payload, expected_revision=revision,
                                                        request_id='capture'), receipt)
        records = self.store.snapshot()['records']
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, self.assignment,
                                                      records['review_attempt']['captured']))

    def test_missing_or_corrupt_native_binding_is_a_validation_error(self):
        self.post()
        for corruption in ('missing-reference', 'missing-bytes', 'bad-subject-digest', 'bad-claim-digest'):
            with self.subTest(corruption=corruption):
                records = self.store.snapshot()['records']
                assignment = records['review_assignment'][self.assignment['id']]
                if corruption == 'missing-reference':
                    assignment['context'].pop('native_packet')
                elif corruption == 'missing-bytes':
                    assignment['context']['native_packet'] = dict(self.reference, sha256='f' * 64,
                                                                  path='research/sources/objects/missing')
                else:
                    packet = json.loads(self.artifacts.read(self.reference))
                    field = 'claim_digest' if corruption == 'bad-claim-digest' else 'subject_digest'
                    packet[field] = '0' * 64
                    assignment['context']['native_packet'] = self.artifacts.put(json.dumps(packet).encode(),
                                                                               'application/json')
                with self.assertRaises(ResearchError) as caught:
                    problem_delivery.find_review_post(records, assignment, self.artifacts)
                plan = review_continuation.plan_next(records, self.artifacts, assignment['id'])
                self.assertEqual(plan['state'], 'stop')
                self.assertEqual(plan['code'], caught.exception.code)
                self.assertNotEqual(plan.get('code'), 'problem_already_posted')
                self.assertNotIn('command', plan)

    def test_independent_native_solver_packet_remains_eligible_after_post(self):
        packet = json.loads(self.artifacts.read(self.reference))
        packet['contract'].pop('orchestration')
        packet['scientific_context'].pop('openness_minimum')
        reference = self.artifacts.put(json.dumps(packet).encode(), 'application/json')
        records = self.store.snapshot()['records']
        records['review_assignment'][self.assignment['id']]['context']['native_packet'] = reference
        before = review_continuation.plan_next(records, self.artifacts, self.assignment['id'])
        self.assertEqual(before['state'], 'run')
        self.post()
        records['problem_post'] = self.store.snapshot()['records']['problem_post']
        self.assertEqual(review_continuation.plan_next(records, self.artifacts, self.assignment['id']), before)

    def test_changed_statement_openness_started_before_or_after_another_post_stays_eligible(self):
        original = copy.deepcopy(self.selected)
        for timing in ('before', 'after'):
            with self.subTest(timing=timing):
                records = self.store.snapshot()['records']
                packet = json.loads(self.artifacts.read(self.reference))
                changed = dict(original, statement='A different complete synthetic statement.')
                packet['subject'] = changed
                packet['subject_digest'] = digest(changed)
                packet['claim_digest'] = problem_claims.compute_statement_digest(changed)
                reference = self.artifacts.put(json.dumps(packet).encode(), 'application/json')
                assignment = records['review_assignment'][self.assignment['id']]
                assignment['context']['native_packet'] = reference
                revision = assignment['recorded_revision']
                records['problem_post'] = dict(post=dict(id='post',
                    statement_digest=problem_claims.compute_statement_digest(original),
                    recorded_revision=revision + 1 if timing == 'before' else revision - 1))
                self.assertEqual(review_continuation.plan_next(records, self.artifacts, assignment['id'])['state'], 'run')


class UnboundClosureTests(ClosureCase):
    def test_malformed_falsey_bindings_are_validation_errors(self):
        self.post()
        records = self.store.snapshot()['records']
        root = dict(id='unbound', role='bar', dossier_id=None, reviewer_id='first', context={},
                    subject=None, recorded_revision=1)
        for field in ('subject', 'context', 'staged_review'):
            for malformed in ([], '', False, 0):
                with self.subTest(field=field, value=malformed):
                    row = dict(root, **{field: malformed})
                    with self.assertRaises(ResearchError):
                        problem_delivery.find_review_post(records, row, self.artifacts)
        for malformed in ([], {}):
            with self.subTest(slot=malformed):
                with self.assertRaises(ResearchError):
                    problem_delivery.find_review_closure_root(records, dict(root, slot_id=malformed))
        for field, malformed in (('slot_id', []), ('history_assignment_id', '')):
            with self.subTest(bound_ancestry=field):
                row = dict(root, subject=dict(kind='problem', statement_id=self.selected['id']),
                           **{field: malformed})
                with self.assertRaises(ResearchError):
                    problem_delivery.find_review_post(records, row, self.artifacts)
        with self.assertRaises(ResearchError):
            problem_delivery.find_review_post(records,
                dict(root, staged_review=dict(kind='task', controller_assignment_id=[])), self.artifacts)
        for malformed in ([], None):
            with self.subTest(controller=malformed):
                records.setdefault('review_assignment', {})['controller'] = dict(root, id='controller',
                                                                                staged_review=malformed)
                child = dict(root, staged_review=dict(kind='task', controller_assignment_id='controller'))
                with self.assertRaises(ResearchError):
                    problem_delivery.find_review_closure_root(records, child)

    def test_unbound_retained_slot_root_predates_legacy_post_even_when_head_is_newer(self):
        root = dict(id='unbound-root', role='bar', route_id='route', dossier_id=None,
                    reviewer_id='first', author_id='problem-author', subject=dict(kind='problem', statement_id=None),
                    context={}, slot_id='retained-slot', recorded_revision=self.store.revision + 1)
        self.put('review_assignment', root['id'], root)
        self.post(legacy=True)
        post_revision = self.store.revision
        head = dict(root, id='replacement', context={}, recorded_revision=post_revision + 1)
        head['staged_review'] = dict(kind='controller')
        self.put('review_assignment', head['id'], head)
        records = self.store.snapshot()['records']
        self.assertIsNotNone(problem_delivery.find_review_post(records, head))
        self.assertEqual(problem_delivery.find_review_closure_root(records, head)['id'], root['id'])
        child = dict(head, id='staged-child', staged_review=dict(kind='task', controller_assignment_id=head['id']))
        records['review_assignment'][child['id']] = child
        self.assertEqual(problem_delivery.find_review_closure_root(records, child)['id'], root['id'])
        for row in (root, head, child):
            with self.subTest(head=row['id']):
                plan = review_continuation.plan_next(records, self.artifacts, row['id'])
                self.assertEqual((plan['state'], plan['head_id']), ('done', head['id']))
                for key in ('command', 'payload', 'workflow', 'scope'):
                    self.assertNotIn(key, plan)
                with self.assertRaises(ResearchError) as caught:
                    problem_delivery.require_unposted_review(records, row, self.artifacts)
                self.assertEqual(caught.exception.code, 'problem_already_posted')

    def test_missing_post_revision_and_invalid_ancestry_are_never_unbound_fallbacks(self):
        self.post(legacy=True)
        records = self.store.snapshot()['records']
        root = dict(id='unbound', role='slate', dossier_id=None, reviewer_id='slate-reviewer', context={},
                    subject=dict(kind='problem', statement_id=None), recorded_revision=1)
        for mode in ('no-index', 'missing-parent', 'cycle', 'missing-statement'):
            with self.subTest(mode=mode):
                selected = copy.deepcopy(records)
                row = dict(root)
                if mode == 'no-index':
                    selected = dict(selected)
                elif mode == 'missing-parent':
                    row['context'] = dict(prior_assignment_id='absent')
                elif mode == 'cycle':
                    row['context'] = dict(prior_assignment_id=row['id'])
                else:
                    row['subject'] = dict(kind='problem', statement_id='absent')
                selected.setdefault('review_assignment', {})[row['id']] = row
                with self.assertRaises(ResearchError):
                    problem_delivery.find_review_post(selected, row)
                plan = review_continuation.plan_next(selected, self.artifacts, row['id'])
                self.assertEqual(plan['state'], 'stop')
                self.assertEqual(plan['code'], {
                    'no-index': 'problem_post_revision_missing',
                    'missing-parent': 'invalid_review_context',
                    'cycle': 'invalid_review_context',
                    'missing-statement': 'problem_statement_missing',
                }[mode])


class SlateClosureSlotTests(unittest.TestCase):
    def test_new_slate_root_uses_final_reviewer_slot_after_another_slot_was_posted(self):
        from test_research_problem_slate import ProblemSlateTests
        case = ProblemSlateTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.record_candidate()
        case.observe_bars()
        old = case.assign('slate', 'first')
        with patch.object(protocol.transport, 'send_request', side_effect=OSError('Synthetic failed slate')):
            case.mutate(protocol.invoke_assignment, dict(id='failed-slate', assignment_id=old['id']), credential='fixture')
        case.record_slate()
        statement = case.select_statement(candidate_id='candidate-1')
        case.mutate(problem_claims.record_post_external, dict(id='post',
            problem_id='656c336e-e892-4c47-80d2-a71d022f4116',
            statement_digest=problem_claims.compute_statement_digest(statement),
            reason='Synthetic publication.', evidence='Synthetic server receipt.'))
        new = case.assign('slate', 'second')
        self.assertEqual(old['slot_id'], 'bar-first')
        self.assertEqual(new['slot_id'], 'bar-second')
        self.assertIsNone(problem_delivery.find_review_post(case.read_records(), new, case.artifacts))
        self.assertEqual(problem_delivery.find_review_closure_root(case.read_records(), new)['id'], new['id'])

