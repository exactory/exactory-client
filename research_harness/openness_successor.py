"""Admit the next packet of a stopped problem or math chain before offering its handoff."""

import copy

from . import openness_reading as reading, openness_reading_v7 as v7
from . import review_protocol as protocol
from .evidence import digest
from .native_review import load_packet
from .errors import ResearchError
from .problem_openness import serialize_record

# The messages of the handoff export that a stopped problem chain gets when a new chain is admitted. details.successor
# holds that admission. NEW_CHAIN_ROUTE_TEXT is their statement on the route record of the first assignment.
NEW_CHAIN_ROUTE_TEXT = (
    'details.successor states the admission of the new chain on one route record: the record that '
    'details.successor.route_id names or, without that member, the route record of the stopped chain. A record that '
    'details.successor.route_id names is verified, has the adapter, model, endpoint and configuration of the route '
    'of the stopped chain, and pins the installed runtime. The assignment of details.step holds that record as '
    'route_id. When it leaves route_id to you instead, the route record of the stopped chain is retired, and '
    'details.successor states the admission on that retired record: give route_id a route record that is verified, '
    'is not retired and pins the installed runtime, and read the admission on it from the check of details.step. ')
V7_SUCCESSOR_MESSAGE = (
    'Export a new packet and assign it with the same reviewer_id, as details.step states. ' + NEW_CHAIN_ROUTE_TEXT +
    'The admission accepts the new packet as a chain of exactory-openness-reading-v7. That chain carries the '
    'verified reading turns that details.successor.carried_readings lists. It reads the ranges that remain and the '
    'carried excerpts and passage answers that its final cannot hold. It also reads the newest carried notes that '
    'rest on a unit that changed or left the packet.')
V6_SUCCESSOR_MESSAGE = (
    'Export a new packet and assign it with the same reviewer_id, as details.step states. ' + NEW_CHAIN_ROUTE_TEXT +
    'The admission accepts the new packet as a chain of exactory-openness-reading-v6. That chain carries no '
    'earlier turn: it reads every unit again, in the number of reading turns that '
    'details.successor.planned_reading_turns gives.')
RATIO_SUCCESSOR_MESSAGE = (
    'Where details.successor holds measure, the new chain has a capacity plan, and measure states the ratio of '
    'that plan and the source of the ratio: the ratio of details.ratio, which this chain measured, or a denser one '
    'that an earlier chain of the same statement and route shows. A new chain without measure is admitted by the '
    'rules of its own protocol and does not take this ratio.')
# An answer of the decision after an output over the limit, and details.export_message of the same stop.
OUTPUT_CAP_EXPORT_MESSAGE = 'Export a new packet and start a new chain with the same reviewer'
# The texts of a stopped chain of exactory-openness-reading-math-v1 once the installed release holds
# exactory-openness-reading-math-v2 in openness_reading.NEW_MATH_CHAIN_PROTOCOLS (ruling 24.2 as corrected).
MATH_V2_SUCCESSOR_MESSAGE = (
    'Build a new review packet with the math controller and assign it with the same reviewer_id: take '
    'details.step, and then the steps of its then in their order. ' + NEW_CHAIN_ROUTE_TEXT + 'On the route record '
    'of details.successor, the new packet starts a chain of exactory-openness-reading-math-v2. That chain carries '
    'the verified reading turns that details.successor.carried_readings lists. It reads the ranges that remain and '
    'the carried excerpts and passage answers that its final cannot hold. It also reads the newest carried notes '
    'that rest on a unit that changed or left the packet.')
MATH_V1_SUCCESSOR_MESSAGE = (
    'Build a new review packet with the math controller and assign it with the same reviewer_id: take '
    'details.step, and then the steps of its then in their order. ' + NEW_CHAIN_ROUTE_TEXT + 'On the route record '
    'of details.successor, exactory-openness-reading-math-v2 does not admit the new packet, and '
    'details.successor.admissions gives its refusal. The new packet runs under exactory-openness-reading-math-v1, '
    'which carries no earlier turn: its reviewer reads every unit again.')
MATH_SUCCESSOR_ANSWER = 'Build a new math review packet and start the admitted chain with the same reviewer'
# The two questions hold for each admitted successor: details.successor states whether the new chain carries the
# verified turns of this chain (carried_readings) or reads every unit again (reads_every_unit_again).
MATH_ESTIMATE_DECISION = ('Whether this chain continues to its final turn, or the review runs over a packet that '
                          'binds less material. Where an answer offers it, the review can also move to a new chain '
                          'over the same material.')
