"""Prospective result contracts preserve old findings and actual input boundaries."""

import copy
import hashlib
import importlib
import json
import unittest
from unittest.mock import patch

import test_research_problem_reviews as fixtures
from problem_parts_fixtures import KEY as PARTS_KEY, ResultPartsCase
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
        old = protocol.canonical_prompt('result', version=2, orchestration='problem_creation', common_version=1)
        current = protocol.canonical_prompt('result', orchestration='problem_creation')
        self.assertEqual(old['template_hash'], 'dbe0052d524fce3b04fd975777f3c19d163e5e13bb6e56fc50943d547782fc93')
        self.assertEqual(current['template_id'], 'exactory-problem-review-v5/result')
        marker = 'A valid final response, with the placeholders replaced by the delivered values: '
        example, _ = json.JSONDecoder().raw_decode(current['text'].split(marker, 1)[1])
        self.assertEqual([row['status'] for row in example['value']['assurances'] if row['kind'] == 'stop'], ['passed'])
        self.assertNotEqual(current['template_hash'], old['template_hash'])
        for role in ('bar', 'slate', 'adjudicator'):
            self.assertEqual(protocol.canonical_prompt(role, orchestration='problem_creation')['template_id'],
                             ('exactory-problem-review-v2/' if role == 'adjudicator' else 'exactory-problem-review-v5/') + role)

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

    def add_bound_openness_search(self):
        from research_harness.acquisition import import_response
        from research_harness.literature import record_search
        from research_harness.problem_evidence import describe_openness_searches
        case = self.case
        query = 'bounded sequence comparison'
        captured = import_response(case.store, 'mcp',
            json.dumps({'query': query, 'results': [], 'metadata': 'r' * 300000}).encode(),
            source_url='https://example.org/search', captured_at='2026-09-07T12:00:00Z',
            media_type='application/json', mappings=[], expected_revision=case.store.revision,
            request_id='bound-search-response')
        source_id = captured['source_ids'][0]
        case.mutate(record_search, {'id': 'bound-search', 'profile': 'research', 'purpose': 'direct',
            'question': case.statement['statement'], 'queries': [query],
            'responses': [{'source_id': source_id, 'query': query,
                'query_locator': {'kind': 'json', 'pointer': '/query', 'value': query}, 'results_pointer': '/results'}],
            'captured_at': '2026-09-07T12:00:00Z', 'scope': case.statement['statement'],
            'found_work_ids': [], 'verdict': 'nothing-new', 'cited_work_ids': [], 'dispositions': [],
            'impact': 'No matching prior contribution was exposed in this saved search.', 'gaps': []})
        records = case.read_records()
        search = records['literature_search']['bound-search']
        case.expected['openness']['minimum'] = {'searches': [
            {'formulation': case.statement['statement'], 'search_digest': digest(search)}]}
        case.expected['openness_searches'] = describe_openness_searches(records, case.expected['openness'])
        return records['source'][source_id]['response']

    def observe_elided_search_result(self, response):
        case = self.case
        case.observe_bar_reviews()
        self.local_projection()
        limit = patch.object(transport, 'CODEX_INPUT_LIMIT_CHARS', 250000)
        limit.start()
        self.addCleanup(limit.stop)
        case.observe('result', 'first')
        records = case.read_records()
        packets = direction_reviews.collect_actual_input_packets(records, case.artifacts, 'result-first')
        current = [packet for packet in packets if packet.get('problem_evidence_digest') == digest(case.expected)]
        self.assertTrue(current)
        responses = [entry for packet in current for entry in packet['evidence'] if entry['artifact'] == response]
        self.assertTrue(responses)
        self.assertTrue(all(entry.get('delivery') == 'body-elided-route-limit' and entry.get('content') == ''
                            for entry in responses))
        return records

    def test_result_coverage_accepts_bound_search_records_when_response_bodies_are_elided(self):
        case = self.case
        case.add_reading_and_computation()
        response = self.add_bound_openness_search()
        records = self.observe_elided_search_result(response)
        pending = decisions._assess_problem_coverage(records, case.artifacts,
            [records['value_review']['review-result-first']], case.expected, digest(case.expected))
        self.assertEqual(pending, [])

    def test_result_coverage_requires_computation_bytes_when_search_responses_are_elided(self):
        case = self.case
        binary = self.binary_computation()
        response = self.add_bound_openness_search()
        records = self.observe_elided_search_result(response)
        pending = decisions._assess_problem_coverage(records, case.artifacts,
            [records['value_review']['review-result-first']], case.expected, digest(case.expected))
        self.assertEqual([row['code'] for row in pending], ['problem_evidence_unassessed'])
        self.assertEqual(pending[0]['artifact_digests'], [binary['sha256']])

    def test_result_coverage_requires_matching_bound_search_records(self):
        case = self.case
        response = self.add_bound_openness_search()
        records = self.observe_elided_search_result(response)
        expected = copy.deepcopy(case.expected)
        expected['openness_searches'][0]['search']['queries'] = ['a different formulation']
        pending = decisions._assess_problem_coverage(records, case.artifacts,
            [records['value_review']['review-result-first']], expected, digest(case.expected))
        self.assertEqual([row['code'] for row in pending], ['problem_evidence_unassessed'])
        self.assertEqual(pending[0]['missing'], ['openness_searches'])

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

    def test_a_result_in_a_bar_slot_repaired_after_the_statement_keeps_its_actual_input(self):
        """A context repair keeps the bar subject of the packet that it reassesses. When the bar predates the
        statement, the repair assignment names the statement and its packet does not; the result that continues the
        repaired slot still has its actual request counted."""
        case = self.case
        case.put('problem_selection', 'current', {'statement_id': None})
        case.observe_bar_reviews()
        case.put('problem_selection', 'current', {'statement_id': case.statement['id']})
        case.replace_first_bar()
        output = case.build_review_output('result')
        output['value']['bar_ids'] = ['review-bar-replaced', 'review-bar-second']
        case.observe('result', 'replacement', output)
        records = case.read_records()
        repair = records['review_assignment']['bar-replaced']
        self.assertEqual(repair['subject'], {'kind': 'problem', 'statement_id': case.statement['id']})
        self.assertEqual(json.loads(case.artifacts.read(repair['packet']))['subject'],
                         {'kind': 'problem', 'statement_id': None})
        packets = direction_reviews.collect_actual_input_packets(records, case.artifacts, 'result-replacement')
        self.assertIn(repair['packet_digest'], [packet['digest'] for packet in packets])
        pending = decisions._assess_problem_coverage(records, case.artifacts,
            [records['value_review']['review-result-replacement']], case.expected, digest(case.expected))
        self.assertEqual(pending, [])

    def test_bound_ancestry_holds_a_bar_packet_to_the_field_and_a_result_packet_to_its_statement(self):
        statement = {'kind': 'problem', 'statement_id': 'statement-1'}
        records = {'review_assignment': {
            'bar-repair': {'id': 'bar-repair', 'role': 'bar', 'reviewer_id': 'replacement', 'dossier_id': None,
                           'subject': statement, 'packet_digest': 'a' * 64, 'context': {}},
            'result-1': {'id': 'result-1', 'role': 'result', 'reviewer_id': 'replacement', 'dossier_id': None,
                         'subject': statement, 'packet_digest': 'b' * 64, 'history_assignment_id': 'bar-repair',
                         'context': {}}}}

        def build_packet(identifier, subject):
            assignment = records['review_assignment'][identifier]
            return {'digest': assignment['packet_digest'], 'role': assignment['role'],
                    'reviewer_id': assignment['reviewer_id'], 'dossier_id': None, 'subject': subject}
        check = direction_reviews._matches_bound_input_ancestry
        older_bar = build_packet('bar-repair', {'kind': 'problem', 'statement_id': None})
        result = build_packet('result-1', statement)
        self.assertTrue(check(records, 'result-1', [older_bar, result]))
        self.assertFalse(check(records, 'result-1', [older_bar, build_packet('result-1',
            {'kind': 'problem', 'statement_id': 'statement-2'})]))
        self.assertFalse(check(records, 'result-1', [dict(older_bar, subject=None), result]))


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
                                                   repair_prompt=protocol.context_repair.PROMPT_0560, common_version=1)
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

    def test_version_four_keeps_only_the_native_terminal_space_correction(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                old = protocol.canonical_prompt('result', version=3, repair=repair,
                                                orchestration='problem_creation')
                current = protocol.canonical_prompt('result', version=4, repair=repair, orchestration='problem_creation')
                suffix = '/context-repair' if repair else ''
                self.assertEqual(current['template_id'], 'exactory-problem-review-v4/result' + suffix)
                self.assertEqual(current['text'], old['text'] if repair else old['text'][:-1])
                self.assertEqual(current['text'], current['text'].rstrip())
                if not repair:
                    self.assertEqual(old['text'][-1], ' ')
                    self.assertEqual(len(old['text']) - len(current['text']), 1)
                assignment = {'role': 'result', 'subject': {'kind': 'problem'}, 'prompt': current,
                              'reassessment_required': repair}
                self.assertTrue(protocol.is_previous_prompt(assignment))
                self.assertTrue(protocol._prompt_matches(assignment))
                changed = copy.deepcopy(assignment)
                changed['prompt']['text'] += ' '
                self.assertFalse(protocol._prompt_matches(changed))


if __name__ == '__main__':
    unittest.main()



class ProblemResultSeriesCoverageTests(ResultPartsCase):
    def test_two_series_reviews_deliver_all_current_items(self):
        self.oversized()
        reviews = []
        for reviewer in ('first', 'second'):
            _, final = self.finish_parts(reviewer=reviewer)
            reviews.append(self.record_final(final))
        records = self.case.read_records()
        self.assertEqual(decisions._assess_problem_coverage(records, self.case.artifacts,
                         reviews, self.case.expected, digest(self.case.expected)), [])
        self.assertTrue(decisions.assess_problem_reviews(records, self.case.artifacts)['ready'])

    def test_indexed_duplicate_items_cannot_cover_an_omitted_position(self):
        self.oversized(count=5, characters=24000)
        self.case.expected['openness_searches'][1] = copy.deepcopy(self.case.expected['openness_searches'][0])
        parts, final = self.finish_parts()
        review = self.record_final(final)
        self.assertEqual(self.coverage(review), [])
        from research_harness import review_problem_parts
        packets = review_problem_parts.collect_series_packets(self.case.read_records(), self.case.artifacts, final['id'])
        altered = copy.deepcopy(packets)
        for packet in altered:
            ranges = packet[PARTS_KEY]['delivered_now']['openness_searches']
            if ranges and ranges[0][0] == 0:
                packet['problem_evidence']['openness_searches'] = packet['problem_evidence']['openness_searches'][:1]
                packet[PARTS_KEY]['delivered_now']['openness_searches'] = [[0, 1]]
        self.assertFalse(review_problem_parts.covers_items(altered, self.case.expected, 'openness_searches'))
        with patch.object(review_problem_parts, 'collect_series_packets', return_value=altered):
            pending = self.coverage(review)
        self.assertTrue(pending)
        self.assertIn('openness_searches', pending[0]['missing'])

    def test_an_unverified_or_other_digest_or_other_series_turn_has_no_credit(self):
        self.oversized()
        parts, final = self.finish_parts()
        review = self.record_final(final)
        from research_harness import review_problem_parts
        original_state = protocol.assignment_state
        def unverified(records, artifacts, identifier):
            state = original_state(records, artifacts, identifier)
            return dict(state, ready=False) if identifier == parts[0]['id'] else state
        with patch.object(protocol, 'assignment_state', side_effect=unverified):
            pending = self.coverage(review)
        self.assertTrue(pending)
        packets = review_problem_parts.collect_series_packets(self.case.read_records(), self.case.artifacts, final['id'])
        for field, value in (('problem_evidence_digest', '0' * 64), ('series_assignment_id', 'other-series')):
            altered = copy.deepcopy(packets)
            part = next(p for p in altered if p[PARTS_KEY]['stage'] == 'part')
            if field == 'series_assignment_id':
                part[PARTS_KEY][field] = value
            else:
                part[field] = value
            with patch.object(review_problem_parts, 'collect_series_packets', return_value=altered):
                self.assertTrue(self.coverage(review), field)

    def test_receipt_must_name_the_exact_observed_attempts(self):
        self.oversized()
        parts, final = self.finish_parts()
        review = self.record_final(final)
        from research_harness import review_problem_parts
        packets = review_problem_parts.collect_series_packets(self.case.read_records(), self.case.artifacts, final['id'])
        changed = copy.deepcopy(packets)
        last = next(packet for packet in changed if packet[PARTS_KEY]['stage'] == 'final')
        last[PARTS_KEY]['delivered_before'][0]['attempt_id'] = 'not-the-observed-attempt'
        with patch.object(review_problem_parts, 'collect_series_packets', return_value=changed):
            self.assertTrue(self.coverage(review))

    def test_reading_ranges_and_owned_artifacts_remain_required(self):
        self.oversized()
        self.case.add_reading_and_computation()
        parts, final = self.finish_parts()
        review = self.record_final(final)
        self.assertEqual(self.coverage(review), [])
        from research_harness import review_problem_parts
        packets = review_problem_parts.collect_series_packets(self.case.read_records(), self.case.artifacts, final['id'])
        altered = copy.deepcopy(packets)
        for packet in altered:
            packet['problem_evidence']['passages'] = []
            packet[PARTS_KEY]['delivered_now']['passages'] = []
            packet['evidence'] = []
        with patch.object(review_problem_parts, 'collect_series_packets', return_value=altered), \
                patch.object(direction_reviews, 'collect_actual_input_packets', return_value=[]):
            pending = self.coverage(review)
        self.assertEqual(pending[0]['reading_ids'], ['decisive-reading'])
        self.assertTrue(pending[0]['artifact_digests'])
