---
name: reduce-existence-to-one-algebraic-obstruction
move_class: reduce
costs: [constructivity, obligations]
---

## Trigger

- The statement asserts the existence or nonexistence of a geometric or topological object (a map between spheres with a given invariant, a product structure on a vector space, a metric of constant curvature in a conformal class, a geometric object with restricted ramification) for every value of a dimension-like parameter, and existence is known for a short list of small values or for a model space.
- By a known chain of implications, existence is equivalent to a single quantity taking a specific value: an invariant of an auxiliary space with very little cohomology, or the infimum of a functional compared with its value on the model space.
- Invariants that detect the quantity in some range of the parameter exist (an operation on cohomology, a characteristic class with a divisibility property, a local expansion of the functional at a point), and at least one is known to be blind for some values.

## Action

1. Write the equivalence: the object exists if and only if the obstruction quantity vanishes (or the inequality is strict). Identify the auxiliary space on which the obstruction lives and what makes it small: the vanishing of intermediate cohomology, the invariance of the functional under the model space's symmetries.
2. Detect with the coarsest invariant: an identity among operations that kills every decomposable operation on the auxiliary space, a divisibility of a characteristic class, a local test function whose expansion reads a curvature term at a point. Record which values of the parameter it decides and which it leaves.
3. For the values it leaves, replace the invariant by a finer one that sees more: a higher-order operation indexed by the relations among the first-order ones, with the relations computed in the degrees needed and any undetermined coefficient of its universal formula fixed by evaluating on a test space where every term is computable; the same operation in a richer cohomology theory where a commutation relation holds; a global invariant of the space when the local one vanishes identically.
4. Reduce the residual cases to an elementary statement (a divisibility among integers, the sign of one constant) and prove it directly; settle the base cases in the other direction by a nonexistence theorem for the auxiliary objects.

## Output form

Nonexistence for every value of the parameter outside the known list (or existence for every value), with the obstruction and the detecting invariant named, and the equivalence itself as a separate statement.

## Failure signal

The finer invariant also vanishes on the auxiliary space (the space is too small for it to see anything); the residual elementary statement is false at a value where the object is known not to exist (the invariant is not sharp); or the chain of implications from existence to the obstruction loses a case (the obstruction vanishes without the object existing).

## Typical cash-out

Reduction or equivalence (existence if and only if the obstruction vanishes); conditional or special-case result (the parameter values decided); new machinery (the finer invariant).
