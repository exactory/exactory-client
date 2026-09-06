# Persistent mathematical search controller

Date: 2026-09-05

Status: Approved by the user on 2026-09-05. Implementation in progress.

Target: Exactory plugin 0.34.0, extending the 0.33.1 math-solver harness.

This is the implementation copy of the approved design in the user's
`misc/tree-search-mechanism.md`. The original request is preserved beside that file.
This specification governs the product change, not a new Dittert conjecture attack.

## 1. Decision and scope

Add a small, deterministic objective controller above the existing attack harness.
Keep its strategy library, studies, journal, local move budget, verification commands,
and publication-unit workflow. Make the controller authoritative for admission,
cross-attack obligations, checkpoints, resource accounting, backtracking, and
completion of the original objective.

The controller is not a theorem prover. Given the same recorded inputs, reviews,
evidence bytes, and command, it must make the same transition and scheduling decision.
Mathematical reasoning and research assessment remain judgments that must be recorded
and reviewed. A deterministic validator cannot decide whether an arbitrary conjecture
is true or whether a journal would accept a hypothetical result.

| Approach | Benefit | Limitation | Decision |
| --- | --- | --- | --- |
| Strengthen instructions and maintain a Markdown tree | Small change | No enforceable admission, dependency, or completion semantics | Reject |
| Add an objective controller over existing attacks | Reuses local checks; supports persistent obligations and bounded alternatives | Requires explicit migration and hook integration | Adopt |
| Replace the harness with a general search engine | Broad extensibility | Substantial complexity and migration cost | Reject |

Version 0.34.0 does not add learned scores, MCTS, probabilistic scheduling, a database
service, distributed execution, a general mathematical expression parser, or a new
publication engine. It does not solve Dittert or claim that orchestration guarantees
eventual mathematical discovery. Publication and external uploads remain separately
authorized actions.

## 2. Required invariants

1. The original objective and requested proof standard remain visible and immutable.
   Changing them requires an explicit user-directed replacement, recorded as such,
   never as completion of the old objective.
2. Every research move belongs to an admitted investigation of a named obligation,
   or to an admitted independently valuable side result. A new directory is not
   admission, and a strategy precondition verdict is not admission.
3. Every accepted contribution has an exact claim, assumption context, evidence,
   verification classification, and a checked connection to the obligation it closes.
4. All premises of a chosen proof route are required. Different routes to the same
   conclusion are alternatives. Closing one premise cannot close the conclusion.
5. Finishing an attack, publishing a partial result, exhausting a budget, or finding
   related literature cannot by itself resolve the objective.
6. Failure retains the attempt and its evidence. Backtracking changes the active
   route, not history, resource counters, or the truth status of independent facts.
7. Every node declares checkpoint and retreat criteria before research execution.
   Renaming a node, moving it, or producing a paper cannot reset its allowance.
8. Hooks and CLI commands consume the same controller decision. Read-only status
   does not create nodes, spend budgets, accept results, or select a different route.
9. Missing, stale, ambiguous, or corrupt evidence never counts as effective proof
   coverage. An execution pause must be reported as a pause, not as mathematical success.

## 3. Existing implementation and concrete gaps

The audit covered the source skill, study and cash-out contracts, strategy routing,
the CLI harness, both host adapters, existing tests, and release wiring. Paths below
are relative to `plugins/exactory-client/`; line numbers refer to 0.33.1. Harness
paths are under `skills/math-solver/harness/`.

| Area | Existing behavior | Required change |
| --- | --- | --- |
| `attack.py:157` | Native children have one level and record parent slug and journal count | Keep that contract; add logical lineage and admission |
| `attack.py:211,574` | Digest detects changes to mutable `problem.json`, not weakening of the original objective | Freeze the objective and each admitted claim separately |
| `attack.py:797` | Each attack gets 24 moves; verification can run before a move is journalled | Reserve moves and runs before execution; account across successors |
| `attack.py:943,1139` | `FINISHED.json` means locally finished, including zero-unit cash-out | Never interpret it as objective completion |
| `attack.py:956` | Literature-only finish returns before the open-child check | Apply child and terminal-state checks to every finish path |
| `attack.py:1386,1475` | Results do not bind the complete claim and dependency snapshot | Add immutable evidence manifests and scope review |
| `attack.py:1409` | Axiom-inspection subprocess exit status is not checked before its text is parsed | Require successful inspection of the requested declaration |
| `attack.py:1001` | Unit check stamps primarily bind `unit.json` | Invalidate acceptance when evidence or review inputs change |
| `hooks/resume_attack.py`, `hooks/continue_attack.py` | Discover unfinished attacks; Stop selects alphabetically; counters are per attack | Use one objective status and continuation allowance |
| Shared hooks | Unexpected exceptions generally exit successfully | Fail closed for recognized managed mutations; report errors |
| `codex/hook.py`, `codex/generate.py` | Explicit translation covers patches, not all current shell-event forms | Normalize supported payloads and verify host coverage |

The strategy `precedes` relation orders methods. It is not a proof dependency.
`tasks.json` tracks work, not theorem obligations. Activity logs are advisory, not
authoritative evidence or budget ledgers. These distinctions remain explicit.

## 4. Logical model

One controller manages one original objective per attack root. Unrelated objectives
use separate attack roots. A workspace may register several roots, but only one is
focused for automatic continuation in a session.

The contract names a retained root attack whose `claim` is the full original objective.
Its local workflow may finish without closing the objective. The controller continues
to show that claim and its residual obligations. Scheduling depends on the obligation
graph, not on the existence of a currently running parent attack.

The user-facing structure is a search tree of investigations. Its proof model also
contains an acyclic dependency graph:

