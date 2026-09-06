---
name: formalise-while-fresh
move_class: compute
costs: [axioms, object, obligations]
---

## Trigger

- A proof is complete and is long, computer-dependent, or new enough that refereeing is the bottleneck for acceptance.
- The proof's dependency graph can be written as a blueprint of lemmas.

## Action

1. Write a human-readable blueprint whose lemma structure matches the intended formal development, and link each lemma to its formal counterpart.
2. Formalise in a proof assistant as a formal check per lemma, distributing lemmas across contributors. Admit each bounded check and route its exact declaration, requested type, source, dependencies, environment and toolchain through `../SEARCH.md#metered-execution-and-native-integration`; track the dependency graph until every node has a controlled terminal result, interpretation, and accepted policy review. Passing local nodes alone do not prove the dependency graph or root.
3. Correct the errors found, and record which were gaps and which were slips.
4. State the formalised theorem exactly, with its numerical constants.

## Output form

A machine-checked proof and a blueprint.

## Failure signal

The formalisation exposes a gap that no local repair closes (then the proof was incomplete and the attack resumes at that lemma); or the effort exceeds what the result warrants.

## Typical cash-out

A formalisation as a standalone unit; survey or exposition (the blueprint).
