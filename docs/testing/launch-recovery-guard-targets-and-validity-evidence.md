# Launch recovery, guard write targets and validity evidence (0.49.0): test record

Source plan: the v11 harness-improvement record and tasks H4, H10, H11, H12,
H14 and Problem 12 in the exactory repository (`misc/harness-improvement/v11/`).
The user asked on 2026-09-30 to release these tasks at once. The literature
tasks H2, H3 and H6 are reverted on the release branch (`55cb4ba`), and H13 and
H15 come in a later release.

## Journeys

1. As an author on a loaded machine, I want a launch to wait for a live worker,
   so that a slow start does not leave a claimed run to reconcile.
2. As an author, I want every launch failure to end with an error that names
   the recovery, so that I never meet a Python traceback and a run without an
   outcome.
3. As a math-solver user, I want the search controller's launch to behave in
   the same way.
4. As an author, I want to read workspace state and write notes that quote
   commands, while the experiment guard still stops every write into
   `.exactory/`.
5. As a reviewer, I want an assessment to state a passed validity check only
   with validation evidence.
6. As an author, I want an old arXiv identifier that lost its hyphen to resolve
   to its archive.

## RED and GREEN evidence

Commands run from the repository root. Modules that import test fixtures need
`PYTHONPATH=tests`, for example
`PYTHONPATH=tests python3 -m unittest tests.test_research_execution`. The
math-solver module runs as
`python3 -m unittest discover -s skills/math-solver/harness/tests -t skills/math-solver/harness -p 'test_search_execution.py'`.
Each row names the commit that added the failing test and the commit that made
it pass; each commit message gives the RED and GREEN runs.

| Task | RED commit and cause | GREEN commit | Test module |
| --- | --- | --- | --- |
| H4 | `b428b1b`: reproduce an old arXiv archive name that lost its hyphen | `035ef09` | `test_research_acquisition`, `test_research_providers` |
| H4 | `a1ba109`: reproduce the bundle refusal that hides the canonical target | `9846f1b` | `test_research_graph` |
| H12 | `98cd290`: reproduce the state-write guard that denies reads and heredoc text | `bb435a7` | `test_hooks` |
| H12 | `94e359f`: reproduce a state write through a nested substitution target | `22d979a` | `test_hooks` |
| H12 | `cd01521`: reproduce a heredoc body that a later command runs | `80d8603` | `test_hooks` |
| H12 | `6abfff1`: reproduce an expanded heredoc body whose substitution runs | `765c5cd` | `test_hooks` |
| H12 | `e741a6c`: reproduce heredocs that the guard reads differently from the shell | `37b62ef` | `test_hooks` |
| H12 | `ab66101`: reproduce "." and tee read as commands when they are arguments | `1a8f186` | `test_hooks` |
| H12 | `68b6172`: reproduce a state write whose redirection target is quoted | `0933c5d` | `test_hooks` |
| H12 | `9601dec`: reproduce zsh redirections into workspace state that pass the guard | `957858e` | `test_hooks` |
| H12 | `cef3a43`: reproduce a tee state write after a redirection or substitution | `a331ad6` | `test_hooks` |
| H12 | `72682bb`: reproduce heredoc bodies that run although the guard skips them | `bbf19f3` | `test_hooks` |
| H12 | `4090a3a`: reproduce a padded command that outlasts the guard's timeout | `41580da` | `test_hooks` |
| Problem 12 | `ab5c342`: reproduce the Stop hook that does not say how a direct-path study ends | `f5b347f` | `test_hooks` |
| H11 | `20ab3ba`: reproduce a passed validity check recorded without validation evidence | `975cea0` | `test_research_development` |
| H11 | `66c5feb`: reproduce a refusal that names only the first unsupported validity check | `265cc0f` | `test_research_development` |
| H10 | `3f46003`: reproduce the missing route of a partial computational result | `560acb4` | `test_research_guidance` |
| H10 | `1722925`: reproduce the documented partial-result route that stays blocked | `7570eab` | `test_research_development`, `test_research_guidance` |
| H14 | `e6add64`: reproduce a slow worker start that the launch reports as an unknown outcome | `ad48e5f` | `test_research_execution` |
| H14 | `77faa64`: reproduce a slow worker start that a short run timeout still reports as unknown | `d757b22` | `test_research_execution` |
| H14 | `b342b21`: reproduce a worker end after the wait for its run that escapes as a traceback | `c624dcf` | `test_research_execution` |
| H14 | `db22279`: reproduce a worker end before its token that escapes as a broken pipe | `e7acd84` | `test_research_execution` |
| H14 | `6b88abc`: reproduce a long run timeout that stops the launch with an overflow | `542ac2e` | `test_research_execution` |
| H14 | `3305e75`: reproduce a slow math-solver launcher start that ends in recovery_required | `39b43e6` | math-solver `test_search_execution.py` |
| H14 | `f34c469`: reproduce a math-solver run that outlasts the wait for it and leaves as a traceback | `f242c9f` | math-solver `test_search_execution.py` |
| H14 | `50fa5ae`: reproduce a refused math-solver launch whose live launcher leaves a traceback | `a9e13a3` | math-solver `test_search_execution.py` |
| H14 | `5b8fb7a`: reproduce a math-solver launcher end before its token that leaves as a broken pipe | `79bd2d9` | math-solver `test_search_execution.py` |
| H14 | `b6ed341`: reproduce a worker that cannot start and leaves a claimed run as a traceback | `70bf03e` | `test_research_execution` |
| H14 | `a2c5b0a`: reproduce a second exit wait after a refused math-solver launch | `048a585` | math-solver `test_search_execution.py` |
| H14 | `473dea3`: reproduce a live launcher error that hides why the math-solver launch was refused | `38487f0` | math-solver `test_search_execution.py` |
| H14 | `234fdfe`: reproduce a math-solver launcher that cannot start and leaves as a traceback | `027647e` | math-solver `test_search_execution.py` |
| H14 | `de42844`: reproduce a launcher start failure whose details omit the later errors | `9f940fb` | math-solver `test_search_execution.py` |
| H14 | `c1650f9`: reproduce a failed reconciliation that hides the refusal of a math-solver launch | `eba339b` | math-solver `test_search_execution.py` |
| H14 | `6a9366a`: reproduce a live launcher error that drops the details of the refusal | `f143583` | math-solver `test_search_execution.py` |

