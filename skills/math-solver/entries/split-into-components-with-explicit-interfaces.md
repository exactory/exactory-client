---
name: split-into-components-with-explicit-interfaces
move_class: decompose
costs: [bound_quality, object, obligations]
---

## Trigger

- The proof, or the planned proof, is a chain of implications each carrying a numerical parameter (an exponent of distribution, a tuple size, a decomposition number, a splitting depth), and the final bound is a function of all of them.
- At least two components can be improved independently of each other.

## Action

1. State each interface as a numerical inequality: component A delivers parameter x; component B needs x at least y.
2. Optimise each component separately, computer-assisted where the component is an optimisation, and in the open where many hands help.
3. Isolate the hardest component (the hardest local configuration, the hardest prime, the diagonal case) and give it a dedicated proof, or drop it while keeping the others.
4. Recombine and recompute the final bound; keep a ledger of which component now dominates.
5. When the whole attack has stalled, treat each component as a separate publishable unit.

## Output form

A table of components, interfaces, and current parameter values, and the final bound as a function of them.

## Failure signal

The interfaces are not numerical (a component delivers a structural property that no number captures); or improving one component worsens another through a shared parameter; or one component is a known barrier and the final bound cannot move without it.

## Typical cash-out

Quantitative improvement; reduction or equivalence (the interface statements); new machinery (a component that stands alone).
