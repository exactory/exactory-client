"""Expand packet-local citations and retain bounded reviewer arguments.

Protocol builders supply ``handle_table`` as a mapping from a handle
(u, e, c, t, g, h, n or m) to an entry with ``reference``. That reference is the
exact native citation. An entry used with offsets also holds ``text``, the text
of that delivery, and may hold its ``part``. Split units can additionally hold
``parts``, a mapping from decimal part numbers to entries of the same shape.
A citation's optional ``part`` selects one; otherwise it uses the current entry.
Offsets always address that part's text.
Inventory and chain-state builders own these entries; expansion never looks up
live records. ``build_gap_handles`` numbers the supplied minimum_gaps from zero.

Material packets additionally hold capacity.answer_bound and material_state.
``advance_material`` returns the next state, handle table and material_request;
protocol builders put those members in the next packet. The state holds text
artifact descriptors, never argument bodies. V7 resolves the table and material
state from its bound inventory and state artifacts instead of request members.
"""

import copy
import hashlib

from .artifacts import describe_artifact
from .errors import ResearchError
from .evidence import digest
from .operations import fields, text
from .review_answer_recovery import derive_output, extract_answer_text, recover_answer
from .review_refusal import contract_check
from .source_links import span_locator
from .storage import _canonical


HANDLE_TABLE = 'handle_table'
# A protocol builder can supply the openness minimum that an approval of its packet records (decision 25).
SUPPLIED_MINIMUM = 'supplied_minimum'
EXPANSION_PROTOCOL = 'exactory-review-handle-expansion-v1'
MATERIAL_PART_LIMIT = 8
INVALID_REFERENCE_HANDLE_MESSAGE = (
    'A citation of this answer is not valid; details.path names its place. A citation is {ref}, or {ref,start,end} '
    'for a span, each with an optional part. ref is a handle of handle_table in this packet. part is a positive '
    'integer that names a delivered part of a split unit. start and end are integer character offsets in the text '
    'that this packet delivers for that handle or part, with 0 <= start < end <= the length of that text, and '
    'only a handle of a source takes a span. Correct the citation.'
)
MATERIAL_READING_TURN_MESSAGE = (
    'A part of an argument is accepted only as the answer of a final turn, and this is a reading turn. Return '
    'the reading response of this turn, and put the outline of the argument into your notes. The final turn '
    'takes the argument.')
INVALID_MATERIAL_MESSAGE = (
    'The answer is not the part of an argument that this turn expects. Return {stage:"material",title,part,text,more}. '
    'part is the next part number, which starts at 1, and a later part keeps the title of the first part. text has '
    'at most the characters of the answer bound of this turn. more is true while a part follows and false in the '
    'last part, and an argument has at most eight parts. While material_state.pending names an argument, the answer '
    'of the turn is its next part.'
)


def _refuse_handle(path, handle):
    raise ResearchError('invalid_reference_handle', INVALID_REFERENCE_HANDLE_MESSAGE,
                        {'path': path, 'ref': handle})


def _expand_value(table, value, path, artifacts=None):
    if isinstance(value, list):
        expanded, count = [], 0
        for index, item in enumerate(value):
            item, references = _expand_value(table, item, path + '/' + str(index), artifacts)
            expanded.append(item)
            count += references
        return expanded, count
    if not isinstance(value, dict):
        return value, 0
    if 'ref' in value:
        handle = value['ref']
        entry = table.get(handle) if isinstance(handle, str) else None
        if entry is None or set(value) not in (
                {'ref'}, {'ref', 'part'}, {'ref', 'start', 'end'}, {'ref', 'part', 'start', 'end'}):
            _refuse_handle(path, handle)
        if 'part' in value:
            part = value['part']
            if type(part) is not int or part < 1:
                _refuse_handle(path, handle)
            entry = entry if entry.get('part') == part else entry.get('parts', {}).get(str(part))
            if entry is None:
                _refuse_handle(path, handle)
        reference = copy.deepcopy(entry['reference'])
        if 'start' in value or entry.get('cite_part_span'):
            reference = copy.deepcopy(entry.get('span_reference', reference))
            content = entry.get('text')
            start, end = value.get('start', 0), value.get('end', len(content) if isinstance(content, str) else 0)
            if (type(start) is not int or type(end) is not int or not isinstance(content, str)
                    or not 0 <= start < end <= len(content)):
                _refuse_handle(path, handle)
            unit_start = entry.get('unit_offset', 0)
            component = next((component for component in entry.get('span_components', [])
                              if component['start'] <= unit_start + start < unit_start + end <= component['end']), None)
            if component is not None and not entry.get('native_digest'):
                reference = copy.deepcopy(component['reference'])
                reference['link']['locator'] = span_locator(component['text'],
                    unit_start + start - component['start'], unit_start + end - component['start'], excerpt_length=0)
                if entry.get('bare_source_link'):
                    reference = reference['link']
            elif entry.get('artifact_spans'):
                data = content[start:end].encode('utf-8')
                media = 'text/plain; profile=exactory-review-parts-span-v3'
                reference = describe_artifact(data, media)
                if artifacts is not None:
                    artifacts.put(data, media)
                if entry.get('native_digest'):
                    reference = reference['sha256']
            else:
                if reference.get('kind') != 'source':
                    _refuse_handle(path, handle)
                offset = entry.get('source_start', 0)
                reference['link']['locator'] = span_locator(entry.get('source_text', content),
                                                           offset + start, offset + end, excerpt_length=0)
        if artifacts is not None and entry.get('store_text'):
            artifacts.put(entry.get('source_text', entry['text']).encode('utf-8'), 'application/json')
        return reference, 1
    expanded, count = {}, 0
    for name, item in value.items():
        item_path = path + '/' + name.replace('~', '~0').replace('/', '~1')
        expanded[name], references = _expand_value(table, item, item_path, artifacts)
        count += references
    return expanded, count


