"""The Claude Code reviewer runs isolated and is verified on its observed stream, not on a self-report."""

import base64
import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness.errors import ResearchError
from research_harness.evidence import digest
from claude_review_fixtures import (RECORDED_CONTEXTS, RELEASED_IMPLEMENTATION, build_recorded_response,
                                    build_recorded_runtime)

ROUTE = {'id': 'route-claude', 'adapter': 'claude_cli_v1', 'endpoint': 'claude://local', 'model': 'claude-opus-5-5',
         'configuration': {'effort': 'xhigh', 'max_output_tokens': 20000, 'timeout_seconds': 1800}}
# Shape from the embedded schema in Claude Code 2.1.310, with synthetic identifiers. The historical response
# artifacts retain event types and digests, not event payloads; this is not a captured event (see DRAFT.md).
UI_INVALIDATE = {'type': 'system', 'subtype': 'ui_invalidate', 'event': 'ui.render',
                 'uuid': '90f3cdfd-4442-4a8e-8054-69221cacb72c', 'session_id': 'session-1'}
# The basic SDKAPIRetryMessage schema in Claude Code 2.1.310, with synthetic values. Its optional
# no_response payload is outside this provisional exact-field allowance.
API_RETRY = {'type': 'system', 'subtype': 'api_retry', 'attempt': 1, 'max_retries': 10,
             'retry_delay_ms': 1000, 'error_status': 529, 'error': 'overloaded',
             'uuid': '624c6151-b94d-410c-b6b7-4e29b6e74b40', 'session_id': 'session-1'}


def module():
    from research_harness import review_claude_transport
    return review_claude_transport


def pinned_route():
    """A recorded route carries the runtime of the adapter it was recorded with (record_route adds it)."""
    return dict(ROUTE, runtime=module().runtime())


def request_for(text='{"id":"allowed"}'):
    return {'model': ROUTE['model'], 'instructions': 'Review the supplied packet.',
            'input': [{'role': 'user', 'content': text}], 'tools': [], 'store': False,
            'max_output_tokens': 20000, 'text': {'format': {'type': 'json_object'}}}


def clean_stream(request, output='{"ok": true}', *, model=ROUTE['model'], session='session-1'):
    sent = module().packet_text(request)
    return [
        {'type': 'system', 'subtype': 'init', 'session_id': session, 'model': model, 'tools': [], 'mcp_servers': [],
         'skills': [], 'slash_commands': [], 'claude_code_version': '2.1.306'},
        {'type': 'user', 'isReplay': True, 'session_id': session,
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': sent}]}},
        {'type': 'assistant', 'session_id': session,
         'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': ''}, {'type': 'text', 'text': output}]}},
        {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed'}},
        {'type': 'result', 'subtype': 'success', 'is_error': False, 'num_turns': 1, 'result': output,
         'session_id': session, 'total_cost_usd': 0.25,
         'usage': {'input_tokens': 40, 'cache_creation_input_tokens': 1200, 'cache_read_input_tokens': 300,
                   'output_tokens': 900}},
    ]


def completed(events, returncode=0):
    return subprocess.CompletedProcess(['claude'], returncode, ''.join(json.dumps(event) + '\n' for event in events), '')


