---
name: reduce-the-counterexample-to-a-combinatorial-principle
move_class: reduce
costs: [axioms, implication, obligations]
---

## Trigger

- The statement is universal over objects of a fixed uncountable size (linear orders, abelian groups, sets of reals) whose countable substructures all have the property, so a counterexample is an assembly of countable pieces along an uncountable index and its property is decided by how the pieces attach.
- The attachment pattern of a candidate counterexample defines a combinatorial object of a simpler kind: a tree of approximations, a colouring of a ladder system along the limit ordinals, a family of sets indexed by a stationary set.
- The base theory does not decide the statement, and the combinatorial objects of that kind have their own theory of existence and non-existence: they are the objects that guessing principles and forcing axioms act on.

## Action

1. From a hypothetical counterexample, extract the combinatorial object its attachment pattern defines, and prove that the property the statement denies corresponds to a property of that object (the tree has no uncountable branch or antichain, the colouring cannot be uniformised).
2. Prove the converse: from a combinatorial object with the property, assemble a counterexample, piece by piece along the index.
3. State the equivalence, and replace the target by the combinatorial principle: attack the principle by set-theoretic methods (test-independence-under-two-opposite-axioms), where the objects are simpler than the original ones.
4. After a consistency proof, abstract what the construction yields into a general principle or an axiom, and prove its consistency by the same construction; the principle then decides the original statement and its siblings uniformly.

## Output form

An equivalence between the original statement and a combinatorial principle about the attachment of countable pieces, and, after the attack on the principle, a general axiom the construction proves consistent.

## Failure signal

The extracted object does not determine the counterexample (the equivalence holds in one direction only, and the principle is weaker or stronger than the statement); or the principle is already known to be decided by the base theory in the direction that does not help.

## Typical cash-out

Reduction or equivalence; new machinery (the principle stated as an axiom).
