"""Pure final allocation, ordered page plans and authenticated replacements."""

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
    return [dict(id='t' + str(index), minimum=6700 + rows,
                 preferred=30000 + rows, maximum=60000 + rows, overhead=rows)
            for index in range(count)]


def condense_quarters(characters, room):
    """Synthetic rule, independent of the future production condensing module."""
    turns = 0
    while characters > room:
        turns += (characters + room - 1) // room
        characters = (characters + 3) // 4
    return turns, characters


def build_targets(count, entries=300, rows=0):
    return [dict(handle='t' + str(index), entries=entries, rows=rows) for index in range(count)]


def build_turns(targets, room=60000, **changes):
    arguments = dict(page_room=room, notes=20000, pool=1000, targets=targets,
                     closing_room=1200000, row_characters=360, condense=condense_quarters)
    arguments.update(changes)
    return pages.plan_final_turns(**arguments)


def build_acceptance_shapes():
    def build_mixed(count):
        return [dict(handle='t' + str(i), entries=300 + (i % 100) * 300, rows=0) for i in range(count)]

    return {'5000_targets': build_mixed(5000), '20000_targets': build_mixed(20000),
            '2000_gaps': build_targets(1, 30000, 2000 * 400),
            'one_target_all_units': build_targets(1, 10000 * capacity.CARD_ENTRY_CHARACTERS)}


class FinalPageAssertions:
    def assert_pages_fit_in_order(self, turns, targets):
        delivered = []
        numbers = []
        for turn in turns:
            numbers.append(turn['number'])
            if turn['kind'] in ('combine', 'closing'):
                context = turn['context']
                room = context['closing_room'] if turn['kind'] == 'closing' or turn['scope'] == 'decisions' else context['page_room']
                self.assertLessEqual(turn['characters'], room)
            if turn['kind'] not in ('page', 'final'):
                continue
            context = turn['context']
            sizes = [context['notes'], context['pool']]
            sizes.extend(part['entries'] + part['rows'] for part in turn['parts'])
            division = capacity.divide_room(context['page_room'], [
                dict(minimum=size, preferred=size, maximum=size) for size in sizes])
            self.assertEqual(division['minimum'], turn['characters'])
            self.assertEqual([part['handle'] for part in turn['parts']], turn['targets'])
            delivered.extend(turn['targets'])
        self.assertEqual(delivered, [target['handle'] for target in targets])
        self.assertEqual(len(numbers), len(set(numbers)))


