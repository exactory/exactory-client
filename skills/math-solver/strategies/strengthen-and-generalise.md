---
name: strengthen-and-generalise
component: statement
description: Use when the natural argument is an induction, an iteration, or a chain of inequalities, and the hypothesis or target configuration is destroyed by passage to a subfamily, a link, a smaller scale, or a truncation
entries: [strengthen-the-target, test-strengthenings-by-counterexample, strengthen-the-inductive-hypothesis, export-the-lemma-to-sibling-problems]
precedes: [verify-formally-with-lean4]
excludes: []
costs: [implication, bound_quality, object, obligations]
---

## What it moves

The statement. Before: the proposition as posed, with an exact
configuration or rigid hypothesis the passage operations destroy.
After: a stronger statement over the widest natural class whose
hypothesis and conclusion survive every passage operation, implying the
original by a short deduction. Stage, direction, and mode stay.

`object` is paid at step 2, where the statement becomes the stronger one
over the wider class, and at step 4, where it carries the tracked
object. `implication` is paid at step 2: the deduction runs from the
strengthening to the original only, so a counterexample to the
strengthening leaves the claim standing. `bound_quality` is paid at step
2 when that deduction costs a factor, which the record keeps in the
bound it returns to the original. `obligations` is paid at steps 2 and
4: the strengthened statement, its base scale, and the repair step each
stay to be proved.

## Precondition procedure

Answer from the named field of `problem.json`.

1. Does the recorded proof shape name an induction, an iteration, or a chain of inequalities? (from: shape.proof_shape; required)
2. Is the target configuration recorded as destroyed by restriction, passage to a link, rescaling, or truncation? (from: shape.configuration; required)
3. Do the known bounds record a per-step loss compounding to the whole gap, or the natural argument stopped short of the target? (from: shape.known_bounds; required)
4. Do the neighbours list siblings sharing the bottleneck's shape, or a refutation of a candidate strengthening? (from: shape.neighbours; optional)
5. Do the finite certificates record small instances as computable by solver? (from: shape.finite_certificates; optional)

Verdict: yes when questions 1 to 3 are yes; unknown when one of
questions 1 to 3 is unknown and none is no; no otherwise. A recorded
refutation excludes its candidate in step 2.

## Plan

1. Study how the statement was strengthened before, on problems of this
   shape, under `../STUDY.md`, producing
   `study/strengthen-and-generalise.md`. Settle before step 2: the
   candidate strengthenings in print and which were refuted, with the
   refuting instance; the inductions that stopped, and at which scale;
   the siblings sharing the bottleneck, for step 5. Output: the
   constraints on step 2's first candidate.
2. Write the strengthening: a stronger statement whose hypothesis and
   conclusion survive every passage operation the argument needs, with
   the short deduction to the original. Entry dispatched:
   strengthen-the-target, steps 1 to 4. Output: the candidate and its
   deduction, journalled as a change of statement, handed to step 3.
3. Test the candidate on small instances. Entry dispatched:
   test-strengthenings-by-counterexample. Output: a counterexample,
   returning the plan to step 2. Step 4 begins on the entry's failure
   signal, no counterexample in the computable range; the candidate is
   then the statement to prove.
4. Close the induction: add the tracked object and repair step that
   make the step at stage k deliver strictly more than it receives, and
   prove the base scale. Entry dispatched:
   strengthen-the-inductive-hypothesis. Output: the inductive lemma and
   base case, proving the stronger statement.
5. Export the lemma to the siblings and prove the variant on which its
   bound is sharp. Entry dispatched:
   export-the-lemma-to-sibling-problems. Output: theorems on the
   siblings and the lemma's sharpness statement, handed with step 4's
   proved statement to the next strategy or the cash-out.

## Failure signal

The strategy ends, within three candidates and two repair attempts each,
when the third candidate is refuted by a small instance; when the
deduction to the original is long or costs a factor destroying the
target growth rate; or when the repair step costs more than the loss it
repairs or the strengthened hypothesis fails at the base scale. The
harness then sets the verdict to no. The record gains the
operation no candidate survived, each refuting instance, and the
inductive lemma as far as it closed.

## Cash-out

From the forms in `../CASHOUT.md`: the deduction to the
original is a reduction (form 3); a refuted candidate is a
counterexample (form 5); a reduced per-step loss is a quantitative
improvement (form 2); the exported lemma is new machinery (form 6) and
its sharpness a barrier (form 4); a tested, unproved candidate is a
problem paper (form 7).

## Composes with

Precedes `verify-formally-with-lean4`, which receives the closed chain,
the one order the front matter enforces. Not enforced:
`attack-the-negative-side` after it, taking a refuted candidate's
counterexample space; `ladder-the-parameter` before it when its loss
ledger's lossiest step is what step 4 repairs, after it when the
feature removed here was the lossiest step. Excludes nothing: a
statement move composes with any other component.

## Common mistakes

- A computable proxy in place of the target, with no deduction to the
  original. Check: step 2's journal line has `problem_changed` true
  and names the deduction.
- The candidate attacked before it is tested. Check: a step 4 move exists
  only after a step 3 move with `failure_signal_fired` true and no
  counterexample.
- The strategy chosen by cost or by what recent results use. Check:
  `preconditions.json` answers the three required questions yes from the
  cited fields.
- The step at stage k delivers exactly what it received. Check: the
  lemma's conclusion is strictly stronger than its hypothesis.
- A refuted candidate or a skipped step dropped without record. Check:
  the refuting instance is in a step 3 move's output; a skipped step's
  move has `failure_signal_fired` true and the reason.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/strengthen-and-generalise.md` is missing
  or empty (`../harness/README.md`).
