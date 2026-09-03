"""`finish` closes the workspace: every unit that stands is checked, drafted, and
evaluated, and the record says so in units/FINISHED.json."""

import json

from tests.support import WorkspaceTest, make_move, write_journal, write_study


class FinishTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        write_journal(self.workspace, [make_move(1)])
        (self.workspace / "units" / "INVENTORY.md").write_text("# Inventory: sample\n")
        self.unit_dir = self.workspace / "units" / "1"
        self.unit_dir.mkdir()
        (self.unit_dir / "proof.md").write_text("proof\n")
        self.write_json(
            "units/1/unit.json",
            {
                "statement": "For every n the bound holds.",
                "form": "quantitative-improvement",
                "evidence": "units/1/proof.md",
                "novelty": "2026-09-01 searched by statement; no hit",
                "moves": [1],
                "costs": [],
            },
        )
        self.assertEqual(self.run_cli("check-unit", self.slug, "1")[0], 0)
        (self.unit_dir / "draft.md").write_text("# Draft\n")
        (self.unit_dir / "evaluation.md").write_text("every claim points to its evidence\n")

    def finish(self):
        return self.run_cli("finish", self.slug)

    def read_finished(self):
        return self.read_json("units/FINISHED.json")

    def test_finishes_a_cashed_out_attack(self):
        self.assertEqual(self.finish(), (0, "finished sample: 1 unit stands\n", ""))
        self.assertEqual(self.read_finished(), {"outcome": "cashed-out", "units": [1]})

    def test_counts_every_unit_and_ignores_what_is_not_one(self):
        (self.workspace / "units" / "consolidation.md").write_text("consolidated\n")
        (self.workspace / "units" / "notes").mkdir()
        second = self.workspace / "units" / "2"
        second.mkdir()
        for name in ("unit.json", "check-unit.json", "draft.md", "evaluation.md"):
            (second / name).write_text((self.unit_dir / name).read_text())
        self.assertEqual(self.finish(), (0, "finished sample: 2 units stand\n", ""))
        self.assertEqual(self.read_finished()["units"], [1, 2])

    def test_finishes_with_no_unit_after_the_inventory(self):
        for path in self.unit_dir.iterdir():
            path.unlink()
        self.unit_dir.rmdir()
        self.assertEqual(self.finish(), (0, "finished sample: 0 units stand\n", ""))
        self.assertEqual(self.read_finished(), {"outcome": "cashed-out", "units": []})

    def test_refuses_before_the_inventory_exists(self):
        (self.workspace / "units" / "INVENTORY.md").unlink()
        status, out, err = self.finish()
        self.assertEqual((status, err), (1, "units/INVENTORY.md: missing; run stall before finish\n"))
        self.assertFalse((self.workspace / "units" / "FINISHED.json").exists())

    def test_refuses_a_unit_that_was_not_checked(self):
        (self.unit_dir / "check-unit.json").unlink()
        self.assertEqual(self.finish()[2], "units/1: not checked; run check-unit\n")

    def test_refuses_a_unit_changed_after_its_check(self):
        unit = self.read_json("units/1/unit.json")
        unit["statement"] = "For every n a weaker bound holds."
        self.write_json("units/1/unit.json", unit)
        self.assertEqual(self.finish()[2], "units/1: unit.json changed after check-unit; run it again\n")

    def test_refuses_a_unit_without_a_draft(self):
        (self.unit_dir / "draft.md").write_text(" \n")
        self.assertEqual(self.finish()[2], "units/1/draft.md: missing or empty\n")
        (self.unit_dir / "draft.md").unlink()
        self.assertEqual(self.finish()[2], "units/1/draft.md: missing or empty\n")

    def test_refuses_a_unit_without_an_evaluation(self):
        (self.unit_dir / "evaluation.md").unlink()
        self.assertEqual(self.finish()[2], "units/1/evaluation.md: missing or empty\n")

    def test_lists_every_problem_at_once(self):
        (self.unit_dir / "draft.md").unlink()
        (self.unit_dir / "evaluation.md").unlink()
        self.assertEqual(
            self.finish()[2],
            "units/1/draft.md: missing or empty\nunits/1/evaluation.md: missing or empty\n",
        )


class FinishAtStageThreeTest(WorkspaceTest):
    """The attack that ends at stage 3, because the statement is in the literature,
    finishes on the study and the novelty record alone."""

    def setUp(self):
        super().setUp()
        write_study(self.workspace, "problem")
        (self.workspace / "novelty.md").write_text("2026-09-01 arXiv: solved, see the record\n")

    def test_records_the_exit(self):
        status, out, err = self.run_cli("finish", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "finished sample: the statement is in the literature; novelty.md records where\n")
        self.assertEqual(self.read_json("units/FINISHED.json"), {"outcome": "solved-in-literature", "units": []})

    def test_refuses_without_the_novelty_record(self):
        (self.workspace / "novelty.md").write_text("")
        self.assertEqual(
            self.run_cli("finish", self.slug)[2],
            "novelty.md: empty; the stage 3 exit records where the statement is solved\n",
        )

    def test_refuses_without_the_problem_study(self):
        (self.workspace / "study" / "problem.md").unlink()
        self.assertEqual(
            self.run_cli("finish", self.slug)[2],
            "study/problem.md: missing or empty; write the problem-level study (STUDY.md) before finish\n",
        )

    def test_an_attack_with_moves_is_not_a_stage_three_exit(self):
        write_journal(self.workspace, [make_move(1)])
        self.assertEqual(
            self.run_cli("finish", self.slug)[2],
            "units/INVENTORY.md: missing; run stall before finish\n",
        )
