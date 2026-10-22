"""Whole requested sources cross capacity boundaries without acquiring false delivery credit."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_transport as transport
from research_harness.review_codex_transport import packet_text
from test_review_html_delivery import ROUTE, entry


def source_packet():
    bodies = [entry(str(i) + (' complete mathematical source %d.' % i) * 1200, 'text/plain')
              for i in range(3)]
    requests = [{'id': 'request-%d' % i, 'kind': 'source', 'version_id': 'arxiv:2601.0000%dv1' % i,
                 'depth': 'fulltext'} for i in range(3)]
    deliveries = []
    for request, body in zip(requests, bodies):
        deliveries.append({'request': request, 'status': 'delivered', 'reading_credit': False,
            'reason': 'All exact native captures are available.', 'evidence': [body['artifact']],
            'source_scope_pending': [], 'source_alternatives': [{'source_id': request['version_id']}],
            'source_provenance': [{'delivered_sha256': [body['artifact']['sha256']],
                                   'completeness': 'complete'}]})
    return {'digest': 'frozen-original-digest', 'role': 'result', 'continuation': {'assignment_id': 'prior'},
            'history_delivery': transport.HISTORY_COMPACT_V2,
            'source_deliveries': deliveries, 'evidence': bodies, 'pending_source_requests': []}


class SourceCapacityTests(unittest.TestCase):
    def send(self, value, limit=80000, history=()):
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit):
            request = transport.request_body(ROUTE, 'Review exact JSON evidence.', value, history)
        return request, json.loads(request['input'][-1]['content'].split('\n', 1)[1])

    def test_whole_groups_fit_and_deferred_requests_are_explicit(self):
        original = source_packet()
        request, sent = self.send(original)
        self.assertLessEqual(len(packet_text(request)), 80000)
        self.assertEqual(sent['digest'], original['digest'])
        self.assertTrue(sent['pending_source_requests'])
        admitted = []
        for actual, expected in zip(sent['source_deliveries'], original['source_deliveries']):
            if actual['status'] == 'delivered':
                admitted.append(actual['request']['id'])
                self.assertEqual(actual, expected)
            else:
                self.assertEqual(actual['status'], 'deferred_capacity')
                self.assertIn(actual['request'], sent['pending_source_requests'])
                self.assertFalse(actual['reading_credit'])
                self.assertEqual(actual['evidence'], [])
                self.assertEqual(actual['source_provenance'][0]['delivered_sha256'], [])
                self.assertEqual(actual['source_alternatives'], expected['source_alternatives'])
        self.assertTrue(admitted)
        self.assertEqual(original, source_packet())
        self.assertEqual(request, self.send(original)[0])

    def test_shared_source_and_non_source_artifacts_are_never_stripped(self):
        for owner in ('source', 'non-source'):
            with self.subTest(owner=owner):
                value = source_packet()
                shared = value['evidence'][-1]
                if owner == 'source':
                    value['source_deliveries'][0]['evidence'].append(shared['artifact'])
                    value['source_deliveries'][0]['source_provenance'][0]['delivered_sha256'].append(
                        shared['artifact']['sha256'])
                else:
                    value['claim_evidence'] = [shared['artifact']]
                request, sent = self.send(value, 85000)
                self.assertLessEqual(len(packet_text(request)), 85000)
                actual = next(row for row in sent['evidence'] if row['artifact'] == shared['artifact'])
                self.assertEqual(actual, shared)

    def test_preexisting_unavailable_and_partial_requests_stay_pending(self):
        value = source_packet()
        old = [{'id': 'unavailable', 'kind': 'source', 'version_id': 'arxiv:2601.00999v1', 'depth': 'fulltext'},
               {'id': 'partial', 'kind': 'source', 'version_id': 'arxiv:2601.00998v1', 'depth': 'fulltext'}]
        value['pending_source_requests'] = copy.deepcopy(old)
        for request, status in zip(old, ('unavailable', 'partial')):
            value['source_deliveries'].append({'request': request, 'status': status, 'evidence': [],
                                              'source_scope_pending': [{'code': status}]})
        _, sent = self.send(value)
        self.assertEqual(sent['pending_source_requests'][:2], old)
        self.assertGreater(len(sent['pending_source_requests']), 2)
        self.assertEqual(sent['source_deliveries'][-2:], value['source_deliveries'][-2:])

    def test_partly_deferred_group_keeps_its_shared_body_and_provenance(self):
        value = source_packet()
        shared, unique = value['evidence'][:2]
        delivery = value['source_deliveries'][1]
        delivery['evidence'].append(shared['artifact'])
        delivery['source_provenance'][0]['delivered_sha256'].append(shared['artifact']['sha256'])
        request, sent = self.send(value, 60000)
        self.assertLessEqual(len(packet_text(request)), 60000)
        deferred = sent['source_deliveries'][1]
        self.assertEqual(deferred['status'], 'deferred_capacity')
        self.assertEqual(deferred['evidence'], [shared['artifact']])
        self.assertEqual(deferred['source_provenance'][0]['delivered_sha256'], [shared['artifact']['sha256']])
        self.assertEqual(deferred['capacity_deferral']['withheld_sha256'], [unique['artifact']['sha256']])
        self.assertEqual(sent['evidence'][0], shared)
        self.assertEqual(sent['evidence'][1]['content'], '')

    def test_required_unit_is_kept_with_its_whole_source(self):
        value = source_packet()
        unit = entry('Required supplementary proof. ' * 1000, 'text/plain')
        value['evidence'].append(unit)
        source = value['source_deliveries'][0]
        source['evidence'].append(unit['artifact'])
        source['source_provenance'].append({'kind': 'source_unit', 'completeness': 'complete',
                                          'delivered_sha256': [unit['artifact']['sha256']]})
        request, sent = self.send(value, 60000)
        self.assertLessEqual(len(packet_text(request)), 60000)
        self.assertEqual(sent['source_deliveries'][0]['status'], 'deferred_capacity')
        self.assertEqual(sent['source_deliveries'][0]['evidence'], [])
        self.assertEqual(sent['evidence'][0]['content'], '')
        self.assertEqual(sent['evidence'][-1]['content'], '')
        self.assertTrue(any(item['status'] == 'delivered' for item in sent['source_deliveries'][1:]))

    def test_no_complete_source_fits_preserves_honest_oversized_failure(self):
        value = source_packet()
        request, sent = self.send(value, 15000)
        self.assertGreater(len(packet_text(request)), 15000)
        self.assertEqual(sent, value)

    def test_already_fitting_request_and_history_are_unchanged(self):
        value = source_packet()
        history = [{'role': 'user', 'content': 'Exact prior claim and search metadata.'},
                   {'role': 'assistant', 'content': 'An unresolved scientific objection.'}]
        request, sent = self.send(value, 200000, history)
        self.assertEqual(sent, value)
        self.assertEqual(request['input'][:-1], history)
        request, sent = self.send(value, 80000, history)
        self.assertLessEqual(len(packet_text(request)), 80000)
        self.assertEqual(request['input'][:-1], history)

    def test_initial_unrequested_sources_are_not_capacity_batched(self):
        value = source_packet()
        value.pop('continuation')
        request, sent = self.send(value)
        self.assertGreater(len(packet_text(request)), 80000)
        self.assertEqual(sent, value)


if __name__ == '__main__':
    unittest.main()