def _build_v7_handle_table(packet, artifacts, assignment):
    from .openness_reading import find_protocol, BOUNDED_CHAIN_PROTOCOLS
    if find_protocol(packet) not in BOUNDED_CHAIN_PROTOCOLS:
        return packet
    from .openness_reading_v7 import build_handle_packet
    return build_handle_packet(packet, artifacts, assignment)


def _build_parts_v3_handle_packet(packet, artifacts, assignment):
    from .review_parts_v3 import build_handle_packet, PROTOCOL
    if packet.get('review_parts', {}).get('protocol') != PROTOCOL:
        return packet
    return build_handle_packet(packet, artifacts, assignment)


def _build_session_handles(packet, artifacts, assignment):
    from .review_session_v4 import build_handle_packet
    return build_handle_packet(packet, artifacts, assignment)


# Rows of later planned chain families use this same (packet, artifacts,
# assignment) -> transient handle packet interface. Tables never travel.
HANDLE_TABLE_BUILDERS = {
    'reading_turn': _build_v7_handle_table,
    'reading_final': _build_v7_handle_table,
    'reading_combine': _build_v7_handle_table,
    'review_parts': _build_parts_v3_handle_packet,
    'session_turn': _build_session_handles,
    'session_final': _build_session_handles,
    'session_combine': _build_session_handles,
}


def _resolve_packet(packet, artifacts=None, assignment=None):
    if HANDLE_TABLE in packet:
        return packet
    for member, builder in HANDLE_TABLE_BUILDERS.items():
        if member in packet:
            resolved = builder(packet, artifacts, assignment)
            if HANDLE_TABLE in resolved:
                return resolved
    return packet


def _validate_v7_refs(packet, output, *, raw):
    if 'reading_final' not in packet or not isinstance(output, dict):
        return
    findings = output.get('findings')
    openness = findings.get('openness') if isinstance(findings, dict) else None
    # Each place holds a list of grounds; a refused reference names its ground, and for a page its target.
    located = [('/findings/openness/grounds', openness.get('grounds') if isinstance(openness, dict) else None)]
    if 'page_plan_digest' in packet['reading_final']:
        if 'closing' in packet['reading_final']:
            located = [('/grounds', output.get('grounds'))]
        elif isinstance(output.get('targets'), list):
            located = [('/targets/%d/grounds' % index, target.get('grounds'))
                       for index, target in enumerate(output['targets']) if isinstance(target, dict)]
    for place, grounds in located:
        for ground_index, ground in enumerate(grounds if isinstance(grounds, list) else []):
            references = ground.get('refs') if isinstance(ground, dict) else None
            if not isinstance(references, list):
                continue  # The role validator reports the malformed container.
            for reference_index, reference in enumerate(references):
                if not raw and isinstance(reference, dict) and 'ref' not in reference:
                    continue  # Already expanded authenticated output.
                handle = reference.get('ref') if isinstance(reference, dict) else None
                if (not isinstance(reference, dict) or set(reference) != {'ref'}
                        or not isinstance(handle, str) or handle[:1] not in (
                            ('g', 'h', 'n', 'm', 'u', 'x') if packet['reading_final'].get('unread')
                            else ('g', 'h', 'n', 'm'))):
                    _refuse_handle('%s/%d/refs/%d' % (place, ground_index, reference_index), handle)


