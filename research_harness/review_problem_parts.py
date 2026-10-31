"""Deliver oversized problem result evidence through observed, indexed part turns."""

import copy
import json

from . import review_excerpts, review_transport as transport
from .direction_sources import has_attachment
from .errors import ResearchError, describe_error_at
from .evaluation import Evaluation
from .evidence import digest
from .operations import fields, normalized_text, text

PROTOCOL = 'exactory-problem-evidence-parts-v1'
KEY = 'problem_evidence_parts'
STAGES = ('part', 'final')
SPLIT_KEYS = ('openness_searches', 'assessment_evidence', 'readings', 'passages', 'computations', 'formal_checks')
NOTES_CHARACTERS = 60000
PART_UNIT_CHARACTERS = 800000
PROMPTS = {
    'part': (
        ' Problem evidence parts of this session. The problem evidence is too large for one input of this route, '
        'so it reaches you in part turns and then one final turn. problem_evidence_parts describes the turns: '
        'stage is "part" or "final", turn is the number of this turn, delivered_now lists the items that arrive '
        'now, delivered_before lists the items of each earlier turn with its assignment_id and attempt_id, and '
        'remaining lists the items still to come. Every turn repeats the frame of problem_evidence: claim, '
        'openness, value, assessment, attempt, results, citations and delivery. The items of its other lists '
        '(openness_searches, assessment_evidence, readings, passages, computations and formal_checks) are the '
        'units. A turn carries only its share of the units, and no later turn delivers them again, with one '
        'exception. A turn that carries passages of a reading whose entry in readings an earlier turn delivered '
        'carries that entry again, and delivered_now.readings lists the index of that reading again, so that you '
        'have the entry to note those passages under. A unit is named by its key and its index in the full list '
        'of that key; delivered_now gives index ranges [start, end] per key, start included and end excluded, and'
        " this turn's items of that key stand in that order.\n\nEach turn is a fresh input that replays the earlier"
        ' turns before it. A later turn can leave an earlier packet out of that replay to fit the route limit, '
        'but it keeps your own responses. The final assessment relies on your notes and verified excerpts for the'
        ' units of this turn.\n\nThis is a part turn. Do not assess the problem. Read everything delivered now. '
        'Return exactly '
        '{"stage":"result","phase":"evidence_part","part":<problem_evidence_parts.turn>,"notes":[{"unit":{"key":"<key>","index":<index>},"note":"..."}],"excerpts":[...]}'
        ' and not the result review. notes has exactly one entry for every unit delivered now except a passage '
        'and no entry for any other unit; the entries can be in any order. Note a passage in the entry of the '
        'reading that its reading_id names. Each note is nonblank text, and the note texts together have at most '
        '60,000 characters. A note keeps what the final assessment needs from the unit for the five assurances '
        'and the rubric items, and names each record of the unit by the record_kind and id of its cite object or '
        'record reference, so that the final turn can cite it. excerpts is optional and follows the excerpt rules'
        ' above; an excerpt can copy from a passage, named by its version_id. A part response carries no '
        'source_requests. Request a source or a passage in the final turn.'),
    'final': (
        ' Problem evidence parts of this session. This is the final turn of a result review whose problem '
        'evidence was delivered in parts. The units (the items of openness_searches, assessment_evidence, '
        'readings, passages, computations and formal_checks) reached you in the earlier turns that '
        'problem_evidence_parts.delivered_before lists, each turn with its assignment_id, its attempt_id and its '
        'units. This turn carries the frame of problem_evidence (claim, openness, value, assessment, attempt, '
        'results, citations and delivery) and no units: every other list of problem_evidence is empty, and '
        'delivered_now and remaining list no items. An empty list in this turn is not a part that the record '
        'lacks; the units of the record reached you in the earlier turns. Each turn is a fresh input that replays'
        ' the earlier turns before it. That replay holds your responses of the earlier turns with your notes, and'
        ' reviewer_excerpts holds your verified excerpts. An earlier packet can be absent from the replay to fit '
        'the route limit, and an absent packet is not available to you.\n\nAssess the problem now from the frame, '
        'your notes and verified excerpts of the earlier turns, and any earlier packet that the replay still '
        'holds. Cite a record by the cite object that a turn of this session delivered. Cite a source span only '
        'within what a turn of this session delivered. Return the result review defined above. When a decisive '
        'source is missing, return source_requests under the rules above instead; the turn that answers them '
        'carries the frame and your deliveries, and again no units.'),
}


