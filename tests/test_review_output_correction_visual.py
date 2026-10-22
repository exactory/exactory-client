"""Observed text deltas retain visual evidence and cannot improve negative grades."""

import copy
import json
import unittest
from unittest.mock import patch

from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import review_output_correction as correction, research_decisions as decisions
from research_harness import review_codex_transport as codex
from research_harness.direction_reviews import collect_actual_input_packets
from research_harness.errors import ResearchError
from research_harness.evidence import digest
import test_visual_context_repair as fixtures


class VisualCorrectionTests(unittest.TestCase):
    def test_original_visual_delivery_and_negative_science_survive_observed_text_delta(self):
        fixture = fixtures.VisualContextRepairTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case, artifacts = fixture.case, fixture.artifacts
        assignment = fixture.assign()
        packet = json.loads(artifacts.read(assignment['packet']))
        original = copy.deepcopy(fixture.output)
        original['value']['reason'] = 'The retained scientific assessment is now explicitly negative.'
        original['value']['assurances'][0]['status'] = 'failed'
        original['value']['rubric']['calibration'] = 'fail'
        original['reassessment'] = {'assignment_id': fixture.prior['id'], 'findings': [
            {'review_id': row['id'], 'disposition': 'confirmed', 'reason': 'This metadata requires exact comparison.',
             'evidence': [{'kind': 'source', 'link': fixture.fixture.pdf_link}]}
            for row in packet['historical_reviews']]}
        for identifier in ('negative-visual-one', 'negative-visual-two'):
            with patch.object(transport, 'send_request', side_effect=lambda route, request, credential:
                              fixture.fixture.response(route, request, original)):
                case.mutate(protocol.invoke_assignment, {'id': identifier, 'assignment_id': assignment['id']},
                            credential='test-only')
            with self.assertRaises(ResearchError) as caught:
                case.mutate(decisions.record_value_review,
                    dict(original, id='negative-final', dossier_id=None, assignment_id=assignment['id']))
            self.assertEqual(caught.exception.code, 'review_reassessment_mismatch')
        delta = {'source_output_digest': digest(original), 'findings': [
            {'review_id': row['review_id'], 'disposition': 'revised',
             'reason': 'The exact scientific content differs; the negative findings remain unchanged.'}
            for row in original['reassessment']['findings']]}
        events = []

        def text_response(route, request, credential):
            response = case.build_provider_response(delta)
            events.extend([
                {'type': 'session_meta', 'payload': {'id': response['id'], 'source': 'exec',
                    'base_instructions': {'text': request['instructions']}}},
                {'type': 'turn_context', 'payload': {'model': route['model'], 'effort': 'high'}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                    'content': [{'type': 'input_text', 'text': codex.packet_text(request)}]}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': json.dumps(delta)}]}}])
            evidence = codex.inspect_rollout(events, request, response['id'], '/fixture', [])
            self.assertTrue(evidence['input_verified'], evidence['issues'])
            evidence.update(inspected=True, route_digest=digest(route), runtime=codex.runtime(), event_digest=digest(events))
            response['context_evidence'] = evidence
            return response

        with patch.object(transport, 'send_request', side_effect=text_response), patch.object(codex, '_rollout', return_value=events):
            observed = case.mutate(correction.invoke_correction,
                {'id': 'text-delta', 'assignment_id': assignment['id'], 'route_id': 'route'}, credential='test-only')['result']
        self.assertEqual(observed['correction_status'], 'accepted', observed['correction_error'])
        records = case.read_records()
        state = protocol.assignment_state(records, artifacts, assignment['id'])
        self.assertTrue(state['ready'], state['obligations'])
        self.assertEqual(state['attempt_id'], 'negative-visual-two')
        self.assertEqual(state['output']['value'], original['value'])
        self.assertEqual(json.loads(artifacts.read(observed['output'])), delta)
        supplied = collect_actual_input_packets(records, artifacts, assignment['id'])
        self.assertTrue(supplied)
        self.assertNotIn(correction.PROTOCOL, {row.get('protocol') for row in supplied})
        review = case.mutate(decisions.record_value_review,
            dict(state['output'], id='negative-final', dossier_id=None, assignment_id=assignment['id']))['result']
        records = case.read_records()
        self.assertEqual(decisions._assess_problem_coverage(records, artifacts, [review], case.expected, digest(case.expected)), [])
        second = case.assign('result', 'second')
        second_output = case.build_review_output('result')
        fixture.fixture.native.invoke(second['id'], second_output)
        case.mutate(decisions.record_value_review,
            dict(second_output, id='second-final', dossier_id=None, assignment_id=second['id']))
        records = case.read_records()
        gate = decisions.assess_problem_reviews(records, artifacts)
        codes = {row['code'] for row in gate['obligations']}
        self.assertFalse(gate['ready'])
        self.assertIn('problem_assurance_pending', codes)
        self.assertIn('problem_rubric_below_threshold', codes)
        self.assertIn('negative-final', gate['review_ids'])
        self.assertEqual(set(gate['review_ids']), {'negative-final', 'second-final'})
        for key, wrong in [('model', 'different-model'), ('effort', 'low')]:
            changed = copy.deepcopy(records)
            native = copy.deepcopy(events)
            native[1]['payload'][key] = wrong
            bad = changed['review_output_correction']['text-delta']
            bad['native_rollout'] = artifacts.put(json.dumps(native).encode(), 'application/json')
            response = json.loads(artifacts.read(bad['response']))
            response['context_evidence']['rollout_digest'] = digest(native)
            bad['response'] = artifacts.put(json.dumps(response).encode(), 'application/json')
            with self.subTest(actual_context=key):
                self.assertIsNone(correction.selected_correction(changed, artifacts, assignment['id']))


if __name__ == '__main__':
    unittest.main()
