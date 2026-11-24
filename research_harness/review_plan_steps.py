"""Payload rules and ordered prerequisites for review handoffs and decisions."""

import copy

from . import review_protocol as protocol
from .stop_classes import STOP_REGISTRY, describe_terminal


PLAN_STEPS = {
    1: dict(command='review-assignment', roles=('bar', 'slate', 'result', 'adjudicator'),
            chooses=('id', 'route_id', 'reviewer_id', 'context.context_repair.probe_id',
                     'context.context_repair.reason', 'context.context_repair.event_ids',
                     'context.context_repair.evidence'),
            prerequisites=('Record the cause events.',
                           'Verify the route record of the repair with a probe that is recorded after the latest '
                           'cause event.')),
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


def describe_step(records, artifacts, assignment, number, *, stop_id=None, reading=None, repair_events=None,
                  open_minimum=False, checked_export=False):
    """Fill the known payload and leave reviewer choices explicit."""
    row = PLAN_STEPS[number]
    payload = _build_identity_payload(assignment)
    context = copy.deepcopy(assignment.get('context', {}))
    context.pop('prior_assignment_id', None)
    context.pop('output_refusal', None)
    result = dict(id=number, command=row['command'], chooses=list(row['chooses']),
                  prerequisites=[dict(condition=condition) for condition in row['prerequisites']])
    if number in (1, 2):
        events = repair_events
        if events is None:
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
            from .openness_successor import MATH_PROTOCOLS, bind_math_export
            packet = protocol._read_json(artifacts, assignment['packet'])
            reading_protocol = protocol.openness_reading.find_protocol(packet)
            math_chain = reading_protocol in MATH_PROTOCOLS
            if review_parts.find_kind(packet) is not None:
                packet = review_parts.read_logical_packet(artifacts, packet[review_parts.KEY])
            native = packet.get('native', {})
            if math_chain or packet.get('reading_turn'):
                from .native_review import load_packet
                native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
            if math_chain:
                result['payload'] = payload
                bind_math_export(result, native, assignment['reviewer_id'], records,
                                 fresh_attestation=reading_protocol == protocol.openness_reading.MATH_PROTOCOL_V2,
                                 open_minimum=open_minimum, checked_export=checked_export)
            else:
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


CAPACITY_ROUTE_RETIRE_REASON = 'This route is retired so that the retained review is reassessed on a serving route.'
CAPACITY_PROBE_RETRY_CONDITION = (
    'The harness measured the reassessment on this route record, and the reassessment needs a probe of the record '
    'that is recorded after its cause event. The steps end at that probe. Then run the stopped command again: when '
    'the probe verifies enough capacity, the next stop names the reassessment.')
CAPACITY_REPAIR_CONDITION = (
    'The harness measured this reassessment before the members that the step leaves to you were chosen. The store '
    'records it when the completed assignment fits this route record.')
CAPACITY_RETIRE_REPAIR_CONDITION = (
    'The harness measured the reassessment on this route record, and the reassessment needs a cause event and a '
    'probe that is recorded after it. The steps retire the route record of the retained review, which gives the '
    'cause event, and end at the probe. Then run the stopped command again: when the probe verifies enough '
    'capacity, the next stop names the reassessment.')
CAPACITY_NEW_ENTRY_CONDITION = (
    'The harness measured the review on the route record that route_id of this answer names. The steps record a '
    'new route record with the same adapter, model, endpoint and configuration and start the review there with a '
    'new assignment. The store records that assignment when the new route record is verified and the assignment '
    'fits it.')
CAPACITY_CAPTURE_CONDITION = ('In the context of the assignment, put the text artifact that the first step stores in '
                              'the place of the body that the member body of that step names.')
CAPACITY_REASSESS_ANSWER = 'Reassess the retained review'
CAPACITY_NEW_ENTRY_ANSWER = 'Prepare a new entry'
# The conditions of the answer "Probe this route": what follows the probe for a stopped command without a bound repair,
# and for a stopped repair, whose command binds an earlier probe.
CAPACITY_PROBE_CONDITION = (
    'The steps end at the probe. Then run the stopped command again: when the probe verifies enough capacity, the '
    'store records the assignment; otherwise the next stop names the next step.')
CAPACITY_PROBE_REPAIR_CONDITION = (
    'The steps end at the probe. The stopped command binds an earlier probe, so the store refuses it again. Then run '
    'review-series on the review that context.context_repair.assignment_id of the stopped command names: its plan '
    'names the repair, which can bind the new probe.')


def find_capacity_parent(records, assignment, packet):
    context = assignment.get('context', {})
    if 'session_refresh' in context:
        # Rule F5: a refresh is a new chain and replays no turn of a predecessor, so it has no capacity parent.
        return None
    parent_id = context.get('prior_assignment_id') or context.get('context_repair', {}).get('assignment_id')
    if parent_id is None and (packet or {}).get('own_bars'):
        parent_id = packet['own_bars'][-1]['assignment_id']
    return records.get('review_assignment', {}).get(parent_id)


def find_capacity_repair_binding(assignment, parent):
    """Read the stopped payload's repair binding when it is for this predecessor."""
    binding = assignment.get('context', {}).get('context_repair', {})
    return binding if binding.get('assignment_id') == parent['id'] else {}


def describe_capacity_repair(records, artifacts, assignment, packet):
    """Read the cause and binding once for this stop, including an empty result."""
    from . import review_context_repair
    parent = find_capacity_parent(records, assignment, packet)
    numbers = select_route_steps(records, artifacts, parent) if parent is not None else []
    lineage, events, cause = [], [], None
    if 1 in numbers:
        lineage = review_context_repair._exposure_lineage(records, parent)
        identifiers, _ = review_context_repair.find_repair_scope(records, lineage)
        events = review_context_repair.cause_events(records, identifiers)
        binding = find_capacity_repair_binding(assignment, parent)
        cause = review_context_repair.find_repair_cause(records, artifacts, parent,
                                                         exhaustion=binding.get('exhaustion'), events=events)
    return dict(parent=parent, numbers=numbers, lineage=lineage, events=events, cause=cause, probes={})


def find_capacity_repair_probe(records, state, cause):
    """Return the verified probe when it follows the bound events."""
    if cause is not None and state['context_status'] == 'verified_for_route':
        probe = records['review_route_probe'][state['probe_id']]
        if probe['recorded_revision'] > cause['after_revision']:
            return state['probe_id']
    return None


def reconcile_capacity_routes(serving_routes, repair_plan):
    """Keep the direct entry when another answer would retire its route."""
    parent = repair_plan['parent']
    if parent is None or 1 not in repair_plan['numbers']:
        return serving_routes
    if repair_plan['cause'] is None and any(
            row['route_id'] == parent['route_id'] and row.get('delivery') in ('single', 'parts', 'session')
            for row in serving_routes):
        return [row for row in serving_routes if row.get('delivery') in ('single', 'parts', 'session')]
    return serving_routes


def describe_capacity_assignment(records, artifacts, assignment, route_id, *, repair_probe=None):
    """Assign the stopped payload on this route, choosing the new probe for a repair when needed."""
    step = describe_step(records, artifacts, dict(assignment, route_id=route_id), 14)
    if 'scheduler' in assignment:
        step['payload']['scheduler'] = copy.deepcopy(assignment['scheduler'])
    binding = step['payload']['context'].get('context_repair')
    if binding is not None:
        step['payload']['context'] = dict(context_repair=binding)
        if repair_probe is None:
            binding.pop('probe_id', None)
            step['chooses'].append('context.context_repair.probe_id')
        else:
            binding['probe_id'] = repair_probe
    return step


def describe_serving_repair_steps(records, artifacts, assignment, route, repair_plan):
    """Move the preserved slot to a serving record after the required cause and probe."""
    parent, cause = repair_plan['parent'], repair_plan['cause']
    route_id = route['route_id']
    steps = []
    probe_id = None
    if cause is None:
        retirement = describe_step(records, artifacts, parent, 8)
        retirement['payload']['reason'] = CAPACITY_ROUTE_RETIRE_REASON
        retirement['chooses'].remove('reason')
        steps.append(retirement)
    elif route_id in repair_plan['probes']:
        probe_id = repair_plan['probes'][route_id]
    else:
        probe_id = find_capacity_repair_probe(records, protocol.route_state(records, artifacts, route_id), cause)
    if probe_id is None:
        steps.append(describe_route_probe_step(records, artifacts, parent, route_id))
    if find_capacity_repair_binding(assignment, parent):
        repair = describe_capacity_assignment(records, artifacts, assignment, route_id, repair_probe=probe_id)
    else:
        repair = describe_step(records, artifacts, parent, 1, repair_events=repair_plan['events'])
        repair['payload']['route_id'] = route_id
        repair['chooses'].remove('route_id')
        if probe_id is not None:
            repair['payload']['context']['context_repair']['probe_id'] = probe_id
            repair['chooses'].remove('context.context_repair.probe_id')
    steps.append(repair)
    return steps


def describe_capacity_steps(records, artifacts, assignment, serving_routes, probe_tokens=None, *, body=None,
                            packet=None, series_body=False, repair_plan=None, public_assignment=None):
    """Resume a refused first assignment without inventing a slot or a repair event."""
    answers = []
    if public_assignment is None:
        public_assignment = assignment
    if repair_plan is None:
        repair_plan = describe_capacity_repair(records, artifacts, assignment, packet) if serving_routes else {
            'parent': None, 'numbers': [], 'cause': None}
    conditional = repair_plan.get('conditional_routes', [])
    choices = reconcile_capacity_routes(serving_routes + conditional, repair_plan)
    parent, numbers = repair_plan['parent'], repair_plan['numbers']
    if body is not None:
        capture = describe_step(records, artifacts, assignment, 15)
        capture['body'] = copy.deepcopy(body)
        if series_body:
            # The slate planner reads the text form under its fixed ID; the next assignment is checked once it exists.
            from .review_parts_slate import derive_text_capture_id
            capture['payload'].update(id=derive_text_capture_id(body), text_form_of=copy.deepcopy(body))
            capture['chooses'].remove('id')
            answers.append(dict(answer='Capture a text form', steps=[capture]))
        else:
            resume = describe_capacity_assignment(records, artifacts, public_assignment, assignment['route_id'])
            resume['chooses'] = ['context']
            resume['prerequisites'].append(dict(condition=CAPACITY_CAPTURE_CONDITION))
            answers.append(dict(answer='Capture a text form', condition=CAPACITY_CAPTURE_CONDITION, steps=[capture, resume]))
    retires_original = (parent is not None and 1 in numbers and repair_plan['cause'] is None
                        and parent['route_id'] == assignment['route_id']
                        and any(row.get('delivery') == 'reassessment' for row in choices))
    if probe_tokens is not None and not retires_original:
        probe = describe_step(records, artifacts, assignment, 16)
        probe['payload']['capacity'] = dict(input_tokens=probe_tokens)
        # The assignment that follows depends on the result of the probe; the condition names the command that plans it.
        bound_repair = parent is not None and bool(find_capacity_repair_binding(assignment, parent))
        answers.append(dict(answer='Probe this route', steps=[probe],
                            condition=CAPACITY_PROBE_REPAIR_CONDITION if bound_repair else CAPACITY_PROBE_CONDITION))
    for route in choices:
        probe_at = None
        if parent is None or (route.get('delivery') in ('single', 'parts', 'session') and route not in conditional):
            steps = [describe_capacity_assignment(records, artifacts, public_assignment, route['route_id'])]
        elif 1 in numbers:
            # The reassessment ends with its first probe: the next stop of the same payload names the repair.
            steps = describe_serving_repair_steps(records, artifacts, public_assignment, route, repair_plan)
            probe_at = next((index for index, step in enumerate(steps) if step['command'] == 'review-probe'), None)
            if probe_at is not None:
                steps = steps[:probe_at + 1]
        else:
            steps = describe_new_entry_steps(records, artifacts, parent, numbers, route['route_id'])
        answer = dict(answer='Use a serving route', route_id=route['route_id'], steps=steps)
        if route in conditional:
            if route['delivery'] == 'reassessment':
                condition = (CAPACITY_RETIRE_REPAIR_CONDITION if repair_plan['cause'] is None
                             else CAPACITY_PROBE_RETRY_CONDITION if probe_at is not None else CAPACITY_REPAIR_CONDITION)
                answer['answer'] = CAPACITY_REASSESS_ANSWER
            else:
                condition = CAPACITY_NEW_ENTRY_CONDITION
                answer['answer'] = CAPACITY_NEW_ENTRY_ANSWER
            answer['condition'] = condition
            if probe_at is None:
                steps[-1]['prerequisites'].append(dict(condition=condition))
        answers.append(answer)
    return answers


def describe_new_entry_steps(records, artifacts, parent, numbers, route_id):
    """The role steps that move the review of parent to a new route record with the configuration of route_id."""
    steps = [describe_step(records, artifacts, parent, number) for number in numbers]
    serving = records['review_route'][route_id]
    for step in steps:
        if step['command'] == 'review-route':
            step['payload'] = {key: copy.deepcopy(serving[key]) for key in
                               ('adapter', 'model', 'endpoint', 'configuration')}
        if step['id'] == 6:
            step['payload']['context'].pop('prior_assignment_id', None)
    return steps


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


def find_unserved_repair_routes(records, artifacts, assignment, step):
    """The verified routes after the cause events when none of them serves the repair of a recorded v3 series."""
    from . import review_parts
    from .review_route_capacity import find_serving_routes
    protocols, recorded = review_parts.select_series_protocols(records, artifacts, {}, head=assignment)
    if not recorded or protocols != ('exactory-review-parts-v3',):
        return None
    lineage = protocol.context_repair._exposure_lineage(records, assignment)
    events = protocol.context_repair.cause_events(records, {item['id'] for item in lineage})
    after_revision = max((event['recorded_revision'] for event in events), default=0)
    exhaustion = step['payload']['context']['context_repair'].get('exhaustion')
    if exhaustion is not None:
        # The probe of an exhaustion repair follows the last bound refusal, correction and cause event.
        cause = protocol.context_repair.find_repair_cause(records, artifacts, assignment, exhaustion=exhaustion,
                                                          events=events)
        after_revision = cause['after_revision']
    retired = protocol.retired_routes(records)
    eligible = []
    for route_id in sorted(records.get('review_route', {})):
        state = protocol.route_state(records, artifacts, route_id)
        probe = records.get('review_route_probe', {}).get(state.get('probe_id'))
        if (route_id not in retired and state['context_status'] == 'verified_for_route'
                and probe is not None and probe['recorded_revision'] > after_revision):
            eligible.append(route_id)
    if not eligible:
        return None
    prospect = dict(step['payload'], id=assignment['id'] + '-repair')
    if 'subject' in assignment:
        prospect['subject'] = assignment['subject']
    serving = find_serving_routes(records, assignment['role'], protocol._read_json(artifacts, assignment['packet']),
                                  artifacts=artifacts, assignment=prospect, prompt=assignment['prompt'])
    if any(row['route_id'] in eligible and row['delivery'] == 'reassessment' for row in serving):
        return None
    return eligible


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


def describe_route_steps(records, artifacts, assignment, numbers, details, *, repair_cause=None):
    """The steps of the stale-route procedure for these step numbers, in the order to take them, with
    details.renewal. The step that gives the slot a route record with the installed runtime is step 7 when the
    store holds no such record of the same identity, the probe of that record when it lacks one, and no step when
    the record is verified. A repair needs the probe after its cause events, so the probe follows the retirement."""
    from .review_route_plan import select_current_route
    route = records['review_route'][assignment['route_id']]
    after_revision = 0
    if repair_cause is not None:
        after_revision = repair_cause['after_revision']
    elif 1 in numbers:
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
            steps.append(describe_step(records, artifacts, assignment, number,
                                       repair_events=repair_cause['events'] if repair_cause else None))
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
    handoff = dict(state='handoff', code=code, command=steps[0]['command'], details=details)
    stop = _plan_unserved_repair(records, artifacts, assignment, handoff) if numbers == [2] else None
    return handoff if stop is None else stop


def _describe_session_route_steps(records, artifacts, assignment):
    steps = [describe_step(records, artifacts, assignment, number)
             for number in select_route_steps(records, artifacts, assignment)]
    # Retirement is recorded by an earlier step, so its event and evidence
    # are choices filled from that command's receipt rather than stale bindings.
    steps[-1]['chooses'].extend(('context.context_repair.event_ids', 'context.context_repair.evidence'))
    return steps


REFRESH_ROUTE_CHOICE = (
    'Choose a route record that is verified, is not retired, pins the installed runtime and is no visual route, '
    'and set route_id to its id. When review-assignment refuses the choice, do what that refusal states.')


def _plan_unserved_repair(records, artifacts, assignment, plan):
    """Give the capacity decision in place of the repair step of a recorded v3 series that no verified route serves."""
    details = plan['details']
    unserved = find_unserved_repair_routes(records, artifacts, assignment, details['step'])
    if unserved is None:
        return None
    # No verified route can take the repair that the recorded v3 series keeps: the owner moves the slot to a
    # route that can, before any repair is offered.
    from .review_route_capacity import describe_capacity_decision
    decision = describe_capacity_decision(records, artifacts, assignment, 'input_over_limit',
        dict(details, refusal='review_route_capacity_below_floor', unserved_route_ids=unserved))
    for answer in decision['then']:
        # After the route steps, an answer names the repair that this stop replaces (step 1 or 2).
        answer['steps'] = [copy.deepcopy(details['step']) if step.get('id') == 1 else step
                           for step in answer['steps'] if step.get('id') != 8]
    chosen = {key: decision[key] for key in ('owner_decides', 'then')}
    # The recovery member keeps the decision when a caller completes the plan again (a context handoff is
    # completed where it is planned and again by plan_next).
    details.update(decision['details'], decision=chosen, recovery=dict(chosen, kind='decision'))
    # The decision was built from the details of the refused repair; the stop names only the answers.
    details.pop('step')
    plan.pop('command', None)
    return dict(plan, state='stop', code=decision['code'])


def _complete_context_handoff(records, artifacts, assignment, plan):
    """Choose the step for the named turn of a context refusal or handoff."""
    details = plan['details']
    if 'step' in details:
        return dict(plan, command=details['step']['command'])
    lost = details['turn']
    route_id = lost['route']['id']
    retire = False
    if not lost['events']:
        from .review_route_plan import read_route_standing
        standing = read_route_standing(records, artifacts, route_id)
        if not standing['stale']:
            details['step'] = describe_route_probe_step(records, artifacts, assignment, route_id)
            if plan.get('not_before') is not None:
                details['step']['prerequisites'].append(dict(not_before=plan['not_before']))
            return dict(plan, command='review-probe')
        retire = not standing['retired']

    if not retire:
        number = select_route_steps(records, artifacts, assignment)[-1]
        if number == 1:
            lineage = protocol.context_repair._exposure_lineage(records, assignment)
            causes = protocol.context_repair.cause_events(records, {turn['id'] for turn in lineage})
            if not causes:
                # A retirement on another slot can leave this route's proof unverified and this repair unbound.
                retire = protocol.is_contaminated_context(lost) or (
                    protocol.find_answered_route_status(records, artifacts, route_id) != 'verified_for_route')
        if not retire:
            details['step'] = describe_step(records, artifacts, assignment, number)
            stop = _plan_unserved_repair(records, artifacts, assignment, plan) if number == 1 else None
            if stop is not None:
                return stop
    if retire:
        turn = records['review_assignment'][lost['assignment_id']]
        details['step'] = describe_step(records, artifacts, turn, 8)
        details['steps'] = [details['step'], dict(command='review-series', assignment_id=assignment['id'])]
    return dict(plan, command=details['step']['command'])


def complete_plan(records, artifacts, assignment, plan):
    """Add executable payload rules to external boundaries without changing call payloads."""
    if (plan['state'] not in ('stop', 'handoff')
            or (plan['state'] == 'stop' and plan.get('command') == 'review-stop' and 'payload' in plan
                and assignment and assignment.get('session_chain'))):
        return plan
    plan = copy.deepcopy(plan)
    details = plan.setdefault('details', {})
    code = plan.get('code')
    if (plan['state'] == 'handoff' and 'turn' in details
            and code in ('review_context_unverified', 'review_context_contaminated')):
        return _complete_context_handoff(records, artifacts, assignment, plan)
    repair_source = (code == 'review_source_continuation_unverified'
                     and plan.get('command') == 'review-assignment' and plan['state'] == 'handoff')
    if STOP_REGISTRY.get(code, {}).get('group') and not repair_source:
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
    elif code == 'review_route_retired' and (
            (plan.get('payload') or {}).get('context', {}).get('session_refresh')
            or details.get('step', {}).get('id') == 14):
        if 'payload' in plan:
            payload = plan.pop('payload')
            payload.pop('route_id')
            details['step'] = dict(id=14, command='review-assignment', payload=payload, chooses=['route_id'],
                                   prerequisites=[dict(condition=REFRESH_ROUTE_CHOICE)])
        plan.update(state='handoff', command='review-assignment')
    elif repair_source or code in ('review_context_contaminated', 'review_route_retired', 'review_context_repair_required',
                                   'review_context_repair_stale', 'review_context_repair_mismatch',
                                   'review_context_repair_unverified'):
        if code == 'review_context_repair_stale':
            repairs = [row for row in protocol.context_repair._lineage(records, assignment)
                       if row.get('context', {}).get('context_repair') is not None]
            binding = repairs[-1]['context']['context_repair'] if repairs else {}
            if binding.get('exhaustion') is not None:
                predecessor = records['review_assignment'][binding['assignment_id']]
                details['step'] = describe_step(records, artifacts, predecessor, 2)
                details['step']['payload']['context']['context_repair']['exhaustion'] = copy.deepcopy(
                    binding['exhaustion'])
                # The repair of the exhausted predecessor is named only on a route that serves it.
                stop = _plan_unserved_repair(records, artifacts, predecessor, plan)
                if stop is not None:
                    return stop
                return dict(plan, state='handoff', command='review-assignment')
        numbers = select_route_steps(records, artifacts, assignment)
        number = numbers[-1]
        cause = protocol.context_repair.find_repair_cause(records, artifacts, assignment) if 8 in numbers else None
        from .review_route_plan import read_route_standing
        if 8 in numbers and cause is None:
            details['steps'] = describe_route_steps(records, artifacts, assignment, numbers, details)
            details['step'] = details['steps'][0]
        elif code == 'review_route_retired' and read_route_standing(records, artifacts, assignment['route_id'])['stale']:
            # The slot continues with the installed runtime, verified after the cause.
            details['steps'] = describe_route_steps(records, artifacts, assignment, [7, number], details,
                                                    repair_cause=cause)
            details['step'] = details['steps'][0]
            if len(details['steps']) == 1:
                details.pop('steps')
        else:
            details['step'] = describe_step(records, artifacts, assignment, number,
                                            repair_events=cause['events'] if cause else None)
            stop = _plan_unserved_repair(records, artifacts, assignment, plan) if number == 1 else None
            if stop is not None:
                return stop
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
            # that the plan names (the record of a turn under rule PC5), else the record of an accepted output
            # correction when that one is not verified, otherwise the route of the assignment.
            route_id = assignment['route_id']
            if code == 'review_context_unverified':
                from .review_route_plan import find_unverified_route
                route_id = details.get('route_id') or find_unverified_route(records, artifacts, assignment)
            prerequisites = details['step']['prerequisites']
            details['step'] = describe_route_probe_step(records, artifacts, assignment, route_id)
            details['step']['prerequisites'] = prerequisites
    elif code == 'review_route_capacity_below_floor' and (plan.get('payload') or {}).get('context', {}).get(
            'session_refresh'):
        # Rule F5: the refresh that the capacity admission refuses takes the probe of its route or step 14 on a
        # serving route, the answers of the refusal; with none, the plan stops.
        plan.pop('payload')
        plan.pop('command', None)
        details['decision'] = dict(owner_decides=details['owner_decides'], then=details['then'])
        if details['then']:
            details['step'] = details['then'][0]['steps'][0]
            plan['command'] = details['step']['command']
        plan['state'] = 'stop'
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
