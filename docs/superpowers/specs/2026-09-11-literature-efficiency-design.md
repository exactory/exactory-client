# Literature efficiency design

Date: 2026-09-11

Status: Approved by the user on 2026-09-11 for full implementation (Release A, B, and C) with `exhaustive-v1` as the default preparation policy. The user also approved: constitution Version 2, priority ordering for `next`, required search dispositions, unit-level reading freshness, refusal of a same-assessor re-review on an identical bundle, editing the listed skill files, and leaving the mapped reading view out of scope.

Target version: 0.38.0

Inputs: `misc/harness-improvement/v2/` in the exactory repository (background, audit, proposal, validation protocol, compact-report plan) and the read-only map of the plugin at commit `c46dd2b` on branch `feat/literature-efficiency`.

This supplements the [research foundation design](2026-09-07-research-foundation-design.md). It replaces that design's Tier 2 definition (all direct references) with the committed selected-Tier-2 rule of commit `59df1c4`, which the user chose on 2026-09-09 in the closed-gravity study decision log and confirmed on 2026-09-11.

## Objective

Direct expensive reading and reasoning at consequential questions while keeping exact-source traceability, independent review, innovation study, and prospective planning unchanged. Remove repeated data handling first, then narrow invalidation to the dependency that changed, then offer a tested, versioned screened preparation policy that a study selects explicitly.

Measured baseline on the closed-gravity study (revision 3,584, 2,505 cohort families, 2,610 readings): `exactory-research status` takes 78 s wall, 2.35 GB RSS, and emits 87.6 MB of JSON; two full history replays cost 19 s; the readiness gate 44.6 s; the cohort gate is computed and emitted twice. One 18.24 MB extraction is stored about 18 times inside SQLite. One abstract `read` costs a median 8.6 s and grows with history.

## Global constraints

- Python 3.9+ standard library. `pdftotext` stays optional.
- The SQLite schema stays at `schema_version` 1. Events and receipts stay append-only. History is never rewritten or compacted.
- Every read command stays read-only: no event, no projection repair, no journal recovery.
- Full store validation still runs at least once per process before any evidence is used, and inside every write transaction.
- Legacy records (text locators with a full quote, readings without unit digests, searches without dispositions, configurations without a policy) remain readable and keep their scientific credit under the rules stated below.
- No cache outlives one process. Reuse is scoped to one verified snapshot inside one CLI invocation.
- Compact responses, budget counters, screening records, and runtime provenance never grant scientific authority. Gates keep deciding from current evidence.
- Unavailable, partial, timed-out, stale, failed, and budget-exhausted work stays pending. Exhaustion and saturation produce checkpoints, never readiness.
- Three fresh blind manuscript reviews per measurement and two accepting reviews before publication stay as they are.
- Five to ten external original paper families read in full and at least one within-field innovation case stay required for a research foundation.
- All artifacts, code, and documentation are English. Research runs stay under `/Users/ryshiro/exactory/exactory-research` and are not committed.
- Both host manifests carry the same version. `codex/generate.py --check` passes.

## 1. Components

| Module | Responsibility |
| --- | --- |
| `research_harness/provenance.py` (new) | Runtime identity: plugin version, source commit, dirty flag, executable path, package digest, constitution contract |
| `research_harness/evaluation.py` (new) | One verified snapshot evaluated once: verified artifact bytes, reading index, memoized graph, cohort, foundation, synthesis, counters |
| `research_harness/report_views.py` (new) | Bounded advisory views for `status --summary`, `next --summary`, and `obligations` pages |
| `research_harness/storage.py` | Retain the validated snapshot from construction; reuse it when revision and last event digest are unchanged |
| `research_harness/source_links.py` | `span` locator kind; shared identity for `text` and `span`; span-aware containment and coverage |
| `research_harness/reading.py` | Unit digest, bibliography digest, `read-batch`, derived whole-abstract inspections |
| `research_harness/batches.py` (new) | Read-only export of reading and screening batches for coordinator-driven agents |
| `research_harness/fulltext.py`, `acquisition.py` | Extraction diagnostics and options; entry-level collection completeness; retained exclusions; deadline-aware acquisition |
| `research_harness/literature.py`, `synthesis.py`, `development.py` | Dispositions; frontier and content digests; section-level dependencies; preparation digest |
| `research_harness/screening.py` (new) | Screening records, audit sample, doctrine coverage, saturation checkpoint, policy report |
| `research_harness/resources.py` (new) | Resource budgets and accounts |
| `research_harness/review_packets.py` (new) | Neutral readiness and manuscript packets |
| `research_harness/cli.py` | New commands and flags |
| `RESEARCH_CONSTITUTION.md` | Version 2: preparation policy principles |