MATH_EXACT_DECISION = ('Whether the review runs over a packet that binds less material or, where an answer offers '
                       'it, moves to a new chain over the same material.')
MATH_REVIEWER_PREREQUISITE = (
    'reviewer.actor_id is the reviewer_id of this step: a new chain carries earlier readings only under the same '
    'reviewer. Where the payload holds no reviewer, give it that actor_id.')
MATH_FRESH_ATTESTATION = (
    'reviewer.attestation_id is review-assignment: followed by the id of the new assignment, the id that the '
    'assignment of then takes. Where the payload does not hold it, choose that id and set both.')
MATH_CLAIM_MINIMUM_PREREQUISITE = (
    'Choose the openness minimum of the new packet yourself. The harness could not confirm from the packet of the '
    'stopped chain that its minimum states the claim that claim_digest names. Give the new minimum the statement '
    'that the math controller resolves for that digest.')
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


def find_successor_chain(records, artifacts, assignment):
    """Return the first turn of the chain that continues the review of assignment after it, or None.

    A math v2 successor names assignment as its native predecessor. Any other successor of a reading chain is a later
    first turn of the same reviewer whose stored carried selection carries a turn of the chain of assignment.
    """
    import json
    from .review_math_handoff import find_successor
    from .storage import read_filtered_records

    linked = find_successor(records, assignment)
    if linked is not None or not assignment.get('reading_chain'):
        return linked
    chain = assignment['reading_chain']['first_assignment_id']
    candidates = sorted(read_filtered_records(records, 'review_assignment', role='native_math').values(),
                        key=lambda row: row['recorded_revision'])
    for candidate in candidates:
        if (candidate['recorded_revision'] <= assignment['recorded_revision']
                or candidate['reviewer_id'] != assignment['reviewer_id'] or candidate.get('carried_selection') is None
                or candidate['reading_chain']['first_assignment_id'] != candidate['id']):
            continue
        selection = json.loads(artifacts.read(candidate['carried_selection']['artifact']))
        if any(turn['origin']['chain'] == chain for turn in selection['turns']):
            return candidate
    return None


def admit_successor_chain(records, artifacts, assignment, route_id=None):
    """Return the actual ordered admission and carried work, or all protocol refusals, for a new chain on the route
    record of the stopped chain, or on the route record that route_id names. The result names that other record."""
    packet = protocol._read_json(artifacts, assignment['packet'])
    math_chain = is_math_successor_installed(packet)
    if reading.find_protocol(packet) not in (reading.PROTOCOL_V5, reading.PROTOCOL_V6, v7.PROTOCOL) and not math_chain:
        return None
    native = load_packet(artifacts, assignment['context']['native_packet'], assignment['reviewer_id'])
    if not (reading.is_math_openness_packet(native) if math_chain else reading.is_openness_packet(native)):
        return None
    overlay = v7._PlanningArtifacts(artifacts)
    reference = assignment['context']['native_packet']
    prospective = assignment
    if math_chain and len(assignment['id']) > reading.RESERVED_IDENTIFIER_CHARACTERS:
        from .review_continuation import _find_unused_id
        identifier = _find_unused_id(records, 'math-successor-' + assignment['id'],
                                     maximum=reading.RESERVED_IDENTIFIER_CHARACTERS)
        native = dict(native, reviewer=dict(native['reviewer'], attestation_id='review-assignment:' + identifier))
        reference = overlay.put(serialize_record(native), 'application/json')
        prospective = dict(assignment, id=identifier, context=dict(native_packet=reference))
        prospective.pop('recorded_revision', None)
    if not math_chain:
        projected = _project_successor_native(records, overlay, native)
        if projected is not native:
            reference = overlay.put(serialize_record(projected), 'application/json')
        native = projected
    route = records['review_route'][route_id or assignment['route_id']]
    logical = protocol.build_packet(records, overlay, assignment['dossier_id'], assignment['role'],
                                    assignment['reviewer_id'], native_packet=reference)
    selected = reading.choose_chain_protocol(records, overlay, route, native, packet=logical, assignment=prospective)
    result = {key: value for key, value in selected.items() if key != 'result'}
    if route['id'] != assignment['route_id']:
        result['route_id'] = route['id']
    if selected['protocol'] in v7.PROTOCOL_DESCRIPTIONS:
        value = {key: prospective[key] for key in ('id', 'role', 'route_id', 'dossier_id', 'reviewer_id', 'author_id', 'context')}
        value['route_id'] = route['id']
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


