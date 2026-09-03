---
name: test-strengthenings-by-counterexample
move_class: construct
---

## Trigger

- The attack has produced, or the literature contains, a stronger, relaxed, translated, or neighbouring statement that would imply the target or is implied by it: a version that drops a monotonicity or a growth hypothesis; one enclosing structure in place of a covering by many; a weight translated by a constant; a constant-factor version; a monotonicity along a parameter.
- Small instances of the neighbouring statement are computable.

## Action

1. List the natural strengthenings and relaxations of the target, each with the hypothesis it drops or the parameter it changes.
2. For each, search small instances for a counterexample, by hand or by solver; for a statement about the solutions of an equation, reduce the equation to a finite-dimensional function of the candidate counterexample's parameters (a centre and a scale) and perturb a strict local extremum of that function into a solution.
3. When one is found, record which hypothesis it exploits. This fixes the boundary of what remains open and names the hypothesis that any proof must use.
4. Publish the counterexample when it refutes a statement that had been conjectured or that was in use as a route.

## Output form

A counterexample to a neighbouring statement, and a sharpened boundary for the target.

## Failure signal

No counterexample appears in the computable range and the strengthening is as hard as the target (then it is a target, not a test); or the counterexample exploits a degenerate feature that the target's hypotheses already exclude.

## Typical cash-out

Counterexample or computational evidence; problem paper (the corrected conjecture).