The hook naming of H12 (`eea8511`, `a5a67e0`, `6ca96bf`, `5d02c9d`, `bea3737`),
the renames of the math-solver branch (`279806c`, `603cee6`, `abca508`) and the
message corrections of `9ea5409` change no behavior; the tests of their groups
ran after them.

## Independent verification

- Identities, assessment and hooks (H4, H10, H11, H12, Problem 12): three
  rounds. Round 1 found three blockers and four minor issues, round 2 one
  blocker, one major and one minor issue, round 3 one minor issue (names).
- Hook naming: three rounds. Rounds 1 and 2 found minor naming and wording
  issues. Round 3 found a gap of the attack-files guard that has existed since
  0.33.0 (task H15, for a later release) and two false statements, which
  `a1930ba` corrects.
- Launcher wait (H14 in `research_harness`): three rounds. Round 2 found an
  `OverflowError` for run timeouts above about 24.86 days (fixed in `542ac2e`);
  round 3 found the three math-solver launch outcomes that the math-solver
  branch fixes.
- Math-solver launch branch: two rounds of independent verification, with a
  third fix round for eight minor findings that the coordinator reviewed; at
  `abca508`, `test_search_execution.py` (40 tests), `test_search_token_guard.py`
  (5 tests) and `tests.test_research_execution` (43 tests) passed under Python
  3.9.6 and 3.13.8.
- Release branch: an independent review of the literature revert, the merges,
  the documentation and the release note.

## Suites

- An independent review of the release branch at `b7ca5d5` ran
  `tests.test_research_graph`, `test_research_literature`,
  `test_research_lineage`, `test_research_search_pages`,
  `test_research_source_deferrals`, `test_research_guidance`, `test_manifest`,
  `test_codex`, `test_hooks` and `test_research_execution` from a `git archive`
  export: 209 tests OK under Python 3.9.6 and 209 OK under 3.13.8. The
  math-solver `test_search_execution.py` gave 40 OK under 3.9.6; under 3.13.8
  two timing tests failed at a load average of 30 to 47. One of them fails the
  same way on 0.48.0 (`2ab92cf`), and the other passed on rerun on both trees.
- The review found four false statements in the release note, the README and
  the attack guard's docstring, one overstated statement and five minor issues.
  The commit that adds this record corrects them. It changes no code.
- CI runs on every commit of pull request #31 (Python 3.9.25, 3.12.14 and
  3.14.7: the full `tests` suite, the math-solver harness suite, the Codex
  check and the JSON validation). The v11 record in the exactory repository
  keeps the run ids and their results.
- A local run of the full and math-solver suites on `b7ca5d5` started at 08:40
  PDT on 2026-09-30, while other sessions held the load average between 30 and
  150. Its result goes to the v11 record.
