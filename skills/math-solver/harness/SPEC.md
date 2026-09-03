# Harness code: `attack.py`

The deterministic part of the harness. It owns the attack workspace's
files, validates what the agent writes into them, enumerates strategy
compositions from the precondition table, enforces the move budget, and
runs the certificate checks. It contains no mathematics and no judgment
about the problem; every judgment is written by the agent into a file
the harness validates and reads.

Location: `attack.py`, Python 3.9+, standard library only.
Tests: `tests/`, run from the plugin root with
`python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness`.
Written test-first.

## Workspace

`attack/<slug>/` holds:

| file | written by | validated by |
|---|---|---|
| `problem.json` | agent (stage 2) | `check-problem` |
| `novelty.md` | agent (stage 3) | no command; `check-unit` requires a non-empty `novelty` field on each unit |
| `study/problem.md`, `study/<strategy>.md` | agent (stage 3, and step 1 of each strategy) | `journal add` refuses a move under a strategy whose `study/<strategy>.md` is missing or empty; `plan` refuses to run without `study/problem.md` |
| `preconditions.json` | agent (stage 4) | `plan` |
| `compositions.json` | harness (`plan`) | |
| `ranking.json` | agent (stage 4b) | `rank`; `journal add` refuses a move while it does not order exactly the current shortlist |
| `journal.jsonl` | harness (`journal add`) | schema and flow rules on write |
| `deterministic/<step>-<n>/` | agent, checked by harness | `verify`, which writes `result.json` there |
| `units/INVENTORY.md` | harness skeleton (`stall`), agent below it (stage 7) | `check-unit` and `finish` refuse to run without it |
| `units/<n>/unit.json` | agent (stage 7) | `check-unit`, which writes `units/<n>/check-unit.json` when it passes |
| `units/<n>/draft.md`, `units/<n>/evaluation.md` | agent (stage 8) | `finish` |
| `units/FINISHED.json` | harness (`finish`) | |

The files the harness writes (`compositions.json`, `journal.jsonl`,
`result.json`, `check-unit.json`, `FINISHED.json`) are written by their
commands only; the plugin's hooks refuse an edit to them from any other
tool.

## Formats

`problem.json`:

```json
{
  "claim": "one sentence, every quantifier explicit",
  "quadruple": {
    "statement": "the proposition as attacked now",
    "stage": "the setting the objects live in",
    "direction": "true | false | unreachable | undecided",
    "mode": "existence | construction | computation | certificate | undecided"
  },
  "shape": {
    "objects": "...",
    "quantifiers": "...",
    "target_quantity": "...",
    "ambient_structure": "...",
    "symmetries": "...",
    "configuration": "...",
    "extremal_candidate": "...",
    "finite_certificates": "...",
    "monotonicity": "...",
    "uniformity_parameter": "...",
    "proof_shape": "...",
    "neighbours": "...",
    "known_bounds": "...",
    "missing_input": "...",
    "base_theory": "..."
  },
  "known": ["one line per known result, with a reference"]
}
```

Every shape key is required; an unknown value is the literal string
`"unknown"`, never an empty string, so that a precondition answered from
it is `unknown` rather than a guess.

`preconditions.json`, one object per strategy file found under
`../strategies/`:

```json
{
  "ladder-the-parameter": {
    "verdict": "yes | no | unknown",
    "answers": [
      {"question": 1, "answer": "yes", "cites": "shape.target_quantity"},
      {"question": 2, "answer": "unknown", "cites": "shape.known_bounds"}
    ]
  }
}
```

`plan` rejects the file when a strategy is missing, a cited field does
not exist in `problem.json`, or a verdict contradicts its answers under
the verdict rule. The rule reads the questions the strategy file marks
`required` and ignores the ones it marks `optional`: yes requires every
required answer yes; unknown requires no required answer no and at least
one required unknown; no requires at least one required no, except that a
record carrying a `note` is exempt because its `no` came from `fail`. The
record answers every question the strategy file asks and no other, and
carries only the keys `verdict`, `answers`, `note`, and
`failed_after_move`.

`compositions.json`, written by `plan`:

