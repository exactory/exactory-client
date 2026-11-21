"""Payload rules and ordered prerequisites for review handoffs and decisions."""

import copy

from . import review_protocol as protocol
from .stop_classes import STOP_REGISTRY, describe_terminal


PLAN_STEPS = {
    1: dict(command='review-assignment', roles=('bar', 'slate', 'result', 'adjudicator'),
            chooses=('id', 'route_id', 'reviewer_id', 'context.context_repair.probe_id',
                     'context.context_repair.reason', 'context.context_repair.event_ids',
                     'context.context_repair.evidence'),
            prerequisites=('Record the cause events.', 'Verify the new route after the latest cause event.')),
    2: dict(command='review-assignment', roles=('slate', 'result'),
            chooses=('id', 'route_id', 'context.context_repair.probe_id', 'context.context_repair.reason'),
            prerequisites=('Verify the new route with a probe recorded after the last bound refusal, correction '
                           'and cause event.',)),
    3: dict(command='review-assignment', roles=('bar', 'adjudicator'),
            chooses=('id', 'route_id', 'reviewer_id'), prerequisites=('Verify an eligible reviewer route.',)),
    4: dict(command='export', roles=('native_math',), chooses=('assignment_id', 'reviewer_id', 'minimum'),
            prerequisites=('Export a current problem-openness packet, accepting its gaps when required.',
                           'Assign its attested reviewer on a verified route.')),
    5: dict(command='search review-packet', roles=('native_math',), chooses=('reviewer', 'inputs'),
            prerequisites=('Meet the math controller review-packet requirements.',
                           'Assign its attested reviewer on a verified route.')),
    6: dict(command='review-assignment', roles=('verification', 'manuscript', 'standalone'),
            chooses=('id', 'route_id', 'reviewer_id'), prerequisites=('Verify the new route.',)),
    7: dict(command='review-route', roles=protocol.ROLES,
            chooses=('id', 'adapter', 'model', 'endpoint', 'configuration'),
            prerequisites=('Install a working reviewer CLI for this route.',)),
    8: dict(command='review-context', roles=('bar', 'slate', 'result', 'adjudicator'),
            chooses=('id', 'reason', 'evidence'), prerequisites=("Retain the owner's instruction as evidence.",)),
    9: dict(command='review-context', roles=protocol.ROLES, chooses=('id', 'reason', 'evidence'),
            prerequisites=("Retain the owner's instruction as evidence.",)),
    10: dict(command='review-stop', roles=protocol.ROLES, chooses=('id', 'reason'),
             prerequisites=('Settle the stopped calls and end the earlier drivers.',)),
    11: dict(command='review-budget', roles=protocol.ROLES, chooses=('max_calls', 'reason'),
             prerequisites=("Obtain the owner's grant of further calls.",)),
    12: dict(command='select-main-capture', roles=('bar', 'slate', 'result'),
             chooses=('version_id', 'source_id', 'reason'), prerequisites=('Capture the original in a size that fits.',)),
    13: dict(command='read', roles=('bar', 'slate', 'result', 'adjudicator'),
             chooses=('id', 'inspections', 'notes'),
             prerequisites=('Acquire a complete full-text capture.', 'Declare its required-unit bundle.')),
    16: dict(command='review-probe', roles=protocol.ROLES, chooses=('id',),
             prerequisites=("Obtain the owner's authorization for the paid capacity probe.",)),
    14: dict(command='review-assignment', roles=protocol.ROLES, chooses=(), prerequisites=()),
    15: dict(command='artifact', roles=protocol.ROLES, chooses=('id', 'path'),
             prerequisites=('Write the complete text of the named body to a file in the workspace.',)),
}


def _build_identity_payload(assignment):
    return {key: copy.deepcopy(assignment.get(key)) for key in
            ('role', 'dossier_id', 'author_id')}


