"""Run native CLI regressions in their own tests package."""

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MathNativeRefusalTests(unittest.TestCase):
    def test_native_nonapprovals_and_malformed_records_at_each_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_native_review_refusals"],
            cwd=ROOT / "skills/math-solver/harness", capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
