"""Lossless visible text survives the real legacy adapter wrapper."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness import review_exact_text as codec
from research_harness.errors import ResearchError
from research_harness.review_codex_transport import packet_text


def identity(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ExactTextTests(unittest.TestCase):
    def roundtrip(self, messages, sources=None):
        encoded = codec.encode(messages, sources or {})
        wire = packet_text({'input': [{'role': 'user', 'content': encoded}]})
        actual = json.loads(wire)['review_messages'][0]['content']
        self.assertEqual(codec.decode(actual), messages)
        self.assertEqual(codec.encode(messages, sources or {}), encoded)
        return encoded

    def test_actual_wire_preserves_unicode_source_and_exact_span(self):
        full = 'αβγ source equation λ² ≤ m.\n' * 250
        excerpt = full[71:2111]
        messages = [{'role': 'user', 'content': json.dumps({'full': full, 'passage': excerpt}, ensure_ascii=False)},
                    {'role': 'assistant', 'content': 'An unresolved objection, with μ and λ.'}]
        encoded = self.roundtrip(messages, {identity(full): full})
        self.assertIn(full, encoded)
        self.assertIn('exact_text_span', encoded)
        self.assertLess(len(encoded), len(json.dumps(messages, ensure_ascii=False)))

    def test_json_dictionary_order_is_part_of_original_string(self):
        first = json.dumps({'z': 1, 'a': {'b': 'long value ' * 40, 'a': 3}}, indent=2) + '\n'
        second = json.dumps({'a': {'a': 3, 'b': 'long value ' * 40}, 'z': 1}, indent=2) + '\n'
        messages = [{'role': 'user', 'content': json.dumps({'first': first, 'second': second,
                    'repeated': [first] * 8}, sort_keys=True)}]
        self.assertIn('ordered_json_object', self.roundtrip(messages))

    def test_reserved_tags_and_literal_markers_are_ordinary_data(self):
        value = {name: {'same_as': 0, 'verbatim_block': 0} for name in
                 ('same_as', 'verbatim_block', 'exact_text_span', 'exact_json_string',
                  'ordered_json_object', 'literal_object')}
        messages = [{'role': 'user', 'content': json.dumps(value)},
                    {'role': 'assistant', 'content': codec.PREFIX + 'TEXT 0 CHARS 4 SHA256 fake\nbody'}]
        self.roundtrip(messages)

    def test_unrecognized_json_serialization_remains_exact(self):
        text = '{ "z" : 1, "a": "slash\\/and\\u03bb" }\n'
        self.roundtrip([{'role': 'user', 'content': text}])

    def test_hash_mismatch_in_complete_source_is_rejected(self):
        with self.assertRaises(ResearchError):
            codec.encode([{'role': 'user', 'content': 'source'}], {'0' * 64: 'source'})

    def test_source_must_be_present_in_messages(self):
        with self.assertRaises(ResearchError):
            codec.encode([{'role': 'user', 'content': 'unrelated'}], {identity('absent'): 'absent'})

    def test_tampered_block_and_trailing_bytes_are_rejected(self):
        full = 'A complete mathematical text.\n' * 200
        text = self.roundtrip([{'role': 'user', 'content': full}], {identity(full): full})
        for changed in (text.replace(full, 'B' + full[1:]), text + 'trailing', text[:-1]):
            with self.subTest(changed=identity(changed)):
                with self.assertRaises(ResearchError):
                    codec.decode(changed)

    def test_missing_wire_block_is_not_filled_from_a_local_artifact(self):
        full = 'A locally retained source cannot replace a missing wire block.\n' * 120
        encoded = self.roundtrip([{'role': 'user', 'content': full}], {identity(full): full})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / identity(full)
            path.write_text(full)
            header = encoded[len(codec.PREFIX):].split('\n', 1)[0]
            with self.assertRaises(ResearchError):
                codec.decode(codec.PREFIX + header + '\n')
            self.assertEqual(path.read_text(), full)

    def test_bad_shared_reference_and_cycle_are_rejected(self):
        text = codec.encode([{'role': 'user', 'content': 'x' * 300}] * 5, {})
        header_line, blocks = text[len(codec.PREFIX):].split('\n', 1)
        header = json.loads(header_line)
        for values in ([{'same_as': 0}], [{'same_as': 9999}]):
            changed = dict(header, messages={'same_as': 0}, exact_shared_values=values)
            with self.subTest(values=values):
                with self.assertRaises(ResearchError):
                    codec.decode(codec.PREFIX + json.dumps(changed) + '\n' + blocks)

    def test_duplicate_header_keys_are_rejected(self):
        text = codec.encode([{'role': 'user', 'content': 'source'}], {})
        header, blocks = text[len(codec.PREFIX):].split('\n', 1)
        changed = '{"messages": [],' + header[1:]
        with self.assertRaises(ResearchError):
            codec.decode(codec.PREFIX + changed + '\n' + blocks)

    def test_encoding_changes_no_input_structure(self):
        messages = [{'role': 'user', 'content': '{"proof":"original"}'}]
        before = json.dumps(messages)
        self.roundtrip(messages)
        self.assertEqual(json.dumps(messages), before)

    def test_restoration_budget_includes_repeated_source_spans(self):
        full = 'a' * 2000
        messages = [{'role': 'user', 'content': json.dumps({'full': full, 'spans': [full[:1000]] * 10})}]
        encoded = self.roundtrip(messages, {identity(full): full})
        with patch.object(codec, 'MAX_EXPANDED', 5000):
            with self.assertRaises(ResearchError):
                codec.decode(encoded)

    def test_restoration_budget_includes_nested_json_serialization(self):
        inner = json.dumps({'text': '\\' * 500, 'padding': 'data' * 100}, sort_keys=True)
        outer = json.dumps({'first': inner, 'second': inner, 'third': inner}, sort_keys=True)
        messages = [{'role': 'user', 'content': json.dumps({'wrapped': outer}, sort_keys=True)}]
        encoded = self.roundtrip(messages)
        with patch.object(codec, 'MAX_EXPANDED', 5000):
            with self.assertRaises(ResearchError):
                codec.decode(encoded)


if __name__ == '__main__':
    unittest.main()
