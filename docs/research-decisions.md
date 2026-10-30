# Scientific contribution decisions

Managed author research compares what a result would establish before committing
to it and what the evidence actually establishes before developing a manuscript.
This decision is separate from the user's publication authority and from whether
the full scientific objective has been achieved. Software checks preserve the
records and prerequisites; independent scientific judgment determines whether
the supported consequence is worth pursuing.

Read this guide before committing an author-chosen objective, opening another
investigation tranche, selecting a result for writing, or deciding a manuscript
round. Use the [tool catalog](research-tools.md) for freely ordered actions and
the [managed workflow](research-workflow.md) for milestone gates, source preparation,
execution and publication, and the [CLI reference](research-cli.md) and
`exactory-research example OPERATION` for the exact current payloads. Examples
require actual evidence, identities and digests before they can be recorded.

## Scope and authority

The decision lifecycle applies to managed study and draft research and to work
with an explicitly recorded research intent. A shared `research` preparation
profile alone does not activate it: native mathematical search keeps its own
proposal, review and acceptance rules. Standalone evaluation and external-paper
verification keep their existing contracts. Any claim of independent or blind
assessment still needs the reviewer-context controls below.

Record the user's actual instruction and its provenance in a versioned intent.
Keep the full objective, branch objective and remaining obligations separate.
The task kind is open research, bounded research or a specified delivery; the
author cannot create a narrow-delivery instruction merely by narrowing a claim.
An absent quality standard is recorded as unspecified, not invented as a user
requirement. Unknown resources differ from zero and do not establish an arbitrary
whole-run cap or permission for new paid spending.

Read existing instructions before asking anything. An unconditional instruction
to continue through a named deposit or submission endpoint already covers that
chain. A justified decision may deliver a valid limited result under that
instruction while retaining the unmet scientific bar and objective. The task
does not need to be reclassified as a specified delivery. If publication has an
explicit scientific quality condition, keep developing meaningful authorized
research while the condition remains unmet. Ask only about a genuinely unresolved
scope or resource choice, and continue independent work that does not depend on it.

## One decision across the research lifecycle

| Phase | Evidence and decision |
| --- | --- |
| Prospective | Assess both the proposed objective against the user's aim and the proposed approach before author-chosen target commitment. Use a proposed-content binding when no native objective exists. |
| Result | Assess the selected claims, evidence, failures and fulfilled or unresolved consequences before writing or opening a further tranche. No manuscript is required. |
| Post-measurement | Bind the exact manuscript bundle, completed measurement and all reviewer-demand dispositions. New critical findings return to the same decision state. |

The canonical decision separates scientific action, artifact disposition and
original-goal status. It references the exact intent/dossier, independent
assessments, evidence dependencies, resources and disposition of every material
objection. The available actions are:

| Action | Required basis |
| --- | --- |
| `investigate` | A worthwhile prospective consequence, a named question, adequate test or adequacy probe, bounded authorized resource tranche, and end/failure conditions. |
| `develop_manuscript` | Current support for the retained claims and an independently supported realized consequence sufficient for the recorded aim, with no unresolved material comparison or inference gap. |
| `pivot` | A distinct prospective route, its comparison with alternatives, inherited evidence and failures, and why relevant old obstructions no longer defeat it. |
| `close_branch` | Retained results or failed attempts, a justified stopping reason, feasible alternatives considered, and the unresolved full objective and next action. |
| `deliver_requested` | An existing instruction covering the supported artifact, including an unconditional named publication endpoint, with current validity and limited scientific contribution recorded. |

An optional contribution rating is diagnostic. It cannot authorize a transition.
An expected theorem, decisive negative result or important restricted result can
be worthwhile without surprise, commercial adoption or a numerical threshold.
Conversely, a new true bound may still leave the motivating question undecided.

## Before target commitment

The direction decision is a milestone of ai-science. Ideation, search, reading
and probes can inform it in any order. Before recording a new `investigate` or
`pivot` decision, satisfy this mechanical minimum:

| Evidence | Obligation codes |
| --- | --- |
| A selected valid `direct` search bound to the current literature scope, with explicit bounded or complete coverage, all captured results accounted by disposition, and no unresolved integrity error. | `direction_search_missing`, `direction_search_incomplete` |
| Every relevant or contradictory direct-search work read in full. A deferral retains the missing reading. | `direction_reading_missing` |
| Every root read in full. | `direction_root_reading_missing` |
| Selected `standards` with valid own sources and resolved applicability. Staleness by the foundation, the configuration or the constitution alone is ignored; a changed scope, evidence or population is not. | `direction_standards_missing`, `direction_standards_unresolved` |
| A current study Grand Challenge record. | `grand_challenge_missing` |

