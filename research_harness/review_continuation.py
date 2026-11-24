"""Compose the recorded review protocols into one prospective next action."""

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from . import bar_sources, openness_reading, review_parts, review_problem_parts
from . import review_protocol as protocol
from .errors import ResearchError
from .evaluation import Evaluation
from .evidence import digest
from .operations import text
from .review_task_execution import has_call_terminated, identify_call, rebuild_call_payload

# The message of the handoff of a refresh (rules F3 and F5 of the session refresh) while the store lacks the route
# record of the new chain or its probe.
REFRESH_ROUTE_MSG = (
    'The recorded review of this head needs a refresh. The refresh is a new chain in the slot of the head, and that '
    'chain needs a route record that pins the installed runtime. The route record of the head pins another one. '
    'details.renewal.lacks names what the store lacks. The value record means a route record with the same adapter, '
    'model, endpoint and configuration that pins the installed runtime. The value probe means a probe that verifies '
    'the route record that details.renewal.route_id names. Take details.step, and then the steps of its then in '
    'their order. The last of them is review-series on this head, which then names the assignment of the refresh '
    'with the id of that route record. Nothing was recorded.')
CAPACITY_SCHEDULER_MSG = (
    'The store limit of the capacity policy holds this call back while its own route runs no call. '
    'details.scheduler is a policy that admits one call at a time on each verified route among the route of this '
    'call and the routes of the active and the waiting calls. To record it, give it as the member scheduler of the '
    'next review-assignment of this record. Calls on different routes then run at the same time.')
# The reason of the after-turn stop that the plan of a session v4 head names when the capacity plan pinned to its
# chain cannot serve the next turn or the replacement of a failed turn.
SESSION_CAPACITY_STOP_MSG = (
    'The capacity plan pinned to this chain has no room for its next turn ({limit}). The pinned plan stays the same '
    'on every route, so another route does not give this chain more room. This stop ends the chain after its '
    'current turn.')


def describe_context_failure(error, assignment_id=None):
    """Keep a retained-context refusal visible in a read-only inspection."""
    failure = (error.as_dict() if isinstance(error, ResearchError) else
               dict(code='review_input_unreadable',
                    message='A retained input of this review does not parse as JSON. Check the record with '
                            'record-check.',
                    details=dict(kind=type(error).__name__)))
    if assignment_id is not None:
        failure = dict(assignment_id=assignment_id, **failure)
    return failure


@contextmanager
def _collect_context_failure(failures, assignment_id):
    try:
        yield
    except (ResearchError, json.JSONDecodeError) as error:
        if failures is None:
            raise
        failure = describe_context_failure(error, assignment_id)
        if failure not in failures:
            failures.append(failure)


def _find_parent(records, assignment):
    context = assignment.get('context', {})
    history_id = assignment.get('history_assignment_id')
    if not isinstance(context, dict):
        raise ResearchError('invalid_review_context', 'Review ancestry requires an object context')
    if not isinstance(assignment.get('staged_review', {}), dict):
        raise ResearchError('invalid_review_context', 'Review staging requires an object binding')
    has_posts = bool(records.get('problem_post'))
    for key in ('context_repair', 'staged_upgrade', 'output_refusal', 'unread'):
        binding = context.get(key)
        if binding is not None or (key == 'output_refusal' and key in context):
            if not isinstance(binding, dict) or ((has_posts or key == 'output_refusal')
                                                and 'assignment_id' not in binding):
                raise ResearchError('invalid_review_context', 'Review ancestry requires an exact predecessor')
            identifier = binding.get('assignment_id')
            if has_posts or key == 'output_refusal' or (identifier is not None and not isinstance(identifier, str)):
                text(identifier, 'Review predecessor', code='invalid_review_context')
    for identifier in (history_id, context.get('prior_assignment_id'), assignment.get('slot_id')):
        if identifier is not None and (has_posts or not isinstance(identifier, str)):
            text(identifier, 'Review ancestry identity', code='invalid_review_context')
    history_parent = records.get('review_assignment', {}).get(history_id, {})
    # A result may inherit its bar's scientific history. That history is a
    # different phase, not another turn of the result chain.
    if history_parent.get('role') != assignment['role']:
        history_id = None
    return (assignment.get('capacity_replacement_of') or context.get('prior_assignment_id') or history_id
            or (context.get('unread') or {}).get('assignment_id')
            or (context.get('context_repair') or {}).get('assignment_id')
            or (context.get('staged_upgrade') or {}).get('assignment_id'))


def validate_context_bindings(records, artifacts, assignment):
    """Read retained container and controller bindings at an inspection's edge."""
    _find_parent(records, assignment)
    staged = assignment.get('staged_review', {})
    controller = staged.get('controller_assignment_id')
    if (staged.get('kind') == 'task' and
            (not isinstance(controller, str) or controller not in records.get('review_assignment', {}))):
        _find_head(records, artifacts, assignment['id'])


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


def list_chain_heads(records, artifacts, *, context_failures=None):
    """List current terminal assignment IDs; staged children belong to their controller."""
    assignments, parents = {}, set()
    for identifier, row in records.get('review_assignment', {}).items():
        with _collect_context_failure(context_failures, identifier):
            parents.add(_find_parent(records, row))
            assignments[identifier] = row
    checked = dict(review_assignment=assignments)
    result = []
    for row in assignments.values():
        with _collect_context_failure(context_failures, row['id']):
            if row.get('staged_review', {}).get('kind') == 'task' or row['id'] in parents:
                continue
            if protocol.find_replacement(checked, row) is not None:
                continue
            peers = [other for other in assignments.values()
                     if row.get('slot_id') is not None and other.get('slot_id') == row['slot_id']
                     and other['role'] == row['role'] and other.get('dossier_id') == row.get('dossier_id')
                     and other.get('staged_review', {}).get('kind') != 'task'
                     and other.get('recorded_revision', 0) > row.get('recorded_revision', 0)]
            if not peers:
                result.append(row['id'])
    return sorted(result)


