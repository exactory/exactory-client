# Problem creation (0.53.0): test record

Source design: `misc/tools-and-orchestration/DESIGN.md` section 12 in the
exactory repository, with the review record `REVIEW.md` in the same folder.
Astra (gpt-6-astra at effort xhigh) confirmed the implementation prompt in
five rounds (MSG-019-ASTRA to MSG-023-ASTRA) before the implementer started.
Implementer: gpt-6.1-sol at effort xhigh, one pass. Reviewer of the pass:
Claude. Code review: Astra, three rounds (MSG-024-ASTRA, MSG-025-ASTRA,
MSG-026-ASTRA).

## Journeys

1. As a mathematician, I want a research record that holds the claim that a
   problem is precisely stated, open and worth solving, with its evidence and
   its reviews, before I post the problem to the Mathematics section.
2. As a mathematician, I want the openness finding to come from a native
   reviewer who saw this record's own searches and readings, and to go stale
   when I change the statement, declare a neighbour, or add a material search.
3. As a mathematician, I want every motivation and current-state paragraph
   bound to a full reading or to a computation that was admitted, reconciled
   and assessed, and I want a requested formal check bound to the completion
   that math-solver delivered.
4. As a reviewer, I want to judge the value of a problem in its field before I
   see the statement, and to judge the statement with the evidence afterwards,
   and I want an author of the record kept out of both roles.
5. As a mathematician, I want the posted parts to be exactly the parts that the
   record exported, and one post per statement.
6. As an author of an existing study, draft, verification or math objective, I
   want my record and its reports unchanged by this release.

## Commits

| Commit | Subject |
| --- | --- |
| 421341f | Add problem creation as an orchestration |
| 1c8e174 | Refuse a targeted store for problem-init and narrow the problem status limits |
| 98b84fa | Normalize a workspace path before the parent walk |
| 4e84433 | Follow the filesystem for a parent segment in a workspace path |
| 74fd32b | Block a problem post on material objections and pin the statement area |
| 52b07a4 | Let a problem adjudication use the current evidence |

The release commit changes the version files, the README and the
documentation only.

## RED and GREEN evidence

The implementer wrote each test before the code it checks, under the confirmed
prompt (`prompts/sol-m2-math-conjecturer.md`, Part 5: required tests). Its own
final review reproduced five defects with isolated stores and repaired each one
with a test: an openness packet whose subject differed from the selected
statement was accepted; a cited computation with an unplanned result scope gave
support; one formal completion cleared a different formal request on the same
output; revised value evidence could not obtain refreshed results from the
original reviewers; a fully read work with two captured main originals could
not be cited. A sixth repair fixed the assessment of a cited computation at the
value's recorded revision. The notes are in
`misc/tools-and-orchestration/sol-notes/m2-first-pass/`.

Claude's live check of 421341f in new stores (see below) found two defects.
`problem-init` accepted a research store whose configuration already had a
research target, and kept the target. A workspace path with a parent segment
(`../other`) resolved to the store of the directory that the segment passes
through. Commit 1c8e174 fixes the first and removes the cohort policy counts of
ai-science from the status report of a problem record; commit 98b84fa fixes the
second. Each change has a test.

Astra's code review round 1 (MSG-024-ASTRA) found six defects in
e9300a9..98b84fa. A recorded review objection never blocked the post decision,
the export or the post. A statement could carry another `math.*` area than the
record whose bars were established for it. An observed `unresolved` finding
with a null minimum was recorded and then broke `status` with an exception.
The openness export hashed recomputed readings instead of the stored reading
records, so a valid reading recorded by an earlier release failed the audit.
The lexical normalization of 98b84fa ignored a symlink before the parent
segment. Several new functions had names without a verb. Commit 4e84433 fixes
the symlink case (a parent segment follows the filesystem; the final component
stays as supplied). Commit 74fd32b fixes the other five: the objections of
both bars and both result histories block the decision, the export and the
post until both result reviewers resolve them with evidence or a focused
adjudication finds them not upheld; the statement's area must equal the
record's area; a supplied minimum passes the shared schema for every outcome;
the export hashes the stored reading records; the new functions carry a verb.
Each change has a test (41 regressions in 74fd32b).

Round 2 (MSG-025-ASTRA) found three defects in the new adjudication path: a
problem adjudication could use only the evidence that the original pair saw; an
adjudication whose response had become unavailable blocked a post that both
result reviewers had resolved with evidence; the bar repair prompt commanded an
empty objection list while the recording contract required the inherited
objection. Commit 52b07a4 fixes them with 11 regressions: a problem
adjudication has its own prompt and names the current delivered evidence, a
correction still needs an error in the original context, an evidenced
resolution by both reviewers suffices, and the repair prompt requires the
inherited objections.

GREEN, Claude's runs (`misc/tools-and-orchestration/test-logs/m2-*`):

- The eleven new modules `tests/test_research_problem_*.py` at 421341f: 132
  tests OK on Python 3.9.6 and 3.12.8.
- The math-solver harness suite alone (`skills/math-solver/harness/tests`,
  with the two new modules): 823 tests OK on Python 3.9.6 and 3.12.8.
- The modules that import a changed production module: 551 tests OK on Python
  3.12.8.
- The eight modules that use the renamed helpers (`deliver_packet`,
  `collect_references`, `has_attachment`): 97 tests OK on Python 3.12.8.
- After the two fixes, at 98b84fa: the eleven problem modules with the two new
  tests, `test_workspace_init_retry`, `test_research_depth_plan`,
  `test_research_report_views` and `test_research_principles`: 195 tests OK on
  Python 3.9.6 and 3.12.8. The eleven modules that read the `limits` block of
  an ai-science store (`test_research_cli`, resources, source deferrals, lab,
  probe, probe lifecycle, development, literature, synthesis, rounds, OAI
  cohort): 408 tests OK on Python 3.12.8.
