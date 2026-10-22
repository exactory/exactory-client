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


    def test_a_repair_packet_whose_history_still_exceeds_the_limit_elides_the_earlier_packets(self):
        from research_harness import review_claude_transport
        limit = review_claude_transport.INPUT_LIMIT_CHARACTERS
        # The weight sits in the earlier packets' readings, not in evidence bodies, so body elision cannot fit the input.
        heavy = {'digest': 'h' * 64, 'problem_evidence': {'readings': [{'text': 'r' * limit}]}}
        light = {'digest': 'l' * 64, 'problem_evidence': {'readings': [{'text': 'r' * 1000}]}}
        big = {'id': 'now', 'scientific_history': [
            {'assignment_id': 'bar-a', 'observed_output': {'value': 'kept'}, 'independence': 'verified_for_route', 'packet': light},
            {'assignment_id': 'bar-b', 'observed_output': {'value': 'kept too'}, 'independence': 'verified_for_route', 'packet': heavy},
            {'assignment_id': 'bar-c', 'observed_output': {'value': 'kept as well'}, 'independence': 'verified_for_route',
             'packet': dict(heavy, digest='k' * 64)}]}
        request = transport.request_body(CLAUDE, 'Review.', big)
        supplied = packet_of(request['input'][-1])
        self.assertLessEqual(len(review_claude_transport.packet_text(request)), limit)
        turns = {turn['assignment_id']: turn for turn in supplied['scientific_history']}
        # The largest packets gave way first, each to a digest reference; the light one and every output stayed.
        for identifier, digest_value in (('bar-b', 'h' * 64), ('bar-c', 'k' * 64)):
            self.assertEqual(turns[identifier]['packet'], {'delivery': 'packet-elided-route-limit', 'packet_digest': digest_value,
                                                           'note': transport.HISTORY_PACKET_LIMIT_NOTE})
        self.assertEqual(turns['bar-a']['packet'], light)
        self.assertEqual([turn['observed_output'] for turn in supplied['scientific_history']],
                         [{'value': 'kept'}, {'value': 'kept too'}, {'value': 'kept as well'}])


    def test_a_replayed_turn_whose_packet_still_exceeds_the_limit_gives_way_to_its_digest(self):
        from research_harness import review_claude_transport
        limit = review_claude_transport.INPUT_LIMIT_CHARACTERS
        # The weight of the earlier turn sits in its readings, which no body elision reaches.
        earlier = {'digest': 'e' * 64, 'history_delivery': transport.HISTORY_COMPACT_V2,
                   'problem_evidence': {'readings': [{'text': 'r' * limit}]}}
        history = [{'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(earlier, sort_keys=True)},
                   {'role': 'assistant', 'content': '{"value": "the earlier answer"}'}]
        current = {'id': 'now', 'history_delivery': transport.HISTORY_COMPACT_V2, 'problem_evidence': {'readings': [{'text': 'r' * 1000}]}}
        request = transport.request_body(CLAUDE, 'Review.', current, history)
        self.assertLessEqual(len(review_claude_transport.packet_text(request)), limit)
        replayed = packet_of(request['input'][0])
        self.assertEqual(replayed, {'delivery': 'packet-elided-route-limit', 'packet_digest': 'e' * 64,
                                    'history_delivery': transport.HISTORY_COMPACT_V2, 'note': transport.HISTORY_PACKET_LIMIT_NOTE})
        self.assertEqual(request['input'][1], history[1])
        self.assertEqual(packet_of(request['input'][-1]), current)
        # A history that fits is replayed as it was compacted, not reduced to a digest.
        small_history = [{'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(dict(earlier, problem_evidence={'readings': [{'text': 'r' * 500}]}), sort_keys=True)}]
        kept = transport.request_body(CLAUDE, 'Review.', current, small_history)
        self.assertIn('problem_evidence', packet_of(kept['input'][0]))
class ProblemSearchResponseElisionTests(unittest.TestCase):
    """A problem packet attaches the captured responses of the searches that its openness finding binds."""

    def routes(self):
        from research_harness import review_claude_transport, review_codex_transport
        return ((CLAUDE, review_claude_transport.INPUT_LIMIT_CHARACTERS, review_claude_transport.packet_text),
                (CODEX, transport.CODEX_INPUT_LIMIT_CHARS, review_codex_transport.packet_text))

    def problem_packet(self, responses, others=(), history=None):
        """Responses and others are (name, length) pairs; only the responses belong to a bound search."""
        evidence = [{'artifact': artifact(name, 'application/json'), 'content': name[0] * length, 'encoding': 'utf-8'}
                    for name, length in [*responses, *others]]
        sources = [{'id': 'acq:' + name, 'provider': 'openalex', 'response': artifact(name, 'application/json')}
                   for name, _ in responses]
        packet = {'id': 'now', 'history_delivery': transport.HISTORY_COMPACT_V2, 'evidence': evidence,
                  'problem_evidence': {'openness_searches': [{'formulation': 'The statement.', 'search_digest': 'sd',
                                                              'search': {'id': 'search-1'}, 'sources': sources}]}}
        if history is not None:
            packet['scientific_history'] = history
        return packet

    def test_the_route_limit_elides_search_responses_largest_first_until_the_input_fits(self):
        for route, limit, measure in self.routes():
            with self.subTest(route=route['adapter']):
                sizes = {'a1': int(limit * 0.30), 'b2': int(limit * 0.45), 'c3': int(limit * 0.10), 'd4': int(limit * 0.60)}
                packet = self.problem_packet([('a1', sizes['a1']), ('b2', sizes['b2']), ('c3', sizes['c3'])],
                                             others=[('d4', sizes['d4'])])
                request = transport.request_body(route, 'Review.', packet)
                delivered = {entry['artifact']['sha256'][:2]: entry for entry in packet_of(request['input'][-1])['evidence']}
                for name in ('b2', 'a1'):
                    self.assertEqual(delivered[name]['content'], '')
                    self.assertEqual(delivered[name]['delivery'], 'body-elided-route-limit')
                    self.assertEqual(delivered[name]['note'], transport.ELIDED_SEARCH_RESPONSE_NOTE)
                self.assertEqual(delivered['c3']['content'], 'c' * sizes['c3'])
                self.assertEqual(delivered['d4']['content'], 'd' * sizes['d4'])
                self.assertLessEqual(len(measure(request)), limit)
                self.assertEqual(packet['evidence'][1]['content'], 'b' * sizes['b2'])
                self.assertEqual(transport.request_body(route, 'Review.', packet), request)

    def test_a_problem_packet_within_the_limit_keeps_its_search_responses(self):
        for route, _, _ in self.routes():
            with self.subTest(route=route['adapter']):
                packet = self.problem_packet([('a1', 9000)], others=[('d4', 9000)])
                delivered = packet_of(transport.request_body(route, 'Review.', packet)['input'][-1])['evidence']
                self.assertEqual([entry['content'] for entry in delivered], ['a' * 9000, 'd' * 9000])
                self.assertEqual([entry.get('delivery') for entry in delivered], [None, None])

    def test_search_responses_give_way_before_the_packets_of_the_slot_history(self):
        for route, limit, measure in self.routes():
            with self.subTest(route=route['adapter']):
                notes = 'h' * int(limit * 0.3)
                history = [{'assignment_id': 'bar-1', 'packet': {'digest': 'h1', 'notes': notes},
                            'observed_output': {'stage': 'bar'}}]
                packet = self.problem_packet([('a1', int(limit * 0.9))], history=history)
                request = transport.request_body(route, 'Review.', packet)
                supplied = packet_of(request['input'][-1])
                self.assertEqual(supplied['evidence'][0]['delivery'], 'body-elided-route-limit')
                self.assertEqual(supplied['scientific_history'][0]['packet'], {'digest': 'h1', 'notes': notes})
                self.assertLessEqual(len(measure(request)), limit)


class ProblemFullTextElisionTests(unittest.TestCase):
    """A problem packet delivers each full reading as its record, notes and inspected passages; the full text of a work
    that the packet cites through a source link can give way at the route limit."""

    def problem_packet(self, response_length, read_length, unread_length):
        evidence = [{'artifact': artifact('re', 'application/json'), 'content': 'r' * response_length, 'encoding': 'utf-8'},
                    {'artifact': artifact('tx'), 'content': 'x' * read_length, 'encoding': 'utf-8'},
                    {'artifact': artifact('ty'), 'content': 'y' * unread_length, 'encoding': 'utf-8'}]
        return {'id': 'now', 'history_delivery': transport.HISTORY_COMPACT_V2, 'evidence': evidence,
                'objection': {'evidence': [{'kind': 'source', 'link': {'version_id': 'arxiv:read', 'artifact_reference_template': {'artifact_descriptor': artifact('tx')}}},
                                           {'kind': 'source', 'link': {'version_id': 'arxiv:unread', 'artifact': artifact('ty')}}]},
                'problem_evidence': {
                    'openness_searches': [{'formulation': 'The statement.', 'search_digest': 'sd', 'search': {'id': 'search-1'},
                                           'sources': [{'id': 'acq:re', 'response': artifact('re', 'application/json')}]}],
                    'readings': [{'id': 'reading-1', 'version_id': 'arxiv:read', 'depth': 'fulltext'}]}}

    def delivered(self, request):
        return {entry['artifact']['sha256'][:2]: entry for entry in packet_of(request['input'][-1])['evidence']}

    def test_a_read_full_text_gives_way_after_the_search_responses(self):
        from research_harness import review_claude_transport
        limit = review_claude_transport.INPUT_LIMIT_CHARACTERS
        request = transport.request_body(CLAUDE, 'Review.', self.problem_packet(int(limit * 0.2), int(limit * 0.6), int(limit * 0.45)))
        delivered = self.delivered(request)
        self.assertEqual(delivered['re']['delivery'], 'body-elided-route-limit')
        self.assertEqual(delivered['tx']['delivery'], 'body-elided-route-limit')
        self.assertEqual(delivered['tx']['note'], transport.ELIDED_TEXT_NOTE)
        self.assertEqual(delivered['ty']['content'], 'y' * int(limit * 0.45))
        self.assertLessEqual(len(review_claude_transport.packet_text(request)), limit)

    def test_another_body_of_a_problem_packet_gives_way_last(self):
        from research_harness import review_claude_transport
        limit = review_claude_transport.INPUT_LIMIT_CHARACTERS
        request = transport.request_body(CLAUDE, 'Review.', self.problem_packet(int(limit * 0.2), int(limit * 0.6), int(limit * 1.05)))
        delivered = self.delivered(request)
        self.assertEqual([delivered[name].get('delivery') for name in ('re', 'tx', 'ty')], ['body-elided-route-limit'] * 3)
        self.assertEqual(delivered['ty']['note'], transport.ELIDED_EVIDENCE_NOTE)
        self.assertLessEqual(len(review_claude_transport.packet_text(request)), limit)

    def test_a_read_full_text_stays_when_the_search_responses_suffice(self):
        from research_harness import review_claude_transport
        limit = review_claude_transport.INPUT_LIMIT_CHARACTERS
        request = transport.request_body(CLAUDE, 'Review.', self.problem_packet(int(limit * 0.6), int(limit * 0.3), int(limit * 0.2)))
        delivered = self.delivered(request)
        self.assertEqual(delivered['re']['delivery'], 'body-elided-route-limit')
        self.assertEqual(delivered['tx']['content'], 'x' * int(limit * 0.3))


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
        legacy = review_protocol.canonical_prompt('result', version=2, orchestration='problem_creation')
        current = review_protocol.canonical_prompt('result', orchestration='problem_creation')
        self.assertNotEqual(previous['text'], current['text'])
        self.assertEqual(previous['template_id'], legacy['template_id'])
        self.assertEqual(current['template_id'], 'exactory-problem-review-v4/result')
        self.assertIn('with record_kind, id and digest copied from the delivery', previous['text'])
        assignment = {'role': 'result', 'subject': {'kind': 'problem', 'statement_id': 's'}, 'prompt': previous}
        self.assertTrue(review_protocol.is_previous_prompt(assignment))
        self.assertTrue(review_protocol._prompt_matches(assignment))
        edited = dict(assignment, prompt=dict(previous, text=previous['text'] + ' '))
        self.assertFalse(review_protocol.is_previous_prompt(edited))
        repaired = {'role': 'result', 'subject': {'kind': 'problem', 'statement_id': 's'}, 'reassessment_required': True,
                    'prompt': review_protocol.previous_problem_result_prompt(repair=True)}
        self.assertTrue(review_protocol.is_previous_prompt(repaired))
        self.assertEqual(repaired['prompt']['template_id'], legacy['template_id'] + '/context-repair')

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
