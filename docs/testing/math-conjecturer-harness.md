# The research half of math-conjecturer (0.55.0): test record

Source design: `misc/math-conjecturer/DESIGN.md` 0.4 and `RUBRIC.md` 0.2 in the
exactory repository, co-authored and confirmed with Astra (gpt-6-astra at
effort xhigh, MSG-005-ASTRA to MSG-007-ASTRA, `REVIEW.md` in the same folder);
the user's decisions in `GOAL.md` 0.5 section 6 and `DESIGN.md` section 9. The
implementation plan is `PLAN.md` (superpowers, six tasks). Implementers:
gpt-6.1-sol at effort xhigh (Tasks 1, 3, 4, 4b; fix rounds at high or xhigh)
and Claude Opus 5.5 at xhigh (Task 2) and high (Task 5). Each task was reviewed
by a Claude Opus 5.5 task reviewer (high) and each fix round by a scoped
re-reviewer (medium); the controller (Claude Fable 5.1) read every production
diff, ran the tests independently and recorded the rulings R1 to R14 in the SDD
ledger. Code review of the whole branch: Astra, in five rounds. MSG-009-ASTRA
and MSG-010-ASTRA ran on gpt-6-astra at effort xhigh through the Codex
companion; MSG-011-ASTRA, MSG-012-ASTRA and MSG-013-ASTRA ran at effort max
through the Codex CLI on a fork of the design thread, at the user's
instruction, with the settings verified from the session record. Each round
found gaps that a fix wave closed (MSG-009: eleven findings, wave 1, rulings
R15 to R25; MSG-010: N1 to N3, wave 2, R27 to R30; MSG-011: A1 to A4 and the
text findings B1 to B10, wave 3 and the controller's text pass, R31 to R33;
MSG-012: N1 to N5, wave 4 with three fix rounds, R34 to R40; MSG-013: F1, a
version citing a typed record reference delivered no evidence to the
adjudicator and the coverage gate accepted it, and F2, the adjudicator
prompt's wording, wave 5 with three fix rounds and the controller's wording,
R41 to R45; MSG-014: F3, the adjudicator's calibrations let the packet attach
a passage-only source's entire original, F4, the evidence grammar invited
record citations that the record-time rule refused, and F5, review payloads
carried no copyable record digest, wave 6 and the controller's wording, R46
to R48). After the user's directive of 2026-10-19 (ruling R26) the controller
wrote every text an AI reads directly: the version 2 reviewer prompts and
response contracts, the rubric anchors, the obligation explanations, the
research half of the `math-conjecturer` skill and the documentation sections
it points to. MSG-015-ASTRA (round 7, on the merge of wave 6) closed F3 to
F5 and found four more Important defects in the delivery of cited content
(an attachment lost through a second citation; an out-of-bounds source
citation accepted; an uncitable mapping locator; a first slate asked for a
finding on an undelivered historical bar objection), fixed in wave 7 under
R54 to R58. The user then ended the review loop and ordered the release
(R59), so Astra's confirmation of wave 7 was not obtained; the controller
reviewed the wave 7 diff itself (the SDD ledger holds the reading) and ran
the release checks below at the release head.

## Journeys

1. As a mathematician, I want an exploration record that holds the candidate
   questions I found, their sources, their provisional grades and the
   comparison that selected some of them, so that a posted problem traces back
   to a decision among alternatives, bound to two blind field bars.
2. As a mathematician, I want the harness to attempt the exact statement, with
   math-solver under its normal limits or by a critical full reading of an
   existing attempt, before it calls the problem open, and I want a resolved
   attempt to leave the problem path.
3. As a mathematician, I want the harness to grade its own problem against the
   rubric with an evidence reference per item, and I want the post blocked
   until the self-assessment and both result reviews meet the thresholds, or a
   focused adjudication resolves a below-threshold grade.
4. As a reviewer, I want the evidence I grade delivered to me: the attempt's
   outcomes, the literature binding and its readings, the accepted results of
   the solver attempt with their claims.
5. As a mathematician, I want one exploration to post several problems, one
   selected candidate after another, with fresh evidence and reviews for each.
6. As a math-solver user, I want to deliver the current attempt of an
   objective, resolved or not, into the problem record's store.
7. As a user of the Mathematics section, I want the section operations in a
   skill of their own (`math-section`) and the creation of problems in
   `math-conjecturer`, with the posting text unchanged.
8. As an author of an existing study, draft, verification or math objective, I
   want my record and its reports unchanged by this release.

## Commits

