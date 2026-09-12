# Development rounds design

Date: 2026-09-12

Status: Approved by the user on 2026-09-12 with the decisions recorded at the end of this document. Implementation is authorized through release 0.39.0 without further stops.

Scope: `plugins/exactory-client`, read at commit `19ef5d2` (release 0.38.0). Inputs: `misc/harness-improvement/v3/background-and-requirements.md` in the exactory repository, the plugin's `research_harness`, `skills`, `hooks`, `docs`, and the studies `exactory-research/boed-mean-field` and `exactory-research/closed-gravity-internal-measurements`.

Target version: 0.39.0. This design supplements the [literature efficiency design](2026-09-11-literature-efficiency-design.md) and inherits its global constraints.

## Objective

Let one study develop its paper across several rounds of research. A round starts from the paper as it stands, states an evidenced goal for what the paper will contribute after the round, performs new investigation and new experiments toward that goal, and grows the same manuscript. An independent assessor approves every round before it starts and every stop before the paper is deposited. The loop ends by a recorded decision, a recorded exhaustion, or a recorded repetition, never by drifting.

## Global constraints

Inherited from 0.38.0 unchanged: Python 3.9 standard library; `schema_version` 1; append-only events and receipts; read commands stay read-only; one `Evaluation` per command and no cache that outlives a process; legacy records keep their credit; compact views and budgets grant no scientific authority; three fresh blind reviews per manuscript measurement and two accepting blind reviews before publication; five to ten external innovation families and one within-field case for a research foundation; English everywhere; research runs stay under `exactory-research`; both host manifests carry the same version and `codex/generate.py --check` passes.

Added by this design:

- A round adds a bounded number of records: one `round`, one `round_review`, one `round_admission`, one `round_assessment`, and the readings, searches, cycles and reviews the round's own work produces. No record is created per cohort member or per source because a round exists.
- The cohort, its readings, the innovation studies and the field standards of the study are reused by every round. A round reads new sources only where its consequence questions require them.
- Every round records at least one captured search per consequence purpose, at least one full reading of a development exemplar, at least one admitted and assessed experiment cycle, and at least one new evidenced claim in the manuscript. A round that does not is recorded as unproductive.
- The stop gate is mechanical: a `continue` decision needs an approved, distinct goal that builds on the previous rounds' products within the recorded budgets; a `stop` decision needs an approved disposition of every candidate; both bind the exact manuscript bundle they were made on.
- Blind review scores and cohort predictions are recorded at the end of every round as results. No gate rule and no goal depends on them.

## 1. Terms

| Term | Meaning |
| --- | --- |
| Cycle | An admitted experiment cycle (`cycle`, `admit`, `assess`, `checkpoint`), as today. |
| Round | One pass from a goal to a measured manuscript. Round 1 is the study as the harness runs it today, from `initiate` to the end of `evaluate`. Round N+1 starts at the round gate, re-enters `literature`, and ends at the next round gate. |
| Round gate | The decision, taken on the exact current manuscript bundle, to start round N+1 with a goal or to stop and publish. Recorded by `round`, judged by `round-review`, and checked by `gate round`. |
| Goal | The round's statement of what the paper will contribute after the round that it does not contribute now, for whom, in which direction, with success criteria and stop conditions. |
| Direction | `vertical`: the round raises the certainty or generality of what the paper claims (mechanism, tightness, weaker assumptions, generalization). `horizontal`: the round widens who can use it (transfer, unification, practical usefulness, a new field). |
| Consequence purposes | The four search purposes a development round records: `downstream`, `next_step`, `exemplars`, `changes`. |
| Carried development | A useful development that a cycle assessment marks `next_round` instead of pursuing it inside the current round. The round gate must dispose of it. |

The word "round" is also used by screening records (`screening.round`, an integer). The two do not share a record kind or a field, and the CLI reference names both.

## 2. What the harness does today, and what this design adds

Facts, from the code:

- `gates.py` defines the stages `initiate, cohort, literature, ideate, experiment, write, evaluate, deposit, submit, complete`. Backward transitions already include `evaluate -> literature`. Entering `deposit` requires the `publication` gate; nothing else stands between a measured manuscript and its deposit.
- `development.py` requires every cycle assessment to carry a `development` block: `next_question` with one of nine strategies or `none`, `alternatives` and `branches` with dispositions `pursue`, `not_useful`, `resolved`, `budget_paused`. A `pursue` or `budget_paused` item leaves `useful_development_remaining`, so readiness, and therefore writing, waits until every useful development for the fixed objective is done or declared not useful. The readiness reviewer checks `validity, scope, novelty, contribution, development, branches`.
- `principles.py` locks the research objective: `target` refuses to change it (`objective_locked`), and every cycle and every inherited checkpoint must carry the identical objective.
- `publication.py` pins the exact bundle (`pdf, abstract, bibliography, claims, sources` plus `claim_evidence`), refuses a second review by the same assessor on the same bundle, and requires two accepting blind reviews. The rubric core is exactly `summary, strengths, weaknesses, soundness, presentation, contribution, overall, decision`.
- The manuscript loop (`skills/ai-science/LOOP.md`) measures with three blind reviews, revises the most consequential weakness, and ends on a target, a resource condition, or saturation. Its evidence gaps return to a cycle inside the same objective.
- No record states why a study went back from `evaluate` to `literature`, no record states that the paper's development is finished, no local blind measurement of the cohort percentile exists (`.exactory/self-verdict.md` is the author's own expectation), and nothing checks that a later manuscript still contains what an earlier one established.

