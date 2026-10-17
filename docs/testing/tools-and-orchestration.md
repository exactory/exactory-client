# Tools and orchestration (0.52.0): test record

Source design: `misc/tools-and-orchestration/DESIGN.md` version 0.5 in the
exactory repository, with the review record `REVIEW.md` in the same folder. The
user decided the principle and its gates on 2026-10-16 (`DECISIONS.md`).
Implementers: gpt-6.1-sol at effort xhigh, and at effort high for small
follow-ups. Reviewer of every slice: Claude. Code review: Astra (gpt-6-astra
at effort xhigh), two rounds.

## Journeys

1. As an author, I want to run a quick calculation before I choose a theme, and
   keep it as exploratory evidence that gives no result credit.
2. As an author, I want to read more or fewer papers in a field than the
   defaults, and have each record judged by the amounts that it recorded.
3. As an author, I want to search before my literature scope exists, and reuse
   the captured responses after I choose it.
4. As an author, I want the direction of a study to need a minimum of evidence
   and a Q1 bar that both reviewers saw.
5. As an author of a study in progress, I want recorded work to stay current
   after the upgrade and after unrelated readings, searches and synthesis
   changes.
6. As a mathematician, I want math-solver to start without the common
   preparation, and to close only with a supported openness finding.

## Commits

| Commit | Slice | Subject |
| --- | --- | --- |
| 98deb49 | S3 | docs: describe the research harness as shared tools plus orchestrations |
| d03a04f | S2b | feat: require the direction minimum and the Q1 bar for new direction decisions |
| 879db61 | S1 | feat: add the depth plan, the exploratory probe and recorded search coverage |
| 87f8351 | merge | Merge impl/s1-depth-probe: depth plan, probe and search coverage |
| b217d40 | integration | test: check direct-search integrity errors in both record shapes |
| d150e66 | S1 fix (round 1) | fix: supervise probes after worker loss and keep verification's search rule |
| 9fd46b7 | merge | Merge impl/s1-depth-probe: probe supervision, verification search rule, innovation depth |
| 7fc980b | M1 | feat: make math-solver an orchestration over shared tools with an openness gate |
| b00cb46 | merge | Merge impl/m1-math-solver: math-solver over shared tools with an openness gate |
| 8411933 | test hygiene | test: end probe test programs after 60 seconds |
| e05cdcd | S2b fixture fix | test: record the direction minimum before shared fixtures decide a direction |
| 45b1b4c | merge | Merge impl/s2b-direction-gate: record the direction minimum in shared fixtures |
| e901242 | integration | test: require the value-review example in the research workflow |
| 4f07e68 | S2b fix (round 1) | fix: count direction review coverage only from delivered source content |
| 137ddbe | merge | Merge impl/s2b-direction-gate: direction review coverage from delivered content |
| 95e5c09 | S2b fix (round 2) | fix: require complete inspected ranges and named images for direction coverage |
| 3a700fc | merge | Merge impl/s2b-direction-gate: complete inspected ranges and images |
| 439e913 | S1 fix (round 2) | fix: enforce the probe deadline after worker loss without a retry |
| c83aeaa | merge | Merge impl/s1-depth-probe: probe deadline without a retry |
| e866510 | M1 fix (round 2) | fix: require the openness minimum and keep probe provenance in math-solver |
| 4c9f1aa | merge | Merge impl/m1-math-solver: openness minimum and probe provenance |
| 73d301b | S2a | feat: keep claims current under unrelated changes and gate the direction |
| 1eba1ad | merge | Merge impl/s2a-currency-stages: claim currency, stages and the direction gate |
| 9b2fda9 | integration | feat: list the direction minimum before the cycle decision exists |
| 14faaed | merge (into S3) | Merge feat/tools-orchestration into impl/s3-texts for reconciliation |
| 67e220a | S3 reconcile | docs: reconcile the skill and reference texts with the merged code |
| b8e9d8b | merge | Merge impl/s3-texts: skill and reference texts reconciled with the code |
| 693fa04 | integration fix | fix: keep the direction standards current under advisory changes |

| cb8e7fb | integration (tests) | test: build legacy-constitution stores under the Version 6 contract |
| d6f869d | M1 fix (round 2 part 2) | fix: keep exploratory provenance through packet export and resolve duplicate hits |
| a965399 | merge | Merge impl/m1-math-solver: export provenance and duplicate resolutions |
| e7ca8f4 | S2a fix (round 2 part 2) | fix: gate the return to ideate and bind claims to the readings they use |
| 9f9e179 | merge | Merge impl/s2a-currency-stages: the return to ideate and the readings a claim uses |
| 0a963b1 | integration | refactor: authenticate only released candidate compositions |

