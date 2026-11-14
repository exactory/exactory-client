"""Expand packet-local citations and retain bounded reviewer arguments.

The future protocol builders supply ``handle_table`` as a mapping from a handle
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
artifact descriptors, never argument bodies. No current packet builder opts in.
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
EXPANSION_PROTOCOL = 'exactory-review-handle-expansion-v1'
MATERIAL_PART_LIMIT = 8
INVALID_REFERENCE_HANDLE_MESSAGE = (
    'Cite a handle supplied in this packet. For a span, use start and end offsets within the text delivered under that handle.'
)
INVALID_MATERIAL_MESSAGE = (
    'Return material with title, part, text and more. Continue the same title with consecutive parts, up to eight parts, within this turn\'s answer bound.'
)


def _refuse_handle(path, handle):
    raise ResearchError('invalid_reference_handle', INVALID_REFERENCE_HANDLE_MESSAGE,
                        {'path': path, 'ref': handle})


def _expand_value(table, value, path):
    if isinstance(value, list):
        expanded, count = [], 0
        for index, item in enumerate(value):
            item, references = _expand_value(table, item, path + '/' + str(index))
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
        if 'start' in value:
            start, end = value['start'], value['end']
            content = entry.get('text')
            if (type(start) is not int or type(end) is not int or not isinstance(content, str)
                    or not 0 <= start < end <= len(content) or reference.get('kind') != 'source'):
                _refuse_handle(path, handle)
            reference['link']['locator'] = span_locator(content, start, end, excerpt_length=0)
        return reference, 1
    expanded, count = {}, 0
    for name, item in value.items():
        item_path = path + '/' + name.replace('~', '~0').replace('/', '~1')
        expanded[name], references = _expand_value(table, item, item_path)
        count += references
    return expanded, count


def expand_handles(packet, output):
    """Expand citations before role validation, preserving legacy outputs exactly."""
    if HANDLE_TABLE not in packet:
        return output
    with contract_check('review.shape'):
        if packet.get('material_state', {}).get('pending') and not is_material_output(packet, output):
            raise ResearchError('invalid_review_material', INVALID_MATERIAL_MESSAGE,
                                {'path': '/stage', 'pending': packet['material_state']['pending']})
        return _expand_value(packet[HANDLE_TABLE], output, '')[0]


def describe_expansion(packet, output, response):
    """Bind expanded native citations to the exact provider answer text."""
    if HANDLE_TABLE not in packet:
        return output, None
    with contract_check('review.shape'):
        expanded, count = _expand_value(packet[HANDLE_TABLE], output, '')
    answer = extract_answer_text(response)
    certificate = dict(protocol=EXPANSION_PROTOCOL, references=count,
                       answer_sha256=hashlib.sha256(answer.encode('utf-8')).hexdigest(),
                       output_sha256=digest(expanded))
    return expanded, certificate


def record_handle_output(packet, observation, artifacts):
    """Retain expansion and material state in the durable transport capture."""
    if observation['status'] != 'completed' or HANDLE_TABLE not in packet:
        return observation
    from .review_protocol import _put_json, _read_json
    output = _read_json(artifacts, observation['output'])
    try:
        expanded, certificate = describe_expansion(packet, output, _read_json(artifacts, observation['response']))
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


def derive_expanded_output(packet, observation, response):
    """Repeat answer recovery and handle expansion when authenticating an attempt."""
    source = observation
    if 'handle_expansion' in observation and 'answer_recovery' in observation:
        recovered = recover_answer(response)
        source = dict(observation, output=recovered['output'] if recovered else None)
    raw = derive_output(source, response)
    try:
        expanded, certificate = describe_expansion(packet, raw, response)
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
    return HANDLE_TABLE in packet and isinstance(output, dict) and output.get('stage') == 'material'


def validate_material(packet, output):
    """Check a material part once at the output-contract boundary."""
    with contract_check('review.shape'):
        fields(output, ('stage', 'title', 'part', 'text', 'more'), code='invalid_review_material', path='')
        for name in ('title', 'text'):
            text(output[name], 'Material ' + name, code='invalid_review_material')
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


def authenticate_material(packet, output, material, artifacts):
    """Rebuild stored argument descriptors from the authenticated part bytes."""
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
