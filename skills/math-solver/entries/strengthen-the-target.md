---
name: strengthen-the-target
move_class: reformulate
---

## Trigger

- The natural proof shape for the statement is an induction, an iteration, or a chain of inequalities, and the target configuration or the hypothesis is rigid: it is not preserved when the argument passes to a subfamily, a link, a smaller scale, or a truncation.
- A stronger statement can be written that implies the original by a short deduction, whose target is robust (probabilistic, weighted, linear in a transformed quantity, or closed under the passage operations), and that is not already known to be false.
- The statement has resisted attack for long enough that "the statement is too weak to induct on" is a plausible diagnosis.

## Action

1. List the operations the argument would need: restriction to a subfamily, passage to a link, rescaling, truncation, a transform.
2. For each operation, write what the hypothesis and the conclusion become, and mark the ones that do not survive.
3. Write a stronger statement whose hypothesis and conclusion both survive every listed operation: replace an exact configuration by an approximate one that occurs with positive probability; replace a nonlinear functional by a linear one of a transformed object; replace a hypothesis by a non-concentration condition that truncation preserves; state the claim over the widest natural class of objects instead of the class in the problem.
4. Prove that the stronger statement implies the original. The deduction must be short; if it is not, the strengthening is wrong.
5. Run test-strengthenings-by-counterexample on the stronger statement before investing in its proof.
6. Attack the stronger statement. The induction or iteration that failed on the original should now close.

## Output form

A stronger statement with a robust target, and a proof that it implies the original.

## Failure signal

The stronger statement is false (a counterexample appears among small instances); or the deduction to the original costs a factor that destroys the target growth rate; or the strengthened hypothesis still fails to survive one of the listed operations.

## Typical cash-out

Reduction or equivalence (the implication); counterexample (to the strengthening); problem paper (the strengthened conjecture with its evidence).
