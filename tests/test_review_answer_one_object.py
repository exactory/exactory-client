"""Readers accept complete retractions and refuse text after an object."""

import json
import unittest
from unittest.mock import patch

from research_harness import review_claude_transport as claude
from research_harness import review_transport as transport


def build_object():
    return {'stage': 'bar', 'support': None, 'value': {
        'status': 'unresolved',
        'consequence': 'A bounded extremal consequence.',
        'bar_ids': [], 'objective_adequacy': 'not_assessed',
        'method_adequacy': 'not_assessed',
        'reason': 'A decisive source is missing.',
        'evidence': [], 'work_items': [], 'assurances': [],
        'objection_findings': []},
        'objections': [], 'limitations': ['Limited to the supplied evidence.']}


def build_response(answer):
    return {'id': 'answer-fixture', 'model': 'fixture-model',
            'status': 'completed',
            'output': [{'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': answer}]}],
            'usage': {'input_tokens': 37, 'output_tokens': 12}}


def build_malformed_answers():
    original = json.dumps(build_object())
    corrected = json.dumps(dict(build_object(), excerpts=[]))
    suffixes = (
        ('excerpts', ',"excerpts":[]}'),
        ('source_requests', ',"source_requests":[],"excerpts":[]}'),
        ('unused_note', ',"objections_note_unused":null,"excerpts":[]}'),
        ('reassessment', ',"reassessment":{"findings":[]}}'),
        ('objections', ',"objections":[],"limitations":'
                       '["A condition remains."]}'),
        ('extra_brace', '}'))
    for name, suffix in suffixes:
        answer = original + suffix
        yield name, answer
        yield name + '_then_retracted', answer + '\nRETRACTED\n' + corrected


class OneObjectAnswerTests(unittest.TestCase):
    def test_a_complete_retraction_returns_the_corrected_object(self):
        corrected = dict(build_object(), excerpts=[])
        corrected_text = json.dumps(corrected)
        answer = json.dumps(build_object()) + '\nRETRACTED\n' + corrected_text
        parsed, fenced, evidence = claude.parse_result_text(answer)
        self.assertEqual(parsed, corrected_text)
        self.assertFalse(fenced)
        self.assertTrue(evidence['self_corrected'])
        self.assertEqual(evidence['retracted_object_count'], 1)
        self.assertEqual(evidence['result_text'], answer)
        self.assertEqual(transport.response_output(build_response(parsed)),
                         corrected)

    def test_malformed_suffixes_preserve_answers_before_a_retraction(self):
        for name, answer in build_malformed_answers():
            with self.subTest(pattern=name):
                parsed, fenced, evidence = claude.parse_result_text(answer)
                self.assertEqual(parsed, answer)
                self.assertFalse(fenced)
                self.assertEqual(evidence, {})

    def test_text_after_an_object_is_extra_data_on_both_response_paths(self):
        for name, answer in build_malformed_answers():
            for path in ('claude', 'codex'):
                with self.subTest(pattern=name, path=path):
                    parsed = (claude.parse_result_text(answer)[0]
                              if path == 'claude' else answer)
                    with self.assertRaisesRegex(json.JSONDecodeError,
                                                'Extra data'):
                        transport.response_output(build_response(parsed))

    def test_observing_malformed_answers_records_a_transport_failure(self):
        route = {'id': 'route', 'adapter': 'openai_responses_v1',
                 'model': 'fixture-model',
                 'endpoint': 'https://api.openai.com/v1/responses',
                 'configuration': {'max_output_tokens': 1000,
                                   'timeout_seconds': 5}}
        request = transport.request_body(
            route, 'Assess the supplied evidence.', {})
        for name, answer in build_malformed_answers():
            for path in ('claude', 'codex'):
                with self.subTest(pattern=name, path=path):
                    parsed = (claude.parse_result_text(answer)[0]
                              if path == 'claude' else answer)
                    response = build_response(parsed)
                    with patch.object(transport, 'send_request',
                                      return_value=response):
                        observed = transport.observe(
                            route, request, 'test-only')
                    self.assertEqual(observed['status'], 'failed')
                    self.assertEqual(observed['error'], {
                        'kind': 'JSONDecodeError',
                        'code': 'review_transport_failure'})
                    self.assertIsNone(observed['output'])
                    self.assertEqual(observed['response'], response)


if __name__ == '__main__':
    unittest.main()