- At 74fd32b: the 13 problem modules with decisions, review protocol, scoped
  review protocol, reading, cli, workspace, development and execution: 472
  tests OK on Python 3.12.8; the math-solver harness suite alone: 823 tests OK
  on Python 3.12.8; the 13 problem modules: 175 tests OK on Python 3.9.6.
- At 52b07a4: the 15 problem modules with decisions, review protocol, scoped
  review protocol, review variants, cli and workspace: 327 tests OK on Python
  3.12.8; the 15 problem modules: 186 tests OK on Python 3.9.6. The
  implementer's 45-module selection: 613 tests OK on Python 3.9.6 and 3.12.8.

The implementer's own runs agree on both Pythons (132, 823, and a 79-module
compatibility selection of 1,622 cases with 1,575 passed; the 47 other cases
failed in its sandbox before any application code ran, on a loopback bind or on
the designated research directory, and passed in Claude's runs).

## CI runs

| Run | Head | Result |
| --- | --- | --- |
| the pull request's run | the release head | the final run, on the pull request page |

## Runs on recorded stores

`bin/compare_upgrade.py` in the context folder clones a recorded store with
APFS copy-on-write (`cp -cR`), runs `exactory-research status` on the clone
with one plugin checkout, and saves the report, the wall time and the peak
resident memory (`/usr/bin/time -l`). The same fourteen local stores as for
0.52.0 were read with release 0.52.1 (worktree `tools-orchestration` at
fb2a9bb) and then with 421341f, back to back on fresh clones under the same
load (17:49 to 18:25 PDT on 2026-10-17;
`test-logs/upgrade-compare-base0521-m2a.json`). "Identical" means that the
two reports are the same bytes except the `runtime` block, which names the
checkout (executable path, package digest, source commit).

| Store | 0.52.1 s | 421341f s | 0.52.1 MiB | 421341f MiB | Report |
| --- | ---: | ---: | ---: | ---: | --- |
| exactory-research-research-cli-moz3brnm | 0.2 | 0.2 | 24 | 24 | identical |
| lineage-acceptance-author | 7.8 | 7.8 | 765 | 769 | identical |
| lineage-acceptance-verify | 38.1 | 38.0 | 1049 | 1051 | identical |
| storage-replay-performance001-evidence-active-fixture | 13.5 | 13.5 | 687 | 686 | identical |
| studies-2402.14112 | 218.0 | 219.0 | 8685 | 7614 | identical |
| studies-2409.17337 | 355.8 | 358.9 | 11479 | 11466 | identical |
| studies-2601.01283 | 112.1 | 111.7 | 6024 | 6035 | identical |
| studies-2607.29015 | 14.4 | 15.2 | 578 | 581 | identical |
| studies-closed-gravity-internal-measurements | 23.0 | 22.8 | 2294 | 2295 | identical |
| studies-closed-gravity-operational-reconstruction-20260913 | 11.1 | 11.2 | 562 | 560 | identical |
| studies-haar-purity-optimal-designs | 46.4 | 46.2 | 4524 | 4386 | identical |
| verify-2607.02120 | 7.2 | 7.7 | 329 | 327 | identical |
| verify-zenodo-22773593 | 36.6 | 37.1 | 1082 | 1043 | identical |
| verify-zenodo-22920879 | 7.2 | 6.9 | 271 | 274 | identical |

The two small stores above 1.05 times were measured three more times each,
alternating the two checkouts (`test-logs/upgrade-small-repeats.log`). The
medians were 14.5 s against 14.9 s for 2607.29015 and 7.3 s against 7.1 s for
2607.02120; one run of each checkout was an outlier (10.8 s with 0.52.1, 21.8
s with 421341f).

A second run of the fourteen stores with 74fd32b (the review fixes; 20:40 to
20:57 PDT on fresh clones; `test-logs/upgrade-compare-base0521-m2b.json`)
gave the same result: every report identical except the `runtime` block, and
0.99 to 1.03 times the 0.52.1 wall time (2402.14112 220.5 s, 2409.17337
355.6 s, 2601.01283 111.4 s), with equal or lower peak memory. The later commits change the problem
adjudication path and the problem prompts only.

The problem record is a new marker that no existing store holds. For an
existing store the only added work is one marker lookup before the contract
check of a gate.

## Live checks of the problem commands

In new stores under `exactory-research/harness-dev/` with the committed tree
(`test-logs/m2-live-check.log`):

- `problem-init` with the example payload created the marker
  (`orchestration: problem_creation`, immutable `authors`), adopted the
  constitution (Version 7) and set a null target. `status` and `next` reported
  the milestone `exploration` and the next condition
  `problem_statement_missing`; `gate post` listed the six post obligations.
- `gate direction` in the problem store reported `gate_inapplicable`.
- `problem-statement` with the example payload moved the milestone to
  `formulation` and the next condition to `problem_openness_missing`. The same
  request repeated with the same request id returned the original receipt.
- `export --kind problem` before readiness returned `readiness_required` with
  the post obligations.
- A second `problem-init` in the same store, and `intent` in the problem
  store, returned `orchestration_conflict`.
- In a study workspace created by `exactory-lab init`, `problem-init` returned
  `orchestration_conflict` and `gate post` reported `gate_inapplicable`.
- At 98b84fa: `problem-init` in a store with a research target returned
  `orchestration_conflict`; `status` of the problem record listed
  `limits: {"depth": null}`; `status --workspace ../missing` from a store
  directory returned `migration_required` instead of that store's report, and
  `../lab` resolved to the lab workspace.