## 2. Runtime provenance (P0)

`runtime_provenance()` returns:

```json
{"plugin_version": "0.38.0", "source_commit": "hex or null", "dirty": true,
 "executable": "/abs/path/bin/exactory-research", "package_digest": "sha256",
 "constitution": {"version": "2", "sha256": "hex"}, "schema_version": 1}
```

`source_commit` and `dirty` come from `git` when the plugin root is a Git checkout and `git` is on PATH; otherwise both are null. `package_digest` is the SHA-256 over the sorted relative paths and bytes of `research_harness/*.py`, `bin/*`, and `RESEARCH_CONSTITUTION.md`.

`status` and `next` include `runtime`. Every prepared mutation stores `runtime` in its `literature_operation/<request_id>` record; every acquisition operation stores it in `acquisition_operation/<request_id>`. Payloads stay unchanged, so replay comparison is unchanged. `HttpClient` sends `User-Agent: exactory-research/<plugin version>`.

## 3. Bounded reports (P1)

`status --summary` emits at most 16 KiB and `next --summary` at most 4 KiB of UTF-8 JSON, measured on the CLI serializer output (indent 2). Both run the same current evaluation as the default commands. The default output is unchanged.

`status --summary` contains: `schema`, `runtime`, `revision`, `profile`, `study` (stage, status, waiting), `ready`, `preparation_ready`, `counts`, `obligations` (total, by code with counts, shown, omitted), `next_hint`, `resources` (per account: unit, limit, charged, reserved, unknown), and `evaluation` (events replayed, artifacts verified, bytes verified, readings assessed, graph builds, elapsed seconds). Strings are truncated to fixed limits and truncation is marked.

`next --summary` contains `schema`, `revision`, `ready`, `preparation_ready`, `next_hint` (code, version_id, work_id, collection_id, unit_id, explanation, paths), and `details` naming the `obligations` command.

`obligations --code CODE [--limit N] [--cursor C]` returns one page of obligations for that code, bound to the revision. A cursor carries the revision and offset; a cursor whose revision differs from the current one fails with `stale_cursor`. Each page reports `total`, `offset`, `returned`, and `next_cursor`.

`next` is chosen by priority, then by `version_id`, then by digest. Priority follows the preparation order: configuration and constitution, collection pending, cohort abstract, target and roots, critical full text, bundles and required units, searches, Tier 3 abstracts, synthesis sections, development and readiness, publication.

## 4. Evaluate once per invocation (P2)

### 4.1 Store

`Store.__init__` validates the complete history as today and retains the resulting snapshot together with the revision, the event count, and the last event digest. `Store.snapshot()` opens a read transaction, reads those three values, and returns a deep copy of the retained snapshot when all three are unchanged; otherwise it validates the complete history again and replaces the retained snapshot. `Store.mutate()` validates the complete history inside `BEGIN IMMEDIATE` exactly as today.

### 4.2 Evaluation

`Evaluation(snapshot, artifact_store)` exposes `records`, `revision`, `artifacts`, and memoized `graph(profile)`, `cohort(profile, collection_ids)`, `foundation(profile)`, `configuration(profile)`, `synthesis(profile)`, `readings_for(version_id)`, `assessed(reading_id)`, `link(link)`, and `counters`.

`evaluation.artifacts` verifies each object once per evaluation by `(sha256, size)`, then serves the verified bytes and the decoded text. A byte change on disk is detected by the first read of that object in every process, which is the same guarantee as today.

