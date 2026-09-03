from tests.support import ALL_YES, WorkspaceTest, make_preconditions, make_problem, write_ranking, write_study


class RankTest(WorkspaceTest):
    """The plan says which compositions are admissible; the solver says in what order
    to take them, and every row cites what it read."""

    def setUp(self):
        super().setUp()
        self.write_json("problem.json", make_problem())
        write_study(self.workspace, "problem")
        self.write_json("preconditions.json", make_preconditions(ALL_YES))
        self.run_cli("plan", self.slug)

    def ids(self):
        return [row["id"] for row in self.read_json("compositions.json")["compositions"]]

    def rank(self):
        return self.run_cli("rank", self.slug)

    def test_accepts_a_ranking_over_every_composition(self):
        write_ranking(self)
        status, out, err = self.rank()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "1. %s" % self.ids()[0])
        self.assertEqual(len(out.splitlines()), len(self.ids()))

    def test_accepts_an_order_the_plan_did_not_choose(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"].reverse()
        self.write_json("ranking.json", ranking)
        status, out, err = self.rank()
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "1. %s" % self.ids()[-1])

    def test_rejects_a_ranking_that_leaves_a_composition_out(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        dropped = ranking["order"].pop()["composition"]
        self.write_json("ranking.json", ranking)
        self.assertEqual(self.rank()[2], "ranking.json: composition %s is not ordered\n" % dropped)

    def test_rejects_a_composition_the_plan_did_not_emit(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["composition"] = "made+up"
        self.write_json("ranking.json", ranking)
        err = self.rank()[2]
        self.assertIn("ranking.json: made+up is not in compositions.json\n", err)

    def test_rejects_a_repeated_composition(self):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][1]["composition"] = ranking["order"][0]["composition"]
        self.write_json("ranking.json", ranking)
        err = self.rank()[2]
        self.assertIn("is ordered twice", err)

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

    def cite_on_first_row(self, citation):
        write_ranking(self)
        ranking = self.read_json("ranking.json")
        ranking["order"][0]["cites"] = [citation]
        self.write_json("ranking.json", ranking)
        return self.rank()

    def test_accepts_a_cost_the_composition_may_pay(self):
        self.assertEqual(self.cite_on_first_row("cost:constructivity")[0], 0)

    def test_rejects_a_cost_no_strategy_of_that_composition_declares(self):
        self.assertEqual(
            self.cite_on_first_row("cost:axioms")[2],
            "ranking.json row 1: cites cost:axioms, which no strategy of that composition declares\n",
        )
