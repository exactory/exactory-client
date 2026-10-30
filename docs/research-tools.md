# Research tools

Read the [research constitution](../RESEARCH_CONSTITUTION.md). Tools perform
actions; [orchestrations](research-workflow.md) hold the gates for claims and
milestones. The [cohort](../skills/cohort/SKILL.md),
[literature-review](../skills/literature-review/SKILL.md),
[ideate](../skills/ideate/SKILL.md) and [experiment](../skills/experiment/SKILL.md) skills
provide the tools below at every stage. Each caller states the question, depth
and authorized budget. Each call records its arguments, effective amounts, work
done, coverage, stopping reason and cost. A tool record grants no approval or
scientific credit by itself. Independent assessment is a condition of a claim.

Run commands from the study workspace with the installed plugin's `bin/` on
`PATH` in each shell call. For a new mutation, read the current `REVISION` from
`status` and supply a unique `--request-id`. Replay an interrupted mutation with
its original identity, revision and payload. Read
[the CLI reference](research-cli.md) and `exactory-research example OPERATION`
for complete existing payloads. Replace illustrative IDs and artifact descriptors
with actual evidence before recording a call.

## Catalog

| Tool | Arguments, depth and budget | Commands | What the call records |
| --- | --- | --- | --- |
| Population | Corpus, category, window; abstract depth of none, a seeded sample of N (research with explicit depth or a selected plan; verification under its preset), screening, or every member; request, byte, screening and reading allowance. | `exactory-cohort freeze` defines the window; `collect`, `resume`, `import-oai-cohort` enumerate; `sample`, `select-cohort-abstract`, `batches`, `read-batch`, `screen-batch`, `screening-checkpoint` select and inspect. | The freeze result is a definition. Acquisition retains collections, original responses, page receipts, identities, exclusions, completeness and costs. `cohort_sample` keeps the seeded draw; `reading` and `reading_batch` keep actual inspections; screening records keep dispositions, audits and saturation evidence. `batches` writes work files without reading credit. |
| Search | Question or purpose, queries, providers, hits per query and number of hit abstracts to read; request, byte and reading allowance. State how many distinct works to survey and how many receive abstract-only or full reading. | `acquire`, `import-response`, `search`, `read-batch`, `loop-close`; `population-query` supplies local candidates. | `literature_search` binds captures, scope or provisional question, dates, found works, dispositions, judgment, gaps and effective depth. Readings retain inspections; `loop_closure` records covered purposes, gaps, tried queries and its effective limit. Coverage describes captured results per query as below. |
| Read | Exact works and versions, abstract or full-text depth, required units and supplements; acquisition and reading allowance. State the number of abstract-only and full readings. | `acquire`, `fulltext`, `visual`, `bundle`, `read`, `read-batch`, `require-fulltext`, `availability`, `defer-source`, `resume-source`. | Original bytes and extraction provenance, `source_bundle` unit and bibliography inventories, `reading` inspections and notes, `fulltext_requirement` dependencies, availability and source-deferral history. A capture establishes acquisition; a reading establishes only its inspected coverage. An unavailable unit remains a gap. |
| Expand | Exact roots, references or citing works, number of levels and reading depth at each level; request, byte and reading allowance. | `roots`, `expand`, `acquire`, `import-response`, `search`, and the reading commands. | `literature_scope` selects exact roots and collections. Captured bibliography occurrences and provider responses preserve the source network and identity conflicts. `expand` acquires an unresolved identifier. The caller repeats expansion for the stated levels and records citing-work captures through search. Direction and levels are caller instructions, not additional `expand` flags. |
| Ideate | Question, candidate count, credible alternatives, evidence, distinguishing tests, failure signals and resource demand. | `intent`, `strategy`, `research-lead`, `work-item`. | `research_intent`, `strategy_dossier`, `research_lead` and `research_work_item` retain proposed questions, comparisons, provenance, dispositions, actual execution status and dependencies. Candidate count is the caller's requested depth. Committing a direction uses the orchestration's decision gate. |
| Probe | Exact question, Python program, pinned inputs, timeout and experiment allowance; for a run of a program that a plan will admit, the future cycle id, the program's stable path and the outputs read. | `exactory-lab probe`, `probe-intent`, `probe`. | `probe_intent` pins the program and inputs and reserves time; `probe_launch` retains the frozen local launch; the `probe` outcome retains outputs, streams, exit status, elapsed time and observation with exploratory provenance. A disclosed run adds `program_run` to the intent and the outcome; without a read output its files and streams are sealed unread. See the two-phase contract below. |
| Run | Current admitted prospective plan, pinned program and inputs, exact runtime, outputs, validation and resource reservation. | `cycle`, `admit`, `bind-run`, `exactory-lab run`, `reconcile-run`, `assess`, `checkpoint`. | `cycle_plan`, `execution_admission`, binding and execution observations, `cycle_assessment` and checkpoints preserve authority, original outcomes, sealed outputs, actual usage, validity, scope, failures and next questions. Acceptance of a result uses the orchestration's gate. |
| Synthesis notes | Exact supporting sources, field and applicability, claim scope, rationale, innovation cases and context; reading and analysis allowance. Innovation calls state candidate and case counts. | `standards`, `rationale`, `innovation`, `context`. | Selected immutable `synthesis` sections bind their own evidence and assessments. Innovation retains the effective candidate and case amounts. Notes inform claims; the direction gate checks the standards it needs. |
| Challenges ahead | Ultimate and nearer goals, advance criteria, exact full-read evidence and reason for a changed direction; source and analysis allowance. | `grand-challenge`. | `grand_challenge` and its current selection retain goals, criteria and evidence for the study. This study record is distinct from a challenge posted on exactory. It belongs to ai-science's direction minimum. |
| Depth plan | Default amounts and a reason for each change, within authorized resource budgets. | `depth`. | Immutable `depth_plan` with `{id, values, reason, revision}` and `depth_plan_selection` selecting the current plan. The plan stays outside configuration and claim dependency digests. |

