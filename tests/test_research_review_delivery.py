"""Reviewer requests on local routes: labelled packets, omitted bytes, compacted history and bounded elision."""

import json
import unittest

from research_harness import review_transport as transport
from research_harness import strategy
from research_harness.errors import ResearchError
from research_harness.evidence import digest

CODEX = {'id': 'route-codex', 'adapter': 'codex_cli_v1', 'model': 'gpt-6-astra', 'endpoint': 'codex://local',
         'configuration': {'max_output_tokens': 20000, 'timeout_seconds': 600}}
CLAUDE = {'id': 'route-claude', 'adapter': 'claude_cli_v1', 'model': 'claude-opus-5-5', 'endpoint': 'claude://local',
          'configuration': {'effort': 'max', 'max_output_tokens': 20000, 'timeout_seconds': 1800}}
RESPONSES = {'id': 'route-api', 'adapter': 'openai_responses_v1', 'model': 'gpt-6', 'endpoint': 'https://api.example.test/v1/responses',
             'configuration': {'max_output_tokens': 20000, 'timeout_seconds': 600}}


def artifact(name, media_type='text/plain'):
    return {'path': 'objects/' + name, 'sha256': name * 16, 'size': 3, 'media_type': media_type}


def user_turn(packet):
    return {'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(packet, sort_keys=True, ensure_ascii=False)}


def packet_of(message):
    return json.loads(message['content'][len(transport.PACKET_LABEL):])


class PacketShapeTests(unittest.TestCase):
    def test_every_route_labels_the_packet_as_json(self):
        for route in (CODEX, CLAUDE, RESPONSES):
            with self.subTest(route=route['adapter']):
                request = transport.request_body(route, 'Review.', {'id': 'packet'})
                content = request['input'][-1]['content']
                self.assertTrue(content.startswith(transport.PACKET_LABEL))
                self.assertEqual(json.loads(content[len(transport.PACKET_LABEL):]), {'id': 'packet'})
                self.assertEqual(request['tools'], [])
                self.assertIs(request['store'], False)

    def test_text_only_routes_name_binary_evidence_without_its_bytes(self):
        packet = {'evidence': [{'artifact': artifact('ab', 'application/pdf'), 'content': 'QUJD', 'encoding': 'base64'},
                               {'artifact': artifact('cd'), 'content': 'hello', 'encoding': 'utf-8'}]}
        for route in (CODEX, CLAUDE):
            with self.subTest(route=route['adapter']):
                delivered = packet_of(transport.request_body(route, 'Review.', packet)['input'][-1])['evidence']
                self.assertEqual(delivered[0]['encoding'], 'omitted')
                self.assertEqual(delivered[0]['delivery'], 'bytes-omitted-on-text-route')
                self.assertEqual(delivered[0]['note'], transport.OMITTED_BYTES_NOTE)
                self.assertNotIn('content', delivered[0])
                self.assertEqual(delivered[1]['content'], 'hello')
        api = transport.request_body(RESPONSES, 'Review.', packet)['input'][-1]['content']
        self.assertEqual(api[1]['type'], 'input_file')
        self.assertIn('QUJD', api[1]['file_data'])


class HistoryCompactionTests(unittest.TestCase):
    def earlier_turn(self):
        return user_turn({'evidence': [{'artifact': artifact('aa'), 'content': 'x' * 5000, 'encoding': 'utf-8'},
                                       {'artifact': artifact('bb'), 'content': 'short', 'encoding': 'utf-8'}],
                          'scientific_history': [{'assignment_id': 'earlier', 'packet': {'digest': 'd1', 'evidence': []},
                                                  'observed_output': {'stage': 'bar'}}],
                          'bars': [{'id': 'bar-1'}], 'source_inventory': {'digest': 'inv'},
                          'source_deliveries': [{'request': {'id': 'r1', 'kind': 'source'}, 'status': 'delivered',
                                                 'evidence': [artifact('aa')]}]})

    def test_a_packet_without_a_marker_replays_its_history_unchanged(self):
        history = [self.earlier_turn(), {'role': 'assistant', 'content': '{}'}]
        request = transport.request_body(CLAUDE, 'Review.', {'id': 'now'}, history)
        self.assertEqual(request['input'][:2], history)

    def test_the_first_marker_drops_long_bodies_and_history_packets(self):
        history = [self.earlier_turn(), {'role': 'assistant', 'content': '{}'}]
        packet = {'id': 'now', 'history_delivery': transport.HISTORY_COMPACT, 'bars': [{'id': 'bar-1'}]}
        replayed = packet_of(transport.request_body(CLAUDE, 'Review.', packet, history)['input'][0])
        self.assertEqual(replayed['evidence'][0]['content'], '')
        self.assertEqual(replayed['evidence'][0]['delivery'], 'body-elided-from-replayed-history')
        self.assertEqual(replayed['evidence'][0]['note'], transport.HISTORY_BODY_NOTE)
        self.assertEqual(replayed['evidence'][1]['content'], 'short')
        self.assertEqual(replayed['scientific_history'][0]['packet']['delivery'], 'packet-elided-from-replayed-history')
        self.assertEqual(replayed['scientific_history'][0]['packet']['packet_digest'], 'd1')
        self.assertEqual(replayed['scientific_history'][0]['observed_output'], {'stage': 'bar'})
        self.assertEqual(replayed['bars'], [{'id': 'bar-1'}])
        self.assertEqual(replayed['source_deliveries'][0]['evidence'], [artifact('aa')])

    def test_the_second_marker_also_drops_repeated_parts_and_summarizes_deliveries(self):
        history = [self.earlier_turn(), {'role': 'assistant', 'content': '{}'}]
        packet = {'id': 'now', 'history_delivery': transport.HISTORY_COMPACT_V2, 'bars': [{'id': 'bar-1'}],
                  'source_inventory': {'digest': 'other'}}
        replayed = packet_of(transport.request_body(CLAUDE, 'Review.', packet, history)['input'][0])
        self.assertEqual(replayed['bars']['delivery'], 'repeated-in-the-current-packet')
        self.assertEqual(replayed['source_inventory'], {'digest': 'inv'})
        delivery = replayed['source_deliveries'][0]
        self.assertEqual(delivery['request'], {'id': 'r1', 'kind': 'source'})
        self.assertEqual(delivery['status'], 'delivered')
        self.assertEqual(delivery['delivery'], 'summarized-in-replayed-history')
        self.assertNotIn('evidence', delivery)

    def test_the_codex_route_elides_until_the_input_fits_and_then_sends_as_it_stands(self):
        big = {'id': 'now', 'evidence': [{'artifact': artifact('cc'), 'content': 'y' * 1100000, 'encoding': 'utf-8'}],
               'previously_supplied_evidence': [{'sha256': 'cc' * 16}],
               'scientific_history': [{'packet': {'evidence': [{'artifact': artifact('dd'), 'content': 'z' * 500000,
                                                                 'encoding': 'utf-8'}]}}]}
        request = transport.request_body(CODEX, 'Review.', big)
        supplied = packet_of(request['input'][-1])
        self.assertEqual(supplied['scientific_history'][0]['packet']['evidence'][0]['delivery'], 'body-elided-from-replayed-history')
        self.assertEqual(supplied['evidence'][0]['delivery'], 'body-elided-previously-supplied')
        self.assertLessEqual(len(json.dumps(request['input'], ensure_ascii=False)), transport.CODEX_INPUT_LIMIT_CHARS)
        unfittable = {'id': 'now', 'text': 'w' * (transport.CODEX_INPUT_LIMIT_CHARS + 10)}
        sent = transport.request_body(CODEX, 'Review.', unfittable)
        self.assertEqual(packet_of(sent['input'][-1])['text'], unfittable['text'])


class ClaudeRouteElisionTests(unittest.TestCase):
    def test_the_claude_route_elides_the_same_way_against_its_own_limit(self):
        from research_harness import review_claude_transport
        limit = review_claude_transport.INPUT_LIMIT_CHARACTERS
        big = {'id': 'now', 'history_delivery': transport.HISTORY_COMPACT_V2,
               'scientific_history': [{'packet': {'evidence': [{'artifact': artifact('ee'), 'content': 'z' * (limit + 1000),
                                                                 'encoding': 'utf-8'}]}}]}
        request = transport.request_body(CLAUDE, 'Review.', big)
        supplied = packet_of(request['input'][-1])
        self.assertEqual(supplied['scientific_history'][0]['packet']['evidence'][0]['delivery'], 'body-elided-from-replayed-history')
        self.assertLessEqual(len(review_claude_transport.packet_text(request)), limit)
        small = {'id': 'now', 'scientific_history': [{'packet': {'evidence': [{'artifact': artifact('ff'), 'content': 'z' * 9000,
                                                                                 'encoding': 'utf-8'}]}}]}
        kept = packet_of(transport.request_body(CLAUDE, 'Review.', small)['input'][-1])
        self.assertEqual(kept['scientific_history'][0]['packet']['evidence'][0]['content'], 'z' * 9000)


class ReviewCitationTests(unittest.TestCase):
    def records(self):
        return {'problem_value': {'value-1': {'id': 'value-1', 'motivation': []}}}

    def test_a_review_cites_a_record_by_kind_and_id(self):
        records = self.records()
        reference = {'kind': 'record', 'record_kind': 'problem_value', 'id': 'value-1'}
        strategy.evidence(records, None, [reference], review_binding=True)
        with self.assertRaises(ResearchError) as refused:
            strategy.evidence(records, None, [reference])
        self.assertEqual(refused.exception.code, 'invalid_strategy')
        hyphenated = dict(reference, record_kind='problem-value')
        strategy.evidence(records, None, [hyphenated], review_binding=True)
        with self.assertRaises(ResearchError):
            strategy.evidence(records, None, [dict(reference, id='value-2')], review_binding=True)
        stale = dict(reference, digest='0' * 64)
        with self.assertRaises(ResearchError) as refused:
            strategy.evidence(records, None, [stale], review_binding=True)
        self.assertEqual(refused.exception.code, 'strategy_evidence_stale')

    def test_a_carried_record_is_found_without_a_digest_at_its_stored_version(self):
        from research_harness.review_protocol import find_carried_record
        records = self.records()
        stored = digest(records['problem_value']['value-1'])
        carried = [('problem_value', 'value-1', stored, {'id': 'value-1'}, []),
                   ('problem_value', 'value-1', '1' * 64, {'id': 'value-1', 'older': True}, [])]
        found = find_carried_record(records, carried, {'kind': 'record', 'record_kind': 'problem-value', 'id': 'value-1'})
        self.assertEqual(found, ({'id': 'value-1'}, []))
        self.assertIsNone(find_carried_record(records, carried, {'kind': 'record', 'record_kind': 'problem_value', 'id': 'value-9'}))
        older = find_carried_record(records, carried, {'kind': 'record', 'record_kind': 'problem_value', 'id': 'value-1',
                                                       'digest': '1' * 64})
        self.assertEqual(older, ({'id': 'value-1', 'older': True}, []))


class OpennessAndSearchShapeTests(unittest.TestCase):
    def test_web_search_responses_name_their_query_parameters(self):
        from research_harness.search_pages import QUERY_PARAMETER_NAMES
        self.assertIn('search_string', QUERY_PARAMETER_NAMES['web'])
        self.assertIn('q', QUERY_PARAMETER_NAMES['web'])


if __name__ == '__main__':
    unittest.main()


class PromptAndDeliveryCompatibilityTests(unittest.TestCase):
    def test_the_result_prompt_of_0_55_is_a_recognized_previous_prompt(self):
        from research_harness import review_protocol
        previous = review_protocol.previous_problem_result_prompt()
        current = review_protocol.canonical_prompt('result', orchestration='problem_creation')
        self.assertNotEqual(previous['text'], current['text'])
        self.assertEqual(previous['template_id'], current['template_id'])
        self.assertIn('with record_kind, id and digest copied from the delivery', previous['text'])
        assignment = {'role': 'result', 'subject': {'kind': 'problem', 'statement_id': 's'}, 'prompt': previous}
        self.assertTrue(review_protocol.is_previous_prompt(assignment))
        self.assertTrue(review_protocol._prompt_matches(assignment))
        edited = dict(assignment, prompt=dict(previous, text=previous['text'] + ' '))
        self.assertFalse(review_protocol.is_previous_prompt(edited))
        repaired = {'role': 'result', 'subject': {'kind': 'problem', 'statement_id': 's'}, 'reassessment_required': True,
                    'prompt': review_protocol.previous_problem_result_prompt(repair=True)}
        self.assertTrue(review_protocol.is_previous_prompt(repaired))
        self.assertEqual(repaired['prompt']['template_id'], current['template_id'] + '/context-repair')

    def test_text_only_delivery_names_binary_originals_without_their_bytes(self):
        import tempfile
        from pathlib import Path
        from research_harness import review_protocol
        from research_harness.artifacts import ArtifactStore
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory))
            reference = store.put(b'%PDF-1.4 fixture', 'application/pdf')
            self.assertFalse(transport.text_only_enabled())
            with transport.text_only_delivery(True):
                self.assertTrue(transport.text_only_enabled())
                omitted = review_protocol._evidence(store, {'cited': reference})
            self.assertFalse(transport.text_only_enabled())
            self.assertEqual(omitted[0]['encoding'], 'omitted')
            self.assertEqual(omitted[0]['delivery'], 'bytes-omitted-on-text-route')
            self.assertEqual(omitted[0]['content'], '')
            attached = review_protocol._evidence(store, {'cited': reference})
            self.assertEqual(attached[0]['encoding'], 'base64')
