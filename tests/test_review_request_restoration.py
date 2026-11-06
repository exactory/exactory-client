"""Prospective refreshes give back exact current bodies after later reductions."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_request_evaluation as requests
from research_harness.artifacts import ArtifactStore
from research_harness.evaluation import Evaluation
from test_review_html_delivery import ROUTE, entry
import problem_parts_fixtures as fixtures


POLICY = 'exactory-review-request-restoration-v1'


def make_refresh_packet():
    bodies = [entry('Previously supplied source. λ"\\\n' * 180, 'text/plain'),
              entry('Another previously supplied source. ' * 700, 'text/plain')]
    return {'protocol': 'exactory-problem-review-v2', 'role': 'result', 'dossier_id': None,
            'reviewer_id': 'reviewer', 'subject': {'kind': 'problem', 'statement_id': 'statement'},
            'digest': 'a' * 64, 'problem_refresh': {'assignment_id': 'earlier-result'},
            'problem_evidence': {}, 'evidence': bodies,
            'previously_supplied_evidence': [body['artifact'] for body in bodies],
            'scientific_history': [{'assignment_id': 'earlier-result',
                'packet': {'digest': 'b' * 64, 'scientific_payload': 'history ' * 5000},
                'observed_output': {'finding': 'The witness remains unresolved.'},
                'independence': 'no_longer_qualified'}]}


class RequestRestorationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = ArtifactStore(Path(directory.name))
        self.packet = make_refresh_packet()
        self.assignment = {'id': 'refresh', 'route_id': 'route', 'role': 'result',
            'packet': protocol._put_json(self.artifacts, self.packet),
            'prompt': {'text': 'Review the JSON.'}, 'history': [], 'request_policy': POLICY}
        self.records = {'review_route': {'route': ROUTE}, 'review_attempt': {},
                        'review_assignment': {'refresh': self.assignment}}

    def build(self, assignment=None):
        return protocol.build_assignment_request(self.records, self.artifacts, assignment or self.assignment)

    def delivered(self, request):
        return json.loads(request['input'][-1]['content'][len(transport.PACKET_LABEL):])

    def test_refresh_restores_a_body_when_later_history_reduction_makes_room(self):
        before = copy.deepcopy(self.packet)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            legacy = transport.request_body(ROUTE, 'Review the JSON.', self.packet)
            self.assertEqual(self.delivered(legacy)['evidence'][0]['content'], '')
            actual = self.build()
            self.assertEqual(self.delivered(actual)['evidence'][0], self.packet['evidence'][0])
            self.assertEqual(self.delivered(actual)['evidence'][1]['content'], '')
            self.assertLessEqual(len(transport._local_adapter(ROUTE).packet_text(actual)), 12000)
        self.assertEqual(self.packet, before)

    def test_unversioned_assignment_rebuilds_old_request_bytes_for_every_order(self):
        old = dict(self.assignment)
        old.pop('request_policy')
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            candidates = list(requests.iter_built_requests(Evaluation(self.records, self.artifacts), ROUTE,
                old['prompt']['text'], self.packet))
            expected = list(transport.list_built_requests(ROUTE, old['prompt']['text'], self.packet))
            self.assertEqual(json.dumps(candidates, ensure_ascii=False), json.dumps(expected, ensure_ascii=False))
            self.assertEqual(self.build(old), expected[0])

    def test_retry_retains_the_exact_restored_request_and_independent_copies(self):
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            first = self.build()
            self.records['review_attempt']['attempt'] = {'assignment_id': 'refresh', 'status': 'completed',
                                                       'request': protocol._put_json(self.artifacts, first)}
            second = self.build()
            self.assertEqual(second, first)
            second['input'][-1]['content'] = 'caller mutation'
            self.assertEqual(self.build(), first)

    def test_required_current_sources_survive_and_omitted_content_gets_no_credit(self):
        required = entry('Required source. ' * 300, 'text/plain')
        for body in self.packet['evidence'] + [required]:
            body['artifact'] = self.artifacts.put(body['content'].encode('utf-8'), 'text/plain')
        self.packet['previously_supplied_evidence'] = [body['artifact'] for body in self.packet['evidence']]
        self.packet['evidence'].append(required)
        self.packet['source_deliveries'] = [{'request': {'id': 'required', 'kind': 'source',
            'version_id': 'version', 'depth': 'fulltext'}, 'status': 'delivered',
            'evidence': [required['artifact']]}]
        self.assignment['packet'] = protocol._put_json(self.artifacts, self.packet)
        from research_harness.review_input_projection import packets_from_request
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 16000):
            request = self.build()
            actual = packets_from_request(request)[-1]
            self.assertEqual(actual['evidence'][-1], required)
            self.assertEqual(actual['evidence'][0], self.packet['evidence'][0])
            self.assertEqual(actual['evidence'][1]['content'], '')
            self.assertEqual(actual['source_deliveries'], self.packet['source_deliveries'])
            from research_harness.review_delivered_evidence import project_delivery_view
            credit = project_delivery_view(self.records, self.artifacts, [actual])['artifacts']
            self.assertEqual(set(credit), {self.packet['evidence'][0]['artifact']['sha256'],
                                         required['artifact']['sha256']})

    def test_builder_substitutions_remain_effective_under_the_new_policy(self):
        sentinel = {'input': [{'role': 'user', 'content': 'emulated request'}]}
        with patch.object(requests, '_build_request', return_value=sentinel) as build:
            self.assertEqual(self.build(), sentinel)
        self.assertEqual(build.call_count, 1)

    def test_each_text_route_uses_its_fully_encoded_restoration_boundary(self):
        from research_harness import review_claude_transport as claude
        routes = [ROUTE, dict(ROUTE, adapter='codex_cli_framed_v1'),
                  dict(ROUTE, adapter='claude_cli_v1', endpoint=claude.ENDPOINT,
                       configuration=dict(ROUTE['configuration'], effort='high'))]
        from research_harness.review_request_restoration import restore_request
        for route in routes:
            adapter = transport._local_adapter(route)
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000), \
                    patch.object(claude, 'INPUT_LIMIT_CHARACTERS', 12000):
                reduced = transport.request_body(route, 'Review the JSON.', self.packet)
            supplied = self.delivered(reduced)
            supplied['evidence'][0] = self.packet['evidence'][0]
            trial = copy.deepcopy(reduced)
            trial['input'][-1]['content'] = transport.PACKET_LABEL + json.dumps(
                supplied, sort_keys=True, ensure_ascii=False)
            boundary = len(adapter.packet_text(trial))
            for limit in (boundary - 1, boundary, boundary + 1):
                with self.subTest(adapter=route['adapter'], limit=limit), \
                        patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', limit), \
                        patch.object(claude, 'INPUT_LIMIT_CHARACTERS', limit):
                    actual = restore_request(route, self.packet, reduced, POLICY)
                    self.assertEqual(self.delivered(actual)['evidence'][0]['content'],
                                     self.packet['evidence'][0]['content'] if limit >= boundary else '')
                    self.assertLessEqual(len(adapter.packet_text(actual)), limit)

    def test_cache_keeps_historical_and_prospective_policies_separate(self):
        evaluation = Evaluation(self.records, self.artifacts)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            arguments = (evaluation, ROUTE, 'Review the JSON.', self.packet, [], transport.FIT_ORDER)
            old = requests.request_for_order(*arguments)
            new = requests.request_for_order(*arguments, policy=POLICY)
            self.assertNotEqual(old, new)
            self.assertEqual(requests.request_for_order(*arguments), old)
            self.assertEqual(requests.request_for_order(*arguments, policy=POLICY), new)

    def test_capacity_deferrals_and_html_substitutions_cannot_be_restored_as_body_omissions(self):
        from research_harness.review_request_restoration import restore_request
        from test_review_html_delivery import packet as make_html_packet
        from test_review_source_capacity import source_packet
        for packet in (source_packet(), make_html_packet(20000, 120)):
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
                reduced = transport.request_body(ROUTE, 'Review the JSON.', packet)
            with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1000000):
                self.assertEqual(restore_request(ROUTE, packet, reduced, POLICY), reduced)

    def test_transport_wrapper_still_observes_the_pinned_fallback(self):
        from research_harness import review_request_fit
        native = transport.request_body
        with patch.object(review_request_fit, 'try_build_request', return_value=None), \
                patch.object(transport, 'request_body', wraps=native) as build, \
                patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            actual = self.build()
        self.assertEqual(build.call_count, 1)
        self.assertEqual(self.delivered(actual)['evidence'][0], self.packet['evidence'][0])

    def test_corrupt_or_conflicting_originals_cannot_gain_restoration_credit(self):
        from research_harness.review_request_restoration import restore_request
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 12000):
            reduced = transport.request_body(ROUTE, 'Review the JSON.', self.packet)
            for conflict in (False, True):
                with self.subTest(conflict=conflict):
                    altered = copy.deepcopy(self.packet)
                    if conflict:
                        altered['evidence'].append(copy.deepcopy(altered['evidence'][0]))
                        altered['evidence'][-1]['content'] += 'changed'
                    else:
                        altered['evidence'][0]['content'] += 'changed'
                    self.assertEqual(restore_request(ROUTE, altered, reduced, POLICY), reduced)

    def test_policy_metadata_cannot_silently_fall_back_on_another_representation(self):
        from research_harness.errors import ResearchError
        from research_harness.review_request_restoration import select_policy
        for adapter in ('openai_responses_v1', 'codex_cli_visual_v1'):
            route = dict(ROUTE, adapter=adapter)
            self.assertIsNone(select_policy(route, self.packet))
            with self.assertRaises(ResearchError) as failure:
                next(requests.iter_built_requests(Evaluation(self.records, self.artifacts), route,
                    'Review the JSON.', self.packet, policy=POLICY))
            self.assertEqual(failure.exception.code, 'review_request_policy_mismatch')

    def test_local_measurement_emulation_does_not_select_a_policy_for_other_routes(self):
        from research_harness.review_request_restoration import select_policy
        with patch.object(transport, 'local_input_limit', return_value=105000):
            for adapter in ('openai_responses_v1', 'codex_cli_visual_v1'):
                with self.subTest(adapter=adapter):
                    self.assertIsNone(select_policy(dict(ROUTE, adapter=adapter), self.packet))


class ProspectiveAssignmentTests(fixtures.ResultPartsCase):
    def test_new_assignment_records_the_policy_without_changing_prompt_or_packet(self):
        from research_harness.review_request_restoration import select_policy
        assignment = self.assign('prospective-policy')
        records = self.case.read_records()
        packet = protocol._read_json(self.case.artifacts, assignment['packet'])
        route = records['review_route'][assignment['route_id']]
        self.assertEqual(assignment['request_policy'], select_policy(route, packet))
        self.assertNotIn('request_policy', packet)
        self.assertTrue(protocol._prompt_matches(assignment))


if __name__ == '__main__':
    unittest.main()