| Commit | Subject |
| --- | --- |
| 1105039 | Add search deliver-attempt for the native attempt record |
| b07f927 | Name the attempt summary builder and export historical usage |
| e6c9c99 | Add the rubric self-assessment, review templates v2 and the rubric gate |
| a448e11 | Merge branch 'impl/mc-deliver-attempt' into impl/math-conjecturer-harness |
| 5e05332 | Derive the problem template ids and build the rubric objection in one place |
| 377d8f3 | Add problem candidates, the slate and the selection decision |
| 1e839fd | Report stale slates and refuse malformed candidate ids |
| 110ed3b | Add the problem attempt record and accepted results as value evidence |
| fec05a1 | Read the native resolution before closure and deliver the attempt evidence |
| 605df39 | Split the math skills and write the research half of math-conjecturer |
| 8b3d411 | Apply the Task 5 review: reviewers, slate order and rubric text |
| 68b9501 | Name one result review per bar reviewer and the causes of a stale finding |
| 43cfae1 | Test a second post from one problem workspace |
| 73ed69b | Bind slate reviews to the post path and calibrate bars on version 2 |
| 9941176 | Track native snapshots, bind applicability and deliver the assessment evidence |
| 6bac67b | Refresh slate reviews on changed bars and keep current slate objections |
| 695225a | Key the snapshot succession on the attack root and let the deliverer name the current snapshot |
| ac7a365 | Merge branch 'impl/mc-attempt' into impl/math-conjecturer-harness |
| 52f15aa | State the named scope before the objective runs and which digest to bind |
| e3ae4f8 | Bind slate objections to candidates and deliver calibrated bars to results |
| c7d9460 | Keep slate objections along candidate lineages and deliver slot bars |
| 797ec10 | Rewrite the version 2 problem reviewer prompts |
| b9161f7 | Rewrite the research half of the math-conjecturer skill and the texts an agent reads |
| 40c347e | Anchor the rubric texts and correct the reviewer prompts and the skill |
| e616cc3 | Version material objections and deliver the review standards and openness |
| 5c75dd5 | Refresh the fixed adjudicator on a changed objection version |
| a12057d | Merge branch 'impl/mc-attempt' into impl/math-conjecturer-harness |
| 6b45c80 | Read the rubric anchors directly in the adjudicator packet |
| 1511c3c | Give each problem reviewer role its own response contract |
| 3d942b0 | Name the delivered version and the original calibrations in the adjudicator prompt |
| a7fa044 | Tell a refreshed bar to give findings on its earlier objections |
| b2c0c1e | Pin the bar contract's refresh rule in the prompt text test |
| 2c6b4f3 | Version finding updates and deliver original standards to adjudicators |
| 1eeba0f | Hold versions in the latest review of a lineage and narrow adjudicator refresh |
| bc60426 | Make a refreshed bar give findings on its earlier objections |
| 34c9192 | Deliver the lineage reviews in bar continuation packets |
| 0f4a82c | Merge branch 'impl/mc-attempt' into impl/math-conjecturer-harness |
| 8dc4a39 | Promise the version keys to the adjudicator for a material objection only |
| 4a6e966 | Deliver cited records in version evidence and name undelivered references |
| 49de923 | Deliver a cited record as the projection its stating reviewer received |
| 71eb508 | Describe every version_evidence entry kind to the adjudicator |
| 0f0b71f | Expose record digests in problem packets and refuse undelivered citations |
| 7770064 | Check cited records after the objections are validated |
| ac461a6 | Merge branch 'impl/mc-attempt' into impl/math-conjecturer-harness |
| 2c253f7 | Let reviewers cite only records delivered to them as content |
| 06a3395 | Bound calibration bytes, expose review references, deliver objection evidence |
| 5ef4e77 | Deliver every review payload and objection with templated references |
| 2f5a7b6 | Tell reviewers how passages and templates are cited |
| 0dfe0a7 | Say which objections reach a slate reviewer with their content |
| 5965346 | Deliver objection passages and historical objection evidence |
| 036bd8d | Refuse artifact citations that no packet of the review delivered |
| d73c29d | Merge branch 'impl/mc-attempt' into impl/math-conjecturer-harness |
| aa3fe35 | Tell reviewers to cite a passage by the citation given with its mapping |
| 86073e4 | Say that a mapping without a citation is for reading only |
| b20965c | Keep record attachments, bound source citations, cite passages, deliver historical bars |
| ce47c39 | Offer a passage citation only where the receiving role can record it |
| 4038d9b | Merge branch 'impl/mc-attempt' into impl/math-conjecturer-harness |

The release commit on top of 4038d9b changes the version files, the README
and the documentation only.

## RED and GREEN evidence

