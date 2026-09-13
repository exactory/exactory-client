# Development rounds (0.39.0) test evidence

Date: 2026-09-12. Host: macOS 26.6.2 (Darwin 25.6.0), Python 3.9.6.
Runner: `python3 -m unittest discover -s tests -p <test_file.py>` from the
plugin worktree; the full suite is `python3 -m unittest discover -s tests`.

## Baseline

Full suite on `main` at `19ef5d2` (0.38.0) before any change: 901 tests,
1,025 s, OK.

## Per-task evidence

Every task followed the plan's loop: the failing tests were committed first
(`test:` commits), the implementation second (`feat(research):` commits), then
two independent reviews (specification conformance; code quality and test
rigor) whose blocker and major findings were fixed with regression tests
(`fix(research):` commits). The observed RED reasons and GREEN tallies were
recorded task by task in the progress record
(`docs/superpowers/plans/2026-09-12-development-rounds-progress.md`), which is
the primary source; this table restates them.

| Task | RED as recorded | GREEN as recorded |
| --- | --- | --- |
| 1 | The failure text was not recorded | The tally was not recorded; `test_research_resources.py` ran 9 OK at Task 5 |
| 2 | The failure text was not recorded | The tally was not recorded; `test_research_development.py` ran 65 OK at Task 4's third review |
| 3 | The failure text was not recorded | The tally was not recorded; `test_research_development.py` ran 65 OK at Task 4's third review |
| 4 | The failure text was not recorded | `test_research_rounds.py` 21, `test_research_publication.py` 8, `test_research_review_packets.py` 20 OK after the fourth review |
| 5 | RED then GREEN recorded without the failure text | `test_research_rounds.py` 32, `test_research_resources.py` 9 OK after the second review |
| 6 | RED then GREEN recorded without the failure text; the review fix's RED was `'downstream' not found in ['recent', 'direct', 'theory', 'originals', 'adjacent']` | `test_research_rounds.py` 37, `test_research_synthesis.py` 33, `test_research_literature.py` 23 OK after the second review |
| 7 | `ImportError: cannot import name 'predictions'` (the test module imports `predictions`, `resources` and `rounds` at the top) | `test_research_rounds.py` 39, `test_research_publication.py` 8, `test_research_review_packets.py` 20 OK after the second review |
| 8 | RED then GREEN recorded without the failure text | `test_research_rounds.py` 48 OK after the fourth review |
| 9 | Eight of the ten new rounds tests failed (`invalid_gate`, `KeyError: 'round'`, `ResearchError not raised`, the status list) and the report-views case with `KeyError: 'round'`; the second review's additions failed with `KeyError: 'budget'` and `KeyError: 'limits'` | `test_research_rounds.py` 59, `test_research_report_views.py` 12, `test_research_gates.py` 7, `test_research_cli.py` 18 OK after the second review |
| 10 | `ImportError: cannot import name 'deliver_round'`; the review fix's RED was verified by dropping `("authors", "author")` from the packet's scrub call | `test_research_review_packets.py` 24, `test_research_rounds.py` 59 OK after the review |
| 11 | `'round' not found in OPERATIONS` (`round` was already in `GATES` from Task 9) | `test_research_cli.py` 19, `test_research_guidance.py` 5 OK |
| 12 | `'0.38.0' != '0.39.0'` in `test_manifest`, `test_codex` and `test_research_guidance`; `'2' != '3'` in `test_research_principles` | see below |

Task 12 GREEN, on the release tree, with the commands as run:

- `python3 -m unittest discover -s tests -p test_manifest.py`: 7 tests, OK.
- `python3 -m unittest discover -s tests -p test_codex.py`: 29 tests, OK.
- `python3 -m unittest discover -s tests -p test_research_guidance.py`: 5 tests, OK.
- `python3 -m unittest discover -s tests -p test_research_principles.py`: 11 tests, OK.
- `python3 -m unittest discover -s tests -p test_hooks.py`: 64 tests, OK.
- `python3 -m unittest discover -s tests -p test_research_report_views.py` (its fixture literals changed): 12 tests, OK.
- `python3 codex/generate.py --check` and `git diff --check`: clean.

## Validation cases R01 to R25

The cases of `misc/harness-improvement/v3/validation.md` (exactory repository)
map to these tests. Unless a module is named, the test is in
`tests/test_research_rounds.py`.

