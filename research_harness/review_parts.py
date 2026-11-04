"""Fresh-input review series, with pure planning and explicit store boundaries."""

import base64
import binascii
import copy
import hashlib
import json
import math
from dataclasses import dataclass

from . import review_excerpts, review_parts_prompts as prompts
from .artifacts import describe_artifact
from .errors import ResearchError
from .evidence import digest
from .operations import fields

PROTOCOL = 'exactory-review-parts-v2'
KEY = 'review_parts'
KINDS = ('read', 'combine', 'check', 'final')
STATE_KEY = 'review_parts_state'
READ_CHARACTERS = 600000
CARD_CHARACTERS = 80000
OUTPUT_CHARACTERS = 60000
STATEMENT_CHARACTERS = 6000
STATEMENTS_CHARACTERS = 40000


def render_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':'))


def find_kind(packet):
    header = packet.get(KEY) or {}
    if header.get('protocol') == PROTOCOL and header.get('kind') in KINDS:
        return header['kind']
    return None


def _require(condition, message, code='invalid_value_review', **details):
    if not condition:
        raise ResearchError(code, message, details or None)


def _require_text(value, name, blank=False):
    _require(isinstance(value, str) and (blank or bool(value.strip())),
             'Expected text for ' + name, path=name)


def _find_collection(packet, path):
    collection = packet
    for component in path:
        collection = collection[component]
    return collection


def _find_address(occurrence):
    return occurrence['index'] if 'index' in occurrence else occurrence['key']


def enumerate_units(packet, adapter, previous=None):
    """Deduplicate canonical items, retaining every list index or object key."""
    from .review_parts_roles import get_adapter

    units = []
    by_digest = {}
    definition = get_adapter(adapter)
    for source in definition['enumerate_units'](packet):
        item_digest = digest(source['item'])
        unit = by_digest.get(item_digest)
        if unit is None:
            unit = dict(item=copy.deepcopy(source['item']), digest=item_digest,
                        occurrences=[], bodies=[], citations=[])
            if source.get('carded_under'):
                unit['carded_under'] = source['carded_under']
            units.append(unit)
            by_digest[item_digest] = unit
        citations = (definition['citations'](source) if 'citations' in definition
                     else source.get('citations', []))
        for citation in citations:
            if citation not in unit['citations']:
                unit['citations'].append(copy.deepcopy(citation))
        for occurrence in source['occurrences']:
            if occurrence not in unit['occurrences']:
                unit['occurrences'].append(copy.deepcopy(occurrence))
        for body in source.get('bodies', []):
            existing = next((entry for entry in unit['bodies']
                             if entry['artifact']['sha256'] == body['artifact']['sha256']), None)
            if existing is None:
                unit['bodies'].append(copy.deepcopy(body))
            elif existing['artifact'] != body['artifact']:
                existing.setdefault('aliases', []).append(copy.deepcopy(body['artifact']))
    earlier = (previous or {}).get('units', [])
    previous_digests = {row['digest'] for row in earlier}
    if any(row['digest'] not in by_digest for row in earlier):
        raise ResearchError('review_parts_inventory_changed', 'An earlier unit disappeared')
    units = [by_digest[row['digest']] for row in earlier] + [
        unit for unit in units if unit['digest'] not in previous_digests]
    handles = {unit['digest']: 'u' + str(index + 1) for index, unit in enumerate(units)}
    for unit in units:
        unit['handle'] = handles[unit['digest']]
        unit['text'] = render_json(unit['item'])
        unit['item_characters'] = len(unit['text'])
        if unit.get('carded_under'):
            unit['carded_under'] = handles[unit['carded_under']]
        unit['characters'] = unit['item_characters'] + sum(
            len(render_json(body['content'])) for body in unit['bodies'])
    return units


def _describe_unit(unit):
    row = {key: copy.deepcopy(unit[key]) for key in
           ('handle', 'digest', 'occurrences', 'item_characters', 'characters', 'citations')}
    row['bodies'] = [dict(sha256=body['artifact']['sha256'], kind=body['encoding'],
                          characters=len(render_json(body['content'])), length=len(body['content']),
                          artifact=copy.deepcopy(body['artifact']),
                          aliases=copy.deepcopy(body.get('aliases', [])))
                     for body in unit['bodies']]
    if unit.get('carded_under'):
        row['carded_under'] = unit['carded_under']
    return row


def build_inventory(packet, adapter, previous=None):
    """Build an append-only list; later duplicate occurrences have separate bindings."""
    units = enumerate_units(packet, adapter, previous)
    rows = [_describe_unit(unit) for unit in units]
    additional = copy.deepcopy((previous or {}).get('additional_occurrences', []))
    for index, old in enumerate((previous or {}).get('units', [])):
        current = rows[index]
        old_core = {key: value for key, value in old.items() if key != 'occurrences'}
        new_core = {key: value for key, value in current.items() if key != 'occurrences'}
        _require(old_core == new_core, 'An earlier unit changed',
                 'review_parts_inventory_changed', unit=old['handle'])
        known = old['occurrences'] + [entry['occurrence'] for entry in additional
                                     if entry['unit'] == old['handle']]
        _require(all(entry in current['occurrences'] for entry in known),
                 'An earlier occurrence disappeared', 'review_parts_inventory_changed')
        for occurrence in current['occurrences']:
            binding = dict(unit=old['handle'], occurrence=occurrence)
            if occurrence not in known and binding not in additional:
                additional.append(copy.deepcopy(binding))
        rows[index] = copy.deepcopy(old)
    seen_bodies = set()
    characters = sum(unit['item_characters'] for unit in units)
    for unit in units:
        for body in unit['bodies']:
            identity = body['artifact']['sha256']
            if identity not in seen_bodies:
                characters += len(render_json(body['content']))
                seen_bodies.add(identity)
    return dict(protocol=PROTOCOL, adapter=adapter, units=rows, characters=characters,
                additional_occurrences=additional)


def build_frame(packet, units):
    frame = copy.deepcopy(packet)
    frame.pop('digest', None)
    frame.pop(KEY, None)
    frame.pop(STATE_KEY, None)
    owned = {body['artifact']['sha256'] for unit in units for body in unit['bodies']}
    for unit in units:
        for occurrence in unit['occurrences']:
            _find_collection(frame, occurrence['path']).clear()
    frame['evidence'] = [entry for entry in frame.get('evidence', [])
                         if entry['artifact']['sha256'] not in owned]
    return frame


def _merge_spans(spans):
    result = []
    for start, end in sorted(spans):
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def _find_gaps(length, spans, start=0, end=None):
    end = length if end is None else end
    gaps = []
    position = start
    for left, right in _merge_spans(spans):
        if right <= position:
            continue
        if left > position:
            gaps.append([position, min(left, end)])
        position = max(position, right)
        if position >= end:
            break
    if position < end:
        gaps.append([position, end])
    return gaps


def _read_entry_bytes(entry):
    content = entry.get('content')
    if not isinstance(content, str):
        return None
    if entry.get('encoding') == 'utf-8':
        data = content.encode('utf-8')
    elif entry.get('encoding') == 'base64':
        try:
            data = base64.b64decode(content, validate=True)
        except binascii.Error:
            return None
    else:
        return None
    reference = entry['artifact']
    if len(data) != reference['size'] or hashlib.sha256(data).hexdigest() != reference['sha256']:
        return None
    return data


def read_delivered_items(packet):
    """Read the actual projected address, with separate list and object addressing."""
    result = {}
    for row in packet.get(KEY, {}).get('delivered_items', []):
        collection = _find_collection(packet, row['path'])
        address = row['position'] if isinstance(collection, list) else row['key']
        valid = (type(address) is int and 0 <= address < len(collection)
                 if isinstance(collection, list) else address in collection)
        _require(valid, 'A delivery manifest names no actual item', 'review_parts_delivery_invalid')
        item = collection[address]
        _require(digest(item) == row['digest'], 'A delivered item differs from its digest',
                 'review_parts_delivery_invalid')
        result[row['unit']] = item
    return result


def _find_item_spans(packets, unit):
    spans = []
    for packet in packets:
        if any(row['unit'] == unit['handle'] and row['digest'] == unit['digest']
               for row in packet.get(KEY, {}).get('delivered_items', [])):
            spans.append([0, unit['item_characters']])
        for entry in packet.get('evidence', []):
            if entry.get('item') and entry.get('unit') == unit['handle']:
                segment = entry.get('segment', {})
                if (_read_entry_bytes(entry) is not None and segment.get('length') == unit['item_characters']
                        and 0 <= segment['start'] < segment['end'] <= segment['length']
                        and len(entry['content']) == segment['end'] - segment['start']):
                    spans.append([segment['start'], segment['end']])
    return _merge_spans(spans)


def _find_body_spans(packets, sha256):
    spans = []
    for packet in packets:
        for entry in packet.get('evidence', []):
            original = entry.get('original_artifact', entry.get('artifact', {}))
            if entry.get('item') or original.get('sha256') != sha256:
                continue
            if _read_entry_bytes(entry) is None:
                continue
            segment = entry.get('segment')
            if segment is None:
                spans.append([0, len(entry['content'])])
            elif (entry['encoding'] == 'utf-8'
                  and 0 <= segment['start'] < segment['end'] <= segment['length']
                  and len(entry['content']) == segment['end'] - segment['start']):
                spans.append([segment['start'], segment['end']])
    return _merge_spans(spans)


def is_unit_complete(unit, packets):
    if _find_gaps(unit['item_characters'], _find_item_spans(packets, unit)):
        return False
    for body in unit['bodies']:
        spans = _find_body_spans(packets, body['artifact']['sha256'])
        if not spans or _find_gaps(len(body['content']), spans):
            return False
    return True


def _count_content(units, packets):
    total = 0
    seen_bodies = set()
    for unit in units:
        total += sum(end - start for start, end in _find_item_spans(packets, unit))
        for body in unit['bodies']:
            identity = body['artifact']['sha256']
            if identity in seen_bodies:
                continue
            seen_bodies.add(identity)
            spans = _find_body_spans(packets, identity)
            for start, end in spans:
                total += len(render_json(body['content'][:end])) - len(render_json(body['content'][:start]))
            if spans:
                total += 2
    return total


