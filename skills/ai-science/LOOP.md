# Research development and manuscript improvement

Read the [research constitution](../../RESEARCH_CONSTITUTION.md),
[managed workflow](../../docs/research-workflow.md) and
[scientific decision contract](../../docs/research-decisions.md). Managed author
research has one strategic decision lifecycle. Manuscript blind measurement is a
separate later assessment. Scores do not replace scientific support or sufficient
consequence, and a delivery receipt does not establish scientific success.

AI Science holds the direction, investigation, result, manuscript and publication
milestones. Use the [shared tools](../../docs/research-tools.md) at any point,
stating depth and budget and recording actual work. Tool order is free; each
claim's fixed minimum and independent assessment determine its gate. Probe
observations remain exploratory wherever cited. The preparation report in
managed `status` has `preparation.advisory: true` and `next` names the
milestone's missing condition. At intake, `direction` already lists its
minimum's missing evidence before a cycle decision exists.

## Develop research before writing

1. Read `status --summary`, `next --summary`, actual user intent, full objective,
   advisory preparation, retained checkpoints/failures, critical work items and
   remaining authorized resources. Historical completed work stays historical;
   apply the current policy at the next substantial commitment.
2. Before an author-chosen target is committed, prepare the proposed-content
   dossier. Two independent assessors first state worthwhile consequences from
   intent and verified field context, without the author's slate or value advocacy.
   The bar names the field, Q1 venues, acceptable advance over closest prior
   work, evidence standard and disqualifying conditions. Persist and reconcile
   those bars, then deliver the candidate slate. Assess
   objective adequacy and approach adequacy separately. Commit the exact approved
   target with its source delta; refresh materially affected findings. Recording
   a version-2 investigate/pivot decision and target commitment require the
   direction minimum: scoped direct search with bounded or complete coverage
   and accounted results, full readings of relevant/contradictory hits and
   roots, applicable standards with valid own sources, and the Grand Challenge
   record. Both reviewers receive complete inspected ranges and every named
   image asset, with checked source bytes and provenance. Observed deliveries
   in packets, continuations and supplements establish coverage; inventory,
   reading notes and assignment chronology alone do not. Missing content
   leaves `direction_evidence_unassessed`. Its evidence digest is
   fixed in the decision; new decisive readings need a new reviewed decision.
   Both slate reviews pass `q1_venue` on potential under the planned method.
3. Compare credible candidate approaches, the option of not pursuing the branch,
   and discrepancy leads from verifications, reviews or failed checks. Preserve
   prior findings as input knowledge. A fixed user question remains fixed.
4. Select the earliest affordable test that can change the commitment, including
   a transfer or deciding-assumption probe where needed. A baseline prerequisite
   has a bounded completion condition and an exit into that test. The investigate
   decision names the question, resource tranche, expected evidence and end/failure
   conditions. Several dependent cycles may share that tranche.
5. Plan a prospective `cycle`, pin actual inputs, admit and bind the exact execution
   contract, then launch that admission through `exactory-lab run`. Retain every
   outcome and cost. Inspect original observations before assessing result,
   validity, scope, assumptions, contribution and remaining obligations.
6. Checkpoint the evidence and next question. When the question resolves, a failure
   signal occurs or the tranche ends, assess what was achieved before another
   commitment. A new ID or restored source variant cannot reset history/resources.
7. Deliver the result dossier to two assessors through canonical assignments on
   verified context routes. Each answers support first and consequence second in
   one response. Preserve validity, scope, novelty, contribution-statement,
   development and branch findings. Derived readiness and the canonical decision
   consume those findings, including `q1_venue` on realized supported advance;
   do not request a second full strategic approval.

Every retained validity gap needs evidence or withdrawal of the claim. Every
consequence-critical gap stays open until resolved, even when its request is
adopted or deferred. Keep disposition separate from unattempted, planned,
attempted, validated, failed and blocked execution. Deferral states evidence,
resource implications, reopening trigger and effect on current claims. Feasible
but not chosen is distinct from infeasible.

