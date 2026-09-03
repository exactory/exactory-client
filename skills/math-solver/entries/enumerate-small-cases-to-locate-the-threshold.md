---
name: enumerate-small-cases-to-locate-the-threshold
move_class: compute
costs: [bound_quality, object]
---

## Trigger

- The statement is monotone in a parameter; or an explicit bound has left a finite range of parameters to check; or the seed for an amplification must be found in the smallest admissible dimension.
- Each instance is finite and checkable: a colouring of an interval, a polytope with given parameters, a pair of exponents against a congruence criterion, a subset of a small vector space.
- A certificate for at least one side is finite: an unsatisfiable formula, an exhaustive classification.

## Action

1. Encode the instance into satisfiability, integer programming, or a custom enumeration, as an enumeration run with a trusted base small enough to read (an encoder of a few dozen lines).
2. Raise the parameter from the last known value; when each parameter's witness is derived from the previous parameter's witness, run the parameters as a certified witness chain. Record the first value where the answer changes, and the pattern of solutions below it.
3. Match the search paradigm to the instance (global splitting for hard combinatorial structure, local refutation for propagation-heavy instances), and tune the splitting heuristic to the statistical profile of the instance or to an expected density; a default heuristic can cost orders of magnitude.
4. Emit a checkable certificate for each instance and verify it with the enumeration run's independent checker.
5. Report exact values for the parameters settled and the threshold located.

## Output form

Exact values or a threshold for the small parameters; a seed object; a finite-range exclusion (no counterexample with parameter below an explicit value).

## Failure signal

The instance size grows past the solver's reach before any change or pattern appears; or the settled small cases decide nothing about the asymptotic quantity that is the target, because they fix building blocks and not the rate; or the certificate is too large to check independently.

## Typical cash-out

Counterexample or computational evidence; conditional or special-case result (the settled parameters); a single counterexample as a standalone unit.