def _make_segment(unit, content, start, end, *, body=None):
    original = (describe_artifact(content.encode('utf-8'), 'application/json')
                if body is None else body['artifact'])
    fragment = content[start:end]
    reference = describe_artifact(fragment.encode('utf-8'), original['media_type'])
    entry = dict(artifact=reference, original_artifact=copy.deepcopy(original),
                 encoding='utf-8', content=fragment, unit=unit['handle'],
                 segment=dict(start=start, end=end, length=len(content)), note=prompts.SEGMENT_NOTE)
    if body is None:
        entry['item'] = True
    return entry


def describe_selections(selections):
    return [{key: value for key, value in selection.items()
             if key in ('unit', 'body', 'item', 'start', 'end')}
            for selection in selections]


def project_packet(frame, units, selections, header, state, *, parent_characters=None):
    packet = copy.deepcopy(frame)
    by_handle = {unit['handle']: unit for unit in units}
    selected = {selection['unit'] for selection in selections}
    card_parents = ({by_handle[handle]['carded_under'] for handle in selected
                     if by_handle[handle].get('carded_under')}
                    if header['kind'] == 'read' else set())
    positions = {}
    body_entries = {}
    parent_items = [dict(unit=handle, item_only=True) if parent_characters is None else
                    dict(unit=handle, item=True, start=0,
                         end=min(parent_characters, by_handle[handle]['item_characters']))
                    for handle in card_parents if handle not in selected]
    for selection in selections + parent_items:
        unit = by_handle[selection['unit']]
        item_segment = (selection.get('item') is True and 'start' in selection
                        and not selection.get('whole_item'))
        if item_segment:
            entry = _make_segment(unit, unit['text'], selection['start'], selection['end'])
            body_entries[digest(entry)] = entry
        elif not selection.get('omit_item') and (not selection.get('body') or selection.get('repeat_item')):
            occurrence = unit['occurrences'][0]
            path = tuple(occurrence['path'])
            positions.setdefault(path, {})[_find_address(occurrence)] = unit
        if selection.get('item_only') or selection.get('item'):
            continue
        for body in unit['bodies']:
            identity = body['artifact']['sha256']
            if selection.get('body') and selection['body'] != identity:
                continue
            if selection.get('body') and 'start' in selection:
                entry = _make_segment(unit, body['content'], selection['start'], selection['end'], body=body)
            else:
                entry = copy.deepcopy(body)
                entry['unit'] = unit['handle']
            body_entries[digest(entry)] = entry
    manifest = []
    for path, entries in positions.items():
        collection = _find_collection(packet, path)
        for address in sorted(entries):
            unit = entries[address]
            if isinstance(collection, list):
                position = len(collection)
                collection.append(copy.deepcopy(unit['item']))
                binding = dict(index=address, position=position)
            else:
                collection[address] = copy.deepcopy(unit['item'])
                binding = dict(key=address)
            manifest.append(dict(unit=unit['handle'], path=list(path),
                                 digest=unit['digest'], **binding))
    packet['evidence'].extend(body_entries.values())
    packet[KEY] = copy.deepcopy(header)
    delivered = [unit['handle'] for unit in units if unit['handle'] in selected | card_parents]
    packet[KEY].update(delivered_units=delivered, delivered_items=manifest,
                       units=[_describe_unit(by_handle[handle]) for handle in delivered],
                       originals=describe_selections(selections), state_digest=digest(state))
    packet[STATE_KEY] = copy.deepcopy(state)
    packet['digest'] = digest(packet)
    return packet


def _find_span_end(content, start, end, allowance, item=False):
    left, right = start, min(end, start + allowance)
    while left < right:
        middle = (left + right + 1) // 2
        characters = middle - start if item else len(render_json(content[start:middle]))
        if characters <= allowance:
            left = middle
        else:
            right = middle - 1
    if left == start:
        return start
    return start + review_excerpts.compute_part_spans(content[start:end], left - start)[0][1]


def select_originals(units, allowance, packets=(), requests=None, *, distinct=False,
                     allow_segments=True):
    """Take a bounded prefix, cutting canonical item text or UTF-8 bodies exactly."""
    by_handle = {unit['handle']: unit for unit in units}
    requests = requests if requests is not None else [dict(unit=unit['handle']) for unit in units]
    selected = []
    used = 0
    for request in requests:
        unit = by_handle[request['unit']]
        if 'item' in request:
            item_spans = _find_item_spans(packets, unit) if distinct else []
            pieces = [('item', None, start, end) for start, end in
                      _find_gaps(unit['item_characters'], item_spans, request['start'], request['end'])]
        elif 'body' in request:
            body = next(entry for entry in unit['bodies']
                        if entry['artifact']['sha256'] == request['body'])
            body_spans = _find_body_spans(packets, request['body']) if distinct else []
            pieces = [('body', body, start, end) for start, end in
                      _find_gaps(len(body['content']), body_spans, request['start'], request['end'])]
        else:
            item_spans = _find_item_spans(packets, unit) if distinct else []
            pieces = [('item', None, start, end) for start, end in
                      _find_gaps(unit['item_characters'], item_spans)]
            for body in unit['bodies']:
                body_spans = _find_body_spans(packets, body['artifact']['sha256']) if distinct else []
                gaps = _find_gaps(len(body['content']), body_spans)
                if not body['content'] and not body_spans:
                    gaps = [[0, 0]]
                pieces.extend(('body', body, start, end) for start, end in gaps)
        costs = [(end - start if kind == 'item' else len(render_json(body['content'][start:end])))
                 for kind, body, start, end in pieces]
        if not pieces:
            continue
        if used + sum(costs) <= allowance:
            if ('start' not in request and not request.get('body')
                    and (not distinct or not item_spans
                         and all(not _find_body_spans(packets, body['artifact']['sha256'])
                                 for body in unit['bodies']))):
                selected.append(dict(unit=unit['handle']))
            else:
                for kind, body, start, end in pieces:
                    entry = dict(unit=unit['handle'], start=start, end=end)
                    if kind == 'item':
                        entry['item'] = True
                        entry['whole_item'] = start == 0 and end == unit['item_characters']
                    elif body['encoding'] == 'utf-8' and start < end:
                        entry['body'] = body['artifact']['sha256']
                        entry['repeat_item'] = unit['item_characters'] <= allowance
                    else:
                        entry = dict(unit=unit['handle'], body=body['artifact']['sha256'])
                    selected.append(entry)
            used += sum(costs)
            continue
        if not allow_segments:
            break
        for (kind, body, start, end), cost in zip(pieces, costs):
            remaining = allowance - used
            if remaining <= 0:
                break
            if kind == 'body' and body['encoding'] != 'utf-8':
                if cost <= remaining:
                    selected.append(dict(unit=unit['handle'], body=body['artifact']['sha256']))
                    used += cost
                break
            content = unit['text'] if kind == 'item' else body['content']
            stop = _find_span_end(content, start, end, remaining, item=kind == 'item')
            if stop <= start:
                break
            entry = dict(unit=unit['handle'], start=start, end=stop)
            if kind == 'item':
                entry['item'] = True
                entry['whole_item'] = start == 0 and stop == unit['item_characters']
            else:
                entry['body'] = body['artifact']['sha256']
                entry['repeat_item'] = unit['item_characters'] <= allowance
            selected.append(entry)
            used += stop - start if kind == 'item' else len(render_json(content[start:stop]))
            if stop < end:
                break
        break
    return selected, used


def build_state(observations):
    state = dict(cards=[], combinations=[], findings=[])
    combinations = set()
    for observation in observations:
        state['cards'].extend(copy.deepcopy(observation['delta']['cards']))
        state['findings'].extend(copy.deepcopy(observation['delta']['findings']))
        for combination in observation['delta']['combinations']:
            identity = digest([sorted(combination['units']), sorted(
                (render_json(original) for original in combination['read']))])
            if identity not in combinations:
                combinations.add(identity)
                state['combinations'].append(copy.deepcopy(combination))
    checked = {finding['combination'] for finding in state['findings']}
    for combination in state['combinations']:
        combination['checked'] = combination['id'] in checked
    return state


def _has_original(request, packets, units):
    unit = next(unit for unit in units if unit['handle'] == request['unit'])
    if request.get('item'):
        return not _find_gaps(unit['item_characters'], _find_item_spans(packets, unit),
                              request['start'], request['end'])
    if request.get('body'):
        body = next(body for body in unit['bodies'] if body['artifact']['sha256'] == request['body'])
        return not _find_gaps(len(body['content']), _find_body_spans(packets, request['body']),
                              request['start'], request['end'])
    return is_unit_complete(unit, packets)


@dataclass
class PlanningContext:
    packet: dict
    inventory: dict
    observations: list
    units: list
    frame: dict
    state: dict
    header: dict
    input_limit: int
    measure: object
    output_tokens: int
    read_packets: list
    remaining: list
    budgets: dict

    def render(self, kind, selections=(), state=None, *, parent_characters=None, **header):
        parameters = dict(self.header, kind=kind, **header)
        parameters['plan'] = dict(parameters['plan'], kind=kind)
        return project_packet(self.frame, self.units, list(selections), parameters,
                              self.state if state is None else state, parent_characters=parent_characters)


