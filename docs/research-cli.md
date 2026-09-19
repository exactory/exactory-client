# Research CLI

`exactory-research` exposes the shared research store through Python 3.9 or later and the standard library. `exactory-lab`, `exactory-draft`, `exactory`, and the native math controller consume its current evidence at their own authoritative boundaries. A download, marker file, process exit code, old receipt, or stage name does not establish scientific readiness.

Follow the [managed research workflow](research-workflow.md) for the executable
stage order, evidence interpretation, independent review, and continuation rules.

## Inputs and receipts

Each JSON mutation takes the same arguments:

```sh
exactory-research OPERATION --workspace /absolute/path/to/study --file payload.json \
  --expected-revision 42 --request-id study-specific-operation-001
```

Read the current revision with `status`. Use the same request ID and identical payload when retrying an interrupted operation. An identical committed request returns its original receipt even after later revisions. Reusing that ID with different content fails; a new operation at a stale revision fails. A historical successful receipt does not bypass a current gate. Acquisition also persists collection operations and individual page receipts.

`exactory-lab init` and `exactory-draft init` also accept `--expected-revision` and `--request-id`. Retain those values and the original user arguments when retrying a lost success. They reuse the original generated timestamps and committed initialization, even after later study changes. Current workspace projections are repaired from current history, while existing context, literature, Git configuration and user files are preserved. A retry can finish an interrupted initial layout. Different user arguments under the same ID conflict, and a new ID cannot reinitialize an existing workspace. Legacy markers without a managed initialization require explicit adoption; an interrupted existing database still requires explicit recovery.

Every operation has an illustrative input available without a workspace:

```sh
exactory-research example init
exactory-research example cycle > cycle.json
exactory-research example bind-verdict > verdict-assessment.json
```

The complete inputs are in [research-cli-examples.json](research-cli-examples.json). These are JSON payload shapes, not acquired evidence or finished assessments. Replace the uppercase IDs, zero hashes, paths, source passages, claims, and judgments with actual records and observations. The examples intentionally have no resolvable evidence objects. The innovation example shows one case of each relation; current research preparation still requires the configured minimum of distinct external cases. No example certifies an assessment merely by being saved.

JSON must be UTF-8, an object of the documented shape, and contain neither duplicate keys nor nonfinite numbers. Unlisted fields are rejected by the corresponding domain operation. Mutation output is a JSON receipt with `revision`, `request_id`, and the operation-specific `result`; acquisition reports additionally expose their retained operation and collection status. A refused gate or mutation writes a structured `error` to stderr and exits nonzero. Read operations do not repair a journal or migrate evidence.

The recurring types are:

| Type | Exact shape and interpretation |
| --- | --- |
| `ArtifactRef` | `{ "sha256": string, "path": string, "size": integer, "media_type": string }`. The path is `research/sources/objects/SHA256`. The actual bytes must match size and SHA-256. |
| `Link` | `{ "version_id": string, "source_id": string, "artifact": ArtifactRef, "locator": Locator }`. It binds a captured exact version, source, and inspected location. |
| Span locator | `{ "kind": "span", "start": integer, "end": integer, "sha256": string, "excerpt"?: string }`. Offsets are Unicode code points into the saved UTF-8 text; `sha256` is the SHA-256 of the UTF-8 encoding of that exact substring; `excerpt` is at most 200 characters of its start, for display only. This is the recommended form: the harness already holds the bytes. |
| Text locator | `{ "kind": "text", "start": integer, "end": integer, "quote": string }`, the legacy form carrying the substring itself. It stays valid; a text and a span locator over the same bytes have the same content identity. |
| JSON locator | `{ "kind": "json", "pointer": string, "value": any JSON value }`, naming an exact value in the saved JSON. |
| Objective | `{ "kind": "objective", "id": string, "statement": string }`, preserving the complete original research objective. |
| Verification target | `{ "kind": "work", "id": string, "source_id": string or null, "sha256": string or null }`. The two nullable fields are both null during incomplete preparation or both identify the available original main body. |
| Result evidence | `{ "kind": "result", "execution_id": string, "output_id": string, "artifact": ArtifactRef, "locator": Locator }`. The artifact must be an actual output of that execution. |
| Source evidence | `{ "kind": "source", "link": Link }`. Consequential synthesis, development and round evidence requires the applicable full reading. |
| Assessor | `{ "id": string, "kind": "human" or "agent", "provenance": ArtifactRef, "relationship": string, "independence_basis": string }`. Identity declarations and provenance are retained, not independently authenticated. |

Use `artifact` to pin authored program files, original review JSON, assessor provenance, or other local bytes and obtain an `ArtifactRef`. It accepts `{id, path, media_type}` and explicitly returns `scientific_validation: false`. It does not import a publication source or turn a local note into a reading. Acquire publication sources through `acquire`, `fulltext`, or `import-response`.

## Preparation order

1. Initialize the author workspace with `exactory-lab init --slug STUDY [--preparation-policy exhaustive-v1|screened-v1|lineage-v1]`, or initialize only the common store with `init` and `{ "profile": "research", "target": null }`. A draft initialized by `exactory-draft init` also receives a pending current contract. Use a separate verification workspace for an external paper. There are four preparation policies. `lineage-v1` belongs to the `research` profile and `sampled-v1` to the `verification` profile, and either one named on the other profile is refused with `policy_inapplicable`; `exhaustive-v1` and `screened-v1` are available to both profiles. `exactory-lab init` records the research default `lineage-v1` when `--preparation-policy` is absent, `exactory-draft init` always records it, and an `init` payload without `preparation_policy` records the profile's default: `lineage-v1` for `research`, `sampled-v1` for `verification`. A configuration recorded before this field existed stays `exhaustive-v1`.
2. Enter `cohort`, collect its frozen population, and record actual complete abstract readings. `cohort -> literature` checks enumeration and the selected cohort's abstract reading coverage. It does not require literature work that is still being prepared.
3. In `literature`, acquire one to five exact root paper families, expand captured citation references, inventory source bundles, read the required units, and record the five search purposes the recorded policy requires: `direct`, `originals`, `theory`, `adjacent`, and `recent` under `exhaustive-v1`, `screened-v1` and `lineage-v1`, which captures them as stage 1 of its bounded loop. Under `sampled-v1` the five are optional, because its searches are targeted: a purpose with no recorded search owes nothing, and a search that was recorded still owes the current literature scope. The cohort abstract coverage the recorded preparation policy requires remains required: every member under `exhaustive-v1`, the screened preparation set under `screened-v1`, every sampled member under `sampled-v1`, and no member under `lineage-v1`, which closes the bounded five-purpose loop with `loop-close` instead. An incomplete bibliography or unread critical source remains pending.
4. Record `grand-challenge`, the challenges ahead of the study, and set the complete objective with `target`, written against that record. Then record `standards`, `rationale`, `innovation`, and `context`. The Grand Challenge record stays current until a new record replaces it. These commands are available while preparing literature. `literature -> ideate` requires the applicable complete synthesis; it does not require entering ideate before setting the objective. Once fixed, the complete objective cannot be replaced with a special case.
5. Record a prospective `cycle`, pin the program and inputs, `admit` it, then `bind-run` with its exact backend and output contract. `ideate -> experiment` requires a current admitted cycle. The launch checks its dependencies again before releasing a process.
6. Observe the run, assess result and validity evidence separately, preserve a `checkpoint`, and deliver its candidate to an independent reviewer. Record `review`, then evaluate current whole `readiness`. An individual passed assessment or historical review receipt is insufficient.
7. Prepare the exact `manuscript`, deliver its actual bytes to independent blind assessors, and record their unchanged review JSON through `manuscript-review`. Measure that bundle with three blind reviews and their `manuscript-prediction` records, record its `contribution-analysis`, and then decide on the bundle with `round` and its independent `round-review` (see "Development rounds"). Entering `deposit` from `evaluate` requires the current resulting bundle with two accepting reviews and `gate round` ready, which means an approved `stop` decision closing the current round on that bundle. Submission requires the production publication receipt of that bundle.

