"""Task maintenance must preserve pending native snapshots and research budgets."""

import subprocess
import sys
from unittest import mock

import attack
from tests.support import WorkspaceTest, admit_native_child
from tests.search_execution_support import admit_workspace, begin_spec, invoke


class TaskRecoveryTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)

    def assert_tasks_protected(self, existing):
        before = self.controller.status()
        path = self.workspace / "tasks.json"
        raw = path.read_bytes() if path.exists() else None
        commands = [("add", "Preserve the next question")]
        if existing:
            commands.append(("done", "1"))
        for command, value in commands:
            status, out, err = self.run_cli("task", command, self.slug, value)
            self.assertEqual((status, out), (1, ""))
            self.assertIn("recovery_required", err)
            self.assertEqual(path.read_bytes() if path.exists() else None, raw)
        self.assertEqual(self.run_cli("task", "list", self.slug)[0], 0)
        self.assertEqual(self.run_cli("status", self.slug)[0], 0)
        self.assertEqual(self.controller.status(), before)

    def test_live_native_owner_blocks_task_creation(self):
        original = attack.run_rank
        def interleaved(args):
            self.assert_tasks_protected(existing=False)
            return original(args)
        with mock.patch.object(attack, "run_rank", side_effect=interleaved):
            self.assertEqual(self.run_cli("rank", self.slug)[0], 0)

    def test_live_native_owner_blocks_task_add_and_done(self):
        self.assertEqual(self.run_cli("task", "add", self.slug, "Existing question")[0], 0)
        original = attack.run_rank
        def interleaved(args):
            self.assert_tasks_protected(existing=True)
            return original(args)
        with mock.patch.object(attack, "run_rank", side_effect=interleaved):
            self.assertEqual(self.run_cli("rank", self.slug)[0], 0)

    def crash_rank(self):
        with mock.patch.object(attack, "run_rank", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli("rank", self.slug)

    def test_released_pending_intent_blocks_task_creation(self):
        self.crash_rank()
        self.assert_tasks_protected(existing=False)

    def test_released_pending_intent_blocks_writes_then_recovers_once(self):
        self.assertEqual(self.run_cli("task", "add", self.slug, "Existing question")[0], 0)
        self.crash_rank()
        self.assert_tasks_protected(existing=True)
        before = self.controller.status()
        identity = next(iter(before["service"]["native_intents"]))
        invoke(self.controller, "reconcile", {}, None)
        recovered = self.controller.status()
        self.assertEqual(recovered["service"]["native_intents"], {})
        self.assertEqual(recovered["service"]["native_receipts"][identity]["outcome"], "unchanged")
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["service"]["native_receipts"],
                         recovered["service"]["native_receipts"])
        self.assertEqual(self.run_cli("rank", self.slug)[0], 0)
        self.assertEqual(self.run_cli("task", "done", self.slug, "1")[0], 0)
        self.assertEqual(self.run_cli("task", "add", self.slug, "Next question")[0], 0)
        state = self.controller.status()
        self.assertEqual(state["totals"], before["totals"])
        self.assertEqual(state["accounts"], before["accounts"])
        self.assertEqual(state["nodes"]["node-000001"]["claim"], before["nodes"]["node-000001"]["claim"])

    def test_task_maintenance_during_a_reserved_move_preserves_controller_state(self):
        invoke(self.controller, "begin", begin_spec())
        before = self.controller.status()
        self.assertEqual(self.run_cli("task", "add", self.slug, "Document the current step")[0], 0)
        self.assertEqual(self.run_cli("task", "done", self.slug, "1")[0], 0)
        self.assertEqual(self.controller.status(), before)

    def test_pending_filesystem_initialization_blocks_writes_until_recovered(self):
        from search_controller import service
        self.assertEqual(self.run_cli("task", "add", self.slug, "Existing question")[0], 0)
        original = service.replace_text
        def interrupted(path, value):
            if path.name == "journal.jsonl":
                raise OSError("Interrupted child initialization")
            return original(path, value)
        with mock.patch.object(service, "replace_text", side_effect=interrupted):
            with self.assertRaises(OSError):
                admit_native_child(self.controller)
        self.assertTrue(self.controller.status()["service"]["pending_effect_ids"])
        self.assert_tasks_protected(existing=True)
        invoke(self.controller, "render", {}, None)
        self.assertEqual(self.controller.status()["service"]["pending_effect_ids"], [])
        self.assertEqual(self.run_cli("task", "done", self.slug, "1")[0], 0)
        self.assertEqual(self.run_cli("task", "add", self.slug, "Next question")[0], 0)

    def test_task_maintenance_after_native_finish_preserves_controller_state(self):
        self.assertEqual(self.run_cli("finish", self.slug)[0], 0)
        before = self.controller.status()
        self.assertEqual(self.run_cli("task", "add", self.slug, "Record the handoff")[0], 0)
        self.assertEqual(self.run_cli("task", "done", self.slug, "1")[0], 0)
        self.assertEqual(self.controller.status(), before)

    def test_task_read_modify_write_holds_the_native_intent_transaction_lock(self):
        read_tasks, write_tasks = attack.read_tasks, attack.write_tasks
        def inspect_lock():
            script = (
                "import fcntl, sys\n"
                "with open(sys.argv[1], 'rb') as lock:\n"
                "    try:\n"
                "        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                "    except BlockingIOError:\n"
                "        sys.exit(0)\n"
                "    sys.exit(1)\n"
            )
            result = subprocess.run([sys.executable, "-c", script, str(self.controller.store.lock_path)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, "Task write did not exclude native intent creation")
        def read(workspace):
            inspect_lock()
            return read_tasks(workspace)
        def write(workspace, tasks):
            inspect_lock()
            return write_tasks(workspace, tasks)
        with mock.patch.object(attack, "read_tasks", side_effect=read), \
                mock.patch.object(attack, "write_tasks", side_effect=write):
            self.assertEqual(self.run_cli("task", "add", self.slug, "Protected question")[0], 0)
            self.assertEqual(self.run_cli("task", "done", self.slug, "1")[0], 0)
