# Mathematical search controller JSON contract

This document defines the executable Task 2 and Task 3 interfaces for the persistent search
controller. It does not authorize execution, publish results, or replace the
existing math-solver workflow. Checkpoints, proof acceptance, and scheduling are
pure logic. Filesystem evidence verification, execution, CLI commands, and host
adapters are implemented by subsequent tasks.

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
| `replan_recorded` | `{"route_orders": [RouteOrder], "progress_acceptance_ids": [ID], "reason": Text}` |
| `control_recorded` | `ControlInput` |

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
schema_version: 1
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
status: "admitted" | "active" | "waiting" | "result_ready" | "finished"
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

Recording facts updates local lifecycle only, never theorem truth. Terminal local
nodes cannot restart through these facts. The service determines truthful stage
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
```

Retreat requires a declared predicate that has fired: account move/run limits,
observed studied-strategy failure, declared stagnation, or a usable counterexample
to a method prerequisite. It records the reason and saved checkpoint, marks the
node retreated, suspends logically descending investigations that depend on the
abandoned routes/assumptions, and preserves independent accepted facts. It retains
all accounts and historical records. Selection then tries admitted alternatives
at the nearest ancestor before returning to root traversal.

Replan preserves accounts and changes only committed alternative/selected-route
order and round accounting. No usable new progress increments the objective round
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
IDs do not consume another count; missing IDs conservatively count each invocation.
The 40th request durably pauses and issues one `summary_then_stop`. Later calls,
including replay of a formerly continuing delivery after pause, permit stopping.
Neither missing metadata nor `stop_hook_active: false` creates new authorization.
Pause, unrelated/ambiguous focus, resolution and genuine blockers permit handoff.
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
{kind: "retreat", node_id: NodeID, criterion: RetreatCriterion}
{kind: "replan", round: PositiveInteger, obligation_ids: [ObligationID]}
```

Priority is pause/state errors/focus/live execution, pending reconciliation,
produced-result verification/snapshot/acceptance, root finalization, due local
cash-out, active main work and its continuation, waiting prerequisites, nearest
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
`stop_count`, `stop_deliveries` (delivery ID to decision), `summary_issued`,
`stop_decision`, `pause_reason`, `focus`, `state_error`, `active_node_id`,
`retreat_node_id`, `pending_moves`, `node_facts`, `selected_routes`, `closure`,
`main_external_block`, `side_interval`, `resume_record`, `resume_ids`,
`side_instruction_ids`, and `retreats`. All live in the single replayed state.
Node-fact entries add derived `suspended: Boolean`; retreat may create an entry
containing only that suspension field before service facts arrive. Initial nullable
references are null, collections empty, focus focused, counters zero, and initial
Stop decision allow_stop. Side intervals store `node_id`, `account_id`,
`start_used_moves`, and `max_moves`. Pending move identities and unresolved
execution errors are reserved integration fields for the subsequent execution
reducer; they are not public flags and Task 3 does not invent executor events.
