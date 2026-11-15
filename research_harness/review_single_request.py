"""Enter parts from a single task, retaining its frozen packet and independence."""

import copy

from . import review_parts as parts, review_route_capacity as capacity
from .errors import ResearchError
from .operations import fields, normalized_text, text


def describe_task_entry(records, route, role, packet, request):
    if capacity.describe_task_kind(role, packet) != 'single':
        return None
    task = capacity.describe_task(role, packet, route=route, request=request)
    profile = capacity.read_output_profile(records, route)
    limits = capacity.select_task_limits(route, profile, 'single', targets=task['targets'], frame_characters=0)
    characters = capacity.measure_request(route, request)['text_characters']
    return 'task' if characters > limits['task_characters'] else None


OUTPUT_SERIES_WARNING = 'This single review exceeds its output profile start value. Follow its continuation plan after an attempt reaches the output limit.'


def describe_assignment_delivery(records, artifacts, assignment):
    """Explain the prospective form without adding diagnostics to a stored assignment."""
    from . import review_protocol as protocol
    from .review_parts_roles import find_adapter

    if assignment.get('staged_review'):
        return dict(form='staged', reason='protocol')
    packet = protocol._read_json(artifacts, assignment['packet'])
    kind = capacity.describe_task_kind(assignment['role'], packet)
    if parts.find_kind(packet) is not None:
        return dict(form='series', reason=packet[parts.KEY].get('entry', 'input'))
    if kind != 'single':
        return dict(form=kind, reason='protocol')
    route = records['review_route'][assignment['route_id']]
    request = protocol.build_assignment_request(records, artifacts, assignment)
    if describe_task_entry(records, route, assignment['role'], packet, request) is None:
        return dict(form='single', reason='within_start')
    adapter = find_adapter(assignment, packet)
    reason = ('recorded_single' if adapter is not None and route['adapter'] in protocol.transport.TEXT_ONLY_ADAPTERS
              else 'no_parts_adapter')
    return dict(form='single', reason=reason, capacity_warning=dict(output=dict(
        state='above_start', message=OUTPUT_SERIES_WARNING,
        adapter=adapter, route_adapter=route['adapter'])))


def measure_parts_request(records, route, request, characters):
    """Give the parts fitter the canonical token and text limits in its character unit."""
    profile = capacity.read_output_profile(records, route)
    limits = capacity.describe_route_capacity(route, profile)
    measure = capacity.measure_request(route, request)
    if (measure['tokens_upper'] > limits['input_tokens']['value']
            or measure['text_characters'] > limits['input_text_characters']['value']):
        from .review_transport import local_input_limit
        return max(characters, local_input_limit(route) + 1)
    return characters


def is_first_output_read(assignment, packet):
    """Identify the first read of a frozen single-output replacement series."""
    return (parts.find_kind(packet) == 'read' and packet[parts.KEY].get('entry') == 'output'
            and bool(assignment.get('capacity_replacement_of')
                     or assignment.get('context', {}).get('output_refusal'))
            and not assignment.get('context', {}).get('prior_assignment_id'))