If the consequence shrinks, independently reconsider whether that smaller result
would justify starting the study, against the original slate, later leads and
assessor alternatives, without credit for sunk effort. Preserve the old commitment
and critical gap. An unexpected valuable result can receive a new consequence
assessment without pretending the original objective was achieved.

The next action is investigate, develop manuscript, pivot, close branch or deliver
requested. Development needs current support and a sufficient realized consequence
with no unresolved material objection. A pivot retains results, failures and
complete claim dispositions, explaining relevant old obstructions. A branch can
close with a result/failure report before any manuscript exists. The full objective
remains open wherever it is unresolved.

## Independent assignments and objections

Use canonical role prompts, exact packets and actual invocation provenance.
Two fresh agent labels do not prove isolation. Verify the route/version against
startup context, automatic memory, prior scores and allowed evidence/tool access,
using excluded synthetic tokens and an allowed-input positive control. Keep
author-history isolation, prior-assessment isolation and identity masking separate.
An unverified or contaminated assignment cannot supply independent approval.

While a study is on autopilot, is not at stage `complete` and has no `waiting`
reason, its Stop hook asks every session that stops inside the study directory,
at any depth, to continue the study, and each stop it blocks with that request
is one advance toward the study's safety cap (`EXACTORY_AUTOPILOT_MAX`, default
50). At the cap, the hook blocks one more stop, to ask for a summary, and that
block is not an advance; the hook then lets the session stop until the next
user turn. The review routes of the harness start their reviewers with hooks
disabled, so the Stop hook never runs in them, wherever their working directory
is. A session that a study script or a person starts inside the study for one
assignment, such as a code review in `work/<name>/`, needs the environment
variable `EXACTORY_ASSIGNED_SESSION` set to any nonempty value when its CLI is
started: the hook then lets that session stop and leaves the advance count
unchanged. Setting the variable from a command inside the session does not
reach the hook. The session that coordinates the study runs without the
variable, because a coordinator started with it is never asked to continue.

