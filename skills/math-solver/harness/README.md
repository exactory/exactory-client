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

223 tests, one module per command or pure function, plus
`tests/test_journal_rules.py` for the flow rules `journal add` enforces.
The strategy files
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
| `init <slug> [--from <parent>]` | creates `attack/<slug>/` with `problem.json` (shape keys set to `"unknown"`), empty `novelty.md` and `journal.jsonl`, and `study/`, `deterministic/`, and `units/`; refuses to overwrite. With `--from`, opens it as a child of an open attack that is nobody's child, writing `parent.json` |
| `check-problem <slug>` | validates `problem.json`: every key present, no empty strings, `direction` and `mode` from the allowed sets; prints `problem.json: ok` |
| `plan <slug>` | validates `preconditions.json` against the strategy files and `problem.json`, writes `openings.json` with every strategy whose verdict is not `no` (yes before unknown, name order within, each with its component and declared costs), prints them; refuses to run while `study/problem.md` is missing or empty |
| `rank <slug>` | validates `ranking.json` against the openings (every one exactly once, each row citing a `problem.json` field or a cost the strategy declares) and prints the order the solver chose |
| `journal add <slug> --json '<move>'` | validates the move's fields, `problem.json`, the study record, the costs against the quadruple, the steps it ran, a closing move's direction and mode, the `problem_changed` flag against the problem digest, the strategy's verdict, the entry against the strategy, the trigger against the shape fields, the walk (the opening against the ranking, a step against the walk rules with its `step_cites`), and the budget (the flow rules in `SPEC.md`); appends it with the problem digest and prints the budget state |
| `budget <slug>` | prints moves used in this pass and overall, passes used, and whether a stall is due |
| `fail <slug> <strategy>` | sets the strategy's verdict to `no` with a `note` and a `failed_after_move` stamp, then runs `plan` |
| `verify lean <slug> <step-dir>` | in `deterministic/<step-dir>/`: `lake build`, then `#print axioms` on the theorem named in `step.json`; writes `result.json` |
| `verify certificate <slug> <step-dir>` | runs `deterministic/<step-dir>/check.sh`, refusing it when it is not executable, and writes `result.json` with `status` `pass` on exit 0 and `fail` otherwise, the exit status, and the first 20 output lines |
| `stall <slug>` | refuses while no cash-out rule holds; otherwise writes `units/INVENTORY.md`: the walk, then every move grouped by strategy, marking the ones whose failure signal fired, the one that closed the attack, and what each paid, with the whole ledger summed at the top, and names the rule |
| `check-unit <slug> <n>` | refuses before the inventory exists; validates `units/<n>/unit.json`: `statement`, `form`, `evidence` (a path relative to the workspace that exists, with a `result.json` when it is a deterministic run), `novelty`, `moves` (journal move numbers), `costs` (the ledger the evidence carries), and the form against the evidence and the ledger; writes `units/<n>/check-unit.json` on success |
| `finish <slug>` | refuses while any unit lacks a matching stamp, a `draft.md`, or an `evaluation.md`, and while a child attack is open; writes `units/FINISHED.json`. With no move and no inventory, records the stage 3 exit |
| `status <slug>` | prints where the attack stands, derived from the record, with the parent or the children when there are any, ending with the `next:` line a resumed session continues from |
| `task add <slug> <text>`, `task done <slug> <id>`, `task list <slug>` | the action list in `tasks.json`, each change stamped with the time and the move count |

Budget constants at the top of the file: 8 moves per pass, 3 passes,
24 moves hard cap, stall after 3 consecutive failure signals. A stall is
due when a move closed the attack, when the last three moves since the
last `fail` all fired their failure signal, when pass 3 has used its 8
moves, or when 24 moves are used; `journal add` rejects a move while a
stall is due, and rejects a move in a pass that has used its 8 moves (the
next move starts the next pass).

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
- The walk is read from the journal: consecutive moves under one strategy
  are one step of it, and a move under a different strategy from the
  previous move is a step into that strategy, whatever the strategy's
  history in the walk. The ranking is read on the first move only; a
  re-plan mid-walk rewrites `openings.json` and leaves the walk where it
  stands, so `rank` need not run again unless the attack has not opened.
- `problem_changed` is judged against a digest, so a move that rewrote
  `problem.json` to the same content counts as unchanged, and a move that
  touched only whitespace inside a value counts as changed.
- `finish` accepts a workspace with no unit after the inventory (nothing
  survived the claim test) and records it as cashed out with an empty
  list.
- `status` derives the stage from the files alone and never from
  `tasks.json` or `activity.jsonl`, which it only reports, so a stale task
  cannot move the stage.

## An end-to-end run

Recorded on 2026-09-02 against the fifteen strategy files under
`../strategies/`, with `--attack-root` pointing at a scratch directory
(its absolute path is shortened to `attack/` below). The files the agent
owns (`problem.json`, the study records, `preconditions.json`,
`ranking.json`, the step's `check.sh`, `unit.json`, `draft.md`,
`evaluation.md`) were written between the commands; the `[exit n]` lines
are the exit statuses. The claim is a textbook exercise, chosen so the
run exercises every command and not the mathematics.

