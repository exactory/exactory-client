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
| `journal.jsonl` | harness (`journal add`) | schema on write |
| `deterministic/<step>-<n>/` | agent, checked by harness | `verify` |
| `units/<n>/` | agent (stage 7) | `check-unit` |

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
  "compositions": [
    {"rank": 1, "id": "<strategy>+<strategy>", "strategies": ["...", "..."],
     "yes": 2, "unknown": 0,
     "components": ["stage", "statement"], "assumption": null}
  ]
}
```

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
 "trigger_features": ["shape.quantifiers"], "action": "...", "output": "...",
 "costs_paid": ["object"], "failure_signal_fired": false, "problem_changed": false}
```

`composition` is the id of a composition the ranking orders, which is the
strategies joined by `+` and survives a re-plan; a rank does not.
`costs_paid` holds what this move gave up, from the cost vocabulary.

## Commands

| command | does |
|---|---|
| `init <slug>` | creates the workspace with empty files and the shape keys pre-filled with `"unknown"` |
| `check-problem <slug>` | validates `problem.json`: every key present, no empty strings, quadruple values from the allowed sets |
| `plan <slug>` | validates `preconditions.json` against the strategy files, drops the strategies whose declared cost contradicts the quadruple and prints each exclusion the scan had not already ruled out, enumerates compositions under the rules in `../strategies/README.md`, writes `compositions.json`, prints the shortlist |
| `rank <slug>` | validates `ranking.json`: it orders exactly the current shortlist, each row citing a `problem.json` field or a cost, and prints the order |
| `journal add <slug> --json '<move>'` | validates the move against the schema and the current budget, appends it, prints the budget state |
| `budget <slug>` | prints moves used in this pass and overall, passes used, and whether a stall is due (three consecutive failure signals, pass exhausted, or hard cap) |
| `fail <slug> <strategy>` | sets the strategy's verdict to no with a note, stamps the journal length as `failed_after_move`, re-runs `plan` |
| `verify lean <slug> <step-dir>` | runs `lake build`, then reads `#print axioms` for the named theorem: the standard axioms give status `pass`, a native evaluation axiom gives `evidence`, `sorryAx` or a custom axiom gives `fail`; writes `result.json` with the status, the axioms list, and the reason |
| `verify certificate <slug> <step-dir>` | runs the step's `check.sh` (the independent checker the agent wrote), which must be executable, and writes `result.json` with `status` `pass` when it exits 0 and `fail` otherwise, the exit status, and the first lines of output |
| `stall <slug>` | writes the inventory skeleton (`units/INVENTORY.md`) listing every journal move, grouped by strategy, marking the ones whose failure signal fired, for the cash-out stage |
| `check-unit <slug> <n>` | validates that `units/<n>/unit.json` has statement, form (one of the seven publication forms plus `full-proof` and `second-proof`), evidence path that exists, novelty record, journal move numbers, and `costs`, the ledger its evidence carries |

Budget constants live at the top of the file: 8 moves per pass, 3
passes, 24 moves hard cap, stall after 3 consecutive failure signals.
The consecutive-failure window starts at the last `fail`, so ending one
strategy leaves the attack free to run the next composition.

## Composition enumeration

Read every `../strategies/*.md` front matter (name, component, precedes,
excludes, costs); a file missing a key, or naming a cost outside the
vocabulary, is refused with one line. Candidates are the strategies with
verdict yes or unknown whose declared costs the quadruple allows.
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
