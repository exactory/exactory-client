"""Finite continuity matrix and the completion contract for executed stop answers."""

STOP_END_MESSAGE = ('A stop answer did not end at an eligible assignment that follows the stopped head, at a recorded action, '
                    'or at a stated boundary without a serving route. Make the journey of the answer reach one of these ends.')
STOP_ANSWER_MESSAGE = ('The executed journeys do not match the answers of the stop, a journey did not run the commands of its '
                       'answer in order, or the stop code has no registry row. Execute exactly one journey for each answer, '
                       'remove a journey for an answer that the stop does not declare, and register the code in stop_classes.py.')
SIZE_MATRIX_MESSAGE = ('The continuity rows do not match the required task kinds and size outcomes, or a row lacks its retained '
                       'call or its executed boundary. Execute one complete row for each required case, and remove or correct '
                       'a row for a case that is not required.')
SIZE_OUTCOMES = (
    ('output_over_cap', None), ('output_over_cap', 'reduced_by_context'),
    ('input_over_limit', None), ('input_over_limit', 'compacted'),
    ('response_over_limit', None), ('timeout', None),
)

STAGE2_REQUIREMENTS = frozenset(('scientific_refused', 'exhaustion', 'refusal_notice',
    'refusal_ladder', 'finality', 'task_size', 'capacity_position', 'legacy_boundary', 'registry'))
STAGE2_EVIDENCE_MESSAGE = ('A Stage 2 continuity requirement or supported chain is missing executed evidence. '
                         'Keep a real-command journey for each supported chain and run every named evidence test.')
