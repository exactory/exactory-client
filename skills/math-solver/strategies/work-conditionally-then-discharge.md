---
name: work-conditionally-then-discharge
component: organisation
description: Use when the strongest known method leaves exactly one unproved input, a recognised conjecture, a named hypothesis, a uniform version of a known theorem, or an axiom beyond the base theory, and the implication to the target has an explicit threshold
entries: [condition-on-the-one-missing-input, test-independence-under-two-opposite-axioms]
precedes: [solve-the-model-world-first, verify-formally-with-lean4]
excludes: []
costs: [bound_quality, axioms, object, obligations]
---

## What it moves

The organisation. Before: one attempt stopped at a step it cannot
prove. After: two records. The first keeps the quadruple, the input a
declared hypothesis: "input, at this threshold, implies target". The
second is a new `problem.json` with the restricted input as claim. The
result stays conditional on that input until the second record closes.

Bound quality is paid at step 2 when the sharpest threshold in print
carries the implication only to a conclusion weaker than the claim.
Axioms are paid at steps 3 and 5, where the hypothesis is an axiom
outside the base theory, so the theorem holds in that axiom's theory. The
object cost is paid at steps 2 and 3: the implication is what is proved,
and the second record's claim is the restricted hypothesis. Obligations
are paid at step 2: the standing hypothesis is a statement the record
owes until the second record closes.

## Precondition procedure

1. Does the missing input name exactly one unproved input of the strongest known method? (from: shape.missing_input; required)
2. Is that input a recognised conjecture, a named hypothesis, a uniform version of a known theorem, or a named axiom? (from: shape.missing_input; required)
3. Does the proof shape give the implication an explicit threshold? (from: shape.proof_shape; required)
4. Do the known bounds record no equivalence of input and target? (from: shape.known_bounds; required)
5. Do the neighbours name a setting where the input is a theorem? (from: shape.neighbours; optional)
6. Is the input an axiom the base theory leaves out? (from: shape.base_theory; optional)
7. Are the quantifiers universal over uncountable objects or all sets of reals? (from: shape.quantifiers; optional)
8. Do the neighbours record the countable or definable case as settled? (from: shape.neighbours; optional)
9. Does the base theory record what the standing hypothesis adds to it, an axiom beyond it or a statement inside it? (from: shape.base_theory; required)

Verdict: yes when questions 1 to 4 and 9 are yes; unknown when one is
unknown and none is no; no otherwise. Question 5 picks step 4's route; yes to
6, 7, and 8 sends step 3 to step 5.

## Plan

1. Study how the input was assumed and discharged before, on problems
   of this shape, under `../STUDY.md`, producing
   `study/work-conditionally-then-discharge.md`. Settle before step 2:
   the sharpest threshold in print for the implication; the methods
   that attacked the input and where each stopped, so step 4 takes
   another; the settings where the input is a theorem. Output: the
   thresholds, the stopped routes, and the analogue settings, for steps
   2 and 4.
2. State the implication as a theorem with the sharpest threshold, its
   conclusion the claim sentence verbatim; journal the input as the
   standing hypothesis. Dispatches
   condition-on-the-one-missing-input, step 1. Output: the conditional
   theorem, to step 3.
3. Restrict the hypothesis to the form the proof uses; open the second
   record with it as claim. Dispatches
   condition-on-the-one-missing-input, step 2. Output: the restricted
   hypothesis, to step 4, or to step 5 when questions 6 to 8 are yes.
4. Take the route the study and question 5 pick: prove the target where
   the input is a theorem; attack the restricted hypothesis by a method
   not recorded as stopped; or replace it by an explicit object.
   Dispatches condition-on-the-one-missing-input, steps 3 to 5.
   Output: the conditional theorem, its restricted hypothesis an
   explicit argument, to `verify-formally-with-lean4`; the
   analogue-setting special case, to `solve-the-model-world-first` as
   its model proof; the second record, with what it still needs, to
   the cash-out.
5. Test whether the opposite axiom refutes the target. Dispatches
   test-independence-under-two-opposite-axioms. Output: an independence
   theorem, or a one-directional conditional result with the open half
   named, to the cash-out.

## Failure signal

The strategy ends in bounded moves with verdict no; the record gains
what each signal names:

- the input is equivalent to the target: the proved equivalence;
- the implication reaches only a weaker conclusion under the full
  input: that conclusion with its threshold;
- the restricted form needs an estimate no technique or analogue
  gives: the open sub-question with its ceiling;
- both step 5 directions fail at one lemma: that lemma as a candidate
  theorem of the base theory;
- three consecutive moves under step 4 or 5 end in the entry's signal:
  the routes and where each stopped.

## Cash-out

From the forms in `../CASHOUT.md`: the implication with its
threshold, naming the restricted hypothesis, and step 5's
one-directional half are conditional results (form 1); the
analogue-setting theorem a special case (form 1); a proved equivalence
a reduction (form 3); an independence theorem a barrier (form 4).

## Composes with

Precedes `solve-the-model-world-first`, whose model is the analogue
setting, and `verify-formally-with-lean4`, where the standing
hypothesis is an explicit argument. Follows strategies ending in a
chain with one unproved node: `ladder-the-parameter`,
`strengthen-and-generalise`, `reduce-and-translate`. Excludes nothing:
it moves no quadruple component.

## Common mistakes

- The hypothesis assumed, never journalled as a decision. Check: step
  2's journal line names it, and the second record opened at step 3
  carries `parent.json` naming this one.
- The full hypothesis assumed when the proof uses less. Check: step 3's
  form is weaker, or the record says why not.
- The discharge tried by the method that left the input missing. Check:
  the study names it; step 4's route differs.
- Step 4 or 5 run until the budget is spent. Check:
  `failure_signal_fired` per journal line; the third consecutive true
  ends the strategy.
- The conditional theorem held back until the attack stalls. Check: the
  journal records it when this strategy ends; its unit is written at
  stage 7.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/work-conditionally-then-discharge.md` is missing
  or empty (`../harness/README.md`).
