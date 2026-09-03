from tests.support import WorkspaceTest, make_move, write_journal


class CheckUnitTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        write_journal(self.workspace, [make_move(1), make_move(2)])
        self.unit_dir = self.workspace / "units" / "1"
        self.unit_dir.mkdir()
        (self.unit_dir / "proof.md").write_text("proof\n")

    def make_unit(self, **overrides):
        unit = {
            "statement": "For every n the bound holds.",
            "form": "quantitative-improvement",
            "evidence": "units/1/proof.md",
            "novelty": "2026-09-01 searched by statement; no hit",
            "moves": [1, 2],
            "costs": [],
        }
        unit.update(overrides)
        return unit

    def check(self, unit):
        self.write_json("units/1/unit.json", unit)
        return self.run_cli("check-unit", self.slug, "1")

    def test_accepts_a_complete_unit(self):
        self.assertEqual(self.check(self.make_unit()), (0, "units/1/unit.json: ok\n", ""))

    def test_accepts_full_proof_and_second_proof_forms(self):
        self.assertEqual(self.check(self.make_unit(form="full-proof"))[0], 0)
        self.assertEqual(self.check(self.make_unit(form="second-proof"))[0], 0)

    def test_rejects_a_missing_key(self):
        unit = self.make_unit()
        del unit["novelty"]
        self.assertEqual(self.check(unit)[2], "novelty: missing\n")

    def test_rejects_an_empty_statement(self):
        self.assertEqual(self.check(self.make_unit(statement=" "))[2], "statement: empty string\n")

    def test_rejects_a_form_outside_the_allowed_set(self):
        status, out, err = self.check(self.make_unit(form="essay"))
        self.assertTrue(err.startswith("form: 'essay' is not one of conditional-or-special-case, "), err)
        self.assertTrue(err.endswith(", full-proof, second-proof\n"), err)

    def test_rejects_evidence_that_does_not_exist(self):
        self.assertEqual(self.check(self.make_unit(evidence="units/1/nope.md"))[2], "evidence: units/1/nope.md does not exist\n")

    def test_accepts_evidence_that_is_a_deterministic_run_directory(self):
        (self.workspace / "deterministic" / "enumeration-1").mkdir()
        self.assertEqual(self.check(self.make_unit(evidence="deterministic/enumeration-1"))[0], 0)

    def test_rejects_moves_absent_from_the_journal(self):
        self.assertEqual(self.check(self.make_unit(moves=[1, 5]))[2], "moves: 5 is not a journal move\n")

    def test_rejects_an_empty_move_list(self):
        self.assertEqual(self.check(self.make_unit(moves=[]))[2], "moves: empty\n")

    def test_rejects_moves_that_are_not_ints(self):
        self.assertEqual(self.check(self.make_unit(moves=["1"]))[2], "moves: must be a list of int\n")

    def test_rejects_a_missing_unit_file(self):
        status, out, err = self.run_cli("check-unit", self.slug, "1")
        self.assertEqual((status, err), (1, "unit.json: missing\n"))

    def test_rejects_a_unit_that_does_not_state_its_ledger(self):
        unit = self.make_unit()
        del unit["costs"]
        self.assertEqual(self.check(unit)[2], "costs: missing\n")

    def test_rejects_a_cost_outside_the_vocabulary(self):
        self.assertEqual(
            self.check(self.make_unit(costs=["elegance"]))[2],
            "costs: 'elegance' is not one of implication, effectivity, constructivity,"
            " bound_quality, axioms, object, obligations\n",
        )


    def test_the_ledger_is_the_sum_over_the_moves_the_unit_lists(self):
        write_journal(
            self.workspace,
            [make_move(1, costs_paid=["object"]), make_move(2, costs_paid=["object", "axioms"])],
        )
        self.assertEqual(self.check(self.make_unit(costs=["axioms", "object"]))[0], 0)

    def test_rejects_a_ledger_that_drops_a_cost_a_listed_move_paid(self):
        write_journal(
            self.workspace,
            [make_move(1, costs_paid=["object"]), make_move(2, costs_paid=["axioms"])],
        )
        self.assertEqual(
            self.check(self.make_unit(costs=["object"]))[2],
            "costs: moves 1, 2 paid axioms, object\n",
        )

    def test_rejects_a_ledger_that_adds_a_cost_no_listed_move_paid(self):
        write_journal(self.workspace, [make_move(1), make_move(2)])
        self.assertEqual(
            self.check(self.make_unit(costs=["object"]))[2],
            "costs: moves 1, 2 paid nothing\n",
        )
