# Managed research workflow

Read the [research constitution](../RESEARCH_CONSTITUTION.md) before the stage
workflow. System, host, and user instructions govern the work. This workflow
connects the shared skills to the actual commands in the
[CLI reference](research-cli.md). JSON shapes come from
[the complete example catalog](research-cli-examples.json), also available with
`exactory-research example OPERATION`. Examples need actual acquired evidence,
source locations, identities, hashes, and assessments before use.

Managed author studies and drafts also follow the
[scientific decision lifecycle](research-decisions.md): independent bar and
candidate assessment before an author-chosen objective is committed, then one
combined support/value assessment at each result commitment. Native mathematical
search, standalone evaluation and external verification keep their own decision
contracts. A shared preparation profile alone does not add the author gates.
Verified reviewer context is required wherever independence or blindness is claimed.

Run commands from the study directory with the installed plugin's `bin/` on PATH
for each shell call. Keep payload files and human notes inside the workspace.
Every `REVISION` below means the current revision from `status`, read before that
new mutation. Give each new mutation a distinct request ID. An interrupted retry
uses its original ID, original payload, and original expected revision; a replay
receipt preserves history and does not authorize a later action.

## Initiate and resume

Create an author study once. Keep the initial target pending while literature
informs the prospective comparison of the objective and first approach.

```sh
exactory-lab init --dir study --slug study --expected-revision 0 --request-id initialize-study
exactory-lab init --dir screened-study --slug screened-study --preparation-policy screened-v1 --expected-revision 0 --request-id initialize-screened-study
```

The preparation policy is recorded at initialization. `exactory-lab init`
records the research default `lineage-v1` when `--preparation-policy` is absent,
and `exactory-draft init` always records it: it reads the lineage and the
classics in full, runs the bounded five-purpose loop below, and leaves every
other cohort member unread. A verification workspace records `sampled-v1`, which
reads a random sample of the population. The two legacy policies stay available on `exactory-lab init`:
`exhaustive-v1` reads every cohort member and every Tier 3 reference, and
`screened-v1` selects the preparation set through recorded screenings, audits,
and doctrine coverage as the cohort section describes. Change the recorded
policy later only with `policy` and a reason.

Change into `study`, read the user's context and constraints, and inspect the
authoritative state. Read these first when resuming any managed stage:

```sh
exactory-research status --summary
exactory-research next --summary
exactory-research obligations --code abstract_reading_missing --limit 20
```

The compact views are bounded advisory summaries of the same current evaluation;
`obligations` pages one obligation code at a time. Run the default `status` or
`next` only when the full nested report is needed. The common SQLite history is authoritative. JSON views, stage names, notes, and
old receipts describe work but do not replace a current gate. Retain existing
checkpoints, unsuccessful branches, original observations, costs, and user files.
Use `adopt` explicitly for legacy work; use `recover` only when the Store reports
that native rollback recovery is required. A crash does not initialize a new
study or reset an account. The original initialization request can repair an
interrupted layout without overwriting later state or user material.

Record a versioned `intent` with the original instruction, complete objective,
task kind, quality standard or `unspecified`, resources, artifact/publication
authority and current branch relationship. Use a proposed-content binding until
the native objective exists. Existing instructions through a named endpoint remain
effective; distinguish a limited authorized delivery from scientific success.
Only an actual explicit quality condition makes that condition a publication
prerequisite. Unknown resource limits are not zero or an invented whole-run cap.

Constitution version 6 changes the policy hash. Use the existing explicit
`constitution` revalidation flow for a new substantial commitment, preserving old policy and
completed historical states. Old strategic decisions remain `legacy_unassessed`.
An admitted active cycle may finish and retain its evidence; migration does not
rerun all old cycles or fabricate prospective assessments from hindsight.

## Cohort: enumerate the population and read its abstracts

Select the corpus and category from the field and context. The freeze command
computes a population definition, including the six complete calendar months
before publication. It does not enumerate or read members. For an undeposited
study use the current date, then reconcile the publication date at deposit.

```sh
exactory-lab state set --waiting none --stage cohort --status pending
exactory-cohort freeze --corpus arxiv --category cs.LG --published 2026-09-08 > cohort-definition.json
exactory-research example budget > budget.json
exactory-research budget --file budget.json --expected-revision REVISION --request-id set-budget-001
exactory-research example collect > collect.json
exactory-research collect --file collect.json --expected-revision REVISION --request-id collect-cohort-001
```

Set the preparation budget the user approved with `budget` before the costly
work; reservation and reconciliation are automatic, and exhaustion is a
checkpoint condition, never completion. Put the actual frozen definition into `collect.json`. Inspect the retained
collection/page receipts and resume its collection ID until enumeration is
complete. Under `exhaustive-v1` read every member's complete captured abstract,
including exact-version resolution where required; every other policy reads the
members the paragraphs below name. For each, author a `read` payload from actual
inspections and the seven source-grounded note fields. Set `depth: "abstract"`
and omit `bundle_id` for an abstract reading. Its inspection uses `unit_id: null`
and covers the whole saved abstract.

```sh
exactory-research batches --depth abstract --size 60 --destination cohort/batches
exactory-research example read-batch > notes-001.json
exactory-research read-batch --file notes-001.json --expected-revision REVISION --request-id read-batch-001
exactory-research example read > abstract-reading.json
exactory-research read --file abstract-reading.json --expected-revision REVISION --request-id read-abstract-001
exactory-research gate cohort
exactory-lab decide --stage cohort --decision "Enter literature" --why "The captured cohort is enumerated and every required abstract is read."
exactory-lab state set --stage literature --status pending
```

