---
name: reduce-to-a-finite-computation
component: mode
description: Use when the statement finitises to instances a solver can refute or confirm.
entries: [reduce-to-finite-witnesses, certify-the-finite-residue-by-computation]
precedes: []
costs: [constructivity]
excludes: []
---

## What it moves

The mode: from existence to certificate.

## Precondition procedure

1. Does the statement reduce to finitely many instances? (from: shape.finite_certificates; required)
2. Is the smallest open parameter known? (from: shape.monotonicity; required)
3. Are finite certificates recorded for the instances? (from: shape.finite_certificates; optional)

## Plan

Encode the instances; run with proof logging; verify each certificate.

## Failure signal

The instance count grows past what a solver finishes.

## Cash-out

Counterexample or computational evidence.

## Composes with

Follows ladder-the-parameter.

## Common mistakes

Trusting a solver without an independent checker.
