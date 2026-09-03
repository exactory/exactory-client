---
name: verify-formally-with-lean4
component: mode
description: Use when a lemma chain or a finite check is stated precisely enough to encode, its objects are defined in core Lean or Mathlib, and the record needs a machine-checked certificate
entries: [formalise-while-fresh, certify-the-finite-residue-by-computation]
precedes: []
excludes: []
costs: [axioms, object, obligations]
---

## What it moves

The mode. Before: an informal argument a referee reads. After:
certificate. The statement is a Lean theorem, its proof a term the kernel
accepted, and the record holds the axiom list it rests on.
Statement, stage, and direction stay; the re-expression is itself a check
that the theorem is the claim.

Axioms are paid at steps 4 and 6, where a check the kernel cannot reduce
is evaluated natively: the axiom list carries the compiler, and the unit
is evidence, not a certificate. The object cost is paid at steps 4
and 5, where a lemma is restated smaller and the theorem left standing
carries the resisting lemma as an added hypothesis, so what the kernel
checks is not the statement step 2 fixed. Obligations are paid at step 3:
every lemma the skeleton still proves by `sorry` is a statement the
record owes.

## Precondition procedure

1. Is the statement, or the lemma chain that proves it, written with every quantifier and hypothesis explicit? (from: shape.proof_shape; required)
2. Do the objects have definitions in core Lean or Mathlib, rather than being objects the attack defined for itself? (from: shape.objects; required)
3. Is the base theory one Lean's standard axioms cover, classical mathematics with choice? (from: shape.base_theory; required)
4. Does one side of the statement have a finite certificate a decision procedure can evaluate? (from: shape.finite_certificates; optional)
5. Is the mode existence, construction, or computation with the argument written out in full? (from: quadruple.mode; optional)

Verdict: yes when questions 1 to 3 are yes; unknown when one is unknown
and none is no; no otherwise. Question 4 picks the shape of input that
comes first, question 5 whether the chain is encoded or only the finite
check.

## Plan

1. Study how this statement and its neighbours were formalised before,
   under `../STUDY.md`, producing `study/verify-formally-with-lean4.md`.
   Settle before step 2: whether a repository holds the canonical formal
   statement of the claim (`../references/sources.md`, section 3); what
   core or Mathlib defines; whether a formal proof or a native evaluation
   is in print. Output: those constraints on steps 2 to 6.
2. Fix the statement: the theorem written in Lean before any proof, under
   the encoding rules of `references/lean4.md` section 8, read against the
   claim sentence quantifier by quantifier. Entry:
   formalise-while-fresh, step 1. Output: the statement file, elaborated
   under `sorry`.
3. Question 4 yes and question 5 no: go to step 4 and encode only the
   finite check; otherwise encode the chain, one theorem per lemma in
   dependency order, each proved by `sorry`, a second build showing they
   suffice for the final theorem. Entry: formalise-while-fresh, step 2.
   Output: the skeleton, every unproved node visible.
4. Discharge the finite residue: a decision procedure of
   `references/lean4.md` section 6 in place of `sorry`; an instance too large to reduce is restated smaller,
   or recorded as natively evaluated evidence. Entry:
   certify-the-finite-residue-by-computation, steps 2 and 6. Output: the
   residue lemmas closed, with their axiom lists.
5. Discharge the remaining lemmas in that section's tactic order, six
   attempts and two restatements at most, a restatement allowed only if it
   still implies what the chain needs. Entry:
   formalise-while-fresh, step 3. Output: every lemma closed, or the one
   that resists named.
6. Run `formal check` in the step directory of that reference's section
   7: the harness
   writes `result.json`, and pass means the axiom list stays within Lean's
   standard axioms. Record the theorem, its constants, the toolchain, and
   that list in the step's `README.md`. Entry: formalise-while-fresh,
   step 4. Output: the certificate.

## Failure signal

The strategy ends when one fires:

- The statement needs library material that does not exist: stating the
  lemmas would mean building a definition and its lemma library first.
  The record gains what was written, the missing definitions, and
  question 2's no.
- A lemma resists every tactic within six attempts and two restatements,
  a gap the informal proof did not close. The attack resumes in the
  strategy that produced it; the record gains its name, the checked
  skeleton, and the conditional theorem.
- The build fails on the toolchain: infrastructure, not mathematics; the
  record gains the build log and the step is marked not run.
- The axiom check reports `sorryAx` or a custom axiom after step 5 is
  spent; the record gains the list and the node carrying it.

## Cash-out

From the forms in `../CASHOUT.md`: a complete checked chain: a
Lean formalisation, one unit whatever its lemma count; a kernel-
evaluated finite check: computational evidence (form 5), or part of a full
proof when the chain is checked; a natively evaluated one: computational
evidence only; every lemma but one checked: conditional result (form 1),
the unproved lemma an explicit hypothesis; a stalled chain's blueprint:
exposition (form 7).

## Composes with

Follows `reduce-to-a-finite-computation`, whose finite instances are step
4's input, and any strategy ending in a lemma chain:
`ladder-the-parameter`, `strengthen-and-generalise`,
`make-the-proof-constructive`, `work-conditionally-then-discharge`, whose
undischarged hypothesis becomes an argument of the final theorem, and
`attack-the-negative-side` with a finite witness. Precedes nothing: no
strategy moves a certified statement further, so it closes the
composition. Excludes nothing: a mode change follows any earlier change.

## Common mistakes

- `sorry` left in, the axiom list never printed, or a native evaluation
  taken as a proof: a warning build filed as a certificate. Check:
  `result.json` carries the harness's list; `sorryAx` fails the step, and
  a compiler axiom of section 5 relabels the unit computational evidence.
- A theorem weaker than the claim: a smaller domain, a missing quantifier,
  a rounded constant, a redefined notion. Check: step 2's reading against
  the claim sentence, repeated at cash-out by a second reader.
- An entry dispatched before the study record exists. Check:
  `journal add` refuses a move whose `study/verify-formally-with-lean4.md` is missing
  or empty (`../harness/README.md`).
