"""Contamination repair preserves the scientific record and fixed review slots."""

import copy
import json
from unittest.mock import patch

from strategy_fixtures import StrategyCase


class ContextRepairTests(StrategyCase):
    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.record_response('bar-a', 'reviewer-a', self.review_response('bar', status='insufficient'))
        self.record_response('bar-b', 'reviewer-b', self.review_response('bar'))
        self.protocol = self.module('review_protocol')
        self.mutate(self.protocol.record_context_event, {
            'id': 'exposure', 'assignment_id': 'assignment-bar-a', 'kind': 'contamination',
            'source': 'author_history', 'reason': 'The original review context received author advocacy.',
            'evidence': [self.evidence]})

    def payload(self):
        return {'id': 'repair', 'route_id': 'route', 'dossier_id': 'dossier-1', 'role': 'bar',
                'reviewer_id': 'repair-reviewer', 'author_id': 'author', 'context': {'context_repair': {
                    'assignment_id': 'assignment-bar-a', 'event_ids': ['exposure'], 'probe_id': 'repair-probe',
                    'reason': 'Reassess all original findings in a newly verified context.', 'evidence': [self.evidence]}}}

    def probe(self):
        def response(route, request, credential):
            packet = json.loads(request['input'][0]['content'].removeprefix('The packet below is JSON.\n'))
            return self.provider_response({'allowed_control': packet['allowed_control'],
                                           'evidence_control': packet['evidence_control'], 'excluded_controls': []})
        with patch('research_harness.review_transport.send_request', side_effect=response):
            self.protocol.probe_route(self.store, {'id': 'repair-probe', 'route_id': 'route'},
                                      expected_revision=self.store.revision, request_id='repair-probe-call',
                                      credential='fixture-only')

    def prepare(self, payload=None):
        return self.module('review_context_repair').prepare_repair(
            self.store.snapshot()['records'], self.artifacts, self.payload() if payload is None else payload)

    def test_repair_requires_a_new_verified_probe_after_contamination(self):
        payload = self.payload()
        payload['context']['context_repair']['probe_id'] = 'probe'
        self.error('review_context_repair_unverified', lambda: self.prepare(payload))

    def test_repair_retains_original_slot_packet_and_negative_finding(self):
        self.probe()
        prepared = self.prepare()
        self.assertEqual(prepared['slot_id'], 'assignment-bar-a')
        self.assertEqual([r['id'] for r in prepared['historical_reviews']], ['bar-a'])
        self.assertEqual(prepared['scientific_history'][0]['observed_output']['value']['status'], 'insufficient')
        records = self.store.snapshot()['records']
        self.assertEqual(prepared['packet'], json.loads(self.artifacts.read(records['review_assignment']['assignment-bar-a']['packet'])))

    def test_repair_cannot_omit_recorded_contamination_events(self):
        self.probe()
        payload = self.payload()
        payload['context']['context_repair']['event_ids'] = []
        self.error('review_context_repair_mismatch', lambda: self.prepare(payload))

    def test_repair_cannot_choose_the_other_review_slot_identity(self):
        self.probe()
        payload = self.payload()
        payload['reviewer_id'] = 'reviewer-b'
        self.error('review_not_independent', lambda: self.prepare(payload))

    def test_repair_cannot_change_phase_or_add_author_source_substitutes(self):
        self.probe()
        payload = self.payload()
        payload['role'] = 'slate'
        self.error('review_context_repair_mismatch', lambda: self.prepare(payload))
        payload = self.payload()
        payload['context']['source_links'] = []
        self.error('invalid_review_context', lambda: self.prepare(payload))

    def test_historical_negative_reviews_survive_contamination(self):
        reviews = self.module('review_context_repair').historical_reviews(
            self.store.snapshot()['records'], 'dossier-1', 'bar')
        self.assertEqual([r['id'] for r in reviews], ['bar-a'])

    def test_no_output_predecessor_binds_failed_attempt_and_preserves_prior_history(self):
        self.probe()
        records = copy.deepcopy(self.store.snapshot()['records'])
        original = records['review_assignment']['assignment-bar-a']
        failed = dict(original, id='failed', recorded_revision=original['recorded_revision'] + 1,
                      context={'prior_assignment_id': original['id']})
        records['review_assignment']['failed'] = failed
        records.setdefault('review_attempt', {})['failed-call'] = dict(
            id='failed-call', assignment_id='failed', status='failed', recorded_revision=failed['recorded_revision'] + 1,
            origin='instrumented')
        payload = self.payload()
        payload['context']['context_repair']['assignment_id'] = 'failed'
        real_state = self.protocol.assignment_state
        def state(values, artifacts, identifier):
            if identifier == 'failed':
                return dict(output=None, raw_output=None, effective={'kind': 'pending'}, context_status='contaminated',
                            attempt_id=None, obligations=[{'code': 'review_output_pending'}])
            return real_state(values, artifacts, identifier)
        with patch.object(self.protocol, 'assignment_state', side_effect=state):
            prepared = self.module('review_context_repair').prepare_repair(records, self.artifacts, payload)
        self.assertEqual(prepared['predecessor_binding']['attempt_ids'], ['failed-call'])
        self.assertTrue(prepared['predecessor_binding']['no_output'])
        self.assertEqual(prepared['scientific_history'][0]['observed_output']['value']['status'], 'insufficient')

    def test_no_output_repair_rejects_pending_invocation(self):
        self.probe()
        records = copy.deepcopy(self.store.snapshot()['records'])
        records.setdefault('review_invocation', {})['pending'] = dict(id='pending', assignment_id='assignment-bar-a')
        self.error('review_context_repair_pending', lambda: self.module('review_context_repair').prepare_repair(
            records, self.artifacts, self.payload()))

    def test_partial_series_preserves_intermediate_output_without_recording_a_final(self):
        from research_harness import review_problem_parts as parts
        self.probe()
        records = self.store.snapshot()['records']
        state = self.protocol.assignment_state(records, self.artifacts, 'assignment-bar-a')
        notes = {'unit_notes': [{'note': 'A required condition remains unassessed.'}]}
        state.update(output=notes, raw_output=notes, effective={'kind': 'ordinary', 'effective_output': notes})
        packet = {parts.KEY: {'protocol': parts.PROTOCOL, 'stage': 'part'}}
        with patch.object(self.protocol, 'assignment_state', return_value=state), \
                patch.object(self.protocol, 'source_delivery_packet', return_value=packet):
            prepared = self.module('review_context_repair').prepare_repair(records, self.artifacts, self.payload())
        self.assertIsNone(prepared['scientific_history'][0]['observed_output'])
        self.assertEqual(prepared['scientific_history'][0]['intermediate_output'], notes)
        self.assertFalse(prepared['predecessor_binding']['no_output'])
        self.assertTrue(prepared['predecessor_binding']['no_effective_output'])

    def test_later_ancestor_exposure_invalidates_the_recorded_repair_binding(self):
        self.probe()
        self.prepare()
        saved = self.mutate(self.protocol.record_assignment, self.payload())['result']
        self.mutate(self.protocol.record_context_event, {
            'id': 'later-exposure', 'assignment_id': 'assignment-bar-a', 'kind': 'contamination',
            'source': 'prior_assessments', 'reason': 'Additional exposure was discovered after reassessment.',
            'evidence': [self.evidence]})
        helper = self.module('review_context_repair')
        self.error('review_context_repair_stale', lambda: helper.validate_repair_binding(
            self.store.snapshot()['records'], self.artifacts, saved))

    def test_observed_reassessment_must_address_each_original_review(self):
        self.probe()
        helper = self.module('review_context_repair')
        output = self.review_response('bar')
        self.error('review_reassessment_missing', lambda: helper.validate_reassessment(
            self.store.snapshot()['records'], self.artifacts, self.payload(), output))
        output['reassessment'] = {'assignment_id': 'assignment-bar-a', 'findings': []}
        self.error('review_reassessment_incomplete', lambda: helper.validate_reassessment(
            self.store.snapshot()['records'], self.artifacts, self.payload(), output))

    def test_favorable_change_cannot_be_labeled_confirmation(self):
        self.probe()
        helper = self.module('review_context_repair')
        output = self.review_response('bar')
        output['reassessment'] = {'assignment_id': 'assignment-bar-a', 'findings': [{
            'review_id': 'bar-a', 'disposition': 'confirmed', 'reason': 'The earlier finding is retained.',
            'evidence': [self.evidence]}]}
        self.error('review_reassessment_mismatch', lambda: helper.validate_reassessment(
            self.store.snapshot()['records'], self.artifacts, self.payload(), output))
        changed = copy.deepcopy(output)
        changed['reassessment']['findings'][0]['disposition'] = 'revised'
        helper.validate_reassessment(self.store.snapshot()['records'], self.artifacts, self.payload(), changed)