`exactory-lab state set` validates all requested fields together before changing any state. The stages are `initiate`, `cohort`, `literature`, `ideate`, `experiment`, `write`, `evaluate`, `deposit`, `submit`, and `complete`. Forward edges are adjacent only; both source completion and target prerequisites apply. Any proposed `done` status also checks the target stage's completion requirement, including a status inherited from the previous stage. To enter unfinished work after a completed stage, include `--status pending`. Waiting, paused, or operational status changes do not certify readiness.

Explicit returns to `literature` are available from `ideate`, `experiment`, `write`, `deposit`, and `submit`, and from `evaluate` only while the round opened by `round-admit` has no assessment (`readiness_required` otherwise). Explicit returns are also available from `experiment` to `ideate` after an assessment, and from `evaluate` to `experiment` or `write`. A return cannot also assert `done`. All histories, observations, objective identity, and original resource accounts remain retained.

For a fresh verification directory, begin with an acquisition mutation. `acquire`, `collect`, and `import-response` can create an unconfigured Store when no managed workspace exists. This does not select the research profile or certify preparation. Existing legacy markers require explicit adoption, and an existing hot Store still requires `recover`.

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

| Operation | Required payload fields | Optional fields and variants |
| --- | --- | --- |
| `init` | `profile`, `target` | `preparation_policy`: `lineage-v1` (the `research` default; the lineage and the classics in full, a bounded five-purpose loop of at most 100 abstracts, and no cohort member reading), `sampled-v1` (the `verification` default; a stratified random sample of at most 100 members, each read with a placement; see `sample`), `exhaustive-v1` (every cohort member and Tier 3 reference owes an abstract reading) or `screened-v1` (a recorded screen selects the preparation set; see `screen-batch`). Profiles are `research` and `verification`; `lineage-v1` and `sampled-v1` each belong to one profile (`policy_inapplicable` on the other). Without this field the profile's default is recorded. Acquire exact metadata before initializing a verification target. A configuration recorded before this field existed is `exhaustive-v1`. |
| `policy` | `previous`, `policy`, `reason` | Changes the preparation policy; `previous` must name the recorded policy (`policy_conflict` otherwise). Synthesis sections and plans bind the configuration, so they need current reassessment afterwards. |
| `adopt` | `id`, `profile`, `target`, `files: string[]`, `reason` | Archive selected legacy bytes; no implicit completed readings or new execution credit. |
| `target` | `target`, `reason` | Verification repinning is explicit and requires matching subsequent roots. |
| `constitution` | `previous_sha256`, `reason` | Adopts the current distributed policy; dependent decisions need explicit reassessment. |
| `artifact` | `id`, `path`, `media_type` | The local path must remain inside the workspace without traversing symlinks. |
| `collect` | `definition: {corpus, primaryCategory, windowStart, windowEnd}` | `max_requests`, `page_size`; current corpus is `arxiv`. Dates are ISO calendar dates. |
| `resume` | `collection_id` | `max_requests`; resumes retained collection/page evidence. |
| `acquire`, `expand` | `identifier` | `provider`, `max_requests`; expansion acquires an unresolved identifier without asserting its bibliography has been read. |
| `fulltext` | `identifier`, `url` | `max_requests`; `extraction_options: {"layout": bool}` (default true) chooses the PDF extractor's layout mode. Preserves the exact original and extraction result, and records `extraction` (extractor, version, options, text bytes, page count, longest line, whitespace fraction, expansion ratio) on the capture. A capture with other options is a distinct capture of the same original; a reading on any capture of that original satisfies its full-text coverage. |
| `import-response` | `provider`, `response_file`, `source_url`, `captured_at` | `media_type`, `mappings`; `web`/`mcp` mappings use JSON pointers into the original saved response for every work. |
| `import-oai-cohort` | `collection_id`, `pages: [{response_file, source_url, captured_at}]` | Existing arXiv math-ph collection only. Each file is an original XML response at a checked workspace-relative path. Supply the complete ordered arXivRaw whole-set token chain; no authored works, totals, flags or HTTP receipts. |
| `roots` | `profile`, `roots: string[]`, `collection_ids: string[]` | `target` is required for verification; optional `historical_cutoff`. Research objective identity is set by `target`, not by this scope. |
| `bundle` | `id`, `version_id`, `source_id`, `scope`, `completeness`, `units`, `inventory`, `bibliography`, `resolutions` | Each unit has `id`, `kind`, `required`, `link`, and optional `reason`/`url`; inventory and bibliography must reflect the original article. |
| `read` | `id`, `version_id`, `depth`, `inspections`, `notes` | Fulltext reading also supplies `bundle_id`; each inspection has `unit_id`, `link`, `note`. All seven note fields shown in the example are required. |
| `budget` | `profile`, `purpose`, `limits`, `reason` | Purposes are `literature`, `screening`, `experiment`, `development`. `limits` names every unit (`network_requests`, `source_bytes`, `readings`, `screenings`, `model_input_tokens`, `model_output_tokens`, `wall_seconds`, `rounds`); null means unlimited. A `development` budget bounds the number of rounds with the `rounds` unit; `round-admit` charges one round. Any unit of a `development` budget at its limit refuses the next `continue` with `resource_budget_exhausted`, so leave its other units null. A later budget for the same key needs a reason and cannot drop a limit below the charged amount (`resource_budget_below_charged`). Acquisition admission needs room for `max_requests` (at least one request) beside the reservations of every acquisition that is still admitted, and finish charges the attempts and bytes actually used; the reserved amount is the allowance of the admitted operations, so an interrupted acquisition holds its allowance until a new request id for the same operation and target supersedes it. `read-batch` and `screen-batch` charge their counts and reported usage, counting null usage as unknown. New work over a limit is refused with `resource_budget_exhausted`; usage already spent is always recorded; charged plus reserved at or above a limit is a preparation obligation, except for `development`, whose exhausted limit is a `gate round` obligation; `status --summary` shows every account. |
| `read-batch` | `id`, `depth`, `items` | `usage`. Records 1 to 100 abstract readings in one event, all or none. Each item is `{version_id, note, notes: {seven fields: {text, status}}}` with optional `screening: {relevance, reason, conventions}`, `audit: {relevance, reason}`, `consequential: bool`, `placement: {position, reason}` where position is `above`, `below` or `unplaced`, `loop: {purposes, disposition, source}` where source is `search`, `population`, `citing` or `author`, `innovation_candidate: true`, and `search_hit: true`; the harness derives the whole-abstract span inspection itself. The recorded policy decides which extras apply: `loop` and `innovation_candidate` under `lineage-v1`, `placement` and `search_hit` under `sampled-v1`, and an extra of the other policy fails with `invalid_batch`. The catalog example carries `loop` on its first item and `placement` on its second so that both shapes are visible; one batch carries only the extras of its own policy, and the `README.json` of a `batches` export states the shape for that export's mode. A `placement` judges a member of the current sample (`invalid_batch` otherwise). A batch that would take the study past 100 loop readings (`loop_limit_reached`), an open development round past 40 loop readings (`round_loop_limit_reached`), or a verification past 20 search-hit readings (`search_reading_limit_reached`) is rejected whole; the 20 abstracts of a further loop round are a rule the coordinator follows, not one the store refuses. Reading ids are `reading:<sha256 of [batch id, version]>`. The reading covers the version's selected complete abstract; a version without one fails with `abstract_missing`. An `audit` belongs to a currently excluded member (`invalid_batch` otherwise) and is stored with the member's screening `round` at the time of reading. A failing item rejects the batch with `invalid_batch` and the failing `items` (`index`, `code`, `message`). `usage` is `{model, input_tokens, output_tokens, wall_seconds}`, each null when unknown. |
| `screen-batch` | `id`, `items`, `screener` | `round` (default 1), `usage`. Under `screened-v1` only (`policy_inapplicable` otherwise). Records 1 to 200 screenings, all or none; a failing item rejects the batch with `invalid_screening` and the failing `items`. A re-screen needs a round above the member's current round and replaces the member's screening. Each item is `{collection_id (null for a Tier 3 family), work_id, version_id, disposition, relevance, reason, conventions}` with `promotion_reasons` for `promote` and `context` for a reference. Rules: `strong` relevance requires `promote`; `exclude` requires `none` relevance and a complete abstract; `weak` stays `pending`, `doctrine`, or `promote`. `screener` is `{kind: agent|human, model}`. A screen is never a reading. |
| `screening-checkpoint` | `id`, `batch_ids`, `reason` | Under `screened-v1` only. Accepted when the two named batches are the two most recent `read-batch` records, both contain only `pending` members, and every item of both was judged `consequential: false`; otherwise `invalid_screening`. While it covers, unread `pending` members carry no obligation and are counted as `inventoried_unread`; a later consequential batch, a higher screening round, or a policy change removes the effect. |
| `sample` | `id`, `collection_id`, `size`, `seed` | Under `sampled-v1` only (`policy_inapplicable` otherwise). Draws and records the verification's sample of one frozen population, stratified by month with equal allocation, a short month's shortfall passed to the next months, and the whole population when it fits within `size` (1 to 100). The enumeration must be complete (`collection_pending`). The record keeps the seed, the per-month population and sampled counts, the population digest and the drawn members; the draw repeats from that seed, and two verifications of one paper choose their own seeds and read different members. A second draw against the unchanged population is refused with `sample_exists`; a population that changed after the draw reports `sample_stale`, which a new draw clears. Every sampled member then owes an abstract reading (`sample_reading_missing`) carrying a placement (`placement_missing`), and a scoped collection that is not the sampled one reports `sample_missing`. |
| `loop-close` | `id`, `purposes` | Under `lineage-v1` only (`policy_inapplicable` otherwise). Closes the bounded loop. `purposes` names all five search purposes exactly once, each `{status, note}` with status `covered` or `gap`. A `covered` purpose needs a loop reading judged `relevant` or `contradictory`, or two distinct captured queries for it with no such hit; a `gap` needs two distinct queries tried, or the loop at its 100-reading limit. The record keeps the queries each purpose tried and a digest of the loop it closed: a later loop reading or search reports `loop_closure_stale`, and no closure at all reports `loop_closure_missing`. |
| `search` | `id`, `profile`, `purpose`, `queries`, `responses`, `captured_at`, `scope`, `found_work_ids`, `verdict`, `cited_work_ids`, `impact`, `gaps`, `dispositions` | Optional `resolved`. Bind the original query and every result. `nothing-new` needs an actual captured search, including an actual empty result array when appropriate. `dispositions` judges every found work once: `{work_id, disposition, reason}` with `relevant`, `contradictory`, `potentially_relevant`, `out_of_scope`, `duplicate`, or `unresolved`; cited works are relevant or contradictory. A new search for a purpose carries forward each `contradictory` or `unresolved` work of the selected search or lists it under `resolved: [{work_id, reason}]`, else `search_findings_dropped`. A judgment is stale when its scope changes (`search_scope_stale`), when a root, required full text, found or cited work changes content (`search_evidence_stale`), or when a family enters or moves in the citation graph (`search_frontier_stale`); a new version of an unrelated reference changes nothing. Searches recorded before dispositions existed report `search_dispositions_missing`. Under `lineage-v1` and `sampled-v1` a captured query keeps at most 10 hits, counted across every response of that query; rank the results and keep the top 10 (`invalid_search` above that). A capture also enumerates completely: a page that returns fewer records than its reported total is `search_response_incomplete` and leaves the search a `search_pending` obligation on the foundation, so write a native query that returns at most ten results in total, or import the results as a mapped capture and acquire each kept hit with `acquire` before it is read. |
| `require-fulltext` | `id`, `profile`, `version_id`, `purpose`, `reason` | Optional `historical_cutoff`, `depends_on`. Purposes: `major_claim`, `novelty`, `innovation`, `validity`, `exemplar`, `lineage` (the parent's own line of results), `classic` (a foundational paper that line rests on), `core` (a paper whose full text decides a verdict's stance or its prediction band) and `contradiction` (a source that opposes a claim). `lineage`, `classic` and `core` name the claim, lineage entry or finding they serve in `depends_on`. An eleventh `core` requirement is refused with `core_limit_reached`. Every `lineage` and `classic` entry is cited in the pinned bibliography (see "Review, publication, and verification"). An `exemplar` requirement under the `research` profile, recorded while a round is active, is that round's exemplar (see "Development rounds"). |
| `availability` | `id`, `profile`, `version_id`, `depth`, `source_ids`, `reason`, `policy` | Policy has `id`, `minimum_attempts`, `allowed_statuses`, `rationale`. Two policies qualify: captured terminal HTTP 403/404/410/451 failures, or (abstract depth, non-arXiv work) `allowed_statuses` `[200]` with complete captures of the work from every registry that addresses its identifiers (Crossref and OpenAlex for a DOI, OpenAlex for an OpenAlex id, the saved web/MCP import for a `url:` work), none carrying an abstract. Critical sources remain required. |
| `select-cohort-abstract` | `collection_id`, `work_id`, `unresolved_assertion_id`, `selected_assertion_id`, `reason` | Resolves a retained versionless family member to same-family exact abstract evidence. It cannot replace a known v1 obligation with v2. |
| `visual` | `link`, `url` | Optional `max_requests`; acquires an asset referenced by the saved source. |
| `standards` | `id`, `profile`, `scope`, `field`, `article_type`, `venue`, `cohort_doctrine`, `methodology`, `reporting`, `citation`, `presentation`, `applicability_questions` | `article_type` and `venue` may be null; evidence claims retain scientific status, timing, assumptions, and uncertainties. |
| `rationale` | `id`, `profile`, `scope`, `and`, `but`, `therefore`, `value` | Research only. The established context in `and` must be supported as scoped; a proposed or refuted context is retained but pending. |
| `innovation` | `id`, `profile`, `scope`, `cases` | Research only. Optional `origin_collection: {id, version}`. Cases separately describe original constraint, conceptual change, validation, adoption, and tested transfer limits. |
| `context` | `id`, `profile`, `scope`, `current`, `historical`, `beneficiaries`, `capabilities`, `barriers`, `uncertainties`, `speculative_links` | Research only. Basic science does not require an invented immediate application. |
| `grand-challenge` | `id`, `reason`, `challenges` | Research only (`configuration_missing` without a research configuration, `profile_inapplicable` in a verification workspace). Records the challenges ahead of the study before the objective is written: each challenge is `{id, horizon: ultimate|near_term, statement, state, criteria: [{id, statement}], evidence}`, at least one is `ultimate`, and criterion ids are unique across the record (`invalid_grand_challenge`). Evidence is a source read in full or an execution result, validated as in `round` (`invalid_development` for a malformed item, `reading_missing` for a source without a full reading). The preparation gate owes `grand_challenge_missing` until the first record exists. The latest record is current for the whole study; a new record with a reason replaces it. It is outside the preparation digest, so recording it never makes a pinned bundle stale. `status` reports the current record. |
| `cycle` | `id`, `author`, `objective`, `scope`, `hypothesis`, `question`, `strategy`, `predecessor`, `inheritance`, `reopening`, `distinguishing_test`, `expected_outcomes`, `failure_signals`, `evidence_requirements`, `literature`, `resource_limits` | The full nested example includes a first cycle. Successors retain checkpoint lineage and account limits. |
| `admit` | `id`, `cycle_id`, `plan_digest`, `command`, `reserved_units` | Command has `argv`, `program`, `inputs`, `versions`, `seed`, `seed_reason`. This reserves resources; it does not launch. |
| `bind-run` | `admission_id`, `script`, `backend`, `timeout_seconds`, `inputs`, `outputs`, `usage_unit` | Backend is `local` or `colab`; usage is planned `execution` or `wall_seconds`. Inputs map each admitted artifact to a relative path. |
| `reconcile-run` | `admission_id` | Optional `resolution`, `reason`: `interrupted` for a confirmed dead local owner, or `not_released` for a Colab claim with no durable release. Unknown released remote work remains pending. |
| `result` | `id`, `cycle_id`, `origin`, `command`, `status`, `exit_code`, `usage`, `outputs`, `notes` | Public JSON permits imported history only. Its exact `origin.original`, `reason`, and `deduction` fields are shown in the example. Managed outcomes come from run/reconcile. |
| `assess` | `id`, `cycle_id`, `author`, `scope`, `execution_ids`, `result`, `validity_checks`, `outcomes`, `failures`, `findings`, `assumptions`, `remaining_obligations`, `objective_status`, `disposition`, `development` | Full nested example includes outcome judgments, distinct validity checks, and development/branch assessments. |
| `checkpoint` | `id`, `cycle_id`, `assessment_id`, `reason`, `next_hypothesis`, `select_for_readiness` | An unassessed unresolved checkpoint may have null assessment; it supplies no validated-result credit. |
| `review` | `id`, `candidate_digest`, `assessor`, `verdict`, `checks`, `limitations` | Checks separately cover `validity`, `scope`, `novelty`, `contribution`, `development`, `branches` and their exact evidence. |
| `manuscript` | `id`, `files`, `claim_evidence` | Refused with `contribution_analysis_missing` while the selected bundle has a complete measurement and no `contribution-analysis`. Files contains `pdf`, `abstract`, `bibliography`, `claims`, `sources`; `sources` may be null. Each claim mapping has `claim_id`, `evidence`. `claims` is a nonempty JSON array of objects with nonblank text `id` and `claim` and unique ids (`publication_claims_missing` for a non-array, an empty array, a non-object entry or a repeated id; `invalid_input` for a missing or blank `id` or `claim`). A claim carries at most one marker, `revised: {previous, reason}` or `superseded: {reason}`, with exactly those fields as nonblank text (`publication_claims_missing` otherwise). |
| `manuscript-review` | `id`, `bundle_digest`, `assessor`, `review: ArtifactRef`, `blind: true` | The referenced original rubric JSON is unchanged and carries `changes_for_maximum` (`skills/evaluate/RUBRIC.md`); a review recorded before 0.42.0 without it keeps its credit. Latest applicable reviews from two distinct independent assessors must accept. |
| `bind-verdict` | `id`, `task_digest`, `body: ArtifactRef`, `assessment` | Assessment has `assessor`, `provenance`, `independence_basis`, `blind: true`, and separate `soundness`, `novelty`, `impact` checks with full-read links. |
| `manuscript-prediction` | `id`, `bundle_digest`, `blind: true`, `assessor`, `prediction`, `reasons` | `prediction` is `{corpus, category, windowStart, windowEnd, percentile, band: {best, worst}}`; the four cohort fields equal the study's frozen collection definition (`prediction_cohort_mismatch` otherwise). `percentile` and both band ends are "top X%" of the cohort, where 1 is the strongest position, with `best <= percentile <= worst` as integers from 1 to 100. One prediction per assessor per exact bundle (`manuscript_prediction_duplicate`); the assessor is not a cycle author. A bundle takes three predicting assessors; a fourth is refused (`manuscript_prediction_excess`), so a complete measurement stays complete. Recorded and summarized as a result: `round` and `contribution-analysis` need the complete measurement, and no gate rule reads the predicted values. |
| `contribution-analysis` | `id`, `bundle_digest`, `investigation`, `position`, `reviewer_changes`, `steps` | `bundle_digest` names the selected bundle (`contribution_analysis_stale`), which needs a complete measurement (`manuscript_measurement_missing`); one analysis per bundle (`contribution_analysis_duplicate`); a current Grand Challenge record exists (`grand_challenge_missing`). `investigation` is a nonempty list of `{query, response: ArtifactRef, finding}`: the original response of each query, saved with `artifact`, and what it shows about the challenges ahead. A query with its response serves one analysis (`contribution_investigation_reused`); the harness compares the query and the response bytes and no more, and the round assessor receives the responses. A malformed `response` fails as any artifact reference does (`invalid_input`, `unsafe_path`), and one whose bytes are not stored with `artifact_missing`. `reviewer_changes` dispose of every contribution change of the three measurement reviews once, `{review_id, change, disposition: adopted|rejected, step_id (adopted only), reason}` (`reviewer_change_missing`). `position` is `{criterion_ids, established, remaining, evidence}`. Steps are `{id, statement, criterion_ids, direction, reach: this_round|next_round, builds_on (current claim ids, `contribution_claim_unknown` otherwise), community: {who, capability, evidence}, risks, evidence}`. Evidence is validated as in `round` (`invalid_development`, `reading_missing`, `round_evidence_mismatch`); any other malformed field fails with `invalid_contribution_analysis`. The record keeps the id and digest of the Grand Challenge record its criterion ids were checked against. |
| `round` | `id`, `closes`, `decision`, `bundle_digest`, `candidates`, `carried`, `next`, `reason` | Needs the bundle's complete measurement and its `contribution-analysis` (`manuscript_measurement_missing`, `contribution_analysis_missing`); the candidates list every step of that analysis (`contribution_step_missing`), and a `continue` goal names `criterion_ids` of the current Grand Challenge record. `decision` is `continue` (exactly one `pursue` candidate and `next` present) or `stop` (at least one candidate, no `pursue` candidate or carried development, and `next: null`). Candidates are `{id, direction: vertical|horizontal, statement, disposition: pursue|rejected|deferred, reason, evidence}`; evidence takes the `source` and `result` shapes or `{kind: "review", review_id}` naming a manuscript review of this bundle; a `source` item needs a full reading that inspects that link (`reading_missing`). `carried` disposes of every `next_round` development the closing round's cycle assessments recorded: `{assessment_id, kind: alternative|branch, question or cycle_id, disposition, reason}`. `next` is `{number, objective, objective_lineage, goal, resource_limits, reopening}`; the goal has `direction`, `criterion_ids`, `field_change`, `statement`, `contribution_delta`, `beneficiaries`, `success_criteria` (`kind` `claim` or `scope`), `stop_conditions`, `continuity`, `route`, `risks`, `evidence`. See "Development rounds". |
| `round-review` | `id`, `round_id`, `round_digest`, `assessor`, `verdict`, `checks`, `limitations` | `verdict` is `approved`, `not_approved`, or `unresolved`. For a `continue` decision the checks are `impact`, `demand`, `novelty_risk`, `feasibility`, `distinctness`, `continuity`, `grand_challenge`; for `stop` they are `stop` and `demand`; each is addressed once with `status` (`passed`, `failed`, `unresolved`), a reason and evidence. One review per assessor per decision (`round_review_duplicate`); the assessor is not a cycle author. |
| `round-admit` | `id`, `round_id`, `review_id`, `reason` | Opens the round of an approved `continue` decision on the current bundle. Records the round's number, goal, objective, limits and opening state; applies a widened objective through its lineage. While the round runs, its receipt is the only output that reports the admitted goal (see "Development rounds"). |
| `round-assess` | `id`, `round_id`, `bundle_digest`, `criteria`, `stop_conditions`, `summary` | Judges every success criterion and stop condition of the admitted goal once with `status` (`observed`, `not_observed`, `unresolved`), an explanation and evidence, on the exact current bundle. The harness derives and stores the round's progress with the record. |