```json
{
  "generated_from": "preconditions.json",
  "problem_digest": "sha256 of problem.json as plan read it",
  "compositions": [
    {"rank": 1, "id": "<strategy>+<strategy>", "strategies": ["...", "..."],
     "yes": 2, "unknown": 0,
     "components": ["stage", "statement"], "assumption": null}
  ]
}
```

`problem_digest` is the SHA-256 of `problem.json` serialised with sorted
keys and no whitespace. `journal add` compares it with the digest of the
problem as it stands when a new pass starts.

`ranking.json`, written by the agent over the shortlist `plan` emitted:

```json
{
  "generated_from": "compositions.json",
  "order": [
    {"composition": "<strategy>+<strategy>", "cites": ["shape.target_quantity", "cost:effectivity"],
     "reason": "one sentence"}
  ]
}
```

Every composition of the current `compositions.json` appears exactly once
and nothing else does. A citation is a dotted path that exists in
`problem.json`, or `cost:<name>` with the name in the cost vocabulary.

`journal.jsonl`, one line per move:

```json
{"move": 7, "pass": 1, "composition": "<strategy>+<strategy>", "strategy": "...", "entry": "...",
 "trigger_features": ["shape.quantifiers"], "action": "...", "steps": ["enumeration-run-1"],
 "output": "...", "costs_paid": ["object"], "failure_signal_fired": false,
 "problem_changed": false, "closes": false,
 "problem_digest": "sha256 of problem.json as the move left it"}
```

The agent supplies every field but `problem_digest`, which `journal add`
computes and appends; a move that carries it is refused as an unknown
field. `composition` is the id of a composition the ranking orders, which
is the strategies joined by `+` and survives a re-plan; a rank does not.
`costs_paid` holds what this move gave up, from the cost vocabulary.
`steps` names the directories under `deterministic/` the move ran, each of
which holds a `result.json`. `closes` marks the move whose output is a
proof of the claim or a counterexample to it.

## Flow rules

`journal add` refuses a move unless all of these hold, in this order:

1. The schema: every field present, none unknown, each of its type,
   every cost in the vocabulary.
2. `problem.json` passes `check-problem`.
3. `study/<strategy>.md` exists and is non-empty.
4. No cost paid contradicts the quadruple under `COST_GATES`.
5. Every step named exists under `deterministic/` and holds a
   `result.json`.
6. A closing move finds `quadruple.direction` and `quadruple.mode`
   decided.
7. `problem_changed` equals whether the digest of `problem.json` differs
   from the previous move's digest (or, for the first move, from the
   digest `plan` recorded); and the first move of a new pass finds
   `compositions.json` written over the problem as it now stands.
8. `ranking.json` orders exactly the current shortlist.
9. The composition is in the ranking; the strategy is in the composition;
   the entry is one the strategy's front matter lists; `trigger_features`
   is non-empty and names `problem.json` fields whose value is not
   `unknown`; the composition is the current one (the previous move's,
   or the first of the order carrying a strategy with no move yet); every
   strategy before this one in the composition has a move; and the
   composition has not moved past this strategy.
10. The budget: the move number is the next, the pass is the current or
    the next, the pass is not spent, and no stall is due.

The stall reasons, in the order checked: the last move closed the attack;
24 moves used; the last pass spent; three consecutive fired failure
signals since the last `fail`.

## Commands

