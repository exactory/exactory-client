# Research foundation and development harness design

Date: 2026-09-07.
Status: Approved direction and implementation decisions. The user authorized implementation through local-dev, dev, and main after reviewing the discussion proposal, explicitly accepted foundational scientific value with uncertain applications, and prioritized actual cohort abstract acquisition.

## Objective

Make purposeful, well-grounded research a managed process: acquire and read the relevant literature, identify a consequential scientific bottleneck, learn from prior innovations, develop hypotheses through cumulative validated work, and preserve independent assessment. Enforce the mechanical prerequisites in executable code. Do not claim that software can guarantee understanding, social benefit, truth outside a checker’s scope, or a breakthrough.

## Global constraints

- Support Python 3.9 or later on macOS and Linux with the Python standard library. PDF text extraction may invoke an installed pdftotext executable with explicit argv and a timeout; missing extraction support creates a pending obligation, never a successful reading.
- All implementation, documentation, comments, and generated artifact text are English.
- All research runs and acquired research evidence for development stay under /Users/ryshiro/exactory/exactory-research and are not committed.
- Preserve verifier soundness independently of novelty, significance, and impact prediction. Preserve the rule against reading other verdicts before filing one's own.
- Preserve the math controller's proof acceptance, accounts, budgets, lineage, and recovery invariants.
- Preserve the five literature search purposes and the existing whole-cohort abstract reading obligation.
- Never mark unavailable, partial, timed-out, stale, failed, or budget-exhausted work complete. No force, ignore, disable, or skip mechanism may satisfy an unmet research gate.
- Tests must verify meaningful behavior. Fix gate findings in code or design without suppressing the finding.
- Keep local-dev, dev, and main permanently, and integrate through them in that order.

## 1. Components

Add a root-level research_harness Python package, an exactory-research CLI, and a shared literature-review skill. Keep the existing cohort prediction definition unchanged. Add real collection to exactory-cohort using the new acquisition service. All managed CLI entrypoints call shared validators, with hooks as supplementary enforcement.

Package responsibilities:

| Module | Responsibility |
|---|---|
| errors.py, storage.py, artifacts.py | Typed errors, atomic revisioned records, idempotency, immutable source bytes, checked workspace paths. |
| identities.py, graph.py | Work and version identifiers, references, minimum citation tier, outstanding reference obligations. |
| http.py, providers.py, acquisition.py | Safe bounded transport, registry adapters, resumable metadata and full-text collection, normalization and provenance. |
| literature.py, reading.py | Corpus import, five search purposes, source-specific reading records, coverage and foundation digest. |
| principles.py, synthesis.py | Constitution contract, ABT rationale, field and external innovation cases, scientific/social context. |
| development.py | Planned hypothesis cycles, evidence-bound result assessments, checkpoints, next developments, research readiness. |
| gates.py, integration.py | Task profiles, managed transition/publication guards, explicit adoption and stale-input checks. |
| cli.py | Typed JSON-file inputs, useful next-action/status output, deterministic nonzero failures. |

Split a responsibility further when it makes the interface clearer. Do not create duplicate validators in the binaries.

## 2. Durable storage contract

Use SQLite from the standard library at .exactory/research.sqlite3. A metadata row holds schema version and global revision. The authoritative configuration record (kind configuration, key research) holds task profile, target, and constitution digest, through the same transaction API as other records. Storage initialization alone creates no scientific configuration or readiness. Records have a kind, stable key, canonical JSON value, and content digest. Events and request receipts are append-only. Transactions use BEGIN IMMEDIATE, a finite busy timeout, rollback on failure, and compare-and-swap revisions. Reject unknown future schemas and malformed/corrupt state explicitly.

The shared profiles are `research` and `verification`. A verification target has the shape `{kind: "work", id: EXACT_WORK_ID, source_id: SOURCE_ID_OR_NULL, sha256: ORIGINAL_MAIN_DOCUMENT_SHA_OR_NULL}`. Its identifier is an exact `work.id`, not a paper-family alias. Missing source pins are allowed while preparing the study but leave the verification foundation pending. A complete pin identifies the target's original main full-text bytes, rather than a metadata response, abstract, or text extraction. The literature scope uses the same target shape and must agree with the configured target. A research target is initially null, then `{kind: "objective", id: OBJECTIVE_ID, statement: FULL_OBJECTIVE}` when the complete objective is fixed. This does not replace branch scopes, remaining obligations, or the native mathematical acceptance contract.

