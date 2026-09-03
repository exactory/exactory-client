---
name: consolidate-the-proof
move_class: reformulate
---

## Trigger

- A proof of the target, or of a major intermediate, exists (in the attack or in the literature) that is long, depends on a computation, or takes a route its own authors call ad hoc.
- The authors leave a question of the form "is there a version inside the standard framework", "can the computational input be replaced", or "is there a second route", or such a question is natural.
- Or the attack has closed and the closing argument is about to become a full-proof or second-proof unit.

## Action

1. Mark the steps whose necessity is unexplained: a computer verification of an inequality, a hybrid decomposition, an ad hoc lemma, a detour through a model setting, a forced motif that a different motif might replace.
2. For each marked step, try the standard framework's tool in its place and check whether the constant or exponent survives.
3. Rewrite the proof so that each step's role is visible. The aim is that the hardest inequality is recognised as a known theorem, or is mechanised, or is replaced by an algebraic argument.
4. Look for an independent second route to the same conclusion through a different intermediate object (a richer theory in which a higher-order construction becomes first-order, a presentation by partial orders in place of an algebraic one, a coordinate system that unifies a local and a global argument); two routes expose which features of the first were essential.
5. Extract the general statement the proof actually proves, which is usually wider than the target.

## Output form

A shorter or computation-free proof, an exposition, an independent second proof, or a generalisation.

## Failure signal

Every replacement of a marked step loses a factor that the target does not permit; or the computational input cannot be removed without a new idea; or the second route produces a far larger object than the first.

## Typical cash-out

Survey or exposition; new machinery; quantitative improvement, when optimising a lossy step falls out of the rewrite.
