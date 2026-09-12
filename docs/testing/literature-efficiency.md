# Literature efficiency (0.38.0) measurements

Date: 2026-09-11. Host: macOS 25.6.0, Python 3.9.6, pdftotext 26.02.0.

## Read-only measurement on the closed-gravity study

Study: `closed-gravity-internal-measurements`, revision 3,584, 2,505 cohort
families, 2,526 exact cohort versions, 2,610 readings, 604 MB SQLite. Every run
used the local checkout's `bin/exactory-research status --workspace .` on the
live study without any write. One run per condition; wall time includes the
operating system file cache, which stayed warm between runs.

| Condition | Wall | User CPU | System CPU | Max RSS | Output |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline (`c46dd2b`, 0.37.0 semantics) | 78.03 s | 39.54 s | 28.63 s | 2.35 GB | 87,631,431 bytes |
| After one replay per invocation (Task 2) | 60.62 s | 27.62 s | 21.42 s | 2.32 GB | 87,631,906 bytes |
| After the evaluation context (Task 3) | 20.58 s | 14.07 s | 2.13 s | 2.32 GB | 87,631,906 bytes |
| Final `status` (all tasks) | 20.04 s | 12.67 s | 2.15 s | 2.32 GB | 87,850,632 bytes |
| Final `status --summary` | 14.21 s | 11.98 s | 1.84 s | | 2,604 bytes |
| Final `next --summary` | | | | | 566 bytes |

CPU (user plus system) fell from 68.17 s to 14.82 s, 21.7% of the baseline,
which meets the 30% engineering target. After Task 3 the 454 obligations, the
synthesis digest, the foundation digest and the preparation report were
byte-identical to the baseline. The final full report is larger by the new
`runtime` and `resources` keys and by the 11 obligations the release introduces
on this existing study: `constitution_revalidation_required` (1),
`search_dispositions_missing` (5) and `search_frontier_stale` (5), for 465 in
total. The baseline already carried `search_evidence_stale` (5) and
`synthesis_dependencies_stale` (4) from earlier changes to the study, so this
measurement does not show whether an untouched 0.37.0 study gains those two
codes on upgrade; the release note states that it does, from the changed
digest definitions. The same 465 obligations, by code, were reproduced by
`status --summary` on the release commit's code (18.5 s wall, 11.8 s user).

Evaluation counters on the final `status --summary`: 33,123 reads served by
3,559 verified objects (87.3 MB), 2,610 readings assessed, 2,898 links
validated, 1 graph build, 1 cohort report, 13.95 s elapsed.

`policy-report --policy screened-v1` on the study (recorded policy
`exhaustive-v1`): 2,505 families, 2,526 versions, 2,526 read, 2,526 unscreened,
no dispositions, no saturation. The retrospective screening evaluation from the
validation protocol has not been run; this report is its starting point.

## Test evidence

Baseline suite before any change: 809 tests, 1,292 s, OK (with the pre-existing
registry-abstract-absence change committed as `c46dd2b`).

Review before release: two independent reviewers (record integrity and
specification conformance) read the complete change. Every blocker and major
finding was fixed in code with a regression test, and the specification was
amended where the reviewers showed that a stated rule would have made the
workflow loop (the frontier definition) or was weaker than intended (audit
credit, screening rounds, reservation release). The corrections are listed in
the release note.

Final suites on the release commit's tree, run one after the other on an
otherwise idle machine: the plugin suite, 901 tests, 1,000 s, OK; the
math-solver harness suite (`skills/math-solver/harness/tests`), 689 tests,
1,607 s, OK. An earlier run of the math suite concurrently with the plugin
suite reported 38 failures and 2 errors in the Lean and search-execution
modules; every one of those modules passed when rerun alone (95 tests, OK)
and in the final sequential run, so those failures came from the concurrent
load, not from the code.

## Validation cases M01 to M28

The validation protocol's cases map to these tests. A case marked "measured"
also rests on the read-only measurement above.

