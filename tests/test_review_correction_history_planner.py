"""A whole oversized correction history yields an admissible bounded-refusal replacement."""

import copy
import unittest
from unittest.mock import patch

from research_harness import review_continuation as continuation, review_output_correction as correction
from research_harness import review_protocol as protocol
from research_harness.errors import ResearchError
import test_review_output_correction as fixtures


class CorrectionHistoryPlannerTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        super().setUp()

    def test_forty_long_historical_reviews_replace_without_spending_a_correction(self):
        case = fixtures.CorrectionTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.prepare_refused()
        fixture = case.fixture
        assignment_id = case.case.assignment['id']
        packet = copy.deepcopy(correction.prepare_packet(fixture.read_records(), fixture.artifacts, assignment_id))
        original = packet['historical_reviews'][0]
        packet['historical_reviews'] = []
        for number in range(40):
            review = copy.deepcopy(original)
            review['id'] = 'historical-result-' + str(number)
            review['payload']['value']['reason'] = 'h' * 60000
            packet['historical_reviews'].append(review)
        before = copy.deepcopy(fixture.read_records())
        # Inflate only the prepared correction packet. The refusals, route, whole request measurement,
        # notice selection and replacement admission keep their actual recorded implementations.
        with patch.object(correction, 'prepare_packet', return_value=packet):
            for _ in range(2):
                plan = continuation.plan_next(fixture.read_records(), fixture.artifacts, assignment_id)
                self.assertEqual(plan['state'], 'replace', plan)
                self.assertEqual(plan['command'], 'review-assignment')
                self.assertEqual(plan['details']['form'], 'bounded')
                self.assertGreater(plan['details']['correction_preflight']['measurement']['text_characters'],
                                   40 * 60000)
            self.assertEqual(fixture.read_records(), before)
            with self.assertRaises(ResearchError) as caught:
                fixture.mutate(correction.invoke_correction,
                    {'id': 'oversized-correction', 'assignment_id': assignment_id, 'route_id': 'route'},
                    credential='test-only')
            self.assertEqual(caught.exception.code, 'review_input_too_large')
            self.assertFalse(fixture.read_records().get('review_output_correction_invocation'))
            replacement = fixture.mutate(protocol.record_assignment, plan['payload'])['result']
        notice = protocol._read_json(fixture.artifacts, replacement['packet'])['output_refusal']
        self.assertEqual(notice['form'], 'bounded')
        self.assertEqual(replacement['context']['output_refusal'], {'assignment_id': assignment_id})
        next_plan = continuation.plan_next(fixture.read_records(), fixture.artifacts, assignment_id)
        self.assertEqual(next_plan['command'], 'review-run', next_plan)
        self.assertEqual(next_plan['payload']['assignment_id'], replacement['id'])
        self.assertFalse(fixture.read_records().get('review_output_correction_invocation'))


if __name__ == '__main__':
    unittest.main()
