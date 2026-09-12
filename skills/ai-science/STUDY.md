# The study workspace

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and the
[managed research workflow](../../docs/research-workflow.md).
`exactory-lab init` creates the study layer; `exactory-draft init` can add the
draft layout when the title and category exist. Creating either layout leaves
research preparation pending. Every path is relative to the workspace root.

```text
<workspace>/
├── .exactory/
│   ├── research.sqlite3    authoritative common history and request receipts
│   ├── study.json         current study projection
│   ├── decisions.jsonl    projection of retained stage decisions
│   ├── draft.json         current draft projection
│   ├── deposit.json       projection of the observed deposit
│   ├── citation-check.json
│   └── citation-cache.json
├── context/               preserved human material and constraints
├── cohort/                source-linked notes and doctrine
├── idea/idea.md            full objective, branch hypotheses and prospective tests
├── experiment/
│   ├── code/  logs/  results/  plots/
│   └── journal.jsonl      human search narrative linked to managed records
├── draft/                 manuscript sources and references.bib
├── evidence/claims.json   scoped claims with actual evidence and quantities
├── research/
│   ├── sources/objects/   immutable captured and pinned bytes
│   └── literature.md      cumulative human survey narrative
├── reviews/               exact deliveries, original reviews and score history
└── learnings/iter_NNN.md   predictions, observations and next questions
```

SQLite records configuration, source collections, versions, readings, synthesis,
cycles, admissions, execution observations, assessments, checkpoints, reviews,
and remote intents. Read `exactory-research status --summary` and `next --summary` for current
obligations. Hand-editing a projection cannot authorize work. Explicit
`export --kind workspace` rebuilds workspace views from current history; run
reconciliation repairs execution views. Legacy work is adopted explicitly and
does not inherit finished reading or execution credit from old labels.

The initializer creates a Git repository when needed and preserves an existing
one. Respect the user's storage and commit policy. A durable checkpoint may use
a Git commit or an immutable snapshot, with a stable ID, exact statement,
evidence, verification status, unresolved obligations, and next hypothesis.
Research stored under a user-designated local-only directory stays local.

## Study state and decisions

The CLI exports a version-2 study projection:

```json
{"version": 2, "slug": "study", "stage": "cohort",
 "status": "pending", "autopilot": true, "waiting": null,
 "loop": {"target": null, "budget": null, "notes": ""},
 "created": "...", "updated": "...",
 "research": {"store": ".exactory/research.sqlite3", "profile": "research"}}
```

Stages are `initiate`, `cohort`, `literature`, `ideate`, `experiment`,
`write`, `evaluate`, `deposit`, `submit`, and `complete`. The workflow
describes adjacent forward transitions and explicit returns. Both source
completion and target prerequisites apply. Enter unfinished work with
`--status pending`; a proposed `done` status checks the destination too.

Change state with `exactory-lab state set`, and preserve actual decisions with
`exactory-lab decide --stage STAGE --decision TEXT --why TEXT --evidence TEXT`.
Both use revision and request identity, as do other managed mutations. Keep the
original identity and payload when retrying a lost success. New decisions use a
current revision and new ID.

Waiting and free-form operational status describe conditions, not research
completion. `loop.target`, `budget`, and `notes` record optional manuscript
measurement limits and user pacing; they do not replace research cycle accounts.

## Sources and cohort doctrine

`exactory-cohort freeze` defines the cohort; managed collection enumerates it.
Actual `read` records and source inspections establish coverage. Any local
cohort inventory is a human view, not a ledger of authored completion booleans.
Keep the entire cohort's abstract obligations, root/reference tiers, required
full readings, source units, dates, and availability gaps in the common records.

Preserve `cohort/doctrine.md` and notes as source-grounded synthesis. Record all
five search purposes and research standards, rationale, innovation studies, and
context with the supported mutations. The human survey log points to those
records and original evidence; it does not substitute for them.

## Experiments, branches, and claims

Each prospective cycle names the complete objective and exact branch scope,
predecessor checkpoint, hypothesis, distinguishing test, failure signals,
evidence requirements, and resource limits. A managed launch uses its current
admission and exact binding. Record human journal entries with actual cycle,
admission, execution, assessment, and checkpoint IDs.

Keep invalid, negative, timed-out, interrupted, and unknown outcomes distinct.
Preserve original sealed result evidence and resource charges, including failed
branches. A working source variant may change while the complete history remains.

A quantitative claim ledger precedes the manuscript. Each entry states the claim,
actual immutable evidence reference, source/result locator, scope, assumptions,
units, population/denominator, interval, outcome, and comparison baseline where
applicable. Unknown and inapplicable dimensions retain reasons. Distinguish fitted
parameters, external inputs, derivations, and selection choices. A suggested
command is a reproduction instruction, not evidence that it ran.

## Reviews and continuation

A readiness export delivers the selected assessed research candidate and actual
evidence to an independent assessor before writing. A manuscript export later
delivers the exact paper and claim evidence for two distinct blind reviews.
Original review JSON, provenance, digests, and receipts stay retained. The current
whole gate decides mechanical eligibility; a historical review does not.

[LOOP.md](LOOP.md) governs development and manuscript iteration. The write skill's
[WORKSPACE.md](../write/WORKSPACE.md) describes the human review and learning files.
On resume, read current status, the search tree, checkpoint lineage, pending work,
and user context before continuing. Preserve unfinished source changes and all
managed history; a crash never authorizes erasing unmeasured work.
