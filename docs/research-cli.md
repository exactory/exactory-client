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
| Source evidence | `{ "kind": "source", "link": Link }`. Consequential synthesis and development evidence requires the applicable full reading. |
| Assessor | `{ "id": string, "kind": "human" or "agent", "provenance": ArtifactRef, "relationship": string, "independence_basis": string }`. Identity declarations and provenance are retained, not independently authenticated. |

Use `artifact` to pin authored program files, original review JSON, assessor provenance, or other local bytes and obtain an `ArtifactRef`. It accepts `{id, path, media_type}` and explicitly returns `scientific_validation: false`. It does not import a publication source or turn a local note into a reading. Acquire publication sources through `acquire`, `fulltext`, or `import-response`.

## Preparation order

1. Initialize the author workspace with `exactory-lab init --slug STUDY`, or initialize only the common store with `init` and `{ "profile": "research", "target": null }`. A draft initialized by `exactory-draft init` also receives a pending current contract. Use a separate verification workspace for an external paper.
2. Enter `cohort`, collect its frozen population, and record actual complete abstract readings. `cohort -> literature` checks enumeration and the selected cohort's abstract reading coverage. It does not require literature work that is still being prepared.
3. In `literature`, acquire one to five exact root paper families, expand captured citation references, inventory source bundles, read the required units, and record all five search purposes: `direct`, `originals`, `theory`, `adjacent`, and `recent`. Whole-cohort abstract coverage remains required. An incomplete bibliography or unread critical source remains pending.
4. Set the complete objective with `target`, then record `standards`, `rationale`, `innovation`, and `context`. These commands are available while preparing literature. `literature -> ideate` requires the applicable complete synthesis; it does not require entering ideate before setting the objective. Once fixed, the complete objective cannot be replaced with a special case.
5. Record a prospective `cycle`, pin the program and inputs, `admit` it, then `bind-run` with its exact backend and output contract. `ideate -> experiment` requires a current admitted cycle. The launch checks its dependencies again before releasing a process.
6. Observe the run, assess result and validity evidence separately, preserve a `checkpoint`, and deliver its candidate to an independent reviewer. Record `review`, then evaluate current whole `readiness`. An individual passed assessment or historical review receipt is insufficient.
7. Prepare the exact `manuscript`, deliver its actual bytes to two independent blind assessors, and record their unchanged review JSON through `manuscript-review`. Deposit and submission require the current resulting bundle and receipts.

`exactory-lab state set` validates all requested fields together before changing any state. The stages are `initiate`, `cohort`, `literature`, `ideate`, `experiment`, `write`, `evaluate`, `deposit`, `submit`, and `complete`. Forward edges are adjacent only; both source completion and target prerequisites apply. Any proposed `done` status also checks the target stage's completion requirement, including a status inherited from the previous stage. To enter unfinished work after a completed stage, include `--status pending`. Waiting, paused, or operational status changes do not certify readiness.

