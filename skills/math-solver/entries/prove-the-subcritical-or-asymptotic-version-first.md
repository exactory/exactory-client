---
name: prove-the-subcritical-or-asymptotic-version-first
move_class: reduce
---

## Trigger

- The target quantity is an exact value of a parameter of the statement itself, and the statement is critical at that value: an exponent at which a compactness or embedding property fails, a bound whose conjectured constant is attained by several extremal families of different structure, an exact count with no error term.
- The same statement at a less demanding value of that parameter (a subcritical exponent, the bound with a lower-order error term, an asymptotic version) is within reach of the standard tools, and the literature states no result at the critical value.
- The difficulty is the exactness of the target value, not uniformity over a free parameter (the latter is relax-to-the-averaged-or-fractional-version).

## Action

1. Write the statement at the less demanding parameter value, and prove it by the standard tools: minimise the functional at a subcritical exponent; build the object by an iterative semi-random procedure that reaches the constant up to a lower-order term.
2. State exactly what the passage to the critical value needs: a uniform bound on the subcritical solutions as the exponent rises; a stability estimate giving slack away from the extremal families; the removal of an error term.
3. Locate the regime where the passage statement holds (below a threshold of an invariant; away from the extremal families) and prove it there. Hand that regime to prove-the-special-case-where-the-method-is-stronger, and the remaining regime to the entry whose trigger the obstruction matches.
4. Combine: the critical statement follows from the weakened theorem plus the passage statement.

## Output form

A theorem at the less demanding parameter value, and a precise statement of what the passage to the critical value needs, with the regime in which the passage is proved.

## Failure signal

The weakened statement is already in the literature and the passage statement is the whole difficulty; the passage fails in every regime (the subcritical solutions concentrate for every object, the error term is of the same order as the gap); or the weakened version is true for a reason that does not survive the critical value.

## Typical cash-out

Quantitative improvement (the asymptotically sharp bound); conditional or special-case result (the critical statement in the regime where the passage holds); reduction or equivalence (critical equals weakened plus passage).
