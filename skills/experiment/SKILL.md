---
description: Execute admitted research cycles, assess actual results and validity, preserve checkpoints and failed branches, and obtain independent research readiness review before writing. Use after current preparation and a prospective experiment admission.
---

# Experiment

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and execute the
[managed research workflow](../../docs/research-workflow.md). Begin or resume with
`exactory-research status --summary` and `next --summary`. Keep the complete objective, admitted cycle,
resource account, current preparation, and exact output contract in view.

## Plan and execute each cycle

Use preliminary implementation, baseline tuning, hypothesis tests, and ablation
where they answer the question. Choose repetitions, seeds, comparison methods,
and stopping conditions from noise, scientific requirements, and available
resources; no fixed number of cycles or seeds establishes validity.

Before every new launch, write the prospective `cycle`, pin actual program/input
bytes with `artifact`, `admit` the current plan and reservation, and `bind-run` the
exact backend, seed, timeout, paths, outputs, and usage. These are prerequisites
even for debugging, tuning, plotting that constitutes a planned execution, or a
return from manuscript revision. Launch with the actual admission ID:

```sh
exactory-lab run code/program.py --admission run-001 --backend local --timeout 30 \
  --expected-revision 42 --request-id launch-cycle-001
```

Use the current revision and exact bound values, as described in the workflow.
The worker executes a private copy of pinned inputs and seals the observed output
inventory. Metrics may be printed as JSON or written to the bound fallback path,
but a process exit or metric alone is not a scientific assessment. Read the saved
outputs and original observation before recording conclusions. Keep the human
`experiment/journal.jsonl` consistent with the authoritative execution history.

Keep scripts and data within `experiment/`. Use small public, synthetic, or built-in
data appropriate to the authorized resources. Set and record meaningful seeds,
versions, and null-seed reasons. A guard finding requires a code or design fix.
The local backend suits work that fits CPU or MPS. Bind Colab only for a live
runner and a justified test; if unavailable, prospectively admit an adequate local
test or preserve the unresolved resource requirement. Retrying the same admission
reconciles the existing execution and never launches it again.

## Assess and deepen

Separate a valid negative result from invalid methodology, a code defect, timeout,
missing output, or unknown execution. Retain every outcome and its cost. Use
`reconcile-run` for interrupted observations; unknown released work retains its
reservation and cannot provide completed evidence. Assess the real result and
validity checks separately, with scope, novelty, contribution, assumptions,
failure signals, and remaining obligations for the full objective.

Save a durable `checkpoint` with its stable ID, exact claim, evidence, verification
status, unresolved obligations, and next hypothesis. Deepen promising branches
using prospective successor cycles and explicit inheritance. Preserve failed
branches and reopen them only when new evidence addresses the recorded obstruction.
A branch result does not complete the full objective by itself.

For scalar parameter optimization, use a noise-aware baseline and prospective
comparisons. Select the best supported source variant while retaining every
attempt, observation, failure, review, and resource charge. Restoring source code
never restores an earlier budget or erases the search history. Stop an optimization
phase according to its recorded scientific and resource conditions; an exhausted
budget is not evidence of success or impossibility.

## Research readiness before writing

After assessing and selecting a checkpoint candidate, export a fresh readiness
delivery with `exactory-research export --kind readiness --destination PATH`.
Provide the independent assessor its actual candidate, plan, sources, results,
execution observations, and exact digest. Retain the real assessor provenance and
original assessment. Record `review` for that candidate and run the current whole
`exactory-research gate readiness`.

Address findings with source corrections, appropriate validity checks, or further
scoped cycles and renewed independent assessment. There is no cycle quota and no
automatic handoff because metrics exist. Once the current whole gate passes,
record the decision and enter `write` with `--status pending`. Later manuscript
reviews assess the actual paper separately and cannot substitute for this step.

Prepare result summaries and plots with stated units, populations/denominators,
intervals, outcomes, baselines, uncertainty, and actual evidence references.
Record every reportable claim in `evidence/claims.json`, including unknown or
inapplicable dimensions with reasons. Check derived equations with
`exactory-derive check` where applicable and report the check's actual scope.

Report the supported result, comparisons, validity limits, failed approaches,
remaining objective, and resource usage. Resume from current status, retained
admissions, checkpoints, and the search tree without resetting history.
