---
name: reduce-to-one-generating-instance
move_class: reduce
---

## Trigger

- The statement is universal over a large class (all listable sets, all matrices of a given kind, all packings, all pairs of parameters, all primes), and the class has closure operations (composition, products, taking substructures, tensoring, localisation) under which the property propagates.
- A single concrete instance, or a single one-parameter family, generates the class under those operations; or the extreme value of the parameter is provably the whole problem.

## Action

1. List the closure operations and prove that the property is preserved by each.
2. Identify the generating instance: one relation whose definability forces all others; one matrix class whose decomposition forces all decompositions; one auxiliary function with a prescribed root set; one parameter value at which the maximum is attained; one prime at which the local statement is hardest.
3. State the equivalence: the target holds if and only if it holds for the generating instance.
4. Attack the instance. It is now a construction problem with fixed data.

## Output form

An equivalence between the original statement and one concrete construction problem or one parameter value.

## Failure signal

The closure operations lose a constant at each application and the number of applications needed grows without limit; or the class has infinitely many independent generators; or the generating instance is exactly as hard as the class.

## Typical cash-out

Reduction or equivalence; conditional result (the target under the generating instance).
