# Literature repairs, store bounds and derivation inputs (0.50.1): test record

Source plan: the v11 harness-improvement record and tasks H2, H3, H6 and H13 in
the exactory repository (`misc/harness-improvement/v11/`). Release 0.49.0 left
these tasks out. On 2026-10-01 the user asked to release the remaining code
changes as 0.50.1 on top of 0.50.0, and to leave the attack guard repair (task
H15) out of it.

## Journeys

1. As an author, I want a replaced search judgment to stop owing full readings
   of the works that no current judgment cites.
2. As an author, I want a citing-works capture from OpenAlex to record a search
   without a second import.
3. As an author, I want the bounded loop to count one captured query once,
   however its URL spells it.
4. As an author, I want the citation graph to drop the obligations of a bundle
   that a later bundle of the same paper replaced.
5. As a maintainer, I want every supported Python to read back what the store
   writes.
6. As a verifier, I want `exactory-derive` to report each step, or to refuse
   its input, without a Python traceback.

## How the release branch holds the commits

Pull requests #31 (0.49.0) and #32 (0.50.0) were squash merges, so `main` holds
no commit of the v11 branches. The branch `release/0.50.1` starts at 0.50.0
(`6d7779f`) and applies the source diffs as three-way merges:

| Release commit | Source diff | Content |
| --- | --- | --- |
| `34a538a` | `git diff 564ee99 4193dbd` | The literature group (H2, H3, H6) as 0.49.0 reverted it in `55cb4ba` |
| `9781d7d` | `git diff bea3737 c079f1e` | H13 and the fourth literature round |
| `b1a74e2` | `git diff ce02903 703bf7e` | The H13 limits and the `exactory-derive` fixes |
| `e69c980`, `94eebd5` | none | The steps-file bound, found by CI on Python 3.14.7 |
| `7247130`, `a0fba56` | none | The paper review assignment under the command bound, found by the review of the port |

The source commits are on the branches `fix/v11-harness-friction` (to `c079f1e`)
and `fix/v11-h13-limits` (to `703bf7e`). The commits to `4193dbd` are also in
the history of `release/0.49.0`.

Five files conflicted with 0.50.0, and each was resolved by hand:

- `tests/test_research_graph.py` (`34a538a`): the file equals its content at
  `bea3737`.
- `tests/test_research_guidance.py` (`9781d7d`): both the H13 test and the
  0.50.0 test `test_workflow_preserves_supported_delivery_and_authorized_objective_change`
  stay.
- `docs/research-cli.md` and `docs/research-workflow.md` (`b1a74e2`): each table
  row was resolved on its own. The 0.50.0 review paragraphs stay, and the H13
  input-bound paragraph follows them. In the manuscript paragraph, the H13
  sentences come before the 0.50.0 reviewer sentences.
- `tests/test_research_verification.py` (`b1a74e2`): 0.50.0 requires an observed
  reviewer assignment for `bind-verdict`, so `bind` and `build_bind_payload`
  add one by default. The two refusal tests of the verdict body bound pass
  `observed=False`, because `bind-verdict` checks that bound before the
  assignment.

## RED and GREEN evidence

Commands run from the repository root. Modules that import test fixtures need
`PYTHONPATH=tests`, for example
`PYTHONPATH=tests python3 -m unittest tests.test_research_literature`. Each row
names the commit that added the failing test and the commit that made it pass.
Each commit message gives the RED and GREEN runs.