Explicit returns are available from `ideate` through `submit` to `literature`, from `experiment` to `ideate` after an assessment, and from `evaluate` to `experiment` or `write`. A return cannot also assert `done`. All histories, observations, objective identity, and original resource accounts remain retained.

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
| `init` | `profile`, `target` | Profiles are `research` and `verification`. Acquire exact metadata before initializing a verification target. |
| `adopt` | `id`, `profile`, `target`, `files: string[]`, `reason` | Archive selected legacy bytes; no implicit completed readings or new execution credit. |
| `target` | `target`, `reason` | Verification repinning is explicit and requires matching subsequent roots. |
| `constitution` | `previous_sha256`, `reason` | Adopts the current distributed policy; dependent decisions need explicit reassessment. |
| `artifact` | `id`, `path`, `media_type` | The local path must remain inside the workspace without traversing symlinks. |
| `collect` | `definition: {corpus, primaryCategory, windowStart, windowEnd}` | `max_requests`, `page_size`; current corpus is `arxiv`. Dates are ISO calendar dates. |
| `resume` | `collection_id` | `max_requests`; resumes retained collection/page evidence. |
| `acquire`, `expand` | `identifier` | `provider`, `max_requests`; expansion acquires an unresolved identifier without asserting its bibliography has been read. |
| `fulltext` | `identifier`, `url` | `max_requests`; preserves the exact original and extraction result. |
| `import-response` | `provider`, `response_file`, `source_url`, `captured_at` | `media_type`, `mappings`; `web`/`mcp` mappings use JSON pointers into the original saved response for every work. |
| `roots` | `profile`, `roots: string[]`, `collection_ids: string[]` | `target` is required for verification; optional `historical_cutoff`. Research objective identity is set by `target`, not by this scope. |
| `bundle` | `id`, `version_id`, `source_id`, `scope`, `completeness`, `units`, `inventory`, `bibliography`, `resolutions` | Each unit has `id`, `kind`, `required`, `link`, and optional `reason`/`url`; inventory and bibliography must reflect the original article. |
| `read` | `id`, `version_id`, `depth`, `inspections`, `notes` | Fulltext reading also supplies `bundle_id`; each inspection has `unit_id`, `link`, `note`. All seven note fields shown in the example are required. |
| `search` | `id`, `profile`, `purpose`, `queries`, `responses`, `captured_at`, `scope`, `found_work_ids`, `verdict`, `cited_work_ids`, `impact`, `gaps` | Bind the original query and every result. `nothing-new` needs an actual captured search, including an actual empty result array when appropriate. |
| `require-fulltext` | `id`, `profile`, `version_id`, `purpose`, `reason` | Optional `historical_cutoff`. Purposes: `major_claim`, `novelty`, `innovation`, `validity`. |
| `availability` | `id`, `profile`, `version_id`, `depth`, `source_ids`, `reason`, `policy` | Policy has `id`, `minimum_attempts`, `allowed_statuses`, `rationale`. Two policies qualify: captured terminal HTTP 403/404/410/451 failures, or (abstract depth, non-arXiv work) `allowed_statuses` `[200]` with complete captures of the work from every registry that addresses its identifiers (Crossref and OpenAlex for a DOI, OpenAlex for an OpenAlex id, the saved web/MCP import for a `url:` work), none carrying an abstract. Critical sources remain required. |
| `select-cohort-abstract` | `collection_id`, `work_id`, `unresolved_assertion_id`, `selected_assertion_id`, `reason` | Resolves a retained versionless family member to same-family exact abstract evidence. It cannot replace a known v1 obligation with v2. |
| `visual` | `link`, `url` | Optional `max_requests`; acquires an asset referenced by the saved source. |
| `standards` | `id`, `profile`, `scope`, `field`, `article_type`, `venue`, `cohort_doctrine`, `methodology`, `reporting`, `citation`, `presentation`, `applicability_questions` | `article_type` and `venue` may be null; evidence claims retain scientific status, timing, assumptions, and uncertainties. |
| `rationale` | `id`, `profile`, `scope`, `and`, `but`, `therefore`, `value` | Research only. The established context in `and` must be supported as scoped; a proposed or refuted context is retained but pending. |
| `innovation` | `id`, `profile`, `scope`, `cases` | Research only. Optional `origin_collection: {id, version}`. Cases separately describe original constraint, conceptual change, validation, adoption, and tested transfer limits. |
| `context` | `id`, `profile`, `scope`, `current`, `historical`, `beneficiaries`, `capabilities`, `barriers`, `uncertainties`, `speculative_links` | Research only. Basic science does not require an invented immediate application. |
| `cycle` | `id`, `author`, `objective`, `scope`, `hypothesis`, `question`, `strategy`, `predecessor`, `inheritance`, `reopening`, `distinguishing_test`, `expected_outcomes`, `failure_signals`, `evidence_requirements`, `literature`, `resource_limits` | The full nested example includes a first cycle. Successors retain checkpoint lineage and account limits. |
| `admit` | `id`, `cycle_id`, `plan_digest`, `command`, `reserved_units` | Command has `argv`, `program`, `inputs`, `versions`, `seed`, `seed_reason`. This reserves resources; it does not launch. |
| `bind-run` | `admission_id`, `script`, `backend`, `timeout_seconds`, `inputs`, `outputs`, `usage_unit` | Backend is `local` or `colab`; usage is planned `execution` or `wall_seconds`. Inputs map each admitted artifact to a relative path. |
| `reconcile-run` | `admission_id` | Optional `resolution`, `reason`: `interrupted` for a confirmed dead local owner, or `not_released` for a Colab claim with no durable release. Unknown released remote work remains pending. |
| `result` | `id`, `cycle_id`, `origin`, `command`, `status`, `exit_code`, `usage`, `outputs`, `notes` | Public JSON permits imported history only. Its exact `origin.original`, `reason`, and `deduction` fields are shown in the example. Managed outcomes come from run/reconcile. |
| `assess` | `id`, `cycle_id`, `author`, `scope`, `execution_ids`, `result`, `validity_checks`, `outcomes`, `failures`, `findings`, `assumptions`, `remaining_obligations`, `objective_status`, `disposition`, `development` | Full nested example includes outcome judgments, distinct validity checks, and development/branch assessments. |
| `checkpoint` | `id`, `cycle_id`, `assessment_id`, `reason`, `next_hypothesis`, `select_for_readiness` | An unassessed unresolved checkpoint may have null assessment; it supplies no validated-result credit. |
| `review` | `id`, `candidate_digest`, `assessor`, `verdict`, `checks`, `limitations` | Checks separately cover `validity`, `scope`, `novelty`, `contribution`, `development`, `branches` and their exact evidence. |
| `manuscript` | `id`, `files`, `claim_evidence` | Files contains `pdf`, `abstract`, `bibliography`, `claims`, `sources`; `sources` may be null. Each claim mapping has `claim_id`, `evidence`. |
| `manuscript-review` | `id`, `bundle_digest`, `assessor`, `review: ArtifactRef`, `blind: true` | The referenced original rubric JSON is unchanged. Latest applicable reviews from two distinct independent assessors must accept. |
| `bind-verdict` | `id`, `task_digest`, `body: ArtifactRef`, `assessment` | Assessment has `assessor`, `provenance`, `independence_basis`, `blind: true`, and separate `soundness`, `novelty`, `impact` checks with full-read links. |

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
| `development.alternatives[].disposition` | `pursue`: a useful development remains. `not_useful`: the stated question is not useful for this candidate, with a reason and evidence. `resolved`: the question is substantively resolved, with evidence. `budget_paused`: useful development remains and is paused for its budget. |
| `development.branches[].disposition` | The same four values as alternatives: `pursue`, `not_useful`, `resolved`, `budget_paused`. Each entry names an actual `cycle_id` and retains its reason and evidence. At readiness, a `resolved` branch requires its own current assessment with `validated_result: true`. |

