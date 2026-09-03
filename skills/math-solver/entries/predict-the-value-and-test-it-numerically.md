---
name: predict-the-value-and-test-it-numerically
move_class: compute
---

## Trigger

- A heuristic predicts the target quantity: a local-independence or first-moment computation gives a density, a threshold, or a constant; or an intermediate inequality has a predicted range of validity.
- The predicted quantity, or the inequality, is computable to good precision for many instances.

## Action

1. Derive the prediction and state its assumptions: which events are treated as independent, which lower-order terms are dropped.
2. Compute the actual quantity for many instances, as an enumeration run when the instances are finite and exact and as a numerical optimisation run when the quantity is an optimum, and compare.
3. On a systematic discrepancy, locate the dependence the heuristic missed (a containment between the fields or events treated as independent, a correlation) and publish the corrected conjecture with its correction factor.
4. On exact agreement to many digits, conjecture exactness and extract the structural data the proof will need.
5. Search the literature for the numerically verified inequality; it may be a known theorem in a neighbouring field.
6. Test the intended proof route on its smallest instances by a counterexample search run against each lemma of the route before committing to it.

## Output form

A confirmed or corrected conjecture with numerical evidence, and the list of instances checked.

## Failure signal

The numerics are inconclusive across the computable range because the discrepancy lies within the error of the asymptotic regime; or the correction factor has no structural explanation; or the inequality holds numerically but no proof of it exists in any field.

## Typical cash-out

Counterexample or computational evidence; problem paper (the corrected conjecture).
