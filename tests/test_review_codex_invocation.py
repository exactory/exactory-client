"""Plain and framed calls retain and authenticate actual native settings."""

import copy
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import codex_route_fixtures
import test_research_review_protocol as protocol_fixtures
from research_harness import review_codex_transport as plain
from research_harness import review_codex_framed_transport as framed
from research_harness import review_transport as transport
from research_harness.evidence import digest


class CodexInvocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='invocation-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = str(Path(temporary.name).resolve())
        self.request = {'model': 'fixture-model', 'instructions': 'Inspect the supplied evidence.',
                        'input': [{'role': 'user', 'content': 'Synthetic scientific evidence.'}]}
        self.runtime = {'adapter': plain.ADAPTER, 'version': 'fixture-native-settings'}
        self.route = {'id': 'route', 'adapter': plain.ADAPTER, 'endpoint': 'codex://local',
                      'model': 'fixture-model', 'configuration': {
                          'max_output_tokens': 1000, 'timeout_seconds': 5}, 'runtime': self.runtime}
        for adapter in (plain, framed):
            declarations = patch.object(adapter, 'NATIVE_DECLARATION_SETS', {()})
            declarations.start()
            self.addCleanup(declarations.stop)
        self.events = self.build_events(plain)

    def read_facade(self):
        name = 'research_harness.review_codex_invocation'
        return importlib.import_module(name) if importlib.util.find_spec(name) else None

    def build_events(self, adapter):
        return [
            {'type': 'session_meta', 'payload': {'id': 'thread-fixture', 'source': 'exec',
                'base_instructions': {'text': self.request['instructions']}}},
            {'type': 'turn_context', 'payload': {'model': 'fixture-model', 'effort': 'high',
                                               'cwd': self.directory}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': adapter.packet_text(self.request)}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': '{"finding":"retained"}'}]}},
        ]

    def build_response(self, events=None, adapter=plain):
        events = self.events if events is None else events
        evidence = adapter.inspect_rollout(events, self.request, 'thread-fixture', self.directory, [])
        evidence.update(inspected=True, runtime=self.runtime, route_digest=digest(self.route),
                        event_digest='native-stream-digest')
        return {'id': 'thread-fixture', 'model': 'fixture-model', 'status': 'completed',
                'usage': {'input_tokens': 10, 'output_tokens': 5},
                'output': [{'type': 'message', 'role': 'assistant', 'content': [
                    {'type': 'output_text', 'text': '{"finding":"retained"}'}]}],
                'context_evidence': evidence}

    def capture(self, response, events):
        facade = self.read_facade()
        return (facade.capture_response(self.route, self.request, response, events)
                if facade is not None else copy.deepcopy(response))

    def verify(self, response, required=True):
        facade = self.read_facade()
        return (facade.verify_context(self.route, self.request, response, required=required)
                if facade is not None else transport.context_verified(self.route, self.request, response))

    def test_wrong_native_model_is_rejected_for_plain_and_framed(self):
        for adapter in (plain, framed):
            with self.subTest(adapter=adapter.ADAPTER):
                self.route['adapter'] = adapter.ADAPTER
                events = self.build_events(adapter)
                events[1]['payload']['model'] = 'another-model'
                response = self.build_response(events, adapter)
                self.assertTrue(adapter.verify_context(self.route, self.request, response))
                self.assertFalse(self.verify(self.capture(response, events)),
                                 'The native model must authenticate, not the echoed route model.')

    def test_wrong_adapter_required_effort_is_rejected_for_plain_and_framed(self):
        for adapter in (plain, framed):
            with self.subTest(adapter=adapter.ADAPTER):
                self.route['adapter'] = adapter.ADAPTER
                events = self.build_events(adapter)
                events[1]['payload']['effort'] = 'xhigh'
                response = self.build_response(events, adapter)
                self.assertTrue(adapter.verify_context(self.route, self.request, response))
                self.assertFalse(self.verify(self.capture(response, events)),
                                 'Both unchanged adapters require actual high effort.')

    def test_missing_or_duplicate_native_turn_context_is_rejected(self):
        for adapter in (plain, framed):
            self.route['adapter'] = adapter.ADAPTER
            complete = self.build_events(adapter)
            for events in ([event for event in complete if event['type'] != 'turn_context'],
                           complete + [copy.deepcopy(complete[1])]):
                with self.subTest(adapter=adapter.ADAPTER, contexts=len(events)):
                    response = self.build_response(events, adapter)
                    self.assertTrue(adapter.verify_context(self.route, self.request, response))
                    self.assertFalse(self.verify(self.capture(response, events)),
                                     'Exactly one actual native turn context is required.')

    def test_matching_settings_retain_native_context_and_sender_bytes(self):
        facade = self.read_facade()
        self.assertIsNotNone(facade, 'Versioned native invocation capture must exist.')
        for adapter in (plain, framed):
            self.route['adapter'] = adapter.ADAPTER
            events = self.build_events(adapter)
            response = self.build_response(events, adapter)
            original = copy.deepcopy(response)
            captured = self.capture(response, events)
            self.assertEqual(response, original)
            self.assertEqual(captured['codex_invocation'], {
                'protocol': facade.PROTOCOL, 'native_rollout': events})
            self.assertEqual({key: value for key, value in captured.items()
                              if key != 'codex_invocation'}, original)
            self.assertTrue(self.verify(captured))
            self.assertTrue(self.verify(json.loads(json.dumps(captured))))

    def test_legacy_authentication_does_not_invent_settings_evidence(self):
        events = [event for event in self.events if event['type'] != 'turn_context']
        response = self.build_response(events)
        original = copy.deepcopy(response)
        self.assertTrue(self.verify(response, required=False))
        self.assertEqual(response, original)
        self.assertNotIn('codex_invocation', response)
        self.assertFalse(self.verify(response, required=True),
                         'A prospective invocation cannot fall back to legacy authentication.')

    def test_metadata_binding_accepts_only_legacy_or_the_exact_protocol(self):
        facade = self.read_facade()
        self.assertTrue(facade.has_supported_binding({}, {facade.KEY: None},
                                                     {facade.KEY: facade.PROTOCOL}))
        for invalid in ('unsupported-v2', '', False, 1, {}, []):
            with self.subTest(binding=invalid):
                self.assertFalse(facade.has_supported_binding(
                    {facade.KEY: facade.PROTOCOL}, {facade.KEY: invalid}))

    def test_retained_rollout_and_correction_digest_cannot_be_substituted(self):
        response = self.build_response()
        captured = self.capture(response, self.events)
        self.assertTrue(self.verify(captured))
        for change in ('context', 'packet', 'output', 'thread', 'digest'):
            with self.subTest(change=change):
                altered = copy.deepcopy(captured)
                self.assertIn('codex_invocation', altered, 'The native rollout must be retained.')
                events = altered['codex_invocation']['native_rollout']
                if change == 'context':
                    events[1]['payload']['model'] = 'another-model'
                elif change == 'packet':
                    events[2]['payload']['content'][0]['text'] = 'Substituted evidence.'
                elif change == 'output':
                    events[3]['payload']['content'][0]['text'] = '{"finding":"changed"}'
                elif change == 'thread':
                    events[0]['payload']['id'] = 'another-thread'
                else:
                    altered['context_evidence']['rollout_digest'] = 'wrong'
                self.assertFalse(self.verify(altered))

    def test_rebound_rollout_digest_still_checks_input_and_output(self):
        captured = self.capture(self.build_response(), self.events)
        self.assertIn('codex_invocation', captured, 'The native rollout must be retained.')
        for event_index, value in ((2, 'Different evidence.'), (3, '{"finding":"changed"}')):
            with self.subTest(event=event_index):
                altered = copy.deepcopy(captured)
                events = altered['codex_invocation']['native_rollout']
                events[event_index]['payload']['content'][0]['text'] = value
                altered['context_evidence']['rollout_digest'] = digest(events)
                self.assertFalse(self.verify(altered))
        altered = copy.deepcopy(captured)
        altered['output'][0]['content'][0]['text'] = '{"finding":"changed"}'
        self.assertFalse(self.verify(altered))

    def test_malformed_native_record_shapes_are_rejected_without_reader_exceptions(self):
        for adapter in (plain, framed):
            self.route['adapter'] = adapter.ADAPTER
            events = self.build_events(adapter)
            response = self.capture(self.build_response(events, adapter), events)
            changes = []
            for invalid in (None, '', [], 3, {'text': None}, {'text': []}):
                changes.append((0, 'base_instructions', invalid))
            for invalid in (None, {}, [None], [{'type': 'input_text', 'text': None}]):
                changes.append((2, 'content', invalid))
            for index, key, invalid in changes:
                with self.subTest(adapter=adapter.ADAPTER, field=key, value=invalid):
                    altered = copy.deepcopy(response)
                    native = altered['codex_invocation']['native_rollout']
                    native[index]['payload'][key] = invalid
                    altered['context_evidence']['rollout_digest'] = digest(native)
                    self.assertFalse(self.verify(altered))
                    captured = self.capture(altered, native)
                    self.assertEqual(captured['codex_invocation'], {
                        'protocol': self.read_facade().PROTOCOL, 'native_rollout': native})
                    self.assertFalse(self.verify(captured))

    def test_request_route_and_response_model_remain_exact(self):
        captured = self.capture(self.build_response(), self.events)
        self.assertTrue(self.verify(captured))
        original_route = copy.deepcopy(self.route)
        original_request = copy.deepcopy(self.request)
        for target in ('request', 'route', 'response'):
            with self.subTest(target=target):
                altered = copy.deepcopy(captured)
                self.route = copy.deepcopy(original_route)
                self.request = copy.deepcopy(original_request)
                if target == 'request':
                    self.request['model'] = 'another-model'
                elif target == 'route':
                    self.route['model'] = 'another-model'
                else:
                    altered['model'] = 'another-model'
                self.assertFalse(self.verify(altered))

    def test_capture_reads_the_persistent_rollout_after_unchanged_sender_returns(self):
        facade = self.read_facade()
        self.assertIsNotNone(facade, 'Versioned native invocation capture must exist.')
        for adapter in (plain, framed):
            with self.subTest(adapter=adapter.ADAPTER):
                self.route['adapter'] = adapter.ADAPTER
                self.runtime['adapter'] = adapter.ADAPTER
                observed_events = []
                native_commands = []

                def execute(command, **kwargs):
                    native_commands.append(command)
                    events = self.build_events(adapter)
                    events[1]['payload']['cwd'] = str(Path(kwargs['directory']).resolve())
                    observed_events[:] = events
                    stream = [
                        {'type': 'thread.started', 'thread_id': 'thread-fixture'},
                        {'type': 'item.completed', 'item': {'type': 'agent_message',
                                                          'text': '{"finding":"retained"}'}},
                        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}},
                    ]
                    return subprocess.CompletedProcess(command, 0, '\n'.join(map(json.dumps, stream)), '')

                with patch.object(adapter, 'runtime', return_value=self.runtime), \
                        patch.object(adapter, '_run_owned', side_effect=execute) as execute_mock, \
                        patch.object(adapter, '_rollout', side_effect=lambda *args: copy.deepcopy(observed_events)), \
                        patch.object(plain, '_rollout', side_effect=lambda *args: copy.deepcopy(observed_events)):
                    observed = facade.observe(self.route, self.request, 'fixture-credential')
                self.assertEqual(execute_mock.call_count, 1)
                self.assertEqual(observed['status'], 'completed')
                self.assertEqual(native_commands[0][native_commands[0].index('-m') + 1], 'fixture-model')
                self.assertIn('model_reasoning_effort="high"', native_commands[0])
                self.assertEqual(observed['response']['codex_invocation']['native_rollout'], observed_events)
                self.assertTrue(self.verify(observed['response']))

    def test_failed_native_capture_is_retained_without_invented_context(self):
        facade = self.read_facade()
        self.assertIsNotNone(facade, 'Versioned native invocation capture must exist.')
        response = self.build_response()
        observed = {'status': 'completed', 'response': response, 'output': {'finding': 'retained'}}
        with patch.object(plain, '_rollout', side_effect=OSError('fixture unavailable')):
            captured = facade.capture_observation(self.route, self.request, observed)
        self.assertEqual(observed['response'], response)
        self.assertEqual(captured['response']['context_evidence'], response['context_evidence'])
        self.assertNotIn('native_rollout', captured['response']['codex_invocation'])
        self.assertFalse(self.verify(captured['response']))

    def test_disabled_exec_notice_keeps_raw_capture_and_authenticates_exact_normalization(self):
        from research_harness import review_codex_notices as notices
        for adapter in (plain, framed):
            with self.subTest(adapter=adapter.ADAPTER):
                self.route['adapter'] = adapter.ADAPTER
                events = self.build_events(adapter)
                events[3:3] = [
                    {'type': 'response_item', 'payload': {'type': 'custom_tool_call',
                        'name': 'exec', 'call_id': 'disabled-call', 'input': '1 + 1'}},
                    {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output',
                        'call_id': 'disabled-call', 'output': 'code-mode host is disabled'}},
                ]
                response = self.build_response(events, adapter)
                original = copy.deepcopy(response['context_evidence'])
                self.assertFalse(original['input_verified'])
                captured = self.capture(response, events)
                self.assertEqual(captured['codex_invocation'], {
                    'protocol': self.read_facade().PROTOCOL, 'native_rollout': events})
                self.assertEqual(captured['context_evidence'], original)
                with patch.object(adapter, '_rollout', return_value=events):
                    notices.record_disabled_exec_notices(self.route, captured)
                self.assertTrue(captured['context_evidence']['input_verified'])
                with patch.object(adapter, '_rollout', side_effect=AssertionError('Use retained native evidence')):
                    self.assertTrue(self.verify(captured))
                for field, value in (('event_index', 2), ('event', {'type': 'different-call'})):
                    altered = copy.deepcopy(captured)
                    altered['context_evidence']['notices'][0][field] = value
                    self.assertFalse(self.verify(altered))
                for key, value in (('model', 'another-model'), ('effort', 'xhigh')):
                    altered = copy.deepcopy(captured)
                    native = altered['codex_invocation']['native_rollout']
                    native[1]['payload'][key] = value
                    altered['context_evidence']['rollout_digest'] = digest(native)
                    self.assertFalse(self.verify(altered))

    def test_capture_uses_the_exact_adapter_rollout_reader_and_clean_environment(self):
        facade = self.read_facade()
        for adapter, other in ((plain, framed), (framed, plain)):
            with self.subTest(adapter=adapter.ADAPTER):
                self.route['adapter'] = adapter.ADAPTER
                events = self.build_events(adapter)
                observed = {'status': 'completed', 'response': self.build_response(events, adapter),
                            'output': {'finding': 'retained'}}
                environment = {'HOME': '/fixture/native-home'}
                with patch.object(adapter, 'clean_environment', return_value=environment), \
                        patch.object(adapter, '_rollout', return_value=events) as reader, \
                        patch.object(other, '_rollout', side_effect=AssertionError('Use the exact adapter')):
                    captured = facade.capture_observation(self.route, self.request, observed)
                reader.assert_called_once_with('thread-fixture', environment)
                self.assertEqual(captured['response']['codex_invocation']['native_rollout'], events)

    def test_visual_and_non_codex_observations_stay_unchanged(self):
        facade = self.read_facade()
        self.assertIsNotNone(facade, 'Versioned native invocation capture must exist.')
        observed = {'status': 'completed', 'response': self.build_response(), 'output': {'finding': 'retained'}}
        for adapter in ('codex_cli_visual_v1', 'openai_responses_v1'):
            route = dict(self.route, adapter=adapter)
            with patch.object(plain, '_rollout') as read_rollout:
                self.assertEqual(facade.capture_observation(route, self.request, observed), observed)
            read_rollout.assert_not_called()
            for verified in (True, False):
                with patch.object(transport, 'context_verified', return_value=verified) as verify:
                    self.assertEqual(facade.verify_context(route, self.request, observed['response'],
                                                          required=True), verified)
                verify.assert_called_once_with(route, self.request, observed['response'])


