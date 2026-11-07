# Harness code: `attack.py`

The deterministic part of the harness. It owns the attack workspace's
files, validates what the agent writes into them, lists the strategies
the precondition table admits, holds the walk to its rules, enforces the
move budget, runs the certificate checks, keeps the action list, and
reports where the attack stands. It contains no mathematics and no
judgment about the problem; every judgment is written by the agent into a
file the harness validates and reads.

Location: `attack.py`, Python 3.9+, standard library only.
Tests: `tests/`, run from the plugin root with
`python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness`.
Written test-first.

## Managed objective boundary

This document specifies the native per-node record. Fresh mathematical objectives
are owned by the search controller whose closed schemas and public commands are in
`../SEARCH.md`. Reviewed `search admit` creates each managed native workspace. When
a proposal has `native_parent`, that same admission creates `parent.json`; legacy
`init --from` refuses inside a managed objective and must not be run again.

Before a managed `journal add`, `search begin` reserves the exact move. Managed
generic computations use `search run`; managed legacy certificate and Lean
verification commands route through the same frozen executor after an independent
input review. Native journal bytes, `result.json`, unit stamps, and `FINISHED.json`
remain unchanged local facts. None is controller acceptance or root completion.

Fresh result acceptance uses native result review version 2. A resolved openness
finding (`supported_open` or `already_solved`) needs the `OpennessMinimum` defined
in `../SEARCH.md`: exact `statement`, declared `neighbours`, bound common searches
with actual `coverage` and `captured_at`, pinned `reading_digests` establishing
full reading for every relevant or contradictory result, and `settling_work_id`
for `already_solved`. The observed packet delivers the common records and source
bytes. Missing, unavailable or deferred required evidence leaves `unresolved`;
proof validity may be accepted separately, while root closure stays pending.
Fresh resolved findings must include other material searches of those formulations.
This does not reinstate a common preparation gate for research. Requested formal
verification and deliverables remain required after `already_solved`. Historical
version 1 acceptances and closures replay under their recorded rules.
Both an earlier uncertain hit and its explicit later `resolved` search judgment
stay bound. `out_of_scope` removes that hit's reading obligation. Each `duplicate`
has a `minimum.duplicate_resolutions` binding (`search_digest`, `work_id`,
`canonical_work_id`) to a `relevant` or `contradictory` source in that same search;
that decisive canonical source needs full reading. Decisive judgments retain
their obligations; a missing canonical binding or reading is `openness_reading_gap`.

The observed packet is read complete. On a text route with a local input limit
(`claude_cli_v1`, `codex_cli_v1` or `codex_cli_framed_v1`), a packet whose
captured responses, full texts and records do not fit one input complete, or
together exceed 120,000 characters, is read as a reading chain
(`../SEARCH.md#observed-native-review-delivery`). The service stores the chain's
derived `reading_turns` on the acceptance or closure and compares them in every
audit of that record.

Native evidence import records original and canonical content identities and
resolves shared exploratory probe origins in the associated common store.
Fresh checkpoint, admission, strategy, computation and acceptance dependency
audits refuse exploratory proof, counterexample or decision credit regardless of
artifact role, analytical classification or JSON wrapper. A probe-derived native
certificate or Lean proof requires admitted verification and a candidate
interpretation; historical accepted imports replay. Exploratory probes remain
outside native runs and reservations, with no native credit. Their actual `id`,
`question` and `elapsed_seconds` belong in `units/INVENTORY.md`.
JSON imports, plan snapshots and native problem pinning retain original bytes
alongside canonical content. Fresh content commits append their aliases through
`probe_origins_recorded`. Export publishes
the checked `native_import_identity` binding (`schema_version: 1`,
`canonical_digest`, `raw: ArtifactRef`) before canonical bytes and includes
`raw_identities: [ArtifactRef]` in the exported artifact reference. Imports and
fresh audits verify the alias bytes and resolve shared probe origins through
them across both native JSON encodings and every checked alias in the chain,
including origins recorded later. Export flattens the chain into common bindings
for other importers. A missing raw identity requires the
original artifact bytes. Assessment and historical replay publish nothing;
recorded packets remain readable.
An interrupted export may recover missing common bytes from exact staged or
native originals after checking the committed raw descriptor. This read-only
fallback never publishes during assessment.