| 803321f |  | feat: read and post math problems, vote, and report from the CLI |
| e169a14 |  | fix: keep CLI path segments in their route and share the vote and report requests |
| 947b996 |  | test: check that the CLI accepts every math report kind |
| 16a0c6c |  | feat: add formal statements, status declarations, and problem links from the CLI |
| 0f71497 |  | fix: name the repository pattern for what it holds and name whitespace in link refusals |
| 9b8f0b2 |  | feat: post proofs, Lean check reports, and discussion posts from the CLI |
| 6ce5164 |  | fix: refuse unreadable files and undecodable ids without a traceback |
| 6ac19dd |  | feat: deposit for a math problem, with a recovery journal and attach-problem |
| 8a89c2b |  | fix: trust only a checked journal and keep deposits open while an attachment is pending |
| 597e16e |  | fix: drive journal retries only against the current exactory origin |
| 92ae961 |  | fix: retry only the attachment, read the journal safely, and keep the token on Zenodo |
| 12746e4 |  | fix: keep the exactory API key from following a redirect to another origin |
| 02911ab |  | fix: check the journal origin at every attachment and refuse redirects of writes |
| 27e8f9e |  | fix: ask the user to check a refused write before running it again |
| 3986817 |  | fix: check plain text locally and ask for a check after a write whose outcome is unknown |
| 0f62697 |  | fix: give each failed request the advice that fits what the client knows |
| 2fc7282 |  | fix: tell an unsent request from an uncertain one and keep the deposit record true |
| 0a361f7 |  | fix: report a removed problem link, count line breaks once, and trim as the server does |
| 8f4dd2e |  | fix: never publish a fresh draft as a new version, and say only what the client knows |
| 6be268d |  | docs: add the math-conjecturer skill and document the math commands |
| 6b42fbe |  | docs: describe removed problem links, the read-back, and the text limits |
| 2456aa4 |  | docs: quote the final messages and describe the final recovery rules |
| c32a5a4 |  | fix: record a Zenodo answer only for the deposition that the request named |
| f0ab97e |  | fix: read back a completed receipt that has no DOI |
| 12d6fb8 |  | Release 0.51.0 |
| 0f0da44 |  | fix: give a read-back publication its own receipt |
| 7a5f5af |  | fix: keep read-back receipts in their own record kind |
| acf4258 |  | docs: state the server's refusal status and the reach of each recovery |
| 3984eec |  | docs: describe only what every answer message says |
| 933abfb | integration (tests) | test: read the frozen 0.50.1 depth reports under the Version 7 distribution |
| d151dae | merge (main 0.51.0; the 0.51.0 commits are the Mathematics release's own record) | Merge main (0.51.0, the Mathematics release) into feat/tools-orchestration |
| d0d8421 | M1 fix (round 2 part 3) | fix: resolve normalization aliases in the shared probe-origin resolver |
| 6bc455b | merge | Merge impl/m1-math-solver: normalization aliases in the shared resolver |
| 98d1134 | M1 fix (round 2 part 4) | fix: make originals durable before an export commits their aliases |
| 743579c | merge | Merge impl/m1-math-solver: originals durable before their aliases |
| c81e636 | M1 fix (round 2 part 5) | fix: publish a normalization chain in the order of its provenance |
| a9e351b | merge | Merge impl/m1-math-solver: normalization chains published in provenance order |
| 2685a16 | M1 fix (round 2 part 6) | fix: export either member of a canonical normalization cycle |
| 1bec1d9 | merge | Merge impl/m1-math-solver: both members of a canonical cycle exportable |
| 8b714b8 | M1 fix (round 2 part 7) | fix: schedule an export closure so every member keeps its readable originals |
| dcba4e3 | merge | Merge impl/m1-math-solver: one publication order for every export closure |
| the release commit | release | Release 0.52.0 |

## Conflicts resolved at the merges

- M1 into the integration branch (b00cb46): `review_protocol._prompt_matches`, both rules kept.
- S2a into the integration branch (1eba1ad): `research_harness/execution_evidence.py`, S1's probe
  observation functions kept with S2a's `author_readiness_state` signature.
- The integration branch into the S3 branch (14faaed): `docs/research-cli.md` keeps S1's sections with
  S3's heading; `docs/research-workflow.md` keeps M1's observed native review paragraphs.
- The other merges applied without conflicts.

## RED and GREEN evidence

RED: CI run 38884411457 on d03a04f failed 60 tests (the managed integration fixture decided without
the direction minimum); CI run 38916674383 on 45b1b4c failed one test (a workflow example that M1 removed);
the first S2a history crashed `status` on 2402.14112 (`KeyError: 'prerequisites'`), the second took more
than 29 minutes of CPU on it, the third 2.55 times the 0.50.1 time; part 1 of the merged-branch run on
9b2fda9 failed two S2a tests at the S2b seam (`research_decision_required`), and Sol's S3 run failed the
CLI test that replaced the standards after the direction decision. Each is fixed by a later commit below.

GREEN, as stated in the commit messages (implementer runs on Python 3.9.6 and 3.12.8, Claude's runs on
3.12.8):

- test: end probe test programs after 60 seconds (bounded-probe-test-waits.txt): GREEN (Claude, Python 3.12.8): test_lab.TestProbe 16 tests OK,
- test: check direct-search integrity errors in both record shapes (fix-claim-minimum-test.txt): GREEN: PYTHONPATH=.:tests python3.12 -m unittest test_research_claim_minimums:
- test: require the value-review example in the research workflow (guidance-value-review.txt): GREEN (Claude): test_research_guidance, 9 tests OK on Python 3.9.6 and
- feat: list the direction minimum before the cycle decision exists (integration-direction-gate.txt): GREEN (Claude, Python 3.12.8): test_research_gates, 13 tests OK.
- fix: keep the direction standards current under advisory changes (integration-direction-standards.txt): GREEN (Claude, Python 3.12.8): test_research_claim_minimums 15 OK; the two
- fix: require the openness minimum and keep probe provenance in math-solver (m1-openness-minimum.txt): GREEN (implementer): math-solver harness suite alone, 781 tests OK on GREEN (Claude, Python 3.12.8): math-solver harness suite alone, Ran 781
- feat: make math-solver an orchestration over shared tools with an openness gate (m1.txt): GREEN (implementer): math-solver harness suite alone, 752 tests OK on Python
- fix: enforce the probe deadline after worker loss without a retry (s1-probe-deadline.txt): GREEN (implementer): test_lab test_research_probe GREEN (Claude, Python 3.12.8): test_lab.TestProbeLifecycle
- fix: supervise probes after worker loss and keep verification's search rule (s1-review-fixes.txt): GREEN (implementer): 11 modules, 276 tests OK on Python 3.9.6 and 3.12.8. GREEN (Claude, Python 3.12.8): test_research_probe test_research_probe_lifecycle
- feat: add the depth plan, the exploratory probe and recorded search coverage (s1.txt): GREEN (implementer): 45 modules on Python 3.9.6 and 3.12.8 (sweep and targeted GREEN (Claude, Python 3.12.8): test_research_depth_plan test_research_probe
- feat: keep claims current under unrelated changes and gate the direction (s2a.txt): GREEN (implementer): the nine compatibility modules, 289 tests OK on Python GREEN (Claude, Python 3.12.8, after the shared artifact cache):
- fix: require complete inspected ranges and named images for direction coverage (s2b-complete-content.txt): GREEN (implementer): test_research_direction_sources test_research_decisions GREEN (Claude, Python 3.12.8): test_research_direction_sources, 24 tests OK.
- test: record the direction minimum before shared fixtures decide a direction (s2b-fixture-fix.txt): GREEN (implementer): 52 modules on Python 3.9.6 and 3.12.8, including
- fix: count direction review coverage only from delivered source content (s2b-review-coverage.txt): GREEN (implementer): 362 tests (decisions, protocol, sources, bar, scoped, GREEN (Claude, Python 3.12.8): test_research_direction_sources

RED after part 2: CI run 38984647915 on 693fa04 failed nine tests on every Python (eight assumed a
Version 6 distribution, one expected the pre-693fa04 standards rule); part 1 of the merged-branch run found
the fourth intermediate-composition test. GREEN:

- test: build legacy-constitution stores under the Version 6 contract (constitution-v7-tests.txt): GREEN (Claude, Python 3.12.8): test_research_principles test_research_provenance
- fix: keep exploratory provenance through packet export and resolve duplicate hits (m1-export-provenance.txt): GREEN (implementer): math-solver harness suite alone, 798 tests OK on Python GREEN (Claude, Python 3.12.8): math-solver harness suite alone, Ran 798
- fix: gate the return to ideate and bind claims to the readings they use (s2a-followup-5.txt): GREEN (implementer): test_research_gates test_research_development GREEN (Claude): the modules run on the integration branch after the merge;
- refactor: authenticate only released candidate compositions (integration-alias-cleanup.txt): GREEN (Claude, Python 3.12.8, on the merged tree with this change): three

- test: read the frozen 0.50.1 depth reports under the Version 7 distribution (depth-plan-test-v7.txt): GREEN (Claude, Python 3.12.8): test_research_depth_plan, 13 tests OK.
- fix: resolve normalization aliases in the shared probe-origin resolver (m1-shared-provenance.txt): GREEN (implementer): Python 3.12, the targeted provenance, lifecycle and GREEN (Claude, Python 3.12.8): math-solver harness suite alone, Ran 798

- fix: make originals durable before an export commits their aliases (m1-publication-order.txt): GREEN (implementer): math-solver harness suite alone, 801 tests OK on Python GREEN (Claude, Python 3.12.8): math-solver harness suite alone, Ran 801

- fix: publish a normalization chain in the order of its provenance (m1-chain-order.txt): GREEN (implementer): math-solver harness suite alone, 806 tests OK on Python GREEN (Claude, Python 3.12.8): math-solver harness suite alone, Ran 806

- fix: export either member of a canonical normalization cycle (m1-cycle-export.txt): GREEN (implementer): math-solver harness suite alone, 809 tests OK on Python GREEN (Claude, Python 3.12.8): math-solver harness suite alone, Ran 809

- fix: schedule an export closure so every member keeps its readable originals (m1-closure-order.txt): GREEN (implementer): math-solver harness suite alone, 810 tests OK on Python GREEN (Claude, Python 3.12.8): math-solver harness suite alone, Ran 810

The release commit changes the version files, the README and the
documentation only.

## CI runs on the integration branch

| Run | Head | Result |
| --- | --- | --- |
| 38884411457 | d03a04f (S2b) | 60 failures: the managed integration fixture decided without the direction minimum |
| 38916674383 | 45b1b4c | one failure: a workflow example that M1 had removed |
| 38931921552 | e901242 | success |
| 38944456570 | c83aeaa | success |
| 38959620240 | 4c9f1aa | success |
| 38984647915 | 693fa04 | nine failures on every Python: eight tests assumed the Version 6 constitution, one the pre-693fa04 standards rule |
| 38991442160 | cb8e7fb | success |
| 38995800365 | a965399 | success |
| 39004480784 | 0a963b1 | cancelled while queued (superseded; the branch runs one workflow at a time) |
| 39006114623 | d151dae | cancelled in progress (superseded) |
| 39007951874 | 6bc455b | cancelled while queued (superseded) |
| 39011336575 | 743579c | cancelled in progress (superseded) |
| 39014819732 | a9e351b | cancelled in progress (superseded) |
| 39017864405 | 1bec1d9 | cancelled in progress (superseded) |
| 39021383513 | dcba4e3 | cancelled while queued (superseded by the release commit) |
| the pull request's run | the release head | the final run, on the pull request page |

## Runs on recorded stores

`bin/compare_upgrade.py` in the context folder clones a recorded store with
APFS copy-on-write (`cp -cR`), runs `exactory-research status` on the clone
with one plugin checkout, and saves the report, the wall time and the peak
resident memory (`/usr/bin/time -l`). Fourteen local stores were read with
release 0.50.1 (worktree `baseline-0.50.1` at 22849b0) and with the S2a slice:
nine studies (stages `cohort` to `complete`, 2 MB to 4.1 GB), four
verifications and one test store. The first S2a version crashed on one store
(a `cycle_assessment` report without `prerequisites`, written by a release
before 0.4x) and used 20 to 30 percent more memory; the second version took
more than 29 minutes of CPU on one store, because every historical record read
decompressed and parsed its version again. The third version finished every
store; the fourth version computes each recorded revision once, and the
released code adds the direction minimum before a cycle decision, the shared
artifact cache and the reading projection.

The comparison of the final integration head 0a963b1 (label final1, 2026-10-16 22:03 to 22:35) with
release 0.50.1 (label base, 13:06) is `test-logs/upgrade-compare-base-final1.json` in the context folder:

| Store | 0.50.1 s | 0.52.0 s | 0.50.1 MiB | 0.52.0 MiB | Report |
| --- | ---: | ---: | ---: | ---: | --- |
| exactory-research-research-cli-moz3brnm | 0.3 | 0.2 |  | 32 | identical |
| lineage-acceptance-author | 8.4 | 7.1 |  | 751 | gone: candidate_checkpoint_missing, synthesis_dependencies_stale; new: direction_reading_missing |
| lineage-acceptance-verify | 35.2 | 34.3 |  | 961 | identical |
| storage-replay-performance001-evidence-active-fixture | 10.6 | 10.1 |  | 667 | identical |
| studies-2402.14112 | 142.0 | 178.2 |  | 10359 | gone: manuscript_readiness_required, publication_scope_stale; new: publication_readiness_stale; next: constitution_revalidation_required to research_intent_missing |
| studies-2409.17337 | 246.1 | 291.8 | 12834 | 13831 | gone: candidate_checkpoint_stale, development_dependencies_stale, inheritance_refresh_stale, inherited_result_stale, novelty_comparison_stale, readiness_review_stale, synthesis_dependencies_stale; new: claim_reassessment_required; count changed: branch_resolution_unverified 14 to 2 |
| studies-2601.01283 | 80.4 | 91.1 | 6831 | 6967 | gone: branch_resolution_unverified, candidate_checkpoint_stale, development_dependencies_stale, inherited_result_stale, readiness_review_stale, synthesis_dependencies_stale |
| studies-2607.29015 | 11.0 | 12.6 | 581 | 622 | gone: branch_resolution_unverified, candidate_checkpoint_stale, development_dependencies_stale, inherited_result_stale, novelty_comparison_stale, readiness_review_stale, synthesis_dependencies_stale; new: claim_reassessment_required |
| studies-closed-gravity-internal-measurements | 21.5 | 22.3 |  | 2297 | gone: abstract_reading_missing, candidate_checkpoint_missing, reference_unresolved, search_dispositions_missing, search_evidence_stale, search_frontier_stale, synthesis_dependencies_stale; new: direction_search_incomplete, direction_standards_unresolved |
| studies-closed-gravity-operational-reconstruction-20260913 | 12.3 | 10.4 |  | 484 | gone: candidate_checkpoint_missing, cohort_missing, context_missing, innovation_missing, rationale_missing, roots_missing, search_purpose_missing, standards_missing; new: direction_search_missing, direction_standards_missing; next: None to constitution_revalidation_required |
| studies-haar-purity-optimal-designs | 38.5 | 38.7 |  | 5743 | gone: grand_challenge_missing, synthesis_dependencies_stale |
| verify-2607.02120 | 11.1 | 6.2 |  | 303 | identical |
| verify-zenodo-22773593 | 31.5 | 33.7 |  | 994 | identical |
| verify-zenodo-22920879 | 10.9 | 6.5 |  | 263 | identical |

The 0.50.1 peak memory of the first runs was not recorded for five small stores
(the first version of the script did not keep it). After the shared artifact
cache, the pairs measured back to back on fresh clones of 2402.14112 were:
0.50.1 155.5 s and 8560 MiB against 204.7 s and 9525 MiB before the shared
cache (1.32 times, 1.11 times); 0.50.1 170.5 s and 8672 MiB against 233.3 s
and 8923 MiB after it (1.37 times, 1.03 times). The machine ran another
session's test suites during the later pairs, and 0.50.1 alone varied from
142 s to 180 s. On 2409.17337 the pair was 278.9 s and 12916 MiB against
322.1 s and 13320 MiB (1.15 times, 1.03 times), on 2601.01283 87.9 s and
6411 MiB against 104.9 s and 6188 MiB (1.19 times, 0.97 times).

## Live checks of the shared tools

In a new study workspace (`exactory-lab init`) with the integration branch,
before any target or cycle existed:

- `exactory-lab probe` ran a script and recorded one `probe` outcome (exit
  status 0, stdout pinned, 0.17 s charged to the experiment wall-time budget).
  The same command again returned the original receipt without a second run.
- `exactory-research depth` recorded a plan with `loop_abstracts` 150 and
  `hits_per_query` 15 and the defaults for the other amounts. A second plan
  with the same `id` failed with `record_conflict`.
- A captured empty tool response and a `search` with a `question` recorded a
  provisional search: `provisional: true`, `scope_digest: null`, one coverage
  entry of kind `bounded_unordered` with `requested_k` 15 from the depth plan,
  no integrity error. `status` listed it under `provisional_searches`.
