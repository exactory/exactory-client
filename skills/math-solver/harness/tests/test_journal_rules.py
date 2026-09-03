"""The flow rules `journal add` enforces beyond the schema: the strategy belongs to
the composition and dispatches the entry, the trigger names shape fields that
carry a value, and the compositions and their strategies are taken in the
order the ranking gives."""

import hashlib
import json

from tests.support import (
    RANK_ONE,
    WorkspaceTest,
    make_move,
    prepare_plan,
    write_ranking,
    write_study,
)

ENTRY_OF = {
    "attack-the-negative-side": "test-strengthenings-by-counterexample",
    "ladder-the-parameter": "embed-the-object-in-a-family-and-move-along-it",
    "reduce-to-a-finite-computation": "reduce-to-finite-witnesses",
    "solve-the-model-world-first": "isolate-a-model-problem",
    "prove-the-barrier-first": "axiomatise-the-method-and-build-a-near-miss",
}


class JournalRulesTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        for name in ENTRY_OF:
            write_study(self.workspace, name)
        prepare_plan(self)

    def add(self, move):
        return self.run_cli("journal", "add", self.slug, "--json", json.dumps(move))

    def move_under(self, number, strategy, **overrides):
        return make_move(number, strategy=strategy, entry=ENTRY_OF[strategy], **overrides)

    def ordered_ids(self):
        return [row["composition"] for row in self.read_json("ranking.json")["order"]]

    def set_shape(self, **values):
        problem = self.read_json("problem.json")
        problem["shape"].update(values)
        self.write_json("problem.json", problem)

    # The strategy and the entry

    def test_rejects_a_strategy_outside_the_composition(self):
        self.assertEqual(
            self.add(self.move_under(1, "prove-the-barrier-first"))[2],
            "move: strategy prove-the-barrier-first is not in composition %s\n" % RANK_ONE,
        )

    def test_rejects_an_entry_the_strategy_does_not_dispatch(self):
        self.assertEqual(
            self.add(make_move(1, entry="isolate-a-model-problem"))[2],
            "move: entry isolate-a-model-problem is not dispatched by attack-the-negative-side\n",
        )

    # The trigger

    def test_rejects_empty_trigger_features(self):
        self.assertEqual(
            self.add(make_move(1, trigger_features=[]))[2],
            "move: trigger_features is empty; a move exists only where the entry's trigger matches a shape field\n",
        )

    def test_rejects_a_trigger_feature_that_is_not_a_problem_field(self):
        self.assertEqual(
            self.add(make_move(1, trigger_features=["shape.nothing"]))[2],
            "move: trigger_features cites shape.nothing, which is not a problem.json field\n",
        )

    def test_rejects_a_trigger_feature_whose_value_is_unknown(self):
        self.set_shape(target_quantity="unknown")
        self.assertEqual(
            self.add(make_move(1, problem_changed=True))[2],
            "move: trigger_features cites shape.target_quantity, whose value is unknown\n",
        )

    # The order of strategies within a composition

    def test_rejects_a_first_move_under_a_later_strategy_of_the_composition(self):
        self.assertEqual(
            self.add(self.move_under(1, "ladder-the-parameter"))[2],
            "move: composition %s reaches ladder-the-parameter after attack-the-negative-side, which has no move yet\n" % RANK_ONE,
        )

    def test_accepts_the_next_strategy_once_the_one_before_it_has_a_move(self):
        self.assertEqual(self.add(make_move(1))[0], 0)
        self.assertEqual(self.add(self.move_under(2, "ladder-the-parameter"))[0], 0)

    def test_rejects_a_return_to_an_earlier_strategy_of_the_composition(self):
        self.add(make_move(1))
        self.add(self.move_under(2, "ladder-the-parameter"))
        self.assertEqual(
            self.add(make_move(3))[2],
            "move: composition %s has moved on from attack-the-negative-side to ladder-the-parameter\n" % RANK_ONE,
        )

    # The order of compositions

    def test_rejects_a_first_move_under_a_composition_that_is_not_first_in_the_order(self):
        second = self.ordered_ids()[1]
        move = self.move_under(1, second.split("+")[0], composition=second)
        self.assertEqual(
            self.add(move)[2],
            "move: composition %s is not the current one; the order gives %s\n" % (second, RANK_ONE),
        )

    def test_accepts_continuing_the_previous_composition_after_the_order_changes(self):
        self.add(make_move(1))
        ranking = self.read_json("ranking.json")
        ranking["order"].reverse()
        self.write_json("ranking.json", ranking)
        self.assertEqual(self.add(make_move(2))[0], 0)

    def test_moves_on_to_the_first_composition_with_a_strategy_not_yet_executed(self):
        executed = RANK_ONE.split("+")
        for number, strategy in enumerate(executed, start=1):
            self.assertEqual(self.add(self.move_under(number, strategy))[0], 0)
        ordered = self.ordered_ids()
        first_open = next(
            identifier for identifier in ordered
            if any(strategy not in executed for strategy in identifier.split("+"))
        )
        closed = next(
            identifier for identifier in ordered
            if identifier != RANK_ONE and all(strategy in executed for strategy in identifier.split("+"))
        )
        refused = self.move_under(5, closed.split("+")[0], composition=closed)
        self.assertEqual(
            self.add(refused)[2],
            "move: composition %s is not the current one; the order gives %s or %s\n" % (closed, RANK_ONE, first_open),
        )
        next_strategy = next(strategy for strategy in first_open.split("+") if strategy not in executed)
        self.assertEqual(self.add(self.move_under(5, next_strategy, composition=first_open))[0], 0)