def choose_math_minimum(step, condition, **bindings):
    """Mark openness as a choice and carry any current evidence requirements in the action."""
    step['payload'].pop('openness', None)
    if 'openness' not in step['chooses']:
        step['chooses'].append('openness')
    step['prerequisites'].insert(0, dict(condition=condition, **bindings))


def _find_math_claim_statement(native):
    """Resolve the retained claim digest from the portable subject and objective contract."""
    pending = [native['contract'], native['subject']]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            statement = item.get('statement')
            if isinstance(statement, str) and digest(item) == native['claim_digest']:
                return statement
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return None


def bind_math_export(step, native, reviewer_id, records, *, fresh_attestation=False, open_minimum=False,
                     checked_export=False):
    """Bind the step to the attested native review, including for turns with projected packets.

    With checked_export, the caller completes the step through the controller's checked export, which binds the
    material searches that a stale minimum lacks: such a minimum stays fixed for that refresh.
    """
    context = native['scientific_context']
    step['payload'].update(kind=native['kind'], subject=copy.deepcopy(native['subject']),
                           claim_digest=native['claim_digest'])
    step.update(root=native['root'], objective_id=native['objective_id'], checkpoint=context.get('checkpoint'),
                reviewer_id=reviewer_id)
    step['prerequisites'].append(dict(condition=MATH_REVIEWER_PREREQUISITE))
    if fresh_attestation:
        step['payload']['reviewer'] = {key: native['reviewer'][key] for key in ('source', 'actor_id')}
        step['chooses'] = ['reviewer.attestation_id', 'inputs']
        step['prerequisites'].append(dict(condition=MATH_FRESH_ATTESTATION))
        continuation = step['then'][0]
        continuation['payload']['reviewer_id'] = reviewer_id
        continuation['chooses'].remove('reviewer_id')
    minimum = context['openness_minimum']
    if open_minimum or not isinstance(minimum, dict) or minimum.get('schema_version') != 1:
        choose_math_minimum(step, v7.MATH_UNITS_PREREQUISITE)
        return
    statement = _find_math_claim_statement(native)
    if statement is None or statement != minimum.get('statement'):
        choose_math_minimum(step, MATH_CLAIM_MINIMUM_PREREQUISITE, claim_digest=native['claim_digest'])
        return
    from .openness_minimum import OpennessError, validate_minimum_bindings
    try:
        validate_minimum_bindings(minimum, statement, records)
    except OpennessError as error:
        if checked_export and error.code == 'openness_search_stale':
            step['payload']['openness'] = copy.deepcopy(minimum)
            return
        bindings = error.details or {}
        step['minimum_refusal'] = dict(cause=error.code, **bindings)
        choose_math_minimum(step, v7.MATH_UNITS_PREREQUISITE, **bindings)
    else:
        step['payload']['openness'] = copy.deepcopy(minimum)


def _describe_successor_step(records, artifacts, assignment, route_id, *, open_minimum=False):
    """The step of a new packet under the same reviewer: row 4 for a problem chain, row 5 for math. Its assignment
    names the route record route_id, or leaves the route to the agent when route_id is None."""
    from .review_plan_steps import describe_step
    packet = protocol._read_json(artifacts, assignment['packet'])
    if reading.find_protocol(packet) in MATH_PROTOCOLS:
        step = describe_step(records, artifacts, assignment, 5, open_minimum=open_minimum)
    else:
        step = describe_step(records, artifacts, assignment, 4)
        step['payload']['reviewer_id'] = assignment['reviewer_id']
        step['chooses'].remove('reviewer_id')
    continuation = step['then'][0]
    # A printed payload keeps the member order of release 0.92.1: the route record before the reviewer.
    if route_id is not None:
        continuation['payload']['route_id'] = route_id
    continuation['payload']['reviewer_id'] = assignment['reviewer_id']
    continuation['chooses'] = [key for key in continuation['chooses'] if key not in continuation['payload']]
    step['then'] = [dict(copy.deepcopy(continuation), check=True), continuation]
    return step


