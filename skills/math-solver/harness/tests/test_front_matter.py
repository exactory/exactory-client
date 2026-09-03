import unittest

from attack import parse_front_matter


class ParseFrontMatterTest(unittest.TestCase):
    def test_reads_scalars_and_flow_lists(self):
        text = (
            "---\n"
            "name: ladder-the-parameter\n"
            "component: statement\n"
            "description: Use when a parameter exists.\n"
            "entries: [embed-the-object, move-along-it]\n"
            "precedes: [reduce-to-a-finite-computation]\n"
            "excludes: []\n"
            "---\n"
            "\n## What it moves\n\nBody text.\n"
        )
        front = parse_front_matter(text)
        self.assertEqual(front["name"], "ladder-the-parameter")
        self.assertEqual(front["component"], "statement")
        self.assertEqual(front["entries"], ["embed-the-object", "move-along-it"])
        self.assertEqual(front["precedes"], ["reduce-to-a-finite-computation"])
        self.assertEqual(front["excludes"], [])
