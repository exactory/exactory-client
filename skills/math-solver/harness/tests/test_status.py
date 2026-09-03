"""`status` derives where an attack stands from the record alone: the stage, what
exists, the walk, the budget, the units, the open tasks, the last activity, and
the next step. It is what a resumed session reads first."""

import json

from tests.support import (
    ALL_YES,
    OPENING,
    WorkspaceTest,
    make_move,
    make_preconditions,
    make_problem,
    prepare_plan,
    write_journal,
    write_ranking,
    write_study,
)


class StatusTest(WorkspaceTest):
    def status(self):
        return self.run_cli("status", self.slug)

    def lines(self):
        status, out, err = self.status()
        self.assertEqual((status, err), (0, ""))
        return out.splitlines()

    def line(self, head):
        return next(line for line in self.lines() if line.startswith(head))

    def add(self, move):
        return self.run_cli("journal", "add", self.slug, "--json", json.dumps(move))

    # The stages, from the empty workspace to the finished one

    def test_a_fresh_workspace_is_at_stage_2(self):
        lines = self.lines()
        self.assertEqual(lines[0], "attack/sample: stage 2 (set the problem)")
        self.assertEqual(self.line("problem:"), "problem: not set (claim: empty string)")
        self.assertEqual(self.line("next:"), "next: fill problem.json and run check-problem")

    def test_a_set_problem_is_at_stage_3(self):
        self.write_json("problem.json", make_problem())
        self.assertEqual(self.lines()[0], "attack/sample: stage 3 (study and novelty check)")
        self.assertEqual(self.line("problem:"), "problem: ok")
        self.assertEqual(self.line("study:"), "study: problem.md missing; novelty.md empty")
        self.assertEqual(self.line("next:"), "next: write study/problem.md and novelty.md, then preconditions.json, and run plan")

    def test_a_studied_problem_is_at_stage_4(self):
        self.write_json("problem.json", make_problem())
        write_study(self.workspace, "problem")
        (self.workspace / "novelty.md").write_text("searched\n")
        self.assertEqual(self.lines()[0], "attack/sample: stage 4 (precondition scan)")
        self.assertEqual(self.line("study:"), "study: problem.md present; novelty.md present")
        self.assertEqual(self.line("plan:"), "plan: not run")
        self.assertEqual(self.line("next:"), "next: write preconditions.json and run plan")

    def test_a_planned_attack_waits_for_the_ranking(self):
        self.write_json("problem.json", make_problem())
        write_study(self.workspace, "problem")
        self.write_json("preconditions.json", make_preconditions(ALL_YES))
        self.run_cli("plan", self.slug)
        self.assertEqual(self.lines()[0], "attack/sample: stage 4 (precondition scan)")
        self.assertEqual(self.line("plan:"), "plan: 5 openings admitted")
        self.assertEqual(self.line("ranking:"), "ranking: missing")
        self.assertEqual(self.line("next:"), "next: write ranking.json over the openings and run rank")

    def test_a_ranked_attack_opens_at_the_first_strategy(self):
        prepare_plan(self)
        self.assertEqual(self.lines()[0], "attack/sample: stage 5 (attack)")
        self.assertEqual(self.line("ranking:"), "ranking: ok")
        self.assertEqual(self.line("walk:"), "walk: none yet")
        self.assertEqual(
            self.line("next:"),
            "next: open the attack with %s: write study/%s.md, then journal add its first move" % (OPENING, OPENING),
        )

    def test_a_stale_ranking_is_reported(self):
        prepare_plan(self)
        self.write_json("ranking.json", {"generated_from": "openings.json", "order": []})
        self.assertTrue(self.line("ranking:").startswith("ranking: stale (ranking.json:"))
        self.assertEqual(self.line("next:"), "next: write ranking.json over the openings and run rank")

    def test_an_open_walk_names_the_current_strategy(self):
        write_study(self.workspace, OPENING)
        prepare_plan(self)
        self.add(make_move(1))
        self.add(make_move(2, failed=True))
        self.assertEqual(self.lines()[0], "attack/sample: stage 5 (attack)")
        self.assertEqual(self.line("walk:"), "walk: %s (2 moves, last move 2 in pass 1)" % OPENING)
        self.assertEqual(self.line("budget:"), "budget: moves this pass 2/8, overall 2/24, passes 1/3, stall due: no")
        self.assertEqual(
            self.line("next:"),
            "next: continue %s with its next move, step into another admitted strategy, or fail %s if its failure signal fired"
            % (OPENING, OPENING),
        )

    def test_a_stall_due_points_at_stall(self):
        write_study(self.workspace, OPENING)
        prepare_plan(self)
        for number in (1, 2, 3):
            self.add(make_move(number, failed=True))
        self.assertEqual(self.lines()[0], "attack/sample: stage 7 (cash out)")
        self.assertEqual(self.line("budget:"), "budget: moves this pass 3/8, overall 3/24, passes 1/3, stall due: yes (3 consecutive failure signals)")
        self.assertEqual(self.line("next:"), "next: run stall; the rule is 3 consecutive failure signals")

    def test_an_empty_plan_points_at_stall(self):
        write_study(self.workspace, OPENING)
        prepare_plan(self)
        self.add(make_move(1))
        prepare_plan(self, {name: "no" for name in ALL_YES})
        self.assertEqual(self.line("next:"), "next: run stall; the rule is no admissible opening")

    def test_units_are_counted_by_what_they_still_need(self):
        write_journal(self.workspace, [make_move(1, closes=True)])
        (self.workspace / "units" / "INVENTORY.md").write_text("# Inventory: sample\n")
        for number in (1, 2, 3):
            unit_dir = self.workspace / "units" / str(number)
            unit_dir.mkdir()
            (unit_dir / "proof.md").write_text("proof\n")
            self.write_json("units/%d/unit.json" % number, {
                "statement": "For every n the bound holds.",
                "form": "quantitative-improvement",
                "evidence": "units/%d/proof.md" % number,
                "novelty": "searched",
                "moves": [1],
                "costs": [],
            })
        self.run_cli("check-unit", self.slug, "2")
        self.run_cli("check-unit", self.slug, "3")
        (self.workspace / "units" / "3" / "draft.md").write_text("# Draft\n")
        self.assertEqual(self.lines()[0], "attack/sample: stage 8 (write)")
        self.assertEqual(self.line("cash-out:"), "cash-out: inventory written; 3 units (1 unchecked, 1 undrafted, 1 unevaluated); finished: no")
        self.assertEqual(self.line("next:"), "next: check-unit 1; write units/2/draft.md; write units/3/evaluation.md; then finish")

    def test_an_inventory_with_no_unit_asks_for_the_conversion(self):
        write_journal(self.workspace, [make_move(1, closes=True)])
        (self.workspace / "units" / "INVENTORY.md").write_text("# Inventory: sample\n")
        self.assertEqual(self.lines()[0], "attack/sample: stage 7 (cash out)")
        self.assertEqual(self.line("next:"), "next: convert the inventory into units under CASHOUT.md, or run finish with none")

    def test_a_finished_attack_says_so(self):
        self.write_json("units/FINISHED.json", {"outcome": "cashed-out", "units": [1]})
        self.assertEqual(self.lines()[0], "attack/sample: finished (cashed-out)")
        self.assertEqual(self.line("next:"), "next: nothing; the attack is finished")

    # Tasks and activity

    def test_open_tasks_are_listed_under_the_status(self):
        self.run_cli("task", "add", self.slug, "write check.sh")
        self.run_cli("task", "add", self.slug, "run verify")
        self.run_cli("task", "done", self.slug, "1")
        lines = self.lines()
        self.assertIn("tasks: 1 open, 1 done", lines)
        self.assertIn("  [ ] 2. run verify", lines)
        self.assertNotIn("  [x] 1. write check.sh", lines)

    def test_no_task_is_reported(self):
        self.assertIn("tasks: none", self.lines())

    def test_the_last_activity_is_shown(self):
        entries = [
            {"at": "2026-09-02T21:00:0%dZ" % n, "tool": "Write", "target": "deterministic/run-1/check-%d.sh" % n}
            for n in range(5)
        ]
        (self.workspace / "activity.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries))
        lines = self.lines()
        self.assertIn("activity: last 3 of 5", lines)
        self.assertIn("  2026-09-02T21:00:04Z Write deterministic/run-1/check-4.sh", lines)
        self.assertNotIn("  2026-09-02T21:00:00Z Write deterministic/run-1/check-0.sh", lines)

    def test_no_activity_is_reported(self):
        self.assertIn("activity: none recorded", self.lines())
