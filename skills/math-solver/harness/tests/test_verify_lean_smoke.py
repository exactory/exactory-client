"""Integration test: `verify lean` on the real Lean project under fixtures/lean-smoke/."""

import os
import shutil
import unittest
from pathlib import Path

from tests.support import WorkspaceTest

ELAN_BIN = Path.home() / ".elan" / "bin"
SMOKE_PROJECT = Path(__file__).parent.parent / "fixtures" / "lean-smoke"
PATH_WITH_ELAN = str(ELAN_BIN) + os.pathsep + os.environ["PATH"]


@unittest.skipUnless(shutil.which("lake", path=PATH_WITH_ELAN), "lake is not installed")
class VerifyLeanSmokeTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.step_dir = self.workspace / "deterministic" / "formal-check-1"
        shutil.copytree(SMOKE_PROJECT, self.step_dir)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = PATH_WITH_ELAN
        self.addCleanup(os.environ.__setitem__, "PATH", self.old_path)

    def test_the_smoke_theorem_passes_with_no_axioms(self):
        status, out, err = self.run_cli("verify", "lean", self.slug, "formal-check-1")
        self.assertEqual((status, err), (0, ""), out)
        self.assertEqual(out, "pass: square_mod_four depends on no axioms\n")
        result = self.read_json("deterministic/formal-check-1/result.json")
        self.assertEqual((result["status"], result["axioms"], result["reason"]), ("pass", [], None))
        self.assertFalse((self.step_dir / "axioms-check.lean").exists())
