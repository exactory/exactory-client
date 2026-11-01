"""Measured card scheduling and truthful joint versus segmented scope."""

import copy
import itertools
import unittest

from research_harness import review_staged as staged
from test_review_staged_fixtures import epoch, profile


class CombinationPlannerTests(unittest.TestCase):
    def plan(self, sizes, route_limit=100, **changes):
        cards = [dict(id='c%d' % i, characters=size) for i, size in enumerate(sizes)]
        return staged.plan_tasks(epoch(), route_limit=route_limit, output_tokens=100,
                                 profiles={'combine': profile(**changes)}, cards=cards)

    def test_all_cards_one_call(self):
        plan = self.plan([10, 10, 10])
        self.assertEqual(len(plan['tasks']), 1)
        self.assertEqual(plan['tasks'][0]['scope'], 'joint_card')
        self.assertEqual(len(plan['tasks'][0]['claim_ids']), 2)
        self.assertEqual(plan['projected_calls']['combination'], 1)

    def test_complete_block_pairs_and_missing_pair(self):
        plan = self.plan([30] * 6)
        self.assertEqual(len(plan['blocks']), 6)
        self.assertEqual(len(plan['tasks']), 15)
        expected = set(itertools.combinations(['c%d' % i for i in range(6)], 2))
        actual = set(tuple(t['card_ids']) for t in plan['tasks'])
        self.assertEqual(actual, expected)
        self.assertEqual(staged.missing_card_pairs(plan, plan['tasks']), [])
        self.assertEqual(len(staged.missing_card_pairs(plan, plan['tasks'][:-1])), 1)

    def test_oversized_card_is_paged_and_never_joint(self):
        plan = self.plan([150, 10])
        pair = next(t for t in plan['tasks'] if t['scope'] == 'segmented_card')
        self.assertEqual(pair['card_ids'], ['c0', 'c1'])
        self.assertTrue(pair['segment_task_ids'])
        segments = [t for t in plan['tasks'] if t['id'] in pair['segment_task_ids']]
        ranges = [[x['start'], x['end']] for t in segments for x in t['card_segments'] if x['card_id'] == 'c0']
        self.assertEqual(staged.interval_gaps([[0, 150]], ranges), [])
        self.assertEqual(plan['projected_calls']['exceptional_pairs'], 1)

    def test_two_point_six_cards_receive_exact_segmented_labels(self):
        plan = self.plan([60, 60], route_limit=120)
        self.assertEqual(plan['projected_calls']['exceptional_pairs'], 1)
        self.assertFalse(any(t['scope'] == 'joint_card' for t in plan['tasks']))
        self.assertEqual(plan['tasks'][-1]['scope'], 'segmented_card')
        self.assertTrue(all(t['scope'] == 'card_segment' for t in plan['tasks'][:-1]))

    def test_context_output_and_duration_are_part_of_fit(self):
        self.assertTrue(staged.joint_fit([20, 20], 100, 100, profile())['fits'])
        self.assertFalse(staged.joint_fit([20, 20], 100, 100, profile(context_characters=70))['fits'])
        self.assertFalse(staged.joint_fit([20, 20], 100, 10,
                                         profile(output_base_tokens=20))['fits'])
        self.assertFalse(staged.joint_fit([20, 20], 100, 100,
                                         profile(seconds_per_character=10, duration_limit_seconds=50))['fits'])
        self.assertFalse(staged.joint_fit([20], 100, 100, profile(), preserved=False)['fits'])

    def test_three_block_interaction_becomes_original_check(self):
        combination_plan = self.plan([30, 30, 30])
        self.assertEqual(len(combination_plan['blocks']), 3)
        self.assertEqual(combination_plan['projected_calls']['combination'], 3)
        value = epoch(count=3)
        occurrence_ids = [o['id'] for o in value['manifest']['occurrences']]
        groups = [dict(id='triple', occurrence_ids=occurrence_ids, claim_ids=[value['manifest']['claims'][0]['id']],
                       question='Does the three-way dependency hold?', requested_by=['integration'])]
        plan = staged.plan_tasks(value, route_limit=300, output_tokens=100,
                                 profiles={'original_check': profile()}, selected_groups=groups)
        checks = [t for t in plan['tasks'] if t['type'] == 'original_check']
        self.assertTrue(checks)
        self.assertEqual(checks[-1]['group_ids'], ['triple'])
        self.assertEqual(checks[-1]['occurrence_ids'], occurrence_ids)
        self.assertFalse(staged.verify_completion(value, {'observations': []}, tasks=checks)['complete'])


if __name__ == '__main__':
    unittest.main()
