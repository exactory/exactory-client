from tests.support import ALL_YES, WorkspaceTest, make_preconditions, make_problem, write_study


class PlanTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.write_json("problem.json", make_problem())
        write_study(self.workspace, "problem")

    def plan(self, preconditions):
        self.write_json("preconditions.json", preconditions)
        return self.run_cli("plan", self.slug)

    def one(self, answers, verdict="yes"):
        """A preconditions.json in which ladder-the-parameter has the given answers."""
        record = make_preconditions(ALL_YES)
        record["ladder-the-parameter"] = {"verdict": verdict, "answers": [
            {"question": number, "answer": answer, "cites": "shape.objects"}
            for number, answer in enumerate(answers, start=1)]}
        return record

    def test_verdict_yes_survives_an_optional_answer_that_is_not_yes(self):
        for optional in ("no", "unknown"):
            status, out, err = self.plan(self.one(["yes", "yes", optional]))
            self.assertEqual((status, err), (0, ""), "optional %s" % optional)
            self.assertIn("ladder-the-parameter", out)

    def test_verdict_yes_needs_every_required_answer_yes(self):
        for required in ("no", "unknown"):
            status, out, err = self.plan(self.one(["yes", required, "yes"]))
            self.assertEqual(status, 1, "required %s" % required)
            self.assertEqual(err, "ladder-the-parameter: verdict yes requires every required answer yes\n")

    def test_verdict_no_needs_a_required_answer_no(self):
        status, out, err = self.plan(self.one(["yes", "yes", "no"], verdict="no"))
        self.assertEqual(err, "ladder-the-parameter: verdict no requires at least one required answer no\n")

    def test_verdict_unknown_needs_a_required_answer_unknown(self):
        status, out, err = self.plan(self.one(["yes", "yes", "unknown"], verdict="unknown"))
        self.assertEqual(err, "ladder-the-parameter: verdict unknown requires no required answer no and at least one required unknown\n")

    def test_rejects_a_record_that_leaves_a_question_unanswered(self):
        status, out, err = self.plan(self.one(["yes", "yes"]))
        self.assertEqual((status, err), (1, "ladder-the-parameter: question 3 is not answered\n"))

    def test_rejects_an_answer_to_a_question_the_strategy_does_not_ask(self):
        status, out, err = self.plan(self.one(["yes", "yes", "yes", "yes"]))
        self.assertEqual((status, err), (1, "ladder-the-parameter: question 4 is not in the strategy file\n"))

    def test_rejects_a_key_the_record_may_not_carry(self):
        record = make_preconditions(ALL_YES)
        record["ladder-the-parameter"]["optional_answers"] = [{"question": 3, "answer": "no"}]
        status, out, err = self.plan(record)
        self.assertEqual((status, err), (1, "ladder-the-parameter: unknown key optional_answers\n"))

    def test_refuses_to_run_without_the_problem_study(self):
        (self.workspace / "study" / "problem.md").unlink()
        status, out, err = self.plan(make_preconditions(ALL_YES))
        self.assertEqual((status, out), (1, ""))
        self.assertEqual(err, "study/problem.md: missing or empty; write the problem-level study (STUDY.md) before plan\n")
        self.assertFalse((self.workspace / "compositions.json").exists())

    def test_refuses_to_run_when_the_problem_study_is_empty(self):
        write_study(self.workspace, "problem", " \n")
        status, out, err = self.plan(make_preconditions(ALL_YES))
        self.assertEqual(status, 1)
        self.assertEqual(err, "study/problem.md: missing or empty; write the problem-level study (STUDY.md) before plan\n")

    def test_writes_the_ranked_compositions(self):
        status, out, err = self.plan(make_preconditions(ALL_YES))
        self.assertEqual((status, err), (0, ""))
        written = self.read_json("compositions.json")
        self.assertEqual(written["generated_from"], "preconditions.json")
        self.assertEqual(len(written["compositions"]), 20)
        self.assertEqual(
            written["compositions"][0],
            {
                "rank": 1,
                "id": "attack-the-negative-side+ladder-the-parameter"
                      "+reduce-to-a-finite-computation+solve-the-model-world-first",
                "strategies": [
                    "attack-the-negative-side",
                    "ladder-the-parameter",
                    "reduce-to-a-finite-computation",
                    "solve-the-model-world-first",
                ],
                "yes": 4,
                "unknown": 0,
                "components": ["direction", "mode", "stage", "statement"],
                "costs": ["bound_quality", "constructivity", "object"],
                "assumption": None,
            },
        )

    def test_prints_one_line_per_composition_in_rank_order(self):
        verdicts = dict(ALL_YES, **{"reduce-to-a-finite-computation": "no", "prove-the-barrier-first": "no"})
        status, out, err = self.plan(make_preconditions(verdicts))
        self.assertEqual(
            out.splitlines()[:2],
            [
                "1. attack-the-negative-side -> ladder-the-parameter -> solve-the-model-world-first"
                "  yes=3 unknown=0 components=direction,stage,statement",
                "2. attack-the-negative-side -> solve-the-model-world-first -> ladder-the-parameter"
                "  yes=3 unknown=0 components=direction,stage,statement",
            ],
        )

    def test_names_the_assumption_on_the_printed_line(self):
        verdicts = {name: "no" for name in ALL_YES}
        verdicts["ladder-the-parameter"] = "yes"
        verdicts["solve-the-model-world-first"] = "unknown"
        status, out, err = self.plan(make_preconditions(verdicts))
        self.assertEqual(
            out.splitlines()[0],
            "1. ladder-the-parameter -> solve-the-model-world-first"
            "  yes=1 unknown=1 components=stage,statement assumption=solve-the-model-world-first",
        )

    def test_says_so_when_nothing_is_admissible(self):
        status, out, err = self.plan(make_preconditions({name: "no" for name in ALL_YES}))
        self.assertEqual((status, out), (0, "no admissible composition\n"))
        self.assertEqual(self.read_json("compositions.json")["compositions"], [])

    def test_rejects_a_missing_strategy(self):
        preconditions = make_preconditions(ALL_YES)
        del preconditions["prove-the-barrier-first"]
        status, out, err = self.plan(preconditions)
        self.assertEqual((status, err), (1, "preconditions.json: missing strategy prove-the-barrier-first\n"))
        self.assertFalse((self.workspace / "compositions.json").exists())

    def test_rejects_a_name_with_no_strategy_file(self):
        preconditions = make_preconditions(ALL_YES)
        preconditions["README"] = preconditions["ladder-the-parameter"]
        status, out, err = self.plan(preconditions)
        self.assertEqual(err, "preconditions.json: no strategy file for README\n")

    def test_rejects_a_cited_field_absent_from_the_problem(self):
        preconditions = make_preconditions(ALL_YES)
        preconditions["ladder-the-parameter"]["answers"][1]["cites"] = "shape.nope"
        status, out, err = self.plan(preconditions)
        self.assertEqual(err, "ladder-the-parameter question 2: cites shape.nope, which is not in problem.json\n")

    def test_accepts_a_cited_quadruple_field(self):
        preconditions = make_preconditions(ALL_YES)
        preconditions["ladder-the-parameter"]["answers"][1]["cites"] = "quadruple.direction"
        self.assertEqual(self.plan(preconditions)[0], 0)

    def test_rejects_yes_with_a_required_answer_that_is_not_yes(self):
        preconditions = make_preconditions(ALL_YES)
        preconditions["ladder-the-parameter"]["answers"][0]["answer"] = "unknown"
        status, out, err = self.plan(preconditions)
        self.assertEqual(err, "ladder-the-parameter: verdict yes requires every required answer yes\n")

    def test_rejects_unknown_with_a_required_no_answer(self):
        preconditions = make_preconditions(dict(ALL_YES, **{"ladder-the-parameter": "unknown"}))
        preconditions["ladder-the-parameter"]["answers"][0]["answer"] = "no"
        status, out, err = self.plan(preconditions)
        self.assertEqual(
            err,
            "ladder-the-parameter: verdict unknown requires no required answer no"
            " and at least one required unknown\n",
        )

    def test_rejects_unknown_without_a_required_unknown_answer(self):
        preconditions = make_preconditions(dict(ALL_YES, **{"ladder-the-parameter": "unknown"}))
        preconditions["ladder-the-parameter"]["answers"][1]["answer"] = "yes"
        status, out, err = self.plan(preconditions)
        self.assertEqual(
            err,
            "ladder-the-parameter: verdict unknown requires no required answer no"
            " and at least one required unknown\n",
        )

    def test_rejects_no_without_a_required_no_answer(self):
        preconditions = make_preconditions(dict(ALL_YES, **{"ladder-the-parameter": "no"}))
        preconditions["ladder-the-parameter"]["answers"][1]["answer"] = "unknown"
        status, out, err = self.plan(preconditions)
        self.assertEqual(err, "ladder-the-parameter: verdict no requires at least one required answer no\n")

    def test_accepts_a_noted_no_without_a_no_answer(self):
        preconditions = make_preconditions(ALL_YES)
        preconditions["ladder-the-parameter"]["verdict"] = "no"
        preconditions["ladder-the-parameter"]["note"] = "failure signal fired at move 4"
        status, out, err = self.plan(preconditions)
        self.assertEqual((status, err), (0, ""))

    def test_rejects_a_verdict_outside_the_allowed_set(self):
        preconditions = make_preconditions(ALL_YES)
        preconditions["ladder-the-parameter"]["verdict"] = "maybe"
        status, out, err = self.plan(preconditions)
        self.assertEqual(err, "ladder-the-parameter: verdict 'maybe' is not one of yes, no, unknown\n")

    def test_rejects_an_answer_outside_the_allowed_set(self):
        """An answer that is not yes, no, or unknown is reported on its own: the
        verdict cannot be judged until every answer is one of the three."""
        preconditions = make_preconditions(ALL_YES)
        preconditions["ladder-the-parameter"]["answers"][0]["answer"] = "probably"
        status, out, err = self.plan(preconditions)
        self.assertEqual(
            err, "ladder-the-parameter question 1: answer 'probably' is not one of yes, no, unknown\n"
        )

    def test_rejects_a_missing_preconditions_file(self):
        status, out, err = self.run_cli("plan", self.slug)
        self.assertEqual((status, err), (1, "preconditions.json: missing\n"))
