"""Bounded correction rejects new scientific content and preserves its single allowance."""

import copy
import unittest
from unittest.mock import patch

from research_harness import review_output_correction as correction, review_protocol as protocol
from research_harness.errors import ResearchError
import test_review_output_correction as fixtures


class CorrectionNegativeTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.CorrectionTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.fixture = self.case.fixture

    def assert_no_binding_or_original_changes(self):
        records = self.fixture.read_records()
        self.assertEqual(records['review_attempt'], self.case.before['review_attempt'])
        self.assertEqual(records['review_output_refusal'], self.case.before['review_output_refusal'])
        state = protocol.assignment_state(records, self.fixture.artifacts, self.case.case.assignment['id'])
        self.assertIsNone(state['output'])
        self.assertFalse(state['ready'])

    def assert_correction_exhausted(self):
        with patch('research_harness.review_transport.send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                self.fixture.mutate(correction.invoke_correction,
                    {'id': 'forbidden-second-correction', 'assignment_id': self.case.case.assignment['id'],
                     'route_id': 'route'}, credential='test-only')
        self.assertEqual(caught.exception.code, 'review_correction_exhausted')
        send.assert_not_called()

    def test_scientific_grade_injection_is_invalid_and_consumes_the_single_allowance(self):
        self.case.prepare_refused()
        response = self.case.delta()
        response['value'] = copy.deepcopy(self.case.output['value'])
        response['value']['rubric']['priority'] = 3
        observed = self.case.correct(response)
        self.assertEqual(observed['correction_status'], 'invalid')
        self.assert_no_binding_or_original_changes()
        self.assert_correction_exhausted()

    def test_even_existing_evidence_cannot_be_changed_by_the_correction(self):
        self.case.prepare_refused()
        response = self.case.delta()
        response['findings'][0]['evidence'] = [copy.deepcopy(self.fixture.supporting)]
        observed = self.case.correct(response)
        self.assertEqual(observed['correction_status'], 'invalid')
        self.assert_no_binding_or_original_changes()

    def test_an_unnamed_finding_cannot_be_changed(self):
        self.case.prepare_refused()
        response = self.case.delta()
        response['findings'][0]['review_id'] = 'a-different-historical-finding'
        observed = self.case.correct(response)
        self.assertEqual(observed['correction_status'], 'invalid')
        self.assert_no_binding_or_original_changes()

    def test_the_delta_must_bind_the_exact_original_output_digest(self):
        self.case.prepare_refused()
        response = self.case.delta()
        response['source_output_digest'] = '0' * 64
        observed = self.case.correct(response)
        self.assertEqual(observed['correction_status'], 'invalid')
        self.assert_no_binding_or_original_changes()

    def test_declining_consumes_the_single_allowance_without_binding_a_review(self):
        self.case.prepare_refused()
        observed = self.case.correct({'correction': 'declined',
            'reason': 'I decline to provide the requested structural classification correction.'})
        self.assertEqual(observed['correction_status'], 'declined')
        self.assert_no_binding_or_original_changes()
        self.assert_correction_exhausted()

    def test_a_missing_second_native_refusal_blocks_before_any_provider_call(self):
        self.case.case.observe(self.case.output, 'only-one-completed-attempt')
        with patch('research_harness.review_transport.send_request') as send:
            with self.assertRaises(ResearchError) as caught:
                self.fixture.mutate(correction.invoke_correction,
                    {'id': 'ineligible-correction', 'assignment_id': self.case.case.assignment['id'],
                     'route_id': 'route'}, credential='test-only')
        self.assertEqual(caught.exception.code, 'review_correction_ineligible')
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
