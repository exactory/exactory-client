"""A correction that exhausts its output budget cannot supply a corrected review."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_continuation as continuation
from research_harness import review_output_correction as correction, review_protocol as protocol
from research_harness.errors import ResearchError
import test_review_output_correction as fixtures


class CorrectionOutputLimitTests(unittest.TestCase):
    def setUp(self):
        from research_harness import bar_sources, review_protocol
        released = patch.object(review_protocol, 'NEW_SESSION_RULES', (bar_sources.PROTOCOL_V3,))
        released.start()
        self.addCleanup(released.stop)
        self.case = fixtures.CorrectionTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.prepare_refused()
        self.fixture = self.case.fixture
        self.assignment_id = self.case.case.assignment['id']

    def assert_correction_cannot_bind(self, output_tokens):
        response = self.fixture.build_provider_response(self.case.delta())
        response['usage']['output_tokens'] = output_tokens
        with patch('research_harness.review_transport.send_request', return_value=response):
            result = self.fixture.mutate(correction.invoke_correction,
                {'id': 'capped-correction', 'assignment_id': self.assignment_id, 'route_id': 'route'},
                credential='test-only')['result']
        self.assertEqual(result['correction_status'], 'invalid')
        self.assertEqual(result['correction_error']['code'], 'review_output_over_cap')
        self.assertIsNone(result['corrected_output'])
        self.assertEqual(result['usage']['output_tokens'], output_tokens)
        self.assertEqual(json.loads(self.fixture.artifacts.read(result['output'])), self.case.delta())
        records = self.fixture.read_records()
        self.assertEqual(set(records['review_output_correction_invocation']), {'capped-correction'})
        self.assertEqual(records['review_attempt'], self.case.before['review_attempt'])
        self.assertEqual(records['review_output_refusal'], self.case.before['review_output_refusal'])
        self.assertIsNone(correction.selected_correction(records, self.fixture.artifacts, self.assignment_id))
        state = protocol.assignment_state(records, self.fixture.artifacts, self.assignment_id)
        self.assertFalse(state['ready'])
        self.assertIsNone(state['output'])
        with self.assertRaises(ResearchError) as caught:
            self.fixture.mutate(correction.invoke_correction,
                {'id': 'second-correction', 'assignment_id': self.assignment_id, 'route_id': 'route'},
                credential='test-only')
        self.assertEqual(caught.exception.code, 'review_correction_exhausted')
        plan = continuation.plan_next(records, self.fixture.artifacts, self.assignment_id)
        self.assertEqual(plan['state'], 'replace', plan)
        self.assertEqual(plan['command'], 'review-assignment')
        self.assertEqual(plan['details']['form'], 'bounded')
        replacement = self.fixture.mutate(protocol.record_assignment, plan['payload'])['result']
        notice = protocol._read_json(self.fixture.artifacts, replacement['packet'])['output_refusal']
        self.assertEqual(notice['form'], 'bounded')

    def test_at_output_limit_spends_the_correction_without_selecting_it(self):
        self.assert_correction_cannot_bind(1000)

    def test_over_output_limit_spends_the_correction_without_selecting_it(self):
        self.assert_correction_cannot_bind(1001)

    def test_below_output_limit_can_supply_the_corrected_review(self):
        built = correction.build_request(self.fixture.read_records(), self.fixture.artifacts, self.assignment_id, 'route')
        self.assertEqual(built['packet']['diagnostic']['attempt_id'], 'refused-two')
        response = self.fixture.build_provider_response(self.case.delta())
        response['usage']['output_tokens'] = 999
        with patch('research_harness.review_transport.send_request', return_value=response):
            result = self.fixture.mutate(correction.invoke_correction,
                {'id': 'fitting-correction', 'assignment_id': self.assignment_id, 'route_id': 'route'},
                credential='test-only')['result']
        self.assertEqual(result['correction_status'], 'accepted')
        selected = correction.selected_correction(self.fixture.read_records(), self.fixture.artifacts,
                                                 self.assignment_id)
        self.assertEqual(selected['output'], self.case.corrected())

    def test_historical_accepted_correction_keeps_its_meaning_at_the_output_limit(self):
        built = correction.build_request(self.fixture.read_records(), self.fixture.artifacts, self.assignment_id, 'route')
        self.assertEqual(built['packet']['diagnostic']['original_output_digest'], fixtures.digest(self.case.output))
        observed = self.case.correct(self.case.delta())
        records = copy.deepcopy(self.fixture.read_records())
        retained = records['review_output_correction'][observed['id']]
        response = json.loads(self.fixture.artifacts.read(retained['response']))
        response['usage']['output_tokens'] = 1000
        retained['response'] = protocol._put_json(self.fixture.artifacts, response)
        retained['usage']['output_tokens'] = 1000
        # Before the output-budget admission rule, an otherwise exact completed delta was accepted.
        selected = correction.selected_correction(records, self.fixture.artifacts, self.assignment_id)
        self.assertEqual(selected['id'], observed['id'])
        self.assertEqual(selected['output'], self.case.corrected())


if __name__ == '__main__':
    unittest.main()
