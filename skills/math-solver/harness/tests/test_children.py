"""A strategy that opens a second record (its claim a hypothesis of the first) opens
it as a child attack: `init <child> --from <parent>` links the two, `status` shows
the link both ways, `finish` on the parent waits for the child, and a child opens
no child of its own."""

import json

from tests.support import WorkspaceTest, make_move, write_journal


class ChildAttackTest(WorkspaceTest):
    def init_child(self, slug="hypothesis", parent="sample"):
        return self.run_cli("init", slug, "--from", parent)

    def child_json(self, slug="hypothesis"):
        return json.loads((self.attack_root / slug / "parent.json").read_text())

    def finish_child(self, slug="hypothesis"):
        (self.attack_root / slug / "units" / "FINISHED.json").write_text(
            json.dumps({"outcome": "cashed-out", "units": []})
        )

    def status_lines(self, slug):
        status, out, err = self.run_cli("status", slug)
        self.assertEqual((status, err), (0, ""))
        return out.splitlines()

    def test_opens_the_child_and_records_the_parent_and_the_move_count(self):
        write_journal(self.workspace, [make_move(1), make_move(2)])
        status, out, err = self.init_child()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "created %s (child of sample, opened after move 2)\n" % (self.attack_root / "hypothesis"))
        self.assertEqual(self.child_json(), {"parent": "sample", "opened_after_move": 2})
        self.assertTrue((self.attack_root / "hypothesis" / "problem.json").exists())

    def test_refuses_a_parent_with_no_workspace(self):
        status, out, err = self.init_child(parent="nope")
        self.assertEqual((status, err), (1, "no workspace for parent nope; a child opens under an open attack\n"))
        self.assertFalse((self.attack_root / "hypothesis").exists())

    def test_refuses_a_finished_parent(self):
        self.finish_child("sample")
        self.assertEqual(
            self.init_child()[2],
            "parent sample is finished; a child opens under an open attack\n",
        )

    def test_a_child_opens_no_child(self):
        self.init_child()
        self.assertEqual(
            self.init_child(slug="deeper", parent="hypothesis")[2],
            "parent hypothesis is itself a child of sample; a child opens no child\n",
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
