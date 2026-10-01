"""An operational replacement owns the existing slot and cannot add a vote."""

from unittest.mock import patch

from strategy_fixtures import StrategyCase
import test_research_context_repair_integration as fixtures
from research_harness import research_decisions, review_context_repair, review_protocol


class RepairSlotReplacementTests(StrategyCase):
    expose = fixtures.ContextRepairIntegrationTests.expose
    probe_repair = fixtures.ContextRepairIntegrationTests.probe_repair
    repair_payload = fixtures.ContextRepairIntegrationTests.repair_payload
    prepare_repair = fixtures.ContextRepairIntegrationTests.prepare_repair
    final_response = fixtures.ContextRepairIntegrationTests.final_response
    invoke = fixtures.ContextRepairIntegrationTests.invoke
    record_final = fixtures.ContextRepairIntegrationTests.record_final

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.objection = {'id': 'counterexample', 'claim': 'The broader inference is unsupported.',
            'reason': 'The same observation fits both mechanisms.', 'evidence': [self.evidence],
            'resolution_condition': 'Supply a deciding observation for the two mechanisms.'}
        self.original = self.review_response('bar', status='insufficient', objections=[self.objection])
        self.record_response('bar-a', 'reviewer-a', self.original)
        self.record_response('bar-b', 'reviewer-b', self.review_response('bar'))

    def failed_repair_replacement(self):
        self.prepare_repair()
        with patch('research_harness.review_transport.send_request', side_effect=OSError('Recorded synthetic transport failure')):
            failure = review_protocol.invoke_assignment(self.store, {'id': 'failed-repair', 'assignment_id': 'repair-a'},
                expected_revision=self.store.revision, request_id='failed-repair-call', credential='fixture-only')
        self.assertEqual(failure['result']['status'], 'failed')
        payload = self.repair_payload()
        payload.update(id='repair-b', reviewer_id='second-repair-reviewer')
        self.mutate(review_protocol.record_assignment, payload)
        output = self.final_response()
        self.invoke(output, 'repair-b')
        self.record_final(output, 'repair-b')
        return output

    def test_successful_failed_repair_replacement_prevents_old_retry(self):
        output = self.failed_repair_replacement()
        self.error('review_assignment_superseded', lambda: self.invoke(output, 'repair-a'))
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_attempt']['failed-repair']['status'], 'failed')
        self.assertEqual(review_context_repair.slot_id(records, records['review_assignment']['repair-b']), 'assignment-bar-a')
        self.assertEqual({r['id'] for r, _ in review_protocol._bars(records, self.artifacts, 'intent-1')},
                         {'bar-b', 'repair-final'})

    def test_pair_cannot_count_original_and_repaired_finding_as_two_slots(self):
        self.failed_repair_replacement()
        records = self.store.snapshot()['records']
        self.error('research_review_pair_required', lambda: research_decisions._pair(records,
            ['bar-a', 'repair-final'], records['strategy_dossier']['dossier-1'], 'bar'))

    def test_failed_repair_does_not_allow_fresh_assignment_to_drop_adverse_history(self):
        self.prepare_repair()
        with patch('research_harness.review_transport.send_request', side_effect=OSError('Recorded synthetic transport failure')):
            review_protocol.invoke_assignment(self.store, {'id': 'failed-repair', 'assignment_id': 'repair-a'},
                expected_revision=self.store.revision, request_id='failed-repair-call', credential='fixture-only')
        payload = self.repair_payload()
        payload.update(id='fresh-replacement', reviewer_id='fresh-reviewer', context={})
        self.error('review_slots_fixed', lambda: self.mutate(review_protocol.record_assignment, payload))

    def test_unfinished_failed_repair_retry_cannot_be_replaced(self):
        self.prepare_repair()
        with patch('research_harness.review_transport.send_request', side_effect=OSError('Recorded first failure')):
            review_protocol.invoke_assignment(self.store, {'id': 'failure', 'assignment_id': 'repair-a'},
                expected_revision=self.store.revision, request_id='failure-call', credential='fixture-only')
        with patch('research_harness.review_transport.send_request', side_effect=SystemExit('Native worker interrupted')):
            with self.assertRaises(SystemExit):
                review_protocol.invoke_assignment(self.store, {'id': 'unfinished-repair-retry', 'assignment_id': 'repair-a'},
                    expected_revision=self.store.revision, request_id='unfinished-repair-retry-call', credential='fixture-only')
        records = self.store.snapshot()['records']
        self.assertIn('unfinished-repair-retry', records['review_invocation'])
        self.assertNotIn('unfinished-repair-retry', records['review_attempt'])
        payload = self.repair_payload()
        payload.update(id='repair-b', reviewer_id='replacement-reviewer')
        self.error('review_resampling_forbidden', lambda: self.mutate(review_protocol.record_assignment, payload))


class OrdinarySlotReplacementTests(StrategyCase):
    invoke = fixtures.ContextRepairIntegrationTests.invoke

    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()

    def assign(self, identifier, reviewer):
        return self.mutate(review_protocol.record_assignment, {'id': identifier, 'route_id': 'route',
            'dossier_id': 'dossier-1', 'role': 'bar', 'reviewer_id': reviewer, 'author_id': 'author', 'context': {}})['result']

    def test_failed_initial_replacement_preserves_slot_and_prevents_old_retry(self):
        self.assign('failed-a', 'reviewer-a')
        with patch('research_harness.review_transport.send_request', side_effect=OSError('Recorded synthetic transport failure')):
            failure = review_protocol.invoke_assignment(self.store, {'id': 'failure', 'assignment_id': 'failed-a'},
                expected_revision=self.store.revision, request_id='failure-call', credential='fixture-only')
        self.assertEqual(failure['result']['status'], 'failed')
        replacement = self.assign('replacement-c', 'reviewer-c')
        self.assertEqual(review_context_repair.slot_id(self.store.snapshot()['records'], replacement), 'failed-a')
        other = self.assign('other-b', 'reviewer-b')
        self.assertEqual(review_context_repair.slot_id(self.store.snapshot()['records'], other), 'other-b')
        self.invoke(self.review_response('bar'), 'replacement-c')
        self.error('review_assignment_superseded', lambda: self.invoke(self.review_response('bar'), 'failed-a'))
        self.error('review_slots_fixed', lambda: self.assign('extra-d', 'reviewer-d'))

    def test_unfinished_retry_keeps_its_failed_slot_occupied(self):
        self.assign('failed-a', 'reviewer-a')
        self.assign('other-b', 'reviewer-b')
        with patch('research_harness.review_transport.send_request', side_effect=OSError('Recorded first failure')):
            review_protocol.invoke_assignment(self.store, {'id': 'failure', 'assignment_id': 'failed-a'},
                expected_revision=self.store.revision, request_id='failure-call', credential='fixture-only')
        with patch('research_harness.review_transport.send_request', side_effect=SystemExit('Native worker interrupted')):
            with self.assertRaises(SystemExit):
                review_protocol.invoke_assignment(self.store, {'id': 'unfinished-retry', 'assignment_id': 'failed-a'},
                    expected_revision=self.store.revision, request_id='unfinished-retry-call', credential='fixture-only')
        records = self.store.snapshot()['records']
        self.assertIn('unfinished-retry', records['review_invocation'])
        self.assertNotIn('unfinished-retry', records['review_attempt'])
        self.error('review_slots_fixed', lambda: self.assign('replacement-c', 'reviewer-c'))