def find_stage(packet):
    plan = packet.get(KEY) or {}
    return plan.get('stage') if plan.get('protocol') == PROTOCOL and plan.get('stage') in STAGES else None


def _ordered_items(evidence):
    for key in SPLIT_KEYS[:2]:
        for index, item in enumerate(evidence.get(key, [])):
            yield key, index, item
    passages = evidence.get('passages', [])
    used = set()
    for index, item in enumerate(evidence.get('readings', [])):
        yield 'readings', index, item
        for passage_index, passage in enumerate(passages):
            if passage.get('reading_id') == item['id']:
                used.add(passage_index)
                yield 'passages', passage_index, passage
    for index, item in enumerate(passages):
        if index not in used:
            yield 'passages', index, item
    for key in SPLIT_KEYS[4:]:
        for index, item in enumerate(evidence.get(key, [])):
            yield key, index, item


def _owned_artifacts(key, item):
    from .review_protocol import collect_references
    owned = []
    if key in ('computations', 'formal_checks'):
        owned = [item]
    elif key == 'assessment_evidence':
        if 'kind' not in item:
            owned = [item]
        elif item.get('kind') == 'record' and item.get('record_kind') == 'local_artifact':
            owned = [item['record']['artifact']]
    references = {digest(reference): reference for reference in collect_references(owned)}
    return [references[key] for key in sorted(references)]


def list_units(artifacts, evidence):
    """The indivisible items, in evidence order, with their original indices and required artifact bodies."""
    from .review_protocol import _evidence
    units = []
    for key, index, item in _ordered_items(evidence):
        references = _owned_artifacts(key, item)
        bodies = _evidence(artifacts, references)
        characters = len(json.dumps(item, sort_keys=True)) + sum(len(json.dumps(body, sort_keys=True)) for body in bodies)
        units.append({'key': key, 'index': index, 'digest': digest(item), 'item': item,
                      'artifacts': references, 'characters': characters})
    return units


def _identity(unit):
    return {key: unit[key] for key in ('key', 'index', 'digest')}


def _ranges(units):
    result = {key: [] for key in SPLIT_KEYS}
    for key in SPLIT_KEYS:
        for index in sorted({unit['index'] for unit in units if unit['key'] == key}):
            if result[key] and result[key][-1][1] == index:
                result[key][-1][1] += 1
            else:
                result[key].append([index, index + 1])
    return result


def _indices(ranges, count):
    indices = []
    for span in ranges:
        if (not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span)
                or not 0 <= span[0] < span[1] <= count or indices and span[0] <= indices[-1]):
            raise ValueError('Problem evidence unit ranges must be ordered, disjoint and within the frozen list')
        indices.extend(range(*span))
    return indices


def _delivered_units(packet):
    plan, evidence = packet[KEY], packet['problem_evidence']
    positions = {key: _indices(plan['delivered_now'][key], plan['units'][key]) for key in SPLIT_KEYS}
    if any(len(positions[key]) != len(evidence[key]) for key in SPLIT_KEYS):
        raise ValueError('Every delivered item must have its exact original index')
    return [{'key': key, 'index': positions[key][index], 'digest': digest(item), 'item': item}
            for key, index, item in _ordered_items(evidence)]


