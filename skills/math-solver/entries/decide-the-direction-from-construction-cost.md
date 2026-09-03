---
name: decide-the-direction-from-construction-cost
move_class: reformulate
costs: [object]
---

## Trigger

- The statement is an existence question for every value of a parameter ("for all N there is an object with parameter at least N"), so that a positive answer is an infinite sequence of constructions and a negative answer is one universal bound.
- A record of constructions exists, with the resource each one needed (number of pieces, size of the certificate, search time) known or recoverable.
- The prevailing expectation about the answer, if there is one, rests on no structural reason.

## Action

1. Tabulate the record values against the resource each needed.
2. Test whether any record extends: does the construction at one parameter value contain a pattern that produces the next value with resource growing at a fixed rate?
3. If the resource grows faster than any exponential and no record extends, take the negative answer as the working hypothesis and restate it as a positive lower bound on what every construction must leave out (uncovered points, unsatisfied constraints, an excess over the conjectured bound). A lower bound is something a sieve, a density argument, or a solver can produce; a bare negative is not.
4. If a record extends, take the positive answer as the working hypothesis and pass to seed-and-amplify.
5. If the record has stopped inside a reduced form that a known equivalence invites, look for the construction outside that form.
6. Enter the direction in the journal as an assumption with its evidence, never as a fact.

## Output form

A working direction (prove the universal bound, or build the sequence), and the restated target for that direction.

## Failure signal

The resource curve fits both a polynomial and an exponential over the known range; or one record extends while another appears to be a ceiling; or the restated target is of the same difficulty as the original statement.

## Typical cash-out

Counterexample or computational evidence (the record table with its constructions); problem paper.
