---
name: seed-and-amplify
move_class: construct
---

## Trigger

- The wanted object (a counterexample, a lower-bound construction, a singular solution of a model equation, a graph that forces many colours) must live in a parameter range too large to search directly, or no direct construction is known.
- An operation exists that maps small objects to larger ones and amplifies the relevant quantity: a product, a gluing along a shared substructure, an iteration of a reduction that increases an excess, a cascade that hands a conserved quantity from one scale to the next, a hinge of two copies of a rigid gadget so that forced pairs collide.

## Action

1. Prove the amplification lemma first: state exactly how the operation changes each parameter and the quantity of interest, and confirm that it preserves the constraint (no forbidden configuration is created, the conserved quantity is still conserved).
2. Determine the smallest parameter at which a seed can carry a positive excess: the first dimension where a length can exceed its threshold, the smallest motif that can be forced to appear somewhere and forbidden at a fixed position.
3. Search for the seed there (enumerate-small-cases-to-locate-the-threshold) and verify it by an independent check, with a computer-free proof where one is available.
4. Apply the amplification and quantify the violation or the bound it gives.
5. Optimise the seed to shrink the final object, then test the limits of the seed class.

## Output form

An explicit object with the required property, or an infinite family of them, with the quantity of interest computed.

## Failure signal

The amplification's gain tends to zero under iteration (the excess is diluted by a denominator that grows faster); or every seed in the smallest parameter range has zero excess, so the seed dimension must rise and the search becomes infeasible; or the operation creates the forbidden configuration.

## Typical cash-out

Counterexample or computational evidence; quantitative improvement (a lower bound).