## Common-store attempt delivery

`search deliver-attempt` exports `native_attempt` evidence without requiring
closure; see `../SEARCH.md`, Common-store delivery. Its totals are exactly
`{moves, runs, elapsed_seconds, workers, timeout_seconds, historical_usage}`.
Moves and runs are consumed totals; elapsed duration is null. Workers and
`timeout_seconds` are the largest admitted limits across nodes with an admission,
or zero without one. Historical usage remains as recorded. The record adds its
first common-store `recorded_revision` outside the summary digest; repeats retain
that revision. Current succession is keyed
by `(attack_root, objective_id)` and declared by the mutable pointer
`native_attempt_current/<digest({attack_root, objective_id})>` containing
`{attack_root, objective_id, native_attempt_digest, recorded_revision}`. Attempt
delivery updates this pointer through the common prepared mutation whenever it
names another digest or is absent. Re-delivering an earlier digest moves the
pointer without adding or rewriting an immutable snapshot; an already current
repeat writes nothing. Pointer-only updates use
`native-attempt:<digest(summary)>:current:<next-common-revision>` rather than
replaying the first delivery's content receipt. Closure delivery writes no pointer.
Both attempt and closure delivery freshly audit acceptance evidence, including
on repeats and after competing common writes; `audit_failed` details retain the
failures map keyed by acceptance id.

## User dispositions (0.80.0)

The CLI adds `search set-aside CHECKPOINT --spec FILE` and
`search end-objective --spec FILE`. Both require revision and request identity,
support `--check`, and take exactly:

```text
{objective_id: ID, message_id: Text, session_id: Text, instruction: Text,
 provenance: {source: "reported_by_agent"}
           | {source: "host" | "operator", actor_id: Text, attestation_id: Text}}
```

`objective_id`, `message_id`, `session_id` and `instruction` are nonempty text,
and the instruction is at most 64 KiB of UTF-8 bytes. `message_id` labels the
user message that gave the instruction, and one
label backs one disposition. A session whose host does not capture user
instructions reports the user's actual words with exactly
`{source: "reported_by_agent"}`, without actor or attestation fields. This
records an agent's report and claims no authentication. A host or operator that
authenticated the instruction can supply the existing full provenance form,
checked by `schema.validate_provenance`. The controller checks only the form;
it cannot check authorship or that authentication occurred. The same provenance
form check also applies to `resume` and `side_interval`; neither receives a new
instruction size bound, so earlier records remain replayable. A provenance in
neither form refuses `resume` and `side_interval` with
`control_provenance_required`, where earlier releases refused with
`invalid_record`. Focus and reviewer
validation keep `schema.validate_provenance`. The digest of
`objective_id`, `message_id` and `session_id` is appended to
`control.resume_ids`, which `resume` also consumes, so an identity used by any
of resume, set-aside and end-objective refuses the other two
(`control_instruction_consumed`; `resume` refuses it with `invalid_record`). A
`message_id` in `control.side_instruction_ids` refuses, and a disposition's
`message_id` refuses a later side allocation. A request repeated with the same
inputs returns its receipt.
Resume and side allocation retain their separate historical identity checks;
no new check between those two actions is added.

Two new inner event kinds are registered in `model.EVENT_HANDLERS` and its
service-command allowlist, and in `output_views.OPERATION_GROUPS`:

| Command | Kind | Closed event payload |
| --- | --- | --- |
| `set-aside CHECKPOINT` | `result_set_aside` | Spec plus `checkpoint_id` |
| `end-objective` | `objective_ended` | Spec |

