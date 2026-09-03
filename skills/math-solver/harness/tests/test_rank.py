from tests.support import ALL_YES, WorkspaceTest, make_preconditions, make_problem, write_ranking, write_study


class RankTest(WorkspaceTest):
    """The plan says which strategies are admitted as openings; the solver says in
    what order it would open with them, and every row cites what it read."""

    def setUp(self):
        super().setUp()
        self.write_json("problem.json", make_problem())
        write_study(self.workspace, "problem")
        self.write_json("preconditions.json", make_preconditions(ALL_YES))
        self.run_cli("plan", self.slug)

    def openings(self):
        return [row["strategy"] for row in self.read_json("openings.json")["openings"]]

    def rank(self):
        return self.run_cli("rank", self.slug)

    def test_accepts_a_ranking_over_every_opening(self):
        write_ranking(self)
        status, out, err = self.rank()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "1. %s" % self.openings()[0])
        self.assertEqual(len(out.splitlines()), len(self.openings()))

    def test_accepts_an_order_the_plan_did_not_choose(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"].reverse()
        self.write_json("ranking.json", ranking)
        status, out, err = self.rank()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "1. %s" % self.openings()[-1])

    def test_rejects_a_ranking_that_leaves_an_opening_out(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        dropped = ranking["order"].pop()["strategy"]
        self.write_json("ranking.json", ranking)
        self.assertEqual(self.rank()[2], "ranking.json: strategy %s is not ordered\n" % dropped)

    def test_rejects_a_strategy_the_plan_did_not_admit(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["strategy"] = "made-up"
        self.write_json("ranking.json", ranking)
        self.assertIn("ranking.json: made-up is not in openings.json\n", self.rank()[2])

    def test_rejects_a_repeated_strategy(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][1]["strategy"] = ranking["order"][0]["strategy"]
        self.write_json("ranking.json", ranking)
        self.assertIn("is ordered twice", self.rank()[2])

    def test_rejects_a_row_with_no_reason(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["reason"] = "  "
        self.write_json("ranking.json", ranking)
        self.assertEqual(self.rank()[2], "ranking.json row 1: reason: empty string\n")

    def test_rejects_a_row_that_cites_nothing(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["cites"] = []
        self.write_json("ranking.json", ranking)
        self.assertEqual(self.rank()[2], "ranking.json row 1: cites nothing\n")

    def test_rejects_a_citation_that_names_no_field_and_no_cost(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["cites"] = ["shape.nope"]
        self.write_json("ranking.json", ranking)
        self.assertEqual(
            self.rank()[2],
            "ranking.json row 1: cites shape.nope, which is not a problem.json field or a cost\n",
        )

    def test_rejects_a_cost_citation_outside_the_vocabulary(self):
        write_ranking(self, cites=("cost:elegance",))
        self.assertIn("cites cost:elegance, which is not a problem.json field or a cost", self.rank()[2])

    def cite_on_the_ladder_row(self, citation):
        """ladder-the-parameter declares bound_quality in the fixture and nothing else."""
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        row = next(row for row in ranking["order"] if row["strategy"] == "ladder-the-parameter")
        row["cites"] = [citation]
        self.write_json("ranking.json", ranking)
        return self.rank()

    def test_accepts_a_cost_the_strategy_declares(self):
        self.assertEqual(self.cite_on_the_ladder_row("cost:bound_quality")[0], 0)

    def test_rejects_a_cost_the_strategy_does_not_declare(self):
        self.assertEqual(
            self.cite_on_the_ladder_row("cost:axioms")[2],
            "ranking.json row 2: cites cost:axioms, which ladder-the-parameter does not declare\n",
        )
