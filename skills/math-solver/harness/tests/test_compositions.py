import unittest

import attack
from attack import enumerate_compositions


QUADRUPLE = {"direction": "true", "mode": "existence"}


def rank_all(front_matters, verdicts, quadruple=None):
    return enumerate_compositions(front_matters, verdicts, quadruple or QUADRUPLE)


def strategy(component, precedes=(), excludes=(), costs=()):
    return {
        "component": component,
        "precedes": list(precedes),
        "excludes": list(excludes),
        "costs": list(costs),
    }


def sequences(compositions):
    return [tuple(c["strategies"]) for c in compositions]


class EnumerateCompositionsTest(unittest.TestCase):
    def test_a_no_verdict_is_never_a_candidate(self):
        front = {"a": strategy("statement"), "b": strategy("stage")}
        ranked = rank_all(front, {"a": "yes", "b": "no"})
        self.assertEqual(sequences(ranked), [("a",)])

    def test_selections_are_ordered_without_repetition_up_to_length_four(self):
        names = ["a", "b", "c", "d", "e"]
        front = {name: strategy("statement") for name in names}
        ranked = rank_all(front, {name: "yes" for name in names})
        for composition in ranked:
            chosen = composition["strategies"]
            self.assertLessEqual(len(chosen), 4)
            self.assertEqual(len(chosen), len(set(chosen)))

    def test_emits_at_most_twenty(self):
        names = ["a", "b", "c", "d", "e"]
        front = {name: strategy("statement") for name in names}
        ranked = rank_all(front, {name: "yes" for name in names})
        self.assertEqual(len(ranked), 20)
        self.assertEqual([c["rank"] for c in ranked], list(range(1, 21)))

    def test_at_most_one_unknown_and_it_is_named_as_the_assumption(self):
        front = {"a": strategy("statement"), "u": strategy("stage"), "v": strategy("mode")}
        ranked = rank_all(front, {"a": "yes", "u": "unknown", "v": "unknown"})
        for composition in ranked:
            unknowns = [s for s in composition["strategies"] if s in ("u", "v")]
            self.assertLessEqual(len(unknowns), 1)
            self.assertEqual(composition["unknown"], len(unknowns))
            self.assertEqual(composition["assumption"], unknowns[0] if unknowns else None)
        self.assertIn(("a", "u"), sequences(ranked))
        self.assertNotIn(("u", "v"), sequences(ranked))

    def test_excludes_removes_every_selection_holding_both(self):
        front = {
            "a": strategy("statement", excludes=["b"]),
            "b": strategy("stage"),
            "c": strategy("mode"),
        }
        ranked = rank_all(front, {"a": "yes", "b": "yes", "c": "yes"})
        for chosen in sequences(ranked):
            self.assertFalse("a" in chosen and "b" in chosen, chosen)
        self.assertIn(("a", "c"), sequences(ranked))
        self.assertIn(("b", "c"), sequences(ranked))

    def test_precedes_fixes_the_order_when_both_are_chosen(self):
        front = {"a": strategy("statement", precedes=["b"]), "b": strategy("stage")}
        ranked = rank_all(front, {"a": "yes", "b": "yes"})
        self.assertEqual(sequences(ranked), [("a", "b"), ("a",), ("b",)])

    def test_ranks_by_yes_count_then_distinct_components_then_name_order(self):
        front = {"a": strategy("statement"), "b": strategy("stage"), "c": strategy("statement")}
        ranked = rank_all(front, {"a": "yes", "b": "yes", "c": "yes"})
        self.assertEqual(
            sequences(ranked),
            [
                ("a", "b", "c"), ("a", "c", "b"), ("b", "a", "c"),
                ("b", "c", "a"), ("c", "a", "b"), ("c", "b", "a"),
                ("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"),
                ("a", "c"), ("c", "a"),
                ("a",), ("b",), ("c",),
            ],
        )

    def test_an_unknown_that_adds_a_component_outranks_the_yes_only_selection(self):
        front = {"a": strategy("statement"), "b": strategy("stage"), "u": strategy("mode")}
        ranked = rank_all(front, {"a": "yes", "b": "yes", "u": "unknown"})
        self.assertEqual(sequences(ranked)[0], ("a", "b", "u"))

    def test_records_the_counts_and_sorted_distinct_components(self):
        front = {"a": strategy("statement"), "b": strategy("stage"), "u": strategy("mode")}
        ranked = rank_all(front, {"a": "yes", "b": "yes", "u": "unknown"})
        first = ranked[0]
        self.assertEqual(first["strategies"], ["a", "b", "u"])
        self.assertEqual(first["yes"], 2)
        self.assertEqual(first["unknown"], 1)
        self.assertEqual(first["components"], ["mode", "stage", "statement"])
        self.assertEqual(first["assumption"], "u")


