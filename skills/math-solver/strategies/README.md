# Strategies: proof-design decisions

The entries under `../entries/` are moves. A strategy is one
level up: a decision about which part of the attempt to change. Every
attempt on an open proposition is a quadruple, and every strategy moves
one or two of its components.

| component | what it is |
|---|---|
| statement | the proposition being proved |
| stage | the language, category, or model the objects live in |
| direction | proving it true, proving it false, or proving it unreachable |
| mode | what counts as a proof: existence, construction, computation, certificate |

A fifth kind, organisation, changes how the work is arranged rather than
the attempt itself.

## What a move costs

A move buys progress with something. What it spends is a property of the
tool, not of the problem: each entry declares under `costs` in its front
matter what one application of it can take away, the same set whichever
strategy dispatches it. A strategy declares under its own `costs` at
least the union over the entries it dispatches, plus what its framing
adds at a step (a rung, an image, a model in place of the claim), and
its What it moves section says which cost is paid at which step and why.
The vocabulary is fixed and the harness code owns it.

| cost | what the move gives up |
|---|---|
| implication | only one direction is proved, so the claim can no longer be refuted this way |
| effectivity | a constant stops being computable |
| constructivity | existence is proved without an example |
| bound_quality | the bound's type degrades |
| axioms | the argument borrows strength beyond the recorded base theory |
| object | the statement proved is no longer the original one |
| obligations | the move adds statements that must themselves be proved |

A declared cost is what a move under the strategy can take away, not what
every move does, so it never drops the strategy. The contradiction is
between a cost actually paid and what the attack requires, and the harness
catches it at the move that pays:

- a move paying `constructivity` when `quadruple.mode` is `construction`;
- a move paying `implication` when `quadruple.direction` is `false`.

`journal add` refuses such a move. A strategy that can pay a cost the
attack cannot afford is still usable at every step that does not pay it,
which is why the gate sits on the move and not on the shortlist.

The two costs whose bearing the code cannot read from an enum are gated by
the strategy's own precondition instead: a strategy declaring `effectivity`
carries a required question citing `shape.target_quantity`, and one
declaring `axioms` carries a required question citing `shape.base_theory`.

A change in quantifier order or in uniformity is an `object` cost: the
statement proved is no longer the one the attack started from.

## The strategies

| strategy | moves | precondition, in one line |
|---|---|---|
| reduce-and-translate | statement, stage | the destination already has tools the origin lacks |
| ladder-the-parameter | statement | a continuous progress parameter exists between the trivial and the ideal value |
| strengthen-and-generalise | statement | a special feature of the statement is what obstructs the argument |
| solve-the-model-world-first | stage | a structurally parallel simpler setting exists with the obstruction removed |
| transfer-between-finitary-and-infinitary | stage | effective bounds can be given up, or must be recovered |
| transport-to-a-tractable-category | stage | a correspondence to another setting is known that reflects the property |
| attack-the-negative-side | direction | the space of counterexamples compresses to something searchable |
| split-structure-from-randomness | direction | a usable dual notion of "structured" can be written down |
| prove-the-barrier-first | direction | existing methods have failed uniformly and share an identifiable property |
| replace-existence-with-probability | mode | the claim is existential over a large finite or measurable space |
| reduce-to-a-finite-computation | mode | the statement finitises to instances a solver can refute or confirm |
| make-the-proof-constructive | mode | a search procedure for the object can be written |
| verify-formally-with-lean4 | mode | a lemma chain or finite check is stated precisely enough to encode |
| decompose-and-parallelise | organisation | progress is a single scalar |
| work-conditionally-then-discharge | organisation | a plausible strong hypothesis is available |

Each strategy is one file in this directory with the shape below. The
harness (`../SKILL.md`) runs every strategy's precondition procedure
against the problem record, admits the strategies whose preconditions
hold, and walks them one after another, each strategy dispatching its
entries under the move loop.

## The shape of a strategy file

Front matter, machine-read by the harness code:

```yaml
---
name: <verb-first kebab-case, equals the file name>
component: <statement | stage | direction | mode | organisation>
description: Use when <the problem features that make this the decision to consider>
entries: [<entry names this strategy dispatches, in the order it dispatches them>]
precedes: [<strategies that, when both appear in a composition, come after this one>]
excludes: [<strategies that cannot share a composition with this one>]
costs: [<what a move under this strategy can take away, from the vocabulary above>]
---
```

