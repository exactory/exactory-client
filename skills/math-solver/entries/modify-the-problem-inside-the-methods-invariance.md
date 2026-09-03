---
name: modify-the-problem-inside-the-methods-invariance
move_class: obstruct
costs: [axioms, constructivity, object, obligations]
---

## Trigger

- Several different methods have failed, and they share an invariance: each would prove the same conclusion for a modified problem (relative to an oracle; for a version in which the operator is replaced by an average over a family of operators sharing its invariants; for an abstract set system satisfying the hypotheses the proofs use; for a model that keeps the scaling and the conserved quantity).
- The modified problem's answer can be determined.

## Action

1. Name the shared property of the known proofs precisely: they relativise; they use only the conserved quantity and the scaling; they use only the abstract axioms.
2. Define the class of modifications under which that property is invariant.
3. Exhibit a member of the class for which the conclusion fails: a world where the target is false, an averaged equation with a singular solution, an abstract structure that satisfies the proofs' axioms and has the opposite answer. Build it as an explicit object; seed-and-amplify supplies the construction when it must be engineered.
4. State exactly which existing arguments escape the barrier, and what a proof must use that the modification destroys.
5. Publish the barrier as a theorem, and convert the construction into a programme for the true problem.

## Output form

A barrier theorem: every argument with the named property proves the conclusion for the modified problem, where it is false.

## Failure signal

The modified problem has the same answer as the original, so the invariance does not separate them; or the property is not shared by a method actually in use; or the modification is so artificial that no method's invariance covers it.

## Typical cash-out

Barrier; counterexample (the modified problem's counterexample stands alone); new machinery.
