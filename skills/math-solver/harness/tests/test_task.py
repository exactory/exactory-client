"""`task` keeps the action list of an attack in tasks.json: what the solver means to
do next inside a stage, which the record alone does not say, so a session that
resumes the attack knows where the previous one was."""

from tests.support import WorkspaceTest, make_move, write_journal


class TaskTest(WorkspaceTest):
    def task(self, *argv):
        return self.run_cli("task", *argv)

    def test_add_appends_an_open_task_and_numbers_it(self):
        status, out, err = self.task("add", self.slug, "write check.sh for enumeration-run-1")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "task 1 added\n")
        self.assertEqual(self.task("add", self.slug, "run verify certificate")[1], "task 2 added\n")
        tasks = self.read_json("tasks.json")["tasks"]
        self.assertEqual([task["id"] for task in tasks], [1, 2])
        self.assertEqual(tasks[0]["text"], "write check.sh for enumeration-run-1")
        self.assertEqual(tasks[0]["status"], "open")
        self.assertEqual(tasks[0]["added_after_move"], 0)
        self.assertRegex(tasks[0]["added_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_add_records_the_move_count_at_the_time(self):
        write_journal(self.workspace, [make_move(1), make_move(2)])
        self.task("add", self.slug, "step into ladder-the-parameter")
        self.assertEqual(self.read_json("tasks.json")["tasks"][0]["added_after_move"], 2)

    def test_add_refuses_empty_text(self):
        status, out, err = self.task("add", self.slug, "  ")
        self.assertEqual((status, err), (1, "task: text is empty\n"))

    def test_done_closes_the_task_and_stamps_it(self):
        self.task("add", self.slug, "write check.sh")
        write_journal(self.workspace, [make_move(1)])
        status, out, err = self.task("done", self.slug, "1")
        self.assertEqual((status, out, err), (0, "task 1 done\n", ""))
        task = self.read_json("tasks.json")["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["done_after_move"], 1)
        self.assertRegex(task["done_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_done_refuses_an_unknown_id_and_a_task_already_done(self):
        self.task("add", self.slug, "write check.sh")
        self.assertEqual(self.task("done", self.slug, "5")[2], "tasks.json: no task 5\n")
        self.task("done", self.slug, "1")
        self.assertEqual(self.task("done", self.slug, "1")[2], "tasks.json: task 1 is already done\n")

    def test_list_prints_every_task_with_its_state(self):
        self.task("add", self.slug, "write check.sh")
        self.task("add", self.slug, "run verify certificate")
        self.task("done", self.slug, "1")
        status, out, err = self.task("list", self.slug)
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "[x] 1. write check.sh\n[ ] 2. run verify certificate\n")

    def test_list_says_so_when_there_is_no_task(self):
        self.assertEqual(self.task("list", self.slug)[1], "no task\n")
