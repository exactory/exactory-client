---
description: Use when a study needs an exploratory calculation at any stage, an admitted execution, result assessment or a decision before writing.
---

# Experiment

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and execute the
[managed research workflow](../../docs/research-workflow.md) and
[scientific contribution decisions](../../docs/research-decisions.md). Begin or resume with
`exactory-research status --summary` and `next --summary`. Keep the complete objective, admitted cycle,
resource account, advisory preparation, and exact output contract in view.
This is a [tool](../../docs/research-tools.md) available at every stage.
The caller states the question, depth and budget. Record effective amounts,
actual work, outcomes, stopping reason and cost. The orchestration holds the
gates that accept a direction or result; a tool record grants no approval.

## Probe before or during any milestone

Use `exactory-lab probe` for a quick calculation before choosing a theme, while
comparing approaches, or during a later stage. Configuration is required; a
target and admitted cycle may still be absent. State the exact question, program,
inputs and wall-time allowance:

```sh
exactory-lab probe --id example-001 --question "Does the small example satisfy the conjectured relation?" \
  --script experiment/check.py --input experiment/data.json --output-dir results --timeout 30
```

The [two-phase probe contract](../../docs/research-tools.md#probe) pins program
and inputs and records `probe-intent` before launch, reserving the timeout against
the `experiment` purpose. The observed `probe` outcome pins outputs, stdout and
stderr, records exit status and actual elapsed time, charges that usage and
releases the reservation, including failures, timeouts and output-bound outcomes.
Launch ownership is persisted and exclusive. The frozen `supervision.json`
binds worker/program identities, process group and deadline. An independent
watchdog enforces the deadline after worker loss even without a retry. It writes
`supervisor-timeout.json`, terminates the program group, verifies termination
and writes `supervisor-terminal.json`. A later retry records the outcome and
reconciles actual usage before releasing the reservation.

When the probe runs the program that a planned cycle will admit, disclose it:
add `--cycle-id` with the future cycle's id and `--program-path` with the
program's stable path in the workspace. Add `--read-output work/FILE`,
`--read-output stdout` or `--read-output stderr` for each output that you read.
Give no read path for a completion check: the harness then seals every output
and both streams unread, keeps their hashes, and never delivers their bytes.
Record the id of each disclosed run in the plan's `prior_knowledge`, with the
program, its path and an account of what was known. A plan that omits such a
run is refused with the run's id, and `admit` refuses a program other than the
one the plan froze.

Repeating an ID returns its outcome
or `pending` for a live owner. Reconcile an unfinished intent only after owner
loss or checked terminal evidence and confirmation that the original computation
has stopped; retain the reservation until reconciliation. Preserve measured
elapsed time and record unmeasured usage as unknown.

Cite probes in dossiers with their record references. Probe artifacts retain
exploratory origin through references and imports, with no prospective-test
credit. Existing supported imported evidence retains its standing. Independent
support assessment determines the additional validation a claim needs; repeat an
execution when that validation requires it and retain the prior chronology.
Computations relied on by recorded mathematical strategy decisions, proofs,
finite checks, counterexamples or acceptances require native admission.
Follow the [native provenance contract](../math-solver/SEARCH.md#computation-admission-and-mandatory-interpretation)
for imported exploratory output and `exploratory_evidence`.

## Plan and execute each cycle

Use preliminary implementation, baseline tuning, hypothesis tests, and ablation
where they answer the question. Choose repetitions, seeds, comparison methods,
and stopping conditions from noise, scientific requirements, and available
resources; no fixed number of cycles or seeds establishes validity.

Each cycle belongs to a current investigation decision and named resource tranche.
Run the earliest affordable test that can change the commitment. A necessary
baseline has a bounded completion condition and an exit into that deciding test.
Check whether the method can distinguish the hypotheses and whether its proxy
supports the target inference. A bounded adequacy probe may establish that link;
extra precision on a shared fit cannot establish it by itself.

For every admitted prospective run, write the `cycle`, pin actual program/input
bytes with `artifact`, `admit` the current plan and reservation, and `bind-run` the
exact backend, seed, timeout, paths, outputs, and usage. These are prerequisites
for debugging, tuning and plotting when they form part of that planned execution,
including a return from manuscript revision. Quick exploratory work uses the
probe contract above. Planning and admission require configuration and a current
cycle decision; the complete synthesis report remains advisory. Launch with the actual admission ID:

```sh
exactory-lab run code/program.py --admission run-001 --backend local --timeout 30 \
  --expected-revision 42 --request-id launch-cycle-001
```

The admitted `argv[0]` must resolve to the Python that runs `bind-run` and
`exactory-lab run`. For a venv interpreter, start both with it, where `VENV` is the
venv directory:

```sh
VENV/bin/python3 "$(command -v exactory-research)" bind-run --file run-binding.json \
  --expected-revision 41 --request-id bind-cycle-001
VENV/bin/python3 "$(command -v exactory-lab)" run code/program.py --admission run-001 \
  --backend local --timeout 30 --expected-revision 42 --request-id launch-cycle-001
```

The worker then starts that venv interpreter, so the program has the venv's packages.
The launch of a binding that exactory-client 0.47.0 or earlier recorded starts the
resolved file instead. To give the program the venv's packages, admit and bind a
new run.

Use the current revision and exact bound values, as described in the workflow.
`cycle`, `admit` and `bind-run` accept `--check`, which reports the first refusal
from one snapshot of the store and records nothing: an accepted check of `cycle`
is no plan, and `admit` needs the recorded plan. `exactory-lab run` has no check
mode, and `reconcile-run --check` is refused for a new request.
The worker executes a private copy of pinned inputs and seals the observed output
inventory. Metrics may be printed as JSON or written to the bound fallback path,
but a process exit or metric alone is not a scientific assessment. Read the saved
outputs and original observation before recording conclusions. Keep the human
`experiment/journal.jsonl` consistent with the authoritative execution history.

A `result` output takes any media type. Bind a figure, a PDF or another binary
file to a `result` requirement when the assessment is to cite it. A `validation`
output stays text or JSON; `bind-run` refuses another type for it. Cite a whole
binary output with `{"kind": "artifact"}` and a page of a PDF output with a
result `pdf` locator, as "Assess and deepen" shows.

Keep scripts and data within `experiment/`. Choose data, scale and methods adequate
for the question within authorized resources. Small public or synthetic data are
useful when they answer the deciding question or a named prerequisite. Set and record meaningful seeds,
versions, and null-seed reasons. A guard finding requires a code or design fix.
The local backend suits work that fits CPU or MPS. Bind Colab only for a live
runner and a justified test; if unavailable, prospectively admit an adequate local
test or preserve the unresolved resource requirement. Retrying the same admission
reconciles the existing execution and never launches it again.

When the outputs of an assessed cycle are needed in another format, admit a
format-only run instead of a successor: add `format_only_of` with the id of the
completed, assessed execution to the `admit` payload, then bind and launch as
usual. The harness records the run only when every output equals an earlier
output of the same requirement, byte for byte or as an equal strict JSON value
(`format_only_output_mismatch` otherwise). The earlier failures stay recorded,
the run charges the original strategy budget and tranche, and the converted
outputs need their own assessment. A figure redrawn in another image format,
such as PNG to SVG, is not covered by this check; the harness has no route for
it yet.

## Assess and deepen

Separate a valid negative result from invalid methodology, a code defect, timeout,
missing output, or unknown execution. Retain every outcome and its cost. Use
`reconcile-run` for interrupted observations; unknown released work retains its
reservation and cannot provide completed evidence. Assess the real result and
validity checks separately, with scope, novelty, contribution, assumptions,
failure signals, and remaining obligations for the full objective.

A result reference cites a whole output of any media type with
`{"kind": "artifact"}` as its locator, and a page of a PDF output with
`{"kind": "pdf", "page_index": 0, "printed_page": null, "region": [0, 0, 1, 1]}`,
where the page count comes from `pdfinfo` on the output bytes. The reference's
`artifact` must equal the output descriptor that the execution recorded. A
locator alone grants nothing: `validated_result` still follows from the
assessment's validity checks, which cite text or JSON `validation` outputs.

Save a durable `checkpoint` with its stable ID, exact claim, evidence, verification
status, unresolved obligations, and next hypothesis. Deepen promising branches
using prospective successor cycles and explicit inheritance. Preserve failed
branches and reopen them only when new evidence addresses the recorded obstruction.
A branch result does not complete the full objective by itself.

Link developments and reviewer requests to stable work items, retaining the
original request and evidence. Action disposition differs from execution status:
an adopted `next_round` step may remain `unattempted`. A validity gap blocks a
retained claim; a consequence-critical gap blocks the promised inference. Optional
development is compared by scientific value and opportunity cost.

When work exceeds current scope or resources, `next_round` records its schedule,
reason, evidence, resource implications, reopening condition and effect on claims.
It does not discharge a critical gap. At the next commitment, test that gap,
establish a concrete obstruction or failed bounded probe, or justify a different
route or branch closure. Feasible but unchosen work is not infeasible. Preserve
legacy `carried` dispositions and link them to the same work items.

At tranche end, question resolution or a failure signal, update the result dossier
with actual knowledge gained, failed approaches, realized or unmet consequences
and cumulative resource use. A new cycle ID cannot renew unchanged work without
a current decision. If the supported consequence is smaller, reconsider whether
it would justify starting the study now, comparing current alternatives without
credit for sunk effort. Preserve the full objective and every unresolved gap.

For scalar parameter optimization, use a noise-aware baseline and prospective
comparisons. Select the best supported source variant while retaining every
attempt, observation, failure, review, and resource charge. Restoring source code
never restores an earlier budget or erases the search history. Stop an optimization
phase according to its recorded scientific and resource conditions; an exhausted
budget is not evidence of success or impossibility.

## Combined result assessment before writing

After selecting an assessed checkpoint, extend the same strategy dossier with
the candidate, plans, sources, sealed observations, exact supported claims,
comparisons, failures, work-item execution and consequence status. No manuscript
is needed. Deliver the canonical result packet to two independent assessors on
verified reviewer routes. Each checks validity, scope, novelty, contribution
support and branch obligations before value in one response. Preserve unchanged
outputs and exact assignment/evidence bindings. The packet lists each run that a
probe disclosed before a plan under `program_probes` of its branch, with the
program diff against the admission and the read status of its outputs. An output
sealed unread reaches no reviewer. The canonical support and value
findings derive compatible readiness; do not add a separate full readiness-review
loop. Blind manuscript measurement later evaluates the paper separately.

Inspect each result `review-run` output artifact for `source_requests` before
recording final support or value findings. Apply the same rule to bar or slate
reviews when a changed intent requires them. Follow the [native continuation contract](../../docs/research-decisions.md#reviewer-delivery-and-disagreement)
in the same reviewer slot. Acquire unavailable exact sources through the ordinary
pipeline and retain pending or unaffordable requests as unresolved. A request
response is not a final `value-review`; both final bars must exist before the
slate is released. Reuse unaffected existing bars when the intent is unchanged.

Record the evidence-bound `research-decision` and run
`research-decision-assess --boundary write` with the current readiness gate.
`develop_manuscript` needs both supported claims and a worthwhile realized
consequence. Version-2 decisions require `q1_venue` passed in both reviews on
the realized supported advance and actual evidence. `deliver_requested` can use an existing unconditional delivery
instruction for a valid limited result while retaining an unmet bar and objective.
An explicit quality condition remains binding. Selected source-limited publication
contracts retain `gate manuscript-readiness`; whole-objective `gate readiness`
continues to report the original objective.

Address findings with source corrections, appropriate validity checks, or further
scoped cycles and affected independent reassessment. A material disagreement uses
focused adjudication with the objection retained. If value is still unmet, make a
justified investigate, pivot, close-branch or authorized-delivery decision; do not
manufacture success by narrowing the original objective. Enter `write` with
`--status pending` only when the current action and applicable gates permit it.

Prepare result summaries and plots with stated units, populations/denominators,
intervals, outcomes, baselines, uncertainty, and actual evidence references.
Record every reportable claim in `evidence/claims.json`, including unknown or
inapplicable dimensions with reasons. Check derived equations with
`exactory-derive check` where applicable and report the check's actual scope.

Report the supported result, comparisons, validity limits, failed approaches,
remaining objective, and resource usage. Resume from current status, retained
admissions, checkpoints, and the search tree without resetting history.
