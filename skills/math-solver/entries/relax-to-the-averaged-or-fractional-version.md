---
name: relax-to-the-averaged-or-fractional-version
move_class: reduce
costs: [bound_quality, effectivity, object, obligations]
---

## Trigger

- The statement has a free parameter (a base, a set of moduli, a cover, a colouring) over which it must hold pointwise, and the difficulty is uniformity in that parameter.
- Averaging over the parameter, or replacing an integral quantity by its fractional or linear-programming relaxation, gives a statement that the standard tools close.

## Action

1. Write the averaged or fractional statement and prove it.
2. Decompose the original into the relaxed statement plus a rounding or deviation statement: the integral optimum is within a factor of the fractional one; the pointwise value is within the average.
3. Attack the rounding statement separately. Its hypothesis is often the dual of the relaxation, which is where an imported lemma may fit (import-the-engine-from-an-adjacent-problem).
4. If the rounding gap cannot be closed, the averaged result stands as a result of the right order.

## Output form

A theorem on average or for the fractional version, and a clean statement of the rounding gap.

## Failure signal

The rounding gap is as large as the original difficulty (the fractional and integral versions differ by the whole factor at stake); or the averaged version is trivial because the average is dominated by easy instances.

## Typical cash-out

Conditional or special-case result (on average); reduction or equivalence (original equals relaxed plus rounding); quantitative improvement (for a fractional version of the target quantity).