A fulltext note uses `depth: "fulltext"`, its current `bundle_id`, and inspections for the actual required units. Abstract inspections use `unit_id: null` and cover the complete saved abstract. Notes report `present`, `absent`, or `not_applicable` as supported by the source. Saving text about a paper without matching its captured location cannot satisfy a reading.

A retry deadline exposed as `next_eligible_at` is a pending condition until that deadline. Resuming earlier can make zero requests and preserve all evidence. An expired deadline is retained history, not a permanent block. Unversioned arXiv material remains unresolved until exact-version evidence is explicitly selected. PDF extraction uses installed `pdftotext` with bounded execution; unavailable extraction remains pending.

## Actual execution

`bind-run.script`, input paths, and ordinary output paths are relative to `experiment/`. Outputs `stdout` and `stderr` select the captured streams. Each declared output has `{id, requirement_id, path, media_type}`; output IDs and paths must be unique, and a result cannot overwrite an input. The admitted argv begins with a Python executable and the exact declared script. Local execution pins the resolved interpreter's bytes and declared Python version. Other imported command histories are preserved without claiming this launcher observed them.

```sh
exactory-lab run code/program.py --admission run-1 --backend local --timeout 30 \
  --expected-revision 42 --request-id actual-run-001
```

Backend, timeout, and seed must match the binding and admission. A null seed needs its declared reason; a seed is passed as `EXACTORY_LAB_SEED`. The conventional fallback metric path is `results/SCRIPT_STEM.json` inside the private working directory. The process runs in a private copy of the pinned script and inputs. The launcher claims the admission once and rechecks current preparation before releasing its token. Before publishing its terminal outcome, the worker seals the presence or absence, SHA-256, and size of every declared output, both streams, and the fallback metric file. Reconciliation rejects changed, removed, or newly inserted bytes and saves the complete sealed inventory as immutable artifacts. Current author readiness checks the saved result, log, and metric against that original seal. The familiar result/log paths are disposable projections.