class ClaudeRouteTests(unittest.TestCase):
    def test_route_takes_the_local_endpoint_an_effort_and_bounds(self):
        module().validate_route(ROUTE)
        for change in ({'endpoint': 'https://api.anthropic.com'}, {'adapter': 'codex_cli_v1'},
                       {'configuration': dict(ROUTE['configuration'], effort='ultra')},
                       {'configuration': dict(ROUTE['configuration'], timeout_seconds=3601)},
                       {'configuration': dict(ROUTE['configuration'], max_output_tokens=0)},
                       {'configuration': {'max_output_tokens': 20000, 'timeout_seconds': 600}}):
            with self.subTest(change=change):
                with self.assertRaises(ResearchError):
                    module().validate_route(dict(ROUTE, **change))

    def test_runtime_names_the_isolation_and_not_the_installed_version(self):
        runtime = module().runtime()
        self.assertEqual(runtime['adapter'], 'claude_cli_v1')
        self.assertEqual(runtime['input_contract'], 'observed-print-stream-v1')
        for flag in ('--safe-mode', '--tools', '--strict-mcp-config', '--disable-slash-commands',
                     '--no-session-persistence', '--replay-user-messages'):
            self.assertIn(flag, runtime['flags'])
        self.assertNotIn('version', runtime)
        self.assertNotIn('executable', runtime)

    def test_environment_keeps_the_subscription_home_and_drops_api_credentials(self):
        environment = module().clean_environment({
            'HOME': '/home/user', 'PATH': '/bin', 'USER': 'user', 'CLAUDE_CONFIG_DIR': '/home/user/.claude',
            'ANTHROPIC_API_KEY': 'must-not-use', 'ANTHROPIC_AUTH_TOKEN': 'must-not-use', 'CLAUDECODE': '1',
            'CLAUDE_CODE_ENTRYPOINT': 'cli', 'OPENAI_API_KEY': 'must-not-use'})
        self.assertEqual(environment, {'HOME': '/home/user', 'PATH': '/bin', 'USER': 'user',
                                       'CLAUDE_CONFIG_DIR': '/home/user/.claude'})