- An objective is the original quantified proposition and delivery contract.
- An obligation is an immutable statement with explicit assumptions. It may be
  the whole objective, an exhaustive case, a lemma, or a required verification step.
- A route has one conclusion and a finite, ordered set of required premises plus
  a bridge obligation proving that those premises suffice. Its requirements are AND.
- Multiple routes with the same conclusion are OR alternatives. A route is a
  hypothesis until its bridge is accepted. An outline is not an accepted bridge.
- An attack node is one bounded investigation using the existing skill on an
  exact obligation or admitted standalone claim. It has one logical predecessor.
- A checkpoint is an immutable record of a result, reduction, precise obstruction,
  or materially sharpened hypothesis. Only qualifying proof evidence closes obligations.

```text
Objective A
  Route R1: B AND C AND D AND bridge(B, C, D => A)
    Attack on B -> checkpoint proving B -> optional local cash-out
    Attack on C -> failed approach -> retreat -> next admitted approach to C
    Attack on D -> pending
  Route R2: E AND bridge(E => A), an alternative to R1
```

The implication can depend on the root assumptions; these are part of the bridge
statement. Parent-child placement is never itself a logical implication. Circular
proof dependencies and self-supporting checkpoints are rejected. A complete alternative
route need not prove abandoned premises of R1. It must still prove every case and
premise required by its own exhaustive argument.

### 4.1 Identity and native children

Use controller-assigned stable IDs for objectives, obligations, routes, nodes,
checkpoints, reviews, budget accounts, and execution reservations. Slugs are display
and directory names, not identities. References use IDs and immutable content digests.

Keep `init <child> --from <parent>` only where the native contract permits it: an
unfinished parent that is not already a native child. For deeper logical descendants
or successors of finished attacks, use ordinary initialization internally and record
the relationship in the controller. Never rewrite `parent.json` to simulate nesting.
Generated `LINEAGE.md` distinguishes native and logical links.

### 4.2 Proof and execution status

The objective has separate derived fields:

- `proof_status`: `open`, `proved`, `disproved`, or `invalidated`.
- `execution_status`: `running`, `needs_replan`, `paused`, `blocked`, or `resolved`.
- `publication_status`: references to local units and their existing workflow status.

`disproved` requires a verified counterexample to the original statement, not a
stronger surrogate. Report disproof as disproof, never as successful proof. If a
proof-only request conflicts with a certified counterexample, report that conflict
and request direction. Independence, a method barrier, or a conditional result does
not satisfy a proof request. A user-directed scope change creates a new contract
linked to the old one; the old objective remains accurately labelled.

Local node lifecycle is `proposed`, `admitted`, `active`, `waiting`, `result_ready`,
`retreated`, or `finished`. Logical acceptance is separate from this lifecycle.
An attack may be locally finished with no accepted proof contribution.

Accepted root evidence makes the result ready for final review. `proof_status` becomes
`proved` or `disproved` only after `search complete` accepts the final closure record.
A subsequent failed freshness audit derives `invalidated` and reopens execution;
it does not erase the old closure event or assert that the proposition is false.

## 5. Admission before branching or computation

The controller accepts a proposal only after structural validation and the required
independent review. Writing a proposal and conducting its bounded literature study
are permitted before admission. Mathematical experiments and proof-search execution
are not. The same gate applies inside an existing node, not just to new directories.

Every proposal includes:

- Exact claim, quantifiers, assumption IDs, targeted obligation, and logical predecessor.
- Predecessor checkpoint and inherited evidence, or the initial objective snapshot
  for the first attempt.
- Relationship: `main`, `prerequisite`, `coverage`, `alternative`, `continuation`, or
  `standalone`. A continuation retains its original admission category.
- Concrete hypothesis and method, applicability, success and failure criteria, and
  the result that would change the parent's remaining work.
- Its own problem study and novelty assessment, inherited assumptions, and the
  strategy studies required before each method executes.
- Checkpoint criteria, retreat criteria, resource limits, budget account, and
  references to already tried equivalent claims or methods.

Admission is one of three substantive cases:

| Case | Evidence required before execution | Effect of success |
| --- | --- | --- |
| Main progression, including a prerequisite | Named root-reachable obligation; precise proposed route and bridge; why the step is needed on that route; bounded test or proof task | Closes an obligation or resolves the admitted necessary decision; a decision alone gives no theorem coverage |
| Exact coverage branch | Explicit subset of an exhaustive, non-weakened partition; reviewed subset-to-parent deduction | Removes exactly fully proved cases, counting overlaps once |
| Independently valuable side result | Exact prospective theorem; primary-source novelty study; comparison with strongest relevant known results; specific mathematical contribution and venue-level significance | Establishes a standalone result; no root coverage without a separately accepted bridge |

An attack directly targeting the original objective needs no artificial self-implication
bridge. Its full claim and admitted proof strategy provide the direct connection.

Prerequisite means necessary for the selected route, not a claim that all possible
proofs must use that lemma. A hard unresolved bridge can itself be a main obligation.
Requiring it to be proved before any work on its premises would prevent legitimate
research. Instead, the complete proposed chain must reach the root, receive independent
assessment of its mathematical substance, and remain visibly open until proved.

One independent reviewer assesses main and coverage proposals against the root and
the proposed deduction. Two independent reviewers must both approve a standalone
proposal's novelty and substantial mathematical value. Reviews name inspected input
digests, findings, decision (`approve`, `revise`, or `reject`), reviewer provenance,
and unresolved objections. A proposal author cannot review that proposal. Missing
review capability pauses admission; another admitted main route can continue. An
optimistic sentence or a venue name is insufficient. No review guarantees acceptance
by a journal.

