---
name: isolate-a-model-problem
move_class: reformulate
---

## Trigger

- The statement lives in an ambient structure that carries more than one kind of structure at once (an interval of integers carrying both order and arithmetic; real space carrying both a metric and a measure; a nonlinear evolution carrying both a scaling symmetry and a conserved quantity), and the known methods get stuck on the interaction between the two.
- A simpler ambient structure exists that keeps the forbidden configuration or the constraint and drops one kind of structure: a vector space over a small finite field in place of the integers, a finite set of scales in place of a continuum, a finite graph or set system in place of a measure space, a system of ordinary differential equations indexed by scales in place of a partial differential equation.
- The statement can be posed verbatim in the simpler structure, and its truth value there is not already known to differ from the original.

## Action

1. Write the statement in the model setting, keeping the quantifier structure and the target quantity (an exponent, a density, a threshold).
2. List which invariants and symmetries survive the passage, and which tools become available only in the model (a group structure, a rank or dimension count, a finite case analysis).
3. Attack the model with those tools. Label each step of the resulting proof by whether it uses structure the original lacks.
4. If the model resists, add to it the one feature of the original that the model's counterexamples exploit (a weight attached to the objects, a cancellation condition), and attack again.
5. Hand the labelled proof to carry-the-model-argument-back.

## Output form

A statement in the model setting with the same shape as the original, together with a proof whose steps are labelled by their dependence on model-only structure.

## Failure signal

The model's answer is qualitatively different from the original (the model bound has a growth rate that the constructions in the original setting rule out); or the unweighted model is false while the original stands and no weight repairs it; or the method that solves the model uses one tool (a polynomial or rank count) for which the original setting has no counterpart, and the transfer attempt finds no substitute.

## Typical cash-out

Conditional or special-case result (the model theorem stands on its own); new machinery; barrier, when the model solution is proved not to transfer.