`gate_state(evaluation, action, profile)`, `foundation_state(evaluation, profile)`, `cohort_report(evaluation, collection_ids, target)`, `synthesis_state(evaluation, profile)`, `readiness_state(evaluation)`, `author_readiness_state(evaluation)`, `publication_state(evaluation, action)`, `configuration_state(evaluation, profile)`, `current_readings(evaluation, version_id, target)`, and `development._Context` take the evaluation. Leaf validators keep `(records, artifacts)` and receive `evaluation.records` and `evaluation.artifacts`.

`status_report` builds one evaluation, computes the readiness or verification report, and reuses the memoized cohort report as `preparation` instead of computing it again. `prepared_mutation` builds one evaluation for `prepare()`, so a batch shares it across items.

Acceptance: on an identical valid snapshot, the previous and the new evaluators return the same `ready`, the same normalized obligation set, the same digests, and the same selected evidence. Changed artifact bytes still fail. Corrupt stores still fail before any gate.

## 5. Reference-based reading evidence (P3)

### 5.1 Span locator

```json
{"kind": "span", "start": 0, "end": 1234, "sha256": "hex", "excerpt": "optional, at most 200 characters"}
```

`start` and `end` are Unicode code-point offsets into the strictly UTF-8 decoded artifact. `sha256` is the SHA-256 of the UTF-8 encoding of `content[start:end]` without normalization. The validator reads the artifact, checks the artifact hash, decodes, hashes the span, and returns the substring. `excerpt` is display only.

`text` locators stay valid forever. `contains` and `covers_text` treat `text` and `span` as one class. `link_identity` maps both to `{kind: "text-span", start, end, sha256}` where a `text` locator's `sha256` is computed from its quote. Search `query_locator` comparison uses the returned substring.

### 5.2 Unit digest and bibliography digest

`unit_digest(bundle)` covers `version_id`, `original_sha256`, `scope`, `completeness`, `includes_abstract`, visual asset dependencies, and the units (id, kind, required, link identity, reason, url). `bibliography_digest(bundle)` covers the bibliography entries and resolutions.

A stored fulltext reading is current when `unit_digest(bundle referenced by the reading)` equals `unit_digest(selected bundle)`. Both sides are recomputed with the current identity function; the reading's stored `assessment.bundle_digest` is retained as history and no longer compared. `reading_bundle_stale` therefore appears only when a unit changes. Bibliography changes produce `bibliography_incomplete` or reference obligations, never a reading obligation.

A new reading that must add a unit repeats its unchanged inspections; inspections are small under `span`, so no inheritance record is needed.

### 5.3 Cohort abstract credit

`cohort_evidence.abstract_reading` compares the artifact slice returned by the locator with the abstract text, so `text` and `span` readings both discharge a cohort abstract.

### 5.4 Extraction diagnostics and options

Each fulltext capture records `extraction`: `{"extractor": "pdftotext"|"html", "version": "string or null", "options": {"layout": bool}, "text_bytes", "page_count", "max_line_length", "whitespace_fraction", "expansion_ratio"}`. The values are computed from the decoded text before it is stored. `fulltext` accepts `extraction_options: {"layout": bool}` (default true, matching today). A capture with different options is a distinct capture of the same original; `fulltext_coverage` accepts a reading whose bundle covers any available capture of that original.

The `batches` export includes the diagnostics so a coordinator sees an anomalous extraction before dispatching a reader.

## 6. Batches and resume (P4)

### 6.1 `read-batch`

```json
{"id": "batch-001", "depth": "abstract",
 "items": [{"version_id": "arxiv:2601.00001v1", "note": "inspection note",
            "notes": {"problem": {"text": "...", "status": "present"}, "...": "..."},
            "screening": {"relevance": "weak", "reason": "...", "conventions": ["..."]},
            "audit": {"relevance": "none", "reason": "..."},
            "consequential": false}],
 "usage": {"model": "string or null", "input_tokens": null, "output_tokens": null, "wall_seconds": null}}
```