The host supplies independent review records. The CLI checks their bindings, distinct
provenance, required findings, and verdicts. It cannot authenticate intellectual
independence from a user-editable name alone. This is an explicit trust boundary.

### 5.1 Necessity gate for finite computation

The number of cases does not determine admissibility. The question is what the
computation contributes to required progression, or whether it independently meets
the substantial-result gate. Before any finite run, its proposal must state:

1. The exact obligation or necessary strategy decision it serves.
2. What cannot proceed on the selected route if this computation is omitted.
3. Why the chosen finite domain and precision suffice for that obligation or decision.
4. How each relevant outcome changes the recorded next action.
5. A finite stopping condition and resource limit.

Independent review must reject an unsupported assertion of necessity. A bare link
to the root or a generic promise to "inform the proof" is not an adequate deduction.

Review admits a bounded computational task once, including its purpose, input domain,
outcome-to-action mapping, and finite run allowance. Individual invocations and compiler
retries need reservations, not new significance reviews. Changing that admitted
purpose, domain, or allowance requires a reviewed amendment before execution. The
amendment cannot erase prior usage or override the reset rules in section 8.

- Full proofs for `n = 5, 6` within a root covering `5 <= n <= 15` are valid coverage.
  Checking selected matrices or a sufficient scalar inequality is not case closure.
- The first 100 cases can be required base cases or the complete finite residue of
  a reduction. They are then main progression, with the induction or reduction
  bridge still explicitly owed.
- Checking the first 100 cases of an infinite assertion merely to observe a pattern
  gives no proof fraction. Larger finite prefixes are rejected unless their exact
  necessity or standalone mathematical significance is established.
- A finite computation settles an infinite theorem only together with a proved
  completeness reduction and all of its hypotheses.
- Bounded counterexample search may be a main attack because one valid counterexample
  can refute the original proposition. A no-hit search is not proof, does not refresh
  a budget, and does not count as a progress checkpoint.
- A discriminating experiment on a main lemma is permitted only when a stated
  unresolved decision prevents the route from advancing and the outcome-to-action
  mapping is fixed beforehand. General exploration or repeated samples without
  such a decision fail admission.
- Toy models, numerical optima, and neighboring statements need a concrete required
  transfer route or independent significance. Ease of computation is not admission.

Inherited checks from a paper still need exact scope review. A published theorem
can satisfy an obligation only at the requested standard. A citation cannot by
itself satisfy an end-to-end Lean formalization request.

## 6. Coverage and result acceptance

The initial contract records the original statement, assumptions, expected outputs,
and proof policy: `reviewed`, `certificate`, or `lean-kernel`. An objective can require
a conventional proof and specified formalized components, but only a root-level
`lean-kernel` proof permits the label "fully Lean-verified".

Initially the root can be one unresolved obligation. Later decomposition adds reviewed
routes; it cannot edit the root statement or mark an incomplete partition exhaustive.
Version 0.34.0 supports explicit case IDs, finite integer intervals with endpoint
flags, and named general obligations. General symbolic set equality is out of scope.
A nontrivial partition needs its own coverage bridge proof. Infinite ranges use a
quantified tail or induction obligation, not an arbitrary finite list.

Acceptance requires all of the following:

1. The result is the targeted obligation or a separately proved statement with an
   accepted implication to that obligation.
2. Extra assumptions are explicit and either discharged or still required premises.
   A conditional theorem can close an implication, not its unconditional consequent.
3. Scope includes the full domain, quantifiers, endpoints, degeneracies, and equality
   characterization when requested. Statement review compares these individually.
4. Evidence satisfies the obligation's proof policy and is fresh.
5. Upward bridges are accepted under a compatible policy. A kernel-checked scalar
   computation with a prose-only reduction cannot close a fully formal root.

The controller does not infer implication from matching strings. Semantic equivalence
and informal deductions require independent review; formal implications require a
checked declaration. Propagation operates only on accepted bindings.

Display exact coverage, for example: "2 of 11 dimension cases accepted; cases 7
through 15 and the listed common obligations remain open." Do not display percentages
of effort, confidence, or proof completeness. A case counts only when its whole
obligation, including common prerequisites it uses, is accepted.

### 6.1 Evidence manifests and freshness

An immutable evidence manifest binds:

- Claim, assumptions, proof policy, and a tagged evidence origin. A `journal_move`
  origin names the producer node, move, and journal-prefix digest. An `external_result`
  origin names the pinned publication/source, exact statement, study, and their
  digests. Reviews attach through acceptance, not inside this origin. No fabricated
  journal move is required for an imported literature result.
- Exact proof source, solver/checker code, inputs, outputs/certificates, and result
  files, each with a relative path and SHA-256 digest.
- Dependency manifests and versions, toolchain and package lockfiles, command argument
  vector, working directory, exit statuses, and declared nonsecret environment inputs.
- For Lean, the fully qualified declaration, its printed type, its axiom report,
  and the successful build and inspection results.
- The exact subject material required for independent statement/checker review.

Review records bind the immutable subject manifest and claim digests. The acceptance
event joins those reviews to the checkpoint. A subject manifest must not contain
the reviews of itself, which would create a circular content digest. Additional
reviews produce new acceptance records, not edits to an existing manifest.

The manifest covers transitive proof inputs. Omitting an imported local theorem or
checker dependency makes it incomplete. Build caches are not proof sources. Record
how dependencies were enumerated; independent review checks that boundary. Never
copy secrets into manifests, logs, environment reports, or command lines.

