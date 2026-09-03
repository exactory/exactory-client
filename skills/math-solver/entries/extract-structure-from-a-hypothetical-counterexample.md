---
name: extract-structure-from-a-hypothetical-counterexample
move_class: reduce
---

## Trigger

- The statement is universal ("no solution exists", "every object has dimension at least d", "every colouring contains the configuration"), and a counterexample would be an explicit object with parameters.
- The known method fails to exclude counterexamples but yields necessary conditions on them: a divisibility, a congruence on the parameters, a pseudorandomness or self-similarity property, a resemblance to a known near-miss shape, a concentration profile at a point when compactness fails at a critical exponent, an attachment pattern of countable pieces along an uncountable index.

## Action

1. Assume a counterexample exists with the worst possible parameters (the minimal dimension, the exact conjectured exponent).
2. From each available argument, derive a necessary condition: what the counterexample's factors must divide, which residue classes its parameters occupy, how its density is distributed across scales, at which points and at which rate it concentrates and which model profile the concentration must resemble, how its countable pieces attach along the index, which known structures it must resemble.
3. Accumulate the conditions until they contradict a known theorem or an integral identity whose boundary term is controlled by an invariant of the ambient space, force the object into a class already understood, or leave a finite range of parameters.
4. Hand a finite range to enumerate-small-cases-to-locate-the-threshold; hand a structured class to prove-the-special-case-where-the-method-is-stronger; hand a near-miss class to axiomatise-the-method-and-build-a-near-miss.

## Output form

A list of necessary conditions on any counterexample, or a reduction of the statement to a finite range of parameters or to a structured class.

## Failure signal

Every condition is satisfied by a known object that is not a counterexample (a near miss that passes every test), so these methods yield no further condition; or the finite range left is far beyond computational reach.

## Typical cash-out

Reduction or equivalence; quantitative improvement (any counterexample has parameters beyond an explicit threshold); conditional result.
