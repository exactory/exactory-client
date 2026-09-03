---
name: prove-the-barrier-first
component: direction
description: Use when every recorded method stops at one documented value or a long period has produced no argument either way, the stopped methods share a property that can be written down, and a neighbouring variant, relativised world, modified problem, or independent statement is recorded
entries: [axiomatise-the-method-and-build-a-near-miss, modify-the-problem-inside-the-methods-invariance, seed-and-amplify, test-independence-under-two-opposite-axioms, diagonalise-against-every-candidate-under-a-guessing-principle, adjoin-the-wanted-object-by-generic-approximation]
precedes: [verify-formally-with-lean4]
excludes: []
costs: [constructivity, axioms, object, obligations]
---

## What it moves

The direction. Before: true or false. After: unreachable for a named
class of arguments; statement and stage stay. The output is a barrier
theorem: no argument in the class passes the ceiling, or the base theory
decides neither side. Its proof is a near miss, an object satisfying
everything the methods use and failing the conclusion.

Constructivity is paid at step 3: the model gives consistency, not an
object. Axioms are paid there too: each direction stands only under its
hypothesis beyond the base theory. Object is paid at step 4: the
theorem is about a class of arguments or a modified problem, not the
claim. Obligations are paid at step 3: membership, failure, and the
modified problem's answer are still to be proved.

## Precondition procedure

1. Do the known bounds record methods stopped at one documented value, diminishing returns, or a long period with no argument either way? (from: shape.known_bounds; required)
2. Does the proof shape name a property the stopped methods share: some axioms only, relativisation, a conserved quantity and a scaling only, or provability from the base theory? (from: shape.proof_shape; required)
3. Do the neighbours record an abstract set system, an averaged operator, a relativised world, a modified problem, or a statement shown independent? (from: shape.neighbours; required)
4. Does the base theory name the axiom system, so a barrier stated as unprovability names what it is relative to? (from: shape.base_theory; required)
5. Does the base theory record a hypothesis beyond it deciding the statement in one direction? (from: shape.base_theory; optional)
6. Do the neighbours record the countable or definable case as settled? (from: shape.neighbours; optional)

Verdict: yes when questions 1 to 4 are yes; unknown when one of them
is unknown and none is no; no otherwise. Yes to 5 and 6 sends
steps 2 to 4 to the independence form.

## Plan

1. Study how barriers were proved on problems of this shape, under
   `../STUDY.md`, producing `study/prove-the-barrier-first.md`. Settle
   before step 2: the stopped methods and the value each reached, step
   2's class; the barriers in print, step 4's citations, one on this
   class ending the strategy; the near misses and modified problems,
   for step 3. Output: those constraints.
2. Fix the admissible class, a definition deciding any argument's
   membership, from the property every stopped method invokes:
   axiomatise-the-method-and-build-a-near-miss, step 1, for one method;
   modify-the-problem-inside-the-methods-invariance, steps 1 and 2, for
   several; every proof from the base theory in the independence form.
   Output: the class.
3. Build the near miss: an object satisfying the axioms and violating the
   bound, or the admissible family's optimum
   (axiomatise-the-method-and-build-a-near-miss, step 2); a member of the
   modification class where the conclusion fails
   (modify-the-problem-inside-the-methods-invariance, step 3,
   seed-and-amplify engineering it); or two models under opposite
   hypotheses (test-independence-under-two-opposite-axioms, steps 1 to
   3, running diagonalise-against-every-candidate-under-a-guessing-principle
   for the negation, adjoin-the-wanted-object-by-generic-approximation
   for the statement). Output: the near miss with proofs of membership
   and failure, or the two models with their hypotheses.
4. State the barrier as a theorem: class, ceiling or modified problem,
   arguments inside and outside, and the property the near miss lacks
   (axiomatise-the-method-and-build-a-near-miss, steps 3 and 4;
   modify-the-problem-inside-the-methods-invariance, steps 4 and 5). In
   the independence form (test-independence-under-two-opposite-axioms,
   step 4) the theorem is independence over the base theory, and the
   lacking property is each direction's hypothesis beyond it. Output:
   the unit per `../CASHOUT.md`.
5. Hand off: write the lacking property into `shape.missing_input`,
   return the direction to true or false, and pass the record to the next
   strategy or the unit to cash-out.

## Failure signal

One of these ends the strategy:

- No recorded variant exhibits the ceiling or the opposite answer: no
  object satisfying the axioms violates the bound, or the modified
  problem keeps the original's answer: the record gains the class and
  each failed construction with the axiom it broke.
- An independence direction fails at a lemma, or the two need
  incompatible side hypotheses. The record gains the lemma (a candidate
  theorem of the base theory when both fail there) or the pair, and any
  one-direction result.
- The barrier is already in print: the record gains the reference.
- The budget, two admissible classes and three near-miss constructions
  per class, is spent: the record gains the classes tried and their
  failed constructions, form 7 content.

## Cash-out

From the forms in `../CASHOUT.md`: a barrier or independence
proof is form 4; the modified problem's counterexample, form 5; one
model or a counterexample under a guessing principle, form 1; the
admissible family's optimum, when new, form 2; a map of classes and
ceilings with no closed barrier, form 7.

## Composes with

Follows `split-structure-from-randomness` and `reduce-and-translate`,
which list it under `precedes`: the increment's dominant loss or the
destination's tools are the property to axiomatise. Follows
`ladder-the-parameter`, unenforced, a tight step being the property,
and `attack-the-negative-side`, its near misses' common property being
the class. Precedes `verify-formally-with-lean4`; the
lacking property feeds a later statement or mode strategy. Excludes
nothing: a direction move follows any other move.

## Common mistakes

- A stall reported as a barrier: "every method stops here" is
  activity. Check: the unit passes the claim test of `../CASHOUT.md`,
  with proof.
- The shared property left in prose. Check: question 2 is answered from
  `shape.proof_shape`, not the journal.
- The class defined as "what has been tried". Check: the definition
  decides membership of an argument nobody has written.
- The modified problem's answer reported as the target's. Check: the
  unit names the modified problem and records the direction unreachable
  for the class.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/prove-the-barrier-first.md` is missing
  or empty (`../harness/README.md`).
