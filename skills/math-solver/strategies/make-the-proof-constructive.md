---
name: make-the-proof-constructive
component: mode
description: Use when the mode accepts an explicit object, determined by partial approximations with common extensions or reachable from a seed by an amplifying operation the ambient structure supplies
entries: [pin-the-extremal-candidate-first, seed-and-amplify, enumerate-small-cases-to-locate-the-threshold, adjoin-the-wanted-object-by-generic-approximation, iterate-the-construction-and-bookkeep-every-candidate, vary-the-side-parameter-the-construction-fixed]
precedes: [reduce-to-a-finite-computation, verify-formally-with-lean4]
excludes: []
costs: [constructivity, bound_quality, axioms, object, obligations]
---

## What it moves

The mode. Before: existence, computation, or undecided. After:
construction, a written search procedure that produces the object.
Construction has already moved, certificate is final: question 1 says
no to both. The branch's first move carries the mode move
(`problem_changed` true); `quadruple.mode` then reads construction.

Bound quality is paid at steps 2 and 3: the value an explicit family
attains can sit below what a non-constructive existence argument gives.
Constructivity is paid at steps 4 and 5, where existence holds in a model
and no example is named. Axioms are paid there too, where the object lives
under a forcing axiom, so the record holds a consistency statement, not a
theorem of the base theory. The object cost is paid at steps 4 to 6:
existence in one model, or the target beside a fixed side quantity,
replaces the claim as posed. Obligations are paid at steps 3 and 5: the
amplification lemma and the preservation class are statements the record
still owes.

## Precondition procedure

1. Is the mode existence, computation, or undecided? (from: quadruple.mode; required)
2. Are the objects determined by partial approximations with common extensions, or reachable from a seed by an amplifying operation? (from: shape.objects; required)
3. Does the ambient structure supply that operation (amalgamation, product, gluing, recursion)? (from: shape.ambient_structure; required)
4. Does the configuration name what the operation must preserve: a forbidden configuration, a conserved quantity, or a chain or closure condition? (from: shape.configuration; required)
5. Is there a smallest parameter at which a seed with positive excess can be searched? (from: shape.finite_certificates; optional)
6. Is existence undecided by the base theory, the difficulty starting at an uncountable cardinal? (from: shape.base_theory; optional)
7. Does the base theory record whether the construction stays inside it, and which hypotheses beyond it decide the statement? (from: shape.base_theory; required)

Verdict: yes when questions 1 to 4 and 7 are yes; unknown when one is
unknown and none is no; no otherwise. Question 5 selects the seed branch (steps
2 and 3), question 6 the approximation branch (steps 4 to 6), seed
first when both, step 1's route when neither.

## Plan

1. Study how the object was constructed before, on problems of this
   shape, under `../STUDY.md`, producing
   `study/make-the-proof-constructive.md`. Settle before step 2: the
   value to beat and the construction holding it; the seeds and
   amplifying operations tried, with the range searched; whether an
   approximation argument was carried through, and at which stage it
   stopped. Output: the constraints and the branch of the route in
   print: seed when it amplified, approximation when it adjoined.
2. Fix the value to beat (seed branch, `shape.target_quantity`
   extremal). Dispatches pin-the-extremal-candidate-first. Output: a
   lower-bound construction with its value, a conjectured constant, a
   rigidity assessment; the value to step 3.
3. Seed and amplify. Dispatches seed-and-amplify, with
   enumerate-small-cases-to-locate-the-threshold as one enumeration
   run: the amplification lemma; the smallest parameter carrying
   positive excess, over step 2's value when it ran, else by action 2;
   the run; an independent check; the amplified object. Output: the
   object or family with its quantity, to step 7.
4. Adjoin (approximation branch). Dispatches
   adjoin-the-wanted-object-by-generic-approximation. Output: the model
   or theorem under a forcing axiom, to step 7; its failure signal on
   new candidates, to step 5.
5. Iterate and bookkeep. Dispatches
   iterate-the-construction-and-bookkeep-every-candidate. Output: the
   model of the universal statement with its preservation class, to
   step 7; its failure signal on a side quantity a neighbour excludes,
   to step 6.
6. Vary the side quantity. Dispatches
   vary-the-side-parameter-the-construction-fixed. Output: the range of
   compatible side values, to step 7.
7. Hand over; no entry, no move: the last move's `output` carries the
   object, procedure, and verification, to the next strategy or the
   cash-out.

## Failure signal

At most six moves: one at steps 2, 4, 5, and 6, two at step 3, whose
run stops at the first parameter the solver cannot settle. A firing
ends the strategy (the third, with question 6 yes, only the seed
branch); the harness then sets the verdict to no:

- Step 1 finds no branch: neither question 5 nor 6 is yes, no route in
  print. The record gains the study.
- The operation breaks what question 4 names, or two conditions have no
  common extension (question 2 becomes no). The record gains the
  obstruction.
- No seed with positive excess, or the gain tends to zero under
  iteration: question 5 becomes no; the record gains the searched
  range.
- The cardinal collapses at a limit, or every side-quantity variant
  breaks the target at one lemma. The record gains the failing stage or
  lemma, `prove-the-barrier-first`'s input.

## Cash-out

From the forms in `../CASHOUT.md`: a counterexample (form
5, standalone) or a lower bound (form 2); the search procedure with its
proof, an algorithm; a model or theorem under a forcing
axiom, conditional (form 1); the preservation class, new machinery
(form 6); a searched range without a seed, computational evidence (form
5).

## Composes with

Follows `attack-the-negative-side` (the counterexample),
`ladder-the-parameter` (the ideal value attained),
`work-conditionally-then-discharge` (the axiom discharged). Precedes
`reduce-to-a-finite-computation` (the next seed search) and
`verify-formally-with-lean4` (the lemma chain). Excludes nothing: a
mode move follows any other change.

## Common mistakes

- A proxy object, not what the claim quantifies. Check: step 7 reads
  the object against the claim quantifier by quantifier; a substitution
  is a statement move.
- The branch chosen by cost. Check: the journal cites question 5's or
  6's field, or the study's route.
- The seed search before the amplification lemma. Check: the lemma
  precedes the run in the record.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/make-the-proof-constructive.md` is missing
  or empty (`../harness/README.md`).
