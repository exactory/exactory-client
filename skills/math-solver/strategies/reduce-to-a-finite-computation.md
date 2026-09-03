---
name: reduce-to-a-finite-computation
component: mode
description: Use when each instance is finite and checkable, one side has a finite certificate, and the known bounds leave a finite residue, a parameter range, a tail inequality, local configurations, or a truncated certificate family
entries: [reduce-to-finite-witnesses, average-a-local-inequality-over-neighbourhoods, certify-the-finite-residue-by-computation, choose-the-auxiliary-weight-or-certificate, predict-the-value-and-test-it-numerically, enumerate-small-cases-to-locate-the-threshold, optimise-the-certificate-family-numerically]
precedes: [verify-formally-with-lean4]
excludes: []
costs: [implication, effectivity, bound_quality, axioms, object, obligations]
---

## What it moves

The mode. Before: an argument a referee reads. After: a finite list of
instances, each decided by a deterministic run with an independently
checked certificate, plus a completeness argument (a tail estimate, a
range bound, a classification). Statement, stage, and direction stay.

Implication is paid at step 2 on the local-configurations branch: the
local inequality is sufficient only, so a failing configuration no longer
refutes the claim. A compactness reduction at step 2 pays effectivity:
the record then names a parameter it cannot compute. Bound quality is
paid at steps 4 and 5: the value proved is what exact arithmetic confirms
over the truncated family or covered range, below the run's optimum.
Axioms are paid at step 5, where a check the kernel cannot reduce is
evaluated natively and the trusted base grows past the base theory. The
object cost is paid at step 2, where the residue carries a quantifier
order and a uniform constant the claim does not. Obligations are paid
there too: the completeness argument is a statement the record still
owes.

## Precondition procedure

1. Is each instance finite and checkable, with a finite certificate on one side? (from: shape.finite_certificates; required)
2. Do the known bounds leave a finite residue: a parameter range, a tail inequality, local configurations, or a truncated certificate family? (from: shape.known_bounds; required)
3. Is the mode computation, certificate, or undecided? (from: quadruple.mode; required)
4. Is the statement monotone in a parameter? (from: shape.monotonicity; optional)
5. Is a construction recorded whose value bounds the target from the side opposite to question 1's certificates? (from: shape.extremal_candidate; optional)
6. Does the proof shape record a finite step that a standard solver or a proof assistant runs? (from: shape.proof_shape; optional)
7. Is the target quantity finiteness, a decision, a threshold, or a value the runs produce, rather than a constant the finitisation leaves uncomputed? (from: shape.target_quantity; required)
8. Does the base theory record that the residue's machine check stays inside it? (from: shape.base_theory; required)

Verdict: yes when questions 1 to 3, 7 and 8 are yes; unknown when one is
unknown and none is no; no otherwise. Question 4 sets step 4's stopping point,
question 5 opens its optimisation, question 6 picks step 5's tools.

## Plan

1. Study how the statement was finitised and computed before, on
   problems of this shape, under `../STUDY.md`, producing
   `study/reduce-to-a-finite-computation.md`. Settle before step 2: the
   last value settled by computation, with the encoder and solver that
   settled it; whether each certificate had an independent checker; the
   residue in print and where its completeness argument stopped.
   Output: the last settled value, solvers, and routes not to duplicate.
2. Write the residue. Entry, by residue kind: reduce-to-finite-witnesses
   for a parameter range; average-a-local-inequality-over-neighbourhoods
   for local configurations; certify-the-finite-residue-by-computation,
   step 1, for a tail inequality;
   choose-the-auxiliary-weight-or-certificate, steps 1 to 3, for a
   certificate family. Output: the residue with its completeness
   argument.
3. Predict. Entry: predict-the-value-and-test-it-numerically, steps 1, 2
   and 6. Output: the confirmed or corrected conjecture for the threshold
   or optimum, and the instances checked, bounding step 4's range.
4. Settle the instances. Entry, by residue kind:
   enumerate-small-cases-to-locate-the-threshold as `enumeration run`
   for a parameter range, its steps 1 and 3 choosing encoder and solver,
   from the last settled value to the threshold (question 4 yes) or the
   range's end; optimise-the-certificate-family-numerically as
   `numerical optimisation run` for a certificate family when question 5
   is yes; certify-the-finite-residue-by-computation, steps 1 and 3,
   otherwise. Output: certified values, the optimum per parameter, or
   inequalities on compact ranges.
5. Certify. Entry: certify-the-finite-residue-by-computation as
   `certified special-case check`. Question 6 yes: its steps 2 and 5,
   standard solvers and the proof assistant; otherwise its step 4, a
   pipeline whose every phase emits a checkable proof. Output: the
   residue closed, with the independent checker and trusted base named in
   the run's `README.md`; or the residue open, with the settled range and
   first open instance, to the cash-out.

## Failure signal

It ends, within the move budget, when one fires:

- No residue: the constants depend on the parameter, or no local
  inequality holds. The record gains the partial residue and question
  2's no.
- The case count passes the solver's reach, or a certificate has no
  independent checker. The record gains the settled range and first open
  instance.
- The settled instances fix building blocks, not the rate, or the
  trusted base stays large. The values or pipeline enter the record as
  evidence.

The harness then sets the verdict to no.

## Cash-out

From the forms in `../CASHOUT.md`: residue closed: a full
proof by computation; a range short of the threshold: conditional result
(form 1); residue open: reduction (form 3); an unproved optimum, tested
prediction, or witness: computational evidence (form 5), a witness
standalone; the pipeline: new machinery (form 6); a corrected
prediction: problem paper (form 7).

## Composes with

Precedes `verify-formally-with-lean4`, whose plan step 4 recasts the
closed residue in the proof assistant. Follows the strategies that leave the residue:
`ladder-the-parameter` (a range),
`transfer-between-finitary-and-infinitary` (the finite side),
`reduce-and-translate` and `transport-to-a-tractable-category`
(enumerable instances), `attack-the-negative-side` (a compressed
counterexample space). Excludes nothing: a mode change composes with any
earlier statement, stage, or direction change.

## Common mistakes

- A computable proxy replaces the claim. Check: step 2's residue reads
  "the claim holds when these instances hold".
- The strategy entered on cost. Check: the verdict lists its required
  answers with their fields.
- Evidence reported as proof: a numerical optimum, a self-checked
  certificate. Check: the unit is form 5 until the run's `README.md`
  names the independent checker and the trusted base.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/reduce-to-a-finite-computation.md` is missing
  or empty (`../harness/README.md`).