A retry returns/reconciles that run; it does not execute the same admission twice. Missing or lost results require `reconcile-run`. A dead local worker may be recorded interrupted after ownership checks. Unknown usage retains its full reservation. An interrupted process, timeout, missing output, or old modeled managed record cannot become current author readiness through a public result JSON label. An imported observation retains its actual provenance and chronological limits and does not consume or satisfy a prospective admission.

Known wall time is recorded for successful, failed, partial, and timed-out processes. Charges retain the reservation floor and add measured overruns. A claimed outcome without its completed observation remains pending for fresh execution, even when its outcome record already exists. Reconcile the original observation before spending remaining strategy budget. If an older pinned terminal contains measured time that its historical account did not charge, that strategy's new admissions and unstarted launches remain pending for accounting reconciliation. Historical payloads, receipts, and charges are preserved; this release does not automatically add a correction or invent unmeasured usage.

Older terminal records without a worker output seal cannot establish a new managed observation or current readiness. Already recorded historical receipts still replay. An explicit dead-owner recovery may retain partial bytes with a recovery seal, which is not a worker completion seal and supplies no completed-result authority. Do not create a replacement seal for old bytes whose original producer binding is unavailable.

For Colab, set `EXACTORY_LAB_COLAB_DIR` to an existing shared folder on both hosts, bind `backend: "colab"`, and declare the actual remote Python version in `command.versions.python`. Run the installed `exactory-lab colab-serve` on the remote host. The client needs the same admitted logical script path, but the remote runner maps it to its private working directory and records the actual executable path and SHA-256.

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

