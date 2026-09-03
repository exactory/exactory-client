import json

from tests.support import (
    RANK_ONE,
    WorkspaceTest,
    make_move,
    prepare_plan,
    write_journal,
    write_ranking,
    write_study,
)


class JournalAddTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        write_study(self.workspace, "attack-the-negative-side")
        prepare_plan(self)

    def add(self, move):
        return self.run_cli("journal", "add", self.slug, "--json", json.dumps(move))

    def journal_lines(self):
        return (self.workspace / "journal.jsonl").read_text().splitlines()

    def test_refuses_a_move_whose_strategy_has_no_study_record(self):
        (self.workspace / "study" / "attack-the-negative-side.md").unlink()
        status, out, err = self.add(make_move(1))
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, "study/attack-the-negative-side.md: missing or empty; write the strategy's study (STUDY.md) before its first move\n")
        self.assertEqual(self.journal_lines(), [])

    def test_refuses_a_move_whose_strategy_study_is_empty(self):
        write_study(self.workspace, "attack-the-negative-side", "\n")
        self.assertEqual(self.add(make_move(1))[2], "study/attack-the-negative-side.md: missing or empty; write the strategy's study (STUDY.md) before its first move\n")

    def test_appends_the_move_and_prints_the_budget(self):
        status, out, err = self.add(make_move(1))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "moves this pass: 1/8\nmoves overall: 1/24\npasses used: 1/3\nstall due: no\n")
        lines = [json.loads(line) for line in self.journal_lines()]
        self.assertEqual(len(lines[0].pop("problem_digest")), 64)
        self.assertEqual(lines, [make_move(1)])

    def test_appends_a_second_move_after_the_first(self):
        self.add(make_move(1))
        self.assertEqual(self.add(make_move(2))[0], 0)
        self.assertEqual(len(self.journal_lines()), 2)

    def test_rejects_text_that_is_not_json(self):
        status, out, err = self.run_cli("journal", "add", self.slug, "--json", "{oops")
        self.assertEqual(status, 1)
        self.assertTrue(err.startswith("--json: not valid JSON"), err)

    def test_rejects_a_missing_field(self):
        move = make_move(1)
        del move["output"]
        self.assertEqual(self.add(move)[2], "move: missing output\n")

    def test_rejects_an_unknown_field(self):
        self.assertEqual(self.add(make_move(1, mood="hopeful"))[2], "move: unknown field mood\n")

    def test_rejects_a_field_of_the_wrong_type(self):
        self.assertEqual(self.add(make_move(1, failure_signal_fired="no"))[2], "move: failure_signal_fired must be bool\n")
        self.assertEqual(self.add(make_move(True))[2], "move: move must be int\n")
        self.assertEqual(self.add(make_move(1, trigger_features="shape.objects"))[2], "move: trigger_features must be a list of str\n")
        self.assertEqual(self.add(make_move(1, trigger_features=[1]))[2], "move: trigger_features must be a list of str\n")

    def test_rejects_a_move_number_out_of_sequence(self):
        self.add(make_move(1))
        self.assertEqual(self.add(make_move(3))[2], "move: move must be 2\n")

    def test_rejects_a_pass_that_is_not_the_current_or_the_next(self):
        self.assertEqual(self.add(make_move(1, 2))[2], "move: pass must be 1\n")
        self.add(make_move(1))
        self.assertEqual(self.add(make_move(2, 3))[2], "move: pass must be 1 or 2\n")

    def test_rejects_a_fourth_pass(self):
        write_journal(self.workspace, [make_move(1), make_move(2, 2), make_move(3, 3)])
        self.assertEqual(self.add(make_move(4, 4))[2], "move: pass must be 3\n")

    def test_rejects_a_move_in_a_spent_pass(self):
        write_journal(self.workspace, [make_move(n) for n in range(1, 9)])
        self.assertEqual(self.add(make_move(9))[2], "move: pass 1 is spent; start pass 2\n")
        self.assertEqual(self.add(make_move(9, 2))[0], 0)

    def test_rejects_a_move_when_a_stall_is_due(self):
        write_journal(self.workspace, [make_move(n, failed=True) for n in range(1, 4)])
        status, out, err = self.add(make_move(4))
        self.assertEqual((status, err), (1, "move: stall is due (3 consecutive failure signals)\n"))
        self.assertEqual(len(self.journal_lines()), 3)

    def test_rejects_a_move_after_the_last_pass_is_spent(self):
        write_journal(self.workspace, [make_move(1), make_move(2, 2)] + [make_move(n, 3) for n in range(3, 11)])
        self.assertEqual(self.add(make_move(11, 3))[2], "move: stall is due (last pass spent)\n")

    def test_rejects_a_composition_the_ranking_does_not_carry(self):
        self.assertEqual(
            self.add(make_move(1, composition="ladder-the-parameter+nowhere"))[2],
            "move: composition ladder-the-parameter+nowhere is not in ranking.json\n",
        )

    def test_rejects_a_move_while_the_ranking_does_not_cover_the_current_plan(self):
        self.write_json("ranking.json", {"generated_from": "compositions.json", "order": []})
        status, out, err = self.add(make_move(1))
        self.assertEqual((status, out), (1, ""))
        self.assertTrue(err.startswith("ranking.json:"), err)

    def test_rejects_a_cost_outside_the_vocabulary(self):
        self.assertEqual(
            self.add(make_move(1, costs_paid=["elegance"]))[2],
            "move: costs_paid 'elegance' is not one of implication, effectivity, constructivity,"
            " bound_quality, axioms, object, obligations\n",
        )

    def test_accepts_a_move_that_paid_a_cost(self):
        self.assertEqual(self.add(make_move(1, costs_paid=["object", "effectivity"]))[0], 0)
        self.assertEqual(json.loads(self.journal_lines()[0])["costs_paid"], ["object", "effectivity"])

    def set_quadruple(self, **values):
        problem = self.read_json("problem.json")
        problem["quadruple"].update(values)
        self.write_json("problem.json", problem)

    def test_refuses_a_move_that_pays_constructivity_when_the_mode_is_construction(self):
        self.set_quadruple(mode="construction")
        self.assertEqual(
            self.add(make_move(1, costs_paid=["constructivity"]))[2],
            "move: pays constructivity, and quadruple.mode is construction\n",
        )

    def test_refuses_a_move_that_pays_implication_when_the_direction_is_false(self):
        self.set_quadruple(direction="false")
        self.assertEqual(
            self.add(make_move(1, costs_paid=["implication"]))[2],
            "move: pays implication, and quadruple.direction is false\n",
        )

    def test_accepts_the_same_cost_under_a_quadruple_it_does_not_contradict(self):
        self.assertEqual(self.add(make_move(1, costs_paid=["constructivity", "implication"]))[0], 0)