Each item is expanded to a complete abstract reading: the harness derives the inspection as the whole abstract artifact of the version with a `span` locator, then validates it with the same rules as `read`. Reading ids are `reading:<sha256 of [batch id, version_id]>`. Items with `screening` also produce a screening record (section 9). At most 100 items per batch. The batch fails as a whole when any item fails; the error `invalid_batch` lists `{index, code, message}` for every failing item. Duplicate `version_id` values within a batch fail. One event, one receipt, N reading records, and one resource charge.

Fulltext readings keep the single `read` operation.

### 6.2 `batches` export

`batches --depth abstract --size N --destination DIR [--screen]` writes `batch-NNN.json` files for the current unread abstract obligations (cohort items first, then Tier 3). Each entry carries `version_id`, `title`, `authors`, `published`, `categories`, `text`, the abstract `link`, and, for fulltext-derived texts, the extraction diagnostics. With `--screen`, entries are the members that lack a screening record under the screened policy. The command reads the store and writes only under the destination.

### 6.3 Collection completeness

A partition completes when the set of exact entry ids seen across its pages equals the reported total and no page is pending. Entries are tracked per exact id, so two versions of one family in one listing no longer trigger `duplicate_or_missing_results`. Entries whose primary category or published date fails validation are retained as `cohort_exclusion` with a reason code; they count as seen and do not restart the partition. `changed_total`, `changed_cursor`, `inconsistent_count`, and `invalid_response` keep restarting the partition. `invalid_alias` is removed from the restart list because the parser already keeps the entry with a warning.

`acquire`, `expand`, and `fulltext` pass the source's persisted `next_eligible_at` as `not_before`.

## 7. Dependency-specific freshness (P5)

### 7.1 Search dispositions

`search` requires `dispositions`: one entry per `found_work_ids` element with `disposition` in `relevant`, `contradictory`, `potentially_relevant`, `out_of_scope`, `duplicate`, `unresolved`, and a `reason`. `cited_work_ids` is a subset of the works whose disposition is `relevant` or `contradictory`. A new search for a purpose that already has a selected search must carry forward every `contradictory` and `unresolved` disposition of the selected search or list it under `resolved: [{work_id, reason}]`; otherwise `search_findings_dropped`. Searches recorded before this release have no dispositions and are reported as `search_dispositions_missing` until re-recorded; their captured responses can be reused.

### 7.2 Digests

For a profile, the `frontier` is the sorted list of `(family_id, tier)` for graph nodes plus the families of fulltext requirements plus the families of found and cited works of selected searches.

- `scope_digest`: unchanged.
- `content_digest(search)`: metadata scalars, known versions, abstract shas, fulltext shas, aliases, and bundle unit digests of the families in roots, fulltext requirements, and the search's found and cited works.
- `frontier_digest`: digest of the frontier.

A search is `search_scope_stale` when the scope digest differs, `search_evidence_stale` when its content digest differs, and `search_frontier_stale` when the frontier digest differs. A new version or capture of a Tier 3 reference that is not found or cited changes none of the three.

### 7.3 Section dependencies

| Section | Binds |
| --- | --- |
| standards | configuration, constitution, scope, evidence digest, cohort population digest (collection definition and member families) |
| rationale | configuration, constitution, scope, evidence digest, frontier digest, selected search judgments digest |
| innovation | as rationale, plus the selected standards digest |
| context | as rationale |

`literature_digest(profile)` is the digest of the scope digest, the frontier digest, the selected search judgments, and the requirement set; a plan's literature comparison and a development's novelty comparison bind it. `preparation_digest(profile)` is the digest of the configuration digest, `literature_digest`, and the four section digests; plans, admissions, assessments, checkpoints, and candidates bind it where they bound the foundation digest. The foundation digest remains computed for the foundation report.

## 8. Review packets (P7)

`review_packets.readiness_packet(evaluation)` returns the current `review_inputs` with every key named `request_id`, `token`, `authors`, or ending in `_revision` removed recursively, and without `synthesis.history`. The six readiness checks keep their evidence: candidate, branches, plans, assessments, checkpoints, sources, synthesis sections, strategy accounts.