STAGE2_EVIDENCE = (
    dict(id='session-refusal', requirement='scientific_refused', chains=('session',), tests=(
        'test_review_refusal_planner.RefusalPlannerTests.test_first_refusal_plans_and_executes_replacement_then_records',
        'test_review_route_renewal.RouteRenewalRegressionTests.test_refused_final_on_a_stale_route_is_recorded_through_a_replacement_on_a_renewed_route',
        'test_review_route_renewal.RouteRenewalRegressionTests.test_stale_route_without_a_renewed_route_ends_before_any_record',
        'test_review_route_renewal.RouteRenewalCapacityTests.test_capacity_probe_is_offered_when_evidence_is_what_the_record_lacks')),
    dict(id='focused-refusal', requirement='scientific_refused', chains=('adjudication',), tests=(
        'test_review_refusal_planner.PlannerChainRowsTests.test_focused_adjudication_executes_the_planned_row',)),
    dict(id='repair-refusal', requirement='scientific_refused', chains=('context_repair', 'parts_v2:context_repair'), tests=(
        'test_review_refusal_planner.PlannerChainRowsTests.test_repaired_bar_final_executes_the_planned_row',
        'test_review_exhaustion_repair.ExhaustionRepairTests.test_parts_final_uses_the_versioned_exhaustion_cause_and_replays_the_old_final',
        'test_review_route_renewal.RouteRenewalRegressionTests.test_refused_turn_outside_the_renewal_on_a_stale_route_ends_before_any_record')),
    dict(id='result-parts-refusal', requirement='scientific_refused', chains=('parts_v2:problem_result',), tests=(
        'test_review_refusal_planner.PlannerChainRowsTests.test_parts_read_combine_check_and_final_execute_the_planned_row',)),
    dict(id='native-parts-refusal', requirement='scientific_refused', chains=('parts_v2:native_math',), tests=(
        'test_review_refusal_planner.PlannerChainRowsTests.test_native_parts_executes_the_planned_row',)),
    dict(id='verification-parts-refusal', requirement='scientific_refused', chains=('parts_v2:study_verification',), tests=(
        'test_review_refusal_planner.PlannerChainRowsTests.test_verification_parts_executes_the_planned_row',)),
    dict(id='other-installed-parts-refusals', requirement='scientific_refused',
        chains=('parts_v1', 'parts_v2:problem_adjudicator', 'parts_v2:study_readiness', 'parts_v2:problem_slate'), tests=(
        'test_continuity_guard.ContinuityGuardTests.test_other_installed_parts_execute_the_refusal_replacement',)),
    dict(id='role-exhaustion', requirement='exhaustion', chains=(), tests=(
        'test_d2_outcomes.OutcomeTests.test_exhausted_bar_continuation_accepts_a_new_blind_reviewer',
        'test_d2_outcomes.OutcomeTests.test_focused_exhaustion_accepts_another_reviewer_and_refuses_resampling',
        'test_d2_outcomes.OutcomeTests.test_slate_and_result_exhaustion_keep_their_reviewer_slots',
        'test_review_exhaustion_repair.ExhaustionRepairTests.test_result_handoff_has_complete_binding_and_records_without_retirement',
        'test_review_exhaustion_repair.ExhaustionRepairTests.test_slate_handoff_admits_the_same_complete_binding')),
    dict(id='notice-bounds-and-isolation', requirement='refusal_notice', chains=(), tests=(
        'test_review_refusal_notice.ContractCheckRegistryTests.test_every_check_attribution_is_registered_and_registry_fields_are_explicit',
        'test_review_refusal_notice.RefusalNoticeTests.test_every_registered_validator_produces_a_notice_without_the_other_reviewers_markers',
        'test_review_refusal_notice.RefusalNoticeTests.test_bounded_notice_limits_serialized_entries_details_and_part_for_large_outputs')),
    dict(id='bounded-ladder-and-correction-history', requirement='refusal_ladder', chains=(), tests=(
        'test_review_refusal_planner.PlannerChainRowsTests.test_a7_real_refused_read_descends_and_admits_its_bounded_notice',
        'test_review_refusal_planner.RefusalPlannerTests.test_a7_whole_then_bounded_and_capacity_floor_end_at_role_step',
        'test_review_correction_history_planner.CorrectionHistoryPlannerTests.test_forty_long_historical_reviews_replace_without_spending_a_correction',
        'test_review_refusal_planner.CorrectionPlannerTests.test_new_reassessment_reruns_once_then_corrects_and_records')),
    dict(id='c12-output-and-store-boundaries', requirement='refusal_ladder', chains=(), tests=(
        'test_review_refusal_planner.PlannerChainRowsTests.test_c12_a_bar_abstract_link_executes_planned_replacement_and_records',
        'test_review_refusal_planner.PlannerChainRowsTests.test_c12_b_bar_mixed_links_executes_replacement_and_reading_handoff',
        'test_review_refusal_planner_citations.CitationPlannerRoleTests.test_c12_c_result_abstract_alone_and_beside_unread_fulltext_reach_repair',
        'test_review_refusal_planner_citations.CitationPlannerRoleTests.test_c12_c_adjudication_abstract_alone_and_beside_unread_fulltext_reach_new_reviewer')),
    dict(id='validator-upgrade-finality', requirement='finality', chains=(), tests=(
        'test_review_refusal_replacement.RefusalReplacementTests.test_second_replacement_lists_revived_attempt_and_keeps_head_finality',
        'test_review_refusal_replacement.RefusalReplacementTests.test_revived_head_without_successor_records_its_original_observation',
        'test_review_exhaustion_repair.ExhaustionRepairTests.test_origin_uses_stored_evidence_after_the_validator_changes',
        'test_review_exhaustion_repair.ExhaustionRepairTests.test_every_earlier_repair_prompt_keeps_its_exact_bytes')),
    dict(id='smaller-tasks-and-finite-floors', requirement='task_size', chains=(), tests=(
        'test_review_task_size.TaskAllowanceTests.test_all_pinned_reading_levels_step_down_without_lowering_effort',
        'test_review_task_size.RecoveryRowsTests.test_k2_every_installed_kind_protocol_pair_has_an_explicit_row',
        'test_review_task_size.RecoveryRowsTests.test_a19_reduced_context_cap_replaces_a_read_and_real_assignment_halves_it',
        'test_review_task_size.RecoveryRowsTests.test_a21_timeout_and_provider_refusal_use_the_lower_read_after_their_retries',
        'test_review_task_size.RecoveryRowsTests.test_input_over_limit_replaces_a_read_on_the_same_route',
        'test_review_task_size.RecoveryRowsTests.test_floor_has_one_identical_retry_and_then_full_decision_evidence',
        'test_continuity_guard.ContinuityGuardTests.test_size_kinds_execute_real_commands_to_preflight_or_all_stop_answers')),
    dict(id='capacity-links-in-refusal-position', requirement='capacity_position', chains=(), tests=(
        'test_review_exhaustion_capacity_position.MixedCapacityPositionTests.test_mixed_capacity_and_refusal_position_repairs_without_rewriting_history',
        'test_review_exhaustion_capacity_position.MixedCapacityPositionTests.test_capacity_replacement_of_a_refusal_keeps_the_second_refusal_replacement',
        'test_review_exhaustion_capacity_position.MixedCapacityPositionTests.test_copied_refusal_context_counts_as_capacity')),
    dict(id='no-unreviewed-registry-rows', requirement='registry', chains=(), tests=(
        'test_continuity_guard.ContinuityGuardTests.test_stage2_registry_has_no_unreviewed_rows',)),
    dict(id='legacy-reading-and-native-boundaries', requirement='legacy_boundary', chains=('reading', 'native_math'), tests=(
        'test_review_refusal_replacement_parts.PartsReplacementTests.test_v6_reading_continues_with_its_stored_prompt_version_and_cannot_carry_refusal',
        'test_review_continuation.ContinuationTests.test_two_refused_native_outputs_need_a_new_controller_attestation',
        'test_d2_outcomes.OutcomeTests.test_native_math_handoff_accepts_a_new_attested_packet',
        'test_review_task_size.RecoveryRowsTests.test_legacy_reading_and_final_decisions_include_profile_attempts_and_migration')),
)


def list_stage2_evidence_tests(rows=STAGE2_EVIDENCE):
    return sorted({identifier for row in rows for identifier in row['tests']})


