"""Problem final turns require delivered passages under v4 and retain recorded v3 behavior."""

import json
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase, build_theorem
from research_harness import openness_reading, review_excerpts, review_protocol as protocol
from research_harness import review_transport as transport


PROTOCOL_V3 = 'exactory-openness-reading-v3'
PROTOCOL_V4 = 'exactory-openness-reading-v4'


class ProblemOpennessPassageProgressTests(OpennessReadingCase):
    def start_final_turn(self):
        self.limit_input()
        native = self.export(priority=self.versions)
        turns = self.run_reading_turns(native)
        return native, turns[-1]

    def build_passage(self, identifier='found', characters=90, missing=False):
        return {'id': identifier, 'version_id': self.versions[0], 'characters': characters,
                'anchor': ('This phrase is absent from every bound body.' if missing else build_theorem(1)[:40])}

    def assert_refused_response(self, native, final, passages):
        packet = self.read_packet(final)
        receipt = self.invoke(final, self.build_reading_response(packet, passages=passages))
        records = self.store.snapshot()['records']
        refusal = records.get('review_output_refusal', {}).get('attempt-' + final)
        self.assertIsNotNone(refusal, 'A final response without delivered passage text must be refused')
        self.assertEqual((refusal['code'], refusal['check']), ('invalid_reading_response', 'native.output'))
        self.assertEqual(refusal['details'], {'message': openness_reading.PASSAGE_PROGRESS_ERR_MSG,
                                              'path': '/passage_requests'})
        self.assertEqual(receipt['output_refusal']['details'], refusal['details'])
        self.assertFalse(protocol.assignment_state(records, self.artifacts, final)['ready'])
        self.assert_error('review_source_continuation_unverified',
                          lambda: self.assign('turn-refused', native, prior=final))
        self.assertNotIn('turn-refused', self.store.snapshot()['records']['review_assignment'])
        self.assertEqual(self.read_packet(final), packet)
        self.invoke(final, self.build_native_result(packet), attempt='attempt-native-result')
        self.assertTrue(protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, final)['ready'])

    def test_new_problem_final_refuses_only_missing_anchors(self):
        native, final = self.start_final_turn()
        self.assert_refused_response(native, final, [self.build_passage(missing=True)])
        self.assertEqual(self.read_packet(final)['reading_record']['protocol'], PROTOCOL_V4)

    def test_final_refuses_passages_exceeding_positive_remaining_allowance(self):
        with patch.object(review_excerpts, 'SESSION_PASSAGE_CHARACTERS', 100):
            native, final = self.start_final_turn()
        self.invoke(final, self.build_reading_response(self.read_packet(final), passages=[self.build_passage()]))
        self.assign('turn-budget', native, prior=final)
        record = self.read_packet('turn-budget')['reading_record']
        self.assertEqual(record['allowances']['passages'], {'characters': 100, 'used': 90})
        self.assertEqual(record['passages'][0]['status'], 'delivered')
        self.assert_refused_response(native, 'turn-budget', [self.build_passage('large-1', 11),
                                                            self.build_passage('large-2', 20)])

    def test_final_continues_with_one_delivered_record_passage_and_one_missing(self):
        native, final = self.start_final_turn()
        binding = self.read_packet(final)['native']
        records = self.store.snapshot()['records']
        rows = openness_reading.list_reading_rows(records, self.artifacts, binding)
        row = next(row for row in rows if row.get('record_kind') == 'reading')
        body = self.artifacts.read(row['artifact']).decode('utf-8')
        found = {'id': 'record-found', 'record_kind': 'reading', 'record_id': row['record_id'],
                 'anchor': body[:40], 'characters': 90}
        self.invoke(final, self.build_reading_response(self.read_packet(final),
                                                      passages=[self.build_passage('missing', missing=True), found]))
        self.assertTrue(protocol.assignment_state(self.store.snapshot()['records'], self.artifacts, final)['ready'])
        self.assign('turn-further', native, prior=final)
        record = self.read_packet('turn-further')['reading_record']
        self.assertEqual([(answer['request_id'], answer['status']) for answer in record['passages']],
                         [('missing', 'not_found'), ('record-found', 'delivered')])
        self.assertEqual(record['passages'][1]['text'], body[:90])
        self.assertEqual(record['allowances']['passages']['used'], 90)
        self.assertEqual(record['protocol'], PROTOCOL_V4)

    def test_recorded_v3_rebuilds_and_checks_its_output_before_continuing_under_v4(self):
        with patch.object(openness_reading, 'find_chain_protocol', return_value=PROTOCOL_V3):
            native, final = self.start_final_turn()
        records = self.store.snapshot()['records']
        assignment = records['review_assignment'][final]
        packet_bytes = self.artifacts.read(assignment['packet'])
        packet = json.loads(packet_bytes)
        prompt_bytes = json.dumps(assignment['prompt'], sort_keys=True).encode('utf-8')
        # Rebuild the saved final packet from its bound native packet, record and observed earlier turns.
        rows = openness_reading.list_reading_rows(records, self.artifacts, packet['native'])
        spans = openness_reading._compute_spans(self.artifacts, rows)
        chain = openness_reading._list_chain(records, assignment)
        observations = openness_reading._observe_turns(records, self.artifacts, chain[:-1], rows, spans)
        with transport.text_only_delivery(True):
            ordinary = protocol.build_packet(records, self.artifacts, None, 'native_math', 'openness-reviewer',
                                             native_packet=native)
            rebuilt = openness_reading._build_final_turn(records['review_route']['route'], ordinary,
                                                         packet['reading_record'], rows, spans, observations)
        self.assertEqual(json.dumps(rebuilt, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(), packet_bytes)
        self.assertEqual(json.dumps(protocol.canonical_prompt('native_math', reading_stage='final',
                                                              reading_protocol=PROTOCOL_V3), sort_keys=True).encode(),
                         prompt_bytes)
        self.invoke(final, self.build_reading_response(packet, passages=[self.build_passage(missing=True)]))
        records = self.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-' + final]
        self.assertNotIn('attempt-' + final, records.get('review_output_refusal', {}))
        self.assertTrue(protocol.assignment_state(records, self.artifacts, final)['ready'])
        self.assertTrue(protocol.authenticate_attempt(records, self.artifacts, assignment, attempt))
        rebuilt_request = protocol.build_assignment_request(records, self.artifacts, assignment)
        self.assertEqual(json.dumps(rebuilt_request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(),
                         self.artifacts.read(attempt['request']))
        self.assertEqual(self.artifacts.read(assignment['packet']), packet_bytes)
        self.assign('turn-v4', native, prior=final)
        record = self.read_packet('turn-v4')['reading_record']
        self.assertEqual([(answer['request_id'], answer['status']) for answer in record['passages']],
                         [('found', 'not_found')])
        self.assertEqual(record['protocol'], PROTOCOL_V4)