Every implementer wrote the tests of its brief before the code and kept the
failing run (the logs are under `.superpowers/sdd/PLAN/` of the worktrees and
copied to `misc/math-conjecturer/test-logs/` where the controller ran them):

- Task 1 (the rubric records, templates version 2, the rubric gate): Sol's
  RED runs `task-1-review-red.log` and the per-step logs; Sol's final runs 985
  tests per Python version. The task review found seven minors; five were
  fixed in one round (5e05332) and two accepted with a reason.
- Task 2 (candidates, the slate, the selection decision): RED
  `task-2-candidates-red-python-3.12.log` (ImportError before the module
  existed) and `task-2-slate-red-python-3.12.log` (fifteen errors and two
  failures before `record_slate`); the continuation test fails on a mutant
  (`task-2-continuation-mutant-python-3.12.log`). Opus's final runs: 1002
  tests on 3.12 and 3.9. The task review found one Important defect (a list or
  object as `previous` raised an uncaught TypeError) and five minors; the fix
  round (1e839fd) also added `problem_slate_stale` (ruling R7); its RED log is
  `task-2-fix-1-red-python-3.12.log`.
- Task 3 (`search deliver-attempt`): Sol's RED and GREEN runs of the thirteen
  tests in worktree B; the task review found four minors, three fixed in one
  round (b07f927).
- Task 4 (the attempt record, accepted results as value evidence): RED
  `task-4-attempt-red.log`, `task-4-results-red.log`,
  `task-4-empty-coverage-red.log` (Sol's own self-review found that an omitted
  coverage key compared equal to null); the fix round's RED
  `task-4-fix1-red.log` and `task-4-fix1-readings-red.log` (resolution before
  closure, attempt readings delivered; rulings R10 and R11). Sol's final runs
  262 problem tests and 140 related tests per Python version.
- Task 5 (the skill split, the research half of the skill text): RED
  `task-5-text-red.log` (eight failures and one error on the previous text).
  The task review found two Important text defects (the result reviewers must
  be the two bar reviewers; the focused slate review precedes the slate that
  cites it) and eleven minors; the fix round (8b3d411) and a text fix by the
  controller (68b9501) applied them; ruling R12 reworded the version 2 result
  prompt and its pinned hash.
- Task 4b (a second post from one workspace): the first run stopped at the
  controller's wrong expectation (`problem_attempt_missing` where the gate
  reports `problem_attempt_stale` for the previous statement's attempt, ruling
  R14); the continuation ran the second chain to the post and refused reused
  result reviews with `problem_review_pair_required`.

GREEN, the controller's runs (`misc/math-conjecturer/test-logs/`):

- At b07f927 (Task 3): the math-solver harness suite alone, 835 tests OK on
  Python 3.12.8; the thirteen delivery tests OK on 3.9.6 and 3.12.8.
- At a448e11 and 5e05332 (Task 1 and its fix): the review, decision, gate and
  guidance modules 193 OK on 3.12; the seventeen problem modules 220 OK on 3.9
  and on 3.12; six modules 103 OK on 3.12 after the fix.
- At 377d8f3 (Task 2): the problem, review, decision, gate, report-view,
  guidance and CLI modules 398 OK on 3.9; the 67-module set 1002 OK on 3.12.
  At 1e839fd (its fix): the nine affected modules 131 OK on 3.9 and on 3.12.
- At 110ed3b (Task 4): 21 problem modules and 8 related modules 421 OK on 3.9.
  At fec05a1 (its fix): 423 tests with one failure in `test_research_guidance`
  caused by the Task 5 implementer creating `skills/math-section` in the same
  worktree during the run (fec05a1 has no such directory); the module passed
  at the Task 5 commit.
- At 605df39 (Task 5): the text, manifest, Codex, guidance, math-problems CLI
  and problem CLI modules 238 OK on 3.12 and on 3.9. At 8b3d411: the eight
  text and review modules 155 OK on both. At 68b9501: the text, guidance and
  Codex modules 53 OK on both. `codex/generate.py --check` exit 0 at each.