def build_output_replacement(records, artifacts, assignment, *, identifier=None):
    """Plan or reduce the first read of a frozen single-output replacement."""
    from . import review_protocol as protocol, review_preflight
    from .review_parts_roles import find_adapter

    packet = protocol._read_json(artifacts, assignment['packet'])
    route = records['review_route'][assignment['route_id']]
    retry_read = is_first_output_read(assignment, packet)
    header = packet[parts.KEY] if retry_read else {}
    frozen_notice = packet.get('output_refusal') if retry_read else None
    if retry_read:
        packet = parts.read_logical_packet(artifacts, header)
    if (not retry_read and capacity.describe_task_kind(assignment['role'], packet) != 'single'
            or find_adapter(assignment, packet) is None
            or route['adapter'] not in protocol.transport.TEXT_ONLY_ADAPTERS):
        return None
    adapter = find_adapter(assignment, packet)
    inventory = parts.build_inventory(packet, adapter)
    if inventory['characters'] < 2:
        return None
    profile = capacity.read_output_profile(records, route)
    allowance = min(inventory['characters'] // 2,
                    capacity.select_task_limits(route, profile, 'read')['material_allowance'])
    if retry_read:
        allowance = min(allowance, header['unit_characters'] // 2)
    base = assignment['prompt']['parameters']
    if retry_read:
        base = base['base_prompt']['parameters']
    parameters = dict(repair=bool(base.get('context_repair')),
        orchestration=base.get('orchestration'), excerpts='reviewer_excerpts' in base,
        common_version=protocol.find_common_prompt_version(assignment['prompt']))
    from .review_prompt_paragraphs import read_paragraph_parameters
    parameters.update(read_paragraph_parameters(assignment['prompt']))
    value = {key: copy.deepcopy(assignment[key]) for key in
             ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
    value['id'] = identifier or value['id'] + '-replacement'
    planned = parts.prepare_packet(records, artifacts, value, route, packet, assignment['prompt'],
        protocol.read_history(artifacts, assignment), base_prompt_parameters=parameters,
        slot_id=protocol.context_repair.slot_id(records, assignment), entry='output', read_allowance=allowance,
        replaces_assignment_id=header['replaces_assignment_id'] if retry_read else assignment['id'],
        refusal_notice=frozen_notice)
    if parts.find_kind(planned) != 'read':
        return None
    prompt = parts.build_turn_prompt(assignment['role'], parameters, planned[parts.KEY])
    request = protocol.transport.request_body(route, prompt['text'], planned, [], order=())
    preflight = review_preflight.check_request(review_preflight.build_report(
        records, artifacts, route, assignment['role'], prompt, planned, request),
        request, packet=planned, artifacts=artifacts)
    if planned[parts.KEY]['unit_characters'] > inventory['characters'] // 2:
        return None
    if retry_read:
        from .review_problem_parts import _measure
        old_request = protocol.build_assignment_request(records, artifacts, assignment)
        if _measure(route, request) >= _measure(route, old_request):
            return None
    return dict(packet=planned, prompt=prompt, preflight=preflight,
                context=dict(capacity_replacement=assignment['id']))


def prepare_replacement(records, artifacts, value, revision):
    """Validate the command's replacement binding once, then publish its frozen read turn."""
    from . import review_protocol as protocol, review_failure_class as failures
    from .operations import immutable_record

    fields(value['context'], ('capacity_replacement',), code='invalid_review_context')
    identifier = value['context']['capacity_replacement']
    text(identifier, 'capacity_replacement', code='invalid_review_context')
    previous = protocol._get(records, 'review_assignment', identifier)
    if (any(value[key] != previous[key] for key in ('role', 'route_id', 'dossier_id'))
            or any(normalized_text(value[key]) != normalized_text(previous[key])
                   for key in ('reviewer_id', 'author_id'))):
        raise ResearchError('review_source_continuation_mismatch',
                            'A capacity replacement keeps the frozen review, route, reviewer and author.')
    if protocol.find_replacement(records, previous) is not None:
        raise ResearchError('review_source_continuation_fork', 'Continue the recorded capacity replacement.')
    attempts = protocol.read_filtered_records(records, 'review_attempt', assignment_id=identifier).values()
    classifications = [failures.classify_attempt(records, artifacts, row) for row in attempts]
    eligible = any((outcome or {}).get('outcome') == 'output_over_cap' for outcome in classifications)
    if is_first_output_read(previous, protocol._read_json(artifacts, previous['packet'])):
        recovery = failures.plan_after_failure(records, artifacts, identifier)
        eligible = (recovery['state'] == 'replace'
                    and recovery.get('details', {}).get('handler') == 'single_request_series')
    if (protocol.has_pending_invocation(records, identifier)
            or any(outcome is None for outcome in classifications) or not eligible):
        raise ResearchError('invalid_review_context',
                            'A capacity replacement needs an admissible smaller task, no pending invocation and no usable completed output.')
    planned = build_output_replacement(records, artifacts, previous, identifier=value['id'])
    if planned is None:
        raise ResearchError('invalid_review_context', 'This single review has no smaller parts task.')
    packet, prompt = planned['packet'], planned['prompt']
    header = packet[parts.KEY]
    record = copy.deepcopy(previous)
    record.update(id=value['id'], packet=protocol._put_json(artifacts, packet),
                  packet_digest=packet['digest'], prompt=prompt, prompt_hash=prompt['rendered_prompt_hash'],
                  history=[], history_assignment_id=None, recorded_revision=revision,
                  capacity_replacement_of=identifier)
    record['context_state'] = protocol.route_state(records, artifacts, previous['route_id'])
    record['slot_id'] = protocol.context_repair.slot_id(records, previous)
    record.pop('history_ref', None)
    record[parts.KEY] = {key: header[key] for key in
        ('protocol', 'adapter', 'kind', 'series_assignment_id', 'turn', 'inventory_digest', 'replaces_assignment_id')}
    record['review_parts_plan'] = parts.describe_plan(packet)
    from .problem_delivery import require_unposted_review
    require_unposted_review(records, record, artifacts)
    return [immutable_record(records, 'review_assignment', value['id'], record)], record
