---
name: adjoin-the-wanted-object-by-generic-approximation
move_class: construct
costs: [axioms, constructivity, object]
---

## Trigger

- The statement asserts, or its negation asserts, the existence of one infinite object: an uncountable set with a covering or measure property, an uncountable tree or order with a chain condition, a homomorphism splitting an extension, a single real avoiding every set of a ground class. The countable case is settled, and the base theory the problem is posed in is not known to decide the existence.
- The object is determined by its finite or countable partial approximations (finite partial functions, countable initial segments, closed sets of positive measure, trees of a fixed kind), and two approximations that meet different requirements have a common extension.
- Each requirement the object must meet (a value at one point, an extension defeating one candidate obstruction, avoidance of one set) is met by every sufficiently extended approximation, and the requirements number at most the cardinal at which the problem lives.

## Action

1. Define the approximations as conditions ordered by extension, and prove the amalgamation: any two conditions meeting different requirements extend to a common one.
2. Prove the chain condition or the closure property the target needs, so that the cardinal indexing the problem survives in the extension and the side hypotheses the statement uses are preserved.
3. For each requirement, prove that the conditions meeting it are dense. Obtain a filter meeting every requirement: a generic filter over the ground model, or, when the number of dense sets is below the bound of an available forcing axiom, a filter supplied by that axiom inside the ground universe.
4. Read the object off the filter, and verify the target property against every candidate of the ground model: every ground-model set of the kind in question is covered, split, or avoided by the new object.

## Output form

A model of the base theory, or a theorem under a forcing axiom, in which the object exists: the consistency of the existential statement, or of the negation of the universal one, relative to the base theory or to the axiom used.

## Failure signal

Two conditions meeting different requirements cannot be amalgamated (the requirements conflict, so the poset is not directed); the chain condition fails and the cardinal indexing the problem collapses in the extension; or the object handles only the candidates present in the ground model, and the extension contains new candidates of the same kind (hand the construction to iterate-the-construction-and-bookkeep-every-candidate).

## Typical cash-out

Conditional or special-case result (consistency relative to the base theory or to a forcing axiom); counterexample under a hypothesis.
