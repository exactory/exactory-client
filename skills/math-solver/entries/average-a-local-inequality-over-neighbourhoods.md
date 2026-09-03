---
name: average-a-local-inequality-over-neighbourhoods
move_class: reduce
costs: [effectivity, implication, object, obligations]
---

## Trigger

- The statement is a universal bound on a global quantity (a density, a weighted sum over a set) attained or nearly attained by an explicit configuration.
- Each element of a configuration can be assigned a region or a weight (a cell of a decomposition of space, a set of multiples with a prescribed least prime factor) so that the regions are disjoint or the weights sum to at most one.
- The naive per-element bound is known to fall short of the target: the best single region has density above the conjectured optimum, or the per-element comparison costs a fixed constant factor.

## Action

1. Write the packing inequality: the sum over elements of the local weight is at most the total. Locate exactly where the loss between the local weight and the target weight sits.
2. Replace the per-element inequality by one that averages over an element and its neighbours, or by one that uses a constraint the extremal structure imposes on which elements can be present together.
3. Choose the decomposition so that the local inequality is both true and tractable; a hybrid of two natural decompositions is allowed.
4. The result is a finite optimisation over local configurations. Pass it to certify-the-finite-residue-by-computation when the case count is large, or close it by hand when a structural constraint leaves a margin.

## Output form

A reduction of the global extremal problem to a local inequality over neighbourhoods, or to a finite list of local configurations.

## Failure signal

No decomposition makes the local inequality true (each candidate has a local configuration exceeding the target); or the local optimisation has more cases than any certified computation can cover; or the constant lost in the local comparison is exactly the gap to the target and no neighbourhood averaging recovers it.

## Typical cash-out

Quantitative improvement (a better constant from a better local inequality); reduction to a finite computation.
