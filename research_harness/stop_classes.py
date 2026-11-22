"""The stop classes and next steps shared by the store and math controller.

D2 owns this registry. Each package adds its codes here when its unit ships.
Unknown codes retain the decision class used by stored records.
"""

import copy
from .openness_reading_v7 import ADMISSION_QUESTION, ADMISSION_SMALLER_ANSWER, ADMISSION_ROUTE_ANSWER
from .openness_reading_v7 import VISUAL_TEXT_ROUTE_CODE, VISUAL_TEXT_ROUTE_QUESTION, VISUAL_TEXT_ROUTE_ANSWER


_STORED_CONTENT_CODES_REVIEW_A = (
    'review_answer_recovery_invalid',
    'review_correction_route_mismatch',
    'review_history_ambiguous',
    'review_objection_evidence_invalid',
    'review_parts_delivery_invalid',
    'review_prompt_mismatch',
    'review_request_changed',
    'review_output_unavailable',
    'review_inventory_mismatch',
    'review_source_parent_unverified',
    'review_history_invalid',
    'artifact_missing',
    'artifact_corrupt',
)

_OUTPUT_CONTRACT_CODES_REVIEW_A = (
    'review_adjudication_mismatch',
    'review_bar_mismatch',
    'review_bar_pending',
    'review_composition_conflict',
    'review_composition_pending',
    'review_objection_history_incomplete',
    'review_parts_request_no_progress',
    'review_reassessment_incomplete',
    'review_reassessment_mismatch',
    'review_reassessment_missing',
    'review_work_items_incomplete',
)

_CALL_OUTCOMES_REVIEW_A = (('review_provider_failure', 'provider_unavailable'),)

_STORED_CONTENT_CODES_REVIEW_B = (
    'invalid_review_prompt_version',
    'invalid_review_role',
    'value_review_output_mismatch',
)


STOP_REGISTRY = {
    code: dict(code=code, **{'class': 'waiting'}, owner='D2',
               next=dict(kind='wait', ends='The recorded owner or capacity becomes available.'))
    for code in (
        'rate_limited', 'review_capacity_wait', 'review_invocation_pending', 'provider_state_capacity',
        'execution_live', 'remote_operation_owned', 'verification_ingest_pending',
        'store_busy', 'lock_timeout', 'review_provider_unavailable', 'review_sequence_wait')
}
STOP_REGISTRY.update({
    'reading_missing': dict(code='reading_missing', **{'class': 'decision'}, owner='D2', replan=True,
        next=dict(kind='handoff', step=13, command='read')),
    'source_link_not_fulltext': dict(code='source_link_not_fulltext', **{'class': 'decision'}, owner='D2',
        next=dict(kind='continue', command='review-run')),
    'invalid_review_capacity_probe': dict(code='invalid_review_capacity_probe', **{'class': 'decision'},
        owner='D1', next=dict(kind='handoff', step=16, command='review-probe')),
    'review_route_capacity_below_floor': dict(code='review_route_capacity_below_floor', **{'class': 'decision'},
        owner='D1', reasons=('no_parts_delivery', 'binary_input_over_measure',
                             'binary_input_without_bound', 'body_without_text_form'),
        next=dict(kind='handoff', step=14, command='review-assignment')),
    'review_series_no_progress': dict(code='review_series_no_progress', **{'class': 'harness_defect'},
        owner='D2', proof='test_automatic_gate_override.RecordedSeriesStopTests.'
                         'test_unchanged_repeat_records_the_harness_stop_and_ended_schedule',
        next=dict(kind='handoff', command='gate-auto-pass')),
    'review_chain_stopped': dict(code='review_chain_stopped', **{'class': 'requested'}, owner='D2',
        next=dict(kind='handoff', step=10, command='review-stop')),
})
STOP_REGISTRY.update({
    'review_route_capacity_plan_violated': dict(code='review_route_capacity_plan_violated',
        **{'class': 'harness_defect'}, owner='D1',
        proof='test_review_capacity_plan.CapacityPlanTests.test_exact_request_above_pinned_bound_is_harness_defect',
        next=copy.deepcopy(STOP_REGISTRY['review_series_no_progress']['next'])),
})
STOP_REGISTRY['review_route_capacity_below_floor']['problem_chain_next'] = dict(
    kind='decision', owner_decides=ADMISSION_QUESTION,
    then=[dict(answer=ADMISSION_SMALLER_ANSWER, steps=[dict(id=4, command='export'),
                 dict(command='review-assignment', check=True)]),
          dict(answer=ADMISSION_ROUTE_ANSWER, steps=[dict(id=7, command='review-route'),
                 dict(command='review-probe'), dict(id=4, command='export')])])
