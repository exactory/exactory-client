---
name: replace-counting-by-an-algebraic-invariant
move_class: bound
---

## Trigger

- The statement is an extremal bound on a set or a subgraph inside a highly symmetric ambient structure: a vector space over a finite field, a hypercube or another vertex-transitive graph, a configuration of lines or points in space.
- The forbidden or required configuration is an algebraic condition: three points summing to zero, adjacency, incidence with a curve, a linear dependence.
- Counting arguments (density increments, incidence counts, greedy degree bounds) have a documented ceiling well short of the target.

## Action

1. Find a matrix, polynomial, or tensor whose rank, spectrum, or degree encodes the configuration: a polynomial of controlled degree vanishing on the whole configuration, a signed adjacency matrix, a tensor whose rank-type invariant is at most the ambient dimension.
2. Establish the monotonicity the argument needs: the invariant is inherited by induced substructures (eigenvalue interlacing), is subadditive under decomposition, or is at most a dimension count.
3. Compare the invariant on the full object with what a configuration-free set forces (a diagonal tensor of full rank; a polynomial vanishing on too many points must vanish identically).
4. Choose the auxiliary object (the signing, the space of polynomials) so that its invariant is as spread or as small as possible; this choice is where the sharpness lies.
5. Read off the extremal bound. It is typically exponential where counting gave a polynomial saving.

## Output form

An upper bound on the extremal quantity in terms of an algebraic invariant of the ambient structure.

## Failure signal

The invariant is not monotone under the passage the argument needs (no interlacing, no subadditivity); or the ambient structure has no algebraic model (the integers in place of a finite field) and no substitute for the polynomial count exists; or the invariant's bound is provably tight for a variant that is not the target.

## Typical cash-out

Quantitative improvement (an exponential saving); new machinery; barrier, when the invariant's bound is shown tight for the variant it controls.
