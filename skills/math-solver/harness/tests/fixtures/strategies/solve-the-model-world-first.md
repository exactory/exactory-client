---
name: solve-the-model-world-first
component: stage
description: Use when a structurally parallel simpler setting exists with the obstruction removed.
entries: [isolate-a-model-problem, carry-the-model-argument-back]
precedes: []
costs: [object]
excludes: []
---

## What it moves

The stage: from the original setting to the model setting.

## Precondition procedure

1. Does a model setting exist that keeps the constraint? (from: shape.ambient_structure; required)
2. Is the obstruction absent in the model? (from: shape.missing_input; required)
3. Does the ambient structure record a simpler model setting? (from: shape.ambient_structure; optional)

## Plan

Solve the model; carry the argument back.

## Failure signal

The model argument uses a feature the original lacks.

## Cash-out

Conditional or special-case result.

## Composes with

Follows nothing in particular.

## Common mistakes

Picking a model with a different obstruction.