def expand_handles(packet, output, *, artifacts=None, assignment=None, raw=False):
    """Expand citations before role validation, preserving legacy outputs exactly."""
    packet = _resolve_packet(packet, artifacts, assignment)
    if HANDLE_TABLE not in packet:
        return output
    with contract_check('review.shape'):
        _validate_v7_refs(packet, output, raw=raw)
        if packet.get('material_state', {}).get('pending') and not is_material_output(packet, output):
            raise ResearchError('invalid_review_material', INVALID_MATERIAL_MESSAGE,
                                {'path': '/stage', 'pending': packet['material_state']['pending']})
        return _supply_minimum(packet, _expand_value(packet[HANDLE_TABLE], output, '', artifacts)[0])


def describe_expansion(packet, output, response, *, artifacts=None, assignment=None):
    """Bind expanded native citations to the exact provider answer text."""
    packet = _resolve_packet(packet, artifacts, assignment)
    if HANDLE_TABLE not in packet:
        return output, None
    with contract_check('review.shape'):
        _validate_v7_refs(packet, output, raw=True)
        expanded, count = _expand_value(packet[HANDLE_TABLE], output, '', artifacts)
        expanded = _supply_minimum(packet, expanded)
    answer = extract_answer_text(response)
    certificate = dict(protocol=EXPANSION_PROTOCOL, references=count,
                       answer_sha256=hashlib.sha256(answer.encode('utf-8')).hexdigest(),
                       output_sha256=digest(expanded))
    return expanded, certificate


def record_handle_output(packet, observation, artifacts, *, assignment=None):
    """Retain expansion and material state in the durable transport capture."""
    packet = _resolve_packet(packet, artifacts, assignment)
    if observation['status'] != 'completed' or HANDLE_TABLE not in packet:
        return observation
    from .review_protocol import _put_json, _read_json
    output = _read_json(artifacts, observation['output'])
    try:
        expanded, certificate = describe_expansion(packet, output, _read_json(artifacts, observation['response']),
                                                  artifacts=artifacts, assignment=assignment)
    except ResearchError as error:
        if error.code != 'invalid_reference_handle':
            raise
        # Keep the raw answer so authentication and the retry path can repeat
        # its shape refusal without losing the completed provider call.
        return observation
    result = dict(observation, output=_put_json(artifacts, expanded), handle_expansion=certificate)
    if is_material_output(packet, expanded):
        try:
            validate_material(packet, expanded)
        except ResearchError:
            return result  # The output contract reports the retained invalid part.
        result['material'] = _put_json(artifacts, advance_material(packet, expanded, artifacts))
    return result


def derive_expanded_output(packet, observation, response, *, artifacts=None, assignment=None):
    """Repeat answer recovery and handle expansion when authenticating an attempt."""
    source = observation
    if 'handle_expansion' in observation and 'answer_recovery' in observation:
        recovered = recover_answer(response)
        source = dict(observation, output=recovered['output'] if recovered else None)
    raw = derive_output(source, response)
    try:
        expanded, certificate = describe_expansion(packet, raw, response, artifacts=artifacts, assignment=assignment)
    except ResearchError as error:
        # A refused raw answer still has authentic provenance. Its contract
        # check must be able to repeat the refusal rather than lose the call.
        if error.code == 'invalid_reference_handle' and 'handle_expansion' not in observation:
            if digest(raw) != digest(observation['output']):
                raise ResearchError('review_invocation_unverified', 'The refused handle answer must match the original response.')
            return raw
        raise
    if (_canonical(certificate) != _canonical(observation.get('handle_expansion'))
            or certificate is not None and digest(observation['output']) != certificate['output_sha256']):
        raise ResearchError('review_invocation_unverified', 'The stored handle expansion must match the original answer and packet.')
    return expanded


def build_gap_handles(minimum_gaps, artifacts):
    """Bind g<n> to exactly position n of the supplied list, including duplicates."""
    return {'g' + str(index): {'reference': artifacts.put(_canonical(gap).encode('utf-8'), 'application/json')}
            for index, gap in enumerate(minimum_gaps)}


def is_material_output(packet, output):
    from .openness_reading import find_protocol, BOUNDED_CHAIN_PROTOCOLS
    return (HANDLE_TABLE in packet or 'session_turn' in packet or 'session_final' in packet
            or find_protocol(packet) in BOUNDED_CHAIN_PROTOCOLS
            or packet.get('review_parts', {}).get('protocol') == 'exactory-review-parts-v3') and isinstance(output, dict) and output.get('stage') == 'material'


