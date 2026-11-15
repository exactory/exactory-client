"""Divisible content, separate entry reserves and sized reads (finding 4)."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_harness import review_condensing as condensing, review_parts_v3 as parts
from research_harness import review_route_capacity as capacity
from research_harness.artifacts import ArtifactStore
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_route_capacity import build_route
from test_review_condensing import build_combination, measure


BASE_PLAN_PATH = Path(__file__).parent / 'fixtures/review-parts-v3-base-plan.json'


def assert_inventory_coverage(case, inventory, groups):
    indices = {unit['handle']: index for index, unit in enumerate(inventory)}
    prefix = [0]
    for unit in inventory:
        prefix.append(prefix[-1] + unit['characters'])
    index, offset, delivered = 0, 0, 0
    for group in groups:
        case.assertEqual(group['first'], dict(unit=inventory[index]['handle'], offset=offset))
        last = indices[group['last']['unit']]
        case.assertEqual(group['unit_count'], last - index + 1)
        size = prefix[last] - prefix[index] - offset + group['last']['offset']
        case.assertEqual(size, group['unit_characters'])
        case.assertEqual(group['delivered_before'], delivered)
        delivered += size
        case.assertEqual(group['delivered_through'], delivered)
        if group['last']['offset'] == inventory[last]['characters']:
            index, offset = last + 1, 0
        else:
            index, offset = last, group['last']['offset']
    case.assertEqual((index, offset, delivered), (len(inventory), 0, prefix[-1]))


def build_small_plan(rate=0.01, entry=300, start=30000, room=60000):
    levels, value = [], start
    while value > 15000:
        levels.append(dict(material_allowance=value, answer_bound=80000))
        value //= 2
    levels.append(dict(material_allowance=15000, answer_bound=80000))
    return dict(allocation=dict(card_rate=rate, entry_sizes=dict(card=entry),
                                rooms=dict(combine=room), page_room=60000,
                                card_budget=1000000, card_reserve=1000000,
                                largest_card_count=10000, late_unit_reserve=10000),
                task=dict(read=dict(material_allowance=start, answer_bound=80000,
                                    levels=levels),
                          combine=dict(material_allowance=room, levels=[dict(material_allowance=room)])))


def build_fixture_plan(state, small_units=False):
    route = build_route(model='unlisted-model' if state == 'unmeasured' else 'claude-opus-5-5',
                        effort='max' if state == 'over_limit_expected' else 'xhigh',
                        output=32000 if state == 'unmeasured' else 128000)
    profile = capacity.read_output_profile({}, route)
    if state == 'over_limit_expected':
        row = copy.deepcopy(next(row for row in profile['rows']
                                 if row['kind'] == 'reading' and row['task_level'] == 240000))
        row['kind'] = 'read'
        profile = capacity.read_output_profile({}, route, priors=[row])
    limit = capacity.describe_route_capacity(route, profile)['input_text_characters']['value']
    total = 2000000 if small_units else 30 * limit
    inventory = [dict(handle='u' + str(index), kind='text',
                      characters=total // 10000 + (index < total % 10000)) for index in range(10000)]
    plan = capacity.plan_chain_capacity(route, profile,
        frames={kind: dict(instructions=3000) for kind in ('read', 'combine', 'check', 'final')},
        inventory=inventory, targets=[])
    return plan, inventory


class CardAllowanceTests(unittest.TestCase):
    def test_public_bounds_read_the_pinned_turn_and_card_limits(self):
        plan = build_small_plan(start=60000)
        before = copy.deepcopy(plan)
        self.assertEqual(parts.describe_card_bounds(plan, 60000),
                         dict(turn_characters=80000, card_characters=60000))
        plan['task']['read']['levels'][1]['answer_bound'] = 30000
        self.assertEqual(parts.describe_card_bounds(plan, 30000),
                         dict(turn_characters=30000, card_characters=30000))
        plan['task']['read']['answer_bound'] = 100000
        self.assertEqual(parts.describe_card_bounds(plan, 60000),
                         dict(turn_characters=80000, card_characters=60000))
        self.assertEqual(before['allocation'], plan['allocation'])
        with self.assertRaises(ResearchError) as caught:
            parts.describe_card_bounds(plan, 12345)
        self.assertEqual(caught.exception.code, 'invalid_input')

    def test_allowance_sums_the_parts_own_intervals_and_separate_reserves(self):
        plan = build_small_plan(rate=0.5)
        given = [dict(delivered_before=0, delivered_through=1, content_characters=999),
                 dict(delivered_before=9, delivered_through=10, late=True, content_characters=999)]
        before = copy.deepcopy((plan, given))
        self.assertEqual(parts.compute_card_allowance(plan, groups=given),
                         dict(content_characters=1, entry_characters=600, characters=601,
                              card_budget=0, card_reserve=300, late_unit_reserve=301))
        self.assertEqual((plan, given), before)
        self.assertEqual(parts.compute_card_allowance(plan, []),
                         dict(content_characters=0, entry_characters=0, characters=0,
                              card_budget=0, card_reserve=0, late_unit_reserve=0))

    def test_revised_examples_keep_full_allocations_and_reserves_at_every_level(self):
        floor_plan = build_small_plan(rate=5, entry=2200, start=15000)
        floor_plan['allocation'].update(card_budget=75000, largest_card_count=1, card_reserve=2200)
        answer_plan = build_small_plan(rate=1.25, entry=2500, start=60000)
        answer_plan['allocation'].update(card_budget=75000, largest_card_count=4, card_reserve=10000)
        answer_plan['task']['read']['answer_bound'] = 30000
        for row in answer_plan['task']['read']['levels']:
            row['answer_bound'] = 30000
        base_raw = BASE_PLAN_PATH.read_bytes()
        base = json.loads(base_raw)
        base_digest = digest(base)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            artifacts = ArtifactStore(Path(root))
            reference = artifacts.put(base_raw, 'application/json')
            cases = ((floor_plan, 15000, 57800), (answer_plan, 60000, 75000), (base, 450000, 338999))
            for plan, characters, expected_content in cases:
                before = copy.deepcopy(plan)
                units = [dict(handle='u', characters=characters)]
                levels = [row['material_allowance'] for row in plan['task']['read']['levels']]
                floor = min(levels)
                for level in levels:
                    with self.subTest(characters=characters, level=level):
                        turns = parts.plan_read_turns(plan, units, material_allowance=level)
                        given = [part for turn in turns for part in turn['cards']]
                        self.assertEqual(sum(part['content_characters'] for part in given), expected_content)
                        self.assertLessEqual(len(given), plan['allocation']['largest_card_count'])
                        self.assertLessEqual(sum(part['entry_characters'] for part in given),
                                             plan['allocation']['card_reserve'])
                        bounds = parts.describe_card_bounds(plan, level)
                        for turn in turns:
                            allowance = parts.compute_card_allowance(plan, turn['cards'])
                            self.assertEqual(turn['allowance'], allowance)
                            self.assertLessEqual(allowance['characters'], bounds['turn_characters'])
                        for part in given:
                            content = parts.compute_content_allocation(plan, part['delivered_before'],
                                                                       part['delivered_through'])
                            self.assertEqual(part['content_characters'], content)
                            self.assertEqual(part['characters'], content + plan['allocation']['entry_sizes']['card'])
                            self.assertLessEqual(part['characters'], bounds['card_characters'])
                            if part['last']['offset'] < characters:
                                self.assertGreaterEqual(part['unit_characters'], floor)
                            complete = dict(ref=part['handle'], summary='')
                            complete['summary'] = 's' * (part['characters'] - measure(complete))
                            self.assertEqual(measure(complete), part['characters'])
                        midpoint = characters // 2
                        self.assertEqual(parts.compute_content_allocation(plan, 0, midpoint) +
                                         parts.compute_content_allocation(plan, midpoint, characters), expected_content)
                        split = len(given) // 2
                        self.assertEqual(parts.compute_card_allowance(plan, given[:split])['content_characters'] +
                                         parts.compute_card_allowance(plan, given[split:])['content_characters'],
                                         expected_content)
                        self.assertEqual(plan, before)
                        self.assertEqual(artifacts.read(reference), base_raw)
                        self.assertEqual(digest(base), base_digest)
                if plan is floor_plan:
                    self.assertEqual(len(given), 1)
                    self.assertEqual(given[0]['characters'], 60000)

    def test_integer_content_allocations_telescope_at_arbitrary_boundaries(self):
        plan = build_small_plan(rate=0.1234567, start=600000)
        self.assertEqual(parts.read_content_rate(plan), Fraction(1234567, 10000000))
        whole = parts.compute_content_allocation(plan, 0, 10001)
        first = parts.compute_content_allocation(plan, 0, 5000)
        second = parts.compute_content_allocation(plan, 5000, 10001)
        self.assertEqual((whole, first, second), (1234, 617, 617))
        self.assertEqual(first + second, whole)
        self.assertEqual(parts.compute_content_allocation(plan, 11, 10012), 1235)

    def test_repeated_level_division_keeps_content_and_the_base_plan_artifact(self):
        raw = BASE_PLAN_PATH.read_bytes()
        plan = json.loads(raw)
        before = digest(plan)
        self.assertEqual(before, '5287850fefcd26987684195c54ef5cafd196f9226ea79aa7417ff03898f3f01a')
        units = [dict(handle='u', characters=450000)]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            artifacts = ArtifactStore(Path(root))
            reference = artifacts.put(raw, 'application/json')
            for level in (600000, 300000, 150000, 75000, 37500, 18750, 15000):
                turns = parts.plan_read_turns(plan, units, material_allowance=level)
                self.assertEqual(sum(turn['allowance']['content_characters'] for turn in turns), 338999)
                count = sum(len(turn['cards']) for turn in turns)
                entries = sum(turn['allowance']['card_reserve'] for turn in turns)
                self.assertLessEqual(count, plan['allocation']['largest_card_count'])
                self.assertLessEqual(entries, plan['allocation']['card_reserve'])
                self.assertTrue(all(card['characters'] <= 60000 for turn in turns for card in turn['cards']))
                self.assertEqual(artifacts.read(reference), raw)
                self.assertEqual(digest(plan), before)
            with patch.object(capacity, 'CARD_ENTRY_CHARACTERS', 999999), \
                    patch.object(capacity, 'read_output_profile', side_effect=AssertionError('Use the stored plan')):
                self.assertEqual(sum(turn['allowance']['content_characters']
                    for turn in parts.plan_read_turns(plan, units)), 338999)
                parts.count_series_turns(plan, units)
            self.assertEqual(artifacts.read(reference), raw)

    def test_rate_ceiling_keeps_a_floor_card_within_combine_and_its_reserve(self):
        plan = build_small_plan(rate=5, entry=2200, start=15000)
        plan['allocation'].update(largest_card_count=1, card_reserve=2200)
        self.assertEqual(parts.read_content_rate(plan), Fraction(57800, 15000))
        turns = parts.plan_read_turns(plan, [dict(handle='u', characters=15000)])
        self.assertEqual(len(turns), 1)
        self.assertEqual(len(turns[0]['cards']), 1)
        self.assertEqual(turns[0]['cards'][0]['characters'], 60000)
        self.assertEqual(turns[0]['allowance']['card_reserve'], 2200)

    def test_each_read_level_honors_its_pinned_answer_bound(self):
        plan = build_small_plan(rate=1.25, entry=2500, start=60000)
        plan['task']['read']['answer_bound'] = 30000
        for row in plan['task']['read']['levels']:
            row['answer_bound'] = 30000
        before = digest(plan)
        units = [dict(handle='u', characters=60000)]
        for level in (60000, 15000):
            turns = parts.plan_read_turns(plan, units, material_allowance=level)
            self.assertTrue(all(turn['allowance']['characters'] <= 30000 for turn in turns))
            self.assertEqual(sum(turn['allowance']['content_characters'] for turn in turns), 75000)
            self.assertLessEqual(sum(len(turn['cards']) for turn in turns), 4)
        self.assertEqual(digest(plan), before)

    def test_rate_uses_every_reachable_level_and_below_entry_refuses(self):
        plan = build_small_plan(rate=5, entry=2200, start=60000)
        plan['task']['read']['levels'][-1]['answer_bound'] = 30000
        plan['task']['read']['adaptation'] = dict(ceiling=60000)
        plan['task']['read']['levels'].insert(0, dict(material_allowance=120000, answer_bound=100))
        self.assertEqual(parts.read_content_rate(plan), Fraction(27800, 15000))
        plan['task']['read']['levels'][-1]['answer_bound'] = 2200
        with self.assertRaises(ResearchError) as caught:
            parts.read_content_rate(plan)
        self.assertEqual(caught.exception.code, 'review_route_capacity_below_floor')

    def test_a_nonmultiple_read_level_uses_its_available_material_room(self):
        plan = build_small_plan(rate=0.01, start=75000)
        turn = parts.size_read_turn(plan, 37500, [dict(handle='u', characters=75000)])
        self.assertEqual(turn['unit_characters'], 37500)
        self.assertEqual([group['unit_characters'] for group in turn['cards']], [18750, 18750])

    def test_all_full_entries_and_content_stay_within_their_separate_budgets(self):
        for small in (True, False):
            with self.subTest(small=small):
                plan, units = build_fixture_plan('fits', small_units=small)
                before = digest(plan)
                for level in (plan['task']['read']['material_allowance'], 15000):
                    turns = parts.plan_read_turns(plan, units, material_allowance=level)
                    content = sum(turn['allowance']['card_budget'] for turn in turns)
                    entries = sum(turn['allowance']['card_reserve'] for turn in turns)
                    self.assertLessEqual(content, plan['allocation']['card_budget'])
                    self.assertLessEqual(entries, plan['allocation']['card_reserve'])
                    self.assertEqual(sum(card['characters'] for turn in turns for card in turn['cards']),
                                     content + entries)
                    self.assertLessEqual(sum(len(turn['cards']) for turn in turns),
                                         plan['allocation']['largest_card_count'])
                self.assertEqual(digest(plan), before)

    def test_late_units_take_content_and_entries_from_the_late_reserve(self):
        plan = build_small_plan(rate=0.01)
        units = [dict(handle='initial', characters=1000), dict(handle='later', characters=1000, late=True)]
        turns = parts.plan_read_turns(plan, units)
        allowance = {key: sum(turn['allowance'][key] for turn in turns) for key in turns[0]['allowance']}
        self.assertEqual(allowance, dict(content_characters=20, entry_characters=600, characters=620,
                                        card_budget=10, card_reserve=300, late_unit_reserve=310))
        plan['allocation']['late_unit_reserve'] = 309
        with self.assertRaises(ResearchError) as caught:
            parts.plan_read_turns(plan, units)
        self.assertEqual(caught.exception.code, 'invalid_input')


class CardCondensingTests(unittest.TestCase):
    def test_forecast_returns_sized_reads_without_using_the_total_forecast(self):
        plan = build_small_plan(rate=0)
        units = [dict(handle='a', characters=10000), dict(handle='b', characters=10000)]
        with patch.object(condensing, 'forecast_condensing', side_effect=AssertionError('Use entries')):
            report = parts.count_series_turns(plan, units)
        self.assertEqual([turn['unit_characters'] for turn in report['start']['reads']], [20000])
        self.assertEqual([turn['unit_characters'] for turn in report['lowest']['reads']], [15000, 5000])
        self.assertEqual(report['lowest']['reads'][1]['delivered_before'], 15000)
        self.assertEqual(report['lowest']['reads'][1]['delivered_through'], 20000)
        self.assertIs(report['start']['condensing']['forecast'], True)

    def test_low_rate_schedules_each_full_block_and_higher_rounds(self):
        turns = [dict(unit_characters=30000,
                      cards=[dict(handle='c' + str(index * 4 + part), characters=300) for part in range(4)])
                 for index in range(5)]
        before = copy.deepcopy(turns)
        events = parts.plan_card_condensing(build_small_plan(room=1200), turns)
        self.assertEqual([event['after_read_turn'] for event in events], [1, 2, 3, 4, 4, 5])
        self.assertEqual([event['round'] for event in events], [1, 1, 1, 1, 2, 1])
        self.assertTrue(all(event['block']['characters'] <= 1200 for event in events))
        self.assertEqual(turns, before)
        for rate in (0.02, 0.021):
            self.assertEqual(parts.plan_card_condensing(build_small_plan(rate, room=1200), turns), [])
        self.assertEqual(parts.plan_card_condensing(build_small_plan(), []), [])

    def test_a_large_single_target_keeps_every_card_once(self):
        cards = [dict(handle='c' + str(index), characters=300) for index in range(50)]
        blocks = parts.plan_target_condensing(build_small_plan(room=1200), cards, 6000)
        self.assertEqual([handle for block in blocks for handle in block['entries']],
                         [card['handle'] for card in cards])
        self.assertTrue(all(block['characters'] <= 1200 for block in blocks))
        self.assertEqual(parts.plan_target_condensing(build_small_plan(), cards[:20], 6000), [])
        self.assertEqual(parts.plan_target_condensing(build_small_plan(), cards, 600),
                         condensing.cut_condensing_blocks(cards, 600))

    def test_read_packing_segments_units_keeps_zero_units_and_deduplicates_bodies(self):
        plan = build_small_plan()
        units = [dict(handle='a', characters=40000, sha256='a' * 64),
                 dict(handle='duplicate', characters=40000, sha256='a' * 64), dict(handle='empty', characters=0)]
        before = copy.deepcopy((plan, units))
        turns = parts.plan_read_turns(plan, units)
        self.assertEqual([turn['unit_characters'] for turn in turns], [30000, 10000])
        self.assertEqual(turns[0]['cards'][0]['first'], dict(unit='a', offset=0))
        self.assertEqual(turns[-1]['cards'][-1]['last'], dict(unit='empty', offset=0))
        self.assertEqual(turns[-1]['cards'][-1]['unit_count'], 2)
        self.assertEqual(parts.count_series_turns(plan, [])['start']['read_turns'], 0)
        self.assertEqual((plan, units), before)

    def test_empty_and_zero_only_inventory_work_with_a_high_stored_rate(self):
        plan = build_small_plan(rate=2000)
        self.assertEqual(parts.plan_read_turns(plan, []), [])
        turns = parts.plan_read_turns(plan, [dict(handle='empty', characters=0)])
        self.assertEqual(turns[0]['allowance']['characters'], 300)
        self.assertEqual(turns[0]['allowance']['content_characters'], 0)

    def test_impossible_entry_level_and_exhausted_reserve_refuse(self):
        for change in ('level', 'entry', 'reserve'):
            with self.subTest(change=change):
                plan = build_small_plan()
                level = 0 if change == 'level' else 30000
                if change == 'entry':
                    plan['allocation']['entry_sizes']['card'] = 60001
                if change == 'reserve':
                    plan['allocation']['card_reserve'] = 299
                with self.assertRaises(ResearchError) as caught:
                    parts.size_read_turn(plan, level, [dict(handle='u', characters=200)])
                self.assertEqual(caught.exception.code,
                                 'review_route_capacity_below_floor' if change == 'entry' else 'invalid_input')


class GroupSizingTests(unittest.TestCase):
    def test_a_card_groups_consecutive_units_with_bounded_endpoint_metadata(self):
        plan = build_small_plan(rate=0)
        units = [dict(handle='u' + str(index), characters=5000) for index in range(6)]
        before = copy.deepcopy((plan, units))
        turn = parts.size_read_turn(plan, 30000, units)
        self.assertEqual(turn['unit_characters'], 30000)
        self.assertEqual(len(turn['cards']), 2)
        for index, group in enumerate(turn['cards']):
            self.assertEqual(group['first'], dict(unit='u' + str(index * 3), offset=0))
            self.assertEqual(group['last'], dict(unit='u' + str(index * 3 + 2), offset=5000))
            self.assertEqual(group['unit_count'], 3)
            self.assertEqual(group['unit_characters'], 15000)
        self.assertEqual((plan, units), before)

    def test_groups_split_a_long_unit_and_balance_within_one_character(self):
        plan = build_small_plan(rate=0, start=75000)
        turn = parts.size_read_turn(plan, 37500, [dict(handle='u', characters=75000)])
        self.assertEqual([group['unit_characters'] for group in turn['cards']], [18750, 18750])
        self.assertEqual(turn['cards'][1]['first'], dict(unit='u', offset=18750))
        self.assertEqual(turn['cards'][1]['last'], dict(unit='u', offset=37500))
        self.assertTrue(all(group['unit_count'] == 1 for group in turn['cards']))

    def test_maximum_material_precedes_card_count_and_no_allowance_is_cut(self):
        plan = build_small_plan(rate=2, entry=2200, start=60000)
        turn = parts.size_read_turn(plan, 60000, [dict(handle='u', characters=60000)])
        self.assertEqual(turn['unit_characters'], 37800)
        self.assertEqual(len(turn['cards']), 2)
        self.assertEqual(turn['allowance']['characters'], 80000)
        self.assertEqual([group['unit_characters'] for group in turn['cards']], [18900, 18900])

    def test_balanced_rounding_can_require_the_shorter_group_first(self):
        plan = build_small_plan(rate=5, entry=2200, start=60000, room=12201)
        plan['task']['read']['answer_bound'] = 24402
        plan['task']['read']['levels'][-1]['answer_bound'] = 12200
        units = [dict(handle='prior', characters=15001), dict(handle='u', characters=30003)]
        cursor = dict(unit_index=1, unit_offset=0, delivered=15001,
                      card_count=1, card_reserve=2200, card_budget=10000)
        turn = parts.size_read_turn(plan, 60000, units, position=cursor)
        self.assertEqual(turn['unit_characters'], 30003)
        self.assertEqual([group['unit_characters'] for group in turn['cards']], [15001, 15002])
        self.assertEqual([group['content_characters'] for group in turn['cards']], [10001, 10001])
        self.assertEqual(turn['cards'][0]['last'], dict(unit='u', offset=15001))

    def test_initial_and_late_groups_can_share_a_turn_without_mixing(self):
        plan = build_small_plan(rate=0, start=60000)
        units = [dict(handle='initial', characters=20000), dict(handle='later', characters=20000, late=True)]
        turn = parts.size_read_turn(plan, 60000, units)
        self.assertEqual(turn['unit_characters'], 40000)
        self.assertEqual([group['late'] for group in turn['cards']], [False, True])
        self.assertEqual([group['unit_characters'] for group in turn['cards']], [20000, 20000])
        self.assertEqual(turn['allowance']['card_reserve'], 300)
        self.assertEqual(turn['allowance']['late_unit_reserve'], 300)
        self.assertEqual(turn['cards'][0]['first'], dict(unit='initial', offset=0))
        self.assertTrue(all(group['unit_count'] == 1 for group in turn['cards']))
        units = [dict(handle='initial', characters=20000), dict(handle='later', characters=60000, late=True)]
        turn = parts.size_read_turn(plan, 60000, units)
        self.assertEqual((turn['unit_characters'], len(turn['cards'])), (60000, 3))
        units[0]['characters'] = 22000
        turn = parts.size_read_turn(plan, 60000, units)
        self.assertEqual(turn['unit_characters'], 44001)
        self.assertEqual([group['unit_characters'] for group in turn['cards']], [22000, 22001])

    def test_a_short_ownership_tail_stays_separate_and_zero_units_are_reachable(self):
        plan = build_small_plan(rate=0)
        units = [dict(handle='zero', characters=0), dict(handle='initial', characters=1000),
                 dict(handle='later', characters=1000, late=True), dict(handle='last-zero', characters=0, late=True)]
        turns = parts.plan_read_turns(plan, units)
        self.assertEqual([turn['unit_characters'] for turn in turns], [1000, 1000])
        self.assertEqual([turn['cards'][0]['unit_count'] for turn in turns], [2, 2])
        self.assertEqual(turns[0]['cards'][0]['first'], dict(unit='zero', offset=0))
        self.assertEqual(turns[1]['cards'][0]['last'], dict(unit='last-zero', offset=0))


class BudgetPolicyMeasurementsTests(unittest.TestCase):
    def test_read_bounds_card_counts_and_turn_counts_for_both_inventories(self):
        expected = {
            'fits': (80000, {True: ((36, 4), (1, 134)), False: ((36, 47), (1, 1871))}),
            'over_limit_expected': (4000, {True: ((1, 54), (1, 134)),
                                          False: ((1, 749), (1, 1871))}),
            'unmeasured': (4000, {True: ((1, 134), (1, 134)),
                                 False: ((1, 336), (1, 336))}),
        }
        for state, (answer_bound, inventories) in expected.items():
            for small, wanted in inventories.items():
                with self.subTest(state=state, small=small):
                    plan, inventory = build_fixture_plan(state, small_units=small)
                    before = digest(plan)
                    task = plan['task']['read']
                    self.assertEqual(task['state'], state)
                    for row in task['levels']:
                        if row['material_allowance'] <= task['adaptation']['ceiling']:
                            self.assertEqual(parts.describe_card_bounds(plan, row['material_allowance']),
                                             dict(turn_characters=answer_bound,
                                                  card_characters=min(answer_bound, 60000)))
                    measured = []
                    for level in (task['material_allowance'], 15000):
                        turns = parts.plan_read_turns(plan, inventory, material_allowance=level)
                        measured.append((max(len(turn['cards']) for turn in turns), len(turns)))
                        self.assertEqual(len(turns[0]['cards']), wanted[len(measured) - 1][0])
                        self.assertTrue(all(turn['allowance']['characters'] <= answer_bound for turn in turns))
                        if answer_bound == 4000:
                            self.assertTrue(all(len(turn['cards']) == 1 for turn in turns))
                        self.assertTrue(all(turn['unit_characters'] >= 15000 for turn in turns[:-1]))
                        self.assertLessEqual(sum(len(turn['cards']) for turn in turns),
                                             plan['allocation']['largest_card_count'])
                        self.assertLessEqual(sum(len(turn['cards']) for turn in turns),
                                             (sum(unit['characters'] for unit in inventory) + 14999) // 15000)
                        for turn in turns:
                            sizes = [group['unit_characters'] for group in turn['cards']]
                            self.assertLessEqual(max(sizes) - min(sizes), 1)
                            self.assertTrue(all(group['characters'] <= min(answer_bound, 60000)
                                                for group in turn['cards']))
                    self.assertEqual(tuple(measured), wanted)
                    self.assertEqual(digest(plan), before)

    def test_unmeasured_low_outputs_reach_the_ceiling_in_29_and_70_reads(self):
        for small, maximum in ((True, 29), (False, 70)):
            with self.subTest(small=small):
                plan, inventory = build_fixture_plan('unmeasured', small_units=small)
                before = json.dumps(plan, sort_keys=True)
                observations, turns, position, levels = [], [], None, []
                for _ in range(maximum):
                    limits = capacity.derive_plan_task_limits(plan, 'read', observations)
                    level = limits['material_allowance']
                    turn = parts.size_read_turn(plan, level, inventory, position=position)
                    if turn is None:
                        break
                    levels.append(level)
                    turns.append(turn)
                    position = turn['next']
                    observations.append(dict(material_allowance=level,
                        output_tokens=plan['limits']['output_tokens']['value'] * 35 // 100))
                    if position['unit_index'] < len(inventory):
                        self.assertGreaterEqual(turn['unit_characters'], 15000)
                self.assertEqual(position['unit_index'], len(inventory))
                self.assertLessEqual(len(turns), maximum)
                self.assertEqual(levels[:4], [15000, 18750, 37500, 75000])
                self.assertEqual(max(levels), plan['task']['read']['adaptation']['ceiling'])
                self.assertEqual(len(turns), maximum)
                self.assertTrue(all(len(turn['cards']) == 1 for turn in turns))
                assert_inventory_coverage(self, inventory, [group for turn in turns for group in turn['cards']])
                self.assertEqual(json.dumps(plan, sort_keys=True), before)

    def test_unit_density_does_not_change_read_turns(self):
        for state in ('fits', 'over_limit_expected', 'unmeasured'):
            with self.subTest(state=state):
                original, units = build_fixture_plan(state, small_units=True)
                route = build_route(model='unlisted-model' if state == 'unmeasured' else 'claude-opus-5-5',
                                    effort='max' if state == 'over_limit_expected' else 'xhigh',
                                    output=32000 if state == 'unmeasured' else 128000)
                many_units = [dict(handle='v' + str(index), kind='text', characters=20) for index in range(100000)]
                many = capacity.plan_chain_capacity(route, original['profile'],
                    frames={kind: frame['fields'] for kind, frame in original['frame'].items()},
                    inventory=many_units, targets=[], carried_budget=original['allocation']['carried_turn_pool'])
                before = (digest(original), digest(many))
                for level in (original['task']['read']['material_allowance'], 15000):
                    sparse = parts.plan_read_turns(original, units, material_allowance=level)
                    dense = parts.plan_read_turns(many, many_units, material_allowance=level)
                    self.assertEqual([turn['unit_characters'] for turn in dense],
                                     [turn['unit_characters'] for turn in sparse])
                    self.assertEqual([len(turn['cards']) for turn in dense],
                                     [len(turn['cards']) for turn in sparse])
                    assert_inventory_coverage(self, many_units, [group for turn in dense for group in turn['cards']])
                self.assertEqual((digest(original), digest(many)), before)


class SeriesSimulationTests(unittest.TestCase):
    def simulate_series(self, state):
        plan, inventory = build_fixture_plan(state)
        before = digest(plan)
        forecast = parts.count_series_turns(plan, inventory)
        self.assertEqual(plan['task']['read']['state'], state)
        self.assertEqual(plan['profile']['digest'], digest(plan['profile']['rows']))
        observed = {}
        for name, level in (('start', plan['task']['read']['material_allowance']), ('lowest', 15000)):
            turns = parts.plan_read_turns(plan, inventory, material_allowance=level)
            events = parts.plan_card_condensing(plan, turns)
            carried = dict(entries=[], handles={}, blocks={})
            pending = iter(events)
            next_event = next(pending, None)
            combinations, delivered = 0, []
            for index, turn in enumerate(turns, 1):
                self.assertLessEqual(turn['unit_characters'], level)
                self.assertEqual(turn['allowance'], parts.compute_card_allowance(plan, turn['cards']))
                self.assertLessEqual(turn['allowance']['characters'], 80000)
                for card in turn['cards']:
                    self.assertLessEqual(card['characters'], 60000)
                    delivered.append(card)
                    value = dict(card, summary='')
                    value['summary'] = 's' * (card['characters'] - measure(value))
                    self.assertEqual(measure(value), card['characters'])
                    entry = dict(handle=card['handle'], characters=card['characters'], value=value)
                    carried['entries'].append(entry)
                    carried['handles'][card['handle']] = entry
                while next_event is not None and next_event['after_read_turn'] == index:
                    block = next_event['block']
                    output = build_combination(block, plan['task']['combine']['answer_bound'])
                    condensing.validate_combination(block, output, measure, plan['task']['combine']['answer_bound'])
                    self.assertEqual(sum(measure(carried['handles'][handle]['value'])
                        for handle in block['entries']), block['characters'])
                    self.assertLessEqual(block['characters'], 60000)
                    carried = condensing.apply_combination(carried, block, output, measure)
                    combinations += 1
                    next_event = next(pending, None)
            self.assertIsNone(next_event)
            assert_inventory_coverage(self, inventory, delivered)
            self.assertLessEqual(sum(measure(entry['value']) for entry in carried['entries']), 60000)
            self.assertEqual(forecast[name]['read_turns'], len(turns))
            self.assertIs(forecast[name]['condensing']['forecast'], True)
            self.assertGreaterEqual(forecast[name]['condensing']['turns'], combinations)
            self.assertEqual(sum(turn['allowance']['content_characters'] for turn in turns), 0)
            self.assertLessEqual(sum(turn['allowance']['card_reserve'] for turn in turns),
                                 plan['allocation']['card_reserve'])
            observed[name] = (len(turns), combinations)
        self.assertEqual(digest(plan), before)
        return observed

    def test_measured_fits_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('fits'), dict(start=(47, 83), lowest=(1871, 95)))

    def test_scoped_max_prior_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('over_limit_expected'), dict(start=(749, 37), lowest=(1871, 95)))

    def test_unlisted_model_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('unmeasured'), dict(start=(336, 17), lowest=(336, 17)))