def _find_head(records, artifacts, identifier, *, context_failures=None):
    assignments = records.get('review_assignment', {})
    assignment = protocol._get(records, 'review_assignment', identifier)
    staged = assignment.get('staged_review', {})
    has_posts = bool(records.get('problem_post'))
    if not isinstance(staged, dict):
        raise ResearchError('invalid_review_context', 'Review staging requires an object binding')
    if staged.get('kind') == 'task':
        text(staged.get('controller_assignment_id'), 'Review controller', code='invalid_review_context')
        identifier = assignment['staged_review']['controller_assignment_id']
        assignment = assignments.get(identifier)
        if not isinstance(assignment, dict):
            raise ResearchError('invalid_review_context', 'The review controller is missing')
    if assignment.get('capacity_plan') and assignment.get('reading_chain'):
        replacement = protocol.find_replacement(records, assignment)
        while replacement is not None:
            identifier, assignment = replacement['id'], replacement
            replacement = protocol.find_replacement(records, assignment)
    candidates = []
    for head in list_chain_heads(records, artifacts, context_failures=context_failures):
        row = assignments[head]
        with _collect_context_failure(context_failures, head):
            if (identifier in {entry['id'] for entry in _list_lineage(records, row)}
                    or (row.get('slot_id') is not None
                        and row.get('slot_id') == assignment.get('slot_id', assignment['id'])
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


def _find_unused_id(records, proposed, *, maximum=None):
    occupied = {key for rows in records.values() for key in rows}
    identifier = proposed if maximum is None else proposed[:maximum]
    suffix = 2
    while identifier in occupied:
        ending = '-' + str(suffix)
        identifier = proposed + ending if maximum is None else proposed[:maximum - len(ending)] + ending
        suffix += 1
    return identifier


def _build_assignment_payload(records, assignment, *, replace=False):
    lineage = _list_lineage(records, assignment)
    payload = {key: copy.deepcopy(assignment.get(key)) for key in
               ('route_id', 'dossier_id', 'role', 'reviewer_id', 'author_id')}
    # Repair prerequisites identify the first repaired turn, not each later turn.
    context = copy.deepcopy(assignment.get('context', {}))
    if replace and assignment.get('staged_review', {}).get('kind') == 'controller':
        context = dict(staged_upgrade=dict(assignment_id=assignment['id']))
    elif not replace:
        context = {key: value for key, value in context.items() if key in
                   ('native_packet', 'verification_task_id', 'review_ids', 'objection_id',
                    'correction', 'source_delivery')}
        context['prior_assignment_id'] = assignment['id']
    maximum = (openness_reading.RESERVED_IDENTIFIER_CHARACTERS if assignment['prompt']['template_id'].startswith(
        tuple(protocol + '/' for protocol in (openness_reading.PROTOCOL_V6,) + openness_reading.BOUNDED_CHAIN_PROTOCOLS)) else None)
    if assignment.get('session_chain'):
        from .review_session_v4 import ASSIGNMENT_ID_CHARACTERS
        maximum = ASSIGNMENT_ID_CHARACTERS
    payload.update(id=_find_unused_id(records, lineage[0]['id'] + '-t' + str(len(lineage) + 1), maximum=maximum),
                   context=context)
    return payload


def _build_run_payload(records, assignment):
    attempts = [row for row in records.get('review_attempt', {}).values()
                if row['assignment_id'] == assignment['id']]
    invocations = [row for row in records.get('review_invocation', {}).values()
                   if row['assignment_id'] == assignment['id']]
    count = len({row['id'] for row in attempts + invocations}) + 1
    maximum = (openness_reading.RESERVED_IDENTIFIER_CHARACTERS if assignment['prompt']['template_id'].startswith(
        tuple(protocol + '/' for protocol in (openness_reading.PROTOCOL_V6,) + openness_reading.BOUNDED_CHAIN_PROTOCOLS)) else None)
    return dict(id=_find_unused_id(records, assignment['id'] + '-run-' + str(count), maximum=maximum),
                assignment_id=assignment['id'])


def _list_recorded_finals(records, identifiers):
    result = []
    for kind in ('value_review', 'review_adjudication', 'verdict_assessment', 'manuscript_review',
                 'problem_openness'):
        for key, row in records.get(kind, {}).items():
            if (row.get('assignment_id') or row.get('assessment', {}).get('assignment_id')) in identifiers:
                result.append([kind, key])
    return sorted(result)


def _describe_progress(records, artifacts, assignment, *, context_failures=None):
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
    failed_contexts = {row.get('assignment_id') for row in context_failures or []}
    for row in records.get('review_assignment', {}).values():
        if row['id'] in failed_contexts:
            continue
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


def plan_route_handoff(assignment, renewal=None):
    """The handoff of a head whose route record is stale, with the renewal that a refused replacement states."""
    from .review_route_plan import plan_stale_route
    return plan_stale_route(assignment, renewal)


def _plan_pending_call(records, assignment):
    for invocation in records.get('review_output_correction_invocation', {}).values():
        if invocation['assignment_id'] == assignment['id'] and invocation['id'] not in records.get('review_output_correction', {}):
            identifier = identify_call('review.output_correction', invocation['id'])
            captured = records.get('review_call_observation', {}).get(identifier)
            if captured is not None:
                return dict(state='resume', command='review-output-correction', request_id=captured['request_id'],
                    payload=rebuild_call_payload('review.output_correction', invocation))
            return dict(state='wait', command='review-run',
                payload=dict(action='reconcile', operation='review.output_correction', invocation_id=invocation['id']),
                not_before=_describe_wait(), code='review_invocation_pending', details={})
    for invocation in records.get('review_invocation', {}).values():
        if invocation['assignment_id'] != assignment['id']:
            continue
        identifier = identify_call('review.invoke', invocation['id'])
        captured = records.get('review_call_observation', {}).get(identifier)
        if captured is not None and invocation['id'] not in records.get('review_attempt', {}):
            return dict(state='resume', command='review-run', request_id=captured['request_id'],
                        payload=rebuild_call_payload('review.invoke', invocation))
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


def _plan_capacity(records, assignment, action, artifacts=None, *, refusal=None):
    from .review_call_admission import (check_review_call_admission, describe_capacity_wait,
                                        list_active_calls, list_waiting_calls)
    from .review_task_execution import read_call_kinds
    from .review_stop import build_reconciliation_step, describe_call
    if refusal is None:
        payload = action['payload']
        try:
            check_review_call_admission(records, 'review.invoke', payload['id'], assignment['route_id'],
                                        invocation=payload)
        except ResearchError as error:
            if error.code != 'review_capacity_wait':
                raise
            refusal = error
        else:
            return action
    active = list_active_calls(records)
    capacity = describe_capacity_wait(records, artifacts, assignment['route_id'], active, list_waiting_calls(records))
    details = dict(refusal.details, **capacity, message=refusal.message)
    guidance = dict(message=CAPACITY_SCHEDULER_MSG) if 'scheduler' in capacity else {}
    for owned in active:
        if owned['id'] in records.get('review_reservation', {}):
            call = dict(operation='review.task_reserve', reservation_id=owned['id'])
        else:
            member = next(row for row in records['review_pool_membership'].values()
                          if row.get('invocation_id') == owned['id'] and not row.get('released', True)
                          and records[read_call_kinds(row['operation'])[0]].get(owned['id']) == owned)
            call = dict(operation=member['operation'], invocation_id=owned['id'])
        row = describe_call(records, call)
        if row['state'] in ('interrupted', 'captured', 'accepted'):
            # Reconcile only an ended owner's call. A live owner's current turn is
            # served by waiting on this head's own run instead.
            from .review_stop import _describe_call_step, has_live_call_processes
            lifecycle = _describe_call_step(records, call)[0]
            if row['state'] == 'interrupted' or not has_live_call_processes(lifecycle):
                return dict(state='wait', not_before=_describe_wait(), code='review_capacity_wait',
                            details=details,
                            **build_reconciliation_step(records, call), **guidance)
    return dict(action, state='wait', not_before=_describe_wait(), code='review_capacity_wait',
                details=details, **guidance)


def _plan_ordinary_call(records, artifacts, assignment, action):
    """Gate each newly selected ordinary run on the same snapshot checks as its start."""
    try:
        prepared = protocol.prepare_assignment_call(records, artifacts, payload=action['payload'],
                                                    bounded_chain_checked=True)
    except ResearchError as error:
        if error.code == 'review_capacity_wait':
            return _plan_capacity(records, assignment, action, artifacts, refusal=error)
        from .review_failure_class import describe_assignment_call_refusal
        context = action.get('details', {})
        recovery = context.get('recovery', {})
        if (error.code == 'review_resampling_forbidden' and recovery.get('kind') == 'decision'
                and (assignment.get('session_chain') or assignment.get('reading_chain', {}).get('final_page_plan'))):
            # The run of a capped turn at its floor again is refused while its capped answer is whole and unrefused:
            # the plan is the decision of its capacity recovery, the stop that a second capped run reaches.
            return dict(state='stop', code=recovery['code'], not_before=None, details=copy.deepcopy(context))
        refused = describe_assignment_call_refusal(assignment, error)
        refused['details'] = dict(context, **refused['details'])
        if error.code == 'review_runtime_missing' and context.get('outcome') == 'runtime_unavailable':
            refused['not_before'] = action.get('not_before')
        return refused
    if 'capacity_forecast' in prepared:
        action['capacity_forecast'] = prepared['capacity_forecast']
    if action.get('details', {}).get('outcome') == 'runtime_unavailable':
        action['not_before'] = None
    return action


UNRECORDED_REFUSAL_MESSAGE = (
    'The answer of this turn fails the output check of its turn, and the store holds no refusal record for it. The '
    'write of that record did not commit when the run ended. details.refusal gives the code, the check and the '
    'details of the failure. This step runs the recorded review-run request of that answer again, with its request '
    'ID. It calls no reviewer: it returns the recorded run and records the refusal. The next plan is the step for a '
    'refused answer.')


def _plan_unrecorded_refusal(records, artifacts, assignment, state, packet):
    """Plan the recorded run request again for a native answer that fails its output check without a refusal record.

    The run of such a turn records the refusal, and the same request records it when that write did not commit.
    A turn of a bounded chain (protocol v7 or math v2) and an answer under the finding contract are read only after
    this check, so their head gets this step in place of a continuation or a recording that the command would refuse.
    The check fails only for an authenticated attempt, and the run writes the invocation and the capture of such an
    attempt before the attempt itself, so both records are read directly."""
    from .native_review import uses_finding_contract
    if (openness_reading.find_protocol(packet) not in openness_reading.BOUNDED_CHAIN_PROTOCOLS
            and not uses_finding_contract(assignment)):
        return None
    attempt_id = state['attempt_id']
    if attempt_id in records.get('review_output_refusal', {}):
        return None
    from .review_refusal import classify_observed_output
    failure = classify_observed_output(records, artifacts, attempt_id)
    if failure is None:
        return None
    captured = records['review_call_observation'][identify_call('review.invoke', attempt_id)]
    return dict(state='resume', command='review-run', request_id=captured['request_id'],
                payload=rebuild_call_payload('review.invoke', records['review_invocation'][attempt_id]),
                details=dict(assignment_id=assignment['id'], attempt_id=attempt_id, refusal=failure,
                             message=UNRECORDED_REFUSAL_MESSAGE))


def _plan_recording(records, artifacts, assignment, state):
    if _list_recorded_finals(records, {assignment['id']}):
        return dict(state='done')
    output = copy.deepcopy(state.get('output'))
    effective = state.get('effective') or {}
    if effective.get('kind') in ('ordinary', 'paged'):
        output = copy.deepcopy(effective['effective_output'])
    session_composite = bool(assignment.get('session_chain', {}).get('final_page_plan'))
    if session_composite:
        from .review_composite import read_final_output
        output, _ = read_final_output(records, artifacts, assignment)
    parts_composite = None
    if assignment.get(review_parts.KEY, {}).get('protocol') == 'exactory-review-parts-v3':
        # The selected closing of a final in pages answers with fields; its composite holds the review.
        from .review_composite import read_final_output
        composed, parts_composite = read_final_output(records, artifacts, assignment)
        if parts_composite is not None:
            output = composed
    role = assignment['role']
    if role == 'native_math':
        from .native_review import load_packet, uses_finding_contract
        packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
        if (assignment.get('reading_chain') and openness_reading.find_protocol(
                protocol._read_json(artifacts, assignment['packet'])) == openness_reading.MATH_PROTOCOL_V2):
            return _plan_math_v2_result(records, artifacts, assignment, packet, output)
        if openness_reading.is_openness_packet(packet):
            from .problem_openness import prepare_openness
            try:
                prepare_openness(records, artifacts, dict(assignment_id=assignment['id']),
                                 assignment['recorded_revision'] + 1)
            except ResearchError as error:
                if uses_finding_contract(assignment):
                    outcome = output['outcome'] if 'outcome' in output else output['findings']['openness']['outcome']
                    if outcome != 'unresolved':
                        outdated = _plan_openness_freshness(records, artifacts, assignment, packet)
                        if outdated is not None:
                            return outdated
                    return dict(state='handoff', code=error.code, command='export',
                                details=dict(error.details or {}, assignment_id=assignment['id'],
                                             kind='problem-openness', message=error.message))
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
    from .review_reading import list_source_citations, plan_readings
    attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
    try:
        if not list_source_citations(output):
            handoff = None
        elif command == 'value-review':
            if not session_composite:
                from .research_decisions import validate_review_contract
                validate_review_contract(records, artifacts, assignment, output, attempt=attempt,
                                         composed=parts_composite is not None)
            handoff = plan_readings(records, artifacts, assignment, output)
        elif assignment.get('subject', {}).get('kind') == 'problem':
            packet = protocol._read_json(artifacts, assignment['packet'])
            protocol.validate_adjudication_contract(records, artifacts, assignment, packet, output, attempt=attempt)
            handoff = plan_readings(records, artifacts, assignment, output)
        else:
            handoff = None
    except (ResearchError, KeyError, TypeError, AttributeError):
        # The recording command owns every non-reading failure and its durable
        # refusal. An invalid observation must not ask the agent to read first.
        handoff = None
    if handoff is not None:
        return handoff
    payload = dict(output, id=_find_unused_id(records, 'review:' + assignment['id']),
                   assignment_id=assignment['id'])
    if command == 'value-review':
        payload['dossier_id'] = assignment['dossier_id']
    return dict(state='record', command=command, payload=payload)


MATH_REVIEW_REVISION_PREREQUISITE = (
    'Address the assessment that details.reason and details.objections state before you export. The payload holds '
    'the subject, claim_digest, openness and inputs of the reviewed packet: replace each that the revision changes.')
MATH_REVIEW_ROUTE_PREREQUISITE = (
    'Give route_id a route record that is verified, is not retired and pins the installed runtime. After a final '
    'that ended at its output limit, it is another route record than the one of that final. Give '
    'context.native_packet the reference that the export returned.')
MATH_REVIEW_LEGACY_CLOSURE = (
    'The packet of this assignment holds a closure subject of schema_version 1, and the math controller takes a '
    'new review of a closure only at schema_version 2, so the harness sends no call of this chain. Take '
    'details.step, and then the steps of its then in their order: the export writes a packet with the same closure '
    'subject at schema_version 2, and the assignment starts its review.')
# The question of the stop native_review_stale_minimum, whose one answer is the checked export of details.step.
MATH_REVIEW_STALE_DECISION = 'Whether a new review starts over the present evidence of the math objective.'
MATH_REVIEW_STALE_MINIMUM = (
    'The evidence of the math objective changed after the packet of this chain was exported, so no review over that '
    'packet can be recorded. details.cause, where present, names the refusal of the math controller, and '
    'search_ids, in details or in details.step, the searches that the minimum of the packet does not bind. Take '
    'details.step, and then the steps of its then in their order: the export writes a packet over the present '
    'evidence, and the assignment starts a new review of it. The minimum of the step binds those searches; where '
    'the step lists openness under chooses, the minimum is yours to choose, as its first prerequisite states.')


def describe_checked_math_export(records, artifacts, assignment, native, step, mode):
    """Complete the math caller's export and check its concrete default inputs."""
    from .review_math_handoff import check_export

    fresh_id = _find_unused_id(records, 'math-reissue:' + digest(assignment['id'])[:20])
    if mode == 'legacy':
        step['payload']['subject']['schema_version'] = 2
    step['payload']['reviewer']['attestation_id'] = 'review-assignment:' + fresh_id
    # The binder leaves openness as a choice when the packet does not establish the claim of its minimum: the check
    # then covers the rest of the export, and the choice and its prerequisite stay.
    open_minimum = 'openness' not in step['payload']
    step['payload'], _, search_ids = check_export(records, artifacts, native, step['payload'], refresh=not open_minimum)
    step['chooses'] = ['openness'] if open_minimum else []
    if mode == 'nonapproval':
        step['prerequisites'].append(dict(condition=MATH_REVIEW_REVISION_PREREQUISITE))
        step['chooses'] = ['subject', 'claim_digest', 'openness', 'inputs']
    if search_ids:
        step['search_ids'] = search_ids
    following = step['then'][0]
    following['payload'].update(id=fresh_id, context=dict(native_review_predecessor=assignment['id']))
    following['chooses'].remove('id')
    following['prerequisites'] = [dict(condition=MATH_REVIEW_ROUTE_PREREQUISITE)]
    step['then'] = [dict(copy.deepcopy(following), check=True), following]
    return step


def describe_native_math_stop(details, error, *, native=None):
    """Describe a read-only action for a refused or unavailable native transition."""
    unavailable = error.code == 'native_review_objective_unavailable'
    code = error.code if unavailable else 'native_review_recording_blocked'
    if unavailable or native is None:
        step = dict(command='status', payload={}, chooses=[])
    else:
        step = dict(command='search guard-status', root=native['root'], chooses=[],
                    payload=dict(objective_id=native['objective_id'], contract_digest=digest(native['contract'])))
    # The decision of the registry row of the code, whose one answer takes this step as it stands.
    from .stop_classes import STOP_REGISTRY
    rule = STOP_REGISTRY[code]['next']
    decision = dict(owner_decides=rule['owner_decides'],
                    then=[dict(answer=rule['then'][0]['answer'], steps=[copy.deepcopy(step)])])
    return dict(state='stop', code=code, command=step['command'], stop_class='decision',
                details=dict(details, message=error.message, refusal=dict(code=error.code, details=error.details),
                             step=step, decision=decision))


def _plan_math_v2_export(records, artifacts, assignment, details, code, *, revise=False, legacy=False):
    """Bind a verified fresh native export to the check and write of its new assignment."""
    from .review_plan_steps import describe_step
    from .review_math_handoff import find_successor

    native = protocol._read_json(artifacts, assignment['context']['native_packet'])
    mode = 'legacy' if legacy else 'nonapproval' if revise else 'stale'
    first = find_successor(records, assignment)
    if first is not None:
        return dict(state='done', details=dict(details, successor_assignment_id=first['id']))
    step = describe_step(records, artifacts, assignment, 5, checked_export=True)
    try:
        step = describe_checked_math_export(records, artifacts, assignment, native, step, mode)
    except ResearchError as error:
        return describe_native_math_stop(details, error, native=native)
    if mode != 'stale':
        return dict(state='handoff', code=code, command=step['command'], stop_class='decision',
                    details=dict(details, step=step))
    from .openness_successor import MATH_SUCCESSOR_ANSWER
    decision = dict(owner_decides=MATH_REVIEW_STALE_DECISION,
                    then=[dict(answer=MATH_SUCCESSOR_ANSWER, steps=[copy.deepcopy(step)])])
    return dict(state='stop', code=code, command=step['command'], stop_class='decision',
                details=dict(details, step=step, decision=decision))


def _plan_legacy_math_closure(records, artifacts, assignment, *, packet=None):
    """Send an ineligible closure to the native exporter before a new observation."""
    if assignment['role'] != 'native_math' or not assignment.get('capacity_plan') or not assignment.get('reading_chain'):
        return None
    if packet is None:
        packet = protocol._read_json(artifacts, assignment['packet'])
    from .openness_reading import MATH_PROTOCOL_V2
    if openness_reading.find_protocol(packet) != MATH_PROTOCOL_V2:
        return None
    native = protocol._read_json(artifacts, assignment['context']['native_packet'])
    if 'acceptance_ids' not in native['subject'] or native['subject'].get('schema_version') == 2:
        return None
    return _plan_math_v2_export(records, artifacts, assignment,
        dict(assignment_id=assignment['id'], message=MATH_REVIEW_LEGACY_CLOSURE),
        'native_review_legacy_closure', legacy=True)


def _plan_math_v2_result(records, artifacts, assignment, native, output):
    """Offer only a native recording or export the controller has checked."""
    from .review_math_handoff import check_recording, STALE_NATIVE_EVIDENCE

    # The decision of the review has its own member: details.decision is the decision of the owner that a stop holds.
    details = dict(assignment_id=assignment['id'], role=assignment['role'], review_decision=output['decision'])
    if output['decision'] != 'approve':
        details.update(reason=output['reason'], objections=output['objections'])
        return _plan_math_v2_export(records, artifacts, assignment, details, 'native_review_not_approved', revise=True)
    try:
        step = check_recording(artifacts, native, output)
    except ResearchError as error:
        if error.code in STALE_NATIVE_EVIDENCE:
            details.update(error.details or {}, cause=error.code, message=MATH_REVIEW_STALE_MINIMUM)
            return _plan_math_v2_export(records, artifacts, assignment, details, 'native_review_stale_minimum')
        return describe_native_math_stop(details, error, native=native)
    if step is None:
        return dict(state='done', details=details)
    code = ('native_review_completion_required' if step['command'] == 'search complete'
            else 'native_review_acceptance_required')
    return dict(state='handoff', code=code, command=step['command'], details=dict(details, step=step))


def _plan_staged_action(records, epoch, task, action, artifacts=None):
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
    if task['state'] not in ('waiting', 'reconciling') and result['state'] == 'run':
        refusal = _check_staged_start(records, epoch, task, result['payload'])
        if refusal is not None:
            return refusal
    if task['state'] in ('waiting', 'reconciling'):
        result.update(state='wait', not_before=_describe_wait(),
                      code=task['code'], details=dict(task_id=task['task_id']))
        if task['code'] == 'review_capacity_wait':
            from .review_call_admission import describe_capacity_wait, list_active_calls, list_waiting_calls
            controller = records['review_assignment'][epoch['controller_assignment_id']]
            result['details'].update(describe_capacity_wait(records, artifacts, controller['route_id'],
                list_active_calls(records), list_waiting_calls(records)))
            if 'scheduler' in result['details']:
                result['message'] = CAPACITY_SCHEDULER_MSG
    return result


def _check_staged_start(records, epoch, task, payload):
    """Check a fresh task start with the reservation's own admission: allowance and standing stops.

    The reservation waits for capacity, so a capacity refusal leaves the run planned. A fresh start reserves under a
    new request identity, which no charge holds, so the payload id stands for it in the charge rule."""
    from .review_call_admission import check_review_call_admission
    child = next(row for row in records['review_assignment'].values()
                 if row.get('staged_review', {}).get('task_id') == task['task_id'])
    try:
        check_review_call_admission(records, 'review.task_reserve', payload['id'], child['route_id'],
                                    epoch_id=epoch['id'], task_id=task['task_id'])
    except ResearchError as error:
        if error.code == 'review_capacity_wait':
            return None
        from .review_failure_class import describe_assignment_call_refusal
        return describe_assignment_call_refusal(records['review_assignment'][epoch['controller_assignment_id']], error)
    return None


def _plan_staged(records, artifacts, assignment, state):
    from .problem_delivery import describe_review_tasks
    epoch = protocol._get(records, 'review_epoch', assignment['staged_review']['epoch_id'])
    effective = state.get('effective') or {}
    if effective.get('incomplete_outcome') is not None:
        return dict(state='stop', code='assessment_incomplete',
                    details=effective['incomplete_outcome'])
    if state.get('output') is not None:
        return _plan_recording(records, artifacts, assignment, state)
    for task in describe_review_tasks(records, epoch, state):
        if task['state'] != 'accepted' and task.get('next'):
            return _plan_staged_action(records, epoch, task, task['next'][0], artifacts)
    from .review_staged_protocol import describe_planning_command
    action = describe_planning_command(assignment['id'])
    return dict(state='plan', command=action['command'], payload=action['payload'])


def _plan_openness_freshness(records, artifacts, assignment, packet=None):
    if assignment['role'] != 'native_math':
        return None
    if packet is None:
        packet = protocol._read_json(artifacts, assignment['packet'])
        if openness_reading.find_protocol(packet) not in (openness_reading.PROTOCOL_V6, openness_reading.PROTOCOL_V7):
            return None
        packet = protocol._read_json(artifacts, assignment['context']['native_packet'])
    from .problem_openness import assess_packet_freshness, OUTDATED_PACKET_MESSAGE
    freshness = assess_packet_freshness(records, artifacts, packet)
    if freshness['current']:
        return None
    return dict(state='handoff', command='export', code='openness_packet_outdated',
                details=dict(assignment_id=assignment['id'], kind='problem-openness',
                             message=OUTDATED_PACKET_MESSAGE,
                             added=freshness['added'], removed=freshness['removed']))


def _plan_session_capacity_stop(records, assignment, error):
    """The writable after-turn stop of a session v4 head whose pinned capacity plan cannot serve its next write."""
    from .stop_classes import classify_stop
    limit = ', '.join('%s %s' % (key, value) for key, value in sorted((error.details or {}).items())
                      if isinstance(value, int) and not isinstance(value, bool))
    message = SESSION_CAPACITY_STOP_MSG.format(limit=limit or 'its smallest allocation is larger than its room')
    stop = dict(id=_find_unused_id(records, 'capacity-stop:' + assignment['id']), action='stop', mode='after_turn',
                assignment_id=assignment['id'], reason=error.code + ': ' + message)
    return dict(state='stop', code=error.code, stop_class=classify_stop(error.code), command='review-stop',
                payload=stop, details=dict(error.details or {}, message=message,
                                           step=dict(command='review-stop', payload=stop)))


def _plan_session_refresh(records, artifacts, assignment):
    """Rules F3 and F5: the refresh of the review that this head of a chain of the session rules v4, or this ordinary
    slate assignment, recorded is a new chain in its slot, which names the head in the context member session_refresh.
    Its route record is the one that select_step_route gives for the route of the head. While the store lacks that
    record or its probe, the plan is the step that gives it, then review-series. After retirement, complete_plan uses
    this payload for the handoff. The refresh of an ordinary slate is checked first with the capacity admission of
    review-assignment; when that admission refuses it, the plan is the capacity stop, whose steps complete_plan names."""
    from .review_route_plan import plan_retired_route, select_step_route
    found = select_step_route(records, artifacts, assignment['route_id'])
    if found['lacks'] is not None:
        return dict(state='handoff', command='review-route', code='review_route_runtime_stale',
                    details=dict(assignment_id=assignment['id'], route_id=assignment['route_id'],
                                 renewal=dict(admitted=True, **found), message=REFRESH_ROUTE_MSG))
    payload = _build_assignment_payload(records, assignment)
    payload['context'] = {'session_refresh': {'assignment_id': assignment['id']}}
    if found['route_id'] is None:
        return dict(plan_retired_route(assignment), payload=payload)
    payload['route_id'] = found['route_id']
    if not assignment.get('session_chain'):
        refusal = protocol.check_assignment_capacity(records, artifacts, payload)
        if refusal is not None:
            return dict(state='stop', code=refusal.code, details=refusal.details, payload=payload)
    return dict(state='continue', command='review-assignment', payload=payload)


def _plan_context_handoff(records, artifacts, assignment, lost):
    """Complete the handoff for the named turn so its command follows the actual cause."""
    from .review_plan_steps import complete_plan
    details = dict(assignment_id=assignment['id'], turn=lost, route_id=lost['route']['id'])
    if protocol.is_contaminated_context(lost):
        code, message = 'review_context_contaminated', protocol.CONTEXT_CONTAMINATED_MSG
    else:
        code, message = 'review_context_unverified', protocol.CONTEXT_UNVERIFIED_MSG
    return complete_plan(records, artifacts, assignment,
                         dict(state='handoff', code=code, details=dict(details, message=message)))


def _plan_head(records, artifacts, assignment):
    pending = _plan_pending_call(records, assignment)
    if pending is not None:
        return pending
    legacy = _plan_legacy_math_closure(records, artifacts, assignment)
    if legacy is not None:
        return legacy
    if assignment.get('capacity_plan') and assignment.get('reading_chain'):
        from .openness_reading_v7 import describe_carried_handoff, describe_content_handoff, describe_ratio_handoff
        if not _list_recorded_finals(records, {assignment['id']}):
            # Rules C24 and C29: a changed unit, or a carried turn without a verified context, comes before every
            # further step of the chain.
            handoff = (describe_content_handoff(records, artifacts, assignment)
                       or describe_carried_handoff(records, artifacts, assignment))
            if handoff is not None:
                return handoff
        handoff = describe_ratio_handoff(records, artifacts, assignment)
        if handoff is not None:
            return handoff
    if assignment['context_state']['context_status'] != 'verified_for_route':
        if assignment.get('staged_review', {}).get('kind') == 'controller':
            from .problem_delivery import describe_review_tasks
            epoch = records['review_epoch'][assignment['staged_review']['epoch_id']]
            for task in describe_review_tasks(records, epoch, {}):
                if task['state'] in ('reconciling', 'captured') and task.get('next'):
                    return _plan_staged_action(records, epoch, task, task['next'][0])
        if protocol.describe_assignment_calls(records, assignment)['ran']:
            return dict(state='stop', code='review_context_unverified',
                        details=dict(assignment_id=assignment['id']))
        if protocol.is_dead_assignment(records, assignment):
            route = protocol.route_state(records, artifacts, assignment['route_id'])
            if route['context_status'] != 'verified_for_route':
                return dict(state='handoff', command='review-probe', code='review_context_unverified',
                            details=dict(assignment_id=assignment['id'], route_id=assignment['route_id']))
            if assignment['role'] == 'native_math' and not assignment.get('context', {}).get('prior_assignment_id'):
                # A native packet attests its first assignment ID; a new first turn needs a new packet.
                from .native_review import load_packet
                packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
                common = openness_reading.is_openness_packet(packet)
                return dict(state='handoff', command='export' if common else 'search review-packet',
                            code='native_review_attestation_required',
                            details=dict(assignment_id=assignment['id'],
                                         **(dict(kind='problem-openness') if common else {})))
            return dict(state='replace', command='review-assignment', code='review_context_unverified',
                        payload=_build_assignment_payload(records, assignment, replace=True),
                        details=dict(assignment_id=assignment['id']))
    recorded = _list_recorded_finals(records, {assignment['id']})
    if assignment.get('staged_review'):
        state = protocol.assignment_state(records, artifacts, assignment['id'])
        context_codes = ('review_context_unverified', 'review_context_contaminated')
        blocking = [item for item in state['obligations']
                    if item['code'] not in ('review_task_pending', *context_codes)]
        if blocking:
            return dict(state='handoff', command='review-assignment', code=blocking[0]['code'], details=blocking[0])
        plan = _plan_staged(records, artifacts, assignment, state)
        if recorded or plan['state'] == 'done' or (plan.get('payload') or {}).get('action') in ('accept', 'reconcile'):
            return plan
        lost = protocol.find_blocking_context(records, artifacts, assignment)
        if lost is not None:
            return _plan_context_handoff(records, artifacts, assignment, lost)
        for item in state['obligations']:
            if item['code'] in context_codes:
                return dict(state='handoff', command='review-assignment', code=item['code'], details=item)
        return plan
    lost = None if recorded else protocol.find_blocking_context(records, artifacts, assignment)
    attempts = [row for row in records.get('review_attempt', {}).values()
                if row['assignment_id'] == assignment['id']]
    initial = (assignment['role'] in ('adjudicator', 'result')
               and assignment.get('subject', {}).get('kind') == 'problem')
    if (not attempts and initial
            and protocol._initial_size_failure(records, artifacts, assignment, assignment)):
        if lost is not None:
            return _plan_context_handoff(records, artifacts, assignment, lost)
        return dict(state='replace', command='review-assignment',
                    payload=_build_assignment_payload(records, assignment, replace=True),
                    code='review_input_too_large', details=dict(assignment_id=assignment['id']))
    failed = protocol.has_ended_without_output(records, assignment['id'])
    if attempts and assignment.get('capacity_plan_kind') == 'final':
        from .review_failure_class import classify_final_capacity_attempt
        latest = max(attempts, key=lambda row: row.get('recorded_revision', 0))
        failed = failed or classify_final_capacity_attempt(records, artifacts, latest) is not None
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
        if attempts and not usable:
            latest = max(attempts, key=lambda row: row.get('recorded_revision', 0))
            failed = bool(refused) or latest['status'] != 'completed' or not protocol._context_observed(artifacts, latest)
        if not observed:
            # The selector retains unobserved output for diagnostics. Invocation
            # eligibility ignores that attempt, including its binding diagnostic.
            state = dict(state, output=None, obligations=[item for item in state['obligations']
                if item['code'] not in ('review_invocation_unverified', 'review_output_unavailable')])
    if not attempts:
        from .review_failure_class import list_failure_position_assignments
        position = set(list_failure_position_assignments(records, artifacts, assignment))
        failed = any(row['assignment_id'] in position for row in records.get('review_attempt', {}).values())
    if failed or unverified:
        from .review_failure_class import is_math_request_final, plan_after_failure
        try:
            result = copy.deepcopy(plan_after_failure(records, artifacts, assignment['id'], check_recovered_call=False))
        except ResearchError as error:
            # The replacement of a failed v4 turn is built under the same pinned plan as its successor.
            from .review_session_v4 import PINNED_CAPACITY_CODES
            if not assignment.get('session_chain') or error.code not in PINNED_CAPACITY_CODES:
                raise
            return _plan_session_capacity_stop(records, assignment, error)
        failure = result.get('details', {})
        if is_math_request_final(assignment) and result.get('code') == 'review_output_over_cap':
            # The answer of the cap stop starts a chain that names this head as its native predecessor. Once that
            # chain exists, this head is done, as the head of every other math v2 handoff is.
            from .review_math_handoff import find_successor
            successor = find_successor(records, assignment)
            if successor is not None:
                return dict(state='done', details=dict(assignment_id=assignment['id'],
                                                       successor_assignment_id=successor['id']))
        if is_math_request_final(assignment) and failure.get('outcome') == 'scientific_refused':
            refusal = records.get('review_output_refusal', {}).get(failure.get('attempt_id'), {})
            diagnosis = refusal.get('details', {})
            if diagnosis.get('code') == 'openness_search_stale':
                return _plan_math_v2_export(records, artifacts, assignment,
                    dict(assignment_id=assignment['id'], search_ids=diagnosis['search_ids'],
                         message=MATH_REVIEW_STALE_MINIMUM),
                    'native_review_stale_minimum')
        if not recorded:
            details = result.get('details') or {}
            if result.get('command') == 'review-output-correction':
                correction_route = details.get('correction_route_id')
                if correction_route is not None:
                    lost = protocol.find_blocking_context(records, artifacts, assignment, route_id=correction_route)
                blocked = lost
            elif result['state'] == 'replace' and assignment['role'] not in protocol.context_repair.ROLES:
                payload = details.get('recovery', {}).get('payload') or result.get('payload') or {}
                blocked = protocol.find_blocking_replacement_context(
                    records, artifacts, assignment, payload.get('route_id', assignment['route_id']))
            else:
                blocked = lost if lost is not None and (
                    result['state'] in ('run', 'wait', 'replace') or protocol.is_contaminated_context(lost)) else None
            if blocked is not None:
                return _plan_context_handoff(records, artifacts, assignment, blocked)
        # A refused answer keeps its frozen contract diagnosis and bounded retry.
        if result.get('details', {}).get('outcome') != 'scientific_refused':
            outdated = _plan_openness_freshness(records, artifacts, assignment)
            if outdated is not None:
                return outdated
        if result['state'] == 'replace':
            recovery = result.get('details', {}).get('recovery', {})
            single_replacement = recovery.get('details', {}).get('handler') == 'single_request_series'
            capacity_replacement = (recovery.get('kind') == 'replace'
                and recovery.get('payload', {}).get('context', {}).get('capacity_replacement') == assignment['id'])
            if (not single_replacement and not capacity_replacement and assignment['role'] == 'native_math'
                    and not assignment.get('capacity_plan')
                    and not assignment.get('context', {}).get('prior_assignment_id')):
                from .native_review import load_packet
                packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
                common = openness_reading.is_openness_packet(packet)
                return dict(state='handoff', command='export' if common else 'search review-packet',
                            code='native_review_attestation_required',
                            details=dict(assignment_id=assignment['id'],
                                         **(dict(kind='problem-openness') if common else {})))
            if (not single_replacement and not capacity_replacement and assignment['role'] in bar_sources.ROLES
                    and not assignment.get('context', {}).get('prior_assignment_id')
                    and not assignment.get('context', {}).get('context_repair')
                    and not result.get('payload', {}).get('context', {}).get('output_refusal')
                    and not result.get('payload', {}).get('context', {}).get('unread')
                    and not (initial and protocol._initial_size_failure(
                        records, artifacts, assignment, assignment))):
                return dict(state='handoff', command='review-assignment',
                            code='review_resampling_forbidden',
                            details=dict(assignment_id=assignment['id'],
                                         failure_code=result.get('code'),
                                         failure_details=result.get('details', {})))
            payload = result.get('payload') or _build_assignment_payload(records, assignment, replace=True)
            if recovery.get('kind') == 'replace':
                payload = copy.deepcopy(recovery['payload'])
                payload['id'] = _find_unused_id(records, payload['id'])
            result.update(command='review-assignment', payload=payload)
        elif result['state'] == 'handoff':
            result.setdefault('command', 'review-probe')
        elif result['state'] in ('run', 'wait'):
            result.update(command='review-run', payload=_build_run_payload(records, assignment))
        return result
    if lost is not None:
        if (not protocol.is_contaminated_context(lost) and state.get('output') is not None
                and assignment['role'] == 'native_math'):
            packet = protocol._read_json(artifacts, assignment['packet'])
            replay = _plan_unrecorded_refusal(records, artifacts, assignment, state, packet)
            if replay is not None:
                return replay
        return _plan_context_handoff(records, artifacts, assignment, lost)
    blocking = [item for item in state.get('obligations', [])
                if item['code'] not in ('review_output_pending', 'review_output_refused')]
    if blocking:
        item = blocking[0]
        repair = item['code'].startswith('review_context_') or item['code'] == 'review_route_retired'
        return dict(state='handoff' if repair else 'stop', code=item['code'], details=item,
                    **(dict(command='review-assignment') if repair else {}))
    if state.get('output') is None:
        outdated = _plan_openness_freshness(records, artifacts, assignment)
        if outdated is not None:
            return outdated
        from .openness_successor import describe_input_handoff
        handoff = describe_input_handoff(records, artifacts, assignment)
        if handoff is not None:
            return handoff
        return dict(state='run', command='review-run', payload=_build_run_payload(records, assignment))
    packet = protocol._read_json(artifacts, assignment['packet'])
    if assignment['role'] == 'native_math' and not _list_recorded_finals(records, {assignment['id']}):
        unrecorded = _plan_unrecorded_refusal(records, artifacts, assignment, state, packet)
        if unrecorded is not None:
            return unrecorded
    if protocol.is_session_refresh(records, artifacts, assignment):
        return _plan_session_refresh(records, artifacts, assignment)
    from . import review_parts_v3
    is_parts = review_parts.KEY in assignment
    continuing = (is_parts and (assignment[review_parts.KEY]['kind'] != 'final'
                                or packet[review_parts.KEY].get('final_form') == 'page'
                                or (packet[review_parts.KEY].get('page_plan_digest') and
                                    state['output'].get('stage') == 'material')
                                or review_parts_v3.is_final_material_answer(packet, state['output'])
                                or state['output'].get('kind') == 'requests')
                  or review_problem_parts.find_stage(packet) == 'part'
                  or openness_reading.find_stage(packet) is not None and state['output'].get('stage') in ('reading', 'material')
                  or openness_reading.find_stage(packet) in ('page', 'combine')
                  or bool(state.get('pending_source_requests'))
                  or (packet.get(review_parts.KEY, {}).get('protocol') != 'exactory-review-parts-v3'
                      and bool(state['output'].get('settled_requests')))
                  or (assignment.get('subject', {}).get('kind') == 'problem'
                      and assignment['role'] in bar_sources.ROLES and not assignment.get('session_chain')
                      and _list_recorded_finals(records, {assignment['id']})
                      and protocol._is_problem_refresh(records, artifacts, assignment, packet)))
    if continuing:
        outdated = _plan_openness_freshness(records, artifacts, assignment)
        if outdated is not None:
            return outdated
        payload = _build_assignment_payload(records, assignment)
        if assignment.get('session_chain'):
            from .review_session_v4 import PINNED_CAPACITY_CODES, require_writable_session_continuation
            try:
                require_writable_session_continuation(records, artifacts, assignment, payload)
            except ResearchError as error:
                if error.code not in PINNED_CAPACITY_CODES:
                    raise
                return _plan_session_capacity_stop(records, assignment, error)
        from .openness_successor import describe_input_handoff
        handoff = describe_input_handoff(records, artifacts, assignment, continuation=payload)
        if handoff is not None:
            return handoff
        if packet.get(review_parts.KEY, {}).get('protocol') == 'exactory-review-parts-v3':
            from .review_route_capacity import describe_capacity_decision
            try:
                protocol.preview_parts_successor(records, artifacts, payload)
            except ResearchError as error:
                if error.code not in ('review_route_capacity_plan_violated', 'review_route_capacity_below_floor',
                                      'review_input_too_large', 'review_parts_frame_too_large'):
                    raise
                decision = describe_capacity_decision(records, artifacts, assignment, 'input_over_limit',
                    dict(error.details or {}, refusal=error.code))
                return dict(state='stop', code=decision['code'], details=dict(decision['details'],
                    decision={key: decision[key] for key in ('owner_decides', 'then')}))
        return dict(state='continue', command='review-assignment', payload=payload)
    return _plan_recording(records, artifacts, assignment, state)


def plan_next(records, artifacts, head_id, *, context_failures=None):
    """Describe one exact action; the named operation retains every mutation validator."""
    from .review_state_evaluation import runtime_scope
    try:
        with runtime_scope(records, artifacts) as evaluation, evaluation.planning_view() as preview:
            return _plan_next(records, preview, head_id, context_failures=context_failures)
    except ResearchError as error:
        if error.code != 'review_runtime_changed':
            raise
        result = describe_runtime_change(head_id, error)
        result.update(chain=None, head_id=head_id, progress=None)
        return result


def describe_runtime_change(head_id, error):
    """The stop of a plan whose reviewer runtime changed while the plan read it; review-series plans again."""
    from .stop_classes import describe_terminal
    result = describe_terminal('review_runtime_changed')
    result['state'] = 'stop'
    result['details'].update(message=error.message, step=dict(command='review-series', assignment_id=head_id))
    return result


def _plan_next(records, artifacts, head_id, *, context_failures=None):
    result = dict(state='stop', chain=None, head_id=head_id, progress=None)
    initial_assignment = None
    assignment = None
    stop = None
    try:
        from .review_stop import find_stop, describe_stop
        if records.get('review_stop'):
            initial_assignment = records.get('review_assignment', {}).get(head_id)
            if initial_assignment is not None:
                stop = find_stop(records, assignment_id=head_id)
        assignment = _find_head(records, artifacts, head_id, context_failures=context_failures)
        result.update(chain=_describe_chain(assignment), head_id=assignment['id'],
                      progress=_describe_progress(records, artifacts, assignment, context_failures=context_failures))
        from .problem_delivery import find_review_post
        if find_review_post(records, assignment, artifacts) is not None:
            from .review_stop import describe_closed_calls
            result.update(state='done', details=dict(closed_calls=describe_closed_calls(records, artifacts, assignment)))
        elif stop is not None:
            result.update(state='handoff', command='review-stop', code='review_chain_stopped',
                          details=dict(stop_id=stop['id'], **describe_stop(records, stop)))
        else:
            action = _plan_head(records, artifacts, assignment)
            if (action['state'] == 'run' and action.get('command') == 'review-run'
                    and not assignment.get('staged_review')):
                action = _plan_ordinary_call(records, artifacts, assignment, action)
            result.update(action)
        if assignment['role'] in bar_sources.ROLES and result['state'] in ('continue', 'record'):
            from .review_route_capacity import describe_session_forecast, describe_session_stop
            state = protocol.assignment_state(records, artifacts, assignment['id'])
            forecast = describe_session_forecast(records, artifacts, assignment, output=state.get('output'))
            if forecast is not None:
                result['capacity_forecast'] = forecast
                stopped = describe_session_stop(forecast)
                if stopped is not None and result['state'] == 'continue':
                    for key in ('command', 'payload', 'not_before', 'request_id'):
                        result.pop(key, None)
                    result.update(stopped)
        from .review_plan_steps import complete_plan
        from .review_route_plan import apply_route_rule
        if result['state'] != 'run' or assignment.get('staged_review'):
            result = apply_route_rule(records, artifacts, assignment,
                                      complete_plan(records, artifacts, assignment, result))
        if result.get('details', {}).get('step', {}).get('id') in (4, 5):
            # A new packet that a successor chain already continues is not exported again.
            from .openness_successor import find_successor_chain
            successor = find_successor_chain(records, artifacts, assignment)
            if successor is not None:
                result = dict(state='done', chain=result['chain'], head_id=result['head_id'],
                              progress=result['progress'], details=dict(successor_assignment_id=successor['id']))
    except (ResearchError, json.JSONDecodeError) as error:
        if stop is not None and isinstance(error, ResearchError) and error.code == 'review_source_continuation_fork':
            from .review_plan_steps import complete_plan
            assignment = assignment or initial_assignment
            result.update(state='handoff', chain=_describe_chain(assignment), head_id=assignment['id'],
                command='review-stop', code='review_chain_stopped',
                details=dict(stop_id=stop['id'], **describe_stop(records, stop)))
            return copy.deepcopy(complete_plan(records, artifacts, assignment, result))
        if isinstance(error, ResearchError):
            result.update(state='stop', code=error.code, details=dict(error.details or {}, message=error.message))
        else:
            result.update(state='stop', code='review_input_unreadable', details=dict(kind=type(error).__name__))
        from .stop_classes import describe_terminal, describe_unlisted_step
        terminal = describe_terminal(result['code'])
        details = dict(result['details'], **terminal['details'])
        for field in ('command', 'payload', 'not_before', 'request_id'):
            result.pop(field, None)
        result.update(terminal, details=details)
        if result['state'] in ('wait', 'continue') and assignment is not None:
            result.update(state='wait', command='review-run', not_before=_describe_wait(),
                          payload=_build_run_payload(records, assignment))
        elif assignment is not None:
            try:
                from .review_plan_steps import complete_plan
                from .review_route_plan import apply_route_rule
                result = apply_route_rule(records, artifacts, assignment,
                                          complete_plan(records, artifacts, assignment, result))
            except (ResearchError, json.JSONDecodeError) as detail_error:
                # A damaged binding cannot supply an executable repair payload.
                decision = describe_unlisted_step()
                decision.pop('kind')
                result.update(state='stop', code=getattr(detail_error, 'code', 'review_input_unreadable'),
                              details=dict(decision=decision, reason=str(detail_error)))
                result.pop('command', None)
        elif result['state'] not in ('done', 'stop'):
            decision = describe_unlisted_step()
            decision.pop('kind')
            result.update(state='stop', details=dict(unlisted=True, decision=decision))
    if result.get('code'):
        from .stop_classes import describe_terminal
        action = ({key: copy.deepcopy(result[key]) for key in ('command', 'state', 'payload') if key in result}
                  if result['state'] == 'handoff' else dict(command='plan_next', assignment_id=head_id))
        terminal = describe_terminal(result['code'], action=action)
        if terminal['details'].get('unlisted'):
            result.setdefault('details', {}).update(terminal['details'])
            if result['state'] == 'handoff':
                result.update(state='stop')
                result.pop('command', None)
                result.pop('payload', None)
    return copy.deepcopy(result)


def describe_next_actions(records, artifacts, assignment_id, actions=(), *, context_failures=None, plan=None):
    """Add executable actions while preserving existing navigation fields. A caller that has already planned the
    assignment on the same records passes that plan."""
    if plan is None:
        plan = plan_next(records, artifacts, assignment_id, context_failures=context_failures)
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
                selected = _plan_staged_action(records, epoch, task, original, artifacts)
        for key in ('command', 'payload', 'request_id', 'not_before', 'code', 'details'):
            action.pop(key, None)
            if key in selected:
                action[key] = copy.deepcopy(selected[key])
        if selected.get('code') == 'review_capacity_wait' and 'message' in selected:
            action['message'] = selected['message']
        action['state'] = selected['state']
        if is_posted:
            action.pop('workflow', None)
            action.pop('scope', None)
        result.append(action)
    return result


def _describe_initial_problem_bar_step(records, artifacts):
    from .research_decisions import select_problem_slot_assignments
    from .review_call_admission import describe_route_scheduler
    from .review_task_execution import COVERED_ADAPTERS

    routes = records.get('review_route', {}).keys() - protocol.retired_routes(records)
    if not routes or len(select_problem_slot_assignments(records, 'bar', None)) >= 2:
        return None
    payload = dict(role='bar', dossier_id=None)
    if 'store' not in records.get('review_capacity_policy', {}):
        scheduler = describe_route_scheduler(records, artifacts, routes, COVERED_ADAPTERS)
        if scheduler['store_limit'] >= 2:
            payload['scheduler'] = scheduler
    return dict(command='review-assignment', payload=payload,
                chooses=['id', 'route_id', 'reviewer_id', 'author_id', 'context'])


def annotate_review_obligations(records, artifacts, report, *, context_failures=None):
    """Attach review actions, optionally collecting unreadable contexts for status."""
    # The plan of each head that the context check below builds, which the actions of its obligations reuse.
    plans = {}
    if 'review' in report and 'obligations' in report:
        from .problem_delivery import describe_after_post_obligations, describe_posted_calls, find_review_post
        rows = []
        closed_rows = []
        after_post = list(report.get('after_post', []))
        seen_calls = {(row.get('operation'), row.get('invocation_id'), row.get('reservation_id'))
                      for row in after_post if row.get('operation') is not None}
        for call in describe_posted_calls(records, artifacts, context_failures=context_failures):
            identity = (call['operation'], call.get('invocation_id'), call.get('reservation_id'))
            if identity not in seen_calls:
                after_post.append(call)
                seen_calls.add(identity)
        for item in report['obligations']:
            identifier = item.get('assignment_id') or item.get('controller_assignment_id')
            assignment = records.get('review_assignment', {}).get(identifier)
            posted = False
            with _collect_context_failure(context_failures, identifier):
                posted = assignment is not None and find_review_post(records, assignment, artifacts) is not None
            if posted:
                closed_rows.append(item)
                after_post.extend(describe_after_post_obligations([item]))
            else:
                rows.append(item)
        if len(rows) != len(report['obligations']):
            report = dict(report, obligations=rows, after_post=after_post)
            if 'counts' in report:
                report['counts'] = dict(report['counts'], obligations=len(rows))
            if report.get('next') in closed_rows:
                report['next'] = rows[0] if rows else None
        represented = {item.get('assignment_id') or item.get('controller_assignment_id')
                       for item in rows}
        retained_findings = {digest(item) for item in after_post}
        upcoming = report.get('next')
        for identifier in list_chain_heads(records, artifacts, context_failures=context_failures):
            with _collect_context_failure(context_failures, identifier):
                assignment = records['review_assignment'][identifier]
                if assignment['role'] == 'native_math':
                    try:
                        from .native_review import load_packet
                        packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
                    except (ResearchError, json.JSONDecodeError) as error:
                        if context_failures is not None:
                            failure = describe_context_failure(error, identifier)
                            if failure not in context_failures:
                                context_failures.append(failure)
                        continue
                    if not openness_reading.is_openness_packet(packet):
                        continue
                from .graph import obligation
                posted = find_review_post(records, assignment, artifacts) is not None
                if identifier not in represented:
                    state = protocol.assignment_state(records, artifacts, identifier)
                    pending = [dict(item, assignment_id=identifier) for item in state.get('obligations', [])]
                    binding = records.get('review_attempt', {}).get(state.get('attempt_id'))
                    if (not state.get('obligations') and binding is not None
                            and not state.get('output_correction_id')
                            and not protocol._context_observed(artifacts, binding)):
                        pending.append(obligation('review_output_pending',
                            'A complete observed reviewer response is required.', assignment_id=identifier))
                    if state.get('pending_source_requests'):
                        pending.append(obligation('review_sources_pending',
                            'Deliver the sources requested by the observed reviewer.',
                            assignment_id=identifier, requests=state['pending_source_requests']))
                    if posted:
                        for item in describe_after_post_obligations(pending):
                            identity = digest(item)
                            if identity not in retained_findings:
                                after_post.append(item)
                                retained_findings.add(identity)
                    else:
                        rows.extend(pending)
                if posted or _list_recorded_finals(records, {identifier}):
                    continue
                # PC8 follows the planned operation, including replacements, replays and staged dispatches.
                plan = plans[identifier] = plan_next(records, artifacts, identifier, context_failures=context_failures)
                code, turn = plan.get('code'), plan.get('details', {}).get('turn')
                if (plan['state'] != 'handoff' or turn is None
                        or code not in ('review_context_unverified', 'review_context_contaminated')):
                    continue
                for index, item in enumerate(rows):
                    if (item['code'] == code
                            and (item.get('assignment_id') or item.get('controller_assignment_id')) == identifier):
                        rows[index] = dict(item, turn=turn)
                        if upcoming == item:
                            upcoming = rows[index]
                        break
                else:
                    # Keep the two context codes explicit at the obligation constructor for the reach check.
                    rows.append(obligation(
                        'review_context_contaminated' if code == 'review_context_contaminated'
                        else 'review_context_unverified',
                        protocol.ROUTE_CONTEXT_MSG, assignment_id=identifier, turn=turn))
        if after_post != report.get('after_post', []):
            report = dict(report, after_post=after_post)
        if rows != report['obligations']:
            from .report_views import order_obligations
            report = dict(report, obligations=order_obligations(rows))
            if 'counts' in report:
                report['counts'] = dict(report['counts'], obligations=len(rows))
            report['next'] = upcoming if upcoming is not None else report['obligations'][0]
    result = dict(report)
    staged_outcomes = None
    for key in ('obligations', 'manuscript_obligations', 'objective_obligations'):
        if key not in report:
            continue
        rows = []
        for original in report[key]:
            item = dict(original)
            if 'review' in report and item['code'] == 'problem_bar_pending':
                step = _describe_initial_problem_bar_step(records, artifacts)
                if step is not None:
                    item['next'] = [step]
            identifier = item.get('assignment_id') or item.get('controller_assignment_id')
            with _collect_context_failure(context_failures, identifier):
                if identifier in records.get('review_assignment', {}):
                    item['next'] = describe_next_actions(
                        records, artifacts, identifier, item.get('next', []), context_failures=context_failures,
                        plan=plans.get(identifier))
                elif records.get('review_epoch'):
                    # Legacy staged diagnostics have no assignment identity in the
                    # gate digest. Resolve their controllers only in the reader.
                    if staged_outcomes is None:
                        from .problem_delivery import collect_review_outcomes
                        staged_outcomes = []
                        staged_outcomes = [row for row in collect_review_outcomes(records, artifacts)
                                           if row['epoch'] is not None]
                    identifiers = [row['assignment']['id'] for row in staged_outcomes
                                   if original in row['state'].get('obligations', [])]
                    if identifiers:
                        item['next'] = [action for identifier in identifiers for action in
                            describe_next_actions(records, artifacts, identifier, item.get('next', []),
                                                  context_failures=context_failures, plan=plans.get(identifier))]
            rows.append(item)
        result[key] = rows
    if report.get('preparation'):
        result['preparation'] = annotate_review_obligations(
            records, artifacts, report['preparation'], context_failures=context_failures)
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
                    with _collect_context_failure(context_failures, identifier):
                        result['next'] = dict(upcoming, next=describe_next_actions(
                            records, artifacts, identifier, upcoming.get('next', []),
                            context_failures=context_failures, plan=plans.get(identifier)))
    if context_failures:
        result['review_context_failures'] = list(context_failures)
    return result