| Case | Tests |
| --- | --- |
| R01 | `test_a_decision_binds_the_exact_current_bundle_and_the_current_round`, `test_a_decision_needs_a_pinned_bundle` |
| R02 | `test_a_decision_binds_the_exact_current_bundle_and_the_current_round`, `test_admission_records_the_revision_and_limits_and_closes_only_the_current_round` |
| R03 | `test_an_admitted_round_is_assessed_before_the_next_decision`, `test_an_admitted_round_is_assessed_on_this_bundle_before_the_next_decision` |
| R04 | `test_research_development`: `test_a_next_round_alternative_is_carried_and_does_not_block_readiness`, `test_a_next_round_branch_is_carried`; `test_a_carried_development_must_be_disposed`, `test_a_late_reassessment_of_an_earlier_cycle_is_carried_by_the_closing_round` |
| R05 | `test_continue_pursues_exactly_one_candidate_and_stop_pursues_none` |
| R06 | `test_a_rejected_candidate_cannot_become_a_goal`, `test_a_deferred_candidate_may_become_a_later_goal`, `test_a_repeated_round_goal_reopens_that_round_with_changed_evidence`, `test_a_successful_round_is_not_reopened` |
| R07 | `test_a_goal_needs_claim_or_scope_criteria_stop_conditions_and_continuity`, `test_the_next_round_is_well_formed` |
| R08 | `test_the_review_is_independent_complete_and_bound_to_the_decision`, `test_an_author_of_a_cycle_assessment_cannot_review_the_round`, `test_a_stop_review_has_its_own_checks`, `test_review_evidence_names_a_manuscript_review_of_this_bundle`, `test_the_gate_walks_from_bundle_to_decision_to_review_to_admission` (`round_review_pending`) |
| R09 | `test_one_approved_decision_per_closing_round`, `test_an_approved_decision_on_a_superseded_bundle_does_not_block_the_round`, `test_a_review_binds_the_current_bundle` |
| R10 | `test_transitions_follow_the_round_gate`, `test_a_stop_does_not_enter_a_development_round`, `test_a_stop_after_an_assessed_round_permits_deposit`, `test_a_stop_decision_makes_the_gate_ready` |
| R11 | `test_admission_widens_the_objective_and_charges_the_development_budget`; `test_research_development`: `test_a_widened_objective_keeps_the_old_one_as_an_ancestor`, `test_an_unlinked_unchanged_or_malformed_objective_is_refused`, `test_an_ancestor_objective_id_cannot_be_reused_after_a_widening` |
| R12 | `test_research_development.test_a_widened_objective_keeps_the_old_one_as_an_ancestor` (a successor cycle planned under the widened objective inherits the earlier checkpoint; the stale-objective refusal is `test_an_unlinked_unchanged_or_malformed_objective_is_refused`) |
| R13 | `test_an_active_round_requires_fresh_consequence_searches_and_an_exemplar`, `test_a_fresh_consequence_search_is_judged_like_the_five_purposes`, `test_development_searches_enter_the_judgments_that_synthesis_depends_on`, `test_a_development_purpose_is_a_search_purpose`; `test_research_literature.test_a_development_purpose_is_a_search_purpose_and_an_unknown_purpose_is_not` |
| R14 | `test_an_active_round_requires_fresh_consequence_searches_and_an_exemplar`, `test_an_exemplar_the_round_opened_with_does_not_count` |
| R15 | `test_an_unproductive_round_lacks_a_fresh_search_the_round_exemplar_or_a_new_cycle`, `test_an_unproductive_round_is_unsuccessful_even_when_a_criterion_is_observed` |
| R16 | `test_claims_continuity_marks_revised_superseded_and_dropped_claims`, `test_the_gate_reports_the_round_claims_and_the_stale_assessment`; `test_research_publication.test_claim_continuity_markers_have_the_stated_shape` |
| R17 | `test_a_prediction_binds_the_bundle_the_cohort_and_a_blind_assessor`; `test_research_publication.test_validate_assessor_is_shared_and_refuses_authors` |
| R18 | `test_a_productive_round_is_assessed_with_derived_progress`, `test_measurement_summary_reports_medians_and_spreads`, `test_every_criterion_and_stop_condition_is_judged_once_on_the_current_bundle`, `test_every_criterion_of_a_two_criterion_goal_is_judged_with_evidence`, `test_a_productive_round_without_an_observed_criterion_is_unsuccessful`, `test_a_round_assessed_on_a_superseded_bundle_is_assessed_again_on_the_current_one`, `test_an_earlier_round_is_not_assessed_on_a_later_round_bundle` |
| R19 | `test_two_unsuccessful_rounds_exhaust_a_direction`, `test_an_exhausted_direction_reopens_one_of_its_two_unsuccessful_rounds` |
| R20 | `test_an_exhausted_development_budget_refuses_the_decision`, `test_admission_needs_development_budget_room`, `test_an_exhausted_development_budget_is_a_round_gate_obligation`; `test_research_resources`: `test_development_rounds_are_a_budgeted_unit`, `test_a_budget_payload_names_the_rounds_unit`, `test_an_account_stored_before_the_rounds_unit_keeps_its_credit` |
| R21 | `test_research_review_packets.test_the_round_packet_carries_history_and_reviews_but_no_labels_or_authors` |
| R22 | `test_research_review_packets.test_the_manuscript_packet_stays_blind_to_rounds_and_predictions` |
| R23 | `test_status_at_evaluate_carries_publication_and_round_obligations_in_priority_order`, `test_a_manuscript_awaiting_reviews_is_led_to_the_reviews_before_the_round`, `test_the_round_summary_is_bounded_and_carries_the_active_round`; `test_research_report_views.test_the_round_object_is_copied_into_the_status_summary` |
| R24 | `test_research_principles.test_distributed_constitution_is_version_three` (the constitution obligation); the `round_decision_missing` leg on a bundle without a decision is `test_the_gate_walks_from_bundle_to_decision_to_review_to_admission`. No test opens a store written by 0.38.0; the case rests on the unchanged `schema_version` 1 and on the existing 0.38.0 tests, which stay enabled. |
| R25 | `test_a_field_change_stays_in_the_corpus_and_needs_literature_room`, `test_a_field_change_adds_a_category_the_cohort_lacks`, `test_a_field_change_to_a_new_category_is_admitted`. The difference-collection linking is the existing per-version reading rule of the cohort stage; no round test collects a second category. |

The CLI registration of the five operations, the `round` gate and the export
kind is `test_research_cli.test_round_commands_are_registered_with_examples_and_the_round_gate`;
the example catalog and the workflow's `sh` blocks are parsed by
`test_research_guidance`.

## Review process

Each task ended with two independent reviews (specification conformance; code
quality and test rigor). Every blocker and major finding was fixed in code with
a regression test; the findings and the fixes are recorded per task in the
progress record. The whole-branch two-reviewer pass and the final full-suite
result are recorded below once run.

## Final full suite

Pending: to be recorded by the release step
(`python3 -m unittest discover -s tests`).