This design adds the round records, the round gate, the consequence purposes, the `next_round` disposition, objective lineage across rounds, claims continuity, and a blind cohort prediction per measurement reviewer. It changes no existing gate's meaning.

## 3. Approaches considered

| Approach | What it is | Why not, or why |
| --- | --- | --- |
| Skill-only loop | `LOOP.md` tells the agent to run further rounds and to stop when nothing remains. | No record, no gate, no independence. The v1 proposal already found that requirements affect the work only through enforced transitions. Rejected. |
| A new study per round | Round N+1 is a new managed workspace that cites round N's paper. | Publishes intermediate states, repeats the cohort and literature cost, and splits the evidence store. Contradicts the settled principle. Rejected. |
| Rounds as records and a gate inside the existing stage machine | New records and one gate; the existing backward transition `evaluate -> literature` becomes the round entry; deposit requires the gate's `stop`. | Reuses every existing idiom (`prepared_mutation`, `immutable_record`, obligations, assessor independence, neutral packets, budgets). Bounded cost per round. Chosen. |

## 4. The round loop

```
round 1:   initiate -> cohort -> literature -> ideate -> experiment -> write -> evaluate
                                                                                  |
                                       round gate (round, round-review, gate round)
                                          |                                   |
                                     continue                                stop
                                          |                                   |
round N+1: round-admit -> literature -> ideate -> experiment -> write -> evaluate -> round-assess -> round gate
                                                                                                        |
                                                                            deposit -> submit -> complete
```

Inside a round the existing stages, gates and loops run unchanged: preparation gate before `ideate`, admitted cycles, readiness review before `write`, the manuscript measurement and the publication gate in `evaluate`. What is new is the gate at the end of `evaluate`, the assessment of a round against its goal, and what `literature`, `ideate`, `experiment` and `write` require when a round is active.

Round 1 has no goal record; its goal is the complete objective and the recorded rationale. Round numbers start at 1; the first `round` record closes round 1 and, on `continue`, proposes round 2.

## 5. Records and operations

All new operations use `prepared_mutation` with `--expected-revision` and `--request-id`, produce immutable records, and appear in `docs/research-cli-examples.json` and `exactory-research example OPERATION`.

### 5.1 `round`: close the current round and decide

```json
{"id": "round-decision-002", "closes": 1, "decision": "continue",
 "bundle_digest": "SHA256 of the current publication bundle",
 "candidates": [
   {"id": "multivariate-nonlinear", "direction": "vertical", "statement": "...",
    "disposition": "pursue", "reason": "...", "evidence": [ {"kind": "source", "link": {...}} ]},
   {"id": "real-boed-application", "direction": "horizontal", "statement": "...",
    "disposition": "rejected", "reason": "...", "evidence": [ ... ]}
 ],
 "carried": [
   {"assessment_id": "assessment-003", "kind": "alternative", "question": "The exact next_question text of the carried alternative",
    "disposition": "pursue", "reason": "..."},
   {"assessment_id": "assessment-003", "kind": "branch", "cycle_id": "cycle-2", "disposition": "deferred", "reason": "..."}
 ],
 "next": {
   "number": 2,
   "objective": {"kind": "objective", "id": "...", "statement": "..."},
   "objective_lineage": null,
   "goal": {
     "direction": "vertical", "field_change": null,
     "statement": "...",
     "contribution_delta": "What the field can do after this round that it cannot do with the current paper.",
     "beneficiaries": [{"who": "...", "bottleneck": "...", "evidence": [ ... ]}],
     "success_criteria": [{"id": "sc-1", "kind": "claim", "statement": "The manuscript establishes ... with evidence."},
                          {"id": "sc-2", "kind": "scope", "statement": "The assessed scope covers ... under assumptions ..."}],
     "stop_conditions": [{"id": "st-1", "statement": "..."}],
     "route": "The hypotheses and tests the round will plan in ideate.",
     "risks": ["..."],
     "evidence": [ ... ]
   },
   "resource_limits": {"literature": {"network_requests": 60, "readings": 40}, "experiment": {"wall_seconds": 7200}},
   "reopening": null
 },
 "reason": "Why this decision follows from the candidates and the current paper."}
```