`review_packets.manuscript_packet(evaluation, bundle)` returns `kind`, `bundle_digest`, `files`, `claim_evidence`, `evidence` (the works, readings, bundles, and sources reachable from `claim_evidence`), `results` (execution observations for cited result evidence, without `token`), and `standards` (the selected standards section payload). It carries no candidate, plan, assessment, checkpoint, synthesis history, strategy account, author list, or revision label.

`deliver_readiness` and `deliver_manuscript` write these packets as `inputs.json` and copy exactly the artifacts they reference.

`manuscript-review` refuses a second review from the same normalized assessor on the same `bundle_digest` with `manuscript_review_duplicate`. A changed bundle accepts a new review.

## 9. Screened preparation policy (P6)

### 9.1 Policy record

`configuration/research.preparation_policy = {"id": "exhaustive-v1" | "screened-v1"}` is set at initialization (`exactory-lab init --preparation-policy`, default `exhaustive-v1`; `init` payload field `preparation_policy`, optional). A configuration without the field is `exhaustive-v1`. `policy` changes the policy with `{"previous": "id", "policy": "id", "reason": "..."}`; synthesis and plans become stale through the configuration digest as designed.

### 9.2 Screening records

`screening/<sha256 of [collection_id or "graph", family_id]>`:

```json
{"collection_id": "cohort:... or null", "work_id": "family", "version_id": "exact",
 "disposition": "promote|doctrine|sample|exclude|pending",
 "promotion_reasons": ["prior_art", "contradiction", "assumption_or_method", "correction", "standards", "identity_conflict", "unclassifiable"],
 "relevance": "none|weak|strong", "reason": "...", "conventions": ["..."],
 "context": "citation context for a reference, or null",
 "policy": {"id": "screened-v1"}, "screener": {"kind": "agent|human", "model": "string or null"},
 "round": 1}
```

`screen-batch` records up to 200 screenings in one event. `promote` needs at least one promotion reason. `exclude` needs a reason. A member without a complete abstract cannot be `exclude`. A `require-fulltext` family and an unresolved reference keep their obligations whatever their screening says. Screening records are never readings; `reading` records are never screenings.

Dispositions are authored as `promote`, `doctrine`, `exclude`, or `pending`. Relevance constrains disposition: `strong` requires `promote`; `exclude` requires `none`; `weak` is `pending`, `doctrine`, or `promote`. `pending` means the member is read in batches until a saturation checkpoint (9.5).

### 9.3 Cohort gate under `screened-v1`

- Every member version has a screening record, else `screening_missing`.
- Every `promote`, `doctrine`, and `pending` member has an accepted abstract reading, else `cohort_abstract_reading_missing`. A `pending` member is exempt only while a saturation checkpoint covers it (9.5).
- Audit sample: the harness derives a deterministic sample of `exclude` members, seeded by the collection id, of size `min(150, excluded count)`. Each sampled member owes an abstract reading whose batch item carries `audit: {"relevance": "none|weak|strong", "reason": "..."}`; the judgment is stored on the reading's assessment. A sampled member without such a reading produces `screening_audit_reading_missing`. A sampled reading with audit relevance `strong` produces `screening_audit_failed` until a new screening round (higher `round`) re-screens every excluded member and its own sample passes.
- Doctrine coverage: every calendar month of the window has at least 8 members with disposition `doctrine` or `promote` that are read, else `doctrine_coverage_missing`.

The population, its identities, exclusions, and collection completeness are unchanged by the policy.

### 9.4 Tier 3 under `screened-v1`

Tier 3 families need a screening record keyed by family with `context`. `promote`, `doctrine`, and `pending` families owe abstract readings for every captured version. `exclude` families are inventoried; the same audit sample rule applies over excluded Tier 3 families with a separate seed. Unresolved references keep `reference_unresolved`.

### 9.5 Saturation

