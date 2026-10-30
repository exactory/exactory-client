"""A reading turn carries at most one budget of new full text; longer texts arrive in parts over consecutive turns."""

import json
import random
import unittest
from unittest.mock import patch

from openness_reading_fixtures import OpennessReadingCase
from research_harness import openness_reading, review_excerpts, review_protocol as protocol
from research_harness import review_codex_transport as codex, review_transport as transport

# The new full text that one turn delivers at most (review_excerpts.TURN_FULLTEXT_CHARACTERS).
BUDGET = 120000
# The extracted full texts of the eight readings of the KMP record's openness packet (2026-10-27).
KMP_SIZES = (291855, 195429, 63236, 304336, 101335, 243326, 185115, 213686)


def fail_transport(route, request, credential):
    raise ValueError('The reviewer process was stopped at the route timeout.')


class PartSplitTests(unittest.TestCase):
    def test_a_turn_reads_at_most_120000_characters_of_new_full_text(self):
        self.assertEqual(review_excerpts.TURN_FULLTEXT_CHARACTERS, BUDGET)

    def test_a_text_within_the_budget_is_one_part(self):
        for text in ('', 'Short text.', 'x' * BUDGET):
            with self.subTest(characters=len(text)):
                self.assertEqual(review_excerpts.compute_part_spans(text), [(0, len(text))])

    def test_a_part_ends_after_the_last_blank_line_in_the_last_quarter_of_its_window(self):
        text = 'a' * 100000 + '\n\n' + 'b' * 9998 + '\n\n' + 'c' * 9000 + '\nd' + 'e' * 50000
        self.assertEqual(review_excerpts.compute_part_spans(text), [(0, 110002), (110002, len(text))])

    def test_without_a_blank_line_a_part_ends_after_the_last_newline_there(self):
        text = 'a' * 50000 + '\n\n' + 'b' * 50000 + '\n' + 'c' * 5000 + '\n' + 'd' * 30000
        self.assertEqual(review_excerpts.compute_part_spans(text), [(0, 105004), (105004, len(text))])

    def test_without_a_newline_a_part_ends_exactly_at_the_budget(self):
        text = 'a' * 89000 + '\n\n' + 'e' * 200000
        self.assertEqual(review_excerpts.compute_part_spans(text),
                         [(0, BUDGET), (BUDGET, 2 * BUDGET), (2 * BUDGET, len(text))])

    def test_the_parts_cover_the_whole_text_in_order(self):
        generator = random.Random(20261028)
        paragraphs = []
        while sum(map(len, paragraphs)) < 700000:
            line = ' '.join('w%d' % generator.randrange(1000) for _ in range(generator.randrange(5, 400)))
            paragraphs.append(line + generator.choice(('\n', '\n\n', ' ')))
        text = ''.join(paragraphs)
        spans = review_excerpts.compute_part_spans(text)
        self.assertEqual(spans, review_excerpts.compute_part_spans(text))
        self.assertEqual(''.join(text[start:end] for start, end in spans), text)
        self.assertEqual([start for start, _ in spans[1:]], [end for _, end in spans[:-1]])
        for start, end in spans[:-1]:
            self.assertGreater(end - start, BUDGET - BUDGET // 4)
            self.assertLessEqual(end - start, BUDGET)
        self.assertLessEqual(spans[-1][1] - spans[-1][0], BUDGET)


class KmpSizedChainTests(OpennessReadingCase):
    text_sizes = KMP_SIZES

    def test_a_chain_with_the_kmp_text_sizes_reads_every_part_within_the_budget(self):
        from research_harness.problem_openness import assess_openness
        native_packet = self.export(priority=self.versions)
        turns = self.run_reading_turns(native_packet, turn_count=25)
        seen = {}
        for identifier in turns[:-1]:
            plan = self.read_packet(identifier)['reading_turn']['reading_plan']
            self.assertLessEqual(sum(row['characters'] for row in plan['delivered_now']), BUDGET)
            request, sent = self.read_sent_packet(identifier)
            self.assertLessEqual(len(codex.packet_text(request)), transport.CODEX_INPUT_LIMIT_CHARS)
            complete = review_excerpts.find_complete_bodies(sent)
            for row in plan['delivered_now']:
                self.assertIn(row['artifact']['sha256'], complete)
                if row['kind'] != 'fulltext':
                    continue
                body = self.artifacts.read(self.texts[row['version_id']]).decode()
                self.assertEqual(self.artifacts.read(row['artifact']).decode(), body[row['start']:row['end']])
                self.assertEqual(row['characters'], row['end'] - row['start'])
                seen.setdefault(row['version_id'], []).append((row['part'], row['parts'], row['start'], row['end']))
        for version, size in zip(self.versions, KMP_SIZES):
            with self.subTest(version=version):
                parts = seen[version]
                body = self.artifacts.read(self.texts[version]).decode()
                self.assertEqual(parts, [(index, len(parts), start, end) for index, (start, end)
                                         in enumerate(review_excerpts.compute_part_spans(body), 1)])
                self.assertEqual((parts[0][2], parts[-1][3]), (0, size))
                self.assertEqual(len(parts) == 1, size <= BUDGET)
        final = turns[-1]
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        saved = self.mutate(self.load_problem_module().record_openness, {'assignment_id': final})['result']
        self.assertEqual([row['assignment_id'] for row in saved['reading_turns']], turns)
        self.assertEqual(sorted({entry['version_id'] for row in saved['reading_turns']
                                 for entry in row.get('delivered_parts', []) if entry['kind'] == 'fulltext'}),
                         sorted(version for version, size in zip(self.versions, KMP_SIZES) if size > BUDGET))
        self.assertEqual(assess_openness(self.store.snapshot()['records'], self.artifacts), [])


class ReadingBudgetChainTests(OpennessReadingCase):
    """Three texts of 8,476 characters with a budget of 5,000 characters: each text arrives in two parts. The input
    limit keeps the three texts out of one input, and the final turn holds the record of six reading turns."""

    def setUp(self):
        super().setUp()
        self.limit_input(40000)
        budget = patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 5000)
        budget.start()
        self.addCleanup(budget.stop)

    def record_openness(self, identifier):
        return self.mutate(self.load_problem_module().record_openness, {'assignment_id': identifier})['result']

    def test_a_long_text_arrives_in_parts_over_consecutive_turns(self):
        turns = self.run_reading_turns(self.export(priority=self.versions), turn_count=12)
        # The last part of the third text leaves room for the first record in the same turn.
        delivered = [[(row['version_id'], row['part'], row['parts'], row['start'], row['end'])
                      for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']
                      if row['kind'] == 'fulltext'] for identifier in turns[:6]]
        self.assertEqual(delivered, [[(version, part, 2, start, end)] for version in self.versions
                                     for part, (start, end) in ((1, (0, 5000)), (2, (5000, 8476)))])
        self.assertTrue(all(row['kind'] == 'record' for identifier in turns[6:-1]
                            for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']))
        parts = [[dict(self.unit_identity(version), part=part, parts=2, start=start, end=end, characters=end - start)]
                 for version in self.versions for part, start, end in ((1, 0, 5000), (2, 5000, 8476))]
        record = self.read_packet(turns[-1])['reading_record']
        self.assertEqual([row['delivered_parts'] for row in record['turns'][:6]], parts)
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        saved = self.record_openness(turns[-1])
        self.assertEqual([row['delivered_parts'] for row in saved['reading_turns'][:6]], parts)

    def test_a_text_counts_as_read_only_when_every_part_reached_a_reading_turn(self):
        real = openness_reading.list_reading_units

        def drop_the_last_part(artifacts, rows):
            """The units of the rows without the last part of the third text, which no turn then delivers."""
            return [unit for unit in real(artifacts, rows)
                    if not (unit.get('version_id') == self.versions[2] and unit['part'] == unit['parts'])]
        with patch.object(openness_reading, 'list_reading_units', side_effect=drop_the_last_part), \
                self.omit_final_body(self.texts[self.versions[2]]['sha256']):
            final = self.run_reading_turns(self.export(priority=self.versions), turn_count=12)[-1]
        self.invoke(final, self.build_native_result(self.read_packet(final)))
        with self.assertRaises(Exception) as raised:
            self.record_openness(final)
        self.assertEqual((raised.exception.code, raised.exception.details['version_ids']),
                         ('openness_reading_incomplete', [self.versions[2]]))
        self.assertEqual(raised.exception.details['units'],
                         [dict(self.unit_identity(self.versions[2]), missing_parts=[2])])

    def test_a_failed_reading_turn_is_replaced_and_can_no_longer_run_or_continue(self):
        native_packet = self.export(priority=self.versions)
        self.assign('turn-1', native_packet)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        self.assign('turn-2', native_packet, prior='turn-1')
        with patch.object(transport, 'send_request', side_effect=fail_transport):
            self.mutate(protocol.invoke_assignment, {'id': 'attempt-turn-2', 'assignment_id': 'turn-2'},
                        credential='test-only')
        records = self.store.snapshot()['records']
        self.assertEqual(records['review_attempt']['attempt-turn-2']['status'], 'failed')
        replacement = self.assign('turn-2b', native_packet, prior='turn-1')
        self.assertEqual(replacement['reading_chain'], {'first_assignment_id': 'turn-1', 'turn': 2})
        self.assertEqual(self.read_packet('turn-2b')['reading_turn']['reading_plan']['delivered_now'],
                         self.read_packet('turn-2')['reading_turn']['reading_plan']['delivered_now'])
        self.assert_error('review_assignment_replaced', lambda: self.mutate(
            protocol.invoke_assignment, {'id': 'attempt-turn-2-again', 'assignment_id': 'turn-2'},
            credential='test-only'))
        self.assert_error('review_assignment_replaced', lambda: self.assign('turn-3', native_packet, prior='turn-2'))
        self.invoke('turn-2b', self.build_reading_response(self.read_packet('turn-2b')))
        self.assign('turn-3', native_packet, prior='turn-2b')
        self.assertIn('turn-2', self.store.snapshot()['records']['review_assignment'])

    def test_a_turn_that_ran_or_never_ran_still_blocks_a_second_continuation(self):
        native_packet = self.export(priority=self.versions)
        self.assign('turn-1', native_packet)
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        self.assign('turn-2', native_packet, prior='turn-1')
        self.assert_error('review_source_continuation_fork',
                          lambda: self.assign('turn-2b', native_packet, prior='turn-1'))
        self.invoke('turn-2', self.build_reading_response(self.read_packet('turn-2')))
        self.assert_error('review_source_continuation_fork',
                          lambda: self.assign('turn-2b', native_packet, prior='turn-1'))

    def test_a_chain_started_by_0_64_2_continues_under_the_budget(self):
        native_packet = self.export(priority=self.versions)
        real_prompt, real_assemble = protocol.canonical_prompt, openness_reading._assemble_reading_turn

        def build_earlier_prompt(role, **options):
            if options.get('reading_stage') is not None:
                options['reading_protocol'] = openness_reading.PROTOCOL_V1
            return real_prompt(role, **options)

        def assemble_as_0_64_2(artifacts, packet, record, delivered, now, remaining, turn):
            """The turn 1 of 0.64.2: two whole texts, with plan entries that have no part fields."""
            value = real_assemble(artifacts, packet, record, delivered, now, remaining, turn)
            plan = value['reading_turn']['reading_plan']
            plan['delivered_now'] = [{key: row[key] for key in ('version_id', 'characters', 'artifact')}
                                     for row in plan['delivered_now']]
            plan['remaining'] = [{key: row[key] for key in ('version_id', 'characters', 'artifact_descriptor')}
                                 for row in plan['remaining']]
            value['reading_turn']['protocol'] = value['reading_turn']['reading_record']['protocol'] = \
                openness_reading.PROTOCOL_V1
            value.pop('digest')
            value['evidence'] = protocol._evidence(artifacts, value)
            value['digest'] = protocol.digest(value)
            return value

        def list_0_64_2_units(artifacts, texts):
            """0.64.2 delivered every full text whole, and no record."""
            return [{'version_id': row['version_id'], 'part': 1, 'parts': 1, 'start': 0,
                     'end': len(artifacts.read(row['artifact']).decode('utf-8')),
                     'characters': len(artifacts.read(row['artifact']).decode('utf-8')),
                     'artifact_descriptor': openness_reading.descriptor(row['artifact'])}
                    for row in texts if 'version_id' in row]
        large = patch.object(review_excerpts, 'TURN_FULLTEXT_CHARACTERS', 20000)
        with patch.object(protocol, 'canonical_prompt', side_effect=build_earlier_prompt), \
                patch.object(openness_reading, '_assemble_reading_turn', side_effect=assemble_as_0_64_2), \
                patch.object(openness_reading, 'list_reading_units', side_effect=list_0_64_2_units), large:
            earlier = self.assign('turn-1', native_packet)
        self.assertEqual(earlier['prompt']['template_id'], openness_reading.PROTOCOL_V1 + '/reading')
        plan = self.read_packet('turn-1')['reading_turn']['reading_plan']
        self.assertEqual([row['version_id'] for row in plan['delivered_now']], self.versions[:2])
        self.assertEqual([sorted(row) for row in plan['remaining']],
                         [['artifact_descriptor', 'characters', 'version_id']])
        self.invoke('turn-1', self.build_reading_response(self.read_packet('turn-1')))
        records = self.store.snapshot()['records']
        state = protocol.assignment_state(records, self.artifacts, 'turn-1')
        self.assertTrue(state['ready'], state['obligations'])
        turns = ['turn-1']
        while True:
            turns.append('turn-%d' % (len(turns) + 1))
            assignment = self.assign(turns[-1], native_packet, prior=turns[-2])
            if 'reading_turn' not in self.read_packet(turns[-1]):
                break
            self.assertEqual(assignment['prompt'], protocol.canonical_prompt('native_math', reading_stage='reading'))
            self.invoke(turns[-1], self.build_reading_response(self.read_packet(turns[-1])))
            self.assertLessEqual(len(turns), 8)
        self.assertEqual([[(row['version_id'], row['part'], row['parts']) for row in
                           self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']
                           if row['kind'] == 'fulltext'] for identifier in turns[1:3]],
                         [[(self.versions[2], 1, 2)], [(self.versions[2], 2, 2)]])
        self.assertTrue(all(row['kind'] == 'record' for identifier in turns[3:-1]
                            for row in self.read_packet(identifier)['reading_turn']['reading_plan']['delivered_now']))
        self.invoke(turns[-1], self.build_native_result(self.read_packet(turns[-1])))
        saved = self.record_openness(turns[-1])
        self.assertEqual(saved['reading_turns'][0]['delivered_units'],
                         [self.unit_identity(version) for version in self.versions[:2]])
        self.assertEqual([row['delivered_parts'] for row in saved['reading_turns'][1:3]],
                         [[dict(self.unit_identity(self.versions[2]), part=part, parts=2, start=start, end=end,
                                characters=end - start)] for part, start, end in ((1, 0, 5000), (2, 5000, 8476))])


class EarlierPromptTests(unittest.TestCase):
    """The prompts of 0.64.2 to 0.65.1 stay available, so their assignments keep authenticating."""

    def test_the_earlier_reading_and_excerpt_prompts_keep_their_exact_bytes(self):
        earlier = {('native_math', 'reading'): 'c3b851f86bef6f01a1ba4318d95b89cb20ce8f6bb5f6f237d8e555de78beff9f',
                   ('native_math', 'final'): '5a9359e8c3b9c27301011cab6a19b1157d9f466e4c5ce2db8d9940a02bde9739'}
        for (role, stage), template_hash in earlier.items():
            prompt = protocol.canonical_prompt(role, reading_stage=stage,
                                               reading_protocol=openness_reading.PROTOCOL_V1, common_version=1)
            self.assertEqual((prompt['template_id'], prompt['template_hash']),
                             (openness_reading.PROTOCOL_V1 + '/' + stage, template_hash))
            assignment = {'role': role, 'prompt': prompt, 'context': {}}
            self.assertTrue(protocol._prompt_matches(assignment))
            current = protocol.canonical_prompt(role, reading_stage=stage)
            self.assertEqual(current['template_id'], openness_reading.PROTOCOL + '/' + stage)
            self.assertNotEqual(current['template_hash'], template_hash)
        for options, template_hash in (
                ({'orchestration': 'problem_creation'},
                 '81186a4b16988f1b05fa503f34dd3b808657b26eb2563fbec7459434f227ad7a'),
                ({'orchestration': 'problem_creation', 'repair': True},
                 'fcf79ccd45606833338f49b94f6a391bdef83e80a1fbb6171a249e5eebfa9564'),
                ({}, '05dbf324f0c26ef1923671ad830bc742a0f86db0e3591917600aa36d15f8ebda')):
            with self.subTest(options=options):
                prompt = protocol.canonical_prompt('bar', excerpts=True, excerpt_rules=review_excerpts.PROTOCOL_V1,
                                                   common_version=1, **options)
                self.assertEqual(prompt['template_hash'], template_hash)
                context = {'context_repair': {}} if options.get('repair') else {'prior_assignment_id': 'earlier'}
                assignment = {'role': 'bar', 'prompt': prompt, 'context': context,
                              'reassessment_required': bool(options.get('repair')),
                              'subject': {'kind': 'problem'} if options else {}}
                self.assertTrue(protocol._prompt_matches(assignment))
                current = protocol.canonical_prompt('bar', excerpts=True, **options)
                self.assertEqual(current['template_id'], prompt['template_id'])
                self.assertEqual(current['parameters']['reviewer_excerpts'], review_excerpts.PROTOCOL)

    def test_the_current_prompts_state_the_turn_budget(self):
        for text in (review_excerpts.PHASE_PROMPT, openness_reading.READING_PROMPT):
            self.assertIn('120,000', text)
            self.assertIn('part', text)
        self.assertIn('deferred_reading_budget', review_excerpts.PHASE_PROMPT)
        self.assertNotIn('120,000', review_excerpts.PHASE_PROMPT_V1)
        self.assertEqual(json.dumps(sorted(openness_reading.PROMPTS)),
                         json.dumps(sorted([openness_reading.PROTOCOL_V1, openness_reading.PROTOCOL_V2,
                                            openness_reading.PROTOCOL, openness_reading.MATH_PROTOCOL])))