for _code, _outcome in (
        ('review_provider_refused', 'provider_refused'), ('review_output_over_cap', 'output_over_cap'),
        ('review_response_too_large', 'response_over_limit'),
        ('review_input_too_large', 'input_over_limit'), ('review_timeout', 'timeout'),
        ('review_execution_incomplete', 'execution_incomplete'), ('review_invocation_interrupted', 'interrupted'),
        ('review_answer_malformed', 'answer_malformed'), ('review_failure_unknown', 'unknown'),
        ('review_format_unsupported', 'format_unsupported')) + tuple(
            (code, 'scientific_refused') for code in _OUTPUT_CONTRACT_CODES_REVIEW_A) + _CALL_OUTCOMES_REVIEW_A:
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='outcome', outcome=_outcome))
for _code in (
        'invalid_locator',
        'invalid_native_review',
        'invalid_reading_response',
        'invalid_record',
        'invalid_review_adjudication',
        'invalid_review_source_request',
        'invalid_review_staged',
        'invalid_value_review',
        'research_objection_identity_conflict',
        'source_mismatch',
        'value_review_support_missing',
        'openness_minimum_required',
        'openness_search_date',
):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='outcome', outcome='scientific_refused'))
for _code in ('review_slots_fixed', 'review_resampling_forbidden', 'review_adjudicator_fixed',
              'value_review_duplicate',
              'review_slot_exhausted', 'review_correction_exhausted', 'review_correction_ineligible',
              'review_refusal_notice_metadata_too_large'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2', next=dict(kind='role'))
for _code, _step, _command in (
        ('review_runtime_missing', 7, 'review-probe'), ('review_route_runtime_stale', 7, 'review-route'),
        ('review_context_unverified', 7, 'review-probe'),
        ('review_context_contaminated', 1, 'review-assignment'),
        ('review_context_repair_required', 1, 'review-assignment'),
        ('review_context_repair_mismatch', 1, 'review-assignment'),
        ('review_context_repair_unverified', 1, 'review-assignment'),
        ('review_context_repair_stale', 1, 'review-assignment'),
        ('native_review_attestation_required', 5, 'search review-packet'),
        ('openness_review_not_approved', 4, 'export')):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='handoff', step=_step, command=_command))
for _code, _command in (
        ('openness_search_stale', 'search'),
        ('review_staged_scale_unsupported', 'review-series'),
        ('review_source_no_progress', 'fulltext'),
        ('native_review_acceptance_required', 'search accept'),
        ('verification_verdict_body_required', 'bind-verdict'),
        ('review_route_retired', 'review-assignment'),
        ('review_recording_required', 'review'),
        ('review_worker_reconciliation_required', 'review-attempt'),
        ('review_parts_pending', 'review-series'),
        ('problem_evidence_part_pending', 'review-series')):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='handoff', command=_command))
STOP_REGISTRY['assessment_incomplete'] = dict(code='assessment_incomplete',
    **{'class': 'decision'}, owner='D2',
    next=dict(kind='decision', owner_decides='How should the incomplete assessment be answered?',
        then=[dict(answer='Check the retained record', steps=[dict(command='record-check')]),
              dict(answer='List what the workspace owes', steps=[dict(command='status')])]))
_STORED_STATE_CODES_FIRST = ()


for _code, _command in (
        ('xml_depth_limit', 'fulltext'), ('xml_element_limit', 'fulltext'),
        ('xml_external_entity', 'fulltext'), ('xml_internal_entity', 'fulltext'),
        ('xml_text_limit', 'fulltext'),
        ('loop_closure_missing', 'loop-close'), ('loop_closure_stale', 'loop-close'),
        ('search_dispositions_missing', 'search'), ('search_evidence_stale', 'search'),
        ('search_frontier_stale', 'search'), ('search_scope_stale', 'search'),
        ('reference_identity_ambiguous', 'bundle'),
        ('store_recovery_required', 'recover'),
        ('gate_override_public_write', 'gate-override')):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
        next=dict(kind='handoff', command=_command, payload={},
                  chooses=[] if _command == 'recover' else ['command inputs']))
STOP_REGISTRY['output_view_render_deferred'] = dict(code='output_view_render_deferred',
    **{'class': 'waiting'}, owner='D2',
    next=dict(kind='wait', ends='The output renderer releases the workspace render lock.'))
for _code in ('invalid_identifier', 'unsafe_url'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='outcome', outcome='scientific_refused'))