Validation, in order:

- `bundle_digest` equals the current publication bundle's digest (`publication._bundle`). There is no decision without an exact manuscript.
- `closes` equals the current round number: 1 when no round was admitted, otherwise the latest admitted round's number. For a round of number 2 or more, that round's `round_assessment` exists and binds the same bundle (`round_assessment_missing` otherwise).
- Every carried development recorded by any cycle assessment of the closing round (every `next_round` alternative or branch) appears in `carried` with a disposition (`carried_development_missing` otherwise). An alternative is identified by its assessment id and its exact `question` text; a branch by its assessment id and `cycle_id`. Dispositions are `pursue`, `rejected`, `deferred`. `pursue` items and the pursued candidate must agree with `next.goal`.
- `candidates` is nonempty for both decisions. For `continue`, exactly one candidate is `pursue` and `next` is present. For `stop`, no candidate is `pursue` and `next` is null.
- Every candidate carries evidence links: the existing `source` and `result` shapes, or `{"kind": "review", "review_id": ...}` naming a manuscript review of this bundle.
- `next.number` is `closes + 1`. `next.objective` is either identical to the configured objective (`objective_lineage: null`) or a wider statement with `objective_lineage: {previous_id, containment}` where `containment` explains why the new objective contains the previous one. Containment is the author's recorded assertion: the statement text gives no mechanical containment check, so the round reviewer's continuity and distinctness checks judge it. The harness refuses with `objective_locked` a `previous_id` that is not the configured objective's id, an unchanged statement, or a reused objective id, and with `invalid_target` a malformed objective or lineage.
- `next.goal.statement` differs, after the same normalization `_normalized` applies to development questions, from the goal of every earlier round and from every earlier `rejected` candidate (`round_goal_repeated`). A `deferred` candidate of an earlier decision may become the goal of a later round. A goal that repeats an earlier withdrawn or unsuccessful goal is accepted only with `next.reopening: {round_id, reason, evidence}` naming the earlier round and carrying at least one evidence reference that the earlier decision did not carry.
- `next.goal.success_criteria` has at least one entry; `kind` is `claim` (a claim the manuscript will carry with evidence) or `scope` (a wider assessed scope). Blind review scores and cohort predictions are results of a round, recorded with its assessment; they are not criteria and a goal cannot name them. `stop_conditions` has at least one entry.
- `next.goal.continuity` states how the previous rounds' products stay in full use: which claims, evidence and readings the round builds on. Every round continues from the paper as it stands; a goal that replaces earlier products is not a development round.
- `next.goal.field_change` is `null`, or `{corpus, primaryCategory}` for a horizontal goal that stays in the study's corpus and adds a category. The round then collects the difference: the new category's members in the study's window that are not already inventoried (section 7). `next.resource_limits.literature` must then carry `network_requests` and `readings` allowances for that difference.
- When the user recorded a `development` budget, it has room for one more round (`resource_budget_exhausted` for the `development` purpose otherwise).
- After two consecutive rounds assessed as unsuccessful (section 5.4), `continue` is accepted only with a goal in the other direction or a `reopening` with changed evidence (`round_direction_exhausted` otherwise).

The record is stored as `round/<id>` with `digest`, `decided_revision` and the bundle's digest. Several decisions may be recorded for one closing round, for example when the assessor does not approve the first; the gate reads the one that has an approving review, and `round-review` refuses to approve a second decision for a closing round that already has an approved one (`round_decision_duplicate`). The bundle of a decision may carry the same files as the previous gate's bundle when the closing round changed no manuscript file; it is still a newly pinned bundle, because the round's cycles changed the readiness candidate, and that round is unproductive by section 5.4.

### 5.2 `round-review`: the independent judgment

```json
{"id": "round-review-002", "round_id": "round-decision-002", "round_digest": "...",
 "assessor": {"id": "...", "kind": "agent", "provenance": "local_artifact id", "relationship": "...", "independence_basis": "..."},
 "verdict": "approved",
 "checks": [
   {"kind": "impact", "status": "passed", "reason": "...", "evidence": [ ... ]},
   {"kind": "demand", "status": "passed", "reason": "...", "evidence": [ ... ]},
   {"kind": "novelty_risk", "status": "passed", "reason": "...", "evidence": [ ... ]},
   {"kind": "feasibility", "status": "passed", "reason": "...", "evidence": [ ... ]},
   {"kind": "distinctness", "status": "passed", "reason": "...", "evidence": [ ... ]},
   {"kind": "continuity", "status": "passed", "reason": "...", "evidence": [ ... ]}
 ],
 "limitations": ["..."]}
```

