"""A strategy that opens a second record (its claim a hypothesis of the first) opens
it through reviewed controller admission. `status` shows
the link both ways, `finish` on the parent waits for the child, and a child opens
no child of its own."""

import json
import attack

from tests.support import AdmittedWorkspaceTest, make_move, write_journal, prepare_plan, admit_native_child, write_study


class ChildAttackTest(AdmittedWorkspaceTest):
    def setUp(self):
        super().setUp()
        prepare_plan(self)

    def init_child(self, slug="hypothesis", parent="sample"):
        return admit_native_child(self.controller, slug, parent)

    def child_json(self, slug="hypothesis"):
        return json.loads((self.attack_root / slug / "parent.json").read_text())

    def finish_child(self, slug="hypothesis"):
        workspace = self.attack_root / slug
        write_study(workspace, "problem")
        (workspace / "units/INVENTORY.md").write_text("Inventory: no retained units.\n")
        self.assertEqual(self.run_cli("finish", slug)[0], 0)

    def status_lines(self, slug):
        status, out, err = self.run_cli("status", slug)
        self.assertEqual((status, err), (0, ""))
        return out.splitlines()

    def test_opens_the_child_and_records_the_parent_and_the_move_count(self):
        write_journal(self.workspace, [make_move(1), make_move(2)])
        self.assertEqual(self.init_child(), (self.attack_root / "hypothesis").resolve())
        self.assertEqual(self.child_json(), {"parent": "sample", "opened_after_move": 2})
        self.assertTrue((self.attack_root / "hypothesis" / "problem.json").exists())

    def test_refuses_a_parent_with_no_workspace(self):
        self.assertEqual(list(attack.find_parent_defects(self.attack_root, "nope")),
                         ["no workspace for parent nope; a child opens under an open attack"])
        self.assertFalse((self.attack_root / "hypothesis").exists())

    def test_refuses_a_finished_parent(self):
        self.finish_child("sample")
        self.assertEqual(
            list(attack.find_parent_defects(self.attack_root, "sample")),
            ["parent sample is finished; a child opens under an open attack"],
        )

    def test_a_child_opens_no_child(self):
        self.init_child()
        self.assertEqual(
            list(attack.find_parent_defects(self.attack_root, "hypothesis")),
            ["parent hypothesis is itself a child of sample; a child opens no child"],
        )

    def test_status_shows_the_link_both_ways(self):
        write_journal(self.workspace, [make_move(1)])
        self.init_child()
        self.assertIn("parent: attack/sample (opened after move 1)", self.status_lines("hypothesis"))
        self.assertIn("children: attack/hypothesis (open)", self.status_lines("sample"))
        self.finish_child()
        self.assertIn("children: attack/hypothesis (finished: cashed-out)", self.status_lines("sample"))

    def test_status_omits_the_link_lines_when_there_is_none(self):
        lines = self.status_lines("sample")
        self.assertFalse(any(line.startswith(("parent:", "children:")) for line in lines))

    def test_the_parent_finishes_after_its_children(self):
        (self.workspace / "units" / "INVENTORY.md").write_text("# Inventory: sample\n")
        write_journal(self.workspace, [make_move(1, closes=True)])
        self.init_child()
        self.init_child(slug="second")
        status, out, err = self.run_cli("finish", "sample")
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(
            err,
            "attack/hypothesis: not finished; a parent finishes after its children\n"
            "attack/second: not finished; a parent finishes after its children\n",
        )
        self.finish_child()
        self.finish_child("second")
        self.assertEqual(self.run_cli("finish", "sample")[0], 0)