Tool budgets use the existing `budget` operation and `resource_budget` and
`resource_account` records. The purposes are `literature`, `screening`,
`experiment` and `development`. Record actual network requests, source bytes,
readings, screenings, model usage, wall time and rounds where applicable.
Unknown usage remains unknown. Exhaustion records a checkpoint condition.
Transport limits remain: reading batches contain at most 100 items and screening
batches at most 200. Call counts and budget ceilings allocate work; assessors
judge whether that work is sufficient for a claim.

## Probe

A probe can answer a quick computational question before target commitment or
during any later stage. Configuration is required before launch; the target and
cycle may still be absent. The launcher pins the exact program and input bytes,
records `probe-intent` and reserves the timeout against the `experiment` purpose
before running the program.

```sh
exactory-lab probe --id finite-example-001 --question "Does this finite example satisfy the proposed bound?" \
  --script experiment/check.py --input experiment/data.json --output-dir results --timeout 30
```

Repeat `--input` for further files. The launcher uses the current Python
interpreter, supplies the copied input paths as script arguments, runs in a fresh
directory under `research/probes/ID/` with stdin closed, and enforces the timeout.
The output directory is relative to that working directory. It pins output files,
stdout and stderr, retaining the actual outcome and usage for nonzero exits,
timeouts, failures and output-bound outcomes. Each captured stream is bounded to
32 MiB. Exit status is an integer or `timed_out`, `failed`, `output_bound`
or `interrupted`. The outcome charges actual elapsed time and releases the
reservation. The CLI reference gives the
[launcher and outcome contract](research-cli.md#exploratory-computation-probe-intent-probe).

The two managed operation payloads are:

```json
{
  "id": "finite-example-001",
  "question": "Does this finite example satisfy the proposed bound?",
  "interpreter": "/absolute/path/to/python3",
  "program": {
    "path": "research/sources/objects/PROGRAM_SHA256",
    "sha256": "PROGRAM_SHA256", "size": 123, "media_type": "text/x-python"
  },
  "inputs": [{
    "path": "research/sources/objects/INPUT_SHA256",
    "sha256": "INPUT_SHA256", "size": 45, "media_type": "application/json"
  }],
  "timeout_seconds": 30
}
```

```json
{
  "id": "finite-example-001",
  "command": "/absolute/path/to/python3 program/check.py inputs/0/data.json",
  "outputs": [],
  "stdout": {
    "path": "research/sources/objects/STDOUT_SHA256",
    "sha256": "STDOUT_SHA256", "size": 20, "media_type": "text/plain"
  },
  "stderr": {
    "path": "research/sources/objects/STDERR_SHA256",
    "sha256": "STDERR_SHA256", "size": 0, "media_type": "text/plain"
  },
  "exit_status": 0,
  "elapsed_seconds": 0.2,
  "observation": "The checked example satisfied the bound."
}
```

The descriptors above illustrate the shape. Use held artifacts and their actual
hashes and sizes. `outputs` lists the output artifact descriptors. Save the first
payload as `probe-intent.json` and the observed second payload as `probe.json`.
The launcher normally performs these mutations; the managed interfaces are:

```sh
exactory-research probe-intent --file probe-intent.json --expected-revision REVISION --request-id intent-finite-example-001
exactory-research probe --file probe.json --expected-revision REVISION --request-id outcome-finite-example-001
```

Both accept `--check`, which reports the first refusal of the payload, for
example `probe_intent_missing` for an outcome without an intent, and reserves and
charges nothing. `exactory-lab probe --check` is refused with `check_unsupported`
before the launcher reads anything, because the launcher runs a program. A check
of `artifact` on a probe path is refused with `check_unsupported`, because that
pin commits in two steps.

The launcher persists `supervision.json` with the worker and program start
identities, process group and frozen deadline. An independent watchdog enforces
that deadline after worker loss even without another CLI invocation. It writes
`supervisor-timeout.json` before terminating the program group and
`supervisor-terminal.json` after verifying termination. A later retry reconciles
the outcome and budget using those receipts; the watchdog's terminal file alone
does not release the reservation.

A probe can disclose a run of the program that a later plan admits, so that the
plan accounts for what was known before it. Add `--cycle-id ID` and
`--program-path PATH` to the command, and `--read-output PATH` for each output
that you read (`work/FILE`, `stdout` or `stderr`). The intent then carries
`program_run: {cycle_id, program_path, read_outputs}` and the outcome
`program_run: {started_at, ended_at, outputs, read_outputs, outputs_unread}`.
A run with no read path is a completion check: its outputs and streams are
stored sealed, their hashes are recorded, and their bytes are not read, cited or
delivered. The plan names each such run in `prior_knowledge`, and `admit`
requires the program that the plan froze. The CLI reference gives the
[fields and refusals](research-cli.md#exploratory-computation-probe-intent-probe).

Resolve duplicate IDs before launch. A repeated ID returns its recorded outcome
without executing again; a changed intent under that ID is refused. Launch
ownership is persisted and exclusive. A retry observing a live owner returns
`pending` and retains the reservation. Reconcile an intent without an outcome
only after owner loss or checked terminal evidence and verification that the
original process group has stopped, using retained output files or recording
`interrupted`. Absence of an outcome alone
leaves the reservation held. Reconciliation charges measured usage once and
records any unmeasured remainder as unknown usage.

`status` reports `probes: {count, elapsed_seconds}` when intent or outcome
history exists. A dossier cites the recorded probe with
`{kind: "record", record_kind: "probe", id, digest}`, using the exact
probe record's ID and digest. Its artifacts retain exploratory origin through
later references and imports and cannot acquire prospective-test credit. Existing
supported imported evidence retains its standing. Independent support assessment
determines additional validation, including whether a new execution is required.
Computations relied on by a recorded mathematical strategy, proof, finite check,
counterexample or acceptance use native admission. The
[native provenance contract](../skills/math-solver/SEARCH.md#computation-admission-and-mandatory-interpretation)
describes probe provenance through native imports and `exploratory_evidence`.

## Depth plan

Preparation policies are presets. The research defaults are:

| Key | Default | Effective amount recorded by |
| --- | ---: | --- |
| `loop_abstracts` | 100 | Loop readings and `loop-close`. |
| `round_loop_abstracts` | 40 | Loop readings during a development round. |
| `hits_per_query` | 10 | `search`. |
| `innovation_candidates` | 10 | `innovation`. |
| `innovation_cases` | 5 | `innovation`. |
| `sample_size` | 100 | `sample`. |
| `search_readings` | 20 | Search-hit readings. |
| `core_fulltexts` | 10 | `require-fulltext` with purpose `core`. |

Values are positive integers and `innovation_cases` is at most
`innovation_candidates`. Omitted keys retain their current values. A changed
plan records the reason and all resolved defaults:

```json
{
  "id": "depth-002",
  "values": {"loop_abstracts": 150, "hits_per_query": 15},
  "reason": "The direct comparison spans two neighboring methods."
}
```

```sh
exactory-research depth --file depth.json --expected-revision REVISION --request-id set-depth-002
```

With a selected plan, `status` exposes `limits.depth: {plan_id, values}`.
Stores without a selected plan retain their existing report shape. Depth changes
need no constitution revalidation, including in research studies retaining Version 6.
Verification depth changes are out of scope for this release; verification keeps
its fixed contract and `depth` returns `profile_inapplicable` there.

Each consuming call accepts an optional `depth` object for its applicable keys:
`search`, `sample`, `require-fulltext` for `core`, `innovation` and
`loop-close`. A `read-batch` call keeps `depth: "abstract"` and supplies
`depth_values` for its loop, round-loop or search-hit amounts; the earlier
object form of `depth` also remains supported. Overrides belong to the call,
rather than individual items. The `reading_batch` record saves
`effective_depth`; samples and core requirements save `depth`; searches save
`hits_per_query`; innovation saves its candidate and case amounts; and closure
saves `depth.loop_abstracts`. Research `sample` requires an explicit `depth`
object or selected plan. For example, a search may include
`"depth": {"hits_per_query": 15}`; the search also accepts its own
`hits_per_query` value. Record the resolved effective values alongside actual
work. Reassess a saved record using its recorded amounts. Legacy records use
their original policy values. Under `lineage-v1`, saved candidate amounts and
exact external-case counts apply. New `exhaustive-v1` and `screened-v1` research
sections require at least `innovation_cases` external cases and at most
`max(10, innovation_cases)`; historical sections without depth keep five to ten.
Later plans change future defaults; they leave earlier evidence amounts and
claim minimums unchanged. Resource budgets bound cost without an additional
multiple-of-default ceiling.

## Search coverage

Keep every original captured response. Each entry of `literature_search.coverage`
has `query`, `requested_k`, `returned_count`, `inspected_count`,
`page_positions`, `provider`, `ordering` and `kind`. These counts describe
captured rows and distinct dispositioned works and grant no reading credit.
The entries retain captured page positions, request ordering and enumeration
evidence. `complete` requires
evidence that enumeration finished. `top_k` requires evidence of the ordering
that establishes the first k inspected results. `bounded_unordered` describes
bounded inspection without that ordering evidence. An author's label supplies
neither completeness nor ordering evidence. Bounded coverage supports statements
about inspected results; results beyond that coverage remain unknown.

Legacy searches retain the coverage their captured responses establish and use
`unspecified` only where ambiguous. Preserve those responses without recapture.
Dispositions account for every captured result, including contradictions and
unresolved identities. Parser and capture failures remain integrity gaps;
research uses bounded enumeration as a coverage limit. Verification preserves
all enumeration conditions, including `search_response_incomplete` and the
resulting `search_pending`, for historical and current records.

A search can state a provisional `question` before roots exist, with
`scope_digest: null` and `provisional: true`. It is visible in status and retains
its work and coverage. A later scoped search can reuse its captured responses
through `binding: {provisional_search_id, reason}`, recorded as `search_binding`.
Claim minimums count searches bound to the current scope; provisional searches
remain exploratory until that binding exists.