For historical searches without coverage fields, the gate uses their captured
response accounting and integrity checks. Preserve those captures and the
coverage they establish without repeating a search just to add a new label.

New decisions record `gate_version: 2`. Recording an `investigate` or `pivot`
decision while the minimum is unmet returns `direction_minimum_unmet`, with
the missing obligations. The evidence must exist before the reviews that judge
it. Both reviewers must receive the decisive source content and claim in their
observed packet histories, source-request continuations or bound supplements.
Coverage requires the complete inspected ranges and every image asset named by
the inspection. The harness checks the supplied bytes and their provenance
against the exact captured source. Long ranges are delivered in consecutive
chunks of at most 2,000 characters; PDF inspections deliver the checked original
within the 64 MiB per-artifact bound. Every chunk of the inspected range and each
named image must be present. A caption with an image descriptor leaves coverage
missing until the image bytes are delivered.

Reading-record equality, author notes, artifact descriptors, inventory membership
and assignment chronology alone supply no source-content coverage. A passage
marked `visuals_require_source_request: true` remains incomplete while required
visual bytes are missing. Missing ranges or assets leave
`direction_evidence_unassessed`. Qualifying historical source attachments and
checked passage deliveries retain coverage without rewriting packets or repeating
author reading. Assessment verifies delivery without creating source artifacts.

The decision stores `direction_evidence_digest`: the direct search ID, relevant
and contradictory versions with their full-reading IDs, roots with their
full-reading IDs, standards section ID and digest, and Grand Challenge record
ID. Readiness requires the minimum to hold with that same decisive evidence.
`direction_evidence_unassessed` requires a new decision with reviews that saw
new decisive evidence. Selecting a replacement standards section changes this
evidence even when its conclusions match the earlier section. Stamping a digest
when recording a decision supplies no evidence of what earlier reviewers saw.
Target commitment applies the same minimum
and review-coverage rule to its decision. Historical decisions keep their standing.

Create a prospective dossier from the available literature. It compares the
direction with a credible alternative and with not pursuing the branch. For a
fixed user question, compare methods or intermediate questions within that scope.
One feasible candidate needs evidence explaining why alternatives are unavailable.
Do not invent candidates to satisfy a count.

For each candidate, retain the exact question/target, relation to the full aim,
positive/negative/inconclusive consequences, nearest prior result, method
adequacy, decisive assumptions, resources, first test, success criterion and
failure signal. Keep the author's recommendation and value advocacy outside the
independent packet. Inspect verification findings, reviewer findings, failed
checks and unexpected observations for leads. A lead retains its original
identity, verification status and evidence; a prior finding is input knowledge,
not a discovery made again by this study. Record a reason and reopening condition
for an unselected lead.

Each `next_test.prerequisites` entry is an object identifying the necessary
preparatory work, its completion condition and the transition into the deciding
test. Use an empty array when no prerequisite is needed. For example:

```json
{
  "description": "Calibrate the control measurement.",
  "end_condition": "The known control is reproduced within the stated tolerance.",
  "exit_condition": "Run the discriminator after the calibration check passes."
}
```

A malformed candidate object produces a structured error with its JSON pointer,
expected type, received type and missing or unexpected fields. A path such as
`/candidates/0/next_test/prerequisites/0` identifies the entry to correct; an
invalid submission records no dossier or selection change.

Two assessors first receive the bar packet: the original intent, field question,
authorized resources and verified prior context, including held-source identities
and reading status. The complete inventory is bound to an immutable snapshot;
the packet includes its identity, total and first page, and assessors can request
other pages, matching entries or exact source content. Inventory entries do not
establish that a source was delivered or read. Initial `context.source_links`
selects exact previously inspected passages as prior context, using `text`,
`span` or `json` locators. Request visual originals through `source_requests`;
delivery alone does not establish visual inspection. Source and inspection checks
do not establish that the source's claims are true.
Withhold the proposed objective, candidate consequences,
methods, desired action, earlier scores and the other assessor's initial answer.
Each states the Q1 bar described below. Persist both
responses before either sees the other's bar. Reconcile alternative sufficient
consequences with reasons; a material dispute follows focused adjudication.
Tell the user once which independently derived bar guides an otherwise unspecified
open-research standard.

Only then deliver the slate to those reviewer sessions. Record separate findings
on objective adequacy and approach adequacy. The bar's position before the slate
is a delivery boundary, not the order of two fields in one answer.

Before the native target exists, intent and dossier use a proposed objective ID
and exact statement/scope digest. `target` consumes the approved exact content
and records its native objective link atomically. Assigning that ID does not
invalidate approval; changing statement or scope does. Record the source and
preparation delta since approval, including newly relevant sources not listed
in the old dependency set. Material or unresolved impact on nearest work, scope,
transfer or the bar requires affected findings to refresh before commitment.
The first result assessment checks that declaration against the source delta.
The `direction` gate combines current configuration readiness with the selected
decision's obligations at the `cycle` boundary. It governs completing literature
and entering investigation planning. The complete preparation report is advisory
for managed research; cycle execution retains prospective admission. Before a
cycle decision exists, `gate direction` also lists the direction minimum
obligations alongside configuration and missing-decision obligations.

