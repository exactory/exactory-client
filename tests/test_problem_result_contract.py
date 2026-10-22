"""Prospective result contracts preserve old findings and actual input boundaries."""

import copy
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

import test_research_problem_reviews as fixtures
from research_harness import direction_reviews, review_protocol as protocol
from research_harness import research_decisions as decisions, review_transport as transport
from research_harness.errors import ResearchError
from research_harness.evidence import digest


class ProblemResultContractTests(unittest.TestCase):
    def setUp(self):
        # Load the native candidate dependency before the existing claim fixture replaces its module.
        importlib.import_module('research_harness.problem_candidates')
        self.case = fixtures.ProblemReviewTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()

    def old_result(self):
        case = self.case
        case.observe_bar_reviews()
        original = protocol.canonical_prompt
        def old_prompt(role, **options):
            if (role == 'result' and options.get('orchestration') == 'problem_creation'
                    and 'version' not in options):
                options['version'] = 2
            return original(role, **options)
        output = case.build_review_output('result')
        output['value']['assurances'][-1].update(status='not_assessed',
            reason='The delivered contract left the stop assessment incomplete.')
        with patch.object(protocol, 'canonical_prompt', side_effect=old_prompt):
            case.observe('result', 'first', output)
        return case.read_records()['review_assignment']['result-first'], output

    def test_new_result_example_does_not_treat_completed_no_stop_assessment_as_incomplete(self):
        old = protocol.canonical_prompt('result', version=2, orchestration='problem_creation')
        current = protocol.canonical_prompt('result', orchestration='problem_creation')
        self.assertEqual(old['template_hash'], 'dbe0052d524fce3b04fd975777f3c19d163e5e13bb6e56fc50943d547782fc93')
        self.assertEqual(current['template_id'], 'exactory-problem-review-v4/result')
        marker = 'A valid final response, with the placeholders replaced by the delivered values: '
        example, _ = json.JSONDecoder().raw_decode(current['text'].split(marker, 1)[1])
        self.assertEqual([row['status'] for row in example['value']['assurances'] if row['kind'] == 'stop'], ['passed'])
        self.assertNotEqual(current['template_hash'], old['template_hash'])
        for role in ('bar', 'slate', 'adjudicator'):
            self.assertEqual(protocol.canonical_prompt(role, orchestration='problem_creation')['template_id'],
                             'exactory-problem-review-v2/' + role)

    def test_current_result_explains_native_page_order_and_bounded_citations(self):
        current = protocol.canonical_prompt('result', orchestration='problem_creation')['text']
        previous = protocol.canonical_prompt('result', version=2, orchestration='problem_creation')['text']
        self.assertIn('visual_source_delivery', current)
        self.assertIn('exact-descriptor deduplication', current)
        self.assertIn('previously unseen image descriptors', current)
        self.assertIn('does not deliver the whole original PDF artifact', current)
        self.assertNotIn('visual_source_delivery', previous)

    def test_exact_version_two_results_remain_verified_including_context_repair(self):
        for repair in (False, True):
            for old in (protocol.canonical_prompt('result', version=2, repair=repair, orchestration='problem_creation'),
                        protocol.previous_problem_result_prompt(repair)):
                assignment = {'role': 'result', 'subject': {'kind': 'problem'},
                              'prompt': old, 'reassessment_required': repair}
                self.assertTrue(protocol.is_previous_prompt(assignment))
                self.assertTrue(protocol._prompt_matches(assignment))
                for key in ('text', 'template_hash', 'template_id'):
                    altered = copy.deepcopy(assignment)
                    altered['prompt'][key] += ' altered'
                    self.assertFalse(protocol._prompt_matches(altered))

    def test_legacy_final_continues_once_with_exact_output_and_same_bar_slot(self):
        case = self.case
        prior, output = self.old_result()
        before = copy.deepcopy(case.read_records())
        attempt = before['review_attempt']['attempt-result-first']
        request = json.loads(case.artifacts.read(attempt['request']))
        try:
            successor = case.assign('result', 'first', identifier='result-contract-refresh')
            refusal = None
        except ResearchError as error:
            successor, refusal = None, error.code
        self.assertIsNotNone(successor, 'The recognized old contract needs a bounded refresh: ' + str(refusal))
        for key in ('route_id', 'reviewer_id', 'author_id', 'session_id', 'slot_id', 'subject'):
            self.assertEqual(successor[key], prior[key])
        self.assertEqual(successor['history_assignment_id'], prior['id'])
        self.assertEqual(successor['history'][:-1], transport.logical_messages(request))
        self.assertEqual(successor['history'][-1], {'role': 'assistant', 'content': json.dumps(output, sort_keys=True)})
        packet = json.loads(case.artifacts.read(successor['packet']))
        refresh = packet['problem_refresh']
        self.assertEqual(refresh['reason'], 'prompt_contract_changed')
        self.assertEqual(refresh['previous_template_id'], prior['prompt']['template_id'])
        self.assertEqual(refresh['previous_prompt_hash'], prior['prompt']['rendered_prompt_hash'])
        self.assertEqual(refresh['template_id'], successor['prompt']['template_id'])
        self.assertEqual(refresh['prompt_hash'], successor['prompt']['rendered_prompt_hash'])
        for kind in ('review_assignment', 'review_attempt', 'value_review'):
            for identifier, original in before[kind].items():
                self.assertEqual(case.read_records()[kind][identifier], original)
        case.assert_error('review_source_continuation_fork', lambda: case.assign('result', 'first',
            identifier='fork', prior_assignment_id=prior['id']))
        with patch.object(transport, 'send_request', return_value=case.build_provider_response(output)):
            case.mutate(protocol.invoke_assignment, {'id': 'attempt-refresh', 'assignment_id': successor['id']}, credential='test-only')
        case.mutate(decisions.record_value_review, dict(output, id='review-refresh', dossier_id=None,
                                                       assignment_id=successor['id']))
        case.assert_error('review_resampling_forbidden', lambda: case.assign('result', 'first',
            identifier='another-current-result', prior_assignment_id=successor['id']))
        self.assertEqual(case.read_records()['value_review']['review-refresh']['payload']['value']['assurances'][-1]['status'],
                         'not_assessed')

    def test_current_final_stays_fixed_and_incomplete_stop_still_blocks_posting(self):
        case = self.case
        case.observe_bar_reviews()
        output = case.build_review_output('result')
        output['value']['assurances'][-1]['status'] = 'not_assessed'
        case.observe('result', 'first', output)
        case.observe('result', 'second')
        case.assert_error('review_resampling_forbidden', lambda: case.assign('result', 'first', identifier='repeat'))
        state = decisions.assess_problem_reviews(case.read_records(), case.artifacts)
        checks = [item for item in state['obligations'] if item['code'] == 'problem_assurance_pending']
        self.assertTrue(checks)
        self.assertEqual(case.read_records()['value_review']['review-result-first']['payload']['value']['assurances'][-1]['status'],
                         'not_assessed')

    def binary_computation(self):
        case = self.case
        case.add_reading_and_computation()
        binary = case.artifacts.put(b'%PDF-1.4\nAn opaque computation report, supplied as exact bytes.\n%%EOF\n', 'application/pdf')
        case.expected['computations'][0]['execution']['outputs'][0]['artifact'] = binary
        return binary

    def local_projection(self):
        original = transport.request_body
        def request_body(route, prompt, packet, history=()):
            return original(dict(route, adapter='codex_cli_v1', endpoint='codex://local'), prompt, packet, history)
        active = patch.object(transport, 'request_body', side_effect=request_body)
        active.start()
        self.addCleanup(active.stop)

    def test_saved_binary_is_not_covered_when_actual_native_text_route_omits_it(self):
        case = self.case
        binary = self.binary_computation()
        case.observe_bar_reviews()
        self.local_projection()
        for reviewer in ('first', 'second'):
            case.observe('result', reviewer)
        records = case.read_records()
        raw = json.loads(case.artifacts.read(records['review_assignment']['result-first']['packet']))
        self.assertTrue(any(entry['artifact'] == binary and entry.get('content') for entry in raw['evidence']))
        pending = decisions._assess_problem_coverage(records, case.artifacts,
            [records['value_review']['review-result-first']], case.expected, digest(case.expected))
        self.assertTrue(pending, 'The actual request omits the binary despite its raw saved packet.')
        self.assertIn(binary['sha256'], pending[0]['artifact_digests'])

    def test_actual_exact_file_input_keeps_coverage_without_restoring_saved_bytes(self):
        case = self.case
        binary = self.binary_computation()
        case.observe_result_reviews()
        records = case.read_records()
        actual = json.loads(case.artifacts.read(records['review_attempt']['attempt-result-first']['request']))
        self.assertTrue(any(part['type'] == 'input_file' for part in actual['input'][-1]['content']))
        helper = getattr(direction_reviews, 'collect_actual_input_packets', None)
        self.assertTrue(callable(helper), 'Problem coverage needs authenticated actual input projection.')
        with patch.object(type(case.artifacts), 'put', side_effect=AssertionError('Coverage must be read-only.')):
            packets = helper(records, case.artifacts, 'result-first')
            pending = decisions._assess_problem_coverage(records, case.artifacts,
                [records['value_review']['review-result-first']], case.expected, digest(case.expected))
        self.assertEqual(pending, [])
        entries = [entry for packet in packets for entry in packet.get('evidence', []) if entry['artifact'] == binary]
        self.assertTrue(entries and entries[-1].get('content'))

    def test_changed_observed_request_has_no_raw_packet_fallback(self):
        case = self.case
        self.binary_computation()
        case.observe_result_reviews()
        records = case.read_records()
        attempt = records['review_attempt']['attempt-result-first']
        actual = json.loads(case.artifacts.read(attempt['request']))
        actual['input'][-1]['content'] = 'A substituted input without the expected evidence.'
        attempt['request'] = case.artifacts.put(json.dumps(actual).encode(), 'application/json')
        helper = getattr(direction_reviews, 'collect_actual_input_packets', None)
        self.assertTrue(callable(helper), 'Problem coverage needs authenticated actual input projection.')
        self.assertEqual(helper(records, case.artifacts, 'result-first'), [])
        pending = decisions._assess_problem_coverage(records, case.artifacts,
            [records['value_review']['review-result-first']], case.expected, digest(case.expected))
        self.assertTrue(pending)


