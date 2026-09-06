"""Regressions for reviewed snapshot execution and native invocation ownership."""

from pathlib import Path
import sys
from unittest import mock

from search_controller import execution
from search_controller.errors import SearchError
from tests.support import WorkspaceTest
from tests.search_execution_support import admit_workspace, begin_spec, command_spec, invoke, review_native_inputs


class SnapshotInvocationTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root, computational=True)
        self.step = self.workspace / "deterministic/job"
        self.step.mkdir()

    def run_with_live_edit(self, script, argv, environment=None):
        invoke(self.controller, "begin", begin_spec())
        spec = command_spec(self.workspace, argv)
        if environment is not None:
            spec["environment"] = environment
        original = execution.materialize
        def materialize(controller, run):
            directory = original(controller, run)
            script.write_text("#!/bin/sh\nprintf 'unreviewed live bytes'\n" if script.suffix == ".sh" else "print('unreviewed live bytes')\n")
            return directory
        with mock.patch.object(execution, "materialize", side_effect=materialize):
            invoke(self.controller, "run", spec)
        run = self.controller.status()["runs"]["run-000001"]
        result = self.controller.store.get_blob(run["result_digest"])
        self.assertEqual(run["termination"], "exit")
        self.assertEqual(run["charged_units"], 1)
        self.assertEqual(self.controller.store.get_artifact(result["commands"][0]["stdout_digest"]).strip(), b"reviewed snapshot bytes")
        return run

    def test_absolute_script_argument_uses_frozen_bytes(self):
        script = self.step / "job.py"
        script.write_text("print('reviewed snapshot bytes')\n")
        run = self.run_with_live_edit(script, [sys.executable, str(script)])
        self.assertEqual(run["commands"][0][1], str(Path(run["cwd"]) / "job.py"))

    def test_absolute_local_executable_uses_frozen_bytes_and_permissions(self):
        script = self.step / "job.sh"
        script.write_text("#!/bin/sh\nprintf 'reviewed snapshot bytes'\n")
        script.chmod(0o750)
        run = self.run_with_live_edit(script, [str(script)])
        self.assertEqual(run["commands"][0][0], str(Path(run["cwd"]) / "job.sh"))
        self.assertEqual((Path(run["cwd"]) / "job.sh").stat().st_mode & 0o777, 0o750)

    def test_relative_local_executable_resolves_from_the_step_not_cli_cwd(self):
        script = self.step / "job.sh"
        script.write_text("#!/bin/sh\nprintf 'reviewed snapshot bytes'\n")
        script.chmod(0o750)
        self.run_with_live_edit(script, ["./job.sh"])

    def test_path_selected_local_executable_uses_the_same_frozen_boundary(self):
        script = self.step / "job.sh"
        script.write_text("#!/bin/sh\nprintf 'reviewed snapshot bytes'\n")
        script.chmod(0o750)
        self.run_with_live_edit(script, ["job.sh"], {"PATH": str(self.step)})

    def test_undeclared_absolute_local_argument_refuses_before_reservation(self):
        (self.step / "declared.txt").write_text("complete declared input")
        script = self.workspace / "undeclared.py"
        script.write_text("print('undeclared')\n")
        invoke(self.controller, "begin", begin_spec())
        with self.assertRaises(SearchError):
            invoke(self.controller, "run", command_spec(self.workspace, [sys.executable, str(script)]))
        self.assertEqual(self.controller.status()["runs"], {})


class NativeModeTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)
        self.step = self.workspace / "deterministic/check"
        self.step.mkdir()
        (self.step / "check.sh").write_text("#!/bin/sh\nexec ./helper.sh\n")
        (self.step / "check.sh").chmod(0o755)
        (self.step / "helper.sh").write_text("#!/bin/sh\nprintf 'reviewed helper'\n")
        (self.step / "helper.sh").chmod(0o750)
        (self.step / "data.txt").write_text("nonexecutable input")
        (self.step / "data.txt").chmod(0o640)
        invoke(self.controller, "begin", begin_spec())
        review_native_inputs(self.controller, self.slug, "check")

    def test_real_local_helper_retains_only_its_reviewed_permissions(self):
        self.assertEqual(self.run_cli("verify", "certificate", self.slug, "check")[0], 0)
        run = self.controller.status()["runs"]["run-000001"]
        result = self.controller.store.get_blob(run["result_digest"])
        self.assertEqual(self.controller.store.get_artifact(result["commands"][0]["stdout_digest"]), b"reviewed helper")
        self.assertEqual(run["charged_units"], 1)
        modes = {item["path"]: item["mode"] for item in run["input_modes"]}
        for name, mode in [("helper.sh", 0o750), ("data.txt", 0o640)]:
            self.assertEqual(modes[self.slug + "/deterministic/check/" + name], mode)
            self.assertEqual((Path(run["cwd"]) / name).stat().st_mode & 0o777, mode)
            self.assertEqual((Path(run["snapshot_root"]) / self.slug / "deterministic/check" / name).stat().st_mode & 0o777, mode)
            self.assertEqual((self.step / name).stat().st_mode & 0o777, mode)

    def test_changed_permissions_cannot_reuse_native_input_review(self):
        (self.step / "helper.sh").chmod(0o700)
        self.assertNotEqual(self.run_cli("verify", "certificate", self.slug, "check")[0], 0)
        self.assertEqual(self.controller.status()["runs"], {})

    def test_snapshot_permission_change_refuses_before_start(self):
        original = execution.materialize
        def materialize(controller, run):
            directory = original(controller, run)
            (Path(run["cwd"]) / "helper.sh").chmod(0o700)
            return directory
        with mock.patch.object(execution, "materialize", side_effect=materialize):
            self.assertNotEqual(self.run_cli("verify", "certificate", self.slug, "check")[0], 0)
        run = self.controller.status()["runs"]["run-000001"]
        self.assertEqual(run["termination"], "input_changed")
        self.assertEqual(run["started_units"], 0)


class NativeOwnershipTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.controller = admit_workspace(self.attack_root)

    def test_reconcile_between_intent_and_write_keeps_live_invocation_owned(self):
        import attack
        original = attack.run_finish
        def interleaved(args):
            before = self.controller.status()["service"]["native_intents"]
            self.assertEqual(len(before), 1)
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "reconcile", {}, None)
            self.assertEqual(caught.exception.code, "recovery_required")
            self.assertEqual(self.controller.status()["service"]["native_intents"], before)
            self.assertIn("recovery_required", self.run_cli("plan", self.slug)[2])
            return original(args)
        with mock.patch.object(attack, "run_finish", side_effect=interleaved):
            self.assertEqual(self.run_cli("finish", self.slug)[0], 0)
        state = self.controller.status()
        self.assertEqual(state["service"]["native_intents"], {})
        self.assertEqual(state["nodes"]["node-000001"]["status"], "finished")

    def test_durable_receipt_does_not_release_ownership_before_acknowledgement(self):
        from search_controller import integration
        original = integration.record_native_success
        def interleaved(controller, identity, *args):
            original(controller, identity, *args)
            with self.assertRaises(SearchError) as caught:
                invoke(self.controller, "reconcile", {}, None)
            self.assertEqual(caught.exception.code, "recovery_required")
            self.assertIn(identity, self.controller.status()["service"]["native_intents"])
        with mock.patch.object(integration, "record_native_success", side_effect=interleaved):
            self.assertEqual(self.run_cli("finish", self.slug)[0], 0)
        self.assertEqual(self.controller.status()["service"]["native_intents"], {})

    def test_interrupted_invocation_releases_ownership_but_missing_proof_blocks(self):
        import attack
        with mock.patch.object(attack, "run_finish", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli("finish", self.slug)
        identity = next(iter(self.controller.status()["service"]["native_intents"]))
        lock = self.controller.store.root / "native" / (identity + ".lock")
        self.assertTrue(lock.exists())
        held = lock.with_suffix(".preserved")
        lock.rename(held)
        with self.assertRaises(SearchError):
            invoke(self.controller, "reconcile", {}, None)
        self.assertIn(identity, self.controller.status()["service"]["native_intents"])
        lock.write_bytes(b"replacement ownership file")
        with self.assertRaises(SearchError):
            invoke(self.controller, "reconcile", {}, None)
        lock.rename(lock.with_suffix(".replacement"))
        held.rename(lock)
        invoke(self.controller, "reconcile", {}, None)
        state = self.controller.status()
        self.assertEqual(state["service"]["native_intents"], {})
        self.assertEqual(state["service"]["native_receipts"][identity]["outcome"], "unchanged")
        invoke(self.controller, "reconcile", {}, None)
        self.assertEqual(self.controller.status()["service"]["native_receipts"], state["service"]["native_receipts"])