Before execution, capture local proof/checker/input bytes into a content-addressed
snapshot. Run against that snapshot, with declared separate mutable output/build
locations, not against source files another agent is editing. Pin external dependencies
and verify their digests. Verify inputs remain unchanged at completion; otherwise the
run is evidence of an execution error, not a proof. Capture results into immutable
artifacts before creating a checkpoint. This avoids binding outputs to post-run edits.

`reviewed` means explicit independent proof review, not a machine certificate.
`certificate` also requires an independently assessed checker and completeness
argument. Analytical bridges may be independently reviewed; every computational
claim they use must have the required certificate. An exit-zero `check.sh` alone is
insufficient. `lean-kernel` requires kernel-checked evidence for the requested
declaration and formal bridges, with no
`sorryAx`, custom axioms, or native-evaluation trust beyond the recorded standard
axioms. Existing native-evaluation status `evidence` remains evidence, not kernel proof.

Unsuccessful or ambiguous theorem inspection fails verification even if the output
contains an axiom-like line. Inspect the requested declaration, not the first unrelated
axiom report in combined output.

Every mutation that consumes evidence revalidates its dependency closure. Read-only
status can return `freshness_unchecked` if a bounded check cannot finish; it must not
report current completion then. A dedicated audit performs the complete check without
running mathematical jobs. Changed or missing referenced artifacts invalidate dependent
acceptances and reopen affected obligations. Historical acceptance events remain. Revalidation
requires new verification; editing evidence cannot update an old stamp.

Acceptance binds the immutable snapshot, not a later working copy. Editing a working
copy alone does not invalidate its preserved proof. Changing or losing a referenced
snapshot does. A changed definition or target in a later proposal requires a fresh
compatibility review; the older proof does not automatically transfer to that context.
The latest working files cannot be published as verified merely because an older
snapshot passed. The output package must bind the accepted versions.

## 7. Checkpoints, successors, and retreat

Each node declares checkpoint criteria with named output obligations, permitted
result kinds, and a mathematical explanation. Use fixed typed predicates, not
executable expressions embedded in JSON. Examples are an accepted lemma ID, an exact
case set, a verified reduction, or a verified obstruction to a specific route.

A checkpoint records its ID, source origin, exact statement and assumptions, evidence
manifests, verification status, what changed, remaining obligations, and next hypothesis.
The initial objective snapshot is a
lineage anchor, not a proof checkpoint.

For local work the origin is its node, move, and journal-prefix digest. For an exact
external result it is the pinned source, statement, and study. The same acceptance
policy applies to both. Snapshot creation can precede result acceptance; checkpoint
criteria confer progress credit only after the required acceptance has succeeded.

Classes are `proof`, `reduction`, `obstruction`, and `hypothesis`. An obstruction can
exclude one strategy without disproving the target. A hypothesis snapshot preserves
research state but grants neither coverage nor a fresh budget. No-hit searches,
elapsed time, more journal lines, or a new paper are not verified progress.

Before planning a successor that uses a result, save its checkpoint. A successor
names inherited results, its new exact question, success and failure criteria, and
evidence addressing relevant earlier failures. It performs its own studies and plan.
Inherited records can reduce duplicated reading but do not waive required checks.

Retreat conditions include the existing strategy failure signal, a contradicted
method prerequisite, declared resource limit, and predeclared stagnation test.
Resource predicates are evaluated mechanically. Mathematical failure reports state
the evidence and whether the obstruction is independently verified. An unsuccessful
attempt can retreat without proving a general impossibility theorem.

Retreat records the failed hypothesis, observation, last usable checkpoint, remaining
assumptions, and conditions for reconsideration. It suspends descendants that depend
on the abandoned route or assumption, then returns to the nearest ancestor with an
untried admitted alternative. Independent accepted results remain reusable. No files
are deleted or reset, and no fact becomes false merely because its original route failed.

## 8. Budgets and deterministic scheduling

### 8.1 Local limits and reset prevention

Keep 8 journal moves per pass, 3 passes, and the 24-move hard cap. Keep existing strategy
walk, precondition, study, and failure rules. New nodes cannot waive these restrictions.

Before an entry executes, reserve its next move. Before a solver or checker runs,
reserve a run under that move with purpose, inputs, command, timeout, and expected
result. Journal the completed move before another begins. Unjournalled reservations
remain visible and block new research moves until reconciled.

Each node declares a finite `max_runs`, per-run `timeout_seconds`, and move limit
no greater than 24. Templates default to 24 runs, 300 seconds per run, and one compute
worker. Larger workloads declare limits before admission and obey the user's CPU
constraints. Retries, checks, compiler failures, launched cancellations, and crashes
consume allowance. Rejected preflight does not. A crash after reservation is charged
unless recovery establishes that execution never began. Timeouts terminate the
launched process group where supported and record failure.

Run lifecycle is explicit:

| State | Meaning | Recovery rule |
| --- | --- | --- |
| `reserved` | Allowance is reserved; launch has not been durably confirmed | Reconcile before another launch; do not assume the process never started |
| `launched` | Launcher recorded run token, PID/process group, and available process-start identity | Observe that workload; no duplicate launch |
| `terminal` | Exit/timeout/cancellation and artifacts are durably recorded | Reuse the recorded result; never rerun on event replay |
| `indeterminate` | Launch or termination cannot be established safely | Block replacement work until bounded diagnosis or user-directed recovery resolves it |

A launcher handshake records process identity before mathematical work begins. If the
executor dies while its child survives, the reservation remains occupied. A PID alone
is not identity; recovery must not kill a possibly reused PID. Never automatically
rerun during reconciliation. Record unresolved descendants after timeout and do not
claim the machine is idle. The one-worker policy limits admitted top-level workloads,
not every OS subprocess a compiler may create; apply declared thread limits where
the invoked tool supports them.

