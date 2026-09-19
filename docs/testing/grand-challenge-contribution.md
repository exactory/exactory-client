# Test record: Grand Challenge direction and contribution development

Design: `misc/harness-improvement/v6/grand-challenge-contribution-design.md` in the exactory repository.
Each task was written test first. The RED line is the failure seen before the implementation; the GREEN lines are
the suites run after it. Suites marked "(frozen copy)" ran on a copy of the tree taken at that task.

| Task | Command | Result |
| --- | --- | --- |
| T01 baseline on 3e14b24 (archive copy) | `python3 -m unittest discover -s tests` | Ran 1155 tests in 2603.015s OK (skipped=1) |
| T02 RED | `test_research_publication.py` | the two new tests errored: the validator refused the ninth field (`Expected fields: ...`) |
| T02 GREEN | `test_research_publication.py` | Ran 22 tests OK |
| T02 (frozen copy) | `test_research_rounds.py`, `test_research_round_integrity.py`, `test_research_review_packets.py`, `test_draft.py`, `test_research_cli.py` | Ran 63, 12, 51, 81, 23 tests OK |
| T03 RED | `test_research_literature.py -k grand_challenge`, `test_research_contribution.py` | `invalid_search`: the purpose was unknown |
| T03 GREEN | `test_research_literature.py`, `test_research_contribution.py`, `test_research_lineage.py` | OK, OK, OK |
| T04 RED | `test_research_challenge.py` | ImportError: `research_harness.challenge` did not exist |
| T04 GREEN | `test_research_challenge.py`, `test_research_synthesis.py` | Ran 5 tests OK; Ran 34 tests OK after three hand-built syntheses recorded the Grand Challenge |
| T04 (frozen copy) | development, cli, guidance, report_views, publication, literature, lineage | Ran 65, 23, 5, 12, 22, 24, 8 tests OK |
| T05 RED | `test_research_principles.py` | `AssertionError: '4' != '5'` |
| T05 GREEN | `test_research_principles.py`, `test_research_guidance.py` | OK, OK |
| T06 RED | `test_research_contribution.py` | ImportError: `research_harness.contribution` did not exist |
| T06 GREEN | `test_research_contribution.py` | Ran 10 tests OK |
| T07 RED | `test_research_contribution.py -k NextBundle` | `AssertionError: ResearchError not raised` |
| T07 GREEN | `test_research_contribution.py` | OK |
| T08 RED | `test_research_contribution.py -k RoundContribution` | 2 failures and 3 errors: no measurement or analysis rule; `criterion_ids` refused |
| T08 GREEN | `test_research_contribution.py` | Ran 16 tests OK |
| T09 | `test_research_guidance.py`, `test_research_cli.py` | OK, OK |
| T10 | `python3 codex/generate.py --check` | exit 0; the generated entrypoints did not change |
| Finished tree (frozen copy, before 0c1c4f7) | `python3 -m unittest discover -s tests` | Ran 1183 tests: 3 failures, all expectations the plan did not list (provenance pinned constitution 4; a stop decision now needs a complete measurement; the round summary gained `analysis`); fixed in 0c1c4f7 |
| T12 on 9d01b2f | `python3 -m unittest discover -s tests` | Ran 1183 tests in 3157.487s OK |
| T12 on 9d01b2f | `python3 codex/generate.py --check` | exit 0 |

## T11: migration of two existing studies

Copies of two study workspaces were made under `exactory-research/harness-dev/grand-challenge-acceptance/` on
2026-09-18; the studies themselves were not touched. The branch's `bin/exactory-research` ran `status`, adopted
constitution 5 on each copy with `constitution`, and ran `status` again. The "before" column is the full `status` of
the original study under 0.41.0 (`git archive 3e14b24`), which is read-only; the "after" column is the full `status`
of the copy under this branch.

| Copy | Stage | Constitution before | Before revalidation | After revalidation |
| --- | --- | --- | --- | --- |
| `closed-gravity-internal-measurements` | cohort | 1 | under 0.41.0, read-only on the original study: `abstract_reading_missing` 398, `reference_unresolved` 46, `search_dispositions_missing` 5, `search_evidence_stale` 5, `search_frontier_stale` 5, `synthesis_dependencies_stale` 4, `candidate_checkpoint_missing` 1, `constitution_revalidation_required` 1 (store revision 3584 before and after) | `abstract_reading_missing` 398, `reference_unresolved` 46, `search_dispositions_missing` 5, `search_evidence_stale` 5, `search_frontier_stale` 5, `synthesis_dependencies_stale` 4, `candidate_checkpoint_missing` 1, `grand_challenge_missing` 1; round gate `publication_bundle_missing` |
| `haar-purity-optimal-designs` | complete | 3 | under 0.41.0, read-only on the original study: `synthesis_dependencies_stale` 4, `constitution_revalidation_required` 1, `inherited_result_stale` 1 (store revision 1183 before and after) | `synthesis_dependencies_stale` 4, `grand_challenge_missing` 1, `inherited_result_stale` 1; round gate `readiness_required` |