They append the payload plus `kind` to optional `control.dispositions`. Older
events and initial state acquire no new fields. Storage retains its event
envelope and validates through the model reducer. Earlier readers refuse these
kinds; writer-runtime diagnostics already identify the writing runtime. Unlike
rank recovery, these are new kinds, not extensions of an existing acknowledgment.
The journal-transition compatibility rule applies: all readers/writers must
support the kinds before a session writes them.

A set-aside command also records the existing native observations, as checkpoint
and acceptance commands do, so immediate scheduling uses the same exact latest-run
matching as a subsequent refresh.
A set-aside result retains its checkpoint and its verification status; it
grants no proof or obligation credit. The exact checkpoint stops requiring
acceptance, including after native observation refresh. Another unaccepted
checkpoint remains mandatory. Later normal acceptance adds
`ended_by_acceptance_id` to the disposition's replayed projection and permanently
ends its scheduling effect, including after invalidation. The instruction remains
in history; another set-aside after invalidation needs a fresh instruction.
No review or acceptance is synthesized.

An ended objective has `execution_status: "ended"` and next action
`{kind: "ended", instruction: Text}`. Its proof status, obligations and closure
are unchanged. `propose`, `review`, `admit` and `adopt` (a retry of a recorded
request of these four included), `begin`, `run`, `resume`, `pause`, `replan`, `side-interval` and
`complete`, the legacy `plan`, `rank`, `verify` and `fail`, a reserved run at its
launch audit and at the release of its workload token, and a `control_recorded`
event with action `pause`, `resume` or `side_interval` refuse with
`control_objective_ended`. A fresh `end-objective` request also refuses with
`control_objective_ended`, and an exact replay returns its receipt. Every
other command keeps its ordinary checks:
`reconcile`, `audit`, `render`, `status`, `next`, `refresh`, `accept`,
`checkpoint`, `retreat`, `focus`, `interpret`, the amendments,
`reassess` and `deliver-attempt` keep their own
rules, and a move reserved before the end can still be journalled. An ended
objective is terminal: `resume` refuses, and more work on the same statement
starts a new objective.
`end-objective` may record while a native intent or a move is pending;
`set-aside` still requires native-intent reconciliation because it observes the
node. Ending preserves the pending intent and its state error. `next` returns
`ended`, but `deliver-attempt` keeps refusing with `native_state_pending` until
`search reconcile` clears that intent. A pending move alone does not prevent
attempt delivery. Ending an already resolved objective
refuses with `control_objective_resolved`. Closure delivery still requires actual
completion and refuses with `native_completion_required`. An ended objective
needs no focus: `next` returns `ended` for any session, `status`, `next` and
`strategy-context` return no handoff for an unfocused session, and `hook-stop`
needs neither a matching focus nor the reconciliation of a pending native
intent. Stop accounting permits stopping without consuming continuation, and
SessionStart emits no request for work.

Status and the objective output view show each full disposition with the user's
words and unchanged provenance, including the reported form. Attempt summaries
add `dispositions` only when present and carry the same provenance. The common-store
attempt and the problem review's attempt projection retain it; records without
dispositions retain their exact summary bytes and digest. No native-attempt
consumer closes the summary's top-level fields, so this release accepts the key.
The problem-review projection adds `dispositions_note` only beside an existing
`dispositions` key to explain that these control records confer no proof credit.

New control refusals are `control_invalid`, `control_provenance_required`,
`control_objective_mismatch`, `control_instruction_consumed`,
`control_checkpoint_unavailable`, `control_result_accepted`,
`control_objective_ended` and `control_objective_resolved`. The messages for
the spec fields, the provenance and a consumed identity state the requirement
on the condition that the user gave the instruction and otherwise say to report
the stop; no message asks the agent to supply an instruction.

## Workspace

`attack/<slug>/` holds:

| file | written by | validated by |
|---|---|---|
| `problem.json` | agent (stage 2) | `check-problem`; `journal add` on every move |
| `parent.json` | harness, through reviewed controller admission for managed work | `finish` on the parent refuses while the child is open |
| `novelty.md` | agent (stage 3) | `finish` at the stage 3 exit requires it non-empty; `check-unit` requires a non-empty `novelty` field on each unit |
| `study/problem.md`, `study/<strategy>.md` | agent (stage 3, and step 1 of each strategy) | `journal add` refuses a move under a strategy whose `study/<strategy>.md` is missing or empty; `plan` validates complete `problem.json` and preconditions directly without reading or requiring `study/problem.md`. Reviewed proposal study digests remain required admission evidence |
| `preconditions.json` | agent (stage 4) | `plan`; `journal add` reads the verdicts |
| `openings.json` | harness (`plan`) | |
| `ranking.json` | agent (stage 4b) | `rank`; `journal add` on the opening move |
| `journal.jsonl` | harness (`journal add`) | schema and flow rules on write |
| `tasks.json` | harness (`task`) | |
| `activity.jsonl` | the plugin's activity hook | read by `status`; a line it cannot parse is skipped |
| `deterministic/<step>-<n>/` | agent, checked by harness | `verify`, which writes `result.json` there |
| `units/INVENTORY.md` | harness skeleton (`stall`), agent below it (stage 7) | `check-unit` and `finish` refuse to run without it |
| `units/<n>/unit.json` | agent (stage 7) | `check-unit`, which writes `units/<n>/check-unit.json` when it passes |
| `units/<n>/draft.md`, `units/<n>/evaluation.md` | agent (stage 8) | `finish` |
| `units/FINISHED.json` | harness (`finish`) | |

The files the harness and the hooks write (`openings.json`,
`journal.jsonl`, `tasks.json`, `activity.jsonl`, `parent.json`,
`result.json`, `check-unit.json`, `FINISHED.json`) are written by their
commands only, and each command publishes the complete file read-only. It
writes a temporary file in the same directory, sets the final mode `0400`,
syncs it, renames it over the target and syncs the directory, so an
interruption before the rename leaves the previous file intact. An append
(`journal add` outside the controller, the activity hook) reads the file,
extends it and replaces it the same way under the lock its writer holds.
A direct write to one of these files, a shell redirection `>` or `>>` or a
Python `open` for writing or appending, fails with a permission error; the
commands still update them. This stops accidental direct writes only: the
same user can still change the mode, unlink or replace a file through its
writable directory, and full protection needs host-enforced read-only
access with a separately authorized writer, which this plugin does not
establish. The attack-file guard stays: it refuses a `Write` or an `Edit` of
these files and a shell command that writes to one of them in a form its
parser recognizes, and the read-only mode covers the direct writes that the
guard does not see. The files the agent writes by design (`problem.json`,
`novelty.md`, `preconditions.json`, `ranking.json`, the `study/` files,
`units/INVENTORY.md` and the unit files) stay writable. A reader never
requires the read-only mode: a record written before 0.76.0 with writable
files reads and plans as before, and the next native write of such a file
publishes it again read-only, also when its bytes are unchanged. When a
direct write to a file that a command owns fails with a permission error,
use that command. When the file belongs to a hook (`activity.jsonl`), leave
its updates to the hook. Never change the mode of a harness-owned file.

A native child record has the same format whether historical or managed:
its `parent.json` is `{"parent": "<slug>", "opened_after_move": n}`,
`n` the parent's move count at the time. The parent must exist, be
unfinished, and be nobody's child. The parent's children are found by
scanning the attack root for links that name it; nothing is duplicated
into the parent.

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

`openings.json`, written by `plan`:

```json
{
  "generated_from": "preconditions.json",
  "problem_digest": "sha256 of problem.json as plan read it",
  "openings": [
    {"rank": 1, "strategy": "<strategy>", "verdict": "yes",
     "component": "stage", "costs": ["effectivity", "object"]}
  ]
}
```

The openings are the strategies whose verdict is not `no`, verdict yes
before unknown and name order within, each with its component and its
declared costs sorted. `problem_digest` is the SHA-256 of `problem.json`
serialised with sorted keys and no whitespace; `journal add` compares it
with the digest of the problem as it stands when a new pass starts.