Track total moves and runs across the objective and stable budget accounts. A
continuation or renamed retry uses its predecessor's remaining account. Splitting
an exhausted node cannot distribute fresh budgets to unchanged copies.

A distinct obligation from a reviewed decomposition can receive its own account.
A substantially new route after exhaustion requires reviewed admission based on a
verified progress checkpoint or new external evidence addressing the obstruction.
A new title, another finite prefix, a hypothesis-only checkpoint, or an unsupported
claim that a method is different does not qualify. Check obvious identity structurally;
semantic duplication also requires independent review.

Allow at most three controller replanning rounds without new verified progress or
relevant new external evidence. A round assesses the current remaining alternatives,
not one proposal. Exhaustion produces `needs_replan`, then a durable pause and exact
report. Explicit user authorization can renew investigation, but does not erase
failures or reset an identical exhausted local attack. Enforce any user-set total
move/run cap across nodes. Do not promise indefinite autonomous host execution.

### 8.2 One frontier

Each route has an ordered list of obligations and a committed order of alternative
attacks justified by studies. Do not assign invented success probabilities or optimize
for inexpensive publication units.

`search next` uses this order:

1. Respect user pause, unresolved state errors, and active or indeterminate execution.
   A resolved objective returns no research action; ambiguous focus requests a handoff.
2. Reconcile pending moves or results. Then request any missing verification, snapshot,
   or acceptance for a result already produced, within its admitted allowance.
3. If accepted evidence reaches the root, request final consolidation, independent
   statement review, audit, any deliverables explicitly required by the contract,
   and `search complete`. Do not select another research alternative first.
4. If local cash-out is due, follow the existing stage 7/8 next action. Budget exhaustion
   blocks new research, not the required inventory and honest local handoff. Root
   deliverables include the cash-out artifacts of contributing attacks, as defined
   in section 11; unused alternatives do not prevent root completion.
5. Continue the active main node while admissible and before retreat conditions fire.
   After a qualifying checkpoint, follow its admitted continuation before restarting
   at the root.
6. For a node waiting on prerequisites, select the first ready prerequisite in its
   route order. A consumer with an unproved premise must explicitly target a conditional
   theorem; otherwise it cannot execute.
7. After retreat, select the next admitted alternative at the nearest ancestor,
   moving upward until eligible main work is found.
8. On initial scheduling and after success, traverse the selected route from the root
   and choose the first ready unresolved obligation and its first eligible admitted
   attack. This works even with no active parent attack. Thus success on B advances
   to ready C rather than inventing a new plan.
9. If no admitted main action exists, request bounded replanning of the unresolved
   frontier. The root stays open even if every existing attack finished.

Stable creation IDs break ties. Ranking changes require a replan event and preserve
budgets. Standalone side nodes cannot displace ready main work. Run them only when
main work is externally blocked or the user allocates a side-work interval. Their
success does not satisfy the root.

Retain the default 40 Stop-block limit but count at objective level, so changing
branches cannot reset it. Repeated delivery of one host event is idempotent when
delivery IDs exist. Without IDs, do not claim exact deduplication; duplicate counting
may pause early, never grant extra allowance. Verified user resume rearms continuation,
not local move accounts. Explicit stop or task switching overrides continuation.

An internal `search hook-stop --spec FILE` operation records Stop accounting and
returns the next decision in one bounded transaction. It is separate from read-only
status. Normalize available session, turn, delivery, and `stop_hook_active` fields.
Use delivery ID for idempotency when present; otherwise assign an invocation request
ID and conservatively count each observed invocation. Missing fields never imply a
new authorization. In particular, `stop_hook_active: false` does not by itself rearm
a paused objective or prove that a user requested continued research.

The cap transition durably sets paused state and marks a single summary request as
issued. Later Stop calls permit stopping, even when their IDs or metadata are absent.
An ambiguous focus or unrelated user turn also permits handoff instead of repeatedly
blocking Stop. `search resume` requires a recorded explicit user instruction referring
to this objective, with message/session provenance supplied by the host or operator.
It does not accept a model-authored assertion as host authentication. Where the host
cannot attest user-control provenance, require explicit operator resume and report
that boundary. Initial persistent-work authorization stays effective only within
the remaining allowance and the focused task.

One admitted mathematical workload runs by default. Read-only literature work and
independent reviews may use separate agents. This is not a global CPU allocator across unrelated
projects. Never launch heavy mathematics merely to test the controller.

## 9. Persistence and interface

Use Python 3.9+ and the standard library. Store one authoritative versioned JSON event
document per attack root plus immutable evidence/checkpoint blobs. Do not introduce
a database service or independently mutable copies of derived state.

```text
attack/
  .search/
    tree.json                    # Contract and ordered events
    tree.lock                    # Single-writer lock
    blobs/<sha256>.json           # Immutable proposals, reviews, manifests, checkpoints
    artifacts/<sha256>            # Captured local proof, input, and output bytes
    legacy/                      # Preserved imported index and lineage snapshots
  SEARCH_TREE.md                  # Generated cross-attack index
  <slug>/
    problem.json                 # Existing working attack description
    LINEAGE.md                   # Generated native/logical lineage
    journal.jsonl                # Existing schema, unchanged
    deterministic/               # Existing run workspaces
    units/                       # Existing publication workflow
```

`tree.json` contains `schema_version`, `objective_id`, frozen contract, and ordered
`events`. Events contain `sequence`, `request_id`, `kind`, input blob digests, and
typed payload. Revision is the last sequence. A pure reducer validates events and
derives obligations, effective acceptance, accounts, lifecycles, and frontier.
Timestamps are audit metadata, not tie-breakers.