| Task | RED commit and cause | GREEN commit | Test module |
| --- | --- | --- | --- |
| H3 | `176efdb`: reproduce the refused filter query of a native OpenAlex capture | `2750854` | `test_research_search_pages` |
| H2 | `84e25fb`: reproduce the requirements a replaced search judgment keeps | `8dcf9d4` | `test_research_graph`, `test_research_literature` |
| H2 | `6f97b42`: reproduce a deferral that stays bound to a replaced judgment's requirement | `8dcf9d4` | `test_research_source_deferrals` |
| H6 | `caeb709`: reproduce the obligations of a replaced bundle's occurrences | `38155ac` | `test_research_graph` |
| H2 | `be23227`: reproduce the stale successor that cites another version of a replaced citation | `e2726a8` | `test_research_literature` |
| H6 | `31ae54c`: reproduce a target graph that depends on its pin and the bundle import order | `c28f6de` | `test_research_graph` |
| H6 | `2c667fc`: reproduce the passage occurrences that a later article bundle hides | `c28f6de` | `test_research_graph` |
| H3 | `effa513`: reproduce two queries of one captured request that cover a loop purpose | `e1f9719` | `test_research_lineage` |
| H2 | `369ade3`: reproduce a deferral that an upgrade stales while its version stays required | `dc975d3` | `test_research_source_deferrals` |
| H2 | `3289363`: reproduce a judgment that cites again a family only a replaced judgment cited | `8f42202` | `test_research_literature` |
| H2 | `02355d5`: reproduce the requirement digests that change when a replacement cites the same works | `f545158` | `test_research_literature` |
| H3 | `5fca374`: reproduce loop counts that merge distinct requests and split one request | `34ecfcb` | `test_research_lineage` |
| H3 | `9aacaab`: reproduce a tool capture that adds a query to a native request bound to the same value | `4193dbd` | `test_research_lineage` |
| H3 | `da13ee1`: reproduce one query that counts twice because its captures differ in sort, select or mailto | `f1a1dfc` | `test_research_lineage`, `test_research_search_pages` |
| H2 | `342d661`: reproduce a deferral and digests that drop a version the citation graph still requires | `32b1190` | `test_research_literature`, `test_research_source_deferrals` |
| H6 | `a42b855`: reproduce a passage bundle that keeps its occurrences beside an article bundle | `b2812b4` | `test_research_graph` |
| H3 | `debfa46`: reproduce one query that counts twice because its URL spells the request differently | `41ed6a0` | `test_research_lineage`, `test_research_search_pages` |
| H3 | `ebb63f3`: reproduce one query that counts twice because its OpenAlex filter differs in letter case or alternative order | `bf2ddd4` | `test_research_lineage`, `test_research_search_pages` |
| H3 | `0ff0dd2`: reproduce two search filters that differ in their Boolean operators and count as one query | `314bb3c` | `test_research_search_pages` |
| H13 | `ff777e6`: reproduce stored values that another supported Python cannot read back | `619c3ad` | `test_research_storage` |
| H13 | `48dece6`: reproduce the workflow recipe that says reconcile-run completes every observation | `1f31dd0` | `test_research_guidance` |
| H13 | `bf24113`: reproduce JSON integers beyond the float range that escape as an OverflowError | `020af01` | `test_research_binary_pdf`, `test_research_execution` |
| H13 | `1dd0c70`: reproduce wall seconds beyond the float range that escape as an OverflowError | `4f464bb` | `test_research_resources`, `test_research_screening` |
| H13 | `cbe06f4`: reproduce a float charge to an account total that an earlier release stored beyond the float range | `4f464bb` | `test_research_resources` |
| H13 | `123ec44`: reproduce integer charges refused beyond the float range although only a float charge raises | `8349b38` | `test_research_resources`, `test_research_screening` |
| H13 | `ffb07f6`: reproduce an integer charge beyond the float range to a float total that raises OverflowError | `8349b38` | `test_research_resources` |
| H13 | `68d6a66`: reproduce a payload that the store accepts but a later record cannot copy | `e5469e9` | `test_research_publication`, `test_research_storage` |
| H13 | `44ccb35`: move the payload depth bound tests from the store to the command boundary | `e5469e9` | `test_research_publication`, `test_research_storage`, `test_research_verification` |
| H13 | `72db12d`: reproduce command inputs of 65 levels accepted, against a deepest copy of 10 levels | `debbeb0` | `test_research_publication`, `test_research_verification` |
| H13 | `763a02f`: reproduce a refused command input that still creates the store | `9210484` | `test_research_cli` |
| H13 | `11195bf`: reproduce command inputs of 33 levels accepted, beyond what the reviewer exports read | `5334ad0` | `test_research_cli`, `test_research_publication`, `test_research_verification` |
| H13 | `392d9f5`: reproduce refusals of the command bound that do not name the verdict body | `68f329d` | `test_research_cli`, `test_research_storage`, `test_research_verification` |
| Derive | `5d6b080`: reproduce a derivation range bound beyond the float range that escapes as an OverflowError | `ce02903` | `test_derive` |
| Derive | `fd49e21`, `2ca0009`: reproduce a wrong derivation step reported consistent when a side has no finite value | `2fc7f69` | `test_derive` |
| Derive | `9dae92d`: reproduce a wrong derivation step reported unparseable when an early sample point has no finite value | `62a55a6` | `test_derive` |
| Derive | `ba161ee`: reproduce complex values that end exactory-derive in a traceback | `f43f6ee` | `test_derive` |
| Derive | `f7d56ce`: reproduce malformed derivation steps and a long complex value that exactory-derive cannot report | `bab0177` | `test_derive` |
| Derive | `f6afbfb`: reproduce derivation inputs nested beyond the interpreter limits that end exactory-derive in a traceback | `81ee4f4` | `test_derive` |
| Derive | `e69c980`: reproduce a deep steps file that Python 3.14 reads and the other Pythons refuse | `94eebd5` | `test_derive` |
| H13 | `7247130`: reproduce a paper review assignment that the command bound refuses | `a0fba56` | `test_research_publication` |

Seven commits add tests that pass when they are added. They pin behavior that
the release keeps, and their messages give the runs:

- `8291d5f` pins the store bound for values built from tuples. It fails on a
  copy of `619c3ad` whose check skips tuples.
- `8b84f15` and `b79547f` pin the limits on records of earlier releases that the
  user accepted on 2026-09-30. The tests of `b79547f` fail on 0.49.0
  (`b6a2a40`), which has no store bounds.
- `5140408`, `08e79a8` and `540251e` pin the staleness of the other purposes
  after a replacement drops a family. The user accepted this effect of H2 on
  2026-09-29, and the documents state it (`79a4485`, `c982d05`, `d132f0e`).
