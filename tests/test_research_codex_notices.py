"""Disabled exec notices require exact, ordered call/output evidence from the observed rollout."""

import base64
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from codex_route_fixtures import record_codex_route
from literature_fixtures import LiteratureCase
from test_research_codex_controls import ADAPTERS, build_png
from research_harness import review_codex_transport as codex, review_codex_visual_transport as visual
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest


def read_disabled_exec():
    path = Path(__file__).parent / 'fixtures' / 'codex_disabled_exec.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()]


class CodexNoticeTests(LiteratureCase):
    def build_response(self, adapter, route, request, items):
        parts = (visual.native_user_parts(request, '/safe/work') if adapter is visual
                 else [{'type': 'input_text', 'text': adapter.packet_text(request)}])
        events = [
            {'type': 'session_meta', 'payload': {'id': 'thread', 'source': 'exec',
                'base_instructions': {'text': request['instructions']}}},
            {'type': 'turn_context', 'payload': {'cwd': '/safe/work', 'model': 'fixture-model', 'effort': 'high'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': parts}},
            *items,
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': '{"verdict":"sound"}'}]}},
        ]
        native = codex if adapter is visual else adapter
        with patch.object(native, 'NATIVE_DECLARATION_SETS', {()}):
            arguments = {'excluded_images': [b'excluded-image']} if adapter is visual else {}
            evidence = adapter.inspect_rollout(events, request, 'thread', '/safe/work', ['EXCLUDED_CONTROL'],
                                               **arguments)
        evidence.update(inspected=True, route_digest=digest(route), runtime=route['runtime'],
                        event_digest='cli-events')
        return {'id': 'thread', 'model': 'fixture-model', 'status': 'completed',
                'output': [events[-1]['payload']], 'context_evidence': evidence}, events

    def observe_items(self, adapter, items, *, change_rollout=None, read_error=None, extra_issues=()):
        runtime = {'adapter': adapter.ADAPTER, 'implementation_sha256': '1' * 64}
        route = {'id': 'route', 'adapter': adapter.ADAPTER, 'model': 'fixture-model',
                 'endpoint': 'codex://local', 'runtime': runtime,
                 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 5}}
        request = {'model': 'fixture-model', 'instructions': 'Review the supplied evidence.',
                   'input': [{'role': 'user', 'content': 'Evidence.'}]}
        if adapter is visual:
            data = build_png()
            request['exactory_image_inputs'] = [{'artifact': describe_artifact(data, 'image/png'),
                'data_url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')}]
        response, events = self.build_response(adapter, route, request, items)
        response['context_evidence']['issues'].extend(extra_issues)
        if extra_issues:
            response['context_evidence']['input_verified'] = False
        original = copy.deepcopy(response['context_evidence'])
        retained = copy.deepcopy(events)
        if change_rollout:
            change_rollout(retained)
        native = codex if adapter is visual else adapter
        with patch.object(adapter, 'runtime', return_value=runtime), \
                patch.object(transport, 'send_request', return_value=response), \
                patch.object(native, '_rollout', return_value=retained, side_effect=read_error):
            observed = protocol._observed(self.artifacts, route, request, None)
        saved = protocol._read_json(self.artifacts, observed['response'])
        return saved, protocol.verify_route_context(self.artifacts, route, request, saved), original, events

    def assert_verified_notice(self, adapter, items):
        response, verified, original, events = self.observe_items(adapter, items)
        evidence = response['context_evidence']
        self.assertTrue(verified, evidence)
        self.assertTrue(evidence['input_verified'])
        self.assertEqual(evidence['issues'], [])
        self.assertEqual(evidence['notices'], [
            {'event_index': index, 'event': event['payload']} for index, event in enumerate(events)
            if event.get('payload', {}).get('type') == 'custom_tool_call'])
        for key in original.keys() - {'input_verified', 'issues'}:
            self.assertEqual(evidence[key], original[key], key)

    def test_recorded_disabled_exec_is_saved_as_a_notice_for_every_codex_route(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER):
                self.assert_verified_notice(adapter, read_disabled_exec())

    def test_exec_requires_exact_output_and_an_unambiguous_preceding_call(self):
        call, output = read_disabled_exec()
        cases = [('recorded', [call, output], True)]
        for value in ('ran code', 'code-mode host is disabled\n', ' code-mode host is disabled',
                      'Code-mode host is disabled', '', None, ['code-mode host is disabled']):
            changed = copy.deepcopy(output)
            changed['payload']['output'] = value
            cases.append(('output ' + repr(value), [call, changed], False))
        for key in ('output', 'call_id'):
            changed = copy.deepcopy(output)
            del changed['payload'][key]
            cases.append(('missing ' + key, [call, changed], False))
        changed = copy.deepcopy(output)
        changed['payload']['call_id'] = 'another-call'
        cases.extend([('missing output', [call], False), ('early output', [output, call], False),
                      ('unmatched output', [call, changed], False),
                      ('repeated output', [call, output, output], False),
                      ('reused call id', [call, output, call, output], False)])
        for adapter in ADAPTERS:
            for name, items, expected in cases:
                with self.subTest(adapter=adapter.ADAPTER, case=name):
                    if expected:
                        self.assert_verified_notice(adapter, items)
                    else:
                        response, verified, original, _ = self.observe_items(adapter, items)
                        self.assertFalse(verified)
                        self.assertEqual(response['context_evidence'], original)
                        self.assertIn('tool_call:exec', original['issues'])

    def test_other_tools_and_unknown_inputs_keep_the_attempt_unverified(self):
        call, output = read_disabled_exec()
        cases = [('recorded', [call, output], None)]
        for kind, name, issue in [('custom_tool_call', 'js_repl', 'tool_call:js_repl'),
                                  ('custom_tool_call', 'wait', 'tool_call:wait'),
                                  ('custom_tool_call', None, 'tool_call:unknown'),
                                  ('function_call', 'exec', 'unknown_or_tool_input'),
                                  ('unknown_context', None, 'unknown_or_tool_input')]:
            item = {'type': 'response_item', 'payload': {'type': kind, 'name': name, 'call_id': 'other'}}
            cases.append((kind + ':' + str(name), [call, output, item], issue))
        for adapter in ADAPTERS:
            for name, items, issue in cases:
                with self.subTest(adapter=adapter.ADAPTER, case=name):
                    if issue is None:
                        self.assert_verified_notice(adapter, items)
                    else:
                        response, verified, original, _ = self.observe_items(adapter, items)
                        self.assertFalse(verified)
                        self.assertEqual(response['context_evidence'], original)
                        self.assertIn(issue, original['issues'])

    def test_multiple_exec_calls_each_need_their_own_ordered_output(self):
        first_call, first_output = read_disabled_exec()
        second_call, second_output = read_disabled_exec()
        second_call['payload']['call_id'] = second_output['payload']['call_id'] = 'second-call'
        for adapter in ADAPTERS:
            for items in ([first_call, first_output, second_call, second_output],
                          [first_call, second_call, second_output, first_output]):
                with self.subTest(adapter=adapter.ADAPTER, items=items):
                    self.assert_verified_notice(adapter, items)
            with self.subTest(adapter=adapter.ADAPTER, case='one output missing'):
                response, verified, original, _ = self.observe_items(
                    adapter, [first_call, first_output, second_call])
                self.assertFalse(verified)
                self.assertEqual(response['context_evidence'], original)

    def test_notice_reclassification_requires_the_exact_inspected_rollout(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER, case='recorded'):
                self.assert_verified_notice(adapter, read_disabled_exec())
            for options in ({'change_rollout': lambda events: events.reverse()},
                            {'change_rollout': lambda events: events.pop()},
                            {'read_error': OSError('unavailable')},
                            {'read_error': ResearchError('review_context_unverified', 'Unavailable')}):
                with self.subTest(adapter=adapter.ADAPTER, options=options):
                    response, verified, original, _ = self.observe_items(adapter, read_disabled_exec(), **options)
                    self.assertFalse(verified)
                    self.assertEqual(response['context_evidence'], original)

    def test_notices_preserve_other_context_issues(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER, case='recorded'):
                self.assert_verified_notice(adapter, read_disabled_exec())
            for issue in ('provider_output_mismatch', 'excluded_control_exposed', 'packet_or_history_mismatch'):
                with self.subTest(adapter=adapter.ADAPTER, issue=issue):
                    response, verified, _, _ = self.observe_items(
                        adapter, read_disabled_exec(), extra_issues=[issue])
                    evidence = response['context_evidence']
                    self.assertFalse(verified)
                    self.assertEqual(evidence['issues'], [issue])
                    self.assertEqual(len(evidence['notices']), 1)

    def test_saved_attempt_authenticates_without_rereading_the_rollout_and_cannot_be_resampled(self):
        record_codex_route(self)
        evidence = self.artifacts.put(b'Evidence for the review.', 'text/plain')
        self.mutate(protocol.record_assignment, {'id': 'assignment', 'route_id': 'route', 'dossier_id': None,
            'role': 'standalone', 'reviewer_id': 'reviewer', 'author_id': 'author',
            'context': {'artifact_refs': [evidence]}})
        retained = []

        def respond(route, request, credential):
            response, events = self.build_response(codex, route, request, read_disabled_exec())
            retained[:] = events
            return response

        with patch.object(transport, 'send_request', side_effect=respond), \
                patch.object(codex, '_rollout', side_effect=lambda *args: retained):
            self.mutate(protocol.invoke_assignment, {'id': 'attempt', 'assignment_id': 'assignment'})
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt']
        with patch.object(codex, '_rollout', side_effect=AssertionError('Must use saved evidence')):
            self.assertTrue(protocol.authenticate_attempt(
                records, self.artifacts, records['review_assignment']['assignment'], attempt))
            self.assertTrue(protocol.assignment_state(records, self.artifacts, 'assignment')['ready'])
            with self.assertRaises(ResearchError) as failure:
                self.mutate(protocol.invoke_assignment, {'id': 'retry', 'assignment_id': 'assignment'})
            self.assertEqual(failure.exception.code, 'review_resampling_forbidden')
        saved = protocol._read_json(self.artifacts, attempt['response'])
        self.assertEqual(len(saved['context_evidence']['notices']), 1)


if __name__ == '__main__':
    unittest.main()
