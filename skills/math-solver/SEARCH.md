# Mathematical search controller JSON contract

This file is the single schema and command authority for managed mathematical
search. `SKILL.md`, strategies, entries, and harness notes link here instead of
restating closed records. A fresh objective starts with `search init`, and every
new node starts with `search propose`, independent `search review`, and
`search admit`. Local native files preserve the mathematical walk but do not
replace the controller's original objective, accepted-evidence graph, or resource
accounts.

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and the
[managed research workflow](../../docs/research-workflow.md). New proposals use
schema version 3 and a reviewed current `foundation` reference. Complete common
preparation before proposing, then export actual source bytes and native inputs
for independent review. The [native foundation contract](../../docs/research-cli.md#native-math-preparation)
defines the exact reference, export, external-verification claim binding, current
checks, and append-only `search amend-foundation` for historical nodes. Common
preparation is separate from native proof acceptance and resource accounting.

The current interface documented here includes reviewed admission, durable
pause and explicit resume, controlled deadlines, frozen input/result/delivery
identity audits, and the computation and interpretation contracts below. It does
not yet include immediate cancellation of an owned workload, full local
snapshot-byte or elapsed-time accounting, typed local-package or literature
receipts, or an automatic parent progress report. A status read never resumes a
paused objective. Local `finish`, a literature hit, a terminal run, and a native
journal `closes` flag are recorded facts, not root acceptance.

## Computation admission and mandatory interpretation

The computation field introduced in proposal version 2 remains required in
version 3 alongside `foundation`. Analytical `task.kind: proof` uses
`computation: null`. Versions 1 and 2, their reviews, admissions, reservations,
and accepted results remain readable during replay. Fresh work requires the
applicable current reviewed amendments; historical replay grants no fresh
execution permission. New run reservations include `computation_digest`,
which is null only for analytical tasks. Historical reservations without that field
are accepted only for original version 1 admissions without amendments. This is
historical replay compatibility; there is no public arbitrary event API.

The closed, versioned computation contract is:

```text
ComputationContract = {
  schema_version: 1,
  domain: FiniteDomain,
  basis: Basis,
  preflight: {
    uncertainty: Text,
    inspected_evidence: [ArtifactDigest],
    already_determined: Boolean,
    cheapest_sufficient_check: Text,
    failure_signal: Text
  },
  verification_plan: {
    certificate_shape: Text,
    checker_method: Text,
    producer_seconds: PositiveInteger,
    checker_seconds: PositiveInteger,
    checker_cap_seconds: PositiveInteger,
    fallback: Text,
    max_input_bytes: PositiveInteger
  }
}
FiniteDomain = Scope(kind="case_ids"|"integer_interval") |
  {kind: "bounded_encoding", encoding_digest: ArtifactDigest,
   max_instances: PositiveInteger}
Basis common fields = {
  kind: "root_finite_scope"|"finite_residue"|"diagnostic"|"standalone"|"counterexample",
  deduction_digest: ArtifactDigest,
  dependencies: [{acceptance_id: AcceptanceID,
                  acceptance_digest: SHA256(Acceptance), claim_digest: SHA256(Claim)}],
  completeness_acceptance_id: AcceptanceID|null,
  bound_acceptance_id: AcceptanceID|null
}
finite_residue adds exactly {reduction_acceptance_id: AcceptanceID}.
```

Every non-null acceptance reference must occur in `dependencies`. Deduction,
encoding and inspected-evidence artifacts are nonempty immutable bytes imported
through the existing explicit `inputs` list. Bound acceptances pin their entire
accepted records and exact claims. Their transitive proof dependencies must remain
accepted, meet the proposal's proof policy and introduce no additional assumptions.
Admission and each launch audit the original immutable proof artifacts and reviews.

`root_finite_scope` requires an actual subdomain of the original finite root.
For `bounded_encoding`, the proposal claim supplies the finite scope compared
with the root; accepted completeness and bound evidence must establish that the
exact encoding covers that scope. Parameter inclusion alone is insufficient.
`finite_residue` requires an accepted proof of the exact root route's bridge,
including its pinned route content, with the target among its premises. The target
has an explicit finite scope. Every other unresolved premise also has an explicit
finite scope; an unresolved named or infinite tail is refused. Finite siblings
need not already be proved. A proposed route alone grants no computation basis.
Any accepted support used to remove a nonfinite premise must be within the
reviewed basis dependency closure. Other recorded acceptances cannot supply
implicit support. The same closure receives the claim, assumption, policy,
artifact and review audits before admission and launch.

`diagnostic` uses the proposal's reviewed necessity, outcome/action map and stopping
condition. `standalone` requires the existing standalone significance record and
its two independent proposal approvals. `counterexample` requires
`task.kind: counterexample_search`. These purposes may have null completeness and
bound references; a proof-producing bounded encoding always requires both. These
are recorded semantic reviews. The controller does not decide mathematical
relevance or encoding completeness by comparing prose.

The existing independent proposal review binds the entire computation contract.
Its scope finding must assess completeness for the exact claim and encoding, and
its necessity finding must assess preflight evidence, the cheapest sufficient
check, the existing outcome/action map and the proposed verification plan. No
duplicate computation review is required for an unchanged version 2 proposal.
`already_determined: true` refuses a producer. A separately admitted verification
role can still check exact existing inputs through its independent native
input-review contract; it cannot use a generic command to repeat production.

All plan durations and the byte allowance are positive integers. Expected checker
time cannot exceed `checker_cap_seconds`, and all durations remain within the
original node timeout allowance. Native checking is capped by the admitted checker
cap. The byte allowance and producer/checker estimates are typed declarations for
subsequent execution measurement and byte-I/O work; this task adds neither a new
executor nor runtime byte accounting. The examples and test fixtures use
`max_input_bytes: 67108864` (64 MiB). A larger value needs explicit review as part
of the same immutable contract. Existing move, command-unit and worker limits
remain unchanged.

Historical finite nodes require a retrospective amendment before a new run:

```text
exactory-math search amend-computation NODE --spec amendment.json \
  --expected-revision REV --request-id REQUEST --json
amendment.json = {proposal_digest, computation: ComputationContract,
                  review: ResultReview,
                  inputs: [{path: RelativeAttackRootPath, digest: Digest,
                            kind: "artifact"|"blob"}]}
review subject = {node_id: NODE, proposal_digest, computation}
```

The original proposal digest binds task, claim, method, limits and account. A
finite structured claim retains its exact domain; a material scope change needs
a reviewed successor on the existing account. For historical textual domains,
the independent review must establish exact correspondence to the original task.
An amendment adds guarantees once and is immutable. It does not allocate resources,
resume a paused objective, edit the original proposal or review, change native
journals, or reinterpret historical output. Its pinned subject, review and basis
artifacts are audited before subsequent work. An unchanged retry retains the same
account and scope and receives the existing per-run charge.

Every actual terminal run with a computation digest needs explicit interpretation:

```text
exactory-math search interpret RUN --spec interpretation.json \
  --expected-revision REV --request-id REQUEST --json
interpretation.json = {
  result_digest: Digest, computation_digest: Digest,
  outcome: Text|null, inconclusive_reason: Text|null,
  classification: "observation"|"audit_only"|"undecided"|
                  "proof_candidate"|"counterexample_candidate",
  root_decision: {kind: "undecided"|"proof_candidate"|"counterexample_candidate",
                  reason: Text},
  remaining_obligation_ids: [ObligationID], next_action: Text
}
```

Exactly one of outcome and inconclusive_reason is non-null. A declared outcome
and its next action must match the existing admitted necessity map. The immutable
interpretation additionally pins `run_id: RUN`. Actual result bytes, command argv,
stdout, stderr and declared output artifacts are audited; arbitrary stdout is not
parsed for a mathematical verdict. An explicit `UNKNOWN` outcome, inconclusive
interpretation, timeout, cancellation or other failed run cannot claim a theorem
candidate. Successful exit codes alone supply no proof acceptance.

A candidate root decision requires matching candidate classification and a
non-standalone root connection. A partial or standalone theorem candidate may
leave `root_decision.kind: undecided`, with a reason stating what remains open.
Finite decisions and diagnostic/audit-only results cannot become theorem
candidates. Interpretation itself supplies neither acceptance nor execution
authority, and it cannot overwrite a previous interpretation.

After live-work ownership and required journal reconciliation, `next` returns
`{kind:"interpret_run",run_id,node_id}` before another mathematical launch anywhere
in the objective. Renaming or admitting another node does not clear this gate.
Pause, unresolved native intents and live runs retain their existing precedence.
Journalling and reconciliation remain available, including before a retry.
Read-only status and next never create interpretations.

The existing event log carries the following typed service operations:

```text
amend-computation -> computation_amended: {subject, review, digest: SHA256(subject)}
interpret -> run_interpreted: {interpretation, digest: SHA256(interpretation)}
```

Replay stores them in `service.computation_amendments` keyed by node ID and
`service.run_interpretations` keyed by run ID. These are projections of the same
event log, not another task database, scheduler or event store.

## Mandatory strategy reassessment

After a recorded research signal, the next research move requires a reviewed
assessment of the current objective-wide context. Signals include checkpoints,
acceptances and their invalidation, acknowledged problem changes, journal failure
observations, terminal runs and their interpretations, native strategy failures,
and retreats. A changed residual-obligation set or admitted strategy inventory
also invalidates an existing assessment. These are planning signals. A hypothesis,
inconclusive run or reassessment grants no proof credit, budget renewal, or reset
of the nonprogress counter.

Run `search strategy-context --json` to obtain the current immutable context and
`context_digest`. It includes `evidence`, keyed by stable evidence IDs,
`strategies`, `remaining_obligation_ids`, and `planning_priorities` (selected
routes, obligation orders, deferred node IDs and route alternative orders).
The response also includes
`plan_bindings`, `native_eligibility` (each strategy's `eligible` and `defects`),
`stale_plan_node_ids`, `required`, `policy_enabled`, `upgrade_required`, and the
latest `assessment_id` and `assessment_digest`. This command is read-only. It
neither reserves work nor resumes a paused objective. `search status` reports
assessment freshness against recorded evidence; use `strategy-context` to check
mutable native plans too.

Update relevant precondition answers from the current problem, preserving native
failure records. Run `plan` for the current problem and prepare a valid ranking
when needed. Study new methods and admit new scopes through their normal reviewed
proposals. Then prepare this closed record:

```text
reassess --spec FILE:
  {assessment: StrategyAssessment, review: ResultReview}
StrategyAssessment = {
  context_digest: SHA256(CurrentContext), author: Provenance,
  what_changed: Text, remaining_obligation_ids: [ObligationID],
  considered_failure_ids: [EvidenceID],
  assessments: [{node_id: NodeID, strategy: Text,
    disposition: "continue"|"revise"|"defer"|"retire",
    reason: Text, next_action: Text, evidence_ids: [EvidenceID],
    failure_resolutions: [{failure_id: EvidenceID, changed_input: Text,
                          evidence_ids: [EvidenceID]}]}],
  plan_bindings: {NodeID: {problem_digest: Digest, preconditions_digest: Digest,
                         openings_digest: Digest, ranking_digest: Digest}},
  strategy_order: [{node_id: NodeID, strategy: Text}],
  next_hypotheses: [{statement: Text, evidence_ids: [EvidenceID],
                     success_criterion: Text, failure_signal: Text}],
  no_new_hypothesis_reason: Text|null
}
```

Classify every current nonterminal admitted strategy exactly once, including
those no longer promising. Cite context evidence for every decision and hypothesis
when evidence exists. Preserve exactly the current residual obligations and
explicitly consider every retained native failure. `strategy_order` contains
exactly the continuing strategies, in the reviewed execution order; it can replace
the previous active node with a better alternative. A later `replan` that changes
route or obligation priorities or node deferrals invalidates this assessment.
The next review must consider those recorded priorities; once current, its global
strategy order governs new research subject to native prerequisites, readiness
and deferrals. An administrative replan that leaves those choices unchanged does
not invalidate it. `plan_bindings` contains
exactly the continuing nodes, using the digests returned by `strategy-context`.
Their native plans must be current, and each continuing strategy must be legal
under its studies, opening ranking and existing walk. The native problem digest
uses the harness's JSON encoding; copy it from the response rather than recomputing
it with the controller's blob encoding.

`revise`, `defer`, and `retire` exclude a strategy from new research without
deleting its history, obligations, accounts or local reporting. Record either
new hypotheses with checkable success and failure criteria, or a nonempty
`no_new_hypothesis_reason` and an empty hypothesis list. Continuing an existing
method requires an explicit justification against the current evidence; inventing
a new method for every signal is not required.

Native failures are retained from controlled receipts and node observations,
including the last failure output when available. Clearing a mutable note cannot
restore a natively failed strategy in the same node. A reviewed successor using
that method must address each related failure with a concrete changed input and
checkpoint, acceptance, journal or run evidence recorded after the failure.
Related failures include logical ancestors, the same resource-account lineage,
and nodes with the same claim. A new slug or sibling alone is insufficient.

Obtain a separate reviewer with a fresh context. Give the reviewer the original
claim, full current context, previous assessments and retained failures, referenced
immutable evidence, proposed assessment, studies, and current plans. Ask whether
each disposition follows from what changed, whether any failed method is being
repeated without its obstruction being addressed, and whether the next hypotheses
can advance the residual claim. Use `ResultReview` with
`subject_digest = SHA256(StrategyAssessment)` and
`claim_digest = SHA256(Contract.original_claim)`. All five findings are required.
The reviewer must differ from both the assessment author and every current
admission author in actor and attestation IDs. The harness checks record coverage,
bindings and declared independence; it does not authenticate identities or decide
mathematical relevance from prose. Do not fabricate a reviewer identity.

Submit the reviewed record with the current revision and a unique request ID.
The service freezes the context, assessment, review and native plans, and records
an `assessment-000001` style ID with its predecessor. Replaying the original
request returns its original result and cannot clear later progress. Read
`search next --json` again after submission. An old assessment or a new `replan`
alone cannot authorize execution.

`reassess_strategies` takes priority over new research. Pending execution,
reconciliation, journal acknowledgement, required interpretation, result
verification and acceptance, due local cash-out, and audited root completion
retain their existing priority. Reassessment itself requires pending work to be
reconciled and terminal computations interpreted. `begin` binds purpose, context,
assessment and plan digests. A generic producer rechecks them at reservation and
launch, so an old pending move cannot run another producer after a new result.
Input-reviewed certificate and Lean verification can finish evidence under their
existing contracts. A verification-purpose reservation cannot start a generic
producer. Changes to native planning files after review or reservation are refused
before producer execution.

New controllers enable this policy in their initialization transaction. Old
event histories retain their original replay semantics. The current service
previews the upgrade read-only and activates it with the first successful new
`begin` or `reassess`; rejected requests commit no upgrade prefix. Historical
journal observations are recovered from their frozen acknowledged bytes. New
producer calls check research freshness even while finishing a historical
reservation. Existing recovery and result verification remain available.

`SEARCH_TREE.md` and each `LINEAGE.md` retain the assessment sequence, predecessor
IDs, snapshot links, decisions, evidence and failure references, and hypotheses.
They preserve the previous investigation and checkpoint sections. They are
generated views; the event log remains authoritative.

## Metered execution and native integration

The four-field document and event envelopes below remain unchanged. Execution
events are typed operations inside the existing `service_operation` payload.
`execution_state.py` is pure; `execution.py` owns process I/O; `integration.py`
owns native guards, intent reconciliation, and stage observations. Existing
`evidence.py` owns input freezing and provenance auditing.

The closed public execution specs are:

```text
begin NODE --spec FILE:
  {strategy: Text, entry: Text, pass: Int[1,3],
   trigger_features: [Text], step_cites: [Text]}
reconcile:
  {} (ordinary recovery; omit --spec or supply an empty object)
reconcile --spec FILE:
  {rank_recovery: ReviewedRankRecovery} (see harness/RANK_RECOVERY.md)
run NODE --spec FILE:
  Common plus exactly one tagged variant below
Common = {kind: "command"|"certificate"|"lean", step_dir: RelativeStepName,
  artifacts: [{path: RelativeAttackRootPath, digest: Digest, role: ArtifactRole}],
  external_dependencies: [{path: AbsolutePath, digest: Digest}],
  environment: {NonsecretEnvironmentKey: String}, dependency_enumeration: Text,
  timeout_seconds: PositiveInt, expected_outputs: [RelativeOutputPath]}
command adds {argv: [Text]}
certificate adds {input_review: ResultReview, input_modes: [InputMode]}
lean adds {input_review: ResultReview, input_modes: [InputMode], requested_declaration: Text,
  requested_type: Text, toolchain_inventory_digest: Digest,
  inspection_source_digest: Digest}
InputMode = {path: RelativeAttackRootPath, mode: Int[0,511]}
```

All mutations retain expected-revision and request-ID checks. Begin derives the
next move number and legal walk from the actual native record. Studies, admitted
strategy, claim, pass and native flow limits must match. A move is not an
unmetered planning token: its eventual journal line must bind exactly that entry,
pass, walk, triggers, citations, and prior journal bytes. Begin preserves the
native canonical problem bytes under the reservation's problem digest. If the
problem changes during that move, the journal intent records both problem
values and its exact native line. Both values must validate and retain the
admitted claim. The reservation keeps its original digest; the intent and
acknowledgment bind the updated digest. The native journal schema remains
unchanged, including its derived problem digest and its existing
`problem_changed` comparison with the previous journal line or plan.

Normal journal writers and reconciliation share nonblocking per-node ownership
through durable acknowledgment. Recovery validates the frozen append before
writing it, appends at most once, and charges the original reservation once.
Existing reservations without a stored preimage can supply the exact original
problem value with `journal add --problem-before PATH`. See
[JOURNAL_TRANSITIONS.md](harness/JOURNAL_TRANSITIONS.md) for the recovery and
version boundaries. A problem transition does not change frozen run inputs,
authorize a new claim or workload, accept a proof, or reset a budget.

Generic commands require an admitted finite task and its reviewed necessity,
complete input domain, outcomes and stopping condition. An analytical proof
admission does not authorize arbitrary computation. The controller binds the
actual task and inputs but cannot infer arbitrary code's mathematical purpose
from filenames or labels. Domain completeness and semantic correspondence remain
independent review responsibilities. Generic success never creates certificate
or kernel verification status.

Exact absolute local argv elements, including argv[0], are mapped from declared
artifacts beneath the attack root into the captured build tree. A relative local
executable containing `/` is resolved from the declared step directory. An
executable selected from a local PATH entry is also mapped to its declared frozen
artifact. An undeclared absolute local path is refused before reservation. This mapping is not
a shell parser: paths embedded in source code or compound option strings remain
part of the reviewed semantic/dependency boundary, not an OS sandbox guarantee.
External dependencies remain an explicitly trusted read-only boundary.

Every run records the actual permission mode of each frozen artifact, in artifact
order, and restores that mode separately on its input/build copies. Special
permission bits are unsupported. Native `input_modes` also belongs to the reusable
input-review subject; changing source permissions requires a new input-bound
review. Generic runs derive modes at reservation. Pre/post execution checks cover
copied bytes and modes, and verification audits bind run modes to the reviewed
specification. No input is made executable merely because another file needs it.

Before native reservation, an independent `ResultReview` must approve exactly
`{node_id, task, spec_without_input_review}` and the node's claim digest. The
reviewer must differ from the proposal author. Native argv is derived, not caller
supplied: certificate uses `/bin/sh check.sh`; Lean uses the resolved installed
Lake executable for `build`, then `env lean axioms-check.lean`. The subject uses
logical project paths and freezes that protocol's generated source. A matching
claim digest alone does not establish correspondence. Changed source, environment,
dependency inventory or verifier source needs a new input-bound review. Unchanged
retries can reuse the review, with a fresh charged run reservation each time.

Legacy verify reads `verification-review.json`. Its `step.json` may declare
`environment`, `external_dependencies`, `dependency_enumeration`, and (for Lean)
must declare `theorem` and `requested_type`, with optional `file`. The review is
stored immutably outside its own snapshot. Native `result.json`, `inspection.log`
and generated `axioms-check.lean` are output metadata, not frozen source inputs.
This exclusion does not authorize omitting actual checker dependencies. All other
local project files outside caches must be enumerated. Native PATH and resolved
verifier binaries are pinned. Environment values may be empty strings, but never
contain NUL; credential-like keys and loader/Python injection variables are refused.
The executor supplies bounded thread settings and does not inherit credentials.

The default limits remain eight moves per pass, three passes, 24 moves overall,
24 command units, 300 seconds per workload and one compute worker. Native and
generic paths share the same current-account transaction checks. A certificate or
generic workload reserves one command unit. A Lean workload reserves two units
atomically under one run ID. Build and inspection share one wall-time deadline.
A durably failed build releases only the inspection unit proven never started.
Uncertain crashes retain all reserved charges and prevent replacement. Rejected
preflight spends nothing. `len(runs)` counts workloads, not consumed command units.

The closed execution operation payloads are:

```text
move_reserved: {reservation: MoveReservation}
MoveReservation = {id, node_id, account_id, move, pass, strategy, entry, walk,
  trigger_features, step_cites, problem_digest, journal_prefix_digest,
  purpose: "research"|"verification", strategy_context_digest,
  assessment_digest: Digest|null, planning_digest}
journal_intended: {reservation_id, before_digest, after_digest, problem_digest,
  journal_line: NativeJournalLine,
  problem_transition?: {before: NativeProblem, after: NativeProblem,
    line: NativeJournalLine}}
journal_acknowledged: {node_id, move, reservation_id,
  journal_prefix_digest, problem_digest}
run_reserved: {run: RunReservation}
RunReservation = {id, node_id, account_id, reservation_id, kind, input_digest,
  spec_digest, task, computation_digest, strategy_context_digest,
  cwd, snapshot_root, output_root, commands, timeout_seconds,
  environment, threads, expected_outputs, dependency_enumeration,
  executable_bindings, reserved_units, token, requested_declaration,
  requested_type_digest, toolchain_digest, toolchain_inventory_digest,
  inspection_source_digest, publication_prestate, input_modes: [InputMode]}
executable_bindings = [{path: AbsolutePath, digest: Digest}]
publication_prestate = [{path: RelativeAttackRootPath, digest: Digest|null}]
run_launched: {run_id, token, identity: {pid, process_group, start_identity, token}}
run_finished: {run_id, token, status: "terminal"|"indeterminate",
  started_units, charged_units, result_digest, termination,
  legacy_result: PathDigest|null, legacy_inspection: PathDigest|null,
  outputs: [PathDigest], inspection: Inspection|null}
PathDigest = {path, digest}
Inspection = {printed_type_digest, source_digest,
  declaration_axioms: [Text], correspondence_axioms: [Text]}
```

New service operations always include the strategy fields above. Historical
move reservations can omit the four fields from `purpose` through
`planning_digest` together, historical run reservations can omit
`strategy_context_digest`, and historical journal intents can omit `journal_line`.
These legacy shapes preserve original replay; they provide no public bypass for
new research. A present journal line is checked against the reserved entry and
the frozen appended bytes, and must equal the transition line when one exists.

The authoritative run adds `status: reserved`, `identity: null`, zero started and
charged units, and null result/termination on reservation, then retains lifecycle
facts. Native-only metadata is null for generic runs. Existing frozen-input and
terminal verification result formats below are unchanged. A generic result is
`{schema_version: 1, kind: "command", run_id, claim_digest, input_digest, commands}`;
it is execution evidence only. Each command retains exact argv, exit code and
immutable stdout/stderr digests. Streams and each expected output are bounded to
1 MiB. Expected outputs are written beneath `EXACTORY_OUTPUT_DIR`, separately from
the build directory.

The executor creates immutable input and separate working/build and output
directories beneath `.search/runs/RUN`. A launcher acquires its ownership lock,
persists PID/process-group/start identity and a random token, then waits on a pipe.
The controller durably acknowledges that identity before sending the token that
permits mathematical execution. Every attempted command gets a durable started
marker. The launcher records bounded outputs and a terminal token-bound receipt.
Reconciliation commits that original result exactly once. No marker means an
uncertain charged run, not permission to retry. Recovery does not signal an
uncertain PID and never reruns the job. Timeouts terminate only an owned live
child process group; uncertain descendants block replacement.

Shared external dependencies remain an explicitly trusted read-only boundary,
not an OS-hermetic sandbox. Installed Lean files have a content-addressed
provenance inventory `{root: AbsolutePath, files: [{path: RelativePath, digest}]}`.
Exact membership and hashes are checked before launch, between the two commands,
after execution and during full acceptance/completion audits. The existing pinned
installation is resolved without downloading or asking elan to select another
toolchain. The controller does not install software or change trust settings.

Lean inspection captures the complete original printed type between unique
delimiters. It generates the named `exactory_correspondence` theorem of the
requested source type and records both that theorem's and the original theorem's
axioms. A successful expected-type assignment can insert coercions; checking only
the original theorem misses their dependencies. Both axiom records must satisfy
policy. The requested type digest remains in the existing terminal record; the
complete printed type, generated source and both axiom lists remain run provenance.
Pretty-printed text equality is not a general type-equivalence proof.

Native results and the complete Lean inspection stdout are published once at
their recorded project paths. Recovery checks the pinned previous or exact target
bytes before writing; intervening edits cause `recovery_conflict`. Final checked
packages must include the exact accepted result and inspection versions as well
as accepted input versions across the manifest closure. A refreshed package cannot
substitute another run's plausible result. Conflicting accepted path versions are
reported explicitly.

Native non-journal operations use `native_intended` with exactly
`{id,node_id,command,args_digest,pre_digest,output_paths,ownership_digest,strategy}`.
`strategy` is the failed method for `fail` and null for other commands; historical
intents may omit it. The pinned pre/post
snapshot blob is `{files:[{path,digest}]}` and the args blob contains the original
native parsed arguments except its Python callable. Native success writes a
durable receipt before acknowledgement. `native_acknowledged` is exactly
`{id,outcome:"succeeded"|"unchanged",post_digest}` or
`{id,outcome:"failed",post_digest,diagnostics:[Text]}`. A caught validation failure
may record permitted changes such as removing a stale unit stamp without claiming
success. Missing receipts are not inferred from file presence or absence. A
conflict preserves the user's files and reports the original intent and command.
Unresolved native intents block new research mutations. Reconcile does not replay
an unknown native operation or duplicate a prior acknowledgement.

Before recording the intent, the synchronous native invocation exclusively locks
its fresh `native/ID.lock` file and retains that descriptor through all native
writes and acknowledgement. Its immutable ownership blob is
`{id,pid,start_identity,device,inode}`. The lock file is never unlinked or recreated
by the protocol. The invocation closes its descriptor on stack termination;
process death also releases OS ownership. Reconciliation must acquire that exact
recorded lock before acknowledging a terminated invocation. A stored PID alone
is not proof of liveness or death. A live lock, a missing lock, or replaced ownership
evidence blocks reconciliation, even when a receipt exists. Only the owning
invocation can acknowledge its own durable receipt while retaining the lock.
With no receipt, an acquired original lock and exact unchanged pre-state permit
an unchanged acknowledgement; changed state remains ambiguous.

An ambiguous changed state returns `recovery_conflict` with `intent_id`,
`original_command`, the pinned `original_args`, and exact absolute
`conflicting_paths`. Stop at operator handoff. Preserve those files, the intent,
its argument/pre-state/ownership blobs, the original lock file, and any receipt.
This release provides no replay or automatic overwrite for an ambiguous native
effect. Do not rerun the uncertain command, edit controller records, infer success
from matching output bytes, or restore files merely to make reconciliation pass.
An operator must inspect the preserved evidence and determine a supported repair
before work continues. Ordinary reconciliation only consumes an existing valid
receipt, or acknowledges the exact unchanged pre-state after acquiring its lock.
The explicitly reviewed, opt-in [legacy rank operator recovery](harness/RANK_RECOVERY.md)
is a separate abandonment decision for its narrowly defined initial interruption.
It preserves the changed inputs and all prior records, does not fabricate a native
receipt, and does not establish the original process outcome or successful rank.

Guarded legacy mutations require admission; journal and verify also require the
current reserved move. Provisional `init` without a parent and read-only native
inspection remain available. In 0.34.0, `init CHILD --from PARENT` intentionally
returns `admission_required` and directs callers to reviewed propose/review/admit
with `native_parent`. Only that controller effect creates a native child. The
existing parent.json schema, depth-one rule, opened-after-move count and both
finish paths' child checks remain unchanged.

Native observations retain cash-out stages, actual local finish, failure and
stagnation facts. Exact controlled producer receipts/results request verification,
snapshot or acceptance without conferring proof credit. Checkpoint and acceptance
transactions refresh those observations. A same-claim historical checkpoint with
another move, run or input snapshot cannot advance the current result frontier.
Begin may service an exact verification frontier, but not a snapshot/acceptance
frontier. Local finish is separate from root proof closure. Bounded read-only
status adds `process_observations` with live, terminal, pending_reconciliation or
indeterminate per run. A recorded active/launched state is not proof of liveness;
only current launcher ownership establishes live. Evidence freshness remains
unchecked unless an explicit full audit has succeeded.

This document defines the executable Task 2 through Task 5 interfaces for the persistent search
controller. It does not publish results or replace the
existing math-solver workflow. Checkpoints, proof acceptance, and scheduling are
pure logic. Task 4 implements the filesystem service, CLI, adoption and generated
views. Task 5 adds metered execution, reservation reconciliation and native guards.
Host adapters remain subsequent tasks.

## Pure model and storage integration

`search_controller.model` exports `initial_state(contract, objective_id) -> dict`,
`apply_event(state, event) -> dict`, and `replay(document) -> dict`. Inputs remain
unchanged, including on failure. Replay performs no I/O, creates no files, and runs
no processes. Unknown events fail with `SearchError(code="unknown_event", ...)`.

`Store` takes the controller directory, normally `attack/.search`. The event
document has exactly `schema_version`, `objective_id`, `contract`, and `events`.
Schema version is integer `1`. Each event has exactly `sequence`, `request_id`,
`kind`, and `payload`. Sequence starts at one and increases by one. Storage handles
identical request replay and conflicting reuse before invoking validation.
The pure reducer rejects duplicate event IDs or noncontiguous sequence numbers.

Use `validate=lambda document, event: apply_event(replay(document), event)` with
`Store.append`. Validation runs under the writer lock. The storage layer supplies
isolated snapshots; the callback cannot change the event being persisted.

Supported events and exact payloads:

| Event | Payload |
| --- | --- |
| `proposal_recorded` | `{"proposal": Proposal, "digest": SHA256(Proposal)}` |
| `review_recorded` | `{"proposal_id": ID, "review": Review, "digest": SHA256(Review)}` |
| `proposal_admitted` | `{"proposal_id": ID}` |
| `checkpoint_recorded` | `{"checkpoint": CheckpointInput, "digest": SHA256(CheckpointInput)}` |
| `result_accepted` | `{"acceptance": AcceptanceInput, "digest": SHA256(AcceptanceInput)}` |
| `evidence_invalidated` | `{"acceptance_ids": [ID], "reason": Text, "provenance": Provenance}` |
| `objective_completed` | `{"closure": Closure, "digest": SHA256(Closure)}` |
| `node_facts_recorded` | `{"facts": NodeFacts}` |
| `node_retreated` | `{"retreat": Retreat}` |
| `replan_recorded` | `{"route_orders": [RouteOrder], "progress_acceptance_ids": [ID], "reason": Text}`, optionally also `"obligation_orders": [ObligationOrder]` and/or `"deferred_node_ids": [NodeID]` |
| `control_recorded` | `ControlInput` |
| `strategy_policy_enabled` | `{version: 1, journal_observations: [{reservation_id: ID, line: AcknowledgedJournalLine}]}` |
| `strategies_reassessed` | `{assessment: StrategyAssessment, review: ResultReview, digest: SHA256(StrategyAssessment)}` |

Digests use `SHA256(canonical_bytes(record))`. Canonical JSON is sorted-key,
compact, UTF-8, finite JSON, with Unicode preserved. Records are inline in the
event for pure replay and include their input digest. The service also stores and
checks the corresponding immutable blobs. Pure validation checks bindings; it
does not claim that an artifact or attestation has been externally authenticated.

All record shapes below are closed. All listed fields are required, including
nullable fields. Unknown fields and enum values are errors. Text must be nonempty
unless stated otherwise. Lists of IDs/text must be unique. Integer allowances do
not accept booleans. Validators are in `search_controller.schema`: `validate_scope`,
`validate_claim`, `validate_contract`, `validate_limits`, `validate_provenance`,
`validate_checkpoint_criterion`, `validate_retreat_criterion`,
`validate_necessity`, `validate_decomposition`, `validate_proposal`, and `validate_review`.
They raise `SearchError` and do not modify their input.

## Contract, claims, and scopes

Contract fields:

```text
schema_version: 1
original_claim: Claim
assumption_ids: [ID]
root_obligation: "obligation-000001"
root_attack_slug: Slug
requested_outcome: "proof" | "decision"
proof_policy: "reviewed" | "certificate" | "lean-kernel"
required_deliverables: [Text]
resource_policy: {
  max_total_moves: PositiveInteger | null,
  max_total_runs: PositiveInteger | null,
  max_workers: PositiveInteger
}
```

The original claim's assumptions and proof policy must match the contract. The
first root obligation is fixed; proposals never edit it. The service assigns
`objective_id` when it initializes this contract. The reducer accepts a nonempty
objective ID supplied by that service. Slugs use `[a-z0-9][a-z0-9_-]*`.

Claim fields are `statement: Text`, `quantifiers: Text`, `assumption_ids: [ID]`,
`proof_policy: Policy`, and `scope: Scope`. Assumptions name contract assumptions
or already declared obligations. An added assumption is explicit in the claim;
it does not discharge an unconditional target.

Scope is one of these exact tagged shapes:

```json
{"kind": "named", "name": "general quantified obligation"}
{"kind": "case_ids", "case_ids": ["case-a", "case-b"]}
{"kind": "integer_interval", "lower": 5, "upper": 15,
 "lower_inclusive": true, "upper_inclusive": true}
```

Case lists are nonempty. Integer intervals must contain at least one integer,
including after applying endpoint flags. Infinite domains use named quantified
obligations. No symbolic set parser or inference from prose is provided.

## Proposal

```text
schema_version: 3 (versions 1 and 2 are historical replay)
author: Provenance
category: "main" | "coverage" | "standalone"
relationship: "main" | "prerequisite" | "coverage" | "alternative" |
              "continuation" | "standalone"
attack_slug: Slug
role: "research" | "verification"
claim: Claim
target_obligation: ObligationRef | null
logical_predecessor: NodeID | null
native_parent: NodeID | null
anchor: {kind: "objective", digest: ContractDigest} |
        {kind: "checkpoint", checkpoint_id: CheckpointID, digest: CheckpointDigest}
inherited_evidence: [SHA256]
inherited_assumption_ids: [ID]
hypothesis: Text
method: Text
applicability: Text
success_criterion: Text
failure_criterion: Text
parent_effect: Text
studies: {
  problem: SHA256, novelty: SHA256,
  strategies: [{method: Text, digest: SHA256}]
}
contribution: {
  route: RouteRef | null, deduction: Text, necessity: Necessity | null,
  coverage: Coverage | null, standalone: Standalone | null
}
task: {
  kind: "proof" | "finite_decision" | "finite_proof" | "counterexample_search",
  purpose: Text, input_domain: Text
}
computation: ComputationContract | null  (required in versions 2 and 3; absent in version 1)
foundation: FoundationReference  (required in version 3; defined in the linked native foundation contract)
limits: {
  max_moves: Integer[1,24], max_runs: PositiveInteger,
  timeout_seconds: PositiveInteger, workers: PositiveInteger
}
budget: {
  mode: "new" | "inherit" | "renew", account_id: AccountID | null,
  basis_checkpoint_id: CheckpointID | null,
  basis_checkpoint_digest: SHA256 | null, justification: Text
}
equivalent_node_ids: [NodeID]
checkpoint_criteria: [CheckpointCriterion]
retreat_criteria: [RetreatCriterion]
decomposition: Decomposition
```

The normal template supplies 24 moves, 24 runs, 300 seconds, and one worker. Worker
counts cannot exceed the contract. Eight moves per pass, three passes, and the
24-move local cap remain mandatory in the execution integration. A continuation
retains its predecessor's admission category and exact account. A native parent
is a controller node ID whose node must be unfinished and not itself a native
child; generated lineage resolves its display slug without rewriting native files.

A direct attack on the original objective can use `route: null`; its claim must
match the original obligation exactly. Other main/coverage targets require a
proposed route connected to the root. Route admission is not bridge acceptance.
Standalone admission requires `target_obligation: null`, no root route, and no
root decomposition. Its immutable `claim_digest` is the standalone claim identity
bound into its node. It creates no root coverage.

The objective anchor applies to an initial attempt with no inherited evidence.
A logical successor must bind an existing checkpoint, and inherited evidence must
be contained in that checkpoint's `evidence_digests`. Hypothesis anchors preserve
lineage but do not create proof or budget credit. A selected method needs its own
strategy-study entry. All text assessments are inspected by independent reviewers;
nonempty text is a structural requirement, not mathematical certification.

Necessity fields:

```text
obligation_id: ObligationRef | null
omission_consequence: Text
domain_justification: Text
outcomes: [{outcome: Text, next_action: Text}, ...]
stopping_condition: Text
```

All finite task kinds require this record. It must bind the targeted obligation;
only a standalone task uses null to bind its own prospective claim. The outcomes
must contain at least two distinct alternatives, with explicit next actions.
Independent review assesses whether the omission really blocks progression,
whether the finite domain suffices, and whether the outcomes and stopping rule
cover the stated decision. A generic strategy/checker disclaimer is insufficient.
Finite-decision admission itself never closes an obligation. Future execution
guards must bind every run to this admitted purpose, domain, and allowance.

Coverage fields are `parent_obligation: ObligationRef`, `scope: Scope`,
`partition_route: RouteRef`, and `subset_deduction: Text`. Only coverage proposals
carry this record. The subset must equal the target scope and stay within the
parent's finite interval or explicit cases. Named subsets require exact named
scope identity. The target must be a premise of the declared parent route. Full
partition exhaustiveness and bridge proof remain acceptance obligations.

Standalone fields are `prospective_theorem: Text`, `primary_sources: [SHA256]`,
`strongest_known_result: Text`, `mathematical_contribution: Text`, and
`significance: Text`. Primary sources are nonempty pinned study/source digests.
Only standalone proposals carry this record.

## Reviewed decomposition and criteria

```text
Decomposition = {
  obligations: [{key: Slug, claim: Claim}],
  routes: [{
    key: Slug, conclusion: ObligationRef, premises: [ObligationRef],
    bridge: ObligationRef, alternative_order: [NodeID]
  }]
}
```

`new:<key>` references another entry in the same proposal, separately within the
obligation or route namespace. Other references are stable IDs already in state.
New stable IDs are assigned only at admission. Repeated structurally equivalent
obligations reuse the existing identity. Route premises are ordered and nonempty;
premises and bridge are distinct. Cycles, dangling references, and targets that
cannot reach the root are rejected. `alternative_order` names already admitted
nodes targeting the route conclusion. Later replan events own order amendments.

Every checkpoint criterion has `kind`, `criterion_id`, and `explanation`, plus
exactly the fields in the table. The list is nonempty and criterion IDs are unique.

| Kind | Additional fields |
| --- | --- |
| `accepted_obligation` | `obligation_id: ObligationRef` |
| `accepted_case_set` | `obligation_id: ObligationRef`, `case_ids: [Text]` (nonempty) |
| `verified_reduction` | `obligation_id: ObligationRef` |
| `verified_obstruction` | `route_id: RouteRef` |
| `hypothesis_recorded` | None |

Retreat criteria are nonempty and have exactly `kind` and the corresponding field:

| Kind | Additional field |
| --- | --- |
| `strategy_failure` | `strategy_id: Text`, matching a studied method |
| `move_limit` | `threshold: PositiveInteger` |
| `run_limit` | `threshold: PositiveInteger` |
| `stagnation_window` | `threshold: PositiveInteger` |
| `method_prerequisite_failed` | `obligation_id: ObligationRef` |

These are typed data, never executable expressions. Task 3 owns predicate
evaluation and checkpoint/retreat handlers.

## Review and provenance

```text
Provenance = {source: "host" | "operator", actor_id: Text, attestation_id: Text}
Review = {
  schema_version: 1, subject_digest: SHA256, claim_digest: SHA256,
  reviewer: Provenance, decision: "approve" | "revise" | "reject",
  findings: {
    root_connection: String, mathematical_substance: String,
    assumptions: String, scope: String, equivalence: String,
    necessity: String, novelty: String, significance: String, renewal_basis: String
  },
  unresolved_objections: [{blocking: Boolean, description: Text}]
}
```

The subject digest covers the full immutable proposal, including studies, task,
limits, and decomposition; the claim digest covers its exact claim. Reviews never
occur inside their own hashed subject. The author and reviewers must have distinct
actor IDs and attestation IDs. Two reviewers of one proposal must also differ in
both fields. The host/operator supplies the records. The CLI must validate their
provenance boundary; a user-editable name is not cryptographic authentication of
intellectual independence. `source: "model"` is not accepted.

All reviews used for admission must approve and have no unresolved blocking
objection. Main and coverage need one independent approval. Standalone needs two.
All require nonempty mathematical-substance, assumptions, scope, and equivalence
findings; main/coverage also require root connection, standalone requires novelty
and significance, finite tasks require necessity, and renewal requires renewal
basis. Inapplicable findings use empty strings. Admission reviews cannot be added
or changed after admission. A revise/reject outcome needs a revised proposal and
new bound reviews; it cannot be hidden by adding an optimistic review.

## Derived state and stable accounts

State contains exactly the initial model fields `schema_version`, `objective_id`,
`contract`, `contract_digest`, `revision`, `obligations`, `routes`, `nodes`,
`proposals`, `reviews`, `accounts`, `checkpoints`, `acceptances`, `runs`, `control`,
`totals`, `next_ids`, `proof_status`, `execution_status`, `publication_status`,
and `requests`. Tasks 3 and 5 extend their owned records and events.

Collection keys are assigned in stable creation order: `proposal-000001`,
`review-000001`, `route-000001`, `node-000001`, `account-000001`, and so on.
`next_ids` also reserves counters for checkpoint, acceptance, and run IDs.
Failed transitions consume no IDs in the input state. Slugs are never identities.

An obligation has `id`, `schema_version`, `claim`, `claim_digest`, `proposal_id`
(null for the root), and `status` (initially `open`). A route has `id`,
`schema_version`, `conclusion`, `premises`, `bridge`, `proposal_id`,
`proposal_digest`, `review_ids`, `alternative_order`, and `status` (initially
`hypothesis`). All route references are normalized stable IDs.

Proposal wrappers have `id`, `schema_version`, `record`, `digest`, and `status`
(`proposed` or `admitted`). Review wrappers have `id`, `schema_version`,
`proposal_id`, `record`, and `digest`.

A node has `id`, `schema_version`, `attack_slug`, `obligation_id`, `claim`,
`claim_digest`, `role`, `category`, `relationship`, `logical_predecessor`,
`native_parent`, `checkpoint_id`, `proposal_id`, `account_id`, `status`, `route_id`,
`checkpoint_criteria`, `retreat_criteria`, and `admission`. Node `status` starts
`admitted`; later lifecycle transitions are `active`, `waiting`, `result_ready`,
`retreated`, and `finished`. `admission` has exactly `proposal_digest`, `review_ids`,
`contribution`, `studies`, `task`, and `limits`. Contribution remains the original
review-bound record, with proposal-local references where originally supplied;
the node, graph, and criteria contain the resolved stable references.

Account fields:

```text
id, schema_version, owner_obligation, owner_claim_digest, lineage_owner,
predecessor_account_id, max_moves, max_runs,
used_moves, used_runs, reserved_moves, reserved_runs,
historical_usage, historical_moves, historical_runs,
renewal_basis, renewal_basis_digest
```

`used_*` and `reserved_*` are nonnegative cumulative counters within that account's
allowance, with no reset on continuation. Historical totals carry earlier account
segments in the same lineage. `historical_usage` is `known` or `unknown`; unknown
`historical_moves` and `historical_runs` are null, not zero. `lineage_owner` remains
the first account ID, and renewal preserves its predecessor's counters.

`budget.mode: new` has null account and checkpoint bindings; it still reuses an
account if a target, structurally equivalent claim, or declared equivalent node
already has one. This implicit discovery resolves the matching node's account
lineage to its unique current segment, even when a reviewed renewal targets a
different obligation and only the historical node matches the new proposal.
It preserves the current segment's used/reserved counters and inherited history;
it does not create another allowance. Explicit selection of that current segment
is related to historical targets in the same lineage. Explicit selection of the
historical segment itself is still rejected. `inherit` requires an account and
null checkpoint bindings.
It cannot increase that account's limits. Only the unique current segment of a
lineage can receive new admissions or resource reservations. Explicitly choosing a
superseded account fails with `account_superseded`, even if it has unused allowance.
Continuation uses the exact predecessor account, which must still be current;
it cannot switch to a different segment. Structural identity normalizes assumption
order, case-set order, and
equivalent integer endpoint representations; general semantic equivalence is a
required review finding. A copied obligation or renamed directory grants no credit.

`renew` requires an existing account, a checkpoint ID and its digest, and the
independent renewal finding. Its selected account must be the current segment, and
the method must differ from prior methods throughout that account lineage. The
attempt cannot be a continuation or declared equivalent copy, and pending
reservations anywhere in the lineage must first be reconciled. The next segment
retains the current segment's historical totals plus its cumulative usage, so no
intermediate allowance segment can disappear from the new history.
Qualifying progress is an existing
checkpoint whose `kind` is `proof`, `reduction`, or `obstruction`, joined to an
internal acceptance with matching `checkpoint_id`, matching `checkpoint_digest`,
and `status: accepted`. A `hypothesis` never qualifies. Exact external results may
use `origin.kind: external_result`, without any fabricated journal move. The
review must explain how the evidence addresses the earlier obstruction. A lineage
cannot reuse the same checkpoint to renew repeatedly, including a copy whose only
change is its controller ID. New accounts receive a
finite local allowance and retain historical usage, including unknown history.

`qualifying_progress(state, checkpoint_id, expected_digest)`,
`require_current_account(state, account_id)`, and `remaining_allowance(account)`
are pure admission helpers. `require_current_account` returns the existing current
account without modifying state, rejects a superseded segment with
`account_superseded`, and rejects an ambiguous lineage with `corrupt_state`.
`remaining_allowance` returns
`{"moves": remaining, "runs": remaining}` and raises `usage_unknown` for an
unknown account without reviewed renewal. A renewal basis must remain accepted
when it supports subsequent admission. The service must freshness-audit the
checkpoint's complete dependency closure before consumption and invalidate stale
acceptance status. A public `verified: true` flag never establishes this fact.
Task 3 owns checkpoint/acceptance events; Task 5 owns actual reservation counters.

Before every new move or run reservation, Task 5 must invoke
`require_current_account` on the node's account inside the locked reducer
transaction, then check its remaining allowance. Account-local subtraction alone
cannot establish permission: an earlier admitted node can retain a historical
account with unused allowance after renewal. New work must not charge that account
after its usage has been inherited. This guard applies to new admissions and new
resource reservations only. Reconciliation retains its original reservation and
account IDs, charges previously reserved work consistently, and never launches
replacement work as part of reconciliation. Reading or accepting valid immutable
evidence from an older segment remains allowed and does not require a rerun.

`totals` starts with zero `used_moves`, `used_runs`, `reserved_moves`, and
`reserved_runs`, and `historical_usage: known`. Adoption must mark unknown imported
objective history `unknown`. Under an objective-wide finite cap, unknown history
blocks admission even if an account has a reviewed renewal. It cannot establish
remaining global capacity. Renewals do not reset objective totals.

`control.nonprogress_replans` starts at zero; three rounds without qualifying
progress block further admission with `replan_limit`. Task 3 owns round accounting,
verified-progress resets, and the durable pause. Initially proof is `open`,
execution is `needs_replan`, and publication status is an empty list. Admission
never changes a proof status or creates an acceptance.

## Integration test fixtures

`tests.search_fixtures` supplies fresh records through `contract()`,
`proposal(category="main")`, and `review(subject, reviewer="reviewer-one",
decision="approve")`. `decomposition_proposal()` supplies a root interval split
into two exact finite pieces and an explicit open bridge. The checkpoint and
successor helpers are internal pure-test fixtures only; they are not CLI inputs
and do not bypass the eventual evidence service.

## Checkpoints and accepted results

All events added by Task 3 are internal reducer events. The public service must
construct them after validating actual artifacts and host/operator provenance.
It must never expose arbitrary event injection or accept a public verification
flag in place of those checks. Pure replay checks bindings and logical sufficiency;
it neither authenticates a person nor opens an evidence file.

`CheckpointInput` has exactly:

```text
schema_version: 1
kind: "proof" | "reduction" | "obstruction" | "hypothesis"
claim: Claim
origin: JournalOrigin | ExternalOrigin
evidence_digests: [SHA256]
verification_status: "pending"
what_changed: Text
remaining_obligation_ids: [ObligationID]
next_hypothesis: Text
milestone_id: Text | null
```

Evidence is nonempty except for hypotheses. The reducer adds its stable `id` to
the stored checkpoint; all later bindings use the digest of this complete stored
record, including `id`. The pending status is immutable snapshot metadata.
Acceptance is represented separately and never edits the checkpoint.

```text
JournalOrigin = {
  kind: "journal_move", node_id: NodeID, move: Integer[1,24],
  journal_prefix_digest: SHA256
}
ExternalOrigin = {
  kind: "external_result", source: Text, source_digest: SHA256,
  statement: Text, statement_digest: SHA256(Claim), study_digest: SHA256
}
```

External `statement` equals the claim's statement; its digest covers the entire
claim, including scope and assumptions. It has null `milestone_id` and never
fabricates a journal move. Sources, studies, and evidence are immutable pinned
inputs. Neither origin contains its own review, avoiding a content-digest cycle.

A local origin requires a declared milestone of its admitted producer. For
`accepted_obligation`, `accepted_case_set`, or `verified_reduction`, the checkpoint
claim must equal the named milestone obligation. For `verified_obstruction` or
`hypothesis_recorded`, it must equal the producer's admitted claim. Permitted
checkpoint kinds are respectively proof, proof, reduction, obstruction, and
hypothesis. This permits a reviewed, predeclared intermediate output but forbids
arbitrary cross-node claim capture. The service additionally checks that the
pinned journal prefix really belongs to this producer, the move was properly
reserved and journalled, and the admitted milestone corresponds to the actual
result snapshot. A matching integer move number alone is not that check.

`AcceptanceInput` has exactly:

```text
schema_version: 1
checkpoint_id: CheckpointID
checkpoint_digest: SHA256(StoredCheckpoint)
obligation_id: ObligationID | null
outcome: "proof" | "counterexample" | "reduction" | "obstruction"
classification: "analytical" | "computational"
standard: "reviewed" | "certificate" | "lean-kernel"
dependency_ids: [AcceptanceID]
route_bindings: [RouteBinding]
review: ResultReview
audit: ResultAudit
```

The target claim, quantifiers, scope, proof policy, and assumption context must
match the checkpoint structurally. Implications require their own exact target
and bridge. Null target is permitted only for an admitted standalone local
producer with an exact matching claim. Hypotheses and no-hit outcomes cannot be
accepted. A proof checkpoint permits proof or counterexample; obstruction permits
obstruction. A reduction checkpoint permits reduction (nonclosing progress), or
proof when it proves the exact targeted reduction/implication under all normal
proof checks. Only `outcome: proof` can supply a premise or bridge. A reduction
label by itself does not imply its conclusion.

```text
ResultReview = {
  subject_digest: SHA256(StoredCheckpoint), claim_digest: SHA256(Claim),
  reviewer: Provenance, decision: "approve",
  findings: {statement: Text, assumptions: Text, scope: Text,
             dependencies: Text, policy: Text}
}
ResultAudit = {
  subject_digest: SHA256(StoredCheckpoint), dependency_ids: [AcceptanceID],
  evidence_digests: [SHA256], provenance: Provenance
}
RouteBinding = {
  route_id: RouteID, route_digest: SHA256(CurrentRoute),
  case_obligation_ids: [ObligationID], shared_prerequisite_ids: [ObligationID],
  discharged_assumption_ids: [ObligationID]
}
```

Review is distinct from proposal review and binds the full stored checkpoint
and exact claim. A local producer cannot supply its own result review. The audit
lists must exactly match the acceptance dependencies and checkpoint evidence.
The service supplies an audit only after checking freshness of the complete
transitive immutable dependency closure, manifest completeness, actual proof
policy checks, and the pinned review. It classifies computational claims from
their actual evidence; callers cannot relabel a computation as analytical.

Analytical reviewed bridges can satisfy certificate policy. Every computational
claim in their dependency closure must meet certificate policy. Lean-kernel
requirements apply to all contributing claims and bridges, including dependencies
whose local claim requested a weaker policy. Local stronger requirements also
remain binding when a parent requests a weaker policy. The service must check
the real checker/completeness review or requested Lean declaration, build,
inspection and permitted axioms before emitting the corresponding standard.

Dependencies must already be accepted proofs, must remain usable, and cannot
refer back to the same checkpoint or drop extra assumptions. Graph propagation
uses all premises AND an explicitly accepted bridge; alternative routes are OR.
A route binding must be supplied by the exact bridge's proof acceptance and
must pin that route. The stored acceptance adds `route_content_digests`, which
hash each bound route excluding mutable status and alternative order. Reordering
work does not change an accepted theorem.

Case and common-prerequisite lists are disjoint subsets of the route premises.
When cases are listed, together they must name every premise. Every admitted
coverage node must be explicitly listed as a case by its bridge. The service's
independent scope review must distinguish whole cases from scalar components;
the controller does not infer that distinction from prose. Each case scope must
be inside the finite parent scope. Interval unions use endpoint-aware interval
operations, not integer enumeration, and overlap is counted once. Missing cases,
an unproved common prerequisite, a dropped assumption, or an insufficient bridge
policy prevents the affected coverage. Named infinite domains have no finite
fraction and require their quantified bridge or tail obligations.

Discharged assumptions are explicit route premises with proofs under the parent
assumption context. An assumption cannot discharge itself. More complicated
conditional chains must expose the necessary intermediate obligations. A
conditional theorem proves its stated implication, not its consequent without
the hypothesis. Direct proofs of the exact root need no artificial bridge.

Stored acceptances add `id`, `status: accepted | invalidated`,
`route_content_digests: {RouteID: SHA256}`, `progress_key: SHA256`, and
`progress_eligible: Boolean`. The latter fields are derived, never caller inputs.
Progress identity normalizes the exact claim and checkpoint kind; acceptance
additionally distinguishes proof from nonclosing reduction. A previously accepted
mathematical result cannot regain progress credit by changing its checkpoint ID,
origin bookkeeping, next hypothesis, evidence bytes, or run-manifest metadata.
Historical invalidation does not grant a fresh research allowance either.
New accepted claims/reductions/obstructions can qualify. Semantic duplication
still requires the admission/result review boundary.

`qualifying_progress` consumes the full checkpoint digest and accepted status as
before, and now also requires derived `progress_eligible` for Task 3 records.
Historical minimal Task 2 fixture records retain compatibility. Renewal identity
for complete checkpoints uses normalized claim plus kind, preventing copy-based
renewal even if a new acceptance is requested. Replan progress keys are consumed
at most once. Valid old proofs remain reusable even when they confer no new budget.

Pure proof functions are:

```text
acceptance_closure(state, acceptance_id, policy) -> set[AcceptanceID] | None
obligation_support(state, obligation_id, policy=None) -> set[AcceptanceID] | None
root_support(state) -> {outcome: "proof" | "counterexample", acceptance_ids: [ID]} | None
coverage(state, route_id) -> {accepted: Integer, total: Integer, remaining: List}
checkpoint_milestone(state, checkpoint_id) -> Boolean
```

Remaining integer coverage is a list of inclusive `[lower, upper]` pairs; explicit
cases use a list of remaining case IDs. This is an exact case count, not overall
proof completeness. Milestones evaluate their named typed predicate and require
matching usable acceptance, except `hypothesis_recorded`, which only records a
hypothesis and grants no proof or renewal credit. Verified reduction recognizes
both a nonclosing reduction and a proved reduction. Verified obstruction names
the producer's declared route and never disproves the objective by implication.

Invalidation preserves checkpoints and acceptance history, invalidates transitive
acceptance dependencies, and recomputes obligation status. If it affects the stored
final closure, proof becomes `invalidated` and execution reopens unless paused.
Changing a working copy is not an invalidation input unless that copy is itself
the referenced immutable evidence. The service must distinguish these cases.

## Final closure and local delivery

Root support only requests finalization. `objective_completed` alone changes
proof status to proved/disproved. A counterexample must target the original claim;
disproof of a premise never propagates as root disproof. A proof-only contract
with accepted root counterexample requires the host to report that conflict and
request user direction, while accurately retaining outcome `counterexample` or
proof status `disproved`.

`Closure` is `{subject: ClosureSubject, review: ResultReview, audit: ClosureAudit}`.
Here ResultReview's subject digest covers `ClosureSubject`, and its claim digest
is the original claim digest. Its reviewer is independent of every local producer.

```text
ClosureSubject = {
  schema_version: 1, objective_id: ID, contract_digest: SHA256,
  outcome: "proof" | "counterexample", acceptance_ids: [AcceptanceID],
  evidence_digests: [SHA256], local_deliveries: [LocalDelivery],
  deliverables: [{requirement: Text, digest: SHA256}]
}
ClosureAudit = {
  subject_digest: SHA256(ClosureSubject), acceptance_ids: [AcceptanceID],
  evidence_digests: [SHA256], provenance: Provenance
}
LocalDelivery = {
  node_id: NodeID, inventory_digest: SHA256, checked_unit_digests: [SHA256],
  consolidation_digest: SHA256, draft_digests: [SHA256],
  evaluation_digests: [SHA256], handoff_digest: SHA256,
  finish: {kind: "finished" | "local_finish_pending_unused_children",
           unused_child_ids: [NodeID]}
}
```

Closure binds the deterministic complete root-support set and the exact union
of its evidence digests. Every local producer in that transitive support needs
one delivery; unrelated nodes cannot be included. Required deliverables match
the frozen contract exactly. Pending moves/runs, pause, unresolved state errors,
and ambiguous focus prevent completion.

The service validates the actual stage 7/8 triggers, inventory, applicable checked
units, consolidation, drafts, evaluations and handoff, including applicability of
empty artifact lists. It checks every referenced immutable artifact in the subject
and its compatibility with the accepted versions before creating ClosureAudit.
These records are service facts, not caller-supplied declarations that checks passed.
For `finished`, the service verifies the real finish result. For the pending finish
exception, it verifies that all contributing artifacts are checked and unused
native children are the sole remaining local finish refusal. The reducer requires
exactly the actual unfinished native child IDs and excludes contributing children.
It does not mark any child or parent finished. Other local gate failures cannot use
this exception. Root completion remains independent of unused alternative research.

## Node facts, retreat and replanning

`NodeFacts` are internal observations from journal/stage reconciliation:

```text
node_id: NodeID
status: "admitted" | "active" | "waiting" | "result_ready" | "retreated" | "finished"
result_action: "verification" | "snapshot" | "acceptance" | null
cashout_action: "inventory" | "unit_checks" | "consolidation" | "draft" |
                "evaluation" | "finish" | "handoff" | null
waiting_on: [ObligationID]
failed_strategies: [StudiedMethod]
stagnation_moves: NonnegativeInteger
external_block: Text | null
dependency_route_ids: [RouteID]
dependency_assumption_ids: [ID]
```

Recording facts updates local lifecycle only, never theorem truth or an already
resolved objective's execution status. A retreated node can retain `retreated`
while reporting cash-out progress and then become `finished`; it cannot return to
research states. A finished node remains finished. NodeFacts cannot initiate
retreat: that requires `node_retreated` and its declared predicate. Ordinary active
observations from unused investigations preserve completed root delivery. The
service determines truthful stage
and result actions from the native records, and counts stagnation from the declared
test, not arbitrary model assertions. It retains recorded dependency routes and
assumptions so retreat can suspend dependent descendants. The scheduler also
requires the premises and bridge of a consumer's explicitly selected route;
unproved execution prerequisites are allowed only when stated in its conditional
claim's assumption context.

```text
Retreat = {
  node_id: NodeID, criterion: RetreatCriterion, failed_hypothesis: Text,
  observation: Text, last_checkpoint_id: CheckpointID | null,
  remaining_assumption_ids: [ID], reconsideration: Text,
  abandoned_route_ids: [RouteID], abandoned_assumption_ids: [ID]
}
RouteOrder = {route_id: RouteID, alternative_order: [NodeID], selected: Boolean}
ObligationOrder = {obligation_id: ObligationID, alternative_order: [NodeID]}
```

Retreat requires a declared predicate that has fired: account move/run limits,
observed studied-strategy failure, declared stagnation, or a usable counterexample
to a method prerequisite. It records the reason and saved checkpoint, marks the
node retreated, suspends logically descending investigations that depend on the
abandoned routes/assumptions, and preserves independent accepted facts. It retains
all accounts and historical records. Selection then tries admitted alternatives
at the nearest ancestor before returning to root traversal.

Replan preserves accounts and changes only committed alternative/selected-route
order, explicit research deferrals and round accounting. Its optional `obligation_orders` list orders direct
alternatives even when no route has their obligation as its conclusion. Each
entry names an existing obligation and distinct already-admitted, non-standalone
nodes targeting that exact obligation. Unknown references, duplicate obligation
entries and extra fields are rejected. An empty `alternative_order` clears that
obligation's explicit preference; an omitted `obligation_orders` field preserves
existing preferences. These preferences precede per-route alternative order and
stable creation order during traversal. They never bypass readiness, active-work
priority, pending execution, review/closure priority, pause, budget, studied-method
admission or native entry validation. No failure, retreat, route or proof fact is
created. The existing event log derives the preference; no stored history is
rewritten. Older records replay with no direct preference. Readers lacking this
extension reject new records containing the field rather than silently selecting
a different frontier, so retain a compatible installed reader once it is used.

The optional `deferred_node_ids: [NodeID]` field supplies the complete desired
set excluded from new research selection. Omission retains the previous set;
`[]` clears it and restores ordinary frontier selection. IDs must be distinct,
known, reviewed main or coverage investigations with status exactly `admitted`.
An active node, a node with any historical move or run, or a node with observed
result or cash-out work cannot be deferred. Any pending move or reserved,
launched or indeterminate run prevents setting or clearing this field.

The filesystem service holds the existing transaction and per-node journal
ownership through validation and commit. It checks every requested node's native
journal is present and empty, no native finish exists, and current `observe_node`
facts contain no result or cash-out action. Nonempty or inconsistent records are
refused without rewriting them. Existing native-intent and filesystem-effect
recovery guards still apply. Caller-supplied NodeFacts cannot authorize deferral;
the reducer independently revalidates state-level eligibility on replay.

Deferral is not a lifecycle transition, failure, retreat, proof acceptance or
route abandonment. It preserves claims, residual obligations, evidence, retreat
history, accounts, usage and resource limits. It neither suspends descendants nor
adds execution authority. Pause, focus, prerequisite, budget, result and root
closure priorities remain in force; normal frontier, studied-method and native
entry checks still govern `begin`. It earns no progress credit or refund.
Invalid or stale requests and conflicting request IDs commit no event prefix;
replaying the same request consumes one event and one round in total.

Generated SEARCH_TREE.md and per-node LINEAGE.md display deferral alongside the
unchanged lifecycle, claim and residual information. Old events replay with an
empty deferral set. Readers without this extension reject new events containing
the field, so retain a compatible patched reader across plugin upgrades once
deferral has been used.

No usable new progress increments the objective round
count; three such rounds durably pause. New usable accepted progress resets the
round counter only once per mathematical progress identity. New checkpoint IDs,
review prose, hypothesis records, and rerun bytes do not reset it. Only explicit
verified resume can clear an existing pause.

## Control and closed scheduler actions

`ControlInput` has one of these exact shapes:

```text
{action: "pause", reason: Text}
{action: "resume", objective_id: ID, message_id: Text, session_id: Text,
 instruction: Text, provenance: Provenance}
{action: "focus", focus: "focused" | "ambiguous" | "unrelated", provenance: Provenance}
{action: "hook_stop", delivery_id: Text | null, session_id: Text | null,
 turn_id: Text | null, stop_hook_active: Boolean | null}
{action: "side_interval", node_id: NodeID, max_moves: Integer[1,24],
 message_id: Text, provenance: Provenance}
```

The host/operator must authenticate explicit user instruction for resume and side
allocation; model-authored provenance is rejected. Resume binds this objective
and a previously unused message/session identity. It rearms objective continuation
and controller replanning, never local accounts. Old resume instructions cannot
rearm later pauses. Side intervals stay within the admitted node's current account
and remaining move allowance, and are measured from its cumulative used moves.
Main work keeps priority even during a side interval. Standalone nodes can also
run when admitted main work is externally blocked; they confer no root coverage.

Stop accounting is objective-wide, capped at 40 continuations. Repeated delivery
IDs do not consume another count. For a previously continuing delivery, the returned
action is derived from the current frontier, including newly accepted root evidence,
pending reconciliation, or changed prerequisites; the historical action is never
reissued as authorization. A previously stopping delivery grants no free continuation.
Missing IDs conservatively count each invocation.
The 40th request durably pauses and issues one `summary_then_stop`. Later calls,
including replay of a formerly continuing delivery after pause, permit stopping.
Neither missing metadata nor `stop_hook_active: false` creates new authorization.
Pause, unrelated/ambiguous focus, resolution and genuine blockers permit handoff.
Waiting on a live or unresolved owned workload (`execution_pending`) also permits
stopping. It does not request another research move, spend a Stop count, or rearm
the allowance. Pending native effects require reconciliation before local files
can count as successful observations.
Delivery replay is objective-local; the host must normalize delivery identity
without reusing one identifier for distinct observed deliveries.

`next_action(state)` returns exactly one of these closed shapes and performs no I/O:

```text
{kind: "paused", reason: Text}
{kind: "blocked", reason: Text}
{kind: "handoff", reason: Text}
{kind: "resolved", proof_status: "proved" | "disproved"}
{kind: "execution_pending", run_id: RunID, status: "launched" | "indeterminate"}
{kind: "reconcile_move", move_id: ID}
{kind: "reconcile_run", run_id: RunID}
{kind: "prepare_result", node_id: NodeID, step: "verification" | "snapshot" | "acceptance"}
{kind: "finalize_root", outcome: "proof" | "counterexample", acceptance_ids: [AcceptanceID]}
{kind: "local_cashout", node_id: NodeID, step: CashoutAction}
{kind: "execute_node", node_id: NodeID}
{kind: "execute_node", node_id: NodeID, strategy: Text}
{kind: "reassess_strategies", context_digest: Digest, previous_assessment_id: ID|null}
{kind: "retreat", node_id: NodeID, criterion: RetreatCriterion}
{kind: "replan", round: PositiveInteger, obligation_ids: [ObligationID]}
```

Priority is pause/state errors/focus/live execution, pending reconciliation,
produced-result verification/snapshot/acceptance, root finalization, due local
cash-out, required strategy reassessment and reviewed strategy selection, active
main work and its continuation when no assessment exists, waiting prerequisites, nearest
ancestor alternative, root-route traversal, and bounded replanning. A successful
B can select ready C without any active parent. Local cash-out remains possible
after research exhaustion. New verification requires a current account and
remaining moves/runs; accepting historical immutable evidence does not require
a current account or a rerun. Stable creation IDs break otherwise equal choices.

`control.stop_decision` is one of:

```text
{kind: "allow_stop"}
{kind: "summary_then_stop", reason: Text}
{kind: "continue", action: NextAction}
```

Derived control fields are `nonprogress_replans`, `progress_fingerprints`,
`stop_count`, `stop_deliveries` (delivery ID to original accounting decision), `summary_issued`,
`stop_decision`, `pause_reason`, `focus`, `focus_record`, `state_error`, `active_node_id`,
`retreat_node_id`, `pending_moves`, `node_facts`, `selected_routes`, `obligation_orders`, `deferred_node_ids`, `closure`,
`main_external_block`, `side_interval`, `resume_record`, `resume_ids`,
`side_instruction_ids`, `retreats`, and `strategy_refresh`. All live in the single replayed state.
`strategy_refresh` contains policy activation, immutable assessment history,
retained failures and event ordinals for planning evidence.
`obligation_orders` maps obligation IDs to their committed node-order lists and
starts empty. `deferred_node_ids` is an initially empty list of investigations
excluded by the ready predicate; only an explicit replan field replaces it.
Node-fact entries add derived `suspended: Boolean`; retreat may create an entry
containing only that suspension field before service facts arrive. Initial nullable
references are null, collections empty, focus focused, counters zero, and initial
Stop decision allow_stop. Side intervals store `node_id`, `account_id`,
`start_used_moves`, and `max_moves`. Pending move identities and unresolved
execution errors are reserved integration fields for the subsequent execution
reducer; they are not public flags and Task 3 does not invent executor events.

## Filesystem service and CLI

`Controller(root: Path, strategies_dir=None)` takes the attack root. Its storage
is `Store(root / ".search")`. The service exports
`command(name, spec, expected_revision, request_id, target=None, *, workspace_root=None, hook_session=None)`,
`status(full_audit=False)`, and `guard_legacy(command, slug, details)`.
`cli.install_parser(commands, strategies_default)` installs the nested `search`
parser while retaining global `--attack-root` and `--strategies` placement.

Public mutations require `--expected-revision N --request-id ID`. Specs are read
with `--spec FILE`. `--json` and human-readable output contain the same JSON
decision; human output is indented. Errors are JSON on stderr:
`{"error":{"code":Text,"message":Text,"details":JSON|null}}`, with exit 1.
Unknown commands, unknown fields, unsafe paths, stale revisions and conflicting
request IDs fail without committing a prefix. There is no arbitrary-event command.
`begin` reserves a legal native entry, `run` executes a bounded admitted workload,
and `reconcile` recovers original recorded effects without rerunning a mathematical job.

Public command specs are closed records:

| Command | Spec |
| --- | --- |
| init | `{contract: Contract}` |
| propose | `{proposal: Proposal, inputs: [Input]}` |
| review | `{proposal_id: ID, review: Review, inputs: [Input]}` |
| admit ID | `{}`; `--spec` may be omitted |
| begin ID | The `begin NODE --spec FILE` record under [Metered execution and native integration](#metered-execution-and-native-integration) |
| run ID | The tagged `run NODE --spec FILE` record under [Metered execution and native integration](#metered-execution-and-native-integration) |
| reconcile | `{}` normally; optional `--spec` for the exact [rank operator recovery](harness/RANK_RECOVERY.md) record |
| amend-computation ID | The amendment record under [Computation admission and mandatory interpretation](#computation-admission-and-mandatory-interpretation) |
| interpret ID | The interpretation record under [Computation admission and mandatory interpretation](#computation-admission-and-mandatory-interpretation) |
| checkpoint [ID] | `{checkpoint: PublicCheckpoint, inputs: [Input]}` |
| accept ID | `{obligation_id: ID|null, outcome: Outcome, dependency_ids: [ID], route_bindings: [RouteBinding], review: ResultReview, inputs: [Input]}` |
| complete | `{subject: ClosureSubject, review: ResultReview, inputs: [Input]}` |
| adopt | `{mappings: [ImportMapping], inputs: [Input]}` |
| retreat ID | `Retreat` without `node_id` |
| replan | The documented `replan_recorded` payload |
| reassess | The reviewed record under [Mandatory strategy reassessment](#mandatory-strategy-reassessment) |
| focus | `{focus: "focused"|"ambiguous"|"unrelated", provenance: Provenance, session_id: Text}` |
| pause, resume, hook-stop | The respective `ControlInput` without `action` |
| audit, render, status, next, strategy-context | `{}`; no spec file |

`status`, `next`, and `strategy-context` are read-only and need no revision or request ID.
Every other public command uses the current `--expected-revision` and a unique
`--request-id`. Commands whose table row defines a nonempty spec require
`--spec FILE`; `admit` accepts an optional empty spec and `reconcile` an optional
operator-recovery spec. `audit` and `render` have no `--spec` option. Place
`--attack-root` before `search`. For
example:

```sh
exactory-math --attack-root attack search propose --spec proposal.json \
  --expected-revision 1 --request-id propose-root --json
exactory-math --attack-root attack search admit proposal-000001 \
  --expected-revision 3 --request-id admit-root --json
exactory-math --attack-root attack search status --json
exactory-math --attack-root attack search next --json
```

The proposal's normal limits are explicit values, not omitted defaults:
`{"max_moves":24,"max_runs":24,"timeout_seconds":300,"workers":1}`.
`PublicCheckpoint` is `CheckpointInput` without `verification_status`; the service
sets `pending`. Local checkpoints require their positional node ID and an
acknowledged reserved journal receipt. External checkpoints have no positional
node and consume no journal move. Neither kind accepts evidence automatically.

`Input = {path: RelativePath, digest: SHA256, kind: "artifact"|"blob"}`.
Paths resolve within the attack root and cannot traverse or escape through
symlinks. Artifact digests hash raw bytes. Blob digests hash strict canonical JSON.
Duplicate JSON keys and non-finite JSON are rejected. Inputs are copied into
immutable stores before the event document commits. Study/source digests in
proposals identify nonempty raw artifacts; inherited evidence identifies manifest
blobs. Reviews are separately stored blobs and never occur inside the subject
whose digest they review. Host/operator provenance is an explicit trust boundary;
the CLI cannot establish intellectual independence cryptographically.

Mutation receipts contain `objective_id`, `revision`, `proof_status`, and `command`.
They describe the original committed mutation on identical request replay.
`hook-stop` additionally returns `decision` and `current_revision`: accounting is
deduplicated but a former continuation is evaluated against current control. An
old execution decision cannot override a later pause or root-ready frontier. The
continuation-limit summary is issued once; replay permits stopping.

Bounded status includes `freshness: {status:"unchecked", failures:{}}`; recorded
acceptance is not a claim that artifact hashes were freshly checked. Full status
audit performs no computation and returns effective failed freshness without
writing history. `search audit` commits discovered invalidations. Acceptance and
completion always inspect the full accepted dependency closure. A failed final
delivery audit conservatively invalidates its contributing acceptance support and
the recorded closure, retaining all history. None of these audits runs a compiler,
checker, Lean, shell script or mathematical job.

### Workspace discovery and session ownership

`init`, `focus`, and `resume` accept optional `--workspace-root DIRECTORY`. The
default is the canonical attack root's direct parent, for both CLI and service
calls. An explicit directory authorizes writes only there. Registration never
chooses a destination by searching ancestors, and symlinked registration paths
are rejected. The pointer file is `<workspace>/.exactory/math-search.json`:

```text
{schema_version: 1,
 roots: [{path: AbsolutePath, objective_id: ID, contract_digest: SHA256}],
 sessions: {HostSession: {generation: SHA256,
   target: {root: RootIdentity, focus_request_id: Text} | null}}}
```

Host sessions are `codex:<session-id>` or `claude:<session-id>`. Cleared targets
retain a generation tombstone. The registry contains no claims, nodes, budgets,
pause flags, or scheduling decisions. Initialization registers identity but no
session. Authoritative ownership lives in `control.focus_record`, initially null,
then `{session_id, request_id}`. Only that session with a matching published pointer
may request automatic continuation, including public `hook-stop` calls. Other
sessions can inspect the objective and perform ordinary validated collaborative
CLI work. Focus does not select a mathematical branch or renew any allowance.
Explicit authorized resume updates the owner using the existing resume checks.

Each registration command commits a closed typed `discovery_recorded` operation
alongside its authoritative change. `service.discovery_intents` retains canonical
routing inputs, root identity, command digest, request/session identities, exact
expected prior session entry (including absence or tombstone), and desired entry.
Changing `--workspace-root` under the same request ID is `request_id_conflict`.
Replays reuse the immutable original intent, not a new reading of the pointer.
New requests briefly read the expected pointer under the registry lock and release
it before taking the controller lock. Publication holds the controller lock, then
the single registry lock, validates the current owner, and compare-and-sets the
expected entry or accepts the already-equal desired entry. It never takes two
controller locks together and preserves unrelated registrations. Atomic publication
uses file and directory fsync. These intents are separate from pending native
initialization effects, so a failed publication does not prevent fresh focus.

A committed transaction whose pointer could not publish returns
`discovery_unpublished` with its committed revision and request ID. If a later
focus supersedes it, replay cannot replace the later pointer, even across
objectives. Recover stale publication with a fresh explicit focus command; an old
replay is not guaranteed to repair every registration failure.

### Hook lifecycle and supported host boundary

The shared math hook adapter discovers exact registered roots and `.search/tree.json`
markers through ancestors of event cwd, effective shell workdir, and explicit
targets. It does not recursively scan or choose an alphabetically first root.
Unrelated ancestor registrations do not make unrelated paths managed. A root
outside its registration workspace can be found from its own node cwd; the
controller still verifies its owner against the original workspace pointer.
Missing, cleared, or conflicting bindings request explicit handoff.

SessionStart/Resume makes one bounded read-only `search next --json` call with
optional `--session-id`, `--focus-request-id`, `--objective-id`, and
`--contract-digest` validation inputs, also accepted by `status`. It does not
refresh facts, repair pointers, or resume execution. Pause therefore survives
status-only questions, retrospective requests, compaction, and session resume
without transcript interpretation. Hooks never infer user authorization.

Stop makes one bounded `search hook-stop --internal` call. Only this internal CLI
route may omit the expected revision; it reads the revision and invokes the
validated service in that process. A fresh transaction request ID is separate
from the optional host delivery ID. Concurrent revision conflicts return recovery
guidance without unlimited retry. Before control accounting, the service derives
changed local observations through `integration.observe_node`, replays them into
the prospective state, then derives the decision. Unchanged observations do not
add operations. Native and journal intents retain recovery precedence, and the
256-operation bound is enforced with an explicit recovery error. Advisory activity
logs cannot grant proof credit. Newly managed-node shell activity records only a
relative target or native command name and slug, not raw shell arguments; existing
unmanaged advisory formatting and authoritative controlled-run argv are unchanged.
The 40th continuation requests one summary; later
Stop calls permit stopping until explicit operator resume, even with absent or
false `stop_hook_active` and after an admitted alternative or replan.

Reported direct `Bash` and `exec_command` (`command` or `cmd`, with `workdir`)
operations and the documented `export PATH=...:$PATH` bootstrap are normalized.
Supported patch add/update/delete/move operations protect `.search` snapshots and
artifacts, generated `SEARCH_TREE.md`/`LINEAGE.md`, discovery pointers, and native
records under custom roots. Combined unit/draft sequencing remains enforced.
Read-only file references and command-like prose are not write authorization.
Recognized managed mutations with corrupt state or unsupported visible shell or
wrapper payloads fail closed with direct-call recovery guidance. The adapter does
not parse arbitrary shell programs or JavaScript. Nested operations that the host
never reports remain an observability boundary, not an enforced sandbox. Tests use
synthetic host payloads; they do not establish a live host smoke result.

Ordinary and adoption admission audit their checkpoint anchors, inherited manifest
closures, accepted renewal bases and transitive acceptance dependencies inside
the locked command transaction. Unaccepted inputs receive an integrity audit;
first verification does not require its own future terminal run. Accepted support
receives the full verification/policy audit before it can justify an allowance.
A failed audit commits no event, account change or workspace effect.
This includes an existing current segment's implicit `renewal_basis` when a
`new` or `inherit` proposal reuses it. Auditing and admission share the pure
`resolve_proposal_account(state, proposal, target)` selector; it does not allocate
or mutate an account. Automatic reuse resolves the current segment, while an
explicit superseded account remains an `account_superseded` refusal. Proposal
context and references are validated before auditing, so an unknown checkpoint
anchor returns structured `dangling_reference` without a transaction prefix.

Local publication packages must contain the exact accepted versions of all local
artifacts in the complete manifest dependency closure, including `source` and
`input` roles. Two required digests for the same local path cause
`conflicting_evidence_versions`; an omitted or replaced required artifact causes
`digest_mismatch`. A refreshed package cannot replace the accepted closure.

## Immutable evidence manifests

Every checkpoint evidence digest names this closed blob:

```text
EvidenceManifest = {
  schema_version: 1, kind: "analytical"|"certificate"|"lean",
  claim_digest: SHA256(Claim),
  conclusion: {outcome: "proof"|"counterexample"|"reduction"|"obstruction"|"hypothesis",
               dependency_ids: [AcceptanceID], route_bindings: [RouteBinding]},
  artifacts: [{path: RelativePath, digest: SHA256(RawBytes),
               role: "proof"|"source"|"study"|"certificate"|"checker"|
                     "theorem"|"toolchain"|"input"}],
  dependencies: [EvidenceManifestDigest],
  external_dependencies: [{path: AbsolutePath, digest: SHA256(RawBytes)}],
  verification: Verification | null
}
```

The conclusion pins the interpretation before the result review. Acceptance must
match its outcome, dependency IDs and route bindings exactly. A positive proof
cannot become a counterexample by changing the acceptance command. Classification
and standard derive from the inspected manifest closure. Analytical evidence needs
a proof artifact and cannot contain checker/certificate/theorem/toolchain roles
or a computational dependency. Reviews assess the mathematical meaning of the
declared analytical proof; this is not an automatic natural-language proof checker.
Certificate evidence needs both certificate and checker artifacts. Lean evidence
needs theorem and toolchain artifacts. Missing dependency bytes, cycles, mismatched
claims and insufficient transitive policies fail closed.

Relative artifact paths describe the frozen version. Editing a later working copy
does not edit accepted evidence. Explicit absolute external dependencies are
read-only inputs whose current bytes are checked on every full audit. They are
never write targets. Source and study artifacts of external origins are also pinned
and audited. Capturing a directory is not evidence that the manifest enumerates
every mathematical dependency; the independent review and controlled verifier
must establish that completeness.

Task 5 must produce and bind these computational records:

```text
Verification = {
  run_id: RunID, result_digest: SHA256(TerminalVerificationResult),
  policy_review: ResultReview,
  requested_declaration: Text|null, requested_type_digest: SHA256|null
}
TerminalVerificationResult = {
  schema_version: 1, run_id: RunID, claim_digest: SHA256(Claim),
  input_digest: SHA256(FrozenVerificationInputs),
  commands: [{argv: [Text], exit_code: Integer,
              stdout_digest: SHA256(RawBytes), stderr_digest: SHA256(RawBytes)}],
  declaration: Text|null, theorem_type_digest: SHA256|null,
  toolchain_digest: SHA256|null
}
FrozenVerificationInputs = {
  schema_version: 1, claim_digest: SHA256(Claim),
  artifacts: EvidenceManifest.artifacts,
  external_dependencies: EvidenceManifest.external_dependencies
}
```

The run must exist in authoritative `state.runs`, have `status: terminal`, and
bind exactly `result_digest` and `input_digest`. Importing a result JSON file
cannot create that run. The policy review pins the terminal result and exact
claim; its findings cover checker correctness/completeness or formal statement,
requested declaration/type and permitted axioms as appropriate.
Certificate results have one successful command and null Lean fields. Lean
results have successful build and inspection commands, the exact requested
declaration/type, and pinned toolchain/type artifacts. Inspection output must
identify the requested declaration exactly once and use only the existing
standard axiom allowlist. Plausible output with nonzero exit status is rejected.
Task 5 owns actual command selection, process ownership, input capture, compiler
retry charging and terminal-result persistence. Task 4 consumes only their audited
records and cannot mint a controlled run.

Task 5 also owns `service.journal_receipts`, keyed by `"NodeID:MoveNumber"`:

```text
{node_id: NodeID, move: Integer[1,24], reservation_id: ID,
 journal_prefix_digest: SHA256(RawJournalPrefix), problem_digest: SHA256}
```

Only acknowledged, immutable reservations may create receipts. The complete raw
prefix, including the harness-derived last move's problem digest, must match the
actual journal bytes. Appending later moves preserves earlier receipts. Changed
prefixes fail audit. No public spec can inject receipts, and adoption does not
synthesize them from old line counts. Existing pass rules remain 8 moves per pass,
3 passes and the 24-move hard cap; default templates remain 24 runs, 300 seconds,
and one worker.

## Exact local delivery packages

Each `LocalDelivery.checked_unit_digests` entry names:

```text
{schema_version:1, node_id:NodeID, unit_number:PositiveInteger,
 files:[{path:RelativePathWithinNode, digest:SHA256(RawBytes)}]}
```

Packages are ordered by the native unit numbers and cover every applicable unit.
Each contains its exact `unit.json`, check stamp, draft, evaluation and referenced
proof evidence inputs. Auditing re-runs native read-only unit/form/finish validators
and compares actual bytes, not merely the `unit.json` stamp. Inventory,
consolidation and handoff digests bind `units/INVENTORY.md`,
`units/consolidation.md`, and `HANDOFF.md`. Cash-out must have its actual native
stage-seven trigger; empty unit lists are valid only when there are no units.
The separate draft/evaluation lists equal the package versions exactly.

`finished` requires the actual matching native FINISHED record and no unfinished
native children. The pending-unused-child exception requires every other artifact
and gate to pass first, the exact unfinished mapped native child IDs, and no
contributing child. It writes no fabricated FINISHED file. A missing evaluation or
changed proof input cannot be hidden by that exception. Final statement review
pins the complete ClosureSubject and all these exact package/deliverable digests.

## Explicit adoption and preserved history

```text
ImportMapping = {
  attack_slug: Slug, claim: Claim, target_obligation: ID|null,
  logical_predecessor: NodeID|null, snapshot_digest: SHA256(LegacySnapshot),
  snapshot_paths: [RelativePathWithinNode], usage: {moves: Integer|null, runs: Integer|null},
  review: ResultReview, verification: AdoptionVerification|null
}
LegacySnapshot = {
  files:[{path:RelativePathWithinNode,digest:SHA256(RawBytes)}], omitted_paths:[RelativePath]
}
AdoptionVerification = {proposal: Proposal, review: Review, allowance_review: ResultReview}
```

The import review subject is ImportMapping without `review` and `verification`.
The snapshot boundary explicitly lists every relevant visible research file.
Automatic traversal excludes hidden files/trees and `build`, `dist`,
`node_modules`, `__pycache__`; exclusions are visible in `omitted_paths`. Required
proof sources in an omitted tree must still be explicitly frozen or declared as
read-only proof dependencies before acceptance. Adoption does not claim a complete
proof closure from this snapshot alone. Problem, journal, parent, FINISHED, units,
and manual lineage bytes remain unchanged. Native parents must be mapped first;
depth-one native links remain distinct from deeper logical predecessors.

Historical nodes have `proposal_id:null`, no fabricated admission/studies, and
status `imported` when unfinished or `finished` when an actual historical FINISHED
exists. Both are read-only imports. They cannot produce new local checkpoints,
retreats or lifecycle facts. Historical local completion grants no proof acceptance.
The generated tree and lineage show the original objective and residual obligations.
Original manual views are content-addressed before replacement.

Usage fields are reviewed cumulative lineage observations. Unknown history stays
`historical_usage:unknown`, with null historical counters. Observable journal
counts are retained as lower bounds. Copies of the same normalized exact claim
share the historical account; increasing known observations can only increase its
usage. No source alias or changed snapshot creates a fresh verification account.
Unknown objective usage refuses a new allowance under a finite global cap.

The first controlled verification has a separately reviewed allowance subject:

```text
{import_id:ImportID, snapshot_digest:LegacySnapshotDigest, claim_digest:SHA256(Claim),
 proposal_digest:SHA256(Proposal), limits:Limits}
```

Its verification-only account has immutable
`adoption_basis:{import_id,snapshot_digest,review_digest,claim_digest}`,
`role:verification`, and `adoption_proposal_digests`. The underlying ordinary
proposal/review and independent allowance review are pinned. The basis permits
the explicitly approved first verification despite unknown prior history; it
does not make that history known. An arbitrary adoption_basis on a research
account cannot unblock it. Revalidation and reviewed changed inputs use the same
current account and remaining allowance, never fresh counters.

If an equivalent managed verification already belongs to another account lineage,
adoption with requested verification atomically refuses with
`managed_verification_amendment_required`. Error details contain `node_id` and
the complete `current_account`, resolving superseded account IDs to the current
segment. Use an ordinary reviewed verification amendment on that existing account,
subject to its remaining allowance and history checks. Adoption does not silently
merge the lineages, clear unknown history, or create another first-verification
allowance. The refused command commits neither its import nor its allowance.

`service.imports` retains original records. `service.import_versions` appends
reviewed same-path amendments, preserving exact claim, target, native/logical
lineage and original history. Shortening/changing the preserved journal, editing
native parent or historical FINISHED bytes, or reducing recorded usage is rejected.
`service.adoption_allowances` pins each approved snapshot version explicitly.
An unchanged replay reuses the node. A genuinely new input version cannot reopen
a finished verification node or overwrite its admission; it needs another
ordinarily reviewed verification node on the same account. Exhaustion remains
exhaustion and must be resolved through a bounded, explicitly authorized renewal.

## Locked command construction and recoverable projections

`Store.append_operation(identity, expected_revision, request_id, build, validate=None)`
is the internal service transaction API. Identity is exactly
`{command,target,spec_digest}`. Identical replay and conflicting-ID checks precede
the builder; stale new requests never call it. `build(isolated_document, content_sink)`
returns `{command,target,spec_digest,operations,effects}`. The sink stages immutable
bytes in memory and supplies `put/get_blob` and `put/get_artifact`; it does not
reacquire the writer lock. After pure validation succeeds, the same locked writer
persists staged content and atomically replaces the event document. Callback
mutation cannot change authoritative input or the candidate persisted by storage.

The outer event remains exactly `{sequence,request_id,kind,payload}`, with kind
`service_operation`. Its bounded list contains only typed `{kind,payload}` internal
operations permitted for that command. Nested batches and unknown operations are
rejected. Adoption additionally uses `legacy_imported`,
`legacy_import_version_recorded`, and `adoption_allowance_recorded` internal facts;
the service never exposes their injection through CLI inputs. Failed later
operations commit neither a prefix nor staged artifacts.

Effects are `{kind:"initialize_workspace"|"preserve_manual_views",slug,snapshot_digest}`.
Workspace effects pin an immutable `{directories:[RelativePath],files:[{path,digest}]}`
snapshot. View-preservation effects use slug `.` and save original view bytes.
The committed event is the durable intent. Idempotent filesystem application
happens afterward, with content-bound `.search/effect-DIGEST.json`
acknowledgements. Interrupted initialization does not imply an atomic transaction
across legacy files. Status/next report pending effects as a blocker; replaying
the original command or `search render` recovers without rerunning math or
granting another admission. Conflicting preexisting bytes cause `recovery_conflict`.
Projection writes serialize against the current event document to avoid stale
SEARCH_TREE/LINEAGE output from concurrent commands.

Task 4 wires managed legacy preflight for admission, imported/finished terminality,
safe paths, and refusal of unmanaged verifier entry within a registered controller.
Full legacy research-stage reservation enforcement, guarding direct unmanaged
research, journal acknowledgement and process execution remain Task 5. This is
not an OS sandbox, a production bypass flag, or a claim that host hooks are already
integrated. No progress-report addendum or event timestamp field is implemented.
