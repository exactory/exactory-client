"""Claude self-corrections retain the full answer and still pass the ordinary output contracts."""

import copy
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from research_harness import native_review, review_claude_transport as claude
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from research_harness.operations import immutable_record, prepared_mutation
from research_harness.review_refusal import classify_observed_output
from research_harness.storage import Store
from test_research_claude_transport import ROUTE, clean_stream, completed, pinned_route


class ClaudeResultTextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.route = pinned_route()
        self.root = Path(temporary.name) / 'review'
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.native = {'kind': 'result', 'subject_digest': 'subject', 'claim_digest': 'claim',
                       'reviewer': {'source': 'host', 'actor_id': 'reviewer', 'attestation_id': 'assignment'}}
        packet = {'protocol': protocol.PROTOCOL, 'role': 'native_math', 'native': self.native}
        packet['digest'] = digest(packet)
        prompt = protocol.canonical_prompt('native_math')
        self.assignment = {'id': 'assignment', 'role': 'native_math', 'route_id': self.route['id'],
                           'route_digest': digest(self.route), 'packet': protocol._put_json(self.artifacts, packet),
                           'packet_digest': packet['digest'], 'prompt': prompt,
                           'prompt_hash': prompt['rendered_prompt_hash']}
        self.request = transport.request_body(self.route, prompt['text'], packet)

    def build_output(self):
        return {'subject_digest': self.native['subject_digest'], 'claim_digest': self.native['claim_digest'],
                'reviewer': self.native['reviewer'], 'decision': 'approve',
                'findings': {key: 'Checked the supplied evidence.' for key in native_review.RESULT_FINDINGS}}

    def read_json(self, reference):
        return json.loads(self.artifacts.read(reference))

    def observe_text(self, text):
        events = clean_stream(self.request, output=text)
        with patch.object(claude, '_cli', return_value=('/usr/local/bin/claude', 'fixture-version')), \
                patch.object(claude, '_run_owned', return_value=completed(events)):
            observation = protocol._observed(self.artifacts, self.route, self.request, None)
        return dict(observation, id='attempt', assignment_id=self.assignment['id'], origin='instrumented',
                    packet_digest=self.assignment['packet_digest'], prompt_hash=self.assignment['prompt_hash'])

    def classify_attempt(self, attempt):
        records = {'review_route': {self.route['id']: self.route},
                   'review_assignment': {self.assignment['id']: self.assignment},
                   'review_attempt': {attempt['id']: attempt}}
        return classify_observed_output(records, self.artifacts, attempt['id'])

    def assert_correction(self, text, expected, retracted_object_count=1):
        attempt = self.observe_text(text)
        self.assertEqual(attempt['status'], 'completed', attempt['error'])
        self.assertEqual(self.read_json(attempt['output']), expected)
        response = self.read_json(attempt['response'])
        self.assertIs(response['execution']['self_corrected'], True)
        self.assertEqual(response['execution'].get('retracted_object_count'), retracted_object_count)
        self.assertEqual(response['execution']['result_text'], text)
        self.assertEqual(response['execution']['result_text_digest'], digest(text))
        self.assertTrue(transport.context_verified(self.route, self.request, response))
        self.assertEqual(transport.response_output(response), expected)
        return attempt

    def assert_refusal(self, text):
        attempt = self.observe_text(text)
        self.assertEqual(attempt['status'], 'failed')
        self.assertIsNone(attempt['output'])
        self.assertEqual(attempt['error'], {'code': 'review_transport_failure', 'kind': 'JSONDecodeError'})
        response = self.read_json(attempt['response'])
        self.assertEqual(response['output'][0]['content'][0]['text'], claude.strip_fence(text)[0])
        self.assertNotIn('self_corrected', response['execution'])
        self.assertNotIn('retracted_object_count', response['execution'])

    def test_retracted_examples_and_container_openers_refuse_recovery(self):
        texts = (
            '{"decision":"reject","reason":"Earlier assessment."}\nRETRACTED\n'
            'I withdraw that assessment and have no replacement yet.\n'
            'The following is only an example from the packet:\n```json\n'
            '{"decision":"revise","reason":"Illustrative only."}\n```',
            '{}\nRETRACTED\n```python\n{"decision":"revise","reason":"Example only."}',
            '{}\nRETRACTED\n[\n{"decision":"revise","reason":"Example only."}',
        )
        for text in texts:
            with self.subTest(text=text):
                self.assert_refusal(text)

    def test_only_blank_lines_may_separate_retractions_and_blocks(self):
        output = {'decision': 'reject', 'reason': 'Gap.'}
        last = '{"decision":"reject","reason":"Gap."}'
        for text in ('\n{}\nRETRACTED\n' + last + '\n \t\n',
                     '\n{}\n \t\nRETRACTED\n\t\n' + last + '\n\n',
                     '```JSON\n\n{}\n\t\n```\n\nRETRACTED\n \t\n```json\n\n' + last + '\n\n```\n\t'):
            with self.subTest(text=text):
                self.assert_correction(text, output)
        for separator in ('\nCorrection follows.\nRETRACTED\n', '\nRETRACTED\nCorrection follows.\n'):
            with self.subTest(separator=separator):
                self.assert_refusal('{}' + separator + last)

    def test_retractions_inside_answer_fences_do_not_withdraw_objects(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        last = '{"decision":"revise","reason":"Illustrative only."}'
        for tag in ('json', ''):
            for prose in ('Quoted protocol example:\n', ''):
                text = first + '\n' + prose + '```' + tag + '\nRETRACTED\n' + last + '\n```'
                with self.subTest(tag=tag, prose=prose):
                    self.assert_refusal(text)

    def test_indented_protocol_examples_keep_the_original_response(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        last = '{"decision":"revise","reason":"Illustrative only."}'
        for indentation in ('    ', '\t'):
            text = first + '\nQuoted protocol example:\n\n' + indentation + 'RETRACTED\n' + indentation + last
            with self.subTest(indentation=indentation):
                self.assert_refusal(text)

    def test_html_protocol_examples_keep_the_original_response(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        last = '{"decision":"revise","reason":"Illustrative only."}'
        for prefix in ('', 'Quoted protocol example:\n'):
            for body in ('<pre>\nRETRACTED\n</pre>\n' + last, '<pre>\nRETRACTED\n' + last + '\n</pre>'):
                with self.subTest(prefix=prefix, body=body):
                    self.assert_refusal(first + '\n' + prefix + body)

    def test_prose_before_a_retraction_refuses_recovery(self):
        last = json.dumps(self.build_output())
        for prose in ('Correction follows.', 'The first object is withdrawn.', '"A quoted explanation.',
                      '[{"example": {}}]'):
            with self.subTest(prose=prose):
                self.assert_refusal('{}\n' + prose + '\nRETRACTED\n' + last)
        text = '{}\nRETRACTED\nThe complete correction follows.\n\n```json\n' + last + '\n```'
        self.assert_refusal(text)
        self.assert_correction('{}\n\nRETRACTED\n\n```json\n' + last + '\n```', self.build_output())

    def test_retraction_lines_must_start_at_column_zero(self):
        last = json.dumps(self.build_output())
        for marker in (' RETRACTED', '\tRETRACTED', '    RETRACTED', 'RETRACTED ', 'RETRACTED\t'):
            with self.subTest(marker=marker):
                self.assert_refusal('{}\n' + marker + '\n' + last)

    def test_objects_must_start_at_column_zero(self):
        last = json.dumps(self.build_output())
        for indentation in (' ', '    ', '\t'):
            for text in (indentation + '{}\nRETRACTED\n' + last,
                         '{}\nRETRACTED\n' + indentation + last,
                         '{}\nRETRACTED\n```json\n' + indentation + last + '\n```'):
                with self.subTest(indentation=indentation, text=text):
                    self.assert_refusal(text)
        text = '{}\t \r\nRETRACTED\r\n' + json.dumps(self.build_output(), indent=2) + ' \t\r\n'
        self.assert_correction(text, self.build_output())

    def test_fenced_objects_require_exact_fence_lines(self):
        last = json.dumps(self.build_output())
        for opening, closing in ((' ```json', '```'), ('```json ', '```'), ('```json\t', '```'),
                                 ('```json', '``` '), ('```json', '```\t'), ('````json', '````'),
                                 ('~~~json', '~~~'), ('``` JSON', '```')):
            with self.subTest(opening=opening, closing=closing):
                self.assert_refusal('{}\nRETRACTED\n' + opening + '\n' + last + '\n' + closing)
        for tag in ('', 'json', 'JSON', 'jSoN'):
            text = ' \t\n```' + tag + '\n\t\n{}\n \n```\n\nRETRACTED\n\n'
            text += '```' + tag + '\n\n' + last + '\n\t\n```\n \t'
            with self.subTest(tag=tag):
                self.assert_correction(text, self.build_output())

    def test_text_before_the_first_block_refuses_recovery(self):
        last = json.dumps(self.build_output())
        for prefix in ('Introduction:\n', 'The example is "{}".\n', '<pre>\n', '    Quoted example:\n'):
            with self.subTest(prefix=prefix):
                self.assert_refusal(prefix + '{}\nRETRACTED\n' + last)

    def test_retraction_evidence_counts_withdrawn_blocks(self):
        last = json.dumps(self.build_output())
        for text in ('{}\nRETRACTED\nRETRACTED\n' + last,
                     '{}\nRETRACTED\nAn explanation.\nRETRACTED\n' + last):
            with self.subTest(text=text):
                self.assert_refusal(text)
        text = '{}\nRETRACTED\n{"decision":"approve"}\nRETRACTED\n' + last
        self.assert_correction(text, self.build_output(), retracted_object_count=2)

    def test_unmarked_answer_examples_keep_the_original_response(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        prose = 'My answer above is final. The following is a quoted example, not my assessment:'
        for tag in ('json', ''):
            for example in ('{"decision":"revise","reason":"Illustrative only."}', json.dumps(self.build_output())):
                with self.subTest(tag=tag, example=example):
                    self.assert_refusal(first + '\n' + prose + '\n```' + tag + '\n' + example + '\n```')

    def test_marked_quoted_examples_refuse_recovery(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        prose = 'My answer above is final. The following is a quoted example, not my assessment:'
        output = {'decision': 'revise', 'reason': 'Illustrative only.'}
        for tag in ('json', ''):
            text = first + '\n' + prose + '\nRETRACTED\n```' + tag + '\n' + json.dumps(output) + '\n```'
            with self.subTest(tag=tag):
                self.assert_refusal(text)
                text = first + '\nRETRACTED\n' + prose + '\n```' + tag + '\n' + json.dumps(output) + '\n```'
                self.assert_refusal(text)
                text = first + '\nRETRACTED\n```' + tag + '\n' + json.dumps(output) + '\n```'
                attempt = self.assert_correction(text, output)
                self.assertIsNone(self.classify_attempt(attempt))
        self.assertEqual(getattr(claude, 'RESULT_RETRACTION_LINE', None), 'RETRACTED')

    def test_each_earlier_candidate_must_be_retracted(self):
        first, middle, last = '{}', '{"decision":"approve"}', json.dumps(self.build_output())
        for marker in ('\nRETRACTED\n', '\rRETRACTED\r'):
            with self.subTest(marker=marker):
                self.assert_correction(first + marker + middle + marker + last,
                                       self.build_output(), retracted_object_count=2)
        for text in (first + '\nRETRACTED\nRETRACTED\n' + middle + '\nRETRACTED\nRETRACTED\n' + last,
                     first + '\n \tRETRACTED\t \r\n' + middle + '\n \tRETRACTED\t \r\n' + last,
                     first + '\n' + middle + '\nRETRACTED\n' + last,
                     first + '\nRETRACTED\n' + middle + '\n' + last,
                     'RETRACTED\n' + first + '\n' + last,
                     first + '\nRETRACTED\n' + last + '\nRETRACTED',
                     first + '\nRETRACTED\n```json\n' + last + '\nRETRACTED\n```'):
            with self.subTest(text=text):
                self.assert_refusal(text)

    def test_only_exact_marker_lines_outside_code_retract_candidates(self):
        last = json.dumps(self.build_output())
        for marker in ('retracted', 'Retracted', 'RETRACTED.', 'Correction: RETRACTED',
                       'RETRACTED followed by commentary', '\u00a0RETRACTED\u00a0',
                       '```python\nRETRACTED\n```', '```javascript\nRETRACTED\n```',
                       '~~~text\nRETRACTED\n~~~', '["RETRACTED"]'):
            with self.subTest(marker=marker):
                self.assert_refusal('{}\n' + marker + '\n' + last)

    def test_unmarked_incident_correction_is_refused(self):
        output = self.build_output()
        malformed = copy.deepcopy(output)
        malformed['findings'].update(dependencies=[], policy='', scope='')
        prose = ('Correction: the object above is malformed (I garbled the dependencies field and left policy, '
                 'coverage and reason empty). It must not be used. The complete, corrected assessment follows.')
        text = json.dumps(malformed) + '\n' + prose + '\n```json\n' + json.dumps(output) + '\n```'
        self.assert_refusal(text)

    def test_non_grammar_fence_lines_refuse_recovery(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        last = '{"decision":"revise","reason":"Quoted example only."}'
        for marker in ('\n', '\nRETRACTED\n'):
            for opening, closing in (('````text', '```'), ('~~~~text', '~~~'), ('````text', '~~~~'),
                                     ('~~~~text', '````'), ('````json', '````json')):
                with self.subTest(marker=marker, opening=opening, closing=closing):
                    text = first + marker + opening + '\n' + closing + '\n' + last
                    self.assert_refusal(text)

    def test_tilde_fence_lines_refuse_recovery(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        last = '{"decision":"revise","reason":"Quoted example only."}'
        for marker in ('\n', '\nRETRACTED\n'):
            with self.subTest(marker=marker):
                text = first + marker + '~~~text\n' + last
                self.assert_refusal(text)

    def test_only_grammar_fences_allow_marked_corrections(self):
        output = self.build_output()
        for opening, closing in (('````json', '````'), ('````json', '`````'), ('~~~~JSON', '~~~~'),
                                 ('~~~', '~~~~'), ('\t````json', '\t```` \t')):
            text = '{}\nRETRACTED\n' + opening + '\n' + json.dumps(output) + '\n' + closing
            with self.subTest(opening=opening, closing=closing):
                self.assert_refusal(text)
        text = '````json\n{}\nRETRACTED\n' + json.dumps(output) + '\n````'
        self.assert_refusal(text)
        self.assert_correction('{}\nRETRACTED\n```json\n' + json.dumps(output) + '\n```', output)

    def test_backticks_in_fence_tags_refuse_recovery(self):
        output = self.build_output()
        for tag in ('json`example', ' `json', 'json ``quoted``'):
            text = '{}\nRETRACTED\n```' + tag + '\n' + json.dumps(output)
            with self.subTest(tag=tag):
                self.assert_refusal(text)

    def test_a_contract_malformed_first_object_is_retained_with_the_valid_correction(self):
        output = self.build_output()
        malformed = copy.deepcopy(output)
        malformed['findings'].update(dependencies=[], policy='')
        with self.assertRaises(ResearchError):
            native_review.validate_output(self.native, malformed)
        text = json.dumps(malformed) + '\nCorrection: the complete assessment follows.\nRETRACTED\n'
        text += json.dumps(output)
        self.assert_refusal(text)
        text = json.dumps(malformed) + '\nRETRACTED\nCorrection: the complete assessment follows.\n'
        text += json.dumps(output)
        self.assert_refusal(text)
        text = json.dumps(malformed) + '\nRETRACTED\n' + json.dumps(output)
        attempt = self.assert_correction(text, output)
        self.assertIsNone(self.classify_attempt(attempt))
        native_review.validate_output(self.native, self.read_json(attempt['output']))

    def test_separate_fences_and_a_shared_fence_keep_the_exact_whole_text(self):
        output = self.build_output()
        first, last = json.dumps({'decision': 'approve'}), json.dumps(output)
        for text in (first + '\nRETRACTED\n' + last,
                     '```json\n' + first + '\n```\nCorrected assessment:\nRETRACTED\n```json\n' + last + '\n```',
                     ' \n```json\n' + first + '\nCorrection follows.\nRETRACTED\n' + last + '\n```\n ',
                     '```\n' + first + '\n```\nCorrection:\nRETRACTED\n' + last + '\n',
                     first + '\nCorrection:\nRETRACTED\n```JSON\n' + last + '\n```',
                     'Initial assessment:\n' + first + '\nCorrection:\nRETRACTED\n' + last,
                     'The example is "{}".\n' + first + '\nThe "corrected" assessment follows.\nRETRACTED\n' + last):
            with self.subTest(text=text):
                if text == first + '\nRETRACTED\n' + last:
                    self.assert_correction(text, output)
                else:
                    self.assert_refusal(text)
        for text in ('```json\n' + first + '\n```\nRETRACTED\nCorrected assessment:\n```json\n' + last + '\n```',
                     ' \n```json\n' + first + '\n```\nRETRACTED\nCorrection follows.\n' + last + '\n ',
                     '```\n' + first + '\n```\nRETRACTED\nCorrection:\n' + last + '\n',
                     first + '\nRETRACTED\nCorrection:\n```JSON\n' + last + '\n```'):
            with self.subTest(text=text):
                self.assert_refusal(text)
        for text in ('```json\n' + first + '\n```\nRETRACTED\n```json\n' + last + '\n```',
                     ' \n```json\n' + first + '\n```\nRETRACTED\n' + last + '\n ',
                     '```\n' + first + '\n```\nRETRACTED\n' + last + '\n',
                     first + '\nRETRACTED\n```JSON\n' + last + '\n```'):
            with self.subTest(text=text):
                self.assert_correction(text, output)

    def test_an_invalid_last_object_is_refused_by_the_native_contract_without_falling_back(self):
        output = self.build_output()
        invalid = copy.deepcopy(output)
        invalid['findings']['policy'] = ''
        text = json.dumps(output) + '\nRETRACTED\nCorrection:\n' + json.dumps(invalid)
        self.assert_refusal(text)
        text = json.dumps(output) + '\nRETRACTED\n' + json.dumps(invalid)
        attempt = self.assert_correction(text, invalid)
        self.assertEqual(self.classify_attempt(attempt),
                         {'code': 'invalid_native_review', 'check': 'native.output', 'details': None})
        with self.assertRaises(ResearchError):
            native_review.validate_output(self.native, self.read_json(attempt['output']))

    def test_any_non_fence_trailer_refuses_the_answer(self):
        output = self.build_output()
        text = '{}\nRETRACTED\nCorrection:\n' + json.dumps(output)
        self.assert_refusal(text)
        text = '{}\nRETRACTED\n' + json.dumps(output)
        self.assert_correction(text, output)
        for trailer in ('\nDo not use this assessment.', '\n```\nExtra text.', '\n```json',
                        '\n```\n```', '\n[]', '\nnull', '\n{"unfinished":'):
            with self.subTest(trailer=trailer):
                attempt = self.observe_text(text + trailer)
                self.assertEqual(attempt['status'], 'failed')
                self.assertIsNone(attempt['output'])
                self.assertEqual(attempt['error']['kind'], 'JSONDecodeError')

    def test_single_objects_and_previously_invalid_answers_keep_their_behavior(self):
        output = self.build_output()
        plain = json.dumps(output)
        for text, answer, fenced in ((plain, plain, False), (' \n' + plain + '\n ', ' \n' + plain + '\n ', False),
                                     ('```json\n' + plain + '\n```', plain, True)):
            with self.subTest(text=text):
                attempt = self.observe_text(text)
                self.assertEqual(attempt['status'], 'completed')
                response = self.read_json(attempt['response'])
                self.assertEqual(response['output'][0]['content'][0]['text'], answer)
                self.assertIs(response['execution']['fence_removed'], fenced)
                self.assertNotIn('self_corrected', response['execution'])
                self.assertNotIn('retracted_object_count', response['execution'])
                self.assertNotIn('result_text', response['execution'])
                self.assertIsNone(self.classify_attempt(attempt))
        for text in ('[]', 'null', '1', '"answer"', '', 'Introduction:\n' + plain, plain + '\nCommentary.',
                     'The example is "{}".\n' + plain, 'RETRACTED\n' + plain, plain + '\nRETRACTED'):
            with self.subTest(text=text):
                self.assertEqual(self.observe_text(text)['status'], 'failed')
        self.assert_correction('{}\nRETRACTED\n' + plain, output)

    def test_syntax_errors_and_objects_inside_arrays_are_not_rescued(self):
        output = self.build_output()
        last = json.dumps(output)
        self.assert_correction('{}\nRETRACTED\n' + last, output)
        for text in ('{"dependencies": }\nRETRACTED\n' + last,
                     '{"unfinished": {"nested": {}}\nRETRACTED\n' + last,
                     '[{}]\nRETRACTED\n' + last,
                     '{}\nRETRACTED\n[' + last + ']'):
            with self.subTest(text=text):
                self.assertEqual(self.observe_text(text)['status'], 'failed')
        self.assert_refusal('{}\nCorrection:\n[' + last + ']\nCorrection:\nRETRACTED\n' + last)
        self.assert_refusal('{}\nRETRACTED\nCorrection:\n[' + last + ']\nCorrection:\n' + last)

    def test_nested_objects_and_long_strings_are_decoded_in_disjoint_spans(self):
        output = self.build_output()
        output['findings']['dependencies'] = 'Quoted braces: {"nested": {"value": "}"}} and an escaped \\".'
        first = json.dumps({'nested': {'object': {'deeper': []}}, 'text': '{' * 20000})
        middle = json.dumps({'another': {'nested': {}}})
        text = first + '\nRETRACTED\n' + 'Correction. ' * 20000 + '\n' + middle
        text += '\nRETRACTED\nFinal correction:\n' + json.dumps(output)
        self.assert_refusal(text)
        text = first + '\nRETRACTED\n' + middle + '\nRETRACTED\n' + json.dumps(output)
        decoded_spans = []
        decode = json.JSONDecoder.raw_decode

        def read_span(decoder, source, idx=0):
            value, end = decode(decoder, source, idx)
            if source == text:
                decoded_spans.append((idx, end))
            return value, end

        with patch.object(json.JSONDecoder, 'raw_decode', read_span):
            self.assert_correction(text, output, retracted_object_count=2)
        self.assertEqual(len(decoded_spans), 3)
        self.assertEqual(decoded_spans[0], (0, len(first)))
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(decoded_spans, decoded_spans[1:])))
        self.assertLessEqual(sum(end - start for start, end in decoded_spans), len(text))
        self.assertEqual(decoded_spans[-1][1], len(text))

    def test_embedded_code_examples_are_not_answer_candidates(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        for example in ('{"decision":"revise","reason":"Illustrative only."}', json.dumps(self.build_output())):
            text = first + '\nRETRACTED\nQuoted packet example, not my assessment:\n```python\n'
            text += 'example = ' + example + '\n```'
            with self.subTest(example=example):
                attempt = self.observe_text(text)
                self.assertEqual(attempt['status'], 'failed')
                self.assertIsNone(attempt['output'])
                response = self.read_json(attempt['response'])
                self.assertEqual(response['output'][0]['content'][0]['text'], text)
                self.assertNotIn('self_corrected', response['execution'])

    def test_prose_punctuation_refuses_recovery(self):
        output = {'decision': 'reject', 'reason': 'A gap remains.'}
        for prose in ('Correction: remove [typo] from the explanation.',
                      'Correction: replace {policy} in the explanation.',
                      'Correction: an unmatched "quotation begins here.',
                      'Correction: "the quoted explanation\ncontinues on this line".',
                      'The [typo] and {policy} fields and an unmatched "quotation need correction.',
                      '[{"example": {}}]\nCorrection: the array above is an example.'):
            with self.subTest(prose=prose):
                self.assert_refusal('{}\n' + prose + '\nRETRACTED\n' + json.dumps(output))
                self.assert_refusal('{}\nRETRACTED\n' + prose + '\n' + json.dumps(output))
        text = '{}\n[typo]\nThe {policy} field and an unmatched "quotation need correction.\nRETRACTED\n'
        text += json.dumps(output)
        attempt = self.observe_text(text)
        self.assertEqual(attempt['status'], 'failed')
        self.assertIsNone(attempt['output'])
        self.assert_refusal('{}\nRETRACTED\n[typo]\n' + json.dumps(output))

    def test_only_line_delimited_objects_count_as_candidates(self):
        last = json.dumps(self.build_output())
        for prose in ('example = {}', '{}; explanation', '{} {}', '  {} inline text', '\t{}\t inline text'):
            with self.subTest(prose=prose):
                attempt = self.observe_text(prose + '\nRETRACTED\n' + last)
                self.assertEqual(attempt['status'], 'failed')
                self.assertIsNone(attempt['output'])
        for text in (' \t{}\t\r\n \tRETRACTED\t \r\n```json\r\n\t' + last + ' \t\r\n```\r\n',
                     '{}\n```python\nexample = {}\n```\nRETRACTED\n' + last):
            with self.subTest(text=text):
                self.assert_refusal(text)
        text = '{}\t\r\nRETRACTED\r\n```json\r\n' + last + ' \t\r\n```\r\n'
        self.assert_correction(text, self.build_output())

    def test_array_lines_refuse_recovery(self):
        output = {'decision': 'reject', 'reason': 'A gap remains.'}
        last = json.dumps(output)
        for text in ('[\n  {}\n]\n' + last,
                     '[\n  {}\n]\nRETRACTED\n' + last,
                     '{}\n[\n{"decision":"revise","reason":"An array element."}',
                     '{}\nRETRACTED\n[\n  {"nested": [\n    ' + last + '\n  ]}\n'):
            with self.subTest(text=text):
                attempt = self.observe_text(text)
                self.assertEqual(attempt['status'], 'failed')
                self.assertIsNone(attempt['output'])
                self.assertNotIn('self_corrected', self.read_json(attempt['response'])['execution'])
        self.assert_refusal('{}\n[\n  {"example": [\n    {}\n  ]}\n]\nRETRACTED\n' + last)
        self.assert_refusal('{}\nRETRACTED\n[\n{"decision":"revise","reason":"An array element."}')
        self.assert_refusal('{}\nRETRACTED\n[\n  {"example": [\n    {}\n  ]}\n]\n' + last)

    def test_non_json_fences_do_not_supply_answer_candidates(self):
        first = '{"decision":"reject","reason":"The proof fails."}'
        examples = ('{"decision":"revise","reason":"Illustrative only."}', json.dumps(self.build_output()))
        for tag, prefix in (('javascript', 'const example =\n'), ('python', '')):
            for example in examples:
                text = first + '\nRETRACTED\nQuoted packet example, not my assessment:\n```' + tag + '\n'
                text += prefix + example + '\n```'
                with self.subTest(tag=tag, example=example):
                    attempt = self.observe_text(text)
                    self.assertEqual(attempt['status'], 'failed')
                    self.assertIsNone(attempt['output'])
                    self.assertNotIn('self_corrected', self.read_json(attempt['response'])['execution'])
        output = {'decision': 'reject', 'reason': 'The gap remains.'}
        text = '{}\n```python\n[unterminated\n{not_json}\n```\nRETRACTED\n' + json.dumps(output)
        self.assert_refusal(text)

    def test_unlabelled_and_json_fences_keep_the_corrected_answer(self):
        output = self.build_output()
        for tag in ('', 'JSON', 'jSoN'):
            text = '{}\nCorrection:\nRETRACTED\n```' + tag + '\n' + json.dumps(output) + '\n```\n'
            with self.subTest(tag=tag):
                self.assert_refusal(text)
                text = '{}\nRETRACTED\nCorrection:\n```' + tag + '\n' + json.dumps(output) + '\n```\n'
                self.assert_refusal(text)
                text = '{}\nRETRACTED\n```' + tag + '\n' + json.dumps(output) + '\n```\n'
                attempt = self.assert_correction(text, output)
                self.assertIsNone(self.classify_attempt(attempt))

    def test_unclosed_answer_fences_refuse_recovery(self):
        last = json.dumps(self.build_output())
        for text in ('{}\nRETRACTED\n```json\n' + last,
                     '{}\nRETRACTED\n```\n' + last,
                     '```json\n{}\nRETRACTED\n' + last,
                     '```python\nAn example without a closing fence.\n{}\nRETRACTED\n' + last):
            with self.subTest(text=text):
                attempt = self.observe_text(text)
                self.assertEqual(attempt['status'], 'failed')
                self.assertIsNone(attempt['output'])
        self.assert_refusal('{}\nRETRACTED\n```python\n' + last)

    def test_brace_prefixed_prose_keeps_the_existing_refusal(self):
        text = '{}\n{policy} is the field corrected below.\n{"decision":"reject","reason":"A gap remains."}'
        attempt = self.observe_text(text)
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['error'], {'code': 'review_transport_failure', 'kind': 'JSONDecodeError'})
        self.assertIsNone(attempt['output'])
        response = self.read_json(attempt['response'])
        self.assertEqual(response['output'][0]['content'][0]['text'], text)
        self.assertNotIn('self_corrected', response['execution'])

    def test_last_candidate_accepts_only_its_own_closing_fence(self):
        last = json.dumps(self.build_output())
        for text in ('{}\nRETRACTED\n' + last + '\n```',
                     '```json\n{}\n```\nRETRACTED\n' + last + '\n```',
                     '{}\nRETRACTED\n```json\n' + last + '\n```\n```',
                     '{}\nRETRACTED\n```json\n' + last + '\n```\nA later qualification.'):
            with self.subTest(text=text):
                attempt = self.observe_text(text)
                self.assertEqual(attempt['status'], 'failed')
                self.assertIsNone(attempt['output'])
        self.assert_correction('{}\nRETRACTED\n```json\n' + last + '\n```\n \t', self.build_output())

    def test_recursive_candidate_returns_response_and_records_failed_attempt(self):
        nested = '{"x":' * 2000 + '0' + '}' * 2000
        self.assert_refusal('{}\n' + nested)
        prefix = '{}\nRETRACTED\n'
        text = prefix + nested
        events = clean_stream(self.request, output=text)
        with patch.object(json.scanner, 'make_scanner', json.scanner.py_make_scanner), \
                patch.object(claude, '_cli', return_value=('/usr/local/bin/claude', 'fixture-version')), \
                patch.object(claude, '_run_owned', return_value=completed(events)):
            with self.assertRaises(RecursionError):
                json.JSONDecoder().raw_decode(text, len(prefix))
            try:
                response = claude.send_request(self.route, self.request)
            except RecursionError:
                self.fail('Candidate recursion escaped before the response could be retained')
        self.assertEqual(response['output'][0]['content'][0]['text'], text)
        self.assertNotIn('self_corrected', response['execution'])
        protocol.record_route(self.store, ROUTE, expected_revision=self.store.revision, request_id='deep-route')
        payload = {'id': 'deep-attempt', 'assignment_id': self.assignment['id']}

        def build_invocation(records, value):
            invocation = dict(value, route_id=self.route['id'],
                              request=protocol._put_json(self.artifacts, self.request))
            return [immutable_record(records, 'review_invocation', value['id'], invocation)], invocation

        def record_observation(records, value, invocation, observation, revision):
            attempt = dict(observation, **value, origin='instrumented', recorded_revision=revision)
            return [immutable_record(records, 'review_attempt', value['id'], attempt)], attempt

        with patch.object(json.scanner, 'make_scanner', json.scanner.py_make_scanner), \
                patch.object(claude, '_cli', return_value=('/usr/local/bin/claude', 'fixture-version')), \
                patch.object(claude, '_run_owned', return_value=completed(events)):
            receipt = protocol._invoke_once(self.store, payload, 'review.invoke', build_invocation,
                record_observation, expected_revision=self.store.revision, request_id='deep-run', credential=None)
        attempt = receipt['result']
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['error'], {'code': 'review_transport_failure', 'kind': 'JSONDecodeError'})
        self.assertIsNone(attempt['output'])
        self.assertEqual(self.read_json(attempt['response'])['output'][0]['content'][0]['text'], text)
        records = self.store.snapshot()['records']
        self.assertIn(payload['id'], records['review_invocation'])
        self.assertEqual(records['review_attempt'][payload['id']], attempt)
        with patch.object(transport, 'observe', side_effect=AssertionError('A failed attempt must replay')):
            replayed = protocol._invoke_once(self.store, payload, 'review.invoke', build_invocation,
                record_observation, expected_revision=0, request_id='deep-run', credential=None)
        self.assertEqual(replayed, receipt)

    def test_discarded_large_numbers_do_not_delay_candidate_discovery(self):
        script = textwrap.dedent('''\
            import sys
            sys.path[:0] = sys.argv[1:3]
            import math
            from time import monotonic
            from research_harness import review_claude_transport as claude, review_transport as transport
            from test_research_claude_result_text import ClaudeResultTextTests
            case = ClaudeResultTextTests()
            case.setUp()
            started = monotonic()
            try:
                text = '{}\\nRETRACTED\\n{"n":' + '9' * 500_000 + '}\\nRETRACTED\\n'
                text += '{"decision":"reject","reason":"Gap."}'
                attempt = case.assert_correction(text, {'decision': 'reject', 'reason': 'Gap.'}, 2)
                assert case.classify_attempt(attempt) is None
                text = ('{"integer":123,"fraction":1e9999,"constant":NaN,"positive":Infinity,"negative":-Infinity}'
                        '\\nRETRACTED\\n{"integer":7,"fraction":1.25,"constant":NaN}')
                answer, _, _ = claude.parse_result_text(text)
                response = {'id': 'numbers', 'status': 'completed', 'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': answer}]}]}
                output = transport.response_output(response)
                case.assertIs(type(output['integer']), int)
                case.assertEqual(output['integer'], 7)
                case.assertIs(type(output['fraction']), float)
                case.assertEqual(output['fraction'], 1.25)
                case.assertTrue(math.isnan(output['constant']))
                print('500000-digit discovery seconds=' + str(monotonic() - started))
            finally:
                case.doCleanups()
        ''')
        tests_directory = Path(__file__).resolve().parent
        try:
            process = subprocess.run([sys.executable, '-c', script,
                                      str(tests_directory), str(tests_directory.parent)],
                                     cwd=self.root, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            self.fail('Discovery of a discarded 500000-digit integer exceeded 10 seconds')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        print(process.stdout.strip())

    def test_recorded_attempts_replay_the_saved_results_without_parsing_again(self):
        output = self.build_output()
        plain = json.dumps(output)
        old_text = '{}\nCorrection:\n' + plain
        legacy_response = {'id': 'legacy-session', 'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': old_text}]}]}
        observations = []
        for text, status in ((old_text, 'failed'), (plain, 'completed')):
            response = copy.deepcopy(legacy_response)
            response['output'][0]['content'][0]['text'] = text
            with patch.object(claude, 'send_request', return_value=response):
                observation = protocol._observed(self.artifacts, self.route, self.request, None)
            self.assertEqual(observation['status'], status)
            observations.append(observation)
        retained_text = '{}\nRETRACTED\nCorrection:\n' + plain
        self.assert_refusal(retained_text)
        retained_response = copy.deepcopy(legacy_response)
        retained_response['output'][0]['content'][0]['text'] = plain
        retained_response['execution'] = {'self_corrected': True, 'retracted_object_count': 1,
                                          'result_text': retained_text, 'result_text_digest': digest(retained_text)}
        with patch.object(claude, 'send_request', return_value=retained_response):
            observation = protocol._observed(self.artifacts, self.route, self.request, None)
        self.assertEqual(observation['status'], 'completed')
        observations.append(observation)
        observations.append(self.assert_correction('{}\nRETRACTED\n' + plain, output))
        receipts = []
        for index, observation in enumerate(observations):
            payload = {'id': 'saved-' + str(index), 'assignment_id': self.assignment['id']}

            def prepare(records, value):
                attempt = dict(observation, **value)
                return [immutable_record(records, 'review_attempt', value['id'], attempt)], attempt

            receipt = prepared_mutation(self.store, 'review.invoke', payload, prepare,
                                        expected_revision=self.store.revision, request_id=payload['id'])
            receipts.append((payload, receipt))
        snapshot = self.store.snapshot()

        def refuse_reinvocation(*args):
            self.fail('A recorded invocation must replay without invoking or parsing the reviewer')

        with patch.object(transport, 'observe', side_effect=refuse_reinvocation):
            for payload, receipt in receipts:
                replayed = protocol._invoke_once(Store(self.root), payload, 'review.invoke', refuse_reinvocation,
                    refuse_reinvocation, expected_revision=0, request_id=payload['id'], credential=None)
                self.assertEqual(replayed, receipt)
        self.assertEqual(Store(self.root).snapshot(), snapshot)
        self.assertEqual(self.read_json(observations[0]['response']), legacy_response)
        self.assertEqual(self.read_json(observations[2]['response']), retained_response)


if __name__ == '__main__':
    unittest.main()