- At 43cfae1 (the branch head before Astra's first round): the math-solver
  harness suite alone, 836 tests OK on 3.12 (`final-43cfae1-harness-312.log`).
  The 73-module runs started at 43cfae1, 52f15aa, b9161f7 and 6b45c80 were
  stopped when the next fix wave moved the head (their logs hold the progress
  dots only); the harness suite alone completed at b9161f7 and 6b45c80 with 840
  tests OK on 3.12. The complete runs of the release are the ones below.
- The fix waves: each implementer ran the problem, review, decision, gate,
  CLI, report-view, guidance and Codex modules (31 modules) on both Pythons
  before its commit (wave 1, Opus: 464 after its fix round; wave 1, Sol: 413
  research and 839 harness tests, then 417 and 840 after its fix round; wave
  2: 482 and 488 after its fix round; wave 3: 497 and 499; wave 4: 506, 511
  and 517 after its two fix rounds), and each task reviewer and re-reviewer
  ran the affected modules again. The controller's merged-head run at 6b45c80 (after wave 3): 507 OK
  on 3.12 and 507 OK on 3.9 over 34 modules
  (`merged-a12057d-affected-both.log`). The controller's text passes: 52f15aa
  (53 text, guidance and Codex tests on 3.12), 797ec10 and b9161f7 (17 modules,
  298 OK on each Python), 40c347e (18 modules, 307 OK on each), 1511c3c (the
  four review modules, 93 OK on each), 3d942b0 (the three review modules OK on
  each), a7fa044 (six review, guidance and Codex modules, 131 OK on each),
  b2c0c1e (the review module, 48 OK on each), 8dc4a39 and 71eb508 (the review
  and the two adjudication modules, 62 OK on each), 2c253f7 and 2f5a7b6 (five
  review modules, 116 OK on each), 0dfe0a7 (the review and slate modules,
  73 OK on each), aa3fe35 and 86073e4 (five review modules, 127 OK on
  each); `codex/generate.py --check` exit 0 at each. At d73c29d (the merge of wave
  6) the 34 modules: 723 OK on 3.12 and 723 OK on 3.9
  (`merged-d73c29d-affected-both.log`). The controller's own packet audit at 5ef4e77 (a scratch probe over
  the adjudication fixtures listing every JSON path at which an artifact
  descriptor sits, since the packet attaches every descriptor in full) found
  that every packet attaches bytes only through its explicit paths and that
  the unread tail of a passage-only source reaches no packet, and found the
  gap that the wave 6 review also found (a material objection's passage-only
  citation reaching the result reviewers as nothing; ruling R50). At
  0f4a82c (the merge of wave 4) the controller ran the 34 problem and related
  modules (`merged-modules.txt`): 702 OK on 3.12 and 702 OK on 3.9
  (`merged-0f4a82c-affected-both.log`); at ac461a6 (the merge of wave 5): 712
  OK on 3.12 and 712 OK on 3.9 (`merged-ac461a6-affected-both.log`).
- At the release tree (4038d9b with the release files, 2026-10-19 23:05 PDT
  onwards): the math-solver harness suite alone, 840 tests OK on 3.12
  (1007 s) and 840 OK on 3.9 (960 s) (`final-4038d9b-harness-both.log`); the
  73 release modules (`final-modules.txt`) on 3.12 and 3.9
  (`final-4038d9b-312.log`, `final-4038d9b-39.log`), started at 23:05 and
  still running when this record was committed at the user's instruction
  to open the pull request; their results are in the SDD ledger of the
  context folder and in the pull request's conversation, and the pull
  request's own run covers Python 3.9, 3.12 and 3.14.

## CI runs

| Run | Head | Result |
| --- | --- | --- |
| the pull request's run | the release commit | the pull request's checks (19 jobs: Python 3.9, 3.12 and 3.14 shards, the remaining checks, the summary and the immutable test sources job); the merge waited for them |
| the post-merge runs | the merge commit on main, dev, local-dev and the tag | recorded in the SDD ledger of the context folder |

## Runs on recorded stores

Journey 8 is checked the way the 0.52.0 release checked it:
`bin/compare_upgrade.py` of `misc/tools-and-orchestration/` in the exactory
repository clones a recorded store with APFS copy-on-write, runs
`exactory-research status` on the clone with one plugin checkout, and saves
the report, the wall time and the peak resident memory. The thirteen local
stores of the 0.52.0 comparison (nine studies, two verifications, one lineage
acceptance pair and one test store) were read with release 0.54.0 (worktree
`baseline-0.54.0` at 26b52fc, label base0540, 14:55 to 15:13 PDT), with the
branch at 8dc4a39 (label mc8dc4a39, 15:13 to 15:30, every report identical,
`test-logs/upgrade-compare-base0540-mc8dc4a39.json`), with the branch at
ac461a6, the merge of wave 5 (label mcac461a6, 17:41 to 17:58, every report
identical, time and memory within 1.03x,
`test-logs/upgrade-compare-base0540-mcac461a6.json`), with the branch at
d73c29d, the merge of wave 6 (label mcd73c29d, 21:45 to 22:02, every report
identical, `test-logs/upgrade-compare-base0540-mcd73c29d.json`), and with
the release tree at 4038d9b, the merge of wave 7 (label mc4038d9b, 23:05 to
23:22, while the 73-module runs and the harness suite shared the machine).
The comparison at the release tree is
`test-logs/upgrade-compare-base0540-mc4038d9b.json` in the context folder:

