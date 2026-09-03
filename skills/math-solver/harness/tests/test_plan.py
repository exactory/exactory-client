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
        self.assertFalse((self.workspace / "openings.json").exists())

    def test_refuses_to_run_when_the_problem_study_is_empty(self):
        write_study(self.workspace, "problem", " \n")
        status, out, err = self.plan(make_preconditions(ALL_YES))
        self.assertEqual(status, 1)
        self.assertEqual(err, "study/problem.md: missing or empty; write the problem-level study (STUDY.md) before plan\n")

    def test_writes_the_openings(self):
        status, out, err = self.plan(make_preconditions(ALL_YES))
        self.assertEqual((status, err), (0, ""))
        written = self.read_json("openings.json")
        self.assertEqual(written["generated_from"], "preconditions.json")
        self.assertEqual(len(written["openings"]), 5)
        self.assertEqual(
            written["openings"][0],
            {
                "rank": 1,
                "strategy": "attack-the-negative-side",
                "verdict": "yes",
                "component": "direction",
                "costs": [],
            },
        )

    def test_prints_one_line_per_opening_in_rank_order(self):
        verdicts = dict(ALL_YES, **{"reduce-to-a-finite-computation": "no", "prove-the-barrier-first": "no"})
        status, out, err = self.plan(make_preconditions(verdicts))
        self.assertEqual(
            out.splitlines(),
            [
                "1. attack-the-negative-side  verdict=yes component=direction costs=none",
                "2. ladder-the-parameter  verdict=yes component=statement costs=bound_quality",
                "3. solve-the-model-world-first  verdict=yes component=stage costs=object",
            ],
        )

    def test_an_unknown_verdict_ranks_after_every_yes(self):
        verdicts = {name: "no" for name in ALL_YES}
        verdicts["solve-the-model-world-first"] = "unknown"
        verdicts["ladder-the-parameter"] = "yes"
        status, out, err = self.plan(make_preconditions(verdicts))
        self.assertEqual(
            out.splitlines(),
            [
                "1. ladder-the-parameter  verdict=yes component=statement costs=bound_quality",
                "2. solve-the-model-world-first  verdict=unknown component=stage costs=object",
            ],
        )

    def test_says_so_when_nothing_is_admissible(self):
        status, out, err = self.plan(make_preconditions({name: "no" for name in ALL_YES}))
        self.assertEqual((status, out), (0, "no admissible opening\n"))
        self.assertEqual(self.read_json("openings.json")["openings"], [])

    def test_rejects_a_missing_strategy(self):
        preconditions = make_preconditions(ALL_YES)
        del preconditions["prove-the-barrier-first"]
        status, out, err = self.plan(preconditions)
        self.assertEqual((status, err), (1, "preconditions.json: missing strategy prove-the-barrier-first\n"))
        self.assertFalse((self.workspace / "openings.json").exists())

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
