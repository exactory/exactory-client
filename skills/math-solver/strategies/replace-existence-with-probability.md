---
name: replace-existence-with-probability
component: mode
description: Use when the claim asserts existence, covering, or full measure over a large finite or measurable space, the recorded first moment exceeds one or diverges, and pairwise dependence is given by an arithmetic or combinatorial parameter or by a spread condition
entries: [strengthen-the-target, reduce-dependent-events-to-pairwise-overlaps, bound-failure-by-random-restriction-and-encoding, isolate-a-model-problem, choose-the-auxiliary-weight-or-certificate]
precedes: [solve-the-model-world-first, verify-formally-with-lean4]
excludes: []
costs: [implication, constructivity, bound_quality, object, obligations]
---

## What it moves

The mode. Before: existence or undecided; the claim needs an object, a
cover, or a set of full measure. After: existence, proved by fixing a
random object (a random subset with element probability p, or a random
point against the events) and showing failure has probability below
one. Statement, stage, and direction stay; the mode move is step 2's
`journal.jsonl` line with `problem_changed` true, after which
`quadruple.mode` in `problem.json` reads existence.

Implication is paid at step 2: a random object proves the existence claim
and cannot refute it. Constructivity is paid at step 4 or step 6: failure
has probability below one, so the record names no example. Bound quality is
paid at step 6 as well: the bound becomes the functional at the exhibited
weight, which the choice controls. Object is paid at steps 2 and 5: the
stronger claim and the model problem stand where the claim stood.
Obligations are paid at steps 3 to 6: the deduction, the overlap estimate,
the model statement, and the weight's bound are still to be proved.

## Precondition procedure

1. Do the quantifiers assert existence, covering, or full measure over a large finite or measurable space? (from: shape.quantifiers; required)
2. Do the known bounds record a first moment that exceeds one or diverges? (from: shape.known_bounds; required)
3. Does the ambient structure give the pairwise dependence by an arithmetic or combinatorial parameter, or by a spread condition on the family? (from: shape.ambient_structure; required)
4. Is the mode existence or undecided? (from: quadruple.mode; required)

Verdict: yes when all four are yes; unknown when one is unknown and
none is no; no otherwise. Question 3's field, not its answer, picks
the branch after step 2: a parameter to step 3, a spread condition to
step 4, both to step 1's constraints.

## Plan

1. Study how a random object was used before, on problems of this
   shape, under `../STUDY.md`, producing
   `study/replace-existence-with-probability.md`. Settle before step 2:
   the first moment already computed, and at which p; the overlap or
   encoding bound reached, and where it stayed above one; whether a
   weight or a model problem was already split off. Output: its
   constraints, and the branch when the field names both.
2. Restate. Write the stronger claim, that the random object at element
   probability p has the property with positive probability, deduce the
   original from it, and record the mode move. Dispatches
   strengthen-the-target. Output: the claim and its deduction, to step 3
   or 4, which read the first moment from `shape.known_bounds` and
   compute it when question 2 was unknown.
3. Reduce (parameter branch). Over blocks where the first moment is of
   order one, reduce the claim to a bound on the sum of pairwise
   overlaps, each in the parameter. Dispatches
   reduce-dependent-events-to-pairwise-overlaps, steps 1 and 2. Output:
   the reduction to the unweighted overlap sum, to step 5.
4. Encode (spread branch). Sample in rounds and count descriptions of
   failing samples against the samples, as entropy when loose.
   Dispatches bound-failure-by-random-restriction-and-encoding. Output:
   a bound on the failure probability in the spread parameter and the
   rounds: below one at step 2's p, the strategy's output, to the next
   strategy or the cash-out; otherwise the third failure signal.
5. Split off the enemy (parameter branch), the support sharing the
   parameter unusually often, and state the model problem it leaves.
   Dispatches reduce-dependent-events-to-pairwise-overlaps, step 3, then
   isolate-a-model-problem, steps 1 and 2. Output: the model
   statement, to `solve-the-model-world-first` when the walk steps
   into it; the enemy, to step 6.
6. Weight (parameter branch). Write step 3's overlap sum as a
   functional of a weight on the support, exhibit the weight the problem
   supplies (uniform when the pointwise bound holds), and bound the sum.
   Dispatches choose-the-auxiliary-weight-or-certificate. Output: the
   sum at most the functional at the exhibited weight; with step 3's
   reduction, failure with probability below one on the range where it
   holds, to the next strategy or the cash-out.

## Failure signal

The strategy ends when one fires; the harness sets its verdict to no:

- The first moment, computed at step 3 or 4 when `shape.known_bounds`
  left it unknown, is below one or converges. The record gains it;
  question 2 becomes no.
- The overlap ratio has no uniform bound and step 6 finds no weight.
  The record gains step 3's reduction, conditional on a regularity
  hypothesis on the support.
- Step 4's bound stays above one, or the spread condition has no
  analogue. The record gains the encoding lemma as far as it holds.

## Cash-out

From the forms in `../CASHOUT.md`: step 3's reduction
(form 3, reduction); the overlap sum bounded under a regularity
hypothesis (form 1, conditional); a sharpened range for p or the
parameter (form 2, quantitative improvement); the encoding lemma and
the weight family (form 6, new machinery).

## Composes with

Precedes `solve-the-model-world-first`: step 5's model problem is the
stage move it carries out and back. Typically follows
`split-structure-from-randomness`, which supplies question 3's spread
condition, and precedes `verify-formally-with-lean4`, whose input is
step 6's lemma chain. Excludes nothing: a mode move follows any change
of statement, stage, or direction.

## Common mistakes

- A first moment above one taken as the proof. Check: `journal.jsonl`
  holds step 4's or step 6's bound before a unit is declared.
- The expected count taken as the target. Check: step 2's deduction is
  written and `quadruple.statement` is unchanged.
- The structured support absorbed into the average. Check: step 5 names
  the enemy before step 6 weights it.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/replace-existence-with-probability.md` is missing
  or empty (`../harness/README.md`).
