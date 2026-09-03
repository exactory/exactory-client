---
name: prove-the-special-case-where-the-method-is-stronger
move_class: reduce
---

## Trigger

- The statement has an axis along which the known method's loss varies: symmetric against asymmetric parameters, one exponent range against another, a structured subfamily (objects with a fixed number of prime factors, self-similar sets, a restricted computational model, an analogue over a function field, definable objects against all objects, the regime where an invariant lies below a threshold, a subclass with rigid local structure) against the general case.
- On one side of the axis the method's loss vanishes, or a hypothesis it needs becomes a theorem.
- A route from the special case back to the general one is visible: a symmetrisation, an induction along the axis, a finite ambiguity, a worst-case argument that forces the structure.

## Action

1. Name the axis and the subclass explicitly, and check that the subclass is not already settled in the literature.
2. Run the method on the subclass to completion and state the result with its exact hypothesis.
3. Prove the bootstrap: show that a counterexample to the general statement can be symmetrised, rescaled, or restricted into a counterexample in the subclass; that the general statement follows by induction along the axis; that passing to an inner model in which the subclass is everything keeps enough of the ambient theory; or that a level-raising step moves every object into the subclass.
4. If the bootstrap fails, the special case stands as a result on its own, with the bootstrap gap recorded.

## Output form

A theorem on the subclass, with the bootstrap either proved or recorded as the remaining gap.

## Failure signal

The subclass result is already known; or the bootstrap costs a factor that returns the loss the special case removed; or the general case's extremal objects do not lie in or near the subclass, so a worst-case counterexample can avoid the structure.

## Typical cash-out

Conditional or special-case result.