## The Q1 bar and assurance

The bar describes field- and intent-level standards while the author's candidate
is withheld. In `value.consequence`, each assessor states:

1. The field and Q1 venues considered.
2. The advance over the closest prior work such a venue would accept for this intent.
3. The evidence standard it requires.
4. Conditions that would make the result unpublishable there.

These standards guide the scientific assessment; journal acceptance itself is
out of scope for the assurance. The `q1_venue` assurance uses the existing
`{kind, status, reason, evidence}` shape with status `passed`, `failed` or
`unresolved`. At the slate it assesses potential: the selected candidate, if its
success criterion is met with the planned method, meets the required advance
and evidence standard and avoids the disqualifying conditions. At a result or
post-measurement decision it assesses the realized, supported advance and its
actual evidence.

For a `gate_version: 2` decision, `investigate`, `pivot` and
`develop_manuscript` require a passed `q1_venue` finding in both reviews.
`q1_venue_missing` reports an absent finding; `research_assurance_pending`
reports one that has not passed. An old review without this finding can retain
historical standing while leaving a new commitment pending. `deliver_requested`
retains its `stop` and `demand` checks, actual authority and explicit quality
conditions.

### Previous and current prompt versions

Assignments bind exact template IDs and hashes. The previous strategic prompts
use `exactory-independent-review-sources-v2/ROLE`, or the earlier
`exactory-independent-review-v1/ROLE`. The current bar, slate and result prompts
use `exactory-q1-review-v2/ROLE`; the current legacy packet form uses
`exactory-q1-review-v2/legacy/ROLE`. Context-repair templates retain their
`/context-repair` suffix. `ROLE` is `bar`, `slate` or `result`.

Assignments recorded with the previous prompts still run and record with their
previous assurance set: `impact`, `demand`, `novelty_risk`, `feasibility`,
`distinctness`, `continuity`, `grand_challenge` and `stop`. Current slate and
result responses add `q1_venue`. Bars keep empty assurances and unassessed
objective and method findings while establishing the field standard. A new
commitment consumes the findings it requires; a changed template leaves already
approved historical tranches intact.

## Test the inference that matters

State the precise target and transfer relation for every consequence:

| Relation | What supports the inference |
| --- | --- |
| Direct | Evidence about the actual stated system or mathematical object within its scope. |
| Logical transfer | A deduction with matching assumptions and quantifiers, such as a restricted counterexample to a universal claim. |
| Bounded transfer | A justified proxy-to-target bound with its error and applicable regime. |
| Empirically validated transfer | Relevant target checks and comparisons, coverage and uncertainty. A fitted observable alone does not establish a unique mechanism. |
| Conventional idealization | A documented field convention for that question; convention alone does not establish a target-level conclusion. |
| Unestablished transfer | An explicit open relationship that prevents the wider inference until resolved. |

A narrow theoretical result may itself matter. Judge it at its supported level;
it cannot borrow importance from an unproved target claim. A proxy method needs a
transfer plan before it is committed as a decisive target test. A bounded probe
of unknown adequacy is a valid investigation when its result can change the choice.

Start with the earliest affordable test that can change the commitment. If a
cheaper baseline is necessary, name its dependency, bounded completion condition
and exit into the deciding test. Each investigation authorizes a named question
and resource tranche, possibly containing dependent cycles. Its end, question
resolution or failure signal requires a new decision based on what it achieved.
A renamed sensitivity calculation does not renew an exhausted tranche. All
tranches charge the same authorized run and preserve the full objective.

## Results, work items and reconsideration

Extend the same dossier with exact supported claims, primary result/proof
references, failures, comparisons with matching assumptions, and which committed
consequences are established, contradicted or undecided. Keep the memo concise
while making necessary definitions, proofs and evidence available.

Each of two independent assessors evaluates validity, scope, novelty,
contribution-statement support, development and branch obligations before
consequence, including realized `q1_venue`, in one response. The harness records support and value as distinct
immutable findings and derives compatible readiness from them. Do not commission
a separate readiness approval followed by another full value-review loop.
Unsupported retained claims cannot be promoted because their possible importance
is high. Blind manuscript measurement remains a separate later assessment.

Connect each reviewer request, cycle alternative and contribution step to stable
work items. Keep every original request and reviewer linked. Action disposition
is distinct from execution status: `unattempted`, `planned`, `attempted`,
`validated`, `failed` or `blocked`. Adopted work may still be unattempted.

