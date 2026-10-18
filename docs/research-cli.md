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

Both reviewers must receive the decisive source content and claim in their
observed packet histories, source-request continuations or bound supplements.
Coverage requires every inspected range and every image asset named by its
inspection. Long text ranges are delivered in consecutive chunks of at most
2,000 characters; PDF inspections use the checked original within the 64 MiB
per-artifact delivery bound. Qualifying historical attachments retain coverage.
A descriptor, inventory entry, reading note or assignment date alone supplies
no delivered-source coverage. Missing ranges or image bytes leave
`direction_evidence_unassessed`. The decision stores
`direction_evidence_digest`; changed decisive evidence reports
`direction_evidence_unassessed` and requires a new decision with reviews that
saw it. Target commitment applies that same minimum and review-coverage rule.

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
| `review-route` | Register the explicit instrumented provider, model and resource configuration. |
| `review-probe` | Execute and retain the input-boundary positive-control test. A failed test establishes no independence. |
| `review-assignment` | Freeze the canonical role, exact allowed packet, reviewer identity and current verified route. |
| `review-run` | Execute that assignment once, retaining actual output, usage and failed or interrupted attempts. |
| `review-attempt` | Preserve imported output or failure history; imported assertions never establish verified independence. |
| `review-context` | Record evidence of later contamination or other context observations. |
| `review-adjudication` | Bind the observed fresh assessor's focused objection or correction finding. |
| `value-review` | Record the exact observed bar, slate or combined support/value result. |
| `research-decision` | Select the canonical scientific action, artifact disposition and full-goal status. |
| `source-impact` | Bind an explicit impact classification for the complete source delta since the selected decision. |

`research-decision-assess --boundary target|cycle|write|publication|round`
returns the same obligations consumed by direct mutations and gates. It accepts
an optional `--decision-id`. `strategy-packet --dossier-id ID --role ROLE
--reviewer-id ID` exposes the exact role packet; additional explicit role context
uses `--context-file`. `review-route-assess --route-id ID` reports the current
route test. These reports do not mutate the workspace.

An initial `bar` assignment can supply `context.source_links`, using the exact
source Link shape in `example read`. These links select previously inspected
passages with `text`, `span` or `json` locators for the assessor's initial context.
Request visual originals through `source_requests`; receipt is not proof of
visual inspection. The complete held-source inventory
is bound separately and available through paged requests; metadata alone does
not establish source delivery or reading. Replace every placeholder in
`example review-assignment` with an actual saved source and locator.

