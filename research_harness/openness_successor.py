"""Admit the next packet of a stopped problem or math chain before offering its handoff."""

import copy

from . import openness_reading as reading, openness_reading_v7 as v7
from . import review_protocol as protocol
from .evidence import digest
from .native_review import load_packet
from .errors import ResearchError

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
# The texts of a stopped chain of exactory-openness-reading-math-v1 once the installed release holds
# exactory-openness-reading-math-v2 in openness_reading.NEW_MATH_CHAIN_PROTOCOLS (ruling 24.2 as corrected).
MATH_V2_SUCCESSOR_MESSAGE = (
    'Build a new review packet with the math controller and assign it on this route with the same reviewer_id, as '
    'details.step states. The admission accepts it as a chain of exactory-openness-reading-math-v2. That chain '
    'carries the verified reading turns that details.successor.carried_readings lists, and it reads only the ranges '
    'that remain.')
MATH_V1_SUCCESSOR_MESSAGE = (
    'Build a new review packet with the math controller and assign it on this route with the same reviewer_id, as '
    'details.step states. The admission accepts it as a chain of exactory-openness-reading-math-v1. That chain '
    'carries no earlier turn: it reads every unit again.')
MATH_SUCCESSOR_ANSWER = 'Build a new math review packet and start the admitted chain on this route'
MATH_ESTIMATE_DECISION = ('Whether this chain continues to its final turn, a new chain carries its verified turns, or '
                          'the review runs over a packet that binds less material.')
MATH_EXACT_DECISION = ('Whether a new chain carries the verified turns of this chain, or the review runs over a packet '
                       'that binds less material.')
MATH_REVIEWER_PREREQUISITE = 'Give reviewer.actor_id the reviewer_id that this step names.'
MATH_PROTOCOLS = (reading.MATH_PROTOCOL, reading.MATH_PROTOCOL_V2)


def is_math_successor_installed(packet):
    """Whether a stopped math chain has a successor: the installed order starts new math chains under v2."""
    return (reading.find_protocol(packet) in MATH_PROTOCOLS
            and reading.MATH_PROTOCOL_V2 in reading.NEW_MATH_CHAIN_PROTOCOLS)