The serialized records have the following required fields in addition to their ID
and schema version. References resolve only within the objective or to explicitly
reviewed imports. Unknown fields and unknown enum values are rejected.

| Record | Required content |
| --- | --- |
| Contract | Original claim, assumption IDs, root obligation, root attack slug, requested outcome (`proof` or `decision`), proof policy, required deliverables, resource policy |
| Obligation | Statement, assumption IDs, proof policy, scope descriptor (`named`, `case_ids`, or `integer_interval`) |
| Route | Conclusion obligation, ordered premise IDs, bridge obligation, proposal/review bindings, committed alternative order |
| Node | Attack slug, obligation or standalone claim ID, role (`research` or `verification`), relationship, logical predecessor, checkpoint ID, native parent if any, admission binding, budget account, criteria |
| Admission | Proposal digest, root contribution/necessity assessment, study digests, review IDs, approved input domain and task limits |
| Budget account | Stable lineage owner, move/run limits, cumulative reservations and usage, historical-usage classification |
| Checkpoint | Kind, tagged origin, claim and assumption IDs, evidence digests, predecessor, criterion IDs to evaluate, residual obligations, next hypothesis |
| Review | Subject/claim digests, reviewer provenance, required findings, decision, unresolved objections |
| Run | Node/account/move reservation, admitted purpose, frozen input manifest, argv, cwd, timeout/thread settings, launcher identity, lifecycle, terminal result digest |
| Control event | Focused objective, session/turn/delivery metadata when available, source provenance, action, allowance before/after, one-summary flag |

Checkpoint predicates are `accepted_obligation`, `accepted_case_set`, `verified_reduction`,
`verified_obstruction`, and `hypothesis_recorded`, each with the exact referenced IDs.
Each predicate is a separate named milestone; fulfilling one does not imply fulfilling
the others. The last kind grants no proof or budget credit. Retreat predicates are
`strategy_failure`, `move_limit`, `run_limit`, `stagnation_window`, and
`method_prerequisite_failed`, with their strategy/obligation IDs or integer thresholds.
No predicate executes user-provided code or treats a free-text reason as a theorem.

Mutations require expected revision and request ID. Identical request replay returns
the original result; a reused ID with different inputs is rejected. Hold a per-tree
lock, validate, write immutable blobs first, then atomically replace the event document
using a temporary file, flush, and fsync. Interrupted writes leave the old or new
valid document. Unreferenced blobs are harmless and not silently deleted. Revision
conflicts require rereading. A running compute process does not hold the writer lock.

Legacy writes linked to controller operations use intent events and idempotent
reconciliation. A crash between journal append and controller acknowledgement is
recovered by comparing the intended journal digest, then acknowledging once. No new
work proceeds while an intent is unresolved. Atomic `tree.json` replacement is not
a transaction across legacy journal and result files.

Validate schemas, IDs, event uniqueness, references, acyclicity, immutable claims,
safe paths, and evidence bindings. Resolve relative paths against the registered
root; reject traversal and symlinks escaping it. Shared external dependencies are
explicit read-only manifest inputs, never controller write targets. Report corrupt
state; do not silently reconstruct it from Markdown or erase it.

### 9.1 Commands

Add `search` under `exactory-math`. These commands are interfaces to implement, not
commands claimed to exist in 0.33.1. Mutations accept `--expected-revision` and
`--request-id`; structured inputs use `--spec FILE`.

| Command | Contract |
| --- | --- |
| `search init --spec FILE` | Create objective contract and initial obligation, preserving existing data |
| `search adopt --spec FILE` | Explicitly map legacy attacks and preserve indexes; import no proof acceptance automatically |
| `search propose --spec FILE` | Record route/node proposal; allow study, not execution |
| `search review --spec FILE` | Record independent input-bound review |
| `search admit <proposal-id>` | Enforce admission and budgets; initialize/link attack |
| `search begin <node-id> --spec FILE` | Reserve next entry/move after stage and strategy checks |
| `search run <node-id> --spec FILE` | Reserve and execute one bounded command under that move |
| `search reconcile` | Resolve pending journal/run intents; never rerun math jobs automatically |
| `search checkpoint [<node-id>] --spec FILE` | Snapshot journalled or external result; node is required for local work; no implicit acceptance |
| `search accept <checkpoint-id> --spec FILE` | Check policy, scope, freshness, and premises; propagate coverage |
| `search retreat <node-id> --spec FILE` | Record failure and backtrack under declared criteria |
| `search replan --spec FILE` | Record revised frontier/ranking within limits |
| `search focus`, `search pause`, `search resume` | Explicit focus and user-directed execution control |
| `search audit` | Validate records and dependency evidence without executing proofs |
| `search status --json`, `search next --json` | Read-only state and one proposed next action with reasons |
| `search hook-stop --spec FILE` | Internal adapter operation: record continuation accounting and return a decision |
| `search render` | Regenerate tree and lineage views |
| `search complete --spec FILE` | Recheck root closure and consolidation at required standard; record exact outcome |

Separate schema/reducer rules, storage/locking, execution/evidence, and thin CLI
integration. Do not put all responsibilities into `attack.py`. Module filenames
are implementation details; the boundaries and tests are part of this contract.

Existing `plan`, `rank`, `journal add`, `verify`, `fail`, `stall`, `check-unit`, and
`finish` invoke controller guards for managed attacks. `verify` uses the same run
reservation and executor, not an unmetered second entrypoint. Preserve journal and
native parent schemas; controller sidecars carry new metadata. Terminal local
attacks reject further research mutations.

## 10. Hooks and host boundary

CLI transitions are authoritative. Hooks protect owned files, gate recognized
execution, provide resume context, and maintain focused-objective continuation.
They do not duplicate scheduling or infer completion from file existence.

