"""Problem chains keep excerpt-v3 retention independently of their reading delivery protocol."""

import contextlib
import hashlib
import json
from unittest.mock import patch

from openness_legacy_fixtures import load_0_72_0_openness_reading
from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading as reading, review_excerpts, review_protocol as protocol


class ExcerptProtocolTests(OpennessReadingCase):
    reading_protocol = None
    text_sizes = (3000, 3000)

    def complete_chain(self, *, released=None, replay=False):
        reference = self.export(priority=self.versions)
        expected_protocol = reading.PROTOCOL_V4 if released else reading.PROTOCOL_V5
        source = self.artifacts.read(self.texts[self.versions[0]]).decode('utf-8')
        verified = {'version_id': self.versions[0], 'text': source[:80], 'purpose': 'p' * 800}
        unmatched = {'version_id': self.versions[0], 'text': 'z' * 6001, 'purpose': 'Unmatched copy.'}
        passages = [{'id': 'present', 'version_id': self.versions[0], 'anchor': source[:45], 'characters': 80},
                    {'id': 'missing', 'version_id': self.versions[0], 'anchor': 'z' * 6001, 'characters': 80}]
        prior, turns = None, []
        with patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 3000):
            for number in range(1, 20):
                identifier = 'turn-%d' % number
                builder = released if number == 1 or replay else None
                with (patch.object(protocol, 'openness_reading', builder) if builder else contextlib.nullcontext()):
                    self.assign(identifier, reference, prior)
                turns.append(identifier)
                packet = self.read_packet(identifier)
                record = reading._find_record(packet)
                self.assertEqual(record['protocol'], expected_protocol)
                self.assertEqual(record['excerpt_protocol'], review_excerpts.SESSION_PROTOCOL)
                self.assertEqual('search_delivery' in record, expected_protocol == reading.PROTOCOL_V5)
                if reading.find_stage(packet) == 'final':
                    self.invoke(identifier, self.build_native_result(packet))
                    break
                self.invoke(identifier, self.build_reading_response(
                    packet, excerpts=[verified, verified, unmatched, unmatched], passages=passages))
                prior = identifier
            else:
                self.fail('The excerpt-v3 chain must finish')
            self.assertGreater(len(turns), 2)
            self.assertEqual(len(record['excerpts']), 2)
            self.assertEqual(record['allowances']['excerpts']['used'], 80)
            self.assertEqual(record['excerpts'][0]['purpose'], 'p' * 500)
            self.assertEqual(record['excerpts'][0]['purpose_characters'], 800)
            self.assertEqual(record['excerpts'][0]['purpose_sha256'], hashlib.sha256(b'p' * 800).hexdigest())
            self.assertEqual(record['excerpts'][1]['head'], 'z' * 200)
            self.assertEqual(record['excerpts'][1]['characters'], 6001)
            self.assertNotIn('text', record['excerpts'][1])
            self.assertEqual({entry['status'] for entry in record['passages']}, {'delivered', 'not_found'})
            self.assertNotIn('z' * 201, json.dumps(record))
            records = self.store.snapshot()['records']
            final = records['review_assignment'][identifier]
            before = self.store.snapshot()
            saved = {assignment['packet']['sha256']: self.artifacts.read(assignment['packet'])
                     for assignment in records['review_assignment'].values()}
            receipts = reading.verify_chain(records, self.artifacts, final)
            self.assertEqual([turn['assignment_id'] for turn in receipts], turns)
            if replay:
                self.assertEqual(receipts, released.verify_chain(records, self.artifacts, final))
            self.assertEqual(self.store.snapshot(), before)
            for assignment in records['review_assignment'].values():
                self.assertEqual(self.artifacts.read(assignment['packet']), saved[assignment['packet']['sha256']])

    def test_stored_0_72_0_v4_chain_replays_with_excerpt_v3(self):
        self.complete_chain(released=load_0_72_0_openness_reading(), replay=True)

    def test_unfinished_0_72_0_v4_chain_keeps_excerpt_v3_on_continuation(self):
        self.complete_chain(released=load_0_72_0_openness_reading())

    def test_new_v5_chain_uses_excerpt_v3_and_rebuilds_its_bounded_record(self):
        self.complete_chain()
