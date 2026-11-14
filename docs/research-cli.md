# Research CLI

## Scientific contribution decisions

Managed studies and drafts now use one phase-specific decision at objective,
cycle, writing, publication and round boundaries. Read the full
[decision contract](research-decisions.md) for scientific judgments, source
changes, stable work items, migration and reviewer-context limits. A bare shared
research profile used by native mathematics does not activate these strategic
gates. Historical records remain readable and supply no fabricated current
independence.

The [shared tools](research-tools.md) are available at every stage with explicit
depth and budget. Each call records actual work; the orchestration holds gates
for direction, investigation, result, manuscript and publication claims.

### Direction gate

`gate direction` requires current configuration and a selected research
decision with no obligations at the `cycle` boundary, the same authority used
for cycle planning and admission. It governs completing `literature` and
entering `ideate` in managed research. Its report carries the configuration
and decision obligations. Before any cycle decision exists, it also lists the
direction minimum's missing search, full readings, standards and Grand Challenge
evidence. This lets `status` and `next` name the missing evidence during
intake. Other stores receive their preparation report.

New decisions carry `gate_version: 2`. Recording `investigate` or `pivot`
requires a scoped valid direct search with bounded or complete coverage and all
captured results accounted; full readings of relevant/contradictory hits and
roots; selected standards with valid own sources and resolved applicability;
and a current Grand Challenge record. Missing evidence returns
`direction_minimum_unmet`. The [decision contract](research-decisions.md#before-target-commitment)
lists `direction_search_missing`, `direction_search_incomplete`,
`direction_reading_missing`, `direction_root_reading_missing`,
`direction_standards_missing`, `direction_standards_unresolved` and
`grand_challenge_missing`.

Each reviewer must receive the decisive source content and claim in the
request of the exact authenticated attempt that produced its review: its
current packet and the earlier packets of its session that the request
replays. Coverage requires every inspected range and every image asset named
by its inspection. Long text ranges are delivered in consecutive chunks of at
most 2,000 characters; PDF inspections use the checked original within the
64 MiB per-artifact delivery bound. A qualifying historical attachment retains
coverage when the request carried it. A descriptor, an omission marker, an
inventory entry, a reading note, an assignment date, a saved packet that the
request did not carry or an artifact present only in the store supplies no
delivered-source coverage, and a delivery that cannot be authenticated or
decoded credits nothing. Missing ranges or image bytes leave
`direction_evidence_unassessed`, with the uncovered fields in `missing` and
the undelivered readings in `reading_ids`. The decision stores
`direction_evidence_digest`; changed decisive evidence reports
`direction_evidence_unassessed` and requires a new decision with reviews that
saw it. Target commitment applies that same minimum and review-coverage rule.

Within one command, request selection and authentication reuse derived text
requests only when the complete route, prompt, packet, ordered history, fit order
and local input limit match. Inputs are read and verified before reuse, and each
recorded request still receives the full authentication checks. Exact compressed
requests use a protected LRU capped at half the evaluation's existing 64 MiB
optional retention budget; other entries borrow unused space and cannot evict
requests. Oversized or unencodable requests are computed without retention, and
no retained state survives the command. Visual requests still check current
emitter bytes on every build, and staged reviews keep their existing path.

For version-2 `investigate`, `pivot` and `develop_manuscript`, both reviews
pass `q1_venue`. Missing findings report `q1_venue_missing`; a failed or
unresolved finding reports `research_assurance_pending`. The bar defines
field/intent standards, slate reviews judge potential, and result reviews judge
realized supported advance. Previous prompt assignments retain their original
assurance set; historical decisions keep their standing. `deliver_requested`
keeps its stop checks, authority and explicit quality conditions.

All new mutations use the normal `--file`, `--expected-revision` and
`--request-id` contract. `example OPERATION` provides their complete schemas.

| Operation | Purpose |
| --- | --- |
| `intent` | Version the actual user instruction, full aim, branch, publication endpoint and resource authority. |
| `strategy` | Freeze a comparative prospective, result or post-measurement dossier, exact dependencies and a bounded question. |
| `work-item` | Retain one stable reviewer request or development obligation, classification, actual execution state and lineage. |
| `research-lead` | Preserve an earlier discrepancy and its original provenance, verification status and next check. |
| `review-route` | Register the explicit instrumented provider, model and resource configuration: `claude_cli_v1` at `claude://local` (the reviewer of a Claude Code session, run as an isolated `claude -p` process on the owner's subscription), `codex_cli_v1` at `codex://local` (the reviewer of a Codex session), `codex_cli_framed_v1` at `codex://local` (an isolated versioned route for lossless openness evidence framing), `codex_cli_visual_v1` at `codex://local` (a Codex route that also receives the rendered pages of a required PDF inspection as images), or `openai_responses_v1` at an HTTPS endpoint with an API credential. The record pins the runtime of the installed adapter. Probes and attempts verify against that pin, so a review bound earlier stays verifiable after the adapter changes. A run or a probe on a route whose pin differs from the installed adapter is refused with `review_route_runtime_stale` before any call. Record and probe a new route then. Since 0.75.0 a new probe, invocation and assignment on `codex_cli_v1` or `codex_cli_framed_v1` record `codex_invocation: "codex-invocation-settings-v1"`. Every observation of a call on these routes (an attempt, an output correction or a staged task) records the field too. The child assignment that runs a staged task does not record the field. The response of such a call retains the native rollout of the call under `codex_invocation.native_rollout`, read after the call returns. A response that holds the field authenticates only through the retained rollout. A probe whose record carries the field authenticates only when its response holds the rollout. The same holds for an ordinary attempt whose assignment or attempt record carries the field, and for a staged task whose observation carries it. The rollout must have the recorded `rollout_digest` and hold one `session_meta` and one `turn_context`. The turn's `model` must be the route's model and its `effort` must be `high`. The inspection of the retained rollout must give the recorded `context_evidence` again. The answer in the response must be the assistant output of the rollout. When the rollout cannot be read after the call, the response records the error under `codex_invocation.error` instead, and the call does not authenticate. A record without the field keeps its earlier authentication. A record whose `codex_invocation` is another value authenticates nothing. A `status` read or a review preparation observes the installed runtime of the adapters in use at its start and at its end; when it changed in between, the command refuses with `review_runtime_changed` and writes nothing, and the same command run again reads the current runtime. |
| `review-probe` | Execute and retain the input-boundary positive-control test. A failed test establishes no independence. The probe binds to the route's pinned runtime; after an adapter change, probe a new route instead of the old one. A probe interrupted after the reviewer answered is finished or reconciled as an interrupted `review-run` is. |
| `review-assignment` | Freeze the canonical role, exact allowed packet, reviewer identity and current verified route. When a post closes the chain of a new assignment, the assignment is refused with `problem_already_posted` ([Problem creation](#problem-creation)). A packet whose request does not fit one input of a local text route, or whose fitted request withholds an evidence body, is delivered as a series of fresh-input turns when an adapter applies to its role (a problem `result` or `adjudicator` packet, a context repair, a `verification` or readiness `result` packet, a `native_math` packet without an openness minimum; [Reviews in fresh parts](#reviews-in-fresh-parts)) on a `claude_cli_v1`, `codex_cli_v1` or `codex_cli_framed_v1` route with a local input limit; an admitted adjudicator repair on such a route enters parts whatever its size. The next turn is assigned with a new id, the same role, dossier, route, reviewer and author, and the last turn as `context: {prior_assignment_id}`, and the assignment result carries `review_parts_plan`. A recorded 0.69.x result series continues in parts under its own protocol ([Problem evidence in parts](#problem-evidence-in-parts)). A staged review starts only through `context: {staged_upgrade: {assignment_id}}`, which opens one in a recorded slot; `{controller_assignment_id}` alone then plans and assigns its next task and returns the next command, `context: {controller_assignment_id, task_id}` records the child that runs one of its tasks, and `scheduler: {store_limit, route_limits}` records the reviewer capacity ([Staged problem reviews](#staged-problem-reviews)). A turn of a recorded 0.69.x series that the v1 part planner refuses with `review_input_too_large` can instead freeze a reduced replay history of completed earlier series, and since 0.73.0, when the planner refuses the reduced history too, starts one parts v2 successor in the same slot ([Problem evidence in parts](#problem-evidence-in-parts)). Since 0.71.0 an ordinary assignment, one that is neither on a `codex_cli_visual_v1` route nor the controller or a task of a staged review nor a turn of a parts v2 series (whose `history` is `[]`), stores its replayed history as `history_ref` instead of `history`: a version 1 root with `version`, `message_count`, `history_digest` and the ordered `pages`, each page an immutable artifact with `version`, `start` and at most 32 `messages`, and each message with `body` (an artifact that holds the exact content, UTF-8 text or JSON for a native content array), `metadata` (the other fields of the message), `source` (`assignment_id`, `message_index`) and `occurrence` (`session_id`, `index`); the pages and bodies of an unchanged prefix are shared between the turns of a session. Every command reads either form, and the reviewer receives the identical history. A record that carries both fields is refused with `review_history_ambiguous`, and a page or body that does not match its root with `review_history_invalid`, or with `artifact_missing` or `artifact_corrupt` when its artifact is gone or altered; either code means that the artifacts of the store are damaged or a record was edited, so stop and report it. |
| `review-run` | Execute that assignment once, retaining the output, the usage and the failed or interrupted attempts. When a post closes the chain of the assignment, `review-run` is refused with `problem_already_posted`, except for a replay, an acceptance and a reconciliation ([Problem creation](#problem-creation)). When the reviewer answers, the run commits the answer as one immutable record, `review_call_observation`, before the write that records the run: this capture binds the operation and the invocation, the original request ID and the digest of the payload, the route and the request, the reservation and the calling process, and holds the exact observation with its artifacts. The write that records the run reads the capture and is tried again after `store_busy`, `stale_revision` or `review_task_dependency_changed`, at most 600 times within 600 seconds, without a second call. The run that waited exits with the last of those refusals, and the capture stays. A run whose process ended after the capture committed, or whose write is still refused after that wait, is finished by repeating `review-run` with the same payload and the original request ID: that returns the recorded receipt when one exists and otherwise records the run from the capture, without calling the reviewer again. Until then the invocation is pending: a `review-run` of the assignment under another `id` refuses with `review_invocation_pending` and names that step, and `review-attempt` refuses an import under the run's `id`. A run whose process ended before the capture committed leaves an unknown outcome and the invocation pending: a replay with the original request ID and every later `review-run` of the assignment refuse with `review_invocation_pending`, and the refusals say to reconcile it. `review-run` with `{"action": "reconcile", "operation": OPERATION, "invocation_id": ID}`, where the operation is `review.invoke` for a run, `review.route_probe` for a probe or `review.output_correction` for a correction and the id is the `id` of the original payload, releases the capacity of the call once its process and its transport processes have ended; for a call without a capture it also records the unknown outcome as a `failed` attempt, probe or correction with the error `review_invocation_interrupted`, which ends the pending state. For a run that 0.69.x started, which has no reservation, the step is `review-attempt` under the run's `id`: a `failed` attempt with `output: null`, a reason and a `usage` object with null counters (the [route instructions](research-decisions.md#commands-and-shared-state) give the payload), and the refusal carries that payload under `next`. After either step, a `review-run` of the assignment under a new `id` and request ID is permitted. After a refused output, one retry with the same request is permitted. On the Claude route, an answer that withdraws earlier objects, each followed by a line that is exactly `RETRACTED`, is read to its last object. The whole text stays in the response as evidence. Prose or any other content outside the blocks refuses that reading, and the other routes refuse an answer with more than one object. The Claude route also retains every system event of the stream, up to 4,096 events and 4 MiB. A refused context can then be classified again offline with `inspect_retained_system_events` of `research_harness.review_claude_transport`; there is no command for it, and it changes no recorded result. For a part turn of a problem result series ([Problem evidence in parts](#problem-evidence-in-parts)), `review-run` checks the part response when an authenticated run completes and writes the output refusal `invalid_value_review` under `review.shape` when it fails. For a turn of a parts v2 series ([Reviews in fresh parts](#reviews-in-fresh-parts)) it checks a read, combine or check output and the request form of a final the same way, under `review.shape` whatever the role, with `invalid_value_review` for a contract failure and `review_parts_request_no_progress` for a request that makes no progress. For a task of a staged review, `review-run {assignment_id, epoch_id, task_id}` reserves the task, runs it through an owned worker, accepts its committed capture and reconciles the worker; `action: "accept"` resumes an acceptance and `action: "reconcile"` a reconciliation, neither with a reviewer call; a refused capture permits one retry with the same request, and the result names `review-assignment {controller_assignment_id}` as the next step ([Staged problem reviews](#staged-problem-reviews)). See the [reviewer delivery contract](research-decisions.md#reviewer-delivery-and-disagreement) and the [route instructions](research-decisions.md#commands-and-shared-state). |
| `review-series` | `--assignment HEAD`, where any assignment of the chain resolves to the chain's current head, and `--workspace` (default `.`). Execute the planned actions of one review chain in process, through the same operations as the CLI, until the chain is done or needs the agent. It takes no `--file`, `--expected-revision` or `--request-id`, and has no turn limit. `--check` is refused with `check_unsupported` before any workspace or model effect. Every `stop` and `wait` line with a `code` carries `stop_class`, and the last line of a run that ends in a `stop` carries `stop_id`, the id of the `review_series_stop` record that the driver writes for that stop, with `revision_after_stop`, or `stop_record_error`. [Review chains and `review-series`](#review-chains-and-review-series) states the plan of a chain, the exit statuses, the handoffs, the outcomes of a failed reviewer call and the stop record. |
| `review-attempt` | Preserve imported output or failure history; imported assertions never establish verified independence. |
| `review-context` | Record evidence of later contamination or other context observations, or retire a route: `kind: "route_retired"` on an assignment of that route, with the owner's instruction as evidence. A retired route accepts no new assignment or run, an assignment on it without an observed output is dead, and a preserved slot continues through a context repair that names the retirement as its cause. |
| `review-adjudication` | Record the exact observed focused finding. An output that fails its output contract leaves a refusal record. The finding's citations are checked against the delivery of the exact authenticated attempt that produced it, the packets that its request carried, and not against the saved packets of its lineage ([Problem creation](#problem-creation)). A turn of a parts v2 series that is not the final of a complete series, or a final that returned a request, is refused with `review_parts_pending` ([Reviews in fresh parts](#reviews-in-fresh-parts)). |
| `review-output-correction` | `{id, assignment_id, route_id}`. When a post closes the chain of the assignment, a new correction is refused with `problem_already_posted` ([Problem creation](#problem-creation)). For a problem result reassessment whose two completed, authenticated outputs both have recorded refusals, the latest for `review_reassessment_mismatch`: when relabeling the latest output's `confirmed` findings whose content changed as `revised` would satisfy its output contract, ask the same reviewer once for that relabeling. Use a verified route with the original endpoint, model and adapter; a `codex_cli_visual_v1` assignment can use `codex_cli_v1` instead. The reviewer returns only those dispositions and reasons, or declines; every other value of the output is kept, and `value-review` then records the composed output with its `output_correction_id`. It is not a new review and grants no further sample. On a `codex_cli_v1` route the native rollout of the correction turn is read once, when the answer is captured, and retained in the capture, so a correction finished after an interruption authenticates against the retained rollout. An interrupted correction is finished or reconciled as an interrupted `review-run` is. |
| `review-budget` | `{max_calls, reason}`. Record the optional store-wide allowance of reviewer calls as `review_budget/store`: `max_calls` is a nonnegative integer, or null for no limit, and `reason` is nonempty text. A store without the record behaves as before. With it, the start of every ordinary `review-run`, `review-probe` and `review-output-correction`, and every reservation of a staged task, is refused with `review_budget_exhausted` when no call remains; the check runs in the transaction that records the start, together with the capacity admission ([Staged problem reviews](#staged-problem-reviews)), so two processes cannot both take the last call. Each admission counts once: a staged reservation and its accepted attempt count once, a replay, a capture, an acceptance and a reconciliation add nothing, and a failed or unknown outcome stays counted. An allowance below the calls already admitted is refused with `review_budget_below_charged`, whose details name the smallest allowance permitted as `minimum_max_calls` and a `review-budget` command under `recovery`; raising the allowance keeps the expenditure. With an allowance set, the ID of an imported call cannot admit a new call (`record_conflict`). `intermediate/reviews/usage.json` shows the allowance, the charged calls and the remaining calls under `review_budget`. The allowance counts calls, not money, and review usage stays outside the preparation budgets of `budget`. `--check` accepts or refuses without writing. |
| `value-review` | Record the exact observed bar, slate or combined support/value result. An output that fails its output contract leaves a refusal record. The output's citations and bars are checked against the delivery of the exact authenticated attempt that produced it, the packets that its request carried, and not against the saved packets of its lineage ([Problem creation](#problem-creation)). A part turn of a problem result series is refused with `problem_evidence_part_pending` and leaves no refusal record. Of a parts v2 series, record only the final turn that returned the review; a read, combine or check turn and a final that returned a request carry no review ([Reviews in fresh parts](#reviews-in-fresh-parts)). A staged review is recorded from its controller's effective result; an incomplete assessment is refused with `assessment_incomplete`. |
| `research-decision` | Select the canonical scientific action, artifact disposition and full-goal status. |
| `source-impact` | Bind an explicit impact classification for the complete source delta since the selected decision. |

`research-decision-assess --boundary target|cycle|write|publication|round`
returns the same obligations consumed by direct mutations and gates. It accepts
an optional `--decision-id`. `strategy-packet --dossier-id ID --role ROLE
--reviewer-id ID` exposes the exact role packet; additional explicit role context
uses `--context-file`. `review-route-assess --route-id ID` reports the current
route test. These reports change no revision. `strategy-packet` can store a file
in the artifact store while it builds the packet, for example a projected claim
file, so it accepts `--check` and refuses it with `check_unsupported` before it
opens the workspace. The other two only read.

An initial `bar` assignment can supply `context.source_links`, using the exact
source Link shape in `example read`. These links select previously inspected
passages with `text`, `span` or `json` locators for the assessor's initial context.
Request visual originals through `source_requests`; receipt is not proof of
visual inspection. The complete held-source inventory
is bound separately and available through paged requests; metadata alone does
not establish source delivery or reading. Replace every placeholder in
`example review-assignment` with an actual saved source and locator.

A `bar`, `slate` or `result` response can include `source_requests` for an `inventory_page`, an
`inventory_query`, an exact `source`, or, under the excerpt rules below, a `passage`. The
[reviewer delivery contract](research-decisions.md#reviewer-delivery-and-disagreement)
gives the request fields and unresolved-source rules. Read and retain the observed
output artifact returned by `review-run`, then continue its pending request with
a new assignment with the same role, dossier, route and reviewer:

```json
{
  "id": "assignment-bar-a-source-1",
  "route_id": "review-route-1",
  "dossier_id": "dossier-1",
  "role": "bar",
  "reviewer_id": "reviewer-a",
  "author_id": "author",
  "context": {"prior_assignment_id": "assignment-bar-a"}
}
```

Invoke the new assignment with `review-run`. The harness supplies the requested
native evidence and preserves that assessor's own history. Keep all ancestor
source gaps and charge the continuation to the same allowance. A request response
remains unresolved and cannot be recorded with `value-review`; a final phase
assessment cannot use this interface to obtain a replacement answer.

The requested full texts of a session can be longer together than one input of
the route. A continuation (`prior_assignment_id`) or a context repair therefore
carries the excerpt rules: its prompt adds them under the same template id, and
its packet carries `reviewer_excerpts`. An initial assignment has no excerpt
rules. An assignment recorded before these rules keeps its prompt and stays
authenticated, and the rules create no refresh, calibration or regrade obligation
for a recorded bar, slate or result review. Under the excerpt rules:

- A response can carry `excerpts: [{version_id, text, purpose}]`. Each `text` is
  a verbatim copy from a delivered text, and `purpose` says why the reviewer keeps
  it. The next continuation checks each excerpt against the stored extracted text
  of its exact version: the main full-text capture, or the abstract capture when
  no full text is held. In both texts each character becomes its NFKC form and
  each run of white space becomes one space, and the first occurrence counts. A
  match of at least 20 normalized characters and at most 6,000 original
  characters is `verified`: the packet delivers the original characters, not the
  reviewer's copy, with a locator `{kind: "span", start, end, sha256}` and the
  descriptor of the stored text. Verified excerpts have a total of 200,000
  characters in a session, counted in output order; an excerpt beyond it is
  `over_allowance`. Any other excerpt is `unmatched` with the reason
  `no_stored_text`, `too_short`, `too_long` or `not_found`, marked as not
  checked: in a session under `exactory-review-excerpts-v3` (every session
  started since 0.72.0) it keeps the SHA-256, the length and the first 200
  characters of the reviewer's text, and in an earlier session the whole text.
  Under v3 a `purpose` longer than 500 characters keeps its first 500
  characters with the SHA-256 and the length of the whole, excerpts with the
  same source binding, text digest and status are kept once, and a verified
  duplicate does not charge the allowance. `reviewer_excerpts` lists the checked
  excerpts of all earlier turns in order, with the allowances used.
- One turn delivers at most 120,000 characters of new full text. Full-text
  requests are answered in request order. A source whose text bodies fit what
  is left of the turn arrives whole. A source that would fit a fresh turn waits
  with status `deferred_reading_budget` and a reason, and so does every later
  full-text request of the turn that needs new text. The extracted text of a
  longer source arrives in consecutive parts of at most 120,000 characters, one
  turn after another. Each part is a delivery of its own text artifact with
  `part: {version_id, part, parts, start, end, characters}`, where `start` and
  `end` are character offsets in the stored text; the original file and the
  other bodies of that source are not delivered with its parts. A part also maps
  its span of the stored text, so a citation inside one part counts as
  delivered. A request stays pending until its source, or its last part, has
  arrived. Excerpts, passages and abstracts do not count against the budget.
- A part ends after the last blank line in the last quarter of its
  120,000-character window, else after the last newline there, else exactly at
  120,000 characters. The parts cover the whole text in order, and the same
  text always gives the same parts. Excerpts and passages are checked against
  the whole stored text.
- A span locator counts from the start of the artifact that its link names. On
  the text artifact of a part, a span therefore counts from the start of that
  part. On the stored text, a span counts from the start of the stored text,
  as the `start` and `end` of `part` do. A span that lies outside the saved
  text of its artifact is refused with `invalid_locator`, and the harness does
  not read it as a span of another text. When a span on the artifact of a part
  lies inside the range `[start, end)` that `part` gives, for a part that a
  packet of the review delivered with the same version and the same source,
  the message names that range and gives the start and the end of the same
  passage counted from the start of the part, and the details give
  `part_relative_span: {start, end}`. Any other span outside the saved text
  keeps the message "The span must lie within the saved text".
- A `source` request at depth `fulltext` is answered with status
  `already_delivered` when turns of this session under the excerpt rules
  delivered the complete stored text of that version, whole or in all of its
  parts. The actual recorded requests of those turns decide, and the answer names
  the latest of them. A turn under the earlier rules does not count, so a
  session that began before these rules receives each text once more.
- A `passage` request `{id, kind: "passage", version_id, anchor, characters}`
  names a verbatim anchor of at least 20 normalized characters and a whole number
  of characters from 1 to 20,000. The next turn answers it with the original text
  that starts at the first occurrence of the anchor and has at most that many
  characters (`delivered`), with `not_found`, or with `over_allowance` beyond
  100,000 delivered passage characters in the session. A passage request is
  valid only in a turn under the excerpt rules. Under
  `exactory-review-excerpts-v3` an answer that was not delivered keeps the
  first 200 characters of its anchor with the SHA-256 and the length of the
  whole; `not_found` and `over_allowance` settle the request, and when
  capacity defers the reply, the pending request keeps the complete anchor
  and a later continuation answers that original request.

A final phase assessment still needs every pending request settled.
`already_delivered` answers and the passage answers `delivered`, `not_found`
and `over_allowance` are settled and never pending; a passage reply that a
turn defers for capacity (below) stays pending until a later turn includes it.

The main capture. A version can hold several full-text captures of one
original, for example a first capture whose text layout mode padded and a
later capture of the same PDF with the plain text. One of them is the main
capture of that original, by the rule of `select-main-capture`
([Operation fields](#operation-fields)): the first of them, or the one that
the command recorded. The parts of a long text are parts of the text of the
main capture, and excerpts and passages are checked against that text. For a
version with several originals, this is the main capture of the first
original that has an extracted text. A delivery that arrives whole carries
the text of every available capture. Once an original has a choice, the
delivery carries the text of the chosen capture and not the texts of the
other captures of that original, while the original file, the components,
the supplements and the captures of other originals are delivered as before.
A choice takes effect in the next packet: a pending request then receives the
chosen text, whole or in parts by the rules above. The parts that earlier
turns delivered stay delivered, and the earlier packets, requests and
verified excerpts stay as recorded. A packet that is built while the record
holds a choice records the current choices as `main_capture_selections:
{protocol: "exactory-main-capture-selection-v1", choices}`, in
`source_inventory` for a first packet and at the top level for a
continuation. The excerpts of a turn's output are checked against the main
captures of that turn's own packet, and so is the complete-source selection
of a `complete-visible-v1` continuation. A packet without
`main_capture_selections`, which includes every packet recorded before
0.87.0, has the first capture of each original as its main capture, so its
excerpts and its complete-source selection authenticate as before.

The source reply batch. When `review-assignment` records a continuation that
answers source requests, or a `complete-visible-v1` assignment, it plans the
replies of that turn against the complete reviewer request: the prompt, the
history, the metadata of every reply (an inventory page or query, an abstract,
a full text or a passage) and the native attachments, measured in the route's
own representation. It includes whole replies in request order while the
request fits the route's input limit; the exact full-text parts of the reading
budget stay the only subdivision of a reply. A reply that does not fit is
deferred with status `deferred_capacity` and stays in
`pending_source_requests`, a passage included, and the passage allowance of
the session is charged only for the passages that the turn includes. A reply
counts as delivered only when its metadata, its provenance and its required
bodies survive the construction of the request. The choice is frozen in the
packet under `source_reply_batch` (protocol
`exactory-review-source-batch-v1`) with `included_request_ids`,
`deferred_request_ids`, the requests themselves and the measurement; the
marker's `guidance` tells the reviewer which replies the turn includes and
defers, that a deferred request stays pending and need not be repeated, and
that a pending request its assessment needs keeps the response unresolved
with `support: null`. A reply that cannot fit on its own refuses the
assignment with `review_input_too_large` and the details `limiting_component:
"source_reply"`, `source_request`, `measurement` and
`input_limit_characters`; a `complete-visible-v1` closure that does not fit
refuses the assignment before any invocation, with `limiting_component:
"complete_visible_closure"`, and never becomes a partial review. A
delivery with status `unavailable_qualified` settles an exact-version request
with currently valid availability evidence and grants no reading credit. Its
captured registry or terminal-origin responses arrive with the answer; an
eligible partial abstract stays partial. Incomplete (`partial`) or unqualified
`unavailable` requests stay pending. A qualified answer is one whole reply for
capacity planning: when it does not fit, it becomes `deferred_capacity` and
stays pending with none of its evidence delivered. A continuation whose
inherited unavailable requests and bound source state are
unchanged, compared by the meaning of each request and the state of its
source rather than by request id, is refused with
`review_source_no_progress` before any reviewer call, and its pending
requests stay as they were: the reviewer repeated requests that the harness
already answered with nothing new, so do not continue the session with the
same requests; `review-series` ends with a `stop` of that code
([Review chains and `review-series`](#review-chains-and-review-series)). The
observed output of the earlier turn is already kept by
`review-run`, and while its inherited requests stay pending `value-review`
refuses to record a final assessment (`review_sources_pending`). Change the
source state first, for example by acquiring the missing source or recording a
qualifying availability policy, and then
continue the session, or report the unresolved source gap to the user. A
packet recorded before 0.71.0 rebuilds unchanged and gains no batch.

A qualified unavailability settles a request only when the route can deliver
its required evidence. On a local route (`claude_cli_v1`, `codex_cli_v1`,
`codex_cli_framed_v1` and `codex_cli_visual_v1`), whose source packet is
text-only, a qualification whose evidence is binary, a PDF or an image, does
not settle the request. The harness skips it and reads the other
qualifications of the requested version and depth. When none of them settles
the request, the delivery keeps the status, evidence and `source_provenance`
it had before the qualifications were read: `unavailable` with no evidence, or
`partial` with its partial captures. It gains no `availability`, its reason
names the text-only route, and it grants no reading or scientific-support
credit. The request stays in `pending_source_requests`. A qualification whose
evidence is text, for example the JSON responses of a registry absence,
settles on a text-only route as before. Every qualification
settles on a route that delivers binary originals. For an abstract request
whose alias family has related versions or competing identities, the delivery
also reports the related versions under `abstract_candidates`. Each candidate
carries `version_id`, `work_id`, `version`, `identity_status` (`family_only`,
or `ambiguous` when the family has competing identities) and `reusable: false`.
Its `identity_provenance` holds the captured `same_work` assertions with the
provenance of their sources, and its `abstracts` hold each abstract's
representation, completeness and artifact descriptor. `identity_ambiguities`
lists each identifier asserted into more than one family and each work with
more than one DOI identity. `candidate_source_deltas` reports for each
candidate whether its captured sources changed since the inventory
(`inventory_capture_digest`, `current_capture_digest`, `changed`). A family
alias never establishes the exact version. A candidate's abstract is an
alternative that no delivery attaches as evidence. A qualified absence of the
requested version keeps its status and grants no scientific credit. An exact
capture of the requested version still delivers. An assignment recorded before
these fields rebuilds its request and packet without them.

A continuation whose recorded attempts all ended without a completed output
does not hold the session. A new continuation of the same parent, with the
same reviewer, route and author, replaces the failed one, which stays in the
record and can no longer be run or continued (`review_assignment_replaced`).
Since 0.73.0 `plan_next` plans that replacement (state `replace`) only in
the cases of the outcomes of a failed reviewer call that name one, and
`review-series` executes it; every other failed turn is run again on the
same assignment, waits or stops under those outcomes; a
replacement that the rules below refuse with
`review_resampling_forbidden` is a `handoff` whose command is
`review-assignment` for a new eligible reviewer
([Review chains and `review-series`](#review-chains-and-review-series)). A continuation
with a completed output, or one that never ran, still blocks a second
continuation of its parent (`review_source_continuation_fork`). An initial
problem `result` assignment that never ran because `review-run` refuses its
exact frozen request before an invocation exists (`review_input_too_large`
with `phase: "dispatch_preflight"`, the dispatch preflight below), or whose
recorded attempts all failed with `review_input_too_large` before the reviewer
CLI started (the record that such a refusal left before 0.71.0), with no
completed output and no pending invocation, does not hold its slot either.
Assign a new initial `result` assignment of the same reviewer, on the same
route and with the same author, with `dossier_id: null` and an empty context:
it takes the slot of that reviewer's bar, and the earlier assignment stays in
the record and can no longer be run or continued
(`review_assignment_superseded`). Since 0.72.0 the same applies to a focused
`adjudicator` assignment recorded before 0.72.0 that never ran and whose
frozen request fails the size preflight: a new assignment with the same
adjudicator, route, author, `review_ids`, objection and correction takes its
slot and enters a parts v2 series
([Reviews in fresh parts](#reviews-in-fresh-parts)),
and the old record stays. A replacement on another route or with
another author, a replacement of an initial assignment that never ran and
fits, and a replacement of one that has a completed output, a pending
invocation or an attempt that failed with another code, are refused with
`review_resampling_forbidden`.

The excerpt rules of 0.64.2 to 0.65.1 (`exactory-review-excerpts-v1`) had no
reading budget. Assignments recorded under them keep their prompt and stay
authenticated, and their sessions continue under the rules of
`exactory-review-excerpts-v2` from the next continuation. A session started
since 0.72.0 records `exactory-review-excerpts-v3`: the same rules and
allowances, with the bounded retention above. A parts v2 series of a bar,
slate or result review creates a v3 session only when its packet has no
excerpt session; otherwise it keeps the inherited session protocol. A
recorded v2 session stays v2.

New v5 problem openness chains carry `excerpt_protocol` set to
`exactory-review-excerpts-v3`. A continuing v4 chain keeps its recorded excerpt
protocol: 0.72.0 chains retain v3, and older chains with no `excerpt_protocol`
field keep the field absent and their earlier retention rules.

A text route fits an oversized request, other than a `complete-visible-v1`
continuation, by applying these reductions in order, one at a time, until the
input fits:

1. the evidence bodies of the replayed earlier turns of the session;
2. the bodies that the scientific history of a context repair re-delivers;
3. an HTML original whose complete extraction from the same capture is
   delivered; that extraction then stays;
4. the bodies that an earlier turn of the session supplied;
5. the full texts of a declared `text_delivery_priority`, from the end of the
   list;
6. the captured responses of the searches that a problem record's openness
   finding binds, largest first;
7. the packets of the slot's scientific history, largest first;
8. the replayed packets of earlier turns, oldest first;
9. the full texts of fully read works that the packet cites, largest first;
10. whole requested sources that do not fit, in a deterministic batch: they
    stay pending as `deferred_capacity` for the same reviewer;
11. any other evidence body of a problem packet, largest first.

Steps 4 to 6, 9 and 11 keep every protected body and every source that the turn
delivers on request. A requested source loses a body only in step 3, where its
HTML original is withheld while its complete extraction is delivered, and in
step 10, where a whole source is deferred as pending. Each withheld body names
its delivery and a note. Since 0.71.0 the whole replies of a new continuation
or `complete-visible-v1` assignment are chosen when `review-assignment`
records it (the source reply batch, above), and the replies that it includes
survive this fitting whole. [Problem creation](#problem-creation) states which
records, readings and artifact bytes each result reviewer's actual request must
deliver for the post gate's result coverage check, and how that check treats a
body that step 6 withholds. A request that still does not fit is refused by
`review-run` before an invocation exists, with the code
`review_input_too_large`, the details `phase: "dispatch_preflight"` and the
preflight report (the dispatch preflight, below), and nothing is recorded. The
limit is 1,300,000 characters on `claude_cli_v1` and 1,048,576 on
`codex_cli_v1` and `codex_cli_framed_v1` (the
[route instructions](research-decisions.md#commands-and-shared-state)). The CLI
receives nothing.
A new problem `result` or `adjudicator` request on these text routes enters
parts v2 when it does not fit or its fitted request withholds an evidence
body, subject to the entry exceptions in
[Reviews in fresh parts](#reviews-in-fresh-parts). A recorded v1 result
series continues under its own protocol
([Problem evidence in parts](#problem-evidence-in-parts)). A staged review
([Staged problem reviews](#staged-problem-reviews)) starts only through an
explicit `staged_upgrade` of a recorded slot.
The harness also keeps the earlier
reduction orders of 0.56.0, of 0.56.1 and 0.57.0, and of the two repaired
copies of 0.56.0 that the extremal-graph-stability and Sidorenko girth-six
problem records ran before 0.58.0, which the
[0.58.0 release note](releases/0.58.0.md) describes; it rebuilds a recorded
request with each earlier order, so an attempt recorded under one of them stays
authenticated.

Since 0.75.0 a new assignment on `claude_cli_v1`, `codex_cli_v1` or
`codex_cli_framed_v1` records
`request_policy: "exactory-review-request-restoration-v1"`. After the
reductions above, its request gives back bodies of the current packet that
steps 4 to 6, 9 and 11 withheld (`delivery` `body-elided-previously-supplied`
or `body-elided-route-limit`). The bodies are tried in packet order. A body is
eligible when the packet holds its complete text, with the size and SHA-256 of
its artifact, and when all entries of the packet for that artifact are
identical. An eligible body comes back whole when the request, in the
route's own representation, still fits the route limit with it. A body that
does not fit stays withheld, and the next one is tried. When the reduced
request itself does not fit, nothing comes back. The replayed turns and the
scientific history (steps 1, 2, 7 and 8), a withheld HTML original (step 3)
and a deferred source (step 10) keep their projection. A `complete-visible-v1`
assignment and the controller or a task of a staged review do not record the
field. An assignment without the field rebuilds its request as before. When
the request is rebuilt, a `request_policy` that is not this one, or that is
recorded for a route or packet outside this rule, is refused with
`review_request_policy_mismatch`. Then `review-run` stops before an invocation
exists, and an attempt under it does not authenticate. The request of a staged
task is rebuilt under its own protocol, which does not read the field.

The dispatch preflight. `review-run` of an ordinary assignment, `review-probe`
and `review-output-correction` build the exact request and measure it before
the invocation is recorded, in the route's own representation (the visual
route counts its image wrappers). An input above the route limit, a mandatory
current source that the request cannot carry, or a fitted correction request
that loses the original output or its history is refused with
`review_input_too_large`, the details `phase: "dispatch_preflight"` and the
report under `preflight`, and nothing is recorded: no invocation and no
attempt. Before 0.71.0 such a run recorded a failed attempt. The report
(`exactory-review-preflight-v1`) states `input_size` (the measured characters,
the limit and the representation), `mandatory_final_input`,
`required_output_units` and `work_units` (the kind of work and its count),
`remaining_calls`, `remaining_cost_usd`, `output_tokens` and
`duration_seconds`, each marked `kind: "measurement"` or `kind: "unknown"`;
the output-token and duration estimates are unknown in 0.71.0 and never refuse
work. A run that passes keeps the report in its invocation and attempt
records, and `review-run` returns it under `preflight`. A recorded request is
never checked again: the reconstruction and authentication of recorded
attempts do not run the preflight.

### Review chains and `review-series`

A review chain is one assignment with the turns, runs, replacements and
recording steps that follow it: a source-request session, a reading chain, a
parts series, a staged review or a single assignment. Since 0.73.0 one
function, `plan_next`, plans the next action of a chain from its head, and
`exactory-research review-series` executes those actions. `status` and the
`gate` command attach this plan to every obligation that names a review
assignment, after every report digest is computed, as the fields `state`,
`command`, `payload`, `request_id`, `not_before`, `code` and `details` of its
`next` entries. `status` also lists a chain head that ended without an
output, with the obligations of its own state (for example
`review_output_pending`), each with its planned action. It adds no obligation
for a failed `native_math` head unless the head is an openness chain: the
math-solver controller names the native reviews it still needs
(`exactory-math search next`).

The plan. `plan_next` returns `state`, `chain` (the chain kind), `head_id`,
`progress` and, per state, `command`, `payload`, `request_id` (`resume`),
`not_before` (`wait`), `code` and `details`. A complete payload leaves only
`expected_revision` to the caller. The planner copies the head's `route_id`,
`dossier_id`, `role`, `reviewer_id` and `author_id`; a `continue` payload
keeps the context keys `native_packet`, `verification_task_id`, `review_ids`,
`objection_id`, `correction` and `source_delivery` and adds
`prior_assignment_id`; a `replace` payload keeps the whole context. The
planner never moves a chain to another route. New ids are
`<first turn id>-t<n>` for a turn and `<assignment id>-run-<k>` for a run.

| `state` | Meaning |
| --- | --- |
| `run` | A fresh call of the head may start now (`review-run`). |
| `resume` | A captured call whose attempt is not yet recorded is finished with its original request id. An uncaptured current call is a `wait` with `payload.action` `reconcile` and the code `review_invocation_pending`; a call without pool membership (0.69.x) is the handoff `review_worker_reconciliation_required`. |
| `wait` | Not before `not_before` (a provider condition, an active call, a capacity limit); the payload is the action to take then. |
| `continue` | The head's output asks for another turn (`review-assignment` with the complete payload). |
| `replace` | The head ended without an output and its rule names a replacement turn from the original parent (`review-assignment`). |
| `plan` | A staged controller plans or assigns its next task. |
| `record` | The final output is observed and recording needs no judgment (`value-review`, `review-adjudication`, `problem-openness`). |
| `handoff` | The next step needs the agent; `code`, `details` and the `command` the agent runs. |
| `done` | The chain's final is recorded, or a post closes the chain ([Problem creation](#problem-creation)). |
| `stop` | A typed limit or refusal; `code`, `details`. On a line of `review-series` also `stop_class`, and on the last line `stop_id` with `revision_after_stop`, or `stop_record_error` (the driver, below). |

The `record` action of an openness chain is `problem-openness` with the
head's `assignment_id`, planned only when `problem-openness` accepts the
final output by its own checks (an approved version 2 native result with an
openness finding, whatever the openness outcome); any other final output is
the handoff `openness_review_not_approved` (the handoffs below). The final of
a staged bar, slate or result review is a `record` action (`value-review`).

The driver. `review-series --assignment HEAD` (`--workspace` defaults to `.`,
and any assignment of the chain resolves to the chain's current head) executes
the planned actions of one review chain in process, through the same
operations as the CLI, until the chain is done or needs the agent. It takes
no `--file`, `--expected-revision` or `--request-id`: the driver supplies the
revision and the request id of each action. It has no turn limit. It prints
one JSON object per line. For an executed action, the line is the plan with
the `request_id` that the driver used and the `revision` of the snapshot read
before the action; a refused action carries `error` instead of `revision`. A
wait and a terminal plan print the plan. The exit status is `0` when the plan is `done`; `3`
at a `handoff` or a `stop`, with the plan on the last line; and `4` when a
`wait` ends more than 30 minutes ahead, with the plan, with its `not_before`,
on the last line. A `wait` of at most 30 minutes: it sleeps until `not_before`
and plans again. A refusal with `store_busy`, `stale_revision` or
`review_task_dependency_changed` is planned again from a fresh read, at most
8 times in a row; the 8th ends in a `stop` with that code. A captured call is
resumed with its original request id (state `resume`, field `request_id`);
every other action, including the reconciliation of an uncaptured call, gets
a new request id. It
stops with `review_series_no_progress` when it would repeat an action (the
same command and payload apart from a fresh id) while the chain's progress is
unchanged; the `details` then hold `command`, `action_state`,
`action_digest`, `progress_digest` and `successful_request_id`, the request
id of the first, successful execution of that action at that progress. The
store revision is not progress. Progress is: the recorded
turns, the request digests of completed attempts, the accepted staged tasks,
the attempts of the head's turn position, the staged task attempts, and the
recorded finals. When an executed action is refused with
`review_route_runtime_stale`, the command ends with exit status 3 and the
handoff of that code. When an executed action is refused with any other code,
the command plans again if the refusal recorded a new output refusal (a
recording command that refuses an output records it before it raises) or the
code is `review_invocation_pending`; otherwise it ends with exit status 3 and
a `stop` whose `code` and `details` are those of the refusal (for example
`review_input_too_large` or a `review_parts_*` code). Output views are
rendered once when it ends. `--check` is refused with `check_unsupported`
before any workspace or model effect. Every `stop` and `wait` line that has a
`code` carries `stop_class`, the class that the harness derives from the
code: `waiting`, `harness_defect` or `decision`
([The class of a stop](research-workflow.md#the-class-of-a-stop)). When the
run ends in a `stop` with a `code` and a `head_id`, the driver writes the
record `review_series_stop/<id>` before it prints the last line, with
`origin: "harness"`, the `code`, the `assignment_id` of the head, the whole
plan, `retry` (for `review_series_no_progress` the policy
`one_execution_per_progress_position` with `executions: 1`, `exhausted: true`
and the `action_state`, `action_digest`, `progress_digest` and
`successful_request_id` of the details; null for every other code, which has
no retry schedule), its `request_id`, its `recorded_revision` and `scope`
(the gate and the stage move that an automatic pass of this stop would make,
or null). When another writer committed between the driver's snapshot and the
record's commit (`stale_revision`), the driver writes the record once more
from a fresh snapshot, with a new stop identity and recorded revision; a
second `stale_revision` or any other error ends the attempt. The last line
then carries `stop_id` and `revision_after_stop`, the revision at which the
record was committed (the line's other revision fields are unchanged), or
`stop_record_error` when the record could not be written: a refusal of the
store as `{code, message, details}`, or `{code: "stop_record_error", message}`
for any other error. The stop's `code`, `details`, `stop_class` and the exit
status are the same in both cases. An intermediate `stop` that the driver plans
again writes no record. At a `handoff`, the agent performs the
step that the handoff names; the paragraph after the handoffs states when
`review-series` runs again. At a `stop` the
harness cannot continue the chain. The exactory skills run to the end without
stopping for questions: the agent reads `code`, `details` and `stop_class`;
when `stop_class` is `harness_defect` and the last line carries `stop_id`, the
agent requests the automatic pass with that `stop_id`
([The automatic pass](research-workflow.md#the-automatic-pass)); a
`stop_record_error` changes no class, and the stop then follows its class
without a `stop_id` ([The class of a stop](research-workflow.md#the-class-of-a-stop));
when this reference names a step for that code, the agent takes it;
otherwise the agent
reports the stop with its code and details in its summary and continues the
work that does not depend on this chain. After exit status 4, the agent runs
`review-series` again at `not_before`. The agent writes no driver, turn limit
or retry loop of its own around reviewer calls.

The handoffs:

| `code` | `command` | When |
| --- | --- | --- |
| `native_review_acceptance_required` | `search accept` | A native mathematical review has its output; the math-solver controller accepts it. |
| `native_review_attestation_required` | `search review-packet` (openness chains: `export`, kind `problem-openness`) | A native first turn needs a replacement, which needs a new attestation. |
| `verification_verdict_body_required` | `bind-verdict` | A verification review has its output; recording needs the verdict body. |
| `review_recording_required` | `manuscript-review`, `export` (standalone, kind `outputs`) or `review` | Another role's output needs its recording step. |
| `review_slot_exhausted` | `review-output-correction` when that operation accepts the head (a problem result reassessment whose latest failure is `review_reassessment_mismatch`); `export`, kind `problem-openness`, for an openness chain (a new chain); `search review-packet` for another native mathematical review (a new attestation); `review-assignment` for an initial bar (role `bar` without `prior_assignment_id`: a new eligible reviewer replaces the slot) and for a role outside `bar`, `slate`, `result` and `adjudicator`. For a bar continuation, a slate, a result or an adjudicator the plan is a `stop` with this code and no command: the slot keeps its continuity or its fixed adjudicator, and `review-assignment` of a new reviewer is refused (`review_slots_fixed`, `review_resampling_forbidden` or `review_adjudicator_fixed`). | Two outputs of the head were refused; the assignment is exhausted. |
| `review_resampling_forbidden` | `review-assignment` | An initial bar, slate or result review ended without an output; the same reviewer identity is not sampled again, so a new eligible reviewer is needed. |
| `review_worker_reconciliation_required` | `review-attempt` | A pending invocation has no pool membership (a call of an earlier version). |
| `assessment_incomplete` | `review-assignment` | A staged assessment is incomplete. |
| `review_context_*`, `review_route_retired` | `review-assignment` | A context repair is needed. |
| `review_route_runtime_stale` | `review-route` | The head's route pins another reviewer runtime. The planner keeps the route of the chain, so `review-series` on the same head gives this handoff again. Record a new route with a new id and the same adapter, model, endpoint and configuration, probe it, and then follow the stale-route procedure of the [route instructions](research-decisions.md#commands-and-shared-state): assign the role again on the new route, or, for a slot with history, retire the stale route and continue the slot through a context repair; then run `review-series` with the new assignment. |
| `openness_review_not_approved` | `export`, kind `problem-openness` | The final turn of an openness chain returned a result that `problem-openness` does not accept (for example the decision `revise` or `reject`, or an approved finding whose evidence or statement does not match); `details` hold `assignment_id`, `decision`, `reason`, `kind` and `refusal` (the `code` and `message` of the `problem-openness` refusal). Obtain a new finding after the reviewer's reason and the refusal are addressed. |

After a handoff, the agent performs the step that the handoff names.
`review-series` sees a chain as done only through a final recorded in the
research store (`value_review`, `review_adjudication`, `verdict_assessment`,
`manuscript_review`, `problem_openness`). A native acceptance (`search accept`)
is recorded by the math-solver controller, and a standalone `export --kind
outputs` completes that delivery; neither makes the chain done for
`review-series`, so after them the agent continues with the controller
(`search next`) or the delivery, and does not run `review-series` on that head
again. The agent runs `review-series` again only when the step leaves more
work in the chain (for example `bind-verdict`), and with the new assignment id
when the step starts a new chain.

In this table an openness chain is a problem-creation openness chain, whose
packets come from `export --kind problem-openness`. A math-solver openness
reading chain (`exactory-openness-reading-math-v1`, packets from
`search review-packet`) is another native mathematical review: its
`review_slot_exhausted` and `native_review_attestation_required` handoffs name
`search review-packet`, and its head belongs to the math-solver controller.

Outcomes of a failed reviewer call. New observations record `observed_at`
(UTC, ISO 8601, seconds). A failed Codex call with an empty answer also
records `provider_diagnostic`: `source` `codex_rollout`, `thread_id`,
`codex_error_info`, `message` and `rollout_sha256`, read from the last
`task_complete` error of the call's session rollout under
`$CODEX_HOME/sessions` (default `~/.codex/sessions`), at most 64 MiB and
never through a symbolic link. Neither field takes part in authentication.
The reviewer runtime check (the CLI's version read) is retried after 1, 2, 4,
8 and 16 seconds at the invocation start, the probe start, the staged
invocation and the observation, before any model process starts. A runtime
failure after admission becomes a captured, finished failed attempt. The
outcomes below are counted over the whole failure sequence of the head's turn
position: replacements of the turn count, and a usable completed output
starts a new sequence.

| Outcome | Recognized by | Next | Final code |
| --- | --- | --- | --- |
| `usage_limit` | an answer that starts "You've hit your ... limit" or "You've reached your ... limit", or the Codex diagnostic `usage_limit_exceeded` | `wait` until the stated reset (Claude: "resets <Mon> <d> at <h>[:<mm>]am/pm (<zone>)"; Codex: "try again at <Mon> <d>th, <yyyy> <h>:<mm> AM/PM.", local time), else the provider schedule | `review_provider_unavailable` |
| `model_at_capacity` | the Codex diagnostic `server_overloaded` | the provider schedule | `review_provider_unavailable` |
| `provider_unavailable` | an answer that starts "API Error:" (other than the two rows below), or the error code `review_provider_failure` | the provider schedule | `review_provider_unavailable` |
| `provider_refused` | an "API Error:" answer that names safeguards | one more call; the second stops | `review_provider_refused` |
| `output_over_cap` | the adaptive handlers' own predicates, output tokens at or over the route cap, or an "API Error:" answer that states an exceeded output token maximum | a replacement when the existing handler proves a smaller admissible request (a parts v2 read turn at half its unit characters; a context repair into parts v2); otherwise stop with the measured tokens | `review_output_over_cap`, with `handler_error` naming the handler's refusal; a parts v2 read at the floor stops with `review_parts_budget_exhausted` and its measurements ([Reviews in fresh parts](#reviews-in-fresh-parts)) |
| `execution_incomplete` | an interrupted invocation, an incomplete or injected turn, or an incomplete response with text | one more call; the second of this outcome stops | `review_execution_incomplete` |
| `answer_malformed` | a completed response whose answer is not one object and is not recovered (the answer recovery below) | one more call; the second of this outcome stops | `review_answer_malformed` |
| `context_unverified` | a completed attempt whose context was not verified | one more call; the second of this outcome stops | `review_context_unverified` |
| `scientific_refused` | an output refusal of a completed answer | one more call; after the second, the handoff `review_slot_exhausted` | `review_slot_exhausted` |
| `input_over_limit` | the error code `review_input_too_large` | the existing initial-size replacement when it applies, else stop | `review_input_too_large` |
| `runtime_unavailable` | the error code `review_runtime_missing` | the provider schedule with its own count; an attempt without `observed_at` runs at once | `review_runtime_missing` |
| `runtime_stale` | the error code `review_route_runtime_stale` | the handoff `review-route` | `review_route_runtime_stale` |
| `timeout` | an execution timeout | stop | `review_timeout` |
| `format_unsupported` | the error code `review_format_unsupported` | stop | `review_format_unsupported` |
| `unknown` | none of the above | on a Codex route, the provider schedule while every failed call of the turn position had no answer and at most 120 seconds of wall time; otherwise stop | `review_failure_unknown` |

The provider schedule: `wait` 60, 120, 240, 480, 960 and 1800 seconds after
the n-th failure of the provider group (`usage_limit`, `model_at_capacity`,
`provider_unavailable`, and an eligible `unknown`); the 7th stops. An attempt
recorded before 0.73.0 has no `observed_at`; its wait counts as elapsed.
Every turn position also stops after 8 failed attempts of any outcome, with
`review_turn_failures_exhausted`.

A run that the route's time limit stopped is `timeout`: it stops with
`review_timeout`. `plan_next` plans `replace` only for `output_over_cap` when
the existing handler proves a smaller admissible request, for
`input_over_limit` when the existing initial-size replacement applies, and for
an initial turn that never ran and whose input is too large; every other
failed turn is run again on the same assignment, waits, or stops, by the table
above. Reading turns and source-request continuations follow the same rules.
An attempt imported with `review-attempt` (0.69.x calls) has no error, no
response and no measured wall time: it is the outcome `unknown`, which is not
eligible for the provider schedule, so its plan stops with
`review_failure_unknown`. An uncaptured current call that the reconciliation
ends is recorded with the error `review_invocation_interrupted`: the outcome
`execution_incomplete`, one more call.

Answer recovery. The protocol `exactory-review-answer-recovery-v1` applies to
a new observation of a completed response with a non-empty `id` whose answer
text the transport cannot decode. Attempts recorded before 0.73.0 stay as
recorded. Its rules: `surplus_closing_braces` (one object, then only `}` and
white space), `premature_root_close` (one object, then `,`: removing the brace
that closed it makes the whole text one object) and `raw_control_characters`
(a control character written literally inside a string; recovered with no
removal, alone or with one of the two other rules). It refuses, so that the
attempt stays failed: a duplicate member name at any depth, `NaN`, `Infinity`
and other non-finite numbers, nesting deeper than 512, text after the object
other than the two rules, and any other reading. The certificate,
`answer_recovery` on the attempt, holds `protocol`, `rules`, `removed_offsets`
(code-point offsets into the answer text), `answer_sha256`, `output_sha256`
(the canonical digest of the recovered output) and `error` (`kind`, `code`).
Authentication repeats the recovery and compares every field and the stored
output's digest. `status` shows `answer_recoveries` per assignment (and
`answer_recovery` for a staged task); the usage report shows
`recovered_calls` and `answer_recoveries`; a raw observation shows
`answer_text`. The answer text stays in the response artifact.

### Reviews in fresh parts

A review whose request does not fit one input of a text route with a local
input limit (`claude_cli_v1`, `codex_cli_v1` and `codex_cli_framed_v1`), or
whose fitted request withholds an evidence body, is delivered as a series of
fresh-input turns under the protocol `exactory-review-parts-v2` ("parts v2")
when an adapter applies to its role and packet. The adapters: `problem_result`
(a `result` review of a problem record without a `complete-visible-v1`
delivery), `problem_adjudicator` (a focused adjudication of a problem record),
`context_repair` (a repair of a bar, slate or result slot, or of an admitted
adjudicator slot), `study_verification` (a `verification` review),
`study_readiness` (a `result` review with a strategic dossier) and
`native_math` (a `native_math` review without an openness minimum).
For new work outside an existing series, `review-assignment` builds the
ordinary request first. A fitting request that carries every body stays
ordinary and is recorded as before, byte for byte, unless a repair rule
requires parts: an admitted adjudicator repair on such a route uses parts
whatever its size, and an earlier overflow of the same repair binding (the
same role, dossier, slot and repair predecessor) also requires parts. A
request that does not fit, or whose fitted request withholds a body, starts a
series. A recorded 0.69.x
series continues under [Problem evidence in parts](#problem-evidence-in-parts)
while its previous turn is a v1 turn with the same evidence digest and no
recorded final; a refresh after its recorded final, or a changed evidence
digest, enters this section. A staged review starts only through an explicit
`staged_upgrade` ([Staged problem reviews](#staged-problem-reviews)). On a
route without a local input limit, and on the visual route, nothing changes.

Terms. A series is the chain of assignments that delivers one review, named
by its first assignment (`series_assignment_id`); a turn is one assignment of
it. A unit is one entry of a collection that the adapter declares splittable,
with the artifact bodies that the existing evidence attachment owns for it; it
has a handle (`u1`, `u2`, ...). A card is the structured reviewer record
returned for a unit; passage units share their reading's card. A target is a
check, rubric item, objection or other question that the final review must
decide. Equal items at several places are one unit
with several occurrences, and every occurrence of the frozen packet stays an
obligation. The frame is the packet with every unit removed; every turn
carries it whole. The inventory is the list of the series' units, stored by
the first turn as a canonical JSON artifact and bound by its digest in every
turn: for each unit the handle, the item digest, the item characters, the
occurrences (collection path and key or index), the owned bodies (SHA-256,
kind, characters) and `citations`, the exact references by which the role's
validator accepts a citation of the unit's content, so that a final turn cites
a unit that it saw only as a card by copying them. The first turn also stores
the logical packet, the complete packet before projection, and binds its
digest. The inventory is append-only: a turn's inventory holds every row of
the previous one unchanged plus the units that appeared since, in packet
order; a unit that disappears from the current packet ends the series.

Turns. Every turn's packet holds the frame, the header `review_parts`
(`protocol`, `adapter`, `series_assignment_id`, `series_key`, `kind`, `turn`,
`inventory` with `inventory_digest`, `logical_packet` with
`logical_packet_digest`, the `receipts` of the earlier turns, the `targets`
with their labels, the allowances, the `plan` and a fixed `note`) and the
carried state `review_parts_state` (`cards`, `combinations`, `findings`),
which the harness builds only from the selected authenticated attempts of the
earlier turns. The kinds:

- `read`: the units of this turn, each at its first occurrence in its
  collection, with their bodies or body segments. The reviewer returns
  `{"kind": "read", "turn": N, "cards": [...], "unborne": [{"target",
  "reason"}]}`: exactly one card per delivered unit (a passage is carded under
  its reading), each `{"unit", "summary", "bearing": [{"target", "relation",
  "reason"}], "statements": [{"text", "why"}], "depends_on", "questions"}`
  with `relation` one of `supports`, `qualifies`, `contradicts`, `context`,
  and `unborne` naming exactly the targets that no card of the turn bears on.
  A card with a `supports`, `qualifies` or `contradicts` bearing has at least
  one statement.
- `combine`: the cards of the series, or two blocks of them when they do not
  fit one input (each pair of blocks meets once). The reviewer returns
  `{"kind": "combine", "turn": N, "combinations": [{"units", "question",
  "view", "read": [...]}], "rest"}`, where each `read` entry is a whole unit
  `{"unit"}`, a body span `{"unit", "body", "start", "end"}` or an item span
  `{"unit", "item": true, "start", "end"}`, and the originals of one
  combination fit one check turn. The harness assigns the combination ids
  (`c<turn>-<n>`).
- `check`: the combinations of this turn with the cards of their units and
  the originals they named. The reviewer returns `{"kind": "check", "turn":
  N, "findings": [{"combination", "finding", "effect", "statements":
  [{"unit", "text", "why"}]}]}` with exactly one finding per combination and
  `effect` one of `supports`, `qualifies`, `contradicts`, `unresolved`.
- `final`: the frame, every card, combination and finding, and the fill, the
  originals that fit the spare room (first the units with a `contradicts` or
  `qualifies` bearing or finding, then the originals that combinations named,
  then the other units). The reviewer returns the role's ordinary output under
  the role's validator, or `{"kind": "requests", "unit_requests": [...],
  "source_requests": [...]}` with at least one nonempty list; a unit request
  has the shape of a combination's `read` entry plus `why`, and the source
  requests follow the existing source-request rules (empty for an
  adjudication, a verification and a native review). A request is answered by
  a further final turn, assigned the same way, which carries the requested
  originals first and then the fill.

The planner chooses a read turn while any unit lacks complete item or body
delivery. It chooses combine turns until the required card and block pairs
have been read together. It then chooses check turns while combinations lack
findings, and a final turn otherwise. A series with at most one card needs no
combine turn, and a series without combinations needs no check turn. Units
that appear after the first turn get read turns even after combine or check
turns ran, and the combine turn that follows reads every new card against
every card of the series.

Statements. Every statement of a card or a finding is checked against the
text that the turn delivered for its unit, under the normalization of the
excerpt rules (Unicode compatibility normalization, NFKC, applied to each
character, and white space collapsed). Only verified statements are carried.
Each keeps `at`: the unit, the body's SHA-256 or `item: true`, and the Unicode
code point offsets `[start, end)` in the whole text; they locate the first
matching occurrence in the delivered text (for a segment, the offsets count
from the start of the whole body). The
carried card records how many statements were not verified; an unverified
statement does not refuse the output. The statements of a turn count against
40,000 characters per turn and 6,000 per statement, and the allowances do not
accumulate over the series.

Segments. A text body that does not fit the room of one read turn is delivered
in exact segments. A segment ends at a paragraph or line boundary in the last
quarter of its allowed span when one is available, and at the size limit
otherwise. Each segment names its body, its offsets and the body's total
length, and the body counts as delivered when the segments of the series
cover `[0, length)`. An item whose
canonical JSON does not fit is delivered the same way, as segments of its
canonical JSON text bound by the item digest. A body whose encoding is not
UTF-8 is delivered whole in one read turn and never segmented. A binary body
follows the existing route rules.

Sizes. Every planned packet is measured as its exact rendered request, so no
margin guesses the prompt size. The read allowance, the unit characters of
one read turn, starts at 600,000 and follows the series' own recorded
attempts: after a read turn whose output tokens reached 75 percent of the
route's `max_output_tokens` the next allowance is half the turn's unit
characters, after a turn at 35 percent or less one and a half times,
otherwise the same; when a failed read turn qualifies for replacement after an
output-cap failure or `review_incomplete`, the replacement starts with half
the failed turn's measured `unit_characters`. Since 0.73.0 a downward
adaptation and that halving stop at `unit_allowance_floor`: the smallest read
allowance with which the remaining read turns' card floors (4,000 characters
each) still fit the remaining card budget. The most read turns are the
remaining card budget divided by 4,000, rounded down; the floor is the
remaining unit characters divided by that number, rounded up. The
assignment's `review_parts_plan` and `status` state it; it is `null` for
assignments recorded before 0.73.0 and `0` for turns that are not reads, and
it is in no request and no packet. The check before the first turn is
unchanged. A read planned at the floor that overflowed the output cap ends
the series with the typed stop `review_parts_budget_exhausted`: `plan_next`
plans that stop at the next planning, before another call. A continuing read
that spends the last card floor and leaves originals unread is refused by
`review-assignment` with the same code, a `stop` of `review-series`. The
details hold the remaining units and characters, the floor, the most read
turns, the card and state budgets, the failed assignment id, the output
tokens and the route cap
([Review chains and `review-series`](#review-chains-and-review-series)).
Units enter a turn in inventory
order; a unit enters whole when its
item and pending bodies fit, and the first unit that does not fit whole is
delivered with as many segments as fit and continues in the next turns. The
state budget: everything the final carries besides its frame fits the room
that the measured final frame leaves under the route limit, with cards at
most half of that room, combinations a tenth, findings a fifth and the rest
the fill. The cards of a read turn get at most 80,000 characters and at least
4,000, in proportion to the turn's share of the uncarded unit characters; a
combine output, a check output and the request form of a final get at most
60,000 characters; the role's ordinary output in a final is bounded by the
role's validator and the route's output cap, as it is today. The packet
states each allowance under `review_parts.output_allowance`.

Progress. Every read turn delivers at least one item, body segment or item
segment that no earlier turn delivered. A final request must name at least
one original or source that the final it answers did not carry (it can ask
again for an original that an earlier final carried), and the final turns of
a series carry at most twice the inventory's characters of originals in
total. The refusals of `review-assignment`, at entry or during continuation,
each before an assignment or invocation is recorded (the preparation can
already have stored the inventory and logical packet artifacts), each a
`stop` of `review-series` with its code when the command assigned the turn
([Review chains and `review-series`](#review-chains-and-review-series)), some
without size details: `review_parts_frame_too_large` with `frame_characters`,
`input_limit_characters` and `largest_fields` when the frame leaves no room;
`review_parts_final_too_large` when the final frame cannot hold the minimum
cards, when two card blocks or the combination blocks do not fit, or when the
final frame and its state do not fit; `review_parts_inventory_too_large` when
the floors of the planned read turns exceed the card budget, when a non-text
body cannot fit one read turn whole (`unit`, `body`, `body_characters`), when
the remaining card budget is below 4,000 characters while units remain, when
the findings budget is below 2,000 characters per pending combination, or when
a combination cannot fit its promised check room; `review_parts_no_progress`
when the next read turn can add nothing; and
`review_parts_request_no_progress` when no requested original fits the next
final. A final request output is refused with
`review_parts_request_no_progress`, under `review.shape` and the retry rule
below, when it requests nothing absent from the answered final, or includes
unit requests after the final-original budget is exhausted; a request larger
than the remaining room is answered in segments over successive final turns.

Series identity, replacement and change. A series is valid when its turns
share role, route, slot, session, reviewer, author, subject and the adapter's
series key, each turn's `prior_assignment_id` names the turn before it, and
each turn's header holds the receipts of exactly the observed earlier turns.
The series keys: `problem_result`, the problem evidence digest and the digest
of the bars; `problem_adjudicator`, the problem evidence digest, the
objection's material version and the digest of the current-source
requirements; `context_repair`, the role's key and the repair binding;
`study_verification` and `native_math`, the digest of the frame;
`study_readiness`, the digest of the frame without its transport envelope
(`continuation`, `source_deliveries`, `pending_source_requests`,
`prior_context`, `reviewer_excerpts`, `evidence`), so that the series' own
source replies do not restart it. A changed series key or a removed inventory
unit ends the series. The next request is measured again and starts another
parts series only when parts are still required. The cards of the ended
series are not carried. When every attempt of a turn ended without completed
output and no invocation remains pending, `plan_next` plans the next action
under the outcomes of a failed reviewer call
([Review chains and `review-series`](#review-chains-and-review-series)): a run
of the same assignment, a wait, a stop, or, in the cases that name one, a
replacement with a new id (state `replace`), which `review-series` executes.
A replacement of a continuation keeps its original `prior_assignment_id`
(`review_assignment_replaced` for the failed one); a replacement of a first
turn repeats its original context without `prior_assignment_id`; a native
first turn first needs a new packet whose attestation names the replacement
assignment (the handoff `native_review_attestation_required`). No turn of a series runs
concurrently with another turn of the same series. For a bar, slate or result
series, or an admitted adjudicator predecessor, repair a retired route or a
contaminated context through `context_repair`. Native and verification
assignments do not accept that context; report their blocked binding instead
of submitting a context-repair assignment.

Prompts. The prompt of a turn is the role's base prompt, as `canonical_prompt`
builds it for that role and situation, followed by the parts v2 common text
and the text of the turn's kind, and in a final turn the text of the adapter
(after the request-answer text when the final answers a request). Its
template id is `exactory-review-parts-v2/<adapter>/<kind>` (`final-answer`
for a final that answers a request); `parameters.base_prompt` records the base
prompt's parameters, template id and hash, and `parameters.review_parts` the
binding (`adapter`, `kind`, `protocol`, `answer`). Every existing template id,
prompt text and hash stays as it is, and a parts prompt recorded under one
common text version keeps authenticating after the common version changes.

Running a series. Assign the first turn as the ordinary assignment of the
role; the assignment result holds `review_parts` and `review_parts_plan`
(`kind`, `turn`, `units_carded`, `units_remaining`, `unit_allowance`,
`card_allowance`, and `remaining_turns` per kind, each `{estimate}` computed
from the series' own turns only). Before the first step that calls a model,
read `review_parts_plan` in the assignment result, and the allowances,
estimates, progress and next action in `status` under
`review.assignments.<id>.review_parts`; `review-assignment --check` checks a
parts preparation and writes nothing, since 0.73.0 a check reads the reviewer
CLI version when a preparation needs it
([Inputs and receipts](#inputs-and-receipts)). Then run `review-series --assignment ID`
([Review chains and `review-series`](#review-chains-and-review-series)): it
runs each turn and assigns each next turn with the complete payload (for
`review-assignment`, a new id, the same role, dossier, route, reviewer and
author, and `prior_assignment_id` inside `context` naming the turn that just
ran, with `native_packet` (the same packet) or `verification_task_id` (the
same task) there when the role needs it, so that only `expected_revision`
and a new request id are left to it). A focused continuation inherits
`review_ids`, `objection_id` and `correction` from the turn. The command
continues until a final turn returns the role's ordinary output, or until a
handoff, a stop or a wait of more than 30 minutes ends it. `status` and
`next` name each step:
`review.assignments.<id>.review_parts` carries the adapter, the series id, the
kind and the turn, the units carded and remaining, the allowances and
estimates, `complete`, the bounded `issues`, `blocked` with a code when the
turn cannot continue, and `next`, a list of actions `{kind, workflow, scope}`
(`review-assignment` with the last turn as `prior_assignment_id`, or
`review-run` of the recorded turn) and, since 0.73.0, the plan of the chain
(`state`, `command`, `payload`, `request_id`, `not_before`, `code`,
`details`); in a problem record the obligation
`review_parts_pending` carries the same fields; `status --summary` adds
`review_parts: {count, examples, omitted}`.
When the assignment is of a posted statement, `review_parts.next` is `[]`
([Problem creation](#problem-creation)).
When an authenticated run
completes, `review-run` checks a read, combine or check output and the
request form of a final under `review.shape`, whatever the role, and writes
the output refusal when it fails: `invalid_value_review` for an output over
its allowance, a missing or extra card or finding, an unknown handle, a wrong
kind or turn, or a span outside its text, and
`review_parts_request_no_progress` for a request that makes no progress. The
ordinary final output is validated by the role's validator
(`adjudication.shape` for an adjudicator, `verification.parts` for a
verification). The retry after a refused output and `review_slot_exhausted`
after two refused outputs apply as for any output. `review-series` records
the final that returned the review with `value-review` or
`review-adjudication` (state `record`); for a verification or a native
review it ends with the handoff `verification_verdict_body_required` or
`native_review_acceptance_required`, and the agent records the final with
`bind-verdict` or the math-solver command of the role. `review-adjudication`
refuses every other turn with `review_parts_pending`.

What a series delivers. `research_harness.review_parts.verify_series`
authenticates every turn (its selected attempt is instrumented, completed and
ready, its actual request carries the frozen packet, and its output passes the
contract of its kind) and reports whether the series is complete: every
occurrence of every unit of the final inventory was credited by a read turn
(the item whole or by item segments, and every owned body complete in one turn
or covered by segments); every read turn has one card per delivered unit and
every target borne or `unborne`; every card was read by a combine turn, unless
the series has at most one card, and every pair of blocks met once; every
combination has a finding; and the final
turn is observed with the role's ordinary output. `read_series_delivery`
builds what the series delivered from its actual packets: complete
attachments, segments with their exact offsets, and the bodies whose segments
cover them. A final cites what any turn of its series delivered; the result
coverage check, the citation check and the adjudicator source checks read
that delivery, so a body delivered in three segments is citable and counts
for coverage ([Problem creation](#problem-creation)). For a focused
adjudication the completed series is checked against the stored logical
packet, so the manifest, projection, inspected-unit, version and descriptor
comparisons stay exact and only byte availability comes from the series. For
a readiness review a candidate evidence reference is covered when the final's
support evidence cites it, or when the complete series carries an
authenticated card of a unit whose inventory `citations` include that exact
reference and whose bearing names one of the six readiness targets.
`require_verdict_assignment` accepts only the final of a complete
verification series; the math-solver harness accepts the final of a complete
native series as it accepts a reading chain's final, with the series receipts
as `reading_turns`.

Views. `export --kind outputs` places the outputs of read, combine and check
turns, and the request form of a final, under
`intermediate/reviews/<role>/<series assignment id>/<NN>-<assignment id>.<run
id>.json`, and the selected final output of a complete series under
`final/reviews/<role>/<series assignment id>/`: an ordinary final as
`<NN>-<assignment id>.<run id>.json`, a corrected final as
`<NN>-<assignment id>.<correction id>.corrected.json`. Read the run's
`output_path` or the correction's `corrected_output_path` for the exact
location. `series.json` lists the turns in series order; each turn has a
`review_parts` progress object (`protocol`, `adapter`, `series_assignment_id`,
`kind`, `turn`, the allowances, the estimates, the evaluated progress and the
next actions), and the output index entries carry `series_assignment_id`,
`turn`, `adapter` and `review_parts_kind`.
When the assignment of a turn is of a posted statement, the `next` list of its
`review_parts` object is `[]` ([Problem creation](#problem-creation)).

### Staged problem reviews

A staged review performs one review as tasks of a frozen epoch, each task a
fresh call with an empty history, instead of one call or a series of part
turns. Its protocol is `exactory-review-staged-v2`. It starts in two ways:
`review-assignment` with `context: {staged_upgrade: {assignment_id}}` opens
one in a recorded slot (Upgrading a recorded session, below), and a context
repair of a `result` slot whose predecessor is a controller is itself a
controller when its ordinary repair request fits one input
(Context repair of a staged slot, below). Every other `result` or
`adjudicator` assignment, except a child assignment that runs a task of a
staged review, is an ordinary assignment or a turn of a series. A problem
`result` or `adjudicator` assignment on a local route (`claude_cli_v1`,
`codex_cli_v1`, `codex_cli_framed_v1` and `codex_cli_visual_v1`) is an
ordinary review when the request that the transport builds fits the route's
input limit and delivers every item of the packet and every artifact body of
its `evidence` list; the harness records the packet, the prompt and the
request byte for byte, as before. A `result` or `adjudicator` request on a
route with a local input limit that does not fit, or whose fitted request
withholds one of those bodies, is delivered as a parts v2 series
([Reviews in fresh parts](#reviews-in-fresh-parts)); a recorded 0.69.x
series continued with the same evidence digest goes on under its own protocol
([Problem evidence in parts](#problem-evidence-in-parts)). A context repair
of an ordinary slot whose request does not fit, or whose earlier attempt with
the same role, dossier, slot and repair predecessor ended over the output
cap, is a parts v2 series too, and so is every admitted adjudicator repair on
such a route. A continuation with
`context.source_delivery` and an assignment on the visual route keep their
ordinary packet, whatever its size; when the request does not fit,
`review-run` refuses it before an invocation exists with
`review_input_too_large` (the dispatch preflight, above) and records nothing,
except that a `complete-visible-v1` closure that does not fit is refused by
`review-assignment` (the source reply batch, above),
and a recorded `result` or
`adjudicator` assignment can be the predecessor of an explicit upgrade
(Upgrading a recorded session, below). Since 0.75.0 a context repair of a
`result` slot on the visual route carries `visual_context_repair` in its
packet under `exactory-visual-context-repair-v2`, with an `ancestry` list.
Each entry binds one assignment of the repaired lineage with its selected
native attempt, request, output and packet digest, authenticated against its
recorded provenance. The scientific history then carries each assignment once
instead of nested inside each later packet. A repair recorded under
`exactory-visual-context-repair-v1` rebuilds as before, and a repair of a
staged slot keeps its own rule (Context repair of a staged slot, below). 0.70.0
opened a staged review for every oversized `result` or `adjudicator` request
when it was assigned. The slots it
opened keep working unchanged: their controller, tasks and child assignments
continue, their requests rebuild byte for byte, and the post gate reports
`review_quality_measurement_pending` for their epochs. The harness plans every
task of a staged review itself and names the next command in `next`; the
operator runs those commands and builds no task envelope.

The scale preflight. The task contracts of this release require one full card
to judge every claim of the epoch and later tasks to carry every claim id and
context root. Before the epoch of an upgrade is frozen, the harness measures
that envelope against the route's input limit, and the estimated output of the
smallest full card that judges every claim against the output allowance
(`max_output_tokens` minus the planner's margin). When either does not fit,
the `staged_upgrade` is refused with `review_staged_scale_unsupported` and
nothing is written: no epoch, task, slot head or packet record, and no
artifact; the recorded slot keeps its delivery, a result slot in parts and an
adjudication or a context repair with its ordinary packet. The details of the
refusal are `claims`, `occurrences`, `envelope_characters`, `input_limit`,
`card_output_tokens` and `output_allowance`, so the refusal shows which bound
was exceeded and by how much. The measured shape that met this limit, 15,389
frame leaves and 106 occurrences, is in the
[0.70.0 release note](releases/0.70.0.md#known-limits).

The controller and the epoch. `review-assignment` records the controller with
`staged_review: {protocol, kind: "controller", epoch_id, task_ids}`, an empty
`history` and `staged_started_at`, and in the same write the epoch
`review_epoch/<epoch id>` (the id is `epoch:<controller id>`), one
`review_task` record per frame task and the slot head `review_slot_head/<slot
id>` with `state: "pending"`. Its result names the first command,
`review-assignment {controller_assignment_id}`. The epoch freezes the subject,
the evidence currency (`problem_review_currency`, else the evidence digest),
the role, the slot, the reviewer, the route and the author; the manifest; the
material objections with their evidence entries resolved; the permitted values
of every decision of the role; and the budgets `max_calls` 10,000,
`max_retries` 1 (two attempts per task), `max_rereads` 2, `max_subdivisions`
10,000, `max_epochs` 2 and `max_elapsed_seconds` 86,400. The manifest holds
the sources (each frame field, each item of the evidence lists and each
artifact body, as text with its character count and the SHA-256 of its UTF-8
bytes; a body that is not UTF-8 text is a binary source), the occurrences
(each unit of the six split keys with the bodies it owns, the frame evidence
bodies, and for an adjudicator the original assessments, the supplied source
mappings, the bar calibrations and the original evidence under the class
`original_context`, with the current problem evidence under `current`), the
claims with the context each one needs, the assurance dependencies and the
inherited findings, which are the material objections of the packet. A
predecessor, named by `prior_assignment_id`, by the repair or by the upgrade
below, is bound by its assignment digest, its last attempt and whether that
attempt had an output, and, when it was itself a controller, by its epoch and
that epoch's digest. The adverse findings of the predecessor's accepted tasks
enter the new manifest as inherited findings with their provenance, the
adverse records of its refused outputs reach the new tasks as
`refused_findings`, and the harness refuses the controller with
`review_worker_reconciliation_required` while the predecessor has a pending
invocation or an unreleased reservation. A slot whose evidence currency
already has two epochs refuses a third with `assessment_incomplete` and the
prerequisite `new_evidence_or_resolved_dependency`. The controller's packet is
the ordinary packet with `staged_review: {protocol, kind: "controller"}` and a
new digest. A controller is never run: `review-run` on it is refused with
`review_task_pending`.
When a post closes the chain of the controller, the refusal is
`problem_already_posted` instead ([Problem creation](#problem-creation)).

The task types. A task has a type, a scope, the claims it judges
(`claim_ids`), the occurrences it reads (`occurrence_ids`), its input spans,
its dependencies on earlier tasks, the context root of each of its claims
(`context_roots`) and a frozen measurement. The scopes are `joint_original`
(the whole unit, every owned body, the claim and its context are in one input
together), `segmented_original` (a reconciliation over segments),
`original_segment` (one segment of an original), `joint_card` (all compact
cards, or two blocks of them, in one input), `segmented_card` (a
reconciliation over card segments; also the scope of answer and final tasks),
`card_segment` (one segment of one card) and `unresolved`. The types are:

| Type | Input | Output |
| --- | --- | --- |
| `frame` | A group of the frame fields as exact text, and the inherited findings. | `claims`, `conditions`, `dependencies` and `unresolved`: the fields decomposed into claims, conditions and definitions, with no judgment. |
| `read` | The originals of one unit, or one segment of it, and the assigned claims. | `cards` with one `judgments` cell per assigned claim, `findings` and `new_dependencies`. |
| `combine` | The compact cards of the epoch together, or the cards of two blocks, or one card segment. | `combinations` with a synthesis per claim and `selected_groups`, the combinations whose originals must be read together; `segment_readings` for a card segment. |
| `original_check` | The originals of the selected groups again, with their compact cards. | `original_checks` with grounded conclusions, qualifications, conflicts and unresolved questions. |
| `integrate` | The conclusions of the assigned full cards, the receipts of the child tasks, the findings and the decision constraints; or the fragment cards of one unit, for its reconciliation. | `integrations` with one disposition per obligation, `answers`, further `selected_groups` and `retrievals`; or the full card of the unit. |
| `answer` | One page of a paged final: the fields it owns and the integrated conclusions. | `pages` with one page that writes only its owned fields. |
| `final` | The integrated claims, decisions, conditions, cards and findings; for a paged final, the composition root. | The result review or the adjudication object beside `terminal`, `assurance_checks`, `decision_checks` and `answers`; or `retrievals`; or `assessment_incomplete`. |

The prompt of a task is the role's current template text followed by the
staged texts of the task, under the template id
`exactory-review-staged-v2/<role>/<type>`, with `parameters.staged_review`
(the protocol), `parameters.staged_task` (type, scope, claim ids, occurrence
ids and group ids), `composed` (true for the terminal task of a paged final),
`claim_range` (true for a task over a part of the claims) and
`context_repair`. The packet of a task is `{protocol, subject, role,
staged_review, evidence: []}`, with the material objections whose evidence the
task holds and, for a repair, `context_repair`. `staged_review` carries `task`
(the frozen envelope), `claims` (each with its `context_root`), `decisions`
(the permitted values per decision path), `units`, `originals` (each input
span with its exact text; originals travel as text in the packet, not as
attachments), `frame` (the frame fields among the originals), `continuity`
(the id, the task and the raw output digest of each accepted dependency),
`index`, `index_root`, `inherited`, `refused_findings` (the adverse records of
refused outputs, below), `output_tokens` (the route's `max_output_tokens`
minus the planner's margin of one tenth of it; the planner sizes the task
with the same margin),
`context_graph_root` once a claim context was expanded, and the section of
its type: `frame`, `fragments`, `combine`, `segments`, `original_check`,
`integrate`, `answer` or `final`. A task that reads originals again for a
pending retrieval also carries `retrieval`, `retrieval_observations` and
`reread`. The accepted outputs of the dependencies reach a task only as
records inside this packet; no earlier turn is replayed, and every request has
`history: []`. On `codex_cli_visual_v1`, a task whose original is a binary
body receives every page of that PDF as an image under the route's existing
native page contract, `staged_review.binary_sources` names those originals,
and acceptance credits the binary only when the actual request carried every
page. On another route such a task is `review_staged_binary_unsupported`, and
the planner records its work as an execution-origin incomplete outcome
(below).

Sizing. Every task is measured in three dimensions before it is admitted: the
characters of its input, the output tokens it is projected to need and the
seconds it is projected to take. The projection uses one profile per task
type. The profiles of this release are the calibration version
`unqualified-planning-v1`: bootstrap estimates (the characters of the prompt
text, 4,000 envelope characters, 128 base output tokens, 100 output tokens
per claim, 0.01 output tokens per character of originals and 0.5 for a frame
task, 0.0001 seconds per character and 0.1 per claim), raised by the observed `usage` of the
captures on the same route, output tokens and wall seconds per character of
work. A task is admitted when its input fits the route limit, its projected
output plus the margin fits `max_output_tokens`, its projected duration fits
the route's timeout, and the fitted request preserves the mandatory material
(`input_fits`, `output_fits`, `duration_fits`, `mandatory_preserved`). The
measurement is frozen on the task.

Planning. `review-assignment {controller_assignment_id}` plans the ready
frontier of the current epoch in one store transaction and records it.
When a post closes the chain of the controller, the command is refused with
`problem_already_posted` before any planning condition, and `--check` gives the
same refusal ([Problem creation](#problem-creation)).
It checks these conditions in order and returns the first that applies: an
unreleased reservation of the epoch (`next` is `review-run ... action:
"reconcile"` for it); a slot head in state `incomplete` (the outcome and its
recovery work); an accepted final whose completion check passes (`next` is the
`value-review` or `review-adjudication` payload `{id: "review:<controller
id>", assignment_id: <controller id>, ...}` built from the effective result,
and, once that record exists, `complete: true` with an empty `next`); the call
budget or the elapsed-time budget of the epoch reached (an execution-origin
incomplete outcome). Otherwise it collects the ready tasks. A task whose two
attempts were both refused or failed is not run again: when it is a final
task whose refused output is a complete review, the planner freezes a
composition plan from that output and plans answer pages and a terminal
(paged recovery); otherwise it splits the task into two children along one
dimension (claims, answer ranges, input spans, card segments or obligations,
and for an integration task its child receipts, cards or obligations),
records `review_task_repair/<task id>` with the `child_ids`, and counts the
split against `max_subdivisions`; a task that cannot be split ends in an
execution-origin incomplete outcome. When no task is ready, the planner
plans in this order: the rereads that pending `retrievals` request, the answer
tasks of a composition plan, the remaining required tasks of the core planner
(read tasks partitioned by claims and by source spans so that each fits, a
reconciliation for a unit read in segments, the combine tasks, one original
check per selected group, integration and the final), and, when every
occurrence has a complete card and no task remains, the reconciliation of
several integration roots, an integration over the cards, or the final. It
then freezes one ready task (its packet, prompt, request and measurement; a
task that does not fit is split before it is recorded), records the child
assignment `task-assignment:<digest of [epoch id, task id]>` itself, and
returns `review-run {assignment_id: <controller id>, epoch_id, task_id}` as
`next`. The frame tasks are planned when the controller is recorded: the
frame fields are grouped into spans of the largest size whose joint
measurement fits, and a group whose request still does not fit is halved. A
frame that cannot be rendered at one character ends in an execution-origin
incomplete outcome at creation.

Rereads. A `retrievals` entry of an integration or final task asks for a
rereading of named spans for named claims. Entries whose normalized question,
source versions and claim contexts are the same share one obligation and one
reread budget, `max_rereads` rounds of reading; the round number is carried
in the task's `obligation_ids`, so a subdivided reread task consumes the round
of its parent and not a new one. The planner plans the read (or frame) task of
the next round, then a `final` task of scope `segmented_original` that
resolves the question from the rereads. An entry the reviewer returns as
`unresolved`, or a question that is still pending at the limit, ends in an
execution-origin incomplete outcome that names the question, its spans and
its claims.

The context graph. A read task returns `new_dependencies` and a frame task
`dependencies`: claims whose context the reviewer found to need another span.
The harness derives a versioned context graph from the accepted outputs, each
version with a `root` and its parent, and records the graph a task was planned
under with the task (`review_task.context_graph`). A task binds the roots of
its claims in `context_roots`. A task planned under an older version is
superseded (`task_supersessions`, reason `context_expanded`) once current
tasks cover every obligation it held, and it leaves the frontier; a child
assignment of such a task is refused with `review_task_dependency_changed`,
and so is its run. Since 0.71.0 the same code refuses an ordinary review
assignment, run, probe or output correction when another process's commit
changed a record that its preparation read (an unrelated commit leaves the
preparation valid, and the finishing commit of a run tries again by itself),
and the repair is to run the same command again. A frame task's claims, conditions, dependencies and
unresolved items enter the claim graph, and an unresolved item must be
answered by a grounded `answers` entry of a later task before the completion
check passes.

The operator's commands. The commands are the same as for an ordinary review,
and `next` names each one with its exact payload. After the controller's
`review-assignment`, the sequence is `review-assignment
{controller_assignment_id}`, which plans and assigns the next task, then
`review-run {assignment_id: <controller id>, epoch_id, task_id}`, which runs it
through its child, then `review-assignment {controller_assignment_id}` again,
until `next` is the `value-review` or `review-adjudication` payload. `review-run`
with `action: "accept"` resumes the acceptance of a committed capture, and
with `action: "reconcile"` reconciles the task's worker; neither makes a
reviewer call. An `id` names the attempt; without one the harness derives it,
a rerun keeps the attempt that owns the capture, and a refused capture gets a
new attempt id for its retry. A child assignment can also be recorded by
hand: `review-assignment` with the controller's `role`, `reviewer_id`,
`author_id`, `route_id` and `dossier_id` and `context:
{controller_assignment_id, task_id}`, plus `task` with a sealed envelope for a
task that is not recorded yet and `composition_plan` for an answer or
terminal task. The child carries `staged_review: {protocol, kind: "task",
controller_assignment_id, epoch_id, task_id}`, `staged_task`, the task's
packet, prompt, request and `planned_request_digest`, and `history: []`. One
task has one child; a second is refused with `review_resampling_forbidden`,
a child of an epoch that is no longer the slot head with `review_epoch_stale`,
and a child of a task whose claim contexts are no longer current with
`review_task_dependency_changed`. The review is the controller's effective
result: `value-review` or `review-adjudication` records it naming the
controller's assignment id.

What `next` shows. For each task of the current epoch, `next` and the post
gate list one obligation with `task_id`, `state`, the task's `resource_ledger`
and `next`, the exact command payloads:

| State | Code | `next` |
| --- | --- | --- |
| `ready`, no child yet | `review_task_pending` | `review-assignment` with `context: {controller_assignment_id, task_id}` and `id: task-assignment:<digest>`. |
| `ready`, child recorded | `review_task_pending` | `review-run {assignment_id, epoch_id, task_id}`. |
| `waiting` | `review_capacity_wait` | `review-run ... action: "reconcile"` for each active reservation, listed in `reservations`. |
| `blocked` | `review_task_pending` | Empty while a dependency is not accepted; otherwise a recovery entry with the prerequisite `measured_rendering_and_admission` or `smaller_admissible_task`. |
| `reconciling` | `review_worker_reconciliation_required` | `review-run ... action: "reconcile"`. |
| `captured` | `review_observation_acceptance_pending` | `review-run ... action: "accept"`. |
| `accepted` | none | Empty. |
| `historical` | `review_epoch_stale` | A recovery entry naming the predecessor epoch and the prerequisite `authorized_successor`. |
| `exhausted` | `assessment_incomplete` | `review-assignment {controller_assignment_id}`, which subdivides the task or records the incomplete outcome; never the exhausted task again. |
| `incomplete` | `assessment_incomplete` | The recovery work of the incomplete outcome. |

When every recorded task is accepted and the epoch has no effective result,
`next` lists one `review_task_pending` whose `next` is `review-assignment
{controller_assignment_id}`. The `--summary` views carry the first action of
the selected obligation as `next_step` when it fits in 1,024 bytes, and the
count of the other actions as `omitted_next_steps`.

Capacity. The default is one reviewer call at a time per store.
`review-assignment` with `scheduler: {store_limit, route_limits,
covered_adapters}` records `review_capacity_policy/store` (version 1, scope
`store`); `route_limits` maps a route id to its own limit, and
`covered_adapters` defaults to the four local adapters. Every covered call
passes one admission in the store transaction that records its start: the
reservation of a staged task, and the start of an ordinary `review-run`, a
`review-probe` and a `review-output-correction`. Admission counts the active
reservations and the pending ordinary invocations, probes and corrections on
covered adapters, refuses with `review_capacity_wait` and the active
reservation ids when the store limit or the route limit is reached, and
otherwise writes the reservation and its pool membership together. When
`review-budget` has recorded an allowance of calls (its row under "Scientific
contribution decisions"), the same admission first refuses with
`review_budget_exhausted` when no call of the allowance remains. A route
outside the covered adapters cannot reserve a task
(`review_route_unsupported`). Lowering a limit admits no new call until the
active calls are below it. A reservation is released only after the harness
confirms that the worker and its process group ended; the passing of time
alone never releases one.

Capture, acceptance and recovery. `review-run` reserves the task
(`review_reservation/<id>`, `review_task_execution/<task id>` and
`review_execution_plan/<plan id>`), launches an owned worker process, gives it
60 seconds to bind its process identity to the reservation (the launch
handshake), waits for its capture up to the route's timeout plus 15 seconds,
accepts the capture and reconciles the worker. The worker holds a lock under
`research/review-workers/`, makes the one reviewer call and commits the
observation as `review_observation/<reservation id>` through the store. A
response file that no worker committed is no observation. Capture is that
commit. The attempt (`review_attempt`) and its binding to the capture
(`review_capture_attempt/<reservation id>`) are recorded before acceptance, so
a refused or failed capture keeps its attempt. Acceptance is a separate write,
`review_task_acceptance/<reservation id>`: the harness authenticates the
capture against the frozen request, binds every quotation of the output to
its span in the source (a quotation that is not found invalidates the record
that carries it, and the receipt lists it under `unmatched`), checks the
output against the task's shape, records the task's actual input, and writes
the index `review_task_acceptance_index/<task id>`, through which a later task
reads exactly the dependencies it names, so that the acceptance of an
unrelated sibling does not invalidate a prepared commit. A `review-run`
interrupted after the capture resumes the acceptance on its next call without
a second reviewer call; one interrupted while the worker still runs waits for
the capture again. When the worker ends without a capture, the reservation is
reconciled as `unknown` (it was running) or `known_failure` and released,
`review-run` refuses with `review_worker_reconciliation_required`, and the
next `review-run` of the task makes the one permitted retry. A launch
handshake that does not complete within its 60 seconds, or that an
interruption ends, is refused with the same code, and the reservation keeps
its claim for `action: "reconcile"`. A capture whose
status is not `completed` is a failed attempt under the same rule, and its
result names the planning command as `next`. An output that fails its task
check is refused: the harness records `review_task_refusal/<reservation id>`
with the code, the message and the digest of the capture, moves the
reservation and the task head to `refused`, and returns the attempt with
`refusal` and the planning command as `next`. The capture keeps its bytes as
the task's raw observation. The next `review-run` of the task sends the same
request once more; when that capture is refused too, the planner subdivides
the task or plans its pages, as above. The adverse records of a refused output
(findings marked `adverse`, objections, conditions and unresolved entries)
travel to every later task as `refused_findings` with `accepted: false`, into
the incomplete outcome and into a successor epoch; they earn no credit. A
third `review-run` of a task is refused with `review_task_exhausted` and
records the execution-origin incomplete outcome. Capture and
acceptance commit through task-scoped writes (`Store.mutate_scoped`), so a
commit of another task of the same epoch does not invalidate them.

The resource ledger. `review_resource_ledger/<epoch id>` is charged in the
transaction that reserves each task: `attempts`, `retries`, `rereads` (the
reread rounds), `subdivisions`, `epochs` (the epochs of the slot with the same
evidence currency), `elapsed_seconds` since `staged_started_at` and
`unknown_usage`. A reservation that would exceed a budget is refused and
writes `review_execution_exhaustion/<id>` with the limit and the ledger
(`review_task_exhausted` for the per-task cap, `assessment_incomplete` for a
budget of the epoch); `review-run` then records the execution-origin
incomplete outcome and the planner returns its recovery work. A ledger for an
epoch written before this record is reconstructed once from its reservations
and marked `unknown_usage`.

Raw observation and effective result. `assignment_state` carries both:
`raw_output` and `raw_output_digest`, the exact object the call returned, and
`effective`, the scientific result assembled from the accepted tasks:
`{kind, effective_output, effective_output_digest, composition_version,
composition_root, selected_observations_root, completion_root,
terminal_attempt_id, terminal_raw_output_digest, currency_state,
context_state}`, with `raw_page_bindings` for a paged result. `kind` is
`ordinary` (the final task fit one input and returned the review beside its
staged records), `paged` (answer pages and a terminal bound under a
composition root), `incomplete` (with `incomplete_outcome`) or `pending`. The
compatibility field `output` of a controller is the effective output, and null
otherwise; a child's `output` is always null, and its `accepted` flag says
whether its task was accepted. The state's `next` is the planning command.
Its obligations are `review_task_pending {controller_assignment_id, epoch_id}`
while the epoch has no effective output, `review_epoch_stale` when the problem
evidence or the subject changed or the slot has a successor, and
`review_invocation_unverified` or `review_context_contaminated` for a selected
task whose capture does not authenticate or that received foreign input.
Authentication, refusal classification and output correction read the raw
observation; recording, the independence check, coverage, objections, context
repair and the export read the effective result. `value-review` records the
effective output exactly, writes `effective_binding` on the review record,
moves the slot head to `complete` with the record's id and output digest, and
refuses an incomplete assessment with `assessment_incomplete`. The problem
export adds `reviews.json` with each review's output and binding when a review
of the decision is staged, and refuses an epoch without a completed effective
result (`assessment_incomplete`).

Grounding. Every conclusion and condition of a card, an original check or an
integration is resolved from its own grounds and its own dependencies, not
from the card it belongs to. A ground is an exact span of a source, which must
quote text that is not empty after normalization, or the id of a resolved
record. A condition the reviewer marks `satisfied: false` is unsatisfied, and
an assurance that depends on it cannot pass. An answer page's `grounding`
entries are checked against what the answer task received: a quotation must
lie inside the spans the task's packet delivered and match the source, and a
record ground must be the exact accepted, resolved record that the packet
carried; a page whose grounds fail is refused (`review_composition_conflict`)
at the acceptance of its task, again at composition and when the task graph
is collected. The checks
establish shape, identity, delivery and quotation, not scientific truth.

The completion report. The completion check of an epoch writes a report under
the schema `exactory-review-completion-v2`. `issues` holds one entry per issue
code, sorted by code, each with `count` (the exact number of its rows),
`digest` (the SHA-256 of the exact sorted row set as canonical JSON) and
`sample` (at most 20 rows); `context_roots` and `coverage` carry the same bound
with `context_roots_count`, `context_roots_digest`, `coverage_count` and
`coverage_digest`; `complete` is true when `issues` is empty. Completeness is
decided from the exact set, never from a sample, and the missing claim cells
are counted without being listed one by one.
`research_harness.review_staged.iter_completion_issues(epoch, selection,
code=, offset=, limit=)` reads the exact rows of an authenticated selection.
The issue codes are `review_task_pending`, `review_source_coverage_pending`,
`review_claim_cell_pending`, `review_card_pending`,
`review_card_question_pending`, `review_frame_unresolved`,
`review_finding_answer_pending`, `review_combination_pending`,
`review_original_check_pending`, `review_integration_pending`,
`review_integration_unresolved`, `review_claim_unresolved`,
`review_retrieval_pending`, `review_composition_pending`,
`review_assurance_dependency_pending`, `review_terminal_pending`,
`review_task_dependency_changed`, `review_objection_evidence_pending` (no
accepted observation has delivered the named entry of a material objection
yet), `review_objection_evidence_invalid` (the entry was delivered with the
wrong digest), `invalid_review_staged` (an accepted record is malformed; the
row names its task) and `assessment_incomplete`.

Incomplete assessments. An incomplete outcome has one of two origins. With
`origin: "reviewer"`, a final task returned `assessment_incomplete
{unresolved_scope, recovery_work}` instead of a review, or the pages of a
paged final were planned under the composition role `incomplete`. With
`origin: "execution"`, the harness recorded it itself: a task whose attempts
are used up and that cannot be split, an unknown worker outcome, the call,
time, subdivision or reread budget of the epoch, a binary original that the
route cannot deliver, or a frame that cannot be rendered; `origin_binding`
then names the `execution_receipts` (the reservations, refusals, repairs or
exhaustion receipts) and the `reason`. Both write the record
`assessment-incomplete:<digest>` with the exact epoch, slot, role, subject,
evidence currency, reviewer, route and author, the roots of the selected
observations, the completion report, the known conclusions, the unresolved
scope and the inherited adverse findings, the `resource_ledger`, the unresolved
entries (each with its scope, cause, affected decisions, exhausted tasks, scope
kind and next action) and the recovery work, and move the slot head to
`incomplete`. The record states no status, grade or disposition. The effective
kind is `incomplete`, the slot stays occupied, and `next` lists the recovery
work and never a `review-run` of an exhausted task; when every action would
repeat one, it lists a research entry per unresolved scope instead. The
recovery work of an execution-origin outcome is a successor `review-assignment`
with `context: {staged_upgrade: {assignment_id: <controller id>}}`, and its
unresolved entry names the prerequisite `new_evidence_or_eligible_recovery`.
A new assessment of the slot is that authorized successor, bounded by
`max_epochs` for one evidence currency.

The post gate. The gate reads the occupancy of every current result head,
whatever kind of series it is. The kinds are an ordinary review, a parts v1
series (0.69.x), a parts v2 series and a staged review. A head without a
recorded final is pending, and a staged head can also be incomplete. When a
current result head is incomplete, the gate lists `assessment_incomplete`
with `occupied` (the number of occupied slots), `incomplete` and `pending`,
also when another head is pending. When heads are pending and none is
incomplete, it lists `review_task_pending` with the same fields. Only
completed heads form the pair, and the gate asks for no replacement second
reviewer while both slots are occupied. When fewer than two slots are
occupied, the gate also lists `problem_review_pair_required` next to the
pending work of the occupied slot. That obligation reports that the
independent pair is missing, does not name a slot, and carries `review_ids`
(the current recorded reviews, possibly empty). A completed peer of a pending
head is checked for independence, assurances, rubric and coverage. A record can
therefore show the findings of that peer while the other slot is pending.
When every head is complete, the gate checks the pair as before. `next` names
concrete continuation work, such as `problem_evidence_part_pending`, before the
`review_task_pending` of the pending heads. When that obligation is first
(for example, with two pending ordinary successors), `next` lists the planned
actions of the pending heads that are not staged reviews (the plan of
`review-series`, for example `review-run`). In this way `next` continues the
pending series, and the obligation itself keeps no actions. `plan_next` does
not branch on these codes. A recorded review of a slot whose latest
controller is a newer assignment leaves the current pair until that
controller completes. For a
staged review the result coverage check reads the accepted task graph
([Problem creation](#problem-creation)). Each staged slot adds
`assessment_incomplete` (with `outcome_id`, `origin`, `unresolved_scope`,
`resource_ledger` and `next`) or the task obligations above, and always
`review_quality_measurement_pending {route_id, protocol,
calibration_profile_version}`: the measurement that compares a staged review
with a single-input review has not run, so the staged mode decides no posting
in this release, and no author flag lifts the obligation; a slot delivered in
parts carries no such obligation, and its completed series can make the gate
ready. The gate report carries `slot_outcomes` (`slots`, `occupied`,
`complete`, `incomplete`, `pending`, `adverse_findings`,
`assessment_complete` and `ready`) in a record with a staged or parts v2
result review, or a pending or incomplete current result head. The `review`
section of `status` carries them in a record that holds an epoch.
`status --summary` reduces them to
`review_slots: {occupied, complete, incomplete, pending}`. A record whose
heads are all complete keeps the report of 0.73.2. `status` also
reports each staged assignment's `epoch_id`, `kind`, `raw_output_digest`,
`effective_output_digest`, `composition_root`, `incomplete_outcome` and
`tasks`. Two conditions sort the obligations of a problem record
(preparation order, then version, then a stable digest). `status` and `next`
sort them when the record holds a staged epoch, a parts v2 series, or a
pending or incomplete current result head. `status` also sorts the whole
list, in every record, when it adds the obligations of a review chain head
that no gate obligation names (for example `review_output_pending`). This
was already so in 0.73.2. A record where neither condition applies keeps the
gate's own order. In a record that holds an epoch, the sorted order places
the obligations of a staged review before the scientific shortfalls. In such
a record, `problem_assurance_pending` and `research_objection_unresolved`
carry a research `next` (the workflow of the assurance, or the objection's
resolution condition).

Focused adjudication. A staged adjudicator's epoch freezes `focused_scope`:
the digests of the two result reviews named in `context.review_ids`, the
objection id and the digest of the objection as delivered. The final task's
terminal chooses the disposition; `review-adjudication` records the
controller's effective result, writes `effective_binding` on the adjudication
and moves the slot head to `complete`. It is refused with
`review_task_pending` while the task graph is incomplete, with
`review_adjudication_mismatch` when the review pair or the objection version
differs from the frozen scope, and with `assessment_incomplete` when the
adjudication is incomplete; the objection then stays open, and the
adjudication state carries `incomplete_outcome`. A source review of the pair
that is itself staged is read through its recorded `effective_binding`.

Upgrading a recorded session. `review-assignment` with `context:
{staged_upgrade: {assignment_id}}` opens a staged review in the slot of a
recorded `result` or `adjudicator` assignment of the same role, reviewer,
author, route, subject and dossier: a session that is a series in parts, one
without an output, a completed one whose slot needs a new assessment, or the
controller of an incomplete epoch. It is refused while the predecessor has a
pending invocation or an unreleased reservation
(`review_worker_reconciliation_required`). The new epoch binds the predecessor
as above and inherits its recorded reviews as historical reviews and their
material objections, the controller's packet carries `staged_review` and a new
digest, and the slot head moves to the new epoch with the earlier state in its
`history`, so the earlier review of that slot leaves the current pair.
Recording the successor's review does not collide with the predecessor's.
The scientific predecessor of an assignment is read from
`context.prior_assignment_id`, `context.context_repair.assignment_id` or
`context.staged_upgrade.assignment_id`, in that order, by every consumer.

Context repair of a staged slot. A `context_repair` assignment of a `result`
slot whose predecessor is a controller is itself a controller when its
ordinary repair request fits one input; a repair whose request does not fit,
or whose earlier attempt with the same role, dossier, slot and repair
predecessor ended over the output cap, and every repair of an `adjudicator`
slot on a route with a local input limit, run as a parts v2 series
([Reviews in fresh parts](#reviews-in-fresh-parts)). A controller repair
waits for pending workers and invocations
(`review_context_repair_pending`), authenticates each staged task of the
predecessor's history against its frozen request and committed capture,
carries the predecessor's raw observations, intermediate findings and
effective binding, reads the pending source requests of the predecessor with
its epoch's manifest, keeps an adjudicator's objection and review pair, and
on the visual route opens a fresh epoch instead of replaying the visual
history. A repair of a 0.69.x series needs no recorded final for a part turn.

Recorded 0.69.x series. A recorded part or final turn authenticates as
recorded. A turn that continues such a series with the same evidence digest,
before the result review of its final turn is recorded, continues it under
the rules of the next section. A continuation whose evidence digest changed
and a refresh of the series start new work under the entry rule above
(ordinary when the complete request fits, otherwise a parts v2 series), and an
explicit `staged_upgrade` moves the recorded slot to a staged review at once,
under the scale preflight. No module that a route pins by its bytes
changed, and the reduction orders do not change.

Views. `intermediate/reviews/staged/<kind>/<id>.json` holds the epochs, the
tasks, the execution plans, the task executions, the reservations, the
observations, the acceptance receipts, the refusals, the repairs, the pool
memberships, the capacity policy, the slot heads, the dispatch records and the
composition plans. The acceptance index, the epoch discoveries, the resource
ledger, the exhaustion receipts and the capture attempts are store records
without a view. `final/reviews/incomplete/<id>.json` holds an incomplete
outcome of the current epoch of its slot, and
`final/history/reviews/incomplete/` one of an earlier epoch. A controller's
turn in `intermediate/reviews/<role>/<root>/` adds `raw_output_digest` and
`effective`, and its effective output is written as `<NN>-<assignment
id>.effective.json`; a child's turn names its `task_id`. A recorded review or
adjudication whose slot has a staged successor is `historical`.

### Problem evidence in parts

This delivery, `exactory-problem-evidence-parts-v1`, serves a `result`
session that is already a series. Since 0.72.0 no new series starts under it.
A turn that continues a recorded series with the same evidence digest, before
the result review of its final turn is recorded, continues under these rules;
this includes a series started under 0.69.0 to 0.69.4. A continuation of a
series whose evidence digest changed, a refresh of a series and a first turn
of a session that never was a series are measured against the route limit: a
request that fits and delivers every body is built as before, byte for byte,
with the same packet and prompt; one that does not starts a parts v2 series
([Reviews in fresh parts](#reviews-in-fresh-parts)). A `staged_upgrade`
assignment moves a recorded slot to a staged review at once
([Staged problem reviews](#staged-problem-reviews)).
The rules below describe the series that continue. They apply
to a `result` session on a route with a local input limit (`claude_cli_v1`,
`codex_cli_v1` and `codex_cli_framed_v1`); a `complete-visible-v1`
continuation and a bar or slate packet, which carries no problem evidence, are
never a v1 series.

A series is the turns of one result session that deliver one problem evidence
digest in parts; it is named by the id of its first assignment
(`series_assignment_id`). The frame is the keys `claim`, `openness`, `value`,
`assessment`, `attempt`, `results`, `citations` and `delivery` of
`problem_evidence`; every turn of the series carries the frame whole. The units
are the items of the other six keys, the split keys, in this order: each
`openness_searches` item, each `assessment_evidence` item, then for each
reading its `readings` entry followed by its `passages` items, then each
`computations` item and each `formal_checks` item. A unit is named by its key
and its index in the full list of that key, and an item that the evidence
lists under two keys or two indices is a unit under each. A passage is the
smallest unit and is never split. A unit owns the artifact bodies that the
coverage check requires for it: the artifact of an `assessment_evidence` item
that is an artifact descriptor or a `local_artifact` record, and the artifacts
of a `computations` or `formal_checks` item. Every turn's `problem_evidence`
keeps the full key set: the frame keys whole, and each split key with only this
turn's items in index order, an empty list when the turn delivers none. A turn
that delivers passages of a reading also carries that reading's `readings`
entry, so that the reviewer has the inspection record next to the passages.

The harness plans each turn when `review-assignment` records it, after the
turn's history is known. A unit counts as delivered when a valid turn carried
its exact current item under the same index and valid turns cumulatively
attached every artifact body that it owns. A valid turn is observed in a
verified context, its sent packet has the series evidence digest and the same
series id, its part response passed its check, and its turn number and
`delivered_before` continue the earlier valid turns. A part turn must complete
a unit or attach an owned body that no earlier valid turn attached. Its receipt
keeps every carried index, including a unit that is still incomplete.

A unit's characters count its item and its pending owned bodies as canonical
JSON. A part turn takes the longest fitting prefix of the remaining units
within 800,000 characters (`PART_UNIT_CHARACTERS`), checking the fitted request
as the transport builds it. When the first remaining unit cannot fit whole,
it travels alone, bypassing that cap: the planner carries its item and adds
pending bodies one at a time in owned-artifact order while the request fits.
It leaves a body pending when it cannot fit with the bodies already selected
and tries the next body. Tiny bodies that shrink their pending entries can
collectively make the initial request fit.
An incomplete carried unit stays in `remaining` and travels first in the next
turn, with a new note required for its item again. Each owned body not attached
in this turn has an empty evidence entry with encoding `omitted`: delivery
`delivered-in-an-earlier-part-turn` and `turn` naming its earliest valid
delivery, or delivery `delivered-in-a-later-part-turn` when it is still pending.
The entry is matched by the full artifact descriptor, including when two
descriptors have the same SHA-256. Duplicate units therefore carry their items
without repeating bodies that earlier valid turns attached.

When the frame and item alone, or the item and its next pending body, cannot
fit and no selection makes progress, `review-assignment` is refused with
`review_input_too_large` and the details `{stage, turn, input_characters,
input_limit_characters, frame_characters, unit}`. `input_characters` measures
the fitted request that could not advance delivery, and `unit` is `{key,
index, digest}`; nothing is recorded, and no attempt is written. The final turn
is planned after every unit is complete and carries no units; when its frame
alone does not fit, it is refused the same way with `unit: null`.
The fitted final request must attach every artifact body referenced by the
material objections' `version_evidence`, matched by the full reference. If
the transport elides any of these bodies, planning refuses the final turn
with the same error, including a final turn that answers source requests.
Before its digest is computed, the final packet keeps each distinct material-objection evidence entry once,
walking objections in order and each one's `version_evidence` then `version_source_mappings`.
Later entries with the same canonical digest carry `delivery: repeated-in-material-objections`,
`objection_id`, `field`, a zero-based `index`, `digest` and an explanatory `note` naming the first full entry
in the same packet. Evidence readers resolve these entries; each required artifact descriptor still needs its
body once. Part packets still omit both lists, and recorded final packets retain their original representation.

After such a refusal, the harness attempts replay reduction when the turn has a
history parent and that history contains a recorded result review but no
context repair or `complete-visible-v1` turn. It replans only if the history
passes validation and the reduction changes at least one response. Otherwise
the original refusal stands. The harness binds each message to its assignment
by lineage and replaces two kinds of assistant message with a placeholder: a
part response of an earlier series of the same slot that closed with an
authenticated recorded review whose projection is in the packet's
`historical_reviews`, and the final response of such a series when the history
holds a later recorded result. This replacement step leaves all bar responses,
the latest recorded result response, every response of the current series,
every response of a series without a recorded review and all user messages
unchanged. A placeholder replaces a response only when it is shorter, and a
placeholder inherited from the parent is never rewritten. The placeholder is
the JSON object `{protocol: "exactory-problem-result-history-v1", delivery:
"output-elided-from-replayed-history", assignment_id, series_assignment_id,
turn, evidence_stage, output_digest, review_id, note}`: `evidence_stage` is
`"part"` or `"final"`, `output_digest` is the digest of the omitted output and
`review_id` is the review that closed that series. A final also carries
`later_review_id`, the latest recorded result of the history when the
placeholder was made. When the planner refuses the reduced history too, that
refusal stands and nothing is recorded. When the planner accepts the original
history, the fallback leaves the history and request bytes unchanged. The
assignment stores the reduced history for retries and request rebuilding.
Later assignments build their histories from the parent's actual request and
selected output. Existing assignments and requests remain unchanged. The
omission resets no excerpt, reading or passage allowance and changes no
full-text delivery accounting.

The restart into parts v2. Since 0.73.0 the planner's `review_input_too_large`
after the replay reduction, at either of its two refusal points, no longer
stands: `review-assignment` then prepares one parts v2 successor in the same
slot, session and reviewer identity, with predecessor links, a fresh v2
inventory, empty delivery credit (receipts, cards, combinations, findings)
and empty replay history, and with the inherited
`continuation.scientific_context` sentence removed. A normal continuation
payload is unchanged, and there is no restart flag. The successor records
`review_parts_restart`: `series_assignment_id` (the abandoned v1 series),
`reason` (the refusal code) and `details` (the planner's details). When the
v2 planning fails, nothing is recorded and the current series stands. A v1
continuation that fits is unchanged byte for byte (packet, prompt, history,
request). Captured search-response bodies that the prospective v2 planning
cannot fit are withheld, initially and on later turns; the search records,
source descriptors and omission notices stay, and the bodies of the units
and of requested source deliveries stay protected. Frozen inventories,
packets and request reconstruction are unchanged. Each result slot
contributes only the recorded final of its current series: an older final no
longer supplies the slot, for automatic, explicit and saved-decision pairs
alike, and a pending successor occupies its slot. The current result reviews
are the recorded finals of the current series of the two result slots. An
objection is resolved when both of them record an evidenced resolution of
it, and the packet of each delivered its current material version. It is
also resolved when a ready focused adjudication of its current material
version, bound to the current evidence digest, finds it `not_upheld` (or
`not_binding` for the problem). Superseded finals stay as history: they keep
their historical findings and resolve nothing in the current gate. An
objection that a superseded final resolved is therefore unresolved
(`research_objection_unresolved`) until both current finals, or such an
adjudication, resolve it. A recorded post of the current statement moves these
findings unchanged into `after_post`. Recording a new result of
a superseded series is refused (`review_series_superseded`, details
`assignment_id` and `current_assignment_id`), and a result needs the
authenticated complete final of the current v2 series (`review_parts_pending`
otherwise). Records made before stay valid: replay, historical
authentication and objection retention are unchanged. `status` and `next`: a
ready observed v1 head continues before any successor exists (an unready head
keeps its blocker); a new v2 root is pending at read turn 1 with zero credit;
an observed complete final that is not recorded is the obligation
`review_result_pending` with the `value-review` step; continuation work comes
before the generic missing-pair obligation. In the output views the new
series has its own folder, older finals move to `final/history/`, and the
index of the old series names the abandoned series, its successor
(`successor_assignment_id`) and the reason (`review_parts_restarts`).

The packet of a turn is the result packet as built above, with the bars, the
own bars, the material objections, the historical reviews, the review
currency, the evidence digest, the source inventory, the deliveries and the
excerpts, with the reduced `problem_evidence` and one more top-level key,
`problem_evidence_parts`: `{protocol: "exactory-problem-evidence-parts-v1",
series_assignment_id, stage, turn, units, delivered_before, delivered_now,
remaining, note}`. `stage` is `"part"` or `"final"`, and `turn` counts the
turns of the series from 1. `units` maps each split key to the count of its
items in the current evidence. `delivered_now` and `remaining` map each split
key to a list of index ranges `[start, end]`, `start` included and `end`
excluded, into the full list of that key; this turn's items of a key are
exactly the items of its `delivered_now` ranges, in index order.
In a part packet, each material objection omits `version_evidence` and
`version_source_mappings` and retains every other field. A part response gives
no finding on objections; the final packet delivers their evidence as usual.
`delivered_before` lists every earlier valid turn as `{turn, assignment_id,
attempt_id, units}`, with its selected attempt and the ranges that it
carried. `note` is one sentence that tells the reviewer that the evidence of
this review arrives in parts. A turn whose packet has no `reviewer_excerpts`
record, an initial assignment, adds an empty one; a turn that continues a
session, including part turn 1 of a series that a continuation or a refresh
started under 0.69.x, carries the checked excerpts of the session as every continuation
does. `problem_evidence_digest` and `problem_review_currency` are the same as
in a single-turn packet, and `evidence` and `digest` are computed for the
reduced packet. The prompt of a turn keeps the template id
`exactory-problem-review-v5/result` and the excerpt rules, adds the
instructions of its stage after them, and records `parameters.evidence_parts`
as `exactory-problem-evidence-parts-v1/part` or
`exactory-problem-evidence-parts-v1/final`. The rendered prompt hash differs
per stage, so each turn is bound to the text of its stage, and a recorded
attempt of either stage authenticates as every other attempt does.

A part turn returns no review. Its response is exactly `{stage: "result",
phase: "evidence_part", part, notes, excerpts}`, where `excerpts` is optional
and `part` equals the packet's `turn`. `notes` holds exactly one entry `{unit:
{key, index}, note}` for every unit of `delivered_now` except a passage, in any
order, with no entry for another unit and no second entry for a unit; a
passage is noted in the entry of its reading. Each `note` is nonblank text, and
the note texts together have at most 60,000 characters (`NOTES_CHARACTERS`).
`excerpts` follows the excerpt rules of the session. A part response carries no
`source_requests` key; the reviewer requests a source or a passage in the final
turn. When an authenticated run of a part turn completes, `review-run` checks
the response and, when it fails, writes the output refusal
`invalid_value_review` under the check `review.shape` and names it in its
receipt under `output_refusal`, as it does for a `native_math` review. A
refused part output delivers nothing, and its units stay remaining. The retry
after a refused output and `review_slot_exhausted` after two refused outputs
apply as above.

While the latest output of the session has `phase: "evidence_part"`, continue
it as a source request is continued: a new assignment of the same role,
reviewer, route and author with `context: {prior_assignment_id}` naming the
part turn. A part turn whose selected output is not a valid part response
cannot be continued (`invalid_value_review`). The harness carries the checked
excerpts of the session and plans the next turn from the valid turns of the
series: another part turn while units remain, else the final turn. A turn of a
series that is a continuation and whose run ended without output is run
again, waits, stops or is replaced by a new continuation of the same parent,
as above, under the outcomes of a failed reviewer call
(`review_assignment_replaced` for a replaced turn); a turn that is an initial
assignment keeps the retry of a failed call under the same outcomes
([Review chains and `review-series`](#review-chains-and-review-series)). A part turn with an output and no
continuation is the obligation `problem_evidence_part_pending {assignment_id,
turn, remaining}` in `next` and in the post gate, beside
`review_sources_pending`. `value-review` refuses a part turn with
`problem_evidence_part_pending` and writes no refusal record.

The final turn carries the frame, `delivered_before` with every earlier valid
turn of the series, and no units: each split key of its `problem_evidence` is
an empty list, and `delivered_now` and `remaining` list no items. It returns
the ordinary result review, or `source_requests` under the excerpt rules; the
continuation that answers them is a final turn again, with the frame, the
deliveries and no units. Record the turn that returned the review with
`value-review`. The output contract of a review of a series is checked against
the authenticated actual requests of the series, and a final turn without an
authenticated completed attempt is refused with `review_invocation_unverified`.
[Problem creation](#problem-creation) states how the post gate checks the
coverage of a review of a series and what that check guarantees.

A series freezes the evidence digest of its first turn. Once a session is a
series, a continuation whose current evidence digest differs does not continue
the series, and neither does a refresh of a series, a new assignment of the
same reviewer after the recorded result review of its final turn. Both start
new work under the entry rule of this section: ordinary when the request fits
and delivers every body, and otherwise in parts from part turn 1. A later
turn of a refreshed session carries its `problem_refresh` marker, and the
marker alone starts no new series. A continuation of a series with the
unchanged digest receives the full current problem evidence and never the
`delivered-in-an-earlier-turn` marker, because the replayed history of a
series does not carry the evidence whole.

The problem adjudicator and a context repair are not delivered under this
protocol. Since 0.72.0 an adjudicator request that does not fit one input,
and a repair request that does not fit, or whose earlier attempt with the
same role, dossier, slot and repair predecessor ended over the output cap, are
delivered as parts v2 series
([Reviews in fresh parts](#reviews-in-fresh-parts)); an explicit
`staged_upgrade` that names a recorded adjudicator assignment still opens a
staged review in its slot
([Staged problem reviews](#staged-problem-reviews)). A context repair of
a slot whose session holds a part turn keeps each part turn in its scientific
history as `intermediate_output` and needs no recorded final for it; a final
turn of the series that returned a review must be recorded before the repair,
as before (`review_context_repair_history_unrecorded`).

On the `codex_cli_v1` route, a problem result continuation can set
`context.source_delivery: "complete-visible-v1"` with `prior_assignment_id`.
For each full text that its result lineage requested and that is available with
its complete required source units, the same reviewer then receives one complete
authenticated main text per exact version, in a visible reversible encoding of
the logical messages. An incomplete or unqualified unavailable request stays
pending. A request answered as `unavailable_qualified` with its current
availability evidence is settled without reading credit; it does not enter
complete-source selection. Further source requests remain actionable. Such a
continuation and a visual
route do not use the reductions above. The complete texts of such a
continuation are planned as one closure when `review-assignment` records it
(the source reply batch in
[Scientific contribution decisions](#scientific-contribution-decisions)): a
closure that does not fit the route's input limit is refused there with
`review_input_too_large` and `limiting_component: "complete_visible_closure"`,
before any invocation, and never becomes a partial review.

A final assessment binds its assignment. `value-review` and `review-adjudication`
check the observed output against its output contract: the shape of the payload,
its locators against the delivered bytes, its citations against the delivered
content, its bar ids against the delivered bars, its findings on the objections
that its own packet delivered and on the historical reviews of its lineage, a
reassessment against its frozen predecessor, and the adjudicator's objection id
and disposition. When the output fails one of these checks, the command returns
the error and writes one immutable `review_output_refusal` record for the
attempt, with the code, the name of the check and `details`. Since 0.67.0
`details` is an object: `message` is the sentence of the failed rule, the
message of the error; `path` is a JSON Pointer into the output that names the
failed field or item, for example `/value/bar_ids`, `/objections/0/evidence`
or `/passage_requests/0/anchor`; a passage or source request whose `id` is a
valid string adds `request_id`; a length or range check adds its measurement
and its limit, for example `normalized_characters: 18` with
`minimum_characters: 20` for a short anchor, `characters` with
`minimum_characters` and `maximum_characters` for the notes or the length of a
passage, `items` with `minimum_items` for a final turn without a passage
request, and `page` with `minimum_page` for an inventory page, plus
`maximum_page`, `page_size` and `total` for a page out of range; a span on a
delivered part that counts in the stored text adds `part_relative_span`, the
`start` and `end` of the same passage in the part (the excerpt rules in
[Scientific contribution decisions](#scientific-contribution-decisions)); a
closed object that the shared field check refuses, for example a passage request or an
excerpt, adds `expected_type`, `received_type`, `missing_fields` and
`unexpected_fields`, while a closed object of a native review and the top level
of a reading response carry only `path` and `message`;
and the checks of bars, work items, objections and candidates keep the
identities they gave before. A check that runs on the items of a list names the
item by its index or by its valid id, and the first failed check is the one
reported. A refusal recorded before 0.67.0 keeps its `details` as recorded,
`null` when its validator gave none, and its receipt is not rewritten. A `native_math`
review is recorded by `exactory-math`, not by this harness, so `review-run`
checks its output when an authenticated run completes. A proposal review and an
approval of a result or a recovery have the closed fields of the native review,
with the packet's exact `subject_digest` and `reviewer`; a proposal review and a
result approval also have its `claim_digest`. A result or recovery output with
the decision `revise` or `reject` is kept only as an assessment, and it needs
only that decision and a nonempty `reason`. A problem record's openness coverage
can be a nonempty string, list or object. A failure writes
the refusal record (check `native.output`) at once, and the receipt of
`review-run` names it under `output_refusal`, with the same `code`, `check` and
`details` as the record and the revision after it, so the diagnostic is read
from the receipt without opening the output artifact. A check that
depends on the store (a pending source request, a reading that is not current, a
conflict with a stored review) writes no record. An output whose provenance does
not authenticate is never refused. An answer that does not parse as JSON is a
failed attempt with the code `review_transport_failure`. Parsed JSON that is
not one object, or a response that is not complete, is a failed attempt with
the code `review_incomplete`. Neither is an output refusal. On the Claude route
this includes an answer that the `RETRACTED` correction form of the
[reviewer delivery contract](research-decisions.md#reviewer-delivery-and-disagreement)
does not cover: the whole answer is then read as one object. Since 0.73.0 a
completed response with a non-empty `id` whose answer text the transport
cannot decode first goes through the answer recovery of
[Review chains and `review-series`](#review-chains-and-review-series): an
answer it recovers is a completed observation with `answer_recovery`, and an
answer it refuses stays a failed attempt. A failed attempt
writes no refusal record and keeps the retry of a failed call under the
outcomes of that section. While every completed attempt of an
assignment is refused, and each refused output still fails its check,
`review-run` invokes the assignment again with the same packet, prompt, route
and request, and the reviewer receives nothing about the refusal. A refused
output that has become recordable binds again, and `review-run` refuses the
retry with `review_resampling_forbidden`. After two refused outputs,
`review-run` refuses the next invocation with `review_slot_exhausted`, and
`plan_next` gives the handoff of that code with its command
([Review chains and `review-series`](#review-chains-and-review-series)). A new
reviewer can take an exhausted initial bar slot (the handoff names
`review-assignment`); the same reviewer is refused. A bar continuation, a
slate or result slot and an adjudicator keep their continuity or their fixed
adjudicator, so after the second refusal their plan is a `stop` with
`review_slot_exhausted` and no command, and `review-assignment` of a new
reviewer is refused (`review_slots_fixed`, `review_resampling_forbidden` or
`review_adjudicator_fixed`). `status` lists the
refused attempts of each assignment under `review.assignments`, and
`usage_report` counts them as calls.

After recorded context contamination, first record every actual observed final
assessment in the inherited history with `value-review`, retaining the exact
response, adverse findings and assignment. If repair returns
`review_context_repair_history_unrecorded`, record the identified final before
retrying at the current revision. This preserves scientific history without
granting current independent approval. Corrupt or malformed raw outputs remain
explicit unresolved evidence. Pending source-request turns need no final record;
all their actual requests must remain pending through repair.

Repair and probe the actual context route, then create a strategic
`review-assignment` using this context, with actual saved IDs and the exact event
evidence:

```json
{
  "context_repair": {
    "assignment_id": "affected-assignment",
    "event_ids": ["recorded-exposure"],
    "probe_id": "verified-probe-after-repair",
    "reason": "The actual context route was repaired and tested.",
    "evidence": [{
      "path": "research/sources/objects/REPLACE_WITH_ACTUAL_DIGEST",
      "sha256": "REPLACE_WITH_ACTUAL_DIGEST",
      "size": 123,
      "media_type": "text/plain"
    }]
  }
}
```

`event_ids` names every recorded cause of the repair: each `review-context`
event of kind `contamination` or `route_retired` on an assignment in the
lineage of the predecessor. That lineage is the predecessor and every
assignment that it reaches through `prior_assignment_id`, `context_repair`,
`staged_upgrade` and `history_assignment_id`, with the task assignments of a
staged review among them. `evidence` holds the evidence entries of all those
events, as they are recorded. A lineage can hold more than one such event. For
example, a slot whose route was retired, repaired and retired again holds two
retirements, and its second repair names both events and carries the evidence
of both. The harness compares `event_ids` with the recorded events as a set
and the evidence entries by digest, so the order is free, and a further id or
entry is refused like a missing one. A repair whose `event_ids` or `evidence`
differ is refused with `review_context_repair_mismatch`. Its message and its
details give `expected_event_ids` (the recorded events, in the order of their
recording), `missing_event_ids` (the events among them that `event_ids` does
not name) and `expected_evidence` (the evidence entries of those events, in
the same order). When `expected_event_ids` is empty, the lineage records no
contamination and no route retirement, and a context repair needs one.

This retains the original reviewer slot, scientific findings and objections.
Previously delivered figures and PDFs return as actual attachments, and supplied
excerpts retain their original bounds.
Run its new assignment normally, then record the unchanged observed response,
including the required `reassessment` findings described in the
[repair contract](research-decisions.md#reviewer-delivery-and-disagreement).
Later exposure may invalidate its current approval while its exact original
prospective provenance still permits historical recording. Bind a successor
repair to all current events before using the finding as independent approval.

A repair labels an inherited review `confirmed` only when its output repeats that
review's support, value, objections and limitations exactly, under the objection
identities that its packet delivered; any change, including wording, is
`revised`. A prior material objection receives its finding in
`objection_findings` and is not restated in `objections`. A repair whose two
completed outputs were both refused no longer holds the slot, so another repair
of the same predecessor can continue it.

An ordinary third assignment cannot replace a completed adverse review. A
replacement for a failed initial assignment inherits its native fixed slot;
replacing a failed repair still requires explicit repair. Superseded assignments
cannot be retried or used for current approval. A decision requires two distinct
logical slots and reviewers; recording or repairing a final does not authorize
resampling it.

A managed `target` adds optional `decision_id` and `source_impact` to its existing
payload. A managed `cycle` identifies `decision_id` and `candidate_id`; its actual
test and resource limits must fit the approved tranche. Admission charges that
tranche and any explicit same-unit authority. Existing admitted legacy runs can
retain their outcomes, but a new commitment needs a current decision.

A new `manuscript-review` or `manuscript-prediction` requires `assignment_id`.
The observed manuscript response contains `{review, prediction, reasons}` and
both commands bind the unchanged corresponding content. Earlier review JSON
without an observed context stays in history as unverified. Later contamination
invalidates current independent approval without deleting scientific findings.
External `bind-verdict` likewise requires `assessment.assignment_id` from a
`verification` assignment whose context names `verification_task_id`, the exact
recorded task digest. Its observed output is `{verdict, checks}`. The original
paper, preparation and cohort are supplied without earlier verdicts; no strategic
research decision is required. Current posting rechecks isolation and the exact
unchanged verdict body.

Manuscript pinning consumes a result decision; managed publication consumes the
exact post-measurement decision. This creates no additional publication consent.

For managed studies, the four historical round commands are compatibility
adapters over the same decision. `round`, `round-review` and `round-admit` use
`{id, research_decision_id, reason}`; they neither resample approval nor renew a
tranche. `round-assess` also records `outcome`, `cycle_ids` and actual `evidence`
against the admitted question. The longer round payloads below remain the
legacy, non-strategic record format. Seven continue assurances and two stop
assurances are retained in the canonical independent findings.

## Explicit source-limited manuscripts

`publication-scope` records and selects an immutable `source-limited-v1` contract. `select-publication-scope` selects an existing contract ID or `null`. Both use normal expected-revision and request-ID semantics. `scoped-review` records a separately targeted independent review. Use `example OPERATION` for the payload shapes. The complete objective and old readiness reviews remain unchanged.

`gate readiness` still checks the complete objective. `gate manuscript-readiness` checks a selected supported manuscript, every ordinary evidence/development prerequisite, exact active deferral coverage, and an independent scoped acceptance. It reports manuscript readiness separately from the open complete objective. An adverse review permanently blocks its scientific target. Renaming IDs or selecting a later acceptance cannot remove that finding. A different scientific target needs a service-derived correction response covering every adverse predecessor, exact scientific changes, and a fresh independent corrective review. Scientific preparers and prior scope preparers cannot independently review the study.

The manuscript claim registry must match every accepted claim ID, statement, polarity, assumption, evidence mapping and public limitation. Each claim uses `claim` for its statement plus `polarity`, `assumptions`, `limitation_ids`, and the associated exact `public_limitations`. Manuscript reviews, three paired measurements and contribution analysis retain their scientific requirements. Managed research consumes the canonical post-measurement decision at the round and publication boundaries.

Selected source-limited publication commands always retain these flags on retries:

```sh
exactory-draft deposit --managed-only --scope-contract-id CONTRACT --scope-target-digest TARGET --production --publish --confirm-publish --pdf draft/paper.pdf --abstract-file draft/abstract.txt --creator AUTHOR
exactory submit --doi RECORD_DOI --managed-only --scope-contract-id CONTRACT --scope-target-digest TARGET
```

The paired scope arguments require `--managed-only`. Native state failures, stale scopes, declined reviews, changed manuscripts and round refusals stop before remote writes. Never retry a refused scoped command without its strict flags. Existing ordinary commands without flags retain their established direct fallback. Scope identities are retained in managed intents and historical receipts. Later source recovery invalidates current eligibility without erasing historical publication.

An optional immutable `scientific_delivery` array declares `{original_sha256, disposition, projection}`. Author artifacts require `disposition: project` with checked JSON locators, text spans or archive members; a `public` label cannot authorize raw authored bytes or override internal roles. Exact public source originals retain their provenance. Exact manuscript PDF, abstract, bibliography and claim registry are delivered as pinned; the authored `sources` archive always requires explicit checked member projection. Every required evidence locator must retain its exact value in a derivative, with scalar original hashes and locator mapping. Missing scientific context requires refusal or independent rejection, never silent omission. Initial scoped review receives the same mechanically bounded projections before acceptance. Blind manuscript packets exclude private instructions, preparer records, correction responses and earlier readiness verdicts. Identified corrective review receives the required finding response; round review retains its ordinary current manuscript reviews and scientific context.

Projection kinds are `json_locators` with native `locators`, `text_spans` with native text/span `locators`, `archive_members` with `{path, sha256, media_type, projection}` members, and `npy_array` for a complete primitive NPY array. Each includes a nonempty scientific `context`. Numerical derivatives preserve dtype, shape, C/Fortran element strides, every storage-order value and exact per-element storage hex. Integers use exact decimal strings, and nonfinite real/complex components use typed tags. Fixed-width Unicode retains interior NULs and exact UCS-4 padding bytes. Object/pickle, structured and native-endian dtypes are refused. Generic archives allow 1,000 members; complete declared NPY populations allow 20,000 members. Individual raw/derived artifacts and archive expansion are bounded to 64 MiB, headers to 64 KiB, rank to 32, aggregate numerical elements to 8,000,000, and the complete delivery including its manifest to 512 MiB. Exceeding a bound fails before destination creation and never truncates science.

A `json_locators` or `text_spans` derivative is a JSON object with `derivative: true`, `original_sha256`, `projection_kind`, `context`, `entries` and `locator_mapping`. Each entry is `{original_locator, value}`: `value` holds the selected value once, and `original_locator` is the declared locator without its `value` key plus `locator_digest`. `locator_digest` is the SHA-256 of the complete declared locator serialized as UTF-8 JSON with sorted keys, separators `,` and `:`, non-ASCII characters kept and non-finite numbers refused. A `text` locator keeps its `quote`, and a span keeps its `start`, `end`, `sha256` and `excerpt`. Each `locator_mapping` item is `{original_locator, derived_pointer}` with the same identity and the pointer `/entries/<index>/value`. Reviewer tools read `entries[i].value`. The manifest still carries each evidence reference's complete `original_locator`, including its value, and a `locator` into the derivative. A manifest locator whose value differs from the declared value, even only in canonical form, has no mapping item and is refused with `scientific_projection_incomplete`.

`source_component` declares `source_sha256`, `start` and `end` byte offsets for an unchanged JPEG stream within an established complete original PDF. Its exact bytes, native public source provenance and private-role precedence are checked; rendered or recompressed images cannot use this path. Required JSON values containing native public source descriptors retain those exact values and checked source closure. Unknown, authored or private nested descriptors fail. No projection or corrective-response permission overrides an authorization, preparer, acquisition or prior-review role sharing the same hash.

JSON validation and delivery share one byte decoder for JSON media types (including parameters and `+json`), structural JSON, UTF-8 BOM and UTF-16/32. Decoded JSON string values and keys also undergo privacy checks, with depth bounded to 40. Exact bytes remain unchanged; encoded nested artifact descriptors are refused rather than becoming an implicit delivery channel.

A media type that declares JSON requires strict JSON: finite numbers, no duplicate fields, no lone surrogates and depth of at most 40, measured on brackets outside strings. Bytes of another media type, and string values, that begin with `{`, `[` or `"` are JSON when a JSON decoder reads them (Python's `json` module, with integers of any length), and then the same strict rules apply, because as text their decoded strings would not reach the privacy checks. Bytes that no JSON decoder reads are delivered as exact text after the privacy check of the raw bytes and, for UTF-16 or UTF-32 text that begins with a byte-order mark or an ASCII character, of the text in UTF-8, so a plain-text transcript that begins with a bracket does not fail with `invalid_json` or the depth bound. The privacy check does not decode escape sequences in such text. Where the interpreter limits integer string conversion (`sys.get_int_max_str_digits()`, 4,300 digits by default), JSON with a longer integer fails the strict rules with `invalid_json`; an interpreter without that limit reads it.


`exactory-research` exposes the shared research store through Python 3.9 or later and the standard library. `exactory-lab`, `exactory-draft`, `exactory`, and the native math controller consume its current evidence at their own authoritative boundaries. A download, marker file, process exit code, old receipt, or stage name does not establish scientific readiness.

Follow the [managed research workflow](research-workflow.md) for the executable
tool use, milestone gates, evidence interpretation, independent review and continuation.

## Inputs and receipts

Each JSON mutation takes the same arguments:

```sh
exactory-research OPERATION --workspace /absolute/path/to/study --file payload.json \
  --expected-revision 42 --request-id study-specific-operation-001
```

Read the current revision with `status`. Use the same request ID and identical payload when retrying an interrupted operation. An identical committed request returns its original receipt even after later revisions. Reusing that ID with different content fails; a new operation at a stale revision fails. A historical successful receipt does not bypass a current gate. Acquisition also persists collection operations and individual page receipts. One read-only exception: `import-response --preview` (see "Operation fields") takes neither `--expected-revision` nor `--request-id`, and a real import without both is refused with `invalid_input`.

`exactory-lab init` and `exactory-draft init` also accept `--expected-revision` and `--request-id`. Retain those values and the original user arguments when retrying a lost success. They reuse the original generated timestamps and committed initialization, even after later study changes. Current workspace projections are repaired from current history, while existing context, literature, Git configuration and user files are preserved. A retry can finish an interrupted initial layout. Different user arguments under the same ID conflict, and a new ID cannot reinitialize an existing workspace. Legacy markers without a managed initialization require explicit adoption; an interrupted existing database still requires explicit recovery.

Every operation has an illustrative input available without a workspace:

```sh
exactory-research example init
exactory-research example cycle > cycle.json
exactory-research example bind-verdict > verdict-assessment.json
```

The complete inputs are in [research-cli-examples.json](research-cli-examples.json). These are JSON payload shapes, not acquired evidence or finished assessments. Replace the uppercase IDs, zero hashes, paths, source passages, claims, and judgments with actual records and observations. The examples intentionally have no resolvable evidence objects. The innovation example shows one case of each relation; current research preparation still requires the configured minimum of distinct external cases. No example certifies an assessment merely by being saved.

JSON must be UTF-8, an object of the documented shape, and contain neither duplicate keys nor nonfinite numbers. Unlisted fields are rejected by the corresponding domain operation. Mutation output is a JSON receipt with `revision`, `request_id`, and the operation-specific `result`; acquisition reports additionally expose their retained operation and collection status. A refused gate or mutation writes a structured `error` to stderr and exits nonzero. Read operations do not repair a journal or migrate evidence.

Every mutation also accepts `--check`, with the same `--file`, `--expected-revision` and `--request-id`:

```sh
exactory-research OPERATION --workspace /absolute/path/to/study --file payload.json \
  --expected-revision 42 --request-id study-specific-operation-001 --check
```

A check opens the store read-only, reads one snapshot, and runs the operation's own validation and preparation on it, in the order of the write. It writes no record, receipt, artifact or request reservation, and it creates no lock file. It prints the first refusal, with the code and the message that the write gives, or the diagnostic `{"check": "accepted", "revision": N, "recorded": false, "operation": NAME, "payload_digest": D, "snapshot_digest": D, "artifact_digests": [D, ...]}`. `revision` is the revision of the snapshot. `operation` is the store operation name, for example `review.route` for `review-route`. `payload_digest` is the digest of the canonical payload. `snapshot_digest` binds the database identity, the revision and the authenticated history. `artifact_digests` lists the SHA-256 of every artifact that the preparation read, verified or staged. Bytes that the preparation stores, such as the files of a `manuscript` pin, stay in memory; an object that already exists at their path is checked as a write checks it. An accepted check is a diagnostic, not a record: it satisfies no gate, grants no admission, is no attempt, review or decision, and cannot be cited as evidence. An accepted check of `cycle` gives `admit` nothing to admit; `admit` needs the recorded plan. The write that follows validates everything again on the store as it is then, and a store that changed in between refuses the write with `stale_revision`. A check needs an existing store and creates none: outside a workspace it fails with `migration_required`, and in a workspace without a store with `store_missing`, also for `init`, `adopt` and `problem-init`. A check keeps the request rules of the write. A request ID already committed with the same payload returns its original receipt, one committed with another payload fails with `request_id_conflict`, and a stale revision fails with `stale_revision`. The payload bounds above apply to a new request. `exactory-lab state set` and `exactory-lab decide` accept `--check` too and run the same check on their store operations, `workspace.state` and `workspace.decide`, with the same diagnostic. Without `--expected-revision` the check uses the current revision, and without `--request-id` it uses a new generated request ID, so the replay of a committed request needs its original `--request-id`. An accepted check of `state set` records no state and no draft record, an accepted check of `decide` records no entry, and neither writes a projection.

A check refuses with `check_unsupported` when the operation acts outside the store:

- `collect`, `resume`, `acquire`, `expand`, `fulltext`, `fulltext-start`, `fulltext-resume`, `import-response`, `import-oai-cohort` and `visual`, which acquire sources; `review-probe`, `review-run` and `review-output-correction`, which call a reviewer, and `review-series`, which executes the calls of a review chain and refuses before any workspace or model effect; and `reconcile-run`. A new request is refused before any effect. A request already recorded returns its receipt. `fulltext-status` only reads and has no `--check` flag; `record-check` checks a copy of the record ([Record check](#record-check)) and has no `--check` flag either.
- `batches`, `export`, `recover` and `restore`, which write files or recovery state, and `strategy-packet`, which can store a file in the artifact store while it builds the packet, for example a projected claim file, without a revision change. These five refuse before they open the workspace.
- `exactory-lab init`, `run`, `probe` and `colab-serve`, and every `exactory-draft` command: `init`, `package`, `deposit`, `reconcile` and `attach-problem`. They create a workspace, run a program, write an archive or call Zenodo or exactory, and they refuse before they read anything.
- A preparation that reads the page count of a `result` locator into a PDF output with `pdfinfo`.
- A preparation that commits in two steps: `artifact` on a probe path.

Since 0.73.0 a check permits two local reads, the reviewer CLI version and PDF page rendering, so a preparation that needs them is checked instead of refused: `review-route` with a local adapter (`claude_cli_v1`, `codex_cli_v1`, `codex_cli_framed_v1` or `codex_cli_visual_v1`); in a workspace that holds a route of a local adapter, every command whose validation evaluates a route or an assignment state, for example `review-assignment`, `value-review`, `review-adjudication` and `problem-openness` (that evaluation reads the installed runtime of each local adapter in use, and reading a Codex runtime runs the Codex CLI); and `review-assignment` on a `codex_cli_visual_v1` route. The check still writes nothing.

`check_unsupported` says that check mode cannot prepare the operation. It says nothing about the payload; run the write for that answer. A test derives the commands of `exactory-research`, `exactory-math`, `exactory-lab` and `exactory-draft` from their parsers and fails when a writing command lacks `--check`. It runs `--check` for every writing command and compares the whole workspace tree before and after. No check writes.

The store writes only JSON that every supported Python reads back. The payload, each record that a mutation writes, and its result must hold no integer of more than 4300 digits and must nest at most 100 levels. A mutation outside these bounds fails with `invalid_input` and does not change the revision. The CLI parses the payload file before the store checks it, and a file that the interpreter cannot parse fails with `invalid_json`. For example, an interpreter that limits integer string conversion (`sys.get_int_max_str_digits()`, 4300 digits by default) cannot parse a longer integer. Reads and retries of committed requests do not check these bounds, so a store that an earlier release wrote stays as readable as before.

Later records and reviewer exports hold copies of the values of a command input deeper than the input holds them. So `exactory-research` accepts a payload file only when it nests at most 32 levels, and `bind-verdict` accepts a verdict body of at most 32 levels. A `review-assignment` of a paper review (role `manuscript` or `standalone`) repeats the stored bundle of `manuscript` in `context.bundle`, and the assignment requires that bundle to equal the stored record. So the bound counts the rest of that payload and not the bundle. A reviewer export reads JSON of at most 40 levels, and the deepest copy found in one is 9 levels deeper than the input. There, the packet of a review assignment with role `result` in a source-limited study holds the readiness export one level deeper, with each full reading, so a `read` payload of 32 levels reaches 41 levels. Such a payload nests deeply only inside the locators of its evidence links. The export does not add the levels inside a locator to the depth of the reading. The deepest copy found whose levels the export counts is 8 levels deeper, where the same packet holds the `component` of a `fulltext` payload. So an input of 32 levels reaches the 40 levels of an export. The deepest record copy found is 12 levels deeper, where a paper review assignment holds the bundle of `manuscript`, which holds the same component 10 levels deeper than the payload. So an input of 32 levels reaches 44 levels in a record. A deeper input fails with `invalid_input` and changes nothing, unless it retries a committed request. A run's metric has had the same bound since 0.48.0, and a line or file that nests deeper gives no metric (see "Actual execution"). A later payload can still cite an accepted value at a deeper position, and then it fails if it nests more than 32 levels. For example, `review` cites every evidence reference of the candidate one level deeper than `result.evidence` of an assessment holds it, and `manuscript` cites the evidence of its claims at the same depth. A `value-review` holds such a support assessment in its `support` field, one level deeper again. So an assessment whose deepest value in `result.evidence` is at level 31 leaves no room for its `value-review`, and one at level 32 leaves no room for its `review`. Since 0.65.0 the bundle of `manuscript` holds its review inputs and execution observations as artifacts (see "Review, publication, and verification"); the pin checks both values against the store bounds before it writes them, as if the bundle held them inline.

An earlier release did not check these bounds, so a store that it wrote can hold a value outside them. It can also hold a value that fits its record but not a later copy. A new payload, record or result that copies such a value is outside the bounds too, and it fails with `invalid_input`. For example, the bundle of `manuscript` holds the current assessment of every cycle 5 levels deeper than `assess` held it. So a current assessment whose payload an earlier release recorded with more than 95 levels makes `manuscript` fail. [Actual execution](#actual-execution) gives the cases for the seed and the metric of a run.

Some commands write to the store in more than one step. If the store refuses a later step, the earlier steps stay, so the revision changes although the command fails. For example, `reconcile-run` and `exactory-lab run` record the outcome of a run before its observation.

The recurring types are:

| Type | Exact shape and interpretation |
| --- | --- |
| `ArtifactRef` | `{ "sha256": string, "path": string, "size": integer, "media_type": string }`. The path is `research/sources/objects/SHA256`. The actual bytes must match size and SHA-256. |
| `Link` | `{ "version_id": string, "source_id": string, "artifact": ArtifactRef, "locator": Locator }`. It binds a captured exact version, source, and inspected location. |
| Span locator | `{ "kind": "span", "start": integer, "end": integer, "sha256": string, "excerpt"?: string }`. Offsets are Unicode code points into the saved UTF-8 text; `sha256` is the SHA-256 of the UTF-8 encoding of that exact substring; `excerpt` is at most 200 characters of its start, for display only. This is the recommended form: the harness already holds the bytes. |
| Text locator | `{ "kind": "text", "start": integer, "end": integer, "quote": string }`, the legacy form carrying the substring itself. It stays valid; a text and a span locator over the same bytes have the same content identity. |
| JSON locator | `{ "kind": "json", "pointer": string, "value": any JSON value }`, naming an exact value in the saved JSON. |
| Objective | `{ "kind": "objective", "id": string, "statement": string, "title"?: string, "category"?: string }`, preserving the complete original research objective. `title` is the working title of the paper and `category` its arXiv category. Each is optional nonblank text (`invalid_target` otherwise). Entering `write` records the draft workspace from them when no draft record and no `.exactory/draft.json` entry exist (see "Milestone transitions and preparation presets"). Give them when the objective is committed: added to a committed objective, they are a change of it, which `target` refuses with `objective_locked`. |
| Verification target | `{ "kind": "work", "id": string, "source_id": string or null, "sha256": string or null }`. The two nullable fields are both null during incomplete preparation or both identify the available original main body. |
| Result evidence | `{ "kind": "result", "execution_id": string, "output_id": string, "artifact": ArtifactRef, "locator": Locator }`. The artifact must equal the output descriptor that the execution recorded (`result_evidence_mismatch` otherwise), and the stored bytes must match its hash and size. `exactory-lab run` and `reconcile-run` print these values for a managed run: `execution_id` is their `execution_id`, and `output_id` and `artifact` are the `id` and the `artifact` of one entry of their `outputs` (see "Actual execution"). The locator is a span, text or JSON locator into a text or JSON output, an artifact locator, or a result PDF locator. |
| Artifact locator | `{ "kind": "artifact" }`, with no other field, in result evidence only. It cites a whole output of any media type, for example a PNG, SVG or PDF file. It cannot cite a source. A reviewer packet delivers the cited output under the media type that `bind-run` declared for it: as text when its bytes are UTF-8; as a native file or image input on the `openai_responses_v1` route when it is a PDF or an image; and otherwise, on that route, as an evidence entry with `delivery: "inline-result-bytes"`, the exact bytes in base64 inside the JSON packet. The harness checks that the request carried those exact bytes; the entry is no native attachment, and what the reviewer reports about such bytes rests on the reviewer's own decoding. The text-only routes (`claude_cli_v1`, `codex_cli_v1` and `codex_cli_framed_v1`) omit the bytes of a PDF, an image and such an output, with `encoding: "omitted"`, `delivery: "bytes-omitted-on-text-route"` and a note that says so. An artifact that is not UTF-8 text, a PDF, an image or a recorded result output is refused with `review_evidence_unreadable`. |
| Result PDF locator | `{ "kind": "pdf", "page_index": integer, "printed_page": string or null, "region": [x, y, width, height] }` in result evidence. The output must be `application/pdf` and start with the PDF signature. The page count comes from `pdfinfo` on the output bytes, with a 30-second deadline and a 64 KiB output limit, and `pdfinfo` must print exactly one positive `Pages:` line. A missing `pdfinfo`, a failed run or another page count is `invalid_locator`. `page_index` counts from zero and lies inside the output; the region is normalized to the page, as for a source PDF locator. A source PDF locator still needs the acquired original and its page map. |
| Source evidence | `{ "kind": "source", "link": Link }`. Consequential synthesis, development and round evidence requires the applicable full reading. |
| Assessor | `{ "id": string, "kind": "human" or "agent", "provenance": ArtifactRef, "relationship": string, "independence_basis": string }`. Identity declarations and provenance are retained, not independently authenticated. Optional `author_memory_notes_present`, a boolean, discloses whether the author's memory notes were present in the assessor's context. Only the JSON values `true` and `false` are accepted: another type, `null` included, is refused with `invalid_input`, or with `invalid_development` on `review`. Omit the field when no disclosure is recorded; an omitted field is not `false`. The harness retains either value and reads it in no gate; a record without the field has unknown exposure. The field applies to `review`, `manuscript-review`, `manuscript-prediction`, `round-review` and `scoped-review`, for a human or an agent assessor; `review` accepts it since 0.67.0. |

Use `artifact` to pin authored program files, original review JSON, assessor provenance, or other local bytes and obtain an `ArtifactRef`. It accepts `{id, path, media_type}` and explicitly returns `scientific_validation: false`. It does not import a publication source or turn a local note into a reading. Acquire publication sources through `acquire`, `fulltext`, or `import-response`.

## Milestone transitions and preparation presets

Initialize an author workspace with `exactory-lab init --slug STUDY` and an
optional `--preparation-policy`, or initialize the common store with
`init` and `{"profile": "research", "target": null}`. Author initialization
defaults to `lineage-v1`; verification defaults to `sampled-v1`.
`exhaustive-v1` and `screened-v1` remain available to both profiles.
A configuration predating this field retains `exhaustive-v1`. Each bounded
preset belongs to its profile and returns `policy_inapplicable` on the other.
The [catalog](research-tools.md) describes depth and budget arguments. Research
presets supply default amounts; a verification raises only its three
literature amounts, through a plan of its own profile ("Depth plan").

`exactory-lab state set` validates requested fields together. Stages remain
`initiate`, `cohort`, `literature`, `ideate`, `experiment`, `write`,
`evaluate`, `deposit`, `submit` and `complete`. They describe milestone
progress. For managed research:

| Move or completion | Gate |
| --- | --- |
| `initiate` to `literature` | Intake permits this direct move; `cohort` is optional. |
| Leaving `cohort` | No source completion gate. |
| Completing `literature` or entering `ideate` | `direction`: current configuration and cycle decision. |
| Entering `experiment` | `execution`: a valid pending admission, without preparation obligations. |
| Entering `write` | The current result decision and `readiness`, or selected `manuscript-readiness`. |
| Entering `evaluate` | The exact `manuscript` gate. |
| Entering `deposit` | Current `publication` and `round` gates. |
| Entering `submit` or `complete` | The applicable `deposited` or `submitted` receipt gate. |

Entering `write` also records the draft workspace, in the same mutation as the
study state, and so does every later `exactory-lab state set` that succeeds
and keeps the study at `write`. The mutation records the draft only when the
objective carries both `title` and `category`, no draft record exists, and no
entry exists at `.exactory/draft.json`: a regular file, a symbolic link
(dangling included) and a directory each prevent it. The record is `{title,
category, version: 2, created, research: {store: ".exactory/research.sqlite3",
profile: "research"}}`, with the study's `updated` time as `created`. The
export that follows writes `.exactory/draft.json` and creates `draft/`. In
every other case the mutation records no draft, and a refused update records
nothing. A same-stage update whose status stays `done` checks the `manuscript`
gate and the `readiness` gate (or the selected `manuscript-readiness` gate)
first. While no draft record exists, or the marker is not a regular file that
holds the record's JSON, `status` names the state under `draft_marker_missing`
(see "Status, projections, and recovery").

Planning and admitting a cycle require configuration readiness and the current
cycle decision. Tools can supply evidence at any milestone. The complete
preparation report is advisory for managed research.

Forward edges otherwise remain adjacent. Source completion and destination
prerequisites both apply. A proposed `done` status also checks destination
completion. Enter unfinished work with `--status pending`. Operational status
and waiting changes preserve research obligations.

Explicit returns to `literature` remain available from `ideate`,
`experiment`, `write`, `deposit` and `submit`. In managed research,
`evaluate` may return to `literature` when the canonical round decision is
ready with action `investigate` or `pivot`. Other stores retain the active,
unassessed admitted-round condition. `experiment` can return to `ideate`
after assessment, and `evaluate` can return to `experiment` or `write`.
A return retains histories, objective identity and original accounts and enters
unfinished work. Nonmanaged stores and verification keep their existing
preparation and transition gates.

For a fresh verification directory, begin with an acquisition mutation. A real `acquire`, `collect` or `import-response` can create an unconfigured Store when no managed workspace exists; `import-response --preview` needs an existing store and creates none. This does not select the research profile or certify preparation. Existing legacy markers require explicit adoption, and an existing hot Store still requires `recover`.

Save `{ "identifier": "arxiv:2601.00001v1", "max_requests": 4 }` as `metadata-query.json`, then acquire the exact metadata:

```sh
exactory-research acquire --file metadata-query.json --expected-revision 0 --request-id metadata-first
exactory-research status
```

When metadata for that exact version is present, save `{ "profile": "verification", "target": { "kind": "work", "id": "arxiv:2601.00001v1", "source_id": null, "sha256": null } }` as `verification-init.json`. Use the current revision returned by status as `REVISION`:

```sh
exactory-research init --file verification-init.json --expected-revision REVISION --request-id verification-target
```

If acquisition remains pending, retain its request history and complete acquisition before initialization. Initialization continues to reject unknown works. A metadata-only target remains pending for verification until its original main source, exact target pin, required reading, and synthesis are complete. An imported metadata response is explicitly attributed external input; it is not an original-source reading.

## Operation fields

The example for each operation contains all its required keys and shows the complete nested payload. This table lists the top-level fields and supported optional variants; braces here describe objects, and `[]` denotes an array.

File paths in a payload. These fields name a file or a directory of the workspace: `path` of `artifact`, each entry of `files` of `adopt`, `response_file` of `import-response` and of each page of `import-oai-cohort`, each value of `files` of `manuscript`, `export_directory` of `problem-posted` and `instruction` of `gate-override`. The `path` of an artifact reference (`{path, sha256, size, media_type}`) that a payload carries is such a field too, except in the publication bundle that a paper review assignment repeats at `context.bundle`, which stays as stored. Each of these fields accepts a canonical workspace-relative path, or an absolute path that lies inside the workspace. In either form the path has no empty component, no `.` or `..` component and no backslash, so `./notes.txt`, `work//notes.txt` and `work/` are refused. A path through a symbolic link is refused: no component that exists can be a link, the last one included. Every such refusal has the code `unsafe_path` and one message, with `--check` as without it. The harness records the workspace-relative form. An absolute path and its relative form therefore give the same payload, the same record and the same receipt, and a request that is sent again in the other form replays. A file outside the workspace is not accepted: copy it into the workspace first. The other rules of a field stay as its row states them, for example that `instruction` lies under `context/`. Paths of another kind keep their own rules: the script, input and output paths of `bind-run`, which are relative to `experiment/`, and `source_workspace` and `destination_workspace`, which are absolute paths of other workspaces.

| Operation | Required payload fields | Optional fields and variants |
| --- | --- | --- |
| `init` | `profile`, `target` | `preparation_policy`: `lineage-v1` (the `research` default; the lineage and the classics in full, a bounded five-purpose loop with default limit 100 abstracts, and no cohort member reading), `sampled-v1` (the `verification` default; a stratified random sample of at most 100 members, each read with a placement; see `sample`), `exhaustive-v1` (every cohort member and Tier 3 reference owes an abstract reading) or `screened-v1` (a recorded screen selects the preparation set; see `screen-batch`). Profiles are `research` and `verification`; `lineage-v1` and `sampled-v1` each belong to one profile (`policy_inapplicable` on the other). Without this field the profile's default is recorded. Acquire exact metadata before initializing a verification target. A configuration recorded before this field existed is `exhaustive-v1`. |
| `policy` | `previous`, `policy`, `reason` | Changes the preparation policy; `previous` must name the recorded policy (`policy_conflict` otherwise). Synthesis reports retain their configuration binding. Managed claim currency follows the claim projection below; a policy change alone leaves a scientific claim current. Other stores retain their existing dependency checks. |
| `adopt` | `id`, `profile`, `target`, `files: string[]`, `reason` | Archive selected legacy bytes; no implicit completed readings or new execution credit. |
| `target` | `target`, `reason` | Optional `authorization`. Verification repinning is explicit and requires matching subsequent roots. A fixed research objective changes only with `authorization`: the `id` of an `artifact` pin whose path is under `context/` and whose bytes hold the user's explicit instruction (`objective_locked` without it or when an earlier change used the same bytes; `invalid_target` for an unknown pin, a pin outside `context/`, an empty file, or `authorization` on a first or unchanged target). The harness checks the pin and its bytes, not who wrote them: save only the user's own words, never an instruction you wrote. The new objective needs a new `id` and a statement that differs after casefolding and whitespace collapsing (`objective_locked` otherwise). The direction of the change is not checked; the reason states it. The earlier objective stays in `research_objective`, and `objective_lineage/{new id}` records `{id, predecessor, relation: "user_authorized", reason, authorization: {id, path, artifact, text}, round_id: null}`, so earlier checkpoints stay inheritable. The readiness candidate and the round review packet carry these changes as `objective_changes`. The changed objective reaches dependent claims through their objective and scope. Reassess affected evidence (`inherited_result_stale` for a stale successor) and use `inheritance_refresh` when replacing an inherited assessment. Synthesis reports retain their own configuration dependencies; unrelated global preparation changes alone leave managed claims current. A math-solver search bound to the workspace refuses the changed objective (`research_objective_mismatch`), as after a widening. |
| `constitution` | `previous_sha256`, `reason` | Adopts the current distributed policy. Research adoption of Version 7 alone leaves claims current. An adopted Version 6 can remain in use without revalidation; verification retains Version 6 in this release. |
| `artifact` | `id`, `path`, `media_type` | `path` follows the rule for file paths in a payload (above). |
| `collect` | `definition: {corpus, primaryCategory, windowStart, windowEnd}` | `max_requests`, `page_size`; current corpus is `arxiv`. Dates are ISO calendar dates. |
| `resume` | `collection_id` | `max_requests`, `recheck`; resumes retained collection/page evidence. A partition at the minimum width (one minute) whose reported total still reaches the provider's query ceiling keeps a permanent `query_ceiling` diagnosis in `pending`, and an ordinary `resume` leaves it as it is; `recheck: true` asks the provider for that partition again, from its first page. |
| `acquire`, `expand` | `identifier` | `provider`, `max_requests`; expansion acquires an unresolved identifier without asserting its bibliography has been read. An old arXiv archive name without its hyphen, as in `arxiv:astroph/0410063`, is read as the hyphenated archive (`arxiv:astro-ph/0410063`). |
| `fulltext` | `identifier`, `url` | Optional `component` declares a pinned supplement relationship (see below). The response is read when its reported media type, without parameters such as `charset`, is PDF (`application/pdf`), HTML (`text/html`, `application/xhtml+xml`) or XML (`application/xml`, `text/xml`). A body served as `application/octet-stream` or `binary/octet-stream` is read as a PDF when its bytes start with the PDF signature. Without that signature it is `unsupported_fulltext`. A response of any other type is recorded `pending` with `unexpected_mime`. XML is read as a JATS article when its root element is `article`. Element names are matched by their local name, so a namespaced document reads the same. The Europe PMC endpoint `https://www.ebi.ac.uk/europepmc/webservices/rest/<PMCID>/fullTextXML` (for example `PMC13104172`) serves such a document for an open-access article. The extractor `jats` saves the text of the whole article (front matter, body and back matter) with a line break at each block element. `includes_abstract` is true when the abstract has text. The capture field `visual_asset_names` lists the `graphic` references of each `fig`, in document order and without repeats. These are names only: no image is fetched, and `visual` acquires images from an HTML original only. The extraction status depends on body content: text in a paragraph, list item, table cell or similar element inside `body`, outside headings, abstracts and metadata. An `article` without such text is `landing_page`, and an XML document whose root is not `article` is `unsupported_fulltext`. The parser loads no external DTD or entity and expands no declared entity. A document that declares an internal entity is `xml_internal_entity`. A document that refers to an external entity is `xml_external_entity`. So is a document that refers to a named entity that only an external DTD defines (for example `&nbsp;` from the JATS DTD). Invalid XML is `malformed_xml`. Predefined entities and numeric character references read normally. The extractor is bounded: at most 200,000 elements (`xml_element_limit`), a nesting depth of at most 256 (`xml_depth_limit`), at most 33,554,432 characters of decoded markup and text (`xml_text_limit`, a bound on characters, not on bytes) and ten seconds for the whole extraction (`xml_timeout`). Each of these XML statuses leaves the capture `pending` with that code as `extraction_status` and keeps the original bytes. A pending capture has no text to read. Acquire another supported original of the work with a new `fulltext` request, for example the publisher's PDF or HTML page, and read that capture. `max_requests` bounds the HTTP attempts of the capture, and each redirect and each retry is one attempt, so set it for the whole chain of a link that redirects: a publisher PDF link that was redirected three times before it served the file needed four attempts. When the budget runs out, the capture is recorded as `pending` with `extraction_status` `request_budget` and stays in the record; a new `fulltext` request with its own request id and a larger `max_requests` tries again. For a PDF, the harness chooses the extraction mode itself when the request gives no `layout` option and no OCR. It first extracts the text in the extractor's layout mode, which pads the text with spaces to keep the physical layout of each page, and measures that text. When the whitespace fraction is greater than 0.90, or the expansion ratio (the UTF-8 bytes of the extracted text divided by the bytes of the PDF) is greater than 2.0, the harness extracts the PDF again without layout mode and keeps that plain text. Neither mode truncates the text. The thresholds come from measurements with `pdftotext` 26.02.0: among 30 ordinary article PDFs the largest whitespace fraction was 0.644 and the largest expansion ratio 0.487, and among three PDFs whose layout text was padded the smallest were 0.970 and 2.61. The capture records the choice in `extraction.layout_selection`: `protocol` (`exactory-pdf-layout-selection-v1`), `kept_mode` (`layout` or `plain`), `thresholds` (`whitespace_fraction` and `expansion_ratio`) and `measurements.layout`, which holds the text bytes, page count, longest line, whitespace fraction and expansion ratio of the layout text. After a second extraction it also records `plain_status`, the status of the plain extraction, and, when that extraction gave a text, `measurements.plain`. When the plain extraction fails, `kept_mode` is null, the capture is `pending` with that status as `extraction_status`, and the padded text is not kept. The other fields of `extraction` describe the text that was kept. The harness tries the plain mode only after a layout extraction that succeeded: when the layout extraction itself fails, the capture is `pending` with that status and records no `layout_selection`. An explicit `extraction_options: {"layout": true}` or `{"layout": false}` fixes the mode: the harness extracts once in that mode, keeps the result whatever its measures, and records no `layout_selection`. For scanned PDFs, `extraction_options: {"ocr": true}` uses bounded English OCR with pdfinfo, pdftoppm and Tesseract, retaining every PDF page boundary and actual tool versions. OCR has a 600-second deadline, 200-page limit, 5000-pixel maximum page dimension, and 32 MiB limits for each rendered page and total text. OCR and layout options are mutually exclusive, and an OCR capture records no `layout_selection`. Interpret OCR text against the original page images; the harness records the tools and does not check the text. An original up to 128 MiB is captured; a larger response fails with `response_too_large`. Preserves the exact original and extraction result, and records `extraction` (extractor, version, options, text bytes, page count, longest line, whitespace fraction, expansion ratio) on the capture. Every request with a new request id adds a capture and changes no earlier capture. Captures with the same original bytes are captures of one original, whatever their options and their texts, and a reading on any capture of that original satisfies its full-text coverage. `select-main-capture` (below) chooses which capture of an original is its main capture. A PDF original larger than 128 MiB, or a PDF transfer or extraction that one `fulltext` call cannot finish within its limits, is acquired as a full-text job (`fulltext-start`, below); a job extracts PDF originals only, so an HTML or XML original stays with this command and its limits. |
| `fulltext-start` | `identifier`, and exactly one of `url` or `source_id` | `extraction_options`, `allowances`, `use_ranges`. Records a durable full-text job for the exact version and returns `{job_id, status: "pending", pending: [{code: "fulltext_job_pending"}], revision}`; the transfer and the extraction run in `fulltext-resume`. `url` names the original to transfer. The job binds the transferred original to `identifier` by the rule of `fulltext`: for an arXiv identifier the final URL of the transfer must normalize to the exact requested version, with its version number, so request the versioned arXiv URL; another destination leaves the job pending with `version_mismatch`, which no resume changes. For any other identifier the final URL is not an identity, `observed_identifier` stays null, and the job's source and assembly bind the requested identifier. `source_id` names a retained complete original of the exact version, for example the original of a `fulltext` capture, which the job extracts without a transfer; the source is accepted by its requested identifier, and for an arXiv identifier its observed identifier must be the same exact version. The job extracts a PDF original only (see "Full-text jobs" below). A job does not choose the extraction mode as `fulltext` does: it extracts in layout mode unless `extraction_options` sets `layout` to false, and its capture records no `layout_selection`. `use_ranges` (default true) transfers in verified byte ranges that a later resume continues; false transfers the original whole. `allowances` are the job's limits: `original_bytes` (default 512 MiB), `retained_bytes` (4 GiB of retained transfer and extraction artifacts), `text_bytes` (256 MiB of extracted text), `network_attempts` (100), `invocation_seconds` (120 per resume), `unit_seconds` (30 for each unit of extraction work, such as one page; a transfer uses what remains of `invocation_seconds`), `unit_output_bytes` and `unit_image_bytes` (32 MiB each), `pages_per_resume` (20) and `range_bytes` (8 MiB). See "Full-text jobs" below. |
| `fulltext-resume` | `job_id` | `max_requests`, `max_pages`, `allowances`, `reason`. Runs the job within its allowances: the next transfer units (`max_requests` bounds the network attempts of this invocation) and then the next extraction pages of the PDF original (`max_pages`, 1 to `pages_per_resume`), each recorded durably as it completes. `allowances` raises one or more limits of the job, never lowers one, and then needs `reason`; the expenditure so far is kept. The result is `{job_id, status, pending, completed_pages, page_count, attempts_used, next_unit, revision}`, and `fulltext-status` shows the next action. |
| `import-response` | `provider`, `response_file`, `source_url`, `captured_at` | `media_type`, `mappings`, `publisher_capture`; `web`/`mcp` mappings use JSON pointers into the original saved response for every work. A request already committed is replayed first, from its recorded receipt, before the response is parsed. A new response, or the import payload, above 8 MiB is refused with `import_input_too_large` before it is parsed. The result of a new import carries `identity_conflicts`: each DOI of the batch that the import makes ambiguous, as `{identifier, existing_families, incoming_families, candidates}` with sorted arrays, including a conflict between two works of one batch; an assertion that repeats a recorded family adds none. The import continues, the field is advisory, and the original result of an earlier import replays without it. `--preview` on the command line reports the same for the payload without writing: `{status: "preview", scope: "metadata_import", revision, work_ids, pending, warnings, identity_conflicts}` from the current snapshot, with no record, artifact, receipt or charge. It needs an existing store and takes no `--expected-revision` or `--request-id`. Optional `publisher_capture: {format, identifier}` imports one complete captured HTTP response of a publisher API, in place of `mappings`. It applies to provider `web` or `mcp` with a `media_type` of `message/http`, the captured status line, headers and body, and no `mappings`. The only format is `springer-meta-json-v1`. The parser follows the published format of the Springer Nature Meta API v2. `source_url` is `https://api.springernature.com/meta/v2/json` with its query, without a credential parameter such as `api_key`, and `identifier` is the article's canonical DOI, `doi:10.1007/...` or `doi:10.1038/...`. The URL check that every import runs first refuses, with `unsafe_url`, a URL with a credential query parameter, a user name or password, a fragment or a port other than 443. The capture admits one exact DOI abstract from a complete response with unambiguous JSON boundaries. The response is a `200` response of `HTTP/1.0` or `HTTP/1.1` whose `Content-Length` equals the length of its body. It has no `Transfer-Encoding` and no `Content-Range`, its content encoding is identity, and its `Content-Type` is `application/json`. The body is UTF-8 JSON, one object with no repeated key, that holds the API message and exactly one record with counts of `1`. The record's `doi`, and its `identifier` when present, normalize to the requested DOI, and its `title` and `abstract` are nonblank. The work's abstract is recorded `available` and `complete` with the representation `springer-meta-json-v1`. The source carries `publisher_capture`, `content_scope: "publisher_metadata"`, `observed_identifier`, `capture_method: "external_import"` and `origin_verified: false`. The import creates no full-text record and no reading. Everything else is refused with `invalid_publisher_capture`, and nothing is written. That includes a capture object with other fields, another format, another media type, and a media type with whitespace around it or a malformed parameter. It also includes a native provider, `mappings` beside the capture, another source URL that passes the URL check, and an identifier that is not such a DOI. The same code refuses a partial, chunked, compressed or ranged response and a repeated key. It also refuses more than one record, another DOI, and an empty title or abstract. The command's other refusals apply unchanged. A response that is not bytes, or a `captured_at` without a timezone, is `invalid_import`. An oversized input is `import_input_too_large`, and a replay whose capture differs is `request_id_conflict`. `--preview` takes the field and adds `source_candidates`, one `{version_id, candidates, ambiguities}` per work. The candidates are the related versions of the work's alias family with their abstracts, as a bar source delivery reports them under `abstract_candidates`. An import recorded without the field keeps its payload bytes and replays unchanged, and a mapped abstract stays a partial `mapped_excerpt`. |
| `import-oai-cohort` | `collection_id`, `pages: [{response_file, source_url, captured_at}]` | Existing arXiv math-ph collection only. Each file is an original XML response, at a path under the rule for file paths in a payload (above). Supply the complete ordered arXivRaw whole-set token chain; no authored works, totals, flags or HTTP receipts. |
| `roots` | `profile`, `roots: string[]`, `collection_ids: string[]` | `target` is required for verification; optional `historical_cutoff`. Research objective identity is set by `target`, not by this scope. |
| `bundle` | `id`, `version_id`, `source_id`, `scope`, `completeness`, `units`, `inventory`, `bibliography`, `resolutions` | Each unit has `id`, `kind`, `required`, `link`, and optional `reason`/`url`; inventory and bibliography must reflect the original article. The citation graph reads every registry occurrence and, for each acquired original of the version that has a bundle, the bibliography occurrences of that original's latest bundle, unless the version's last `bundle` import named an earlier bundle of that original. An acquired original is an available main original of the version, so an original that a bundle of another of the version's bodies links as a `supplement` unit is another body. Only a version none of whose acquired originals has a bundle reads, by the same rule, the bundles of its other bodies, such as the saved response of a passage or such a supplement. A bundle of another body, such as a passage on a saved tool response or a bundle whose body is such a supplement, is therefore not read once a bundle of an acquired original exists, whichever was imported first, while a passage bundle whose body is an acquired original is read as that original's bundle. A new bundle for the same body replaces the occurrences of the earlier one, so their obligations end with it. A verification target reads the bundle of each acquired original whatever its pin. `resolutions` account for earlier occurrences that the graph still reads, such as registry observations. An entry or a resolution of kind `paper` can add `target_family`: the canonical id of an acquired family that an assertion of its `target` names, chosen with the entry's own `link` and `reason`. A DOI target can choose its own family or an asserted arXiv family; an exact arXiv target of the chosen family keeps its version. An unknown, unrelated, versioned, noncanonical or malformed choice is `invalid_family_selection`, and nothing is written. The choice binds that occurrence or resolution only: earlier bundles and reading units keep their digests, and an occurrence without a choice stays `reference_identity_ambiguous` while its target has conflicting assertions. A resolution with a choice resolves an occurrence of an earlier bundle. |
| `read` | `id`, `version_id`, `depth`, `inspections`, `notes` | Fulltext reading also supplies `bundle_id`; each inspection has `unit_id`, `link`, `note`. All seven note fields shown in the example are required. |
| `budget` | `profile`, `purpose`, `limits`, `reason` | Purposes are `literature`, `screening`, `experiment`, `development`. `limits` names every unit (`network_requests`, `source_bytes`, `readings`, `screenings`, `model_input_tokens`, `model_output_tokens`, `wall_seconds`, `rounds`); null means unlimited. A `development` budget bounds the number of rounds with the `rounds` unit; `round-admit` charges one round. Any unit of a `development` budget at its limit refuses the next `continue` with `resource_budget_exhausted`, so leave its other units null. A later budget for the same key needs a reason and cannot drop a limit below the charged amount (`resource_budget_below_charged`). Acquisition admission needs room for `max_requests` (at least one request) beside the reservations of every acquisition that is still admitted, and finish charges the attempts and bytes actually used; the reserved amount is the allowance of the admitted operations, so an interrupted acquisition holds its allowance until a new request id for the same operation and target supersedes it. `read-batch` and `screen-batch` charge their counts and reported usage, counting null usage as unknown. New work over a limit is refused with `resource_budget_exhausted`, and usage already spent is recorded. Three kinds of charge fail instead and change nothing. A charge that makes an account total an integer of more than 4300 digits fails with `invalid_input`, because the store refuses that integer. A float `wall_seconds` charge to an integer total beyond the float range (about 1.8e308) fails with `invalid_input`. A `wall_seconds` charge whose sum with the total is a float beyond the float range fails with `invalid_input`. With a `wall_seconds` limit, it fails with `resource_budget_exhausted` instead. A JSON number with a decimal point or an exponent is a float. Charged plus reserved at or above a limit is a preparation obligation, except for `development`, whose exhausted limit is a `gate round` obligation; `status --summary` shows every account. |
| `read-batch` | `id`, `depth`, `items` | Optional `usage`, `depth_values`. Use `depth: "abstract"` with `depth_values` for per-call amounts; the object form of `depth` remains supported. Records 1 to 100 abstract readings in one event, all or none. Each item is `{version_id, note, notes: {seven fields: {text, status}}}` with optional `screening: {relevance, reason, conventions}`, `audit: {relevance, reason}`, `consequential: bool`, `placement: {position, reason}` where position is `above`, `below` or `unplaced`, `loop: {purposes, disposition, source}` where source is `search`, `population`, `citing` or `author`, `innovation_candidate: true`, and `search_hit: true`; the harness derives the whole-abstract span inspection itself. The recorded policy decides which extras apply: `loop` and `innovation_candidate` under `lineage-v1`, and `placement` and `search_hit` under `sampled-v1`. A research call with explicit depth values or a selected depth plan can also record sample placements and search-hit readings; other incompatible extras fail with `invalid_batch`. The catalog example carries `loop` on its first item and `placement` on its second so that both shapes are visible; use the applicable policy or explicit research-tool depth, and the `README.json` of a `batches` export states the shape for that export's mode. A `placement` judges a member of the current sample (`invalid_batch` otherwise). A batch that would exceed its effective study loop limit (`loop_limit_reached`, default 100), effective development-round loop limit (`round_loop_limit_reached`, default 40), or search-hit limit (`search_reading_limit_reached`, the effective `search_readings` amount, default 20 and never below it for verification) is rejected whole; the 20 abstracts of a further loop round are a rule the coordinator follows, not one the store refuses. Reading ids are `reading:<sha256 of [batch id, version]>`. The reading covers the version's selected complete abstract; a version without one fails with `abstract_missing`. An `audit` belongs to a currently excluded member (`invalid_batch` otherwise) and is stored with the member's screening `round` at the time of reading. A failing item rejects the batch with `invalid_batch` and the failing `items` (`index`, `code`, `message`). `usage` is `{model, input_tokens, output_tokens, wall_seconds}`, each null when unknown. The token counts are nonnegative integers. `wall_seconds` is a nonnegative number with a finite float value, so an integer beyond the float range (about 1.8e308) fails with `invalid_batch`. |
| `screen-batch` | `id`, `items`, `screener` | `round` (default 1), `usage` with the shape and rules of `read-batch`. Under `screened-v1` only (`policy_inapplicable` otherwise). Records 1 to 200 screenings, all or none; a failing item rejects the batch with `invalid_screening` and the failing `items`. A re-screen needs a round above the member's current round and replaces the member's screening. Each item is `{collection_id (null for a Tier 3 family), work_id, version_id, disposition, relevance, reason, conventions}` with `promotion_reasons` for `promote` and `context` for a reference. Rules: `strong` relevance requires `promote`; `exclude` requires `none` relevance and a complete abstract; `weak` stays `pending`, `doctrine`, or `promote`. `screener` is `{kind: agent|human, model}`. A screen is never a reading. |
| `screening-checkpoint` | `id`, `batch_ids`, `reason` | Under `screened-v1` only. Accepted when the two named batches are the two most recent `read-batch` records, both contain only `pending` members, and every item of both was judged `consequential: false`; otherwise `invalid_screening`. While it covers, unread `pending` members carry no obligation and are counted as `inventoried_unread`; a later consequential batch, a higher screening round, or a policy change removes the effect. |
| `sample` | `id`, `collection_id`, `size`, `seed` | Optional `depth`. Available under `sampled-v1`, or in research with an explicit `depth` object or selected depth plan (`policy_inapplicable` otherwise). Draws and records a sample of one frozen population, stratified by month with equal allocation, a short month's shortfall passed to the next months, and the whole population when it fits within `size` (1 to the effective `sample_size`; verification keeps 1 to 100). The enumeration must be complete (`collection_pending`). The record keeps the seed, the per-month population and sampled counts, the population digest and the drawn members; the draw repeats from that seed, and two verifications of one paper choose their own seeds and read different members. A second draw against the unchanged population is refused with `sample_exists`; a population that changed after the draw reports `sample_stale`, which a new draw clears. Under the verification preset every sampled member owes an abstract reading (`sample_reading_missing`) carrying a placement (`placement_missing`), and a scoped collection that is not the sampled one reports `sample_missing`. Research records the draw as tool evidence, with readings at its stated depth. |
| `loop-close` | `id`, `purposes` | Optional `depth`. Under `lineage-v1` only (`policy_inapplicable` otherwise). Closes the bounded loop. `purposes` names all five search purposes exactly once, each `{status, note}` with status `covered` or `gap`. A `covered` purpose needs a loop reading judged `relevant` or `contradictory`, or two distinct captured queries for it with no such hit; a `gap` needs two distinct queries tried, or the loop at the call's effective `loop_abstracts` limit (default 100). Each captured query counts once. A native capture counts by the query it asks: its provider, endpoint and the parameters that decide which works match, as the registries' API references define them (`search_query` and `id_list` for arXiv; `query`, every field query `query.<field>` and `filter` for Crossref; `search`, `search.exact`, `search.semantic`, `filter` and `corpus` for OpenAlex). Its pages, its repeated captures and captures that differ only in another parameter (an order such as `sort`, `order`, `sortBy` or `sortOrder`, `select`, `sample`, `seed`, `facet`, `group_by`, paging, `mailto`, `api_key`, or any parameter outside these lists) therefore count once, whichever of its query parameters a judgment binds (for example OpenAlex `search` and `filter`). So do captures whose URLs spell one query differently: the endpoint is compared by its host and its path without a trailing slash, an empty parameter or list item counts as absent, and the comma-separated items of a `filter` or an arXiv `id_list` are compared as a set, because the registries combine them in any order. An OpenAlex `filter` is compared as OpenAlex reads it: the letter case of its values does not count, and the `\|` alternatives of one filter, after a leading `!` that negates them all, are compared as a set, so `type:Article` asks the same query as `type:article`, and `cites:W2\|W1` as `cites:W1\|W2`. Two native captures whose matching parameters differ in any other way count apart, even when they share a query value; this includes the name of a filter in another letter case, another letter case of the value of a `.search` filter such as `title.search` (OpenAlex reads its Boolean operators only in uppercase), and another letter case of the value of any other matching parameter, such as `search`. A `web` or `mcp` capture counts by its saved response and its bound query: the queries bound to one response count once, and so do the responses bound to one query. Such a capture adds no query when a native capture of the purpose was bound to the same value, because the harness cannot tell it from another capture of that query. The record keeps the queries each purpose tried and a digest of the loop it closed: a later loop reading or search reports `loop_closure_stale`, and no closure at all reports `loop_closure_missing`. |
| `search` | `id`, `profile`, `purpose`, `queries`, `responses`, `captured_at`, `found_work_ids`, `verdict`, `cited_work_ids`, `impact`, `gaps`, `dispositions` | Optional `resolved`, `scope`, `question`, `binding`, `depth`, `hits_per_query`. Without `scope`, the question supplies its text. A provisional search before roots requires `question`; see the search coverage section. Bind the original query and every result. A response's `query` is the value of a query parameter of its captured URL (`search_query` for arXiv; `query`, `query.bibliographic` or `query.author` for Crossref; `search` or `filter` for OpenAlex, so a citing-works capture binds its whole filter value, such as `cites:W123` or, when the filter also holds a date clause, `cites:W123,from_publication_date:2025-01-01`; `q`, `query`, `search` or `search_query` for `web` and `mcp`), or, for a `web` or `mcp` capture, a saved response value named by `query_locator`. `nothing-new` needs an actual captured search, including an actual empty result array when appropriate. `dispositions` judges every found work once: `{work_id, disposition, reason}` with `relevant`, `contradictory`, `potentially_relevant`, `out_of_scope`, `duplicate`, or `unresolved`; cited works are relevant or contradictory. Each cited work becomes a critical full-text requirement (purpose `novelty`), so it is owed a full reading, while its judgment is the selected search of the profile and purpose; a new search for the purpose retires the requirements of the works it does not cite again (the stored requirement records stay). A new search for a purpose carries forward each `contradictory` or `unresolved` work of the selected search or lists it under `resolved: [{work_id, reason}]`, else `search_findings_dropped`. A judgment is stale when its scope changes (`search_scope_stale`), when a root, required full text, found or cited work changes content (`search_evidence_stale`), or when a family enters or moves in the citation graph (`search_frontier_stale`); a new version of an unrelated reference changes nothing. When a new search for a purpose drops a family that only the judgment it replaced required, each selected judgment of another purpose that was recorded while the family was required reports `search_frontier_stale` and `search_evidence_stale` until it is recorded again: as notices under `lineage-v1` and `sampled-v1`, and as obligations under `exhaustive-v1` and `screened-v1`. The one exception is a judgment that found a version of the family: it reports only `search_frontier_stale` when the citation graph had read no reference of the family's versions when the judgment was recorded, for example because no root or other required full text cited the family then. A reference of the family that the graph read later, while the family was required, does not change this. A judgment recorded before the family was required is stale while the family is required, and the drop ends that staleness. Searches recorded before dispositions existed report `search_dispositions_missing`. Under `lineage-v1` and `sampled-v1`, captured hits are bounded by the resolved `hits_per_query`, counted across every response of that query (`invalid_search` above that). Research may use bounded coverage. Verification resolves `hits_per_query` from a plan or override of its own profile, default 10 and never below it, and keeps its native search enumeration obligation; see the search coverage section for both profiles. |
| `require-fulltext` | `id`, `profile`, `version_id`, `purpose`, `reason` | Optional `historical_cutoff`, `depends_on`, and `depth` for purpose `core`. Purposes: `major_claim`, `novelty`, `innovation`, `validity`, `exemplar`, `lineage` (the parent's own line of results), `classic` (a foundational paper that line rests on), `core` (a paper whose full text decides a verdict's stance or its prediction band) and `contradiction` (a source that opposes a claim). `lineage`, `classic` and `core` name the claim, lineage entry or finding they serve in `depends_on`. A `core` requirement beyond the effective `core_fulltexts` amount is refused with `core_limit_reached`; verification resolves the amount from a plan or override of its own profile, default 10 and never below it. Every `lineage` and nondeferred `classic` entry is cited in the pinned bibliography (see "Review, publication, and verification"). An `exemplar` requirement under the `research` profile, recorded while a round is active, is that round's exemplar (see "Development rounds"). |
| `availability` | `id`, `profile`, `version_id`, `depth`, `source_ids`, `reason`, `policy` | Policy has `id`, `minimum_attempts`, `allowed_statuses`, `rationale`. Two policies qualify: captured terminal HTTP 403/404/410/451 failures, or (abstract depth, non-arXiv work) `allowed_statuses` `[200]` with complete parsed captures bound to the work from every registry that addresses its identifiers (Crossref and OpenAlex for a DOI, OpenAlex for an OpenAlex id, the saved web/MCP import for a `url:` work), none carrying an abstract. A review source request for the exact version and depth can be settled as `unavailable_qualified` only after current bindings, registry coverage and response bytes are revalidated and the evidence is delivered. Review registry absence requires at least one addressing registry; a web/MCP import alone is insufficient. A current complete source takes precedence, and a terminal failure cannot excuse pending units of held full text. Qualified answers grant no reading or scientific-support credit. Critical sources remain required. |
| `defer-source` | `id`, `profile`, `version_id`, `reason`, `authorization`, `acquisition_evidence`, `dependent_claims`, `continuing_work` | Research only. Names one registered exact version with a current missing full-text requirement. `authorization` is a checked, nonempty artifact reference; `acquisition_evidence` is a nonempty array of checked, nonempty artifact references. The last two fields are nonempty string arrays recording claims that cannot use the source and work that can continue. Roots and verification targets cannot be deferred. |
| `resume-source` | `id`, `profile`, `version_id`, `deferral_id`, `reason` | Restores ordinary obligations for the selected active or stale research deferral. Names its exact `deferral_id`; all earlier decisions and receipts remain immutable. |
| `select-cohort-abstract` | `collection_id`, `work_id`, `unresolved_assertion_id`, `selected_assertion_id`, `reason` | Resolves a retained versionless family member to same-family exact abstract evidence. It cannot replace a known v1 obligation with v2. |
| `select-main-capture` | `version_id`, `source_id`, `reason` | Chooses which capture of one original of an exact version is its main capture. Without a choice, the main capture of an original is its first available capture that is neither a component nor linked as a supplement by a source bundle of the version. `version_id` names a known exact version (`unknown_work` otherwise, and `missing_version` for a recorded arXiv work without a version number). `source_id` names a full-text capture of that version that is available, has an extracted text, is not a component and is not linked as a supplement. Any other capture, a pending capture included, and a source id that names no capture of the version are refused with `invalid_main_capture`. The original and the text of the capture must be readable (`artifact_missing` or `artifact_corrupt` otherwise). `reason` is nonempty text, and the payload has no other field (`invalid_input`). The command records `main_capture_selection` under the version and the SHA-256 of the capture's original: the payload with `original_sha256`, `text_sha256` and `protocol` (`exactory-main-capture-selection-v1`), which the receipt returns. Each original has one current choice, and a later choice for the same original replaces it. A choice covers the captures with the same original bytes. A capture of other bytes belongs to another original, which keeps its own main capture, so compare `original_sha256` of the receipt with the original of the capture that the choice is to replace. The command changes no capture, no artifact and no reading, and a reading on any capture of the original still satisfies its full-text coverage. From the next packet on, a review chain delivers the text of the chosen capture and no longer the texts of the other captures of that original (the main capture in [Scientific contribution decisions](#scientific-contribution-decisions)). An openness reading chain is not bound to the choices of its packet. It reads the current main capture of each version that a bound full reading read, and its packet carries the text that the reading inspected. When the packet does not carry the text of the main capture, the chain has no full-text unit for that version: a `problem-openness` export whose `minimum.text_priority` lists the version is refused with `openness_reading_gap`, and a recorded openness finding that read the earlier text is stale (`problem_openness_stale` with the cause `openness_reading_incomplete`) until the earlier capture is chosen again. Before an openness export, record a new `bundle` and a full reading on the chosen capture, so that the packet carries the chosen text. Use the command when the main capture of a PDF holds a text that layout mode padded. First add a capture of the same original with the plain text. A new `fulltext` request for the PDF adds one when the server returns the same bytes, and `fulltext-start` with the `source_id` of the padded capture and `extraction_options: {"layout": false}` extracts the retained original again. Then choose the new capture. The same request id replays its receipt, also after a later choice. |
| `visual` | `link`, `url` | Optional `max_requests`. `link` is the link of a visual unit (a figure, table or equation) into the acquired original HTML, with an `html` locator `{"kind": "html", "anchor": <span or text locator into the saved HTML>}`, plus `assets` once images are acquired. `url` must be an image that the markup of that unit references, such as the `src` of an `img`; a hyperlink inside the unit, such as a link to a figure page or to a supplementary archive, and an image elsewhere on the page are not resources of the unit, and `visual` refuses such a URL with `invalid_visual_asset` before it sends or records anything. A complete result returns `asset_link`: put it in the `assets` array of the unit's locator in a new `bundle` and give the reading's inspection that locator; an inspection without it leaves the unit `required_unit_uninspected`. Every HTML visual unit also carries one `visual_document_style_unsupported` item for each `style` element and each stylesheet link of the whole page, because the harness has no CSS cascade evaluator. A full reading of a page that has one stays `partial` after the image is acquired, linked and inspected. A figure unit of a PDF capture of the same version, with a `pdf` locator into the acquired original and its page map, carries no such item, so its reading can complete. |
| `standards` | `id`, `profile`, `scope`, `field`, `article_type`, `venue`, `cohort_doctrine`, `methodology`, `reporting`, `citation`, `presentation`, `applicability_questions` | `article_type` and `venue` may be null; evidence claims retain scientific status, timing, assumptions, and uncertainties. |
| `rationale` | `id`, `profile`, `scope`, `and`, `but`, `therefore`, `value` | Research only. The established context in `and` must be supported as scoped; a proposed or refuted context is retained but pending. |
| `innovation` | `id`, `profile`, `scope`, `cases` | Research only. Optional `origin_collection: {id, version}` and `depth`. The depth-plan section gives the current and historical case bounds. Cases separately describe original constraint, conceptual change, validation, adoption, and tested transfer limits. |
| `context` | `id`, `profile`, `scope`, `current`, `historical`, `beneficiaries`, `capabilities`, `barriers`, `uncertainties`, `speculative_links` | Research only. Basic science does not require an invented immediate application. |
| `grand-challenge` | `id`, `reason`, `challenges` | Research only (`configuration_missing` without a research configuration, `profile_inapplicable` in a verification workspace). Records the challenges ahead of the ai-science study as part of the direction minimum: each challenge is `{id, horizon: ultimate|near_term, statement, state, criteria: [{id, statement}], evidence}`, at least one is `ultimate`, and criterion ids are unique across the record (`invalid_grand_challenge`). Evidence is a source read in full or an execution result, validated as in `round` (`invalid_development` for a malformed item, `reading_missing` for a source without a full reading). The managed direction minimum owes `grand_challenge_missing` until the first record exists. A user-specified mathematical objective follows its own gates. The latest record is current for the whole study; a new record with a reason replaces it. It is outside the preparation digest, so recording it never makes a pinned bundle stale. `status` reports the current record. |
| `cycle` | `id`, `author`, `objective`, `scope`, `hypothesis`, `question`, `strategy`, `predecessor`, `inheritance`, `reopening`, `distinguishing_test`, `expected_outcomes`, `failure_signals`, `evidence_requirements`, `literature`, `resource_limits` | The full nested example includes a first cycle. Successors retain checkpoint lineage and account limits. Optional `prior_knowledge`, `{program, program_path, probe_ids, account}`, discloses the runs of the plan's program that probes recorded before the plan (see "Planning a successor"). In a problem record, optional `test_declaration`, `{alternative, excluded_outcome, prior_knowledge, search_id}`, declares the run as a test of the current statement before the execution ([The test declaration of a plan](#the-test-declaration-of-a-plan)). The `prior_knowledge` of the declaration is a field of the declaration, and it is another thing than the `prior_knowledge` of the plan. |
| `admit` | `id`, `cycle_id`, `plan_digest`, `command`, `reserved_units` | Command has `argv`, `program`, `inputs`, `versions`, `seed`, `seed_reason`. This reserves resources; it does not launch. Optional `format_only_of` names the id of a completed, assessed, managed execution of the same plan and declares that the new run changes only the format of its outputs (see "Actual execution"). The program must be the program that the plan's `prior_knowledge` froze (`preplan_program_mismatch`), and a plan without that account cannot admit a program that a disclosed probe ran before the plan was recorded (`preplan_probe_missing`; see "Planning a successor"). |
| `bind-run` | `admission_id`, `script`, `backend`, `timeout_seconds`, `inputs`, `outputs`, `usage_unit` | Backend is `local` or `colab`; usage is planned `execution` or `wall_seconds`. Inputs map each admitted artifact to a relative path. |
| `reconcile-run` | `admission_id` | Optional `resolution`, `reason`: `interrupted` for a confirmed dead local owner, or `not_released` for a Colab claim with no durable release. Unknown released remote work remains pending. The result is the run summary, as before, with `execution_id` and `outputs` of the execution that the store records for the admission after the command, or null for both when it records none (see "Actual execution"). The receipt of the request holds the summary alone. Some runs that an earlier release claimed or admitted fail with `invalid_input` (see "Actual execution"). Their metric or seed exceeds the store bounds in a new record or result. |
| `result` | `id`, `cycle_id`, `origin`, `command`, `status`, `exit_code`, `usage`, `outputs`, `notes` | Public JSON permits imported history only. Its exact `origin.original`, `reason`, and `deduction` fields are shown in the example. Managed outcomes come from run/reconcile. |
| `assess` | `id`, `cycle_id`, `author`, `scope`, `execution_ids`, `result`, `validity_checks`, `outcomes`, `failures`, `findings`, `assumptions`, `remaining_obligations`, `objective_status`, `disposition`, `development` | Full nested example includes outcome judgments, distinct validity checks, and development/branch assessments. Optional `inheritance_refresh` explicitly binds current reassessments of the same ancestors without changing the prospective plan. A validity check with `status: passed` cites a completed output of a planned `validation` requirement; otherwise `assess` records nothing and fails with `validity_evidence_missing`, whose `check_ids` list every such check. A `failed` or `unresolved` check is recorded with the `validity_unresolved` obligation. |
| `checkpoint` | `id`, `cycle_id`, `assessment_id`, `reason`, `next_hypothesis`, `select_for_readiness` | An unassessed unresolved checkpoint may have null assessment; it supplies no validated-result credit. |
| `review` | `id`, `candidate_digest`, `assessor`, `verdict`, `checks`, `limitations` | Checks separately cover `validity`, `scope`, `novelty`, `contribution`, `development`, `branches` and their exact evidence. The `assessor` accepts the optional boolean `author_memory_notes_present` described under "Inputs and receipts" (`invalid_development` for another type); the recorded review keeps the value as given. |
| `manuscript` | `id`, `files`, `claim_evidence` (optional `citation_accounting`) | Refused with `contribution_analysis_missing` while the selected bundle has a complete measurement and no `contribution-analysis`. Files contains `pdf`, `abstract`, `bibliography`, `claims`, `sources`; `sources` can be null. Each claim mapping has `claim_id`, `evidence`. After the decision, readiness and file checks, the pin validates the claims file (below), then the whole mapping, then the evidence. The mapping check: each `claim_id` names a registry claim, none repeats, and every registry claim is mapped (`publication_claims_missing` otherwise). A mapping that leaves a claim unmapped is refused before any reference is compared. The evidence check: every reference equals a reference of the current candidate's evidence as a whole, key order aside, so a reference with another locator or artifact is outside the candidate (`publication_evidence_mismatch`; the message names the first claim id and reference outside the candidate, and `mismatches` in the details lists every `{claim_id, evidence}` pair in mapping order). A claim whose `evidence` is not a nonempty array fails with the same code. `check-claims --file FILE` runs the evidence check alone, with the candidate but without the claims file, the mapping check, the readiness review, the decision or a write (see "Status, projections, and recovery"). `claims` is a nonempty JSON array of objects with nonblank text `id` and `claim` and unique ids (`publication_claims_missing` for a non-array, an empty array, a non-object entry or a repeated id; `invalid_input` for a missing or blank `id` or `claim`). A claim carries at most one marker, `revised: {previous, reason}` or `superseded: {reason}`, with exactly those fields as nonblank text (`publication_claims_missing` otherwise). Optional `citation_accounting` covers every work with a recorded full-text reading and every work that a selected research search of the five purposes cites. A BibTeX entry cites such a work when it carries the work's arXiv family or DOI, or a DOI of a provider alias, as a whole identifier. An entry also cites the work when its title field equals the work's title of two or more words. Titles compare after casefolding, removing accents, naming Greek letters and dropping LaTeX commands, braces, punctuation and spaces. Another bibliography format cites the work when its text carries an identifier or contains the title; there, a shorter title inside a longer one also counts. The lineage gate reads the same evidence. Each work that no entry cites needs one item: `{work_id, cited_as}` with a BibTeX key of the pinned bibliography, or `{work_id, reason}` with nonblank text. `citation_accounting_incomplete` lists each work without an item and its reasons (`fulltext`, `search:<purpose>`). A wrong item is `invalid_citation_accounting`, with its `index` and `cause`: `fields`, `already_cited`, `unknown_work`, `duplicate`, `cited_as_and_reason`, `neither` or `unknown_key`. The bundle keeps `citation_accounting: {cited: [{work_id, key, basis}], not_cited: [{work_id, reason}]}`. `basis` is `bibliography` or `declared`. `key` is the first citing entry in file order, or null for a bibliography that is not BibTeX. `manuscript` fails with `invalid_input` and changes nothing when the bundle it builds holds a value beyond the store bounds (see "Inputs and receipts"). The bundle holds the metric of each cited run five levels deep. A run that exactory-client 0.47.0 or earlier claimed has no metric bound, so `manuscript` fails when such a run's metric nests more than 95 levels. The bundle also holds the admissions of every cycle, so `manuscript` fails for a seed of more than 4300 digits. An earlier release stored such a seed only under an interpreter without a limit on integer string conversion (see "Actual execution"). A bundle pinned since 0.65.0 holds its review inputs and execution observations as `review_inputs_artifact` and `execution_observations_artifact` instead of inline copies (see "Review, publication, and verification"). |
| `manuscript-review` | `id`, `bundle_digest`, `assessor`, `review: ArtifactRef`, `blind: true`, `assignment_id` | The referenced original rubric JSON is unchanged and carries `changes_for_maximum` (`skills/evaluate/RUBRIC.md`); historical JSON is retained, but a context without observed independent provenance cannot authorize current publication. Latest applicable reviews from two distinct independent assessors must accept. The optional `assessor.author_memory_notes_present` is described under "Inputs and receipts". |
| `bind-verdict` | `id`, `task_digest`, `body: ArtifactRef`, `assessment` | Assessment has `assessor`, `provenance`, `independence_basis`, `blind: true`, and separate `soundness`, `novelty`, `impact` checks with full-read links. The verdict body that `body` names nests at most 32 levels, as a payload file does (see "Inputs and receipts"). A deeper body fails with `invalid_input`, and the message names the verdict body. A body with an integer of more than 4300 digits fails the same way, unless the interpreter cannot parse it (`invalid_json`). An earlier release bound deeper bodies. `exactory verify` sends a bound body of 98 levels. The store then refuses to record the response, because the remote intent holds the body 3 levels deeper. The intent stays in flight, so `exactory reconcile` fails with `verdict_reconciliation_pending`. A later `exactory verify` then sends the verdict directly, with a `Managed record skipped` line. A bound body of 99 levels or more fails with `invalid_input` before the request. |
| `manuscript-prediction` | `id`, `bundle_digest`, `blind: true`, `assessor`, `prediction`, `reasons`, `assignment_id` | `prediction` is `{corpus, category, windowStart, windowEnd, percentile, band: {best, worst}}`; the four cohort fields equal the study's frozen collection definition (`prediction_cohort_mismatch` otherwise). `percentile` and both band ends are "top X%" of the cohort, where 1 is the strongest position, with `best <= percentile <= worst` as integers from 1 to 100. One prediction per assessor per exact bundle (`manuscript_prediction_duplicate`); the assessor is not a cycle author. A bundle takes three predicting assessors; a fourth is refused (`manuscript_prediction_excess`), so a complete measurement stays complete. Recorded and summarized as a result: `round` and `contribution-analysis` need the complete measurement, and no gate rule reads the predicted values. |
| `contribution-analysis` | `id`, `bundle_digest`, `investigation`, `position`, `reviewer_changes`, `steps` | `bundle_digest` names the selected bundle (`contribution_analysis_stale`), which needs a complete measurement (`manuscript_measurement_missing`); one analysis per bundle (`contribution_analysis_duplicate`); a current Grand Challenge record exists (`grand_challenge_missing`). `investigation` is a nonempty list of `{query, response: ArtifactRef, finding}`: the original response of each query, saved with `artifact`, and what it shows about the challenges ahead. For managed research, unchanged held searches may be reused through an explicit `refresh` declaration addressing the named unresolved question and meaningful source change; legacy records retain their original query accounting. The assessor receives the original responses. A malformed `response` fails as any artifact reference does (`invalid_input`, `unsafe_path`), and one whose bytes are not stored with `artifact_missing`. `reviewer_changes` dispose of every applicable request once (all three axes in managed research, with stable work-item IDs), `{review_id, change, disposition: adopted|rejected, step_id (adopted only), reason}` (`reviewer_change_missing`). `position` is `{criterion_ids, established, remaining, evidence}`. Steps are `{id, statement, criterion_ids, direction, reach: this_round|next_round, builds_on (current claim ids, `contribution_claim_unknown` otherwise), community: {who, capability, evidence}, risks, evidence}`. Evidence is validated as in `round` (`invalid_development`, `reading_missing`, `round_evidence_mismatch`); any other malformed field fails with `invalid_contribution_analysis`. The record keeps the id and digest of the Grand Challenge record its criterion ids were checked against. |
| `round` | `id`, `closes`, `decision`, `bundle_digest`, `candidates`, `carried`, `next`, `reason` | Needs the bundle's complete measurement and its `contribution-analysis` (`manuscript_measurement_missing`, `contribution_analysis_missing`); the candidates list every step of that analysis (`contribution_step_missing`), and a `continue` goal names `criterion_ids` of the current Grand Challenge record. `decision` is `continue` (exactly one `pursue` candidate and `next` present) or `stop` (at least one candidate, no `pursue` candidate or carried development, and `next: null`). Candidates are `{id, direction: vertical|horizontal, statement, disposition: pursue|rejected|deferred, reason, evidence}`; evidence takes the `source` and `result` shapes or `{kind: "review", review_id}` naming a manuscript review of this bundle; a `source` item needs a full reading that inspects that link (`reading_missing`). `carried` disposes of every `next_round` development the closing round's cycle assessments recorded: `{assessment_id, kind: alternative|branch, question or cycle_id, disposition, reason}`. `next` is `{number, objective, objective_lineage, goal, resource_limits, reopening}`; the goal has `direction`, `criterion_ids`, `field_change`, `statement`, `contribution_delta`, `beneficiaries`, `success_criteria` (`kind` `claim` or `scope`), `stop_conditions`, `continuity`, `route`, `risks`, `evidence`. See "Development rounds". |
| `round-review` | `id`, `round_id`, `round_digest`, `assessor`, `verdict`, `checks`, `limitations` | `verdict` is `approved`, `not_approved`, or `unresolved`. For a `continue` decision the checks are `impact`, `demand`, `novelty_risk`, `feasibility`, `distinctness`, `continuity`, `grand_challenge`; for `stop` they are `stop` and `demand`; each is addressed once with `status` (`passed`, `failed`, `unresolved`), a reason and evidence. One review per assessor per decision (`round_review_duplicate`); the assessor is not a cycle author. |
| `round-admit` | `id`, `round_id`, `review_id`, `reason` | Opens the round of an approved `continue` decision on the current bundle. Records the round's number, goal, objective, limits and opening state; applies a widened objective through its lineage. While the round runs, its receipt is the only output that reports the admitted goal (see "Development rounds"). |
| `round-assess` | `id`, `round_id`, `bundle_digest`, `criteria`, `stop_conditions`, `summary` | Judges every success criterion and stop condition of the admitted goal once with `status` (`observed`, `not_observed`, `unresolved`), an explanation and evidence, on the exact current bundle. The harness derives and stores the round's progress with the record. |

Full-text jobs. A job acquires a PDF full text that a single `fulltext` call
cannot finish: an original above 128 MiB, up to the job's `original_bytes`, or
a transfer or an extraction that would otherwise be lost at an interruption
or a time limit. A job extracts PDF originals only: an original whose bytes do
not start with `%PDF-` stays pending with `unsupported_media_type`, and no
resume or allowance changes that, so an HTML or XML original is acquired with
`fulltext` within its limits. `fulltext-start` records the job, and each
`fulltext-resume` runs it within the invocation allowances and records every
completed transfer unit, network attempt and `pdf-pages-v1` extraction page
durably as a `fulltext_unit` record, so an interrupted resume loses no
completed unit and the next resume continues from the verified byte ranges
already transferred and from the next page. A completed range transfer
records a `source_assembly` for the verified original and a source with the
capture method `http_range_v1`, even while the extraction is pending; the
readers of captures accept `http_range_v1` where they accept `http`. A
transfer of the whole response records a source with the capture method
`http` and no assembly, and a job on a retained original transfers nothing.
Only a completed extraction appends the readable full-text capture to the
work and marks the job complete. A pending job grants no reading,
availability or delivery credit, and a complete capture keeps the
visual-inspection and reading obligations of every other capture.
`fulltext-status --job-id ID [--cursor N] [--limit N]` is read-only and takes
no `--expected-revision`, `--request-id` or `--check`: it returns the job
record (`status`, `pending`, `next_unit`, `capture`, the allowances and the
counts) with `next_action` (`fulltext-resume` while the job is pending,
`complete` when it is) and one page of its units as `units`, `total_units`
and `next_cursor`, with `--limit` from 1 to 100 (default 30). The job codes
are `fulltext_job_pending` (the job is not complete after this invocation,
because its time, page or request allowance for the invocation ended or an
earlier worker has not terminated; resume it), `fulltext_transfer_changed`
(the server's response no longer matches the committed transfer units, for
example another validator, size or range), `fulltext_resume_unsupported` (the
transfer cannot continue where it stopped: the server answered the range
continuation with a whole response or without a strong ETag, or a whole
transfer was interrupted; start a new job, with `use_ranges: false` and a
larger `invocation_seconds` when the server serves no ranges),
`fulltext_unit_limit` (a unit's output or the extracted text exceeds its
allowance) and `fulltext_storage_limit` (the retained artifacts or the scratch
space exceed the storage allowance); `unknown_fulltext_job` names a job id
that no record has. Every acquisition command for one target (an identifier,
a cohort definition or collection, a source URL or a job) holds a lock while
it runs, so a second command for the same target is refused with
`acquisition_in_progress`, whose details name the running operation; a replay
of a committed request takes no lock.

Source deferral requires an explicit authorization artifact and the actual retained
retrieval evidence. It creates no HTTP failure, availability qualification or
reading. At least one main original or required component must be unavailable.
An acquired original without a bundle, a complete unread bundle, or available
units awaiting inspection cannot be deferred. A missing required external image
or a captured HTTP/network retrieval failure can qualify. Malformed, unsupported,
unlinked or stale visual material keeps its ordinary validity or inventory checks.
Source-access and reading-completeness obligations for that exact version
move unchanged to `deferred_obligations` in the foundation report. The inventory
still reports `fulltext_read: false`. Identity, unresolved references, historical
validity, contradiction handling, budgets, actual execution and independent
review remain ordinary obligations.

The decision binds the source captures, required units, readings and the
version's requirement records: every record while the version is a required
full text, because one of its records counts or the citation graph places it at
Tier 1 or 2, and none once it is not. A change makes it `stale` and restores
ordinary blocking obligations.
Record a new decision with a new ID to reassess the gap. `resume-source` restores
the ordinary obligations explicitly. `status` retains active, stale, superseded
and resumed decisions, and `status --summary` counts them and deferred obligations.
Both operations use `--expected-revision` and immutable `--request-id` receipts;
replaying an old request never reactivates its decision.

Deferral changes the preparation dependencies and invalidates dependent admissions
and reviews. Refresh affected synthesis and comparisons before continuing. A
deferred version cannot provide source evidence to a research cycle, assessment
or manuscript claim, even if an earlier body of that version was read. Historical
context and limited source statements remain recorded. An active deferred classic
is exempt from the mandatory primary-citation check; retain its historical
attribution and use actually read alternative evidence for scientific claims.
Lineage citations remain required. Independent reviewers receive the scientific
source-gap disclosure, including excluded claims and missing requirements, without
the private authorization or retrieval-log artifacts. The full objective and all
readiness and publication checks still apply.

A fulltext note uses `depth: "fulltext"`, its current `bundle_id`, and inspections for the actual required units. Abstract inspections use `unit_id: null` and cover the complete saved abstract. Notes report `present`, `absent`, or `not_applicable` as supported by the source. Saving text about a paper without matching its captured location cannot satisfy a reading.

A retry deadline exposed as `next_eligible_at` is a pending condition until that deadline. Resuming earlier can make zero requests and preserve all evidence. An expired deadline is retained history, not a permanent block. Unversioned arXiv material remains unresolved until exact-version evidence is explicitly selected. PDF extraction uses installed `pdftotext` with bounded execution; unavailable extraction remains pending.

Every HTTP attempt of the research harness is admitted through one scheduling state per user on this machine: the SQLite file `provider-pacing.sqlite3` under `$XDG_STATE_HOME/exactory`, or `~/.local/state/exactory` when `XDG_STATE_HOME` is unset. The state holds the current deadlines and cooldowns of each provider group, not a request history. A `Retry-After` cooldown is recorded as soon as the response headers arrive and blocks every identifier, alias, client, process and workspace of that provider group on this machine until it expires. The arXiv hosts form one group with the existing interval of 3 seconds between requests and one active connection at a time, held until the response closes; other providers can have concurrent requests and share their cooldowns. A call refuses with `rate_limited` when the wait would exceed its allowance, naming the provider group and the next eligible time in the details, and the refusal charges no network attempt; with `provider_state_capacity` when live deadlines fill the state; and with `provider_state_unavailable` when the state is corrupt, unavailable or stays busy. Nothing is sent after these refusals. The coordination covers the processes of one machine and one user, so several machines that work on arXiv at the same time must send their requests from one machine.

## Depth plan (`depth`)

Record `{id, values, reason}` as an immutable `depth_plan` and select it as current.
The ID and reason are required nonempty text. Values may contain any subset of:

| Key | Default |
| --- | ---: |
| `loop_abstracts` | 100 |
| `round_loop_abstracts` | 40 |
| `hits_per_query` | 10 |
| `innovation_candidates` | 10 |
| `innovation_cases` | 5 |
| `sample_size` | 100 |
| `search_readings` | 20 |
| `core_fulltexts` | 10 |

Amounts are positive integers with no fixed upper ceiling. Omitted keys retain
their current values, and merged `innovation_cases` cannot exceed
`innovation_candidates`. Budgets still bound cost. Research depth needs no
particular constitution version or revalidation.

A depth plan belongs to a profile, and the selection is kept per profile: a
research plan never changes a verification search, and a verification plan
never changes research work. A plan without `profile` belongs to the
configured profile, and a selection that an earlier release wrote under
`current` is read as the research plan. A verification plan is `{id, profile:
"verification", values, reason, stopping_criterion}` with a nonempty stopping
criterion; a plan in a verification workspace without `profile: verification`
is `profile_inapplicable`. Its values may hold only `hits_per_query`,
`search_readings` and `core_fulltexts`, each at least its default (10, 20 and
10): a smaller value is `invalid_input`, and another key is
`profile_inapplicable`, for the plan and for an explicit per-call override
alike. Work recorded before the plan keeps its amounts. A research plan may
also carry `stopping_criterion`.

```json
{"id":"depth-1","values":{"loop_abstracts":150,"hits_per_query":15},"reason":"Broaden the next comparison."}
```

```sh
exactory-research depth --file depth.json --expected-revision REV --request-id depth-1
```

The plan records its revision and the effective amounts of its profile, all
eight for research and the three for verification. Search, sample,
`require-fulltext` with purpose `core`, innovation, and loop-close accept an
optional `depth` object with their relevant keys. For loop or search-hit
`read-batch`, keep `depth: "abstract"` and add the optional `depth_values`
object, for example `{"loop_abstracts":150,"round_loop_abstracts":60}`.
The earlier object form of `depth` also remains supported; when present it
takes precedence over `depth_values`.
A search can also supply `hits_per_query` directly, which takes precedence over
`depth.hits_per_query`. Transport batch limits remain unchanged.

Each call records resolved amounts: searches store `hits_per_query`; reading
batches store `effective_depth`; samples and core requirements store `depth`;
innovation stores its candidate and case counts; loop closures store their loop
limit in `depth.loop_abstracts`. Historical records lacking these fields retain
their original policy values. Saved sections are reassessed against their saved counts. Under `lineage-v1`,
the recorded candidate minimum and exact external-case count apply. For new
`exhaustive-v1` and `screened-v1` research innovation sections, the minimum
external-case count is `innovation_cases` and the maximum is
`max(10, innovation_cases)`. Historical sections without recorded depth retain
their original policy: five cases from ten candidates under `lineage-v1`,
and five to ten external cases under the other two policies. Defaults and
overrides change work amounts, never claim minimums.

Depth stays outside configuration and dependency digests. With a selected plan,
status includes `limits.depth: {plan_id, profile, stopping_criterion, values}`;
old stores keep their report shape. Summary plan IDs are truncated to 64 characters. Invalid keys, types,
nonpositive values or case relationships produce `invalid_input`; immutable ID,
revision and request conflicts use the normal mutation errors.

## Exploratory computation (`probe-intent`, `probe`)

Run a local computation at any stage, including before a target or cycle exists:

```sh
exactory-lab probe --id finite-check-1 --question "Does the finite example fit?" \
  --script check.py --input data.json --output-dir results --timeout 30
```

The launcher pins the script and repeated `--input` files, records a managed
intent, and reserves the timeout against the experiment wall-time budget before
launch. It requires an initialized configuration, with no target, stage or
readiness check. It runs in the nearest workspace that holds a research store:
a study, or a problem record of `problem-init`, which has no `study.json`. The
intent payload is:

```text
{id, question, interpreter, program, inputs, timeout_seconds}
```

Question and interpreter are nonempty text; program is a pinned artifact
descriptor and inputs is an array of them. `timeout_seconds` is finite and
positive. A different payload under an existing ID is refused. Record one with
`exactory-research probe-intent --file intent.json --expected-revision REV
--request-id probe-intent:ID`.

A probe can also disclose a run of a program that a later plan admits. Give
`exactory-lab probe` the options `--cycle-id ID` and `--program-path PATH`
together (`invalid_input` with only one of them). `ID` is the id of the future
`cycle`, and `PATH` is the stable workspace-relative path of the program across
its versions. The launcher then adds `program_run` to the intent:

```text
{cycle_id, program_path, read_outputs}
```

`read_outputs` lists the run-relative paths of the outputs that you read:
`work/FILE` for a file under the run's working directory, `stdout` or `stderr`.
Repeat `--read-output PATH` for each of them. Without a read path the run is a
completion check: every output and both streams are stored sealed under
`research/sources/sealed/`, and the outcome records `outputs_unread: true`. A
sealed object keeps its bytes and its hash, but no ordinary read returns it. A
reading, a source passage, a result reference, an import or a pin of a sealed
object fails with `artifact_sealed`, and review packets and native exports carry
its hash, size and media type without the bytes. With at least one read path
nothing is sealed, and `read_outputs` names each declared path that the run
produced, with its hash. A disclosure whose cycle already has a plan is refused
with `preplan_cycle_frozen`. An existing intent cannot gain this context: a
different payload under its ID is refused.

```sh
exactory-lab probe --id completion-check-1 --question "Does the program run to completion?" \
  --script experiment/code/check.py --input experiment/data.json --timeout 60 \
  --cycle-id cycle-2 --program-path experiment/code/check.py
```

The example runs from the workspace root. `--script` and `--input` are read
relative to the current directory, and `--program-path` is recorded as given: a
canonical workspace-relative path (`unsafe_path` otherwise) that the harness does
not compare with the script.

The script receives `[current_python, copied_script, copied_input, ...]`, with
closed stdin and a fresh cwd under `research/probes/ID/`. `--output-dir` is
relative to that cwd and defaults to `.`. Every new or modified file there is
pinned, including modified copies of original inputs; the intent retains their
original bytes. Outputs, stdout and stderr retain exploratory provenance through
later result imports. They supply no managed prospective execution credit.
Unrelated imported history keeps its existing standing. A separate, actually
observed admitted execution may validate the same result independently.

The outcome payload is:

```text
{id, command, outputs, stdout, stderr, exit_status, elapsed_seconds, observation}
```

Command and observation are required text. Outputs is an array of pinned
descriptors; stdout and stderr are pinned descriptors. Elapsed seconds is finite
and nonnegative. Exit status is an integer code, `timed_out`, `failed`,
`output_bound` or `interrupted`. Nonzero exits, timeout cleanup, launch failures
and output-bound outcomes retain actual usage. Recording the outcome charges it
even if the actual cost exhausts the budget, and releases the intent's hold.
`--observation TEXT` overrides the factual default. Manual recording uses
`exactory-research probe --file outcome.json --expected-revision REV
--request-id probe:ID`; a locally claimed launch must retain its terminal evidence.

A disclosed run adds `program_run` to the outcome:

```text
{started_at, ended_at, outputs, read_outputs, outputs_unread}
```

The worker observes `started_at` and `ended_at` as UTC timestamps. `outputs`
names every captured file and both streams as `{path, sha256}` pairs, in the
order of the outcome's `outputs` followed by `stdout` and `stderr`.
`read_outputs` is the subset that the intent declared read, and `outputs_unread`
is true exactly when the intent declared no read path. A recorded outcome must
keep the intent's context: `invalid_input` for a missing context, a changed
hash, a changed read policy, a repeated path, or a completed run without both
times. Recovery of an interrupted run keeps the times it observed and records a
missing time as `null`; it never invents an execution time. The readiness export
lists each disclosed run beside the admissions of its plan (see "Review,
publication, and verification").

A worker retains exclusive ownership independently of its launcher. The frozen
`supervision.json` records worker and program identities, the process group and
deadline. If the worker disappears, an independent watchdog process enforces
that deadline even when nobody retries. It writes `supervisor-timeout.json`,
terminates the original program group, verifies that the group has no running
members, and writes `supervisor-terminal.json`. These files retain the frozen
configuration and process identities and measured elapsed time. A later retry
uses the terminal evidence to capture outputs, record the outcome, reconcile
actual usage and release the reservation after verifying termination.

A retry
during a live run prints `{"id":"ID","status":"pending","state":"running"}`
and changes neither the revision nor the reservation. A terminal ID prints its
original receipt, even if the newly supplied script path no longer exists; it
never executes again. An intent without an outcome is reconciled only after its
owner lock is free and the original computation has stopped, or checked terminal
evidence and process-group checks establish completion. Complete
files recover the original result; otherwise the outcome is `interrupted`.
Partial artifacts remain pinned. Last measured elapsed time is charged and any
unmeasured remainder is explicitly counted as unknown resource usage. Absence of
an outcome alone never releases a hold. Use a new ID for a new execution.

Each captured stream is bounded at 32 MiB. Exceeding that bound terminates the
process group and records `output_bound`; the bounded prefix is pinned, the raw
file remains, and its full SHA identity retains exploratory provenance. Unsafe
output paths produce a charged failed capture without admitting unsafe artifacts.
The launcher prints JSON receipts successfully for recorded failures and timeouts.
Its managed request IDs are `probe-intent:ID`, `probe-launch:ID` and `probe:ID`.

Errors include `configuration_missing`, `resource_budget_exhausted` before launch,
`probe_intent_missing` for an outcome without an intent, `invalid_input`,
`artifact_missing`, `artifact_corrupt`, `artifact_sealed`, `unsafe_path`,
`preplan_cycle_frozen`, and normal immutable/revision/request conflicts. Status
adds `probes: {count, elapsed_seconds}` when probe history exists. Probes alone
never form a result candidate.

## Search depth, coverage and provisional questions (`search`)

A search records its resolved `hits_per_query`, optional per-call `depth`, and one
coverage object per query:

```text
{query, requested_k, returned_count, inspected_count, page_positions,
 provider, ordering, kind}
```

`returned_count` counts captured result rows; `inspected_count` counts distinct
captured works accounted by a disposition, without granting reading credit.
Positions preserve unique captured page starts in receipt order. Provider is a
name or a list for a mixed-provider query; ordering contains explicit captured
request sort parameters, or null. `complete` requires the existing native
enumeration evidence with no pending pages. `top_k` requires an intact ordered
prefix of at least requested k. Otherwise new coverage is `bounded_unordered`.
An author's label cannot establish completeness or ordering. All captured
responses remain in the record. Legacy coverage is derived from saved evidence,
with `unspecified` only where that evidence is ambiguous; nothing is recaptured.

New `integrity_errors` contains the search's pending items except
`search_response_incomplete`. In research, foundation and direction checks use
those integrity errors and accept bounded enumeration. Verification keeps every
recorded `pending` item, including `search_response_incomplete`, and retains
its `search_pending` enumeration obligation under both historical and current
record shapes. `find_search_integrity_errors(search)` supplies the same interpretation
for historical records without changing their reports.

Before roots exist, add required text `question` to the normal search payload:

```json
{"id":"early-search","profile":"research","purpose":"direct","question":"Does this direction already have a solution?","queries":["bounded sequences"],"responses":[{"source_id":"CAPTURE_ID","query":"bounded sequences","query_locator":{"kind":"json","pointer":"/query","value":"bounded sequences"},"results_pointer":"/results"}],"captured_at":"2026-10-16T12:00:00Z","found_work_ids":[],"cited_work_ids":[],"verdict":"nothing-new","impact":"No matching result appeared in this captured response.","gaps":[],"dispositions":[],"scope":"exploratory"}
```

Replace `CAPTURE_ID` with the matching captured receipt, set its actual capture
time, and account for every returned work as in a normal search. The record has
`scope_digest: null` and `provisional: true`, appears in status, and grants no
purpose selection, loop coverage, claim minimum or foundation credit. Once roots
exist, a new scoped search may reuse the exact responses and queries with
`binding: {"provisional_search_id":"early-search","reason":"These unchanged receipts address the chosen roots."}`.
The binding validates matching profile/purpose and records the current scope.
Roots never force recapturing unchanged evidence. Invalid receipts, substitutions
or unsupported depth produce the normal `invalid_search`/`invalid_input` errors;
missing scope without a question still produces `roots_missing`.

## Explicit supplement components

`fulltext` normally retrieves the exact main article. Its arXiv final-URL and
version checks remain strict. An optional `component` object retrieves a
supplement through an explicit assessed relationship to an already acquired
main original. This first contract supports only `component_correspondence`.
It does not establish historical arXiv attachment identity or main-article
version equivalence.

The request shape is:

```json
{
  "identifier": "arxiv:2601.00001v1",
  "url": "https://publisher.example/supplement.pdf",
  "component": {
    "kind": "supplement",
    "unit_id": "external-supporting-information",
    "parent_source_id": "acq:REPLACE_WITH_PARENT_SOURCE_ID",
    "parent_original_sha256": "REPLACE_WITH_64_HEX_PARENT_SHA256",
    "expected_original_sha256": "REPLACE_WITH_64_HEX_COMPONENT_SHA256",
    "basis": "component_correspondence",
    "assessment": {
      "sha256": "REPLACE_WITH_ASSESSMENT_SHA256",
      "path": "research/sources/objects/REPLACE_WITH_ASSESSMENT_SHA256",
      "size": 1234,
      "media_type": "application/json"
    }
  }
}
```

Replace the example pins with actual values. `assessment` is an immutable
`ArtifactRef` to a JSON object. Pin that JSON with `artifact` before acquisition;
registration alone grants neither acquired provenance nor reading authority.
The assessment requires all these fields:

| Field | Contract |
|---|---|
| `status` | `accepted` or `pending`, an explicit research assessment. |
| `scope` | Exactly `required_supplement_only`. |
| `conclusion` | Nonempty source-grounded scoped conclusion. |
| `limitations` | Nonempty array of distinct nonempty strings. |
| `historical_attachment_identity` | Exactly `false`. |
| `main_article_equivalence` | Exactly `false`. |
| `evidence.requirement` | A parent `Link` locating the citation that requires the supplement. |
| `evidence.identity` | Object with `title` and `authors` evidence pairs; optional `affiliations` pair. Locate the component cover identity. |
| `evidence.references` | Nonempty array of evidence pairs locating corresponding figures or sections. |
| `evidence.conditions` | Nonempty array of evidence pairs locating the compared scientific conditions. |

Each evidence pair has precisely `parent`, `component`, and `judgment`:

```json
{
  "parent": {
    "version_id": "arxiv:2601.00001v1",
    "source_id": "acq:REPLACE_WITH_PARENT_SOURCE_ID",
    "artifact": "REPLACE_WITH_ACTUAL_PARENT_ARTIFACT_REF",
    "locator": "REPLACE_WITH_ACTUAL_PARENT_LOCATOR"
  },
  "component": {
    "document": "original",
    "locator": {
      "kind": "pdf",
      "page_index": 0,
      "printed_page": null,
      "region": [0, 0, 1, 1]
    }
  },
  "judgment": "The located cover and parent passages identify the same title and authors."
}
```

The two placeholder strings in `parent` stand for structured `ArtifactRef` and
locator objects, using the source-link contracts above. A component location is
`{document: "original" | "text", locator}`. It resolves against the fresh HTTP
original or its actual extraction, not a local note or an earlier failed
capture. A text/span locator must match that extraction exactly. PDF locators
use its acquired page map. `expected_original_sha256` must match the fresh
response bytes. Scientific judgments remain the assessor's assertions; the
runtime checks locations and declared scope and does not infer scientific truth
from matching strings.

The parent must be an exact version with complete acquired main original bytes.
An existing imported article bundle for that source must already contain the
named required supplement unit. Every cited parent location must be covered by
an actual recorded inspection of that exact parent source. A partial parent
reading is sufficient; the missing supplement may be why it remains partial.
Publisher landing captures and offline comparison notes can inform the
assessment and remain separately retained under their real provenance. They
cannot be presented as native HTTP acquisitions by attaching them to a note.

Successful acquisition retains the real final publisher URL, HTTP response and
headers. Observed arXiv identifier and version remain null. The capture's
`component` field records `status: "bound" | "pending"`, `parent_version_id`,
the complete request `spec`, and its content `identity`. An unresolved or invalid
binding retains the fetched bytes and extraction while leaving the capture
pending. Retry with a new request ID and assessment as needed; old finalized
request IDs replay their original outcomes.

Import a new immutable bundle to fill the existing required supplement unit
with a whole-component text link. For a PDF component, add required original
visual units (`figure`, `table`, or `equation`) with whole-page PDF locators
(`region: [0, 0, 1, 1]`) for every extracted page, including the cover. Retain
all prior required units and register ordinary reading inspections for every
required text and visual unit. Missing pages, partial text and omitted
inspections remain incomplete. Component acquisition and an accepted assessment
do not close reading obligations.

A component cannot be a bundle main source, verification main-original target,
or main-original coverage substitute. Every bundle component link rechecks the
exact parent original SHA-256. The binding and assessment identity participate
in source-link, reading and preparation dependencies; a new assessment can
require a new reading while previous acquisitions and readings remain intact.
Current validation rereads the pinned evidence, so missing or changed evidence
cannot retain current acceptance.

Component-specific failures use typed `ResearchError` codes, returned in the
acquisition `pending` array after retrieval or raised at later linking boundaries:

| Code | Meaning |
|---|---|
| `invalid_component` | Invalid fields, unsupported kind/basis, invalid scope, missing paired evidence, or changed binding identity. |
| `component_parent_mismatch` | Wrong exact parent version/source/original, incomplete main original, or a different bundle main original. |
| `component_hash_mismatch` | Fresh component original does not match its expected hash. |
| `component_requirement_missing` | The existing required supplement unit is absent, or a reading has not filled it with this component. |
| `component_evidence_uninspected` | A cited parent location lacks a recorded native inspection. |
| `component_pending` | Unresolved assessment/binding or unavailable component extraction. |
| `component_page_missing` | Reading obligation: an original PDF component page lacks a required whole-page visual unit. |

Existing errors remain applicable: `missing_version`, `invalid_locator`,
artifact integrity errors, HTTP/extraction failures, and `source_pending` for a
pending capture. Main-source promotion raises `invalid_bundle` or
`invalid_target`. Standard reading obligations still report
`required_unit_incomplete` and `required_unit_uninspected`.

## Actual execution

`bind-run.script`, input paths, and ordinary output paths are relative to `experiment/`. Outputs `stdout` and `stderr` select the captured streams. Each declared output has `{id, requirement_id, path, media_type}`; output IDs and paths must be unique, and a result cannot overwrite an input. An output of a `result` requirement takes any media type: an assessment cites a text or JSON output through a span, text or JSON locator, and a figure, a PDF or another binary output through an artifact locator or a result PDF locator (see "Inputs and receipts"). An output of a `validation` requirement is cited through its text or JSON, so a new `bind-run` refuses a `validation` output unless its media type is `text/*`, `application/json` or a `+json` type; bind such a binary file to a `result` or `log` requirement instead. `bind-run` with the payload of a binding that an earlier release recorded returns that binding, and an artifact locator also cites a binary `result` output of such a binding. The admitted argv begins with a Python executable and the exact declared script. Local execution pins the resolved interpreter file, its bytes and the declared Python version in the binding's `runtime`. When `argv[0]` is a symbolic link, as a venv's `bin/python3` is, `runtime.path` keeps `argv[0]` and `runtime.resolved_path` names the file. The worker then starts that link, so the program keeps the venv's packages. A link that resolves to another file when the launcher claims the run stops the launch with `execution_runtime_changed`. If the interpreter changes after the claim, the worker refuses to start the program, and the launch returns `execution_recovery_required`. Record that run with `reconcile-run`, giving `resolution: "interrupted"` and a reason. For a regular file, `runtime.path` is its resolved path, as in earlier releases. A binding that exactory-client 0.47.0 or earlier recorded for a link keeps the resolved file as `runtime.path`. Its launch starts that file, without the venv's packages, and `bind-run` with the same payload returns that binding. `bind-run` and `exactory-lab run` must run under a Python that resolves to the same file as `argv[0]`; for a venv, start them as `VENV/bin/python3 "$(command -v exactory-research)" bind-run ...` and `VENV/bin/python3 "$(command -v exactory-lab)" run ...`. Other imported command histories are preserved without claiming this launcher observed them.

```sh
exactory-lab run code/program.py --admission run-1 --backend local --timeout 30 \
  --expected-revision 42 --request-id actual-run-001
```

Backend, timeout, and seed must match the binding and admission. A null seed needs its declared reason; a seed is passed as `EXACTORY_LAB_SEED`. The run's metric comes from the first of three sources that gives one. A line or file gives a metric only when its JSON is finite and within two bounds. It nests at most 32 levels, the same depth as a command input (see "Inputs and receipts"). It holds no integer of more than 4300 digits, so that every supported Python reads the metric back from the store. The first source is the last stdout line that gives a metric and has a `metric` key. The second is the conventional fallback file `results/SCRIPT_STEM.json` inside the private working directory. The third is the first declared `application/json` or `+json` output of a `validation` requirement, which the run configuration records as `metric_output`. The process runs in a private copy of the pinned script and inputs. The launcher claims the admission once and rechecks current preparation before releasing its token. Before publishing its terminal outcome, the worker seals the presence or absence, SHA-256, and size of every declared output, both streams, and the fallback metric file. Reconciliation rejects changed, removed, or newly inserted bytes and saves the complete sealed inventory as immutable artifacts. Current author readiness checks the saved result, log, and metric against that original seal. `result.json` and `log.txt` under `intermediate/lab/runs/<admission id>/` are generated views of the summary and the log (see "Final and intermediate outputs"). `exactory-lab run` prints the run summary of the observation, unchanged (`admission_id`, `cycle_id`, `node`, `ok`, `is_buggy`, `returncode`, `timed_out`, `duration_s`, `metric`, `seed`, `log`, `stderr_tail`, `backend`, `program_sha256`, `timeout_seconds` and `scientific_validation`), and beside it `execution_id` and `outputs` of the execution that the store records for the admission after the launch: the id of that record, and its `outputs` list unchanged, one entry `{id, requirement_id, artifact}` for each declared output that the run produced, where `artifact` is the output descriptor that the execution recorded. Both are null when the store holds no execution for the admission after the command. `reconcile-run` returns the same object. The two fields are printed only: the stored receipt, the run's `summary.json` under `research/runs/` and `intermediate/lab/runs/<admission id>/result.json` hold the summary alone. A retry of `exactory-lab run` or `reconcile-run` with the original request ID prints the same values, because the execution record is immutable. A launch whose recording fails prints `{"error": ...}` on stderr and nothing on stdout, as before. To find the execution, both commands read the store once more after the command; the time of that read grows with the store.

The metric bound does not apply to a run that exactory-client 0.47.0 or earlier claimed, so a metric that such a run recorded stays valid. The store bounds in "Inputs and receipts" still apply to each new record and result that holds such a metric. If the interpreter reads the metric, these operations fail with `invalid_input`:

- `reconcile-run` of such a run without an observation, when the metric nests more than 98 levels. The observation holds the metric two levels deep. A retry of `exactory-lab run` with the original request ID reconciles the run and fails in the same way. If the run has no outcome yet, the first attempt of either kind records the outcome and changes the revision before it fails. Every other attempt changes nothing. After the error, the run has an outcome but no observation, so its strategy admits no further run (`execution_usage_reconciliation_required`).
- A new `reconcile-run` request for such a run that an earlier release observed, when the metric nests more than 99 levels. The result of the request holds the metric one level deep. A retry of `exactory-lab run` with the original request ID fails in the same way if that launch stopped before it reconciled the run. Both change nothing.
- `manuscript`, when the candidate's evidence includes a result of such a run whose metric nests more than 95 levels. The bundle holds the metric five levels deep. The failure changes nothing.

An interpreter without a limit on integer string conversion reads a metric integer of any length. There, an integer of more than 4300 digits makes each of these operations fail the same way. A line or file that the interpreter cannot read, because of its limit on integer string conversion or its recursion limit, gives no metric.

Under an interpreter without a limit on integer string conversion, such as Python 3.9.6, an earlier release accepted a seed of more than 4300 digits. The claim, the outcome and the observation of a run hold its seed, and so does the result of `reconcile-run`. These operations then fail with `invalid_input` and change nothing:

- `exactory-lab run` of such a run that no launcher claimed. The next admission of its strategy then fails with `execution_pending`.
- `reconcile-run` of such a run that an earlier release claimed but did not observe, and a retry of `exactory-lab run` with the original request ID. The next admission of its strategy then fails with `execution_pending`, or with `execution_usage_reconciliation_required` if an earlier release recorded the outcome.
- A new `reconcile-run` request for such a run that an earlier release observed. A retry of `exactory-lab run` with the original request ID fails in the same way if that launch stopped before it reconciled the run.
- `manuscript` of an otherwise ready study in which any cycle holds such an admission. The bundle holds the admissions of every cycle.

For a local run, the launcher starts a worker process after the claim and waits until the worker reports that it is ready. The wait continues while the worker process is alive, for at most the binding's `timeout_seconds` plus 10 seconds. If the worker exits before it is ready, or is not ready in that time, the launch returns `execution_recovery_required` and the program does not run. If the worker process cannot start, for example at a limit on processes or open files, the launch also returns `execution_recovery_required`. Record that run with `reconcile-run`, giving `resolution: "interrupted"` and a reason. After the launcher releases its token to a ready worker, it waits for the worker to end for at most the binding's `timeout_seconds` plus 16 seconds. If the worker is still running then, the launch returns `execution_recovery_required` and the worker continues. After the worker ends, `reconcile-run` without a resolution records the run's outcome.

After the launch, or after `reconcile-run`, the harness shows the run in `intermediate/lab/runs/<admission id>/`: `run.json` with every record of the run, `result.json` with the observation's summary and `log.txt` with the observed log; the run directory under `research/runs/` keeps the pinned files. Releases before 0.68.0 also wrote the summary and the log to `experiment/results/<node>.json` and `experiment/logs/<node>.log`; 0.68.0 does not. A retry returns/reconciles that run; it does not execute the same admission twice. Missing or lost results require `reconcile-run`. A dead local worker may be recorded interrupted after ownership checks. Unknown usage retains its full reservation. An interrupted process, timeout, missing output, or old modeled managed record cannot become current author readiness through a public result JSON label. An imported observation retains its actual provenance and chronological limits and does not consume or satisfy a prospective admission.

A second run of an assessed plan can change only the format of its outputs, for example the name of a file or the serialization of a JSON value. Give `admit` the optional field `format_only_of` with the id of the earlier execution. That execution must be a completed managed run of the same plan, with outputs, and an assessment of the cycle must list it (`format_only_reference_invalid` otherwise). Such an admission skips the reopening check and the closed-cycle refusal: the plan's recorded strategy failures stay as they are, and a `complete`, `failed` or `budget_paused` cycle keeps its status, so an ordinary admission after the conversion is still refused. Every other rule applies: the current decision, the other plan dependencies (`plan_dependencies_stale`), the pending run rule (`execution_pending`), the strategy budget (`strategy_budget_exhausted`) and the reviewed tranche (`research_tranche_exhausted`). Bind and launch the run as usual. Recording accepts the execution only when every output equals an earlier output of the same requirement: byte for byte, or, when both outputs declare a JSON media type, as equal strict JSON values, where a boolean never equals a number and key order and whitespace do not count. An output that keeps the id of an earlier output must equal that output, and every earlier output must be covered. Otherwise the recording fails with `format_only_output_mismatch`, no execution or outcome record is written, and the admission keeps its reservation under the ordinary reconciliation rules of this section. A recorded conversion carries `format_only_of: {execution_id, execution_digest}`. It leaves every earlier execution, assessment and failure in place, and its outputs do not count as changed evidence in a `reopening` entry. Its outputs need their own assessment before a candidate cites them. A figure redrawn in another image format, for example PNG to SVG, is not covered: its bytes differ and it is not JSON. Records without the field keep their earlier shape and behavior.

Known wall time is recorded for successful, failed, partial, and timed-out processes. Charges retain the reservation floor and add measured overruns. A claimed outcome without its completed observation remains pending for fresh execution, even when its outcome record already exists. Reconcile the original observation before spending remaining strategy budget. If an older pinned terminal contains measured time that its historical account did not charge, that strategy's new admissions and unstarted launches remain pending for accounting reconciliation. Historical payloads, receipts, and charges are preserved; this release does not automatically add a correction or invent unmeasured usage.

Older terminal records without a worker output seal cannot establish a new managed observation or current readiness. Already recorded historical receipts still replay. An explicit dead-owner recovery may retain partial bytes with a recovery seal, which is not a worker completion seal and supplies no completed-result authority. Do not create a replacement seal for old bytes whose original producer binding is unavailable.

For Colab, set `EXACTORY_LAB_COLAB_DIR` to an existing shared folder on both hosts, bind `backend: "colab"`, and declare the actual remote Python version in `command.versions.python`. Run the installed `exactory-lab colab-serve` on the remote host. The client needs the same admitted logical script path, but the remote runner maps it to its private working directory and records the actual executable path and SHA-256. A runner started with a symbolic link, such as a venv's `bin/python3`, starts each program with that link. The program then has the venv's packages, and the runner records the file behind the link as `resolved_path`.

Protocol 2 writes immutable job bytes and `READY`, records a runner's unique nonce and runtime, then commits a client release and `GO` only after current local preparation passes. A saved release does not authorize emitting a missing GO after preparation changes; that side effect needs a fresh current check. An already started or completed original job is collected without recreating a missing signal. Only the released nonce may start once. The producer validates its seal before publishing result files. Collection checks transport identities and per-file hashes while copying; reconciliation then validates the complete seal before recording the outcome. Both client and runner need the seal-aware execution implementation; older unsealed terminal bytes remain pending. Old jobs without protocol 2 cannot execute. A dead heartbeat or transport wait timeout leaves the run pending; it is not evidence that remote execution failed. A runner crash or mirror conflict after a durable release can require external inspection. No retry silently creates another job or refunds its reservation.

## Planning a successor

A successor is another full `cycle` payload with a new `id`, a distinct
`question`, the same complete `objective`, and a current claim-specific
`literature` comparison. Its `predecessor` is a checkpoint ID, not a cycle ID.
The checkpoint must preserve the same complete objective, and at least one
`inheritance` entry must explain its contribution and remaining limits.

| Field | Exact shape and supported choices |
| --- | --- |
| `predecessor` | `null` for an initial cycle, or the string ID of a retained checkpoint. |
| `inheritance[]` | `{checkpoint_id, assessment_id, use, evidence, assumptions, deduction}`. `assessment_id` is the exact ID saved in that checkpoint, or `null` if it preserved no assessment. `use` is `validated_result`, `failure`, or `unresolved`; `evidence` is a nonempty array of the result/source evidence objects above; `assumptions` is a string array; `deduction` explains the contribution to the complete objective. |
| `reopening[]` | `{assessment_id, signal_id, reason, evidence}`. Name an actual observed failure in that assessment, explain the changed condition, and supply a nonempty evidence array containing changed original source or output bytes. Use `[]` when no recorded failure needs reopening. |
| `prior_knowledge` | Optional, for a first cycle or a successor. `{program: ArtifactRef, program_path: string, probe_ids: string[], account: string}`. `program` is the pinned program that `admit` will name, `program_path` its stable workspace-relative path, `probe_ids` the ids of every run of that program that a probe disclosed before this plan, and `account` what those runs made known. |

For example, replace these three fields in a full successor `cycle` payload to
inherit an assessed failure and address its obstruction. This is a partial edit;
replace every illustrative ID, hash, source passage, locator and judgment with
the actual retained records. The result and changed source have the complete
evidence shapes accepted by the command.

```json
{
  "predecessor": "checkpoint-failure-001",
  "inheritance": [
    {
      "checkpoint_id": "checkpoint-failure-001",
      "assessment_id": "assessment-failure-001",
      "use": "failure",
      "evidence": [
        {
          "kind": "result",
          "execution_id": "execution-001",
          "output_id": "result",
          "artifact": {
            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "path": "research/sources/objects/0000000000000000000000000000000000000000000000000000000000000000",
            "size": 15,
            "media_type": "application/json"
          },
          "locator": {"kind": "json", "pointer": "/result", "value": {"bound": 9}}
        }
      ],
      "assumptions": ["n is an integer in the stated finite range."],
      "deduction": "Retain the strict-bound failure while testing the original non-strict objective."
    }
  ],
  "reopening": [
    {
      "assessment_id": "assessment-failure-001",
      "signal_id": "counterexample",
      "reason": "The newly acquired comparison addresses the recorded strict-bound obstruction.",
      "evidence": [
        {
          "kind": "source",
          "link": {
            "version_id": "arxiv:2601.00002v1",
            "source_id": "SOURCE_ID",
            "artifact": {
              "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
              "path": "research/sources/objects/0000000000000000000000000000000000000000000000000000000000000000",
              "size": 15,
              "media_type": "text/plain"
            },
            "locator": {"kind": "span", "start": 0, "end": 15, "sha256": "012bf3924437baf5382a1783506c0eb7c5a1f9926a1f047ca4be7ed992bed9f9", "excerpt": "Source passage."}
          }
        }
      ]
    }
  ]
}
```

`validated_result` requires a currently validated inherited assessment and the
exact result references used by that assessment. Retain all its assumptions.
An `unresolved` inheritance earns no result credit. For a checkpoint with
`assessment_id: null`, no recorded output, and no pending admission, its own
original plan sources and assumptions can support `use: "unresolved"`. When
outputs exist, cite retained execution evidence. Do not invent an assessment or
an output to continue an unexecuted or interrupted plan.

For the same strategy, every recorded observed failure needs an applicable
`reopening` entry. A renamed source, a new locator within old bytes, or a changed
label is insufficient; the original source or output identity must change and
the reason must explain how that evidence addresses the failure. The same
normalized method, test, objective and scope retain their original account and
limits. A successor ID does not reset charges, erase failures, or reopen an
exhausted budget. Registering a successor also does not launch it; obtain a
current admission and binding before execution.

A plan accounts for every run of its program that a probe disclosed before it
(see "Exploratory computation"). `prior_knowledge.probe_ids` names each
program-run probe whose `cycle_id` is this plan's id, whose `program_path`
equals the account's path, or whose program bytes equal the account's program.
A missing one refuses the plan with `preplan_probe_missing` and lists the
`probe_ids`. A disclosed run without an outcome refuses it with
`preplan_probe_pending` and its `probe_id`. An id that names no completed
program-run probe is `preplan_probe_unknown`. A plan without `prior_knowledge`
is refused the same way when a probe disclosed a run under its cycle id. `admit`
then requires the program of the account: another program is
`preplan_program_mismatch`. A plan without an account cannot admit a program
that a disclosed probe ran before the plan was recorded (`preplan_probe_missing`
at admission). A frozen plan cannot gain runs: a later disclosure under its
cycle id is refused with `preplan_cycle_frozen`. Records written before 0.65.0
have none of these fields and keep their results and digests.

## Assessing unfinished work

`exactory-research example assess` prints a complete-case payload shape. Choose
the values supported by the actual observations; its `achieved` and `complete`
values are not defaults for a new assessment. Keep the full original objective,
the assessed scope, every actual execution, and the remaining obligations.

| Field | Supported values and meaning |
| --- | --- |
| `objective_status` | `open`: the complete original objective has unfinished obligations. `achieved`: the assessment claims that the whole objective is established; all current evidence and scope requirements still apply. |
| `disposition` | `continue`: further work remains on this cycle's branch. `complete`: the branch is claimed complete. `failed`: retain a failed branch and its actual failure assessment. `budget_paused`: retain unfinished work under its resource limit. |
| `development.alternatives[].disposition` | `pursue`: a useful development remains. `not_useful`: the stated question is not useful for this candidate, with a reason and evidence. `resolved`: the question is substantively resolved, with evidence. `budget_paused`: useful development remains and is paused for its budget. `next_round`: a useful development that exceeds this round's admitted scope; it leaves no readiness obligation and is carried to the round gate, where the next `round` decision disposes of it in `carried`. |
| `development.branches[].disposition` | The same five values as alternatives: `pursue`, `not_useful`, `resolved`, `budget_paused`, `next_round`. Each entry names an actual `cycle_id` and retains its reason and evidence. At readiness, a `resolved` branch requires its own current assessment with `validated_result: true`. |

The top-level `disposition` and the two nested disposition fields use different
vocabularies. A retained `pursue` or `budget_paused` alternative or branch leaves
an unmet development obligation; a `next_round` one leaves none and is disposed
of by the next `round` decision. Neither `not_useful` nor `resolved` is a way to
erase a failed branch, its evidence, or its resource account. Readiness includes
the candidate's own branch and every other retained cycle.

For an unfinished assessment whose next development has not yet been assessed,
replace these four fields in the full `assess` payload. This JSON is a partial
edit, not a standalone command payload; replace its obligation with the actual
unfinished work and retain accurate values for all other required fields.

```json
{
  "remaining_obligations": ["Assess the unexecuted planned test and its result validity."],
  "objective_status": "open",
  "disposition": "continue",
  "development": null
}
```

`development: null` is accepted and retained, with
`development_assessment_missing` still unmet. An unexecuted plan can retain
`execution_ids: []`, `validity_checks: []`, and a `result` statement explaining
that no execution result is available. Its `result.evidence` can reference
actual plan sources without claiming an output. Its planned outcome and
failure statuses remain `unresolved` when they have not been observed. That
assessment supplies no validated result or completion credit. When a substantive
development assessment is available, use the full nested shape from the example:
`development.strategy` is `generalization`, `weaker_assumptions`, `mechanism`,
`tightness_limits`, `unification`, `representation_change`, `transfer`,
`practical_usefulness`, `other`, or `none`. A selected strategy requires a
nonempty `next_question`; `none` requires `next_question: null`. A useful next
question remains an obligation before readiness.

Record a valid negative result separately from invalid or missing evidence.
Validity-check statuses are `passed`, `failed`, or `unresolved`; planned outcome
and failure-signal statuses are `observed`, `not_observed`, or `unresolved`.
Observing a scientific failure signal can establish a valid negative result.
Setting an assessment's `disposition` to `failed` does not by itself establish
that the underlying result is invalid, and setting it to `complete` does not
establish readiness. Inspect the resulting obligations and current whole gate.

## Claim-scoped currency

For managed research, plans, admissions, assessments, checkpoints, candidates,
readiness reviews and scoped publication contracts use one claim-dependency
projection. It includes objective, scope and assumptions, plan, executions,
evidence and its cited source records, inheritance, validations, strategy
failures and authorizations. Full dependency maps remain recorded for audit.
Global preparation, configuration and constitution digests, foundation inventory
and synthesis sections stay outside the projection. Synthesis obligations stay
outside result assessment and writing readiness.

An unrelated reading, search judgment, synthesis edit or policy adoption leaves
the claim current. Plan literature and novelty comparisons still require exact
scope, resolved gaps and valid cited full-read sources. A later tool result
reaches a claim when used as evidence or when a source change must be declared
through `source-impact`. Changed supporting bytes, scope, assumptions or
authorization require affected reassessment.

Validate historical digests against their recorded inputs, then compare the
projection of those inputs with the current projection, transitively through
checkpoints, inheritance, candidates, reviews and scoped contracts. Reconstruct
every field of an opaque legacy composition from recorded evidence, snapshots
or packets, including branch, strategy-account and source-record identities.
Verify the reconstruction against the original digest before comparing
projections. The managed store retains per-record history and reconstructs the
recorded input snapshot at its original revision. Missing or unauthenticated
historical inputs report `claim_reassessment_required`, naming the record to
reassess. See [the upgrade procedure](research-workflow.md#studies-in-progress).

Studies may retain adopted Version 6 with byte-identical configuration,
foundation and synthesis reports. Research adoption of Version 7 alone changes
no claim. Apply new direction and Q1 obligations at the next relevant commitment
and reconcile pending executions once under their frozen authority. Verification
Version 7 adoption is out of scope for this release; verification retains
Version 6 and its fixed contract.

## Refreshing inherited assessment dependencies

A changed claim dependency can make an inherited assessment stale. Unrelated
preparation, synthesis or constitution changes leave managed inheritance current. Reassess
the ancestor's actual retained execution first and save a new checkpoint. Its
old checkpoint remains historical. A descendant's original plan still names
that old checkpoint; the harness never substitutes the latest one implicitly.

When reassessing the same descendant execution, add optional
`inheritance_refresh: {plan_digest, bindings}` to the full `assess` payload.
`plan_digest` is the original immutable descendant plan's digest. Each binding
has exactly `previous_checkpoint_id`, `previous_assessment_id`, `checkpoint_id`,
`assessment_id`, `evidence`, `assumptions`, and `deduction`. Refresh each original
edge at most once. Both old and replacement checkpoints must bind their exact
assessments of the same ancestor cycle within the objective lineage. The new
checkpoint must preserve that ancestor's current, dependency-fresh assessment.

For example, this is a partial edit to a full reassessment payload, not a new
plan or standalone command. Replace all placeholders with actual records and
copy the complete original edge's evidence list unchanged:

```json
{
  "inheritance_refresh": {
    "plan_digest": "ORIGINAL_DESCENDANT_PLAN_DIGEST",
    "bindings": [
      {
        "previous_checkpoint_id": "parent-checkpoint-original",
        "previous_assessment_id": "parent-assessment-original",
        "checkpoint_id": "parent-checkpoint-current",
        "assessment_id": "parent-assessment-current",
        "evidence": [
          {
            "kind": "result",
            "execution_id": "execution-parent",
            "output_id": "result",
            "artifact": {
              "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
              "path": "research/sources/objects/0000000000000000000000000000000000000000000000000000000000000000",
              "size": 15,
              "media_type": "application/json"
            },
            "locator": {"kind": "json", "pointer": "/result", "value": {"bound": 9}}
          }
        ],
        "assumptions": ["Every original edge assumption and every old/current parent assessment assumption."],
        "deduction": "Explain why these exact retained results still contribute under all current qualifications."
      }
    ]
  }
}
```

The binding and the new assessment's top-level `assumptions` must retain all
original edge assumptions and both parent assessments' assumptions. Preserve
the scientific meaning of added qualifications in the deduction and assessed
scope. Changed relied-on locators are rejected. For `validated_result`, the
replacement must remain validated and explicitly assess those exact result
references. For `failure` or assessed `unresolved`, its current assessment must
retain the exact evidence; numerical validity need not become passing. The
original `use` is retained automatically. A binding has no credit-override
field, and this operation cannot turn a failed branch into a validated result.
An unassessed checkpoint with a null assessment uses the separate unresolved
successor workflow above.

Refresh ancestors in dependency order, including assessed failure chains.
Historical evaluation checks the entire assessment, including later inherited
checkpoints, refresh bindings and development evidence. It collects explicit
inheritance freshness obligations and reports stale ancestry as nonvalidated
and incomplete, preserving the original payload and recomputing its evidence.
It never substitutes a saved report after an early dependency failure.
That report is not a current replacement. A new reassessment of recursively
stale ancestry still needs explicit bindings. Artifact corruption, missing or
invalid evidence and forged lineage remain errors; they are not converted to
dependency-staleness reports.

The original plan, admissions, executions, outputs, checkpoints and resource
counters are unchanged. Prior failure observations remain; reassessing an
observed signal appends it under the new assessment ID, preserving the ordinary
changed-evidence reopening requirements. The new assessment records the
explicit refresh in its dependency digest. Later changes to its relied-on evidence
or parent assessment require affected reassessment. Other stores retain their
existing preparation dependency checks. Normal revision CAS, request replay, fresh checkpoints and
independent readiness review remain required. A refresh never authorizes a run.

## Review, publication, and verification

```sh
exactory-research export --kind readiness
exactory-research export --kind manuscript --destination /new/independent/manuscript
```

Delivery copies the actual candidate/source/program/output bytes and an `inputs.json` manifest into a new directory: `--destination`, or without it `intermediate/reviews/packets/<kind>-r<revision>/` under the workspace root, where `<revision>` is the store revision at the export and a numeric suffix is added when that directory exists. `--destination` stays free so that a reviewer can run outside the workspace. Each artifact is copied from a verified private copy, the manifest is written last, and the directory is renamed into place only when it is complete, so a failure leaves no partial delivery. One exception: an object that a completion check sealed (see "Exploratory computation") is not copied; the packet keeps its hash, size and media type, with `sealed_path` in place of `path`. The manifest is a neutral packet: no revision labels, request identities, launcher tokens, author names, assessment history, prior scores or verdicts. A readiness packet carries the candidate, branches, plans, assessments, checkpoints, sources and synthesis sections that the six checks need. A branch of `review_inputs.branches` whose plan disclosed runs also carries `program_probes`: for each disclosed run its `program`, `inputs`, `interpreter`, `context`, `program_run` (the observed times, output hashes and read status), `outputs`, `stdout`, `stderr`, `exit_status`, `elapsed_seconds`, a `statement` of whether the outputs were read, and `admitted_runs`, which lists each admission of the plan with its `program_sha256`, `earlier_version` (true when the hashes differ) and a unified `program_diff` of the two programs when both are UTF-8 text.

A manuscript packet carries the exact PDF, abstract, bibliography and optional source archive. It derives a separate claims file for the reviewer. This file contains current claims, with `revised` metadata and `superseded` entries omitted. The evidence map and its source/result closure contain only those current claims. The packet also carries the selected field standards. The derived claims file has its own content hash and exists only in the delivery directory. The original ledger and bundle digest remain unchanged. Give this directory to the separate reviewer. A digest list alone is insufficient. Export does not certify comprehension or independence.

`manuscript-review` accepts one review per assessor per exact bundle; a second review by the same assessor on the same `bundle_digest` fails with `manuscript_review_duplicate`, so a rejection stands until the manuscript changes and a new bundle is prepared.

The original manuscript rubric JSON has exactly `summary`, `strengths`, `weaknesses`, `soundness`, `presentation`, `contribution`, `overall`, `decision` and `changes_for_maximum`; the three dimension scores use 1 through 4, overall uses 1 through 10, decision is `accept` or `reject`, and `changes_for_maximum` has exactly the keys `soundness`, `presentation` and `contribution`, each a list that is empty exactly when that score is 4 (`invalid_review` otherwise). A review recorded before 0.42.0 has the first eight fields and keeps its credit. `manuscript-review` adds the external assessor/bundle envelope without changing those original bytes. Changed PDF, abstract, bibliography, claims, optional source archive, current evidence, or review dependencies invalidate the current publication gate.

`manuscript-review` and `manuscript-prediction` also read the working files of the selected bundle: the `pdf`, `abstract`, `bibliography` and `claims` files, and `sources` when it is not null. A file whose bytes differ from the pinned bytes fails both with `publication_artifact_changed`, with the path in the details. `status` names every such file under `bundle_drift` while the bundle's measurement is incomplete (see "Status, projections, and recovery"). `restore --bundle DIGEST` writes the pinned files of a recorded bundle back to their paths. `DIGEST` is the bundle's `digest` as the pin returned it, 64 lowercase hexadecimal digits (`invalid_input` otherwise, and `publication_bundle_missing` when no recorded bundle has it). Any recorded bundle can be named, not only the selected one, and the selection does not change. The command checks every pinned file before it moves or writes any file. A pinned path that is absolute, contains `..`, or lies under `.exactory`, `.git` or `research/sources/objects` in any letter case is `unsafe_path`, as is a working path that is a symlink, a directory, or under a symlinked directory. A stored object that does not match its hash, a pinned object or an existing backup, is `artifact_corrupt`. Pinned paths that name one destination form one group: paths whose components are equal after NFC normalization and casefolding, and paths whose existing files are one inode. Two pinned files of one group with different bytes are `publication_artifact_invalid`, refused before any file moves. A group whose working files all equal the pinned bytes is listed under `unchanged` by its first path and is not touched. Every other group is restored in two steps. First, each existing working file of the group is moved to `.restore-backup-<32 hexadecimal digits>` in its own directory, and the moved bytes are stored in the artifact store as an `application/octet-stream` artifact, the backup. A file that changed between the check and the move stops the command with `publication_artifact_changed`. Second, after every move, the pinned bytes are written to a temporary file and linked at each path of the group. A missing file is created, with its missing parent directories. Between its move and its install a path is absent. A file that appeared at a path in the meantime stops the command with `publication_artifact_changed`. The pinned bytes never overwrite it. Several files are not one transaction. A stop leaves the files already installed in place, links each moved file whose path was not installed back to its path, and reports under the error details `restored` (the groups installed), `installed_paths` and `backups` (`{path, backup, backup_path}` of every moved file). The output is `{revision, bundle_id, bundle_digest, restored: [{path, artifact, backup, backup_path}], unchanged: [path, ...]}`. `artifact` is the pinned artifact reference. `backup` is the reference of the stored copy, `{sha256, path, size, media_type}` with `path` under `research/sources/objects/`, or null for a file that was missing. `backup_path` is the path of the moved file, which stays in the working tree, or null for a file that was missing. A group of several paths lists the other paths under `aliases` and every member under `backups`, each `{path, backup, backup_path}`. The command records nothing and the revision does not change. A backup is a content-addressed object that no record names, so keep the output to find it again. A process that still had the file open continues to write to the moved file, not to the stored copy, so read the moved file at `backup_path` for an edit that continued after the restore. After the restore, the file check of `manuscript-review` and `manuscript-prediction` passes for that bundle.

A bundle that `manuscript` pins since 0.65.0 carries `review_inputs_artifact` and `execution_observations_artifact`: each an `ArtifactRef` to the canonical JSON export of the readiness review inputs or of the execution observations, serialized as the harness digests JSON, so its SHA-256 names the value. The store writes each distinct export once, so a second pin of the same inputs adds no object, and the bundle record does not grow with the readiness packet. Before writing them, the pin checks both values against the store bounds as if the bundle held them inline. A bundle pinned by an earlier release keeps its inline `review_inputs` and `execution_observations`, its digest, its reviews and its receipts. Every reader (`export --kind manuscript`, `manuscript-review`, round packets, reviewer assignments and the rebuild of pinned readiness) resolves both shapes. A pinned object that is missing or corrupt fails the reader with `artifact_missing` or `artifact_corrupt`; no reader falls back to current readiness. Releases before 0.65.0 do not read the reference shape.

Under `lineage-v1` the manuscript gate also checks the pinned bibliography for every `lineage` and `classic` entry. It reads the same citation evidence as `citation_accounting`. A BibTeX entry cites the work when it carries the work's arXiv identifier in family form, its DOI or a DOI of a provider alias, as a whole identifier. A bibliography that cites one version cites the work. An entry also cites the work when its title field equals the work's title of two or more words; a one-word title is too common to stand as evidence. Another bibliography format cites the work when its text carries an identifier or contains the title. A work with no such evidence keeps `lineage_citation_missing` until the bibliography carries it. Since 0.43.0, a DOI that only starts another DOI is not evidence, and in a BibTeX bibliography a title inside a longer title is not evidence. The bibliography is read for citation evidence, not validated: undecodable bytes become replacement characters instead of a failure.

`exactory-draft package --output PATH [--bundle ID_OR_DIGEST] [--include PATH ...]` writes a ZIP of the code, data and records behind a pinned manuscript, from one authenticated snapshot, and changes nothing in the store. It runs in any directory of the workspace. Without `--bundle` it reads the selected bundle; `--bundle` names any recorded bundle by its id or its digest (`publication_bundle_missing` otherwise). The archive holds `README.txt`, one sentence that names the manifests; `claims.json`, the pinned claim registry; `claim-evidence.json`, the pinned evidence map; `package.json`; `data/INDEX.json`; `runs/`; `data/`; and `managed-record/`. For each execution that the evidence map cites, `runs/run-<admission id>/` holds the admitted program at its bound script path and each admitted input at its bound path, at the version of that run, with empty `logs/`, `results/` and `plots/` directories. `package.json` lists the run with its `execution_id`, `cycle_id`, `origin`, `command`, `admission_id`, `directory`, `argv` (the runtime path, the script and the bound input paths), `runtime`, `script`, `inputs`, `declared_outputs` and `outputs`, each output with its member `archive`, `data/execution-<execution id>/output-<output id>.<suffix>`. Imported history keeps its recorded outputs with a null `admission_id`, `directory` and `argv`. Every other stored object that the evidence map or a record references, followed through JSON objects, is `data/objects/<sha256>`, and `data/INDEX.json` maps each object path under `research/sources/objects/` to its member. `managed-record/<kind>/<id>.json` holds every record of the kinds `cycle`, `cycle_plan`, `development_scope`, `strategy_account`, `execution_admission`, `execution_binding`, `execution_prelaunch`, `execution_claim`, `execution`, `execution_observation`, `execution_outcome`, `cycle_assessment`, `checkpoint`, `development_selection`, `bundle_selection`, `readiness_review`, `manuscript_review`, `manuscript_prediction`, `contribution_analysis`, `round_decision`, `round_review`, `source_deferral`, `workspace_decision`, `publication_bundle`, `publication_selection`, `local_artifact`, `gate_override` and `review_series_stop`, with the recorded values unchanged, and `package.json` maps each kind and id to its member. Each `--include PATH` adds a workspace file, or every regular file under a directory, at its own relative path and lists it under `supplements` with its `sha256` and `size`; a symlink or another special file is `unsafe_path`. `package.json` is `{schema: 1, revision, bundle: {id, digest}, runs, records, supplements}`. The result is `{revision, bundle_digest, runs, artifacts, members, mechanical_only: true, path, size}`. The command refuses, and leaves no file at the destination: `package_destination_exists` for an existing file or link at `--output`; `package_member_conflict` for a supplement named `package.json` or `data/INDEX.json`, under `runs/`, or at the name of an archive member or directory; `package_binding_missing` for a cited managed execution without its admission and binding; `execution_identity_mismatch` for a binding whose paths or inputs differ from the admission; `result_evidence_mismatch` for claim evidence that names no recorded output; `publication_claims_missing` for a bundle without the claims file or the evidence map; `artifact_corrupt` for a stored object whose bytes differ from its hash. Each member is written from a verified private copy of its object, and the archive is published at `--output` only when it is complete. The same snapshot and bundle give the same member names, bytes and order; the container bytes of an archive built again can differ from an earlier archive of the same content, so compare two archives by their members. Pass the file to `exactory-draft deposit --sources`; a managed deposit requires it to equal the `sources` file that the reviewed bundle pinned, inside the workspace (`publication_artifact_mismatch` otherwise, and the deposit takes the direct path).

After an automatic pass in the workspace, `exactory-draft deposit`, `exactory submit` and `exactory verify` first need the user's recorded instruction, on the managed path and on the direct path alike ([Public writes after an automatic pass](research-workflow.md#public-writes-after-an-automatic-pass)). The command checks the instruction before its managed checks run, `submit` and `verify` with their exact action and `deposit` with the gate or any action-scoped override, and each transport checks its exact action before the write. A refusal with `gate_override_public_write` ends the command. It is not a `Managed record skipped` case, and no direct write follows it. On a managed path that saved an intent, a refusal after a claimed step leaves no pending remote step: the intent keeps a `local_authority_refusal` observation, and the same command resumes it. Record the instruction with `gate-override` in that workspace, then run the command again. The paragraphs below describe the paths without an automatic pass.

`exactory-draft deposit` runs from the root of a draft workspace, the directory that holds `.exactory/draft.json`, and its existing options remain available, including sandbox, production, source archive, publication confirmation, and new version. When the workspace's publication gate is ready, the round gate is ready, and the upload matches the exact reviewed bundle, the command saves a remote intent before external writes, freezes and verifies each file that it will send before its first remote write, and uploads those validated bytes as streams with their lengths; `exactory-draft reconcile INTENT_ID` reconciles an unknown outcome before continuing. Otherwise the command creates the record directly from the given files, writing `.exactory/deposit.json`. A store that refuses the managed record prints one line first, `Managed record skipped (<code>): <message>`, with ` Pending: <codes>.` appended when the refusal carries obligations. A workspace with no store, such as a draft workspace initialized before 0.38.0, takes the direct path with no line. A direct deposit prints `Deposition <id> is open on Zenodo: <url>` before the publish, so a lost publish response leaves a record to open rather than a deposit to repeat. In the two cases where the store keeps no record of the direct deposit, a store that refuses the write and a store file that does not open, the command writes `.exactory/deposit.json`, reports that it holds the only local copy, and exits nonzero after it prints the record. Production runs the offline citation check first; a failing check prints one `Citation report:` line and the deposit continues. Local and remote storage are not an atomic transaction.

`exactory submit` records a submission receipt when the current published production record matches the reviewed bundle; the receipt keeps Zenodo's concrete record DOI distinct from the concept DOI the server may return, and `exactory reconcile INTENT_ID` reads a later task without repeating the original POST. In any other directory or workspace the command posts the request directly; a store that refuses the receipt prints the same `Managed record skipped` line first, and a directory with no workspace, or a workspace with no store, posts with no line.

`exactory verify IDENTIFIER --file verdict.json` sends the verdict from any directory. A verification workspace that acquired the exact body, prepared the `verification` profile, and bound the task and the verdict also records the verdict receipt:

```sh
exactory task IDENTIFIER --bind --expected-revision 42 --request-id bind-task-001
exactory-research bind-verdict --file verdict-assessment.json \
  --expected-revision 43 --request-id assess-verdict-001
exactory verify IDENTIFIER --file original-verdict.json \
  --expected-revision 44 --request-id send-verdict-001
```

UUID, DOI, and arXiv routes all fetch the task-only target. The server supplies source/version identity but no original-body SHA, so the exact body is bound to the separately acquired local source pin and full reading. The verdict's stance, rationale, and cohort impact prediction remain separate. `bind-verdict` references the exact original outgoing JSON, including an explicit `supersedesVerdictId` when revising. `exactory task --bind` and `exactory-research bind-verdict` fail before a write on a wrong task version, body, profile, or current preparation, so the workspace binds only what it assessed. `exactory verify` records the verdict receipt when the same checks pass on the fetched task, and otherwise posts the verdict directly, with the same `Managed record skipped` line when a store refuses the receipt. No other verdict content is requested by this flow.

Under `sampled-v1` `bind-verdict` also checks the verdict body's prediction against the sample. The percentile and both band ends are integers from 1 to 100. It refuses with `prediction_mismatch` when the sample places no member, when the body states a percentile other than the sample estimate, when the body's band does not contain the sample band, or when more than 20 sampled members are unplaced and the body's band is no wider than the sample band. The refusal carries both the sample prediction and the claimed one.

The task endpoint may reveal only an own-verdict ID after a lost POST response; it cannot establish the exact body that was accepted. Such an intent remains pending even when that ID exists, and the managed path does not repeat the POST; `exactory verify` prints the `Managed record skipped` line and sends the verdict directly. A verification-level owner serializes the complete verdict decision and write, including distinct assessment IDs. Other verification targets retain independent owners. Known-success receipts and explicitly bound revisions remain supported. A stale task response cannot erase a locally confirmed prior verdict or permit a revision that omits the latest known predecessor. The server's `requestedByViewer` means the request opener, not the paper author, and supplies no authorship proof.

### Deposit for a math problem

`exactory-draft deposit --math-problem <problem-id>` deposits a write-up for a problem.
The command checks that the problem stands before the first Zenodo write.
The metadata includes `related_identifiers` with `identifier: "<EXACTORY_API_URL>/math/<problem-id>"` and `relation: "references"`.
A published production record attaches to the problem as a related paper.
Sandbox records stay unattached.
A production publish needs the user's approval and `--confirm-publish`, because a published DOI is permanent.

`exactory submit --math-problem <problem-id>` puts the paper among the problem's linked papers, marked "declared at submission".
Submission does not change the problem's status.

Before the publish request, the command writes `.exactory/problem-deposit.json` in the workspace root.
The journal records the problem, application origin, Zenodo environment, and operation.
On the managed path, it names the saved intent. On the direct path, it names the deposition and its page.
After publication, it records the DOI and marks publication settled before it attaches the record.
After attachment, it marks the journal attached.

If a moderator removed the record's link from the problem, attachment exits with code 3.
It leaves the journal settled and unattached. The record stays published.
HTTP 409 from `link-problem-paper` or `link-problem-github` means that the moderator removed that link.
Those commands change nothing and exit with code 3. The removed link cannot be attached again.
The deposit message includes:
`A moderator removed the link of record <doi> from problem <problem-id>. The record stays published as <doi>.`
It also gives the settled-journal removal rule below. Do not retry the removed link.

If attachment fails for another reason after publication, the record stays published and the journal keeps the pending attachment.
The command prints: `Resolve that cause. Then run exactory-draft attach-problem. It attaches the recorded record and publishes nothing.`
A publishing deposit for the same problem also retries that attachment and publishes nothing new.
It uses the recorded record even if the manuscript changed.
A publishing deposit for another problem stops until the attachment succeeds or the user discards its journal.

If the user decides to discard that attachment, the command gives this rule:
`If you decide not to attach the record, remove .exactory/problem-deposit.json.`
This rule applies to a settled, published record. It does not authorize removal of an uncertain publication.

If the publish response was lost, run the same deposit command again.
After the problem id check, the command settles the journal before it reads the manuscript or other deposit options.
It reads the recorded deposition back from Zenodo.
If Zenodo shows it published, the command records and attaches it.
If Zenodo shows it unpublished, the deposit command publishes that same deposition and creates no second record.

On the managed path, publication still needs the manuscript and reviews that the saved intent names.
Otherwise it stops with `readiness_required`: `The current manuscript and dual reviews must match the saved publication intent`.
Restore that manuscript and its reviews before the next deposit run.
If the user wants the changed manuscript published instead, stop and ask the user how to proceed.
No command abandons a recorded deposition.

The attachment uses the recorded application origin.
If `EXACTORY_API_URL` names another origin, the command stops before attachment and leaves the journal intact.
If the recorded origin is yours, set `EXACTORY_API_URL` to it before retrying.
Run one `deposit`, `attach-problem`, or `reconcile` at a time in a workspace.
An overlap stops with `remote_operation_owned` and sends nothing:
`Another exactory-draft run owns the deposits of this workspace. Wait for that run to end. Then run this command again.`

#### Read back a direct deposit before a new version

The deposit command settles an unsettled problem journal before direct-deposit recovery.
If the run reaches the direct-deposit path, `exactory-draft deposit --new-version` reads `.exactory/deposit.json` before its first request.
If that record has no DOI, it reads the recorded deposition back from Zenodo first.

| Read-back state | What the command does |
| --- | --- |
| Published deposition | Record its DOI and links, print the record, and stop with exit 0. A later `--new-version` run revises it. |
| New-version draft, identified by its recorded concept DOI | Continue that same draft. Create no second record. |
| Fresh draft recorded by a plain run | Stop before any write. Delete the draft with the Delete button on its Zenodo page, then run again. |
| Deleted deposition | Continue from the store's newest published direct deposit in the same environment. If none exists, stop with recovery options. |
| History cannot be read | Stop with the store error. Resolve that error before retrying. |

For a published read-back, the command prints:
`Deposition <id> of the earlier run is published. This run recorded its DOI. A second run with --new-version revises it.`
If no published history exists after deletion, the command offers a plain first deposit or the published version's New version button.
If the workspace has no store, the command says that it has no store to search and gives the same options.
A version made on Zenodo does not reach the workspace's record.

If a failure leaves a fresh draft without a problem journal, delete the draft before retrying.
The command says:
`A second run creates another draft. Before you run the command again, delete this draft with the Delete button on its page.`
Do not finish a draft by hand.
If the command cannot keep the local deposit record, its advice depends on the recorded state.
For a published record, the message includes:
`A second plain run publishes a second permanent record when no problem journal records this deposit.`
For an unpublished draft, it includes:
`A second plain run creates another draft when no problem journal records this deposit. Before a second plain run, delete this draft with the Delete button on its page.`
Read the named record and its Zenodo state before retrying.

### Attach a recorded problem deposit

Run `exactory-draft attach-problem` from the workspace root after an attachment failure or a lost publish response.
It reads `.exactory/problem-deposit.json` for the problem and operation, regardless of the current manuscript.
It never publishes.
If the journal is absent, it stops with:
`.exactory/problem-deposit.json does not exist here. Run attach-problem from the workspace root of a deposit made with --math-problem.`

| Journal state | What `attach-problem` does |
| --- | --- |
| Unsettled publication | Record a confirmed publication, then attach a production record. Leave an unpublished deposition or unclaimed managed operation for `deposit`. |
| Settled production record, attachment pending | Attach the recorded DOI. Read no Zenodo token and send no Zenodo request. |
| Attached record | Report that it is already attached. Change nothing. |
| Settled sandbox record | Report that only a published production record attaches. Change nothing. |
| Settled managed operation with no published record | Report that there is nothing to attach. Change nothing. |

If a direct deposition reads back unpublished, the command stops with:
`Deposition <id> is not published. Run exactory-draft deposit to publish it. That command publishes this deposition and creates no other.`
On the managed path, that stop is `publication_unconfirmed`:
`The record of this deposit is not published. Run exactory-draft deposit to finish it.`
If a managed operation never sent its publish request, `attach-problem` stops with:
`The recorded deposit never sent its publish request, so there is no record to attach. Run exactory-draft deposit to deposit the paper.`
The next deposit run releases that unclaimed journal before it starts a new deposit.
For a claimed publication, it keeps the operation bound to its recorded deposition.

If a moderator removed the link, `attach-problem` exits with code 3 and leaves the journal settled and unattached.
It reports that the record stays published and gives the same settled-journal removal rule.
The link cannot be attached again. Nothing changed.

`reconcile` uses the same workspace lock.
After it reconciles the journal's operation, a pending attachment still needs `attach-problem`.
Never publish the paper a second time by another route.

## Native math preparation

Math-solver is an orchestration over shared tools. Its gates preserve the contract,
openness, independent result review, requested formal verification and closure.
A user-specified proposition has no value gate; problem value belongs to
`math-conjecturer`. Native research requires no Grand Challenge record.

New native proposals use schema version 3 with the existing computation field.
For `role: "research"`, common preparation is optional and `foundation: null` is
valid. In the proposal delivery (`{proposal, inputs}`), optionally include
`review_workspace: "."` or a canonical ancestor such as `".."`. The first proposal
without a foundation records that stable common review-store association as an
append-only event; omitted workspace defaults to the existing foundation ancestor
or `.`. Observed shared review assignments use that store. Conflicting associations
and later foundations naming another workspace are refused. Existing objectives
continue deriving the common store from their foundation references.

Historical work and external checkpoints without a foundation record the default
`.` on the first fresh scientific mutation, including reviewed adoption and native plan or launch
intents. Read-only status and packet export remain free of native events.

To include an optional research foundation, or supply the required verification
foundation, export current complete common preparation before independent review:

```sh
exactory-research export --workspace /study --kind native --attack-root /study/attack \
  --destination /study/attack/research/native-inputs/review-001
```

The manifest returns `foundation` and native `inputs`. Add both to the proposal's existing reviewed delivery. The reference contains `schema_version`, `workspace`, `profile`, `revision`, `snapshot_digest`, `preparation_digest`, and `claim_binding`. Its workspace is `.` or an ancestor expressed with `..`; the delivery remains inside the attack root. The immutable native store imports the snapshot blob and actual source artifacts. Research validates the recorded snapshot and preparation digests, exact pinned sources, original-objective correspondence and observed review provenance without rerunning the historical preparation evaluator or requiring equality with current common preparation. Changed common preparation alone requires no research amendment; corrupted pinned bytes still refuse fresh work. Verification retains the current preparation checks and requires a reviewed amendment when those dependencies change.

Common author preparation can be retained for native research and an author's verification of the same full objective. The optional common research export still evaluates common preparation, including its Grand Challenge condition; research can use `foundation: null` instead. Verification using a research-profile foundation retains that profile's current preparation audit, including this condition, as required by its unchanged contract. Common external verification preparation supports only native `role: "verification"` and has no Grand Challenge condition. The latter additionally exports `--claim-binding binding.json` with this exact reviewed correspondence:

```json
{
  "root_claim_digest": "SHA256_OF_COMPLETE_NATIVE_ROOT_CLAIM",
  "claim_digest": "SHA256_OF_THIS_NATIVE_PROPOSAL_CLAIM",
  "evidence": [{
    "version_id": "EXACT_TARGET_WORK_ID",
    "source_id": "EXACT_TARGET_SOURCE_ID",
    "artifact": {
      "sha256": "SOURCE_ARTIFACT_SHA256",
      "path": "research/sources/objects/SOURCE_ARTIFACT_SHA256",
      "size": 15,
      "media_type": "text/plain"
    },
    "locator": {"kind": "span", "start": 0, "end": 15, "sha256": "012bf3924437baf5382a1783506c0eb7c5a1f9926a1f047ca4be7ed992bed9f9", "excerpt": "Source passage."}
  }],
  "reason": "Explain the exact paper-source correspondence and its limits."
}
```

Replace the illustrative IDs, hashes, and passage with the actual exact-target `Link`. Both native claim digests and the configured exact original target reading must match. This is a reviewed source correspondence, not automatic semantic entailment.

Historical proposal versions 1 and 2 and old terminal runs continue to replay under their original schemas. Research may continue without a foundation amendment. Verification without its required current foundation must use `search amend-foundation NODE --file FILE` with the ordinary revision/request arguments. Its input is `{subject, review, inputs}`. The subject is `{node_id, proposal_digest, previous_snapshot_digest, foundation, reason}`; the previous snapshot is null only when none was previously selected. The independent review uses the versioned native result-review schema, with subject and exact claim digests. The amendment is an append-only native event and cannot alter the claim, budget, accounts, or prior proof acceptance.

Complete retained run/journal reconciliation before an amendment. Recovery, pause, existing journal completion, historical audit, and proof/account replay remain available. Admission, planning/ranking, begin, run and token release preserve the pinned foundation evidence audits described above; verification additionally checks current preparation. At final launch the common review-store read transaction and native writer lock remain held through the token write and flush. Common mutations cannot commit during that interval; both locks are released before waiting for the worker or reconciling results. Common mutations never acquire the native lock. Source-byte checks and the receiver's check before each command remain required.

The novelty study uses shared search/read tools for the exact statement and each
neighbouring formulation, recording each query's date, depth and top-k versus
complete coverage. New acceptance requires native result review version 2 with
`findings.openness`: `{outcome, statement_digest, evidence, searched_at, coverage,
reason, minimum?}`. Outcomes are `supported_open`, `already_solved` (citing the settling
source), and `unresolved`. The exact claim digest binds the statement; nonempty
evidence digests cite the delivered novelty study and search/read records, the
date is `YYYY-MM-DD`, and coverage and reason are nonempty text. Every decisive
prior or settling source must be fully read; an unavailable decisive source stays
a gap and keeps the finding unresolved. Material new settling evidence must be
recorded as a native checkpoint with exact source/search/read artifacts and
reassessed in a new observed final review that includes them. `accept` may set `claims_openness: true`, requiring
`supported_open`; otherwise it defaults to false and `unresolved` may approve proof
support without claiming openness. For every math-solver objective, including a
request to prove or disprove, unresolved keeps the openness milestone and root
closure pending. Fresh `complete` requires a version 2 closure subject and result
review with `supported_open` or `already_solved`. A prior observed packet omitting
newly recorded material evidence cannot authorize closure. Already solved preserves
requested formal verification, proof policy and every other deliverable. Recorded
version 1 acceptances and closures replay unchanged.
Fresh resolved findings require `minimum: {schema_version: 1, statement,
neighbours, searches: [{formulation, search_digest}], reading_digests,
settling_work_id}`. `statement` is the exact claim; each declared formulation
needs an actual common `literature_search` record with dated `captured_at` and
per-query `coverage` of kind `complete`, `top_k` or `bounded_unordered`, including
`inspected_count`. A search addresses a formulation when its `question` is the
formulation's exact text, character for character, or when that text is one of
its `queries`. Each formulation, the exact claim and each declared neighbour,
needs at least one bound search that addresses it. A material search is one that
addresses a formulation and has a `relevant`, `contradictory`,
`potentially_relevant`, `unresolved` or `duplicate` result, or nonempty `gaps`.
Every material search must be bound as well. Every relevant or contradictory
result needs fulltext coverage from the pinned common `reading` records.
Unresolved or potentially relevant hits, unavailable required source units and
active or stale deferrals remain gaps. `already_solved` binds its fully read
settling result via `settling_work_id`; `supported_open` uses null. All
search/reading hashes must be in both the finding's `evidence` and the observed
packet. Export the declaration in the optional `openness` field of
`search review-packet --spec FILE --json`; the reviewer echoes it as
`findings.openness.minimum`. Before it writes the packet, the export checks the
declaration against the common store, in this order: the minimum's schema
(`invalid_record`); `statement` equal to the statement of the claim that
`claim_digest` names (`openness_statement_mismatch`); every material search of
the declared formulations bound (`openness_search_stale`, details
`search_ids`); every entry of `reading_digests` the digest of a `reading` record
(`openness_evidence_mismatch`, details `reading_digests`); each binding, in the
order of `searches`, with a `search_digest` that names a `literature_search`
record (`openness_search_missing`, details `formulation` and `search_digest`),
a formulation that is the claim or a declared neighbour
(`openness_search_missing`, details `formulation`, `search_id` and
`search_digest`) and a search that addresses it (`openness_statement_mismatch`,
the same details); and every formulation bound (`openness_search_missing`,
details `formulations`). When `claim_digest` names no claim of the subject or
the native state, the export refuses with `claim_mismatch` before these checks.
A refused export writes no evidence object. `accept` and `complete` make the
same checks again on the returned minimum, with the evidence checks. The
exporter delivers records and captured source bytes from the associated common
workspace. An object that a completion check sealed is described without its
bytes. The exact schemas and `openness_*` refusal codes are in the math-solver
`SEARCH.md`.
Retain earlier uncertain hits and their explicit later `resolved` judgments.
`out_of_scope` removes the uncertain hit's reading obligation. A `duplicate`
needs `minimum.duplicate_resolutions: [{search_digest, work_id, canonical_work_id}]`;
the canonical source must be `relevant` or `contradictory` in that same search
and fully read. Decisive judgments retain their reading obligations. A missing
canonical binding or reading leaves `openness_reading_gap`.
A new native checkpoint after version 2 closure reopens final openness and root
closure review, retaining accepted proofs and the earlier closure record.

Exploratory `exactory-lab probe` runs in the common workspace under the common budget
and exploratory provenance, outside managed math execution, with no native credit.
Any computation whose result a recorded strategy decision, proof, finite check,
counterexample or acceptance relies on requires native admission, accounts,
interpretation and review even when run in the common workspace. Every finite
computation inside the controller, including diagnostics, keeps this contract.
The cash-out inventory lists every used probe's id, question and elapsed time.
Native imports retain original and canonical identities and resolve common probe
origins again at fresh dependency and strategy commitments. Renaming output,
nesting a digest in JSON, importing a canonical `blob`, or labelling an artifact
`proof` in an `analytical` manifest cannot grant exploratory credit
(`exploratory_evidence`). A probe-derived certificate or Lean proof needs native
admission, its actual verification and candidate interpretation. Historical
accepted imports replay unchanged.
Normalized review-packet JSON references, including native problem snapshots,
carry `raw_identities: [ArtifactRef]`.
Export first appends checked `native_import_identity` records in the associated
common store: `{schema_version: 1, canonical_digest, raw: ArtifactRef}`. Imports
and fresh audits verify the original bytes against their canonical digest and
consult the aliases before resolving shared probe origins, including origins
recorded later. Missing original bytes require an original-artifact export.
The shared resolver also checks these aliases when recording execution outputs
and looking up result evidence. An execution's `probe_origins` binds `sha256` to
the referenced output; a normalized output also retains `raw_sha256` for the
original probe bytes. Fresh result lookups discover later probe origins without
rewriting the execution. Only an authenticated managed launcher observation can
establish that a matching output was independently rerun under its admission.
Packet assessment and historical replay publish no objects or aliases; recorded
packets remain readable.
`plan` validates complete `problem.json` and precondition inputs directly; neither
`plan` nor `begin` requires the local `study/problem.md` file. The reviewed proposal
study digests remain required. The [remaining order table](../skills/math-solver/SKILL.md#remaining-order-conditions)
justifies complete planning inputs, proposal study digests, strategy study
before moves, precondition scan and ranking, computation admission, move/pass
limits and closure as claim, execution-authority or proof-dependency conditions.

## Problem creation

A problem workspace uses the shared research store and the `research` profile.
`problem-init` writes `workspace/problem` with `{version: 1, orchestration:
"problem_creation", slug, area, field, authors, created, updated, research:
{store, profile: "research"}}`. The nonempty immutable `authors` list governs
reviewer independence. `area` is a nonempty `math.*` subject class; `field`
describes the field and question explored for the bar reviewers. The optional
`start` is `{kind, description}`, two nonempty texts that record the kind of
start as an open value: `field_question`, `phenomenon` (a phenomenon that
appears in several places), `thought_experiment`, or another kind in the
author's words. Another shape is `invalid_input`. The marker holds `start` only
when the payload gives it, so a marker without it keeps the bytes of 0.82.0 and
reads as "not recorded"; full `status` shows it under `problem.start`. When
configuration is absent, initialization adopts the constitution and sets a null
target. Study, draft and research-intent markers conflict with this marker
(`orchestration_conflict`) in either direction, and so does a research target
already recorded in the configuration. `intent` is therefore refused in a
problem record; the Ideate commands that run there are `forming` and
`form-outcome` ([Forming records](#forming-records)) and `forming-import`
([Imported forming history](#imported-forming-history)). The marker holds the
one subject class of the record. A record names the further areas that its
statements concern with `problem-area` ([Further areas](#further-areas)), and
`problem-report` records how its exploration ended
([The report of an exploration](#the-report-of-an-exploration)).

| Claim | Mechanical minimum | Independent assessment | Currency |
| --- | --- | --- | --- |
| Problem: precisely stated, open, worth solving | A slate that selects the statement's candidate, exact statement and criteria, current unresolved attempt, current supported openness, evidence-bound value with the accounts of the statement, rubric self-assessment and projected citations | Two observed bar reviews and two observed result reviews that grade every rubric item, A7 included, and meet the rubric thresholds after focused adjudication | The explicit `problem_evidence_digest` binds the selected claim, attempt, accepted results, assessment and decisive evidence. |

Every mutation below uses `--file FILE --expected-revision REVISION
--request-id ID`. Read the revision before a new mutation; an interrupted retry
retains its original identity and payload. These operations append immutable
records and move explicit selections. Each also accepts `--check`
([Inputs and receipts](#inputs-and-receipts)). In a record that holds a Claude or
Codex route, a check whose validation evaluates a route or an assignment state,
such as `review-assignment`, `value-review` or `problem-openness`, reads the
runtime of the installed reviewer adapter, a local read that a check permits
since 0.73.0, and is checked, not refused.

| Command | JSON payload | Records and behavior |
| --- | --- | --- |
| `problem-init` | `{slug, area, field, authors}` and the optional `start: {kind, description}` | Creates the orchestration marker and initial configuration. `start` records the kind of start as an open value (above). |
| `problem-area` | `{id, area, reason}` | `problem_area/<id>` names one further area of the record: a `math.*` subject class other than the record's own and other than the areas already recorded (`problem_area_duplicate`), with the reason for naming it. The record adds `recorded_revision`, and the marker keeps its bytes. The command runs while no slate assignment and no result assignment exists (`problem_area_new_record_required` otherwise). The bar and result packets then carry the areas, and their prompts carry the areas paragraph ([Further areas](#further-areas)). |
| `problem-candidate` | `{id, previous, statement, source, observation, neighbours, potential, notes, disposition}` | `problem_candidate/<id>` records a tentative question, adding `recorded_revision`. `previous` is null or an existing candidate id (`problem_candidate_missing`); a candidate that a later candidate names as `previous` is no longer current. `source` is `{kind, reference, evidence}`: kind `own_record`, `literature`, `computation`, `section`, `user`, `thought_experiment` or `phenomenon`, a nonempty reference and a possibly empty list of evidence references. A candidate of kind `phenomenon` was formed in the record from a phenomenon that appears in several places; its evidence cites at least one forming call as `{kind: "record", record_kind: "forming_call", id, digest}` (`problem_candidate_forming_missing` otherwise). A candidate of any kind can cite forming calls and form outcomes in the same way ([Forming records](#forming-records)). `potential` holds the provisional grades `{significance: {B1, B2}, priority}` with an optional `readiness` verdict (`invalid_rubric_grades`). `notes` is a string, possibly empty. `disposition` is `{status, reason, evidence_needed}` with status `open`, `selected`, `deferred` or `rejected` and `evidence_needed` text or null. It is the author's provisional disposition when the candidate is recorded; the slate's lists are the decision of record, may differ from it, and are the only dispositions the gate reads. |
| `forming` | `{id, question, move, inputs, amounts, dictionary, comparison_count, forms, probes, stopping_reason, performer, usage}` | `forming_call/<id>` records one forming call of the Ideate tool: the question, the move, the inputs, the amounts that the performer reports, every form that the call produced, the probes that it used, the stopping reason and the performer. It adds `recorded_revision` and `recorded_at`. The fields, the rules and the refusals are in [Forming records](#forming-records). |
| `form-outcome` | `{id, form_id, target, standing, disposition, lack_of_means, outcome, evidence, reason}` | `form_outcome/<id>` records how one form, or one expected relation or planned check of a form, ended, with its evidence. It adds `previous` (the id of the latest earlier outcome of the same item, or null), `refutation_basis`, `recorded_revision` and `recorded_at` ([Forming records](#forming-records)). |
| `forming-import` | `{id, source_workspace, source_revision, source_candidate_ids}` | `problem_forming_import/<id>` gives this record the forming history of another problem record whose candidates it takes up. The command reads the source at its current revision, keeps a verified snapshot of it in this record's artifact store, and records one manifest of that snapshot: every forming call, outcome, probe, plan, run and assessment of the source, with the searches, readings and solver snapshots that they cite, each with its origin, its original id, its digest, its original date and its standing. No source record becomes a record of this store, and the source is not written. A candidate, an outcome and an entry of the accounts cite an imported record as `{kind: "record", origin, record_kind, id, digest}` ([Imported forming history](#imported-forming-history)). |
| `problem-slate` | `{id, selected, deferred, rejected, bar_review_ids, review_ids, comparison, reason}` | `problem_slate/<id>` records the selection decision over every current candidate, adding the computed `candidate_ids`, `recorded_revision` and, in a record with a carry, `problem_objection_carry_digest`. The latest slate by recorded revision is current. |
| `problem-objections-carry` | `{id, source_workspace, source_revision}` | `problem_objection_carry/<id>` imports every material objection of another problem record's bar and slate reviews, with authenticated provenance and local evidence, and links this record to that source ([Carried objections](#carried-objections)). `source_workspace` is the absolute path of the source workspace and `source_revision` its current revision. The payload has no other field (`invalid_input` otherwise). A carry id with another payload is `record_conflict`, and the same request replays its receipt. `--check` with a reachable source validates the whole carry and writes nothing. Full `status` lists the carries under `problem_objection_carries`. |
| `problem-candidate-transfer` | `{id, candidate_id, destination_workspace, reason}` | `forming_transfer/<id>` records in this record, the source, that another record took up one of its candidates. `candidate_id` is a candidate that the latest slate selects, defers or rejects (`problem_candidate_transfer_not_on_slate`, with `candidate_id` and `slate_id`). `destination_workspace` is the absolute path of the workspace of the other record. A path that does not exist, or that cannot be resolved (for example a loop of symbolic links), is `invalid_input`, and a path that resolves to this workspace is `problem_candidate_transfer_self`. The record keeps the resolved path, and the harness does not open the workspace of the other record. `reason` is nonempty text. The record adds `recorded_revision`. It changes no gate, no obligation and no next action. The report of this record gives the candidate the ending `taken up in another record`, unless a later slate was recorded after the transfer ([The report of an exploration](#the-report-of-an-exploration)). An id that holds another payload is `record_conflict`, and the same request replays its receipt. Outside a problem record the command is refused with `orchestration_conflict`. Supports `--check`. |
| `problem-statement` | `{id, title, area, statement, resolution_criteria, neighbours, formal_statement, candidate_id}` and the optional `revises_statement_id` | `problem_statement/<id>` and `problem_selection/current = {statement_id}`. Statement and criteria are nonempty text with TeX mathematics. Write `statement` in the two parts of [math-conjecturer Formulation step 1](../skills/math-conjecturer/SKILL.md#formulation), with the optional `---` line and the details in the same field. Neighbours are formulation texts, possibly empty. Formal statement is optional, null or `{language, text}`. `candidate_id` is optional, null or the id of a current candidate that the statement formulates (`problem_candidate_missing`). The area must equal the immutable problem record's area (`problem_area_mismatch`); for another subject class, the rule of [math-conjecturer Exploration](../skills/math-conjecturer/SKILL.md#exploration) applies. Any other changed field requires a new statement id and moves the selection. `revises_statement_id` is optional: the id of the statement record that this one revises (`problem_statement_missing` when the store holds no statement record with that id). The harness reads these references to list the runs on the earlier texts of a statement beside the runs on its current text ([The accounts of a value record](#the-accounts-of-a-value-record)). The statement digest covers `statement` and `resolution_criteria`, so a revision that changes only the neighbours keeps the digest. A record without the field keeps the bytes of 0.82.0. |
| `problem-attempt` | `{id, statement_id, kind, solver, literature, outcomes, proportion}` | Appends `problem_attempt/<id>`, adding `recorded_revision`. Binds the current statement to a solver attempt delivered in this store or a critically assessed full-reading literature attempt. The latest record by recorded revision is current; a resolved attempt blocks posting as an open problem. |
| `problem-openness` | `{assignment_id}` | Records `problem_openness/<id>` from the observed native result review. Every supplied minimum must pass the shared closed-schema validation. `supported_open` advances the claim; `unresolved` records the gap without an evidence audit; `already_solved` redirects to the settling result and blocks posting as open. |
| `problem-value` | `{id, statement_id, accounts, motivation, current_state}` | `problem_value/<id>` holds the accounts of the statement and two ordered paragraph arrays. Each paragraph is `{text, evidence}` with the optional `field_fact`, and its nonempty evidence cites entries of the accounts. `accounts` is required (`problem_accounts_required` otherwise). The record adds `recorded_revision` and the dates that the harness derives. [The accounts of a value record](#the-accounts-of-a-value-record) gives the parts, the rules, the derived parts and the refusals. A value record that an earlier release recorded has no accounts and keeps its contract. |
| `problem-citation-metadata` | `{id, version_id, title, authors, year, evidence}` | Appends `problem_citation_metadata/<id>`, adding `provenance` and `recorded_revision`, for an existing `url:` work with no DOI and no arXiv identity among its id, its aliases and its metadata (`problem_citation_work_ineligible` otherwise) that a valid full reading of this store covers (`problem_value_reading_missing` otherwise). `title` is nonblank text, `authors` is a nonempty ordered array of nonblank names, and `year`, optional, is four ASCII digits; a record without a year is cited without one, with no fallback to the work's year. `evidence` is `{title: span, authors: [span, ...], year: span}`: one span for the title, one span per author in the same order, and a year span exactly when `year` is given. A span is `{kind, source_id, sha256, start, end}`. `kind` is `fulltext`, the extracted text of a `fulltext` capture of this exact version, or `response`, the captured response of a `source` record; `source_id` names that source record; `sha256` is the hash of the retained object, the `text.sha256` of the capture or the `response.sha256` of the source record; `start` and `end` are zero-based UTF-8 byte offsets into that object, end exclusive, with `0 <= start < end <= size`. The selected bytes must equal the UTF-8 encoding of the field value exactly (`problem_citation_span_mismatch` otherwise): no JSON escape is decoded, no whitespace is normalized and no fragments are combined, so a line-wrapped title in a PDF text needs another retained capture that holds the clean title. An offset inside a multibyte character, or an object that is not valid UTF-8, is `invalid_input`. Every object is resolved through the stored records and the artifact store, never through a caller path: an absent source record is `unknown_source`; a source whose response is not a complete capture, or a capture that is not available and extracted, is `source_pending`; a `sha256` that is not the response of that source, a full text that no capture of this version with that source holds, or a retained object that is missing is `problem_citation_evidence_unbound`; retained bytes that fail their integrity check are `artifact_corrupt`. The record keeps the resolved provenance of every span: the source record's identity fields and digest, the response hash and, for a full text, the capture digest with the text and original hashes. The latest record for a version by `(recorded_revision, id)` is selected and revalidated at every projection. A correction is a new id with the complete replacement payload; a changed payload under an existing id is `record_conflict`. The selected record supplies the citation text alone; the locator does not change. |
| `problem-citation-locator` | `{id, version_id, reading_id, original_sha256, url, source_id}` | Appends `citation_locator/<id>`, adding `reading_digest`, `source_digest` and `recorded_revision`: the HTTPS location of the exact original that an existing reading read. It supplies the locator of a read work that has no DOI, no arXiv id and no bundle HTTPS URL, for example a manuscript PDF in a GitHub repository, after those three in the locator order of `export --kind problem` (below); the citation text does not change. Every field is nonblank text. A payload that fails any of these conditions is refused with `problem_citation_locator_unbound`: `url` is an absolute `https` URL with a host, without a user name, a password, a fragment, whitespace or the port 0; `reading_id` names a reading of `version_id`; the bundle of that reading has `original_sha256`; `source_id` names a `source` record with this exact `url`, status `captured`, a complete response and a verified origin, whose response has the hash `original_sha256`; for a `url:` work, the text after `url:` equals `url`; a full-text capture of the work names this source and this original. The reading itself must still be valid (its own codes). Retained bytes that fail their integrity check are `artifact_corrupt`. The reading, its bundle and the work stay unchanged, and no new reading is needed. The latest record of a reading by `(recorded_revision, id)` is selected and revalidated at every projection; a selected record whose reading or source record changed after it was made is refused with `problem_citation_locator_unbound`. A correction is a new id; a changed payload under an existing id is `record_conflict`. |
| `problem-assessment` | `{id, statement_id, grades, items, primary}` | `problem_assessment/<id>` binds the rubric self-assessment to the current statement, adding `recorded_revision`. Each item has evidence and a note; `primary` names its significance ground and criterion. |
| `problem-formal-check` | `{evidence: {execution_id, output_id}, native_closure_digest}` | `problem_formal_check/<id>` binds a requested support check to an authenticated native completion in this store. Invalid bindings return `problem_formal_check_unbound`. |
| `problem-decision` | `{id, action, review_ids, bar_review_ids, reason}` | `problem_decision/<id>` binds the current evidence digest. Action is `post`, `revise` or `withdraw`; `post` requires the minimum and observed review coverage, otherwise `problem_minimum_unmet`. The other actions record the reason. Since 0.85.0 a `withdraw` decision also records what it withdraws: `statement_id`, the current statement or null, and `candidate_ids`, the candidate of that statement, or, while no statement is current, the candidates that the current slate selects, or an empty list. The report of an exploration reads these fields. A `withdraw` decision of an earlier release has neither field and keeps its bytes. |
| `problem-posted` | `{problem_id, export_directory}` | Rebuilds the approved export and compares every file byte for byte before recording `problem_post/<id>`. A changed file, even with a changed manifest hash, returns `problem_export_mismatch`. It requires the approved exact export. |
| `gate-override` | `{id, gate, instruction, reason, stop_code?, transition?: {from, to}, public_action?}` | Records `gate_override/<id>`: the user's instruction that passes one gate, with that gate's unmet obligations at the recording revision ([Stops and overrides](research-workflow.md#stops-and-overrides)). `gate` is a name that `gate` accepts (`gate_override_gate` otherwise). `instruction` is the path, relative to the workspace root and under `context/` without `.` or `..` parts, of a regular UTF-8 file that holds the user's own words: not a link, not empty, at most 64 KiB (`gate_override_instruction` otherwise). The record keeps its `path`, its `text` and its stored `artifact`. `reason` and `stop_code` are nonempty text. `transition` names one stage move of a study: `from` is the current stage and `to` the same or a later stage (`gate_override_transition` otherwise, also in a record without a study). `exactory-lab state set` then permits exactly that move. The store does not expire a recorded move; whether the user's words cover a later pass of it is decided by those words, not by the store. `public_action` is the exact `METHOD /path` of one public write of `exactory` or of Zenodo, the URL path without the origin, for example `POST /api/v1/reports` or `POST /api/deposit/depositions`: `POST`, `PUT`, `PATCH` or `DELETE`, one space and a path that starts with `/`, on one line (`gate_override_invalid` otherwise). The record keeps it, and `status` and `next` show it. The transports read it only after an automatic pass: an override with `public_action` covers that one write and none of the other writes of its gate, and an override without it covers the writes of its gate ([Public writes after an automatic pass](research-workflow.md#public-writes-after-an-automatic-pass)). With `gate` `post` the record binds the current statement as `statement_id` and `statement_digest`, the digest of the statement text and the resolution criteria (`problem_statement_missing` without a statement), and `export --kind problem-override` reads it. The record adds `authority: "user"`, `evidence: "reported_by_agent"`, `obligations: [{code, explanation}]` from the gate report, with the gate's own error as the one obligation when that report fails, and `recorded_revision`. No gate reads the record: every `gate` action, `manuscript`, `problem-posted` and the managed deposit report as before, and no review, score, acceptance or decision record is created. An id recorded with another payload is refused with `gate_override_conflict`; the same request replays its receipt. Supports `--check`. When the store refuses with `corrupt_state` or `storage_io`, the command without `--check` appends the override to `.exactory/unrecorded-overrides.jsonl` and returns `{recorded: false, path}` (`gate_override_storage` when the log cannot be written). That entry holds the instruction text and its SHA-256 and no obligations; it permits no move and is not imported into the store later. See Stops and overrides for the status views, the exports and the publication disclosure. |
| `gate-auto-pass` | `{stop_id}` | Records `gate_override/automatic:<digest>`, the automatic pass of a harness-defect stop that `review-series` recorded, and moves the study from `experiment` to `write` in the same mutation ([The automatic pass](research-workflow.md#the-automatic-pass)). `stop_id` is the id of a `review_series_stop` record, from the last line of `review-series`. The payload has no other field (`gate_override_invalid` otherwise): the command takes no gate, class, reason, scope or transition from the caller. `exactory-research example gate-auto-pass` prints the shape. The conditions of the pass, the code of each refusal and its next action are in [The automatic pass](research-workflow.md#the-automatic-pass); this row states what the command writes. Before the commit it writes `.exactory/automatic-passes/<digest>.json` with `recorded: false` (`gate_override_report` with `report_path` when it cannot; nothing is recorded then). The mutation `research.gate_auto_pass` appends the record with `authority: "harness"`, `evidence: "recorded_stop"`, `gate`, `transition`, `stop_id`, `stop_code`, `cited_stops`, `obligations: [{code, explanation}]`, `target_digest`, `recorded_revision`, `report_path` and `report_artifact`, and sets the study's `stage` to `write`, its `status` to `in_progress` and its `waiting` to null; the move records the draft workspace as `exactory-lab state set` does. After the commit it writes the report with `recorded: true` and renders the views (`gate_override_report` with `override_id` when that fails: the pass is recorded, and the same request restores them). A store refusal after the pending report leaves that file, and the error carries its `report_path`. The same request replays its receipt. A store that holds the record needs plugin 0.81.0 or later ([The automatic pass](research-workflow.md#the-automatic-pass)). No gate reads the record, and the stage check ignores it, so `exactory-lab state set` gains no move from it. No review, score, acceptance, decision, publication receipt or problem post is created. Supports `--check`, which runs every check on the snapshot and writes no report and no record. After the pass, every public write needs the user's recorded instruction ([Public writes after an automatic pass](research-workflow.md#public-writes-after-an-automatic-pass)). |
| `problem-post-external` | `{id, problem_id, statement_digest, reason, evidence, posted_at}` | Records a post made outside the harness as a `problem_post` with `gate` `bypassed` and no export: `problem_id` is the exactory.ai problem id (a UUID), `statement_digest` the digest of the store's current statement, `reason` a non-empty text, and `evidence` a non-empty text such as the owner's instruction, or an artifact reference, which is verified. `posted_at` is the time of the post on exactory.ai, for example the `createdAt` of the server's response. It must be a timestamp with an explicit timezone (ISO 8601, `Z` accepted), and the record keeps it exactly as given (fractional seconds and offsets keep their text). When that time is known, pass it as `posted_at`. When it is not known, omit `posted_at`. A post recorded without `posted_at` stores `posted_at` null, and `problem-post-time` does not accept it (it has `recorded_at`), so no later command can state its time. The record adds `recorded_at`, the recording time in UTC. The harness does not compare `posted_at` with the local clock, so a time in the future is accepted. For the display of older posts, see the paragraph on a posted problem, below. Refusals: `invalid_input` (the problem id is not a UUID, a field is empty, or `posted_at` is given and is not such a timestamp, `null` and a non-string included), `problem_statement_missing`, `problem_statement_mismatch` (with the current digest) and `problem_already_posted`. The same request replays its result. |
| `problem-post-time` | `{post_id, posted_at, evidence}` | Records a statement of the time of an older external post: a `problem_post` with `gate` `bypassed` and no `recorded_at`, which 0.73.0 to 0.73.2 recorded. The immutable record `problem_post_time/<post_id>` holds the payload fields, `recorded_at` (UTC) and `recorded_revision`. `post_id` must be a non-empty text, the id of the stored `problem_post`. `posted_at` must be a timestamp with an explicit timezone, as defined for `problem-post-external`, and the record keeps it exactly as given. `evidence` must be a non-empty text or an artifact reference, which is verified (`artifact_missing` and the other artifact errors when its bytes are not stored). The post is found by its id, also when another problem statement is now current, and nothing compares `posted_at` with the stored recording time. One statement is accepted per post. `status` shows the statement with the post (the paragraph on a posted problem, below). The output views export each statement at `problem/post-times`, and the post's own export keeps its bytes. The statement is not part of the problem evidence or its digest, the post gate, or the reconstruction of review requests. Refusals, after the field checks and in this order: `problem_post_missing` (no `problem_post` has that id, details `post_id`), `problem_post_time_inapplicable` (the post has a `gate` other than `bypassed`, or it has `recorded_at`, details `post_id` and `gate`) and `problem_post_time_already_recorded` (a statement exists for that post, details `post_id`). Outside a problem record the command is refused with `orchestration_conflict`, as `problem-post-external` is. It follows the normal `--file`, `--expected-revision`, `--request-id` and `--check` contract. The same request id with the same payload returns its original receipt before any of these checks, and with another payload it fails with `request_id_conflict`. |
| `problem-post-fact` | `{id, post_id, fact, source, date, bears_on}` | Records `problem_post_fact/<id>`: a fact about the place of a posted statement that arrived after its post, for example in a reply. `post_id` is the id of a stored `problem_post`, which `gate post` and `problem-posted` or `problem-post-external` recorded. The post is found by its id, also when another statement is now current (`problem_post_missing` otherwise). `fact` is nonempty text. `source` is a stored artifact descriptor `{path, sha256, size, media_type}`, which `exactory-research artifact` returns for a stored file, or an exact reading record of this store, `{kind: "record", record_kind: "reading", id, digest}`, at any depth. Another shape is `invalid_input`, missing bytes are `artifact_missing`, an unknown reading is `strategy_reference_missing`, and a digest that differs from the stored reading is `strategy_evidence_stale`. `date` is nonempty text: the day on which the fact became available, as the source gives it, or `unknown`. `bears_on` is `{value, description}`, two nonempty texts: what the fact bears on, as an open value such as `earlier_statement`, `alternative`, `relation` or `refutation`, with a description. The record adds `recorded_revision`. It needs no value record. It is not part of the problem evidence or its digest, of the post gate or of the reconstruction of review requests, so the stored post, the reviews and the gate result keep their bytes. `status` lists the fact under `after_post` with the code `problem_post_fact` and the fact as its `reason`, and the output views show it at `problem/post-facts`. The harness records the fact and does not review it. An id that holds another payload is `record_conflict`, and the same request replays its receipt. Outside a problem record the command is refused with `orchestration_conflict`. Supports `--check`. |
| `problem-report` | `{id, reopen_when}` | `problem_report/<id>` records how the exploration ended. `reopen_when` is nonempty text: the finding that would make the exploration worth reopening. The harness computes every other field from the records at the current revision: the start, every form with its outcome, the forming imports, the candidates with their endings, the statements with their publication and the classification of each post, the counts, what was spent, and one of six outcomes. The record keeps its `cutoff` and the digest of the records that it read. `status` and `next` show a due report and a stale report as advisories, which change no gate. The command refuses a due report while a form is open (`problem_report_forms_open`) ([The report of an exploration](#the-report-of-an-exploration)). |

An exploration records its candidates with `problem-candidate` and compares
them in a slate with `problem-slate`. The slate gives every current candidate
exactly one disposition: `selected` lists candidate ids, `deferred` is
`[{candidate_id, evidence_needed}]` and `rejected` is `[{candidate_id, reason}]`.
A missing, unknown, superseded or repeated candidate returns
`problem_slate_incomplete` with `{missing, extra}`. A selected candidate needs
potential grades with `max(B1, B2) >= 2` and readiness `pass`; otherwise
`problem_slate_candidate_below_threshold` names `candidate_id` and its
`{item, required, graded}` shortfalls. Defer such a candidate with the evidence
that would raise it. `comparison` is the nonempty comparison of the candidates.
A slate that selects no candidate is the no-post outcome and needs a nonempty
`reason` (`problem_slate_reason_missing`); with a selection, `reason` is a string
and can be empty. `bar_review_ids` names two observed problem bars from distinct
reviewers and slots. A missing or invalid pair returns `problem_bar_pending` with
`{bar_review_ids}`, a bar recorded on the version 1 template returns
`problem_bar_calibration_required` with `{review_id}`, a bar that was given
before a further area was recorded returns the same code with `{review_id,
area_ids}` ([Further areas](#further-areas)), a bar for another area or
field returns `review_bar_mismatch`, and a bar whose observed context no longer
qualifies returns the code of its first assignment obligation. Selection
authorizes further research expenditure; it establishes neither openness nor
significance.

A version 1 bar lacks the calibration of levels 2 and 3. Assign the bar again to
the same reviewer with an empty `context`: the assignment refreshes the version 1
bar on the current bar template, `exactory-problem-review-v5/bar`. It continues
that reviewer's bar session and slot, the version 1 bar stays in its history, and
the packet carries `problem_refresh` with `{assignment_id, previous_template_id,
template_id}`. The refresh packet also carries `historical_reviews` with the
version 1 bar, whose objections the refreshed bar addresses. The refreshed bar
gives an `objection_findings` entry for every material objection of the version 1
bar (`review_objection_history_incomplete`); until it is recorded, those
objections stay current. Cite the refreshed bars in the slate; the slate and
result reviews then continue the refreshed sessions, because only the latest bar
of each slot is current. A reviewer whose latest bar is on
`exactory-problem-review-v2/bar` or `exactory-problem-review-v5/bar` cannot be
assigned another bar (`review_resampling_forbidden`), unless its latest bar
carries `review_evidence_undelivered` (the refresh below).

A focused slate review is optional, with two exceptions. The first is a carry
that imported at least one objection ([Carried objections](#carried-objections)).
`review_ids` is then nonempty, and an empty list is refused with
`problem_slate_review_unverified`, which names the current `import_set_digest`
and no `review_id`. The second is a slate that selects a candidate formed in the
record: a candidate of the source kind `phenomenon`, or a candidate whose
`source.evidence` cites a `forming_call` or a `form_outcome` record. Such a slate
with an empty `review_ids` is refused with `problem_slate_review_required`;
obtain a slate review of the current candidates and bars and name it. A formed
candidate that the slate defers or rejects requires no review, and a slate of
other candidates behaves as in 0.82.0. `review_ids`
lists zero to two observed
problem slate reviews from distinct reviewers and slots. Each must assess exactly
the slate's bars, current candidates and imported objections and keep its observed
independence; otherwise `problem_slate_review_unverified` names `review_id`. Assign a slate
review with `review-assignment`, role `slate` and `dossier_id: null`, to one of
the two bar reviewers: it continues that reviewer's bar session and slot
(`review_bar_continuity`). The template is `exactory-problem-review-v5/slate` and
the subject is `{kind: "problem", statement_id: null}`. The packet carries the
area, field, source inventory, `bar_ids`, `bars`, the reviewer's own bars and
`candidates`: the id, record digest, statement, source kind and reference,
observation, neighbours and potential grades of every current candidate. Notes
and dispositions are withheld. The response names both packet bars in `bar_ids`,
leaves `work_items` empty, reports the five problem assurances as `not_assessed`
and adds `value.rubric_potential`, one `{significance: {B1, B2}, priority}` entry
for exactly each packet candidate. Each material objection of a slate review also
carries `candidate_ids`: the distinct packet candidates it concerns, or an empty
list for an objection against the slate as a whole. Any other shape returns
`invalid_value_review`.

The slate packet also carries `candidate_set_digest`, the digest of the sorted
`{id, digest}` of every current candidate, and a cited review must have assessed
the current set: the same candidate ids and the same digest, and the current bars.
In a record with imported objections, it must also have been assigned under the
current `import_set_digest` of its `problem_objection_carry` marker. After a
candidate is added or superseded, or after a bar is refreshed or replaced, assign
the slate again to the same reviewer with an empty `context`. The same holds
after a carry changes the imported set. The
assignment refreshes the completed slate review: it continues that reviewer's
session and slot, lists the current candidates and bars, carries the earlier slate
findings in `historical_reviews`, and adds `problem_refresh` with `{assignment_id,
previous_candidate_set_digest, candidate_set_digest, previous_bar_ids, bar_ids,
review_ids}`. The new response covers the earlier material objections in
`objection_findings`. While the candidate set and the bars are unchanged, another
slate assignment for that reviewer returns `review_resampling_forbidden`, unless
its latest slate review carries `review_evidence_undelivered`.

The material objections of observed slate reviews bind the post like the bars'
objections, whether or not the latest slate cites the review; `review_ids` names
the reviews that the selection rests on. A named candidate stays the same
candidate along its `previous` lineage, whatever a later version changes: an
objection concerns the statement that formulates a named candidate or one of its
later versions, and its projection names the current versions. An objection with
empty `candidate_ids` concerns the slate as a whole and every statement. Adding or
revising candidates and changing the bars erase no objection, and an objection
whose named lineages are all other candidates does not block the current
statement. A slate review recorded before objections named candidates (no
`candidate_set_digest` in its packet or no `candidate_ids`) is not counted. The
objections that concern the current statement join the result packets'
`material_objections`,
so each result review needs an `objection_findings` entry for them
(`review_objection_history_incomplete`), and they remain post obligations
(`research_objection_unresolved`) until both result reviews record an evidenced
resolution or a focused adjudication finds them not upheld. A refreshed slate
review that marks such an objection `continuing` or `contested` with a new
`reason` or new `evidence` states a new material version of it, with the same
candidates (see the material versions below). Adjudicate a
slate-review objection with the two bar review ids in `context.review_ids`, as
for a bar objection; the adjudicator packet's `assessments` add the slate reviews
that raised it.

An objection that `problem-objections-carry` imported from another record joins
the `material_objections` of every slate and result packet under its qualified
id. It does so whatever candidates it named in its source, and it binds the post
the same way. [Carried objections](#carried-objections) states its identity, its
versions and its clearance.

Formulate a selected candidate with `problem-statement` and its `candidate_id`.
The post gate requires a slate (`problem_slate_missing`) and a current statement
whose candidate the latest slate selects (`problem_candidate_unselected`, naming
`statement_id` and `candidate_id`). A statement recorded before the slate is a
preliminary formulation and is permitted. The latest slate must still cover
exactly the current candidates: a candidate recorded after it, or one that
supersedes a candidate it disposed of, gives `problem_slate_stale` with
`{slate_id, missing, extra}` until a new slate is recorded. In a record with
imported objections, a slate recorded before the carry gives `problem_slate_stale`
with `slate_id` and the `reason` `problem_objection_carry_changed`. So does a
slate recorded before a later carry changed the
imported set. The obligation ends when a new slate, cited to a focused slate
review of the current set, is recorded. A slate whose candidate set is stale
counts as no slate for the milestone and for `problem_no_post`. A slate that is
stale by the carry digest alone blocks readiness, but it still counts as the
slate for those two computations. A statement recorded
in a 0.53.0 store before candidates existed carries no `candidate_id`; formulate
it again under a new statement id with `candidate_id`, because re-selecting it
unchanged gives `record_conflict`.

The statement digest hashes `{statement, resolution_criteria}`. Declared
neighbours also bind openness currency: adding a neighbour requires searches for
that formulation even when the statement digest is unchanged. The openness
minimum uses this store's own searches, readings and deferrals. It requires a
dated search with bounded or complete coverage for the exact statement and each
declared neighbour, and full readings of all decisive prior or settling works.
A search addresses a formulation when its `question` is the formulation's exact
text, character for character, or when that text is one of its `queries`. The
statement's text is the whole `statement` field, with its `---` line and
details when present; a neighbour's text is the one declared in `neighbours`.
Each formulation needs at least one bound search that addresses it. A material
search is one that addresses a formulation and has a `relevant`,
`contradictory`, `potentially_relevant`, `unresolved` or `duplicate` result, or
nonempty `gaps`. Every material search must be bound as well. A new search
record can reuse the captured responses of an earlier search and carry the new
`question`, without a new capture.
An unavailable source remains a gap. Shared `openness_*` audit codes describe
missing, incomplete, stale or undelivered evidence.
`openness_statement_mismatch` marks a finding for another exact claim, a
minimum whose statement or neighbours are not the current ones, or a bound
search that does not address its formulation.

Prepare the native openness packet with:

```sh
exactory-research export --kind problem-openness --spec openness-spec.json
```

Without `--destination` the packet is written to
`intermediate/reviews/packets/<assignment id>/` under the workspace root, or to
`<assignment id>-2`, `-3`, ... when that directory exists; an assignment id that
is not a portable file name (a letter or digit, then up to 127 letters, digits,
`.`, `_` or `-`) is replaced by `id-` and the first 32 hex digits of its SHA-256.
The result names the directory under `destination`. `--destination DIR` places
the packet anywhere else, for example outside the workspace for an isolated
reviewer. The spec is `{assignment_id, reviewer_id, minimum: {searches:
[{formulation, search_id}], settling_work_id, duplicate_resolutions}}`. The
export pins the current statement, statement digest, native reviewer assignment
identity, search records and the captured bytes of required full readings.
Before it writes anything, the export resolves each `search_id` and refuses an
unknown one with `openness_search_missing` and the binding as details. Then it
builds the minimum from the current statement and the resolved searches and
checks it against the store, in this order: the minimum's schema
(`invalid_record`, for example a neighbour equal to the statement or a
malformed `duplicate_resolutions` entry); every material search of the
statement and the declared neighbours bound (`openness_search_stale`, details
`search_ids`); each binding, in the order of the spec, with a formulation that
is the statement or a declared neighbour (`openness_search_missing`, details
`formulation`, `search_id` and `search_digest`) and a search that addresses it
(`openness_statement_mismatch`, the same details); and every formulation bound
(`openness_search_missing`, details `formulations`). A refusal of these checks
writes nothing, so the same assignment id can be exported again. Use the
exported packet in the `context` of a `review-assignment` payload with that
assignment id, reviewer id, `role: "native_math"`, `dossier_id: null`, a verified
route and an author from the marker, then invoke `review-run`. Record the
observed result through `problem-openness`. For `supported_open` and
`already_solved` it makes the same checks again on the returned minimum, with
the evidence checks. A new unbound material search, changed bound
reading/search or missing current formulation makes the finding stale
(`problem_openness_stale`).

On a text route (`claude_cli_v1`, `codex_cli_v1`, `codex_cli_framed_v1`) the
packet's full-text bodies count against the route's input limit. The optional
`minimum.text_priority` lists version ids of read works, the decisive ones first.
While the input exceeds the limit, the route withholds the full-text bodies of
the listed versions from the end of the list, one at a time. It keeps every
reading record, with its notes and inspected excerpts, and every protected body
(required proof bytes, selected source texts and screening evidence), also when
the body is listed. A listed version needs a required full reading that the
packet pins and at least one extracted text of an available main full-text
capture of that version carried in the packet's evidence. Each listed version
expands, in list order, to every distinct such text digest in ascending SHA-256
order. A listed version with no such text is refused with
`openness_reading_gap` and details `{version_id}`; a repeated version is refused
with `invalid_input`. Without the
field the route withholds no full text by priority; the binary originals that a
text route never carries and the projection of an `openness_delivery` record
still apply, and a request that stays above the limit is refused by
`review-run` before an invocation exists, with `review_input_too_large` and
`phase: "dispatch_preflight"` and nothing recorded, on the Claude route and on
the Codex routes alike (the visual route counts its image wrappers in its
limit). Without `text_priority`, the
export estimates the packet and returns a `review_input_too_large` warning that
names each registered text route whose limit the estimate exceeds; the estimate
counts the packet and its UTF-8 evidence as JSON strings, so a delivery
projection or a framed route can send less. In a reading chain the list also
orders the full-text units, listed versions first, and the final turn withholds
delivered full texts from the end of the list first.

A reviewer reads every full text itself. An openness packet stays a single
turn only when its request, as the route fits it, holds every unit of the
packet (the full texts and the records described below) complete within the
input limit, and those units together are at most 120,000 characters (the
reading budget of one turn). In every other case the openness review reads the
packet in a reading chain of several turns: `review-assignment` records
`reading_chain: {first_assignment_id, turn}` on the assignment, which becomes
reading turn 1. A native result review of a math-solver objective whose packet
binds `scientific_context.openness_minimum` outside problem creation follows
the same rule with its own units and protocol, described after the problem
chain below. A native result review without an openness minimum keeps its
single turn.

New problem reading chains use `exactory-openness-reading-v5`. Every bound
search keeps its original digest and minimum binding. The first assignment
freezes `reading_record.search_delivery`, a manifest of the original searches,
their residual descriptors and their shared field descriptors. The exact
canonical values of `dispositions`, `queries`, `responses` and `scope` are
shared when two or more distinct bound search records have the same value.
Array order, Unicode, explicit nulls and absent fields remain distinct.
Other fields, including each search's identity, purpose, question and impact,
remain in its residual. An unaffected search retains its whole original body.

A reading unit has a kind and an identity. A `fulltext` unit is the extracted
full text of a read version, named by `version_id`. A `search_response` unit
is the captured response of a search, named by `source_id`. A `record` unit is
a record of the store that the packet carries as evidence, named by
`record_kind` (`literature_search`, `reading` or `problem_statement`) and
`record_id`. A full text or a captured response can also carry `text_sha256`,
the SHA-256 of its whole stored body. A problem-openness chain delivers the
full texts of the packet's readings, in `text_delivery_priority` order and
then by version id and text digest, and after them the packet's own records:
its bound search records in the order of `minimum.searches`, each once, its
reading records by record id, and the statement record. The full texts are the
extracted texts of the main full-text captures of each bound reading's version
that the exported packet carries as evidence, one unit per version and text
digest; a capture or reading recorded after the export does not enlarge the
chain. When the packet binds an `openness_delivery` record (an export with
`native_reviews`), the alternate main bodies that the record omits
(`scientific_context.openness_delivery.alternate_body_digests`) are not units;
the selected representation of each version is read. A record arrives as the
exact JSON text of
the artifact that the packet carries, so the unit is the same bytes that the
final turn and `problem-openness` see. The `search_response` kind is accepted
by the splitter, the checks and the chain verification. A problem-openness
chain delivers none, and the math chain below delivers the captured responses
of its bound searches. A unit of
at most 120,000 characters is delivered whole. A longer unit is cut into parts
by the rule of the excerpt rules above, and each part is saved as its own text
artifact; a part of a record or of a captured response is saved as plain text
(`text/plain; charset=utf-8`), because a fragment of a JSON body is not JSON.

For v5 the logical record catalog above remains the citation catalog. Physical
search delivery follows the full texts: shared field bodies in first-use order
in the minimum, then each search's residual or unchanged original body in
minimum order, then the other records.
A shared unit uses `record_kind: "search_shared_field"`, names its `field`,
and lists all original owning searches and formulations in `owners`. A residual
unit uses `record_kind: "search_residual"` and retains `record_id` and
`search_digest`. Its JSON envelope has disjoint inline `fields` and
`shared_fields`, a field-to-digest map. These bodies follow the same consecutive
part rule and share the 120,000-character reading budget. The reviewer must
assess every formulation separately, including formulations whose searches
contain identical field values. For excerpts and passage requests about shared
fields or residuals, use `record_kind: "literature_search"` and the owning
original search's `record_id`. The harness checks the text against the
canonical JSON of that original record and keeps the span's offsets and SHA-256
in the reading record. An identity of kind `search_shared_field` or
`search_residual` is refused with `invalid_reading_response`.

A reading turn is a fresh input without the earlier turns. Its packet holds
`reading_turn`: the subject digest, claim digest and reviewer of the native
packet, the statement record, the formulations (the statement and its
neighbours), `reading_plan` (`turn`, `delivered_now`, `delivered_before` and
`remaining`) and `reading_record`, the compact record of the earlier turns
described below. Its evidence is only the units of `delivered_now`. Each plan
entry names its unit by kind and identity, with `{part, parts, start, end,
characters}` for a part, where `start` and `end` are character offsets in the
whole body. An entry outside `delivered_now` carries the descriptor of its
artifact instead of a reference to it, so no earlier body is delivered again
by accident. A plan entry without `kind`, from a chain that 0.64.2 to 0.66.2
started, is a full text. A turn takes the longest run of the next units, at
least one, whose characters together are at most 120,000 and whose actual
fitted request delivers each of them complete within the input limit. When
that request does not fit, the turn withholds the notes of the earliest turns,
oldest first, as the final turn does below, and never a unit of
`delivered_now`. A unit that does not fit alone, even with every note
withheld, fails the assignment with `review_input_too_large`. The reviewer is
told that a unit or part delivered in a turn is not delivered in a later
reading turn, and that the final turn withholds bodies that reading turns
delivered when its input has no room for them, so it keeps what the judgment
needs as excerpts and in its latest notes. It answers with
`{schema_version: 1, stage: "reading", subject_digest, claim_digest, reviewer,
excerpts, notes, passage_requests}`: `notes` (at most 20,000 characters) says
what each delivered unit or part proves and leaves open and how it bears on
each formulation, `excerpts` are verbatim copies, and each passage request is
`{id, <identity>, anchor, characters}`. An excerpt or a passage request names
its unit by one identity form: `version_id` or `source_id`, each with an
optional `text_sha256`, or `record_kind` and `record_id`; mixed identity
fields are refused. The harness checks it against the exact bytes of the bound
unit, a full text against the whole stored text and a record against its JSON
text with the escapes inside its strings, under the checks and limits of the
excerpt rules of bar, slate and result sessions in
[Scientific contribution decisions](#scientific-contribution-decisions). A
`text_sha256` that differs from the bound body, or an identity that names no
bound unit, gives `unmatched` or `not_found` with the reason `no_stored_text`,
as there; an identity without `text_sha256` that names more than one bound
body gives the same statuses with the reason `ambiguous_identity`. Those
statuses belong to an accepted answer: an excerpt that matches no bound text
and a passage request that is not found or exceeds its allowance keep their
own entry in the record, and the turn stays valid. A malformed answer is
refused with `invalid_reading_response` (check `native.output`) as a whole: no
note, excerpt or passage request of it enters the chain, the saved output is an
attempt artifact only, and the ordinary retry rules apply. The refusal's
`details` name the failed field by a JSON Pointer into the answer, with
`request_id` for a passage request whose `id` is a valid string, the message of
the failed rule, and a measurement against its limit where the rule has one.
An anchor is measured after each character is put in NFKC form and each run of
white space becomes one space, so a first passage request whose anchor is
`Lemma 35. Let Wℓ,m` is refused with `path: "/passage_requests/0/anchor"`,
`normalized_characters: 18` and `minimum_characters: 20`.

The reading record is compact. It holds `protocol`, `turns`, `sources`,
`excerpts`, `passages` and `allowances`. `turns` lists the earlier turns with
their assignment, attempt and delivery fields and their `notes`, or
`notes_withheld`, a sentence naming the turn, when the notes are withheld from
this input. `sources` lists one descriptor per cited artifact with the unit
identities that name it. A verified excerpt or a delivered passage keeps its
identity, `start`, `end`, the SHA-256 of the span, `status`, `text` and, for
an excerpt, `purpose`; a passage also keeps the assignment and request id that
asked for it. An unmatched excerpt keeps its `reason`, and an entry over an
allowance keeps its `characters`. The locator, descriptor and note that every
entry repeated under 0.64.2 to 0.66.2 are gone. The entries keep their order
and every text that the allowance rules retained.

A v5 record also carries the frozen `search_delivery` manifest and derived
`search_receipts`. A receipt identifies the original search digest and every
component used to reconstruct it, with the delivering assignment, attempt and
whole-body or part span and digest. A bound search receives delivery credit
after authenticated, valid reading turns of the same chain deliver every
required body, whole or in every part. These bodies are its residual and
shared field bodies when it uses shared fields, or its original body
otherwise. The harness verifies the exact original canonical bytes and digest.
A saved artifact, manifest,
planned unit, reviewer assertion, excerpt or passage supplies no such credit.

The final turn is the native packet, its evidence and the reading record, on
its own prompt. A v5 final omits factored search bodies, including when
the route has room. Each keeps its original artifact descriptor, empty content
and `delivery: "search-rebuilt-from-reading-turns"`; the manifest and verified
receipts describe its complete original delivery. It never expands those
searches again. Bytes with an independent protected role, including required
proof, retain their existing final-input treatment even when their digest also
names a factored search.
The harness builds the final with every other body and every note and
measures the exact request that the route sends. While that request exceeds
the input limit, it withholds content in this order, one item at a time, and
stops at the first request that fits:

1. the full texts that reading turns of the chain delivered complete, from the
   end of `text_delivery_priority`;
2. the other full texts that reading turns delivered complete, largest first;
3. the captured responses that reading turns delivered complete as units,
   largest first;
4. the reading records that reading turns delivered complete, largest first;
5. the search records that reading turns delivered complete, largest first;
6. the notes of the earlier turns, oldest first, the newest last.

Within a kind, equal sizes go by identity and then by artifact SHA-256. A body
counts as delivered only when the actual authenticated requests of the reading
turns carried it whole or in all of its parts; a plan entry or a claimed
delivery does not count. In a v5 chain, a selected text of the unchanged
`openness_delivery` contract may also be withheld in this order when it is an
exact full-text reading unit, earlier authenticated reading turns delivered it
whole or in every part, and the chain retains its native packet, reviewer,
author and route. Its digest must have no other protected role: required proof,
screening evidence, an original, a figure, table, equation, supplement or
inspection asset. Eligibility uses complete metadata on both text-only and
other routes, including declared units' original and asset descriptors before
transport projection. When several full-text units have the same digest,
coverage keeps each unit's delivering turns. Final withholding and source
validation both select the first complete unit by identity for that digest.
A withheld selected text keeps its artifact descriptor with `content: ""` and
`delivery: "body-delivered-in-reading-turns"`, the label of every withheld body
below, and a note that names the turns that delivered it.
Source validation derives coverage again from authenticated requests; saved
receipts alone give no credit. Existing proof-container
containment still applies. Generic route fitting continues to protect selected
texts, and v4 and math chains retain their existing rules. A withheld body keeps
its artifact reference and encoding, with `content: ""`,
`delivery: "body-delivered-in-reading-turns"` and a note that names the turns
that delivered it. A withheld note is replaced by `notes_withheld` in this
input only; the observed outputs keep every note, and each later assignment
starts from the full notes again. The statement and its record, the
formulations, every excerpt, every passage, the source descriptors and the
allowances are never withheld. When even the fully reduced request does not
fit, `review-assignment` refuses with `review_input_too_large` before it
records the assignment. The details give `input_characters`,
`input_limit_characters` and `parts: {base_packet, notes, excerpts,
passages}`, the characters that the reduced request spends on each part; the
four parts add up to the input characters, and the message names all four.
What remains at that point is protected evidence, the statement, and the
excerpts and passages that the allowances let the chain keep. Re-exporting the
same records preserves their mandatory evidence. A new chain starts with empty
excerpt, passage and note lists, so its final turn carries only what its own
turns gather.

The mandatory final input is measured before the chain reads. Before the
first reading turn of a new chain writes its part artifacts, and before each
later turn is planned, the harness measures a lower bound of the final turn's
input in the route's own request representation: the native context, which no
turn changes, every evidence body that the final turn cannot withhold (the
statement record, the bodies that the proof, source and screening contracts
protect, and the required proof bytes), the excerpts and passages that the
chain has accumulated so far, the v5 search manifest and the final prompt; a body that the final
turn may withhold counts as empty. When that lower bound exceeds the route's
input limit, `review-assignment` refuses with `review_input_too_large` and
the details `phase: "final_preflight"`, `measurement_kind:
"mandatory_input_lower_bound"`, `input_characters`, `input_limit_characters`
and `mandatory_components`, one entry per component with its `kind`
(`evidence`, `final_prompt`, `native_context`, `excerpt`, `passage` or, in a
v5 chain, `search_manifest`), its `sha256`, its `size_bytes` and its
`characters`; an `evidence` entry also carries its `encoding` and `delivery`.
Nothing is written: no assignment, no
part artifact and no observation. The refusal means that the chain cannot
finish on that route with that mandatory material, whichever turns it reads,
and the components show which bodies to examine. Re-exporting the same records
preserves their mandatory evidence. A new chain starts with empty excerpt and
passage lists, so its initial measured lower bound can be smaller. The
measurement is made where the
representation gives a sound lower bound: `claude_cli_v1`, `codex_cli_v1`
and `codex_cli_framed_v1` without a text encoding. On the visual route, on
`codex_cli_framed_v1` with `scientific_context.review_text_encoding`, for a
packet of a `complete-visible-v1` source delivery and on a route without a
character limit the measurement is unknown, and the chain is not refused on
it. A chain that passes the measurement is not thereby known to finish: the
excerpts and passages that later turns add are counted again before each
later turn, and the final turn is fitted and refused as above. The projection
is a diagnostic; it is never stored, sent to a reviewer or counted as a
delivery.

Every successful `review-assignment` of a v5 chain returns
`result.diagnostics.final_input`, before the reviewer invocation. The report
stays outside the saved assignment and scientific packet. It includes the
route limit (`input_limit_characters`), the measured mandatory floor
(`input_characters`), and `headroom_upper_bound_characters`, the limit minus
that floor. `mandatory_components` also identifies the `search_manifest`.
`planned_removable_texts` lists each eligible selected text by its full-text
identity and artifact descriptor; `deliveries` lists its verified contributing
assignments, attempts and whole-body or part receipts, or is empty until
complete delivery. `remaining_allowances` gives the remaining excerpt and
passage content characters separately. `capacity_warning: true` means that
the headroom cannot hold both remaining content allowances, even before their
serialization overhead. Low headroom warns and does not refuse the assignment.
An unsupported representation retains `status: "unknown"`, with a null floor,
headroom and warning. A sound floor above the route limit still refuses.

For this prospective v5 measurement, the harness first projects the complete
source contract normally, then counts eligible selected texts as empty because
the chain must deliver them before reaching its final. That diagnostic
assumption neither authorizes withholding an unread text nor creates a
delivery receipt. The same projection is checked again before invocation.

Run a reading chain turn by turn:

1. Export the packet and assign it as usual, with `context: {native_packet}`.
   When the receipt shows `reading_chain`, the assignment is reading turn 1. Run
   it with `review-run`.
2. Run `review-series --assignment ID`
   ([Review chains and `review-series`](#review-chains-and-review-series)). It
   assigns the next turn with the same reviewer, route, author and packet, and
   `context: {native_packet, prior_assignment_id}` naming the previous turn. The
   previous turn must be ready and its answer a valid reading answer. The harness
   checks its excerpts, answers its passages and builds the next reading turn
   while units remain. The command runs each turn. When a turn's attempts
   all ended without a completed output, it runs the turn again, waits, stops
   or replaces the turn under the outcomes of a failed reviewer call (a
   replacement only in the cases that name one; a timeout stops with
   `review_timeout`). A replacement is the next turn assigned again with the
   same `prior_assignment_id`, and it replaces the failed turn, which can no
   longer be run or continued (`review_assignment_replaced`). A first turn
   that needs a replacement has no parent: the command ends with the handoff
   `native_review_attestation_required`, and the agent exports the packet
   again under a new assignment id (`export`, kind `problem-openness`) and
   starts a new chain.
3. After the last reading turn the next assignment is the final turn, built and
   fitted as described above. When its request does not fit after every
   withholding, `review-assignment` refuses with `review_input_too_large` and
   the four part sizes, and `review-series` ends with a `stop` of that code
   (exit status 3). A new export does not change them; report the sizes in
   the summary and continue the work that does not depend on this chain. A
   refusal at step 1 or step 2 with `phase: "final_preflight"` says the same
   of the mandatory material alone, before that turn is planned; report its
   `mandatory_components` in the same way.
4. The final turn returns the native result review, or a reading answer with at
   least one passage request while the passage allowance has characters left. A
   further final turn, assigned as in step 2, then delivers the passages. In a
   chain under `exactory-openness-reading-v4` or `exactory-openness-reading-v5` at
   least one of those requests must deliver characters, that is, its anchor is
   found in a bound body and its characters fit the remaining passage
   allowance. `review-run` records a reading answer in which no request
   delivers characters as the output refusal `invalid_reading_response`
   (`details.path` `/passage_requests`), the turn is not ready, and
   `review-assignment` does not continue from it; `review-series` runs the
   same turn once more (the `scientific_refused` outcome), and after a second
   refusal it ends with the handoff `review_slot_exhausted`, whose command for
   an openness chain is `export`, kind `problem-openness`: a new chain. A
   native result review that `problem-openness` does not accept is not run
   again; its plan is the handoff of step 5.
5. `review-series` records the final turn that returned the native result
   with `problem-openness` and its `assignment_id` (state `record`) when
   `problem-openness` accepts that output by its own checks: an approved
   version 2 native result with an openness finding, whatever the openness
   outcome. Any other result (for example the decision `revise` or `reject`,
   or an approved finding whose evidence or statement does not match) ends
   the command with the handoff `openness_review_not_approved` (exit status
   3), whose command is `export`, kind `problem-openness`; its `details` hold
   `assignment_id`, the reviewer's `decision` and `reason`, `kind` and
   `refusal`, the `code` and `message` of the `problem-openness` refusal.
   Address the reason and the refusal, then export the packet again under a
   new assignment id and start a new chain for a new finding.

`problem-openness` on a final turn keeps every check and also checks the chain:
each turn is ready, the reading record names the observed attempts of the
turns before the final turn, and every unit of the packet, each extracted full
text of the readings and each packet record, was delivered complete in the
actual recorded requests of the reading turns, whole or in all of its parts,
or whole in the final turn's own request. A part counts as delivered when a
request carried, complete, the exact bytes of that part of the unit's whole
body as the part rule cuts it. An excerpt, a passage, a descriptor, a withheld
body or a planned part is not a delivery. An earlier final turn that returned
passage requests keeps its notes and passages, and its entry lists the units
whose bodies its own request carried complete, but only the reading turns and
the terminal final turn's own request count as deliveries. The record adds
`reading_turns: [{assignment_id, attempt_id, delivered_units,
delivered_parts?}]` for every turn of the chain, the final turn included:
`delivered_units` lists the identities of the units whose whole body a turn
delivered, each `{kind, ...}` with the identity fields of its kind, and
`delivered_parts` the parts `{<identity>, part, parts, start, end,
characters}` when it delivered any. The openness assessment checks them
again. When a turn is no longer ready or its observed attempt changes, the
finding becomes `problem_openness_stale` with the cause
`review_context_unverified` or `openness_reading_incomplete`. A finding without
reading turns behaves as before.

For v5, verification also reconstructs every carried reading record from the
authenticated outputs, checks the frozen manifest and final search omissions,
and keeps the exact native packet, reviewer, author, route, ancestry, protocol
and canonical prompts of the first assignment. `reading_turns` retains physical
`delivered_units` and `delivered_parts` and adds `completed_searches` when a turn
first completes an original search. `problem-openness` and later assessments
derive this verified original-search coverage before auditing the unchanged
minimum. Result review builders still receive the complete original search rows.

A chain that 0.64.2 to 0.66.2 started (prompts `exactory-openness-reading-v1`
or `exactory-openness-reading-v2`) keeps its recorded turns, which stay
authenticated under their own protocol, and a finding recorded from it stays
current: its chain is verified under that protocol and gives the same
`reading_turns` entries, `{assignment_id, attempt_id, delivered,
delivered_parts?}`. Its next assignment continues under the current prompts
(`exactory-openness-reading-v4`) with the same native packet, first assignment
id, route, reviewer and author. The previous answer is validated under its own
protocol, and its excerpts and passages are checked by version id as before;
the carried record becomes the compact form without charging them again, and
its turns are listed again from the observed outputs of the chain. A chain
that an earlier release started under `exactory-openness-reading-v3` continues
in the same way: its next turn records `exactory-openness-reading-v4`, which
keeps the units, the shape and the identities of v3 and adds the passage rule
of a final turn (step 4 above). Each turn's output is checked under the
protocol of its own packet, so a turn built under v3 keeps v3's rules, and its
recorded turns rebuild byte for byte. The harness
does not schedule from the earlier plan. At every continuation it computes what remains
from the actual authenticated requests of the chain's reading turns: in the
order of the units, full texts and then records, it delivers next every unit
that no reading turn delivered complete, the unit whole or the parts of it
that no reading turn delivered. A chain whose full texts were all read, or
whose final turn returned passage requests, therefore reads its records next,
and the final turn follows. A final turn that returned the native result still
ends its chain.

A stored v4 chain continues as v4. Its prompts, requests, packets, observations,
source delivery rules and `reading_turns` remain unchanged. Only new problem
chains select v5; historical chains are never relabeled or factored midway.

A native result review of a math-solver objective reads its packet in the same
chain when the packet binds `scientific_context.openness_minimum` outside
problem creation: a checkpoint review or the closure review of `exactory-math
search`. Its protocol is `exactory-openness-reading-math-v1`, with its own
prompts. Its units come in three groups. First, the captured responses of the
bound searches (`search_response`, by `source_id` with `text_sha256`), in the
order of `minimum.searches` and then of each search record's `responses`, each
source once. Second, the extracted full text of the main capture of the
original that each bound reading's source bundle pins (`fulltext`, by
`version_id` with `text_sha256`), by version id and text digest. Third, the
bound search records in the order of the minimum and the reading records by
record id. The packet must carry every response body, text and record as
evidence. A missing one refuses the assignment with
`openness_reading_incomplete`, and a capture recorded after the export never
enlarges the chain. There is no statement record: `reading_turn.statement` is
the native subject, the checkpoint or the closure subject, and
`reading_turn.formulations` holds the statement and the neighbours of the
minimum. The turn packets, the reading record, the budget, the parts, the
excerpt and passage rules, the final turn and the measurement of the
mandatory final input before each turn are those of the problem chain.
The final turn withholds the full texts largest first, then the captured
responses, the reading records and the search records, each kind largest first,
and then the earliest notes. Two rules are added. The first is the passage rule
of a final turn, which a problem chain under `exactory-openness-reading-v4`
shares since 0.71.0 and a problem chain under `exactory-openness-reading-v5`
shares as well. A reading answer in a final turn must request at least one
passage that the harness delivers from a bound body within the remaining
allowance. An answer whose requests are all not found, name no single stored
body or exceed the allowance is refused with `invalid_reading_response`
(`details.path` `/passage_requests`), `review-assignment` does not continue
from it, and the same assignment runs again under a new `review-run`
invocation. The second is the verification of the chain at the controller, not
by `problem-openness`. `exactory-math search accept` and
`search complete` resolve the review's attestation, which names the first turn,
to the effective terminal turn: a turn that ended without output and was
replaced is history, and a second live continuation is
`review_source_continuation_fork`. They require every turn to keep the native
packet, route, reviewer, author, protocol and prompt of the first turn and to
carry the reading record that the observed outputs of the earlier turns give,
the final turn to withhold only bodies that reading turns delivered complete,
and the chain to end at its first final turn that returns the native result.
They store `reading_turns` on the acceptance or closure, and every later audit
of that record follows the chain again and compares them. The math-solver
`SEARCH.md` describes the receipts. A minimum whose units fit one turn stays a
single native turn.

Refusal codes of a reading chain:

- `review_input_too_large`: the first unit of a reading turn does not fit
  alone, even with every earlier note withheld, or the final turn does not fit
  after every withholding. The message says that the reading turn, or the
  final turn, has that many input characters after it withholds every body and
  note that it may withhold, above the route input limit, and names the base
  packet, the notes, the excerpts and the passages; the details give
  `input_characters`, `input_limit_characters` and `parts`. Before a turn is
  planned, the same code with `phase: "final_preflight"` says that the lower
  bound of the mandatory final input, described above, exceeds the route
  input limit; the details give `measurement_kind`, `input_characters`,
  `input_limit_characters` and `mandatory_components`.
- `invalid_reading_response`: a malformed reading answer, an excerpt or passage
  request with mixed identity fields, a reading answer in a final turn without
  a passage request, one after the passage allowance is used up, or, in a math
  chain or a problem chain under `exactory-openness-reading-v4` or
  `exactory-openness-reading-v5`, a reading answer in a final turn whose
  passage requests all deliver no characters. `review-run` records it as an
  output refusal, and `review-assignment` refuses to continue from it.
- `review_source_continuation_mismatch`: the next turn names another packet,
  reviewer, route or author, or a turn that is not part of a reading chain.
- `review_source_continuation_fork`: another turn already continues the named
  turn, and that turn has a completed output or has not run. At `search accept`
  or `search complete` of a math chain, and in every audit of the acceptance or
  closure, one turn has more than one live continuation. The controller reports
  it as the `cause` of `review_context_unverified`.
- `review_assignment_replaced`: the named turn ended without output and a later
  turn of the same parent replaced it; it can be neither run nor continued.
- `review_source_continuation_unverified`: the named turn has no observed,
  authenticated output in a verified context.
- `review_resampling_forbidden`: the named final turn returned the native result,
  which ends the chain.
- `review_context_unverified`: `problem-openness` finds a turn of the chain
  without an observed, authenticated output in a verified context. For a math
  chain, `search accept`, `search complete` and every audit of the acceptance
  or closure give it for any failed chain check, with `details.cause` set to
  the code of that check: `review_context_unverified` for a turn that is not
  ready, `review_source_continuation_fork`, `invalid_reading_response` or
  `openness_reading_incomplete`.
- `openness_reading_incomplete`: `problem-openness` names a turn without a
  native result, a unit of the packet that reached neither a reading turn nor
  the final turn's request complete (`details.units` lists each by kind and
  identity, with `missing_parts` for a unit delivered in parts, and
  `details.version_ids` lists the missing full texts as before), a chain with
  a missing turn, or a reading record that no longer names the observed turns.
  For a math chain the controller reports it as the `cause` of
  `review_context_unverified`, also for a packet that does not carry a bound
  response body, text or record, a chain that has not reached a final turn with
  the native result, a turn whose binding, protocol, prompt or reading record
  differs from what the chain gives, a final turn that withholds a body outside
  the delivered prefix, and a chain that continues after a native result.
  Recorded `reading_turns` that differ from the chain verified now are
  `openness_reading_incomplete` itself, raised by the controller.

When a local supporting proof is material, the spec also accepts optional
`native_reviews: [{attempt_id, assignment_id}]`. Each entry names the current
exact-statement solver attempt and an observed approving `native_math` result
output with `schema_version: 2` for a proof or reduction checkpoint of that
attempt. The export binds the
complete reviewed checkpoint, its claim, the unchanged output and the bytes of its
analytical dependency closure (every manifest with `verification: null`;
certificate and Lean evidence are refused). Those bytes must appear in the actual
requests of the supporting review and of the openness review; a descriptor or an
omitted body supplies none. The packet then binds an `openness_delivery` record
that selects one complete main representation per exact source version. This
reports an observed approving review; it asserts neither native acceptance nor
resolution of the original objective. A change of the attempt, the native
snapshot, the observed review or the proof bytes makes the finding stale.

For primary evidence used to screen a search hit, the spec also accepts optional
`source_evidence: [{source_id, reason, locator?}]`. Each source ID names an exact
current common acquisition record. The default delivers its complete captured
response, which does not assert access to a complete article. An optional span
locator `{kind: "span", start, end, sha256, excerpt?}` selects an exact substring
of the saved UTF-8 response using Unicode character offsets and its SHA-256.
The immutable source record and selected response bytes are supplied together.
The packet binds the actual acquisition provenance, including unverified
external-import origins, and explicitly grants no fulltext reading credit.
Registration and later currency assessment validate the source record, original
response, exact selection, and both artifacts in the authenticated reviewer
request. A missing or changed binding fails with
`openness_screening_evidence_mismatch`; an undelivered required artifact fails
with `openness_native_evidence_undelivered`. Existing reading obligations and
native proof evidence remain binding. Input fitting cannot omit the selected
screening evidence or its provenance to satisfy a route limit.

New screening-source exports carry
`scientific_context.review_text_encoding: "openness-verbatim-evidence-v1"`.
The separate `codex_cli_framed_v1` route renders their complete UTF-8 evidence
as visible numbered blocks with exact character lengths, byte sizes and SHA-256
bindings. The header retains every descriptor, omission and native context.
Before sending, the renderer reconstructs the exact original canonical message;
the adapter then compares the actual observed input against the complete framed
text. No source text is summarized or compressed. Source bodies that contain
delimiter-like text are parsed by their declared lengths. The canonical request
remains the authenticated stored request for ordinary evidence validation.
The route's operational probe puts both positive controls in one framed body.
Register and probe this new route separately. The existing Codex adapter and
its runtime fingerprint remain unchanged, preserving earlier observations.
The new runtime fingerprint binds both its adapter and its renderer. Framing
supports one canonical user packet without history; unmarked inputs retain
their existing serialization.

Record an attempt with `problem-attempt --file attempt.json
--expected-revision N --request-id ID`. `kind` is `solver` or `literature`;
exactly the matching binding is non-null (`invalid_input`). For a solver attempt,
`solver` is `{native_attempt_digest, applicability}` and `literature` is null.
`applicability` is nonempty text explaining how the native claim's `quantifiers`,
`assumption_ids` and `scope` match the statement and resolution criteria; missing
or blank text returns `invalid_input`. The math-solver
proposal sets `review_workspace` to the problem record's workspace, for example
`..` from `<workspace>/attack`, so that `search deliver-attempt` writes into this
store. Deliver with `exactory-math --attack-root attack search deliver-attempt
--file empty.json --expected-revision N --request-id deliver-attempt --json`,
where `empty.json` contains `{}` and N is the native controller revision.
It writes an immutable `native_attempt/<digest(summary)>` containing the contract,
state, obligations, checkpoints, acceptances, retreats, failed strategies, nodes,
totals and delivery provenance. The research harness reads only this common-store
record and its current pointer. Attempt delivery maintains the mutable record
`native_attempt_current/<digest({attack_root, objective_id})>` with
`{attack_root, objective_id, native_attempt_digest, recorded_revision}` through
the common prepared mutation. The pair `(attack_root, objective_id)` identifies
one succession; different attack roots remain independent even when their
objective ids coincide. Every delivery declares the current digest, including
an exact repeat of an earlier snapshot. Such a repeat moves the pointer without
adding or rewriting an immutable snapshot. Closure delivery writes no pointer.
Missing delivery or a missing pointer returns `problem_attempt_unbound` with
`{native_attempt_digest, expected_store}`. Its `contract.original_claim.statement`
must equal the current statement text exactly (`problem_attempt_statement_mismatch`).
Its scope must have `kind: "named"`; `case_ids` and `integer_interval` return
`problem_attempt_scope_restricted` with `{native_attempt_digest, scope}`.
Bind the native snapshot named by this succession's current pointer in this
store. Native delivery preserves its first common-store `recorded_revision`
outside the summary digest; record order does not determine succession.
Binding another snapshot returns `problem_attempt_superseded`
with `{native_attempt_digest, latest_native_attempt_digest}`. At the gate it is
`problem_attempt_stale`, also naming `attempt_id` and `statement_id`, with the
explanation "Record a new attempt bound to the latest delivered snapshot of this objective."

For a literature attempt, `solver` is null and `literature` is
`{readings: [{version_id, reading_id}], description}`: a nonempty list of this
store's full readings and a nonempty critical assessment of the substantive
attempt on the exact statement. The shared full-reading validator applies;
abstract, missing, deferred or incomplete readings give
`problem_value_reading_missing`. Readings of the current literature attempt
enter the same collection as value readings: their passages are delivered to
the result reviewers and checked for coverage, and their works are projected
as citations and exported with the problem, even when no value paragraph or
openness finding cites them.

`outcomes` is `{resolved, footholds, unresolved, obstructions}`. `resolved` is
boolean; an `invalidated` native proof status is unresolved. Otherwise a solver attempt is resolved when `state.proof_status` is `proved` or
`disproved`, the root obligation (`contract.root_obligation`) has status
`accepted`, or an accepted root acceptance has outcome `proof` or `counterexample`.
This includes accepted root support delivered before closure while proof status
is still `open`. A different `resolved` flag gives `problem_attempt_outcome_mismatch`.
A root reduction remains eligible foothold and result evidence.
`footholds` is an array of `{claim, evidence}`,
`unresolved` of `{description}`, and `obstructions` of `{claim, scope, evidence}`.
These arrays may be empty; their text fields are nonempty. Evidence is either
`{kind: "acceptance", acceptance_id}` for the solver binding or
`{kind: "reading", version_id, reading_id}` for a reading in the literature
binding. Solver footholds require accepted `proof` or `reduction` acceptances;
obstructions require accepted `obstruction` or `counterexample` acceptances on an
obligation other than `contract.root_obligation`. Invalid support returns
`problem_attempt_evidence_missing`, naming the acceptance or reading ids.
An unresolved inventory describes unfinished work and supplies no obstruction
or difficulty claim.

`proportion` is `{extension_reason}` with text or null. When
any delivered total exceeds the normal limits of 24 moves, 24 runs, one worker
or 300 timeout seconds, give a nonempty reason
(`problem_attempt_extension_unexplained`, with `{totals, exceeded}`, where
`exceeded` lists the names in moves, runs, workers, timeout_seconds order).
Totals preserve `moves`, `runs`, `elapsed_seconds` (null), `workers`,
`timeout_seconds` and `historical_usage`;
the harness infers no elapsed time or unknown historical usage. A new statement
id requires a new attempt record even when only the resolution criteria changed.
A resolved literature or solver attempt redirects to the established result
and blocks the open-problem post (`problem_attempt_resolved`). The gate examines
every attempt for the current statement: a literature resolution persists, and
each solver attempt uses its `(attack_root, objective_id)` pointer's snapshot to determine
resolution. A later unresolved literature attempt cannot erase that finding.
An `invalidated` latest native snapshot lifts that objective's resolution; a new
statement id starts a separate resolution history. The blocker names the latest
attempt among those establishing a resolution.

Value evidence is `{kind: "reading", version_id, reading_id}` for a full reading,
`{kind: "computation", execution_id, output_id, formal_check}`, or a result
reference for an accepted mathematical result. In a value record with accounts
these references stand in the entries of the accounts, and a paragraph cites
the entries ([The accounts of a value record](#the-accounts-of-a-value-record)).
A result reference has one of two shapes. `{kind: "result", attempt_id, acceptance_id}` cites the current solver
attempt on the current statement: the acceptance has status `accepted` and
outcome `proof`, `reduction` or `obstruction`; otherwise
`problem_value_result_missing` names `{attempt_id, acceptance_id}`.
`{kind: "result", native_attempt_digest, acceptance_id, purpose,
extension_reason}` cites any math-solver objective that was delivered into this
store, for example a bounded objective on a form, on a stronger form or on a
relation of the statement. It has exactly these fields (`invalid_input`
otherwise):

- `native_attempt_digest` names the snapshot that the current pointer of its
  `(attack_root, objective_id)` names. A digest that this store does not hold,
  or whose objective has no current pointer in this store, is
  `problem_result_unbound` with `{native_attempt_digest}`: deliver the objective
  with `search deliver-attempt` from a proposal whose `review_workspace` is this
  workspace, and cite the digest that the delivery returns. An earlier snapshot
  of the objective is `problem_result_superseded` with `{native_attempt_digest,
  latest_native_attempt_digest}`: cite the latest one.
- `acceptance_id` names an acceptance of that snapshot with status `accepted`
  and outcome `proof`, `reduction`, `obstruction` or `counterexample`; otherwise
  `problem_result_missing` names `{native_attempt_digest, acceptance_id}`.
- `purpose` is nonempty text: the form or the relation that the objective is
  for. It describes the citation and establishes no relation between the result
  and the current statement.
- `extension_reason` is text or null. When a delivered total of the objective
  exceeds a normal limit (24 moves, 24 runs, one worker or 300 timeout seconds),
  give a nonempty reason (`problem_result_extension_unexplained` with `{totals,
  exceeded}`).

Such an objective needs no named scope and no `problem-attempt` record, and its
contract claim need not be the current statement. Its result supports exactly
its accepted conclusion: a root proof or counterexample of the objective does
not resolve the current statement. The same reference is evidence of a form
outcome ([Forming records](#forming-records)). When the solver delivers a later
snapshot of the objective, a value record that cites the earlier one is stale
until a new value record cites the latest. For a value record with accounts the
gate lists one obligation, `problem_value_stale`, which names the later
snapshot among its `changes`. For a value record without accounts, which an
earlier release recorded, the gate lists `problem_result_superseded`.
Delivering the earlier snapshot again makes it current again.
Invalidated acceptances and hypotheses supply no result evidence. Abstract and
passage readings, availability qualifications and deferrals supply no value
evidence. A cited computation requires its admission, reconciled output and an
assessment that lists and validates that result, with its execution accounting
and resource obligations satisfied. Whole-objective obligations and obligations
about other results do not invalidate a supported paragraph. Probe-origin
outputs retain `exploratory_evidence` and cannot be promoted by citation; an
admitted computation keeps its admission whether or not a paragraph cites it.

The attempt projection gives `kind`, `outcomes`, `proportion`, the recorded
`literature` binding (null for a solver), and `acceptances`: each acceptance cited
by a solver foothold or obstruction, once in first citation order, as
`{acceptance_id, claim, outcome, standard}` with its checkpoint claim. Literature
attempts have an empty acceptance list. Solver evidence also gives
`applicability`, `contract`, `obligations`, `failed_strategies`, `retreats` and
`nodes` as delivered, alongside `state` and `totals`. Both the binding and the
reviewer evidence project only an attempt for the current statement. When the
latest attempt belongs to a previous statement they give `attempt: null` and no
result of that attempt, so `results` holds the cited results of delivered
objectives alone, or is empty; the gate still reports the stale attempt.

The value's recorded revision fixes the immutable assessment used for each cited
execution. A later assessment enters the claim through a new value record;
it does not silently replace an already reviewed assessment.

Computation support is empirical. It grants no proof, disproof or formal-check
acceptance. `formal_check` is null unless a formal check was requested, in which
case record `{claim, required_deliverables}` before verification. `claim` is the
exact native `{statement, quantifiers, assumption_ids, proof_policy, scope}`
object, with policy `certificate` or `lean-kernel`. In math-solver, run
`exactory-math search deliver-closure` after native completion. It writes
`native_closure/<closure digest>` into the associated common store, retaining
the contract, subject, review, audit and provenance. Repeating the same delivery
is a no-op; a completed reopened objective can deliver another immutable closure.
`problem-formal-check` checks the exact requested claim and policy, contract
digest, proof outcome, delivered required texts and authenticated audit bindings.
When one output has several requested support claims, each exact claim and its
required deliverables needs its own binding. A binding for another requested
claim on the same output supplies no formal-check credit.

Record the rubric self-assessment with `problem-assessment`. `grades` has exactly
`{admission: {A1, A2, A3, A4, A5, A6, A7}, significance: {B1, B2}, priority,
attempt, accessibility, calibration}`. Admission, attempt and calibration
verdicts are `pass` or `fail`; significance and priority levels are integers 0
to 3; accessibility is `E-F`, `E-A` or `E-N`. `items` has exactly `A1` through
`A7`, `B1`, `B2`, `priority`, `attempt`, `accessibility` and `calibration`, each
`{evidence: [refs], note}` with nonempty evidence and a nonempty text note.
References use the shared strategy evidence shapes: a held artifact descriptor
`{path, sha256, size, media_type}`, `{kind: "source", link}` for a full reading,
or `{kind: "record", record_kind, id, digest}` for an exact record. An absent
record or mismatched digest returns `problem_assessment_evidence_missing`
with `{item, record_kind, id}` at recording and projection. Matching mutable
records, including `configuration/research`, are accepted; after a record
changes, re-record the assessment with its current digest. A missing item or empty item
evidence returns `problem_assessment_evidence_missing` naming `item`; malformed
grades return `invalid_rubric_grades`. `primary` is `{ground: "B1"|"B2",
criterion: "V1"|...|"V8"}`; the named ground must have a nonzero grade
(`invalid_input`). The latest assessment by recorded revision enters the
evidence digest and the reviewer evidence as `{id, grades, items, primary}`.
`assessment_evidence` resolves each distinct current assessment reference in
item order, then reference order. A record reference becomes
`{kind: "record", record_kind, id, digest, record}` with the stored record's
content. If the record is absent or its digest differs, evidence projection
returns `problem_assessment_evidence_missing {item, record_kind, id}`.
A source reference becomes `{kind: "source", link, reading_ids}` naming this
store's full readings whose version or inspected link matches the reference;
none gives `problem_assessment_evidence_missing {item, link}`. These readings
join value readings for passage delivery, coverage, citations and export.
Artifact descriptors remain unchanged and their bytes are attached as packet
evidence. A `local_artifact` record reference also requires its artifact bytes
to reach both reviewers; an unattached SHA gives `problem_evidence_unassessed`.
The digest binding carries the same `assessment_evidence` list with
record contents omitted; both result packets must carry the resolved list
(`problem_evidence_unassessed`). A statement change requires a new assessment
for that statement.

Posting requires every admission item `pass`, `max(B1, B2) >= 2` (at least 3
for `E-A` or `E-N`), priority at least 2, attempt `pass` and calibration `pass`.
Grounds never add. The harness's self-assessment must meet these thresholds.
The attempt grade records a judgment; this operation creates no attempt record.
`assess_attempt` checks the latest assessment: a passing attempt grade on the
current statement without a current attempt gives `problem_rubric_attempt_unbound`
with `assessment_id` and `statement_id`. `assess_assessment` retains its rubric
threshold and statement currency checks.

The evidence binding adds `attempt: {id, digest, native_attempt_digest}` (null
when absent, with a null native digest for literature) and distinct cited
`results: [{attempt_id, acceptance_id, digest}]`, where each result digest hashes
the acceptance. A cited result of a delivered objective is bound as
`{native_attempt_digest, acceptance_id, purpose, extension_reason,
objective_claim, claim, digest}` and reaches the reviewers as
`{native_attempt_digest, acceptance_id, purpose, extension_reason,
objective_claim, claim, outcome, standard}`: `objective_claim` is the contract
claim of the objective and `claim` the checkpoint claim of the acceptance, so a
reviewer sees what the objective set out to show beside what was accepted. Two
references to one acceptance with different `purpose` or `extension_reason` are
two entries. A value record that cites no such result keeps the binding and the
projection of 0.82.0. Reviewer evidence adds `attempt` with its `kind`,
`outcomes`, `proportion` and `literature` binding (`{description, readings}` as
recorded for literature, null for a solver), plus native `state` and `totals`
for a solver.
Its `acceptances` array carries `{acceptance_id, claim, outcome, standard}` for
every acceptance cited by a solver foothold or obstruction, once in order of
first citation, with the claim taken from its checkpoint; the array is empty
for literature attempts. The current literature attempt's full readings appear
in the reviewer readings and passages, coverage check and projected citations.
Reviewer evidence also includes
`results: [{attempt_id, acceptance_id, claim, outcome, standard}]`, taking the
claim from the acceptance's checkpoint. Reviewer evidence carries the openness
finding as recorded, `{id, outcome, statement_digest, reason, coverage,
searched_at, evidence, minimum}`, and `openness_searches`: for every search that
its minimum binds, `{formulation, search_digest, search, sources}` with the search
record (its queries, coverage and the disposition of every found work) and its
captured source records, whose response bytes are attached to the packet. The
evidence binding adds `openness_searches: [{formulation, search_digest,
source_digests}]`. The post gate checks the coverage of each problem result on
the actual request that its reviewer received, in the observed packets whose
`problem_evidence_digest` is the current evidence digest. For each of the
twelve evidence keys `claim`, `openness`, `openness_searches`, `value`,
`assessment`, `assessment_evidence`, `attempt`, `results`, `citations`,
`readings`, `computations` and `formal_checks`, at least one of those packets
must carry the key with its current expected value, including a null or empty
value; a different packet can satisfy each key. When the current value record
holds accounts, the expected `value` carries the accounts with
`derived_accounts`, and `account_records` is a thirteenth key
([The accounts of a value record](#the-accounts-of-a-value-record)). The same
request must deliver every reading of the evidence and attach the exact bytes of every artifact of
the assessment evidence, including the artifact of a `local_artifact` record
reference, of the computations and of the formal checks. A shortfall returns
`problem_evidence_unassessed` for that review with the keys that are absent or
differ in `missing`, the ids of the undelivered readings in `reading_ids` and
the digests of the unattached artifacts in `artifact_digests`. The packets are
decoded from the request of the exact authenticated attempt that produced the
review; when that delivery cannot be authenticated or decoded, the review
returns `problem_evidence_unassessed` with the refusal code in `issues`, and no
saved packet is read in its place. Coverage of the
bound searches rests on their search records: a text route that withholds the
bodies of their captured responses at its input limit (reduction step 6) leaves
the coverage intact, and a route that admits those bodies delivers them.

For a result review whose session delivered the problem evidence in parts (a
series, [Problem evidence in parts](#problem-evidence-in-parts)), the check
reads the actual sent packets of the series instead of one request. A packet
counts when it is the sent packet of the selected authenticated attempt of a
valid turn of the series: the same series id, the current evidence digest, the
same reviewer, route, author, slot and session, a part response that passed its
check, every owned body attached complete, and a turn number and
`delivered_before` that continue the earlier counted turns. The final turn's
`delivered_before` must name exactly those turns with their selected attempts
and their ranges; otherwise no turn of the series counts. The check covers the
twelve keys above, `passages` and `delivery`. A frame key must equal its
expected value in a counted packet. Each item of a split key counts when an
equal item reached the reviewer under the same index in a counted packet, so an
item that the evidence lists twice is delivered under each index. The inspected
ranges of a reading are covered by the union of the inline passages of the
counted packets, and an artifact by its complete bytes in a counted packet. A
shortfall returns `problem_evidence_unassessed` as above. This rule guarantees
that the reviewer received the current value of every item in an authenticated
turn of the same session. It does not guarantee what the model call that wrote
the review held: that call holds the items of the earlier turns only through
the reviewer's own notes, its verified excerpts and the earlier packets that
the replayed history still holds, and reduction step 8 replaces a replayed
packet by a digest stub while it keeps the reviewer's response of that turn.
The responses of the current series stay verbatim. A part response or an
earlier final response of a completed earlier series can be a placeholder of
the reduced history ([Problem evidence in parts](#problem-evidence-in-parts)).
Coverage still comes from the authenticated actual turns of the series, not
from the replayed history.
This applies only to a session that is a v1 series: a session became a v1
series when a first packet did not fit one input before 0.72.0, and after a
changed evidence digest or a refresh the request is measured again under the
current entry rules ([Reviews in fresh parts](#reviews-in-fresh-parts)). A
session that is not a series delivers the whole evidence in one request as
before. `value-review` and the
`review_evidence_undelivered` audit read the authenticated actual packets of
the series for a review of a series, so the review can cite a record or a
passage that a counted turn delivered; a span inside an inline passage of a
part turn is a delivered bounded span and gives no whole-artifact credit.

For a result review delivered as a parts v2 series
([Reviews in fresh parts](#reviews-in-fresh-parts)), the check reads the
authenticated read turns of the series. The review is credited only when
`verify_series` reports the series complete and the actual packets of its
read turns, under the current evidence digest, carry every item of the six
split keys (`openness_searches`, `assessment_evidence`, `readings`,
`passages`, `computations`, `formal_checks`), whole or as item segments that
cover it; equal the expected value of every frame key (`claim`, `openness`,
`value`, `assessment`, `attempt`, `results`, `citations`, and `delivery` when
the evidence has it); and attach every artifact body of the assessment
evidence, the computations and the formal checks, complete or in segments
that cover it. A shortfall returns `problem_evidence_unassessed` with
`missing`, `artifact_digests` and the series' `issues`. The review cites what
any turn of its series delivered, and a body delivered in segments is citable
through them.

For a staged review ([Staged problem reviews](#staged-problem-reviews)), the
check reads the accepted task graph of the epoch instead of one request: the
actual packet of every accepted task, authenticated against its frozen request
and its committed capture. The completion check of the epoch must be complete,
and the controller's frozen packet must carry the current evidence digest;
otherwise the review returns `problem_evidence_unassessed` with the completion
`issues`. The accepted tasks together must have delivered every frame field,
every item and every owned body as exact text spans, which the completion
check records per source as `coverage`. `value-review` and the
`review_evidence_undelivered` audit read the same packets, so a review can
cite a record or a span that an accepted task delivered, and the union of the
delivered spans of one artifact credits a citation inside it without widening
it.

The problem bar uses `exactory-problem-review-v5/bar` and receives `area`,
`field` and source inventory/deliveries with the statement, title, neighbours and
value withheld. The selected statement must retain the record's area, so the
bars remain established for their original scope; a statement in another area
is refused during bar assessment with `review_bar_mismatch`. Another subject
class requires a new problem record and its own bars. A record that names
further areas keeps its one subject class: its problem packets carry
`problem_areas`, and each bar states the standard of each area
([Further areas](#further-areas)). Two observed bars are
required before a result packet, and a result assignment, its source-request
continuations, refreshes and context repairs are refused before dispatch while a
current bar or a bar that the latest slate cites was recorded on the version 1
template (`problem_bar_calibration_required` naming `review_id`), or while
such a bar was given before a further area was recorded (the same code, naming
`review_id` and `area_ids`). Every result
packet, including a continuation, a refresh or a context repair, carries the
current `bar_ids`, `bars` and `own_bars`: the reviewer's own bar, or for a
reviewer that holds the slot through a context repair, the current bar of that
slot. The bar payloads keep their grades and standards readable, with artifact
references as templates. A result review names only bars whose content the
request of its exact authenticated attempt delivered, in its current packet or
in an earlier packet of its session that the request replays: `value-review` refuses
another with `review_bar_mismatch` naming `bar_ids`, and the post gate reports
the same code for a recorded review. Each result packet also carries
`problem_review_currency`, the digest of `{problem_evidence_digest, bar_ids,
objection_versions}` (the sorted current bars and, for each material objection
the packet delivers, its id and material version). A new result assignment for a reviewer
whose completed result review was given under another currency continues that
reviewer's session and slot as a refresh, and its `problem_refresh` adds
`previous_problem_review_currency` and `problem_review_currency`. A completed
result review whose assignment is on a supported earlier result template is
refreshed the same way even when the currency is unchanged, and its
`problem_refresh` then adds the previous and the current template ids and
hashes, under the reason `prompt_contract_changed`, or `evidence_undelivered`
when the refreshed review also cites content its packets did not deliver.
Otherwise, under the same currency, a new assignment returns
`review_resampling_forbidden`, unless the reviewer's latest result review
carries `review_evidence_undelivered`. The result template
`exactory-problem-review-v5/result` receives `problem_evidence`
with the claim, attempt, accepted results, openness, value, assessment, resolved
`assessment_evidence`, citations, delivered readings/passages,
admissions/executions/assessments/output validations and bound native closures.
For a value record with accounts, the value part carries the accounts with
`derived_accounts`, the packet carries `account_records`, and the prompt carries
the accounts paragraph, and the replacement paragraph when the accounts hold a
replacement part ([The accounts of a value record](#the-accounts-of-a-value-record)).
Each record that a problem packet projects carries the stored record's `id` and
`digest` (the claim, openness, value, assessment, attempt, readings and formal
checks), and a projection that carries further records (the attempt's native
attempt, a formal check's native closure, a computation's admission, execution
and assessment) lists them in `record_references`. A reviewer cites any of them
as `{kind: "record", record_kind, id, digest}`. Each review payload that a
problem packet delivers (`bars`, `own_bars` and `historical_reviews` of the bar,
slate and result packets, and the calibrations) carries `record_reference`, the
exact reference of the stored review, and its artifact references as templates,
so a payload that cites a source attaches none of its bytes. Each entry of a
result packet's `material_objections`, each entry of a slate packet's
`material_objections` (first, continuation and context repair), and each
objection of a review in the `historical_reviews` of a first slate or result
packet (another slot's contaminated or repaired lineage, on whose objections the
reviewer also gives a finding), carries its own references as templates and two
delivery keys in the adjudicator's shape (below): `version_evidence`, the cited
records as content and the supplied artifacts, and `version_source_mappings`,
the passages that its stating reviewer received for the cited sources. The
`material_objections` of slate and result packets include every objection of a
historical bar review (a contaminated or repaired bar lineage) as the version
its review stated: the same collection from which `value-review` requires a
finding on each such objection. Every delivered passage mapping (`prior_context`,
`version_source_mappings`, `supplied_source_mappings` and the mappings under an
objection) carries `citation` only when its text or span link passes the receiving
role's evidence validator, requiring a current full-text reading whose inspections
contain the passage. The citation is a complete, valid source link
`{version_id, source_id, artifact, locator}` for exactly that passage, which a
reviewer copies verbatim to cite it; the citation names its original without
attaching it. An abstract-only text passage or a JSON passage carries no
`citation` in a review or adjudicator packet and is delivered for reading.
A correction may still cite any delivered passage, including JSON: correction
input and adjudicator output evidence have separate requirements. The packet attaches only the bounded excerpts of those passages,
and a link within a passage's bounds that the reviewer copies is delivered when
an adjudicator receives the reviewer's version. Bytes reach a
reviewer only through the problem evidence, the delivered sources and passages
with their bounds, an objection's `version_evidence` and
`version_source_mappings`, a context-repair packet's
`previously_supplied_evidence` and the reviewer's own session history; a
reviewer that needs a source that another review cites requests it with
`source_requests`. Since 0.71.0 the observed packets of a review are the
packets that the request of its exact authenticated attempt carried, together
with the history that the recorded protocol permits: the counted turns and the
completed earlier series of a result slot delivered in parts (the 0.69.19
rule), and the accepted task graph of a staged review. A saved packet of the
lineage that the request did not carry delivers nothing; an output correction
is checked against the delivery of the original attempt that its diagnostic
binds; and a delivery that cannot be authenticated or decoded is refused with
`review_invocation_unverified` or `review_input_unreadable`, with no fallback to
a saved packet. A
bar, slate or result review, or a problem adjudicator, cites a record only when one of its observed packets
carried that record at the cited digest as content; a reference that the
reviewer received only inside a finding, or as an undelivered entry, does not
count. It cites an artifact as a descriptor only when its observed packets
delivered that artifact's bytes or one of its passages; the descriptor of an
artifact received only as passages names those passages and does not widen
their bounds. It cites a source link only when its packets delivered the
artifact in full, or when the link's locator lies within a delivered passage of
the same artifact (a text locator by its offsets; a locator of another kind
equal to the passage's). These delivery checks also cover `reassessment.findings[].evidence`.
`value-review` and `review-adjudication` refuse another citation, after the
shape checks, with `review_evidence_undelivered`, naming the record, or the
artifact's `sha256` and, for a source link, its `locator`.
Both result reviews must pass exactly `problem_statement`, `problem_openness`,
`problem_value`, `problem_citations` and `stop`. A marker author cannot be a
reviewer (`review_not_independent`); an assignment's unknown author is refused
with `problem_author_unknown`. In a problem record the `author_id` of every
`review-assignment` names one of the authors that `problem-init` recorded.
This holds for a `native_math` assignment of a math-solver objective inside the
record too, and when the session that wrote the reviewed work has another
name. The refusal lists those authors in its details, under `authors`.
Existing route probes, source-request
continuations, assignment currency and independence checks apply.
The blind bar also states the specialist's meaning of levels 2 and 3 of B1,
B2 and comparative priority for the field and line. A result review on a
template that carries the rubric (`exactory-problem-review-v2/result` and the
later result templates through `exactory-problem-review-v5/result`) adds
`value.rubric` with the same full grades shape and `value.rubric_evidence` with
nonempty evidence-and-reasoning text for exactly its graded items; a review on
the current template grades `A1` through `A7`, and a review recorded on one of
the earlier ones with `A1` through `A6` is read as recorded. Both result reviews
must meet the thresholds after any focused adjudication. Recorded problem
assignments on the earlier templates, the version 1 ones included, retain their
exact authenticated prompt and remain verified. For a statement not yet posted,
the post gate requires both result reviews to grade every rubric item, A7
included: a version 1 result review, which has no rubric grades, gives
`problem_rubric_review_required`, and a result review graded with `A1` through
`A6` gives `problem_rubric_regrade_required`.

Material objections from both bars and both observed result histories remain
post obligations (`research_objection_unresolved`), including predecessor
findings marked `continuing` or `contested`. Each objection carries a material
version: the digest of its finding content (`claim`, `reason`, `evidence`,
`resolution_condition` and, for a slate objection, `candidate_ids`) with the id
and recorded revision of the review that stated it. An entry of `objection_findings`
in an observed bar, slate or result review with status `continuing` or `contested`
whose `reason` or `evidence` differs from the version it continues is also a new
version: it keeps the `claim`, the `resolution_condition` and the `candidate_ids`
of that version, and it takes the finding's `reason` and `evidence` and the id
and recorded revision of its review. A finding that repeats the reason and
evidence of the version it continues adds no version. Versions follow the
recorded order of the reviews. Only the latest review of a lineage (the same
stage, reviewer, session and slot; for a result, also the same problem evidence)
holds versions current, by stating them or by continuing them in its findings. A
review that a later review of its lineage superseded states no current version:
its objections and findings are history. A refreshed bar, slate or result review
therefore gives an `objection_findings` entry for every material objection of the
review it supersedes (`review_objection_history_incomplete`), and until it is
recorded those objections stay current. Per objection id, packets
deliver the latest current version with `material_version`, `review_id`,
`recorded_revision` and the other versions in `history`. Both result reviewers
must record evidence-backed resolutions, each in a review whose packet delivered
the current version, or an independent focused adjudication of the current
version must find the objection not upheld; a resolution or an adjudication of an
earlier version disposes of nothing later, and the changed currency lets the same
reviewers reassess. The gate computes the problem evidence digest whenever it
checks the evidence, also while the current result pair is empty. A ready
focused adjudication (`not_upheld`, or `not_binding` for the problem) bound to
the current evidence digest and material version therefore keeps its effect
while both result slots have pending successors. A resolved objection does
not satisfy the pair: the gate still requires two completed independent
result reviews. For resolution after a refresh of a result slot, see
[Problem evidence in parts](#problem-evidence-in-parts). The
`material_objections` of a result packet leave out the
results given on the current problem evidence: the other current result review
and the superseded reviews of both lineages. A refreshed reviewer sees its own
earlier reviews in `historical_reviews`. A new version from the other current
result review's finding is resolved by an adjudication or by a reassessment under
changed evidence.

An imported objection ([Carried objections](#carried-objections)) has the identity
`<id>@origin:<source identity>`, where the source identity is the digest of the
source record's initialization event and marker. An objection carried back into
the record that stated it keeps its native id. Its `material_version` is the
digest of the keys of every retained member of the identity. The members are each
version that a carry imported, from every carry, and each native version of the
same identity. A later carry that adds a member changes the version, so a
resolution or an
adjudication of the earlier version disposes of nothing later. A carry that adds
no member keeps the objection's material version, but it can update the source
links and the freshness cut. Both result reviews must resolve the delivered version
with evidence, or a focused adjudication must find it `not_upheld` or
`not_binding`, as for a native objection. The source's resolutions and
adjudications of it are delivered as context records with `purpose`
`source_resolution` or `source_adjudication` and clear nothing here.

Blind reviewers cannot see each other's objections, so two of them can choose one
id for different claims. In a problem record, an objection whose id an earlier
review stated with another claim, and that no packet of the review's lineage
delivered, is recorded under the identity `<id>@<review id>`. The review record
keeps the observed output unchanged and adds `objection_identities`, the map from
each such stated id to its identity. Packets deliver a qualified objection under
its identity with `stated_id`, the id in the reviewer's own output, and findings,
versions, adjudications and the post gate use the identity; name it in
`context.objection_id`. A stated id and claim that an earlier review stated keep
that objection's identity. A different claim under an id that the review's lineage
delivered is an output failure (`research_objection_identity_conflict`, check
`review.objections`), recorded as a refusal: a reviewer changes a delivered
objection through its finding. A carry whose imported identity equals a native
objection id with another claim is refused with the same code. Earlier releases read `objection_identities` as
absent, so a record that holds one needs 0.58.0 or later. Problem adjudication uses the existing review tools
with `dossier_id: null`; changed value evidence, a changed material version of
the adjudicated objection, or an earlier adjudicator packet that lacked a delivery
that the post gate requires of it (below) retains the focused adjudicator and its
observed history rather than opening a new reviewer choice: its next assignment is
a `focused_refresh` naming the previous and current evidence digests and material
versions and its `reason`, `version_changed`, `evidence_changed`,
`disposition_unsupported` or
`packet_incomplete`, the last with the lacking keys in `undelivered_keys`. Under
`disposition_unsupported`, `undelivered_references` names the evidence that the
previous recorded adjudication cited without receiving it. The reviewer, route
and author must remain the same. Under
an unchanged digest and version, a complete earlier packet and a disposition
whose evidence was delivered, another assignment
returns `review_adjudicator_fixed`, also when a delivery that the gate does not
require of the earlier packet differs, such as the calibrations of another pair
or `current_bar_calibrations`.
For a material objection, the problem adjudicator can also find `not_binding`:
the objection's claim holds on the delivered evidence, but the statement, its
resolution criteria and its value paragraphs do not rest on the contested
premise, or state their dependence on it explicitly, and the adjudicator cites
the problem evidence that shows this. A ready, in-scope `not_binding`
adjudication resolves the objection as `not_upheld` does. A rubric shortfall
needs `not_upheld`. An adjudication decided on the adjudicator prompt before
`not_binding` existed can be refreshed by the same adjudicator with the reason
`protocol_changed`. A focused packet also delivers the complete current source of
each full reading of the problem evidence (`focused-current-sources-v1`), because
the adjudicator cannot request a source, and its recording checks those bytes in
the actual request.
The adjudicator template is `exactory-problem-review-v2/adjudicator`. For a
below-threshold rubric grade, set `context.objection_id` to
`rubric:<review_id>:<item>` with both original result review IDs. Items are
`A1` through `A7`, `significance`, `priority`, `attempt` or `calibration`.
The packet identifies the grade and required threshold and carries both
results' `rubric_grades` and `rubric_definition`, `{item, definition, threshold}`
of the adjudicated item. Every problem adjudicator packet carries
`bar_calibrations`, the payloads of the original bar reviews that the adjudicated
pair was graded against, sorted by id: for a bar pair the two bars, and for a
result pair the bars that the results' recorded `bar_ids` name, as the results'
observed packets delivered them. `current_bar_calibrations` carries the current
bars that are not among them, which a context repair installed; it is empty when
no bar was replaced. Both keep the content and the `record_reference` of each
bar, with artifact references as templates, so the standards attach no source
bytes; bytes reach the adjudicator only through `current_evidence`,
`original_evidence`, `version_evidence` and the passages with their bounds. The
gate compares the calibrations in that templated form, so a packet that
delivered them as raw payloads still covers them. For a material objection the packet also carries the
evidence of the adjudicated version: `version_review`, the payload of the review
that stated the version, with artifact references as templates;
`version_evidence`, one entry for each reference that the version cites, except
an artifact that the stating review's packets delivered only as passages; and
`version_source_mappings`, the source passages that those packets delivered for
the cited artifacts. A cited artifact descriptor or source link is delivered as
the artifact descriptor that the stating review's packets supplied, whose bytes
the packet delivers. An artifact that those packets delivered only as passages,
cited as a source link or as its artifact descriptor, has no `version_evidence`
entry and is delivered through `version_source_mappings`. A
cited record `{kind: "record", record_kind, id, digest}` is delivered as
`{kind: "record", record_kind, id, digest, record, artifacts}`. `record` is the
projection that one of the stating review's observed packets carried for that
record at that digest, not the stored record: for a candidate, the slate
packet's `candidates` entry, which has no author notes and no disposition; for
a record that a result packet projects, the entry of the `problem_evidence`
list that carried it, or the projection whose `record_references` list it; for
a review, its payload in `bars`, `own_bars` or `historical_reviews`, matched by
its `record_reference`; for a record that a material objection's
`version_evidence` delivered to a reviewer, the inner projection of that entry,
together with the artifacts that the delivery supplied with it. A record
kind that no problem packet projects is never delivered. Artifact references in `record` are templates, and
`artifacts` holds the descriptors of its artifacts that the stating review's
packets supplied, including those a delivery supplied with the record, whose
bytes the packet delivers; the gate compares each entry with its artifacts, so
a packet without one of them is incomplete. A source link citation outside the
bounds of the passages its stating reviewer received is undelivered, also for a
version recorded before that rule. A reference that cannot be
delivered, a record that no observed packet of the stating review carried at
that digest or an artifact that those packets did not supply, is an entry
`{kind: "undelivered", identity, reference}`, and the packet is incomplete. An
artifact that the stating reviewer saw only inside a review payload, such as a
bar's evidence, was not supplied to it, so a version that cites it is
undelivered. `current_evidence`, the
version evidence and the original evidence used for a correction
(`original_evidence` and `supplied_source_mappings`) stay distinct. An observed, ready
`not_upheld` adjudication resolves only that item for the same subject and current
`problem_evidence_digest`, and only when its packet carried exactly the original
`bar_calibrations` and the `rubric_definition`; a material objection's
adjudication needs exactly the original `bar_calibrations` and the
`version_review`, `version_evidence` and `version_source_mappings` of the current
version, with every cited reference delivered. A cited reference that the packet
lacks or cannot deliver is named in `undelivered_keys` as
`version_evidence:<record_kind>:<id>` or `version_evidence:<sha256>`, and as
`version_evidence` alone when the packet has every expected entry but its list
differs, for example by an extra entry; the post gate does not count that
adjudication, and the same adjudicator refreshes with reason
`packet_incomplete`.
The adjudicator's own output evidence must have reached it through its packet
lineage: the problem evidence, projected records, the objection's `version_evidence`
and attachments, or the original and version passage mappings, including deliveries
retained through a correction or focused refresh. A record reference merely named
in a prior finding supplies no record content. An earlier saved adjudication
that cites undelivered content retains its history but carries
`review_adjudication_evidence_undelivered`, with `undelivered_references`; status
names that obligation and the disposition cannot resolve the objection or make
the post gate ready. A focused refresh with reason `disposition_unsupported`
lets the same adjudicator reassess it; a later ready adjudication supplies the
current disposition.
At assessment time the post gate also audits every current recorded bar, slate
and result review of the problem against the frozen delivery of the review's
assignment lineage, reassessment findings included. A review that cites content
its packets did not deliver stays in the record and carries
`review_evidence_undelivered` with `review_id` and `undelivered_references`,
the details of the `value-review` refusal. The pair is not ready, and no refusal
record is written for a recorded review. The audit reads the delivery as the
packets recorded it, so a current reading added or removed after the record
changes nothing. The same reviewer refreshes the review in its session and
slot: a new assignment of the same role with an empty `context`. Its packet
carries `problem_refresh` with `reason: "evidence_undelivered"` and `review_ids`
naming the superseded review, which `historical_reviews` delivers so that the
new output gives its findings on the same objections. The refreshed review
supersedes the audited one and ends the obligation. Another refresh under the
same currency is refused with `review_resampling_forbidden`. A post decision
that names the superseded pair needs a new post decision with the refreshed
reviews. The audit and the refresh apply to problem reviews only.
The original grades remain visible. An old-evidence adjudication, a resolution
of another item, or a material-objection finding cannot waive the shortfall;
the five independent assurances still have to pass.
If an id identifies both a rubric shortfall and an explicit material objection,
disambiguate the findings before focused adjudication (`review_objection_missing`).
A material objection with a rubric-shaped id remains valid when no grade collides.

`gate post` is the only applicable gate in a problem workspace. Every other
gate reports `gate_inapplicable`, as does `post` outside this orchestration.
`exactory-lab state show` and `state set` have no study to read or update.
Budgets, scopes and roots, searches, readings, deferrals, availability, source
requests, depth plans, probes and review tools are available without a target,
intent, dossier, synthesis or direction decision. The `cycle`, `admit`,
`bind-run`, `reconcile-run` and `assess` route uses `{id: statement_id,
statement}` as its objective and requires the current statement and adopted
constitution. Its records retain nonmanaged dependency composition and
empirical meaning. A plan's `literature` comparison carries the
`literature_digest` that the record's `status` reports (see below); `cycle`
refuses a plan whose `literature.literature_digest` differs from it with
`literature_comparison_stale`. `exactory-lab run` launches the admitted
computation from the record's root or a directory under it, with the same
bound script, backend, timeout, seed and request identity as in a study, and
prints the run summary with `execution_id` and `outputs` as there (see "Actual
execution"); `reconcile-run` and `assess` follow as there, and no study is
created. `exactory-lab decide`
appends an operational entry to the record's decision log when `--stage` names
the stage; without `--stage` it refuses with `invalid_input`, because the
record has no study stage to default to. Such an entry records a choice, for
example the population of the cohort tool. The post decision is
`problem-decision`.

| Post obligation codes | Required action |
| --- | --- |
| `problem_slate_missing` | Compare the current candidates and record the selection with `problem-slate`. |
| `problem_slate_stale` | Record a new slate that disposes of every current candidate; `missing` names current candidates outside the latest slate and `extra` names its candidates that are no longer current. With the `reason` `problem_objection_carry_changed`, the latest slate does not bind the current imported set: obtain a focused slate review of it and record a new slate ([Carried objections](#carried-objections)). |
| `problem_candidate_unselected` | Formulate a candidate that the latest slate selects, or record a new slate. |
| `problem_statement_missing` | Select the exact statement and resolution criteria. |
| `problem_attempt_missing`, `problem_attempt_stale` | Record an attempt for the current statement and criteria; a stale obligation names `attempt_id` and `statement_id`. A superseded native snapshot also names `native_attempt_digest` and `latest_native_attempt_digest`. |
| `problem_attempt_resolved` | The attempt resolved this statement, so the candidate leaves the open-problem path: a literature resolution redirects to the established result and its source, a solver resolution follows math-solver's own result delivery; the obligation names `attempt_id`. |
| `problem_rubric_attempt_unbound` | Bind the passing current assessment's attempt judgment to a current attempt record; the obligation names `assessment_id` and `statement_id`. |
| `problem_attempt_unbound`, `problem_attempt_statement_mismatch`, `problem_attempt_outcome_mismatch` | Deliver and bind the native attempt from this store with the exact claim and matching resolved outcome. |
| `problem_attempt_superseded` | Bind the snapshot named by the current pointer for `(attack_root, objective_id)`; both native digests identify the succession. |
| `problem_attempt_scope_restricted` | Use a named scope covering the statement; `native_attempt_digest` and `scope` identify the restricted native claim. |
| `problem_attempt_evidence_missing` | Cite an appropriate accepted acceptance or a listed full reading for the named finding. |
| `problem_attempt_extension_unexplained` | Explain an attempt exceeding 24 moves, 24 runs, one admitted worker or 300 admitted timeout seconds; `totals` gives usage and admitted bounds, and `exceeded` lists the limit names. |
| `problem_openness_missing`, `problem_openness_unresolved` | Obtain an observed supported openness finding with the audited minimum. |
| `problem_already_solved` | Redirect to the named established result or record a new statement. |
| `problem_openness_stale` | Refresh the finding for the changed formulation, search or reading. |
| `problem_value_missing`, `problem_value_reading_missing`, `problem_value_computation_missing` | Supply a value record with the accounts of the statement and ordered value paragraphs, with valid full readings or admitted, reconciled, assessed computation support. |
| `problem_value_stale` | The current value record holds accounts, and the record gained a later run on the current text, a later outcome of a form in the history of the statement, or a later delivered snapshot of a cited objective (`value_id`, `changes`). Record a new value record whose accounts take each change into account and cite the latest snapshot. The result reviews are then refreshed ([The accounts of a value record](#the-accounts-of-a-value-record)). |
| The codes of the accounts, for example `problem_neighbour_role_missing` or `problem_test_run_invalid` | The current value record does not validate against the current records, for example after a revision of the statement changed its neighbours. Record a new value record. The table under [The accounts of a value record](#the-accounts-of-a-value-record) gives the repair for each code. |
| `problem_value_result_missing` | Cite an accepted proof, reduction or obstruction of the current solver attempt; the obligation names `attempt_id` and `acceptance_id`. |
| `problem_result_unbound`, `problem_result_superseded`, `problem_result_missing`, `problem_result_extension_unexplained` | A value paragraph cites a result of a delivered objective that does not hold: the objective is not delivered into this store (`native_attempt_digest`), a later snapshot of it was delivered (`latest_native_attempt_digest`), the acceptance is not an accepted proof, reduction, obstruction or counterexample of the snapshot (`acceptance_id`), or a delivered total exceeds a normal limit without a reason (`totals`, `exceeded`). Deliver the objective, or record a new value record that cites the latest snapshot, an accepted acceptance of it and the `extension_reason`. For a value record with accounts, the gate lists a later snapshot as `problem_value_stale`, and `problem_result_superseded` is the refusal of a new record that cites the earlier snapshot. |
| `problem_assessment_missing` | Record the rubric self-assessment with evidence for every item. |
| `problem_assessment_evidence_missing` | Supply item evidence with the exact stored record digest at recording and projection, or at least one matching full source reading at projection; names `item` and the record ids or `link`. Re-record an assessment after a mutable record changes. |
| `problem_assessment_stale` | Record a new assessment for the current statement; the obligation names both ids. |
| `problem_rubric_below_threshold` | Repair self-assessment shortfalls, or repair or independently adjudicate the named review shortfalls. `source` distinguishes `self` and `review`; ids and `{item, required, graded}` shortfalls identify the judgment. |
| `problem_rubric_review_required` | The named result review was recorded on the version 1 result template, which has no rubric grades. Assign the result role again to the same reviewer: the harness continues the review on the current result template, which grades every rubric item. |
| `problem_rubric_regrade_required` | The current self-assessment (`assessment_id`, null when it grades A7) or the named result reviews (`review_ids`, possibly empty) were graded with exactly `A1` through `A6`, before A7, and the statement is not posted. Record a new self-assessment that grades A7 when `assessment_id` is not null, and assign the result role again to the reviewer of each review in `review_ids`: the harness continues that review in the same session and slot on the current result template, and its packet carries `problem_refresh` with the previous and the current template, under the reason `prompt_contract_changed`, or `evidence_undelivered` when that review also cites content its packets did not deliver. The earlier records are read without error and stay unchanged; while this obligation is listed, their below-threshold shortfalls are not listed. Bars on `exactory-problem-review-v2/bar` and slate reviews on `exactory-problem-review-v2/slate` stay valid and are not reassigned for A7 alone (`review_resampling_forbidden`); a version 1 bar still needs its calibration refresh (`problem_bar_calibration_required`), a slate review is refreshed when its candidates or bars change, and a bar or a slate review is refreshed to repair `review_evidence_undelivered`. |
| `exploratory_evidence` | Preserve the probe observation and obtain admissible support for the claim. |
| `problem_formal_check_missing`, `problem_formal_check_stale` | Bind the requested check for the current value evidence to its authenticated native completion. |
| `problem_citation_locator_missing` | Record a DOI, arXiv id or read-version bundle HTTPS locator for the cited work. For a read work that has none of them, record the HTTPS location of its retained original with `problem-citation-locator` (the table under [Problem creation](#problem-creation)). In a problem record, `bundle` returns this code as a warning when the bundle's work has none of them, before a full reading binds the bundle. |
| `problem_citation_locator_unbound` | Bind the location to an HTTPS capture of the exact original, version and reading: the conditions of the `problem-citation-locator` row under [Problem creation](#problem-creation). When the message says that the provenance changed, the reading or the source record changed after the record was made: record the location again under a new id. |
| `problem_citation_work_ineligible` | Record citation metadata only for an existing `url:` work with no DOI and no arXiv identity; the refusal names `version_id`. A work with either identity is cited from its work record. |
| `problem_citation_evidence_unbound` | Bind each span to a retained object of this store, the `response.sha256` of the named source record or the `text.sha256` of a `fulltext` capture of this exact version with that source, and keep the object retained; `source_id` and `sha256` name the span. Without details, the source record of a span changed after the record was made: record new metadata under a new id. |
| `problem_citation_span_mismatch` | Make the field value equal the selected bytes exactly, or move `start` and `end` to the bytes that hold it; `source_id` and `sha256` name the span. |
| `problem_bar_pending`, `review_bar_mismatch` | Obtain both current field/area bars. |
| `problem_review_pair_required`, `problem_evidence_unassessed`, `problem_assurance_pending` | Obtain the independent observed pair with delivered current evidence and all five assurances passed. When fewer than two result slots are occupied, `problem_review_pair_required` reports that the independent pair is missing, also when the occupied slot has pending work. It does not name a slot, and it carries `review_ids` (the current recorded reviews, possibly empty). |
| `research_objection_unresolved` | Resolve the named material objection with evidence or a focused independent adjudication. An imported objection (`<id>@origin:<source identity>`) is resolved in this record the same way; its source's resolution does not count. |
| `problem_objections_refresh_required` | A linked source record changed since its carry, or has unfinished relevant review work (`source_workspace`, `source_revision`): it recorded a bar or slate review, a new version of an objection or a carry of its own; or a current bar or slate slot head, or a current result head whose frozen packet delivers a bar, slate or imported objection identity, has no recorded `value-review`, a completed but unrecorded final included. A result head of an older statement counts too. Complete each such chain to its final assessment and record it; a superseded assignment and an intermediate turn need no record. When the code stays, refresh the source's own links first and record a new carry with a new id. Then refresh the destination reviews whose currency changed: a changed nonempty imported set needs a focused slate review and a new slate, a review whose currency did not change stays valid, and the gate lists what remains ([Carried objections](#carried-objections)). When the pending review belongs to an earlier statement whose candidate is still current, run `problem-statement` in the source with the exact payload of that statement under a new request id, which selects it again; record the review; then select the newer statement the same way. Complete relevant reviews before you supersede their candidate: when that candidate is already superseded, `problem-statement` refuses with `problem_candidate_missing`, and no command of this release records the review. Preserve the records and report it. |
| `problem_objection_source_unavailable` | A linked source workspace cannot be opened, or holds another record (`source_workspace`, `cause`). Restore it, or record a new carry that names the moved workspace. Under `after_post` of a posted record, this row and `problem_objections_refresh_required` are an owed refresh of a post that stands, and no review work follows ([Carried objections](#carried-objections)). |
| `problem_forming_refresh_required` | The forming history of a linked source changed since its forming import (`source_workspace`, `source_revision`): the source recorded a forming call, an outcome, a probe, a plan, a run or an assessment, a later snapshot of a solver objective that its outcomes cite was delivered, or it recorded an import of its own. Run `forming-import` again under a new id with the source's current revision and the same candidates. The new import changes the problem evidence, so the result reviews are refreshed ([Imported forming history](#imported-forming-history)). |
| `problem_forming_source_unavailable` | The source workspace of a forming import cannot be opened, or holds another record (`source_workspace`, `cause`). Restore it, or record a new import that names the moved workspace. Under `after_post` of a posted record, this row and `problem_forming_refresh_required` are an owed refresh of a post that stands. |
| `review_evidence_undelivered` | The named recorded review cites content that the request of its authenticated attempt did not deliver (`review_id`, `undelivered_references`); since 0.71.0 the audit reads that request, so an older review can carry this obligation for content that only a saved packet of its lineage held. Assign the same reviewer and role again with an empty context to refresh it. |
| `problem_decision_missing`, `problem_decision_stale` | Record a `post` decision covering the current evidence digest. |
| `review_sources_pending` | Resolve the reviewers' pending source requests. |
| `problem_evidence_part_pending` | A series that 0.69.x started is not complete: run `review-series --assignment ID` ([Review chains and `review-series`](#review-chains-and-review-series)), which continues the named part turn (`assignment_id`, `turn`, `remaining`) with a new assignment of the same reviewer, route and author and `context: {prior_assignment_id}`; the turn after the last part is the final turn, which returns the review. |
| `review_parts_pending` | A series of fresh-input turns ([Reviews in fresh parts](#reviews-in-fresh-parts)) is not complete; a recording that names a series whose current v2 successor has no authenticated complete final is refused with the same code. The obligation carries the plan of the chain (`state`, `command`, `payload`, `request_id`, `not_before`, `code`, `details`); run `review-series --assignment ID`, which executes it ([Review chains and `review-series`](#review-chains-and-review-series)). `adapter`, `series_assignment_id`, `kind`, `turn`, `units_carded` and `units_remaining` show the progress, and `blocked` names the code when the turn cannot continue. |
| `review_series_superseded` | A saved selection or a result names a final of a superseded series. Select only the recorded finals of the current result slots; older finals stay as history ([Problem evidence in parts](#problem-evidence-in-parts)). |
| `review_result_pending` | The current series of a slot has an observed complete final that is not recorded. Record it with `value-review` from its exact output; no further reviewer call is needed. |
| `review_task_pending` | A task of a staged review is ready, blocked or not yet planned: run the `review-assignment` or `review-run` payload in `next`; `review-assignment {controller_assignment_id}` plans and assigns the next task ([Staged problem reviews](#staged-problem-reviews)). The post gate also lists this code, with `occupied`, `incomplete` and `pending`, when a current result head is pending and no head is incomplete. The head can be of any series: an ordinary review, a parts v1 series, a parts v2 series or a staged review. When a current result head is incomplete, the gate lists `assessment_incomplete` instead, also when another head is pending. Do the work that `next` lists for the pending series. That is a continuation obligation such as `problem_evidence_part_pending`, or the planned action of the pending head, for example `review-run`. |
| `review_capacity_wait` | The reviewer capacity is occupied: reconcile the named reservations with `review-run ... action: "reconcile"` and start no other call. |
| `review_worker_reconciliation_required`, `review_observation_acceptance_pending` | Reconcile the task's worker, or resume the acceptance of its committed capture, with the `review-run` payload in `next`; neither makes a reviewer call. |
| `review_epoch_stale` | The problem evidence, the subject or the slot head changed under a frozen epoch, or, in a record with a carry, the imported objection set changed; open an authorized successor with `context: {staged_upgrade: {assignment_id}}`. |
| `assessment_incomplete` | A required assessment of an occupied slot is incomplete, or a recording command met one: do the research or recovery work in `next`, never a repeat of an exhausted task. |
| `review_runtime_changed` | The installed runtime of the reviewer adapters in use changed between the start and the end of a `status` read or a review preparation; the command refused and wrote nothing. Run the same command again against the current runtime. |
| `review_quality_measurement_pending` | The staged protocol and reviewer configuration have no passing quality measurement; the gate stays not ready for a staged review in this release, whether 0.70.0 opened it on assignment or an explicit `staged_upgrade` did. A slot delivered in parts carries no such obligation. |
| `problem_already_posted` | Retain the existing confirmed post; the same statement cannot be posted twice. When the store holds a `problem_post` for the current statement, `next` is this code alone, and `status` lists the problem's other findings under `after_post` (below). Since 0.73.6 the same code refuses new review work on the posted statement. Since 0.75.0 a post closes review work per chain: `plan_next` gives `done` for a closed chain, and the chains of other statements continue ([Problem creation](#problem-creation)). In a record with a carry, a linked source that changed or disappeared after the post is listed under `after_post` as `problem_objections_refresh_required` or `problem_objection_source_unavailable` ([Carried objections](#carried-objections)). In a record with a forming import, the same holds with `problem_forming_refresh_required` and `problem_forming_source_unavailable` ([Imported forming history](#imported-forming-history)). |

`status` and `next` compute milestones from records: `exploration` before
any slate or while the latest slate is stale by its candidate set (a slate stale
by the carry digest alone keeps its milestone), `slate` with a current slate and no
statement or with a current slate that selects no candidate, `formulation` when
the current slate selects a candidate and a statement is selected, `attempt` when
`assess_attempt` returns no obligations,
`openness` after that with current `supported_open`, `value` with
valid value evidence, a passing current rubric self-assessment that grades A7,
both bars, both result reviews grading every rubric item, A7 included, and
passing the assurances and rubric after adjudication, and a current post
decision, and `post` with a recorded post.
`next` names the first missing condition in the order slate, statement,
attempt, openness, value, reviews, decision, export. A stale slate leads with
`problem_slate_stale`. When the current slate selects no candidate, `next` is
`{code: "problem_no_post", reason, explanation}`
with the slate's reason: the exploration ends without a post unless a new slate
selects a candidate. The full `status` and `next` reports carry that reason; the
`--summary` views show the code and the explanation only. Milestones impose no
tool order. `status` also reports
`candidates: {current, selected, deferred, rejected}`, the number of current
candidates and the lengths of the latest slate's lists, and `slate_id`, the
latest slate or null. Once the record holds a forming call, `status` and
`status --summary` also report `forming: {calls, forms, by_standing,
by_disposition, without_outcome, outcomes}`
([Forming records](#forming-records)); a record without a forming call has no
such entry. It lists the current depth plan under `limits` and no
cohort policy counts; those belong to ai-science. The full `status` and `next`
reports also carry `literature_digest`, the digest of the record's current
literature scope, frontier, search judgments and requirements. `cycle` compares
it with a plan's `literature.literature_digest` and refuses another value with
`literature_comparison_stale`; a plan that carries the reported value passes
`cycle --check`. `status --summary` and `next --summary` do not carry it. In a
study, `status` keeps the digest at `preparation.literature_digest`, as before.

After `gate post` reports ready, run:

```sh
exactory-research export --kind problem
```

`export --kind problem-override` composes the same files from the current
statement and the value record when the store holds a `gate_override` with
`gate` `post` whose `statement_digest` is the current statement's
(`gate_override_required` otherwise; the digest covers the statement text and
the resolution criteria, not the id, title, area, neighbours or formal
statement; `problem_value_missing` without the value record). It reads no review and no
decision: its `problem-export.json` has `decision_id` and
`problem_evidence_digest` null, the `override_id` and `gate: "bypassed"`. It
writes under the same folder with the same destination rules, `problem-posted`
refuses its directory, and the post is recorded with `problem-post-external`
([Stops and overrides](research-workflow.md#stops-and-overrides)).

Without `--destination` the export writes to
`final/problem/exports/<export digest>/` under the workspace root, where the
export digest is the digest of every exported file, `problem-export.json`
included, the value that `problem-posted` records as `export_digest`; the result
is the manifest with the directory under `destination`. A destination, default or
given, that already holds exactly the exported files is returned as it is; one
that holds other files is refused with `review_destination_exists`
(`destination` in the details). A `--destination` outside
`<workspace root>/final/problem/exports/` is refused with
`export_destination_outside_final` (`destination` and `folder`, the allowed
folder, in the details); its message names the default.
An unready export returns `readiness_required`. The deterministic projection is
`title.txt`, `statement.md`, `motivation.md`, `state.md`, `criteria.md`,
`citations.json`, `citation-refs.json`, optional `formal-statement.txt`, and
`problem-export.json`.
The manifest contains `{schema, statement_id, statement_digest,
problem_evidence_digest, decision_id, area, formal_statement_language,
files: {name: sha256}}`, with `schema` set to `exactory-problem-export-v2`.
Citations are projected once per distinct work among the
decisive openness and settling readings, the value readings, the readings of the
current literature attempt and the full readings that the self-assessment's source
references name, as `{citation, locator}` using metadata citation and DOI, then
arXiv id, then the HTTPS URL of a unit of the read-version bundle, then the URL of
the latest `problem-citation-locator` record of the cited reading. Every cited work
has a reading in this store. The binding lists `citation_locators` (`{id, digest}`)
when such a record exists, and the delivered evidence gives the reading its
`citation_locator` with its digest.

A `url:` work has no registry metadata, so its citation comes from the imported
work record, whose title can be a file path and whose authors can be absent, for
example a manuscript PDF in a GitHub repository found through code search. For
such a work, record the title, the authors and, when a retained capture states
it, the year with `problem-citation-metadata` (the table under
[Problem creation](#problem-creation)), each bound to the exact bytes of a
retained capture: the extracted text of the PDF, or a captured response such as
the repository's README. Verbatim matching establishes that the text appears in
that capture; that the README's authors wrote the manuscript stays a reviewer
judgment. The export and the evidence binding then use the latest record for
that version: `citation` is the recorded authors joined by `, `, then `. ` and
the recorded title, then `. ` and the year when one is recorded, and `locator`
is unchanged. The binding adds `citation_metadata: [{version_id, id, digest}]`
for every such version among the cited readings, so a changed span changes the
evidence digest even when the citation text is the same. The result reviewers
receive the record inside the reading's `readings` entry as `citation_metadata`,
with the values, the spans, their provenance, the record's `id` and `digest`,
an excerpt of up to 256 characters on each side of every matched span with its
byte boundaries, and a `cite` reference of kind `record` and record kind
`problem_citation_metadata`; the coverage check requires that entry exactly. A
record that no longer validates, because a retained object or the source
record's provenance changed, is reported under its refusal code, never
replaced by the work record.

`citation-refs.json` is the reference list of the exported citations, in the
shape that `exactory-check lookup --refs-json` reads. It holds one entry for
each entry of `citations.json`, in the same order:
`{referenceString, bibliography: {title, authors, doi}}`, with `arxivId` in
place of `doi` for a work that has an arXiv id and no DOI. `referenceString` is
the `citation` text. `title` and `authors`, a list with one name in each
string, are the values from which that text was composed: those of the work
record or, for a `url:` work, those of its latest `problem-citation-metadata`
record. `doi` or `arxivId` holds the locator of the citation. A work whose
locator is an HTTPS URL has neither field, and the check then searches for its
title. `exactory post-problem` does not read the file. `export --kind problem`
and `export --kind problem-override` write it since 0.88.0, and the `schema`
field of the manifest marks an export that holds it. An export that an earlier
release wrote has no `citation-refs.json` and no `schema`. It keeps its files
and its digest, and `problem-posted` records it as before: the command
compares a directory with the current projection first and then with the
projection without the reference list. A directory whose `citation-refs.json`
was changed or removed is refused with `problem_export_mismatch`.

The math-conjecturer delivery section checks the exported citation locators
and duplicates, runs `exactory post-problem` on the exported files, then records
the returned id through `problem-posted`. The post record contains
`{id, problem_id, statement_id, statement_digest, problem_evidence_digest,
export_digest, posted_at}`. Its `posted_at` is the time when the harness
records the confirmed post, normally right after `exactory post-problem`
returns. Because posting and recording are two commands, an interruption
between them delays that time. Its export digest hashes rebuilt approved bytes;
the directory's manifest is informational, and the comparison of the
directory's file set ignores entries whose names start with a dot. A changed
part requires a new
statement and a fresh post gate. The `math-conjecturer` skill creates the
research record and runs its delivery section.

A posted problem. Since 0.73.0, when the store holds a `problem_post` for the
current statement, `next` is `problem_already_posted` alone, and `status`
lists the problem's other findings under `after_post`, each with its `code`
and `reason`, so that nothing is hidden. Since 0.84.0 `after_post` also lists,
after those findings and in recording order, each fact that `problem-post-fact`
recorded for the post, with the code `problem_post_fact`. Since 0.88.0
`status` and `next` list the obligations of a review chain that a post closes
(Review work after a post, below) under `after_post` as well, and not among
the main obligations. This holds for an obligation that names an assignment of
such a chain, and for the obligations that `status` adds for a chain head that
ended without an output, for example `review_invocation_unverified` or
`review_output_pending` of a turn that was stopped, or of a chain that a later
export replaced. `next` therefore stays `problem_already_posted` alone, and
the `obligations` command lists no such row. Such a row stays under
`after_post` when another statement is selected afterwards, so `status` can
carry `after_post` while the current statement has no post. `next --summary`
reports `after_post` as a count. `status` shows the stored post under `post`, with `gate` set to
`passed` when the record has no `gate` (a post recorded before 0.73.0). An
external post also carries its `reason`. A post recorded by `problem-posted`
has no `reason`. A problem posted outside the harness, without `gate post`, is
recorded with `problem-post-external` (the table above): the record is a
`problem_post` with `gate` `bypassed` and no export, it is terminal for the
post gate under the same rule, and `status` shows the bypass. An older
external post, which 0.73.0 to 0.73.2 recorded, has no `recorded_at`, and
its `posted_at` holds the recording time. `status` shows a copy of it with
`recorded_at` set to the stored `posted_at` and `posted_at` null. When the
store holds a time statement for it (`problem-post-time`, the table above),
`status` shows the post with `posted_at` from the statement and `recorded_at`
from the stored post. It adds `time_statement`, the statement record
(`post_id`, `posted_at`, `evidence`, `recorded_at`, `recorded_revision`). The
post's `reason`, `evidence` and `recorded_revision` stay. The stored record, its
receipt, the store fingerprint and the raw output exports
(`final/problem/posts`) keep their bytes. A post with `gate` `passed`, or
without `gate` (recorded by `problem-posted`), is shown as before.
`problem-posted` keeps its contract and requires the approved exact export.

Review work after a post. Since 0.73.6 a recorded post (a `problem_post`,
recorded by `gate post` and `problem-posted` or by `problem-post-external`)
closes review work on the posted statement. Since 0.75.0 a post closes review
work per review chain, not per record. A review chain of a problem record is
closed by a post under one of the two rules below. The other chains of the
record continue as before. New review work on a closed chain is refused with
`problem_already_posted`, the message "This exact statement already has a
recorded post." and the details named below. Nothing is recorded in the
store. Where a command accepts `--check`, the check gives the same refusal.
`review-run --check` and `review-output-correction --check` refuse with
`check_unsupported` first, as before. The rules cover the roles `bar`, `slate`,
`result` and `adjudicator`, and a `native_math` assignment whose verified
native packet is an openness packet (`export --kind problem-openness`). A task
of a staged review is classified through its controller. The other roles
(`verification`, `manuscript`, `standalone`), a `native_math` assignment whose
packet is not an openness packet (an independent solver review), a subject
with `kind: "study"`, and study records are not affected. A record without a
`problem_post` plans and admits review work as in 0.74.0, with no new
validation.

A chain bound to a statement. A chain is bound to a statement when the subject
of its assignment has a non-null `statement_id`. An openness chain is bound to
the claim of its packet: the `claim_digest` of the packet is the digest of its
`subject` (`statement`, `resolution_criteria`). Such a chain is closed when a
recorded post has the digest of that statement. The digest covers the claim
and the resolution criteria, so a statement under another id with the same
claim and criteria is closed too. A statement whose claim or criteria differ
stays open, and its reviews work as before. The details of the refusal are
`post_id` and `statement_id`. An openness chain has no subject record: its
details are `post_id` and `closure_root_id` (below), or `post_id` alone for a
new assignment. A bound statement that is missing or invalid is refused with
`problem_statement_missing`. A `native_math` assignment without a
`native_packet` reference is refused with `invalid_native_review_packet`. An
openness packet whose `kind` is not `result`, or whose `claim_digest` does not
bind its subject, is refused with the same code.

A chain bound to no statement. A chain whose assignment has no subject, or a
subject with a null `statement_id` (a `slate`, or a `bar`, `result` or
`adjudicator` assigned while no statement was selected), is closed by
chronology. Its root is the earliest assignment of the same role and dossier
in its lineage and in its retained slot. The lineage follows
`prior_assignment_id`, `context_repair`, `staged_upgrade` and
`history_assignment_id`, and the controller of a task. A
`history_assignment_id` of another role (the bar history that a result
inherits) is skipped. The retained slot holds the assignments of the same role
and dossier that share its `slot_id`. An assignment of that role and dossier
without `slot_id` is in the slot when its `id` is the `slot_id`. The chain is
closed when a post of the record, of any statement, was recorded after that
root. The order compares the
local creation revision of the post with the `recorded_revision` of the root. A
post that `problem-posted` or `problem-post-external` records carries that
revision as `recorded_revision`. For an earlier post without the field, the
store derives the revision from the first event that wrote the post, and
authenticates it on replay. A post whose revision is unknown, or whose
`recorded_revision` differs from its creation event, is refused with
`problem_post_revision_missing`, with the detail `post_id`. When such a chain
is closed, the details of `problem_already_posted` are `post_id` and
`closure_root_id`, the id of the root. When the record holds a post, the
ancestry of a chain with a predecessor or a retained slot is validated. A
predecessor that is missing, forms a cycle, has another role or dossier, or
changes the retained slot is refused with `invalid_review_context`. An
assignment without `recorded_revision`, and a binding that is not an object,
are refused with the same code. In `plan_next` these refusals are a `stop`
with their code.

Refused review work. For a closed chain, `review-assignment` refuses every new
assignment of the covered roles, the initial assignment included. The next turn
of a series (`prior_assignment_id`), a context repair, a staged upgrade, a
focused adjudication, a refresh and a replacement are refused too. For a chain
bound to a statement, the check runs after the identity checks of a problem
record (`review_not_independent` for a problem author as reviewer,
`problem_author_unknown`). It runs before the route, packet and slot rules, and
therefore before `review_resampling_forbidden`, `review_slots_fixed`,
`review_adjudicator_fixed` and `review_input_too_large`. For a chain bound to
no statement, the check needs the slot and the history of the new assignment.
It therefore runs after the slot and adjudicator rules
(`review_resampling_forbidden`, `review_slots_fixed`,
`review_adjudicator_fixed`) and before the part planners
(`review_input_too_large`). The complete record is checked once more before
the write. When the chain of the controller is closed, `review-assignment
{controller_assignment_id}` is refused before any planning condition, also with
`--check`. A staged child (`context: {controller_assignment_id, task_id}`) of
such a controller is refused too. `review-run` of an ordinary assignment under
a new attempt `id` is refused. This covers a first call, a new call after a
reconciled call, and the retry after a refused output. The refusal comes before
the invocation is recorded, and before the capacity admission, the call budget
(`review_budget_exhausted`) and the dispatch preflight. `review-run` of a
staged controller is refused with `problem_already_posted` instead of
`review_task_pending`. `review-run {assignment_id, epoch_id, task_id}` of a
staged task is refused for a new reservation and for a new attempt id. The
retry after a refused capture or a failed worker, and the launch of a reserved
worker that is not launched yet, are refused too. The reservation and the
launch check the post again inside their own serialized writes. A staged
`review-run` commits its dispatch before the reservation. A post recorded while
the run is in progress therefore stops it at the reservation or at the launch
authorization. The records committed before that point stay. A worker process
that started its launch handshake exits without a reviewer call, and the
reservation stays until it is reconciled. When a reservation stays, reconcile
it with `review-run` and `action: "reconcile"`, which releases its capacity
without a call. The post starts no new reviewer call. A call that started
before the post (an ordinary run whose start was recorded, or a staged worker
whose launch was authorized) completes, and its capture can be accepted (Work
that stays available, below). `review-output-correction` is refused before the
correction packet and its request are built.

Work that stays available. After a post, the replay of a committed request with
the same payload still returns its original receipt. This holds also for an
assignment that 0.73.3 to 0.73.5 recorded after a post. Another payload under
the same request id fails with `request_id_conflict`. For an ordinary call,
`review-run` with the original payload and the original request ID records the
run from its committed capture without a reviewer call. For a staged task, the
task payload with `action: "accept"`, or a rerun under the attempt that owns
the capture, accepts the committed capture without a call. `review-run` with
`action: "reconcile"` stays available for a call or a staged worker. The
reconciliation of a reservation whose worker never launched releases its
capacity without a call. A review that was already observed is recorded with
`value-review` or `review-adjudication`, and `review-context` events can be
recorded too. `plan_next` is per chain head. A closed head is `done`, with
`chain`, `head_id` and `progress` and without `command` or `payload`. The
heads of the other chains keep their plan. `review-series` of a closed chain
ends at `done` before any operation. `review-series` of an open chain of the
same record runs it. `record-check` ends a closed chain at the boundary `done`
and checks the other chains as before.

What the views show. Every reader keeps its results: `status`, `next`, `gate`,
the output views, authentication, the reconstruction of review requests,
`record-check` and `ci/record_fingerprint.py`. Only the presentation of a
posted statement and of a closed chain changes, as follows. The `after_post`
rows of the post gate keep every field of their obligation except `next`, in
the same order and number, with `reason` (the `explanation` when the
obligation has no `reason`). The posted record's `obligations`, `next`, `ready`
and `digest` are unchanged. Since 0.88.0 `status` and `next` then move each
row that names an assignment of a closed chain from `obligations` to
`after_post` (A posted problem, above), and `counts.obligations` counts the
rows that stay. The `gate` command reports its rows as before. For an
assignment of a closed chain, `status` shows
`review.assignments.<id>.review_parts.next` as `[]` and the `next` of each task
of a staged review as `[]`. The output view `series.json` shows
`review_parts.next` as `[]`. The `next` entries that `status` and `gate` attach
from the plan of a closed chain keep `kind` and `state` (`done`) and drop
`workflow` and `scope`. The entries of an open chain keep their fields.
Progress, findings, `blocked`, `incomplete_outcome` with its `recovery_work`,
the scientific flags, `assignment_state` and the raw records keep their
content. Records without a post keep their output byte for byte.

### Carried objections

A candidate that math-conjecturer takes up in another problem record keeps the
bar and slate objections of its source record through `problem-objections-carry`
([math-conjecturer Exploration](../skills/math-conjecturer/SKILL.md#exploration),
"Carry a reviewed candidate to another record"). The other record is a record of
another subject class, a sibling record, or a new record that names further
areas ([Further areas](#further-areas)). The forming history of the candidate
travels apart from the objections, with `forming-import`
([Imported forming history](#imported-forming-history)). The command runs in the
destination. Its payload is `{id, source_workspace, source_revision}`, with the
ordinary `--file`, `--expected-revision`, `--request-id` and `--check` flags.
`exactory-research example problem-objections-carry` prints it. The source is
named explicitly. Nothing is inferred from a candidate's `source.reference`, from
`observation` or from `notes`, and a record without a carry behaves as before.
With `--check` and a reachable source, the command validates the complete carry
and its proof. It stages the retained bytes in memory, sealed ones included, and
verifies the proof copies in disposable temporary storage. It publishes no
destination artifact, record, receipt, event or view, and it reserves no request
id. To record the carry, run the command without `--check`, with the current
revision. Full `status` and unsummarized `next` of a record with a carry list
`problem_objection_carries`. Each row is one carry, sorted by id, with `id`,
`source_workspace`, `source_origin` and `source_revision`. An empty carry is a
row too. The listing names the recorded direct sources, not their current state.
A record without a carry gains no field, and `--summary` has no listing.

What the command does. It opens the source store under a read guard. It
authenticates every bar and slate review of the source against its observed
assignment and output. It authenticates in the same way every result review of
the source that gives a finding on their objections. It exports every bar and
slate objection of the source, its later versions (the `continuing` or
`contested` findings of source result reviews), and the objections that the
source itself carried. The command copies the source's SQLite snapshot into this
record's artifact store as the `proof`. It adds the managed artifacts that it
finds through the source's history and nested JSON. The portable replay then
verifies the evidence that the carry requires. It writes one immutable
`problem_carried_evidence` record per member and per context entry. The record
holds the exact evidence projection that the stating reviewer received, with its
artifacts and bounded excerpts. Then it appends `problem_objection_carry/<id>`
with `source_workspace`, `source_revision`,
`source_origin`, `destination_origin`, `source_cut`, `members`, `proof`,
`origin_records`, `context_evidence_ids`, `sources` and `recorded_revision`. The
source's `resolved` findings and its adjudications of those objections become
context records (`purpose` `source_resolution` or `source_adjudication`) and clear
nothing here. An objection that a source result review states first, outside the
bar and slate identities, is not imported. The source is not written. An import
with no member records the link to the source alone. A carry of the same id and
an unchanged source adds no carry record. Under a new request id it still commits
its operation and advances the revision. Only the replay of the original request
returns its receipt without a write or a source check. The same id after a source
change is `record_conflict`, and a changed cut needs a new id. Carried proof and evidence
records appear in no `final/` or `intermediate/` view. Reviewers receive them
through packets.

Refusals of the command:

| Code | Cause and next action |
| --- | --- |
| `invalid_input` | A field is missing or extra, `source_workspace` is not an absolute path, or `source_revision` is not a nonnegative integer. Send exactly the three fields. |
| `problem_objection_self_import` | The source path is this workspace, or the source has this record's initialization identity (a copy or a snapshot of it). Name another record. |
| `stale_revision` | `--expected-revision` is not the destination's current revision (`revision`). Read it and retry. |
| `problem_objection_source_unavailable` | The source workspace, or a workspace that this record or the source links to, cannot be opened or is not a problem record (`source_workspace`, with `cause` for the underlying code), or it holds another record identity than the link (`source_workspace` alone). Restore the original workspace and its initialization identity; for a moved source, name its new path in a new carry. When `cause` is `store_recovery_required`, run `exactory-research recover` in that source. |
| `problem_objection_sources_exceeded` | This record, the source and their transitive links name more than 32 distinct source workspaces (`limit`). Stop and report it; omit no source. |
| `store_busy` | A linked store is in use. Retry after the active operation ends. |
| `problem_objection_source_revision_mismatch` | `source_revision` differs from the source's current revision (`source_revision`, `expected_source_revision`). Read it and retry with a new request. |
| `problem_objection_provenance_invalid` | An immutable review, assignment, adjudication or carry record of the source changed; a review or carry record has a `recorded_revision` other than the revision of the event that created it; a review does not match its observed assignment or output; a review's stored `objection_identities` differ from the identities that its observed output and the native identity rules give (also on first insertion, and also when the stored map is empty); an objection cites evidence that its reviewer never received; a proof descriptor changed; or the portable proof does not reproduce the exported set. The check covers every review of the source recorded up to the latest exported one, because the identities depend on the earlier reviews. Restore the original records and artifacts; author text cannot replace them. |
| `artifact_missing`, `artifact_corrupt` | A source artifact, or a retained proof artifact, is missing or has other bytes. Restore the exact bytes that the descriptor names. |
| `problem_objections_refresh_required` | The source has a bar or slate assignment whose review is not recorded, or a result assignment whose frozen packet delivers one of the carried identities and whose review is not recorded, a completed but unrecorded one included (`assignment_ids`); or a record that the source links to changed since the source's own carry (`source_workspace`). Finish and record that work, refresh the source's own links, then carry again. A result assignment whose packet delivers no carried identity creates no such obligation. For the same code at a later check, and for pending work on an older statement of the source, see the obligation table above. |
| `problem_objection_origin_conflict` | One origin, record kind and id hold two different immutable byte sets across the carries (`member`, or `origin`, `record_kind`, `id`). Investigate the conflicting records; do not rename an imported objection. |
| `research_objection_identity_conflict` | An imported identity equals a native objection id with a different claim (`objection_id`): the objection diverged in the two records. Preserve both claims and the inherited ids, and rewrite neither record. The carry into this record stays refused while the claims differ; report the conflict. |

Existing refusals keep their meaning: `orchestration_conflict` outside a problem
record, and `request_id_conflict` for a request id reused with another payload.
A carry can
bring back this record's own earlier records (a return to the origin, or a copy
of this record). After it, a later native `review-assignment`, `value-review`,
`review-adjudication` or staged task plan (`review-assignment
{controller_assignment_id}`, whose child ids are deterministic) cannot reuse an
id of that inventory with other bytes (`problem_objection_origin_conflict`).
Choose a fresh id, or a fresh controller for new staged work. Imported objection
ids stay unchanged.

Delivery. When a carry holds at least one member, every slate and result packet
of the destination carries `problem_objection_carry: {protocol:
"exactory-problem-objection-carry-v1", import_set_digest}`. Its
`material_objections` then hold one entry per imported identity, with these
fields:

- `id`: `<id>@origin:<source identity>`, or the native id when the objection
  returns to the record that stated it.
- `origin` and `origin_id`: the record that stated the objection, and its id there.
- `original_objection`: the verbatim source objection.
- `material_members`: one entry per retained version. `producer_origin` and
  `review_id` name the record and the review that produced it, and `version`
  holds the version. An entry also keeps `origin`, `origin_id` and
  `source_material_version`: the identity of the objection and the version id in
  its source.
- `history` and `material_version`: the versions, and the digest of the member
  keys.
- `evidence`: citations of the `problem_carried_evidence` records, with the usual
  `version_evidence` and `version_source_mappings`.

Bar packets carry none of this. The prompt of such an assignment is the current
slate or result template with the suffix `/objection-carry-v1` and one appended
paragraph (`PROMPT` in `research_harness/problem_objection_carry.py`). Its frozen
envelope keeps the parameter, so the assignment rebuilds from its own envelope
after later carries. An assignment without a carry keeps its prompt bytes. A
parts v2 series binds the digest in its series key. A legacy parts turn and its
continuation keep the marker. The final packet of a staged result review
delivers every imported identity. When a carry changes the imported set under a
frozen staged epoch, a new child task is refused with `review_epoch_stale`. Open
a successor epoch.

Findings and clearance. Each slate and result review gives one `objection_findings`
entry per imported identity, under the delivered id
(`review_objection_history_incomplete` for a missing entry). A different claim
under that id is `research_objection_identity_conflict`. The finding covers the
complete delivered version set. A reviewer that finds the objection irrelevant to
this record gives `resolved` with the evidence that shows it. A disputed relevance
goes to a focused adjudication with the qualified id in `context.objection_id`
and the two bar review ids in `context.review_ids`. The post gate lists an
imported objection as `research_objection_unresolved` until it clears. It clears
when both current result reviews resolve the delivered `material_version` with
evidence, or when a ready focused adjudication finds it `not_upheld` or
`not_binding`. A source resolution, the
selection or deferral of the candidate, a `continuing` or `contested` finding,
or one favourable finding clears nothing.

Slate currency. With at least one member, `problem-slate` requires a nonempty
`review_ids` (`problem_slate_review_unverified` with `import_set_digest`). A
cited slate review must have been assigned under the current `import_set_digest`.
The slate record binds that digest as `problem_objection_carry_digest`. A slate
recorded under another digest is `problem_slate_stale` with `slate_id` and the
`reason` `problem_objection_carry_changed`. After a carry that changed the set,
assign the slate review again to the same reviewer with an empty `context`. The
harness refreshes it as after a candidate change.

Freshness. Each carry records `sources`: the source and every record that the
source itself links to, each with its `origin`, `workspace` and the `cut`
imported (its bar and slate review ids and digests, its outstanding relevant
review work, the retained member keys with their producing origins, and its own
links). The harness opens every linked workspace under a bounded read guard
before a slate or result assignment, before a staged frontier plan, at
`problem-decision` with `post`, in `export --kind problem` and in `gate post`. It
reads the whole snapshot, validates the source's history and identity, and compares
the current cut of each with the recorded one. It rebuilds no evidence and no
sent request; the evidence and the proof are built by the carry alone. A changed
timestamp or revision alone does not count. A changed cut is
`problem_objections_refresh_required` (`source_workspace`, `source_revision`),
and an unopenable or replaced workspace is `problem_objection_source_unavailable`.
`gate post` lists both as obligations, and the mutations refuse. The check is
local and happens at the moment of the mutation. It cannot make an external post
atomic with the source, so run `gate post` immediately before `exactory
post-problem`. `problem-posted` opens no source. It records a post whose export
the gate approved when it was sent, also when a source changed or disappeared
after the gate, and it still requires the exact approved export and the current
local readiness. The post is an ordinary `problem_post`, which `status` shows
with `gate: "passed"`.
After it, `status` and `next` show the posted terminal: `problem_already_posted`
is the only main obligation, and the freshness failure is a row under
`after_post`, with its code, its reason and the source details, and without a
`next` action. The harness schedules no review work on the posted statement and
changes no post. New review work on it, an explicit continuation of a closed
slate lineage and the children of a posted staged controller are refused as
posted before any source is opened. `record-check` verifies every carry from its
retained proof (`problem_objection_carries` in the `integrity` result) and opens
no source workspace, so it establishes no current freshness.

What the proof establishes. The retained proof is a check of local hashes. It
shows that:

- the source's event and receipt history is consistent, and each immutable
  record kept its first bytes and its creation revision.
- each exported review matches its observed request, output and evidence.
- the stored objection identities equal the ones that the observed outputs and
  the native identity rules give.
- the exported projection is the one that the destination holds.

It does not establish who or what produced an output, or when an event happened.
It does not establish that the initialization was honest, that the history is
complete, or that an objection is true. The identity check rejects a stored
mapping that disagrees with the retained outputs and the recorded earlier
reviews. It does not authenticate the order or the completeness of that history
on its own. A party that can write the source store and its artifacts can build
a consistent fabricated history.

Evidence routes. Carried evidence can include rendered PDF pages. A destination
route that cannot receive image evidence marks those bytes omitted, and a
reviewer must not claim to have seen them. Assign such a review on a route that
receives images.

### Forming records

`forming` and `form-outcome` are commands of the Ideate tool in a problem
record ([Forming calls and outcomes](research-tools.md#forming-calls-and-outcomes)).
They record how the record formed a statement and how each tentative statement
ended ([math-conjecturer Exploration](../skills/math-conjecturer/SKILL.md#exploration)).
The third command of the tool in a problem record, `forming-import`, brings the
forming history of another record ([Imported forming history](#imported-forming-history)).
Both take the ordinary `--file`, `--expected-revision`, `--request-id` and
`--check` flags, and `exactory-research example forming` and `exactory-research
example form-outcome` print complete payloads. Both check their own inputs and
no milestone. Their records grant no openness, no significance, no proof credit
and no test credit, and the post gate reads none of them. The slate reads the
source of a selected candidate (the slate review rule under
[Problem creation](#problem-creation)). In this release the commands run in a
problem record: in a study, a verification or a math-solver root they are
refused with `orchestration_conflict`.

An evidence reference in the tables below has one of the shared evidence
shapes: a held artifact descriptor `{path, sha256, size, media_type}`,
`{kind: "source", link}` for a full reading, or `{kind: "record", record_kind,
id, digest}` for an exact record of this store. `digest` is the SHA-256 of the
stored record as canonical JSON: keys sorted, no whitespace between tokens,
non-ASCII characters unescaped. The receipt of `forming` or `form-outcome`
returns the stored record as `result`, and the output folders hold the same
record (a record over 1 MiB is shown there as a summary), so this command
prints the digest of a forming call:

```sh
python3 -c 'import hashlib, json, sys; print(hashlib.sha256(json.dumps(json.load(sys.stdin), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())' < intermediate/problem/forming/forming-1.json
```

A reference whose digest differs from the stored record is refused with
`strategy_evidence_stale`, and a reference to a record that the store does not
hold with `strategy_reference_missing`. A record reference with an `origin`,
`{kind: "record", origin, record_kind, id, digest}`, names a record of a
forming import and is resolved in the snapshot of that import
([Imported forming history](#imported-forming-history)).

The payload of `forming`:

| Field | Content |
| --- | --- |
| `id` | The id of the forming call. `forming_call/<id>` is immutable, and an id is used once: the same request replays its receipt, and another request with that id is `record_conflict`. |
| `question` | `{kind, description, places}`. `kind` and `description` are nonempty text, and the kind is an open value such as `phenomenon`, `thought_experiment` or `field_question`. `places` is a list of `{description, evidence}`, each with at least one evidence reference: the places where a phenomenon appears, each with the reading, search or other evidence that shows it. The kind `phenomenon` needs at least one place. Another kind can leave the list empty. |
| `move` | `{kind, description}`: the kind of step that produced the forms, as an open value with a description. Two kinds carry a further field: `transfer` needs `dictionary`, and `comparison` needs `comparison_count` and `amounts.pairs`. |
| `inputs` | A list, possibly empty, of `{description, evidence}`: what the call worked from, each with at least one evidence reference. |
| `amounts` | `{forms, nearest_alternatives, expected_relations, pairs}`. `forms` is a nonnegative integer. `nearest_alternatives` and `expected_relations` are objects with one nonnegative integer for each form id of this call and no other key. `pairs` is a nonnegative integer or null. The amounts are the performer's report of what was done. The harness records them as reported and does not compare them with `forms`. |
| `dictionary` | Null, or `{source_setting, target_setting, entries}` for a transfer: the two settings, and at least one entry `{source, target, description}` that carries one term of the source setting to the target setting. |
| `comparison_count` | Null, or the number of comparisons that the call made, a nonnegative integer. |
| `forms` | A list, possibly empty, of the forms that the call produced, a form that was set aside at once included (the next table). A call without a form records its question and its stopping reason. |
| `probes` | A list, possibly empty, of `{probe_id, form_id, role}`: a recorded probe that the call used, the form that it concerns or null, and the role `observation` or `origin`. A probe is the origin of a form when the form was chosen with its result in view. A probe that was recorded after the forming call of a form cannot be the origin of that form (`problem_form_origin_late`). The probe keeps its exploratory provenance. |
| `stopping_reason` | Nonempty text: why the call ended. |
| `performer` | `{kind: "session", session_id}` for the session, or `{kind: "model_lane", lane_id, model, effort, prompt, output}` for a model lane outside it: the model and the effort that its runner reports, and its prompt and its output as held artifact descriptors (store each file with `exactory-research artifact`). |
| `usage` | Null, or an object of named measurements, each a nonnegative number or null: the usage as far as it was measured. |

A form of `forms`:

| Field | Content |
| --- | --- |
| `id` | The id of the form, distinct from every form of the record and of the call (`problem_form_id_conflict`). |
| `text` | The text of the tentative statement, as it was stated. |
| `exact` | `true` when the text is an exact statement, `false` when it is not yet exact. |
| `purpose` | `{kind, form_id}`. `expected_statement` with `form_id` null: the statement that the record expects at that time. `alternative` with the id of another form: an alternative of that form, for example a stronger form written to test whether a hypothesis is necessary. |
| `previous` | Null, or `{form_id, reason}` for a form that changes an earlier one: the earlier form, which is a form of an earlier call or one that precedes this form in the call, and the reason for the change. |
| `grounds` | A list, possibly empty, of `{description, evidence}`: what speaks for stating the form. The evidence list of a ground can be empty. |
| `nearest_alternatives` | The ids of the forms that could be posed in place of this one. Each is a form of the record or of the call, and none is the form itself. |
| `expected_relations` | A list, possibly empty, of `{id, description, form_ids}`: a relation that the performer expects of the form, with an id that is distinct within the form, and the ids of the other forms that it concerns, possibly none. |
| `planned_checks` | A list, possibly empty, of `{id, description, contradiction, no_contradiction_reason}`, with ids distinct within the form. Exactly one of the last two fields is text and the other is null: the outcome that would contradict the form, or the reason that no outcome can. |
| `correction_checks` | Empty for a form without `previous`. For a correction, one entry `{check_id, was_made, keeps_prediction, chosen_with_outcome}` for each planned check of the earlier form and for no other check (`problem_form_correction_incomplete`). The three values are booleans: whether the check was made, whether the corrected form keeps its prediction, and whether the correction was chosen with its outcome in view. An entry can also give `execution_id`, the id of the recorded execution that made the check (`invalid_input` when the store holds no such execution). A run stays a test of the corrected statement only through this entry ([The accounts of a value record](#the-accounts-of-a-value-record)). |

The payload of `form-outcome`:

| Field | Content |
| --- | --- |
| `id` | The id of the outcome. `form_outcome/<id>` is immutable, and an id is used once. |
| `form_id` | A form that the record holds (`problem_form_missing`). |
| `target` | `{kind, id}`: `form` with `id` null for the form as a whole, or `expected_relation` or `planned_check` with the id of that item in the form (`problem_form_item_missing`). |
| `standing` | For a form, its standing as a claim: `refuted`, `proved`, `implied_by_known_results`, `found_in_literature`, `supported`, `unresolved`, `without_evidence` or `not_examined`. Null for an expected relation or a planned check. |
| `disposition` | For a form, its disposition in the exploration, `{status, reason, form_id, candidate_id}` with a nonempty reason. `open` and `set_aside` leave both ids null. `superseded` names in `form_id` another form that was recorded later, in a later forming call or later in the same call. `taken_up_as_candidate` names in `candidate_id` a recorded candidate (`strategy_reference_missing` otherwise). Null for an expected relation or a planned check. |
| `lack_of_means` | Nonempty text for the standing `not_examined`: the means that were lacking. Null otherwise. |
| `outcome` | Nonempty text: how the item ended, in words. |
| `evidence` | A list of `{kind, description, standing, date, reference, counterexample}` (below). |
| `reason` | Nonempty text: the reason for the outcome. |

An evidence item of an outcome has a `kind`, which is an open value in words, a
`description`, and:

- `reference`, one of `{kind: "reading", version_id, reading_id}` for a full
  reading (`problem_value_reading_missing` otherwise), `{kind: "computation",
  execution_id, output_id, formal_check}` for an admitted, reconciled and
  assessed computation (`problem_value_computation_missing` otherwise), a result
  reference in either shape of the value evidence
  ([Problem creation](#problem-creation)), `{kind: "probe", id}` for a
  recorded probe, or `{kind: "record", origin, record_kind, id, digest}` for a
  record of a forming import;
- `standing`: `admissible` for a reading, a computation or a result, and
  `exploratory` for a probe (`problem_form_evidence_standing` otherwise). An
  imported record has the standing that it has in its source
  ([Imported forming history](#imported-forming-history));
- `date`: the day on which the fact became available, as `unknown`, `YYYY`,
  `YYYY-MM`, `YYYY-MM-DD` or a timestamp with a timezone, in the precision that
  the source gives;
- `counterexample`: text that states the counterexample, or null.

The standing of a form needs evidence as follows. `proved`,
`implied_by_known_results` and `found_in_literature` need at least one
admissible item, and `supported` needs at least one item of either standing
(`problem_form_evidence_missing`). `refuted` needs an admissible item, or an
item whose `counterexample` states the counterexample
(`problem_form_evidence_missing` without evidence,
`problem_form_counterexample_missing` with exploratory evidence that exhibits
none). The record then holds `refutation_basis`: `admissible_evidence`, or
`exploratory_computation` for a refutation that rests on an exhibited
counterexample. `unresolved`, `without_evidence` and `not_examined` need no
evidence. A value paragraph cannot cite a probe, so a refutation that a value
case needs rests on admissible evidence.

A later outcome of the same form and target supersedes the earlier one, which
stays in the record: the later record names it in `previous`. `status` counts
each form by the standing and the disposition of its latest outcome:
`forming: {calls, forms, by_standing, by_disposition, without_outcome,
outcomes}`, where `without_outcome` is the number of forms that have no outcome
as a whole form and `outcomes` the number of outcome records.

Refusals of the two commands:

| Code | Cause and next action |
| --- | --- |
| `orchestration_conflict` | The workspace holds no problem record. Run the command in a workspace that `problem-init` opened; in this release the forming commands run in a problem record. |
| `invalid_input` | A field is missing or extra, or has another type or value than the tables state. The message names the field and the repair, for example the `dictionary` of a transfer, the counts of a comparison, the places of a phenomenon, or the two fields of a planned check. |
| `invalid_strategy` | An evidence reference has none of the three shared shapes, or a place or an input has no evidence. |
| `strategy_reference_missing` | A cited record, probe or candidate is not in the store (`id`). Record it first. |
| `strategy_evidence_stale` | The digest of a record reference differs from the stored record (`id`). Compute the digest of the stored record. |
| `record_conflict` | The id of the call or of the outcome is in use. Choose a new id; to change the outcome of an item, record a later outcome. |
| `problem_form_id_conflict` | A form id of the call is the id of another form of the record or of the call (`form_id`). Give the form a new id. |
| `problem_form_missing` | A named form is not a form of the record or of the call, or `previous.form_id` names a form that does not precede this one (`form_id`). |
| `problem_form_correction_incomplete` | `correction_checks` does not hold exactly one entry for each planned check of the earlier form. |
| `problem_form_origin_late` | A probe with the role `origin` was recorded after the forming call of the form. Give it the role `observation`. |
| `problem_form_item_missing` | `target.id` is no expected relation or planned check of the form (`kind`, `id`). |
| `problem_form_evidence_standing` | An evidence item names another standing than its reference has. |
| `problem_form_evidence_missing` | The standing needs evidence that the outcome does not cite. Cite it, or record `unresolved` or `without_evidence`. |
| `problem_form_counterexample_missing` | `refuted` rests on exploratory evidence that states no counterexample. State it in `counterexample`, or cite admissible evidence. |
| `problem_value_reading_missing`, `problem_value_computation_missing`, `problem_value_result_missing`, `problem_result_unbound`, `problem_result_superseded`, `problem_result_missing`, `problem_result_extension_unexplained` | A cited reading, computation or result is not valid evidence; each code has the meaning that it has for value evidence. |

Two refusals of other commands concern forming records:
`problem_candidate_forming_missing` when `problem-candidate` records a candidate
of kind `phenomenon` whose evidence cites no forming call, and
`problem_slate_review_required` when `problem-slate` selects a candidate formed
in the record without a slate review.

A record that holds no forming record keeps the `status` report, the output
folders and the digests of 0.82.0.

### Imported forming history

`forming-import` is the command of the Ideate tool that gives a problem record
the forming history of another problem record whose candidates it takes up
([math-conjecturer Exploration](../skills/math-conjecturer/SKILL.md#exploration),
"The forming history of a candidate taken up in another record"). The command
runs in the destination, and the other record is the source.
`problem-candidate-transfer` records the same move in the source (the table
under [Problem creation](#problem-creation)). Both take the ordinary `--file`,
`--expected-revision`, `--request-id` and `--check` flags, and
`exactory-research example forming-import` and `exactory-research example
problem-candidate-transfer` print the payloads. With `--check` and a reachable
source, `forming-import` validates the complete import and writes neither
store. A record without an import behaves as before.

The payload of `forming-import`:

| Field | Content |
| --- | --- |
| `id` | The id of the import. `problem_forming_import/<id>` is immutable, and an id is used once: the same request replays its receipt, and another request with that id is `record_conflict`. |
| `source_workspace` | The absolute path of the source workspace (`invalid_input` otherwise). The source is another problem record. This workspace, an alias of it, a copy of this record and a source whose own imports lead back to this record are `problem_forming_self_import`. |
| `source_revision` | The current revision of the source, a nonnegative integer. Another revision is `problem_forming_source_revision_mismatch`, with `source_revision` and `expected_source_revision`. |
| `source_candidate_ids` | A nonempty list of candidate ids of the source: the candidates that this record takes up (`problem_candidate_missing` for an id that the source does not hold). |

What the command does. It opens the source store under a read guard and
authenticates its history as the carry does, and in addition for the forming
records: every forming call, outcome, probe, probe intent, plan, run and
assessment of the source kept its first bytes and its creation revision. It
verifies the imports and the carries that the source itself holds, and it
requires the imports of the source to be fresh. It copies the source's SQLite
snapshot, with the artifacts that the snapshot needs, into this record's
artifact store as the `proof`, in the form of a carry proof
([Carried objections](#carried-objections)). Then it appends
`problem_forming_import/<id>` with the payload fields, `protocol`
(`exactory-problem-forming-import-v1`), `source_origin`, `destination_origin`,
`proof`, `recorded_revision`, `recorded_at` and these computed parts:

- `members`: one entry for each record of the source that this record can
  cite. These are the forming calls, the outcomes, the probes with their
  intents, the plans, the runs and the assessments of the whole exploration of
  the source, and the records that they cite: the search of a test declaration
  with its source records, the statement that a plan names as its objective,
  the admission and the outcome of a run, the reading of an outcome with its
  work and its bundle, and the attempt and the solver snapshot of a result
  that an outcome cites. An entry is `{origin, record_kind, id, digest, date,
  revision, standing, availability, form_ids, forming_call_id}`. `origin` is
  the identity of the source record. `date` is the original date of the record,
  or `unknown`. `standing` keeps what the record is in its source: for an
  outcome, the standing of its form as a claim, its disposition and the
  standing of each evidence item; for a run, its status, its plan, the checks
  of the source's accounts that rest on it, the statement digest of its plan
  and the assessed status of each failure signal; for a plan, its test
  declaration; for a solver snapshot, its acceptances; and `exploratory` for a
  probe. `availability` gives, for an outcome, a run, a reading and a solver
  snapshot, the revision and the date at which the evidence became available
  in its source.
- `history`, `checks` and `counts`: the forms from which the named candidates
  descend, with their outcomes; the runs on the texts of the statements that
  formulate those candidates; and the counts for that lineage and for the
  whole exploration of the source. They have the shape of the derived parts of
  a value record ([The accounts of a value record](#the-accounts-of-a-value-record)).
- `forming`: the counts of the forming records of the source, as `status`
  reports them.
- `computations`: every probe and every run of the source, as `{kind, id,
  question, revision}`.
- `candidate_digests`: the digest of each named candidate.
- `inherited`: one entry for each record that the source itself imported from,
  with the members, the history, the counts, the checks and the computations of
  that earlier source.
- `sources`: the link to the source and to each record that the source
  imported from, as `{origin, workspace, source_revision, candidate_ids, cut}`.

No record of the source becomes a record of this store, and the source is not
written. The import record appears in no `final/` or `intermediate/` view. The
receipt of the command returns it, and the report of the record lists it.

Citing an imported record. A reference is `{kind: "record", origin,
record_kind, id, digest}`, with the four values copied from an entry of
`members`. It is resolved in the retained snapshot of the latest import that
lists the record. A reference that no import lists is `problem_forming_reference_missing`
(`origin`, `record_kind`, `id`), and another digest is
`strategy_evidence_stale`. The reference is accepted:

- in `source.evidence` of a candidate. An imported forming call or outcome
  counts as a forming record: it meets the rule of the source kind
  `phenomenon`, and a slate that selects the candidate needs a slate review
  (`problem_slate_review_required`);
- in the evidence of a place, an input or a ground of a forming call, and in
  the `sources` of a `refuted_conjectures` entry;
- as the `reference` of an evidence item of `form-outcome`. The item names the
  standing that the member has in its source (`problem_form_evidence_standing`
  otherwise): `exploratory` for a probe and for an outcome whose evidence is
  exploratory, and `admissible` for an outcome with an admissible item, for a
  run with an assessment, for a reading, and for a solver snapshot whose
  accepted result an outcome of the source cites. Another member, for example a
  forming call, is `problem_forming_evidence_invalid`;
- in `evidence` of an entry of the accounts. The admissible kinds of the last
  item are accepted in every entry, and the exploratory kinds only in a check
  whose standing is not `test` (`problem_forming_evidence_invalid` otherwise).
  A `replacement` entry is the exception: it rests on a reading, a computation
  or a result of this store (`problem_replacement_evidence_missing`);
- in the evidence of a review, by copying the `cite` object that the packet
  delivers with the record.

The history rule of a gain claim and of a passed test
([The accounts of a value record](#the-accounts-of-a-value-record)) accepts an
imported forming call or outcome that the candidate cites. For evidence of
this store and for the plan of a test, the import was recorded before that
evidence or that plan. For imported evidence, the forming call of the form
precedes the evidence in its source, by revision and by date. The correction
chain that the replacement part and the derived `history` read holds forms of
this store only.

What the harness derives and delivers. For a value record with accounts,
`derived_accounts` gains `inherited`: for each origin, `{origin,
source_revision, history, checks, counts}` of the latest import, so that the
counts of the source stand beside the record's own and no source is counted
twice. The `earlier_computations` beside a test include the probes and the runs
of each source that were imported before the plan, each with its `origin`. The
problem evidence carries `forming_imports`, the origin and the revision of
each import, and its `account_records` carry every imported record as
`{record, cite, date, standing}`, with artifact references as descriptors. The
result coverage check requires the request of a review to carry them
(`problem_evidence_unassessed`). A slate packet carries
`problem_forming_imports`, with the same records. The reviewer paragraphs of
0.84.0 do not describe these parts; a reviewer reads the origin, the date and
the standing from each delivered record.

Freshness. The record stays linked to each source. `gate post`, `status`,
`export --kind problem` and `problem-decision` with `post` open every linked
source under a read guard and compare its current forming history with the
recorded `cut`, which covers the members, the history, the counts, the checks,
the computations, the named candidates and the source's own imports. A source
record of another kind, such as a new search, a review, a transfer or a
report, changes no cut. A changed cut is `problem_forming_refresh_required`
(`source_workspace`, `source_revision`), and an unopenable or replaced
workspace is `problem_forming_source_unavailable` (`source_workspace`,
`cause`). `gate post` lists both as obligations, and `problem-decision` with
`post` and the export refuse. Record a new import under a new id; the earlier
import and its snapshot stay, and a later import of the same origin takes the
place of the earlier one in the derived parts and in the delivery. A review
assignment opens no source of an import, and `problem-posted` opens none
either. After a post, `status` lists the finding under `after_post`, and the
post stands. `record-check` verifies every import from its retained snapshot
(`problem_forming_imports` in the `integrity` result) and opens no source
workspace.

Refusals of `forming-import` and of a reference to an imported record:

| Code | Cause and next action |
| --- | --- |
| `orchestration_conflict` | The workspace holds no problem record. Run the command in the destination record. |
| `invalid_input` | A field is missing or extra, `source_workspace` is not an absolute path, `source_revision` is not a nonnegative integer, or `source_candidate_ids` is empty. Send exactly the four fields. |
| `stale_revision` | `--expected-revision` is not the destination's current revision. Read it and retry. |
| `record_conflict` | The import id is in use. An import record keeps its source cut, so run the import under a new id. |
| `problem_forming_self_import` | The source is this workspace or an alias of it, a copy of this record, or a record whose own imports lead back to this record. Name another record. |
| `problem_forming_source_unavailable` | The source workspace, or a workspace that the source links to, cannot be opened as the problem record that the import names (`source_workspace`, `cause`). Restore it; for a moved source, name its new path in a new import. |
| `store_busy` | A linked store is in use. Retry after the active operation ends. |
| `problem_forming_source_revision_mismatch` | `source_revision` differs from the source's current revision (`source_revision`, `expected_source_revision`). Read it with `status` in the source, and run the import again. |
| `problem_candidate_missing` | A named candidate is not in the source. Name candidates that the source holds. |
| `problem_forming_provenance_invalid` | A forming record of the source changed after it was recorded, or has a creation revision other than the revision of the event that created it; or a stored import does not reproduce from the snapshot that it retains. Restore the damaged store and its artifacts from an intact copy. |
| `artifact_missing`, `artifact_corrupt` | An artifact of the source, or of a retained snapshot, is missing or has other bytes, for example the prompt of a model lane of a forming call. Restore the exact bytes that the descriptor names. |
| `problem_forming_refresh_required` | The forming history of a linked source changed after its import, or, during an import, an import that the source itself holds is stale (`source_workspace`, `source_revision`). Run `forming-import` again under a new id with the current revision; refresh the source's own imports first. |
| `problem_forming_sources_exceeded` | The import would link this record to more than 32 source records, counting the records that each source imported from (`limit`). Nothing is recorded. Stop and report it. |
| `problem_forming_origin_conflict` | Two copies of one source give different bytes for the same original record (`source_workspace`). Import only from the copy that continues the history of the earlier imports. |
| `problem_forming_reference_missing` | A reference names an origin, a record kind and an id that no import of this record lists. Copy the reference from an entry of `members`. |
| `strategy_evidence_stale` | The digest of a reference differs from the imported record (`id`). Copy the digest from the entry of `members`. |
| `problem_forming_evidence_invalid` | An imported record is cited where it is no evidence of the needed standing: a member that is no reading, assessed run, cited solver snapshot or outcome with evidence, or an exploratory member in an entry that needs admissible evidence. Cite an admissible member, or enter the fact as a check that is not a test. |
| `problem_form_evidence_standing` | An evidence item of an outcome names another standing than the imported record has in its source. Give `exploratory` or `admissible` as the member's `standing` shows. |

### The test declaration of a plan

A plan of a problem record can declare its run as a test of the current
statement. `cycle` takes one optional field, `test_declaration`, and freezes it
with the plan, before the execution ([Run](research-tools.md#run)). A plan
without the field keeps the bytes, the digest and the meaning that it had in
0.82.0, in a problem record and in a study. Outside a problem record, a plan
with the field is refused with `orchestration_conflict`.

| Field | Content |
| --- | --- |
| `alternative` | The alternative that the test tells against. The record holds it before the plan. `{kind: "neighbour", text}` names a declared neighbour of the current statement by its exact text. `{kind: "form", record_kind: "forming_call", id, form_id}` names a form: `id` is a recorded forming call, and `form_id` is a form of that call. An alternative that the record does not hold is `problem_test_alternative_missing`, and another shape is `invalid_development`. |
| `excluded_outcome` | Nonempty text: the outcome that this alternative excludes. The outcome that would contradict the statement stands among the `failure_signals` of the plan. |
| `prior_knowledge` | A list with at least one entry (`invalid_development` for an empty list) of `{source: {kind, description}, known}`, with three nonempty texts: the account of what the performer knew about the outcome when the plan was frozen, by source. The source kind is an open value. The described kinds are `record_computations`, `inherited_computations`, `external_computations`, `literature_and_tables` and `planning_model_recall` ([Run](research-tools.md#run)). The harness records the account as the payload gives it. |
| `search_id` | The id of a recorded search: the search for the outcome in print. The store holds the search before the plan is recorded (`problem_test_search_missing` otherwise). |

The declaration grants no credit by itself. A run is a test of a text of the
statement when the accounts of a value record enter it as a check with the
standing `test`, and that check meets the rule of a test
([The accounts of a value record](#the-accounts-of-a-value-record)). The result
packet delivers the plan with its declaration, and the search, as records.

### The accounts of a value record

The accounts are a part of the value record, beside the motivation and the
current state. They keep the conjectured answer apart from the question, and
they enter each fact once: what each answer would settle, what speaks for the
conjectured answer, and why the statement was posed and not one of its nearest
alternatives ([math-conjecturer The accounts](../skills/math-conjecturer/SKILL.md#the-accounts)).
`problem-value` requires `accounts` in every new value record
(`problem_accounts_required` otherwise). A value record that an earlier release
recorded has no accounts. It is validated, digested, projected and gated under
the contract of its release, and it needs no change.

The payload of `problem-value` is `{id, statement_id, accounts, motivation,
current_state}`. `statement_id` names the current statement
(`problem_statement_missing` otherwise). `exactory-research example
problem-value` prints a payload of the first tier.

The accounts have two tiers. Every problem holds the first tier: `answer`,
`answer_evidence`, `boundaries`, `explanatory_use`, `alternatives`, `strength`,
`relations` and `refuted_conjectures`. The second tier, `checks`, `gain` and a
nonempty `search_coverage`, is required when the record claims a gain or enters
a passed test (`problem_accounts_tier_two_missing`). `replacement` is required
in its two cases (below). A key outside these twelve is `invalid_input`.

Rules that hold for the entries of every part:

- `id` is nonempty text, distinct within the accounts. The id `statement` is
  reserved for `credited_to` (`problem_account_id_conflict`).
- `evidence`, where a part has it, is a list, possibly empty, of references:
  `{kind: "reading", version_id, reading_id}` for a full reading,
  `{kind: "computation", execution_id, output_id, formal_check}` for an
  admitted, reconciled and assessed computation, or a result reference in
  either shape of the value evidence ([Problem creation](#problem-creation)).
  Each reference is validated with the codes that it has as value evidence. A
  probe, `{kind: "probe", id}`, is accepted only in a check whose standing is
  not `test`. Another entry that rests on a probe, directly or through a cited
  entry, is refused with the code of its part.
- `evidence_ids`, where a part has it, is a list, possibly empty, of the ids of
  other entries. The entry then rests on their evidence as well as on its own.
  An id that no entry holds is `problem_account_reference_missing`, and
  references that lead back to their entry are
  `problem_account_reference_cycle`.
- `scope`, in `answer_evidence`, `boundaries`, `explanatory_use` and
  `relations`, is nonempty text: the scope within which the fact holds.
- `date`, in those four parts and in `checks`, is the day on which the fact
  became available: `unknown`, `YYYY`, `YYYY-MM`, `YYYY-MM-DD` or a timestamp
  with a timezone. A reading gives the year of its work, and a result gives the
  day of its acceptance. When every reference that the entry rests on gives a
  date, the harness records the latest of them as `date`, also in place of a
  value that the payload gives. Otherwise the payload gives `date`
  (`problem_account_date_missing`). A computation reference gives no date.

The first tier:

| Part | Entry | Rules |
| --- | --- | --- |
| `answer` | `{id, status, text, evidence_ids}` | The conjectured answer. `status` is `none`, `forecast` (an answer stated without grounds) or `conjectured`. `text` is null for `none` and nonempty text otherwise. A `conjectured` answer names in `evidence_ids` at least one `answer_evidence` entry with the direction `for` (`problem_answer_evidence_missing`). |
| `answer_evidence` | A list of `{id, kind, direction, alternative_id, fact, scope, evidence, evidence_ids}` with the optional `date` and `check_id` | The evidence for or against the answer. `kind` is an open value `{kind, description}`. The described kinds are a theorem in a neighbouring setting, a theorem on a subclass, a derivation from or an equivalence with a statement on independent grounds, `passed_test`, and a doubted consequence. `direction` is `for` or `against`. `alternative_id` names the `alternatives` entry that the fact tells against, with a role other than `no_alternative` (`problem_answer_alternative_missing`). The entry rests on at least one reference (`problem_answer_evidence_missing`). An entry of the kind `passed_test` names in `check_id` a check with the standing `test` and `passed: true`, and both name the same alternative (`problem_test_run_invalid`). |
| `boundaries` | A list of `{id, fact, scope, evidence, evidence_ids, argument}` with the optional `date` and `check_id` | The evidence that locates boundaries: each fact that shows a relaxation or a hypothesis to be necessary, among them a refuted stronger alternative that was written to test a hypothesis. `argument` is the argument that ties the fact to the statement posed, or null. The entry rests on at least one reference (`problem_boundary_evidence_missing`). |
| `explanatory_use` | A list of `{id, fact, scope, evidence, evidence_ids, answer_argument}` with the optional `date` and `check_id` | The evidence of explanatory use: each fact that shows what the statement organizes. `answer_argument` is the argument of the fact for the answer, or null. The entry rests on at least one reference (`problem_explanatory_evidence_missing`). |
| `alternatives` | A list of `{id, neighbour, form_id, role}` with the optional `relation_id`, `surplus`, `reason` and `search_ids` | The nearest alternatives. Every declared neighbour of the current statement has exactly one entry whose `neighbour` is its exact text (`problem_neighbour_role_missing`); its `form_id` is null or a recorded form. An alternative that was named after the formulation has `neighbour` null, a recorded form in `form_id` (`problem_alternative_missing`) and nonempty `search_ids`, the recorded searches that give its status. Every search that an entry names in `search_ids`, also for a declared neighbour, is a recorded search (`problem_account_search_missing`). `role` is `stronger`, `weaker`, `same_assertion` (the same assertion with another constant or exponent), `other_assertion` (another assertion about the same objects) or `no_alternative`. `stronger` and `weaker` name in `relation_id` a `relations` entry whose `alternative_id` is this entry (`problem_alternative_relation_missing`). `stronger` and `no_alternative` give a nonempty `reason`: why the stronger alternative was not posed, or why the neighbour is no alternative. `weaker` gives `surplus` (below; `problem_surplus_missing` otherwise). |
| `strength` | Exactly four entries `{id, head, description, alternative_ids, no_alternative_reason}` | The parts of the strength, one entry for each head: `range_of_instances`, `constants`, `exponents` and `exactness`. `alternative_ids` names the `alternatives` entries that relax the part, each with a role other than `no_alternative` (`problem_alternative_missing`). An entry that names none gives `no_alternative_reason`, the reason that no alternative relaxes the part; the field is null or text otherwise (`problem_strength_incomplete` for a missing head, a repeated head or a part without either). |
| `relations` | A list of `{id, relation, target, alternative_id, standing, scope, evidence, evidence_ids, earlier_statement, consequence}` with the optional `date` | What is at stake: each relation of the statement to a named statement or body of facts. `relation` is `implies`, `follows_from`, `equivalent_to`, `incompatible_with`, `strengthens`, `generalizes` or `presents_anew`. `target` names the statement or the body of facts. `alternative_id` is null, or the `alternatives` entry that the relation concerns (`problem_alternative_missing`). `standing` is `established` or `expected`. An established relation rests on at least one reference (`problem_relation_evidence_missing`). `earlier_statement` is a boolean: whether the target is an earlier statement of the same content. `consequence` is null, or the crediting of a consequence (below). |
| `refuted_conjectures` | A list of `{id, statement, identified_by, sources, evidence_ids}` | Each refuted conjecture that the question concerns, possibly none. `identified_by` is `author`, `search` or `reviewer`. `sources` is a list of evidence references in the shared shapes, which identify the conjecture; it is nonempty unless `identified_by` is `author`. `evidence_ids` names the entries that hold the refuting evidence. Every entry needs a `replacement` entry (`problem_replacement_missing`). |

The `surplus` of a weaker alternative is `{description, status, evidence_ids,
relation_ids}`: what the statement asserts beyond the alternative, and whether
that surplus is `supported`, `used`, `supported_and_used` or `neither`. A
weaker alternative without the field, or with a null `surplus`, is refused
with `problem_surplus_missing`. A surplus with the status `supported` or
`supported_and_used` names in `evidence_ids` an `answer_evidence` entry with
the direction `for` whose `alternative_id` is this same alternative
(`problem_surplus_support_missing`). A surplus with the status `used` or
`supported_and_used` names in `relation_ids` a `relations` entry whose
standing is `established` and whose consequence is credited to the statement
(`problem_surplus_use_missing`).

The `consequence` of a relation is `{assertion, instances_or_translation,
credited_to, weaker_derivations}`. `assertion` is the assertion of the
statement that the derivation uses. `instances_or_translation` is the family of
instances or the proved translation. `credited_to` is the alternative that the
consequence is credited to, which is the weakest named alternative that gives
it: the id of an `alternatives` entry, or `statement`
(`problem_alternative_missing` otherwise). `weaker_derivations` is a list of
`{id, alternative_id, status, reason, evidence_ids}` with the status `derived`,
`not_derived` or `not_attempted` and a nonempty `reason`. A consequence that is
credited to the statement has exactly one entry for each alternative with the
role `weaker` (`problem_consequence_comparison_missing`).

The second tier:

| Part | Entry | Rules |
| --- | --- | --- |
| `checks` | A list of `{id, target, contradiction, no_contradiction_reason, result, standing, passed, alternative_id, evidence, evidence_ids, independence}` with the optional `date` and `lineage` | Each check that the record made or that a reader can make. `target` is `{kind, description}` with the kind `instance`, `family`, `consequence` or `asymptotic_regime`. Exactly one of `contradiction` (the outcome that would contradict the statement) and `no_contradiction_reason` (the reason that no outcome can) is text, and the other is null. `result` is text or null. `standing` is `planned`, `observation`, `origin` or `test`. `passed` is a boolean, and it is `true` only for a test. `alternative_id` is null or the id of an entry. `independence` is null, or the independence of a test. A check with the standing `test` meets the rule of a test (below). A record of the first tier can also hold this part. |
| `gain` | Null, or `{id, form, claim, relation_ids, separate_grounds, conjunction, establishing_ids, new_object_justification}` | Null when the record claims no gain. `form` is `common_structure`, `explanatory_relation` or `proved_advantage`. `claim` states the gain. `relation_ids` is nonempty: the relations that the statement gathers, each established, and at least one a consequence that is credited to the statement (`problem_gain_relations_invalid`). `separate_grounds` is a list of `{id, relation_id, evidence_ids}`: for a gathered relation, the entries whose evidence is the work that states or studies the gathered statement for reasons of its own (`problem_gain_relations_invalid` for a relation that the gain does not gather, `problem_gain_evidence_missing` for an entry without evidence). `conjunction` is `{status, description, evidence_ids}`: how the statement stands to the conjunction of what it gathers, `proved_equivalent`, `proved_stronger` or `not_known`; a proved status rests on evidence (`problem_gain_conjunction_evidence_missing`). `establishing_ids` is nonempty: the entries whose evidence establishes the gain in its form. That evidence is admissible, so it holds no probe (`problem_gain_evidence_missing`). `new_object_justification` is text or null: the justification of a new object where the statement defines one. |
| `search_coverage` | A list of `{id, purpose, search_ids, unreached}` | `purpose` is `earlier_statements` (earlier statements of the same content), `alternatives`, or `gathered_as_case` (a work that presents one gathered statement as a case of another). `search_ids` is nonempty and names recorded searches (`problem_account_search_missing`). `unreached` is nonempty text: what the searches could not reach, such as lectures and letters. |

A gain claim and a passed test need a history. The candidate that the statement
formulates cites, in `source.evidence`, a forming call or a form outcome
([Forming records](#forming-records)). A cited forming call counts with its
forms of the purpose `expected_statement`, and a cited outcome with its form.
One of these forms was recorded before the plan of the test, or before the
evidence that establishes the gain: before the recorded revision of a result, a
computation or a reading of this store, and before the date of that evidence
where it gives one (`problem_accounts_history_missing` otherwise).

`replacement` is a list of `{id, refuted_id, form_id, evidence_ids, account}`.
An entry names exactly one refuted statement and sets the other field to null:
`refuted_id` is the id of a `refuted_conjectures` entry
(`problem_account_reference_missing`), and `form_id` is a form that an outcome
of this record gives the standing `refuted` (`problem_form_missing`).
`evidence_ids` names entries whose evidence shows the refutation; that evidence
is admissible, so it holds no probe (`problem_replacement_evidence_missing`).
`account` is nonempty text: how the statement stands to the refuted one. The
part is required in two cases (`problem_replacement_missing`, with `form_ids`
and `conjecture_ids`). The first is a refuted form of the purpose
`expected_statement` among the forms that the candidate of the statement
cites and the forms that these change, through `previous`. The second is every
entry of `refuted_conjectures`. A refuted form of the purpose `alternative`
enters `boundaries` and needs no replacement entry.

A paragraph of `motivation` or `current_state` is `{text, evidence}` with the
optional `field_fact`. `evidence` is nonempty. Each reference is
`{kind: "account", id}`, the id of an entry of the accounts. A paragraph with
`field_fact: true` can also cite a full reading directly, as `{kind:
"reading", version_id, reading_id}`, for a fact about the field that no part of
the accounts holds; another direct reference is
`problem_value_account_required`. A paragraph whose cited entries are all
expected relations is refused (`problem_value_expected_only`). The delivered
evidence of a paragraph is the evidence that its cited entries rest on.

Some entries are shortfalls that the harness records without a refusal: a
surplus with the status `neither`, a derivation with the status
`not_attempted` and a relation with the standing `expected`. The reviewers
grade them.

A check entered as a test. A check with the standing `test` gives
`contradiction` and `result`, names in `alternative_id` an `alternatives` entry
with a role other than `no_alternative`, and rests on at least one computation.
Its `independence` is `{status: "outcome_not_known", reason}` with the optional
`prior_knowledge`, a list of findings `{source_index, status:
"outcome_not_known", reason}`: exactly one for each entry of
`test_declaration.prior_knowledge` of the plan, with `source_index` counted
from 0. For each computation that the check rests on, the harness checks the
plan of its run. Each failure below is `problem_test_run_invalid`:

- The plan carries a test declaration, and it was recorded before the run was
  admitted.
- The plan names a statement record whose statement digest is that of the
  current statement. The digest covers `statement` and `resolution_criteria`,
  so a revision that changes only the neighbours keeps the test. For a plan on
  an earlier text, the check gives `lineage: {form_id, check_id}`. The form is a
  form that the candidate cites, its text is the current statement, the text
  of its `previous` form is the statement that the plan names, it has a ground
  with evidence, and its `correction_checks` entry for `check_id` has
  `execution_id` equal to this run, `was_made: true`, `keeps_prediction: true`
  and `chosen_with_outcome: false`.
- The alternative of the check is the one that the declaration names: the same
  neighbour text, or the same form.
- The search that the declaration names is in the store.
- No execution and no probe that the record held before the plan has the
  `question` of the plan, character for character.
- An assessment that was recorded before the value record covers the run. For
  `passed: true`, the plan has at least one failure signal, and the assessment
  gives `not_observed` for each.

The harness compares earlier questions character for character only, and it
does not read what the search found. It does not establish that the outcome was
unknown to the model that planned the run. The reviewers judge these points
([Run](research-tools.md#run)). A run whose outcome was known, or whose
independence is not established, is entered with the standing `observation`.

What the harness derives. For a value record with accounts, the problem
evidence carries `derived_accounts` beside the accounts. The harness computes
it from the records at each projection, and the author does not enter it:

- `checks: {current, earlier}`. `current` lists every execution whose plan
  names a statement record with the statement digest of the value's statement.
  `earlier` lists the executions on the other texts of its lineage, which the
  `revises_statement_id` references give. Each row is `{execution_id, plan_id,
  statement_id, statement_digest, statement, resolution_criteria, question,
  assessment_id, failure_signals, test_ids}`. Each failure signal of the plan
  carries the `status` of the latest assessment: `observed`, `not_observed`,
  or `unresolved` when no assessment gives one. `test_ids` names the checks of
  the accounts with the standing `test` that rest on the execution. A row with
  a test also carries `earlier_computations`: every probe and every execution
  that the record held before the plan, as `{kind, id, question, revision}`.
- `history`: the forms from which the statement descends, in recording order.
  These are the forms that the candidate cites and the forms that these change,
  through `previous`. Each row is `{form, forming_call_id, outcomes}` with the
  latest outcome of the form and of each of its items.
- `counts: {lineage, exploration}`, each `{forming_calls, forms,
  recorded_comparisons, probes, runs}`: for the forming calls of the history,
  and for the whole record.

Currency. `derived_accounts` and the records that the accounts cite enter the
evidence digest. A value record with accounts is stale when the record holds a
later execution on the current text, a later outcome of a form of the history,
or a later delivered snapshot of an objective that an entry cites. The gate
then lists one obligation, `problem_value_stale`, with `value_id` and
`changes`: each change is `{kind, id, reason}` with the reason
`later_run_on_current_text` (kind `execution`), `later_outcome_in_history`
(kind `form_outcome`) or `later_snapshot_of_cited_objective` (kind
`native_attempt`, with `previous_native_attempt_digest`). Record a new value
record whose accounts take each change into account and cite the latest
snapshot. The new record changes the evidence digest, and the result reviews
are refreshed as after any change of evidence. A value record without accounts
has no such obligation: a later snapshot of an objective that it cites is
listed as `problem_result_superseded`.

Delivery. The value part of a result packet carries `accounts` and
`derived_accounts`, and each paragraph carries `delivered_evidence`. The packet
also carries `account_records`, a list of `{record, cite}`: the searches that
`search_coverage` and the alternatives name; the plan of each cited
computation, and the search that its test declaration names; the record
sources of `refuted_conjectures`; the forming calls and the outcomes of the
forms that the alternatives, the replacement entries, a check lineage and the
history name; for each listed run its plan, its execution and its assessment;
the earlier computations beside a test; and the source records of the
delivered searches. In a review delivered in fresh parts, the value part
travels whole in every frame. The result coverage check requires the request
of a review to carry `value` and `account_records` with their current values
(`problem_evidence_unassessed` with the keys under `missing`).

Reviewer paragraphs. A prompt carries an optional paragraph only when its
packet carries the material. The registry is `PARAGRAPHS` in
`research_harness/review_prompt_paragraphs.py`, and the order is fixed: the
carry paragraph, the accounts paragraph, the slate paragraph, the
replacement paragraph and the areas paragraph.

| Paragraph | Roles | Present when |
| --- | --- | --- |
| `exactory-problem-accounts-v1` | Result, in every turn of a series; adjudicator of `A3`, `priority`, `calibration` or `significance`, with the definition of the item | The delivered value record holds accounts. |
| `exactory-problem-slate-forming-v1` | Slate | A current candidate has the source kind `phenomenon`, or its `source.evidence` cites a forming call or a form outcome. The slate packet then keeps those references in the candidate's `source.evidence`. |
| `exactory-problem-replacement-v1` | Result; adjudicator of `A7`, with the definition of the item | The accounts hold a nonempty `replacement`. |
| `exactory-problem-areas-v1` | Bar; result | The record names a further area, so the packet carries `problem_areas` ([Further areas](#further-areas)). |

The accounts paragraph gives the reading of the rubric with the accounts, which
governs where it is stricter than an anchor, the questions on the gain, what
stays unestablished about a test, and where each judgment goes. The response
shapes are unchanged: the assurance `problem_value` covers the accounts, and
the grades and evidence texts of the rubric carry the judgments. A prompt with
paragraphs has the template id of its base template with one suffix for each
paragraph, for example `/accounts-v1/replacement-v1`, and its
`parameters.problem_paragraphs` lists the version ids in order. The frozen
envelope keeps them, so an assignment rebuilds from its own envelope after a
later version of a paragraph is registered. An assignment whose packet calls
for no paragraph keeps the prompt bytes of 0.82.0.

Refusals of `problem-value` for the accounts, beside the codes of the cited
evidence:

| Code | Cause and next action |
| --- | --- |
| `problem_accounts_required` | The payload has no `accounts`. Add the accounts, and cite their entries in the paragraphs. |
| `invalid_input` | A part or a field is missing or extra, or has another type or value than the tables state. The message names the field. |
| `problem_account_id_conflict` | Two entries have the same id, or an entry has the id `statement` (`id`). Give each entry an id of its own. |
| `problem_account_reference_missing` | An entry or a paragraph cites an id that no entry holds (`id`), or `refuted_id` names no `refuted_conjectures` entry. Cite an entry of the accounts, or add it. |
| `problem_account_reference_cycle` | The references of an entry lead back to the entry (`id`). Remove one reference of the cycle. |
| `problem_account_date_missing` | The harness cannot derive the date of an entry from its evidence (`id`). Enter `date`, or `unknown`. |
| `problem_account_search_missing` | A named search is not in the store (`id`). Record the search first. |
| `problem_answer_evidence_missing` | A conjectured answer names no `answer_evidence` entry with the direction `for`, or an `answer_evidence` entry rests on no admissible reference (`id`). Cite the evidence, or record the answer as `none` or `forecast`. |
| `problem_answer_alternative_missing` | An `answer_evidence` entry names no nearest alternative (`id`). Set `alternative_id` to an `alternatives` entry with a role other than `no_alternative`. |
| `problem_boundary_evidence_missing`, `problem_explanatory_evidence_missing` | A `boundaries` or an `explanatory_use` entry rests on no admissible reference (`id`). Cite a full reading, an admitted computation or an accepted result. |
| `problem_relation_evidence_missing` | An established relation rests on no admissible reference (`id`). Cite the evidence, or record the standing `expected`. |
| `problem_neighbour_role_missing` | A declared neighbour has no entry, or more than one, or an entry has another role than the five (`neighbour`). Give each declared neighbour exactly one entry. |
| `problem_alternative_missing` | An entry names an alternative that the record does not hold: an alternative that is neither a declared neighbour nor a recorded form, a `form_id` that names no form, or an `alternative_id`, a `credited_to` or a strength entry that names no `alternatives` entry (`id`). Name a declared neighbour by its exact text, record the form with `forming`, or name an entry of `alternatives`. |
| `problem_alternative_relation_missing` | A stronger or a weaker alternative names no relation whose `alternative_id` is that alternative (`id`). Add the `relations` entry, and name it in `relation_id`. |
| `problem_surplus_missing` | An alternative with the role `weaker` has no `surplus` (`id`). Add `surplus: {description, status, evidence_ids, relation_ids}`: what the statement asserts beyond the alternative, with the status `supported`, `used`, `supported_and_used` or `neither`. |
| `problem_surplus_support_missing` | A surplus with the status `supported` or `supported_and_used` cites no `answer_evidence` entry with the direction `for` against the same alternative (`id`). Cite one, or record another status. |
| `problem_surplus_use_missing` | A surplus with the status `used` or `supported_and_used` cites no established relation whose consequence is credited to the statement (`id`). Cite one, or record another status. |
| `problem_strength_incomplete` | `strength` does not hold exactly one entry for each of the four heads, or an entry names no relaxing alternative and gives no reason (`id`). |
| `problem_consequence_comparison_missing` | A consequence that is credited to the statement does not hold exactly one `weaker_derivations` entry for each weaker alternative (`id`). Add the entries; `not_attempted` is a valid status. |
| `problem_accounts_tier_two_missing` | The accounts claim a gain or enter a passed test, and `checks`, `gain` or a nonempty `search_coverage` is absent. Add the second tier. |
| `problem_accounts_history_missing` | The candidate of the statement cites no form that was recorded before the evidence of the gain or the plan of the test. Record `gain` as null, and enter the check as an observation. |
| `problem_test_run_invalid` | A check with the standing `test`, or an `answer_evidence` entry of the kind `passed_test`, does not meet the rule of a test. The message names the failed condition. Enter the check with the standing `observation` or `origin`. |
| `problem_gain_relations_invalid` | The gain gathers a relation that is not established, gathers no consequence that is credited to the statement, or a `separate_grounds` entry names a relation that the gain does not gather. |
| `problem_gain_conjunction_evidence_missing` | The relation to the conjunction has a proved status and rests on no evidence. Cite the proof, or record `not_known`. |
| `problem_gain_evidence_missing` | No admissible evidence establishes the gain, or a `separate_grounds` entry cites an entry without evidence. Cite the evidence, or record `gain` as null. |
| `problem_replacement_missing` | A refuted expected form in the correction chain of the statement, or a refuted conjecture of the accounts, has no `replacement` entry (`form_ids`, `conjecture_ids`). Add one entry for each. |
| `problem_replacement_evidence_missing` | A `replacement` entry rests on no admissible evidence of the refutation. Cite a full reading, an admitted computation or an accepted result. |
| `problem_form_missing` | `form_id` of a `replacement` entry names no form with the standing `refuted`. Record that outcome with `form-outcome` first. |
| `problem_value_account_required` | A paragraph cites a reference that is no account entry, and it is not a full reading in a paragraph with `field_fact: true`. Enter the fact in its account, and cite the entry. |
| `problem_value_expected_only` | Every entry that a paragraph cites is an expected relation. Establish a relation, or cite an entry that holds a fact. |

### Further areas

A problem record has one subject class, the `area` of its marker, and a
statement, its bars and its post keep that class. A further area is another
`math.*` subject class that the statements of the record concern
([math-conjecturer Further areas](../skills/math-conjecturer/SKILL.md#further-areas)).
`problem-area` records one, with the ordinary `--file`, `--expected-revision`,
`--request-id` and `--check` flags, and `exactory-research example
problem-area` prints the payload.

| Field | Content |
| --- | --- |
| `id` | The id of the area record. `problem_area/<id>` is immutable: the same id with another payload is `record_conflict`. |
| `area` | A `math.*` subject class (`invalid_input` otherwise), other than the record's own class and other than the class of every other area record (`problem_area_duplicate`). |
| `reason` | Nonempty text: which part of that area the field question touches. The bar reviewers receive it before any candidate is shown to them, and the harness does not check its content. |

The record adds `recorded_revision`. The marker `workspace/problem` keeps its
bytes, and the output views show every area at `problem/areas`. The command
checks one condition of the record: no `review_assignment` with the role
`slate` or `result` exists, whether or not it ran. Such an assignment refuses
the command with `problem_area_new_record_required`, because a reviewer of the
record can then hold a candidate. The statement is then taken up in a new
record that names the areas from its opening, with `problem-objections-carry`
for the objections ([Carried objections](#carried-objections)) and
`forming-import` for the forming history
([Imported forming history](#imported-forming-history)).

Delivery. While the record holds an area record, every bar, slate, result and
adjudicator packet of the record carries `problem_areas`, the list of the area
records, beside `area` and `field`. The bar and result prompts then carry the
areas paragraph, `exactory-problem-areas-v1`, with the template suffix
`/areas-v1`. For a bar, the paragraph asks for the standard of each area
separately: for the record's subject class and for each further area, the
active lines, the nearest problems and conjectures, what counts as a
substantial advance, and the meaning of levels 2 and 3. For a result review, it
says that each relation, each earlier statement and each alternative is judged
by the standard of the area that it belongs to, and that every grade is given
by the standard of the record's subject class. The thresholds for posting read
those grades, so no item, level, threshold or response shape changes. A record
without an area record keeps its packets and its prompt bytes.

A bar that was given before an area was recorded. `problem-slate`, a slate
assignment and a result assignment validate that the packet of each bar
delivered every area record. A bar whose packet lacks one is refused with
`problem_bar_calibration_required`, with `review_id` and `area_ids`. Assign
the bar again to the same reviewer with an empty `context`. The assignment
continues that reviewer's bar session and slot, as the calibration refresh of
a version 1 bar does: the packet carries every area and `problem_refresh`, the
earlier bar stays in the history, and the continued bar is the current bar of
its slot. A bar on the version 1 template that also lacks an area needs one
continuation for both. A source-request continuation of a pending bar receives
the areas that were recorded between its turns. Record a slate that names the
continued bars in `bar_review_ids`: a result assignment is refused with the
same code while the latest slate cites a bar that lacks an area. A reviewer
whose current bar covers every area cannot be assigned another bar
(`review_resampling_forbidden`).

Refusals of `problem-area`:

| Code | Cause and next action |
| --- | --- |
| `orchestration_conflict` | The workspace holds no problem record. |
| `invalid_input` | A field is missing or extra, is not nonempty text, or `area` has no `math.` prefix. Send exactly the three fields. |
| `problem_area_duplicate` | `area` is the record's own subject class, or another area record names it. Name a subject class that the record does not hold yet. |
| `problem_area_new_record_required` | A slate or result review of the record has been assigned. Open a new record, name every area there before its bars are assigned, carry the objections, import the forming history, and record the transfer of the candidate in this record with `problem-candidate-transfer`. |
| `record_conflict` | The id holds another payload. An area record does not change; choose a new id for another area. |

### The report of an exploration

`problem-report` records how the exploration of a problem record ended
([math-conjecturer The end of an exploration](../skills/math-conjecturer/SKILL.md#the-end-of-an-exploration)).
It takes the ordinary `--file`, `--expected-revision`, `--request-id` and
`--check` flags, and `exactory-research example problem-report` prints the
payload. The payload is `{id, reopen_when}`, two nonempty texts: the id of the
report, and the finding that would make the exploration worth reopening.
Another field is `invalid_input`: the author enters no computed part. The
report is no gate, it receives no review, and no gate reads it.

The harness computes the report from the records at the revision of the
command and stores it as the immutable `problem_report/<id>`:

| Field | Content |
| --- | --- |
| `cutoff`, `dependency_digest` | The revision of the store at which the report was computed, and the digest of the records that it read (Currency, below). |
| `start` | `{field, kind, description, first_question}`: the field question of the marker, the kind and the description of the start, or null when the marker records none, and the question of the first forming call, or null. |
| `forms` | One row for every form of the record, in recording order: `{form, forming_call_id, move, standing, disposition, outcome, evidence, lack_of_means, item_outcomes}`. `outcome` is the latest outcome of the form as a whole. A form without one has the standing `without_evidence` and the disposition `open`. `item_outcomes` holds the latest outcome of each expected relation and planned check. |
| `imports` | The forming import records of the record, in recording order, each with its source workspace, its members, its history and its counts ([Imported forming history](#imported-forming-history)). The forms and the counts of a source stand here, apart from `forms` and `counts`. |
| `candidates` | One row for every candidate: `{candidate, slate_id, disposition, post_ids, withdrawal_ids, transfers, ended, ending}`. `disposition` is its entry on the latest slate (`selected`, `deferred` with the evidence needed, or `rejected` with the reason), or null. `ending` is `posted`, `withdrawn`, `taken up in another record`, or null. |
| `withdrawals` | Every `withdraw` decision with the candidates that it ends and whether it recorded its target (`target_recorded`). |
| `statements` | One row for every statement record: `{statement, publication, posts}`. `publication` is `not posted`, `posted through the gate` or `posted outside the gate`. Each post is `{post, passed, classification, covered_value_id, problem_evidence_digest, posted_at, time_statement, has_post_fact, after_post, consequences, proof_publication}`. |
| `counts` | `forming` (the counts that `status` reports), `probes`, `runs`, `recorded_comparisons`, `forms_with_evidence` (the standings `refuted`, `proved`, `implied_by_known_results` and `found_in_literature`), `forms_without_evidence`, `forms_not_examined` (each with its `lack_of_means`), `relations: {established, expected}` over the latest value record of each statement, `forming_expected_relations`, `tests: {total, passed, failed, unresolved}` and `posts`, the number of posts of each classification. |
| `tests` | Every run that a value record enters as a check with the standing `test`, with its plan, its execution, its latest assessment and its outcome: `failed` when a failure signal is `observed`, `passed` when the check is marked passed and every signal is `not_observed`, and `unresolved` otherwise. |
| `spent` | `accounts` and `strategy`, the resource accounts as they stand; `reviews`, the measured usage of the reviewer calls; and `reported_forming_usage`, the usage that each forming call reports. Unknown usage stays unknown. |
| `outcome` | One of six texts (below). |
| `reopen_when`, `recorded_revision` | The payload text, and the revision of the record. |

How a candidate ends. A candidate is `posted` when a `problem_post` has the
statement digest of a statement that formulates it. It is `withdrawn` when a
`withdraw` decision names it and was recorded at or after the latest slate. A
`withdraw` decision of 0.85.0 or later records `statement_id` and
`candidate_ids` (the `problem-decision` row). An earlier one records neither,
and it ends a candidate only when every statement recorded before it
formulates that one candidate. A candidate is `taken up in another record`
when a `problem-candidate-transfer` record names it and was recorded at or
after the latest slate. A later slate decides its candidates again, so a
withdrawal or a transfer that precedes the latest slate ends nothing.

The classification of a post. A post that `problem-posted` recorded passed the
gate, and its record holds the evidence digest that its decision covered. The
report reads the value record of that evidence from the saved review packets
(`problem_report_evidence_missing` when no packet delivers it unchanged). The
classification is `a gain was established` when the accounts of that value
record hold a gain, `ordinary` when their `gain` is null, and `kind
unassessed` when the value record holds no accounts. A post that
`problem-post-external` recorded is `kind unassessed`: no completed result
review judged its evidence. A later value record changes no classification.
`after_post` shows it, with the facts that `problem-post-fact` recorded and
the later reviews and adjudications of the statement. `consequences` lists the
established relations of the value records of the statement that are
consequences and cite an accepted result, each with its result references, and
`proof_publication` says that the record does not hold whether those proofs
were posted.

The outcome is computed in this order:

1. `The assessment is incomplete`, when a candidate that the current slate
   selects has not ended.
2. `A statement of the kind was posted`, when a post has the classification `a
   gain was established`.
3. `Statements were posted and the kind of at least one is unassessed`, when a
   post is `kind unassessed`.
4. `Only ordinary problems were posted`, when the record holds a post.
5. `Statements were formed and none was posted`, when the record holds a form
   or a statement.
6. `No statement was formed`.

The current slate is the latest slate while it covers exactly the current
candidates. A record without a current slate skips the first step.

When the report is due. The rule applies to a record whose marker has the
start kind `phenomenon` or `thought_experiment`, and to every record that
holds a forming call. For such a record the report is due once a current slate
exists and selects nothing, or every candidate that it selects has ended.
`status` and `next` then carry one `advisories` entry `{code:
"problem_report_due", reason}` while the record holds no report. The command
refuses a due report while a form has no outcome or the disposition `open`
(`problem_report_forms_open`, with `form_ids`). A report can be recorded before
it is due, and in a record to which the rule does not apply.

Currency. `dependency_digest` covers every record of the kinds that the
report reads: the marker and the area records; the forming calls, the
outcomes, the forming imports and the transfers; the candidates, the slates,
the statements and the selection; the value records, the decisions, the posts,
their time statements and the facts after a post; the reviews, the
adjudications, the review assignments, the review epochs and the records of
the reviewer calls; the attempt records, the delivered solver snapshots and
their pointers; the plans, the runs, the assessments and the probes; and the
resource accounts and budgets. When the latest report has another digest than
the current records give, `status` and `next` carry the entry `{code:
"problem_report_stale", reason, report_id}`. Record a new report under a new
id. A record of another kind changes no digest by itself. A search, a reading
or a computation that charges a resource account changes it through that
account.

Both advisories stand in `advisories` beside `output_views_stale`
([Status, projections, and recovery](#status-projections-and-recovery)), and
`status --summary` and `next --summary` show their code and reason. They
change no `ready`, no obligation, no milestone and no `next` action, and no
gate reads them. The output views show the latest report at `problem/reports`
under `final/`, complete also when it is large, and every earlier report under
`final/history/`.

Refusals of `problem-report`:

| Code | Cause and next action |
| --- | --- |
| `orchestration_conflict` | The workspace holds no problem record. |
| `invalid_input` | `id` or `reopen_when` is missing or blank, or the payload holds another field. Send exactly the two fields. |
| `problem_report_forms_open` | The report is due, and a form has no outcome or the disposition `open` (`form_ids`). Record a `form-outcome` for each, with the disposition `superseded`, `taken_up_as_candidate` or `set_aside`. |
| `problem_report_evidence_missing` | A post passed the gate, and no saved review packet delivers the value record that its evidence digest covered (`post_id`). Restore the store from a copy that holds those packets and that value record unchanged. |
| `record_conflict` | The report id is in use. A report does not change; record a new report under a new id. |

## Development rounds

Managed studies use the compact canonical adapters described above. The detailed
full-payload contract below also describes historical and nonmanaged round
records. Managed currency follows the claim projection rather than the global
preparation report.

A development round is one pass of the paper-level loop: a goal that the current
paper does not yet meet, the literature, cycles and manuscript that pursue it, and
an assessment against that goal. Every record binds the exact current publication
bundle; the loop is bounded by records and it ends only through a recorded exit.

Each complete measurement of a bundle is followed by its `contribution-analysis`: a small
investigation kept as captured responses, the paper's position against the study's current
Grand Challenge record, a disposition of every contribution change the measurement reviewers
named, and the steps toward the challenges. Retain its original captures and record a scoped search when its scientific
question needs one. A later search affects a managed bundle through claim evidence
or declared source impact; an unrelated capture alone leaves it current. `manuscript` refuses to pin the next bundle while the
selected bundle has a complete measurement and no analysis (`contribution_analysis_missing`),
and `round` reads the analysis of the bundle it decides on. A step that is not pursued is
`deferred` unless the evidence rejects it: a candidate that an approved decision rejected never
becomes a goal, and a step's statement is fixed by the analysis of its bundle.

`round` closes the current round on the exact bundle (`round_bundle_mismatch`
otherwise) and decides: `continue` with one pursued candidate and `next`, or `stop`.
`closes` is the current round number, 1 before any admission (`round_number_mismatch`
otherwise). A round of number 2 or more needs its assessment on this bundle first
(`round_assessment_missing`) and every claim of its opening bundle kept, revised or
superseded (`round_claims_dropped` with the claim ids). Every `next_round`
development of the closing round's cycle assessments is disposed of in `carried`
(`carried_development_missing`); a candidate alone does not dispose of it.
`next.number` is `closes + 1`. `next.objective` is the configured objective with
`objective_lineage: null`, or a wider statement with
`objective_lineage: {previous_id, containment}` (`objective_locked` for a wrong
`previous_id`, an unchanged statement or a reused id; `invalid_target` for a
malformed objective or lineage, which includes a changed objective with
`objective_lineage: null`). Containment is the author's recorded assertion, judged
by the round reviewer; the harness does not check it.

The goal states the pursued candidate. Its statement never repeats a candidate
that an approved decision rejected (`round_goal_repeated`), even with a reopening;
a decision the review did not approve binds nothing. It repeats an
earlier round's goal only when `next.reopening: {round_id, reason, evidence}` names
that round (`round_goal_repeated` otherwise). The named round must have been
assessed unsuccessful (`invalid_round` otherwise), and the reopening carries
evidence the earlier decision did not (`round_reopening_unchanged` otherwise).
When the two most recent rounds were both assessed unsuccessful, a goal in the
direction of either round is refused (`round_direction_exhausted`, with their
`round_ids`) unless `next.reopening` names one of those two rounds. When the two
rounds took different directions, every goal needs that reopening.

A `field_change` needs a horizontal goal (`invalid_round` otherwise) and literature
limits with positive `network_requests` and `readings` allowances for the collected
difference (`round_field_change_refused`). `round` does not check its corpus or
category; `round-admit` does, so confirm both against the study's collections
before the review. Any unit of a recorded `development` budget at its limit refuses
`continue` (`resource_budget_exhausted`). A malformed decision fails with
`invalid_round`, except that a `resource_limits` amount or unit fails with
`invalid_input`, and an evidence item of an unknown kind, or a malformed `source` or
`result` item, fails with `invalid_development`. A `source` evidence item needs a full
reading that inspects that link (`reading_missing`). A `review` evidence item names a
manuscript review of the current bundle (`round_evidence_mismatch`).

`round-review` is the independent judgment of one decision. It binds the decision's
digest on the current bundle (`unknown_round_decision`, `round_review_stale`), takes an
assessor who is not a cycle author (`review_not_independent`), and addresses every
check of the decision's kind once. A malformed assessor fails with `invalid_input`
(`review_not_independent` for a kind other than `human` or `agent`), check evidence
is validated as in `round`, and any other malformed field fails with
`invalid_round`. When the bundle has a publication scope, the delivered round packet
(`export --kind round`, below) carries derivative evidence references, with
`derivative`, `original_sha256` and `original_locator`. A check cites such a reference
as an unchanged copy from the delivered packet of the exact decision and bundle under
review: the command maps it to the native evidence that the packet projected and
validates that evidence as `round` does. A reference with any of those three fields
that is not such a copy is refused with `round_evidence_mismatch`. The other operations
that take round evidence refuse every such reference with that code. When a check
cites a projected reference, the record adds `evidence`, every cited item: a projected
item carries the submitted `reference`, its `native_reference` and
`projection_binding` with the digests of the decision and the bundle.
One review per assessor per decision (`round_review_duplicate`); a
closing round with an approved decision on this bundle takes no second approval
(`round_decision_duplicate`). The evaluate skill's section "The round review" states
the questions the assessor answers and the fields it returns.

`round-admit` opens the round of an approved `continue` decision. It needs the
decision and its review (`unknown_round_decision`, `unknown_round_review`), an
approving review of that decision (`round_review_required`), the decision's bundle
still current (`round_review_stale`), the decision closing the current round
(`round_number_mismatch`), and no admitted round without an assessment
(`round_active`). A `field_change` goal names the corpus of one of the study's
collections and a `primaryCategory` that none of them has
(`round_field_change_refused`). It charges one round to the `development` account
(`resource_budget_exhausted` when the recorded `rounds` limit has no room, for example after the
budget was lowered since the decision), applies a widened objective (the
configuration target, `research_objective` and `objective_lineage` records), and
stores the opening state the round is judged against: the bundle and its claim ids,
the selected search per purpose, the ids of the full-text requirements that count, the cycle ids, the
full reading count and the resource accounts. The `round-admit` receipt carries the
admitted `goal` with its success criterion and stop condition ids. `status`, `gate
round` and `export --kind round` do not report the goal while the round runs, so
keep the receipt for `round-assess`.

While a round is active, `gate round` carries its obligations:
`round_search_missing` for each consequence purpose (`downstream`, `next_step`,
`exemplars`, `changes`) whose selected search is the opening one,
`round_exemplar_missing` until a `require-fulltext` with purpose `exemplar` is
recorded in the round (its full reading is then owed under the existing
`fulltext_reading_missing`, which `status` and `gate foundation` report),
`round_cycle_missing` until a cycle planned in the round has a recorded assessment,
`round_claims_dropped` and `round_claim_missing` once a bundle is pinned, and
`round_assessment_missing`. `gate foundation`, and through it the preparation and
readiness obligations of `status`, carry only `round_search_missing` and
`round_exemplar_missing`. The other round obligations enter the status obligations
at the `evaluate` stage. Before that stage, `status` reports the round gate only
through `round.obligations`, the count of every obligation of that gate.
Consequence searches keep the shape and checks of the five foundation purposes. The
manuscript keeps every opening claim id; a changed claim carries
`revised: {previous, reason}` and a withdrawn one `superseded: {reason}`.
`manuscript` refuses a malformed marker or both markers on one claim
(`publication_claims_missing`). A rewritten text without a marker counts as dropped,
and a superseded claim does not count as new. A new or changed `revised` marker
states, in `revised.previous`, the exact claim text of the bundle that opened the
round. A claim with unchanged text keeps the identical `revised` marker that the
opening bundle carries for it, and that marker passes with the `previous` it
has. Any other `revised` marker on a claim of the opening bundle whose
`previous` is not the opening text is `round_claim_revision_mismatch`, with the
`claim_ids`. A claim id outside the opening bundle is not checked. `gate round`
reports the mismatch as an obligation, and `round` refuses the closing decision
with that code.

`round-assess` judges the admitted round on the exact current bundle
(`unknown_round`, `round_bundle_mismatch`; `invalid_round` for a round that is not
the latest admitted one; `round_already_assessed` for a second assessment on the
same bundle). Each success criterion and stop condition id of the admitted goal is
judged exactly once (`invalid_round` otherwise). The harness derives and stores with
the record the round's new, revised, superseded, changed and dropped claim ids, the
consequence purposes whose selected search changed since admission, whether an
exemplar requirement was recorded in the round, the cycles planned and assessed
since admission, the count of full readings since admission, its resource usage,
and the bundle's review and prediction medians and spreads. A round with no
`observed` success criterion is unsuccessful; a round without a new claim, the four
searches, the exemplar or an assessed cycle is unproductive and therefore
unsuccessful. An observed stop condition does not by itself make the round
unsuccessful. The medians are information for the user and the round assessor; no
rule reads them.

`manuscript-prediction` records one blind assessor's cohort prediction on the exact
bundle (`publication_review_stale` otherwise): the percentile the paper is expected
to reach in the study's frozen cohort, in the shape the market's verdict carries.
Each blind measurement reviewer returns the rubric core and, separately, the
prediction and its reasons; the author gives the reviewer the study's cohort and adds
`id`, `bundle_digest`, `blind: true` and `assessor` when recording it. `status --summary`,
`round-assess` and the round packet report the review and prediction medians.

The measurement pairs reviews and predictions by assessor identity on the exact
bundle. Identity matching normalizes case and whitespace. Publication-only
reviewers remain outside this group. `measurement.complete` is true when exactly
three distinct prediction assessors each have a review. A bundle takes three
predicting assessors, so a complete measurement stays complete; an incomplete
group, or an ambiguous one recorded before this rule, retains its counts but
reports null medians and spreads. Earlier stored round assessments
retain the measurement values that their original version computed.

`gate round` evaluates, in order: the current bundle (`publication_bundle_missing`,
`readiness_required`, `publication_readiness_stale`, `publication_artifact_changed`);
for an admitted round, while it is active (admitted without an assessment) its
productivity obligations (`round_search_missing` per consequence purpose,
`round_exemplar_missing`, `round_cycle_missing`, `round_claim_missing`) and
`round_assessment_missing`, and once it is assessed an assessment that binds this
bundle (`round_assessment_missing`); in both cases its claims continuity
(`round_claims_dropped`); then the bundle's complete measurement
(`manuscript_measurement_missing`) and its contribution analysis
(`contribution_analysis_missing`); then the decision closing the current round
(`round_decision_missing`), its approving review (`round_review_missing`,
`round_review_pending`) and, for `continue`, the admission
(`round_admission_missing`). An exhausted `development` budget
(`resource_budget_exhausted`) is reported beside each of these decision steps while
one is pending, and no longer once a `stop` is approved. While the selected bundle
is not current, the gate still reports `contribution_analysis_missing` for a measured
bundle without its analysis, directly after the bundle's own obligation, because the
next pin waits for it. The report carries
`decision`, `round`, `active`, `assessed`, `next`, `progress`, `analysis`, `measurement`,
`limits` and `budget`; `analysis` says whether the selected bundle has its contribution
analysis. Two transition rules use the rounds. `evaluate -> literature` needs a
round that `round-admit` opened and that has no assessment yet. It does not require
the gate to be ready, because the gate then reports the new round's own
obligations. Without such a round the transition is refused with
`readiness_required`, carrying the gate's `decision` and `obligations`.
`evaluate -> deposit` needs the publication gate and a ready round gate, which is
ready only with an approved `stop` on the current bundle.

`exactory-draft deposit` also enforces the round gate at the publication boundary,
including `--new-version` and resumed unfinished deposits. Each remote write
requires the exact reviewed bundle and its approved stop. Completed receipt
retrieval and reconciliation of remote outcomes remain available.

`export --kind round [--destination DIR]` delivers the latest decision on the current
bundle (`round_decision_missing` without one) to the independent round assessor, into
`DIR` or by default `intermediate/reviews/packets/round-r<revision>/` (a numeric
suffix when that directory exists): the
manuscript files with the complete internal claims ledger, the bundle's reviews and predictions with their measurement, the
decision, every earlier round's goal, assessment and decision, the `development`
blocks of the closing round's cycle assessments, the `context` and `innovation`
synthesis sections, the selected `downstream` and `next_step` searches, the study's
current Grand Challenge record, the bundle's contribution analysis with the captured
responses of its investigation, the record the analysis named its criteria against when
another record has replaced it (`analysis_grand_challenge`; the decision keeps its own
record id and digest in the store), and the
research resource accounts with their budgets (`development` once it has a budget or a charged
round), with the
actual source bytes and an `inputs.json` manifest.

## Status, projections, and recovery

`status` and `next` expose the current revision, study, preparation, actionable obligations, pending admissions, remote intents/observations, retained adoptions, the `round` (number, active, assessed, decision, whether the selected bundle has its contribution analysis, the count of round gate obligations, progress counts, the measurement's medians and spreads, the admitted round's limits, the `development` budget line and the usage since admission), and the `runtime` that produced the report (plugin version, source commit, dirty flag, package digest, constitution digest). `status` also carries `grand_challenge`, the payload of the current Grand Challenge record; `status --summary` reduces it to the record id, the number of `ultimate` and `near_term` challenges, the first six criterion ids and a count of the rest, each id cut at 32 characters. In a problem workspace, the full `status` and `next` reports also carry `literature_digest` (see "Problem creation"). Both carry `advisories`: an empty list, or one entry `{code: "output_views_stale", explanation, action: "exactory-research export --kind outputs", reason, rendered_revision, revision, path, error}` while `final/` and `intermediate/` may not show the records at the current revision (see "Final and intermediate outputs" below). In a problem workspace the list can also hold one entry for the report of the exploration, `{code: "problem_report_due", reason}` or `{code: "problem_report_stale", reason, report_id}` ([The report of an exploration](#the-report-of-an-exploration)). `next` in managed research is the current milestone's first missing condition:
at `initiate`, `cohort` and `literature` it follows `direction`, and at `ideate`
it follows `execution`. The preparation report stays under `preparation`
with `advisory: true` (`preparation.advisory`); its backlog is available for
useful tool work. Before a cycle decision exists, `direction` includes the
direction minimum obligations as well as the missing decision. Other
stores retain preparation ordering and the next unread cohort abstract. At the `evaluate` stage the status obligations also carry the publication gate's and the round gate's obligations, publication first, so the study finishes the manuscript measurement before the round decision.

A deposit made outside the managed flow, the direct path of `exactory-draft deposit` (see "Review, publication, and verification"), is reported apart from the stages. While the workspace's current deposit is such a record, `status` carries `external_deposit`. It holds the recorded `environment`, `deposition_id`, `draft_url`, `doi`, `concept_doi` and `record_url`, with `status`, `bundle_digest`, `current_bundle` and `managed_credit: false`. Its `status` is `published` for a production deposit with a DOI and `sandbox_published` for a sandbox deposit with a DOI. A deposit without a DOI is `draft`. If the pinned files of the selected bundle matched the working files when the deposit was first recorded, `bundle_digest` is that bundle's digest. Otherwise it is null. `current_bundle` is whether that bundle is the selected one now and its files are unchanged. The deposit grants no managed credit. It changes no gate and satisfies no obligation, and `ready` still follows the applicable gates. While the gates are not ready, a stage change to `deposit` is still refused with `readiness_required`. A published production deposit of the current bundle removes `deposit` from `publication_commands`. At the `evaluate` stage, after a published production deposit, the publication gate's pending manuscript review (`manuscript_reviews_required`, or an obligation that names a `review_id`) is reported as `managed_wait` with `workflow: "manuscript-review"` and becomes `next`. `status --summary` and `next --summary` carry `external_deposit` with `status`, `bundle_digest`, `current_bundle`, `managed_credit`, `environment`, `doi`, `record_url` and `deposition_id`, and `managed_wait` as a hint with its `workflow`.

`status` also carries `limits`, the counts of the recorded policy against its own bounds: `{policy, loop, sample, innovation_candidates, search_readings, core_papers}`, with the selected depth plan under `depth` when present. Under `lineage-v1`, `loop` is `{readings, limit, covered}`, the registered loop readings, the current loop limit (default 100) and the purposes already covered, and `innovation_candidates` is `{families, required}` with the current required count. Under `sampled-v1`, `sample` is `{n, placed, unplaced, percentile, band, widen_required}` once a sample is drawn and null before that, `search_readings` is `{readings, limit}` against 20, and `core_papers` is `{requirements, limit}` against 10. A field that the recorded policy does not bound stays null. The verifier reads the percentile and the band it files from `limits.sample`.

`status` in the research profile also carries `bundle_drift`: null, or `{bundle_id, bundle_digest, files: [{path, code}]}` for the selected bundle while its measurement is incomplete (fewer than three assessors with both a review and a prediction on it), with every pinned file whose working copy differs. `code` is `publication_artifact_changed` for other bytes, `artifact_missing` for a missing file, and `unsafe_path` for a symlink, a directory or a path under a symlinked directory. The field is null when every file equals its pinned bytes, when the measurement is complete, when no bundle is selected, and outside the research profile. Reading it writes nothing. `restore --bundle DIGEST` puts the pinned bytes back (see "Review, publication, and verification"). `status --summary` carries the same drift in a bounded form: `bundle_id` cut at 64 characters with `bundle_id_truncated`, `bundle_digest` whole, and for each file its `path` cut at 128 characters with `path_truncated` and its `code` whole. When the summary still exceeds 16 KiB, it first omits Grand Challenge criterion ids (counted in `omitted_criterion_ids`), then cuts the longest path by 16 characters at a time while that path is longer than 32 characters, marking each cut path, then cuts `waiting` to no fewer than 32 characters and the `next_hint` fields `explanation` to no fewer than 32 and `version_id`, `work_id`, `collection_id` and `unit_id` to no fewer than 16, listing each cut hint field in `next_hint.truncated_fields`. `waiting` carries no mark of its own beyond `display_strings_may_be_truncated`. Every file entry stays in the summary. The default `status` keeps the full names.

At stage `write`, `status` and `next` also carry one `draft_marker_missing` obligation while no draft record exists, or while `.exactory/draft.json` (the marker) is not a regular file that holds the JSON of the draft record. The comparison is of parsed JSON, not of bytes, and it applies no default: a record that holds `version` differs from a file without it. The full list appends the obligation after the other obligations, and both commands select it as `next`, so `status --summary` and `next --summary` carry it as `next_hint`. `by_code` of the summaries orders codes by count and then by name, not by `next`. The obligation carries `stage` (`write`), `path` (`.exactory/draft.json`) and `missing_fields`: the objective fields among `title` and `category` that are absent while neither record nor entry exists, and an empty list otherwise. The hint carries the fixed hint keys `code`, `version_id`, `work_id`, `collection_id`, `unit_id` and `explanation`, with the four identifiers null for this obligation and the explanation cut at 120 characters, and `truncated_fields` and `details_required: true`. It carries no `path`, `stage` or `missing_fields`. A summary that exceeds its size bound can cut the explanation shorter and then lists it in `next_hint.truncated_fields`. Every explanation names the step of its state within 120 characters. The states and their steps: (1) A draft record exists, and the marker is missing, holds other or invalid JSON, or is a symbolic link: `export` (its default `--kind workspace`) writes the marker from the record again. It replaces a link and leaves the link's target alone. (2) A draft record exists, and the entry is a directory or another type: move the entry to an unused name in `.exactory/`, then `export`. (3) No draft record exists, and the marker is a regular file that holds a JSON object whose `version` is absent, 1 or 2: `adopt` records that object as the draft, as it is and without adding `version`. Its payload, sent with the usual `--expected-revision` and `--request-id`, names `.exactory/draft.json` in `files` and gives an `id`, a `reason` and the current `profile` and `target` exactly (`configuration_conflict` otherwise). `exactory-draft init` refuses the directory while the file exists. (4) No draft record exists, and the entry is a symbolic link (dangling included), a directory, or a regular file that is not such an object (`adopt` answers `invalid_json` or `migration_required`): move the entry to an unused name in `.exactory/`, for example `mv -n .exactory/draft.json .exactory/draft.json.saved`, with no trailing slash on either path and a name that no entry holds yet (`-n` does not overwrite an existing file of that name, and the entry then stays in place), then run `status` again and take the step of the state it reports. The saved entry is not an input of `adopt`. (5) Neither record nor entry exists, and `missing_fields` is not empty: `exactory-draft init --title TITLE --category CATEGORY` in the workspace root records the draft from its own values and writes the marker. The objective does not change. (6) Neither exists, `missing_fields` is empty, and the study status is not `done`: `exactory-lab state set --stage write` records the draft from the objective. (7) Neither exists, `missing_fields` is empty, and the status is `done`: `exactory-draft init` with the objective's `title` and `category`. The status stays `done`, whereas a same-stage update whose status stays `done` checks the `manuscript` gate and the `readiness` gate (or the selected `manuscript-readiness` gate) first. Reading `status` writes nothing and never reads the target of a link.

The optional `--summary` flag on `status` and `next` returns a bounded advisory view. It runs the same current-state evaluation as the default command. It does not change research obligations, authorize a transition, or reduce required reading. `status --summary` is limited to 16 KiB and `next --summary` to 4 KiB of UTF-8 JSON. Counts distinguish displayed and omitted obligations. Hints can omit or truncate details and must not be used directly as mutation payloads. One exception is `next_step` of `next_hint`, the first action of the selected obligation of a staged review, which is copied exactly when it fits in 1,024 bytes and is otherwise null, with `omitted_next_steps` counting the actions not shown; `status --summary` adds `review_slots: {occupied, complete, incomplete, pending}` when the record holds a staged review, and `review_parts: {count, examples, omitted}` when it holds a parts v2 series ([Reviews in fresh parts](#reviews-in-fresh-parts)): one example per series, its latest turn with `kind`, `turn`, `units_carded`, `units_remaining`, `complete`, the ids cut at 64 characters and `blocked` when set, at most 20 examples and fewer when the summary exceeds its bound. The full `status` carries the progress of every parts turn under `review.assignments.<id>.review_parts`, and the `next_hint` of a `review_parts_pending` obligation adds `review_parts` with the kind, the turn, the units carded and remaining, the adapter, the series id and `blocked`. `status --summary` also reports `evaluation` counters (objects verified, readings assessed, graph builds, elapsed seconds); they describe this invocation and are never stored. Full reports retain the exact current data; summaries remain advisory views.

Since 0.73.0 `status` and the `gate` command attach the plan of the review chain to every obligation that names a review assignment, after every report digest is computed: the fields `state`, `command`, `payload`, `request_id`, `not_before`, `code` and `details` of its `next` entries, which `review-series` executes ([Review chains and `review-series`](#review-chains-and-review-series)). When a post closes the chain of the assignment, the attached entries keep `kind` and `state` (`done`) and drop `workflow` and `scope` ([Problem creation](#problem-creation)). `status` also lists a chain head that ended without an output, with the obligations of its own state (for example `review_output_pending`), each with its planned action, and it adds no obligation for a failed `native_math` head unless the head is an openness chain. It shows `answer_recoveries` per assignment, `answer_recovery` for a staged task, and `unit_allowance_floor` for a parts v2 turn ([Reviews in fresh parts](#reviews-in-fresh-parts)). In a problem workspace whose store holds a `problem_post` for the current statement, `status` lists the problem's other findings under `after_post`, and `next --summary` reports `after_post` as a count ([Problem creation](#problem-creation)).

`batches --depth abstract --size N --destination DIR [--profile PROFILE] [--screen] [--max-input-bytes BYTES] [--max-output-tokens TOKENS --estimated-output-tokens-per-item TOKENS]` writes the current unread abstracts (cohort members first, then Tier 3 references) as `batch-NNN.json` files with each version's selected complete abstract text, the exact link, and a `README.json` naming the notes shape that `read-batch` accepts. `--profile` defaults to the configured profile. It reads the store and writes only under the new destination directory (`review_destination_exists` when it exists; `migration_required` without a configuration); a reader agent works from one file and returns one notes file, and a single coordinator submits it with `read-batch`. With `--screen` it writes `screen-NNN.json` files of the unscreened members for `screen-batch`, under `screened-v1` only (`policy_inapplicable` otherwise). The `README.json` names the shape of the export's own mode: the screening shape with `--screen`, the loop shape with `--loop`, the placement shape under `sampled-v1`, and the plain notes shape otherwise.

`batches --loop [--candidates FILE]` writes the loop's unread abstracts instead, under `lineage-v1` only (`policy_inapplicable` otherwise): the found works of the selected searches that carry no loop reading yet, followed by the caller's candidates. `--candidates` reads a JSON file that is either a `population-query` result or a plain list of version ids (`invalid_input` for anything else), and it needs `--loop` (`invalid_input` without it). The exported `README.json` carries the loop item shape, `{loop: {purposes, disposition, source}, innovation_candidate}`, and the note that `innovation_candidate` marks one of the current required innovation candidates and stays out of every other item. Under `lineage-v1` a plain export is refused with `policy_inapplicable`, because this policy asks for no reading of the enumerated population: its abstracts come out with `--loop`.

Each batch file is bounded by its items and by its bytes. `--size N` bounds the items of a file (default 60, at most 100). `--max-input-bytes BYTES` (default 262,144) bounds the complete encoded batch file, its wrapper and metadata included. `--max-output-tokens TOKENS` with `--estimated-output-tokens-per-item TOKENS`, given together (`invalid_input` for one without the other), add an item ceiling of their quotient; an allowance below one item's estimate is refused with `batch_output_budget_too_small`. The entries are packed in the existing order, and each batch file is written as soon as it is full, so an export that fails later leaves the earlier files usable. One entry that alone exceeds the byte bound is refused with `batch_item_too_large`, whose details name its `version_id`, its `input_bytes`, the `max_input_bytes` bound and the `parameters` that would admit it; an abstract is never cut. Both refusals carry under `recovery` a runnable `batches` command with bounds that admit the work. `manifest.json` in the destination is written with `complete: false` before the first batch file and set to `complete: true` only after the whole enumeration. It carries the `revision`, `counts: {candidate, exported, missing, skipped}`, the `files` with their entries, input bytes and estimated output tokens, `selection_coverage` (the profile, the preparation policy, the mode, the requested candidates, the selected `version_ids`, the `missing_version_ids` and the `skipped_candidate_ids`) and `capacity` (`max_items`, `effective_max_items`, `max_input_bytes`, the two output options and `output_capacity`, `estimated` with them and `unknown` without them). A selected member without a saved abstract is counted as missing, and a candidate outside the store as skipped. When `--candidates` holds a `population-query` result, `selection_coverage` adds `query_coverage` (`complete` for a first page that omitted no match, `partial` for any other page, `unknown` when the file carries no consistent counts) and the query result without its matches under `population_query`. Read the manifest before the batches: while it is not complete, or while a missing count is above zero or the query coverage is partial, the files are not the whole selection. Inputs within the defaults produce the same files as before. The result is `{revision, entries, files, manifest, mechanical_only}`.

`population-query --terms TERM [TERM ...] [--limit N] [--cursor CURSOR]` is read-only. It ranks the enumerated population's stored abstracts by how many of the terms appear in the title or the saved abstract, each term matched as a whole word or the start of one and ignoring case, and returns one page of the ranked matches, highest score first: `{terms, population, matches, read_only, revision, query_digest, ranking_version, total_matches, returned, offset, omitted, remaining, bound_reached, next_cursor, searchable, unavailable}` with each match as `{version_id, work_id, title, matched_terms, score}`. `total_matches` counts every member that matches a term, `returned` the matches of the page, `offset` the position of the page, `omitted` the matches outside the page, `remaining` the matches after the page, `bound_reached` whether the page filled `--limit`, `searchable` the members with a saved abstract and `unavailable` the members without one. `--limit` is positive and at most the current `loop_abstracts` amount (default 100), and defaults to 30. While matches remain, `next_cursor` is a string, and `--cursor CURSOR` continues the same query from it; on the last page it is null. A page is not the whole matching population: read `remaining` and `next_cursor` before treating the matches as complete. The cursor binds the store revision, the digest of the terms, the ranking version and the offset. A cursor from another query or ranking fails with `invalid_input`, and a cursor from an older revision with `stale_cursor`; both refusals carry under `recovery` the command that restarts the query from its first page. Ranking and the `--limit` bound do not change with a cursor. The result is the local source of the lineage loop: pass it straight to `batches --loop --candidates`, which records the page's coverage in its manifest.

Under `screened-v1` the cohort gate requires a screening for every member (`screening_missing`), an abstract reading for every `promote`, `doctrine` and (until saturation) `pending` member, an audit reading with `audit: {relevance, reason}` for a deterministic sample of at most 150 excluded members per collection (`screening_audit_reading_missing`; any accepted reading of the version can carry the audit; a `strong` audit judgment is `screening_audit_failed` until the paper is promoted and every excluded member is re-screened in a round above the audited one), and at least 8 read `doctrine` or `promote` members per calendar month of the window, or every member of a smaller month (`doctrine_coverage_missing`). Tier 3 references need a screening keyed by family with `context`; excluded references are sampled the same way. `require-fulltext` families and unresolved references keep their obligations whatever their screening says. `batches --screen` exports the unscreened members for `screen-batch`.

Under `sampled-v1` the cohort gate requires a current sample of the scoped collection (`sample_missing`, and `sample_stale` once the population changed under it), an abstract reading of every sampled member (`sample_reading_missing`), and a placement on each of those readings (`placement_missing`). No other member owes a reading. Under `lineage-v1` no cohort member owes a reading at all, and a pending acquisition is reported as a notice rather than an obligation, so the gate passes while the enumeration is still incomplete. Both policies drop the Tier 3 obligation on the references of a full-read paper; the bibliography is still inventoried for identity.

`policy-report [--policy POLICY] [--reference FILE]` is read-only. It reports each collection's members, readings, unscreened count, dispositions, audit sample and selected preparation set under the recorded or a hypothetical policy, whether a saturation checkpoint covers, and, with a reference file `{"prior_art": [...], "contradictions": [...], "methods": [...], "doctrine": [...]}` of family ids, the recall of the selected set per category with the missed families listed. `--policy` takes any of the four policies. For `lineage-v1` and `sampled-v1` the report is `{revision, policy, recorded_policy, limits, mechanical_only}`, because a bounded policy prepares a loop or a sample rather than a selection out of the population; `limits` holds the same counts as `status.limits`, read from the recorded workspace, so naming a hypothetical bounded policy does not recompute the counts under it.

`obligations --code CODE [--limit N] [--cursor CURSOR]` returns one page of the current obligations that carry `CODE`, ordered by exact version. The current obligations are the ones `next` follows: the managed milestone's gate, or the existing cohort/readiness ordering in other stores. A page carries `total`, `offset`, `returned`, and `next_cursor`. A cursor is `REVISION:OFFSET`; a cursor whose revision differs from the current store fails with `stale_cursor`, so a listing never mixes revisions. On resume, read `status --summary`, then `next --summary`, then the `obligations` page for the code you are working on; run the default detailed command only when the full nested report is needed.

`gate ACTION` supports `cohort`, `foundation`, `preparation`, `direction`, `readiness`, `manuscript-readiness`, `execution`, `verification`, `manuscript`, `publication`, `deposited`, `submitted`, and `round` and exits nonzero for unmet obligations.

`check-claims --file FILE` is read-only. `FILE` holds the `claim_evidence` array of a `manuscript` payload, `[{claim_id, evidence: [reference, ...]}]`, or the whole payload, whose `claim_evidence` the command reads, under the payload rules of "Inputs and receipts". The command compares each reference with the evidence of the current assessed candidate, the candidate that `manuscript` pins (`candidate_checkpoint_missing`, with the obligations in the details, while no candidate exists). It needs neither the readiness review nor the write decision, so it runs while the mapping is built, before the readiness review and before the pin. A reference matches when it equals a candidate reference as a whole, key order aside, so a reference with another locator or artifact is outside the candidate. When every reference matches, the command exits 0 and prints `{revision, mismatches: []}`. Otherwise it exits 1 with `publication_evidence_mismatch`: the message names the first claim id and reference outside the candidate, and `mismatches` in the details lists every `{claim_id, evidence}` pair in mapping order. A file that is not an array, or a payload without `claim_evidence`, is `publication_claims_missing`. A claim whose `evidence` is not a nonempty array is `publication_evidence_mismatch`, and the command stops at that claim. The command checks the evidence only. The pin still checks the claim ids against the claims file, the citation accounting, the readiness review and the decision.

SQLite is authoritative for the managed records. Workspace JSON, decision logs, and the output views in `final/` and `intermediate/` are projections of those records. Editing them cannot authorize work. `.exactory/problem-deposit.json` is a recovery journal, not a projection: the deposit commands read it, `attach-problem` requires it, and `export --kind workspace` does not rebuild it. When the store keeps no record of a direct deposit, `.exactory/deposit.json` holds the only local copy of that deposit and a projection export can replace the file; copy its state out first, as the command's message says. The harness publishes these files read-only, mode `0400`: the workspace projections `.exactory/study.json`, `draft.json`, `deposit.json` and `decisions.jsonl`, the recovery journal `.exactory/problem-deposit.json`, the markers `.exactory/output-views.json` and `output-views-dirty.json`, the report of `exactory-check lookup` (`.exactory/citation-check.json`, or the `--out` path) with `citation-cache.json` beside it, the report of `exactory-derive check` (`.exactory/derivation-check.json`), the authorship record `.exactory/authorship.json` and the autopilot counter `.exactory/autopilot_count` that the hooks write, the output views, and the packet, problem, native and literature exports. Each is published atomically: the writer writes a temporary file in the same directory, sets the final mode, syncs it, renames it over the target and syncs the directory, so an interruption before the rename leaves the previous file intact. A direct write to such a file, a shell redirection `>` or `>>` or a Python `open` for writing or appending, fails with a permission error; the commands and the hooks still update it. This stops accidental direct writes only: the same user can still change the mode, unlink or replace a file through its writable directory. A reader never requires the mode: a workspace written before 0.76.0 with writable files reads as before, `record-check` makes its disposable copy writable and its checks pass, and the next native write publishes such a file again read-only, also when its bytes are unchanged. The work files of an owned probe are published with mode `0600`, the artifact objects under `research/sources/objects/` keep their own publication with mode `0444`, and `research.sqlite3` and the lock files keep their handling. `export --kind workspace` rebuilds workspace projections from current history; `export --kind outputs` renders `final/` and `intermediate/` again (next section); run reconciliation repairs execution projections. `exactory-lab decide` retains immutable decisions with revision/request identity and preserves any old decision-log bytes. Explicit `adopt` archives selected legacy paths and exposes pending obligations without rewriting their old scientific claims.

### Final and intermediate outputs

`final/` and `intermediate/` are generated views of the records, in the workspace root: the directory of the store, or, for a store at a math-solver controller root, that root's parent. `final/` holds the current step results and `final/history/` the superseded and historical ones, in the same layout: `reviews/incomplete/<id>.json` for an incomplete assessment of a staged review (current while its epoch is the slot head); `reviews/<role>/<record id>.json` for the recorded reviews and adjudications (a `value_review` under the role of its assignment, `review_adjudication` under `adjudicator`, `manuscript_review` under `manuscript`, `manuscript_prediction` under `prediction`, `readiness_review` under `readiness`, `scoped_readiness_review` under `scoped-readiness`, `round_review` under `round`); `reviews/<role>/<series assignment id>/<NN>-<assignment id>.<run id>.json` for the selected final output of a complete parts v2 series, or `<NN>-<assignment id>.<correction id>.corrected.json` for a corrected final ([Reviews in fresh parts](#reviews-in-fresh-parts)); `overrides/<record id>.json` for every recorded override (`gate_override`), user and automatic ([Stops and overrides](research-workflow.md#stops-and-overrides)); `problem/<kind>/<record id>.json` for the current statement and the latest slate, attempt, openness finding, value, assessment, decision, formal checks and post (`statements/`, `slates/`, `attempts/`, `openness/`, `values/`, `assessments/`, `decisions/`, `formal-checks/`, `posts/`); `study/<kind>/<record id>.json` for the current round admissions, round decisions, round assessments, round factual assessments, research decisions, cycle assessments, contribution analyses, checkpoints and receipts (`round-admissions/`, `round-decisions/`, `round-assessments/`, `round-factual-assessments/`, `decisions/`, `cycle-assessments/`, `contribution-analyses/`, `checkpoints/`, `receipts/`); `study/manuscripts/<bundle id>/bundle.json` with the pinned files under `files/`, for the selected bundle and the bundle of the latest deposit receipt; `study/deposits/<intent id>/receipt.json`, `read-back.json` and the uploaded files under `files/`; `verification/verdicts/` and `verification/tasks/`; `math/deliveries/attempts/` and `math/deliveries/closures/`; and `math/<rel>/`, which the math-solver controller renders (its `SEARCH.md`, "Locked command construction and recoverable projections"). A record whose canonical JSON exceeds 1 MiB is shown as a summary, `{summary: true, kind, id, recorded_revision, status, bytes, sha256, fields}`, in which a field over 64 KiB is `{omitted: true, bytes, sha256}`. An id or a role in a file name that is not a portable file name (`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`), for example an automatic override id with `:`, is rendered as `id-<the first 32 hexadecimal digits of the SHA-256 of the id>`; `index.json` maps each file to its record id.

`intermediate/` holds every other output. `reviews/<role>/<root assignment id>/series.json` and `<NN>-<assignment id>.<run id>.json` show each reviewer series, the turns of one lineage (an adjudicator's turns are grouped by session): `series.json` lists each turn with its link, slot, packet reference, runs, their status, error, usage, refusal and correction, the selected run and the record that adopted the turn, and the file of a run holds its output; a correction's output is `<NN>-<assignment id>.<correction id>.json`. The turns of a parts v2 series are grouped under the series id, each with a `review_parts` progress object (`protocol`, `adapter`, `series_assignment_id`, `kind`, `turn`, the allowances, the estimates, the evaluated progress and the next actions) and each run with `output_path`: the outputs of read, combine and check turns and the request form of a final stay here, and the selected final output of a complete series is written under `final/reviews/<role>/<series assignment id>/`, an ordinary final as `<NN>-<assignment id>.<run id>.json` and a corrected final as `<NN>-<assignment id>.<correction id>.corrected.json` (`corrected_output_path`); the index entries of both carry `series_assignment_id`, `turn`, `adapter` and `review_parts_kind`. When the assignment of a turn is of a posted statement, the `next` list of its `review_parts` object is `[]` ([Problem creation](#problem-creation)). A controller's turn of a staged review adds `raw_output_digest` and `effective`, with its effective output in `<NN>-<assignment id>.effective.json`, and a child's turn names its `task_id`; `reviews/staged/<kind>/<id>.json` holds the records of the staged reviews (epochs, tasks, execution plans, task executions, reservations, observations, acceptance receipts, pool memberships, the capacity policy, slot heads, dispatch records and composition plans) and, under `review_call_observation`, the captured answers of the ordinary reviewer calls. The other entries are `reviews/routes/<route id>/route.json` and `probes/<probe id>.json`; `reviews/usage.json`, the review call usage with the allowance, the charged calls and the remaining calls under `review_budget`; `reviews/packets/<assignment id>/` and `reviews/packets/<kind>-r<revision>/`, the default destinations of the packet exports, with a numeric suffix when the directory exists; `problem/candidates/<record id>.json`; `problem/forming/<record id>.json` for each forming call and `problem/form-outcomes/<record id>.json` for each outcome of a form, where the index entry of an outcome that a later outcome of the same item supersedes names it under `superseded_by`; `lab/probes/<probe id>/probe.json`, `lab/artifacts/<id>.json` for a local artifact that no pin ties to a probe, `lab/runs/<admission id>/run.json`, `result.json` and `log.txt`, and `lab/executions/<id>.json` for an execution that no outcome names; `study/bundles/<bundle id>.json` for the bundles that were neither selected nor deposited, `study/score-history.json` (every recorded manuscript review in recorded order), `study/remote-intents/` and `study/remote-observations/`; `checks/citation-check.json` and `checks/derivation-check.json`, copies of the reports in `.exactory/`; `literature/exports/`, the destination of `export --kind foundation`; `legacy/<original path>/`, the copies that a session moved there once; and `math/<rel>/`, rendered by the controller. A run output of at most 1 MiB is copied; a larger, sealed, missing or corrupt one is listed in `series.json` with `output_listed` set to `over_1_mib`, `sealed`, `artifact_missing` or `artifact_corrupt`.

Each folder has `README.md` and `index.json`. The index lists every file with its `type` (`view`, `controller`, `export`, `legacy`), the `kind`, `id`, `recorded_revision` and `status` of its record (`current`, `superseded`, `historical`, `not_selected`), an `error` when the file could not be written, and every other file under `unowned`. The marker `.exactory/output-views.json` records the rendered revision, the digest of the event at that revision, the plugin version and the size, modification time and digest of every owned file. Each recording command of `exactory-research`, `exactory-lab`, `exactory-draft`, `exactory-cohort` and `exactory` (`submit`, `verify`, `reconcile`) renders once after its last commit, outside every store lock, under its own lock `.exactory/output-views.lock`; read commands and `--check` render nothing. This automatic render builds no model when nothing needs an update: the marker shows a complete render under the installed plugin version with the current inputs, no dirty marker is pending, and no record of a view kind changed since the marker's revision; the marker then takes the current revision. Otherwise, and on every `export --kind outputs`, the render builds the whole model from the records and writes it: a file whose bytes are unchanged and that has no write bits stays in place, a writable owned file is published again even when its bytes match, a file whose bytes differ is written, and the owned files that the model no longer produces are removed; the render never overwrites a file that it does not own (`output_view_conflict` on the entry). The read-only mode is not a condition for reading a view. A failed render prints one warning line on stderr and leaves the command's result and exit status unchanged. `export --kind outputs` renders every view again and returns `{revision, files: {final, intermediate}, errors}`. `status` and `next` carry `advisories` with one `output_views_stale` entry while the views may not show the current records; its `reason` is `shared_workspace_root` (a store at a controller root whose parent also holds a store renders nothing), `marker_unreadable`, `render_failed` (with `path` and `error`), `file_errors` (the first path with an error), `not_rendered`, `plugin_changed`, `history_changed`, `behind` or `render_deferred` (a render waited more than one second for the renderer lock, so it left the dirty marker `.exactory/output-views-dirty.json`, named in `path`; the next recording command or `export --kind outputs` renders every view again); a field that does not apply to the reason is null. `status --summary` and `next --summary` add `advisories: [{code, reason}]` only when the list is not empty. The advisory never changes `ready`, `obligations`, `counts` or `next`, and no gate reads it. The guard hook refuses `Write`, `Edit` and `NotebookEdit` into `<workspace root>/final/` and `<workspace root>/intermediate/` (only these two direct children, so `draft/final/` is not guarded), except `intermediate/legacy/`, when the workspace root holds `.exactory/output-views.json`, or a folder in it holds `.search/output-views.json` (a controller root), or a controller root in it (`.search/tree.json`) holds `.exactory/output-views.json` (a research store located at the controller root); shell commands are not inspected, and each render checks the files it owns.

A genuine hot SQLite rollback journal makes default reads return `store_recovery_required`. Use `exactory-research recover`, optionally with `--expected-revision`, to request native SQLite rollback recovery after path checks. This preserves committed history, migrates no schema, resets no accounts, and satisfies no research gate.

### Record check

`record-check --workspace W [--keep-clone DIR] [--time-budget SECONDS]` qualifies a copy of a research record offline, in six steps, and prints a JSON report. `--workspace` is required, and the command has no `--check` flag. It never opens a SQLite connection on `W`. It copies `W` file by file (APFS `clonefile` on macOS) into a clone in a new temporary directory outside `W` and checks the copy (`PRAGMA integrity_check` and the store's full history validation); it copies up to three times until one copy is consistent, otherwise the `snapshot` step fails with `record_snapshot_inconsistent`. The clone is removed when the command ends. `--keep-clone DIR` keeps the clone at `DIR`, which must not exist and must be outside `W` (`unsafe_path` otherwise).

The six steps run in this order. The first failed step ends the check, and the later steps do not run:

1. `snapshot`: the consistent copy, with its revision and the number of copies.
2. `integrity`: every artifact of the store named by any historical event verifies, superseded records included. An artifact of the store is a reference whose `path` is under `research/sources/objects` or `research/sources/sealed`. The result counts the verified artifacts as `artifacts`. Any other descriptor with `path`, `sha256` and `media_type` (for example the publication scope's projection of a workspace file) is not read. The result counts those descriptors as `workspace_descriptors`. A store reference that lacks a field or whose bytes differ fails the step. When the record holds a carry ([Carried objections](#carried-objections)), the step also opens every retained proof and verifies each carry from it, without opening a source workspace. The result counts them as `problem_objection_carries`. A proof that does not reproduce its carry fails the step with `problem_objection_provenance_invalid`.
3. `reconstruction`: every retained probe request, reviewer request, output-correction request and frozen staged request must rebuild byte for byte under a supported fit order or an applicable historical form, described below. Since 0.74.0, when no supported fit order rebuilds a retained probe or reviewer request whose observation is `failed` with a null `output`, and the invocation, request, route, packet, prompt and history bindings of that observation match, the step also tries five historical forms against the whole serialized request, byte for byte: the unlabelled probe request (for a probe) and, for a reviewer invocation, the labelled pre-fit local request, the Codex order before source capacity, the Claude order before the full-text steps and complete-source exact text v1. A match is reported in the step's `invocations` entry of that request as `historical_profile` (`unlabelled-probe`, `labelled-pre-fit-local`, `codex-order-before-source-capacity`, `claude-order-before-full-text-steps` or `complete-source-exact-text-v1`) and authenticates nothing: the observation stays failed, and dispatch, authentication and `ci/record_fingerprint.py` are unchanged. If neither a supported fit order nor an applicable historical form rebuilds the request, the step fails with `record_request_changed`, also when the previous release fails on the same request. Its details name `kind` and `invocation_id` for a probe, reviewer or correction request, and `assignment_id` and `id` for a frozen staged request; a probe whose instructions no longer match their manifest has no details.
4. `authentication`: each observation authenticates as before and keeps its classification; failed, imported and pending history stays as it is (a failed attempt need not become ready).
5. `continuation`: for every chain head, `plan_next` and the transitions that stay local. The offline executor runs only `review-assignment`, `value-review`, `review-adjudication` and `problem-openness`, and only on the clone. A `review-run` action (`run`, `wait`, `resume`) is never executed: its request is rebuilt, its delivery validated and `review_preflight.check_request` applied. Each chain ends at a boundary: `done`; `run` or `wait` (the prospective request is rebuilt and passes its checks); `pending` (a live worker or a call to finish); `handoff` or `stop` with a code that the check accepts (the handoffs and the outcomes of [Review chains and `review-series`](#review-chains-and-review-series), except `review_input_too_large` and `review_format_unsupported`, which fail the step when a plan stops with them); `decision` (below); or `controller` (a native head that is not an openness chain belongs to the math-solver controller, which the `report` step reads). The step fails on an unknown stop code, an action outside the allowlist (`qualification_unavailable`), an unknown plan state (`record_unknown_action`) or a repeated unchanged action (`review_series_no_progress`).
6. `report`: the three report readers, `status`, `next` and `obligations` (one page of each obligation code), each run under the time budget (below). The math-solver readers run under the same budget. They are `exactory-math search status` and `search next`, which read each math-solver controller found on disk inside the clone (never through the registry of absolute paths), with `--attack-root` set to the cloned root. A reader that does not stay within the budget fails with `record_command_timeout`, and a math-solver reader that refuses fails with `record_math_read_failed`. A failed reader fails the step with `record_report_failed`, and the step keeps its `result`. The `result` gives a row for each report reader under `commands` and a row for the math-solver readers under `math`. Each row gives `seconds` (the elapsed time), `processor_seconds` and, after a failure, the reader's `error`.

Decision stops. A refusal of an allowed action for route capacity means that the chain cannot finish on that route with that material; the next step is a decision (a route with a larger limit, or less material). The codes are `review_input_too_large` in the phase `final_preflight` (the mandatory final input of a reading chain is over the route limit), `review_parts_frame_too_large`, `review_parts_inventory_too_large` and `review_parts_final_too_large`. Each is listed under the continuation result's `decision_stops` with `head_id`, `command`, `code` and `details`, and the record passes the step. Every other refusal keeps its result.

`--time-budget SECONDS` bounds the processor time of each reader of the `report` step, to catch a runaway cost. It is a finite positive number of seconds, at most 100000000, default 10800. Input that the option parser rejects, for example `abc`, ends the command with a usage error and exit status 2, and a value that the parser accepts but that is not finite or is outside this range is refused with `invalid_input` before the command copies `W`. The maximum is a limit of the harness, chosen inside the range of values that macOS accepts for an interval timer. The default rests on two measurements made for 0.85.3 on one machine. A report reader of the largest live record used 1,186 to 1,231 seconds of processor time. A fixed Python workload used 3.1 to 3.7 times as much processor time when it ran on the efficiency cores of that machine.

Since 0.85.3 the budget counts processor time (user and system time), not elapsed time. The processor time of a reader is that of the check process during the reader, plus that of the child processes that ended during the reader. The time that a reader waits while other processes use the machine is not processor time. A reader can therefore take longer than the budget in elapsed time and pass.

- A report reader runs in the check process, under a processor-time timer (`ITIMER_PROF`, signal `SIGPROF`) that the check arms with the budget. The timer stops the reader when the check process has used the budget during the reader. A `SIGPROF` that arrives after the reader returns, before the check disarms the timer, counts as the same stop.
- A math-solver reader runs as a child process. A short launcher arms the same kind of timer with the budget and then replaces itself with the reader command (`os.execv`). The timer stays armed in the reader, and `SIGPROF` ends the reader when the reader has used the budget.

After a reader of either kind ends, the check compares the reader's processor time with the budget. A reader that the timer stopped, or whose processor time is larger than the budget, fails with `record_command_timeout`.

The message of `record_command_timeout` names the reader and the budget. The details give `command`, `budget_seconds`, `processor_seconds` and `seconds`. For a report reader, `command` is the reader's name. For a math-solver reader, it is the reader command without the launcher, and the details add `attack_root`. When the timer stops a reader before the reader finishes, `processor_seconds` shows where the reader stopped, not what the reader needs. The error decides nothing about the record. When a reader fails with `record_command_timeout`, run `record-check` again with a larger `--time-budget`. The comparison with the previous release is part of the release procedure, not of the command.

Local tool limits. A local tool has its own limit of elapsed time, which `--time-budget` does not change. For example, `pdftoppm` has 60 seconds to render one page of a PDF that a review cites. When a tool does not finish within its limit, the step or the reader that reached the tool fails with `local_tool_timeout`. The details give `tool` and `budget_seconds`, which is the limit of the tool. The error decides nothing about the record. The harness records no refusal and keeps no result of the unfinished tool, and a review whose page did not render keeps its standing. Outside `record-check`, a command of `exactory-research`, `exactory`, `exactory-draft` or `exactory-lab` that reaches such a tool ends with the same code in a JSON error and with exit status 1. When the machine has less load, run the command again.

Local reads. The command permits, and records in `local_reads`, the reads of a reviewer runtime (`route_runtime:<adapter>`: a Codex route reads `codex --version`; the Claude route launches nothing), the runtime fingerprint of a route, and `pdf_render` (the pinned PDF renderer `pdftoppm`; its temporary files stay inside the clone). Every row has `kind` and, inside a step, `step`; a runtime row has `executable` (null when nothing is launched); a `pdf_render` row has `executable` and `directory`; a fingerprint row has neither. Every other external effect is refused: network connections, acquisitions, reviewer calls, publication, worker launches and other processes. A step that needs one fails with `qualification_unavailable` and its step and reason.

Isolation. On macOS the steps after `snapshot` run under `/usr/bin/sandbox-exec` with a profile that denies outbound network, every read under `W`, and writes outside the clone and the report directory; the report says `isolation` `sandbox-exec`. Where the profile cannot be applied (for example inside another sandbox), `isolation` is `unavailable` with `isolation_reason`, and the refusal of external effects inside the process stays active.

The report: `ready`, `workspace`, `clone`, `steps` (each with `step`, `status`, `seconds`, `processor_seconds` and `result` or `error`), `local_reads`, `time_budget_seconds`, `isolation` (and `isolation_reason`) and `runtime` (`harness_version`, `harness_root`, `python_executable`, `python_version`). `runtime` names the version and the files that actually ran; run through a record's own wrapper, the command shows the version that the wrapper runs. The command prints the report and exits 0 when `ready` is true, 1 otherwise. An error before the report (`invalid_input`, `unsafe_path`) prints the error JSON on stderr and exits 1.

Activation of a new version for a record:

1. Before an existing managed record is used with a new harness version, run `record-check` with that version on the record; the command makes the copy. This holds for every record: one whose wrapper pins a version, and one that runs the harness from PATH.
2. When the report says `ready` true, update the version that the record's wrapper pins, or the command resolution that the record uses. Verify the executable path and the version that the record's command actually runs (the report's `runtime`, when the check ran through that command). Start a new session so that the new hooks load.
3. When `ready` is false, the record stays on the version it runs. Report the failed step and its `error` in the summary. Continue the work on that version. A historical request that no release rebuilds fails the `reconstruction` step under every version, so such a record stays on its version.
4. A running session keeps the version it loaded; the plugin cache is updated only when no session that loaded it runs.

The release procedure for live records is for maintainers; `misc/harness-improvement/LIVE-RECORDS.md` states it.

### Import an original OAI cohort traversal

`import-oai-cohort` repairs enumeration of an existing collection without changing its four-field frozen definition. It supports primary category `math-ph` and the official `physics:math-ph` subject set only. Supply one original XML response per `pages` entry, ordered from the first `ListRecords` request through the terminating response. `response_file` is relative to the research workspace; absolute paths, traversal and symlinks are rejected. The command validates every record before filtering, including records outside the frozen submission window. It rejects incomplete or repeated tokens, duplicate article identities, protocol errors, deleted records, malformed fields, missing categories, invalid dates and incomplete version-number histories.

The source URL must use exactly `https://oaipmh.arxiv.org/oai`. Its first query is exactly `verb=ListRecords`, `metadataPrefix=arXivRaw`, `set=physics:math-ph`; later queries contain only `verb=ListRecords` and the preceding opaque `resumptionToken`. The XML request attributes must agree. The XML request base text alone may use the same official HTTP endpoint, as the original service does. This exception does not verify the caller's transport. Capture and response times must be ordered within one UTC day. Token expiration metadata is preserved; a later local import does not reapply token expiry.

Version numbers must be contiguous and unique, beginning at v1. The greatest version number supplies the current exact ID; v1's original submitted date supplies the inclusive UTC submission-window filter. Every version date must be valid and no later than the original response time. Original dates are preserved even when later version numbers have earlier dates. Those cases receive `nonmonotone_version_dates` diagnostics in source-bound assertions and the projection ledger, and `date_anomaly_count` in the receipt. Such an assertion cannot by itself establish historical priority from its current-version date; independent valid temporal assertions remain subject to the existing cutoff rule.

Primary category is the first token in the original current category sequence, which is preserved in full with its page/record locator. This follows the official OAI template's verbatim `current_meta.abs_categories` and arxiv-base's primary-first parser/serializer. Inspected upstream commits are `arXiv/oaipmh@27ad99e3e37ab7919869bd3d4cd24449dea78135` (`oaipmh/templates/record_formats.xml`) and `arXiv/arxiv-base@f835347408851239ba23d2171f7f10916799cf62` (`arxiv/document/parse_abs.py`, `arxiv/base/templates/base/macros.html`). The raw author string is retained without inventing author boundaries. An absent optional abstract remains missing, with native reading obligations; a present malformed or empty abstract is rejected.

The receipt describes enumeration of the supplied observed chain, projected by original submission dates and current primary categories. It does not establish historical category assignments or a transactional snapshot of the mutable provider. All sources retain original bytes, `capture_method=external_import`, `origin_verified=false`, `http_status=null` and no caller-authored HTTP headers. `external_response_count` counts supplied response bodies, not external retries or total HTTP attempts. Native network usage is zero. The exact imported response bytes are checked against the byte budget and charged once, even when no network budget remains.

One CAS transaction publishes the immutable history snapshot, all sources and projected works, the new enumeration epoch, the resource charge and the finished receipt. The previous partitions, pending causes, counters and pacing state are saved in native enumeration history; old sources, pages, operations, receipts and family observations are preserved. `source_count` and `returned_count` remain cumulative; `enumeration_counts` separates this traversal's returned, projected, member and out-of-window counts. The partition total is explicitly a `local_submission_date_projection`, never a provider-reported total. Active collection acquisitions are rejected. Request replay binds the collection and ordered original hashes, sizes, URLs and capture times; changing a filename alone does not change identity.

All original pages, including pages with no members, the complete identity/date projection ledger, source assertions, parser provenance and history are dependencies of collection status and cohort preparation. Missing or corrupt evidence fails those checks, and a new epoch changes synthesis dependencies. Previously observed families absent from the new date projection still produce the existing population-change obligation. Existing exact-version abstracts and readings retain their ordinary rules; a later conflicting abstract is retained as a separate source assertion. Importing metadata creates no reading, screening, standards, readiness or admission record.