class CrossPhaseContextRepairTests(StrategyCase):
    probe = ContextRepairTests.probe

    def test_slate_repair_preserves_its_slot_and_actual_bar_exposure(self):
        self.reviewed_dossier()
        self.protocol = self.module('review_protocol')
        self.mutate(self.protocol.record_context_event, {
            'id': 'bar-exposure', 'assignment_id': 'assignment-bar-a', 'kind': 'contamination',
            'source': 'author_history', 'reason': 'The inherited bar context contained author advocacy.',
            'evidence': [self.evidence]})
        self.probe()
        payload = {'id': 'slate-repair', 'route_id': 'route', 'dossier_id': 'dossier-1', 'role': 'slate',
                   'reviewer_id': 'slate-repair-reviewer', 'author_id': 'author', 'context': {'context_repair': {
                       'assignment_id': 'assignment-slate-a', 'event_ids': ['bar-exposure'], 'probe_id': 'repair-probe',
                       'reason': 'Reassess the scientific slate after exposure in its actual inherited context.',
                       'evidence': [self.evidence]}}}
        prepared = self.module('review_context_repair').prepare_repair(
            self.store.snapshot()['records'], self.artifacts, payload)
        self.assertEqual(prepared['slot_id'], 'assignment-slate-a')
        self.assertEqual([item['assignment_id'] for item in prepared['scientific_history']],
                         ['assignment-bar-a', 'assignment-slate-a'])
        self.assertEqual([item['id'] for item in prepared['historical_reviews']], ['slate-a'])