_STORED_STATE_CODES_STUDY = (
    'development_question_repeated',
    'execution_output_mismatch',
    'failure_scope_mismatch',
    'inheritance_assumptions_missing',
    'inheritance_mismatch',
    'inheritance_missing',
    'inheritance_refresh_evidence_changed',
    'inheritance_refresh_mismatch',
    'inheritance_result_mismatch',
    'invalid_source_deferral',
    'lineage_cycle',
    'objective_scope_mismatch',
    'outside_reading_unit',
    'unknown_cycle',
    'unknown_review',
    'unknown_review_assignment',
)
for _code in ('review_turn_failures_exhausted', 'review_parts_budget_exhausted',
              'review_quality_measurement_pending', 'review_invocation_unverified',
              'review_output_unavailable', 'review_inventory_mismatch', 'review_source_parent_unverified',
              'review_history_invalid', 'artifact_missing', 'artifact_corrupt', 'review_budget_exhausted'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
        next=dict(kind='decision', owner_decides='How should the state of the record that the refusal reports be treated?',
                  then=[dict(answer='Check the retained record', steps=[dict(command='record-check')])]))
STOP_REGISTRY.update({
    'openness_minimum_gaps': dict(code='openness_minimum_gaps', **{'class': 'decision'}, owner='D3',
        next=dict(kind='handoff', command='export', payload=dict(kind='problem-openness'),
                  chooses=['spec', 'accept_gaps'], prerequisites=[dict(condition=
                      'Repair the gaps named in the refusal, or export with --accept-gaps.')])),
    'gate_override_condition_unmet': dict(code='gate_override_condition_unmet', **{'class': 'decision'}, owner='D3',
        next=dict(kind='decision', owner_decides='Whether to lift the unmet condition of the recorded instruction.',
                  then=[dict(answer='Lift the condition', steps=[dict(command='gate-override',
                            chooses=['id', 'gate', 'instruction', 'lifts'])]),
                        dict(answer='Meet the condition', steps=[], prerequisites=[dict(condition=
                            'Complete the work that the obligations of the condition name.')])])),
})

STOP_REGISTRY['problem_bar_calibration_required'] = dict(code='problem_bar_calibration_required',
    **{'class': 'decision'}, owner='D2',
    next=dict(kind='decision', owner_decides='Whether to refresh the bar for the current problem.',
        then=[dict(answer='Refresh the named bar', steps=[dict(command='review-assignment',
            payload=dict(role='bar', context={}),
            chooses=['id', 'dossier_id', 'author_id', 'reviewer_id', 'route_id'],
            prerequisites=[dict(condition=(
                'Use the reviewer, route, author and dossier of the bar named by review_id.'))])])]))


_STORED_STATE_CODES_STORE = (
    'constitution_archive_missing', 'corrupt_state', 'invalid_profile', 'unsupported_schema',
)
STOP_REGISTRY['problem_already_posted'] = dict(code='problem_already_posted', **{'class': 'decision'},
                                             owner='D2', next=dict(kind='done'))
from .openness_reading import PROJECTION_STOP, PROBLEM_PROJECTION_DECISION, WAIT_PROBLEM_ANSWER, SMALLER_PACKET_ANSWER
STOP_REGISTRY[PROJECTION_STOP] = dict(code=PROJECTION_STOP, **{'class': 'decision'}, owner='D1',
    next=dict(kind='decision', owner_decides=PROBLEM_PROJECTION_DECISION,
              then=[dict(answer=WAIT_PROBLEM_ANSWER, steps=[dict(id=4, command='export',
                    prerequisites=[dict(condition=(
                        'The installed release holds a reading protocol that bounds the record of this chain.'))])]),
                    dict(answer=SMALLER_PACKET_ANSWER, steps=[dict(id=4, command='export'),
                         dict(command='review-assignment', check=True)])]))
