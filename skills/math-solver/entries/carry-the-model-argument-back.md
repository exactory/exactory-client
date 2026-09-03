---
name: carry-the-model-argument-back
move_class: transfer
---

## Trigger

- isolate-a-model-problem, or the literature, has solved the model version, and the model proof's steps are labelled by whether they use model-only structure.
- The original setting has a substitute for at least some of the model-only steps: an approximate subgroup for a subspace, a non-concentration condition for a finite-field count, a spectral structure that can be converted into structure of the set.

## Action

1. For each model-only step, find a substitute in the original setting and record the loss it introduces.
2. When a step has no substitute (a polynomial or rank count with no counterpart), solve the model again by a route that avoids that tool. The second route is the one that transfers.
3. Add the conversion step that the original needs and the model did not: turn spectral or transform-side structure into structure of the set itself.
4. Re-run the argument in the original setting and compare the exponent with the model's.

## Output form

A proof in the original setting with the model's shape, and an exponent comparable with the model's.

## Failure signal

A model-only step has no substitute and every alternative proof of the model uses the same step; or the substitutes lose so much that the transferred bound is no better than the previous record in the original setting.

## Typical cash-out

Quantitative improvement; new machinery (the substitutes); barrier (the non-transferability, when proved).
