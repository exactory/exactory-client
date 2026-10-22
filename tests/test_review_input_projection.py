"""Actual input bytes, not descriptors or earlier stored packets, supply coverage."""

import base64
import copy
import json
import unittest

from research_harness.artifacts import describe_artifact
from research_harness.errors import ResearchError
from research_harness import review_exact_text, review_exact_text_v2, review_transport as transport
from research_harness.review_input_projection import packets_from_request


class ActualInputProjectionTests(unittest.TestCase):
    def setUp(self):
        self.data = b'%PDF-1.4\nExact actual bytes\n%%EOF\n'
        self.reference = describe_artifact(self.data, 'application/pdf')
        self.encoded = base64.b64encode(self.data).decode()
        self.packet = {'protocol': 'exactory-problem-review-v2', 'role': 'result', 'reviewer_id': 'first',
                       'dossier_id': None, 'subject': {'kind': 'problem', 'statement_id': 'statement-1'},
                       'digest': '1' * 64, 'problem_evidence_digest': 'current', 'evidence': [
            {'artifact': self.reference, 'encoding': 'base64', 'delivery': 'attached-exact-bytes'}]}
        self.file = {'type': 'input_file', 'filename': self.reference['sha256'] + '.pdf',
                     'file_data': 'data:application/pdf;base64,' + self.encoded}

    def message(self, packet=None):
        return {'role': 'user', 'content': transport.PACKET_LABEL + json.dumps(packet or self.packet)}

    def request(self, file=None, packet=None):
        text = self.message(packet)['content']
        return {'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}, file or self.file]}]}

    def test_exact_native_file_bytes_are_projected_without_an_artifact_store(self):
        request = self.request()
        before = copy.deepcopy(request)
        packets = packets_from_request(request, native_attachments=True)
        self.assertEqual(packets[0]['evidence'][0]['content'], self.encoded)
        self.assertEqual(request, before)

    def test_inline_base64_prose_is_not_native_file_delivery(self):
        packet = copy.deepcopy(self.packet)
        packet['evidence'][0]['content'] = self.encoded
        projected = packets_from_request({'input': [self.message(packet)]}, native_attachments=True)
        self.assertNotIn('content', projected[0]['evidence'][0])
        self.assertEqual(projected[0]['evidence'][0]['encoding'], 'omitted')

    def test_forged_text_encoding_cannot_deliver_a_binary_descriptor(self):
        for media in ('application/pdf', 'image/png', 'application/octet-stream'):
            packet = copy.deepcopy(self.packet)
            packet['evidence'][0].update(encoding='utf-8', content=self.data.decode())
            packet['evidence'][0]['artifact']['media_type'] = media
            with self.subTest(media=media):
                projected = packets_from_request({'input': [self.message(packet)]}, native_attachments=True)
                self.assertNotIn('content', projected[0]['evidence'][0])

    def test_packet_label_does_not_replace_native_protocol_role_or_identity(self):
        for key, value in [('protocol', 'invented-protocol'), ('role', 'assistant'), ('reviewer_id', ''),
                           ('dossier_id', 'unrelated-dossier'), ('subject', {'kind': 'paper'}),
                           ('digest', 'not-a-native-digest')]:
            packet = dict(self.packet, **{key: value})
            with self.subTest(field=key):
                with self.assertRaises(ResearchError):
                    packets_from_request({'input': [self.message(packet)]})

    def test_text_only_route_cannot_turn_multipart_metadata_into_actual_attachments(self):
        projected = packets_from_request(self.request())
        self.assertNotIn('content', projected[0]['evidence'][0])

    def test_changed_hash_size_media_and_missing_bytes_never_restore_a_descriptor(self):
        for key, value in [('sha256', '0' * 64), ('size', len(self.data) + 1), ('media_type', 'image/png')]:
            packet = copy.deepcopy(self.packet)
            packet['evidence'][0]['artifact'][key] = value
            with self.subTest(field=key):
                projected = packets_from_request(self.request(packet=packet), native_attachments=True)
                self.assertNotIn('content', projected[0]['evidence'][0])
        for value in ('data:application/pdf;base64,%%%%', 'data:application/pdf;base64,',
                      'data:image/png;base64,' + self.encoded, 'https://example.org/unfetched.pdf'):
            with self.subTest(data=value):
                projected = packets_from_request(self.request(file=dict(self.file, file_data=value)), native_attachments=True)
                self.assertNotIn('content', projected[0]['evidence'][0])

    def test_attachment_from_another_message_does_not_fill_the_current_packet(self):
        first = self.request()['input'][0]
        second = self.message()
        packets = packets_from_request({'input': [first, second]}, native_attachments=True)
        self.assertEqual(packets[0]['evidence'][0]['content'], self.encoded)
        self.assertNotIn('content', packets[1]['evidence'][0])

    def test_both_exact_codecs_preserve_only_the_actual_logical_packets(self):
        packet = dict(self.packet, evidence=[],
                  scientific_history= [{'packet': {'evidence': [{'content': 'Not a separate delivered packet.'}]}}])
        message = self.message(packet)
        messages = [message, {'role': 'assistant', 'content': message['content']},
                    {'role': 'user', 'content': 'An ordinary non-packet control.'}]
        for codec in (review_exact_text, review_exact_text_v2):
            encoded = codec.encode(messages, {})
            request = {'input': [{'role': 'user', 'content': encoded}]}
            with self.subTest(protocol=codec.PROTOCOL):
                self.assertEqual(packets_from_request(request), [packet])
                with self.assertRaises(ResearchError):
                    packets_from_request({'input': [{'role': 'user', 'content': encoded.replace('current', 'altered', 1)}]})

    def test_ambiguous_or_malformed_native_text_cannot_supply_a_packet(self):
        request = self.request()
        request['input'][0]['content'].append({'type': 'input_text', 'text': 'Unbound extra instructions.'})
        with self.assertRaises(ResearchError):
            packets_from_request(request, native_attachments=True)
        for text in ('[]', '{"evidence":[],"evidence":[]}'):
            with self.subTest(text=text):
                with self.assertRaises(ResearchError):
                    packets_from_request({'input': [{'role': 'user', 'content': transport.PACKET_LABEL + text}]})

    def test_native_repeated_parts_supply_only_the_actual_later_content(self):
        from research_harness.research_decisions import find_undelivered_bar_ids
        from research_harness.review_supplied_evidence import list_delivered_objections
        parts = {'bars': [{'id': 'bar-1'}, {'id': 'bar-2'}], 'own_bars': [{'id': 'bar-1'}],
                 'material_objections': [{'id': 'objection-1'}]}
        current = dict(self.packet, evidence=[], **parts)
        compacted = transport._compact_history_message(self.message(current), repeated=parts)
        request = {'input': [compacted, self.message(current)]}
        before = copy.deepcopy(request)
        projected = packets_from_request(request)
        for key, value in parts.items():
            self.assertNotIn(key, projected[0])
            self.assertEqual(projected[1][key], value)
        self.assertEqual(find_undelivered_bar_ids({}, None, 'unused', ['bar-1', 'bar-2'], packets=projected), [])
        self.assertEqual(list_delivered_objections(projected[0]), [])
        self.assertEqual(list_delivered_objections(projected[1]), parts['material_objections'])
        self.assertEqual(request, before)

    def test_repeated_marker_without_current_content_cannot_supply_bars(self):
        from research_harness.research_decisions import find_undelivered_bar_ids
        current = dict(self.packet, evidence=[], bars=[{'id': 'bar-1'}])
        compacted = transport._compact_history_message(self.message(current), repeated={'bars': current['bars']})
        projected = packets_from_request({'input': [compacted]})
        self.assertEqual(find_undelivered_bar_ids({}, None, 'unused', ['bar-1'], packets=projected), ['bar-1'])

    def test_only_exact_top_level_repeated_markers_are_removed(self):
        marker = {'delivery': 'repeated-in-the-current-packet', 'note': transport.REPEATED_PART_NOTE}
        variants = [dict(marker, note='Unrecognized omission.'), dict(marker, delivery='unrecognized'),
                    dict(marker, id='bar-1'), {'id': 'bar-1'}]
        for value in variants:
            packet = dict(self.packet, evidence=[], bars=value, nested={'bars': marker})
            with self.subTest(value=value):
                projected = packets_from_request({'input': [self.message(packet)]})[0]
                self.assertEqual(projected['bars'], value)
                self.assertEqual(projected['nested']['bars'], marker)


if __name__ == '__main__':
    unittest.main()
