"""Admit the next export of a stopped problem chain before offering its handoff."""

import copy

from . import openness_reading as reading, openness_reading_v7 as v7
from . import review_protocol as protocol
from .evidence import digest
from .native_review import load_packet
from .errors import ResearchError

V7_SUCCESSOR_MESSAGE = 'Export a new packet on this route with the same reviewer. Its v7 chain takes over the listed verified readings and reads the remaining ranges.'
V6_SUCCESSOR_MESSAGE = 'Export a new packet on this route. Its v6 chain reads every unit again in the planned reading turns.'
RATIO_SUCCESSOR_MESSAGE = 'The new chain inherits the measured ratio and its source; its admission decides whether this route can continue.'
OUTPUT_CAP_EXPORT_MESSAGE = 'Export a new packet for the admitted successor on this route.'


def describe_input_handoff(records, artifacts, assignment, *, continuation=None):
    """Measure an uncalled request, including a prospective continuation, before planning its call."""
    if not assignment.get('reading_chain'):
        return None
    packet = protocol._read_json(artifacts, assignment['packet'])
    if reading.find_protocol(packet) not in (reading.PROTOCOL_V5, reading.PROTOCOL_V6, v7.PROTOCOL):
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
    native = copy.deepcopy(native)
    context = native['scientific_context']
    minimum = context['openness_minimum']
    if minimum['schema_version'] == 1:
        from .problem_openness import build_minimum
        searches = {digest(row): row for row in records.get('literature_search', {}).values()}
        bound = [(row['formulation'], searches[row['search_digest']]) for row in minimum['searches']]
        readings = {key: row for key, row in records.get('reading', {}).items()
                    if digest(row) in minimum['reading_digests']}
        minimum = build_minimum(native['subject'], bound, readings, minimum, records=records, artifacts=artifacts)
        context['openness_minimum'] = minimum
        from .openness_minimum import (list_minimum_gaps, forecast_outcomes, list_minimum_notices,
                                      find_searched_through)
        gaps = list_minimum_gaps(minimum, minimum['statement'], records, artifacts)
        context.update(minimum_gaps=gaps, audit_forecast=dict(outcomes=forecast_outcomes(gaps), gaps=gaps,
            notices=list_minimum_notices(minimum, records, artifacts),
            searched_through=find_searched_through(minimum, records)))
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
    native = _project_successor_native(records, overlay, native)
    route = records['review_route'][assignment['route_id']]
    logical = dict(protocol=protocol.PROTOCOL, role=assignment['role'], dossier_id=assignment['dossier_id'],
                   reviewer_id=assignment['reviewer_id'], native=native,
                   evidence=protocol.build_packet(records, overlay, assignment['dossier_id'], assignment['role'],
                       assignment['reviewer_id'], native_packet=assignment['context']['native_packet'])['evidence'])
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
