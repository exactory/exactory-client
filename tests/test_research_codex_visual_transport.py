"""Actual multimodal input and model identity are required for visual reviews."""

import base64
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from research_harness import review_codex_transport as old
from research_harness import review_exact_text_v2 as codec
from research_harness.errors import ResearchError
from research_harness.evidence import digest


def png(red):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes((0, red, 0, 0)))) + chunk(b'IEND', b''))


def image_row(red):
    data = png(red)
    sha = hashlib.sha256(data).hexdigest()
    return {'artifact': {'path': 'research/sources/objects/' + sha, 'sha256': sha,
                         'size': len(data), 'media_type': 'image/png'},
            'data_url': 'data:image/png;base64,' + base64.b64encode(data).decode()}


class VisualTransportTests(unittest.TestCase):
    def setUp(self):
        name = 'research_harness.review_codex_visual_transport'
        self.assertIsNotNone(importlib.util.find_spec(name), 'The audited image route is not implemented.')
        self.adapter = importlib.import_module(name)
        temporary = tempfile.TemporaryDirectory(prefix='visual-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = str(Path(temporary.name).resolve())
        self.request = {'model': 'gpt-6-astra', 'instructions': 'Return only the observed controls.',
                        'input': [{'role': 'user', 'content': 'Read the first and second images.'}],
                        'exactory_image_inputs': [image_row(40), image_row(90)]}
        self.route = {'id': 'visual', 'adapter': 'codex_cli_visual_v1', 'endpoint': 'codex://local',
                      'model': 'gpt-6-astra', 'configuration': {'max_output_tokens': 1000, 'timeout_seconds': 30}}
        declaration = 'Audited native declaration for this fixture.'
        active = patch.object(old, 'NATIVE_DECLARATION_SETS', {(hashlib.sha256(declaration.encode()).hexdigest(),)})
        active.start()
        self.addCleanup(active.stop)
        wire = json.dumps({'review_messages': self.request['input']}, sort_keys=True, ensure_ascii=False)
        parts = []
        for index, row in enumerate(self.request['exactory_image_inputs'], 1):
            parts.extend([{'type': 'input_text', 'text': '<image name=[Image #' + str(index) + '] path="'
                          + self.directory + '/image-' + str(index).zfill(3) + '.png">'},
                          {'type': 'input_image', 'image_url': row['data_url'], 'detail': 'high'},
                          {'type': 'input_text', 'text': '</image>'}])
        parts.append({'type': 'input_text', 'text': wire})
        self.events = [
            {'type': 'session_meta', 'payload': {'id': 'thread-test', 'source': 'exec',
                'base_instructions': {'text': self.request['instructions']}}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-6-astra', 'effort': 'high', 'cwd': self.directory}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer',
                'content': [{'type': 'input_text', 'text': declaration}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': parts}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': '{"first":"seen","second":"seen"}'}]}},
        ]

    def inspect(self, events=None):
        return self.adapter.inspect_rollout(events or self.events, self.request, 'thread-test',
                                            self.directory, ['EXCLUDED_CONTROL'])

    def test_exact_images_and_model_have_actual_rollout_evidence(self):
        result = self.inspect()
        self.assertTrue(result['input_verified'], result['issues'])
        self.assertEqual(result['rollout_digest'], digest(self.events))
        self.assertEqual(result['observed_model'], 'gpt-6-astra')
        self.assertEqual(result['observed_effort'], 'high')
        self.assertEqual(result['image_digests'], [row['artifact']['sha256'] for row in self.request['exactory_image_inputs']])

    def test_absent_extra_reordered_or_changed_images_fail(self):
        changes = [lambda p: p.pop(1), lambda p: p.insert(2, copy.deepcopy(p[1])),
                   lambda p: p.__setitem__(slice(0, 6), p[3:6] + p[0:3]),
                   lambda p: p[1].update(image_url=image_row(100)['data_url']),
                   lambda p: p[1].update(detail='low'),
                   lambda p: p[0].update(text='<image name=[Image #1] path="wrong.png">'),
                   lambda p: p.insert(0, {'type': 'input_text', 'text': 'Image loading failed.'})]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                events = copy.deepcopy(self.events)
                change(events[3]['payload']['content'])
                self.assertFalse(self.inspect(events)['input_verified'])

    def test_model_effort_context_count_and_tool_exposure_are_checked(self):
        for key, value in [('model', 'gpt-6-sol'), ('effort', 'xhigh'), ('cwd', '/wrong')]:
            with self.subTest(key=key):
                events = copy.deepcopy(self.events)
                events[1]['payload'][key] = value
                self.assertFalse(self.inspect(events)['input_verified'])
        for extra in [self.events[1], {'type': 'compacted', 'payload': {}},
                      {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec'}}]:
            self.assertFalse(self.inspect(self.events + [extra])['input_verified'])
        self.assertFalse(self.inspect([event for event in self.events if event['type'] != 'turn_context'])['input_verified'])

    def test_exact_codec_is_validated_and_sent_without_outer_json_escaping(self):
        logical = [{'role': 'user', 'content': 'A complete source with alpha and beta.'},
                   {'role': 'assistant', 'content': '{"prior":"kept exactly"}'}]
        wire = codec.encode(logical, {})
        request = dict(self.request, input=[{'role': 'user', 'content': wire}])
        self.assertEqual(self.adapter.packet_text(request), wire)
        self.assertEqual(codec.decode(wire), logical)
        request['input'][0]['content'] = wire + 'unexpected'
        with self.assertRaises(ResearchError):
            self.adapter.packet_text(request)

    def test_invalid_image_claims_do_not_reach_exec(self):
        bad = [lambda r: r[0]['artifact'].update(sha256='0' * 64),
               lambda r: r[0]['artifact'].update(size=1),
               lambda r: r[0]['artifact'].update(media_type='application/pdf'),
               lambda r: r[0].update(data_url='data:image/png;base64,not base64!'),
               lambda r: r[0].update(data_url='https://example.org/image.png')]
        for change in bad:
            request = copy.deepcopy(self.request)
            change(request['exactory_image_inputs'])
            with self.assertRaises(ResearchError):
                self.adapter.native_user_parts(request, self.directory)

    def test_native_wrappers_count_toward_the_user_text_limit(self):
        request = copy.deepcopy(self.request)
        request['input'][0]['content'] = 'x' * (1048576 - 100)
        with self.assertRaises(ResearchError) as failure:
            self.adapter.native_user_parts(request, self.directory)
        self.assertEqual(failure.exception.code, 'review_input_too_large')

    def test_corrupt_png_with_matching_descriptor_is_rejected(self):
        request = copy.deepcopy(self.request)
        data = bytearray(png(90))
        data[-5] ^= 1
        request['exactory_image_inputs'][0] = {
            'artifact': dict(image_row(90)['artifact'], size=len(data), sha256=hashlib.sha256(data).hexdigest()),
            'data_url': 'data:image/png;base64,' + base64.b64encode(data).decode()}
        with self.assertRaises(ResearchError):
            self.adapter.native_user_parts(request, self.directory)

    def test_context_verification_binds_model_images_and_runtime(self):
        runtime = {'adapter': 'codex_cli_visual_v1', 'version': 'test fixture runtime'}
        self.route['runtime'] = runtime
        evidence = self.adapter.inspect_rollout(self.events, self.request, 'thread-test', self.directory,
            ['EXCLUDED_CONTROL'], excluded_images=[png(150)])
        evidence.update(inspected=True, route_digest=digest(self.route), event_digest='observed-events', runtime=runtime)
        response = {'id': 'thread-test', 'context_evidence': evidence}
        with patch.object(self.adapter, 'runtime', return_value=runtime):
            self.assertTrue(self.adapter.verify_context(self.route, self.request, response))
            for key, value in [('observed_model', 'gpt-6-sol'), ('observed_effort', 'low'),
                               ('image_digests', []), ('excluded_image_digests', []), ('runtime', {'version': 'changed'}),
                               ('request_digest', 'wrong')]:
                changed = copy.deepcopy(response)
                changed['context_evidence'][key] = value
                self.assertFalse(self.adapter.verify_context(self.route, self.request, changed))

    def test_excluded_image_is_absent_in_bytes_and_control_text(self):
        excluded = image_row(150)
        data = base64.b64decode(excluded['data_url'].split(',', 1)[1])
        result = self.adapter.inspect_rollout(self.events, self.request, 'thread-test',
            self.directory, ['EXCLUDED_CONTROL'], excluded_images=[data])
        self.assertTrue(result['input_verified'], result['issues'])
        self.assertEqual(result['excluded_image_digests'], [excluded['artifact']['sha256']])
        leaked = copy.deepcopy(self.events)
        leaked[3]['payload']['content'].append({'type': 'input_image', 'image_url': excluded['data_url'], 'detail': 'high'})
        result = self.adapter.inspect_rollout(leaked, self.request, 'thread-test',
            self.directory, ['EXCLUDED_CONTROL'], excluded_images=[data])
        self.assertFalse(result['input_verified'])
        self.assertIn('excluded_image_exposed', result['issues'])

    def test_model_mismatch_is_rejected_before_invocation(self):
        request = dict(self.request, model='gpt-6-sol')
        with self.assertRaises(ResearchError) as failure:
            self.adapter.send_request(self.route, request)
        self.assertEqual(failure.exception.code, 'review_model_mismatch')


if __name__ == '__main__':
    unittest.main()
