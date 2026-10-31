"""Bounded result evidence parts retain actual delivery and ordinary final reviews."""

import copy
import importlib
import json
import random
from unittest.mock import patch

from problem_parts_fixtures import KEY, SPLIT_KEYS, ResultPartsCase, indices, note_units
from research_harness import review_protocol as protocol, review_transport as transport
from research_harness import research_decisions as decisions, review_excerpts, review_refusal
from research_harness import review_codex_transport as codex, review_claude_transport as claude
from research_harness.evidence import digest


class ProblemResultPartsTests(ResultPartsCase):
    def test_fitting_packet_prompt_and_request_are_unchanged(self):
        records, artifacts = self.case.read_records(), self.case.artifacts
        with transport.text_only_delivery(True):
            expected = protocol.build_packet(records, artifacts, None, 'result', 'first')
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation')
        assignment = self.assign()
        self.assertEqual(artifacts.read(assignment['packet']), json.dumps(expected, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
        self.assertEqual(assignment['prompt'], prompt)
        self.assertNotIn(KEY, self.packet(assignment))
        self.assertEqual(self.request(assignment), transport.request_body(records['review_route']['route'],
            prompt['text'], expected, assignment['history']))

    def test_oversized_searches_take_the_longest_fitting_prefix_and_empty_final(self):
        self.oversized()
        first = self.assign()
        packet = self.packet(first)
        plan = packet[KEY]
        self.assertEqual((plan['series_assignment_id'], plan['stage'], plan['turn']), (first['id'], 'part', 1))
        self.assertEqual(plan['delivered_before'], [])
        self.assertEqual(set(packet['problem_evidence']), set(self.case.expected))
        delivered = indices(plan['delivered_now']['openness_searches'])
        self.assertEqual(delivered, list(range(len(delivered))))
        self.assertGreater(len(delivered), 0)
        self.assertLess(len(delivered), 6)
        parts = importlib.import_module('research_harness.review_problem_parts')
        units = parts.list_units(self.case.artifacts, self.case.expected)
        next_packet = copy.deepcopy(packet)
        next_packet['problem_evidence']['openness_searches'].append(self.case.expected['openness_searches'][len(delivered)])
        next_packet[KEY]['delivered_now']['openness_searches'] = [[0, len(delivered) + 1]]
        next_packet[KEY]['remaining']['openness_searches'] = [[len(delivered) + 1, 6]]
        next_packet.pop('digest')
        next_packet['digest'] = digest(next_packet)
        larger = transport.request_body(self.case.read_records()['review_route']['route'], first['prompt']['text'],
                                       next_packet, first['history'])
        self.assertGreater(len(codex.packet_text(larger)), transport.CODEX_INPUT_LIMIT_CHARS)
        all_parts, final = self.finish_parts(first)
        for assignment in all_parts + [final]:
            self.assertLessEqual(len(codex.packet_text(self.request(assignment))), transport.CODEX_INPUT_LIMIT_CHARS)
            self.assertTrue(protocol._prompt_matches(assignment))
        final_packet = self.packet(final)
        self.assertTrue(all(final_packet['problem_evidence'][key] == [] for key in SPLIT_KEYS))
        self.assertEqual(final_packet[KEY]['delivered_now'], {key: [] for key in SPLIT_KEYS})
        self.assertEqual(final_packet[KEY]['remaining'], {key: [] for key in SPLIT_KEYS})
        self.assertEqual([row['assignment_id'] for row in final_packet[KEY]['delivered_before']],
                         [assignment['id'] for assignment in all_parts])
        review = self.record_final(final)
        self.assertEqual(self.coverage(review), [])
        for assignment in all_parts + [final]:
            records = self.case.read_records()
            attempt = records['review_attempt']['attempt-' + assignment['id']]
            self.assertTrue(protocol.authenticate_attempt(records, self.case.artifacts, assignment, attempt))
        self.assertEqual(len(units), 6)

    def test_units_include_owned_bodies_and_never_split_a_passage(self):
        parts = importlib.import_module('research_harness.review_problem_parts')
        self.case.add_reading_and_computation()
        evidence = copy.deepcopy(self.case.expected)
        evidence['openness_searches'] = [{'search': {'id': 's'}, 'sources': []}]
        units = parts.list_units(self.case.artifacts, evidence)
        self.assertEqual([(row['key'], row['index']) for row in units],
                         [('openness_searches', 0), ('readings', 0)]
                         + [('passages', i) for i in range(len(evidence['passages']))]
                         + [('computations', 0), ('formal_checks', 0)])
        computation = next(row for row in units if row['key'] == 'computations')
        self.assertEqual({body['sha256'] for body in computation['artifacts']},
                         {ref['sha256'] for ref in protocol.collect_references(evidence['computations'])})
        self.assertGreater(computation['characters'], len(json.dumps(computation['item'], sort_keys=True)))
        for row in units:
            self.assertEqual(row['digest'], digest(row['item']))

    def test_unit_cap_and_reading_context_on_a_later_passage_turn(self):
        parts = importlib.import_module('research_harness.review_problem_parts')
        self.oversized(count=1, characters=500, limit=70000)
        self.case.add_reading_and_computation()
        reading = self.case.expected['readings'][0]
        self.case.expected['passages'] = [dict(reading_id=reading['id'], inspection_index=i,
            version_id=reading['version_id'], start=i * 18000, end=(i + 1) * 18000, text='p' * 18000)
            for i in range(5)]
        with patch.object(parts, 'PART_UNIT_CHARACTERS', 25000):
            first = self.assign()
            turns, final = self.finish_parts(first)
        later = [self.packet(a) for a in turns[1:] if self.packet(a)['problem_evidence']['passages']]
        self.assertTrue(later)
        for packet in later:
            self.assertEqual(packet['problem_evidence']['readings'], [reading])
            self.assertEqual(packet[KEY]['delivered_now']['readings'], [[0, 1]])
            self.assertTrue(all(len(row['text']) == 18000 for row in packet['problem_evidence']['passages']))
            self.assertIn({'key': 'readings', 'index': 0}, note_units(packet))
            self.assertEqual(sum(unit['key'] == 'readings' for unit in note_units(packet)), 1)
        self.assertEqual(self.packet(final)[KEY]['stage'], 'final')
        self.assertTrue(parts.covers_items([self.packet(turn) for turn in turns], self.case.expected, 'readings'))
        self.assertTrue(parts.covers_items([self.packet(turn) for turn in turns], self.case.expected, 'passages'))

    def test_frame_and_indivisible_unit_refusals_are_assignment_time_and_atomic(self):
        self.oversized(count=1, characters=150000, limit=80000)
        revision = self.case.store.revision
        error = self.assert_code('review_input_too_large', self.assign)
        self.assertEqual(self.case.store.revision, revision)
        self.assertEqual(set(('stage', 'turn', 'input_characters', 'input_limit_characters', 'frame_characters', 'unit'))
                         - set(error.details), set())
        self.assertEqual(error.details['unit']['key'], 'openness_searches')
        self.case.expected['openness_searches'] = []
        self.case.expected['claim']['statement'] = 'large frame ' * 16000
        error = self.assert_code('review_input_too_large', self.assign)
        self.assertGreater(error.details['frame_characters'], error.details['input_limit_characters'])
        self.assertEqual(self.case.store.revision, revision)

    def test_owned_body_elision_shrinks_the_prefix(self):
        self.oversized(count=1, characters=17000, limit=110000)
        body = self.case.artifacts.put(b'computation output\n' * 1800, 'text/plain')
        self.case.expected['computations'] = [dict(execution_id='execution-' + str(i),
            execution={'id': 'execution-' + str(i), 'outputs': [{'id': 'output', 'artifact': body}]},
            assessment={'reason': 'c' * 18000}) for i in range(5)]
        parts, final = self.finish_parts()
        from research_harness.direction_sources import has_attachment
        found = []
        for assignment in parts:
            sent = review_excerpts.read_sent_packet(self.request(assignment))
            rows = sent['problem_evidence']['computations']
            if rows:
                found.extend(rows)
                self.assertTrue(has_attachment(self.case.artifacts, sent, body))
        self.assertEqual(found, self.case.expected['computations'])
        self.assertEqual(self.coverage(self.record_final(final)), [])

    def test_malformed_part_is_refused_on_run_and_retry_is_bounded(self):
        self.oversized()
        first = self.assign()
        malformed = dict(self.output(first), notes=[])
        receipt = self.invoke(first, malformed)
        refusal = self.case.read_records()['review_output_refusal']['attempt-' + first['id']]
        self.assertEqual((refusal['code'], refusal['check']), ('invalid_value_review', 'review.shape'))
        self.assertEqual(receipt['output_refusal']['check'], 'review.shape')
        self.assert_code('review_source_continuation_unverified', lambda: self.assign('next', first['id']))
        parts = importlib.import_module('research_harness.review_problem_parts')
        self.assertEqual(parts.collect_series_packets(self.case.read_records(), self.case.artifacts, first['id']), [])
        self.invoke(first, attempt='retry-valid')
        state = protocol.assignment_state(self.case.read_records(), self.case.artifacts, first['id'])
        self.assertTrue(state['ready'], state['obligations'])
        successor = self.assign('next', first['id'])
        self.assertEqual(self.packet(successor)[KEY]['delivered_before'][0]['attempt_id'], 'retry-valid')

    def test_two_refused_outputs_exhaust_the_part_assignment(self):
        self.oversized()
        first = self.assign()
        self.invoke(first, dict(self.output(first), notes=[]))
        self.invoke(first, dict(self.output(first), notes=[]), attempt='retry-invalid')
        self.assert_code('review_slot_exhausted', lambda: self.invoke(first, attempt='third'))

    def test_notes_exact_shape_nonblank_length_and_no_source_requests(self):
        self.oversized()
        first = self.assign()
        valid = self.output(first)
        parts = importlib.import_module('research_harness.review_problem_parts')
        packet = self.packet(first)
        parts.validate_part_output(packet, valid)
        self.assertTrue(all(set(entry['unit']) == {'key', 'index'} for entry in valid['notes']))
        bad_note = copy.deepcopy(valid['notes'])
        bad_note[0]['note'] = '  \n'
        wrong_unit = copy.deepcopy(valid['notes'])
        wrong_unit[0]['unit']['index'] = 999
        cases = [dict(valid, notes=[]), dict(valid, notes={}), dict(valid, notes=bad_note), dict(valid, notes=wrong_unit),
                 dict(valid, part=True), dict(valid, part=2), dict(valid, phase='final'),
                 dict(valid, source_requests=[]), dict(valid, extra=True), dict(valid, excerpts=[{}])]
        long_notes = copy.deepcopy(valid['notes'])
        long_notes[0]['note'] = 'n' * 60001
        cases.append(dict(valid, notes=long_notes))
        for unit in (dict(valid['notes'][0]['unit'], digest='0' * 64),
                     dict(valid['notes'][0]['unit'], extra=True),
                     {'key': 'not-a-split-key', 'index': 0}, {'key': 'openness_searches'},
                     {'key': [], 'index': 0},
                     {'key': 'openness_searches', 'index': 0.0}, {'key': 'openness_searches', 'index': '0'}):
            notes = copy.deepcopy(valid['notes'])
            notes[0]['unit'] = unit
            cases.append(dict(valid, notes=notes))
        for output in cases:
            with self.subTest(output_keys=list(output)):
                error = self.assert_code('invalid_value_review',
                    lambda: decisions.validate_review_contract(self.case.read_records(), self.case.artifacts, first, output))
                self.assertEqual(error.contract_check, 'review.shape')

    def test_notes_accept_sorted_key_and_shuffled_orders(self):
        from research_harness import review_problem_parts
        self.oversized(count=1, characters=500)
        self.case.add_reading_and_computation()
        self.case.expected['formal_checks'] = [dict(id='formal-' + str(index), reason='f' * 40000)
                                              for index in range(3)]
        assignment = self.assign()
        packet, output = self.packet(assignment), self.output(assignment)
        notes = output['notes']
        self.assertGreaterEqual(len({entry['unit']['key'] for entry in notes}), 3)
        alphabetical = sorted(notes, key=lambda entry: (entry['unit']['key'], entry['unit']['index']))
        self.assertNotEqual(alphabetical, notes)
        orders = [alphabetical, list(reversed(notes))]
        randomizer = random.Random(69)
        for _ in range(8):
            shuffled = copy.deepcopy(notes)
            randomizer.shuffle(shuffled)
            orders.append(shuffled)
        for order in orders:
            with self.subTest(units=[entry['unit'] for entry in order]):
                review_problem_parts.validate_part_output(packet, dict(output, notes=order))
        passage = {'key': 'passages', 'index': indices(packet[KEY]['delivered_now']['passages'])[0]}
        error = self.assert_code('invalid_value_review', lambda: review_problem_parts.validate_part_output(
            packet, dict(output, notes=notes + [dict(notes[0], unit=passage)])))
        self.assertEqual(error.details, {'path': '/notes/' + str(len(notes)) + '/unit',
                                        'reason': 'unknown_unit', 'unit': passage})
        self.invoke(assignment, dict(output, notes=alphabetical))
        records = self.case.read_records()
        self.assertTrue(protocol.assignment_state(records, self.case.artifacts, assignment['id'])['ready'])
        self.assertEqual(len(review_problem_parts.collect_series_packets(records, self.case.artifacts, assignment['id'])), 1)

    def test_duplicate_missing_and_extra_notes_identify_the_unit(self):
        self.oversized()
        assignment = self.assign()
        output = self.output(assignment)
        notes = output['notes']
        self.assertGreaterEqual(len(notes), 2)
        unknown = {'key': notes[0]['unit']['key'], 'index': 999}
        extra = dict(notes[0], unit=unknown)
        cases = [(notes + [notes[0]], '/notes/' + str(len(notes)) + '/unit', 'duplicate_unit', notes[0]['unit']),
                 ([notes[0], notes[0]] + notes[2:], '/notes/1/unit', 'duplicate_unit', notes[0]['unit']),
                 (notes + [extra], '/notes/' + str(len(notes)) + '/unit', 'unknown_unit', unknown),
                 ([extra] + notes[1:], '/notes/0/unit', 'unknown_unit', unknown)]
        for entries, path, reason, unit in cases:
            with self.subTest(path=path, reason=reason):
                error = self.assert_code('invalid_value_review', lambda: decisions.validate_review_contract(
                    self.case.read_records(), self.case.artifacts, assignment, dict(output, notes=entries)))
                self.assertEqual(error.contract_check, 'review.shape')
                self.assertEqual(error.details['path'], path)
                self.assertEqual(error.details['reason'], reason)
                self.assertEqual(error.details['unit'], unit)
                if reason == 'duplicate_unit':
                    self.assertEqual(error.details['duplicate_of'], '/notes/0/unit')
        for omitted in ([0], [1], list(range(len(notes)))):
            remaining = [entry for index, entry in enumerate(notes) if index not in omitted]
            with self.subTest(omitted=omitted):
                error = self.assert_code('invalid_value_review', lambda: decisions.validate_review_contract(
                    self.case.read_records(), self.case.artifacts, assignment, dict(output, notes=list(reversed(remaining)))))
                self.assertEqual(error.details['path'], '/notes')
                self.assertEqual(error.details['missing_unit'], notes[min(omitted)]['unit'])
                self.assertEqual(error.contract_check, 'review.shape')

    def test_note_paths_and_total_character_limit(self):
        from research_harness import review_problem_parts
        self.oversized()
        assignment = self.assign()
        output = self.output(assignment)
        notes = output['notes']
        notes[0]['note'] = 'n' * (review_problem_parts.NOTES_CHARACTERS - sum(len(entry['note']) for entry in notes[1:]))
        packet = self.packet(assignment)
        review_problem_parts.validate_part_output(packet, dict(output, notes=list(reversed(notes))))
        notes[-1]['note'] += 'x'
        error = self.assert_code('invalid_value_review', lambda: review_problem_parts.validate_part_output(packet, output))
        self.assertEqual(error.details, {'path': '/notes', 'characters': 60001, 'maximum_characters': 60000})
        for value in ('  \n', None, 0):
            notes[-1]['note'] = value
            error = self.assert_code('invalid_value_review', lambda: review_problem_parts.validate_part_output(packet, output))
            self.assertEqual(error.details['path'], '/notes/' + str(len(notes) - 1) + '/note')

    def test_valid_part_cannot_be_recorded_and_pending_obligation_moves_to_leaf(self):
        from research_harness.problem_delivery import assess_source_requests
        self.oversized()
        first = self.assign()
        self.invoke(first)
        self.assertNotIn('review_output_refusal', self.case.read_records())
        payload = dict(self.output(first), id='not-a-review', dossier_id=None, assignment_id=first['id'])
        revision = self.case.store.revision
        self.assert_code('problem_evidence_part_pending', lambda: self.case.mutate(decisions.record_value_review, payload))
        self.assertEqual(self.case.store.revision, revision)
        pending = assess_source_requests(self.case.read_records(), self.case.artifacts)
        self.assertEqual([(row['code'], row['assignment_id']) for row in pending],
                         [('problem_evidence_part_pending', first['id'])])
        successor = self.assign('part-2', first['id'])
        self.assertEqual(assess_source_requests(self.case.read_records(), self.case.artifacts), [])
        self.invoke(successor)
        pending = assess_source_requests(self.case.read_records(), self.case.artifacts)
        self.assertEqual([row['assignment_id'] for row in pending], [successor['id']])

    def test_final_source_request_continuation_has_no_list_items(self):
        self.oversized()
        self.case.add_reading_and_computation()
        _, final = self.finish_parts()
        output = self.final_output()
        output['value']['status'] = 'unresolved'
        output['source_requests'] = [{'id': 'read-source', 'kind': 'source', 'depth': 'fulltext',
                                      'version_id': self.case.expected['readings'][0]['version_id']}]
        self.invoke(final, output)
        continued = self.assign('source-continuation', final['id'])
        packet = self.packet(continued)
        self.assertEqual(packet[KEY]['stage'], 'final')
        self.assertTrue(all(packet['problem_evidence'][key] == [] for key in SPLIT_KEYS))
        self.assertEqual(packet[KEY]['delivered_now'], {key: [] for key in SPLIT_KEYS})
        self.assertEqual(packet[KEY]['remaining'], {key: [] for key in SPLIT_KEYS})
        self.assertTrue(packet['source_deliveries'])
        self.assertEqual(packet['pending_source_requests'], [])
        self.assertEqual(self.coverage(self.record_final(continued)), [])

    def test_verbatim_inline_passage_excerpt_is_verified_in_the_next_turn(self):
        from research_harness.direction_sources import build_inline_passages
        self.oversized()
        self.case.add_reading_and_computation()
        reading = self.case.read_records()['reading']['decisive-reading']
        self.case.expected['passages'] = [passage for passage, _ in build_inline_passages(
            self.case.read_records(), self.case.artifacts, [reading])]
        assignment = self.assign()
        while not any(len(passage['text']) >= review_excerpts.MINIMUM_PHRASE_CHARACTERS
                      for passage in self.packet(assignment)['problem_evidence']['passages']):
            self.invoke(assignment)
            assignment = self.assign('excerpt-turn-' + str(self.packet(assignment)[KEY]['turn'] + 1),
                                     assignment['id'])
        packet = self.packet(assignment)
        passage = next(passage for passage in packet['problem_evidence']['passages']
                       if len(passage['text']) >= review_excerpts.MINIMUM_PHRASE_CHARACTERS)
        self.assertIn(passage, review_excerpts.read_sent_packet(self.request(assignment))['problem_evidence']['passages'])
        output = self.output(assignment)
        output['excerpts'] = [{'version_id': passage['version_id'], 'text': passage['text'],
                               'purpose': 'Retain the inspected source for the final assessment.'}]
        self.invoke(assignment, output)
        continued = self.assign('excerpt-continued', assignment['id'])
        excerpts = self.packet(continued)[review_excerpts.KEY]['excerpts']
        self.assertEqual(len(excerpts), 1)
        excerpt = excerpts[0]
        self.assertEqual((excerpt['status'], excerpt['text']), ('verified', passage['text']))
        reference = review_excerpts.find_source_text(self.case.read_records(), passage['version_id'])
        self.assertEqual(excerpt['artifact_descriptor']['sha256'], reference['sha256'])
        source = self.case.artifacts.read(reference).decode('utf-8')
        self.assertEqual(source[excerpt['locator']['start']:excerpt['locator']['end']], passage['text'])

    def test_unrefused_invalid_part_has_no_credit_and_cannot_continue(self):
        from research_harness import review_problem_parts
        self.oversized()
        first = self.assign()
        with patch.object(review_refusal, 'record_native_refusal'):
            self.invoke(first, dict(self.output(first), notes=[]))
        self.assertNotIn('review_output_refusal', self.case.read_records())
        self.assertEqual(review_problem_parts.collect_series_packets(self.case.read_records(), self.case.artifacts,
                                                                     first['id']), [])
        self.assert_code('invalid_value_review', lambda: self.assign('invalid-successor', first['id']))

    def test_a_tampered_actual_request_gives_no_series_delivery_credit(self):
        from research_harness import review_problem_parts
        self.oversized()
        first = self.assign()
        self.invoke(first)
        records = self.case.read_records()
        attempt = records['review_attempt']['attempt-' + first['id']]
        request = json.loads(self.case.artifacts.read(attempt['request']))
        request['input'][-1]['content'] = 'Substituted input with no evidence.'
        attempt['request'] = self.case.artifacts.put(json.dumps(request).encode(), 'application/json')
        self.assertEqual(review_problem_parts.collect_series_packets(records, self.case.artifacts, first['id']), [])

    def test_owned_assessment_artifact_alone_cannot_exceed_the_unit_cap(self):
        from research_harness import review_problem_parts
        self.oversized(count=0, limit=150000)
        artifact = self.case.artifacts.put(b'assessment ' * 8000, 'text/plain')
        self.case.expected['assessment_evidence'] = [artifact, {'kind': 'record', 'record_kind': 'problem_value',
            'id': 'large-assessment-record', 'digest': '1' * 64, 'record': {'reason': 'x' * 200000}}]
        with patch.object(review_problem_parts, 'PART_UNIT_CHARACTERS', 10000):
            error = self.assert_code('review_input_too_large', self.assign)
        self.assertEqual(error.details['unit']['key'], 'assessment_evidence')

    def test_note_unit_indices_are_integers_not_booleans(self):
        self.oversized()
        first = self.assign()
        output = self.output(first)
        output['notes'][0]['unit']['index'] = False
        self.assert_code('invalid_value_review', lambda: decisions.validate_review_contract(
            self.case.read_records(), self.case.artifacts, first, output))

    def test_changed_digest_restarts_but_inherited_refresh_marker_does_not(self):
        self.oversized()
        first = self.assign()
        self.invoke(first)
        self.case.expected['openness_searches'][0]['search']['reason'] += ' new evidence'
        restarted = self.assign('restarted', first['id'])
        self.assertEqual(self.packet(restarted)[KEY]['series_assignment_id'], 'restarted')
        self.assertEqual(self.packet(restarted)[KEY]['turn'], 1)
        self.assertEqual(self.packet(restarted)[KEY]['delivered_before'], [])
        self.invoke(restarted)
        real = protocol.build_packet
        def inherited(*args, **kwargs):
            packet = real(*args, **kwargs)
            packet['problem_refresh'] = {'assignment_id': 'older-recorded-final'}
            return packet
        with patch.object(protocol, 'build_packet', side_effect=inherited):
            successor = self.assign('continued', restarted['id'])
        self.assertEqual(self.packet(successor)[KEY]['series_assignment_id'], 'restarted')
        self.assertEqual(self.packet(successor)[KEY]['turn'], 2)

    def test_refresh_after_recorded_final_starts_fresh_delivery(self):
        self.oversized()
        _, final = self.finish_parts()
        self.record_final(final)
        self.case.expected['value']['motivation'][0]['text'] += ' changed'
        successor = self.assign('refresh')
        self.assertEqual(self.packet(successor)[KEY]['series_assignment_id'], 'refresh')
        self.assertEqual(self.packet(successor)[KEY]['delivered_before'], [])
        self.invoke(successor)
        next_turn = self.assign('refresh-part-2', successor['id'])
        self.assertEqual(self.packet(next_turn)[KEY]['series_assignment_id'], 'refresh')

    def test_changed_series_digest_starts_fresh_even_when_new_evidence_is_small(self):
        self.oversized()
        first = self.assign()
        self.invoke(first)
        self.case.expected['openness_searches'] = [{'search': {'id': 'smaller-search', 'reason': 'New evidence.'},
                                                   'sources': []}]
        restarted = self.assign('smaller-series', first['id'])
        self.assertEqual(self.packet(restarted)[KEY]['series_assignment_id'], 'smaller-series')
        self.assertEqual(self.packet(restarted)[KEY]['stage'], 'part')
        self.assertEqual(self.packet(restarted)[KEY]['delivered_before'], [])

    def test_stage_prompts_match_every_supported_excerpt_and_common_version(self):
        for stage in ('part', 'final'):
            for rules in review_excerpts.PHASE_PROMPTS:
                for common in (1, protocol.COMMON_VERSION):
                    prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                        evidence_parts=stage, excerpt_rules=rules, common_version=common)
                    assignment = {'role': 'result', 'subject': {'kind': 'problem'}, 'context': {}, 'prompt': prompt}
                    self.assertEqual(prompt['parameters']['evidence_parts'], 'exactory-problem-evidence-parts-v1/' + stage)
                    self.assertEqual(prompt['template_id'], 'exactory-problem-review-v5/result')
                    self.assertTrue(protocol._prompt_matches(assignment))
                    bad = copy.deepcopy(assignment)
                    bad['prompt']['parameters']['evidence_parts'] = 'exactory-problem-evidence-parts-v1/other'
                    self.assertFalse(protocol._prompt_matches(bad))
        self.assert_code('invalid_review_context', lambda: protocol.canonical_prompt('result',
            orchestration='problem_creation', evidence_parts='part'))

    def test_claude_planner_measures_the_claude_packet_text(self):
        self.oversized()
        parts = importlib.import_module('research_harness.review_problem_parts')
        records = self.case.read_records()
        route = dict(records['review_route']['route'], adapter='claude_cli_v1', endpoint='claude://local',
                     configuration={'effort': 'max', 'max_output_tokens': 1000, 'timeout_seconds': 5})
        packet = protocol.build_packet(records, self.case.artifacts, None, 'result', 'first')
        value = {'id': 'claude-part', 'role': 'result', 'context': {}, 'subject': packet['subject']}
        prompt = protocol.canonical_prompt('result', orchestration='problem_creation')
        with patch.object(claude, 'INPUT_LIMIT_CHARACTERS', 90000):
            planned = parts.prepare_packet(records, self.case.artifacts, value, route, packet, prompt, [])
            stage_prompt = protocol.canonical_prompt('result', orchestration='problem_creation', excerpts=True,
                                                       evidence_parts=planned[KEY]['stage'])
            request = transport.request_body(route, stage_prompt['text'], planned)
            self.assertLessEqual(len(claude.packet_text(request)), 90000)
            self.assertEqual(planned[KEY]['stage'], 'part')