class FinalTurnTests(FinalPageAssertions, unittest.TestCase):
    def test_exact_fit_remains_one_final_without_a_closing_page(self):
        targets = build_targets(3, 13000)
        turns = build_turns(targets)
        self.assertEqual([turn['kind'] for turn in turns], ['final'])
        self.assertEqual(turns[0]['characters'], 60000)
        self.assert_pages_fit_in_order(turns, targets)
        self.assertEqual(pages.count_final_turns(turns), dict(pages=1, condensing_turns=0, closing_pages=0))

    def test_nine_targets_can_use_every_granted_entry_character(self):
        allocation = pages.allocate_final_targets(60000, build_notes(), build_target_parts(9))
        targets = [dict(handle=row['id'], entries=row['allowance'], rows=0) for row in allocation['targets']]
        turns = build_turns(targets)
        self.assert_pages_fit_in_order(turns, targets)
        self.assertEqual(pages.count_final_turns(turns), dict(pages=9, condensing_turns=0, closing_pages=1))

    def test_five_thousand_targets_pack_mixed_serialized_sizes(self):
        targets = [dict(handle='t' + str(i), entries=300 + (i % 100) * 300, rows=0) for i in range(5000)]
        for room in (60000, 480000):
            with self.subTest(room=room):
                self.assert_pages_fit_in_order(build_turns(targets, room), targets)

    def test_twenty_thousand_targets_pack_mixed_serialized_sizes(self):
        targets = [dict(handle='t' + str(i), entries=300 + (i % 100) * 300, rows=0) for i in range(20000)]
        for room in (60000, 480000):
            with self.subTest(room=room):
                self.assert_pages_fit_in_order(build_turns(targets, room), targets)

    def test_one_target_three_times_the_room_is_condensed_before_its_page(self):
        targets = build_targets(1, 180000)
        turns = build_turns(targets)
        # 180,000 -> 45,000 -> 11,250 in five then two blocks at room 39,000.
        self.assertEqual([turn['kind'] for turn in turns], ['combine'] * 7 + ['page', 'closing'])
        self.assertEqual(turns[-2]['characters'], 32250)
        self.assert_pages_fit_in_order(turns, targets)

    def test_two_thousand_gap_rows_are_condensed_before_their_page(self):
        targets = build_targets(1, 30000, rows=2000 * 400)
        turns = build_turns(targets)
        self.assertEqual(turns[0]['scope'], 'rows')
        self.assertLessEqual(turns[-2]['parts'][0]['rows'], 15000)
        self.assert_pages_fit_in_order(turns, targets)

    def test_twenty_thousand_decisions_use_the_injected_count_before_closing(self):
        def condense_seventeen(characters, room):
            self.assertEqual((characters, room), (7200000, 1200000))
            return 17, 1100000

        targets = build_targets(20000)
        turns = build_turns(targets, condense=condense_seventeen)
        self.assertEqual([turn['kind'] for turn in turns[-18:]], ['combine'] * 17 + ['closing'])
        self.assertTrue(all(turn['scope'] == 'decisions' for turn in turns[-18:-1]))
        self.assertEqual(turns[-1]['characters'], 1100000)
        self.assertEqual(pages.count_final_turns(turns)['condensing_turns'], 17)

    def test_empty_targets_and_zero_sizes_still_have_one_final(self):
        turns = build_turns([], notes=0, pool=0)
        self.assertEqual([turn['kind'] for turn in turns], ['final'])
        self.assertEqual(turns[0]['characters'], 0)

    def test_shared_notes_and_pool_leave_no_room_for_a_target(self):
        with self.assertRaises(ResearchError) as caught:
            build_turns(build_targets(1), notes=60000)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')

    def test_condensing_must_return_a_positive_count_and_a_fitting_bound(self):
        for answer in ((0, 300), (2, 60000), (-1, 0), (1, -1), None, (1,), (1, 0, 2)):
            with self.subTest(answer=answer):
                with self.assertRaises(ResearchError) as caught:
                    build_turns(build_targets(1, 180000), condense=lambda characters, room: answer)
                self.assertEqual(caught.exception.code, 'invalid_input')

    def test_planning_does_not_mutate_or_alias_the_input_sizes(self):
        targets = build_targets(3, 30000)
        before = copy.deepcopy(targets)
        turns = build_turns(targets)
        self.assertEqual(targets, before)
        turns[0]['inputs'][0]['entries'] = 0
        self.assertEqual(targets, before)

    def test_many_zero_size_targets_fit_without_a_per_target_scan_of_the_page(self):
        targets = build_targets(20000, 0)
        turns = build_turns(targets)
        self.assertEqual([turn['kind'] for turn in turns], ['final'])
        self.assert_pages_fit_in_order(turns, targets)

    def test_invalid_sizes_and_repeated_handles_refuse_at_the_planner_edge(self):
        for changes in (dict(page_room=-1), dict(notes=-1), dict(pool=-1),
                        dict(closing_room=0), dict(row_characters=-1),
                        dict(targets=build_targets(1, -1)),
                        dict(targets=build_targets(1, rows=-1)),
                        dict(targets=build_targets(1) * 2)):
            arguments = dict(targets=build_targets(2))
            arguments.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ResearchError) as caught:
                    build_turns(**arguments)
                self.assertEqual(caught.exception.code, 'invalid_input')


