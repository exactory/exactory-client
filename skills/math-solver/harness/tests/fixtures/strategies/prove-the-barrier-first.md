---
name: prove-the-barrier-first
component: direction
description: Use when existing methods have failed uniformly and share an identifiable property.
entries: [axiomatise-the-method-and-build-a-near-miss]
precedes: []
costs: [implication]
excludes: []
---

## What it moves

The direction: from proving true to proving unreachable by the known class.

## Precondition procedure

1. Do the known bounds share a documented ceiling? (from: shape.known_bounds; required)
2. Is the shared property nameable? (from: shape.proof_shape; required)
3. Do the neighbours record a barrier for a neighbouring statement? (from: shape.neighbours; optional)

## Plan

Axiomatise the class; build the near miss.

## Failure signal

No near-miss object exists inside the class.

## Cash-out

Barrier.

## Composes with

Excluded by attack-the-negative-side.

## Common mistakes

Declaring a barrier without a near-miss object.