## Review, publication, and verification

```sh
exactory-research export --kind readiness --destination /new/independent/candidate
exactory-research export --kind manuscript --destination /new/independent/manuscript
```

Delivery copies the actual candidate/source/program/output bytes and an `inputs.json` manifest into a new directory. The manifest is a neutral packet: no revision labels, request identities, launcher tokens, author names, assessment history, prior scores or verdicts. A readiness packet carries the candidate, branches, plans, assessments, checkpoints, sources and synthesis sections that the six checks need.

A manuscript packet carries the exact PDF, abstract, bibliography and optional source archive. It derives a separate claims file for the reviewer. This file contains current claims, with `revised` metadata and `superseded` entries omitted. The evidence map and its source/result closure contain only those current claims. The packet also carries the selected field standards. The derived claims file has its own content hash and exists only in the delivery directory. The original ledger and bundle digest remain unchanged. Give this directory to the separate reviewer. A digest list alone is insufficient. Export does not certify comprehension or independence.

`manuscript-review` accepts one review per assessor per exact bundle; a second review by the same assessor on the same `bundle_digest` fails with `manuscript_review_duplicate`, so a rejection stands until the manuscript changes and a new bundle is prepared.

The original manuscript rubric JSON has exactly `summary`, `strengths`, `weaknesses`, `soundness`, `presentation`, `contribution`, `overall`, `decision` and `changes_for_maximum`; the three dimension scores use 1 through 4, overall uses 1 through 10, decision is `accept` or `reject`, and `changes_for_maximum` has exactly the keys `soundness`, `presentation` and `contribution`, each a list that is empty exactly when that score is 4 (`invalid_review` otherwise). A review recorded before 0.42.0 has the first eight fields and keeps its credit. `manuscript-review` adds the external assessor/bundle envelope without changing those original bytes. Changed PDF, abstract, bibliography, claims, optional source archive, current evidence, or review dependencies invalidate the current publication gate.

