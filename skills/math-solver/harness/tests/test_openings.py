"""`plan` emits the openings: the strategies the scan admits, one row each, which
the solver then ranks. The walk starts at the first of the ranking and grows one
step at a time under the walk rules `journal add` enforces."""

import unittest

from attack import list_openings


def strategy(component, costs=()):
    return {"component": component, "precedes": [], "excludes": [], "costs": list(costs)}


def names(openings):
    return [row["strategy"] for row in openings]


class ListOpeningsTest(unittest.TestCase):
    def test_a_no_verdict_is_never_an_opening(self):
        front = {"a": strategy("statement"), "b": strategy("stage")}
        self.assertEqual(names(list_openings(front, {"a": "yes", "b": "no"})), ["a"])

    def test_yes_comes_before_unknown_and_names_order_the_rest(self):
        front = {name: strategy("statement") for name in ("c", "a", "u", "b")}
        verdicts = {"c": "yes", "a": "unknown", "u": "unknown", "b": "yes"}
        self.assertEqual(names(list_openings(front, verdicts)), ["b", "c", "a", "u"])

    def test_a_row_carries_the_rank_the_verdict_the_component_and_the_declared_costs(self):
        front = {"a": strategy("stage", costs=["object", "axioms"])}
        self.assertEqual(
            list_openings(front, {"a": "unknown"}),
            [{"rank": 1, "strategy": "a", "verdict": "unknown", "component": "stage", "costs": ["axioms", "object"]}],
        )

    def test_nothing_admitted_gives_an_empty_list(self):
        front = {"a": strategy("statement")}
        self.assertEqual(list_openings(front, {"a": "no"}), [])
