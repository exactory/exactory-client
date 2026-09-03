from tests.support import StepTest


class VerifyLeanTest(StepTest):
    def setUp(self):
        super().setUp()
        self.write_step_file("lakefile.lean", "import Lake\nopen Lake DSL\npackage step\n")
        self.write_step_file("lean-toolchain", "leanprover/lean4:v4.22.0\n")
        self.write_step_file("Main.lean", "theorem my_theorem : True := trivial\n")
        self.write_step_file("step.json", '{"theorem": "my_theorem"}')

    def verify(self):
        return self.run_cli("verify", "lean", self.slug, self.step_name)

    def test_passes_when_the_theorem_avoids_sorryAx(self):
        status, out, err = self.verify()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out, "pass: my_theorem depends on axioms propext, Classical.choice, Quot.sound\n")
        result = self.read_result()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["theorem"], "my_theorem")
        self.assertEqual(result["axioms"], ["propext", "Classical.choice", "Quot.sound"])
        self.assertIsNone(result["reason"])

    def test_builds_in_the_step_directory_then_prints_the_axioms_from_a_temporary_file(self):
        self.verify()
        log = self.log.read_text().splitlines()
        self.assertEqual(log[0], "%s build" % self.step_dir.resolve())
        self.assertEqual(log[1], "%s env lean axioms-check.lean" % self.step_dir.resolve())
        self.assertEqual(log[2:4], ["import Main", "#print axioms my_theorem"])
        self.assertFalse((self.step_dir / "axioms-check.lean").exists())

    def test_imports_the_module_named_in_step_json(self):
        self.write_step_file("Proofs/Bound.lean", "theorem my_theorem : True := trivial\n")
        self.write_step_file("step.json", '{"theorem": "my_theorem", "file": "Proofs/Bound.lean"}')
        self.assertEqual(self.verify()[0], 0)
        self.assertIn("import Proofs.Bound", self.log.read_text().splitlines())

    def test_passes_with_no_axioms(self):
        self.set_env(FAKE_LAKE_AXIOMS="")
        status, out, err = self.verify()
        self.assertEqual((status, out), (0, "pass: my_theorem depends on no axioms\n"))
        self.assertEqual(self.read_result()["axioms"], [])

    def test_fails_on_sorryAx(self):
        self.set_env(FAKE_LAKE_AXIOMS="propext, sorryAx")
        status, out, err = self.verify()
        self.assertEqual((status, out, err), (1, "fail: my_theorem depends on sorryAx\n", ""))
        result = self.read_result()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["axioms"], ["propext", "sorryAx"])
        self.assertEqual(result["reason"], "my_theorem depends on sorryAx")

    def test_fails_when_lake_build_fails(self):
        self.set_env(FAKE_LAKE_BUILD_STATUS="1")
        status, out, err = self.verify()
        self.assertEqual((status, out), (1, "fail: lake build failed\n"))
        result = self.read_result()
        self.assertEqual((result["status"], result["reason"]), ("fail", "lake build failed"))
        self.assertEqual(result["output_head"], ["Build completed successfully."])
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_fails_when_lean_prints_no_axioms_line(self):
        self.set_env(FAKE_LAKE_LEAN_STATUS="1")
        status, out, err = self.verify()
        self.assertEqual((status, out), (1, "fail: no axioms line in the lean output\n"))
        self.assertEqual(self.read_result()["output_head"], ["axioms-check.lean:1:0: error: unknown identifier"])

    def test_rejects_a_missing_step_directory(self):
        status, out, err = self.run_cli("verify", "lean", self.slug, "nope")
        self.assertEqual((status, err), (1, "deterministic/nope: missing\n"))

    def test_rejects_a_missing_step_json(self):
        (self.step_dir / "step.json").unlink()
        self.assertEqual(self.verify()[2], "step.json: missing\n")

    def test_rejects_step_json_without_a_theorem(self):
        self.write_step_file("step.json", '{"file": "Main.lean"}')
        self.assertEqual(self.verify()[2], "step.json: missing theorem\n")

    def test_rejects_a_directory_that_is_not_a_lean_project(self):
        (self.step_dir / "lakefile.lean").unlink()
        (self.step_dir / "lean-toolchain").unlink()
        (self.step_dir / "Main.lean").unlink()
        self.assertEqual(
            self.verify()[2],
            "formal-check-1: no lakefile.lean or lakefile.toml\n"
            "formal-check-1: no lean-toolchain\n"
            "Main.lean: missing\n",
        )
        self.assertFalse(self.log.exists())

    def test_accepts_a_toml_lakefile(self):
        (self.step_dir / "lakefile.lean").unlink()
        self.write_step_file("lakefile.toml", 'name = "step"\n')
        self.assertEqual(self.verify()[0], 0)

    def test_fails_on_a_custom_axiom(self):
        self.set_env(FAKE_LAKE_AXIOMS="propext, my_conjecture")
        status, out, err = self.verify()
        self.assertEqual((status, out), (1, "fail: my_theorem depends on custom axioms my_conjecture\n"))
        self.assertEqual(self.read_result()["status"], "fail")

    def test_reports_native_evaluation_as_evidence(self):
        self.set_env(FAKE_LAKE_AXIOMS="propext, Lean.trustCompiler, Lean.ofReduceBool")
        status, out, err = self.verify()
        self.assertEqual(
            (status, out),
            (
                0,
                "evidence: my_theorem depends on Lean.trustCompiler, Lean.ofReduceBool:"
                " native evaluation, computational evidence rather than a kernel-checked certificate\n",
            ),
        )
        result = self.read_result()
        self.assertEqual(result["status"], "evidence")
        self.assertEqual(result["axioms"], ["propext", "Lean.trustCompiler", "Lean.ofReduceBool"])

    def test_treats_a_native_decide_axiom_as_native_evaluation(self):
        self.set_env(FAKE_LAKE_AXIOMS="my_theorem._native.native_decide.ax_1")
        status, out, err = self.verify()
        self.assertEqual((status, self.read_result()["status"]), (0, "evidence"))

    def test_sorryAx_outranks_the_other_classes(self):
        self.set_env(FAKE_LAKE_AXIOMS="sorryAx, my_conjecture, Lean.trustCompiler")
        status, out, err = self.verify()
        self.assertEqual((status, out), (1, "fail: my_theorem depends on sorryAx\n"))