- Protect controller documents, blobs, generated indexes, and continuation state.
  Direct file writes cannot fabricate accepted transitions through supported tools.
- Normalize operation, command/argument vector, working directory, paths, event ID,
  and supplied success metadata. Cover Claude `Bash`/`Write`/`Edit`, Codex patches,
  and observed Codex `exec_command`/`cmd`/`workdir` forms.
- Recognized managed computation uses the admitted CLI executor. Read-only inspection
  and study remain available before admission.
- Arbitrary shell programs and wrappers such as `functions.exec` cannot be safely
  parsed with token heuristics. Do not invent a general parser. Refuse recognized
  managed mutations with unsupported payloads and explain the supported direct-call
  alternative. When the host never exposes a nested operation, document that limit
  and retain CLI enforcement.
- Fail closed on corrupt managed state or unresolvable protected operations with
  recovery guidance. Do not block unrelated work outside registered roots. Advisory
  logging errors produce diagnostics but cannot fabricate acceptance.
- Resume reads one bounded `search status/next --json` result. Stop uses one bounded
  `search hook-stop` call, which applies the same reducer and next-action rules plus
  continuation accounting. Neither launches a subprocess per attack or parses prose.
- A workspace discovery record registers custom roots and session focus using only
  pointers and objective IDs. Missing or ambiguous focus requires selection; alphabetical
  order cannot redirect work to another project.
- Stop never creates successors, accepts checkpoints, or extends move limits. It
  requests the next authorized action. User stop, continuation exhaustion, and real
  blockers leave a saved handoff and permit ending the turn with the root open.

This is workflow enforcement on supported, trusted hook installations, not an OS
sandbox or protection against arbitrary filesystem access. Report trust, payload
compatibility, and missed-event limitations accurately. Do not claim untested host
forms are protected.

## 11. Cash-out, literature, and the full objective

Keep local cash-out triggers and unit checks. A checkpoint alone does not start a
publication unit. A new node cannot bypass cash-out rules.

If B is the exact claim of a prerequisite attack, proving B closes that local attack
and starts its existing cash-out. Save and accept its checkpoint; keep C, D, and the
bridge open. Local publication work may finish, but the objective remains active.
This needs no second publication system. A meaningful nonclosing result is checkpointed
immediately and enters local cash-out when an existing trigger permits it.

Mandatory delivery from a contributing local attack means its legitimately triggered
inventory, applicable checked units, consolidation, drafts, and evaluations. Ordinarily
it also completes `finish`. There is one explicit status distinction: an unused native
child can prevent the parent's local `finish` under the preserved harness contract.
If that is the only remaining local finish refusal, objective completion may consume
the fully checked artifacts and a recorded handoff without requiring the parent's
`FINISHED.json`. Report `local_finish_pending_unused_children` with the exact child IDs.
Do not mark those children finished, weaken their gates, or suppress other local
validation failures. The root proof and all required artifacts must still be complete.
This preserves native workflow history without requiring research on an unused route.

If all attacks have cashed out while the root is open, `search next` returns the
unresolved frontier and replan action or precise pause reason. It cannot claim the
original proof is finished merely because no local attack is running.

A literature result has three distinct implications:

1. Import only after scope, assumptions, and required standard are checked, closing
   only the matching obligation.
2. Independent verification and formalization remain required when the user requested
   them, regardless of novelty.
3. A neighboring open statement is an admission proposal, not an automatic replacement
   for the original objective.

`search complete` requires an accepted root proof or counterexample, every premise
used by it, a fresh evidence audit, and independent final statement review. No case
or extra hypothesis can be hidden by a completed task or unit. Unused alternative
routes and standalone work may remain historically unfinished; they do not invalidate
a complete proof. Report standard, trusted components, reused-result provenance, and
exact outcome.

## 12. Adoption, compatibility, and rollout

Existing workspaces remain readable. The new skill establishes or resumes the
controller before research. Unmanaged `init` can create a provisional workspace for
formulation, not execution. Research mutation or verification without an admitted
node returns `admission_required` with initialization/adoption instructions. No
unmetered legacy execution flag is permitted.

`search adopt` takes an explicit objective mapping, snapshots the manual index,
lineage sidecars, and relevant state, and preserves journals, outputs, and units.
Existing `FINISHED.json`, `closes: true`, result status, and check stamps are historical
assertions, not automatic acceptance. Imported runs count toward known usage. Unknown
prior usage stays labelled unknown, not zero. A new allowance needs explicit adoption
review instead of a fabricated unused account.

A reviewed `external_result` checkpoint can import exact literature evidence without
a journal move. If a preserved legacy certificate needs its first verification under
this controller, adoption admits a verification-only prerequisite node referencing
that exact claim and snapshot. Its allowance is explicitly reviewed and its prior
usage remains labelled; the historical attack stays finished. Its own verification
move and new evidence version follow the normal reservation and acceptance path.

Later revalidation or repair uses the existing verification budget account, not a
fresh account on each changed digest. A verification-only successor grants no allowance
for unrelated research. If its run/move allowance is exhausted, report the exact
bounded renewal needed and require explicit user direction. Neither file tampering
nor declaring another import can manufacture a fresh research budget.

Adoption is idempotent and preserves native parents. Deeper links use the controller.
Replace the manual index with its generated view only after saving original bytes
and their digest. Do not migrate the Dittert workspace as a plugin test.

Existing data schemas remain readable; execution gains an admission preflight. Tests
that formerly started research directly must create admitted fixtures. Do not add a
production bypass for old tests. Pure legacy parser/replay tests remain permissible.