| command | does |
|---|---|
| `init <slug>` | creates the workspace with empty files and the shape keys pre-filled with `"unknown"` |
| `check-problem <slug>` | validates `problem.json`: every key present, no empty strings, quadruple values from the allowed sets |
| `plan <slug>` | validates `preconditions.json` against the strategy files, enumerates compositions under the rules in `../strategies/README.md` over every strategy whose verdict is not no, writes `compositions.json`, prints the shortlist |
| `rank <slug>` | validates `ranking.json`: it orders exactly the current shortlist, each row citing a `problem.json` field or a cost, and prints the order |
| `journal add <slug> --json '<move>'` | validates the move under the flow rules above, appends it with the problem digest, prints the budget state |
| `budget <slug>` | prints moves used in this pass and overall, passes used, and whether a stall is due (a closing move, hard cap, last pass spent, or three consecutive failure signals) |
| `fail <slug> <strategy>` | sets the strategy's verdict to no with a note, stamps the journal length as `failed_after_move`, re-runs `plan` |
| `verify lean <slug> <step-dir>` | runs `lake build`, then reads `#print axioms` for the named theorem: the standard axioms give status `pass`, a native evaluation axiom gives `evidence`, `sorryAx` or a custom axiom gives `fail`; writes `result.json` with the status, the axioms list, and the reason |
| `verify certificate <slug> <step-dir>` | runs the step's `check.sh` (the independent checker the agent wrote), which must be executable, and writes `result.json` with `status` `pass` when it exits 0 and `fail` otherwise, the exit status, and the first lines of output |
| `stall <slug>` | refuses while no cash-out rule holds (a stall is due, or the plan emitted no composition); otherwise writes the inventory skeleton (`units/INVENTORY.md`) listing every journal move, grouped by strategy, marking the ones whose failure signal fired and the one that closed the attack, and names the rule |
| `check-unit <slug> <n>` | refuses before the inventory exists; validates that `units/<n>/unit.json` has statement, form (one of the seven publication forms plus `full-proof` and `second-proof`), evidence path that exists (with a `result.json` when it is a deterministic run), novelty record, journal move numbers, and `costs`, the ledger its evidence carries; refuses a form the evidence or the ledger rules out (a run that did not pass is evidence; a closing form carries neither `object` nor `obligations`; `algorithm` and `counterexample` carry no `constructivity`); writes `check-unit.json` with the digest of the record on success and removes any stamp first |
| `finish <slug>` | refuses before the inventory exists, and while any `units/<n>/` lacks a stamp matching its `unit.json`, a non-empty `draft.md`, or a non-empty `evaluation.md`; writes `units/FINISHED.json` with the unit numbers. With no move and no inventory it is the stage 3 exit: it needs a non-empty `study/problem.md` and `novelty.md` and records the outcome `solved-in-literature` |

Budget constants live at the top of the file: 8 moves per pass, 3
passes, 24 moves hard cap, stall after 3 consecutive failure signals.
The consecutive-failure window starts at the last `fail`, so ending one
strategy leaves the attack free to run the next composition.

## Composition enumeration

Read every `../strategies/*.md` front matter (name, component, precedes,
excludes, costs); a file missing a key, or naming a cost outside the
vocabulary, is refused with one line. Candidates are the strategies with
verdict yes or unknown. A declared cost never removes one here; it is
what a move under the strategy can take away, not what every move does,
and `journal add` refuses the move that pays it.
Enumerate ordered selections of length 1 to 4 without repetition; keep a
selection when it contains at most one unknown, violates no `excludes`
pair, and for every pair (A before B in the selection) B does not list A
under `precedes`. Rank by (yes count desc, distinct components desc,
name order). Emit at most 20, chosen by round over the leading strategy:
the best composition led by each strategy, then the second best led by
each, and so on, printed in rank order. A plain cut leaves the
alphabetically first leader holding every slot and the other candidates
unreachable through a list the solver takes in rank order. A list that
fits under the cap is emitted unchanged.

A selection that differs only by order from another and satisfies no
`precedes` constraint between its members is a duplicate in meaning but
not in execution; both are kept, since order is what the agent executes.

## Lean verification

`verify lean` expects the step directory to be a Lean project (a
`lakefile.lean` or `lakefile.toml` and a `lean-toolchain` file) with a
file `Main.lean` (or the file named in `step.json`) declaring the
theorem named in `step.json` (`{"theorem": "name"}`). It runs
`lake build`, then `lake env lean --run` is not used; instead it appends a
temporary file with `#print axioms <name>` and runs `lake env lean` on it,
parses the output and classifies it by the decision rule of section 4 of
`../strategies/references/lean4.md`: `sorryAx` or a custom axiom fails, a
native evaluation axiom gives `evidence`, the standard axioms give
`pass`. Exact commands and their
official documentation are recorded in
`../strategies/verify-formally-with-lean4.md` and its reference `../strategies/references/lean4.md`, which are written from the
vendor's documentation and is the reference `attack.py` follows.