| Store | 0.54.0 s | release s | 0.54.0 MiB | release MiB | Report |
| --- | ---: | ---: | ---: | ---: | --- |
| exactory-research-research-cli-moz3brnm | 1.7 | 0.2 | 32 | 24 | identical |
| lineage-acceptance-author | 7.5 | 6.8 | 769 | 772 | identical |
| lineage-acceptance-verify | 29.7 | 33.6 | 1043 | 1043 | identical |
| storage-replay-performance001-evidence-active-fixture | 14.1 | 13.4 | 686 | 687 | identical |
| studies-2402.14112 | 233.1 | 227.0 | 8707 | 8775 | identical |
| studies-2409.17337 | 372.6 | 364.9 | 11467 | 11492 | identical |
| studies-2601.01283 | 114.5 | 111.9 | 6031 | 6029 | identical |
| studies-2607.29015 | 14.0 | 13.7 | 577 | 579 | identical |
| studies-closed-gravity-internal-measurements | 19.2 | 22.8 | 2295 | 2452 | identical |
| studies-closed-gravity-operational-reconstruction-20260913 | 10.7 | 8.8 | 566 | 567 | identical |
| studies-haar-purity-optimal-designs | 47.7 | 47.1 | 4852 | 4526 | identical |
| verify-2607.02120 | 6.7 | 6.1 | 324 | 323 | identical |
| verify-zenodo-22773593 | 32.7 | 29.9 | 1068 | 1100 | identical |

Every report is identical. The five largest stores are faster; one small
store took 1.19x and 1.07x the memory under the shared load; the others are
within 1.13x and 1.03x.

## Live checks of the problem commands

In a new store under `exactory-research/harness-dev/mc-live-check-0.55.0`
with the tree at fec05a1 (`misc/math-conjecturer/test-logs/
live-check-fec05a1.log`, run by `misc/math-conjecturer/bin/live_check.py`):

- `problem-init` with the example payload created the marker; `status`
  reported the milestone `exploration`, `next` `problem_slate_missing`, zero
  candidates and no slate; `gate post` listed the obligations in the order
  slate, statement, attempt, openness, value, assessment, bars, reviews,
  decision.
- `problem-candidate` with the example payload (without record evidence)
  recorded the candidate; `status` counted one current candidate.
- `problem-slate` before any bar returned `problem_bar_pending`; a slate
  naming an unknown candidate `problem_slate_incomplete`; a candidate with
  `previous: []` `invalid_input`.
- `problem-statement` with the example payload and `candidate_id` recorded the
  statement; `next` stayed `problem_slate_missing`; a statement naming an
  unknown candidate returned `problem_candidate_missing`.
- A second candidate naming the first as `previous` left one current
  candidate.
- `problem-attempt` with the example payload before any reading returned
  `problem_value_reading_missing`.
- The first candidate request repeated with its request id returned the
  original receipt.
- `export --kind problem` before readiness returned `readiness_required` with
  the obligations.

The same check at the merged head 0f4a82c (store
`mc-live-check-0.55.0-0f4a82c`, log `live-check-0f4a82c.log`) gave the same
outcomes at every step except two. Step 9 (`problem-attempt` with the example
payload in a fresh store) returns `problem_attempt_unbound` with the expected
store and the undelivered native digest: since wave 1 the solver binding is
checked before the readings. Step 12 (`export --kind problem` before
readiness) returns `readiness_required` with the obligations through the
standard error path (exit 1); the fec05a1 log shows exit 2 and an unparsed
line for that step. The same check at ac461a6, the merge of wave 5 (store
`mc-live-check-0.55.0-ac461a6`, log `live-check-ac461a6.log`), and at
d73c29d, the merge of wave 6 (store `mc-live-check-0.55.0-d73c29d`, log
`live-check-d73c29d.log`), and at the release tree, 4038d9b, the merge of
wave 7, with the release files present (store `mc-live-check-0.55.0-4038d9b`,
log `live-check-4038d9b.log`), gave the outcomes of the 0f4a82c run exactly.

## Release 0.55.1