Public storage API:

```python
Store(root: Path, create: bool = False)
store.revision -> int
store.snapshot() -> dict  # {revision: int, records: {kind: {key: value}}}
store.mutate(operation: str, payload: dict, apply, *, expected_revision: int, request_id: str) -> dict
# apply(transaction) returns JSON-serializable result.
# Returned envelope: {revision: int, request_id: str, result: ...}.
transaction.get(kind: str, key: str) -> Optional[dict]
transaction.records(kind: str) -> dict
transaction.put(kind: str, key: str, value: dict) -> None
```

The same request ID and identical operation/payload returns the original committed result, even after later revisions. A conflicting replay or stale new mutation fails before changing any state. Reads and failed mutations must not create or upgrade a store. Preserve original mutation values in the event record, so updated projections do not erase history. Do not store a full corpus snapshot after every individual paper update.

If a crashed SQLite write leaves a hot rollback journal, default read-only opening fails with a typed store_recovery_required error instead of writing during a read. Explicit Store(root, create=True) may perform SQLite's native rollback recovery after managed path checks, then validate the existing store. Recovery preserves the last committed revision, records and history. It does not migrate schemas, reset state or certify scientific progress. Provide an explicit supported recovery command in the CLI.

Content objects are stored under research/sources/objects/ by SHA-256. ArtifactStore(root).put(data: bytes, media_type: str) returns {sha256, path, size, media_type}; read(ref) returns verified bytes. Paths are workspace-relative, regular files, and cannot escape via symlinks or traversal. Verify existing objects on reuse. Record source URL, provider, exact source version, capture time, and retrieval failures in domain records. Do not retain credentials in provenance. Mutable human-readable exports are projections, never trusted authoritative state.

This protects managed operations and detects inconsistent artifacts. It does not make a user-writable local directory immune to an actor who can replace the program and database.

## 3. Acquisition and cohort

The existing exactory-cohort freeze JSON stays exactly {corpus, primaryCategory, windowStart, windowEnd}. New collection enumerates the full category/window using paginated arXiv metadata, saves original responses and each abstract, and distinguishes returned cross-lists from matching primary-category members. Persist cursor, source count, unique count, extraction failures, and completion state. Never silently stop after a default number of papers. An explicit request/page/time budget pauses with pending work, and resume continues the same query. Do not turn an upstream zero-page failure or changing cursor into an empty completed cohort.

Support arXiv metadata and PDF/HTML sources, OpenAlex metadata/reference graphs/abstract reconstruction, and Crossref metadata/references. Services may not return abstracts or complete references; represent that absence honestly. Credentialed registries use optional environment configuration without logging secrets. An authorized web/MCP result can be imported with its source provenance, exact response artifact, and matching normalized records through the same service. A failed provider does not erase previously acquired evidence.

HTTP uses HTTPS, finite timeouts, finite response sizes, bounded retries for transient failures with Retry-After support, and explicit failure classification. Reject private/loopback/link-local destinations and credential-bearing URLs for untrusted downloads, including redirects. Test transport with injected openers and response fixtures, not live network in unit tests. Respect arXiv request pacing and provider pagination contracts.

Every normalized work contains canonical id, known aliases, version, title, authors, publication date when known, category when known, abstract artifact when available, full-text artifacts when available, source provenance, and reference metadata status. Preserve unresolved references and non-paper sources. Do not merge ambiguous titles. arXiv DOI aliases and work/version identity require explicit resolution evidence.

Collection retrieves content but does not mark it read. next/status must expose the actual next abstract or source path and its reading obligations, so the agent can read it and submit evidence-specific notes.

## 4. Literature and reading

Tier 1 contains one to five closest starting works. In verification it includes the pinned target and up to four comparisons. Tier 2 contains all direct references not in Tier 1. Tier 3 contains all references of Tier 2 not already in Tier 1 or 2. Compute minimum tier over cycles and duplicate paths. Account for every reference occurrence from Tier 1 and 2, including unresolved ones. An incomplete bibliography prevents a complete graph claim.

The verification target's full reading must cover its pinned original bytes and the current required source-bundle units. A different captured body under the same DOI or work identifier cannot satisfy the pin. Identical verified original bytes for the same exact work may reuse an existing valid reading through explicit source linkage, while retaining the configured pin's provenance. A new response receipt alone need not require another reading. Newly required figures or supplements remain obligations even when the main document's bytes are unchanged.

