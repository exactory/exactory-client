"""Overflow-only visible factoring preserves the complete legacy-adapter input."""

import copy
import hashlib
import importlib
import importlib.util
import json
import unittest
from unittest.mock import patch

from research_harness import review_exact_text as v1, review_transport as transport
from research_harness import review_source_complete as complete
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.review_codex_transport import packet_text
from test_review_html_delivery import ROUTE


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def fixture():
    source = 'αβγ complete synthetic source with λ² and an exact / character.\n' * 150
    fields = [{'third': 'https://example.test/path/' + str(i), 'first': 'shared field value ' * 10,
               'second': i, 'fourth': {'z': 'original order', 'a': 'original \\/ and /'}} for i in range(80)]
    raw = json.dumps(fields, ensure_ascii=True, separators=(',', ':')).replace('/', '\\/')
    payload = {'source': source, 'span': source[43:1444], 'first': raw, 'second': raw,
               'repeated_metadata': fields, 'repeated_again': fields}
    messages = [{'role': 'user', 'content': json.dumps(payload, sort_keys=True, ensure_ascii=False)},
                {'role': 'assistant', 'content': 'Original unresolved response, retained in full.'}]
    return messages, {sha(source): source}


class ExactTextV2Tests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('research_harness.review_exact_text_v2'),
                             'The separately versioned overflow renderer is not implemented')
        self.codec = importlib.import_module('research_harness.review_exact_text_v2')

    def roundtrip(self, messages, sources=None):
        encoded = self.codec.encode(messages, sources or {})
        wire = packet_text({'input': [{'role': 'user', 'content': encoded}]})
        actual = json.loads(wire)['review_messages'][0]['content']
        self.assertEqual(self.codec.decode(actual), messages)
        self.assertEqual(self.codec.encode(messages, sources or {}), encoded)
        self.assertEqual(transport.logical_messages({'input': [{'role': 'user', 'content': actual}]}), messages)
        return encoded

    def header(self, encoded):
        line, blocks = encoded[len(self.codec.PREFIX):].split('\n', 1)
        return json.loads(line), blocks

    def rejected(self, header, blocks=''):
        with self.assertRaises(ResearchError):
            self.codec.decode(self.codec.PREFIX + json.dumps(header) + '\n' + blocks)

    def test_actual_wire_retains_full_source_span_solidus_and_original_json_order(self):
        messages, sources = fixture()
        encoded = self.roundtrip(messages, sources)
        header, _ = self.header(encoded)
        self.assertTrue(header['object_keys'])
        self.assertTrue(header['key_names'])
        for tag in ('exact_text_span', 'same_as', 'verbatim_block', 'ordered_json_object', 'escape_solidus'):
            self.assertIn(tag, encoded)
        self.assertIn(next(iter(sources.values())), encoded)
        self.assertLess(len(packet_text({'input': [{'role': 'user', 'content': encoded}]})),
                        len(packet_text({'input': [{'role': 'user', 'content': v1.encode(messages, sources)}]})))

    def test_every_reserved_name_and_literal_marker_remains_ordinary_data(self):
        payload = {name: {'object_fields': [0, ['literal']], 'same_as': 0} for name in
                   ('object_fields', 'same_as', 'verbatim_block', 'exact_text_span', 'exact_json_string',
                    'ordered_json_object', 'literal_object', 'key_names', 'object_keys', 'structure', 'protocol')}
        self.roundtrip([{'role': 'user', 'content': json.dumps(payload)},
                        {'role': 'assistant', 'content': 'TEXT 0 CHARS 4 SHA256 fake\nbody'}])

    def test_escaped_solidus_mixed_with_literal_backslashes_unicode_and_controls(self):
        payload = {'z': '\\/' * 40 + '/' * 40 + 'λ\n\t', 'a': {'z': 'last', 'a': 'first'}}
        for style in ({'indent': 2}, {'separators': (',', ':')}, {}):
            for ascii_only in (True, False):
                raw = json.dumps(payload, ensure_ascii=ascii_only, **style).replace('/', '\\/') + '\n'
                with self.subTest(style=style, ascii_only=ascii_only):
                    self.roundtrip([{'role': 'user', 'content': raw}])

    def test_unrecognized_json_spelling_is_verbatim(self):
        raw = '{ "z" : "a\\/b", "a" : "\\u03bb" }\n'
        self.roundtrip([{'role': 'user', 'content': raw}])

    def test_field_alias_json_order_cannot_change_original_nested_json_order(self):
        raw = json.dumps({'z': {'a': 'first' * 40, 'b': 'middle', 'c': 'last'}, 'a': 4})
        messages = [{'role': 'user', 'content': raw}]
        encoded = self.codec.encode(messages, {})
        header, blocks = self.header(encoded)
        names = header['key_names']
        aliases = {str(index): str(len(names) - index - 1) for index in range(len(names))}
        def reindex(value):
            if isinstance(value, dict):
                return {aliases[key]: reindex(child) for key, child in value.items()}
            return [reindex(child) for child in value] if isinstance(value, list) else value
        changed = dict(header, key_names=list(reversed(names)), structure=reindex(header['structure']))
        wire = self.codec.PREFIX + json.dumps(changed, sort_keys=True) + '\n' + blocks
        self.assertEqual(self.codec.decode(wire), messages)

    def test_source_missing_or_wrong_digest_is_rejected(self):
        for sources in ({'0' * 64: 'wrong'}, {sha('absent'): 'absent'}):
            with self.subTest(sources=sources):
                with self.assertRaises(ResearchError):
                    self.codec.encode([{'role': 'user', 'content': 'other'}], sources)

    def test_field_name_table_rejects_duplicate_or_nonstring_names(self):
        header, blocks = self.header(self.codec.encode(*fixture()))
        for names in ([*header['key_names'], header['key_names'][0]], [3], ['0', False]):
            self.rejected(dict(header, key_names=names), blocks)

    def test_field_indices_are_canonical_and_in_range(self):
        header, blocks = self.header(self.codec.encode(*fixture()))
        for key in ('-1', '00', '+0', ' 0', '1.0', str(len(header['key_names'])), 'same_as'):
            self.rejected(dict(header, structure={key: 'invalid'}), blocks)

    def test_object_shape_index_and_arity_are_exact(self):
        header, blocks = self.header(self.codec.encode(*fixture()))
        tag = str(header['key_names'].index('object_fields'))
        for row in ([True, []], [-1, []], [999999, []], [0, []], [0, 'not a list'], [0, [], 1]):
            self.rejected(dict(header, structure={tag: row}), blocks)

    def test_duplicate_object_fields_and_duplicate_shapes_are_rejected(self):
        header, blocks = self.header(self.codec.encode(*fixture()))
        for shapes in ([['x', 'x', 'y']], [header['object_keys'][0]] * 2, [[0, 'x', 'y']]):
            self.rejected(dict(header, object_keys=shapes), blocks)

    def test_unknown_protocol_duplicate_header_and_trailing_bytes_are_rejected(self):
        text = self.codec.encode(*fixture())
        header, blocks = self.header(text)
        self.rejected(dict(header, protocol=v1.PROTOCOL), blocks)
        duplicate = '{"structure": [],' + json.dumps(header)[1:]
        for changed in (self.codec.PREFIX + duplicate + '\n' + blocks, text + 'trailing', text[:-1]):
            with self.assertRaises(ResearchError):
                self.codec.decode(changed)

    def test_missing_or_tampered_visible_source_cannot_be_repaired_from_digest(self):
        messages, sources = fixture()
        text = self.codec.encode(messages, sources)
        header, blocks = self.header(text)
        self.rejected(header)
        source = next(iter(sources.values()))
        with self.assertRaises(ResearchError):
            self.codec.decode(text.replace(source, 'X' + source[1:]))

    def test_logical_hash_tamper_is_rejected(self):
        header, blocks = self.header(self.codec.encode(*fixture()))
        def tamper(value):
            if isinstance(value, dict):
                return {key: ('0' * 64 if header['key_names'][int(key)] == 'logical_sha256' else tamper(child))
                        for key, child in value.items()}
            return [tamper(child) for child in value] if isinstance(value, list) else value
        changed = copy.deepcopy(header)
        changed['structure'] = tamper(changed['structure'])
        self.assertNotEqual(changed, header)
        self.rejected(changed, blocks)

    def test_table_and_decoded_field_expansion_has_finite_budget(self):
        text = self.codec.encode(*fixture())
        with patch.object(self.codec, 'MAX_EXPANDED', 2000):
            with self.assertRaises(ResearchError):
                self.codec.decode(text)

    def test_shared_value_cycles_invalid_indices_and_boolean_indices_are_rejected(self):
        envelope, blocks = self.header(self.codec.encode([{'role': 'user', 'content': 'value ' * 60}] * 5, {}))
        original = self.codec._unpack(envelope)
        for reference in (0, 999999, -1, True):
            changed = dict(original, messages={'same_as': 0}, exact_shared_values=[{'same_as': reference}])
            with self.subTest(reference=reference):
                self.rejected(self.codec._pack(changed), blocks)

    def test_exact_source_span_rejects_byte_boundaries_hashes_and_absent_sources(self):
        source = 'λβγ source / line.\n' * 200
        messages = [{'role': 'user', 'content': source}]
        envelope, blocks = self.header(self.codec.encode(messages, {sha(source): source}))
        original = self.codec._unpack(envelope)
        row = {'sha256': sha(source), 'byte_start': 0, 'byte_end': 100, 'text_sha256': sha(source[:100])}
        for changes in ({'byte_start': -1}, {'byte_end': len(source.encode()) + 1}, {'byte_start': True},
                        {'byte_start': 1}, {'byte_end': 0}, {'sha256': '0' * 64}, {'text_sha256': '0' * 64}):
            changed = dict(original, messages=[{'role': 'user', 'content': {'exact_text_span': dict(row, **changes)}}])
            with self.subTest(changes=changes):
                self.rejected(self.codec._pack(changed), blocks)

    def test_restoration_budget_accounts_for_repeated_exact_spans_after_unpack(self):
        source = 'a' * 2000
        messages = [{'role': 'user', 'content': json.dumps({'full': source, 'spans': [source[:1000]] * 15})}]
        text = self.codec.encode(messages, {sha(source): source})
        envelope, _ = self.header(text)
        with patch.object(self.codec, 'MAX_EXPANDED', 10000):
            self.codec._unpack(envelope)
            with self.assertRaisesRegex(ResearchError, 'Restored values and JSON strings'):
                self.codec.decode(text)

    def test_restoration_budget_accounts_for_nested_json_solidus_expansion(self):
        inner = json.dumps({'path': '/' * 500, 'padding': 'data' * 100}, sort_keys=True).replace('/', '\\/')
        outer = json.dumps({'first': inner, 'second': inner, 'third': inner}, sort_keys=True)
        messages = [{'role': 'user', 'content': outer}]
        text = self.codec.encode(messages, {})
        envelope, _ = self.header(text)
        with patch.object(self.codec, 'MAX_EXPANDED', 8000):
            self.codec._unpack(envelope)
            with self.assertRaisesRegex(ResearchError, 'Restored values and JSON strings'):
                self.codec.decode(text)

    def test_ordered_object_duplicate_keys_and_wrong_arity_are_rejected(self):
        envelope, blocks = self.header(self.codec.encode([{'role': 'user', 'content': 'original'}], {}))
        original = self.codec._unpack(envelope)
        for row in ({'keys': ['x', 'x'], 'values': [1, 2]}, {'keys': ['x'], 'values': []},
                    {'keys': [False], 'values': ['bad']}):
            changed = dict(original, messages=[{'role': 'user', 'content': {'ordered_json_object': row}}])
            self.rejected(self.codec._pack(changed), blocks)

    def test_serialization_options_and_solidus_flag_are_strictly_typed(self):
        envelope, blocks = self.header(self.codec.encode([{'role': 'user', 'content': 'original'}], {}))
        original = self.codec._unpack(envelope)
        exact = {'prefix': '', 'suffix': '', 'options': {'sort_keys': True, 'ensure_ascii': True},
                 'sha256': sha('{}'), 'value': {}}
        for changes in ({'escape_solidus': 1}, {'options': ['sort_keys', 'ensure_ascii']},
                        {'options': {'sort_keys': 1, 'ensure_ascii': True}}, {'prefix': []}):
            changed = dict(original, messages=[{'role': 'user', 'content': {'exact_json_string': dict(exact, **changes)}}])
            self.rejected(self.codec._pack(changed), blocks)

    def test_deep_table_structure_is_rejected_before_unbounded_recursion(self):
        header, blocks = self.header(self.codec.encode(*fixture()))
        value = 'leaf'
        for _ in range(270):
            value = [value]
        self.rejected(dict(header, structure=value), blocks)

    def test_codec_does_not_mutate_logical_input(self):
        messages, sources = fixture()
        original = copy.deepcopy(messages)
        self.roundtrip(messages, sources)
        self.assertEqual(messages, original)

    def test_overflow_dispatch_uses_real_adapter_size_and_keeps_small_v1_exact(self):
        messages, sources = fixture()
        packet = {complete.MARKER: {'protocol': complete.PROTOCOL, 'history_digest': digest(messages),
                  'sources': [], 'alternate_body_sha256': [], 'protected_sha256': [], 'restored_evidence': []},
                  'payload': messages[0]['content']}
        logical, selected = complete.project(packet, messages)
        old = transport.request_body(ROUTE, 'Review JSON input.', packet, messages)
        expected_v1 = [{'role': 'user', 'content': v1.encode(logical, selected)}]
        self.assertEqual(old['input'], expected_v1)
        new_input = [{'role': 'user', 'content': self.codec.encode(logical, selected)}]
        large_size = len(packet_text(old))
        small_size = len(packet_text(dict(old, input=new_input)))
        self.assertLess(small_size, large_size)
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', (large_size + small_size) // 2):
            actual = transport.request_body(ROUTE, 'Review JSON input.', packet, messages)
        self.assertEqual(actual, dict(old, input=new_input))
        self.assertEqual(transport.logical_messages(actual), logical)

    def test_oversized_v2_remains_oversized_without_any_logical_omission(self):
        packet = {complete.MARKER: {'protocol': complete.PROTOCOL, 'history_digest': digest([]),
                  'sources': [], 'alternate_body_sha256': [], 'protected_sha256': [], 'restored_evidence': []},
                  'proof': 'All exact proof text remains present. ' * 1000}
        with patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 100):
            actual = transport.request_body(ROUTE, 'Review JSON input.', packet)
        self.assertTrue(self.codec.is_encoded(actual['input'][0]['content']))
        self.assertGreater(len(packet_text(actual)), 100)
        self.assertEqual(transport.logical_messages(actual), complete.project(packet, [])[0])


if __name__ == '__main__':
    unittest.main()
