---
name: certify-the-finite-residue-by-computation
move_class: compute
---

## Trigger

- The remaining step of a proof is finite but large: a case analysis over a classifiable family, a finite list of explicit nonlinear inequalities on compact ranges, a finite range of parameters against a criterion, or inequalities on a half-line reducible to a compact range plus a tail estimate.
- Or a computer-found witness or certificate exists and is too large for independent tools or for a human to check.

## Action

1. Reduce the analytic residue to inequalities on compact ranges with explicit tail estimates, then prove each by a certified special-case check (interval arithmetic in two independent implementations); when the residue is a statement over an interval of the parameter, cover the interval by a certified witness chain.
2. Enumerate the finite family by an enumeration run whose trusted base is small and published; recast the completeness of the enumeration as a formal check when its completeness is in question.
3. Replace each nonlinear local problem by a linear relaxation, solved as a numerical optimisation run, and split by branch and bound only where the relaxation fails.
4. Organise the computation as a pipeline in which every phase emits its own checkable proof: an algebraic phase as a symbolic computation run, an enumerative phase as an enumeration run, an analytic phase as a certified special-case check. Design around the size of the certificate: a compact certificate that regenerates the proof beats a huge stored one.
5. Shrink the witness: use the solver's proof artifact to decide which components to keep, exploit symmetry orbits, and iterate until the independent checker of the enumeration run or the counterexample search run certifies it. Prefer standard solvers to custom verification code.
6. Isolate the single hardest case and give it a dedicated proof, as a formal check when the case is a finite check.

## Output form

A machine-checked proof of the finite residue with a published certificate, or a minimised witness with an independent verification.

## Failure signal

The case count grows past feasibility before the residue is covered; or the certificate cannot be regenerated or checked within the available resources; or the trusted base cannot be made small.

## Typical cash-out

Full proof by computation; a single counterexample or witness as a standalone unit; new machinery (the pipeline, reusable on the next problem of the same family).
