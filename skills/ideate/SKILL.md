---
description: Develop a falsifiable hypothesis and prospective research cycle within the complete objective, using current literature, innovation studies, field doctrine, and human context. Use after the literature preparation gate and before experiment admission.
---

# Ideate

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and follow the
[managed research workflow](../../docs/research-workflow.md). Start with current
`exactory-research status --summary`, `next --summary`, and `gate preparation`.
The Grand Challenge record, the complete original objective, closed five-purpose
loop, field synthesis, and the five innovation cases are established in
[literature review](../literature-review/SKILL.md) before this stage. If
preparation is pending, complete its named obligations first.

## Develop the next question

Read the source-grounded cohort doctrine, current synthesis, context, and retained
branch/checkpoint history. Develop candidate hypotheses within the full objective.
For each, state the bottleneck, proposed conceptual change, closest prior work,
expected contribution relative to the field's advance criterion, distinguishing
test, failure signal, and feasible resource demand. Include ambitious hypotheses
when their assumptions and tests can be made precise. A valid negative result can
resolve an important uncertainty; contribution is assessed from evidence.

In a development round, read the admitted goal in the saved `round-admit` output
(`reviews/round-00N/admission.json`), the carried developments it pursues, and the
`downstream` and `next_step` records before forming hypotheses.
Every hypothesis of the round names the success criterion it serves. A successor
cycle may inherit a checkpoint of an earlier round. When the round widened the
objective, objective lineage keeps the earlier objective as an ancestor, so that
checkpoint can still be inherited. Its assessment is stale once the round changes
the objective or the preparation, and the round's literature stage changes the
preparation. To inherit the result as `validated_result`, assess that cycle again
under the current objective and preparation, checkpoint the new assessment, and
inherit that checkpoint (`inherited_result_stale` otherwise). The inheritance entry
retains the assumptions under which the result was assessed
(`inheritance_assumptions_missing` otherwise), and its deduction explains how the
result contributes to the round's objective.

Use fresh captured searches to test each consequential novelty claim. The
literature-review skill is part of this plugin and governs the refresh. Record
`nothing-new`, `scooped`, `replicate-extend`, `contradicted`, or `novel-confirmed`
with actual sources, scope, and unresolved gaps. Reframe a scooped claim or test a
contradiction explicitly. Keep basic science eligible when applications are unknown.

Every hypothesis advances at least one criterion of the study's Grand Challenge
record (`status` reports the current record). Name those criterion ids and the
established results the hypothesis follows from in `idea/idea.md`. Prefer the
hypothesis that moves the community furthest toward those challenges over the one
that is easiest to finish, when its assumptions and tests can be made precise.
Grand Challenges on exactory and recorded next steps show questions and demand;
they do not prove novelty, correctness, or authorship.

## Plan, admit, and hand off

Choose the hypothesis using the evidence and user's constraints. Record the choice
in `idea/idea.md` with the complete objective, exact branch scope, assumptions,
comparisons, prospective tests, metrics and validity checks, risks, and remaining
obligations. A special case contributes to the original objective without replacing
it. Link a successor to its predecessor checkpoint and inherited evidence; reopen
a failed branch only when new evidence addresses the recorded obstruction.

Create the managed `cycle` before running code. Pin its actual program and inputs
with `artifact`, `admit` the exact plan with the resource reservation, then
`bind-run` its backend, seed, timeout, input paths, and expected outputs. Follow the
executable recipe in the workflow. Changes to these choices require a current
matching admission, including a fallback from unavailable compute.

The draft layer may be initialized once the title and category are known with
`exactory-draft init`; this creates a pending layout and does not authorize writing.
Retain the explicit initialization request and original arguments for retries.
After recording the ideation decision, enter
`exactory-lab state set --stage experiment --status pending` only with a current
admitted cycle. The experiment skill launches that admission and assesses its
actual evidence before any research readiness review or writing transition.

Report the selected hypothesis, expected contribution, concrete test, resource
limits, and uncertainty. Continue with the best supported choice unless the user
asked to choose. On resume, inspect current gates and pending admissions before
planning additional work; preserve previous plans, failures, costs, and checkpoints.
