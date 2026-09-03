---
name: transfer-between-finitary-and-infinitary
component: stage
description: Use when an infinite object or a parameter-indexed family is quantified over, one side has a finite certificate, the statement is monotone in a parameter, and the known bounds state the constant's dependence on it
entries: [reduce-to-finite-witnesses, enumerate-small-cases-to-locate-the-threshold, make-the-bound-explicit-then-attack-the-lossiest-step]
precedes: [attack-the-negative-side, verify-formally-with-lean4]
excludes: []
costs: [effectivity, constructivity, bound_quality, axioms, object, obligations]
---

## What it moves

The stage, between one infinite object and a finite family indexed by a
parameter, tied by a proved equivalence with a constant uniform in it.
Giving up effective bounds moves to the infinite object and a
qualitative statement; recovering them, to the finite family and an
explicit constant. Direction does not move; mode is checked, not moved.

`effectivity` is paid at step 3, where compactness gives a constant whose
value the record loses. `constructivity` is paid at the same step, which
proves a parameter exists and exhibits none. `axioms` is paid there too,
where the compactness principle borrows strength beyond the recorded base
theory, and the record loses the theory it was posed in. `bound_quality` is
paid at step 4 giving up, where a qualitative statement replaces the bound.
`object` is paid at steps 3 and 4, where uniformity in the parameter leaves
the record. `obligations` is paid at step 2, which claims the uniform
constant step 3 must prove.

## Precondition procedure

1. Is what is quantified over infinite, or a family of finite objects indexed by a parameter? (from: shape.objects; required)
2. Does one side of the statement have a finite certificate? (from: shape.finite_certificates; required)
3. Is the statement monotone in a parameter? (from: shape.monotonicity; required)
4. Is that parameter's smallest open value recorded? (from: shape.monotonicity; optional)
5. Do the known bounds give the constant as independent of that parameter or as left to recover from a qualitative proof, not as growing with it or unstated? (from: shape.known_bounds; required)
6. Does the proof shape record that qualitative proof step by step with its losses? (from: shape.proof_shape; optional)
7. Does the mode accept a non-effective existence proof (existence or undecided)? (from: quadruple.mode; optional)
8. Is the target quantity one a qualitative statement settles, or the explicit constant the recovering direction returns? (from: shape.target_quantity; required)
9. Does the base theory name the system the statement is posed in? (from: shape.base_theory; required)

Verdict: yes when questions 1, 2, 3, 5, 8, and 9 are yes; unknown when
one is unknown and none is no; no otherwise. `shape.known_bounds` names the
direction: left to recover is recovering, open on question 6's yes;
independent is giving up, open on question 7's yes.

## Plan

1. Study how the statement was moved between its infinite and finite
   versions before, on problems of this shape, under `../STUDY.md`,
   producing `study/transfer-between-finitary-and-infinitary.md`. Settle
   before step 2: the finite versions in print, and whether each
   equivalence was proved both ways; the last parameter settled by
   computation; whether the constant's dependence on the parameter was
   traced. Output: the constraints on steps 2 to 5.
2. Write the finite version beside the infinite statement, naming
   question 3's parameter and the constant that must not depend on it.
   Entry: reduce-to-finite-witnesses, step 1. Output: both statements,
   to step 3. The move's action carries the direction to steps 4 and 5.
3. Prove the equivalence, both implications stated and the non-effective
   one marked; record the stage move in `problem.json`. Entry:
   reduce-to-finite-witnesses, steps 2 and 3. Output: the lemma, the
   finite statement equivalent to the original with named parameter and
   uniform constant.
4. Settle question 2's side up the parameter from question 4's value, or
   the study's settled range on a no. Entry:
   enumerate-small-cases-to-locate-the-threshold, at most four moves.
   Output: exact values or a threshold, a seed object, a finite-range
   exclusion. Giving up ends here; the next strategy takes the infinite
   statement.
5. When recovering, trace the constant through question 6's proof,
   replace the step where the parameter enters, and check the bound
   against step 4's values. Entry:
   make-the-bound-explicit-then-attack-the-lossiest-step. Output: an
   explicit bound with its ledger, or the resisting step.

## Failure signal

Steps 2, 3, and 5 are one move each, step 4 at most four: seven moves.
It ends at step 5's output or when one fires:

- Step 2: the direction is not open. The record gains both statements,
  the fail note naming the closing question.
- Step 2 or 3: the constant grows with the parameter, so finite instances
  decide nothing. The record gains the dependence and a no on question 5.
- Step 4, any move: the certificate outgrows every tool before a pattern
  appears. The record gains the last settled parameter as a finite-range
  exclusion.
- Step 5: nothing finite replaces the non-effective step. The record
  gains the ledger with that step as the ceiling.

The verdict becomes no; the statements and the lemma stay in the record.

## Cash-out

From the forms in `../CASHOUT.md`:

- Step 3's lemma: reduction or equivalence (form 3).
- Step 4's parameters: computational evidence (form 5) and a
  special-case result (form 1) up to that parameter; a negative object
  is standalone.
- Step 5's bound: quantitative improvement (form 2), the ledger as
  evidence.

## Composes with

Precedes `attack-the-negative-side`: the stage moves before the
direction, so the counterexample space it compresses is the finite
version's objects. `reduce-to-a-finite-computation`
follows unenforced, taking step 3's finite version and the open range
above step 4's exclusion. Follows `solve-the-model-world-first`, whose
stage returns to the original first. Excludes nothing: a stage move
composes with any direction or mode move.

## Common mistakes

- Small parameters reported with no equivalence lemma. Check: the step 3
  move precedes any enumeration move in the journal.
- The direction chosen by computation cost or recent results. Check: the
  step 2 move's action cites `shape.known_bounds` and names the direction.
- A compactness step under a mode that rejects it, or one implication
  missing. Check: question 7's answer, and both implications marked.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/transfer-between-finitary-and-infinitary.md` is missing
  or empty (`../harness/README.md`).
