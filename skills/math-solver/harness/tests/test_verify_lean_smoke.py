"""Integration test: `verify lean` on the real Lean project under fixtures/lean-smoke/."""

import os
import json
import shutil
import unittest
from pathlib import Path

from tests.support import WorkspaceTest
from tests.search_execution_support import admit_workspace, begin_spec, invoke, review_native_inputs

ELAN_BIN = Path.home() / ".elan" / "bin"
SMOKE_PROJECT = Path(__file__).parent.parent / "fixtures" / "lean-smoke"
PATH_WITH_ELAN = str(ELAN_BIN) + os.pathsep + os.environ["PATH"]


@unittest.skipUnless(shutil.which("lake", path=PATH_WITH_ELAN), "lake is not installed")
class VerifyLeanSmokeTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.step_dir = self.workspace / "deterministic" / "formal-check-1"
        shutil.copytree(SMOKE_PROJECT, self.step_dir,
                        ignore=shutil.ignore_patterns(".lake"))
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = PATH_WITH_ELAN
        self.addCleanup(os.environ.__setitem__, "PATH", self.old_path)

    def prepare(self, claim, requested_type):
        self.controller = admit_workspace(self.attack_root, exact_claim=claim)
        description = json.loads((self.step_dir / "step.json").read_text())
        description["requested_type"] = requested_type
        (self.step_dir / "step.json").write_text(json.dumps(description))
        invoke(self.controller, "begin", begin_spec())
        review_native_inputs(self.controller, self.slug, "formal-check-1", "lean")

    def test_the_smoke_theorem_passes_with_no_axioms(self):
        self.prepare("Every residue a in Fin 4 has (a.val * a.val) % 4 not equal to 2.",
                     "∀ a : Fin 4, (a.val * a.val) % 4 ≠ 2")
        status, out, err = self.run_cli("verify", "lean", self.slug, "formal-check-1")
        self.assertEqual((status, err), (0, ""), out)
        self.assertEqual(out, "pass: square_mod_four depends on no axioms\n")
        result = self.read_json("deterministic/formal-check-1/result.json")
        self.assertEqual((result["status"], result["axioms"], result["reason"]), ("pass", [], None))
        self.assertFalse((self.step_dir / "axioms-check.lean").exists())

    def test_requested_type_coercion_cannot_hide_a_custom_axiom(self):
        (self.step_dir / "Smoke.lean").write_text(
            "axiom false_axiom : False\n"
            "instance : Coe True False where coe _ := false_axiom\n"
            "theorem square_mod_four : True := True.intro\n")
        self.prepare("False holds (an intentionally invalid verification fixture).", "False")
        status, out, err = self.run_cli("verify", "lean", self.slug, "formal-check-1")
        self.assertEqual(status, 1, out + err)
        self.assertIn("false_axiom", out)
