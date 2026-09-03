# attack.py

The deterministic harness for an attack workspace. `SPEC.md` is the
contract; this file says how to run it, how to test it, and what the
commands do. Python 3.9 or later, standard library only.

## Run

`exactory-math` is on PATH while this plugin is enabled, and runs this
harness:

```sh
exactory-math <command> <slug> ...
```

Workspaces live under `attack/<slug>/` in the current directory. Two
global options, given before the command:

- `--strategies DIR`: the strategy files (default `../strategies/`,
  the sibling of the harness directory). A `.md` file without front
  matter, such as `README.md` or a reference note, is not a strategy.
- `--attack-root DIR`: where `<slug>/` workspaces live (default `attack`).

`exactory-math skill-dir` prints the directory that holds the skill's own
files: the strategies, the entries, the study contract, and the sources.

Validation problems go to stderr, one line each, with exit status 1.

## Test

From the plugin root:

```sh
python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness
```

144 tests, one module per command or pure function. The strategy files
they read are the fixtures under `tests/fixtures/strategies/` (five
strategies, one `precedes` and one `excludes` relation, plus two files
without front matter that the loader skips). The verification tests put
`tests/fixtures/bin/lake`, a fake `lake`, first on `PATH`, and write
their own `check.sh`; no Lean toolchain is needed for them. One
integration test, `tests/test_verify_lean_smoke.py`, runs `verify lean`
on a copy of the real project under `fixtures/lean-smoke/` with
`~/.elan/bin` prepended to `PATH`; it is skipped when `lake` is not
found.

## Commands

| command | does |
|---|---|
| `init <slug>` | creates `attack/<slug>/` with `problem.json` (shape keys set to `"unknown"`), empty `novelty.md` and `journal.jsonl`, and `study/`, `deterministic/`, and `units/`; refuses to overwrite |
| `check-problem <slug>` | validates `problem.json`: every key present, no empty strings, `direction` and `mode` from the allowed sets; prints `problem.json: ok` |
| `plan <slug>` | validates `preconditions.json` against the strategy files and `problem.json`, drops the strategies whose declared cost contradicts the quadruple, writes `compositions.json`, prints the shortlist (at most 20); refuses to run while `study/problem.md` is missing or empty |
| `rank <slug>` | validates `ranking.json` against the current shortlist and prints the order the solver chose |
| `journal add <slug> --json '<move>'` | validates the move's fields, its `composition` against `ranking.json`, its `costs_paid` against the vocabulary, and the budget, appends it, prints the budget state; refuses a move whose `study/<strategy>.md` is missing or empty |
| `budget <slug>` | prints moves used in this pass and overall, passes used, and whether a stall is due |
| `fail <slug> <strategy>` | sets the strategy's verdict to `no` with a `note` and a `failed_after_move` stamp, then runs `plan` |
| `verify lean <slug> <step-dir>` | in `deterministic/<step-dir>/`: `lake build`, then `#print axioms` on the theorem named in `step.json`; writes `result.json` |
| `verify certificate <slug> <step-dir>` | runs `deterministic/<step-dir>/check.sh`, refusing it when it is not executable, and writes `result.json` with `status` `pass` on exit 0 and `fail` otherwise, the exit status, and the first 20 output lines |
| `stall <slug>` | writes `units/INVENTORY.md`: every move, grouped by strategy, marking the ones whose failure signal fired and what each paid, with the whole ledger summed at the top |
| `check-unit <slug> <n>` | validates `units/<n>/unit.json`: `statement`, `form`, `evidence` (a path relative to the workspace that exists), `novelty`, `moves` (journal move numbers), `costs` (the ledger the evidence carries) |

Budget constants at the top of the file: 8 moves per pass, 3 passes,
24 moves hard cap, stall after 3 consecutive failure signals. A stall is
due when the last three moves since the last `fail` all fired their
failure signal, when pass 3 has used its 8 moves, or when 24 moves are
used; `journal add` rejects
a move while a stall is due, and rejects a move in a pass that has used
its 8 moves (the next move starts the next pass).

Points where the spec left a choice, and what the code does:

- `fail` writes `"verdict": "no"`, a `note`, and `failed_after_move`,
  the journal length at that moment. The verdict rule says a `no` needs
  one required answer `no`; a record with a `note` is exempt, because its
  `no` came from execution, not from the answers. The stamp starts the
  consecutive-failure window, so ending a strategy does not end the
  attack.
- The verdict reads the questions the strategy file marks `required` and
  ignores the ones it marks `optional`. A record answers every question
  the file asks and no other, and carries only `verdict`, `answers`,
  `note`, and `failed_after_move`.
- `verify lean` applies the decision rule of
  `../strategies/references/lean4.md` section 4, which the spec names as
  its reference: `sorryAx` or a custom axiom fails the step; a native
  evaluation axiom (`Lean.trustCompiler`, `Lean.ofReduceBool`,
  `Lean.ofReduceNat`, or `<decl>._native.native_decide.ax_<k>`) gives
  `result.json` the status `evidence` with exit status 0; only the
  standard axioms give `pass`. Exit status 1 means `fail`.
- The unit forms `check-unit` accepts are the seven publication forms in
  kebab-case, the four standalone units (`counterexample`, `algorithm`,
  `formalisation`, `formal-proof-write-up`), and `full-proof` and
  `second-proof`.

## An end-to-end run

Recorded on 2026-09-01 against `../strategies/`, which then held one
strategy file (`verify-formally-with-lean4.md`), from a scratch directory.
`problem.json` and `preconditions.json` were written by hand between the
commands; the `[exit n]` lines are the exit statuses.

```
$ exactory-math init parity-of-consecutive-product
created attack/parity-of-consecutive-product
[exit 0]
$ exactory-math check-problem parity-of-consecutive-product
problem.json: ok
[exit 0]
$ exactory-math plan parity-of-consecutive-product
1. verify-formally-with-lean4  yes=0 unknown=1 components=mode assumption=verify-formally-with-lean4
[exit 0]
$ exactory-math journal add parity-of-consecutive-product --json '{"move": 1, "pass": 1, ...}'
moves this pass: 1/8
moves overall: 1/24
passes used: 1/3
stall due: no
[exit 0]
$ exactory-math journal add parity-of-consecutive-product --json '{"move": 2, "pass": 1, ...}'
moves this pass: 2/8
moves overall: 2/24
passes used: 1/3
stall due: no
[exit 0]
$ exactory-math budget parity-of-consecutive-product
moves this pass: 2/8
moves overall: 2/24
passes used: 1/3
stall due: no
[exit 0]
$ exactory-math stall parity-of-consecutive-product
wrote units/INVENTORY.md (2 moves stand)
[exit 0]
$ cat attack/parity-of-consecutive-product/units/INVENTORY.md
# Inventory: parity-of-consecutive-product

Every journal move whose output stands, grouped by strategy. Convert each
into a unit under CASHOUT.md or discard it.

## verify-formally-with-lean4

- move 1 (pass 1, formalise-while-fresh): blueprint with explicit hypotheses per lemma
- move 2 (pass 1, certify-the-finite-residue-by-computation): formal-check-1 laid out; not yet run
```

The `preconditions.json` used gave `verify-formally-with-lean4` the
verdict `unknown` (questions 1, 2, 3, 5 answered `yes`, question 4
`unknown`), so the one composition rests on an assumption, as the
`plan` line shows.