def describe_new_packet_step(records, artifacts, assignment, *, open_minimum=False):
    """The step that starts a new chain in place of a chain whose packet no longer serves it (rules C26 and C31), and
    the renewal: (step, None) with the step of a new packet on the route record that select_step_route gives, or,
    while the store lacks that record or its probe, (step, renewal) with the step that records or probes it
    (describe_renewal_step, followed by review-series on the head) and renewal dict(admitted=True, route_id,
    lacks). A current destination without a verifying probe gives a probe and no runtime renewal."""
    from .review_route_plan import select_step_route
    found = select_step_route(records, artifacts, assignment['route_id'])
    if found['lacks'] is None:
        route_id = found['route_id']
        if route_id == assignment['route_id'] and protocol.route_state(records, artifacts, route_id)[
                'context_status'] != 'verified_for_route':
            from .review_plan_steps import describe_route_probe_step
            step = describe_route_probe_step(records, artifacts, assignment, route_id)
            step['then'] = [dict(command='review-series', assignment_id=assignment['id'])]
            return step, None
        return _describe_successor_step(records, artifacts, assignment, route_id, open_minimum=open_minimum), None
    from .review_plan_steps import describe_renewal_step
    return describe_renewal_step(records, artifacts, assignment, found), dict(admitted=True, **found)


def describe_math_successor_answer(records, artifacts, assignment):
    """Return the answer that starts the admitted successor of a stopped math chain, or None, and the admission.
    While the store lacks the route record of the new chain or its probe, the answer holds the step that records or
    probes it, and review-series follows: the decision then holds the step of the new packet. The admission is stated
    for the record that the step names once that record is verified, and until then for the route record of the
    stopped chain."""
    from .review_route_plan import select_step_route
    found = select_step_route(records, artifacts, assignment['route_id'])
    admitted = admit_successor_chain(records, artifacts, assignment,
                                     found['route_id'] if found['lacks'] is None else None)
    if admitted['protocol'] is None:
        return None, admitted
    if found['lacks'] is not None:
        from .review_plan_steps import describe_renewal_step
        step = describe_renewal_step(records, artifacts, assignment, found)
        return dict(answer=MATH_SUCCESSOR_ANSWER, steps=[step]), admitted
    return dict(answer=MATH_SUCCESSOR_ANSWER,
                steps=[_describe_successor_step(records, artifacts, assignment, found['route_id'])]), admitted


def describe_math_decision(records, artifacts, assignment, decision, *, estimate):
    """Give the projection stop of a math v1 chain the step of its admitted successor in place of the wait for it.

    An estimate keeps its answer "continue" (ruling 24.2 as corrected). An exact measure has no such answer, because
    only the exact measure ends a continued chain. Without an admitted successor the decision stands alone.
    Return the decision and the admission.
    """
    successor, admitted = describe_math_successor_answer(records, artifacts, assignment)
    if (not estimate and successor is not None and admitted['protocol'] == reading.MATH_PROTOCOL
            and successor['steps'][0]['command'] == 'search review-packet'
            and admitted.get('route_id', assignment['route_id']) == assignment['route_id']):
        successor = None
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
    from .review_route_plan import SUCCESSOR_ROUTE_MSG, select_step_route
    found = select_step_route(records, artifacts, assignment['route_id'])
    successor = details.get('successor') or admit_successor_chain(
        records, artifacts, assignment, found['route_id'] if found['lacks'] is None else None)
    if successor is None:
        return None
    details['successor'] = successor
    if successor['protocol'] is None:
        details.update(successor['stop'])
        details.pop('step', None)
        return dict(state='stop', code=code, details=details)
    if found['lacks'] is not None:
        # The store lacks the route record of the new chain or its probe: that step comes before the export, and
        # the plan after it states the admission on the new record.
        from .review_plan_steps import describe_renewal_step
        step = describe_renewal_step(records, artifacts, assignment, found)
        details.update(route_id=assignment['route_id'], renewal=dict(admitted=True, **found),
                       message=SUCCESSOR_ROUTE_MSG, step=step)
        return dict(state='handoff', code='review_route_runtime_stale', command=step['command'], details=details)
    step = _describe_successor_step(records, artifacts, assignment, found['route_id'])
    messages = {v7.PROTOCOL: V7_SUCCESSOR_MESSAGE, reading.PROTOCOL_V6: V6_SUCCESSOR_MESSAGE,
                reading.MATH_PROTOCOL_V2: MATH_V2_SUCCESSOR_MESSAGE, reading.MATH_PROTOCOL: MATH_V1_SUCCESSOR_MESSAGE}
    details.update(step=step, message=messages[successor['protocol']])
    if details.get('ratio'):
        details['ratio_message'] = RATIO_SUCCESSOR_MESSAGE
    return dict(state='handoff', code=code, command=step['command'], details=details)
