from tests.support import WorkspaceTest, make_move, write_journal


class StallTest(WorkspaceTest):
    def test_lists_every_move_grouped_by_strategy_and_marks_the_fired_signals(self):
        """A move whose failure signal fired leaves what the strategy's Failure
        signal names, so the inventory carries it beside the rest."""
        write_journal(
            self.workspace,
            [
                make_move(1, output="a bound one rung higher"),
                make_move(2, failed=True, output="no rung reachable"),
                make_move(3, strategy="solve-the-model-world-first", entry="isolate-a-model-problem", output="the model theorem"),
                make_move(4, output="the next rung"),
            ],
        )
        status, out, err = self.run_cli("stall", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "wrote units/INVENTORY.md (4 moves, 1 ended in a failure signal)\n")
        self.assertEqual(
            (self.workspace / "units" / "INVENTORY.md").read_text(),
            "# Inventory: sample\n"
            "\n"
            "Every journal move and what its output leaves in the record, grouped by\n"
            "strategy. A move marked as a fired failure signal leaves what that\n"
            "strategy's Failure signal names. Convert each into a unit under\n"
            "CASHOUT.md or discard it.\n"
            "\n"
            "## ladder-the-parameter\n"
            "\n"
            "- move 1 (pass 1, embed-the-object-in-a-family-and-move-along-it): a bound one rung higher\n"
            "- move 2 (pass 1, embed-the-object-in-a-family-and-move-along-it, failure signal fired): no rung reachable\n"
            "- move 4 (pass 1, embed-the-object-in-a-family-and-move-along-it): the next rung\n"
            "\n"
            "## solve-the-model-world-first\n"
            "\n"
            "- move 3 (pass 1, isolate-a-model-problem): the model theorem\n",
        )

    def test_says_so_when_the_journal_is_empty(self):
        status, out, err = self.run_cli("stall", self.slug)
        self.assertEqual(out, "wrote units/INVENTORY.md (0 moves, 0 ended in a failure signal)\n")
        self.assertTrue(
            (self.workspace / "units" / "INVENTORY.md").read_text().endswith("No move is journalled.\n")
        )

    def test_names_what_each_move_paid_and_sums_the_ledger(self):
        write_journal(
            self.workspace,
            [
                make_move(1, costs_paid=["object"], output="the model theorem"),
                make_move(2, costs_paid=["object", "effectivity"], output="the bound, non-effective"),
            ],
        )
        self.run_cli("stall", self.slug)
        text = (self.workspace / "units" / "INVENTORY.md").read_text()
        self.assertIn("Costs paid across the attack: effectivity, object.\n", text)
        self.assertIn(
            "- move 2 (pass 1, embed-the-object-in-a-family-and-move-along-it,"
            " paid object and effectivity): the bound, non-effective\n",
            text,
        )
