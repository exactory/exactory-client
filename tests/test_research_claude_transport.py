"""The Claude Code reviewer runs isolated and is verified on its observed stream, not on a self-report."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness.errors import ResearchError
from research_harness.evidence import digest

ROUTE = {'id': 'route-claude', 'adapter': 'claude_cli_v1', 'endpoint': 'claude://local', 'model': 'claude-opus-5-5',
         'configuration': {'effort': 'xhigh', 'max_output_tokens': 20000, 'timeout_seconds': 1800}}


def module():
    from research_harness import review_claude_transport
    return review_claude_transport


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
    def test_a_clean_stream_verifies_the_delivered_input(self):
        request = request_for()
        evidence = module().inspect_stream(clean_stream(request), request, ROUTE, 'EXCLUDED_CANARY')
        self.assertTrue(evidence['input_verified'], evidence['issues'])
        self.assertEqual(evidence['session_id'], 'session-1')

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
    def invoke(self, events, *, returncode=0, request=None):
        request = request or request_for()
        calls = []

        def run(command, *, environment, timeout, packet=None, directory=None):
            calls.append({'command': command, 'environment': environment, 'timeout': timeout, 'packet': packet,
                          'directory': directory, 'files': sorted(path.name for path in Path(directory).iterdir()),
                          'system_prompt': (Path(directory) / 'system-prompt.txt').read_text()})
            return completed(events, returncode)

        with patch.object(module(), '_run_owned', side_effect=run), \
                patch.object(module(), '_cli', return_value=('/usr/local/bin/claude', '2.1.306 (Claude Code)')):
            response = module().send_request(ROUTE, request)
        return response, calls[0]

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
        self.assertTrue(module().verify_context(ROUTE, request, response))

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
        self.assertFalse(module().verify_context(ROUTE, request, response))

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


if __name__ == '__main__':
    unittest.main()