Sections, in this order:

1. **What it moves.** The decision, stated in the quadruple's terms: what
   the statement, stage, direction, or mode is before and after. Then one
   sentence per declared cost, saying at which step it is paid and what
   the record loses when it is. A strategy that declares nothing says so.
2. **Precondition procedure.** Numbered questions, each answered yes, no,
   or unknown from a named field of `problem.json` (a quadruple field
   `quadruple.<name>` or a shape field `shape.<name>`). Each question line
   ends with `(from: <field>; required)` or `(from: <field>; optional)`,
   and at least two questions are required. The verdict rule: yes when
   every required question is yes; unknown when a required answer is
   unknown and none is no; no otherwise. A question a solver cannot answer
   from the problem record is a defect.
3. **Plan.** The strategy's steps at its own grain, naming the entry
   dispatched at each step and what its output hands to the next step.
   The steps are what a solver does; the entries are how. Step 1 of
   every plan is the study of prior methods for this strategy on this
   problem and its neighbours, under the contract in `../STUDY.md`,
   producing `study/<strategy>.md`; the entries are dispatched from step
   2 on, and the study's constraints (a route already in print, a route
   that stopped and where, a settled range) shape which entry is
   dispatched first. A step dispatching part of an entry's action steps
   hands on the last dispatched step's result, not the entry's full
   Output. The harness refuses a move under a strategy whose study
   record is missing.
4. **Failure signal.** What ends the strategy in bounded moves, and what
   the record gains when it ends (a verdict of no for the precondition, a
   ceiling, a counterexample to the relaxed version).
5. **Cash-out.** Which forms from `../CASHOUT.md`
   the strategy's partial output takes when the full attack stalls.
6. **Composes with.** Which strategies it typically precedes or follows,
   and which it excludes, with the reason in the quadruple's terms.
7. **Common mistakes.** The ways the strategy is misapplied, each with
   the check that catches it.

The strategy files carry no problem names, person names, named theorems
as examples, or worked problems. Each file is between 400 and 1000 words. The description names triggering
features only, never the procedure.

Every entry under `../entries/` is dispatched by at least one
strategy. The few that the harness itself runs (the stall inventory, the
post-proof consolidation) are listed under `harness_entries` in the front
matter of `../SKILL.md` instead.

## Composition

A solution is a composition of strategies, not a single method chosen
from memory. The composition of an attack is its walk: the strategies it
runs, in order, each handed the output of the one before. The harness
does not choose a strategy; the precondition table prunes the space, and
the solver walks it one step at a time, reading the record as it stands.
The rules the harness code applies:

- The plan admits every strategy whose verdict is not no, and prints them
  as the openings: verdict yes before unknown, name order within, each
  with its component and its declared costs.
- The solver writes `ranking.json`: the same strategies, in the order it
  would open with them, each row citing the fields of the problem record
  and the costs that put it there. `rank` refuses a ranking that is not
  exactly the openings, or a row that cites nothing. The attack opens
  with the first of the order, and the ranking is not read after that.
  The order is the solver's judgement, because what a cost is worth
  depends on the problem: giving up effectivity is fatal when the target
  is an explicit constant and harmless when it is finiteness. A fixed
  weight for each cost would be wrong for one of those two.
- Each later step is the solver's choice among the admitted strategies,
  made when the current strategy ends and cited on the move that enters
  the new one (`step_cites`: the fields and the declared costs that put
  it next). A walk may return to a strategy it ran before, and it has no
  length cap; the move budget bounds it.
- Every strategy in the walk has verdict yes, except that one strategy
  with verdict unknown is allowed and the walk rests on that assumption.
- When strategy A lists B under `precedes`, A comes before B in any walk
  containing both, so A cannot enter once B has run. When A lists B under
  `excludes`, no walk contains both.
- A strategy whose failure signal fired has verdict no (`fail`) and takes
  no further move; the walk steps past it.

The precondition column of the table is where the decision procedure
lives. Enumerating strategies has limited value; pruning them has all of
it.
