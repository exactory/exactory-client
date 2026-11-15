"""Unrun assignments keep their recorded context and release an unusable position."""

import copy
import json
import sys
import unittest
from unittest.mock import patch

from research_harness import cli, review_protocol as protocol
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_continuation import plan_next
import test_research_problem_reviews as fixtures


class DeadAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        from test_series_code_reach import record_reachable_errors
        recorder = record_reachable_errors('run_series')
        recorder.__enter__()
        self.addCleanup(recorder.__exit__, None, None, None)
        with patch.object(protocol, 'probe_route'):
            self.case.prepare_review_route()
        self.install_input_capacity(self.case)
        self.case.supporting = self.case.artifacts.put(b'A synthetic standard.', 'text/plain')

    def install_input_capacity(self, case):
        """Give the stub its input witness without proving context independence."""
        from capacity_evidence_fixtures import build_capacity_prior
        from research_harness import review_capacity_evidence as evidence
        route = case.store.snapshot()['records']['review_route']['route']
        witness = build_capacity_prior(route, 2000000, identifier='dead-assignment-input')
        self.assertIsNotNone(witness['key'])
        active = patch.object(evidence, 'CAPACITY_PRIORS', tuple(evidence.read_capacity_priors()) + (witness,))
        active.start()
        case.addCleanup(active.stop)
        self.assertEqual(protocol.route_state(case.store.snapshot()['records'], case.artifacts, route['id'])
                         ['context_status'], 'unverified')

    def probe(self, identifier="probe"):
        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'].split('\n', 1)[1])
            return self.case.build_provider_response(dict(allowed_control=packet['allowed_control'],
                evidence_control=packet['evidence_control'], excluded_controls=[]))
        with patch('research_harness.review_transport.send_request', side_effect=respond):
            self.case.mutate(protocol.probe_route, dict(id=identifier, route_id='route'), credential='fixture')

    def assign(self, reviewer, identifier=None, **context):
        return self.case.assign('bar', reviewer, identifier=identifier, **context)

    def plan(self, identifier):
        return plan_next(self.case.read_records(), self.case.artifacts, identifier)

    def execute(self, plan):
        return self.case.mutate(cli.OPERATIONS[plan['command']], plan['payload'])['result']

    def test_both_occupied_slots_accept_same_reviewer_after_probe(self):
        self.probe()
        self.assign('second')
        with patch('research_harness.review_transport.send_request', side_effect=OSError('offline')):
            self.case.mutate(protocol.probe_route, dict(id='offline', route_id='route'), credential='fixture')
        first = self.assign('first')
        frozen = digest(self.case.read_records())
        self.probe('renewed')
        original = copy.deepcopy(self.case.read_records()['review_assignment'])
        plan = self.plan(first['id'])
        self.assertEqual((plan['state'], plan['command']), ('replace', 'review-assignment'))
        replacement = self.execute(plan)
        self.assertEqual((replacement['reviewer_id'], replacement['slot_id']), ('first', first['id']))
        self.assertEqual(replacement['context_state']['context_status'], 'verified_for_route')
        self.assertEqual(self.plan(first['id'])['state'], 'run')
        for identifier, row in original.items():
            self.assertEqual(self.case.read_records()['review_assignment'][identifier], row)
        self.assertNotEqual(digest(self.case.read_records()), frozen)

    def test_same_reviewer_replacement_selects_its_own_dead_slot(self):
        self.probe()
        first = self.assign('first')
        second = self.assign('second')
        self.seed_unverified_context(first)
        self.seed_unverified_context(second)
        plan = self.plan(second['id'])
        replacement = self.execute(plan)
        self.assertEqual(replacement['slot_id'], second['id'])
        self.assertEqual(self.plan(second['id'])['head_id'], replacement['id'])
        self.assertEqual(self.plan(first['id'])['state'], 'replace')

    def test_dead_slot_accepts_another_reviewer(self):
        self.probe()
        self.assign('second')
        with patch('research_harness.review_transport.send_request', side_effect=OSError('offline')):
            self.case.mutate(protocol.probe_route, dict(id='offline', route_id='route'), credential='fixture')
        first = self.assign('first')
        self.probe('renewed')
        replacement = self.assign('third')
        self.assertEqual(replacement['slot_id'], first['id'])
        from research_harness.review_plan_steps import list_excluded_reviewers
        self.assertNotIn('first', list_excluded_reviewers(self.case.read_records(), replacement))

    def test_unverified_assignment_is_refused_before_any_call_or_attempt(self):
        assignment = self.assign('first')
        before = self.case.read_records()
        with self.assertRaises(ResearchError) as raised:
            self.case.mutate(protocol.invoke_assignment,
                dict(id='unverified', assignment_id=assignment['id']), credential='fixture')
        self.assertEqual(raised.exception.code, 'review_context_unverified')
        next_step = raised.exception.details['next']
        self.assertEqual(next_step['command'], 'review-probe')
        self.assertEqual(self.case.read_records(), before)
        self.assertEqual(self.plan(assignment['id'])['command'], 'review-probe')

    def test_probe_never_makes_an_old_assignment_runnable(self):
        assignment = self.assign('first')
        self.probe()
        before = self.case.read_records()
        with self.assertRaises(ResearchError) as raised:
            self.case.mutate(protocol.invoke_assignment,
                dict(id='old-context', assignment_id=assignment['id']), credential='fixture')
        self.assertEqual(raised.exception.code, 'review_context_unverified')
        next_step = raised.exception.details['next']
        self.assertEqual(next_step['command'], 'review-assignment')
        self.assertEqual(self.case.read_records(), before)

    def test_legacy_attempt_on_unverified_assignment_is_a_decision(self):
        assignment = self.assign('first')
        self.case.mutate(protocol.record_attempt, dict(id='legacy', assignment_id=assignment['id'],
            status='failed', output=None, reason='A historical invocation.', usage=None))
        self.probe()
        plan = self.plan(assignment['id'])
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_context_unverified'))
        self.assertTrue(plan['details']['decision']['then'])

    def seed_unverified_context(self, assignment, case=None):
        case = case or self.case
        legacy = copy.deepcopy(assignment)
        legacy['context_state']['context_status'] = 'unverified'
        case.store.mutate('fixture.legacy-assignment', {},
            lambda tx: tx.put('review_assignment', legacy['id'], legacy),
            expected_revision=case.store.revision, request_id='legacy-' + legacy['id'])

    def test_dead_continuation_can_be_replaced_without_a_fork(self):
        self.probe()
        parent = self.assign('first')
        output = self.case.build_review_output('bar')
        output['source_requests'] = [dict(id='page', kind='inventory_page', page=0)]
        output['value']['status'] = 'unresolved'
        with patch('research_harness.review_transport.send_request',
                   return_value=self.case.build_provider_response(output)):
            self.case.mutate(protocol.invoke_assignment,
                dict(id='parent-run', assignment_id=parent['id']), credential='fixture')
        child = self.execute(self.plan(parent['id']))
        self.seed_unverified_context(child)
        plan = self.plan(child['id'])
        self.assertEqual(plan['state'], 'replace')
        replacement = self.execute(plan)
        self.assertEqual(replacement['context']['prior_assignment_id'], parent['id'])
        self.assertEqual(self.plan(child['id'])['head_id'], replacement['id'])

    def test_dead_repair_releases_its_position_and_keeps_the_binding(self):
        from test_research_repair_slot_replacement import RepairSlotReplacementTests
        case = RepairSlotReplacementTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        repaired = case.prepare_repair()
        legacy = copy.deepcopy(repaired)
        legacy['context_state']['context_status'] = 'unverified'
        case.store.mutate('fixture.legacy-repair', {},
            lambda tx: tx.put('review_assignment', legacy['id'], legacy),
            expected_revision=case.store.revision, request_id='legacy-repair')
        before = copy.deepcopy(case.store.snapshot()['records']['review_assignment'])
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, repaired['id'])
        self.assertEqual(plan['state'], 'replace')
        replacement = case.mutate(cli.OPERATIONS[plan['command']], plan['payload'])['result']
        self.assertEqual(replacement['context'], repaired['context'])
        self.assertEqual(replacement['slot_id'], repaired['slot_id'])
        for identifier, row in before.items():
            self.assertEqual(case.store.snapshot()['records']['review_assignment'][identifier], row)

    def test_dead_native_continuations_release_their_parent(self):
        self.case.doCleanups()
        from openness_reading_fixtures import OpennessReadingCase
        from test_review_parts_native import NativePartsTests
        from review_parts_fixtures import make_output
        from codex_route_fixtures import invoke_codex
        for case_type in (OpennessReadingCase, NativePartsTests):
            with self.subTest(kind=case_type.__name__):
                case = case_type()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.limit_input()
                native = case.export() if case_type is OpennessReadingCase else None
                parent = case.assign('turn-1', native) if native else case.assign('first')
                packet = (case.read_packet(parent['id']) if native else case.read_packet(parent))
                output = case.build_reading_response(packet) if native else make_output(packet)
                invoke_codex(case, parent['id'], output)
                child = (case.assign('turn-2', native, prior=parent['id']) if native
                         else case.assign('turn-2', prior=parent['id']))
                legacy = copy.deepcopy(child)
                legacy['context_state']['context_status'] = 'unverified'
                case.store.mutate('fixture.legacy-child', {},
                    lambda tx: tx.put('review_assignment', legacy['id'], legacy),
                    expected_revision=case.store.revision, request_id='legacy-child')
                plan = plan_next(case.store.snapshot()['records'], case.artifacts, child['id'])
                self.assertEqual(plan['state'], 'replace')
                accepted = cli.OPERATIONS[plan['command']](case.store, plan['payload'],
                    expected_revision=case.store.revision, request_id='replace-dead')['result']
                self.assertEqual(accepted['context']['prior_assignment_id'], parent['id'])

    def test_dead_initial_native_review_requests_a_fresh_attestation(self):
        self.case.doCleanups()
        from test_review_parts_native import NativePartsTests
        case = NativePartsTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        first = case.assign()
        legacy = copy.deepcopy(first)
        legacy['context_state']['context_status'] = 'unverified'
        case.store.mutate('fixture.legacy-initial', {},
            lambda tx: tx.put('review_assignment', legacy['id'], legacy),
            expected_revision=case.store.revision, request_id='legacy-initial')
        plan = plan_next(case.store.snapshot()['records'], case.artifacts, first['id'])
        self.assertEqual((plan['state'], plan['command']), ('handoff', 'search review-packet'))
        self.assertEqual(plan['details']['step']['id'], 5)

    def test_dead_standalone_replacement_is_reached_from_the_original_id(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.route()
        self.install_input_capacity(case)
        initial = case.assign()['result']
        before = copy.deepcopy(initial)
        case.run_probe()
        plan = plan_next(case.records(), case.artifacts, initial['id'])
        replacement = case.mutate(protocol.record_assignment, plan['payload'])['result']
        following = plan_next(case.records(), case.artifacts, initial['id'])
        self.assertEqual((following['head_id'], following['state']), (replacement['id'], 'run'))
        self.assertEqual(case.records()['review_assignment'][initial['id']], before)
        case.mutate(protocol.record_attempt, dict(id='late-legacy', assignment_id=initial['id'],
            status='failed', output=None, usage=None, reason='A delayed historical import.'))
        self.assertEqual(plan_next(case.records(), case.artifacts, initial['id'])['head_id'], replacement['id'])

    def test_dead_verification_replacement_is_reached_from_the_original_id(self):
        from test_review_parts_study import VerificationPartsTests
        case = VerificationPartsTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        first = case.assign()
        self.seed_unverified_context(first, case.case)
        old = copy.deepcopy(case.records()['review_assignment'][first['id']])
        plan = plan_next(case.records(), case.case.artifacts, first['id'])
        accepted = case.case.mutate(protocol.record_assignment, plan['payload'])['result']
        follow = plan_next(case.records(), case.case.artifacts, first['id'])
        self.assertEqual((follow['head_id'], follow['state']), (accepted['id'], 'run'))
        self.assertEqual(case.records()['review_assignment'][first['id']], old)

    def test_dead_manuscript_replacement_is_reached_from_the_original_id(self):
        from test_research_review_protocol import ReviewProtocolTests
        case = ReviewProtocolTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.route()
        case.run_probe()
        case.seed('collection', 'cohort', dict(id='cohort', definition=dict(corpus='arxiv',
            primaryCategory='cs.LG', windowStart='2026-01-01', windowEnd='2026-01-31')))
        case.seed('literature_scope', 'research', dict(collection_ids=['cohort']))
        paper = case.artifacts.put(b'A synthetic scientific manuscript.', 'text/plain')
        bundle = dict(id='paper', digest='exact-paper', files=dict(pdf=dict(artifact=paper)),
            claim_evidence=[], execution_observations={}, review_inputs=dict(synthesis=dict(
                sections=dict(standards=dict(payload=dict(scientific_requirement='State exact assumptions'))),
                foundation={})))
        case.seed('publication_bundle', 'paper', bundle)
        first = case.mutate(protocol.record_assignment, dict(id='manuscript-first', route_id='route',
            dossier_id=None, role='manuscript', reviewer_id='reviewer', author_id='author',
            context=dict(bundle=bundle)))['result']
        self.seed_unverified_context(first, case)
        old = copy.deepcopy(case.records()['review_assignment'][first['id']])
        plan = plan_next(case.records(), case.artifacts, first['id'])
        accepted = case.mutate(protocol.record_assignment, plan['payload'])['result']
        follow = plan_next(case.records(), case.artifacts, first['id'])
        self.assertEqual((follow['head_id'], follow['state']), (accepted['id'], 'run'))
        self.assertEqual(case.records()['review_assignment'][first['id']], old)

    def test_dead_staged_child_is_refused_before_reservation(self):
        from test_review_continuation_staged import StagedContinuationTests
        fixture = StagedContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.execute(fixture.plan())
        planned = fixture.plan()
        records = fixture.case.read_records()
        child = next(row for row in records['review_assignment'].values()
            if row.get('staged_review', {}).get('task_id') == planned['payload']['task_id'])
        legacy = copy.deepcopy(child)
        legacy['context_state']['context_status'] = 'unverified'
        fixture.case.store.mutate('fixture.legacy-staged', {},
            lambda tx: tx.put('review_assignment', legacy['id'], legacy),
            expected_revision=fixture.case.store.revision, request_id='legacy-staged')
        before = fixture.case.read_records()
        with patch('research_harness.review_staged_protocol.wait_for_capture',
                   side_effect=AssertionError('Unverified task reached dispatch')):
            with self.assertRaises(ResearchError) as raised:
                fixture.execute(planned, request='unverified-run')
        self.assertEqual(raised.exception.code, 'review_context_unverified')
        self.assertEqual(fixture.case.read_records(), before)

    def test_dead_other_slot_reviewer_can_repair(self):
        from test_research_repair_slot_replacement import RepairSlotReplacementTests
        case = RepairSlotReplacementTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.expose()
        case.probe_repair()
        legacy = copy.deepcopy(case.store.snapshot()['records']['review_assignment']['assignment-bar-b'])
        legacy.update(id='dead-other', reviewer_id='repair-reviewer')
        legacy['context_state']['context_status'] = 'unverified'
        legacy['recorded_revision'] = case.store.revision + 1
        case.store.mutate('fixture.dead-other', {}, lambda tx: tx.put('review_assignment', legacy['id'], legacy),
            expected_revision=case.store.revision, request_id='dead-other')
        accepted = case.mutate(protocol.record_assignment, case.repair_payload())['result']
        self.assertEqual(accepted['slot_id'], 'assignment-bar-a')

    def test_dead_focused_reviewer_is_eligible_after_successor_exhaustion(self):
        self.case.doCleanups()
        from test_research_problem_adjudication_resolution import ReviewOutputRefusalTests
        case = ReviewOutputRefusalTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare_reviews(result_reviewer='first')
        context = dict(review_ids=['review-result-first', 'review-result-second'],
                       objection_id='unsupported-implication')
        old = case.assign('adjudicator', 'third', identifier='old-focused', **context)
        legacy = copy.deepcopy(old)
        legacy['context_state']['context_status'] = 'unverified'
        case.store.mutate('fixture.dead-focused', {}, lambda tx: tx.put('review_assignment', legacy['id'], legacy),
            expected_revision=case.store.revision, request_id='dead-focused')
        head = case.assign('adjudicator', 'fourth', identifier='focused', **context)
        output = dict(objection_id=context['objection_id'], disposition='not_upheld',
            reason='Exact evidence resolves it.', correction_admissible=None,
            evidence=[case.artifacts.put(b'Undelivered evidence.', 'text/plain')])
        for number in range(3):
            case.refuse(output, head['id'], 'refused-' + str(number + 1), command='review-adjudication')
            plan = plan_next(case.store.snapshot()['records'], case.artifacts, head['id'])
            if number < 2:
                self.assertEqual((plan['state'], plan['command']), ('replace', 'review-assignment'))
                head = case.mutate(protocol.record_assignment, plan['payload'])['result']
        self.assertEqual((plan['state'], plan['code']), ('handoff', 'review_slot_exhausted'))
        accepted = case.assign('adjudicator', 'third', identifier='new-focused', **context)
        self.assertEqual(accepted['slot_id'], old.get('slot_id', old['id']))
        self.assertEqual(plan_next(case.store.snapshot()['records'], case.artifacts, old['id'])['head_id'], accepted['id'])
        records = case.store.snapshot()['records']
        self.assertEqual(records['review_assignment'][old['id']], legacy)
        self.assertFalse(any(attempt['assignment_id'] == old['id'] for attempt in records['review_attempt'].values()))

    def prepare_staged_child(self):
        from test_review_continuation_staged import StagedContinuationTests
        fixture = StagedContinuationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.execute(fixture.plan())
        return fixture, fixture.plan()

    def test_unrun_staged_controllers_are_replaced_without_holding_the_epoch(self):
        from research_harness import review_staged_protocol as staged
        from research_harness.review_continuation import list_chain_heads
        from test_review_continuation_staged import StagedContinuationTests
        for planned_child in (False, True):
            with self.subTest(planned_child=planned_child):
                fixture = StagedContinuationTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                if planned_child:
                    fixture.execute(fixture.plan())
                original_id = fixture.controller['id']
                original = None
                for number in range(3):
                    current = fixture.case.read_records()['review_assignment'][fixture.plan()['head_id']]
                    self.seed_unverified_context(current, fixture.case)
                    before = fixture.case.read_records()
                    original = original or copy.deepcopy(before['review_assignment'][original_id])
                    self.assertIn(current['id'], list_chain_heads(before, fixture.case.artifacts))
                    plan = fixture.plan()
                    self.assertEqual(plan['state'], 'replace')
                    self.assertEqual(plan['payload']['context'], dict(staged_upgrade=dict(assignment_id=current['id'])))
                    epoch = before['review_epoch'][current['staged_review']['epoch_id']]
                    with patch.object(staged, 'build_manifest', return_value=epoch['manifest']):
                        accepted = fixture.execute(plan, request='replace-controller-' + str(number))['result']
                    self.assertEqual(fixture.plan()['head_id'], accepted['id'])
                    self.assertEqual(fixture.plan()['state'], 'plan')
                    self.assertEqual(fixture.case.read_records()['review_assignment'][original_id], original)

    def test_pending_staged_child_precedes_a_dead_controller_replacement(self):
        fixture, action = self.prepare_staged_child()
        with patch('research_harness.review_staged_protocol.wait_for_capture', side_effect=RuntimeError('not launched')):
            with self.assertRaises(RuntimeError):
                fixture.execute(action, request='reserve-child')
        self.seed_unverified_context(fixture.controller, fixture.case)
        plan = fixture.plan()
        self.assertEqual((plan['state'], plan['payload']['action']), ('wait', 'reconcile'))
        self.assertFalse(protocol.is_dead_assignment(fixture.case.read_records(),
            fixture.case.read_records()['review_assignment'][fixture.controller['id']]))

    def test_staged_controller_with_a_completed_child_requires_a_legacy_decision(self):
        from research_harness import review_worker
        from test_review_staged_frontier import WORKER
        fixture, action = self.prepare_staged_child()
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            fixture.execute(action, request='completed-child')
        self.seed_unverified_context(fixture.controller, fixture.case)
        plan = fixture.plan()
        self.assertEqual((plan['state'], plan['code']), ('stop', 'review_context_unverified'))

    def test_captured_staged_child_precedes_the_legacy_context_decision(self):
        from research_harness import review_worker, review_task_execution as execution
        from test_review_staged_frontier import WORKER
        fixture, action = self.prepare_staged_child()
        with patch.object(review_worker, '_entry_command', return_value=[sys.executable, '-c', WORKER]):
            with patch.object(execution, 'accept_observation', side_effect=RuntimeError('accept later')):
                with self.assertRaises(RuntimeError):
                    fixture.execute(action, request='captured-child')
        self.seed_unverified_context(fixture.controller, fixture.case)
        plan = fixture.plan()
        self.assertEqual((plan['state'], plan['payload']['action']), ('resume', 'accept'))
        self.assertEqual(plan['request_id'], 'captured-child')
        fixture.execute(plan)
        self.assertEqual(fixture.plan()['state'], 'stop')

    def test_pending_invocation_keeps_a_dead_context_position(self):
        assignment = self.assign('first')
        self.case.store.mutate('fixture.pending', {}, lambda tx: tx.put('review_invocation', 'pending',
            dict(id='pending', assignment_id=assignment['id'])),
            expected_revision=self.case.store.revision, request_id='pending')
        self.assertFalse(protocol.is_dead_assignment(self.case.read_records(), assignment))
        self.probe()
        with self.assertRaises(ResearchError) as raised:
            self.assign('first', identifier='replacement')
        self.assertEqual(raised.exception.code, 'review_resampling_forbidden')