```
$ exactory-math init parity-of-consecutive-product
created attack/parity-of-consecutive-product
[exit 0]
$ exactory-math check-problem parity-of-consecutive-product
problem.json: ok
[exit 0]
$ exactory-math status parity-of-consecutive-product
attack/parity-of-consecutive-product: stage 3 (study and novelty check)
problem: ok
study: problem.md missing; novelty.md empty
plan: not run
walk: none yet
budget: moves this pass 0/8, overall 0/24, passes 0/3, stall due: no
cash-out: inventory not written; 0 units; finished: no
tasks: none
activity: none recorded
next: write study/problem.md and novelty.md, then preconditions.json, and run plan
[exit 0]
$ exactory-math plan parity-of-consecutive-product
1. reduce-to-a-finite-computation  verdict=yes component=mode costs=axioms,bound_quality,effectivity,implication,object,obligations
2. verify-formally-with-lean4  verdict=yes component=mode costs=axioms,object,obligations
[exit 0]
$ exactory-math rank parity-of-consecutive-product
1. reduce-to-a-finite-computation
2. verify-formally-with-lean4
[exit 0]
$ exactory-math task add parity-of-consecutive-product reduce the claim to the two residues mod 2
task 1 added
[exit 0]
$ exactory-math task add parity-of-consecutive-product check both residues by an enumeration run
task 2 added
[exit 0]
$ exactory-math journal add parity-of-consecutive-product --json '{...}'
moves this pass: 1/8
moves overall: 1/24
passes used: 1/3
stall due: no
[exit 0]
$ exactory-math task done parity-of-consecutive-product 1
task 1 done
[exit 0]
$ exactory-math stall parity-of-consecutive-product
stall: no rule started the cash-out (stall due: no; 2 openings admitted); continue at stage 5
[exit 1]
$ exactory-math verify certificate parity-of-consecutive-product enumeration-run-1
pass: check.sh exited 0
[exit 0]
$ exactory-math journal add parity-of-consecutive-product --json '{...}'
moves this pass: 2/8
moves overall: 2/24
passes used: 1/3
stall due: yes (the attack closed at move 2)
[exit 0]
$ exactory-math task done parity-of-consecutive-product 2
task 2 done
[exit 0]
$ exactory-math status parity-of-consecutive-product
attack/parity-of-consecutive-product: stage 7 (cash out)
problem: ok
study: problem.md present; novelty.md present
plan: 2 openings admitted
ranking: ok
walk: reduce-to-a-finite-computation -> verify-formally-with-lean4 (2 moves, last move 2 in pass 1)
budget: moves this pass 2/8, overall 2/24, passes 1/3, stall due: yes (the attack closed at move 2)
cash-out: inventory not written; 0 units; finished: no
tasks: 0 open, 2 done
activity: none recorded
next: run stall; the rule is the attack closed at move 2
[exit 0]
$ exactory-math stall parity-of-consecutive-product
wrote units/INVENTORY.md (2 moves, 0 ended in a failure signal); rule: the attack closed at move 2
[exit 0]
$ exactory-math finish parity-of-consecutive-product
units/1: not checked; run check-unit
units/1/draft.md: missing or empty
units/1/evaluation.md: missing or empty
[exit 1]
$ exactory-math check-unit parity-of-consecutive-product 1
units/1/unit.json: ok
[exit 0]
$ exactory-math finish parity-of-consecutive-product
finished parity-of-consecutive-product: 1 unit stands
[exit 0]
$ exactory-math status parity-of-consecutive-product
attack/parity-of-consecutive-product: finished (cashed-out)
problem: ok
study: problem.md present; novelty.md present
plan: 2 openings admitted
ranking: ok
walk: reduce-to-a-finite-computation -> verify-formally-with-lean4 (2 moves, last move 2 in pass 1)
budget: moves this pass 2/8, overall 2/24, passes 1/3, stall due: yes (the attack closed at move 2)
cash-out: inventory written; 1 unit (complete); finished: yes
tasks: 0 open, 2 done
activity: none recorded
next: nothing; the attack is finished
[exit 0]
```

The `preconditions.json` used gave `reduce-to-a-finite-computation` and
`verify-formally-with-lean4` the verdict `yes` and every other strategy
`no`, so the plan admits those two openings. Move 1 opened the walk under
the first of the ranking with `steps` empty; move 2 stepped into the
second strategy, with `walk` extended by it and `step_cites` naming two
shape fields, named `enumeration-run-1`, which `verify certificate` had
passed, and carried `closes` true, which is what made the second `stall`
accept. The first `stall` and the first `finish` show the refusals, with
every problem listed at once. `activity` stays "none recorded" because
the plugin's hook, not the harness, writes that log.
