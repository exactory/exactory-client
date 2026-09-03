---
name: choose-the-auxiliary-weight-or-certificate
move_class: bound
costs: [bound_quality, obligations]
---

## Trigger

- The known bound is obtained by summing or integrating against an auxiliary nonnegative object whose choice is free subject to sign or positivity constraints: sieve weights, a test function with a sign-constrained transform, a measure on the surviving set, time-dependent weights along a flow, a signing of an adjacency matrix, a dual solution of a linear program.
- The standard choice is uniform or a fixed ansatz, and the bound it yields falls short of the target by a factor that depends on the choice and not on the objects.

## Action

1. Write the bound as a functional of the auxiliary object with the constraints made explicit.
2. Enlarge the family: let the weight depend on one more coordinate; let it be adaptive, depending only on what earlier stages have revealed; let it concentrate on the surviving set; let it vary with a time parameter so that the target quantity becomes monotone along the flow.
3. Pose the choice as an optimisation (maximise a ratio of quadratic forms, minimise the largest root of an expected polynomial, find a function with prescribed zeros and sign pattern) and pass it to optimise-the-certificate-family-numerically.
4. Where the problem itself supplies a natural weight (a multiplicative function attached to the objects), test it first against the known counterexamples to the unweighted statement.
5. Prove the optimiser's bound with an explicit constant.

## Output form

A bound of the form: target quantity at most the functional evaluated at the chosen auxiliary object, with the object exhibited.

## Failure signal

The optimum over the enlarged family saturates at a value short of the target (the family's ceiling; pass to axiomatise-the-method-and-build-a-near-miss); or every admissible object loses a factor depending on the ambient dimension or on the number of objects, which the target cannot afford; or the natural weight defeats the known counterexamples but the weighted statement is still not provable.

## Typical cash-out

Quantitative improvement; new machinery (the weight family); barrier (the family's ceiling).
