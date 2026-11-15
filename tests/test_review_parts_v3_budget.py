"""Pure v3 card budgets and synthetic, store-free capacity simulations."""
import copy
from fractions import Fraction
import unittest
from unittest.mock import patch

from research_harness import review_condensing as condensing, review_parts_v3 as parts
from research_harness import review_route_capacity as capacity
from research_harness.review_parts import render_json
from research_harness.errors import ResearchError
from research_harness.evidence import digest
from test_review_route_capacity import build_route


def build_small_plan(rate=0.01):
    return dict(allocation=dict(card_rate=rate, entry_sizes=dict(card=300),
                                rooms=dict(combine=1200), page_room=6000),
                task=dict(read=dict(material_allowance=30000,
                                    levels=[dict(material_allowance=30000), dict(material_allowance=15000)]),
                          combine=dict(material_allowance=1200)))


def build_fixture_plan(state):
    route = build_route(model='unlisted-model' if state == 'unmeasured' else 'claude-opus-5-5',
                        effort='max' if state == 'over_limit_expected' else 'xhigh',
                        output=32000 if state == 'unmeasured' else 128000)
    profile = capacity.read_output_profile({}, route)
    if state == 'over_limit_expected':
        # Scope the real max reading prior at 240000 to the synthetic read kind.
        # Existing max read priors are fits; no stored profile or plan is edited.
        row = copy.deepcopy(next(row for row in profile['rows']
                                 if row['kind'] == 'reading' and row['task_level'] == 240000))
        row['kind'] = 'read'
        profile = capacity.read_output_profile({}, route, priors=[row])
    limit = capacity.describe_route_capacity(route, profile)['input_text_characters']['value']
    total = 30 * limit
    inventory = [dict(handle='u' + str(index), kind='text',
                      characters=total // 10000 + (index < total % 10000)) for index in range(10000)]
    plan = capacity.plan_chain_capacity(route, profile,
        frames={kind: dict(instructions=3000) for kind in ('read', 'combine', 'check', 'final')},
        inventory=inventory, targets=[])
    return plan, inventory


class CardAllowanceTests(unittest.TestCase):
    def test_rate_is_pinned_and_arbitrary_split_allowances_are_exact(self):
        plan = build_small_plan(rate=0.1234567)
        before = copy.deepcopy(plan)
        cards = [dict(handle='a', entry_characters=0), dict(handle='b', entry_characters=0)]
        whole = parts.compute_card_allowance(plan, 10001, cards)
        first = parts.compute_card_allowance(plan, 3333, cards[:1])
        second = parts.compute_card_allowance(plan, 6668, cards[1:])
        self.assertEqual(whole, Fraction(12346904567, 10000000))
        self.assertEqual(first + second, whole)
        self.assertEqual(parts.compute_card_allowance(plan, 0, []), 0)
        self.assertEqual(plan, before)

    def test_entry_minimum_and_eighty_thousand_cap(self):
        plan = build_small_plan()
        self.assertEqual(parts.compute_card_allowance(plan, 1000, [dict(handle='a'), dict(handle='b')]), 600)
        self.assertEqual(parts.compute_card_allowance(plan, 1000, [dict(handle='a', entry_characters=700)]), 700)
        plan['allocation']['card_rate'] = 1
        self.assertEqual(parts.compute_card_allowance(plan, 100000, [dict(handle='a')]), 80000)
        with self.assertRaises(ResearchError) as caught:
            parts.compute_card_allowance(plan, 100000, [dict(handle='a', entry_characters=80001)])
        self.assertEqual(caught.exception.code, 'invalid_input')

    def test_split_additivity_when_both_halves_use_the_entry_floor(self):
        plan = build_small_plan(rate=0)
        cards = [dict(handle='a'), dict(handle='b')]
        self.assertEqual(parts.compute_card_allowance(plan, 10000, cards),
                         parts.compute_card_allowance(plan, 5000, cards[:1]) +
                         parts.compute_card_allowance(plan, 5000, cards[1:]))

    def test_stored_rate_is_not_recomputed_from_current_policy(self):
        plan = build_small_plan()
        before = digest(plan)
        with patch.object(capacity, 'CARD_ENTRY_CHARACTERS', 999999):
            self.assertEqual(parts.compute_card_allowance(plan, 100000, [dict(handle='a')]), 1000)
        self.assertEqual(digest(plan), before)


class CardCondensingTests(unittest.TestCase):
    def test_low_rate_schedules_each_full_block_and_higher_rounds(self):
        turns = [dict(unit_characters=30000,
                      cards=[dict(handle='c' + str(index * 4 + part), characters=300) for part in range(4)])
                 for index in range(5)]
        before = copy.deepcopy(turns)
        events = parts.plan_card_condensing(build_small_plan(), turns)
        self.assertEqual([event['after_read_turn'] for event in events], [1, 2, 3, 4, 4, 5, 5])
        self.assertEqual([event['round'] for event in events], [1, 1, 1, 1, 2, 1, 2])
        self.assertTrue(all(event['block']['characters'] <= 1200 for event in events))
        self.assertEqual(turns, before)
        for rate in (0.02, 0.021):
            self.assertEqual(parts.plan_card_condensing(build_small_plan(rate), turns), [])
        self.assertEqual(parts.plan_card_condensing(build_small_plan(), []), [])

    def test_a_large_single_target_keeps_every_card_once(self):
        cards = [dict(handle='c' + str(index), characters=300) for index in range(50)]
        blocks = parts.plan_target_condensing(build_small_plan(), cards, 6000)
        self.assertEqual([entry for block in blocks for entry in block['entries']], cards)
        self.assertTrue(all(block['characters'] <= 1200 for block in blocks))
        self.assertEqual(parts.plan_target_condensing(build_small_plan(), cards[:20], 6000), [])
        self.assertEqual(parts.plan_target_condensing(build_small_plan(), cards, 600),
                         condensing.cut_condensing_blocks(cards, 600))

    def test_read_packing_segments_large_units_keeps_zero_units_and_deduplicates_bodies(self):
        plan = build_small_plan()
        units = [dict(handle='a', characters=40000, sha256='a' * 64),
                 dict(handle='duplicate', characters=40000, sha256='a' * 64),
                 dict(handle='empty', characters=0)]
        before = copy.deepcopy((plan, units))
        turns = parts.plan_read_turns(plan, units)
        self.assertEqual([turn['unit_characters'] for turn in turns], [30000, 10000])
        self.assertEqual([card['unit'] for turn in turns for card in turn['cards']], ['a', 'a', 'empty'])
        self.assertEqual([card['characters'] for turn in turns for card in turn['cards']], [300, 300, 300])
        self.assertEqual(parts.count_series_turns(plan, [])['start']['turns'], 0)
        self.assertEqual((plan, units), before)

    def test_forecast_does_not_undercount_an_uneven_valid_card_distribution(self):
        plan = build_small_plan()
        plan['task']['read']['material_allowance'] = 80000
        inventory = [dict(handle='u' + str(index), characters=40000) for index in range(6)]
        # Three read answers may each spend their 800-character allowance as
        # [500, 300]. Those six cards need three initial blocks of 800.
        forecast = parts.count_series_turns(plan, inventory)
        self.assertGreaterEqual(forecast['start']['condensing_turns'], 3)

    def test_read_packing_keeps_a_single_card_within_the_condensing_room(self):
        plan = build_small_plan(rate=1)
        plan['allocation']['largest_card_count'] = 1
        turns = parts.plan_read_turns(plan, [dict(handle='u', characters=4000)])
        self.assertEqual(sum(turn['unit_characters'] for turn in turns), 4000)
        self.assertTrue(all(card['characters'] <= 1200 for turn in turns for card in turn['cards']))
        self.assertTrue(all(turn['unit_characters'] <= 1200 for turn in turns))
        forecast = parts.count_series_turns(plan, [dict(handle='u', characters=4000)])
        self.assertEqual(forecast['start']['material_allowance'], 30000)
        self.assertEqual(forecast['start']['segment_characters'], 1200)
        self.assertEqual(forecast['start']['planned_card_count'], 4)
        self.assertEqual(forecast['start']['card_count_over_reserve'], 3)

    def test_a_high_rate_does_not_refuse_empty_or_zero_only_inventory(self):
        plan = build_small_plan(rate=2000)
        self.assertEqual(parts.plan_read_turns(plan, []), [])
        self.assertEqual(parts.count_series_turns(plan, [])['start']['turns'], 0)
        turns = parts.plan_read_turns(plan, [dict(handle='empty', characters=0)])
        self.assertEqual([turn['unit_characters'] for turn in turns], [0])
        self.assertEqual(turns[0]['cards'][0]['characters'], 300)
        with self.assertRaises(ResearchError):
            parts.plan_read_turns(plan, [dict(handle='positive', characters=1)])

    def test_impossible_structural_entry_or_read_level_refuses(self):
        plan = build_small_plan()
        for level in (0, -1):
            with self.assertRaises(ResearchError) as caught:
                parts.plan_read_turns(plan, [], material_allowance=level)
            self.assertEqual(caught.exception.code, 'invalid_input')
        plan['allocation']['entry_sizes']['card'] = 1201
        with self.assertRaises(ResearchError) as caught:
            parts.plan_read_turns(plan, [dict(handle='u', characters=1)])
        self.assertEqual(caught.exception.code, 'invalid_input')

    def test_packing_does_not_create_extra_cards_to_fill_another_units_tail(self):
        plan = build_small_plan(rate=0)
        turns = parts.plan_read_turns(plan, [dict(handle='a', characters=10000),
                                            dict(handle='b', characters=10000)], material_allowance=15000)
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
            self.assertEqual(sum(len(turn['cards']) for turn in turns), plan['allocation']['largest_card_count'])
            events = parts.plan_card_condensing(plan, turns)
            carried = dict(entries=[], handles={})
            pending = iter(events)
            next_event = next(pending, None)
            combinations = 0
            delivered = {}
            for index, turn in enumerate(turns, 1):
                self.assertLessEqual(turn['unit_characters'], level)
                self.assertEqual(sum(card['characters'] for card in turn['cards']),
                                 int(parts.compute_card_allowance(plan, turn['unit_characters'], turn['cards'])))
                self.assertLessEqual(sum(card['characters'] for card in turn['cards']), 80000)
                for card in turn['cards']:
                    self.assertNotIn(card['handle'], carried['handles'])
                    delivered[card['unit']] = delivered.get(card['unit'], 0) + card['unit_characters']
                    card['summary'] = ''
                    # Each serialized card plus its separator spends its whole
                    # allowance. The synthetic content carries no source text.
                    card['summary'] = 's' * (card['characters'] - len(render_json(card)) - 1)
                    self.assertEqual(len(render_json(card)) + 1, card['characters'])
                    carried['entries'].append(card)
                    carried['handles'][card['handle']] = card
                while next_event is not None and next_event['after_read_turn'] == index:
                    block = next_event['block']
                    output = dict(block=block['block'], summary='s' * block['summary_bound'],
                                  refs=[entry['handle'] for entry in block['entries']])
                    condensing.validate_combination(block, output)
                    self.assertLessEqual(len(render_json([carried['handles'][entry['handle']]
                        for entry in block['entries']])), plan['allocation']['rooms']['combine'])
                    carried = condensing.apply_combination(carried, block, output)
                    self.assertLessEqual(block['characters'], 60000)
                    combinations += 1
                    next_event = next(pending, None)
            self.assertIsNone(next_event)
            self.assertEqual(delivered, {unit['handle']: unit['characters'] for unit in inventory})
            remaining = sum(entry['characters'] for entry in carried['entries'])
            self.assertLessEqual(remaining, 60000)
            self.assertLessEqual(len(render_json(carried['entries'])), plan['allocation']['rooms']['combine'])
            self.assertEqual(forecast[name]['read_turns'], len(turns))
            self.assertEqual(forecast[name]['condensing_turns'], combinations)
            self.assertEqual(forecast[name]['remaining_characters'], remaining)
            self.assertEqual(forecast[name]['turns'], len(turns) + combinations)
            observed[name] = (len(turns), combinations)
        self.assertEqual(digest(plan), before)
        return observed

    def test_measured_fits_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('fits'),
                         dict(start=(371, 496), lowest=(2000, 496)))

    def test_scoped_max_prior_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('over_limit_expected'),
                         dict(start=(770, 496), lowest=(2000, 496)))

    def test_unlisted_model_series_uses_every_allowance_and_finishes(self):
        self.assertEqual(self.simulate_series('unmeasured'),
                         dict(start=(371, 496), lowest=(371, 496)))