Release 0.55.1 corrects the delivery boundary of the problem reviews and adds
the recovery after a refused review output. Astra's eighth round
(MSG-016-ASTRA, gpt-6-astra at effort max through the Codex CLI on the fork of
the design thread, settings verified from the session record) reviewed the
wave 7 code of 0.55.0 after the release: F6 to F9 closed; F10 (citations
offered that the receiving role cannot record), F11 (no delivery check on the
adjudicator's output and on reassessment findings) and F12 (a wording of the
evidence grammar, deferred to the next prompt version under ruling R63, the
prompts staying byte-identical) found; and the controller's recovery proposal
judged sound with changes, adopted as ruling R60. Implementer: gpt-6.1-sol at
effort xhigh (waves 8, 8a, 8b and 8c); the controller (Claude Fable 5.1) read
every diff, ran its own probe and wrote every text an AI reads (R26). Astra's
ninth round (MSG-017-ASTRA, same settings) reviewed 7b95663: the R60 code
confirmed on every design change and R64 passed, with two findings on saved
0.55.0 records: F13 (a reassessment finding recorded through the 0.55.0 path
that cites undelivered content passed the post gate), fixed in wave 8d under
ruling R65, and F14 (a saved 0.55.0 bar packet with a citation of an
abstract-only passage strands the reviewer that copied it), deferred to 0.55.2
under ruling R66 and named in the release notes as a known limitation.
Astra's tenth round (MSG-018-ASTRA, 16:44 to 16:52 PDT, same settings) reviewed b672f29: RELEASE with F14 deferred; F13 closed; the R65 recovery keeps the R60 invariants; no new finding; Astra ran eleven regression cases and two recovery probes on both Pythons itself.

### Commits

| Commit | Subject |
| --- | --- |
| cd399dc | Pin the typed missing-artifact error and the legacy undelivered entry |
| 7644de0 | Offer citations a role can record, and check the adjudicator's evidence for delivery |
| 7b95663 | Record a refused review output and permit one retry of the same assignment |
| b672f29 | Audit recorded review evidence at assessment time and refresh after it |
| b35fd17 | Expect the refusal record in the two source-request tests |

The test commit b35fd17 on top of b672f29 changes two tests and the fixture
only; the release commit on top of it changes the version files, the README
and the documentation only.

### RED and GREEN evidence

The logs are under `.superpowers/sdd/PLAN/` of the worktree `mc-0.55.1` and
in `misc/math-conjecturer/test-logs/` of the exactory repository where the
controller ran them.

- Wave 8 (cd399dc): the raw `FileNotFoundError` of a missing problem-evidence
  attachment did not reproduce (`ArtifactStore.read` raises `artifact_missing`
  on every path), so the two tests pin the typed error and the legacy
  undelivered entry of `describe_version_evidence`; 734 OK on Python 3.12 and
  3.9 over the 34 review modules.
- Wave 8a (7644de0): eight tests added and one corrected (the JSON-correction
  test of R58 expects no output citation), each failing first on cd399dc; 764
  OK on 3.12 (953.5 s) and 3.9 (1153.8 s) over 36 modules. Sol's own reviewer
  caught one regression during the wave (a typed `artifact_missing` turned
  into an error in `assess_rubric_reviews`), fixed before the report.
- Wave 8b (7b95663): 31 tests added, 22 failing first on 7644de0
  (`wave-8b-red-python312.log`, sixteen failures and six errors at the old
  resampling prohibition) and nine review-driven additions replayed against an
  isolated archive of 7644de0; three legacy fixtures patch the refusal writer
  to model pre-0.55.1 history; 795 OK on 3.12 (957.3 s) and 3.9 (1186.4 s).
  The controller's probe (`test-logs/probe-refresh-findings-8b.log`, the
  probe beside it) then found that a refreshed result was required to give a
  finding on an objection that only an earlier packet of the session had
  delivered, resolved since by a refreshed bar: both refreshed outputs were
  refused and two refusal records written, where 0.55.0 records them
  (`probe-refresh-findings-0.55.0.log`).
- Wave 8c (same commit): ruling R64 (the own packet's material objections plus
  the historical reviews and historical bars of the lineage) and the status
  section without `assignment_state`; four tests, each failing first on the
  8b tree (`wave-8c-red-python312.log`, `wave-8c-red-final-python312.log`).
  Sol's full runs stopped on a stray empty store at the repository root that
  the workspace walk-up found (six CLI tests met `corrupt_state`); the
  controller moved it aside, reran the CLI module (28 OK), its probe (both
  outputs recorded, no refusal record), the 35 refusal tests (OK) and the two
  full runs: 799 OK on 3.12 (1032.3 s) and 799 OK on 3.9
  (1232.3 s) over the 36 modules (`test-logs/wave-8c-merged-312.log`,
  `wave-8c-merged-39.log`).