def list_excluded_reviewers(records, assignment, *, include_slot=True):
    """Keep both slots and every earlier reviewer of this slot out of a blind replacement."""
    from .operations import normalized_text
    slot = protocol.context_repair.slot_id(records, assignment)
    reviewers = {normalized_text(row['reviewer_id']) for row in records.get('review_assignment', {}).values()
                 if row['role'] == assignment['role'] and row.get('dossier_id') == assignment.get('dossier_id')
                 and protocol.context_repair.is_same_subject(row, assignment)
                 and not protocol.is_dead_assignment(records, row)
                 and (include_slot or protocol.context_repair.slot_id(records, row) != slot)}
    if assignment['role'] == 'adjudicator':
        reviews = records.get('value_review', {})
        reviewers.update(normalized_text(reviews[key]['reviewer_id'])
                         for key in assignment['context'].get('review_ids', []) if key in reviews)
    return sorted(reviewers)


def describe_step(records, artifacts, assignment, number, *, stop_id=None, reading=None):
    """Fill the known payload and leave reviewer choices explicit."""
    row = PLAN_STEPS[number]
    payload = _build_identity_payload(assignment)
    context = copy.deepcopy(assignment.get('context', {}))
    context.pop('prior_assignment_id', None)
    context.pop('output_refusal', None)
    result = dict(id=number, command=row['command'], chooses=list(row['chooses']),
                  prerequisites=[dict(condition=condition) for condition in row['prerequisites']])
    if number in (1, 2):
        lineage = protocol.context_repair._exposure_lineage(records, assignment)
        events = protocol.context_repair.cause_events(records, {item['id'] for item in lineage})
        binding = dict(assignment_id=assignment['id'], event_ids=[event['id'] for event in events],
                       evidence=[item for event in events for item in event['evidence']])
        if number == 2:
            binding['exhaustion'] = protocol.context_repair.build_exhaustion_binding(records, artifacts, assignment)
            payload['reviewer_id'] = assignment['reviewer_id']
        payload['context'] = dict(context_repair=binding)
        result['excluded_reviewer_ids'] = list_excluded_reviewers(records, assignment, include_slot=False)
    elif number == 3:
        payload['context'] = ({} if assignment['role'] == 'bar' else
                              {key: context[key] for key in ('review_ids', 'objection_id', 'correction')
                               if key in context})
        result.update(slot_id=protocol.context_repair.slot_id(records, assignment),
                      excluded_reviewer_ids=list_excluded_reviewers(records, assignment))
    elif number in (4, 5):
        payload = dict(kind='problem-openness') if number == 4 else {}
        result['then'] = [dict(command='review-assignment',
            payload=_build_identity_payload(assignment),
            chooses=['id', 'route_id', 'reviewer_id', 'context.native_packet'])]
        if number == 5:
            from . import review_parts
            packet = protocol._read_json(artifacts, assignment['packet'])
            if review_parts.find_kind(packet) is not None:
                packet = review_parts.read_logical_packet(artifacts, packet[review_parts.KEY])
            native = packet.get('native', {})
            if packet.get('reading_turn'):
                from .native_review import load_packet
                native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
            payload = {key: copy.deepcopy(native[key]) for key in ('kind', 'subject', 'claim_digest')
                       if key in native}
            result['root'] = native.get('root')
            result['objective_id'] = native.get('objective_id')
            result['checkpoint'] = copy.deepcopy(native.get('scientific_context', {}).get('checkpoint'))
    elif number == 6:
        payload['context'] = context
    elif number == 7:
        route = records.get('review_route', {}).get(assignment['route_id'], {})
        payload = {key: copy.deepcopy(value) for key, value in route.items()
                   if key in ('adapter', 'model', 'endpoint', 'configuration')}
        result['route_id'] = assignment['route_id']
        result['then'] = [dict(command='review-probe', chooses=['id', 'route_id'])]
    elif number in (8, 9):
        result['route_id'] = assignment['route_id']
        payload = dict(assignment_id=assignment['id'], source='owner',
                       kind='route_retired' if number == 8 else 'observation')
    elif number == 10:
        payload = dict(action='release', stop_id=stop_id)
    elif number == 11:
        from .review_budget import read_budget_usage
        payload = {}
        result['usage'] = read_budget_usage(records)
    elif number == 13:
        payload = dict(version_id=reading['version_id'], depth='fulltext')
        captures = reading['captures']
        if len(captures) == 1 and captures[0]['bundle_id'] is not None:
            payload['bundle_id'] = captures[0]['bundle_id']
        else:
            result['chooses'].append('bundle_id')
        result['captures'] = copy.deepcopy(captures)
        result['prerequisites'] = [copy.deepcopy(step) for capture in captures for step in capture['prerequisites']]
    elif number == 16:
        payload = dict(route_id=assignment['route_id'])
    elif number == 14:
        payload = {key: copy.deepcopy(assignment[key]) for key in
                   ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
    elif number == 15:
        payload = dict(media_type='text/plain')
    else:
        payload = {}
    result['payload'] = payload
    return result


CAPACITY_ROUTE_RETIRE_REASON = 'This route cannot deliver the review within its limits. The preserved review continues on a route that can.'


def find_capacity_parent(records, assignment, packet):
    context = assignment.get('context', {})
    parent_id = context.get('prior_assignment_id') or context.get('context_repair', {}).get('assignment_id')
    if parent_id is None and (packet or {}).get('own_bars'):
        parent_id = packet['own_bars'][-1]['assignment_id']
    return records.get('review_assignment', {}).get(parent_id)


def describe_capacity_steps(records, artifacts, assignment, serving_routes, probe_tokens=None, *, body=None, packet=None):
    """Resume a refused first assignment without inventing a slot or a repair event."""
    answers = []
    if body is not None:
        capture = describe_step(records, artifacts, assignment, 15)
        capture['body'] = copy.deepcopy(body)
        resume = describe_step(records, artifacts, assignment, 14)
        resume['chooses'] = ['context']
        resume['prerequisites'].append(dict(condition='Replace the body in the inputs of the review with its stored text form.'))
        answers.append(dict(answer='Capture a text form', steps=[capture, resume]))
    if probe_tokens is not None:
        probe = describe_step(records, artifacts, assignment, 16)
        probe['payload']['capacity'] = dict(input_tokens=probe_tokens)
        answers.append(dict(answer='Probe this route', steps=[probe, describe_step(records, artifacts, assignment, 14)]))
    for route in serving_routes:
        parent = find_capacity_parent(records, assignment, packet)
        if parent is None:
            steps = [describe_step(records, artifacts, dict(assignment, route_id=route['route_id']), 14)]
        else:
            steps = [describe_step(records, artifacts, parent, number)
                     for number in select_route_steps(records, artifacts, parent)]
            serving = records['review_route'][route['route_id']]
            for step in steps:
                if step['id'] == 8:
                    step['payload']['reason'] = CAPACITY_ROUTE_RETIRE_REASON
                    step['chooses'].remove('reason')
                if step['command'] == 'review-route':
                    step['payload'] = {key: copy.deepcopy(serving[key]) for key in
                                       ('adapter', 'model', 'endpoint', 'configuration')}
                if step['id'] == 6:
                    step['payload']['context'].pop('prior_assignment_id', None)
        answers.append(dict(answer='Use a serving route', route_id=route['route_id'], steps=steps))
    return answers


def select_route_steps(records, artifacts, assignment):
    """Choose a first-turn or repair payload that this role admits."""
    role = assignment['role']
    if role == 'native_math':
        from . import openness_reading
        from .native_review import load_packet
        packet = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
        return [4 if openness_reading.is_openness_packet(packet) else 5]
    if role in ('verification', 'manuscript', 'standalone'):
        return [7, 6]
    if role == 'adjudicator' and not (assignment.get('review_parts') or assignment.get('staged_review')):
        return [7, 3]
    # A cause must precede the probe that authenticates the repair.
    return [8, 7, 1]


def describe_route_probe_step(records, artifacts, assignment, route_id):
    """The step that probes this route record."""
    step = describe_step(records, artifacts, dict(assignment, route_id=route_id), 7)
    step.update(command='review-probe', payload=dict(route_id=route_id), chooses=['id'])
    step.pop('then', None)
    return step


def describe_renewal_step(records, artifacts, assignment, renewal):
    """The step that gives the store what a renewal lacks: a route record with the installed runtime and its probe,
    the probe of the route record that the renewal names, or its capacity probe. review-series on the head follows
    it."""
    series = dict(command='review-series', assignment_id=assignment['id'])
    if renewal['lacks'] == 'record':
        step = describe_step(records, artifacts, assignment, 7)
        step['then'].append(series)
        return step
    candidate = dict(assignment, route_id=renewal['route_id'])
    if renewal['lacks'] == 'capacity':
        step = describe_step(records, artifacts, candidate, 16)
        step['payload']['capacity'] = dict(input_tokens=renewal['capacity']['input_tokens'])
    else:
        step = describe_step(records, artifacts, candidate, 7)
        step.update(command='review-probe', payload=dict(route_id=renewal['route_id']), chooses=['id'])
    step['then'] = [series]
    return step


def describe_route_steps(records, artifacts, assignment, numbers, details):
    """The steps of the stale-route procedure for these step numbers, in the order to take them, with
    details.renewal. The step that gives the slot a route record with the installed runtime is step 7 when the
    store holds no such record of the same identity, the probe of that record when it lacks one, and no step when
    the record is verified. A repair needs the probe after its cause events, so the probe follows the retirement."""
    from .review_route_plan import select_current_route
    route = records['review_route'][assignment['route_id']]
    after_revision = 0
    if 1 in numbers:
        lineage = protocol.context_repair._exposure_lineage(records, assignment)
        events = protocol.context_repair.cause_events(records, {item['id'] for item in lineage})
        after_revision = max((event['recorded_revision'] for event in events), default=0)
    found = select_current_route(records, artifacts, route, after_revision=after_revision)
    if 8 in numbers and found['route_id'] is not None:
        # The retirement of step 8 comes after every probe that the store holds.
        found = dict(found, lacks='probe')
    details['renewal'] = dict(details.get('renewal') or {}, admitted=False, **found)
    steps = []
    for number in numbers:
        if number != 7:
            steps.append(describe_step(records, artifacts, assignment, number))
        elif found['route_id'] is None:
            steps.append(describe_step(records, artifacts, assignment, 7))
        elif found['lacks'] == 'probe':
            steps.append(describe_route_probe_step(records, artifacts, assignment, found['route_id']))
    return steps


def awaits_verification(records, artifacts, assignment):
    """Whether this head holds an observed answer whose context is not verified now."""
    state = protocol.assignment_state(records, artifacts, assignment['id'])
    return state.get('output') is not None and any(
        item['code'] == 'review_context_unverified' for item in state['obligations'])


def describe_role_handoff(records, artifacts, assignment, code, details=None):
    """Give each exhausted role its executable preserved-slot handoff."""
    details = copy.deepcopy(details or {})
    exhaustion = code in ('review_slot_exhausted', 'review_refusal_notice_metadata_too_large')
    if assignment['role'] in ('bar', 'adjudicator'):
        numbers = [3]
    elif assignment['role'] in ('slate', 'result') and exhaustion:
        numbers = [2]
    else:
        numbers = select_route_steps(records, artifacts, assignment)
    steps = [describe_step(records, artifacts, assignment, number) for number in numbers]
    details['step'] = steps[0]
    if len(steps) > 1:
        details['steps'] = steps
    if assignment['role'] in ('slate', 'result') and not exhaustion:
        details['decision'] = dict(owner_decides='Whether to retire this route and repair the preserved review slot.',
            then=[dict(answer='Retire the route', steps=steps)])
        return dict(state='stop', code=code, details=details)
    return dict(state='handoff', code=code, command=steps[0]['command'], details=details)


def _describe_session_route_steps(records, artifacts, assignment):
    steps = [describe_step(records, artifacts, assignment, number)
             for number in select_route_steps(records, artifacts, assignment)]
    # Retirement is recorded by an earlier step, so its event and evidence
    # are choices filled from that command's receipt rather than stale bindings.
    steps[-1]['chooses'].extend(('context.context_repair.event_ids', 'context.context_repair.evidence'))
    return steps


def complete_plan(records, artifacts, assignment, plan):
    """Add executable payload rules to external boundaries without changing call payloads."""
    if plan['state'] not in ('stop', 'handoff'):
        return plan
    plan = copy.deepcopy(plan)
    details = plan.setdefault('details', {})
    code = plan.get('code')
    if STOP_REGISTRY.get(code, {}).get('group'):
        terminal = describe_terminal(code)
        details.update(terminal['details'])
        details.pop('step', None)
        details.pop('steps', None)
        plan.update(state='stop', stop_class=terminal['stop_class'])
        plan.pop('command', None)
        plan.pop('payload', None)
        return plan
    if code == 'reading_missing':
        from .review_reading import collect_missing_readings, READING_HANDOFF_MESSAGE
        if 'readings' not in details:
            state = protocol.assignment_state(records, artifacts, assignment['id'])
            effective = state.get('effective') or {}
            output = effective.get('effective_output', state.get('output'))
            details['readings'] = collect_missing_readings(records, artifacts, output)
        steps = [describe_step(records, artifacts, assignment, 13, reading=row) for row in details['readings']]
        details.update(message=READING_HANDOFF_MESSAGE,
                       then=dict(command='review-series', assignment_id=assignment['id']))
        if steps:
            details['step'] = steps[0]
            if len(steps) > 1:
                details['steps'] = steps
        return dict(plan, state='handoff', command='read')
    if (code in ('review_input_too_large', 'review_chain_projection_over_limit')
            and assignment.get('reading_chain')):
        from .openness_successor import describe_successor_handoff
        successor = describe_successor_handoff(records, artifacts, assignment, code, details)
        if successor is not None:
            plan.pop('command', None)
            plan.pop('payload', None)
            return dict(plan, **successor)
        from .openness_reading import describe_projection_decision, find_protocol, PROTOCOL_V7
        packet = protocol._read_json(artifacts, assignment['packet'])
        if find_protocol(packet) == PROTOCOL_V7:
            return plan
        details['decision'] = describe_projection_decision(find_protocol(packet), records=records,
                                                           artifacts=artifacts, assignment=assignment)
        plan['state'] = 'stop'
        plan.pop('command', None)
        return plan
    terminal = describe_terminal(code)
    if code in ('review_slot_exhausted', 'review_refusal_notice_metadata_too_large') and assignment.get('reading_chain'):
        from .openness_successor import describe_successor_handoff
        successor = describe_successor_handoff(records, artifacts, assignment, code, details)
        if successor is not None:
            plan.pop('command', None)
            plan.pop('payload', None)
            return dict(plan, **successor)
    if terminal['details'].get('next', {}).get('kind') == 'role' and plan.get('command') != 'review-output-correction':
        return dict(plan, **describe_role_handoff(records, artifacts, assignment, code, details))
    context_status = assignment.get('context_state', {}).get('context_status')
    head_ran = protocol.describe_assignment_calls(records, assignment)['ran']
    if code == 'review_pending_delivery_over_plan':
        from .review_route_capacity import (SESSION_FORECAST_DECISION, SESSION_CAPTURE_ANSWER,
            SESSION_CAPTURE_REPAIR_ANSWER, SESSION_ROUTE_ANSWER, SESSION_ARTIFACT_DECISION, SESSION_STOP_ANSWER)
        forecast = details['capacity_forecast']
        if forecast['reason'] == 'pending_turns' and any(row.get('sha256') and row.get('pending_parts')
                                                       for row in details['pending_sources']):
            step = dict(command='review-stop', payload=dict(action='stop', mode='now',
                assignment_id=assignment['id']), chooses=['id', 'reason'])
            details.update(step=step, decision=dict(owner_decides=SESSION_ARTIFACT_DECISION,
                then=[dict(answer=SESSION_STOP_ANSWER, steps=[step])]))
            return dict(plan, state='stop', command='review-stop')
        captures = []
        for source in details['pending_sources']:
            if not source.get('pending_parts') or 'version_id' not in source:
                continue
            step = describe_step(records, artifacts, assignment, 12)
            step['payload'] = dict(version_id=source['version_id'])
            step['chooses'].remove('version_id')
            captures.append(step)
        has_captures = bool(captures)
        packet = protocol._read_json(artifacts, assignment['packet'])
        keeps_source_delivery = any(row.get('request', {}).get('kind') == 'source'
            and row['request'].get('depth') == 'fulltext' for row in packet.get('source_deliveries', []))
        if not head_ran or keeps_source_delivery or not forecast['request']['fits']:
            # A selected capture cannot replace bytes frozen in this head or its history.
            captures.extend(_describe_session_route_steps(records, artifacts, assignment))
            capture_answer = SESSION_CAPTURE_REPAIR_ANSWER
        else:
            captures.append(dict(command='review-series', assignment_id=assignment['id']))
            capture_answer = SESSION_CAPTURE_ANSWER
        answers = [dict(answer=capture_answer, steps=captures)] if has_captures else []
        if details['capacity_forecast']['reason'] == 'projected_input':
            answers.append(dict(answer=SESSION_ROUTE_ANSWER,
                                steps=_describe_session_route_steps(records, artifacts, assignment)))
        details['decision'] = dict(owner_decides=SESSION_FORECAST_DECISION, then=answers)
        details['step'] = answers[0]['steps'][0]
        plan.update(state='stop', command=details['step']['command'])
    elif code in ('native_review_attestation_required', 'openness_review_not_approved'):
        number = select_route_steps(records, artifacts, assignment)[-1]
        details['step'] = describe_step(records, artifacts, assignment, number)
        plan.update(state='handoff', command=details['step']['command'])
    elif code == 'review_context_unverified' and head_ran and context_status not in (None, 'verified_for_route'):
        plan['state'] = 'stop'
    elif code in ('review_context_contaminated', 'review_route_retired', 'review_context_repair_required',
                  'review_context_repair_stale', 'review_context_repair_mismatch', 'review_context_repair_unverified'):
        if code == 'review_context_repair_stale':
            repairs = [row for row in protocol.context_repair._lineage(records, assignment)
                       if row.get('context', {}).get('context_repair') is not None]
            binding = repairs[-1]['context']['context_repair'] if repairs else {}
            if binding.get('exhaustion') is not None:
                predecessor = records['review_assignment'][binding['assignment_id']]
                details['step'] = describe_step(records, artifacts, predecessor, 2)
                details['step']['payload']['context']['context_repair']['exhaustion'] = copy.deepcopy(
                    binding['exhaustion'])
                return dict(plan, state='handoff', command='review-assignment')
        steps = select_route_steps(records, artifacts, assignment)
        # An existing cause event already meets retirement's prerequisite.
        steps = [number for number in steps if number != 8]
        number = steps[-1] if steps[-1] in (4, 5, 6) else 1
        from .review_route_plan import read_route_standing
        if code == 'review_route_retired' and read_route_standing(records, artifacts, assignment['route_id'])['stale']:
            # The retired route record is stale: the slot continues on a record of the same identity with the
            # installed runtime, verified after the retirement.
            details['steps'] = describe_route_steps(records, artifacts, assignment, [7, number], details)
            details['step'] = details['steps'][0]
            if len(details['steps']) == 1:
                details.pop('steps')
        else:
            details['step'] = describe_step(records, artifacts, assignment, number)
        plan.update(state='handoff', command=details['step']['command'])
    elif code == 'review_route_runtime_stale' and details.get('renewal', {}).get('admitted') is False:
        # A head whose next step cannot move to another route record: the steps of the stale-route procedure for
        # its role, in the order to take them.
        from .review_route_plan import STALE_ROUTE_ANSWER_MSG, STALE_ROUTE_PLAN_MSG
        numbers = select_route_steps(records, artifacts, assignment)
        if numbers in ([4], [5]):
            numbers = [7] + numbers
        if 8 in numbers and awaits_verification(records, artifacts, assignment):
            # The retirement can end the unverified state of an observed answer, so the plan after it names what
            # follows.
            details['steps'] = [describe_step(records, artifacts, assignment, 8),
                                dict(command='review-series', assignment_id=assignment['id'])]
            details.setdefault('message', STALE_ROUTE_ANSWER_MSG)
        else:
            details['steps'] = describe_route_steps(records, artifacts, assignment, numbers, details)
            details.setdefault('message', STALE_ROUTE_PLAN_MSG)
        details['step'] = details['steps'][0]
        plan.update(state='handoff', command=details['step']['command'])
    elif (code == 'review_route_runtime_stale'
          and details.get('renewal', {}).get('lacks') in ('record', 'probe', 'capacity')):
        # The store lacks the route record of a renewal, its probe or its capacity evidence; review-series then
        # names the work that runs on that record.
        details['step'] = describe_renewal_step(records, artifacts, assignment, details['renewal'])
        plan.update(state='handoff', command=details['step']['command'])
    elif code in ('review_runtime_missing', 'review_route_runtime_stale') or (
            code == 'review_context_unverified' and plan['state'] == 'handoff'):
        details['step'] = describe_step(records, artifacts, assignment, 7)
        if plan.get('not_before') is not None:
            details['step']['prerequisites'].append(dict(not_before=plan['not_before']))
        plan.update(state='handoff', command='review-probe' if code != 'review_route_runtime_stale' else 'review-route')
        if plan['command'] == 'review-probe':
            # The probe that ends an unverified context is the probe of the route record that causes it: the record
            # of an accepted output correction when that one is not verified, otherwise the route of the assignment.
            route_id = assignment['route_id']
            if code == 'review_context_unverified':
                from .review_route_plan import find_unverified_route
                route_id = find_unverified_route(records, artifacts, assignment)
            prerequisites = details['step']['prerequisites']
            details['step'] = describe_route_probe_step(records, artifacts, assignment, route_id)
            details['step']['prerequisites'] = prerequisites
    elif code == 'review_route_capacity_below_floor':
        details['step'] = describe_step(records, artifacts, assignment, 7)
        plan.update(state='handoff', command='review-route')
    elif code == 'review_route_capacity_plan_violated':
        plan.update(state='stop', stop_class='harness_defect', command='gate-auto-pass')
        details['step'] = dict(command='gate-auto-pass', payload=dict(stop_id=details.get('stop_id')),
            chooses=[] if details.get('stop_id') else ['stop_id'],
            prerequisites=[dict(condition='Retain the harness stop with the exact request and pinned capacity plan.')])
    elif code == 'review_chain_stopped':
        details['step'] = describe_step(records, artifacts, assignment, 10, stop_id=details.get('stop_id'))
        plan.update(state='handoff', command='review-stop')
    if plan['state'] == 'stop':
        recovery = details.get('recovery', {})
        if recovery.get('kind') == 'decision':
            details['decision'] = {key: copy.deepcopy(recovery[key]) for key in ('owner_decides', 'then')}
            return plan
        if (code in ('review_turn_failures_exhausted', 'review_output_over_cap', 'review_input_too_large',
                     'review_timeout', 'review_format_unsupported', 'review_context_unverified',
                     'review_response_too_large')
                or details.get('recovery', {}).get('kind') == 'decision'):
            numbers = select_route_steps(records, artifacts, assignment)
            if numbers == [4]:
                numbers = [7, 4]
            steps = [describe_step(records, artifacts, assignment, number) for number in numbers]
            from .review_failure_class import ROUTE_DECISION_QUESTION
            question = ('Whether this route still serves this slot or chain.' if code == 'review_turn_failures_exhausted'
                        else 'Whether this slot or chain moves to a serving route on another adapter.'
                        if code == 'review_response_too_large'
                        else ROUTE_DECISION_QUESTION)
            answers = [dict(answer='Move to another route', steps=steps)]
            if code == 'review_turn_failures_exhausted':
                answers.append(dict(answer='Keep this route', steps=[
                    describe_step(records, artifacts, assignment, 9),
                    dict(command='review-series', assignment_id=assignment['id'])]))
            if code == 'review_turn_failures_exhausted' and details.get('outcome') == 'provider_refused':
                from .openness_reading import find_protocol, PROTOCOL_V5, PROTOCOL_V6
                packet = protocol._read_json(artifacts, assignment['packet'])
                if find_protocol(packet) in (PROTOCOL_V5, PROTOCOL_V6):
                    from .openness_successor import OUTPUT_CAP_EXPORT_MESSAGE, describe_successor_handoff
                    successor = describe_successor_handoff(records, artifacts, assignment, code)
                    if successor is not None:
                        details['successor'] = successor['details']['successor']
                        step = successor['details'].get('step')
                        if step is not None:
                            details['row'] = 4
                            answers.append(dict(answer=OUTPUT_CAP_EXPORT_MESSAGE, steps=[step]))
            details['decision'] = dict(owner_decides=question, then=answers)
            if code == 'review_response_too_large':
                from .review_route_capacity import find_serving_routes
                packet = protocol._read_json(artifacts, assignment['packet'])
                choices = find_serving_routes(records, assignment['role'], packet, artifacts=artifacts,
                    assignment=assignment, prompt=assignment.get('prompt', {}).get('text', ''))
                details['serving_routes'] = [row['route_id'] for row in choices
                    if records['review_route'][row['route_id']]['adapter'] !=
                    records['review_route'][assignment['route_id']]['adapter']]
                details['route_availability'] = ('Registered text routes serve this review.' if details['serving_routes']
                    else 'No registered route serves this review.')
        elif code == 'review_budget_exhausted':
            details['decision'] = dict(owner_decides='Whether to grant more reviewer calls.',
                then=[dict(answer='Grant more calls', steps=[describe_step(records, artifacts, assignment, 11)])])
        else:
            terminal = describe_terminal(code)
            details.setdefault('decision', terminal['details'].get('decision', dict(
                owner_decides='How should the retained review record be repaired?',
                then=[dict(answer='Inspect the record', steps=[dict(command='record-check')])])))
    elif 'step' not in details and 'payload' in terminal['details'].get('next', {}):
        rule = terminal['details']['next']
        details['step'] = dict(id=plan['command'], command=plan['command'],
            payload=copy.deepcopy(rule['payload']), chooses=copy.deepcopy(rule['chooses']),
            prerequisites=copy.deepcopy(rule.get('prerequisites', [])))
    elif 'step' not in details:
        # Existing recording boundaries retain their command-specific input choices.
        command = plan.get('command')
        payload = dict(assignment_id=assignment['id'])
        chooses = ['command inputs']
        if command == 'review-output-correction':
            payload['route_id'] = details.get('correction_route_id', assignment['route_id'])
            chooses = ['id']
        elif command == 'search':
            payload = dict(profile='research', purpose='direct')
            chooses = ['id', 'question', 'scope', 'queries', 'responses', 'captured_at',
                       'found_work_ids', 'verdict', 'cited_work_ids', 'impact', 'gaps', 'dispositions']
        elif command == 'search accept':
            payload = {}
            chooses = ['target', 'acceptance', 'digest']
        elif command == 'bind-verdict':
            payload = dict(task_digest=assignment['context']['verification_task_id'])
            chooses = ['id', 'body', 'assessment']
        elif command == 'manuscript-review':
            bundle = assignment['context'].get('bundle', {})
            payload = dict(bundle_digest=bundle['digest']) if 'digest' in bundle else {}
            chooses = ['id', 'assessor', 'review', 'prediction', 'reasons']
        elif command == 'export':
            payload = dict(kind=details.get('kind', 'outputs'))
            chooses = ['destination']
        elif command == 'fulltext':
            payload = {key: details[key] for key in ('version_id',) if key in details}
            chooses = [key for key in ('version_id',) if key not in payload]
        elif command == 'review-attempt' and details.get('next'):
            payload = copy.deepcopy(details['next'].get('payload', {}))
            chooses = []
        details['step'] = dict(id=command, command=command, payload=payload, chooses=chooses,
                              prerequisites=[dict(condition='Meet the named command requirements.')])
    return plan
