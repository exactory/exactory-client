import json

from tests.support import (
    ALL_YES,
    WorkspaceTest,
    make_move,
    make_preconditions,
    make_problem,
    write_journal,
    write_ranking,
    write_study,
)


class FailTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.write_json("problem.json", make_problem())
        write_study(self.workspace, "problem")
        self.write_json("preconditions.json", make_preconditions(ALL_YES))
        self.run_cli("plan", self.slug)

    def test_sets_the_verdict_to_no_with_a_note_and_replans(self):
        status, out, err = self.run_cli("fail", self.slug, "ladder-the-parameter")
        self.assertEqual((status, err), (0, ""))
        record = self.read_json("preconditions.json")["ladder-the-parameter"]
        self.assertEqual(record["verdict"], "no")
        self.assertEqual(record["note"], "set to no by fail: the strategy ended in its failure signal")
        openings = [row["strategy"] for row in self.read_json("openings.json")["openings"]]
        self.assertNotIn("ladder-the-parameter", openings)
        self.assertTrue(out.startswith("1. attack-the-negative-side  verdict=yes"), out)
        self.assertNotIn("ladder-the-parameter", out)

    def test_rejects_a_strategy_absent_from_the_preconditions(self):
        status, out, err = self.run_cli("fail", self.slug, "no-such-strategy")
        self.assertEqual((status, err), (1, "preconditions.json: no entry for no-such-strategy\n"))

    def test_ending_a_strategy_clears_the_consecutive_failure_window(self):
        write_journal(self.workspace, [make_move(n, failed=True) for n in range(1, 4)])
        self.assertIn("stall due: yes", self.run_cli("budget", self.slug)[1])
        self.assertEqual(self.run_cli("fail", self.slug, "ladder-the-parameter")[0], 0)
        self.assertIn("stall due: no", self.run_cli("budget", self.slug)[1])
        write_study(self.workspace, "reduce-to-a-finite-computation")
        move = make_move(
            4,
            strategy="reduce-to-a-finite-computation",
            entry="reduce-to-finite-witnesses",
            walk="attack-the-negative-side+reduce-to-a-finite-computation",
            step_cites=["shape.finite_certificates"],
        )
        status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(move))
        self.assertEqual((status, err), (0, ""))

    def test_a_failed_strategy_takes_no_further_move(self):
        write_journal(self.workspace, [make_move(1), make_move(2, failed=True)])
        self.run_cli("fail", self.slug, "attack-the-negative-side")
        write_study(self.workspace, "attack-the-negative-side")
        status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(make_move(3)))
        self.assertEqual((status, err), (1, "move: strategy attack-the-negative-side has verdict no\n"))

    def test_a_move_is_refused_until_the_ranking_covers_the_new_plan(self):
        """`fail` re-plans, so the ranking written over the old plan no longer holds."""
        write_ranking(self)
        self.run_cli("fail", self.slug, "ladder-the-parameter")
        write_study(self.workspace, "solve-the-model-world-first")
        move = make_move(1, strategy="solve-the-model-world-first", entry="isolate-a-model-problem")
        status, out, err = self.run_cli("journal", "add", self.slug, "--json", json.dumps(move))
        self.assertEqual(status, 1)
        self.assertTrue(err.startswith("ranking.json:"), err)

    def test_a_stall_still_falls_due_three_failures_after_the_last_fail(self):
        write_journal(self.workspace, [make_move(n, failed=True) for n in range(1, 4)])
        self.run_cli("fail", self.slug, "ladder-the-parameter")
        write_journal(self.workspace, [make_move(n, failed=True) for n in range(1, 7)])
        self.assertIn("stall due: yes", self.run_cli("budget", self.slug)[1])
