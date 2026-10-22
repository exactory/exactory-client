"""Only the same authenticated result slot may add native visual delivery."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness import review_protocol, review_transport


class VisualRouteTests(unittest.TestCase):
    def setUp(self):
        import test_research_visual_sources as fixtures
        fixture = fixtures.VisualSourceTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.artifacts = fixture.artifacts
        self.fixture = fixture
        self.native_records = fixture.records
        self.visual = fixture.prepared()['visual_source_delivery']
        self.packet = {'role': 'result', 'subject': {'kind': 'problem', 'id': 'statement'},
                       'bar_ids': ['a', 'b'], 'bars': [{'id': 'a'}, {'id': 'b'}],
                       'problem_evidence_digest': 'same-evidence',
                       'problem_evidence': {'readings': [{'id': fixture.native_reading['id'],
                                                         'digest': digest(fixture.native_reading)}]},
                       'complete_source_delivery': {'protocol': 'exactory-review-complete-source-v1'}}
        self.request = {'input': [{'role': 'user', 'content': review_transport.PACKET_LABEL + json.dumps(self.packet)}]}
        self.output = {'value': {'status': 'supported'}, 'objections': []}
        self.prior = {'id': 'prior', 'role': 'result', 'route_id': 'old', 'reviewer_id': 'Astra',
                      'author_id': 'Author', 'dossier_id': None, 'slot_id': 'slot', 'subject': self.packet['subject'],
                      'context': {'source_delivery': 'complete-visible-v1'},
                      'packet': self.artifacts.put(json.dumps(self.packet).encode(), 'application/json')}
        self.route = {'id': 'visual', 'adapter': 'codex_cli_visual_v1', 'model': 'gpt-6-astra', 'endpoint': 'codex://local'}
        self.records = {'review_assignment': {'prior': self.prior},
                        'review_route': {'old': dict(self.route, id='old', adapter='codex_cli_v1'), 'visual': self.route},
                        'review_attempt': {'attempt': {'id': 'attempt', 'request_digest': digest(self.request),
                        'request': self.artifacts.put(json.dumps(self.request).encode(), 'application/json')}}}
        self.records.update(self.native_records)
        current = copy.deepcopy(self.packet); current['visual_source_delivery'] = self.visual
        self.context = {'packet': current, 'history': self.request['input'] + [
            {'role': 'assistant', 'content': json.dumps(self.output, sort_keys=True)}], 'reviewer_id': 'Astra',
            'author_id': 'Author', 'slot_id': 'slot', 'subject': self.packet['subject'], 'role': 'result'}
        for name, value in [('assignment_state', {'ready': True, 'attempt_id': 'attempt', 'output': self.output}),
                            ('authenticate_attempt', True), ('route_state', {'context_status': 'verified_for_route', 'probe_id': 'probe'})]:
            active = patch.object(review_protocol, name, return_value=value); active.start(); self.addCleanup(active.stop)

    def validate(self, context=None, route=None):
        from research_harness.review_visual_route import validate_transition
        return validate_transition(self.records, self.artifacts, self.prior, route or self.route, context or self.context)

    def test_verified_same_slot_preserves_complete_prior_history(self):
        bridge = self.validate()
        self.assertEqual(bridge['previous_assignment_id'], 'prior')
        self.assertEqual(bridge['history_digest'], digest(self.context['history']))
        self.assertEqual(bridge['probe_id'], 'probe')
        self.assertEqual(bridge['model'], 'gpt-6-astra')

    def test_changed_model_reviewer_author_slot_subject_bars_or_history_is_rejected(self):
        for key, value in [('reviewer_id', 'Sol'), ('author_id', 'Other'), ('slot_id', 'new-slot'),
                           ('subject', {'kind': 'problem', 'id': 'different'}), ('role', 'bar')]:
            with self.subTest(key=key), self.assertRaises(ResearchError):
                self.validate(dict(self.context, **{key: value}))
        for key, value in [('model', 'gpt-6-sol'), ('adapter', 'claude_cli_v1')]:
            with self.subTest(key=key), self.assertRaises(ResearchError):
                self.validate(route=dict(self.route, **{key: value}))
        for change in ('bars', 'history', 'latest-output', 'evidence'):
            context = copy.deepcopy(self.context)
            if change == 'bars': context['packet']['bar_ids'] = ['a', 'different']
            if change == 'history': context['history'].pop(0)
            if change == 'latest-output': context['history'][-1]['content'] = '{}'
            if change == 'evidence': context['packet']['problem_evidence_digest'] = 'changed'
            with self.subTest(change=change), self.assertRaises(ResearchError):
                self.validate(context)

    def test_missing_required_native_images_cannot_justify_route_change(self):
        missing = copy.deepcopy(self.context); missing['packet'].pop('visual_source_delivery')
        with self.assertRaises(ResearchError):
            self.validate(missing)
        empty = copy.deepcopy(self.context); empty['packet']['visual_source_delivery']['pages'] = []
        with self.assertRaises(ResearchError):
            self.validate(empty)
        forged = copy.deepcopy(self.context); forged['packet']['visual_source_delivery']['readings'][0]['digest'] = '0' * 64
        with self.assertRaises(ResearchError):
            self.validate(forged)

    def test_valid_unrelated_reading_cannot_justify_route_change(self):
        from research_harness import reading
        from research_harness.review_visual_sources import prepare_packet
        self.fixture.mutate(reading.record_reading, self.fixture.full_note(self.fixture.bundle_value, note_id='unrelated'))
        self.records.update(self.fixture.store.snapshot()['records'])
        unrelated = self.records['reading']['unrelated']
        foreign = {'problem_evidence': {'readings': [{'id': unrelated['id'], 'digest': digest(unrelated)}]}, 'evidence': []}
        prepare_packet(self.records, self.artifacts, foreign)
        changed = copy.deepcopy(self.context)
        changed['packet']['visual_source_delivery'] = foreign['visual_source_delivery']
        with self.assertRaises(ResearchError):
            self.validate(changed)

    def test_unverified_route_or_predecessor_cannot_create_bridge(self):
        with patch.object(review_protocol, 'route_state', return_value={'context_status': 'unverified', 'probe_id': None}):
            with self.assertRaises(ResearchError): self.validate()
        with patch.object(review_protocol, 'authenticate_attempt', return_value=False):
            with self.assertRaises(ResearchError): self.validate()