for _code in ('stale_revision', 'review_task_dependency_changed', 'review_output_pending', 'review_sources_pending',
              'review_runtime_changed'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2', next=dict(kind='continue'))
STOP_REGISTRY['openness_packet_outdated'] = dict(code='openness_packet_outdated', **{'class': 'decision'},
    owner='D3', next=dict(kind='handoff', step=4, command='export'))
STOP_REGISTRY['search_completion_invalid'] = dict(code='search_completion_invalid', **{'class': 'decision'},
    owner='D3', next=dict(kind='handoff', command='search'))
STOP_REGISTRY['openness_finding_invalid'] = dict(code='openness_finding_invalid', **{'class': 'decision'},
    owner='D3', next=dict(kind='continue', command='review-run'))
STOP_REGISTRY['openness_result_unlinked'] = dict(code='openness_result_unlinked', **{'class': 'decision'},
    owner='D3', next=dict(kind='handoff', command='search deliver-attempt', then=[dict(command='export')]))
STOP_REGISTRY['openness_minimum_gaps']['next']['step'] = 4
for _code in ('openness_result_gap', 'openness_result_changed'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D3', next=dict(kind='continue'))

# Refusal replacement admission and immutable origin checks (D2 R11-R14, R57).
for _code in ('review_refusal_replacement_ineligible', 'review_refusal_replacement_mismatch',
              'review_refusal_replacement_origin_invalid'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='handoff', step=1, command='review-assignment'))


STOP_REGISTRY.update({
    'prediction_required': dict(code='prediction_required', **{'class': 'decision'}, owner='D2',
        next=dict(kind='outcome', outcome='scientific_refused')),
    'literature_comparison_stale': dict(code='literature_comparison_stale', **{'class': 'decision'}, owner='D2',
        next=dict(kind='decision', owner_decides='How should the literature comparison of the plan be brought up to date?',
            then=[dict(answer='Record a plan whose literature comparison has the exact scope of the plan, no gaps and, '
                               'where the plan needs one, the current literature digest',
                steps=[dict(command='cycle', chooses=[
                    'id', 'author', 'objective', 'scope', 'hypothesis', 'question', 'strategy',
                    'predecessor', 'inheritance', 'reopening', 'distinguishing_test', 'expected_outcomes',
                    'failure_signals', 'evidence_requirements', 'literature', 'resource_limits'])])])),
    'problem_citation_locator_missing': dict(code='problem_citation_locator_missing',
        **{'class': 'decision'}, owner='D2',
        next=dict(kind='decision', owner_decides='Which retained original supplies the missing citation location?',
            then=[dict(answer='Bind the location to the existing reading', steps=[dict(
                command='problem-citation-locator',
                chooses=['id', 'version_id', 'reading_id', 'original_sha256', 'url', 'source_id'])])])),
})


_STORED_STATE_CODES_DECISION = (
    'gate_override_gate',
    'gate_override_transition',
    'invalid_round',
    'invalid_synthesis',
    'objective_mismatch',
    'problem_attempt_extension_unexplained',
    'problem_attempt_outcome_mismatch',
    'problem_attempt_scope_restricted',
    'problem_attempt_statement_mismatch',
    'problem_citation_span_mismatch',
    'problem_forming_origin_conflict',
    'problem_post_revision_missing',
    'publication_evidence_mismatch',
    'publication_scope_corrupt',
    'publication_scope_objective_open',
    'scope_assumptions_missing',
    'scoped_review_corrupt',
)

for _code in ('invalid_rubric_grades', 'invalid_strategy'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
        next=dict(kind='outcome', outcome='scientific_refused'))

# A new citation needs corrected evidence or metadata before the refused write can succeed.
for _code in ('problem_citation_text_character', 'problem_citation_text_length',
              'problem_citation_locator_character', 'problem_citation_locator_length',
              'problem_citation_locator_format'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
        next=dict(kind='outcome', outcome='scientific_refused'))

# These commands and their input choices come from the CLI reference. A decision
# carries its own payload rule, so a non-review command receives no assignment_id.
for _codes, _command, _payload, _chooses in (
        (('configuration_missing',), 'init', {}, ('profile', 'target')),
        (('constitution_revalidation_required',), 'constitution', {}, ('previous_sha256', 'reason')),
        (('grand_challenge_missing',), 'grand-challenge', {}, ('id', 'reason', 'challenges')),
        (('problem_attempt_superseded',), 'problem-attempt', {},
         ('id', 'statement_id', 'kind', 'solver', 'literature', 'outcomes', 'proportion')),
        (('problem_attempt_unbound', 'problem_result_unbound'), 'search deliver-attempt', {},
         ('attack_root', 'expected_revision', 'request_id')),
        (('problem_citation_evidence_unbound',), 'problem-citation-metadata', {},
         ('id', 'version_id', 'title', 'authors', 'evidence')),
        (('problem_citation_locator_unbound',), 'problem-citation-locator', {},
         ('id', 'version_id', 'reading_id', 'original_sha256', 'url', 'source_id')),
        (('problem_objections_refresh_required',), 'problem-objections-carry', {},
         ('id', 'source_workspace', 'source_revision')),
        (('problem_result_extension_unexplained', 'problem_result_missing', 'problem_result_superseded',
          'problem_value_stale'), 'problem-value', {},
         ('id', 'statement_id', 'accounts', 'motivation', 'changes_if_resolved', 'current_state')),
        (('problem_value_reading_missing',), 'read', {},
         ('id', 'version_id', 'depth', 'bundle_id', 'inspections', 'notes')),
        (('resource_budget_exhausted',), 'budget', {}, ('profile', 'purpose', 'limits', 'reason')),
        (('round_exemplar_missing',), 'require-fulltext', {'purpose': 'exemplar'},
         ('id', 'profile', 'version_id', 'reason')),
        (('round_search_missing',), 'search', {},
         ('id', 'profile', 'purpose', 'queries', 'responses', 'captured_at', 'scope', 'found_work_ids',
          'verdict', 'cited_work_ids', 'impact', 'gaps', 'dispositions')),
):
    for _code in _codes:
        STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
            next=dict(kind='decision', owner_decides='How should this prerequisite be completed?',
                then=[dict(answer='Complete the prerequisite', steps=[dict(command=_command,
                    payload=copy.deepcopy(_payload), chooses=list(_chooses))])]))


