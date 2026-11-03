"""Math openness receipts remain valid across a common text upgrade."""

import copy
import unittest
from unittest.mock import patch

from research_harness import review_protocol as protocol
from tests import test_openness_reading_chain as reading_fixtures


class MathOpennessCommonUpgradeTests(unittest.TestCase):
    def test_a_mixed_version_chain_keeps_receipts_in_a_fresh_full_audit(self):
        case = reading_fixtures.MathOpennessReadingTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        with patch.object(protocol, 'COMMON_VERSION', 2):
            case.build_fixture()
            first = case.assign_turn('turn-1')
            case.invoke(first['id'], case.build_reading_response(
                case.read_turn(first['id'])))
            frozen = copy.deepcopy(case.store.snapshot()['records'])
            first_attempt = frozen['review_attempt']['attempt-turn-1']
            first_request = case.artifacts.read(first_attempt['request'])
            before = protocol.assignment_state(
                frozen, case.artifacts, first['id'])
            self.assertTrue(before['ready'], before['obligations'])
        turns = [first]
        while 'reading_turn' in case.read_turn(turns[-1]['id']):
            self.assertLess(len(turns), 40)
            assignment = case.assign_turn(
                'turn-%d' % (len(turns) + 1), turns[-1]['id'])
            turns.append(assignment)
            packet = case.read_turn(assignment['id'])
            output = (case.build_reading_response(packet)
                      if 'reading_turn' in packet else case.review)
            case.invoke(assignment['id'], output)
        case.accept_result()
        receipts = copy.deepcopy(case.read_receipts(turns[-1]['id']))
        stored = copy.deepcopy(case.controller.store.read())
        accepted = copy.deepcopy(
            case.controller.status()['acceptances']['acceptance-000001'])
        audited = case.controller.status(full_audit=True)
        self.assertEqual(audited['freshness']['status'], 'fresh',
                         audited['freshness']['failures'])
        self.assertEqual(audited['acceptances']['acceptance-000001'], accepted)
        self.assertEqual(accepted['status'], 'accepted')
        self.assertEqual(accepted['reading_turns'], receipts)
        self.assertEqual([receipt['assignment_id'] for receipt in receipts],
                         [assignment['id'] for assignment in turns])
        self.assertEqual(case.controller.store.read(), stored)
        self.assertEqual(case.read_receipts(turns[-1]['id']), receipts)
        records = case.store.snapshot()['records']
        for index, assignment in enumerate(turns):
            version = 2 if index == 0 else protocol.COMMON_VERSION
            prompt = assignment['prompt']
            self.assertEqual(prompt['parameters']['common_version'], version)
            self.assertTrue(prompt['text'].startswith(
                protocol._COMMON_PROMPTS[version]))
            state = protocol.assignment_state(
                records, case.artifacts, assignment['id'])
            self.assertTrue(state['ready'], state['obligations'])
            self.assertEqual(state['context_status'], before['context_status'])
        for kind in ('review_assignment', 'review_attempt'):
            for identifier, saved in frozen[kind].items():
                self.assertEqual(records[kind][identifier], saved)
        self.assertEqual(case.artifacts.read(first_attempt['request']),
                         first_request)
        self.assertGreater(protocol.COMMON_VERSION, 2)


if __name__ == '__main__':
    unittest.main()
