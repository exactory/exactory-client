---
name: measure-size-by-entropy
move_class: reformulate
costs: [bound_quality]
---

## Trigger

- The statement bounds the cardinality of a set or family under an additive or combinatorial constraint (a small sumset, a spread condition), and the standard counting inequalities lose a factor each time two of them are composed.
- The objects can be replaced by random variables (the uniform distribution on the set, a random member of the family) so that the constraint becomes an inequality between entropies.

## Action

1. Restate the size measure as an entropy, and the constraint as an entropic distance or an information inequality.
2. Use the chain rule, submodularity, and conditioning to decompose the quantity along a projection or a fibration; these compose without loss where the counting inequalities lost a factor.
3. Run the increment or compression argument (iterate-a-structure-versus-randomness-increment) on the entropic functional, with a separate endgame for the case where no step improves.
4. Convert back to sets once, at the end, and record the loss of that single conversion.

## Output form

An entropic inequality equivalent to the set statement, and a set bound obtained by one conversion.

## Failure signal

The conversion back to sets loses the polynomial dependence that was the target; or the constraint has no entropic form because it is not preserved by passing to a random variable on the set.

## Typical cash-out

New machinery; quantitative improvement.