The top-level `disposition` and the two nested disposition fields use different
vocabularies. A retained `pursue` or `budget_paused` alternative or branch leaves
an unmet development obligation. Neither `not_useful` nor `resolved` is a way to
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

Delivery copies the actual candidate/source/program/output bytes and an `inputs.json` manifest into a new directory. It includes the observed managed execution chain when applicable and excludes existing assessor scores and verdicts. Give these bytes to the separate reviewer; a digest list alone is insufficient. Export does not certify comprehension or independence.

The original manuscript rubric JSON has exactly `summary`, `strengths`, `weaknesses`, `soundness`, `presentation`, `contribution`, `overall`, and `decision`; the three dimension scores use 1 through 4, overall uses 1 through 10, and decision is `accept` or `reject`. `manuscript-review` adds the external assessor/bundle envelope without changing those original bytes. Changed PDF, abstract, bibliography, claims, optional source archive, current evidence, or review dependencies invalidate the current publication gate.

Existing `exactory-draft deposit` options remain available, including sandbox, production, source archive, publication confirmation, and new version. All paths must identify the exact current reviewed bundle. Production also retains the existing citation/date checks. The command saves a remote intent before external writes and uploads those validated bytes. Use `exactory-draft reconcile INTENT_ID` after an unknown outcome. It reconciles known records/files/metadata/publication through remote reads before continuing. An uncertain new-version creation with no recoverable draft ID remains pending instead of repeating the create operation. Local and remote storage are not an atomic transaction.

Managed author `exactory submit DOI` binds the current published production record. Zenodo's concrete record DOI remains distinct from the concept DOI that the server may return. A task for another concrete record fails the association; a request awaiting ingestion remains pending. Equivalent concrete DOI and record URL spellings reuse one submission intent for the same publication receipt, while preserving their exact local request receipts and the original outgoing body. `exactory reconcile INTENT_ID` can read the later task without repeating the original POST. Creating an external verification request outside a managed author workspace remains available before doing a verifier's research.

For verification, acquire exact metadata and original body, initialize the `verification` profile, set matching roots/target, complete its required literature and standards, then run:

```sh
exactory task IDENTIFIER --bind --expected-revision 42 --request-id bind-task-001
exactory-research bind-verdict --file verdict-assessment.json \
  --expected-revision 43 --request-id assess-verdict-001
exactory verify IDENTIFIER --file original-verdict.json \
  --expected-revision 44 --request-id send-verdict-001
```