def describe_unlisted_step():
    """Return the R56 decision for a code without a reviewed registry row."""
    return dict(kind='decision', owner_decides='How should the retained error be resolved?',
        then=[dict(answer='Inspect the record', steps=[dict(command='record-check')])])


_NAMED_CAUSE_CODES_ENTRY = (
    'abstract_reading_incomplete',
    'abstract_reading_missing',
    'and_context_not_established',
    'bibliography_incomplete',
    'branch_disposition_missing',
    'branch_resolution_unverified',
    'candidate_checkpoint_stale',
    'case_field_conflict',
    'claim_source_support_unresolved',
    'cohort_abstract_reading_missing',
    'cohort_missing',
    'collection_incomplete',
    'collection_pending',
    'component_page_missing',
    'context_missing',
    'contribution_evidence_missing',
    'critical_source_unavailable',
    'development_assessment_missing',
    'development_dependencies_stale',
    'direction_reading_missing',
    'direction_root_reading_missing',
    'direction_search_incomplete',
    'direction_search_missing',
    'direction_standards_missing',
    'direction_standards_unresolved',
    'doctrine_coverage_missing',
    'dynamic_visual_unsupported',
    'embedded_visual_dependency_unsupported',
    'embedded_visual_unsupported',
    'execution_coverage_missing',
    'execution_pending',
    'expected_outcome_unresolved',
    'external_case_within_field',
    'external_cases_excess',
    'external_cases_insufficient',
    'failure_signal_unresolved',
    'fulltext_reading_missing',
    'historical_version_unresolved',
    'independent_review_missing',
    'independent_review_pending',
    'innovation_candidates_missing',
    'innovation_case_not_candidate',
    'innovation_event_order_unresolved',
    'innovation_missing',
    'innovation_paper_type_unresolved',
    'invalid_alias',
    'invalid_primary_category',
    'invalid_work',
    'missing_abstract',
    'missing_primary_category',
    'nonmonotone_version_dates',
    'novelty_comparison_stale',
    'novelty_source_missing',
    'objective_incomplete',
    'objective_missing',
    'objective_scope_incomplete',
    'placement_missing',
    'population_changed',
    'primary_category_conflict',
    'primary_category_unasserted',
    'problem_already_solved',
    'problem_openness_missing',
    'problem_openness_stale',
    'problem_openness_unresolved',
    'profile_mismatch',
    'prospective_execution_missing',
    'rationale_missing',
    'reading_bundle_stale',
    'reference_unresolved',
    'remaining_obligations',
    'required_evidence_missing',
    'required_result_unverified',
    'required_unit_incomplete',
    'required_unit_missing',
    'required_unit_uninspected',
    'required_validation_unverified',
    'resource_limit_exceeded',
    'result_evidence_missing',
    'result_execution_incomplete',
    'result_scope_unplanned',
    'review_adjudication_evidence_undelivered',
    'review_adjudication_missing',
    'review_assignment_missing',
    'review_assurance_dependency_pending',
    'review_card_pending',
    'review_card_question_pending',
    'review_claim_cell_pending',
    'review_claim_unresolved',
    'review_combination_pending',
    'review_correction_inadmissible',
    'review_finding_answer_pending',
    'review_frame_unresolved',
    'review_integration_pending',
    'review_integration_unresolved',
    'review_objection_evidence_pending',
    'review_original_check_pending',
    'review_output_refused',
    'review_retrieval_pending',
    'review_source_coverage_pending',
    'review_task_exhausted',
    'review_terminal_pending',
    'review_transport_failure',
    'review_usage_window_insufficient',
    'root_missing',
    'roots_missing',
    'sample_reading_missing',
    'sample_stale',
    'scoped_review_missing',
    'scoped_review_pending',
    'scoped_target_rejected',
    'screening_audit_failed',
    'screening_audit_reading_missing',
    'screening_missing',
    'search_page_capture_interval',
    'search_page_metadata_mismatch',
    'search_pagination_unsupported',
    'search_pending',
    'search_purpose_missing',
    'source_bundle_incomplete',
    'source_bundle_missing',
    'standards_applicability_unresolved',
    'standards_missing',
    'stop_record_error',
    'synthesis_dependencies_stale',
    'target_mismatch',
    'target_source_pin_invalid',
    'target_source_pin_missing',
    'unstructured_authors',
    'useful_branch_remaining',
    'useful_development_remaining',
    'validity_check_missing',
    'validity_evidence_unbound',
    'validity_unresolved',
    'visual_asset_missing',
    'visual_asset_pending',
    'visual_asset_stale',
    'visual_asset_unlinked',
    'visual_document_style_unsupported',
    'visual_markup_ambiguous',
    'visual_resource_missing',
    'visual_scope_unresolved',
    'visual_srcset_unsupported',
    'visual_style_unsupported',
    'visual_url_unsupported',
    'within_field_case_missing',
    'work_missing',
)
_NAMED_CAUSE_CODES_RECORD = (
    'ambiguous_alias',
    'candidate_checkpoint_missing',
    'claim_reassessment_required',
    'component_evidence_uninspected',
    'component_pending',
    'configuration_not_ready',
    'execution_output_seal_required',
    'exploratory_evidence',
    'inheritance_refresh_stale',
    'inherited_result_stale',
    'missing_version',
    'openness_evidence_mismatch',
    'openness_native_evidence_missing',
    'openness_native_review_mismatch',
    'openness_native_review_unverified',
    'openness_screening_evidence_mismatch',
    'openness_search_missing',
    'openness_source_delivery_mismatch',
    'openness_statement_mismatch',
    'orchestration_conflict',
    'problem_assessment_evidence_missing',
    'problem_candidate_missing',
    'problem_forming_reference_missing',
    'problem_statement_missing',
    'problem_value_computation_missing',
    'problem_value_result_missing',
    'publication_scope_correction_required',
    'publication_scope_debt_mismatch',
    'publication_scope_missing',
    'publication_scope_stale',
    'readiness_required',
    'research_not_ready',
    'research_review_binding_stale',
    'research_review_pair_required',
    'research_review_phase_mismatch',
    'research_review_stale',
    'research_review_unverified',
    'research_support_scope_mismatch',
    'result_evidence_mismatch',
    'review_adjudication_unverified',
    'review_bundle_mismatch',
    'review_complete_source_mismatch',
    'review_current_source_incomplete',
    'review_objection_missing',
    'review_packet_incomplete',
    'review_record_missing',
    'review_source_binding_mismatch',
    'review_task_failed',
    'review_task_index_required',
    'round_evidence_mismatch',
    'scoped_review_stale',
    'strategy_evidence_stale',
    'strategy_reference_missing',
    'unknown_execution',
    'unknown_source',
    'unknown_work',
    'value_review_assignment_mismatch',
    'value_review_phase_mismatch',
)
_NAMED_CAUSE_CODES_CONDITIONS = (
    'check_unsupported',
    'component_hash_mismatch',
    'component_parent_mismatch',
    'constitution_unavailable',
    'dependency_cycle',
    'extraction_cleanup_failed',
    'extraction_failed',
    'extraction_timeout',
    'extraction_too_large',
    'fulltext_storage_limit',
    'fulltext_transfer_changed',
    'fulltext_unit_limit',
    'http_status',
    'incomplete_response',
    'invalid_availability',
    'invalid_component',
    'invalid_family_selection',
    'invalid_json',
    'invalid_numerical_projection',
    'invalid_oai_chain',
    'invalid_preparation',
    'invalid_publication_scope',
    'invalid_redirect',
    'invalid_response',
    'invalid_review_attempt',
    'invalid_review_context',
    'invalid_review_correction',
    'invalid_scientific_delivery',
    'invalid_snapshot_guard',
    'invalid_visual_asset',
    'network_error',
    'openness_native_evidence_undelivered',
    'openness_native_evidence_unsupported',
    'package_member_conflict',
    'partial_response',
    'private_mixed_artifact_required',
    'problem_account_reference_cycle',
    'problem_account_reference_missing',
    'problem_citation_work_ineligible',
    'problem_forming_provenance_invalid',
    'problem_objection_origin_conflict',
    'problem_objection_provenance_invalid',
    'problem_objection_source_unavailable',
    'problem_objection_sources_exceeded',
    'profile_inapplicable',
    'provider_error',
    'record_conflict',
    'redirect_limit',
    'response_too_large',
    'review_complete_source_route',
    'review_correction_invalid',
    'review_correction_unverified',
    'review_credential_missing',
    'review_evidence_incomplete',
    'review_evidence_too_large',
    'review_evidence_unreadable',
    'review_exact_cost_invalid',
    'review_exact_text_invalid',
    'review_image_input_invalid',
    'review_incomplete',
    'review_instrumented_capture_required',
    'review_packet_mismatch',
    'review_parts_inventory_changed',
    'review_parts_route_unsupported',
    'review_request_policy_mismatch',
    'review_route_unsupported',
    'review_source_continuation_fork',
    'review_staged_binary_unsupported',
    'review_verbatim_evidence_invalid',
    'review_visual_input_mismatch',
    'review_visual_repair_mismatch',
    'review_visual_route_mismatch',
    'review_visual_source_mismatch',
    'scientific_projection_incomplete',
    'storage_io',
    'store_reentrant_access',
    'timeout',
    'transaction_closed',
    'unexpected_mime',
    'unknown_fulltext_job',
    'unsafe_destination',
    'unsupported_encoding',
    'value_review_support_identity',
    'verification_target_mismatch',
    'verification_target_pending',
    'visual_asset_dependencies_unsupported',
)
_NAMED_CAUSE_CODES_RECOVERY = (
    'cohort_selection_conflict',
    'cohort_selection_stale',
    'component_requirement_missing',
    'execution_observation_required',
    'fulltext_resume_unsupported',
    'gate_override_invalid',
    'invalid_cohort_selection',
    'invalid_development',
    'invalid_import',
    'invalid_reading',
    'invalid_reference_handle',
    'invalid_search',
    'malformed_visual_asset',
    'openness_reading_incomplete',
    'problem_author_unknown',
    'query_ceiling',
    'readiness_review_stale',
    'request_budget',
    'review_assignment_replaced',
    'review_assignment_superseded',
    'review_bar_continuity',
    'review_epoch_stale',
    'review_evidence_undelivered',
    'review_not_independent',
    'review_parts_final_too_large',
    'review_parts_frame_too_large',
    'review_parts_inventory_too_large',
    'review_parts_no_progress',
    'review_series_superseded',
    'review_source_continuation_mismatch',
    'review_source_continuation_unverified',
    'review_task_pending',
    'sample_missing',
    'source_deferred',
    'source_pending',
    'unsupported_visual_asset',
    'validity_evidence_missing',
    'verification_task_required',
    'xml_timeout',
)
_NAMED_CAUSE_CODES_CLOSING = (
    'composite_missing',
    'final_pending',
    'requested_range_not_delivered',
    'series_invalid',
    'unit_not_delivered',
    'unobserved_turn',
    'artifact_sealed',
    'execution_observation_mismatch',
    'extractor_missing',
    'gate_override_instruction',
    'invalid_import_identity',
    'invalid_input',
    'invalid_native_review_packet',
    'invalid_review_material',
    'invalid_review_session_turn',
    'invalid_review_route',
    'invalid_target',
    'malformed_pdf',
    'openness_reading_gap',
    'openness_search_gap',
    'openness_settling_source',
    'problem_attempt_evidence_missing',
    'process_gone',
    'provider_state_unavailable',
    'publication_scope_correction_mismatch',
    'review_context_repair_history_unrecorded',
    'review_context_repair_pending',
    'review_correction_duplicate',
    'review_input_unreadable',
    'review_invocation_unverified',
    'review_observation_acceptance_pending',
    'review_parts_target_label_missing',
    'unknown_assessment',
    'unknown_checkpoint',
    'unsafe_path',
)
_STORED_STATE_CODES_RECORD = (
    'store_missing',
)
_STORED_STATE_CODES_CONDITIONS = (
    'gate_override_stop_untrusted',
    'request_id_conflict',
    'review_output_mismatch',
)
_STORED_STATE_CODES_RECOVERY = (
    'review_route_changed',
)
_STORED_STATE_CODES_CLOSING = ()
_STORED_STATE_CODES_COMPOSITE = ('review_composite_unverified',)