def describe_input_handoff(records, artifacts, assignment, *, continuation=None):
    """Measure an uncalled request, including a prospective continuation, before planning its call."""
    if not assignment.get('reading_chain'):
        return None
    packet = protocol._read_json(artifacts, assignment['packet'])
    math_chain = reading.find_protocol(packet) == reading.MATH_PROTOCOL and is_math_successor_installed(packet)
    if reading.find_protocol(packet) not in (reading.PROTOCOL_V5, reading.PROTOCOL_V6) and not math_chain:
        return None
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    if not reading.is_openness_packet(native) and not math_chain:
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
                reading_protocol=reading.find_protocol(packet), common_version=protocol.find_common_prompt_version(assignment['prompt']),
                finding_version=2 if 'finding_index' in packet else 1)
            request = protocol.transport.request_body(route, prompt['text'], packet)
        measured = capacity.measure_request(route, request, records=records, artifacts=artifacts)
        # A math v1 turn is admitted by the character limit of its transport; its token bound is a warning.
        if measured['characters'] <= measured['character_limit'] if math_chain else measured['fits']:
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
    math_chain = is_math_successor_installed(packet)
    if reading.find_protocol(packet) not in (reading.PROTOCOL_V5, reading.PROTOCOL_V6, v7.PROTOCOL) and not math_chain:
        return None
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    if not (reading.is_math_openness_packet(native) if math_chain else reading.is_openness_packet(native)):
        return None
    overlay = v7._PlanningArtifacts(artifacts)
    if not math_chain:
        native = _project_successor_native(records, overlay, native)
    route = records['review_route'][assignment['route_id']]
    logical = dict(protocol=protocol.PROTOCOL, role=assignment['role'], dossier_id=assignment['dossier_id'],
                   reviewer_id=assignment['reviewer_id'], native=native,
                   evidence=protocol.build_packet(records, overlay, assignment['dossier_id'], assignment['role'],
                       assignment['reviewer_id'], native_packet=assignment['context']['native_packet'])['evidence'])
    selected = reading.choose_chain_protocol(records, overlay, route, native, packet=logical, assignment=assignment)
    result = {key: value for key, value in selected.items() if key != 'result'}
    if selected['protocol'] in v7.PROTOCOL_DESCRIPTIONS:
        value = {key: assignment[key] for key in ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        _, chain = v7.prepare_carried_assignment(records, overlay, value, route, logical, selected['result'])
        result.update(carried_readings=chain['carried_readings'],
                      measure=selected['result']['capacity_plan']['measure'])
    elif selected['protocol'] == reading.PROTOCOL_V6:
        result.update(reads_every_unit_again=True,
                      planned_reading_turns=selected['result']['projection']['planned_reading_turns'])
    elif selected['protocol'] == reading.MATH_PROTOCOL:
        result.update(reads_every_unit_again=True)
    else:
        # A math chain without an admitted successor keeps the decision of its own stop.
        result['stop'] = ({} if math_chain else
                          v7.describe_admission_stop(records, overlay, assignment, route, logical, selected))
    return result


def _describe_successor_step(records, artifacts, assignment):
    """The step of a new packet on this route under the same reviewer: row 4 for a problem chain, row 5 for math."""
    from .review_plan_steps import describe_step
    packet = protocol._read_json(artifacts, assignment['packet'])
    if reading.find_protocol(packet) in MATH_PROTOCOLS:
        step = describe_step(records, artifacts, assignment, 5)
        native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
        step['payload']['openness'] = copy.deepcopy(native['scientific_context']['openness_minimum'])
        step.update(reviewer_id=assignment['reviewer_id'])
        step['prerequisites'].append(dict(condition=MATH_REVIEWER_PREREQUISITE))
    else:
        step = describe_step(records, artifacts, assignment, 4)
        step['payload']['reviewer_id'] = assignment['reviewer_id']
        step['chooses'].remove('reviewer_id')
    continuation = step['then'][0]
    continuation['payload'].update(route_id=assignment['route_id'], reviewer_id=assignment['reviewer_id'])
    continuation['chooses'] = [key for key in continuation['chooses'] if key not in ('route_id', 'reviewer_id')]
    step['then'] = [dict(copy.deepcopy(continuation), check=True), continuation]
    return step


def describe_math_successor_answer(records, artifacts, assignment):
    """Return the answer that starts the admitted successor of a stopped math chain, or None, and the admission."""
    admitted = admit_successor_chain(records, artifacts, assignment)
    if admitted['protocol'] is None:
        return None, admitted
    return dict(answer=MATH_SUCCESSOR_ANSWER, steps=[_describe_successor_step(records, artifacts, assignment)]), admitted


def describe_math_decision(records, artifacts, assignment, decision, *, estimate):
    """Give the projection stop of a math v1 chain the step of its admitted successor in place of the wait for it.

    An estimate keeps its answer "continue" (ruling 24.2 as corrected). An exact measure has no such answer, because
    only the exact measure ends a continued chain. Without an admitted successor the decision stands alone.
    Return the decision and the admission.
    """
    successor, admitted = describe_math_successor_answer(records, artifacts, assignment)
    answers = [copy.deepcopy(row) for row in decision['then'] if row['answer'] != reading.WAIT_MATH_ANSWER
               and (estimate or row['answer'] != reading.CONTINUE_MATH_ANSWER)]
    if successor is not None:
        answers.insert(1 if estimate else 0, successor)
    return dict(owner_decides=MATH_ESTIMATE_DECISION if estimate else MATH_EXACT_DECISION, then=answers), admitted


def describe_successor_handoff(records, artifacts, assignment, code, details=None):
    """Offer export only for an admitted successor; otherwise give executable owner answers."""
    details = copy.deepcopy(details or {})
    packet = protocol._read_json(artifacts, assignment['packet'])
    if code in ('review_input_too_large', reading.PROJECTION_STOP) and not details.get('ratio'):
        if reading.find_protocol(packet) in v7.PROTOCOL_DESCRIPTIONS:
            return None
    if reading.find_protocol(packet) == reading.MATH_PROTOCOL:
        # A math v1 chain stops with the owner's decision, which names the admitted successor once it is installed.
        if code not in ('review_input_too_large', reading.PROJECTION_STOP) or not is_math_successor_installed(packet):
            return None
        details['decision'] = reading.describe_stop_decision(reading.MATH_PROTOCOL, details, records=records,
                                                             artifacts=artifacts, assignment=assignment)
        return dict(state='stop', code=code, details=details)
    successor = details.get('successor') or admit_successor_chain(records, artifacts, assignment)
    if successor is None:
        return None
    details['successor'] = successor
    if successor['protocol'] is None:
        details.update(successor['stop'])
        details.pop('step', None)
        return dict(state='stop', code=code, details=details)
    step = _describe_successor_step(records, artifacts, assignment)
    messages = {v7.PROTOCOL: V7_SUCCESSOR_MESSAGE, reading.PROTOCOL_V6: V6_SUCCESSOR_MESSAGE,
                reading.MATH_PROTOCOL_V2: MATH_V2_SUCCESSOR_MESSAGE, reading.MATH_PROTOCOL: MATH_V1_SUCCESSOR_MESSAGE}
    details.update(step=step, message=messages[successor['protocol']])
    if details.get('ratio'):
        details['ratio_message'] = RATIO_SUCCESSOR_MESSAGE
    return dict(state='handoff', code=code, command=step['command'], details=details)
