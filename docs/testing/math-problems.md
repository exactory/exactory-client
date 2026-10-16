# Test record: the Mathematics section client

Design: `docs/superpowers/specs/2026-10-12-math-problems-design.md` in the exactory repository (DESIGN.md version 0.9).
Each task was written test first. The RED line is the failure seen before the
implementation; the GREEN lines are the suites run after it.

The F1 to F4 and F6 rows reproduce their task reports, including every fix round.
The row labels name the report round and source line. These are historical results, not F5 reruns.
Command fragments and descriptions stay as reported. A missing full command is not reconstructed.
Each Python version has its own row. Repeated suite runs have separate rows.
The record also includes the reported Python 3.13 suite runs. Mutation checks remain separate from suite runs.
F5 ran each command with `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN`.
F5 changes documentation and a generated entry. It adds no behavior test and records no RED run.
Release notes carry the release version. F5 leaves the plugin version unchanged.

| Task | Command | Result |
| --- | --- | --- |
| F1 RED (initial; report line 42) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -m unittest discover -s tests -p 'test_math_problems_cli.py' -v` | python3 3.9.6: 22 tests; FAILED (failures=1, errors=38) |
| F1 RED (base copy) (initial; report line 49) | 29 final math tests against the base command in a scratch tree; full test command not reported | python3 3.9.6: 29 tests; 28 errors, 1 passes |
| F1 RED (base copy) (initial; report line 49) | 29 final math tests against the base command in a scratch tree; full test command not reported | python3.12 3.12.8: 29 tests; 28 errors, 1 passes |
| F1 GREEN (initial; report line 28) | `-m unittest discover -s tests -p 'test_math_problems_cli.py' -v` (final file) | python3 3.9.6: 29 tests, OK |
| F1 GREEN (initial; report line 28) | `-m unittest discover -s tests -p 'test_math_problems_cli.py' -v` (final file) | python3.12 3.12.8: 29 tests, OK |
| F1 GREEN (before the last edit) (initial; report line 30) | `-p 'test_transport.py' -v` (brief's Step 4, existing transport tests), run twice: before and after my last edit to `bin/exactory` | python3 3.9.6: 99 tests, OK |
| F1 GREEN (after the last edit) (initial; report line 30) | `-p 'test_transport.py' -v` (brief's Step 4, existing transport tests), run twice: before and after my last edit to `bin/exactory` | python3 3.9.6: 99 tests, OK |
| F1 GREEN (before the last edit) (initial; report line 30) | `-p 'test_transport.py' -v` (brief's Step 4, existing transport tests), run twice: before and after my last edit to `bin/exactory` | python3.12 3.12.8: 99 tests, OK |
| F1 GREEN (after the last edit) (initial; report line 30) | `-p 'test_transport.py' -v` (brief's Step 4, existing transport tests), run twice: before and after my last edit to `bin/exactory` | python3.12 3.12.8: 99 tests, OK |
| F1 GREEN (initial; report line 31) | The six modules that load or run `bin/exactory`, together, on the final bytes: `test_math_problems_cli` 29, `test_transport` 99, `test_manifest` 8, `test_research_source_limited_commands` 10, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3 3.9.6: 168 tests, OK, 974 s |
| F1 GREEN (initial; report line 31) | The six modules that load or run `bin/exactory`, together, on the final bytes: `test_math_problems_cli` 29, `test_transport` 99, `test_manifest` 8, `test_research_source_limited_commands` 10, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3.12 3.12.8: 168 tests, OK, 878 s |
| F1 GREEN (first run) (initial; report line 53) | 22 math tests; full command not repeated in the report | python3 3.9.6: First GREEN run, 22 tests |
| F1 GREEN (first run) (initial; report line 53) | 22 math tests; full command not repeated in the report | python3.12 3.12.8: First GREEN run, 22 tests |
| F1 CHECK (initial; report line 29) | same, with `-X dev -W error` (no stray warning) | python3 3.9.6: OK |
| F1 CHECK (initial; report line 29) | same, with `-X dev -W error` (no stray warning) | python3.12 3.12.8: OK |
| F1 CHECK (initial; report line 32) | `python3 codex/generate.py --check` | python3 3.9.6: exit 0 |
| F1 CHECK (initial; report line 33) | syntax and `py_compile` of every `bin/` file | python3 3.9.6: OK |
| F1 CHECK (initial; report line 33) | syntax and `py_compile` of every `bin/` file | python3.12 3.12.8: OK |
| F1 PARTIAL (initial; report line 63) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -m unittest discover -s tests -v` | Stopped after 319 s; 177 finished, all OK; suite incomplete |
| F1 PARTIAL (initial; report line 64) | `ci/unittest_shards.py`, 10 shards in a scratch tree; full command not reported | Stopped at 900 s; 543 finished, all OK; 10 running and 1384 not started |
| F1 RED (Fix round 1; report line 155) | `python3 -m unittest discover -s tests -p 'test_math_problems_cli.py' -v` | python3 3.9.6: FAILED (failures=15, errors=3) |
| F1 RED (Fix round 1; report line 155) | `python3 -m unittest discover -s tests -p 'test_math_problems_cli.py' -v` | python3.12 3.12.8: FAILED (failures=15, errors=3) |
| F1 RED (Fix round 1; report line 156) | `python3 -m unittest discover -s tests -p 'test_transport.py' -k test_a_command_with_an_id_in_its_path_refuses` | python3 3.9.6: FAILED (failures=16) |
| F1 GREEN (Fix round 1; report line 165) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest test_math_problems_cli test_transport test_manifest test_user_authorized_publication test_research_guidance -v` (30 + 101 + 8 + 13 + 9 tests) | python3 3.9.6: 161 tests, OK, 147 s |
| F1 GREEN (Fix round 1; report line 165) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest test_math_problems_cli test_transport test_manifest test_user_authorized_publication test_research_guidance -v` (30 + 101 + 8 + 13 + 9 tests) | python3.12 3.12.8: 161 tests, OK, 132 s |
| F1 GREEN (Fix round 1; report line 166) | same flags, `test_research_source_limited_commands -v` (the slow lifecycle module; `bin/exactory` bytes are the same as in the committed fix) | python3 3.9.6: 10 tests, OK, 380 s |
| F1 GREEN (Fix round 1; report line 166) | same flags, `test_research_source_limited_commands -v` (the slow lifecycle module; `bin/exactory` bytes are the same as in the committed fix) | python3.12 3.12.8: 10 tests, OK, 332 s |
| F1 CHECK (Fix round 1; report line 167) | `python3 codex/generate.py --check` | python3 3.9.6: exit 0 |
| F1 CHECK (Fix round 1; report line 168) | `ast.parse` of every `bin/` file | python3 3.9.6: OK |
| F1 CHECK (Fix round 1; report line 168) | `ast.parse` of every `bin/` file | python3.12 3.12.8: OK |
| F1 RED (Fix round 1, follow-up; report line 196) | `python3 -m unittest discover -s tests -p 'test_math_problems_cli.py' -v`, before the change in `bin/exactory` (RED) | python3 3.9.6: FAILED (failures=3) |
| F1 RED (Fix round 1, follow-up; report line 196) | `python3 -m unittest discover -s tests -p 'test_math_problems_cli.py' -v`, before the change in `bin/exactory` (RED) | python3.12 3.12.8: FAILED (failures=3) |
| F1 GREEN (Fix round 1, follow-up; report line 198) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest test_math_problems_cli test_transport -v` | python3 3.9.6: 133 tests (32 + 101), OK, 127 s |
| F1 GREEN (Fix round 1, follow-up; report line 198) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest test_math_problems_cli test_transport -v` | python3.12 3.12.8: 133 tests, OK, 111 s |
| F1 CHECK (Fix round 1, follow-up; report line 197) | same command, after the change (GREEN) | python3 3.9.6: 32 tests, OK |
| F1 CHECK (Fix round 1, follow-up; report line 197) | same command, after the change (GREEN) | python3.12 3.12.8: 32 tests, OK |
| F1 CHECK (Fix round 1, follow-up; report line 199) | `python3 codex/generate.py --check`; `ast.parse` of every `bin/` file | python3 3.9.6: exit 0; OK |
| F1 CHECK (Fix round 1, follow-up; report line 199) | `python3 codex/generate.py --check`; `ast.parse` of every `bin/` file | python3.12 3.12.8: OK |
| F2 RED (initial; report line 54) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest tests.test_math_problems_cli` | python3 3.9.6: 63 tests; FAILED (errors=128) |
| F2 RED (initial; report line 54) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest tests.test_math_problems_cli` | python3.12 3.12.8: 63 tests; FAILED (errors=128) |
| F2 IMPORT ERROR (initial; report line 43) | Dotted import of `test_research_source_limited_commands`; full command not reported | `ModuleNotFoundError: No module named 'test_transport'`; discover rerun passed |
| F2 GREEN (initial; report line 37) | `-m unittest tests.test_math_problems_cli -v` | python3 3.9.6: 64 tests OK |
| F2 GREEN (initial; report line 37) | `-m unittest tests.test_math_problems_cli -v` | python3.12 3.12.8: 64 tests OK |
| F2 GREEN (initial; report line 38) | the brief's Step 4 command, `-m unittest discover -s tests -p 'test_math_problems_cli.py' -v` | python3 3.9.6: 64 tests OK |
| F2 GREEN (initial; report line 38) | the brief's Step 4 command, `-m unittest discover -s tests -p 'test_math_problems_cli.py' -v` | python3.12 3.12.8: 64 tests OK |
| F2 GREEN (initial; report line 39) | together: math 64, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3 3.9.6: 195 tests OK in 152.8 s |
| F2 GREEN (initial; report line 39) | together: math 64, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3.12 3.12.8: 195 tests OK in 135.3 s |
| F2 GREEN (initial; report line 40) | `test_research_source_limited_commands`, through `discover` | python3 3.9.6: 10 tests OK in 365.1 s |
| F2 GREEN (initial; report line 40) | `test_research_source_limited_commands`, through `discover` | python3.12 3.12.8: 10 tests OK in 309.8 s |
| F2 CHECK (initial; report line 41) | `python3 codex/generate.py --check` | python3 3.9.6: exit 0 |
| F2 RED (Fix round 1; report line 150) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest tests.test_math_problems_cli` | python3 3.9.6: 69 tests; FAILED (failures=29, errors=2) |
| F2 RED (Fix round 1; report line 150) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest tests.test_math_problems_cli` | python3.12 3.12.8: 69 tests; FAILED (failures=29, errors=2) |
| F2 GREEN ATTEMPT (Fix round 1; report line 157) | First run after implementation; full command not repeated | 3 failures from old whitespace rows |
| F2 GREEN (Fix round 1; report line 137) | `-m unittest tests.test_math_problems_cli -v` | python3 3.9.6: 70 tests OK |
| F2 GREEN (Fix round 1; report line 137) | `-m unittest tests.test_math_problems_cli -v` | python3.12 3.12.8: 70 tests OK |
| F2 GREEN (Fix round 1; report line 138) | the brief's form, `discover -s tests -p 'test_math_problems_cli.py' -v` | python3 3.9.6: 70 tests OK |
| F2 GREEN (Fix round 1; report line 138) | the brief's form, `discover -s tests -p 'test_math_problems_cli.py' -v` | python3.12 3.12.8: 70 tests OK |
| F2 GREEN (Fix round 1; report line 139) | together: math 70, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3 3.9.6: 201 tests OK in 147.9 s |
| F2 GREEN (Fix round 1; report line 139) | together: math 70, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3.12 3.12.8: 201 tests OK in 131.2 s |
| F2 GREEN (Fix round 1; report line 140) | `test_research_source_limited_commands`, through `discover` | python3 3.9.6: 10 tests OK in 356.5 s |
| F2 GREEN (Fix round 1; report line 140) | `test_research_source_limited_commands`, through `discover` | python3.12 3.12.8: 10 tests OK in 311.4 s |
| F2 GREEN (Fix round 1; report line 157) | Run after the three whitespace rows changed; full command not repeated | 69 tests OK; interpreter not specified for this intermediate run |
| F2 CHECK (Fix round 1; report line 141) | `python3 codex/generate.py --check` | python3 3.9.6: exit 0 |
| F3 RED (initial; report line 57) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest tests.test_math_problems_cli.TestLinkPatternsOnAHostilePath tests.test_math_problems_cli.TestProofsChecksAndDiscussion tests.test_math_problems_cli.TestSubmitWithMathProblems` | 9 tests; FAILED (errors=16) |
| F3 RED (initial; report line 70) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest -v tests.test_math_problems_cli` | python3 3.9.6: 123 tests; FAILED (failures=10, errors=281) |
| F3 RED (initial; report line 70) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest -v tests.test_math_problems_cli` | python3.12 3.12.8: 123 tests; FAILED (failures=10, errors=281) |
| F3 GREEN (initial; report line 41) | baseline before any change: `-m unittest tests.test_math_problems_cli` | python3 3.9.6: 70 tests OK |
| F3 GREEN (initial; report line 41) | baseline before any change: `-m unittest tests.test_math_problems_cli` | python3.12 3.12.8: 70 tests OK |
| F3 GREEN (initial; report line 42) | final: `-m unittest tests.test_math_problems_cli` | python3 3.9.6: 124 tests OK |
| F3 GREEN (initial; report line 42) | final: `-m unittest tests.test_math_problems_cli` | python3.12 3.12.8: 124 tests OK |
| F3 GREEN (working tree) (initial; report line 43) | the brief's Step 4: `discover -s tests -p 'test_math_problems_cli.py' -v`, then `... -p 'test_transport.py' -v` | python3 3.9.6: 124 OK |
| F3 GREEN (committed tree) (initial; report line 43) | the brief's Step 4: `discover -s tests -p 'test_math_problems_cli.py' -v`, then `... -p 'test_transport.py' -v` | python3 3.9.6: 101 OK in 121.0 s |
| F3 GREEN (working tree) (initial; report line 43) | the brief's Step 4: `discover -s tests -p 'test_math_problems_cli.py' -v`, then `... -p 'test_transport.py' -v` | python3.12 3.12.8: 124 OK |
| F3 GREEN (committed tree) (initial; report line 43) | the brief's Step 4: `discover -s tests -p 'test_math_problems_cli.py' -v`, then `... -p 'test_transport.py' -v` | python3.12 3.12.8: 101 OK in 104.8 s |
| F3 GREEN (initial; report line 44) | together: math 124, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3 3.9.6: 255 tests OK in 152.1 s |
| F3 GREEN (initial; report line 44) | together: math 124, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3.12 3.12.8: 255 tests OK in 134.5 s |
| F3 GREEN (initial; report line 45) | `test_research_source_limited_commands`, through `discover` | python3 3.9.6: 10 tests OK in 367.1 s |
| F3 GREEN (initial; report line 45) | `test_research_source_limited_commands`, through `discover` | python3.12 3.12.8: 10 tests OK in 322.2 s |
| F3 GREEN (initial; report line 78) | Same 123-test command after implementation | python3 3.9.6: 123 tests OK |
| F3 GREEN (initial; report line 78) | Same 123-test command after implementation | python3.12 3.12.8: 123 tests OK |
| F3 CHECK (initial; report line 46) | `python3 codex/generate.py --check` | python3 3.9.6: exit 0 |
| F3 CHECK (initial; report line 46) | `python3 codex/generate.py --check` | python3.12 3.12.8: exit 0 (run for completeness) |
| F3 RED (Fix round 1; report line 201) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest -v tests.test_math_problems_cli` | python3 3.9.6: 141 tests; FAILED (failures=23, errors=46) |
| F3 RED (Fix round 1; report line 201) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL python3 -X dev -W error -m unittest -v tests.test_math_problems_cli` | python3.12 3.12.8: 141 tests; FAILED (failures=23, errors=46) |
| F3 GREEN (Fix round 1; report line 186) | Math module alone; full command not repeated | python3 3.9.6: 142 tests OK |
| F3 GREEN (Fix round 1; report line 186) | Math module alone; full command not repeated | python3.12 3.12.8: 142 tests OK |
| F3 GREEN (working tree) (Fix round 1; report line 190) | together: math 142, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3 3.9.6: 273 OK in 158.5 s |
| F3 GREEN (committed tree) (Fix round 1; report line 190) | together: math 142, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3 3.9.6: 273 OK in 155.9 s |
| F3 GREEN (working tree) (Fix round 1; report line 190) | together: math 142, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3.12 3.12.8: 273 OK in 140.3 s |
| F3 GREEN (committed tree) (Fix round 1; report line 190) | together: math 142, `test_transport` 101, `test_manifest` 8, `test_user_authorized_publication` 13, `test_research_guidance` 9 | python3.12 3.12.8: 273 OK in 137.8 s |
| F3 GREEN (working tree) (Fix round 1; report line 191) | `test_research_source_limited_commands`, through `discover` | python3 3.9.6: 10 OK in 382.0 s |
| F3 GREEN (committed tree) (Fix round 1; report line 191) | `test_research_source_limited_commands`, through `discover` | python3 3.9.6: 10 OK in 371.4 s |
| F3 GREEN (working tree) (Fix round 1; report line 191) | `test_research_source_limited_commands`, through `discover` | python3.12 3.12.8: 10 OK in 335.1 s |
| F3 GREEN (committed tree) (Fix round 1; report line 191) | `test_research_source_limited_commands`, through `discover` | python3.12 3.12.8: 10 OK in 328.1 s |
| F3 GREEN (Fix round 1; report line 209) | Same 141-test command after implementation | python3 3.9.6: 141 tests OK |
| F3 GREEN (Fix round 1; report line 209) | Same 141-test command after implementation | python3.12 3.12.8: 141 tests OK |
| F3 CHECK (Fix round 1; report line 192) | `python3 codex/generate.py --check` | python3 3.9.6: exit 0 |
| F3 CHECK (Fix round 1; report line 192) | `python3 codex/generate.py --check` | python3.12 3.12.8: exit 0 (run for completeness) |
| F4 RED (initial; report line 62) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN python3 -X dev -W error -m unittest discover -s tests -p 'test_draft_problem_attachment.py' -v` | 27 tests; FAILED (errors=27), missing `_call_exactory` |
| F4 GREEN (initial; report line 41) | New module, 38 tests (27 from the brief + 11 added) | python3 3.9.6: OK |
| F4 GREEN (initial; report line 41) | New module, 38 tests (27 from the brief + 11 added) | python3.12 3.12.8: OK |
| F4 GREEN (initial; report line 42) | Sharded run of 12 modules (470 tests: `test_draft` 81, new module 38, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8) | python3 3.9.6: 470 OK in 10 shards |
| F4 GREEN (initial; report line 42) | Sharded run of 12 modules (470 tests: `test_draft` 81, new module 38, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8) | python3.12 3.12.8: 470 OK in 10 shards |
| F4 GREEN (initial; report line 44) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests`) | python3 3.9.6: OK |
| F4 GREEN (initial; report line 44) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests`) | python3.12 3.12.8: OK |
| F4 GREEN (extra interpreter) (initial; report line 46) | 24 fast attachment tests; full command not reported | python3.13: 24 tests pass |
| F4 GREEN (initial; report line 67) | 13 direct-path tests; full command not reported | python3 3.9.6: 13 tests passed |
| F4 GREEN (initial; report line 67) | 13 direct-path tests; full command not reported | python3.12 3.12.8: 13 tests passed |
| F4 GREEN (initial; report line 67) | 14 managed tests, 7 shards; full command not reported | python3 3.9.6: each shard ran 2 tests OK |
| F4 GREEN (initial; report line 494) | The new module, `-m unittest discover -s tests -p test_draft_problem_attachment.py` (135) | python3 3.9.6: OK, 2675.6 s |
| F4 GREEN (initial; report line 494) | The new module, `-m unittest discover -s tests -p test_draft_problem_attachment.py` (135) | python3.12 3.12.8: OK, 2390.2 s |
| F4 GREEN (initial; report line 495) | `test_draft` (81) | python3 3.9.6: OK, 4573.1 s |
| F4 GREEN (initial; report line 495) | `test_draft` (81) | python3.12 3.12.8: OK, 4062.6 s |
| F4 GREEN (initial; report line 496) | `test_transport` (101), adapted stubs | python3 3.9.6: OK, 248.8 s |
| F4 GREEN (initial; report line 496) | `test_transport` (101), adapted stubs | python3.12 3.12.8: OK, 227.6 s |
| F4 GREEN (initial; report line 497) | `test_math_problems_cli` (142), adapted stub | python3 3.9.6: OK, 8.7 s |
| F4 GREEN (initial; report line 497) | `test_math_problems_cli` (142), adapted stub | python3.12 3.12.8: OK, 4.8 s |
| F4 GREEN (initial; report line 498) | `test_research_publication` (33) | python3 3.9.6: OK, 1039.6 s |
| F4 GREEN (initial; report line 498) | `test_research_publication` (33) | python3.12 3.12.8: OK, 950.9 s |
| F4 GREEN (initial; report line 499) | `test_research_round_integrity` (12) | python3 3.9.6: OK, 1022.7 s |
| F4 GREEN (initial; report line 499) | `test_research_round_integrity` (12) | python3.12 3.12.8: OK, 932.4 s |
| F4 GREEN (initial; report line 500) | `test_research_source_limited_commands` (10) | python3 3.9.6: OK, 623.2 s |
| F4 GREEN (initial; report line 500) | `test_research_source_limited_commands` (10) | python3.12 3.12.8: OK, 545.5 s |
| F4 GREEN (initial; report line 501) | `test_research_gates` (7) | python3 3.9.6: OK, 92.2 s |
| F4 GREEN (initial; report line 501) | `test_research_gates` (7) | python3.12 3.12.8: OK, 97.2 s |
| F4 GREEN (initial; report line 502) | `test_research_guidance` (9) | python3 3.9.6: OK, 43.6 s |
| F4 GREEN (initial; report line 502) | `test_research_guidance` (9) | python3.12 3.12.8: OK, 42.5 s |
| F4 GREEN (initial; report line 503) | `test_user_authorized_publication` (13) | python3 3.9.6: OK, 22.1 s |
| F4 GREEN (initial; report line 503) | `test_user_authorized_publication` (13) | python3.12 3.12.8: OK, 21.0 s |
| F4 GREEN (initial; report line 504) | `test_workspace_init_retry` (16) | python3 3.9.6: OK, 33.5 s |
| F4 GREEN (initial; report line 504) | `test_workspace_init_retry` (16) | python3.12 3.12.8: OK, 32.7 s |
| F4 GREEN (initial; report line 505) | `test_manifest` (8) | python3 3.9.6: OK, 1.2 s |
| F4 GREEN (initial; report line 505) | `test_manifest` (8) | python3.12 3.12.8: OK, 1.3 s |
| F4 GREEN (initial; report line 506) | `test_codex` (29) | python3 3.9.6: OK, 55.0 s |
| F4 GREEN (initial; report line 506) | `test_codex` (29) | python3.12 3.12.8: OK, 50.9 s |
| F4 GREEN (initial; report line 507) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `python3 codex/generate.py --check` | python3 3.9.6: rc 0 |
| F4 GREEN (initial; report line 507) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `python3 codex/generate.py --check` | python3.12 3.12.8: rc 0 |
| F4 GREEN (initial; report line 600) | The new module, `-m unittest discover -s tests -p test_draft_problem_attachment.py` (137) | python3 3.9.6: OK, 2957.0 s |
| F4 GREEN (initial; report line 600) | The new module, `-m unittest discover -s tests -p test_draft_problem_attachment.py` (137) | python3.12 3.12.8: OK, 2672.5 s |
| F4 GREEN (initial; report line 601) | `test_draft` (81) | python3 3.9.6: OK, 5210.2 s |
| F4 GREEN (initial; report line 601) | `test_draft` (81) | python3.12 3.12.8: OK, 4716.9 s |
| F4 GREEN (initial; report line 602) | `test_transport` (101) | python3 3.9.6: OK, 217.9 s |
| F4 GREEN (initial; report line 602) | `test_transport` (101) | python3.12 3.12.8: OK, 207.7 s |
| F4 GREEN (initial; report line 603) | `test_math_problems_cli` (142) | python3 3.9.6: OK, 6.6 s |
| F4 GREEN (initial; report line 603) | `test_math_problems_cli` (142) | python3.12 3.12.8: OK, 4.2 s |
| F4 GREEN (initial; report line 604) | `test_research_publication` (33) | python3 3.9.6: OK, 1071.5 s |
| F4 GREEN (initial; report line 604) | `test_research_publication` (33) | python3.12 3.12.8: OK, 967.2 s |
| F4 GREEN (initial; report line 605) | `test_research_round_integrity` (12) | python3 3.9.6: OK, 997.7 s |
| F4 GREEN (initial; report line 605) | `test_research_round_integrity` (12) | python3.12 3.12.8: OK, 915.5 s |
| F4 GREEN (initial; report line 606) | `test_research_source_limited_commands` (10) | python3 3.9.6: OK, 560.8 s |
| F4 GREEN (initial; report line 606) | `test_research_source_limited_commands` (10) | python3.12 3.12.8: OK, 522.3 s |
| F4 GREEN (initial; report line 607) | `test_research_gates` (7) | python3 3.9.6: OK, 91.6 s |
| F4 GREEN (initial; report line 607) | `test_research_gates` (7) | python3.12 3.12.8: OK, 88.2 s |
| F4 GREEN (initial; report line 608) | `test_research_guidance` (9) | python3 3.9.6: OK, 44.7 s |
| F4 GREEN (initial; report line 608) | `test_research_guidance` (9) | python3.12 3.12.8: OK, 44.3 s |
| F4 GREEN (initial; report line 609) | `test_user_authorized_publication` (13) | python3 3.9.6: OK, 20.5 s |
| F4 GREEN (initial; report line 609) | `test_user_authorized_publication` (13) | python3.12 3.12.8: OK, 19.5 s |
| F4 GREEN (initial; report line 610) | `test_workspace_init_retry` (16) | python3 3.9.6: OK, 36.2 s |
| F4 GREEN (initial; report line 610) | `test_workspace_init_retry` (16) | python3.12 3.12.8: OK, 35.1 s |
| F4 GREEN (initial; report line 611) | `test_manifest` (8) | python3 3.9.6: OK, 1.5 s |
| F4 GREEN (initial; report line 611) | `test_manifest` (8) | python3.12 3.12.8: OK, 1.6 s |
| F4 GREEN (initial; report line 612) | `test_codex` (29) | python3 3.9.6: OK, 47.6 s |
| F4 GREEN (initial; report line 612) | `test_codex` (29) | python3.12 3.12.8: OK, 46.1 s |
| F4 GREEN (initial; report line 613) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `python3 codex/generate.py --check` | python3 3.9.6: rc 0 |
| F4 GREEN (initial; report line 613) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `python3 codex/generate.py --check` | python3.12 3.12.8: rc 0 |
| F4 CHECK (initial; report line 43) | `python3 codex/generate.py --check` | python3 3.9.6: rc 0 |
| F4 CHECK (initial; report line 43) | `python3 codex/generate.py --check` | python3.12 3.12.8: rc 0 |
| F4 GREEN (Fix round 1; report line 168) | 12 modules, 500 tests: `test_draft` 81, the new module 68, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8 | python3 3.9.6: OK, 10 shards, 1609 to 1889 s each |
| F4 GREEN (Fix round 1; report line 168) | 12 modules, 500 tests: `test_draft` 81, the new module 68, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8 | python3.12 3.12.8: OK, 10 shards, 1446 to 1832 s each |
| F4 GREEN (Fix round 1; report line 169) | The new module in the literal form `-m unittest discover -s tests -p test_draft_problem_attachment.py` | python3 3.9.6: OK, 68 tests in 1308.8 s |
| F4 GREEN (Fix round 1; report line 169) | The new module in the literal form `-m unittest discover -s tests -p test_draft_problem_attachment.py` | python3.12 3.12.8: OK, 68 tests in 1143.9 s |
| F4 GREEN (extra interpreter) (Fix round 1; report line 173) | 53 fast attachment tests; full command not reported | python3.13: 53 tests pass |
| F4 GREEN (Fix round 1; report line 175) | 53 fast tests rerun after the comment edit; full command not reported | python3 3.9.6: 53 tests passed |
| F4 GREEN (Fix round 1; report line 175) | 53 fast tests rerun after the comment edit; full command not reported | python3.12 3.12.8: 53 tests passed |
| F4 CHECK (Fix round 1; report line 170) | `python3 codex/generate.py --check` | python3 3.9.6: rc 0 |
| F4 CHECK (Fix round 1; report line 170) | `python3 codex/generate.py --check` | python3.12 3.12.8: rc 0 |
| F4 CHECK (Fix round 1; report line 171) | CI compile steps | python3 3.9.6: OK |
| F4 CHECK (Fix round 1; report line 171) | CI compile steps | python3.12 3.12.8: OK |
| F4 GREEN (Follow-up; report line 210) | 12 modules, 503 tests in 10 shards; full command not reported | python3 3.9.6: 503 tests pass; 1514 to 2001 s per shard |
| F4 GREEN (Follow-up; report line 210) | 12 modules, 503 tests in 10 shards; full command not reported | python3.12 3.12.8: 503 tests pass; 1357 to 1867 s per shard |
| F4 GREEN (extra interpreter) (Follow-up; report line 210) | 56 fast attachment tests; full command not reported | python3.13: 56 tests pass |
| F4 GREEN (Fix round 2; report line 286) | 12 modules, 538 tests, in 10 shards: `test_draft` 81, the new module 106, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8 | python3 3.9.6: OK, 1648 to 2122 s per shard |
| F4 GREEN (Fix round 2; report line 286) | 12 modules, 538 tests, in 10 shards: `test_draft` 81, the new module 106, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8 | python3.12 3.12.8: OK, 1473 to 2015 s per shard |
| F4 GREEN (Fix round 2; report line 287) | The final new module (106 tests, after the last edit of the test file) in 11 shards | python3 3.9.6: OK, 468 to 496 s |
| F4 GREEN (Fix round 2; report line 287) | The final new module (106 tests, after the last edit of the test file) in 11 shards | python3.12 3.12.8: OK, 404 to 486 s |
| F4 GREEN (Fix round 2; report line 288) | Literal form `-m unittest discover -s tests -p <module>.py`: the new module (106) | python3 3.9.6: OK, 4230 s of test time |
| F4 GREEN (Fix round 2; report line 288) | Literal form `-m unittest discover -s tests -p <module>.py`: the new module (106) | python3.12 3.12.8: OK, 3961 s |
| F4 GREEN (Fix round 2; report line 289) | The same form: `test_transport` (101) | python3 3.9.6: OK, 184 s |
| F4 GREEN (Fix round 2; report line 289) | The same form: `test_transport` (101) | python3.12 3.12.8: OK, 169 s |
| F4 GREEN (Fix round 2; report line 290) | The same form: `test_math_problems_cli` (142) | python3 3.9.6: OK, 7 s |
| F4 GREEN (Fix round 2; report line 290) | The same form: `test_math_problems_cli` (142) | python3.12 3.12.8: OK, 4 s |
| F4 GREEN (Fix round 2; report line 291) | The same form: `test_research_publication` (33) | python3 3.9.6: OK, 840 s |
| F4 GREEN (Fix round 2; report line 291) | The same form: `test_research_publication` (33) | python3.12 3.12.8: OK, 759 s |
| F4 GREEN (Fix round 2; report line 292) | The same form: `test_research_round_integrity` (12) | python3 3.9.6: OK, 830 s |
| F4 GREEN (Fix round 2; report line 292) | The same form: `test_research_round_integrity` (12) | python3.12 3.12.8: OK, 747 s |
| F4 GREEN (Fix round 2; report line 293) | The same form: `test_research_source_limited_commands` (10) | python3 3.9.6: OK, 463 s |
| F4 GREEN (Fix round 2; report line 293) | The same form: `test_research_source_limited_commands` (10) | python3.12 3.12.8: OK, 425 s |
| F4 GREEN (Fix round 2; report line 294) | The same form: `test_draft` (81) | python3 3.9.6: OK, 4285 s |
| F4 GREEN (Fix round 2; report line 294) | The same form: `test_draft` (81) | python3.12 3.12.8: OK, 3769 s |
| F4 GREEN (Fix round 2; report line 295) | `python3 codex/generate.py --check` and the CI compile steps (`py_compile bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) | python3 3.9.6: rc 0 |
| F4 GREEN (Fix round 2; report line 295) | `python3 codex/generate.py --check` and the CI compile steps (`py_compile bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) | python3.12 3.12.8: rc 0 |
| F4 GREEN (extra interpreter) (Fix round 2; report line 297) | 84 fast attachment tests; full command not reported | python3.13: 84 tests pass |
| F4 GREEN (Fix round 2; report line 297) | `test_ci_shards`; full command not reported | 9 tests pass; interpreter not specified |
| F4 GREEN (Fix round 3; report line 363) | 12 modules, 558 tests, in 10 shards: `test_draft` 81, the new module 126, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8 | python3 3.9.6: OK, 2333 to 3018 s per shard |
| F4 GREEN (Fix round 3; report line 363) | 12 modules, 558 tests, in 10 shards: `test_draft` 81, the new module 126, `test_research_publication` 33, `test_research_round_integrity` 12, `test_transport` 101, `test_math_problems_cli` 142, `test_research_source_limited_commands` 10, `test_research_gates` 7, `test_research_guidance` 9, `test_user_authorized_publication` 13, `test_workspace_init_retry` 16, `test_manifest` 8 | python3.12 3.12.8: OK, 2133 to 2900 s per shard |
| F4 GREEN (Fix round 3; report line 364) | Literal form `-m unittest discover -s tests -p <module>.py`: the new module (126) | python3 3.9.6: OK, 4268 s of test time |
| F4 GREEN (Fix round 3; report line 364) | Literal form `-m unittest discover -s tests -p <module>.py`: the new module (126) | python3.12 3.12.8: OK, 3959 s |
| F4 GREEN (Fix round 3; report line 365) | The same form: `test_draft` (81) | python3 3.9.6: OK, 6110 s |
| F4 GREEN (Fix round 3; report line 365) | The same form: `test_draft` (81) | python3.12 3.12.8: OK, 5622 s |
| F4 GREEN (Fix round 3; report line 366) | The same form: `test_transport` (101), unchanged file | python3 3.9.6: OK, 529 s |
| F4 GREEN (Fix round 3; report line 366) | The same form: `test_transport` (101), unchanged file | python3.12 3.12.8: OK, 422 s |
| F4 GREEN (Fix round 3; report line 367) | The same form: `test_math_problems_cli` (142), unchanged file | python3 3.9.6: OK, 7 s |
| F4 GREEN (Fix round 3; report line 367) | The same form: `test_math_problems_cli` (142), unchanged file | python3.12 3.12.8: OK, 8 s |
| F4 GREEN (Fix round 3; report line 368) | The same form: `test_research_publication` (33) | python3 3.9.6: OK, 2616 s |
| F4 GREEN (Fix round 3; report line 368) | The same form: `test_research_publication` (33) | python3.12 3.12.8: OK, 2397 s |
| F4 GREEN (Fix round 3; report line 369) | The same form: `test_research_round_integrity` (12) | python3 3.9.6: OK, 2509 s |
| F4 GREEN (Fix round 3; report line 369) | The same form: `test_research_round_integrity` (12) | python3.12 3.12.8: OK, 2302 s |
| F4 GREEN (Fix round 3; report line 370) | The same form: `test_research_source_limited_commands` (10) | python3 3.9.6: OK, 1375 s |
| F4 GREEN (Fix round 3; report line 370) | The same form: `test_research_source_limited_commands` (10) | python3.12 3.12.8: OK, 1277 s |
| F4 GREEN (Fix round 3; report line 371) | The same form: `test_user_authorized_publication` (13), `test_manifest` (8), `test_codex` (29) | python3 3.9.6: OK |
| F4 GREEN (Fix round 3; report line 371) | The same form: `test_user_authorized_publication` (13), `test_manifest` (8), `test_codex` (29) | python3.12 3.12.8: OK |
| F4 GREEN (Fix round 3; report line 372) | `python3 codex/generate.py --check` and the CI compile steps (`py_compile bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) | python3 3.9.6: rc 0 |
| F4 GREEN (Fix round 3; report line 372) | `python3 codex/generate.py --check` and the CI compile steps (`py_compile bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) | python3.12 3.12.8: rc 0 |
| F4 GREEN (extra interpreter) (Fix round 3; report line 374) | 101 fast attachment tests; full command not reported | python3.13: 101 tests pass |
| F4 RED (Fix round 4; report line 442) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestSameOriginRedirects test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestZenodoTokenOrigin test_draft_problem_attachment.TestPlantedSettledJournal test_draft_problem_attachment.TestProblemDepositJournalStates test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestDirectDepositForAProblem.test_a_rerun_after_a_lost_publish_reads_the_deposition_back_and_creates_nothing test_draft_problem_attachment.TestDirectDepositForAProblem.test_an_identical_rerun_attaches_the_recorded_record_and_publishes_nothing_new` | python3 3.9.6: 60 tests; FAILED (failures=48, errors=7) |
| F4 RED (Fix round 4; report line 442) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestSameOriginRedirects test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestZenodoTokenOrigin test_draft_problem_attachment.TestPlantedSettledJournal test_draft_problem_attachment.TestProblemDepositJournalStates test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestDirectDepositForAProblem.test_a_rerun_after_a_lost_publish_reads_the_deposition_back_and_creates_nothing test_draft_problem_attachment.TestDirectDepositForAProblem.test_an_identical_rerun_attaches_the_recorded_record_and_publishes_nothing_new` | python3.12 3.12.8: 60 tests; FAILED (failures=48, errors=6) |
| F4 RED (Fix round 4; report line 464) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_lost_publish_response_is_read_back_by_the_same_command_and_then_attached` | python3 3.9.6: FAILED (failures=1) |
| F4 RED (Fix round 4; report line 464) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_lost_publish_response_is_read_back_by_the_same_command_and_then_attached` | python3.12 3.12.8: FAILED (failures=1) |
| F4 RED (Fix round 4; report line 464) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_reconcile_during_the_release_of_an_unclaimed_journal_is_refused_and_one_record_is_published` | python3 3.9.6: FAILED (failures=1) |
| F4 RED (Fix round 4; report line 464) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_reconcile_during_the_release_of_an_unclaimed_journal_is_refused_and_one_record_is_published` | python3.12 3.12.8: FAILED (failures=1) |
| F4 RED (mutation) (Fix round 4; report line 470) | `test_the_release_exception_of_the_origin_check_covers_an_unsettled_journal_only`; full command not reported | The mutant without the settled condition fails this test |
| F4 GREEN (Fix round 4; report line 468) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestSameOriginRedirects test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestZenodoTokenOrigin test_draft_problem_attachment.TestPlantedSettledJournal test_draft_problem_attachment.TestProblemDepositJournalStates test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestDirectDepositForAProblem.test_a_rerun_after_a_lost_publish_reads_the_deposition_back_and_creates_nothing test_draft_problem_attachment.TestDirectDepositForAProblem.test_an_identical_rerun_attaches_the_recorded_record_and_publishes_nothing_new` | python3 3.9.6: 60 tests OK |
| F4 GREEN (Fix round 4; report line 468) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestSameOriginRedirects test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestZenodoTokenOrigin test_draft_problem_attachment.TestPlantedSettledJournal test_draft_problem_attachment.TestProblemDepositJournalStates test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestDirectDepositForAProblem.test_a_rerun_after_a_lost_publish_reads_the_deposition_back_and_creates_nothing test_draft_problem_attachment.TestDirectDepositForAProblem.test_an_identical_rerun_attaches_the_recorded_record_and_publishes_nothing_new` | python3.12 3.12.8: 60 tests OK |
| F4 GREEN (Fix round 4; report line 468) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_lost_publish_response_is_read_back_by_the_same_command_and_then_attached` | python3 3.9.6: OK |
| F4 GREEN (Fix round 4; report line 468) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_lost_publish_response_is_read_back_by_the_same_command_and_then_attached` | python3.12 3.12.8: OK |
| F4 GREEN (Fix round 4; report line 468) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_reconcile_during_the_release_of_an_unclaimed_journal_is_refused_and_one_record_is_published` | python3 3.9.6: OK |
| F4 GREEN (Fix round 4; report line 468) | `PYTHONPATH=tests <python> -X dev -W error -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_reconcile_during_the_release_of_an_unclaimed_journal_is_refused_and_one_record_is_published` | python3.12 3.12.8: OK |
| F4 GREEN (Fix round 4; report line 470) | `test_the_release_exception_of_the_origin_check_covers_an_unsettled_journal_only`; full command not reported | python3 3.9.6: Passes on the real tree |
| F4 GREEN (Fix round 4; report line 470) | `test_the_release_exception_of_the_origin_check_covers_an_unsettled_journal_only`; full command not reported | python3.12 3.12.8: Passes on the real tree |
| F4 GREEN (extra interpreter) (Fix round 4; report line 509) | 110 fast attachment tests; full command not reported | python3.13: 110 tests pass |
| F4 RED (Fix round 5; report line 565) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestProblemDepositJournalStates` | python3 3.9.6: 42 tests; FAILED (failures=36) |
| F4 RED (Fix round 5; report line 565) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestProblemDepositJournalStates` | python3.12 3.12.8: 42 tests; FAILED (failures=36) |
| F4 GREEN (Fix round 5; report line 580) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestProblemDepositJournalStates` | python3 3.9.6: 42 tests OK |
| F4 GREEN (Fix round 5; report line 580) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest -v test_draft_problem_attachment.TestExactoryRedirects test_draft_problem_attachment.TestExactoryRequestRedirects test_draft_problem_attachment.TestProblemDepositJournalStates` | python3.12 3.12.8: 42 tests OK |
| F4 GREEN (Fix round 5; report line 580) | 112 fast attachment tests, `test_transport` (101), `test_math_problems_cli` (142); full command not repeated | python3 3.9.6: All OK |
| F4 GREEN (Fix round 5; report line 580) | 112 fast attachment tests, `test_transport` (101), `test_math_problems_cli` (142); full command not repeated | python3.12 3.12.8: All OK |
| F4 GREEN (extra interpreter) (Fix round 5; report line 615) | 112 fast attachment tests; full command not reported | python3.13: 112 tests pass |
| F5 GREEN | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 256 tests OK in 198.399 s; no skips |
| F5 GREEN | `PYTHONPATH=tests python3 -B tests/smoke_codex.py --codex /Users/ryshiro/.nvm/versions/node/v24.18.0/bin/codex --output <scratch-log.json>` | PASS: installed plugin, 16 skills, 11 hooks, no load errors |
| F5 GREEN | `PYTHONPATH=tests python3.12 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 256 tests OK in 175.633 s; no skips |
| F5 GREEN | `PYTHONPATH=tests python3.12 -B tests/smoke_codex.py --codex /Users/ryshiro/.nvm/versions/node/v24.18.0/bin/codex --output <scratch-log.json>` | PASS: installed plugin, 16 skills, 11 hooks, no load errors |
| F5 GREEN (before the final wording edit) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex` | 46 tests OK in 88.057 s; no skips |
| F5 GREEN (final full run) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 256 tests OK in 444.801 s; no skips |
| F5 GREEN (after the final wording edit) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex` | 46 tests OK in 135.996 s; no skips |
| F5 GREEN (final host smoke) | `PYTHONPATH=tests python3 -B tests/smoke_codex.py --codex /Users/ryshiro/.nvm/versions/node/v24.18.0/bin/codex --output <scratch-log.json>` | PASS: installed plugin, 16 skills, 11 hooks, no load errors |
| F5 GREEN (before the final wording edit) | `PYTHONPATH=tests python3.12 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex` | 46 tests OK in 84.094 s; no skips |
| F5 GREEN (final full run) | `PYTHONPATH=tests python3.12 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 256 tests OK in 328.277 s; no skips |
| F5 GREEN (after the final wording edit) | `PYTHONPATH=tests python3.12 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex` | 46 tests OK in 132.712 s; no skips |
| F5 GREEN (final host smoke) | `PYTHONPATH=tests python3.12 -B tests/smoke_codex.py --codex /Users/ryshiro/.nvm/versions/node/v24.18.0/bin/codex --output <scratch-log.json>` | PASS: installed plugin, 16 skills, 11 hooks, no load errors |
| F6 RED (initial; report line 146) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest test_math_problems_cli test_transport` | python3 3.9.6: 264 tests; FAILED (failures=77, errors=27) |
| F6 RED (initial; report line 146) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest test_math_problems_cli test_transport` | python3.12 3.12.8: 264 tests; FAILED (failures=77, errors=27) |
| F6 RED (initial; report line 161) | `... PYTHONPATH=tests python3 -X dev -W error -m unittest test_draft.TestDirectDeposit test_draft.TestZenodoAnswersThatCannotBeRead test_draft.TestManagedDepositAfterALostCreateResponse test_draft_problem_attachment.TestProblemDepositJournalStates.test_a_managed_journal_whose_intent_the_store_does_not_give_names_the_removal_rule_and_the_way_out test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestAttachmentWhoseAnswerDidNotArriveInFull` | python3 3.9.6: 45 tests; FAILED (failures=13, errors=5) |
| F6 RED (initial; report line 161) | `... PYTHONPATH=tests python3 -X dev -W error -m unittest test_draft.TestDirectDeposit test_draft.TestZenodoAnswersThatCannotBeRead test_draft.TestManagedDepositAfterALostCreateResponse test_draft_problem_attachment.TestProblemDepositJournalStates.test_a_managed_journal_whose_intent_the_store_does_not_give_names_the_removal_rule_and_the_way_out test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestAttachmentWhoseAnswerDidNotArriveInFull` | python3.12 3.12.8: 45 tests; FAILED (failures=13, errors=5) |
| F6 GREEN ATTEMPT (initial; report line 174) | First CLI run after implementation; full command not repeated | 1 error, released lone-surrogate citation test |
| F6 GREEN (initial; report line 173) | `... PYTHONPATH=tests python3 -X dev -W error -m unittest test_draft.TestDirectDeposit test_draft.TestZenodoAnswersThatCannotBeRead test_draft.TestManagedDepositAfterALostCreateResponse test_draft_problem_attachment.TestProblemDepositJournalStates.test_a_managed_journal_whose_intent_the_store_does_not_give_names_the_removal_rule_and_the_way_out test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestAttachmentWhoseAnswerDidNotArriveInFull` | python3 3.9.6: 45 tests OK |
| F6 GREEN (initial; report line 173) | `... PYTHONPATH=tests python3 -X dev -W error -m unittest test_draft.TestDirectDeposit test_draft.TestZenodoAnswersThatCannotBeRead test_draft.TestManagedDepositAfterALostCreateResponse test_draft_problem_attachment.TestProblemDepositJournalStates.test_a_managed_journal_whose_intent_the_store_does_not_give_names_the_removal_rule_and_the_way_out test_draft_problem_attachment.TestExplainUnsettledJournal test_draft_problem_attachment.TestAttachmentWhoseAnswerDidNotArriveInFull` | python3.12 3.12.8: 45 tests OK |
| F6 GREEN (initial; report line 175) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest test_math_problems_cli test_transport` | python3 3.9.6: 265 tests OK after the citation expectation changed |
| F6 GREEN (initial; report line 175) | `env -u EXACTORY_API_KEY -u EXACTORY_API_URL -u ZENODO_TOKEN -u ZENODO_SANDBOX_TOKEN PYTHONPATH=tests python3 -X dev -W error -m unittest test_math_problems_cli test_transport` | python3.12 3.12.8: 265 tests OK after the citation expectation changed |
| F6 GREEN (initial; report line 224) | `test_draft_problem_attachment` | python3 3.9.6: OK, 139 tests, 3398.1 s |
| F6 GREEN (initial; report line 224) | `test_draft_problem_attachment` | python3.12 3.12.8: OK, 139 tests, 3078.5 s |
| F6 GREEN (initial; report line 225) | `test_draft` | python3 3.9.6: OK, 89 tests, 6099.4 s |
| F6 GREEN (initial; report line 225) | `test_draft` | python3.12 3.12.8: OK, 89 tests, 5385.4 s |
| F6 GREEN (initial; report line 226) | `test_transport` | python3 3.9.6: OK, 108 tests, 342.0 s |
| F6 GREEN (initial; report line 226) | `test_transport` | python3.12 3.12.8: OK, 108 tests, 289.5 s |
| F6 GREEN (initial; report line 227) | `test_math_problems_cli` | python3 3.9.6: OK, 157 tests, 8.8 s |
| F6 GREEN (initial; report line 227) | `test_math_problems_cli` | python3.12 3.12.8: OK, 157 tests, 5.1 s |
| F6 GREEN (initial; report line 228) | `test_research_publication` | python3 3.9.6: OK, 33 tests, 1538.5 s |
| F6 GREEN (initial; report line 228) | `test_research_publication` | python3.12 3.12.8: OK, 33 tests, 1434.7 s |
| F6 GREEN (initial; report line 229) | `test_research_round_integrity` | python3 3.9.6: OK, 12 tests, 1427.5 s |
| F6 GREEN (initial; report line 229) | `test_research_round_integrity` | python3.12 3.12.8: OK, 12 tests, 1328.8 s |
| F6 GREEN (initial; report line 230) | `test_research_source_limited_commands` | python3 3.9.6: OK, 10 tests, 764.5 s |
| F6 GREEN (initial; report line 230) | `test_research_source_limited_commands` | python3.12 3.12.8: OK, 10 tests, 733.9 s |
| F6 GREEN (initial; report line 231) | `test_research_gates` | python3 3.9.6: OK, 7 tests, 147.5 s |
| F6 GREEN (initial; report line 231) | `test_research_gates` | python3.12 3.12.8: OK, 7 tests, 137.1 s |
| F6 GREEN (initial; report line 232) | `test_research_guidance` | python3 3.9.6: OK, 9 tests, 61.7 s |
| F6 GREEN (initial; report line 232) | `test_research_guidance` | python3.12 3.12.8: OK, 9 tests, 61.2 s |
| F6 GREEN (initial; report line 233) | `test_user_authorized_publication` | python3 3.9.6: OK, 13 tests, 33.7 s |
| F6 GREEN (initial; report line 233) | `test_user_authorized_publication` | python3.12 3.12.8: OK, 13 tests, 32.3 s |
| F6 GREEN (initial; report line 234) | `test_workspace_init_retry` | python3 3.9.6: OK, 16 tests, 49.8 s |
| F6 GREEN (initial; report line 234) | `test_workspace_init_retry` | python3.12 3.12.8: OK, 16 tests, 48.1 s |
| F6 GREEN (initial; report line 235) | `test_manifest` | python3 3.9.6: OK, 8 tests, 2.5 s |
| F6 GREEN (initial; report line 235) | `test_manifest` | python3.12 3.12.8: OK, 8 tests, 3.0 s |
| F6 GREEN (initial; report line 236) | `test_codex` | python3 3.9.6: OK, 29 tests, 77.4 s |
| F6 GREEN (initial; report line 236) | `test_codex` | python3.12 3.12.8: OK, 29 tests, 72.2 s |
| F6 GREEN (initial; report line 237) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `codex/generate.py --check` | python3 3.9.6: rc 0 |
| F6 GREEN (initial; report line 237) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `codex/generate.py --check` | python3.12 3.12.8: rc 0 |
| F6 RED (Fix round 1; report line 469) | CLI set: 8 classes of `test_math_problems_cli` and `test_transport`; full command not reported | python3 3.9.6: 49 tests; FAILED (failures=63, errors=18) |
| F6 RED (Fix round 1; report line 469) | Draft set: `TestDirectDeposit`, `TestZenodoAnswersThatCannotBeRead`, `TestManagedDepositAfterALostCreateResponse`; full command not reported | python3 3.9.6: 39 tests; FAILED (failures=25, errors=8) |
| F6 RED (Fix round 1; report line 469) | Attachment set: 6 classes and tests; full command not reported | python3 3.9.6: 19 tests; FAILED (failures=5, errors=3) |
| F6 RED (verification stub) (Fix round 1; report line 533) | `test_transport` | python3 3.9.6: first run FAILED (errors=2) of 113 |
| F6 RED (verification stub) (Fix round 1; report line 533) | `test_transport` | python3.12 3.12.8: first run FAILED (errors=2) of 113 |
| F6 GREEN (Fix round 1; report line 477) | CLI set: 8 classes of `test_math_problems_cli` and `test_transport`; full command not reported | python3 3.9.6: 49 tests OK |
| F6 GREEN (Fix round 1; report line 477) | CLI set: 8 classes of `test_math_problems_cli` and `test_transport`; full command not reported | python3.12 3.12.8: 49 tests OK |
| F6 GREEN (Fix round 1; report line 477) | Draft set: `TestDirectDeposit`, `TestZenodoAnswersThatCannotBeRead`, `TestManagedDepositAfterALostCreateResponse`; full command not reported | python3 3.9.6: 40 tests OK |
| F6 GREEN (Fix round 1; report line 477) | Draft set: `TestDirectDeposit`, `TestZenodoAnswersThatCannotBeRead`, `TestManagedDepositAfterALostCreateResponse`; full command not reported | python3.12 3.12.8: 40 tests OK |
| F6 GREEN (Fix round 1; report line 477) | Attachment set: 6 classes and tests; full command not reported | python3 3.9.6: 19 tests OK |
| F6 GREEN (Fix round 1; report line 477) | Attachment set: 6 classes and tests; full command not reported | python3.12 3.12.8: 19 tests OK |
| F6 GREEN (final refinement) (Fix round 1; report line 478) | CLI set: 8 classes of `test_math_problems_cli` and `test_transport`; full command not reported | python3 3.9.6: 49 tests OK |
| F6 GREEN (final refinement) (Fix round 1; report line 478) | Draft set: `TestDirectDeposit`, `TestZenodoAnswersThatCannotBeRead`, `TestManagedDepositAfterALostCreateResponse`; full command not reported | python3 3.9.6: 40 tests OK |
| F6 GREEN (final refinement) (Fix round 1; report line 478) | Attachment set with all of `TestDirectDepositForAProblem`; full command not reported | python3 3.9.6: 43 tests OK |
| F6 GREEN (extra interpreter) (Fix round 1; report line 479) | CLI set; full command not reported | python3.13 3.13.8: 49 tests pass |
| F6 GREEN (extra interpreter) (Fix round 1; report line 479) | Draft set; full command not reported | python3.13 3.13.8: 40 tests pass |
| F6 GREEN (extra interpreter) (Fix round 1; report line 479) | Attachment set; full command not reported | python3.13 3.13.8: 43 tests pass |
| F6 GREEN (Fix round 1; report line 484) | `TestTasksSearchFlags`; full command not reported | python3 3.9.6: OK after the stub fix |
| F6 GREEN (Fix round 1; report line 484) | `TestTasksSearchFlags`; full command not reported | python3.12 3.12.8: OK after the stub fix |
| F6 GREEN (Fix round 1; report line 531) | `test_draft_problem_attachment` | python3 3.9.6: OK, 142 tests, 3321.3 s |
| F6 GREEN (Fix round 1; report line 531) | `test_draft_problem_attachment` | python3.12 3.12.8: OK, 142 tests, 3048.0 s |
| F6 GREEN (Fix round 1; report line 532) | `test_draft` | python3 3.9.6: OK, 92 tests, 5452.3 s |
| F6 GREEN (Fix round 1; report line 532) | `test_draft` | python3.12 3.12.8: OK, 92 tests, 4922.5 s |
| F6 GREEN (Fix round 1; report line 533) | `test_transport` | python3 3.9.6: OK, 113 tests, 280.0 s |
| F6 GREEN (Fix round 1; report line 533) | `test_transport` | python3.12 3.12.8: OK, 113 tests, 256.0 s |
| F6 GREEN (Fix round 1; report line 534) | `test_math_problems_cli` | python3 3.9.6: OK, 157 tests, 11.8 s |
| F6 GREEN (Fix round 1; report line 534) | `test_math_problems_cli` | python3.12 3.12.8: OK, 157 tests, 6.5 s |
| F6 GREEN (Fix round 1; report line 535) | `test_research_publication` | python3 3.9.6: OK, 33 tests, 1589.3 s |
| F6 GREEN (Fix round 1; report line 535) | `test_research_publication` | python3.12 3.12.8: OK, 33 tests, 1485.4 s |
| F6 GREEN (Fix round 1; report line 536) | `test_research_round_integrity` | python3 3.9.6: OK, 12 tests, 1489.0 s |
| F6 GREEN (Fix round 1; report line 536) | `test_research_round_integrity` | python3.12 3.12.8: OK, 12 tests, 1339.4 s |
| F6 GREEN (Fix round 1; report line 537) | `test_research_source_limited_commands` | python3 3.9.6: OK, 10 tests, 668.1 s |
| F6 GREEN (rerun) (Fix round 1; report line 537) | `test_research_source_limited_commands` | python3 3.9.6: OK, 688.2 s |
| F6 GREEN (Fix round 1; report line 537) | `test_research_source_limited_commands` | python3.12 3.12.8: OK, 10 tests, 648.9 s |
| F6 GREEN (rerun) (Fix round 1; report line 537) | `test_research_source_limited_commands` | python3.12 3.12.8: OK, 647.3 s |
| F6 GREEN (Fix round 1; report line 538) | `test_research_gates` | python3 3.9.6: OK, 7 tests, 120.0 s |
| F6 GREEN (Fix round 1; report line 538) | `test_research_gates` | python3.12 3.12.8: OK, 7 tests, 124.3 s |
| F6 GREEN (Fix round 1; report line 539) | `test_research_guidance` | python3 3.9.6: OK, 9 tests, 50.5 s |
| F6 GREEN (Fix round 1; report line 539) | `test_research_guidance` | python3.12 3.12.8: OK, 9 tests, 49.7 s |
| F6 GREEN (Fix round 1; report line 540) | `test_user_authorized_publication` | python3 3.9.6: OK, 13 tests, 30.4 s |
| F6 GREEN (rerun) (Fix round 1; report line 540) | `test_user_authorized_publication` | python3 3.9.6: OK, 27.3 s |
| F6 GREEN (Fix round 1; report line 540) | `test_user_authorized_publication` | python3.12 3.12.8: OK, 13 tests, 30.1 s |
| F6 GREEN (rerun) (Fix round 1; report line 540) | `test_user_authorized_publication` | python3.12 3.12.8: OK, 26.2 s |
| F6 GREEN (Fix round 1; report line 541) | `test_workspace_init_retry` | python3 3.9.6: OK, 16 tests, 48.8 s |
| F6 GREEN (Fix round 1; report line 541) | `test_workspace_init_retry` | python3.12 3.12.8: OK, 16 tests, 48.6 s |
| F6 GREEN (Fix round 1; report line 542) | `test_manifest` | python3 3.9.6: OK, 8 tests, 2.4 s |
| F6 GREEN (Fix round 1; report line 542) | `test_manifest` | python3.12 3.12.8: OK, 8 tests, 1.9 s |
| F6 GREEN (Fix round 1; report line 543) | `test_codex` | python3 3.9.6: OK, 29 tests, 63.7 s |
| F6 GREEN (Fix round 1; report line 543) | `test_codex` | python3.12 3.12.8: OK, 29 tests, 64.0 s |
| F6 GREEN (Fix round 1; report line 544) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `codex/generate.py --check` | python3 3.9.6: rc 0 |
| F6 GREEN (Fix round 1; report line 544) | CI compile steps (`py_compile` of every `bin/*`, `compileall -q ci hooks codex research_harness tests skills/math-solver/harness`) and `codex/generate.py --check` | python3.12 3.12.8: rc 0 |