Under `lineage-v1` the manuscript gate also checks the pinned bibliography for every `lineage` and `classic` entry. An entry matches on its arXiv identifier in family form, so a bibliography that cites one version cites the work; on its DOI; or on its title when that title has at least two words, because a one-word title is too common a string to stand as evidence of a citation. An entry with none of those three cannot be matched mechanically and keeps `lineage_citation_missing` until the bibliography carries one of them. The bibliography is read for citation evidence, not validated: undecodable bytes become replacement characters instead of a failure.

`exactory-draft deposit` runs from the root of a draft workspace, the directory that holds `.exactory/draft.json`, and its existing options remain available, including sandbox, production, source archive, publication confirmation, and new version. When the workspace's publication gate is ready, the round gate is ready, and the upload matches the exact reviewed bundle, the command saves a remote intent before external writes and uploads those validated bytes; `exactory-draft reconcile INTENT_ID` reconciles an unknown outcome before continuing. Otherwise the command creates the record directly from the given files, writing `.exactory/deposit.json`. A store that refuses the managed record prints one line first, `Managed record skipped (<code>): <message>`, with ` Pending: <codes>.` appended when the refusal carries obligations. A workspace with no store, such as a draft workspace initialized before 0.38.0, takes the direct path with no line. A direct deposit prints `Deposition <id> is open on Zenodo: <url>` before the publish, so a lost publish response leaves a record to open rather than a deposit to repeat. In the two cases where the store keeps no record of the direct deposit, a store that refuses the write and a store file that does not open, the command writes `.exactory/deposit.json`, reports that it holds the only local copy, and exits nonzero after it prints the record. Production runs the offline citation check first; a failing check prints one `Citation report:` line and the deposit continues. Local and remote storage are not an atomic transaction.

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

## Development rounds

A development round is one pass of the paper-level loop: a goal that the current
paper does not yet meet, the literature, cycles and manuscript that pursue it, and
an assessment against that goal. Every record binds the exact current publication
bundle; the loop is bounded by records and it ends only through a recorded exit.

Each complete measurement of a bundle is followed by its `contribution-analysis`: a small
investigation kept as captured responses, the paper's position against the study's current
Grand Challenge record, a disposition of every contribution change the measurement reviewers
named, and the steps toward the challenges. The investigation is not a `search`: a search needs
its capture imported, the import changes the record of every held work the capture returns, and
that makes the measured bundle stale. `manuscript` refuses to pin the next bundle while the
selected bundle has a complete measurement and no analysis (`contribution_analysis_missing`),
and `round` reads the analysis of the bundle it decides on. A step that is not pursued is
`deferred` unless the evidence rejects it: a `rejected` candidate can never become a goal, and
a step's statement is fixed by the analysis of its bundle.

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

The goal states the pursued candidate. Its statement never repeats an earlier
`rejected` candidate (`round_goal_repeated`), even with a reopening. It repeats an
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
reading that inspects that link (`reading_missing`).

`round-review` is the independent judgment of one decision. It binds the decision's
digest on the current bundle (`unknown_round_decision`, `round_review_stale`), takes an
assessor who is not a cycle author (`review_not_independent`), and addresses every
check of the decision's kind once. A malformed assessor fails with `invalid_input`
(`review_not_independent` for a kind other than `human` or `agent`), check evidence
is validated as in `round`, and any other malformed field fails with
`invalid_round`. One review per assessor per decision (`round_review_duplicate`); a
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
the selected search per purpose, the full-text requirement ids, the cycle ids, the
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
and a superseded claim does not count as new.

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

`export --kind round --destination DIR` delivers the latest decision on the current
bundle (`round_decision_missing` without one) to the independent round assessor: the
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

## Native math preparation

New native proposals use schema version 3 with the existing computation field and a `foundation` reference. Export current complete preparation before independent proposal review:

```sh
exactory-research export --workspace /study --kind native --attack-root /study/attack \
  --destination /study/attack/research/native-inputs/review-001
```

