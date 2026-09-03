---
name: reduce-to-finite-witnesses
move_class: reduce
---

## Trigger

- The statement quantifies over an infinite object (all points of a space, all positive integers, an infinite-dimensional algebra), but the property asserted is finitary: a colouring failure, a monochromatic configuration, a decomposition, each certified by finitely many elements.
- A compactness principle or a monotonicity in a parameter applies, so that the infinite statement holds if and only if a finite version holds for some (or for every) value of a finite parameter, with any constants independent of that parameter.

## Action

1. State the finite version explicitly, with the parameter that indexes it (number of vertices, the interval from 1 to n, the matrix dimension) and the constant that must not depend on it.
2. Prove the equivalence by compactness, by monotonicity, or by a limiting argument.
3. Decide which side has a finite certificate: a negative answer by one finite object (a graph, an unsatisfiable formula), a positive answer by an infinite family or a uniform construction.
4. Pass the finitely certifiable side to enumerate-small-cases-to-locate-the-threshold or to certify-the-finite-residue-by-computation. Pass the uniform side to the bounding entries, with the constant's independence from the parameter as the primary obstacle.

## Output form

A finite statement equivalent to the original, with a named parameter and a named uniform constant.

## Failure signal

The natural finite version has constants that grow with the parameter (a logarithm of the dimension, a factor depending on the number of vectors), so results on finite instances prove nothing about the infinite statement; or the finite certificate grows past what any tool can check before any pattern appears.

## Typical cash-out

Reduction or equivalence; computational evidence (the finite parameters checked).
