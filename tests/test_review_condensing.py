"""Entry-aware condensation and whole serialized combination bounds (finding 3)."""
import copy
import unittest

from research_harness import review_condensing as condensing
from research_harness.errors import ResearchError
from research_harness.review_parts import render_json


def measure(value):
    return len(render_json(value))


def build_entries(count, characters=300):
    return [dict(handle='e' + str(index), characters=characters) for index in range(count)]


def build_combination(block, answer_bound=None):
    if answer_bound is None:
        answer_bound = block['characters'] // 4
    bound = condensing.describe_combination_bound(block, answer_bound)
    output = dict(block=block['handle'], summary='', refs=list(block['entries']))
    room = bound - measure(output)
    if room < 0:
        raise AssertionError('Fixture references do not fit the combination bound')
    # Both quotation marks and backslashes cost two serialized characters.
    output['summary'] = ('"\\' * (room // 4) + '"' * ((room % 4) // 2) + 's' * (room % 2))
    if measure(output) != bound:
        raise AssertionError('Fixture did not spend its complete serialized bound')
    return output


class CondensingTests(unittest.TestCase):
    def test_ten_thousand_entries_fit_ordered_blocks_and_finish(self):
        entries = build_entries(10000)
        before = copy.deepcopy(entries)
        plan = condensing.plan_condensing(entries, 480000)
        self.assertEqual([len(round_) for round_ in plan['rounds']], [7, 2])
        self.assertEqual(plan['turns'], 9)
        self.assertEqual(plan['remaining_characters'], 187500)
        self.assertEqual([handle for block in plan['blocks'] for handle in block['entries']],
                         [entry['handle'] for entry in entries])
        self.assertTrue(all(block['characters'] <= 480000 for round_ in plan['rounds'] for block in round_))
        self.assertEqual(entries, before)

    def test_equal_totals_have_different_exact_blocks_and_a_marked_upper_forecast(self):
        forecast = condensing.forecast_condensing(108000, 60000, 54000)
        self.assertIs(forecast['forecast'], True)
        for count, size in ((3, 36000), (2, 54000)):
            with self.subTest(count=count):
                plan = condensing.plan_condensing(build_entries(count, size), 60000)
                self.assertEqual(len(plan['blocks']), count)
                self.assertEqual(plan['turns'], count)
                self.assertGreaterEqual(forecast['turns'], plan['turns'])
                self.assertLessEqual(forecast['remaining_characters'], 60000)

    def test_four_and_sixteen_rooms_need_one_and_two_rounds(self):
        for count, rounds, turns in ((4, 1, 4), (16, 2, 20)):
            with self.subTest(count=count):
                plan = condensing.plan_condensing(build_entries(count, 480000), 480000)
                self.assertEqual(len(plan['rounds']), rounds)
                self.assertEqual(plan['turns'], turns)
                self.assertEqual(plan['remaining_characters'], 480000)

    def test_whole_combinations_at_bounds_fit_next_round_and_keep_block_access(self):
        entries = build_entries(16, 480000)
        before = copy.deepcopy(entries)
        plan = condensing.plan_condensing(entries, 480000)
        state = dict(entries=entries, handles={}, blocks={})
        for blocks in plan['rounds']:
            for block in blocks:
                self.assertLessEqual(sum(state['handles'].get(handle, next(
                    (entry for entry in state['entries'] if entry['handle'] == handle), {}))['characters']
                    for handle in block['entries']), 480000)
                output = build_combination(block)
                condensing.validate_combination(block, output, measure, 120000)
                state = condensing.apply_combination(state, block, output, measure)
                self.assertEqual(state['handles'][block['handle']]['characters'], measure(output))
        self.assertEqual(sum(measure(entry['value']) for entry in state['entries']), 480000)
        outer = state['blocks'][state['entries'][0]['handle']]
        inner = state['blocks'][outer['entries'][0]]
        self.assertEqual(state['handles'][inner['entries'][0]], entries[0])
        self.assertEqual(entries, before)
        state['handles']['e0']['characters'] = 1
        self.assertEqual(entries[0]['characters'], 480000)

    def test_small_last_block_is_carried_unchanged_at_the_stated_threshold(self):
        minimum = condensing.MINIMUM_CONDENSING_BLOCK_CHARACTERS
        for tail, turns in ((minimum - 1, 1), (minimum, 2)):
            with self.subTest(tail=tail):
                entries = [dict(handle='full', characters=60000), dict(handle='tail', characters=tail)]
                plan = condensing.plan_condensing(entries, 60000)
                self.assertEqual(plan['turns'], turns)
                if turns == 1:
                    self.assertEqual(plan['entries'][-1], entries[-1])
                    self.assertEqual(plan['remaining_characters'], 15000 + tail)

    def test_small_block_before_a_full_entry_is_carried_in_its_original_position(self):
        entries = [dict(handle='small', characters=300), dict(handle='large', characters=60000)]
        plan = condensing.plan_condensing(entries, 60000)
        self.assertEqual(plan['turns'], 1)
        self.assertEqual(plan['entries'][0], entries[0])
        self.assertEqual(plan['rounds'][0][0]['entries'], ['large'])
        self.assertEqual(plan['remaining_characters'], 15300)

    def test_an_overflow_with_only_undersized_blocks_refuses_without_repeating(self):
        with self.assertRaises(ResearchError) as caught:
            condensing.plan_condensing(build_entries(2, 900), 1200)
        self.assertEqual(caught.exception.code, 'invalid_input')

    def test_empty_fitting_and_zero_sizes(self):
        self.assertEqual(condensing.cut_condensing_blocks([], 10), [])
        plan = condensing.plan_condensing([], 10)
        self.assertEqual((plan['blocks'], plan['rounds'], plan['turns'], plan['remaining_characters']),
                         ([], [], 0, 0))
        self.assertEqual(condensing.forecast_condensing(0, 10, 0)['turns'], 0)
        self.assertEqual(len(condensing.cut_condensing_blocks(build_entries(2, 0), 1)), 1)

    def test_invalid_room_size_and_oversized_entry_refuse(self):
        for entries, room in (([], 0), ([], -1), (build_entries(1, -1), 10),
                              (build_entries(1, 11), 10),
                              ([dict(handle='e', characters=True)], 10)):
            with self.subTest(entries=entries, room=room):
                with self.assertRaises(ResearchError) as caught:
                    condensing.cut_condensing_blocks(entries, room)
                self.assertEqual(caught.exception.code, 'invalid_input')
        for characters, room, largest in ((-1, 1, 1), (1, 0, 1), (True, 1, 1), (10, 5, 6), (10, 5, 0)):
            with self.subTest(characters=characters, room=room, largest=largest):
                with self.assertRaises(ResearchError):
                    condensing.forecast_condensing(characters, room, largest)


class CombinationTests(unittest.TestCase):
    def setUp(self):
        self.entries = build_entries(8)
        self.block = condensing.cut_condensing_blocks(self.entries, 1200)[0]
        self.output = None

    def test_whole_escaped_combination_with_all_refs_accepts_exact_bound(self):
        output = build_combination(self.block)
        before = copy.deepcopy((self.block, output))
        self.assertEqual(measure(output), 300)
        self.assertIn('"', output['summary'])
        self.assertIn('\\', output['summary'])
        self.assertEqual(condensing.validate_combination(self.block, output, measure, 1000), output)
        self.assertEqual((self.block, output), before)
        with self.assertRaises(ResearchError) as caught:
            condensing.validate_combination(self.block, dict(output, summary=output['summary'] + 's'), measure, 1000)
        self.assertEqual(caught.exception.contract_check, 'review.shape')

    def test_lower_turn_answer_bound_controls_whole_output(self):
        self.assertEqual(condensing.describe_combination_bound(self.block, 250), 250)
        output = build_combination(self.block, 250)
        self.assertEqual(condensing.validate_combination(self.block, output, measure, 250), output)
        with self.assertRaises(ResearchError):
            condensing.validate_combination(self.block, dict(output, summary=output['summary'] + 's'), measure, 250)

    def test_invalid_combinations_refuse_under_shape_check(self):
        output = build_combination(self.block)
        bad = [dict(output, summary=output['summary'] + 's'), dict(output, refs=['e4']),
               {key: value for key, value in output.items() if key != 'refs'},
               dict(output, block='another-block'), dict(output, summary=1),
               dict(output, refs='e0'), dict(output, refs=[{}]), dict(output, refs=[]),
               dict(output, refs=['e0', 'e0']), dict(output, extra=True), None]
        for value in bad:
            with self.subTest(output=value):
                with self.assertRaises(ResearchError) as caught:
                    condensing.validate_combination(self.block, value, measure, 1000)
                self.assertEqual(caught.exception.code, 'invalid_value_review')
                self.assertEqual(caught.exception.contract_check, 'review.shape')

    def test_replacement_refuses_a_block_absent_from_the_current_state(self):
        with self.assertRaises(ResearchError) as caught:
            condensing.apply_combination(dict(entries=self.entries[4:], handles={}, blocks={}),
                                         self.block, build_combination(self.block), measure)
        self.assertEqual(caught.exception.code, 'invalid_input')
