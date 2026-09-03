---
name: iterate-a-structure-versus-randomness-increment
move_class: bound
costs: [bound_quality, implication, object, obligations]
---

## Trigger

- The statement is an upper bound on the size or density of a set or family that avoids a configuration inside a structured ambient (an interval of integers, a vector space, a set system, a graph).
- A random set of the claimed density would contain the configuration, so a counterexample must be biased, and the bias is visible to some transform or statistic: a large character coefficient, a dense link, a large pairwise parameter.
- The ambient has a class of structured pieces (subprogressions, approximate subgroups, subspaces, links, compressed graphs) on which the set can be denser.

## Action

1. Define the increment quantity: the density on a structured piece, an entropy, or a hybrid of density and size that the compression step can increase.
2. Prove the dichotomy: either the configuration count is as for a random set (and the target follows), or the set is denser on some structured piece by a quantifiable increment.
3. Check that the structured pieces are closed under the operations the argument needs (translation, dilation, intersection). If they are not, replace them by approximate versions that are (strengthen-the-target).
4. Iterate. The density cannot exceed one, so the number of iterations is finite. Give a separate endgame for the case where no step improves.
5. Measure the loss per iteration against the gain. The exponent of the final bound is their ratio.

## Output form

An upper bound on the extremal density, with the exponent determined by the ratio of increment to loss.

## Failure signal

Tracking one quantity alone loses control of another the argument needs (density alone loses the size of the pieces; size alone cannot be made to increase at every stage); or the per-iteration loss is of the same order as the gain, so the bound stalls at a logarithmic saving, which is the documented ceiling of this route; or the structured pieces are not closed under the operations and no approximate version is.

## Typical cash-out

Quantitative improvement; barrier (the increment method's ceiling).
