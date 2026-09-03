---
name: embed-the-object-in-a-family-and-move-along-it
move_class: reformulate
---

## Trigger

- The statement is about a rigid object with no continuous parameter attached to it: a representation with finite image, a function fixed by finitely many coefficients under a normalisation, a finite object with no geometric origin given in advance. The known methods have nothing to vary.
- A family exists in which the object sits as one member: a one-parameter deformation governed by an evolution equation, a family indexed by the primes in which each member is the reduction of one characteristic-zero object, a lift from a residual object to a deformation space. A known preservation or lifting statement tracks the property of interest along the family.
- At another member of the family the property is known, or the parameter that measures difficulty (a weight, a level, a coefficient index) is smaller.

## Action

1. Prove that the object has a lift into the family: run a lifting theorem backwards, using a dimension bound on the deformation space to produce a characteristic-zero point whose reduction is the object; or write the evolution equation with the object as its terminal data.
2. Move along the family to a member where the property is known or the difficulty parameter is smaller, and prove the property there: by the literature, by a direct nonexistence theorem for the base cases, or by the standard tools at the smaller parameter.
3. Come back: transport the property along the family by the preservation or lifting statement, choosing the path (an auxiliary prime, a time interval) by an elementary estimate on the distribution of the parameter, so that only transport theorems already proved are invoked.
4. Organise the whole as an induction on the difficulty parameter, with a level-raising or level-lowering step that moves the object into a subclass with rigid local structure where the transport theorems apply, and with base cases settled directly.

## Output form

The property for the original object as the endpoint of a chain of transports along the family; or, when one transport step is missing, the reduction "the property at one member implies the property at another".

## Failure signal

No lift exists (the deformation space is too small, or the object is not the reduction of any member of the family); the transport theorem needs a local hypothesis (a ramification condition, a chain condition) that every path to the known member violates; or the evolution does not preserve the constraint that defines the object.

## Typical cash-out

Reduction or equivalence (the transport statement); conditional or special-case result (the property at every member reachable by known transports).