Update shared skill stages, harness contract and usage, cash-out completion wording,
decomposition/conditional/model/finite-computation strategies, and affected entries.
Remove instructions that still authorize ungated branches or premature root exit.
`AGENTS.md` can reference the shipped contract but cannot be its only source.
Installed cache files are deployment outputs, not primary implementation sources.

Keep version 0.34.0 consistent in both manifests, manifest tests, release notes, and
generated Codex entrypoints/hooks. Marketplace catalogs currently reference the Git
repository without a version pin; leave unrelated settings unchanged. Verify source
before an installed-plugin update. Do not automatically push, tag, publish, or change
the user's hook trust.

## 13. Acceptance and verification plan

Write failing regression tests before production changes. Controller tests use mock
Lean processes and tiny certificates, remain offline and deterministic, and do not
launch expensive mathematics. Run independent skill pressure scenarios before and
after instruction changes, preserving actual outcomes, including compliant baselines.
Do not invent failures to justify wording. Store research-style agent test artifacts
under `exactory-research`, using isolated temporary workspaces and one compute worker.

| Group | Required passing behavior |
| --- | --- |
| Root fidelity | Weaker quantifier, narrowed claim, missing equality case, or lower proof standard cannot close root |
| Useful branching | Full n=5,6 closure admitted; scalar fragments cannot close those cases; arbitrary finite prefixes rejected |
| Computation necessity | Reject generic root links; require omission test, sufficient domain, outcome-to-action mapping, and stopping rule |
| Necessary sampling | Admit 100 selected instances for a reviewed necessary next-step decision; passing them closes no universal case |
| Main progression | Proving or publishing B leaves C, D, and bridge open |
| AND/OR | All premises of chosen route needed; abandoned alternatives not required; circular support rejected |
| Standalone value | Missing, stale, same-author, uncertain, or negative reviews reject; proper approvals cover only exact proposal |
| Coverage | Detect gaps, endpoints, overlap, and missing quantified tails; no invented proof percentage |
| Conditions | Conditional theorem does not close consequent; imports cannot drop assumptions |
| Retreat | Preserve lineage and failures; restore eligible ancestor alternative; retain independent proved facts |
| Reset resistance | Renames, copies, no-hit runs, hypothesis snapshots, and cash-out cannot replenish accounts |
| Execution | Direct verify has no bypass; crashes, retries, timeouts, cancellations, and pre-journal runs are charged/reconciled |
| Freshness | Changed accepted snapshot, transitive dependency, review, theorem type, or journal prefix invalidates acceptance; editing an unused working copy does not |
| Lean inspection | Failed inspection with plausible axiom output fails; wrong declaration and forbidden/native axioms cannot pass kernel policy |
| Completion | All attacks finished with root open yields replan; literature finish checks children and does not imply formalization |
| Persistence | Reject stale revision, request-ID conflict, malformed event, path escape, and cycles; recover intents without duplicate execution |
| Hosts | Exercise actual supported shell/patch forms, workdir-relative paths, custom roots, missing metadata, duplicate events, and unsupported forms |
| Pacing | User stop wins; continuation cap survives branch switches; pauses do not become success; unrelated projects remain untouched |
| Adoption | Preserve bytes, native parents, indexes, uncertainty, and units; import no unverified closure |
| Imported verification | Zero-move literature import works; finished legacy certificate gets bounded first verification without reopening history; repeat imports cannot reset it |
| Success scheduling | Accepted B advances to ready C without a running parent; root-ready evidence selects final review/completion rather than research |
| Unused native children | Root closure can consume complete contributing artifacts while reporting a parent's pending native finish; no child completion is fabricated and no other failure is ignored |

Retain existing harness/client suites, adjusting setup instead of suppressing gates.
Add reducer replay tests for identical state and next actions from identical events.
Include lightweight real process-launch tests, not only mocked validators.

Release checks include client and harness tests, syntax/JSON checks, manifest parity,
`codex/generate.py --check`, and the existing coverage gate. Run optional real Lean
integration only when resources permit; report whether it ran. Do not call an unrun
host smoke test passed. Inspect installed-hook payloads before claiming end-to-end
Codex/Claude enforcement.

After design approval, implement controller/persistence, admission/routes,
execution/evidence, hooks/adapters, skill alignment, then adoption/release validation.
Independent review must attempt admission bypass and false root completion. Fix
findings in code and design, never through suppression flags or weakened gates.

## 14. Prior research and selected ideas

These sources inform organization, not a claim that their algorithms solve open
mathematics or that this plugin inherits their empirical results.

- Lample et al., *HyperTree Proof Search for Neural Theorem Proving* (2022), models
  alternative tactics and sets of subgoals that must all be proved. Adopt the AND/OR
  distinction and explicit dependencies, not its learned values, online training,
  or stochastic search. [Paper](https://arxiv.org/abs/2205.11491),
  [proof-hypergraph discussion](https://arxiv.org/html/2205.11491v1).
- Yao et al., *Tree of Thoughts: Deliberate Problem Solving with Large Language Models*
  (2023). Adopt explicit states, alternative attempts, and backtracking. Do not use
  model self-evaluation as theorem evidence or an admission oracle.
  [Paper](https://arxiv.org/abs/2305.10601).
- Jiang et al., *Draft, Sketch, and Prove: Guiding Formal Theorem Provers with Informal
  Proofs* (2022; revised 2023). Adopt the separation between proposed proof sketches
  and formal obligations that must be discharged. An outline is not a completed
  bridge. [Paper](https://arxiv.org/abs/2210.12283).

Contribution-gated admission, immutable objectives, inherited budgets, and the
publication/completion separation are design decisions for this research workflow,
not results attributed to those papers.