def prepare_plan(packet, inventory, observations, *, series_id, input_limit, measure,
                 output_tokens, replacement=None, inventory_reference=None,
                 logical_reference=None, series_key=None):
    from .review_parts_roles import get_adapter

    units = enumerate_units(packet, inventory['adapter'], inventory)
    _require(build_inventory(packet, inventory['adapter'], inventory) == inventory,
             'The packet differs from its inventory', 'review_parts_inventory_changed')
    frame = build_frame(packet, units)
    state = build_state(observations)
    reads = [row['packet'] for row in observations if find_kind(row['packet']) == 'read']
    remaining = [unit for unit in units if not is_unit_complete(unit, reads)]
    allowance = READ_CHARACTERS
    for observation in observations:
        if find_kind(observation['packet']) != 'read':
            continue
        characters = observation['packet'][KEY]['unit_characters']
        tokens = observation.get('output_tokens')
        if type(tokens) is not int:
            allowance = characters
        elif tokens >= 0.75 * output_tokens:
            allowance = characters // 2
        elif tokens <= 0.35 * output_tokens:
            allowance = int(1.5 * characters)
        else:
            allowance = characters
    if replacement and replacement.get('over_cap'):
        allowance = replacement['unit_characters'] // 2
    turn = len(observations) + 1
    header = dict(protocol=PROTOCOL, adapter=inventory['adapter'], series_assignment_id=series_id,
                  series_key=series_key, kind='final', turn=turn, inventory_digest=digest(inventory),
                  receipts=[row['receipt'] for row in observations], note=prompts.PARTS_NOTE,
                  targets=get_adapter(inventory['adapter'])['targets'](packet),
                  statement_allowance=dict(turn=STATEMENTS_CHARACTERS, each=STATEMENT_CHARACTERS),
                  output_allowance=OUTPUT_CHARACTERS, unit_characters=0,
                  plan=dict(kind='final', turn=turn, units_carded=len(units) - len(remaining),
                            units_remaining=len(remaining), unit_allowance=allowance, card_allowance=0,
                            remaining_turns={kind: dict(estimate=int(kind == 'final')) for kind in KINDS}))
    if inventory_reference is not None:
        header['inventory'] = inventory_reference
    if logical_reference is not None:
        header.update(logical_packet=logical_reference, logical_packet_digest=logical_reference['sha256'])
    empty = dict(cards=[], combinations=[], findings=[])
    final_frame = measure(project_packet(frame, units, [], header, empty))
    header['final_frame_characters'] = final_frame
    room = input_limit - final_frame
    _require(room > 0, 'The frame leaves no route room', 'review_parts_frame_too_large',
             frame_characters=final_frame, input_limit_characters=input_limit,
             largest_fields=sorted(((key, len(render_json(value))) for key, value in frame.items()),
                                   key=lambda row: row[1], reverse=True)[:10])
    budgets = dict(cards=room // 2, combinations=room // 10, findings=room // 5)
    header['state_budget'] = budgets
    header['card_budget'] = budgets['cards']
    header['plan']['unit_allowance'] = min(allowance, room)
    if not observations and remaining:
        _require(budgets['cards'] >= 4000, 'The final cannot hold the minimum cards',
                 'review_parts_final_too_large', final_frame_characters=final_frame,
                 input_limit_characters=input_limit)
        minimum_reads = math.ceil(inventory['characters'] / max(1, min(allowance, room)))
        _require(minimum_reads * 4000 <= budgets['cards'],
                 'The read floors exceed the final state budget', 'review_parts_inventory_too_large',
                 minimum_read_turns=minimum_reads, card_budget=budgets['cards'])
    context = PlanningContext(packet, inventory, list(observations), units, frame, state, header,
                              input_limit, measure, output_tokens, reads, remaining, budgets)
    if not observations:
        for unit in units:
            for body in unit['bodies']:
                if body['encoding'] == 'utf-8':
                    continue
                candidate = context.render('read', [dict(unit=unit['handle'], body=body['artifact']['sha256'])],
                                           state=empty)
                _require(_read_entry_bytes(body) is not None
                         and len(render_json(body['content'])) <= min(allowance, room)
                         and measure(candidate) <= input_limit,
                         'A non-text body cannot fit whole', 'review_parts_inventory_too_large',
                         unit=unit['handle'], body=body['artifact']['sha256'],
                         body_characters=len(render_json(body['content'])), input_limit_characters=input_limit)
    return context


def select_kind(context):
    if context.remaining:
        return 'read'
    identities = {(card['unit'], card['turn']) for card in context.state['cards']}
    round_id = digest([(card['unit'], card['turn']) for card in context.state['cards']])
    combines = [row['packet'][KEY] for row in context.observations
                if find_kind(row['packet']) == 'combine' and row['packet'][KEY].get('round') == round_id]
    if combines:
        count = len(combines[0]['block_plan'])
        expected = 1 if count == 1 else count * (count - 1) // 2
        if len({tuple(header['combine_pair']) for header in combines}) < expected:
            return 'combine'
    if len(identities) > 1:
        paired = set()
        for observation in context.observations:
            if find_kind(observation['packet']) == 'combine':
                cards = observation['packet'][STATE_KEY]['cards']
                identifiers = [(card['unit'], card['turn']) for card in cards]
                paired.update(frozenset((left, right)) for left in identifiers for right in identifiers)
        if any(frozenset((left, right)) not in paired for left in identities for right in identities):
            return 'combine'
    if any(not combination['checked'] for combination in context.state['combinations']):
        return 'check'
    return 'final'


def plan_read(context):
    empty = dict(cards=[], combinations=[], findings=[])
    before = _count_content(context.units, context.read_packets)
    remaining_characters = max(1, context.inventory['characters'] - before)
    available = context.budgets['cards'] - len(render_json(context.state['cards']))
    _require(available >= 4000, 'The remaining card budget is below its floor',
             'review_parts_inventory_too_large', card_budget_remaining=available)
    allowance = context.header['plan']['unit_allowance']
    budget = allowance
    while budget > 0:
        selected, _ = select_originals(context.remaining, budget, context.read_packets, distinct=True)
        handles = {entry['unit'] for entry in selected}
        parents = {unit.get('carded_under', unit['handle']) for unit in context.units
                   if unit['handle'] in handles}
        carried = dict(empty, cards=[card for card in context.state['cards'] if card['unit'] in parents])
        provisional = context.render('read', selected, state=carried)
        characters = _count_content(context.units, context.read_packets + [provisional]) - before
        card_allowance = min(CARD_CHARACTERS, available,
                             max(4000, available * characters // remaining_characters))
        context.header['plan'].update(card_allowance=card_allowance,
                                      unit_allowance=allowance)
        context.header['plan']['remaining_turns']['read']['estimate'] = math.ceil(
            remaining_characters / max(1, allowance))
        result = context.render('read', selected, state=carried, unit_characters=characters,
                                output_allowance=card_allowance, card_budget_remaining=available,
                                unit_characters_remaining=remaining_characters)
        measured = context.measure(result)
        if measured > context.input_limit and parents - handles:
            # Earlier cards supply the parent context. Its repeated canonical
            # item can itself be segmented when the whole item blocks progress.
            result = context.render('read', selected, state=carried, parent_characters=1,
                                    unit_characters=characters, output_allowance=card_allowance,
                                    card_budget_remaining=available,
                                    unit_characters_remaining=remaining_characters)
            measured = context.measure(result)
        if selected and characters > 0 and measured <= context.input_limit:
            return result
        if not selected:
            break
        budget = budget // 2 if measured <= context.input_limit + 1 or characters <= 0 else min(
            budget - 1, max(budget // 2, budget - (measured - context.input_limit)))
    raise ResearchError('review_parts_no_progress', 'The next read adds no item or segment',
                        dict(frame_characters=context.header['final_frame_characters'],
                             input_limit_characters=context.input_limit, unit_allowance=allowance))


def _pack_card_weights(weights, start, end, maximum):
    blocks = []
    current = []
    used = 0
    for index in range(start, end):
        if weights[index] > maximum:
            return None
        if current and used + weights[index] > maximum:
            blocks.append(current)
            current = []
            used = 0
        current.append(index)
        used += weights[index]
    if current:
        blocks.append(current)
    return blocks


def partition_card_weights(weights, capacity):
    """Fewest contiguous blocks whose two largest weights fit the pair room."""
    if sum(weights) <= capacity:
        return [list(range(len(weights)))]
    best = _pack_card_weights(weights, 0, len(weights), capacity // 2)
    # Only one block can exceed half of the pair room. Enumerate its position;
    # greedily packing both sides minimizes their block count for that choice.
    for start in range(len(weights)):
        total = 0
        for end in range(start + 1, len(weights) + 1):
            total += weights[end - 1]
            if total > capacity:
                break
            if total <= capacity // 2:
                continue
            left = _pack_card_weights(weights, 0, start, capacity - total)
            right = _pack_card_weights(weights, end, len(weights), capacity - total)
            if left is not None and right is not None:
                candidate = left + [list(range(start, end))] + right
                if best is None or len(candidate) < len(best):
                    best = candidate
    return best


def _render_combine(context, cards, blocks, pair, previous):
    pairs = [(0, 0)] if len(blocks) == 1 else [
        (left, right) for left in range(len(blocks)) for right in range(left + 1, len(blocks))]
    indices = list(dict.fromkeys(blocks[pair[0]] + blocks[pair[1]]))
    selected_cards = [cards[index] for index in indices]
    seen_blocks = {block for packet in previous for block in packet[KEY].get('within_blocks', [])}
    within = [block for block in dict.fromkeys(pair) if block not in seen_blocks]
    available = context.budgets['combinations'] - len(render_json(context.state['combinations']))
    allowance = min(OUTPUT_CHARACTERS, max(0, available // (len(pairs) - len(previous))))
    check_frame = context.render('check', state=dict(cards=cards, combinations=[], findings=[]))
    check_room = max(0, context.input_limit - context.measure(check_frame)
                     - context.budgets['combinations'])
    context.header['plan']['remaining_turns']['combine']['estimate'] = len(pairs) - len(previous)
    extra = dict(round=digest([(card['unit'], card['turn']) for card in cards]),
                 block_plan=blocks, combine_pair=list(pair), within_blocks=within,
                 check_room=check_room, output_allowance=allowance,
                 combination_budget_remaining=available)
    if len(blocks) > 1:
        extra['blocks'] = [dict(block=block, units=[cards[index]['unit'] for index in blocks[block]])
                           for block in pair]
    return context.render('combine', state=dict(cards=selected_cards, combinations=[], findings=[]), **extra)


def _partition_cards(context, cards):
    all_indices = list(range(len(cards)))
    if context.measure(_render_combine(context, cards, [all_indices], (0, 0), [])) <= context.input_limit:
        return [all_indices]
    empty = context.render('combine', state=dict(cards=[], combinations=[], findings=[]))
    frame_characters = context.measure(empty)
    weights = [context.measure(context.render('combine', state=dict(
        cards=[card], combinations=[], findings=[]))) - frame_characters for card in cards]
    capacity = context.input_limit - frame_characters
    while capacity > 0:
        blocks = partition_card_weights(weights, capacity)
        if not blocks:
            break
        excess = 0
        for left in range(len(blocks)):
            for right in range(left + 1, len(blocks)):
                candidate = _render_combine(context, cards, blocks, (left, right), [])
                excess = max(excess, context.measure(candidate) - context.input_limit)
        if excess <= 0:
            return blocks
        capacity = min(capacity - 1, max(capacity // 2, capacity - excess))
    raise ResearchError('review_parts_final_too_large', 'Two card blocks cannot fit this route')


def plan_combine(context):
    cards = context.state['cards']
    round_id = digest([(card['unit'], card['turn']) for card in cards])
    previous = [row['packet'] for row in context.observations
                if find_kind(row['packet']) == 'combine' and row['packet'][KEY].get('round') == round_id]
    blocks = previous[0][KEY]['block_plan'] if previous else _partition_cards(context, cards)
    pairs = [(0, 0)] if len(blocks) == 1 else [
        (left, right) for left in range(len(blocks)) for right in range(left + 1, len(blocks))]
    completed = {tuple(packet[KEY]['combine_pair']) for packet in previous}
    pair = next(pair for pair in pairs if pair not in completed)
    result = _render_combine(context, cards, blocks, pair, previous)
    _require(context.measure(result) <= context.input_limit,
             'The combination blocks exceed this route', 'review_parts_final_too_large')
    return result


def _find_card_handles(cards, handles):
    return {card['unit'] for card in cards
            if card['unit'] in handles
            or any(row['handle'] in handles for row in card.get('carded_units', []))}


def _select_combination_state(context, combinations):
    handles = {handle for combination in combinations for handle in combination['units']}
    card_handles = _find_card_handles(context.state['cards'], handles)
    return dict(cards=[card for card in context.state['cards'] if card['unit'] in card_handles],
                combinations=copy.deepcopy(combinations), findings=[])


def plan_check(context):
    pending = [entry for entry in context.state['combinations'] if not entry['checked']]
    available = context.budgets['findings'] - len(render_json(context.state['findings']))
    _require(2000 * len(pending) <= available,
             'The findings floors exceed the remaining budget', 'review_parts_inventory_too_large',
             combinations_remaining=len(pending), findings_budget_remaining=available)
    best = None
    for count in range(1, len(pending) + 1):
        selected = pending[:count]
        requests = [original for combination in selected for original in combination['read']]
        allowance = min(OUTPUT_CHARACTERS, max(2000 * count, available * count // len(pending)))
        candidate = context.render('check', requests, state=_select_combination_state(context, selected),
                                   check_combinations=[entry['id'] for entry in selected],
                                   output_allowance=allowance, findings_budget_remaining=available)
        if context.measure(candidate) > context.input_limit:
            break
        best = candidate
    _require(best is not None, 'A combination cannot fit its promised check room',
             'review_parts_inventory_too_large')
    return best


def _list_final_priority(context):
    sensitive = {card['unit'] for card in context.state['cards']
                 if any(bearing['relation'] in ('contradicts', 'qualifies') for bearing in card['bearing'])}
    sensitive.update(handle for finding in context.state['findings']
                     if finding['effect'] in ('contradicts', 'qualifies')
                     for combination in context.state['combinations']
                     if combination['id'] == finding['combination'] for handle in combination['units'])
    ordered = [dict(unit=unit['handle']) for unit in context.units if unit['handle'] in sensitive]
    ordered.extend(original for combination in context.state['combinations'] for original in combination['read'])
    ordered.extend(dict(unit=unit['handle']) for unit in context.units)
    seen = set()
    result = []
    for request in ordered:
        if digest(request) not in seen:
            seen.add(digest(request))
            result.append(copy.deepcopy(request))
    return result


def plan_final(context):
    finals = [row['packet'] for row in context.observations if find_kind(row['packet']) == 'final']
    traffic = sum(packet[KEY].get('original_characters', 0) for packet in finals)
    request_room = max(0, 2 * context.inventory['characters'] - traffic)
    carried = [original for packet in finals for original in packet[KEY].get('originals', [])]
    final_observations = [row for row in context.observations if find_kind(row['packet']) == 'final']
    previous_output = final_observations[-1]['output'] if final_observations else {}
    request = previous_output if previous_output.get('kind') == 'requests' else None
    requested = [{key: value for key, value in original.items() if key != 'why'}
                 for original in (request or {}).get('unit_requests', [])]
    extra = dict(carried_before=carried, request_room=request_room, fill=[],
                 output_allowance=OUTPUT_CHARACTERS)
    if request:
        extra.update(request=request, answered=[], unanswered=[])
    base = context.render('final', **extra)
    _require(context.measure(base) <= context.input_limit,
             'The final frame and state exceed this route', 'review_parts_final_too_large',
             final_frame_characters=context.header['final_frame_characters'],
             state_characters=len(render_json(context.state)))
    if requested:
        _require(not finals or any(not _has_original(original, [finals[-1]], context.units)
                                  for original in requested) or request.get('source_requests'),
                 'The request repeats only the final it answers', 'review_parts_request_no_progress')
    allowance = min(request_room, max(0, context.input_limit - context.measure(base)))
    while allowance >= 0:
        missing = [original for original in requested
                   if not finals or not _has_original(original, [finals[-1]], context.units)]
        repeated = [original for original in requested if original not in missing]
        answered = []
        characters = 0
        for original in missing:
            previous = finals if not _has_original(original, finals, context.units) else finals[-1:]
            fits_whole = _validate_original(original, context.inventory) <= allowance - characters
            originals, added = select_originals(context.units, allowance - characters, previous,
                                                 requests=[original], distinct=not fits_whole)
            answered.extend(originals)
            characters += added
        reread, reread_characters = select_originals(context.units, allowance - characters,
                                                     requests=repeated)
        answered += reread
        characters += reread_characters
        answered_handles = {entry['unit'] for entry in answered}
        fill, filled = select_originals(context.units, allowance - characters,
                                       requests=[entry for entry in _list_final_priority(context)
                                                 if entry['unit'] not in answered_handles])
        selections = answered + fill
        extra['fill'] = describe_selections(fill)
        extra['original_characters'] = characters + filled
        if request:
            extra['answered'] = describe_selections(answered)
            extra['unanswered'] = [dict(request=original, reason=prompts.SEGMENT_NOTE)
                                   for original in requested if original['unit'] not in answered_handles]
        result = context.render('final', selections, **extra)
        measured = context.measure(result)
        if measured <= context.input_limit:
            _require(not requested or bool(answered), 'No requested original fits the final budget',
                     'review_parts_request_no_progress', request_room=request_room)
            return result
        if allowance == 0:
            break
        allowance = allowance // 2 if measured <= context.input_limit + 1 else min(
            allowance - 1, max(allowance // 2, allowance - (measured - context.input_limit)))
    raise ResearchError('review_parts_final_too_large', 'The final cannot fit this route')


def plan_turn(packet, inventory, observations, **options):
    """Choose pending work, then run the planner for that kind of fresh turn."""
    context = prepare_plan(packet, inventory, observations, **options)
    return {'read': plan_read, 'combine': plan_combine, 'check': plan_check,
            'final': plan_final}[select_kind(context)](context)


def _validate_original(request, inventory, *, requested=False):
    optional = ('body', 'item', 'start', 'end') + (('why',) if requested else ())
    required = ('unit', 'why') if requested else ('unit',)
    fields(request, required, optional if not requested else optional[:-1], code='invalid_value_review')
    _require(isinstance(request['unit'], str), 'An original names a unit handle')
    unit = next((row for row in inventory['units'] if row['handle'] == request['unit']), None)
    _require(unit is not None, 'An original names an inventory unit')
    if requested:
        _require_text(request['why'], '/unit_requests/why')
    scope = {key: value for key, value in request.items() if key != 'why'}
    if set(scope) == {'unit'}:
        return unit['characters']
    if scope.get('item') is True:
        _require(set(scope) == {'unit', 'item', 'start', 'end'}, 'An item original names a span')
        length = unit['item_characters']
    else:
        _require(set(scope) == {'unit', 'body', 'start', 'end'}, 'A body original names a span')
        body = next((row for row in unit['bodies'] if row['sha256'] == scope.get('body')), None)
        _require(body is not None and body['kind'] == 'utf-8', 'Only a known text body has a span')
        length = body['length']
    _require(type(scope['start']) is int and type(scope['end']) is int
             and 0 <= scope['start'] < scope['end'] <= length, 'An original span lies within its text')
    return scope['end'] - scope['start']


def validate_deliveries(packet, inventory):
    by_handle = {row['handle']: row for row in inventory['units']}
    delivered = packet[KEY]['delivered_units']
    _require(isinstance(delivered, list) and len(delivered) == len(set(delivered))
             and all(handle in by_handle for handle in delivered),
             'The delivery lists known distinct handles', 'review_parts_delivery_invalid')
    items = read_delivered_items(packet)
    for handle, item in items.items():
        _require(handle in by_handle and digest(item) == by_handle[handle]['digest'],
                 'The actual item differs from the inventory', 'review_parts_delivery_invalid')
    for row in packet[KEY].get('delivered_items', []):
        occurrence = dict(path=row['path'], **({'index': row['index']} if 'index' in row else {'key': row['key']}))
        known = by_handle[row['unit']]['occurrences'] + [entry['occurrence'] for entry in
                inventory.get('additional_occurrences', []) if entry['unit'] == row['unit']]
        _require(occurrence in known and row['digest'] == by_handle[row['unit']]['digest'],
                 'The manifest changes an occurrence', 'review_parts_delivery_invalid')
    supplied = set(items)
    for entry in packet.get('evidence', []):
        if entry.get('item'):
            row = by_handle.get(entry.get('unit'))
            _require(row is not None and entry.get('original_artifact', {}).get('sha256') == row['digest']
                     and _read_entry_bytes(entry) is not None,
                     'An item segment is bound to its item digest', 'review_parts_delivery_invalid')
            span = entry['segment']
            _require(type(span['start']) is int and type(span['end']) is int
                     and 0 <= span['start'] < span['end'] <= row['item_characters']
                     and span['length'] == row['item_characters']
                     and len(entry['content']) == span['end'] - span['start'],
                     'An item segment has exact bounds', 'review_parts_delivery_invalid')
            supplied.add(entry['unit'])
        elif entry.get('unit'):
            _require(entry['unit'] in by_handle and _read_entry_bytes(entry) is not None,
                     'A body entry delivers exact bytes', 'review_parts_delivery_invalid')
            original = entry.get('original_artifact', entry['artifact'])
            body = next((body for body in by_handle[entry['unit']]['bodies']
                         if body['sha256'] == original['sha256']), None)
            _require(body is not None, 'The body belongs to its unit', 'review_parts_delivery_invalid')
            if entry.get('segment'):
                span = entry['segment']
                _require(body['kind'] == 'utf-8' and span['length'] == body['length']
                         and type(span['start']) is int and type(span['end']) is int
                         and 0 <= span['start'] < span['end'] <= body['length']
                         and len(entry['content']) == span['end'] - span['start'],
                         'A body segment has exact original bounds', 'review_parts_delivery_invalid')
            supplied.add(entry['unit'])
    if find_kind(packet) == 'read':
        _require(set(delivered) == supplied, 'Read handles name exactly the actual delivered units',
                 'review_parts_delivery_invalid')


def _list_delivered_texts(packet, handle, inventory):
    by_handle = {row['handle']: row for row in inventory['units']}
    covered = {row['handle'] for row in inventory['units']
               if row['handle'] == handle or row.get('carded_under') == handle}
    texts = []
    for unit, item in read_delivered_items(packet).items():
        if unit in covered:
            texts.append(dict(content=render_json(item), at=dict(unit=unit, item=True), offset=0))
    body_ids = {body['sha256'] for unit in covered for body in by_handle[unit]['bodies']}
    for entry in packet.get('evidence', []):
        original = entry.get('original_artifact', entry.get('artifact', {}))
        if entry.get('encoding') == 'utf-8' and (
                entry.get('unit') in covered or original.get('sha256') in body_ids):
            owner = entry.get('unit', handle)
            location = dict(unit=owner, item=True) if entry.get('item') else dict(
                unit=owner, body=original['sha256'])
            texts.append(dict(content=entry['content'], at=location,
                              offset=entry.get('segment', {}).get('start', 0)))
    return texts


def _verify_statements(packet, handle, statements, inventory, used):
    _require(isinstance(statements, list), 'Statements are an array')
    sources = []
    for source in _list_delivered_texts(packet, handle, inventory):
        normalized, offsets = review_excerpts.normalize_with_offsets(source['content'])
        sources.append((source, normalized, offsets))
    verified = []
    unmatched = 0
    for statement in statements:
        fields(statement, ('text', 'why'), code='invalid_value_review')
        _require_text(statement['text'], '/statements/text')
        _require_text(statement['why'], '/statements/why')
        used += len(statement['text'])
        _require(len(statement['text']) <= STATEMENT_CHARACTERS and used <= STATEMENTS_CHARACTERS,
                 'Statements exceed their turn or individual allowance')
        phrase = review_excerpts.normalize_with_offsets(statement['text'])[0]
        found = False
        for source, normalized, offsets in sources:
            content = source['content']
            position = normalized.find(phrase)
            if position >= 0 and phrase:
                exact = content[offsets[position]:offsets[position + len(phrase) - 1] + 1]
                if len(exact) <= STATEMENT_CHARACTERS:
                    location = dict(source['at'], start=source['offset'] + offsets[position],
                                    end=source['offset'] + offsets[position + len(phrase) - 1] + 1)
                    verified.append(dict(statement, text=exact, at=location))
                    found = True
                break
        if not found:
            unmatched += 1
    return verified, unmatched, used


def project_source_output(output, role='result'):
    if output.get('kind') != 'requests':
        return output
    value = dict(status='unresolved')
    if role == 'bar':
        value.update(objective_adequacy='not_assessed', method_adequacy='not_assessed',
                     bar_ids=[], work_items=[], assurances=[], objection_findings=[])
    return dict(stage=role, support=None, value=value,
                source_requests=copy.deepcopy(output.get('source_requests', [])))


def _validate_read(packet, output, inventory):
    header = packet[KEY]
    fields(output, ('kind', 'turn', 'cards', 'unborne'), code='invalid_value_review')
    _require(isinstance(output['cards'], list), 'Cards are an array')
    _require(len(render_json(output['cards'])) <= header['output_allowance'], 'Cards exceed their allowance')
    by_handle = {row['handle']: row for row in inventory['units']}
    expected = {by_handle[handle].get('carded_under', handle) for handle in header['delivered_units']}
    targets = {row['id'] for row in header['targets']}
    borne = set()
    seen = set()
    cards = []
    used = 0
    for card in output['cards']:
        fields(card, ('unit', 'summary', 'bearing', 'statements', 'depends_on', 'questions'),
               code='invalid_value_review')
        _require(isinstance(card['unit'], str) and card['unit'] in expected and card['unit'] not in seen,
                 'Supply exactly one card per delivered unit, passages under their reading')
        seen.add(card['unit'])
        for name in ('summary', 'depends_on', 'questions'):
            _require_text(card[name], '/cards/' + name, blank=name != 'summary')
        _require(isinstance(card['bearing'], list), 'Bearing is an array')
        material = False
        for bearing in card['bearing']:
            fields(bearing, ('target', 'relation', 'reason'), code='invalid_value_review')
            _require(isinstance(bearing['target'], str) and bearing['target'] in targets
                     and bearing['relation'] in ('supports', 'qualifies', 'contradicts', 'context'),
                     'A bearing names an assigned target and relation')
            _require_text(bearing['reason'], '/cards/bearing/reason')
            borne.add(bearing['target'])
            material = material or bearing['relation'] != 'context'
        _require(not material or bool(card['statements']), 'A material bearing needs a statement')
        statements, unmatched, used = _verify_statements(packet, card['unit'], card['statements'], inventory, used)
        metadata = {key: copy.deepcopy(value) for key, value in by_handle[card['unit']].items()
                    if key != 'handle'}
        covered = [row for row in header['units']
                   if row.get('carded_under', row['handle']) == card['unit'] and row['handle'] != card['unit']]
        carried = dict(copy.deepcopy(card), **metadata, turn=header['turn'], statements=statements,
                       unverified_statements=unmatched)
        if covered:
            carried['carded_units'] = copy.deepcopy(covered)
        cards.append(carried)
    _require(seen == expected, 'Supply exactly one card for each delivered card unit')
    _require(isinstance(output['unborne'], list), 'Unborne is an array')
    unborne = set()
    for entry in output['unborne']:
        fields(entry, ('target', 'reason'), code='invalid_value_review')
        _require(isinstance(entry['target'], str) and entry['target'] in targets
                 and entry['target'] not in unborne, 'Unborne names distinct assigned targets')
        _require_text(entry['reason'], '/unborne/reason')
        unborne.add(entry['target'])
    _require(unborne == targets - borne, 'Unborne names exactly the targets without a bearing')
    _require(len(render_json(cards)) <= header['card_budget_remaining'],
             'The carried cards exceed the remaining state budget')
    return dict(cards=cards, combinations=[], findings=[])


def _validate_combine(packet, output, inventory):
    fields(output, ('kind', 'turn', 'combinations', 'rest'), code='invalid_value_review')
    _require_text(output['rest'], '/rest')
    _require(isinstance(output['combinations'], list), 'Combinations are an array')
    handles = {row['handle'] for row in inventory['units']}
    combinations = []
    for index, combination in enumerate(output['combinations']):
        fields(combination, ('units', 'question', 'view', 'read'), code='invalid_value_review')
        units = combination['units']
        _require(isinstance(units, list) and units
                 and all(isinstance(handle, str) and handle in handles for handle in units)
                 and len(units) == len(set(units)), 'A combination names distinct known handles')
        card_units = {handle for card in packet[STATE_KEY]['cards']
                      for handle in [card['unit']] + [row['handle']
                          for row in card.get('carded_units', [])]}
        _require(set(units) <= card_units,
                 'A combination names the cards this turn read')
        _require_text(combination['question'], '/combinations/question')
        _require_text(combination['view'], '/combinations/view')
        _require(isinstance(combination['read'], list) and combination['read'],
                 'A combination names originals')
        characters = 0
        for original in combination['read']:
            characters += _validate_original(original, inventory)
            _require(original['unit'] in units, 'An original belongs to its combination')
        _require(characters <= packet[KEY]['check_room'], 'The originals exceed one check turn')
        if packet[KEY].get('blocks'):
            card_handles = _find_card_handles(packet[STATE_KEY]['cards'], set(units))
            within = [block['block'] for block in packet[KEY]['blocks']
                      if card_handles <= set(block['units'])]
            _require(not within or any(block in packet[KEY]['within_blocks'] for block in within),
                     'Internal combinations belong to the first turn holding their block')
        combinations.append(dict(copy.deepcopy(combination), id='c%d-%d' % (packet[KEY]['turn'], index + 1),
                                 turn=packet[KEY]['turn'], checked=False))
    _require(len(render_json(combinations)) <= packet[KEY]['combination_budget_remaining'],
             'Combinations exceed their carried state budget')
    return dict(cards=[], combinations=combinations, findings=[])


def _validate_check(packet, output, inventory):
    fields(output, ('kind', 'turn', 'findings'), code='invalid_value_review')
    _require(isinstance(output['findings'], list), 'Findings are an array')
    expected = set(packet[KEY]['check_combinations'])
    seen = set()
    handles = {row['handle'] for row in inventory['units']}
    findings = []
    used = 0
    for finding in output['findings']:
        fields(finding, ('combination', 'finding', 'effect', 'statements'), code='invalid_value_review')
        identifier = finding['combination']
        _require(isinstance(identifier, str) and identifier in expected and identifier not in seen,
                 'Supply one finding per delivered combination')
        seen.add(identifier)
        _require_text(finding['finding'], '/findings/finding')
        _require(finding['effect'] in ('supports', 'qualifies', 'contradicts', 'unresolved'),
                 'A finding has a known effect')
        _require(isinstance(finding['statements'], list), 'Statements are an array')
        statements = []
        unmatched = 0
        for statement in finding['statements']:
            fields(statement, ('unit', 'text', 'why'), code='invalid_value_review')
            _require(isinstance(statement['unit'], str) and statement['unit'] in handles,
                     'A finding statement names a known unit')
            verified, count, used = _verify_statements(packet, statement['unit'],
                [dict(text=statement['text'], why=statement['why'])], inventory, used)
            statements.extend(dict(entry, unit=statement['unit']) for entry in verified)
            unmatched += count
        findings.append(dict(copy.deepcopy(finding), turn=packet[KEY]['turn'],
                             statements=statements, unverified_statements=unmatched))
    _require(seen == expected, 'Supply exactly one finding per delivered combination')
    _require(len(render_json(findings)) <= packet[KEY]['findings_budget_remaining'],
             'Findings exceed the remaining carried state budget')
    return dict(cards=[], combinations=[], findings=findings)


def _validate_requests(packet, output, inventory):
    from . import bar_sources
    from .review_source_batches import digest_request_meaning
    from .review_parts_roles import get_adapter

    fields(output, ('kind', 'unit_requests', 'source_requests'), code='invalid_value_review')
    _require(isinstance(output['unit_requests'], list) and isinstance(output['source_requests'], list)
             and (output['unit_requests'] or output['source_requests']), 'A request names originals or sources')
    _require(len(render_json(output)) <= OUTPUT_CHARACTERS, 'The request exceeds its character allowance')
    requested = []
    for original in output['unit_requests']:
        _validate_original(original, inventory, requested=True)
        requested.append({key: value for key, value in original.items() if key != 'why'})
    if output['source_requests']:
        _require(get_adapter(inventory['adapter']).get('source_requests', False),
                 'This adapter has no source requests')
        bar_sources.requests(project_source_output(output, packet['role']),
                             passages=review_excerpts.KEY in packet)
    carried = packet[KEY].get('originals', [])
    new_units = any(not covers_original(carried, original, inventory) for original in requested)
    delivered_sources = [entry.get('request') for entry in packet.get('source_deliveries', [])
                         if entry.get('status') in ('delivered', 'already_delivered')]
    delivered_meanings = {digest_request_meaning(source) for source in delivered_sources}
    new_sources = any(digest_request_meaning(source) not in delivered_meanings
                      for source in output['source_requests'])
    _require(new_units or new_sources, 'A request must add something to the final it answers',
             'review_parts_request_no_progress')
    _require(packet[KEY].get('request_room', 0) > packet[KEY].get('original_characters', 0)
             or not requested, 'The final original budget is exhausted', 'review_parts_request_no_progress')
    return dict(cards=[], combinations=[], findings=[])


def covers_original(originals, requested, inventory):
    """Compare original scopes by their union, including a complete segmented item."""
    row = next(row for row in inventory['units'] if row['handle'] == requested['unit'])
    same_unit = [original for original in originals if original['unit'] == requested['unit']]
    if any(not any(key in original for key in ('body', 'item')) for original in same_unit):
        return True
    if requested.get('item'):
        scopes = [original for original in same_unit if original.get('item')]
        return not _find_gaps(row['item_characters'], [[scope['start'], scope['end']] for scope in scopes],
                              requested['start'], requested['end'])
    if requested.get('body'):
        body = next(body for body in row['bodies'] if body['sha256'] == requested['body'])
        scopes = [original for original in same_unit if original.get('body') == body['sha256']]
        return not _find_gaps(body['length'], [[scope.get('start', 0), scope.get('end', body['length'])]
                                             for scope in scopes], requested['start'], requested['end'])
    item = dict(unit=requested['unit'], item=True, start=0, end=row['item_characters'])
    if not covers_original(originals, item, inventory):
        return False
    for body in row['bodies']:
        scope = dict(unit=requested['unit'], body=body['sha256'], start=0, end=body['length'])
        if not covers_original(originals, scope, inventory):
            return False
    return True


def validate_output(packet, output, inventory):
    """Check this turn only; its explicit attempt supplies scientific delivery."""
    validate_deliveries(packet, inventory)
    _require(isinstance(output, dict), 'A parts output is an object')
    kind = find_kind(packet)
    if kind == 'final':
        if output.get('kind') == 'requests':
            return _validate_requests(packet, output, inventory)
        return dict(cards=[], combinations=[], findings=[])
    _require(output.get('kind') == kind and type(output.get('turn')) is int
             and output['turn'] == packet[KEY]['turn'], 'The output names this exact kind and turn')
    if kind != 'read':
        _require(len(render_json(output)) <= packet[KEY]['output_allowance'], 'The output exceeds its allowance')
    return {'read': _validate_read, 'combine': _validate_combine,
            'check': _validate_check}[kind](packet, output, inventory)


def _reconstruct_items(packets, inventory):
    items = {}
    for packet in packets:
        items.update(read_delivered_items(packet))
    for unit in inventory['units']:
        if unit['handle'] in items:
            continue
        entries = [entry for packet in packets for entry in packet.get('evidence', [])
                   if entry.get('item') and entry.get('unit') == unit['handle']]
        text = _join_segments(entries, unit['item_characters'])
        if text is not None and hashlib.sha256(text.encode('utf-8')).hexdigest() == unit['digest']:
            item = json.loads(text)
            if render_json(item) == text:
                items[unit['handle']] = item
    return items


def _join_segments(entries, length):
    position = 0
    pieces = []
    for entry in sorted(entries, key=lambda value: (value['segment']['start'], value['segment']['end'])):
        span = entry['segment']
        if (_read_entry_bytes(entry) is None or span['length'] != length
                or not 0 <= span['start'] < span['end'] <= length
                or len(entry['content']) != span['end'] - span['start']):
            return None
        if span['start'] > position:
            return None
        if span['end'] > position:
            pieces.append(entry['content'][position - span['start']:])
            position = span['end']
    return ''.join(pieces) if position == length else None


def covers_items(packets, expected, key, inventory=None, *, path=None):
    """Credit each frozen occurrence, including item segments and equal duplicates."""
    reads = [packet for packet in packets if find_kind(packet) == 'read']
    if inventory is None:
        rows = {}
        for packet in reads:
            for row in packet[KEY]['units']:
                rows.setdefault(row['handle'], row)
        inventory = dict(units=list(rows.values()), additional_occurrences=[])
    items = _reconstruct_items(reads, inventory)
    delivered = {}
    for unit in inventory['units']:
        if unit['handle'] not in items:
            continue
        occurrences = unit['occurrences'] + [entry['occurrence'] for entry in
                       inventory.get('additional_occurrences', []) if entry['unit'] == unit['handle']]
        for occurrence in occurrences:
            if occurrence['path'] == (path if path is not None else ['problem_evidence', key]):
                delivered[_find_address(occurrence)] = items[unit['handle']]
    collection = expected[key]
    entries = enumerate(collection) if isinstance(collection, list) else collection.items()
    return all(delivered.get(address) == item for address, item in entries)


def has_series_attachment(artifacts, packets, reference):
    """Credit exact full bytes or a complete union of exact UTF-8 segments, including aliases."""
    for packet in packets:
        for entry in packet.get('evidence', []):
            if (entry.get('artifact', {}).get('sha256') == reference['sha256']
                    and _read_entry_bytes(entry) is not None):
                return len(_read_entry_bytes(entry)) == reference['size']
    entries = [entry for packet in packets for entry in packet.get('evidence', [])
               if not entry.get('item') and entry.get('segment')
               and entry.get('original_artifact', {}).get('sha256') == reference['sha256']]
    if not entries:
        return False
    text = _join_segments(entries, entries[0]['segment']['length'])
    if text is None:
        return False
    data = text.encode('utf-8')
    return len(data) == reference['size'] and hashlib.sha256(data).hexdigest() == reference['sha256']


def has_series_reference(artifacts, packets, reference):
    """Credit a complete original or a source locator covered by exact body segments."""
    artifact = reference['link']['artifact'] if reference.get('kind') == 'source' else reference
    if has_series_attachment(artifacts, packets, artifact):
        return True
    locator = reference.get('link', {}).get('locator', {})
    if locator.get('kind') not in ('text', 'span'):
        return False
    start, end = locator['start'], locator['end']
    entries = [entry for packet in packets for entry in packet.get('evidence', [])
               if not entry.get('item') and entry.get('segment')
               and entry.get('original_artifact', {}).get('sha256') == artifact['sha256']
               and _read_entry_bytes(entry) is not None]
    position = start
    fragments = []
    for entry in sorted(entries, key=lambda row: row['segment']['start']):
        span = entry['segment']
        if span['end'] <= position:
            continue
        if span['start'] > position:
            break
        stop = min(end, span['end'])
        fragments.append(entry['content'][position - span['start']:stop - span['start']])
        position = stop
        if position == end:
            text = ''.join(fragments)
            return (text == locator['quote'] if locator['kind'] == 'text' else
                    hashlib.sha256(text.encode('utf-8')).hexdigest() == locator['sha256'])
    return False


# Store and transport boundaries. Planners and validators above use only values.
def store_inventory(artifacts, inventory):
    return artifacts.put(render_json(inventory).encode('utf-8'), 'application/json')


def read_inventory(artifacts, reference, expected_digest=None):
    from .workspace import strict_json
    inventory = strict_json(artifacts.read(reference))
    _require(isinstance(inventory, dict) and inventory.get('protocol') == PROTOCOL
             and isinstance(inventory.get('units'), list), 'The inventory is not a parts inventory',
             'review_parts_inventory_changed')
    _require(expected_digest is None or digest(inventory) == expected_digest,
             'The inventory differs from its digest', 'review_parts_inventory_changed')
    return inventory


def read_logical_packet(artifacts, header):
    from .workspace import strict_json
    packet = strict_json(artifacts.read(header['logical_packet']))
    _require(isinstance(packet, dict) and digest(packet) == header['logical_packet_digest'],
             'The logical packet differs from its digest', 'review_parts_delivery_invalid')
    return packet


def _list_series(records, artifacts, assignment_id):
    from .operations import normalized_text
    from .workspace import strict_json

    latest = records.get('review_assignment', {}).get(assignment_id)
    if latest is None:
        return []
    packet = strict_json(artifacts.read(latest['packet']))
    if find_kind(packet) is None:
        return []
    header = packet[KEY]
    _require({'series_assignment_id', 'series_key', 'adapter'} <= header.keys(),
             'The series binding is incomplete', 'review_parts_delivery_invalid')
    chain = []
    seen = set()
    current = latest
    while current is not None and current['id'] not in seen:
        seen.add(current['id'])
        frozen = strict_json(artifacts.read(current['packet']))
        binding = frozen.get(KEY, {})
        if (find_kind(frozen) is None
                or any(binding.get(key) != header.get(key) for key in
                       ('series_assignment_id', 'series_key', 'adapter'))
                or any(current.get(key) != latest.get(key) for key in
                       ('role', 'route_id', 'slot_id', 'session_id', 'subject'))
                or any(normalized_text(current[key]) != normalized_text(latest[key])
                       for key in ('reviewer_id', 'author_id'))):
            return []
        chain.append((current, frozen))
        if current['id'] == header['series_assignment_id']:
            return list(reversed(chain))
        parent = current.get('context', {}).get('prior_assignment_id')
        if parent != current.get('history_assignment_id'):
            return []
        current = records.get('review_assignment', {}).get(parent)
    return []


def _read_observed_turn(records, artifacts, assignment, frozen):
    from . import review_protocol as protocol
    from .review_input_projection import packets_from_request
    from .review_parts_roles import get_adapter
    from .workspace import strict_json

    state = protocol.assignment_state(records, artifacts, assignment['id'])
    attempt = records.get('review_attempt', {}).get(state.get('attempt_id'))
    if not state['ready'] or attempt is None or attempt.get('origin') != 'instrumented':
        return None
    request = strict_json(artifacts.read(attempt['request']))
    packets = packets_from_request(request)
    if not packets or packets[-1] != frozen:
        return None
    packet = packets[-1]
    header = packet[KEY]
    inventory = read_inventory(artifacts, header['inventory'], header['inventory_digest'])
    logical = read_logical_packet(artifacts, header)
    if get_adapter(header['adapter'])['series_key'](logical) != header['series_key']:
        return None
    if build_inventory(logical, header['adapter'], inventory) != inventory:
        return None
    delta = validate_output(packet, state['output'], inventory)
    receipt = dict(turn=header['turn'], kind=header['kind'], assignment_id=assignment['id'],
                   attempt_id=attempt['id'], output_digest=state['output_digest'])
    return dict(packet=packet, output=state['output'], delta=delta, inventory=inventory,
                receipt=receipt, output_tokens=(attempt.get('usage') or {}).get('output_tokens'))


def _select_carried_state(packet, state):
    kind = find_kind(packet)
    header = packet[KEY]
    if kind == 'read':
        parents = {row.get('carded_under', row['handle']) for row in header['units']}
        return dict(cards=[card for card in state['cards'] if card['unit'] in parents],
                    combinations=[], findings=[])
    if kind == 'combine':
        blocks = header['block_plan']
        pair = header['combine_pair']
        indices = list(dict.fromkeys(blocks[pair[0]] + blocks[pair[1]]))
        return dict(cards=[state['cards'][index] for index in indices], combinations=[], findings=[])
    if kind == 'check':
        combinations = [entry for entry in state['combinations']
                        if entry['id'] in header['check_combinations']]
        handles = {handle for entry in combinations for handle in entry['units']}
        card_handles = _find_card_handles(state['cards'], handles)
        return dict(cards=[card for card in state['cards'] if card['unit'] in card_handles],
                    combinations=combinations, findings=[])
    return state


def observe_series(records, artifacts, assignment_id):
    """Read each selected turn once in this evaluation, independently of series readiness."""
    from .evaluation import Evaluation
    evaluation = Evaluation.of(records, artifacts)
    observations = []
    for assignment, frozen in _list_series(records, evaluation, assignment_id):
        observation = evaluation.once(('review_parts_turn', assignment['id']),
            lambda assignment=assignment, frozen=frozen:
                _read_observed_turn(records, evaluation, assignment, frozen))
        if observation is None:
            break
        packet = observation['packet']
        header = packet[KEY]
        if (header['turn'] != len(observations) + 1
                or header['receipts'] != [row['receipt'] for row in observations]
                or header['state_digest'] != digest(packet[STATE_KEY])
                or packet[STATE_KEY] != _select_carried_state(packet, build_state(observations))):
            break
        if observations:
            previous = observations[-1]['inventory']
            if observation['inventory']['units'][:len(previous['units'])] != previous['units']:
                break
            previous_occurrences = previous.get('additional_occurrences', [])
            occurrences = observation['inventory'].get('additional_occurrences', [])
            if occurrences[:len(previous_occurrences)] != previous_occurrences:
                break
        observations.append(observation)
    return observations


def collect_series_packets(records, artifacts, assignment_id):
    try:
        return [row['packet'] for row in observe_series(records, artifacts, assignment_id)]
    except (ResearchError, json.JSONDecodeError):
        return []


def collect_contract_packets(records, artifacts, assignment):
    """Earlier selected turns and the current frozen packet, without selecting this turn."""
    from .workspace import strict_json
    frozen = strict_json(artifacts.read(assignment['packet']))
    prior = assignment.get('context', {}).get('prior_assignment_id')
    earlier = collect_series_packets(records, artifacts, prior) if prior else []
    series_id = frozen[KEY]['series_assignment_id']
    return [frozen] + list(reversed([packet for packet in earlier
                                    if packet[KEY]['series_assignment_id'] == series_id]))


def read_series_delivery(records, artifacts, assignment_id=None, *, packets=None, inventory=None):
    """Project authenticated actual series packets, preserving aliases and exact segment bounds.

    Explicit packets come only from an already authenticated attempt reader. The
    extra reconstructed packet is a delivery projection, never a saved-input fallback.
    """
    from .review_delivered_evidence import project_delivery_view
    from .review_protocol import collect_references
    from .review_supplied_evidence import register_supplied_artifact

    if packets is None:
        packets = collect_series_packets(records, artifacts, assignment_id)
    actual = list(packets)
    fresh_packets = [packet for packet in actual if find_kind(packet) is not None]
    if not fresh_packets:
        return project_delivery_view(records, artifacts, actual)
    if inventory is None and fresh_packets:
        header = fresh_packets[-1][KEY]
        inventory = read_inventory(artifacts, header['inventory'], header['inventory_digest'])
    projected = copy.deepcopy(actual)
    reads = [packet for packet in fresh_packets if find_kind(packet) == 'read']
    if inventory is not None and reads:
        items = _reconstruct_items(reads, inventory)
        logical = read_logical_packet(artifacts, fresh_packets[-1][KEY])
        units = enumerate_units(logical, inventory['adapter'], inventory)
        complete = [dict(unit=unit['handle'], item_only=True) for unit in units
                    if unit['handle'] in items]
        header = dict(reads[-1][KEY], delivery_projection=True)
        reconstructed = project_packet(build_frame(logical, units), units, complete, header,
                                        dict(cards=[], combinations=[], findings=[]))
        # Its occurrence map is that of the final inventory, including late aliases.
        reconstructed[KEY]['units'] = copy.deepcopy(inventory['units'])
        reconstructed[KEY]['additional_occurrences'] = copy.deepcopy(inventory.get('additional_occurrences', []))
        projected.append(reconstructed)
    delivery = project_delivery_view(records, artifacts, projected)
    segments = [copy.deepcopy(entry) for packet in actual for entry in packet.get('evidence', [])
                if entry.get('segment') and not entry.get('item')]
    for reference in collect_references(actual):
        if has_series_attachment(artifacts, actual, reference):
            register_supplied_artifact(delivery['artifacts'], reference)
    delivery.update(actual_packets=actual, read_packets=[packet for packet in projected
                    if find_kind(packet) == 'read'], segments=segments, inventory=inventory)
    return delivery


def verify_series(records, artifacts, final_assignment_id):
    issues = []
    issue_count = 0

    def add_issue(code, **details):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < 20:
            issues.append(dict(code=code, **details))

    packets = []
    delivery = None
    try:
        chain = _list_series(records, artifacts, final_assignment_id)
        observations = observe_series(records, artifacts, final_assignment_id)
        packets = [row['packet'] for row in observations]
        if not chain or len(chain) != len(observations):
            add_issue('unobserved_turn')
        if not packets or find_kind(packets[-1]) != 'final' or observations[-1]['output'].get('kind') == 'requests':
            add_issue('final_pending')
        if observations:
            inventory = observations[-1]['inventory']
            delivery = read_series_delivery(records, artifacts, packets=packets, inventory=inventory)
            reads = [packet for packet in packets if find_kind(packet) == 'read']
            items = _reconstruct_items(reads, inventory)
            for unit in inventory['units']:
                if unit['handle'] not in items:
                    add_issue('unit_not_delivered', unit=unit['handle'])
                for body in unit['bodies']:
                    if not has_series_attachment(artifacts, reads, body['artifact']):
                        add_issue('body_not_delivered', unit=unit['handle'], body=body['sha256'])
            state = build_state(observations)
            identities = {(card['unit'], card['turn']) for card in state['cards']}
            paired = set()
            rounds = {}
            for packet in packets:
                if find_kind(packet) != 'combine':
                    continue
                header = packet[KEY]
                rounds.setdefault(header['round'], []).append(header)
                cards = [(card['unit'], card['turn']) for card in packet[STATE_KEY]['cards']]
                paired.update(frozenset((left, right)) for left in cards for right in cards)
            if len(identities) > 1 and any(frozenset((left, right)) not in paired
                                           for left in identities for right in identities):
                add_issue('combine_pair_missing')
            for headers in rounds.values():
                blocks = headers[0]['block_plan']
                expected = {(0, 0)} if len(blocks) == 1 else {
                    (left, right) for left in range(len(blocks)) for right in range(left + 1, len(blocks))}
                actual = [tuple(header['combine_pair']) for header in headers]
                if any(header['block_plan'] != blocks for header in headers):
                    add_issue('combine_blocks_changed')
                if set(actual) != expected or len(actual) != len(expected):
                    add_issue('combine_pair_missing')
            if any(not entry['checked'] for entry in state['combinations']):
                add_issue('combination_unchecked')
    except (ResearchError, json.JSONDecodeError) as error:
        add_issue('series_invalid', reason=getattr(error, 'code', 'invalid_json'))
    return dict(complete=issue_count == 0, packets=packets, delivery=delivery,
                issues=dict(count=issue_count, examples=issues))


def find_failed_replacement(records, artifacts, value, adapter, series_key, slot_id, prior_id, output_tokens,
                      *, logical_packet=None):
    from .operations import normalized_text
    from .review_parts_roles import get_adapter
    from .workspace import strict_json

    replacement = None
    assignments = sorted(records.get('review_assignment', {}).values(),
                         key=lambda assignment: assignment['recorded_revision'])
    for failed in assignments:
        if (failed.get('context', {}).get('prior_assignment_id') != prior_id
                or failed.get('slot_id') != slot_id
                or any(failed.get(key) != value.get(key)
                       for key in ('role', 'route_id'))
                or any(normalized_text(failed[key]) != normalized_text(value[key])
                       for key in ('reviewer_id', 'author_id'))):
            continue
        packet = strict_json(artifacts.read(failed['packet']))
        header = packet.get(KEY, {})
        if find_kind(packet) != 'read' or header.get('adapter') != adapter:
            continue
        attempts = [attempt for attempt in records.get('review_attempt', {}).values()
                    if attempt['assignment_id'] == failed['id']]
        if not attempts or any(attempt.get('output') is not None for attempt in attempts):
            continue
        over_cap = any(((attempt.get('usage') or {}).get('output_tokens') or 0) >= output_tokens
                       or (attempt.get('error') or {}).get('code') == 'review_incomplete'
                       for attempt in attempts)
        if over_cap:
            if header.get('series_key') != series_key:
                replacement_key = get_adapter(adapter).get('replacement_key')
                if (prior_id is not None or logical_packet is None or replacement_key is None
                        or replacement_key(read_logical_packet(artifacts, header)) != replacement_key(logical_packet)):
                    continue
            replacement = dict(unit_characters=header['unit_characters'], over_cap=True)
    return replacement


def build_turn_prompt(role, base_parameters, header):
    from .review_protocol import canonical_prompt
    return canonical_prompt(role, **base_parameters,
        review_parts=dict(adapter=header['adapter'], kind=header['kind'],
                          answer=bool(header.get('request'))))


def validate_turn_output(records, artifacts, assignment, packet, output, *, attempt=None):
    """Validate this explicit invocation without asking for the series' readiness."""
    inventory = read_inventory(artifacts, packet[KEY]['inventory'], packet[KEY]['inventory_digest'])
    delta = validate_output(packet, output, inventory)
    if find_kind(packet) != 'combine':
        return delta
    from .review_protocol import canonical_prompt
    from .review_problem_parts import _measure
    from . import review_transport as transport

    logical = read_logical_packet(artifacts, packet[KEY])
    units = enumerate_units(logical, inventory['adapter'], inventory)
    base_parameters = assignment['prompt']['parameters']['base_prompt']['parameters']
    for combination in delta['combinations']:
        header = dict(packet[KEY], kind='check', check_combinations=[combination['id']])
        card_handles = _find_card_handles(packet[STATE_KEY]['cards'], set(combination['units']))
        state = dict(cards=[card for card in packet[STATE_KEY]['cards']
                            if card['unit'] in card_handles], combinations=[combination], findings=[])
        projected = project_packet(build_frame(logical, units), units, combination['read'], header, state)
        prompt = canonical_prompt(assignment['role'], **base_parameters,
                                  review_parts=dict(adapter=header['adapter'], kind='check'))
        route = records['review_route'][assignment['route_id']]
        request = transport.request_body(route, prompt['text'], projected, [], order=())
        _require(_measure(route, request) <= transport.local_input_limit(route),
                 'The combination originals do not fit one check turn')
    return delta


def prepare_packet(records, artifacts, value, route, packet, prompt, history, *,
                   base_prompt_parameters=None, slot_id=None, legacy_refusal=None):
    """Enter or continue parts with the same base prompt and adapter-owned series key."""
    from . import review_problem_parts as legacy, review_transport as transport
    from .review_parts_roles import find_adapter, get_adapter
    from .workspace import strict_json

    adapter = find_adapter(value, packet)
    if (adapter is None or route['adapter'] not in ('claude_cli_v1', 'codex_cli_v1', 'codex_cli_framed_v1')
            or transport.local_input_limit(route) is None):
        return packet
    prior_id = value.get('context', {}).get('prior_assignment_id')
    previous_assignment = records.get('review_assignment', {}).get(prior_id)
    previous = strict_json(artifacts.read(previous_assignment['packet'])) if previous_assignment else {}
    refreshed = any(review['assignment_id'] == prior_id for review in records.get('value_review', {}).values())
    series_key = get_adapter(adapter)['series_key'](packet)
    old_header = previous.get(KEY, {})
    continuing = (find_kind(previous) is not None and not refreshed
                  and old_header.get('adapter') == adapter and old_header.get('series_key') == series_key)
    continuing_legacy = legacy.is_continuing_series(records, previous, packet, prior_id)
    if legacy_refusal is not None:
        if (legacy_refusal.code != 'review_input_too_large' or adapter != 'problem_result'
                or not continuing_legacy):
            raise legacy_refusal
        continuing = False
    elif continuing_legacy:
        return packet
    previous_inventory = None
    if continuing:
        previous_inventory = read_inventory(artifacts, old_header['inventory'], old_header['inventory_digest'])
        try:
            inventory = build_inventory(packet, adapter, previous_inventory)
        except ResearchError as error:
            if error.code != 'review_parts_inventory_changed':
                raise
            continuing = False
    if not continuing:
        if legacy_refusal is None:
            ordinary = transport.request_body(route, prompt['text'], packet, history)
            if (legacy._measure(route, ordinary) <= transport.local_input_limit(route)
                    and legacy.preserves_ordinary_packet(artifacts, packet, ordinary)):
                return packet
        inventory = build_inventory(packet, adapter)
    observations = observe_series(records, artifacts, prior_id) if continuing else []
    series_id = old_header['series_assignment_id'] if continuing else value['id']
    inventory_reference = (old_header['inventory'] if continuing and inventory == previous_inventory
                           else store_inventory(artifacts, inventory))
    if base_prompt_parameters is None:
        parameters = prompt.get('parameters', {})
        base_prompt_parameters = dict(orchestration=parameters.get('orchestration'),
            repair=bool(parameters.get('context_repair')), excerpts='reviewer_excerpts' in parameters,
            common_version=parameters.get('common_prompt_version'))
    if value['role'] in ('bar', 'slate', 'result'):
        if review_excerpts.KEY not in packet:
            packet = dict(packet, **{review_excerpts.KEY:
                                    review_excerpts.carry_session(records, artifacts, {}, {})})
        base_prompt_parameters['excerpts'] = True
    packet = dict(packet)
    if adapter == 'problem_result' and not continuing and 'continuation' in packet:
        packet['continuation'] = dict(packet['continuation'])
        packet['continuation'].pop('scientific_context', None)
    packet.pop('digest', None)
    packet['digest'] = digest(packet)
    logical_reference = artifacts.put(render_json(packet).encode('utf-8'), 'application/json')
    replacement = find_failed_replacement(records, artifacts, value, adapter, series_key, slot_id,
                                    prior_id, route['configuration']['max_output_tokens'], logical_packet=packet)

    def measure(candidate):
        turn_prompt = build_turn_prompt(value['role'], base_prompt_parameters, candidate[KEY])
        if candidate.get('source_deliveries'):
            from .review_source_batches import plan_packet
            try:
                candidate = plan_packet(records, artifacts, route, candidate, turn_prompt, [])
            except ResearchError as error:
                measured = (error.details or {}).get('measurement', {}).get('rendered_characters')
                if error.code == 'review_input_too_large' and measured is not None:
                    # A non-stable source projection is infeasible even below its cap.
                    return max(measured, transport.local_input_limit(route) + 1)
                raise
        return legacy._measure(route, transport.request_body(route, turn_prompt['text'], candidate, [], order=()))

    # Search captures are provenance, unless a unit or a source delivery owns
    # their bodies. Reduce only prospective packets; frozen inventories and
    # request reconstruction retain the exact preparation recorded before.
    reductions = iter(())
    if adapter == 'problem_result':
        fit = transport._Fit(copy.deepcopy(packet), [], transport.FIT_ORDER)
        fit.protected.update(body['artifact']['sha256'] for unit in inventory['units'] for body in unit['bodies'])
        from .review_protocol import collect_references
        fit.protected.update(reference['sha256'] for reference in collect_references(packet.get('source_deliveries', [])))
        reductions = transport._reduce_search_responses(fit)
    while True:
        try:
            planned = plan_turn(packet, inventory, observations, series_id=series_id,
                input_limit=transport.local_input_limit(route), measure=measure,
                output_tokens=route['configuration']['max_output_tokens'], replacement=replacement,
                inventory_reference=inventory_reference, logical_reference=logical_reference, series_key=series_key)
            break
        except ResearchError as error:
            if error.code not in ('review_parts_frame_too_large', 'review_parts_final_too_large',
                                   'review_parts_inventory_too_large', 'review_parts_no_progress'):
                raise
            if next(reductions, False) is False:
                raise
            packet = copy.deepcopy(fit.supplied)
            packet.pop('digest', None)
            packet['digest'] = digest(packet)
            logical_reference = artifacts.put(render_json(packet).encode('utf-8'), 'application/json')
    for entry in planned.get('evidence', []):
        if entry.get('segment'):
            reference = artifacts.put(entry['content'].encode('utf-8'), entry['artifact']['media_type'])
            _require(reference == entry['artifact'], 'The stored segment differs from its descriptor',
                     'review_parts_delivery_invalid')
    return planned
