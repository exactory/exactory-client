---
name: reduce-and-translate
component: statement
description: Use when a map turns the configuration's relation into another kind of object, a neighbouring statement about that kind is known true, and the original setting's arguments have a documented ceiling
entries: [change-the-ambient-space, replace-counting-by-an-algebraic-invariant, measure-size-by-entropy, attach-an-auxiliary-object-to-each-solution, reduce-to-one-generating-instance, reduce-the-counterexample-to-a-combinatorial-principle, import-the-engine-from-an-adjacent-problem, revive-the-abandoned-route]
precedes: [split-structure-from-randomness, prove-the-barrier-first, verify-formally-with-lean4]
excludes: []
costs: [implication, effectivity, bound_quality, axioms, object, obligations]
---

## What it moves

The statement, and the stage when the image lives elsewhere. Before: a
claim about a configuration or its solutions whose direct arguments have
a ceiling. After: a claim about the image in a destination whose theory
controls it, with a proved pull-back deducing the original from the
image. Direction and mode stay.

`object` is paid at step 2: the statement becomes the image, and the record
loses the claim until the pull-back is proved. `implication` is paid at
step 2 or 4, where a stronger image or a one-way equivalence costs the
record the refutation route. `effectivity` is paid at steps 3 and 5, where
the auxiliary class's finiteness theorem and the imported lemma give a
constant the argument cannot compute, so the record loses its value.
`bound_quality` is paid at step 2 or 5, where an invariant, a conversion,
or an imported lemma costs the record its numeric bound. `axioms` is paid
at step 4, where the principle decides the statement only beyond the base
theory, which the record gives up. `obligations` is paid at steps 3 to 5:
the auxiliary claim, the generating instance, and the imported lemma each
stay to be proved.

## Precondition procedure

Answer from the named field of `problem.json`.

1. Is the configuration a relation among the objects, algebraic or not? (from: shape.configuration; required)
2. Is a neighbouring statement posing that relation as an incidence, adjacency, rank, entropy, or combinatorial principle marked known true? (from: shape.neighbours; required)
3. Is a ceiling documented for counting, direct invariants, or density increments? (from: shape.known_bounds; required)
4. Is the statement quantified over solutions directly? (from: shape.objects; optional)
5. Is the quantifier structure universal? (from: shape.quantifiers; optional)
6. Does the base theory name the system the statement is posed in and the hypotheses beyond it that decide it? (from: shape.base_theory; required)
7. Is the missing input a lemma free of the problem's vocabulary? (from: shape.missing_input; optional)
8. Is the target quantity one that an unspecified constant still settles? (from: shape.target_quantity; required)

Verdict: yes when 1 to 3, 6 and 8 are yes; unknown when one is unknown
and none no; no otherwise. Question 4 is step 3's condition, question 5 step
4's, question 7 step 5's; question 6 picks step 4's entry.

## Plan

1. Study how this relation was translated before, on problems of this
   shape, under `../STUDY.md`, producing
   `study/reduce-and-translate.md`. Settle before step 2: the maps in
   print and what each pull-back lost; the route stopped at one
   obstruction, for step 6; the lemma step 5 would import. Output: the
   constraints picking step 2's first map.
2. Translate the configuration. List the maps turning the relation into
   another kind of object; take the one whose destination has the theory
   the origin lacks, the next on a failure signal, three at most. Entry
   and output, by kind of image: change-the-ambient-space, the image
   statement and its pull-back;
   replace-counting-by-an-algebraic-invariant, the bound by the
   invariant; measure-size-by-entropy, the entropic inequality and its
   set bound. Journalled with `problem_changed` true, the quadruple
   naming image and destination.
3. Translate the solutions, when question 4 is yes. Entry:
   attach-an-auxiliary-object-to-each-solution. Output: the map proved
   finite-to-one and the auxiliary claim, with its lemma remaining.
4. Translate the class, when question 5 is yes and question 4 is no.
   Entry: reduce-to-one-generating-instance, or
   reduce-the-counterexample-to-a-combinatorial-principle when question
   6's base theory leaves the statement undecided at an uncountable
   cardinality. Output: an equivalence with one instance or principle.
5. Import the engine, when question 7 is yes or step 3 left one lemma.
   Entry: import-the-engine-from-an-adjacent-problem. Output: the lemma
   proved and the bound.
6. Revive the route step 1 found stopped at one obstruction. Entry:
   revive-the-abandoned-route. Output: the old route proved inside step
   2's framework.

The last step run hands the next strategy the translated quadruple,
losses, and bound.

## Failure signal

The strategy ends within three maps at step 2 and one move per later
step, when:

- Three maps land at the origin's ceiling or lose the origin's
  structure their image needs: the record gains each loss and the
  ceiling.
- The pull-back costs the whole gap, or the invariant is tight for a
  variant: the record gains a ceiling.
- The solution map is not finite-to-one, or the equivalence is one-way:
  the record gains a conditional.
- The imported lemma loses a factor, lacks its hypothesis, or the
  obstruction reappears: the record gains the ceiling, a conditional, or
  the loss.

The harness then sets the verdict to no.

## Cash-out

From the forms in `../CASHOUT.md`: the pull-back with the
image unproved: reduction (form 3); a known conjecture on the auxiliary
class or a one-way equivalence: conditional (form 1); a gained exponent:
quantitative improvement (form 2); the invariant, inequality, or
principle: new machinery (form 6); a tight invariant: barrier (form 4).

## Composes with

Precedes `split-structure-from-randomness`, whose dichotomy is stated
on the translated statement, and `prove-the-barrier-first`, whose
class is the destination's tools; both enforced: a direction move
needs the statement moved first. Follows `strengthen-and-generalise`
when the generalised statement admits the map. Excludes nothing: a
statement move composes with any component.

## Common mistakes

- A proxy for the target, the pull-back skipped, or a one-way
  implication recorded as an equivalence. Check: step 2's journal line
  carries the pull-back; steps 3 and 4 carry both directions.
- The translation picked from memory. Check: step 2 lists more than one
  map, none the study marks as in print.
- A step dropped unchecked. Check: each step whose condition holds has
  a journal line, and a skipped step's condition fails on record.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/reduce-and-translate.md` is missing
  or empty (`../harness/README.md`).