- Wave 8d (b672f29): ruling R65 after Astra's F13; eight tests in
  `RecordedReviewEvidenceTests`, each failing first on 7b95663
  (`wave-8d-red-final-python312.log`, replayed against an archive of 7b95663
  in `wave-8d-red-baseline-final-python312.log`); 807 OK on 3.12 (1304.0 s)
  and 3.9 (1560.7 s) in Sol's runs. The controller's two probes on the tree:
  the R64 probe again (`test-logs/probe-refresh-findings-8d.log`, both
  refreshed outputs recorded) and the R60 retry inside an R65 refresh
  (`test-logs/probe-evidence-refresh-retry-8d.log`: one refusal record, the
  obligation kept until the retry records, then cleared, a third assignment
  refused). After the controller's texts: the eight tests OK
  (`wave-8d-focused-controller-312.log`), then 807 OK on 3.12
  (1256.9 s) and 807 OK on 3.9 (1528.9 s) over the
  36 modules (`test-logs/wave-8d-merged-312.log`, `wave-8d-merged-39.log`).
- The pinned prompt hash tests (four) are unchanged and pass at every commit.

GREEN at the release tree, the controller's runs. The first run of the 73
release modules at b672f29 with the release files gave 1385 tests with two
failures on Python 3.12 (6803.4 s; `test-logs/final-0.55.1-312.log`) and
the same two failures of 1385 on 3.9 (8529.0 s; `final-0.55.1-39.log`). The two
failures, `test_sufficient_response_with_source_requests_cannot_continue_or_finalize`
of `test_research_bar_sources` and
`test_result_request_cannot_include_a_final_support_verdict` of
`test_research_strategic_source_requests`, expected a refused review output
with contradictory source requests to leave the store unchanged. Since
7b95663 (R60) that output writes one `review_output_refusal` record through
the pure check `review.source_requests`, and the two modules lie outside the
36 modules of the fix waves, so no wave run reached them (the 73-module run at
7b95663 ended with the lost session). b35fd17 corrects the two tests through
the fixture helper `assert_refusal`; the harness code is unchanged. The two
modules then gave 31 OK on 3.12 (35.3 s) and 31 OK on 3.9
(42.2 s) (`fix-source-request-modules-312.log`, `-39.log`). No module
outside the 73 records a review or adjudication output, so the pull
request's CI run over the complete test tree is the full evidence at the
release commit. The math-solver harness suite alone 840 OK on 3.12 (1181.9 s)
and 840 OK on 3.9 (1239.4 s).

### CI runs

| Run | Head | Result |
| --- | --- | --- |
| the pull request's run | the release commit | the pull request's checks (19 jobs: Python 3.9, 3.12 and 3.14 shards, the remaining checks, the summary and the immutable test sources job); the merge waited for them |
| the post-merge runs | the merge commit on main, dev, local-dev and the tag | recorded in the SDD ledger of the context folder |

### Runs on recorded stores

The thirteen local stores of the 0.55.0 comparison were read with release
0.54.0 (label base0540, the recorded run of 2026-10-19) and with the release
tree of 0.55.1 (b672f29 with the release files; label mcb672f29, 16:45 to
17:03 PDT, while the 73-module runs and the harness suite on both Pythons
shared the machine): `test-logs/upgrade-compare-base0540-mcb672f29.json`,
log `upgrade-compare-run-5.log`.

| Store | 0.54.0 s | release s | 0.54.0 MiB | release MiB | Report |
| --- | ---: | ---: | ---: | ---: | --- |
| exactory-research-research-cli-moz3brnm | 1.7 | 0.2 | 32 | 24 | identical |
| lineage-acceptance-author | 7.5 | 7.4 | 769 | 772 | identical |
| lineage-acceptance-verify | 29.7 | 33.9 | 1043 | 1048 | identical |
| storage-replay-performance001-evidence-active-fixture | 14.1 | 14.2 | 686 | 687 | identical |
| studies-2402.14112 | 233.1 | 239.1 | 8707 | 8746 | identical |
| studies-2409.17337 | 372.6 | 381.4 | 11467 | 11483 | identical |
| studies-2601.01283 | 114.5 | 122.3 | 6031 | 6027 | identical |
| studies-2607.29015 | 14.0 | 15.9 | 577 | 577 | identical |
| studies-closed-gravity-internal-measurements | 19.2 | 23.9 | 2295 | 2290 | identical |
| studies-closed-gravity-operational-reconstruction-20260913 | 10.7 | 13.2 | 566 | 603 | identical |
| studies-haar-purity-optimal-designs | 47.7 | 52.6 | 4852 | 4690 | identical |
| verify-2607.02120 | 6.7 | 8.5 | 324 | 326 | identical |
| verify-zenodo-22773593 | 32.7 | 41.9 | 1068 | 1072 | identical |

