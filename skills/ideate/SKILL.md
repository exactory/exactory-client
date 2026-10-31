---
description: Use when a study needs candidate questions, a direction decision against the Q1 bar or an investigation plan at any stage.
---

# Ideate

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and follow the
[managed research workflow](../../docs/research-workflow.md) and
[scientific contribution decisions](../../docs/research-decisions.md). Start with current
`exactory-research status --summary` and `next --summary`. Ideation is a
[tool](../../docs/research-tools.md) usable at every stage. State its question,
candidate count, alternatives, evidence depth and resource allowance. Record
effective amounts, actual comparisons, gaps, stopping reason and cost. Search,
read, cohort and probe tools may inform it in any order. Tentative candidates
grant no direction approval. Managed preparation is advisory; the orchestration
holds the direction and investigation gates.

## Direction minimum and Q1 assessment

Before recording a version-2 `investigate` or `pivot` decision, complete the
direction minimum: a valid `direct` search bound to current scope, with bounded
or complete coverage and accounted results; full readings of every relevant or
contradictory hit and root; selected applicable `standards` with valid own
sources; and a current Grand Challenge record. Parser and capture failures
remain integrity gaps. A deferred decisive source remains a missing reading.
The [decision contract](../../docs/research-decisions.md#before-target-commitment)
lists each obligation code.

Two independent assessors first state the field and Q1 venues considered,
acceptable advance over closest prior work for the intent, evidence standard
and disqualifying conditions with the candidate withheld. Persist both bars
before slate delivery. Both reviewers must receive the decisive source content
and claim in their observed histories or bound supplements. Coverage requires
complete inspected ranges and every named image asset. Inventory membership,
reading notes and assignment dates alone supply no delivery coverage.
Missing content leaves `direction_evidence_unassessed`; qualifying historical
attachments retain coverage. The decision contract gives the exact delivery
and chunking rules. At recording, the
decision carries `gate_version: 2` and `direction_evidence_digest`; new decisive
readings require a new decision with reviews that saw them. Target commitment
applies the same minimum and review-coverage rule.

For `investigate`, `pivot` and `develop_manuscript`, both reviews pass
`q1_venue`: potential under the planned method and success criterion at the
slate, realized supported advance at results. Previous prompt assignments keep
their original assurance set, while a new commitment needs its required findings.
Historical decisions and approved tranches retain their standing.
`deliver_requested` keeps its stop checks, actual authority and quality conditions.

## Develop the next question

Read the source-grounded cohort doctrine, current synthesis, context, and retained
branch/checkpoint history. Develop candidate hypotheses within the full objective.
For each, state the bottleneck, proposed conceptual change, closest prior work,
expected contribution relative to the field's advance criterion, distinguishing
test, failure signal, and feasible resource demand. Include ambitious hypotheses
when their assumptions and tests can be made precise. A valid negative result can
resolve an important uncertainty; contribution is assessed from evidence.

Before an author-chosen target is committed, use the versioned intent and
prospective dossier to compare the proposed objective and approach with a
credible alternative and branch closure. For a user-fixed question, compare
methods or intermediate questions within that scope. Record evidence when only
one candidate is feasible. Obtain two independent consequence bars before
delivering the candidate slate, then separate objective-adequacy and
approach-adequacy findings under the verified reviewer protocol. Author advocacy
does not establish the bar. The `direction` gate combines configuration readiness
and the selected decision's obligations at the `cycle` boundary. Before a cycle
decision exists, it also lists the direction minimum obligations, so missing
evidence is visible during intake. Execution uses
its own prospective admission.

After each bar or slate `review-run`, inspect the observed output artifact for
`source_requests`. Use the
[native source-request continuation](../../docs/research-decisions.md#reviewer-delivery-and-disagreement)
in the same reviewer slot and supply unavailable exact sources through ordinary
acquisition. Inventory entries are metadata, not source reading. Keep unresolved
and unaffordable requests with their costs; do not record a request response as
a final `value-review` or release the slate before both final bars exist.

Inspect verification findings, failed checks, reviewer findings and unexpected
observations for research leads. Preserve each lead's original identity, source,
verification status and relation to the question; do not claim inherited knowledge
as a new result. For an unselected lead, record the reason and reopening condition.

In a development round, read the admitted goal in the current round admission
(`final/study/round-admissions/<admission id>.json`), the carried developments it
pursues, and the `downstream` and `next_step` records before forming hypotheses.
Every hypothesis of the round names the success criterion it serves. A successor
cycle may inherit a checkpoint of an earlier round. When the round widened the
objective, objective lineage keeps the earlier objective as an ancestor, so that
checkpoint can still be inherited. Currency follows the result's claim
dependencies. Unrelated reading, search, synthesis, configuration or constitution
changes leave the assessment current. An actual changed objective, assumption,
supporting artifact or material source impact requires affected reassessment.
When that changes the inherited result, assess it under the current claim,
checkpoint the assessment and inherit that checkpoint (`inherited_result_stale`
otherwise). Preserve historical digests and compare projections from recorded
inputs rather than current global preparation. The inheritance entry
retains the assumptions under which the result was assessed
(`inheritance_assumptions_missing` otherwise), and its deduction explains how the
result contributes to the round's objective.

Use captured primary-source evidence to test each consequential novelty claim.
Reuse a current verified finding when it still answers the same question; search
when the question, source coverage or evidence has changed. The literature-review
skill governs the refresh and source-delta declaration, including newly relevant
sources outside earlier bindings. Record
`nothing-new`, `scooped`, `replicate-extend`, `contradicted`, or `novel-confirmed`
with actual sources, scope, and unresolved gaps. Reframe a scooped claim or test a
contradiction explicitly. Keep basic science eligible when applications are unknown.

At an ai-science direction commitment, relate each selected hypothesis to a
criterion of the study's Grand Challenge record (`status` reports the current
record). Name those criterion ids and the
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

State the evidence-to-target relation as direct, logical, bounded, empirically
validated, conventional idealization or unestablished. A proxy fitting one
observable cannot distinguish mechanisms that fit that observable equally well.
Plan the independent observable or justified transfer test before treating the
proxy as a decisive test. A narrow result may have value at its supported level;
the independent assessment must establish that value without borrowing the
importance of an unresolved wider claim.

Choose the earliest affordable test that can change the research choice. A
necessary baseline has a named dependency, bounded completion condition and exit
into that test. Record an `investigate` decision with its named question, adequate
method or bounded adequacy probe, resource tranche, end/failure conditions and
linked work items. The tranche may contain dependent cycles. At its end, question
resolution or failure signal, reassess what was learned before admitting more
work. A new cycle ID or more precision on the same non-discriminating observable
does not renew an exhausted tranche. Charge all tranches to the same authorized
run and preserve its unresolved objective.

For critical work items, distinguish action disposition from actual execution.
At the next commitment, test an unresolved item, establish its specific
obstruction, or use it to justify a different route or branch closure. Relabeling
unattempted work as adopted, deferred or `next_round` supplies no evidence. If the
consequence has shrunk, explicitly reconsider whether it would justify starting
the study now, using current alternatives and without credit for sunk effort.

Planning and admitting a managed cycle require configuration readiness and the
current cycle decision. The complete synthesis report remains advisory. A quick
exploratory calculation can use the experiment tool's probe before commitment
or during planning; its evidence stays exploratory. Create the managed `cycle`
under the current decision before running its prospective test. Pin its actual program and inputs
with `artifact`, `admit` the exact plan with the resource reservation, then
`bind-run` its backend, seed, timeout, input paths, and expected outputs. Follow the
executable recipe in the workflow. Changes to these choices require a current
matching admission, including a fallback from unavailable compute. `cycle --check`
and `admit --check` report the first refusal, for example a scope refusal, without
recording. An accepted check is no recorded plan or admission: `admit` still needs
the recorded `cycle`, and the launch the recorded admission.

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
