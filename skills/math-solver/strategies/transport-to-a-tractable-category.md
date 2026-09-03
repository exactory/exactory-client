---
name: transport-to-a-tractable-category
component: stage
description: Use when a proved correspondence carries the object into another setting and reflects the property, the property is known at another member or a short list of values, and the direct invariants admit no known bound
entries: [embed-the-object-in-a-family-and-move-along-it, reduce-existence-to-one-algebraic-obstruction]
precedes: [verify-formally-with-lean4]
excludes: []
costs: [implication, constructivity, object, obligations]
---

## What it moves

The stage. Before: the setting where the statement is posed, whose
direct invariants no known method bounds. After: the destination of a
proved correspondence, a family containing the object or an auxiliary
space carrying one invariant. The correspondence reflects the property,
so the statement keeps its truth value. Direction and mode stay.

Implication is paid at step 3, where a missing transport leaves the
reduction between two members proved in one direction only, so the record
loses the refutation route. Constructivity is paid at step 4: an invariant
decides existence at the values outside the known list, and the record
holds no object realising it. Object is paid at step 3 or step 4, where
that reduction, or the equivalence, stands as a statement of its own, so
what the record carries is not the claim. Obligations are paid at step 2:
the marked direction, the lift, and each transport's local hypothesis along
the path enter the record as statements still to be proved.

## Precondition procedure

Answer from the named field of `problem.json`.

1. Do the neighbours record a proved correspondence out of the setting: a lifting or preservation theorem along a family, or a chain of implications to one invariant? (from: shape.neighbours; required)
2. Does the recorded correspondence reflect the property, the object having it exactly when its image does? (from: shape.neighbours; required)
3. Is the property known, or the difficulty parameter smaller, at another member or a short list of values? (from: shape.known_bounds; required)
4. Are the direct invariants recorded as unbounded by known methods? (from: shape.known_bounds; required)
5. Is the object rigid, with no continuous parameter to vary? (from: shape.objects; optional)
6. Does the ambient structure supply the transport's local hypothesis along a path to the known member? (from: shape.ambient_structure; optional)

Verdict: yes when questions 1 to 4 are yes; unknown when one is unknown
and none is no; no otherwise. Questions 5 and 6 both yes select the
family shape, otherwise the obstruction shape.

## Plan

1. Study how the object was carried into another setting before, on
   problems of this shape, under `../STUDY.md`, producing
   `study/transport-to-a-tractable-category.md`. Settle before step 2:
   the correspondences in print and the direction each is proved in;
   the members or values already reached, and the hypothesis that
   blocked the path; the invariants already found to vanish. Output:
   the correspondence to use, where it stopped, and the settled range.
2. Write the correspondence as one equivalence line, sourcing each
   direction and marking the one the attack uses. Family shape: prove
   the lift. Entry dispatched:
   embed-the-object-in-a-family-and-move-along-it, step 1. Output: the
   sourced line and the lift. Obstruction shape: name the auxiliary
   space and what makes it small. Entry dispatched:
   reduce-existence-to-one-algebraic-obstruction, step 1. Output: the
   sourced line and the auxiliary space.
3. Family shape. Prove the property at the known member and return
   along a path using only transport theorems in print, as an induction
   on the difficulty parameter. Entry dispatched:
   embed-the-object-in-a-family-and-move-along-it, steps 2 to 4. Output:
   the chain of transports, handed to the next strategy, or, when one
   transport is missing, the reduction between members, for the
   cash-out.
4. Obstruction shape. Detect with the coarsest invariant, then a finer
   one on the values left, and prove the residual elementary statement
   directly. Entry dispatched:
   reduce-existence-to-one-algebraic-obstruction, steps 2 to 4. Output:
   the decision at every value outside the known list, its detecting
   invariant, and the equivalence as a separate statement, handed to the
   next strategy or the cash-out.

## Failure signal

The strategy ends within six moves (step 2, then at most two paths or
two invariants) when:

- The marked direction is not in print and proving it restates the
  original difficulty. The record gains the one-directional statement
  and a no for question 2.
- No lift exists, or every path violates the transport's local
  hypothesis. The record gains the members reachable and the blocking
  hypothesis.
- The finer invariant also vanishes, the residual statement fails where
  the object is known absent, or the chain loses a case. The record gains
  the values decided and the blind range as a ceiling.

At each ending the harness sets the verdict to no.

## Cash-out

From the forms in `../CASHOUT.md`: the equivalence is a
reduction or equivalence (form 3) while the destination stays
open; the members reachable or values decided are a conditional or
special-case result (form 1); a finer invariant or new transport theorem
is new machinery (form 6).

## Composes with

Precedes `verify-formally-with-lean4`, which receives the chain or the
decision, the one order the front matter enforces. Composes with
`strengthen-and-generalise`, in either order, when the statement must
first widen to the whole family. `reduce-and-translate` moves the
statement with its correspondence and `solve-the-model-world-first`
moves to a setting with nothing proved between them; this one keeps
the statement. Excludes nothing.

## Common mistakes

- Transport in the wrong direction: the theorem in print goes from
  object to image and the attack needs the converse. Check: step 2's
  line cites a source for the marked direction.
- The invariant treated as the target, the stage move unrecorded.
  Check: step 2's journal entry names the stage before and after, and
  every unit is about the object or labelled as the equivalence.
- The correspondence chosen because recent results used it. Check: each
  required answer cites its field of `problem.json` and the study cites
  the theorem proving it.
- A third path or invariant after the second fails. Check: at most six
  journal moves under this strategy.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/transport-to-a-tractable-category.md` is missing
  or empty (`../harness/README.md`).
