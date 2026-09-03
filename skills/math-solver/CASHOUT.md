# Cash-out: from the state of an attack to publishable units

The unit of publication is a new, correct, non-trivial claim. The inventory produced by declare-the-stall-and-inventory-what-stands lists candidate claims; this document converts each into a unit of one of the forms below, or discards it.

## What every unit contains

A unit is the directory `units/<n>/` in the attack workspace. Its `unit.json` holds the six fields `check-unit` validates:

1. `statement`: one sentence, every quantifier explicit, every hypothesis named.
2. `form`: one of the labels below.
3. `evidence`: a path relative to the workspace that exists: the proof text, or the deterministic step directory (code, inputs, outputs, certificate, `README.md`, `result.json`). Every claim in the unit points to one of these.
4. `novelty`: the queries and hits for this statement, citing `study/problem.md` and the unit-level queries run at stage 7 with the unit's statement as the query.
5. `moves`: the journal move numbers that produced it.
6. `costs`: the ledger the evidence carries, the `costs_paid` of those moves summed, from the cost vocabulary in `strategies/README.md`.

Beside `unit.json` the directory holds the position note (on the path to the target or off to the side, and the prior best result it improves, with its reference) and the journal excerpt (the moves under `moves`, copied from `journal.jsonl`, with their failure signals).

The form labels `check-unit` accepts: `conditional-or-special-case`, `quantitative-improvement`, `reduction-or-equivalence`, `barrier`, `counterexample-or-computational-evidence`, `new-machinery`, `survey-or-problem-paper` (the seven publication forms, numbered 1 to 7 in this order), `counterexample`, `algorithm`, `formalisation`, `formal-proof-write-up` (the standalone units), and `full-proof` and `second-proof` (the closing forms).

## The claim test

A unit's statement has one of these shapes:

- for all X with property P, Q holds;
- there exists X with Q, and X is exhibited;
- A implies B, or A holds if and only if B holds;
- every argument with property P proves Q for the modified problem, where Q is false;
- for every parameter below N, Q holds, with a certificate;
- the following object satisfies Q, with an independent verification.

A statement of the shape "we studied", "we tried", "we explored", or "we attempted", or a narrative of the moves, describes activity and is not a unit. Its content goes into the problem paper.

## What the ledger decides

The `costs` of a unit are the sum of the `costs_paid` of the journal moves the unit lists, and `check-unit` refuses a unit whose `costs` is anything else. It is what this unit's own evidence gave up, not what the strategies behind it declared they might. A move that closes an argument pays nothing, so a unit can carry an empty ledger even under a strategy that declares five costs.

The ledger decides the form the unit takes and what its statement says. The rules compose: a unit carrying two costs answers to both.

- `object`: the unit is a result about the statement the attack reached, not about the claim. A full proof whose ledger carries `object` is two units: a conditional-or-special-case unit on the statement that was proved, and a reduction-or-equivalence unit for the deduction between that statement and the claim, or for the part of it that is proved.
- `effectivity`: the unit is not a quantitative improvement stated with an explicit constant; it states the bound with the constant left unspecified.
- `axioms`: the unit names the base theory its evidence needs, and states the claim relative to it.
- `implication`: the unit states which direction is proved.
- `constructivity`: the unit is not an algorithm unit and not a counterexample unit; it states that the object exists and does not exhibit one.
- `bound_quality`: the unit states the bound in the type its evidence reached, and names the type the claim asks for.
- `obligations`: the unit names the statements its evidence still owes as hypotheses of its statement, and takes the conditional-or-special-case form while any of them stands unproved. An obligation the attack later discharged is not in this ledger, because the move that discharged it paid nothing.

A unit carrying both `object` and `obligations` is the conditional-or-special-case unit of the first rule, with the second rule's hypotheses named in its statement, plus the reduction-or-equivalence unit. A `full-proof` unit carries neither: a proof of the claim itself pays no `object`, and a proof that still owes a statement is conditional, not full.

## Converting the attack state into each form

### Form 1 - Conditional or special-case result