UUID, DOI, and arXiv routes all fetch the task-only target. The server supplies source/version identity but no original-body SHA, so the exact body is bound to the separately acquired local source pin and full reading. The verdict's stance, rationale, and cohort impact prediction remain separate. `bind-verdict` references the exact original outgoing JSON, including an explicit `supersedesVerdictId` when revising. Wrong task version, body, profile, or current preparation fails before a write. No other verdict content is requested by this flow.

The task endpoint may reveal only an own-verdict ID after a lost POST response; it cannot establish the exact body that was accepted. Such an intent remains pending even when that ID exists, and a second POST for the uncertain submission is refused. A verification-level owner serializes the complete verdict decision and write, including distinct assessment IDs. Other verification targets retain independent owners. Known-success receipts and explicitly bound revisions remain supported. A stale task response cannot erase a locally confirmed prior verdict or permit a revision that omits the latest known predecessor. The server's `requestedByViewer` means the request opener, not the paper author, and supplies no authorship proof.

## Native math preparation

New native proposals use schema version 3 with the existing computation field and a `foundation` reference. Export current complete preparation before independent proposal review:

```sh
exactory-research export --workspace /study --kind native --attack-root /study/attack \
  --destination /study/attack/research/native-inputs/review-001
```

The manifest returns `foundation` and native `inputs`. Add both to the proposal's existing reviewed delivery. The reference contains `schema_version`, `workspace`, `profile`, `revision`, `snapshot_digest`, `preparation_digest`, and `claim_binding`. Its workspace is `.` or an ancestor expressed with `..`; the delivery remains inside the attack root. The immutable native store imports the snapshot blob and actual source artifacts. A later unrelated common revision does not invalidate unchanged preparation, but changed required sources, policy, scope, or synthesis require a new reviewed amendment before fresh work.

Common author research preparation supports native research and an author's native verification of the same full original objective. Common external verification preparation supports only native `role: "verification"`. The latter additionally exports `--claim-binding binding.json` with this exact reviewed correspondence:

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

`status` and `next` expose the current revision, study, preparation, actionable obligations, pending admissions, remote intents/observations, retained adoptions, and the `runtime` that produced the report (plugin version, source commit, dirty flag, package digest, constitution digest). `next` is the highest-priority current obligation in preparation order (configuration, collection, cohort abstracts, objective and roots, critical full text, bundles and units, searches, Tier 3 abstracts, synthesis); at the cohort stage it is the next unread cohort abstract.

The optional `--summary` flag on `status` and `next` returns a bounded advisory view. It runs the same current-state evaluation as the default command. It does not change research obligations, authorize a transition, or reduce required reading. `status --summary` is limited to 16 KiB and `next --summary` to 4 KiB of UTF-8 JSON. Counts distinguish displayed and omitted obligations. Hints may omit or truncate details and must not be used directly as mutation payloads. `status --summary` also reports `evaluation` counters (objects verified, readings assessed, graph builds, elapsed seconds); they describe this invocation and are never stored. The default `status` and `next` responses are unchanged.

`obligations --code CODE [--limit N] [--cursor CURSOR]` returns one page of the obligations that carry `CODE`, ordered by exact version. A page carries `total`, `offset`, `returned`, and `next_cursor`. A cursor is `REVISION:OFFSET`; a cursor whose revision differs from the current store fails with `stale_cursor`, so a listing never mixes revisions. On resume, read `status --summary`, then `next --summary`, then the `obligations` page for the code you are working on; run the default detailed command only when the full nested report is needed.

`gate ACTION` supports `cohort`, `foundation`, `preparation`, `readiness`, `execution`, `verification`, `manuscript`, `publication`, `deposited`, and `submitted` and exits nonzero for unmet obligations.

SQLite is authoritative. Workspace JSON, decision logs, and familiar result views are projections. Editing them cannot authorize work. `export --kind workspace` rebuilds workspace projections from current history; run reconciliation repairs execution projections. `exactory-lab decide` retains immutable decisions with revision/request identity and preserves any old decision-log bytes. Explicit `adopt` archives selected legacy paths and exposes pending obligations without rewriting their old scientific claims.

A genuine hot SQLite rollback journal makes default reads return `store_recovery_required`. Use `exactory-research recover`, optionally with `--expected-revision`, to request native SQLite rollback recovery after path checks. This preserves committed history, migrates no schema, resets no accounts, and satisfies no research gate.
