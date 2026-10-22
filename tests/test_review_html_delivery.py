"""Prevent oversized native HTML from blocking delivery of its full extraction."""

import hashlib
import copy
import json
import unittest

from research_harness import review_transport as transport
from research_harness.review_codex_transport import packet_text


ROUTE = {'adapter': 'codex_cli_v1', 'endpoint': 'codex://local', 'model': 'gpt-6-astra',
         'configuration': {'max_output_tokens': 32000, 'timeout_seconds': 600}}


def entry(content, media_type):
    data = content.encode()
    sha = hashlib.sha256(data).hexdigest()
    return {'artifact': {'sha256': sha, 'path': 'objects/' + sha, 'size': len(data), 'media_type': media_type},
            'encoding': 'utf-8', 'content': content}


def packet(size=1100000, text_repeats=1):
    html = entry('<html>' + 'x' * size + '</html>', 'text/html')
    text = entry('An exact native extraction with mathematical content.' * text_repeats, 'text/plain; charset=utf-8')
    provenance = {'original_descriptor': html['artifact'], 'capture_method': 'http', 'origin_verified': True,
                  'completeness': 'complete', 'delivery_mode': 'extracted-text-only',
                  'delivered_sha256': [html['artifact']['sha256'], text['artifact']['sha256']]}
    return {'evidence': [html, text], 'source_deliveries': [
        {'request': {'depth': 'fulltext'}, 'source_provenance': [provenance],
         'source_scope_pending': [{'code': 'required_visual_unavailable'}]}]}


class HtmlDeliveryTests(unittest.TestCase):
    def test_repeated_fulltexts_survive_when_omitting_html_is_sufficient(self):
        value = packet()
        other = entry('Other complete paper. ' * 4000, 'text/plain')
        value['evidence'].append(other)
        value['previously_supplied_evidence'] = [item['artifact'] for item in value['evidence'][1:]]
        request, sent = self.delivered(value)
        self.assertLess(len(packet_text(request)), 1048576)
        self.assertEqual(sent['evidence'][1:], value['evidence'][1:])

    def test_html_omission_extraction_survives_later_reductions(self):
        for mode in ('previously_supplied_evidence', 'priority'):
            with self.subTest(mode=mode):
                value = packet(text_repeats=200)
                extraction = value['evidence'][1]
                value['evidence'].append(entry('Irreducible body ' * 100000, 'text/plain'))
                if mode == 'previously_supplied_evidence':
                    value[mode] = [extraction['artifact']]
                else:
                    value['native'] = {'scientific_context': {
                        'text_delivery_priority': [extraction['artifact']['sha256']]}}
                request, sent = self.delivered(value)
                self.assertEqual(sent['evidence'][1], extraction)
                self.assertGreater(len(packet_text(request)), 1048576)
                self.assertEqual(sent['evidence'][0]['content'], '')

    def delivered(self, value):
        request = transport.request_body(ROUTE, 'Review JSON.', value)
        return request, json.loads(request['input'][-1]['content'].split('\n', 1)[1])

    def test_oversized_native_html_preserves_full_extraction_and_explicit_omission(self):
        original = packet()
        request, sent = self.delivered(original)
        self.assertLess(len(packet_text(request)), 1048576)
        self.assertEqual(sent['evidence'][0]['content'], '')
        self.assertIn('html-original', sent['evidence'][0]['delivery'])
        self.assertTrue(sent['evidence'][0]['note'])
        self.assertEqual(sent['evidence'][1], original['evidence'][1])
        delivery = sent['source_deliveries'][0]
        self.assertEqual(delivery['source_scope_pending'], [{'code': 'required_visual_unavailable'}])
        self.assertEqual(delivery['transport_omissions'][0]['sha256'], original['evidence'][0]['artifact']['sha256'])
        self.assertEqual(original['evidence'][0]['content'][:6], '<html>')

    def test_small_original_is_not_elided(self):
        value = packet(50)
        _, sent = self.delivered(value)
        self.assertEqual(sent, value)

    def test_history_is_compacted_before_current_original_is_withheld(self):
        value = packet(50000)
        earlier = {'evidence': [entry('prior ' * 200000, 'text/plain')]}
        history = [{'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(earlier)}]
        request = transport.request_body(ROUTE, 'Review JSON.', value, history)
        sent = json.loads(request['input'][-1]['content'].split('\n', 1)[1])
        self.assertLess(len(packet_text(request)), 1048576)
        self.assertEqual(sent, value)

    def test_scientific_history_is_compacted_before_current_original(self):
        value = packet(50000, text_repeats=200)
        value['scientific_history'] = [{'packet': {
            'evidence': [entry('earlier body ' * 150000, 'text/plain')]}}]
        request, sent = self.delivered(value)
        self.assertLess(len(packet_text(request)), 1048576)
        self.assertEqual(sent['evidence'], value['evidence'])
        self.assertEqual(sent['scientific_history'][0]['packet']['evidence'][0]['content'], '')

    def test_omitted_original_is_not_claimed_delivered_by_repeated_source_units(self):
        value = packet()
        original_sha = value['evidence'][0]['artifact']['sha256']
        unit = copy.deepcopy(value['source_deliveries'][0]['source_provenance'][0])
        unit['kind'] = 'source_unit'
        value['source_deliveries'][0]['source_provenance'].append(unit)
        value['source_deliveries'].append(copy.deepcopy(value['source_deliveries'][0]))
        _, sent = self.delivered(value)
        for delivery in sent['source_deliveries']:
            self.assertEqual(delivery['transport_omissions'][0]['sha256'], original_sha)
            for provenance in delivery['source_provenance']:
                self.assertNotIn(original_sha, provenance['delivered_sha256'])
                self.assertIn(original_sha, provenance['captured_delivery_sha256'])

    def test_unbound_or_missing_extraction_is_not_elided(self):
        for mode in ('no-provenance', 'no-text', 'already-elided', 'source-unit', 'wrong-capture', 'corrupt-text'):
            with self.subTest(mode=mode):
                value = packet()
                provenance = value['source_deliveries'][0]['source_provenance'][0]
                if mode == 'no-provenance':
                    value['source_deliveries'] = []
                elif mode == 'no-text':
                    value['evidence'].pop()
                elif mode == 'already-elided':
                    value['evidence'][1]['content'] = ''
                    value['evidence'][1]['delivery'] = 'body-elided-route-limit'
                elif mode == 'source-unit':
                    provenance['kind'] = 'source_unit'
                elif mode == 'wrong-capture':
                    provenance['delivered_sha256'].pop()
                elif mode == 'corrupt-text':
                    value['evidence'][1]['content'] += ' changed'
                _, sent = self.delivered(value)
                self.assertEqual(sent['evidence'][0]['content'], value['evidence'][0]['content'])


if __name__ == '__main__':
    unittest.main()
