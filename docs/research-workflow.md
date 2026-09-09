# Managed research workflow

Read the [research constitution](../RESEARCH_CONSTITUTION.md) before the stage
workflow. System, host, and user instructions govern the work. This workflow
connects the shared skills to the actual commands in the
[CLI reference](research-cli.md). JSON shapes come from
[the complete example catalog](research-cli-examples.json), also available with
`exactory-research example OPERATION`. Examples need actual acquired evidence,
source locations, identities, hashes, and assessments before use.

Run commands from the study directory with the installed plugin's `bin/` on PATH
for each shell call. Keep payload files and human notes inside the workspace.
Every `REVISION` below means the current revision from `status`, read before that
new mutation. Give each new mutation a distinct request ID. An interrupted retry
uses its original ID, original payload, and original expected revision; a replay
receipt preserves history and does not authorize a later action.

## Initiate and resume

Create an author study once. The initial target is pending so that literature can
establish the complete objective before ideation.

```sh
exactory-lab init --dir study --slug study --expected-revision 0 --request-id initialize-study
```

Change into `study`, read the user's context and constraints, and inspect the
authoritative state. Read these first when resuming any managed stage:

```sh
exactory-research status
exactory-research next
```

The common SQLite history is authoritative. JSON views, stage names, notes, and
old receipts describe work but do not replace a current gate. Retain existing
checkpoints, unsuccessful branches, original observations, costs, and user files.
Use `adopt` explicitly for legacy work; use `recover` only when the Store reports
that native rollback recovery is required. A crash does not initialize a new
study or reset an account. The original initialization request can repair an
interrupted layout without overwriting later state or user material.

## Cohort: enumerate and read every abstract

Select the corpus and category from the field and context. The freeze command
computes a population definition, including the six complete calendar months
before publication. It does not enumerate or read members. For an undeposited
study use the current date, then reconcile the publication date at deposit.

```sh
exactory-lab state set --waiting none --stage cohort --status pending
exactory-cohort freeze --corpus arxiv --category cs.LG --published 2026-09-08 > cohort-definition.json
exactory-research example collect > collect.json
exactory-research collect --file collect.json --expected-revision REVISION --request-id collect-cohort-001
```

Put the actual frozen definition into `collect.json`. Inspect the retained
collection/page receipts and resume its collection ID until enumeration is
complete. Read every member's complete captured abstract, including exact-version
resolution where required. For each, author a `read` payload from actual
inspections and the seven source-grounded note fields. Set `depth: "abstract"`
and omit `bundle_id` for an abstract reading. Its inspection uses `unit_id: null`
and covers the whole saved abstract.

```sh
exactory-research example read > abstract-reading.json
exactory-research read --file abstract-reading.json --expected-revision REVISION --request-id read-abstract-001
exactory-research gate cohort
exactory-lab decide --stage cohort --decision "Enter literature" --why "The captured cohort is enumerated and every required abstract is read."
exactory-lab state set --stage literature --status pending
```

Repeat the reading mutation with distinct actual records for all members before
the gate. Complete abstracts reveal field coverage; consequential claims and
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
and read at abstract depth. Preserve all occurrences and exact versions.

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

These are five purposes, independently required from the five to ten external
innovation papers below. Save the actual query, original response, enumeration,
source dates, found works, exact scope, judgment, and remaining gaps. Tool or web
responses can be retained through `import-response` with original JSON pointer
mappings. A `nothing-new` result still needs the captured search, including an
actual empty results array when that is what the search returned.

```sh
exactory-research example search > direct-search.json
exactory-research search --file direct-search.json --expected-revision REVISION --request-id search-direct-001
exactory-research search --file originals-search.json --expected-revision REVISION --request-id search-originals-001
exactory-research search --file theory-search.json --expected-revision REVISION --request-id search-theory-001
exactory-research search --file adjacent-search.json --expected-revision REVISION --request-id search-adjacent-001
exactory-research search --file recent-search.json --expected-revision REVISION --request-id search-recent-001
exactory-research target --file complete-objective.json --expected-revision REVISION --request-id fix-complete-objective
```

Set the complete original objective while still in `literature`. A branch's
special case, restricted parameter range, or conditional hypothesis belongs in
its cycle scope and leaves the original objective intact. Develop the following
source-grounded synthesis before entering `ideate`:

- `standards`: retain the cohort doctrine, applicable field and article type,
  and venue when chosen. Link methodological, reporting, citation, and
  presentation expectations to saved sources; state unresolved applicability.
- `rationale`: And gives cited established context, But gives the cited
  consequential unresolved bottleneck, and Therefore proposes a response and
  distinguishing test. Distinguish hypotheses from observed results.
- `innovation`: study within-field innovations and five to ten distinct external
  original paper families in full. For each, identify the original bottleneck,
  prior constraint, conceptual change, evidence, scope, and the proposed transfer's
  assumptions, limits, distinguishing test, and failure signal. Versions, aliases,
  and several case labels for one paper still count as one family. Separately
  date original results, later validation or improvement, and evidenced use or
  adoption. A shared case collection supplies candidates, not current approval.
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

## Ideate and experiment: plan before launching

Choose a falsifiable hypothesis within the complete objective. Compare candidate
strategies using the actual novelty search, field advance criteria, available
compute, and remaining resources. Preserve the complete objective and explicit
remaining obligations in `idea/idea.md` and the managed records.

