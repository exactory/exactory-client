---
name: attach-an-auxiliary-object-to-each-solution
move_class: reformulate
costs: [effectivity, obligations]
---

## Trigger

- The statement is about solutions directly (points, functions, colourings, integer tuples), and the direct invariants of a solution (its size, height, degree, number of factors) admit no bound by known methods.
- Each solution determines an algebraic object with richer structure: a factor of an algebraic expression, a unit in a ring, a module or a representation, a polynomial whose roots encode the solution, a signed matrix whose spectrum encodes the configuration.
- The auxiliary objects have a classification, finiteness, or spectral theory of their own.

## Action

1. Define the map from solutions to auxiliary objects, and prove that it is finite-to-one, or that the auxiliary object determines the solution up to a finite ambiguity.
2. Transfer the claim: finiteness or a bound for the solutions follows from finiteness or a bound for the auxiliary class.
3. Attack the auxiliary class with its own theory: a height that is invariant under the operations generating the class and comparable with the naive height; an explicit annihilator of a group; a spectral bound inherited by substructures; the expected polynomial of a random family, whose largest root controls some member.
4. When the first auxiliary object resists, swap it for a sibling with more tractable structure (a second recurrence sequence with simpler arithmetic, the unit factor instead of the quotient, a different signing) and repeat from step 1.

## Output form

A reduction of the original claim to a claim about the auxiliary class, and a statement of what remains to be proved there.

## Failure signal

The map is not finite-to-one and the ambiguity cannot be controlled; or the auxiliary class has no finiteness or bound theory, so the transferred claim is as hard as the original; or the invariant on the auxiliary class cannot be compared with the naive invariant of the solution.

## Typical cash-out

Reduction or equivalence; new machinery (the theory of the auxiliary class); conditional result, when the auxiliary claim is a known conjecture.