def validate_stage2_evidence(rows, outcomes, supported_chains):
    """Require executed journeys and coverage of each installed refusal replacement adapter."""
    identities = [row.get('id') for row in rows]
    chains = {chain for row in rows if row.get('requirement') == 'scientific_refused'
              for chain in row.get('chains', ())}
    if (len(set(identities)) != len(rows) or None in identities
            or {row.get('requirement') for row in rows} != STAGE2_REQUIREMENTS
            or chains != set(supported_chains)
            or any(not row.get('tests') for row in rows)
            or any(outcomes.get(identifier) != 'passed' for identifier in list_stage2_evidence_tests(rows))):
        return [STAGE2_EVIDENCE_MESSAGE]
    return []


def list_size_cases(kinds):
    return [(kind, outcome, detail) for kind in kinds for outcome, detail in SIZE_OUTCOMES]


def validate_size_matrix(executed, kinds):
    """Require retained-call receipts and an executed boundary for every row."""
    covered = set()
    for row in executed:
        if not isinstance(row, dict):
            return [SIZE_MATRIX_MESSAGE]
        boundary = row.get('boundary', {})
        call = row.get('call', {})
        if not (row.get('attempt_id') and row.get('request_digest') and row.get('accepted_commands')
                and call.get('id') == row['attempt_id']
                and call.get('request_digest') == row['request_digest']
                and call.get('request') and call.get('recorded_revision')):
            return [SIZE_MATRIX_MESSAGE]
        if boundary.get('kind') == 'assignment':
            complete = (boundary.get('eligible') and boundary.get('id') != row.get('assignment_id')
                and boundary.get('prior_head') == row.get('assignment_id') and boundary.get('preflight')
                and boundary.get('record', {}).get('id') == boundary.get('id')
                and boundary.get('record', {}).get('recorded_revision')
                and boundary.get('subject') == row.get('subject'))
        else:
            receipt = boundary.get('receipt', {})
            complete = (boundary.get('kind') == 'action' and isinstance(receipt, dict)
                and receipt.get('id') and receipt.get('recorded_revision')
                and boundary.get('command') in row['accepted_commands'])
        if not complete:
            return [SIZE_MATRIX_MESSAGE]
        covered.add((row.get('kind'), row.get('outcome'), row.get('detail')))
    return [] if covered == set(list_size_cases(kinds)) else [SIZE_MATRIX_MESSAGE]


def validate_stop_journeys(stop, journeys, registry):
    """Check receipts produced by real-command journeys, never command names alone."""
    answers = stop.get('details', {}).get('decision', {}).get('then', [])
    if stop.get('code') not in registry or {row['answer'] for row in journeys} != {row['answer'] for row in answers}:
        return [STOP_ANSWER_MESSAGE]
    errors = []
    def list_commands(steps):
        commands = []
        for step in steps:
            if step.get('command'):
                commands.append(step['command'])
            commands.extend(list_commands(step.get('then', [])))
        return commands
    for journey in journeys:
        answer = next(row for row in answers if row['answer'] == journey['answer'])
        accepted = iter(journey.get('accepted_commands', []))
        for command in list_commands(answer.get('steps', [])):
            if not any(value == command for value in accepted):
                errors.append(STOP_ANSWER_MESSAGE)
                break
        end = journey.get('end', {})
        if end.get('kind') == 'assignment':
            complete = bool(end.get('eligible') and end.get('id') and
                end.get('prior_head') == stop.get('head_id'))
        elif end.get('kind') == 'action':
            complete = bool(end.get('receipt') and end.get('command') in journey.get('accepted_commands', []))
        else:
            complete = end.get('kind') == 'unserved' and bool(end.get('statement')) and not end.get('serving_routes')
        if not complete:
            errors.append(STOP_END_MESSAGE)
    return errors


def build_kind_packet(kind):
    """Small retained packet projections for the outcome counter, not admission fixtures."""
    from research_harness import openness_reading, review_excerpts, review_parts, review_staged
    if kind == 'reading':
        return dict(reading_turn=dict(protocol=openness_reading.PROTOCOL_V6))
    if kind == 'final':
        return dict(reading_record=dict(protocol=openness_reading.PROTOCOL_V6))
    if kind in ('read', 'combine', 'check'):
        return dict(review_parts=dict(kind=kind, protocol=review_parts.PROTOCOL,
            adapter='problem_result', series_key='0' * 64, unit_characters=15000))
    if kind == 'request':
        return dict(reviewer_excerpts=dict(protocol=review_excerpts.SESSION_PROTOCOL))
    if kind == 'staged':
        return dict(staged_review=dict(protocol=review_staged.PROTOCOL))
    if kind == 'probe':
        return dict(allowed_control='synthetic', evidence_control='synthetic')
    if kind == 'correction':
        return dict(protocol='exactory-reassessment-disposition-correction-v1')
    return {}


def describe_probe_boundary(outcome):
    # A probe is evidence, not an assignment; none of these failures admits a route.
    if outcome not in {row[0] for row in SIZE_OUTCOMES}:
        raise ValueError(SIZE_MATRIX_MESSAGE)
    return 'unverified'
