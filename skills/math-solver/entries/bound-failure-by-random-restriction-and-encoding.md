---
name: bound-failure-by-random-restriction-and-encoding
move_class: bound
---

## Trigger

- The statement concerns a family of sets and a robust target condition: a random subset of the ground set, keeping each element with probability p, contains a member of the family with high probability; or the family admits no cheap cover.
- The family is spread (no small set is contained in a large fraction of the members), or the argument has already split off the structured case where one is.

## Action

1. Sample the random subset in rounds.
2. For each way the random set can fail to contain a member, define a short description: the minimum fragment of a member relative to the sample, or the members whose fragments are large, collected into a cover.
3. Show that either the sample contains a member, or the descriptions assemble into a cheap cover, which the spread hypothesis excludes; equivalently, count the descriptions and compare with the number of failing samples.
4. When the combinatorial count is loose, recast it in the language of entropy or noiseless coding; this removes parasitic dependence on secondary parameters.

## Output form

A bound on the probability that the random restriction fails, in terms of the spread parameter and the number of rounds.

## Failure signal

The encoding length exceeds the entropy budget by a factor that grows with the set size; or the spread hypothesis has no analogue for the integral version and the dual object the argument relied on disappears, in which case the descriptions must certify smallness directly or the move stops.

## Typical cash-out

Quantitative improvement; new machinery (the encoding lemma, which exports readily).