Write a prospective `cycle` with its exact scope, predecessor/checkpoint,
inheritance or reopening reason, distinguishing test, expected outcomes, failure
signals, evidence requirements, and resource limits. Pin actual program and input
bytes with `artifact`. Use the returned plan digest and artifact references in
`admit`; bind the exact backend, interpreter/version, seed or null-seed reason,
timeout, input paths, and declared output/usage contract before a launch.

Use the CLI's [successor payload fields](research-cli.md#planning-a-successor)
for the populated `predecessor`, `inheritance`, and `reopening` objects. Retain
the checkpoint's actual assessment and evidence, inherited assumptions and
deduction, and the strategy's original failures, charges, and limits.

```sh
exactory-research cycle --file cycle.json --expected-revision REVISION --request-id plan-cycle-001
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

Keep failed and timed-out executions and their usage. `reconcile-run` completes
interrupted observation using the same admission; unknown released work remains
pending with its reservation retained. A valid negative finding differs from a
broken implementation, missing output, or unknown execution. An exit code or
metric alone does not settle validity or the full objective.

## Develop and review the research before writing

Read the actual outputs and checks. `assess` separately records the result,
validity evidence, scope, novelty/contribution, branch development, failures,
assumptions, and remaining obligations. Preserve a durable `checkpoint` with a
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

Deliver the entire fresh export directory to an independent assessor, including
`inputs.json`, its candidate and plan digests, actual source/output bytes, and
execution observations. The assessor evaluates the exact candidate's validity,
scope, novelty, contribution, development, and branch obligations. Retain the
original assessment and actual assessor provenance, then record `review` for that
candidate. An author cannot invent an independent review or treat identity
declarations as authenticated scientific judgment.

```sh
exactory-research artifact --file assessor-provenance.json --expected-revision REVISION --request-id pin-research-assessor-001
exactory-research review --file readiness-review.json --expected-revision REVISION --request-id review-research-candidate-001
exactory-research gate readiness
exactory-lab decide --stage experiment --decision "Draft the reviewed result" --why "The current whole research readiness gate passes for the selected candidate."
exactory-lab state set --stage write --status pending
```

Enter writing only after this current whole readiness gate passes. A checkpoint,
per-review `ready` value, earlier receipt, or plan for later manuscript review is
insufficient. Findings that require new evidence lead to a scoped successor cycle
and renewed review. A narrower verified result is reported with its contribution
to the original objective and the obligations it leaves open.

## Manuscript assessment and publication

Draft from the reviewed evidence and field standards. Keep source support,
source-level contradiction, unsupported attribution, and unresolved access
separate from scientific proof, replication, or refutation. Every quantitative
claim keeps its conditions and evidence. Add registry-verified citations as used;
neither a citation count nor a page count establishes research quality.

Pin the exact PDF, abstract, bibliography, claim ledger, optional sources, and
claim-to-evidence mappings with `manuscript`. Export its actual bytes for two
distinct independent blind assessors. Save each unchanged original rubric JSON
and assessor provenance, then wrap those artifact references with
`manuscript-review`. The manuscript review is separate from the earlier research
readiness assessment.

```sh
exactory-research manuscript --file manuscript.json --expected-revision REVISION --request-id pin-manuscript-001
exactory-research export --kind manuscript --destination reviews/manuscript-001
exactory-research manuscript-review --file manuscript-review-a.json --expected-revision REVISION --request-id record-manuscript-review-a
exactory-research manuscript-review --file manuscript-review-b.json --expected-revision REVISION --request-id record-manuscript-review-b
exactory-research gate publication
```

Publication uses the current exact bundle, two applicable accepting reviews, and
the existing citation/remote receipt checks. Changes invalidate affected current
decisions and require reassessment. Follow the deposit and submission skills
within the user's authorization. Reconcile an unknown remote intent before any
new write; an API credential does not itself authorize publication.

## Verification and native mathematics

An external-paper verification uses a separate `verification` profile. Acquire
the exact metadata first in the fresh directory, then initialize that known
version with nullable main-body fields. Acquire and inspect the original body,
pin its source and hash with `target`, and set matching verification roots.
Complete the same network, reading, five search purposes, and applicable standards.
Keep historical prior art tied to the version that existed at the target's date.
Current work can explain a result without becoming earlier prior art. Author
innovation goals and contribution targets are not verification prerequisites.
Use the [verification skill](../skills/verify/SKILL.md) and CLI's task/bind-verdict
flow for the independent exact-target assessment and original outgoing verdict.
`requestedByViewer` identifies the request opener and proves no authorship.

For a native mathematical attack, complete current common preparation first,
then export `--kind native` for independent review of the actual schema-3
proposal. Preserve the native original claim, novelty and strategy studies,
proof checks, budgets, accounts, failed-method history, and checkpoint lineage.
Follow the [math skill](../skills/math-solver/SKILL.md) and
[native CLI contract](research-cli.md#native-math-preparation); common readiness
never replaces mathematical acceptance. Historical replay remains available,
while fresh work needs a current reviewed foundation and applicable amendments.

## Pending work and continuation

Distinguish unqueried, captured but unread, unavailable original text, deferred
work, a pending retry deadline, and exhausted resources. Preserve each source
attempt and failure. Resume an eligible collection with its retained ID and new
request identity; retry an interrupted identical mutation with its original
identity. A noncritical availability qualification requires the actual permitted
HTTP evidence and policy, and does not complete a reading. Critical sources stay
pending until the actual dependency is resolved.

On resume, read `status`, `next`, the search tree and relevant checkpoint records,
and the current gate for the intended next action. Continue independent work
within the remaining authorized resources. When a required condition prevents
progress, record its exact obligation, preserved evidence, and next action.