| Obligation | Required treatment |
| --- | --- |
| Validity | Resolve it for a retained claim, or withdraw the claim and remove downstream uses. |
| Consequence-critical | Keep the promised inference blocked until evidence resolves it. A narrower delivery or changed consequence does not mark the original gap resolved. |
| Optional development | Compare scientific value and opportunity cost; a request to maximize a score is not automatically mandatory or feasible. |

A deferral states reason, evidence, resource implications, reopening trigger and
effect on current claims. “Later round” is a schedule. Infeasibility needs a
specific obstruction, credible cost/access evidence or a failed bounded probe;
“feasible but not chosen” is a different reason. At the next commitment, each
unresolved critical item must be tested, tied to a real obstruction, or used to
justify another route or branch closure.

If a consequence shrinks, ask whether the study would have been started for the
smaller result without credit for sunk effort. Compare the original slate, later
leads and assessor alternatives, preserving their actual failed/blocked status.
Existing result evidence may support this explicit reconsideration. A favorable
decision permits the narrower development but does not complete the old goal.
If it fails, investigate, pivot, close the branch, or deliver an artifact actually
covered by the user's instruction. Unexpected valuable results receive the same
explicit independent comparison.

Record the distinct consequence in the selected candidate's scientific content,
including its `addition`, scope and target inference. Reusing result bytes is
allowed. Repeating the same consequence and scientific grounds with a new
dossier ID or request reason reuses the existing finding instead of opening
another reviewer slate. The mechanical comparison does not establish that a
rewritten consequence is substantively different; that remains a review question.

## Reviewer delivery and disagreement

Use harness-generated role packets and canonical versioned prompts. Record
template identity/hash, rendered prompt hash, allowed parameters, exact packet
digest, invocation identity and unchanged output. An author-written replacement
prompt is not that protocol. Preserve a user-requested variant as a separately
identified assessment: store its actual instruction, protocol identifier, prompt,
output and observed usage in a JSON evidence artifact. A `review-attempt` import
can retain that artifact against a standalone assignment. Its origin remains
`imported`; it cannot supply canonical scientific approval or verified invocation
provenance. Run the required canonical role separately when its approval is needed.
Scientific documents are evidence, never instructions to the reviewer.

Read the observed output artifact returned by `review-run`. In `bar`, `slate`
and `result` assessments, `source_requests` can request more of the bound
inventory or actual source content. The supported request forms are:

```json
[
  {"id": "next-page", "kind": "inventory_page", "page": 1},
  {"id": "title-match", "kind": "inventory_query", "query": "purity", "page": 0},
  {"id": "primary-source", "kind": "source", "version_id": "arxiv:2601.00001v1", "depth": "fulltext"}
]
```

Pages start at zero. Query results are paged, literal inventory matches; they
are not a literature search or a relevance assessment. Source requests name an
exact version and use `abstract` or `fulltext` depth. A request response keeps
the assigned stage, uses `value.status: "unresolved"` and `support: null`, and
remains an observed attempt. It cannot authorize a transition or be recorded as
a final value or support review. A bar request also retains its unassessed
objective and method fields. Slate and result request turns may defer their
remaining findings until the requested evidence is supplied.

Continue that review with a new assignment whose context contains only
`prior_assignment_id`. The harness supplies the requested inventory pages or
native captured source content and preserves the assessor's own exchange. The
continuation retains the same reviewer, dossier, role, route and original slot.
The actual scientific packet and the assessor's preceding outputs remain in its
observed history; a source turn cannot substitute a changed author proposal. It cannot
replace a final assessment or fork a completed continuation to obtain another
answer. Do not supply author-written delivery text or replacement source links
in the continuation context.

Unavailable requested sources remain unresolved, including requests inherited
from earlier exchanges. An inventory-only request or an empty request list
cannot discard that obligation. Later native acquisition is recorded separately
from the original inventory snapshot. Source delivery does not create an author
reading record or certify scientific truth. Charge every continuation, including
its supplied history, to the existing review resource allowance. If evidence or
resources remain unavailable, retain the unresolved outcome and actual costs.

Full-text delivery preserves the current native source bundle and its required
text, figures, tables, equations and supplements. Required units that are missing,
partial or unsupported remain explicit source-scope obligations. Original visual
assets are delivered with their verified source bindings. A captured article
without a native unit inventory is identified as not inventoried; receipt of its
bytes does not certify complete article scope or grant full reading credit.

