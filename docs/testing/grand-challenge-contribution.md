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