class FinalReplacementTests(FinalPageAssertions, unittest.TestCase):
    def test_split_seven_targets_into_four_then_three_without_mutating_the_input(self):
        targets = build_targets(14, 5500)
        turns = build_turns(targets, pool=0)
        before = copy.deepcopy(turns)
        split = pages.split_final_turn(turns, turns[0]['number'])
        self.assertEqual([len(turn['targets']) for turn in split if turn['kind'] == 'page'], [4, 3, 7])
        self.assertEqual(turns, before)
        self.assertEqual(split[-1], turns[-1])
        self.assert_pages_fit_in_order(split, targets)

    def test_split_singleton_inserts_condensing_turns_then_the_page(self):
        targets = build_targets(2, 30000)
        turns = build_turns(targets)
        split = pages.split_final_turn(turns, turns[0]['number'], condense=condense_quarters)
        self.assertEqual([turn['kind'] for turn in split[:3]], ['combine', 'combine', 'page'])
        self.assertEqual(split[2]['characters'], 28500)
        self.assert_pages_fit_in_order(split, targets)

    def test_split_final_inserts_two_pages_and_one_closing_page(self):
        targets = build_targets(7, 1000)
        turns = build_turns(targets)
        split = pages.split_final_turn(turns, 1)
        self.assertEqual([turn['kind'] for turn in split], ['page', 'page', 'closing'])
        self.assertEqual([len(turn['targets']) for turn in split[:2]], [4, 3])
        self.assertEqual(split[-1]['characters'], 2520)
        self.assert_pages_fit_in_order(split, targets)

    def test_split_single_target_final_condenses_without_an_empty_second_page(self):
        targets = build_targets(1, 1000)
        split = pages.split_final_turn(build_turns(targets), 1, condense=condense_quarters)
        self.assertEqual([turn['kind'] for turn in split], ['combine', 'combine', 'page', 'closing'])
        self.assert_pages_fit_in_order(split, targets)

    def test_split_refuses_unknown_nonpage_and_empty_final(self):
        turns = build_turns(build_targets(9, 30000))
        for number in (999, turns[-1]['number']):
            with self.subTest(number=number), self.assertRaises(ResearchError) as caught:
                pages.split_final_turn(turns, number)
            self.assertEqual(caught.exception.code, 'invalid_input')
        with self.assertRaises(ResearchError):
            pages.split_final_turn(build_turns([]), 1)

    def test_repack_preserves_assigned_turns_and_reuses_every_other_target_once(self):
        targets = build_targets(25, 10000)
        turns = build_turns(targets, room=480000)
        turns = pages.split_final_turn(turns, 1)
        assigned = [turns[0]['number']]
        before = copy.deepcopy(turns)
        repacked = pages.repack_final_turns(turns, assigned, 60000, condense=condense_quarters)
        self.assertEqual(repacked[0], turns[0])
        self.assertEqual(turns, before)
        self.assert_pages_fit_in_order(repacked, targets)

    def test_repack_preserves_nonprefix_assigned_page_and_completed_closing(self):
        targets = build_targets(20, 10000)
        turns = build_turns(targets)
        assigned = [turns[2]['number'], turns[-1]['number']]
        repacked = pages.repack_final_turns(turns, assigned, 480000, condense=condense_quarters)
        for turn in turns:
            if turn['number'] in assigned:
                self.assertIn(turn, repacked)
        self.assert_pages_fit_in_order(repacked, targets)
        self.assertEqual(pages.count_final_turns(repacked)['closing_pages'], 1)

    def test_repack_refuses_unknown_assigned_turns(self):
        for assigned in ([999], [1, 1]):
            with self.subTest(assigned=assigned):
                with self.assertRaises(ResearchError) as caught:
                    pages.repack_final_turns(build_turns(build_targets(2)), assigned, 60000)
                self.assertEqual(caught.exception.code, 'invalid_input')

    def test_singleton_recovery_requires_the_external_condensing_policy(self):
        turns = build_turns(build_targets(2, 30000))
        with self.assertRaises(ResearchError) as caught:
            pages.split_final_turn(turns, 1)
        self.assertEqual(caught.exception.code, 'invalid_input')

    def test_repack_of_a_completed_final_preserves_the_entire_list(self):
        turns = build_turns(build_targets(2))
        self.assertEqual(pages.repack_final_turns(turns, [1], 480000), turns)

    def test_repack_does_not_repeat_completed_decision_condensing(self):
        turns = build_turns(build_targets(2, 8), room=10, notes=0, pool=0,
                            closing_room=5, row_characters=4, condense=lambda characters, room: (1, 2))
        assigned = [turn['number'] for turn in turns[:-1]]
        repacked = pages.repack_final_turns(turns, assigned, 20,
                                           condense=lambda characters, room: (1, 2))
        self.assertEqual(pages.count_final_turns(repacked), dict(pages=2, condensing_turns=1, closing_pages=1))
        self.assertEqual(repacked[-1]['characters'], 2)

    def test_repack_after_a_split_keeps_the_completed_target_reduction(self):
        turns = build_turns(build_targets(1, 180000))
        assigned = [turn['number'] for turn in turns if turn['kind'] == 'combine']
        split = pages.split_final_turn(turns, turns[-2]['number'], condense=condense_quarters)
        repacked = pages.repack_final_turns(split, assigned, 60000, condense=condense_quarters)
        self.assertEqual(pages.count_final_turns(repacked)['condensing_turns'], 7)
        self.assertEqual(repacked[-2]['parts'][0]['entries'], 11250)
        self.assertTrue(all(turn in repacked for turn in turns if turn['number'] in assigned))

    def test_repack_keeps_completed_row_reduction_before_pending_entry_reduction(self):
        turns = build_turns(build_targets(1, 30000, 800000))
        assigned = [turn['number'] for turn in turns if turn['kind'] == 'combine' and turn['scope'] == 'rows']
        repacked = pages.repack_final_turns(turns, assigned, 60000, condense=condense_quarters)
        new_combinations = [turn for turn in repacked if turn['kind'] == 'combine' and turn['number'] not in assigned]
        self.assertEqual(len(new_combinations), 2)
        self.assertTrue(all(turn['scope'] == 'entries' for turn in new_combinations))