Tier 1 and 2 require every accessible full text read in full. Tier 3 requires available abstracts read. Cohort members separately require abstracts read. Full-text reading satisfies abstract depth when the abstract is included. A source relied on for a major claim, novelty decision, innovation case, or validity finding requires full reading even when it is Tier 3 or outside the graph. This changes the depth obligation, not the graph tier.

Record source-specific notes with summaries of the problem, claims, assumptions, methods, supporting evidence, limitations, and relevance, and locators into the actual saved text. Validate that the cited text fragment exists in the stored source; abstract notes cannot satisfy a full-text obligation. Missing extraction, malformed documents, and truncated captures remain pending. Preserve originals for figures/tables and require a record of visual inspection when extraction is incomplete.

The five search purposes are direct, originals, theory, adjacent, and recent. Each search record contains purpose, queries, provider/source, capture date, found work IDs, novelty verdict (the existing vocabulary), impact, and remaining gaps. Empty results are allowed with an actual response record; an empty list supplied without provenance is not a search. Search beyond the backward graph, including later citations/current work, to avoid graph blind spots.

An unavailable record requires captured retrieval attempts under a specified policy. Critical missing dependencies block foundation/readiness or comprehensive verification of that dependency. Noncritical unavailable material stays explicitly in coverage output and may satisfy only an availability-qualified obligation after documented attempts. A resource pause never satisfies an unavailable condition. Do not shrink the corpus to pass a gate.

Foundation validation returns a structured report with passed conditions, pending obligations, invalid references, and a digest of exact dependencies. Follow-on decisions bind to this digest, so unrelated new notes need not invalidate unrelated evidence while changed source bytes or changed relevant scope invalidate dependent decisions.

## 5. Constitution, innovation, and context

Create one versioned RESEARCH_CONSTITUTION.md linked by every shared skill and correctly exposed to generated Codex entrypoints. Initialization records its digest. A release that changes the constitution requires explicit revalidation of active decisions against the new digest; no automatic success migration.

Principles: purpose and honest scientific value, respect through faithful study and attribution, ABT reasoning grounded in evidence, field standards, ambition demonstrated by fair comparison, transferable lessons from prior work, cumulative inquiry, truthful failures, and deliberate resource use. Basic science with uncertain near-term application is valid. Investigate social connections and uncertainty without inventing applications. Formatting is field/venue-specific; no page or citation quotas.

Record applicable field standards as evidence-linked synthesis, preserving the cohort doctrine. Identify the field, article type and venue when chosen, methodological and reporting expectations, citation and presentation conventions, supporting sources, and unresolved applicability questions. Both research and verification profiles require a current standards assessment. The record supports review of actual adherence; field presence alone does not prove that a finished paper meets every convention.

ABT records reference saved sources: And is established context, But is a consequential unresolved bottleneck, Therefore is a proposed response and distinguishing test. Distinguish proposal from observed conclusion.

Require within-field innovation analysis and five to ten external full-text cases for a research foundation. A versioned shared collection may be imported, but each study records its own relevance and transfer limitations. Cases reference original result, later validation, and eventual adoption as separately dated claims. Citation counts and author recognition are discovery signals only. Store source-linked original bottleneck, prior constraint, conceptual change, evidence, scope, possible transfer and failure conditions.

Scientific/social context records include current topics and historical neighboring impacts, dated source evidence, beneficiaries or downstream scientific capabilities, adoption barriers, uncertainties, and explicitly speculative links. A proposed social application never substitutes for proof or experiment.

Verification uses the same acquisition/reading contracts and field standards. It must not inherit the author's judgments, desired scores, innovation target, or prior reviews. The target's soundness and its novelty/significance remain separate. Current later developments must not be mislabeled as prior art at the original publication date. Reusable innovation sources do not require manufacturing a private authoring objective for a verifier.

## 6. Cumulative research development

A cycle is planned before execution. It records a stable ID, predecessor checkpoint if any, full objective, exact hypothesis, foundation digest, related works, distinguishing test, expected outcomes, failure signals, evidence requirements, and declared resource limits. Executed evidence is an immutable file or run artifact with command/version/seed information where applicable. A cycle cannot be closed as successful from a process exit code alone.

