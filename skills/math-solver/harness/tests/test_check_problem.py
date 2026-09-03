from tests.support import WorkspaceTest, make_problem


class CheckProblemTest(WorkspaceTest):
    def check(self, problem):
        self.write_json("problem.json", problem)
        return self.run_cli("check-problem", self.slug)

    def test_accepts_a_complete_problem(self):
        self.assertEqual(self.check(make_problem()), (0, "problem.json: ok\n", ""))

    def test_rejects_the_skeleton_init_wrote(self):
        status, out, err = self.run_cli("check-problem", self.slug)
        self.assertEqual((status, out), (1, ""))
        self.assertIn("claim: empty string\n", err)
        self.assertIn("quadruple.statement: empty string\n", err)
        self.assertIn("quadruple.stage: empty string\n", err)

    def test_rejects_a_missing_shape_key(self):
        problem = make_problem()
        del problem["shape"]["known_bounds"]
        status, out, err = self.check(problem)
        self.assertEqual((status, err), (1, "shape.known_bounds: missing\n"))

    def test_rejects_an_empty_shape_value(self):
        problem = make_problem()
        problem["shape"]["objects"] = ""
        self.assertEqual(self.check(problem)[2], "shape.objects: empty string\n")

    def test_rejects_an_empty_known_line(self):
        problem = make_problem()
        problem["known"] = ["a result", ""]
        self.assertEqual(self.check(problem)[2], "known[1]: empty string\n")

    def test_rejects_a_direction_outside_the_allowed_set(self):
        problem = make_problem()
        problem["quadruple"]["direction"] = "maybe"
        self.assertEqual(
            self.check(problem)[2],
            "quadruple.direction: 'maybe' is not one of true, false, unreachable, undecided\n",
        )

    def test_rejects_a_mode_outside_the_allowed_set(self):
        problem = make_problem()
        problem["quadruple"]["mode"] = "guess"
        self.assertEqual(
            self.check(problem)[2],
            "quadruple.mode: 'guess' is not one of existence, construction, computation, certificate, undecided\n",
        )

    def test_rejects_a_missing_top_level_key(self):
        problem = make_problem()
        del problem["known"]
        self.assertEqual(self.check(problem)[2], "known: missing\n")

    def test_reports_every_problem_on_its_own_line(self):
        problem = make_problem()
        problem["claim"] = ""
        problem["shape"]["objects"] = ""
        status, out, err = self.check(problem)
        self.assertEqual(err, "claim: empty string\nshape.objects: empty string\n")

    def test_rejects_a_file_that_is_not_json(self):
        (self.workspace / "problem.json").write_text("{not json")
        status, out, err = self.run_cli("check-problem", self.slug)
        self.assertEqual(status, 1)
        self.assertTrue(err.startswith("problem.json: not valid JSON"), err)

    def test_rejects_a_missing_file(self):
        (self.workspace / "problem.json").unlink()
        status, out, err = self.run_cli("check-problem", self.slug)
        self.assertEqual((status, err), (1, "problem.json: missing\n"))
