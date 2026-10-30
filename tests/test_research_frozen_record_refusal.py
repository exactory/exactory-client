"""Observed native outputs retain omitted references or receive exact typed delivery refusals."""

import copy
import json
import unittest

from research_harness import problem_candidates, research_decisions, review_protocol, review_refusal
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_research_problem_reviews as reviewers


class NativeFrozenRecordTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = reviewers.ProblemReviewTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.record = {'id': 'value-1', 'motivation': [{'text': 'Exact delivered value.', 'evidence': []}],
                       'current_state': []}
        fixture.put('problem_value', self.record['id'], self.record)
        fixture.expected['value'] = dict(self.record, digest=digest(self.record))
        fixture.observe_bar_reviews()
        self.reference = {'kind': 'record', 'record_kind': 'problem_value', 'id': self.record['id']}

    def observe(self, reference):
        output = self.fixture.build_review_output('result')
        output['value']['evidence'] = [reference]
        return self.fixture.observe('result', 'first', output=output, record=False)

    def assert_typed_refusal(self, payload):
        fixture = self.fixture
        records = fixture.store.snapshot()['records']
        attempt = records['review_attempt']['attempt-result-first']
        original_output = fixture.artifacts.read(attempt['output'])
        revision = fixture.store.revision
        with self.assertRaises(ResearchError) as caught:
            fixture.mutate(research_decisions.record_value_review, payload)
        self.assertEqual(caught.exception.code, 'review_evidence_undelivered')
        reference = payload['value']['evidence'][0]
        # A reference without a digest is named with digest None, as 0.56.1 reports it.
        details = {'record_kind': reference['record_kind'], 'id': reference['id'], 'digest': reference.get('digest'),
                   'path': '/value/evidence'}
        self.assertEqual(caught.exception.details, details)
        records = fixture.store.snapshot()['records']
        refusal = records['review_output_refusal'][attempt['id']]
        self.assertEqual(fixture.store.revision, revision + 1)
        self.assertEqual((refusal['code'], refusal['check'], refusal['details']),
                         ('review_evidence_undelivered', 'review.evidence_delivery',
                          dict(details, message=caught.exception.message)))
        self.assertEqual(fixture.artifacts.read(attempt['output']), original_output)
        self.assertNotIn(payload['id'], records.get('value_review', {}))
        self.assertEqual(review_refusal.classify_observed_output(records, fixture.artifacts, attempt['id']),
                         {'code': refusal['code'], 'check': refusal['check'],
                          'details': dict(details, message=caught.exception.message)})

    def test_observed_omitted_digest_records_without_rewriting_the_output_or_payload(self):
        payload = self.observe(copy.deepcopy(self.reference))
        records = self.fixture.store.snapshot()['records']
        assignment = records['review_assignment']['result-first']
        # The frozen contract is unchanged by later current-record deletion or namespace additions.
        for future in ({}, {'value-1': {'id': 'value-1', 'future': True}}):
            changed = copy.deepcopy(records)
            changed['problem_value'] = future
            changed['problem-value'] = {'value-1': {'id': 'value-1', 'separate-kind': True}}
            review_protocol_output = research_decisions._response(payload)
            research_decisions.validate_review_contract(changed, self.fixture.artifacts, assignment, review_protocol_output)
        before = copy.deepcopy(payload)
        saved = self.fixture.mutate(research_decisions.record_value_review, payload)['result']
        self.assertEqual(saved['payload'], before)
        self.assertNotIn('digest', saved['payload']['value']['evidence'][0])
        self.assertEqual(research_decisions.find_undelivered_review_references(
            self.fixture.store.snapshot()['records'], self.fixture.artifacts, saved), [])

    def test_an_undelivered_reference_cannot_be_manufactured_by_a_later_current_record(self):
        reference = dict(self.reference, id='future-value')
        payload = self.observe(reference)
        self.fixture.put('problem_value', 'future-value', {'id': 'future-value', 'motivation': []})
        self.assert_typed_refusal(payload)

    def test_two_frozen_versions_require_an_explicit_digest_and_record_typed_refusal(self):
        self.fixture.expected['assessment_evidence'] = [{'kind': 'record', 'record_kind': 'problem_value',
            'id': 'value-1', 'digest': '1' * 64, 'record': {'id': 'value-1', 'earlier-version': True}}]
        self.assert_typed_refusal(self.observe(copy.deepcopy(self.reference)))

    def test_wrong_explicit_digest_records_a_typed_delivery_refusal(self):
        self.assert_typed_refusal(self.observe(dict(self.reference, digest='0' * 64)))


if __name__ == '__main__':
    unittest.main()
