"""Admit the next export of a stopped problem chain before offering its handoff."""

import copy

from . import openness_reading as reading, openness_reading_v7 as v7
from . import review_protocol as protocol
from .evidence import digest
from .native_review import load_packet
from .errors import ResearchError
from .problem_openness import serialize_record

# The messages of the handoff export that a stopped problem chain gets when a new chain is admitted. details.successor
# holds that admission.
V7_SUCCESSOR_MESSAGE = (
    'Export a new packet and assign it on this route with the same reviewer_id, as details.step states. The '
    'admission accepts it as a chain of exactory-openness-reading-v7. That chain carries the verified reading turns '
    'that details.successor.carried_readings lists, and it reads only the ranges that remain.')
V6_SUCCESSOR_MESSAGE = (
    'Export a new packet and assign it on this route with the same reviewer_id, as details.step states. The '
    'admission accepts it as a chain of exactory-openness-reading-v6. That chain carries no earlier turn: it reads '
    'every unit again, in the number of reading turns that details.successor.planned_reading_turns gives.')
RATIO_SUCCESSOR_MESSAGE = (
    'When the new chain runs under exactory-openness-reading-v7, details.successor.measure states the ratio of its '
    'plan and the source of that ratio: the ratio of details.ratio, which this chain measured, or a denser one that '
    'an earlier chain of the same statement and route shows. A chain under exactory-openness-reading-v6 is admitted '
    'by its own projection and does not take this '
    'ratio.')
# An answer of the decision after an output over the limit, and details.export_message of the same stop.
OUTPUT_CAP_EXPORT_MESSAGE = 'Export a new packet and start a new chain on this route'


def describe_input_handoff(records, artifacts, assignment, *, continuation=None):
    """Measure an uncalled request, including a prospective continuation, before planning its call."""
    if not assignment.get('reading_chain'):
        return None
    packet = protocol._read_json(artifacts, assignment['packet'])
    if reading.find_protocol(packet) not in (reading.PROTOCOL_V5, reading.PROTOCOL_V6):
        return None
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    if not reading.is_openness_packet(native):
        return None
    from . import review_route_capacity as capacity
    route = records['review_route'][assignment['route_id']]
    try:
        if continuation is None:
            request = protocol.build_assignment_request(records, artifacts, assignment)
        else:
            overlay = v7._PlanningArtifacts(artifacts)
            logical = protocol.build_packet(records, overlay, continuation['dossier_id'], continuation['role'],
                                           continuation['reviewer_id'], **continuation['context'])
            packet, _ = reading.prepare_packet(records, overlay, continuation, route, logical)
            prompt = protocol.canonical_prompt(assignment['role'], reading_stage=reading.find_stage(packet),
                reading_protocol=reading.find_protocol(packet), common_version=protocol.find_common_prompt_version(assignment['prompt']))
            request = protocol.transport.request_body(route, prompt['text'], packet)
        measured = capacity.measure_request(route, request, records=records, artifacts=artifacts)
        if measured['fits']:
            return None
        details = dict(request_measure=measured)
    except ResearchError as error:
        if error.code not in ('review_input_too_large', reading.PROJECTION_STOP):
            raise
        details = error.details
    return describe_successor_handoff(records, artifacts, assignment, reading.PROJECTION_STOP, details)