Inspect observed `bar`, `slate` and `result` outputs for `source_requests` before
recording final value or support reviews. Follow the [native continuation contract](../../docs/research-decisions.md#reviewer-delivery-and-disagreement)
in the same reviewer slot, using ordinary acquisition for unavailable exact
sources. Preserve pending requests and unaffordable continuations as unresolved,
with actual costs; inventory access does not establish reading. Release the slate
only after both final independent bars have been recorded.

Every material objection receives disposition. A contested objection goes to a
fresh adjudicator with the original objection, both assessments and relevant
evidence in the canonical packet. It decides upheld, not upheld or unresolved
for that objection. Majority opinion cannot remove a valid counterexample.
Corrections need a demonstrable misreading or omission required by the original
packet contract; a further newly identified material error may be corrected with
admissibility evidence and cost. Rewording or resampling alone cannot reopen a
finding. Preserve failed calls, unfavorable answers and later context findings.

## Improve the manuscript

Use the evaluate rubric and write workspace's measurement protocol. Record any
user quality target, pacing and actual resource limit. An author stopping target
never becomes a desired reviewer output. The scientific result and communication
quality are assessed separately.

At manuscript measurement, obtain three independent blind reviews with paired
cohort predictions and keep their medians and spread. Give each the exact artifact,
prescribed evidence and cohort definition, without author advocacy, scores,
revision history, strategic decisions or another reviewer's answer. Use verified
context routes for these assignments. The two accepting current publication reviews
remain a separate requirement of the managed publication gate. Keep the pinned
working files as pinned until the three reviews and predictions are recorded:
`manuscript-review` and `manuscript-prediction` refuse a differing file with
`publication_artifact_changed`. `status` names such a file under `bundle_drift`
while the measurement is incomplete. `exactory-research restore --bundle DIGEST`
writes the pinned bytes back after it moves each current file aside and stores
its bytes as a backup artifact. Its output names both (write skill,
WORKSPACE.md).

For each substantive iteration:

1. Inspect new evidence, context, reviewer demands and the current decision.
   Refresh literature for an affected scientific question, reusing verified
   sources that remain adequate. Source novelty or an inference gap can invalidate
   affected current approval even if the manuscript wording did not change.
2. Connect every contribution request and cycle alternative to the same work items.
   Record the contribution analysis and dossier delta, preserving all original
   requests, reasons and execution status. A later-round label does not resolve
   a critical obligation.
3. Revise presentation directly when science stands. New evidence uses a current
   admitted cycle, actual assessment, checkpoint and renewed combined support/value
   findings before further manuscript development. Reconsider reduced consequence
   explicitly instead of declaring a narrower goal complete.
4. Preserve claim IDs and exact evidence mappings; record revised or withdrawn
   claims. Add citations through the registry command, compile and check citations
   and applicable derivations. Fix findings in code, evidence or claim scope.
5. Remeasure changed science, a diagnosed communication/packet repair, or an
   explicitly requested evaluation. Label unchanged science and retain earlier
   scores. Repeated cosmetic sampling is not a scientific improvement.
6. Select a scientifically supported version and retain every attempted revision,
   result, cost and review, including when restoring earlier source files.

Each predicting assessor has one review and one prediction on the exact bundle,
using the same normalized identity. The prediction copies the research scope's
first collection definition: corpus, primaryCategory as category, windowStart
and windowEnd. Exactly three distinct matched assessors complete measurement;
missing or ambiguous groups remain incomplete. Publication-only reviewers remain
outside it. See the evaluate skill for unchanged output and recording contracts.

## Develop across rounds

At post-measurement commitment, bind the exact bundle, completed measurement and
all reviewer-demand dispositions to the canonical research decision. Old `round`,
`round-review` and `round-admit` commands adapt this same decision and assessment.
Continue means an approved investigate/pivot with a next tranche and permits
`evaluate` to return to `literature`. Stop records
branch closure, manuscript preparation or an authorized limited delivery separately.
It does not authorize publication or complete the full objective by itself.

Keep the old impact, demand, novelty, feasibility, distinctness, continuity,
Grand Challenge and stopping assurances as named scientific findings. Reuse
`round-assess` as the factual assessment of the round's success/failure conditions,
not a second strategic approval. Preserve the stored admitted goal and its criteria
when directing cycles. A relevant prior failure must be resolved or avoided with
evidence before a route is reopened.

Claim currency follows exact objective, scope, assumptions, plans, executions,
evidence, cited sources, inheritance, validations, failures and authorizations.
Full dependencies remain recorded. Unrelated reading, synthesis, configuration
or constitution changes leave the claim current. Preserve historical digests and
validate them against recorded inputs before comparing claim projections.
Managed per-record history supplies the original input snapshots; unresolved
reconstruction reports `claim_reassessment_required`. Follow the
[upgrade procedure](../../docs/research-workflow.md#studies-in-progress). A
study may retain Version 6; research adoption of Version 7 alone changes no claim.
New direction and Q1 requirements apply at the next relevant commitment, and
pending executions reconcile once under frozen authority.

Refresh downstream, next-step, exemplar and changed-source questions when a
material scientific need arises. Retain applicable source/preparation obligations
without generating redundant searches solely for another receipt. Evidence of a
scooped or contradicted goal, a failure condition or exhausted tranche triggers
the next justified decision. A branch limit never resets the whole-run account
or ends a continuously authorized objective by itself.

## Publication and resume

Honor actual user authority. An unconditional instruction through deposit or
submission can cover `deliver_requested` for a valid limited result, with unmet
scientific goals retained, without another permission question or a forced change
of task kind. An explicit quality condition remains binding. Ask only about a
genuinely unresolved choice outside existing instructions, while continuing work
that does not depend on it.

Pin and review the exact publication bundle; use current publication/round
obligations and preserve confirmed receipts or any explicit managed-record refusal.
Reconcile an unknown remote outcome before another write. No receipt or score
converts an unmet scientific target into success.

On resume, read `status --summary`, `next --summary`, the named obligations,
current decision, checkpoint lineage, pending executions/reviews/remote intents
and user context; the harness shows the current decision, checkpoints and
recorded reviews in `final/` and the pending turns and runs in `intermediate/`
(STUDY.md). Preserve completed historical states, uncommitted work, original
evidence, failures and resource history. Repair exposed reviewer routes and
reassess affected current findings before consuming their approval. Continue
authorized independent work while a dependency is pending.
