---
name: split-structure-from-randomness
component: direction
description: Use when the target bounds the density of a set avoiding a configuration in a structured ambient, a random set of that density contains the configuration, and structured pieces and a bias statistic exist
entries: [iterate-a-structure-versus-randomness-increment, strengthen-the-target, measure-size-by-entropy, bound-failure-by-random-restriction-and-encoding, revive-the-abandoned-route]
precedes: [prove-the-barrier-first, verify-formally-with-lean4]
excludes: []
costs: [implication, bound_quality, object, obligations]
---

## What it moves

The direction. Before: undecided, or true by one argument for every set.
After: true by dichotomy: a set of the claimed density is random-like,
so the configuration count gives the claim, or biased, so denser on a
structured piece, where the argument restarts. Stage and mode stay; the
statement changes only by a strengthening implying the original.

Implication is paid at step 2: the dichotomy proves the upper bound
only, so no move under this strategy can refute the claim. Bound quality
is paid at step 5: the exponent is what the ratio of increment to loss
allows, so the record carries a weaker bound than the claim. Object is
paid at step 3: the strengthening replaces the target with a robust
version, and the claim stays in the record only through the deduction
written with it. Obligations are paid at steps 2 and 3: the dichotomy
lemma, the closure of the pieces, and that deduction are statements
still to be proved.

## Precondition procedure

1. Is the target an upper bound on the density of a set or family? (from: shape.target_quantity; required)
2. Does the set avoid the configuration? (from: shape.configuration; required)
3. Do the known bounds record a random set of the claimed density containing the configuration? (from: shape.known_bounds; required)
4. Does the ambient supply structured pieces (subprogressions, subspaces, links), closed or nearly closed under intersection, and a bias statistic (a transform coefficient, a dense link, a spread parameter)? (from: shape.ambient_structure; required)
5. Do the structured pieces stay closed, or approximately closed, under the operations the argument needs (translation, dilation, intersection)? (from: shape.symmetries; required)
6. Can the objects become random variables, so size is an entropy? (from: shape.objects; optional)
7. Is the family spread, no small set in many members? (from: shape.objects; optional)

Verdict: yes when every required question is yes; unknown when a
required answer is unknown and none is no; no otherwise.

## Plan

1. Study how the dichotomy was run before, on problems of this shape,
   under `../STUDY.md`, producing
   `study/split-structure-from-randomness.md`. Settle before step 2:
   the bias statistics and piece systems already spent, with the
   exponent each reached; the dominant loss in each ledger in print;
   whether a route stopped at one obstruction, for step 6. Output: the
   ceiling and statistics spent, to step 2; the stopped route, to step
   6.
2. Write the dual notion. Dispatch
   iterate-a-structure-versus-randomness-increment, steps 1 and 2: the
   increment quantity, density on a piece or a hybrid with size, and the
   dichotomy. Output: the dichotomy lemma and its increment quantity.
3. Close the pieces. Dispatch
   iterate-a-structure-versus-randomness-increment, step 3; for pieces
   only nearly closed, strengthen-the-target: approximate pieces or
   configuration surviving the operations, with its deduction to the
   original. Output: closed pieces and the statement to prove.
4. Choose the functional. Question 6 yes: dispatch
   measure-size-by-entropy, steps 1 and 2; output: the entropic
   functional. Else question 7 yes: dispatch
   bound-failure-by-random-restriction-and-encoding; output: its failure
   bound for the random half, step 2's increment quantity for the
   structured half. Neither: no entry; output: that quantity.
5. Iterate and measure. Dispatch
   iterate-a-structure-versus-randomness-increment, steps 4 and 5, on
   step 4's functional; after an entropic run, measure-size-by-entropy,
   step 4, converts to sets once. Output: the density bound, exponent,
   and ledger (gain, loss, endgame), to step 6 given a stopped route,
   else to the next strategy or the cash-out.
6. Revive. Dispatch revive-the-abandoned-route on that route; step 4's
   functional is the new framework. Output: a proof along the old route
   inside the new framework, the better bound and ledger, to the next
   strategy or the cash-out.

## Failure signal

The strategy ends, verdict no, when:

- Density loses control of piece size and no hybrid restores both: the
  record gains the lemma and the escaping quantity; question 4 becomes
  no.
- Loss per iteration matches the gain, a logarithmic saving: the record
  gains the ceiling and ledger.
- No approximate pieces survive, or the strengthening fails on a small
  instance: the record gains the counterexample; question 4 becomes no.
- The entropic conversion or the encoding loses the target dependence:
  the record gains the failed functional.
- Three moves without a higher exponent: the record gains the last ledger.

## Cash-out

From the forms in `../CASHOUT.md`: a raised exponent,
quantitative improvement (form 2); the dichotomy lemma or the
strengthening with its deduction, reduction (form 3); the increment
class's ceiling, barrier (form 4); the entropic inequality or encoding
lemma, new machinery (form 6); a counterexample to the strengthening
(form 5).

## Composes with

Precedes `prove-the-barrier-first`, enforced: every increment argument
shares the ledger's dominant loss, which that strategy axiomatises.
Before `ladder-the-parameter`, not enforced: the exponent is a rung.
Follows `reduce-and-translate` (the increment runs on translated pieces)
and `strengthen-and-generalise` (the removed feature is a rigid target).
Excludes nothing: a direction move follows any change.

## Common mistakes

- A proxy (statistic, one piece's count) replaces the target unrecorded.
  Check: the dichotomy's halves imply the claim; `quadruple.direction`
  updates at step 2.
- Statistic and pieces chosen by cost or fashion. Check: step 2 cites
  `shape.ambient_structure`; the study marks the route absent from print.
- The route dismissed on general bounds. Check: a no to question 3
  cites `shape.known_bounds` recording a random set avoiding the
  configuration.
- Iteration without a stall rule. Check: each move records density and
  loss; the third without a higher exponent ends the strategy.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/split-structure-from-randomness.md` is missing
  or empty (`../harness/README.md`).