Every report is identical. Under the shared load the wall time is between
0.1x and 1.28x of the 0.54.0 run (1.28x on `verify-zenodo-22773593`, 32.7 s
to 41.9 s) and the peak memory within 1.07x (603 MiB against 566 MiB on
`studies-closed-gravity-operational-reconstruction-20260913`); the three
largest stores are within 1.07x in time and 1.01x in memory.

### Live check of the problem commands

The live check of 0.55.0 (`bin/live_check.py`) at the release tree (b672f29
with the release files, 16:46 PDT; store `mc-live-check-0.55.1-b672f29`, log
`live-check-b672f29.log`) gave the outcomes of the 4038d9b run of 0.55.0 at
every step, with the same exit codes. The only differences in the output are
the timestamps, the executable path, the package digest, the version, the
source commit and the `review` section that 0.55.1 adds to `status`.

## Release 0.56.0

The Claude Code reviewer route and the repairs of `misc/harness-improvement/v19` of the exactory
repository (T2 to T26, recorded on the first problem record on 2026-10-20 and 2026-10-21), merged onto
0.55.1 on the branch `feat/claude-review-route`. The owner's instructions of 2026-10-21: no Codex
call in this process, and the plugin before the problem. The design was not reviewed by Astra for
that reason; the merge resolutions are listed in the commit `273e62b`.

### Commits

- `976736b` test: add the contract of the Claude Code reviewer route (RED: `ImportError`, 26 errors
  in `tests.test_research_claude_transport`, the module did not exist).
- `dc9d3b5` feat: add the Claude Code reviewer route (GREEN: 12 tests of that module and the 7 tests
  of `tests.test_research_codex_transport`).
- `5cc4a7f` fix: local repairs T2 to T26 of harness-improvement v19 on 0.55.0 (the ten files of the
  installed 0.55.0 copy after the repairs, on a branch from the tag `exactory--v0.55.0`).
- `273e62b` fix: ship the v19 repairs of the problem reviews with the Claude Code route (the merge
  of that branch, the conflict resolutions in `strategy.py`, `research_decisions.py`,
  `review_transport.py` and `review_protocol.py`, the previous-prompt recognition, the route-driven
  text-only delivery, the tests, the documentation and the version).

### RED and GREEN evidence

| Module | RED | GREEN |
| --- | --- | --- |
| `tests.test_research_claude_transport` | 26 errors, `ImportError: cannot import name 'review_claude_transport'` | 12 tests OK |
| `tests.test_research_codex_transport` | after the merge, 1 failure: the setup patched `NATIVE_DECLARATIONS` only, and the repaired adapter checks `NATIVE_DECLARATION_SETS` | 7 tests OK with the set patched |
| `tests.test_research_review_delivery` | written after the merge; the Codex elision test first expected the second reduction on an input that the first reduction already fit (`KeyError: 'delivery'`) | 11 tests OK with a 1,100,000-character body |
| `tests.test_research_inline_passages` | written after the merge; the tamper case that pointed a passage at the whole-text inspection still covered, because the offsets and text matched the source | 5 tests OK with a case that points at an inspection that does not contain the span |

The run of the four modules together: 35 tests OK (`PYTHONPATH=tests python3 -m unittest ...`).

### Runs on the recorded store

The first problem record (`math-conjectures/stretched-lr-positivity`, revision 478, 34 assignments)
recomputed with the branch's harness and with the repaired 0.55.0 copy: the same state for 31
assignments, the same `problem_evidence_digest`
(`dae50a55ddd16fbee2bae3476e84334affbb7e40e4d87f8ea4f6180ebfc79a84`) and the same prompt hashes for
the bar, slate, result, adjudicator and native_math roles. The three result assignments on the
0.55.x result prompt (`assignment-result-astra`, `assignment-result-luna`, `assignment-result-luna-2`)
report `verified_for_route` under the branch and `unverified` under the repaired copy, because the
branch recognizes that prompt as a previous prompt. No record was written.

### CI runs

Pending: the pull request's CI matrix (Python 3.9, 3.12, 3.14; four general shards; Codex and
math-solver jobs).

### Live check of the Claude route

Pending: `review-route` and `review-probe` on the installed 0.56.0 against `claude-opus-5-5`, recorded
on the first problem record. An exploratory invocation outside the harness on 2026-10-21
(`claude -p --safe-mode --tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'
--disable-slash-commands --no-session-persistence --system-prompt ... --model claude-sonnet-5-5
--output-format stream-json --verbose --input-format stream-json --replay-user-messages`) returned the
init event with `tools: []`, `mcp_servers: []`, `skills: []`, `slash_commands: []`, the replayed user
message, one assistant text and a `result` of subtype `success` with `num_turns: 1`.