In both studies the only obligation the release adds is `grand_challenge_missing`, and revalidation removes
`constitution_revalidation_required`; every other code was owed before the migration.
`contribution_analysis_missing` does not appear yet because both round gates first owe a current bundle; it
appears once a study measures a current bundle.

## 0.42.1: review of 0.42.0 and its corrections

A lead review and two independent reviews (correctness; conformance with the design) read `3e14b24..61d5dc6`.
Every finding below was reproduced before it was changed. "RED" is the failure seen with the new test before the
implementation changed; "GREEN" is the suite after it.

| Finding | RED | GREEN |
| --- | --- | --- |
| The capture import that precedes a `grand_challenge` search changes the record of every held work the response returns, so the measured bundle became stale | probe on `61d5dc6`: a capture that returns the study's root gives `readiness_required` with 13 obligations after `import_response`; a capture that returns `arxiv:2601.00001v2` gives `readiness_required` with 6 | the investigation is kept as captured responses; `test_an_investigation_that_names_held_works_keeps_the_pinned_bundle_current` records an analysis whose capture names the root, a held source and a newer version, and then the round decision; `test_research_contribution.py` Ran 22 tests OK |
| A fourth predicting assessor made a complete measurement incomplete | `AssertionError: ResearchError not raised`; probe: `owing after a fourth prediction alone: False`, next pin accepted | `manuscript_prediction_excess`; OK |
| `batches --loop` exported the hits of a `grand_challenge` search | the export listed `arxiv:2601.00005v1` | fixed in `d0042b9`, then made moot: the purpose is removed and `literature.py` and `batches.py` equal `3e14b24` again (`git diff 3e14b24 -- research_harness/literature.py research_harness/batches.py` is empty) |
| The workflow and the CLI reference showed `target` before `grand-challenge` | read | both documents and the CLI stage test record `grand-challenge` before `target` |
| A Grand Challenge record was accepted without a research configuration, and stored a shared source twice | `ResearchError not raised`; two identical evidence items | `configuration_missing`, `profile_inapplicable`, one item; `test_research_challenge.py` Ran 9 tests OK |
| `status --summary` with a large record | `AssertionError: 811173 not less than or equal to 16384` | the record id, the challenges per horizon and six criterion ids cut at 32 characters; worst case 15,752 bytes with ids of a control character, which JSON writes as six bytes |
| Malformed evidence of an analysis | `'invalid_round' != 'invalid_contribution_analysis'` for three payloads | OK |
| `round.analysis` on a stale selected bundle; owed analysis hidden by `gate round` | `False is not true`; `[] != ['paper-75']` | OK |
| Criterion ids were not bound to a Grand Challenge record | `KeyError: 'grand_challenge'` | the analysis and the decision keep `{id, digest}`; the packet carries `analysis_grand_challenge` |
| A list in a field of an artifact reference | `TypeError: unhashable type: 'list'` | typed `ResearchError`; `test_research_evaluation.py` OK |
| The four CLI examples could not be recorded together | read: the step statement differed from the candidate and goal statements, and `builds_on` named no claim of the `manuscript` example | `test_the_grand_challenge_examples_can_be_recorded_together` |

Tests added without a production change, because 0.42.0 held the behaviour and nothing pinned it: a full reading of a
source the study does not hold keeps the bundle current and serves as step evidence; the preparation gate owes exactly
`grand_challenge_missing` when the record is the only thing missing; reviews without `changes_for_maximum` leave nothing
to dispose of; a reworded step is refused as a candidate.

A review of the fix diff (`61d5dc6..dc75555`) found no blocker and these defects in the corrections themselves:

| Finding | RED | GREEN |
| --- | --- | --- |
| The first correction put `grand_challenge_missing` directly before `objective_missing`, so `next` named it before the roots, the readings and the searches, although the record needs a source read in full | `['grand_challenge_missing', 'roots_missing', ...] != ['roots_missing', 'fulltext_reading_missing', 'search_purpose_missing', 'grand_challenge_missing', 'standards_missing']` | the code sits after the searches and before the synthesis sections; `objective_missing` keeps its 0.38 place |
| The 16 KiB test used ASCII ids; an id of control characters costs six bytes each | `AssertionError: 19640 not less than or equal to 16384` | a flat view with six criterion ids cut at 32 characters; 15,752 bytes |
| Reuse was detected by the response bytes alone, so another query that returned the same empty response was refused | read, and the reviewer's probe | reuse is the same query with the same response; the test records a second analysis whose other query returns the same bytes |

The same review confirmed by running: the corrected flow through the real `artifact` operation keeps the bundle current
and the round decision is accepted; the four CLI examples were recorded together through the real operations; a 0.42.0
analysis without `investigation` crashes neither `round_state`, `record_round` nor the round packet. A test now holds,
under `lineage-v1`, that a full reading of a source the study does not hold leaves the foundation unchanged.
