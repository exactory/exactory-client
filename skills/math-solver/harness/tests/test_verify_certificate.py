import os
from pathlib import Path

from tests.support import StepTest, run


class VerifyCertificateTest(StepTest):
    step_name = "enumeration-1"

    def write_check_script(self, body):
        self.write_step_file("check.sh", "#!/bin/sh\n" + body)
        os.chmod(self.step_dir / "check.sh", 0o755)

    def verify(self):
        return self.run_cli("verify", "certificate", self.slug, self.step_name)

    def test_passes_when_the_checker_exits_zero(self):
        self.write_check_script("echo verified 12 certificates\necho all agree\n")
        status, out, err = self.verify()
        self.assertEqual((status, out, err), (0, "pass: check.sh exited 0\n", ""))
        self.assertEqual(
            self.read_result(),
            {"status": "pass", "exit_status": 0, "output_head": ["verified 12 certificates", "all agree"]},
        )

    def test_fails_when_the_checker_exits_nonzero(self):
        self.write_check_script("echo certificate 7 rejected >&2\nexit 2\n")
        status, out, err = self.verify()
        self.assertEqual((status, out, err), (1, "fail: check.sh exited 2\n", ""))
        self.assertEqual(
            self.read_result(),
            {"status": "fail", "exit_status": 2, "output_head": ["certificate 7 rejected"]},
        )

    def test_runs_the_checker_in_the_step_directory(self):
        self.write_check_script("pwd\n")
        self.verify()
        self.assertEqual(self.read_result()["output_head"], [str(self.step_dir.resolve())])

    def test_keeps_only_the_first_twenty_lines(self):
        self.write_check_script("i=0\nwhile [ $i -lt 30 ]; do echo line $i; i=$((i+1)); done\n")
        self.verify()
        head = self.read_result()["output_head"]
        self.assertEqual(len(head), 20)
        self.assertEqual(head[-1], "line 19")

    def test_rejects_a_missing_checker(self):
        self.assertEqual(self.verify()[2], "check.sh: missing\n")

    def test_rejects_a_checker_that_is_not_executable(self):
        self.write_step_file("check.sh", "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.verify()[2], "check.sh: not executable\n")


class RelativeAttackRootTest(StepTest):
    """The skill passes --attack-root as a path relative to the work directory."""

    step_name = "enumeration-2"

    def test_runs_the_checker_from_a_relative_attack_root(self):
        self.write_step_file("check.sh", "#!/bin/sh\necho ok\n")
        os.chmod(self.step_dir / "check.sh", 0o755)
        here = os.getcwd()
        os.chdir(self.attack_root.parent)
        self.addCleanup(os.chdir, here)
        status, out, err = run(
            ["verify", "certificate", self.slug, self.step_name], Path(self.attack_root.name)
        )
        self.assertEqual((status, out, err), (0, "pass: check.sh exited 0\n", ""))