The manifest returns `foundation` and native `inputs`. Add both to the proposal's existing reviewed delivery. The reference contains `schema_version`, `workspace`, `profile`, `revision`, `snapshot_digest`, `preparation_digest`, and `claim_binding`. Its workspace is `.` or an ancestor expressed with `..`; the delivery remains inside the attack root. The immutable native store imports the snapshot blob and actual source artifacts. A later unrelated common revision does not invalidate unchanged preparation, but changed required sources, policy, scope, or synthesis require a new reviewed amendment before fresh work.

Common author research preparation supports native research and an author's native verification of the same full original objective. It is complete only with a current Grand Challenge record (`grand_challenge_missing` otherwise), like every research-profile preparation since 0.42.0: record `grand-challenge` for the objective before the export. Common external verification preparation supports only native `role: "verification"`. The latter additionally exports `--claim-binding binding.json` with this exact reviewed correspondence:

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

Historical proposal versions 1 and 2 and old terminal runs continue to replay under their original schemas. Before fresh work, use `search amend-foundation NODE --file FILE` with the ordinary revision/request arguments. Its input is `{subject, review, inputs}`. The subject is `{node_id, proposal_digest, previous_snapshot_digest, foundation, reason}`; the previous snapshot is null only when none was previously selected. The independent review uses the existing native proof-review schema, with subject and exact claim digests. The amendment is an append-only native event and cannot alter the claim, budget, accounts, or prior proof acceptance.

Complete retained run/journal reconciliation before an amendment. Recovery, pause, existing journal completion, historical audit, and proof/account replay remain available without pretending new preparation was already done. Admission, new planning/ranking, begin, run, and actual token release check their applicable current frozen foundation under the native writer lock followed by a common-store read. At final launch, the validated common read transaction and native lock remain held through the actual token write and flush. Common mutations cannot commit during that interval; a busy request can be retried unchanged after release. Both locks are released before waiting for the worker or reconciling results. Common mutations never acquire the native lock. Current source-byte checks and the receiver's check before each command remain required. Native proof acceptance and account rules remain separate authorities.

## Status, projections, and recovery

`status` and `next` expose the current revision, study, preparation, actionable obligations, pending admissions, remote intents/observations, retained adoptions, the `round` (number, active, assessed, decision, whether the selected bundle has its contribution analysis, the count of round gate obligations, progress counts, the measurement's medians and spreads, the admitted round's limits, the `development` budget line and the usage since admission), and the `runtime` that produced the report (plugin version, source commit, dirty flag, package digest, constitution digest). `status` also carries `grand_challenge`, the payload of the current Grand Challenge record; `status --summary` reduces it to the record id, the number of `ultimate` and `near_term` challenges, the first six criterion ids and a count of the rest, each id cut at 32 characters. `next` is the highest-priority current obligation in preparation order (configuration, collection, cohort abstracts, objective and roots, critical full text, bundles and units, searches, Tier 3 abstracts, the Grand Challenge record, synthesis); at the cohort stage it is the next unread cohort abstract. At the `evaluate` stage the status obligations also carry the publication gate's and the round gate's obligations, publication first, so the study finishes the manuscript measurement before the round decision.

`status` also carries `limits`, the counts of the recorded policy against its own bounds: `{policy, loop, sample, innovation_candidates, search_readings, core_papers}`. Under `lineage-v1`, `loop` is `{readings, limit, covered}`, the registered loop readings, the 100-reading limit and the purposes already covered, and `innovation_candidates` is `{families, required}`. Under `sampled-v1`, `sample` is `{n, placed, unplaced, percentile, band, widen_required}` once a sample is drawn and null before that, `search_readings` is `{readings, limit}` against 20, and `core_papers` is `{requirements, limit}` against 10. A field that the recorded policy does not bound stays null. The verifier reads the percentile and the band it files from `limits.sample`.

The optional `--summary` flag on `status` and `next` returns a bounded advisory view. It runs the same current-state evaluation as the default command. It does not change research obligations, authorize a transition, or reduce required reading. `status --summary` is limited to 16 KiB and `next --summary` to 4 KiB of UTF-8 JSON. Counts distinguish displayed and omitted obligations. Hints may omit or truncate details and must not be used directly as mutation payloads. `status --summary` also reports `evaluation` counters (objects verified, readings assessed, graph builds, elapsed seconds); they describe this invocation and are never stored. The default `status` and `next` responses are unchanged.

`batches --depth abstract --size N --destination DIR [--profile PROFILE] [--screen]` writes the current unread abstracts (cohort members first, then Tier 3 references) as `batch-NNN.json` files with each version's selected complete abstract text, the exact link, and a `README.json` naming the notes shape that `read-batch` accepts. `--profile` defaults to the configured profile. It reads the store and writes only under the new destination directory (`review_destination_exists` when it exists; `migration_required` without a configuration); a reader agent works from one file and returns one notes file, and a single coordinator submits it with `read-batch`. With `--screen` it writes `screen-NNN.json` files of the unscreened members for `screen-batch`, under `screened-v1` only (`policy_inapplicable` otherwise). The `README.json` names the shape of the export's own mode: the screening shape with `--screen`, the loop shape with `--loop`, the placement shape under `sampled-v1`, and the plain notes shape otherwise.

`batches --loop [--candidates FILE]` writes the loop's unread abstracts instead, under `lineage-v1` only (`policy_inapplicable` otherwise): the found works of the selected searches that carry no loop reading yet, followed by the caller's candidates. `--candidates` reads a JSON file that is either a `population-query` result or a plain list of version ids (`invalid_input` for anything else), and it needs `--loop` (`invalid_input` without it). The exported `README.json` carries the loop item shape, `{loop: {purposes, disposition, source}, innovation_candidate}`, and the note that `innovation_candidate` marks one of the ten candidates and stays out of every other item. Under `lineage-v1` a plain export is refused with `policy_inapplicable`, because this policy asks for no reading of the enumerated population: its abstracts come out with `--loop`.

`population-query --terms TERM [TERM ...] [--limit N]` is read-only. It ranks the enumerated population's stored abstracts by how many of the terms appear in the title or the saved abstract, each term matched as a whole word or the start of one and ignoring case, and returns `{terms, population, matches, read_only}` with each match as `{version_id, work_id, title, matched_terms, score}`, highest score first. `--limit` is 1 to 100 and defaults to 30. The result is the local source of the lineage loop: pass it straight to `batches --loop --candidates`.

Under `screened-v1` the cohort gate requires a screening for every member (`screening_missing`), an abstract reading for every `promote`, `doctrine` and (until saturation) `pending` member, an audit reading with `audit: {relevance, reason}` for a deterministic sample of at most 150 excluded members per collection (`screening_audit_reading_missing`; any accepted reading of the version can carry the audit; a `strong` audit judgment is `screening_audit_failed` until the paper is promoted and every excluded member is re-screened in a round above the audited one), and at least 8 read `doctrine` or `promote` members per calendar month of the window, or every member of a smaller month (`doctrine_coverage_missing`). Tier 3 references need a screening keyed by family with `context`; excluded references are sampled the same way. `require-fulltext` families and unresolved references keep their obligations whatever their screening says. `batches --screen` exports the unscreened members for `screen-batch`.