`batches` writes the unread abstracts as files for reader agents; each reader
returns one notes file and one coordinator records it with `read-batch`, which
derives the whole-abstract inspection and applies the single-reading rules to
every item. Repeat the reading mutations with distinct actual records for all
members before the gate. Under `lineage-v1` this plain export is refused with
`policy_inapplicable`: that policy reads no cohort member, and its abstracts come
out of the loop export below, `batches --loop`.

Under `lineage-v1` the population is enumerated but never read as a whole. Run
`collect` and `resume` until the collection is complete, or import an original
OAI traversal with `import-oai-cohort`; no member owes an abstract reading.
`gate cohort` passes while acquisition is still pending, because a pending
collection is reported as a notice instead of an obligation, and the incomplete
enumeration belongs in the study's recorded limitations. The stored abstracts
serve the loop as a local source: `population-query` ranks them by terms taken
from the target statement and the parent's title and abstract, and its matches
are handed to `batches --loop --candidates`, as the literature section shows.

Under `sampled-v1` the enumeration must complete before any reading: `sample`
refuses to draw while the collection is pending (`collection_pending`). Draw the
sample once per verification with `{id, collection_id, size, seed}`. The draw is
stratified by month with equal allocation, and it takes the whole population
when the population fits within `size`. The record keeps the seed beside the
drawn members: the draw repeats from that seed, and two verifiers of the same
paper choose their own seeds and read different members. A verification scopes
one frozen population: the sample names one `collection_id`, and any other
scoped collection reports `sample_missing`. A second draw against the unchanged
population is refused with `sample_exists`; a population that changed after the
draw reports `sample_stale`, which a new draw clears. `batches` then exports the
unread sampled members, and every one of those readings carries a `placement`
judgment. The verification section below holds the recipe.

Under `screened-v1`, screen every member first: `batches --screen` exports the
unscreened members, a screener judges each one (promote with reasons, doctrine,
exclude with no relevance, or pending), and `screen-batch` records the batch.
Read every promoted, doctrine and pending member; read the audit sample of
excluded members with an `audit` judgment; cover every month with doctrine
representatives. After two consecutive batches of pending members with no
consequential item, `screening-checkpoint` lets the remaining pending members
stay inventoried and unread. `policy-report` shows the preparation set and,
against an adjudicated reference file, its recall.

```sh
exactory-research batches --depth abstract --size 60 --destination cohort/screen --screen
exactory-research example screen-batch > screen-001.json
exactory-research screen-batch --file screen-001.json --expected-revision REVISION --request-id screen-batch-001
exactory-research example screening-checkpoint > saturation.json
exactory-research screening-checkpoint --file saturation.json --expected-revision REVISION --request-id saturation-001
exactory-research policy-report --policy screened-v1
```

Complete abstracts reveal field coverage; consequential claims and
doctrine from a paper require its applicable full reading in the next stage.
Handwritten completion flags are not reading evidence.

## Literature: build the source network and the full objective

Acquire one to five exact root paper families. Record `roots` with the actual
selected collection IDs. Follow captured bibliography occurrences through the
three-tier network: a root is Tier 1, read in full with its complete bibliography;
its references are Tier 3, read at abstract depth, until the author selects a
reference with `require-fulltext` for a major claim, novelty judgment, innovation
transfer, or validity decision. A selected reference is Tier 2 wherever it appears:
read in full with its complete bibliography, and its own references become Tier 3.
Few papers are read in full; every reference of those papers is still inventoried
and read at abstract depth. Preserve all occurrences and exact versions. A work
that a search judgment cites is required in full in the same way while that
judgment is the selected search of its purpose; recording a new search for the
purpose retires the requirements of the works it no longer cites.

Under `lineage-v1` and `sampled-v1` the Tier 3 abstract obligation is removed.
The bibliography of a full-read paper is still inventoried for identity, and it
creates no reading obligation. Name each entry that is read in full with
`require-fulltext`: purpose `lineage` for the parent's own line of results,
`classic` for the foundational papers that line rests on, `core` for a
verification's decisive papers, and `contradiction` for a source that opposes a
claim. The first three name the claim, lineage entry or finding they serve in
`depends_on`.

```sh
exactory-research acquire --file root-query.json --expected-revision REVISION --request-id acquire-root-001
exactory-research roots --file roots.json --expected-revision REVISION --request-id select-roots-001
exactory-research expand --file reference-query.json --expected-revision REVISION --request-id expand-reference-001
exactory-research fulltext --file fulltext-query.json --expected-revision REVISION --request-id capture-original-001
exactory-research bundle --file source-bundle.json --expected-revision REVISION --request-id inventory-original-001
exactory-research read --file full-reading.json --expected-revision REVISION --request-id read-original-001
```

Inventory each original body, its article boundaries and bibliography, figures,
tables, equations, proofs, appendices, and required supplements with `bundle`.
Inspect their actual text and visuals; acquire referenced visual assets with
`visual` when needed. A `fulltext` reading names that bundle and inspections for
all required units. A text extraction alone does not inspect a figure, and an
incomplete bibliography leaves expansion pending. Save unknowns and conflicts.

Conduct and record each search purpose separately against the current scope:

| Purpose | Question and resulting work |
| --- | --- |
| `direct` | Which work most closely addresses the stated question, and what remains open? |
| `originals` | Which original papers established the relevant concepts and results? |
| `theory` | Which mechanisms, assumptions, proofs, or models explain the proposed effect? |
| `adjacent` | Which neighboring fields offer a relevant mechanism or counterexample? |
| `recent` | Which current developments change the proposed claim or comparison? |

These are five purposes, independently required from the external innovation
papers below. Save the actual query, original response, enumeration,
source dates, found works, exact scope, judgment, and remaining gaps. Judge every
found work with a disposition (relevant, contradictory, potentially relevant, out
of scope, duplicate, unresolved) and a reason; a later search for the same purpose
carries its contradictory and unresolved findings forward or resolves them by name. Tool or web
responses can be retained through `import-response` with original JSON pointer
mappings. A `nothing-new` result still needs the captured search, including an
actual empty results array when that is what the search returned. Under
`lineage-v1` these five captures are stage 1 of the bounded loop described at
the end of this section.

```sh
exactory-research example search > direct-search.json
exactory-research search --file direct-search.json --expected-revision REVISION --request-id search-direct-001
exactory-research search --file originals-search.json --expected-revision REVISION --request-id search-originals-001
exactory-research search --file theory-search.json --expected-revision REVISION --request-id search-theory-001
exactory-research search --file adjacent-search.json --expected-revision REVISION --request-id search-adjacent-001
exactory-research search --file recent-search.json --expected-revision REVISION --request-id search-recent-001
exactory-research grand-challenge --file grand-challenge.json --expected-revision REVISION --request-id record-grand-challenge-001
```

Before the objective, investigate the challenges ahead of the research and record
them with `grand-challenge`: the ultimate goal the field is trying to reach and,
where useful, nearer large goals, each with criteria that would show it reached
and evidence read in full. Open Grand Challenges on exactory for the field are one
input. The record is not a synthesis section: it stays current for the whole
study, and a new record with a reason replaces it only when the direction
changes. `gate preparation` owes `grand_challenge_missing` until it exists.