Sources: condition-on-the-one-missing-input (a proved implication with its threshold); prove-the-special-case-where-the-method-is-stronger (a theorem on the subclass); relax-to-the-averaged-or-fractional-version (a result on average or for the fractional version); isolate-a-model-problem (the model theorem); enumerate-small-cases-to-locate-the-threshold (the settled parameters); prove-the-subcritical-or-asymptotic-version-first (the critical statement in the regime where the passage holds); embed-the-object-in-a-family-and-move-along-it (the property at every member reachable by known transports); reduce-existence-to-one-algebraic-obstruction (the parameter values decided); adjoin-the-wanted-object-by-generic-approximation and iterate-the-construction-and-bookkeep-every-candidate (consistency relative to the base theory or to a forcing axiom); diagonalise-against-every-candidate-under-a-guessing-principle (a counterexample under a hypothesis); vary-the-side-parameter-the-construction-fixed (joint consistency); test-independence-under-two-opposite-axioms (one direction proved).

The unit states the exact hypothesis or subclass, the threshold (how much of the hypothesis is needed), why the hypothesis is credible or the subclass natural, and what the bootstrap to the full statement still needs.

### Form 2 - Quantitative improvement

Sources: make-the-bound-explicit-then-attack-the-lossiest-step; choose-the-auxiliary-weight-or-certificate; replace-counting-by-an-algebraic-invariant; iterate-a-structure-versus-randomness-increment; strengthen-the-inductive-hypothesis; bound-failure-by-random-restriction-and-encoding; seed-and-amplify and pin-the-extremal-candidate-first (lower bounds); optimise-the-certificate-family-numerically (a numerical bound made rigorous); prove-the-subcritical-or-asymptotic-version-first (the asymptotically sharp bound); vary-the-side-parameter-the-construction-fixed (the range of the side quantity).

The unit states the previous record with its reference, the new constant or exponent, the ledger of where the gain comes from, and the step that now dominates.

### Form 3 - Reduction or equivalence

Sources: reduce-to-finite-witnesses; reduce-to-one-generating-instance; reduce-dependent-events-to-pairwise-overlaps; extract-structure-from-a-hypothetical-counterexample; attach-an-auxiliary-object-to-each-solution; change-the-ambient-space; strengthen-the-target (the implication); split-into-components-with-explicit-interfaces (the interface statements); relax-to-the-averaged-or-fractional-version (original equals relaxed plus rounding); reduce-the-counterexample-to-a-combinatorial-principle; reduce-existence-to-one-algebraic-obstruction (existence if and only if the obstruction vanishes); embed-the-object-in-a-family-and-move-along-it (the transport statement); prove-the-subcritical-or-asymptotic-version-first (critical equals weakened plus passage); diagonalise-against-every-candidate-under-a-guessing-principle (the extracted principle).

The unit states both statements, proves the implication, and says what the reduced statement gains (fixed data, a finite parameter, a different theory) and what it loses (a constant factor, generality).

### Form 4 - Barrier

Sources: axiomatise-the-method-and-build-a-near-miss; modify-the-problem-inside-the-methods-invariance; test-independence-under-two-opposite-axioms (an independence theorem: the admissible class is every proof from the base theory, and the two models are the near misses); the ceiling reported by a bound entry's failure signal, once it is proved.

The unit defines the admissible class precisely (the axioms the method uses, or the invariance the proofs share), exhibits the near-miss object or the modified problem with proof, states the exact value of the ceiling, lists the known arguments inside and outside the class, and names the property the next method must use. A barrier is a theorem; "approach X did not work" is not one.

### Form 5 - Counterexample or computational evidence

Sources: test-strengthenings-by-counterexample; seed-and-amplify (a counterexample); enumerate-small-cases-to-locate-the-threshold; predict-the-value-and-test-it-numerically; decide-the-direction-from-construction-cost (the record table); optimise-the-certificate-family-numerically (numerical evidence of exactness); diagonalise-against-every-candidate-under-a-guessing-principle (a counterexample under a hypothesis).

The unit gives the object or the data, the independent verification, the exact statement refuted or supported, the range searched, and what is needed to reproduce it (code and inputs). When the unit is evidence rather than proof, it also explains why the conjecture is interesting, what prior work led to it, what follows from it, which special cases are provable, and how to reproduce the experiments. A single counterexample is a standalone unit.

### Form 6 - New machinery

Sources: export-the-lemma-to-sibling-problems (a lemma with generic hypotheses); the substitutes from carry-the-model-argument-back; the weight family from choose-the-auxiliary-weight-or-certificate; the encoding lemma from bound-failure-by-random-restriction-and-encoding; the pipeline from certify-the-finite-residue-by-computation; measure-size-by-entropy; the preservation class and iteration scheme from iterate-the-construction-and-bookkeep-every-candidate; the principle stated as an axiom from reduce-the-counterexample-to-a-combinatorial-principle; the finer invariant from reduce-existence-to-one-algebraic-obstruction.

