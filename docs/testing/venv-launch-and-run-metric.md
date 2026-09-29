# venv launch, citable evidence and run metric (0.48.0): test record

Source plan: the v11 harness-improvement record and tasks H1, H7, H8 and H9, and
task N5 of v12, in the exactory repository (`misc/harness-improvement/v11/`,
`misc/harness-improvement/v12/`). The user chose the H8 option (refuse at
`bind-run`) and released these four tasks first.

## Journeys

1. As an author, I want a program admitted with a venv's `bin/python3` to run
   with the venv's packages, so that an ordinary venv works without copies.
2. As an author, I want the commands that bind and launch such a run to be
   stated, so that I start them with the right interpreter.
3. As an author, I want a figure bound as result evidence to fail before the run,
   so that no cycle ends with a result that no locator can cite.
4. As an author, I want a completed program that writes its summary under its own
   name to report its metric, so that the receipt says `ok: true`.
5. As the person who supervises a study, I want every recorded metric to stay
   readable by every supported Python, so that a later reader never meets a
   corrupt store.

## RED and GREEN evidence

Commands run from the repository root; modules that import test fixtures need
`PYTHONPATH=tests`.

| Task | RED commit and cause | GREEN commit | Command |
| --- | --- | --- | --- |
| H1 venv entry point | `80314d3`: the program could not import the venv-only module, because the worker started the resolved base interpreter | `95d3d48` | `python3 -m unittest discover -s tests -p test_research_execution.py -k test_symlinked_venv_interpreter` |
| H8 citable evidence (v12 N5) | `132387d`: a PNG-only result output bound without error | `20955ce` | `python3 -m unittest discover -s tests -p test_research_execution.py -k test_output_that_no_locator_can_cite` |
| H9 validation-output metric | `106e188`: `ok: false` and no metric for `code/refine_cycle.py` writing `results/run_cycle.json` | `9b1b8bb` | `python3 -m unittest discover -s tests -p test_research_execution.py -k test_declared_json_validation_output` |
| H7 venv recipe | `6509a59`: the workflow and the skill named no command for a venv interpreter | `51d9c42` | `python3 -m unittest tests.test_research_guidance tests.test_manifest` |
| Review 1: a real interpreter named through a linked directory | `fa2a88d`: its runtime record changed from the resolved path | `5f357a5` | `PYTHONPATH=tests python3 -m unittest tests.test_research_execution.ResearchExecutionTests.test_real_interpreter_file_named_through_a_linked_directory_keeps_the_record_of_earlier_releases` |
| Review 1: a link retargeted to an identical file | `8661bca`: the claim accepted a link that now named another installation | `37cd451` | `PYTHONPATH=tests python3 -m unittest tests.test_research_execution.ResearchExecutionTests.test_link_retargeted_to_an_identical_file_of_another_installation_stops_the_claim` |
| Review 1: identical `bind-run` of an earlier release's binding | `11b161e`: `record_conflict` for the same payload | `a218932` | `PYTHONPATH=tests python3 -m unittest tests.test_research_execution.ResearchExecutionTests.test_identical_bind_run_again_returns_the_binding_of_an_earlier_release` |
| Review 1: the shared-folder runner started from a venv | `b7ab303`: the runner started its resolved interpreter and lost the venv | `e40cdeb` | `PYTHONPATH=tests python3 -m unittest tests.test_research_remote_execution.RemoteExecutionTests.test_runner_started_from_a_venv_link_runs_the_program_with_the_venv_packages` |
| Review 1: the documented outcome of a 0.47.0 venv binding | `cc1c37d` | `5a4c00d` | `PYTHONPATH=tests python3 -m unittest tests.test_research_guidance` |
| Review 2: a metric that a later Python cannot read back | `62b7d51`: a 4817-digit metric written by Python 3.9.6 made a Python 3.13 `status --summary` exit with `corrupt_state` | `a018485` | `PYTHONPATH=tests python3 -m unittest tests.test_research_execution.RunMetricTests` under 3.9.6 and 3.12.8 |
| Review 2: a validation output above 16 KiB gave no metric | `bcab2b2` (the 16 KiB bound of `ee7f8d7`) | `358d77a` | `PYTHONPATH=tests python3 -m unittest tests.test_research_execution.RunMetricTests.test_declared_validation_output_of_any_size_supplies_the_metric` |
| Review 2: identical `bind-run` of an earlier binding with a figure as result evidence | `a369e2e`: `invalid_execution` on the retry | `ddd938b` | `PYTHONPATH=tests python3 -m unittest tests.test_research_execution.ResearchExecutionTests.test_identical_bind_run_again_returns_an_earlier_release_binding_of_a_figure_as_result_evidence` |
| Review 2: the outcome of an interpreter change after the claim | `043a546` | `db04b7c` | `PYTHONPATH=tests python3 -m unittest tests.test_research_guidance` |

