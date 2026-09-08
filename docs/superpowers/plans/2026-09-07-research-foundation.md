# Research Foundation Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task by task. Each implementer works alone; the controller supplies independent task and final reviews.

Goal: Implement the approved research foundation, actual cohort reading inputs, cumulative research development, and authoritative prerequisites, and integrate the tested change through local-dev, dev, and main.

Architecture: A standard-library research_harness package owns durable evidence and shared validation. Existing CLIs integrate these services; the new exactory-research CLI supplies explicit collection, reading, synthesis, development, adoption, status, and gate operations. Skills teach the intellectual work against those contracts.

Tech Stack: Python 3.9+, SQLite, unittest, urllib/http.client, optional installed pdftotext, existing Git and Codex/Claude adapters.

Spec: docs/superpowers/specs/2026-09-07-research-foundation-design.md

## Global Constraints

- Support Python 3.9 or later on macOS and Linux with the Python standard library. PDF text extraction may invoke an installed pdftotext executable with explicit argv and a timeout; missing extraction support creates a pending obligation, never a successful reading.
- All implementation, documentation, comments, and generated artifact text are English.
- All research runs and acquired research evidence for development stay under /Users/ryshiro/exactory/exactory-research and are not committed.
- Preserve verifier soundness independently of novelty, significance, and impact prediction. Preserve the rule against reading other verdicts before filing one's own.
- Preserve the math controller's proof acceptance, accounts, budgets, lineage, and recovery invariants.
- Preserve the five literature search purposes and the existing whole-cohort abstract reading obligation.
- Never mark unavailable, partial, timed-out, stale, failed, or budget-exhausted work complete. No force, ignore, disable, or skip mechanism may satisfy an unmet research gate.
- Tests must verify meaningful behavior. Fix gate findings in code or design without suppressing the finding.
- Keep local-dev, dev, and main permanently, and integrate through them in that order.

## Task 1: Durable research evidence storage

Files: create research_harness/__init__.py, errors.py, storage.py, artifacts.py; tests/test_research_storage.py and tests/test_research_artifacts.py.

Consumes: no new domain dependencies. Produces the exact Store, Transaction, and ArtifactStore APIs in spec section 2. ResearchError is a typed exception with code, message, and optional details, suitable for JSON error reporting, and does not terminate the interpreter.

- [ ] Write failing storage tests, including this actual callback contract, conflict replay, stale revisions, rollback, two-connection contention, schema corruption, and immutable artifact traversal/symlink/hash failures.

```python
store = Store(workspace, create=True)
def add(tx):
    tx.put("work", "arxiv:1706.03762", {"title": "Attention Is All You Need"})
    return {"id": "arxiv:1706.03762"}
first = store.mutate("test-add", {"id": "arxiv:1706.03762"}, add,
                     expected_revision=0, request_id="request-1")
second = store.mutate("test-add", {"id": "arxiv:1706.03762"}, add,
                      expected_revision=0, request_id="request-1")
self.assertEqual(first, second)
self.assertEqual(store.revision, 1)
self.assertEqual(store.snapshot()["records"]["work"]["arxiv:1706.03762"]["title"],
                 "Attention Is All You Need")
```

- [ ] Run `python3 -m unittest discover -s tests -p 'test_research_st*.py' -v` and the artifact file separately. Record the initial expected failure.
- [ ] Implement SQLite transactions, finite lock wait, canonical JSON, schema version validation, original-result idempotency, event patches, and content-addressed artifact writes. Exclude no paths from validation. Read-only open must not create files. SQLite must not follow an out-of-workspace symlink.
- [ ] Run the focused tests and existing root tests, self-review, commit as `feat(research): add transactional evidence storage`, and report RED/GREEN evidence and any concerns.

## Task 2: Real literature acquisition and cohort abstracts