class CodexInvocationProtocolTests(unittest.TestCase):
    def prepare_case(self, adapter):
        case = protocol_fixtures.ReviewProtocolTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        codex_route_fixtures.install_codex_runtime(case)
        case.mutate(case.protocol().record_route, {
            'id': 'route', 'adapter': adapter.ADAPTER, 'model': 'fixture-model',
            'endpoint': 'codex://local', 'configuration': {
                'max_output_tokens': 1000, 'timeout_seconds': 5}})
        return case

    def call_observed(self, case, adapter, operation, payload, output, change=None):
        events = []

        def respond(route, request, credential):
            actual_output = output(request) if callable(output) else output
            response = case.response(actual_output)
            events[:] = [
                {'type': 'session_meta', 'payload': {'id': response['id'], 'source': 'exec',
                    'base_instructions': {'text': request['instructions']}}},
                {'type': 'turn_context', 'payload': {'model': route['model'], 'effort': 'high',
                                                   'cwd': '/fixture'}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                    'content': [{'type': 'input_text', 'text': adapter.packet_text(request)}]}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': json.dumps(actual_output)}]}},
            ]
            if change is not None:
                change(events)
            evidence = adapter.inspect_rollout(events, request, response['id'], '/fixture', [])
            self.assertTrue(evidence['input_verified'], evidence['issues'])
            evidence.update(inspected=True, route_digest=digest(route), runtime=adapter.runtime(),
                            event_digest=digest(events))
            response['context_evidence'] = evidence
            return response

        with patch.object(transport, 'send_request', side_effect=respond) as sender, \
                patch.object(adapter, '_rollout', side_effect=lambda *args: copy.deepcopy(events)):
            result = case.mutate(operation, payload, credential='fixture-only')
        self.assertEqual(sender.call_count, 1)
        return result['result']

    def probe_case(self, case, adapter, change=None):
        def output(request):
            packet = json.loads(request['input'][0]['content'][len(transport.PACKET_LABEL):])
            return {'allowed_control': packet['allowed_control'],
                    'evidence_control': packet['evidence_control'], 'excluded_controls': []}
        return self.call_observed(case, adapter, case.protocol().probe_route,
                                  {'id': 'probe', 'route_id': 'route'}, output, change)

    def test_public_calls_pin_capture_and_matching_settings_become_ready(self):
        from research_harness import review_codex_invocation as facade
        for adapter in (plain, framed):
            with self.subTest(adapter=adapter.ADAPTER):
                case = self.prepare_case(adapter)
                probe = self.probe_case(case, adapter)
                case.assign()
                attempt = self.call_observed(case, adapter, case.protocol().invoke_assignment,
                    {'id': 'attempt', 'assignment_id': 'assignment'}, {'verdict': 'sound'})
                records = case.records()
                for record in (probe, records['review_assignment']['assignment'],
                               records['review_invocation']['attempt'], attempt):
                    self.assertEqual(record.get(facade.KEY), facade.PROTOCOL)
                self.assertEqual(case.protocol().route_state(records, case.artifacts, 'route')['context_status'],
                                 'verified_for_route')
                self.assertTrue(case.protocol().authenticate_attempt(records, case.artifacts,
                    records['review_assignment']['assignment'], attempt))
                state = case.protocol().assignment_state(records, case.artifacts, 'assignment')
                self.assertTrue(state['ready'], state['obligations'])
                response = json.loads(case.artifacts.read(attempt['response']))
                self.assertEqual(response[facade.KEY]['protocol'], facade.PROTOCOL)
                self.assertEqual(len([event for event in response[facade.KEY]['native_rollout']
                                      if event['type'] == 'turn_context']), 1)
                response.pop(facade.KEY)
                altered = copy.deepcopy(attempt)
                altered['response'] = case.artifacts.put(json.dumps(response).encode(), 'application/json')
                self.assertFalse(case.protocol().authenticate_attempt(records, case.artifacts,
                    records['review_assignment']['assignment'], altered))

    def test_shared_codex_fixture_retains_native_settings_for_plain_and_framed_calls(self):
        from research_harness import review_codex_invocation as facade
        for adapter in (plain, framed):
            with self.subTest(adapter=adapter.ADAPTER):
                case = self.prepare_case(adapter)
                codex_route_fixtures.probe_codex_route(case, 'shared-probe')
                case.assign()
                attempt = codex_route_fixtures.invoke_codex(
                    case, 'assignment', {'verdict': 'sound'}, 'shared-attempt')['result']
                records = case.records()
                for record in (records['review_route_probe']['shared-probe'],
                               records['review_assignment']['assignment'],
                               records['review_invocation']['shared-attempt'], attempt):
                    self.assertEqual(record[facade.KEY], facade.PROTOCOL)
                response = case.protocol()._read_json(case.artifacts, attempt['response'])
                events = response[facade.KEY]['native_rollout']
                contexts = [event['payload'] for event in events if event['type'] == 'turn_context']
                self.assertEqual(contexts, [{'model': 'fixture-model', 'effort': 'high', 'cwd': '/fixture'}])
                self.assertTrue(case.protocol().authenticate_attempt(records, case.artifacts,
                    records['review_assignment']['assignment'], attempt))
                state = case.protocol().assignment_state(records, case.artifacts, 'assignment')
                self.assertTrue(state['ready'], state['obligations'])

    def test_public_invocations_retain_wrong_settings_without_scientific_credit(self):
        from research_harness import review_codex_invocation as facade
        changes = [lambda rows: rows[1]['payload'].update(model='another-model'),
                   lambda rows: rows[1]['payload'].update(effort='xhigh'),
                   lambda rows: rows.pop(1),
                   lambda rows: rows.append(copy.deepcopy(rows[1]))]
        for adapter in (plain, framed):
            case = self.prepare_case(adapter)
            self.probe_case(case, adapter)
            for index, change in enumerate(changes):
                with self.subTest(adapter=adapter.ADAPTER, change=index):
                    assignment_id = 'assignment-' + str(index)
                    case.assign(identifier=assignment_id, reviewer='reviewer-' + str(index))
                    attempt = self.call_observed(case, adapter, case.protocol().invoke_assignment,
                        {'id': 'attempt-' + str(index), 'assignment_id': assignment_id},
                        {'verdict': 'sound'}, change)
                    records = case.records()
                    assignment = records['review_assignment'][assignment_id]
                    self.assertEqual(attempt['status'], 'completed')
                    self.assertEqual(attempt.get(facade.KEY), facade.PROTOCOL)
                    self.assertIsNotNone(attempt['output'])
                    self.assertTrue(case.protocol().authenticate_provenance(
                        records, case.artifacts, assignment, attempt))
                    self.assertFalse(case.protocol().authenticate_attempt(
                        records, case.artifacts, assignment, attempt))
                    state = case.protocol().assignment_state(records, case.artifacts, assignment_id)
                    self.assertFalse(state['ready'])

    def test_public_probe_refuses_wrong_native_model_and_effort(self):
        for adapter in (plain, framed):
            for key, value in (('model', 'another-model'), ('effort', 'xhigh')):
                with self.subTest(adapter=adapter.ADAPTER, key=key):
                    case = self.prepare_case(adapter)
                    probe = self.probe_case(case, adapter,
                        lambda rows: rows[1]['payload'].update({key: value}))
                    self.assertEqual(probe['status'], 'completed')
                    state = case.protocol().route_state(case.records(), case.artifacts, 'route')
                    self.assertNotEqual(state['context_status'], 'verified_for_route')

    def test_public_protocol_rejects_unsupported_assignment_attempt_and_probe_bindings(self):
        from research_harness import review_codex_invocation as facade
        for adapter in (plain, framed):
            case = self.prepare_case(adapter)
            self.probe_case(case, adapter)
            case.assign()
            self.call_observed(case, adapter, case.protocol().invoke_assignment,
                {'id': 'attempt', 'assignment_id': 'assignment'}, {'verdict': 'sound'})
            original = case.records()
            for kind, identifier in (('review_assignment', 'assignment'),
                                     ('review_attempt', 'attempt'), ('review_route_probe', 'probe')):
                with self.subTest(adapter=adapter.ADAPTER, kind=kind):
                    records = copy.deepcopy(original)
                    records[kind][identifier][facade.KEY] = 'unsupported-v2'
                    if kind == 'review_route_probe':
                        state = case.protocol().route_state(records, case.artifacts, 'route')
                        self.assertNotEqual(state['context_status'], 'verified_for_route')
                    else:
                        assignment = records['review_assignment']['assignment']
                        attempt = records['review_attempt']['attempt']
                        self.assertFalse(case.protocol().authenticate_provenance(
                            records, case.artifacts, assignment, attempt))
                        self.assertFalse(case.protocol().authenticate_attempt(
                            records, case.artifacts, assignment, attempt))
                        state = case.protocol().assignment_state(records, case.artifacts, 'assignment')
                        self.assertFalse(state['ready'])


if __name__ == '__main__':
    unittest.main()
