---
name: test-independence-under-two-opposite-axioms
move_class: obstruct
---

## Trigger

- The statement is universal over uncountable objects or over all sets of reals, the countable or definable case is settled in the base theory, and no argument in either direction inside the base theory has appeared over a long period of attention.
- A hypothesis beyond the base theory is known to decide one direction (a continuum hypothesis or a constructibility principle yields a counterexample; an absoluteness or definability argument yields the statement for a restricted class), or a neighbouring statement of the same shape has been shown independent.
- The difficulty sits at the first uncountable cardinal: the way countable pieces attach along an uncountable index decides the property.

## Action

1. Pick the pair of hypotheses: a guessing principle for the counterexample direction, and a forcing axiom or a forcing construction for the other, chosen so that each acts on the way the countable pieces attach.
2. Run diagonalise-against-every-candidate-under-a-guessing-principle for the negation.
3. Run adjoin-the-wanted-object-by-generic-approximation for the statement, and, when the statement is universal over candidates that keep appearing, iterate-the-construction-and-bookkeep-every-candidate.
4. When both directions succeed, state the independence over the base theory. Pair each direction with an inner-model direction already in the literature to read off its consistency strength: whether a large cardinal is needed, and for which half.
5. When one direction succeeds and the other fails, record the conditional result and mark the other direction as the open half, with the lemma at which it failed.

## Output form

An independence theorem: the statement is neither provable nor refutable in the base theory, with the hypothesis each direction used and the consistency strength of each; or a one-directional conditional result with the open half named.

## Failure signal

Both directions fail at the same lemma, which is then a candidate theorem of the base theory and the attack should pursue that direction directly; or the two constructions need incompatible side hypotheses (hand it to vary-the-side-parameter-the-construction-fixed).

## Typical cash-out

Barrier (independence: the admissible class is every proof from the base theory, and the two models are the near misses); conditional or special-case result (one direction).
