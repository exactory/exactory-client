"""Result evidence follows its exact delivered contract and retains native refusal limits."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import research_decisions as decisions
from research_harness import review_protocol as protocol, review_refusal as refusal
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_research_problem_reviews as fixtures


class ResultReassessmentBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = fixtures.ProblemReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.value = {'id': 'value-1', 'motivation': [{'text': 'A bounded mathematical consequence.', 'evidence': []}],
                      'current_state': []}
        fixture.put('problem_value', 'value-1', self.value)
        fixture.expected['value'] = dict(self.value, digest=digest(self.value))
        self.reference = {'kind': 'record', 'record_kind': 'problem_value', 'id': 'value-1'}

    def prepare(self, *, ambiguous=False):
        fixture = self.fixture
        if ambiguous:
            fixture.expected['assessment_evidence'] = [dict(self.reference, digest='1' * 64,
                record={'id': 'value-1', 'earlier-version': True})]
        fixture.observe_bar_reviews()
        fixture.observe('result', 'first')
        fixture.mutate(protocol.record_context_event, {'id': 'exposure', 'assignment_id': 'result-first',
            'kind': 'contamination', 'source': 'author_history',
            'reason': 'This synthetic fixture records actual prior author-context exposure.',
            'evidence': [fixture.supporting]})

        def respond(route, request, credential):
            packet = json.loads(request['input'][0]['content'][len('The packet below is JSON.\n'):])
            return fixture.build_provider_response({'allowed_control': packet['allowed_control'],
                'evidence_control': packet['evidence_control'], 'excluded_controls': []})

        with patch('research_harness.review_transport.send_request', side_effect=respond):
            fixture.mutate(protocol.probe_route, {'id': 'repair-probe', 'route_id': 'route'}, credential='test-only')
        self.assignment = fixture.mutate(protocol.record_assignment, {'id': 'repair-result', 'route_id': 'route',
            'dossier_id': None, 'role': 'result', 'reviewer_id': 'first', 'author_id': 'author',
            'context': {'context_repair': {'assignment_id': 'result-first', 'event_ids': ['exposure'],
                'probe_id': 'repair-probe', 'reason': 'Reassess the retained exact result in a clean context.',
                'evidence': [fixture.supporting]}}})['result']
        self.output = fixture.build_review_output('result')
        self.output['reassessment'] = {'assignment_id': 'result-first', 'findings': [{
            'review_id': 'review-result-first', 'disposition': 'confirmed',
            'reason': 'The exact scientific assessment remains unchanged.', 'evidence': [copy.deepcopy(self.reference)]}]}

    def contract(self, output=None, assignment=None):
        return decisions.validate_review_contract(self.fixture.read_records(), self.fixture.artifacts,
            assignment or self.assignment, output or self.output)

    def observe(self, output, identifier='repair-attempt'):
        fixture = self.fixture
        with patch('research_harness.review_transport.send_request', return_value=fixture.build_provider_response(output)):
            fixture.mutate(protocol.invoke_assignment, {'id': identifier, 'assignment_id': self.assignment['id']},
                           credential='test-only')
        return fixture.read_records()['review_attempt'][identifier]

    def payload(self, output=None):
        return dict(output or self.output, id='repair-final', dossier_id=None, assignment_id=self.assignment['id'])

    def historical_grammar_refusal(self, attempt):
        # Model the real immutable certificate emitted before this contract-alignment fix.
        # Its continued effect is determined by native reclassification of the authenticated output.
        value = {'id': attempt['id'], 'assignment_id': self.assignment['id'], 'attempt_id': attempt['id'],
            'output_digest': digest(json.loads(self.fixture.artifacts.read(attempt['output']))),
            'request_digest': attempt['request_digest'], 'protocol': refusal.PROTOCOL,
            'code': 'invalid_strategy', 'check': 'evidence.shape', 'details': None,
            'recorded_revision': self.fixture.store.revision + 1}
        self.fixture.put('review_output_refusal', attempt['id'], value)
        return copy.deepcopy(value)

    def with_objection(self, reference=None):
        output = copy.deepcopy(self.output)
        output['reassessment']['findings'][0]['disposition'] = 'revised'
        output['objections'] = [{'id': 'new-material-objection', 'claim': 'The stated consequence needs a narrower scope.',
            'reason': 'The delivered value establishes only the bounded case.',
            'evidence': [copy.deepcopy(reference or self.reference)],
            'resolution_condition': 'State the exact bounded scope supported by the delivered record.'}]
        return output

    def test_exact_recordable_output_rebinds_after_historical_grammar_refusal_without_resampling(self):
        self.prepare()
        attempt = self.observe(self.output)
        original_bytes = self.fixture.artifacts.read(attempt['output'])
        certificate = self.historical_grammar_refusal(attempt)
        records = self.fixture.read_records()
        self.assertIsNone(refusal.classify_observed_output(records, self.fixture.artifacts, attempt['id']))
        state = protocol.assignment_state(records, self.fixture.artifacts, self.assignment['id'])
        self.assertEqual(state['attempt_id'], attempt['id'])
        self.assertEqual(state['refused_attempts'], [])
        saved = self.fixture.mutate(decisions.record_value_review, self.payload())['result']
        self.assertEqual(saved['payload'], self.payload())
        self.assertNotIn('digest', saved['payload']['reassessment']['findings'][0]['evidence'][0])
        self.assertEqual(decisions.find_undelivered_review_references(self.fixture.read_records(), self.fixture.artifacts, saved), [])
        with self.assertRaises(ResearchError) as caught:
            self.observe(self.output, 'forbidden-replacement')
        self.assertEqual(caught.exception.code, 'review_resampling_forbidden')
        self.assertEqual(self.fixture.artifacts.read(attempt['output']), original_bytes)
        self.assertEqual(self.fixture.read_records()['review_output_refusal'][attempt['id']], certificate)

    def test_new_result_objection_binds_the_exact_delivered_record_without_rewriting_it(self):
        self.prepare()
        output = self.with_objection()
        attempt = self.observe(output)
        original_bytes = self.fixture.artifacts.read(attempt['output'])
        saved = self.fixture.mutate(decisions.record_value_review, self.payload(output))['result']
        self.assertEqual(saved['payload'], self.payload(output))
        self.assertNotIn('digest', saved['payload']['objections'][0]['evidence'][0])
        self.assertEqual(self.fixture.artifacts.read(attempt['output']), original_bytes)

    def test_undelivered_and_wrong_explicit_record_versions_still_fail_both_evidence_arrays(self):
        self.prepare()
        self.fixture.put('problem_value', 'future-value', {'id': 'future-value', 'later': True})
        references = [dict(self.reference, id='absent-value'), dict(self.reference, id='future-value'),
                      dict(self.reference, digest='0' * 64)]
        for surface in ('reassessment', 'objection'):
            for reference in references:
                output = self.with_objection(reference) if surface == 'objection' else copy.deepcopy(self.output)
                if surface == 'reassessment':
                    output['reassessment']['findings'][0]['evidence'] = [reference]
                with self.subTest(surface=surface, reference=reference), self.assertRaises(ResearchError) as caught:
                    self.contract(output)
                self.assertEqual(caught.exception.code, 'review_evidence_undelivered')

    def test_ambiguous_delivered_versions_require_the_exact_explicit_digest_in_both_arrays(self):
        self.prepare(ambiguous=True)
        for surface in ('reassessment', 'objection'):
            output = self.with_objection() if surface == 'objection' else copy.deepcopy(self.output)
            with self.subTest(surface=surface), self.assertRaises(ResearchError) as caught:
                self.contract(output)
            self.assertEqual(caught.exception.code, 'review_evidence_undelivered')
            explicit = dict(self.reference, digest=digest(self.value))
            output['reassessment']['findings'][0]['evidence'] = [explicit]
            if surface == 'objection':
                output['objections'][0]['evidence'] = [explicit]
            self.contract(output)

    def test_exact_current_v2_v3_v4_prompts_bind_records_and_old_or_edited_prompts_remain_strict(self):
        self.prepare()
        for version in (2, 3, 4):
            prompt = protocol.canonical_prompt('result', version=version, repair=True, orchestration='problem_creation')
            self.assertIn('without a digest, which the harness binds to the version that the packet delivered', prompt['text'])
            with self.subTest(version=version):
                self.contract(assignment=dict(self.assignment, prompt=prompt))
        old = protocol.previous_problem_result_prompt(repair=True)
        self.assertNotIn('without a digest, which the harness binds', old['text'])
        edited = copy.deepcopy(self.assignment['prompt'])
        edited['text'] += ' '
        version_one = protocol.canonical_prompt('result', version=1, repair=True, orchestration='problem_creation')
        for label, prompt in [('old-0551', old), ('edited', edited), ('version-one', version_one)]:
            output = copy.deepcopy(self.output)
            if label == 'version-one':
                del output['value']['rubric']
                del output['value']['rubric_evidence']
            with self.subTest(prompt=label), self.assertRaises(ResearchError) as caught:
                self.contract(output, assignment=dict(self.assignment, prompt=prompt))
            self.assertEqual(caught.exception.code, 'invalid_strategy')

    def test_changed_confirmed_content_stays_refused_and_only_one_exact_request_retry_can_bind(self):
        self.prepare()
        output = copy.deepcopy(self.output)
        output['value']['reason'] = 'This changed scientific reasoning cannot be labeled confirmed.'
        attempt = self.observe(output)
        original_bytes = self.fixture.artifacts.read(attempt['output'])
        certificate = self.historical_grammar_refusal(attempt)
        records = self.fixture.read_records()
        failure = refusal.classify_observed_output(records, self.fixture.artifacts, attempt['id'])
        self.assertEqual((failure['code'], failure['check']), ('review_reassessment_mismatch', 'review.reassessment'))
        state = protocol.assignment_state(records, self.fixture.artifacts, self.assignment['id'])
        self.assertIsNone(state['attempt_id'])
        self.assertEqual(state['refused_attempts'], [{'attempt_id': attempt['id'], 'code': 'review_reassessment_mismatch'}])
        repaired = copy.deepcopy(output)
        repaired['reassessment']['findings'][0]['disposition'] = 'revised'
        repaired['reassessment']['findings'][0]['reason'] = 'The changed scientific reasoning is now explicitly reassessed.'
        second = self.observe(repaired, 'repair-attempt-two')
        self.assertEqual(second['request_digest'], attempt['request_digest'])
        self.fixture.mutate(decisions.record_value_review, self.payload(repaired))
        with self.assertRaises(ResearchError) as caught:
            self.observe(repaired, 'forbidden-third-attempt')
        self.assertEqual(caught.exception.code, 'review_resampling_forbidden')
        self.assertEqual(self.fixture.artifacts.read(attempt['output']), original_bytes)
        self.assertEqual(self.fixture.read_records()['review_output_refusal'][attempt['id']], certificate)


if __name__ == '__main__':
    unittest.main()
