import tempfile
import unittest
from pathlib import Path

import attack
from attack import load_strategies
from tests.support import FIXTURE_STRATEGIES


class LoadStrategiesTest(unittest.TestCase):
    def test_keys_every_strategy_by_its_front_matter_name(self):
        front_matters = load_strategies(FIXTURE_STRATEGIES)
        self.assertEqual(
            sorted(front_matters),
            [
                "attack-the-negative-side",
                "ladder-the-parameter",
                "prove-the-barrier-first",
                "reduce-to-a-finite-computation",
                "solve-the-model-world-first",
            ],
        )
        self.assertEqual(front_matters["ladder-the-parameter"]["precedes"], ["reduce-to-a-finite-computation"])
        self.assertEqual(front_matters["attack-the-negative-side"]["excludes"], ["prove-the-barrier-first"])

    def test_a_file_without_front_matter_is_not_a_strategy(self):
        self.assertTrue((FIXTURE_STRATEGIES / "reference.md").exists())
        self.assertTrue((FIXTURE_STRATEGIES / "README.md").exists())
        self.assertNotIn("reference", load_strategies(FIXTURE_STRATEGIES))

    def write_strategy(self, directory, front):
        (Path(directory) / "one.md").write_text("---\n%s\n---\n\n## What it moves\n" % front)

    def test_refuses_a_strategy_file_that_does_not_declare_what_it_costs(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_strategy(
                directory,
                "name: one\ncomponent: statement\ndescription: Use when x.\n"
                "entries: [e]\nprecedes: []\nexcludes: []",
            )
            with self.assertRaises(attack.ValidationError) as caught:
                load_strategies(Path(directory))
        self.assertEqual(caught.exception.problems, ["one.md: front matter is missing costs"])

    def test_refuses_a_cost_outside_the_vocabulary(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_strategy(
                directory,
                "name: one\ncomponent: statement\ndescription: Use when x.\n"
                "entries: [e]\nprecedes: []\nexcludes: []\ncosts: [elegance]",
            )
            with self.assertRaises(attack.ValidationError) as caught:
                load_strategies(Path(directory))
        self.assertEqual(
            caught.exception.problems,
            ["one.md: cost 'elegance' is not one of %s" % ", ".join(attack.COSTS)],
        )