STOP_GROUPS = {
    'stored_state': dict(id='stored_state', **{'class': 'decision'}, owner='D2',
        next=copy.deepcopy(STOP_REGISTRY['artifact_corrupt']['next']),
        conditions=dict(
            store='It checks the store itself: its files, its lock, its schema, its history or its journal.',
            content='It compares stored content with other stored content, a schema, a digest or a version, '
                    'and the state is one that the commands of the harness do not produce in their ordinary order.',
            payload='It reads only the payload of an executed command, and the planner built that payload.')),
}

STOP_GROUPS['named_cause'] = dict(id='named_cause', **{'class': 'decision'}, owner='D2',
    next=dict(kind='decision', owner_decides='How should the cause that the refusal names be answered?',
        then=[dict(answer='Check the retained record', steps=[dict(command='record-check')]),
              dict(answer='List what the workspace owes', steps=[dict(command='status')])]),
    conditions=dict(
        entry='It writes the code as the code of an entry in a report, an obligation list or a member of a line.',
        record='It refuses because a record that another command of the harness writes is absent, stale, '
               'pending or incomplete.',
        environment='It reads something outside the workspace: the network, a provider, a credential, the clock, '
                    'a process, or a file or a store outside the workspace.',
        form='It reads an input of a command, a source or a route, and the input has a form that the harness '
             'does not read.',
        limit='It compares a size or a count with a bound.',
        harness='It checks how the code of the harness uses an object of the store that a caller holds: '
                'a transaction, a snapshot guard, a preparation or the lock of the calling thread.',
        **STOP_GROUPS['stored_state']['conditions']))


