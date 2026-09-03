---
name: axiomatise-the-method-and-build-a-near-miss
move_class: obstruct
costs: [axioms, constructivity, object, obligations]
---

## Trigger

- A method has produced a sequence of improvements with diminishing returns, or has stalled at a documented value.
- The method uses only some properties of the objects (intersection axioms for a family of thin sets, a class of nonnegative weights, quasirandomness counts, an abstract set-system structure, a restricted computational model) and not the full hypothesis.

## Action

1. Write down exactly the properties of the objects that every step of the method invokes.
2. Construct an object that satisfies those properties and violates the target bound (a near miss), or prove that the optimum over the method's admissible family equals the stalled value; when every attempt to remove a hypothesis has failed at the same point, convert that failure into a construction showing the hypothesis necessary.
3. State the barrier: no argument using only these properties passes the stalled value.
4. Name the property of the real objects that the near miss lacks. That property is what the next method must use.
5. Check whether the barrier is tight for a variant of the problem that the method naturally controls; if so, publish that as a sharpness result.

## Output form

A barrier theorem naming the method's admissible class and its ceiling, and a near-miss object.

## Failure signal

No object satisfying the axioms violates the bound (then the method may still work, and the attack should push it); or the axiomatisation is so narrow that the barrier excludes nothing anyone would try.

## Typical cash-out

Barrier; quantitative improvement, when pushing the method to its exact ceiling is itself new.
