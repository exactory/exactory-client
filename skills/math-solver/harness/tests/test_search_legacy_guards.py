"""Legacy commands must cross the controller's admission boundary."""

from tests.support import WorkspaceTest


class UnmanagedLegacyGuardTests(WorkspaceTest):
    def test_unadmitted_verify_never_launches_checker(self):
        step = self.workspace / "deterministic" / "check-1"
        step.mkdir()
        marker = self.attack_root / "checker-ran"
        checker = step / "check.sh"
        checker.write_text("#!/bin/sh\nprintf executed > '" + str(marker) + "'\n")
        checker.chmod(0o755)
        status, out, err = self.run_cli("verify", "certificate", self.slug, "check-1")
        self.assertNotEqual(status, 0)
        self.assertIn("admission_required", err)
        self.assertFalse(marker.exists())

    def test_unadmitted_plan_refuses_before_native_validation(self):
        status, out, err = self.run_cli("plan", self.slug)
        self.assertNotEqual(status, 0)
        self.assertIn("admission_required", err)
        self.assertFalse((self.workspace / "openings.json").exists())

    def test_unadmitted_child_init_never_writes_child(self):
        status, out, err = self.run_cli("init", "child", "--from", self.slug)
        self.assertNotEqual(status, 0)
        self.assertIn("admission_required", err)
        self.assertFalse((self.attack_root / "child").exists())
        self.assertIn("native_parent", err)

    def test_unadmitted_check_unit_preserves_existing_stamp(self):
        unit = self.workspace / "units" / "1"
        unit.mkdir()
        stamp = unit / "check-unit.json"
        stamp.write_text("original stamp")
        status, out, err = self.run_cli("check-unit", self.slug, "1")
        self.assertNotEqual(status, 0)
        self.assertIn("admission_required", err)
        self.assertEqual(stamp.read_text(), "original stamp")