| Case | Tests |
| --- | --- |
| M01 | `test_research_evaluation`: `test_reports_are_identical_between_fresh_and_shared_evaluations`, `test_status_computes_the_cohort_report_and_graph_once`; measured (identical obligations and digests on the 2,505-family study) |
| M02 | `test_research_cli.test_compact_reports_are_opt_in_and_read_only`; `test_research_report_views.test_views_preserve_state_and_exclude_source_bodies` |
| M03 | `test_research_report_views`: `test_obligation_counts_include_undisplayed_codes`, `test_many_instances_of_one_code_preserve_the_total`, `test_worst_case_strings_stay_bounded_and_are_marked` |
| M04 | `test_research_report_views.test_obligations_page_is_bound_to_the_revision`; the stale cursor in `test_research_cli.test_compact_reports_are_opt_in_and_read_only` |
| M05 | `test_research_storage`: `test_record_tampering_is_caught_at_first_use_not_at_construction`, `test_corrupt_database_and_schema_fail_explicitly`, `test_corrupt_projection_digest_and_revision_are_detected`, `test_receipt_json_types_must_match_the_immutable_event`, `test_hot_journal_needs_explicit_recovery_and_preserves_committed_state` |
| M06 | `test_research_evaluation.test_bytes_are_verified_once_per_evaluation_and_tampering_is_still_caught`; `test_research_source_contracts.test_changed_asset_behind_identical_html_requires_new_bundle_and_reading` |
| M07 | `test_research_source_contracts`: `test_span_offsets_are_code_points_across_combining_marks_and_crlf`, `test_span_locator_validates_by_hash_and_shares_identity_with_text` |
| M08 | `test_research_reading`: `test_bad_locators_partial_abstract_and_cross_version_links_are_rejected`, `test_different_body_under_same_work_id_cannot_reuse_old_reading`; `test_research_source_contracts.test_partial_span_of_a_supplement_does_not_supply_full_supplement_depth` |
| M09 | `test_research_reading.test_required_visual_and_missing_supplement_units_remain_pending`; `test_research_source_contracts.test_missing_visual_bytes_are_actionable_before_a_reading_is_recorded` |
| M10 | `test_research_reading`: `test_a_reading_on_any_capture_of_the_same_original_covers_it`, `test_identical_original_bytes_can_reuse_reading_through_a_new_capture_pin` |
| M11 | `test_research_source_contracts.test_equal_extraction_does_not_cross_distinct_original_pin`; `test_research_reading.test_new_distinct_body_needs_its_own_bundle_but_the_fixed_target_stays_pinned` |
| M12 | `test_research_reading`: `test_a_new_required_unit_still_stales_the_reading`, `test_new_bundle_required_unit_invalidates_full_depth_without_deleting_reading` |
| M13 | `test_research_batches.test_invalid_item_rejects_the_whole_batch_with_indices` |
| M14 | `test_research_batches.test_replay_and_conflict_follow_request_identity` |
| M15 | `test_research_storage`: `test_concurrent_same_request_returns_one_result_and_calls_once`, `test_process_exit_during_mutation_leaves_no_partial_commit`, `test_two_connections_serialize_and_second_stale_writer_is_rejected` |
| M16 | `test_cohort_collection`: `test_malformed_provider_doi_is_retained_as_warning_and_does_not_restart_partition`, `test_category_conflict_on_resume_remains_explicit`, `test_failure_on_the_last_page_resumes_from_that_page_only` |
| M17 | `test_cohort_collection`: `test_changed_total_or_start_is_never_completion`, `test_a_repeated_page_is_pending_without_restarting_the_partition`, `test_duplicate_exact_ids_keep_distinct_original_entry_assertions`, `test_population_loss_after_changed_total_remains_pending` |
| M18 | `test_research_literature`: `test_unrelated_reference_changes_do_not_stale_but_a_new_frontier_family_does`, `test_recording_another_purpose_does_not_stale_a_selected_search`; `test_research_synthesis.test_unrelated_readings_leave_sections_current_and_new_judgments_stale_them`; `test_research_reading.test_parsed_bibliography_does_not_invalidate_a_fulltext_reading` |
| M19 | `test_research_literature`: `test_search_decision_is_stale_after_relevant_source_change_and_promotes_cited_depth`, `test_unrelated_reference_changes_do_not_stale_but_a_new_frontier_family_does` |
| M20 | `test_research_synthesis`: `test_historical_and_eligibility_is_reassessed_without_rewriting_saved_judgments`; `test_research_literature.test_temporal_cutoff_keeps_current_capture_but_exposes_historical_content_gap` |
| M21 | `test_research_literature.test_contradictory_findings_are_carried_forward_or_resolved` |
| M22 | `test_research_synthesis`: `test_complete_research_and_scope_changes_require_new_assessments_preserving_old_results`, `test_constitution_adoption_does_not_automatically_revalidate_synthesis`; `test_research_principles.test_policy_change_names_the_current_policy_and_changes_the_configuration_digest`; `test_research_screening.test_a_new_screening_round_or_policy_change_removes_the_checkpoint_effect` |
| M23 | `test_research_resources`: `test_exhaustion_is_an_obligation_shown_in_status`, `test_an_interrupted_acquisition_holds_its_reservation_until_superseded`, `test_an_acquisition_without_an_allowance_needs_room_for_one_request`; `test_cohort_collection.test_collect_zero_budget_status_resume_and_unchanged_freeze_contract` |
| M24 | `test_research_review_packets`: `test_manuscript_delivery_is_a_neutral_packet_with_the_claim_evidence_closure`, `test_readiness_delivery_keeps_check_evidence_and_drops_labels`, `test_scrub_removes_labels_at_every_depth`; `test_research_publication.test_manuscript_delivery_contains_the_actual_candidate_bytes_and_no_review_scores` |
| M25 | `test_research_review_packets.test_one_review_per_assessor_per_bundle_and_a_rejection_stands` covers the normalized assessor identity. The independence basis remains a declared field that the review contract records; no test proves that two identifiers are one context. |
| M26 | `test_research_publication.test_changed_pdf_abstract_bibliography_and_claims_invalidate_the_exact_bundle` |
| M27 | `test_research_evaluation.test_reports_are_identical_between_fresh_and_shared_evaluations`; measured (byte-identical obligations and digests after Task 3) |
| M28 | `test_research_source_contracts.test_span_locator_validates_by_hash_and_shares_identity_with_text`; `test_research_reading.test_legacy_assessment_digest_is_not_compared`; `test_research_literature.test_legacy_search_without_dispositions_is_an_obligation_not_a_crash`; `test_research_development.test_a_plan_bound_to_the_foundation_digest_is_stale_not_malformed`; `test_research_storage.test_construction_checks_structure_and_first_read_replays_history`; `test_research_cli.test_mutation_replay_and_stale_revision_preserve_original_state` |
