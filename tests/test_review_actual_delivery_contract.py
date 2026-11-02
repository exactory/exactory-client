"""Ordinary evidence credit follows the authenticated request, including its omissions."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import research_decisions as decisions, review_protocol as protocol
from research_harness import review_refusal as refusal, review_supplied_evidence as supplied
from research_harness import review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_research_problem_reviews as fixtures
import test_research_problem_adjudication_contract as focused_fixtures


class ActualDeliveryContractTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ProblemReviewTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.observe_bar_reviews()

    def fit_local_requests(self):
        original = transport.request_body

        def build(route, prompt, packet, history=(), **kwargs):
            return original(dict(route, adapter='codex_cli_v1', endpoint='codex://local'),
                            prompt, packet, history, **kwargs)

        for patcher in (patch.object(transport, 'request_body', side_effect=build),
                        patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_request(self, assignment_id):
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-' + assignment_id]
        return protocol._read_json(self.case.artifacts, attempt['request'])

    def assert_refused_at_all_boundaries(self, payload, expected):
        records = self.case.read_records()
        attempt_id = 'attempt-' + payload['assignment_id']
        raw = self.case.artifacts.read(records['review_attempt'][attempt_id]['output'])
        finding = refusal.classify_observed_output(records, self.case.artifacts, attempt_id)
        self.assertIsNotNone(finding)
        self.assertEqual((finding['code'], finding['check']),
                         ('review_evidence_undelivered', 'review.evidence_delivery'))
        for key, value in expected.items():
            self.assertEqual(finding['details'][key], value)
        historical = dict(id=payload['id'], assignment_id=payload['assignment_id'],
                          subject=records['review_assignment'][payload['assignment_id']]['subject'], payload=payload)
        missing = decisions.find_undelivered_review_references(records, self.case.artifacts, historical)
        self.assertIn(expected, missing)
        with self.assertRaises(ResearchError) as caught:
            self.case.mutate(decisions.record_value_review, payload)
        self.assertEqual(caught.exception.code, 'review_evidence_undelivered')
        records = self.case.read_records()
        certificate = records['review_output_refusal'][attempt_id]
        self.assertEqual(certificate['details'], finding['details'])
        self.assertEqual(self.case.artifacts.read(records['review_attempt'][attempt_id]['output']), raw)

    def test_ordinary_review_cannot_cite_a_route_elided_body(self):
        body = self.case.artifacts.put(b'Exact assessment body withheld by the fitted request.\n' * 200, 'text/plain')
        self.case.expected['assessment_evidence'] = [body]
        output = self.case.build_review_output('result')
        output['value']['evidence'] = [body]
        self.fit_local_requests()
        payload = self.case.observe('result', 'first', output, record=False)
        packet = protocol._read_json(self.case.artifacts,
            self.case.read_records()['review_assignment']['result-first']['packet'])
        self.assertTrue(next(entry['content'] for entry in packet['evidence'] if entry['artifact'] == body))
        sent = json.loads(self.read_request('result-first')['input'][-1]['content'][len(transport.PACKET_LABEL):])
        self.assertEqual(next(entry['delivery'] for entry in sent['evidence'] if entry['artifact'] == body),
                         'body-elided-route-limit')
        references, _ = supplied.supplied_context(self.case.read_records(), self.case.artifacts, ['result-first'])
        self.assertNotIn(body['sha256'], references)
        self.assert_refused_at_all_boundaries(payload, {'sha256': body['sha256']})

    def test_ordinary_review_cannot_cite_a_record_only_in_elided_history(self):
        reference = dict(kind='record', record_kind='problem_value', id='value-1')
        value = dict(self.case.expected['value'])
        self.case.put('problem_value', 'value-1', value)
        self.fit_local_requests()
        first = self.case.assign('result', 'first', identifier='history')
        request_output = self.case.build_review_output('result')
        request_output['value']['status'] = 'unresolved'
        request_output['source_requests'] = [dict(id='inventory', kind='inventory_page', page=0)]
        with patch.object(transport, 'send_request', return_value=self.case.build_provider_response(request_output)):
            self.case.mutate(protocol.invoke_assignment, dict(id='attempt-history', assignment_id=first['id']),
                             credential='test-only')
        assignment = self.case.assign('result', 'first', identifier='continued', prior_assignment_id=first['id'])
        output = self.case.build_review_output('result')
        output['value']['evidence'] = [reference]
        with patch.object(transport, 'send_request', return_value=self.case.build_provider_response(output)):
            self.case.mutate(protocol.invoke_assignment, dict(id='attempt-continued', assignment_id=assignment['id']),
                             credential='test-only')
        self.assertTrue(any('packet-elided-route-limit' in message.get('content', '')
                            for message in self.read_request('continued')['input']))
        payload = dict(output, id='review-continued', dossier_id=None, assignment_id=assignment['id'])
        self.assert_refused_at_all_boundaries(payload, dict(record_kind='problem_value', id='value-1', digest=None))

    def test_fitting_ordinary_review_keeps_credit_at_every_boundary(self):
        payload = self.case.observe('result', 'first', record=False)
        records = self.case.read_records()
        self.assertIsNone(refusal.classify_observed_output(records, self.case.artifacts, 'attempt-result-first'))
        saved = self.case.mutate(decisions.record_value_review, payload)['result']
        self.assertEqual(decisions.find_undelivered_review_references(
            self.case.read_records(), self.case.artifacts, saved), [])
        references, _ = supplied.supplied_context(self.case.read_records(), self.case.artifacts, ['result-first'])
        self.assertIn(self.case.supporting, references.values())
        self.case.observe('result', 'second')
        gate = decisions.assess_problem_reviews(self.case.read_records(), self.case.artifacts)
        self.assertTrue(gate['ready'], gate['obligations'])

    def test_classification_validates_the_named_attempt_without_selecting_another(self):
        self.case.observe('result', 'first', record=False)
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-result-first']
        output = protocol._read_json(self.case.artifacts, attempt['output'])
        withheld = self.case.artifacts.put(b'Local bytes never supplied to either attempt.', 'text/plain')
        output['value']['evidence'] = [withheld]
        second = dict(attempt, id='second-attempt', output=protocol._put_json(self.case.artifacts, output),
                      response=protocol._put_json(self.case.artifacts, self.case.build_provider_response(output)),
                      recorded_revision=attempt['recorded_revision'] + 1)
        records['review_attempt'][second['id']] = second
        with patch.object(protocol, 'assignment_state', side_effect=AssertionError('Recursive attempt selection')):
            self.assertIsNone(refusal.classify_observed_output(records, self.case.artifacts, attempt['id']))
            finding = refusal.classify_observed_output(records, self.case.artifacts, second['id'])
        self.assertEqual(finding['code'], 'review_evidence_undelivered')
        self.assertEqual(finding['details']['sha256'], withheld['sha256'])


class FocusedActualDeliveryTests(unittest.TestCase):
    def test_focused_ordinary_adjudicator_cannot_cite_a_withheld_body(self):
        case = focused_fixtures.ProblemAdjudicationContractTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare_reviews(result_reviewer='first')
        capture = case.capture
        with patch.object(case, 'capture', side_effect=lambda *args, **kwargs:
                capture(*args, **dict(kwargs, body=kwargs['body'] * 200))):
            body = case.add_new_source()
        assignment = case.assign_focused_review()
        output = dict(objection_id='unsupported-implication', disposition='not_upheld',
                      reason='The withheld comparator is claimed as evidence.', evidence=[body], correction_admissible=None)
        original = transport.request_body

        def build(route, prompt, packet, history=(), **kwargs):
            return original(dict(route, adapter='codex_cli_v1', endpoint='codex://local'),
                            prompt, packet, history, **kwargs)

        with patch.object(transport, 'request_body', side_effect=build), \
                patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 1):
            with patch.object(transport, 'send_request', return_value=case.build_provider_response(output)):
                case.mutate(protocol.invoke_assignment, dict(id='attempt-focused', assignment_id=assignment['id']),
                            credential='test-only')
            records = case.read_records()
            finding = refusal.classify_observed_output(records, case.artifacts, 'attempt-focused')
            self.assertIsNotNone(finding)
            self.assertEqual(finding['code'], 'review_evidence_undelivered')
            self.assertEqual(finding['details']['sha256'], body['sha256'])
            packet = protocol._read_json(case.artifacts, assignment['packet'])
            records.setdefault('review_adjudication', {})['historical-focused'] = dict(output,
                id='historical-focused', assignment_id=assignment['id'], dossier_id=None,
                recorded_revision=case.store.revision, subject=assignment['subject'],
                problem_evidence_digest=packet['problem_evidence_digest'])
            audited = protocol.adjudication_state(records, case.artifacts, 'historical-focused')
            self.assertIn('review_adjudication_evidence_undelivered', [row['code'] for row in audited['obligations']])
            with self.assertRaises(ResearchError) as caught:
                case.mutate(protocol.record_adjudication, dict(output, id='focused-finding', assignment_id='focused'))
            self.assertEqual(caught.exception.code, 'review_evidence_undelivered')


if __name__ == '__main__':
    unittest.main()