class PromptTerminalBoundaryTests(unittest.TestCase):
    def test_exact_version_three_prompt_bytes_and_recognition_are_preserved(self):
        expected = {
            False: ('a5d5a1ed1edb88d4442d2506cc59eccd2cf34cae6c016c775bbabb70160c9839',
                    '1ae5fc887347eeab6513f89735ed8b9848b1983fcd82f43825665a983fc817ca'),
            True: ('83cc6e48e08869d4c95486d29c512fd516f65bb268bcbdb81b8508afcbed1df8',
                   'c4a38d52b4d9da538609025b6d0338fa5f60ebec26657843735c02188b8eb2e0'),
        }
        for repair, (native_hash, byte_hash) in expected.items():
            with self.subTest(repair=repair):
                # The version 3 prompt was recorded with the context-repair suffix of 0.56.0.
                prompt = protocol.canonical_prompt('result', version=3, repair=repair, orchestration='problem_creation',
                                                   repair_prompt=protocol.context_repair.PROMPT_0560)
                suffix = '/context-repair' if repair else ''
                self.assertEqual(prompt['template_id'], 'exactory-problem-review-v3/result' + suffix)
                self.assertEqual(prompt['template_hash'], native_hash)
                self.assertEqual(prompt['rendered_prompt_hash'], native_hash)
                self.assertEqual(hashlib.sha256(prompt['text'].encode()).hexdigest(), byte_hash)
                assignment = {'role': 'result', 'subject': {'kind': 'problem'}, 'prompt': prompt,
                              'reassessment_required': repair}
                self.assertTrue(protocol.is_previous_prompt(assignment))
                self.assertTrue(protocol._prompt_matches(assignment))
                self.assertTrue(protocol.has_problem_result_rubric(assignment))
                changed = copy.deepcopy(assignment)
                changed['prompt']['text'] += ' '
                self.assertFalse(protocol._prompt_matches(changed))

    def test_new_version_four_has_only_the_native_terminal_space_correction(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                old = protocol.canonical_prompt('result', version=3, repair=repair,
                                                orchestration='problem_creation')
                current = protocol.canonical_prompt('result', repair=repair, orchestration='problem_creation')
                suffix = '/context-repair' if repair else ''
                self.assertEqual(current['template_id'], 'exactory-problem-review-v4/result' + suffix)
                self.assertEqual(current['text'], old['text'] if repair else old['text'][:-1])
                self.assertEqual(current['text'], current['text'].rstrip())
                if not repair:
                    self.assertEqual(old['text'][-1], ' ')
                    self.assertEqual(len(old['text']) - len(current['text']), 1)
                assignment = {'role': 'result', 'subject': {'kind': 'problem'}, 'prompt': current,
                              'reassessment_required': repair}
                self.assertFalse(protocol.is_previous_prompt(assignment))
                self.assertTrue(protocol._prompt_matches(assignment))
                changed = copy.deepcopy(assignment)
                changed['prompt']['text'] += ' '
                self.assertFalse(protocol._prompt_matches(changed))


if __name__ == '__main__':
    unittest.main()
