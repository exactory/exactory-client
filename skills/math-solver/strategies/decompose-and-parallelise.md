---
name: decompose-and-parallelise
component: organisation
description: Use when the planned proof is a chain of implications each carrying a numerical parameter, the final bound is a function of all of them, and at least two parameters move independently
entries: [split-into-components-with-explicit-interfaces, make-the-bound-explicit-then-attack-the-lossiest-step, choose-the-auxiliary-weight-or-certificate, optimise-the-certificate-family-numerically, formalise-while-fresh]
precedes: [verify-formally-with-lean4]
excludes: []
costs: [bound_quality, axioms, object, obligations]
---

## What it moves

The organisation. Before: progress is one scalar and every move touches
the whole chain. After: a set of components, each delivering a named
parameter across an inequality interface and worked in parallel, and a
ledger recombining them into the final bound. Statement, stage,
direction, and mode stay.

Bound quality is paid at steps 2 and 3: an interface inequality drops what
two adjacent steps share, so the recombined bound is weaker than the chain
worked in one piece gives. Axioms are paid at step 6, where a check the
kernel cannot reduce is evaluated natively and the axiom list carries the
compiler. The object cost is paid at step 5, where the bound left standing
carries the hardest component's inequality as an added hypothesis, so what
the ledger recombines is not the claim. Obligations are paid at steps 2 and
5, where every interface inequality and step 5's hypothesis is a statement
the record still owes.

## Precondition procedure

1. Is the planned proof a chain of implications, each carrying a numerical parameter across an inequality interface, the final bound a function of all of them? (from: shape.proof_shape; required)
2. Is the target a number rather than a yes/no property? (from: shape.target_quantity; required)
3. Does the proof shape record each step's loss separately, with at least two steps sharing no parameter? (from: shape.proof_shape; required)
4. Do the known bounds record each method's ceiling, with every bound below its ceiling? (from: shape.known_bounds; optional)
5. Does the proof shape record a step bounded by a sum against a free auxiliary object? (from: shape.proof_shape; optional)
6. Does the base theory record that a machine check of an interface theorem stays inside it? (from: shape.base_theory; required)

Verdict: yes when questions 1 to 3 and 6 are yes; unknown when one is
unknown and none is no; no otherwise. A no for question 4 marks the
barrier row; a yes for question 5 marks step 4's numerical row.

## Plan

1. Study how the chain was cut and its components worked before, on
   problems of this shape, under `../STUDY.md`, producing
   `study/decompose-and-parallelise.md`. Settle before step 2: which
   component each route in print improved, and to what value; where
   each stopped, and whether that component is a known barrier; whether
   two components were found to share a parameter. Output: the settled
   values, step 2's current column, and the barrier and coupled rows.
2. Cut the chain into inequality interfaces: component A delivers x,
   component B needs x at least y. Entry:
   split-into-components-with-explicit-interfaces, step 1. Output: the
   component table, interface and current value per row.
3. Write the final bound as an explicit function of the table's
   parameters. Entry:
   make-the-bound-explicit-then-attack-the-lossiest-step. Output: the
   explicit bound and the loss ledger; the dominant row opens step 4.
4. Work the components in parallel, each with its own journalled moves
   and budget; a component bounded through a free auxiliary object
   dispatches choose-the-auxiliary-weight-or-certificate, then
   optimise-the-certificate-family-numerically. Entry:
   split-into-components-with-explicit-interfaces, step 2. Output: per
   component, a new value with proof, a numerical optimum labelled form
   5, or its failure signal.
5. Isolate the hardest component: a dedicated proof, or an explicit
   hypothesis while the others close. Entry:
   split-into-components-with-explicit-interfaces, step 3. Output: the
   hardest row's interface inequality, proved or marked as the
   hypothesis.
6. Encode the interface theorem of a closed component whose proof is long
   or computer-dependent, when question 6 is yes and `preconditions.json`
   records verdict yes for `verify-formally-with-lean4`. At any other
   verdict, hand the interface theorem to that strategy at a later step
   of the walk and go to step 7. Entry: formalise-while-fresh. Output: the
   machine-checked interface theorem and its blueprint.
7. Recombine: recompute the bound and the ledger. Entry:
   split-into-components-with-explicit-interfaces, step 4. Return to step
   4 while a round moves the bound, three rounds at most; then hand table,
   interfaces, and ledger to the next strategy or the cash-out.

## Failure signal

The strategy ends when:

- A component delivers a structural property no parameter captures: the
  record gains its name and the verdict no for question 1.
- Two components share a parameter, so raising one lowers the other: the
  record gains the coupling inequality and the verdict no for question 3.
- A component is a known barrier the bound cannot move without: the
  record gains the ceiling.
- Three rounds of step 4 end, or two leave the bound unchanged: the
  record gains the last ledger.

The harness then sets the verdict to no.

## Cash-out

From the forms in `../CASHOUT.md`: a bound past the
record: quantitative improvement (form 2); each interface statement:
reduction (form 3); the bound under the hardest component as
hypothesis: conditional result (form 1); a standalone component: new
machinery (form 6); an unproved numerical optimum: computational
evidence (form 5). Components joined by an interface are one unit under
`../CASHOUT.md`.

## Composes with

Follows `ladder-the-parameter`, whose independent ledger rows are the
components, and a stage strategy supplying the chain,
`reduce-and-translate` or `transport-to-a-tractable-category`. Typically
before `reduce-to-a-finite-computation` (a finitising component),
`verify-formally-with-lean4` (a closed interface theorem), and
`work-conditionally-then-discharge` (the hardest component as
hypothesis). Excludes nothing: the quadruple stays.

## Common mistakes

- One method for every component. Check: each row names the shape
  features its moves matched; its route is absent from the study record.
- A component's scalar taken as the target. Check: every round ends at
  step 7, bound recomputed.
- Interfaces stated in words. Check: each is an inequality with a named
  parameter and a value on both sides.
- A numerical optimum reported as a value. Check: proved, or the row is
  labelled form 5.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/decompose-and-parallelise.md` is missing
  or empty (`../harness/README.md`).
