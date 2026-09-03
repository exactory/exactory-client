import unittest

from attack import compute_budget
from tests.support import WorkspaceTest, make_move, write_journal


class ComputeBudgetTest(unittest.TestCase):
    def test_empty_journal(self):
        self.assertEqual(
            compute_budget([]),
            {"pass": 0, "moves_in_pass": 0, "moves_total": 0, "stall_reason": None},
        )

    def test_counts_the_current_pass_and_the_total(self):
        moves = [make_move(1), make_move(2), make_move(3), make_move(4, 2), make_move(5, 2)]
        self.assertEqual(
            compute_budget(moves),
            {"pass": 2, "moves_in_pass": 2, "moves_total": 5, "stall_reason": None},
        )

    def test_three_consecutive_failure_signals_stall(self):
        moves = [make_move(1), make_move(2, failed=True), make_move(3, failed=True), make_move(4, failed=True)]
        self.assertEqual(compute_budget(moves)["stall_reason"], "3 consecutive failure signals")

    def test_a_success_between_failures_does_not_stall(self):
        moves = [make_move(1, failed=True), make_move(2, failed=True), make_move(3), make_move(4, failed=True)]
        self.assertIsNone(compute_budget(moves)["stall_reason"])

    def test_a_spent_first_pass_does_not_stall(self):
        self.assertIsNone(compute_budget([make_move(n) for n in range(1, 9)])["stall_reason"])

    def test_a_spent_last_pass_stalls(self):
        moves = [make_move(1), make_move(2, 2)] + [make_move(n, 3) for n in range(3, 11)]
        self.assertEqual(compute_budget(moves)["stall_reason"], "last pass spent")

    def test_the_hard_cap_stalls(self):
        moves = [make_move(n) for n in range(1, 25)]
        self.assertEqual(compute_budget(moves)["stall_reason"], "hard cap of 24 moves reached")


class BudgetCommandTest(WorkspaceTest):
    def test_prints_the_state_of_an_empty_journal(self):
        status, out, err = self.run_cli("budget", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(
            out, "moves this pass: 0/8\nmoves overall: 0/24\npasses used: 0/3\nstall due: no\n"
        )

    def test_prints_the_stall_reason(self):
        write_journal(self.workspace, [make_move(n, failed=True) for n in range(1, 4)])
        status, out, err = self.run_cli("budget", self.slug)
        self.assertEqual(
            out,
            "moves this pass: 3/8\nmoves overall: 3/24\npasses used: 1/3\n"
            "stall due: yes (3 consecutive failure signals)\n",
        )

    def test_rejects_a_slug_with_no_workspace(self):
        status, out, err = self.run_cli("budget", "no-such-slug")
        self.assertEqual((status, err), (1, "journal.jsonl: missing\n"))