The unit states the lemma in its widest true form, gives at least one application outside the target, and states where the lemma stops. It is judged on its own merits; being produced while attacking a famous problem earns nothing.

### Form 7 - Survey or problem paper

Source: the residue after the inventory: the journal, the map of ceilings, a corrected conjecture, a strengthened conjecture with its evidence.

The unit contains the problem-shape record, every route with where it stops and why (with the barrier theorems where they exist), the neighbouring statements marked known true or false, and the open sub-questions in one sentence each. This is the one form where "where every known approach breaks down" is acceptable content. When the content is too thin for a survey, it goes to a preprint, a research blog, or the problem's discussion thread as a progress note that says how far the attack got.

### Full proof

Source: the closing of the attack (a move whose output is a proof of the claim, or a counterexample to it that decides the claim), with consolidate-the-proof and formalise-while-fresh as the sources of what the unit contains beyond a partial form.

The unit contains, beyond what every unit contains:

1. The complete argument, from the claim to its conclusion, with every lemma stated and proved in the text or cited with a reference.
2. Every deterministic run the argument depends on, as the step directory with its code, inputs, outputs, `README.md`, and `result.json` with status `pass`. A run with any other status makes the unit a counterexample-or-computational-evidence unit, not a full proof.
3. The independent verification where one exists: the formal check's axiom list from `verify lean`, or the second implementation's agreement from a certified special-case check, or the independent checker's output from `verify certificate`.
4. The consolidation record from consolidate-the-proof (`units/consolidation.md`): the steps marked as unexplained, what replaced each, the rewritten proof with each step's role visible, and the general statement the proof proves.
5. The formalisation record from formalise-while-fresh when it ran: the blueprint, the checked theorem stated exactly with its constants, and the errors found with which were gaps and which were slips.
6. The direction decided (`true` or `false`) and the mode the proof is in (`existence`, `construction`, `computation`, or `certificate`), as `problem.json` records them at closing.

### Second proof

Source: consolidate-the-proof, step 4 (an independent second route to a conclusion already proved), applied to a proof in the attack or in the literature.

The unit contains everything a full-proof unit contains, for the second route, and in addition: the existing proof it is independent of, with its reference; the first step at which the two arguments diverge, named as an intermediate object in each; and the features of the first proof the second one shows to be inessential, which is what a second proof is for. A second route that shares the first proof's hardest step is an exposition (the survey-or-problem-paper form, or a formal-proof-write-up), not a second proof.

### A heuristic argument that predicts a value

Source: predict-the-value-and-test-it-numerically (the derived prediction with its assumptions, and the corrected conjecture with its correction factor); optimise-the-certificate-family-numerically (the exactness conjecture).

A heuristic argument proves nothing, so it is never the evidence of a full-proof or quantitative-improvement unit. It takes one of two paths:

- When it rests on a deterministic run that tested it (an enumeration run or a numerical optimisation run whose README records the instances compared and the agreement or the discrepancy), it is a counterexample-or-computational-evidence unit with the run as its evidence, stated as a conjecture with the predicted value, the assumptions of the heuristic (which events were treated as independent, which terms were dropped), and the range over which it was tested.
- Otherwise it is content of the survey-or-problem-paper form: the conjecture with its predicted value and the heuristic's assumptions, placed with the neighbouring statements and the open sub-questions.

### Standalone units

A single counterexample; an algorithm from a constructive proof or a search procedure, with its correctness proof; a formalisation from formalise-while-fresh; a human-readable write-up of an existing formal proof from consolidate-the-proof.

## Decomposition into units

Several results become one unit when one implies the other, when one is the input to the other's interface (split-into-components-with-explicit-interfaces), or when they share a hypothesis and a narrative. Otherwise each result is its own unit: a barrier and a special case of the same problem are two papers; a lemma exported to a sibling problem is its own paper, judged on the sibling's merits. The venue follows the unit's position relative to the target, not its form.

## Self-check before writing

- Can the claim be stated in one sentence?
- Is it a claim, or a description of activity?
- Does it follow from known literature in a few lines? If so, it is not a unit.
- Has someone already proved it? Search hardest here, by statement.
- Is it on the path to the target, or off to the side? This decides the venue, not whether it is publishable.
- Does every claim in the unit point to a proof or to a deterministic run?
