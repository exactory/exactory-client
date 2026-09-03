---
name: change-the-ambient-space
move_class: reformulate
costs: [bound_quality, implication, object]
---

## Trigger

- The statement concerns a configuration defined by a relation among objects (a repeated distance, a flip of one coordinate, three points in a line, a family of thin sets in given directions), and the extremal quantity has been attacked by counting in the space where the objects live.
- A map exists under which the configuration becomes a different kind of object that a separate theory controls: into the symmetry group, where a repeated configuration becomes an incidence between curves; into a fixed graph, where all objects of the class become induced subgraphs; into an abelian group by slicing, where the configuration becomes a sumset condition; into a different ambient group, where a rank or dimension count applies.
- The counting arguments in the original space have a documented ceiling.

## Action

1. Write the configuration as a relation and list the maps under which the relation becomes an incidence, an adjacency, or an additive condition.
2. For each map, state the image problem exactly and check that it is not harder than the original (the image problem may lose the special structure of the family; record what it loses).
3. Pick the image whose ambient has a theory the original lacks (incidence bounds in a higher-dimensional space, spectral bounds on one fixed graph, sums-versus-differences estimates, rank bounds).
4. Prove the image statement, then pull the bound back, and check that the pull-back uses the special structure of the family where the image theorem alone is false.

## Output form

An equivalent or stronger statement about a configuration in a different ambient space, and a proof of the pull-back.

## Failure signal

The image problem is false without the special structure of the family, and no way to encode that structure in the image is found; or the pull-back costs a factor equal to the whole gap; or every candidate image lands in a space whose theory has the same ceiling as the original.

## Typical cash-out

Reduction or equivalence; new machinery; quantitative improvement, when the image theory gives a better exponent.
