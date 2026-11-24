"""Pure entry-aware final plans, bounded recovery and immutable currency."""
import copy
import json
import unittest
from research_harness import review_final_pages as pages
from research_harness import review_route_capacity as capacity
from research_harness.errors import ResearchError
from research_harness.evidence import digest


def build_notes():
    return dict(id='notes', minimum=6000, preferred=20000, maximum=20000)


def build_target_parts(count, rows=0):
    return [dict(id='t' + str(i), minimum=6700 + rows, preferred=30000 + rows,
                 maximum=60000 + rows, overhead=rows) for i in range(count)]


def build_entries(prefix, characters, largest=6000):
    if characters < 0:
        return [dict(handle=prefix, characters=characters)]
    return [dict(handle=prefix + str(i), characters=min(largest, characters - start))
            for i, start in enumerate(range(0, characters, largest))]


def build_targets(count, entries=300, rows=0):
    return [dict(handle='t' + str(i), entries=build_entries('e' + str(i) + ':', entries),
                 rows=build_entries('g' + str(i) + ':', rows, 400)) for i in range(count)]


def sum_entries(entries):
    return sum(entry['characters'] for entry in entries)


def build_block(entries):
    characters = sum_entries(entries)
    handle = digest(entries)
    return dict(handle='b' + handle, entries=[entry['handle'] for entry in entries], characters=characters)


