"""Rendered card work consumes output and duration budgets without original spans."""

import unittest
from unittest.mock import patch

from research_harness import review_staged_protocol as producer
from research_harness.storage import _canonical
from test_review_staged_fixtures import profile
import test_review_staged_integration_partitions as fixtures


class RenderedSizingTests(unittest.TestCase):
    def test_card_only_integration_partitions_against_output_and_duration(self):
        for dimension in ('output', 'duration'):
            with self.subTest(dimension=dimension):
                case = fixtures.IntegrationPartitionTests()
                case.setUp()
                self.addCleanup(case.doCleanups)
                one = case.make_leaf(case.cards[:1])
                all_cards = case.make_leaf(case.cards)
                # Rebuild the envelopes with no originals: all scientific work
                # comes from the accepted, complete dependency cards.
                one = producer.remake_task(case.epoch, one, inputs=[], scope='segmented_original')
                all_cards = producer.remake_task(case.epoch, all_cards, inputs=[], scope='segmented_original')
                body = producer.build_packet(case.epoch, one, observations=[case.observed])['staged_review']
                work = len(_canonical(body['integrate']))
                measured = profile(output_tokens_per_character=0, seconds_per_character=0)
                if dimension == 'output':
                    measured.update(output_tokens_per_character=0.1, output_margin_tokens=100)
                    case.route['configuration']['max_output_tokens'] = int(work * 0.1 + 200)
                else:
                    measured.update(seconds_per_character=0.001, duration_limit_seconds=work * 0.001 + 5)
                with patch.object(producer, 'build_profiles', return_value={'integrate':measured}):
                    _, plans, blocked = producer.prepare_partitions(case.records, case.artifacts,
                        case.controller, case.epoch, all_cards)
                self.assertIsNone(blocked)
                self.assertEqual(len(plans), len(case.cards))
                for planned in plans:
                    value = planned['task']['measurement']
                    self.assertTrue(value['fits'])
                    if dimension == 'output':
                        self.assertGreater(value['output_tokens'], 100)
                    else:
                        self.assertGreater(value['duration_seconds'], 1)


if __name__ == '__main__':
    unittest.main()