Files: create research_harness/http.py, providers.py, acquisition.py, identities.py; tests/test_research_acquisition.py, tests/test_research_transport.py, tests/fixtures/research/*; modify bin/exactory-cohort and add tests/test_cohort_collection.py.

Consumes: Store/ArtifactStore/ResearchError. Produces acquisition functions `collect_cohort(store, definition, *, request_id, expected_revision, max_requests=None)` and `acquire_work(store, identifier, *, request_id, expected_revision)`, plus provider-normalized work/import records documented in a module docstring and report. Long collection commits resumable pages using deterministic derived request IDs; a supplied revision applies to admission, not blindly to every later page. Request limits pause rather than certify completion.

- [ ] Write failed tests around an injected arXiv transport yielding multiple Atom pages with original abstracts, duplicate/cross-listed papers, empty intermediate page, changed total, rate limits, and interrupted/resumed pagination. Preserve freeze's exact existing JSON contract.

```python
result = collect_cohort(store, definition, request_id="collect-1",
                        expected_revision=store.revision, max_requests=1)
self.assertEqual(result["status"], "paused")
self.assertTrue(result["pending"])
# The acquisition fixture supplies an abstract that must exist as verified saved bytes.
work = store.snapshot()["records"]["work"][expected_id]
self.assertEqual(ArtifactStore(workspace).read(work["abstract"]).decode(), expected_abstract)
self.assertNotIn("reading", store.snapshot()["records"])
```

- [ ] Run `python3 -m unittest discover -s tests -p 'test_*collection.py' -v` and the new acquisition/transport files, recording expected RED.
- [ ] Implement actual paginated arXiv collection, OpenAlex/Crossref metadata/reference acquisition, safe full-text HTTP capture, PDF extraction with explicit failure states, and provenance-preserving normalized import for web/MCP. Expose cohort `collect` and `status` operations without changing freeze date arithmetic. Validate identities and alias evidence. No hidden truncation, downloads-as-reading, or source credentials in logs.
- [ ] Test redirects, private targets, MIME/size/time limits, Retry-After, missing abstracts, malformed/scanned PDFs, conflicting aliases, acquisition failure and resume; run root suite, commit and report interfaces/results.

## Task 3: Citation graph, reading evidence, and five-purpose searches

Files: create research_harness/graph.py, literature.py, reading.py; tests/test_research_graph.py, tests/test_research_reading.py, tests/test_research_literature.py; refine acquisition/provider integration only as needed, including the explicit versionless cohort-abstract selection contract.

Consumes: normalized work/reference records from Task 2. Produces domain operations `set_roots`, `import_bundle`, `record_reading`, `record_search`, `require_fulltext`, `select_cohort_abstract`, and `foundation_report(store, profile)`; mutating operations accept explicit expected_revision and request_id. A report returns `{ready: bool, digest: str, obligations: list, counts: dict}`. An obligation has a stable code, affected work/reference and actionable explanation.

- [ ] Write failing graph fixtures with roots A/B, shared reference C, cycles, unresolved occurrences, incomplete bibliography, and a Tier 3 work required at full depth. The lowest tier wins; no occurrence disappears.

```python
report = foundation_report(store, "research")
self.assertFalse(report["ready"])
self.assertIn("fulltext_reading_missing", {x["code"] for x in report["obligations"]})
# Recording a note for the abstract must leave the full-text obligation open.
record_reading(store, abstract_note, expected_revision=store.revision, request_id="read-1")
self.assertFalse(foundation_report(store, "research")["ready"])
```

- [ ] Run focused RED tests. Implement source-specific notes and locator checks, full-text versus abstract distinction, actual cohort abstract reading coverage, five search purposes with saved-response provenance, pending/unavailable policy, and dependency digests.
- [ ] Ensure later/current work can exist outside the backward graph, alias/version edits invalidate affected decisions, and complete graph claims fail on unknown reference coverage. Export human-readable inventory/coverage without trusting exports as state.
- [ ] Test explicit current abstract selection for a versionless cohort observation while retaining its unresolved history, original-result replay, and all unrelated pending conditions. Reject a different family, mismatched dates/categories, incomplete evidence, and replacement of a known version's obligation with another version.
- [ ] Run focused and root tests, commit and report. Keep all acquired original content separate from generated notes and never fabricate missing abstracts.

## Task 4: Research principles and evidence-linked synthesis

Files: create RESEARCH_CONSTITUTION.md, research_harness/principles.py, synthesis.py; tests/test_research_synthesis.py and tests/test_research_principles.py.

Consumes: current foundation report, work/source/reading records. Produces `record_standards`, `record_rationale`, `record_innovation`, `record_context`, and `synthesis_report(store, profile)` with the same explicit mutation envelope/report conventions. Initialize profile/target/constitution via a named configuration record, not an untyped loose file.

- [ ] Write failing tests requiring source-linked field standards and cohort doctrine, cited established context and bottleneck, study-specific source-linked innovation notes, five to ten full-read external cases for research, within-field cases, dated current/historical context, and unknown-application uncertainty.

```python
report = synthesis_report(store, "research")
self.assertFalse(report["ready"])
# A truthful basic-science rationale is eligible even with no promised deployment date.
record_rationale(store, basic_science_rationale,
                 expected_revision=store.revision, request_id="rationale-1")
self.assertNotIn("near_term_application_required",
                 {item["code"] for item in synthesis_report(store, "research")["obligations"]})
```

- [ ] Run RED tests. Implement constitution version binding and revalidation, ABT evidence links, innovation mechanisms and transfer limits, scientific/social uncertainty, source dates, and profiles appropriate to authoring versus verification.
- [ ] Keep originality, later validation, and adoption separately evidenced. Reusing a common case collection still requires task-specific analysis; famous sources do not confer correctness. Use the controller's locally researched case study note for design insights only, without committing acquired research evidence.
- [ ] Run tests, commit and report. All source-link validation uses the same artifact and reading functions rather than duplicated partial checks.

## Task 5: Cumulative hypotheses, checkpoints, and readiness

Files: create research_harness/development.py; tests/test_research_development.py and reusable research fixtures under tests.

Consumes: foundation and synthesis reports plus Store/ArtifactStore. Produces `plan_cycle`, `record_execution`, `assess_cycle`, `checkpoint`, `record_readiness_review`, and `readiness_report`, all using stable IDs and explicit current revisions/request IDs. A ready result binds the exact objective/scope, literature digest, result hashes and independent assessment.

- [ ] Write failing tests for execution without plan, unverified successful process, partial scope asserted as full success, stale evidence, reused exhausted strategy, missing development assessment, negative findings preserved, and objective/branch lineage.

```python
self.assertFalse(readiness_report(store)["ready"])
# A result for a special case cannot close the full objective.
assess_cycle(store, partial_result, expected_revision=store.revision, request_id="assess-1")
self.assertFalse(readiness_report(store)["ready"])
self.assertTrue(store.snapshot()["records"]["cycle"][cycle_id]["remaining_obligations"])
```

- [ ] Run RED tests. Implement planned outcomes/failure signals, validated result evidence, durable checkpoint lineage, useful next-development decisions, explicit budget-paused states, and current independent readiness reviews. Preserve validated results across unsuccessful branches.
- [ ] Add development strategy vocabulary and verify a good first result requires a substantive assessment, while an arbitrary cycle count never proves readiness. Refresh dependent literature decisions on claim/scope changes.
- [ ] Run tests, commit and report. Do not replace native math controller proof acceptance with this package's empirical readiness labels.

## Task 6: Supported CLI workflow and authoritative guards

Files: create bin/exactory-research, research_harness/cli.py, gates.py, integration.py; modify bin/exactory-lab, bin/exactory-draft, bin/exactory, relevant hooks, native math-controller admission/integration boundaries, and their existing tests; add tests/test_research_cli.py and tests/test_research_gates.py.

Consumes: all domain APIs. Produces executable init/adopt, acquire/import, roots/expand, reading/search, standards/rationale/innovation/context, cycle/result/assessment/checkpoint/review, status/next/export/gate operations. Command help and typed JSON examples specify exact input shapes. Every mutating command supports optimistic revision and idempotency; long collectors persist their operation and page receipts.

- [ ] Write failing subprocess tests proving a bare draft marker and a direct state jump cannot satisfy prerequisites, cohort cannot advance without actual abstract reading, source changes invalidate readiness, wrong PDF/DOI/verification target fails before mutation, and external verification-request creation still works.

```python
process = run_cli("exactory-lab", "state", "set", "--stage", "write")
self.assertNotEqual(process.returncode, 0)
self.assertIn("readiness", process.stderr)
self.assertEqual(read_study()["stage"], original_stage)
```

- [ ] Run RED tests. Add literature stage, explicit allowed backward edges, status-done validation, admitted-cycle run guard, current-contract init, and lossless adoption with pending obligations. Wire guards at existing CLI/service operations before external mutation. Artifacts are hashed/pinned and uploaded from the validated snapshot; record/reconcile publication intent so retries do not blindly duplicate remote records.
- [ ] Preserve soundness/novelty separation, blind-verifier inputs, external submit before a verifier's research, and administrative reads. Guard native math authoritative boundaries using a pinned common foundation without changing native budgets/proof/account state or overwriting historical acceptance.
- [ ] Update existing tests for intentional prerequisite changes using valid fixtures; retain their original date/transport/security/proof assertions. Run root and native math suites, commit and report. No bypass flag or implicit successful migration.

## Task 7: Skill workflow, documentation, and host distribution

Files: create skills/literature-review/SKILL.md and supporting workflow/schema examples; modify all shared skills to reference the constitution, cohort/ideate/experiment/write/evaluate/verify/ai-science orchestration/workspace docs, math shared foundation instructions, README.md, codex/README.md, manifest version and release notes; regenerate codex entrypoints and hooks; modify/add contract tests and CI compile coverage for research_harness.

Consumes: actual CLI help, implemented validation contracts, and accepted principle source. Produces usable end-to-end skill paths for both hosts, accurate upgrade instructions, and examples that run against current parsers.

- [ ] Add failing contract tests for common constitution references, complete generated entrypoints, new executable list/version consistency, and CLI examples with real parser acceptance. Existing manifest tests must enumerate the new command honestly.
- [ ] Update each skill's actual entry/transition/stop/resume instructions. Cohort must collect and read rather than handcraft a nonexistent membership list. Literature-review must teach tier depth, all five search purposes, innovation lessons, social context, and unavailability policy. Ideate/experiment must perform the pre-draft research loop and preserve accumulated results.
- [ ] Document profiles and adoption, independent soundness, workload/budget behavior, and exact mechanical versus intellectual guarantee boundaries. Document field/venue standards and no page/citation quotas. New release version is 0.35.0 because prerequisites materially change behavior; update all necessary single-source version expectations.
- [ ] Run `python3 codex/generate.py`, `python3 codex/generate.py --check`, parser/contracts, root suite, and appropriate compile checks. Commit and report. Do not publish a release tag or alter a user's running installed cache as part of this task.

## Task 8: System validation, failure recovery, and main integration

Files: add focused end-to-end/adversarial tests where gaps remain, docs/releases/0.35.0.md validation results, and any necessary fixes identified by independent whole-branch review. Research examples and actual source bytes remain local.

- [ ] Run a real small arXiv cohort acquisition from the local research directory, inspect saved abstracts against original responses, and demonstrate pending reading -> recorded reading -> valid transition. Exercise network failure/resume without claiming a full corpus from partial output.
- [ ] Run an end-to-end small evidence-backed research and verification workflow with honest local source fixtures or acquired material, including a failed hypothesis and a useful successor, changed source invalidation, checkpoint resume, and independence of verifier soundness from contribution goals. Obtain an independent evidence review and report the practical limits of the demonstration.
- [ ] Run all required CI checks, two-connection/concurrent mutation and interruption tests, plus final independent whole-branch review. Record and fix every material finding. No blanket waivers, coverage exclusions, or test skips introduced to pass.
- [ ] Commit final fixes and validation report, push the feature branch, inspect remote CI and review findings, resolve failures in code. Integrate through local-dev, then dev, then main without force pushes. Confirm all three remote tips equal and all required checks on the integrated commit are green. Preserve permanent branches.

## Controller preflight and decisions

Task interfaces and shared files are reviewed in the SDD ledger before execution. Task 2 owns initial cohort CLI acquisition; Task 6 integrates common gates; Task 7 documents the final APIs. The package's public store/error contracts are fixed by Task 1. Domain operation details are documented in each implementation report and carried to the next brief; the spec, rather than example fixture naming, governs correctness.

The user already authorized execution, resource expenditure, independent quality work through superpowers, and integration. The controller resolves routine details using the approved design and records them; no additional permission checkpoint is required for the planned merge flow.
