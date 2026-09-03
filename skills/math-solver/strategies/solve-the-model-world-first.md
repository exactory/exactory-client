---
name: solve-the-model-world-first
component: stage
description: Use when the ambient carries two kinds of structure, the known methods are stuck on their interaction, and a recorded simpler setting keeps the configuration and drops one kind
entries: [isolate-a-model-problem, carry-the-model-argument-back]
precedes: [verify-formally-with-lean4, transfer-between-finitary-and-infinitary]
excludes: []
costs: [effectivity, bound_quality, object, obligations]
---

## What it moves

The stage, out and back. Before: an ambient carrying two kinds of
structure, the known methods stuck on their interaction. Out: a model
ambient keeping the configuration and dropping one kind, the statement
posed there verbatim. Back: the original ambient, each model-only step
replaced by a substitute. Statement, direction, and mode stay; both
stage moves are `journal.jsonl` lines with `problem_changed` true.

`object` is paid at step 2, where the statement is read in the model
ambient, and the record holds a theorem about that ambient until step 5
moves the stage back. `effectivity` is paid at step 5, where a substitute
proved by a structure theorem gives a constant the argument cannot compute,
and the record returns a bound whose value it has lost. `bound_quality` is
paid at step 5 as well, where each substitute's loss enters the exponent
and the record returns a bound weaker than the model's. `obligations` is
paid at step 4, where every model-only step leaves a substitute to be
proved in the original.

## Precondition procedure

1. Does the ambient structure carry two kinds of structure at once? (from: shape.ambient_structure; required)
2. Does the missing input name the interaction of those two kinds as what the strongest known method lacks? (from: shape.missing_input; required)
3. Do the neighbours record the statement's version in a simpler ambient that drops one of those kinds, marked open or settled? (from: shape.neighbours; required)
4. Is the configuration stated in the kept kind alone, so that model carries it unchanged? (from: shape.configuration; required)
5. Does the statement read verbatim in that model, no clause resting on the dropped kind? (from: quadruple.statement; required)
6. Does the proof shape record, for a known model proof, which steps use model-only structure and whether the original has substitutes? (from: shape.proof_shape; optional)
7. Is the target quantity one that an unspecified constant still settles? (from: shape.target_quantity; required)

Verdict: yes when questions 1 to 5 and 7 are yes; unknown when one of
those is unknown and none is no; no otherwise. A yes to question 6 skips
steps 2 and 3; step 4 starts from the known model proof the study
records.

## Plan

1. Study how the statement was posed in a model before, on problems of
   this shape, under `../STUDY.md`, producing
   `study/solve-the-model-world-first.md`. Settle before step 2: the
   model ambients tried and the kind each dropped; whether a model proof
   is in print, with its model-only steps labelled; the substitutes
   tried in the original and where each lost. Output: the constraints
   picking step 2's model, or the known model proof starting step 4.
2. Pose the model. Write the statement verbatim in the model ambient,
   read it against the claim sentence, and record the stage move.
   Dispatches isolate-a-model-problem, steps 1 and 2. Output: the model
   statement, to step 3.
3. Prove the model, labelling each step that uses structure the original
   lacks; when the model resists, add once the feature its
   counterexamples exploit. Dispatches isolate-a-model-problem, steps 3
   and 4. Output: the labelled proof, to step 4.
4. Substitute. For each model-only step, record a substitute in the
   original and its loss; for a step with none, solve the model again by
   a route avoiding that tool. Dispatches carry-the-model-argument-back,
   steps 1 and 2. Output: the substitutes with their recorded losses, to
   step 5.
5. Carry back. Add the conversion step the original needs, re-run the
   argument in the original, compare its exponent with the model's, and
   record the stage move back. Dispatches carry-the-model-argument-back,
   steps 3 and 4. Output: a proof in the original setting with the
   model's shape and an exponent comparable with the model's, to the
   next strategy or cash-out.

## Failure signal

The strategy ends, in bounded moves, when one of these fires; the
harness sets its verdict to no:

- The model's answer differs qualitatively and the one added feature of
  step 3 does not repair it. The record gains the model statement with
  its proof or counterexample.
- A model-only step has no substitute and the second route of step 4
  uses the same tool. The record gains the labelled proof and a barrier
  candidate.
- The transferred bound is no better than the previous record. The
  record gains the substitutes, their losses, and the bound.

## Cash-out

From the forms in `../CASHOUT.md`: the model theorem is a
special-case result (form 1) naming its ambient; a transferred
proof beating the record is a quantitative improvement (form 2); the
substitutes are new machinery (form 6); proved non-transferability is a
barrier (form 4); a model counterexample is form 5, labelled as about
the model.

## Composes with

Precedes `verify-formally-with-lean4`, since the model proof is a lemma
chain, and `transfer-between-finitary-and-infinitary`, since the stage
returns to the original. Typically it precedes
`reduce-to-a-finite-computation` when the model is finite, and follows
`strengthen-and-generalise` and `ladder-the-parameter`, which fix the
statement first. Excludes nothing.

## Common mistakes

- The target replaced by a proxy the model computes. Check: step 2's
  reading against the claim sentence, and the statement field unchanged.
- The model chosen from memory drops the wrong kind. Check: the study
  record exists, and question 3's answer names the dropped kind, one of
  the two in question 1.
- The model theorem declared as the result, the stage never moved back.
  Check: `journal.jsonl` carries both stage moves, and each unit's claim
  names its ambient.
- The added feature of step 3 repeated until the budget is spent. Check:
  it is added once, then the failure signal fires.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/solve-the-model-world-first.md` is missing
  or empty (`../harness/README.md`).
