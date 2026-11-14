"""The stop classes and next steps shared by the store and math controller.

D2 owns this registry. Each package adds its codes here when its unit ships.
Unknown codes retain the decision class used by stored records.
"""

import copy


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
        ('review_format_unsupported', 'format_unsupported')):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='outcome', outcome=_outcome))
for _code in ('review_slots_fixed', 'review_resampling_forbidden', 'review_adjudicator_fixed',
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
        ('review_source_no_progress', 'fulltext'),
        ('native_review_acceptance_required', 'search accept'),
        ('verification_verdict_body_required', 'bind-verdict'),
        ('review_recording_required', 'review'),
        ('review_worker_reconciliation_required', 'review-attempt'),
        ('assessment_incomplete', 'review-assignment')):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
                                next=dict(kind='handoff', command=_command))
for _code in ('review_turn_failures_exhausted', 'review_parts_budget_exhausted',
              'review_quality_measurement_pending', 'review_invocation_unverified',
              'review_output_unavailable', 'review_inventory_mismatch', 'review_source_parent_unverified',
              'review_history_invalid', 'artifact_missing', 'artifact_corrupt', 'review_budget_exhausted'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2',
        next=dict(kind='decision', owner_decides='How should this review boundary be resolved?',
                  then=[dict(answer='Check the retained record', steps=[dict(command='record-check')])]))
STOP_REGISTRY['problem_already_posted'] = dict(code='problem_already_posted', **{'class': 'decision'},
                                             owner='D2', next=dict(kind='done'))
from .openness_reading import PROJECTION_STOP, PROBLEM_PROJECTION_DECISION, WAIT_PROBLEM_ANSWER, SMALLER_PACKET_ANSWER
STOP_REGISTRY[PROJECTION_STOP] = dict(code=PROJECTION_STOP, **{'class': 'decision'}, owner='D1',
    next=dict(kind='decision', owner_decides=PROBLEM_PROJECTION_DECISION,
              then=[dict(answer=WAIT_PROBLEM_ANSWER, steps=[dict(id=4, command='export',
                    prerequisites=[dict(condition='Install a bounded reading protocol for this packet.')])]),
                    dict(answer=SMALLER_PACKET_ANSWER, steps=[dict(id=4, command='export'),
                         dict(command='review-assignment', check=True)])]))
for _code in ('stale_revision', 'review_task_dependency_changed'):
    STOP_REGISTRY[_code] = dict(code=_code, **{'class': 'decision'}, owner='D2', next=dict(kind='continue'))


def validate_registry(rows):
    """Require a next step and an executable proof for every defect classification."""
    for code, row in rows.items():
        if (row['code'] != code or row['class'] not in ('waiting', 'harness_defect', 'decision', 'requested')
                or not row.get('owner') or not row.get('next')):
            raise ValueError('A stop registry row needs its code, class, owner and next step: ' + code)
        if row['class'] == 'harness_defect' and not row.get('proof'):
            raise ValueError('A harness-defect row needs its guard test: ' + code)


validate_registry(STOP_REGISTRY)
WAITING_CODES = frozenset(code for code, row in STOP_REGISTRY.items() if row['class'] == 'waiting')
HARNESS_DEFECT_CODES = frozenset(code for code, row in STOP_REGISTRY.items() if row['class'] == 'harness_defect')
EXPECTED_STOP_CODES = frozenset(code for code, row in STOP_REGISTRY.items() if row['class'] != 'harness_defect')
STOP_REGISTRY['store_busy']['replan'] = True
REPLAN_CODES = frozenset(code for code, row in STOP_REGISTRY.items()
                         if row['next']['kind'] == 'continue' or row.get('replan'))


def classify_stop(code):
    return STOP_REGISTRY.get(code, {}).get('class', 'decision')


def describe_terminal(code):
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
    if 'command' in next_step:
        result['command'] = next_step.pop('command')
    if kind == 'decision':
        result['details']['decision'] = next_step
    else:
        result['details']['next'] = dict(kind=kind, **next_step)
    return result
