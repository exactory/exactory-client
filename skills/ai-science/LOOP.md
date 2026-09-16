# Research development and manuscript improvement

Read the [research constitution](../../RESEARCH_CONSTITUTION.md) and the
[managed research workflow](../../docs/research-workflow.md). Two distinct
assessments govern the work: research readiness before writing, and independent
review of the exact manuscript afterward. Scores do not replace either gate.

## Develop the research before writing

1. Read current `status --summary`, `next --summary`, the complete objective, source-grounded
   synthesis, user context, checkpoint lineage, failures, and remaining resources.
2. Form the next hypothesis within an explicit scope. Link inherited evidence
   and its assumptions. Revisit a failed method only when new evidence addresses
   the recorded obstruction.
3. Plan a prospective `cycle` and its distinguishing test, validity checks,
   expected outcomes, failure signals, and resource limits. Pin actual inputs,
   admit the current plan, bind the exact execution contract, and launch that
   admission through `exactory-lab run`.
4. Inspect the original observation and actual outputs. Assess result, validity,
   scope, novelty/contribution, assumptions, and remaining obligations separately.
   Preserve negative, invalid, missing, and unknown outcomes and every cost.
5. Save a durable checkpoint and next hypothesis. Deepen promising branches and
   return verified findings to the full objective with explicit deductions.
6. Export the actual assessed candidate for independent readiness review. Retain
   original assessor provenance and assessment, record the review for its exact
   digest, and run the current whole `gate readiness`.

Continue substantive cycles when the evidence or review requires them and
resources permit. There is no fixed cycle count. A checkpoint or a complete form
does not certify a result. An exhausted budget, missing critical source, unknown
execution, or unavailable independent reviewer stays a specific pending condition.
Enter writing only after the current whole readiness gate passes.

## Improve the manuscript

Use the evaluate skill's rubric and the write workspace's measurement protocol.
Record any user target, pacing, or iteration budget. A score target is a stopping
condition for manuscript improvement, never an instruction to a reviewer.

At the first manuscript evaluation, measure with three independent blind reviews
and keep their median and spread. The reviewers receive the actual manuscript
and prescribed evidence, without author expectations, prior scores, or revision
history. These measurements are separate from the two accepting current reviews
required for publication.

For each iteration:

1. Read the current obligations, source narrative, doctrine, prior learnings, and
   new context. Record whether the next question changes and why.
2. Refresh affected literature and synthesis before a new claim or reframing.
   Source, scope, or policy changes require current preparation and dependent
   reassessment.
3. Select the most consequential supported weakness. Revise presentation directly
   when the evidence stands. An evidence gap returns through the supported state
   transition to a prospective admitted cycle, actual assessment, checkpoint, and
   renewed independent research readiness before rewriting.
4. Keep claims, quantitative conditions, and evidence mappings consistent. Add
   references through the registry command, compile, check citations, and check
   applicable derivations. Resolve findings in sources or code, never by editing
   gate reports.
5. Preserve a source checkpoint and record the predicted score separately from
   the reviewer delivery. Obtain three fresh blind reviews, save their unchanged
   JSON, and record the actual median, spread, and reasons for any difference.
6. Select the better supported manuscript version using scientific accuracy and
   review evidence. A higher score cannot justify a false claim. If restoring an
   earlier source variant, make that limited source change explicitly and retain
   every experiment, review, failed revision, cost, and decision.
7. Append the learning and score records with exact source and review identifiers
   and the next unresolved question.

A small gain inside review variation is not established improvement. End the
manuscript optimization phase when the stated target or resource condition holds,
or evidence shows saturation and there is no substantive justified next revision.
Record the reason. Saturation does not authorize publication; the user's
instruction does, and pending research or manuscript gates still name what the
study owes.

## Develop the paper across rounds

