"""Compose the recorded review protocols into one prospective next action."""

import copy
from datetime import datetime, timedelta, timezone
import json

from . import bar_sources, openness_reading, review_parts, review_problem_parts
from . import review_protocol as protocol
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import text
from .review_task_execution import has_call_terminated, identify_call


def _find_parent(records, assignment):
    context = assignment.get('context', {})
    history_id = assignment.get('history_assignment_id')
    if records.get('problem_post'):
        if not isinstance(context, dict):
            raise ResearchError('invalid_review_context', 'Review ancestry requires an object context')
        if not isinstance(assignment.get('staged_review', {}), dict):
            raise ResearchError('invalid_review_context', 'Review staging requires an object binding')
        for key in ('context_repair', 'staged_upgrade'):
            binding = context.get(key)
            if binding is not None:
                if not isinstance(binding, dict) or 'assignment_id' not in binding:
                    raise ResearchError('invalid_review_context', 'Review ancestry requires an exact predecessor')
                text(binding['assignment_id'], 'Review predecessor', code='invalid_review_context')
        for identifier in (history_id, context.get('prior_assignment_id'), assignment.get('slot_id')):
            if identifier is not None:
                text(identifier, 'Review ancestry identity', code='invalid_review_context')
    history_parent = records.get('review_assignment', {}).get(history_id, {})
    # A result may inherit its bar's scientific history. That history is a
    # different phase, not another turn of the result chain.
    if history_parent.get('role') != assignment['role']:
        history_id = None
    return (context.get('prior_assignment_id') or history_id
            or (context.get('context_repair') or {}).get('assignment_id')
            or (context.get('staged_upgrade') or {}).get('assignment_id'))


def _list_lineage(records, assignment):
    result = []
    seen = set()
    while assignment is not None:
        if assignment['id'] in seen:
            raise ResearchError('invalid_review_context', 'Review ancestry contains a cycle')
        seen.add(assignment['id'])
        result.insert(0, assignment)
        assignment = records.get('review_assignment', {}).get(_find_parent(records, assignment))
    return result


def list_chain_heads(records, artifacts):
    """List current terminal assignment IDs; staged children belong to their controller."""
    assignments = records.get('review_assignment', {})
    parents = {_find_parent(records, row) for row in assignments.values()}
    result = []
    for row in assignments.values():
        if row.get('staged_review', {}).get('kind') == 'task' or row['id'] in parents:
            continue
        if protocol.find_replacement(records, row) is not None:
            continue
        peers = [other for other in assignments.values()
                 if row.get('slot_id') is not None and other.get('slot_id') == row['slot_id']
                 and other['role'] == row['role'] and other.get('dossier_id') == row.get('dossier_id')
                 and other.get('recorded_revision', 0) > row.get('recorded_revision', 0)]
        if not peers:
            result.append(row['id'])
    return sorted(result)


def _find_head(records, artifacts, identifier):
    assignments = records.get('review_assignment', {})
    assignment = protocol._get(records, 'review_assignment', identifier)
    staged = assignment.get('staged_review', {})
    has_posts = bool(records.get('problem_post'))
    if has_posts and not isinstance(staged, dict):
        raise ResearchError('invalid_review_context', 'Review staging requires an object binding')
    if staged.get('kind') == 'task':
        if has_posts:
            text(staged.get('controller_assignment_id'), 'Review controller', code='invalid_review_context')
        identifier = assignment['staged_review']['controller_assignment_id']
        if has_posts:
            assignment = assignments.get(identifier)
            if not isinstance(assignment, dict):
                raise ResearchError('invalid_review_context', 'The review controller is missing')
        else:
            assignment = assignments[identifier]
    candidates = []
    for head in list_chain_heads(records, artifacts):
        row = assignments[head]
        if (identifier in {entry['id'] for entry in _list_lineage(records, row)}
                or (assignment.get('slot_id') is not None
                    and row.get('slot_id') == assignment['slot_id']
                    and row['role'] == assignment['role']
                    and row.get('dossier_id') == assignment.get('dossier_id'))):
            candidates.append(row)
    if len(candidates) > 1:
        raise ResearchError('review_source_continuation_fork', 'The review has more than one live continuation',
                            dict(assignment_ids=sorted(row['id'] for row in candidates)))
    return candidates[0] if candidates else assignment


