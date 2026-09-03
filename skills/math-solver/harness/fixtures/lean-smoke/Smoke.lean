/-!
Smoke fixture for the harness's `verify lean` step. Library only, no Mathlib.
Each theorem is generic; `step.json` names the one the harness checks.
-/

/-- A finite statement: no square is 2 modulo 4. The quantifier stays inside
the statement because `decide` rejects a target with local variables; the
`Decidable` instance for `∀ a : Fin 4` enumerates the four cases. -/
theorem square_mod_four : ∀ a : Fin 4, (a.val * a.val) % 4 ≠ 2 := by
  decide

/-- A linear-arithmetic statement with its hypothesis as an explicit argument.
`omega` closes goals in linear arithmetic over `Nat` and `Int`. -/
theorem two_mul_le_add (a b : Nat) (h : a ≤ b) : 2 * a ≤ a + b := by
  omega