- The assessor is not an author of any cycle plan in the study (the readiness rule `review_not_independent`, applied to the union of cycle authors). `kind` is `human` or `agent`; provenance is a pinned artifact.
- For `continue` the six checks `impact, demand, novelty_risk, feasibility, distinctness, continuity` are each addressed once. For `stop` the checks are `stop` (every candidate and carried item was judged fairly against the paper's evidence and reviews) and `demand` (no evidenced demand for a further round remains). Verdict is `approved`, `not_approved`, or `unresolved`.
- The review binds `round_digest`; a changed decision needs a new review. One review per assessor per decision.

### 5.3 `round-admit`: open the next round

```json
{"id": "round-002", "round_id": "round-decision-002", "review_id": "round-review-002", "reason": "..."}
```

- Requires an approving review of a `continue` decision on the current bundle, the `development` budget room, and no other admitted round without an assessment.
- Applies the objective: when `objective_lineage` is present, the configuration target becomes `next.objective`, `research_objective/{objective id}` records `next.objective` unchanged, and `objective_lineage/{objective id}` records `{id, predecessor, containment, round_id}` with `predecessor` the previous objective id and `round_id` this admission's id. The previous objective stays retained. This is the only path that changes a research objective.
- Records `round_admission/<id>` with `number`, `goal`, `objective`, `admitted_revision`, the `resource_limits`, and the opening state of the round: the bundle id and digest, its claim ids, the selected search per purpose, the full-text requirement ids, the cycle ids, the reading count, and the resource accounts. Freshness inside the round is judged against this opening state: a search purpose is fresh when its selected search differs from the opening one, a cycle is new when its id is not among the opening cycle ids, and a claim is new when its id is not among the opening claim ids. The study's current round number becomes `number`.
- The `literature` stage of the new round is entered afterwards with `exactory-lab state set --stage literature --status pending`; the transition rule in section 6 checks that this admission exists.

### 5.4 `round-assess`: assess a finished round against its goal

```json
{"id": "round-assessment-002", "round_id": "round-002", "bundle_digest": "...",
 "criteria": [{"id": "sc-1", "status": "observed", "explanation": "...", "evidence": [ ... ]},
              {"id": "sc-2", "status": "not_observed", "explanation": "...", "evidence": [ ... ]}],
 "stop_conditions": [{"id": "st-1", "status": "not_observed", "explanation": "...", "evidence": [ ... ]}],
 "summary": "What the round established, what it did not, and what it cost."}
```

- `bundle_digest` is the current publication bundle. Every success criterion and stop condition of the admitted goal is addressed once with `observed`, `not_observed`, or `unresolved` and evidence.
- The harness derives and stores with the record, as information about the round and never as a criterion: the set of claim ids new in this bundle relative to the round's opening bundle, the review median and spread and the prediction median and spread for this bundle, the count of cycles, searches and full readings recorded since the round's admission, and the resource accounts charged since admission. The medians are reference values for the user and the round assessor; the gate's rules do not read them.
- A round with no `observed` success criterion is `unsuccessful`. A round with no new claim, or without the required searches, exemplar and cycle, is `unproductive`; an unproductive round is also unsuccessful.
- One assessment per round. It is a prerequisite of the next `round` decision.

### 5.5 `manuscript-prediction`: the blind cohort prediction

```json
{"id": "prediction-004-r1", "bundle_digest": "...", "blind": true,
 "assessor": {"id": "...", "kind": "agent", "provenance": "...", "relationship": "...", "independence_basis": "..."},
 "prediction": {"corpus": "arxiv", "category": "cs.LG", "windowStart": "2026-03-01", "windowEnd": "2026-08-31",
                "percentile": 25, "band": {"best": 15, "worst": 40}},
 "reasons": ["..."]}
```

- The four cohort fields equal the study's frozen collection definition (`collection.definition`); `best <= percentile <= worst`, all within 1 to 100. This is the shape the market's verdict carries (`skills/verify/SKILL.md`), so the local median is directly comparable with `predictionSummary` after submission.
- Each blind measurement reviewer returns its rubric core and, in a second file, this prediction; the rubric core stays exactly the eight fields the publication gate validates. One prediction per assessor per bundle; the assessor is not an author.
- The measurement's prediction median and spread are derived by the harness for `round-assess`, `status --summary`, and the round packet. They are recorded at the end of every round as a reference reading of where the paper stands; no gate rule depends on them and no goal names them. `reviews/score_history.jsonl` lines gain `predictions` (the three percentiles) and `prediction_median`; RUBRIC.md and WORKSPACE.md describe the added file.

### 5.6 Development disposition `next_round`

`development.alternatives[].disposition` and `development.branches[].disposition` accept `next_round` beside `pursue`, `not_useful`, `resolved`, `budget_paused`. A `next_round` item requires a reason and evidence like the others. It leaves no `useful_development_remaining` obligation, so readiness and writing proceed; it is a carried development that the next `round` decision must dispose of (section 5.1). The readiness reviewer sees carried items in the `development` check as today.

### 5.7 Objective lineage across rounds

- `objective_lineage/{objective id}` is an immutable record `{id, predecessor, containment, round_id}` written by `round-admit`; `research_objective/{objective id}` stays identical to the objective it records, so the configuration check that the target equals its `research_objective` record is unchanged. The lineage is followed from the configured objective through `objective_lineage` predecessors. The configuration target is the latest.
- A widened objective changes the configuration digest that every synthesis section binds (section 7), so the four sections report `synthesis_dependencies_stale` until the round's literature stage re-records them.
- `plan_cycle` still requires `value["objective"]` to equal the current objective. `_inheritance` accepts a predecessor checkpoint whose objective is the current objective or a recorded ancestor of it; the inheritance entry keeps that checkpoint's original scope and assumptions. `_candidate` and `_scope` use the current objective; a partial scope's `remaining_obligations` may name obligations from any ancestor.
- Strategy accounts keep their key (objective, scope, mechanism, test). A widened objective therefore opens new accounts; the earlier accounts, failures and charges stay retained and visible to reopening checks.

## 6. Gate `round` and stage transitions

`gate round` (new in `GATES`) evaluates on the current snapshot:

1. The current publication bundle exists (`publication_bundle_missing` otherwise).
2. When the current round has number 2 or more: its `round_assessment` binds this bundle (`round_assessment_missing`); the round's productivity obligations are met (section 7 and 9: `round_search_missing` per consequence purpose, `round_exemplar_missing`, `round_cycle_missing`, `round_claim_missing`, `round_claims_dropped`).
3. A `round` decision binds this bundle (`round_decision_missing`) and an approving `round_review` binds that decision (`round_review_missing`, `round_review_pending`).
4. For `continue`: `resource_budget_exhausted` for the `development` purpose, `round_goal_repeated`, `round_direction_exhausted` as in section 5.1.
5. The report carries `decision`, `round` (the closing round), `next` (the proposed round), and `admitted` (whether `round-admit` was recorded for the decision).

`validate_transition` in `gates.py` gains two rules:

- `evaluate -> literature` (a backward transition today) requires `gate round` ready with `decision: continue` and an admission for that decision (`readiness_required` with the gate's obligations otherwise). The other backward transitions (`evaluate -> experiment`, `evaluate -> write`, and returns to `literature` from `ideate`, `experiment`, `write`) stay as they are for in-round evidence gaps and refreshes.
- `evaluate -> deposit` requires, beside the `publication` gate, `gate round` ready with `decision: stop`.

`exactory-lab state set` keeps its arguments. `enforce_decision_log` keeps its rule: a stage closes only with a logged decision; the round decision is logged with `exactory-lab decide --stage evaluate` as today.

The study projection (`.exactory/study.json`) is unchanged; `status` and `status --summary` report a `round` object computed from the records (section 14).

## 7. Literature in a development round

The first round reads to position the work in its field. A later round reads to trace the consequences of the paper. The scope of that reading is narrower because the question is narrower; within the question the rules are the same.

`literature.py` gains `DEVELOPMENT_PURPOSES = ("downstream", "next_step", "exemplars", "changes")` beside `SEARCH_PURPOSES`. When the current round has number 2 or more, `foundation_state` requires, for the research profile, a selected search for each development purpose whose record was made after the round's admission revision (`round_search_missing` with the purpose named). The five original purposes keep their existing rules: their judgments go stale only when the scope, the works they rest on, or the frontier changes, and a re-judgment reuses the captured responses with carried-forward findings.

| Purpose | Question | What the record establishes |
| --- | --- | --- |
| `downstream` | Who is blocked by what the paper does not yet do? | Found works whose stated bottleneck the goal addresses; each with a disposition and the passage that states the bottleneck. This is the demand evidence the goal cites. |
| `next_step` | Has the next step already been taken? | Prior art for the round's new claim. A `scooped` verdict withdraws the goal (section 10). The first round's novelty judgments are not reused for the new claim. |
| `exemplars` | How were comparable first results developed into larger contributions? | At least one found work selected with `require-fulltext` under the new purpose `exemplar` and therefore read in full, whose development from a first result to a larger contribution is analyzed like an innovation case: original constraint, conceptual change, evidence, transfer assumptions, distinguishing test, failure signal. `round_exemplar_missing` until a requirement recorded in this round exists; the existing `fulltext_reading_missing` then requires the reading. |
| `changes` | What changed since the previous round? | New results, corrections, contradictions, and reactions to the paper's line since the previous round's searches; `contradicted` returns the affected claims to a cycle. |

Synthesis in a later round:

- `rationale` and `context` are re-recorded for the round after its searches: the `But` names the bottleneck the goal addresses, and `context` names the round's beneficiaries. The existing dependency rules already stale them when the frontier changes; the round adds no separate rule.
- `standards` is inherited; it goes stale when the population changes, as today, and when the round widened the objective.
- `innovation` is inherited; the exemplar reading is recorded as one more case in a re-recorded innovation section, which the existing rule requires anyway once the frontier changed. The five-to-ten external families are not read again.
- A widened objective (section 5.7) changes the configuration digest that every synthesis section binds, so a widening round re-records all four sections, `standards` and `innovation` included, in its literature stage. Those are four bounded records per widening round. The binding stays as it is because a narrower binding would mark the standards of every existing study stale.
- The cohort is not collected again. A `field_change` goal collects the difference: `collect` for the added category over the study's window, in which members already inventoried by the study's collections are linked to their existing readings and only the new members owe abstract readings under the recorded preparation policy. The added members join the population that `standards` binds, so `standards` is re-recorded once. A move to another corpus, or to a category whose difference the round's literature allowance cannot cover, is refused at admission; a large migration is not a development round.

## 8. Ideate and experiment in a development round

- `ideate` reads the admitted goal, the carried developments it pursues, and the `downstream` and `next_step` records before forming hypotheses. Every hypothesis of the round states which success criterion it serves.
- Cycles are planned as successors: `predecessor` is a checkpoint of an earlier round where the hypothesis builds on one, with `inheritance` naming the inherited result and its assumptions, or `null` for a fresh line under the round's objective. Objective lineage (section 5.7) makes cross-round inheritance valid.
- `assess` is unchanged except for the `next_round` disposition. A useful development that exceeds the round's admitted resources or scope is carried, not paused.
- `round_cycle_missing`: the round gate requires at least one cycle planned after the round's admission with a recorded assessment. Manuscript work alone does not close a round.
- Readiness review before writing is unchanged: the candidate's assessment disposes of every retained cycle of every round, with the earlier rounds' cycles `resolved` by their own assessments.

## 9. Manuscript in a development round

- The paper is one paper. `draft/` grows in place; the manuscript loop's git commits and `versions/` snapshots continue; the bundle at each round gate is tagged in the study repository as `round-N` when the study keeps a repository.
- Claims continuity. The round gate in round N (N of 2 or more) compares the current bundle's `evidence/claims.json` with the round's opening bundle: every opening claim id is present with the same text, or present with `revised: {previous, reason}`, or present with `superseded: {reason}`. A claim that disappears is `round_claims_dropped`. At least one claim id not in the opening bundle exists (`round_claim_missing` otherwise). Both are round-gate obligations, not manuscript-pin refusals, so the manuscript loop can still measure an intermediate draft.
- Measurement. Each iteration's three blind reviewers return the rubric core and a `manuscript-prediction`. The manuscript packet stays neutral: no round number, no earlier reviews, no history; the paper carries no revision markers, as today.
- Publication gate. Unchanged: two accepting blind reviews of the exact bundle. A `stop` decision with a bundle that does not pass the publication gate leaves the study where it is today: the plateau is recorded and the study parks for the user.

## 10. How the loop ends

Every path out of the loop is a record. The table is complete; there is no other exit.

| Exit | Where it is recorded | What follows |
| --- | --- | --- |
| Stop approved | `round` with `decision: stop`, approving `round_review` | `deposit` when the publication gate passes; otherwise the study parks with the plateau recorded. |
| Goal withdrawn in literature | `next_step` search with verdict `scooped`, or `changes` with `contradicted`, recorded in the active round | The round is assessed `unsuccessful` with the withdrawal as evidence; the next `round` decision proposes a distinct goal or stops. A round budget is charged for the round. |
| Stop condition observed | `round-assess` with a stop condition `observed` | The next `round` decision proposes a distinct goal or stops. |
| Round unsuccessful twice in a row | `round_direction_exhausted` on the next `continue` | Only a goal in the other direction or a `reopening` with changed evidence can continue; otherwise stop. |
| Round budget exhausted, when the user recorded a `development` budget | `resource_budget_exhausted` for the `development` purpose | Checkpoint and stop, or the user raises the budget with a reason. |
| Both directions exhausted | `round_direction_exhausted` in both directions without changed evidence | `continue` is refused; stop. |
| Purpose or experiment budget exhausted inside a round | Existing `resource_budget_exhausted`, `strategy_budget_exhausted` | Existing behavior: checkpoint, raise with a reason, narrow, or pause. |
| Independent assessor unavailable | `round_review_missing` stays pending | Existing behavior for unavailable reviewers: the study parks. |
| Autopilot cap | `continue_autopilot` at `EXACTORY_AUTOPILOT_MAX` advances per user turn | Existing behavior: summary and a new budget on the next user turn. Unchanged by this design. |

A `continue` that is not backed by new evidence cannot happen: the round gate requires the assessment of the closing round, which requires the round's searches, exemplar, cycle and new claim. A round spent only on manuscript revision is recorded unproductive and counts toward `round_direction_exhausted`.

## 11. Independent round review

`export --kind round --destination PATH` writes a packet for the round assessor. Unlike the manuscript packet, this packet deliberately carries history, because the assessor judges the research program, not the paper's quality:

- the exact current manuscript files and claim evidence (as the manuscript packet);
- every blind review and prediction of this bundle, with medians and spreads;
- the `round` decision under review, with its candidates, carried items and goal;
- every earlier round's goal, assessment and decision;
- the cycle assessments' `development` blocks of the closing round;
- the synthesis `context` and `innovation` sections and the `downstream` and `next_step` records where they exist;
- the resource accounts and the `development` budget.

It excludes author names, request identities, launcher tokens and revision labels, through the same `scrub` the other packets use. The assessor's provenance and independence basis are recorded with the review; an author of a cycle cannot review the round.

The checks, restated as questions for the assessor:

| Check | Question |
| --- | --- |
| `impact` | Does the goal name something concrete the field could do after the round that it cannot do with the current paper, and is that named with evidence rather than asserted? |
| `demand` | Are the beneficiaries real, as shown by sources whose stated bottleneck this addresses, by Grand Challenges, or by review findings, and not invented to satisfy the form? |
| `novelty_risk` | As far as the current sources show, is the next step still open, and does the goal state how it will be tested for prior art before experiments? |
| `feasibility` | Can the route reach the success criteria within the stated resources, from where the study stands? |
| `distinctness` | Is the goal different from earlier rounds' goals and from rejected candidates, or is a reopening justified by changed evidence? |
| `continuity` | Does the round build on the previous rounds' products in full, keeping their claims, evidence and readings in use, rather than replacing them or migrating the study elsewhere? |
| `stop` | For a stop: were the candidates and carried items judged fairly against the paper's evidence and reviews? |

## 12. Resources

- The number of rounds is an outcome of the gate, not a design parameter: rounds continue while a goal with value passes the gate, and no default round count exists. `resources.PURPOSES` gains `development` and `UNITS` gains `rounds` so that a user who wants a ceiling can record one with `budget` (purpose `development`, a `rounds` limit and a reason; every budget payload now names `rounds` beside the seven existing units); `round-admit` then charges one round, and charged at the limit is `resource_budget_exhausted` for the `development` purpose, an obligation and a checkpoint condition, never readiness. Without a recorded budget the loop is bounded by the direction rule (section 10), the goal distinctness rule, the literature and experiment budgets, and the autopilot cap.
- A round's `resource_limits` set per-purpose allowances for its `literature` and `experiment` work. They are recorded on the admission and reported by `status --summary`; existing accounts keep charging as today, and a round's usage is derived as the difference of the accounts since admission.
- A `field_change` round states the expected size of the category difference from `exactory-cohort freeze` and the collection's first page, and its literature allowance covers it; the admission refuses a field change without room or outside the study's corpus.

## 13. Cost per round

What a round costs beyond the research it performs, in harness terms:

| Item | Per round | Growth with corpus size |
| --- | --- | --- |
| Records | `round`, `round_review`, `round_admission`, `round_assessment`: four events | None |
| Searches | Four captured consequence searches; the five original purposes re-judged only if the scope changed, reusing captured responses | None beyond the found works |
| Readings | New sources the consequence searches select; at least one exemplar in full; nothing already read is read again | Proportional to new sources only |
| Synthesis | `rationale`, `context`, `innovation` re-recorded once after the round's searches (existing dependency rule) | None |
| Reviews | The manuscript loop's three reviews per iteration and the two gate reviews, unchanged; one round review at each gate | None |
| Predictions | Zero additional calls: the same blind reviewer writes a second file | None |
| Reports | `status --summary` adds one `round` object under the existing 16 KiB bound | None |
| Evaluation | The round gate runs inside the same `Evaluation`; no new replay | None |

The round packet copies the manuscript files and the review JSON; sources are referenced by their existing artifacts as in the manuscript packet.

## 14. Reports

- `status` and `status --summary` add `round`: `number`, `active`, `decision`, `admitted`, `assessed`, the current round's counts (searches, readings, cycles, new claims since admission), the latest measurement's review median and prediction median, and the `development` budget line. All strings are bounded as the other summary fields are.
- At the `evaluate` stage the status obligations also carry the publication gate's and the round gate's obligations, and `PRIORITY` places the publication obligations before the round obligations, so a study at `evaluate` is led first to finish the manuscript measurement, then to the round decision. Before `evaluate` the round gate adds nothing to the status obligations.
- `gate round` prints the report of section 6.

## 15. Hosts, skills, documentation

Files to edit, listed for the user's explicit permission (skills are the project's standard):

- `RESEARCH_CONSTITUTION.md`: Version 3 with the section in 16.
- `skills/ai-science/SKILL.md` (stage table and the round loop), `LOOP.md` (a third section, "Develop the paper across rounds"), `STUDY.md` (round records and projection).
- `skills/literature-review/SKILL.md` (consequence purposes and the exemplar reading), `skills/ideate/SKILL.md` (goal and carried developments), `skills/experiment/SKILL.md` (`next_round`), `skills/write/SKILL.md` and `WORKSPACE.md` (claims continuity, `score_history.jsonl` predictions), `skills/evaluate/SKILL.md` and `RUBRIC.md` (the prediction file beside the rubric core, the round packet), `skills/deposit/SKILL.md` (the `stop` decision before deposit).
- `docs/research-workflow.md` (a section between manuscript assessment and publication), `docs/research-cli.md` (five operations, one gate, one export kind, the `next_round` disposition, the `development` budget), `docs/research-cli-examples.json`, `docs/releases/0.39.0.md`, `README.md`, `codex/README.md`.
- `codex/generate.py` regenerates the Codex skill entrypoints; no hook changes.
- Version 0.39.0 in both manifests, the test literals, and the README.

## 16. Constitution Version 3: the added section

Draft text, at the level the constitution keeps:

> ## Development across rounds
>
> A study produces one paper and develops it across rounds. Each round begins from the paper as it stands and ends with a paper that contains what the earlier rounds established; intermediate states are not published. A round is authorized by a goal that states what the field will be able to do afterwards, for whom, in which direction, with the criteria that recognize success and the conditions that end the round early. An independent assessor judges the goal before the round and judges the decision to stop before publication. Every round performs new investigation of the paper's consequences and adds evidence and claims; a round spent on presentation alone does not count as development. Stopping on evidence is a correct outcome and is recorded as one. A measurement target is a stopping condition for the author, never an instruction to a reviewer.

Adopting Version 3 follows the existing path: an existing study reports `constitution_revalidation_required` and adopts it with `constitution` and the previous SHA-256.

## 17. Acceptance, measurement, release

- Tests first, in the plugin's `unittest` style with synthetic fixtures (`development_fixtures.DevelopmentCase` extended with a pinned bundle, reviews and predictions). The validation document lists the cases (R01 to R25).
- Existing tests stay enabled and unchanged in meaning; the six-check readiness review, the publication gate, and every 0.38.0 case keep passing.
- Measurement follows `misc/harness-improvement/v3/validation.md` in the exactory repository: the outcome targets are measured on real studies and reported as measured.
- Release follows the 0.38.0 procedure: a feature branch, a two-reviewer pass (record integrity and specification conformance), the release commit, PR to `main`, CI on three Python versions, fast-forward of `local-dev` and `dev`, annotated tag `exactory--v0.39.0`, GitHub release with absolute links, no marketplace change.
- Upgrade: an existing study reports `constitution_revalidation_required`; a study at `evaluate` reports `round_decision_missing` before `deposit`; a study before `evaluate` sees no new obligation until it reaches the round gate.

## Out of scope

- Publishing intermediate versions of the paper on Zenodo; deposit happens once, after `stop`. A later `--new-version` deposit of a published paper stays a manual choice outside the round loop.
- Changing the rubric core, the reviewer counts, or the manuscript loop's saturation rule.
- Reading other verifiers' verdicts, or any market signal that requires a published record, as an input to the round gate. Grand Challenges and recorded next steps stay available as they are for `ideate`.
- Screening policy changes; a `field_change` cohort uses the study's recorded policy.
- Automatic search fetching; searches stay agent-captured and harness-validated.
- Adopting `boed-mean-field` into the managed harness; its records are used as a reference in the validation, not as a live study.

## Decisions of 2026-09-12

The user answered the ten questions of the first draft as follows, and delegated everything else to the designer's recommendation.

1. and 2. The number of rounds and the paper's extent are results of the process, medians of what it produces. Neither enters the design: no default round count, no reference or page target, and no score or prediction criterion in a goal. The validation document measures them.
3. The round assessor is a fresh subagent with the round packet, recorded with provenance; a human assessor remains possible.
4. Blind predictions are recorded at the end of every round as reference values. The loop's decisions may consult them but never depend on them alone.
5. A round may widen the objective as long as the widening has value, through `round-admit` with recorded containment.
6. Every round continues on the premise that the previous rounds' products stay in full use; a large migration is not a round. A field change within that premise collects the cohort difference against the existing cohort, not a second cohort.
7. The round gate is mandatory for every study.
8. The closed-gravity study is the first live study to reach the round gate under 0.39.0.
9. The names `round`, `round-review`, `round-admit`, `round-assess`, `manuscript-prediction` stand.
10. The skill and documentation edits in section 15 are permitted.