def _describe_chain(assignment):
    if assignment.get('staged_review'):
        return 'staged'
    if review_parts.KEY in assignment:
        return 'parts_v2:' + assignment[review_parts.KEY]['adapter']
    if 'reading_chain' in assignment:
        return 'reading'
    if 'evidence_parts' in assignment.get('prompt', {}).get('parameters', {}):
        return 'parts_v1'
    if assignment.get('reassessment_required') or 'context_repair' in assignment.get('context', {}):
        return 'context_repair'
    return {'adjudicator': 'adjudication'}.get(assignment['role'],
        'session' if assignment['role'] in bar_sources.ROLES else assignment['role'])


def _find_unused_id(records, proposed):
    occupied = {key for rows in records.values() for key in rows}
    identifier = proposed
    suffix = 2
    while identifier in occupied:
        identifier = proposed + '-' + str(suffix)
        suffix += 1
    return identifier


def _build_assignment_payload(records, assignment, *, replace=False):
    lineage = _list_lineage(records, assignment)
    payload = {key: copy.deepcopy(assignment.get(key)) for key in
               ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id')}
    # Repair prerequisites identify the first repaired turn, not each later turn.
    context = copy.deepcopy(assignment.get('context', {}))
    if not replace:
        context = {key: value for key, value in context.items() if key in
                   ('native_packet', 'verification_task_id', 'review_ids', 'objection_id',
                    'correction', 'source_delivery')}
        context['prior_assignment_id'] = assignment['id']
    payload.update(id=_find_unused_id(records, lineage[0]['id'] + '-t' + str(len(lineage) + 1)),
                   context=context)
    return payload


def _build_run_payload(records, assignment):
    attempts = [row for row in records.get('review_attempt', {}).values()
                if row['assignment_id'] == assignment['id']]
    invocations = [row for row in records.get('review_invocation', {}).values()
                   if row['assignment_id'] == assignment['id']]
    count = len({row['id'] for row in attempts + invocations}) + 1
    return dict(id=_find_unused_id(records, assignment['id'] + '-run-' + str(count)),
                assignment_id=assignment['id'])


def _list_recorded_finals(records, identifiers):
    result = []
    for kind in ('value_review', 'review_adjudication', 'verdict_assessment', 'manuscript_review',
                 'problem_openness'):
        for key, row in records.get(kind, {}).items():
            if (row.get('assignment_id') or row.get('assessment', {}).get('assignment_id')) in identifiers:
                result.append([kind, key])
    return sorted(result)


def _describe_progress(records, artifacts, assignment):
    lineage = _list_lineage(records, assignment)
    identifiers = {row['id'] for row in lineage}
    epochs = {row.get('staged_review', {}).get('epoch_id') for row in lineage} - {None}
    turns = [row['id'] for row in lineage]
    # The exact actual-request digest commits to delivered units and segments,
    # including transport projection. A planned packet is not delivery credit.
    deliveries = sorted([dict(assignment_id=row['assignment_id'], request_digest=row.get('request_digest'))
                         for row in records.get('review_attempt', {}).values()
                         if row['assignment_id'] in identifiers and row['status'] == 'completed'],
                        key=lambda row: (row['assignment_id'], row['request_digest'] or ''))
    parent = assignment.get('context', {}).get('prior_assignment_id')
    position = {assignment['id']}
    for row in records.get('review_assignment', {}).values():
        if any(row.get(key) != assignment.get(key) for key in ('role', 'dossier_id')):
            continue
        if parent is not None and row.get('context', {}).get('prior_assignment_id') == parent:
            position.add(row['id'])
        elif (parent is None and row.get('context', {}).get('prior_assignment_id') is None
              and assignment.get('slot_id') is not None and row.get('slot_id') == assignment['slot_id']
              and _find_parent(records, row) == _find_parent(records, assignment)):
            position.add(row['id'])
    task_ids = {key for key, row in records.get('review_task', {}).items() if row.get('epoch_id') in epochs}
    tasks = sorted([key for key, row in records.get('review_task_execution', {}).items()
                    if key in task_ids and row.get('state') == 'accepted'])
    task_attempts = sorted([[key, row.get('attempts', 0)] for key, row in
                           records.get('review_task_execution', {}).items() if key in task_ids])
    attempts = sorted([row['id'] for row in records.get('review_attempt', {}).values()
                       if row['assignment_id'] in position])
    return dict(turns=turns, deliveries=deliveries, completed_tasks=tasks,
                attempts=attempts, task_attempts=task_attempts,
                recorded_final=_list_recorded_finals(records, identifiers))


def _describe_wait():
    return (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()


def plan_route_handoff(assignment):
    """Name the agent action required to record a current runtime route."""
    return dict(state='handoff', command='review-route', code='review_route_runtime_stale',
                details=dict(assignment_id=assignment['id'], route_id=assignment['route_id']))


def _plan_pending_call(records, assignment):
    for invocation in records.get('review_invocation', {}).values():
        if invocation['assignment_id'] != assignment['id']:
            continue
        identifier = identify_call('review.invoke', invocation['id'])
        captured = records.get('review_call_observation', {}).get(identifier)
        if captured is not None and invocation['id'] not in records.get('review_attempt', {}):
            return dict(state='resume', command='review-run', request_id=captured['request_id'],
                        payload=dict(id=invocation['id'], assignment_id=assignment['id']))
        if not has_call_terminated(records, 'review.invoke', invocation['id']):
            payload = dict(action='reconcile', operation='review.invoke', invocation_id=invocation['id'])
            if identifier not in records.get('review_pool_membership', {}):
                from .review_task_execution import build_historical_attempt_step
                return dict(state='handoff', code='review_worker_reconciliation_required',
                            details=dict(invocation_id=invocation['id'],
                                         next=build_historical_attempt_step(invocation)), command='review-attempt')
            return dict(state='wait', command='review-run', payload=payload,
                        not_before=_describe_wait(), code='review_invocation_pending', details={})
    return None


def _plan_capacity(records, assignment, action):
    from .review_task_execution import _active_legacy, _policy
    policy = _policy(records)
    route = records.get('review_route', {}).get(assignment['route_id'], {})
    if route.get('adapter') not in policy['covered_adapters']:
        return action
    memberships = records.get('review_pool_membership', {})
    active = [row for row in records.get('review_reservation', {}).values()
              if row['id'] in memberships and not row['released']]
    active.extend(_active_legacy(records, policy))
    route_active = [row for row in active if row['route_id'] == assignment['route_id']]
    if (len(active) < policy['store_limit'] and len(route_active) < policy['route_limits'].get(
            assignment['route_id'], policy['store_limit'])):
        return action
    owned = active[0]
    if owned['id'] in records.get('review_reservation', {}):
        epoch = records['review_epoch'][owned['epoch_id']]
        payload = dict(assignment_id=epoch['controller_assignment_id'], epoch_id=epoch['id'],
                       task_id=owned['task_id'], action='reconcile')
    else:
        operation = next(operation for operation in
                         ('review.invoke', 'review.route_probe', 'review.output_correction')
                         if identify_call(operation, owned['id']) in memberships)
        payload = dict(action='reconcile', operation=operation, invocation_id=owned['id'])
    return dict(state='wait', not_before=_describe_wait(), command='review-run', payload=payload,
                code='review_capacity_wait', details=dict(reservations=[row['id'] for row in active]))


def _plan_recording(records, artifacts, assignment, state):
    if _list_recorded_finals(records, {assignment['id']}):
        return dict(state='done')
    output = copy.deepcopy(state.get('output'))
    effective = state.get('effective') or {}
    if effective.get('kind') in ('ordinary', 'paged'):
        output = copy.deepcopy(effective['effective_output'])
    role = assignment['role']
    if role == 'native_math':
        from .native_review import load_packet
        packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
        if openness_reading.is_openness_packet(packet):
            from .problem_openness import prepare_openness
            try:
                prepare_openness(records, artifacts, dict(assignment_id=assignment['id']),
                                 assignment['recorded_revision'] + 1)
            except ResearchError as error:
                return dict(state='handoff', code='openness_review_not_approved', command='export',
                            details=dict(assignment_id=assignment['id'], decision=output.get('decision'),
                                         reason=output.get('reason'), kind='problem-openness',
                                         refusal=dict(code=error.code, message=error.message)))
            return dict(state='record', command='problem-openness',
                        payload=dict(assignment_id=assignment['id']))
    command = {'bar': 'value-review', 'slate': 'value-review', 'result': 'value-review',
               'adjudicator': 'review-adjudication'}.get(role)
    if command is None:
        return dict(state='handoff', code={'verification': 'verification_verdict_body_required',
                    'native_math': 'native_review_acceptance_required'}.get(role, 'review_recording_required'),
                    command={'verification': 'bind-verdict', 'native_math': 'search accept',
                             'manuscript': 'manuscript-review', 'standalone': 'export'}.get(role, 'review'),
                    details=dict(assignment_id=assignment['id'], role=role,
                                 **(dict(kind='outputs') if role == 'standalone' else {})))
    payload = dict(output, id=_find_unused_id(records, 'review:' + assignment['id']),
                   assignment_id=assignment['id'])
    if command == 'value-review':
        payload['dossier_id'] = assignment['dossier_id']
    return dict(state='record', command=command, payload=payload)


def _plan_staged_action(records, epoch, task, action):
    command, payload = action.get('command'), action.get('payload')
    if command is None or payload is None:
        from .review_staged_protocol import describe_planning_command
        action = describe_planning_command(epoch['controller_assignment_id'])
        command, payload = action['command'], action['payload']
    result = dict(state='plan' if command == 'review-assignment' else 'run',
                  command=command, payload=copy.deepcopy(payload))
    if command != 'review-run':
        return result
    if payload.get('action') in ('accept', 'reconcile'):
        result['state'] = 'resume'
        execution = records.get('review_task_execution', {}).get(task['task_id'], {})
        reservation_id = execution.get('reservation_id')
        reservation = records.get('review_reservation', {}).get(reservation_id, {})
        owner = next(((request, row) for request, row in
                      records.get('review_task_dispatch', {}).items()
                      if row.get('task_id') == task['task_id']
                      and reservation_id == 'review-reservation:' + digest(
                          [reservation.get('plan_id'), request + ':reserve'])), None)
        if owner is not None and payload.get('action') == 'accept':
            request, owner = owner
            result.update(request_id=request)
            result['payload']['id'] = owner['id']
    elif 'id' not in payload:
        child = next(row for row in records['review_assignment'].values()
                     if row.get('staged_review', {}).get('task_id') == task['task_id'])
        result['payload']['id'] = _build_run_payload(records, child)['id']
    if task['state'] in ('waiting', 'reconciling'):
        result.update(state='wait', not_before=_describe_wait(),
                      code=task['code'], details=dict(task_id=task['task_id']))
    return result


def _plan_staged(records, artifacts, assignment, state):
    from .problem_delivery import describe_review_tasks
    epoch = protocol._get(records, 'review_epoch', assignment['staged_review']['epoch_id'])
    effective = state.get('effective') or {}
    if effective.get('incomplete_outcome') is not None:
        return dict(state='handoff', code='assessment_incomplete', command='review-assignment',
                    details=effective['incomplete_outcome'])
    if state.get('output') is not None:
        return _plan_recording(records, artifacts, assignment, state)
    for task in describe_review_tasks(records, epoch, state):
        if task['state'] != 'accepted' and task.get('next'):
            return _plan_staged_action(records, epoch, task, task['next'][0])
    from .review_staged_protocol import describe_planning_command
    action = describe_planning_command(assignment['id'])
    return dict(state='plan', command=action['command'], payload=action['payload'])


def _plan_head(records, artifacts, assignment):
    if assignment.get('staged_review'):
        state = protocol.assignment_state(records, artifacts, assignment['id'])
        blocking = [item for item in state['obligations'] if item['code'] != 'review_task_pending']
        if blocking:
            return dict(state='handoff', command='review-assignment', code=blocking[0]['code'], details=blocking[0])
        return _plan_staged(records, artifacts, assignment, state)
    pending = _plan_pending_call(records, assignment)
    if pending is not None:
        return pending
    attempts = [row for row in records.get('review_attempt', {}).values()
                if row['assignment_id'] == assignment['id']]
    initial = (assignment['role'] in ('adjudicator', 'result')
               and assignment.get('subject', {}).get('kind') == 'problem')
    if (not attempts and initial
            and protocol._initial_size_failure(records, artifacts, assignment, assignment)):
        return dict(state='replace', command='review-assignment',
                    payload=_build_assignment_payload(records, assignment, replace=True),
                    code='review_input_too_large', details=dict(assignment_id=assignment['id']))
    failed = protocol.has_ended_without_output(records, assignment['id'])
    unverified = bool(attempts) and all(row['status'] != 'completed'
        or not protocol._context_observed(artifacts, row) for row in attempts)
    state = None
    if not failed and not unverified:
        state = protocol.assignment_state(records, artifacts, assignment['id'])
        binding = records.get('review_attempt', {}).get(state.get('attempt_id'))
        observed = (state.get('output_correction_id') is not None or binding is None
                    or protocol._context_observed(artifacts, binding))
        usable = state.get('output') is not None and observed
        refused = state.get('refused_attempts', [])
        if not usable and len(refused) >= 2:
            from . import review_output_correction
            command = 'review-output-correction'
            details = dict(assignment_id=assignment['id'], refused_attempts=refused)
            try:
                review_output_correction.prepare_packet(records, artifacts, assignment['id'])
            except ResearchError:
                if (assignment['role'] in ('bar', 'slate', 'result', 'adjudicator')
                        and (assignment['role'] != 'bar'
                             or assignment.get('context', {}).get('prior_assignment_id'))):
                    return dict(state='stop', code='review_slot_exhausted', details=details)
                command = 'review-assignment'
                if assignment['role'] == 'native_math':
                    from .native_review import load_packet
                    native = load_packet(artifacts, assignment['context']['native_packet'],
                                         assignment['reviewer_id'])
                    if openness_reading.is_openness_packet(native):
                        command = 'export'
                        details['kind'] = 'problem-openness'
                    else:
                        command = 'search review-packet'
            return dict(state='handoff', command=command, code='review_slot_exhausted', details=details)
        if attempts and not usable:
            latest = max(attempts, key=lambda row: row.get('recorded_revision', 0))
            failed = latest['status'] != 'completed' or not protocol._context_observed(artifacts, latest)
        if not observed:
            # The selector retains unobserved output for diagnostics. Invocation
            # eligibility ignores that attempt, including its binding diagnostic.
            state = dict(state, output=None, obligations=[item for item in state['obligations']
                if item['code'] not in ('review_invocation_unverified', 'review_output_unavailable')])
    if failed or unverified:
        from .review_failure_class import plan_after_failure
        result = copy.deepcopy(plan_after_failure(records, artifacts, assignment['id']))
        if result['state'] == 'stop' and result.get('code') == 'review_route_runtime_stale':
            return plan_route_handoff(assignment)
        if result['state'] == 'replace':
            if assignment['role'] == 'native_math' and not assignment.get('context', {}).get('prior_assignment_id'):
                from .native_review import load_packet
                packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
                common = openness_reading.is_openness_packet(packet)
                return dict(state='handoff', command='export' if common else 'search review-packet',
                            code='native_review_attestation_required',
                            details=dict(assignment_id=assignment['id'],
                                         **(dict(kind='problem-openness') if common else {})))
            if (assignment['role'] in bar_sources.ROLES
                    and not assignment.get('context', {}).get('prior_assignment_id')
                    and not assignment.get('context', {}).get('context_repair')
                    and not (initial and protocol._initial_size_failure(
                        records, artifacts, assignment, assignment))):
                return dict(state='handoff', command='review-assignment',
                            code='review_resampling_forbidden',
                            details=dict(assignment_id=assignment['id'],
                                         failure_code=result.get('code'),
                                         failure_details=result.get('details', {})))
            result.update(command='review-assignment',
                          payload=_build_assignment_payload(records, assignment, replace=True))
        elif result['state'] in ('run', 'wait'):
            result.update(command='review-run', payload=_build_run_payload(records, assignment))
            if result['state'] == 'run':
                result = _plan_capacity(records, assignment, result)
        return result
    blocking = [item for item in state.get('obligations', [])
                if item['code'] not in ('review_output_pending', 'review_output_refused')]
    if blocking:
        item = blocking[0]
        repair = item['code'].startswith('review_context_') or item['code'] == 'review_route_retired'
        return dict(state='handoff' if repair else 'stop', code=item['code'], details=item,
                    **(dict(command='review-assignment') if repair else {}))
    if state.get('output') is None:
        return _plan_capacity(records, assignment, dict(state='run', command='review-run',
                              payload=_build_run_payload(records, assignment)))
    packet = protocol._read_json(artifacts, assignment['packet'])
    is_parts = review_parts.KEY in assignment
    continuing = (is_parts and (assignment[review_parts.KEY]['kind'] != 'final'
                                or state['output'].get('kind') == 'requests')
                  or review_problem_parts.find_stage(packet) == 'part'
                  or openness_reading.find_stage(packet) is not None and state['output'].get('stage') == 'reading'
                  or bool(state.get('pending_source_requests'))
                  or (assignment.get('subject', {}).get('kind') == 'problem'
                      and assignment['role'] in bar_sources.ROLES
                      and _list_recorded_finals(records, {assignment['id']})
                      and protocol._is_problem_refresh(records, artifacts, assignment, packet)))
    if continuing:
        return dict(state='continue', command='review-assignment',
                    payload=_build_assignment_payload(records, assignment))
    return _plan_recording(records, artifacts, assignment, state)


def plan_next(records, artifacts, head_id):
    """Describe one exact action; the named operation retains every mutation validator."""
    artifacts = Evaluation.of(records, artifacts)
    result = dict(state='stop', chain=None, head_id=head_id, progress=None)
    try:
        assignment = _find_head(records, artifacts, head_id)
        result.update(chain=_describe_chain(assignment), head_id=assignment['id'],
                      progress=_describe_progress(records, artifacts, assignment))
        from .problem_delivery import find_review_post
        if find_review_post(records, assignment, artifacts) is not None:
            result['state'] = 'done'
        else:
            result.update(_plan_head(records, artifacts, assignment))
    except (ResearchError, json.JSONDecodeError) as error:
        if isinstance(error, ResearchError):
            result.update(state='stop', code=error.code, details=error.details or {})
        else:
            result.update(state='stop', code='review_input_unreadable', details=dict(kind=type(error).__name__))
    return copy.deepcopy(result)


def describe_next_actions(records, artifacts, assignment_id, actions=()):
    """Add executable actions while preserving existing navigation fields."""
    plan = plan_next(records, artifacts, assignment_id)
    from .problem_delivery import find_review_post
    assignment = records.get('review_assignment', {}).get(assignment_id, {})
    resolved_assignment = records.get('review_assignment', {}).get(plan['head_id'], {})
    is_posted = find_review_post(records, resolved_assignment, artifacts) is not None
    result = []
    for original in actions or [dict(kind='review', workflow=plan.get('command'),
                                     scope=dict(assignment_id=assignment_id))]:
        action = copy.deepcopy(original)
        selected = plan
        task_id = original.get('payload', {}).get('task_id') or original.get('scope', {}).get('task_id')
        if (assignment.get('staged_review') and task_id is not None
                and plan['state'] not in ('done', 'stop', 'handoff')):
            from .problem_delivery import describe_review_tasks
            epoch = records['review_epoch'][assignment['staged_review']['epoch_id']]
            state = protocol.assignment_state(records, artifacts, assignment_id)
            task = next((task for task in describe_review_tasks(records, epoch, state)
                         if task['task_id'] == task_id), None)
            if task is not None and task.get('next'):
                selected = _plan_staged_action(records, epoch, task, original)
        for key in ('command', 'payload', 'request_id', 'not_before', 'code', 'details'):
            action.pop(key, None)
            if key in selected:
                action[key] = copy.deepcopy(selected[key])
        action['state'] = selected['state']
        if is_posted:
            action.pop('workflow', None)
            action.pop('scope', None)
        result.append(action)
    return result


def annotate_review_obligations(records, artifacts, report):
    """Attach the common action wherever a gate already names a review assignment."""
    if 'review' in report and 'obligations' in report:
        rows = list(report['obligations'])
        represented = {item.get('assignment_id') or item.get('controller_assignment_id') for item in rows}
        for identifier in list_chain_heads(records, artifacts):
            if identifier in represented:
                continue
            assignment = records['review_assignment'][identifier]
            if assignment['role'] == 'native_math':
                try:
                    from .native_review import load_packet
                    packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
                except (ResearchError, json.JSONDecodeError):
                    continue
                if not openness_reading.is_openness_packet(packet):
                    continue
            state = protocol.assignment_state(records, artifacts, identifier)
            rows.extend(dict(item, assignment_id=identifier) for item in state.get('obligations', []))
            binding = records.get('review_attempt', {}).get(state.get('attempt_id'))
            if (not state.get('obligations') and binding is not None
                    and not state.get('output_correction_id')
                    and not protocol._context_observed(artifacts, binding)):
                from .graph import obligation
                rows.append(obligation('review_output_pending',
                    'A complete observed reviewer response is required.', assignment_id=identifier))
            if state.get('pending_source_requests'):
                from .graph import obligation
                rows.append(obligation('review_sources_pending',
                    'Deliver the sources requested by the observed reviewer.',
                    assignment_id=identifier, requests=state['pending_source_requests']))
        if len(rows) != len(report['obligations']):
            from .report_views import order_obligations
            report = dict(report, obligations=order_obligations(rows))
            if 'counts' in report:
                report['counts'] = dict(report['counts'], obligations=len(rows))
            if report.get('next') is None:
                report['next'] = report['obligations'][0]
    result = dict(report)
    staged_outcomes = None
    for key in ('obligations', 'manuscript_obligations', 'objective_obligations'):
        if key not in report:
            continue
        rows = []
        for original in report[key]:
            item = dict(original)
            identifier = item.get('assignment_id') or item.get('controller_assignment_id')
            if identifier in records.get('review_assignment', {}):
                item['next'] = describe_next_actions(records, artifacts, identifier, item.get('next', []))
            elif records.get('review_epoch'):
                # Legacy staged diagnostics have no assignment identity in the
                # gate digest. Resolve their controllers only in the reader.
                if staged_outcomes is None:
                    from .problem_delivery import collect_review_outcomes
                    staged_outcomes = [row for row in collect_review_outcomes(records, artifacts)
                                       if row['epoch'] is not None]
                identifiers = [row['assignment']['id'] for row in staged_outcomes
                               if original in row['state'].get('obligations', [])]
                if identifiers:
                    item['next'] = [action for identifier in identifiers for action in
                        describe_next_actions(records, artifacts, identifier, item.get('next', []))]
            rows.append(item)
        result[key] = rows
    if report.get('preparation'):
        result['preparation'] = annotate_review_obligations(records, artifacts, report['preparation'])
    if report.get('next') is not None:
        upcoming = report['next']
        if 'code' in upcoming:
            annotated = next((item for original, item in zip(
                report.get('obligations', []), result.get('obligations', []))
                if original == upcoming), None)
            if annotated is not None:
                result['next'] = dict(annotated)
            else:
                identifier = upcoming.get('assignment_id') or upcoming.get('controller_assignment_id')
                if identifier in records.get('review_assignment', {}):
                    result['next'] = dict(upcoming, next=describe_next_actions(
                        records, artifacts, identifier, upcoming.get('next', [])))
    return result