Results receive evidence-linked assessments: what was shown, validity checks, what failed, how the bottleneck changed, assumptions and scope, unresolved obligations, next useful development, and branch disposition. Preserve failed results. Checkpoints bind these records and the source/result digests. A successor inherits explicit results and assumptions; it must pose a distinct development question rather than reset an exhausted budget.

Support generalization, weaker assumptions, mechanism, tightness/limits, unification, representation change, transfer, and practical usefulness as a strategy vocabulary with an open text explanation. A strategy is selected for scientific value, not to satisfy a count or score. User-requested complete objectives stay open when only a special case is established.

Readiness requires validated results, a current novelty comparison, an ABT contribution explanation, a substantive development assessment, and a disposition of remaining useful branches. Require an independent evidence-linked readiness assessment against the exact candidate snapshot; this is research assessment before manuscript review, not a self-assigned innovation score. A first result cannot automatically proceed to writing; neither is an arbitrary minimum number of experiment cycles sufficient or necessary.

Budget exhaustion or lack of progress produces a durable incomplete checkpoint. A useful partial/negative result can be preserved and accurately documented. It does not satisfy a larger root objective or silently authorize publication of a paper below the agreed contribution objective. User-directed publication of a narrower result requires a distinct recorded scope and readiness assessment, not an integrity-gate bypass.

## 7. Managed entrypoints and migration

New study stages are initiate -> cohort -> literature -> ideate -> experiment -> write -> evaluate -> deposit -> submit -> complete. Explicit reverse edges support literature refresh, experiment -> ideate after an assessed result, and evaluate -> experiment for evidence gaps. Stage advancement checks the source stage's required evidence as well as target prerequisites. A status-only done mutation cannot skip these checks. Waiting/paused status does not assert success.

exactory-lab init initializes the current research contract. exactory-draft init may initialize a title/workspace before research readiness, as ideate currently does; it must initialize the contract and cannot certify readiness. A standalone imported study uses explicit import/adopt and the same validators.

exactory-lab run requires an admitted active hypothesis cycle and records execution evidence. Writing/evaluation transitions require relevant readiness and current claims. Production deposit and managed author submit bind the actual PDF, bibliography, reviews, record DOI, and current research receipts. Validation occurs before the first network mutation. A different --pdf must be validated as that artifact. Sandbox is a real artifact mutation and needs an appropriate current artifact contract as well.

An external exactory submit creates a verification request for someone else's existing record and must remain possible before research begins. It is not an author-publication success and must not advance an author study. Verify verdict submission requires a target-bound verification foundation, never an author-readiness score. Administrative login, tasks, task, and status reads remain lightweight.

Existing workspaces stay readable, but legacy state/marker absence cannot bypass gates. An explicit adopt operation preserves all source bytes/history and creates current pending obligations; it does not mark old work validated or reset native math budgets. Entry failures name the unmet prerequisite and next supported command. No force or legacy-skip flag.

Math managed admission/execution uses shared foundation gates at the existing authoritative controller boundaries. Existing immutable proof results retain their existing acceptance contract; adoption must not retroactively claim new literature work was done or silently alter a paused attack. Native paths receive the same checks where they claim managed research eligibility.

## 8. Verification and delivery

Test failures before implementation, then prove expected behavior with fixtures and subprocesses. Include pagination/resume, source alias ambiguity, graph cycles and tier promotion, abstracts actually saved, full-text extraction boundaries, incorrect source locators, missing critical sources, forged or stale receipts, budget exhaustion, request replay, concurrency, crash recovery, direct stage jumps, standalone markers, wrong publication targets, verifier independence, and native math invariants.

Run all existing test suites, generated-entrypoint checks, compile checks, and CI. New required behavior may update tests that intentionally asserted the old weak behavior; preserve their transport/date/proof assertions with valid fixtures rather than disabling tests. Make a real small corpus acquisition and research workflow demonstration in the local research directory. Record source access failures as failures, not empty data. Assess source-faithfulness and prototype contribution evidence independently; do not promise general research quality improvement from unit tests alone.

Use task-specific independent reviews and a final whole-branch review. Fix findings in code. Publish release/upgrade documentation covering new CLI use, migration, workload implications, enforcement limits, and checks run. Merge through local-dev, dev, main without force pushes or deleting permanent branches. User authorization already covers this integration; do not ask again.
