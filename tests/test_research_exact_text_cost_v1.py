"""Exact reconstruction and prospective profile tests for the cost emitter."""

import copy
import hashlib
import json
from pathlib import Path
import unittest

from research_harness import review_exact_text_v2 as codec
from research_harness import review_exact_text_cost_v1 as emitter
from research_harness.errors import ResearchError


def request_for(messages, sources=None):
    return {
        'model': 'gpt-6-astra',
        'instructions': 'Keep this exact instruction.',
        'input': [{'role': 'user', 'content': codec.encode(messages, sources or {})}],
        'tools': [], 'store': False,
        'exactory_image_inputs': [{'opaque_transport_field': 'unchanged image bytes'}],
    }


class ExactTextCostTests(unittest.TestCase):
    def assert_exact_request(self, request, messages):
        original = copy.deepcopy(request)
        optimized = emitter.optimize_request(request)
        self.assertEqual(codec.decode(optimized['input'][0]['content']), messages)
        self.assertEqual(request, original)
        self.assertEqual({key: value for key, value in optimized.items() if key != 'input'},
                         {key: value for key, value in original.items() if key != 'input'})
        self.assertEqual(optimized, emitter.optimize_request(request))
        return optimized

    def test_profile_binds_the_versioned_module(self):
        self.assertEqual(emitter.profile(), {
            'protocol': 'exact-v2-cost-optimized-v1',
            'implementation_sha256': hashlib.sha256(Path(emitter.__file__).read_bytes()).hexdigest(),
        })

    def test_ordered_objects_keep_exact_json_serialization(self):
        rows = [{'zeta': index, 'alpha': 'value ' + str(index), 'middle': [index, False, None]}
                for index in range(80)]
        value = {'z': rows, 'a': {'second': 'https://example.test/a/b', 'first': 'é λ 日本語'},
                 'm': 'A quote: " and a backslash: \\ and a newline:\n'}
        content = json.dumps(value, ensure_ascii=True, indent=2).replace('/', '\\/') + '\n'
        messages = [{'role': 'user', 'content': content}, {'role': 'assistant', 'content': 'Exact observation.'}]
        request = request_for(messages)
        optimized = self.assert_exact_request(request, messages)
        self.assertLess(len(optimized['input'][0]['content']), len(request['input'][0]['content']))

    def test_reserved_tags_and_decimal_keys_remain_literal_data(self):
        objects = [{tag: {'same_as': 1000, 'nested': [tag, {'verbatim_block': -2}]}}
                   for tag in sorted(codec.TAGS)]
        objects.extend([
            {'2': 'second', '0': 'zero', '01': 'leading zero', '1': 'first'},
            {'same_as': 0, 'z': 'literal', 'a': 'ordered'},
            {'object_fields': [0, []], '1': 'numeric', '0': 'numeric again'},
            {'two': {'exact_text_span': {'sha256': 'not a source'}}, 'one': None},
            {'single': {'literal_object': [['x', 1]]}},
        ])
        messages = [{'role': 'user', 'content': json.dumps(objects, ensure_ascii=False)}]
        self.assert_exact_request(request_for(messages), messages)

    def test_selected_full_sources_and_optional_text_are_exact(self):
        source = ('A selected complete source. Unicode λ and line breaks.\n' * 150)
        optional = 'An optional historical text without additional claims. ' * 120
        identity = hashlib.sha256(source.encode()).hexdigest()
        value = {'source': source, 'optional': optional, 'observed': optional[:700],
                 'nested': {'last': 3, 'first': 1, 'middle': 2}}
        messages = [{'role': 'user', 'content': json.dumps(value, ensure_ascii=False)}]
        optimized = self.assert_exact_request(request_for(messages, {identity: source}), messages)
        text = optimized['input'][0]['content']
        self.assertIn(source + '\n', text)
        envelope = json.loads(text[len(codec.PREFIX):].split('\n', 1)[0])
        header = codec._unpack(envelope)
        self.assertEqual(header['source_sha256'], [identity])

    def test_deep_shared_values_and_short_literals_are_preserved(self):
        repeated = {'category': 'unresolved', 'count': 7, 'positive': True, 'empty': None}
        rows = [{'tail': [repeated, repeated, 'unresolved'], 'head': index, 'middle': {'b': 2, 'a': 1}}
                for index in range(40)]
        messages = [{'role': 'user', 'content': json.dumps(rows)},
                    {'role': 'assistant', 'content': json.dumps({'result': rows[0], 'exact': True})}]
        self.assert_exact_request(request_for(messages), messages)

    def test_rejects_noncanonical_request_shapes_and_corrupt_text(self):
        good = request_for([{'role': 'user', 'content': 'An exact message.'}])
        malformed = []
        for messages in ([], good['input'] * 2, [{'role': 'assistant', 'content': good['input'][0]['content']}],
                         [{'role': 'user', 'content': 'Unencoded text'}]):
            row = copy.deepcopy(good)
            row['input'] = messages
            malformed.append(row)
        row = copy.deepcopy(good)
        row['input'][0]['content'] += 'corrupt suffix'
        malformed.append(row)
        for request in malformed:
            with self.subTest(input=request['input']):
                with self.assertRaises(ResearchError):
                    emitter.optimize_request(request)

    def test_rejects_cycles_and_out_of_bounds_pool_indices(self):
        for pool, root in ([{'same_as': 0}], {'same_as': 0}), ([], {'same_as': 3}):
            header = {'protocol': codec.PROTOCOL, 'messages': root, 'exact_shared_values': pool,
                      'logical_sha256': '0' * 64, 'source_sha256': []}
            request = {'input': [{'role': 'user', 'content': codec.PREFIX + codec._compact(codec._pack(header)) + '\n'}]}
            with self.subTest(pool=pool):
                with self.assertRaises(ResearchError):
                    emitter.optimize_request(request)


if __name__ == '__main__':
    unittest.main()
