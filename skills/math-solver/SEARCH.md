# Mathematical search controller JSON contract

This document defines the executable Task 2 interfaces for the persistent search
controller. It does not authorize execution, publish results, or replace the
existing math-solver workflow. Proof acceptance, checkpoints, scheduling, execution,
CLI commands, and host adapters are implemented by subsequent tasks.

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