def validate_part_output(packet, output):
    """Validate exactly one note per delivered record or reading, in any order; passages belong to their reading."""
    code = 'invalid_value_review'
    fields(output, ('stage', 'phase', 'part', 'notes'), ('excerpts',), code=code, path='')
    for key, expected in (('stage', 'result'), ('phase', 'evidence_part'), ('part', packet[KEY]['turn'])):
        if output[key] != expected or key == 'part' and type(output[key]) is not int:
            raise ResearchError(code, 'A part response must name its exact result evidence turn', {'path': '/' + key})
    expected = {(unit['key'], unit['index']): {key: unit[key] for key in ('key', 'index')}
                for unit in _delivered_units(packet) if unit['key'] != 'passages'}
    notes = output['notes']
    message = 'Supply one note for every delivered record and reading unit'
    if not isinstance(notes, list):
        raise ResearchError(code, message, {'path': '/notes', 'expected_type': 'array'})
    characters = 0
    seen = {}
    for index, entry in enumerate(notes):
        path = '/notes/' + str(index)
        fields(entry, ('unit', 'note'), code=code, path=path)
        fields(entry['unit'], ('key', 'index'), code=code, path=path + '/unit')
        unit = entry['unit']
        if type(unit['index']) is not int:
            raise ResearchError(code, message, {'path': path + '/unit/index', 'expected_type': 'integer'})
        identity = (unit['key'], unit['index'])
        if not isinstance(unit['key'], str) or identity not in expected:
            raise ResearchError(code, message, {'path': path + '/unit', 'reason': 'unknown_unit', 'unit': unit})
        if identity in seen:
            raise ResearchError(code, message, {'path': path + '/unit', 'reason': 'duplicate_unit',
                                               'unit': unit, 'duplicate_of': seen[identity]})
        seen[identity] = path + '/unit'
        with describe_error_at(path + '/note'):
            text(entry['note'], 'Evidence unit note', code=code)
        characters += len(entry['note'])
    missing = next((unit for identity, unit in expected.items() if identity not in seen), None)
    if missing is not None:
        raise ResearchError(code, message, {'path': '/notes', 'reason': 'missing_unit', 'missing_unit': missing})
    if characters > NOTES_CHARACTERS:
        raise ResearchError(code, 'The evidence part notes exceed their character allowance',
                            {'path': '/notes', 'characters': characters, 'maximum_characters': NOTES_CHARACTERS})
    if 'excerpts' in output:
        review_excerpts.validate_excerpts(output['excerpts'], code)


def _read_packet(artifacts, assignment):
    return json.loads(artifacts.read(assignment['packet']))


def _list_series(records, artifacts, assignment_id):
    assignments = records.get('review_assignment', {})
    latest = assignments.get(assignment_id)
    if latest is None:
        return []
    packet = _read_packet(artifacts, latest)
    if find_stage(packet) is None:
        return []
    series_id, evidence_digest = packet[KEY]['series_assignment_id'], packet['problem_evidence_digest']
    chain, seen = [], set()
    current = latest
    while current is not None and current['id'] not in seen:
        seen.add(current['id'])
        packet = _read_packet(artifacts, current)
        if (find_stage(packet) is None or packet[KEY]['series_assignment_id'] != series_id
                or packet.get('problem_evidence_digest') != evidence_digest
                or any(current.get(key) != latest.get(key) for key in ('role', 'route_id', 'subject', 'slot_id', 'session_id'))
                or any(normalized_text(current[key]) != normalized_text(latest[key]) for key in ('reviewer_id', 'author_id'))):
            break
        chain.append((current, packet))
        if current['id'] == series_id:
            return list(reversed(chain))
        current = assignments.get(current.get('history_assignment_id'))
    return []


def _sent_packet(records, artifacts, assignment, attempt):
    from .review_input_projection import packets_from_request
    request = json.loads(artifacts.read(attempt['request']))
    route = records['review_route'][assignment['route_id']]
    packets = packets_from_request(request, native_attachments=route['adapter'] == 'openai_responses_v1')
    packet = packets[-1] if packets else {}
    if (packet.get('digest') != assignment['packet_digest']
            or any(packet.get(key) != assignment.get(key) for key in ('role', 'reviewer_id', 'subject', 'dossier_id'))):
        return None
    return packet


def observe_series(records, artifacts, assignment_id):
    """Observe valid ready turns from their selected authenticated requests, checking each receipt against its past."""
    from .review_protocol import assignment_state
    observations = []
    evaluation = Evaluation.of(records, artifacts)
    for assignment, frozen in _list_series(records, evaluation, assignment_id):
        state = assignment_state(records, evaluation, assignment['id'])
        attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
        if not state['ready'] or attempt is None or attempt.get('origin') != 'instrumented':
            continue
        try:
            packet = _sent_packet(records, evaluation, assignment, attempt)
            if packet is None or packet.get(KEY) != frozen[KEY]:
                continue
            plan = packet[KEY]
            if (plan['turn'] != len(observations) + 1
                    or plan['delivered_before'] != [row['receipt'] for row in observations]):
                continue
            if find_stage(packet) == 'part':
                validate_part_output(packet, state['output'])
            units = _delivered_units(packet)
            if any(not has_attachment(evaluation, packet, reference) for unit in units
                   for reference in _owned_artifacts(unit['key'], unit['item'])):
                continue
            receipt = {'turn': plan['turn'], 'assignment_id': assignment['id'], 'attempt_id': attempt['id'],
                       'units': _ranges(units)}
            observations.append({'packet': packet, 'receipt': receipt, 'units': units})
        except (ResearchError, ValueError, TypeError, KeyError, UnicodeError):
            continue
    return observations


