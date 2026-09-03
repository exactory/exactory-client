---
name: make-the-bound-explicit-then-attack-the-lossiest-step
move_class: bound
costs: []
---

## Trigger

- A qualitative result stands (finiteness, existence of a constant, an o(1) saving, a bound with unspecified dependence on a parameter), and its proof is a chain of steps whose losses can be located.
- The community's measure of progress on this problem is the constant, the exponent, or the dependence on a parameter.

## Action

1. Trace the constant through every step of the proof and record the loss at each.
2. Rank the steps by loss and take the lossiest.
3. Replace it by a sharper special-case estimate, by a different argument for the same lemma, or by a modification that removes a parasitic dependence (a lemma that carried a factor of the ground-set size where only the set's own size was needed).
4. Restate the bound with explicit constants and exponents, and state which step now dominates.
5. When the lossiest step resists, test whether it is the method's ceiling (axiomatise-the-method-and-build-a-near-miss).

## Output form

A bound with an explicit constant or exponent, together with a ledger of losses by step.

## Failure signal

The lossiest step is provably tight for the method (a near miss saturates it); or every improvement of one step is cancelled by a compensating loss in another; or the explicit bound leaves a finite range far beyond computational reach and that range is the whole remaining problem.

## Typical cash-out

Quantitative improvement.