A reviewer that needs more full text than one input holds reads it in parts and
keeps its own verbatim excerpts. On 2026-10-27 two reviews of one problem record
could not finish. A blind bar session requested seven full texts of 1,331,003
characters together. Each continuation delivered some of them, the route fitting
left out the earlier bodies, and the reviewer requested them again for ten
rounds. A problem openness review carried eight full readings of 1,598,318
characters. The route withheld five bodies by text priority, the reviewer did not
accept reading records in place of complete reading, and a `native_math` review
had no later turn in which the texts could arrive. Both totals exceed the route
limits of 1,048,576 and 1,300,000 characters. The repair keeps four conditions.
The reviewer compresses the sources, not the author, so an author's summary does
not replace reading. The compressed form is verbatim excerpts with their
locations, not summaries. A turn that delivers a full text tells the reviewer
that the text will not come back. The harness checks every excerpt against the
stored source text, and only checked excerpts reach later turns as source text.
The final assessment rests on the reviewer's notes and checked excerpts, and a
bounded passage request reads a missing part again. The
[CLI reference](research-cli.md#scientific-contribution-decisions) gives the
excerpt rules of source-request sessions, and its
[problem creation section](research-cli.md#problem-creation) gives the reading
chains of openness reviews.

One turn delivers at most 120,000 characters of new full text, and a longer
text arrives in parts over consecutive turns. On 2026-10-28 a reading turn with
956,141 characters of new full text and a bar continuation with about 600,000
were stopped at the route's 600-second limit before the reviewer had read them
and written its excerpts. The input limit was not the constraint; the work of
one turn was. A turn that ended without output can therefore be replaced by a
new continuation of the same parent.

The records that an openness packet carries as evidence (its bound search
records, its reading records and the statement record) are reading units like
the full texts, delivered as exact JSON text in the reading turns and quoted
against those bytes, because on 2026-10-28 a final turn was refused at
1,284,492 characters after 17 completed reading turns: every full-text body was
already withheld, and the packet's 17 records, about 431,000 characters, had
reached no reading turn. The final turn is bounded, so that a chain ends in a
request that fits or in a refusal that names the size of each part: it carries
a compact reading record and withholds, only as far as its measured request
needs, the bodies that reading turns delivered complete and then the notes of
the earliest turns, never an excerpt or a passage.

External-paper verification uses the `verification` role with `dossier_id: null`
and `context: {"verification_task_id": "<bound task digest>"}`. It requires the
same verified actual context but no author strategy or research-decision gates.
The exact prepared paper and comparison evidence are bound to that task. Its
response is `{verdict, checks}`, with separate soundness, novelty and impact
checks. `bind-verdict` requires `assessment.assignment_id` and the unchanged
observed verdict body and checks. The verifier's current cohort sample and
derived prediction are permitted evidence; earlier reviewers' scores are not.

An independent assignment needs a verified actual route and version. Record
host/version, reviewer identity, model/provider/version when exposed, fork/resume
mode, configuration scope, permitted evidence/tool paths and test receipts.
Keep author-history isolation, prior-assessment isolation and identity/arm masking
separate. Context status is `verified_for_route`, `unverified` or `contaminated`.
A recognizable paper is not the same exposure as receiving its earlier score.

Test startup instructions, automatic memory, hooks and permitted tool access.
Use synthetic excluded tokens absent from the assessment prompt and a positive
control proving intended packet/evidence delivery. Absence from the answer alone
cannot prove exclusion from input. A new directory, host or session is not a
verified remedy by itself. Recheck relevant isolation controls after route/version
or configuration changes, and record later tool/cache/search exposure. Only an
assignment meeting the required context contract supplies independent approval.
Prefer another model family when configured and authorized, otherwise report
the common-family limitation.

Each material objection has a stable ID, contested inference, evidence and
resolution condition. The other assessor either accepts it as an obligation or
contests it with evidence. A fresh adjudicator, neither author nor initial
assessor, receives the harness-built objection packet and canonical prompt under
the recorded assignment policy. It returns `upheld`, `not_upheld` or `unresolved`
on that objection. An unresolved material objection prevents development; two
favorable opinions cannot erase a counterexample.

Corrections require a demonstrable misreading of supplied evidence or an omission
required by that packet's contract at the time. Adjudicate admissibility before
reassessment. Batch known errors; another correction requires a newly identified
material error, evidence and why it was not resolved earlier. New significance
evidence is a dossier revision, not a correction of the old packet. Charge every
attempt and preserve every output. Exact duplicate requests reuse findings.
Material new evidence, deduction, comparison, user intent or explicit reduced-
consequence reconsideration can justify a new decision, carrying every old
objection. Renaming a claim or adding an irrelevant citation cannot do so.
An adjudication resolves its exact original dossier and objection. A later
dossier must retain and assess new evidence on that objection; an earlier
favorable adjudication cannot override current unresolved findings.

Failed calls remain operational failures with attempt identity and cost. Retry
the same packet under the bounded operational policy; do not replace an unfavorable
scientific answer by selecting another assessor. An ordinary replacement for a
failed initial assignment inherits its fixed slot. Replacing a failed repair
requires another explicit repair in that same slot. The superseded assignment
cannot later be invoked or supply current approval. A decision needs two distinct
logical slots as well as two distinct reviewers. If later contamination is found,
preserve the review and decisions, mark affected independence claims, repair the
route and reassess affected current findings before consuming approval. Missing
historical metadata is unknown exposure, not proof of contamination.

Before repairing a contaminated strategic review, record every actual observed
final assessment in its inherited scientific history with `value-review`,
including adverse findings. Use the unchanged observed response and its exact
assignment; recording it preserves immutable scientific history and does not
restore independent approval. If repair returns
`review_context_repair_history_unrecorded`, record the identified final response
before retrying with the current workspace revision. Corrupt or malformed raw
outputs remain explicit unresolved evidence; do not reconstruct a valid final
or obtain another answer to replace them. A genuine pending source-request turn
needs no premature final record. Carry all its actual requests into repair,
including requests first made in the last observed output.

For a contaminated strategic review, create an explicit repaired assignment with
`context.context_repair`. Supply the exact predecessor `assignment_id`, every
applicable contamination `event_ids`, a verified `probe_id` recorded after those
events, the repair `reason`, and the exact recorded event `evidence`. Use the same
dossier, phase and author boundary. This context cannot include replacement
source links or `prior_assignment_id`. The repaired assignment retains the
original slot and starts a fresh transport context. Its canonical packet keeps
the actual prior scientific inputs, adverse findings and material objections.
Previously delivered figures and PDFs are supplied again as actual attachments
with their exact artifact bindings. Previously supplied excerpts retain their
original bounds; repair does not expose additional parts of their originals.

The final observed response adds `reassessment: {assignment_id, findings}`.
Each finding identifies an inherited `review_id`, a `disposition` of `confirmed`,
`revised` or `unresolved`, its scientific `reason` and exact `evidence`. Address
every inherited final review of that phase. Changed scientific content cannot
be called confirmed. Keep inherited objections in `value.objection_findings`
and the later decision's objection dispositions; a repaired context does not
refute an old counterexample. Source-request turns may defer these final findings.
They retain every pending source obligation until native delivery resolves it.
If later exposure makes a repaired final stale before it was recorded, its exact
original prospective repair provenance still permits historical registration.
Current invocation and independent approval require coverage of all current
contamination events; historical registration does not satisfy that requirement.
An unrelated later clean probe cannot replace missing or corrupt evidence of the
bound prospective repair probe. A genuinely new exposure requires a separately
bound successor repair, preserving the earlier history and costs.

Native mathematical search uses the same observed isolation mechanism through
the `native_math` role with `dossier_id: null` and `context.native_packet`.
Its [native export and submission contract](../skills/math-solver/SEARCH.md#observed-native-review-delivery)
binds the unchanged native review schema and exact scientific evidence. Native
proof obligations, resource accounts and historical replay remain in force,
without adding strategic intent, bar or value prerequisites. A standalone
paper-review packet is not a substitute for these native subjects.

## Commands and shared state

All mutations below use the existing `--file`, `--workspace`,
`--expected-revision` and `--request-id` conventions. Use the current revision for
a new mutation and the original payload, revision and identity for its replay.

| Command | Record |
| --- | --- |
| `intent` | Versioned instruction, scope, resource and delivery contract. |
| `strategy` | Versioned candidate/result dossier and exact dependencies. |
| `work-item` | Persistent obligation, disposition and execution evidence. |
| `research-lead` | Versioned discrepancy provenance and research disposition. |
| `review-route` | Actual route and versioned configuration. |
| `review-probe` | Observed invocation testing intended input delivery and context exclusions for that route. |
| `review-assignment` | Role, packet/prompt binding, identity and delivery context; an exact prior assignment can continue a pending bar source request. |
| `review-run` | Invoke the assigned canonical packet, retaining observed output, failure and usage. |
| `review-attempt` | Import historical invocation evidence; imported assertions alone do not verify a route. |
| `review-context` | Later exposure or context status affecting the assignment. |
| `review-adjudication` | Evidence-bound disposition of a specific objection or correction. |
| `value-review` | Independent support/consequence response bound to the assignment. |
| `research-decision` | Phase-specific action and complete evidence/objection dispositions. |
| `source-impact` | Exact new/changed-source declaration for the selected decision; affected science still needs reassessment. |

The read-only interfaces expose delivery material and the same obligations used
by managed transition entrypoints:

```sh
exactory-research strategy-packet --dossier-id DOSSIER --role ROLE --reviewer-id REVIEWER
exactory-research review-route-assess --route-id ROUTE
exactory-research research-decision-assess --boundary write
exactory-research research-decision-assess --boundary cycle --decision-id DECISION
```

Boundary values are `target`, `cycle`, `write`, `publication` and `round`. Use
actual identifiers and the role appropriate to the current delivery phase.
`status`, `next`, readiness, cycle/round admission, manuscript preparation/pinning
and publication planning consume the same current decision requirements.
Assessment reports obligations; it does not manufacture scientific approval from
nonempty text fields. Material dependency changes require affected reassessment;
unrelated metadata changes do not invalidate the entire dossier.
A successor dossier invalidates the earlier decision for new commitments,
including when the caller supplies the old decision ID explicitly. Manuscript
assignments use the exact stored publication bundle. A matching caller-supplied
digest cannot substitute different manuscript bytes or evidence.

Set up a supported route, run its probe, inspect `review-route-assess`, and only
then create independent assignments. `review-probe` uses `{"id": "probe-id",
"route_id": "route-id"}`; `review-run` uses `{"id": "attempt-id",
"assignment_id": "assignment-id"}`. Use actual unique IDs and the usual mutation
identity flags. These commands perform real provider calls; run them only within
the user's authorized resources. Load a needed credential into that process,
never into a payload or artifact. A replay of the same request returns its saved
observation. An interrupted uncertain invocation remains pending and does not
silently run again.

A route pins the adapter runtime it was recorded with. Verification of probes
and attempts uses that pinned runtime. Execution requires the installed adapter
runtime to match the route's pinned runtime; otherwise, record and probe a new
route.

The subscription-backed route uses this shape:

```json
{
  "id": "codex-route-001",
  "adapter": "codex_cli_v1",
  "model": "gpt-6.1-sol",
  "endpoint": "codex://local",
  "configuration": {"max_output_tokens": 4000, "timeout_seconds": 300}
}
```

Its inspected startup contract is for Codex CLI 0.159.2 and 0.162.1 with user
configuration and rules disabled, at reasoning effort `high`. It accepts text
evidence only. The adapter checks actual recorded input and rejects uninspected
startup declarations, additional context and tool events. Other runtime contexts
remain unverified until inspected and probed. `timeout_seconds` is a hard
wall-time limit, positive and at most 600. `max_output_tokens` is checked after
generation; it is not a provider-side token reservation or a guarantee against
spending beyond that value. Binary evidence requires a separately configured and
verified supported route. The `openai_responses_v1` adapter uses an HTTPS
endpoint and an API credential; its existence does not authorize paid calls or
establish live route verification.

The reviewer of a Claude Code session is the Claude Code CLI itself, on the
owner's subscription:

```json
{
  "id": "claude-route-001",
  "adapter": "claude_cli_v1",
  "model": "claude-opus-5-5",
  "endpoint": "claude://local",
  "configuration": {"effort": "max", "max_output_tokens": 20000, "timeout_seconds": 1800}
}
```

Each invocation runs `claude -p` as its own process in an empty working
directory, in safe mode (no CLAUDE.md, skills, plugins, hooks, MCP servers or
auto memory), with no built-in tools, a system prompt replaced by the canonical
review prompt, no session persistence and no permission prompts. The packet goes
in as one stream-json user message that the CLI echoes back, and the adapter
verifies the observed stream: the session initialization reports no tools, MCP
servers, skills or commands and the configured model; the echoed input equals
the request; no tool use, tool result, hook output, compaction or second turn
appears; and the result is one successful turn. `effort` is one of `low`,
`medium`, `high`, `xhigh` or `max`; `timeout_seconds` is at most 3600. The
route limit is 1,300,000 characters of input; a larger request is refused before
the CLI runs and recorded as a failed attempt. The recorded runtime names the
isolation flags, not the installed CLI version, which is recorded with each
invocation instead, so a CLI update does not invalidate earlier reviews. Like
the Codex route it accepts text evidence only. The API credentials of the parent
session are not passed to the reviewer process.

On the local routes the packet is labelled as JSON, a binary artifact is named
without its bytes, and a packet that carries problem evidence asks the transport
to replay the session's earlier turns without long evidence bodies, without the
packets of a scientific history and without the parts the current packet
repeats. The Codex route additionally elides re-delivered and prioritized text
bodies until the input fits the app-server limit, and sends an input that no
elision fits as it stands, so that the app-server's refusal is recorded.

A route can be retired: an account closes, a model is withdrawn, or the owner
forbids the route. Record `review-context` with `kind: "route_retired"` on the
slot's last observed assignment, `source: "owner"` or the actual cause, a
reason, and the owner's instruction as the evidence artifact. From then on the
route accepts no new assignment and no run (`review_route_retired`), an
assignment on it that never produced an observed output is dead and holds no
slot, and the observed outputs on it stay verified. The slot continues through
the context-repair path: probe another route after the retirement, then assign
the same role with `context.context_repair` naming the retirement event, its
evidence and the probe. The repair packet carries the cause under
`context_repair.cause`, so the replacement reviewer reads why it reassesses.

`source-impact` has exactly `id`, `decision_id` and `source_impact`. An empty
source delta uses this payload shape:

```json
{
  "id": "source-impact-001",
  "decision_id": "decision-001",
  "source_impact": {
    "sources": [],
    "aspects": {
      "nearest_work": "unchanged",
      "scope": "unchanged",
      "transfer": "unchanged",
      "bar": "unchanged"
    }
  }
}
```

For a nonempty delta, `sources` has exactly one object per actual changed source:
`source_id` (stored source ID), `impact` (`unrelated`, `material` or `unresolved`),
`reason` (scientific explanation), and `evidence` (actual supported evidence
references). Each of the four `aspects` accepts `unchanged`, `material` or
`unresolved`. The declaration must cover the exact current delta. Material or
unresolved impact refuses consumption of old approval and requires an affected
dossier/assessment refresh; it cannot be cleared by assigning another native
objective ID. Use the same structure for a target commitment's source impact.

Old round commands adapt this same decision. Continue means investigate or pivot
with a next tranche; stop separately records branch closure, supported manuscript
preparation or authorized delivery. `round-review` records/displays the same
assessment, not a second approval, and `round-assess` retains the round's factual
result. Preserve the old seven continue assurances: consequence, demand, prior
work, adequacy/resources, distinctness, inheritance and Grand Challenge relation.
The old stop/demand assurances remain evidence-based closure and alternative
comparison, including rejection of unsupported infeasibility claims.

For a managed canonical decision, `round`, `round-review` and `round-admit` accept the
compact adapter payload `{"id": "round-record-id", "research_decision_id":
"decision-id", "reason": "Evidence-based reason for this operation."}`. Its
approval comes from the referenced canonical decision and original findings.
Historical full round records remain readable, and unmanaged/native workflows
retain their own contracts.

`round-assess` also requires the factual `outcome` (`answered`, `failed`,
`exhausted` or `unresolved`), `cycle_ids` and actual `evidence`. It binds assessed
cycles in the original tranche. A prospective approval and a generic reason
cannot establish a factual result. Recording a failed outcome requires no new
strategic approval; a successor dossier can cite the retained factual assessment.

Contribution analysis can record `refresh.status` as `current` with a reason,
evidence and the scientific questions checked when existing verified findings
still answer those questions. That permits an empty new investigation or reuse
of its original captured responses; `refresh_required` names the work still
needed. Bind each `reviewer_changes[].work_item_id` and each adopted
`steps[].work_item_id` to the exact stable work record. A scientifically justified
pivot may cite `inherited_failures` instead of inventing positive `builds_on`
claims. When current investigation shows no supported next step and no adopted
request is left without a step, preserve that reason and an empty `steps` list;
do not manufacture follow-up work to close the branch. Managed reviewer-change
coverage includes soundness, presentation and contribution requests, each with
its own work-item classification. Keep actual source-reading and measured-bundle
requirements current.

## Migration and limits

Continue existing studies with their evidence and results. A study may retain
Version 6 without constitution revalidation and keep its configuration,
foundation and synthesis reports byte-identical. A research study may adopt
Version 7 explicitly; adoption alone changes no claim. Verification workspaces
retain Version 6; their Version 7 adoption is out of scope for this release.

Claim currency follows objective, scope, assumptions, plan, executions, exact
supporting evidence and its source records, inheritance, validations, strategy
failures and authorizations. Full dependency maps remain available for audit.
Unrelated global preparation, configuration, constitution, foundation inventory
and synthesis changes leave the claim current. Declare relevant source changes
through `source-impact`; changed decisive evidence requires affected assessment.
Validate historical digests against recorded inputs before comparing their claim
projections. Reconstruct every missing historical field from retained evidence
and verify the original digest. Managed per-record history supplies the original
revision's inputs; `claim_reassessment_required` names any record whose inputs
cannot be recovered or authenticated. The
[workflow upgrade procedure](research-workflow.md#studies-in-progress) describes
the compatibility mechanics.

Apply new direction and Q1 obligations at the next relevant commitment. Recorded
decisions and approved tranches keep their historical standing. Stores that never
recorded strategic assessment can remain `legacy_unassessed` until that next
commitment. Reconcile pending executions once under their frozen authority.
Preserve completed manuscripts and receipts and repeat scientific work only when
required validation needs it.

This workflow preserves scientific support, source provenance and user authority.
It does not guarantee an important discovery. Report software correctness,
operational usefulness and scientific efficacy separately. Higher manuscript
scores or fewer written papers alone do not establish more scientific knowledge.