class PagePlanTests(unittest.TestCase):
    def build_successors(self):
        turns = build_turns(build_targets(30, 5500), pool=0)
        first = pages.build_page_plan('chain', 'capacity-digest', 'state-digest', turns)
        split = pages.split_final_turn(turns, 1)
        second = pages.build_page_plan('chain', 'capacity-digest', 'state-digest', split,
                                       replaces=digest(first),
                                       cause=dict(kind='split', turn=1, attempt_id='failed-attempt'))
        assigned = [split[0]['number']]
        repacked = pages.repack_final_turns(split, assigned, 480000)
        third = pages.build_page_plan('chain', 'capacity-digest', 'state-digest', repacked,
                                      replaces=digest(second),
                                      cause=dict(kind='level', attempt_id='level-attempt', page_room=480000))
        return [first, second, third], turns, {'level-attempt': assigned}

    def test_same_inputs_have_one_canonical_digest_and_no_initial_predecessor(self):
        turns = build_turns(build_targets(7, 1000))
        plan = pages.build_page_plan('chain', 'capacity-digest', 'state-digest', turns)
        self.assertEqual(set(plan), {'protocol', 'chain', 'plan_digest', 'state_digest', 'turns'})
        self.assertEqual(plan['protocol'], 'exactory-final-page-plan-v1')
        serialized = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        self.assertEqual(digest(plan), digest(json.loads(serialized)))
        self.assertEqual(digest(plan), digest(pages.build_page_plan('chain', 'capacity-digest', 'state-digest', turns)))
        turns[0]['targets'].clear()
        self.assertEqual(len(plan['turns'][0]['targets']), 7)

    def test_verifies_the_initial_plan_without_optional_context(self):
        plans, turns, _ = self.build_successors()
        self.assertTrue(pages.verify_page_plans(plans[:1], turns))

    def test_verifies_two_successors_from_their_causes_and_authenticated_assignments(self):
        plans, turns, assigned = self.build_successors()
        before = copy.deepcopy(plans)
        self.assertTrue(pages.verify_page_plans(plans, turns, assigned=assigned))
        self.assertEqual(plans, before)

    def test_verifies_singleton_split_with_the_callers_policy(self):
        turns = build_turns(build_targets(2, 30000))
        first = pages.build_page_plan('chain', 'capacity-digest', 'state-digest', turns)
        split = pages.split_final_turn(turns, 1, condense=condense_quarters)
        second = pages.build_page_plan('chain', 'capacity-digest', 'state-digest', split,
                                       replaces=digest(first), cause=dict(kind='split', turn=1, attempt_id='attempt'))
        self.assertTrue(pages.verify_page_plans([first, second], turns, condense=condense_quarters))

    def test_refuses_changed_turn_changed_cause_missing_predecessor_and_wrong_roots(self):
        original, turns, assigned = self.build_successors()
        for mutation in ('turn', 'cause', 'predecessor', 'chain', 'capacity', 'state', 'extra'):
            plans = copy.deepcopy(original)
            if mutation == 'turn':
                plans[1]['turns'][0]['characters'] -= 1
            elif mutation == 'cause':
                plans[1]['cause']['turn'] = 2
            elif mutation == 'predecessor':
                plans.pop(1)
            elif mutation == 'extra':
                plans[1]['unbound'] = True
            else:
                field = {'chain': 'chain', 'capacity': 'plan_digest', 'state': 'state_digest'}[mutation]
                plans[1][field] = 'another-binding'
            with self.subTest(mutation=mutation), self.assertRaises(ResearchError) as caught:
                pages.verify_page_plans(plans, turns, assigned=assigned)
            self.assertEqual(caught.exception.code, 'artifact_corrupt')

    def test_refuses_a_changed_first_plan_empty_list_and_missing_assignment_witness(self):
        plans, turns, _ = self.build_successors()
        with self.assertRaises(ResearchError):
            pages.verify_page_plans(plans[1:], turns)
        with self.assertRaises(ResearchError):
            pages.verify_page_plans([], turns)
        with self.assertRaises(ResearchError):
            pages.verify_page_plans(plans, turns)
        changed = copy.deepcopy(plans[:1])
        changed[0]['turns'][0]['targets'].reverse()
        with self.assertRaises(ResearchError):
            pages.verify_page_plans(changed, turns)

    def test_refuses_canonically_different_boolean_and_float_numbers(self):
        turns = build_turns(build_targets(2))
        first = pages.build_page_plan('chain', 'capacity', 'state', turns)
        for number in (True, 1.0):
            changed = copy.deepcopy(first)
            changed['turns'][0]['number'] = number
            self.assertNotEqual(digest(changed), digest(first))
            with self.subTest(number=number), self.assertRaises(ResearchError) as caught:
                pages.verify_page_plans([changed], turns)
            self.assertEqual(caught.exception.code, 'artifact_corrupt')

    def test_refuses_malformed_causes_and_unpaired_replacement_arguments(self):
        turns = build_turns(build_targets(2))
        cases = [(None, dict(kind='split', turn=1, attempt_id='a')),
                 ('digest', None),
                 ('digest', dict(kind='unknown', attempt_id='a')),
                 ('digest', dict(kind='split', attempt_id='a')),
                 ('digest', dict(kind='level', attempt_id='a', page_room=0))]
        for replaces, cause in cases:
            with self.subTest(cause=cause), self.assertRaises(ResearchError) as caught:
                pages.build_page_plan('chain', 'capacity', 'state', turns, replaces=replaces, cause=cause)
            self.assertEqual(caught.exception.code, 'invalid_input')


