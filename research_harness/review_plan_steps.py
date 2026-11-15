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
            prerequisites=('An exhaustion binding is supported by the installed release.',
                           'Verify the new route after the last bound refusal and event.')),
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
    result = dict(id=number, command=row['command'], chooses=list(row['chooses']),
                  prerequisites=[dict(condition=condition) for condition in row['prerequisites']])
    if number in (1, 2):
        lineage = protocol.context_repair._exposure_lineage(records, assignment)
        events = protocol.context_repair.cause_events(records, {item['id'] for item in lineage})
        binding = dict(assignment_id=assignment['id'], event_ids=[event['id'] for event in events],
                       evidence=[item for event in events for item in event['evidence']])
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
    else:
        payload = {}
    result['payload'] = payload
    return result


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


def describe_role_handoff(records, artifacts, assignment, code, details=None):
    """Describe exhaustion without inventing an event-free repair before that unit ships."""
    details = copy.deepcopy(details or {})
    if assignment['role'] in ('bar', 'adjudicator'):
        numbers = [3]
    else:
        numbers = select_route_steps(records, artifacts, assignment)
    steps = [describe_step(records, artifacts, assignment, number) for number in numbers]
    details['step'] = steps[0]
    if len(steps) > 1:
        details['steps'] = steps
    if assignment['role'] in ('slate', 'result'):
        details['decision'] = dict(owner_decides='Whether to retire this route and repair the preserved review slot.',
            then=[dict(answer='Retire the route', steps=steps)])
        return dict(state='stop', code=code, details=details)
    return dict(state='handoff', code=code, command=steps[0]['command'], details=details)


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
        # Reading-v7 changes the problem-chain row to export when it can carry these verified turns.
        from .openness_reading import describe_projection_decision, find_protocol
        packet = protocol._read_json(artifacts, assignment['packet'])
        details['decision'] = describe_projection_decision(find_protocol(packet), records=records,
                                                           artifacts=artifacts, assignment=assignment)
        plan['state'] = 'stop'
        plan.pop('command', None)
        return plan
    terminal = describe_terminal(code)
    if terminal['details'].get('next', {}).get('kind') == 'role' and plan.get('command') != 'review-output-correction':
        return dict(plan, **describe_role_handoff(records, artifacts, assignment, code, details))
    context_status = assignment.get('context_state', {}).get('context_status')
    head_ran = protocol.describe_assignment_calls(records, assignment)['ran']
    if code in ('native_review_attestation_required', 'openness_review_not_approved'):
        number = select_route_steps(records, artifacts, assignment)[-1]
        details['step'] = describe_step(records, artifacts, assignment, number)
        plan.update(state='handoff', command=details['step']['command'])
    elif code == 'review_context_unverified' and head_ran and context_status not in (None, 'verified_for_route'):
        plan['state'] = 'stop'
    elif code in ('review_context_contaminated', 'review_route_retired', 'review_context_repair_required',
                  'review_context_repair_stale', 'review_context_repair_mismatch', 'review_context_repair_unverified'):
        steps = select_route_steps(records, artifacts, assignment)
        # An existing cause event already meets retirement's prerequisite.
        steps = [number for number in steps if number != 8]
        number = steps[-1] if steps[-1] in (4, 5, 6) else 1
        details['step'] = describe_step(records, artifacts, assignment, number)
        plan.update(state='handoff', command=details['step']['command'])
    elif code in ('review_runtime_missing', 'review_route_runtime_stale') or (
            code == 'review_context_unverified' and plan['state'] == 'handoff'):
        details['step'] = describe_step(records, artifacts, assignment, 7)
        if plan.get('not_before') is not None:
            details['step']['prerequisites'].append(dict(not_before=plan['not_before']))
        plan.update(state='handoff', command='review-probe' if code != 'review_route_runtime_stale' else 'review-route')
        if plan['command'] == 'review-probe':
            details['step'].update(command='review-probe', payload=dict(route_id=assignment['route_id']),
                                   chooses=['id'])
            details['step'].pop('then', None)
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
            payload['route_id'] = assignment['route_id']
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
