import hashlib
import json

from tests.support import WorkspaceTest, make_move, write_journal

EVIDENCE_FORM = "counterexample-or-computational-evidence"


class CheckUnitTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        write_journal(self.workspace, [make_move(1), make_move(2)])
        (self.workspace / "units" / "INVENTORY.md").write_text("# Inventory: sample\n")
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

    def make_run(self, name, result=None):
        run_dir = self.workspace / "deterministic" / name
        run_dir.mkdir()
        if result is not None:
            (run_dir / "result.json").write_text(json.dumps(result))

    def pay(self, *costs):
        write_journal(self.workspace, [make_move(1, costs_paid=list(costs)), make_move(2)])

    def test_accepts_a_complete_unit(self):
        self.assertEqual(self.check(self.make_unit()), (0, "units/1/unit.json: ok\n", ""))

    def test_accepts_full_proof_and_second_proof_forms(self):
        self.assertEqual(self.check(self.make_unit(form="full-proof"))[0], 0)
        self.assertEqual(self.check(self.make_unit(form="second-proof"))[0], 0)

    def test_refuses_before_the_inventory_exists(self):
        (self.workspace / "units" / "INVENTORY.md").unlink()
        self.assertEqual(
            self.check(self.make_unit())[2],
            "units/INVENTORY.md: missing; run stall before check-unit\n",
        )

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

    # Evidence that is a deterministic run

    def test_accepts_evidence_that_is_a_deterministic_run_that_passed(self):
        self.make_run("enumeration-1", {"status": "pass"})
        self.assertEqual(self.check(self.make_unit(evidence="deterministic/enumeration-1"))[0], 0)

    def test_rejects_a_deterministic_run_that_has_no_result(self):
        self.make_run("enumeration-1")
        self.assertEqual(
            self.check(self.make_unit(evidence="deterministic/enumeration-1"))[2],
            "evidence: deterministic/enumeration-1 has no result.json; run verify first\n",
        )

    def test_a_run_that_did_not_pass_makes_the_unit_evidence(self):
        for status in ("fail", "evidence"):
            with self.subTest(status=status):
                self.make_run("run-" + status, {"status": status})
                unit = self.make_unit(evidence="deterministic/run-" + status, form="full-proof")
                self.assertEqual(
                    self.check(unit)[2],
                    "form: full-proof rests on deterministic/run-%s with status %s; a run that did not pass"
                    " is evidence, and the form is %s\n" % (status, status, EVIDENCE_FORM),
                )
                self.assertEqual(self.check(dict(unit, form=EVIDENCE_FORM))[0], 0)

    # What the ledger decides about the form

    def test_a_closing_form_carries_neither_object_nor_obligations(self):
        for form in ("full-proof", "second-proof"):
            with self.subTest(form=form):
                self.pay("object")
                self.assertEqual(
                    self.check(self.make_unit(form=form, costs=["object"]))[2],
                    "form: %s carries object; a proof of the claim itself pays no object, so this is a"
                    " conditional-or-special-case unit plus a reduction-or-equivalence unit\n" % form,
                )
                self.pay("obligations")
                self.assertEqual(
                    self.check(self.make_unit(form=form, costs=["obligations"]))[2],
                    "form: %s carries obligations; a proof that still owes a statement is a"
                    " conditional-or-special-case unit\n" % form,
                )

    def test_constructivity_excludes_the_forms_that_exhibit_the_object(self):
        self.pay("constructivity")
        for form in ("algorithm", "counterexample"):
            with self.subTest(form=form):
                self.assertEqual(
                    self.check(self.make_unit(form=form, costs=["constructivity"]))[2],
                    "form: %s carries constructivity; the evidence does not exhibit the object\n" % form,
                )

    # The rest of the record

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

    # The stamp

    def test_stamps_the_checked_unit_with_the_digest_of_its_record(self):
        self.check(self.make_unit())
        stamp = json.loads((self.unit_dir / "check-unit.json").read_text())
        digest = hashlib.sha256((self.unit_dir / "unit.json").read_bytes()).hexdigest()
        self.assertEqual(stamp, {"unit_sha256": digest})

    def test_a_refused_unit_carries_no_stamp(self):
        self.check(self.make_unit())
        self.assertEqual(self.check(self.make_unit(statement=" "))[0], 1)
        self.assertFalse((self.unit_dir / "check-unit.json").exists())