class FinalIncidentTests(FinalPageAssertions, unittest.TestCase):
    def test_all_acceptance_shapes_have_bounded_pages_at_both_rooms(self):
        for shape, targets in build_acceptance_shapes().items():
            for room in (60000, 480000):
                with self.subTest(shape=shape, room=room):
                    turns = build_turns(targets, room)
                    self.assert_pages_fit_in_order(turns, targets)
                    counts = pages.count_final_turns(turns)
                    self.assertGreater(counts['pages'], 0)
                    self.assertEqual(counts['closing_pages'], 1)

    def test_fifty_seven_turn_final_has_a_bounded_page_plan_at_both_rooms(self):
        # Shape and serialized sizes only, from the named November 12 incident.
        # The refused 1,303,075-character final included notes 7,797, excerpts
        # 624,737 and passages 108,165, for ten targets; earlier turns add no term.
        targets = build_targets(10, (624737 + 108165 + 9) // 10)
        for room in (60000, 480000):
            with self.subTest(room=room):
                turns = build_turns(targets, room, notes=7797, pool=0)
                self.assert_pages_fit_in_order(turns, targets)
                self.assertEqual(turns[-1]['kind'], 'closing')


class FinalAllocationTests(unittest.TestCase):
    def test_eight_targets_keep_the_existing_single_room_division(self):
        notes, targets = build_notes(), build_target_parts(8)
        before = copy.deepcopy((notes, targets))
        result = pages.allocate_final_targets(60000, notes, targets)
        self.assertEqual(result['form'], 'request')
        self.assertEqual(result['division'], capacity.divide_room(60000, [notes] + targets))
        self.assertEqual((notes, targets), before)

    def test_nine_targets_get_the_preferred_thirty_thousand_each(self):
        result = pages.allocate_final_targets(60000, build_notes(), build_target_parts(9))
        self.assertEqual(result['form'], 'pages')
        self.assertEqual([part['allowance'] for part in result['targets']], [30000] * 9)
        self.assertEqual([part['id'] for part in result['targets']], ['t' + str(i) for i in range(9)])

    def test_seventy_targets_fit_the_large_room_without_changing_the_division(self):
        notes, targets = build_notes(), build_target_parts(70)
        result = pages.allocate_final_targets(480000, notes, targets)
        self.assertEqual(result['form'], 'request')
        self.assertEqual(result['division'], capacity.divide_room(480000, [notes] + targets))

    def test_oversized_gap_rows_are_marked_and_keep_a_target_allowance(self):
        result = pages.allocate_final_targets(60000, build_notes(), build_target_parts(1, 800000))
        target = result['targets'][0]
        self.assertEqual(result['form'], 'pages')
        self.assertTrue(target['condense_rows'])
        self.assertEqual(target['rows'], 15000)
        self.assertEqual(target['allowance'], 30000)
        self.assertGreaterEqual(target['allowance'], 6700 + target['rows'])

    def test_minimum_above_half_a_page_still_refuses_below_floor(self):
        with self.assertRaises(ResearchError) as caught:
            pages.allocate_final_targets(10000, build_notes(), build_target_parts(2))
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')


if __name__ == '__main__':
    unittest.main()