Before `target` in managed author research, prepare the prospective `strategy`
dossier from the user's intent, available prior work, candidates and discrepancy
leads. Follow the [bar-before-slate delivery](research-decisions.md#before-target-commitment)
with two independent assessors on verified routes. Compare the proposed objective
and approach against the independently recorded worthwhile consequences. A fixed
user problem stays fixed; compare methods within its scope. This strategic approval
does not declare preparation complete or authorize an experiment.

At commitment, `target` consumes the approved exact statement/scope and records
its native objective link. Record the source/preparation delta since approval,
including newly relevant unbound sources. Material or unresolved impact requires
affected findings to refresh first; an unrelated metadata change does not require
a complete new review. The first result assessment checks that declaration.

```sh
exactory-research research-decision-assess --boundary target
exactory-research target --file complete-objective.json --expected-revision REVISION --request-id fix-complete-objective
```

Set the complete objective while still in `literature`, written against that
record and the prospective decision. A branch's
special case, restricted parameter range, or conditional hypothesis belongs in
its cycle scope and leaves the original objective intact. A fixed objective
changes only on the user's explicit instruction: save the user's own words in
`context/`, pin the file with `artifact`, and pass the pin's id as
`authorization` to `target`. Never write such an instruction yourself; the
harness checks the pinned bytes, not who wrote them, and one instruction
authorizes one change. The earlier objective stays retained as the predecessor,
and the synthesis and earlier assessments are recorded again under the new
objective. Develop the following
source-grounded synthesis before entering `ideate`:

- `standards`: retain the cohort doctrine, applicable field and article type,
  and venue when chosen. Link methodological, reporting, citation, and
  presentation expectations to saved sources; state unresolved applicability.
- `rationale`: And gives cited established context, But gives the cited
  consequential unresolved bottleneck, and Therefore proposes a response and
  distinguishing test. Distinguish hypotheses from observed results.
- `innovation`: under `lineage-v1`, read the abstracts of ten candidate papers,
  each registered with `innovation_candidate: true`, and study exactly five of
  them in full; the lineage supplies the within-field case. Under
  `exhaustive-v1` and `screened-v1`, study within-field innovations and five to
  ten distinct external original paper families in full. For each case, identify
  the original bottleneck, prior constraint, conceptual change, evidence, scope,
  and the proposed transfer's assumptions, limits, distinguishing test, and
  failure signal. Versions, aliases, and several case labels for one paper still
  count as one family. Separately date original results, later validation or
  improvement, and evidenced use or adoption. A shared case collection supplies
  candidates, not current approval.
- `context`: investigate beneficiaries, scientific capabilities, barriers,
  social connections, and uncertainty. Basic science is eligible when immediate
  applications are unknown. Preserve unknown or inapplicable quantities with
  reasons, including units, population/denominator, observation interval, outcome,
  and baseline; distinguish fitted values, external inputs, and derivations.

```sh
exactory-research standards --file standards.json --expected-revision REVISION --request-id assess-standards-001
exactory-research rationale --file rationale.json --expected-revision REVISION --request-id assess-rationale-001
exactory-research innovation --file innovation.json --expected-revision REVISION --request-id assess-innovation-001
exactory-research context --file scientific-context.json --expected-revision REVISION --request-id assess-context-001
exactory-research gate preparation
exactory-lab decide --stage literature --decision "Enter ideation" --why "Current literature and synthesis support the complete objective."
exactory-lab state set --stage ideate --status pending
```

### The five-purpose loop (lineage-v1)

Under `lineage-v1` the five purposes are covered by a bounded loop over recent
work rather than by a reading of the whole population. Stage 1 captures one
search per purpose, keeps the top 10 hits of each query, and adds two local
sources: a `population-query` over the enumerated population's stored abstracts,
and the papers that cite the parent within the window. A native registry
capture must enumerate completely, because a captured page that returns fewer
records than its reported total is `search_response_incomplete` and leaves the
search a `search_pending` obligation on the foundation: write each native query
narrow enough to return at most ten results in total, or import the results as a
mapped capture and acquire every kept hit with `acquire` before it is read. Read
every candidate abstract and register it as a loop entry that names its
purposes, its disposition and the source it came from (`search`, `population`,
`citing` or `author`).

```sh
exactory-research search --file direct-search.json --expected-revision REVISION --request-id search-direct-001   # at most 10 results, enumerated completely
exactory-research population-query --terms Haar purity "random state" --limit 30 > candidates.json
exactory-research batches --destination loop/round-1 --loop --candidates candidates.json
exactory-research read-batch --file loop/round-1/notes-001.json --expected-revision REVISION --request-id loop-read-001   # items carry "loop"
exactory-research loop-close --file loop-closure.json --expected-revision REVISION --request-id loop-close-001
exactory-research require-fulltext --file lineage-parent.json --expected-revision REVISION --request-id lineage-001   # purpose lineage, depends_on
```

A citing-papers capture is an OpenAlex query whose filter holds
`cites:<parent id>`. Save its original response with `import-response`
(provider `openalex`) and record it as a `recent` search response whose query is
the whole filter value: `cites:W123` for the filter `cites:W123`, and
`cites:W123,from_publication_date:2025-01-01` when the filter also holds a date
clause.

A purpose is covered when its question has an answer grounded in at least one
read paper judged `relevant` or `contradictory`, or when two distinct captured
queries for that purpose returned no relevant hit. A native capture counts by
the query it asks: its provider, endpoint and the parameters that decide which
works match (arXiv `search_query` and `id_list`; Crossref `query`, every field
query `query.<field>` and `filter`; OpenAlex `search`, `search.exact`,
`search.semantic`, `filter` and `corpus`). Its pages and repeated captures, and
captures that differ only in another parameter such as a sort order, a field
selection or a `mailto` address, count once. So do captures whose URLs spell one
query differently: the endpoint is compared by its host and its path without a
trailing slash, an empty parameter or list item counts as absent, and the
comma-separated items of a `filter` or an arXiv `id_list` are compared as a set,
because the registries combine them in any order. An OpenAlex `filter` is
compared as OpenAlex reads it: the letter case of its values does not count, and
the `|` alternatives of one filter, after a leading `!` that negates them all,
are compared as a set, so `type:Article` asks the same query as `type:article`,
and `cites:W2|W1` as `cites:W1|W2`. Two captures whose matching parameters
differ in any other way count apart, even when they share a query value; this
includes the name of a filter in another letter case, another letter case of the
value of a `.search` filter such as `title.search` (OpenAlex reads its Boolean
operators only in uppercase), and another letter case of the value of any other
matching parameter, such as `search`. `web` and `mcp`
captures of one query count once, and not at all when a native capture of the
purpose was bound to that query. The record of each purpose states which.
For each uncovered purpose, a new query is captured and up to 20 more abstracts
are read. The loop stops when every purpose is covered, when a
round adds no `relevant` or `contradictory` paper to any uncovered purpose, or
when the study has registered 100 abstract readings in the loop. At the limit,
the uncovered purposes are recorded as gaps with the queries tried. `loop-close`
records all five purposes at once, each `covered` or `gap`, and it keeps the
queries every gap rests on.

A registered abstract reading is one loop entry with a disposition. Stage-1
candidates count when their abstracts are read, which is all of them. The 100
limit counts registered readings across all rounds of the same study. When stage
1 yields more than 100 candidates, they are read in order of how many sources
returned them, and the remainder are recorded as unread candidates.

| Limit | Value | Enforced by |
| --- | --- | --- |
| Registered abstract readings in the loop | 100 per study | `read-batch` rejects the batch with `loop_limit_reached` |
| New loop readings while a development round is open | 40 per round | `read-batch` rejects the batch with `round_loop_limit_reached` |
| Abstracts per further loop round | 20 per uncovered purpose | the coordinator follows it; the store does not refuse the batch |
| Hits kept per captured query | 10, counted across every response of that query | `search` rejects the capture with `invalid_search` |
| Innovation candidates at abstract depth | 10 | `innovation` reports `innovation_candidates_missing` |
| External innovation cases read in full | exactly 5 | `innovation` reports `external_cases_insufficient` or `external_cases_excess` |

`status` reports the loop's registered readings, its limit and its covered
purposes under `limits.loop`, and the candidate count under
`limits.innovation_candidates`.

## Ideate and experiment: plan before launching

Choose a falsifiable hypothesis within the complete objective and the current
canonical decision. Reuse the strategy dossier to compare credible alternatives,
not pursuing the branch, and evidence-based discrepancy leads. Preserve original
verification status and do not count input findings as new discoveries. Record
the supported consequence, nearest prior result, assumptions, method adequacy,
transfer plan where a proxy serves a wider target, and resources. Use the earliest
affordable test that can change the commitment. A baseline prerequisite needs a
bounded exit into that deciding test.

An approved investigation names a question, authorized resource tranche, expected
evidence and end/failure conditions. Link its cycles to that decision. Resolution,
a failure signal or tranche exhaustion requires a new decision assessing what
the tranche actually established. A new cycle or tranche ID cannot reset resources
or erase the old question. Preserve full-objective obligations in `idea/idea.md`
and the managed records.

Write a prospective `cycle` with its exact scope, predecessor/checkpoint,
inheritance or reopening reason, distinguishing test, expected outcomes, failure
signals, evidence requirements, and resource limits. Plan `result` and
`validation` requirements for text or JSON outputs that an assessment can cite,
and declare figures and other binary files under a `log` requirement; `bind-run`
refuses an output bound to `result` or `validation` whose media type is not text
or JSON. Pin actual program and input bytes with `artifact`. Use the returned plan
digest and artifact references in `admit`; bind the exact backend,
interpreter/version, seed or null-seed reason, timeout, input paths, and declared
output/usage contract before a launch.

Use the CLI's [successor payload fields](research-cli.md#planning-a-successor)
for the populated `predecessor`, `inheritance`, and `reopening` objects. Retain
the checkpoint's actual assessment and evidence, inherited assumptions and
deduction, and the strategy's original failures, charges, and limits.

```sh
exactory-research cycle --file cycle.json --expected-revision REVISION --request-id plan-cycle-001
exactory-research research-decision-assess --boundary cycle
exactory-research artifact --file program-artifact.json --expected-revision REVISION --request-id pin-program-001
exactory-research admit --file admission.json --expected-revision REVISION --request-id admit-cycle-001
exactory-research bind-run --file run-binding.json --expected-revision REVISION --request-id bind-cycle-001
exactory-lab decide --stage ideate --decision "Run the admitted test" --why "The current plan distinguishes the hypothesis within its resource account."
exactory-lab state set --stage experiment --status pending
exactory-lab run code/program.py --admission run-001 --backend local --timeout 30 --expected-revision REVISION --request-id launch-cycle-001
```

Here `run-001` is the actual admission ID and every launch argument must match its
binding. A changed program, backend, seed, or contract needs its own current plan
and admission. The local worker executes a private copy of the pinned inputs and
seals observed outputs, streams, and the fallback metric's presence or absence.
Use the supported `colab` binding only for a live runner whose resources fit the
test; if unavailable, plan and admit a scientifically adequate local test or
record the unmet resource condition. An existing admission cannot be silently
rerouted.

`bind-run` and `exactory-lab run` check the admitted interpreter against the
Python that runs them: the admitted `argv[0]` must resolve to the same file, and
the admitted Python version must equal that Python's version. Both commands start
with the `python3` found on `PATH`; when that is another interpreter, they fail
with `unsupported_execution`. For an admission whose `argv[0]` is a venv
interpreter, start both commands with that interpreter, where `VENV` is the venv
directory:

```sh
VENV/bin/python3 "$(command -v exactory-research)" bind-run --file run-binding.json --expected-revision REVISION --request-id bind-cycle-001
VENV/bin/python3 "$(command -v exactory-lab)" run code/program.py --admission run-001 --backend local --timeout 30 --expected-revision REVISION --request-id launch-cycle-001
```

When the admitted `argv[0]` is a symbolic link, as the `bin/python3` of a venv
is, the worker starts that link itself, not the file it resolves to, so the
program imports the venv's packages. For a regular interpreter file, the binding
records its resolved path, as earlier releases did. The binding pins the resolved
interpreter file by its path and SHA-256. Changed interpreter bytes, a changed
Python version, or a link that now resolves to another file stop the launch with
`execution_runtime_changed` before the launcher claims the run. If the
interpreter changes after the claim, the worker refuses to start the program,
and the launch returns `execution_recovery_required`. Record that run with
`reconcile-run`, giving `resolution: "interrupted"` and a reason.

A binding of a venv interpreter that exactory-client 0.47.0 or earlier recorded
names the resolved file as its path, and its launch starts that file. Its program
therefore runs without the venv's packages. `bind-run` with the same payload
returns that binding. To run the program with the venv's packages, admit and bind
a new run. `admit` refuses a new admission of the same strategy with
`execution_pending` until the earlier run has an outcome.

Keep failed and timed-out executions and their usage. `reconcile-run` completes
interrupted observation using the same admission; unknown released work remains
pending with its reservation retained. One exception is a run that
exactory-client 0.47.0 or earlier claimed whose metric makes the observation
exceed the store bounds. For such a run, `reconcile-run` fails with
`invalid_input`, the run has an outcome but no observation, and its strategy
admits no further run. A retry of `exactory-lab run` with its original request
ID fails in the same way. An earlier release can also have observed such a run.
Then a new `reconcile-run` request, and that retry of `exactory-lab run`, fail
with `invalid_input` and change nothing if the metric nests more than 99 levels.
[Actual execution](research-cli.md#actual-execution) in the CLI reference gives
the bounds and a second exception for the seed of a run. A valid negative
finding differs from a broken implementation, missing output, or unknown
execution. An exit code or metric alone does not settle validity or the full
objective.

## Develop and review the research before writing

Read the actual outputs and checks. `assess` separately records the result,
validity evidence, scope, novelty/contribution, branch development, failures,
assumptions, and remaining obligations. A validity check marked `passed` cites
the completed output of a planned `validation` requirement; until that output
exists, record the check as `unresolved`. Preserve a durable `checkpoint` with a
stable ID and the next hypothesis. Deepen a promising branch from that checkpoint;
reopen a failed branch only when new evidence addresses its recorded obstruction.
There is no fixed cycle count. Resource exhaustion and incomplete evidence are
recorded conditions, not conclusions of success or impossibility.

Use the CLI's [unfinished assessment values](research-cli.md#assessing-unfinished-work)
when obligations remain. The top-level assessment and nested branch dispositions
have distinct allowed values. `development: null` preserves an unfinished
assessment and leaves its development obligation unmet.

```sh
exactory-research assess --file assessment.json --expected-revision REVISION --request-id assess-cycle-001
exactory-research checkpoint --file checkpoint.json --expected-revision REVISION --request-id checkpoint-cycle-001
exactory-research export --kind readiness --destination reviews/research-candidate-001
```

In managed author research, add the candidate's result memo and evidence to the
same strategy dossier. Include claims, failures, nearest-work comparisons,
committed consequences and unresolved work items. Deliver a canonical combined
result packet to two assessors using verified context routes. Each assesses
validity, scope, novelty, contribution-statement support, development and branches
before judging realized consequence in the same response. Retain original outputs
and actual invocation provenance. The validated support and value sections become
separate immutable findings; compatible readiness derives from them. Do not run a
second full readiness review after those combined responses.

Use the [decision reference](research-decisions.md) for canonical delivery,
objections, adjudication and exact command roles. A reviewer needs intended
source/result access and verified exclusion of author history and prior scores.
A clean export or fresh-agent label alone does not establish independence.

A command input nests at most 32 levels, so that later records and reviewer
exports can hold deeper copies of its values. A `review` payload cites each
evidence reference of the candidate one level deeper than `result.evidence` of
an assessment holds it, and a `value-review` holds such a support assessment in
its `support` field, one level deeper again. So an assessment whose deepest
value in `result.evidence` is at level 31 leaves no room for its `value-review`,
and one at level 32 leaves no room for its `review`.

```sh
exactory-research value-review --file result-review-a.json --expected-revision REVISION --request-id value-review-a
exactory-research value-review --file result-review-b.json --expected-revision REVISION --request-id value-review-b
exactory-research research-decision --file result-decision.json --expected-revision REVISION --request-id decide-result-001
exactory-research research-decision-assess --boundary write
exactory-research gate readiness
```

The payloads reference actual assignment outputs and exact evidence bindings;
nonempty fields are not scientific approval. Use the applicable selected-scope
readiness view when the study has a source-limited contract. The full-objective
view continues to report that objective's remaining obligations.

Manuscript development requires both current support for retained claims and an
independently sufficient realized consequence. Missing support, an unresolved
material objection or a critical inference gap prevents promotion even when a
score is high. A true partial result may instead justify more investigation, a
pivot, branch closure with a result/failure report, or a delivery covered by the
user's instruction. Branch closure never requires manufacturing a manuscript and
never completes an unresolved full objective.

Track reviewer requests and scientific alternatives with stable work items.
Adopted, rejected or deferred are dispositions; unattempted, planned, attempted,
validated, failed and blocked are execution states. A `next_round` label does not
resolve a consequence-critical gap. Validity obligations require evidence or
withdrawal of the retained claim. Deferral records its evidence, resource reason,
reopening trigger and consequence for the current result; feasible but not chosen
is distinct from infeasible.

If the realized consequence shrinks, explicitly reassess whether this result
would justify starting the study without credit for sunk effort. Compare the
original slate, later leads and assessor alternatives with their actual status.
Preserve the original critical obligation even when the narrower result is worth
developing. A method pivot inherits claims and failures honestly; every relevant
old obstruction needs an explanation, and complete claim groups may share one
reasoned disposition.

An unconditional user instruction through writing, deposit or submission can
support `deliver_requested` with current validity and honest limits. Do not
require a second permission question or relabel open research as a specified
narrow task. An explicit quality condition remains binding and a new scope choice
outside existing authority needs the actual choice. Author-selected narrowing
alone cannot create that authority. Preserve the full objective and all remaining
scientific obligations after a limited delivery.

For an actual authorized objective change, retain the original user instruction
and use the native `target` authorization and lineage flow. Refresh affected
synthesis and dependent assessments; do not rewrite an earlier objective or pretend
old evidence was collected under the new scope. Neither a policy migration nor a
limited delivery requires all old cycles to be executed again.

## Manuscript assessment and publication

Draft from the reviewed evidence and field standards. Keep source support,
source-level contradiction, unsupported attribution, and unresolved access
separate from scientific proof, replication, or refutation. Every quantitative
claim keeps its conditions and evidence. Add registry-verified citations as used;
neither a citation count nor a page count establishes research quality.

Pin the exact PDF, abstract, bibliography, claim ledger, optional sources, and
claim-to-evidence mappings with `manuscript`. The pin also accounts for the
literature that the study used. Every work with a full-text reading, and every
work that a selected five-purpose search cites, is cited by the bibliography or
has a `citation_accounting` item with a bibliography key or a reason
(`citation_accounting_incomplete` lists the rest). `manuscript` also fails with
`invalid_input`, and changes nothing, when its payload or bundle is beyond a
bound. Its payload cites evidence one level deeper than `result.evidence` of an
assessment holds it. `manuscript` fails if that makes the payload nest more than
32 levels. It also fails when the bundle it builds holds a value that an earlier
release stored beyond the store bounds. One such value is the metric of a cited
run that exactory-client 0.47.0 or earlier claimed, nested more than 95 levels.
Another is a seed of more than 4300 digits in the admission of any cycle. An
earlier release stored such a seed only under an interpreter without a limit on
integer string conversion. [Actual execution](research-cli.md#actual-execution)
gives these cases. Export its actual bytes for two distinct independent blind
assessors. Save each unchanged original rubric JSON and assessor provenance,
then wrap those artifact references with `manuscript-review`. The manuscript
review is separate from the earlier research support/value assessment. Use the
shared canonical reviewer assignment and a verified route for manuscript reviews
too; clean exported files alone do not exclude automatic memory, startup context
or later tool exposure. Preserve contaminated returns without giving them
independent approval credit.

```sh
exactory-research manuscript --file manuscript.json --expected-revision REVISION --request-id pin-manuscript-001
exactory-research export --kind manuscript --destination reviews/manuscript-001
exactory-research manuscript-review --file manuscript-review-a.json --expected-revision REVISION --request-id record-manuscript-review-a
exactory-research manuscript-review --file manuscript-review-b.json --expected-revision REVISION --request-id record-manuscript-review-b
exactory-research gate publication
```

Managed publication uses the current exact bundle, two applicable accepting
reviews, current decision requirements and existing citation/remote receipt checks.
Changes invalidate affected current findings and require reassessment.
`exactory-draft deposit` and `exactory submit` run on the user's instruction.
Distinguish quality-conditioned publication from an unconditional named endpoint
and supported limited delivery. The deposit records the study's publication
receipt when the publication and round gates pass; the submit records the
submission receipt when that publication receipt names this exact bundle and the
submit names its record. Otherwise they create the record or request directly,
with one `Managed record skipped` line when a store refuses the receipt.
Reconcile an unknown remote intent before any new managed write.

## Develop the paper across rounds

The paper develops across rounds. Every blind measurement reviewer returns the
rubric core and, in a separate file, a cohort prediction with its reasons. Give
each reviewer the study's cohort with the packet: the `corpus`, the
`primaryCategory` as `category`, the `windowStart` and the `windowEnd` of the
research scope's first collection. Record each prediction with
`manuscript-prediction`, adding `id`, the exact `bundle_digest`, `blind: true`
and the reviewer's `assessor`. Scores and predictions are results of the round,
not criteria. Then read `gate round`: it names what the closing round still owes
and, once nothing is owed, asks for the decision.

Use the same assessor identity for each review and its prediction on the bundle.
`round.measurement.complete` reports whether exactly three distinct prediction
assessors each have a review. A bundle takes three predicting assessors, so a
complete measurement stays complete; an incomplete group, or an ambiguous one
recorded before this rule, reports counts and null medians.
Publication-only reviewers remain outside the measurement.

### After each measurement: the contribution analysis

Retain the contribution analysis and every original reviewer request. Link
reviewer changes, cycle alternatives and proposed steps to stable work items.
Their adopted/rejected/deferred dispositions never substitute for execution
status. Classify validity, consequence-critical and optional work independently;
record evidence, resource implications and a reopening trigger for a deferral.

Inspect where the measured result stands against the Grand Challenge and nearest
work. Name any scientific question requiring a source refresh and preserve its
actual query/response and reading evidence. Reuse verified sources and findings
that still answer the question; another analysis alone is not a reason to repeat
a search. Newly decisive evidence receives current preparation and impact
assessment even when that invalidates an old approval. Do not hide an unread or
contradictory source merely to keep the measured state unchanged.

Update the dossier with the exact bundle, completed measurement, all demands and
any new critical inference gaps. Compare next actions through the same independent
support/value decision. A step may inherit a result, failed route, tested assumption
or shared objective. Explain relevant prior obstructions and preserve complete
claim dispositions; do not fabricate a positive claim deduction to permit a pivot.

```sh
exactory-research manuscript-prediction --file prediction.json --expected-revision REVISION --request-id predict-001
exactory-research contribution-analysis --file contribution-analysis.json --expected-revision REVISION --request-id analyze-contribution-001
exactory-research research-decision --file post-measurement-decision.json --expected-revision REVISION --request-id decide-measurement-001
exactory-research research-decision-assess --boundary round
exactory-research gate round
```

These commands consume actual completed review, work-item and dossier records;
use `example` and the CLI reference for the adapter payloads. Old `round`,
`round-review` and `round-admit` commands use the same canonical decision and
assessment, not a second strategic approval loop. Continue projects an approved
investigate/pivot action with a next tranche. Stop separately records whether
research on the branch ends, the result is ready for manuscript preparation, or
an authorized limited delivery applies. Ending a round alone authorizes neither
publication nor full-objective completion.

Preserve all seven old continue assurances: scientific consequence, real demand,
prior-work risk, adequacy and resources, material distinctness, evidence/failure
inheritance, and a justified relationship to the full objective and Grand
Challenge. The stop/demand assurances remain fair closure, remaining worthwhile
work, and evidence-based alternative/resource comparison. In particular, reject
an infeasibility claim contradicted by an available adequate test.

Within a round, refresh the downstream/next-step/exemplar/change questions where
new evidence or scope requires it and retain applicable preparation requirements.
Use the stored goal and its success/failure conditions to guide cycles. Keep old
claim identities and explicit revised or withdrawn scope; complete groups may
share a common reason when every member is enumerated. `round-assess` records
what the promised goal actually achieved, including failure. Reuse that factual
assessment in the next canonical decision.

A failed result, failure signal or exhausted tranche requires a new justified
choice. The same unresolved direction cannot be renewed merely under another ID.
Honor actual resource limits and retain unknown costs as unknown; a local tranche
limit does not authorize abandoning a continuously requested full objective.
An unavailable independent reviewer remains a specific operational dependency.
Preserve attempts and costs, repair the route, and continue independent research
that does not consume the missing approval.

## Verification and native mathematics

An external-paper verification uses a separate `verification` profile. Acquire
the exact metadata first in the fresh directory, then initialize that known
version with nullable main-body fields. Acquire and inspect the original body,
pin its source and hash with `target`, and set matching verification roots.
Complete the same network, reading, and applicable standards. Every policy
except `sampled-v1` also requires the five search purposes.
Keep historical prior art tied to the version that existed at the target's date.
Current work can explain a result without becoming earlier prior art. Author
innovation goals and contribution targets are not verification prerequisites.

After binding the task-only server response, use a probed actual reviewer route
and a `review-assignment` with role `verification`, `dossier_id: null`, and
`context.verification_task_id` equal to the bound task digest. `review-run`
returns `{verdict, checks}` for that exact paper and its prepared evidence.
Keep soundness, novelty and impact separate. `bind-verdict` requires
`assessment.assignment_id`, the same actual assessor and the unchanged returned
body and checks. Fresh context labels and manually imported reviewer text do not
establish verified independence. The [decision reference](research-decisions.md)
specifies route controls; it adds no author strategic gates to verification.

Under `sampled-v1` the verifier's population work is the sample. Draw it, read
every sampled abstract completely with a placement judgment, and read at most
ten core papers in full. The five search purposes are optional here: record one
for a finding that needs prior-art or contradiction evidence. A recorded search
still needs the current literature scope.

```sh
exactory-research sample --file sample.json --expected-revision REVISION --request-id draw-sample-001
exactory-research batches --depth abstract --size 50 --destination cohort/sampled
exactory-research read-batch --file cohort/sampled/notes-001.json --expected-revision REVISION --request-id read-sample-001   # items carry "placement"
exactory-research require-fulltext --file core-reference.json --expected-revision REVISION --request-id core-001   # purpose core, depends_on
exactory-research bind-verdict --file verdict-assessment.json --expected-revision REVISION --request-id assess-verdict-001
```

A placement states whether the target ranks `above` or `below` the sampled
member, or that it is `unplaced` against that member, with a reason. Core papers
are the papers whose full text decides the verdict's stance or the prediction's
band; they are chosen in this order until ten are reached: references of the
target that a finding relies on; sampled members the target could not be placed
against, or was placed below; hits of a targeted search judged `relevant` or
`contradictory`. An eleventh requirement is refused with `core_limit_reached`.
Prior-art evidence for a specific finding comes from a targeted search: each
captured query keeps its top 10 hits, and a verification registers at most 20
abstract readings from search hits, each carrying `search_hit: true`
(`search_reading_limit_reached` beyond that). A native capture also enumerates
completely, so write the query narrow enough to return at most ten results in
total, or import the results as a mapped capture and acquire each kept hit with
`acquire` before it is read.

`status` reports the percentile, the band and the placement counts under
`limits.sample`. The verdict body carries that percentile and band, and
`bind-verdict` refuses `prediction_mismatch` when the sample places no member,
when the body states a different percentile, when its band does not contain the
sample band, or, when more than 20 sampled members are unplaced, when its band is
no wider than the sample band.

`exactory verify` sends a verdict from any directory; a verification workspace that
bound the verdict with `task --bind` and `bind-verdict` also records its receipt.
The [verification skill](../skills/verify/SKILL.md) states the direct procedure.
`requestedByViewer` identifies the request opener and proves no authorship.

For a native mathematical attack, complete current common preparation first,
then export `--kind native` for independent review of the actual schema-3
proposal. Preserve the native original claim, novelty and strategy studies,
proof checks, budgets, accounts, failed-method history, and checkpoint lineage.
Follow the [math skill](../skills/math-solver/SKILL.md) and
[native CLI contract](research-cli.md#native-math-preparation); common readiness
never replaces mathematical acceptance. Historical replay remains available,
while fresh work needs a current reviewed foundation and applicable amendments.

The native preparation path retains its independent `review` record:

```sh
exactory-research review --file native-review.json --expected-revision REVISION --request-id native-review-001
```

## Pending work and continuation

Distinguish unqueried, captured but unread, unavailable original text, deferred
work, a pending retry deadline, and exhausted resources. Preserve each source
attempt and failure. Resume an eligible collection with its retained ID and new
request identity; retry an interrupted identical mutation with its original
identity. A noncritical availability qualification requires the actual permitted
HTTP evidence and policy, and does not complete a reading: either captured terminal
origin failures, or, for the abstract of a non-arXiv work, complete registry captures
from every registry that addresses its identifiers (or the saved import of a `url:` work) with no abstract in any of them. Critical sources stay
pending until the actual dependency is resolved.

On resume, read `status --summary`, `next --summary`, the `obligations` page for
the code in hand, the search tree and relevant checkpoint records, and the
current gate for the intended next action; run the default `status` only when
the full nested report is needed. Continue independent work
within the remaining authorized resources. When a required condition prevents
progress, record its exact obligation, preserved evidence, and next action.
