"""Codex review commands exclude executable tools and retain bounded failure evidence."""

import base64
from contextlib import ExitStack
import struct
import subprocess
import unittest
from unittest.mock import patch
import zlib

from research_harness import review_codex_transport as codex
from research_harness import review_codex_framed_transport as framed
from research_harness import review_codex_visual_transport as visual
from research_harness import review_transport
from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError
from research_harness.evidence import digest


ADAPTERS = (codex, framed, visual)
DISABLED_TOOLS = ('code_mode', 'code_mode_host', 'code_mode_only', 'js_repl', 'js_repl_tools_only')


def build_png():
    def build_chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + build_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + build_chunk(b'IDAT', zlib.compress(b'\x00\x40\x00\x00')) + build_chunk(b'IEND', b''))


class CodexControlTests(unittest.TestCase):
    def build_route(self, adapter):
        return {'adapter': adapter.ADAPTER, 'endpoint': 'codex://local', 'model': 'fixture-model',
                'configuration': {'max_output_tokens': 100, 'timeout_seconds': 5}}

    def build_request(self, adapter, characters=None):
        request = {'model': 'fixture-model', 'instructions': 'Review only the supplied evidence.',
                   'input': [{'role': 'user', 'content': ''}]}
        if adapter is visual:
            image = build_png()
            request['exactory_image_inputs'] = [{'artifact': describe_artifact(image, 'image/png'),
                'data_url': 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')}]
        if characters is not None:
            overhead = len(adapter.packet_text(request))
            request['input'][0]['content'] = 'x' * (characters - overhead)
            self.assertEqual(len(adapter.packet_text(request)), characters)
        return request

    def inspect_request(self, adapter, request, extra_events=()):
        parts = (visual.native_user_parts(request, '/safe/work') if adapter is visual
                 else [{'type': 'input_text', 'text': adapter.packet_text(request)}])
        events = [
            {'type': 'session_meta', 'payload': {'id': 'thread', 'source': 'exec',
                'base_instructions': {'text': request['instructions']}}},
            {'type': 'turn_context', 'payload': {'model': 'fixture-model', 'effort': 'high', 'cwd': '/safe/work'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': parts}},
        ]
        native = codex if adapter is visual else adapter
        with patch.object(native, 'NATIVE_DECLARATION_SETS', {()}):
            arguments = {'excluded_images': [b'excluded-image']} if adapter is visual else {}
            return adapter.inspect_rollout(events + list(extra_events), request, 'thread', '/safe/work', [],
                                           **arguments)

    def run_request(self, adapter, request, result, route=None):
        runner = codex if adapter is visual else adapter
        with ExitStack() as stack:
            stack.enter_context(patch.object(codex.shutil, 'which', return_value='/fixture/codex'))
            stack.enter_context(patch.object(adapter, 'runtime', return_value={'executable': '/fixture/codex'}))
            stack.enter_context(patch.object(visual.review_visual_inputs, 'control_image', return_value=build_png()))
            process = stack.enter_context(patch.object(runner, '_run_owned', return_value=result))
            response = adapter.send_request(route or self.build_route(adapter), request)
            return response, process.call_args

    def test_each_transport_disables_code_mode_and_javascript_repl_in_its_command(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER):
                controls = codex.CONTROLS if adapter is visual else adapter.CONTROLS
                for name in DISABLED_TOOLS:
                    self.assertIn('features.' + name + '=false', controls)
                result = subprocess.CompletedProcess(['/fixture/codex'], 0, '', '')
                _, invocation = self.run_request(adapter, self.build_request(adapter), result)
                command = invocation.args[0]
                overrides = [command[index + 1] for index, value in enumerate(command) if value == '-c']
                for name in DISABLED_TOOLS:
                    self.assertIn('features.' + name + '=false', overrides)

    def test_custom_tool_calls_name_the_tool_and_other_unknown_items_keep_the_generic_issue(self):
        items = [({'type': 'custom_tool_call', 'name': 'exec', 'input': 'return ALL_TOOLS;'}, 'tool_call:exec'),
                 ({'type': 'custom_tool_call', 'name': 'js_repl', 'input': '1 + 1'}, 'tool_call:js_repl'),
                 ({'type': 'custom_tool_call', 'input': '1 + 1'}, 'tool_call:unknown'),
                 ({'type': 'custom_tool_call_output', 'output': '[]'}, 'unknown_or_tool_input'),
                 ({'type': 'function_call', 'name': 'exec'}, 'unknown_or_tool_input'),
                 ({'type': 'unknown_context'}, 'unknown_or_tool_input')]
        for adapter in ADAPTERS:
            request = self.build_request(adapter)
            self.assertTrue(self.inspect_request(adapter, request)['input_verified'])
            for item, issue in items:
                with self.subTest(adapter=adapter.ADAPTER, item=item['type'], issue=issue):
                    evidence = self.inspect_request(adapter, request, [{'type': 'response_item', 'payload': item}])
                    self.assertFalse(evidence['input_verified'])
                    self.assertEqual(evidence['issues'], [issue])

    def test_openness_input_of_1055256_characters_is_refused_before_cli_dispatch(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER):
                request = self.build_request(adapter, 1055256)
                runner = codex if adapter is visual else adapter
                with patch.object(codex.shutil, 'which', return_value='/fixture/codex'), \
                        patch.object(adapter, 'runtime', return_value={'executable': '/fixture/codex'}) as runtime, \
                        patch.object(runner, '_run_owned',
                                     return_value=subprocess.CompletedProcess([], 0, '', '')) as process, \
                        patch.object(visual.review_visual_inputs, 'control_image', return_value=build_png()):
                    with self.assertRaises(ResearchError) as failure:
                        adapter.send_request(self.build_route(adapter), request)
                    self.assertEqual(failure.exception.code, 'review_input_too_large')
                    process.assert_not_called()
                    runtime.assert_not_called()

    def test_text_input_limit_is_inclusive_and_counts_the_serialized_packet(self):
        for adapter in (codex, framed):
            with self.subTest(adapter=adapter.ADAPTER):
                result = subprocess.CompletedProcess(['/fixture/codex'], 0, '', '')
                request = self.build_request(adapter, 1048576)
                _, invocation = self.run_request(adapter, request, result)
                self.assertEqual(len(invocation.kwargs['packet']), 1048576)
                request['input'][0]['content'] += 'x'
                with self.assertRaises(ResearchError) as failure:
                    self.run_request(adapter, request, result)
                self.assertEqual(failure.exception.code, 'review_input_too_large')

    def test_observation_records_oversized_input_without_a_provider_response_or_json_error(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER):
                request = self.build_request(adapter, 1055256)
                runner = codex if adapter is visual else adapter
                with patch.object(codex.shutil, 'which', return_value='/fixture/codex'), \
                        patch.object(adapter, 'runtime', return_value={'executable': '/fixture/codex'}), \
                        patch.object(runner, '_run_owned',
                                     return_value=subprocess.CompletedProcess([], 0, '', '')) as process, \
                        patch.object(visual.review_visual_inputs, 'control_image', return_value=build_png()):
                    observed = review_transport.observe(self.build_route(adapter), request, None)
                self.assertEqual(observed['status'], 'failed')
                self.assertEqual(observed['error'], {'kind': 'ResearchError', 'code': 'review_input_too_large'})
                self.assertIsNone(observed['response'])
                self.assertIsNone(observed['output'])
                process.assert_not_called()

    def test_unsuccessful_cli_exit_retains_stderr_and_success_has_an_empty_tail(self):
        for adapter in ADAPTERS:
            for returncode, expected in [(1, 'CLI rejected the request'), (None, 'CLI rejected the request'), (0, '')]:
                with self.subTest(adapter=adapter.ADAPTER, returncode=returncode):
                    result = subprocess.CompletedProcess(['/fixture/codex'], returncode, '',
                                                         'CLI rejected the request\n')
                    response, _ = self.run_request(adapter, self.build_request(adapter), result)
                    self.assertEqual(response['execution'].get('stderr_tail'), expected)
                    self.assertEqual(response['status'], 'incomplete')

    def test_failed_cli_stderr_tail_is_bounded_to_the_last_twenty_lines(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER):
                stderr = '\n'.join('failure ' + str(index) for index in range(100)) + '\n'
                result = subprocess.CompletedProcess(['/fixture/codex'], 1, '', stderr)
                response, _ = self.run_request(adapter, self.build_request(adapter), result)
                expected = '\n'.join('failure ' + str(index) for index in range(80, 100))
                self.assertEqual(response['execution'].get('stderr_tail'), expected)

    def test_failed_cli_stderr_tail_is_bounded_in_utf8_bytes_and_preserves_the_final_error(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.ADAPTER):
                stderr = 'obsolete preface\n' + '😀' * 3000 + '\nfinal CLI error\n'
                result = subprocess.CompletedProcess(['/fixture/codex'], 1, '', stderr)
                response, _ = self.run_request(adapter, self.build_request(adapter), result)
                tail = response['execution'].get('stderr_tail', '')
                self.assertTrue(tail.endswith('\nfinal CLI error'))
                self.assertLessEqual(len(tail.encode('utf-8')), 4096)
                self.assertGreater(len(tail.encode('utf-8')), 4092)
                self.assertLessEqual(len(tail.splitlines()), 20)
                self.assertNotIn('obsolete preface', tail)
                self.assertNotIn('\ufffd', tail)

    def test_context_authentication_uses_the_route_pin_after_the_installed_runtime_changes(self):
        for adapter in ADAPTERS:
            request = self.build_request(adapter)
            pinned = {'adapter': adapter.ADAPTER, 'version': 'codex-cli 0.163.0',
                      'implementation_sha256': '1' * 64, 'controls': ['features.shell_tool=false']}
            installed = dict(pinned, implementation_sha256='2' * 64,
                             controls=pinned['controls'] + ['features.code_mode_host=false'])
            route = dict(self.build_route(adapter), runtime=pinned)
            evidence = self.inspect_request(adapter, request)
            evidence.update(inspected=True, route_digest=digest(route), runtime=pinned, event_digest='saved-events')
            response = {'id': 'thread', 'context_evidence': evidence}
            with self.subTest(adapter=adapter.ADAPTER), patch.object(adapter, 'runtime', return_value=installed):
                self.assertTrue(adapter.verify_context(route, request, response))
                response['context_evidence'] = dict(evidence, runtime=installed)
                self.assertFalse(adapter.verify_context(route, request, response))


if __name__ == '__main__':
    unittest.main()
