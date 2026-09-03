---
name: reduce-dependent-events-to-pairwise-overlaps
move_class: reduce
---

## Trigger

- The statement asserts that a union of many events (short intervals around fractions, congruence classes, random restrictions) covers, or has full measure, or leaves a set of positive density.
- The events are not independent, and their pairwise dependence is governed by an arithmetic or combinatorial parameter: a greatest common divisor, shared prime factors, the overlap of two sets.
- The first-moment computation (the sum of the measures) is known, and it diverges or exceeds one.

## Action

1. Reduce the covering or full-measure statement to a second-moment bound: over blocks where the first moment is of order one, the sum of pairwise overlaps is at most a constant; or, for a dependency-lemma argument, to a bound on how many events each event depends on.
2. Express each pairwise overlap in terms of the arithmetic parameter.
3. Identify the enemy: a support whose members share the parameter unusually often. State the model problem this leaves (isolate-a-model-problem).
4. If a pointwise overlap bound fails, average the overlap bound over the support with a weight the problem supplies (choose-the-auxiliary-weight-or-certificate), and apply the dependency lemma in a relative form when the next stage needs regularity from this one.

## Output form

A reduction of the measure or covering statement to an estimate on a weighted sum of pairwise parameters over the support.

## Failure signal

The pointwise overlap ratio has no uniform bound on the relevant pairs, and averaging over an arbitrary sparse support has no known technique; or the dependency graph is too dense for any dependency lemma.

## Typical cash-out

Reduction or equivalence; conditional result, under an extra-divergence or regularity hypothesis on the support.
