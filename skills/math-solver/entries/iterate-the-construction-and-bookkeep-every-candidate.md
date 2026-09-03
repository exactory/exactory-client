---
name: iterate-the-construction-and-bookkeep-every-candidate
move_class: construct
costs: [axioms, constructivity, object, obligations]
---

## Trigger

- The statement is universal over a class of candidates of uncountable size: every uncountable set of reals, every tree or order of a given kind, every set of ordinals below a fixed cardinal. A single construction step (adjoin-the-wanted-object-by-generic-approximation) defeats or handles only the candidates present before the step, and the step produces new candidates of the same class.
- Every candidate is small relative to the length of the intended construction: it is determined by countably many parameters, or by fewer parameters than the cardinal the construction runs to, so that a cofinality argument places each candidate at an intermediate stage.

## Action

1. Choose the step forcing for one candidate (a forcing tailored to the property, or the candidate itself used as a forcing whose generic destroys it) and the support of the iteration (finite or countable) according to which chain or closure condition must survive at limit stages. Prove the preservation lemma: the chain condition holds at limits, or a fusion lemma shows the cardinal survives, and the damage done to a candidate at its stage persists to the end.
2. Prepare the ground so that every candidate is small: a preliminary collapse that makes every set of the relevant size countable at some stage, so that each candidate lives in a small intermediate model over which the rest of the iteration is homogeneous.
3. Bookkeep: fix an enumeration of all names for candidates that will ever appear, schedule each name at a later stage, and prove by the cofinality argument that every candidate of the final model appears at some intermediate stage and is handled after it.
4. When two targets need incompatible iterations, choose the iteration itself generically: a preparatory forcing adds an interleaving of variants of the two step forcings with partial limits, and the preservation lemma is proved for the interleaving.

## Output form

A model of the universal statement: its consistency relative to the base theory plus whatever the ground preparation cost, with the preservation property of the step forcings isolated as a class.

## Failure signal

The chain or closure condition fails at a limit stage and the cardinal collapses; a candidate has parameters that are not small, so no stage captures it; a later stage undoes the damage of an earlier one (the preservation lemma fails, and the property must be added to the inductive state); or the iteration forces a value of a side quantity (the size of the continuum, a companion hypothesis) that the statement or a neighbouring result excludes (hand it to vary-the-side-parameter-the-construction-fixed).

## Typical cash-out

Conditional or special-case result (the consistency of the target); new machinery (the class of step forcings with the preservation property, and the iteration scheme).