The study produces one paper and develops it across rounds. Each round starts
from the paper as it stands and ends with a decision on the exact bundle. Scores
and predictions are recorded results of a round and never criteria: no gate rule
and no goal reads them.

At every measurement, each of the three blind reviewers returns the rubric core
and, in a separate file, a cohort prediction in the market's shape with its
reasons. Give each reviewer the study's cohort with the packet: `corpus`,
`primaryCategory` as `category`, `windowStart` and `windowEnd` from the definition
of the research scope's first collection. The reviewer copies them into the
prediction. Record each prediction file with `manuscript-prediction` for the
exact bundle: keep its `prediction` and `reasons` unchanged and add `id`,
`bundle_digest`, `blind: true` and the reviewer's `assessor`. Use the same assessor
identity for the review and prediction on that bundle. `status --summary` reports
the paired measurement under `round.measurement`. Its `complete` field is true
when exactly three distinct prediction assessors each have a review.
Duplicate or extra predictions make the group ambiguous. Incomplete or ambiguous
groups report counts and null medians. Publication-only reviewers remain outside
this measurement. The evaluate skill states the recording procedure.

The round gate, at the end of `evaluate`:

1. Read `gate round`. It names what the closing round still owes: for a round of
   number 2 or more, its assessment on this bundle (`round-assess`), the four
   consequence searches, the exemplar, an assessed cycle, a new claim, and claims
   continuity.
2. Write the candidates from the carried developments (`next_round` dispositions
   of the closing round's cycle assessments), the review findings, the `context`
   and `innovation` sections, and the open Grand Challenges. Each candidate has a
   direction (`vertical` or `horizontal`), a disposition and evidence; a source
   cited as evidence needs a full reading of that source.
3. Choose with `round`. `continue` pursues exactly one candidate and states the
   goal: the direction, the contribution delta, the beneficiaries, the success
   criteria, the stop conditions, the continuity with the current paper, the
   route and the risks. `stop` lists at least one candidate, disposes of every
   candidate and carried development, and pursues none of them. Either decision
   disposes of every carried development of the closing round in `carried`, once
   each: `{assessment_id, kind, question or cycle_id, disposition, reason}`, where
   `kind` is `alternative` (named by its exact `question` text) or `branch`
   (named by its `cycle_id`) and `disposition` is `pursue`, `rejected` or
   `deferred`. A missing one is refused with `carried_development_missing`. A goal
   never repeats an earlier rejected candidate (`round_goal_repeated`), even with
   a `reopening`. It repeats an earlier round's goal only with a `reopening` that
   names that round (`round_goal_repeated` otherwise). The named round must have
   been assessed unsuccessful (`invalid_round` otherwise), and the reopening
   carries evidence the earlier decision did not (`round_reopening_unchanged`
   otherwise).
4. Deliver `export --kind round` to a fresh subagent that is not a cycle author.
   It returns the review JSON with one judgment per check; record it with
   `round-review`. The questions and the returned fields are in the evaluate
   skill's SKILL.md, section "The round review".
5. Admit an approved `continue` with `round-admit`. Save its output unchanged,
   for example as `reviews/round-00N/admission.json`. It carries the stored
   admission record under `result`, and while the round runs it is the only output
   that carries the admitted `goal` with its success criterion and stop condition
   ids (`status`, `gate round` and `export --kind round` do not report it). Log the decision with `exactory-lab decide`,
   and enter `literature` with `--status pending`. An approved `stop` enters
   `deposit` when the publication gate passes. When the study keeps a Git
   repository, tag the bundle of each round decision as `round-N`, where N is the
   number of the round that the decision closes.

Inside the round, the literature stage records a selected search for each
consequence purpose (`downstream`: who is blocked by what the paper does not yet
do; `next_step`: whether the next step was already taken; `exemplars`: how a
comparable first result was developed further; `changes`: what changed since
the previous round), one `require-fulltext` with purpose `exemplar` whose work
is read in full and analyzed as one more innovation case, and the re-recorded
`rationale`, `context` and `innovation` sections with the carried findings. The
exemplar case is `within_field` when it comes from the study's field; an
`external` exemplar counts toward the five to ten external cases, so replace an
older external case rather than exceed ten. The re-recorded `rationale` names in
`but` the bottleneck the goal addresses, and `context` names the round's
beneficiaries. The five foundation purposes keep their own freshness rules.

Before the round's candidate is selected, assess the earlier rounds' cycles again
under the current preparation, and list every retained cycle in the candidate
assessment's `development.branches` (an earlier cycle as `resolved` once its
reassessment validates it; `branch_disposition_missing` and
`branch_resolution_unverified` otherwise). Every hypothesis of the round names the
success criterion it serves. A successor inherits a checkpoint of an earlier round
as a validated result only after that cycle is assessed again and checkpointed
under the current objective and preparation; the ideate skill states the rule.