def validate_material(packet, output, *, artifacts=None, assignment=None):
    """Check a material part once at the output-contract boundary."""
    from .openness_reading import BOUNDED_CHAIN_PROTOCOLS
    packet = _resolve_packet(packet, artifacts, assignment) if HANDLE_TABLE not in packet else packet
    with contract_check('review.shape'):
        if ('session_turn' in packet or packet.get('reading_turn', {}).get('protocol') in BOUNDED_CHAIN_PROTOCOLS
                or packet.get('review_parts', {}).get('protocol') == 'exactory-review-parts-v3'
                and packet['review_parts']['kind'] != 'final'):
            raise ResearchError('invalid_review_material', MATERIAL_READING_TURN_MESSAGE, dict(path='/stage'))
        fields(output, ('stage', 'title', 'part', 'text', 'more'), code='invalid_review_material', path='')
        for name in ('title', 'text'):
            text(output[name], 'Material ' + name, code='invalid_review_material')
        if 'session_final' in packet or any(packet.get(key, {}).get('protocol') in BOUNDED_CHAIN_PROTOCOLS
               for key in ('reading_turn', 'reading_final')) or packet.get('review_parts', {}).get('protocol') == 'exactory-review-parts-v3':
            if len(output['title']) > 120:
                raise ResearchError('invalid_review_material', INVALID_MATERIAL_MESSAGE,
                                    dict(path='/title', maximum_characters=120))
        state = packet.get('material_state', {'arguments': {}, 'pending': None})
        pending = state['pending']
        part = output['part']
        expected = pending['part'] if pending else 1
        if (output['stage'] != 'material' or type(part) is not int or part != expected or part > MATERIAL_PART_LIMIT
                or type(output['more']) is not bool or part == MATERIAL_PART_LIMIT and output['more']
                or len(output['text']) > packet['capacity']['answer_bound']
                or pending and output['title'] != state['arguments'][pending['ref']]['title']):
            raise ResearchError('invalid_review_material', INVALID_MATERIAL_MESSAGE,
                                {'path': '', 'expected_part': expected, 'maximum_parts': MATERIAL_PART_LIMIT,
                                 'answer_bound': packet['capacity']['answer_bound']})


def advance_material(packet, output, artifacts):
    """Store one argument part and describe the next material turn or final."""
    state = copy.deepcopy(packet.get('material_state', {'arguments': {}, 'pending': None}))
    table = copy.deepcopy(packet[HANDLE_TABLE])
    pending = state['pending']
    handle = pending['ref'] if pending else 'm' + str(len(state['arguments']) + 1)
    argument = state['arguments'].setdefault(handle, dict(title=output['title'], parts=[], complete=False))
    argument['parts'].append(artifacts.put(output['text'].encode('utf-8'), 'text/plain; charset=utf-8'))
    argument['complete'] = not output['more']
    state['pending'] = dict(ref=handle, part=output['part'] + 1) if output['more'] else None
    if argument['complete']:
        content = b''.join(artifacts.read(part) for part in argument['parts'])
        table[handle] = dict(reference=artifacts.put(content, 'text/plain; charset=utf-8'))
    request = (dict(stage='material', title=argument['title'], **state['pending'])
               if state['pending'] else dict(stage='final', ref=handle))
    return dict(material_state=state, handle_table=table, material_request=request)


def authenticate_material(packet, output, material, artifacts, *, assignment=None):
    """Rebuild stored argument descriptors from the authenticated part bytes."""
    packet = _resolve_packet(packet, artifacts, assignment)
    try:
        validate_material(packet, output)
    except ResearchError:
        return material is None
    # Describe the current part without writing during authentication. The
    # content-addressed store already retains every artifact that this uses.
    class ReadOnlyArtifacts:
        def put(self, data, media_type):
            reference = describe_artifact(data, media_type)
            artifacts.read(reference)
            return reference

        def read(self, reference):
            return artifacts.read(reference)

    return _canonical(advance_material(packet, output, ReadOnlyArtifacts())) == _canonical(material)


def _supply_minimum(packet, output):
    """Set the supplied minimum on an approval; the role validator reports a malformed approval."""
    findings = output.get('findings') if isinstance(output, dict) and output.get('decision') == 'approve' else None
    openness = findings.get('openness') if isinstance(findings, dict) else None
    if SUPPLIED_MINIMUM in packet and isinstance(openness, dict):
        openness['minimum'] = copy.deepcopy(packet[SUPPLIED_MINIMUM])
    return output