def condense_quarters(entries, room):
    """An independent synthetic entry policy, including no-op blocks for recovery."""
    def cut_blocks(values):
        blocks, current, size = [], [], 0
        for entry in values:
            if entry['characters'] > room:
                raise ResearchError('invalid_input', 'Synthetic entries must fit a block whole.')
            if current and size + entry['characters'] > room:
                blocks.append(build_block(current))
                current, size = [], 0
            current.append(entry)
            size += entry['characters']
        if current:
            blocks.append(build_block(current))
        return blocks
    remaining = copy.deepcopy(entries)
    initial, rounds = cut_blocks(remaining), []
    while sum_entries(remaining) > room:
        blocks = cut_blocks(remaining)
        rounds.append(blocks)
        remaining = [dict(handle=block['handle'], characters=block['characters'] // 4) for block in blocks]
    return dict(blocks=initial, rounds=rounds, turns=sum(len(blocks) for blocks in rounds), minimum_block_characters=1,
                entries=remaining, remaining_characters=sum_entries(remaining))


def build_inputs(targets, room=60000, **changes):
    arguments = dict(page_room=room, target_allowance=max(1, len(targets)), target_minimum=6700,
                     notes=20000, pool=build_entries('pool:', 1000), targets=targets,
                     closing_room=1200000, row_characters=360, condense=condense_quarters)
    arguments.update(changes)
    return arguments


def build_turns(targets, room=60000, **changes):
    return pages.plan_final_turns(**build_inputs(targets, room, **changes))


def count_turns(turns):
    return dict(pages=sum(turn['kind'] in ('page', 'final') for turn in turns),
                condensing_turns=sum(turn['kind'] == 'combine' for turn in turns),
                closing_pages=sum(turn['kind'] == 'closing' for turn in turns))


def build_acceptance_shapes():
    def build_mixed(count):
        result = []
        for i in range(count):
            result.append(dict(handle='t' + str(i), rows=[], entries=build_entries(
                'e' + str(i) + ':', 300 + (i % 100) * 300)))
        return result
    return {'5000_targets': build_mixed(5000), '20000_targets': build_mixed(20000),
            '2000_gaps': build_targets(1, 30000, 2000 * 400),
            'one_target_all_units': [dict(handle='all', rows=[], entries=build_entries(
                'card:', 10000 * capacity.CARD_ENTRY_CHARACTERS, capacity.CARD_ENTRY_CHARACTERS))]}


class FinalPageAssertions:
    def assert_pages_fit_in_order(self, turns, targets):
        delivered, numbers = [], []
        for turn in turns:
            numbers.append(turn['number'])
            context = turn['context']
            if turn['kind'] in ('combine', 'closing'):
                room = context['closing_room'] if turn['kind'] == 'closing' or turn['scope'] == 'rows' else context['page_room']
                self.assertLessEqual(turn['characters'], room)
            if turn['kind'] not in ('page', 'final'):
                continue
            sizes = [context['notes'], sum_entries(turn['pool'])]
            sizes.extend(sum_entries(part['entries'] + part['rows']) for part in turn['parts'])
            division = capacity.divide_room(context['page_room'], [
                dict(minimum=size, preferred=size, maximum=size) for size in sizes])
            self.assertEqual(division['minimum'], turn['characters'])
            self.assertLessEqual(len(turn['targets']), context['target_allowance'])
            self.assertEqual([part['handle'] for part in turn['parts']], turn['targets'])
            delivered.extend(turn['targets'])
        self.assertEqual(delivered, [target['handle'] for target in targets])
        self.assertEqual(len(numbers), len(set(numbers)))


class FinalAllocationTests(unittest.TestCase):
    def test_target_allowance_prevents_a_small_single_request(self):
        self.assertEqual(pages.allocate_final_targets(60000, 2, build_notes(), build_target_parts(3))['form'], 'pages')

    def test_eight_targets_keep_the_existing_single_room_division(self):
        notes, targets = build_notes(), build_target_parts(8)
        before = copy.deepcopy((notes, targets))
        result = pages.allocate_final_targets(60000, 8, notes, targets)
        self.assertEqual(result['form'], 'request')
        self.assertEqual(result['division'], capacity.divide_room(60000, [notes] + targets))
        self.assertEqual((notes, targets), before)

    def test_nine_targets_get_the_preferred_thirty_thousand_each(self):
        result = pages.allocate_final_targets(60000, 9, build_notes(), build_target_parts(9))
        self.assertEqual(result['form'], 'pages')
        self.assertEqual([part['allowance'] for part in result['targets']], [30000] * 9)
        self.assertEqual([part['id'] for part in result['targets']], ['t' + str(i) for i in range(9)])

    def test_seventy_targets_fit_the_large_room_without_changing_the_division(self):
        notes, targets = build_notes(), build_target_parts(70)
        result = pages.allocate_final_targets(480000, 70, notes, targets)
        self.assertEqual(result['form'], 'request')
        self.assertEqual(result['division'], capacity.divide_room(480000, [notes] + targets))

    def test_oversized_gap_rows_are_marked_and_keep_a_target_allowance(self):
        target = pages.allocate_final_targets(60000, 1, build_notes(), build_target_parts(1, 800000))['targets'][0]
        self.assertTrue(target['condense_rows'])
        self.assertEqual(target['rows'], 15000)
        self.assertEqual(target['allowance'], 30000)
        self.assertGreaterEqual(target['allowance'], 6700 + target['rows'])

    def test_minimum_above_half_a_page_still_refuses_below_floor(self):
        with self.assertRaises(ResearchError) as caught:
            pages.allocate_final_targets(10000, 2, build_notes(), build_target_parts(2))
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')


class FinalTurnTests(FinalPageAssertions, unittest.TestCase):
    def test_exact_fit_remains_one_final_without_a_closing_page(self):
        targets = build_targets(3, 13000)
        turns = build_turns(targets)
        self.assertEqual([turn['kind'] for turn in turns], ['final'])
        self.assertEqual(turns[0]['characters'], 60000)
        self.assert_pages_fit_in_order(turns, targets)

    def test_three_small_targets_obey_the_target_allowance(self):
        targets = build_targets(3)
        turns = build_turns(targets, target_allowance=2)
        self.assertEqual([turn['kind'] for turn in turns], ['page', 'page', 'closing'])
        self.assertEqual([len(turn['targets']) for turn in turns[:2]], [2, 1])
        self.assert_pages_fit_in_order(turns, targets)

    def test_nine_targets_can_use_every_granted_entry_character(self):
        allocation = pages.allocate_final_targets(60000, 9, build_notes(), build_target_parts(9))
        targets = build_targets(9, allocation['targets'][0]['allowance'])
        turns = build_turns(targets)
        self.assert_pages_fit_in_order(turns, targets)
        self.assertEqual(count_turns(turns), dict(pages=9, condensing_turns=0, closing_pages=1))

    def test_five_thousand_targets_pack_mixed_serialized_sizes(self):
        targets = build_acceptance_shapes()['5000_targets']
        for room in (60000, 480000):
            self.assert_pages_fit_in_order(build_turns(targets, room), targets)

    def test_twenty_thousand_targets_pack_mixed_serialized_sizes(self):
        targets = build_acceptance_shapes()['20000_targets']
        for room in (60000, 480000):
            self.assert_pages_fit_in_order(build_turns(targets, room), targets)

    def test_one_target_three_times_the_room_is_condensed_before_its_page(self):
        targets = build_targets(1, 180000)
        turns = build_turns(targets)
        self.assertEqual([turn['kind'] for turn in turns], ['combine'] * 7 + ['page', 'closing'])
        self.assertEqual(turns[-2]['characters'], 32250)
        self.assert_pages_fit_in_order(turns, targets)

    def test_two_thousand_gap_rows_are_condensed_before_their_page(self):
        targets = build_targets(1, 30000, rows=800000)
        turns = build_turns(targets)
        self.assertEqual((turns[0]['scope'], turns[0]['component']), ('target', 'rows'))
        self.assertLessEqual(sum_entries(turns[-2]['parts'][0]['rows']), 15000)
        self.assert_pages_fit_in_order(turns, targets)

    def test_twenty_thousand_decisions_use_the_injected_count_before_closing(self):
        def condense_seventeen(entries, room):
            self.assertEqual((len(entries), sum_entries(entries), room), (20000, 7200000, 1200000))
            blocks = [build_block(entries[i * 20000 // 17:(i + 1) * 20000 // 17]) for i in range(17)]
            for i, block in enumerate(blocks):
                block['output_bound'] = 64705 if i < 16 else 64720
            return dict(blocks=blocks, rounds=[blocks], turns=17,
                        entries=[dict(handle=block['handle'], characters=block.pop('output_bound')) for block in blocks], remaining_characters=1100000)
        turns = build_turns(build_targets(20000), condense=condense_seventeen)
        self.assertEqual([turn['kind'] for turn in turns[-18:]], ['combine'] * 17 + ['closing'])
        self.assertTrue(all(turn['scope'] == 'rows' for turn in turns[-18:-1]))
        self.assertEqual(turns[-1]['characters'], 1100000)

    def test_common_pool_is_condensed_before_pages_after_the_room_falls(self):
        targets, pool = build_targets(1, 30000), build_entries('shared:', 400000, 20000)
        initial = build_turns(targets, 480000, pool=pool)
        self.assertEqual([turn['kind'] for turn in initial], ['final'])
        turns = build_turns(targets, 240000, pool=pool)
        combines = [turn for turn in turns if turn['kind'] == 'combine']
        self.assertTrue(combines)
        self.assertTrue(all(turn['scope'] == 'pool' for turn in combines))
        self.assertEqual([handle for turn in combines for handle in turn['block']['entries']],
                         [entry['handle'] for entry in pool])
        self.assertEqual(turns[0]['scope'], 'pool')
        self.assertLess(len(turns), 100)
        self.assert_pages_fit_in_order(turns, targets)
        self.assert_pages_fit_in_order(pages.repack_final_turns(initial, [], [], 240000, 1, condense=condense_quarters), targets)

    def test_equal_totals_keep_distinct_whole_entry_block_layouts(self):
        counts = []
        for sizes in ([36000] * 3, [54000] * 2):
            entries = [dict(handle='entry:' + str(i), characters=size) for i, size in enumerate(sizes)]
            turns = build_turns([dict(handle='target', entries=entries, rows=[])], notes=0, pool=[], target_minimum=0)
            combines = [turn for turn in turns if turn['kind'] == 'combine']
            counts.append(len(combines))
            self.assertEqual([handle for turn in combines for handle in turn['block']['entries']],
                             [entry['handle'] for entry in entries])
        self.assertEqual(counts, [3, 2])

    def test_carried_tail_from_the_callback_is_not_discarded(self):
        entries = [dict(handle='large:' + str(i), characters=36000) for i in range(3)]
        tail = dict(handle='tail', characters=300)
        def condense_with_tail(values, room):
            self.assertEqual(values, entries + [tail])
            blocks = [build_block([entry]) for entry in entries]
            return dict(blocks=blocks, rounds=[blocks], turns=3,
                        entries=[dict(handle=block['handle'], characters=block['characters'] // 4) for block in blocks] + [tail], remaining_characters=27300)
        turns = build_turns([dict(handle='target', entries=entries + [tail], rows=[])],
                            notes=0, pool=[], condense=condense_with_tail)
        self.assertIn(tail, turns[-2]['parts'][0]['entries'])
        self.assertEqual(turns[-2]['characters'], 27300)

    def test_empty_targets_and_zero_sizes_still_have_one_final(self):
        turns = build_turns([], notes=0, pool=[])
        self.assertEqual([turn['kind'] for turn in turns], ['final'])
        self.assertEqual(turns[0]['characters'], 0)

    def test_shared_notes_and_pool_leave_no_room_for_a_target(self):
        with self.assertRaises(ResearchError) as caught:
            build_turns(build_targets(1), notes=60000)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')

    def test_condensing_must_return_a_positive_count_and_a_fitting_bound(self):
        for answer in (None, (1,), {}, dict(blocks=[], rounds=[], turns=0, entries=[], remaining_characters=0)):
            with self.subTest(answer=answer), self.assertRaises(ResearchError):
                build_turns(build_targets(1, 180000), condense=lambda entries, room: answer)

    def test_planning_does_not_mutate_or_alias_the_input_sizes(self):
        targets = build_targets(3, 30000)
        before = copy.deepcopy(targets)
        turns = build_turns(targets)
        self.assertEqual(targets, before)
        turns[0]['inputs'][0]['entries'].clear()
        self.assertEqual(targets, before)

    def test_many_zero_size_targets_fit_without_a_per_target_scan_of_the_page(self):
        targets = build_targets(20000, 0)
        turns = build_turns(targets)
        self.assertEqual([turn['kind'] for turn in turns], ['final'])
        self.assert_pages_fit_in_order(turns, targets)

    def test_invalid_sizes_and_repeated_handles_refuse_at_the_planner_edge(self):
        for changes in (dict(page_room=-1), dict(target_allowance=0), dict(notes=-1),
                        dict(pool=build_entries('bad', -1)), dict(closing_room=0), dict(row_characters=-1),
                        dict(targets=build_targets(1, -1)), dict(targets=build_targets(1, rows=-1)),
                        dict(targets=build_targets(1) * 2)):
            arguments = build_inputs(build_targets(2))
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ResearchError):
                pages.plan_final_turns(**arguments)


class FinalReplacementTests(FinalPageAssertions, unittest.TestCase):
    def test_forced_recovery_respects_the_injected_small_block_minimum(self):
        def condense_with_minimum(entries, room):
            result = dict(condense_quarters(entries, room), minimum_block_characters=1024)
            if any(block['characters'] < 1024 for round_ in result['rounds'] for block in round_):
                raise ResearchError('invalid_input', 'No eligible block.', dict(minimum=1024))
            return result
        turns = build_turns(build_targets(2, 30000), condense=condense_with_minimum)
        self.assertIsNone(pages.split_final_turn(turns, turns[-1]['number'], 6000, condense_with_minimum))
        self.assertIsNone(pages.split_final_turn(turns, turns[-1]['number'], 6000, condense_with_minimum))

    def test_recovery_uses_explicit_policy_rounds_without_minimum_metadata(self):
        def condense_without_minimum(entries, room):
            result = condense_quarters(entries, room)
            result.pop('minimum_block_characters')
            return result
        turns = build_turns(build_targets(2, 30000), condense=condense_without_minimum)
        split = pages.split_final_turn(turns, turns[-1]['number'], 6000, condense_without_minimum)
        self.assertEqual([turn['kind'] for turn in split[-3:]], ['combine', 'combine', 'closing'])

    def test_unequal_block_halves_use_policy_evidence_without_a_local_minimum(self):
        def condense_without_minimum(entries, room):
            result = condense_quarters(entries, room)
            result.pop('minimum_block_characters')
            return result
        entries = [dict(handle='e' + str(i), characters=size) for i, size in enumerate((1000, 1000, 5000, 5000, 1000))]
        targets = [dict(handle='target', entries=entries, rows=[])]
        turns = build_turns(targets, 33000, condense=condense_without_minimum)
        split = pages.split_final_turn(turns, turns[0]['number'], 6000, condense_without_minimum)
        self.assertIsNotNone(split)
        self.assertEqual([turn['block']['entries'] for turn in split[:2]], [['e0', 'e1'], ['e2', 'e3']])
        self.assert_pages_fit_in_order(split, targets)

    def test_split_groups_are_held_when_a_later_level_could_rejoin_them(self):
        targets = build_targets(7, 1000)
        split = pages.split_final_turn(build_turns(targets), 1, 6000, condense_quarters)
        self.assertTrue(all(turn['split_of'] == 1 for turn in split))
        repacked = pages.repack_final_turns(split, [], [], 480000, 7, condense=condense_quarters)
        self.assertEqual(repacked, split)
        self.assertEqual([len(turn['targets']) for turn in repacked if turn['kind'] == 'page'], [4, 3])

    def test_repack_into_a_room_that_no_block_can_condense_into_is_below_the_floor(self):
        """Repairs A10. repack_final_turns raises the refusal below the floor itself when the condensing policy finds
        no block of its minimum size for the measured room."""
        def refuse_condensing(entries, room):
            raise ResearchError('invalid_input', 'The overflowing list has no block large enough to condense.',
                                dict(room=room, minimum=4000))
        turns = build_turns(build_targets(9, 10000), target_allowance=2)
        with self.assertRaises(ResearchError) as caught:
            pages.repack_final_turns(turns, [], [], 30000, 1, condense=refuse_condensing)
        self.assertEqual((caught.exception.code, caught.exception.details),
                         ('review_route_capacity_below_floor', dict(room=9000, minimum=4000, command='review-route')))

    def test_pending_request_and_argument_pages_keep_their_targets_and_identity(self):
        targets = build_targets(9, 10000)
        turns = build_turns(targets, target_allowance=2)
        pending = [turns[0]['number'], turns[1]['number']]
        work = {pending[0]: dict(kind='passage', handle='request'),
                pending[1]: dict(kind='argument', handle='part-one')}
        before = digest(work)
        repacked = pages.repack_final_turns(turns, [], pending, 30000, 1, condense=condense_quarters)
        self.assertEqual(repacked[:2], turns[:2])
        self.assertEqual(digest(work), before)
        self.assert_pages_fit_in_order(repacked, targets)

    def test_assignment_guard_enforces_both_limits_without_rejoining_split_groups(self):
        targets = build_targets(7, 1000)
        split = pages.split_final_turn(build_turns(targets), 1, 6000, condense_quarters)
        frozen = digest(split)
        pending_work = {split[0]['number']: 'passage', split[1]['number']: 'argument-part'}
        current = pages.repack_final_turns(split, [], list(pending_work), 60000, 1, condense=condense_quarters)
        for original in split[:2]:
            current = pages.prepare_final_turn(current, original['number'], 60000, 1, 6000,
                                               condense_quarters, pending=list(pending_work))
        page_turns = [turn for turn in current if turn['kind'] == 'page']
        self.assertEqual([len(turn['targets']) for turn in page_turns], [1] * 7)
        for turn in page_turns:
            self.assertIn(turn['pending_of'], pending_work)
            source = next(value for value in split if value['number'] == turn['pending_of'])
            self.assertTrue(set(turn['targets']) <= set(source['targets']))
        self.assert_pages_fit_in_order(current, targets)
        self.assertEqual(digest(split), frozen)

    def test_split_pool_rewrites_pool_handles_of_later_target_combines(self):
        turns = build_turns(build_targets(1, 300000), 240000, pool=build_entries('shared:', 400000))
        old = turns[0]['output']['handle']
        split = pages.split_final_turn(turns, turns[0]['number'], 6000, condense_quarters)
        for turn in split:
            self.assertNotIn(old, [entry['handle'] for entry in turn['pool']])
        self.assert_pages_fit_in_order(split, build_targets(1, 300000))

    def test_split_seven_targets_into_four_then_three_without_mutating_the_input(self):
        targets = build_targets(14, 5500)
        turns = build_turns(targets, pool=[])
        before = copy.deepcopy(turns)
        split = pages.split_final_turn(turns, turns[0]['number'], 6000, condense_quarters)
        self.assertEqual([len(turn['targets']) for turn in split if turn['kind'] == 'page'], [4, 3, 7])
        self.assertEqual(turns, before)
        self.assertEqual(split[-1], turns[-1])
        self.assert_pages_fit_in_order(split, targets)

    def test_split_singleton_inserts_condensing_turns_then_the_page(self):
        targets = build_targets(2, 30000)
        turns = build_turns(targets)
        split = pages.split_final_turn(turns, turns[0]['number'], 6000, condense_quarters)
        self.assertEqual(count_turns(split)['condensing_turns'], 3)
        self.assertEqual(split[-3]['characters'], 28500)
        self.assert_pages_fit_in_order(split, targets)

    def test_split_final_inserts_two_pages_and_one_closing_page(self):
        targets = build_targets(7, 1000)
        split = pages.split_final_turn(build_turns(targets), 1, 6000, condense_quarters)
        self.assertEqual([turn['kind'] for turn in split], ['page', 'page', 'closing'])
        self.assertEqual([len(turn['targets']) for turn in split[:2]], [4, 3])
        self.assertEqual(split[-1]['characters'], 2520)
        self.assert_pages_fit_in_order(split, targets)

    def test_split_single_target_final_condenses_without_an_empty_second_page(self):
        targets = build_targets(1, 10000)
        split = pages.split_final_turn(build_turns(targets), 1, 6000, condense_quarters)
        self.assertEqual(count_turns(split), dict(pages=1, condensing_turns=2, closing_pages=1))
        self.assert_pages_fit_in_order(split, targets)

    def test_one_block_above_floor_is_reduced_and_actual_floor_stays_terminal(self):
        turns = build_turns(build_targets(2, 30000))
        split = pages.split_final_turn(turns, turns[0]['number'], 6000, condense_quarters)
        page = next(turn for turn in split if turn['kind'] == 'page')
        next_split = pages.split_final_turn(split, page['number'], 6000, condense_quarters)
        last_page = next(turn for turn in next_split if turn['kind'] == 'page')
        self.assertLessEqual(sum_entries(last_page['parts'][0]['entries']), 6000)
        self.assertIsNone(pages.split_final_turn(next_split, last_page['number'], 6000, condense_quarters))
        self.assertIsNone(pages.split_final_turn(next_split, last_page['number'], 6000, condense_quarters))
        self.assertIsNone(pages.split_final_turn(build_turns(build_targets(1, 6000)), 1, 6000, condense_quarters))

    def test_split_condensing_block_into_ordered_halves_and_single_entry_stops(self):
        targets = build_targets(1, 180000)
        turns = build_turns(targets)
        first = turns[0]
        split = pages.split_final_turn(turns, first['number'], 6000, condense_quarters)
        self.assertEqual([len(turn['block']['entries']) for turn in split[:2]], [3, 3])
        self.assertEqual([handle for turn in split[:2] for handle in turn['block']['entries']],
                         first['block']['entries'])
        one = build_turns([dict(handle='one', entries=[dict(handle='a', characters=36000),
                                                      dict(handle='b', characters=36000)], rows=[])], notes=0, pool=[])
        self.assertIsNone(pages.split_final_turn(one, one[0]['number'], 6000, condense_quarters))

    def test_split_closing_condenses_two_blocks_and_one_combination_stops(self):
        turns = build_turns(build_targets(6, 10000), row_characters=1000)
        split = pages.split_final_turn(turns, turns[-1]['number'], 6000, condense_quarters)
        self.assertEqual([turn['kind'] for turn in split[-3:]], ['combine', 'combine', 'closing'])
        self.assertEqual([len(turn['block']['entries']) for turn in split[-3:-1]], [3, 3])
        self.assertEqual(split[-1]['characters'], 1500)
        one = copy.deepcopy(split)
        one[-1]['rows'] = [dict(handle='only-combination', characters=100)]
        self.assertIsNone(pages.split_final_turn(one, one[-1]['number'], 6000, condense_quarters))

    def test_split_refuses_unknown_nonpage_and_empty_final(self):
        with self.assertRaises(ResearchError):
            pages.split_final_turn(build_turns(build_targets(9, 30000)), 999, 6000, condense_quarters)
        with self.assertRaises(ResearchError):
            pages.split_final_turn(build_turns([]), 1, 6000, condense_quarters)

    def test_repack_preserves_completed_turns_and_reuses_every_other_target_once(self):
        targets = build_targets(25, 10000)
        turns = pages.split_final_turn(build_turns(targets, 480000), 1, 6000, condense_quarters)
        completed = [turns[0]['number']]
        before = copy.deepcopy(turns)
        repacked = pages.repack_final_turns(turns, completed, [], 60000, 25, condense=condense_quarters)
        self.assertEqual(repacked[0], turns[0])
        self.assertEqual(turns, before)
        self.assert_pages_fit_in_order(repacked, targets)

    def test_repack_preserves_nonprefix_completed_page_and_completed_closing(self):
        targets = build_targets(20, 10000)
        turns = build_turns(targets)
        completed = [turns[2]['number'], turns[-1]['number']]
        repacked = pages.repack_final_turns(turns, completed, [], 480000, 20, condense=condense_quarters)
        self.assertTrue(all(turn in repacked for turn in turns if turn['number'] in completed))
        self.assert_pages_fit_in_order(repacked, targets)
        self.assertEqual(count_turns(repacked)['closing_pages'], 1)

    def test_lower_target_allowance_repacks_assigned_but_uncompleted_pages(self):
        targets = build_targets(9)
        turns = build_turns(targets, target_allowance=2)
        # First page complete; second assigned without output; three unassigned.
        repacked = pages.repack_final_turns(turns, [turns[0]['number']], [], 60000, 1, condense=condense_quarters)
        self.assertEqual(repacked[0], turns[0])
        self.assertNotIn(turns[1], repacked)
        self.assertEqual([len(turn['targets']) for turn in repacked if turn['kind'] == 'page'], [2] + [1] * 7)
        self.assert_pages_fit_in_order(repacked, targets)

    def test_repack_refuses_unknown_completed_turns(self):
        for completed, pending in (([999], []), ([1, 1], []), ([], [999]), ([1], [1])):
            with self.subTest(completed=completed, pending=pending), self.assertRaises(ResearchError):
                pages.repack_final_turns(build_turns(build_targets(2)), completed, pending, 60000, 2, condense=condense_quarters)
        with self.assertRaises(ResearchError):
            pages.repack_final_turns(build_turns(build_targets(1, 180000)), [], [1], 60000, 1, condense=condense_quarters)

    def test_assignment_guard_reduces_material_and_keeps_pending_origin(self):
        targets = build_targets(4, 10000)
        turns = build_turns(targets, target_allowance=2)
        guarded = pages.prepare_final_turn(turns, 1, 30000, 2, 6000, condense_quarters, pending=[1])
        replacements = [turn for turn in guarded if turn['split_of'] == 1 and turn['kind'] == 'page']
        self.assertEqual(len(replacements), 2)
        self.assertTrue(all(turn['characters'] <= 30000 and turn['pending_of'] == 1 for turn in replacements))
        self.assert_pages_fit_in_order(guarded, targets)

    def test_assignment_guard_stops_at_material_floor_and_refuses_invalid_floor(self):
        turns = build_turns(build_targets(1, 10000), notes=0, pool=[], target_minimum=0)
        self.assertIsNone(pages.prepare_final_turn(turns, 1, 7000, 1, 10000, condense_quarters))
        with self.assertRaises(ResearchError):
            pages.prepare_final_turn(turns, 1, 60000, 1, 0, condense_quarters)

    def test_singleton_recovery_requires_the_external_condensing_policy(self):
        with self.assertRaises(ResearchError):
            pages.split_final_turn(build_turns(build_targets(2, 30000)), 1, 6000, None)

    def test_repack_of_a_completed_final_preserves_the_entire_list(self):
        turns = build_turns(build_targets(2))
        self.assertEqual(pages.repack_final_turns(turns, [1], [], 480000, 2, condense=condense_quarters), turns)

    def test_repack_does_not_repeat_completed_decision_condensing(self):
        turns = build_turns(build_targets(3, 10000), target_allowance=1, closing_room=720, row_characters=360)
        repacked = pages.repack_final_turns(turns, [turn['number'] for turn in turns[:-1]], [], 480000, 3, condense=condense_quarters)
        self.assertEqual(count_turns(repacked)['condensing_turns'], count_turns(turns)['condensing_turns'])
        self.assertEqual(repacked[-1]['characters'], turns[-1]['characters'])

    def test_repack_after_a_split_keeps_the_completed_target_reduction(self):
        turns = build_turns(build_targets(1, 180000))
        completed = [turn['number'] for turn in turns if turn['kind'] == 'combine']
        split = pages.split_final_turn(turns, turns[-2]['number'], 6000, condense_quarters)
        repacked = pages.repack_final_turns(split, completed, [], 60000, 1, condense=condense_quarters)
        # The two split-produced reductions are now held, even before completion.
        self.assertEqual(count_turns(repacked)['condensing_turns'], 9)
        self.assertEqual(sum_entries(repacked[-2]['parts'][0]['entries']), 2812)
        self.assertTrue(all(turn in repacked for turn in turns if turn['number'] in completed))

    def test_repack_keeps_completed_row_reduction_before_pending_entry_reduction(self):
        turns = build_turns(build_targets(1, 30000, 800000))
        completed = [turn['number'] for turn in turns if turn['kind'] == 'combine' and turn['component'] == 'rows']
        repacked = pages.repack_final_turns(turns, completed, [], 60000, 1, condense=condense_quarters)
        new = [turn for turn in repacked if turn['kind'] == 'combine' and turn['number'] not in completed]
        self.assertEqual(len(new), 2)
        self.assertTrue(all(turn['component'] == 'entries' for turn in new))


class FinalCountTests(unittest.TestCase):
    def test_exact_count_uses_entries_and_target_allowance(self):
        result = pages.count_final_turns(**build_inputs(build_targets(3), target_allowance=2))
        self.assertEqual(result, dict(pages=2, condensing_turns=0, closing_pages=1, forecast=False))

    def test_forecast_is_marked_and_never_calls_the_exact_entry_planner(self):
        calls = []
        def forecast(characters, room, largest_entry):
            calls.append((characters, room, largest_entry))
            return 37
        inputs = build_inputs([dict(handle='target', entries=180000, rows=0)], pool=0)
        inputs.pop('condense')
        result = pages.count_final_turns(**inputs, forecast=forecast, largest_entry=6000)
        self.assertTrue(result['forecast'])
        self.assertEqual(result['condensing_turns'], 37)
        self.assertEqual(calls, [(180000, 40000, 6000)])

    def test_forecast_accepts_the_policy_marked_mapping_and_its_remaining_bound(self):
        def forecast(characters, room, largest_entry):
            return dict(forecast=True, turns=37, rounds=2, remaining_characters=10000)
        inputs = build_inputs([dict(handle='target', entries=180000, rows=0)], pool=0)
        inputs.pop('condense')
        result = pages.count_final_turns(**inputs, forecast=forecast, largest_entry=6000)
        self.assertEqual(result, dict(pages=1, condensing_turns=37, closing_pages=1, forecast=True))

    def test_forecast_refuses_invalid_limits_sizes_and_callback_counts(self):
        original = build_inputs([dict(handle='target', entries=180000, rows=0)], pool=0)
        original.pop('condense')
        for changes in (dict(target_allowance=0), dict(page_room=0), dict(notes=-1),
                        dict(pool=-1), dict(targets=[dict(handle='target', entries=-1, rows=0)])):
            with self.subTest(changes=changes), self.assertRaises(ResearchError):
                pages.count_final_turns(**dict(original, **changes), forecast=lambda c, r, e: 1, largest_entry=6000)
        for answer in (-1, True, {}, dict(forecast=True, turns=2, remaining_characters=50000)):
            with self.subTest(answer=answer), self.assertRaises(ResearchError):
                pages.count_final_turns(**original, forecast=lambda c, r, e: answer, largest_entry=6000)

    def test_forecast_keeps_target_counts_separate_from_condensing_turns(self):
        targets = [dict(handle='target:' + str(i), entries=180000, rows=0) for i in range(3)]
        inputs = build_inputs(targets, pool=0, target_allowance=2)
        inputs.pop('condense')
        result = pages.count_final_turns(**inputs, largest_entry=6000, forecast=lambda c, r, e: dict(
            forecast=True, turns=37, remaining_characters=10000))
        self.assertEqual(result, dict(pages=3, condensing_turns=111, closing_pages=1, forecast=True))

    def test_forecast_never_joins_isolated_reduced_targets(self):
        targets = [dict(handle='t' + str(i), entries=50000, rows=0) for i in range(3)]
        inputs = build_inputs(targets, pool=0)
        inputs.pop('condense')
        result = pages.count_final_turns(**inputs, largest_entry=1000, forecast=lambda c, r, e: dict(
            forecast=True, turns=2, remaining_characters=12500))
        self.assertEqual(result, dict(pages=3, condensing_turns=6, closing_pages=1, forecast=True))


class PagePlanTests(unittest.TestCase):
    def test_pending_and_held_groups_replay_at_current_limits_after_level_and_ratio(self):
        inputs = dict(build_inputs(build_targets(7, 1000)), floor=6000)
        read_inputs = lambda plan: copy.deepcopy(inputs)
        currency = dict(inventory_digest='inventory')
        first = pages.build_page_plan('chain', 'capacity', 'state', currency, build_turns(inputs['targets']))
        split = pages.split_final_turn(first['turns'], 1, 6000, condense_quarters)
        second = pages.build_page_plan('chain', 'capacity', 'state', currency, split, digest(first), dict(
            kind='split', turn=1, attempt_id='split', completed=[], pending=[]))
        pending = [split[0]['number']]
        level = pages.repack_final_turns(split, [], pending, 48000, 1, condense=condense_quarters)
        third = pages.build_page_plan('chain', 'capacity', 'state', currency, level, digest(second), dict(
            kind='level', attempt_id='level', page_room=48000, target_allowance=1, completed=[], pending=pending))
        ratio = pages.repack_final_turns(level, [], pending, 40000, 1, condense=condense_quarters)
        fourth = pages.build_page_plan('chain', 'capacity', 'state', currency, ratio, digest(third), dict(
            kind='ratio', attempt_id='ratio', page_room=40000, completed=[], pending=pending))
        ready = pages.prepare_final_turn(ratio, pending[0], 40000, 1, 6000, condense_quarters, pending=pending)
        fifth = pages.build_page_plan('chain', 'capacity', 'state', currency, ready, digest(fourth), dict(
            kind='split', turn=pending[0], attempt_id='prepare', completed=[], pending=pending))
        self.assertTrue(pages.verify_page_plans([first, second, third, fourth, fifth], read_inputs))
        self.assertEqual([len(turn['targets']) for turn in fifth['turns'] if turn['kind'] == 'page'], [1, 1, 1, 1, 3])

    def test_changed_pending_snapshot_and_unknown_split_turn_are_refused(self):
        inputs = dict(build_inputs(build_targets(9, 10000), target_allowance=2), floor=6000)
        first = pages.build_page_plan('chain', 'capacity', 'state', dict(inventory_digest='inventory'),
                                     build_turns(inputs['targets'], target_allowance=2))
        pending = [first['turns'][1]['number']]
        repacked = pages.repack_final_turns(first['turns'], [], pending, 480000, 9, condense=condense_quarters)
        second = pages.build_page_plan('chain', 'capacity', 'state', first['currency'], repacked, digest(first), dict(
            kind='level', attempt_id='level', page_room=480000, target_allowance=9, completed=[], pending=pending))
        self.assertTrue(pages.verify_page_plans([first, second], lambda plan: copy.deepcopy(inputs)))
        second['cause']['pending'] = []
        with self.assertRaises(ResearchError):
            pages.verify_page_plans([first, second], lambda plan: copy.deepcopy(inputs))
        bad = pages.build_page_plan('chain', 'capacity', 'state', first['currency'], first['turns'], digest(first), dict(
            kind='split', turn=999, attempt_id='missing', completed=[], pending=[]))
        with self.assertRaises(ResearchError):
            pages.verify_page_plans([first, bad], lambda plan: copy.deepcopy(inputs))

    def build_successors(self):
        targets = build_targets(9, 10000)
        states = {'state': dict(build_inputs(targets, target_allowance=2), floor=6000),
                  'grown': dict(build_inputs(build_targets(9, 12000), target_allowance=2), floor=6000)}
        def read_inputs(plan):
            return copy.deepcopy(states[plan['state_digest']])
        currency = dict(packet_digest='packet', inventory_digest='inventory', series_key='series')
        first = pages.build_page_plan('chain', 'capacity', 'state', currency, build_turns(targets, target_allowance=2))
        split = pages.split_final_turn(first['turns'], 2, 6000, condense_quarters)
        second = pages.build_page_plan('chain', 'capacity', 'state', currency, split, digest(first),
                                       dict(kind='split', turn=2, attempt_id='split-attempt', completed=[1], pending=[]))
        completed = [second['turns'][0]['number']]
        level = pages.repack_final_turns(split, completed, [], 60000, 1, condense=condense_quarters)
        third = pages.build_page_plan('chain', 'capacity', 'state', currency, level, digest(second), dict(
            kind='level', attempt_id='level-attempt', page_room=60000, target_allowance=1, completed=completed, pending=[]))
        completed = [turn['number'] for turn in third['turns'] if turn['kind'] == 'page'][:3]
        ratio = pages.repack_final_turns(level, completed, [], 48000, 1, condense=condense_quarters)
        fourth = pages.build_page_plan('chain', 'capacity', 'state', currency, ratio, digest(third), dict(
            kind='ratio', attempt_id='ratio-attempt', page_room=48000, completed=completed, pending=[]))
        new_currency = dict(currency, packet_digest='new-packet', inventory_digest='new-inventory')
        fifth = pages.build_page_plan('chain', 'capacity', 'grown', new_currency,
            build_turns(states['grown']['targets'], target_allowance=2), digest(fourth), dict(
                kind='inventory', replaced_inventory_digest='inventory', inventory_digest='new-inventory'))
        return [first, second, third, fourth, fifth], read_inputs

    def test_same_inputs_have_one_canonical_digest_and_no_initial_predecessor(self):
        plans, _ = self.build_successors()
        plan = plans[0]
        self.assertEqual(set(plan), {'protocol', 'chain', 'plan_digest', 'state_digest', 'currency', 'turns'})
        self.assertEqual(plan['protocol'], 'exactory-final-page-plan-v1')
        self.assertEqual(digest(plan), digest(json.loads(json.dumps(plan, sort_keys=True))))
        again = pages.build_page_plan('chain', 'capacity', 'state', plan['currency'], plan['turns'])
        self.assertEqual(digest(plan), digest(again))
        again['currency']['inventory_digest'] = 'mutated'
        self.assertEqual(plan['currency']['inventory_digest'], 'inventory')

    def test_verifies_the_initial_plan_without_optional_context(self):
        plans, read_inputs = self.build_successors()
        self.assertTrue(pages.verify_page_plans(plans[:1], read_inputs))

    def test_verifies_two_successors_from_their_causes_and_authenticated_assignments(self):
        plans, read_inputs = self.build_successors()
        before = copy.deepcopy(plans)
        self.assertTrue(pages.verify_page_plans(plans[:3], read_inputs))
        self.assertTrue(pages.verify_page_plans(plans[:4], read_inputs))
        self.assertEqual(digest(plans[2]), digest(before[2]))
        self.assertEqual(plans, before)

    def test_verifies_all_four_causes_and_inventory_restarts_every_target(self):
        plans, read_inputs = self.build_successors()
        self.assertTrue(pages.verify_page_plans(plans, read_inputs))
        self.assertNotEqual(plans[-1]['currency'], plans[-2]['currency'])
        self.assertEqual(plans[-1]['cause']['replaced_inventory_digest'], 'inventory')
        self.assertFalse(any(turn == plans[-2]['turns'][0] for turn in plans[-1]['turns']))

    def test_verifies_singleton_split_with_the_callers_policy(self):
        inputs = dict(build_inputs(build_targets(2, 30000)), floor=6000)
        turns, currency = build_turns(inputs['targets']), dict(inventory_digest='inventory')
        first = pages.build_page_plan('chain', 'capacity', 'state', currency, turns)
        split = pages.split_final_turn(turns, 1, 6000, condense_quarters)
        second = pages.build_page_plan('chain', 'capacity', 'state', currency, split, digest(first),
                                       dict(kind='split', turn=1, attempt_id='attempt', completed=[], pending=[]))
        self.assertTrue(pages.verify_page_plans([first, second], lambda plan: copy.deepcopy(inputs)))

    def test_refuses_changed_turn_changed_cause_missing_predecessor_and_wrong_roots(self):
        original, read_inputs = self.build_successors()
        for mutation in ('turn', 'cause', 'completed', 'predecessor', 'chain', 'capacity', 'extra', 'currency', 'inventory'):
            plans = copy.deepcopy(original)
            if mutation == 'turn':
                plans[1]['turns'][0]['characters'] -= 1
            elif mutation == 'cause':
                plans[1]['cause']['turn'] = 3
            elif mutation == 'completed':
                plans[2]['cause']['completed'] = []
            elif mutation == 'predecessor':
                plans.pop(1)
            elif mutation == 'extra':
                plans[1]['unbound'] = True
            elif mutation == 'currency':
                plans[2]['currency']['inventory_digest'] = 'unexpected'
            elif mutation == 'inventory':
                plans[-1]['cause']['replaced_inventory_digest'] = 'not-the-predecessor'
            else:
                plans[1][{'chain': 'chain', 'capacity': 'plan_digest'}[mutation]] = 'another-binding'
            with self.subTest(mutation=mutation), self.assertRaises(ResearchError) as caught:
                pages.verify_page_plans(plans, read_inputs)
            self.assertEqual(caught.exception.code, 'artifact_corrupt')

    def test_refuses_a_changed_first_plan_empty_list_and_missing_predecessor(self):
        plans, read_inputs = self.build_successors()
        for value in ([], plans[1:]):
            with self.assertRaises(ResearchError):
                pages.verify_page_plans(value, read_inputs)
        changed = copy.deepcopy(plans[:1])
        changed[0]['turns'][0]['targets'].reverse()
        with self.assertRaises(ResearchError):
            pages.verify_page_plans(changed, read_inputs)

    def test_refuses_canonically_different_boolean_and_float_numbers(self):
        plans, read_inputs = self.build_successors()
        for number in (True, 1.0):
            changed = copy.deepcopy(plans[:1])
            changed[0]['turns'][0]['number'] = number
            with self.subTest(number=number), self.assertRaises(ResearchError):
                pages.verify_page_plans(changed, read_inputs)

    def test_refuses_malformed_causes_and_unpaired_replacement_arguments(self):
        turns, currency = build_turns(build_targets(2)), dict(inventory_digest='inventory')
        causes = [None, dict(kind='unknown'), dict(kind='level', attempt_id='a', page_room=0,
                                                 target_allowance=1, completed=[], pending=[]),
                  dict(kind='split', turn=1, attempt_id='a'),
                  dict(kind='ratio', attempt_id='a', page_room=60000, completed=[True], pending=[])]
        for cause in causes:
            with self.subTest(cause=cause), self.assertRaises(ResearchError):
                pages.build_page_plan('chain', 'capacity', 'state', currency, turns, 'digest', cause)
        with self.assertRaises(ResearchError):
            pages.build_page_plan('chain', 'capacity', 'state', currency, turns, cause=dict(
                kind='split', turn=1, attempt_id='a', completed=[], pending=[]))


class FinalIncidentTests(FinalPageAssertions, unittest.TestCase):
    def test_all_acceptance_shapes_have_bounded_pages_at_both_rooms(self):
        for shape, targets in build_acceptance_shapes().items():
            for room in (60000, 480000):
                with self.subTest(shape=shape, room=room):
                    turns = build_turns(targets, room)
                    self.assert_pages_fit_in_order(turns, targets)
                    self.assertGreater(count_turns(turns)['pages'], 0)
                    self.assertEqual(count_turns(turns)['closing_pages'], 1)

    def test_fifty_seven_turn_final_has_a_bounded_page_plan_at_both_rooms(self):
        # Synthetic serialized sizes only, from the named November 12 incident.
        targets = build_targets(10, (624737 + 108165 + 9) // 10)
        for room in (60000, 480000):
            turns = build_turns(targets, room, notes=7797, pool=[])
            self.assert_pages_fit_in_order(turns, targets)
            self.assertEqual(turns[-1]['kind'], 'closing')


if __name__ == '__main__':
    unittest.main()
