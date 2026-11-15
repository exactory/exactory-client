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
    def test_integer_content_allocations_telescope_at_arbitrary_boundaries(self):
        plan = build_small_plan(rate=0.1234567, start=600000)
        self.assertEqual(parts.read_content_rate(plan), Fraction(1234567, 10000000))
        whole = parts.compute_content_allocation(plan, 0, 10001)
        first = parts.compute_content_allocation(plan, 0, 5000)
        second = parts.compute_content_allocation(plan, 5000, 10001)
        self.assertEqual((whole, first, second), (1234, 617, 617))
        self.assertEqual(first + second, whole)
        self.assertEqual(parts.compute_content_allocation(plan, 11, 10012), 1235)

    def test_cap_sizes_the_turn_without_cutting_any_allowance(self):
        plan = build_small_plan(rate=1.25, entry=2500, start=60000)
        units = [dict(handle='u', characters=60000)]
        turn = parts.size_read_turn(plan, 60000, units)
        self.assertEqual(turn['unit_characters'], 60000)
        self.assertEqual(turn['allowance']['characters'], 80000)
        self.assertEqual(turn['allowance']['content_characters'], 75000)
        self.assertTrue(all(card['characters'] <= 60000 for card in turn['cards']))
        halves = parts.plan_read_turns(plan, units, material_allowance=30000)
        self.assertEqual([turn['allowance']['characters'] for turn in halves], [40000, 40000])
        self.assertEqual(sum(turn['allowance']['content_characters'] for turn in halves), 75000)
        # The arithmetic helper never clamps a larger, unsized declaration.
        self.assertEqual(parts.compute_card_allowance(plan, 0, 100000,
                         [dict(unit_characters=100000)])['characters'], 127500)

    def test_dividing_one_card_adds_one_reserved_entry_but_no_content(self):
        plan = build_small_plan(rate=0.05, entry=2500, start=60000)
        units = [dict(handle='u', characters=30000)]
        whole = parts.plan_read_turns(plan, units)
        halves = parts.plan_read_turns(plan, units, material_allowance=15000)
        self.assertEqual(len(whole[0]['cards']), 1)
        self.assertEqual(sum(len(turn['cards']) for turn in halves), 2)
        self.assertEqual(sum(turn['allowance']['content_characters'] for turn in halves),
                         whole[0]['allowance']['content_characters'])
        self.assertEqual(sum(turn['allowance']['card_reserve'] for turn in halves),
                         whole[0]['allowance']['card_reserve'] + 2500)

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

    def test_dividing_a_turn_of_several_parts_keeps_the_same_cards(self):
        plan = build_small_plan(rate=0.01, start=60000)
        units = [dict(handle='u' + str(index), characters=15000) for index in range(4)]
        whole = parts.plan_read_turns(plan, units)
        halves = parts.plan_read_turns(plan, units, material_allowance=30000)
        self.assertEqual([card for turn in whole for card in turn['cards']],
                         [card for turn in halves for card in turn['cards']])

    def test_a_nonmultiple_read_level_uses_its_available_material_room(self):
        plan = build_small_plan(rate=0.01, start=75000)
        turn = parts.size_read_turn(plan, 37500, [dict(handle='u', characters=75000)])
        self.assertEqual(turn['unit_characters'], 37500)
        self.assertEqual(turn['cards'][0]['unit_characters'], 37500)

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
        allowance = turns[0]['allowance']
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
        self.assertEqual([turn['unit_characters'] for turn in report['lowest']['reads']], [10000, 10000])
        self.assertEqual(report['lowest']['reads'][1]['delivered_before'], 10000)
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
        self.assertEqual([card['unit'] for turn in turns for card in turn['cards']], ['a', 'a', 'empty'])
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

    def test_packing_does_not_create_extra_cards_to_fill_another_units_tail(self):
        turns = parts.plan_read_turns(build_small_plan(rate=0),
            [dict(handle='a', characters=10000), dict(handle='b', characters=10000)], material_allowance=15000)
        self.assertEqual([turn['unit_characters'] for turn in turns], [10000, 10000])
        self.assertEqual(sum(len(turn['cards']) for turn in turns), 2)


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
            combinations, delivered = 0, {}
            for index, turn in enumerate(turns, 1):
                self.assertLessEqual(turn['unit_characters'], level)
                self.assertEqual(turn['allowance'], parts.compute_card_allowance(plan,
                    turn['delivered_before'], turn['delivered_through'], turn['cards']))
                self.assertLessEqual(turn['allowance']['characters'], 80000)
                for card in turn['cards']:
                    self.assertLessEqual(card['characters'], 60000)
                    delivered[card['unit']] = delivered.get(card['unit'], 0) + card['unit_characters']
                    value = dict(ref=card['handle'], summary='')
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
            self.assertEqual(delivered, {unit['handle']: unit['characters'] for unit in inventory})
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
        self.assertEqual(self.simulate_series('fits'), dict(start=(278, 496), lowest=(2000, 496)))

    def test_scoped_max_prior_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('over_limit_expected'), dict(start=(10000, 496), lowest=(10000, 496)))

    def test_unlisted_model_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('unmeasured'), dict(start=(10000, 496), lowest=(10000, 496)))