def collect_series_packets(records, artifacts, assignment_id):
    """The actual current packet of every valid ready turn of this assignment's series, oldest first."""
    try:
        return [row['packet'] for row in observe_series(records, artifacts, assignment_id)]
    except (ResearchError, ValueError, TypeError, KeyError, UnicodeError):
        return []


def collect_contract_packets(records, artifacts, assignment):
    """Actual inputs for a series output check, without selecting the current attempt recursively during refusal."""
    from .review_input_projection import packets_from_request
    from .review_protocol import authenticate_attempt
    prior_id = assignment.get('history_assignment_id')
    earlier = collect_series_packets(records, artifacts, prior_id) if prior_id else []
    for attempt in sorted(records.get('review_attempt', {}).values(), key=lambda row: row['recorded_revision']):
        if (attempt['assignment_id'] != assignment['id'] or attempt.get('status') != 'completed'
                or not authenticate_attempt(records, artifacts, assignment, attempt)):
            continue
        request = json.loads(artifacts.read(attempt['request']))
        packets = packets_from_request(request)
        return list(reversed(packets)) + list(reversed(earlier))
    return []


def _with_readings(units, selected):
    reading_ids = {unit['item'].get('reading_id') for unit in selected if unit['key'] == 'passages'}
    identities = {(unit['key'], unit['index']) for unit in selected}
    return [unit for unit in units if (unit['key'], unit['index']) in identities
            or unit['key'] == 'readings' and unit['item']['id'] in reading_ids]


def _project(records, artifacts, packet, units, selected, observations, remaining, series_id, stage, turn):
    from .review_protocol import _evidence
    candidate = {key: copy.deepcopy(value) for key, value in packet.items() if key not in ('evidence', 'digest')}
    evidence = candidate['problem_evidence']
    selected = _with_readings(units, selected)
    for key in SPLIT_KEYS:
        evidence[key] = [unit['item'] for unit in sorted(selected, key=lambda unit: unit['index']) if unit['key'] == key]
    candidate[KEY] = {'protocol': PROTOCOL, 'series_assignment_id': series_id, 'stage': stage, 'turn': turn,
        'units': {key: len(packet['problem_evidence'].get(key, [])) for key in SPLIT_KEYS},
        'delivered_before': [row['receipt'] for row in observations], 'delivered_now': _ranges(selected),
        'remaining': _ranges(remaining),
        'note': 'The problem evidence of this result review is delivered in parts over the turns of this session; the instructions of the session define the part turn, the final turn and the response of each.'}
    if review_excerpts.KEY not in candidate:
        candidate[review_excerpts.KEY] = {'protocol': review_excerpts.PROTOCOL, 'excerpts': [],
                                        'allowances': review_excerpts.create_allowances()}
    with transport.text_only_delivery(True):
        candidate['evidence'] = _evidence(artifacts, candidate, records=records)
    candidate['digest'] = digest(candidate)
    return candidate


def _measure(route, request):
    return len(transport._local_adapter(route).packet_text(request))


def _fits(artifacts, route, prompt, packet, history, selected):
    from .review_source_capacity import MARKER
    request = transport.request_body(route, prompt['text'], packet, history)
    characters = _measure(route, request)
    sent = review_excerpts.read_sent_packet(request) or {}
    complete = all(has_attachment(artifacts, sent, reference) for unit in selected for reference in unit['artifacts'])
    fits = characters <= transport.local_input_limit(route) and complete
    if find_stage(packet) == 'part':
        fits = fits and MARKER not in sent
    return fits, characters


