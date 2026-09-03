---
name: attack-the-negative-side
component: direction
description: Use when the space of counterexamples compresses to something searchable.
entries: [test-strengthenings-by-counterexample]
precedes: []
costs: []
excludes: [prove-the-barrier-first]
---

## What it moves

The direction: from proving true to proving false.

## Precondition procedure

1. Does the negative side have a finite certificate? (from: shape.finite_certificates; required)
2. Is the extremal candidate known? (from: shape.extremal_candidate; required)
3. Does the configuration record a candidate counterexample? (from: shape.configuration; optional)

## Plan

Search the compressed space; verify every hit.

## Failure signal

The searched range is exhausted with no hit.

## Cash-out

Counterexample or computational evidence.

## Composes with

Excludes prove-the-barrier-first: both spend the direction budget.

## Common mistakes

Searching an uncompressed space.
