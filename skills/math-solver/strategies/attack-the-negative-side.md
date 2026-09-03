---
name: attack-the-negative-side
component: direction
description: Use when the leading quantifier is universal, so the negative side is one object or one universal bound, no argument reaching the statement is recorded, and the known bounds compress the counterexample space by necessary conditions, a finite range, or a record of constructions
entries: [decide-the-direction-from-construction-cost, extract-structure-from-a-hypothetical-counterexample, test-strengthenings-by-counterexample, diagonalise-against-every-candidate-under-a-guessing-principle]
precedes: [ladder-the-parameter, verify-formally-with-lean4]
excludes: []
costs: [constructivity, axioms, object, obligations]
---

## What it moves

The direction. Before: true, false, or undecided, no argument reaching
the statement recorded. After: false, journalled as an assumption with
its evidence, and the target restated as what the negative side
produces: an explicit object, a positive lower bound on what every
construction leaves out, a finite range with the conditions every
counterexample satisfies, or a counterexample built by transfinite
recursion under a named guessing principle. Statement, stage, and mode
stay.

Constructivity is paid at step 5: the counterexample is a consistency
statement, not an object. Axioms are paid there too: the counterexample
stands only under the named principle. Object is paid at steps 2 to 4:
a lower bound, a restricted range, or a neighbour replaces the claim.
Obligations are paid at step 3: the condition list is a statement still
to be proved.

## Precondition procedure

1. Is the leading quantifier universal, so the negative side is one object failing the conclusion or one universal bound? (from: shape.quantifiers; required)
2. Is the direction other than unreachable? (from: quadruple.direction; required)
3. Does the proof shape record no argument reaching the statement? (from: shape.proof_shape; required)
4. Do the known bounds compress the counterexample space: necessary conditions, a finite range, or a record of constructions with their resource? (from: shape.known_bounds; required)
5. Does the base theory name the axiom system, so a counterexample assuming more names the principle? (from: shape.base_theory; required)
6. Do the neighbours include stronger, relaxed, or translated statements with computable small instances? (from: shape.neighbours; optional)
7. Does the base theory record a guessing principle available as a hypothesis? (from: shape.base_theory; optional)

Verdict: yes when questions 1 to 5 are yes; unknown when a required
question is unknown and none is no; no otherwise. Question 4's record
runs step 2, its conditions or range step 3; a yes to question 6 opens
step 4, to question 7 step 5.

## Plan

1. Study how the negative side was attacked before, on problems of this
   shape, under `../STUDY.md`, producing
   `study/attack-the-negative-side.md`. Settle before step 2: the range
   searched with no hit; the constructions in print and their resource;
   the near misses and the conditions each passes. Output: those
   constraints; every later search starts above the settled range.
2. With a record of constructions. Entry:
   decide-the-direction-from-construction-cost, whose action 6 enters
   the direction. Output: the direction as a journalled
   assumption and the restated target, a positive lower bound on what
   every construction leaves out.
3. With conditions or a range. Entry:
   extract-structure-from-a-hypothetical-counterexample, whose action 1
   enters the direction. Output: the condition list and what
   it leaves, a finite range or a structured class. Conditions
   contradicting a known theorem (the entry's action 3) leave no object:
   the direction returns to true and the chain goes to the cash-out as
   a full proof.
4. When question 6 is yes. Entry: test-strengthenings-by-counterexample,
   naming the neighbour in the journal. Output: the boundary of what
   stays open and the hypothesis any proof must use.
5. When question 7 is yes. Entry:
   diagonalise-against-every-candidate-under-a-guessing-principle.
   Output: "the principle implies the negation", naming the
   principle.
6. Hand steps 2 to 5's outputs to the next strategy or the cash-out.

## Failure signal

Steps 2, 4, and 5 are one move each, step 3 at most three. One firing
ends it; the harness sets its verdict to no.

- A record extends at a fixed resource rate: the record gains the
  pattern, as the seed the entry's action 4 passes to seed-and-amplify,
  and the direction true as an assumption.
- No compression: the resource curve fits a polynomial and an
  exponential alike, no argument yields a condition, or a known object
  that is not a counterexample passes every condition. The record gains
  the table or condition list, the answer no to question 4, and the
  near miss, `prove-the-barrier-first`'s input.
- The range left exceeds computational reach, a witness is not countably
  parametrised, or two extensions of the recursion conflict: the record
  gains the threshold, or the obstacle and the principle attempted.

## Cash-out

From the forms in `../CASHOUT.md`: a counterexample to the
target is a standalone unit (form 5); under a guessing principle,
conditional (form 1), its extracted principle a reduction (form 3). The
condition list is a reduction (form 3), or conditional (form 1) under a
hypothesis; the threshold it leaves a quantitative improvement (form
2); a list leaving no object the `full-proof` form (`../CASHOUT.md`). A
neighbour's counterexample or the record table
is computational evidence (form 5); the sharpened boundary a problem
paper (form 7).

## Composes with

Precedes `ladder-the-parameter`, the enforced order: step 3's
structured class is its subclass rung, step 2's lower bound its number
with a gap. Also enforced: `verify-formally-with-lean4` follows a
finite witness. Unenforced: `prove-the-barrier-first` follows the
no-compression signal.
Follows `transfer-between-finitary-and-infinitary`, which lists it
under `precedes`, and, unenforced, `strengthen-and-generalise`, taking
a refuted candidate's counterexample space. Excludes nothing: a
direction move composes with any component.

## Common mistakes

- The direction moved on expectation and the raw space searched from the
  smallest instance. Check: the step 2 or 3 move carries a record table
  or condition list, and the search starts above the settled range.
- A neighbour's counterexample, a proxy, or a near miss reported as the
  target's. Check: the unit's claim is read against `claim` in
  `problem.json` and the object tested against the full hypothesis by
  an independent computation.
- A counterexample under a guessing principle reported as unconditional.
  Check: the unit's claim names the principle.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/attack-the-negative-side.md` is missing
  or empty (`../harness/README.md`).
