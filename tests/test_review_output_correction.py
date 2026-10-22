"""A single observed metadata correction preserves the full scientific review."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness import review_output_correction as correction
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_problem_result_reassessment_binding as fixtures


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ResultReassessmentBindingTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.prepare()
        self.fixture = self.case.fixture
        self.output = copy.deepcopy(self.case.output)
        self.output['value']['reason'] = 'This retained scientific wording differs from the predecessor.'

    def prepare_refused(self):
        for identifier in ('refused-one', 'refused-two'):
            self.case.observe(self.output, identifier)
            with self.assertRaises(ResearchError) as caught:
                self.fixture.mutate(decisions.record_value_review, self.case.payload(self.output))
            self.assertEqual(caught.exception.code, 'review_reassessment_mismatch')
        records = self.fixture.read_records()
        self.assertEqual(set(records['review_output_refusal']), {'refused-one', 'refused-two'})
        self.before = copy.deepcopy(records)

    def corrected(self):
        output = copy.deepcopy(self.output)
        output['reassessment']['findings'][0].update(disposition='revised',
            reason='The retained scientific text differs, so exact confirmation is not applicable.')
        return output

    def delta(self):
        finding = self.corrected()['reassessment']['findings'][0]
        return {'source_output_digest': digest(self.output), 'findings': [
            {key: finding[key] for key in ('review_id', 'disposition', 'reason')}]}

    def correct(self, output, identifier='correction'):
        with patch('research_harness.review_transport.send_request',
                   return_value=self.fixture.build_provider_response(output)) as send:
            result = self.fixture.mutate(correction.invoke_correction,
                {'id': identifier, 'assignment_id': self.case.assignment['id'], 'route_id': 'route'},
                credential='test-only')
        self.assertEqual(send.call_count, 1)
        self.request = send.call_args.args[1]
        return result['result']

    def test_observed_correction_binds_original_scientific_attempt_and_preserves_refusals(self):
        self.prepare_refused()
        expected = self.corrected()
        observed = self.correct(self.delta())
        self.assertEqual(observed['correction_status'], 'accepted')
        packet = json.loads(self.request['input'][0]['content'].removeprefix('The packet below is JSON.\n'))
        self.assertEqual(set(packet), {'protocol', 'original_output', 'historical_reviews', 'diagnostic'})
        self.assertEqual(packet['original_output'], self.output)
        self.assertEqual(packet['diagnostic']['attempt_id'], 'refused-two')
        records = self.fixture.read_records()
        self.assertEqual(records['review_attempt'], self.before['review_attempt'])
        self.assertEqual(records['review_output_refusal'], self.before['review_output_refusal'])
        state = protocol.assignment_state(records, self.fixture.artifacts, self.case.assignment['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['attempt_id'], 'refused-two')
        self.assertEqual(state['output_correction_id'], 'correction')
        self.assertEqual(state['output'], expected)
        saved = self.fixture.mutate(decisions.record_value_review, self.case.payload(expected))['result']
        self.assertEqual(saved['output_correction_id'], 'correction')
        self.assertEqual(saved['payload'], self.case.payload(expected))
        self.fixture.observe('result', 'second')
        gate = decisions.assess_problem_reviews(self.fixture.read_records(), self.fixture.artifacts)
        self.assertTrue(gate['ready'], gate['obligations'])
        self.assertEqual(set(gate['review_ids']), {'repair-final', 'review-result-second'})
        self.assertIn('correction', protocol.usage_report(self.fixture.read_records())['call_ids'])
        with self.assertRaises(ResearchError) as caught:
            self.case.observe(expected, 'third-scientific-call')
        self.assertEqual(caught.exception.code, 'review_slot_exhausted')


if __name__ == '__main__':
    unittest.main()