Claims continuity: the internal pinned ledger keeps every claim id of the round's opening
bundle. A changed claim carries `revised: {previous, reason}`, a withdrawn one
`superseded: {reason}`; a rewritten claim without a marker is dropped
(`round_claims_dropped`), and the round adds at least one new claim
(`round_claim_missing`). Both are round-gate obligations, so the manuscript loop
can still measure an intermediate draft.
The manuscript export gives blind reviewers a separate current-claims file.
The original ledger remains available to the round assessor.

The loop ends only through a recorded exit. An approved `stop` leads to the
deposit, and the deposit runs on the user's instruction: `exactory-draft deposit`
records the study's publication receipt when the publication and round gates
pass, and otherwise creates the record anyway, with one `Managed record skipped`
line when a store refuses the receipt. The `deposit` stage stays open until a
receipt exists.
A `scooped` verdict in `next_step` or a `contradicted` one in `changes`
withdraws the goal: the round is assessed unsuccessful with the withdrawal as
evidence, and the next decision proposes a distinct goal or stops. After an
observed stop condition in `round-assess`, the next decision also proposes a
distinct goal or stops. When the two most recent rounds were both unsuccessful, a
goal in the direction of either round is refused (`round_direction_exhausted`)
unless it reopens one of those two rounds with changed evidence. After two
unsuccessful vertical rounds, a horizontal goal still continues. When the two
rounds took different directions, both directions are exhausted: only a reopening
of one of those two rounds with changed evidence continues; otherwise stop. A
recorded `development` budget at its limit is `resource_budget_exhausted`:
checkpoint and stop, or the user raises the budget with a reason. Purpose,
experiment and autopilot budgets inside a round keep their existing behavior, and
an unavailable round assessor leaves `round_review_missing` pending. A round spent
on presentation alone is recorded unproductive and counts as an unsuccessful
round.

## Publication and resume

Pin the final exact bundle, deliver it to two independent blind assessors, record
the original reviews and provenance, and run `exactory-research gate publication`.
The `deposit` stage opens when the publication gate passes and `gate round`
reports an approved `stop` decision for that bundle. Resolve a rejection with appropriate
changes and fresh applicable reviews. The deposit and the submission run on the user's instruction:
`exactory-draft deposit` records the publication receipt when these gates pass,
and `exactory submit` records the submission receipt when that publication
receipt names this exact bundle and the submit names its record. A command that
records no receipt creates the record or the request anyway, with one
`Managed record skipped` line when a store refuses the receipt. Each command
needs its own credential. The managed deposit settles an unknown earlier outcome
against the Zenodo record before it sends that step again.

On resume, read authoritative `status --summary` and `next --summary`, the search tree and checkpoint
records, pending admissions or intents, review history, source state, and context.
Read an active round's goal from the saved `round-admit` output.
Inspect unfinished work and reconcile actual operations. Preserve uncommitted
source changes, user material, immutable evidence, failures, and account history.
Continue the recorded next question without resetting a study or a budget.