STOP_GROUPS['visual_text_reading'] = dict(id='visual_text_reading', **{'class': 'decision'}, owner='D1',
    next=dict(kind='decision', owner_decides=VISUAL_TEXT_ROUTE_QUESTION,
        then=[dict(answer=VISUAL_TEXT_ROUTE_ANSWER, steps=[
            dict(command='review-route'), dict(command='review-probe'), dict(command='export'),
            dict(command='review-assignment', check=True), dict(command='review-assignment')])]),
    conditions=dict(route='A v7 problem reading delivers text units on a route whose native contract requires page images.'))


# Each review lane adds its own (group, codes) tuple here with its evidence file.
for _group, _codes in (
        ('visual_text_reading', (VISUAL_TEXT_ROUTE_CODE,)),
        ('named_cause', _NAMED_CAUSE_CODES_ENTRY),
        ('named_cause', _NAMED_CAUSE_CODES_RECORD),
        ('named_cause', _NAMED_CAUSE_CODES_CONDITIONS),
        ('named_cause', _NAMED_CAUSE_CODES_RECOVERY),
        ('named_cause', _NAMED_CAUSE_CODES_CLOSING),
        ('stored_state', _STORED_STATE_CODES_RECORD),
        ('stored_state', _STORED_STATE_CODES_CONDITIONS),
        ('stored_state', _STORED_STATE_CODES_RECOVERY),
        ('stored_state', _STORED_STATE_CODES_CLOSING),
        ('stored_state', _STORED_STATE_CODES_COMPOSITE),
        ('stored_state', _STORED_STATE_CODES_FIRST),
        ('stored_state', _STORED_CONTENT_CODES_REVIEW_A),
        ('stored_state', _STORED_CONTENT_CODES_REVIEW_B),
        ('stored_state', _STORED_STATE_CODES_STORE),
        ('stored_state', _STORED_STATE_CODES_DECISION),
        ('stored_state', _STORED_STATE_CODES_STUDY),
):
    for _code in _codes:
        _row = copy.deepcopy(STOP_GROUPS[_group])
        _row.pop('id')
        _row.pop('conditions')
        STOP_REGISTRY[_code] = dict(_row, code=_code, group=_group)


