"""Direction coverage comes from each reviewer's actual deciding input."""

import json
from unittest.mock import patch

from strategy_fixtures import StrategyCase
from research_harness import direction_reviews, review_protocol as protocol, review_transport as transport
from research_harness.evidence import digest
from research_harness.review_input_projection import packets_from_request


class DirectionActualDeliveryTests(StrategyCase):
    def setUp(self):
        super().setUp()
        original = transport.request_body

        def build_local_request(route, prompt, packet, history=(), order=transport.FIT_ORDER):
            return original(dict(route, adapter='codex_cli_v1', endpoint='codex://local'),
                            prompt, packet, history, order)

        projection = patch.object(transport, 'request_body', side_effect=build_local_request)
        projection.start()
        self.addCleanup(projection.stop)
        # Exercise every existing omission step with small, real study fixtures.
        limit = patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1)
        limit.start()
        self.addCleanup(limit.stop)

    def change_slate_packet(self, change):
        original = protocol.build_packet

        def build_packet(*args, **kwargs):
            packet = original(*args, **kwargs)
            if packet['role'] == 'slate' and packet['reviewer_id'] == 'reviewer-a':
                change(packet)
                packet.pop('digest')
                packet['digest'] = digest(packet)
            return packet

        return patch.object(protocol, 'build_packet', side_effect=build_packet)

    def record_continued_pair(self, change):
        self.prepare_direction()
        self.setup_dossier()
        self.route()
        for suffix in ('a', 'b'):
            self.record_response('bar-' + suffix, 'reviewer-' + suffix, self.review_response('bar'))
        for suffix in ('a', 'b'):
            identifier = 'history-' + suffix
            self.mutate(protocol.record_assignment, dict(id=identifier, route_id='route', dossier_id='dossier-1',
                role='slate', reviewer_id='reviewer-' + suffix, author_id='author', context={}))
            output = self.review_response(status='unresolved')
            output['source_requests'] = [dict(id='inventory', kind='inventory_page', page=0)]
            with patch.object(transport, 'send_request', return_value=self.provider_response(output)):
                self.mutate(protocol.invoke_assignment, dict(id='attempt-' + identifier, assignment_id=identifier),
                            credential='fixture-only')
        with self.change_slate_packet(change):
            for suffix in ('a', 'b'):
                self.record_response('slate-' + suffix, 'reviewer-' + suffix, self.review_response(),
                                     context={'prior_assignment_id': 'history-' + suffix})

    def assess_reviews(self, records=None):
        records = self.store.snapshot()['records'] if records is None else records
        for suffix in ('a', 'b'):
            state = protocol.assignment_state(records, self.artifacts, 'assignment-slate-' + suffix)
            self.assertTrue(state['ready'], state['obligations'])
        direction = self.module('claim_minimums').describe_direction(records, self.artifacts)
        return direction_reviews.assess_direction_reviews(records, self.artifacts,
            records['strategy_dossier']['dossier-1'],
            [records['value_review']['slate-' + suffix] for suffix in ('a', 'b')], direction)

    def read_packets(self, records=None):
        records = self.store.snapshot()['records'] if records is None else records
        assignment = records['review_assignment']['assignment-slate-a']
        saved = json.loads(self.artifacts.read(assignment['packet']))
        attempt = records['review_attempt']['attempt-slate-a']
        request = json.loads(self.artifacts.read(attempt['request']))
        return saved, packets_from_request(request)

    def test_elided_decisive_passage_is_unassessed_for_its_reviewer(self):
        def omit_current_bodies(packet):
            packet['evidence'] = [entry for entry in packet['evidence'] if entry['artifact'] == self.evidence]

        self.record_continued_pair(omit_current_bodies)
        saved, actual = self.read_packets()
        excerpt = saved['direction_evidence']['passages'][0]['excerpt']
        records = self.store.snapshot()['records']
        historical = json.loads(self.artifacts.read(records['review_assignment']['history-a']['packet']))
        self.assertTrue(next(entry['content'] for entry in historical['evidence'] if entry['artifact'] == excerpt))
        self.assertEqual(len(actual), 1)
        self.assertTrue(all(not entry.get('content') for packet in actual
                            for entry in packet['evidence'] if entry['artifact'] == excerpt))
        obligations = self.assess_reviews()
        self.assertEqual([(row['code'], row['review_id'], row['missing']) for row in obligations],
                         [('direction_evidence_unassessed', 'slate-a', [])])
        self.assertEqual(obligations[0]['reading_ids'],
                         sorted(row['id'] for row in saved['direction_evidence']['readings']))

    def test_elided_history_cannot_restore_direction_bindings(self):
        def omit_bindings(packet):
            for key in ('claim', 'search', 'standards', 'grand_challenge'):
                packet['direction_evidence'].pop(key)

        self.record_continued_pair(omit_bindings)
        saved, actual = self.read_packets()
        self.assertEqual(len(actual), 1)
        records = self.store.snapshot()['records']
        parent = records['review_assignment']['history-a']
        historical = json.loads(self.artifacts.read(parent['packet']))
        for key in ('claim', 'search', 'standards', 'grand_challenge'):
            self.assertNotIn(key, saved['direction_evidence'])
            self.assertIsNotNone(historical['direction_evidence'][key])
        obligations = self.assess_reviews()
        self.assertEqual([(row['code'], row['review_id'], row['missing'], row['reading_ids'])
                          for row in obligations],
                         [('direction_evidence_unassessed', 'slate-a',
                           ['claim', 'grand_challenge', 'search', 'standards'], [])])

    def test_current_delivery_keeps_direction_coverage_after_history_elision(self):
        self.reviewed_dossier()
        _, actual = self.read_packets()
        self.assertEqual(len(actual), 1)
        self.assertEqual(self.assess_reviews(), [])

    def test_retained_actual_history_can_supply_missing_current_bindings(self):
        def omit_current_direction(packet):
            packet.pop('direction_evidence')
            packet['evidence'] = [entry for entry in packet['evidence'] if entry['artifact'] == self.evidence]

        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1048576):
            self.record_continued_pair(omit_current_direction)
            saved, actual = self.read_packets()
            self.assertNotIn('direction_evidence', saved)
            self.assertGreater(len(actual), 1)
            self.assertEqual(self.assess_reviews(), [])

    def test_saved_visual_asset_without_actual_bytes_is_unassessed(self):
        from test_research_decisions import DecisionContractTests
        asset = DecisionContractTests.prepare_visual_direction(self)
        self.reviewed_dossier()
        saved, actual = self.read_packets()
        self.assertTrue(any(entry['artifact'] == asset['artifact'] and entry.get('content')
                            for entry in saved['evidence']))
        self.assertTrue(all(not entry.get('content') for packet in actual
                            for entry in packet['evidence'] if entry['artifact'] == asset['artifact']))
        obligations = self.assess_reviews()
        self.assertEqual({row['review_id'] for row in obligations}, {'slate-a', 'slate-b'})
        self.assertTrue(all(row['code'] == 'direction_evidence_unassessed'
                            and row['missing'] == [] and row['reading_ids'] == ['html-visual-reading']
                            for row in obligations))
