"""The stop classes and next steps shared by the store and math controller.

D2 owns this registry. Each package adds its codes here when its unit ships.
Unknown codes retain the decision class used by stored records.
"""

import copy


_STORED_CONTENT_CODES_REVIEW_A = (
    'review_answer_recovery_invalid',
    'review_correction_route_mismatch',
    'review_history_ambiguous',
    'review_objection_evidence_invalid',
    'review_parts_delivery_invalid',
    'review_prompt_mismatch',
    'review_request_changed',
    'review_invocation_unverified',
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
    'invalid_native_review_packet',
    'invalid_review_prompt_version',
    'invalid_review_role',
    'invalid_review_route',
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
    'review_route_capacity_below_floor': dict(code='review_route_capacity_below_floor',
        **{'class': 'decision'}, owner='D1', next=dict(kind='handoff', step=7, command='review-route')),
})
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
              'review_slot_exhausted', 'review_correction_exhausted', 'review_correction_ineligible'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2', next=dict(kind='role'))
for _code, _step, _command in (
        ('review_runtime_missing', 7, 'review-probe'), ('review_route_runtime_stale', 7, 'review-route'),
        ('review_context_unverified', 7, 'review-probe'),
        ('review_context_contaminated', 1, 'review-assignment'),
        ('review_route_retired', 1, 'review-assignment'),
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
        ('review_recording_required', 'review'),
        ('review_worker_reconciliation_required', 'review-attempt'),
        ('assessment_incomplete', 'review-assignment'),
        ('review_parts_pending', 'review-series'),
        ('problem_evidence_part_pending', 'review-series')):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='handoff', command=_command))
_STORED_STATE_CODES_FIRST = ('review_input_unreadable',)


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
    'development_question_repeated', 'execution_observation_mismatch', 'execution_output_mismatch',
    'failure_scope_mismatch', 'inheritance_assumptions_missing', 'inheritance_mismatch',
    'inheritance_missing', 'inheritance_refresh_evidence_changed', 'inheritance_refresh_mismatch',
    'inheritance_result_mismatch', 'invalid_import_identity',
    'invalid_source_deferral', 'invalid_target', 'lineage_cycle', 'objective_scope_mismatch',
    'outside_reading_unit', 'unknown_assessment', 'unknown_checkpoint', 'unknown_cycle',
    'unknown_review', 'unknown_review_assignment',
)
for _code in ('review_turn_failures_exhausted', 'review_parts_budget_exhausted',
              'review_quality_measurement_pending', 'review_invocation_unverified',
              'review_output_unavailable', 'review_inventory_mismatch', 'review_source_parent_unverified',
              'review_history_invalid', 'artifact_missing', 'artifact_corrupt', 'review_budget_exhausted'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
        next=dict(kind='decision', owner_decides='How should this review boundary be resolved?',
                  then=[dict(answer='Check the retained record', steps=[dict(command='record-check')])]))
STOP_REGISTRY.update({
    'openness_minimum_gaps': dict(code='openness_minimum_gaps', **{'class': 'decision'}, owner='D3',
        next=dict(kind='handoff', command='export', payload=dict(kind='problem-openness'),
                  chooses=['spec', 'accept_gaps'], prerequisites=[dict(condition=
                      'Repair the gaps named in the refusal, or export with --accept-gaps.')])),
    'gate_override_condition_unmet': dict(code='gate_override_condition_unmet', **{'class': 'decision'}, owner='D3',
        next=dict(kind='decision', owner_decides='Whether the action proceeds without the unmet condition.',
                  then=[dict(answer='Proceed without the condition', steps=[dict(command='gate-override',
                            chooses=['id', 'gate', 'instruction', 'lifts'])]),
                        dict(answer='Meet the condition', steps=[], prerequisites=[dict(condition=
                            'Complete the work named by the condition-check obligations.')])])),
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
    'constitution_archive_missing', 'contract_changed', 'corrupt_artifact', 'corrupt_blob',
    'corrupt_state', 'invalid_digest', 'invalid_profile', 'request_conflict', 'unsupported_schema',
)
STOP_REGISTRY['problem_already_posted'] = dict(code='problem_already_posted', **{'class': 'decision'},
                                             owner='D2', next=dict(kind='done'))
from .openness_reading import PROJECTION_STOP, PROBLEM_PROJECTION_DECISION, WAIT_PROBLEM_ANSWER, SMALLER_PACKET_ANSWER
STOP_REGISTRY[PROJECTION_STOP] = dict(code=PROJECTION_STOP, **{'class': 'decision'}, owner='D1',
    next=dict(kind='decision', owner_decides=PROBLEM_PROJECTION_DECISION,
              then=[dict(answer=WAIT_PROBLEM_ANSWER, steps=[dict(id=4, command='export',
                    prerequisites=[dict(condition='Install a bounded reading protocol for this packet.')])]),
                    dict(answer=SMALLER_PACKET_ANSWER, steps=[dict(id=4, command='export'),
                         dict(command='review-assignment', check=True)])]))
for _code in ('stale_revision', 'review_task_dependency_changed', 'review_output_pending', 'review_sources_pending'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2', next=dict(kind='continue'))


STOP_REGISTRY.update({
    'prediction_required': dict(code='prediction_required', **{'class': 'decision'}, owner='D2',
        next=dict(kind='outcome', outcome='scientific_refused')),
    'literature_comparison_stale': dict(code='literature_comparison_stale', **{'class': 'decision'}, owner='D2',
        next=dict(kind='decision', owner_decides='How should the prospective literature comparison be renewed?',
            then=[dict(answer='Record a plan with the current literature comparison',
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
    'gate_override_gate', 'gate_override_instruction', 'gate_override_transition',
    'invalid_round', 'invalid_synthesis', 'objective_mismatch',
    'problem_attempt_evidence_missing', 'problem_attempt_extension_unexplained',
    'problem_attempt_outcome_mismatch', 'problem_attempt_scope_restricted',
    'problem_attempt_statement_mismatch', 'problem_citation_span_mismatch',
    'problem_forming_origin_conflict',
    'problem_post_revision_missing', 'publication_evidence_mismatch',
    'publication_scope_correction_mismatch', 'publication_scope_corrupt',
    'publication_scope_objective_open', 'scope_assumptions_missing', 'scoped_review_corrupt',
)

for _code in ('invalid_rubric_grades', 'invalid_strategy'):
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
         ('id', 'statement_id', 'accounts', 'motivation', 'current_state')),
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


STOP_GROUPS = {
    'stored_state': dict(id='stored_state', **{'class': 'decision'}, owner='D2',
        next=copy.deepcopy(STOP_REGISTRY['artifact_corrupt']['next']),
        conditions=dict(
            store='It checks the store itself: its files, its lock, its schema, its history or its journal.',
            content='It compares stored content with other stored content, a schema, a digest or a version, '
                    'and the state is one that the commands of the harness do not produce in their ordinary order.',
            payload='It reads only the payload of an executed command, and the planner built that payload.')),
}

# Each review lane adds its own (group, codes) tuple here with its evidence file.
for _group, _codes in (
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
    next_step = copy.deepcopy(row['next'] if row is not None else dict(kind='decision',
        owner_decides='How should the retained error be resolved?',
        then=[dict(answer='Inspect the record', steps=[dict(command='record-check')])]))
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