Each `read-batch` under `screened-v1` may declare `consequential: true|false` per item. `screening-checkpoint` records `{"batch_ids": [two ids], "reason": "..."}` and is accepted only when both batches are the two most recent abstract batches for the profile, both contain only `pending` members, and neither has a `consequential: true` item. While a checkpoint exists, unread `pending` members carry no reading obligation and are listed in `counts.inventoried_unread`. A later batch with a consequential item, a new screening round, or a policy change removes the checkpoint's effect.

### 9.6 Policy report

`policy-report [--policy screened-v1] [--reference FILE]` is read-only. It reports members by disposition, the audit sample, unread counts, and, with a reference file `{"prior_art": [...], "contradictions": [...], "methods": [...], "doctrine": [...]}` of family ids, recall per category for the selected preparation set. Without `--policy` it reports the study's recorded policy.

### 9.7 Constitution

`RESEARCH_CONSTITUTION.md` becomes Version 2 with a section "Population and preparation policy": the population is inventoried completely and retained; a recorded, versioned policy selects the preparation set; papers that may establish prior art, contradict a claim, define an assumption or method, correct a source, set a standard, or resolve an identity are promoted; every exclusion keeps its reason; exclusions are audited; a screen is not a reading.

## 10. Resource accounts (P8)

`budget` records `resource_budget/<profile>:<purpose>` with `{"profile", "purpose": "literature|screening|experiment", "limits": {"network_requests", "source_bytes", "readings", "screenings", "model_input_tokens", "model_output_tokens", "wall_seconds"}, "reason"}`. Any limit may be null. A later `budget` for the same key needs `reason` and may not set a limit below the charged amount.

`resource_account/<profile>:<purpose>` holds `charged`, `reserved`, and `unknown` per unit. Acquisition operations reserve `max_requests` network requests at admission and reconcile to `attempts_used` and captured bytes at finish. `read-batch` and `screen-batch` charge item counts and the reported `usage`; null usage increments `unknown`. `search` charges nothing.

A charging mutation whose charge would exceed a limit fails with `resource_budget_exhausted`. Gates report `resource_budget_exhausted` as an obligation while any charged amount is at or above its limit. `status --summary` shows the accounts. Read commands write nothing.

## 11. Hosts, skills, and documentation

- Shared `bin/`, `skills/`, and `hooks/` serve both hosts. `codex/generate.py` regenerates the Codex entrypoints; no hook is added.
- Skills updated: `ai-science` (SKILL, LOOP, STUDY), `cohort`, `literature-review`, `ideate`, `experiment`, `write` (SKILL, WORKSPACE), `evaluate`, `verify`, `submit`, `deposit`, `math-solver`, and `codex/README.md`. Resume reads `status --summary` and `next --summary`; details come from `obligations`.
- Documentation updated: `docs/research-workflow.md`, `docs/research-cli.md`, `docs/research-cli-examples.json`, `RESEARCH_CONSTITUTION.md`, `docs/releases/0.38.0.md`, `README.md`.
- Version 0.38.0 in both manifests, the three test literals, and the README.

## 12. Acceptance and measurement

- Every change lands with failing tests first. The validation protocol's cases M01 to M28 have fixtures. Existing tests stay enabled.
- Report equivalence: identical snapshot, identical readiness, obligations, and digests between the previous and new evaluators.
- Bounds: 16 KiB and 4 KiB measured on adversarial reports.
- Integrity: corrupt records, changed bytes, hot journals, and stale cursors fail as before or as specified.
- Measurement on the closed-gravity study, read-only: `status` wall time, CPU time, RSS, and bytes before and after; `policy-report` counts. The engineering target is at most 30% of the baseline median CPU time with exact gate equivalence. The result is reported as measured, including when the target is missed.

## Out of scope

- Storage compaction, diff-based event changes, and any schema version change.
- A mapped reading view for anomalous extractions; the recorded diagnostics decide whether it is needed.
- Harness-driven search fetching; searches stay agent-captured and harness-validated.
- Changing the innovation minimum and maximum or the reviewer counts.
- Registering the 108 already-read abstracts of the closed-gravity study; that is study work done with `read-batch` after release.
- Publishing the release to the marketplace; that needs the user's confirmation.