A `bar`, `slate` or `result` response can include `source_requests` for an `inventory_page`, an
`inventory_query`, or an exact `source`. The [reviewer delivery contract](research-decisions.md#reviewer-delivery-and-disagreement)
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

This retains the original reviewer slot, scientific findings and objections.
Previously delivered figures and PDFs return as actual attachments, and supplied
excerpts retain their original bounds.
Run its new assignment normally, then record the unchanged observed response,
including the required `reassessment` findings described in the
[repair contract](research-decisions.md#reviewer-delivery-and-disagreement).
Later exposure may invalidate its current approval while its exact original
prospective provenance still permits historical recording. Bind a successor
repair to all current events before using the finding as independent approval.

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

The store writes only JSON that every supported Python reads back. The payload, each record that a mutation writes, and its result must hold no integer of more than 4300 digits and must nest at most 100 levels. A mutation outside these bounds fails with `invalid_input` and does not change the revision. The CLI parses the payload file before the store checks it, and a file that the interpreter cannot parse fails with `invalid_json`. For example, an interpreter that limits integer string conversion (`sys.get_int_max_str_digits()`, 4300 digits by default) cannot parse a longer integer. Reads and retries of committed requests do not check these bounds, so a store that an earlier release wrote stays as readable as before.

Later records and reviewer exports hold copies of the values of a command input deeper than the input holds them. So `exactory-research` accepts a payload file only when it nests at most 32 levels, and `bind-verdict` accepts a verdict body of at most 32 levels. A `review-assignment` of a paper review (role `manuscript` or `standalone`) repeats the stored bundle of `manuscript` in `context.bundle`, and the assignment requires that bundle to equal the stored record. So the bound counts the rest of that payload and not the bundle. A reviewer export reads JSON of at most 40 levels, and the deepest copy found in one is 9 levels deeper than the input. There, the packet of a review assignment with role `result` in a source-limited study holds the readiness export one level deeper, with each full reading, so a `read` payload of 32 levels reaches 41 levels. Such a payload nests deeply only inside the locators of its evidence links. The export does not add the levels inside a locator to the depth of the reading. The deepest copy found whose levels the export counts is 8 levels deeper, where the same packet holds the `component` of a `fulltext` payload. So an input of 32 levels reaches the 40 levels of an export. The deepest record copy found is 12 levels deeper, where a paper review assignment holds the bundle of `manuscript`, which holds the same component 10 levels deeper than the payload. So an input of 32 levels reaches 44 levels in a record. A deeper input fails with `invalid_input` and changes nothing, unless it retries a committed request. A run's metric has had the same bound since 0.48.0, and a line or file that nests deeper gives no metric (see "Actual execution"). A later payload can still cite an accepted value at a deeper position, and then it fails if it nests more than 32 levels. For example, `review` cites every evidence reference of the candidate one level deeper than `result.evidence` of an assessment holds it, and `manuscript` cites the evidence of its claims at the same depth. A `value-review` holds such a support assessment in its `support` field, one level deeper again. So an assessment whose deepest value in `result.evidence` is at level 31 leaves no room for its `value-review`, and one at level 32 leaves no room for its `review`.

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
| Objective | `{ "kind": "objective", "id": string, "statement": string }`, preserving the complete original research objective. |
| Verification target | `{ "kind": "work", "id": string, "source_id": string or null, "sha256": string or null }`. The two nullable fields are both null during incomplete preparation or both identify the available original main body. |
| Result evidence | `{ "kind": "result", "execution_id": string, "output_id": string, "artifact": ArtifactRef, "locator": Locator }`. The artifact must be an actual output of that execution. |
| Source evidence | `{ "kind": "source", "link": Link }`. Consequential synthesis, development and round evidence requires the applicable full reading. |
| Assessor | `{ "id": string, "kind": "human" or "agent", "provenance": ArtifactRef, "relationship": string, "independence_basis": string }`. Identity declarations and provenance are retained, not independently authenticated. |

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
presets supply default amounts; verification retains its fixed contract.

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
| `init` | `profile`, `target` | `preparation_policy`: `lineage-v1` (the `research` default; the lineage and the classics in full, a bounded five-purpose loop with default limit 100 abstracts, and no cohort member reading), `sampled-v1` (the `verification` default; a stratified random sample of at most 100 members, each read with a placement; see `sample`), `exhaustive-v1` (every cohort member and Tier 3 reference owes an abstract reading) or `screened-v1` (a recorded screen selects the preparation set; see `screen-batch`). Profiles are `research` and `verification`; `lineage-v1` and `sampled-v1` each belong to one profile (`policy_inapplicable` on the other). Without this field the profile's default is recorded. Acquire exact metadata before initializing a verification target. A configuration recorded before this field existed is `exhaustive-v1`. |
| `policy` | `previous`, `policy`, `reason` | Changes the preparation policy; `previous` must name the recorded policy (`policy_conflict` otherwise). Synthesis reports retain their configuration binding. Managed claim currency follows the claim projection below; a policy change alone leaves a scientific claim current. Other stores retain their existing dependency checks. |
| `adopt` | `id`, `profile`, `target`, `files: string[]`, `reason` | Archive selected legacy bytes; no implicit completed readings or new execution credit. |
| `target` | `target`, `reason` | Optional `authorization`. Verification repinning is explicit and requires matching subsequent roots. A fixed research objective changes only with `authorization`: the `id` of an `artifact` pin whose path is under `context/` and whose bytes hold the user's explicit instruction (`objective_locked` without it or when an earlier change used the same bytes; `invalid_target` for an unknown pin, a pin outside `context/`, an empty file, or `authorization` on a first or unchanged target). The harness checks the pin and its bytes, not who wrote them: save only the user's own words, never an instruction you wrote. The new objective needs a new `id` and a statement that differs after casefolding and whitespace collapsing (`objective_locked` otherwise). The direction of the change is not checked; the reason states it. The earlier objective stays in `research_objective`, and `objective_lineage/{new id}` records `{id, predecessor, relation: "user_authorized", reason, authorization: {id, path, artifact, text}, round_id: null}`, so earlier checkpoints stay inheritable. The readiness candidate and the round review packet carry these changes as `objective_changes`. The changed objective reaches dependent claims through their objective and scope. Reassess affected evidence (`inherited_result_stale` for a stale successor) and use `inheritance_refresh` when replacing an inherited assessment. Synthesis reports retain their own configuration dependencies; unrelated global preparation changes alone leave managed claims current. A math-solver search bound to the workspace refuses the changed objective (`research_objective_mismatch`), as after a widening. |
| `constitution` | `previous_sha256`, `reason` | Adopts the current distributed policy. Research adoption of Version 7 alone leaves claims current. An adopted Version 6 can remain in use without revalidation; verification retains Version 6 in this release. |
| `artifact` | `id`, `path`, `media_type` | The local path must remain inside the workspace without traversing symlinks. |
| `collect` | `definition: {corpus, primaryCategory, windowStart, windowEnd}` | `max_requests`, `page_size`; current corpus is `arxiv`. Dates are ISO calendar dates. |
| `resume` | `collection_id` | `max_requests`; resumes retained collection/page evidence. |
| `acquire`, `expand` | `identifier` | `provider`, `max_requests`; expansion acquires an unresolved identifier without asserting its bibliography has been read. An old arXiv archive name without its hyphen, as in `arxiv:astroph/0410063`, is read as the hyphenated archive (`arxiv:astro-ph/0410063`). |
| `fulltext` | `identifier`, `url` | Optional `component` declares a pinned supplement relationship (see below). `max_requests`; `extraction_options: {"layout": bool}` (default true) chooses the PDF extractor's layout mode. For scanned PDFs, `extraction_options: {"ocr": true}` uses bounded English OCR with pdfinfo, pdftoppm and Tesseract, retaining every PDF page boundary and actual tool versions. OCR has a 600-second deadline, 200-page limit, 5000-pixel maximum page dimension, and 32 MiB limits for each rendered page and total text. OCR and layout options are mutually exclusive. Interpret OCR text against the original page images; the harness records the tools and does not check the text. An original up to 128 MiB is captured; a larger response fails with `response_too_large`. Preserves the exact original and extraction result, and records `extraction` (extractor, version, options, text bytes, page count, longest line, whitespace fraction, expansion ratio) on the capture. A capture with other options is a distinct capture of the same original; a reading on any capture of that original satisfies its full-text coverage. |
| `import-response` | `provider`, `response_file`, `source_url`, `captured_at` | `media_type`, `mappings`; `web`/`mcp` mappings use JSON pointers into the original saved response for every work. |
| `import-oai-cohort` | `collection_id`, `pages: [{response_file, source_url, captured_at}]` | Existing arXiv math-ph collection only. Each file is an original XML response at a checked workspace-relative path. Supply the complete ordered arXivRaw whole-set token chain; no authored works, totals, flags or HTTP receipts. |
| `roots` | `profile`, `roots: string[]`, `collection_ids: string[]` | `target` is required for verification; optional `historical_cutoff`. Research objective identity is set by `target`, not by this scope. |
| `bundle` | `id`, `version_id`, `source_id`, `scope`, `completeness`, `units`, `inventory`, `bibliography`, `resolutions` | Each unit has `id`, `kind`, `required`, `link`, and optional `reason`/`url`; inventory and bibliography must reflect the original article. The citation graph reads every registry occurrence and, for each acquired original of the version that has a bundle, the bibliography occurrences of that original's latest bundle, unless the version's last `bundle` import named an earlier bundle of that original. An acquired original is an available main original of the version, so an original that a bundle of another of the version's bodies links as a `supplement` unit is another body. Only a version none of whose acquired originals has a bundle reads, by the same rule, the bundles of its other bodies, such as the saved response of a passage or such a supplement. A bundle of another body, such as a passage on a saved tool response or a bundle whose body is such a supplement, is therefore not read once a bundle of an acquired original exists, whichever was imported first, while a passage bundle whose body is an acquired original is read as that original's bundle. A new bundle for the same body replaces the occurrences of the earlier one, so their obligations end with it. A verification target reads the bundle of each acquired original whatever its pin. `resolutions` account for earlier occurrences that the graph still reads, such as registry observations. |
| `read` | `id`, `version_id`, `depth`, `inspections`, `notes` | Fulltext reading also supplies `bundle_id`; each inspection has `unit_id`, `link`, `note`. All seven note fields shown in the example are required. |
| `budget` | `profile`, `purpose`, `limits`, `reason` | Purposes are `literature`, `screening`, `experiment`, `development`. `limits` names every unit (`network_requests`, `source_bytes`, `readings`, `screenings`, `model_input_tokens`, `model_output_tokens`, `wall_seconds`, `rounds`); null means unlimited. A `development` budget bounds the number of rounds with the `rounds` unit; `round-admit` charges one round. Any unit of a `development` budget at its limit refuses the next `continue` with `resource_budget_exhausted`, so leave its other units null. A later budget for the same key needs a reason and cannot drop a limit below the charged amount (`resource_budget_below_charged`). Acquisition admission needs room for `max_requests` (at least one request) beside the reservations of every acquisition that is still admitted, and finish charges the attempts and bytes actually used; the reserved amount is the allowance of the admitted operations, so an interrupted acquisition holds its allowance until a new request id for the same operation and target supersedes it. `read-batch` and `screen-batch` charge their counts and reported usage, counting null usage as unknown. New work over a limit is refused with `resource_budget_exhausted`, and usage already spent is recorded. Three kinds of charge fail instead and change nothing. A charge that makes an account total an integer of more than 4300 digits fails with `invalid_input`, because the store refuses that integer. A float `wall_seconds` charge to an integer total beyond the float range (about 1.8e308) fails with `invalid_input`. A `wall_seconds` charge whose sum with the total is a float beyond the float range fails with `invalid_input`. With a `wall_seconds` limit, it fails with `resource_budget_exhausted` instead. A JSON number with a decimal point or an exponent is a float. Charged plus reserved at or above a limit is a preparation obligation, except for `development`, whose exhausted limit is a `gate round` obligation; `status --summary` shows every account. |
| `read-batch` | `id`, `depth`, `items` | Optional `usage`, `depth_values`. Use `depth: "abstract"` with `depth_values` for per-call amounts; the object form of `depth` remains supported. Records 1 to 100 abstract readings in one event, all or none. Each item is `{version_id, note, notes: {seven fields: {text, status}}}` with optional `screening: {relevance, reason, conventions}`, `audit: {relevance, reason}`, `consequential: bool`, `placement: {position, reason}` where position is `above`, `below` or `unplaced`, `loop: {purposes, disposition, source}` where source is `search`, `population`, `citing` or `author`, `innovation_candidate: true`, and `search_hit: true`; the harness derives the whole-abstract span inspection itself. The recorded policy decides which extras apply: `loop` and `innovation_candidate` under `lineage-v1`, and `placement` and `search_hit` under `sampled-v1`. A research call with explicit depth values or a selected depth plan can also record sample placements and search-hit readings; other incompatible extras fail with `invalid_batch`. The catalog example carries `loop` on its first item and `placement` on its second so that both shapes are visible; use the applicable policy or explicit research-tool depth, and the `README.json` of a `batches` export states the shape for that export's mode. A `placement` judges a member of the current sample (`invalid_batch` otherwise). A batch that would exceed its effective study loop limit (`loop_limit_reached`, default 100), effective development-round loop limit (`round_loop_limit_reached`, default 40), or search-hit limit (`search_reading_limit_reached`, fixed at 20 for verification) is rejected whole; the 20 abstracts of a further loop round are a rule the coordinator follows, not one the store refuses. Reading ids are `reading:<sha256 of [batch id, version]>`. The reading covers the version's selected complete abstract; a version without one fails with `abstract_missing`. An `audit` belongs to a currently excluded member (`invalid_batch` otherwise) and is stored with the member's screening `round` at the time of reading. A failing item rejects the batch with `invalid_batch` and the failing `items` (`index`, `code`, `message`). `usage` is `{model, input_tokens, output_tokens, wall_seconds}`, each null when unknown. The token counts are nonnegative integers. `wall_seconds` is a nonnegative number with a finite float value, so an integer beyond the float range (about 1.8e308) fails with `invalid_batch`. |
| `screen-batch` | `id`, `items`, `screener` | `round` (default 1), `usage` with the shape and rules of `read-batch`. Under `screened-v1` only (`policy_inapplicable` otherwise). Records 1 to 200 screenings, all or none; a failing item rejects the batch with `invalid_screening` and the failing `items`. A re-screen needs a round above the member's current round and replaces the member's screening. Each item is `{collection_id (null for a Tier 3 family), work_id, version_id, disposition, relevance, reason, conventions}` with `promotion_reasons` for `promote` and `context` for a reference. Rules: `strong` relevance requires `promote`; `exclude` requires `none` relevance and a complete abstract; `weak` stays `pending`, `doctrine`, or `promote`. `screener` is `{kind: agent|human, model}`. A screen is never a reading. |
| `screening-checkpoint` | `id`, `batch_ids`, `reason` | Under `screened-v1` only. Accepted when the two named batches are the two most recent `read-batch` records, both contain only `pending` members, and every item of both was judged `consequential: false`; otherwise `invalid_screening`. While it covers, unread `pending` members carry no obligation and are counted as `inventoried_unread`; a later consequential batch, a higher screening round, or a policy change removes the effect. |
| `sample` | `id`, `collection_id`, `size`, `seed` | Optional `depth`. Available under `sampled-v1`, or in research with an explicit `depth` object or selected depth plan (`policy_inapplicable` otherwise). Draws and records a sample of one frozen population, stratified by month with equal allocation, a short month's shortfall passed to the next months, and the whole population when it fits within `size` (1 to the effective `sample_size`; verification keeps 1 to 100). The enumeration must be complete (`collection_pending`). The record keeps the seed, the per-month population and sampled counts, the population digest and the drawn members; the draw repeats from that seed, and two verifications of one paper choose their own seeds and read different members. A second draw against the unchanged population is refused with `sample_exists`; a population that changed after the draw reports `sample_stale`, which a new draw clears. Under the verification preset every sampled member owes an abstract reading (`sample_reading_missing`) carrying a placement (`placement_missing`), and a scoped collection that is not the sampled one reports `sample_missing`. Research records the draw as tool evidence, with readings at its stated depth. |
| `loop-close` | `id`, `purposes` | Optional `depth`. Under `lineage-v1` only (`policy_inapplicable` otherwise). Closes the bounded loop. `purposes` names all five search purposes exactly once, each `{status, note}` with status `covered` or `gap`. A `covered` purpose needs a loop reading judged `relevant` or `contradictory`, or two distinct captured queries for it with no such hit; a `gap` needs two distinct queries tried, or the loop at the call's effective `loop_abstracts` limit (default 100). Each captured query counts once. A native capture counts by the query it asks: its provider, endpoint and the parameters that decide which works match, as the registries' API references define them (`search_query` and `id_list` for arXiv; `query`, every field query `query.<field>` and `filter` for Crossref; `search`, `search.exact`, `search.semantic`, `filter` and `corpus` for OpenAlex). Its pages, its repeated captures and captures that differ only in another parameter (an order such as `sort`, `order`, `sortBy` or `sortOrder`, `select`, `sample`, `seed`, `facet`, `group_by`, paging, `mailto`, `api_key`, or any parameter outside these lists) therefore count once, whichever of its query parameters a judgment binds (for example OpenAlex `search` and `filter`). So do captures whose URLs spell one query differently: the endpoint is compared by its host and its path without a trailing slash, an empty parameter or list item counts as absent, and the comma-separated items of a `filter` or an arXiv `id_list` are compared as a set, because the registries combine them in any order. An OpenAlex `filter` is compared as OpenAlex reads it: the letter case of its values does not count, and the `\|` alternatives of one filter, after a leading `!` that negates them all, are compared as a set, so `type:Article` asks the same query as `type:article`, and `cites:W2\|W1` as `cites:W1\|W2`. Two native captures whose matching parameters differ in any other way count apart, even when they share a query value; this includes the name of a filter in another letter case, another letter case of the value of a `.search` filter such as `title.search` (OpenAlex reads its Boolean operators only in uppercase), and another letter case of the value of any other matching parameter, such as `search`. A `web` or `mcp` capture counts by its saved response and its bound query: the queries bound to one response count once, and so do the responses bound to one query. Such a capture adds no query when a native capture of the purpose was bound to the same value, because the harness cannot tell it from another capture of that query. The record keeps the queries each purpose tried and a digest of the loop it closed: a later loop reading or search reports `loop_closure_stale`, and no closure at all reports `loop_closure_missing`. |
| `search` | `id`, `profile`, `purpose`, `queries`, `responses`, `captured_at`, `found_work_ids`, `verdict`, `cited_work_ids`, `impact`, `gaps`, `dispositions` | Optional `resolved`, `scope`, `question`, `binding`, `depth`, `hits_per_query`. Without `scope`, the question supplies its text. A provisional search before roots requires `question`; see the search coverage section. Bind the original query and every result. A response's `query` is the value of a query parameter of its captured URL (`search_query` for arXiv; `query`, `query.bibliographic` or `query.author` for Crossref; `search` or `filter` for OpenAlex, so a citing-works capture binds its whole filter value, such as `cites:W123` or, when the filter also holds a date clause, `cites:W123,from_publication_date:2025-01-01`; `q`, `query`, `search` or `search_query` for `web` and `mcp`), or, for a `web` or `mcp` capture, a saved response value named by `query_locator`. `nothing-new` needs an actual captured search, including an actual empty result array when appropriate. `dispositions` judges every found work once: `{work_id, disposition, reason}` with `relevant`, `contradictory`, `potentially_relevant`, `out_of_scope`, `duplicate`, or `unresolved`; cited works are relevant or contradictory. Each cited work becomes a critical full-text requirement (purpose `novelty`), so it is owed a full reading, while its judgment is the selected search of the profile and purpose; a new search for the purpose retires the requirements of the works it does not cite again (the stored requirement records stay). A new search for a purpose carries forward each `contradictory` or `unresolved` work of the selected search or lists it under `resolved: [{work_id, reason}]`, else `search_findings_dropped`. A judgment is stale when its scope changes (`search_scope_stale`), when a root, required full text, found or cited work changes content (`search_evidence_stale`), or when a family enters or moves in the citation graph (`search_frontier_stale`); a new version of an unrelated reference changes nothing. When a new search for a purpose drops a family that only the judgment it replaced required, each selected judgment of another purpose that was recorded while the family was required reports `search_frontier_stale` and `search_evidence_stale` until it is recorded again: as notices under `lineage-v1` and `sampled-v1`, and as obligations under `exhaustive-v1` and `screened-v1`. The one exception is a judgment that found a version of the family: it reports only `search_frontier_stale` when the citation graph had read no reference of the family's versions when the judgment was recorded, for example because no root or other required full text cited the family then. A reference of the family that the graph read later, while the family was required, does not change this. A judgment recorded before the family was required is stale while the family is required, and the drop ends that staleness. Searches recorded before dispositions existed report `search_dispositions_missing`. Under `lineage-v1` and `sampled-v1`, captured hits are bounded by the resolved `hits_per_query`, counted across every response of that query (`invalid_search` above that). Research may use bounded coverage. Verification keeps its ten-hit limit and native search enumeration obligation; see the search coverage section for both profiles. |
| `require-fulltext` | `id`, `profile`, `version_id`, `purpose`, `reason` | Optional `historical_cutoff`, `depends_on`, and `depth` for purpose `core`. Purposes: `major_claim`, `novelty`, `innovation`, `validity`, `exemplar`, `lineage` (the parent's own line of results), `classic` (a foundational paper that line rests on), `core` (a paper whose full text decides a verdict's stance or its prediction band) and `contradiction` (a source that opposes a claim). `lineage`, `classic` and `core` name the claim, lineage entry or finding they serve in `depends_on`. A `core` requirement beyond the effective `core_fulltexts` amount is refused with `core_limit_reached`; verification keeps its fixed limit of ten. Every `lineage` and nondeferred `classic` entry is cited in the pinned bibliography (see "Review, publication, and verification"). An `exemplar` requirement under the `research` profile, recorded while a round is active, is that round's exemplar (see "Development rounds"). |
| `availability` | `id`, `profile`, `version_id`, `depth`, `source_ids`, `reason`, `policy` | Policy has `id`, `minimum_attempts`, `allowed_statuses`, `rationale`. Two policies qualify: captured terminal HTTP 403/404/410/451 failures, or (abstract depth, non-arXiv work) `allowed_statuses` `[200]` with complete captures of the work from every registry that addresses its identifiers (Crossref and OpenAlex for a DOI, OpenAlex for an OpenAlex id, the saved web/MCP import for a `url:` work), none carrying an abstract. Critical sources remain required. |
| `defer-source` | `id`, `profile`, `version_id`, `reason`, `authorization`, `acquisition_evidence`, `dependent_claims`, `continuing_work` | Research only. Names one registered exact version with a current missing full-text requirement. `authorization` is a checked, nonempty artifact reference; `acquisition_evidence` is a nonempty array of checked, nonempty artifact references. The last two fields are nonempty string arrays recording claims that cannot use the source and work that can continue. Roots and verification targets cannot be deferred. |
| `resume-source` | `id`, `profile`, `version_id`, `deferral_id`, `reason` | Restores ordinary obligations for the selected active or stale research deferral. Names its exact `deferral_id`; all earlier decisions and receipts remain immutable. |
| `select-cohort-abstract` | `collection_id`, `work_id`, `unresolved_assertion_id`, `selected_assertion_id`, `reason` | Resolves a retained versionless family member to same-family exact abstract evidence. It cannot replace a known v1 obligation with v2. |
| `visual` | `link`, `url` | Optional `max_requests`; acquires an asset referenced by the saved source. |
| `standards` | `id`, `profile`, `scope`, `field`, `article_type`, `venue`, `cohort_doctrine`, `methodology`, `reporting`, `citation`, `presentation`, `applicability_questions` | `article_type` and `venue` may be null; evidence claims retain scientific status, timing, assumptions, and uncertainties. |
| `rationale` | `id`, `profile`, `scope`, `and`, `but`, `therefore`, `value` | Research only. The established context in `and` must be supported as scoped; a proposed or refuted context is retained but pending. |
| `innovation` | `id`, `profile`, `scope`, `cases` | Research only. Optional `origin_collection: {id, version}` and `depth`. The depth-plan section gives the current and historical case bounds. Cases separately describe original constraint, conceptual change, validation, adoption, and tested transfer limits. |
| `context` | `id`, `profile`, `scope`, `current`, `historical`, `beneficiaries`, `capabilities`, `barriers`, `uncertainties`, `speculative_links` | Research only. Basic science does not require an invented immediate application. |
| `grand-challenge` | `id`, `reason`, `challenges` | Research only (`configuration_missing` without a research configuration, `profile_inapplicable` in a verification workspace). Records the challenges ahead of the ai-science study as part of the direction minimum: each challenge is `{id, horizon: ultimate|near_term, statement, state, criteria: [{id, statement}], evidence}`, at least one is `ultimate`, and criterion ids are unique across the record (`invalid_grand_challenge`). Evidence is a source read in full or an execution result, validated as in `round` (`invalid_development` for a malformed item, `reading_missing` for a source without a full reading). The managed direction minimum owes `grand_challenge_missing` until the first record exists. A user-specified mathematical objective follows its own gates. The latest record is current for the whole study; a new record with a reason replaces it. It is outside the preparation digest, so recording it never makes a pinned bundle stale. `status` reports the current record. |
| `cycle` | `id`, `author`, `objective`, `scope`, `hypothesis`, `question`, `strategy`, `predecessor`, `inheritance`, `reopening`, `distinguishing_test`, `expected_outcomes`, `failure_signals`, `evidence_requirements`, `literature`, `resource_limits` | The full nested example includes a first cycle. Successors retain checkpoint lineage and account limits. |
| `admit` | `id`, `cycle_id`, `plan_digest`, `command`, `reserved_units` | Command has `argv`, `program`, `inputs`, `versions`, `seed`, `seed_reason`. This reserves resources; it does not launch. |
| `bind-run` | `admission_id`, `script`, `backend`, `timeout_seconds`, `inputs`, `outputs`, `usage_unit` | Backend is `local` or `colab`; usage is planned `execution` or `wall_seconds`. Inputs map each admitted artifact to a relative path. |
| `reconcile-run` | `admission_id` | Optional `resolution`, `reason`: `interrupted` for a confirmed dead local owner, or `not_released` for a Colab claim with no durable release. Unknown released remote work remains pending. Some runs that an earlier release claimed or admitted fail with `invalid_input` (see "Actual execution"). Their metric or seed exceeds the store bounds in a new record or result. |
| `result` | `id`, `cycle_id`, `origin`, `command`, `status`, `exit_code`, `usage`, `outputs`, `notes` | Public JSON permits imported history only. Its exact `origin.original`, `reason`, and `deduction` fields are shown in the example. Managed outcomes come from run/reconcile. |
| `assess` | `id`, `cycle_id`, `author`, `scope`, `execution_ids`, `result`, `validity_checks`, `outcomes`, `failures`, `findings`, `assumptions`, `remaining_obligations`, `objective_status`, `disposition`, `development` | Full nested example includes outcome judgments, distinct validity checks, and development/branch assessments. Optional `inheritance_refresh` explicitly binds current reassessments of the same ancestors without changing the prospective plan. A validity check with `status: passed` cites a completed output of a planned `validation` requirement; otherwise `assess` records nothing and fails with `validity_evidence_missing`, whose `check_ids` list every such check. A `failed` or `unresolved` check is recorded with the `validity_unresolved` obligation. |
| `checkpoint` | `id`, `cycle_id`, `assessment_id`, `reason`, `next_hypothesis`, `select_for_readiness` | An unassessed unresolved checkpoint may have null assessment; it supplies no validated-result credit. |
| `review` | `id`, `candidate_digest`, `assessor`, `verdict`, `checks`, `limitations` | Checks separately cover `validity`, `scope`, `novelty`, `contribution`, `development`, `branches` and their exact evidence. |
| `manuscript` | `id`, `files`, `claim_evidence` (optional `citation_accounting`) | Refused with `contribution_analysis_missing` while the selected bundle has a complete measurement and no `contribution-analysis`. Files contains `pdf`, `abstract`, `bibliography`, `claims`, `sources`; `sources` may be null. Each claim mapping has `claim_id`, `evidence`. `claims` is a nonempty JSON array of objects with nonblank text `id` and `claim` and unique ids (`publication_claims_missing` for a non-array, an empty array, a non-object entry or a repeated id; `invalid_input` for a missing or blank `id` or `claim`). A claim carries at most one marker, `revised: {previous, reason}` or `superseded: {reason}`, with exactly those fields as nonblank text (`publication_claims_missing` otherwise). Optional `citation_accounting` covers every work with a recorded full-text reading and every work that a selected research search of the five purposes cites. A BibTeX entry cites such a work when it carries the work's arXiv family or DOI, or a DOI of a provider alias, as a whole identifier. An entry also cites the work when its title field equals the work's title of two or more words. Titles compare after casefolding, removing accents, naming Greek letters and dropping LaTeX commands, braces, punctuation and spaces. Another bibliography format cites the work when its text carries an identifier or contains the title; there, a shorter title inside a longer one also counts. The lineage gate reads the same evidence. Each work that no entry cites needs one item: `{work_id, cited_as}` with a BibTeX key of the pinned bibliography, or `{work_id, reason}` with nonblank text. `citation_accounting_incomplete` lists each work without an item and its reasons (`fulltext`, `search:<purpose>`). A wrong item is `invalid_citation_accounting`, with its `index` and `cause`: `fields`, `already_cited`, `unknown_work`, `duplicate`, `cited_as_and_reason`, `neither` or `unknown_key`. The bundle keeps `citation_accounting: {cited: [{work_id, key, basis}], not_cited: [{work_id, reason}]}`. `basis` is `bibliography` or `declared`. `key` is the first citing entry in file order, or null for a bibliography that is not BibTeX. `manuscript` fails with `invalid_input` and changes nothing when the bundle it builds holds a value beyond the store bounds (see "Inputs and receipts"). The bundle holds the metric of each cited run five levels deep. A run that exactory-client 0.47.0 or earlier claimed has no metric bound, so `manuscript` fails when such a run's metric nests more than 95 levels. The bundle also holds the admissions of every cycle, so `manuscript` fails for a seed of more than 4300 digits. An earlier release stored such a seed only under an interpreter without a limit on integer string conversion (see "Actual execution"). |
| `manuscript-review` | `id`, `bundle_digest`, `assessor`, `review: ArtifactRef`, `blind: true`, `assignment_id` | The referenced original rubric JSON is unchanged and carries `changes_for_maximum` (`skills/evaluate/RUBRIC.md`); historical JSON is retained, but a context without observed independent provenance cannot authorize current publication. Latest applicable reviews from two distinct independent assessors must accept. |
| `bind-verdict` | `id`, `task_digest`, `body: ArtifactRef`, `assessment` | Assessment has `assessor`, `provenance`, `independence_basis`, `blind: true`, and separate `soundness`, `novelty`, `impact` checks with full-read links. The verdict body that `body` names nests at most 32 levels, as a payload file does (see "Inputs and receipts"). A deeper body fails with `invalid_input`, and the message names the verdict body. A body with an integer of more than 4300 digits fails the same way, unless the interpreter cannot parse it (`invalid_json`). An earlier release bound deeper bodies. `exactory verify` sends a bound body of 98 levels. The store then refuses to record the response, because the remote intent holds the body 3 levels deeper. The intent stays in flight, so `exactory reconcile` fails with `verdict_reconciliation_pending`. A later `exactory verify` then sends the verdict directly, with a `Managed record skipped` line. A bound body of 99 levels or more fails with `invalid_input` before the request. |
| `manuscript-prediction` | `id`, `bundle_digest`, `blind: true`, `assessor`, `prediction`, `reasons`, `assignment_id` | `prediction` is `{corpus, category, windowStart, windowEnd, percentile, band: {best, worst}}`; the four cohort fields equal the study's frozen collection definition (`prediction_cohort_mismatch` otherwise). `percentile` and both band ends are "top X%" of the cohort, where 1 is the strongest position, with `best <= percentile <= worst` as integers from 1 to 100. One prediction per assessor per exact bundle (`manuscript_prediction_duplicate`); the assessor is not a cycle author. A bundle takes three predicting assessors; a fourth is refused (`manuscript_prediction_excess`), so a complete measurement stays complete. Recorded and summarized as a result: `round` and `contribution-analysis` need the complete measurement, and no gate rule reads the predicted values. |
| `contribution-analysis` | `id`, `bundle_digest`, `investigation`, `position`, `reviewer_changes`, `steps` | `bundle_digest` names the selected bundle (`contribution_analysis_stale`), which needs a complete measurement (`manuscript_measurement_missing`); one analysis per bundle (`contribution_analysis_duplicate`); a current Grand Challenge record exists (`grand_challenge_missing`). `investigation` is a nonempty list of `{query, response: ArtifactRef, finding}`: the original response of each query, saved with `artifact`, and what it shows about the challenges ahead. For managed research, unchanged held searches may be reused through an explicit `refresh` declaration addressing the named unresolved question and meaningful source change; legacy records retain their original query accounting. The assessor receives the original responses. A malformed `response` fails as any artifact reference does (`invalid_input`, `unsafe_path`), and one whose bytes are not stored with `artifact_missing`. `reviewer_changes` dispose of every applicable request once (all three axes in managed research, with stable work-item IDs), `{review_id, change, disposition: adopted|rejected, step_id (adopted only), reason}` (`reviewer_change_missing`). `position` is `{criterion_ids, established, remaining, evidence}`. Steps are `{id, statement, criterion_ids, direction, reach: this_round|next_round, builds_on (current claim ids, `contribution_claim_unknown` otherwise), community: {who, capability, evidence}, risks, evidence}`. Evidence is validated as in `round` (`invalid_development`, `reading_missing`, `round_evidence_mismatch`); any other malformed field fails with `invalid_contribution_analysis`. The record keeps the id and digest of the Grand Challenge record its criterion ids were checked against. |
| `round` | `id`, `closes`, `decision`, `bundle_digest`, `candidates`, `carried`, `next`, `reason` | Needs the bundle's complete measurement and its `contribution-analysis` (`manuscript_measurement_missing`, `contribution_analysis_missing`); the candidates list every step of that analysis (`contribution_step_missing`), and a `continue` goal names `criterion_ids` of the current Grand Challenge record. `decision` is `continue` (exactly one `pursue` candidate and `next` present) or `stop` (at least one candidate, no `pursue` candidate or carried development, and `next: null`). Candidates are `{id, direction: vertical|horizontal, statement, disposition: pursue|rejected|deferred, reason, evidence}`; evidence takes the `source` and `result` shapes or `{kind: "review", review_id}` naming a manuscript review of this bundle; a `source` item needs a full reading that inspects that link (`reading_missing`). `carried` disposes of every `next_round` development the closing round's cycle assessments recorded: `{assessment_id, kind: alternative|branch, question or cycle_id, disposition, reason}`. `next` is `{number, objective, objective_lineage, goal, resource_limits, reopening}`; the goal has `direction`, `criterion_ids`, `field_change`, `statement`, `contribution_delta`, `beneficiaries`, `success_criteria` (`kind` `claim` or `scope`), `stop_conditions`, `continuity`, `route`, `risks`, `evidence`. See "Development rounds". |
| `round-review` | `id`, `round_id`, `round_digest`, `assessor`, `verdict`, `checks`, `limitations` | `verdict` is `approved`, `not_approved`, or `unresolved`. For a `continue` decision the checks are `impact`, `demand`, `novelty_risk`, `feasibility`, `distinctness`, `continuity`, `grand_challenge`; for `stop` they are `stop` and `demand`; each is addressed once with `status` (`passed`, `failed`, `unresolved`), a reason and evidence. One review per assessor per decision (`round_review_duplicate`); the assessor is not a cycle author. |
| `round-admit` | `id`, `round_id`, `review_id`, `reason` | Opens the round of an approved `continue` decision on the current bundle. Records the round's number, goal, objective, limits and opening state; applies a widened objective through its lineage. While the round runs, its receipt is the only output that reports the admitted goal (see "Development rounds"). |
| `round-assess` | `id`, `round_id`, `bundle_digest`, `criteria`, `stop_conditions`, `summary` | Judges every success criterion and stop condition of the admitted goal once with `status` (`observed`, `not_observed`, `unresolved`), an explanation and evidence, on the exact current bundle. The harness derives and stores the round's progress with the record. |

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
`innovation_candidates`. Budgets still bound cost. Verification keeps its fixed
contract and refuses plans or explicit overrides with `profile_inapplicable`.
Research depth needs no particular constitution version or revalidation.

```json
{"id":"depth-1","values":{"loop_abstracts":150,"hits_per_query":15},"reason":"Broaden the next comparison."}
```

```sh
exactory-research depth --file depth.json --expected-revision REV --request-id depth-1
```

The plan records all eight effective defaults and its revision. Search, sample,
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
status includes `limits.depth: {plan_id, values}`; old stores keep their report
shape. Summary plan IDs are truncated to 64 characters. Invalid keys, types,
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
readiness check. The intent payload is:

```text
{id, question, interpreter, program, inputs, timeout_seconds}
```

Question and interpreter are nonempty text; program is a pinned artifact
descriptor and inputs is an array of them. `timeout_seconds` is finite and
positive. A different payload under an existing ID is refused. Record one with
`exactory-research probe-intent --file intent.json --expected-revision REV
--request-id probe-intent:ID`.

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
`artifact_missing`, `artifact_corrupt`, `unsafe_path`, and normal immutable/revision/
request conflicts. Status adds `probes: {count, elapsed_seconds}` when probe
history exists. Probes alone never form a result candidate.

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

`bind-run.script`, input paths, and ordinary output paths are relative to `experiment/`. Outputs `stdout` and `stderr` select the captured streams. Each declared output has `{id, requirement_id, path, media_type}`; output IDs and paths must be unique, and a result cannot overwrite an input. An assessment cites `result` and `validation` evidence through a locator into the output's text or JSON, so `bind-run` refuses an output bound to either kind unless its media type is `text/*`, `application/json` or a `+json` type; bind figures and other binary files to a `log` requirement. This rule applies to a new binding only. `bind-run` with the payload of a binding that an earlier release recorded returns that binding. The admitted argv begins with a Python executable and the exact declared script. Local execution pins the resolved interpreter file, its bytes and the declared Python version in the binding's `runtime`. When `argv[0]` is a symbolic link, as a venv's `bin/python3` is, `runtime.path` keeps `argv[0]` and `runtime.resolved_path` names the file. The worker then starts that link, so the program keeps the venv's packages. A link that resolves to another file when the launcher claims the run stops the launch with `execution_runtime_changed`. If the interpreter changes after the claim, the worker refuses to start the program, and the launch returns `execution_recovery_required`. Record that run with `reconcile-run`, giving `resolution: "interrupted"` and a reason. For a regular file, `runtime.path` is its resolved path, as in earlier releases. A binding that exactory-client 0.47.0 or earlier recorded for a link keeps the resolved file as `runtime.path`. Its launch starts that file, without the venv's packages, and `bind-run` with the same payload returns that binding. `bind-run` and `exactory-lab run` must run under a Python that resolves to the same file as `argv[0]`; for a venv, start them as `VENV/bin/python3 "$(command -v exactory-research)" bind-run ...` and `VENV/bin/python3 "$(command -v exactory-lab)" run ...`. Other imported command histories are preserved without claiming this launcher observed them.

```sh
exactory-lab run code/program.py --admission run-1 --backend local --timeout 30 \
  --expected-revision 42 --request-id actual-run-001
```

Backend, timeout, and seed must match the binding and admission. A null seed needs its declared reason; a seed is passed as `EXACTORY_LAB_SEED`. The run's metric comes from the first of three sources that gives one. A line or file gives a metric only when its JSON is finite and within two bounds. It nests at most 32 levels, the same depth as a command input (see "Inputs and receipts"). It holds no integer of more than 4300 digits, so that every supported Python reads the metric back from the store. The first source is the last stdout line that gives a metric and has a `metric` key. The second is the conventional fallback file `results/SCRIPT_STEM.json` inside the private working directory. The third is the first declared `application/json` or `+json` output of a `validation` requirement, which the run configuration records as `metric_output`. The process runs in a private copy of the pinned script and inputs. The launcher claims the admission once and rechecks current preparation before releasing its token. Before publishing its terminal outcome, the worker seals the presence or absence, SHA-256, and size of every declared output, both streams, and the fallback metric file. Reconciliation rejects changed, removed, or newly inserted bytes and saves the complete sealed inventory as immutable artifacts. Current author readiness checks the saved result, log, and metric against that original seal. The familiar result/log paths are disposable projections.

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

A retry returns/reconciles that run; it does not execute the same admission twice. Missing or lost results require `reconcile-run`. A dead local worker may be recorded interrupted after ownership checks. Unknown usage retains its full reservation. An interrupted process, timeout, missing output, or old modeled managed record cannot become current author readiness through a public result JSON label. An imported observation retains its actual provenance and chronological limits and does not consume or satisfy a prospective admission.

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
exactory-research export --kind readiness --destination /new/independent/candidate
exactory-research export --kind manuscript --destination /new/independent/manuscript
```

Delivery copies the actual candidate/source/program/output bytes and an `inputs.json` manifest into a new directory. The manifest is a neutral packet: no revision labels, request identities, launcher tokens, author names, assessment history, prior scores or verdicts. A readiness packet carries the candidate, branches, plans, assessments, checkpoints, sources and synthesis sections that the six checks need.

A manuscript packet carries the exact PDF, abstract, bibliography and optional source archive. It derives a separate claims file for the reviewer. This file contains current claims, with `revised` metadata and `superseded` entries omitted. The evidence map and its source/result closure contain only those current claims. The packet also carries the selected field standards. The derived claims file has its own content hash and exists only in the delivery directory. The original ledger and bundle digest remain unchanged. Give this directory to the separate reviewer. A digest list alone is insufficient. Export does not certify comprehension or independence.

`manuscript-review` accepts one review per assessor per exact bundle; a second review by the same assessor on the same `bundle_digest` fails with `manuscript_review_duplicate`, so a rejection stands until the manuscript changes and a new bundle is prepared.

The original manuscript rubric JSON has exactly `summary`, `strengths`, `weaknesses`, `soundness`, `presentation`, `contribution`, `overall`, `decision` and `changes_for_maximum`; the three dimension scores use 1 through 4, overall uses 1 through 10, decision is `accept` or `reject`, and `changes_for_maximum` has exactly the keys `soundness`, `presentation` and `contribution`, each a list that is empty exactly when that score is 4 (`invalid_review` otherwise). A review recorded before 0.42.0 has the first eight fields and keeps its credit. `manuscript-review` adds the external assessor/bundle envelope without changing those original bytes. Changed PDF, abstract, bibliography, claims, optional source archive, current evidence, or review dependencies invalidate the current publication gate.

Under `lineage-v1` the manuscript gate also checks the pinned bibliography for every `lineage` and `classic` entry. It reads the same citation evidence as `citation_accounting`. A BibTeX entry cites the work when it carries the work's arXiv identifier in family form, its DOI or a DOI of a provider alias, as a whole identifier. A bibliography that cites one version cites the work. An entry also cites the work when its title field equals the work's title of two or more words; a one-word title is too common to stand as evidence. Another bibliography format cites the work when its text carries an identifier or contains the title. A work with no such evidence keeps `lineage_citation_missing` until the bibliography carries it. Since 0.43.0, a DOI that only starts another DOI is not evidence, and in a BibTeX bibliography a title inside a longer title is not evidence. The bibliography is read for citation evidence, not validated: undecodable bytes become replacement characters instead of a failure.

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

### Deposit for a math problem

`exactory-draft deposit --math-problem <problem-id>` deposits a write-up for a problem.
The command checks that the problem stands and the account has a linked ORCID iD before the first Zenodo write.
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
`inspected_count`. Every relevant or contradictory result needs fulltext coverage
from the pinned common `reading` records. Unresolved or potentially relevant hits,
unavailable required source units and active or stale deferrals remain gaps.
`already_solved` binds its fully read settling result via `settling_work_id`;
`supported_open` uses null. All search/reading hashes must be in both the finding's
`evidence` and the observed packet. Export the declaration in the optional
`openness` field of `search review-packet --spec FILE --json`; the reviewer echoes
it as `findings.openness.minimum`. The exporter delivers records and captured
source bytes from the associated common workspace. Other material searches for
the same formulations must be included on fresh resolved review. The exact
schemas and `openness_*` refusal codes are in the math-solver `SEARCH.md`.
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
describes the field and question explored for the bar reviewers. When
configuration is absent, initialization adopts the constitution and sets a null
target. Study, draft and research-intent markers conflict with this marker
(`orchestration_conflict`) in either direction, and so does a research target
already recorded in the configuration.

| Claim | Mechanical minimum | Independent assessment | Currency |
| --- | --- | --- | --- |
| Problem: precisely stated, open, worth solving | Exact statement and criteria, current supported openness, evidence-bound value and projected citations | Two observed bar reviews and two observed result reviews | The explicit `problem_evidence_digest` binds the selected claim and decisive evidence. |

Every mutation below uses `--file FILE --expected-revision REVISION
--request-id ID`. Read the revision before a new mutation; an interrupted retry
retains its original identity and payload. These operations append immutable
records and move explicit selections.

| Command | JSON payload | Records and behavior |
| --- | --- | --- |
| `problem-init` | `{slug, area, field, authors}` | Creates the orchestration marker and initial configuration. |
| `problem-statement` | `{id, title, area, statement, resolution_criteria, neighbours, formal_statement}` | `problem_statement/<id>` and `problem_selection/current = {statement_id}`. Statement and criteria are nonempty TeX; neighbours are formulation texts, possibly empty. Formal statement is optional, null or `{language, text}`. Any changed field requires a new id and moves the selection. |
| `problem-openness` | `{assignment_id}` | Records `problem_openness/<id>` from the observed native result review. `supported_open` advances the claim; `unresolved` records the gap without a minimum audit; `already_solved` redirects to the settling result and blocks posting as open. |
| `problem-value` | `{id, statement_id, motivation, current_state}` | `problem_value/<id>` contains ordered paragraph arrays, each item `{text, evidence}` with nonempty evidence. |
| `problem-formal-check` | `{evidence: {execution_id, output_id}, native_closure_digest}` | `problem_formal_check/<id>` binds a requested support check to an authenticated native completion in this store. Invalid bindings return `problem_formal_check_unbound`. |
| `problem-decision` | `{id, action, review_ids, bar_review_ids, reason}` | `problem_decision/<id>` binds the current evidence digest. Action is `post`, `revise` or `withdraw`; `post` requires the minimum and observed review coverage, otherwise `problem_minimum_unmet`. The other actions record the reason. |
| `problem-posted` | `{problem_id, export_directory}` | Rebuilds the approved export and compares every file byte for byte before recording `problem_post/<id>`. A changed file, even with a changed manifest hash, returns `problem_export_mismatch`. |

The statement digest hashes `{statement, resolution_criteria}`. Declared
neighbours also bind openness currency: adding a neighbour requires searches for
that formulation even when the statement digest is unchanged. The openness
minimum uses this store's own searches, readings and deferrals. It requires a
dated search with bounded or complete coverage for the exact statement and each
declared neighbour, and full readings of all decisive prior or settling works.
An unavailable source remains a gap. Shared `openness_*` audit codes describe
missing, incomplete, stale or undelivered evidence;
`openness_statement_mismatch` identifies a finding for another exact claim.

Prepare the native openness packet with:

```sh
exactory-research export --kind problem-openness --spec openness-spec.json --destination openness-packet
```

The spec is `{assignment_id, reviewer_id, minimum: {searches:
[{formulation, search_id}], settling_work_id, duplicate_resolutions}}`. The
export pins the current statement, statement digest, native reviewer assignment
identity, search records and the captured bytes of required full readings. Use
the exported packet in the `context` of a `review-assignment` payload with that
assignment id, reviewer id, `role: "native_math"`, `dossier_id: null`, a verified
route and an author from the marker, then invoke `review-run`. Record the
observed result through `problem-openness`. A new unbound material search,
changed bound reading/search or missing current formulation makes the finding
stale (`problem_openness_stale`).

Value evidence is `{kind: "reading", version_id, reading_id}` for a full reading,
or `{kind: "computation", execution_id, output_id, formal_check}`. Abstract and
passage readings, availability qualifications and deferrals supply no value
evidence. A cited computation requires its admission, reconciled output and an
assessment that lists and validates that result, with its execution accounting
and resource obligations satisfied. Whole-objective obligations and obligations
about other results do not invalidate a supported paragraph. Probe-origin
outputs retain `exploratory_evidence` and cannot be promoted by citation. An
uncited computation remains a probe.

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

The problem bar uses `exactory-problem-review-v1/bar` and receives `area`,
`field` and source inventory/deliveries with the statement, title, neighbours and
value withheld. Two observed bars are required before a result packet. The
result template `exactory-problem-review-v1/result` receives `problem_evidence`
with the claim, openness, value, citations, delivered readings/passages,
admissions/executions/assessments/output validations and bound native closures.
Both result reviews must pass exactly `problem_statement`, `problem_openness`,
`problem_value`, `problem_citations` and `stop`. A marker author cannot be a
reviewer (`review_not_independent`); an assignment's unknown author is refused
with `problem_author_unknown`. Existing route probes, source-request
continuations, assignment currency and independence checks apply.

`gate post` is the only applicable gate in a problem workspace. Every other
gate reports `gate_inapplicable`, as does `post` outside this orchestration.
`exactory-lab state set` has no study to update. Budgets, scopes and roots,
searches, readings, deferrals, availability, source requests, depth plans,
probes and review tools are available without a target, intent, dossier,
synthesis or direction decision. The `cycle`, `admit`, `bind-run`,
`reconcile-run` and `assess` route uses `{id: statement_id, statement}` as its
objective and requires the current statement and adopted constitution. Its
records retain nonmanaged dependency composition and empirical meaning.

| Post obligation codes | Required action |
| --- | --- |
| `problem_statement_missing` | Select the exact statement and resolution criteria. |
| `problem_openness_missing`, `problem_openness_unresolved` | Obtain an observed supported openness finding with the audited minimum. |
| `problem_already_solved` | Redirect to the named established result or record a new statement. |
| `problem_openness_stale` | Refresh the finding for the changed formulation, search or reading. |
| `problem_value_missing`, `problem_value_reading_missing`, `problem_value_computation_missing` | Supply ordered value paragraphs with valid full readings or admitted, reconciled, assessed computation support. |
| `exploratory_evidence` | Preserve the probe observation and obtain admissible support for the claim. |
| `problem_formal_check_missing`, `problem_formal_check_stale` | Bind the requested check for the current value evidence to its authenticated native completion. |
| `problem_citation_locator_missing` | Record a DOI, arXiv id or read-version bundle HTTPS locator for the cited work. |
| `problem_bar_pending`, `review_bar_mismatch` | Obtain both current field/area bars. |
| `problem_review_pair_required`, `problem_evidence_unassessed`, `problem_assurance_pending` | Obtain the independent observed pair with delivered current evidence and all five assurances passed. |
| `problem_decision_missing`, `problem_decision_stale` | Record a `post` decision covering the current evidence digest. |
| `review_sources_pending` | Resolve the reviewers' pending source requests. |
| `problem_already_posted` | Retain the existing confirmed post; the same statement cannot be posted twice. |

`status` and `next` compute milestones from records: `exploration` at
initialization, `formulation` with a statement, `openness` with current
`supported_open`, `value` with valid value evidence, both bars, both passed
result reviews and a current post decision, and `post` with a recorded post.
`next` names the first missing condition in the order statement, openness,
value, reviews, decision, export. Milestones impose no tool order. `status`
lists the current depth plan under `limits` and no cohort policy counts; those
belong to ai-science.

After `gate post` reports ready, run:

```sh
exactory-research export --kind problem --destination problem-export
```

An unready export returns `readiness_required`. The deterministic projection is
`title.txt`, `statement.md`, `motivation.md`, `state.md`, `criteria.md`,
`citations.json`, optional `formal-statement.txt`, and `problem-export.json`.
The manifest contains `{statement_id, statement_digest,
problem_evidence_digest, decision_id, area, formal_statement_language,
files: {name: sha256}}`. Citations are projected once per distinct work cited
by the decisive openness/settling readings and value readings, as
`{citation, locator}` using metadata citation and DOI, then arXiv id, then the
read-version bundle's HTTPS URL. Every cited work has a reading in this store.

The math-conjecturer delivery section checks the exported citation locators
and duplicates, runs `exactory post-problem` on the exported files, then records
the returned id through `problem-posted`. The post record contains
`{id, problem_id, statement_id, statement_digest, problem_evidence_digest,
export_digest, posted_at}`. Its export digest hashes rebuilt approved bytes;
the directory's manifest is informational. A changed part requires a new
statement and a fresh post gate. The choice of skill that creates the research
record remains open.

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

## Status, projections, and recovery

`status` and `next` expose the current revision, study, preparation, actionable obligations, pending admissions, remote intents/observations, retained adoptions, the `round` (number, active, assessed, decision, whether the selected bundle has its contribution analysis, the count of round gate obligations, progress counts, the measurement's medians and spreads, the admitted round's limits, the `development` budget line and the usage since admission), and the `runtime` that produced the report (plugin version, source commit, dirty flag, package digest, constitution digest). `status` also carries `grand_challenge`, the payload of the current Grand Challenge record; `status --summary` reduces it to the record id, the number of `ultimate` and `near_term` challenges, the first six criterion ids and a count of the rest, each id cut at 32 characters. `next` in managed research is the current milestone's first missing condition:
at `initiate`, `cohort` and `literature` it follows `direction`, and at `ideate`
it follows `execution`. The preparation report stays under `preparation`
with `advisory: true` (`preparation.advisory`); its backlog is available for
useful tool work. Before a cycle decision exists, `direction` includes the
direction minimum obligations as well as the missing decision. Other
stores retain preparation ordering and the next unread cohort abstract. At the `evaluate` stage the status obligations also carry the publication gate's and the round gate's obligations, publication first, so the study finishes the manuscript measurement before the round decision.

`status` also carries `limits`, the counts of the recorded policy against its own bounds: `{policy, loop, sample, innovation_candidates, search_readings, core_papers}`, with the selected depth plan under `depth` when present. Under `lineage-v1`, `loop` is `{readings, limit, covered}`, the registered loop readings, the current loop limit (default 100) and the purposes already covered, and `innovation_candidates` is `{families, required}` with the current required count. Under `sampled-v1`, `sample` is `{n, placed, unplaced, percentile, band, widen_required}` once a sample is drawn and null before that, `search_readings` is `{readings, limit}` against 20, and `core_papers` is `{requirements, limit}` against 10. A field that the recorded policy does not bound stays null. The verifier reads the percentile and the band it files from `limits.sample`.

The optional `--summary` flag on `status` and `next` returns a bounded advisory view. It runs the same current-state evaluation as the default command. It does not change research obligations, authorize a transition, or reduce required reading. `status --summary` is limited to 16 KiB and `next --summary` to 4 KiB of UTF-8 JSON. Counts distinguish displayed and omitted obligations. Hints may omit or truncate details and must not be used directly as mutation payloads. `status --summary` also reports `evaluation` counters (objects verified, readings assessed, graph builds, elapsed seconds); they describe this invocation and are never stored. Full reports retain the exact current data; summaries remain advisory views.

`batches --depth abstract --size N --destination DIR [--profile PROFILE] [--screen]` writes the current unread abstracts (cohort members first, then Tier 3 references) as `batch-NNN.json` files with each version's selected complete abstract text, the exact link, and a `README.json` naming the notes shape that `read-batch` accepts. `--profile` defaults to the configured profile. It reads the store and writes only under the new destination directory (`review_destination_exists` when it exists; `migration_required` without a configuration); a reader agent works from one file and returns one notes file, and a single coordinator submits it with `read-batch`. With `--screen` it writes `screen-NNN.json` files of the unscreened members for `screen-batch`, under `screened-v1` only (`policy_inapplicable` otherwise). The `README.json` names the shape of the export's own mode: the screening shape with `--screen`, the loop shape with `--loop`, the placement shape under `sampled-v1`, and the plain notes shape otherwise.

`batches --loop [--candidates FILE]` writes the loop's unread abstracts instead, under `lineage-v1` only (`policy_inapplicable` otherwise): the found works of the selected searches that carry no loop reading yet, followed by the caller's candidates. `--candidates` reads a JSON file that is either a `population-query` result or a plain list of version ids (`invalid_input` for anything else), and it needs `--loop` (`invalid_input` without it). The exported `README.json` carries the loop item shape, `{loop: {purposes, disposition, source}, innovation_candidate}`, and the note that `innovation_candidate` marks one of the current required innovation candidates and stays out of every other item. Under `lineage-v1` a plain export is refused with `policy_inapplicable`, because this policy asks for no reading of the enumerated population: its abstracts come out with `--loop`.

`population-query --terms TERM [TERM ...] [--limit N]` is read-only. It ranks the enumerated population's stored abstracts by how many of the terms appear in the title or the saved abstract, each term matched as a whole word or the start of one and ignoring case, and returns `{terms, population, matches, read_only}` with each match as `{version_id, work_id, title, matched_terms, score}`, highest score first. `--limit` is positive and at most the current `loop_abstracts` amount (default 100), and defaults to 30. The result is the local source of the lineage loop: pass it straight to `batches --loop --candidates`.

Under `screened-v1` the cohort gate requires a screening for every member (`screening_missing`), an abstract reading for every `promote`, `doctrine` and (until saturation) `pending` member, an audit reading with `audit: {relevance, reason}` for a deterministic sample of at most 150 excluded members per collection (`screening_audit_reading_missing`; any accepted reading of the version can carry the audit; a `strong` audit judgment is `screening_audit_failed` until the paper is promoted and every excluded member is re-screened in a round above the audited one), and at least 8 read `doctrine` or `promote` members per calendar month of the window, or every member of a smaller month (`doctrine_coverage_missing`). Tier 3 references need a screening keyed by family with `context`; excluded references are sampled the same way. `require-fulltext` families and unresolved references keep their obligations whatever their screening says. `batches --screen` exports the unscreened members for `screen-batch`.

Under `sampled-v1` the cohort gate requires a current sample of the scoped collection (`sample_missing`, and `sample_stale` once the population changed under it), an abstract reading of every sampled member (`sample_reading_missing`), and a placement on each of those readings (`placement_missing`). No other member owes a reading. Under `lineage-v1` no cohort member owes a reading at all, and a pending acquisition is reported as a notice rather than an obligation, so the gate passes while the enumeration is still incomplete. Both policies drop the Tier 3 obligation on the references of a full-read paper; the bibliography is still inventoried for identity.

`policy-report [--policy POLICY] [--reference FILE]` is read-only. It reports each collection's members, readings, unscreened count, dispositions, audit sample and selected preparation set under the recorded or a hypothetical policy, whether a saturation checkpoint covers, and, with a reference file `{"prior_art": [...], "contradictions": [...], "methods": [...], "doctrine": [...]}` of family ids, the recall of the selected set per category with the missed families listed. `--policy` takes any of the four policies. For `lineage-v1` and `sampled-v1` the report is `{revision, policy, recorded_policy, limits, mechanical_only}`, because a bounded policy prepares a loop or a sample rather than a selection out of the population; `limits` holds the same counts as `status.limits`, read from the recorded workspace, so naming a hypothetical bounded policy does not recompute the counts under it.

`obligations --code CODE [--limit N] [--cursor CURSOR]` returns one page of the current obligations that carry `CODE`, ordered by exact version. The current obligations are the ones `next` follows: the managed milestone's gate, or the existing cohort/readiness ordering in other stores. A page carries `total`, `offset`, `returned`, and `next_cursor`. A cursor is `REVISION:OFFSET`; a cursor whose revision differs from the current store fails with `stale_cursor`, so a listing never mixes revisions. On resume, read `status --summary`, then `next --summary`, then the `obligations` page for the code you are working on; run the default detailed command only when the full nested report is needed.

`gate ACTION` supports `cohort`, `foundation`, `preparation`, `direction`, `readiness`, `manuscript-readiness`, `execution`, `verification`, `manuscript`, `publication`, `deposited`, `submitted`, and `round` and exits nonzero for unmet obligations.

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
