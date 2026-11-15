"""Activation-only regressions; held with NEW_PROBLEM_CHAIN_PROTOCOLS for the writer pass."""
import unittest
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
import test_openness_reading_v7_turns as command_fixtures
from research_harness import openness_reading as reading, openness_reading_v7 as v7


class NewChainDefaultTests(OpennessReadingCase):
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    use_released_chain_default = True

    def setUp(self):
        super().setUp()
        self.limit_input(1300000)

    def test_new_chain_records_v7_without_a_protocol_override(self):
        self.assertEqual(reading.NEW_PROBLEM_CHAIN_PROTOCOLS, (v7.PROTOCOL, reading.PROTOCOL_V6))
        receipt = self.assign('turn-1', self.export())
        self.assertEqual(receipt['protocol_selection']['protocol'], v7.PROTOCOL)
        self.assertEqual(reading.find_protocol(self.read_packet('turn-1')), v7.PROTOCOL)
        from research_harness import cli
        status = cli._review_status(self.store.snapshot()['records'], self.artifacts)
        self.assertIn(v7.PROTOCOL, status['new_problem_chain_protocols']['message'])
        self.assertIn(reading.PROTOCOL_V6, status['new_problem_chain_protocols']['message'])

    def test_admitted_v7_and_v6_fallback_use_the_released_default(self):
        self.assertEqual(reading.NEW_PROBLEM_CHAIN_PROTOCOLS, (v7.PROTOCOL, reading.PROTOCOL_V6))
        command_fixtures.V7CommandTests.test_q7_eight_targets_start_v7_and_nine_start_byte_identical_v6(self)


class LegacyContinuationCases:
    reading_protocol = None
    route_model = 'gpt-6-astra'
    route_output_tokens = 128000
    text_sizes = (50000, 50000, 50000)
    use_released_chain_default = True

    def test_existing_chain_continues_with_identical_prompt_and_packet_digests(self):
        import json
        from research_harness import review_protocol as protocol
        self.assertEqual(reading.NEW_PROBLEM_CHAIN_PROTOCOLS, (v7.PROTOCOL, reading.PROTOCOL_V6))
        self.limit_input(1300000)
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
            native = self.export()
            first = self.assign('turn-1', native)
        old_packet = self.read_packet('turn-1')
        old_protocol = reading.find_protocol(old_packet)
        self.invoke('turn-1', self.build_reading_response(old_packet))
        records = self.store.snapshot()['records']
        value = dict(id='turn-2', route_id='route', dossier_id=None, role='native_math',
            reviewer_id=first['reviewer_id'], author_id=first['author_id'],
            context=dict(native_packet=native, prior_assignment_id='turn-1'))
        logical = protocol.build_packet(records, self.artifacts, None, 'native_math', first['reviewer_id'], **value['context'])
        with patch.object(reading, 'NEW_PROBLEM_CHAIN_PROTOCOLS', (reading.PROTOCOL_V6,)):
            expected, _ = reading.prepare_packet(records, self.artifacts, value, records['review_route']['route'], logical)
        expected_prompt = protocol.canonical_prompt('native_math', reading_stage=reading.find_stage(expected),
            reading_protocol=old_protocol, common_version=protocol.find_common_prompt_version(first['prompt']))
        actual = self.assign('turn-2', native, prior='turn-1')
        self.assertEqual(reading.find_protocol(self.read_packet('turn-2')), old_protocol)
        self.assertEqual(actual['packet_digest'], expected['digest'])
        self.assertEqual(actual['prompt_hash'], expected_prompt['rendered_prompt_hash'])
        self.assertEqual(json.loads(self.artifacts.read(actual['packet'])), expected)


class V6ContinuationDefaultTests(LegacyContinuationCases, OpennessReadingCase):
    pass


class V5ContinuationDefaultTests(LegacyContinuationCases, OpennessReadingCase):
    historical_v5 = True