def _project_successor_native(records, artifacts, native):
    """Upgrade a v5 minimum to the declaration made by the next current export."""
    if native['scientific_context']['openness_minimum']['schema_version'] == 2:
        return native
    native = copy.deepcopy(native)
    context = native['scientific_context']
    minimum = context['openness_minimum']
    if minimum['schema_version'] == 1:
        from .problem_openness import build_minimum, describe_minimum_forecast, bind_minimum_records
        searches = {digest(row): row for row in records.get('literature_search', {}).values()}
        bound = [(row['formulation'], searches[row['search_digest']]) for row in minimum['searches']]
        readings = {key: row for key, row in records.get('reading', {}).items()
                    if digest(row) in minimum['reading_digests']}
        minimum = build_minimum(native['subject'], bound, readings, minimum, records=records, artifacts=artifacts)
        context['openness_minimum'] = minimum
        described = describe_minimum_forecast(minimum, minimum['statement'], records, artifacts)
        bindings = bind_minimum_records(records, artifacts, native['subject'], bound, minimum)
        context.update(minimum_gaps=described['gaps'], audit_forecast=described['forecast'], **bindings['context'])
        evidence = {row['digest']: row for row in native['evidence']}
        evidence.update(bindings['evidence'])
        native['evidence'] = [evidence[key] for key in sorted(evidence)]
    return native


def admit_successor_chain(records, artifacts, assignment):
    """Return the actual ordered admission and carried work, or all protocol refusals."""
    packet = protocol._read_json(artifacts, assignment['packet'])
    if reading.find_protocol(packet) not in (reading.PROTOCOL_V5, reading.PROTOCOL_V6, v7.PROTOCOL):
        return None
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    if not reading.is_openness_packet(native):
        return None
    overlay = v7._PlanningArtifacts(artifacts)
    projected = _project_successor_native(records, overlay, native)
    reference = assignment['context']['native_packet']
    if projected is not native:
        reference = overlay.put(serialize_record(projected), 'application/json')
    native = projected
    route = records['review_route'][assignment['route_id']]
    logical = protocol.build_packet(records, overlay, assignment['dossier_id'], assignment['role'],
                                    assignment['reviewer_id'], native_packet=reference)
    selected = reading.choose_chain_protocol(records, overlay, route, native, packet=logical, assignment=assignment)
    result = {key: value for key, value in selected.items() if key != 'result'}
    if selected['protocol'] == v7.PROTOCOL:
        value = {key: assignment[key] for key in ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        _, chain = v7.prepare_carried_assignment(records, overlay, value, route, logical, selected['result'])
        result.update(carried_readings=chain['carried_readings'],
                      measure=selected['result']['capacity_plan']['measure'])
    elif selected['protocol'] == reading.PROTOCOL_V6:
        result.update(reads_every_unit_again=True,
                      planned_reading_turns=selected['result']['projection']['planned_reading_turns'])
    else:
        result['stop'] = v7.describe_admission_stop(records, overlay, assignment, route, logical, selected)
    return result


def describe_successor_handoff(records, artifacts, assignment, code, details=None):
    """Offer export only for an admitted successor; otherwise give executable owner answers."""
    details = copy.deepcopy(details or {})
    if code in ('review_input_too_large', reading.PROJECTION_STOP) and not details.get('ratio'):
        packet = protocol._read_json(artifacts, assignment['packet'])
        if reading.find_protocol(packet) == v7.PROTOCOL:
            return None
    successor = details.get('successor') or admit_successor_chain(records, artifacts, assignment)
    if successor is None:
        return None
    details['successor'] = successor
    if successor['protocol'] is None:
        details.update(successor['stop'])
        details.pop('step', None)
        return dict(state='stop', code=code, details=details)
    from .review_plan_steps import describe_step
    step = describe_step(records, artifacts, assignment, 4)
    step['payload']['reviewer_id'] = assignment['reviewer_id']
    step['chooses'].remove('reviewer_id')
    continuation = step['then'][0]
    continuation['payload'].update(route_id=assignment['route_id'], reviewer_id=assignment['reviewer_id'])
    continuation['chooses'] = [key for key in continuation['chooses'] if key not in ('route_id', 'reviewer_id')]
    step['then'] = [dict(continuation, check=True), continuation]
    details.update(step=step, message=V7_SUCCESSOR_MESSAGE if successor['protocol'] == v7.PROTOCOL else V6_SUCCESSOR_MESSAGE)
    if details.get('ratio'):
        details['ratio_message'] = RATIO_SUCCESSOR_MESSAGE
    return dict(state='handoff', code=code, command='export', details=details)