def digest_of(problem):
    return hashlib.sha256(json.dumps(problem, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class JournalRecordTest(WorkspaceTest):
    """What a journal line records beyond the move itself: the deterministic steps the
    move ran, whether it closed the attack, and whether problem.json changed under it."""

    def setUp(self):
        super().setUp()
        write_study(self.workspace, "attack-the-negative-side")
        prepare_plan(self)

    def add(self, move):
        return self.run_cli("journal", "add", self.slug, "--json", json.dumps(move))

    def journal(self):
        return [json.loads(line) for line in (self.workspace / "journal.jsonl").read_text().splitlines()]

    def set_problem(self, **parts):
        problem = self.read_json("problem.json")
        for key, values in parts.items():
            problem[key].update(values)
        self.write_json("problem.json", problem)

    def make_step(self, name, result=None):
        step_dir = self.workspace / "deterministic" / name
        step_dir.mkdir()
        if result is not None:
            (step_dir / "result.json").write_text(json.dumps(result))

    # The steps the move ran

    def test_rejects_a_step_directory_that_does_not_exist(self):
        self.assertEqual(
            self.add(make_move(1, steps=["enumeration-run-1"]))[2],
            "move: steps names deterministic/enumeration-run-1, which does not exist\n",
        )

    def test_rejects_a_step_that_has_no_result(self):
        self.make_step("enumeration-run-1")
        self.assertEqual(
            self.add(make_move(1, steps=["enumeration-run-1"]))[2],
            "move: steps names deterministic/enumeration-run-1, which has no result.json; run verify first\n",
        )

    def test_records_the_steps_the_move_ran(self):
        self.make_step("enumeration-run-1", {"status": "pass"})
        self.assertEqual(self.add(make_move(1, steps=["enumeration-run-1"]))[0], 0)
        self.assertEqual(self.journal()[0]["steps"], ["enumeration-run-1"])

    # Closing

    def test_rejects_a_closing_move_while_the_direction_is_undecided(self):
        self.set_problem(quadruple={"direction": "undecided"})
        self.assertEqual(
            self.add(make_move(1, closes=True, problem_changed=True))[2],
            "move: closes, and quadruple.direction is undecided\n",
        )

    def test_rejects_a_closing_move_while_the_mode_is_undecided(self):
        self.set_problem(quadruple={"mode": "undecided"})
        self.assertEqual(
            self.add(make_move(1, closes=True, problem_changed=True))[2],
            "move: closes, and quadruple.mode is undecided\n",
        )

    def test_a_closing_move_ends_the_attack(self):
        status, out, err = self.add(make_move(1, closes=True))
        self.assertEqual((status, err), (0, ""))
        self.assertTrue(out.endswith("stall due: yes (the attack closed at move 1)\n"), out)
        self.assertEqual(self.add(make_move(2))[2], "move: stall is due (the attack closed at move 1)\n")

    # problem.json under the move

    def test_records_the_problem_digest_on_every_line(self):
        self.add(make_move(1))
        self.assertEqual(self.journal()[0]["problem_digest"], digest_of(self.read_json("problem.json")))

    def test_rejects_a_line_that_supplies_its_own_digest(self):
        self.assertEqual(self.add(make_move(1, problem_digest="0"))[2], "move: unknown field problem_digest\n")

    def test_validates_problem_json_before_the_move(self):
        self.set_problem(quadruple={"statement": ""})
        self.assertEqual(self.add(make_move(1, problem_changed=True))[2], "quadruple.statement: empty string\n")

    def test_rejects_problem_changed_false_after_problem_json_changed(self):
        self.add(make_move(1))
        self.set_problem(shape={"objects": "finite sets of integers"})
        self.assertEqual(
            self.add(make_move(2))[2],
            "move: problem_changed is false, and problem.json changed since move 1\n",
        )

    def test_rejects_problem_changed_true_while_problem_json_is_unchanged(self):
        self.assertEqual(
            self.add(make_move(1, problem_changed=True))[2],
            "move: problem_changed is true, and problem.json is unchanged since plan\n",
        )
        self.add(make_move(1))
        self.assertEqual(
            self.add(make_move(2, problem_changed=True))[2],
            "move: problem_changed is true, and problem.json is unchanged since move 1\n",
        )

    def test_accepts_problem_changed_true_with_a_changed_problem(self):
        self.add(make_move(1))
        self.set_problem(shape={"objects": "finite sets of integers"})
        self.assertEqual(self.add(make_move(2, problem_changed=True))[0], 0)
        self.assertEqual(self.journal()[1]["problem_digest"], digest_of(self.read_json("problem.json")))

    def test_a_new_pass_needs_a_plan_over_the_changed_problem(self):
        self.add(make_move(1))
        self.set_problem(shape={"objects": "finite sets of integers"})
        self.add(make_move(2, problem_changed=True))
        self.assertEqual(
            self.add(make_move(3, 2))[2],
            "move: problem.json changed in pass 1, and compositions.json predates the change; run plan and rank before pass 2\n",
        )
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        write_ranking(self)
        self.assertEqual(self.add(make_move(3, 2))[0], 0)

    def test_a_new_pass_after_an_unchanged_pass_needs_no_plan(self):
        self.add(make_move(1))
        self.assertEqual(self.add(make_move(2, 2))[0], 0)
