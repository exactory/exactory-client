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
| Read | Exact works and versions, abstract or full-text depth, required units and supplements; acquisition and reading allowance. State the number of abstract-only and full readings. | `acquire`, `fulltext`, `fulltext-start`, `fulltext-resume`, `fulltext-status`, `visual`, `bundle`, `read`, `read-batch`, `require-fulltext`, `availability`, `defer-source`, `resume-source`. | Original bytes and extraction provenance, `source_bundle` unit and bibliography inventories, `reading` inspections and notes, `fulltext_requirement` dependencies, availability and source-deferral history. A capture establishes acquisition; a reading establishes only its inspected coverage. An unavailable unit remains a gap. |
| Expand | Exact roots, references or citing works, number of levels and reading depth at each level; request, byte and reading allowance. | `roots`, `expand`, `acquire`, `import-response`, `search`, and the reading commands. | `literature_scope` selects exact roots and collections. Captured bibliography occurrences and provider responses preserve the source network and identity conflicts. `expand` acquires an unresolved identifier. The caller repeats expansion for the stated levels and records citing-work captures through search. Direction and levels are caller instructions, not additional `expand` flags. |
| Ideate | Question, candidate count, credible alternatives, evidence, distinguishing tests, failure signals and resource demand. In a problem record: the question of the call with its places, the move, the inputs, and the amounts of forms, nearest alternatives, expected relations and compared pairs. For an import: the source record, its revision and the candidates that are taken up. | In a study: `intent`, `strategy`, `research-lead`, `work-item`. In a problem record: `forming`, `form-outcome`, `forming-import`. | `research_intent`, `strategy_dossier`, `research_lead` and `research_work_item` retain proposed questions, comparisons, provenance, dispositions, actual execution status and dependencies. Candidate count is the caller's requested depth. Committing a direction uses the orchestration's decision gate. `forming_call` retains the question, the move, the inputs, the amounts as the performer reports them, every form that the call produced, the probes that it used, the stopping reason, the performer and the measured usage. `form_outcome` retains how one form, one expected relation or one planned check ended, with its evidence. `problem_forming_import` retains a verified snapshot of the forming history of another problem record, and lists each record of it with its origin, its original id, its date and its standing. See [Forming calls and outcomes](#forming-calls-and-outcomes). |
| Probe | Exact question, Python program, pinned inputs, timeout and experiment allowance; for a run of a program that a plan will admit, the future cycle id, the program's stable path and the outputs read. | `exactory-lab probe`, `probe-intent`, `probe`. | `probe_intent` pins the program and inputs and reserves time; `probe_launch` retains the frozen local launch; the `probe` outcome retains outputs, streams, exit status, elapsed time and observation with exploratory provenance. A disclosed run adds `program_run` to the intent and the outcome; without a read output its files and streams are sealed unread. See the two-phase contract below. |
| Run | Current admitted prospective plan, pinned program and inputs, exact runtime, outputs, validation and resource reservation. In a problem record, optionally the test declaration of the plan: the alternative with the outcome that it excludes, the account of prior knowledge and the search for the outcome in print. | `cycle`, `admit`, `bind-run`, `exactory-lab run`, `reconcile-run`, `assess`, `checkpoint`. | `cycle_plan`, `execution_admission`, binding and execution observations, `cycle_assessment` and checkpoints preserve authority, original outcomes, sealed outputs, actual usage, validity, scope, failures and next questions. Acceptance of a result uses the orchestration's gate. A plan with a test declaration keeps the declaration, frozen before the execution. See [Run](#run). |
| Synthesis notes | Exact supporting sources, field and applicability, claim scope, rationale, innovation cases and context; reading and analysis allowance. Innovation calls state candidate and case counts. | `standards`, `rationale`, `innovation`, `context`. | Selected immutable `synthesis` sections bind their own evidence and assessments. Innovation retains the effective candidate and case amounts. Notes inform claims; the direction gate checks the standards it needs. |
| Challenges ahead | Ultimate and nearer goals, advance criteria, exact full-read evidence and reason for a changed direction; source and analysis allowance. | `grand-challenge`. | `grand_challenge` and its current selection retain goals, criteria and evidence for the study. This study record is distinct from a challenge posted on exactory. It belongs to ai-science's direction minimum. |
| Depth plan | Default amounts and a reason for each change, within authorized resource budgets; for a verification, also the stopping criterion of the deeper reading. | `depth`. | Immutable `depth_plan` with `{id, profile, values, reason, revision}` and `stopping_criterion` (required for verification), and `depth_plan_selection` selecting the current plan of each profile. The plan stays outside configuration and claim dependency digests. |

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
delivered. The plan's `prior_knowledge` names every disclosed run that carries
the plan's cycle id, and every disclosed run whose program path or program bytes
`prior_knowledge` names, whether the run's outputs were read or sealed. A run
left out refuses the plan with `preplan_probe_missing`. With `prior_knowledge`,
`admit` requires the program that the plan froze. Without it, `admit` refuses a
program that a disclosed probe ran before the plan was recorded. The CLI
reference gives the
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

## Forming calls and outcomes

In a problem record the Ideate tool records how a statement was formed and how
it ended. A forming call (`forming`) is one piece of forming work. It states
its own question: a phenomenon with the places where it appears, a thought
experiment, or another start. That question takes the place that the intent has
in a study. An outcome (`form-outcome`) records how one form, or one expected
relation or planned check of a form, ended. A third command, `forming-import`,
gives a problem record the forming history of another problem record whose
candidate it takes up (below). In this release the three commands run
in a problem record. In a study, a verification or a math-solver root they are
refused with `orchestration_conflict`. `intent` stays refused in a problem
record (`orchestration_conflict`), and `strategy`, `research-lead` and
`work-item` need a recorded intent (`research_intent_missing`), so the three
forming commands are the Ideate commands of a problem record.

A form is a tentative statement, kept with its text as it was stated. It passes
no gate, receives no independent assessment and stays in the record as history. Forming records
grant no openness, no significance, no proof credit and no test credit. The
candidates and the slate remain the records in which the problem-creation
orchestration compares and selects, and a candidate cites the forming calls and
outcomes that it derives from as records. Both commands check their own inputs
and nothing about the milestone, so they are available from the opening of the
record on.

A forming call records:

- the question, as `{kind, description, places}`. For the kind `phenomenon`,
  `places` lists each place with its evidence;
- the move, the kind of step that produced the forms, as an open value with a
  description. A `transfer` carries its dictionary, and a `comparison` carries
  the number of comparisons made;
- the inputs, each with its evidence;
- every form that the call produced: its text, whether the text is exact, its
  purpose (the statement that the record expects at that time, or an
  alternative of another form), the earlier form that it changes with the
  reason, its grounds, its nearest alternatives, the relations expected of it,
  and its planned checks, each with the outcome that would contradict the form
  or the reason that no outcome can. A correction also says, for each planned
  check of the earlier form, whether the check was made, whether the corrected
  form keeps its prediction and whether the correction was chosen with its
  outcome;
- the probes that the call used, each as an observation or as the origin of a
  form. A probe keeps its exploratory provenance;
- the stopping reason, and the performer: the session, or a model lane with its
  model, its effort, its prompt and its output, the last two as held artifacts.

The amounts are the performer's report. `amounts` gives the number of forms,
the number of nearest alternatives and of expected relations for each form, and
for a comparison the number of pairs. The harness records these numbers as the
performer reports them and does not compare them with the forms of the call.
The same holds for `usage`, which records measurements that a lane reports. A
forming call has no allowance of its own: the searches, readings and
computations that it uses are charged to the `literature` and `experiment`
purposes of the tools that made them.

An outcome of a form keeps two things apart: the standing of the form as a
claim (`refuted`, `proved`, `implied_by_known_results`, `found_in_literature`,
`supported`, `unresolved`, `without_evidence` or `not_examined`) and its
disposition in the exploration (`open`, `superseded`, `taken_up_as_candidate`
or `set_aside`). Each evidence item names its standing: a full reading, an
admitted computation and an accepted result of a delivered solver objective are
`admissible`, and a probe is `exploratory`. A form can be recorded as refuted
on a probe that exhibits the counterexample, and the outcome then records that
the refutation rests on an exploratory computation. A later outcome of the same
item supersedes the earlier one, which stays in the record.

A forming import (`forming-import`) serves a record that takes up a candidate
of another problem record. The call names the source workspace, its current
revision and the candidates that are taken up. It keeps a verified snapshot of
the source in the destination, as the objection carry keeps the proof of its
source, and it records one manifest, `problem_forming_import`. The manifest
lists the forming calls and the outcomes of the source, its probes, its plans,
runs and assessments, the searches that its test declarations name, and the
readings and solver snapshots that its outcomes cite. Each listed record keeps
its origin, its original id, its original date and its standing: a probe stays
exploratory, and an accepted result supports its conclusion. No record of the
source becomes a record of the destination, and the import grants no credit. A
candidate, an outcome and an entry of the accounts of the destination cite an
imported record by its origin and its id, the reviewers receive it from the
snapshot, and the harness counts the forms, the runs and the probes of the
source apart from the destination's own. The destination stays linked to the
source: the post gate reports a source whose forming history changed, and a
new import refreshes the link.

```sh
exactory-research forming --file forming.json --expected-revision REVISION --request-id forming-001
exactory-research form-outcome --file form-outcome.json --expected-revision REVISION --request-id outcome-001
exactory-research forming-import --file forming-import.json --expected-revision REVISION --request-id forming-import-001
```

`status` reports `forming: {calls, forms, by_standing, by_disposition,
without_outcome, outcomes}` once the record holds a forming call, and the
output folders show the calls in `intermediate/problem/forming/` and the
outcomes in `intermediate/problem/form-outcomes/`. An import has no view of
its own; its receipt returns the manifest. The CLI reference gives the
[payloads and refusals](research-cli.md#forming-records) of the first two
commands and those of the [import](research-cli.md#imported-forming-history),
and the
[math-conjecturer skill](../skills/math-conjecturer/SKILL.md#exploration) says
when to use the commands.

## Run

A run is the admitted execution of a plan that was frozen before it. `cycle`
records the plan, `admit` and `bind-run` admit and bind the program,
`exactory-lab run` executes it, `reconcile-run` records an interrupted outcome,
and `assess` assesses the result against the failure signals of the plan. The
CLI reference gives the [payloads](research-cli.md#operation-fields). A run
supports its stated inputs and nothing beyond, and it is never a proof.

In a problem record the objective of a plan is the current statement, and
`cycle` is refused while no statement is recorded. A plan of a problem record
can carry one optional part, the test declaration (`test_declaration`), when
its run is meant as a test of the statement. A plan without the part keeps the
bytes, the digest and the meaning that it had before 0.84.0, in a problem
record and in a study. Outside a problem record, a plan with the part is
refused with `orchestration_conflict`.

A check compares a statement with a fact. For a given text of the statement,
its result has one of three standings. It is a test when the check could have
contradicted the text and was fixed before its outcome was known. It is the
origin when the text was chosen with the result in view. It is an observation
otherwise. A probe result is an observation or the origin of a form, and it
keeps its exploratory provenance. Only a run can be a test.

The declaration is frozen with the plan, before the execution. It holds:

- `alternative`: an alternative to the statement that the record holds before
  the plan. It is a form of a recorded forming call, `{kind: "form",
  record_kind: "forming_call", id, form_id}`, or a declared neighbour of the
  current statement, `{kind: "neighbour", text}`, with its exact text
  (`problem_test_alternative_missing` otherwise).
- `excluded_outcome`: the outcome that this alternative excludes. The outcome
  that would contradict the statement stands among the failure signals that
  every plan holds.
- `prior_knowledge`: the account of what the performer knew about the outcome
  when the plan was frozen, a list of `{source: {kind, description}, known}`
  with at least one entry.
  The source kind is an open value. The described kinds are
  `record_computations` (the earlier probes and runs of the record),
  `inherited_computations` (the computations of the records that this record
  took material from), `external_computations` (another workspace or a model
  lane, named with its place), `literature_and_tables`, and
  `planning_model_recall` (what the model that plans the run recalls about the
  quantity). The harness records the account as the performer gives it. This
  account is a field of the declaration. The `prior_knowledge` of the plan,
  which names the disclosed probe runs of its program ([Probe](#probe)), is
  another field.
- `search_id`: the search for the outcome in print. It is one bounded search
  whose question is the quantity that the run computes, and the store holds it
  before the plan (`problem_test_search_missing` otherwise).

Which runs are tests. The declaration grants no credit by itself. A run is a
test of a text of the statement when the accounts of a value record enter it
as a check with the standing `test`, and these conditions hold
(`problem_test_run_invalid` otherwise):

- The plan carried the declaration, and it was recorded before the run was
  admitted.
- The plan names a statement record with the statement digest of that text. The
  digest covers the statement and the resolution criteria. After a correction
  of the text, a run on the earlier text is the origin of the corrected text.
  It stays a test only when the forming call of the correction states, for
  that run, that the corrected text keeps the tested prediction and that the
  correction was not chosen with the outcome, and gives a ground with evidence.
- The check names the alternative that the plan named. A test tells against
  that alternative and against no other.
- The check gives, for each source of the declaration's account, the finding
  that the outcome was not known.
- No earlier run and no earlier probe of the record has the question of the
  plan, character for character. The reviewers also receive, beside the test,
  the probes and the runs of each source that the record imported before the
  plan ([Forming calls and outcomes](#forming-calls-and-outcomes)).
- An assessment covers the run. For a passed test it gives `not_observed` for
  every failure signal of the plan.
- The candidate of the statement cites a form that was recorded before the
  plan (`problem_accounts_history_missing` otherwise).

The standing of a test is qualified by these checks, as openness is qualified
by its searches. The harness compares earlier questions character for
character, and it does not read what the search found. It does not establish
that the outcome was unknown to the model that planned the run. The result
reviewers therefore receive the plan with its declaration, the search, and
beside each test the probes and runs that the record held before the plan,
with their questions. They judge whether the outcome was known, and a reviewer
who knows the outcome from a source or a table says so. A run whose
independence cannot be established is an observation.

For the current text of a statement, the harness lists every run whose plan
names a statement record with that statement digest, with the assessed status
of each failure signal: `observed`, `not_observed` or `unresolved`. It also
lists the runs on the earlier texts of the lineage, which the
`revises_statement_id` references of the statement records give. The author
does not enter this list, so a failed test stays with its text. A later run on
the current text makes a value record with accounts stale
(`problem_value_stale`).

The CLI reference gives the [fields of the declaration](research-cli.md#the-test-declaration-of-a-plan)
and the [rule of a check entered as a test](research-cli.md#the-accounts-of-a-value-record),
and the [math-conjecturer skill](../skills/math-conjecturer/SKILL.md#a-run-as-a-test-of-the-statement)
gives the steps.

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

With a selected plan, `status` exposes `limits.depth: {plan_id, profile,
stopping_criterion, values}`. Stores without a selected plan retain their
existing report shape. Depth changes need no constitution revalidation,
including in research studies retaining Version 6. A verification plan carries
`profile: verification`, a reason and a nonempty `stopping_criterion`, and
raises only `hits_per_query`, `search_readings` and `core_fulltexts`, never
below their defaults of 10, 20 and 10; the selection is kept per profile, so
a research plan never changes a verification search. The
[CLI reference](research-cli.md#depth-plan-depth) gives the rules.

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