class ClaudeStreamInspectionTests(unittest.TestCase):
    def test_api_retry_requires_the_exact_basic_schema(self):
        request = request_for()
        cases = [(dict(API_RETRY), True), (dict(API_RETRY, error_status=None, error='unknown'), True)]
        cases.extend((dict(API_RETRY, **{field: value}), False) for field, value in (
            ('tools', []), ('message', 'injected'), ('no_response', {'waited_ms': 5, 'retry_wait_ms': 10}),
            ('attempt', True), ('max_retries', '10'), ('retry_delay_ms', 1.5), ('error_status', False),
            ('error', {'message': 'injected'}), ('error', 'new_category'), ('uuid', ''), ('session_id', None)))
        cases.extend(({key: value for key, value in API_RETRY.items() if key != field}, False)
                     for field in API_RETRY if field not in ('type', 'subtype'))
        for event, verified in cases:
            with self.subTest(event=event):
                events = clean_stream(request)
                events.insert(2, event)
                evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
                self.assertEqual(evidence['input_verified'], verified, evidence['issues'])
                self.assertEqual(evidence['notices'], [{'event_index': 2, 'event': event}] if verified else [])
                self.assertEqual(evidence['issues'], [] if verified else ['system_event_api_retry'])
        events = clean_stream(request)
        events.insert(2, dict(API_RETRY, session_id={'unexpected': 'object'}))
        evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
        self.assertFalse(evidence['input_verified'])
        self.assertEqual(evidence['issues'], ['session_identity', 'system_event_api_retry'])

    def test_api_retry_needs_one_final_result_with_matching_output_and_valid_usage(self):
        request = request_for()

        def duplicate_result(events):
            events.append(copy.deepcopy(events[-1]))

        def append_output(events):
            events.append({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': ''}]}})

        edits = [None, duplicate_result, append_output, lambda events: events.pop(),
                 lambda events: events[-1].update(result='a different generation'),
                 lambda events: events[-1].update(num_turns=2),
                 lambda events: events[-1]['usage'].pop('input_tokens'),
                 lambda events: events[-1]['usage'].update(output_tokens=-1),
                 lambda events: events[-1]['usage'].update(cache_read_input_tokens=True)]
        for edit in edits:
            with self.subTest(edit=edit):
                events = clean_stream(request)
                events.insert(2, dict(API_RETRY))
                if edit is not None:
                    edit(events)
                evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
                self.assertEqual(evidence['input_verified'], edit is None, evidence['issues'])
                if edit is None:
                    self.assertEqual(evidence['notices'], [{'event_index': 2, 'event': API_RETRY}])
                else:
                    self.assertIn('system_event_api_retry', evidence['issues'])
                    self.assertEqual(evidence['notices'], [])

    def test_api_retry_notices_preserve_other_context_failures(self):
        request = request_for()
        for edit, issue in ((lambda events: events[0].update(tools=['Read']), 'tools_available'),
                            (lambda events: events[1].update(isReplay=False), 'tool_or_injected_input'),
                            (lambda events: events[2].update(session_id='other-session'), 'session_identity'),
                            (lambda events: events[2].update(uuid='EXCLUDED_CANARY'), 'excluded_control_exposed')):
            with self.subTest(issue=issue):
                events = clean_stream(request)
                events.insert(2, dict(API_RETRY))
                edit(events)
                evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
                self.assertFalse(evidence['input_verified'])
                self.assertIn(issue, evidence['issues'])
                self.assertEqual(len(evidence['notices']), 1)

    def test_a_clean_stream_verifies_the_delivered_input(self):
        request = request_for()
        evidence = module().inspect_stream(clean_stream(request), request, ROUTE, 'EXCLUDED_CANARY')
        self.assertTrue(evidence['input_verified'], evidence['issues'])
        self.assertEqual(evidence['session_id'], 'session-1')
        self.assertEqual(evidence['notices'], [])

    def test_each_ui_invalidation_is_retained_as_a_notice_without_invalidating_input(self):
        request = request_for()
        events = clean_stream(request)
        events[3:3] = [dict(UI_INVALIDATE), dict(UI_INVALIDATE)]
        evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
        self.assertTrue(evidence['input_verified'], evidence['issues'])
        self.assertEqual(evidence['issues'], [])
        self.assertEqual(evidence['notices'], [
            {'event_index': 3, 'event': UI_INVALIDATE}, {'event_index': 4, 'event': UI_INVALIDATE}])

    def test_ui_invalidation_with_any_additional_field_invalidates_input(self):
        request = request_for()
        for field, value in (('tools', ['Read']), ('tools', []), ('message', {'content': 'Injected input'}),
                             ('message', None), ('input', ''), ('permission', False), ('context', {}),
                             ('mcp_servers', []), ('prompts_screened', True), ('prompts_screened', False),
                             ('instances', []), ('future_field', None)):
            with self.subTest(field=field, value=value):
                events = clean_stream(request)
                events.insert(3, dict(UI_INVALIDATE, **{field: value}))
                evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
                self.assertFalse(evidence['input_verified'])
                self.assertEqual(evidence['issues'], ['system_event_ui_invalidate'])
                self.assertEqual(evidence['notices'], [])

    def test_ui_invalidation_requires_the_basic_render_notification_shape(self):
        request = request_for()
        invalid = [dict(UI_INVALIDATE, **{field: value}) for field, value in (
            ('event', 'prompt.submit'), ('event', {'input': 'injected'}), ('uuid', ''), ('uuid', {'message': 'input'}),
            ('session_id', ''), ('session_id', None))]
        invalid.extend({key: value for key, value in UI_INVALIDATE.items() if key != missing}
                       for missing in ('event', 'uuid', 'session_id'))
        for event in invalid:
            with self.subTest(event=event):
                events = clean_stream(request)
                events.insert(3, event)
                evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
                self.assertFalse(evidence['input_verified'])
                self.assertEqual(evidence['issues'], ['system_event_ui_invalidate'])
                self.assertEqual(evidence['notices'], [])

    def test_other_system_subtypes_are_not_ui_notices(self):
        request = request_for()
        for subtype in ('unknown_notification', 'ui_refresh', 'ui_log'):
            with self.subTest(subtype=subtype):
                events = clean_stream(request)
                events.insert(3, dict(UI_INVALIDATE, subtype=subtype))
                evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
                self.assertFalse(evidence['input_verified'])
                self.assertEqual(evidence['issues'], ['system_event_' + subtype])
                self.assertEqual(evidence['notices'], [])

    def test_ui_notices_do_not_bypass_session_or_excluded_control_checks(self):
        request = request_for()
        for change, issue in (({'session_id': 'another-session'}, 'session_identity'),
                              ({'uuid': 'EXCLUDED_CANARY'}, 'excluded_control_exposed')):
            with self.subTest(change=change):
                events = clean_stream(request)
                events.insert(3, dict(UI_INVALIDATE, **change))
                evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_CANARY')
                self.assertFalse(evidence['input_verified'])
                self.assertEqual(evidence['issues'], [issue])
                self.assertEqual(len(evidence['notices']), 1)

    def test_the_thinking_token_accounting_event_does_not_invalidate_the_stream(self):
        request = request_for()
        events = clean_stream(request)
        accounting = {'type': 'system', 'subtype': 'thinking_tokens', 'session_id': 'session-1', 'thinking_tokens': 1200}
        events.insert(3, accounting)
        evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_PROJECT_x')
        self.assertEqual((evidence['input_verified'], evidence['issues']), (True, []))
        events[3] = dict(accounting, subtype='compact_boundary')
        evidence = module().inspect_stream(events, request, ROUTE, 'EXCLUDED_PROJECT_x')
        self.assertEqual((evidence['input_verified'], evidence['issues']), (False, ['system_event_compact_boundary']))

    def test_any_extra_context_or_capability_invalidates_the_stream(self):
        request = request_for()

        def changed(edit):
            events = clean_stream(request)
            edit(events)
            return events

        cases = {
            'tools': lambda e: e[0].update(tools=['Read']),
            'mcp': lambda e: e[0].update(mcp_servers=[{'name': 'memory', 'status': 'connected'}]),
            'skills': lambda e: e[0].update(skills=['simple-japanese']),
            'commands': lambda e: e[0].update(slash_commands=['review']),
            'model': lambda e: e[0].update(model='claude-sonnet-5-5'),
            'replay': lambda e: e[1]['message']['content'][0].update(text='Substituted author account'),
            'tool_use': lambda e: e[2]['message']['content'].append({'type': 'tool_use', 'name': 'Read', 'input': {}}),
            'tool_result': lambda e: e.insert(3, {'type': 'user', 'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'content': 'A prior reviewer score was 5'}]}}),
            'compaction': lambda e: e.insert(2, {'type': 'system', 'subtype': 'compact_boundary'}),
            'hook': lambda e: e.insert(1, {'type': 'system', 'subtype': 'hook_response', 'output': 'injected'}),
            'second_turn': lambda e: e[-1].update(num_turns=2),
            'error_result': lambda e: e[-1].update(subtype='error_during_execution', is_error=True),
            'missing_init': lambda e: e.pop(0),
            'canary': lambda e: e[2]['message']['content'][1].update(text='I can see EXCLUDED_CANARY'),
            'unknown_event': lambda e: e.insert(2, {'type': 'stream_event', 'event': {}}),
        }
        for name, edit in cases.items():
            with self.subTest(case=name):
                evidence = module().inspect_stream(changed(edit), request, ROUTE, 'EXCLUDED_CANARY')
                self.assertFalse(evidence['input_verified'])
                self.assertTrue(evidence['issues'])


class ClaudeInvocationTests(unittest.TestCase):
    def invoke(self, events, *, returncode=0, request=None, raw=None):
        request = request or request_for()
        calls = []

        def run(command, *, environment, timeout, packet=None, directory=None):
            calls.append({'command': command, 'environment': environment, 'timeout': timeout, 'packet': packet,
                          'directory': directory, 'files': sorted(path.name for path in Path(directory).iterdir()),
                          'system_prompt': (Path(directory) / 'system-prompt.txt').read_text()})
            return (completed(events, returncode) if raw is None
                    else subprocess.CompletedProcess(command, returncode, raw, ''))

        # A recorded route carries its pinned runtime; the evidence binds the route digest, so the same
        # pinned route is used for the call and for the verification.
        self.route = pinned_route()
        with patch.object(module(), '_run_owned', side_effect=run), \
                patch.object(module(), '_cli', return_value=('/usr/local/bin/claude', '2.1.306 (Claude Code)')):
            response = module().send_request(self.route, request)
        return response, calls[0]

    def test_api_retry_binds_output_and_usage_to_its_single_result(self):
        request = request_for()
        events = clean_stream(request, output='```json\n{"ok": true}\n```')
        events.insert(2, dict(API_RETRY))
        response, _ = self.invoke(events, request=request)
        self.assertTrue(module().verify_context(self.route, request, response))
        self.assertEqual(response['usage'], {'input_tokens': 1540, 'output_tokens': 900, 'cost_usd_estimate': 0.25})
        for edit in (lambda value: value['usage'].update(input_tokens=1541),
                     lambda value: value['usage'].update(output_tokens=901),
                     lambda value: value['usage'].update(cost_usd_estimate=0.5),
                     lambda value: value['output'][0]['content'][0].update(text='{"other": true}')):
            with self.subTest(edit=edit):
                changed = copy.deepcopy(response)
                edit(changed)
                self.assertFalse(module().verify_context(self.route, request, changed))
        events.append(copy.deepcopy(events[-1]))
        response, _ = self.invoke(events, request=request)
        self.assertEqual(response['status'], 'incomplete')
        self.assertFalse(module().verify_context(self.route, request, response))

    def test_every_system_event_is_retained_byte_exact_in_the_response_artifact(self):
        from research_harness.artifacts import ArtifactStore
        import tempfile
        request = request_for()
        events = clean_stream(request)
        events[2:2] = [dict(UI_INVALIDATE), {'type': 'system', 'subtype': 'future_notice', 'payload': '再描画'}]
        lines = [' \t' + json.dumps(event, ensure_ascii=False, separators=(', ', ': ')) + ' \r\n'
                 for event in events]
        lines[0] = lines[0].replace('"system"', '"syst\\u0065m"')
        response, _ = self.invoke(events, request=request, raw=''.join(lines))
        self.assertFalse(module().verify_context(self.route, request, response))
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            reference = artifacts.put(json.dumps(response).encode(), 'application/json')
            retained_response = json.loads(artifacts.read(reference))
        retention = retained_response['execution']['system_events']
        self.assertFalse(retention['truncated'])
        self.assertEqual(retention['observed_count'], 3)
        self.assertEqual([entry['event_index'] for entry in retention['events']], [0, 2, 3])
        self.assertEqual([base64.b64decode(entry['raw_base64']) for entry in retention['events']],
                         [lines[index].encode() for index in (0, 2, 3)])
        self.assertEqual(retention['sha256'], digest(retention['events']))
        classified = module().inspect_retained_system_events(retained_response)
        self.assertEqual(classified['issues'], ['system_event_future_notice'])
        self.assertEqual(classified['notices'], [{'event_index': 2, 'event': UI_INVALIDATE}])

    def test_retention_keeps_a_prefix_at_the_count_and_byte_bounds(self):
        request = request_for()
        events = clean_stream(request)
        events[2:2] = [dict(UI_INVALIDATE), dict(API_RETRY), dict(UI_INVALIDATE)]
        first_line_size = len((json.dumps(events[0]) + '\n').encode())
        for count_limit, byte_limit, expected_count in ((2, 100000, 2), (100, first_line_size, 1),
                                                       (100, first_line_size - 1, 0)):
            with self.subTest(count_limit=count_limit, byte_limit=byte_limit):
                with patch.object(module(), 'SYSTEM_EVENT_COUNT_LIMIT', count_limit, create=True), \
                        patch.object(module(), 'SYSTEM_EVENT_BYTE_LIMIT', byte_limit, create=True):
                    response, _ = self.invoke(events, request=request)
                retention = response['execution']['system_events']
                self.assertEqual(retention['count_limit'], count_limit)
                self.assertEqual(retention['byte_limit'], byte_limit)
                self.assertEqual(retention['observed_count'], 4)
                self.assertEqual(len(retention['events']), expected_count)
                self.assertLessEqual(retention['retained_bytes'], byte_limit)
                self.assertTrue(retention['truncated'])
                classified = module().inspect_retained_system_events(response)
                self.assertTrue(classified['truncated'])

    def test_refused_retained_events_can_be_classified_again_without_the_cli(self):
        request = request_for()
        events = clean_stream(request)
        events.insert(2, dict(UI_INVALIDATE))
        with patch.object(module(), '_ui_notice', return_value=False):
            response, _ = self.invoke(events, request=request)
        self.assertEqual(response['context_evidence']['issues'], ['system_event_ui_invalidate'])
        before = copy.deepcopy(response)
        with patch.object(module(), '_run_owned', side_effect=AssertionError('No CLI during reinspection')) as run:
            classified = module().inspect_retained_system_events(response)
        self.assertEqual(classified['issues'], [])
        self.assertEqual(classified['notices'], [{'event_index': 2, 'event': UI_INVALIDATE}])
        run.assert_not_called()
        self.assertEqual(response, before)

    def test_retained_retry_classification_checks_the_recorded_generation(self):
        request = request_for()
        events = clean_stream(request)
        events.insert(2, dict(API_RETRY))
        response, _ = self.invoke(events, request=request)
        classified = module().inspect_retained_system_events(response)
        self.assertEqual(classified['issues'], [])
        self.assertEqual(classified['notices'], [{'event_index': 2, 'event': API_RETRY}])
        response['usage']['output_tokens'] += 1
        classified = module().inspect_retained_system_events(response)
        self.assertEqual(classified['issues'], ['system_event_api_retry'])
        self.assertEqual(classified['notices'], [])

    def test_reinspection_refuses_missing_or_corrupted_retention(self):
        request = request_for()
        response, _ = self.invoke(clean_stream(request), request=request)
        for edit in (lambda value: value['execution'].pop('system_events', None),
                     lambda value: value['execution']['system_events']['events'][0].update(raw_base64='%%%'),
                     lambda value: value['execution']['system_events'].update(retained_bytes=0),
                     lambda value: value['execution']['system_events'].update(truncated=True)):
            with self.subTest(edit=edit):
                changed = copy.deepcopy(response)
                edit(changed)
                with self.assertRaises(ResearchError):
                    module().inspect_retained_system_events(changed)

    def test_the_command_isolates_the_reviewer_and_sends_one_replayed_message(self):
        request = request_for()
        response, call = self.invoke(clean_stream(request), request=request)
        command = call['command']
        self.assertEqual(command[0], '/usr/local/bin/claude')
        for flag in module().FLAGS:
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--model') + 1], ROUTE['model'])
        self.assertEqual(command[command.index('--effort') + 1], 'xhigh')
        self.assertEqual(call['system_prompt'], request['instructions'])
        self.assertEqual(call['timeout'], 1800)
        self.assertNotIn('ANTHROPIC_API_KEY', call['environment'])
        self.assertEqual(call['environment']['CLAUDE_CODE_MAX_OUTPUT_TOKENS'],
                         str(ROUTE['configuration']['max_output_tokens']))
        self.assertEqual(call['files'], ['CLAUDE.md', 'system-prompt.txt'])
        line = json.loads(call['packet'])
        self.assertEqual(line['message']['content'][0]['text'], module().packet_text(request))
        self.assertEqual(response['status'], 'completed')
        self.assertEqual(response['id'], 'session-1')
        self.assertEqual(response['output'][0]['content'][0]['text'], '{"ok": true}')
        self.assertEqual(response['usage']['input_tokens'], 1540)
        self.assertEqual(response['usage']['output_tokens'], 900)
        self.assertEqual(response['context_evidence']['cli_version'], '2.1.306 (Claude Code)')
        self.assertTrue(module().verify_context(self.route, request, response))

    def test_an_invocation_with_ui_invalidation_verifies_and_preserves_the_notice(self):
        request = request_for()
        events = clean_stream(request)
        events.insert(3, dict(UI_INVALIDATE))
        response, _ = self.invoke(events, request=request)
        self.assertEqual(response['status'], 'completed')
        self.assertTrue(module().verify_context(self.route, request, response))
        self.assertEqual(response['context_evidence']['notices'], [{'event_index': 3, 'event': UI_INVALIDATE}])
        events.append(events.pop(3))
        response, _ = self.invoke(events, request=request)
        self.assertTrue(module().verify_context(self.route, request, response))
        events.insert(3, events.pop())
        for field, value in (('tools', []), ('message', {'content': 'Injected input'})):
            with self.subTest(field=field):
                events[3] = dict(UI_INVALIDATE, **{field: value})
                response, _ = self.invoke(events, request=request)
                self.assertFalse(module().verify_context(self.route, request, response))

    def test_context_verification_uses_the_route_pin_and_not_the_installed_runtime(self):
        request = request_for()
        response, _ = self.invoke(clean_stream(request), request=request)
        route = self.route
        self.assertTrue(module().verify_context(route, request, response))
        changed = dict(module().runtime(), implementation_sha256='0' * 64)
        with patch.object(module(), 'runtime', return_value=changed):
            # The adapter module changed after the review ran: the evidence still matches the route's pin.
            self.assertTrue(module().verify_context(route, request, response))
        # Evidence recorded by another adapter implementation than the one the route pins does not verify.
        response['context_evidence']['runtime'] = changed
        self.assertFalse(module().verify_context(route, request, response))

    def test_a_failed_or_unbounded_turn_is_incomplete(self):
        request = request_for()
        over = clean_stream(request)
        over[-1]['usage']['output_tokens'] = 20001
        for events, returncode in ((clean_stream(request), 1), (over, 0), (clean_stream(request)[:-1], 0)):
            with self.subTest(returncode=returncode, events=len(events)):
                response, _ = self.invoke(events, returncode=returncode, request=request)
                self.assertEqual(response['status'], 'incomplete')

    def test_a_fenced_json_answer_is_handed_on_without_its_fence(self):
        request = request_for()
        fenced = '```json\n{"ok": true}\n```'
        response, _ = self.invoke(clean_stream(request, output=fenced), request=request)
        self.assertEqual((response['status'], response['output'][0]['content'][0]['text']), ('completed', '{"ok": true}'))
        self.assertTrue(response['context_evidence']['input_verified'])
        self.assertTrue(response['execution']['fence_removed'])
        plain, _ = self.invoke(clean_stream(request), request=request)
        self.assertEqual((plain['output'][0]['content'][0]['text'], plain['execution']['fence_removed']), ('{"ok": true}', False))
        # A fence that does not enclose the whole answer, or a bare language tag, is left as it is.
        for text in ('```json\n{"ok": true}', 'See ```json\n{"ok": true}\n``` above', '```'):
            self.assertEqual(module().strip_fence(text), (text, False))
        self.assertEqual(module().strip_fence('```\n{"ok": true}\n```'), ('{"ok": true}', True))

    def test_a_contaminated_stream_cannot_verify_the_context(self):
        request = request_for()
        events = clean_stream(request)
        events[0]['tools'] = ['Bash']
        response, _ = self.invoke(events, request=request)
        self.assertFalse(module().verify_context(self.route, request, response))

    def test_an_input_above_the_route_limit_is_refused_before_the_cli_runs(self):
        request = request_for('x' * (module().INPUT_LIMIT_CHARACTERS + 1))
        with patch.object(module(), '_run_owned') as run, \
                patch.object(module(), '_cli', return_value=('/usr/local/bin/claude', '2.1.306 (Claude Code)')):
            with self.assertRaises(ResearchError) as raised:
                module().send_request(ROUTE, request)
        self.assertEqual(raised.exception.code, 'review_input_too_large')
        run.assert_not_called()


class ClaudeDispatchTests(unittest.TestCase):
    def test_the_review_transport_dispatches_the_claude_route(self):
        from research_harness import review_transport
        review_transport.validate_route(ROUTE)
        self.assertEqual(review_transport.runtime(ROUTE)['adapter'], 'claude_cli_v1')
        request = review_transport.request_body(ROUTE, 'Review.', {'id': 'packet'})
        self.assertEqual(request['tools'], [])
        self.assertIs(request['store'], False)

    def test_binary_evidence_is_named_without_its_bytes_on_the_claude_route(self):
        from research_harness import review_transport
        packet = {'evidence': [
            {'artifact': {'path': 'objects/ab', 'sha256': 'ab', 'size': 3, 'media_type': 'application/pdf'},
             'content': 'QUJD', 'encoding': 'base64'},
            {'artifact': {'path': 'objects/cd', 'sha256': 'cd', 'size': 5, 'media_type': 'text/plain'},
             'content': 'hello', 'encoding': 'utf-8'}]}
        request = review_transport.request_body(ROUTE, 'Review.', packet)
        content = request['input'][-1]['content']
        self.assertIsInstance(content, str)
        self.assertNotIn('QUJD', content)
        self.assertTrue(content.startswith(review_transport.PACKET_LABEL))
        delivered = json.loads(content[len(review_transport.PACKET_LABEL):])['evidence']
        self.assertEqual(delivered[0]['encoding'], 'omitted')
        self.assertEqual(delivered[1]['content'], 'hello')

    def test_observe_records_a_refused_input_as_a_failed_attempt(self):
        from research_harness import review_transport
        request = review_transport.request_body(ROUTE, 'Review.', {'id': 'x' * (module().INPUT_LIMIT_CHARACTERS + 1)})
        with patch.object(module(), '_cli', return_value=('/usr/local/bin/claude', '2.1.306 (Claude Code)')):
            observed = review_transport.observe(ROUTE, request, None)
        self.assertEqual(observed['status'], 'failed')
        self.assertEqual(observed['error']['code'], 'review_input_too_large')
        self.assertEqual(observed['request_digest'], digest(request))


class ClaudeHistoricalContextTests(unittest.TestCase):
    def test_recorded_release_and_draft_contexts_verify_against_their_route_pins(self):
        request = request_for()
        for context in RECORDED_CONTEXTS:
            with self.subTest(context=context[0]):
                route = dict(ROUTE, runtime=build_recorded_runtime(context[1]))
                response = build_recorded_response(route, request, {'ok': True}, context)
                verify = module().verify_context if context[2] else module().verify_legacy_context
                self.assertTrue(verify(route, request, response))
                self.assertEqual(module().verify_context(route, request, response), context[2])
                changed = copy.deepcopy(response)
                changed['context_evidence']['runtime']['implementation_sha256'] = '0' * 64
                self.assertFalse(verify(route, request, changed))

    def test_legacy_ui_compatibility_requires_the_exact_issue_and_every_other_context_check(self):
        request = request_for()
        context = RECORDED_CONTEXTS[-1]
        route = dict(ROUTE, runtime=build_recorded_runtime(RELEASED_IMPLEMENTATION))
        response = build_recorded_response(route, request, {'ok': True}, context)
        self.assertTrue(module().verify_legacy_context(route, request, response))
        for change in ({'inspected': False}, {'input_verified': None}, {'input_verified': 0},
                       {'issues': ['system_event_ui_invalidate', 'tools_available']},
                       {'issues': ['system_event_ui_invalidate', 'system_event_ui_invalidate']},
                       {'issues': ['system_event_api_retry']}, {'issues': []}, {'request_digest': 'changed'},
                       {'route_digest': 'changed'}, {'input_contract': 'changed'}, {'event_digest': ''},
                       {'session_id': 'other-session'}, {'runtime': module().runtime()}):
            with self.subTest(change=change):
                changed = copy.deepcopy(response)
                changed['context_evidence'].update(change)
                self.assertFalse(module().verify_legacy_context(route, request, changed))
        for implementation in ('0' * 64, module().runtime()['implementation_sha256'],
                               RECORDED_CONTEXTS[1][1], RECORDED_CONTEXTS[2][1]):
            with self.subTest(implementation=implementation):
                changed_route = dict(ROUTE, runtime=build_recorded_runtime(implementation))
                changed_response = build_recorded_response(changed_route, request, {'ok': True}, context)
                changed_response['context_evidence']['runtime'] = changed_route['runtime']
                self.assertFalse(module().verify_legacy_context(changed_route, request, changed_response))


class ClaudeStreamCaptureTests(unittest.TestCase):
    def test_owned_capture_preserves_line_endings_utf8_and_undecodable_bytes(self):
        raw = b' \t{"type":"system","subtype":"future","payload":"\xe7\x94\xbb"}\r\n\xff'
        result = module()._run_owned([sys.executable, '-c', 'import os; os.write(1, ' + repr(raw) + ')'],
                                     environment=dict(os.environ), timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.encode('utf-8', errors='surrogateescape'), raw)


if __name__ == '__main__':
    unittest.main()
