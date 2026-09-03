"""The flow rules `journal add` enforces beyond the schema: the strategy is admitted
and dispatches the entry, the trigger names shape fields that carry a value, and
the walk opens at the first strategy of the ranking and grows one admissible step
at a time, each step citing the record."""

import hashlib
import json

from tests.support import (
    ALL_YES,
    OPENING,
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

    def step(self, number, strategy, walk, cites=("shape.target_quantity",), **overrides):
        """A move that enters `strategy`, extending the walk to `walk`."""
        return make_move(
            number, strategy=strategy, entry=ENTRY_OF[strategy], walk=walk,
            step_cites=list(cites), **overrides,
        )

    def set_shape(self, **values):
        problem = self.read_json("problem.json")
        problem["shape"].update(values)
        self.write_json("problem.json", problem)

    # The strategy and the entry

    def test_rejects_a_strategy_whose_verdict_is_no(self):
        self.add(make_move(1))
        self.run_cli("fail", self.slug, OPENING)
        self.assertEqual(self.add(make_move(2))[2], "move: strategy %s has verdict no\n" % OPENING)

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

    # The opening

    def test_the_attack_opens_with_the_first_strategy_of_the_ranking(self):
        self.assertEqual(
            self.add(self.step(1, "ladder-the-parameter", "ladder-the-parameter", cites=()))[2],
            "move: the attack opens with attack-the-negative-side, the first strategy of ranking.json\n",
        )

    def test_the_opening_follows_the_order_the_solver_wrote(self):
        ranking = self.read_json("ranking.json")
        ranking["order"].reverse()
        self.write_json("ranking.json", ranking)
        first = ranking["order"][0]["strategy"]
        self.assertEqual(self.add(self.step(1, first, first, cites=()))[0], 0)

    def test_rejects_step_cites_on_the_opening_move(self):
        self.assertEqual(
            self.add(make_move(1, step_cites=["shape.objects"]))[2],
            "move: step_cites is not empty; the opening is justified in ranking.json\n",
        )

    # The walk

    def test_the_walk_is_the_strategy_on_the_opening_move(self):
        self.assertEqual(
            self.add(make_move(1, walk="attack-the-negative-side+ladder-the-parameter"))[2],
            "move: walk must be attack-the-negative-side\n",
        )

    def test_a_move_continuing_the_strategy_keeps_the_walk(self):
        self.add(make_move(1))
        self.assertEqual(self.add(make_move(2))[0], 0)
        self.assertEqual(
            self.add(make_move(3, walk="attack-the-negative-side+attack-the-negative-side"))[2],
            "move: walk must be attack-the-negative-side\n",
        )

    def test_rejects_step_cites_while_continuing_a_strategy(self):
        self.add(make_move(1))
        self.assertEqual(
            self.add(make_move(2, step_cites=["shape.objects"]))[2],
            "move: step_cites is not empty; the move continues attack-the-negative-side\n",
        )

    def test_entering_a_strategy_extends_the_walk(self):
        self.add(make_move(1))
        walk = "attack-the-negative-side+ladder-the-parameter"
        status, out, err = self.add(self.step(2, "ladder-the-parameter", walk))
        self.assertEqual((status, err), (0, ""))
        lines = (self.workspace / "journal.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(lines[1])["walk"], walk)

    def test_rejects_a_step_whose_walk_does_not_extend_the_previous_one(self):
        self.add(make_move(1))
        self.assertEqual(
            self.add(self.step(2, "ladder-the-parameter", "ladder-the-parameter"))[2],
            "move: walk must be attack-the-negative-side+ladder-the-parameter\n",
        )

    def test_a_walk_may_return_to_a_strategy_it_left(self):
        self.add(make_move(1))
        self.add(self.step(2, "ladder-the-parameter", "attack-the-negative-side+ladder-the-parameter"))
        walk = "attack-the-negative-side+ladder-the-parameter+attack-the-negative-side"
        self.assertEqual(self.add(self.step(3, OPENING, walk))[0], 0)

    def test_the_ranking_is_read_for_the_opening_only(self):
        self.add(make_move(1))
        self.write_json("ranking.json", {"generated_from": "openings.json", "order": []})
        self.assertEqual(self.add(make_move(2))[0], 0)
        walk = "attack-the-negative-side+ladder-the-parameter"
        self.assertEqual(self.add(self.step(3, "ladder-the-parameter", walk))[0], 0)

    # What a step cites

    def test_rejects_a_step_that_cites_nothing(self):
        self.add(make_move(1))
        walk = "attack-the-negative-side+ladder-the-parameter"
        self.assertEqual(
            self.add(self.step(2, "ladder-the-parameter", walk, cites=()))[2],
            "move: step_cites is empty; a step into ladder-the-parameter cites the fields and costs that put it next\n",
        )

    def test_rejects_a_step_citation_that_names_no_field_and_no_cost(self):
        self.add(make_move(1))
        walk = "attack-the-negative-side+ladder-the-parameter"
        self.assertEqual(
            self.add(self.step(2, "ladder-the-parameter", walk, cites=("shape.nope",)))[2],
            "move: step_cites cites shape.nope, which is not a problem.json field or a cost\n",
        )

    def test_a_step_may_cite_a_cost_the_strategy_declares_and_no_other(self):
        self.add(make_move(1))
        walk = "attack-the-negative-side+ladder-the-parameter"
        self.assertEqual(
            self.add(self.step(2, "ladder-the-parameter", walk, cites=("cost:axioms",)))[2],
            "move: step_cites cites cost:axioms, which ladder-the-parameter does not declare\n",
        )
        self.assertEqual(self.add(self.step(2, "ladder-the-parameter", walk, cites=("cost:bound_quality",)))[0], 0)

    # Admissibility of a step

    def test_rejects_a_step_into_a_strategy_the_walk_excludes(self):
        """attack-the-negative-side lists prove-the-barrier-first under excludes in the fixture."""
        self.add(make_move(1))
        walk = "attack-the-negative-side+prove-the-barrier-first"
        self.assertEqual(
            self.add(self.step(2, "prove-the-barrier-first", walk))[2],
            "move: prove-the-barrier-first and attack-the-negative-side exclude each other, and attack-the-negative-side has run\n",
        )

    def test_rejects_a_step_that_must_come_before_a_strategy_that_ran(self):
        """ladder-the-parameter lists reduce-to-a-finite-computation under precedes in the fixture."""
        self.add(make_move(1))
        self.add(self.step(2, "reduce-to-a-finite-computation", "attack-the-negative-side+reduce-to-a-finite-computation"))
        walk = "attack-the-negative-side+reduce-to-a-finite-computation+ladder-the-parameter"
        self.assertEqual(
            self.add(self.step(3, "ladder-the-parameter", walk))[2],
            "move: ladder-the-parameter precedes reduce-to-a-finite-computation, which has already run\n",
        )

    def test_accepts_the_order_precedes_asks_for(self):
        self.add(make_move(1))
        self.add(self.step(2, "ladder-the-parameter", "attack-the-negative-side+ladder-the-parameter"))
        walk = "attack-the-negative-side+ladder-the-parameter+reduce-to-a-finite-computation"
        self.assertEqual(self.add(self.step(3, "reduce-to-a-finite-computation", walk))[0], 0)

    def test_rejects_a_second_assumption_in_the_walk(self):
        verdicts = dict(ALL_YES, **{"ladder-the-parameter": "unknown", "solve-the-model-world-first": "unknown"})
        prepare_plan(self, verdicts)
        self.add(make_move(1))
        self.add(self.step(2, "ladder-the-parameter", "attack-the-negative-side+ladder-the-parameter"))
        walk = "attack-the-negative-side+ladder-the-parameter+solve-the-model-world-first"
        self.assertEqual(
            self.add(self.step(3, "solve-the-model-world-first", walk))[2],
            "move: the walk would rest on two assumptions, ladder-the-parameter and solve-the-model-world-first\n",
        )


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
            "move: problem.json changed in pass 1, and openings.json predates the change; run plan before pass 2\n",
        )
        self.assertEqual(self.run_cli("plan", self.slug)[0], 0)
        self.assertEqual(self.add(make_move(3, 2))[0], 0)

    def test_a_new_pass_after_an_unchanged_pass_needs_no_plan(self):
        self.add(make_move(1))
        self.assertEqual(self.add(make_move(2, 2))[0], 0)
