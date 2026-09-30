"""Source integrity and corrections bind the evidence actually supplied."""

import copy
from unittest.mock import patch

import test_research_bar_sources as fixtures
from literature_fixtures import LiteratureCase
from strategy_fixtures import StrategyCase
from research_harness import bar_sources, research_decisions, review_protocol
from research_harness.errors import ResearchError
from research_harness.reading import record_reading


class SourceIntegrityTests(StrategyCase):
    metadata = LiteratureCase.metadata
    capture = LiteratureCase.capture
    span = LiteratureCase.span
    link = LiteratureCase.link
    abstract_note = LiteratureCase.abstract_note
    assign = fixtures.BarSourceTests.assign
    packet = fixtures.BarSourceTests.packet
    invoke = fixtures.BarSourceTests.invoke
    request = fixtures.BarSourceTests.request

    def setUp(self):
        super().setUp()
        self.sequence = 0
        self.setup_dossier()
        self.route()

    def corrupt_inventory(self):
        descriptor = self.packet()['source_inventory']['artifact_descriptor']
        path = self.root / descriptor['location']
        path.chmod(0o600)
        path.write_bytes(b'corrupted complete inventory')

    def test_inventory_corruption_refuses_dispatch_before_network_or_reservation(self):
        self.metadata(1)
        self.assign()
        self.corrupt_inventory()
        revision = self.store.revision
        with patch('research_harness.review_transport.send_request', side_effect=AssertionError('must not dispatch')):
            with self.assertRaises(ResearchError):
                review_protocol.invoke_assignment(self.store, {'id': 'attempt', 'assignment_id': 'a'},
                    expected_revision=revision, request_id='invoke-corrupt', credential='fixture-only')
        self.assertEqual(self.store.revision, revision)
        self.assertFalse(self.store.snapshot()['records'].get('review_invocation'))

    def test_inventory_corruption_removes_current_eligibility_and_final_recording(self):
        self.metadata(1)
        self.assign()
        response = self.review_response('bar')
        self.invoke(response)
        self.corrupt_inventory()
        state = review_protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, 'a')
        self.assertFalse(state['ready'])
        self.assertEqual(state['output'], response)
        with self.assertRaises(ResearchError):
            self.mutate(research_decisions.record_value_review,
                dict(response, id='bar-a', dossier_id='dossier-1', assignment_id='a'))
        self.assertFalse(self.store.snapshot()['records'].get('value_review'))

    def test_fault_injected_capture_original_cannot_replace_native_response(self):
        source = self.metadata(1)
        self.capture(source)
        records = copy.deepcopy(self.store.snapshot()['records'])
        records['work'][source]['fulltexts'][0]['original'] = self.artifacts.put(b'Unrelated replacement.', 'text/plain')
        with self.assertRaises(ResearchError):
            bar_sources._source(records, self.artifacts,
                {'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}, {})

    def test_fault_injected_other_work_capture_cannot_supply_an_exact_version(self):
        source = self.metadata(1)
        other = self.metadata(2)
        self.capture(other)
        records = copy.deepcopy(self.store.snapshot()['records'])
        records['work'][source]['fulltexts'] = copy.deepcopy(records['work'][other]['fulltexts'])
        with self.assertRaises(ResearchError):
            bar_sources._source(records, self.artifacts,
                {'id': 'body', 'kind': 'source', 'version_id': source, 'depth': 'fulltext'}, {})

    def passage_reviews(self):
        source = self.metadata(1, abstract='EXACT INSPECTED PASSAGE. NEVER SUPPLIED ORIGINAL TAIL.')
        self.mutate(record_reading, self.abstract_note(source))
        abstract = self.store.snapshot()['records']['work'][source]['abstracts'][0]
        link = self.link(source, abstract['source_id'], abstract['artifact'], 'EXACT INSPECTED PASSAGE.')
        for name in ('a', 'b'):
            self.assign(name, reviewer='reviewer-' + name, source_links=[link])
            response = self.review_response('bar')
            response['value']['evidence'] = [abstract['artifact']]
            if name == 'a':
                response['objections'] = [{'id': 'objection', 'claim': 'The passage was not supplied.',
                    'reason': 'The source context lacked the exact passage.', 'evidence': [abstract['artifact']],
                    'resolution_condition': 'Show the exact passage in the recorded original packet.'}]
            self.invoke(response, name)
            self.mutate(research_decisions.record_value_review,
                dict(response, id='bar-' + name, dossier_id='dossier-1', assignment_id=name))
        return source, abstract, link

    def correction(self, evidence):
        return self.mutate(review_protocol.record_assignment, {'id': 'adjudicator', 'route_id': 'route',
            'dossier_id': 'dossier-1', 'role': 'adjudicator', 'reviewer_id': 'focused', 'author_id': 'author',
            'context': {'review_ids': ['bar-a', 'bar-b'], 'objection_id': 'objection', 'correction': {
                'kind': 'misreading', 'claim': 'The exact passage was supplied.', 'reason': 'The immutable packet contains it.',
                'evidence': [evidence], 'prior_adjudication_id': None, 'new_error_explanation': None}}})

    def test_exact_supplied_passage_is_admissible_without_releasing_unseen_original(self):
        import json
        _, _, link = self.passage_reviews()
        self.correction(link)
        packet = self.packet('adjudicator')
        encoded = json.dumps(packet)
        self.assertIn('EXACT INSPECTED PASSAGE.', encoded)
        self.assertNotIn('NEVER SUPPLIED ORIGINAL TAIL.', encoded)

    def test_correction_cannot_extend_the_original_supplied_passage(self):
        source, abstract, _ = self.passage_reviews()
        outside = self.link(source, abstract['source_id'], abstract['artifact'], 'NEVER SUPPLIED ORIGINAL TAIL.')
        self.error('invalid_review_correction', lambda: self.correction(outside))

    def test_unscoped_original_reference_cannot_expand_a_supplied_excerpt(self):
        _, abstract, _ = self.passage_reviews()
        self.error('invalid_review_correction', lambda: self.correction(abstract['artifact']))
