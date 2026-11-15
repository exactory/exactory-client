"""Size-only condensation, with original entries retained by handle."""
import copy
import unittest

from research_harness import review_condensing as condensing
from research_harness.errors import ResearchError


def build_entries(count, characters=300):
    return [dict(handle='e' + str(index), characters=characters) for index in range(count)]


class CondensingTests(unittest.TestCase):
    def test_ten_thousand_entries_fit_ordered_blocks_and_finish(self):
        entries = build_entries(10000)
        before = copy.deepcopy(entries)
        plan = condensing.plan_condensing(entries, 480000)
        self.assertEqual([len(round_) for round_ in plan['rounds']], [7, 2])
        self.assertEqual(plan['turns'], 9)
        self.assertEqual(plan['remaining_characters'], 187500)
        self.assertEqual([entry for block in plan['rounds'][0] for entry in block['entries']], entries)
        self.assertTrue(all(block['characters'] <= 480000 for round_ in plan['rounds'] for block in round_))
        self.assertEqual(entries, before)

    def test_four_and_sixteen_rooms_need_one_and_two_rounds(self):
        for count, rounds, turns in ((4, 1, 4), (16, 2, 20)):
            with self.subTest(count=count):
                plan = condensing.plan_condensing(build_entries(count, 480000), 480000)
                self.assertEqual(len(plan['rounds']), rounds)
                self.assertEqual(plan['turns'], turns)
                self.assertEqual(plan['remaining_characters'], 480000)

    def test_total_only_count_matches_dense_entry_packing(self):
        for count in (0, 1, 1600, 1601, 6400, 10000, 25600):
            with self.subTest(count=count):
                plan = condensing.plan_condensing(build_entries(count), 480000)
                self.assertEqual(condensing.condense(count * 300, 480000),
                                 (plan['turns'], plan['remaining_characters']))

    def test_indivisible_entries_use_actual_blocks_and_not_a_total_estimate(self):
        plan = condensing.plan_condensing(build_entries(3, 300000), 480000)
        self.assertEqual(plan['turns'], 3)
        self.assertEqual(plan['remaining_characters'], 225000)
        self.assertEqual(condensing.condense(900000, 480000), (2, 225000))

    def test_empty_fitting_zero_and_non_divisible_sizes(self):
        self.assertEqual(condensing.cut_condensing_blocks([], 10), [])
        self.assertEqual(condensing.plan_condensing([], 10),
                         dict(rounds=[], turns=0, remaining_characters=0))
        self.assertEqual(condensing.condense(10, 10), (0, 10))
        plan = condensing.plan_condensing(build_entries(5, 3), 7)
        self.assertEqual(plan['turns'], 3)
        self.assertEqual(plan['remaining_characters'], 2)
        self.assertEqual(len(condensing.cut_condensing_blocks(build_entries(2, 0), 1)), 1)

    def test_invalid_room_size_and_oversized_entry_refuse(self):
        for entries, room in (([], 0), ([], -1), (build_entries(1, -1), 10),
                              (build_entries(1, 11), 10),
                              ([dict(handle='e', characters=True)], 10)):
            with self.subTest(entries=entries, room=room), self.assertRaises(ResearchError) as caught:
                condensing.cut_condensing_blocks(entries, room)
            self.assertEqual(caught.exception.code, 'invalid_input')
        for characters, room in ((-1, 1), (1, 0), (True, 1)):
            with self.assertRaises(ResearchError):
                condensing.condense(characters, room)


class CombinationTests(unittest.TestCase):
    def setUp(self):
        self.entries = build_entries(8)
        self.block = condensing.cut_condensing_blocks(self.entries, 1200)[0]
        self.output = dict(block=self.block['block'], summary='s' * 300, refs=['e0', 'e3'])

    def test_exact_quarter_bound_accepts_a_partial_set_of_local_references(self):
        before = copy.deepcopy((self.block, self.output))
        self.assertEqual(condensing.validate_combination(self.block, self.output), self.output)
        self.assertEqual((self.block, self.output), before)

    def test_invalid_combinations_refuse_under_shape_check(self):
        bad = [dict(self.output, summary='s' * 301), dict(self.output, refs=['e4']),
               {key: value for key, value in self.output.items() if key != 'refs'},
               dict(self.output, block='another-block'), dict(self.output, summary=1),
               dict(self.output, refs='e0'), dict(self.output, refs=[{}]),
               dict(self.output, refs=[]), dict(self.output, refs=['e0', 'e0']),
               dict(self.output, extra=True), None]
        for output in bad:
            with self.subTest(output=output), self.assertRaises(ResearchError) as caught:
                condensing.validate_combination(self.block, output)
            self.assertEqual(caught.exception.code, 'invalid_value_review')
            self.assertEqual(caught.exception.contract_check, 'review.shape')

    def test_replacement_keeps_original_and_nested_handles_without_mutation(self):
        state = dict(entries=self.entries, handles={})
        before = copy.deepcopy(state)
        first = condensing.apply_combination(state, self.block, self.output)
        self.assertEqual(state, before)
        self.assertEqual([row['handle'] for row in first['entries'][1:]], ['e4', 'e5', 'e6', 'e7'])
        for entry in self.entries:
            self.assertEqual(first['handles'][entry['handle']], entry)
        next_block = condensing.cut_condensing_blocks(first['entries'], 1200)[0]
        output = dict(block=next_block['block'], summary='s' * 200,
                      refs=[first['entries'][0]['handle']])
        second = condensing.apply_combination(first, next_block, output)
        self.assertEqual(second['handles']['e0'], self.entries[0])
        self.assertEqual(second['handles'][first['entries'][0]['handle']]['summary'], 's' * 300)
        second['handles']['e0']['characters'] = 1
        self.assertEqual(first['handles']['e0']['characters'], 300)

    def test_replacement_refuses_a_block_absent_from_the_current_state(self):
        with self.assertRaises(ResearchError) as caught:
            condensing.apply_combination(dict(entries=self.entries[4:], handles={}), self.block, self.output)
        self.assertEqual(caught.exception.code, 'invalid_input')