STOP_REGISTRY['review_pending_delivery_over_plan'] = dict(
    code='review_pending_delivery_over_plan', **{'class': 'decision'}, owner='D1',
    next=dict(kind='handoff', step=12, command='select-main-capture'))


def validate_registry(rows, groups=None):
    """Require a next step and an executable proof for every defect classification."""
    if groups is None:
        groups = STOP_GROUPS
    for code, row in rows.items():
        if (row['code'] != code or row['class'] not in ('waiting', 'harness_defect', 'decision', 'requested')
                or not row.get('owner') or not row.get('next')):
            raise ValueError('A stop registry row needs its code, class, owner and next step: ' + code)
        if row['class'] == 'harness_defect' and not row.get('proof'):
            raise ValueError('A harness-defect row needs its guard test: ' + code)
        if 'group' in row:
            group = row['group']
            if group not in groups:
                raise ValueError('Unknown stop group for registry row: ' + code + ': ' + str(group))
            expected = {key: value for key, value in groups[group].items()
                        if key not in ('id', 'conditions')}
            expected.update(code=code, group=group)
            if row != expected:
                raise ValueError('Registry row does not match its stop group: ' + code)


validate_registry(STOP_REGISTRY)
WAITING_CODES = frozenset(code for code, row in STOP_REGISTRY.items() if row['class'] == 'waiting')
HARNESS_DEFECT_CODES = frozenset(code for code, row in STOP_REGISTRY.items() if row['class'] == 'harness_defect')
EXPECTED_STOP_CODES = frozenset(code for code, row in STOP_REGISTRY.items() if row['class'] != 'harness_defect')
STOP_REGISTRY['store_busy']['replan'] = True
REPLAN_CODES = frozenset(code for code, row in STOP_REGISTRY.items()
                         if row['next']['kind'] == 'continue' or row.get('replan'))


def classify_stop(code):
    return STOP_REGISTRY.get(code, {}).get('class', 'decision')


def describe_terminal(code, *, action=None):
    """Describe a refused action without changing its recorded error or evidence."""
    row = STOP_REGISTRY.get(code)
    next_step = copy.deepcopy(row['next']) if row is not None else describe_unlisted_step()
    kind = next_step.pop('kind')
    state = dict(wait='wait', handoff='handoff', done='done', **{'continue': 'continue'}).get(kind, 'stop')
    if classify_stop(code) == 'harness_defect':
        state = 'stop'
    result = dict(state=state, code=code, stop_class=classify_stop(code), details={})
    if row is None:
        result['details']['unlisted'] = True
        if action is not None:
            result['details']['refused_action'] = copy.deepcopy(action)
    if 'command' in next_step:
        result['command'] = next_step.pop('command')
    if kind == 'decision':
        result['details']['decision'] = next_step
    else:
        result['details']['next'] = dict(kind=kind, **next_step)
    return result