- `60bc309` pins that an original that a bundle links as a supplement is read
  only as another body (documented in `8864f23`).

`a0fba56` also adds a test beside its fix that passes on its parent: the
command bound still counts every value of a review assignment except the bundle
of a paper review. It fails on a mutant that skips the bundle of every role and
on one that skips the whole context of a paper review.

## Independent verification

Each group was implemented test first. An independent verifier then tried to
refute it, and a fix agent repaired the confirmed defects. The v11 record keeps
every round:

- The literature group had three rounds in the first workflow. The third round
  left one blocker and one major finding open, so 0.49.0 reverted the group.
  The fourth round fixed both and ended its three verification rounds with two
  minor findings, which `0ff0dd2`, `314bb3c` and `c079f1e` fix.
- H13 had five completed verification rounds on `fix/v11-harness-friction` and
  three on `fix/v11-h13-limits`. The last of these found no blocker and met all
  six items. A last fix round (`a251b30..703bf7e`) repaired its minor findings,
  and the coordinator read that diff and ran the affected modules.
- An independent review of the port onto 0.50.0 (on `b1a74e2`) recomputed each
  port with `git merge-tree --write-tree --merge-base` and found every file
  equal to its commit, apart from the conflicts that the commit messages name.
  It found no change of 0.50.0 lost and no reader of literature state in the
  code that 0.50.0 adds. It ran 61 test modules: 1065 tests OK, 1 skipped,
  under Python 3.9.6 and 3.13.8. It found two blockers and two minor defects:
  - The command bound refused a paper review assignment, which repeats the
    stored bundle of `manuscript`. `7247130` reproduces it, and `a0fba56`
    fixes it.
  - The ported derivation test of a deep steps file failed on Python 3.14.7.
    `e69c980` and `94eebd5` fix it.
  - The depth figures in the storage comment and the CLI reference were those
    of 0.49.0, and the workflow named only `review` where 0.50.0 uses
    `value-review`. `a0fba56` corrects both.

## Upgrade probe

Ten stores on the maintainers' machine (seven studies and three verifications)
were copied, and each copy was read with 0.50.0 (`6d7779f`) and with the release
branch at `b1a74e2`. The later commits change `exactory-derive`, the command
bound of `review-assignment`, the documents and the version, and the probe
reads none of them. The probe compares the citation graph, the
full-text requirements that count, the search judgment staleness, the loop
state and closure, and the status of each source deferral.

| Store kind and policy | Requirements that count | Reference occurrences read | Tier 2 families |
| --- | --- | --- | --- |
| Study, `lineage-v1` | 56 to 39 | 695 to 171 | 3 to 3 |
| Study, `lineage-v1` | 34 to 21 | 989 to 400 | 8 to 7 |
| Study, `lineage-v1` | 24 to 24 | 285 to 161 | 4 to 4 |
| Study, `lineage-v1` | 25 to 16 | 715 to 350 | 13 to 9 |
| Study, `exhaustive-v1` | 12 to 12 | 1162 to 931 | 10 to 10 |
| Study, `screened-v1` | 0 to 0 | no graph | no graph |
| Study, `exhaustive-v1` | 44 to 23 | 574 to 168 | 2 to 2 |
| Verification, `sampled-v1` | 1 to 1 | 395 to 181 | 1 to 1 |
| Verification, `screened-v1` | 34 to 12 | 803 to 463 | 5 to 5 |
| Verification, `sampled-v1` | 2 to 2 | 53 to 53 | 1 to 1 |

In the two stores whose Tier 2 families fell, the frontier digest changed, and
every selected search judgment was stale before and after the upgrade.
`reference_unresolved` obligations fell from 46 to 38 in the `exhaustive-v1`
study with 1162 occurrences, and from 4 to 1 in the verification with 395. No
source deferral changed its status (eight deferrals in one store and two in
another). No loop state, loop closure or search judgment staleness changed.

## Release checks

- CI run 36955518462 on `94eebd5` passed on Python 3.9.25, 3.12.14 and 3.14.7:
  four general shards and the remaining checks for each version. CI run
  36949497180 on `b1a74e2` failed only the derivation test that `94eebd5`
  fixes, in the Python 3.14.7 general shard 0.
- `tests.test_derive` and `tests.test_manifest` pass on `94eebd5` under Python
  3.9.6, 3.13.8 and 3.14.6.
- On `a0fba56` with the version bump: `tests.test_research_publication`,
  `tests.test_research_cli`, `tests.test_research_review_protocol`,
  `tests.test_research_current_approval_binding`,
  `tests.test_research_scoped_review_protocol`, `tests.test_research_storage`
  and `tests.test_research_guidance` ran 158 tests, OK under Python 3.9.6 and
  3.13.8.
- `tests.test_manifest`, `tests.test_codex` and `tests.test_research_guidance`
  ran 46 tests, OK, and `python3 codex/generate.py --check` exits 0 on the
  release commit.