`ranking.json`, written by the agent over the openings:

```json
{
  "generated_from": "openings.json",
  "order": [
    {"strategy": "<strategy>", "cites": ["shape.target_quantity", "cost:effectivity"],
     "reason": "one sentence"}
  ]
}
```

Every opening appears exactly once and nothing else does. A citation is a
dotted path that exists in `problem.json`, or `cost:<name>` with a cost
the strategy declares.

`journal.jsonl`, one line per move:

```json
{"move": 7, "pass": 1, "walk": "<strategy>+<strategy>", "strategy": "...", "entry": "...",
 "trigger_features": ["shape.quantifiers"], "action": "...", "steps": ["enumeration-run-1"],
 "output": "...", "costs_paid": ["object"], "failure_signal_fired": false,
 "problem_changed": false, "closes": false, "step_cites": ["shape.proof_shape"],
 "problem_digest": "sha256 of problem.json as the move left it"}
```

The agent supplies every field but `problem_digest`, which `journal add`
computes and appends; a move that carries it is refused as an unknown
field. `walk` is the walk so far: the strategies entered, joined by `+`
in order, ending in this move's strategy. `step_cites` is empty except on
a move that steps into another strategy, where it names the fields and
the declared costs that put that strategy next. `costs_paid` holds what
this move gave up, from the cost vocabulary. `steps` names the
directories under `deterministic/` the move ran, each of which holds a
`result.json`. `closes` marks the move whose output is a proof of the
claim or a counterexample to it.

`tasks.json`, written by `task`:

```json
{"tasks": [
  {"id": 1, "text": "...", "status": "open | done",
   "added_at": "2026-09-02T21:00:00Z", "added_after_move": 3,
   "done_at": null, "done_after_move": null}
]}
```

`activity.jsonl`, appended by the plugin's activity hook, one object per
tool call that touched the workspace: `{"at": "...", "tool": "Write",
"target": "deterministic/enumeration-run-1/check.sh"}`.

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
   `openings.json` written over the problem as it now stands.
8. The strategy has a precondition record and its verdict is not `no`;
   the entry is one the strategy's front matter lists; `trigger_features`
   is non-empty and names `problem.json` fields whose value is not
   `unknown`.
9. The walk. On the first move: `ranking.json` orders exactly the current
   openings, the strategy is the first of that order, `walk` is that
   strategy, and `step_cites` is empty. On a move under the previous
   move's strategy: `walk` is unchanged and `step_cites` is empty. On a
   move that steps into another strategy: `walk` is the previous walk
   extended by it; `step_cites` is non-empty and every item is a
   `problem.json` field or a cost the strategy declares; the distinct
   strategies of the new walk carry at most one verdict `unknown`; no
   strategy that ran lists the new one under `excludes` or is listed by
   it; and the new one lists no strategy that ran under `precedes`. A
   walk may return to a strategy it left, and has no length cap.
10. The budget: the move number is the next, the pass is the current or
    the next, the pass is not spent, and no stall is due.

The stall reasons, in the order checked: the last move closed the attack;
24 moves used; the last pass spent; three consecutive fired failure
signals since the last `fail`.

## Commands

