from tests.support import WorkspaceTest, make_move, write_journal


class StallTest(WorkspaceTest):
    def plan_nothing(self):
        """A plan that admitted no opening, which is one of the rules that start the cash-out."""
        self.write_json(
            "openings.json",
            {"generated_from": "preconditions.json", "problem_digest": "0" * 64, "openings": []},
        )

    def plan_some(self, count):
        self.write_json(
            "openings.json",
            {
                "generated_from": "preconditions.json",
                "problem_digest": "0" * 64,
                "openings": [{"strategy": "strategy-%d" % n} for n in range(count)],
            },
        )

    def test_lists_every_move_grouped_by_strategy_and_marks_the_fired_signals(self):
        """A move whose failure signal fired leaves what the strategy's Failure
        signal names, so the inventory carries it beside the rest."""
        write_journal(
            self.workspace,
            [
                make_move(1, output="a bound one rung higher"),
                make_move(2, failed=True, output="no rung reachable"),
                make_move(3, strategy="solve-the-model-world-first", entry="isolate-a-model-problem", output="the model theorem"),
                make_move(4, closes=True, output="the next rung"),
            ],
        )
        status, out, err = self.run_cli("stall", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(
            out,
            "wrote units/INVENTORY.md (4 moves, 1 ended in a failure signal); rule: the attack closed at move 4\n",
        )
        self.assertEqual(
            (self.workspace / "units" / "INVENTORY.md").read_text(),
            "# Inventory: sample\n"
            "\n"
            "Every journal move and what its output leaves in the record, grouped by\n"
            "strategy. A move marked as a fired failure signal leaves what that\n"
            "strategy's Failure signal names. Convert each into a unit under\n"
            "CASHOUT.md or discard it.\n"
            "\n"
            "Walk: attack-the-negative-side -> solve-the-model-world-first -> attack-the-negative-side.\n"
            "\n"
            "## attack-the-negative-side\n"
            "\n"
            "- move 1 (pass 1, test-strengthenings-by-counterexample): a bound one rung higher\n"
            "- move 2 (pass 1, test-strengthenings-by-counterexample, failure signal fired): no rung reachable\n"
            "- move 4 (pass 1, test-strengthenings-by-counterexample, closed the attack): the next rung\n"
            "\n"
            "## solve-the-model-world-first\n"
            "\n"
            "- move 3 (pass 1, isolate-a-model-problem): the model theorem\n",
        )

    def test_refuses_while_no_rule_has_started_the_cash_out(self):
        self.plan_some(2)
        write_journal(self.workspace, [make_move(1), make_move(2)])
        status, out, err = self.run_cli("stall", self.slug)
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(
            err,
            "stall: no rule started the cash-out (stall due: no; 2 openings admitted); continue at stage 5\n",
        )
        self.assertFalse((self.workspace / "units" / "INVENTORY.md").exists())

    def test_refuses_before_the_plan_exists(self):
        status, out, err = self.run_cli("stall", self.slug)
        self.assertEqual(
            err,
            "stall: no rule started the cash-out (stall due: no; no plan yet); continue at stage 5\n",
        )

    def test_accepts_when_a_stall_is_due_and_names_the_rule(self):
        write_journal(self.workspace, [make_move(n, failed=True) for n in range(1, 4)])
        status, out, err = self.run_cli("stall", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(
            out,
            "wrote units/INVENTORY.md (3 moves, 3 ended in a failure signal); rule: 3 consecutive failure signals\n",
        )

    def test_accepts_when_the_plan_admitted_no_opening(self):
        self.plan_nothing()
        write_journal(self.workspace, [make_move(1)])
        status, out, err = self.run_cli("stall", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(
            out,
            "wrote units/INVENTORY.md (1 moves, 0 ended in a failure signal); rule: no admissible opening\n",
        )

    def test_an_empty_journal_has_no_walk_line(self):
        self.plan_nothing()
        self.run_cli("stall", self.slug)
        text = (self.workspace / "units" / "INVENTORY.md").read_text()
        self.assertNotIn("Walk:", text)
        self.assertTrue(text.endswith("No move is journalled.\n"))

    def test_names_what_each_move_paid_and_sums_the_ledger(self):
        self.plan_nothing()
        write_journal(
            self.workspace,
            [
                make_move(1, costs_paid=["object"], output="the model theorem"),
                make_move(2, costs_paid=["object", "effectivity"], output="the bound, non-effective"),
            ],
        )
        self.run_cli("stall", self.slug)
        text = (self.workspace / "units" / "INVENTORY.md").read_text()
        self.assertIn("Walk: attack-the-negative-side.\n", text)
        self.assertIn("Costs paid across the attack: effectivity, object.\n", text)
        self.assertIn(
            "- move 2 (pass 1, test-strengthenings-by-counterexample,"
            " paid object and effectivity): the bound, non-effective\n",
            text,
        )
