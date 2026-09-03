---
name: ladder-the-parameter
component: statement
description: Use when a continuous progress parameter exists between the trivial and the ideal value.
entries: [embed-the-object-in-a-family-and-move-along-it]
precedes: [reduce-to-a-finite-computation]
costs: [bound_quality]
excludes: []
---

## What it moves

The statement: from the ideal value to the next rung of the parameter.

## Precondition procedure

1. Is the target quantity a parameter with a trivial and an ideal value? (from: shape.target_quantity; required)
2. Are bounds known on both sides? (from: shape.known_bounds; required)
3. Is a free parameter recorded over which uniformity is the difficulty? (from: shape.uniformity_parameter; optional)

## Plan

Move one rung; hand the new bound to the next step.

## Failure signal

No rung above the known bound is reachable in two moves.

## Cash-out

Quantitative improvement.

## Composes with

Precedes reduce-to-a-finite-computation.

## Common mistakes

Choosing a rung the known method already reaches.