## Later command rounds: F6 rounds 2 and 3

Sources: `f6-round2-report.md` and `f6-round3-report.md` in the supervisor's scratchpad `lanes/` directory.
Supervisor loopback results come from `progress.md` in the app worktree's `.superpowers/sdd/PLAN/` directory.
Each row names its source and line. These are source-reported runs, not tests run by F5.
Module rows are components of the reported full-module runs. A missing full command stays a description.
`SAFE` retains the source report's PATH export, four credential removals, `PYTHONPATH=tests`, and any stated writable cache prefix.
Historical commands retain `test_f6_round2`; round 3 renamed it to `test_request_outcome_advice`.
The round 2 raw-text behavior was superseded by round 3's trim rule. Earlier results remain historical evidence.

| Task and source | Command or reported run | Observed result |
| --- | --- | --- |
| F6 round 2 GREEN (f6-round2-report.md:135) | `SAFE python3 -X dev -W error -m unittest test_f6_round2 test_math_problems_cli test_transport -v` | python3 / 3.9.6: 304 tests; OK, 262.096s |
| F6 round 2 GREEN (f6-round2-report.md:135) | `SAFE python3.12 -X dev -W error -m unittest test_f6_round2 test_math_problems_cli test_transport -v` | python3.12 / 3.12.8: 304 tests; OK, 239.327s |
| F6 round 2 GREEN (f6-round2-report.md:136) | `test_draft`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 92 tests; OK, 5262.214s |
| F6 round 2 GREEN (f6-round2-report.md:136) | `test_draft`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 92 tests; OK, 4713.682s |
| F6 round 2 GREEN (f6-round2-report.md:137) | `test_draft_problem_attachment` (socket-free cases); `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 122 tests; OK, 2990.624s |
| F6 round 2 GREEN (f6-round2-report.md:137) | `test_draft_problem_attachment` (socket-free cases); `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 122 tests; OK, 2609.211s |
| F6 round 2 GREEN (f6-round2-report.md:138) | `test_research_publication`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 33 tests; OK, 1138.949s |
| F6 round 2 GREEN (f6-round2-report.md:138) | `test_research_publication`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 33 tests; OK, 1024.024s |
| F6 round 2 GREEN (f6-round2-report.md:139) | `test_research_round_integrity`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 12 tests; OK, 1096.203s |
| F6 round 2 GREEN (f6-round2-report.md:139) | `test_research_round_integrity`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 12 tests; OK, 982.493s |
| F6 round 2 GREEN (f6-round2-report.md:140) | `test_research_source_limited_commands`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 10 tests; OK, 530.342s |
| F6 round 2 GREEN (f6-round2-report.md:140) | `test_research_source_limited_commands`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 10 tests; OK, 504.683s |
| F6 round 2 GREEN (f6-round2-report.md:141) | `test_research_gates`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 7 tests; OK, 46.052s |
| F6 round 2 GREEN (f6-round2-report.md:141) | `test_research_gates`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 7 tests; OK, 47.875s |
| F6 round 2 GREEN (f6-round2-report.md:142) | `test_research_guidance`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 9 tests; OK, 31.467s |
| F6 round 2 GREEN (f6-round2-report.md:142) | `test_research_guidance`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 9 tests; OK, 35.002s |
| F6 round 2 GREEN (f6-round2-report.md:143) | `test_user_authorized_publication`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 13 tests; OK, 24.436s |
| F6 round 2 GREEN (f6-round2-report.md:143) | `test_user_authorized_publication`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 13 tests; OK, 8.102s |
| F6 round 2 GREEN (f6-round2-report.md:144) | `test_workspace_init_retry`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 16 tests; OK, 45.968s |
| F6 round 2 GREEN (f6-round2-report.md:144) | `test_workspace_init_retry`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 16 tests; OK, 26.562s |
| F6 round 2 GREEN (f6-round2-report.md:145) | `test_manifest`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 8 tests; OK, 1.419s |
| F6 round 2 GREEN (f6-round2-report.md:145) | `test_manifest`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 8 tests; OK, 0.447s |
| F6 round 2 GREEN (f6-round2-report.md:146) | `test_codex`; `SAFE python3 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3 / 3.9.6: 29 tests; OK, 70.374s |
| F6 round 2 GREEN (f6-round2-report.md:146) | `test_codex`; `SAFE python3.12 -X dev -W error /private/tmp/f6_run_suite.py <module>` | python3.12 / 3.12.8: 29 tests; OK, 20.315s |
| F6 round 3 GREEN (f6-round3-report.md:350) | `test_draft_problem_attachment`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 132 tests, 2281.426s, OK |
| F6 round 3 GREEN (f6-round3-report.md:350) | `test_draft_problem_attachment`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 132 tests, 1742.043s, OK |
| F6 round 3 GREEN (f6-round3-report.md:351) | `test_draft`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 92 tests, 4183.650s, OK |
| F6 round 3 GREEN (f6-round3-report.md:351) | `test_draft`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 92 tests, 3137.223s, OK |
| F6 round 3 GREEN (f6-round3-report.md:352) | `test_transport`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 113 tests, 131.589s, OK |
| F6 round 3 GREEN (f6-round3-report.md:352) | `test_transport`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 113 tests, 89.582s, OK |
| F6 round 3 GREEN (f6-round3-report.md:353) | `test_math_problems_cli`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 167 tests, 6.792s, OK |
| F6 round 3 GREEN (f6-round3-report.md:353) | `test_math_problems_cli`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 167 tests, 3.318s, OK |
| F6 round 3 GREEN (f6-round3-report.md:354) | `test_research_publication`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 33 tests, 638.364s, OK |
| F6 round 3 GREEN (f6-round3-report.md:354) | `test_research_publication`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 33 tests, 545.417s, OK |
| F6 round 3 GREEN (f6-round3-report.md:355) | `test_research_round_integrity`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 12 tests, 695.927s, OK |
| F6 round 3 GREEN (f6-round3-report.md:355) | `test_research_round_integrity`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 12 tests, 504.900s, OK |
| F6 round 3 GREEN (f6-round3-report.md:356) | `test_research_source_limited_commands`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 10 tests, 464.585s, OK |
| F6 round 3 GREEN (f6-round3-report.md:356) | `test_research_source_limited_commands`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 10 tests, 264.318s, OK |
| F6 round 3 GREEN (f6-round3-report.md:357) | `test_research_gates`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 7 tests, 29.693s, OK |
| F6 round 3 GREEN (f6-round3-report.md:357) | `test_research_gates`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 7 tests, 24.730s, OK |
| F6 round 3 GREEN (f6-round3-report.md:358) | `test_research_guidance`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 9 tests, 18.183s, OK |
| F6 round 3 GREEN (f6-round3-report.md:358) | `test_research_guidance`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 9 tests, 27.783s, OK |
| F6 round 3 GREEN (f6-round3-report.md:359) | `test_user_authorized_publication`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 13 tests, 5.886s, OK |
| F6 round 3 GREEN (f6-round3-report.md:359) | `test_user_authorized_publication`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 13 tests, 6.103s, OK |
| F6 round 3 GREEN (f6-round3-report.md:360) | `test_workspace_init_retry`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 16 tests, 8.790s, OK |
| F6 round 3 GREEN (f6-round3-report.md:360) | `test_workspace_init_retry`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 16 tests, 9.878s, OK |
| F6 round 3 GREEN (f6-round3-report.md:361) | `test_manifest`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 8 tests, 0.397s, OK |
| F6 round 3 GREEN (f6-round3-report.md:361) | `test_manifest`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 8 tests, 0.431s, OK |
| F6 round 3 GREEN (f6-round3-report.md:362) | `test_codex`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 29 tests, 11.261s, OK |
| F6 round 3 GREEN (f6-round3-report.md:362) | `test_codex`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 29 tests, 11.057s, OK |
| F6 round 3 GREEN (f6-round3-report.md:363) | `test_request_outcome_advice`; component of `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: 34 tests, 0.372s, OK |
| F6 round 3 GREEN (f6-round3-report.md:363) | `test_request_outcome_advice`; component of `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: 34 tests, 0.726s, OK |
| F6 round 2 RED (f6-round2-report.md:22) | `SAFE python3 -X dev -W error -m unittest test_f6_round2 -v` | python3 / 3.9.6: 21 tests; FAILED (failures=36, errors=15) |
| F6 round 2 RED (f6-round2-report.md:22) | `SAFE python3.12 -X dev -W error -m unittest test_f6_round2 -v` | python3.12 / 3.12.8: 21 tests; FAILED (failures=36, errors=15) |
| F6 round 2 GREEN (f6-round2-report.md:22) | `SAFE python3 -X dev -W error -m unittest test_f6_round2 -v` | python3 / 3.9.6: 21 tests OK, first GREEN |
| F6 round 2 GREEN (f6-round2-report.md:22) | `SAFE python3.12 -X dev -W error -m unittest test_f6_round2 -v` | python3.12 / 3.12.8: 21 tests OK, first GREEN |
| F6 round 2 RED (f6-round2-report.md:42) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestJavascriptTrim -v` | 4 tests; FAILED (failures=4, errors=26) |
| F6 round 2 RED (f6-round2-report.md:54) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestDirectReadBack.test_the_command_reads_recorded_publication_before_problem_preflight -v` | FAILED (failures=1) |
| F6 round 2 RED (f6-round2-report.md:54) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestDirectReadBack.test_missing_deposition_with_an_unreadable_store_does_not_claim_empty_history -v` | FAILED (failures=1) |
| F6 round 2 RED (f6-round2-report.md:56) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestDirectReadBack.test_a_null_successful_read_back_never_means_a_404 -v` | FAILED (errors=1) |
| F6 round 2 RED (f6-round2-report.md:56) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestDirectReadBack.test_a_read_back_cannot_switch_the_recorded_deposition -v` | FAILED (failures=1) |
| F6 round 2 RED (f6-round2-report.md:64) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestFailureAdvice.test_a_non_refusal_status_keeps_uncertainty_even_with_a_known_error_body -v` | 1 test; FAILED (failures=3) |
| F6 round 2 RED (f6-round2-report.md:88) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestFailureAdvice.test_publish_request_advice_set_has_an_explicit_name -v` | FAILED (errors=1) |
| F6 round 2 RED (f6-round2-report.md:98) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestFailureAdvice.test_a_deposit_projection_that_cannot_be_written_names_next_run_and_record test_f6_round2.TestFailureAdvice.test_unwritten_managed_journal_says_what_a_second_run_does -v` | 2 tests; FAILED (failures=1, errors=3) |
| F6 round 2 RED (f6-round2-report.md:100) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestFailureAdvice.test_store_record_failures_distinguish_a_refusal_from_a_failed_export test_f6_round2.TestFailureAdvice.test_pending_store_io_failure_keeps_the_file_before_publication -v` | 2 tests; FAILED (failures=1, errors=5) |
| F6 round 2 RED (f6-round2-report.md:129) | `SAFE python3 -X dev -W error -m unittest test_f6_round2.TestFailureAdvice.test_a_resumed_draft_upload_failure_names_the_read_back_on_the_next_run test_f6_round2.TestFailureAdvice.test_an_unpublished_local_copy_does_not_claim_a_second_permanent_record -v` | 2 tests; FAILED (failures=2) |
| F6 round 2 GREEN (f6-round2-report.md:100) | `SAFE python3 -X dev -W error -m unittest test_f6_round2 test_draft.TestDirectDeposit test_draft_problem_attachment.TestDirectDepositForAProblem -v` | python3 / 3.9.6: 94 tests, 21.889s, OK |
| F6 round 2 GREEN (f6-round2-report.md:100) | `SAFE python3.12 -X dev -W error -m unittest test_f6_round2 test_draft.TestDirectDeposit test_draft_problem_attachment.TestDirectDepositForAProblem -v` | python3.12 / 3.12.8: 94 tests, 17.161s, OK |
| F6 round 2 GREEN (f6-round2-report.md:121) | `SAFE python3 -X dev -W error -m unittest test_f6_round2 test_transport.TestLoginSubcommand test_draft.TestDirectDeposit test_draft_problem_attachment.TestDirectDepositForAProblem test_draft.TestZenodoAnswersThatCannotBeRead test_draft_problem_attachment.TestManagedDepositForAProblem.test_new_version_reads_back_and_continues_a_prior_draft_that_has_no_doi test_draft_problem_attachment.TestProblemDepositJournalStates.test_new_version_reads_back_a_prior_deposit_whose_publish_was_refused -v` | python3 / 3.9.6: 110 tests, 240.992s, OK |
| F6 round 2 GREEN (f6-round2-report.md:121) | `SAFE python3.12 -X dev -W error -m unittest test_f6_round2 test_transport.TestLoginSubcommand test_draft.TestDirectDeposit test_draft_problem_attachment.TestDirectDepositForAProblem test_draft.TestZenodoAnswersThatCannotBeRead test_draft_problem_attachment.TestManagedDepositForAProblem.test_new_version_reads_back_and_continues_a_prior_draft_that_has_no_doi test_draft_problem_attachment.TestProblemDepositJournalStates.test_new_version_reads_back_a_prior_deposit_whose_publish_was_refused -v` | python3.12 / 3.12.8: 110 tests, 224.748s, OK |
| F6 round 2 GREEN (f6-round2-report.md:123) | `SAFE python3 -X dev -W error -m unittest test_f6_round2 -v` | python3 / 3.9.6: 34 tests OK, final verification loop |
| F6 round 2 GREEN (f6-round2-report.md:123) | `SAFE python3.12 -X dev -W error -m unittest test_f6_round2 -v` | python3.12 / 3.12.8: 34 tests OK, final verification loop |
| F6 round 2 GREEN (f6-round2-report.md:127) | `SAFE python3 -X dev -W error -m unittest test_draft_problem_attachment.TestProblemDepositJournalStates test_draft_problem_attachment.TestProblemDepositRefusals test_draft.TestCorruptStoreDeposit test_draft.TestLegacyWorkspaceDeposit -v` | python3 / 3.9.6: 48 tests, 54.382s, OK |
| F6 round 2 GREEN (f6-round2-report.md:127) | `SAFE python3.12 -X dev -W error -m unittest test_draft_problem_attachment.TestProblemDepositJournalStates test_draft_problem_attachment.TestProblemDepositRefusals test_draft.TestCorruptStoreDeposit test_draft.TestLegacyWorkspaceDeposit -v` | python3.12 / 3.12.8: 48 tests, 45.807s, OK |
| F6 round 2 GREEN ATTEMPT (f6-round2-report.md:150) | Earlier full core run of test_draft; full command not reported | FAILED (failures=1), old InvalidURL network remedy |
| F6 round 2 GREEN ATTEMPT (f6-round2-report.md:150) | Earlier full core run of test_draft_problem_attachment; full command not reported | FAILED (failures=2), old no-DOI dead-end assertions |
| F6 round 2 GREEN ATTEMPT (f6-round2-report.md:150) | Earlier 302-test CLI runs; full command not reported | Seven subtest/assertion failures expecting implicit trim of untrimmed server fields |
| F6 round 2 CHECK (f6-round2-report.md:152) | `SAFE python3 -m compileall -q ci hooks codex research_harness tests` with the reported writable pycache prefix | python3 / 3.9.6: exit 0 after the final persistence fix |
| F6 round 2 CHECK (f6-round2-report.md:152) | `SAFE python3.12 -m compileall -q ci hooks codex research_harness tests` with the reported writable pycache prefix | python3.12 / 3.12.8: exit 0 after the final persistence fix |
| F6 round 2 CHECK (f6-round2-report.md:152) | `SAFE python3 -m py_compile bin/exactory bin/exactory-draft` with the reported writable pycache prefix | python3 / 3.9.6: exit 0 after the final persistence fix |
| F6 round 2 CHECK (f6-round2-report.md:152) | `SAFE python3.12 -m py_compile bin/exactory bin/exactory-draft` with the reported writable pycache prefix | python3.12 / 3.12.8: exit 0 after the final persistence fix |
| F6 round 2 CHECK (f6-round2-report.md:152) | `SAFE python3 codex/generate.py --check` with the reported writable pycache prefix | python3 / 3.9.6: exit 0 after the final persistence fix |
| F6 round 2 CHECK (f6-round2-report.md:152) | `SAFE python3.12 codex/generate.py --check` with the reported writable pycache prefix | python3.12 / 3.12.8: exit 0 after the final persistence fix |
| F6 round 2 CHECK (f6-round2-report.md:165) | `SAFE python3 -X dev -W error /private/tmp/f6_mutations.py` | python3 / 3.9.6: 36/36 mutants caught, exit 0 |
| F6 round 2 CHECK (f6-round2-report.md:165) | `SAFE python3.12 -X dev -W error /private/tmp/f6_mutations.py` | python3.12 / 3.12.8: 36/36 mutants caught, exit 0 |
| F6 round 3 RED (f6-round3-report.md:56) | `SAFE python3 -m unittest -v test_math_problems_cli.TestRemovedProblemLinks test_draft_problem_attachment.TestRemovedProblemLinks` | 6 tests, 0.778s; FAILED (failures=5) |
| F6 round 3 RED (f6-round3-report.md:61) | `test_draft_problem_attachment.TestProblemDepositJournalStates.test_a_mark_of_the_attachment_that_cannot_be_written_leaves_a_settled_journal_and_a_retry_attaches_again`; full command not reported | 1 test; FAILED (failures=1) |
| F6 round 3 GREEN (f6-round3-report.md:66) | Those removal classes and the advice case; full command not reported | 7 tests, 1.061s, OK |
| F6 round 3 RED (f6-round3-report.md:70) | Direct settling reproduction; full command not reported | 7 tests, 1.684s; FAILED (failures=1) |
| F6 round 3 RED (f6-round3-report.md:83) | `SAFE python3 -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_read_back_names_the_earlier_deposit test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_publication_does_not_claim_nothing_was_published` | 2 tests, 163.453s; FAILED (failures=1) |
| F6 round 3 GREEN (f6-round3-report.md:85) | `SAFE python3 -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_read_back_names_the_earlier_deposit test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_publication_does_not_claim_nothing_was_published` | 2 tests, 173.589s, OK |
| F6 round 3 GREEN (f6-round3-report.md:90) | `SAFE python3 -m unittest -v test_draft_problem_attachment.TestRemovedProblemLinks` | 7 tests, 1.651s, OK |
| F6 round 3 RED (f6-round3-report.md:97) | `SAFE python3 -m unittest -v test_math_problems_cli.TestServerErrorsAfterAWrite` | 5 tests, 0.156s; FAILED (failures=1) |
| F6 round 3 GREEN (f6-round3-report.md:100) | `SAFE python3 -m unittest -v test_math_problems_cli.TestServerErrorsAfterAWrite` | python3 / 3.9.6: 5 tests, 0.185s, OK |
| F6 round 3 RED (f6-round3-report.md:122) | `SAFE python3 -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_on_a_completed_managed_deposit_names_the_earlier_publication` | 1 test, 120.204s; FAILED (failures=1) |
| F6 round 3 GREEN (f6-round3-report.md:142) | `SAFE python3 -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_on_a_completed_managed_deposit_names_the_earlier_publication test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_read_back_names_the_earlier_deposit test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_publication_does_not_claim_nothing_was_published test_draft.TestDeposit.test_a_ready_study_writes_the_publication_receipt` | python3 / 3.9.6: 4 tests, 412.224s, OK |
| F6 round 3 GREEN (f6-round3-report.md:142) | `SAFE python3.12 -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_on_a_completed_managed_deposit_names_the_earlier_publication test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_read_back_names_the_earlier_deposit test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_publication_does_not_claim_nothing_was_published test_draft.TestDeposit.test_a_ready_study_writes_the_publication_receipt` | python3.12 / 3.12.8: 4 tests, 345.898s, OK |
| F6 round 3 RED (f6-round3-report.md:170) | `SAFE python3 -m unittest -v test_math_problems_cli.TestPlainTextLineBreaks` | 5 tests, 0.047s; FAILED (failures=3, errors=1) |
| F6 round 3 GREEN (f6-round3-report.md:170) | `SAFE python3 -m unittest -v test_math_problems_cli.TestPlainTextLineBreaks` | 5 tests, 0.049s, OK |
| F6 round 3 RED (f6-round3-report.md:179) | `SAFE PYTHON -m unittest -v test_math_problems_cli.TestPlainTextOfFreeTextFields` | 7 tests, 0.353s; FAILED (failures=2); interpreter not specified for RED |
| F6 round 3 GREEN (f6-round3-report.md:180) | `SAFE python3 -m unittest -v test_math_problems_cli.TestPlainTextOfFreeTextFields` | python3 / 3.9.6: 7 tests, 0.427s, OK |
| F6 round 3 GREEN (f6-round3-report.md:180) | `SAFE python3.12 -m unittest -v test_math_problems_cli.TestPlainTextOfFreeTextFields` | python3.12 / 3.12.8: 7 tests, 0.256s, OK |
| F6 round 3 RED (f6-round3-report.md:231) | `SAFE python3 -m unittest -v test_f6_round2.TestJavascriptTrim test_math_problems_cli.TestPostProblemInputs test_math_problems_cli.TestPostProofInputs test_math_problems_cli.TestLeanCheckInputs test_transport.TestPostChallengeSubcommand` | 56 tests, 1.473s; FAILED (failures=18, errors=8) |
| F6 round 3 GREEN (f6-round3-report.md:234) | `SAFE python3 -m unittest -v test_f6_round2.TestJavascriptTrim test_math_problems_cli.TestPostProblemInputs test_math_problems_cli.TestPostProofInputs test_math_problems_cli.TestLeanCheckInputs test_transport.TestPostChallengeSubcommand` | 56 tests, 1.437s, OK |
| F6 round 3 RED (f6-round3-report.md:247) | `SAFE python3 -m unittest -v test_request_outcome_advice` | ModuleNotFoundError before the rename |
| F6 round 3 GREEN (f6-round3-report.md:249) | `SAFE python3 -m unittest -v test_request_outcome_advice` | 34 tests, 1.518s, OK |
| F6 round 3 RED (f6-round3-report.md:266) | `SAFE python3 -m unittest -v test_math_problems_cli.TestIdsStayInTheirRoute` | 4 tests / 1 failure |
| F6 round 3 GREEN (f6-round3-report.md:266) | `SAFE python3 -m unittest -v test_math_problems_cli.TestIdsStayInTheirRoute` | 4 tests / OK |
| F6 round 3 GREEN ATTEMPT (f6-round3-report.md:371) | `SAFE python3 /private/tmp/f6-round3-run-suite.py` | python3 / 3.9.6: Initial matrix: 674 cases; driver exit 0 |
| F6 round 3 GREEN ATTEMPT (f6-round3-report.md:371) | `SAFE python3.12 /private/tmp/f6-round3-run-suite.py` | python3.12 / 3.12.8: Initial matrix: 674 cases; driver exit 1 for the obsolete paper-link conflict assertion |
| F6 round 3 GREEN (f6-round3-report.md:384) | `SAFE python3 /private/tmp/f6-round3-run-suite.py test_draft_problem_attachment` | python3 / 3.9.6: 132 cases OK after the completed-intent correction |
| F6 round 3 GREEN (f6-round3-report.md:384) | `SAFE python3.12 /private/tmp/f6-round3-run-suite.py test_draft_problem_attachment` | python3.12 / 3.12.8: 132 cases OK after the completed-intent correction |
| F6 round 3 CHECK (f6-round3-report.md:385) | `SAFE python3 /private/tmp/f6-round3-status.py --check` | exit 0; FINAL MODULE AGGREGATE: PASS, 675 cases per interpreter |
| F6 round 3 GREEN (f6-round3-report.md:412) | `SAFE python3 -m unittest -v test_request_outcome_advice test_math_problems_cli.TestRemovedProblemLinks test_math_problems_cli.TestPlainTextLineBreaks test_math_problems_cli.TestIdsStayInTheirRoute test_math_problems_cli.TestPostProblemInputs test_math_problems_cli.TestPostProofInputs test_math_problems_cli.TestLeanCheckInputs test_math_problems_cli.TestPlainTextOfFreeTextFields test_math_problems_cli.TestServerErrorsAfterAWrite test_transport.TestPostChallengeSubcommand test_draft_problem_attachment.TestRemovedProblemLinks test_draft_problem_attachment.TestDirectDepositForAProblem test_draft_problem_attachment.TestProblemDepositJournalStates` | python3 / 3.9.6: 171 tests, 20.989s, OK |
| F6 round 3 GREEN (f6-round3-report.md:412) | `SAFE python3.12 -m unittest -v test_request_outcome_advice test_math_problems_cli.TestRemovedProblemLinks test_math_problems_cli.TestPlainTextLineBreaks test_math_problems_cli.TestIdsStayInTheirRoute test_math_problems_cli.TestPostProblemInputs test_math_problems_cli.TestPostProofInputs test_math_problems_cli.TestLeanCheckInputs test_math_problems_cli.TestPlainTextOfFreeTextFields test_math_problems_cli.TestServerErrorsAfterAWrite test_transport.TestPostChallengeSubcommand test_draft_problem_attachment.TestRemovedProblemLinks test_draft_problem_attachment.TestDirectDepositForAProblem test_draft_problem_attachment.TestProblemDepositJournalStates` | python3.12 / 3.12.8: 171 tests, 11.825s, OK |
| F6 round 3 GREEN (f6-round3-report.md:415) | `SAFE python3 -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_read_back_names_the_earlier_deposit test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_publication_does_not_claim_nothing_was_published` | python3 / 3.9.6: 2 tests, 215.088s, OK |
| F6 round 3 GREEN (f6-round3-report.md:415) | `SAFE python3.12 -m unittest -v test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_read_back_names_the_earlier_deposit test_draft_problem_attachment.TestManagedDepositForAProblem.test_a_removed_link_after_managed_publication_does_not_claim_nothing_was_published` | python3.12 / 3.12.8: 2 tests, 170.380s, OK |
| F6 round 3 GREEN (f6-round3-report.md:423) | `SAFE python3 -m unittest -v test_draft_problem_attachment.TestRemovedProblemLinks` | python3 / 3.9.6: 7 tests, 2.258s, OK |
| F6 round 3 GREEN (f6-round3-report.md:423) | `SAFE python3.12 -m unittest -v test_draft_problem_attachment.TestRemovedProblemLinks` | python3.12 / 3.12.8: 7 tests, 1.515s, OK |
| F6 round 3 GREEN (f6-round3-report.md:431) | Earlier combined focused suites; full command not reported | python3 / 3.9.6: 153 cases passed |
| F6 round 3 GREEN (f6-round3-report.md:431) | Earlier combined focused suites; full command not reported | python3.12 / 3.12.8: 153 cases passed |
| F6 round 3 REVIEW (f6-round3-report.md:432) | Independent reviewer focused offline cases; full command not reported | 84 passed; reviewer reproduced the later-fixed settlement gap; interpreter not specified |
| F6 round 2 supervisor GREEN ATTEMPT (progress.md:597) | 21 loopback tests outside the sandbox; exact command not recorded in the ledger | 17 passed; the new closed-port control failed on both interpreters |
| F6 round 2 supervisor GREEN (progress.md:597) | Closed-port control after closing the bound port; exact command not recorded in the ledger | Passes on both interpreters |
| F6 round 3 supervisor GREEN ATTEMPT (progress.md:631) | 21 loopback tests outside the sandbox; exact command not recorded in the ledger | 8 subtests failed on both interpreters; three socket tests expected the old attachment lead |
| F6 round 3 supervisor GREEN (progress.md:631) | 21 loopback tests after the supervisor updated those expectations; exact command not recorded in the ledger | 21/21 pass on Python 3.9.6 and 3.12.8 |

