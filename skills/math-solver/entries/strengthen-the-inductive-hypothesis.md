---
name: strengthen-the-inductive-hypothesis
move_class: bound
---

## Trigger

- The natural argument is an induction or iteration on a discrete parameter, on a scale, or on a stage of a sieve ordered by the size of the prime factors.
- Either the induction loses a constant factor per step and each step is tight, so the loss compounds to the whole gap; or the inductive hypothesis is not preserved by the passage to the next step, because truncation and rescaling destroy it.

## Action

1. Write out what the step at stage k receives and what it delivers, and name the quantity that is lost or the property that is not preserved.
2. Add to the inductive state a second tracked object that the next step needs: a second set whose density is monitored, a regularity or non-concentration condition, a robustness parameter.
3. Add a repair step: when the tracked quantity drops, an operation restores it before the next step (a density boost, a relative application of the dependency lemma, a change of measure that concentrates on the surviving set).
4. Prove the step with an output strictly stronger than its input, so the induction closes. When the hypothesis must survive truncation and rescaling, replace it by the non-concentration form that does.
5. Handle the base scale separately.

## Output form

An inductive lemma whose hypothesis and conclusion are the same strengthened statement at two consecutive stages, together with a base case.

## Failure signal

The repair step costs more than the loss it repairs; or the losses compound geometrically (a factor lambda becomes lambda to the power 2 to the N after N steps); or the strengthened hypothesis fails at the base scale; or the strengthened statement is false in the ambient dimension because a known object satisfies the hypothesis and violates the conclusion.

## Typical cash-out

Quantitative improvement; new machinery (the multi-scale structure theorems).
