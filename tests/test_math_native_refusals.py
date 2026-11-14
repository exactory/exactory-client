"""Run native CLI regressions in their own tests package."""

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MathNativeRefusalTests(unittest.TestCase):
    def run_native_tests(self, *methods):
        result = subprocess.run(
            [sys.executable, "-m", "unittest", *[
                "tests.test_native_review_refusals.NativeReviewRefusalTests." + method for method in methods]],
            cwd=ROOT / "skills/math-solver/harness", capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_nonapprovals_and_malformed_records_at_each_command(self):
        self.run_native_tests("test_each_command_refuses_revise_and_reject_with_the_observed_objections",
                              "test_malformed_nonapproval_keeps_invalid_record_for_each_command")

    def test_prompt_nonapprovals_at_each_command(self):
        self.run_native_tests("test_each_command_refuses_the_three_field_nonapproval_of_the_native_prompts")

    def test_malformed_prompt_nonapprovals_at_each_command(self):
        self.run_native_tests("test_malformed_three_field_nonapprovals_keep_invalid_record")