## F5 round 2 checks

These runs occurred in the F5 worktree. Each command used the four credential removals, `PYTHONPATH=tests`, and a writable pycache prefix.
Source names below refer to this round’s scratch logs. No required test was skipped.

| Task and source | Command | Result |
| --- | --- | --- |
| F5 round 2 GREEN (tests-python3.log) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 266 tests, 190.200s, OK |
| F5 round 2 GREEN (tests-python3.12.log) | `PYTHONPATH=tests python3.12 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 266 tests, 173.559s, OK |
| F5 round 2 GREEN (final-tests-python3.log) | `PYTHONPATH=tests python3 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 266 tests, 165.080s, OK |
| F5 round 2 GREEN (final-tests-python3.12.log) | `PYTHONPATH=tests python3.12 -B -X dev -W error -m unittest -v test_manifest test_research_guidance test_codex test_workspace_init_retry test_lab test_math_problems_cli` | 266 tests, 149.875s, OK |
| F5 round 2 GREEN (smoke-python3.log) | `PYTHONPATH=tests python3 -B tests/smoke_codex.py --codex /Users/ryshiro/.nvm/versions/node/v24.18.0/bin/codex --output <scratch-log.json>` | PASS: installed plugin, 16 skills, 11 hooks, no load errors |
| F5 round 2 GREEN (smoke-python3.12.log) | `PYTHONPATH=tests python3.12 -B tests/smoke_codex.py --codex /Users/ryshiro/.nvm/versions/node/v24.18.0/bin/codex --output <scratch-log.json>` | PASS: installed plugin, 16 skills, 11 hooks, no load errors |
