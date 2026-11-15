"""The shipped parts planner stays bounded as text grows; gaps remain explicit."""

import copy
import unittest

from ci import capacity_guard
from research_harness import review_parts as parts
from research_harness.errors import ResearchError
import review_parts_fixtures as fixtures

PARTS_END_MESSAGE = 'A finite text did not finish its declared parts.'


class CapacityPropertyTests(unittest.TestCase):
    def test_text_sizes_to_thirty_route_limits_have_finite_fitting_turns(self):
        room = 80000
        limit = fixtures.measure_input_limit(room)
        gaps = capacity_guard.build_gap_table(capacity_guard.read_matrix())
        self.assertIn('Q17', gaps['gaps'])
        for numerator in (9, 10, 11, 30, 300):
            with self.subTest(tenths=numerator):
                packet = fixtures.make_packet(count=1, characters=limit * numerator // 10)
                observations = []
                for number in range(500):
                    try:
                        turn = fixtures.plan(packet, observations, room=room)
                    except ResearchError as error:
                        self.assertIn('A6', gaps['gaps'])
                        self.assertIn(error.code, ('review_parts_final_too_large', 'review_parts_inventory_too_large'))
                        break
                    clean = {key: value for key, value in turn.items() if key != '_test_inventory'}
                    self.assertLessEqual(fixtures.measure_packet(clean), limit)
                    if parts.find_kind(turn) == 'final':
                        break
                    observations.append(fixtures.observe(turn, output_tokens=1000))
                else:
                    self.fail(PARTS_END_MESSAGE)
                if observations and parts.find_kind(turn) == 'final':
                    inventory = turn['_test_inventory']
                    self.assertTrue(parts.covers_items([row['packet'] for row in observations],
                        packet['problem_evidence'], 'openness_searches', inventory))
                    self.assertEqual(parts.build_inventory(packet, 'problem_result', inventory), inventory)

    def test_ten_thousand_units_end_at_the_declared_inventory_boundary(self):
        packet = fixtures.make_packet(count=10000, characters=200)
        before = copy.deepcopy(packet)
        matrix = capacity_guard.read_matrix()
        self.assertIn('Q2', capacity_guard.build_gap_table(matrix)['gaps'])
        with self.assertRaises(ResearchError) as caught:
            fixtures.plan(packet, room=80000)
        self.assertIn(caught.exception.code, ('review_parts_frame_too_large',
            'review_parts_inventory_too_large', 'review_parts_final_too_large'))
        self.assertEqual(packet, before)
