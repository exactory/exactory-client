"""Observed adverse finals cannot disappear by delaying native transcription."""

import json
from unittest.mock import patch

from strategy_fixtures import StrategyCase
from research_harness import research_decisions, review_protocol


class UnrecordedFinalTests(StrategyCase):
    def setUp(self):
        super().setUp()
        self.setup_dossier()
        self.route()
        self.objection = {'id': 'unresolved-inference', 'claim': 'The inference is unsupported.',
            'reason': 'Both mechanisms explain the observation.', 'evidence': [self.evidence],
            'resolution_condition': 'Supply an observation that distinguishes the mechanisms.'}
        self.original = self.review_response('bar', status='insufficient', objections=[self.objection])
        self.mutate(review_protocol.record_assignment, {'id': 'a', 'route_id': 'route',
            'dossier_id': 'dossier-1', 'role': 'bar', 'reviewer_id': 'reviewer-a',
            'author_id': 'author', 'context': {}})
        self.invoke('a', self.original)

    def invoke(self, assignment, response):
        with patch('research_harness.review_transport.send_request', return_value=self.provider_response(response)):
            return review_protocol.invoke_assignment(self.store, {'id': 'attempt-' + assignment, 'assignment_id': assignment},
                expected_revision=self.store.revision, request_id='invoke-' + assignment, credential='fixture-only')

    def expose(self, identifier):
        self.mutate(review_protocol.record_context_event, {'id': identifier, 'assignment_id': 'a',
            'kind': 'contamination', 'source': 'author_history', 'reason': 'Actual author context exposure was observed.',
            'evidence': [self.evidence]})

    def probe(self, identifier):
        def provider(route, request, credential):
            packet = json.loads(request['input'][0]['content'])
            return self.provider_response({'allowed_control': packet['allowed_control'],
                'evidence_control': packet['evidence_control'], 'excluded_controls': []})
        with patch('research_harness.review_transport.send_request', side_effect=provider):
            return review_protocol.probe_route(self.store, {'id': identifier, 'route_id': 'route'},
                expected_revision=self.store.revision, request_id='invoke-' + identifier, credential='fixture-only')

    def repair(self, identifier, predecessor, events, probe):
        return self.mutate(review_protocol.record_assignment, {'id': identifier, 'route_id': 'route',
            'dossier_id': 'dossier-1', 'role': 'bar', 'reviewer_id': 'reviewer-' + identifier,
            'author_id': 'author', 'context': {'context_repair': {'assignment_id': predecessor,
                'event_ids': events, 'probe_id': probe, 'reason': 'Reassess the retained scientific history.',
                'evidence': [self.evidence]}}})

    def register(self, identifier, assignment, response):
        return self.mutate(research_decisions.record_value_review,
            dict(response, id=identifier, dossier_id='dossier-1', assignment_id=assignment))

    def repaired_response(self):
        response = self.review_response('bar')
        response['reassessment'] = {'assignment_id': 'a', 'findings': [{'review_id': 'original-final',
            'disposition': 'revised', 'reason': 'A bounded consequence is useful; the old broader objection remains.',
            'evidence': [self.evidence]}]}
        response['value']['objection_findings'] = [{'id': self.objection['id'], 'status': 'continuing',
            'reason': 'The broader inference remains unsupported.', 'evidence': [self.evidence]}]
        return response

    def test_original_observed_final_must_be_preserved_before_repair(self):
        self.expose('event-1')
        self.probe('probe-1')
        self.error('review_context_repair_history_unrecorded',
            lambda: self.repair('repair-1', 'a', ['event-1'], 'probe-1'))
        self.register('original-final', 'a', self.original)
        saved = self.repair('repair-1', 'a', ['event-1'], 'probe-1')['result']
        packet = json.loads(self.artifacts.read(saved['packet']))
        self.assertEqual(packet['context_repair']['historical_review_ids'], ['original-final'])
        self.assertIn(self.objection['reason'], json.dumps(packet['historical_reviews']))
        response = self.repaired_response()
        self.invoke('repair-1', response)
        self.register('repaired-final', 'repair-1', response)

    def test_late_exposure_does_not_prevent_historical_registration_of_observed_repair(self):
        self.register('original-final', 'a', self.original)
        self.expose('event-1')
        self.probe('probe-1')
        self.repair('repair-1', 'a', ['event-1'], 'probe-1')
        response = self.repaired_response()
        self.invoke('repair-1', response)
        self.expose('event-2')
        self.register('repaired-final', 'repair-1', response)
        records = self.store.snapshot()['records']
        state = review_protocol.assignment_state(records, self.artifacts, 'repair-1')
        self.assertFalse(state['ready'])
        self.assertIn('review_context_repair_stale', [item['code'] for item in state['obligations']])
        self.probe('probe-2')
        saved = self.repair('repair-2', 'repair-1', ['event-1', 'event-2'], 'probe-2')['result']
        packet = json.loads(self.artifacts.read(saved['packet']))
        self.assertEqual(set(packet['context_repair']['historical_review_ids']), {'original-final', 'repaired-final'})
