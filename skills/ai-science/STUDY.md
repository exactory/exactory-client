# The study workspace

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and the
[managed research workflow](../../docs/research-workflow.md).
`exactory-lab init` creates the study layer. Entering stage `write` adds the
draft layer when the objective carries `title` and `category` and the
workspace holds neither a draft record nor a `.exactory/draft.json` entry.
When the objective lacks one of the fields and the workspace holds neither,
`exactory-draft init --title TITLE --category CATEGORY` adds the draft layer
("Study state and decisions" below gives every state). Creating either layer
leaves milestone gates pending. AI Science uses the
[shared tools](../../docs/research-tools.md) at any point; stage labels describe
milestone progress. Every path is relative to the workspace root.

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
│   ├── probes/            exploratory programs, inputs, outputs and outcomes
│   └── literature.md      cumulative human survey narrative
├── reviews/               exact deliveries, original reviews and score history
│   └── round-00N/admission.json   saved round-admit output: the admitted goal
└── learnings/iter_NNN.md   predictions, observations and next questions
```

SQLite records configuration, source collections, versions, readings, the Grand
Challenge record, synthesis, cycles, admissions, execution observations,
assessments, checkpoints, depth plans, exploratory probes, reviews, contribution
analyses, round decisions, round reviews, round admissions, round assessments,
cohort predictions and remote
intents. Read `exactory-research status --summary` and
`next --summary` for current obligations; `status` reports `round` with the
current round number, whether it is active or assessed, its decision, the number
of its open round obligations, counts of its derived progress, the bundle's
measurement, the admitted round's limits, the `development` budget and the usage
since admission. It does not report the admitted goal; the saved `round-admit`
output under `reviews/` carries it. Hand-editing a projection cannot authorize
work. Explicit `export --kind workspace` rebuilds workspace views from current
history; run reconciliation repairs execution views. Legacy work is adopted
explicitly and does not inherit finished reading or execution credit from old
labels.

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
`write`, `evaluate`, `deposit`, `submit`, and `complete`. The workflow describes
milestone transitions and explicit returns. Managed
research can move from `initiate` directly to `literature`, leaving `cohort`
without a gate. Completing `literature` and entering `ideate` require
`direction`; entering `experiment` requires a current pending admission
through `execution`. An approved canonical round `investigate` or `pivot`
permits `evaluate` to return to `literature`. Enter unfinished work with
`--status pending`; a proposed `done` status checks the destination too.

Entering `write` also records the draft workspace, in the same mutation as the
study state, and so does a later `exactory-lab state set` that succeeds while
the study is at `write`. The mutation records the draft only when the
objective carries both `title` and `category`, no draft record exists, and no
entry exists at `.exactory/draft.json` (a regular file, a symbolic link,
dangling or not, and a directory each count as an entry). The export then
writes `.exactory/draft.json` (the marker) and creates `draft/`. Give the two
fields when the objective is committed: `target` refuses them on a committed
objective with `objective_locked`. At `write`, `status` and `next` carry one
`draft_marker_missing` obligation, appended after the other obligations and
selected as `next`, while no draft record exists or the marker is not a
regular file that holds the record's JSON. Its message names the step for
the state:

- A draft record, and the marker is missing, holds other JSON or is a
  symbolic link: `exactory-research export` writes the marker from the
  record. It replaces a link and leaves the link's target alone.
- A draft record, and the entry is a directory or another type: move the
  entry to an unused name in `.exactory/`, then export.
- No draft record, and a regular file that holds a JSON object whose
  `version` is absent, 1 or 2: `exactory-research adopt` with
  `.exactory/draft.json` in `files` records that object as the draft, as it
  is. `exactory-draft init` refuses the directory while the file exists.
- No draft record, and a symbolic link (dangling included), a directory or a
  regular file that adoption refuses: move the entry to an unused name in
  `.exactory/` (`mv -n`, no trailing slash on either path), then run `status`
  again and take the step it names. The saved entry is not an input of
  `adopt`.
- Neither record nor entry, and the objective lacks a field:
  `exactory-draft init --title TITLE --category CATEGORY` records the draft
  from its own values and writes the marker. The objective does not change.
- Neither record nor entry, the objective has both fields, and the study
  status is not `done`: `exactory-lab state set --stage write` records the
  draft from the objective.
- Neither record nor entry, both fields, and the status is `done`: the same
  `exactory-draft init` with the objective's values. The status stays
  `done`. A same-stage update with status `done` checks the completion and
  readiness gates of `write` first.

The authorship hook records a `.tex` file written with Write under `draft/`
only while `.exactory/draft.json` is a file, and it does not compare the file
with its record. The creation, export, adoption or move of the marker records
nothing for a source written before it.

Change state with `exactory-lab state set`, and preserve actual decisions with
`exactory-lab decide --stage STAGE --decision TEXT --why TEXT --evidence TEXT`.
Both use revision and request identity, as do other managed mutations. Keep the
original identity and payload when retrying a lost success. New decisions use a
current revision and new ID. Every `exactory-research` mutation also accepts
`--check`, which validates the payload against one snapshot of the store and
writes nothing. An accepted check is a diagnostic, not a record: it satisfies no
gate, and the write validates again. Acquisitions, reviewer calls and
`reconcile-run` refuse it (`check_unsupported`); the
[CLI reference](../../docs/research-cli.md#inputs-and-receipts) lists the others.

Waiting and free-form operational status describe conditions, not research
completion. `loop.target`, `budget`, and `notes` record optional manuscript
measurement limits and user pacing; they do not replace research cycle accounts.

## Sources and cohort doctrine

`exactory-cohort freeze` defines the cohort; managed collection enumerates it.
Actual `read` records and source inspections establish coverage. Any local
cohort inventory is a human view, not a ledger of authored completion booleans.
State each population call's depth and budget. Record the preset and effective
amounts, actual inspections, coverage, stopping reason and cost. Keep the selected
cohort abstract obligations, root/reference tiers, required
full readings, source units, dates, and availability gaps in the common records.

Preserve `cohort/doctrine.md` and notes as source-grounded synthesis. Record
searches and synthesis with the supported mutations at the depth the
current question needs. Managed status sets `preparation.advisory: true`;
`next` follows the current milestone. The `direction` gate lists its minimum
even before a cycle decision exists. The direction minimum includes the
scoped direct search, full readings of relevant/contradictory hits and roots,
applicable source-valid standards and a Grand Challenge record. Its decision
binds decisive content delivered to both reviewers and the passed `q1_venue`
assurances. Deliveries cover complete inspected ranges and every named image
asset with checked provenance. Inventory and author notes alone supply no
source-content coverage; missing content keeps `direction_evidence_unassessed`. Other synthesis notes remain available throughout. The human survey
log points to those records and original evidence; it does not substitute for them.

## Depth plans and claim currency

The preparation policy is a preset of the research depth plan. `depth` records
changed defaults and their reason; consuming calls can state overrides and retain
their effective amounts. Saved work uses its recorded amounts. Research studies
can keep Version 6 or adopt Version 7 without changing a claim through adoption
alone. Verification retains Version 6 and its fixed contract; its adoption is
out of scope for this release.

Keep full dependency maps for audit. Claim currency follows objective, scope,
assumptions, plan, executions, supporting evidence and cited sources, inheritance,
validations, failures and authorizations. Unrelated global preparation,
configuration, constitution, foundation inventory and synthesis changes leave
the claim current. Validate historical digests against recorded inputs before
comparing claim projections. Managed per-record history reconstructs the original
revision's input snapshot; missing or unauthenticated inputs report
`claim_reassessment_required`. The
[workflow](../../docs/research-workflow.md#studies-in-progress) gives the
compatibility procedure.
Reconcile pending executions once under their frozen authority.

## Experiments, branches, and claims

A probe records exploratory calculations before or during any milestone. Its
pinned program, inputs, outputs, elapsed time and observations retain exploratory
provenance through later references or imports. Admitted runs supply prospective
test evidence under their exact contract. Independent support assessment decides
what further validation a claim needs.

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
command is a reproduction instruction, not evidence that it ran. The manuscript
pin accepts a reference only when it is evidence of the current candidate, that
is, a reference that the selected assessment, or a retained branch's plan or
assessment, cites. Build the ledger before the result dossier is delivered and
run `exactory-research check-claims --file FILE` on its `claim_evidence` array.
The command needs the candidate but not the readiness review. It lists every
reference outside the candidate with its claim id and changes nothing. A
reference enters the candidate through a new assessment that cites it and a
checkpoint of that assessment. After the readiness review this also costs a new
review, so run the check before the delivery. For each listed reference, cite a
candidate reference instead or record such an assessment. Then run the check
again, until it exits 0.

## Reviews and continuation

A result dossier delivers the selected assessed research candidate and actual
evidence to two independent assessors before writing. Each assesses support before
consequence in one response; the canonical findings derive compatible readiness.
Use the [decision protocol](../../docs/research-decisions.md) for exact delivery,
context verification and objection handling. A manuscript export later delivers
the exact paper and claim evidence for distinct blind reviews.
Original review JSON, provenance, digests, and receipts stay retained. The current
whole gate decides mechanical eligibility; a historical review does not. Before
the result delivery, run `check-claims` on the claim ledger (above), so that the
delivered candidate carries every reference that the manuscript will cite.

[LOOP.md](LOOP.md) governs development and manuscript iteration. The write skill's
[WORKSPACE.md](../write/WORKSPACE.md) describes the human review and learning files.
On resume, read current status, the search tree, checkpoint lineage, pending work,
the saved `round-admit` output of an active round, and user context before
continuing. Preserve unfinished source changes and all
managed history; a crash never authorizes erasing unmeasured work.