def prepare_packet(records, artifacts, value, route, packet, prompt, history):
    """Leave a fitting ordinary packet untouched, or plan the next bounded part and an eventual empty final."""
    from .review_protocol import canonical_prompt
    if (value['role'] != 'result' or packet.get('subject', {}).get('kind') != 'problem'
            or 'context_repair' in packet or value['context'].get('source_delivery')
            or transport.local_input_limit(route) is None or 'problem_evidence' not in packet
            or packet['problem_evidence'].get('delivery') == 'delivered-in-an-earlier-turn'):
        return packet
    artifacts = Evaluation.of(records, artifacts)
    prior_id = value['context'].get('prior_assignment_id')
    previous = _read_packet(artifacts, records['review_assignment'][prior_id]) if prior_id else {}
    refreshed = any(review['assignment_id'] == prior_id for review in records.get('value_review', {}).values())
    continuing = (find_stage(previous) is not None and not refreshed
                  and previous.get('problem_evidence_digest') == packet['problem_evidence_digest'])
    if find_stage(previous) is None:
        ordinary = transport.request_body(route, prompt['text'], packet, history)
        if _measure(route, ordinary) <= transport.local_input_limit(route):
            return packet
    observations = observe_series(records, artifacts, prior_id) if continuing else []
    series_id = previous[KEY]['series_assignment_id'] if continuing else value['id']
    units = list_units(artifacts, packet['problem_evidence'])
    delivered = {(unit['key'], unit['index'], unit['digest']) for row in observations for unit in row['units']}
    remaining = [unit for unit in units if (unit['key'], unit['index'], unit['digest']) not in delivered]
    stage, turn = ('part' if remaining else 'final'), len(observations) + 1
    stage_prompt = canonical_prompt('result', orchestration='problem_creation', excerpts=True, evidence_parts=stage)
    def candidate(count):
        return _project(records, artifacts, packet, units, remaining[:count], observations, remaining[count:],
                        series_id, stage, turn)
    frame = candidate(0)
    _, frame_characters = _fits(artifacts, route, stage_prompt, frame, history, [])
    if not remaining:
        fits, characters = _fits(artifacts, route, stage_prompt, frame, history, [])
        if fits:
            return frame
        count = 0
    else:
        room = min(PART_UNIT_CHARACTERS, max(0, transport.local_input_limit(route) - frame_characters))
        count, used = 0, 0
        for unit in remaining:
            if used + unit['characters'] > room:
                break
            count, used = count + 1, used + unit['characters']
        count = max(1, count)
        while count:
            selected = _with_readings(units, remaining[:count])
            planned = candidate(count)
            fits, characters = _fits(artifacts, route, stage_prompt, planned, history, selected)
            if fits and sum(unit['characters'] for unit in selected) <= PART_UNIT_CHARACTERS:
                break
            count -= 1
        if count:
            while count < len(remaining):
                selected = _with_readings(units, remaining[:count + 1])
                if sum(unit['characters'] for unit in selected) > PART_UNIT_CHARACTERS:
                    break
                larger = candidate(count + 1)
                fits, _ = _fits(artifacts, route, stage_prompt, larger, history, selected)
                if not fits:
                    break
                count, planned = count + 1, larger
            return planned
    raise ResearchError('review_input_too_large', 'The problem evidence frame and next indivisible unit cannot be '
                        'delivered complete within this turn\'s input and unit character limits',
                        {'stage': stage, 'turn': turn, 'input_characters': characters,
                         'input_limit_characters': transport.local_input_limit(route),
                         'frame_characters': frame_characters, 'unit': _identity(remaining[0]) if remaining else None})


def verified_coverage_packets(records, artifacts, assignment_id, packets):
    """Check the final receipt against the selected attempts represented by the collected packets."""
    from .review_protocol import assignment_state
    assignment = records['review_assignment'][assignment_id]
    frozen = _read_packet(artifacts, assignment)
    if find_stage(frozen) != 'final':
        return []
    plan = frozen[KEY]
    current = [packet for packet in packets if packet.get('problem_evidence_digest') == frozen['problem_evidence_digest']
               and packet.get(KEY, {}).get('series_assignment_id') == plan['series_assignment_id']]
    by_digest = {packet.get('digest'): packet for packet in current}
    final = by_digest.get(assignment['packet_digest'])
    if final is None or final.get(KEY) != plan:
        return []
    expected = []
    for row in plan['delivered_before']:
        prior = records.get('review_assignment', {}).get(row['assignment_id'])
        if prior is None:
            return []
        packet = by_digest.get(prior['packet_digest'])
        state = assignment_state(records, artifacts, prior['id'])
        if packet is None or not state['ready']:
            return []
        expected.append({'turn': packet[KEY]['turn'], 'assignment_id': prior['id'],
                         'attempt_id': state['attempt_id'], 'units': _ranges(_delivered_units(packet))})
    return current if expected == plan['delivered_before'] else []


def covers_items(packets, expected, key):
    """Equal duplicate items still require delivery at each distinct expected index."""
    delivered = {}
    for packet in packets:
        for unit in _delivered_units(packet):
            if unit['key'] == key:
                delivered.setdefault(unit['index'], []).append(unit['item'])
    return all(any(item == candidate for candidate in delivered.get(index, []))
               for index, item in enumerate(expected[key]))
