---
name: diagonalise-against-every-candidate-under-a-guessing-principle
move_class: construct
---

## Trigger

- The statement is universal over uncountable objects (every uncountable set of reals has a covering property; every group of the first uncountable size with a homological property has a basis; every order with a chain condition is separable), the countable case holds, and a counterexample must be assembled from uncountably many countable pieces at once.
- Each witness that would show a candidate is not a counterexample (a sequence of covers, a homomorphism that splits the extension, a countable dense subset, an uncountable antichain) is determined by countably many parameters, so the witnesses can be listed in a sequence of the first uncountable length.
- A guessing principle is available as a hypothesis, or the problem list accepts results under one: a continuum hypothesis that enumerates the countable parameters, a constructibility principle, or a diamond-type principle that predicts each witness at a stationary set of stages.

## Action

1. List the witnesses in a sequence of length the first uncountable cardinal, so that each witness is guessed at some stage (under the continuum hypothesis, by enumeration; under a diamond-type principle, at a stationary set of stages).
2. Build the counterexample by transfinite recursion: the countable piece at a stage extends the pieces before it so that the witness guessed at that stage is defeated (the cover misses a point, the guessed homomorphism does not extend, the guessed countable set is not dense), while every countable piece keeps the local property the statement's hypothesis demands.
3. Verify at limit stages that the union of the pieces keeps the local property, and at the end that every witness was defeated at the stage where it was guessed.
4. Extract the exact principle the recursion used (which witnesses were guessed, at which stages, and what was needed of the guess) and restate the result as "the principle implies the negation of the statement".

## Output form

A counterexample under the guessing principle: the negation of the statement is consistent with the base theory relative to the principle, and the principle actually used is named.

## Failure signal

A witness is not determined by countably many parameters, so no sequence of the first uncountable length lists them all; the local property is lost at a limit stage; or the extension needed to defeat the guessed witness conflicts with the extension needed for an earlier one, so the pieces cannot both be kept.

## Typical cash-out

Counterexample under a hypothesis (conditional or special-case result); reduction or equivalence (the extracted principle).
