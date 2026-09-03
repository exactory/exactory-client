---
name: import-the-engine-from-an-adjacent-problem
move_class: transfer
costs: [bound_quality, effectivity, obligations]
---

## Trigger

- The attack's current bottleneck can be stated as a lemma of a recognisable shape: an equidistribution estimate on average, a sign-constrained auxiliary function, an incidence bound, a spread or anti-concentration statement, a barrier-function control of roots, a partition of space by a polynomial, a divisibility of characteristic classes from a periodicity theorem, a positivity theorem for a global invariant, a lifting theorem for one local hypothesis.
- A problem in an adjacent field has recently been moved by a lemma of that shape, and its hypothesis matches the bottleneck's hypothesis, or the dual of its relaxation.

## Action

1. State the bottleneck as a lemma with hypothesis and conclusion, stripped of the problem's vocabulary.
2. Search the adjacent fields for a lemma of that shape. Check its hypothesis against the bottleneck's, including through the dual of a relaxation (relax-to-the-averaged-or-fractional-version).
3. Import it. Where it was proved for a neighbouring conjecture, solve that neighbour first and carry its technical device back.
4. Combine the imported engine with the existing machinery and measure the exponent reached; it often lands exactly at the method's natural threshold.

## Output form

A proof of the bottleneck lemma by an imported argument, and the resulting bound.

## Failure signal

The imported lemma loses a factor depending on the ambient size (a logarithm of the dimension, the number of objects) that the target cannot afford; or its hypothesis has no analogue in the target (the dual object does not exist for the integral version); or the imported engine's restrictions on the parameter are incompatible with the rest of the argument.

## Typical cash-out

Quantitative improvement; new machinery; conditional result, when the import needs an extra hypothesis.