class SpreadTest(unittest.TestCase):
    """The emitted list is capped, so it spreads over the leading strategies:
    a candidate the solver can never reach through it is a candidate the
    precondition scan admitted for nothing."""

    COMPONENTS = ("statement", "stage", "direction", "mode", "organisation")

    def front_matters(self, count):
        return {
            "s%d" % n: {
                "name": "s%d" % n,
                "component": self.COMPONENTS[n % len(self.COMPONENTS)],
                "precedes": [],
                "excludes": [],
            }
            for n in range(1, count + 1)
        }

    def test_every_candidate_leads_at_least_one_emitted_composition(self):
        front_matters = self.front_matters(8)
        verdicts = {name: "yes" for name in front_matters}
        rows = rank_all(front_matters, verdicts)
        self.assertEqual(len(rows), attack.COMPOSITION_LIMIT)
        self.assertEqual(
            sorted({row["strategies"][0] for row in rows}), sorted(front_matters)
        )

    def test_the_first_rank_is_the_highest_ranked_composition(self):
        front_matters = self.front_matters(8)
        verdicts = {name: "yes" for name in front_matters}
        rows = rank_all(front_matters, verdicts)
        self.assertEqual(rows[0]["strategies"], ["s1", "s2", "s3", "s4"])
        self.assertEqual(rows[0]["rank"], 1)


class DeclaredCostTest(unittest.TestCase):
    """A declared cost is what a move under the strategy CAN take away, so it does not
    drop the strategy. The contradiction is caught at the move that pays it."""

    FRONT = {
        "keeps": strategy("statement"),
        "may-lose-construction": strategy("stage", costs=["constructivity"]),
        "may-lose-equivalence": strategy("mode", costs=["implication"]),
    }

    def rows(self, direction, mode):
        return enumerate_compositions(
            self.FRONT, {name: "yes" for name in self.FRONT}, {"direction": direction, "mode": mode}
        )

    def test_a_declared_cost_never_drops_a_strategy(self):
        for direction, mode in (("false", "construction"), ("true", "existence")):
            names = {name for row in self.rows(direction, mode) for name in row["strategies"]}
            self.assertEqual(sorted(names), sorted(self.FRONT), "%s / %s" % (direction, mode))

    def test_a_composition_carries_what_its_strategies_may_spend(self):
        rows = self.rows("true", "existence")
        longest = max(rows, key=lambda row: len(row["strategies"]))
        self.assertEqual(longest["costs"], ["constructivity", "implication"])
        alone = next(row for row in rows if row["strategies"] == ["keeps"])
        self.assertEqual(alone["costs"], [])


class CompositionIdentityTest(unittest.TestCase):
    """A move names the composition it belongs to, so a composition needs a name that
    survives a re-plan. Its rank does not: `fail` renumbers the whole list."""

    def test_the_id_is_the_strategies_joined(self):
        front = {"a": strategy("statement"), "b": strategy("stage")}
        rows = rank_all(front, {"a": "yes", "b": "yes"})
        self.assertEqual(rows[0]["id"], "+".join(rows[0]["strategies"]))
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