An independent verifier checked the group three times. Round 1 found six minor
issues, round 2 one blocker (metric values that a later Python cannot read back)
and three minor issues, and round 3 three minor issues and no unmet criterion.
The round 3 issues are stated in the release note (the metric has no byte bound,
as the fallback file never had) or concern comments.

## Guarantees

| # | Guarantee | Test |
| --- | --- | --- |
| 1 | A program admitted with a symlinked venv interpreter imports the venv's packages | `test_symlinked_venv_interpreter_runs_the_program_with_its_own_site_packages` |
| 2 | A regular interpreter file, also through a linked directory, keeps the runtime record of earlier releases | `test_real_interpreter_file_keeps_the_runtime_record_of_earlier_releases`, `test_real_interpreter_file_named_through_a_linked_directory_keeps_the_record_of_earlier_releases` |
| 3 | A binding recorded by 0.47.0 still launches, and the same `bind-run` payload returns it | `test_binding_that_pinned_the_resolved_interpreter_path_still_launches`, `test_identical_bind_run_again_returns_the_binding_of_an_earlier_release` |
| 4 | A link that resolves to another file, or changed bytes or version, stop the claim; a change after the claim never starts the other interpreter | `test_link_retargeted_to_an_identical_file_of_another_installation_stops_the_claim`, `test_changed_bytes_or_version_behind_a_linked_interpreter_stop_the_claim`, `test_link_retargeted_after_the_claim_never_starts_the_other_installation` |
| 5 | A new binding refuses an output that result or validation evidence cannot cite; an earlier binding with such an output is returned unchanged | `test_output_that_no_locator_can_cite_is_refused_for_result_and_validation_evidence`, `test_identical_bind_run_again_returns_an_earlier_release_binding_of_a_figure_as_result_evidence` |
| 6 | The metric comes from stdout, then the fallback file, then the declared JSON validation output, and an earlier run config keeps its metric | `test_metric_sources_keep_their_order_and_an_earlier_run_config_keeps_its_metric`, `test_declared_json_validation_output_supplies_the_metric_of_a_differently_named_program`, `test_declared_validation_output_of_any_size_supplies_the_metric` |
| 7 | No recorded metric is JSON that a supported Python cannot read back, and an unreadable fallback file gives no metric instead of stopping reconciliation | `test_each_metric_source_gives_only_json_that_every_supported_python_reads_back`, `test_launched_run_records_no_metric_that_a_later_python_cannot_read_back`, `test_unreadable_fallback_file_gives_no_metric_instead_of_stopping_reconciliation` |
| 8 | The shared-folder runner started from a venv link runs programs with the venv's packages | `test_runner_started_from_a_venv_link_runs_the_program_with_the_venv_packages` |
| 9 | The workflow, the CLI reference and the experiment skill state the venv commands, the outcome of an earlier release's binding and the outcome of an interpreter change after the claim | `test_workflow_shell_examples_are_accepted_by_the_actual_command_parsers`, `test_guidance_states_that_an_earlier_release_binding_starts_the_resolved_interpreter`, `test_guidance_names_the_outcome_of_an_interpreter_change_after_the_claim` |
