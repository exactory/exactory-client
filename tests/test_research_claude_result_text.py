"""Claude self-corrections retain the full answer and still pass the ordinary output contracts."""

import copy
import json
import tempfile
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
from test_research_claude_transport import ROUTE, clean_stream, completed


class ClaudeResultTextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'review'
        self.store = Store(self.root, create=True)
        self.artifacts = ArtifactStore(self.root)
        self.native = {'kind': 'result', 'subject_digest': 'subject', 'claim_digest': 'claim',
                       'reviewer': {'source': 'host', 'actor_id': 'reviewer', 'attestation_id': 'assignment'}}
        packet = {'protocol': protocol.PROTOCOL, 'role': 'native_math', 'native': self.native}
        packet['digest'] = digest(packet)
        prompt = protocol.canonical_prompt('native_math')
        self.assignment = {'id': 'assignment', 'role': 'native_math', 'route_id': ROUTE['id'],
                           'route_digest': digest(ROUTE), 'packet': protocol._put_json(self.artifacts, packet),
                           'packet_digest': packet['digest'], 'prompt': prompt,
                           'prompt_hash': prompt['rendered_prompt_hash']}
        self.request = transport.request_body(ROUTE, prompt['text'], packet)

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
            observation = protocol._observed(self.artifacts, ROUTE, self.request, None)
        return dict(observation, id='attempt', assignment_id=self.assignment['id'], origin='instrumented',
                    packet_digest=self.assignment['packet_digest'], prompt_hash=self.assignment['prompt_hash'])

    def classify_attempt(self, attempt):
        records = {'review_route': {ROUTE['id']: ROUTE},
                   'review_assignment': {self.assignment['id']: self.assignment},
                   'review_attempt': {attempt['id']: attempt}}
        return classify_observed_output(records, self.artifacts, attempt['id'])

    def assert_correction(self, text, expected):
        attempt = self.observe_text(text)
        self.assertEqual(attempt['status'], 'completed', attempt['error'])
        self.assertEqual(self.read_json(attempt['output']), expected)
        response = self.read_json(attempt['response'])
        self.assertIs(response['execution']['self_corrected'], True)
        self.assertEqual(response['execution']['result_text'], text)
        self.assertEqual(response['execution']['result_text_digest'], digest(text))
        self.assertTrue(transport.context_verified(ROUTE, self.request, response))
        self.assertEqual(transport.response_output(response), expected)
        return attempt

    def test_a_contract_malformed_first_object_is_retained_with_the_valid_correction(self):
        output = self.build_output()
        malformed = copy.deepcopy(output)
        malformed['findings'].update(dependencies=[], policy='')
        with self.assertRaises(ResearchError):
            native_review.validate_output(self.native, malformed)
        text = json.dumps(malformed) + '\nCorrection: the complete assessment follows.\n' + json.dumps(output)
        attempt = self.assert_correction(text, output)
        self.assertIsNone(self.classify_attempt(attempt))
        native_review.validate_output(self.native, self.read_json(attempt['output']))

    def test_separate_fences_and_a_shared_fence_keep_the_exact_whole_text(self):
        output = self.build_output()
        first, last = json.dumps({'decision': 'approve'}), json.dumps(output)
        for text in (first + '\n' + last,
                     '```json\n' + first + '\n```\nCorrected assessment:\n```json\n' + last + '\n```',
                     ' \n```json\n' + first + '\nCorrection follows.\n' + last + '\n```\n ',
                     '```\n' + first + '\n```\nCorrection:\n' + last + '\n',
                     first + '\nCorrection:\n```JSON\n' + last + '\n```',
                     'Initial assessment:\n' + first + '\nCorrection:\n' + last,
                     'The example is "{}".\n' + first + '\nThe "corrected" assessment follows.\n' + last):
            with self.subTest(text=text):
                self.assert_correction(text, output)

    def test_an_invalid_last_object_is_refused_by_the_native_contract_without_falling_back(self):
        output = self.build_output()
        invalid = copy.deepcopy(output)
        invalid['findings']['policy'] = ''
        attempt = self.assert_correction(json.dumps(output) + '\nCorrection:\n' + json.dumps(invalid), invalid)
        self.assertEqual(self.classify_attempt(attempt),
                         {'code': 'invalid_native_review', 'check': 'native.output', 'details': None})
        with self.assertRaises(ResearchError):
            native_review.validate_output(self.native, self.read_json(attempt['output']))

    def test_any_non_fence_trailer_refuses_the_answer(self):
        output = self.build_output()
        text = '{}\nCorrection:\n' + json.dumps(output)
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
                self.assertNotIn('result_text', response['execution'])
                self.assertIsNone(self.classify_attempt(attempt))
        for text in ('[]', 'null', '1', '"answer"', '', 'Introduction:\n' + plain, plain + '\nCommentary.',
                     'The example is "{}".\n' + plain):
            with self.subTest(text=text):
                self.assertEqual(self.observe_text(text)['status'], 'failed')
        self.assert_correction('{}\n' + plain, output)

    def test_syntax_errors_and_objects_inside_arrays_are_not_rescued(self):
        output = self.build_output()
        last = json.dumps(output)
        self.assert_correction('{}\n' + last, output)
        for text in ('{"dependencies": }\nCorrection:\n' + last,
                     '{"unfinished": {"nested": {}}\nCorrection:\n' + last,
                     '[{}]\nCorrection:\n' + last,
                     '{}\nCorrection:\n[' + last + ']',
                     '{}\nCorrection:\n[' + last + ']\nCorrection:\n' + last):
            with self.subTest(text=text):
                self.assertEqual(self.observe_text(text)['status'], 'failed')

    def test_nested_braces_and_long_prose_are_decoded_in_disjoint_spans(self):
        output = self.build_output()
        output['findings']['dependencies'] = 'Quoted braces: {"nested": {"value": "}"}} and an escaped \\".'
        first = json.dumps({'nested': {'object': {'deeper': []}}, 'text': '{' * 20000})
        middle = json.dumps({'another': {'nested': {}}})
        text = first + '\n' + 'Correction. ' * 20000 + '\n' + middle + '\nFinal correction:\n' + json.dumps(output)
        decoded_spans = []
        decode = json.JSONDecoder.raw_decode

        def read_span(decoder, source, idx=0):
            value, end = decode(decoder, source, idx)
            if source == text:
                decoded_spans.append((idx, end))
            return value, end

        with patch.object(json.JSONDecoder, 'raw_decode', read_span):
            self.assert_correction(text, output)
        self.assertEqual(len(decoded_spans), 3)
        self.assertEqual(decoded_spans[0], (0, len(first)))
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(decoded_spans, decoded_spans[1:])))
        self.assertLessEqual(sum(end - start for start, end in decoded_spans), len(text))
        self.assertEqual(decoded_spans[-1][1], len(text))

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
                observation = protocol._observed(self.artifacts, ROUTE, self.request, None)
            self.assertEqual(observation['status'], status)
            observations.append(observation)
        observations.append(self.assert_correction(old_text, output))
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


if __name__ == '__main__':
    unittest.main()