Under `sampled-v1` the cohort gate requires a current sample of the scoped collection (`sample_missing`, and `sample_stale` once the population changed under it), an abstract reading of every sampled member (`sample_reading_missing`), and a placement on each of those readings (`placement_missing`). No other member owes a reading. Under `lineage-v1` no cohort member owes a reading at all, and a pending acquisition is reported as a notice rather than an obligation, so the gate passes while the enumeration is still incomplete. Both policies drop the Tier 3 obligation on the references of a full-read paper; the bibliography is still inventoried for identity.

`policy-report [--policy POLICY] [--reference FILE]` is read-only. It reports each collection's members, readings, unscreened count, dispositions, audit sample and selected preparation set under the recorded or a hypothetical policy, whether a saturation checkpoint covers, and, with a reference file `{"prior_art": [...], "contradictions": [...], "methods": [...], "doctrine": [...]}` of family ids, the recall of the selected set per category with the missed families listed. `--policy` takes any of the four policies. For `lineage-v1` and `sampled-v1` the report is `{revision, policy, recorded_policy, limits, mechanical_only}`, because a bounded policy prepares a loop or a sample rather than a selection out of the population; `limits` holds the same counts as `status.limits`, read from the recorded workspace, so naming a hypothetical bounded policy does not recompute the counts under it.

`obligations --code CODE [--limit N] [--cursor CURSOR]` returns one page of the current obligations that carry `CODE`, ordered by exact version. The current obligations are the ones `next` follows: the cohort stage's while it has any, otherwise the readiness obligations. A page carries `total`, `offset`, `returned`, and `next_cursor`. A cursor is `REVISION:OFFSET`; a cursor whose revision differs from the current store fails with `stale_cursor`, so a listing never mixes revisions. On resume, read `status --summary`, then `next --summary`, then the `obligations` page for the code you are working on; run the default detailed command only when the full nested report is needed.

`gate ACTION` supports `cohort`, `foundation`, `preparation`, `readiness`, `execution`, `verification`, `manuscript`, `publication`, `deposited`, `submitted`, and `round` and exits nonzero for unmet obligations.

SQLite is authoritative. Workspace JSON, decision logs, and familiar result views are projections. Editing them cannot authorize work. `export --kind workspace` rebuilds workspace projections from current history; run reconciliation repairs execution projections. `exactory-lab decide` retains immutable decisions with revision/request identity and preserves any old decision-log bytes. Explicit `adopt` archives selected legacy paths and exposes pending obligations without rewriting their old scientific claims.

A genuine hot SQLite rollback journal makes default reads return `store_recovery_required`. Use `exactory-research recover`, optionally with `--expected-revision`, to request native SQLite rollback recovery after path checks. This preserves committed history, migrates no schema, resets no accounts, and satisfies no research gate.


### Import an original OAI cohort traversal

`import-oai-cohort` repairs enumeration of an existing collection without changing its four-field frozen definition. It supports primary category `math-ph` and the official `physics:math-ph` subject set only. Supply one original XML response per `pages` entry, ordered from the first `ListRecords` request through the terminating response. `response_file` is relative to the research workspace; absolute paths, traversal and symlinks are rejected. The command validates every record before filtering, including records outside the frozen submission window. It rejects incomplete or repeated tokens, duplicate article identities, protocol errors, deleted records, malformed fields, missing categories, invalid dates and incomplete version-number histories.

The source URL must use exactly `https://oaipmh.arxiv.org/oai`. Its first query is exactly `verb=ListRecords`, `metadataPrefix=arXivRaw`, `set=physics:math-ph`; later queries contain only `verb=ListRecords` and the preceding opaque `resumptionToken`. The XML request attributes must agree. The XML request base text alone may use the same official HTTP endpoint, as the original service does. This exception does not verify the caller's transport. Capture and response times must be ordered within one UTC day. Token expiration metadata is preserved; a later local import does not reapply token expiry.

Version numbers must be contiguous and unique, beginning at v1. The greatest version number supplies the current exact ID; v1's original submitted date supplies the inclusive UTC submission-window filter. Every version date must be valid and no later than the original response time. Original dates are preserved even when later version numbers have earlier dates. Those cases receive `nonmonotone_version_dates` diagnostics in source-bound assertions and the projection ledger, and `date_anomaly_count` in the receipt. Such an assertion cannot by itself establish historical priority from its current-version date; independent valid temporal assertions remain subject to the existing cutoff rule.

Primary category is the first token in the original current category sequence, which is preserved in full with its page/record locator. This follows the official OAI template's verbatim `current_meta.abs_categories` and arxiv-base's primary-first parser/serializer. Inspected upstream commits are `arXiv/oaipmh@27ad99e3e37ab7919869bd3d4cd24449dea78135` (`oaipmh/templates/record_formats.xml`) and `arXiv/arxiv-base@f835347408851239ba23d2171f7f10916799cf62` (`arxiv/document/parse_abs.py`, `arxiv/base/templates/base/macros.html`). The raw author string is retained without inventing author boundaries. An absent optional abstract remains missing, with native reading obligations; a present malformed or empty abstract is rejected.

The receipt describes enumeration of the supplied observed chain, projected by original submission dates and current primary categories. It does not establish historical category assignments or a transactional snapshot of the mutable provider. All sources retain original bytes, `capture_method=external_import`, `origin_verified=false`, `http_status=null` and no caller-authored HTTP headers. `external_response_count` counts supplied response bodies, not external retries or total HTTP attempts. Native network usage is zero. The exact imported response bytes are checked against the byte budget and charged once, even when no network budget remains.

One CAS transaction publishes the immutable history snapshot, all sources and projected works, the new enumeration epoch, the resource charge and the finished receipt. The previous partitions, pending causes, counters and pacing state are saved in native enumeration history; old sources, pages, operations, receipts and family observations are preserved. `source_count` and `returned_count` remain cumulative; `enumeration_counts` separates this traversal's returned, projected, member and out-of-window counts. The partition total is explicitly a `local_submission_date_projection`, never a provider-reported total. Active collection acquisitions are rejected. Request replay binds the collection and ordered original hashes, sizes, URLs and capture times; changing a filename alone does not change identity.

All original pages, including pages with no members, the complete identity/date projection ledger, source assertions, parser provenance and history are dependencies of collection status and cohort preparation. Missing or corrupt evidence fails those checks, and a new epoch changes synthesis dependencies. Previously observed families absent from the new date projection still produce the existing population-change obligation. Existing exact-version abstracts and readings retain their ordinary rules; a later conflicting abstract is retained as a separate source assertion. Importing metadata creates no reading, screening, standards, readiness or admission record.
