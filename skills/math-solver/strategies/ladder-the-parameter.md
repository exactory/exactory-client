---
name: ladder-the-parameter
component: statement
description: Use when the target is a number with an explicit gap below its ideal value and the known argument has locatable losses or a free auxiliary object
entries: [make-the-bound-explicit-then-attack-the-lossiest-step, choose-the-auxiliary-weight-or-certificate, optimise-the-certificate-family-numerically, relax-to-the-averaged-or-fractional-version, prove-the-subcritical-or-asymptotic-version-first, prove-the-special-case-where-the-method-is-stronger]
precedes: [verify-formally-with-lean4]
excludes: []
costs: [effectivity, bound_quality, object, obligations]
---

## What it moves

The statement. Before: the proposition at its ideal value. After: the
proposition at a rung, a less demanding value of one progress parameter (a
smaller constant, a subcritical exponent, an average, a subclass), plus a
remainder carrying the rung to the ideal. Stage, direction, and mode stay.

`effectivity` is paid at step 4, where an asymptotic rung holds above a
threshold the argument does not compute, and the record loses the value of
that threshold. `object` is paid at steps 3 to 5: the rung is the claim at
another value, on average, or on a subclass, and not the ideal.
`bound_quality` is paid at steps 3 and 4, where the bound becomes a
functional at an object, a numerical optimum, or a fractional value, and
the record loses the proved constant. `obligations` is paid at steps 3 to
5, where the remainder (a rounding gap, a passage statement, a bootstrap)
stays open beside the rung.

## Precondition procedure

Answer from the named field of `problem.json`.

1. Is the target a number rather than a yes/no property? (from: shape.target_quantity; required)
2. Do the known bounds record a current and a trivial or conjectured value, so the gap is explicit? (from: shape.known_bounds; required)
3. Is the known argument a chain of steps with locatable losses, or a sum against a free auxiliary object? (from: shape.proof_shape; required)
4. Is a free parameter recorded over which uniformity is the difficulty? (from: shape.uniformity_parameter; optional)
5. Is the statement recorded as monotone in its own critical parameter, so a less demanding value is weaker? (from: shape.monotonicity; optional)
6. Do the neighbours record an open special case of the statement on a subclass of the objects? (from: shape.neighbours; optional)

Verdict: yes when questions 1 to 3 are yes; unknown when one is unknown and
none is no; no otherwise. Questions 4 and 5 pick step 4's entry; question
6 picks step 5.

## Plan

1. Study how the parameter was laddered before, on problems of this
   shape, under `../STUDY.md`, producing `study/ladder-the-parameter.md`.
   Settle before step 2: the trivial, current, and ideal values in print,
   with the method behind each; the rung each route stopped at and the
   step that was tight there; whether a weight family or a numerical
   optimum was already reported. Output: the constraints on the first
   rung.
2. Draw the ladder: trivial, current, and ideal values, and the losses by
   step. Entry: make-the-bound-explicit-then-attack-the-lossiest-step,
   steps 1 and 2. Output: the ledger; its lossiest step selects the rung
   in steps 3 to 5.
3. Rung by auxiliary object, when the loss is a free choice. Entries:
   choose-the-auxiliary-weight-or-certificate, then
   optimise-the-certificate-family-numerically. Output: the target bounded
   by the functional at the exhibited object, and the numerical optimum
   with its conjecturally sharp cases; the proved bound is the rung.
4. Rung by relaxation. Entry: relax-to-the-averaged-or-fractional-version
   when question 4 is yes,
   prove-the-subcritical-or-asymptotic-version-first when question 5 is
   yes. Output: the theorem at the relaxed or less demanding value; the
   rounding gap or passage statement is the remainder.
5. Rung by the subclass, when question 6 is yes or step 4 found a regime
   where the passage holds. Entry:
   prove-the-special-case-where-the-method-is-stronger. Output: the theorem
   on the subclass, with the bootstrap proved or recorded as the gap.
6. Attack the lossiest step. Entry:
   make-the-bound-explicit-then-attack-the-lossiest-step, steps 3 and 4.
   Output: the bound with explicit constants and the next ledger. Return
   to step 3 while a move raises the value; otherwise
   hand the rung, with exact hypothesis, remainder, and ledger, to the
   next strategy or the cash-out.

## Failure signal

The strategy ends when:

- No lossiest step: the record gains the ledger and the verdict no for
  question 3.
- The family's optimum saturates short of the ideal (step 3's first
  entry's failure signal), or the lossiest step is tight for the method:
  the record gains the ceiling.
- The remainder is the whole difficulty: the record gains rung and
  remainder as exact statements.
- Three moves without a higher value: the record gains the last ledger.

The harness then sets the verdict to no.

## Cash-out

From the forms in `../CASHOUT.md`: a raised value:
quantitative improvement (form 2); a rung with its remainder open:
conditional or special-case result (form 1); ideal equals rung plus
remainder: reduction (form 3); a ceiling with a near miss: barrier
(form 4); the weight family: new machinery (form 6); an unproved
numerical optimum: computational evidence (form 5).

## Composes with

The front matter enforces one order after it,
`verify-formally-with-lean4`, and one before it:
`attack-the-negative-side` lists this strategy under `precedes`, its
structured class the subclass rung. Not enforced, after it:
`reduce-to-a-finite-computation`, taking the explicit bound's finite
range; `prove-the-barrier-first`, taking a tight step as its shared
property; `decompose-and-parallelise`, taking independent ledger rows.
Follows a stage strategy supplying the ledger's chain, and
`strengthen-and-generalise` when its removed feature is the lossiest
step. Excludes nothing: a statement move composes with any other
component.

## Common mistakes

- A computable proxy replaces the rung. Check: each rung is the claim
  with one parameter changed, and rung plus remainder implies the
  ideal.
- The rung is chosen by cost or by the latest mechanism in print. Check:
  each rung names the ledger row it attacks and is absent from the study
  record.
- A move that raises nothing is closed unexplained. Check: every move
  records the value before and after; each rung enters the record before
  the next starts.
- A numerical optimum reported as the bound. Check: the constant is
  proved, or the unit is labelled form 5.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/ladder-the-parameter.md` is missing
  or empty (`../harness/README.md`).