| command | does |
|---|---|
| `init <slug> [--from <parent>]` | creates an unmanaged historical-style workspace. In a managed controller root, use reviewed `search admit`; it creates the workspace and, for an approved `native_parent`, writes `parent.json`, while legacy `init --from` refuses with migration guidance |
| `check-problem <slug>` | validates `problem.json`: every key present, no empty strings, quadruple values from the allowed sets |
| `plan <slug>` | validates `preconditions.json` against the strategy files, writes `openings.json` with every strategy whose verdict is not no, prints them |
| `rank <slug>` | validates `ranking.json`: it orders exactly the current openings, each row citing a `problem.json` field or a cost the strategy declares, and prints the order |
| `journal add <slug> --json '<move>'` | validates and appends the native move. Managed work first reserves the exact move with `search begin`; the controller acknowledges the resulting exact journal prefix |
| `budget <slug>` | prints moves used in this pass and overall, passes used, and whether a stall is due (a closing move, hard cap, last pass spent, or three consecutive failure signals) |
| `fail <slug> <strategy>` | sets the strategy's verdict to no with a note, stamps the journal length as `failed_after_move`, re-runs `plan` |
| `verify lean <slug> <step-dir>` | after managed input review, routes the exact requested declaration and type through the frozen controller executor, runs the bounded build and independent axiom inspection, and publishes `result.json`; local pass is evidence pending policy audit and acceptance |
| `verify certificate <slug> <step-dir>` | after managed input review, routes the executable checker and complete declared inputs through the frozen controller executor and publishes `result.json`; local pass is evidence pending completeness review and acceptance |
| `stall <slug>` | refuses while no cash-out rule holds (a stall is due, or the plan admitted no opening); otherwise writes the inventory skeleton (`units/INVENTORY.md`): the walk, then every journal move grouped by strategy, marking the ones whose failure signal fired and the one that closed the attack, and names the rule |
| `check-unit <slug> <n>` | refuses before the inventory exists; validates that `units/<n>/unit.json` has statement, form (one of `conditional-or-special-case`, `quantitative-improvement`, `reduction-or-equivalence`, `barrier`, `counterexample-or-computational-evidence`, `new-machinery`, `survey-or-problem-paper`, `counterexample`, `algorithm`, `formalisation`, `formal-proof-write-up`, `full-proof`, or `second-proof`), evidence path that exists (with a `result.json` when it is a deterministic run), novelty record, journal move numbers, and `costs`, the ledger its evidence carries; refuses a form the evidence or the ledger rules out (a run that did not pass is evidence; a closing form carries neither `object` nor `obligations`; `algorithm` and `counterexample` carry no `constructivity`); writes `check-unit.json` with the digest of the record on success and removes any stamp first |
| `finish <slug>` | refuses before the inventory exists, while any `units/<n>/` lacks a stamp matching its `unit.json`, a non-empty `draft.md`, or a non-empty `evaluation.md`, and while any child attack is open; writes `units/FINISHED.json` with the unit numbers. With no move and no inventory it is the stage 3 exit: it needs a non-empty `study/problem.md` and `novelty.md` and records the local outcome `solved-in-literature`. Neither form closes the controller root |
| `status <slug>` | prints where the attack stands, derived from the record: the stage, the parent or the children when there are any, the problem, the study, the plan, the ranking, the walk, the budget, the cash-out state with what each unit still needs, the open tasks, the last three activity entries, and the next step |
| `task add <slug> <text>` | appends an open task to `tasks.json`, numbered, stamped with the time and the move count |
| `task done <slug> <id>` | marks the task done, stamped the same way; refuses an unknown id and a task already done |
| `task list <slug>` | prints every task as `[ ]` or `[x]` |

Budget constants live at the top of the file: 8 moves per pass, 3
passes, 24 moves hard cap, stall after 3 consecutive failure signals.
The consecutive-failure window starts at the last `fail`, so ending one
strategy leaves the attack free to step into the next.

## The stage `status` reports

Read in the order the stages complete: `units/FINISHED.json` exists, the
attack is finished; `units/INVENTORY.md` exists, stage 7 with no unit
directory and stage 8 with one or more, the next step naming what the
first incomplete unit still needs; a cash-out rule holds (a stall is due,
or the plan admitted no opening), stage 7 and the rule; the journal has
a move, stage 5 under its last strategy; `openings.json` exists, stage 5
at the opening when the ranking is valid and stage 4 otherwise;
`problem.json` fails `check-problem`, stage 2; `preconditions.json`
exists, stage 4 at `plan`; `study/problem.md` is non-empty, stage 4 at
the scan; otherwise stage 3.

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
